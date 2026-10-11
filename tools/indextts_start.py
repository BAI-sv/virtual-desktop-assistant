# -*- coding: utf-8 -*-
"""
拉起 IndexTTS 常驻合成服务，并等 9881 端口就绪。

优先用 NAI-xiao（晓子ya 整合包）里的【官方 IndexTTS 2.5 + ROCm GPU】，
它不可用时回退到老的 D:\\Index-tts-2.0-Windows-AMD（CPU）。

用法： py -3.12 tools\\indextts_start.py
日志： D:\\FAIRY\\logs\\indextts_out.log / indextts_err.log
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
import fairy_root

# ★★ 2026-10-10【编码兜底】stdout/stderr 一律切成 utf-8 + errors=replace。
#   【为什么必须加】本脚本由 fairy.py 用 pythonw 起、stdout/stderr 被重定向成
#   【文件/管道】，此时 sys.stdout.encoding = 'gbk'。本文件的 print 里有 '✓' 之类
#   的字符 —— 编不出来就抛 UnicodeEncodeError，把整个启动流程打断（和
#   indextts_guard.py 那个实测崩溃同一个根因，见那边的 _safe_stdio 说明）。
for _s in (sys.stdout, sys.stderr):
    try:
        if _s is not None:
            _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_HERE = os.path.dirname(os.path.abspath(__file__))

# (根目录, venv 相对该根目录的路径)  —— 按优先级排列
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
        # ★ 2026-10-10：优先用 pythonw.exe（无控制台）。
        #   原因：venv \Scripts\python.exe 只是个启动器，
        #   它会按 pyvenv.cfg 的 home 去找真正的 python.exe 【重新启动自己】，
        #   而那次二次启动是 venv 内部做的——creationflags 管不到，
        #   于是下层进程会挂一个 conhost，弹出黑窗/终端窗口。
        #   pythonw.exe 本身就不创建控制台，彻底解决 ✓✓
        py = os.path.join(root, venv_rel, "Scripts", "pythonw.exe")
        if not os.path.exists(py):
            py = os.path.join(root, venv_rel, "Scripts", "python.exe")   # 回退
        if not os.path.exists(py):
            py = os.path.join(root, venv_rel, "pythonw.exe")     # 老包是 env\python.exe
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
    # ROCm 路径由 indextts_server.setup_rocm_env() 自己配（必须在 import torch 之前）

    out_f = open(os.path.join(LOGDIR, "indextts_out.log"), "wb")
    err_f = open(os.path.join(LOGDIR, "indextts_err.log"), "wb")
    # ★ 2026-10-10【黑窗】即使 pick() 没找到 pythonw.exe 而回退到 python.exe，
    #   这三个 flag 也能把它按住不弹窗（CREATE_NO_WINDOW 才是"不显示窗口"那个）。
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
