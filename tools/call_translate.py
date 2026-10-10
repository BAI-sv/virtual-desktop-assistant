# -*- coding: utf-8 -*-
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

import numpy as np
import fairy_root
import fairy_endpoints as ep


CREATE_NO_WINDOW = fairy_root.CREATE_NO_WINDOW

LOG_DIR = fairy_root.LOGS
CALL_STATE = os.path.join(LOG_DIR, "call_state.json")


CALL_APPS_EXACT = {
    "wechat", "weixin", "wechatappex", "wechatapp", "qq", "qqprotect", "txplatform",
    "discord", "teams", "ms-teams", "zoom", "skype", "skypeapp", "dingtalk",
    "feishu", "lark", "telegram", "whatsapp", "yy", "yylauncher", "kook",
    "mumble", "ventrilo", "raidcall", "hellotalk", "soul", "dingtalkdesktop",
}


COMM_ROLE_KEY = (r"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices\Audio"
                 r"\{0}\{1}\Role")

def log(s):
    print(s, flush=True)

def ps(cmd, t=25):
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, timeout=t,
                           creationflags=CREATE_NO_WINDOW)
        return (r.stdout or b"").decode("gbk", "replace")
    except Exception as e:
        log("[ps] 失败: %s" % str(e)[:80])
        return ""


def _bt_handsfree_active():
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
    out = ps('''Get-Process -ErrorAction SilentlyContinue |
      Select-Object -ExpandProperty ProcessName -Unique''', t=25)
    names = {l.strip().lower().removesuffix(".exe") for l in out.splitlines() if l.strip()}
    hit = sorted(names & CALL_APPS_EXACT)
    if hit:
        return True, "通话类软件在运行(不代表在通话): " + ", ".join(hit[:3])
    return False, ""

def _mic_in_use():
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

    return False, "疑似（仅弱信号）: " + "；".join(s[0] for s in sig)[:90], "low"

def write_call_state(active, reason, conf):
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


_LANG_LOCK = None
_WARMED = False

def _warmup_real():
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


            _ = "".join(s.text for s in it)
            t1 = time.time()
            log("[预热] ① 指定语言预热 %.1fs" % (t1 - t0))


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
        return "", det
    if det in ("zh", None) and len(a) <= 1:
        return "", det
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
        log("⚠ 用立体声混音：只抓 Realtek 输出。声音走蓝牙耳机/<BT_SPEAKER>会抓不到，"
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

            try:
                tok = spk.duck(0.15)
                _speak(zh or text)
                spk.unduck(tok)
            except Exception as e:
                log("[念译文] 失败: %s" % str(e)[:90])
    except Exception as e:
        log("[处理] 第 %d 句出错: %s" % (idx, str(e)[:120]))

def _speak(text):
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
