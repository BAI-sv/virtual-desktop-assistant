# -*- coding: utf-8 -*-
import json
import os
import subprocess
import sys
import time
import urllib.request


CREATE_NO_WINDOW = 0x08000000


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
PROBE_S = 15.0
COOLDOWN_S = 30.0
READY_WAIT_S = 900.0
TTS_URL = "http://127.0.0.1:%d/tts" % PORT

REF_AUDIO = os.path.join(ROOT, "voice", "index_voices", "fairy.wav")

def _safe_stdio():
    for name in ("stdout", "stderr"):
        s = getattr(sys, name, None)
        if s is None:
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
    try:


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
    if not os.path.exists(STARTER):
        log("✗ 找不到 %s，无法拉起" % STARTER)
        return None
    try:
        os.makedirs(LOGDIR, exist_ok=True)
        lf = open(os.path.join(LOGDIR, "indextts_guard_launch.log"), "ab")
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"


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


                st["consec_fail"] = 0
                st["last_ok"] = int(time.time())
                save_state(st)
            return True
        time.sleep(5)
    log("⚠ 等了 %.0f 秒仍未就绪" % timeout)
    return False

def warmup(st=None):
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
