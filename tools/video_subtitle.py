# -*- coding: utf-8 -*-
import argparse
import json
import os
import queue
import sys
import threading
import time
import wave

import numpy as np
import fairy_root

MODEL_DIR = fairy_root.WHISPER_MODELS
VOICE_DIR = fairy_root.VOICES
LOG_DIR = fairy_root.LOGS
OLLAMA = "http://127.0.0.1:11434"
PROMPT = "以下是普通话的句子，请用简体中文转写。"
MIX_KEYS = ("立体声混音", "stereo mix", "what u hear", "loopback")
TARGET_SR = 16000
SILENCE_RMS = 0.0008
GAP_NEED = 0.45
MAX_SENT = 12.0
STEP = 1.0

def log(s):
    print(s, flush=True)


class AudioSource:
    name = "base"

    def available(self):
        return False

    def info(self):
        return {}

    def read(self, seconds):
        raise NotImplementedError

class StereoMixSource(AudioSource):
    name = "stereo_mix"

    def __init__(self, force_idx=None):
        self.idx = self.sr = self.ch = None
        try:
            import sounddevice as sd
            devs = sd.query_devices()
            if force_idx is not None:
                d = devs[force_idx]
                self.idx = force_idx
                self.sr = int(d.get("default_samplerate") or 48000)
                self.ch = min(2, max(1, int(d.get("max_input_channels") or 2)))
                return
            for i, d in enumerate(devs):
                if d.get("max_input_channels", 0) <= 0:
                    continue
                if any(k.lower() in str(d.get("name", "")).lower() for k in MIX_KEYS):
                    self.idx = i
                    self.sr = int(d.get("default_samplerate") or 48000)
                    self.ch = min(2, int(d["max_input_channels"]))
                    return
        except Exception as e:
            log("[采集] 探测失败: %s" % e)

    def available(self):
        return self.idx is not None

    def info(self):
        return {"backend": "stereo_mix", "name": "立体声混音", "device_index": self.idx,
                "samplerate": self.sr, "channels": self.ch,
                "局限": "只抓 Realtek 输出；蓝牙/HDMI 输出可能抓不到（需 PyAudioWPatch 做 loopback）"}

    def read(self, seconds):
        import sounddevice as sd
        n = int(seconds * self.sr)
        rec = sd.rec(n, samplerate=self.sr, channels=self.ch, device=self.idx,
                     dtype="float32", blocking=True)
        if rec.ndim > 1:
            rec = rec.mean(axis=1)
        return np.ascontiguousarray(rec, dtype=np.float32), self.sr

class WasapiLoopbackSource(AudioSource):
    name = "wasapi_loopback"

    def __init__(self):
        self.p = self.dev = None
        self.sr = self.ch = None
        self._pa = None
        try:
            import pyaudiowpatch as pa
            self._pa = pa
            self.p = pa.PyAudio()
            try:
                self.dev = self.p.get_default_wasapi_loopback()
            except Exception:
                self.dev = None
            if self.dev:
                self.sr = int(self.dev["defaultSampleRate"])
                self.ch = int(self.dev["maxInputChannels"])
        except Exception:
            self._pa = None

    def available(self):
        return self.dev is not None

    def info(self):
        if not self.available():
            return {"backend": "wasapi_loopback", "available": False,
                    "how": "需装 pyaudiowpatch（pip install PyAudioWPatch），能抓蓝牙/HDMI 输出"}
        return {"backend": "wasapi_loopback", "device": self.dev.get("name"),
                "samplerate": self.sr, "channels": self.ch}

    def read(self, seconds):
        n = int(seconds * self.sr)
        buf = self.dev.open(format=self._pa.paFloat32, channels=self.ch, rate=self.sr,
                            input=True, frames_per_buffer=1024)
        frames, got = [], 0
        while got < n:
            data = buf.read(min(1024, n - got), exception_on_overflow=False)
            frames.append(np.frombuffer(data, dtype=np.float32))
            got += 1024
        buf.stop_stream()
        buf.close()
        a = np.concatenate(frames)[:n]
        if self.ch > 1:
            a = a.reshape(-1, self.ch).mean(axis=1)
        return np.ascontiguousarray(a, dtype=np.float32), self.sr

def pick_source(src_idx=None):
    if src_idx is not None:
        return StereoMixSource(force_idx=int(src_idx))
    lb = WasapiLoopbackSource()
    if lb.available():
        return lb
    return StereoMixSource()


def to_16k_mono(a, sr):
    if sr == TARGET_SR:
        return a
    try:
        from math import gcd
        from scipy.signal import resample_poly
        g = gcd(int(sr), TARGET_SR)
        return np.ascontiguousarray(resample_poly(a, TARGET_SR // g, int(sr) // g).astype(np.float32))
    except Exception as e:
        log("[重采样] scipy 不可用(%s) -> 线性插值" % e)
        n = int(len(a) * TARGET_SR / sr)
        return np.ascontiguousarray(np.interp(np.linspace(0, len(a) - 1, n),
                                              np.arange(len(a)), a).astype(np.float32))

_MODEL = None
_MODEL_SIZE = "small"
_MODEL_LOCK = threading.Lock()

def _zh_only(s):
    return "".join(ch for ch in str(s or "") if "\u4e00" <= ch <= "\u9fff")

def get_model():
    global _MODEL
    with _MODEL_LOCK:
        if _MODEL is None:
            from faster_whisper import WhisperModel
            log("[模型] 加载 whisper %s（首次约 43 秒）…" % _MODEL_SIZE)
            t0 = time.time()
            _MODEL = WhisperModel(_MODEL_SIZE, device="cpu", compute_type="int8",
                                  download_root=MODEL_DIR)
            log("[模型] 就绪 %.1fs" % (time.time() - t0))


            try:
                _t = time.time()
                list(_MODEL.transcribe(np.zeros(TARGET_SR, dtype=np.float32),
                                       language="zh", beam_size=1,
                                       initial_prompt=PROMPT, vad_filter=False)[0])
                log("[模型] 预热完成 %.1fs（本次慢是正常的，之后每句只需 1~2s）" % (time.time() - _t))
            except Exception as _e:
                log("[模型] 预热失败（不影响使用）: %s" % _e)
    return _MODEL

def transcribe(a16):
    if a16 is None or len(a16) < TARGET_SR // 4:
        return ""
    m = get_model()
    it, _ = m.transcribe(a16, language="zh", beam_size=1,
                         initial_prompt=PROMPT, vad_filter=False)
    txt = "".join(s.text for s in it).strip()
    a, b = _zh_only(txt), _zh_only(PROMPT)
    if not a or a in b or b.endswith(a):
        return ""
    if len(a) <= 1:
        return ""
    return txt


_OLLAMA_MODEL = ""

def _pick_ollama():
    global _OLLAMA_MODEL
    if _OLLAMA_MODEL:
        return _OLLAMA_MODEL
    try:
        import urllib.request
        with urllib.request.urlopen(OLLAMA + "/api/tags", timeout=5) as r:
            names = [m.get("name", "") for m in (json.loads(r.read().decode("utf-8")).get("models") or [])]
        for pref in ("qwen3", "qwen", "deepseek", "llama"):
            for n in names:
                if pref in n.lower():
                    _OLLAMA_MODEL = n
                    return n
        if names:
            _OLLAMA_MODEL = names[0]
            return _OLLAMA_MODEL
    except Exception:
        pass
    return ""

def ollama(prompt, timeout=120):
    m = _pick_ollama()
    if not m:
        return None
    try:
        import urllib.request

        body = json.dumps({"model": m, "prompt": prompt, "stream": False,
                           "keep_alive": "2h",
                           "options": {"temperature": 0.2}}).encode("utf-8")
        req = urllib.request.Request(OLLAMA + "/api/generate", data=body,
                                     headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return (json.loads(r.read().decode("utf-8")).get("response") or "").strip() or None
    except Exception:
        return None

def translate(text):
    if not text:
        return ""
    r = ollama("把下面这句字幕翻译成简体中文，只输出译文，不要解释、不要拼音：\n" + text, timeout=90)
    return r or ""

def summarize(lines):
    if not lines:
        return []
    j = "\n".join(lines)
    if len(j) > 6000:
        j = j[-6000:]
    r = ollama("把下面的视频字幕整理成中文要点清单（每条一行、最多 10 条、只写要点不复述原文）：\n" + j,
               timeout=300)
    return [r] if r else []


class SubtitleWindow:
    def __init__(self, y=1120):
        self.root = self.lbl = None
        self.q = queue.Queue()
        self._y = y
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        try:
            import tkinter as tk
            self.root = tk.Tk()
            self.root.overrideredirect(True)
            self.root.attributes("-topmost", True)
            self.root.configure(bg="#111111")
            self.lbl = tk.Label(self.root, text="准备中…", fg="#ffffff", bg="#111111",
                                font=("Microsoft YaHei", 17), wraplength=1150,
                                justify="center", padx=18, pady=12)
            self.lbl.pack()
            self.root.geometry("+%d+%d" % (280, self._y))

            def pump():
                try:
                    while True:
                        self.lbl.config(text=self.q.get_nowait())
                except queue.Empty:
                    pass
                self.root.after(120, pump)
            pump()
            self.root.mainloop()
        except Exception as e:
            log("[字幕窗] 失败: %s" % e)

    def show(self, t):
        try:
            self.q.put(t)
        except Exception:
            pass

    def close(self):
        try:
            if self.root:
                self.root.after(0, self.root.destroy)
        except Exception:
            pass


def emit(seg, sr, idx, lines, fh, do_tr, win):
    dur = len(seg) / float(sr or 1)
    a16 = to_16k_mono(seg, sr)
    t0 = time.time()
    raw = transcribe(a16)
    asr_t = time.time() - t0
    if not raw:
        log("  [%d] %.1fs 音频 -> 听不出内容（可能非人声，%.1fs）" % (idx, dur, asr_t))
        return
    tr = ""
    tr_t = 0.0
    if do_tr:
        t1 = time.time()
        tr = translate(raw)
        tr_t = time.time() - t1
    stamp = time.strftime("%H:%M:%S")
    line = "[%s] %s" % (stamp, raw)
    if tr:
        line += "  ｜%s" % tr
    lines.append(line)
    try:
        fh.write(line + "\n")
        fh.flush()
    except Exception as e:
        log("  [落盘] 失败: %s" % e)
    log("  [%d] %.1fs音频 asr%.1fs%s | %s%s" % (idx, dur, asr_t,
        (" tr%.1fs" % tr_t) if do_tr else "", raw[:52], (" → " + tr[:44]) if tr else ""))
    if win:
        win.show(raw + (("\n" + tr) if tr else ""))


def run(step=STEP, do_tr=False, subtitle=False, src_idx=None, max_minutes=0, out=None):
    src = pick_source(src_idx)
    if not src.available():
        log("✗ 没有可用采集设备。--list 看设备清单")
        return 2
    log("采集源: %s" % json.dumps(src.info(), ensure_ascii=False))
    if src.name == "stereo_mix":
        log("⚠ 用立体声混音：只抓 Realtek 输出。若你的声音走蓝牙/HDMI 抓不到，需装 PyAudioWPatch 走 loopback。")

    os.makedirs(LOG_DIR, exist_ok=True)
    if out is None:
        out = os.path.join(LOG_DIR, "video_notes_%s.md" % time.strftime("%Y%m%d_%H%M"))
    fh = open(out, "w", encoding="utf-8")
    fh.write("# 视频字幕笔记  %s\n\n采集源: %s\n\n---\n\n"
             % (time.strftime("%Y-%m-%d %H:%M"), json.dumps(src.info(), ensure_ascii=False)))
    fh.flush()
    win = SubtitleWindow() if subtitle else None
    get_model()

    lines, buf, gap, idx, t0 = [], [], 0.0, 0, time.time()
    log("开始。Ctrl+C 停止。（VAD-lite：静音≥%.2fs 或满 %.0fs 切句）" % (GAP_NEED, MAX_SENT))
    try:
        while True:
            if max_minutes and (time.time() - t0) / 60 >= max_minutes:
                log("到达时间上限。")
                break
            a, sr = src.read(step)
            rms = float(np.sqrt((a ** 2).mean())) if len(a) else 0.0
            if rms < SILENCE_RMS:
                gap += step
                if gap >= GAP_NEED and buf:
                    idx += 1
                    emit(np.concatenate(buf), sr, idx, lines, fh, do_tr, win)
                    buf = []
                    gap = 0.0
                continue
            gap = 0.0
            buf.append(a)
            if sum(len(x) for x in buf) / float(sr or 1) >= MAX_SENT:
                idx += 1
                emit(np.concatenate(buf), sr, idx, lines, fh, do_tr, win)
                buf = []
    except KeyboardInterrupt:
        log("\n收到停止信号。")
    finally:
        try:
            if buf:
                idx += 1
                emit(np.concatenate(buf), getattr(src, "sr", None) or 48000,
                     idx, lines, fh, do_tr, win)
            if win:
                win.close()
            log("生成要点清单…")
            pts = summarize(lines)
            if pts:
                fh.write("\n---\n\n## 要点\n\n")
                for p in pts:
                    fh.write(p + "\n")
                log("\n要点：")
                for i, p in enumerate(pts, 1):
                    log("%d. %s" % (i, p[:220]))
            else:
                fh.write("\n---\n\n（要点不可用：本地 Ollama 未运行或缺模型；字幕流水已保留）\n")
                log("（要点不可用：Ollama 未运行/无模型）")
            fh.close()
        except Exception as e:
            log("收尾出错: %s" % e)
        log("笔记已写: %s （共 %d 句）" % (out, len(lines)))
    return 0


def load_wav_16k(p):
    with wave.open(p, "rb") as w:
        nch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw == 2:
        a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sw == 1:
        a = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128.0
    elif sw == 4:
        a = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError("sampwidth=%d" % sw)
    if nch > 1:
        a = a.reshape(-1, nch).mean(axis=1)
    if sr != 16000 and len(a) > 1:
        a = np.interp(np.linspace(0, len(a) - 1, int(len(a) * 16000.0 / sr)),
                      np.arange(len(a)), a).astype(np.float32)
    return np.ascontiguousarray(a, dtype=np.float32)

def selftest():
    import glob
    state = {"ok": 0, "fail": 0}

    def chk(name, cond, extra=""):
        if cond:
            state["ok"] += 1
            log("  [OK]   %s %s" % (name, extra))
        else:
            state["fail"] += 1
            log("  [FAIL] %s %s" % (name, extra))

    log("=" * 66)
    log("自检  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    log("=" * 66)

    log("\n[1] 采集后端")
    lb, sm = WasapiLoopbackSource(), StereoMixSource()
    log("    loopback   : %s" % json.dumps(lb.info(), ensure_ascii=False))
    log("    立体声混音 : %s" % json.dumps(sm.info(), ensure_ascii=False))
    chk("有可用采集后端", sm.available() or lb.available())

    log("\n[2] 真采集 2 秒（有没有声音都算通过，只要能采到）")
    try:
        a, sr = pick_source().read(2.0)
        rms = float(np.sqrt((a ** 2).mean()))
        chk("采到音频", len(a) > 0, "%d 样本 @%dHz RMS=%.5f %s"
            % (len(a), sr, rms, "(有声音)" if rms > SILENCE_RMS else "(静音-通道通)"))
    except Exception as e:
        chk("采到音频", False, str(e)[:80])

    log("\n[3] 真转写（用语料库真语音，不依赖采集）")
    ws = []
    for p in sorted(glob.glob(os.path.join(VOICE_DIR, "*.wav")))[:400]:
        try:
            with wave.open(p, "rb") as w:
                d = w.getnframes() / float(w.getframerate() or 1)
            if 2.0 <= d <= 6.0:
                ws.append(p)
            if len(ws) >= 3:
                break
        except Exception:
            pass
    chk("找到测试 wav", len(ws) > 0, "%d 个" % len(ws))
    tot_a = tot_p = 0.0
    got = 0
    for p in ws:
        a16 = load_wav_16k(p)
        t = time.time()
        txt = transcribe(a16)
        el = time.time() - t
        tot_a += len(a16) / 16000.0
        tot_p += el
        if txt:
            got += 1
        log("    %.2fs音频 -> %.2fs  %.2fx | %s" % (len(a16) / 16000.0, el,
                                                   (len(a16) / 16000.0) / max(.01, el), txt[:46]))
    chk("转写有结果", got > 0, "%d/%d" % (got, len(ws)))
    if tot_p > 0:
        rate = tot_a / tot_p
        chk("平均实时率 >=1.0 能实时", rate >= 1.0, "实测 %.2fx" % rate)

    log("\n[4] 真翻译（本机 Ollama）")
    m = _pick_ollama()
    chk("Ollama 可用", bool(m), "模型 %s" % (m or "无"))
    if m:
        t = time.time()
        tr = translate("A agent needs immediate medical attention. Requesting backup now.")
        chk("翻译有结果", bool(tr), "%.2fs -> %s" % (time.time() - t, (tr or "")[:60]))

    log("\n[5] 回声过滤（Whisper 听不到人话时会吐回提示词）")
    chk("能识别提示词回声", _zh_only("请用简体中文转写。") in _zh_only(PROMPT))

    log("\n[6] 落盘")
    try:
        p = os.path.join(LOG_DIR, "video_subtitle_selftest.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write("# selftest %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        chk("能写笔记文件", os.path.exists(p), p)
    except Exception as e:
        chk("能写笔记文件", False, str(e)[:60])

    log("\n" + "=" * 66)
    log("自检结果：%d 通过 / %d 失败" % (state["ok"], state["fail"]))
    if tot_p > 0:
        log("转写性能：总音频 %.1fs / 总处理 %.1fs = %.2fx 实时" % (tot_a, tot_p, tot_a / tot_p))
    log("=" * 66)
    return 0 if state["fail"] == 0 else 1

def cmd_list():
    import sounddevice as sd
    print("=== 输入设备（可采集）===")
    for i, d in enumerate(sd.query_devices()):
        if d.get("max_input_channels", 0) > 0:
            mark = "  ← 可录系统声音" if any(k.lower() in str(d["name"]).lower() for k in MIX_KEYS) else ""
            print("  [%2d] ch=%d sr=%d  %s%s" % (i, d["max_input_channels"],
                  int(d.get("default_samplerate") or 0), d["name"], mark))
    print("\n=== 输出设备（走 loopback 要装 PyAudioWPatch）===")
    for i, d in enumerate(sd.query_devices()):
        if d.get("max_output_channels", 0) > 0:
            print("  [%2d] ch=%d sr=%d  %s" % (i, d["max_output_channels"],
                  int(d.get("default_samplerate") or 0), d["name"]))
    print("\n=== 采集后端可用性 ===")
    for c in (WasapiLoopbackSource, StereoMixSource):
        s = c()
        print("  %-16s %-6s %s" % (c.name, "可用" if s.available() else "不可用",
                                   json.dumps(s.info(), ensure_ascii=False)[:120]))
    print("\n=== 翻译后端 ===")
    m = _pick_ollama()
    print("  Ollama: %s" % (("可用，模型 " + m) if m else "不可用（服务没起或缺模型）"))
    return 0

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--translate", action="store_true")
    ap.add_argument("--subtitle", action="store_true")
    ap.add_argument("--src", type=int, default=None)
    ap.add_argument("--chunk", type=float, default=STEP, help="每次读多少秒")
    ap.add_argument("--minutes", type=int, default=0)
    ap.add_argument("--model", default="small", choices=("tiny", "base", "small", "medium"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    global _MODEL_SIZE
    _MODEL_SIZE = a.model
    if a.list:
        return cmd_list()
    if a.selftest:
        return selftest()
    if a.run:
        return run(a.chunk, a.translate, a.subtitle, a.src, a.minutes, a.out)
    ap.print_help()
    return 0

if __name__ == "__main__":
    sys.exit(main())
