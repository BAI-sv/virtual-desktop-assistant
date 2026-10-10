# -*- coding: utf-8 -*-
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
FAIRY = os.path.join(TOOLS, "fairy.py")
LOG = os.path.join(ROOT, "logs", "shutdown_all.log")
CREATE_NO_WINDOW = 0x08000000
STRATA_UNLOAD = "http://127.0.0.1:8080/v1/unload"

def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass

def run(cmd, timeout=90):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace",
                           creationflags=CREATE_NO_WINDOW)
        out = ((p.stdout or "") + (p.stderr or "")).strip().replace("\n", " | ")
        return out[:200] if out else "(无输出)"
    except Exception as e:
        return "ERR %s: %s" % (type(e).__name__, e)

def stop_strata():
    out = []
    try:
        req = urllib.request.Request(STRATA_UNLOAD, data=b"{}",
                                     headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            out.append("unload=" + r.read().decode("utf-8", "replace")[:100])
    except Exception as e:
        out.append("unload跳过(%s)" % str(e)[:50])

    try:
        np_ = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             timeout=20, encoding="utf-8", errors="replace",
                             creationflags=CREATE_NO_WINDOW)
        pids = set()
        for line in np_.stdout.splitlines():
            if ":8080" in line and ("LISTENING" in line or "ESTABLISHED" in line):
                parts = line.split()
                if parts and parts[-1].isdigit():
                    pids.add(int(parts[-1]))
        killed = []
        for pid in sorted(pids):
            try:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=10,
                               creationflags=CREATE_NO_WINDOW)
                killed.append(pid)
            except Exception:
                pass
        out.append("杀进程=%s" % (killed or "无"))
    except Exception as e:
        out.append("杀进程失败(%s)" % str(e)[:60])
    return " | ".join(out)

def main():
    keep_ball = "--keep-ball" in sys.argv
    try:
        delay = float(os.environ.get("FAIRY_SHUTDOWN_DELAY", "2.5"))
    except Exception:
        delay = 2.5
    log("=== 全部退出开始（延迟 %.1fs，keep_ball=%s）===" % (delay, keep_ball))
    time.sleep(max(0.0, delay))

    for name in ("tts", "guard", "host"):
        log("stop %-5s: %s" % (name, run([sys.executable, FAIRY, "stop", name])))
    log("本地运算 : %s" % stop_strata())
    if not keep_ball:
        log("stop ball : %s" % run([sys.executable, FAIRY, "stop", "ball"]))
    log("=== 全部退出完成 ===")
    return 0

if __name__ == "__main__":
    sys.exit(main())
