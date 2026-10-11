# -*- coding: utf-8 -*-
"""fairy_shutdown_all.py -- 一键【全部退出】：球 + 守护 + 本地服务 + 语音 + 本地运算。

谁调用它：球的右键菜单「全部退出 Fairy（球+服务+语音）」。
为什么单独一个脚本：球自己也要关，而"自己关自己"时 destroy() 之后的代码不保证跑完
  -> 所以球先把本脚本【脱离地】送出去，再关自己，剩下的事这里收尾。

用法（平时不用手敲）：
    python fairy_shutdown_all.py                # 全套：tts -> guard -> host -> 本地运算 -> ball
    python fairy_shutdown_all.py --keep-ball    # 留着球（只收后台）
延迟可用环境变量 FAIRY_SHUTDOWN_DELAY 改（默认 2.5 秒，留给球把提示说完）。
"""
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
CREATE_NO_WINDOW = 0x08000000            # ★ 不是 0x200
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
    """跑一条命令（不弹窗），返回一行摘要。"""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace",
                           creationflags=CREATE_NO_WINDOW)
        out = ((p.stdout or "") + (p.stderr or "")).strip().replace("\n", " | ")
        return out[:200] if out else "(无输出)"
    except Exception as e:
        return "ERR %s: %s" % (type(e).__name__, e)


def stop_strata():
    """停本地运算（Strata，8080）：先 POST /v1/unload 优雅卸载模型，再杀掉监听 8080 的
    服务进程。★ 必须杀进程：unload 只卸模型，server.py 还在监听，引擎随时会被重新 load
    （这正是"点了全部退出 Strata 又跑起来"的根因）。★ 绝不抛异常。"""
    out = []
    try:
        req = urllib.request.Request(STRATA_UNLOAD, data=b"{}",
                                     headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            out.append("unload=" + r.read().decode("utf-8", "replace")[:100])
    except Exception as e:
        out.append("unload跳过(%s)" % str(e)[:50])
    # 杀监听 8080 的进程（netstat 拿 PID，taskkill 强杀）
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
    # 顺序有讲究：语音 -> 守护（否则守护会立刻把 9881 又拉起来）-> 本地服务 -> 本地运算 -> 球
    for name in ("tts", "guard", "host"):
        log("stop %-5s: %s" % (name, run([sys.executable, FAIRY, "stop", name])))
    log("本地运算 : %s" % stop_strata())
    if not keep_ball:
        log("stop ball : %s" % run([sys.executable, FAIRY, "stop", "ball"]))
    log("=== 全部退出完成 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
