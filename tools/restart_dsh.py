# -*- coding: utf-8 -*-
"""restart_dsh.py -- 重启 DSH（DeepSeek Harness）桌面程序。

为什么单独写这个：
  · DSH 的插件代码（node_modules 里的副本）只有在【重启 DSH】后才重新加载，
    改完插件源码光刷新页面没用。
  · 但在 DSH 里直接 kill 它自己 = 自杀：本进程也在那棵树里，所以必须
    用【完全脱离】的方式起（见 launch_detached / tools\\restart_dsh.py 的调用方），
    并且延迟几秒，好让当前对话把话说完。

用法：
  pythonw D:\\FAIRY\\tools\\restart_dsh.py [延迟秒数，默认 30]

它会写日志到 D:\\FAIRY\\logs\\dsh_restart.log（重启后可以回看是否成功）。
★ 只用 pythonw 跑，绝不弹黑窗（本项目的红线）。
"""
import os
import socket
import subprocess
import sys
import time
from datetime import datetime

LOG = r"D:\FAIRY\logs\dsh_restart.log"
EXE = os.path.join(os.path.expanduser("~"), "AppData", "Local", "Programs",
                   "DeepSeek Harness", "DeepSeek Harness.exe")
PORT = 19387

# ★ 常量别记错：0x08000000 = CREATE_NO_WINDOW（不是 0x00000200）
CREATE_NO_WINDOW = 0x08000000
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


def port_open(port, timeout=0.6):
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


def run(cmd, timeout=60):
    """跑一条命令，不弹窗、不抛异常。"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           creationflags=CREATE_NO_WINDOW)
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except Exception as e:
        return -1, "%s: %s" % (type(e).__name__, e)


def main():
    delay = 30
    if len(sys.argv) > 1:
        try:
            delay = max(0, int(sys.argv[1]))
        except Exception:
            pass
    log("=== 计划重启 DSH（延迟 %ds）===" % delay)
    if not os.path.isfile(EXE):
        log("!! 找不到可执行文件，放弃：%s" % EXE)
        return 1
    log("等待 %d 秒，好让当前对话把话说完…" % delay)
    time.sleep(delay)

    rc, out = run(["tasklist", "/fi", "imagename eq DeepSeek Harness.exe", "/fo", "csv", "/nh"])
    n_before = len([l for l in out.splitlines() if "DeepSeek" in l])
    log("关闭前 DSH 进程数 = %d" % n_before)
    log("19387 关闭前是否在听 = %s" % port_open(PORT))

    rc, out = run(["taskkill", "/IM", "DeepSeek Harness.exe", "/T", "/F"], timeout=90)
    log("taskkill rc=%d out=%s" % (rc, out[:300]))

    # 等端口释放（最多 30 秒）
    freed = False
    for i in range(60):
        time.sleep(0.5)
        if not port_open(PORT):
            freed = True
            log("19387 已释放（%.1fs）" % (0.5 * (i + 1)))
            break
    if not freed:
        log("!! 19387 30 秒后仍在监听 —— 进程可能没杀干净，仍然继续尝试重开")

    time.sleep(2.0)   # 给 Electron 一点收尾时间，避免单实例锁没放开
    try:
        subprocess.Popen([EXE], cwd=os.path.dirname(EXE), close_fds=True,
                         creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)
        log("已重新拉起：%s" % EXE)
    except Exception as e:
        log("!! 重新拉起失败：%s: %s" % (type(e).__name__, e))
        return 1

    ok = False
    for i in range(120):
        time.sleep(1.0)
        if port_open(PORT):
            ok = True
            log("★ 19387 已恢复监听（%.0fs）—— DSH 重启成功" % (i + 1))
            break
    if not ok:
        log("!! 120 秒内 19387 没起来 —— 请手动从开始菜单启动 DeepSeek Harness")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
