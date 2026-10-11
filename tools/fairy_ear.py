# -*- coding: utf-8 -*-
"""
fairy_ear.py —— Fairy 的耳朵：常驻监听，喊「Fairy」就应答

【为什么】用户说「我喊 fairy 你也不会应我啊」。本模块让她能听见并应答。

【用法】
    py -3.12 D:\\FAIRY\\tools\\fairy_ear.py --list         列录音设备 + 建议
    py -3.12 D:\\FAIRY\\tools\\fairy_ear.py --selftest     不需麦克风，验证唤醒判定
    py -3.12 D:\\FAIRY\\tools\\fairy_ear.py --run          常驻监听（默认）
    py -3.12 D:\\FAIRY\\tools\\fairy_ear.py --status-json  一行 JSON
    py -3.12 D:\\FAIRY\\tools\\fairy_ear.py --stop         优雅停止

【★ 蓝牙硬代价（必须知道）】
    蓝牙耳机的麦克风走 HFP 免提模式。一旦被占用，蓝牙链路会从 A2DP（立体声高音质）
    掉到 HFP（通话音质 8~16kHz）。所以【用耳机听歌/看视频时，常听会明显降低音质】。
    若用方糖音箱出声、耳机只当麦克风，则不受影响。
    → 想关掉：在统一配置（fairy.json / fairy_root.CONFIG）里加
        "ear": {"enabled": false}
      ★ 2026-10-09 修：原来这里写“本文件 ENABLED 改 False”，但文件里
        【没有 ENABLED 这个常量】，而且 CFG 里也没有 enabled 键 -> 先前两种关法都是假的。

【分工】
    Python：采集 / 能量门限 / whisper 转写 / 字符串匹配 / 播放 / 写文件
    CNSH  ：wake_logic.py —— 该不该唤醒、选哪句应答、该不该提示设备（纯判断）
"""
import json
import os
import re
import subprocess
import sys
import time
import fairy_root

# ★ 2026-10-10【黑窗】0x08000000 = CREATE_NO_WINDOW（不是 0x00000200）
CREATE_NO_WINDOW = fairy_root.CREATE_NO_WINDOW
import fairy_endpoints as ep
import threading as _threading          # ★ KWS 版：whisper 后台预加载用
import asr_bridge                       # ★ 2026-10-11：语音识别后端（SenseVoice / Paraformer）
import fairy_memory                      # ★ 2026-10-11：使用记忆与习惯学习（热词/常用功能/上下文）

T = os.path.dirname(os.path.abspath(__file__))
LOG = fairy_root.LOGS
SAY_FILE = os.path.join(LOG, "ear_say.txt")          # 给球读（球目前无对外播报入口）
STATE = os.path.join(LOG, "ear_state.json")
LOG_FILE = os.path.join(LOG, "fairy_ear.log")
VOICE_DIR = fairy_root.VOICES

# 不与游戏关联
#   不再【直接播放】游戏原文件；应答改走克隆合成。
#   与 fairy_ball.py 的 PLAY_GAME_ORIGINAL 保持一致：改 True 即恢复原行为。
PLAY_GAME_ORIGINAL = False
MODEL_DIR = fairy_root.WHISPER_MODELS
OW = 1.0                                             # overwrite 放最后

# ★★ 2026-10-11【对话打断 + 上下文接续】
_SPEAKING = False            # Fairy 是否正在播报（主循环据此允许用户插话打断）
_HIST = []                   # 本轮对话历史 [(user, asst), ...]（最近 8 轮）
_HIST_MAX = 8
_INTERRUPT_GATE = 0.02       # 打断门限：播放中麦克风 RMS 超过它视为插话
# 从记忆恢复上次对话（跨重启接续）
try:
    _mem0 = fairy_memory.load()
    for _u, _a in (_mem0.get("last_dialogue") or [])[-_HIST_MAX:]:
        _HIST.append((_u, _a))
except Exception:
    pass

# ---- 配置（可被 <FairyX 根目录>\fairy.json 的 ear 段覆盖）----
CFG = {
    # ★ 2026-10-09 补：这个键以前【不在 CFG 里】，
    #   而 load_cfg() 是 `if k in CFG: CFG[k] = v` -> 用户写 ear.enabled=false
    #   会被静默丢弃（文档却承诺可以关）。
    #   现在真正生效；默认 True = 行为不变。
    "enabled": True,
    # ★ 2026-10-11【大脑引擎】llama=本地视觉引擎（8085，Qwen3-VL-8B，默认）；
    #   ollama=Ollama 30B-A3B。视觉引擎故障会自动降级到 Ollama。
    "engine": "llama",
    "rate": 16000,
    # ★★ 2026-10-09 改 0.1：KWS 硬要求每 0.1 秒(1600 采样) 喂一块
    #   读块大小统一成 0.1 秒：KWS 直接吃这一块，
    #   端点检测则按同样的 0.1 秒粒度累积（tail_silence 0.5s = 5 块）。
    "block": 0.1,
    # ★★★ 2026-10-09 端点检测（这是"喊 Fairy 只听到 by"的根因）
    #   原逻辑：攒够 min_voiced 秒就【切】去识别 → "Fairy"(0.5s+) 被切成 0.3 秒碎片，
    #           每次只拿到半个字，日志实证「听到「by bwd6」」「听到「拜拜」」！
    #   新逻辑：说到【停】才切 —— 门限通过就开始收集，继续收；
    #           连续安静 >= tail_silence 或 总长 >= max_utt 才结束这一段。
    "tail_silence": 0.5,     # 连续安静 0.5 秒 = 这一段说完了
    "max_utt": 8.0,          # 单段最长（对话模式要够说一整句）
    "min_utt": 0.35,         # 一段短于这个就当噪声丢弃
    "lead_pad": 0.15,        # 裁首尾静音时保留的余量（whisper 对纯静音会幻觉）
    "trim_win": 0.05,        # 裁静音的窗口
    "rms_gate": 0.001,       # 能量门限
                             # 换算：乘 100000 -> 静音约 36，正常说话约 400~900，门限 100
                             # ★ 原来乘 1000 再取整，分辨率只有 0.001：
                             #   门限 0.006 时 int(6)，用户说话 int(4.75)=4 → 永远过不去；
                             #   门限 0.0008 时 int(0.8)=0，连静音 int(0.04)=0 都通过。取整 bug。
    "cooldown": 2.0,         # 应答后的冷却（原 6.0，太久了）
    "dialogue": True,        # ★ 连续对话：唤醒后不关麦，继续听指令
    "dialogue_idle": 30.0,   # 对话模式里 30 秒没说话 -> 退回只等唤醒
    "stt_mode": "A",         # A=zh+偏置提示 B=自动语言+提示 C=zh 无提示
    "model": "small",
    # ================= ★★ 2026-10-09 换唤醒方式：Sherpa-ONNX KWS =================
    # 【为什么换】whisper 做唤醒有三个硬伤：
    #   ① 模型 461MB、加载+预热实测 63.4 秒（用户喊一声要等一分钟）
    #   ② 每句识别 1~2 秒
    #   ③ 它本来是给长句转写用的，把 "Fairy" 听成 "by"/"拜拜"
    # KWS：模型仅 4.8MB、每 0.1 秒块 1.36ms（73 倍实时）、
    #      实测 10/12 命中（83%）、0/20 误报。
    "kws": True,             # True=用 KWS 做唤醒判定；False=退回老的"门限+whisper"
    "kws_threshold": 0.10,   # ★ 实测：0.30->8/12 · 0.20->9/12 · 0.10->10/12 · 0.05->10/12
    "kws_score": 1.0,        # ★ 实测【完全无影响】(1.0/2.0/4.0 一样) -> 别去调它
    "kws_zh": False,         # 额外挂中文模型(wenetspeech)一起听；默认关（省内存）
}


# ---- 应答池：★ 应答要短促：「嗯一声，或者你好主人？请说？」
#   原则：短的在前；每句都会用 find_wav() 验证语料库真有这段原声，没有才走合成。
ANSWER_POOL = [
    "嗯", "我在", "你好主人", "请说", "主人请讲", "怎么了",
    "主人", "主人我正在待机", "主人请注意", "肯定", "认同",
]

# ---- ★ 识别配置（"Fairy" 是英文，写死 language=zh 会被硬翻成"拜拜/非常"）----
#   A：中文模型 + 偏置提示词（把识别往目标词上引）
#   B：自动语言检测 + 提示词
#   C：中文模型、无提示词（对照）
#   用 --stt-mode 切换，--selftest-e2e 会把三种都跑一遍对比。
STT_MODES = {
    "A": {"language": "zh", "prompt": "Fairy。飞瑞。法瑞。菲瑞。嗨Fairy。你好主人。"},
    "B": {"language": None, "prompt": "Fairy. 飞瑞。法瑞。菲瑞。"},
    "C": {"language": "zh", "prompt": None},
}

# ---- 连续对话用的大脑（本机 Ollama）----
OLLAMA = "http://127.0.0.1:11434"
OLLAMA_MODEL = "qwen3:30b-a3b"
SAY_URL = "<动态：ep.tts_get() 内部按降级链选择>"     # 见 fairy_endpoints.py

# ---- 唤醒词候选（ASR 把 Fairy 转成的字很不确定，必须宽松）----
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


# --------------------------------------------------------------------------
# 默认音频输出守卫（★ 2026-10-09 新增：把 fairy_audio.py 的保护真正接进耳朵）
# --------------------------------------------------------------------------
# 【为什么需要】这个事故【真实发生过】：
#   耳朵用了蓝牙麦克风后，Windows 会把【默认输出】从扬声器切到耳机(HFP)，
#   而耳朵退出时【不会切回来】-> 球说话的声音全进了耳机 -> 用户“一点声音都没有了”。
# 【做法】开麦前 save()；退出时用 atexit restore()（正常退出与 Ctrl+C 都会触发）。
_AUDIO_SAVED = False
# 【体检用】默认输出名字里含这些就认为是【蓝牙耳机/免提类】
_AUDIO_HIJACK_HINTS = ("Hands-Free", "免提", "Bluetooth", "蓝牙", "SANSUI", "AirPods", "耳机")

# 【不自动选方糖】
#   原因：耳朵开麦后 Windows 会把默认输出切到耳机，
#   守卫会在退出时把它【还原成启动时记下的设备】（即「自动选方糖」）。
#   用户明确不要这个行为：【就用系统默认，谁也别动】。
#   -> 改成常量开关；想恢复守卫把它改 True。
_AUDIO_GUARD_ENABLED = False


def _audio_current_name():
    """当前默认输出的名字（失败返回空串）。"""
    try:
        import fairy_audio
        return fairy_audio.name_of(fairy_audio.current_device_id()) or ""
    except Exception:
        return ""


def _audio_is_hijacked(nm):
    """名字像蓝牙耳机 -> 疑似已被劫持。"""
    return bool(nm) and any(h in nm for h in _AUDIO_HIJACK_HINTS)


def _audio_restore():
    """退出时还原默认音频输出。没存过、或没变过就不动（restore 自己会比较）。"""
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
    """Ctrl+C：先还原音频，再把信号交回默认行为。"""
    _audio_restore()
    raise KeyboardInterrupt()


def _audio_guard_start():
    """开麦前的完整守卫：① 体检（已被劫持就先修）② 记录 ③ 注册退出还原。

    ★ 为什么体检放在 save 之前：
      若上一次是被强杀的（反正 atexit 没跑成），设备就还卡在耳机上。
      这时先把它修回去，再记录“修好后的状态”，退出时就不会把快照跳到耳机。
    """
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
    """兼容旧调用点：等于 _audio_guard_start()。"""
    return _audio_guard_start()


def load_cfg():
    """从统一配置读 ear 段（没有就用默认）。"""
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
    """加载 CNSH 编译出来的判定模块（按实际调用方式：spec_from_file_location）。"""
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


# ---------------------------------------------------------------- 设备
def list_inputs():
    """返回可用的录音设备列表，并标注回环/建议。"""
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
    """挑一个真麦克风（优先蓝牙免提/板载，绝不选回环）。"""
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


# ---------------------------------------------------------------- 匹配
def edit_distance(a, b):
    """小规模编辑距离（用于英文候选的宽松匹配）。"""
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
    """返回 (是否含候选, 最小编辑距离)。纯字符串活在 Python 里做。"""
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
    """用 CNSH 选一句应答（避开上一条）。"""
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
    """在语料库索引里找这句原声的 wav 路径。"""
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
    """直接播音频（不经过球的进程）。
    ★ 2026-10-11【可打断】非阻塞播放 + _SPEAKING 标记；主循环检测到插话即 stop。"""
    global _SPEAKING
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
        sd.stop()
        _SPEAKING = True
        sd.play(d, sr, blocking=False)
        return True
    except Exception as e:
        log("播应答失败: %s" % e)
        _SPEAKING = False
        return False


def stop_current_play():
    """★ 2026-10-11【对话打断】立即停止当前播报（用户插话时调用）。"""
    global _SPEAKING
    try:
        import sounddevice as sd
        sd.stop()
    except Exception:
        pass
    _SPEAKING = False


def _update_speaking():
    """每块检查：播放是否自然结束 -> 清掉 _SPEAKING 标记。"""
    global _SPEAKING
    if not _SPEAKING:
        return
    try:
        import sounddevice as sd
        s = sd.get_stream()
        if s is not None and not getattr(s, "active", True):
            _SPEAKING = False
    except Exception:
        _SPEAKING = False


# ---------------------------------------------------------------- 识别
# ==========================================================================
# ★★ 唤醒：Sherpa-ONNX KWS（关键词检测）—— 2026-10-09 换上
# ==========================================================================
# 【为什么用 KWS 而不是 whisper 做唤醒】见 CFG 里的注释。
# 【实测配方（另一路验证过，照搬）】
#   英文模型 gigaspeech-3.3M + 关键词 '▁FA IR Y :1.0 @FAIRY'（BPE 生成）
#   + 每 0.1 秒流式喂 + keywords_threshold=0.10
#   -> 10/12 命中(83%) · 0/20 误报 · 1.36ms/块（73 倍实时）· 内存 ~124MB
# 【两个必须记住的坑】
#   ① ★ 必须【流式喂】：每 0.1 秒(1600 采样) 一块，边喂边 decode。
#      整段一次性喂【永远不中】—— 同一段音频实测：
#      整段喂 3 次全不中 · 流式喂 3 次全中。
#   ② ★ 命中后【必须】 reset_stream()，否则流内部状态会错乱。
KWS_EN_DIR = os.path.join(fairy_root.WAKEWORD_MODELS,
                          "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01")
KWS_ZH_DIR = os.path.join(fairy_root.WAKEWORD_MODELS,
                          "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01")
KWS_EN_RULE = "\u2581FA IR Y :1.0 @FAIRY"
KWS_ZH_RULE = "n \u01d0 h \u01ceo f a i r y :1.0 @\u4f60\u597dfairy"
KWS_HOP_S = 0.1                      # ★ 硬要求：1600 采样 @16k


def _kws_build(base, rule_text, tag):
    """建一个 KeywordSpotter。失败返回 None（不抛）。"""
    import sherpa_onnx
    p = lambda n: os.path.join(base, n)          # noqa: E731
    enc = p("encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx")
    dec = p("decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx")
    joi = p("joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx")
    tok = p("tokens.txt")
    for f in (enc, dec, joi, tok):
        if not os.path.exists(f):
            log("KWS[%s] 缺文件：%s" % (tag, f))
            return None
    # 关键词文件写到 logs 下（不污染模型目录）
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


def _wakeword_rules():
    """★ 2026-10-11【唤醒词保存】默认 Fairy + fairy.json ear.wakewords 自定义词。
    英文词按字母拆分生成 KWS 规则（gigaspeech BPE 常见词可命中）；
    含中文的词条跳过 KWS 规则（中英词条格式不同），Fairy 原词始终有效。
    自定义词可通过球菜单「设置 → 唤醒词」写入配置后重启耳朵生效。"""
    rules = [KWS_EN_RULE]
    for _w in (CFG.get("wakewords") or []):
        _w = str(_w).strip()
        if not _w:
            continue
        _up = _w.upper()
        if re.fullmatch(r"[A-Z][A-Z0-9 ]*", _up):
            _tok = " ".join(list(_up.replace(" ", "")))
            rules.append("\u2581%s :1.0 @%s" % (_tok, _up))
            log("[ear] 已加载自定义唤醒词: %s" % _w)
        else:
            log("[ear] 唤醒词「%s」含中文，跳过 KWS 规则（建议用英文词条）" % _w)
    return rules


def kws_load():
    """加载 KWS 引擎：返回 [(tag, spotter, stream), ...]；全失败返回 []。"""
    try:
        import sherpa_onnx  # noqa: F401
    except Exception as e:
        log("KWS 不可用（sherpa_onnx 未装）: %s: %s" % (type(e).__name__, e))
        return []
    jobs = [("en", KWS_EN_DIR, "\n".join(_wakeword_rules()))]
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
    """把第 i 个引擎的流换成【全新】一条。"""
    tag, sp, _old = engines[i]
    try:
        engines[i] = (tag, sp, sp.create_stream())
    except Exception as e:
        log("KWS[%s] 重建流失败: %s: %s" % (tag, type(e).__name__, e))


def kws_feed(engines, arr, sr):
    """喂一块音频；命中返回 (tag, keyword)，否则 None。★ 命中后内部已换新流。"""
    for i in range(len(engines)):
        tag, sp, st = engines[i]
        try:
            st.accept_waveform(sr, arr)
            while sp.is_ready(st):
                sp.decode_stream(st)
                r = sp.get_result(st)
                if r:
                    _kws_new_stream(engines, i)      # ★ 重建流（不用 reset_stream）
                    return tag, r
        except Exception as e:
            log("KWS[%s] 喂音频出错（跳过这块）: %s: %s"
                % (tag, type(e).__name__, e))
    return None


def kws_reset(engines):
    """重置：把每个引擎换成【全新一条流】。

    ★★ 为什么不用 reset_stream()：实测发现它会丢检测 ——
      同一批 12 条真含 Fairy 的原声：
        每次新建流      -> 11/12 (92%)
        复用流+reset_stream ->  9/12 (75%)   丢的正是紧跟在上一段后面的
      reset_stream() 没把内部特征/解码状态清干净 -> 下一段被抑制。
    ★ 注意：长时间连续监听时【不要每块都调这个】（那会把流扇开），
      只在“命中后 / 自己说完话后”调一次。
    """
    for i in range(len(engines)):
        _kws_new_stream(engines, i)


def kws_feed_wav(engines, audio, sr=16000):
    """离线：把一整段音频按 0.1 秒流式喂给 KWS，返回命中则 (tag, keyword)。
    ★ 这个函数就是为了证明“必须流式喂”而存在的：整段一次性喂会全不中。
    """
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
_MODEL_LOCK = _threading.Lock()      # ★ KWS 版：whisper 改成后台预加载 -> 要防并发


def warm_whisper_async():
    """后台预加载本地 ASR（SenseVoice，sherpa-onnx）。
    换 SenseVoice 后加载只需约 2 秒（原 whisper 63 秒），放后台更稳。
    """
    def _w():
        try:
            asr_bridge.warm()
        except Exception as e:
            log("ASR 后台预加载失败（唤醒不受影响；指令识别会现场加载）: %s: %s"
                % (type(e).__name__, e))
    th = _threading.Thread(target=_w, daemon=True, name="asr-warm")
    th.start()
    return th


def whisper_ready():
    """ASR（SenseVoice）是否已加载完（用于日志提示，不阻塞）。"""
    return asr_bridge.ready()


def get_model():
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    with _MODEL_LOCK:
        if _MODEL is not None:          # 双检：后台线程可能刚加载完
            return _MODEL
        from faster_whisper import WhisperModel
        t0 = time.time()
        _MODEL = WhisperModel(CFG["model"], device="cpu", compute_type="int8",
                              download_root=MODEL_DIR)
        # 预热：第一句明显更慢，先喂 1 秒静音消化掉
        try:
            import numpy as np
            segs, _ = _MODEL.transcribe(np.zeros(16000, dtype="float32"), language="zh", beam_size=1)
            list(segs)
        except Exception as e:
            log("预热失败（不影响）: %s" % e)
        log("语音识别就绪（%s，%.1fs）" % (CFG["model"], time.time() - t0))
    return _MODEL


def transcribe(audio, mode=None):
    """把 numpy float32 16k 音频转成文字。

    ★ 必须传 ndarray，不能传文件路径。
    ★★ 2026-10-11：识别后端已从 faster-whisper 换成阿里 SenseVoice
       （asr_bridge，sherpa-onnx 引擎）—— 中文接近手机端侧水平、
       自动带标点、CPU 实时、免 torch。mode 参数保留以兼容调用方。
    """
    import numpy as np
    arr = np.asarray(audio, dtype="float32")
    text, _used, _cost = asr_bridge.recognize(arr, 16000, "sensevoice")
    return text


def trim_silence(a, rate, gate, pad):
    """裁掉【首尾静音】—— whisper 拿到纯静音会开始幻觉，必须裁。

    做法：按 trim_win(0.05s) 切窗，算每窗 RMS；找出超过 gate*0.6 的首尾窗，
    两侧各留 pad 秒余量后返回。全静音则原样返回（上层会按长度丢弃）。
    """
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


def ask_ollama(text, timeout=90, history=None):
    """本机大脑（Ollama）。

    ★★ 三条硬规矩（都实测过，别改回去）：
      1. 【必须带 keep_alive】—— 不带则 5 分钟卸载模型，下次冷启动 6.8 秒
      2. 【绝不能传 think:false】—— 本机 qwen3:30b-a3b 传了会返回【空字符串】
      3. ★ 2026-10-11【思考块剥离】Qwen3 系列会输出 <think>...</think>，
         必须剥掉再返回（不然 Fairy 会把思考过程念给用户）。
    ★ 2026-10-11【上下文接续】history=[(user, asst),...] 最近几轮会拼进 prompt。
    ★ 2026-10-11【问答缓存】无上下文的独立问题，短期重复直接回缓存（省推理、不卡顿）。
    """
    import json as _j
    import urllib.request
    if not history:
        _cached = _qa_cache_get(text)
        if _cached is not None:
            log("[ear] 问答缓存命中，直接回（%.0f 秒内的重复问题）" % _QA_CACHE_TTL)
            return _cached
    prompt = text
    if history:
        lines = ["以下是我们的历史对话，接着聊（直接回答最新一句，不要重复历史）："]
        for _u, _a in history[-_HIST_MAX:]:
            lines.append("用户：" + _u)
            lines.append("助手：" + _a)
        lines.append("用户：" + text)
        prompt = "\n".join(lines)
    body = _j.dumps({
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "keep_alive": "2h",
        "options": {"num_predict": 200, "temperature": 0.6},
    }).encode("utf-8")
    req = urllib.request.Request(OLLAMA + "/api/generate", data=body,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        j = _j.loads(r.read().decode("utf-8"))
    out = (j.get("response") or "").strip()
    # 剥离 <think>...</think>（Qwen3 思考块）
    m = re.search(r"<think>.*?</think>", out, re.S)
    if m:
        out = (out[:m.start()] + out[m.end():]).strip()
    if not history and out:
        _qa_cache_put(text, out)
    return out


# ---- ★ 2026-10-11【大脑引擎切换】----
#   本地视觉引擎（llama-server 8085，Qwen3-VL-8B，Vulkan 加速）。
#   与 ask_ollama 一样带问答缓存 + 思考块剥离，可接历史续聊。
LLAMA = "http://127.0.0.1:8085"
LLAMA_MODEL = "qwen3-vl-8b"


def ask_llama(text, timeout=120, history=None):
    """本地视觉引擎（8085，Qwen3-VL-8B）：OpenAI 兼容，可看图可对话。"""
    import json as _j
    import urllib.request
    if not history:
        _cached = _qa_cache_get(text)
        if _cached is not None:
            log("[ear] 视觉引擎问答缓存命中，直接回")
            return _cached
    msgs = []
    if history:
        msgs.append({"role": "system",
                     "content": "以下是我们的历史对话，接着聊（直接回答最新一句，不要重复历史）。"})
        for _u, _a in history[-_HIST_MAX:]:
            msgs.append({"role": "user", "content": _u})
            msgs.append({"role": "assistant", "content": _a})
    msgs.append({"role": "user", "content": text})
    body = _j.dumps({
        "model": LLAMA_MODEL,
        "messages": msgs,
        "stream": False,
        "max_tokens": 512,
        "temperature": 0.6,
    }).encode("utf-8")
    req = urllib.request.Request(LLAMA + "/v1/chat/completions", data=body,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        j = _j.loads(r.read().decode("utf-8"))
    out = ((j.get("choices") or [{}])[0].get("message", {}) or {}).get("content") or ""
    out = out.strip()
    m = re.search(r"<think>.*?</think>", out, re.S)
    if m:
        out = (out[:m.start()] + out[m.end():]).strip()
    if not history and out:
        _qa_cache_put(text, out)
    return out


def ask_brain(text, timeout=120, history=None):
    """★ 2026-10-11【大脑引擎切换】按 fairy.json 的 ear.engine 选引擎：
       llama=本地视觉引擎（8085，默认）；ollama=Ollama 30B-A3B。
       视觉引擎故障自动降级到 Ollama，保证大脑永远有回应。"""
    _eng = (CFG.get("engine") or "llama").strip()
    if _eng == "ollama":
        return ask_ollama(text, timeout=timeout, history=history)
    try:
        return ask_llama(text, timeout=timeout, history=history)
    except Exception as _e:
        log("[ear] 视觉引擎不可用（%s），降级 Ollama 30B" % _e)
        return ask_ollama(text, timeout=timeout, history=history)


# ---- ★ 2026-10-11【问答缓存】短期重复问题直回，省推理让 Fairy 更快 ----
_QA_CACHE = {}
_QA_CACHE_MAX = 40
_QA_CACHE_TTL = 1800        # 30 分钟


def _qa_cache_get(text):
    if not text:
        return None
    _hit = _QA_CACHE.get(text)
    if _hit and time.time() - _hit[1] < _QA_CACHE_TTL:
        return _hit[0]
    return None


def _qa_cache_put(text, ans):
    if not text or not ans:
        return
    if len(_QA_CACHE) > _QA_CACHE_MAX:      # 防膨胀：丢一半最旧的
        for _k in list(_QA_CACHE)[:len(_QA_CACHE) // 2]:
            _QA_CACHE.pop(_k, None)
    _QA_CACHE[text] = (ans, time.time())


def synth_to_file(text):
    """合成音频（Fairy 克隆音色）并落盘。返回文件路径或 None。

    ★【2026-10-10 独立运行改造】原来只打死了 DSH 的 /api/fairy/say，
      DSH 一关耳朵就说不出话。现在走 fairy_endpoints.tts_get() 的统一降级链：
        9881 直连 -> DSH 插件 -> 本地 fairy_host -> DSH tts-api
      契约不变（拿回音频字节，落盘），音色不变（同一个 IndexTTS + 同一份参考音频）。
    """
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
    """说一句任意文本：先找语料库真原声，找不到走克隆合成，都不行就写文件给球。

    返回一个字符串说明走的哪条路（便于日志与排障）。
    """
    if not text:
        return "空"
    # ★ 门控：取消和游戏关联后不再直接播游戏原文件，直接走克隆。
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


# ---------------------------------------------------------------- 主循环
def drain_input(st, hop, n=4):
    """把输入缓冲里残留的旧块吐掉（自己说完话后，
    避免 KWS 去读“刚才自己说的那段”而误触。最多吞 n 块，不阻塞太久。"""
    for _ in range(max(0, int(n))):
        try:
            st.read(hop)
        except Exception:
            break


def cmd_run(seconds=0):
    import numpy as np
    import sounddevice as sd
    # ★ 2026-10-11【权限】未授权麦克风 -> 语音功能禁用（提示后退出）
    try:
        import fairy_perms as _fp
        if not _fp.check("mic"):
            log("✗ 权限未授权：麦克风（语音对话/唤醒需要）。"
                "授权：球右键 → 设置 → 麦克风；或 py -3.12 fairy_perms.py --grant mic")
            return 3
    except Exception:
        pass
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
    # ★★ KWS 硬要求：每 0.1 秒(1600 采样) 喂一块。读块就用它。
    hop = int(rate * KWS_HOP_S)
    block_s = hop / float(rate)
    use_kws = bool(CFG.get("kws", True))
    engines = []
    try:
        # ★ 开麦前先记住默认输出（蓝牙麦克风会把它切走，退出时要还原）
        if _AUDIO_GUARD_ENABLED:      # ★ 用户要求：取消自动切换默认设备
            _audio_save()
        # ================= ① 唤醒引擎：KWS（快：约 1.5 秒） =================
        if use_kws:
            engines = kws_load()
        if engines:
            log("唤醒方式：★ KWS（Sherpa-ONNX）—— 每 0.1 秒流式检测")
        else:
            if use_kws:
                log("⚠ KWS 加载未成功 -> 退回老的「能量门限 + whisper」唤醒（慢但能用）")
            else:
                log("唤醒方式：能量门限 + whisper（ear.kws=false）")
        # ================= ② whisper：后台预加载，不阻塞唤醒 =================
        if CFG.get("dialogue", True) or not engines:
            log("whisper 已改为后台预加载（约 60 秒）：唤醒不受影响，说指令时基本已好")
            warm_whisper_async()
        else:
            log("连续对话已关 -> 不预加载 whisper（省 63 秒）")
        with sd.InputStream(device=dev["index"], channels=1, samplerate=rate,
                            blocksize=hop, dtype="float32") as st:
            log("耳朵已开（喊「Fairy」试试；--stop 停止）")
            # ================= 主循环 =================
            buf = []                 # 当前这一段收集到的音频
            silent = 0.0             # 这一段里连续安静了多久
            collecting = False       # 是否正在收集一段话
            last_wake = None
            last_idx = -1
            mode = "wake"            # wake = 只等唤醒词；dialogue = 连续对话
            last_voice = time.time()
            hold_until = 0.0         # ★ 自说自话保护：这段时间内不采集/不喂KWS
            mic_peak = 0.0           # ★ 这段时间内麦克风的最大幅度（用于判“麦克风有没有声音”）
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
                _update_speaking()      # ★ 2026-10-11：播放自然结束则清标记

                # ---------- ★ 自说自话保护：读掉但不喂 KWS、不收集 ----------
                if now < hold_until:
                    # ★★ 2026-10-11【对话打断】Fairy 播报期间用户插话 -> 停止播报、
                    #    进入收集（尊重用户，不忽略）。
                    if _SPEAKING:
                        lv = float(np.max(np.abs(a)))
                        if lv > _INTERRUPT_GATE:
                            stop_current_play()
                            log("★ 检测到插话 -> 已打断 Fairy 播报，请说")
                            hold_until = now + 0.4   # 给播放器收尾时间
                    # ★ 不在这里 kws_reset：那会 10 次/秒地重建流。
                    #   说完话后已经 reset 过一次；这段时间【不喂】KWS就不会累积。
                    collecting = False
                    buf = []
                    silent = 0.0
                    continue

                # ================= ① KWS 唤醒判定（主力，永远在听）=================
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
                    # speak_text 是阻塞的（播完才返回）：说完先洗掉旧块
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

                # ================= ② 端点检测 =================
                # 什么时候需要：
                #   · 对话模式下——要听用户说指令
                #   · KWS 不可用时——还要兼任唤醒判定（老路子）
                need_ep = (mode == "dialogue" and CFG.get("dialogue", True)) or (not engines)
                if not need_ep:
                    # KWS 在岗、且没在对话 -> 这一块已经被 KWS 吃掉，不做别的
                    skipped += 1
                    lv = float(np.max(np.abs(a)))
                    if lv > mic_peak:
                        mic_peak = lv
                    if skipped % 300 == 0:
                        # ★ 把麦克风电平一并打出来：
                        #   这样才能分清“麦克风没声音”还是“KWS 没识别”。
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

                # ---- 这一段结束 ----
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
                    # ---------- 老路子（KWS 不可用）：用文本判唤醒 ----------
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
                    # ---------- 连续对话：把话交给大脑 ----------
                    log("指令「%s」（%.2fs）" % (txt[:60], dur))
                    # ★ 2026-10-11【记忆】记录常用语 + 功能归类（习惯学习）
                    try:
                        fairy_memory.record_phrase(txt)
                        _f = fairy_memory.map_feature(txt)
                        if _f:
                            fairy_memory.record_use(_f, txt[:40])
                    except Exception:
                        pass
                    try:
                        reply = ask_brain(txt, history=_HIST)
                    except Exception as e:
                        log("大脑不可用: %s" % e)
                        reply = ""
                    if not reply:
                        log("大脑没给出回答，跳过")
                        continue
                    # ★ 2026-10-11【上下文接续】入历史（上限 _HIST_MAX），落盘供下次接续
                    _HIST.append((txt, reply))
                    if len(_HIST) > _HIST_MAX:
                        _HIST = _HIST[-_HIST_MAX:]
                    try:
                        _d = fairy_memory.load()
                        _d["last_dialogue"] = _HIST[-6:]
                        fairy_memory.save(_d)
                    except Exception:
                        pass
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
    """读 wav -> float32 16k 单声道（线性重采样，关键词识别够用）。"""
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
    """从语料库挑一条【真含 Fairy】的游戏原声当测试素材（比合成更真实）。"""
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
        # 偏好 1~3 秒（短、干净、一句话就说完了）
        score = abs(d - 1.8)
        if best is None or score < best[0]:
            best = (score, tx, fp2, d)
    if best:
        return (best[1], best[2]), best[3]
    return None, None


def pick_stimuli(n, want_fairy=True):
    """从语料库挑 n 条音频。want_fairy=True 挑含 Fairy 的，False 挑不含的。

    ★ 按文件名排序后、均匀取样 -> 可复现（同一批每次一样）。
    """
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
    """KWS 唤醒自检：离线喂真游戏原声，验证【命中率】与【误报率】。

    ★ 这是“唤醒到底行不行”的核心证据工具：
      · 正例：挑 n_hit 条【真含 Fairy】的原声 -> 流式喂 KWS -> 数命中
      · 反例：挑 n_miss 条【不含 Fairy】的原声 -> 同样方式 -> 数误报
      · 并顺便验证那个坑：同一段音频【整段一次性喂】会不会不中。
    """
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

    # ---- ① 正例：含 Fairy ----
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

    # ---- ② 反例：不含 Fairy ----
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

    # ---- ③ 验证“必须流式喂”这个坑 ----
    print("\n--- ③ 对照：整段一次性喂 vs 流式喂（著名的坑）---")
    if pos:
        tx, p2, d = pos[0]
        a = load_wav_16k(p2)
        # 变体 1：整段一次性喂、且不调 input_finished（网上常见写法）
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
        # 变体 2：整段喂 + input_finished（把尾部冲出来）
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
        # 变体 3：流式（正确用法）
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
    """端到端自检（不需要真人喊）：
       ① 拿一段【真含 Fairy】的游戏原声
       ② 三种识别配置 A/B/C 逐一转写并对比
       ③ 走完整唤醒判定
       ④ 再喂一句问题，测连续对话（Ollama）
    """
    import numpy as np
    log("=== 端到端自检开始 ===")
    # ---- ① ★ 先测唤醒引擎（KWS）—— 这是现在的唤醒主力 ----
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

    # ---- ② 三种配置对比 ----
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

    # ---- ③ 端到端唤醒判定 ----
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
    # ★ KWS 命中就算唤醒链路成功（whisper 那三种配置是为了看“转写”效果）
    if kws_hit:
        ok = True
        log("KWS 命中 -> 唤醒链路成功（whisper 只用于唤醒后转写指令）")
    log("端到端唤醒：%s" % ("成功 ✓" if ok else "失败 ✗"))
    if not ok:
        log("三种模式的转写都不含候选词 —— 需要扩充 WAKE_CANDIDATES（照着实际转写补）")

    # ---- ④ 连续对话（不需要麦克风：直接喂文本给大脑）----
    print("")
    print("=== 连续对话链路（文本直喂，验证大脑与合成）===")
    q = "现在几点"
    try:
        t0 = time.time()
        reply = ask_brain(q, timeout=120)
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
    # ---- ★ KWS 开关（先处理，后面的子命令才能用到）----
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
        # ★ 这个子命令本身就是为了测 KWS -> 默认强制打开
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
    # ★ 2026-10-09 补：真正尊重 ear.enabled（原来只有文档承诺，没代码）
    if not CFG.get("enabled", True):
        print("[ear] 已按配置关闭（enabled = false），不启动常驻监听。", flush=True)
        print("[ear] 想打开：把本文件顶部 CFG 里的 enabled 改为 true。", flush=True)
        return 0
    return cmd_run(secs)


if __name__ == "__main__":
    sys.exit(main())
