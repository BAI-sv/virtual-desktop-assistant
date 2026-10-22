# -*- coding: utf-8 -*-
"""
asr_sensevoice.py —— 本地语音识别（阿里 SenseVoice，sherpa-onnx 引擎，免费开源）

中文识别接近手机端侧水平，CPU 实时，无需 torch / GPU。
模型来源：ModelScope xiaowangge/sherpa-onnx-sense-voice-small
          （由阿里官方 iic/SenseVoiceSmall 转换的 sherpa-onnx 格式）
引擎：sherpa-onnx（k2-fsa 开源，Apache-2.0）

用法：
  py -3.12 asr_sensevoice.py --file 音频.wav    识别音频文件
  py -3.12 asr_sensevoice.py --mic 5           录 5 秒麦克风再识别
  py -3.12 asr_sensevoice.py --check           检查模型可用
"""
import argparse
import json
import os
import sys

import numpy as np

MODEL_DIR = r"D:\FAIRY\models\models\xiaowangge--sherpa-onnx-sense-voice-small\snapshots\master"


def _recognizer():
    import sherpa_onnx
    return sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=os.path.join(MODEL_DIR, "model_q8.onnx"),
        tokens=os.path.join(MODEL_DIR, "tokens.txt"),
        language="zh",
        use_itn=True,
        num_threads=4,
        decoding_method="greedy_search",
    )


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


def recognize_file(path):
    """识别音频文件，返回识别文本。"""
    samples, sr = _read_wav(path)
    rec = _recognizer()
    s = rec.create_stream()
    s.accept_waveform(sr, samples)
    rec.decode_stream(s)
    return s.result.text.strip()


def recognize_mic(seconds=5):
    """录 seconds 秒麦克风再识别（16k 单声道）。"""
    import sounddevice as sd
    n = int(seconds * 16000)
    rec = sd.rec(n, samplerate=16000, channels=1, dtype="float32", blocking=True)
    if rec.ndim > 1:
        rec = rec.mean(axis=1)
    arr = np.ascontiguousarray(rec, dtype=np.float32)
    if (arr ** 2).mean() ** 0.5 < 1e-4:
        return ""
    rec_ = _recognizer()
    s = rec_.create_stream()
    s.accept_waveform(16000, arr)
    rec_.decode_stream(s)
    return s.result.text.strip()


def main():
    ap = argparse.ArgumentParser(description="SenseVoice 本地语音识别")
    ap.add_argument("--file", default="", help="音频文件（wav）")
    ap.add_argument("--mic", type=int, default=0, help="录 N 秒麦克风再识别")
    ap.add_argument("--check", action="store_true", help="检查模型可用性")
    a = ap.parse_args()

    if a.check:
        ok = os.path.exists(os.path.join(MODEL_DIR, "model_q8.onnx"))
        print(json.dumps({"ok": ok, "model_dir": MODEL_DIR}, ensure_ascii=False))
        return

    if a.file:
        try:
            t = recognize_file(a.file)
            print(json.dumps({"ok": True, "text": t, "file": a.file}, ensure_ascii=False))
        except Exception as e:
            print(json.dumps({"ok": False, "why": "%s: %s" % (type(e).__name__, e)}, ensure_ascii=False))
        return

    if a.mic:
        try:
            t = recognize_mic(a.mic)
            print(json.dumps({"ok": bool(t), "text": t}, ensure_ascii=False))
        except Exception as e:
            print(json.dumps({"ok": False, "why": "%s: %s" % (type(e).__name__, e)}, ensure_ascii=False))
        return

    ap.print_help()


if __name__ == "__main__":
    main()
