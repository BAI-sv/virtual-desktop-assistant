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
import fairy_mic_source                  # ★ 2026-10-12：兜底麦克风源（系统输入引擎异常时走 WDM-KS rec 轮询）

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
OW = 1.0                                             # overwrite 放最后

# ★★ 2026-10-11【对话打断 + 上下文接续】
_SPEAKING = False            # Fairy 是否正在播报（主循环据此允许用户插话打断）
_HIST = []                   # 本轮对话历史 [(user, asst), ...]（最近 8 轮）
_HIST_MAX = 8
_INTERRUPT_GATE = 0.02       # 打断门限：播放中麦克风 RMS 超过它视为插话

# ★ 2026-10-12【自适应底噪门限】部分 USB 麦带恒定底噪（实测 RMS≈0.2、峰值≈0.32），
#   固定门限（rms_gate=0.001、打断 0.02）会被噪声永远顶穿：
#   -> 一直误判"有人声"收集 8 秒噪声、转写出乱码，还把 Fairy 的播报反复打断。
#   改为跟踪滚动底噪（低百分位），人声/插话必须明显高于底噪才触发。
_ADAPT_WIN = 40              # 滚动窗块数（0.1s/块 → 4 秒）
_ADAPT_PCT = 25              # 底噪取第 25 百分位（抗突发噪声）
_ADAPT_RATIO = 1.5           # 触发倍数：超过底噪 1.5 倍才算"有声音"
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
    "tail_silence": 0.5,     # ★ 2026-10-12【说完即答】停顿 0.5 秒 = 这一段说完了（说完才回复）
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
    "dialogue_idle": 8.0,    # 对话模式里 8 秒没说话 -> 退回只等唤醒
    # ★ 2026-10-12【按键说话】按住按键说话、松开即识别并回复。
    #   key: 鼠标侧键 xbutton1(前进)/xbutton2(后退)；也可用键盘单键名(如 f2)
    #   alt_key: 键盘组合快捷键，如 ctrl+alt+v / ctrl+shift+f
    "ptt": {"enabled": True, "key": "xbutton1", "alt_key": "ctrl+alt+v"},
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
    # ★ 2026-10-20【再降阈值】这台机器 USB 麦实测峰值只有 0.05~0.13
    #   （正常麦克风应该在 0.3 以上）。0.04 阈值下 KWS 一次都没命中过。
    #   代价是误报率上升，但「喊了不理」比「偶尔误唤醒」糟糕得多。
    #   如果之后误报太多，把它调回 0.02~0.04。
    #   历史：0.06（2026-10-11 太苛刻）-> 0.04（2026-10-12）-> 0.01（今天）
    "kws_threshold": 0.01,
    "kws_score": 1.0,        # ★ 实测【完全无影响】(1.0/2.0/4.0 一样) -> 别去调它
    "kws_zh": True,          # 挂中文模型(wenetspeech)一起听；用户喊"Fairy"中文口音更易命中
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
# ★ 2026-10-12【模型名修正】实际安装 tag 是 qwen3-30b-a3b:latest（不是 qwen3:30b-a3b），
#   名字对不上 ollama 会直接报 "model not found"，Fairy 就"不搭理"。
OLLAMA_MODEL = "qwen3-30b-a3b"
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
                if k not in CFG:
                    continue
                if isinstance(v, dict) and isinstance(CFG[k], dict):
                    CFG[k].update(v)   # ★ 2026-10-12：嵌套配置（如 ptt）浅合并，可只改单个键
                else:
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
    """返回可用的录音设备列表，并标注回环/建议。
    ★ 2026-10-12：改用 fairy_mic_source（sounddevice 枚举 WDM-KS 输入），
    不再依赖 PyAudio/MME —— 系统输入引擎异常时 MME 枚举为空。"""
    return fairy_mic_source.list_inputs()


def pick_input(devs):
    """挑一个真麦克风。
    ★ 2026-10-12：用 fairy_mic_source.pick_input —— 逐个 rec 试开、
    选信号最强的可用设备（麦克风/线路优先，蓝牙免提最后）。"""
    return fairy_mic_source.pick_input(devs)


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
    ★ 2026-10-11【可打断】非阻塞播放 + _SPEAKING 标记；主循环检测到插话即 stop。
    ★ 2026-10-12：支持 mp3（DSH fallback 返回 mp3 时用 MCI 播）。"""
    global _SPEAKING
    try:
        # ---- mp3（ID3 / 0xFFEx）用 Windows MCI 播 ----
        _is_mp3 = False
        try:
            with open(path, "rb") as _f:
                _h = _f.read(3)
            _is_mp3 = (_h[:3] == b"ID3"
                       or (len(_h) > 1 and _h[0] == 0xFF and (_h[1] & 0xE0) == 0xE0))
        except Exception:
            pass
        if _is_mp3:
            import ctypes
            _mci = ctypes.windll.winmm.mciSendStringW
            _alias = "fairy_mp3_%d" % (id(path) % 100000)
            if _mci('open "%s" type mpegvideo alias %s' % (path, _alias),
                    None, 0, None) == 0:
                _SPEAKING = True
                _mci("play %s" % _alias, None, 0, None)
                return True
            _SPEAKING = False
            return False
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
    """★ 2026-10-11【对话打断】立即停止当前播报（用户插话时调用）。
    ★ 2026-10-12：同时停掉 MCI 的 mp3 播放。"""
    global _SPEAKING
    try:
        import ctypes
        _mci = ctypes.windll.winmm.mciSendStringW
        _mci("close all", None, 0, None)      # 停 mp3（MCI）
    except Exception:
        pass
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
KWS_ZH_RULE = "n \u01d0 h \u01ceo f a i r y :1.0 @\u4f60\u597dfairy\nf a i r y :1.0 @fairy"
KWS_HOP_S = 0.1                      # ★ 硬要求：1600 采样 @16k
# ★ 2026-10-12【唤醒灵敏度】USB/免提麦克风信号偏弱（静默峰值仅 0.06~0.2），
#   喂 KWS 前放大，提升"喊 Fairy"的命中率；打断门限仍用原始信号。
KWS_GAIN = float(os.environ.get("FAIRY_KWS_GAIN", "16.0"))


def kws_amp(a):
    """给 KWS 喂音频前做电平适配。

    ★★ 2026-10-18【找到「一次都没唤醒过」的真因】
      `FairyMicSource.read()` 输出归一化到 -1~1 的 float32。
      旧实现固定 `a * 16`，本意是把"说话峰值 0.03"抬到 0.48。
      但用户这台机器的**风扇噪音底噪就有 0.35**：
          0.35 × 16 = 5.6  -> 全部 clip 成 ±1 的方波
      整段音频变方波后，人声的频谱特征被彻底毁掉，KWS 自然一个都不认。
      实测（真实风扇录音 + 合成 Fairy，SNR 从 +10 到 -15dB）：
          固定 ×16 + 限幅    -> 0/6 命中
          按峰值归一到 0.5   -> 命中回来了
      所以改成**按当前块峰值归一到目标电平**，且放大倍数不超过 KWS_GAIN：
        · 安静（peak 0.03） -> 仍按 ×16 抬起来（保住原来的灵敏度设计）
        · 吵（peak 0.35）   -> 只放大 1.4 倍，不削波，保住人声
    """
    import numpy as _np
    try:
        x = _np.asarray(a, dtype="float32")
        peak = float(_np.max(_np.abs(x))) if x.size else 0.0
        if peak < 1e-5:
            return x
        target = 0.5
        gain = min(KWS_GAIN, target / peak)
        return _np.clip(x * gain, -1.0, 1.0)
    except Exception:
        return a



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


_PTT = {"down": False, "was": False}   # down=按键正按住；was=本段由按键触发


def ptt_listener():
    """★ 2026-10-12【按键说话】全局热键监听（后台线程）：
    按住鼠标侧键（xbutton1/xbutton2）或键盘组合键说话，松开即识别回复。
    只改 _PTT 标志，主循环每块轮询。"""
    try:
        from pynput import keyboard as _kb, mouse as _ms
    except Exception:
        log("⚠ 按键说话不可用（未安装 pynput）—— 已跳过")
        return
    _cfg = CFG.get("ptt") or {}
    if not _cfg.get("enabled", True):
        return
    _key = (_cfg.get("key") or "xbutton1").lower().strip()
    _alt = [x.strip().lower() for x in (_cfg.get("alt_key") or "ctrl+alt+v").split("+") if x.strip()]
    _xmap = {"xbutton1": ("x1", "button.x1"), "xbutton2": ("x2", "button.x2")}
    _mods = set()

    def _is_key(n):
        n = (n or "").lower()
        if _key in _xmap:
            return n in _xmap[_key]
        return n == _key

    def _press(key):
        try:
            n = getattr(key, "name", None)
            if n is None and hasattr(key, "char"):
                n = key.char
            n = (n or "").lower()
            if n in ("ctrl", "alt", "shift"):
                _mods.add(n)
                return
            if _is_key(n):
                _PTT["down"] = True
                log("★ 按键说话：按住（%s）" % (key))
            elif n and n in _alt and _mods >= {x for x in _alt if x not in ("ctrl", "alt", "shift")}:
                _PTT["down"] = True
                log("★ 按键说话：按住（%s）" % "+".join(_alt))
        except Exception:
            pass

    def _release(key):
        try:
            n = getattr(key, "name", None)
            if n is None and hasattr(key, "char"):
                n = key.char
            n = (n or "").lower()
            if n in ("ctrl", "alt", "shift"):
                _mods.discard(n)
                return
            if _PTT["down"] and (_is_key(n) or (n and n in _alt)):
                _PTT["down"] = False
                log("★ 按键说话：松开 -> 识别并回复")
        except Exception:
            pass

    def _m_click(_x, _y, _b, _p):
        try:
            _n = getattr(_b, "name", None) or str(_b).split(".")[-1]
            if _is_key(_n):
                if _p:
                    _PTT["down"] = True
                    log("★ 按键说话：按住（鼠标 %s）" % _n)
                else:
                    _PTT["down"] = False
                    log("★ 按键说话：松开 -> 识别并回复")
        except Exception:
            pass
    try:
        _kl = _kb.Listener(on_press=_press, on_release=_release)
        _ml = _ms.Listener(on_click=_m_click)
        _kl.start()
        _ml.start()
        _kl.join()
        _ml.join()
    except Exception as _e:
        log("按键说话监听退出: %s" % _e)


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


_KWS_CALLS = 0
_KWS_PEAK_MAX = 0.0


def kws_feed(engines, arr, sr):
    """喂一块音频；命中返回 (tag, keyword)，否则 None。★ 命中后内部已换新流。"""
    global _KWS_CALLS, _KWS_PEAK_MAX
    _KWS_CALLS += 1
    try:
        # 记录喂给 KWS 的【实际电平】—— 这是排查「喊了不唤醒」的关键指标。
        # 注意：记的是 kws_amp 归一化之后的峰值，不是原始麦克风峰值。
        _m = max(abs(float(x)) for x in arr)
        if _m > _KWS_PEAK_MAX:
            _KWS_PEAK_MAX = _m
    except Exception:
        pass
    if os.environ.get("FAIRY_KWS_DEBUG") and (_KWS_CALLS % 300 == 0):
        log("[KWS调试] 已喂 %d 块（约 %.0f 秒）· 期间最大电平 %.3f"
            % (_KWS_CALLS, _KWS_CALLS * KWS_HOP_S, _KWS_PEAK_MAX))
        _KWS_PEAK_MAX = 0.0
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




def warm_asr_async():
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


def asr_ready():
    """ASR（SenseVoice）是否已加载完（用于日志提示，不阻塞）。"""
    return asr_bridge.ready()




def _denoise(arr, rate=16000, thr=2.0):
    """★ 2026-10-12【谱减降噪】scipy STFT 谱减法：
    用整段能量最低的 15% 帧估计噪声谱，低于阈值倍数的频率成分衰减，
    去掉耳机底噪/电流干扰后还原波形。纯 CPU，转写前调用。
    """
    import numpy as np
    try:
        from scipy.signal import stft, istft
    except Exception:
        return arr
    _n = len(arr)
    if _n < rate // 2:
        return arr
    _seg = 512
    _hop = _seg // 2
    _f, _t, _Z = stft(arr, fs=rate, nperseg=_seg, noverlap=_hop)
    _m = np.abs(_Z)
    _e = np.mean(_m ** 2, axis=0)
    _nb = max(1, int(_e.shape[0] * 0.15))
    _idx = np.argsort(_e)[:_nb]
    _noise = np.median(_m[:, _idx], axis=1, keepdims=True) + 1e-9
    _mask = _m / _noise
    _g = np.clip((_mask - thr) / (thr * 0.8), 0.0, 1.0)
    _g = np.minimum(_g, 1.0)
    try:
        _g = (np.roll(_g, 1, axis=1) + _g + np.roll(_g, -1, axis=1)) / 3.0
    except Exception:
        pass
    _out = istft(_Z * _g, fs=rate, nperseg=_seg, noverlap=_hop)[1]
    return np.ascontiguousarray(_out[:_n], dtype="float32")


def transcribe(audio, mode=None):
    """把 numpy float32 16k 音频转成文字。

    ★ 必须传 ndarray，不能传文件路径。
    ★★ 2026-10-11：识别后端已从 faster-whisper 换成阿里 SenseVoice
       （asr_bridge，sherpa-onnx 引擎）—— 中文接近手机端侧水平、
       自动带标点、CPU 实时、免 torch。mode 参数保留以兼容调用方。
    ★★ 2026-10-12【识别增益】SenseVoice 对低音量语音容易转成句号/漏字：
       按峰值自动归一化放大（峰值 <0.5 时放大到 ~0.9），噪声由 ASR 自身过滤。
    """
    import numpy as np
    arr = np.asarray(audio, dtype="float32")
    if arr.size == 0:
        return ""
    # ★ 2026-10-12【降噪】耳机麦干扰/底噪先去噪，再做高通与归一化
    arr = _denoise(arr)
    # ★ 2026-10-12【去底噪 + 归一化】麦克风底噪比人声还响时（实测峰值 0.05 vs
    #   人声 RMS 0.036），SenseVoice 只会转成句号。先 FFT 高通去掉低频底噪，
    #   再按峰值归一化放大到 ~0.9，让语音相对噪声突出。
    _n = len(arr)
    if _n > 256:
        try:
            _f = np.fft.rfft(arr)
            _fr = np.fft.rfftfreq(_n, 1.0 / 16000)
            _f[_fr < 120] = 0.0
            arr = np.fft.irfft(_f, _n).astype("float32")
        except Exception:
            pass
    _peak = float(np.max(np.abs(arr))) + 1e-9
    if _peak < 0.5:
        arr = arr * (0.9 / _peak)
    text, _used, _cost = asr_bridge.recognize(arr, 16000, "sensevoice")
    return (text or "").strip()


def trim_silence(a, rate, gate, pad):
    """裁掉【首尾静音】—— ASR 拿到纯静音会开始幻觉，必须裁。

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
    # ★★ 2026-10-20【② 限制回复长度，解决「语音慢」】
    #   IndexTTS 是自回归生成，文本越长合成越慢（30 秒音频 ≈ 26 秒）。
    #   之前没有系统提示，模型经常一次吐三四百字，光合成就要等半分钟。
    #   语音对话本来就不该长篇大论 —— 加硬约束：口语、短、一句话能说完。
    #   512 tokens 也压到 180，双保险。
    msgs.append({"role": "system",
                 "content": "你是语音助手，回答会直接被念出来。"
                            "要求：口语化、一句话说完、不超过 40 个字；"
                            "不要分点、不要列清单、不要 Markdown、不要括号注释。"
                            "需要长内容时只给结论，细节留给屏幕显示。"})
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
        "max_tokens": 180,
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


_VOICE_MODE = None   # None=跟随 fairy.json prefer / local=本地优先 / dsh=网络优先

def _voice_switch_cmd(text):
    """语音命令：'用本地AI'/'用网络AI' 切换问答后端。返回确认话术或 None。"""
    t = (text or "").strip().lower()
    global _VOICE_MODE
    if any(k in t for k in ("用本地", "本地ai", "切换本地", "用本地模型", "本地大脑", "本地回答")):
        _VOICE_MODE = "local"
        return "已切换到本地 AI，本地模型优先，不可用时自动转网络"
    if any(k in t for k in ("用网络", "网络ai", "切换网络", "用网络模型", "在线ai", "联网回答")):
        _VOICE_MODE = "dsh"
        return "已切换到网络 AI，DSH 在线优先"
    if any(k in t for k in ("用自动", "自动ai", "自动选择", "恢复自动")):
        _VOICE_MODE = None
        return "已恢复自动选择"
    return None


def ask_brain(text, timeout=120, history=None):
    """★ 2026-10-12【分层大脑 + DSH 优先】
       prefer=dsh -> 语音文本【实时发 DSH 云端】回答（ep.ask_get：push->同步回复），
                     DSH 失败再回本地引擎；
       其他        -> 本地（llama / ollama）优先，本地全失败自动切在线降级链。"""
    _cmd = _voice_switch_cmd(text)
    if _cmd:
        return _cmd
    _pref = "local"
    try:
        _pref = ((_VOICE_MODE or "") or (ep.prefer() or "") or "local").lower()
    except Exception:
        pass
    if _pref == "dsh":
        # ★ dsh 模式：DSH 云端优先回答（“我说的话实时传给 DSH”的目标链路）
        try:
            _r = ep.ask_get(text, history=history, timeout=timeout)
            if _r:
                _ans, _src = _r
                log("[ear] DSH 云端回答（来源=%s）" % _src)
                return _ans
        except Exception as _e:
            log("[ear] DSH 回答失败（%s）-> 回本地引擎" % _e)
    _eng = (CFG.get("engine") or "llama").strip()
    try:
        if _eng == "ollama":
            return ask_ollama(text, timeout=timeout, history=history)
        return ask_llama(text, timeout=timeout, history=history)
    except Exception as _e:
        log("[ear] 本地引擎不可用（%s），降级 Ollama" % _e)
    try:
        return ask_ollama(text, timeout=timeout, history=history)
    except Exception as _e2:
        log("[ear] 本地模型全部不可用（%s）-> 转在线降级链" % _e2)
    if _pref != "dsh":
        r = ep.ask_get(text, history=history, timeout=timeout)
        if r:
            ans, src = r
            log("[ear] 在线降级回答（来源=%s）" % src)
            return ans
    raise RuntimeError("本地与在线大脑都不可用（%s）" % text[:40])


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


def synth_to_file(text, tag=None):
    """合成音频（Fairy 克隆音色）并落盘。返回文件路径或 None。

    ★【2026-10-10 独立运行改造】原来只打死了 DSH 的 /api/fairy/say，
      DSH 一关耳朵就说不出话。现在走 fairy_endpoints.tts_get() 的统一降级链：
        9881 直连 -> DSH 插件 -> 本地 fairy_host -> DSH tts-api
      契约不变（拿回音频字节，落盘），音色不变（同一个 IndexTTS + 同一份参考音频）。

    ★★ 2026-10-24【全双工 · 分句流式】新增 tag 参数。
      并发合成多句时必须区分文件名，否则后一句会把前一句覆盖掉，
      播放器读到一半文件被改就是一段杂音。
      tag 形如 "_s0"/"_s1"，不传就和原来一样写 ear_say.wav。
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
    p = os.path.join(LOG, "ear_say%s.%s" % (tag or "", "mp3" if is_mp3 else "wav"))
    with open(p, "wb") as f:
        f.write(data)
    print("[ear] 语音=克隆路由(%s|%s) %d 字节"
          % (r.get("voice"), r.get("route"), len(data)), flush=True)
    return p


# ===========================================================================
# ★★ 2026-10-24【全双工 第 1 步】TTS 分句流式
#
# 问题：原来 speak_text 是「整段合成 -> 整段播放」。
#       IndexTTS 是自回归的，文本越长线性越慢（实测 30 秒音频要 26 秒合成），
#       这期间用户什么都听不到 —— 首字延迟 1~3 秒。
#
# 现在：按标点切句，第 1 句合成完立刻播，同时后台合成第 2 句。
#      合成和播放并行，首字延迟降到「第一句的合成时间」。
#
# 并行怎么做的：play_wav 是阻塞的（播完才返回），
#      所以主线程播第 1 句时，worker 线程已经在合成第 2、3 句了。
# ===========================================================================
def _split_for_speech(text, max_len=42, min_len=4):
    """把一句话切成适合逐句合成的小段。

    切分规则（优先级从高到低）：
      1) 句末标点 。！？!?… 后面切
      2) 逗号、分号、顿号 ，；,; 后面切（仅当已经攒够 min_len）
      3) 单段超过 max_len 时硬切
    文本本来就短（<= max_len）就原样返回，避免拆得太碎、语调断裂。
    """
    t = (text or "").strip()
    if not t:
        return []
    if len(t) <= max_len:
        return [t]

    out = []
    buf = ""
    for ch in t:
        buf += ch
        is_end = ch in "。！？!?…"
        is_pause = ch in "，；,;"
        if is_end and len(buf) >= 2:
            out.append(buf.strip())
            buf = ""
        elif is_pause and len(buf) >= min_len and len(buf) < max_len:
            out.append(buf.strip())
            buf = ""
        elif len(buf) >= max_len:
            out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())

    # 去掉纯标点的碎片，避免合成出奇怪的声音
    out = [x for x in out if x and not re.fullmatch(r"[\s\W_]+", x)]
    return out or [t]


def _speak_stream(sentences):
    """分句合成 + 边合边播。返回说明字符串（和 speak_text 的约定一致）。"""
    import queue as _queue

    q = _queue.Queue()

    def _worker():
        for i, s in enumerate(sentences):
            try:
                p = synth_to_file(s, tag="_s%d" % i)
            except Exception as e:
                log("流式合成第 %d 句失败: %s" % (i + 1, e))
                p = None
            q.put((i, p))
        q.put(None)

    th = _threading.Thread(target=_worker, daemon=True, name="tts-stream")
    th.start()

    played = 0
    while True:
        item = q.get()
        if item is None:
            break
        i, p = item
        if not p:
            continue
        try:
            play_wav(p)          # 阻塞：播这句的同时 worker 在合成下一句
            played += 1
        except Exception as e:
            log("播放第 %d 句失败: %s" % (i + 1, e))
    return "流式克隆(%d/%d句)" % (played, len(sentences))


def _autofix_mic():
    """★ 2026-10-12【削波自愈】默认麦克风持续满格(峰值≈1.0)时自动降音量。

    返回 ("lowered", 之前音量, 之后音量) 或 ("still", 当前音量) 或 ("err", 信息)。
    音量 0.4 足够唤醒（ear 会给 KWS 再放大），又不至于被耳机干扰顶到削波。
    """
    try:
        import comtypes
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        from pycaw.constants import CLSID_MMDeviceEnumerator
        from pycaw.api.mmdeviceapi import IMMDeviceEnumerator
        from pycaw.api.endpointvolume import IAudioEndpointVolume
        _enum = comtypes.CoCreateInstance(
            CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_ALL)
        _dev = _enum.GetDefaultAudioEndpoint(1, 0)
        _ev = _dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        _epv = cast(_ev, POINTER(IAudioEndpointVolume))
        _cur = float(_epv.GetMasterVolumeLevelScalar())
        if _cur > 0.5:
            _epv.SetMasterVolumeLevelScalar(0.4, None)
            return ("lowered", _cur, 0.4)
        return ("still", _cur)
    except Exception as _e:
        return ("err", str(_e)[:80])


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
        # ★★ 2026-10-24【全双工 第 1 步】长回答走分句流式：
        #   第 1 句合成完就播，后面边播边合，首字延迟从「整段合成」降到「一句」。
        #   短回答（一句话）没切分收益，仍旧走整段，省一个线程。
        _parts = _split_for_speech(text)
        if len(_parts) > 1:
            return _speak_stream(_parts)
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


def _announce_ready():
    """启动播报：先等 ASR（SenseVoice）真正能转写，才开口报就绪。"""
    _waited = 0
    while _waited < 180:               # 最多等 3 分钟 ASR 就绪
        try:
            if asr_ready():
                break
        except Exception:
            pass
        time.sleep(2)
        _waited += 2
    for _i in range(5):
        try:
            r = speak_text("Fairy 已就绪")
            if r in ("原声", "克隆"):
                log("就绪播报完成（%s，ASR 已就绪）" % r)
                return
        except Exception:
            pass
        time.sleep(5)
    log("就绪播报未完成（tts 未就绪，已放弃）")


# ---------------------------------------------------------------- 主循环
def drain_input(st, hop, n=4):
    """把输入缓冲里残留的旧块吐掉（自己说完话后，
    避免 KWS 去读“刚才自己说的那段”而误触。最多吞 n 块，不阻塞太久。"""
    for _ in range(max(0, int(n))):
        try:
            st.read(hop, exception_on_overflow=False)
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
    # ★ 2026-10-12：改用 fairy_mic_source 试开（WDM-KS rec 直读），
    #   不再依赖 PyAudio/MME 持续流（系统输入引擎异常时全打不开）。
    _ok = fairy_mic_source.pick_input([dev] + [d for d in devs if d["index"] != dev["index"] and not d["lo"]])
    if _ok is None:
        log("✗ 所有麦克风都打不开（被其他程序独占或驱动问题）—— 稍后再试")
        # ★ 2026-10-12【无麦提醒】推给 host -> 球轮询念出："没有语音输入设备"
        try:
            import urllib.request as _ur
            _req = _ur.Request(
                ep.local_base() + "/api/fairy/push",
                data=json.dumps({"text": "没有语音输入设备，请检查麦克风或耳机",
                                 "source": "fairy-ear"}, ensure_ascii=False).encode("utf-8"),
                headers={"content-type": "application/json"})
            with _ur.urlopen(_req, timeout=5):
                pass
        except Exception:
            pass
        return 2
    dev = _ok
    log("监听设备（试开后选定）：idx %d %s（%s）@ %d Hz"
        % (dev["index"], dev["name"], dev["kind"], CFG["rate"]))
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
            log("语音识别模型（SenseVoice）后台预加载中：唤醒不受影响")
            warm_asr_async()
        else:
            log("连续对话已关 -> 不预加载语音识别模型")
        # ★ 2026-10-12：音频源改 fairy_mic_source（WDM-KS rec 轮询，兼容 read 接口）
        import atexit
        st = fairy_mic_source.FairyMicSource(dev, rate=rate, hop=hop)

        def _cleanup_pa():
            try:
                st.stop()
            except Exception:
                pass
        atexit.register(_cleanup_pa)

        log("耳朵已开（喊「Fairy」试试；--stop 停止）")
        # ★★ 2026-10-18【用户要求"启动只播天气和穿衣"】这里原来会念一句
        #   「Fairy 已就绪」—— 那是启动时第二处语音，与 briefing 的天气/穿衣重复。
        #   默认不播；要恢复就在 fairy.json 顶层写 "ear_announce_ready": true。
        try:
            _ann = (CFG or {}).get("ear_announce_ready", False)
        except Exception:
            _ann = False
        # ★★ 2026-10-18【修回归】`import threading as _th` 原来写在 `if _ann:` 里面，
        #   结果 Python 认为 _th 是本函数局部变量；默认 _ann=False 时下面
        #   「按键说话」那行用 _th 就抛 UnboundLocalError
        #   （日志实测：按键说话监听启动失败: cannot access local variable '_th'）。
        #   导入必须无条件执行。
        import threading as _th
        if _ann:
            try:
                _th.Thread(target=_announce_ready, daemon=True).start()
            except Exception:
                pass
        else:
            log("就绪播报已关闭（按用户要求启动只播天气+穿衣；要恢复在 fairy.json 写 ear_announce_ready=true）")
        # ★ 2026-10-12【按键说话】全局热键监听线程（鼠标侧键 / Ctrl+Alt+V）
        try:
            _ptt_cfg = CFG.get("ptt") or {}
            _th.Thread(target=ptt_listener, daemon=True).start()
            log("按键说话：已监听（按住 %s 或 %s 说话，松开识别）"
                % (_ptt_cfg.get("key", "xbutton1"), _ptt_cfg.get("alt_key", "ctrl+alt+v")))
        except Exception as _e_ptt:
            log("按键说话监听启动失败: %s" % _e_ptt)
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
        _sat_cnt = 0             # ★ 2026-10-12【削波自愈】连续“满格”窗口计数
        _sat_last_fix = 0.0      # 上次自动降麦克风音量的时间（10 分钟内不重复）
        _rms_hist = []           # ★ 2026-10-12【自适应底噪】最近块的 RMS / 峰值
        _peak_hist = []
        _floor_rms = 0.0
        _floor_peak = 0.0
        t0 = time.time()
        log("模式=端点检测(tail=%.1fs max=%.1fs) · 对话=%s · STT=%s · 块=%.2fs"
            % (CFG["tail_silence"], CFG["max_utt"],
               "开" if CFG.get("dialogue", True) else "关",
               CFG.get("stt_mode", "A"), block_s))
        while True:
            if seconds and time.time() - t0 >= seconds:
                break
            _raw = st.read(hop, exception_on_overflow=False)
            if isinstance(_raw, np.ndarray):
                blk = _raw.reshape(-1, 1)
            else:
                blk = np.frombuffer(_raw, dtype=np.float32).reshape(-1, 1)
            a = blk[:, 0]
            now = time.time()
            # ★ 2026-10-12【自适应底噪】每块算 RMS/峰值，滚动更新噪声底
            #   （P25 抗突发噪声；说话时也持续跟踪，说完自动回落）。
            rms_blk = float(np.sqrt(np.mean(a * a)) + 1e-12)
            pk_blk = float(np.max(np.abs(a)))
            _rms_hist.append(rms_blk)
            _peak_hist.append(pk_blk)
            if len(_rms_hist) > _ADAPT_WIN:
                _rms_hist.pop(0)
                _peak_hist.pop(0)
            if len(_rms_hist) >= 10:
                _floor_rms = float(np.percentile(_rms_hist, _ADAPT_PCT))
                _floor_peak = float(np.percentile(_peak_hist, _ADAPT_PCT))
            _update_speaking()      # ★ 2026-10-11：播放自然结束则清标记

            # ---------- ★ 自说自话保护：读掉但不收集 ----------
            if now < hold_until:
                # ★★ 2026-10-12【唤醒可打断】Fairy 播报期间喊「Fairy」：
                #    立即停播报并转入听取命令（不忽略用户）。
                if engines:
                    hit = kws_feed(engines, kws_amp(a), rate)
                    if hit:
                        tag, kw = hit
                        stop_current_play()
                        log("★ 唤醒词打断播报（%s）-> 转入听取命令" % kw)
                        hold_until = now + 0.4   # 给播放器收尾时间
                        collecting = False
                        buf = []
                        silent = 0.0
                        continue
                # ★★ 2026-10-11【对话打断】Fairy 播报期间用户插话 -> 停止播报、
                #    进入收集（尊重用户，不忽略）。
                if _SPEAKING:
                    lv = float(np.max(np.abs(a)))
                    # ★ 2026-10-12：噪声底以上才算插话（恒定底噪不再反复打断播报）
                    if lv > max(_INTERRUPT_GATE, _floor_peak * _ADAPT_RATIO):
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
                hit = kws_feed(engines, kws_amp(a), rate)
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
                stop_current_play()   # ★ 2026-10-12：唤醒即打断任何播报（含球端启动播报）
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
            #   · 按键说话按下时——强制收集
            ptt_down = _PTT.get("down", False)
            if ptt_down and mode != "ptt":
                mode = "ptt"
                collecting = True
                buf = [a.copy()]
                silent = 0.0
                log("★ 按键说话：开始收集（松开结束）")
            need_ep = (mode == "dialogue" and CFG.get("dialogue", True)) or (not engines) or (mode == "ptt")
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
                    if mic_peak < 0.03:
                        tip = "  ⚠ 麦克风几乎没收到声音（检查耳麦是否戴好/默音/没连上/或换个输入设备）"
                    # ★ 2026-10-12【削波自愈】连续满格(峰值≈1.0) = 输入被占满，
                    #   KWS 喂进去的全是饱和噪声 -> 自动降默认麦克风音量并播报一次。
                    if mic_peak >= 0.97:
                        _sat_cnt += 1
                    else:
                        _sat_cnt = max(0, _sat_cnt - 1)
                    if _sat_cnt >= 4 and (now - _sat_last_fix) > 600:
                        _sat_last_fix = now
                        _sat_cnt = 0
                        try:
                            _r_af = _autofix_mic()
                            if _r_af[0] == "lowered":
                                log("⚠ 麦克风持续削波(峰值≈1.0) -> 已自动降音量 %.2f→%.2f"
                                    % (_r_af[1], _r_af[2]))
                                try:
                                    import threading as _th2
                                    _th2.Thread(
                                        target=lambda: speak_text(
                                            "麦克风信号一直满格，可能受到干扰，我已自动调低麦克风音量，"
                                            "现在应该能正常唤醒我了"),
                                        daemon=True).start()
                                except Exception:
                                    pass
                            elif _r_af[0] == "still":
                                log("⚠ 麦克风仍削波(音量已低 %.2f) -> 请检查耳机/接口电流干扰"
                                    % _r_af[1])
                                try:
                                    import threading as _th3
                                    _th3.Thread(
                                        target=lambda: speak_text(
                                            "麦克风信号还是满格，音量已经很低了，可能是耳机或者接口有电流干扰，"
                                            "请检查一下麦克风"),
                                        daemon=True).start()
                                except Exception:
                                    pass
                            else:
                                log("削波自愈失败: %s" % _r_af[1])
                        except Exception as _e_m:
                            log("削波自愈异常: %s" % _e_m)
                    log("静默/待唤醒已 %d 块（KWS 在岗，不做转写）· 麦克风峰值=%.4f%s"
                        % (skipped, mic_peak, tip))
                    mic_peak = 0.0
                continue

            rms = rms_blk   # ★ 2026-10-12：块头已算好（含自适应底噪跟踪）
            _gate = max(CFG["rms_gate"], _floor_rms * _ADAPT_RATIO)
            enough = logic.声音够大(int(rms * 100000), int(_gate * 100000))

            if not collecting:
                if not enough:
                    skipped += 1
                    if skipped % 60 == 0:
                        log("静音跳过 %d 块（本块 RMS=%.5f，有效门限 %.5f=底噪%.5f×%.1f）"
                            % (skipped, rms, _gate, _floor_rms, _ADAPT_RATIO))
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
            if mode == "ptt":
                # 按键说话：松开才结束（不按静音判定），最长 max_utt 兜底
                if ptt_down and dur < CFG["max_utt"]:
                    continue
            elif silent < CFG["tail_silence"] and dur < CFG["max_utt"]:
                continue

            # ---- 这一段结束 ----
            collecting = False
            _PTT["was"] = (mode == "ptt")
            if mode == "ptt":
                mode = "wake"   # 按键式一次性：说完回到只等唤醒
                log("按键说话结束 -> 识别中（%.1fs）" % dur)
            audio = np.concatenate(buf).astype("float32")
            buf = []
            # ★ 2026-10-12：裁剪门限也随底噪走（恒定底噪不把整段当"有声"）
            _tg = max(CFG["rms_gate"], _floor_rms * 1.2)
            audio = trim_silence(audio, rate, _tg, CFG["lead_pad"])
            dur = len(audio) / rate
            if dur < CFG["min_utt"]:
                log("段太短（%.2fs < %.2fs）丢弃" % (dur, CFG["min_utt"]))
                continue
            last_voice = time.time()
            if not asr_ready():
                log("⚠ 语音识别模型还在后台加载中，这一句会等它（首次约 2 秒）")
            try:
                txt = transcribe(audio)
            except Exception as e:
                log("识别失败: %s" % e)
                continue
            if not txt:
                log("⚠ 转写返回空（音频 %.2fs）-> 换下一段" % dur)
                continue
            # ★ 2026-10-12【垃圾转写过滤】SenseVoice 对噪声/低音量常返回句号等标点：
            #   不是人话，不唤醒也不进路由，直接跳过。
            if all(_ch in "。，、！？.…" for _ch in txt):
                log("⚠ 转写仅为标点（%r）-> 忽略" % txt[:30])
                continue
            # ★ 2026-10-12【噪声幻觉过滤】纯噪声常被幻觉成"the / 对在这里"这类
            #   短串；非中文且 <6 字且不在常用英文指令白名单里 -> 当噪声忽略。
            _EN_OK = {"stop", "exit", "next", "ok", "yes", "no", "on", "off",
                      "up", "down", "home", "back", "play", "pause", "close",
                      "open", "hello", "hi", "fairy", "help", "music"}
            if (not any("\u4e00" <= ch <= "\u9fff" for ch in txt)
                    and len(txt) < 6 and txt.strip().lower() not in _EN_OK):
                log("⚠ 转写疑似噪声幻觉（%r）-> 忽略" % txt[:30])
                continue

            if not engines and mode == "wake" and not _PTT.get("was"):
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
                # ★ 2026-10-11【自定义指令】使用者定义的"说XX→做XX"优先于大脑
                try:
                    import fairy_custom_cmd as _fcc
                    _cc = _fcc.match(txt)
                    if _cc:
                        log("自定义指令命中：%s" % _cc.get("trigger"))
                        _r = _fcc.run(_cc, say=lambda _t, _k=None: speak_text(_t))
                        if _r:
                            log("自定义指令执行：%s" % _r[:90])
                            hold_until = time.time() + max(1.5, len(_r) * 0.22)
                            continue
                except Exception:
                    pass
                # ★ 2026-10-12【智能家居控制】"打开灯/关空调/调亮" -> smart_home 桥
                #   命中家居目标（灯/空调/窗帘/插座/扫地机等）就走控制；
                #   不是家居目标返回 None，继续走下面的开/关软件路由。
                try:
                    import smart_home as _sh
                    _sr = _sh.control(txt)
                    if _sr:
                        log("智能家居控制：%s -> %s" % (txt, _sr[:60]))
                        _HIST.append((txt, _sr))
                        route = speak_text(_sr)
                        log("已说出（%s）" % route)
                        hold_until = time.time() + 1.8
                        continue
                except Exception:
                    pass
                # ★ 2026-10-12【语音开软件】"打开/启动/运行 XX" -> fairy_apps.open_smart
                #   多级递推：内置目录 -> 开始菜单 -> Everything 全盘 -> 绿色软件
                try:
                    import re as _re
                    _m = _re.search(r"(打开|启动|运行|开启)\s*([\u4e00-\u9fa5a-zA-Z0-9]{2,20})", txt)
                    if _m:
                        _target = _m.group(2).strip()
                        import fairy_apps as _fa
                        _reply = _fa.open_smart(_target)
                        if not _reply.startswith("没有找到"):
                            log("语音打开软件：%s -> %s" % (_target, _reply[:60]))
                            _HIST.append((txt, _reply))
                            route = speak_text(_reply)
                            log("已说出（%s）" % route)
                            hold_until = time.time() + 1.8
                            continue
                except Exception as _e:
                    log("语音开软件异常: %s" % _e)
                # ★ 2026-10-12【语音关软件/关窗口】"关闭/退出/关掉 XX" -> close_smart
                #   （多级递推：进程名 -> 窗口标题 WM_CLOSE -> 注册表 -> 运行进程）
                try:
                    import re as _re2
                    import fairy_apps as _fa2
                    _mc = _re2.search(r"(关闭|退出|关掉|结束)\s*([\u4e00-\u9fa5a-zA-Z0-9]{2,20})", txt)
                    if _mc:
                        _target = _mc.group(2).strip()
                        _reply = _fa2.close_smart(_target)
                        if not _reply.startswith("没有找到"):
                            log("语音关闭：%s -> %s" % (_target, _reply[:60]))
                            _HIST.append((txt, _reply))
                            route = speak_text(_reply)
                            log("已说出（%s）" % route)
                            hold_until = time.time() + 1.8
                            continue
                except Exception as _e:
                    log("语音关软件异常: %s" % _e)
                try:
                    reply = ask_brain(txt, history=_HIST)
                except Exception as e:
                    log("大脑不可用: %s" % e)
                    reply = ""
                if not reply:
                    log("大脑没给出回答，跳过")
                    continue
                # ★ 2026-10-11【上下文接续】入历史（上限 _HIST_MAX），落盘供下次接续
                # 注：必须原地裁剪（del），不能用 _HIST = _HIST[...] 赋值——
                #     那会把模块级 _HIST 遮蔽成 main 局部变量，导致上方 append 报 UnboundLocalError。
                _HIST.append((txt, reply))
                if len(_HIST) > _HIST_MAX:
                    del _HIST[:len(_HIST) - _HIST_MAX]
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
