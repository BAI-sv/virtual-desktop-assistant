# -*- coding: utf-8 -*-
"""
indextts_guard.py —— IndexTTS(:9881) 保活守护

【为什么需要它】
    用户定调：Fairy 的语音必须是【游戏里 Fairy 的音色】。
    音色靠 IndexTTS 用 fairy.wav 克隆出来 —— 它一挂，
    球的 /api/fairy/say 就降级成 Edge 通用音色（用户讨厌的那个）。
    所以必须有人盯着 9881，挂了立刻拉起来。

【用法】
    py -3.12 D:\\FAIRY\\tools\\indextts_guard.py --run          常驻守护（默认）
    py -3.12 D:\\FAIRY\\tools\\indextts_guard.py --once         只探一次，挂了就拉，然后退出
    py -3.12 D:\\FAIRY\\tools\\indextts_guard.py --status-json  一行 JSON
    py -3.12 D:\\FAIRY\\tools\\indextts_guard.py --stop         停掉守护（不杀 IndexTTS 本身）

【拉起方式】★ 复用 indextts_start.py 的候选逻辑（不自己发明）：
    它内部按 D:\\Index-TTS\\venv -> D:\\Index-tts-2.0-Windows-AMD\\env 找解释器，
    以 indextts_server.py 起服务，并写 D:\\FAIRY\\indextts.pid。
    守护这里【后台】起它（不阻塞本循环），随后自己轮询 /health 等 loaded=true。

【状态文件】D:\\FAIRY\\logs\\indextts_guard.json
    {"restarts":N,"last_restart":"HH:MM:SS","started_at":ts,"last_ok":ts,"consec_fail":N}
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

# ★ 2026-10-10【黑窗】0x08000000 = CREATE_NO_WINDOW
#   （★ 0x00000200 是 CREATE_NEW_PROCESS_GROUP，不是"不显示窗口"，以前记错过）
CREATE_NO_WINDOW = 0x08000000

# ★ 2026-10-10【黑窗】把 python.exe 换成 pythonw.exe 用（换不了就原样返回）。
#   本文件不 import fairy_root（它是个独立脚本），所以这里自己实现一份，
#   逻辑与 fairy_root.pythonw_of 一致。
def _pythonw_of(exe=None):
    exe = exe or sys.executable
    if not exe:
        return exe
    if os.path.basename(exe).lower() in ("python.exe", "py.exe"):
        cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(cand):
            return cand
    return exe

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
LOGDIR = os.path.join(ROOT, "logs")
STATE = os.path.join(LOGDIR, "indextts_guard.json")
GUARD_PID = os.path.join(LOGDIR, "indextts_guard.pid")
STARTER = os.path.join(HERE, "indextts_start.py")
PORT = 9881
PROBE_S = 15.0          # 每 15 秒探一次
COOLDOWN_S = 30.0       # 30 秒内不重复拉起（防疯狂重启）
READY_WAIT_S = 900.0    # 拉起后最多等这么久算 ready
TTS_URL = "http://127.0.0.1:%d/tts" % PORT
# 克隆参考音（和 voice_cache.py / 球用的是同一个）
REF_AUDIO = os.path.join(ROOT, "voice", "index_voices", "fairy.wav")


def _safe_stdio():
    """★★★ 2026-10-10【实测抓到的致命 bug】把 stdout/stderr 切成 utf-8 + errors=replace。

    【怎么发现的】本守护用 pythonw 起、stderr 被重定向成管道（fairy.py 的 start_one
    就是这么做的），此时 sys.stdout.encoding = 'gbk'。log() 里有 '✗' / '★'
    —— print 直接抛 UnicodeEncodeError: 'gbk' codec can't encode character '\\u2717'。

    【后果有多严重】do_check() 里那句 "9881 不可用" 正好是【每一次探测失败】都会走的路：
        · --once 模式下：异常直接冒到顶层 -> 守护当场死亡（实测拿到完整 traceback）
        · cmd_run 模式下：异常被循环的 except 吞掉 -> 守护【活着但永远不拉起 IndexTTS】
    -> 表现就是：用户听不到 Fairy 音色，而且保活守护"看着在跑"却什么都不干。

    ★ 这与黑窗同源：都是"父进程用 pythonw + 重定向"这条路上没处理的编码问题。
    """
    for name in ("stdout", "stderr"):
        s = getattr(sys, name, None)
        if s is None:            # pythonw 下 stdout 可能是 None，正常
            continue
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_safe_stdio()


def log(msg):
    print("[guard] %s %s" % (time.strftime("%H:%M:%S"), msg), flush=True)


def health(timeout=5):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/health" % PORT, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "why": "%s: %s" % (type(e).__name__, str(e)[:70])}


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(st):
    try:
        os.makedirs(LOGDIR, exist_ok=True)
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE)
    except Exception as e:
        log("状态落盘失败: %s: %s" % (type(e).__name__, e))


def starter_running():
    """indextts_start.py 是否还在跑（它可能正在等模型加载）"""
    try:
        # ★ 2026-10-10【黑窗】powershell.exe 是控制台程序；守护进程本身用
        #   pythonw / 计划任务起（无控制台）-> 不加这个每 15 秒弹一个窗口！
        #   本函数每轮都调，是"黑窗反复弹"的最主要来源。
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object {$_.CommandLine -like '*indextts_start*'}).ProcessId"],
            capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)
        out = (r.stdout or b"").decode("utf-8", "replace").strip()
        return bool(out)
    except Exception:
        return False


def server_running():
    """indextts_server.py 进程在不在"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object {$_.CommandLine -like '*indextts_server*'}).ProcessId"],
            capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)
        out = (r.stdout or b"").decode("utf-8", "replace").strip()
        return bool(out)
    except Exception:
        return False


def launch():
    """后台拉起 IndexTTS（复用 indextts_start.py）"""
    if not os.path.exists(STARTER):
        log("✗ 找不到 %s，无法拉起" % STARTER)
        return None
    try:
        os.makedirs(LOGDIR, exist_ok=True)
        lf = open(os.path.join(LOGDIR, "indextts_guard_launch.log"), "ab")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        # ★ 2026-10-10【黑窗】原来用 sys.executable：守护自己若是 python.exe
        #   （fairy.py 老版本就是这么起的），拉起来的这个 starter 又会挂一个
        #   控制台 -> 弹窗。换成 pythonw.exe 从根上不建控制台。
        p = subprocess.Popen([_pythonw_of(), STARTER], stdout=lf, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL,
                             creationflags=0x00000008 | 0x00000200 | 0x08000000, env=env)
        log("已后台拉起 IndexTTS（%s PID %d）" % (os.path.basename(STARTER), p.pid))
        return p
    except Exception as e:
        log("✗ 拉起失败: %s: %s" % (type(e).__name__, e))
        return None


def wait_ready(timeout=READY_WAIT_S, st=None):
    t0 = time.time()
    while time.time() - t0 < timeout:
        h = health()
        if h.get("ok") and h.get("loaded"):
            log("★ IndexTTS 就绪（等了 %.0f 秒）device=%s" % (time.time() - t0, h.get("device")))
            if st is not None:
                # ★ 修：就绪后要把失败计数归零、刷新 last_ok，
                #   否则 status 里 consec_fail 会一直停在拉起前的数（实测卡在 7）
                st["consec_fail"] = 0
                st["last_ok"] = int(time.time())
                save_state(st)
            return True
        time.sleep(5)
    log("⚠ 等了 %.0f 秒仍未就绪" % timeout)
    return False


def warmup(st=None):
    """★ 暖机：拉起后先合一句短文本并【丢弃】。

    【为什么必须暖】主代理实测（2026-10-09）：
        POST /api/fairy/say 首次调用 = 15.50 秒（模型首次推理极慢）
        之后的调用                = 2.9 ~ 5.3 秒（稳态）
    → 不暖机的话，用户听到的第一句要等 15 秒，会以为坏了。
    ★ 暖机失败【不算启动失败】—— 绝不能因为暖机报错就重启 IndexTTS。
    """
    t0 = time.time()
    try:
        body = json.dumps({"text": "预热", "ref_audio": REF_AUDIO, "lang": "zh",
                           "emo_alpha": 1.0}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(TTS_URL, data=body,
                                     headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            b = r.read()
        el = time.time() - t0
        log("★ 暖机完成 %.1f 秒（%d 字节，已丢弃）—— 用户听到的第一句就是快的"
            % (el, len(b)))
        if st is not None:
            st["warmup_s"] = round(el, 1)
            st["last_warmup"] = time.strftime("%H:%M:%S")
            st["warmup_error"] = None
            save_state(st)
        return True
    except Exception as e:
        el = time.time() - t0
        log("⚠ 暖机失败（%.1f 秒）：%s: %s —— 按约定【不算启动失败】，继续守护"
            % (el, type(e).__name__, str(e)[:80]))
        if st is not None:
            st["warmup_s"] = None
            st["warmup_error"] = "%s: %s" % (type(e).__name__, str(e)[:60])
            save_state(st)
        return False


def do_check(st, do_launch=True):
    """探一次；挂了就拉。

    ★ 返回 (up, need_wait)：
        need_wait=True 表示"正在起飞，请等它就绪后暖机"。
    【2026-10-09 修正】原来在"发现已有拉起程序在跑"时直接 return，
      导致【不等就绪、也不暖机】—— 用户听到第一句还是 15 秒的冷启动 ✗。
      现在：只要服务还没 ok，且已有拉起程序/服务进程在跑，就返回 need_wait=True 让上层去等 + 暖。
    """
    h = health()
    now = time.time()
    if h.get("ok"):
        st["consec_fail"] = 0
        st["last_ok"] = int(now)
        return True, False
    st["consec_fail"] = int(st.get("consec_fail", 0)) + 1
    log("✗ 9881 不可用（第 %d 次）：%s" % (st["consec_fail"], h.get("why")))
    if not do_launch:
        save_state(st)
        return False, False
    last = float(st.get("last_restart_ts") or 0)
    if now - last < COOLDOWN_S:
        log("· 冷却中（距上次拉起 %.0fs < %.0fs），本轮不重复拉" % (now - last, COOLDOWN_S))
        save_state(st)
        return False, False
    # ★ 已有拉起程序或服务进程在跑 -> 不重复拉，但要【等它就绪 + 暖机】
    if starter_running() or server_running():
        log("· 拉起程序/服务进程已在运行（正在加载模型）-> 等它就绪后暖机")
        save_state(st)
        return False, True
    p = launch()
    st["last_restart_ts"] = int(now)
    st["last_restart"] = time.strftime("%H:%M:%S")
    st["restarts"] = int(st.get("restarts", 0)) + 1
    save_state(st)
    return False, (p is not None)


def cmd_run():
    st = load_state()
    st.setdefault("started_at", int(time.time()))
    try:
        os.makedirs(LOGDIR, exist_ok=True)
        with open(GUARD_PID, "w") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass
    log("守护启动（PID %d）· 探针间隔 %.0fs · 冷却 %.0fs" % (os.getpid(), PROBE_S, COOLDOWN_S))
    h = health()
    log("当前状态：%s" % ("在线 device=%s loaded=%s" % (h.get("device"), h.get("loaded"))
                          if h.get("ok") else "离线 %s" % h.get("why")))
    prev_up = bool(st.get("_last_up"))
    while True:
        try:
            if os.path.exists(GUARD_PID):
                try:
                    if int(open(GUARD_PID).read().strip()) != os.getpid():
                        log("发现 PID 文件被别的守护接管，本进程退出")
                        return 0
                except Exception:
                    pass
            up, need_wait = do_check(st)
            if need_wait:
                if wait_ready(st=st):
                    warmup(st=st)
            elif up and not prev_up:
                # ★【2026-10-09 加】服务本来就在跑（不是本守护拉起的），
                #   但从"挂/未知"变成"活" —— 也要暖一次，
                #   否则用户听到的第一句就是要等 15 秒的冷启动。
                warmup(st=st)
            st["_last_up"] = up
            save_state(st)
            prev_up = up
            time.sleep(PROBE_S)
        except KeyboardInterrupt:
            log("收到中断，退出")
            return 0
        except Exception as e:
            log("循环异常（继续）: %s: %s" % (type(e).__name__, e))
            time.sleep(PROBE_S)


def cmd_status_json():
    st = load_state()
    h = health()
    pid = None
    try:
        if os.path.exists(GUARD_PID):
            pid = int(open(GUARD_PID).read().strip())
    except Exception:
        pass
    guard_up = False
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object {$_.CommandLine -like '*indextts_guard*'}).ProcessId"],
            capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)
        guard_up = bool((r.stdout or b"").decode("utf-8", "replace").strip())
    except Exception:
        pass
    uptime = 0
    if h.get("ok") and st.get("last_ok"):
        uptime = int(time.time()) - int(st.get("last_ok", 0))
        if st.get("started_at"):
            uptime = int(time.time()) - int(st["started_at"])
    out = {"up": bool(h.get("ok")),
           "loaded": bool(h.get("loaded")),
           "device": h.get("device"),
           "pid": pid,
           "guard_running": guard_up,
           "restarts": int(st.get("restarts", 0)),
           "last_restart": st.get("last_restart"),
           "consec_fail": int(st.get("consec_fail", 0)),
           "uptime_s": uptime,
           "why": None if h.get("ok") else h.get("why")}
    print(json.dumps(out, ensure_ascii=False))
    return 0


def cmd_stop():
    """停掉守护进程本身（不杀 IndexTTS）"""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object {$_.CommandLine -like '*indextts_guard*'} | "
             "ForEach-Object { '   kill PID ' + $_.ProcessId; Stop-Process -Id $_.ProcessId -Force }"],
            capture_output=True, timeout=40, creationflags=CREATE_NO_WINDOW)
        print((r.stdout or b"").decode("gbk", "replace").strip() or "   没有守护在跑")
        try:
            os.remove(GUARD_PID)
        except Exception:
            pass
    except Exception as e:
        print("停止失败: %s: %s" % (type(e).__name__, e))
    return 0


def main():
    a = sys.argv[1:]
    if "--stop" in a:
        return cmd_stop()
    if "--status-json" in a:
        return cmd_status_json()
    if "--once" in a:
        st = load_state()
        prev_up = bool(st.get("_last_up"))
        up, need_wait = do_check(st, do_launch=True)
        if need_wait:
            if wait_ready(st=st):
                warmup(st=st)
        elif up and not prev_up:
            # 服务本来就在跑，但从"挂/未知"变成"活" -> 也暖一次（见 cmd_run 里的同款说明）
            warmup(st=st)
        st["_last_up"] = up
        save_state(st)
        h = health()
        print(json.dumps({"before_up": up, "need_wait": need_wait,
                          "now_up": bool(h.get("ok")), "loaded": bool(h.get("loaded")),
                          "restarts": st.get("restarts"), "warmup_s": st.get("warmup_s")},
                         ensure_ascii=False))
        return 0
    return cmd_run()


if __name__ == "__main__":
    sys.exit(main())
