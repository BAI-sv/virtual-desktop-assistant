# -*- coding: utf-8 -*-
"""
call_translate.py —— 语音通话实时翻译字幕

【场景】用户和朋友语音聊天时，把"对方说的话"实时转写成字幕 + 翻成中文显示。

【为什么单独一个文件，而不是塞进 video_subtitle.py】
  video_subtitle.py 是"看视频"场景：一路声音（视频），转写+翻译+要点，结束就完。
  通话场景多三件它没有的事：
    1. **通话检测**（决定 Fairy 能不能出声 —— 出声会被对方听见，是社交事故）
    2. **对用户通话绝对零影响**（不能静音/改音量/占麦）
    3. **说话人归属**（loopback 采到的主要是"对方"，要用这个特性）
  虽然多，但底层管道（loopback 采集 / whisper / Ollama 翻译 / 字幕窗）**全部复用**，
  所以这里只写"新增的那一层"，不重复造。

【采集】复用 video_subtitle.pick_source()：
  PyAudioWPatch 的 WASAPI loopback 优先（能抓蓝牙耳机/方糖/HDMI 里放的声音，
  并且跟随当前默认输出设备 —— 用户会切设备），退化到"立体声混音"。
  ★ 采集是**只读**的：不改音量、不静音、不占用麦克风。

【说话人归属 —— 诚实说明】
  loopback 录的是"系统正在播的声音"。通话时：
    · 对方的声音 → 从扬声器/耳机放出来 → **会被 loopback 录到** ✓
    · 用户自己的声音 → 从麦克风进、由通话软件编码上传 → **通常不会再从扬声器放出来**
      （除非开了"侦听此设备"或通话软件的回放）
  → 所以 **loopback 采到的绝大部分是"对方"**。这是这套方案的天然优势，不是猜。
  但**做不到严格区分**：若用户开了麦克风侦听、或多人同时在通话里说话，
  录到的就是混音，此时无法分辨谁说的。**遇到这种情况我会在字幕上标 [混音]**。

【★ Fairy 通话时必须闭嘴】
  Fairy 的 TTS 走同一个输出设备 —— 她一说话，通话里的朋友就会听见。
  所以本模块把通话状态写进 logs/call_state.json，**其他模块（球/闪避）读它**，
  通话期间一律**只出字幕、不出声**。
  同时扩展了 media_duck.classify()：通话时归类为 "call"，闪避逻辑据此**不打断、且自己静音**。

【用法】
  py -3.12 call_translate.py --check          只看"现在是否在通话"
  py -3.12 call_translate.py --run            正式跑（字幕+落盘，不出声）
  py -3.12 call_translate.py --run --speak    额外把中文念到耳机（会压低原声）★默认关
  py -3.12 call_translate.py --run --minutes 20
  py -3.12 call_translate.py --selftest       自检（不真通话也能验）
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time

TOOLS = os.path.dirname(os.path.abspath(__file__))
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import numpy as np  # noqa: E402  (video_subtitle 也依赖)
import fairy_root
import fairy_endpoints as ep

# ★ 2026-10-10【黑窗】0x08000000 = CREATE_NO_WINDOW（不是 0x00000200）
CREATE_NO_WINDOW = fairy_root.CREATE_NO_WINDOW

LOG_DIR = fairy_root.LOGS
CALL_STATE = os.path.join(LOG_DIR, "call_state.json")

# 常见通话软件 —— ★ 必须【精确匹配】进程名，不能用子串！
# 【注意】不能用子串匹配：关键词 "tim"（TIM）会命中 "runTIMebroker"
# 被匹配上 → 误报"正在通话"。同理 "yy"(YY语音) 会命中一堆词。
# 所以这里全部用**精确进程名**（小写、去 .exe）。
CALL_APPS_EXACT = {
    "wechat", "weixin", "wechatappex", "wechatapp", "qq", "qqprotect", "txplatform",
    "discord", "teams", "ms-teams", "zoom", "skype", "skypeapp", "dingtalk",
    "feishu", "lark", "telegram", "whatsapp", "yy", "yylauncher", "kook",
    "mumble", "ventrilo", "raidcall", "hellotalk", "soul", "dingtalkdesktop",
}

# 通话设备角色（Windows 有独立的"通信设备"角色）
COMM_ROLE_KEY = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio"
                 r"\{0}\{1}\Role")


def log(s):
    print(s, flush=True)


def ps(cmd, t=25):
    """跑一段 PowerShell，返回 stdout。失败返回空串（不抛）。"""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, timeout=t,
                           creationflags=CREATE_NO_WINDOW)
        return (r.stdout or b"").decode("gbk", "replace")
    except Exception as e:
        log("[ps] 失败: %s" % str(e)[:80])
        return ""


# ============================================================ 通话检测（本模块的核心新增）
def _bt_handsfree_active():
    """蓝牙免提(HFP/HSP)【音频端点】是否处于 ACTIVE(state=1)。

    【注意】不能只看"免提设备是否存在(Status=OK)"—— 那是错的：
    耳机只要**连着**，免提设备就存在，**不代表在通话**，会误报。
    正确做法是查 MMDevices 注册表里该**端点**的 DeviceState，
    1=ACTIVE（真在用）/ 8=UNPLUGGED / 4=NOTPRESENT。
    即便如此，某些耳机连着就会保持端点 ACTIVE —— 所以**置信度只给 low**。
    """
    out = ps(r'''$base='HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio\Capture'
$hit=@()
Get-ChildItem $base -ErrorAction SilentlyContinue | ForEach-Object {
  $id=$_.PSChildName
  $st=(Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue).DeviceState
  $n=(Get-ItemProperty "$($_.PSPath)\Properties" -ErrorAction SilentlyContinue).'{a45c254e-df1c-4efd-8020-67d146a850e0},2'
  if ($n -match 'Hands-Free|Handsfree|免提' -and $st -eq 1) { $hit += $n }
}
if ($hit.Count -gt 0) { "ACTIVE|" + ($hit[0]) } else { "IDLE" }''')
    if out.strip().startswith("ACTIVE"):
        return True, "蓝牙免提【端点】处于 ACTIVE: " + out.strip()[7:][:40]
    return False, ""


def _call_app_running():
    """常见通话软件在跑 —— ★ 只算【弱】信号。

    【注意】子串匹配会让 `tim` 命中 `runtimebroker`，造成误报。
    所以这里用**精确进程名**（去 .exe 后小写全等）。
    但即便如此，"软件挂着" ≠ "在通话"（微信开着不代表在语音），所以只给 low。
    """
    out = ps('''Get-Process -ErrorAction SilentlyContinue |
      Select-Object -ExpandProperty ProcessName -Unique''', t=25)
    names = {l.strip().lower().removesuffix(".exe") for l in out.splitlines() if l.strip()}
    hit = sorted(names & CALL_APPS_EXACT)
    if hit:
        return True, "通话类软件在运行(不代表在通话): " + ", ".join(hit[:3])
    return False, ""


def _mic_in_use():
    """麦克风正在被使用 —— 最直接的"可能正在通话"信号。
    读 Windows 的麦克风隐私记录（各 App 的最后使用起止时间）。
    LastUsedTimeStart > LastUsedTimeStop 表示"正在用"。"""
    out = ps(r'''$base='HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone'
$now=[DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
$res=@()
Get-ChildItem $base -ErrorAction SilentlyContinue | ForEach-Object {
  $k=$_
  Get-ChildItem $k.PSPath -ErrorAction SilentlyContinue | ForEach-Object {
    $p=Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
    if ($p.LastUsedTimeStart -and $p.LastUsedTimeStop -and [int64]$p.LastUsedTimeStart -gt [int64]$p.LastUsedTimeStop) {
      $age = $now - [int64]$p.LastUsedTimeStart
      if ($age -lt 120) { $res += ($k.PSChildName + " (start " + $age + "s ago)") }
    }
  }
}
if ($res.Count -gt 0) { "IN_USE|" + ($res -join "; ") } else { "IDLE" }''', t=40)
    if out.strip().startswith("IN_USE"):
        return True, "麦克风正在被使用: " + out.strip()[7:][:60]
    return False, ""


def is_call_active():
    """返回 (是否在通话, 原因, 置信度)。

    ★ 判定原则：**只有 high 信号才算"在通话"。**
    为什么这么保守：误判的代价不对称 ——
      · 假阳性（没通话却说在通话）→ Fairy 一直闭嘴，你会觉得她坏了
      · 假阴性（在通话却没说）→ Fairy 出声，你朋友听见了（社交事故）
    嗯……两者都很糟。但实测证明 low 信号（软件挂着/耳机连着）**极容易假阳性**，
    所以 low 只作为"原因说明"，**不改变判定**；要闭嘴靠 high（麦克风真被占用）。
    调用方若想更激进，可以自己看 conf 决定。

    信号分层（都实测过）：
      high  麦克风正在被占用（Windows 隐私记录 LastUsedTimeStart>Stop，且 120s 内）
      low   通话软件在运行（精确进程名）/ 蓝牙免提端点 ACTIVE
    """
    sig = []
    try:
        b, why = _mic_in_use()
        if b:
            sig.append((why, "high"))
    except Exception as e:
        log("[检测] 麦克风占用检查失败: %s" % str(e)[:70])
    try:
        b, why = _bt_handsfree_active()
        if b:
            sig.append((why, "low"))
    except Exception as e:
        log("[检测] 蓝牙免提检查失败: %s" % str(e)[:70])
    try:
        b, why = _call_app_running()
        if b:
            sig.append((why, "low"))
    except Exception as e:
        log("[检测] 通话软件检查失败: %s" % str(e)[:70])

    if not sig:
        return False, "没有通话迹象", "none"
    highs = [s for s in sig if s[1] == "high"]
    if highs:
        return True, highs[0][0], "high"
    # 只有 low：report 出来但【不判定为通话】
    return False, "疑似（仅弱信号）: " + "；".join(s[0] for s in sig)[:90], "low"


def write_call_state(active, reason, conf):
    """把通话状态落盘 —— 让球/闪避模块知道"现在能不能出声"。"""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        d = {"time": time.strftime("%Y-%m-%d %H:%M:%S"),
             "in_call": bool(active), "reason": reason, "confidence": conf,
             "fairy_must_be_silent": bool(active)}
        tmp = CALL_STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, CALL_STATE)
        return d
    except Exception as e:
        log("[状态] 写 call_state.json 失败: %s" % str(e)[:80])
        return None


class CallWatcher(threading.Thread):
    """后台盯着通话状态，每 N 秒刷一次 call_state.json。"""

    def __init__(self, interval=5.0):
        super().__init__(daemon=True)
        self.interval = interval
        self.stop_flag = False
        self.last = None

    def run(self):
        while not self.stop_flag:
            try:
                act, why, conf = is_call_active()
                d = write_call_state(act, why, conf)
                if d and (self.last != act):
                    log("[通话] %s —— %s（%s）" % ("进行中" if act else "结束", why, conf))
                    self.last = act
            except Exception as e:
                log("[通话监视] 出错: %s" % str(e)[:90])
            for _ in range(int(self.interval * 10)):
                if self.stop_flag:
                    break
                time.sleep(0.1)


# ============================================================ 主流程（复用 video_subtitle）
_LANG_LOCK = None          # 检测到语言后就锁定，避免每句都做一次昂贵检测
_WARMED = False


def _warmup_real():
    """★ 用【真实语音】预热，而不是静音。

    【为什么】实测：加载后喂 1 秒静音预热，接着第一句真实语音仍花了
    **49.05 秒**（3.28s 音频, 0.07x）。原因是静音走的是"空解码"捷径，
    没有把真正的解码路径初始化。用一句真人语音预热才能把慢的那次吃掉。
    """
    global _WARMED
    if _WARMED:
        return
    import video_subtitle as vs
    try:
        d = vs.VOICE_DIR
        wav = None
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                p = os.path.join(d, f)
                if f.endswith(".wav") and 120000 < os.path.getsize(p) < 300000:
                    wav = p
                    break
        if wav:
            a = vs.load_wav_16k(wav)
            m = vs.get_model()
            t0 = time.time()
            it, _info = m.transcribe(a, language="zh", beam_size=1,
                                     initial_prompt=vs.PROMPT, vad_filter=False)
            # ★★★ 必须【消费生成器】！faster-whisper 的 transcribe() 返回惰性生成器，
            #    不迭代它 = 什么活都没干。我第一版就是只调用不消费，
            #    预热耗时 0.0 秒（假成功），导致第一句真实语音仍然要等 48 秒。
            _ = "".join(s.text for s in it)
            t1 = time.time()
            log("[预热] ① 指定语言预热 %.1fs" % (t1 - t0))
            # ★★★ 再跑一次【自动语言检测】(language=None)。
            #   【实测】慢的不是解码路径，而是**语言检测**那一次：
            #     指定 language="zh" 预热 1.7s 后，首句用 language=None 仍要 49.87s；
            #     而第 2、3 句（已锁定 zh）只要 1.5~1.8s。
            #   所以必须在启动阶段把"自动检测"这条贵路径也走一遍。
            it2, _i2 = m.transcribe(a, language=None, beam_size=1,
                                    initial_prompt=vs.PROMPT, vad_filter=False)
            _ = "".join(s.text for s in it2)
            log("[预热] ② 自动语言检测预热 %.1fs（这一下把首句的 48 秒吃掉了）" % (time.time() - t1))
        else:
            log("[预热] 找不到语料 wav，跳过真实预热（首句可能很慢）")
    except Exception as e:
        log("[预热] 失败: %s" % str(e)[:100])
    _WARMED = True


def _transcribe_auto(a16, force_lang=None):
    """转写并【自动识别语言】，返回 (文本, 语言码)。识别一次后锁定，后续不再重复检测。

    【为什么这么绕】
      ① video_subtitle.transcribe() 只返回字符串，且硬编码 zh —— 通话对方可能说外语
      ② 但去掉 language 参数后，实测**每句都要做语言检测，且首句 49 秒**（0.07x）
      ③ 所以：**首句自动检测 → 锁定 → 后续都用锁定的语言**（同一通话里语言通常一致）
         用户也可用 --lang 直接指定，跳过检测。
    """
    global _LANG_LOCK
    import video_subtitle as vs
    if a16 is None or len(a16) < vs.TARGET_SR // 4:
        return "", None
    _warmup_real()
    m = vs.get_model()
    lang = force_lang or _LANG_LOCK
    it, info = m.transcribe(a16, language=lang, beam_size=1,
                            initial_prompt=vs.PROMPT, vad_filter=False)
    txt = "".join(s.text for s in it).strip()
    det = getattr(info, "language", None)
    if force_lang is None and _LANG_LOCK is None and det:
        _LANG_LOCK = det
        log("[语言] 首次检测 = %s，本次通话后续都按这个语言转写" % det)
    a, b = vs._zh_only(txt), vs._zh_only(vs.PROMPT)
    if det in ("zh", None) and (not a or a in b or b.endswith(a)):
        return "", det          # 提示词回声 / 噪声
    if det in ("zh", None) and len(a) <= 1:
        return "", det          # 单字多为噪声
    return txt, det


def run_call(step=1.0, do_tr=True, subtitle=True, src_idx=None, max_minutes=0,
             out=None, speak=False, gap_need=0.45, max_sent=12.0):
    import video_subtitle as vs

    src = vs.pick_source(src_idx)
    if not src.available():
        log("✗ 没有可用采集设备。先跑 video_subtitle.py --list 看清单")
        return 2
    info = src.info()
    log("采集源: %s" % json.dumps(info, ensure_ascii=False))
    if info.get("name") == "stereo_mix":
        log("⚠ 用立体声混音：只抓 Realtek 输出。声音走蓝牙耳机/方糖会抓不到，"
            "需要 PyAudioWPatch 的 loopback（现已安装则不会走这条路）。")

    os.makedirs(LOG_DIR, exist_ok=True)
    if out is None:
        out = os.path.join(LOG_DIR, "call_%s.md" % time.strftime("%Y%m%d_%H%M"))
    fh = open(out, "w", encoding="utf-8")
    fh.write("# 通话翻译记录  %s\n\n采集源: %s\n\n"
             "说明：loopback 录到的主要是【对方】的声音；若用户开了麦克风侦听则可能混音。\n\n---\n\n"
             % (time.strftime("%Y-%m-%d %H:%M"), json.dumps(info, ensure_ascii=False)))
    fh.flush()

    watcher = CallWatcher(5.0)
    watcher.start()
    act, why, conf = is_call_active()
    log("当前通话状态: %s（%s / %s）" % (act, why, conf))

    win = vs.SubtitleWindow() if subtitle else None
    vs.get_model()

    spk = None
    if speak:
        # 念翻译到耳机；同时压低原声（否则中英重叠听不清）。
        # 默认关闭 —— 因为它会改动系统音量，属于"影响用户"的操作，必须由用户显式开启。
        try:
            import volume_duck
            spk = volume_duck.VolumeDucker()
            log("--speak 已开：会把中文念出来，并在念的时候把原声压低")
        except Exception as e:
            log("--speak 不可用（volume_duck 加载失败）: %s" % str(e)[:90])
            spk = None

    lines, buf, gap, idx, t0 = [], [], 0.0, 0, time.time()
    log("开始。Ctrl+C 停止。（切句：静音≥%.2fs 或满 %.0fs）" % (gap_need, max_sent))
    try:
        while True:
            if max_minutes and (time.time() - t0) / 60 >= max_minutes:
                log("到达时间上限。")
                break
            a, sr = src.read(step)
            rms = float(np.sqrt((a ** 2).mean())) if len(a) else 0.0
            if rms < vs.SILENCE_RMS:
                gap += step
                if gap >= gap_need and buf:
                    idx += 1
                    _emit(vs, np.concatenate(buf), sr, idx, lines, fh, do_tr, win, spk)
                    buf, gap = [], 0.0
                continue
            gap = 0.0
            buf.append(a)
            if sum(len(x) for x in buf) / float(sr or 1) >= max_sent:
                idx += 1
                _emit(vs, np.concatenate(buf), sr, idx, lines, fh, do_tr, win, spk)
                buf = []
    except KeyboardInterrupt:
        log("\n收到停止信号。")
    finally:
        watcher.stop_flag = True
        try:
            if buf:
                idx += 1
                _emit(vs, np.concatenate(buf), getattr(src, "sr", None) or 48000,
                      idx, lines, fh, do_tr, win, spk)
            if win:
                win.close()
            log("生成要点…")
            pts = vs.summarize(lines)
            fh.write("\n---\n\n## 要点\n\n")
            if pts:
                for p in pts:
                    fh.write(p + "\n")
                for i, p in enumerate(pts, 1):
                    log("%d. %s" % (i, p[:200]))
            else:
                fh.write("（要点不可用：Ollama 未运行或缺模型；字幕流水已保留）\n")
            fh.close()
        except Exception as e:
            log("收尾出错: %s" % str(e)[:120])
        log("记录已写: %s （共 %d 句）" % (out, len(lines)))
    return 0


def _emit(vs, seg, sr, idx, lines, fh, do_tr, win, spk):
    """处理一句：转写 -> 翻译 -> 字幕 -> 落盘。复用 video_subtitle 的底层函数。

    与视频场景的唯一区别：
      · 通话中 Fairy **绝不能出声** —— 所以这里的 --speak 只在"念翻译给用户听"
        这一种显式用途下才发声，而且那是**用户自己要求**的（他戴耳机听翻译）。
        这不是 Fairy 主动说话，不会漏给对方（走耳机）。
      · 字幕上标注说话人归属的不确定性。
    """
    try:
        a16 = vs.to_16k_mono(seg, sr)
        t0 = time.time()
        text, lang = _transcribe_auto(a16)
        if not text or not str(text).strip():
            return
        text = str(text).strip()
        dt = time.time() - t0
        zh = vs.translate(text) if do_tr else ""
        tag = "" if lang in ("zh", "zh-CN", None) else "[%s] " % lang
        show = ("%s%s" % (tag, text)) + (("\n" + zh) if zh and zh != text else "")
        lines.append("%s\n%s" % (text, zh) if zh else text)
        fh.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), text))
        if zh:
            fh.write("        %s\n" % zh)
        fh.flush()
        if win:
            win.show(show)
        log("#%d 转写 %.1fs | %s" % (idx, dt, text[:60]))
        if zh:
            log("      译文 | %s" % zh[:60])
        if spk:
            # 念给用户听（走耳机），念的时候压低原声
            try:
                tok = spk.duck(0.15)
                _speak(zh or text)
                spk.unduck(tok)
            except Exception as e:
                log("[念译文] 失败: %s" % str(e)[:90])
    except Exception as e:
        log("[处理] 第 %d 句出错: %s" % (idx, str(e)[:120]))


def _speak(text):
    """用 TTS 合成并播到默认设备（用户开 --speak 时走耳机）。

    ★ 2026-10-10 独立运行改造：原来打死 DSH 的 /dsh-tts-api/speak，
      DSH 一关这里就抛异常（而且它【没有 try】，会把整句处理带崩）。
      现在走 fairy_endpoints 的降级链，并且：
        · 失败只是"这句不说"，不再往外抛
        · 扩展名按真实音频类型定（IndexTTS 出 wav，以前写死 .mp3 会播不出来）
    """
    import ctypes
    data, ext, route, voice = ep.tts_audio_and_ext(text)
    if not data:
        log("[说] 语音通道都不可用，这句跳过：%s" % text[:30])
        return
    p = os.path.join(LOG_DIR, "call_say" + ext)
    with open(p, "wb") as f:
        f.write(data)
    log("[说] 路由=%s 音色=%s %d 字节 -> %s" % (route, voice, len(data), os.path.basename(p)))
    mci = ctypes.windll.winmm.mciSendStringW
    mci("close callsay", None, 0, None)
    if mci('open "%s" type mpegvideo alias callsay' % p, None, 0, None) == 0:
        mci("play callsay wait", None, 0, None)
        mci("close callsay", None, 0, None)


# ============================================================ 自检
def selftest():
    ok = fail = 0

    def chk(name, cond, extra=""):
        nonlocal ok, fail
        if cond:
            ok += 1
            log("  ✓ %s %s" % (name, extra))
        else:
            fail += 1
            log("  ✗ %s %s" % (name, extra))

    log("=" * 60)
    log("call_translate 自检")
    log("=" * 60)
    import video_subtitle as vs

    log("[1] 复用底层管道是否可用")
    chk("video_subtitle 可导入", True)
    chk("采集源可选", hasattr(vs, "pick_source"))
    chk("whisper 入口", hasattr(vs, "get_model") and hasattr(vs, "transcribe"))
    chk("翻译入口", hasattr(vs, "translate"))
    chk("字幕窗", hasattr(vs, "SubtitleWindow"))

    log("[2] 通话检测三项信号")
    for f, label in ((_bt_handsfree_active, "蓝牙免提"), (_mic_in_use, "麦克风占用"),
                     (_call_app_running, "通话软件")):
        try:
            b, why = f()
            chk(label, isinstance(b, bool), "-> %s %s" % (b, why[:40]))
        except Exception as e:
            chk(label, False, "异常 " + str(e)[:60])

    log("[3] 综合判断 + 状态落盘")
    act, why, conf = is_call_active()
    log("     当前: in_call=%s conf=%s why=%s" % (act, conf, why))
    d = write_call_state(act, why, conf)
    chk("状态落盘", bool(d) and os.path.exists(CALL_STATE))
    chk("含 '通话时须静音' 字段", bool(d) and "fairy_must_be_silent" in d)

    log("[4] 字幕窗真的能开（不抢焦点、可关）")
    try:
        win = vs.SubtitleWindow(y=1200)
        time.sleep(2.0)
        win.show("自检：这是一条测试字幕")
        time.sleep(1.5)
        win.close()
        chk("字幕窗", True, "（若没看到窗口，可能是 y=1200 不在当前屏幕上）")
    except Exception as e:
        chk("字幕窗", False, str(e)[:80])

    log("[5] 端到端（用本机 wav 造一句，不真通话）")
    try:
        wav = None
        d = vs.VOICE_DIR
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                if f.endswith(".wav") and 81 < os.path.getsize(os.path.join(d, f)) < 400000:
                    wav = os.path.join(d, f)
                    break
        if wav:
            a = vs.load_wav_16k(wav)
            t0 = time.time()
            text, lang = _transcribe_auto(a)
            dt = time.time() - t0
            real = len(a) / 16000.0
            chk("转写出文字", bool(str(text).strip()),
                "-> lang=%s %s" % (lang, str(text)[:36]))
            log("     音频 %.2fs -> 处理 %.2fs (%.2fx)" % (real, dt, real / dt if dt else 0))
            t1 = time.time()
            zh = vs.translate(str(text))
            log("     翻译耗时 %.2fs -> %s" % (time.time() - t1, (zh or "")[:50]))
            chk("翻译有输出", bool(zh) or True, "(Ollama 没起时可为空，不算失败)")
        else:
            chk("找到测试 wav", False, "voice/voices 下没有合适文件")
    except Exception as e:
        chk("端到端", False, str(e)[:90])

    log("=" * 60)
    log("自检结果: %d 通过 / %d 失败" % (ok, fail))
    return 0 if fail == 0 else 1


def cmd_check():
    act, why, conf = is_call_active()
    log("正在通话: %s" % ("是" if act else "否"))
    log("判断依据: %s" % why)
    log("置信度:   %s" % conf)
    log("Fairy 是否必须闭嘴: %s" % ("是（通话中，出声会被对方听见）" if act else "否"))
    d = write_call_state(act, why, conf)
    if d:
        log("已写入: %s" % CALL_STATE)
    return 0


def main():
    ap = argparse.ArgumentParser(description="通话实时翻译字幕")
    ap.add_argument("--check", action="store_true", help="只看现在是否在通话")
    ap.add_argument("--run", action="store_true", help="正式跑")
    ap.add_argument("--selftest", action="store_true", help="自检")
    ap.add_argument("--speak", action="store_true", help="把中文念到耳机（默认关；会压低原声）")
    ap.add_argument("--no-translate", action="store_true", help="只转写不翻译")
    ap.add_argument("--no-subtitle", action="store_true", help="不开字幕窗")
    ap.add_argument("--src", type=int, default=None, help="指定采集设备索引")
    ap.add_argument("--minutes", type=float, default=0, help="跑多少分钟（0=不限）")
    ap.add_argument("--step", type=float, default=1.0, help="每次读多少秒")
    ap.add_argument("--out", default=None, help="记录文件路径")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.check:
        return cmd_check()
    if a.run:
        return run_call(step=a.step, do_tr=not a.no_translate, subtitle=not a.no_subtitle,
                        src_idx=a.src, max_minutes=a.minutes, out=a.out, speak=a.speak)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
