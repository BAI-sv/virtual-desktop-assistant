# -*- coding: utf-8 -*-
import ctypes
import ctypes.wintypes as _wt
import json
import os
import queue
import re
import sys
import threading
import time
import urllib.request

import numpy as np
import tkinter as tk
from PIL import Image, ImageChops, ImageDraw, ImageFont
import fairy_root
import fairy_endpoints as ep


ASSETS = os.path.join(fairy_root.ASSETS, "ball_128")
POS_FILE = fairy_root.BALL_POS


META_URL = "<动态：ep.meta_url()>"
REPLY_URL = "<动态：ep.reply_url()>"


CITIES = ["郑州", "北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "西安", "南京",
          "天津", "重庆", "长沙", "青岛", "苏州", "合肥", "济南", "福州", "厦门", "昆明",
          "沈阳", "大连", "哈尔滨", "石家庄", "太原", "南昌", "贵阳", "南宁", "兰州", "乌鲁木齐"]

SPEAK_RUNNING = "了解开始同步"

TEXT_RUNNING = "正在执行"
SPEAK_DONE = "主人我已完成既定目标"


TTS_SPEAK = "<动态：ep.dsh_base() + '/dsh-tts-api/speak'>"
TTS_BASE = "<动态：ep.dsh_base()>"
SAY_URL = "<动态：ep.tts_get() 内部按链选择>"


STRATA_PORT = 8080
STRATA_STATUS_URL = "http://127.0.0.1:8080/v1/status"
STRATA_LOAD_URL = "http://127.0.0.1:8080/v1/load"
STRATA_UNLOAD_URL = "http://127.0.0.1:8080/v1/unload"

def _resolve_strata_dir():
    _cfg = getattr(fairy_root, "CFG", None) or {}
    _d = os.environ.get("STRATA_DIR") or (_cfg.get("strata_dir") if isinstance(_cfg, dict) else None)
    if _d and os.path.isfile(os.path.join(_d, "serve", "server.py")):
        return _d
    for _cand in (r"C:\Strata", r"D:\Strata", r"E:\Strata", r"F:\Strata",
                  os.path.join(os.path.expanduser("~"), "Strata")):
        if os.path.isfile(os.path.join(_cand, "serve", "server.py")):
            return _cand
    return None

STRATA_DIR = _resolve_strata_dir()
STRATA_LOG = fairy_root.log("strata_server.log")

def _strata_cmd():
    _d = STRATA_DIR or _resolve_strata_dir()
    if not _d:
        return None


    _py = os.path.join(_d, ".venv", "Scripts", "pythonw.exe")
    if not os.path.isfile(_py):
        _py = os.path.join(_d, ".venv", "Scripts", "python.exe")
    if not os.path.isfile(_py):
        _py = sys.executable
    _cfg = os.path.join(_d, "strata-unsloth-ud-iq4_xs.json")
    _c = getattr(fairy_root, "CFG", None) or {}
    if isinstance(_c, dict) and _c.get("strata_config"):
        _cfg = _c["strata_config"]
    return [_py, os.path.join(_d, "serve", "server.py"),
            "--engine", "strata", "--config", _cfg,
            "--port", str(STRATA_PORT)]

class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

def _free_ram_gb():
    try:
        _st = _MEMORYSTATUSEX()
        _st.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(_st)):
            return _st.ullAvailPhys / float(1024 ** 3)
    except Exception:
        pass
    return None


PLAY_GAME_ORIGINAL = False

CRED_FILE = os.path.expanduser(r"~/.dsh/.credentials.yaml")
BALANCE_API = "https://api.deepseek.com/user/balance"
BALANCE_REFRESH_S = 300

VOICE_INDEX = fairy_root.VOICE_INDEX
VOICE_DIR = fairy_root.VOICES


GAME_LINE = {
    "aigc_canvas_link": "支持猜想已为您生成倒影图像",
    "aigc_canvas_list_elements": "正在检索中",
    "aigc_canvas_place": "支持猜想已为您生成倒影图像",
    "aigc_canvas_unlink": "支持猜想已为您生成倒影图像",
    "aigc_get_provider_info": "正在解析设备工作程序",
    "aigc_http_request": "支持猜想已为您生成倒影图像",
    "aigc_media_edit": "支持猜想已为您生成倒影图像",
    "aigc_provider_set_instructions": "正在解析设备工作程序",
    "ask_user_question": "主人请注意",
    "cordis_inspect_list": "正在解析设备工作程序",
    "cordis_inspect_query": "正在解析设备工作程序",
    "create_goal": "正在解析设备工作程序",
    "edit": "系统修复中请稍候",
    "ego_auth_flush": "系统修复中请稍候",
    "ego_captcha": "主人请注意",
    "ego_cdp": "正在解析设备工作程序",
    "ego_check": "正在解析设备工作程序",
    "ego_cli": "正在解析设备工作程序",
    "ego_click": "正在检索中",
    "ego_dialog": "主人请注意",
    "ego_doctor": "正在解析设备工作程序",
    "ego_download": "系统修复中请稍候",
    "ego_drag": "正在解析设备工作程序",
    "ego_fill": "正在解析设备工作程序",
    "ego_help": "正在检索中",
    "ego_hover": "正在检索中",
    "ego_http": "正在搜索武装信息",
    "ego_js": "正在解析设备工作程序",
    "ego_key": "正在解析设备工作程序",
    "ego_login_import": "系统修复中请稍候",
    "ego_navigate": "正在检索中",
    "ego_page_info": "正在检索中",
    "ego_read_element": "正在读取萝卜数据",
    "ego_screenshot": "正在检索中",
    "ego_script": "正在解析设备工作程序",
    "ego_scroll": "正在检索中",
    "ego_select": "正在解析设备工作程序",
    "ego_snapshot": "正在检索中",
    "ego_space_close": "正在解析设备工作程序",
    "ego_space_open": "正在解析设备工作程序",
    "ego_status": "正在解析设备工作程序",
    "ego_upload": "系统修复中请稍候",
    "ego_wait": "当然主人请您稍等",
    "ego_wait_for_response": "当然主人请您稍等",
    "ego_wait_for_selector": "当然主人请您稍等",
    "ego_wait_for_url": "当然主人请您稍等",
    "exit_plan_mode": "正在解析设备工作程序",
    "get_goal": "正在检索中",
    "glob": "正在检索路径",
    "grep": "正在检索路径",
    "interrupt_agent": "正在召集等候的受困者们请稍等",
    "job_kill": "正在召集等候的受困者们请稍等",
    "job_list": "检索结果0个",
    "job_output": "检索结果0个",
    "list_agents": "正在召集等候的受困者们请稍等",
    "load_workspace_dependencies": "系统修复中请稍候",
    "memory_update": "系统修复中请稍候",
    "mineru_get_parse_result": "正在读取萝卜数据",
    "mineru_get_parse_status": "当然主人请您稍等",
    "mineru_health": "正在解析设备工作程序",
    "mineru_parse_document": "正在读取萝卜数据",
    "mineru_submit_parse_job": "正在读取萝卜数据",
    "plugin_manager": "正在解析设备工作程序",
    "present": "支持猜想已为您生成倒影图像",
    "pwsh": "正在解析设备工作程序",
    "read": "正在读取萝卜数据",
    "read_image": "检测到附近的热源信号",
    "run_node": "通信结构解析进度50",
    "run_python": "通信结构解析进度50",
    "schedule_create": "正在解析设备工作程序",
    "schedule_delete": "系统修复中请稍候",
    "schedule_list": "正在检索中",
    "schedule_update": "系统修复中请稍候",
    "send_message": "正在召集等候的受困者们请稍等",
    "skill": "正在解析设备工作程序",
    "sleep": "当然主人请您稍等",
    "subagent": "正在召集等候的受困者们请稍等",
    "subagent_fork": "正在召集等候的受困者们请稍等",
    "todo_write": "正在解析设备工作程序",
    "update_goal": "系统修复中请稍候",
    "web_fetch": "正在读取萝卜数据",
    "web_search": "正在搜索武装信息",
    "workflow": "正在召集等候的受困者们请稍等",
    "write": "系统修复中请稍候",
}


LINE_POOLS_FILE = fairy_root.LINE_POOLS
TOOL_POOL = {
    "aigc_canvas_link": "fix",
    "aigc_canvas_list_elements": "search",
    "aigc_canvas_place": "fix",
    "aigc_canvas_unlink": "fix",
    "aigc_get_provider_info": "read",
    "aigc_http_request": "plan",
    "aigc_media_edit": "fix",
    "aigc_provider_set_instructions": "scan",
    "ask_user_question": "notice",
    "cordis_inspect_list": "search",
    "cordis_inspect_query": "scan",
    "create_goal": "plan",
    "edit": "fix",
    "ego_auth_flush": "fix",
    "ego_captcha": "notice",
    "ego_cdp": "read",
    "ego_check": "search",
    "ego_cli": "fix",
    "ego_click": "fix",
    "ego_dialog": "notice",
    "ego_doctor": "search",
    "ego_download": "fix",
    "ego_drag": "fix",
    "ego_fill": "fix",
    "ego_help": "search",
    "ego_hover": "search",
    "ego_http": "target",
    "ego_js": "read",
    "ego_key": "fix",
    "ego_login_import": "fix",
    "ego_navigate": "fix",
    "ego_page_info": "search",
    "ego_read_element": "read",
    "ego_screenshot": "scan",
    "ego_script": "plan",
    "ego_scroll": "search",
    "ego_select": "fix",
    "ego_snapshot": "search",
    "ego_space_close": "target",
    "ego_space_open": "target",
    "ego_status": "search",
    "ego_upload": "fix",
    "ego_wait": "other",
    "ego_wait_for_response": "other",
    "ego_wait_for_selector": "search",
    "ego_wait_for_url": "search",
    "exit_plan_mode": "other",
    "get_goal": "search",
    "glob": "search",
    "grep": "search",
    "interrupt_agent": "target",
    "job_kill": "target",
    "job_list": "search",
    "job_output": "search",
    "list_agents": "search",
    "load_workspace_dependencies": "read",
    "memory_update": "fix",
    "mineru_get_parse_result": "read",
    "mineru_get_parse_status": "search",
    "mineru_health": "scan",
    "mineru_parse_document": "scan",
    "mineru_submit_parse_job": "scan",
    "plugin_manager": "scan",
    "present": "done",
    "pwsh": "plan",
    "read": "read",
    "read_image": "read",
    "run_node": "plan",
    "run_python": "plan",
    "schedule_create": "fix",
    "schedule_delete": "fix",
    "schedule_list": "other",
    "schedule_update": "fix",
    "send_message": "target",
    "skill": "target",
    "sleep": "other",
    "subagent": "target",
    "subagent_fork": "target",
    "todo_write": "plan",
    "update_goal": "plan",
    "web_fetch": "read",
    "web_search": "other",
    "workflow": "plan",
    "write": "fix",
    "web_search": "search",
    "schedule_list": "search",
}

def _load_line_pools(path=LINE_POOLS_FILE):
    try:
        with open(path, encoding="utf-8") as f:
            j = json.load(f)
        pools = j.get("pools") or {}
        got = {k: [x for x in v if x] for k, v in pools.items() if v}
        if not got:
            print("[fairy] 台词池文件里没有池: %s" % path, flush=True)
            return {}
        return got
    except FileNotFoundError:
        print("[fairy] 台词池文件不存在(%s)，执行提示退回 GAME_LINE" % path, flush=True)
    except Exception as _e:
        print("[fairy] 台词池读取失败(%s: %s)，执行提示退回 GAME_LINE"
              % (type(_e).__name__, _e), flush=True)
    return {}

LINE_POOLS = _load_line_pools()


try:
    if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import voice_cache
except Exception as _e_vc:
    voice_cache = None
    print("[fairy] ⚠ 语音缓存模块加载失败（会退回实时克隆/原声兜底）: %s: %s"
          % (type(_e_vc).__name__, _e_vc), flush=True)

WAV_TMP = fairy_root.log("fairy_say.wav")


_PRE_SEQ = 0
_PRE_SEQ_LOCK = threading.Lock()

def _next_prefetch_stage():
    global _PRE_SEQ
    with _PRE_SEQ_LOCK:
        _PRE_SEQ += 1
        _n = _PRE_SEQ
    return "fairy_pre_%d_%d" % (threading.get_ident(), _n)

def _rm_quiet(path):
    try:
        if path:
            os.remove(path)
    except Exception:
        pass

SIZE = 128
GAP = 8
BAR_W = 208
WIN_W = SIZE + GAP + BAR_W
WIN_H = SIZE
MARGIN = 60
POLL_MS = 900


GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
BI_RGB = 0
DIB_RGB_COLORS = 0

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32

class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", _wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", _wt.WORD), ("biBitCount", _wt.WORD), ("biCompression", _wt.DWORD),
                ("biSizeImage", _wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", _wt.DWORD),
                ("biClrImportant", _wt.DWORD)]

class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", _wt.DWORD * 3)]

class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


_user32.GetWindowLongW.restype = ctypes.c_long
_user32.GetWindowLongW.argtypes = [_wt.HWND, ctypes.c_int]
_user32.SetWindowLongW.restype = ctypes.c_long
_user32.SetWindowLongW.argtypes = [_wt.HWND, ctypes.c_int, ctypes.c_long]
_user32.GetParent.restype = _wt.HWND
_user32.GetParent.argtypes = [_wt.HWND]
_gdi32.SelectObject.restype = _wt.HANDLE
_gdi32.SelectObject.argtypes = [_wt.HDC, _wt.HANDLE]

class LayeredWindow:

    def __init__(self, tk_window, width, height):
        self.win = tk_window
        self.w, self.h = width, height
        child = _wt.HWND(tk_window.winfo_id())
        parent = _user32.GetParent(child)
        self.hwnd = parent if parent else child
        ex = _user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        _user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED | WS_EX_TOOLWINDOW)
        self.layered_ok = bool(_user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE) & WS_EX_LAYERED)
        self._screen_dc = _user32.GetDC(None)
        self._mem_dc = _gdi32.CreateCompatibleDC(self._screen_dc)
        bmi = _BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        self._ppv = ctypes.c_void_p()
        self._hbmp = _gdi32.CreateDIBSection(self._mem_dc, ctypes.byref(bmi), DIB_RGB_COLORS,
                                             ctypes.byref(self._ppv), None, 0)
        _gdi32.SelectObject(self._mem_dc, self._hbmp)

    def blit(self, bgra):
        ctypes.memmove(self._ppv, bgra.ctypes.data, bgra.nbytes)
        pt_src = _wt.POINT(0, 0)
        pt_dst = _wt.POINT(self.win.winfo_x(), self.win.winfo_y())
        size = _wt.SIZE(self.w, self.h)
        bf = _BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        return _user32.UpdateLayeredWindow(self.hwnd, self._screen_dc, ctypes.byref(pt_dst),
                                           ctypes.byref(size), self._mem_dc,
                                           ctypes.byref(pt_src), 0, ctypes.byref(bf), ULW_ALPHA)

def to_premultiplied_bgra(im):
    a = np.asarray(im.convert("RGBA"), dtype=np.uint16)
    al = a[:, :, 3:4]
    rgb = (a[:, :, :3] * al // 255).astype(np.uint8)
    return np.ascontiguousarray(np.dstack([rgb[:, :, 2], rgb[:, :, 1], rgb[:, :, 0],
                                           a[:, :, 3].astype(np.uint8)]))

def fmt_tokens(n):
    try:
        n = float(n)
    except Exception:
        return str(n)
    if n >= 1e6:
        return "%.1fM" % (n / 1e6)
    if n >= 1e3:
        return "%.0fk" % (n / 1e3)
    return "%d" % int(n)

def virtual_screen():
    try:
        x = _user32.GetSystemMetrics(76)
        y = _user32.GetSystemMetrics(77)
        w = _user32.GetSystemMetrics(78)
        h = _user32.GetSystemMetrics(79)
        if w > 0 and h > 0:
            return x, y, w, h
    except Exception:
        pass
    return 0, 0, 1920, 1080


def _dim_frame(im, dim):
    arr = np.asarray(im).astype(np.float32)
    arr[:, :, :3] *= dim
    return Image.fromarray(arr.astype(np.uint8), "RGBA")

def _placeholder_frame():
    im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    m = max(2, int(SIZE * 0.07))
    d.ellipse([m, m, SIZE - m - 1, SIZE - m - 1], fill=(118, 188, 235, 255))
    return im

def load_frames(dim=0.82):
    try:
        files = sorted(f for f in os.listdir(ASSETS) if f.lower().endswith(".png"))
    except Exception as _e:
        print("[fairy] ⚠ 未找到外观素材目录：%s（%s: %s）"
              % (ASSETS, type(_e).__name__, _e), flush=True)
        files = []
    bright, dimmed = [], []
    for f in files:
        try:
            im = Image.open(os.path.join(ASSETS, f)).convert("RGBA")
        except Exception as _e:
            print("[fairy] ⚠ 帧读取失败跳过：%s（%s）" % (f, _e), flush=True)
            continue
        if im.size != (SIZE, SIZE):
            im = im.resize((SIZE, SIZE), Image.LANCZOS)
        bright.append(im)
        dimmed.append(_dim_frame(im, dim))
    if not bright:
        print("[fairy] ⚠ 外观素材为空 -> 使用【占位外观】（纯色圆）。"
              "自备素材请放到 %s（*.png，建议 %dx%d 带透明）"
              % (ASSETS, SIZE, SIZE), flush=True)
        ph = _placeholder_frame()
        files = ["__placeholder__.png"]
        bright = [ph]
        dimmed = [_dim_frame(ph, dim)]
    return files, bright, dimmed


_PUNCT = "\u2026\u3002\uff0c\uff01\uff1f\u3001\uff1b\uff1a\u201c\u201d\u2018\u2019\u300a\u300b\u3010\u3011()[]{}<>!?,.;:\"'\u3000 \t\n"

def _norm(s):
    return "".join(ch for ch in str(s or "") if ch not in _PUNCT).strip()

class Corpus:

    def __init__(self, path=VOICE_INDEX, vdir=VOICE_DIR):
        self.dir = vdir
        self.by = {}
        self.count = 0
        try:
            j = json.load(open(path, encoding="utf-8"))
            vd = j.get("voices_dir") or ""
            if vd:
                self.dir = vd if os.path.isabs(vd) else os.path.join(os.path.dirname(path), vd)
            for e in (j.get("lines") or []):
                self.count += 1
                for k in ("t", "r"):
                    kk = _norm(e.get(k))
                    if kk and kk not in self.by:
                        self.by[kk] = e.get("w")
        except Exception as _e:


            print("[fairy] ⚠ 游戏原声语料库加载失败，将退化为语音合成: %s: %s"
                  % (type(_e).__name__, _e), flush=True)

    def find(self, text):
        w = self.by.get(_norm(text))
        if not w:
            return None
        fp = os.path.join(self.dir, w)
        return fp if os.path.exists(fp) else None

    def fallback(self, prefer="notice"):
        import random as _rnd
        cands = list(LINE_POOLS.get(prefer) or []) + list(LINE_POOLS.get("other") or [])
        _rnd.shuffle(cands)
        for s in cands[:60]:
            fp = self.find(s)
            if fp:
                return fp

        ws = list(self.by.values())
        _rnd.shuffle(ws)
        for w in ws[:200]:
            if w:
                fp = os.path.join(self.dir, w)
                if os.path.exists(fp):
                    return fp
        return None

_BUB_MAGIC = "#ff00fe"


_PANEL_BG = "#0b1622"
_PANEL_BG_RGB = (11, 22, 34)
_PANEL_EDGE = "#2a4a6a"
_PANEL_EDGE_RGB = (42, 74, 106)
_PANEL_FG = "#d8ecff"


_BALL_DISC_R = 62.0
_BALL_DISC_SOLID = 0.45
_BALL_DISC_SS = 4
_BALL_DISC_EDGE_W = 0
_BALL_DISC_EDGE_A = 150
_BALL_DISC_CACHE = {}

def _ball_backing():
    key = (_BALL_DISC_R, _BALL_DISC_SOLID, _BALL_DISC_EDGE_W,
           _BALL_DISC_EDGE_A, _BALL_DISC_SS)
    cached = _BALL_DISC_CACHE.get(key)
    if cached is not None:
        return cached
    ss = int(_BALL_DISC_SS)
    n = SIZE * ss
    R = float(_BALL_DISC_R) * ss
    r_solid = R * float(_BALL_DISC_SOLID)
    span = max(1e-6, R - r_solid)
    c = (n - 1) / 2.0
    yy, xx = np.mgrid[0:n, 0:n]
    rr = np.sqrt((xx - c) ** 2 + (yy - c) ** 2).astype(np.float32)
    t = np.clip((R - rr) / span, 0.0, 1.0)
    a = t * t * (3.0 - 2.0 * t)
    if _BALL_DISC_EDGE_W > 0:

        er = R * 0.92
        band = np.abs(rr - er) <= (_BALL_DISC_EDGE_W * ss / 2.0)
        a = np.maximum(a, band.astype(np.float32) * a * (_BALL_DISC_EDGE_A / 255.0))
    arr = np.zeros((n, n, 4), np.uint8)
    arr[:, :, 0] = _PANEL_BG_RGB[0]
    arr[:, :, 1] = _PANEL_BG_RGB[1]
    arr[:, :, 2] = _PANEL_BG_RGB[2]
    arr[:, :, 3] = np.clip(a * 255.0, 0, 255).astype(np.uint8)
    im = Image.fromarray(arr, "RGBA").resize((SIZE, SIZE), Image.LANCZOS)
    _BALL_DISC_CACHE[key] = im
    return im


_PANEL_BAND_X = 6.0
_PANEL_BAND_Y = 5.0
_PANEL_RGROW = 6


_PANEL_RINSET = 10
_PANEL_RADIUS = 12.0
_PANEL_GRAD_SS = 4
_PANEL_STROKE = (4, 10, 20, 210)
_PANEL_GRAD_CACHE = {}


_PANEL_RPAD = 12


_PANEL_FUSE_FILL = True


def _panel_backing(w, h):
    key = (int(w), int(h), float(_PANEL_BAND_X), float(_PANEL_BAND_Y),
           float(_PANEL_RADIUS), int(_PANEL_GRAD_SS))
    hit = _PANEL_GRAD_CACHE.get(key)
    if hit is not None:
        return hit
    ss = int(_PANEL_GRAD_SS)
    W = max(4, int(round(w)) * ss)
    H = max(4, int(round(h)) * ss)
    bx = max(1e-6, float(_PANEL_BAND_X) * ss)
    by = max(1e-6, float(_PANEL_BAND_Y) * ss)
    A = (W / 2.0) / bx
    B = (H / 2.0) / by
    rc = (float(_PANEL_RADIUS) * ss) / (bx * by) ** 0.5
    rc = min(rc, 0.45 * min(A, B))
    c = (W - 1) / 2.0
    c2 = (H - 1) / 2.0
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    qx = np.abs(xx - c) / bx - max(0.0, A - rc)
    qy = np.abs(yy - c2) / by - max(0.0, B - rc)
    qxc = np.maximum(qx, 0.0)
    qyc = np.maximum(qy, 0.0)
    d = (np.sqrt(qxc * qxc + qyc * qyc)
         + np.minimum(np.maximum(qx, qy), 0.0) - rc)
    t = np.clip(-d, 0.0, 1.0)
    a = t * t * (3.0 - 2.0 * t)
    arr = np.zeros((H, W, 4), np.uint8)
    arr[:, :, 0] = _PANEL_BG_RGB[0]
    arr[:, :, 1] = _PANEL_BG_RGB[1]
    arr[:, :, 2] = _PANEL_BG_RGB[2]
    arr[:, :, 3] = np.clip(a * 255.0, 0, 255).astype(np.uint8)
    im = Image.fromarray(arr, "RGBA").resize(
        (max(1, int(round(w))), max(1, int(round(h)))), Image.LANCZOS)
    _PANEL_GRAD_CACHE[key] = im
    return im

def _ptext(d, xy, txt, font, fill):
    try:
        d.text(xy, txt, font=font, fill=fill,
               stroke_width=1, stroke_fill=_PANEL_STROKE)
    except Exception:
        d.text(xy, txt, font=font, fill=fill)

def _panel_bar(img, plate, px, py, box, radius, fill):
    if plate is None:
        return False
    x1, y1, x2, y2 = (int(v) for v in box)
    if x2 < x1 or y2 < y1:
        return False
    w, h = x2 - x1 + 1, y2 - y1 + 1
    tile = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=fill)

    lx, ly = x1 - int(px), y1 - int(py)
    src = plate.getchannel("A")
    under = Image.new("L", (w, h), 0)
    sx1, sy1 = max(0, lx), max(0, ly)
    sx2, sy2 = min(src.width, lx + w), min(src.height, ly + h)
    if sx2 > sx1 and sy2 > sy1:
        under.paste(src.crop((sx1, sy1, sx2, sy2)), (sx1 - lx, sy1 - ly))
    base = Image.new("RGBA", (w, h), _PANEL_BG_RGB + (255,))
    base.alpha_composite(tile)
    cover = tile.getchannel("A").point(lambda v: 255 if v else 0)
    base.putalpha(ImageChops.multiply(under, cover))
    img.alpha_composite(base, (x1, y1))
    return True

def _round_rect(cv, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
           x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)

def retail_bubble(w, ball_cx, px):
    try:
        g = getattr(w, "_fairy_tail", None)
        if not g:
            return
        cv = g["cv"]
        cv.delete("fairy_tail")
        tx = int(ball_cx - px)
        tx = max(g["x1"] + 18, min(tx, g["x2"] - 18))
        cv.create_polygon(tx - g["tailw"], g["y1"] + 1, tx + g["tailw"], g["y1"] + 1,
                          tx, g["y1"] - g["tailh"] + 1,
                          fill=_PANEL_BG, outline="#0b1622", tags="fairy_tail")
    except Exception as _e:
        print("[fairy] 重画尖角失败: %s: %s" % (type(_e).__name__, _e), flush=True)

def make_bubble(parent, text, tail="left", max_w=250, tail_x=None):
    f = ("Microsoft YaHei", 10)
    w = tk.Toplevel(parent)
    w.overrideredirect(True)
    w.attributes("-topmost", True)
    w.configure(bg=_BUB_MAGIC)
    try:
        w.attributes("-transparentcolor", _BUB_MAGIC)
    except Exception:
        pass

    probe = tk.Label(w, text=text, font=f, justify="left", wraplength=max_w)
    probe.update_idletasks()
    tw, th = probe.winfo_reqwidth(), probe.winfo_reqheight()
    probe.destroy()
    pad, tailw, tailh = 12, 9, 14
    bw, bh = tw + pad * 2, th + pad * 2
    if tail == "top":
        cw, ch = bw + 2, bh + tailh + 2
    else:
        cw, ch = bw + tailw + 2, bh + 2
    cv = tk.Canvas(w, width=cw, height=ch, bg=_BUB_MAGIC, highlightthickness=0, bd=0)
    cv.pack()
    if tail == "top":
        x1, y1 = 1, tailh
    else:
        x1, y1 = (tailw if tail == "left" else 1), 1
    x2 = x1 + bw
    _round_rect(cv, x1, y1, x2, y1 + bh, 13, fill=_PANEL_BG, outline=_PANEL_EDGE, width=1)

    if tail == "top":
        _tx = x1 + (tail_x if tail_x is not None else bw * 0.5)
        _tx = max(x1 + 18, min(_tx, x2 - 18))
        cv.create_polygon(_tx - tailw, y1 + 1, _tx + tailw, y1 + 1, _tx, y1 - tailh + 1,
                          fill=_PANEL_BG, outline="#0b1622", tags="fairy_tail")

        w._fairy_tail = {"cv": cv, "x1": x1, "x2": x2, "y1": y1,
                         "tailw": tailw, "tailh": tailh}
    elif tail == "left":
        cv.create_polygon(x1 + 1, bh * 0.38, x1 + 1, bh * 0.38 + tailh,
                          x1 - tailw + 1, bh * 0.38 + tailh / 2,
                          fill=_PANEL_BG, outline="#0b1622")
    else:
        cv.create_polygon(x2 - 1, bh * 0.38, x2 - 1, bh * 0.38 + tailh,
                          x2 + tailw - 1, bh * 0.38 + tailh / 2,
                          fill=_PANEL_BG, outline="#0b1622")
    cv.create_text(x1 + pad, y1 + bh / 2, text=text, font=f, fill=_PANEL_FG,
                   anchor="w", justify="left", width=max_w)
    return w, cw, ch


def play_audio(path):
    try:
        mci = ctypes.windll.winmm.mciSendStringW
    except Exception:
        return False
    alias = "fairyvoice"
    mci("close " + alias, None, 0, None)
    if mci('open "%s" type mpegvideo alias %s' % (path, alias), None, 0, None) != 0:
        if mci('open "%s" alias %s' % (path, alias), None, 0, None) != 0:
            return False


    print("[fairy] 播放 %s | 闪避链=_duck_around(level感知) | 本函数不直接闪避"
          % os.path.basename(path), flush=True)
    try:
        mci("play %s wait" % alias, None, 0, None)
    finally:
        mci("close " + alias, None, 0, None)
    return True

def read_balance():
    try:
        if not os.path.exists(CRED_FILE):
            return None
        txt = open(CRED_FILE, encoding="utf-8", errors="replace").read()
        m = re.search(r"DEEPSEEK_API_KEY:\s*(\S+)", txt)
        if not m:
            return None
        req = urllib.request.Request(BALANCE_API, headers={
            "Authorization": "Bearer " + m.group(1).strip(), "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.loads(r.read().decode("utf-8"))
        infos = j.get("balance_infos") or []
        if not infos:
            return None
        b = infos[0]
        sym = {"CNY": "\u00a5", "USD": "$"}.get(b.get("currency"), "")
        return sym + str(b.get("total_balance", "")).strip()
    except Exception:
        return None

class Speaker:

    def __init__(self, enabled=True):
        self.enabled = enabled
        self.corpus = Corpus()
        self.hits = 0
        self.misses = 0
        self.last = ""
        self.last_t = 0.0
        self.q = queue.Queue()
        threading.Thread(target=self._worker, daemon=True).start()

    def say_all(self, lines, level="boot"):
        for x in lines:
            if x:
                self.q.put({"text": x, "level": level, "path": None, "prepared": False})

    def say(self, text, level="doing"):
        if not self.enabled or not text:
            return
        now = time.time()
        if text == self.last:
            return
        if now - self.last_t < 2.0:
            return
        self.last, self.last_t = text, now
        self.q.put((text, level))

    def _pick_doing_line(self, tool):
        try:
            pool = TOOL_POOL.get(tool) or "other"
            cands = LINE_POOLS.get(pool) or LINE_POOLS.get("other") or []
            if cands:
                import random
                last = getattr(self, "_last_doing", "")
                pick = None
                for _ in range(3):
                    c = random.choice(cands)
                    if c != last or len(cands) <= 1:
                        pick = c
                        break
                if pick is None:
                    pick = random.choice(cands)
                for _ in range(6):
                    if self.corpus.find(pick):
                        self._last_doing = pick
                        return pick
                    pick = random.choice(cands)
                print("[fairy] 池 %s 抽到的台词都命不中语料库，退回 GAME_LINE" % pool, flush=True)
        except Exception as _e:

            print("[fairy] 抽执行台词失败(tool=%s): %s: %s" % (tool, type(_e).__name__, _e), flush=True)
        return GAME_LINE.get(tool, SPEAK_RUNNING)

    def _duck_mod(self):
        if getattr(self, "_md", "unset") != "unset":
            return self._md
        self._md = None
        try:
            import importlib.util
            fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media_duck.py")
            if os.path.exists(fp):
                s = importlib.util.spec_from_file_location("fairy_media_duck", fp)
                m = importlib.util.module_from_spec(s)
                sys.modules["fairy_media_duck"] = m
                s.loader.exec_module(m)
                self._md = m
        except Exception as _e:
            print("[fairy] 加载 media_duck 失败: %s" % _e, flush=True)
            self._md = None
        return self._md

    def _vol_mod(self):
        if getattr(self, "_vd", "unset") != "unset":
            return self._vd
        self._vd = None
        try:
            import ctypes
            try:
                ctypes.windll.ole32.CoInitialize(None)
            except Exception:
                pass
            import importlib.util
            fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "volume_duck.py")
            if os.path.exists(fp):
                s = importlib.util.spec_from_file_location("fairy_volume_duck", fp)
                m = importlib.util.module_from_spec(s)
                sys.modules["fairy_volume_duck"] = m
                s.loader.exec_module(m)
                self._vd = m.VolumeDucker()
        except Exception as _e:
            print("[fairy] 加载 volume_duck 失败: %s" % _e, flush=True)
            self._vd = None
        return self._vd

    def _duck_around(self, level, fn):
        if level not in ("answer", "alert"):
            return fn()
        md = self._duck_mod()
        tok_media = None
        tok_vol = None
        try:
            if md is not None:
                d = md.duck(level)
                if d.get("want") == "silent":


                    print("[fairy] 通话中：Fairy 不发言（避免被对方听到）| %s"
                          % d.get("why", ""), flush=True)
                    return None
                if d.get("want") == "volume":

                    vd = self._vol_mod()
                    if vd is not None:
                        tok_vol = vd.duck(0.2)
                        if not tok_vol.get("ducked"):
                            print("[fairy] 音量让位未生效: %s" % tok_vol.get("why"), flush=True)
                    else:
                        print("[fairy] 需要音量让位但 volume_duck 不可用", flush=True)
                elif d.get("ducked"):
                    tok_media = d
                else:
                    print("[fairy] 无需让位: %s" % d.get("why", ""), flush=True)
        except Exception as _e:
            print("[fairy] 媒体让位失败: %s" % _e, flush=True)
        try:
            return fn()
        finally:
            if tok_media is not None:
                try:
                    r = md.unduck(tok_media)
                    if not r.get("restored"):
                        print("[fairy] 媒体恢复未生效: %s" % r.get("why"), flush=True)
                except Exception as _e:
                    print("[fairy] 媒体恢复失败: %s" % _e, flush=True)
            if tok_vol is not None:
                try:
                    vd.unduck(tok_vol)
                except Exception as _e:
                    print("[fairy] 音量恢复失败: %s" % _e, flush=True)

    def _warm_brief_cache(self, city=""):
        try:
            import re as _re
            fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "briefing.py")
            if not os.path.exists(fp) or voice_cache is None:
                return
            src = open(fp, encoding="utf-8").read()
            cand = set()

            def _keep(s):
                s = (s or "").strip()
                if len(s) < 6 or "%" in s:
                    return
                if not _re.search(r"[\u4e00-\u9fff]", s):
                    return
                if s.startswith(("http", "www", "Mozilla")):
                    return
                cand.add(s)

            for m in _re.finditer(r'lines\.append\(\s*"([^"\n]*)"', src):
                _keep(m.group(1))
            for m in _re.finditer(r'^\s*return\s+"([^"\n]{4,60})"\s*$', src, _re.M):
                _keep(m.group(1))
            for m in _re.finditer(r'^\s*lines\.append\(\s*\'([^\'\n]{4,60})\'\s*\)', src, _re.M):
                _keep(m.group(1))


            if city:
                _keep("检测到当前位置是%s。" % city)
            todo = []
            for s in sorted(cand):
                try:
                    if voice_cache.lookup(s):
                        continue
                except Exception:
                    continue
                todo.append(s)
            if not todo:
                print("[fairy] 播报预热：固定句已全部在缓存里，无需补", flush=True)
                return
            print("[fairy] 播报预热：缓存缺 %d 句固定话，开始补（后台，不挡说话）" % len(todo),
                  flush=True)
            ok = 0
            for s in todo[:12]:
                if self.q.qsize() > 0 or getattr(self, "_briefed", False):

                    print("[fairy] 播报预热：让路（开始播报了），已补 %d 句" % ok, flush=True)
                    return
                try:
                    if self._prepare(s, stage="fairy_warm"):
                        ok += 1
                except Exception as _e:
                    print("[fairy] 预热单句失败(%s): %s" % (s[:12], _e), flush=True)
            print("[fairy] 播报预热完成：补了 %d 句固定话" % ok, flush=True)
        except Exception as e:
            print("[fairy] 播报预热失败（不影响正常播报）: %s: %s" % (type(e).__name__, e),
                  flush=True)

    def _speak(self, path, level, tag, text, _last_done=None):
        t0 = time.time()
        gap = (t0 - _last_done) if _last_done else 0.0
        self._duck_around(level, lambda pp=path: play_audio(pp))
        t1 = time.time()
        self.spoken = getattr(self, "spoken", 0) + 1

        syn = getattr(self, "_syn_ms", None)
        _req = getattr(self, "_tts_request_s", None)
        print("[语音计时] %s 条=%s 间隔=%.1fs 合成=%s(请求%s) 播放=%.1fs 文本=%s"
              % (time.strftime("%H:%M:%S"), tag, gap,
                 ("%.1fs" % (syn / 1000.0)) if isinstance(syn, (int, float)) else "0s(缓存)",
                 ("%.1fs" % _req) if isinstance(_req, (int, float)) else "-",
                 t1 - t0, (text or "")[:14]), flush=True)
        return t1

    def _prepare(self, text, ext=".wav", dest=None, register=True, stage=None, tag="预取/预热"):
        t0 = time.time()
        data = None
        route = ""
        voice = "?"
        r = None
        _t_req0 = time.time()
        _t_req = 0.0
        try:
            r = ep.tts_get(text)
        except Exception as e1:
            print("[fairy] tts_get 异常(%s: %s) -> 走兜底"
                  % (type(e1).__name__, str(e1)[:80]), flush=True)
        _t_req = time.time() - _t_req0
        if r and r.get("ok") and r.get("data"):
            data = r["data"]
            route = r.get("route") or "?"
            voice = r.get("voice") or "?"
            self._tts_err = None


            print("[fairy] 语音=克隆路由(%s|%s) %d 字节"
                  % (voice, route, len(data)), flush=True)
        else:
            why = ""
            if r:
                why = "; ".join("%s:%s" % (t.get("route"), t.get("error"))
                                for t in (r.get("tried") or []))[:200]

            if why != getattr(self, "_tts_err", None):
                self._tts_err = why
                print("[fairy] ★ 语音通道全失败 -> 走兜底。原因：%s"
                      % (why or "(无详情)"), flush=True)
        self._syn_ms = int((time.time() - t0) * 1000)
        self._tts_request_s = round(_t_req, 1)
        if data is None:
            return None


        is_mp3 = (data[:3] == b"ID3"
                  or (len(data) > 1 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0))


        if stage:
            path = os.path.join(os.path.dirname(WAV_TMP), stage + (".mp3" if is_mp3 else ext))
            print("[fairy] %s落盘：%s（主线程实时合成走 %s）"
                  % (tag, os.path.basename(path), os.path.basename(WAV_TMP)), flush=True)
        else:
            path = dest or os.path.join(os.path.dirname(WAV_TMP),
                                        "fairy_say" + (".mp3" if is_mp3 else ext))
        try:
            with open(path, "wb") as f:
                f.write(data)
        except Exception as e2:
            print("[fairy] 音频落盘失败(%s): %s" % (path, e2), flush=True)
            return None

        if register and voice_cache is not None:
            try:
                voice_cache.register(text, path)
            except Exception as e3:
                print("[fairy] 缓存登记失败(不影响播放): %s" % e3, flush=True)
        return path

    def _prefetch(self, item):
        if item is None or item.get("prepared"):
            return
        if item.get("path") or item.get("hit") or item.get("corpus"):
            return
        text = item.get("text") or ""
        if not text.strip():
            return

        if voice_cache is not None:
            try:
                if voice_cache.lookup(text):
                    return
            except Exception:
                pass
        item["busy"] = True
        try:


            p = self._prepare(text, stage=_next_prefetch_stage(), tag="预取")
            if p:
                item["path"] = p
                item["prepared"] = True
        except Exception as e:
            print("[fairy] 预合成失败(不影响主流程): %s: %s" % (type(e).__name__, e), flush=True)
        finally:
            item["busy"] = False

    def _clone_ready(self):
        try:
            return bool(ep.probe(ep.indextts_url()))
        except Exception:
            return False

    def _worker(self):


        _last_done = None


        try:
            _vd0 = self._vol_mod()
            if _vd0 is not None:
                _r = _vd0.emergency_restore()
                if _r.get("restored"):
                    print("[fairy] 启动自愈：音量已还原到 %.2f" % (_r.get("to") or 0), flush=True)
        except Exception as _e:
            print("[fairy] 启动自愈失败（不影响说话）: %s" % _e, flush=True)
        while True:
            item = self.q.get()


            if item is None:
                return


            if isinstance(item, dict):
                text = item.get("text") or ""
                level = item.get("level") or "doing"
            elif isinstance(item, (tuple, list)) and len(item) == 2:
                text, level = item
                item = {"text": text, "level": level, "path": None, "prepared": False}
            else:
                text, level = item, "doing"
                item = {"text": text, "level": level, "path": None, "prepared": False}

            if not (text or "").strip():
                continue


            try:
                if self.q.qsize() > 0:
                    _nx = self.q.queue[0]
                    if isinstance(_nx, dict) and not _nx.get("prepared"):
                        threading.Thread(target=self._prefetch, args=(_nx,),
                                         daemon=True, name="fairy-prefetch").start()
            except Exception as _ep:
                print("[fairy] 预取启动失败(不影响播放): %s: %s"
                      % (type(_ep).__name__, _ep), flush=True)
            try:

                hit = self.corpus.find(text) if PLAY_GAME_ORIGINAL else None
                if hit:
                    self.hits += 1
                    self._syn_ms = None
                    _last_done = self._speak(hit, level, "原声", text, _last_done)
                    continue
                self.misses += 1


                _cp = None
                if voice_cache is not None:
                    try:
                        _cp = voice_cache.lookup(text)
                    except Exception as _ec:
                        print("[fairy] 缓存查询失败(%s: %s)，继续走实时合成"
                              % (type(_ec).__name__, str(_ec)[:60]), flush=True)
                if _cp and _cp.lower().endswith(".mp3") and self._clone_ready():


                    print("[fairy] 缓存(%s)是别的引擎的 mp3 -> 丢掉，改走克隆实时合成"
                          % os.path.basename(_cp), flush=True)
                    _cp = None
                if _cp:
                    self.cachehits = getattr(self, "cachehits", 0) + 1


                    print("[fairy] 语音=缓存(%s) %s"
                          % (os.path.basename(_cp).replace("cache_", "").replace(".wav", ""),
                             text[:12]), flush=True)
                    self._syn_ms = None
                    _last_done = self._speak(_cp, level, "缓存", text, _last_done)
                    continue

                if isinstance(item, dict) and item.get("path"):
                    _pp = item["path"]
                    _last_done = self._speak(_pp, level, "预取", text, _last_done)

                    _rm_quiet(_pp)
                    continue


                if isinstance(item, dict) and item.get("busy"):

                    for _ in range(600):
                        if item.get("path") or not item.get("busy"):
                            break
                        time.sleep(0.1)
                    if item.get("path"):
                        _pp = item["path"]
                        _last_done = self._speak(_pp, level, "预取", text, _last_done)
                        _rm_quiet(_pp)
                        continue
                _sp = self._prepare(text)
                if _sp:
                    _last_done = self._speak(_sp, level, "克隆", text, _last_done)
                    continue


                _fb = self.corpus.fallback() if PLAY_GAME_ORIGINAL else None
                if _fb:
                    self.fbhits = getattr(self, "fbhits", 0) + 1
                    print("[fairy] 语音=原声兜底（克隆不可用，拒绝退回 Edge）%s"
                          % os.path.basename(_fb), flush=True)
                    self._syn_ms = None
                    _last_done = self._speak(_fb, level, "兜底", text, _last_done)
                continue
            except Exception as _e:


                import traceback
                print("[fairy] 说话链路出错（不影响球继续运行）: %s: %s"
                      % (type(_e).__name__, _e), flush=True)
                traceback.print_exc()

class State:
    def __init__(self, on_change=None):
        self.on_change = on_change
        self.running = None
        self.steps = 0
        self.last_tool = ""
        self.doing = ""
        self.mood = ""
        self.progress = None
        self.cpct = None
        self.crem = None
        self.mode = ""
        self.task_label = ""
        self.steps = 0
        self.ok = False


        self.reply_seq = None
        self.lock = threading.Lock()

    def poll_loop(self, stop):
        while not stop.is_set():
            try:


                with urllib.request.urlopen(ep.meta_url(), timeout=4) as r:
                    j = json.loads(r.read().decode("utf-8"))
                prev_run, prev_doing = self.running, self.doing
                with self.lock:
                    self.running = j.get("running")
                    self.steps = j.get("steps") or 0
                    self.last_tool = j.get("lastTool") or ""
                    self.doing = j.get("doing") or ""
                    self.mood = (j.get("mood") or {}).get("label") or ""
                    self.mode = ((j.get("mode") or {}).get("label") or "")
                    self.task_label = ((j.get("task") or {}).get("label") or "")
                    self.steps = j.get("steps") or 0
                    self.progress = j.get("progress")
                    ctx = j.get("context") or {}
                    self.cpct = ctx.get("pct")
                    _u, _l = ctx.get("used"), ctx.get("limit")
                    self.crem = (_l - _u) if (isinstance(_u, (int, float))
                                              and isinstance(_l, (int, float))) else None
                    self.ok = True
                if self.on_change:

                    if self.doing and self.doing != prev_doing:
                        self.on_change("doing", self.doing)

                    if prev_run is True and self.running is False:
                        self.on_change("done", "")

                    if isinstance(self.cpct, (int, float)) and self.cpct >= 85:
                        self.on_change("token", "上下文快满了，要不要先整理一下")


                try:
                    with urllib.request.urlopen(ep.reply_url(), timeout=4) as rr:
                        rp = json.loads(rr.read().decode("utf-8"))
                    rseq = rp.get("seq") or 0
                    rtxt = (rp.get("text") or "").strip()
                    if self.reply_seq is None:
                        self.reply_seq = rseq
                    elif rseq > self.reply_seq:
                        self.reply_seq = rseq
                        if rtxt:
                            if rp.get("truncated"):
                                rtxt = rtxt + "……后面还有内容，要看请到界面。"
                            if self.on_change:
                                self.on_change("answer", rtxt)
                    self._reply_err = None
                except Exception as e:
                    msg = str(e)


                    if msg != getattr(self, "_reply_err", None):
                        self._reply_err = msg
                        if "401" in msg or "404" in msg:
                            print("[fairy] 助手回复接口还不存在（%s）—— "
                                  "插件的路由改动需要【重启 DSH】才生效；"
                                  "在此之前球不会念助手回复（其余功能不受影响）。" % msg, flush=True)
                        else:
                            print("[fairy] 拉取助手回复失败: %s" % msg, flush=True)
            except Exception:
                with self.lock:
                    self.ok = False
                    self.running = None
            stop.wait(POLL_MS / 1000.0)


HW_REFRESH_S = 2.0

try:
    import psutil as _psutil
except Exception as _e_ps:
    _psutil = None
    print("[fairy] psutil 不可用（内存/CPU 两条会显示 --）: %s: %s"
          % (type(_e_ps).__name__, _e_ps), flush=True)

def _hw_color(pct):
    if pct >= 90:
        return (226, 86, 58, 255)
    if pct >= 75:
        return (240, 176, 74, 255)
    return (74, 170, 235, 255)

class HwMonitor:

    def __init__(self):
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._snap = {"mem_pct": None, "mem_used": None, "mem_total": None,
                      "vram_pct": None, "vram_used": None, "vram_total": None,
                      "cpu_pct": None, "vram_src": ""}
        self._vram_total = self._read_vram_total()
        self._pdh = None
        self._pdh_at = 0.0
        self._wmi = None


        self._cpu_primed = False


    @staticmethod
    def _read_vram_total():
        best = None
        try:
            import winreg
            base = (r"SYSTEM\CurrentControlSet\Control\Class"
                    r"\{4d36e968-e325-11ce-bfc1-08002be10318}")
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base)
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                if len(sub) != 4 or not sub.isdigit():
                    continue
                try:
                    sk = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base + "\\" + sub)
                    v, _t = winreg.QueryValueEx(sk, "HardwareInformation.qwMemorySize")
                    v = int(v)
                    if v > 0 and (best is None or v > best):
                        best = v
                except Exception:
                    continue
        except Exception:
            return None
        return best


    def _pdh_open(self):
        import win32pdh
        insts = win32pdh.EnumObjectItems(None, None, "GPU Adapter Memory",
                                         win32pdh.PERF_DETAIL_WIZARD)[1]
        q = win32pdh.OpenQuery()
        hs = []
        for inst in insts:
            try:
                hs.append(win32pdh.AddEnglishCounter(
                    q, r"\GPU Adapter Memory(%s)\Dedicated Usage" % inst))
            except Exception:
                pass
        if not hs:
            win32pdh.CloseQuery(q)
            return None
        win32pdh.CollectQueryData(q)
        return (q, hs)

    def _pdh_close(self):
        if self._pdh is not None:
            try:
                import win32pdh
                win32pdh.CloseQuery(self._pdh[0])
            except Exception:
                pass
            self._pdh = None

    def _vram_pdh(self):
        try:
            import win32pdh
            now = time.time()


            if self._pdh is None or (now - self._pdh_at) > 600.0:
                self._pdh_close()
                self._pdh = self._pdh_open()
                self._pdh_at = now
                if self._pdh is not None:
                    time.sleep(0.05)
            if self._pdh is None:
                return None
            q, hs = self._pdh
            win32pdh.CollectQueryData(q)
            tot, got = 0, False
            for h in hs:
                try:
                    _t, v = win32pdh.GetFormattedCounterValue(h, win32pdh.PDH_FMT_LARGE)
                    tot += int(v)
                    got = True
                except Exception:
                    pass
            if not got:
                self._pdh_close()
                return None
            return tot
        except Exception:
            self._pdh_close()
            return None


    def _vram_wmi(self):
        try:
            import pythoncom
            import win32com.client
            pythoncom.CoInitialize()
            if self._wmi is None:
                self._wmi = win32com.client.GetObject("winmgmts:")
            rows = self._wmi.ExecQuery(
                "SELECT DedicatedUsage FROM "
                "Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory")
            tot, got = 0, False
            for r in rows:
                tot += int(r.DedicatedUsage)
                got = True
            return tot if got else None
        except Exception:
            self._wmi = None
            return None


    def _refresh(self):
        mem_pct = mem_used = mem_total = cpu = None
        if _psutil is not None:
            try:
                vm = _psutil.virtual_memory()
                mem_pct, mem_total = float(vm.percent), int(vm.total)


                mem_used = int(vm.total - vm.available)
            except Exception:
                pass
            try:
                cpu = float(_psutil.cpu_percent(interval=None))
            except Exception:
                pass
        used, src = self._vram_pdh(), "PDH"
        if used is None:
            used, src = self._vram_wmi(), "WMI"
        if used is None:
            src = ""
        total = self._vram_total
        vram_pct = None
        if used is not None and total:
            vram_pct = min(100.0, used * 100.0 / total)
        with self.lock:
            self._snap = {"mem_pct": mem_pct, "mem_used": mem_used, "mem_total": mem_total,
                          "vram_pct": vram_pct, "vram_used": used, "vram_total": total,
                          "cpu_pct": cpu, "vram_src": src}

    def snap(self):
        got = self.lock.acquire(False)
        try:
            return dict(self._snap)
        finally:
            if got:
                self.lock.release()

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="fairy-hw").start()

    def _loop(self):


        if _psutil is not None:
            try:
                _psutil.cpu_percent(interval=None)
                self._cpu_primed = True
            except Exception:
                pass
        while not self._stop.is_set():


            self._stop.wait(HW_REFRESH_S)
            if self._stop.is_set():
                break
            try:
                self._refresh()
            except Exception as e:
                print("[fairy] 硬件数据采集失败: %s: %s" % (type(e).__name__, e), flush=True)


class FairyBall(tk.Tk):
    def __init__(self):
        super().__init__()
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg="#000000")
        x, y = self._load_pos()
        self.geometry("%dx%d+%d+%d" % (WIN_W, WIN_H, x, y))
        self.canvas = tk.Canvas(self, width=WIN_W, height=WIN_H, bg="#000000", highlightthickness=0)
        self.canvas.pack()
        self.update_idletasks()
        self.update()

        self.files, self.frames_bright, self.frames_dim = load_frames()
        self.lw = LayeredWindow(self, WIN_W, WIN_H)
        try:

            self.f_big = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 24)
            self.f_sm = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 13)


            try:
                self.f_sm_b = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", 13)
            except Exception:
                self.f_sm_b = self.f_sm
        except Exception:
            self.f_big = ImageFont.load_default()
            self.f_sm = ImageFont.load_default()
            self.f_sm_b = self.f_sm
        self.side = "right"
        self.city = ""


        try:
            _bc = self._briefing_mod()._load_conf() or {}
            if _bc.get("city_confirmed") and _bc.get("city"):
                self.city = _bc["city"]
        except Exception:
            pass
        self._briefed = False
        self._brief_at = time.time() + 25
        self.balance = None
        threading.Thread(target=self._balance_loop, daemon=True).start()


        self.hw = HwMonitor()
        try:
            self.hw.start()
        except Exception as _e_hw:
            print("[fairy] 硬件数据线程没起来（面板那行会显示 --）: %s: %s"
                  % (type(_e_hw).__name__, _e_hw), flush=True)

        self._strata_cache = None
        self._strata_busy = False
        self._strata_says = []
        threading.Thread(target=self._strata_loop, daemon=True).start()
        self.idx = 0
        self.spin = 0
        self.speaker = Speaker(enabled=("--mute" not in sys.argv))


        try:
            self.after(8000, lambda: threading.Thread(
                target=self.speaker._warm_brief_cache,
                args=(getattr(self, "city", "") or "",), daemon=True,
                name="fairy-warm").start())
        except Exception as _e_w:
            print("[fairy] 播报预热没挂上（不影响播报）: %s" % _e_w, flush=True)


        self.advisor = None
        self._adv_bubbles = []
        self._adv_running = False
        try:
            import importlib.util as _iu
            _fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "work_advisor.py")
            _s = _iu.spec_from_file_location("fairy_work_advisor", _fp)
            _m = _iu.module_from_spec(_s)
            sys.modules["fairy_work_advisor"] = _m
            _s.loader.exec_module(_m)
            self.advisor = _m.Advisor(
                say=lambda _t, _lvl="advice": self.speaker.say(_t, level=_lvl),
                bubble=lambda _t: self._adv_bubbles.append(_t),
            )
            print("[fairy] 工作建议已加载（每 %s 分钟观察一次）"
                  % self.advisor.cfg.get("interval_min"), flush=True)
        except Exception as _e:
            print("[fairy] 工作建议模块加载失败（不影响球其它功能）: %s: %s"
                  % (type(_e).__name__, _e), flush=True)
        self.state = State(on_change=self._on_state)
        self._stop = threading.Event()
        self._drag = None
        self._moved = False
        self._last_state = None

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._on_right)


        self.canvas.bind("<Enter>", self._on_hover_in)
        self.canvas.bind("<Leave>", self._on_hover_out)

        threading.Thread(target=self.state.poll_loop, args=(self._stop,), daemon=True).start()
        self._tick()

    def _on_state(self, kind, text):
        try:
            if kind == "doing":

                if not getattr(self, "_was_running", False):


                    self._adv_bubbles.append("了解。开始同步。")
                self._was_running = True


                tool = ""
                try:
                    with self.state.lock:
                        tool = self.state.last_tool
                except Exception:
                    pass

                self.speaker.say(self.speaker._pick_doing_line(tool))
            elif kind == "done":
                self.speaker.say(SPEAK_DONE)
                self._was_running = False

                self._adv_bubbles.append("主人，我已完成既定目标。")
            elif kind == "answer":


                self.speaker.say(text, level="answer")
            elif kind == "token":
                self.speaker.say(text)
        except Exception as e:


            print("[fairy] _on_state(%s) 处理失败: %s" % (kind, e), flush=True)
            import traceback
            traceback.print_exc()


    def _load_pos(self):


        try:
            d = json.load(open(POS_FILE, encoding="utf-8"))
            return self._clamp(d.get("x", 0), d.get("y", 0))
        except Exception:
            vx, vy, vw, vh = virtual_screen()
            return self._clamp(vx + vw - WIN_W - MARGIN, vy + MARGIN)

    def _clamp(self, x, y):
        vx, vy, vw, vh = virtual_screen()
        return (max(vx, min(int(x), vx + vw - WIN_W)), max(vy, min(int(y), vy + vh - WIN_H)))

    def _briefing_mod(self):
        import importlib.util
        fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "briefing.py")
        s = importlib.util.spec_from_file_location("fairy_briefing", fp)
        m = importlib.util.module_from_spec(s)
        sys.modules["fairy_briefing"] = m
        s.loader.exec_module(m)
        return m

    def _set_city(self, city):
        try:
            mod = self._briefing_mod()
            if city:
                mod.save_conf({"city": city, "city_confirmed": True})
                self.city = city
                self.speaker.say("位置已设定为" + city)
            else:
                auto, _lat, _lon = mod.get_location("")
                mod.save_conf({"city": "", "city_confirmed": False, "auto_city": auto or ""})
                self.city = auto or ""
                self.speaker.say("重新自动定位到" + (auto or "未知"))
        except Exception:
            pass

    def _popup_pos(self, ww, wh):
        vx, vy, vw, vh = virtual_screen()
        px = self.winfo_x()
        py = self.winfo_y() + WIN_H + 6
        flip = False
        if py + wh > vy + vh - 8:
            py = self.winfo_y() - wh - 6
            flip = True
        if py < vy + 4:
            py = vy + 4
            flip = False
        px = max(vx + 4, min(px, vx + vw - ww - 4))
        return px, py, flip

    def _tail_x(self):
        off = 0 if self.side == "right" else BAR_W + GAP
        return off + SIZE // 2

    def _ball_center_x(self):
        off = 0 if self.side == "right" else BAR_W + GAP
        return self.winfo_x() + off + SIZE // 2

    def _bubble(self, text, seconds=4):
        try:
            old = getattr(self, "_bub", None)
            if old is not None:
                try:
                    old.destroy()
                except Exception:
                    pass
            vx, vy, vw, vh = virtual_screen()
            bx = self._ball_screen_x()
            by = self.winfo_y()

            w, ww, wh = make_bubble(self, text, tail="top", tail_x=self._tail_x())
            px, py, _flip = self._popup_pos(ww, wh)
            retail_bubble(w, self._ball_center_x(), px)
            w.geometry("+%d+%d" % (px, py))
            w.after(int(seconds * 1000), w.destroy)
            self._bub = w
        except Exception:
            pass

    def _show_text(self, lines, seconds=18):
        try:
            w = tk.Toplevel(self)
            w.overrideredirect(True)
            w.attributes("-topmost", True)
            w.configure(bg="#0a1420")
            tk.Label(w, text="\n".join(lines), justify="left", fg="#cfe3f5", bg="#0a1420",
                     font=("Microsoft YaHei", 11), padx=16, pady=12).pack()
            w.update_idletasks()
            vx, vy, vw, vh = virtual_screen()
            ww, wh = w.winfo_width(), w.winfo_height()


            px, py, _flip = self._popup_pos(ww, wh)
            w.geometry("+%d+%d" % (px, py))
            w.after(seconds * 1000, w.destroy)
        except Exception as _e:

            print("[fairy] 播报浮窗失败: %s: %s" % (type(_e).__name__, _e), flush=True)

    def _maybe_brief(self):
        if self._briefed or time.time() < self._brief_at:
            return
        with self.state.lock:
            ok = self.state.ok
        if not ok:
            return
        self._briefed = True
        _bt0 = time.time()
        print("[fairy] 播报开始 @ %s" % time.strftime("%H:%M:%S"), flush=True)
        try:
            import importlib.util
            fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "briefing.py")
            s = importlib.util.spec_from_file_location("fairy_briefing", fp)
            m = importlib.util.module_from_spec(s)
            sys.modules["fairy_briefing"] = m
            s.loader.exec_module(m)
            lines = m.build()
            print("[fairy] 开机播报 %d 句 @ %s（已全部入队）"
                  % (len(lines), time.strftime("%H:%M:%S")), flush=True)
            try:
                for _i, _l in enumerate(lines, 1):
                    print("[fairy]   播报#%d %s" % (_i, _l[:40]), flush=True)
            except Exception:
                pass

            self.speaker.say_all(lines)

            self.after(0, lambda L=list(lines): self._show_text(L))

            try:
                with open(fairy_root.log("briefing_last.txt"), "w", encoding="utf-8") as f:
                    f.write(time.strftime("%Y-%m-%d %H:%M:%S\n"))
                    f.write("\n".join(lines))
            except Exception:
                pass

            try:
                self.city, _nc = m.resolves_city()
            except Exception:
                pass


            def _watch_brief(_n=len(lines), _t0=_bt0):
                try:
                    _last = -1
                    _idle = 0
                    while True:
                        time.sleep(1.0)
                        _sp = getattr(self.speaker, "spoken", 0)
                        if self.speaker.q.empty() and _sp == _last:
                            _idle += 1
                            if _idle >= 2:
                                print("[fairy] 播报结束 %d 句，总用时 %.1fs（%.1f 秒/句）"
                                      % (_n, time.time() - _t0,
                                         (time.time() - _t0) / max(1, _n)), flush=True)
                                return
                        else:
                            _idle = 0
                        _last = _sp
                except Exception:
                    return
            threading.Thread(target=_watch_brief, daemon=True, name="fairy-brief-watch").start()
        except Exception as e:
            print("[fairy] 播报失败: %s" % e, flush=True)

    def _balance_loop(self):
        while True:
            b = read_balance()
            if b:
                self.balance = b
            time.sleep(BALANCE_REFRESH_S)

    def _ball_screen_x(self):
        return self.winfo_x() + (0 if self.side == "right" else BAR_W + GAP)

    def _save_pos(self):


        try:
            tmp = POS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"x": self._ball_screen_x(), "y": self.winfo_y()}, f)
            os.replace(tmp, POS_FILE)
        except Exception as _e:
            print("[fairy] 位置存档写入失败: %s: %s" % (type(_e).__name__, _e), flush=True)


    def _on_press(self, e):
        self._drag = (e.x_root, e.y_root, self.winfo_x(), self.winfo_y())
        self._moved = False

    def _on_drag(self, e):
        if not self._drag:
            return
        sx, sy, ox, oy = self._drag
        nx, ny = ox + (e.x_root - sx), oy + (e.y_root - sy)
        if abs(nx - ox) > 3 or abs(ny - oy) > 3:
            self._moved = True
        cx, cy = self._clamp(nx, ny)
        self.geometry("+%d+%d" % (cx, cy))

    def _on_release(self, e):
        if self._moved:
            self._save_pos()
        self._drag = None

    def _on_hover_in(self, e=None):
        print('[fairy] 鼠标进入球（悬停触发）', flush=True)
        if getattr(self, "_hover", None) is not None:
            return
        try:
            rows = []
            try:
                with self.state.lock:
                    doing = self.state.doing or ""
                    steps = self.state.steps or 0
                    ok = self.state.ok
                    prog = self.state.progress
            except Exception:
                doing, steps, ok, prog = "", 0, False, None


            rows.append("● DSH 在线" if ok else "○ DSH 未连接")
            if doing:
                rows.append("在做：" + doing)
            if steps or isinstance(prog, (int, float)):
                _pt = ("%d%%" % int(prog)) if isinstance(prog, (int, float)) else "--"
                _st = ("第 %d 步" % steps) if steps else "--"
                rows.append("进度：%s（%s）" % (_pt, _st))
            try:
                last = getattr(self.speaker, "last", "") or ""
            except Exception:
                last = ""
            if last:
                rows.append("刚说：" + last)
            if self.city:
                rows.append("位置：" + self.city)
            if self.state.mode:
                rows.append("方式：" + self.state.mode)
            try:
                _sc = getattr(self, "_strata_cache", None)
                if _sc and _sc.get("state") == "running":
                    rows.append("本地运算：运行中")
                elif _sc and _sc.get("state") == "unloaded":
                    rows.append("本地运算：已停止")
            except Exception:
                pass
            txt = "\n".join(rows)
        except Exception:
            txt = "状态读取失败"
        try:
            vx, vy, vw, vh = virtual_screen()
            bx, by = self._ball_screen_x(), self.winfo_y()


            w, ww, wh = make_bubble(self, txt, tail="top", max_w=250,
                                    tail_x=self._tail_x())
            px, py, _flip = self._popup_pos(ww, wh)
            retail_bubble(w, self._ball_center_x(), px)
            w.geometry("+%d+%d" % (px, py))
            w.update_idletasks()
            print("[fairy] 气泡 side=%s bx=%d by=%d px=%d py=%d ww=%d wh=%d 实际=%dx%d+%d+%d"
                  % (self.side, bx, by, px, py, ww, wh,
                     w.winfo_width(), w.winfo_height(), w.winfo_x(), w.winfo_y()), flush=True)
            self._hover = w
        except Exception as _e:
            import traceback
            print('[fairy] 悬停面板失败: %s' % _e, flush=True)
            traceback.print_exc()
            self._hover = None

    def _on_hover_out(self, e=None):
        print('[fairy] 鼠标离开球', flush=True)
        w = getattr(self, "_hover", None)
        if w is not None:
            try:
                w.destroy()
            except Exception:
                pass
            self._hover = None


    def _strata_probe(self, force=False):
        import socket as _sock
        now = time.time()
        c = getattr(self, "_strata_cache", None)
        if not force and c and now - c.get("t", 0) < 15:
            return c
        st = {"t": now, "state": "unknown", "loaded": None,
              "ram_used": None, "ram_total": None, "text": "状态未知"}
        try:
            _s = _sock.create_connection(("127.0.0.1", STRATA_PORT), 0.8)
            _s.close()
        except OSError:
            st.update(state="stopped", text="已停止")
            self._strata_cache = st
            return st
        try:
            with urllib.request.urlopen(STRATA_STATUS_URL, timeout=1.5) as r:
                d = json.loads(r.read().decode("utf-8", "replace") or "{}")
            loaded = bool(d.get("loaded"))
            ram = ((d.get("machine") or {}).get("ram") or {})
            st["loaded"] = loaded
            st["ram_used"] = ram.get("used_gib")
            st["ram_total"] = ram.get("total_gib")
            st["state"] = "running" if loaded else "unloaded"
            st["text"] = "运行中" if loaded else "已停止（内存已回收）"
        except Exception as _e:
            st["text"] = "状态未知"
            print("[fairy] 本地运算状态探测失败: %s: %s" % (type(_e).__name__, _e), flush=True)
        self._strata_cache = st
        return st

    def _strata_loop(self):
        while True:
            try:
                self._strata_probe(force=True)
            except Exception:
                pass
            time.sleep(20)

    def _strata_feedback(self, text, speak=None):
        try:
            self._adv_bubbles.append(text)
            if speak:
                self._strata_says.append(speak)
        except Exception:
            pass
        print("[fairy] " + text, flush=True)

    def _strata_post(self, url, timeout=30):
        body = json.dumps({}).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "{}")

    def _strata_stop(self):
        if getattr(self, "_strata_busy", False):
            self._bubble("上一个操作还在进行…", 3)
            return
        self._strata_busy = True
        self._strata_feedback("正在停止本地运算…")
        threading.Thread(target=self._strata_stop_work, daemon=True).start()

    def _strata_stop_work(self):
        try:
            try:
                _before = _free_ram_gb()
            except Exception:
                _before = None
            d = self._strata_post(STRATA_UNLOAD_URL)
            status = d.get("status")
            time.sleep(4)
            self._strata_cache = None
            after = self._strata_probe(force=True)
            _after = _free_ram_gb()
            if status == "unloaded":
                if _before is not None and _after is not None:
                    msg = "已停止本地运算，回收 %.1f GB" % (_after - _before)
                else:
                    msg = "已停止本地运算"
                self._strata_feedback(msg, speak="本地运算已停止")
            elif status == "not loaded":
                self._strata_feedback("本地运算本来就没在跑", speak="本地运算本来就没在跑")
            elif status == "busy":
                self._strata_feedback("有请求正在跑，等它跑完再试")
            else:
                self._strata_feedback("停止结果：" + str(status))
        except Exception as _e:
            self._strata_feedback("停止失败：" + str(_e)[:60])
        finally:
            self._strata_busy = False
            self._strata_cache = None

    def _strata_start(self):
        if getattr(self, "_strata_busy", False):
            self._bubble("上一个操作还在进行…", 3)
            return
        self._strata_busy = True
        st = self._strata_probe(force=True)
        if st.get("state") == "running":
            self._strata_busy = False
            self._strata_feedback("本地运算已经在跑了")
            return
        if st.get("state") == "unloaded":
            self._strata_feedback("正在启动本地运算（加载模型，约 1~2 分钟）…")
        else:
            self._strata_feedback("正在拉起本地运算服务…")
        threading.Thread(target=self._strata_start_work, args=(st.get("state"),), daemon=True).start()

    def _strata_start_work(self, state):
        try:
            if state == "unloaded":

                self._strata_feedback("已通知服务加载模型，请稍候…")
                try:
                    self._strata_post(STRATA_LOAD_URL, timeout=180)
                except Exception as _e:
                    self._strata_feedback("加载调用失败：" + str(_e)[:50])
            else:

                _cmd = _strata_cmd()
                if not _cmd:
                    self._strata_feedback("未找到 Strata 安装目录（可在 fairy_root.json 配 strata_dir）")
                    return
                import subprocess as _sp
                _lf = open(STRATA_LOG, "a", encoding="utf-8", errors="replace")
                _d = STRATA_DIR or _resolve_strata_dir()
                _sp.Popen(_cmd, stdout=_lf, stderr=_sp.STDOUT,
                          stdin=_sp.DEVNULL, cwd=_d or os.getcwd(),
                          creationflags=0x00000008 | 0x00000200 | 0x08000000,
                          close_fds=True)
                self._strata_feedback("已拉起本地运算服务，等待就绪…")
                time.sleep(8)

            ok = False
            for _ in range(36):
                self._strata_cache = None
                st = self._strata_probe(force=True)
                if st.get("state") == "running":
                    ok = True
                    break
                time.sleep(5)
            self._strata_cache = None
            if ok:
                self._strata_feedback("本地运算已就绪", speak="本地运算已就绪")
            else:
                s2 = self._strata_probe(force=True)
                self._strata_feedback("还没就绪（当前：" + str(s2.get("text")) + "），再等一会儿看看")
        except Exception as _e:
            self._strata_feedback("启动失败：" + str(_e)[:60])
        finally:
            self._strata_busy = False
            self._strata_cache = None

    def _ai_cfg(self):
        try:
            p = getattr(fairy_root, "CONFIG", None)
            c = {}
            if p and os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    c = json.load(f)
            return dict(c.get("ai") or {})
        except Exception:
            return {}

    def _save_ai_cfg(self, patch):
        try:
            path = fairy_root.CONFIG
            c = {}
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        c = json.load(f)
                except Exception:
                    c = {}
            ai = dict(c.get("ai") or {})
            ai.update(patch)
            c["ai"] = ai
            with open(path, "w", encoding="utf-8") as f:
                json.dump(c, f, ensure_ascii=False, indent=2)

            try:
                fairy_root.CFG["ai"] = ai
            except Exception:
                pass
            return True
        except Exception as e:
            self._bubble("保存 AI 配置失败：" + str(e)[:60], 4)
            return False

    def _ai_probe_models(self):
        found = []

        try:
            req = urllib.request.Request(STRATA_STATUS_URL.replace("/v1/status", "/v1/models"),
                                         headers={"content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=6) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            for m in (d.get("data") or []):
                found.append(("Strata", m.get("id") or m.get("name") or str(m)[:40]))
        except Exception:
            pass

        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=6) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            for m in (d.get("models") or []):
                found.append(("Ollama", m.get("name") or m.get("model") or ""))
        except Exception:
            pass

        for _port in (8085, 8080):
            try:
                req = urllib.request.Request("http://127.0.0.1:%d/v1/models" % _port,
                                             headers={"content-type": "application/json"})
                with urllib.request.urlopen(req, timeout=3) as r:
                    d = json.loads(r.read().decode("utf-8", "replace"))
                for m in (d.get("data") or []):
                    found.append(("LLM(:%d)" % _port, m.get("id") or m.get("model") or str(m)[:40]))
            except Exception:
                pass
        return found

    def _ai_set_key(self):
        import tkinter.simpledialog as _sd
        cur = self._ai_cfg().get("api_key") or ""
        masked = ("（已配置，共 %d 位）" % len(cur)) if cur else "（未配置）"
        val = _sd.askstring("AI 大模型 - API Key", "粘贴你的 API Key（当前%s）：" % masked,
                            parent=self)
        if val is None:
            return
        val = val.strip()
        if not val:
            self._bubble("API Key 已清空", 3)
        else:
            self._bubble("API Key 已保存（%d 位）" % len(val), 3)
        self._save_ai_cfg({"api_key": val})

    def _ai_set_model(self):
        import tkinter.simpledialog as _sd
        cur = self._ai_cfg().get("model") or ""
        val = _sd.askstring("AI 大模型 - 模型", "模型名称（当前：%s）" % (cur or "未设置"),
                            parent=self, initialvalue=cur)
        if val is None:
            return
        val = val.strip()
        self._save_ai_cfg({"model": val})
        self._bubble("模型已设为：" + (val or "（空）"), 3)

    def _ai_set_url(self):
        import tkinter.simpledialog as _sd
        cur = self._ai_cfg().get("base_url") or ""
        val = _sd.askstring("AI 大模型 - 接口地址", "OpenAI 兼容接口地址（当前：%s）" % (cur or "未设置"),
                            parent=self, initialvalue=cur)
        if val is None:
            return
        val = val.strip()
        self._save_ai_cfg({"base_url": val})
        self._bubble("接口地址已保存：" + (val or "（空）"), 3)

    def _ai_probe_now(self):
        def _work():
            found = self._ai_probe_models()
            if not found:
                self._bubble("没探测到模型（Strata 和 Ollama 都没在跑）", 4)
                return
            lines = ["可用模型："]
            for src, name in found[:12]:
                lines.append("· %s: %s" % (src, name))
            self._bubble("\n".join(lines), 6)
        threading.Thread(target=_work, daemon=True).start()

    def _local_ask(self, text, timeout=60):
        ai = self._ai_cfg()
        backend = ai.get("backend") or "strata"
        model = (ai.get("model") or "").strip()
        base_url = (ai.get("base_url") or "").strip().rstrip("/")
        api_key = (ai.get("api_key") or "").strip()
        msgs = [{"role": "user", "content": text}]

        def _pick_model(base, port):
            if model:
                return model
            try:
                req = urllib.request.Request(base + "/v1/models",
                                             headers={"content-type": "application/json"})
                with urllib.request.urlopen(req, timeout=4) as r:
                    d = json.loads(r.read().decode("utf-8", "replace"))
                for m in (d.get("data") or []):
                    return m.get("id") or m.get("model") or ""
            except Exception:
                pass
            return ""

        def _chat(url, payload, headers):
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                         headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))

        if backend == "strata":
            _base = "http://127.0.0.1:8080"
            _m = _pick_model(_base, 8080)
            d = _chat(_base + "/v1/chat/completions",
                      {"model": _m, "messages": msgs, "stream": False},
                      {"content-type": "application/json"})
            return (d.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        if backend == "ollama":
            _base = "http://127.0.0.1:11434"
            _m = model or ""
            if not _m:
                with urllib.request.urlopen(_base + "/api/tags", timeout=4) as r:
                    _dd = json.loads(r.read().decode("utf-8", "replace"))
                for mm in (_dd.get("models") or []):
                    _m = mm.get("name") or ""
                    break
            d = _chat(_base + "/api/chat",
                      {"model": _m, "messages": msgs, "stream": False},
                      {"content-type": "application/json"})
            return (d.get("message") or {}).get("content") or ""
        if backend == "llm":
            _base = base_url or "http://127.0.0.1:8085"
            _m = _pick_model(_base, 8085)
            d = _chat(_base + "/v1/chat/completions",
                      {"model": _m, "messages": msgs, "stream": False,
                       "max_tokens": 1024},
                      {"content-type": "application/json"})
            return (d.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        if backend == "custom":
            if not base_url:
                return "（自定义接口未配置 base_url）"
            _h = {"content-type": "application/json"}
            if api_key:
                _h["authorization"] = "Bearer " + api_key
            _m = model or ""
            d = _chat(base_url + "/chat/completions",
                      {"model": _m, "messages": msgs, "stream": False},
                      _h)
            return (d.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        return "（未知后端：%s）" % backend

    def _ai_ask_now(self):
        import tkinter.simpledialog as _sd
        val = _sd.askstring("用本地模型问一句", "输入你的问题：", parent=self)
        if val is None or not val.strip():
            return
        q = val.strip()

        def _work():
            self._bubble("正在问 %s…" % (self._ai_cfg().get("backend") or "本地模型"), 3)
            try:
                a = self._local_ask(q)
                if not a:
                    a = "（模型没返回内容，可能没在跑或没配好）"
                self._bubble(a[:400], 12)
            except Exception as e:
                self._bubble("问模型失败：" + str(e)[:80], 5)
        threading.Thread(target=_work, daemon=True).start()
        try:
            p = getattr(fairy_root, "CONFIG", None)
            c = {}
            if p and os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    c = json.load(f)
            return ((c.get("endpoints") or {}).get("prefer") or "auto")
        except Exception:
            return "auto"

    def _set_pref(self, k):
        try:
            p = getattr(fairy_root, "CONFIG", None)
            c = {}
            if p and os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    c = json.load(f)
            e = dict(c.get("endpoints") or {})
            e["prefer"] = k
            c["endpoints"] = e
            with open(p, "w", encoding="utf-8") as f:
                json.dump(c, f, ensure_ascii=False, indent=2)
            _nm = {"auto": "自动（DSH 优先）", "dsh": "DSH 内核",
                   "local": "本地内核（单机模式）"}.get(k, k)
            self._bubble("大脑模式已切换：" + _nm, 3)
        except Exception as e:
            self._bubble("切换大脑模式失败：" + str(e)[:60], 4)

    def _api_status_panel(self):
        def _work():
            _nm = {"auto": "自动（DSH 优先）", "dsh": "DSH 内核",
                   "local": "本地内核（单机模式）"}
            pref = self._pref()
            lines = ["【Fairy API 状态】", "大脑模式：" + _nm.get(pref, pref)]
            try:
                import fairy_endpoints as _fep
                _dsh = _fep.probe(_fep.dsh_base() + "/api/fairy/meta")
                lines.append("DSH（%s）：%s" % (_fep.dsh_base(), "在线" if _dsh else "离线"))
            except Exception:
                lines.append("DSH：未知")
            _st = self._strata_probe()
            _ram = ""
            if _st.get("ram_used") is not None and _st.get("ram_total"):
                _ram = "（内存 %.1f/%.1f GB）" % (_st["ram_used"], _st["ram_total"])
            lines.append("Strata（8080）：%s%s" % (_st.get("text", "状态未知"), _ram))
            try:
                with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as r:
                    _d = json.loads(r.read().decode("utf-8", "replace"))
                    _ms = [_m.get("name") for _m in ((_d.get("models") or [])[:3])]
                    lines.append("Ollama（11434）：在线" + ("：" + "、".join(_ms) if _ms else ""))
            except Exception:
                lines.append("Ollama（11434）：离线")
            _llm_ok = False
            for _port in (8085, 8080):
                try:
                    with urllib.request.urlopen("http://127.0.0.1:%d/v1/models" % _port, timeout=2) as r:
                        _d = json.loads(r.read().decode("utf-8", "replace"))
                        _ms = [str(_m.get("id") or _m.get("model") or "")[:40]
                               for _m in ((_d.get("data") or [])[:2])]
                        lines.append("LLM·llama.cpp（:%d）：在线" % _port
                                     + ("：" + "、".join(_ms) if _ms else ""))
                        _llm_ok = True
                        break
                except Exception:
                    continue
            if not _llm_ok:
                lines.append("LLM·llama.cpp（8085）：离线")
            _ai = self._ai_cfg()
            _key = _ai.get("api_key") or ""
            _k = ("已配置（%d 位）" % len(_key)) if _key else "未配置"
            _mdl = _ai.get("model") or "（未设置）"
            lines.append("API Key：%s　模型：%s" % (_k, _mdl))
            try:
                _bal = read_balance()
                lines.append("DeepSeek 余额：%s" % (_bal or "（无 key 或查询失败）"))
            except Exception:
                pass
            try:
                import tkinter.messagebox as _mb
                _mb.showinfo("Fairy API 状态", "\n".join(lines), parent=self)
            except Exception:
                self._bubble("\n".join(lines), 8)
        threading.Thread(target=_work, daemon=True).start()

    def _on_right(self, e):
        m = tk.Menu(self, tearoff=0)


        sub = tk.Menu(m, tearoff=0)
        for c in CITIES:
            sub.add_command(label=c, command=(lambda x=c: self._set_city(x)))
        sub.add_separator()
        sub.add_command(label="重新自动定位", command=(lambda: self._set_city("")))
        m.add_cascade(label="位置：" + (self.city or "未确认"), menu=sub)
        m.add_separator()
        m.add_command(label="重新拉取状态", command=lambda: None)
        m.add_separator()

        try:
            _ai = self._ai_cfg()
            _backend = _ai.get("backend") or "strata"
            _label = {"strata": "本地 Strata", "ollama": "本地 Ollama",
                      "custom": "自定义接口"}.get(_backend, _backend)
            _m = _ai.get("model") or ""
            ai_m = tk.Menu(m, tearoff=0)
            _bk = tk.Menu(ai_m, tearoff=0)
            for _name, _key in (("本地 Strata（8080）", "strata"),
                                ("本地 Ollama（11434）", "ollama"),
                                ("本地 LLM·llama.cpp（8085）", "llm"),
                                ("自定义接口（OpenAI 兼容）", "custom")):
                _bk.add_command(
                    label=("✓ " if _backend == _key else "  ") + _name,
                    command=(lambda k=_key: self._save_ai_cfg({"backend": k})))
            ai_m.add_cascade(label="后端：" + _label, menu=_bk)
            _pref = self._pref()
            _pname = {"auto": "自动（DSH 优先）", "dsh": "DSH 内核",
                      "local": "本地内核（单机）"}.get(_pref, _pref)
            _pm = tk.Menu(ai_m, tearoff=0)
            for _pn, _pk in (("自动（DSH 优先）", "auto"), ("DSH 内核", "dsh"),
                             ("本地内核（单机）", "local")):
                _pm.add_command(
                    label=("✓ " if _pref == _pk else "  ") + _pn,
                    command=(lambda k=_pk: self._set_pref(k)))
            ai_m.add_cascade(label="大脑模式：" + _pname, menu=_pm)
            ai_m.add_separator()
            ai_m.add_command(label="打开 API 状态…", command=self._api_status_panel)
            ai_m.add_command(label="设置 API Key…", command=self._ai_set_key)
            ai_m.add_command(label="模型名称…" + ("（" + _m + "）" if _m else ""),
                             command=self._ai_set_model)
            ai_m.add_command(label="接口地址…（自定义时用）", command=self._ai_set_url)
            ai_m.add_command(label="用本地模型问一句…", command=self._ai_ask_now)
            ai_m.add_command(label="重新探测本机模型", command=self._ai_probe_now)
            m.add_cascade(label="AI 大模型", menu=ai_m)
        except Exception as _e:
            print("[fairy] AI 大模型菜单构建失败: %s" % _e, flush=True)
        m.add_separator()

        try:
            if not (STRATA_DIR or _resolve_strata_dir()):
                m.add_command(label="本地运算（未配置 Strata）", state="disabled")
            else:
                _sst = self._strata_probe()
                _sstate = _sst.get("state")
                _ram = ""
                if _sst.get("ram_used") is not None and _sst.get("ram_total"):
                    _ram = "（内存 %.0f/%.0f GB）" % (_sst["ram_used"], _sst["ram_total"])
                if getattr(self, "_strata_busy", False):
                    m.add_command(label="本地运算：处理中…", state="disabled")
                elif _sstate == "running":
                    m.add_command(label="停止本地运算 " + _ram, command=self._strata_stop)
                elif _sstate in ("unloaded", "stopped"):
                    m.add_command(label="停止本地运算（已停止）", state="disabled")
                    m.add_command(label="启动本地运算", command=self._strata_start)
                else:

                    m.add_command(label="停止本地运算（状态未知）", command=self._strata_stop)
                    m.add_command(label="启动本地运算（状态未知）", command=self._strata_start)
        except Exception as _e:
            print("[fairy] 本地运算菜单构建失败: %s" % _e, flush=True)
        m.add_separator()
        m.add_command(label="打开助手窗口", command=self._open_ui)


        m.add_command(label="全部退出 Fairy（球+服务+语音）", command=self._quit_all)
        m.tk_popup(e.x_root, e.y_root)

    def _open_ui(self):
        threading.Thread(target=self._open_ui_job, daemon=True,
                         name="fairy-openui").start()

    def _probe_port(self, port, timeout=0.6):
        import socket as _sock
        try:
            _s = _sock.create_connection(("127.0.0.1", port), timeout)
            _s.close()
            return True
        except Exception:
            return False

    def _ui_bubble(self, text):
        try:
            self._adv_bubbles.append(text)
        except Exception:
            pass

    def _open_ui_job(self):
        import subprocess as _sp
        import webbrowser as _wb
        port = 8081
        url = "http://127.0.0.1:%d/" % port
        alive = self._probe_port(port)
        if not alive:
            _dir = os.path.dirname(os.path.abspath(__file__))
            _script = os.path.join(_dir, "fairy_ui.py")
            if not os.path.exists(_script):
                print("[fairy] 找不到 fairy_ui.py（%s），不打开浏览器" % _script, flush=True)
                self._ui_bubble("找不到助手窗口程序，没打开浏览器。")
                return


            _logp = os.path.join(fairy_root.LOGS, "fairy_ui.log")
            try:
                os.makedirs(os.path.dirname(_logp), exist_ok=True)
                print("[fairy] 助手窗口日志：%s" % _logp, flush=True)
                _lf = open(_logp, "a", encoding="utf-8", errors="replace")
                try:
                    _sp.Popen(
                        [sys.executable, _script, "--no-open"],
                        cwd=_dir, stdout=_lf, stderr=_sp.STDOUT, stdin=_sp.DEVNULL,
                        creationflags=0x00000008 | 0x00000200 | 0x08000000,
                        close_fds=True)
                finally:
                    _lf.close()
                print("[fairy] 已拉起助手窗口代理（fairy_ui.py，%s）" % _script, flush=True)
            except Exception as _e:
                print("[fairy] 拉起代理失败：%s" % _e, flush=True)
                self._ui_bubble("助手窗口没起来，暂时别开浏览器。")
                return

            for _i in range(10):
                time.sleep(0.5)
                if self._probe_port(port):
                    alive = True
                    print("[fairy] 助手窗口代理已就绪（%.1fs）" % (0.5 * (_i + 1)), flush=True)
                    break
            if not alive:
                print("[fairy] 代理拉起后 5 秒仍没监听 %s -> 不打开浏览器" % port, flush=True)
                self._ui_bubble("助手窗口没起来（端口没通），没打开浏览器。")
                return
        try:
            _wb.open(url)
            print("[fairy] 已打开：" + url, flush=True)
        except Exception as _e:
            print("[fairy] 打开浏览器失败：%s" % _e, flush=True)

    def _quit_all(self):
        try:
            import tkinter.messagebox as _mb
            if not _mb.askyesno("Fairy", "要全部退出吗？\n\n"
                                          "球 / 守护 / 本地服务 / 语音 / 本地运算都会关掉。\n"
                                          "（想只关球就选上面那条「退出 Fairy 球」）"):
                return
        except Exception:
            pass
        _dir = os.path.dirname(os.path.abspath(__file__))
        _script = os.path.join(_dir, "fairy_shutdown_all.py")
        try:
            import subprocess as _sp
            _logp = os.path.join(fairy_root.LOGS, "shutdown_all.log")
            os.makedirs(os.path.dirname(_logp), exist_ok=True)
            _lf = open(_logp, "ab")
            try:
                _sp.Popen([sys.executable, _script], cwd=_dir,
                          stdout=_lf, stderr=_sp.STDOUT, stdin=_sp.DEVNULL,
                          creationflags=0x00000008 | 0x00000200 | 0x08000000,
                          close_fds=True)
            finally:
                _lf.close()
            print("[fairy] 已送出「全部退出」任务：%s" % _script, flush=True)
        except Exception as _e:
            print("[fairy] 「全部退出」没送出去：%s: %s" % (type(_e).__name__, _e), flush=True)
            self._bubble("全部退出没送出去，可以一条条手动关", 4)
            return
        try:
            self._strata_feedback("正在全部退出…", speak="好的，全部退出")
        except Exception:
            pass

        self.after(1500, self._quit)

    def _quit(self):
        self._stop.set()
        self.destroy()


    def _pick_side(self):
        try:
            vx, vy, vw, vh = virtual_screen()
            ball_x = self.winfo_x() + (0 if self.side == "right" else BAR_W + GAP)
            center = ball_x + SIZE / 2.0
            return "right" if center < (vx + vw / 2.0) else "left"
        except Exception:
            return "right"

    def _draw_bar(self, img, ball_x, running, prog, cpct, doing, crem=None, bal=None, mode="", task_label="", steps=0):


        w = BAR_W - 16
        x0 = (ball_x + SIZE + GAP) if self.side == "right" else (ball_x - GAP - w)


        _pb_y1, _pb_y2 = 4, WIN_H - 28
        _px0, _px1 = x0 - 16, x0 + w + _PANEL_RGROW


        if self.side == "right":
            _px0 = max(_px0, int(ball_x + SIZE + 2))
        else:
            _px1 = min(_px1, int(ball_x - 2))


        _px1 = max(_px1, _px0 + 8)
        _bw = int(round(_px1 - _px0))
        _bh = int(round(_pb_y2 - _pb_y1))
        _plate = None
        if _bw > 0 and _bh > 0:


            _plate = _panel_backing(_bw, _bh)
            img.alpha_composite(_plate, (int(_px0), int(_pb_y1)))
        d = ImageDraw.Draw(img)


        def _bar_rect(x1, y1, x2, y2, r, fill, fuse=True):
            if fuse and _plate is not None:
                _panel_bar(img, _plate, _px0, _pb_y1, (x1, y1, x2, y2), r, fill)
            else:
                d.rounded_rectangle([x1, y1, x2, y2], radius=r, fill=fill)

        y0 = 12


        low = isinstance(cpct, (int, float)) and cpct >= 85
        _parts = []
        if task_label:
            _parts.append(task_label)
        elif doing:
            _parts.append(doing)
        elif running:
            _parts.append("正在执行")
        if steps and steps > 0:
            _parts.append("第 %d 步" % steps)
        if low:
            _parts.append("上下文 %d%% 快满了" % int(cpct))
        _title = "  ".join(_parts)
        _tcol = (226, 86, 58, 255) if low else (170, 206, 240, 255)


        _mw = 0
        if mode:
            mt = mode
            col2 = (96, 200, 255, 255) if mode != "串行" else (150, 176, 202, 255)
            try:
                _mw = d.textlength(mt, font=self.f_sm)
            except Exception:
                _mw = 60
            _ptext(d, (x0 + w - _mw - _PANEL_RPAD, y0 - 4), mt, self.f_sm, col2)
        if _title:

            _avail = max(40.0, float(w - _PANEL_RINSET) - _mw - 10)
            try:
                if d.textlength(_title, font=self.f_sm) > _avail:
                    _t = _title
                    while _t and d.textlength(_t + "…", font=self.f_sm) > _avail:
                        _t = _t[:-1]
                    _title = (_t + "…") if _t else ""
            except Exception:
                _title = _title[:14]
            if _title:
                _ptext(d, (x0, y0 - 4), _title, self.f_sm, _tcol)

        col = (226, 86, 58, 255) if low else ((74, 170, 235, 255) if running else (92, 124, 152, 255))


        ty = y0 + 30


        bw = max(20, int(w - _PANEL_RINSET))
        _bar_rect(x0, ty, x0 + bw, ty + 12, 6, (255, 255, 255, 46), True)
        if prog is None:
            if running:
                seg = int(bw * 0.34)
                off = int((self.spin * 7) % max(1, bw - seg))
                _bar_rect(x0 + off, ty, x0 + off + seg, ty + 12, 6, col, _PANEL_FUSE_FILL)
                self.spin += 1
        else:
            fw = max(3, int(bw * max(0.0, min(100.0, float(prog))) / 100.0))
            _bar_rect(x0, ty, x0 + fw, ty + 12, 6, col, _PANEL_FUSE_FILL)

        pct_txt = "…" if prog is None else ("%d%%" % int(prog))
        try:
            tw = d.textlength(pct_txt, font=self.f_big)
        except Exception:
            tw = 44
        _ptext(d, (x0 + w - tw - _PANEL_RPAD, y0 + 6), pct_txt, self.f_big, col)


        self._draw_hw(d, x0, bw, _bar_rect)

    def _draw_hw(self, d, x0, w, rrect=None):
        hw = getattr(self, "hw", None)
        snap = hw.snap() if hw is not None else None
        if not snap:
            return
        gap = 6
        cw = int((w - gap * 2) / 3)


        ly = WIN_H - 60
        by = WIN_H - 45
        bh = 7
        cells = (("内存", snap.get("mem_pct")),
                 ("显存", snap.get("vram_pct")),
                 ("CPU", snap.get("cpu_pct")))
        for i, (name, pct) in enumerate(cells):
            cx = x0 + i * (cw + gap)
            if isinstance(pct, (int, float)):
                pct = max(0.0, min(100.0, float(pct)))
                ipct = int(round(pct))
                txt = "%s %d%%" % (name, ipct)
                col = _hw_color(ipct)


                lab = col if ipct >= 90 else (234, 245, 255, 255)
            else:
                txt = "%s --" % name
                ipct = None
                col = None
                lab = (168, 186, 208, 255)
            _ptext(d, (cx, ly), txt, self.f_sm_b, lab)


            if rrect is not None:
                rrect(cx, by, cx + cw, by + bh, 3, (255, 255, 255, 46), True)
            else:
                d.rounded_rectangle([cx, by, cx + cw, by + bh], radius=3,
                                    fill=(255, 255, 255, 46))
            if col is not None:
                fw = max(2, int(cw * ipct / 100.0))
                if rrect is not None:
                    rrect(cx, by, cx + fw, by + bh, 3, col, _PANEL_FUSE_FILL)
                else:
                    d.rounded_rectangle([cx, by, cx + fw, by + bh], radius=3, fill=col)

    def _tick(self):


        try:
            with self.state.lock:
                running, ok = self.state.running, self.state.ok
                prog, cpct = self.state.progress, self.state.cpct
                crem = self.state.crem
                doing = self.state.doing
                mode = self.state.mode

            side = self._pick_side()
            if side != self.side:
                shift = (BAR_W + GAP) if side == "left" else -(BAR_W + GAP)
                try:
                    cx, cy = self._clamp(self.winfo_x() + shift, self.winfo_y())
                    self.geometry("+%d+%d" % (cx, cy))
                except Exception:
                    pass
                self.side = side
            base = (self.frames_bright if (running or not ok) else self.frames_dim)
            ball = base[self.idx % len(base)]
            ball_x = 0 if self.side == "right" else BAR_W + GAP
            img = Image.new("RGBA", (WIN_W, WIN_H), (0, 0, 0, 0))


            _disc = _ball_backing()
            img.paste(_disc, (ball_x, 0), _disc)
            img.paste(ball, (ball_x, 0), ball)


            self._maybe_brief()
            low_token = isinstance(cpct, (int, float)) and cpct >= 85
            if running is True or low_token:
                self._draw_bar(img, ball_x, running, prog, cpct, doing, crem, self.balance, mode,
                           self.state.task_label, self.state.steps)
            self.lw.blit(to_premultiplied_bgra(img))
            self.idx += 1


            try:
                _now = time.time()
                if _now - getattr(self, "_duck_tick_at", 0.0) >= 1.0:
                    self._duck_tick_at = _now
                    _md = getattr(self.speaker, "_md", None)
                    if _md is not None and hasattr(_md, "tick"):
                        try:
                            _md.tick()
                        except Exception as _e:
                            print("[fairy] media_duck.tick 失败: %s" % _e, flush=True)
                    _vd = getattr(self.speaker, "_vd", None)
                    if _vd is not None and hasattr(_vd, "tick"):
                        try:
                            _vd.tick()
                        except Exception as _e:
                            print("[fairy] volume_duck.tick 失败: %s" % _e, flush=True)
            except Exception as _e:
                print("[fairy] 闪避兜底 tick 出错: %s" % _e, flush=True)

            try:
                if getattr(self, "advisor", None) is not None and \
                   time.time() - getattr(self, "_adv_at", 0.0) >= 30.0:
                    self._adv_at = time.time()
                    if not self._adv_running:
                        self._adv_running = True

                        def _adv_job():
                            try:
                                self.advisor.tick()
                            except Exception as _e2:
                                print("[fairy] 工作建议轮次出错: %s: %s" % (type(_e2).__name__, _e2), flush=True)
                            finally:
                                self._adv_running = False

                        threading.Thread(target=_adv_job, daemon=True).start()
            except Exception as _e:
                print("[fairy] 工作建议调度出错: %s" % _e, flush=True)

            try:
                while getattr(self, "_adv_bubbles", None):
                    _bt = self._adv_bubbles.pop(0)
                    self._bubble(_bt, seconds=7)
            except Exception as _e:
                print("[fairy] 建议气泡失败: %s" % _e, flush=True)

            try:
                while getattr(self, "_strata_says", None):
                    _stx = self._strata_says.pop(0)
                    self.speaker.say(_stx, level="advice")
            except Exception as _e:
                print("[fairy] 本地运算播报失败: %s" % _e, flush=True)


            try:
                _nb = time.time()
                if _nb - getattr(self, "_bal_at", 0.0) >= 600:
                    self._bal_at = _nb
                    try:
                        import importlib.util as _iu2
                        _fb = os.path.join(os.path.dirname(os.path.abspath(__file__)), "balance_watch.py")
                        if os.path.exists(_fb):
                            _s2 = _iu2.spec_from_file_location("fairy_balance", _fb)
                            _bw2 = _iu2.module_from_spec(_s2)
                            sys.modules["fairy_balance"] = _bw2
                            _s2.loader.exec_module(_bw2)
                            _r2 = _bw2.check()
                            _t2 = _bw2.should_speak(_r2)
                            if _t2:
                                _lv2 = "alert" if _r2.get("level") == "alert" else "advice"
                                self.speaker.say(_t2, level=_lv2)
                                print("[fairy] 余额预警(%s): %s" % (_r2.get("level"), _t2), flush=True)
                    except Exception as _e2:
                        print("[fairy] 余额预警失败: %s: %s" % (type(_e2).__name__, _e2), flush=True)
            except Exception as _e3:
                print("[fairy] 余额预警块出错: %s" % _e3, flush=True)


            try:
                _nd = time.time()
                if _nd - getattr(self, "_dev_at", 0.0) >= 5.0:
                    self._dev_at = _nd
                    import sounddevice as _sd
                    _cur = set()
                    _ins = set()
                    for _d in _sd.query_devices():
                        _nm = str(_d.get("name") or "").strip()
                        if _nm:
                            _cur.add(_nm)
                            try:
                                if int(_d.get("max_input_channels") or 0) > 0:
                                    _ins.add(_nm)
                            except Exception:
                                pass
                    _prev = getattr(self, "_dev_set", None)
                    self._dev_set = _cur
                    if _prev is None:


                        def _is_loopback(_n):
                            return any(k in _n for k in ("立体声混音", "Stereo Mix", "Sound Mapper",
                                                         "Nahimic", "主声音", "回环", "Loopback"))
                        _realmics = sorted(x for x in _ins if not _is_loopback(x))


                        _ok = None
                        for _idx, _d in enumerate(_sd.query_devices()):
                            _nm = str(_d.get("name") or "").strip()
                            if _nm not in _realmics or int(_d.get("max_input_channels") or 0) <= 0:
                                continue
                            for _sr in (16000, 48000, 44100, 8000):
                                try:
                                    _tmp = _sd.rec(int(_sr * 0.25), samplerate=_sr, channels=1,
                                                   device=_idx, dtype="float32")
                                    _sd.wait()
                                    _ok = (_nm, _sr)
                                    break
                                except Exception:
                                    continue
                            if _ok:
                                break
                        print("[fairy] 设备基线：端点 %d，输入 %d，非回环 %d，实测能打开：%s"
                              % (len(_cur), len(_ins), len(_realmics),
                                 ("%s @%dHz" % (_ok[0][:38], _ok[1])) if _ok else "无"), flush=True)
                        if _ok:
                            try:
                                self.speaker.say("我能听见你说话了", level="advice")
                                print("[fairy] 首次基线：已确认可用麦克风 -> 已提示 (%s)" % _ok[0][:50], flush=True)
                            except Exception as _e_m:
                                print("[fairy] 首次麦克风提示失败: %s: %s" % (type(_e_m).__name__, _e_m), flush=True)
                        else:
                            print("[fairy] 首次基线：没有能打开的麦克风（只有回环或打不开的设备）", flush=True)
                    else:
                        _ev = [(x, True) for x in sorted(_cur - _prev)]
                        _ev += [(x, False) for x in sorted(_prev - _cur)]
                        if _ev and (_nd - getattr(self, "_dev_said_at", 0.0)) >= 20.0:
                            _fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fairy_device.py")
                            if os.path.exists(_fp):
                                _mod = getattr(self, "_dev_mod", None)
                                if _mod is None:
                                    import importlib.util as _iu
                                    _s = _iu.spec_from_file_location("fairy_device", _fp)
                                    _mod = _iu.module_from_spec(_s)
                                    sys.modules["fairy_device"] = _mod
                                    _s.loader.exec_module(_mod)
                                    self._dev_mod = _mod
                                _nm, _isnew = _ev[0]
                                _hs = ("耳机" in _nm) or ("Hands-Free" in _nm)
                                _sp = ("扬声器" in _nm) or ("Speaker" in _nm)
                                _mc = ("麦克风" in _nm) or ("Microphone" in _nm)
                                _kb = ("键盘" in _nm) or ("鼠标" in _nm) or ("Mouse" in _nm)
                                _st = ("U盘" in _nm) or ("Flash" in _nm)
                                _msg = _mod.设备提示(_isnew, _hs, _sp, _mc, _kb, _st)
                                if _msg:
                                    self.speaker.say(_msg, level="advice")
                                    self._dev_said_at = _nd
                                    print("[fairy] 设备提示: %s   <- %s" % (_msg, _nm[:60]), flush=True)
            except Exception as _de:
                print("[fairy] 设备检测失败: %s: %s" % (type(_de).__name__, _de), flush=True)
        except Exception as _e:

            print("[fairy] _tick 单帧出错(动画继续): %s: %s" % (type(_e).__name__, _e), flush=True)
            import traceback
            traceback.print_exc()
        finally:
            self.after(30, self._tick)

def selftest():
    files, bright, dim = load_frames()
    print("素材帧数:", len(files), "首帧:", files[0], "末帧:", files[-1])


    _b0 = np.asarray(bright[0])
    _d0 = np.asarray(dim[0])
    print("亮帧数组:", _b0.shape, _b0.dtype)
    print("暗帧数组:", _d0.shape)
    print("虚拟桌面(含多屏):", virtual_screen())

    _sb = ep.status_base()
    print("状态接口:", ep.meta_url(), "  <- 来源=%s (prefer=%s)" % (_sb["source"], _sb["prefer"]))
    print("语音候选链:", " -> ".join("%s(%s)" % (t["route"], t["voice"]) for t in ep.tts_targets()))
    try:
        with urllib.request.urlopen(ep.meta_url(), timeout=5) as r:
            j = json.loads(r.read().decode("utf-8"))
        print("接口可达 ✓ running=%s steps=%s lastTool=%s frames=%s"
              % (j.get("running"), j.get("steps"), j.get("lastTool"), j.get("frames")))
    except Exception as e:
        print("接口不可达:", str(e)[:80])
    return 0

def hw_selftest(seconds=6.0):
    def _gb(v):
        return "?" if v is None else "%.2f" % (v / float(2 ** 30))

    print("psutil:", getattr(_psutil, "__version__", "缺失"))
    print("显存总量(注册表 qwMemorySize):", HwMonitor._read_vram_total(),
          "=", _gb(HwMonitor._read_vram_total()), "GB")
    m = HwMonitor()
    print("显存-PDH :", m._vram_pdh(), "=", _gb(m._vram_pdh()), "GB")
    print("显存-WMI :", m._vram_wmi(), "=", _gb(m._vram_wmi()), "GB")
    if _psutil is not None:


        print("CPU 主线程直读(★基线伪值，别当真实占用):",
              _psutil.cpu_percent(interval=None))
        time.sleep(1.0)
        print("CPU 隔 1 秒再读:", _psutil.cpu_percent(interval=None))
    print("--- 真实线程模型（每 %.1fs 采一次，主线程只读快照）---" % HW_REFRESH_S)
    m.start()
    t0 = time.time()
    while time.time() - t0 < seconds:
        time.sleep(HW_REFRESH_S)
        s = m.snap()
        print("  t=%.1fs  内存 %s%% (%s/%s GB) · 显存 %s%% (%s/%s GB, %s) · CPU %s%%"
              % (time.time() - t0,
                 "?" if s["mem_pct"] is None else "%.1f" % s["mem_pct"],
                 _gb(s["mem_used"]), _gb(s["mem_total"]),
                 "?" if s["vram_pct"] is None else "%.1f" % s["vram_pct"],
                 _gb(s["vram_used"]), _gb(s["vram_total"]), s["vram_src"] or "拿不到",
                 "?" if s["cpu_pct"] is None else "%.1f" % s["cpu_pct"]))
    m._stop.set()
    return 0

if __name__ == "__main__":
    if "--hwselftest" in sys.argv:
        sys.exit(hw_selftest())
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    FairyBall().mainloop()
