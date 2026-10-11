# -*- coding: utf-8 -*-
"""
fairy_wake_listen.py -- 喊了就应（立即可用版）

【为什么有这个文件】
  用户反复喊「你好Fairy」没反应，因为：
    · Sherpa-ONNX KWS 方案做出来了，但【还没接进 fairy_ear.py】
    · 而 fairy_ear.py 之前被停掉了（那时它还是坏的）
    -> 等于对着一台关着的麦克风喊
  这个文件是【立即可用】的过渡版：双引擎监听，命中就播一句游戏原声应答。

【两个引擎一起听（提高命中）】
  A 中文模型 + n ǐ h ǎo f a i r y   <- 「你好fairy」
  B 英文模型 + ▁FA IR Y             <- 「Fairy」（用户只说英文词也能中）

【用法】
  py -3.12 D:\\FAIRY\\tools\\fairy_wake_listen.py              一直听
  py -3.12 D:\\FAIRY\\tools\\fairy_wake_listen.py 120          只听 120 秒
  py -3.12 D:\\FAIRY\\tools\\fairy_wake_listen.py --list        列出麦克风

【应答】命中后随机播一句【游戏原声】（我在 / 主人 / 对了主人 / 嗯 / 好的主人 / 是主人）
"""
import os
import random
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import sounddevice as sd
import fairy_root
import fairy_endpoints as ep

try:
    import sherpa_onnx
except ImportError:
    print("sherpa_onnx not installed")
    raise SystemExit(2)

MROOT = fairy_root.WAKEWORD_MODELS
ZH = os.path.join(MROOT, "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01")
EN = os.path.join(MROOT, "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01")
KW = fairy_root.WAKEWORD_KW
VROOT = fairy_root.VOICES

ZH_RULE = "n \u01d0 h \u01ceo f a i r y :1.0 @\u4f60\u597dfairy"
EN_RULE = "\u2581FA IR Y :1.0 @FAIRY"
# 备用关键词（放宽命中率）：只喊 fairy / 你好飞瑞
ZH_RULE2 = "f a i r y :1.0 @fairy"
ZH_RULE3 = "n \u01d0 h \u01ceo f \u0113i r u\u00ec :1.0 @\u4f60\u597d\u98de\u745e"

# 应答池：全部是【游戏里 Fairy 的原声】
REPLY_POOL = [
    ("9e5768b731f53e37.wav", "\u6211\u5728", 0.56),
    ("143061844b420ef5.wav", "\u6211\u5728", 0.59),
    ("ef32290c16f63afa.wav", "\u4e3b\u4eba", 0.69),
    ("a5e42e694b80c7a7.wav", "\u5bf9\u4e86\u4e3b\u4eba", 0.85),
    ("65ae767b39599fc6.wav", "\u55ef", 0.95),
    ("8cfb5d9711ed5296.wav", "\u55ef", 0.95),
    ("0413ca59c525a8c1.wav", "\u597d\u7684\u4e3b\u4eba", 0.95),
    ("1388e9a7ee6eb298.wav", "\u662f\u4e3b\u4eba", 1.01),
    ("1c303966a663b7b6.wav", "\u597d\u7684\u4e3b\u4eba", 1.09),
]


def write_kw(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text + "\n")
    return path


def build(base, kwpath, score=1.0, thr=0.10):
    p = lambda n: os.path.join(base, n)
    return sherpa_onnx.KeywordSpotter(
        tokens=p("tokens.txt"),
        encoder=p("encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
        decoder=p("decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
        joiner=p("joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx"),
        num_threads=2, keywords_file=kwpath,
        keywords_score=score, keywords_threshold=thr, provider="cpu")


def find_mic():
    best = None
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] <= 0:
            continue
        nm = d["name"]
        if any(k in nm for k in ("\u7acb\u4f53\u58f0\u6df7\u97f3", "Stereo Mix",
                                 "Sound Mapper", "Nahimic", "\u4e3b\u58f0\u97f3")):
            continue
        if "Hands-Free" in nm or "Bluetooth" in nm:
            return i, nm
        if best is None:
            best = (i, nm)
    return best if best else (None, None)


REPLY_TEXTS = ["我在", "主人", "对了主人", "嗯", "好的主人", "是主人"]
CLONE_URL = "<动态：ep.tts_get() 内部按降级链选择>"


def play_reply(last_text):
    """应答：走克隆合成（不再播游戏原文件）。
       ★ 2026-10-09 用户要求取消和游戏的关联。
       ★ 2026-10-10 独立运行改造：改走 ep.tts_get() 的统一降级链
         （9881 直连 -> DSH 插件 -> 本地 fairy_host -> DSH tts-api），
         DSH 关掉后唤醒应答照样出声。
    """
    import wave as wv
    import tempfile
    cands = [x for x in REPLY_TEXTS if x != last_text] or REPLY_TEXTS
    txt = random.choice(cands)
    try:
        r = ep.tts_get(txt, timeout=90)
        if not (r and r.get("ok") and r.get("data")):
            _why = "; ".join("%s:%s" % (t.get("route"), t.get("error"))
                             for t in (r.get("tried") or []))[:160] if r else "tts_get 异常"
            print("   clone 失败(%s)" % _why, flush=True)
            return last_text
        data = r["data"]
        print("   clone 路由=%s 音色=%s %dB" % (r.get("route"), r.get("voice"), len(data)),
              flush=True)
        if len(data) < 200:
            print("   clone returned too little (%d B)" % len(data), flush=True)
            return last_text
        tmp = os.path.join(tempfile.gettempdir(), "fairy_wake_reply.wav")
        with open(tmp, "wb") as f:
            f.write(data)
        with wv.open(tmp, "rb") as w:
            sr = w.getframerate()
            nf = w.getnchannels()
            a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
            if nf > 1:
                a = a.reshape(-1, nf)
        sd.play(a, sr, blocking=True)
        print("           (clone voice=%s, said: %s)" % (voice, txt), flush=True)
    except Exception as e:
        print("   clone reply failed: %s: %s" % (type(e).__name__, e), flush=True)
    return txt
def main():
    a = sys.argv[1:]
    if "--list" in a:
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                print("   [%2d] %s" % (i, d["name"][:60]))
        return 0

    secs = float(a[0]) if a and a[0].replace(".", "").isdigit() else 0.0

    kz = write_kw(os.path.join(KW, "live_zh.txt"),
                  ZH_RULE + "\n" + ZH_RULE2 + "\n" + ZH_RULE3)
    ke = write_kw(os.path.join(KW, "live_en.txt"), EN_RULE)
    print("building engines (threshold=0.05, multi-keyword) ...", flush=True)
    t0 = time.time()
    eng = [("ZH", build(ZH, kz, thr=0.05), None),
           ("EN", build(EN, ke, thr=0.05), None)]
    eng = [(n, k, k.create_stream()) for n, k, _ in eng]
    print("   built in %.2fs" % (time.time() - t0), flush=True)
    print("   ZH keywords: %s | %s | %s" % (ZH_RULE, ZH_RULE2, ZH_RULE3), flush=True)
    print("   EN keywords: %s" % EN_RULE, flush=True)

    dev, devname = find_mic()
    if dev is None:
        print("no microphone found", flush=True)
        return 2
    print("MIC idx %d %s" % (dev, devname), flush=True)

    sr, hop = 16000, 1600
    hits = 0
    last_wav = ""
    last_hit = 0.0
    t_start = time.time()
    lvl_max = 0.0
    lvl_t = time.time()
    nblk = 0
    print("", flush=True)
    print("***** 开始听：请喊「你好Fairy」或「Fairy」 *****", flush=True)
    print("   命中会播一句游戏原声应答（我在/主人/嗯/好的主人）", flush=True)
    print("   每 3 秒报一次【麦克风收到的最大音量】，用来确认麦克风真的在收音", flush=True)
    print("-" * 56, flush=True)
    try:
        with sd.RawInputStream(samplerate=sr, blocksize=hop, channels=1,
                               dtype="float32", device=dev) as st:
            while True:
                if secs and time.time() - t_start >= secs:
                    break
                data, _ = st.read(hop)
                arr = np.frombuffer(data, dtype=np.float32)
                if arr.size == 0:
                    continue
                nblk += 1
                _rms = float(np.sqrt(np.mean(arr * arr)) + 1e-12)
                if _rms > lvl_max:
                    lvl_max = _rms
                if time.time() - lvl_t >= 3.0:
                    print("[%6.1fs] LVL max RMS over 3s = %.5f   %s"
                          % (time.time() - t_start, lvl_max,
                             "有声音" if lvl_max > 0.002 else "很安静"), flush=True)
                    lvl_t = time.time()
                    lvl_max = 0.0
                for name, kws, s in eng:
                    s.accept_waveform(sr, arr)
                    while kws.is_ready(s):
                        kws.decode_stream(s)
                        r = kws.get_result(s)
                        if r:
                            now = time.time()
                            if now - last_hit < 2.5:
                                kws.reset_stream(s)
                                continue
                            last_hit = now
                            hits += 1
                            el = now - t_start
                            print("[%6.1fs] *** HIT via %-14s keyword=%s" % (el, name, r), flush=True)
                            last_wav = play_reply(last_wav)
                            print("           -> replied with %s" % last_wav, flush=True)
                            kws.reset_stream(s)
    except KeyboardInterrupt:
        print("(stopped)", flush=True)
    except Exception as e:
        print("ERR %s: %s" % (type(e).__name__, e), flush=True)
        return 1
    print("-" * 56, flush=True)
    print("total hits: %d" % hits, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
