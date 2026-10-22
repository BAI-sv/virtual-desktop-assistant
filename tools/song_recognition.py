# -*- coding: utf-8 -*-
"""
song_recognition.py —— 听歌识曲（Shazam 协议，免费开源）

声音指纹识别：抓取系统正在播放的声音（立体声混音）-> Shazam 识别服务 -> 歌名/歌手。
和 SongRec 用的是同一个识别服务（Shazam），但跑在 Windows 且能直接给 Fairy 调用。

【第三方依赖（开源免费）】
- shazamio       —— Shazam 逆向 API 的 Python 封装（GitHub dosiu/shazamio，MIT 许可）
- sounddevice    —— PortAudio 封装（立体声混音采集）

用法：
  py -3.12 song_recognition.py --list                 列出采集设备
  py -3.12 song_recognition.py --once                 录 8 秒识别一次（JSON 输出）
  py -3.12 song_recognition.py --once --device 1      指定设备索引
  py -3.12 song_recognition.py --once --seconds 10    录 10 秒
  py -3.12 song_recognition.py --watch                循环识别（歌曲变化才输出）
"""
import argparse
import io
import json
import os
import sys
import time
import wave

MIX_KEYS = ("立体声混音", "stereo mix", "what u hear", "loopback")
SR = 16000
CH = 1
HERE = os.path.dirname(os.path.abspath(__file__))


def fix_mojibake(s):
    """★ 2026-10-12【乱码自愈】Shazam 接口/库偶尔把 UTF-8 字节按 latin-1/GBK
    错误解码，导致中文歌名/歌手变乱码。做多轮还原：
    ① UTF-8 字节被 latin-1/cp1252 解码（ææ´ 型）
    ② UTF-8 字节被 GBK 解码（锟斤拷 型）
    ③ GBK 字节被 utf-8 解码（鐢樺 型）
    还原后含中文才采用，否则保持原文。"""
    if not s or not isinstance(s, str):
        return s
    if not any(ord(c) > 127 for c in s):
        return s
    _CJK = lambda t: sum(1 for c in t if "\u4e00" <= c <= "\u9fff") >= 2
    # ① latin-1/cp1252 -> utf-8/gbk
    for enc in ("latin-1", "cp1252"):
        try:
            b = s.encode(enc)
        except Exception:
            continue
        for dec in ("utf-8", "gbk"):
            try:
                t = b.decode(dec)
            except Exception:
                continue
            if _CJK(t):
                return t
    # ② utf-8 字节被 gbk 解码（锟斤拷）: 先把乱码 str 按 gbk 编码回原始字节，再 utf-8 解
    try:
        t = s.encode("gbk").decode("utf-8")
        if _CJK(t):
            return t
    except Exception:
        pass
    # ③ gbk 字节被 utf-8 解码（鐢樺）: 先按 utf-8 编码回原始字节，再 gbk 解
    try:
        t = s.encode("utf-8").decode("gbk")
        if _CJK(t):
            return t
    except Exception:
        pass
    return s


def _ensure_ffmpeg():
    """shazamio/pydub 处理音频要调 ffmpeg；系统没有就用手边自带的
    （imageio-ffmpeg 静态二进制），复制成 ffmpeg.exe 并加进 PATH。"""
    try:
        import shutil
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        bindir = os.path.join(HERE, "bin")
        os.makedirs(bindir, exist_ok=True)
        target = os.path.join(bindir, "ffmpeg.exe")
        if not os.path.exists(target):
            try:
                shutil.copy2(exe, target)
            except Exception:
                target = exe          # 复制失败就用原路径（PATH 一起加）
        bindirs = [os.path.dirname(target), os.path.dirname(exe)]
        for d in bindirs:
            if d and d not in os.environ.get("PATH", ""):
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass


_ensure_ffmpeg()


def log(s):
    print(s, flush=True)


def pick_device(force_idx=None):
    """挑采集设备：指定索引优先；否则找立体声混音。
    返回 (index, name, default_samplerate) 或 None。"""
    import sounddevice as sd
    devs = sd.query_devices()
    if force_idx is not None:
        d = devs[force_idx]
        return force_idx, str(d.get("name", "")), int(d.get("default_samplerate") or 48000)
    for i, d in enumerate(devs):
        if d.get("max_input_channels", 0) <= 0:
            continue
        if any(k.lower() in str(d.get("name", "")).lower() for k in MIX_KEYS):
            return i, str(d.get("name", "")), int(d.get("default_samplerate") or 48000)
    return None


def record(device_idx, seconds, sr):
    """录 seconds 秒（float32 单声道，用设备自己的默认采样率）。返回 numpy 数组。"""
    import numpy as np
    import sounddevice as sd
    n = int(seconds * sr)
    rec = sd.rec(n, samplerate=sr, channels=1, device=device_idx,
                 dtype="float32", blocking=True)
    if rec.ndim > 1:
        rec = rec.mean(axis=1)
    arr = np.ascontiguousarray(rec, dtype=np.float32)
    rms = (arr ** 2).mean() ** 0.5
    if rms < 1e-4:
        return None
    return arr


def to_pcm_bytes(arr):
    """float32 数组 -> int16 PCM 字节（16k/采集采样率，由调用方记住 sr）。"""
    import numpy as np
    pcm = np.clip(arr * 32767, -32768, 32767).astype(np.int16)
    return pcm.tobytes()


def recognize_pcm(pcm_bytes, sr):
    """Shazam 识别。直接构造 AudioSegment（raw PCM），不走 pydub 探测（免 ffprobe）。
    返回 dict（含 track 信息或 ok=False）。"""
    import asyncio
    from shazamio import Shazam
    from pydub import AudioSegment

    seg = AudioSegment(data=pcm_bytes, sample_width=2, frame_rate=sr, channels=1)

    async def _run():
        sh = Shazam()
        return await sh.recognize_song(seg)

    res = asyncio.run(_run())
    track = (res or {}).get("track") or {}
    if not track:
        return {"ok": False, "why": "no_match"}
    title = track.get("title") or ""
    artist = track.get("subtitle") or ""
    return {
        "ok": True,
        "title": fix_mojibake(title),
        "artist": fix_mojibake(artist),
        "album": (track.get("sections") or [{}])[0].get("metadata") if track.get("sections") else None,
        "coverart": (track.get("images") or {}).get("coverart") or "",
        "link": (track.get("share") or {}).get("link") or "",
        "raw": track.get("key") or "",
    }


def record_loopback(seconds):
    """WASAPI loopback：抓【默认输出设备】的声音（蓝牙/HDMI/音箱都行）。
    返回 (arr, samplerate, 设备名) 或 None。"""
    try:
        import numpy as np
        import pyaudiowpatch as pa
        p = pa.PyAudio()
        try:
            dev = p.get_default_wasapi_loopback()
        except Exception:
            dev = None
        if not dev or "index" not in dev:
            p.terminate()
            return None
        sr = int(dev["defaultSampleRate"])
        ch = max(1, int(dev["maxInputChannels"]))
        n = int(seconds * sr)
        stream = p.open(format=pa.paFloat32, channels=ch, rate=sr,
                        input=True, input_device_index=dev["index"],
                        frames_per_buffer=1024)
        frames, got = [], 0
        while got < n:
            data = stream.read(min(1024, n - got), exception_on_overflow=False)
            frames.append(np.frombuffer(data, dtype=np.float32))
            got += 1024
        stream.stop_stream()
        stream.close()
        a = np.concatenate(frames)[:n]
        if ch > 1:
            a = a.reshape(-1, ch).mean(axis=1)
        p.terminate()
        return np.ascontiguousarray(a, dtype=np.float32), sr, str(dev.get("name") or "默认输出")
    except Exception:
        return None


def identify_once(device_idx, seconds):
    """录一次 + 识别一次。优先抓默认输出（loopback），失败退回立体声混音。"""
    lb = record_loopback(seconds)
    if lb is not None:
        arr, sr, dname = lb
        if (arr ** 2).mean() ** 0.5 >= 1e-4:
            r = recognize_pcm(to_pcm_bytes(arr), sr)
            r["device"] = dname
            return r
    dev = pick_device(device_idx)
    if not dev:
        return {"ok": False, "why": "no_device"}
    idx, name, sr = dev
    try:
        arr = record(idx, seconds, sr)
    except Exception as e:
        return {"ok": False, "why": "record_error:%s" % e, "device": name}
    if arr is None:
        return {"ok": False, "why": "silence", "device": name}
    r = recognize_pcm(to_pcm_bytes(arr), sr)
    r["device"] = name
    return r


def watch_loop(device_idx, seconds, interval=18, quiet=False):
    """循环识别：同一首歌不重复输出；歌曲变化才输出 JSON 行。"""
    last_sig = ""
    while True:
        try:
            r = identify_once(device_idx, seconds)
            if r.get("ok"):
                sig = "%s|%s" % (r.get("title") or "", r.get("artist") or "")
                if sig != last_sig:
                    last_sig = sig
                    log(json.dumps(r, ensure_ascii=False))
            elif not quiet:
                log(json.dumps(r, ensure_ascii=False))
        except KeyboardInterrupt:
            break
        except Exception as e:
            log(json.dumps({"ok": False, "why": "error:%s" % e}, ensure_ascii=False))
        time.sleep(interval)


def main():
    ap = argparse.ArgumentParser(description="听歌识曲（Shazam 协议）")
    ap.add_argument("--list", action="store_true", help="列出采集设备")
    ap.add_argument("--once", action="store_true", help="识别一次")
    ap.add_argument("--watch", action="store_true", help="循环识别")
    ap.add_argument("--device", type=int, default=None, help="设备索引")
    ap.add_argument("--seconds", type=int, default=8, help="录音秒数（默认 8）")
    ap.add_argument("--interval", type=int, default=18, help="watch 间隔秒数（默认 18）")
    ap.add_argument("--quiet", action="store_true", help="watch 时无匹配不输出")
    a = ap.parse_args()

    if a.list:
        import sounddevice as sd
        for i, d in enumerate(sd.query_devices()):
            if d.get("max_input_channels", 0) > 0:
                log("%d\t%s" % (i, d.get("name", "")))
        return

    if a.once:
        log(json.dumps(identify_once(a.device, a.seconds), ensure_ascii=False))
        return

    if a.watch:
        dev = pick_device(a.device)
        if not dev:
            log(json.dumps({"ok": False, "why": "no_device"}, ensure_ascii=False))
            return
        log("[watch] 设备: %s（%s）" % (dev[1], dev[0]))
        watch_loop(a.device, a.seconds, a.interval, a.quiet)
        return
    ap.print_help()


if __name__ == "__main__":
    main()
