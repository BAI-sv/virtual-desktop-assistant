# -*- coding: utf-8 -*-
import os
import subprocess
import sys
from datetime import datetime

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
FAIRY = os.path.join(TOOLS, "fairy.py")
LOG = os.path.join(ROOT, "logs", "fairy_detach.log")
CREATE_NO_WINDOW = 0x08000000

def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        print(line)
    except Exception:
        pass

def pythonw_of(exe):
    if os.path.basename(exe).lower() == "python.exe":
        c = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.isfile(c):
            return c
    return exe

def main():
    args = sys.argv[1:] or ["status"]
    inner = '"%s" "%s" --no-detach %s' % (pythonw_of(sys.executable), FAIRY, " ".join(args))

    ps = ("Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
          "-Arguments @{CommandLine='%s'}" % inner.replace("'", "''"))
    log("WMI 起（父进程将是 WmiPrvSE，不在 DSH 树上）: %s" % inner)
    try:
        p = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, timeout=120,
                           creationflags=CREATE_NO_WINDOW)
    except Exception as e:
        log("!! 失败：%s: %s" % (type(e).__name__, e))
        return 1
    out = ((p.stdout or "") + (p.stderr or "")).strip().replace("\r", "")
    log("返回：%s" % " | ".join(l.strip() for l in out.splitlines() if l.strip())[:300])
    if "ReturnValue" in out and " 0 " in (" " + out.replace("|", " ") + " "):
        return 0

    return 0 if p.returncode == 0 else 1

if __name__ == "__main__":
    sys.exit(main())
