# -*- coding: utf-8 -*-
"""fairy_dsh_watch.py -- 常驻监视：一旦发现 DSH(DeepSeek Harness, 19387) 启动，就自动拉起 Fairy。

★ 2026-10-17 用户要求「打开 DSH 自动打开 fairy」。
配合 fairy_dsh_start.py（开机/主动启动用）：
  · 启动器管"主动启动 DSH"；本 watcher 管"任何方式打开 DSH 都拉 Fairy"（含手动双击 exe）。
  · fairy.py start 自带幂等——已在跑的组件会跳过，重复调用无副作用。
  · 常驻轮询（每 2 秒一次端口探测，开销可忽略）。随开机自启（HKCU Run 的 FairyWatch）。
"""
import os
import socket
import subprocess
import sys
import time

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
FAIRY = os.path.join(TOOLS, "fairy.py")
CREATE_NO_WINDOW = 0x08000000
POLL = 2.0


def log(msg):
    try:
        os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
        with open(os.path.join(ROOT, "logs", "dsh_watch.log"), "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def dsh_up():
    try:
        s = socket.socket()
        s.settimeout(0.5)
        ok = (s.connect_ex(("127.0.0.1", 19387)) == 0)
        s.close()
        return ok
    except Exception:
        return False


def main():
    log("== FairyWatch 启动：监视 DSH(19387)，DSH 一开就拉 Fairy ==")
    prev = dsh_up()
    while True:
        cur = dsh_up()
        if cur and not prev:
            log("检测到 DSH 启动 -> 拉起 Fairy")
            try:
                subprocess.Popen([sys.executable, FAIRY, "start"], cwd=TOOLS,
                                 creationflags=CREATE_NO_WINDOW, close_fds=True)
            except Exception as e:
                log("拉起 Fairy 失败: %s" % type(e).__name__)
        prev = cur
        time.sleep(POLL)


if __name__ == "__main__":
    main()
