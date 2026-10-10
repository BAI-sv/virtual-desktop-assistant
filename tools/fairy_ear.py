# -*- coding: utf-8 -*-
import json
import os
import subprocess
import sys
import time
import fairy_root


CREATE_NO_WINDOW = fairy_root.CREATE_NO_WINDOW
import fairy_endpoints as ep
import threading as _threading

T = os.path.dirname(os.path.abspath(__file__))
LOG = fairy_root.LOGS
SAY_FILE = os.path.join(LOG, "ear_say.txt")
STATE = os.path.join(LOG, "ear_state.json")
LOG_FILE = os.path.join(LOG, "fairy_ear.log")
VOICE_DIR = fairy_root.VOICES


PLAY_GAME_ORIGINAL = False
MODEL_DIR = fairy_root.WHISPER_MODELS
OW = 1.0


CFG = {


    "enabled": True,
    "rate": 16000,


    "block": 0.1,


    "tail_silence": 0.5,
    "max_utt": 8.0,
    "min_utt": 0.35,
    "lead_pad": 0.15,
    "trim_win": 0.05,
    "rms_gate": 0.001,


    "cooldown": 2.0,
    "dialogue": True,
    "dialogue_idle": 30.0,
    "stt_mode": "A",
    "model": "small",


    "kws": True,
    "kws_threshold": 0.10,
    "kws_score": 1.0,
    "kws_zh": False,
}


ANSWER_POOL = [
    "嗯", "我在", "你好主人", "请说", "主人请讲", "怎么了",
    "主人", "主人我正在待机", "主人请注意", "肯定", "认同",
]


STT_MODES = {
    "A": {"language": "zh", "prompt": "Fairy。飞瑞。法瑞。菲瑞。嗨Fairy。你好主人。"},
    "B": {"language": None, "prompt": "Fairy. 飞瑞。法瑞。菲瑞。"},
    "C": {"language": "zh", "prompt": None},
}


OLLAMA = "http://127.0.0.1:11434"
OLLAMA_MODEL = "qwen3:30b-a3b"
SAY_URL = "<动态：ep.tts_get() 内部按降级链选择>"


WAKE_CANDIDATES = [
    "fairy", "飞瑞", "法瑞", "菲瑞", "法伊", "肥瑞", "菲丽", "飞丽", "法里",
    "菲儿", "飞儿", "费瑞", "菲瑞", "法雷", "菲雷",
]
WAKE_PREFIX = ["嗨", "喂", "嘿", "你好", "那个"]

def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    try:
        os.makedirs(LOG, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as e:
        print("[ear] 写日志失败: %s" % e, flush=True)


_AUDIO_SAVED = False

_AUDIO_HIJACK_HINTS = ("Hands-Free", "免提", "Bluetooth", "蓝牙", "Hands-Free", "AirPods", "耳机")


_AUDIO_GUARD_ENABLED = False

def _audio_current_name():
    try:
        import fairy_audio
        return fairy_audio.name_of(fairy_audio.current_device_id()) or ""
    except Exception:
        return ""

def _audio_is_hijacked(nm):
    return bool(nm) and any(h in nm for h in _AUDIO_HIJACK_HINTS)

def _audio_restore():
    global _AUDIO_SAVED
    if not _AUDIO_SAVED:
        return
    try:
        import fairy_audio
        fairy_audio.restore()
        log("已还原默认音频输出")
    except Exception as e:
        log("还原默认音频输出失败（不影响耳朵已退出）: %s: %s" % (type(e).__name__, e))

def _audio_sigint(signum, frame):
    _audio_restore()
    raise KeyboardInterrupt()

def _audio_guard_start():
    global _AUDIO_SAVED
    if _AUDIO_SAVED:
        return True
    try:
        import atexit
        import signal
        import fairy_audio
        nm = _audio_current_name()
        if _audio_is_hijacked(nm):
            log("⚠ 体检：默认输出是【蓝牙耳机类】%s" % nm)
            log("  疑似已被麦克风占用劫持；尝试还原到上次记录的设备…")
            fairy_audio.restore()
            nm2 = _audio_current_name()
            if nm2 and nm2 != nm:
                log("  已修复 -> %s" % nm2)
            else:
                log("  还原未生效（状态文件里可能没有可用记录）")
        else:
            log("体检通过：默认输出是 %s" % (nm or "(未知)"))
        fairy_audio.save()
        _AUDIO_SAVED = True
        atexit.register(_audio_restore)
        try:
            signal.signal(signal.SIGINT, _audio_sigint)
        except Exception:
            pass
        log("已记录当前默认音频输出（退出时会还原）")
        return True
    except Exception as e:
        log("音频守卫启动失败（不影响耳朵）: %s: %s" % (type(e).__name__, e))
        return False

def _audio_save():
    return _audio_guard_start()

def load_cfg():
    try:
        p = fairy_root.CONFIG
        if os.path.exists(p):
            j = json.load(open(p, encoding="utf-8"))
            e = j.get("ear") or {}
            for k, v in e.items():
                if k in CFG:
                    CFG[k] = v
    except Exception as ex:
        log("读配置失败（用默认）: %s" % ex)
    return CFG

def load_logic():
    import importlib.util
    p = os.path.join(T, "wake_logic.py")
    if not os.path.exists(p):
        log("★ 缺少 wake_logic.py（这个文件应随项目提供，请检查是否被误删）")
        return None
    s = importlib.util.spec_from_file_location("wake_logic", p)
    m = importlib.util.module_from_spec(s)
    sys.modules["wake_logic"] = m
    s.loader.exec_module(m)
    return m


def list_inputs():
    import sounddevice as sd
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] <= 0:
            continue
        n = d["name"]
        lo = ("立体声混音" in n) or ("Stereo Mix" in n) or ("Stereo input" in n)
        kind = "回环(不可用)" if lo else ("蓝牙免提" if "Hands-Free" in n else
               ("USB" if "USB" in n.upper() else ("线路" if "Line" in n or "线路" in n else "其他")))
        if "Mapper" in n or "主声音" in n:
            continue
        out.append({"index": i, "name": n, "lo": lo, "kind": kind,
                    "ch": d["max_input_channels"], "rate": int(d["default_samplerate"])})
    return out

def pick_input(devs):
    real = [d for d in devs if not d["lo"]]
    if not real:
        return None
    order = {"蓝牙免提": 0, "USB": 1, "线路": 2, "其他": 3}
    real.sort(key=lambda d: order.get(d["kind"], 9))
    return real[0]

def cmd_list():
    devs = list_inputs()
    print("=== 录音设备（共 %d 个）===" % len(devs))
    for d in devs:
        print("   idx %-3d in%d  %-10s %-46s%s" % (
            d["index"], d["ch"], d["kind"], d["name"][:46],
            "   ← 回环，绝不能用（会录到自己说话）" if d["lo"] else ""))
    p = pick_input(devs)
    print("\n=== 建议 ===")
    if p:
        print("   用 idx %d：%s（%s）" % (p["index"], p["name"], p["kind"]))
        print("   ★ 提醒：蓝牙麦克风走 HFP 免提，占用时耳机音质会从立体声掉到通话音质")
    else:
        print("   ✗ 没有可用的真麦克风（只有回环设备）—— 插耳机/麦克风再试")
    return 0


def edit_distance(a, b):
    if abs(len(a) - len(b)) > 2:
        return 99
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]

def match_wake(text):
    if not text:
        return False, 99
    t = text.lower()
    for ch in " \t\n.,!?;:，。！？；：、":
        t = t.replace(ch, "")
    for c in WAKE_CANDIDATES:
        if c in t:
            return True, 0
    best = 99
    for c in WAKE_CANDIDATES:
        if c.isascii():
            for pre in WAKE_PREFIX:
                pass
            best = min(best, edit_distance(t, c))
    return False, best

def pick_answer(logic, pool, last_idx):
    import random
    n = len(pool)
    r = random.randrange(n) if n else 0
    idx = r
    if logic is not None:
        try:
            idx = logic.应答序号(r, last_idx, n)
        except Exception as e:
            log("CNSH 应答序号失败，退回随机: %s" % e)
    if not isinstance(idx, int) or not (0 <= idx < n):
        idx = r % n if n else 0
    return idx

def find_wav(text):
    try:
        vi = fairy_root.VOICE_INDEX
        j = json.load(open(vi, encoding="utf-8"))
        vd = j.get("voices_dir") or "voices"
        base = vd if os.path.isabs(vd) else os.path.join(os.path.dirname(vi), vd)
        for e in (j.get("lines") or []):
            if (e.get("t") or "").strip() == text:
                fp = os.path.join(base, e.get("w") or "")
                if os.path.exists(fp):
                    return fp
    except Exception as ex:
        log("查语料库失败: %s" % ex)
    return None

def play_wav(path):
    try:
        import numpy as np
        import sounddevice as sd
        import wave as wv
        with wv.open(path, "rb") as w:
            sr = w.getframerate()
            nf = w.getnchannels()
            d = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
            if nf > 1:
                d = d.reshape(-1, nf)
        sd.play(d, sr, blocking=True)
        return True
    except Exception as e:
        log("播应答失败: %s" % e)
        return False


KWS_EN_DIR = os.path.join(fairy_root.WAKEWORD_MODELS,
                          "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01")
KWS_ZH_DIR = os.path.join(fairy_root.WAKEWORD_MODELS,
                          "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01")
KWS_EN_RULE = "\u2581FA IR Y :1.0 @FAIRY"
KWS_ZH_RULE = "n \u01d0 h \u01ceo f a i r y :1.0 @\u4f60\u597dfairy"
KWS_HOP_S = 0.1

def _kws_build(base, rule_text, tag):
    import sherpa_onnx
    p = lambda n: os.path.join(base, n)
    enc = p("encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx")
    dec = p("decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx")
    joi = p("joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx")
    tok = p("tokens.txt")
    for f in (enc, dec, joi, tok):
        if not os.path.exists(f):
            log("KWS[%s] 缺文件：%s" % (tag, f))
            return None

    kw = os.path.join(LOG, "kws_%s.txt" % tag)
    try:
        os.makedirs(LOG, exist_ok=True)
        with open(kw, "w", encoding="utf-8", newline="\n") as f:
            f.write(rule_text + "\n")
    except Exception as e:
        log("KWS[%s] 写关键词失败: %s" % (tag, e))
        return None
    return sherpa_onnx.KeywordSpotter(
        tokens=tok, encoder=enc, decoder=dec, joiner=joi,
        num_threads=2, keywords_file=kw,
        keywords_score=float(CFG.get("kws_score", 1.0)),
        keywords_threshold=float(CFG.get("kws_threshold", 0.10)),
        provider="cpu")

def kws_load():
    try:
        import sherpa_onnx
    except Exception as e:
        log("KWS 不可用（sherpa_onnx 未装）: %s: %s" % (type(e).__name__, e))
        return []
    jobs = [("en", KWS_EN_DIR, KWS_EN_RULE)]
    if CFG.get("kws_zh"):
        jobs.append(("zh", KWS_ZH_DIR, KWS_ZH_RULE))
    out = []
    t0 = time.time()
    for tag, base, rule in jobs:
        try:
            sp = _kws_build(base, rule, tag)
            if sp is None:
                continue
            out.append((tag, sp, sp.create_stream()))
        except Exception as e:
            log("KWS[%s] 加载失败: %s: %s" % (tag, type(e).__name__, e))
    if out:
        log("KWS 就绪（%s，%.2fs）· 阈值=%.2f"
            % ("+".join(t for t, _, _ in out), time.time() - t0,
               float(CFG.get("kws_threshold", 0.10))))
    else:
        log("✗ KWS 引擎全部加载失败")
    return out

def _kws_new_stream(engines, i):
    tag, sp, _old = engines[i]
    try:
        engines[i] = (tag, sp, sp.create_stream())
    except Exception as e:
        log("KWS[%s] 重建流失败: %s: %s" % (tag, type(e).__name__, e))

def kws_feed(engines, arr, sr):
    for i in range(len(engines)):
        tag, sp, st = engines[i]
        try:
            st.accept_waveform(sr, arr)
            while sp.is_ready(st):
                sp.decode_stream(st)
                r = sp.get_result(st)
                if r:
                    _kws_new_stream(engines, i)
                    return tag, r
        except Exception as e:
            log("KWS[%s] 喂音频出错（跳过这块）: %s: %s"
                % (tag, type(e).__name__, e))
    return None

def kws_reset(engines):
    for i in range(len(engines)):
        _kws_new_stream(engines, i)

def kws_feed_wav(engines, audio, sr=16000):
    import numpy as np
    a = np.asarray(audio, dtype="float32").ravel()
    hop = int(sr * KWS_HOP_S)
    hit = None
    for i in range(0, len(a), hop):
        r = kws_feed(engines, a[i:i + hop], sr)
        if r and hit is None:
            hit = r
            break
    return hit

_MODEL = None
_MODEL_LOCK = _threading.Lock()

def warm_whisper_async():
    def _w():
        try:
            get_model()
        except Exception as e:
            log("whisper 后台预加载失败（唤醒不受影响；指令识别会现场加载）: %s: %s"
                % (type(e).__name__, e))
    th = _threading.Thread(target=_w, daemon=True, name="whisper-warm")
    th.start()
    return th

def whisper_ready():
    return _MODEL is not None

def get_model():
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    with _MODEL_LOCK:
        if _MODEL is not None:
            return _MODEL
        from faster_whisper import WhisperModel
        t0 = time.time()
        _MODEL = WhisperModel(CFG["model"], device="cpu", compute_type="int8",
                              download_root=MODEL_DIR)

        try:
            import numpy as np
            segs, _ = _MODEL.transcribe(np.zeros(16000, dtype="float32"), language="zh", beam_size=1)
            list(segs)
        except Exception as e:
            log("预热失败（不影响）: %s" % e)
        log("whisper 模型就绪（%s，%.1fs）" % (CFG["model"], time.time() - t0))
    return _MODEL

def transcribe(audio, mode=None):
    import numpy as np
    m = get_model()
    arr = np.asarray(audio, dtype="float32")
    md = STT_MODES.get(mode or CFG.get("stt_mode", "A"), STT_MODES["A"])
    kw = {"beam_size": 1, "vad_filter": False}
    if md.get("language"):
        kw["language"] = md["language"]
    if md.get("prompt"):


        kw["initial_prompt"] = md["prompt"]
    segs, _info = m.transcribe(arr, **kw)
    return "".join(s.text for s in segs).strip()

def trim_silence(a, rate, gate, pad):
    import numpy as np
    win = max(1, int(rate * CFG.get("trim_win", 0.05)))
    n = len(a) // win
    if n <= 2:
        return a
    idx = []
    for i in range(n):
        seg = a[i * win:(i + 1) * win]
        if float(np.sqrt(np.mean(seg * seg))) >= gate * 0.6:
            idx.append(i)
    if not idx:
        return a
    padw = int(pad / CFG.get("trim_win", 0.05))
    lo = max(0, idx[0] - padw)
    hi = min(n, idx[-1] + 1 + padw)
    return a[lo * win: hi * win]

def ask_ollama(text, timeout=90):
    import json as _j
    import urllib.request
    body = _j.dumps({
        "model": OLLAMA_MODEL,
        "prompt": text,
        "stream": False,
        "keep_alive": "2h",
        "options": {"num_predict": 150, "temperature": 0.6},
    }).encode("utf-8")
    req = urllib.request.Request(OLLAMA + "/api/generate", data=body,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        j = _j.loads(r.read().decode("utf-8"))
    return (j.get("response") or "").strip()

def synth_to_file(text):
    r = ep.tts_get(text)
    if not (r and r.get("ok") and r.get("data")):
        _why = "; ".join("%s:%s" % (t.get("route"), t.get("error"))
                         for t in (r.get("tried") or []))[:200] if r else "tts_get 异常"
        print("[ear] 语音通道全失败(%s)" % _why, flush=True)
        return None
    data = r["data"]
    if len(data) < 500:
        return None
    is_mp3 = (data[:3] == b"ID3"
              or (len(data) > 1 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0))
    p = os.path.join(LOG, "ear_say" + (".mp3" if is_mp3 else ".wav"))
    with open(p, "wb") as f:
        f.write(data)
    print("[ear] 语音=克隆路由(%s|%s) %d 字节"
          % (r.get("voice"), r.get("route"), len(data)), flush=True)
    return p

def speak_text(text):
    if not text:
        return "空"

    if PLAY_GAME_ORIGINAL:
        wav = find_wav(text)
        if wav:
            try:
                play_wav(wav)
                return "原声"
            except Exception as e:
                log("播原声失败: %s" % e)
    try:
        p = synth_to_file(text)
        if p:
            play_wav(p)
            return "克隆"
    except Exception as e:
        log("克隆合成失败: %s" % e)
    try:
        with open(SAY_FILE, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        return "写文件(球无对外播报入口)"
    except Exception as e:
        log("写 ear_say.txt 失败: %s" % e)
    return "失败"


def drain_input(st, hop, n=4):
    for _ in range(max(0, int(n))):
        try:
            st.read(hop)
        except Exception:
            break

def cmd_run(seconds=0):
    import numpy as np
    import sounddevice as sd
    logic = load_logic()
    if logic is None:
        return 2
    devs = list_inputs()
    dev = pick_input(devs)
    if dev is None:
        log("✗ 没有可用的真麦克风（只有回环）—— 请连耳机/麦克风")
        return 2
    wake_count = 0
    ask_count = 0
    skipped = 0
    log("★ 蓝牙提醒：耳麦占用的麦克风走 HFP 免提，用耳机听歌时音质会下降")
    log("监听设备：idx %d %s（%s）@ %d Hz" % (dev["index"], dev["name"], dev["kind"], CFG["rate"]))
    rate = int(CFG["rate"])

    hop = int(rate * KWS_HOP_S)
    block_s = hop / float(rate)
    use_kws = bool(CFG.get("kws", True))
    engines = []
    try:

        if _AUDIO_GUARD_ENABLED:
            _audio_save()

        if use_kws:
            engines = kws_load()
        if engines:
            log("唤醒方式：★ KWS（Sherpa-ONNX）—— 每 0.1 秒流式检测")
        else:
            if use_kws:
                log("⚠ KWS 加载未成功 -> 退回老的「能量门限 + whisper」唤醒（慢但能用）")
            else:
                log("唤醒方式：能量门限 + whisper（ear.kws=false）")

        if CFG.get("dialogue", True) or not engines:
            log("whisper 已改为后台预加载（约 60 秒）：唤醒不受影响，说指令时基本已好")
            warm_whisper_async()
        else:
            log("连续对话已关 -> 不预加载 whisper（省 63 秒）")
        with sd.InputStream(device=dev["index"], channels=1, samplerate=rate,
                            blocksize=hop, dtype="float32") as st:
            log("耳朵已开（喊「Fairy」试试；--stop 停止）")

            buf = []
            silent = 0.0
            collecting = False
            last_wake = None
            last_idx = -1
            mode = "wake"
            last_voice = time.time()
            hold_until = 0.0
            mic_peak = 0.0
            t0 = time.time()
            log("模式=端点检测(tail=%.1fs max=%.1fs) · 对话=%s · STT=%s · 块=%.2fs"
                % (CFG["tail_silence"], CFG["max_utt"],
                   "开" if CFG.get("dialogue", True) else "关",
                   CFG.get("stt_mode", "A"), block_s))
            while True:
                if seconds and time.time() - t0 >= seconds:
                    break
                blk, _over = st.read(hop)
                a = blk[:, 0]
                now = time.time()


                if now < hold_until:


                    collecting = False
                    buf = []
                    silent = 0.0
                    continue


                woke = False
                if engines:
                    hit = kws_feed(engines, a, rate)
                    if hit:
                        tag, kw = hit
                        gap = 99.0 if last_wake is None else (now - last_wake)
                        if gap >= CFG["cooldown"]:
                            wake_count += 1
                            last_wake = now
                            log("★★ KWS 命中 #%d（模型=%s 关键词=%s 间隕=%.1fs）"
                                % (wake_count, tag, kw, gap))
                            woke = True
                        else:
                            log("KWS 命中但冷却中（间隔 %.1fs < %.1fs）-> 忽略"
                                % (gap, CFG["cooldown"]))
                if woke:
                    last_idx = pick_answer(logic, ANSWER_POOL, last_idx)
                    say = ANSWER_POOL[last_idx % len(ANSWER_POOL)]
                    route = speak_text(say)
                    log("唤醒 #%d 应答「%s」（%s）" % (wake_count, say, route))

                    drain_input(st, hop, 4)
                    kws_reset(engines)
                    hold_until = time.time() + 1.0
                    if CFG.get("dialogue", True):
                        mode = "dialogue"
                        last_voice = time.time()
                        log("→ 进入连续对话（直接说指令即可，%.0f 秒没声自动退出）"
                            % CFG["dialogue_idle"])
                    try:
                        json.dump({"listening": True, "device": dev["name"], "rate": rate,
                                   "mode": mode, "wake_count": wake_count, "ask_count": ask_count,
                                   "last_wake": time.strftime("%H:%M:%S"),
                                   "skipped_quiet": skipped, "stt_mode": CFG.get("stt_mode", "A"),
                                   "wake_engine": ("kws" if engines else "gate+whisper")},
                                  open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
                    except Exception as _e_s:
                        log("写状态失败: %s" % _e_s)
                    continue


                need_ep = (mode == "dialogue" and CFG.get("dialogue", True)) or (not engines)
                if not need_ep:

                    skipped += 1
                    lv = float(np.max(np.abs(a)))
                    if lv > mic_peak:
                        mic_peak = lv
                    if skipped % 300 == 0:


                        tip = ""
                        if mic_peak < 0.01:
                            tip = "  ⚠ 麦克风几乎没收到声音（检查耳麦是否戴好/默音/没连上）"
                        log("静默/待唤醒已 %d 块（KWS 在岗，不做转写）· 麦克风峰值=%.4f%s"
                            % (skipped, mic_peak, tip))
                        mic_peak = 0.0
                    continue

                rms = float(np.sqrt(np.mean(a * a)) + 1e-12)
                enough = logic.声音够大(int(rms * 100000), int(CFG["rms_gate"] * 100000))

                if not collecting:
                    if not enough:
                        skipped += 1
                        if skipped % 60 == 0:
                            log("静音跳过 %d 块（本块 RMS=%.5f，门限 %.5f）"
                                % (skipped, rms, CFG["rms_gate"]))
                        if (mode == "dialogue" and CFG.get("dialogue", True)
                                and time.time() - last_voice > CFG["dialogue_idle"]):
                            mode = "wake"
                            log("对话闲置 %.0f 秒 -> 回到只等唤醒"
                                % (time.time() - last_voice))
                        continue
                    collecting = True
                    buf = [a.copy()]
                    silent = 0.0
                    log("★ 检测到人声 RMS=%.5f -> 开始收集（说到停才切）" % rms)
                    continue

                buf.append(a.copy())
                silent = 0.0 if enough else (silent + block_s)
                dur = len(buf) * block_s
                if silent < CFG["tail_silence"] and dur < CFG["max_utt"]:
                    continue


                collecting = False
                audio = np.concatenate(buf).astype("float32")
                buf = []
                audio = trim_silence(audio, rate, CFG["rms_gate"], CFG["lead_pad"])
                dur = len(audio) / rate
                if dur < CFG["min_utt"]:
                    log("段太短（%.2fs < %.2fs）丢弃" % (dur, CFG["min_utt"]))
                    continue
                last_voice = time.time()
                if not whisper_ready():
                    log("⚠ whisper 还在后台加载中，这一句会等它（首次约 60 秒）")
                try:
                    txt = transcribe(audio)
                except Exception as e:
                    log("识别失败: %s" % e)
                    continue
                if not txt:
                    log("⚠ 转写返回空（音频 %.2fs）-> 换下一段" % dur)
                    continue

                if not engines and mode == "wake":

                    has, ed = match_wake(txt)
                    gap = 99.0 if last_wake is None else (time.time() - last_wake)
                    hit = logic.该唤醒(has, ed, gap, CFG["cooldown"], len(txt))
                    log("听到「%s」 时长=%.2fs 含候选=%s 距离=%s 间隔=%.1fs -> %s"
                        % (txt[:46], dur, has, ed, gap, "★唤醒" if hit else "忽略"))
                    if not hit:
                        continue
                    wake_count += 1
                    last_wake = time.time()
                    last_idx = pick_answer(logic, ANSWER_POOL, last_idx)
                    say = ANSWER_POOL[last_idx % len(ANSWER_POOL)]
                    route = speak_text(say)
                    log("唤醒 #%d 应答「%s」（%s）" % (wake_count, say, route))
                    hold_until = time.time() + 1.2
                    if CFG.get("dialogue", True):
                        mode = "dialogue"
                        last_voice = time.time()
                        log("→ 进入连续对话（直接说指令即可，%.0f 秒没声自动退出）"
                            % CFG["dialogue_idle"])
                else:

                    log("指令「%s」（%.2fs）" % (txt[:60], dur))
                    try:
                        reply = ask_ollama(txt)
                    except Exception as e:
                        log("大脑不可用: %s" % e)
                        reply = ""
                    if not reply:
                        log("大脑没给出回答，跳过")
                        continue
                    ask_count += 1
                    log("回答「%s」" % reply[:90])
                    route = speak_text(reply[:110])
                    log("已说出（%s）" % route)
                    hold_until = time.time() + max(1.5, len(reply) * 0.22)

                try:
                    json.dump({"listening": True, "device": dev["name"], "rate": rate,
                               "mode": mode, "wake_count": wake_count, "ask_count": ask_count,
                               "last_wake": time.strftime("%H:%M:%S"),
                               "skipped_quiet": skipped, "stt_mode": CFG.get("stt_mode", "A"),
                               "wake_engine": ("kws" if engines else "gate+whisper")},
                              open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
                except Exception as _e_s:
                    log("写状态失败: %s" % _e_s)
    except Exception as e:
        log("✗ 启动失败: %s: %s" % (type(e).__name__, e))
        return 1
    log("已停止（唤醒 %d 次 · 对话问答 %d 次 · 跳过静音块 %d 个）"
        % (wake_count, ask_count, skipped))
    return 0

def load_wav_16k(path):
    import wave as wv
    import numpy as np
    with wv.open(path, "rb") as w:
        sr = w.getframerate()
        nf = w.getnchannels()
        raw = w.readframes(w.getnframes())
    d = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if nf > 1:
        d = d.reshape(-1, nf).mean(axis=1)
    if sr != 16000:
        n = max(1, int(len(d) * 16000 / sr))
        d = np.interp(np.linspace(0, len(d) - 1, n), np.arange(len(d)), d).astype(np.float32)
    return d.astype(np.float32)

def pick_fairy_stimulus():
    import json as _j
    vip = fairy_root.VOICE_INDEX
    try:
        vi = _j.load(open(vip, encoding="utf-8"))
    except Exception as e:
        log("读语料库失败: %s" % e)
        return None, None
    vd = vi.get("voices_dir") or "voices"
    base = vd if os.path.isabs(vd) else os.path.join(os.path.dirname(vip), vd)
    best = None
    for e in vi.get("lines") or []:
        tx = (e.get("t") or "").strip()
        fp2 = os.path.join(base, e.get("w") or "")
        if not tx or not os.path.exists(fp2):
            continue
        if "fairy" not in tx.lower():
            continue
        d = float(e.get("d") or 0)

        score = abs(d - 1.8)
        if best is None or score < best[0]:
            best = (score, tx, fp2, d)
    if best:
        return (best[1], best[2]), best[3]
    return None, None

def pick_stimuli(n, want_fairy=True):
    import json as _j
    vip = fairy_root.VOICE_INDEX
    try:
        vi = _j.load(open(vip, encoding="utf-8"))
    except Exception as e:
        log("读语料库失败: %s" % e)
        return []
    vd = vi.get("voices_dir") or "voices"
    base = vd if os.path.isabs(vd) else os.path.join(os.path.dirname(vip), vd)
    out = []
    for e in vi.get("lines") or []:
        tx = (e.get("t") or "").strip()
        w = e.get("w") or ""
        if not tx or not w:
            continue
        p2 = os.path.join(base, w)
        if not os.path.exists(p2):
            continue
        if ("fairy" in tx.lower()) != bool(want_fairy):
            continue
        d = float(e.get("d") or 0)
        if d < 0.8 or d > 12.0:
            continue
        out.append((tx, p2, d))
    out.sort(key=lambda x: x[1])
    if len(out) <= n:
        return out
    step = len(out) / float(n)
    return [out[int(i * step)] for i in range(n)]

def cmd_selftest_kws(n_hit=12, n_miss=20):
    import numpy as np
    print("=== KWS 唤醒自检（离线喂真游戏原声）===")
    if not CFG.get("kws", True):
        print("   ear.kws=false -> 先打开再测（或加 --kws-on）")
        return 2
    eng = kws_load()
    if not eng:
        print("   ✗ KWS 引擎加载失败（看上面的错误）")
        return 2
    print("   引擎：%s · 阈值=%.2f · 块=%.2fs"
          % ("+".join(x[0] for x in eng), float(CFG.get("kws_threshold", 0.10)), KWS_HOP_S))


    pos = pick_stimuli(n_hit, True)
    print("\n--- ① 正例（真含 Fairy 的原声，共 %d 条）---" % len(pos))
    hit_n = 0
    for tx, p2, d in pos:
        try:
            a = load_wav_16k(p2)
        except Exception as e:
            print("   [ERR ] %s: %s" % (os.path.basename(p2), e)); continue
        kws_reset(eng)
        r = kws_feed_wav(eng, a, 16000)
        ok = r is not None
        hit_n += ok
        print("   [%-4s] %.2fs  %-34s  <- %s"
              % ("HIT" if ok else "MISS", len(a) / 16000.0, tx[:34], os.path.basename(p2)))
    pct = (100.0 * hit_n / len(pos)) if pos else 0.0
    print("   ★ 命中 %d/%d = %.0f%%" % (hit_n, len(pos), pct))


    neg = pick_stimuli(n_miss, False)
    print("\n--- ② 反例（不含 Fairy 的原声，共 %d 条）---" % len(neg))
    fp_n = 0
    for tx, p2, d in neg:
        try:
            a = load_wav_16k(p2)
        except Exception:
            continue
        kws_reset(eng)
        r = kws_feed_wav(eng, a, 16000)
        if r:
            fp_n += 1
            print("   [误报!] %s  <- %s" % (tx[:40], os.path.basename(p2)))
    print("   ★ 误报 %d/%d" % (fp_n, len(neg)))


    print("\n--- ③ 对照：整段一次性喂 vs 流式喂（著名的坑）---")
    if pos:
        tx, p2, d = pos[0]
        a = load_wav_16k(p2)

        try:
            kws_reset(eng)
            got = None
            for tag, sp, st in eng:
                st.accept_waveform(16000, a)
                while sp.is_ready(st):
                    sp.decode_stream(st)
                    got = sp.get_result(st)
                    if got:
                        break
            print("   整段一次性喂·不 flush -> %s"
                  % ("命中(%s)" % (got,) if got else "不中"))
        except Exception as e:
            print("   整段一次性喂·不 flush -> 抛错: %s: %s"
                  % (type(e).__name__, e))

        try:
            kws_reset(eng)
            got2 = None
            for tag, sp, st in eng:
                st.accept_waveform(16000, a)
                st.input_finished()
                while sp.is_ready(st):
                    sp.decode_stream(st)
                    got2 = sp.get_result(st)
                    if got2:
                        break
            print("   整段一次性喂·flush  -> %s"
                  % ("命中(%s)" % (got2,) if got2 else "不中"))
        except Exception as e:
            print("   整段喂·flush -> 抛错: %s: %s" % (type(e).__name__, e))

        kws_reset(eng)
        sh = kws_feed_wav(eng, a, 16000)
        print("   流式喂(每 0.1 秒)         -> %s"
              % ("命中(%s)" % (sh,) if sh else "不中"))
        print("   ★ 结论：关键不是块大小，而是【必须跑 is_ready+decode_stream+get_result 循环】。")
        print("     只 accept_waveform 而不跑循环 -> 永远不中（网上那个“整段喂永远不中”的说法，真正原因就在这里）。")

    print("\n=== 结论 ===")
    print("   命中 %d/%d=%.0f%%  ·  误报 %d/%d" % (hit_n, len(pos), pct, fp_n, len(neg)))
    return 0

def cmd_selftest_e2e():
    import numpy as np
    log("=== 端到端自检开始 ===")

    kws_hit = None
    if CFG.get("kws", True):
        eng0 = kws_load()
        if eng0:
            stim0, _d0 = pick_fairy_stimulus()
            if stim0:
                try:
                    a0 = load_wav_16k(stim0[1])
                    kws_reset(eng0)
                    r0 = kws_feed_wav(eng0, a0, 16000)
                    kws_hit = r0 is not None
                    log("★ KWS 唤醒自检：「%s」 -> %s"
                        % (stim0[0][:30], ("命中 %s" % (r0,)) if kws_hit else "未命中"))
                except Exception as e:
                    log("KWS 自检出错: %s: %s" % (type(e).__name__, e))
        print("")
        print("=== ① 唤醒引擎（KWS）===")
        print("   %s" % ("★ 命中 ✓" if kws_hit else
                         ("✗ 未命中" if kws_hit is False else "✗ 引擎未加载")))
        print("")
    stim, dur = pick_fairy_stimulus()
    if not stim:
        log("✗ 语料库里找不到含 Fairy 的条目，无法自检")
        return 2
    txt_want, wav = stim
    log("测试素材（真原声）：「%s」 %.2fs  %s" % (txt_want, dur or 0, wav))
    a = load_wav_16k(wav)
    log("载入音频 %.2fs，RMS=%.5f 峰值=%.5f"
        % (len(a) / 16000, float(np.sqrt(np.mean(a * a))), float(np.max(np.abs(a)))))
    a = trim_silence(a, 16000, CFG["rms_gate"], CFG["lead_pad"])
    log("裁静音后 %.2fs" % (len(a) / 16000))


    print("")
    print("=== 三种识别配置对比（同一段音频）===")
    results = {}
    for m in ("A", "B", "C"):
        try:
            t0 = time.time()
            out = transcribe(a, mode=m)
            took = time.time() - t0
        except Exception as e:
            out = ""
            took = 0.0
            log("模式 %s 失败: %s" % (m, e))
        has, ed = match_wake(out)
        results[m] = (out, has, ed)
        print("   [%s] %-6s 耗时%5.2fs 含候选=%-5s 距离=%-3s  转写：%s"
              % (m, STT_MODES[m]["language"] or "auto", took, has, ed, out[:60]))
    print("")


    ok = False
    for m, (out, has, ed) in results.items():
        hit = False
        try:
            hit = bool(load_logic().该唤醒(has, ed, 99.0, CFG["cooldown"], len(out)))
        except Exception as e:
            log("调 CNSH 判定失败: %s" % e)
        if hit and out:
            ok = True
            log("★ 模式 %s 唤醒成功：转写「%s」" % (m, out[:50]))
            break

    if kws_hit:
        ok = True
        log("KWS 命中 -> 唤醒链路成功（whisper 只用于唤醒后转写指令）")
    log("端到端唤醒：%s" % ("成功 ✓" if ok else "失败 ✗"))
    if not ok:
        log("三种模式的转写都不含候选词 —— 需要扩充 WAKE_CANDIDATES（照着实际转写补）")


    print("")
    print("=== 连续对话链路（文本直喂，验证大脑与合成）===")
    q = "现在几点"
    try:
        t0 = time.time()
        reply = ask_ollama(q, timeout=120)
        print("   问：%s" % q)
        print("   答：%s   （%.1fs）" % ((reply or "(空)")[:90], time.time() - t0))
        if not reply:
            log("⚠ 大脑返回空 —— 检查是否误传了 think:false（本机 qwen3 传了会返回空）")
    except Exception as e:
        print("   ✗ 大脑调用失败: %s" % e)
    print("")
    log("=== 端到端自检结束 ===")
    return 0 if ok else 1

def cmd_selftest():
    logic = load_logic()
    if logic is None:
        return 2
    print("=== 唤醒判定自测（注入假转写）===")
    cases = [
        ("fairy", True), ("Fairy", True), ("嗨 fairy", True), ("飞瑞", True),
        ("法瑞在吗", True), ("喂喂喂", False), ("大家看这里", False),
        ("非常开心", False), ("飞机很吵", False), ("这是法伊吗", True),
    ]
    ok = 0
    for txt, exp in cases:
        has, ed = match_wake(txt)
        hit = logic.该唤醒(has, ed, 999, CFG["cooldown"], len(txt))
        good = (bool(hit) == exp)
        ok += good
        print("   %-14s 含候选=%-5s 距离=%-2d → %-6s %s" % (
            txt, has, ed, "唤醒" if hit else "忽略", "OK" if good else "FAIL 期望" + str(exp)))
    print("   命中判定 %d/%d" % (ok, len(cases)))
    print("\n=== 冷却 ===")
    for gap, exp in ((2.0, False), (7.0, True)):
        hit = logic.该唤醒(True, 0, gap, 6.0, 5)
        print("   间隔 %.0fs → %s %s" % (gap, "唤醒" if hit else "忽略", "OK" if bool(hit) == exp else "FAIL"))
    print("\n=== 应答挑选（避开上一条）===")
    for last in (-1, 0, 3):
        idx = pick_answer(logic, ANSWER_POOL, last)
        print("   上一条=%s → 选 %d (%s) %s" % (last, idx, ANSWER_POOL[idx],
              "OK" if idx != last else "FAIL 撞了"))
    print("\n=== 设备打分 ===")
    devs = list_inputs()
    print("   共 %d 个录音设备，其中回环 %d 个（必须排除）" % (len(devs), sum(1 for d in devs if d["lo"])))
    print("   → 建议：%s" % (pick_input(devs) or {}).get("name", "无可用"))
    return 0 if ok == len(cases) else 1

def cmd_status_json():
    st = {"listening": False, "pid": None, "device": None, "rate": CFG["rate"],
          "state": "idle", "wake_count": 0, "last_wake": None, "cpu": None}
    try:
        if os.path.exists(STATE):
            st.update(json.load(open(STATE, encoding="utf-8")))
    except Exception:
        pass
    try:
        import psutil
        st["cpu"] = psutil.cpu_percent(interval=0.4)
    except Exception:
        st["cpu"] = None
    print(json.dumps(st, ensure_ascii=False))
    return 0

def cmd_stop():
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
        "@(Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\""
        " | Where-Object {$_.CommandLine -like '*fairy_ear*'}).ProcessId"
        ], capture_output=True, timeout=40, creationflags=CREATE_NO_WINDOW)
    pids = [x.strip() for x in (r.stdout or b"").decode("utf-8", "replace").splitlines()
            if x.strip().isdigit()]
    if not pids:
        print("本来就没在跑")
        return 0
    me = str(os.getpid())
    for p in pids:
        if p == me:
            continue
        subprocess.run(["powershell", "-NoProfile", "-Command", "Stop-Process -Id %s -Force" % p],
                       capture_output=True, timeout=30,
                       creationflags=CREATE_NO_WINDOW)
    print("已停止 %d 个" % len([p for p in pids if p != me]))
    return 0

def main():
    a = sys.argv[1:]
    load_cfg()

    if "--kws-off" in a:
        CFG["kws"] = False
        print("KWS：已关闭 -> 退回老的「能量门限 + whisper」唤醒")
    if "--kws-on" in a:
        CFG["kws"] = True
        print("KWS：已打开")
    if "--kws-zh" in a:
        CFG["kws_zh"] = True
        print("KWS：额外挂中文模型（wenetspeech）一起听")
    if "--kws-threshold" in a:
        _it = a.index("--kws-threshold")
        if _it + 1 < len(a):
            try:
                CFG["kws_threshold"] = float(a[_it + 1])
                print("KWS 阈值设为", CFG["kws_threshold"])
            except Exception:
                print("阈值要是数字，如 --kws-threshold 0.05")
    if "--list" in a:
        return cmd_list()
    if "--selftest-kws" in a:

        if "--kws-off" not in a:
            CFG["kws"] = True
        _n, _m = 12, 20
        if "--n" in a:
            try:
                _n = int(a[a.index("--n") + 1])
            except Exception:
                pass
        if "--fp" in a:
            try:
                _m = int(a[a.index("--fp") + 1])
            except Exception:
                pass
        return cmd_selftest_kws(_n, _m)
    if "--selftest" in a:
        return cmd_selftest()
    if "--stt-mode" in a:
        _i = a.index("--stt-mode")
        if _i + 1 < len(a) and a[_i + 1].upper() in STT_MODES:
            CFG["stt_mode"] = a[_i + 1].upper()
            print("STT 模式设为", CFG["stt_mode"], "（", STT_MODES[CFG["stt_mode"]], "）")
    if "--no-dialogue" in a:
        CFG["dialogue"] = False
        print("已关闭连续对话")
    if "--selftest-e2e" in a:
        return cmd_selftest_e2e()
    if "--status-json" in a:
        return cmd_status_json()
    if "--stop" in a:
        return cmd_stop()
    secs = 0
    for i, x in enumerate(a):
        if x == "--run" and i + 1 < len(a) and a[i + 1].isdigit():
            secs = int(a[i + 1])

    if not CFG.get("enabled", True):
        print("[ear] 已按配置关闭（enabled = false），不启动常驻监听。", flush=True)
        print("[ear] 想打开：把本文件顶部 CFG 里的 enabled 改为 true。", flush=True)
        return 0
    return cmd_run(secs)

if __name__ == "__main__":
    sys.exit(main())
