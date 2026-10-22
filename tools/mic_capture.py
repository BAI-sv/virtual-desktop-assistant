import sounddevice as sd
import numpy as np
import wave
import os
import time

DEV = 1
SR = 16000
SECS = 300
import fairy_root
OUTDIR = os.path.join(fairy_root.WORK_LOGS, "mic_capture")

os.makedirs(OUTDIR, exist_ok=True)
for f in os.listdir(OUTDIR):
    if f.endswith(".wav"):
        os.remove(os.path.join(OUTDIR, f))

print("*** 5 MIN CAPTURE -- say Fairy anytime ***", flush=True)
print("    saving every loud burst to %s" % OUTDIR, flush=True)

block = int(SR * 0.1)
n = int(SR * SECS)
saved = 0
loud_total = 0
peak_all = 0.0

stream = sd.InputStream(samplerate=SR, channels=1, device=DEV, dtype="float32", blocksize=block)
stream.start()

buf = []
state = "idle"
clip = []
silent_run = 0
t0 = time.time()

while (time.time() - t0) < SECS:
    data, over = stream.read(block)
    a = data.flatten()
    r = float(np.sqrt(np.mean(a * a)))
    peak = float(np.max(np.abs(a)))
    if peak > peak_all:
        peak_all = peak
    if r > 0.002:
        loud_total += 1
    if state == "idle":
        if r > 0.002:
            state = "rec"
            clip = [buf[-3][i] for i in range(0)] if False else []
            clip = list(a)
            silent_run = 0
    else:
        clip.extend(list(a))
        if r > 0.002:
            silent_run = 0
        else:
            silent_run += 1
            if silent_run >= 8:
                idx = saved + 1
                dur = len(clip) / SR
                if dur >= 0.3:
                    path = os.path.join(OUTDIR, "clip_%02d_%.1fs.wav" % (idx, dur))
                    ca = np.asarray(clip, dtype=np.float32)
                    with wave.open(path, "wb") as w:
                        w.setnchannels(1)
                        w.setsampwidth(2)
                        w.setframerate(SR)
                        w.writeframes((np.clip(ca, -1, 1) * 32767).astype(np.int16).tobytes())
                    print("  [%5.1fs] saved clip_%02d  dur=%.1fs  peak=%.4f" % (time.time() - t0, idx, dur, float(np.max(np.abs(ca)))), flush=True)
                    saved += 1
                state = "idle"
                clip = []
    buf.append(a)
    if len(buf) > 10:
        buf.pop(0)
    el = time.time() - t0
    if int(el) % 30 == 0 and abs(el - round(el)) < 0.11 and int(el) > 0 and int(el) != getattr(stream, "_lastlog", -1):
        stream._lastlog = int(el)
        print("  [%5.1fs] loud blocks so far = %d  peak = %.4f" % (el, loud_total, peak_all), flush=True)

stream.stop()
stream.close()
print("DONE  loud_blocks=%d  peak=%.4f  clips_saved=%d" % (loud_total, peak_all, saved), flush=True)
print("dir: %s" % OUTDIR, flush=True)
