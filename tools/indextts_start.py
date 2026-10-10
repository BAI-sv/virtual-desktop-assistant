# -*- coding: utf-8 -*-
import json
import os
import subprocess
import sys
import time
import urllib.request
import fairy_root


for _s in (sys.stdout, sys.stderr):
    try:
        if _s is not None:
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_HERE = os.path.dirname(os.path.abspath(__file__))


CANDIDATES = [
    (fairy_root.INDEXTTS_ROOT, "venv"),
    (fairy_root.INDEXTTS_CANDIDATES[1], "env"),
]
SERVER = os.path.join(_HERE, "indextts_server.py")
LOGDIR = fairy_root.LOGS
PIDFILE = os.path.join(fairy_root.PID_DIR, "indextts.pid")
PORT = 9881

def pick():
    for root, venv_rel in CANDIDATES:


        py = os.path.join(root, venv_rel, "Scripts", "pythonw.exe")
        if not os.path.exists(py):
            py = os.path.join(root, venv_rel, "Scripts", "python.exe")
        if not os.path.exists(py):
            py = os.path.join(root, venv_rel, "pythonw.exe")
        if not os.path.exists(py):
            py = os.path.join(root, venv_rel, "python.exe")
        ck = os.path.join(root, "checkpoints", "gpt.pth")
        size = os.path.getsize(ck) / 2 ** 30 if os.path.exists(ck) else 0
        print("  候选 %-42s python=%s gpt.pth=%.2fGB" % (root, os.path.exists(py), size))
        if os.path.exists(py):
            return root, py, size
    return None, None, 0

def probe(timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen("http://127.0.0.1:%d/health" % PORT, timeout=5) as r:
                d = json.load(r)
            print("IndexTTS 就绪（%.0f 秒）：api=%s device=%s loaded=%s"
                  % (time.time() - t0, d.get("api"), d.get("device"), d.get("loaded")))
            return 0
        except Exception:
            time.sleep(5)
    print("** %d 秒内 9881 没起来 **（模型加载可能更久，看日志）" % timeout)
    return 1

def show_logs():
    for f in ("indextts_out.log", "indextts_err.log"):
        p = os.path.join(LOGDIR, f)
        if os.path.exists(p):
            print("--- %s 尾部 ---" % f)
            print(open(p, "rb").read()[-2000:].decode("utf-8", "replace"))

def main():
    print("寻找可用的 IndexTTS 安装：")
    root, py, ck_size = pick()
    if not py:
        print("两套候选都没找到可用的 python")
        return 1
    if ck_size < 1.0:
        print("\n【预检警告】%s\\checkpoints\\gpt.pth 只有 %.2fGB，模型可能还没下完。" % (root, ck_size))
        print("下载命令（走 ModelScope，HF 直连不通）：")
        print(r'  "%s" -m indextts.cli_v2 download --source modelscope --model-dir "%s\checkpoints"'
              % (py, root))
        print("仍会尝试启动，若加载失败请看日志。\n")

    os.makedirs(LOGDIR, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["INDEXTTS_ROOT"] = root
    env["INDEXTTS_PORT"] = str(PORT)
    env.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    env.pop("HF_HUB_OFFLINE", None)
    env.pop("TRANSFORMERS_OFFLINE", None)


    out_f = open(os.path.join(LOGDIR, "indextts_out.log"), "wb")
    err_f = open(os.path.join(LOGDIR, "indextts_err.log"), "wb")


    flags = fairy_root.NOWIN_FLAGS
    p = subprocess.Popen([py, SERVER], cwd=root, env=env, stdout=out_f, stderr=err_f,
                         stdin=subprocess.DEVNULL, creationflags=flags, close_fds=True)
    open(PIDFILE, "w").write(str(p.pid))
    print("已启动 IndexTTS 服务，PID = %d" % p.pid)
    print("  root   : %s" % root)
    print("  解释器 : %s" % py)
    print("等待模型加载…")
    rc = probe()
    if rc:
        show_logs()
    return rc

if __name__ == "__main__":
    sys.exit(main())
