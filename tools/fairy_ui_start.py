# -*- coding: utf-8 -*-
import os
import socket
import subprocess
import sys
import time

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
SCRIPT = os.path.join(TOOLS, "fairy_ui.py")
LOG = os.path.join(ROOT, "logs", "fairy_ui.log")
PORT = 8081

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

def port_open(port, timeout=0.5):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass

def main():
    if port_open(PORT):
        print("8081 已经在监听，不动")
        return 0
    if not os.path.isfile(SCRIPT):
        print("找不到 %s" % SCRIPT)
        return 1

    exe = sys.executable
    if os.path.basename(exe).lower() == "python.exe":
        cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.isfile(cand):
            exe = cand
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    lf = open(LOG, "ab")
    try:
        subprocess.Popen([exe, SCRIPT, "--no-open"], cwd=TOOLS, stdout=lf,
                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
                         close_fds=True)
    finally:
        lf.close()
    for i in range(30):
        time.sleep(0.5)
        if port_open(PORT):
            print("8081 已就绪（%.1fs）" % (0.5 * (i + 1)))
            return 0
    print("8081 15 秒内没起来，看日志 %s" % LOG)
    return 1

if __name__ == "__main__":
    sys.exit(main())
