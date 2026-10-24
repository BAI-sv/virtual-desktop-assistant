# -*- coding: utf-8 -*-
"""
fairy_ball.py —— 桌面版 Fairy 悬浮球（真正浮在所有窗口之上）

【它是什么】
  一个**独立的桌面窗口**，不是网页浮层。所以它能浮在其他程序上面、
  不占任务栏、无边框、逐像素透明 —— 这才叫"在桌面上跑"。

【和 DSH 插件的关系】
  球**没有自己的大脑**。它每 900ms 拉一次接口：
      GET <base>/api/fairy/meta
  → {frames, running, steps, lastTool, progress}
  然后按这个状态改自己的样子。**内核是 DSH，球只是显示器。**

  ★★ 2026-10-10【独立运行改造】base 不再写死 —— 见 fairy_endpoints：
      DSH 在（127.0.0.1:19387）-> 用 DSH 的，行为与改造前【完全一致】
      DSH 不在              -> 自动切本地 fairy_host（127.0.0.1:19388）
      语音则【优先直连本机 IndexTTS 9881】，DSH 变成备选
    -> 于是 DSH 关掉时，球仍然能出声、能念话、能显示状态（不再是瞎+哑）。
   排障：把配置里 endpoints.dsh_base 指到一个不通的端口，即可模拟"DSH 不在"。

  状态表现：
    running=true   球打满亮度 + 右上角一个绿点（正在干活）
    running=false  球压暗到 0.82（待机）
    接口连不上     球压暗到 0.7 且不显示点（DSH 和本地服务都没开）
    ★ 本地模式下 running 恒为 null（本地没有 agent 可观察），
      球把 null 当"未知"处理，不会误判成"已完成"。

【为什么用分层窗口而不是 -transparentcolor】
  Tk 的 -transparentcolor 只能抠【单一纯色】，素材那层蓝色柔光会留下紫边。
  这里用 Win32 UpdateLayeredWindow 直接上 32 位预乘 alpha，
  所以边缘是真的半透明。

【用法】
  py -3.12 fairy_ball.py            正常启动
  py -3.12 fairy_ball.py --selftest 只加载素材、生成一张合成图就退出（不建窗口）
"""
import ctypes
import ctypes.wintypes as _wt
import io
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

# ★ 2026-10-11【黑窗修复】统一包装 subprocess.run：默认带 CREATE_NO_WINDOW
#   （球是 pythonw 无控制台，直接跑 powershell/python 控制台程序会弹黑窗）。
#   已有 creationflags 的调用不受影响（setdefault 不覆盖）。
import subprocess as _sp_mod

# ★ 2026-10-23 常量已抽到 ball_consts
from ball_consts import *


# ★ 2026-10-23 Mixin 拆分
from ball_mixins import (
    WindowMixin, InputMixin, BubbleMixin, StrataMixin, AiCfgMixin, VisionMixin, IotMixin, AlarmMixin, SongRecMixin, ServiceMixin, EngineMixin, MenuMixin, DialogMixin, PrefMixin, UiLaunchMixin,
)



# ★ 2026-10-23 拆分：这些从 ball_extracted.py 导入
from ball_extracted import (
    ASSETS, BALANCE_API, CRED_FILE, HW_REFRESH_S, HwMonitor, LINE_POOLS_FILE, POLL_MS, SIZE, STRATA_DIR, STRATA_PORT, State, _BITMAPINFO, _BITMAPINFOHEADER, _BLENDFUNCTION, _MEMORYSTATUSEX, _PANEL_BG, _PANEL_BG_RGB, _PIL_CACHE, _PRE_SEQ, _PRE_SEQ_LOCK, _PUNCT, _dim_frame, _free_ram_gb, _hw_color, _load_line_pools, _next_prefetch_stage, _norm, _panel_bar, _pil_round, _placeholder_frame, _ptext, _resolve_strata_dir, _rm_quiet, _rnw, _round_rect, _split_for_speech, _strata_cmd, _user32, fix_mojibake, fmt_tokens, hw_selftest, load_frames, play_audio, read_balance, retail_bubble, selftest, to_premultiplied_bgra, virtual_screen,
)





# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
# ★★ 2026-10-10【独立运行改造】地址不再写死 127.0.0.1:19387
#   以前：META_URL / REPLY_URL / SAY_URL / TTS_SPEAK 全是 DSH 的地址，
#         DSH 一关，球就【瞎了 + 哑了】。
#   现在：状态/回复走 ep.meta_url() / ep.reply_url() —— 自动判断 DSH 在不在，
#         在就用 DSH（行为与改造前完全一致），不在就用本地 fairy_host(19388)。
#         语音走 ep.tts_get() —— 直连本机 IndexTTS，不再需要 DSH 当转发壳。
#   见 fairy_endpoints.py 的文档字符串。排障：把 dsh_base 指到不通的端口即可模拟 DSH 不在。
META_URL = "<动态：ep.meta_url()>"        # 仅作文档占位；实际取值见 poll_loop
REPLY_URL = "<动态：ep.reply_url()>"
# 开始干活时【说的那句话】——只说"正在执行任务"，
# 不念具体动作（具体动作仍然显示在进度条旁边）。想改台词就改这里。
# 手动选位置时的候选城市（右键菜单「位置」里）
CITIES = ["郑州", "北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "西安", "南京",
          "天津", "重庆", "长沙", "青岛", "苏州", "合肥", "济南", "福州", "厦门", "昆明",
          "沈阳", "大连", "哈尔滨", "石家庄", "太原", "南昌", "贵阳", "南宁", "兰州", "乌鲁木齐"]

SPEAK_RUNNING = "了解开始同步"          # 游戏原声「了解。开始同步。」（1.6s）
# 进度条旁边【显示】的那行字（只显示"正在执行"，不显示具体工具名）
TEXT_RUNNING = "正在执行"
# 语音：【改造前】走自装的 dsh-plugin-tts（Edge TTS，默认音色不受欢迎）。
#      【2026-10-10 改造后】语音统一走 ep.tts_get()，降级链是：
#        1) 直连 IndexTTS 9881   -> x-fairy-voice=indextts-clone  ← Fairy 克隆音色 ✓
#        2) DSH 插件 /api/fairy/say -> indextts-clone            ← 改造前的老路，行为不变
#        3) 本地 fairy_host 19388   -> indextts-clone
#        4) DSH /dsh-tts-api/speak  -> edge-tts-fallback          ← 最后手段，通用音色 ✗
#      ★ 关键实测（2026-10-10）：DSH 那个 /api/fairy/say 只是转发壳 ——
#        它自己也是 POST 9881/tts，参考音频同为 <FAIRY_ROOT>\voice\index_voices\fairy.wav。
#        所以直连 9881 与走 DSH 是【同一个音色】（log-mel 余弦 0.9998）。
#      下面两个常量保留，只用于【最后那层 Edge 兜底】和日志。
TTS_SPEAK = "<动态：ep.dsh_base() + '/dsh-tts-api/speak'>"
TTS_BASE = "<动态：ep.dsh_base()>"
SAY_URL = "<动态：ep.tts_get() 内部按链选择>"

# ---- 本地运算（Strata）控制：右键菜单的「停止 / 启动本地运算」----
# 【为什么需要】Strata 常驻 40+ GB 内存（大模型服务），停掉它能立刻回收内存。
# 契约（Strata serve 的 /v1/unload · /v1/load · /v1/status）：
#   POST /v1/unload  Content-Type: application/json  {}
#        -> {"status":"unloaded"|"not loaded"|"busy", "loaded": bool, ...}
#        Engine.unload()：Release GPU/RAM between requests, retaining the spawn
#        config for automatic reloading  <- 回收内存且可重载
#   POST /v1/load    同上 -> {"status":"loaded", ...}，加载要 1~2 分钟
#   GET  /v1/status  -> {"loaded": bool, "machine":{"ram":{used_gib,total_gib}}, ...}
#   ★ 服务端 _own_page() 要求 Content-Type: application/json 且【没有外来 Origin】











# ★ 已取消与游戏的关联
#   —— 不再【直接播放】游戏原文件。
#   设为 False 后，所有语音都必须经过【克隆】（缓存/实时合成），
#   音色仍然是游戏 Fairy 的音色（因为克隆参考音还在）。
#   想恢复“命中原声就播原文件”，把这里改回 True 即可。
PLAY_GAME_ORIGINAL = False
# 费用余额（DeepSeek 账户）
# 原声语料库：命中就播【游戏原音】——这是唯一能做到"100% 和游戏一样"的路
VOICE_DIR = fairy_root.VOICES
# 工具/动作 -> 【游戏原声台词】（键必须能命中语料库，播的就是游戏原音）
# 这样"动态播报"也是 100% 游戏声，而且语义对得上。
# 工具 -> 【游戏原声台词】。全部由语料库自动分析挑选，都已验证命中（播的是游戏原音）。
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
# ===========================================================================
# ★ 执行提示的【台词池】(2026-10-09 修「老是同一句」)
# 【病根】原来 GAME_LINE 把 84 个工具映射到【只 13 句】台词，其中 run_python/run_node
#   共用「通信结构解析进度50」—— 主代理整天跑 Python，同一句会反复播报。
#   素材利用率 13/1600 = 0.8%。
# 【修法】从语料库机械构建【池】(<FAIRY_ROOT>\voice\line_pools.json，可用
#   build_line_pools.py 重跑)，工具映射到【池】而非固定一句；抽取时随机 + 不连续重复。
# 【兜底】池文件缺失/读失败/抽不到 → 退回下面的 GAME_LINE（保证一定有声音）。
# ===========================================================================
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




LINE_POOLS = _load_line_pools()

# --------------------------------------------------------------------------
# ★【2026-10-09 新增】语音克隆预合成缓存
#   【为什么】需求：所有语音都是游戏里 Fairy 的音色。
#     IndexTTS 一停，球就退回 Edge 通用中文音色（不采用的那个）。
#     把球的固定话术【预先用 Fairy 音色合成好】缓存起来，
#     命中就直接播 —— 零延迟、不依赖 IndexTTS 在线、音色永远是 Fairy。
#   【接口】voice_cache.lookup(文本) -> wav 路径 或 None（很轻：内存字典 + 一次 exists）
# --------------------------------------------------------------------------
try:
    if os.path.dirname(os.path.abspath(__file__)) not in sys.path:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import voice_cache
except Exception as _e_vc:
    voice_cache = None
    print("[fairy] ⚠ 语音缓存模块加载失败（会退回实时克隆/原声兜底）: %s: %s"
          % (type(_e_vc).__name__, _e_vc), flush=True)

WAV_TMP = fairy_root.log("fairy_say.wav")
# ★ 2026-10-10预取专用的唯一文件名序号。
#   原来 _prefetch -> _prepare(text) 没带 stage，落盘和主线程实时合成【同一个】
#   fairy_say.wav；首开机会同时写同一个路径 -> 播到截断内容 / 重复上一句。





# ★★ 2026-10-19【用户要求「显示不出来就变两行」】
#   原来 WIN_H = SIZE + 11 = 128，面板底部那行「内33% 显46%30° C19%65°」挤在
#   1/3 列宽里，温度被隔壁标签压住（实测出现过「显 48% 3(」这种截断）。
#   加高 26px，专门腾出一行放 GPU / CPU 温度，标签就能写全。

# --------------------------------------------------------------------------
# Win32 分层窗口
# --------------------------------------------------------------------------
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080      # 不在任务栏显示
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
BI_RGB = 0
DIB_RGB_COLORS = 0

_gdi32 = ctypes.windll.gdi32








# 64 位下句柄必须显式声明，否则会被按 c_int 截断
_user32.GetWindowLongW.restype = ctypes.c_long
_user32.GetWindowLongW.argtypes = [_wt.HWND, ctypes.c_int]
_user32.SetWindowLongW.restype = ctypes.c_long
_user32.SetWindowLongW.argtypes = [_wt.HWND, ctypes.c_int, ctypes.c_long]
_user32.GetParent.restype = _wt.HWND
_user32.GetParent.argtypes = [_wt.HWND]
_gdi32.SelectObject.restype = _wt.HANDLE
_gdi32.SelectObject.argtypes = [_wt.HDC, _wt.HANDLE]


class LayeredWindow:
    """把一个 Tk 顶层窗口变成可逐像素透明的画布。"""

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
        bmi.bmiHeader.biHeight = -height          # 负值 = top-down
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








# ★ 2026-10-17【窗口边缘约束】所有 Fairy 桌面窗口离屏幕边缘至少 1 厘米，
#   绝不跑出屏幕。1 厘米 = DPI / 2.54 像素；DPI 取不到时按 96 DPI 兜底 ≈ 38px，取整 40。
try:
    _dpi = _user32.GetDpiForSystem()
    if _dpi and _dpi > 0:
        _EDGE_PX = max(30, round(_dpi / 2.54))
except Exception:
    pass


# --------------------------------------------------------------------------
# 素材
# --------------------------------------------------------------------------






# --------------------------------------------------------------------------
# 状态轮询
# --------------------------------------------------------------------------




class Corpus:
    """原声直出。

    语料库每条形如 {"t":"我在","r":"…我在。","w":"xxx.wav"}：
      t = 触发文本，r = 原句，w = 游戏原音文件名
    说话文本命中（归一化后相等）就播那段原音 —— **和游戏里一模一样**。
    命中不了才去调 TTS 插件（克隆音色，像但不可能 100% 一样）。
    """

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
            # 【审计 P1】原来静默 —— 失败会导致【所有游戏原声直出失效、悄悄退化成合成】，
            # 使用者只会觉得"声音怎么变了"，永远查不到根因。这是球最核心的卖点，必须出声。
            print("[fairy] ⚠ 游戏原声语料库加载失败，将退化为语音合成: %s: %s"
                  % (type(_e).__name__, _e), flush=True)

    def find(self, text):
        w = self.by.get(_norm(text))
        if not w:
            return None
        fp = os.path.join(self.dir, w)
        return fp if os.path.exists(fp) else None

    def fallback(self, prefer="notice"):
        """★【2026-10-09 新增】兜底：随机返回一句【游戏原声】的 wav 路径。

        【为什么需要】需求：所有语音都是游戏里 Fairy 的音色。
        克隆音色（缓存 + IndexTTS）万一都不可用时，绝不能退回 Edge 通用音色
        —— 宁可播一句语气中性的游戏原声，音色也对得上。
        """
        import random as _rnd
        cands = list(LINE_POOLS.get(prefer) or []) + list(LINE_POOLS.get("other") or [])
        _rnd.shuffle(cands)
        for s in cands[:60]:
            fp = self.find(s)
            if fp:
                return fp
        # 池子都不可用 -> 直接从语料里挑一个存在的
        ws = list(self.by.values())
        _rnd.shuffle(ws)
        for w in ws[:200]:
            if w:
                fp = os.path.join(self.dir, w)
                if os.path.exists(fp):
                    return fp
        return None


_BUB_MAGIC = "#ff00fe"      # 透明色：Windows 会把这个颜色整片变透明

# ============================================================================
# ★★【面板底色】球和提示条的背景色与悬浮框一致
#
# 【为什么改】进度条面板原来是**完全透明**的（整层只有 alpha=255 的文字和进度条，
#   别处 alpha=0）-> 桌面壁纸直接透上来，字和背景糊在一起，看不清。
#   而悬浮提示框（"DSH 在线 / 在做：…"那个气泡）是**深色实底**，一直很清楚。
#
# 【颜色从哪来】不是猜的 —— 就是 make_bubble() 里悬浮框用的那个 fill 值（下文
#   fill=_PANEL_BG 由它改来）。同一个色值，两处共用，以后改一处就够。
#
# 【怎么做到"实底"】面板像素直接给 alpha=255（原来文字和条之外是 0）。
#   UpdateLayeredWindow 收到 alpha=255 -> 不透明，壁纸透不上来。
#   球的圆外仍然是 alpha=0（不透明的地方才接收鼠标，所以面板现在也能拖）。
# ============================================================================
_PANEL_EDGE = "#2a4a6a"      # 悬浮框的描边色（同一个来源）
_PANEL_EDGE_RGB = (42, 74, 106)  # 同一个色的 RGB 元组（PIL 用）
_PANEL_FG = "#d8ecff"        # 悬浮框的文字色（同一个来源）

# ============================================================================
# ★★【球体实底】球体背景色与面板同色
#
# 【为什么还要加】上一轮只给【进度条面板】铺了实底，【球体自己】还是 alpha=0
#   （整张 128x128 只有球在发光，别处全透明）-> 球放在亮壁纸上就"糊"了：
#   米哈游这套球的素材是【浅蓝半透明发光】做的，本来是给游戏里的深色场景看的，
#   贴到白/亮桌面上整体发灰发白，看着像没渲染完。加一层深色实底 = 给它一个"底盘"。
#
# 【怎么加】球心一个【径向渐变圆盘】（不是方框）：
#     · 圆心 = 球体中心；半径 _BALL_DISC_R=62（窗口高 128 -> 半径上限 63，不会切到边）
#     · 色相 = _PANEL_BG_RGB（和面板/悬浮框【同一个色】，只动 alpha 不动 RGB）
#     · 画在【所有球体图层之下】（_tick 里先 paste 底盘，再 paste 球）
#
# 【为什么要"软边"(feather)】实测踩到的坑：球的发光一直拖到半径 75，是【渐变】的。
#   如果圆盘边缘是硬的，等于在中途把发光【一刀切断】-> 亮壁纸上能看到一圈
#   脏兮兮的暗环（第一版就是这么做的，截图对比后否掉了）。
#   所以圆盘最外 12px 做径向 alpha 渐隐 -> 发光自然淡进底盘，看不出边界。
#   （这个"软边"不碰球体像素，只作用在底盘自己的 alpha 上，所以球本身不会被压暗。）
#
# ★★【第二版：整盘径向渐变】需求：
#     「进度条的背景色中间深，渐变到外围就透明了」
#   【上一版】= 实心圆盘 + 最外 12px 线性渐隐：圆盘本体还是一块【有边界】的实色，
#     亮壁纸上能看出一圈"盘子的轮廓"（尤其球发光弱的时候）。
#   【现在】整块底盘就是一条【径向渐变】曲线：圆心 alpha 最高（深、不透），
#     向外平滑递减，到半径 R 处 alpha 正好 = 0（完全透明）-> 再也【没有盘的边界】。
#   【曲线】不是线性，是 smoothstep（t*t*(3-2t)）：
#     线性渐隐在起止两点上【斜率突变】，肉眼在亮底上仍能看出一道"淡淡的圈"；
#     smoothstep 两端导数都为 0 -> 起点无缝衔接实色段、终点柔和归零，不产生新硬边。
#   【实色段】0 ~ R*_BALL_DISC_SOLID（=27.9px）保持 alpha=255，把球心压成深底；
#     之后 R*0.45 → R（约 34px 宽）渐隐到 0。
#     —— 实色段太大会又变回"有边界的盘子"，太小则球心不够深、整体发雾；
#     0.45 是渲染 4 组对比图后挑的（见手记：实色 0.45 在亮/深底上都最干净）。
#   【描边】上一版有一圈 _PANEL_EDGE 硬描边（宽 2 / alpha 150）。
#     改成"渐变到透明"后，硬描边会正好浮在 alpha≈0 的地方 -> 变成一圈孤零零的蓝线，
#     和"外围透明"直接冲突，所以【这一版不画描边】（_BALL_DISC_EDGE_W = 0）。
#     想恢复只要把宽度设回 >0：代码里描边同样乘那条渐隐曲线，会跟着一起淡掉。
#
# 【不会碰到的】窗口还是 344x128、位置逻辑、拖动、点击热区、面板、气泡 —— 一行没动。
#   面板底板（_draw_bar 里的 _PANEL_BG_RGB 实底）也【一行没动】，仍是原来的实底。
# ============================================================================
_BALL_DISC_R = 62.0          # 底盘半径（球体显示尺寸 128 -> 半宽 64，留 2px 余量）
_BALL_DISC_SOLID = 0.45      # 实色段比例：0 ~ R*0.45 保持 alpha=255，之后渐隐到 R 处 0
_BALL_DISC_SS = 4            # 超采样倍数（4 倍画完再 LANCZOS 缩回 128）
_BALL_DISC_EDGE_W = 0        # 描边宽度（px）。★ 现在是 0 = 不画描边（理由见上）
_BALL_DISC_EDGE_A = 150      # 描边透明度（0-255）—— 只在宽度 >0 时起作用
_BALL_DISC_CACHE = {}


def _ball_backing():
    """球体底下的深色【径向渐变】圆盘，返回一张和球同尺寸的 RGBA 图。

    【画法】先在 4 倍超采样网格（512x512）上按半径直接算 alpha 曲线
    （球心 255 -> R*0.45 仍是 255 -> smoothstep 渐隐 -> R 处 0），
    再 LANCZOS 缩回 128 -> 曲线天然平滑、不会切出硬边。
    【缓存】结果和帧无关（球在动、底盘不动），所以只在第一次算一遍。
    """
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
    a = t * t * (3.0 - 2.0 * t)            # smoothstep：两端斜率为 0，落点柔和
    if _BALL_DISC_EDGE_W > 0:
        # 描边也乘同一条曲线 -> 它会跟着一起渐隐，不会在透明处留一圈孤线
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


# ============================================================================
# ★★【进度条面板底板：中间深、外围渐透】需求：
#   「进度条的背景色中间深，渐变到外围就透明了」
#
# 【上一版】底板是一块【实心】圆角矩形（fill=_PANEL_BG_RGB）外加一圈 _PANEL_EDGE
#   硬描边 —— 四条边都是硬邦邦的直线，和"渐变到透明"正相反。
# 【这一版】底板换成一张【软边圆角矩形】的 RGBA 图：
#   · 用【圆角矩形的符号距离场】算每个像素到边界的距离 d；
#   · alpha = smoothstep(d / 带宽)：向内超过一个带宽 -> 255（中间深、完全不透），
#     贴边 d=0 -> 0（外围完全透明），中间平滑过渡 -> 一条硬边都不剩；
#   · 四角由同一个距离场负责，天然比四边更淡 -> 整体观感就是「中间浓、往外化开」；
#   · 4 倍超采样 + LANCZOS 缩回，和球体底盘 _ball_backing() 同一套打法（带缓存）。
#   ★ 为什么不做成"纯径向"：面板是 208x120 的横条，而里面的文字和进度条
#     【几乎顶满】整个面板（左边距 6px、右边距 0px、上边距 4px、下边距 3px）。
#     纯径向会让左右两端的文字直接落到 alpha≈0 上 —— 那正是当初加底板要治的
#     "字糊在壁纸上"。所以取【四边向内渐隐、四角更淡】，实色段覆盖到内容区，
#     只在最外一圈化开（X/Y 带宽分开调：上下边距比左右更紧）。
# 【描边】上一版那圈硬描边正好压在 alpha≈0 的位置，会变成一圈孤零零的蓝线，
#   和"外围透明"直接冲突 -> 这一版【不画描边】（和球体底盘 _BALL_DISC_EDGE_W=0
#   是同一个决定）。
# 【可读性】渐隐到最外圈时，贴边的字（右上角"多代理 N"、百分比、底部
#   内存/显存/CPU 小条的右端）底衬会变淡，所以这一版给面板文字加了 1px 深色
#   描边（_ptext）。这只是"上保险"：实色段的位置和大小一点没变。
# 【尺寸/位置】底板矩形、文字坐标、进度条【一行没动】。
# ============================================================================
# ★★【背景再大一点，遮住右边露出部分】
#   原来左右渐隐带 12px：右对齐的文字虽然躲进了实色段，但"实色段"本身离右缘还有 12px，
#   亮壁纸仍旧从右边那一带透出来（截图里"多代理 3 / 100%"右侧发灰就是它）。
#   现在：左右带收到 6px、上下带收到 5px，并把底板【往右多铺 _PANEL_RGROW px、
#   往下铺到窗口底】—— 实色区整体变大，右边不再露出。
#   ★ 位置：窗口尺寸、球的坐标、文字坐标、进度条几何【依旧一行没动】。
_PANEL_BAND_X = 6.0       # 左右两侧的渐隐带宽（px）：贴边 0，向内 6px 到全不透
_PANEL_BAND_Y = 8.0       # ★ 2026-10-19 5->8：上下渐隐带加宽，底边不再显得硬切
_PANEL_RGROW = 6          # 底板右缘额外外扩（px）：最多贴到球左缘-2，不吃到球身上
# ★★（第三轮：左右的背景露出一样的长短）
#   实测：底板左缘到内容 = 16px，右缘到内容只有 6px（正好等于渐隐带 -> 看着像贴边）。
#   球贴在右边，底板【不能再往右长】（会压到球），所以反过来把【内容右边收 10px】：
#   轨道 / 填充 / 底下三条硬件条一律少画 _PANEL_RINSET px -> 左右各留 16px，对称。
#   ★ 只动"条子画多长"，x0、文字坐标、百分比位置、窗口尺寸一律没动。
_PANEL_RADIUS = 12.0      # 圆角半径（和原来 rounded_rectangle(radius=12) 一致）
_PANEL_GRAD_SS = 4        # 超采样倍数
_PANEL_STROKE = (4, 10, 20, 210)   # 面板文字的 1px 描边色（比底板更深的蓝黑）
_PANEL_GRAD_CACHE = {}
# ============================================================================
# ★★ 2026-10-10【右对齐文字越过底板右缘】
#   【改前实测】"多代理 N" 右缘正好压在底板最后 1 列（alpha≈0~1）、
#     百分比 "63%" 更是【探出底板 7px】，两处底下没有底衬，只剩 1px 描边撑着。
#   【怎么修】把这两处文字的右对齐位置从 x0+w+2 / x0+w+8 改成 x0+w-_PANEL_RPAD。
#   【为什么是 12】实测底板右缘的渐隐带正好 12px（见 _PANEL_BAND_X）：
#     底板中行 alpha 从 local 185 的 255 一路降到 197 列的 1 —— 也就是说
#     local<=185（= 距右缘 >=12px）才是【实色段】。右对齐到 -12 之后，
#     实测两处文字【所有】像素下面的底板 alpha 都是 255（= 实色段内）。
#   ★ 只动文字坐标：底板矩形、纵向范围、进度条、硬件条一律没动（位置布局保持不变）。
# ★★ 2026-10-10【轨道把底板"打穿"】
#   【改前实测】轨道的 fill=(255,255,255,46) 是【直接写像素】：ImageDraw 在 RGBA 上
#     不做 source-over，它把底板在该处的 alpha 从 255【覆盖】成 46 —— 轨道区永远
#     是固定 46，和周围的渐变对不上，壁纸直接透上来（实测轨道区 alpha 恒 = 46）。
#   【怎么修】见 _panel_bar()：轨道按自己的形状开一张小图块，用真 source-over
#     叠到底板上（颜色 = 轨道 over 底板底色，alpha = 底板自己的 alpha）。
#   【填充段呢】填充是【不透明】的（alpha=255），本来就不存在"透壁纸"，
#     所以默认不并进底板层（保持逐像素等价）。置 True 可让它也跟底板一起化开，
#     代价见下面那行说明。
#   ★ 已改成 True —— 填充段与底板一起渐隐，视觉更统一。
#   代价（当初默认 False 的理由，实测过，保留备查）：主板填充在实色段逐像素等价，只有
#   进度 ~100% 时右端会压到底板渐隐带；而底部那三根硬件条【整行】都坐在底板的
#   下渐隐带里（实测底板 alpha 在这 7 行是 239/238/233/207/165/115/66），
#   并进底板层后三根数据条会整体变半透明 —— 信息变淡。想还原就把上面那行改回 False。


def _panel_backing(w, h):
    """进度条面板的底板：中间深、向外围平滑渐透的【软边圆角矩形】。

    【怎么算】把画布按带宽归一化（x 除以 _PANEL_BAND_X，y 除以 _PANEL_BAND_Y），
    再算【圆角矩形符号距离场】d：d>=0 在边界外，d=0 正好贴边，d<=-1 在实色区。
    alpha = smoothstep(clamp(-d, 0, 1)) -> 中间 255、贴边 0、之间平滑过渡。
    【缓存】和帧无关（面板不动、尺寸固定），所以按尺寸只算一次。
    """
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
    A = (W / 2.0) / bx                                    # 归一化半宽（单位=1 个带宽）
    B = (H / 2.0) / by                                    # 归一化半高
    rc = (float(_PANEL_RADIUS) * ss) / (bx * by) ** 0.5   # 归一化圆角半径
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
    a = t * t * (3.0 - 2.0 * t)        # smoothstep：两端斜率为 0 -> 不产生新硬边
    arr = np.zeros((H, W, 4), np.uint8)
    arr[:, :, 0] = _PANEL_BG_RGB[0]
    arr[:, :, 1] = _PANEL_BG_RGB[1]
    arr[:, :, 2] = _PANEL_BG_RGB[2]
    arr[:, :, 3] = np.clip(a * 255.0, 0, 255).astype(np.uint8)
    im = Image.fromarray(arr, "RGBA").resize(
        (max(1, int(round(w))), max(1, int(round(h)))), Image.LANCZOS)
    _PANEL_GRAD_CACHE[key] = im
    return im












def make_bubble(parent, text, tail="left", max_w=250, tail_x=None):
    """造一个【带尖角的圆角对话气泡】，返回 (窗口, 宽, 高)。

    tail='left'  尖角在左边（气泡在球右侧，尖角指向球）
    tail='right' 尖角在右边
    tail='top'   ★尖角在上边，指向【上方的球】—— 弹窗统一放到面板正下方
                 此时用 tail_x 指定尖角在气泡内的水平位置（默认居中）。
    实现要点：Toplevel 背景设成一个"魔术色"，再用 -transparentcolor 把该色抠掉
    —— 这样才有真正的圆角 + 尖角（tkinter 没有原生圆角窗口）。
    """
    f = ("Microsoft YaHei", 10)
    w = tk.Toplevel(parent)
    w.overrideredirect(True)
    w.attributes("-topmost", True)
    w.configure(bg=_BUB_MAGIC)
    try:
        w.attributes("-transparentcolor", _BUB_MAGIC)
    except Exception:
        pass
    # 先量文字需要多大
    probe = tk.Label(w, text=text, font=f, justify="left", wraplength=max_w)
    probe.update_idletasks()
    tw, th = probe.winfo_reqwidth(), probe.winfo_reqheight()
    probe.destroy()
    pad, tailw, tailh = 12, 9, 14
    bw, bh = tw + pad * 2, th + pad * 2
    if tail == "top":                       # 朝上：尖角占【上方】的空间
        cw, ch = bw + 2, bh + tailh + 2
    else:                                   # 左右：尖角占侧向空间
        cw, ch = bw + tailw + 2, bh + 2
    cv = tk.Canvas(w, width=cw, height=ch, bg=_BUB_MAGIC, highlightthickness=0, bd=0)
    cv.pack()
    if tail == "top":
        x1, y1 = 1, tailh
    else:
        x1, y1 = (tailw if tail == "left" else 1), 1
    x2 = x1 + bw
    _round_rect(cv, x1, y1, x2, y1 + bh, 13, fill=_PANEL_BG, outline=_PANEL_EDGE, width=1)
    # 尖角（三角形），指向球
    if tail == "top":
        _tx = x1 + (tail_x if tail_x is not None else bw * 0.5)
        _tx = max(x1 + 18, min(_tx, x2 - 18))      # 别贴到圆角上
        cv.create_polygon(_tx - tailw, y1 + 1, _tx + tailw, y1 + 1, _tx, y1 - tailh + 1,
                          fill=_PANEL_BG, outline="#0b1622", tags="fairy_tail")
        # 记下几何参数：气泡被屏幕边界推走后，要用 retail_bubble() 重画尖角对准球
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


# 【已删除】原来这里有一个 _ducker()（懒加载 audio_duck.py 的**第三套**闪避实现）。
# 它**全库零调用**（已核实：`_ducker` 全局只出现 1 次 = 定义处本身），
# 而且它**没有级别门控** —— 一旦被接回来，播报"正在读取数据"也会打断正在播放的音乐。
# 现在唯一生效的闪避链是：
#     Speaker._duck_around(level 感知) -> media_duck(GSMTC 暂停) / volume_duck(压低音量)
# audio_duck.py 已删除；它原来承担的"开机自愈"职责，
# 由 volume_duck.VolumeDucker.__init__ 的启动自愈 + Speaker 线程的上电自愈承担。
# 不要接回来。








class Speaker:
    """把 Fairy 的状态"说出来"。

    优先级：【原声直出】> TTS 插件（克隆）。
    命中语料库就播游戏原音（100% 一样），命不中才让 TTS 插件合成。

    链路：状态变化 -> POST /api/fairy/say -> 插件转发 IndexTTS(9881，Fairy 克隆音色)
          -> 拿回 wav -> winsound 播放到**默认播放设备**。
    （想让声音从客厅天猫精灵出来，就把默认播放设备设成「<BT_SPEAKER>」。）

    防吵：同一句话不重复念；两条之间至少隔 2 秒；播放是同步的（播完才取下一条），
    所以句子长了会自动排队而不是叠在一起。
    """

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
        """把一整段播报【全部入队】——不走节流，否则会被 2 秒间隔丢掉大半。

        ★ 2026-10-10：改入【字典条目】。字典条目可以被 _worker **就地填上**
          path/prepared（= 上一句还在播时就把下一句合成好），
          这是"开机播报每句之间等太久"的主要修法；纯文本条目仍然兼容。
        """
        for x in lines:
            if x:
                self.q.put({"text": x, "level": level, "path": None, "prepared": False})

    def say(self, text, level="doing"):
        if not self.enabled or not text:
            return
        if getattr(self, "_dnd", False):
            return
        now = time.time()
        if text == self.last:
            return
        if now - self.last_t < 2.0:
            return
        self.last, self.last_t = text, now
        # ★★ 2026-10-20【分句入队，解决「语音总是很慢才发音」】
        #   用户问「怎么做到豆包那种实时播报」。
        #   豆包快的本质不是模型快，是【第一块好了就播，后面的边播边生成】。
        #   原来这里整段 put 一个条目，IndexTTS 要把整段合完才出声
        #   （实测 30 秒的话要 26 秒），期间播放器只能干等。
        #   现在按标点切成短句逐条入队，_worker 播第 1 句的同时
        #   _prefetch 已经在合成第 2 句 —— 第一句合完就能开口。
        for _s in _split_for_speech(text):
            self.q.put({"text": _s, "level": level, "path": None, "prepared": False})

    def _pick_doing_line(self, tool):
        """按工具选一句【能命中的】原声台词。

        【问题】"一直都是提示音用的解析进度50%…不是有 1600 条语音吗？"
        【病根】老表 GAME_LINE 把 84 个工具压到 13 句，run_python/run_node 又共用
          「通信结构解析进度50」—— 主代理整天跑 Python，所以每次都是它。
        【本方法】按 tool 找到【池】，从池里随机抽一句：
          · 避开上一次抽过的（最多重试 3 次；池里只有 1 句时允许重复，不死循环）
          · 抽到的必须在语料库里【真能命中】（Corpus.find 校验），命不中就换；
            这样绝不会出现"报了却播不出"的空台词
          · 全都不行 → 退回 GAME_LINE（老表） → 再不行 SPEAK_RUNNING（保证有声音）
        """
        try:
            pool = TOOL_POOL.get(tool) or "other"
            cands = LINE_POOLS.get(pool) or LINE_POOLS.get("other") or []
            if cands:
                import random
                last = getattr(self, "_last_doing", "")
                pick = None
                for _ in range(3):                       # 先尽量避开上次那句
                    c = random.choice(cands)
                    if c != last or len(cands) <= 1:
                        pick = c
                        break
                if pick is None:
                    pick = random.choice(cands)
                for _ in range(6):                       # 必须在语料库里命中
                    if self.corpus.find(pick):
                        self._last_doing = pick
                        return pick
                    pick = random.choice(cands)
                print("[fairy] 池 %s 抽到的台词都命不中语料库，退回 GAME_LINE" % pool, flush=True)
        except Exception as _e:
            # 【不静默】静默兜底今天已经藏过好几个 bug
            print("[fairy] 抽执行台词失败(tool=%s): %s: %s" % (tool, type(_e).__name__, _e), flush=True)
        return GAME_LINE.get(tool, SPEAK_RUNNING)

    def _duck_mod(self):
        """懒加载媒体闪避模块（失败不影响说话）。"""
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
        """懒加载音量闪避模块（另一个子代理交付的，走 Core Audio COM）。

        ⚠【多线程坑】Core Audio 需要 CoInitialize，而且**每个线程都要做一次**。
        球的 Speaker 跑在自己的线程里，所以这里必须初始化（重复调用是引用计数，无害）。
        """
        if getattr(self, "_vd", "unset") != "unset":
            return self._vd
        self._vd = None
        try:
            import ctypes
            try:
                ctypes.windll.ole32.CoInitialize(None)   # 本线程 COM 初始化
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
        """A 级发言（回答/告警）前让媒体让位，说完恢复。

        【分级】只有 level in ("answer","alert") 才让位。
        过程播报（doing）每次干活响十几次，若都让位音乐就废了 —— 所以默认不让位。

        【分流】按在播什么选方式（两个子代理的模块各管一半）：
          音乐播放器        -> media_duck.duck()（GSMTC 暂停，保留进度）
          视频/直播/游戏    -> volume_duck.duck()（压低音量，绝不暂停）
          不认识 / 没在播   -> 干跑，不打扰

        【规矩】① 只恢复自己动的（模块内 by_me 标记）
                ② 中途被手动改过则不覆盖（模块内比对）
                ③ 超时兜底（模块内 tick + deadline）；这里再加一层 finally 保底
        """
        if level not in ("answer", "alert"):
            return fn()
        md = self._duck_mod()
        tok_media = None
        tok_vol = None
        try:
            if md is not None:
                d = md.duck(level)
                if d.get("want") == "silent":
                    # ★ H5 修复：**通话中** -> 既不暂停也不压低，而且 **Fairy 自己绝不出声**。
                    # 【为什么最要紧】她的声音和通话走**同一个输出设备**，
                    #   通话时她说话 -> **对方能听见**（最怕的社交事故，不是"打扰"）。
                    # 做法：直接返回、**不调用 fn()** —— fn 就是"播放这句话"。
                    # （字幕/界面仍由 call_translate 那条链负责，不在这里。）
                    print("[fairy] 通话中：Fairy 不发言（避免被对方听到）| %s"
                          % d.get("why", ""), flush=True)
                    return None
                if d.get("want") == "volume":
                    # 分类说该压低音量（视频/游戏）-> 走另一个模块
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
        """★【开机播报预热】开机时就把播报里【固定不变的那几句】提前合成进缓存。

        为什么：播报里有几句**每次开机都一模一样**（"检测到当前位置是…"
        "穿衣建议：…" "播报完毕，随时听候吩咐。"）。它们原来每次开机都要实时合成
        （实测 4~8 秒/句），这就是听到的"间隔太长"里最冤的那部分。
        预先合成好后，第一次开机也能像第二次一样"零等待"。

        做法：只认 briefing.py 里【真的会念出来】的那些形状：
          ① lines.append("…")       —— 单段字面量
          ② lines.append("前缀" + …) —— 只取引号里那截前缀
          ③ 穿衣建议/其它函数的 return "…" —— 文案表
        带 %s 等占位符的一律跳过（每次内容不同，预热没意义）。
        🔒 只做"补缺"：已经在缓存里的直接跳过，不会重复烧 GPU。
        """
        try:
            import re as _re
            fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "briefing.py")
            if not os.path.exists(fp) or voice_cache is None:
                return
            src = open(fp, encoding="utf-8").read()
            cand = set()

            def _keep(s):
                s = (s or "").strip()
                if len(s) < 6 or "%" in s:                     # 太短的（"穿衣建议："这种前缀）不补
                    return
                if not _re.search(r"[\u4e00-\u9fff]", s):      # 必须含中文
                    return
                if s.startswith(("http", "www", "Mozilla")):
                    return
                cand.add(s)

            for m in _re.finditer(r'lines\.append\(\s*"([^"\n]*)"', src):
                _keep(m.group(1))                              # ①② 各种 append 的首段
            for m in _re.finditer(r'^\s*return\s+"([^"\n]{4,60})"\s*$', src, _re.M):
                _keep(m.group(1))                              # ③ 返回的定长文案
            for m in _re.finditer(r'^\s*lines\.append\(\s*\'([^\'\n]{4,60})\'\s*\)', src, _re.M):
                _keep(m.group(1))
            # 位置那句是【带城市名】的，按当前城市补一条
            # （Speaker 上没有 city，所以由调用方传进来；没传就跳过这条）
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
            for s in todo[:12]:                                  # 上限 12 句，别开机就烧太久
                if self.q.qsize() > 0 or getattr(self, "_briefed", False):
                    # 已经在播报了 -> 让路，别抢 GPU
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
        """播一条 + 打【语音计时】日志（合成耗时 / 播放耗时 / 距上一条的间隔）。

        ★ 为什么要有它：反馈"开机播报每句之间等太久"，而原来日志里**没有时间戳**，
        没法实测。现在每条都留下 HH:MM:SS.mmm，间隔可以直接算出来。
        tag: 缓存 / 克隆 / 兜底 / 原声
        返回本条【播完】的时刻（给下一条算间隔）。
        """
        t0 = time.time()
        gap = (t0 - _last_done) if _last_done else 0.0
        self._duck_around(level, lambda pp=path: play_audio(pp))
        t1 = time.time()
        self.spoken = getattr(self, "spoken", 0) + 1     # 已播完的条数（自检/测试用）
        # 合成耗时：本方法被调用时通常已经合成好了，真正的合成用时由调用方塞进 self._syn_ms
        syn = getattr(self, "_syn_ms", None)
        _req = getattr(self, "_tts_request_s", None)
        print("[语音计时] %s 条=%s 间隔=%.1fs 合成=%s(请求%s) 播放=%.1fs 文本=%s"
              % (time.strftime("%H:%M:%S"), tag, gap,
                 ("%.1fs" % (syn / 1000.0)) if isinstance(syn, (int, float)) else "0s(缓存)",
                 ("%.1fs" % _req) if isinstance(_req, (int, float)) else "-",
                 t1 - t0, (text or "")[:14]), flush=True)
        return t1

    def _prepare(self, text, ext=".wav", dest=None, register=True, stage=None, tag="预取/预热"):
        """把一句合成成音频文件，返回路径；失败返回 None。

        【为什么要单独抽出来】开机播报原来是"合成一句 -> 播一句 -> 再合成下一句"，
        每句之间要**干等一次合成**（IndexTTS 实测 3~4 秒/句）→ 听感就是"间隔太长"。
        抽出来之后，_worker 可以在**上一句还在播**的时候就把下一句合成好（预取），
        间隔就只剩"上一句的时长"本身。
        """
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
            self._tts_err = None            # ★ 成功了要清掉失败记忆，否则下次失败不打印
            # ★ 日志前缀保持 `语音=克隆路由(...)` 不变：
            #   voice_audit.py 按这个前缀做统计，别改格式。
            print("[fairy] 语音=克隆路由(%s|%s) %d 字节"
                  % (voice, route, len(data)), flush=True)
        else:
            why = ""
            if r:
                why = "; ".join("%s:%s" % (t.get("route"), t.get("error"))
                                for t in (r.get("tried") or []))[:200]
            # 【为什么只打印变化】同一故障会每句刷一遍日志，折中：变化才打印
            if why != getattr(self, "_tts_err", None):
                self._tts_err = why
                print("[fairy] ★ 语音通道全失败 -> 走兜底。原因：%s"
                      % (why or "(无详情)"), flush=True)
        self._syn_ms = int((time.time() - t0) * 1000)
        self._tts_request_s = round(_t_req, 1)      # 只是【请求】用了多久（不含排队等锁）
        if data is None:
            return None
        # MP3 识别：ID3 头，或任意 MPEG 帧同步字（0xFF 后高 3 位全 1）
        # —— 之前只认 \xff\xfb，漏了 \xff\xf3（Edge TTS 实际给的就是这个）
        is_mp3 = (data[:3] == b"ID3"
                  or (len(data) > 1 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0))
        # ⚠ 落盘位置：worker 和预热线程都要写文件，而 MCI 是【按文件名】打开播放的。
        #   共用一个文件名会出现"预热把正在播的那个文件覆盖了" → 播错内容。
        #   所以预热走自己的 stage 文件，主流程仍用原来的 fairy_say.<ext>。
        # ★ 2026-10-10预取（_prefetch）以前也漏了这一步，走的还是
        #   主线程那个 fairy_say.wav -> 首开机两边同时写同一路径 -> 播到截断/重复。
        #   现在预取由 _next_prefetch_stage() 给【每一条唯一】的名字，播完就删。
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
        # ★ 顺手进预合成缓存：下次开机的"现在天气/未来三天"这类句子就能秒出、不再等合成
        if register and voice_cache is not None:
            try:
                voice_cache.register(text, path)
            except Exception as e3:
                print("[fairy] 缓存登记失败(不影响播放): %s" % e3, flush=True)
        return path

    def _prefetch(self, item):
        """预取：在**当前这句还在播**的时候，先把下一条合成好，省掉句间干等。"""
        if item is None or item.get("prepared"):
            return
        if item.get("path") or item.get("hit") or item.get("corpus"):
            return
        text = item.get("text") or ""
        if not text.strip():
            return
        # 命中预合成缓存的不用预取 —— 播放时本来就是秒出
        if voice_cache is not None:
            try:
                if voice_cache.lookup(text):
                    return
            except Exception:
                pass
        item["busy"] = True                     # ★ 预取成功前不放行，避免和主线程重复合成
        try:
            # ★ 2026-10-10必须给 stage：否则和主线程抢同一个
            #   fairy_say.wav（首开机尤其明显）-> 播到截断内容 / 重复上一句。
            #   每一条一个唯一名，播完由 _worker 侧删掉。
            p = self._prepare(text, stage=_next_prefetch_stage(), tag="预取")
            if p:
                item["path"] = p
                item["prepared"] = True
        except Exception as e:
            print("[fairy] 预合成失败(不影响主流程): %s: %s" % (type(e).__name__, e), flush=True)
        finally:
            item["busy"] = False

    def _clone_ready(self):
        """克隆引擎（IndexTTS 9881）现在能不能用？

        ★ 走 ep.probe 的 20 秒 TTL 缓存，所以不会"每念一句就发一次探测"。
        ★ 用途见 _worker 里那条判断：缓存里若是别的引擎的 mp3，就别拿它糊弄。
        """
        try:
            return bool(ep.probe(ep.indextts_url()))
        except Exception:
            return False

    def _worker(self):
        # ★★ 2026-10-10【区间实测】每条语音都打一个 [语音计时] —— 以前日志没有时间戳，
        #   反馈"开机播报间隔太长"时只能靠猜。现在能直接掐出：
        #     间隔 = 上一条【播完】到这一条【开始合成】的时间（合成）＋ 这一条的时长。
        #   同行给 合成/播放 各用了多少秒，一眼看出慢在哪一段。
        _last_done = None                       # 上一条播完的时刻
        # ★ H4 修复：**上电自愈** —— 上次进程可能死在"已压低音量"的中间态。
        # 【为什么放在这里】volume_duck 走**进程内 Core Audio COM**，而 COM 是**线程级**的；
        #   这个线程就是真正做闪避/tick 的线程，在这里自愈可以避开跨套间调用。
        # 【不做的后果】音量卡在 20% -> 下次 duck() 把 20% 当"原值"存下
        #   -> **20% 变成永久音量**（音箱会被误以为坏了，越用越小声）。
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
            # ★ 2026-10-10：None 是"收工"哨兵。原来没有这个判断，
            #   收到 None 会当成文本往下走（变成长度为 0 的文本），
            #   一路走到合成那步才失败 —— 白白打一条"通道全失败"的假警报。
            if item is None:
                return
            # 队列元素现在是字典（say_all 用），也兼容旧的 (text, level) / 纯 text
            # ★ 统一成字典：下面要用 item["path"]/["prepared"] 装预取结果
            if isinstance(item, dict):
                text = item.get("text") or ""
                level = item.get("level") or "doing"
            elif isinstance(item, (tuple, list)) and len(item) == 2:
                text, level = item
                item = {"text": text, "level": level, "path": None, "prepared": False}
            else:
                text, level = item, "doing"
                item = {"text": text, "level": level, "path": None, "prepared": False}
            # ★ 空文本直接跳过（下面还可能来自其它调用方）
            if not (text or "").strip():
                continue
            # ★★ 2026-10-10【缩短句间间隔】如果队列里还压着话，就【一边播这句、
            #   一边把下一条合成好】。原来必须等这句播完才开始合成下一条，
            #   于是每两句之间都白等一次 3~4 秒的合成（听到的就是"间隔太长"）。
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
                # 0) 先试【原声直出】：命中就播游戏原音，和游戏里一模一样
                hit = self.corpus.find(text) if PLAY_GAME_ORIGINAL else None
                if hit:
                    self.hits += 1
                    self._syn_ms = None
                    _last_done = self._speak(hit, level, "原声", text, _last_done)
                    continue
                self.misses += 1
                # 1) ★【2026-10-09 新增】先查【本地克隆缓存】
                #    命中就直接播 —— 零延迟、不依赖 IndexTTS 在线、音色永远是 Fairy。
                #    这是"所有语音统一成游戏 Fairy 音色"的第一道保障。
                _cp = None
                if voice_cache is not None:
                    try:
                        _cp = voice_cache.lookup(text)
                    except Exception as _ec:
                        print("[fairy] 缓存查询失败(%s: %s)，继续走实时合成"
                              % (type(_ec).__name__, str(_ec)[:60]), flush=True)
                if _cp and _cp.lower().endswith(".mp3") and self._clone_ready():
                    # ★★【问题：天气语音不是 fairy 音色】——
                    #   克隆缓存里混进了【别的引擎】产的 .mp3：走 DSH / Edge 那条路时
                    #   登记的（voice_audit 也是拿 .mp3 当"通用音色"标记的）。
                    #   而缓存是第一优先级 -> 这些句子（天气、整点问候、余额提醒）
                    #   永远用旧音色念，怎么重启都不变 ✗。
                    #   现在：只要克隆引擎在线，就不认 mp3 缓存 -> 实时合成一遍；
                    #   合成完 register() 会用【同一个 hash】覆盖成 .wav，之后自动自愈 ✓。
                    #   ★ 克隆引擎不在线时不拦：那时 mp3 缓存正是"有声音总比没声音好"的兜底。
                    print("[fairy] 缓存(%s)是别的引擎的 mp3 -> 丢掉，改走克隆实时合成"
                          % os.path.basename(_cp), flush=True)
                    _cp = None
                if _cp:
                    self.cachehits = getattr(self, "cachehits", 0) + 1
                    # ★ 日志格式固定成 `语音=缓存(<hash>) <文本前12字>`：
                    #   voice_audit.py 靠这个前缀把「缓存」和「游戏原声」分开统计
                    print("[fairy] 语音=缓存(%s) %s"
                          % (os.path.basename(_cp).replace("cache_", "").replace(".wav", ""),
                             text[:12]), flush=True)
                    self._syn_ms = None
                    _last_done = self._speak(_cp, level, "缓存", text, _last_done)
                    continue
                # 1.5) ★★ 预取好的（上一句还在播时就已经合成完了）—— 直接播，零等待
                if isinstance(item, dict) and item.get("path"):
                    _pp = item["path"]
                    _last_done = self._speak(_pp, level, "预取", text, _last_done)
                    # ★ #3：预取文件是唯一临时文件，播完就删，别在 logs 里堆
                    _rm_quiet(_pp)
                    continue
                # 2) 缓存没有、也没预取 -> 实时合成
                # ★【2026-10-10 独立运行改造】改走 ep.tts_get() 的统一降级链：
                #     9881 直连 -> DSH /api/fairy/say -> 本地 fairy_host -> DSH tts-api
                #   好处：DSH 不在时球照样用 Fairy 克隆音色说话（不再哑巴）。
                #   拿到的 data 与改造前完全同源（同一个 IndexTTS + 同一份参考音频）。
                if isinstance(item, dict) and item.get("busy"):
                    # 预取线程正在合成这一条 —— 等它，别重复合成两遍
                    for _ in range(600):
                        if item.get("path") or not item.get("busy"):
                            break
                        time.sleep(0.1)
                    if item.get("path"):
                        _pp = item["path"]
                        _last_done = self._speak(_pp, level, "预取", text, _last_done)
                        _rm_quiet(_pp)          # ★ #3：播完删掉预取临时文件
                        continue
                _sp = self._prepare(text)
                if _sp:
                    _last_done = self._speak(_sp, level, "克隆", text, _last_done)
                    continue
                # （合成已经统一走上面的 _prepare() —— 这里只剩"合成失败怎么办"）
                # 4) ★【2026-10-09 改】兜底：不退回 Edge，改播【一句游戏原声】
                #    【为什么】需求：所有语音都是游戏里 Fairy 的音色，
                #    退回 Edge 通用音色就等于破了这个承诺。宁可播一句语气中性的原声。
                # ★【2026-10-10】注意：ep.tts_get() 内部第 4 层已经是 Edge，
                #   所以走到这里说明【克隆和 Edge 都不行了】——原声兜底仍然有意义。
                _fb = self.corpus.fallback() if PLAY_GAME_ORIGINAL else None
                if _fb:
                    self.fbhits = getattr(self, "fbhits", 0) + 1
                    print("[fairy] 语音=原声兜底（克隆不可用，拒绝退回 Edge）%s"
                          % os.path.basename(_fb), flush=True)
                    self._syn_ms = None
                    _last_done = self._speak(_fb, level, "兜底", text, _last_done)
                continue
            except Exception as _e:
                # 【审计 P0-2】原来是 except: pass —— 闪避/恢复失败会被完全吞掉。
                # 保留"不影响球"的语义，但必须出声，否则"音乐永久停住"这种事故查不到。
                import traceback
                print("[fairy] 说话链路出错（不影响球继续运行）: %s: %s"
                      % (type(_e).__name__, _e), flush=True)
                traceback.print_exc()




# --------------------------------------------------------------------------
# 实时硬件数据（内存 / 显存 / CPU）
# --------------------------------------------------------------------------
# 【为什么要一条单独的线程】显存只能走性能计数器 / WMI，第一次枚举实测 586 ms，
#   而球的 _tick 是 33fps 的绘制循环 —— 在主线程里取数会直接把动画卡住。
# 【线程模型】只有采数那条 daemon 线程写 _snap（持锁）；
#   Tk 主线程只调 snap() 拿一份【拷贝】，且拿不到锁也不等。
#   绝不从后台线程碰任何 Tk 对象（Tk 跨线程不安全）。

try:
    import psutil as _psutil
except Exception as _e_ps:
    _psutil = None
    print("[fairy] psutil 不可用（内存/CPU 两条会显示 --）: %s: %s"
          % (type(_e_ps).__name__, _e_ps), flush=True)






# --------------------------------------------------------------------------
# 悬浮球
# --------------------------------------------------------------------------


class FairyBall(WindowMixin, InputMixin, BubbleMixin, StrataMixin, AiCfgMixin, VisionMixin, IotMixin, AlarmMixin, SongRecMixin, ServiceMixin, EngineMixin, MenuMixin, DialogMixin, PrefMixin, UiLaunchMixin, tk.Tk):
    def __init__(self):
        super().__init__()
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg="#000000")
        x, y = self._load_pos()
        self.geometry("%dx%d+%d+%d" % (WIN_W, WIN_H, x, y))
        # ★★ 2026-10-19【③ 降透明度 —— 已撤销】
        #   原打算 attributes("-alpha", 0.95) 让整体略透，
        #   但球的窗口走的是 UpdateLayeredWindow（BGRA 位图逐帧重绘），
        #   和 Tk 的 -alpha 是两套机制，加进去之后整块窗口直接【什么都画不出来】
        #   （实测截图：球和面板全消失，只剩桌面）。
        #   所以透明度只能靠逐帧绘制时自己调 alpha，不能用 -alpha。
        #   想要淡一点，改 _draw_bar 里的颜色 alpha 值，别动窗口属性。
        self.canvas = tk.Canvas(self, width=WIN_W, height=WIN_H, bg="#000000", highlightthickness=0)
        self.canvas.pack()
        self.update_idletasks()
        self.update()                     # 必须先真实建窗才能拿到 HWND

        self.files, self.frames_bright, self.frames_dim = load_frames()
        # 字体初始化必须在 __init__ 里（曾经被误放进 say() 的 return 之后，
        # 变成死代码 -> f_sm/f_big 永远未定义 -> 进度条渲染抛错 -> 球变黑条）
        try:
            # 微软雅黑：默认字体渲染不了中文
            self.f_big = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 24)
            self.f_sm = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 13)
            # 【字体更明显】：加一个【粗体小字】，
            #   专给底部三条硬件标签用 —— 13px 细体压在深色底上偏虚。
            #   msyhbd.ttc 万一不在就退回细体（绝不因此起不来）。
            try:
                self.f_sm_b = ImageFont.truetype(r"C:\Windows\Fonts\msyhbd.ttc", 13)
            except Exception:
                self.f_sm_b = self.f_sm
        except Exception:
            self.f_big = ImageFont.load_default()
            self.f_sm = ImageFont.load_default()
            self.f_sm_b = self.f_sm
        self.lw = LayeredWindow(self, WIN_W, WIN_H)
        self.side = "right"
        # ★★ 2026-10-19【用户要求】悬浮窗显隐只看运行状态：
        #   运行中 -> 显示；未运行 -> 隐藏。
        #   左键单击 = 临时看 10 秒（不改状态、不循环），到时自动隐藏。
        self._panel_peek_until = 0.0   # 临时查看的截止时刻（time.time()）
        self.city = ""              # 当前（已确认的）位置
        # ★ 2026-10-10 晚修：启动时把【已保存的】位置读回来。
        #   原来 self.city 只在 _set_city()（手动选城市）时才赋值 -> 每次重启球，
        #   右键菜单都显示「位置：未确认」，可 briefing.json 里明明写的是已确认
        #   （播报用的是那份 -> 两边不一致，看着像坏了）。
        #   ★ 只读已确认的那份，未确认时【不做 IP 定位】（避免启动时多一次网络请求）。
        try:
            _bc = self._briefing_mod()._load_conf() or {}
            if _bc.get("city_confirmed") and _bc.get("city"):
                self.city = _bc["city"]
        except Exception:
            pass
        self._briefed = False                 # 开机简报只播一次
        self._brief_at = time.time() + 25      # 起来 25 秒后再播（等音频设备连上）
        self.balance = None          # 费用余额（后台每 5 分钟刷一次）
        threading.Thread(target=self._balance_loop, daemon=True).start()
        # ---- 实时硬件数据（内存/显存/CPU）：后台线程采，主线程只读快照 ----
        # 见 HwMonitor 的文档字符串：绝不在 Tk 主线程里查 WMI/性能计数器。
        self.hw = HwMonitor()
        try:
            self.hw.start()
        except Exception as _e_hw:
            print("[fairy] 硬件数据线程没起来（面板那行会显示 --）: %s: %s"
                  % (type(_e_hw).__name__, _e_hw), flush=True)
        # ---- 本地运算（Strata）状态：后台刷新，右键菜单零延迟读缓存 ----
        self._strata_cache = None
        self._strata_busy = False
        self._strata_says = []
        threading.Thread(target=self._strata_loop, daemon=True).start()
        self.idx = 0
        # ★★ 2026-10-24【动画速度】用户反馈"晃得太快"。
        #   122 帧 @33fps 时一轮只要 3.7 秒，对轻微起伏的待机动作来说太急。
        #   这里控制每帧前进多少动画帧：
        #     1.0  = 原速（3.7 秒一轮）
        #     0.5  = 一半（7.4 秒一轮）
        #     0.36 = 默认（约 10 秒一轮，接近呼吸节奏）
        #   注意：只影响动画推进速度，重绘仍是 33fps，所以不会变卡。
        self._anim_speed = 0.36
        self.spin = 0
        self.speaker = Speaker(enabled=("--mute" not in sys.argv))
        # ★ 开机播报预热：8 秒后（等 IndexTTS 引擎热起来）补合成固定句。
        #   放 after 里，不阻塞界面；真正的活在后台线程里做。
        try:
            self.after(8000, lambda: threading.Thread(
                target=self.speaker._warm_brief_cache,
                args=(getattr(self, "city", "") or "",), daemon=True,
                name="fairy-warm").start())
        except Exception as _e_w:
            print("[fairy] 播报预热没挂上（不影响播报）: %s" % _e_w, flush=True)
        # ---- 实时工作建议（work_advisor）----
        # 【为什么放球侧】主代理不是常驻的，只有球常驻 —— 所以"实时"必须由球驱动。
        # 【线程安全】advisor.tick() 里有网络调用（Ollama 最多 20s），**绝不能跑在主线程**，
        #   否则球会卡住好几秒。所以放到 daemon 线程里跑；
        #   而 Tk 的 after/窗口操作不是线程安全的 → 气泡文本只【入队】，
        #   由主线程在 _tick 里取出再弹。
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
        # ---- 窗口守护（窗口播报 + 代码审核）----
        # 和 work_advisor 同一范式：daemon 线程里 tick，气泡只入队由主线程弹。
        self.wguard = None
        self._wg_bubbles = []
        self._wg_running = False
        self._local_task = None     # 本地任务名（窗口守护等设置 -> 提示条显示）
        try:
            import importlib.util as _wg_iu
            _wg_fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "window_guard.py")
            _wg_s = _wg_iu.spec_from_file_location("fairy_window_guard", _wg_fp)
            _wg_m = _wg_iu.module_from_spec(_wg_s)
            sys.modules["fairy_window_guard"] = _wg_m
            _wg_s.loader.exec_module(_wg_m)
            self.wguard = _wg_m.WindowGuard(
                say=lambda _t, _lvl="notice": self.speaker.say(_t, level=_lvl),
                bubble=lambda _t: self._wg_bubbles.append(_t),
                progress=lambda _on, _lab: setattr(self, "_local_task", _lab if _on else None),
            )
            print("[fairy] 窗口守护已加载（窗口播报=%s，代码审核=%s）"
                  % (self.wguard.cfg.get("announce"), self.wguard.cfg.get("review")), flush=True)
        except Exception as _e:
            print("[fairy] 窗口守护加载失败（不影响球其它功能）: %s: %s"
                  % (type(_e).__name__, _e), flush=True)
        # ---- 听歌识曲（Shazam 协议，指纹识别，常开后台线程）----
        # 常开：每 18 秒抓系统正在播放的声音识别一次，识别到新歌 -> 气泡。
        # ★★ 2026-10-24【用户要求：听到什么歌就播报一下】
        #   原来 _songrec_on 没有初始值，getattr(..., False) 默认 False，
        #   于是 _songrec_loop 每 5 秒醒来一次、看到开关是关的又睡回去，
        #   从头到尾一次都没识别过（日志里连一条记录都没有）。
        #   现在默认打开：后台每 18 秒抓一次系统声音，识别到新歌就语音播报。
        #   代价是定期录 8 秒系统声音；不想要就在球的右键菜单里点「听歌识曲」关掉，
        #   关了循环立刻空转，不再录音。
        self._songrec_on = True
        self._songrec_at = 0.0
        self._songrec_last = ""
        threading.Thread(target=self._songrec_loop, daemon=True).start()
        # ★ 2026-10-11【快捷功能】免打扰 / 定时提醒
        self._dnd = False
        self._reminders = []
        threading.Thread(target=self._remind_loop, daemon=True).start()
        # ★ 2026-10-11【天气预警 + 夜间模式】主动推送 / 定时自动免打扰
        self._alert_last = ""
        threading.Thread(target=self._alert_loop, daemon=True).start()
        threading.Thread(target=self._night_loop, daemon=True).start()
        self.state = State(on_change=self._on_state)
        self._stop = threading.Event()
        self._drag = None
        self._moved = False
        self._last_state = None

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._on_right)
        # 鼠标悬停：显示"最近一句 + 状态"（移开就消失）
        # —— 这是"要的时候有、不要的时候没有"的形态，比常驻气泡好
        self.canvas.bind("<Enter>", self._on_hover_in)
        self.canvas.bind("<Leave>", self._on_hover_out)

        threading.Thread(target=self.state.poll_loop, args=(self._stop,), daemon=True).start()
        self._tick()

    def say(self, text, level="doing"):
        """★ 2026-10-12 修复：FairyBall 继承 tk.Tk 没有 say，转发给 speaker。
        否则"保存失败: '_tkinter.tkapp' object has no attribute 'say'"。"""
        try:
            if text and level in ("doing", "answer", "alert", "advice"):
                try:
                    self._flash_bar((text or "")[:28])
                except Exception:
                    pass
            return self.speaker.say(text, level=level)
        except Exception:
            return None



    # ---- 位置 ----
















    # ---- 拖动（增量式，容器/多屏都稳） ----







    # ================= 本地运算（Strata）控制 =================
































    # ---- ★ 2026-10-11【视觉工具】截图取字（RapidOCR）+ 目标检测（YOLO11）----





    # ---- ★ 2026-10-11【设备与传感器】自动扫描 + 连接 ----




    # ---- ★ 2026-10-11【安全自检】病毒/漏洞只读检查 ----















    # ---- 听歌识曲（Shazam 指纹识别，默认关闭）----








        # _ans is None（窗口被关掉）-> 不标记，下次启动再问





    # ★ 2026-10-17【DSH 模型联动】fairy 组合 -> DSH 主对话默认模型（provider, model）
    _DSH_MODEL_BY_ENG = {
        "strata": ("strata", "qwen3.8-flash-next-abliterated-iq3_s"),
        "llm":    ("llm", "qwen3.5-9b"),
        "vl":     ("vl", "qwen3-vl-8b"),
    }


























    def _quit_all(self):
        """右键：全部退出 —— 球 + 守护 + 本地服务 + 语音 + 本地运算，一次收干净。

        ★ 为什么交给一个【脱离启动的】小脚本干（fairy_shutdown_all.py）：
          本进程（球）自己也要关，而"自己关自己"时 destroy() 之后的代码
          不保证跑得完 —— 所以先把活儿送出去，再关自己。
        【顺序】脚本里是 语音 -> 守护 -> 本地服务 -> 本地运算 -> 球：
          守护必须先于语音之后停，否则它看到 9881 没了会立刻又拉起来。
        """
        try:
            import tkinter.messagebox as _mb
            if not _mb.askyesno("Fairy", "要全部退出吗？\n\n"
                                          "球 / 守护 / 本地服务 / 语音 / 本地运算都会关掉。\n"
                                          "DSH 也会一起关掉。\n"
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
                _lf.close()          # 句柄已复制给子进程，父进程这份要关
            print("[fairy] 已送出「全部退出」任务：%s" % _script, flush=True)
        except Exception as _e:
            print("[fairy] 「全部退出」没送出去：%s: %s" % (type(_e).__name__, _e), flush=True)
            self._bubble("全部退出没送出去，可以一条条手动关", 4)
            return
        try:
            self._strata_feedback("正在全部退出…", speak="好的，全部退出")
        except Exception:
            pass
        # 自己最后走（留 1.5 秒让脚本先把"停别人"的活儿接过去）
        self.after(1500, self._quit)

    def _quit(self):
        self._stop.set()
        self.destroy()

    # ---- 动画 ----



    def _tick(self):
        # ★ 2026-10-10本函数原来【没有顶层 try】：一旦哪一段抛异常，
        #   最后那行 after 就不会再排队 -> 动画【永久冻住】（球定格、面板不再刷新）。
        #   现在整段包进 try/finally，异常照样记日志，但 after 永远会重排 -> 动画不死。
        try:
            with self.state.lock:
                running, ok = self.state.running, self.state.ok
                prog, cpct = self.state.progress, self.state.cpct
                crem = self.state.crem
                doing = self.state.doing
                mode = self.state.mode
            # ★ 2026-10-12【临时进度优先】启动/组合切换/扫描设备等临时进度
            #   画在球右侧进度条面板（_ball_progress 设置），显示完自动回到宿主状态。
            _tp = getattr(self, "_temp_prog", None)
            if _tp:
                running, prog, doing = True, _tp[0], _tp[1]
            # 侧向：变了就把窗口挪一下，让球在屏幕上原地不动
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
            # ★★ 2026-10-24【动画速度可调】用户说"晃得太快"。
            #   原来 self.idx 每帧 +1，122 帧按 33fps 跑完只要 3.7 秒，
            #   对这类"轻微起伏"的待机动作来说太快，看着像在抽筋。
            #   现在 idx 用浮点累加、取帧时才取整，速度由 _anim_speed 控制：
            #     1.0 = 原来那样（3.7 秒一轮）
            #     0.36 ≈ 10 秒一轮（默认，接近呼吸感）
            #   保持 33fps 重绘不变，所以不会卡顿，只是每帧前进得更少。
            ball = base[int(self.idx) % len(base)]
            ball_x = 0 if self.side == "right" else BAR_W + GAP
            img = Image.new("RGBA", (WIN_W, WIN_H), (0, 0, 0, 0))
            # ★★【2026-10-10 球体实底】先铺【球体底下的深色圆盘】，再把球画在它【之上】。
            #   —— 球的背景色与面板同色（_PANEL_BG / _PANEL_EDGE）。
            #   顺序很关键：底盘是"最底层"，球的多层发光、光环都盖在它上面。
            _disc = _ball_backing()
            img.paste(_disc, (ball_x, 0), _disc)
            img.paste(ball, (ball_x, 0), ball)
            # 按要求：**只在执行任务时出现进度条**；待机时那一侧完全透明
            # （分层窗口 alpha=0 的地方本来就不接收鼠标，所以不会挡住桌面）
            # 例外：额度快满（>=85%）时也画，否则那条告警就看不见了。
            self._maybe_brief()
            low_token = isinstance(cpct, (int, float)) and cpct >= 85
            _lt = getattr(self, "_local_task", None)
            # ★ 2026-10-12【硬件状态常驻】不再只在任务时画面板：
            #   待机也一直显示内存/显存/CPU 三条硬件条（标题显示"待机"）。
            #   任务/临时进度/额度告警照常优先显示。
            # ★★ 2026-10-19【用户要求】悬浮窗显隐只看运行状态：
            #   运行中 -> 显示；未运行 -> 隐藏；
            #   左键单击 -> 临时看 10 秒（_panel_peek_until），到点自动收。
            import time as _t
            _tmp = getattr(self, "_temp_prog", None)
            _peek = _t.time() < float(getattr(self, "_panel_peek_until", 0.0) or 0.0)
            _show = bool(running or _tmp or _peek)
            if _show:
                try:
                    self._draw_bar(img, ball_x, running, prog, cpct, doing, crem, self.balance, mode,
                               _lt or self.state.task_label, self.state.steps)
                except Exception as _e_bar:
                    # 进度条画失败【绝不能拖垮球体】：只记日志，球照常显示
                    print("[fairy] 进度条渲染失败（跳过该帧进度条）: %s" % _e_bar, flush=True)
            self.lw.blit(to_premultiplied_bgra(img))
            # ★★ 2026-10-24【动画减速】原来是 self.idx += 1（3.7 秒一轮太快）。
            #   改成浮点累加：每帧只前进 _anim_speed 帧，取帧时再 int()。
            self.idx += float(getattr(self, "_anim_speed", 0.36))
            # ---- ★超时兜底"上电"（审计 P0：tick() 从未被调用 → 保险是死的）----
            # 【为什么必须做】万一 unduck() 失败，音乐会永久停在暂停态 / 音量永久卡在 20%。
            # 【为什么只 tick 已加载的】不在主线程触发模块加载 —— volume_duck 走进程内
            #   Core Audio COM，跨套间创建/调用有风险，所以只对 Speaker 线程已建好的实例 tick。
            # 【节流】帧循环 33fps，不能每次都调；1 秒一次足够（模块内 deadline 是 60 秒）。
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
            # ---- 工作建议：每 30 秒检查一次；真观察由 advisor 自己按 interval_min 决定 ----
            try:
                if getattr(self, "advisor", None) is not None and \
                   time.time() - getattr(self, "_adv_at", 0.0) >= 30.0:
                    self._adv_at = time.time()
                    if not self._adv_running:                 # 防重入（上一轮还没跑完就跳过）
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
            # ---- 窗口守护：每 20 秒观察一次（窗口播报 + 代码审核）----
            try:
                if getattr(self, "wguard", None) is not None and \
                   time.time() - getattr(self, "_wg_at", 0.0) >= 20.0:
                    self._wg_at = time.time()
                    if not self._wg_running:                 # 防重入
                        self._wg_running = True

                        def _wg_job():
                            try:
                                self.wguard.tick()
                            except Exception as _e2:
                                print("[fairy] 窗口守护轮次出错: %s: %s"
                                      % (type(_e2).__name__, _e2), flush=True)
                            finally:
                                self._wg_running = False

                        threading.Thread(target=_wg_job, daemon=True).start()
            except Exception as _e:
                print("[fairy] 窗口守护调度出错: %s" % _e, flush=True)
            # 气泡由【主线程】弹（Tk 跨线程不安全）
            try:
                while getattr(self, "_adv_bubbles", None):
                    _bt = self._adv_bubbles.pop(0)
                    self._bubble(_bt, seconds=7)
            except Exception as _e:
                print("[fairy] 建议气泡失败: %s" % _e, flush=True)
            # 窗口守护气泡（主线程弹）
            try:
                while getattr(self, "_wg_bubbles", None):
                    _bt = self._wg_bubbles.pop(0)
                    self._bubble(_bt, seconds=7)
            except Exception as _e:
                print("[fairy] 窗口守护气泡失败: %s" % _e, flush=True)
            # 本地运算的语音反馈：同样只在这里（主线程）播
            try:
                while getattr(self, "_strata_says", None):
                    _stx = self._strata_says.pop(0)
                    self.speaker.say(_stx, level="advice")
            except Exception as _e:
                print("[fairy] 本地运算播报失败: %s" % _e, flush=True)
            # ---- 设备接入提示（问题：连上蓝牙耳机却没提示没声音）----
            # 【设计要点】
            #  1) 只用 sounddevice.query_devices()（进程内、百毫秒级）
            #     —— 绝不用 PowerShell Get-PnpDevice（1~2 秒）会把 33fps 帧循环拖死
            #  2) 节流 5 秒一次
            #  3) ★首次只建基线、绝不出声（耳机当前就连着，否则会莫名喊一句）
            #  4) 用集合差异判断增减 —— 每台设备只会进 _added 一次，天然不会重复报
            #  5) 「说什么」的逻辑在 CNSH 里（fairy_device.cnsh -> .py），这里只做分类+播放
            #  6) 再叠一层 20 秒全局节流，防设备抖动连珠炮
            try:
                _nd = time.time()
                if _nd - getattr(self, "_dev_at", 0.0) >= 5.0:
                    self._dev_at = _nd
                    import sounddevice as _sd
                    _cur = set()
                    _ins = set()          # ★ 只收【输入】端点名 —— 判断"有没有可用的耳朵"
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
                        # ★ 修复【蓝牙耳机连上无提示无声】
                        #   原设计"首次只建基线不出声"没错，但漏了一种情况：
                        #   耳机在【基线建立之前】就连着 → 没有"新增"事件 → 一声不吭。
                        #   所以：首次建基线时，如果【已经存在可用的输入设备】→ 主动说一声。
                        #   ⚠ 必须排除回环（立体声混音 / Sound Mapper / Nahimic），否则会录到自己的声音。
                        def _is_loopback(_n):
                            return any(k in _n for k in ("立体声混音", "Stereo Mix", "Sound Mapper",
                                                         "Nahimic", "主声音", "回环", "Loopback"))
                        _realmics = sorted(x for x in _ins if not _is_loopback(x))
                        # ★★【关键修正 2026-10-09】名字有输入通道 ≠ 能打开！
                        #   本机实测：板载麦 / 线路输入 / bthhfenum 打开一律 Errno -9999。
                        #   我第一版只看 max_input_channels>0，结果把【线路输入】当成可用麦克风报了出去 ✗
                        #   → 所以首次建基线时做【一次真开测试】（只在启动做一次，允许 1~3 秒；
                        #     帧循环里绝不做，那会把 33fps 拖死 —— 代码注释早有此约束）。
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
                                # ★ 2026-10-12：不再播"我能听见你说话了"（用户要求删除）；
                                #   启动播报内容由「启动播报内容」设置控制。
                                print("[fairy] 首次基线：已确认可用麦克风 (%s)" % _ok[0][:50], flush=True)
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
            # ★ 不静默：说明是哪一类错，避免像「球卡住但日志干净」这种查不出来的状态
            print("[fairy] _tick 单帧出错(动画继续): %s: %s" % (type(_e).__name__, _e), flush=True)
            import traceback
            traceback.print_exc()
        finally:
            self.after(30, self._tick)          # ~33fps ★ 必须永远重排（#5）






# ===========================================================================
# ★★ 2026-10-24【修·_panel_backing 未定义】
#   Mixin 的方法体在【调用时】才查全局名，但 _panel_backing / make_bubble
#   这些函数定义在文件后半段（L700+）。之前把 bind_globals 放在 import 区
#   末尾（L100），那时它们还不存在，注入失败，于是每帧都刷
#     「进度条渲染失败: name '_panel_backing' is not defined」
#   移到文件最末尾（所有定义都执行完之后）再绑定。
# ===========================================================================
from ball_mixins import bind_globals as _bind_mixin_globals
_bind_mixin_globals(globals())


if __name__ == "__main__":
    if "--hwselftest" in sys.argv:
        sys.exit(hw_selftest())
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--testbar" in sys.argv:
        # ★ 2026-10-12【提示条自检】强制画提示条 + 截屏落盘，
        #   验证"任务运行时进度条/提示条"机制本身是否正常。
        from PIL import ImageGrab
        try:
            import fairy_root as _fr
        except Exception:
            _fr = None
        _app = FairyBall()
        _app.update_idletasks()
        _app.state.running = True
        _app.state.progress = 62
        _app.state.doing = "提示条自检：任务执行中"
        _app._local_task = "提示条自检任务"
        _out = os.path.join(getattr(_fr, "LOG", r"D:\FAIRY\logs") if _fr else r"D:\FAIRY\logs",
                            "ball_testbar.png")
        def _shot():
            try:
                _x, _y = _app.winfo_x(), _app.winfo_y()
                _img = ImageGrab.grab((max(0, _x - 60), max(0, _y - 60),
                                       _x + WIN_W + 60, _y + WIN_H + 60))
                _img.save(_out)
                print("testbar saved: " + _out, flush=True)
            except Exception as _e:
                print("testbar capture fail: %s" % _e, flush=True)
            _app.destroy()
        _app.after(2600, _shot)
        _app.mainloop()
        sys.exit(0)
    _app = FairyBall()
    _app._boot_progress()
    _app.mainloop()
