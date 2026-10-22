# -*- coding: utf-8 -*-
"""
fairy_rim.py —— 屏幕边缘呼吸 RGB 灯效（纯 Win32，点击完全穿透）

全屏透明置顶窗口，四周画一圈彩色灯带（内层亮带 + 外层光晕），随模型状态呼吸变色。
关键：纯 Win32 创建，WS_EX_TRANSPARENT | WS_EX_NOACTIVATE + WM_NCHITTEST 返回
HTTRANSPARENT —— 鼠标点击 100% 穿透到下层，绝不拦截点击、不抢焦点、不占任务栏。

状态来源：Strata /metrics（live.state），Strata 不在 -> 空闲蓝慢呼吸。
"""
import ctypes
import json
import math
import sys
import threading
import time
import urllib.request
from ctypes import wintypes as wt

import numpy as np
from PIL import Image

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32
# ★ 2026-10-17 修复：DefWindowProcW 若不设 argtypes，ctypes 默认按 32 位 c_int 处理，
#   而窗口过程回调（WNDPROC，参数用 c_ssize_t）拿到的 lparam 是 64 位，传大值会
#   抛 OverflowError: int too long to convert（fairy_rim.py.log 反复刷此错）。
#   显式声明为 64 位参数（WPARAM/LPARAM）即可根治。
user32.DefWindowProcW.argtypes = [wt.HWND, ctypes.c_uint, ctypes.c_ssize_t, ctypes.c_ssize_t]
user32.DefWindowProcW.restype = ctypes.c_ssize_t

WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008
WS_POPUP = 0x80000000
SW_SHOWNOACTIVATE = 4
ULW_ALPHA = 0x2
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
BI_RGB = 0
DIB_RGB_COLORS = 0
WM_TIMER = 0x0113
WM_DESTROY = 0x0002
WM_NCHITTEST = 0x0084
HTTRANSPARENT = -1
CS_OWNDC = 0x0020


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32)]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_byte), ("AlphaFormat", ctypes.c_byte)]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, ctypes.c_uint,
                             ctypes.c_ssize_t, ctypes.c_ssize_t)


class WNDCLASS(ctypes.Structure):
    _fields_ = [("style", ctypes.c_uint), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", ctypes.c_void_p), ("hIcon", ctypes.c_void_p),
                ("hCursor", ctypes.c_void_p), ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", ctypes.c_wchar_p), ("lpszClassName", ctypes.c_wchar_p)]


# ---------------- 绘制缓冲（全局） ----------------
_rim = {"w": 0, "h": 0, "dist": None, "screen_dc": None, "mem_dc": None, "ppv": None}


def _make_buffers(w, h):
    _rim["w"], _rim["h"] = w, h
    _rim["dist"] = _build_dist(w, h, 70)
    screen_dc = user32.GetDC(None)
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    bmi = _BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h          # top-down
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB
    ppv = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(mem_dc, ctypes.byref(bmi), DIB_RGB_COLORS,
                                  ctypes.byref(ppv), None, 0)
    gdi32.SelectObject(mem_dc, hbmp)
    _rim.update(screen_dc=screen_dc, mem_dc=mem_dc, ppv=ppv)


def _blit(hwnd, bgra):
    ctypes.memmove(_rim["ppv"], bgra.ctypes.data, bgra.nbytes)
    pt_src = wt.POINT(0, 0)
    pt_dst = wt.POINT(0, 0)   # 窗口自身已在 (x,y)
    size = wt.SIZE(_rim["w"], _rim["h"])
    bf = _BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
    user32.UpdateLayeredWindow(hwnd, _rim["screen_dc"], ctypes.byref(pt_dst),
                               ctypes.byref(size), _rim["mem_dc"],
                               ctypes.byref(pt_src), 0, ctypes.byref(bf), ULW_ALPHA)


def _to_bgra(im):
    a = np.asarray(im.convert("RGBA"), dtype=np.uint16)
    al = a[:, :, 3:4]
    rgb = (a[:, :, :3] * al // 255).astype(np.uint8)
    return np.ascontiguousarray(np.dstack([rgb[:, :, 2], rgb[:, :, 1], rgb[:, :, 0],
                                           a[:, :, 3].astype(np.uint8)]))


def _virtual_screen():
    try:
        x = user32.GetSystemMetrics(76)
        y = user32.GetSystemMetrics(77)
        w = user32.GetSystemMetrics(78)
        h = user32.GetSystemMetrics(79)
        if w > 0 and h > 0:
            return x, y, w, h
    except Exception:
        pass
    return 0, 0, 1920, 1080


# ---------------- 状态轮询 ----------------
_STATE = "idle"
_LOCK = threading.Lock()
STRATA_METRICS = "http://127.0.0.1:8080/metrics"
POLL_S = 1.0

_COLORS = {"idle": (61, 169, 255), "reading": (247, 184, 75),
           "generating": (120, 80, 255), "queued": (255, 200, 80),
           "error": (242, 105, 95)}
_PERIOD = {"idle": 3.2, "reading": 1.6, "generating": 1.4,
           "queued": 1.8, "error": 1.0}

# ★ 2026-10-17 灯带加宽 + 渐变更自然：亮带 8px，光晕平滑淡出到 28px
_RIM_T_IN = 8      # 内层亮带宽度（px）
_RIM_T_OUT = 28    # 外层光晕最远宽度（px）


def _poll_loop():
    global _STATE
    while True:
        s = "idle"
        try:
            req = urllib.request.Request(STRATA_METRICS,
                                         headers={"cache-control": "no-store"})
            with urllib.request.urlopen(req, timeout=1.2) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
            lv = (j.get("live") or {}) if isinstance(j, dict) else {}
            s = lv.get("state") or "idle"
            if s not in _COLORS:
                s = "idle"
        except Exception:
            s = "idle"
        with _LOCK:
            _STATE = s
        time.sleep(POLL_S)


def _build_dist(w, h, r):
    """简单距离场：每像素到最近屏幕边沿的距离，四角直角、四条边直接相接。"""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    return np.minimum.reduce([yy, h - 1 - yy, xx, w - 1 - xx])


def _smoothstep(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def _make_frame(d, rgb, brightness, t_in, t_out):
    """四周灯带：内层亮带 + 外层柔和光晕（smoothstep 平滑淡出）。"""
    h, w = d.shape
    arr = np.zeros((h, w, 4), dtype=np.uint8)
    arr[:, :, :3] = np.array(rgb, dtype=np.uint8)
    inner = d < t_in
    outer = (d >= t_in) & (d < t_out)
    s = np.zeros_like(d, dtype=np.float32)
    s[outer] = (d[outer] - t_in) / float(t_out - t_in)
    a = np.zeros((h, w), dtype=np.float32)
    a[inner] = brightness * 255.0
    a[outer] = brightness * 255.0 * (1.0 - _smoothstep(s[outer]))
    arr[:, :, 3] = a.astype(np.uint8)
    return arr


# ---------------- 窗口过程 ----------------
def _wndproc(hwnd, msg, wparam, lparam):
    if msg == WM_TIMER:
        try:
            with _LOCK:
                state = _STATE
            rgb = _COLORS[state]
            period = _PERIOD[state]
            t = (time.time() % period) / period
            breath = 0.5 * (1.0 - math.cos(2.0 * math.pi * t))
            brightness = 0.40 + 0.60 * breath
            frame = _make_frame(_rim["dist"], rgb, brightness, _RIM_T_IN, _RIM_T_OUT)
            _blit(hwnd, _to_bgra(Image.fromarray(frame, "RGBA")))
        except Exception:
            pass
        return 0
    if msg == WM_NCHITTEST:
        return HTTRANSPARENT   # 绝对点击穿透
    if msg == WM_DESTROY:
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


@WNDPROC
def _proc(hwnd, msg, wparam, lparam):
    return _wndproc(hwnd, msg, wparam, lparam)


def main():
    x, y, w, h = _virtual_screen()
    _make_buffers(w, h)
    hinst = kernel32.GetModuleHandleW(None)
    wc = WNDCLASS()
    wc.style = CS_OWNDC
    wc.lpfnWndProc = _proc
    wc.hInstance = hinst
    wc.lpszClassName = "fairy_rim_overlay"
    user32.RegisterClassW(ctypes.byref(wc))
    hwnd = user32.CreateWindowExW(
        WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW
        | WS_EX_NOACTIVATE | WS_EX_TOPMOST,
        "fairy_rim_overlay", "fairy_rim", WS_POPUP,
        x, y, w, h, None, None, hinst, None)
    if not hwnd:
        return 2
    user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    user32.SetTimer(hwnd, 1, 50, None)   # ~20fps 呼吸
    threading.Thread(target=_poll_loop, daemon=True).start()

    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.DispatchMessageW(ctypes.byref(msg))
    return 0


if __name__ == "__main__":
    sys.exit(main())
