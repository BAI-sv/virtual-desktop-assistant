# -*- coding: utf-8 -*-
"""Fairy 安装器 —— 双击运行，自动检测并安装 Fairy 的依赖。

设计：
  1. 不打包第三方库，全部从国内镜像现下（清华 -> 阿里 -> 中科大 -> 腾讯）
  2. 实时进度：总进度按「已下载体积」，下面一行显示当前包 + 下载速度
  3. 装不上不中断：可选依赖失败只标黄，必需依赖失败才报错
  4. 装完自动校验（逐个 import），成功后写 install_ok.json

用法：
    Fairy.exe            图形界面
    Fairy.exe --check    只检测（命令行）
"""
import json
import os
import platform
import queue
import re
import subprocess
import sys
import threading
import time
from tkinter import ttk
import tkinter as tk

APP = "Fairy"
VER = "1.0"

# ---------------- 依赖清单 ----------------
# (pip 名, import 名, 是否必需, 大约体积 MB, 说明)
DEPS = [
    ("numpy",           "numpy",          True,  31,  "数值计算"),
    ("Pillow",          "PIL",            True,  15,  "图像绘制（球、气泡）"),
    ("psutil",          "psutil",         True,   1,  "进程与系统监控"),
    ("requests",        "requests",       True,   1,  "HTTP 请求"),
    ("pygame",          "pygame",         True,  21,  "提示音/音效"),
    ("pywin32",         "win32api",       True,   9,  "Windows 窗口与系统 API"),
    ("edge-tts",        "edge_tts",       True,   1,  "兜底语音合成"),
    ("faster-whisper",  "faster_whisper", True,  61,  "语音转写"),
    ("sounddevice",     "sounddevice",    False,  1,  "录音/播放"),
    ("PyAudio",         "pyaudio",        False,  1,  "麦克风录音"),
    ("PyAutoGUI",       "pyautogui",      False,  1,  "截图/屏幕操作"),
    ("sherpa-onnx",     "sherpa_onnx",    False, 28,  "语音唤醒关键词检测"),
    ("imageio-ffmpeg",  "imageio_ffmpeg", False, 84,  "自带 ffmpeg 二进制"),
    ("opencv-python",   "cv2",            False,112,  "图像识别"),
]

MIRRORS = [
    ("清华", "https://pypi.tuna.tsinghua.edu.cn/simple"),
    ("阿里", "https://mirrors.aliyun.com/pypi/simple"),
    ("中科大", "https://pypi.mirrors.ustc.edu.cn/simple"),
    ("腾讯", "https://mirrors.cloud.tencent.com/pypi/simple"),
]

RE_DOWNLOAD = re.compile(r"Downloading\s+(\S+)\s+\(([\d.]+)\s*(kB|MB|GB)\)")
RE_SPEED = re.compile(r"([\d.]+)\s*(kB|MB|GB)/s")


def vol_to_mb(num, unit):
    n = float(num)
    if unit == "kB":
        return n / 1024.0
    if unit == "GB":
        return n * 1024.0
    return n


def find_python():
    """找一个能用的 Python。优先 py -3.12。"""
    cands = ([["py", "-3.12"], ["py", "-3"], ["python"], ["python3"]]
             if platform.system() == "Windows" else [["python3"], ["python"]])
    for c in cands:
        try:
            r = subprocess.run(c + ["-c", "import sys;print(sys.version.split()[0])"],
                               capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and r.stdout.strip():
                return c, r.stdout.strip()
        except Exception:
            continue
    return None, None


def installed_map(py):
    out = {}
    try:
        r = subprocess.run(py + ["-m", "pip", "list", "--format=json"],
                           capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            for item in json.loads(r.stdout):
                out[item["name"].lower().replace("_", "-")] = item["version"]
    except Exception:
        pass
    return out


class Installer:
    def __init__(self, root):
        self.root = root
        self.q = queue.Queue()
        self.stop = threading.Event()
        self.total_mb = 0.0
        self.done_mb = 0.0
        self.cur_speed = 0.0
        self.ok_count = 0
        self.fail = []
        self._build()
        self.root.after(80, self._pump)
        threading.Thread(target=self._worker, daemon=True).start()

    # ---------------- UI ----------------
    def _build(self):
        r = self.root
        r.title("%s 安装器 v%s" % (APP, VER))
        r.geometry("720x540")
        r.configure(bg="#0e1621")
        r.resizable(False, False)
        # 窗口图标：优先用 assets\Fairy.ico，没有就退回内置的球图
        _ico = None
        for _base in (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
                      else os.path.dirname(os.path.abspath(__file__)),
                      r"D:\FAIRY\assets", os.getcwd()):
            _cand = os.path.join(_base, "Fairy.ico")
            if os.path.exists(_cand):
                _ico = _cand
                break
        if _ico:
            try:
                r.iconbitmap(_ico)
            except Exception:
                pass
        # 任务栏也不显示图标组（Win 下配合 AppUserModelID 用）
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Fairy.Setup")
        except Exception:
            pass

        tk.Label(r, text="Fairy 依赖安装", bg="#0e1621", fg="#7ec8ff",
                 font=("Microsoft YaHei", 20, "bold")).pack(pady=(18, 2))
        tk.Label(r, text="自动检测缺什么、缺什么装什么 · 国内镜像加速",
                 bg="#0e1621", fg="#8ba3bd", font=("Microsoft YaHei", 10)).pack()

        self.lb_title = tk.Label(r, text="准备中…", bg="#0e1621", fg="#dfe9f5",
                                 font=("Microsoft YaHei", 11, "bold"), anchor="w")
        self.lb_title.pack(fill="x", padx=28, pady=(16, 4))

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("F.Horizontal.TProgressbar", troughcolor="#182430",
                        background="#2f8fd8", bordercolor="#182430",
                        lightcolor="#2f8fd8", darkcolor="#2f8fd8", thickness=22)
        self.pb = ttk.Progressbar(r, style="F.Horizontal.TProgressbar",
                                  orient="horizontal", length=664,
                                  mode="determinate", maximum=100)
        self.pb.pack(padx=28, pady=(0, 6))

        self.lb_sub = tk.Label(r, text="", bg="#0e1621", fg="#6f8aa6",
                               font=("Consolas", 9), anchor="w")
        self.lb_sub.pack(fill="x", padx=28)

        frame = tk.Frame(r, bg="#0e1621")
        frame.pack(fill="both", expand=True, padx=28, pady=(12, 8))
        self.txt = tk.Text(frame, bg="#0a111a", fg="#9fb6cc", bd=0,
                           font=("Consolas", 9), height=13, wrap="word")
        sb = tk.Scrollbar(frame, command=self.txt.yview, bg="#182430")
        self.txt.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.txt.pack(side="left", fill="both", expand=True)
        for tag, col in (("ok", "#4ade80"), ("err", "#f87171"),
                         ("warn", "#fbbf24"), ("info", "#7ec8ff")):
            self.txt.tag_configure(tag, foreground=col)

        bar = tk.Frame(r, bg="#0e1621")
        bar.pack(fill="x", padx=28, pady=(0, 16))
        self.lb_stat = tk.Label(bar, text="", bg="#0e1621", fg="#8ba3bd",
                                font=("Microsoft YaHei", 10), anchor="w")
        self.lb_stat.pack(side="left")
        self.btn = tk.Button(bar, text="关闭", command=self._on_close,
                             bg="#1b2a3a", fg="#cfe2f5", bd=0, padx=18, pady=6,
                             font=("Microsoft YaHei", 10), activebackground="#24405c")
        self.btn.pack(side="right")

    def log(self, msg, tag=""):
        self.txt.insert("end", msg + "\n", tag)
        self.txt.see("end")

    # ---------------- 流程 ----------------
    def _worker(self):
        try:
            self._run()
        except Exception as e:
            import traceback
            self.q.put(("log", "安装器异常：%s" % e, "err"))
            self.q.put(("log", traceback.format_exc(), "err"))
            self.q.put(("state", "异常退出"))

    def _run(self):
        self.q.put(("log", "系统: %s %s" % (platform.system(), platform.release()), "info"))

        py, vinfo = find_python()
        if not py:
            self.q.put(("log", "✗ 没有找到 Python", "err"))
            self.q.put(("log", "  请先装 Python 3.10+：https://www.python.org/downloads/", "warn"))
            self.q.put(("log", "  安装时务必勾选 Add Python to PATH", "warn"))
            self.q.put(("state", "缺 Python"))
            return
        self.q.put(("log", "✓ Python %s  (%s)" % (vinfo, " ".join(py)), "ok"))

        try:
            r = subprocess.run(py + ["-m", "pip", "--version"],
                               capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                self.q.put(("log", "！ pip 不可用，尝试 ensurepip", "warn"))
                subprocess.run(py + ["-m", "ensurepip", "--upgrade"],
                               capture_output=True, text=True, timeout=120)
        except Exception as e:
            self.q.put(("log", "！ pip 检测失败: %s" % e, "warn"))

        have = installed_map(py)
        self.q.put(("log", "已装 %d 个包" % len(have), "info"))

        todo = []
        for pipname, imp, need, mb, desc in DEPS:
            key = pipname.lower().replace("_", "-")
            if key in have:
                self.q.put(("log", "✓ %-16s 已装 %s" % (pipname, have[key]), "ok"))
                self.ok_count += 1
            else:
                todo.append((pipname, imp, need, mb, desc))

        if not todo:
            self.q.put(("log", "", ""))
            self.q.put(("log", "全部依赖已就绪，无需安装。", "ok"))
            self.q.put(("state", "完成"))
            self.q.put(("done", 100))
            self._write_marker(py)
            return

        self.total_mb = sum(t[3] for t in todo)
        self.q.put(("log", "", ""))
        self.q.put(("log", "需要安装 %d 个包，约 %.0f MB" % (len(todo), self.total_mb), "info"))
        self.q.put(("state", "安装中 0/%d" % len(todo)))

        for i, (pipname, imp, need, mb, desc) in enumerate(todo, 1):
            if self.stop.is_set():
                break
            self.q.put(("state", "安装中 %d/%d · %s" % (i, len(todo), pipname)))
            self.q.put(("log", "", ""))
            self.q.put(("log", "[%d/%d] %s  (%s, ~%d MB)" % (i, len(todo), pipname, desc, mb), "info"))
            ok = self._install_one(py, pipname)
            if ok:
                self.ok_count += 1
                self.done_mb += mb
                self.q.put(("log", "  ✓ %s 装好了" % pipname, "ok"))
            else:
                self.fail.append((pipname, need))
                self.q.put(("log", "  %s %s 安装失败%s" %
                            ("✗" if need else "!", pipname,
                             "（必需）" if need else "（可选，对应功能不可用）"),
                            "err" if need else "warn"))
            pct = (self.done_mb / self.total_mb * 100.0) if self.total_mb else 100.0
            self.q.put(("done", min(99.0, pct)))

        self.q.put(("log", "", ""))
        self.q.put(("log", "校验安装结果…", "info"))
        bad = []
        for pipname, imp, need, mb, desc in DEPS:
            try:
                r = subprocess.run(py + ["-c", "import %s" % imp],
                                   capture_output=True, text=True, timeout=30)
                good = (r.returncode == 0)
            except Exception:
                good = False
            if good:
                self.q.put(("log", "  ✓ import %s" % imp, "ok"))
            else:
                self.q.put(("log", "  ✗ import %s 失败" % imp, "err" if need else "warn"))
                bad.append((pipname, need))

        hard = [b for b in bad if b[1]]
        if hard:
            self.q.put(("log", "", ""))
            self.q.put(("log", "必需依赖仍缺：%s" % ", ".join(b[0] for b in hard), "err"))
            self.q.put(("state", "未完成"))
        else:
            self.q.put(("log", "", ""))
            self.q.put(("log", "全部必需依赖就绪 ✓", "ok"))
            self.q.put(("state", "完成"))
            self.q.put(("done", 100))
            self._write_marker(py)

    def _install_one(self, py, pipname):
        for name, url in MIRRORS:
            if self.stop.is_set():
                return False
            self.q.put(("log", "  试 %s 源…" % name, ""))
            host = url.split("//")[1].split("/")[0]
            cmd = py + ["-m", "pip", "install", pipname, "-i", url,
                        "--trusted-host", host, "--disable-pip-version-check",
                        "--no-input", "--no-warn-script-location"]
            try:
                p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True,
                                     bufsize=1, encoding="utf-8", errors="replace")
            except Exception as e:
                self.q.put(("log", "    启动失败: %s" % e, "err"))
                continue
            speed = 0.0
            for line in p.stdout:
                line = line.rstrip()
                if not line:
                    continue
                m = RE_SPEED.search(line)
                if m:
                    speed = vol_to_mb(m.group(1), m.group(2))
                    self.cur_speed = speed
                m = RE_DOWNLOAD.search(line)
                if m:
                    self.q.put(("sub", "%s  %.1f MB  %.1f MB/s" %
                                (os.path.basename(m.group(1))[:44],
                                 vol_to_mb(m.group(2), m.group(3)), speed)))
                if line.startswith("Successfully installed"):
                    self.q.put(("log", "  " + line, "ok"))
                elif "ERROR:" in line:
                    self.q.put(("log", "  " + line[:120], "err"))
            p.wait()
            if p.returncode == 0:
                return True
            self.q.put(("log", "    %s 源失败（退出码 %d）" % (name, p.returncode), "warn"))
        return False

    def _write_marker(self, py):
        try:
            base = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else __file__)
            p = os.path.join(base, "install_ok.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"ok": True, "at": time.strftime("%Y-%m-%d %H:%M:%S"),
                           "python": " ".join(py)}, f, ensure_ascii=False, indent=1)
            self.q.put(("log", "标记: %s" % p, "info"))
        except Exception:
            pass

    # ---------------- UI 刷新 ----------------
    def _pump(self):
        try:
            while True:
                item = self.q.get_nowait()
                kind = item[0]
                if kind == "log":
                    self.log(item[1], item[2] if len(item) > 2 else "")
                elif kind == "state":
                    self.lb_title.configure(text=item[1])
                elif kind == "sub":
                    self.lb_sub.configure(text=item[1])
                elif kind == "done":
                    self.pb["value"] = item[1]
                    self.lb_sub.configure(text="总进度 %.0f%%  %.1f MB/s" %
                                          (item[1], self.cur_speed))
        except queue.Empty:
            pass
        try:
            self.lb_stat.configure(
                text="已装 %d 个 · 失败 %d 个" % (self.ok_count, len(self.fail)))
        except Exception:
            pass
        self.root.after(80, self._pump)

    def _on_close(self):
        self.stop.set()
        self.root.after(150, self.root.destroy)


def main_cli():
    # ★ 2026-10-19【修】Windows 控制台默认 GBK，print 勾/叉会抛 UnicodeEncodeError，
    #   直接把 stdout 切到 utf-8 并忽略无法编码的字符。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    py, vinfo = find_python()
    print("%s 安装器 v%s" % (APP, VER))
    print("系统: %s %s" % (platform.system(), platform.release()))
    if not py:
        print("✗ 没有找到 Python")
        return 1
    print("✓ Python %s (%s)" % (vinfo, " ".join(py)))
    have = installed_map(py)
    miss = 0
    for pipname, imp, need, mb, desc in DEPS:
        key = pipname.lower().replace("_", "-")
        if key in have:
            print("  ✓ %-16s %s" % (pipname, have[key]))
        else:
            print("  ✗ %-16s 缺（%s %d MB）" % (pipname, "必需" if need else "可选", mb))
            miss += 1
    print()
    print("全部就绪" if miss == 0 else "缺 %d 个包" % miss)
    return 0


def main():
    if "--check" in sys.argv:
        return main_cli()
    root = tk.Tk()
    Installer(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
