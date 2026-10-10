import os
import sys
import wave
import time
import numpy as np
import sounddevice as sd
import sherpa_onnx

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import fairy_root
MD = os.path.join(fairy_root.WAKEWORD_MODELS, "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01")
OUTDIR = os.path.join(fairy_root.WORK_LOGS, "mic_capture")
DEV = 1
SR = 16000
SECS = 240
BLK = 1600

os.makedirs(OUTDIR, exist_ok=True)
for f in os.listdir(OUTDIR):
    if f.endswith(".wav"):
        os.remove(os.path.join(OUTDIR, f))

print("=== 加载 KWS ===", flush=True)
sp = sherpa_onnx.KeywordSpotter(
    tokens=os.path.join(MD, "tokens.txt"),
    encoder=os.path.join(MD, "encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
    decoder=os.path.join(MD, "decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
    joiner=os.path.join(MD, "joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
    num_threads=2, max_active_paths=4,
    keywords_file=os.path.join(fairy_root.WAKEWORD_KW, "sw.txt"),
    keywords_score=1.0, keywords_threshold=0.10,
)
print("=== KWS 就绪，开始听（%d 秒）—— 喊 Fairy ===" % SECS, flush=True)

stream = sd.InputStream(samplerate=SR, channels=1, device=DEV, dtype="float32", blocksize=BLK)
stream.start()

kst = sp.create_stream()
t0 = time.time()
peak_all = 0.0
hits = 0
saved = 0
state = "idle"
clip = []
silent_run = 0
last_log = 0

while (time.time() - t0) < SECS:
    data, _ = stream.read(BLK)
    a = data.flatten()
    pk = float(np.max(np.abs(a)))
    if pk > peak_all:
        peak_all = pk


    kst.accept_waveform(SR, a)
    while sp.is_ready(kst):
        sp.decode_stream(kst)
    r = sp.get_result(kst)
    if r and r.strip():
        hits += 1
        el = time.time() - t0
        print("  [%6.1fs] *** KWS HIT #%d : %r ***" % (el, hits, r.strip()), flush=True)
        kst = sp.create_stream()


    rms = float(np.sqrt(np.mean(a * a)))
    if state == "idle":
        if rms > 0.002:
            state = "rec"
            clip = list(a)
            silent_run = 0
    else:
        clip.extend(list(a))
        if rms > 0.002:
            silent_run = 0
        else:
            silent_run += 1
            if silent_run >= 8:
                ca = np.asarray(clip, dtype=np.float32)
                dur = len(ca) / SR
                if dur >= 0.3:
                    saved += 1
                    p = os.path.join(OUTDIR, "clip_%02d_%.1fs_%.3f.wav" % (saved, dur, float(np.max(np.abs(ca)))))
                    with wave.open(p, "wb") as w:
                        w.setnchannels(1)
                        w.setsampwidth(2)
                        w.setframerate(SR)
                        w.writeframes((np.clip(ca, -1, 1) * 32767).astype(np.int16).tobytes())
                    print("  [%6.1fs] clip_%02d saved dur=%.1fs peak=%.4f" % (time.time() - t0, saved, dur, float(np.max(np.abs(ca)))), flush=True)
                state = "idle"
                clip = []

    el = int(time.time() - t0)
    if el > 0 and el % 30 == 0 and el != last_log:
        last_log = el
        print("  [%6.1fs] hits=%d clips=%d peak=%.4f" % (el, hits, saved, peak_all), flush=True)

stream.stop()
stream.close()
print("DONE hits=%d clips=%d peak=%.4f" % (hits, saved, peak_all), flush=True)
