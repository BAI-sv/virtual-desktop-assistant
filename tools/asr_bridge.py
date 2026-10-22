# -*- coding: utf-8 -*-
"""
asr_bridge.py —— 本地语音识别统一接口（SenseVoice + Paraformer，自动降级）

【背景】FairyX 语音链路换用阿里系本地 ASR：
  - SenseVoice（sherpa-onnx 版）：快、准、免 torch —— 默认后端
  - Paraformer（FunASR 版）：工业级准确率，需 torch —— 备用/录音转写
  - 任意后端失败自动降级到另一个；都失败报错。

【第三方开源致谢】
  - SenseVoice / Paraformer / FunASR：阿里达摩院开源（FunAudioLLM）
      https://github.com/FunAudioLLM/SenseVoice
      https://github.com/alibaba-damo-academy/FunASR
  - sherpa-onnx（k2-fsa，Apache-2.0）：https://github.com/k2-fsa/sherpa-onnx

用法：
  py -3.12 asr_bridge.py --check                    检测所有后端可用性
  py -3.12 asr_bridge.py --file 音频.wav            识别文件（默认 sensevoice）
  py -3.12 asr_bridge.py --file 音频.wav --backend paraformer
  py -3.12 asr_bridge.py --mic 5                   录 5 秒麦克风识别
  py -3.12 asr_bridge.py --backend sensevoice --file x.wav --quiet
输出：{"ok": bool, "text": "...", "backend": "...", "cost_ms": 123}
"""
import argparse
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = r"D:\FAIRY\models\models"

# sherpa-onnx 版 SenseVoice（免 torch，默认）
SENSE_ONNX = os.path.join(
    MODELS, "xiaowangge--sherpa-onnx-sense-voice-small", "snapshots", "master")

# FunASR 版（独立环境，DirectML GPU 推理）
FUNASR_PY = r"D:\FAIRY\envs\funasr\Scripts\python.exe"
WORKER = os.path.join(HERE, "funasr_worker.py")
FUNASR_PARAFORMER = "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch"
FUNASR_SENSEVOICE = "iic/SenseVoiceSmall"
FUNASR_VAD = "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch"

BACKENDS = ("sensevoice", "paraformer", "sensevoice-funasr")


# ---------------------------------------------------------------- 音频读入
def _read_wav(path):
    import wave
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        data = w.readframes(w.getnframes())
    a = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return np.ascontiguousarray(a, dtype=np.float32), sr


def _rec_mic(seconds):
    import sounddevice as sd
    n = int(seconds * 16000)
    rec = sd.rec(n, samplerate=16000, channels=1, dtype="float32", blocking=True)
    if rec.ndim > 1:
        rec = rec.mean(axis=1)
    arr = np.ascontiguousarray(rec, dtype=np.float32)
    if (arr ** 2).mean() ** 0.5 < 1e-4:
        return None
    return arr


# ---------------------------------------------------------------- 后端
_SV_REC = None  # SenseVoice 识别器缓存（常驻进程只加载一次）


def _sv_onnx(text, samples, sr):
    """SenseVoice via sherpa-onnx（免 torch，识别器进程内复用）。"""
    global _SV_REC
    import sherpa_onnx
    if _SV_REC is None:
        _SV_REC = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=os.path.join(SENSE_ONNX, "model_q8.onnx"),
            tokens=os.path.join(SENSE_ONNX, "tokens.txt"),
            language="zh", use_itn=True, num_threads=4,
            decoding_method="greedy_search")
    s = _SV_REC.create_stream()
    s.accept_waveform(sr, samples)
    _SV_REC.decode_stream(s)
    return s.result.text.strip()


def warm():
    """预加载 SenseVoice（进程内）。用于常驻服务后台预热。"""
    global _SV_REC
    if _SV_REC is None:
        _ = _sv_onnx("", np.zeros(16000, dtype="float32"), 16000)


def ready():
    """SenseVoice 是否已加载完（不阻塞）。"""
    return _SV_REC is not None


def _sv_onnx_file(path):
    samples, sr = _read_wav(path)
    return _sv_onnx("", samples, sr)


def _funasr_gpu(samples, sr, kind):
    """调独立环境 GPU worker 识别（Paraformer / SenseVoice）。"""
    import subprocess
    import tempfile
    import wave as _wave
    pcm = np.clip(samples * 32767, -32768, 32767).astype(np.int16)
    tmp = os.path.join(tempfile.gettempdir(), "fairy_asr_tmp.wav")
    with _wave.open(tmp, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    r = subprocess.run(
        [FUNASR_PY, WORKER, "--file", tmp, "--kind", kind],
        capture_output=True, text=True, timeout=420,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        j = json.loads(r.stdout.strip().splitlines()[-1])
        return j.get("text") or ""
    except Exception:
        return ""


def _paraformer(samples, sr):
    return _funasr_gpu(samples, sr, "paraformer")


def _sv_funasr(samples, sr):
    return _funasr_gpu(samples, sr, "sensevoice")


# ---------------------------------------------------------------- 入口
def recognize(samples, sr, backend="sensevoice"):
    """按后端顺序尝试，自动降级。返回 (text, 实际后端)。
    ★ 2026-10-12【纯标点降级】SenseVoice 对低音量/噪声常返回句号等标点，
    视为"没识别到"，继续试下一个后端（Paraformer GPU 等）。"""
    order = [backend] + [b for b in BACKENDS if b != backend]
    for b in order:
        t0 = time.time()
        try:
            if b == "sensevoice":
                t = _sv_onnx("", samples, sr)
            elif b == "paraformer":
                t = _paraformer(samples, sr)
            elif b == "sensevoice-funasr":
                t = _sv_funasr(samples, sr)
            else:
                continue
            t = (t or "").strip()
            if t and t not in ("。", ".", "？", "！", "…", "……"):
                return t, b, int((time.time() - t0) * 1000)
        except Exception as e:
            print("[asr] 后端 %s 失败: %s: %s" % (b, type(e).__name__, e), file=sys.stderr)
    return "", "", 0


def check_all():
    out = {}
    out["sensevoice_onnx_ok"] = os.path.exists(os.path.join(SENSE_ONNX, "model_q8.onnx"))
    try:
        import sherpa_onnx  # noqa
        out["sherpa_onnx"] = True
    except Exception:
        out["sherpa_onnx"] = False
    try:
        import funasr  # noqa
        out["funasr"] = True
    except Exception:
        out["funasr"] = os.path.exists(FUNASR_PY)  # 独立环境存在即可用
    out["funasr_venv"] = os.path.exists(FUNASR_PY)
    out["paraformer_model"] = os.path.isdir(os.path.join(
        MODELS, "iic--speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch", "snapshots", "master"))
    out["sensevoice_pytorch_model"] = os.path.isdir(os.path.join(
        MODELS, "iic--SenseVoiceSmall", "snapshots", "master"))
    return out


def main():
    ap = argparse.ArgumentParser(description="本地语音识别统一接口（SenseVoice / Paraformer）")
    ap.add_argument("--check", action="store_true", help="检测后端可用性")
    ap.add_argument("--file", default="", help="识别音频文件（wav）")
    ap.add_argument("--mic", type=int, default=0, help="录 N 秒麦克风再识别")
    ap.add_argument("--backend", choices=BACKENDS, default="sensevoice")
    ap.add_argument("--quiet", action="store_true", help="只输出结果 JSON")
    a = ap.parse_args()

    if a.check:
        print(json.dumps({"ok": True, "backends": check_all()}, ensure_ascii=False))
        return

    if a.file:
        try:
            samples, sr = _read_wav(a.file)
        except Exception as e:
            print(json.dumps({"ok": False, "why": "读文件失败: %s" % e}, ensure_ascii=False))
            return
    elif a.mic:
        samples = _rec_mic(a.mic)
        sr = 16000
        if samples is None:
            print(json.dumps({"ok": False, "why": "silence"}, ensure_ascii=False))
            return
    else:
        ap.print_help()
        return

    text, used, cost = recognize(samples, sr, a.backend)
    if not a.quiet:
        print("[asr] 后端=%s 耗时=%dms" % (used, cost), file=sys.stderr)
    print(json.dumps({"ok": bool(text), "text": text, "backend": used, "cost_ms": cost},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
