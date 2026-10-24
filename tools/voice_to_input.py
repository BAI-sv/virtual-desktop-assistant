# -*- coding: utf-8 -*-
"""voice_to_input.py —— 把一段文字粘贴进 DSH 的输入框。

【用途】Fairy 听到的话，不要自己答，而是落到 DSH 的输入框里，
        由人看一眼、确认没问题再按回车发出去。

【为什么不自动回车】
    语音识别难免听错一个字。直接发出去可能问了个不是本意的问题，
    或者触发了不该触发的操作。所以这里只负责"填进去"，发送权留给人。

【为什么只能模拟键盘】
    DSH 是 Electron 应用，输入框是网页里的 DOM 节点，不是 Win32 控件，
    系统层面拿不到它的句柄，也没法直接 setText。
    唯一通用的办法是：把窗口切到前台 -> 写剪贴板 -> 发 Ctrl+V。
    查过客户端插件侧：可用的服务只有 layout/locale/sessions/slots/
    theme/timer/uiWorkspace/workspaces，没有 composer，插件也写不进输入框。

用法：
    py -3.12 voice_to_input.py "要填进去的话"
    py -3.12 voice_to_input.py --check     只查窗口在不在，不动输入框
"""
import ctypes
import sys
import time
from ctypes import wintypes as w

user32 = ctypes.windll.user32

# 虚拟键码
VK_CONTROL = 0x11
VK_V = 0x56
VK_RETURN = 0x0D

# 窗口标题里出现这些就认为是 DSH
TITLE_KEYS = ("DeepSeek Harness", "DeepSeek 桌面")


def find_dsh_window():
    """返回 DSH 主窗口句柄，找不到返回 None。"""
    found = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n == 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value
        if any(k in title for k in TITLE_KEYS):
            found.append((hwnd, title))
        return True

    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, w.HWND, w.LPARAM)(cb)
    user32.EnumWindows(proc, 0)
    return found[0] if found else None


def _clipboard_set(text):
    """写剪贴板，返回原内容（文本）以便还原。"""
    old = None
    try:
        import tkinter as tk
        r = tk.Tk()
        r.withdraw()
        try:
            old = r.clipboard_get()
        except Exception:
            old = None
        r.clipboard_clear()
        r.clipboard_append(text)
        r.update()
        r.destroy()
    except Exception:
        # tkinter 不行就退回 Win32 剪贴板
        CF_UNICODETEXT = 13
        GMEM_MOVEABLE = 0x0002
        kernel32 = ctypes.windll.kernel32
        data = ctypes.create_unicode_buffer(text)
        size = ctypes.sizeof(data)
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if h:
            p = kernel32.GlobalLock(h)
            if p:
                ctypes.memmove(p, data, size)
                kernel32.GlobalUnlock(h)
            user32.OpenClipboard(0)
            user32.EmptyClipboard()
            user32.SetClipboardData(CF_UNICODETEXT, h)
            user32.CloseClipboard()
    return old


def _key(vk, up=False):
    user32.keybd_event(vk, 0, 0x0002 if up else 0, 0)


def _send_ctrl_v():
    _key(VK_CONTROL)
    time.sleep(0.02)
    _key(VK_V)
    time.sleep(0.02)
    _key(VK_V, True)
    time.sleep(0.02)
    _key(VK_CONTROL, True)


def _send_enter():
    _key(VK_RETURN)
    time.sleep(0.02)
    _key(VK_RETURN, True)


def paste(text, activate=True, press_enter=False, restore_clipboard=True):
    """把 text 粘到 DSH 输入框。返回 (ok, 说明)。"""
    if not text:
        return False, "文本为空"
    hit = find_dsh_window()
    if not hit:
        return False, "没找到 DSH 窗口（标题里要包含 DeepSeek Harness）"
    hwnd, title = hit

    if activate:
        try:
            # 先恢复最小化，再抢前台
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)   # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            time.sleep(0.18)
        except Exception as e:
            return False, "激活窗口失败: %s" % e

    old = _clipboard_set(text)
    time.sleep(0.05)
    try:
        _send_ctrl_v()
        time.sleep(0.08)
        if press_enter:
            _send_enter()
    finally:
        if restore_clipboard and old:
            time.sleep(0.12)
            try:
                _clipboard_set(old)
            except Exception:
                pass

    return True, "已填入 %d 字（窗口：%s）" % (len(text), title[:40])


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if args[0] == "--check":
        hit = find_dsh_window()
        if hit:
            print("找到窗口：hwnd=0x%X  %s" % (hit[0], hit[1][:60]))
            return 0
        print("没找到 DSH 窗口")
        return 1
    text = args[0]
    press_enter = "--enter" in args
    ok, msg = paste(text, press_enter=press_enter)
    print(("✓ " if ok else "✗ ") + msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
