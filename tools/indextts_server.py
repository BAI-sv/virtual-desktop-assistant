# -*- coding: utf-8 -*-
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import fairy_root


CREATE_NO_WINDOW = fairy_root.CREATE_NO_WINDOW

PORT = int(os.environ.get("INDEXTTS_PORT", "9881"))
ROOT = os.environ.get("INDEXTTS_ROOT") or fairy_root.INDEXTTS_ROOT

def log(s):
    print("[IndexTTS %s] %s" % (time.strftime("%H:%M:%S"), s), flush=True)

def setup_rocm_env():
    scripts = os.path.dirname(sys.executable)
    sdk = os.path.join(scripts, "rocm-sdk.exe")
    if not os.path.exists(sdk):
        log("未发现 rocm-sdk.exe（这套环境可能不是 ROCm 构建）")
        return None
    try:


        subprocess.run([sdk, "init"], capture_output=True, timeout=300,
                       creationflags=CREATE_NO_WINDOW)
        r = subprocess.run([sdk, "path", "--root"], capture_output=True, text=True,
                           encoding="mbcs", errors="replace", timeout=300,
                           creationflags=CREATE_NO_WINDOW)
        root = ""
        if r.stdout:
            lines = [x.strip() for x in r.stdout.strip().splitlines() if x.strip()]
            root = lines[-1] if lines else ""
        if root and os.path.isdir(root):
            os.environ["ROCM_PATH"] = root
            os.environ["HIP_PATH"] = root
            os.environ["PATH"] = os.pathsep.join(
                [scripts, os.path.join(os.path.dirname(scripts), "Library", "bin"),
                 os.environ.get("PATH", "")])


            devel = os.path.join(os.path.dirname(scripts), "Lib", "site-packages", "_rocm_sdk_devel")
            if os.path.isdir(devel):
                binp = os.path.join(devel, "bin")
                os.environ["MIOPEN_SYSTEM_DB_PATH"] = binp
                os.environ["ROCBLAS_TENSILE_DB_PATH"] = os.path.join(binp, "rocblas")
                os.environ["ROCBLAS_TENSILE_LIBPATH"] = os.path.join(binp, "rocblas", "library")

            if os.environ.get("VDA_DEBUG_HIP"):
                os.environ["HIP_LAUNCH_BLOCKING"] = "1"
                os.environ["AMD_SERIALIZE_KERNEL"] = "3"
            for k, v in (("MIOPEN_FIND_ENFORCE", "1"), ("MIOPEN_FIND_MODE", "2"),
                         ("MIOPEN_DEBUG_DISABLE_FIND_DB", "0"), ("MIOPEN_SEARCH_CUTOFF", "1"),
                         ("MIOPEN_ENABLE_LOGGING", "0"), ("MIOPEN_LOG_LEVEL", "0"),
                         ("TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL", "1"),
                         ("FLASH_ATTENTION_TRITON_AMD_ENABLE", "TRUE")):
                os.environ.setdefault(k, v)
            app = os.path.dirname(os.path.abspath(__file__))
            for k, sub in (("PYTORCH_TUNABLEOP_CACHE_DIR", "tunableop-cache"),
                           ("TRITON_CACHE_DIR", "triton-cache")):
                d = os.path.join(app, sub)
                try:
                    os.makedirs(d, exist_ok=True)
                    os.environ[k] = d
                except Exception:
                    pass
            log("MIOPEN_SYSTEM_DB_PATH = %s" % os.environ.get("MIOPEN_SYSTEM_DB_PATH", "(未设)"))
            log("ROCm SDK 路径: %s" % root)
            return root
        log("rocm-sdk 没给出有效路径（stdout=%r）" % (r.stdout or "")[:80])
    except Exception as e:
        log("配置 ROCm 环境失败: %s" % e)
    return None

def patch_sdpa_backends():
    import torch
    for name, val in (("enable_flash_sdp", False),
                      ("enable_mem_efficient_sdp", False),
                      ("enable_math_sdp", True)):
        fn = getattr(torch.backends.cuda, name, None)
        if fn is None:
            continue
        try:
            fn(val)
        except Exception as e:
            log("  %s(%s) 设置失败: %s" % (name, val, e))
    try:
        log("SDPA 后端: flash=%s memeff=%s math=%s"
            % (torch.backends.cuda.flash_sdp_enabled(),
               torch.backends.cuda.mem_efficient_sdp_enabled(),
               torch.backends.cuda.math_sdp_enabled()))
    except Exception:
        pass

def patch_miopen_batchnorm():
    try:
        import torch
        from torch.nn.modules.batchnorm import _BatchNorm
        if getattr(_BatchNorm, "_vda_patched", False):
            return
        orig = _BatchNorm.forward

        def forward(self, x):
            if self.training or self.running_mean is None:
                return orig(self, x)
            shape = [1, -1] + [1] * (x.dim() - 2)
            mean = self.running_mean.view(shape)
            var = self.running_var.view(shape)
            w = self.weight.view(shape) if self.weight is not None else 1.0
            b = self.bias.view(shape) if self.bias is not None else 0.0
            return (x - mean) / torch.sqrt(var + self.eps) * w + b

        _BatchNorm.forward = forward
        _BatchNorm._vda_patched = True
        log("已打 BatchNorm 补丁（逐元素实现，绕开 gfx1201 缺失的 MIOpen 内核）")
    except Exception as e:
        log("BatchNorm 补丁失败（不致命）: %s" % e)

def patch_torch():
    import torch
    if not hasattr(torch, "get_default_device"):
        torch.get_default_device = lambda: torch.device("cpu")
    if not hasattr(torch, "set_default_device"):
        torch.set_default_device = lambda device: None
    return torch

class Engine:
    def __init__(self):
        self.tts = None
        self.device = "?"
        self.api = "?"
        self.lock = threading.Lock()

    def load(self):
        setup_rocm_env()
        os.chdir(ROOT)
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

        for k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
            os.environ.pop(k, None)
        torch = patch_torch()
        patch_miopen_batchnorm()
        patch_sdpa_backends()

        mod = None
        for name in ("indextts.infer_v2_5", "indextts.infer_v2"):
            try:
                mod = __import__(name, fromlist=["IndexTTS2"])
                self.api = name.split(".")[-1]
                break
            except Exception as e:
                log("%s 不可用：%s" % (name, str(e)[:120]))
        if mod is None:
            raise RuntimeError("找不到可用的 IndexTTS 推理模块")

        want = os.environ.get("INDEXTTS_DEVICE", "")
        if not want:
            want = "cuda:0" if torch.cuda.is_available() else "cpu"
        self.is_v25 = self.api.endswith("v2_5")
        t0 = time.time()
        log("加载 IndexTTS（api=%s，device=%s，模型目录=%s）…" % (self.api, want, ROOT))
        kw = dict(cfg_path=os.path.join(ROOT, "checkpoints", "config.yaml"),
                  model_dir=os.path.join(ROOT, "checkpoints"), device=want)
        if self.is_v25:
            kw.update(use_bf16=want.startswith("cuda"), use_qwen_emo=False)
        else:
            kw.update(use_fp16=want.startswith("cuda"))


        try:
            import inspect
            allowed = set(inspect.signature(mod.IndexTTS2.__init__).parameters)
            dropped = sorted(k for k in kw if k not in allowed)
            kw = {k: v for k, v in kw.items() if k in allowed}
            if dropped:
                log("构造参数按签名过滤掉：%s" % ", ".join(dropped))
        except Exception as e:
            log("签名过滤失败（继续用原始参数）: %s" % e)
        self.tts = mod.IndexTTS2(**kw)
        self.device = getattr(self.tts, "device", want)
        log("加载完成，耗时 %.0f 秒（实际 device=%s）" % (time.time() - t0, self.device))


    GEN_KEYS = ("do_sample", "top_p", "top_k", "temperature", "length_penalty",
                "num_beams", "repetition_penalty", "max_mel_tokens",
                "max_text_tokens_per_segment")


    GEN_TYPES = {"do_sample": bool, "top_p": float, "top_k": int,
                 "temperature": float, "length_penalty": float, "num_beams": int,
                 "repetition_penalty": float, "max_mel_tokens": int,
                 "max_text_tokens_per_segment": int}
    EMO_KEYS = ("emo_audio_prompt", "emo_alpha", "emo_vector", "use_emo_text",
                "emo_text", "use_random", "interval_silence", "duration_factor")

    @classmethod
    def _coerce(cls, k, v):
        t = cls.GEN_TYPES.get(k)
        if t is None or v is None:
            return v
        try:
            if t is bool:
                return bool(v)
            if t is int:
                return int(round(float(v)))
            return float(v)
        except (TypeError, ValueError):
            return v

    def synth(self, text, ref_audio, lang="zh", emo_alpha=1.0, extra=None):
        if not self.tts:
            raise RuntimeError("模型尚未加载完成")
        if not os.path.exists(ref_audio):
            raise RuntimeError("参考音频不存在: %s" % ref_audio)
        fd, out = tempfile.mkstemp(suffix=".wav", prefix="indextts_")
        os.close(fd)
        try:
            kw = dict(spk_audio_prompt=ref_audio, text=text, output_path=out, verbose=False)
            if self.is_v25:
                kw["lang"] = lang
                kw["emo_alpha"] = emo_alpha
            else:
                kw["emo_alpha"] = emo_alpha
            dropped = []
            for k, v in (extra or {}).items():
                if v is None:
                    continue
                if k in self.GEN_KEYS or k in self.EMO_KEYS:
                    kw[k] = self._coerce(k, v)
                else:
                    dropped.append(k)
            if dropped:
                log("忽略不支持的参数：%s" % ", ".join(sorted(dropped)))


            if MAX_BEAMS and isinstance(kw.get("num_beams"), int) and kw["num_beams"] > MAX_BEAMS:
                log("num_beams %d -> %d（INDEX_MAX_BEAMS 限制）" % (kw["num_beams"], MAX_BEAMS))
                kw["num_beams"] = MAX_BEAMS
            with self.lock:
                t0 = time.time()
                self.tts.infer(**kw)
                log("合成 %.1fs（num_beams=%s, max_mel=%s）：「%s」"
                    % (time.time() - t0, kw.get("num_beams", "默认"),
                       kw.get("max_mel_tokens", "默认"), text[:20]))
            with open(out, "rb") as f:
                return f.read()
        finally:
            try:
                os.remove(out)
            except OSError:
                pass

ENGINE = Engine()


FAIRY_DIR = fairy_root.ROOT
VOICE_DIR = os.path.join(FAIRY_DIR, "voice", "index_voices")
CORPUS_DIR = os.path.join(FAIRY_DIR, "voice", "voices")
PROFILE_DIR = os.path.join(FAIRY_DIR, "voice", "profiles")
AUDIO_EXT = (".wav", ".mp3", ".flac", ".m4a", ".ogg")


MAX_BEAMS = int(os.environ.get("INDEX_MAX_BEAMS", "0") or 0)

def _list_voices():
    try:
        return sorted(f for f in os.listdir(VOICE_DIR) if f.lower().endswith(AUDIO_EXT))
    except OSError:
        return []

def _pick_fairy_ref():
    import wave
    best, bestd = "", 9e9
    try:
        names = sorted(f for f in os.listdir(CORPUS_DIR) if f.lower().endswith(".wav"))
    except OSError:
        return ""
    for n in names[:400]:
        p = os.path.join(CORPUS_DIR, n)
        try:
            with wave.open(p, "rb") as w:
                d = w.getnframes() / float(w.getframerate())
        except Exception:
            continue
        if 3.0 <= d <= 9.0 and abs(d - 6.0) < bestd:
            best, bestd = p, abs(d - 6.0)
    return best

def _seed_voice_dir():
    os.makedirs(VOICE_DIR, exist_ok=True)
    if _list_voices():
        return
    import shutil
    seeds = []
    c = os.path.join(PROFILE_DIR, "custom.wav")
    if os.path.exists(c):
        seeds.append(("custom.wav", c))
    f = _pick_fairy_ref()
    if f:
        seeds.append(("fairy.wav", f))
    for name, src in seeds:
        try:
            shutil.copy2(src, os.path.join(VOICE_DIR, name))
            log("参考音色已就位：%s（来自 %s）" % (name, os.path.basename(src)))
        except Exception as e:
            log("放参考音色 %s 失败: %s" % (name, e))

def _resolve_prompt(name):
    if not name:
        return ""
    name = str(name)
    if os.path.isabs(name) and os.path.exists(name):
        return name
    base = os.path.basename(name)
    for d in (VOICE_DIR, CORPUS_DIR, PROFILE_DIR):
        p = os.path.join(d, base)
        if os.path.exists(p):
            return p
    vs = _list_voices()
    return os.path.join(VOICE_DIR, vs[0]) if vs else ""

def _extract_multipart_file(body, ctype):
    import re as _re
    m = _re.search(r'boundary=([^;]+)', ctype or "")
    if not m:
        return None, None
    boundary = ("--" + m.group(1).strip().strip('"')).encode()
    for part in body.split(boundary):
        if b'name="file"' not in part:
            continue
        fm = _re.search(rb'filename="([^"]*)"', part)
        fname = fm.group(1).decode("utf-8", "replace") if fm else "upload.wav"
        i = part.find(b"\r\n\r\n")
        if i < 0:
            continue
        data = part[i + 4:]
        data = data.rstrip(b"\r\n")
        if data.endswith(b"--"):
            data = data[:-2].rstrip(b"\r\n")
        return fname, data
    return None, None

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

    def do_GET(self):
        p = self.path.split("?")[0]
        if p.startswith("/health"):
            self._send(200, json.dumps({"ok": True, "device": ENGINE.device,
                                        "api": ENGINE.api, "root": ROOT,
                                        "loaded": ENGINE.tts is not None,
                                        "voices": len(_list_voices())}).encode())
        elif p.startswith("/api/v1/voices"):
            vs = _list_voices()

            self._send(200, json.dumps({"voices": vs, "count": len(vs)}).encode())
        else:
            self._send(404, b'{"error":"not found"}')

    def _handle_upload(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n)
        fname, data = _extract_multipart_file(body, self.headers.get("Content-Type"))
        if not data:
            self._send(400, json.dumps({"detail": "没解析到 file 字段"}).encode())
            return
        fname = os.path.basename(fname or "upload.wav")
        if not fname.lower().endswith(AUDIO_EXT):
            fname += ".wav"
        os.makedirs(VOICE_DIR, exist_ok=True)
        with open(os.path.join(VOICE_DIR, fname), "wb") as f:
            f.write(data)
        log("收到参考音频上传：%s（%.1f KB）" % (fname, len(data) / 1024.0))
        vs = _list_voices()
        self._send(200, json.dumps({"voices": vs, "count": len(vs),
                                    "name": fname}).encode())

    def do_POST(self):
        p = self.path.split("?")[0]
        try:
            if p.startswith("/api/v1/upload"):
                self._handle_upload()
                return
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n) or b"{}")

            if p.startswith("/api/v1/tts/tasks"):

                text = (req.get("text") or "").strip()
                if not text:
                    self._send(400, b'{"detail":"text is required"}')
                    return
                ref = _resolve_prompt(req.get("prompt_audio") or "")
                if not ref:
                    self._send(400, b'{"detail":"\xe6\x89\xbe\xe4\xb8\x8d\xe5\x88\xb0\xe5\x8f\x82\xe8\x80\x83\xe9\x9f\xb3\xe9\xa2\x91"}')
                    return
                extra = {}
                for k in ENGINE.GEN_KEYS:
                    if req.get(k) is not None:
                        extra[k] = req[k]
                if req.get("emo_random") is not None:
                    extra["use_random"] = bool(req["emo_random"])
                if req.get("emo_ref_path"):
                    er = _resolve_prompt(req["emo_ref_path"])
                    if er:
                        extra["emo_audio_prompt"] = er
                if (req.get("emo_text") or "").strip():
                    extra["emo_text"] = str(req["emo_text"]).strip()
                    extra["use_emo_text"] = True
                wav = ENGINE.synth(text, ref, "zh",
                                   float(req.get("emo_weight") or 1.0), extra)
                self._send(200, wav, "audio/wav")
                return

            if p.startswith("/tts"):

                text = (req.get("text") or "").strip()
                if not text:
                    self._send(400, b'{"error":"text is required"}')
                    return
                wav = ENGINE.synth(text, req.get("ref_audio") or "",
                                   req.get("lang") or "zh", req.get("emo_alpha", 1.0))
                self._send(200, wav, "audio/wav")
                return

            self._send(404, b'{"error":"not found"}')
        except Exception as e:
            log("合成失败: %s\n%s" % (e, traceback.format_exc()[-700:]))
            self._send(500, json.dumps({"error": str(e)}).encode())

def prewarm():
    try:
        vs = _list_voices()
        if not vs:
            log("没参考音色，跳过预热")
            return
        ref = os.path.join(VOICE_DIR, vs[0])
        t0 = time.time()
        ENGINE.synth("预热。", ref, "zh", 1.0, {"max_mel_tokens": 120, "num_beams": 1})
        log("预热完成，耗时 %.0f 秒（之后每次合成都是热态）" % (time.time() - t0))
    except Exception as e:
        log("预热失败（不影响使用）: %s" % e)

def main():
    ENGINE.load()
    _seed_voice_dir()


    threading.Thread(target=prewarm, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    log("服务就绪: http://127.0.0.1:%d  (api=%s device=%s)" % (PORT, ENGINE.api, ENGINE.device))
    log("  参考音色目录: %s（%d 个）" % (VOICE_DIR, len(_list_voices())))
    log("  兼容 dsh-plugin-tts：GET /api/v1/voices  POST /api/v1/tts/tasks  POST /api/v1/upload")
    srv.serve_forever()

if __name__ == "__main__":
    main()
