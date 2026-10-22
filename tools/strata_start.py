# -*- coding: utf-8 -*-
"""strata_start.py -- 把本机 Strata（8080）拉起来，且【不挂在 DSH 进程树上】。

为什么要单独一个：
  · 2026-10-11 踩过：我从 DSH 的子进程里起 Strata，DSH 一重启（taskkill /T）
    就把它一起带走了 -> DSH 里选 strata 模型就报 Connection error。
  · 所以起它必须走【WMI】那条路（父进程 = WmiPrvSE），本脚本就是给 WMI 调的：
      powershell: Invoke-CimMethod Win32_Process -MethodName Create -Arguments @{
                    CommandLine = '"<pythonw>" "D:\\FAIRY\\tools\\strata_start.py"' }
  · 命令行复用球自己的 _strata_cmd()（那里已经修成用 pythonw，不会弹黑窗）。

用法（人也可以直接跑）：
    py -3.12 D:\\FAIRY\\tools\\strata_start.py
★ 只用 pythonw 起内层；本脚本不 print（可能跑在无控制台的 pythonw 下）。
"""
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
LOG = os.path.join(ROOT, "logs", "strata_start.log")
SERVER_LOG = os.path.join(ROOT, "logs", "strata_server.log")
PORT = 8080

CREATE_NO_WINDOW = 0x08000000        # ★ 不是 0x200
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


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
        log("8080 已经在跑，不动")
        return 0
    sys.path.insert(0, TOOLS)
    try:
        import fairy_ball as fb          # 复用球自己的命令（含 pythonw 修复）
        cmd = fb._strata_cmd()
    except Exception as e:
        log("取启动命令失败：%s: %s" % (type(e).__name__, e))
        return 1
    if not cmd:
        log("没找到 Strata 安装目录（可在 fairy_root.json 配 strata_dir）")
        return 1
    log("拉起：%s" % " ".join(cmd))
    lf = open(SERVER_LOG, "ab")
    try:
        subprocess.Popen(cmd, cwd=r"E:\Strata", stdout=lf, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL,
                         creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW,
                         close_fds=True)
    except Exception as e:
        log("拉起失败：%s: %s" % (type(e).__name__, e))
        return 1
    finally:
        lf.close()

    # ★ 2026-10-15【用户要求】Strata 启动成功后，自动在浏览器打开 8080 网页对话页。
    #   等 8080 就绪（模型加载较慢，几十秒）再开，避免浏览器先弹出报“无法访问”。
    #   用后台线程 + 主进程 join：WMI 是异步调用，调用方不会因这里的等待被阻塞。
    def _open_browser_when_ready():
        deadline = time.time() + 120
        while time.time() < deadline:
            if port_open(PORT, timeout=0.6):
                try:
                    webbrowser.open("http://127.0.0.1:%d" % PORT)
                    log("已自动打开浏览器对话页: http://127.0.0.1:%d" % PORT)
                except Exception as e:
                    log("打开浏览器失败: %s: %s" % (type(e).__name__, e))
                return
            time.sleep(2)
        log("120 秒内 8080 未就绪，未自动打开浏览器")

    _t = threading.Thread(target=_open_browser_when_ready, daemon=True)
    _t.start()
    _t.join(timeout=125)   # 主进程活到浏览器打开为止
    return 0


if __name__ == "__main__":
    sys.exit(main())
