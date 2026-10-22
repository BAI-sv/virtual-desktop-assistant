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
import json
import socket
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
    # ★ 2026-10-14【静默优化】Strata 未运行(8080 未监听)时跳过 unload，不记连接失败噪音
    _s_up = False
    try:
        _s = socket.socket()
        _s.settimeout(0.5)
        _s_up = (_s.connect_ex(("127.0.0.1", 8080)) == 0)
        _s.close()
    except Exception:
        _s_up = False
    if _s_up:
        try:
            req = urllib.request.Request(STRATA_UNLOAD, data=b"{}",
                                         headers={"content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                out.append("unload=" + r.read().decode("utf-8", "replace")[:100])
        except Exception:
            out.append("unload跳过")   # 已探测在线仍失败：记简短结果，不含 WinError 噪音
    # else: Strata 未运行，跳过 unload（进程由下方 netstat 兜底清，无多余噪音）
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


def stop_engines():
    """停组合引擎（Strata 之外的）：Ollama 模型卸载释放显存 + 视觉引擎(8085)
    + llama-server / koboldcpp / ollama.exe 进程。全部退出时一次收干净。"""
    out = []
    # 1) 卸载 ollama 已加载模型（keep_alive=0 -> 立即释放显存，进程保留到下一步再杀）
    #    ★ 2026-10-14【Ollama 已删除】先探测 11434：Ollama 未运行就静默跳过，
    #      不记录「ollama跳过(连接失败)」噪音（Ollama 不再使用，日志保持干净）。
    _ollama_up = False
    try:
        _s = socket.socket()
        _s.settimeout(0.5)
        _ollama_up = (_s.connect_ex(("127.0.0.1", 11434)) == 0)
        _s.close()
    except Exception:
        _ollama_up = False
    if _ollama_up:
        try:
            with urllib.request.urlopen(
                    "http://127.0.0.1:11434/api/tags", timeout=4) as r:
                tags = json.loads(r.read().decode("utf-8", "replace"))
            unloaded = []
            for m in (tags.get("models") or []):
                mn = m.get("name") or m.get("model") or ""
                if not mn:
                    continue
                try:
                    g = urllib.request.Request(
                        "http://127.0.0.1:11434/api/generate",
                        data=json.dumps({"model": mn, "keep_alive": 0}).encode("utf-8"),
                        headers={"content-type": "application/json"})
                    with urllib.request.urlopen(g, timeout=10) as r2:
                        r2.read()
                    unloaded.append(mn)
                except Exception:
                    pass
            out.append("ollama卸载=%s" % (unloaded or "无"))
        except Exception:
            pass   # 卸载失败静默（进程由第 3 步兜底清）
    # else: Ollama 未运行，跳过（不记录，避免误导）
    # 2) 杀 8085 视觉引擎（llama.cpp）
    try:
        np_ = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             timeout=20, encoding="utf-8", errors="replace",
                             creationflags=CREATE_NO_WINDOW)
        pids = set()
        for line in np_.stdout.splitlines():
            if ":8085" in line and ("LISTENING" in line or "ESTABLISHED" in line):
                parts = line.split()
                if parts and parts[-1].isdigit():
                    pids.add(int(parts[-1]))
        for pid in sorted(pids):
            try:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                               capture_output=True, timeout=10,
                               creationflags=CREATE_NO_WINDOW)
            except Exception:
                pass
        out.append("杀8085=%s" % (sorted(pids) or "无"))
    except Exception as e:
        out.append("杀8085失败(%s)" % str(e)[:50])
    # 3) 兜底：常驻引擎进程
    for exe in ("llama-server.exe", "koboldcpp.exe", "ollama.exe"):
        try:
            subprocess.run(["taskkill", "/F", "/IM", exe],
                           capture_output=True, timeout=10,
                           creationflags=CREATE_NO_WINDOW)
        except Exception:
            pass
    out.append("引擎进程已清理")
    return " | ".join(out)


def stop_remains():
    """★ 2026-10-12【退出收尾】把没被组件名覆盖的服务也退干净：
    ① 网页对话代理 8090（fairy_chat.py，由球拉起、不在 fairy.py 组件表里）
    ② 兜底：命令行含 D:\\FAIRY\\tools 的 python/pythonw 残留
      （IndexTTS 服务进程、耳朵、大脑等），排除本脚本自身与球
      （球由 stop ball 单独收尾，--keep-ball 时保留）。
    """
    out = []
    # 1) 8090 网页对话代理
    try:
        np_ = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             timeout=20, encoding="utf-8", errors="replace",
                             creationflags=CREATE_NO_WINDOW)
        pids = set()
        for line in np_.stdout.splitlines():
            if ":8090" in line and ("LISTENING" in line or "ESTABLISHED" in line):
                parts = line.split()
                if parts and parts[-1].isdigit():
                    pids.add(int(parts[-1]))
        for pid in sorted(pids):
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               capture_output=True, timeout=10,
                               creationflags=CREATE_NO_WINDOW)
            except Exception:
                pass
        out.append("杀8090=%s" % (sorted(pids) or "无"))
    except Exception as e:
        out.append("杀8090失败(%s)" % str(e)[:50])
    # 2) 兜底：残留 Fairy 脚本进程（排除本脚本与球）
    try:
        ps = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | "
             "Where-Object {$_.CommandLine -like '*D:\\FAIRY\\tools*' -and "
             "$_.CommandLine -notlike '*fairy_shutdown_all*' -and "
             "$_.CommandLine -notlike '*fairy_ball.py*'} | "
             "ForEach-Object { $_.ProcessId }"],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
        kills = []
        for pid in (ps.stdout or "").split():
            pid = pid.strip()
            if pid.isdigit():
                try:
                    subprocess.run(["taskkill", "/F", "/PID", pid],
                                   capture_output=True, timeout=10,
                                   creationflags=CREATE_NO_WINDOW)
                    kills.append(pid)
                except Exception:
                    pass
        out.append("兜底杀=%s" % (kills or "无"))
    except Exception as e:
        out.append("兜底失败(%s)" % str(e)[:50])
    return " | ".join(out)


def stop_dsh():
    """★ 2026-10-17【DSH 联动】关闭 fairy 时把 DSH（DeepSeek Harness）一起关掉，
    让「全部退出 Fairy」与 DSH 两相关联。
    先杀监听 19387 的进程（netstat 拿 PID），再兜底杀 DeepSeek Harness.exe。绝不抛异常。
    注意：DSH 是 Fairy 的内核，关掉后球/服务不会再被拉起来；keep-ball 场景不调用。"""
    out = []
    try:
        np_ = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                             timeout=20, encoding="utf-8", errors="replace",
                             creationflags=CREATE_NO_WINDOW)
        pids = set()
        for line in np_.stdout.splitlines():
            if ":19387" in line and ("LISTENING" in line or "ESTABLISHED" in line):
                parts = line.split()
                if parts and parts[-1].isdigit():
                    pids.add(int(parts[-1]))
        killed = []
        for pid in sorted(pids):
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               capture_output=True, timeout=10,
                               creationflags=CREATE_NO_WINDOW)
                killed.append(pid)
            except Exception:
                pass
        out.append("杀DSH(19387)=%s" % (killed or "无"))
    except Exception as e:
        out.append("杀DSH失败(%s)" % str(e)[:50])
    try:
        subprocess.run(["taskkill", "/F", "/IM", "DeepSeek Harness.exe"],
                       capture_output=True, timeout=10,
                       creationflags=CREATE_NO_WINDOW)
        out.append("DeepSeek Harness 进程已关")
    except Exception:
        pass
    return " | ".join(out)


def main():
    keep_ball = "--keep-ball" in sys.argv
    try:
        delay = float(os.environ.get("FAIRY_SHUTDOWN_DELAY", "2.5"))
    except Exception:
        delay = 2.5
    log("=== 全部退出开始（延迟 %.1fs，keep_ball=%s）===" % (delay, keep_ball))
    time.sleep(max(0.0, delay))
    # 顺序有讲究：语音 -> 耳朵 -> 守护（否则守护会立刻把 9881 又拉起来）
    #   -> 本地服务 -> 本地运算 -> 引擎 -> 残留 -> 球
    for name in ("tts", "ear", "guard", "host"):
        log("stop %-5s: %s" % (name, run([sys.executable, FAIRY, "stop", name])))
    log("本地运算 : %s" % stop_strata())
    log("组合引擎 : %s" % stop_engines())
    log("残留清理 : %s" % stop_remains())
    if not keep_ball:
        log("stop ball : %s" % run([sys.executable, FAIRY, "stop", "ball"]))
        # ★ 2026-10-17【DSH 联动】用户要求：关 fairy 时 DSH 一起关（两相关联）
        log("DSH 联动 : %s" % stop_dsh())
    log("=== 全部退出完成 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
