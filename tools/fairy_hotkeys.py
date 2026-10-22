"""Fairy 全局快捷键（★ 2026-10-11 新增）：热键触发常用功能。

默认热键：
    Ctrl+Alt+O   截图识别文字（OCR，结果在 tools 目录输出）
    Ctrl+Alt+S   安全自检（病毒/漏洞）
用法：py fairy_hotkeys.py   （常驻后台，Fairy 启动时自动带起）
"""
import os
import subprocess
import sys
import threading
import time

import keyboard

PY = sys.executable
TOOLS = os.path.dirname(os.path.abspath(__file__))


def _run(name, args, timeout=90):
    def _w():
        try:
            subprocess.run([PY, os.path.join(TOOLS, name)] + args, cwd=TOOLS,
                           timeout=timeout, capture_output=True)
        except Exception:
            pass
    threading.Thread(target=_w, daemon=True).start()


def main():
    keyboard.add_hotkey("ctrl+alt+o", lambda: _run("fairy_ocr.py", ["--full"]))
    keyboard.add_hotkey("ctrl+alt+s", lambda: _run("fairy_security.py", ["--check"]))
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
