# -*- coding: utf-8 -*-
import json
import os
import shutil
import sys
import time
import wave
import fairy_root

INDEX = fairy_root.VOICE_INDEX
VOICES = fairy_root.VOICES
BACKUP = os.path.join(fairy_root.VOICE, "voice_index.backup.json")
MODEL_DIR = fairy_root.WHISPER_MODELS


PROMPT = "以下是普通话的句子，请用简体中文转写。"

def log(s):
    print(s, flush=True)

def wav_duration(p):
    try:
        with wave.open(p, "rb") as w:
            return round(w.getnframes() / float(w.getframerate() or 1), 2)
    except Exception:
        return 0.0

def load_audio_16k(path):
    import numpy as np
    with wave.open(path, "rb") as w:
        nch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw == 2:
        a = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sw == 1:
        a = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sw == 4:
        a = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError("unsupported sampwidth %d" % sw)
    if nch > 1:
        a = a.reshape(-1, nch).mean(axis=1)
    if sr != 16000 and len(a) > 1:
        idx = np.linspace(0, len(a) - 1, int(len(a) * 16000.0 / sr))
        a = np.interp(idx, np.arange(len(a)), a).astype(np.float32)
    return np.ascontiguousarray(a, dtype=np.float32)

def norm(s):
    PUNCT = set("…。，！？、；：“”‘’《》【】()[]{}<>!?,.;:\"'　 \t\n——%·")
    return "".join(ch for ch in str(s or "") if ch not in PUNCT).strip()

def main():
    dry = "--dry" in sys.argv
    if not os.path.exists(INDEX):
        log("找不到索引: %s" % INDEX)
        return 1
    idx = json.load(open(INDEX, encoding="utf-8"))
    lines = idx.get("lines") or []
    indexed = {(e.get("w") or "").lower() for e in lines}
    log("原索引: %d 条" % len(lines))

    on_disk = [f for f in sorted(os.listdir(VOICES))
               if f.lower().endswith((".wav", ".mp3", ".ogg", ".flac", ".m4a"))]
    todo = [f for f in on_disk if f.lower() not in indexed]
    log("目录音频: %d 个 | 待补: %d 个" % (len(on_disk), len(todo)))
    if not todo:
        log("没有要补的，结束。")
        return 0
    if dry:
        log("--dry：只报告，不写。")
        return 0


    if not os.path.exists(BACKUP):
        shutil.copy2(INDEX, BACKUP)
        log("已备份原索引 -> %s" % BACKUP)
    else:
        log("备份已存在，跳过备份（保留最早那份）")

    log("加载 Whisper(small)…")
    t0 = time.time()
    from faster_whisper import WhisperModel
    try:
        model = WhisperModel("small", device="cpu", compute_type="int8",
                             download_root=MODEL_DIR)
    except Exception as e:
        log("加载失败: %s" % e)
        return 2
    log("  模型就绪 %.1fs" % (time.time() - t0))

    added = 0
    failed = 0
    t0 = time.time()
    segs = None
    for i, f in enumerate(todo, 1):
        p = os.path.join(VOICES, f)
        try:
            audio = load_audio_16k(p)
            it, _info = model.transcribe(audio, language="zh", beam_size=1,
                                         initial_prompt=PROMPT, vad_filter=False)
            text = "".join(s.text for s in it).strip()
        except Exception as e:
            text = ""
            failed += 1
            if failed <= 3:
                log("  转写失败 %s: %s" % (f, str(e)[:80]))
        if not text:
            continue
        key = norm(text)
        if not key:
            continue
        lines.append({"t": key, "r": text, "w": f, "y": "extra",
                      "d": wav_duration(p)})
        added += 1
        if i % 25 == 0 or i == len(todo):
            el = time.time() - t0
            log("  进度 %d/%d  已补 %d  耗时 %.0fs  预计剩余 %.0fs"
                % (i, len(todo), added, el, el / i * (len(todo) - i)))

    idx["lines"] = lines
    idx["count"] = len(lines)
    tmp = INDEX + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False)
    os.replace(tmp, INDEX)
    log("完成：新增 %d 条（失败 %d）→ 索引共 %d 条" % (added, failed, len(lines)))
    log("索引已写回 %s" % INDEX)
    return 0

if __name__ == "__main__":
    sys.exit(main())
