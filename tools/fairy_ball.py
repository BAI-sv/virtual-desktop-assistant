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


def _rnw(*_a, **_kw):
    _kw.setdefault("creationflags", getattr(_sp_mod, "CREATE_NO_WINDOW", 0))
    return _sp_mod.run(*_a, **_kw)

# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
ASSETS = os.path.join(fairy_root.ASSETS, "ball_128")
POS_FILE = fairy_root.BALL_POS
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
SPEAK_DONE = "主人我已完成既定目标"      # 游戏原声「主人，我已完成既定目标。」（2.2s）
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
STRATA_PORT = 8080
STRATA_STATUS_URL = "http://127.0.0.1:8080/v1/status"
STRATA_LOAD_URL = "http://127.0.0.1:8080/v1/load"
STRATA_UNLOAD_URL = "http://127.0.0.1:8080/v1/unload"


def _resolve_strata_dir():
    """Strata 安装目录：环境变量 STRATA_DIR > fairy_root.json 的 strata_dir > 常见位置探测。
    找不到返回 None（此时"本地运算"菜单项不提供）。"""
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
    """按当前机器拼 Strata 启动命令；没探测到目录返回 None。"""
    _d = STRATA_DIR or _resolve_strata_dir()
    if not _d:
        return None
    # ★★ 2026-10-11 修【黑窗】：这里原来用 venv 的 python.exe —— 它只是个"启动器"，
    #   会【再启动一次真解释器】，那次二次启动 creationflags 管不到，于是弹出一个
    #   Windows Terminal 黑窗（用户当场截到：标题 = E:\Strata\.venv\Scripts\python.exe）。
    #   改成同目录的 pythonw.exe 就不会创建控制台（和 IndexTTS 那边同一个解法）。
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
    """当前可用物理内存（GB）。★ 用 Win32 GlobalMemoryStatusEx，不依赖 psutil。失败返回 None。"""
    try:
        _st = _MEMORYSTATUSEX()
        _st.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(_st)):
            return _st.ullAvailPhys / float(1024 ** 3)
    except Exception:
        pass
    return None

# ★ 已取消与游戏的关联
#   —— 不再【直接播放】游戏原文件。
#   设为 False 后，所有语音都必须经过【克隆】（缓存/实时合成），
#   音色仍然是游戏 Fairy 的音色（因为克隆参考音还在）。
#   想恢复“命中原声就播原文件”，把这里改回 True 即可。
PLAY_GAME_ORIGINAL = False
# 费用余额（DeepSeek 账户）
CRED_FILE = os.path.expanduser(r"~/.dsh/.credentials.yaml")
BALANCE_API = "https://api.deepseek.com/user/balance"
BALANCE_REFRESH_S = 300      # 每 5 分钟刷一次余额
# 原声语料库：命中就播【游戏原音】——这是唯一能做到"100% 和游戏一样"的路
VOICE_INDEX = fairy_root.VOICE_INDEX
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
    """读台词池。失败返回 {}（调用方会退回 GAME_LINE），且【必须打印】不许静默。"""
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
_PRE_SEQ = 0
_PRE_SEQ_LOCK = threading.Lock()


def _next_prefetch_stage():
    """给预取分配一个唯一 stage 名（如 fairy_pre_8123_7）。"""
    global _PRE_SEQ
    with _PRE_SEQ_LOCK:
        _PRE_SEQ += 1
        _n = _PRE_SEQ
    return "fairy_pre_%d_%d" % (threading.get_ident(), _n)


def _rm_quiet(path):
    """删掉临时音频文件；删不掉也不影响播放。"""
    try:
        if path:
            os.remove(path)
    except Exception:
        pass

SIZE = 128                 # 球本体显示尺寸
GAP = 8                    # 球和进度条之间的间隙
BAR_W = 208                # 右侧进度条面板宽度
WIN_W = SIZE + GAP + BAR_W
WIN_H = SIZE
MARGIN = 60                # 离屏幕边缘的安全距离
POLL_MS = 900              # 拉状态的间隔

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


def to_premultiplied_bgra(im):
    """RGBA(PIL) -> 预乘 alpha 的 BGRA uint8 数组（UpdateLayeredWindow 要的格式）。"""
    a = np.asarray(im.convert("RGBA"), dtype=np.uint16)
    al = a[:, :, 3:4]
    rgb = (a[:, :, :3] * al // 255).astype(np.uint8)
    return np.ascontiguousarray(np.dstack([rgb[:, :, 2], rgb[:, :, 1], rgb[:, :, 0],
                                           a[:, :, 3].astype(np.uint8)]))


def fmt_tokens(n):
    """427868 -> '428k'；1200000 -> '1.2M'。"""
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
    """整个虚拟桌面（含多显示器）。不用 Tk 的 winfo_screenwidth，那只给主屏。"""
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


# --------------------------------------------------------------------------
# 素材
# --------------------------------------------------------------------------
def _dim_frame(im, dim):
    """压暗一帧：直接乘 RGB（alpha 不动）。"""
    arr = np.asarray(im).astype(np.float32)
    arr[:, :, :3] *= dim
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def _placeholder_frame():
    """生成一张占位外观：透明底 + 居中浅蓝圆。

    ★ 2026-10-09 新增。为什么：球的外观素材是米哈游的美术素材，
    不能随开源仓库分发（见 NOTICE.md 2.6）。
    原代码在缺素材时会在 __init__ 里 os.listdir 抛 FileNotFoundError
    -> 球直接崩。现在改为用这张占位帧，保证“缺素材也能跑”。
    """
    im = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    m = max(2, int(SIZE * 0.07))
    d.ellipse([m, m, SIZE - m - 1, SIZE - m - 1], fill=(118, 188, 235, 255))
    return im


def load_frames(dim=0.82):
    """加载外观帧，并预生成一份“压暗”的用于待机态。

    ★ 2026-10-09 新增回退：找不到外观素材时【不再崩】，而是用占位外观。
      原因：球的外观素材是米哈游美术素材，不能随仓库分发；
      开源版使用者默认就没有素材，而这段代码位于 __init__，
      一旦抛异常整个球都起不来。
    """
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
        bright.append(im)                     # 只存 128x128 的球，画布每帧再拼（为了左右翻转）
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


# --------------------------------------------------------------------------
# 状态轮询
# --------------------------------------------------------------------------
_PUNCT = "\u2026\u3002\uff0c\uff01\uff1f\u3001\uff1b\uff1a\u201c\u201d\u2018\u2019\u300a\u300b\u3010\u3011()[]{}<>!?,.;:\"'\u3000 \t\n"


def _norm(s):
    """归一化：去掉标点和空白，只留文字，好做命中判断。"""
    return "".join(ch for ch in str(s or "") if ch not in _PUNCT).strip()


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
_PANEL_BG = "#0b1622"        # ★ 悬浮提示框的背景色（深蓝黑实底）
_PANEL_BG_RGB = (11, 22, 34)  # 同一个色的 RGB 元组（PIL 用）
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
_PANEL_BAND_Y = 5.0       # 上下两侧的渐隐带宽（px）：上下边距更紧，所以更窄
_PANEL_RGROW = 6          # 底板右缘额外外扩（px）：最多贴到球左缘-2，不吃到球身上
# ★★（第三轮：左右的背景露出一样的长短）
#   实测：底板左缘到内容 = 16px，右缘到内容只有 6px（正好等于渐隐带 -> 看着像贴边）。
#   球贴在右边，底板【不能再往右长】（会压到球），所以反过来把【内容右边收 10px】：
#   轨道 / 填充 / 底下三条硬件条一律少画 _PANEL_RINSET px -> 左右各留 16px，对称。
#   ★ 只动"条子画多长"，x0、文字坐标、百分比位置、窗口尺寸一律没动。
_PANEL_RINSET = 10        # 内容右边内收（px）：让左右露出的底色一样长
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
_PANEL_RPAD = 12           # 右对齐文字与底板右缘的间距（px）
# ★★ 2026-10-10【轨道把底板"打穿"】
#   【改前实测】轨道的 fill=(255,255,255,46) 是【直接写像素】：ImageDraw 在 RGBA 上
#     不做 source-over，它把底板在该处的 alpha 从 255【覆盖】成 46 —— 轨道区永远
#     是固定 46，和周围的渐变对不上，壁纸直接透上来（实测轨道区 alpha 恒 = 46）。
#   【怎么修】见 _panel_bar()：轨道按自己的形状开一张小图块，用真 source-over
#     叠到底板上（颜色 = 轨道 over 底板底色，alpha = 底板自己的 alpha）。
#   【填充段呢】填充是【不透明】的（alpha=255），本来就不存在"透壁纸"，
#     所以默认不并进底板层（保持逐像素等价）。置 True 可让它也跟底板一起化开，
#     代价见下面那行说明。
_PANEL_FUSE_FILL = True   # ★ 填充段（蓝/红条）是否也跟底板一起渐隐
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


def _ptext(d, xy, txt, font, fill):
    """在面板上写一个字：先描一层 1px 深色边，再写本体（位置/字号/颜色都不变）。

    【为什么要描边】底板改成"外围渐透"后，贴边的字底下会变淡（甚至完全透明），
    亮壁纸上就可能糊掉。描边只在这时候起作用：实色段上的观感几乎不变。
    【失败兜底】极端环境没有 msyh.ttc -> 字体退化成位图字体，位图字体不支持
    stroke_width。这种情况直接退回普通写法（宁可没描边，也不能让面板崩）。
    """
    try:
        d.text(xy, txt, font=font, fill=fill,
               stroke_width=1, stroke_fill=_PANEL_STROKE)
    except Exception:
        d.text(xy, txt, font=font, fill=fill)


def _panel_bar(img, plate, px, py, box, radius, fill):
    """在面板【底板之上】叠一根条（轨道 / 填充），★ 不覆盖底板自己的 alpha。

    【为什么不能直接画】ImageDraw 在 RGBA 图上是【直接写像素】的：轨道原本
        fill=(255,255,255,46) -> 把底板在该处的 alpha 由 255 覆盖成 46
        -> 轨道区变成【底板被挖空、壁纸透上来】的一条痕（实测轨道区 alpha 恒 46），
           和周围的渐变对不上 —— 这就是要修的"打穿"。
    【这里怎么做】按条的包围盒开一张小图块（自带 alpha），真正的 source-over：
        颜色 = 图块 over 底板底色(_PANEL_BG_RGB)
        alpha = 底板自己的 alpha（× 图块的覆盖率）
      再 alpha_composite 贴回画布。于是条永远和底板走同一条 alpha 曲线：
        · 实色段（底板 alpha=255）：与"不透明地直接画"逐像素一致 -> 位置尺寸不变；
        · 渐隐段：跟着底板一起化开 -> 不会在底板外面戳出一条硬边，也不挖空底板。
    【成本】每根条一张小图块（最大 193x13）+ 一次 composite，实测对 33fps 无影响。
    """
    if plate is None:
        return False
    x1, y1, x2, y2 = (int(v) for v in box)
    if x2 < x1 or y2 < y1:
        return False
    w, h = x2 - x1 + 1, y2 - y1 + 1
    tile = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle([0, 0, w - 1, h - 1], radius=radius, fill=fill)
    # 取底板在【同一位置】的 alpha（条可能压在底板边上 -> 外面补 0）
    lx, ly = x1 - int(px), y1 - int(py)
    src = plate.getchannel("A")
    under = Image.new("L", (w, h), 0)
    sx1, sy1 = max(0, lx), max(0, ly)
    sx2, sy2 = min(src.width, lx + w), min(src.height, ly + h)
    if sx2 > sx1 and sy2 > sy1:
        under.paste(src.crop((sx1, sy1, sx2, sy2)), (sx1 - lx, sy1 - ly))
    base = Image.new("RGBA", (w, h), _PANEL_BG_RGB + (255,))
    base.alpha_composite(tile)                      # 颜色 = 条 over 底板底色
    cover = tile.getchannel("A").point(lambda v: 255 if v else 0)
    base.putalpha(ImageChops.multiply(under, cover))   # alpha = 底板自己的（只限条覆盖处）
    img.alpha_composite(base, (x1, y1))
    return True


def _round_rect(cv, x1, y1, x2, y2, r, **kw):
    """在 Canvas 上画圆角矩形（用 smooth 多边形，不用第三方库）。"""
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
           x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
           x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


def retail_bubble(w, ball_cx, px):
    """★按【最终落点】重画朝上的尖角，让它真正对准球心。

    【为什么需要】球贴着屏幕边缘时，气泡为不出屏会被水平推走；
    而 make_bubble 画尖角时还不知道最终位置 → 尖角就指到了空处（实测发现）。
    宽度与 tail_x 无关，所以可以"先定尺寸、后定位置、最后重画尖角"。
    """
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


def fix_mojibake(s):
    """★ 2026-10-12【乱码自愈】把被错误解码的 UTF-8/GBK 文本还原。

    外部队件（模型输出/子进程/RSS/听歌识曲等）偶尔会把 UTF-8 字节流
    按 latin-1 / cp1252 / GBK 错误解码，导致气泡/浮窗显示乱码。
    做多轮还原：①latin-1/cp1252→utf-8/gbk ②utf-8被gbk解（锟斤拷）
    ③gbk被utf-8解（鐢樺）；还原结果若含中文则采用，否则保持原文。
    """
    try:
        if not s or not isinstance(s, str):
            return s
        if not any(ord(c) > 127 for c in s):
            return s
        _CJK = lambda t: sum(1 for c in t if "\u4e00" <= c <= "\u9fff") >= 2
        # 方案A：UTF-8 字节被 latin-1 / cp1252 解码（显示 ï¼ç»åå 这类）
        for enc in ("latin-1", "cp1252"):
            try:
                b = s.encode(enc)
            except Exception:
                continue
            for dec in ("utf-8", "gbk"):
                try:
                    t = b.decode(dec)
                except Exception:
                    continue
                if _CJK(t):
                    return t
        # 方案B：UTF-8 字节被 GBK 解码（显示 锟斤拷 这类）
        try:
            b = s.encode("gbk")
            t = b.decode("utf-8")
            if _CJK(t):
                return t
        except Exception:
            pass
        # 方案C：GBK 字节被 UTF-8 解码（显示 鐢樺 这类）
        try:
            b = s.encode("utf-8")
            t = b.decode("gbk")
            if _CJK(t):
                return t
        except Exception:
            pass
        return s
    except Exception:
        return s


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


def play_audio(path):
    """用 Windows MCI 播放（wav + mp3 都支持，不需要任何第三方库）。

    winsound 只能播 wav，而 Edge TTS 出的是 mp3 —— 所以必须用 MCI。
    `play ... wait` 是同步的：播完才返回，正好配合队列不叠音。
    """
    try:
        mci = ctypes.windll.winmm.mciSendStringW
    except Exception:
        return False
    alias = "fairyvoice"
    mci("close " + alias, None, 0, None)
    if mci('open "%s" type mpegvideo alias %s' % (path, alias), None, 0, None) != 0:
        if mci('open "%s" alias %s' % (path, alias), None, 0, None) != 0:
            return False
    # 【闪避】使用者可能正在放歌，而音乐和 Fairy 的声音都从<BT_SPEAKER>出 —— 会叠在一起。
    # 让位由 **Speaker._duck_around 统一负责**（level 感知）：
    #   只有 level in ("answer","alert") 才让位；doing(过程)/boot(开机)/done(完工) 绝不让位。
    # 本函数**不直接闪避**。历史教训：这里曾挂过第三套闪避（audio_duck.py），
    # 它**没有级别门控** —— 会导致播报"正在读取数据"也打断正在播放的音乐。
    # 那一套（连同调用它的 _ducker()）已随 audio_duck.py 一起删除。
    print("[fairy] 播放 %s | 闪避链=_duck_around(level感知) | 本函数不直接闪避"
          % os.path.basename(path), flush=True)
    try:
        mci("play %s wait" % alias, None, 0, None)
    finally:
        mci("close " + alias, None, 0, None)
    return True


def read_balance():
    """读 DSH 凭据 -> 问 DeepSeek 账户余额。失败返回 None（不编数字）。"""
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
        self.q.put((text, level))

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


class State:
    def __init__(self, on_change=None):
        self.on_change = on_change
        self.running = None      # None=未知/连不上
        self.steps = 0
        self.last_tool = ""
        self.doing = ""          # 人话版"正在做什么"
        self.mood = ""           # 情绪推断（宿主标明是推断）
        self.progress = None     # 真进度(%)，宿主给不出就是 None
        self.cpct = None         # 上下文/token 用量(%)
        self.crem = None         # 剩余 token（= limit - used）
        self.mode = ""           # 自动判断的执行方式（串行/多代理N/后台N）
        self.task_label = ""     # 我声明的任务名（用于标题行）
        self.steps = 0           # 本回合工具调用数
        self.ok = False
        # 助手回复去重：记住插件给的 seq 念到哪了。
        # None = 还没建立基线 —— 首次只记基线不补念，
        # 否则球一重启就会把上一次的旧回复念一遍。
        self.reply_seq = None
        self.lock = threading.Lock()

    def poll_loop(self, stop):
        while not stop.is_set():
            try:
                # ★【2026-10-10】每一轮现取地址：DSH 在 -> DSH，DSH 不在 -> 本地 19388
                #   （ep 内部有 5 秒缓存，不会每次都真探端口）
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
                    # 开始做什么 -> 念出来
                    if self.doing and self.doing != prev_doing:
                        self.on_change("doing", self.doing)
                    # 干活结束 -> 报一句
                    if prev_run is True and self.running is False:
                        self.on_change("done", "")
                    # 额度告警
                    if isinstance(self.cpct, (int, float)) and self.cpct >= 85:
                        self.on_change("token", "上下文快满了，要不要先整理一下")

                # ---- 助手回复（方案 A 拉模式）----
                # 让球念出 DSH 助手说的话，这样"内核是 DSH、嘴是 Fairy"才成立。
                # 用插件给的单调 seq 去重（比比较字符串可靠）；首次只建立基线不补念。
                # 【不静默】出错要打出来 —— 静默兜底会把 bug 藏起来。
                try:
                    with urllib.request.urlopen(ep.reply_url(), timeout=4) as rr:
                        rp = json.loads(rr.read().decode("utf-8"))
                    rseq = rp.get("seq") or 0
                    rtxt = (rp.get("text") or "").strip()
                    if self.reply_seq is None:
                        self.reply_seq = rseq          # 首次：只记基线
                    elif rseq > self.reply_seq:
                        self.reply_seq = rseq
                        if rtxt:
                            if rp.get("truncated"):
                                rtxt = rtxt + "……后面还有内容，要看请到界面。"
                            if self.on_change:
                                self.on_change("answer", rtxt)
                    self._reply_err = None             # 成功 -> 清掉错误记忆
                except Exception as e:
                    msg = str(e)
                    # 【为什么只打印变化】球每 900ms 轮询一次，同一个错误会刷爆日志。
                    # 【为什么还要打印】静默兜底会把 bug 藏起来。
                    # 折中：错误信息变化时才打印。
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


# --------------------------------------------------------------------------
# 实时硬件数据（内存 / 显存 / CPU）
# --------------------------------------------------------------------------
# 【为什么要一条单独的线程】显存只能走性能计数器 / WMI，第一次枚举实测 586 ms，
#   而球的 _tick 是 33fps 的绘制循环 —— 在主线程里取数会直接把动画卡住。
# 【线程模型】只有采数那条 daemon 线程写 _snap（持锁）；
#   Tk 主线程只调 snap() 拿一份【拷贝】，且拿不到锁也不等。
#   绝不从后台线程碰任何 Tk 对象（Tk 跨线程不安全）。
HW_REFRESH_S = 2.0          # 刷新间隔（秒）。按要求 1~2 秒；再快纯属白烧 CPU

try:
    import psutil as _psutil
except Exception as _e_ps:
    _psutil = None
    print("[fairy] psutil 不可用（内存/CPU 两条会显示 --）: %s: %s"
          % (type(_e_ps).__name__, _e_ps), flush=True)


def _hw_color(pct):
    """和主进度条同一套配色：正常蓝 / 偏高橙 / 快满红。"""
    if pct >= 90:
        return (226, 86, 58, 255)
    if pct >= 75:
        return (240, 176, 74, 255)
    return (74, 170, 235, 255)


class HwMonitor:
    """内存 / 显存 / CPU 的采数器（后台线程采，Tk 只读快照）。

    · 内存、CPU : psutil（微秒级，走系统 API）
    · 显存      : 优先 PDH 性能计数器
                  GPU Adapter Memory(<luid>)\\Dedicated Usage（实测 <1ms）；
                  失败退 WMI
                  Win32_PerfFormattedData_GPUPerformanceCounters_GPUAdapterMemory（实测 ~5ms）；
                  两条都拿不到就是 None，界面显示 --（★ 绝不编数字）。
      ★ 口径：这是【整张卡】的已用显存（所有进程合计），不是 Fairy 模型自己的占用。
      ★ 分母（总显存 15.92 GB）从注册表 HardwareInformation.qwMemorySize 读一次。
      ★ AMD 卡在 Windows 上没有 nvml / rocm-smi 可用，上面两条是实测唯一可行的路。
    """

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
        # ★ 2026-10-10psutil 的 cpu_percent 基线不是"全局一份"，
        #   而是存在 _last_cpu_times 里、【按调用线程的 ident 分键】（psutil 7.2.2 实测）。
        #   所以原来在 __init__（主线程）里空打那一发【对采样线程完全无效】——
        #   采样线程的第一发照样是 0.0。基线必须由【采样线程自己】打（见 _loop）。
        self._cpu_primed = False

    # ---------------- 显存总量（注册表，读一次就够）----------------
    @staticmethod
    def _read_vram_total():
        """读真实显存总量（字节）。

        ★ WMI 的 Win32_VideoController.AdapterRAM 实测给的是 4,293,918,720
          （4 GB 假值，32 位字段溢出），不能用；注册表的 qwMemorySize 才是真值。
        """
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

    # ---------------- 显存：PDH 性能计数器（首选）----------------
    def _pdh_open(self):
        """枚举 GPU Adapter Memory 的实例，每个实例挂一个 Dedicated Usage。

        ★ 实测坑：win32pdh.AddEnglishCounter 在中文系统上能用（吃英文计数器名），
          但【通配实例 (*) 读不出来，必须写具体实例名】——通配只对
          PdhGetFormattedCounterArray 有效，对本接口恒返回 0。
        """
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
        win32pdh.CollectQueryData(q)            # 第一发只做基线
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
            # 实例集会随显卡热插拔变，所以 10 分钟重建一次查询
            # （重建实测 ~0.6s，在后台线程里无所谓，不能放主线程）
            if self._pdh is None or (now - self._pdh_at) > 600.0:
                self._pdh_close()
                self._pdh = self._pdh_open()
                self._pdh_at = now
                if self._pdh is not None:
                    time.sleep(0.05)            # 让基线生效，否则第一发可能读到 0
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
            if not got:                          # 句柄全废了 -> 丢掉重建
                self._pdh_close()
                return None
            return tot
        except Exception:
            self._pdh_close()
            return None

    # ---------------- 显存：WMI 兜底 ----------------
    def _vram_wmi(self):
        """兜底：WMI（实测 5 ms）。★ CoInitialize 必须在本线程里调。"""
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

    # ---------------- 一轮采样 ----------------
    def _refresh(self):
        mem_pct = mem_used = mem_total = cpu = None
        if _psutil is not None:
            try:
                vm = _psutil.virtual_memory()
                mem_pct, mem_total = float(vm.percent), int(vm.total)
                # used 用 total - available：这才是任务管理器"使用中"的口径
                # （available 含待机缓存；free 不含，拿 free 算会虚高）
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
        """给 Tk 主线程读的一份拷贝。

        ★ 不阻塞：主线程是 33fps 的绘制循环，宁可给上一次的数据也不能等锁。
        （dict 赋值在 CPython 里是原子的，读到的永远是完整的一份快照。）
        """
        got = self.lock.acquire(False)
        try:
            return dict(self._snap)
        finally:
            if got:
                self.lock.release()

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="fairy-hw").start()

    def _loop(self):
        # ★★ 2026-10-10基线必须在【采样线程自己】这里打第一发：
        #   psutil 的 _last_cpu_times 是【按线程 ident 分键】的（7.2.2 实测），
        #   主线程 __init__ 里空打的那发对本线程无效 -> 采样线程第一发照样 0.0。
        if _psutil is not None:
            try:
                _psutil.cpu_percent(interval=None)     # 只做基线，丢弃这个 0.0
                self._cpu_primed = True
            except Exception:
                pass
        while not self._stop.is_set():
            # ★ #7 再补一刀：打完基线【先等一个采样间隔】再取第一发。
            #   不等的话分母只有几百微秒，第一发仍然是 0.0%（实测过）。
            #   于是面板最初 ~2 秒显示 "--"（None），随后才是真数字 —— 绝不假显 0%。
            self._stop.wait(HW_REFRESH_S)
            if self._stop.is_set():
                break
            try:
                self._refresh()
            except Exception as e:
                print("[fairy] 硬件数据采集失败: %s: %s" % (type(e).__name__, e), flush=True)


# --------------------------------------------------------------------------
# 悬浮球
# --------------------------------------------------------------------------
_PIL_CACHE = {}

def _pil_round(text, min_w, h, r, bg, fg, bold=True, outline="#d8dfe8"):
    """模块级圆角按钮图生成（PIL，粗体 11px），返回 (PhotoImage, 宽)。"""
    _k = (text, min_w, h, r, bg, fg, bold)
    if _k in _PIL_CACHE:
        return _PIL_CACHE[_k]
    from PIL import Image, ImageDraw, ImageFont, ImageTk
    _probe = Image.new("RGBA", (min_w, h), (0, 0, 0, 0))
    _pd = ImageDraw.Draw(_probe)
    try:
        _f = ImageFont.truetype("SourceHanSansCN-Bold.otf" if bold
                                else "SourceHanSansCN-Regular.otf", 14)
    except Exception:
        try:
            _f = ImageFont.truetype("simhei.ttf", 14)
        except Exception:
            try:
                _f = ImageFont.truetype("msyhbd.ttc", 14)
            except Exception:
                _f = ImageFont.load_default()
    _tw = _pd.textlength(text, font=_f)
    _w = max(min_w, int(_tw) + 42)
    _img = Image.new("RGBA", (_w, h), (0, 0, 0, 0))
    _d = ImageDraw.Draw(_img)
    _d.rounded_rectangle([0, 0, _w - 1, h - 1], radius=r, fill=bg,
                         outline=outline, width=1)
    _d.text(((_w - _tw) / 2, (h - 15) / 2), text, font=_f, fill=fg)
    _ti = ImageTk.PhotoImage(_img)
    _PIL_CACHE[_k] = (_ti, _w)
    return _PIL_CACHE[_k]

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
        self._songrec_at = 0.0
        self._songrec_last = ""
        threading.Thread(target=self._songrec_loop, daemon=True).start()
        # ★ 2026-10-11【快捷功能】免打扰 / 定时提醒 / 剪贴板历史
        self._dnd = False
        self._reminders = []
        threading.Thread(target=self._clip_watch, daemon=True).start()
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

    def _flash_bar(self, text):
        """★ 2026-10-12【播报提示条】主线程短暂显示提示条 3 秒，
        播报时也能看到进度条面板（不只在任务运行时显示）。"""
        try:
            def _on():
                self._local_task = text
                self.after(3000, lambda: setattr(self, "_local_task", None))
            self.after(0, _on)
        except Exception:
            pass

    def _on_state(self, kind, text):
        """状态回调。

        **开始干活时只说"正在执行任务"**，不逐条念具体动作
        （具体动作仍在进度条旁边以文字显示）。
        另外两条保留：干完报"任务完成"、额度告警。
        去重逻辑在 Speaker 里，所以同一个任务里这句只会念一次。
        """
        try:
            if kind == "doing":
                # 从"待机"变成"开工"时，弹一次气泡（过程播报不弹，否则太吵）
                if not getattr(self, "_was_running", False):
                    # ★ 2026-10-10这里原来是 self.after(0, ...) ——
                    #   本函数跑在 State.poll_loop 的【后台线程】里，
                    #   而 Tk 的 after/_bubble 只能在主线程碰：
                    #   主线程一忙就抛 "main thread is not in main loop"（今日日志 15 次），
                    #   异常把整个 doing 分支吞掉（含下面的 _was_running = True）
                    #   -> 开工语音时有时无。
                    #   改成和 work_advisor 同一范式：只【入队】，主线程 _tick 里再弹。
                    self._adv_bubbles.append("了解。开始同步。")
                self._was_running = True
                # 【关键】按"刚才那个工具"选一句**语义对得上的游戏原声**，
                # 这样动态播报也是 100% 游戏声（播的是游戏原音文件）。
                # 具体文字仍然显示在进度条旁边。
                tool = ""
                try:
                    with self.state.lock:
                        tool = self.state.last_tool
                except Exception:
                    pass
                # ★ 从"池"里随机抽一句（不再是固定映射那一句）
                self.speaker.say(self.speaker._pick_doing_line(tool))
            elif kind == "done":
                self.speaker.say(SPEAK_DONE)
                self._was_running = False
                # ★ 同 #2：入队 -> 主线程弹（原来 self.after(0, ...) 是 Tk 跨线程调用）
                self._adv_bubbles.append("主人，我已完成既定目标。")
            elif kind == "answer":
                # ★ 助手回复 —— "Fairy 就是 DSH 的嘴"的关键一条。
                # level="answer"：_duck_around 只对 answer/alert 让位，
                # 因为这是"主动提问、在等答案"的场景，音乐应该让位。
                # 过程播报(doing) 绝不用这个 level，否则干活时音量被反复压低。
                # ★ 2026-10-12：播报时也短暂显示提示条（否则日常对话看不到提示条）
                try:
                    self._flash_bar((text or "")[:28])
                except Exception:
                    pass
                self.speaker.say(text, level="answer")
            elif kind == "token":
                self.speaker.say(text)
        except Exception as e:
            # 【不静默】原来这里是 except: pass，会把 bug 藏起来。
            # 今天正是因为静默兜底，一个 bug 被藏了三轮才发现。出错必须打出来。
            print("[fairy] _on_state(%s) 处理失败: %s" % (kind, e), flush=True)
            import traceback
            traceback.print_exc()

    # ---- 位置 ----
    def _load_pos(self):
        # 存档是"球在屏幕上的 x"；启动时 side 默认 right（球在窗口左端），
        # 所以窗口 x 就等于球的 x —— 之后选边逻辑会挪窗口而球不动。
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
        """设置并**确认**位置（写进 briefing.json）。city='' = 重新自动定位。"""
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
        """★所有弹窗的统一位置：球+进度条面板的【正下方】。

        【布局】三种弹窗统一到球+进度条的正下方
        规则：① x 与面板左边缘对齐（受屏幕边界收拢）
              ② y = 面板下沿 + 6；下方放不下就【翻到上方】；上方也放不下就贴顶
              ③ 返回 (x, y, flip) —— flip=True 表示翻到上方了（尖角要反过来）
        """
        vx, vy, vw, vh = virtual_screen()
        px = self.winfo_x()
        py = self.winfo_y() + WIN_H + 6
        flip = False
        if py + wh > vy + vh - 8:          # 下方放不下 -> 翻到面板上方
            py = self.winfo_y() - wh - 6
            flip = True
        if py < vy + 4:                     # 上方也放不下 -> 贴顶
            py = vy + 4
            flip = False
        px = max(vx + 16, min(px, vx + vw - ww - 16))   # ★ 2026-10-12：左右收拢 4->16，提示不再贴边
        return px, py, flip

    def _tail_x(self):
        """尖角在气泡内的水平位置 —— 对准球心（初始值，落点定了还会重画）。"""
        off = 0 if self.side == "right" else BAR_W + GAP
        return off + SIZE // 2

    def _ball_center_x(self):
        """球的【屏幕】中心 x —— 重画尖角时要对准它。"""
        off = 0 if self.side == "right" else BAR_W + GAP
        return self.winfo_x() + off + SIZE // 2

    def _ball_progress(self, pct, label, on=True):
        """★ 2026-10-12【球体进度条】临时进度画在球右侧进度条面板（不弹气泡）。
        on=True 设置 (pct, label)；on=False 清空，回到宿主实时进度。"""
        try:
            if on:
                self._temp_prog = (int(pct), str(label))
            else:
                self._temp_prog = None
        except Exception:
            pass

    def _preset_progress(self, title, port):
        """★ 2026-10-12【组合切换进度 v2】进度画在球右侧进度条面板，
        就绪后播报「组合 XX 已就绪」。port 为字符串端口。"""
        try:
            self._ball_progress(5, "Fairy 组合切换 · %s" % title, True)

            def _poll():
                import urllib.request as _ur
                _t0 = time.time()
                _last = -1
                while time.time() - _t0 < 90:
                    _up = False
                    try:
                        with _ur.urlopen("http://127.0.0.1:%s/v1/models"
                                         % port, timeout=1.2) as _r:
                            _j = json.loads(_r.read().decode("utf-8", "ignore"))
                            _up = bool(_j.get("data") or _j.get("models"))
                    except Exception:
                        try:
                            with _ur.urlopen("http://127.0.0.1:%s/api/tags"
                                             % port, timeout=1.2) as _r2:
                                _j2 = json.loads(_r2.read().decode("utf-8", "ignore"))
                                _up = bool(_j2.get("models"))
                        except Exception:
                            _up = False
                    _p = 40 if not _up else 95
                    if _p != _last:
                        _last = _p
                        self._ball_progress(_p, "Fairy 组合切换 · %s" % title, True)
                    if _up:
                        self._ball_progress(100, "Fairy 组合切换 · %s" % title, True)
                        time.sleep(0.8)
                        self._ball_progress(0, "", False)
                        try:
                            self._bubble("组合「%s」已就绪" % title, 4)
                            self.say("组合%s已就绪" % title, "ok")
                        except Exception:
                            pass
                        return
                    time.sleep(0.6)
                self._ball_progress(0, "", False)
                try:
                    self._bubble("组合「%s」启动超时，请检查引擎" % title, 5)
                    self.say("组合%s启动超时" % title, "warn")
                except Exception:
                    pass
            threading.Thread(target=_poll, daemon=True).start()
        except Exception:
            pass

    def _stack_relayout(self):
        """★ 2026-10-12【气泡栈】多个气泡/浮窗垂直排列，绝不叠加：
        从球正下方逐一向下排（间隔 8px）；放不下翻到球上方从下往上排；
        任一气泡超时消失后自动补位重排。"""
        try:
            _stack = []
            for _w in getattr(self, "_bub_stack", []):
                try:
                    if _w.winfo_exists():
                        _stack.append(_w)
                    else:
                        try:
                            _w.destroy()
                        except Exception:
                            pass
                except Exception:
                    pass
            self._bub_stack = _stack
            if not _stack:
                return
            vx, vy, vw, vh = virtual_screen()
            _heights = [max(1, _w.winfo_height()) for _w in _stack]
            _total = sum(h + 8 for h in _heights)
            _base_y = self.winfo_y() + WIN_H + 6
            _flip_up = False
            if _base_y + _total > vy + vh - 8:
                _flip_up = True
                _base_y = self.winfo_y() - 6
            _y = _base_y - _total if _flip_up else _base_y
            for _w, _h in zip(_stack, _heights):
                _ww2 = max(1, _w.winfo_width())
                _px = self._ball_center_x() - _ww2 // 2
                _px = max(vx + 4, min(_px, vx + vw - _ww2 - 4))
                _w.geometry("+%d+%d" % (_px, _y))
                _y += _h + 8
        except Exception:
            pass

    def _bubble(self, text, seconds=4):
        """对话气泡：只在 Fairy【主动说关键话】时弹一句，几秒后自动消失。

        为什么不常驻：
          · 球只有 128px，常驻气泡会把它从"发光小精灵"变成"聊天窗口"，还挡桌面
          · 正常对话的内容本来就在 DSH 界面里，气泡是重复信息
        为什么还是要：
          · Fairy 主动说话（开工/完工/告警）时你多半没看屏幕，纯语音会漏
          · 补一句短气泡和语音同步，就不会漏事
        ★ 2026-10-12：多个气泡垂直排列（气泡栈），不叠加；各自超时消失并补位。
        """
        try:
            if not hasattr(self, "_bub_stack"):
                self._bub_stack = []
            text = fix_mojibake(text)   # ★ 2026-10-12：乱码自愈，显示前还原
            w, ww, wh = make_bubble(self, text, tail="top", tail_x=self._tail_x())
            px, py, _flip = self._popup_pos(ww, wh)
            retail_bubble(w, self._ball_center_x(), px)   # ★按落点重画尖角，对准球
            w.geometry("+%d+%d" % (px, py))
            self._bub_stack.append(w)

            def _gone():
                try:
                    w.destroy()
                except Exception:
                    pass
                try:
                    if w in self._bub_stack:
                        self._bub_stack.remove(w)
                except Exception:
                    pass
                try:
                    self._stack_relayout()
                except Exception:
                    pass
            w.after(int(seconds * 1000), _gone)
            try:
                self._stack_relayout()
            except Exception:
                pass
        except Exception:
            pass

    def _show_text(self, lines, seconds=18):
        """屏幕通道：弹一个浮窗显示播报全文（不抢焦点，到点自动关）。

        —— 需求：Fairy 可以是声音设备、显示设备等等，
        所以同一段信息同时走【声音】和【屏幕】两条路。
        """
        try:
            w = tk.Toplevel(self)
            w.overrideredirect(True)
            w.attributes("-topmost", True)
            w.configure(bg="#0a1420")
            lines = [fix_mojibake(_l) for _l in lines]   # ★ 2026-10-12：乱码自愈
            tk.Label(w, text="\n".join(lines), justify="left", fg="#cfe3f5", bg="#0a1420",
                     font=("SimHei", 12), padx=16, pady=12).pack()
            w.update_idletasks()
            vx, vy, vw, vh = virtual_screen()
            ww, wh = w.winfo_width(), w.winfo_height()
            # 【布局】贴在【球 + 进度条】那个小窗的【正下方】，不要浮在屏幕中间。
            # 贴屏幕下沿时自动翻到面板上方；左右做边界收拢。
            px, py, _flip = self._popup_pos(ww, wh)   # ★与气泡/悬停面板同一套定位
            w.geometry("+%d+%d" % (px, py))
            # ★ 2026-10-12：播报浮窗也进气泡栈，与短气泡垂直排列不叠加
            if not hasattr(self, "_bub_stack"):
                self._bub_stack = []
            self._bub_stack.append(w)

            def _gone():
                try:
                    w.destroy()
                except Exception:
                    pass
                try:
                    if w in self._bub_stack:
                        self._bub_stack.remove(w)
                except Exception:
                    pass
                try:
                    self._stack_relayout()
                except Exception:
                    pass
            w.after(seconds * 1000, _gone)
            try:
                self._stack_relayout()
            except Exception:
                pass
        except Exception as _e:
            # 顺手去掉静默兜底（今天已多次因它藏住 bug）
            print("[fairy] 播报浮窗失败: %s: %s" % (type(_e).__name__, _e), flush=True)

    def _maybe_brief(self):
        """开机简报：等 DSH 通了 + 过了等待时间，就播一次。"""
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
            # 通道1：声音
            self.speaker.say_all(lines)
            # 通道2：屏幕
            self.after(0, lambda L=list(lines): self._show_text(L))
            # 通道3：文件（可回看）
            try:
                with open(fairy_root.log("briefing_last.txt"), "w", encoding="utf-8") as f:
                    f.write(time.strftime("%Y-%m-%d %H:%M:%S\n"))
                    f.write("\n".join(lines))
            except Exception:
                pass
            # 记住当前定位（用于菜单显示）
            try:
                self.city, _nc = m.resolves_city()
            except Exception:
                pass
            # ★【总时长实测】开一条轻量看门线程：队列排空且最后一条播完 -> 记一笔总时长。
            #   一条总数比七条各自的时间戳更直观；也让下次调优有基线。
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
        """球在**屏幕上**的 x（窗口 x + 球在窗口里的偏移）。位置存档存这个。"""
        return self.winfo_x() + (0 if self.side == "right" else BAR_W + GAP)

    def _save_pos(self):
        # 【审计 P1】原来不带 with、非原子、异常静默 —— 断电/崩溃会写坏存档（球跳回默认位置）。
        # 改成 temp + os.replace（原子），失败要出声。
        try:
            tmp = POS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"x": self._ball_screen_x(), "y": self.winfo_y()}, f)
            os.replace(tmp, POS_FILE)
        except Exception as _e:
            print("[fairy] 位置存档写入失败: %s: %s" % (type(_e).__name__, _e), flush=True)

    # ---- 拖动（增量式，容器/多屏都稳） ----
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
        else:
            # ★ 2026-10-11【左键单击】语音询问使用者需要什么帮助
            self._ask_help()
        self._drag = None

    def _ask_help(self):
        """★ 2026-10-11【左键单击】语音询问 + 气泡提示。"""
        try:
            self.say("主人，有什么需要我帮忙的吗？", "ok")
            self._bubble("需要帮忙吗？可以说：打开软件 / 听歌识曲 / 提醒我…", 6)
        except Exception:
            pass

    def _on_hover_in(self, e=None):
        """★ 2026-10-12【悬停提示卡已删除】按用户要求不再弹出左侧状态卡。
        保留方法签名以兼容 <Enter> 绑定；需要看状态请左键球或打开控制面板。"""
        return

    def _on_hover_out(self, e=None):
        print('[fairy] 鼠标离开球', flush=True)
        w = getattr(self, "_hover", None)
        if w is not None:
            try:
                w.destroy()
            except Exception:
                pass
            self._hover = None

    # ================= 本地运算（Strata）控制 =================
    def _strata_probe(self, force=False):
        """探测本地运算状态。★ 绝不抛异常、绝不久阻塞（菜单弹出前要调）。

        返回 {"t","state","loaded","ram_used","ram_total","text"}
          state: "running"（在跑）| "unloaded"（进程在、模型已卸，内存已回收）
                 | "stopped"（进程没了）| "unknown"（探测失败）
        ★ 15 秒缓存：绝大多数菜单弹出是零延迟的。
        """
        import socket as _sock
        now = time.time()
        c = getattr(self, "_strata_cache", None)
        if not force and c and now - c.get("t", 0) < 15:
            return c
        st = {"t": now, "state": "unknown", "loaded": None,
              "ram_used": None, "ram_total": None, "text": "状态未知"}
        try:                                              # 1) 端口（本机，秒回）
            _s = _sock.create_connection(("127.0.0.1", STRATA_PORT), 0.8)
            _s.close()
        except OSError:
            st.update(state="stopped", text="已停止")
            self._strata_cache = st
            return st
        try:                                              # 2) HTTP 状态（短超时）
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
        """后台每 20 秒刷新一次状态 —— ★ 这样右键菜单弹出时【零延迟】。"""
        while True:
            try:
                self._strata_probe(force=True)
            except Exception:
                pass
            time.sleep(20)

    def _strata_feedback(self, text, speak=None):
        """★ 线程安全反馈：只入队，由主线程 _tick 弹气泡 / 播报（Tk 跨线程不安全）。"""
        try:
            self._adv_bubbles.append(text)
            if speak:
                self._strata_says.append(speak)
        except Exception:
            pass
        print("[fairy] " + text, flush=True)

    def _strata_post(self, url, timeout=30):
        """POST 一个空 JSON（服务端 _own_page 要求 application/json + 无外来 Origin）。"""
        body = json.dumps({}).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "{}")

    def _strata_stop(self):
        """右键：停止本地运算 —— 回收内存（可逆，进程还在，下次用到会自动加载）。"""
        if getattr(self, "_strata_busy", False):
            self._bubble("上一个操作还在进行…", 3)
            return
        self._strata_busy = True
        self._strata_feedback("正在停止本地运算…")
        threading.Thread(target=self._strata_stop_work, daemon=True).start()

    def _strata_stop_work(self):
        """★ 后台线程：POST /v1/unload，并实测回收了多少内存。"""
        try:
            # ★ 2026-10-14【静默优化】Strata(8080) 没跑时直接判"本来就没在跑"，
            #   避免 POST /v1/unload 抛 10061 -> 「停止失败」噪音（health_check 会误报）。
            import socket as _sock
            _s = _sock.socket()
            _s.settimeout(0.5)
            _up = (_s.connect_ex(("127.0.0.1", 8080)) == 0)
            _s.close()
            if not _up:
                self._strata_feedback("本地运算本来就没在跑", speak="本地运算本来就没在跑")
                return
            try:
                _before = _free_ram_gb()
            except Exception:
                _before = None
            d = self._strata_post(STRATA_UNLOAD_URL)
            status = d.get("status")
            time.sleep(4)                       # 等引擎真正退出、内存归还
            self._strata_cache = None           # 强制下次重探
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
        """右键：启动本地运算 —— 两级：进程在就 POST /v1/load，进程没了就按真实命令行拉起。"""
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
            self._strata_feedback("启动本地运算（加载模型，约 1~2 分钟）…")
        else:
            self._strata_feedback("正在拉起本地运算服务…")
        threading.Thread(target=self._strata_start_work, args=(st.get("state"),), daemon=True).start()

    def _strata_start_work(self, state):
        try:
            if state == "unloaded":
                # 进程还在 -> 让服务把引擎重新拉起来
                self._strata_feedback("已通知服务加载模型，请稍候…")
                try:
                    self._strata_post(STRATA_LOAD_URL, timeout=180)
                except Exception as _e:
                    self._strata_feedback("加载调用失败：" + str(_e)[:50])
            else:
                # 进程没了 -> 按当前机器配置的命令行静默拉起
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
            # 轮询等就绪（最多约 3 分钟）
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

    def _dialog(self, title, w=440, h=320, bg="#f2f3f5"):
        """★ 2026-10-12【统一子窗口】所有弹窗同主界面风格：浅灰底 + 圆角 + 黑体。
        返回 (win, wrap)，wrap 是内容容器（淡色底，往里面摆控件）。"""
        win = tk.Toplevel(self)
        win.title(title)
        win.attributes("-topmost", True)
        win.configure(bg=bg)
        win.resizable(False, False)
        win.geometry("%dx%d+%d+%d" % (w, h,
                                      max(0, (win.winfo_screenwidth() - w) // 2),
                                      max(0, (win.winfo_screenheight() - h) // 2)))
        try:
            import ctypes
            _u = ctypes.windll.user32
            _g = ctypes.windll.gdi32
            _rgn = _g.CreateRoundRectRgn(0, 0, w + 1, h + 1, 18, 18)
            if _rgn:
                _u.SetWindowRgn(win.winfo_id(), _rgn, True)
        except Exception:
            pass
        tk.Label(win, text=title, bg=bg, fg="#52749b",
                 font=("SimHei", 15, "bold")).pack(anchor="w", padx=24, pady=(14, 2))
        _wrap = tk.Frame(win, bg=bg)
        _wrap.pack(fill="both", expand=True, padx=24, pady=(4, 18))
        return win, _wrap

    def _dlg_btn(self, parent, text, cmd, danger=False, w=100):
        """统一弹窗按钮：圆角白底，悬停变色。"""
        _fg = "#a8625e" if danger else "#46536a"
        _img = _pil_round(text, w, 40, 12, "#ffffff", _fg, True, "#dfe3ea")[0]
        _img_h = _pil_round(text, w, 40, 12, "#e4edf6", _fg, True, "#cdd9e6")[0]
        _b = tk.Button(parent, image=_img, bd=0, relief="flat",
                       highlightthickness=0, bg="#f2f3f5",
                       activebackground="#f2f3f5", cursor="hand2",
                       command=cmd)
        _b.image = _img
        _b.bind("<Enter>", lambda _ev, b=_b, n=_img, h=_img_h: b.configure(image=h))
        _b.bind("<Leave>", lambda _ev, b=_b, n=_img, h=_img_h: b.configure(image=n))
        _b.pack(side="left", padx=5, pady=4)
        return _b

    def _ai_cfg(self):
        """读取 fairy.json 的 ai 段（后端 / api_key / 模型 / 接口地址）。"""
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
        """把 patch 合入 fairy.json 的 ai 段并保存（保留其它配置键）。"""
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
            # 让 fairy_root.CFG 里也能读到新值（本进程内立即生效）
            try:
                fairy_root.CFG["ai"] = ai
            except Exception:
                pass
            return True
        except Exception as e:
            self._bubble("保存 AI 配置失败：" + str(e)[:60], 4)
            return False

    def _ai_probe_models(self):
        """探测可用模型：Strata /v1/models + Ollama /api/tags + LLM·llama.cpp。返回 (标题, 列表)。"""
        found = []
        # Strata（本地运算，8080）
        try:
            req = urllib.request.Request(STRATA_STATUS_URL.replace("/v1/status", "/v1/models"),
                                         headers={"content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=6) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            for m in (d.get("data") or []):
                found.append(("Strata", m.get("id") or m.get("name") or str(m)[:40]))
        except Exception:
            pass
        # Ollama（11434）
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=6) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            for m in (d.get("models") or []):
                found.append(("Ollama", m.get("name") or m.get("model") or ""))
        except Exception:
            pass
        # LLM·llama.cpp（8085 优先，8080 备用）
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
        """弹窗设置 API Key（不回显明文，只显示已配置与否）。"""
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

    def _doubao_cfg(self):
        """读 fairy.json online.doubao 段。"""
        try:
            p = getattr(fairy_root, "CONFIG", None)
            c = {}
            if p and os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    c = json.load(f)
            return dict(((c.get("online") or {}).get("doubao") or {}))
        except Exception:
            return {}

    def _save_doubao_cfg(self, patch):
        """合入 fairy.json online.doubao 段（保留其它配置键）。"""
        try:
            path = fairy_root.CONFIG
            c = {}
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        c = json.load(f)
                except Exception:
                    c = {}
            onl = dict(c.get("online") or {})
            dou = dict(onl.get("doubao") or {})
            dou.update(patch)
            onl["doubao"] = dou
            c["online"] = onl
            with open(path, "w", encoding="utf-8") as f:
                json.dump(c, f, ensure_ascii=False, indent=2)
            try:
                fairy_root.CFG["online"] = onl
            except Exception:
                pass
            return True
        except Exception as e:
            self._bubble("保存豆包配置失败：" + str(e)[:60], 4)
            return False

    def _set_doubao_key(self):
        """★ 2026-10-12【豆包（火山方舟）】填 API Key 启用在线豆包大脑。
        本地引擎不可用时，ear 自动降级到豆包回答（无需 DSH）。"""
        import tkinter.simpledialog as _sd
        _cur = (self._doubao_cfg().get("api_key") or "").strip()
        _masked = ("（已配置 %d 位）" % len(_cur)) if _cur else "（未配置）"
        _key = _sd.askstring("豆包 - 火山方舟 API Key",
                             "粘贴你的豆包 API Key（当前%s）：\n"
                             "获取：火山方舟控制台 → API Key 管理" % _masked,
                             parent=self)
        if _key is None:
            return
        _key = _key.strip()
        self._save_doubao_cfg({"enabled": bool(_key), "api_key": _key})
        self._bubble(("豆包已启用（%d 位 Key）" % len(_key)) if _key else "豆包已停用", 3)

    def _set_doubao_model(self):
        """弹窗设置豆包模型名（默认 doubao-seed-1-6-250615）。"""
        import tkinter.simpledialog as _sd
        _cur = self._doubao_cfg().get("model") or "doubao-seed-1-6-250615"
        _val = _sd.askstring("豆包 - 模型", "模型名（当前：%s）：\n"
                             "例：doubao-seed-1-6-250615 / doubao-pro-32k" % _cur,
                             parent=self, initialvalue=_cur)
        if _val is None:
            return
        _val = _val.strip()
        if _val:
            self._save_doubao_cfg({"model": _val})
            self._bubble("豆包模型已设为：" + _val, 3)


    def _ai_set_model(self):
        """★ 2026-10-12【本地模型选择】统一风格弹窗 + 下拉选择（探测到的模型）。"""
        win, wrap = self._dialog("本地模型选择", 440, 300)
        tk.Label(wrap, text="从下拉选择本机探测到的模型：", bg="#f2f3f5",
                 fg="#6e7888", font=("SimHei", 13)).pack(anchor="w", pady=(6, 4))
        import tkinter.ttk as _ttk
        _cur = self._ai_cfg().get("model") or ""
        _opts = [_cur] if _cur else []
        _src = {}
        try:
            for _s, _n in self._ai_probe_models():
                if _n not in _src:
                    _src[_n] = _s
                    _opts.append(_n)
        except Exception:
            pass
        _var = tk.StringVar(value=_cur or (_opts[0] if _opts else ""))
        _cb = _ttk.Combobox(wrap, textvariable=_var, values=_opts or ["（未探测到模型）"],
                            font=("SimHei", 13), state="readonly", width=28)
        _cb.pack(anchor="w", pady=(2, 8))
        tk.Label(wrap, text="也可以手动输入模型名（不选下拉就自己填）：", bg="#f2f3f5",
                 fg="#6e7888", font=("SimHei", 13)).pack(anchor="w")
        _man = tk.Entry(wrap, font=("SimHei", 13), width=30)
        _man.pack(anchor="w", pady=(2, 10))

        def _ok():
            _v = (_var.get() or "").strip()
            _m = (_man.get() or "").strip()
            _fin = _m or _v or ""
            self._save_ai_cfg({"model": _fin})
            self._bubble("本地模型已设为：" + (_fin or "（空，用引擎默认）"), 3)
            win.destroy()
        _row = tk.Frame(wrap, bg="#f2f3f5")
        _row.pack(anchor="w", pady=(6, 0))
        self._dlg_btn(_row, "保存", _ok)
        self._dlg_btn(_row, "取消", win.destroy)
        win.focus_force()

    def _ai_set_url(self):
        """弹窗设置自定义接口地址（OpenAI 兼容 base_url）。"""
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
                self._bubble("没探测到模型（Strata / Ollama / LLM 都没在跑）", 4)
                return
            lines = ["可用模型："]
            for src, name in found[:12]:
                lines.append("· %s: %s" % (src, name))
            self._bubble("\n".join(lines), 6)
        threading.Thread(target=_work, daemon=True).start()

    def _pref(self):
        """读取 fairy.json 的 endpoints.prefer（local / dsh / auto）。
        ★ 2026-10-11【DSH 解耦】默认 local（单机模式，不连 DSH）。"""
        try:
            p = getattr(fairy_root, "CONFIG", None)
            c = {}
            if p and os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    c = json.load(f)
            return ((c.get("endpoints") or {}).get("prefer") or "local")
        except Exception:
            return "local"

    def _set_pref(self, k):
        """切换大脑模式：auto（DSH 优先）/ dsh（强制 DSH）/ local（只用本地）。"""
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
            _nm = {"auto": "自动（本地优先）", "dsh": "DSH 云端",
                   "local": "本地内核（单机模式）"}.get(k, k)
            self._bubble("大脑模式已切换：" + _nm, 3)
            # ★ 2026-10-12【DSH 可启动】切到 dsh 模式时，若 DSH 没在跑，自动拉起
            if k == "dsh":
                self._ensure_dsh()
        except Exception as e:
            self._bubble("切换大脑模式失败：" + str(e)[:60], 4)

    def _ensure_dsh(self):
        """★ 2026-10-12【DSH 可启动】DeepSeek Harness 没在跑就自动拉起。

        探测 19387（dsh_base）；不通则后台启动 DeepSeek Harness.exe，
        最多等 90 秒，就绪后气泡+语音播报。绝不弹黑窗。
        """
        def _job():
            import socket as _sk
            import subprocess as _sp
            try:
                try:
                    _s = _sk.create_connection(("127.0.0.1", 19387), timeout=1.5)
                    _s.close()
                    self._bubble("DSH 云端已在运行", 3)
                    return
                except Exception:
                    pass
                _exe = (r"C:\Users\Administrator\AppData\Local\Programs"
                        r"\DeepSeek Harness\DeepSeek Harness.exe")
                if not os.path.isfile(_exe):
                    self._bubble("找不到 DeepSeek Harness，请手动启动", 4)
                    return
                self._bubble("正在启动 DSH 云端…", 4)
                try:
                    self.say("正在启动 DSH 云端", "ok")
                except Exception:
                    pass
                # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP：完全脱离，不弹窗
                _sp.Popen([_exe], cwd=os.path.dirname(_exe), close_fds=True,
                          creationflags=0x00000008 | 0x00000200)
                for _i in range(90):
                    time.sleep(1)
                    try:
                        _s = _sk.create_connection(("127.0.0.1", 19387), timeout=1.0)
                        _s.close()
                        self._bubble("DSH 云端已就绪", 3)
                        try:
                            self.say("DSH 云端已就绪", "ok")
                        except Exception:
                            pass
                        return
                    except Exception:
                        pass
                self._bubble("DSH 云端 90 秒内未就绪，请稍后再试", 5)
            except Exception as _e:
                try:
                    self._bubble("启动 DSH 失败：" + str(_e)[:50], 4)
                except Exception:
                    pass
        try:
            threading.Thread(target=_job, daemon=True).start()
        except Exception:
            pass


    def _probe_backend(self):
        """引擎自动选择：按 Strata → Ollama → llama.cpp → 自定义 顺序探测，返回第一个活的。"""
        import socket

        def _alive(port, timeout=1.0):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=timeout).close()
                return True
            except Exception:
                return False

        if _alive(8080):
            return "strata"
        if _alive(11434):
            return "ollama"
        if _alive(8085):
            return "llm"
        if (self._ai_cfg().get("base_url") or "").strip():
            return "custom"
        return "strata"  # 全都没跑时的兜底



    def _ai_set_net_model(self):
        """★ 2026-10-11【网络选项卡】设置网络模型名（DSH / 自定义接口用的模型）。"""
        import tkinter.simpledialog as _sd
        cur = self._ai_cfg().get("net_model") or ""
        val = _sd.askstring("AI 大模型 - 网络模型",
                            "网络模型名称（当前：%s）" % (cur or "未设置，如 deepseek-chat"),
                            parent=self, initialvalue=cur)
        if val is None:
            return
        val = val.strip()
        self._save_ai_cfg({"net_model": val})
        self._bubble("网络模型已设为：" + (val or "（空）"), 3)

    # ---- ★ 2026-10-11【视觉工具】截图取字（RapidOCR）+ 目标检测（YOLO11）----
    @staticmethod
    def _tools_dir():
        return os.path.dirname(os.path.abspath(__file__))

    @staticmethod
    def _funasr_py():
        p = r"D:\FAIRY\envs\funasr\Scripts\python.exe"
        return p if os.path.exists(p) else sys.executable

    def _vision_ocr(self):
        """★ 2026-10-12【选框OCR】微信风格截图选区域 -> 识别区域文字（不再全屏）。"""
        import subprocess as _sp

        def _work():
            self.say("拖拽选择要识别的区域", "doing")
            try:
                r = _rnw([sys.executable,
                             os.path.join(self._tools_dir(), "fairy_capture.py"),
                             "--mode", "ocr"], capture_output=True, timeout=120,
                            text=True, encoding="utf-8", errors="replace")
                d = json.loads((r.stdout or "{}").strip().splitlines()[-1] or "{}")
                if not d.get("ok"):
                    self.say("已取消" if "cancel" in str(d) else "识别失败", "warn")
                    return
                lines = d.get("lines") or []
                if not lines:
                    self.say("选区里没有识别到文字", "ok")
                    self._bubble("选区里没识别到文字", 3)
                    return
                self.say("识别到%d行文字" % len(lines), "ok")
                self._bubble("识别到 %d 行：%s" % (len(lines),
                                                " | ".join(lines)[:200]), 10)
            except Exception as e:
                self.say("识别失败", "warn")
                self._bubble("OCR 失败：" + str(e)[:60], 4)
        threading.Thread(target=_work, daemon=True).start()

    def _vision_ocr_region(self):
        """（已下线）区域截图识别改为微信风格选框，入口见 _vision_ocr。"""

    def _vision_detect(self):
        """★ 2026-10-12【选框识别物体】微信风格截图选区域 -> YOLO 检测区域物体。"""
        import subprocess as _sp

        def _work():
            self.say("拖拽选择要识别的区域", "doing")
            try:
                r = _rnw([sys.executable,
                             os.path.join(self._tools_dir(), "fairy_capture.py"),
                             "--mode", "detect"], capture_output=True, timeout=150,
                            text=True, encoding="utf-8", errors="replace")
                d = json.loads((r.stdout or "{}").strip().splitlines()[-1] or "{}")
                if not d.get("ok"):
                    self.say("已取消" if "cancel" in str(d) else "识别失败", "warn")
                    return
                objs = d.get("objects") or []
                if not objs:
                    self.say("选区里没有检测到物体", "ok")
                    self._bubble("选区里没检测到物体", 3)
                    return
                labels = "、".join(o["label"] for o in objs[:8])
                self.say("检测到%d个物体：%s" % (len(objs), labels), "ok")
                self._bubble("检测到 %d 个物体：%s" % (len(objs),
                                                   "、".join(o["label"] for o in objs[:10])), 10)
            except Exception as e:
                self.say("识别失败", "warn")
                self._bubble("检测失败：" + str(e)[:60], 4)
        threading.Thread(target=_work, daemon=True).start()

    # ---- ★ 2026-10-11【设备与传感器】自动扫描 + 连接 ----
    @staticmethod
    def _kind_cn(kind):
        return {"lan": "局域网", "bt": "蓝牙", "mdns": "智能设备", "serial": "串口",
                "nearlink": "星闪", "nearby": "星闪"}.get(kind, kind or "设备")

    def _iot_scan(self):
        """★ 2026-10-12【无窗扫描】扫描进度直接画在球右侧进度条面板；
        完成后语音+气泡播报结果，有新设备自动连接。不再弹任何窗口。
        防重入：扫描/连接期间重复点击只提示。"""
        import subprocess as _sp
        if getattr(self, "_iot_scanning", False):
            self._bubble("正在扫描设备，请稍候…", 3)
            return
        self._iot_scanning = True

        def _work():
            self.say("设备正在自检中，扫描局域网、蓝牙和智能设备", "doing")
            self._ball_progress(3, "设备自检 · 启动扫描", True)
            _json_buf = []
            try:
                _p = _sp.Popen([sys.executable,
                                os.path.join(self._tools_dir(), "fairy_iot.py"),
                                "--scan", "all", "--json", "--progress"],
                               stdout=_sp.PIPE, stderr=_sp.DEVNULL, text=True,
                               encoding="utf-8", errors="replace", bufsize=1,
                               creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
                for _line in _p.stdout:
                    _line = (_line or "").strip()
                    if not _line:
                        continue
                    if _line.startswith("P|"):
                        _ps = _line[2:].split("|")
                        if len(_ps) >= 3:
                            _st, _pct_v, _n = _ps[0], int(_ps[1]), int(_ps[2])
                            self._ball_progress(_pct_v, "设备自检 · %s · 已发现 %d 个"
                                                % (_st, _n), True)
                    else:
                        _json_buf.append(_line)
                _p.wait(timeout=200)
            except Exception as _pe:
                self._iot_scanning = False
                self._ball_progress(0, "", False)
                self.say("设备扫描失败", "warn")
                self._bubble("扫描失败：" + str(_pe)[:60], 4)
                return
            try:
                d = json.loads("".join(_json_buf) or "{}")
            except Exception:
                d = {}
            if not d.get("ok"):
                self._iot_scanning = False
                self._ball_progress(0, "", False)
                self.say("扫描失败", "warn")
                self._bubble("扫描失败：" + str(d.get("error"))[:60], 4)
                return
            newd = d.get("new_devs") or []
            total = d.get("count") or 0
            self._ball_progress(0, "", False)
            if not newd:
                self._iot_scanning = False
                self.say("没有新设备，当前%d个设备在线" % total, "ok")
                self._bubble("没有新设备，当前 %d 个设备在线" % total, 5)
                return
            self._ball_progress(60, "设备自检 · 连接 %d 个新设备…" % len(newd), True)
            try:
                r = _rnw([sys.executable,
                          os.path.join(self._tools_dir(), "fairy_iot.py"),
                          "--connect", "--yes", "--json"], capture_output=True,
                         timeout=180, text=True, encoding="utf-8", errors="replace")
                dd = json.loads(r.stdout or "{}")
            except Exception as e:
                dd = {"ok": False, "error": str(e)}
            self._ball_progress(0, "", False)
            self._iot_scanning = False
            if not dd.get("ok"):
                self.say("连接失败", "warn")
                self._bubble("连接失败：" + str(dd.get("error"))[:60], 4)
                return
            groups = {}
            for x in newd:
                k = self._kind_cn(x.get("kind"))
                groups.setdefault(k, []).append(str(x.get("name") or x.get("id")))
            parts = "。".join("发现%s%d个" % (k, len(v)) for k, v in groups.items())
            names = "、".join("%s%s" % (g, "：" + "、".join(vs[:3]))
                                  for g, vs in groups.items())
            self.say("已连接%d个新设备，%s" % (len(newd), parts), "ok")
            self._bubble("%s\n%s" % (parts, names), 8)
            # ★ 2026-10-12【未标记提醒】新设备里没起名的，语音+气泡提醒去标记
            _untag = [x for x in newd if not (x.get("tag") or "").strip()]
            if _untag:
                self.say("还有%d个新设备未标记，可以在控制面板的设备列表里给它们起名字"
                         % len(_untag), "warn")
                self._bubble("%d 个新设备未标记：面板→网络与设备→设备列表→点「标记」起名"
                             % len(_untag), 8)
        threading.Thread(target=_work, daemon=True).start()

    def _iot_devices(self):
        """★ 2026-10-12【设备列表】统一风格窗口：自动分「已标记 / 未标记」，
        未标记设备行尾带「标记」按钮，点一下直接起名（不再手动输 ID）。"""
        import subprocess as _sp
        import tkinter.simpledialog as _sd

        def _work():
            try:
                r = _rnw([sys.executable, os.path.join(self._tools_dir(), "fairy_iot.py"),
                             "--devices", "--json"], capture_output=True, timeout=30,
                            text=True, encoding="utf-8", errors="replace")
                d = json.loads(r.stdout or "{}")
                devs = d.get("devices") or []
            except Exception as e:
                self._bubble("查询设备失败：" + str(e)[:60], 4)
                return

            win, wrap = self._dialog("设备列表（分类 / 标记）", 520, 420)
            _cv = tk.Canvas(wrap, bg="#f2f3f5", highlightthickness=0)
            _sb = tk.Scrollbar(wrap, orient="vertical", command=_cv.yview)
            _cv.configure(yscrollcommand=_sb.set)
            _sb.pack(side="right", fill="y")
            _cv.pack(side="left", fill="both", expand=True)
            _inner = tk.Frame(_cv, bg="#f2f3f5")
            _wid = _cv.create_window((0, 0), window=_inner, anchor="nw")

            def _fit(_e):
                _cv.itemconfigure(_wid, width=max(120, _cv.winfo_width() - 8))
                _cv.configure(scrollregion=_cv.bbox("all"))
            _cv.bind("<Configure>", _fit)

            def _bind_wheel(_w):
                _w.bind("<MouseWheel>", lambda _ev: _cv.yview_scroll(
                    int(-1 * (_ev.delta / 120)), "units"))
                for _c in _w.winfo_children():
                    _bind_wheel(_c)

            if not devs:
                tk.Label(_inner, text="还没有已连接设备（先去「扫描新设备」）",
                         bg="#f2f3f5", fg="#6e7888",
                         font=("SimHei", 13)).pack(anchor="w", pady=10)
                _bind_wheel(_inner)
                return

            _tagged = [x for x in devs if (x.get("tag") or "").strip()]
            _untagged = [x for x in devs if not (x.get("tag") or "").strip()]

            _KCN = {"lan": "局域网", "bt": "蓝牙", "mdns": "组播/投屏",
                    "serial": "串口", "nearlink": "星闪", "nearby": "近距离"}

            def _fmt(x):
                # ★ 2026-10-12【自动区分】每行带厂商 + 通道 + 类型 + IP + MAC(后6位)，
                #   同局域网设备凭 MAC/IP/厂商一眼区分谁是谁。
                _n = x.get("tag") or x.get("name") or x.get("id") or "?"
                _t = x.get("type") or ""
                _v = x.get("vendor") or ""
                _k = _KCN.get(str(x.get("kind") or x.get("adapter") or ""), "")
                _ip = str(x.get("ip") or "").strip()
                _mac = str(x.get("id") or "")
                for _p in ("ble:", "lan:", "nearlink:", "nearby:", "serial:"):
                    _mac = _mac.replace(_p, "")
                _mac = (_mac[-6:] if len(_mac) > 6 else _mac)
                _bits = [b for b in (_v, _k, _t, _ip, _mac) if b and b != "未知设备"]
                _s = " · ".join(_bits)
                return ("%s（%s）" % (_n, _s)) if _s else str(_n)

            def _do_tag(x):
                _tag = _sd.askstring("标记设备", "给「%s」起个名字（例如：卧室灯）："
                                     % (x.get("name") or x.get("id")), parent=win)
                if not (_tag or "").strip():
                    return
                try:
                    _r2 = _rnw([sys.executable,
                                   os.path.join(self._tools_dir(), "fairy_iot.py"),
                                   "--tag", str(x.get("id")), _tag.strip(), "--json"],
                                  capture_output=True, timeout=30, text=True,
                                  encoding="utf-8")
                    _d2 = json.loads(_r2.stdout or "{}")
                    self._bubble(("已标记：%s" % _tag.strip()) if _d2.get("ok")
                                 else ("标记失败：" + str(_d2.get("msg") or "")[:50]), 4)
                    win.destroy()
                    self._iot_devices()      # 刷新列表
                except Exception as _e2:
                    self._bubble("标记失败：" + str(_e2)[:60], 4)

            def _sec(title, items, can_tag):
                if not items:
                    return
                tk.Label(_inner, text=title + "（%d）" % len(items), bg="#f2f3f5",
                         fg="#52749b", font=("SimHei", 15, "bold")
                         ).pack(anchor="w", pady=(10, 4))
                for _x in items:
                    _rowf = tk.Frame(_inner, bg="#f2f3f5")
                    _rowf.pack(fill="x", pady=2)
                    tk.Label(_rowf, text=_fmt(_x), bg="#f2f3f5", fg="#46536a",
                             font=("SimHei", 12), width=38, anchor="w"
                             ).pack(side="left", padx=4)
                    if can_tag:
                        _img = _pil_round("标记", 64, 34, 10, "#ffffff", "#52749b",
                                          True, "#dfe3ea")[0]
                        _b = tk.Button(_rowf, image=_img, bd=0, relief="flat",
                                       highlightthickness=0, bg="#f2f3f5",
                                       activebackground="#f2f3f5", cursor="hand2",
                                       command=lambda x=_x: _do_tag(x))
                        _b.image = _img
                        _b.pack(side="right", padx=4)
                    else:
                        tk.Label(_rowf, text="✓ 已标记", bg="#f2f3f5", fg="#8d97a8",
                                 font=("SimHei", 12)).pack(side="right", padx=6)

            _sec("已标记", _tagged, False)
            _sec("未标记", _untagged, True)
            _bind_wheel(_inner)
        threading.Thread(target=_work, daemon=True).start()

    def _iot_tag(self):
        """★ 标记设备：选择设备后输入名字/用途（使用者告诉 Fairy 这是什么）。"""
        import subprocess as _sp
        import tkinter.simpledialog as _sd

        def _work():
            try:
                r = _rnw([sys.executable, os.path.join(self._tools_dir(), "fairy_iot.py"),
                             "--devices", "--json"], capture_output=True, timeout=30,
                            text=True, encoding="utf-8", errors="replace")
                d = json.loads(r.stdout or "{}")
                devs = d.get("devices") or []
                if not devs:
                    self._bubble("还没有已连接设备（先去「扫描新设备」）", 4)
                    return
                menu = "\n".join("%s  %s（%s）" %
                                 (x.get("id"), x.get("tag") or x.get("name") or x.get("id"),
                                  x.get("type") or x.get("kind") or "?") for x in devs[:15])
                did = _sd.askstring("标记设备 - 选择",
                                    "输入要标记设备的 ID：\n\n%s" % menu, parent=self)
                if did is None or not did.strip():
                    return
                did = did.strip()
                hit = next((x for x in devs if x.get("id") == did), None)
                if not hit:
                    self._bubble("没找到设备：%s" % did, 4)
                    return
                tag = _sd.askstring("标记设备 - 起名",
                                    "给「%s」起个名字或说明（例如：卧室小米温湿度计）："
                                    % (hit.get("name") or did), parent=self)
                if tag is None or not tag.strip():
                    return
                r2 = _rnw([sys.executable, os.path.join(self._tools_dir(), "fairy_iot.py"),
                              "--tag", did, tag.strip(), "--json"], capture_output=True,
                             timeout=30, text=True, encoding="utf-8", errors="replace")
                d2 = json.loads(r2.stdout or "{}")
                if d2.get("ok"):
                    self.say("已标记：%s" % tag.strip(), "ok")
                    self._bubble("已标记：%s" % tag.strip(), 4)
                else:
                    self._bubble("标记失败：" + str(d2.get("msg") or "")[:60], 4)
            except Exception as e:
                self._bubble("标记失败：" + str(e)[:60], 4)
        threading.Thread(target=_work, daemon=True).start()

    # ---- ★ 2026-10-11【安全自检】病毒/漏洞只读检查 ----
    def _security_check(self):
        import subprocess as _sp

        def _work():
            self.say("正在进行安全自检，检查病毒防护和系统漏洞", "doing")
            try:
                r = _rnw([sys.executable, os.path.join(self._tools_dir(), "fairy_security.py"),
                             "--check", "--json"], capture_output=True, timeout=60,
                            text=True, encoding="utf-8", errors="replace")
                d = json.loads(r.stdout or "{}")
                if not d.get("ok") and "items" not in d:
                    self.say("安全自检失败", "warn")
                    self._bubble("自检失败：" + str(d.get("error"))[:60], 4)
                    return
                bad = [it for it in d.get("items") or [] if not it.get("ok")]
                if not bad:
                    self.say("安全自检通过，病毒防护正常，系统无高危漏洞", "ok")
                    self._bubble("安全自检通过：\n" + "\n".join(
                        "✓ %s：%s" % (it["name"], it["detail"]) for it in d.get("items") or []), 10)
                else:
                    self.say("安全自检发现%d项异常" % len(bad), "warn")
                    self._bubble("安全自检发现异常：\n" + "\n".join(
                        "✗ %s：%s" % (it["name"], it["detail"]) for it in bad), 12)
            except Exception as e:
                self.say("安全自检失败", "warn")
                self._bubble("自检失败：" + str(e)[:60], 4)
        threading.Thread(target=_work, daemon=True).start()

    # ---- ★ 2026-10-11【快捷功能】剪贴板历史 / 定时提醒 / 免打扰 / 取色器 / 常驻取字 ----
    def _clip_watch(self):
        """后台监听剪贴板，写入历史（1.5 秒轮询）。"""
        import subprocess as _sp
        last = ""
        while True:
            try:
                r = _rnw(["powershell", "-NoProfile", "-Command", "Get-Clipboard -Raw"],
                            capture_output=True, text=True, timeout=5, encoding="utf-8", errors="replace",
                            creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
                cur = (r.stdout or "").strip()
            except Exception:
                cur = ""
            if cur and cur != last:
                last = cur
                try:
                    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                    import fairy_clip as _fc
                    _fc.add(cur)
                except Exception:
                    pass
            time.sleep(1.5)


    def _remind_add(self):
        """★ 2026-10-12【定时提醒】统一风格弹窗：分钟 + 内容。"""
        win, wrap = self._dialog("定时提醒", 420, 260)
        tk.Label(wrap, text="多久后提醒（分钟）？", bg="#f2f3f5", fg="#6e7888",
                 font=("SimHei", 13)).pack(anchor="w", pady=(6, 2))
        _mins = tk.Entry(wrap, width=10, font=("SimHei", 13))
        _mins.insert(0, "10")
        _mins.pack(anchor="w", pady=(0, 10))
        tk.Label(wrap, text="提醒内容", bg="#f2f3f5", fg="#6e7888",
                 font=("SimHei", 13)).pack(anchor="w", pady=(0, 2))
        _msg = tk.Entry(wrap, width=34, font=("SimHei", 13))
        _msg.insert(0, "到时间了")
        _msg.pack(anchor="w")

        def _ok():
            try:
                mins = float(_mins.get().strip())
            except ValueError:
                self._bubble("分钟数不对", 3)
                return
            t = _msg.get().strip() or "到时间了"
            self._reminders.append((time.time() + mins * 60, t))
            self.say("好的，%g 分钟后提醒你" % mins, "ok")
            self._bubble("已设提醒：%g 分钟后「%s」" % (mins, t), 4)
            win.destroy()
        _row = tk.Frame(wrap, bg="#f2f3f5")
        _row.pack(anchor="w", pady=(12, 0))
        self._dlg_btn(_row, "确定", _ok)
        self._dlg_btn(_row, "取消", win.destroy)
        win.bind("<Return>", lambda _ev: _ok())
        _mins.focus_force()

    def _remind_loop(self):
        """提醒后台线程：到点播报（定时提醒 + 待办到期，已播的待办去重）。"""
        _todo_done = set()
        while True:
            try:
                now = time.time()
                due = [r for r in self._reminders if r[0] <= now]
                if due:
                    self._reminders = [r for r in self._reminders if r[0] > now]
                    for _, msg in due:
                        self.say("提醒：" + msg, "alert")
                        self._bubble("提醒：" + msg, 10)
                try:
                    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                    import fairy_todo as _ft
                    for _t in _ft.due():
                        _tid = _t.get("id")
                        if _tid not in _todo_done:
                            _todo_done.add(_tid)
                            self.say("待办到时间：" + _t.get("text", ""), "alert")
                            self._bubble("待办：" + _t.get("text", ""), 10)
                except Exception:
                    pass
            except Exception:
                pass
            time.sleep(1)   # 1 秒轮询：到点即报，误差不超过 1 秒

    def _dnd_toggle(self):
        """免打扰开关：静音所有语音播报（气泡保留）。"""
        self._dnd = not getattr(self, "_dnd", False)
        if self._dnd:
            self._bubble("已开启免打扰（语音播报静音）", 4)
        else:
            self._bubble("已关闭免打扰", 4)
            self.say("免打扰已关闭", "ok")

    def _color_pick(self):
        """屏幕取色器（独立窗口进程，点击复制颜色代码）。"""
        import subprocess as _sp
        try:
            _sp.Popen([sys.executable, os.path.join(self._tools_dir(), "fairy_color.py")],
                      creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            self._bubble("取色器启动失败：" + str(e)[:60], 4)

    def _overlay_ocr(self):
        """常驻屏幕取字：统一风格弹窗（居中+黑体+柔和色）。"""
        import subprocess as _sp
        win = tk.Toplevel(self)
        win.title("常驻屏幕取字")
        win.attributes("-topmost", True)
        win.configure(bg="#ffffff")
        win.resizable(False, False)
        _W, _H = 430, 250
        win.geometry("%dx%d+%d+%d" % (_W, _H,
                                      max(0, (win.winfo_screenwidth() - _W) // 2),
                                      max(0, (win.winfo_screenheight() - _H) // 2)))
        try:
            import ctypes
            _u = ctypes.windll.user32
            _g = ctypes.windll.gdi32
            _rgn = _g.CreateRoundRectRgn(0, 0, _W + 1, _H + 1, 16, 16)
            if _rgn:
                _u.SetWindowRgn(win.winfo_id(), _rgn, True)
        except Exception:
            pass
        _wrap = tk.Frame(win, bg="#ffffff")
        _wrap.pack(fill="both", expand=True, padx=24, pady=16)
        tk.Label(_wrap, text="常驻屏幕取字", bg="#ffffff", fg="#52749b",
                 font=("SimHei", 16, "bold")).pack(anchor="w", pady=(0, 8))
        tk.Label(_wrap, text="输入识别区域（屏幕坐标）：x y 宽 高",
                 bg="#ffffff", fg="#6e7888", font=("SimHei", 13)).pack(anchor="w")
        tk.Label(_wrap, text="例如：200 100 900 600", bg="#ffffff", fg="#8d97a8",
                 font=("SimHei", 12)).pack(anchor="w", pady=(2, 8))
        _v = tk.StringVar()
        tk.Entry(_wrap, textvariable=_v, font=("SimHei", 14), width=26).pack(anchor="w")

        def _ok():
            val = _v.get().strip()
            if not val:
                win.destroy()
                return
            parts = val.split()
            if len(parts) != 4 or not all(p.lstrip("-").isdigit() for p in parts):
                self._bubble("格式不对：需要 x y 宽 高 四个数字", 4)
                return
            win.destroy()
            try:
                _sp.Popen([sys.executable, os.path.join(self._tools_dir(), "fairy_overlay_ocr.py"),
                           "--region"] + parts, creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
                self.say("常驻取字窗口已开启", "ok")
            except Exception as e:
                self._bubble("启动失败：" + str(e)[:60], 4)

        _row = tk.Frame(_wrap, bg="#ffffff")
        _row.pack(fill="x", pady=(14, 0))
        tk.Button(_row, text="OK", command=_ok, bg="#e4edf6", fg="#46536a",
                  activebackground="#cdd9e6", font=("SimHei", 15, "bold"), bd=0,
                  highlightthickness=0, cursor="hand2", width=8).pack(side="left", padx=4)
        tk.Button(_row, text="Cancel", command=win.destroy, bg="#ffffff", fg="#46536a",
                  activebackground="#f2f3f5", font=("SimHei", 14), bd=0,
                  highlightthickness=1, highlightbackground="#d8dfe8", cursor="hand2",
                  width=8).pack(side="left", padx=4)
        win.bind("<Return>", lambda _e: _ok())
        win.focus_force()

    def _briefing_choose(self):
        """★ 2026-10-11【启动播报内容选择】勾选要播报的项，保存到 fairy.json。"""
        _items = [("time", "时间问候"), ("location", "当前位置"), ("weather", "天气"),
                  ("dress", "穿衣建议"), ("news", "新闻"), ("status", "服务状态"),
                  ("devices", "已连接设备数"), ("holiday", "今日节日/节气"),
                  ("todo", "今日待办"), ("network", "网络状态"), ("api_balance", "API 余额")]
        cur = {}
        try:
            with open(fairy_root.CONFIG, encoding="utf-8") as _f:
                _j = json.load(_f)
            cur = _j.get("briefing") or {}
        except Exception:
            pass
        win = tk.Toplevel(self)
        win.title("启动播报内容")
        win.attributes("-topmost", True)
        win.geometry("+%d+%d" % (self.winfo_screenwidth() // 2 - 130,
                                 self.winfo_screenheight() // 2 - 170))
        win.resizable(False, False)
        _vs = {}
        for _k, _nm in _items:
            _v = tk.BooleanVar(value=cur.get(_k, True))
            _vs[_k] = _v
            tk.Checkbutton(win, text=_nm, variable=_v, anchor="w",
                           font=("SimHei", 12)).pack(fill="x", padx=18, pady=3)

        def _save():
            _new = {_k: bool(_v.get()) for _k, _v in _vs.items()}
            try:
                with open(fairy_root.CONFIG, encoding="utf-8") as _f:
                    _j = json.load(_f)
                _j["briefing"] = _new
                with open(fairy_root.CONFIG, "w", encoding="utf-8") as _f:
                    json.dump(_j, _f, ensure_ascii=False, indent=2)
                self.say("启动播报内容已更新", "ok")
                self._bubble("已保存，下次启动生效", 4)
            except Exception as _e:
                self._bubble("保存失败：" + str(_e)[:60], 4)
            win.destroy()
        tk.Button(win, text="保存（下次启动生效）", command=_save,
                  font=("SimHei", 12)).pack(pady=(10, 4))
        tk.Button(win, text="取消", command=win.destroy,
                  font=("SimHei", 12)).pack(pady=(0, 8))

    def _screenshot_anno(self):
        """★ 2026-10-12【微信风格截图标注】全屏遮罩拖拽选区域 -> 选区标注。
        用 pythonw 启动（无控制台），选区后进入标注窗口（画框/箭头/文字）。"""
        try:
            _d = self._tools_dir()
            _w = os.path.join(os.path.dirname(_d), "pythonw.exe")
            _p = os.path.join(_d, "fairy_capture.py")
            if not os.path.exists(_w):
                _w = sys.executable
            _rnw([_w, _p, "--mode", "annotate"], timeout=3)
            self._bubble("拖拽选择截图区域，松开后进入标注", 3)
        except Exception as _e:
            self._bubble("截图标注启动失败：" + str(_e)[:50], 4)

    def _net_diag(self):
        """★ 2026-10-11【网络诊断】跑检测，气泡+语音播报结果。"""
        try:
            sys.path.insert(0, self._tools_dir())
            import fairy_netdiag as _fn
            _lines = _fn.report(_fn.diag())
            self._bubble("\n".join(_lines), 8)
            self.say(_lines[-1], "ok")
        except Exception as _e:
            self._bubble("网络诊断失败：" + str(_e)[:50], 4)

    def _sys_status(self):
        """★ 2026-10-11 系统状态：气泡+语音播报 CPU/内存/磁盘占用（不动硬件）。"""
        import subprocess as _sp
        try:
            _r = _rnw(["powershell", "-NoProfile", "-Command",
                          "$c=(Get-Counter '\\Processor(_Total)\\% Processor Time').CookedValues[0];"
                          "$m=Get-CimInstance Win32_OperatingSystem;"
                          "$mem=100*(1-$m.FreePhysicalMemory/$m.TotalVisibleMemorySize);"
                          "$d=Get-PSDrive C | Select-Object @{n='u';e={[math]::Round($_.Used/$_.Size*100,0)}};"
                          "Write-Output ('{0:N0}|{1:N0}|{2}' -f $c,$mem,$d.u)"],
                         capture_output=True, text=True, timeout=25, encoding="utf-8", errors="replace")
            _c, _m, _d = _r.stdout.strip().split("|")
            self._bubble("CPU %s%% · 内存 %s%% · C盘 %s%%" % (_c, _m, _d), 6)
            self.say("当前 CPU %s%%，内存 %s%%，C 盘占用 %s%" % (_c, _m, _d), "ok")
        except Exception as _e:
            self._bubble("系统状态读取失败：" + str(_e)[:50], 4)

    def _alert_loop(self):
        """★ 2026-10-11【天气预警】每 30 分钟查一次，预警变化才播（免打扰/关闭时静音）。"""
        while True:
            try:
                with open(fairy_root.CONFIG, encoding="utf-8") as _f:
                    _j = json.load(_f)
                _on = bool((_j.get("briefing") or {}).get("weather_alert", True))
                if _on:
                    import fairy_alert as _fa
                    _ws = _fa.check()
                    _s = "|".join(_ws)
                    if _ws and _s != getattr(self, "_alert_last", ""):
                        self._alert_last = _s
                        if not getattr(self, "_dnd", False):
                            self.say("提醒：" + "；".join(_ws[:2]), "alert")
                            self._bubble("天气预警：" + "；".join(_ws), 10)
            except Exception:
                pass
            time.sleep(1800)

    def _night_loop(self):
        """★ 2026-10-11【夜间模式】按 fairy.json 的 dnd 段时段自动开关免打扰。"""
        while True:
            try:
                with open(fairy_root.CONFIG, encoding="utf-8") as _f:
                    _dn = (json.load(_f).get("dnd") or {})
                if _dn.get("night"):
                    _st = (_dn.get("start") or "22:00").strip()
                    _en = (_dn.get("end") or "07:00").strip()
                    _now = time.strftime("%H:%M")
                    _in = (_st <= _now) or (_now < _en)   # 跨天时段
                    if _in != bool(getattr(self, "_dnd", False)):
                        self._dnd = _in
            except Exception:
                pass
            time.sleep(60)

    def _alert_toggle(self):
        """★ 2026-10-11【天气预警推送开关】存 fairy.json briefing.weather_alert。"""
        _now = True
        try:
            with open(fairy_root.CONFIG, encoding="utf-8") as _f:
                _j = json.load(_f)
            _now = bool((_j.get("briefing") or {}).get("weather_alert", True))
            (_j.setdefault("briefing", {}))["weather_alert"] = not _now
            with open(fairy_root.CONFIG, "w", encoding="utf-8") as _f:
                json.dump(_j, _f, ensure_ascii=False, indent=2)
            self._bubble("天气预警推送已" + ("关闭" if _now else "开启"), 3)
        except Exception as _e:
            self._bubble("操作失败：" + str(_e)[:50], 4)

    def _night_set(self):
        """★ 2026-10-12【夜间模式设置】统一风格弹窗：时段 + 开关。"""
        _cur = {"night": True, "start": "22:00", "end": "07:00"}
        try:
            with open(fairy_root.CONFIG, encoding="utf-8") as _f:
                _cur.update((json.load(_f).get("dnd") or {}))
        except Exception:
            pass
        win, wrap = self._dialog("夜间模式", 420, 280)
        _bg = "#f2f3f5"
        _on = tk.BooleanVar(value=bool(_cur.get("night", True)))
        tk.Checkbutton(wrap, text="开启夜间模式（到点自动静音播报）", variable=_on,
                       anchor="w", bg=_bg, fg="#46536a", activebackground=_bg,
                       selectcolor=_bg, font=("SimHei", 12)
                       ).pack(fill="x", pady=6)
        _st = tk.StringVar(value=_cur.get("start", "22:00"))
        _en = tk.StringVar(value=_cur.get("end", "07:00"))
        tk.Label(wrap, text="开始时间（如 22:00）", bg=_bg, fg="#6e7888",
                 font=("SimHei", 13)).pack(anchor="w")
        tk.Entry(wrap, textvariable=_st, width=14, font=("SimHei", 13)
                 ).pack(anchor="w", pady=(2, 8))
        tk.Label(wrap, text="结束时间（如 07:00）", bg=_bg, fg="#6e7888",
                 font=("SimHei", 13)).pack(anchor="w")
        tk.Entry(wrap, textvariable=_en, width=14, font=("SimHei", 13)
                 ).pack(anchor="w", pady=(2, 6))

        def _save():
            try:
                with open(fairy_root.CONFIG, encoding="utf-8") as _f:
                    _j = json.load(_f)
                _j["dnd"] = {"night": bool(_on.get()), "start": _st.get().strip(),
                             "end": _en.get().strip()}
                with open(fairy_root.CONFIG, "w", encoding="utf-8") as _f:
                    json.dump(_j, _f, ensure_ascii=False, indent=2)
                self._bubble("夜间模式已保存", 3)
            except Exception as _e:
                self._bubble("保存失败：" + str(_e)[:50], 4)
            win.destroy()
        _row = tk.Frame(wrap, bg=_bg)
        _row.pack(anchor="w", pady=(8, 0))
        self._dlg_btn(_row, "保存", _save)
        self._dlg_btn(_row, "取消", win.destroy)

    def _wg_toggle(self, key, name):
        try:
            if getattr(self, "wguard", None) is None:
                self._bubble("窗口守护未加载", 4)
                return
            on = self.wguard.toggle(key)
            self._bubble("%s已%s" % (name, "开启" if on else "关闭"), 3)
        except Exception as _e:
            print("[fairy] 窗口守护开关失败: %s" % _e, flush=True)

    def _wg_review_now(self):
        try:
            if getattr(self, "wguard", None) is None:
                self._bubble("窗口守护未加载", 4)
                return
            if self._wg_running:
                self._bubble("上轮观察还在跑，稍等再点", 3)
                return
            self._wg_running = True

            def _job():
                try:
                    self.wguard.tick(force=True)
                except Exception as _e:
                    print("[fairy] 立即审核出错: %s: %s" % (type(_e).__name__, _e), flush=True)
                finally:
                    self._wg_running = False

            threading.Thread(target=_job, daemon=True).start()
            self._bubble("开始审核当前代码…", 3)
        except Exception as _e:
            print("[fairy] 立即审核失败: %s" % _e, flush=True)

    # ---- 听歌识曲（Shazam 指纹识别，默认关闭）----
    def _result_win(self, title, text):
        """★ 2026-10-12【结果窗】统一风格弹窗显示多行结果（可滚动）。"""
        win, wrap = self._dialog(title, 540, 360)
        _cv = tk.Canvas(wrap, bg="#f2f3f5", highlightthickness=0)
        _sb = tk.Scrollbar(wrap, orient="vertical", command=_cv.yview)
        _cv.configure(yscrollcommand=_sb.set)
        _sb.pack(side="right", fill="y")
        _cv.pack(side="left", fill="both", expand=True)
        _inner = tk.Frame(_cv, bg="#f2f3f5")
        _wid = _cv.create_window((0, 0), window=_inner, anchor="nw")

        def _fit(_e):
            _cv.itemconfigure(_wid, width=max(120, _cv.winfo_width() - 8))
            _cv.configure(scrollregion=_cv.bbox("all"))
        _cv.bind("<Configure>", _fit)

        def _bind_wheel(_w):
            _w.bind("<MouseWheel>", lambda _ev: _cv.yview_scroll(
                int(-1 * (_ev.delta / 120)), "units"))
            for _c in _w.winfo_children():
                _bind_wheel(_c)
        for _ln in str(text).splitlines():
            tk.Label(_inner, text=_ln, bg="#f2f3f5", fg="#46536a",
                     font=("SimHei", 12), justify="left", anchor="w",
                     wraplength=460).pack(fill="x", padx=8, pady=2)
        _bind_wheel(_inner)


    def _songrec_toggle(self):
        """★ 2026-10-12【听歌识曲两态】开/关后台循环（按钮显示当前状态）。
        默认关闭，用户点开才识别。"""
        self._songrec_on = not getattr(self, "_songrec_on", False)
        _st = "开启" if self._songrec_on else "停止"
        self._bubble("听歌识曲已" + _st, 3)
        try:
            self.say("听歌识曲已" + _st, "ok")
        except Exception:
            pass

    def _songrec_now(self):
        """★ 2026-10-11【快捷操作】立即听歌识曲（绕过 18 秒节流）。"""
        self._songrec_at = 0
        threading.Thread(target=self._songrec_identify, daemon=True).start()

    def _songrec_loop(self):
        """后台循环：每 18 秒抓系统声音识别一次（同一首歌不重复报）。
        两态开关：_songrec_on=False 时跳过识别。"""
        while True:
            time.sleep(5)
            try:
                if not getattr(self, "_songrec_on", False):
                    continue
                if time.time() - self._songrec_at < 18:
                    continue
                self._songrec_at = time.time()
                threading.Thread(target=self._songrec_identify, daemon=True).start()
            except Exception as _e:
                print("[fairy] 听歌识曲轮询出错: %s" % _e, flush=True)

    def _songrec_identify(self):
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import song_recognition as _sr
            r = _sr.identify_once(None, 8)
        except Exception as _e:
            print("[fairy] 听歌识曲识别出错: %s" % _e, flush=True)
            return
        if not r.get("ok"):
            return
        sig = "%s|%s" % (r.get("title") or "", r.get("artist") or "")
        if not sig.strip("|") or sig == self._songrec_last:
            return
        self._songrec_last = sig
        _t = "识别到：《%s》 - %s" % (fix_mojibake(r.get("title") or "?"),
                                   fix_mojibake(r.get("artist") or "?"))   # ★ 乱码自愈
        if r.get("link"):
            _t += "\n" + r["link"]
        self._wg_bubbles.append(_t)

    def _boot_progress(self):
        """★ 2026-10-12【启动进度 v4】不再弹独立进度气泡，
        进度直接画在球右侧进度条面板：阶段名 + 百分比，全部就绪后自动清空。"""
        try:
            import socket as _sock
            import threading as _th

            def _port_up(_port, _host="127.0.0.1", _t=0.25):
                try:
                    _s = _sock.socket()
                    _s.settimeout(_t)
                    _s.connect((_host, _port))
                    _s.close()
                    return True
                except Exception:
                    return False

            # ★ 2026-10-12【启动默认游戏模式】启动进度跳过引擎三阶段：
            #   每次启动先进游戏模式 -> 引擎不探测、不拉起、不加载模型，
            #   启动完成后 _boot_pick_game 释放全部引擎；要对话时面板手动切组合。
            _STAGES = [
                ("加载语音素材", lambda: bool(os.path.exists(VOICE_INDEX)
                                           and os.path.getsize(VOICE_INDEX) > 0)),
                ("连接本地大脑", lambda: _port_up(19388)),
                ("语音就绪", lambda: _port_up(9881)),
            ]
            _n = len(_STAGES)
            _done = [False] * _n
            _idx = {"v": 0}                    # 当前阶段
            _enter = {"t": time.time()}        # 进入当前阶段的时间
            _deadline = time.time() + 90
            _pick = {"done": False}            # 启动完成后的默认组合只切一次
            _selfheal = {"done": False}        # 66% 卡住只自愈一次

            def _draw():
                _k = _idx["v"]
                _p = int(100 * _k / _n)
                if _k < _n:
                    self._ball_progress(_p, "Fairy 启动中 · " + _STAGES[_k][0], True)
                else:
                    self._ball_progress(100, "Fairy 启动完成", True)
                    # ★ 2026-10-12【启动默认游戏模式】启动完成后自动进入游戏模式
                    if not _pick["done"]:
                        _pick["done"] = True
                        try:
                            self.after(300, self._boot_pick_game)
                        except Exception:
                            pass
                if _k >= _n or time.time() > _deadline:
                    self._ball_progress(0, "", False)
                    # ★ 2026-10-12【启动收尾】无论进度走完还是超时，
                    #   启动都进入游戏模式（释放全部引擎），避免卡住时永不生效。
                    if not _pick["done"]:
                        _pick["done"] = True
                        try:
                            self.after(300, self._boot_pick_game)
                        except Exception:
                            pass
                    return False
                return True

            def _check():
                """★ 2026-10-12【逐阶段推进】只检查当前阶段；就绪后至少停留 1 秒
                再进下一阶段 -> 进度条按 0→16→33→50→66→83→100 渐进显示。
                模型加载阶段未就绪时停在「加载本地模型」等待真正加载完。"""
                _i = _idx["v"]
                if _i >= _n:
                    return
                # ★ 2026-10-12【66% 自愈】「连接本地大脑」卡住超 8 秒 ->
                #   播报"模型未能启动"并自动重启本地大脑（只做一次，不反复打扰）
                #   ★ 2026-10-12：启动阶段精简后「连接本地大脑」是第 1 阶段
                #   （索引 1），旧值 4 会导致自愈永不触发。
                if _i == 1 and not _selfheal["done"]:
                    if time.time() - _enter["t"] >= 8.0 and not _port_up(19388):
                        _selfheal["done"] = True
                        try:
                            self.say("模型未能启动，正在重新启动本地大脑", "warn")
                            self._bubble("本地大脑未响应，正在自动重启", 5)
                            self._spawn_host()
                        except Exception:
                            pass
                try:
                    if _STAGES[_i][1]():
                        if time.time() - _enter["t"] >= 1.0:
                            _done[_i] = True
                            _idx["v"] = _i + 1
                            _enter["t"] = time.time()
                            try:
                                self.after(0, _draw)
                            except Exception:
                                pass
                except Exception:
                    pass
                if _draw():
                    self.after(400, _check)

            _draw()
            _th.Thread(target=_check, daemon=True).start()
        except Exception:
            pass

    def _boot_pick_game(self):
        """★ 2026-10-12【启动默认游戏模式】Fairy 每次启动先进游戏模式：
        释放全部引擎和模型（显存全给游戏），语音唤醒/识别等轻功能照常；
        需要对话时在面板手动切换组合（日常全能/高精度/极速）。"""
        try:
            _ai = self._ai_cfg()
            if (_ai.get("preset") or "") != "game":
                self._save_ai_cfg({"backend": "off", "model": "", "port": "",
                                   "preset": "game"})
            # 释放全部引擎和模型（Strata + llama.cpp + Ollama 进程）
            self._engine_release()
        except Exception:
            pass

    def _stop_port_proc(self, port):
        """停掉监听指定端口的整个进程树（如 Strata 8080），模型文件保留。"""
        try:
            import subprocess as _sp2
            _out = _sp2.run(["netstat", "-ano"], capture_output=True, text=True,
                            timeout=10).stdout or ""
            _pids = set()
            for _ln in _out.splitlines():
                if ("127.0.0.1:%d" % port) in _ln and "LISTENING" in _ln:
                    _p = _ln.split()[-1]
                    if _p.strip().isdigit():
                        _pids.add(_p.strip())
            for _pid in _pids:
                _sp2.run(["taskkill", "/F", "/T", "/PID", _pid],
                         capture_output=True, timeout=10,
                         creationflags=getattr(_sp2, "CREATE_NO_WINDOW", 0))
            return bool(_pids)
        except Exception:
            return False

    def _spawn_host(self):
        """拉起本地大脑（fairy_host.py，端口 19388）——启动进度 66%
        「连接本地大脑」卡住时的自动重启（自愈）。"""
        try:
            import subprocess as _sp
            _dir = os.path.dirname(os.path.abspath(__file__))
            _py = sys.executable or "pythonw"
            _sp.Popen([_py, os.path.join(_dir, "fairy_host.py")],
                      cwd=_dir,
                      creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0),
                      stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        except Exception:
            pass

    # ★ 2026-10-17【DSH 模型联动】fairy 组合 -> DSH 主对话默认模型（provider, model）
    _DSH_MODEL_BY_ENG = {
        "strata": ("strata", "qwen3.8-flash-next-unsloth-ud-iq4_xs"),
        "llm":    ("llm", "qwen3.5-9b"),
        "vl":     ("vl", "qwen3-vl-8b"),
    }

    def _dsh_switch_model(self, provider, model):
        """★ 2026-10-17【DSH 模型联动】切 fairy 组合时，把 DSH 主对话默认模型
        （cordis.patch.yml 的 agent-default-model）换成对应 provider/model。
        DSH 在跑就自动重启使其立即生效；没跑则下次 DSH 启动生效。绝不抛异常。"""
        import re as _re
        _p = r"C:\Users\Administrator\.dsh\profiles\desktop\cordis.patch.yml"
        try:
            if not os.path.exists(_p):
                return False
            with open(_p, "r", encoding="utf-8") as _f:
                _txt = _f.read()
            _bak = _p + ".bak_fairy_combo"
            if not os.path.exists(_bak):
                try:
                    open(_bak, "w", encoding="utf-8").write(_txt)
                except Exception:
                    pass
            _m = _re.search(r"(- id: agent-default-model\s*\n(?:.*\n)*?)(?=\n- id: |\Z)", _txt)
            if not _m:
                return False
            _block = _m.group(1)
            _nb = _re.sub(r"(?m)^(    provider: ).*$", lambda _x: _x.group(1) + provider, _block)
            _nb = _re.sub(r"(?m)^(    model: ).*$", lambda _x: _x.group(1) + model, _nb)
            _new = _txt[:_m.start(1)] + _nb + _txt[_m.end(1):]
            if _new != _txt:
                with open(_p, "w", encoding="utf-8") as _f:
                    _f.write(_new)
            _up = False
            try:
                import socket as _sk4
                _s4 = _sk4.socket(); _s4.settimeout(1.0)
                _up = _s4.connect_ex(("127.0.0.1", 19387)) == 0
                _s4.close()
            except Exception:
                _up = False
            if _up:
                try:
                    import subprocess as _sp4
                    _sp4.run(["taskkill", "/F", "/IM", "DeepSeek Harness.exe"],
                             capture_output=True, timeout=10,
                             creationflags=getattr(_sp4, "CREATE_NO_WINDOW", 0))
                except Exception:
                    pass
                try:
                    self._bubble("DSH 模型已同步，正在重启 DSH 生效", 4)
                except Exception:
                    pass
                threading.Thread(target=self._ensure_dsh, daemon=True).start()
            return True
        except Exception:
            return False

    def _apply_preset(self, nm, eng, port, models, desc):
        """★ 2026-10-17【组合切换核心】右键菜单 + 设置面板共用。
           切组合：先停旧组合引擎，再起新组合引擎，进度播报，统一开 8090 对话页。"""
        try:
            if eng == "off":
                self._engine_release()
                self._save_ai_cfg({"preset": "game"})
                self._bubble("游戏模式：全部模型已释放，显存已空出，尽情玩吧", 5)
                try:
                    self.say("游戏模式：模型已全部释放，显存已空出，可以开始游戏", "ok")
                except Exception:
                    pass
                return
            mdl = models[0] if models else ""
            self._save_ai_cfg({"backend": eng, "model": mdl,
                               "port": port, "preset": nm})
            try:
                threading.Thread(target=self._cleanup_engines,
                                 args=(eng,), daemon=True).start()
            except Exception:
                pass
            self._bubble("正在切换组合：%s（%s）" % (nm, mdl), 4)
            try:
                self.say("正在切换组合：%s" % nm, "doing")
            except Exception:
                pass
            self._preset_progress(nm, port)

            def _ensure():
                try:
                    if eng == "strata":
                        try:
                            if (self._strata_probe().get("state") or "") != "running":
                                self._strata_start()
                        except Exception:
                            pass
                    elif eng == "llm":
                        import socket as _sk3
                        import subprocess as _sp3
                        _s3 = _sk3.socket(); _s3.settimeout(0.4)
                        try:
                            _s3.connect(("127.0.0.1", 8085)); _s3.close()
                            return
                        except Exception:
                            pass
                        try:
                            _p3 = os.path.join(
                                os.path.dirname(os.path.abspath(__file__)),
                                "fairy_llm.py")
                            _sp3.Popen([sys.executable, _p3],
                                       creationflags=getattr(_sp3, "CREATE_NO_WINDOW", 0),
                                       stdout=_sp3.DEVNULL, stderr=_sp3.DEVNULL)
                            self._bubble("已拉起本地文本引擎（8085），请稍候", 4)
                        except Exception:
                            pass
                    elif eng == "vl":
                        import socket as _sk4
                        import subprocess as _sp4
                        _s4 = _sk4.socket(); _s4.settimeout(0.4)
                        try:
                            _s4.connect(("127.0.0.1", 8086)); _s4.close()
                            return
                        except Exception:
                            pass
                        try:
                            _p4 = os.path.join(
                                os.path.dirname(os.path.abspath(__file__)),
                                "fairy_vl.py")
                            _sp4.Popen([sys.executable, _p4],
                                       creationflags=getattr(_sp4, "CREATE_NO_WINDOW", 0),
                                       stdout=_sp4.DEVNULL, stderr=_sp4.DEVNULL)
                            self._bubble("已拉起视觉引擎（8086），请稍候", 4)
                        except Exception:
                            pass
                except Exception:
                    pass
            threading.Thread(target=_ensure, daemon=True).start()
            self._bubble("已切换组合：%s（%s）" % (nm, mdl), 5)
            try:
                self.say("已切换组合：%s" % nm, "ok")
            except Exception:
                pass
            try:
                if eng in ("strata", "llm", "vl"):
                    self._open_chat_web(eng, port, mdl)
            except Exception:
                pass
            try:
                _dp, _dm = self._DSH_MODEL_BY_ENG.get(eng, (None, None))
                if _dp:
                    self._dsh_switch_model(_dp, _dm)
            except Exception:
                pass
        except Exception as _e:
            try:
                self._bubble("组合切换失败：" + str(_e)[:50], 5)
            except Exception:
                pass

    def _on_right(self, e):
        """★ 2026-10-11【右键菜单】圆角浮动面板，与设置面板同风格。"""
        self._close_menu()
        _m = tk.Toplevel(self)
        _m.overrideredirect(True)
        _m.attributes("-topmost", True)
        _m.configure(bg="#f2f3f5")
        _bgk = "#f2f3f5"
        try:
            _running = self._strata_probe().get("state") == "running"
        except Exception:
            _running = False
        _items = [("Fairy 控制面板", self._open_panel), None,
                  ("本地运算 · 切换组合", self._on_right_combo),
                  ("全部退出 Fairy", self._quit_all)]
        _w = 264
        _h = sum(52 if it else 2 for it in _items) + 18
        _x = min(e.x_root, self.winfo_screenwidth() - _w - 4)
        _y = min(e.y_root, self.winfo_screenheight() - _h - 4)
        _m.geometry("%dx%d+%d+%d" % (_w, _h, max(0, _x), max(0, _y)))
        # ★ 2026-10-11【窗口圆角】GDI 圆角区域裁剪（Win7+ 通用）
        try:
            import ctypes
            _u = ctypes.windll.user32
            _g = ctypes.windll.gdi32
            _hwnd = _m.winfo_id()
            _rgn = _g.CreateRoundRectRgn(0, 0, _w + 1, _h + 1, 22, 22)
            if _rgn:
                _u.SetWindowRgn(_hwnd, _rgn, True)
        except Exception:
            pass
        _wrap = tk.Frame(_m, bg=_bgk)
        _wrap.pack(fill="both", expand=True, padx=6, pady=8)

        def _mk(label, cmd, danger=False):
            _fg = "#a8625e" if danger else "#46536a"
            # 正常态：白底圆角；悬停态：浅蓝底
            _img = _pil_round(label, 248, 48, 12, "#ffffff", _fg, True, "#d8dfe8")[0]
            _img_h = _pil_round(label, 248, 48, 12, "#e4edf6", _fg, True, "#cdd9e6")[0]
            _b = tk.Button(_wrap, image=_img, bd=0, relief="flat",
                           highlightthickness=0, bg=_bgk,
                           activebackground=_bgk, cursor="hand2",
                           command=lambda c=cmd: self._menu_click(c))
            _b.image = _img
            _b.bind("<Enter>", lambda _ev, b=_b, n=_img, h=_img_h: b.configure(image=h))
            _b.bind("<Leave>", lambda _ev, b=_b, n=_img, h=_img_h: b.configure(image=n))
            _b.pack(fill="x", pady=2)

        for _it in _items:
            if _it is None:
                tk.Frame(_wrap, bg="#d8dfe8", height=1).pack(fill="x", pady=3)
            else:
                _mk(*_it)
        _m.bind("<FocusOut>", lambda _ev: self._close_menu())
        _m.bind("<Escape>", lambda _ev: self._close_menu())
        _m.focus_force()
        self._menu_win = _m

    def _on_right_combo(self):
        """★ 2026-10-17【右键组合切换】右键菜单弹组合选择面板，点选即切换。
           组合放进「本地运算」入口。"""
        self._close_menu()
        _PRESETS = [
            ("日常全能", "strata", "8080",
             ["Qwen3.8-Flash-Next\u00b7UD-IQ4_XS"],
             "Strata 主力：写代码/日常全能"),
            ("高精度", "llm", "8085",
             ["qwen3.5-9b"],
             "更强，长推理/疑难问题（本地 9B 引擎）"),
            ("视觉", "vl", "8086",
             ["qwen3-vl-8b"],
             "视觉模型，可看图可对话（Qwen3-VL-8B）"),
            ("游戏模式", "off", "", [],
             "释放全部引擎与模型，显存全给游戏"),
        ]
        _m = tk.Toplevel(self)
        _m.overrideredirect(True)
        _m.attributes("-topmost", True)
        _m.configure(bg="#f2f3f5")
        _bgk = "#f2f3f5"
        try:
            _cur = (self._ai_cfg().get("preset") or "")
        except Exception:
            _cur = ""
        _w = 284
        _h = len(_PRESETS) * 56 + 18
        _x = min(self.winfo_pointerx(), self.winfo_screenwidth() - _w - 4)
        _y = min(self.winfo_pointery(), self.winfo_screenheight() - _h - 4)
        _m.geometry("%dx%d+%d+%d" % (_w, _h, max(0, _x), max(0, _y)))
        try:
            import ctypes
            _u = ctypes.windll.user32
            _g = ctypes.windll.gdi32
            _hwnd = _m.winfo_id()
            _rgn = _g.CreateRoundRectRgn(0, 0, _w + 1, _h + 1, 22, 22)
            if _rgn:
                _u.SetWindowRgn(_hwnd, _rgn, True)
        except Exception:
            pass
        _wrap = tk.Frame(_m, bg=_bgk)
        _wrap.pack(fill="both", expand=True, padx=6, pady=8)

        def _mk(label, cmd):
            _fg = "#46536a"
            _img = _pil_round(label, 266, 48, 12, "#ffffff", _fg, True, "#d8dfe8")[0]
            _img_h = _pil_round(label, 266, 48, 12, "#e4edf6", _fg, True, "#cdd9e6")[0]
            _b = tk.Button(_wrap, image=_img, bd=0, relief="flat",
                           highlightthickness=0, bg=_bgk,
                           activebackground=_bgk, cursor="hand2",
                           command=lambda c=cmd: self._menu_click(c))
            _b.image = _img
            _b.bind("<Enter>", lambda _ev, b=_b, n=_img, h=_img_h: b.configure(image=h))
            _b.bind("<Leave>", lambda _ev, b=_b, n=_img, h=_img_h: b.configure(image=n))
            _b.pack(fill="x", pady=2)
        for _nm, _eng, _pt2, _models, _desc in _PRESETS:
            _sel = (_cur == _nm) or (_cur == "game" and _nm == "游戏模式")
            _ck = "✓ " if _sel else "  "
            _mk(_ck + _nm, lambda n=_nm, e=_eng, p=_pt2, ms=_models, d=_desc:
                self._apply_preset(n, e, p, ms, d))
        _m.bind("<FocusOut>", lambda _ev: self._close_menu())
        _m.bind("<Escape>", lambda _ev: self._close_menu())
        _m.focus_force()
        self._menu_win = _m

    def _close_menu(self):
        """★ 2026-10-11【右键菜单】关闭当前浮动菜单。"""
        _m = getattr(self, "_menu_win", None)
        if _m:
            try:
                _m.destroy()
            except Exception:
                pass
            self._menu_win = None

    def _menu_click(self, cmd):
        """★ 2026-10-12【菜单点击】先关菜单再执行命令；异常落盘 panel_err.log。"""
        try:
            self._close_menu()
        except Exception:
            pass
        try:
            cmd()
        except Exception as _e:
            import traceback as _tb
            try:
                _h = io.open(r"D:\FAIRY\logs\panel_err.log", "a", encoding="utf-8")
                _h.write("MENU_CLICK\n%s\n" % _tb.format_exc())
                _h.close()
            except Exception:
                pass
            try:
                self._bubble("菜单执行失败：" + str(_e)[:50], 5)
            except Exception:
                pass

    def _cleanup_engines(self, _target):
        """★ 2026-10-12【组合退出清理】切走旧组合 -> 停掉不用的引擎/模型，释放资源。

        _target = 新组合的引擎（strata / ollama / llm / off）：
        · 不是 strata -> 停 8080 进程树（Strata + 模型，约释放几十 GB 内存）
        · 不是 llm    -> 停 8085 进程树（视觉 llama.cpp）
        · 不是 ollama -> 不杀 ollama.exe（语音大脑 ask_brain 依赖它），
                        只把已加载的模型全部卸载（keep_alive=0）释放显存
        """
        _bits = []
        if _target != "strata":
            try:
                if self._stop_port_proc(8080):
                    _bits.append("Strata")
            except Exception:
                pass
        if _target != "llm":
            try:
                if self._stop_port_proc(8085):
                    _bits.append("视觉引擎")
            except Exception:
                pass
        if _target != "ollama":
            try:
                import urllib.request as _ur
                _r = _ur.urlopen("http://127.0.0.1:11434/api/tags", timeout=3)
                _j = json.loads(_r.read().decode("utf-8", "replace"))
                for _m in (_j.get("models") or []):
                    _mn = _m.get("name") or _m.get("model") or ""
                    if not _mn:
                        continue
                    try:
                        _req = _ur.Request(
                            "http://127.0.0.1:11434/api/generate",
                            data=json.dumps({"model": _mn, "keep_alive": 0})
                            .encode("utf-8"),
                            headers={"content-type": "application/json"})
                        _ur.urlopen(_req, timeout=8).read()
                        _bits.append(_mn)
                    except Exception:
                        pass
            except Exception:
                pass
        if _bits:
            try:
                self._bubble("已释放：%s" % "、".join(_bits), 4)
            except Exception:
                pass

    def _engine_release(self):
        """★ 2026-10-11【退出引擎与模型释放】停止本地引擎，释放显存/内存。"""
        import subprocess as _sp
        _kw = {"creationflags": getattr(_sp, "CREATE_NO_WINDOW", 0),
               "stdout": _sp.DEVNULL, "stderr": _sp.DEVNULL}
        try:
            self._strata_stop()
        except Exception:
            pass
        for _p in ("llama-server.exe", "ollama.exe", "koboldcpp.exe"):
            try:
                _sp.Popen(["taskkill", "/F", "/IM", _p], **_kw)
            except Exception:
                pass
        self._bubble("引擎与模型已退出，显存/内存已释放", 4)
        self.say("引擎与模型已退出，资源已释放", "ok")

    def _perms_toggle(self, pid):
        """设置菜单：逐项授权/撤销。"""
        import fairy_perms as _fp
        _au = _fp.authorized()
        if pid in _au:
            _fp.revoke([pid])
            self._bubble("已撤销权限：" + pid, 3)
        else:
            _fp.set_perms(_au + [pid])
            self._bubble("已授权：" + pid, 3)

    def _perms_all(self):
        import fairy_perms as _fp
        _fp.set_perms(["all"])
        self._bubble("已授权全部权限", 3)

    def _perms_show(self):
        import fairy_perms as _fp
        lines = ["【Fairy 权限】"]
        _au = _fp.authorized()
        for _pid, _nm, _use, _no in _fp.PERMS:
            lines.append("%s %s：%s" % ("✓" if _pid in _au else "✗", _nm, _use))
        self._bubble("\n".join(lines), 8)

    def _mem_panel(self):
        import fairy_memory as _fm
        s = _fm.stats()
        lines = ["【Fairy 记忆】", "累计交互：%d 次 · 记住短语 %d 个"
                 % (s.get("total", 0), s.get("phrases", 0))]
        _top = s.get("top") or []
        lines.append("常用功能：%s" % ("、".join(f for f, _ in _top) or "暂无"))
        _eng = s.get("engines") or []
        if _eng:
            lines.append("偏好引擎：" + " > ".join(_eng))
        self._bubble("\n".join(lines), 6)

    def _voice_set(self, name):
        """设置菜单：切换音色（写 fairy.json endpoints.ref_audio，下次播报生效）。"""
        import fairy_voice as _fv
        try:
            _n = _fv.set_voice(name)
            self._bubble("已切换音色：%s（下次播报生效）" % _n, 4)
        except Exception as _e:
            self._bubble("切换音色失败：%s" % _e, 5)


    def _open_panel(self):
        """★ 2026-10-11【Fairy 控制面板】入口：异常落盘日志。"""
        try:
            self._open_panel_impl()
        except Exception as _e:
            import traceback
            try:
                _h = io.open(r"D:\FAIRY\logs\panel_err.log", "a", encoding="utf-8")
                _h.write("%s\n%s\n" % (_e, traceback.format_exc()))
                _h.close()
            except Exception:
                pass

    def _open_panel_impl(self):
        """★ 2026-10-11【Fairy 控制面板】左侧圆角导航 + 右侧圆角按钮长页。"""
        win = tk.Toplevel(self)
        win.title("")
        win.attributes("-topmost", True)
        win.overrideredirect(True)
        win.configure(bg="#ffffff")
        win.resizable(False, False)
        # 固定屏幕中央（首次设置 + 窗口显示后再校准一次）
        def _center():
            _w, _h = 860, 600
            _sw = win.winfo_screenwidth()
            _sh = win.winfo_screenheight()
            win.geometry("%dx%d+%d+%d" % (_w, _h,
                                          max(0, (_sw - _w) // 2),
                                          max(0, (_sh - _h) // 2)))
        _center()
        win.after(120, _center)
        # —— 顶部标题栏：无系统标题栏，这里放关闭/退出按钮 ——
        tbar = tk.Frame(win, bg="#f2f3f5")
        tbar.pack(side="top", fill="x")

        def _tbtn(_txt, _fg, _bg, _bg_h, _cmd):
            _im = _pil_round(_txt, 86, 30, 10, _bg, _fg, True, "#dfe3ea")[0]
            _imh = _pil_round(_txt, 86, 30, 10, _bg_h, _fg, True, "#dfe3ea")[0]
            _b = tk.Button(tbar, image=_im, bd=0, relief="flat",
                           highlightthickness=0, bg="#f2f3f5", activebackground="#f2f3f5",
                           cursor="hand2", command=_cmd)
            _b.image = _im
            _b.bind("<Enter>", lambda _ev, b=_b, i=_imh: b.configure(image=i))
            _b.bind("<Leave>", lambda _ev, b=_b, i=_im: b.configure(image=i))
            return _b
        _tbtn("退出 Fairy", "#a8625e", "#ffffff", "#f4e6e4", self._quit_all).pack(
            side="right", padx=(0, 6), pady=8)
        _tbtn("关闭面板", "#46536a", "#ffffff", "#eef1f6", win.destroy).pack(
            side="right", padx=(0, 2), pady=8)
        # —— 右栏（占满）：长页滚动 ——
        body = tk.Frame(win, bg="#ffffff")
        body.pack(fill="both", expand=True)
        _cv = tk.Canvas(body, bg="#ffffff", highlightthickness=0)
        _sb = tk.Scrollbar(body, orient="vertical", command=_cv.yview)
        _cv.configure(yscrollcommand=_sb.set)
        _sb.pack(side="right", fill="y")
        _cv.pack(side="left", fill="both", expand=True)
        _inner = tk.Frame(_cv, bg="#ffffff")
        _wid = _cv.create_window((18, 6), window=_inner, anchor="nw")
        # ★ 2026-10-12【滚动条自动伸缩】展开/收起/内容增长时内层尺寸一变，
        #   滚动区立即重算 -> 滚动条自己伸长/缩短。
        _inner.bind("<Configure>", lambda _e: _cv.configure(
            scrollregion=_cv.bbox("all")))
        # ★ 无标题栏 -> 按住空白区域拖动窗口
        _drag = {"x": 0, "y": 0, "on": False}

        def _press(_e):
            _drag.update(x=_e.x_root, y=_e.y_root, on=True)

        def _move(_e):
            if _drag["on"]:
                win.geometry("+%d+%d" % (win.winfo_x() + _e.x_root - _drag["x"],
                                         win.winfo_y() + _e.y_root - _drag["y"]))
                _drag.update(x=_e.x_root, y=_e.y_root)

        def _rel(_e):
            _drag["on"] = False

        for _w in (body, _cv, _inner, tbar):
            try:
                _w.bind("<Button-1>", _press)
                _w.bind("<B1-Motion>", _move)
                _w.bind("<ButtonRelease-1>", _rel)
            except Exception:
                pass

        def _fit(_e):
            _cv.itemconfigure(_wid, width=max(100, _cv.winfo_width() - 36))
            _cv.configure(scrollregion=_cv.bbox("all"))
        _cv.bind("<Configure>", _fit)

        def _row(parent, title, items):
            if title:
                tk.Label(parent, text=title, bg="#ffffff", fg="#66727f",
                         font=("SimHei", 12, "bold")).pack(pady=(7, 3))
            r = tk.Frame(parent, bg="#ffffff")
            r.pack(fill="x")
            _imgs = [(_pil_round(_l, 100, 34, 10, "#ffffff", "#46536a", True)[0], _l, _c)
                     for _l, _c in items]
            _cur = tk.Frame(r, bg="#ffffff")
            _cur.pack()
            _w = 0
            for _im, _l, _c in _imgs:
                _bw = _im.width() + 10
                if _w > 0 and _w + _bw > 520:
                    _cur = tk.Frame(r, bg="#ffffff")
                    _cur.pack()
                    _w = 0
                _b = tk.Button(_cur, image=_im, command=_c, bd=0, relief="flat",
                               highlightthickness=0, bg="#ffffff",
                               activebackground="#ffffff", cursor="hand2")
                _b.image = _im
                _b.pack(side="left", padx=4, pady=3)
                _w += _bw

        def _exp_row(parent, title, build):
            """★ 2026-10-12【内嵌展开】点击标题行 -> 下方展开内容区，再点收起（替代子窗口）。"""
            _body = tk.Frame(parent, bg="#f2f5f8")
            _open = {"v": False}
            # ★ 2026-10-12【箭头符号修复】▾/▸ 是中文字体没有的字形，PIL 渲染成 ×；
            #   改用全角 ＋（收起）/ －（展开），所有中文字体都支持。
            _img_on = _pil_round(title + "  －", 300, 34, 10, "#ffffff", "#5f7ea5", True)[0]
            _img_off = _pil_round(title + "  ＋", 300, 34, 10, "#ffffff", "#5f7ea5", True)[0]

            def _refresh():
                try:
                    _fit(None)   # ★ 2026-10-12：展开/收起后同步内层宽度 + 滚动区
                    _cv.configure(scrollregion=_cv.bbox("all"))
                    # ★ 2026-10-12【展开自动定位】展开/收起后把滚动区定位到
                    #   该项附近，防止内容生成在视口外 -> 看起来"没内容"。
                    try:
                        _bb = _cv.bbox("all")
                        _rh = _bb[3] if _bb else 0
                        _vh = _cv.winfo_height()
                        if _rh > _vh:
                            _top = (_body.winfo_y() - 10) / float(_rh)
                            _cv.yview_moveto(max(0.0, min(1.0, _top)))
                    except Exception:
                        pass
                except Exception:
                    pass

            def _sync_body(_e=None):
                # ★ 2026-10-12【宽度跟随】父区块宽度一变，灰底展开区立即跟宽，
                #   内容 pack(anchor=center) 随之重新居中。
                try:
                    _body.configure(width=parent.winfo_width())
                except Exception:
                    pass
            parent.bind("<Configure>", _sync_body, add=True)

            def _toggle():
                """展开/收起。顶层包日志：任何异常都落盘，避免"点击无反应"。"""
                try:
                    _open["v"] = not _open["v"]
                    _b.configure(image=_img_on if _open["v"] else _img_off)
                    if _open["v"]:
                        if not _body.winfo_children():
                            try:
                                build(_body)
                            except Exception as _e:
                                # ★ 2026-10-12【展开诊断】异常既显示在展开区，也落盘
                                #   panel_err.log，便于定位"展开打不开"。
                                import traceback as _tb
                                try:
                                    _h = io.open(r"D:\FAIRY\logs\panel_err.log",
                                                 "a", encoding="utf-8")
                                    _h.write("EXPAND %s\n%s\n" % (
                                        title, _tb.format_exc()))
                                    _h.close()
                                except Exception:
                                    pass
                                tk.Label(_body, text="加载失败：" + str(_e)[:50],
                                         bg="#f2f5f8", fg="#a8625e",
                                         font=("SimHei", 11)).pack(pady=4)
                        # ★ 2026-10-12【空白兜底】build 后仍无任何子控件 ->
                        #   显示占位提示，保证展开区永远不会是一片空白。
                        if not _body.winfo_children():
                            tk.Label(_body, text="（此区域暂无内容）",
                                     bg="#f2f5f8", fg="#97a1ad",
                                     font=("SimHei", 11)).pack(pady=6)
                        # ★ 2026-10-12【展开空白修复】Tk pack 的请求高度在
                        #   build 后可能塌陷为 1px（子控件 reqh 正常但父 frame
                        #   不汇总）-> 强制按子控件请求高度总和撑开展开区。
                        try:
                            _body.update_idletasks()
                            _total = sum(
                                _c.winfo_reqheight()
                                for _c in _body.winfo_children()) + 40
                            _body.configure(height=_total)
                            try:
                                _body.configure(
                                    width=max(_body.winfo_width(),
                                              parent.winfo_width()))
                            except Exception:
                                pass
                        except Exception:
                            pass
                    else:
                        # ★ 2026-10-12【收起清空】仅收起时销毁内容；
                        #   修复：此前 else 误挂在诊断 try 上，导致每次展开
                        #   刚 build 完就被清空 -> 展开区永远空白。
                        for _c in _body.winfo_children():
                            _c.destroy()
                        # ★ 2026-10-12【收起高度复位】展开时曾显式撑高 _body，
                        #   收起时必须把高度重置，否则空白面板残留、收不回去。
                        try:
                            _body.configure(height=1)
                        except Exception:
                            pass
                    # ★ 2026-10-12【展开诊断】每次展开/收起记录控件数，便于定位
                    #   "展开没内容"到底是 build 没跑还是渲染不可见。
                    try:
                        _h = io.open(r"D:\FAIRY\logs\panel_err.log",
                                     "a", encoding="utf-8")
                        _h.write("EXPAND_OK %s open=%d children=%d bh=%d par=%s\n" % (
                            title, 1 if _open["v"] else 0,
                            len(_body.winfo_children()),
                            _body.winfo_height(), str(_body.master())[:50]))
                        _h.close()
                    except Exception:
                        pass
                    _refresh()
                except Exception as _e:
                    import traceback as _tb
                    try:
                        _h = io.open(r"D:\FAIRY\logs\panel_err.log",
                                     "a", encoding="utf-8")
                        _h.write("TOGGLE %s\n%s\n" % (title, _tb.format_exc()))
                        _h.close()
                    except Exception:
                        pass

            _b = tk.Button(parent, image=_img_off, command=_toggle, bd=0, relief="flat",
                           highlightthickness=0, bg="#ffffff", activebackground="#ffffff",
                           cursor="hand2")
            _b.image = _img_off
            _b.pack(pady=(3, 0))
            # ★ 2026-10-12【位置修复】内容区创建时就 pack 占位（紧跟标题按钮下方），
            #   展开/收起只控制内容填充与清空，位置不再跑到区块末尾。
            _body.pack(fill="x", pady=(2, 6))

        blocks = {}

        def _block(title, desc=""):
            f = tk.Frame(_inner, bg="#ffffff")
            _h = tk.Frame(f, bg="#ffffff")
            _h.pack(fill="x")
            tk.Frame(_h, bg="#5f7ea5", width=4, height=18).pack(side="left", padx=(8, 6))
            tk.Label(_h, text=title, bg="#ffffff", fg="#5f7ea5",
                     font=("SimHei", 17, "bold")).pack(side="left")
            tk.Frame(f, bg="#e8ecf2", height=1).pack(fill="x", pady=(4, 2))
            f.pack(fill="x")
            blocks[title] = f
            return f

        def _mk2(_parent, _maker, _toggle, _pady=(4, 2)):
            """★ 2026-10-12【两态按钮】点击切换状态并重建文字图片（始终显示当前状态）。"""
            _b = tk.Button(_parent, bd=0, relief="flat", highlightthickness=0,
                           bg="#ffffff", activebackground="#ffffff", cursor="hand2")

            def _upd():
                _img = _maker()
                _b.config(image=_img)
                _b.image = _img
            _b.config(command=lambda: (_toggle(), _upd()))
            _upd()
            _b.pack(pady=_pady)
            return _b

        # 2 AI 模型
        b2 = _block("AI 模型")
        _ai = self._ai_cfg()
        _be = _ai.get("backend") or "strata"
        _m = _ai.get("model") or ""
        _pt = _ai.get("port") or ""
        def _b_engine(f):
            _bg2 = "#f2f5f8"
            tk.Label(f, text="引擎类型", bg=_bg2, fg="#66727f",
                     font=("SimHei", 12, "bold")).pack(anchor="center", pady=(6, 2))
            _ev = tk.StringVar(value=_be)
            _PRESET_MODELS = {
                "strata": ["Qwen3.8-Flash-Next\u00b7UD-IQ4_XS",
                           "Qwen3.8-Flash-Next\u00b7Q4_K_M",
                           "Qwen3.8-Flash-Next\u00b7Q5_K_M",
                           "Qwen3.8-Flash-Next\u00b7Q6_K"],
                "ollama": ["qwen3-30b-a3b", "qwen3.8-27b", "qwen3.5-9b"],
                "llm": ["Qwen3-VL-8B-Q4_K_M", "Qwen3-VL-4B-Q4_K_M",
                        "Qwen2.5-VL-7B-Q4_K_M"],
            }
            _om_box = {"w": None}
            _mv = tk.StringVar()

            def _rebuild_pool():
                """★ 2026-10-12【模型列表兜底】选引擎即重建模型下拉：
                先渲染【常用模型全集 + 本机磁盘 gguf】（任何引擎/游戏模式都有真实选项），
                后台线程再探测正在运行的引擎模型并刷新 —— 不再出现"展开没内容"，
                也不卡主线程（探测/扫盘都在后台）。"""
                try:
                    if _om_box["w"] is not None:
                        try:
                            _om_box["w"].destroy()
                        except Exception:
                            pass
                    _be2 = _ev.get()
                    # ① 常用模型全集兜底：所有引擎预设合并，off/游戏模式也有项
                    _ALL = []
                    for _v in _PRESET_MODELS.values():
                        for _x in _v:
                            if _x not in _ALL:
                                _ALL.append(_x)
                    _pool = list(_ALL)
                    # ② 本机磁盘 gguf（扫描可能慢，放后台，先不阻塞渲染）
                    def _bg():
                        _extra = []
                        try:
                            import capability as _cap
                            _mo = _cap.scan_models() or {}
                            for _f in (_mo.get("gguf_files") or [])[:30]:
                                _nm = _f.get("name") or ""
                                if _nm:
                                    _extra.append(_nm)
                        except Exception:
                            pass
                        try:
                            _probed = self._ai_probe_models()
                            _have = [x for _, x in _probed]
                        except Exception:
                            _have = []
                        if not _extra and not _have:
                            return
                        def _upd():
                            try:
                                # ★ 2026-10-12【销毁防御】后台探测完成时展开区
                                #   可能已被收起（OptionMenu 已销毁）-> 静默退出，
                                #   绝不抛 TclError 打断主循环。
                                if _om_box["w"] is None:
                                    return
                                try:
                                    if not _om_box["w"].winfo_exists():
                                        return
                                except Exception:
                                    return
                                _pool2 = list(_pool)
                                for _x in (_extra + _have):
                                    if _x not in _pool2:
                                        _pool2.insert(0, _x)
                                _mv2 = tk.StringVar(value=_mv.get())
                                _om_box["w"].destroy()
                                _om_box["w"] = tk.OptionMenu(f, _mv2, *_pool2)
                                _om_box["w"].configure(
                                    bg="#ffffff", activebackground="#e4edf6",
                                    font=("SimHei", 11), highlightthickness=0)
                                _om_box["w"].pack(anchor="center", pady=3)
                                _mv.set(_mv2.get())
                            except Exception:
                                pass
                        try:
                            self.after(0, _upd)
                        except Exception:
                            pass
                    import threading as _th
                    _th.Thread(target=_bg, daemon=True).start()
                    # ③ 先用兜底池立即渲染（保证展开就有内容）
                    if _m and _m not in _pool:
                        _pool.insert(0, _m)
                    _pool = _pool or ["（未探测到模型）"]
                    _mv.set(_m if _m in _pool else _pool[0])
                    _om_box["w"] = tk.OptionMenu(f, _mv, *_pool)
                    _om_box["w"].configure(bg="#ffffff", activebackground="#e4edf6",
                                           font=("SimHei", 11), highlightthickness=0)
                    _om_box["w"].pack(anchor="center", pady=3)
                except Exception:
                    pass

            for _nm, _code in (("Strata（本地运算）", "strata"),
                               ("Ollama", "ollama"),
                               ("视觉 llama.cpp", "llm")):
                tk.Radiobutton(f, text=_nm, value=_code, variable=_ev,
                               command=lambda: (_pv.set(
                                   {"strata": "8080", "ollama": "11434",
                                    "llm": "8085"}.get(_ev.get(), "8080")),
                                   _rebuild_pool()),
                               bg=_bg2, fg="#46536a", activebackground=_bg2,
                               selectcolor=_bg2, font=("SimHei", 12)).pack(anchor="center", pady=1)
            tk.Label(f, text="模型（下拉：本机探测到的 + 常用预设）", bg=_bg2, fg="#66727f",
                     font=("SimHei", 12, "bold")).pack(anchor="center", pady=(6, 2))
            # ★ 2026-10-12【下拉修复】探测结果 + 常用预设兜底，引擎没跑也有下拉可选；
            #   原 _ai_probe_models 返回值解包方式错误导致展开区"加载失败"。
            _rebuild_pool()
            # ★ 2026-10-12【手动输入】引擎没跑时探测不到模型 -> 可直接手填模型名
            tk.Label(f, text="或手动输入模型名（引擎没跑时用这个）", bg=_bg2, fg="#97a1ad",
                     font=("SimHei", 10)).pack(anchor="center", pady=(4, 0))
            _me = tk.StringVar(value="")
            tk.Entry(f, textvariable=_me, width=24, font=("SimHei", 12)
                     ).pack(anchor="center", pady=3)
            tk.Label(f, text="端口（保留默认即可，可自行修改）", bg=_bg2, fg="#66727f",
                     font=("SimHei", 12, "bold")).pack(anchor="center", pady=(6, 2))
            _pv = tk.StringVar(value=_pt or {"strata": "8080", "ollama": "11434",
                                             "llm": "8085"}.get(_be, "8080"))
            tk.Entry(f, textvariable=_pv, width=12, font=("SimHei", 12)
                     ).pack(anchor="center", pady=3)

            def _save_eng():
                _b_ = _ev.get()
                _m_ = (_me.get().strip() or _mv.get())
                if not _m_ or _m_.startswith("（"):
                    _m_ = _m
                _p_ = _pv.get().strip() or {"strata": "8080", "ollama": "11434",
                                            "llm": "8085"}.get(_b_, "8080")
                self._save_ai_cfg({"backend": _b_, "model": _m_, "port": _p_})
                self._bubble("本地引擎已保存：%s / %s / 端口 %s"
                             % (_b_, _m_ or "默认", _p_), 4)
                try:
                    self.say("本地引擎设置已保存", "ok")
                except Exception:
                    pass
            _r0 = tk.Frame(f, bg=_bg2)
            _r0.pack(anchor="center", pady=4)
            self._dlg_btn(_r0, "保存", _save_eng)
        _exp_row(b2, "引擎 / 模型 / 端口", _b_engine)

        def _b_preset(f):
            _bg2 = "#f2f5f8"
            _PRESETS = [
                ("日常全能", "strata", "8080",
                 ["Qwen3.8-Flash-Next\u00b7UD-IQ4_XS",
                  "Qwen3.8-Flash-Next\u00b7Q4_K_M",
                  "Qwen3.8-Flash-Next\u00b7Q5_K_M",
                  "Qwen3.8-Flash-Next\u00b7Q6_K"],
                 "Strata 主力：64G 内存甜点位，写代码/日常全能（推荐）"),
                ("高精度", "llm", "8085",
                 ["qwen3.5-9b"],
                 "更强，长推理/疑难问题（本地 9B 引擎，替代 Ollama）"),
                ("视觉", "vl", "8086",
                 ["qwen3-vl-8b"],
                 "视觉模型，可看图可对话（Qwen3-VL-8B）"),
                ("游戏模式", "off", "", [],
                 "释放全部引擎与模型，显存全给游戏；语音/轻功能照常"),
            ]
            _cur_p = (self._ai_cfg().get("preset") or "")
            _ai_now = self._ai_cfg()
            _vars = {}
            _btns = {}
            _imgs = {}

            def _refresh_all():
                # ★ 2026-10-12【实时两态】每次刷新实时读配置，点完立即更新 ✓，
                #   不再等关面板重开。
                _cur_now = (self._ai_cfg().get("preset") or "")
                for _i, (_nm, _eng, _pt2, _models, _desc) in enumerate(_PRESETS):
                    _sel = (_cur_now == _nm) or (_cur_now == "game" and _nm == "游戏模式")
                    _ck = "✓ " if _sel else "  "
                    _imgs[_i] = _pil_round(_ck + _nm, 260, 32, 10, "#ffffff",
                                           "#5f7ea5", True)[0]
                    _btns[_i].config(image=_imgs[_i])
                    _btns[_i].image = _imgs[_i]

            def _apply(idx):
                try:
                    # ★ 2026-10-12【修复】_apply_impl 是本展开区的嵌套函数，
                    #   不是 FairyBall 类方法 —— 之前写成 self._apply_impl 必报
                    #   AttributeError（"'tkinter.tkapp' object has no attribute"）
                    #   ，导致点组合切换永远失败。
                    _apply_impl(idx)
                except Exception as _e:
                    import traceback as _tb
                    try:
                        _h = io.open(r"D:\FAIRY\logs\panel_err.log",
                                     "a", encoding="utf-8")
                        _h.write("APPLY\n%s\n" % _tb.format_exc())
                        _h.close()
                    except Exception:
                        pass
                    try:
                        self._bubble("组合切换失败：" + str(_e)[:50], 5)
                    except Exception:
                        pass

            def _apply_impl(idx):
                _nm, _eng, _pt2, _models, _desc = _PRESETS[idx]
                if _eng == "off":
                    self._engine_release()
                    self._save_ai_cfg({"preset": "game"})
                    self._bubble("游戏模式：全部模型已释放，显存已空出，尽情玩吧", 5)
                    try:
                        self.say("游戏模式：模型已全部释放，显存已空出，可以开始游戏", "ok")
                    except Exception:
                        pass
                    return
                _mdl = _vars.get(idx).get() if _vars.get(idx) else _models[0]
                if not _mdl or _mdl.startswith("（"):
                    _mdl = _models[0]
                self._save_ai_cfg({"backend": _eng, "model": _mdl,
                                   "port": _pt2, "preset": _nm})
                # ★ 2026-10-12【组合退出清理】先停旧组合的引擎/模型（后台线程），
                #   再起新组合引擎 —— 切走即释放，不用的模型不再占内存/显存。
                try:
                    threading.Thread(target=self._cleanup_engines,
                                     args=(_eng,), daemon=True).start()
                except Exception:
                    pass
                # ★ 2026-10-12：切换时立即气泡+语音反馈，并弹加载进度
                self._bubble("正在切换组合：%s（%s）" % (_nm, _mdl), 4)
                try:
                    self.say("正在切换组合：%s" % _nm, "doing")
                except Exception:
                    pass
                self._preset_progress(_nm, _pt2)

                def _ensure():
                    try:
                        if _eng == "strata":
                            try:
                                if (self._strata_probe().get("state") or "") != "running":
                                    self._strata_start()
                            except Exception:
                                pass
                        elif _eng == "llm":
                            # ★ 2026-10-17【替代 Ollama】本地文本引擎 llama-server 8085
                            import socket as _sk3
                            import subprocess as _sp3
                            _s3 = _sk3.socket(); _s3.settimeout(0.4)
                            try:
                                _s3.connect(("127.0.0.1", 8085)); _s3.close()
                                return
                            except Exception:
                                pass
                            try:
                                _p3 = os.path.join(
                                    os.path.dirname(os.path.abspath(__file__)),
                                    "fairy_llm.py")
                                _sp3.Popen([sys.executable, _p3],
                                           creationflags=getattr(_sp3, "CREATE_NO_WINDOW", 0),
                                           stdout=_sp3.DEVNULL, stderr=_sp3.DEVNULL)
                                self._bubble("已拉起本地文本引擎（8085），请稍候", 4)
                            except Exception:
                                pass
                        elif _eng == "ollama":
                            import socket as _sk
                            _s = _sk.socket()
                            _s.settimeout(0.4)
                            try:
                                _s.connect(("127.0.0.1", 11434))
                                _s.close()
                                return
                            except Exception:
                                pass
                            try:
                                import subprocess as _sp2
                                _exe = os.path.expandvars(
                                    r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
                                if os.path.exists(_exe):
                                    _sp2.Popen([_exe, "serve"],
                                                creationflags=getattr(_sp2, "CREATE_NO_WINDOW", 0),
                                                stdout=_sp2.DEVNULL, stderr=_sp2.DEVNULL)
                                    self._bubble("已拉起 Ollama（11434），请稍候", 4)
                            except Exception:
                                pass
                    except Exception:
                        pass
                threading.Thread(target=_ensure, daemon=True).start()
                self._bubble("已切换组合：%s（%s）" % (_nm, _mdl), 5)
                try:
                    self.say("已切换组合：%s" % _nm, "ok")
                except Exception:
                    pass
                _refresh_all()
                # ★ 2026-10-15【统一使用】所有引擎组合统一打开 8090 统一对话页
                #   （对话/监控/关于，中文风格，代理转发到当前引擎，不再分页）。
                try:
                    if _eng in ("strata", "ollama", "llm"):
                        self._open_chat_web(_eng, _pt2, _mdl)
                except Exception:
                    pass

            tk.Label(f, text="点组合切换（右侧可先选该组合的模型）",
                     bg=_bg2, fg="#66727f", font=("SimHei", 11)
                     ).pack(anchor="center", pady=(2, 4))
            for _i, (_nm, _eng, _pt2, _models, _desc) in enumerate(_PRESETS):
                _rowf = tk.Frame(f, bg=_bg2)
                _rowf.pack(anchor="center", pady=1)
                _sel = (_cur_p == _nm) or (_cur_p == "game" and _nm == "游戏模式")
                _ck = "✓ " if _sel else "  "
                _imgs[_i] = _pil_round(_ck + _nm, 250, 32, 10, "#ffffff",
                                       "#5f7ea5", True)[0]
                _b = tk.Button(_rowf, image=_imgs[_i], bd=0, relief="flat",
                               highlightthickness=0, bg=_bg2, activebackground=_bg2,
                               cursor="hand2", command=lambda i=_i: _apply(i))
                _b.image = _imgs[_i]
                _b.pack(side="left", padx=2)
                _btns[_i] = _b
                if _models:
                    _cur_mdl = _ai_now.get("model") or _models[0]
                    if _cur_mdl not in _models:
                        _cur_mdl = _models[0]
                    _vars[_i] = tk.StringVar(value=_cur_mdl)
                    tk.OptionMenu(_rowf, _vars[_i], *_models).configure(
                        bg="#ffffff", activebackground="#e4edf6",
                        font=("SimHei", 10), highlightthickness=0)
                tk.Label(f, text="　" + _desc, bg=_bg2, fg="#97a1ad",
                         font=("SimHei", 10)).pack(anchor="center", pady=(0, 2))

        _exp_row(b2, "组合选择", _b_preset)
        # ★ 2026-10-12【DSH 可启动】"DSH 云端"按钮：切到 dsh 大脑模式，
        #   若 DeepSeek Harness（19387）没在跑会自动拉起（见 _set_pref -> _ensure_dsh）
        _row(b2, "网络配置", [(("✓ DSH 云端" if _be == "dsh" else "DSH 云端"),
                            lambda: self._set_pref("dsh"))])

        def _b_online(f):
            """★ 2026-10-12【在线引擎精简】只保留自定义接口（OpenAI 兼容），
            模型栏带常用预设下拉（自动填模型 + 接口地址）。DSH 云端内核入口保留在上方。"""
            _bg2 = "#f2f5f8"
            _ai = self._ai_cfg()
            _PRESETS = [
                ("DeepSeek（deepseek-chat）", "deepseek-chat",
                 "https://api.deepseek.com/v1"),
                ("DeepSeek 推理（deepseek-reasoner）", "deepseek-reasoner",
                 "https://api.deepseek.com/v1"),
                ("智谱 GLM（glm-4-flash 免费）", "glm-4-flash",
                 "https://open.bigmodel.cn/api/paas/v4"),
                ("通义千问（qwen-plus）", "qwen-plus",
                 "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                ("Kimi（moonshot-v1-8k）", "moonshot-v1-8k",
                 "https://api.moonshot.cn/v1"),
                ("硅基流动（Qwen2.5-7B）", "Qwen/Qwen2.5-7B-Instruct",
                 "https://api.siliconflow.cn/v1"),
                ("OpenAI（gpt-4o-mini）", "gpt-4o-mini",
                 "https://api.openai.com/v1"),
            ]
            _m_var = tk.StringVar(value=_ai.get("model") or "deepseek-chat")
            _k_var = tk.StringVar(value=_ai.get("api_key") or "")
            _u_var = tk.StringVar(value=_ai.get("base_url")
                                  or "https://api.deepseek.com/v1")

            tk.Label(f, text="在线引擎：自定义接口（OpenAI 兼容）",
                     bg=_bg2, fg="#66727f", font=("SimHei", 10, "bold")
                     ).pack(anchor="center", pady=(2, 2))
            tk.Label(f, text="常用服务（选中自动填模型与接口地址）",
                     bg=_bg2, fg="#97a1ad", font=("SimHei", 10)
                     ).pack(anchor="center")
            _cur_nm = _PRESETS[0][0]
            for _nm, _m, _u in _PRESETS:
                if _m_var.get() == _m:
                    _cur_nm = _nm
                    break
            _pv2 = tk.StringVar(value=_cur_nm)

            def _on_pre(_sel):
                for _nm, _m, _u in _PRESETS:
                    if _sel == _nm:
                        _m_var.set(_m)
                        _u_var.set(_u)
                        break
            tk.OptionMenu(f, _pv2, *[p[0] for p in _PRESETS],
                          command=_on_pre).configure(
                bg="#ffffff", activebackground="#e4edf6", font=("SimHei", 11),
                highlightthickness=0)
            tk.Label(f, text="模型（可改）", bg=_bg2, fg="#66727f",
                     font=("SimHei", 10)).pack(anchor="center", pady=(6, 1))
            tk.Entry(f, textvariable=_m_var, font=("SimHei", 11),
                     width=30).pack(anchor="center")
            tk.Label(f, text="API Key", bg=_bg2, fg="#66727f",
                     font=("SimHei", 10)).pack(anchor="center", pady=(4, 1))
            tk.Entry(f, textvariable=_k_var, font=("SimHei", 11),
                     width=30).pack(anchor="center")
            tk.Label(f, text="接口地址（选预设自动填，可改）", bg=_bg2, fg="#66727f",
                     font=("SimHei", 10)).pack(anchor="center", pady=(4, 1))
            tk.Entry(f, textvariable=_u_var, font=("SimHei", 11),
                     width=30).pack(anchor="center")

            def _ok():
                _m = _m_var.get().strip()
                _k = _k_var.get().strip()
                _u = _u_var.get().strip()
                self._save_ai_cfg({"backend": "custom", "model": _m,
                                   "api_key": _k, "base_url": _u})
                self._bubble("自定义接口已保存" + ("（%s）" % _m if _m else ""), 3)
            _r = tk.Frame(f, bg=_bg2)
            _r.pack(anchor="center", pady=3)
            self._dlg_btn(_r, "保存", _ok)
        _exp_row(b2, "自定义接口 / 模型 / Key", _b_online)

        # 1 声音
        b1 = _block("声音")

        def _b_voice(f):
            _bg2 = "#f2f5f8"
            try:
                import fairy_voice as _fv2
                _vs2 = _fv2.list_voices() or ["fairy.wav"]
                _cur2 = _fv2.current()
            except Exception:
                _vs2, _cur2 = ["fairy.wav"], ""
            for _vn in _vs2:
                _img = _pil_round(("✓ " if _vn == _cur2 else "  ") + _vn, 280, 34, 10,
                                  "#ffffff", "#46536a", True, "#dfe3ea")[0]
                _b = tk.Button(f, image=_img, bd=0, relief="flat", highlightthickness=0,
                               bg=_bg2, activebackground=_bg2, cursor="hand2",
                               command=(lambda n=_vn: (self._voice_set(n),
                                                       self._bubble("音色已切换：" + n, 3))))
                _b.image = _img
                _b.pack(pady=2)

        def _b_wake(f):
            _bg2 = "#f2f5f8"
            _old = ""
            try:
                with open(fairy_root.CONFIG, encoding="utf-8") as _f:
                    _old = "、".join((json.load(_f).get("ear") or {}).get("wakewords") or [])
            except Exception:
                pass
            tk.Label(f, text="英文词条，如 alice；多个用逗号分隔（保存后重启 Fairy 生效）",
                     bg=_bg2, fg="#66727f", font=("SimHei", 10)).pack(anchor="center", pady=(4, 2))
            _inp = tk.Entry(f, width=28, font=("SimHei", 12))
            _inp.insert(0, _old)
            _inp.pack(anchor="center", pady=2)

            def _ok():
                _words = [w.strip() for w in _inp.get().replace("，", ",").split(",")
                          if w.strip()]
                try:
                    with open(fairy_root.CONFIG, encoding="utf-8") as _f:
                        _j = json.load(_f)
                    _j.setdefault("ear", {})["wakewords"] = _words
                    with open(fairy_root.CONFIG, "w", encoding="utf-8") as _f:
                        json.dump(_j, _f, ensure_ascii=False, indent=2)
                    self._bubble("已保存唤醒词（重启 Fairy 生效）", 4)
                except Exception as _e:
                    self._bubble("保存失败：" + str(_e)[:50], 4)
            _r = tk.Frame(f, bg=_bg2)
            _r.pack(anchor="center", pady=3)
            self._dlg_btn(_r, "保存", _ok)

        _exp_row(b1, "音色", _b_voice)
        _exp_row(b1, "唤醒词", _b_wake)
        # 听歌识曲两态（★ 2026-10-12：点击后重建图片刷新文字）
        def _sr_maker():
            _on = getattr(self, "_songrec_on", True)
            return _pil_round(("✓ 听歌识曲（运行中）" if _on else "听歌识曲（已停止）"),
                              300, 34, 10, "#ffffff", "#46536a", True)[0]
        _mk2(b1, _sr_maker, self._songrec_toggle)
        # 免打扰两态（★ 2026-10-12：点击后重建图片刷新文字）
        def _dnd_maker():
            _on = getattr(self, "_dnd", False)
            return _pil_round(("✓ 免打扰（已开启）" if _on else "免打扰（已关闭）"),
                              300, 34, 10, "#ffffff", "#46536a", True)[0]
        _mk2(b1, _dnd_maker, self._dnd_toggle, (8, 2))
        # 天气预警两态（★ 2026-10-12：点击后重建图片刷新文字）
        def _wa_maker():
            _wa = True
            try:
                with open(fairy_root.CONFIG, encoding="utf-8") as _f:
                    _wa = bool((json.load(_f).get("briefing") or {}).get("weather_alert", True))
            except Exception:
                pass
            return _pil_round(("✓ 天气预警（已开启）" if _wa else "天气预警（已关闭）"),
                              300, 34, 10, "#ffffff", "#46536a", True)[0]
        _mk2(b1, _wa_maker, self._alert_toggle)

        # 3 视觉识别
        b3 = _block("视觉识别")
        _row(b3, None, [("截图标注", self._screenshot_anno),
                        ("识别文字 OCR", self._vision_ocr),
                        ("识别物体 YOLO", self._vision_detect),
                        ("屏幕取色", self._color_pick)])

        # 4 网络与设备
        b4 = _block("网络与设备")
        _row(b4, None, [("网络诊断", self._net_diag),
                        ("扫描新设备", self._iot_scan)])

        def _b_iot(f):
            """★ 2026-10-12【设备列表·内嵌版】滚动列表（已标记/未标记）+
            未标记行尾「标记」按钮 + 底部起名输入框，不再弹独立窗口。"""
            _bg2 = "#f2f5f8"
            # ★ 2026-10-12【居中】设备列表整体居中：包一层容器，_cv2+滚动条
            #   在容器内左右排布，容器本身锚定中间，不再铺满整个展开区。
            _wrap2 = tk.Frame(f, bg=_bg2)
            _wrap2.pack(anchor="center", pady=2)
            _cv2 = tk.Canvas(_wrap2, bg=_bg2, highlightthickness=0, height=150,
                             width=640)
            _sb2 = tk.Scrollbar(_wrap2, orient="vertical", command=_cv2.yview)
            _cv2.configure(yscrollcommand=_sb2.set)
            _cv2.pack(side="left", fill="y")
            _sb2.pack(side="right", fill="y")
            # ★ 2026-10-12【滚动修复】列表区域滚轮直接滚动（含 Canvas 空白处）；
            #   滚到边界时透传给外层面板，避免"卡住滚不动"。
            def _wheel_cv2(_ev):
                try:
                    _d = int(-1 * (_ev.delta / 120))
                    _top, _bot = _cv2.yview()
                    if (_d > 0 and _top > 0.0) or (_d < 0 and _bot < 1.0):
                        _cv2.yview_scroll(_d, "units")
                    else:
                        try:
                            _cv.yview_scroll(_d, "units")
                        except Exception:
                            pass
                except Exception:
                    pass
            _cv2.bind("<MouseWheel>", _wheel_cv2)
            _inner2 = tk.Frame(_cv2, bg=_bg2)
            _wid2 = _cv2.create_window((0, 0), window=_inner2, anchor="nw")

            def _fit2(_e=None):
                try:
                    _cv2.itemconfigure(_wid2, width=max(120, _cv2.winfo_width() - 6))
                    _cv2.configure(scrollregion=_cv2.bbox("all"))
                except Exception:
                    pass
            _cv2.bind("<Configure>", _fit2)

            def _bind_w2(_w):
                _w.bind("<MouseWheel>", _wheel_cv2)
                for _c in _w.winfo_children():
                    _bind_w2(_c)

            _KCN = {"lan": "局域网", "bt": "蓝牙", "mdns": "组播/投屏",
                    "serial": "串口", "nearlink": "星闪", "nearby": "近距离"}
            _devs = {"list": []}

            def _fmt(x):
                _n = x.get("tag") or x.get("name") or x.get("id") or "?"
                _t = x.get("type") or ""
                _v = x.get("vendor") or ""
                _k = _KCN.get(str(x.get("kind") or x.get("adapter") or ""), "")
                _ip = str(x.get("ip") or "").strip()
                _mac = str(x.get("id") or "")
                for _p in ("ble:", "lan:", "nearlink:", "nearby:", "serial:"):
                    _mac = _mac.replace(_p, "")
                _mac = (_mac[-6:] if len(_mac) > 6 else _mac)
                _bits = [b for b in (_v, _k, _t, _ip, _mac) if b and b != "未知设备"]
                _s = " · ".join(_bits)
                return ("%s（%s）" % (_n, _s)) if _s else str(_n)

            def _paint():
                # ★ 2026-10-12【销毁防御】后台扫描完成回调时，展开区可能已被
                #   用户收起（_inner2 已销毁）-> 直接静默退出，绝不抛 TclError
                #   打断 Tk 事件循环（否则面板/球全"没反应"）。
                try:
                    if not _inner2.winfo_exists():
                        return
                except Exception:
                    return
                for _c in _inner2.winfo_children():
                    _c.destroy()
                _devs2 = _devs["list"]
                if not _devs2:
                    tk.Label(_inner2, text="还没有已连接设备（先去「扫描新设备」）",
                             bg=_bg2, fg="#66727f",
                             font=("SimHei", 11)).pack(pady=8)
                    _bind_w2(_inner2)
                    return
                _tagged = [x for x in _devs2 if (x.get("tag") or "").strip()]
                _untagged = [x for x in _devs2 if not (x.get("tag") or "").strip()]
                for _title, _items, _can_tag in (
                        ("已标记", _tagged, False), ("未标记", _untagged, True)):
                    if not _items:
                        continue
                    tk.Label(_inner2, text="%s（%d）" % (_title, len(_items)),
                             bg=_bg2, fg="#5f7ea5",
                             font=("SimHei", 11, "bold")).pack(anchor="w", pady=(6, 2))
                    for _x in _items:
                        _rowf = tk.Frame(_inner2, bg=_bg2)
                        _rowf.pack(fill="x", pady=1)
                        tk.Label(_rowf, text=_fmt(_x), bg=_bg2, fg="#46536a",
                                 font=("SimHei", 10), width=44,
                                 anchor="w").pack(side="left", padx=2)
                        if _can_tag:
                            _img = _pil_round("标记", 58, 30, 10, "#ffffff",
                                              "#5f7ea5", True, "#dfe3ea")[0]
                            _b = tk.Button(_rowf, image=_img, bd=0, relief="flat",
                                           highlightthickness=0, bg=_bg2,
                                           activebackground=_bg2, cursor="hand2",
                                           command=lambda x=_x: _mark(x))
                            _b.image = _img
                            _b.pack(side="right", padx=2)
                _bind_w2(_inner2)
                _fit2()

            _tag_pending = {"id": None}
            _hint = tk.Label(f, text="", bg=_bg2, fg="#66727f",
                             font=("SimHei", 10))
            _hint.pack(anchor="center", pady=(6, 0))
            _te = tk.Entry(f, width=24, font=("SimHei", 11))
            _te.pack(anchor="center", pady=2)

            def _mark(_x):
                _tag_pending["id"] = str(_x.get("id") or "")
                _hint.config(text="给「%s」起名：" % (_x.get("name") or _x.get("id")))
                _te.focus_force()

            def _save_tag():
                _id = _tag_pending["id"]
                _nm = _te.get().strip()
                if not _id or not _nm:
                    self._bubble("先点未标记设备的「标记」再输入名字", 3)
                    return
                try:
                    _r2 = _rnw([sys.executable,
                                   os.path.join(self._tools_dir(), "fairy_iot.py"),
                                   "--tag", _id, _nm, "--json"],
                               capture_output=True, timeout=30, text=True,
                               encoding="utf-8")
                    _d2 = json.loads(_r2.stdout or "{}")
                    self._bubble(("已标记：%s" % _nm) if _d2.get("ok")
                                 else ("标记失败：" + str(_d2.get("msg") or "")[:50]), 4)
                    _hint.config(text="")
                    _te.delete(0, "end")
                    _reload()
                except Exception as _e2:
                    self._bubble("标记失败：" + str(_e2)[:60], 4)
            _r5 = tk.Frame(f, bg=_bg2)
            _r5.pack(anchor="center", pady=3)
            self._dlg_btn(_r5, "保存标记", _save_tag)

            def _reload():
                import threading as _th2

                def _work():
                    try:
                        _rr = _rnw([sys.executable,
                                       os.path.join(self._tools_dir(), "fairy_iot.py"),
                                       "--devices", "--json"], capture_output=True,
                                      timeout=30, text=True, encoding="utf-8", errors="replace")
                        _dd = json.loads(_rr.stdout or "{}")
                        _devs["list"] = _dd.get("devices") or []
                    except Exception:
                        _devs["list"] = []
                    try:
                        f.after(0, _paint)
                    except Exception:
                        pass
                _th2.Thread(target=_work, daemon=True).start()
            _reload()
        _exp_row(b4, "设备列表（分类 / 标记）", _b_iot)

        # 5 设置
        b5 = _block("设置")

        def _b_pos(f):
            """★ 2026-10-12【位置·精简】只保留自定义城市输入：请输入城市
            + 保存 / 取消（点选城市列表已删除）。"""
            _bg2 = "#f2f5f8"
            tk.Label(f, text="请输入城市", bg=_bg2, fg="#66727f",
                     font=("SimHei", 11)).pack(anchor="center", pady=(4, 1))
            _cv2 = tk.StringVar(value=self.city or "")
            tk.Entry(f, textvariable=_cv2, width=22, font=("SimHei", 12)
                     ).pack(anchor="center", pady=3)

            def _save_pos():
                _c = _cv2.get().strip()
                if _c:
                    self._set_city(_c)
                    self._bubble("位置已设为 %s" % _c, 3)
                else:
                    self._bubble("请输入城市名", 3)

            def _cancel_pos():
                _cv2.set("")
            _r2 = tk.Frame(f, bg=_bg2)
            _r2.pack(anchor="center", pady=3)
            self._dlg_btn(_r2, "保存", _save_pos)
            self._dlg_btn(_r2, "取消", _cancel_pos)

        def _b_perms(f):
            _bg2 = "#f2f5f8"
            try:
                import fairy_perms as _fp2
                _au2 = _fp2.authorized()
            except Exception:
                _au2 = []
            _perms_list = [("麦克风", "mic"), ("摄像头", "camera"), ("位置", "location"),
                           ("剪贴板", "clipboard"), ("截图", "capture"),
                           ("设备连接", "iot"), ("网络", "network"), ("文件读写", "files")]
            _vars = {}
            for _nm, _pid in _perms_list:
                _v = tk.BooleanVar(value=(_pid in _au2 or "all" in _au2))
                _vars[_pid] = _v
                tk.Checkbutton(f, text=_nm, variable=_v, anchor="w", bg=_bg2,
                               fg="#46536a", activebackground=_bg2, selectcolor=_bg2,
                               font=("SimHei", 12)).pack(anchor="center", pady=1)

            def _save_perms():
                try:
                    import fairy_perms as _fp3
                except Exception:
                    return
                _on = [p for p, v in _vars.items() if v.get()]
                _fp3.set_perms(_on)
                self._bubble("已保存 %d 项权限" % len(_on), 3)

            def _all_perms():
                for _v in _vars.values():
                    _v.set(True)
                try:
                    import fairy_perms as _fp4
                    _fp4.set_perms(["all"])
                except Exception:
                    pass
                self._bubble("已全部授权", 3)
            _r3 = tk.Frame(f, bg=_bg2)
            _r3.pack(anchor="center", pady=3)
            self._dlg_btn(_r3, "保存", _save_perms)
            self._dlg_btn(_r3, "全部授权", _all_perms)

        def _b_night(f):
            _bg2 = "#f2f5f8"
            _cur = {"night": True, "start": "22:00", "end": "07:00"}
            try:
                with open(fairy_root.CONFIG, encoding="utf-8") as _f:
                    _cur.update((json.load(_f).get("dnd") or {}))
            except Exception:
                pass
            _on2 = tk.BooleanVar(value=bool(_cur.get("night", True)))
            tk.Checkbutton(f, text="开启夜间模式（到点自动静音播报）", variable=_on2,
                           anchor="w", bg=_bg2, fg="#46536a", activebackground=_bg2,
                           selectcolor=_bg2, font=("SimHei", 12)).pack(anchor="center", pady=3)
            tk.Label(f, text="开始时间（如 22:00）", bg=_bg2, fg="#66727f",
                     font=("SimHei", 11)).pack(anchor="center")
            _st2 = tk.StringVar(value=_cur.get("start", "22:00"))
            tk.Entry(f, textvariable=_st2, width=12, font=("SimHei", 12)
                     ).pack(anchor="center", pady=2)
            tk.Label(f, text="结束时间（如 07:00）", bg=_bg2, fg="#66727f",
                     font=("SimHei", 11)).pack(anchor="center")
            _en2 = tk.StringVar(value=_cur.get("end", "07:00"))
            tk.Entry(f, textvariable=_en2, width=12, font=("SimHei", 12)
                     ).pack(anchor="center", pady=2)

            def _save_night():
                try:
                    with open(fairy_root.CONFIG, encoding="utf-8") as _f:
                        _j = json.load(_f)
                    (_j.setdefault("dnd", {}))["night"] = bool(_on2.get())
                    (_j.setdefault("dnd", {}))["start"] = _st2.get().strip() or "22:00"
                    (_j.setdefault("dnd", {}))["end"] = _en2.get().strip() or "07:00"
                    with open(fairy_root.CONFIG, "w", encoding="utf-8") as _f:
                        json.dump(_j, _f, ensure_ascii=False, indent=2)
                    self._bubble("夜间模式已保存", 3)
                except Exception as _e:
                    self._bubble("保存失败：" + str(_e)[:50], 4)
            _r4 = tk.Frame(f, bg=_bg2)
            _r4.pack(anchor="center", pady=3)
            self._dlg_btn(_r4, "保存", _save_night)

        def _b_clip(f):
            _bg2 = "#f2f5f8"
            try:
                sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                import fairy_clip as _fc2
                _items2 = _fc2.list_items()
            except Exception:
                _items2 = []
            if not _items2:
                tk.Label(f, text="还没有剪贴板历史（复制过内容后自动记录）",
                         bg=_bg2, fg="#97a1ad", font=("SimHei", 11)).pack(anchor="center", pady=4)
                return
            _box = tk.Listbox(f, width=52, height=6, font=("SimHei", 11),
                              bg="#ffffff", fg="#46536a", relief="flat",
                              highlightthickness=1, highlightbackground="#d5d9e3")
            _box.pack(anchor="center", pady=3)
            for i, x in enumerate(_items2[:15]):
                _box.insert("end", "[%d] %s %s" % (i, x["time"], x["text"][:50]))
            _r1 = tk.Frame(f, bg=_bg2)
            _r1.pack(anchor="center", pady=2)
            tk.Label(_r1, text="输入编号复制：", bg=_bg2, fg="#66727f",
                     font=("SimHei", 11)).pack(side="left")
            _idx = tk.Entry(_r1, width=6, font=("SimHei", 12))
            _idx.pack(side="left", padx=6)

            def _ok2():
                _n = _idx.get().strip()
                if not _n.isdigit():
                    self._bubble("输入数字编号", 3)
                    return
                n = int(_n)
                t = _fc2.get(n)
                if t is None:
                    self._bubble("没有这条（编号 0-%d）" % (len(_items2) - 1), 4)
                    return
                try:
                    _rnw(["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value $input"],
                         input=t, text=True, capture_output=True, timeout=10, encoding="utf-8", errors="replace")
                    self._bubble("已复制：" + t[:120], 5)
                except Exception as _e2:
                    self._bubble("复制失败：" + str(_e2)[:50], 4)
            self._dlg_btn(_r1, "复制", _ok2)

        def _b_remind(f):
            _bg2 = "#f2f5f8"
            tk.Label(f, text="多久后提醒（分钟）？", bg=_bg2, fg="#66727f",
                     font=("SimHei", 11)).pack(anchor="center", pady=(4, 2))
            _mins = tk.Entry(f, width=10, font=("SimHei", 12))
            _mins.insert(0, "10")
            _mins.pack(anchor="center", pady=2)
            tk.Label(f, text="提醒内容", bg=_bg2, fg="#66727f",
                     font=("SimHei", 11)).pack(anchor="center", pady=(4, 2))
            _msg = tk.Entry(f, width=30, font=("SimHei", 12))
            _msg.insert(0, "到时间了")
            _msg.pack(anchor="center", pady=2)

            def _ok3():
                try:
                    mins = float(_mins.get().strip())
                except ValueError:
                    self._bubble("分钟数不对", 3)
                    return
                t = _msg.get().strip() or "到时间了"
                self._reminders.append((time.time() + mins * 60, t))
                self.say("好的，%g 分钟后提醒你" % mins, "ok")
                self._bubble("已设提醒：%g 分钟后「%s」" % (mins, t), 4)
            _r = tk.Frame(f, bg=_bg2)
            _r.pack(anchor="center", pady=3)
            self._dlg_btn(_r, "确定", _ok3)

        def _b_todo(f):
            _bg2 = "#f2f5f8"
            _box = tk.Listbox(f, width=48, height=5, font=("SimHei", 11),
                              bg="#ffffff", fg="#46536a", relief="flat",
                              highlightthickness=1, highlightbackground="#d5d9e3")
            _box.pack(anchor="center", pady=3)

            def _refresh2():
                _box.delete(0, "end")
                try:
                    sys.path.insert(0, self._tools_dir())
                    import fairy_todo as _ft2
                    for i, x in enumerate(_ft2.list_items()):
                        _box.insert("end", "[%d] %s%s %s%s" % (
                            i, "完成" if x.get("done") else "待办", "  " + x["text"],
                            " @" + x["at"] if x.get("at") else "",
                            "  ←已到期" if (x.get("at") and x["at"] <= time.strftime("%H:%M")
                                           and not x.get("done")) else ""))
                except Exception:
                    pass
            _refresh2()
            _ent = tk.Entry(f, width=48, font=("SimHei", 12))
            _ent.pack(anchor="center", pady=2)
            tk.Label(f, text="添加：内容|时间（如 交报告|14:30，时间可省）", bg=_bg2,
                     fg="#97a1ad", font=("SimHei", 10)).pack(anchor="center")

            def _add2():
                t = _ent.get().strip()
                if not t:
                    return
                at = ""
                if "|" in t:
                    t, at = t.split("|", 1)
                try:
                    sys.path.insert(0, self._tools_dir())
                    import fairy_todo as _ft3
                    _ft3.add(t.strip(), at.strip())
                    self.say("已添加待办" + ("" if not at else "，" + at.strip() + " 提醒"), "ok")
                except Exception:
                    pass
                _ent.delete(0, "end")
                _refresh2()

            def _done2():
                try:
                    s = _box.get("active").split("]")[0].strip("[").strip()
                    sys.path.insert(0, self._tools_dir())
                    import fairy_todo as _ft4
                    _ft4.done(int(s))
                except Exception:
                    pass
                _refresh2()

            def _del2():
                try:
                    s = _box.get("active").split("]")[0].strip("[").strip()
                    sys.path.insert(0, self._tools_dir())
                    import fairy_todo as _ft5
                    _ft5.delete(int(s))
                except Exception:
                    pass
                _refresh2()
            _r = tk.Frame(f, bg=_bg2)
            _r.pack(anchor="center", pady=3)
            self._dlg_btn(_r, "添加", _add2)
            self._dlg_btn(_r, "完成（选中）", _done2)
            self._dlg_btn(_r, "删除（选中）", _del2, danger=True)

        _exp_row(b5, "剪贴板历史", _b_clip)
        _exp_row(b5, "定时提醒", _b_remind)
        _exp_row(b5, "待办清单", _b_todo)
        _exp_row(b5, "位置", _b_pos)
        _exp_row(b5, "权限授权", _b_perms)
        _row(b5, "记忆与说明", [("记忆统计", self._mem_panel),
                              ("权限说明", self._perms_show)])

        def _b_brief(f):
            _bg2 = "#f2f5f8"
            _items = [("time", "时间问候"), ("location", "当前位置"), ("weather", "天气"),
                      ("dress", "穿衣建议"), ("news", "新闻"), ("status", "服务状态"),
                      ("devices", "已连接设备数"), ("holiday", "今日节日/节气"),
                      ("todo", "今日待办"), ("network", "网络状态"), ("api_balance", "API 余额")]
            cur = {}
            try:
                with open(fairy_root.CONFIG, encoding="utf-8") as _f:
                    _j = json.load(_f)
                cur = _j.get("briefing") or {}
            except Exception:
                pass
            _vs = {}
            for _k, _nm in _items:
                _v = tk.BooleanVar(value=cur.get(_k, True))
                _vs[_k] = _v
                tk.Checkbutton(f, text=_nm, variable=_v, anchor="w", bg=_bg2,
                               fg="#46536a", activebackground=_bg2, selectcolor=_bg2,
                               font=("SimHei", 11)).pack(anchor="center", pady=1)

            def _save():
                _new = {_k: bool(_v.get()) for _k, _v in _vs.items()}
                try:
                    with open(fairy_root.CONFIG, encoding="utf-8") as _f:
                        _j = json.load(_f)
                    _j["briefing"] = _new
                    with open(fairy_root.CONFIG, "w", encoding="utf-8") as _f:
                        json.dump(_j, _f, ensure_ascii=False, indent=2)
                    self.say("启动播报内容已更新", "ok")
                    self._bubble("已保存，下次启动生效", 4)
                except Exception as _e:
                    self._bubble("保存失败：" + str(_e)[:60], 4)
            _r = tk.Frame(f, bg=_bg2)
            _r.pack(anchor="center", pady=3)
            self._dlg_btn(_r, "保存（下次启动生效）", _save)

        def _b_cmd(f):
            _bg2 = "#f2f5f8"
            _box = tk.Listbox(f, width=56, height=5, font=("SimHei", 11),
                              bg="#ffffff", fg="#46536a", relief="flat",
                              highlightthickness=1, highlightbackground="#d5d9e3")
            _box.pack(anchor="center", pady=3)

            def _refresh3():
                _box.delete(0, "end")
                try:
                    sys.path.insert(0, self._tools_dir())
                    import fairy_custom_cmd as _fcc
                    for i, c in enumerate(_fcc._load()):
                        _box.insert("end", "[%d] %s → %s: %s" % (
                            i, c.get("trigger"), c.get("type"), c.get("target")))
                except Exception:
                    pass
            _refresh3()
            _f1 = tk.Frame(f, bg=_bg2)
            _f1.pack(anchor="center", pady=2)
            tk.Label(_f1, text="说：", bg=_bg2, fg="#46536a",
                     font=("SimHei", 11)).pack(side="left")
            _e1 = tk.Entry(_f1, width=12, font=("SimHei", 11))
            _e1.pack(side="left", padx=(0, 8))
            tk.Label(_f1, text="做：", bg=_bg2, fg="#46536a",
                     font=("SimHei", 11)).pack(side="left")
            _e2 = tk.Entry(_f1, width=22, font=("SimHei", 11))
            _e2.pack(side="left")
            _v = tk.StringVar(value="say")
            _f2 = tk.Frame(f, bg=_bg2)
            _f2.pack(anchor="center", pady=2)
            for _k, _nm in (("app", "打开软件"), ("url", "打开网页"),
                            ("say", "回复一句"), ("cmd", "运行命令")):
                tk.Radiobutton(_f2, text=_nm, variable=_v, value=_k, bg=_bg2,
                               fg="#46536a", activebackground=_bg2, selectcolor=_bg2,
                               font=("SimHei", 10)).pack(side="left", padx=3)

            def _add3():
                tr = _e1.get().strip()
                tg = _e2.get().strip()
                if not tr or not tg:
                    return
                try:
                    sys.path.insert(0, self._tools_dir())
                    import fairy_custom_cmd as _fcc2
                    _fcc2.add(tr, _v.get(), tg)
                    self.say("已添加自定义指令", "ok")
                except Exception:
                    pass
                _e1.delete(0, "end")
                _e2.delete(0, "end")
                _refresh3()

            def _del3():
                try:
                    s = _box.get("active").split("]")[0].strip("[").strip()
                    sys.path.insert(0, self._tools_dir())
                    import fairy_custom_cmd as _fcc3
                    _fcc3.remove(int(s))
                except Exception:
                    pass
                _refresh3()
            _r = tk.Frame(f, bg=_bg2)
            _r.pack(anchor="center", pady=3)
            self._dlg_btn(_r, "添加", _add3)
            self._dlg_btn(_r, "删除（选中）", _del3, danger=True)
            tk.Label(f, text="例：说「查快递」→ 打开「拼多多」；说「网怎么了」→ 运行「网络诊断」",
                     bg=_bg2, fg="#97a1ad", font=("SimHei", 10)).pack(anchor="center", pady=(4, 0))

        _exp_row(b5, "自定义指令", _b_cmd)
        _exp_row(b5, "启动播报内容", _b_brief)
        _exp_row(b5, "夜间模式", _b_night)
        tk.Label(b5, text="", bg="#ffffff", height=1).pack()




        _cv.update_idletasks()
        _cv.configure(scrollregion=_cv.bbox("all"))
        win.after(250, lambda: (_cv.update_idletasks(),
                                _cv.configure(scrollregion=_cv.bbox("all"))))
        # ★ 2026-10-12【宽度/滚动区加固】构建完立刻刷新内层宽度与滚动区，
        #   再在 400ms 后补一次 —— 避免 Canvas 首帧宽度为 0 时展开区宽度
        #   被卡成最小值的"灰条不铺满、内容不居中"问题。
        try:
            _fit(None)
        except Exception:
            pass
        win.after(400, lambda: (_fit(None), _cv.update_idletasks(),
                                _cv.configure(scrollregion=_cv.bbox("all"))))

        # 滚轮绑定到右侧所有子控件
        def _bind_wheel(_w):
            _w.bind("<MouseWheel>", lambda _e: _cv.yview_scroll(
                int(-1 * (_e.delta / 120)), "units"))
            for _c in _w.winfo_children():
                _bind_wheel(_c)
        _bind_wheel(_inner)

        # Esc 关闭面板（任意焦点都生效；面板销毁后自动失效）
        def _esc(_e):
            try:
                if win.winfo_exists():
                    win.destroy()
            except Exception:
                pass
        win.bind_all("<Escape>", _esc)



    def _voice_set_ui(self):
        """★ 2026-10-11【音色选择】统一风格弹窗：列出全部音色，点击即切换。"""
        try:
            import fairy_voice as _fv
        except Exception:
            self._bubble("音色模块不可用", 4)
            return
        _vs = _fv.list_voices() or ["fairy.wav"]
        _cur = _fv.current()
        win, wrap = self._dialog("选择音色", 360, 360)
        for _vn in _vs:
            _img = _pil_round(("✓ " if _vn == _cur else "  ") + _vn, 300, 42, 12,
                              "#ffffff", "#46536a", True, "#dfe3ea")[0]
            _b = tk.Button(wrap, image=_img, bd=0, relief="flat",
                           highlightthickness=0, bg="#f2f3f5",
                           activebackground="#f2f3f5", cursor="hand2",
                           command=(lambda n=_vn: (self._voice_set(n), win.destroy())))
            _b.image = _img
            _b.pack(pady=3)



    def _open_ui(self):
        """★ 2026-10-12【打开Strata对话】右键菜单入口。
        Strata（8080）没跑就先用本地运算启动，再打开它的对话页。"""
        threading.Thread(target=self._open_ui_job, daemon=True,
                         name="fairy-openui").start()

    def _open_chat_web(self, engine="ollama", port=11434, model=""):
        """★ 2026-10-12【统一网页对话】Ollama/llm 组合切换后打开的对话页。
        起 8090 代理服务（幂等）-> 浏览器打开带参对话页，样式对齐 Strata。"""
        threading.Thread(target=self._open_chat_web_job,
                         args=(engine, port, model), daemon=True,
                         name="fairy-chatweb").start()

    def _open_chat_web_job(self, engine, port, model):
        import subprocess as _sp
        import webbrowser as _wb
        from urllib.parse import quote
        try:
            if not self._probe_port(8090, timeout=0.8):
                _exe = sys.executable.replace("python.exe", "pythonw.exe")
                _script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "fairy_chat.py")
                try:
                    _sp.Popen([_exe, _script],
                              creationflags=0x08000000,
                              stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
                except Exception:
                    _sp.Popen([sys.executable, _script],
                              creationflags=0x08000000,
                              stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
                for _ in range(15):
                    time.sleep(0.4)
                    if self._probe_port(8090, timeout=0.8):
                        break
            if self._probe_port(8090, timeout=0.8):
                _url = ("http://127.0.0.1:8090/?engine=%s&port=%d&model=%s"
                        % (quote(engine), int(port), quote(model or "")))
                try:
                    _wb.open(_url)
                except Exception as _e2:
                    self._ui_bubble("打开浏览器失败：" + str(_e2)[:40])
            else:
                self._ui_bubble("对话页 8090 启动失败，请查看 logs\\fairy_chat.log")
        except Exception as _e:
            self._ui_bubble("打开对话页失败：" + str(_e)[:40])

    def _probe_port(self, port, timeout=0.6):
        """探一个本地端口通不通。返回 True/False。"""
        import socket as _sock
        try:
            _s = _sock.create_connection(("127.0.0.1", port), timeout)
            _s.close()
            return True
        except Exception:
            return False

    def _ui_bubble(self, text):
        """后台线程安全弹气泡：只入队，由主线程 _tick 取出再弹。"""
        try:
            self._adv_bubbles.append(text)
        except Exception:
            pass

    def _open_ui_job(self):
        """_open_ui 的实际工作（后台线程）：Strata 没跑先启动 -> 通了再开 8090 统一对话页。"""
        port = 8080
        alive = self._probe_port(port)
        if not alive:
            # Strata 没跑：先启动本地运算
            if getattr(self, "_strata_busy", False):
                self._ui_bubble("上一个操作还在进行，稍后再试。")
                return
            self._strata_start()
            for _ in range(40):
                time.sleep(3)
                if self._probe_port(port):
                    break
        if self._probe_port(port):
            # ★ 2026-10-15【统一使用】Strata 也统一打开 8090 对话页（代理到 strata），
            #   不再开 Strata 原生 8080 页；8090 由 _open_chat_web 幂等拉起。
            self._open_chat_web("strata", 8080, "")
        else:
            self._ui_bubble("Strata 还没就绪（加载模型约 1~2 分钟），稍后右键再开。")

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
    def _pick_side(self):
        """球在屏幕**左半边** → 进度条在球的右边；在**右半边** → 条在球的左边。

        用屏幕中线判断（而不是"右边还剩多少空间"），因为这样更符合直觉，
        而且天然保证条不会跑出屏幕外。
        """
        try:
            vx, vy, vw, vh = virtual_screen()
            ball_x = self.winfo_x() + (0 if self.side == "right" else BAR_W + GAP)
            center = ball_x + SIZE / 2.0
            return "right" if center < (vx + vw / 2.0) else "left"
        except Exception:
            return "right"

    def _draw_bar(self, img, ball_x, running, prog, cpct, doing, crem=None, bal=None, mode="", task_label="", steps=0):
        """右侧面板：进度条 + 百分比 + 在做什么。额度不足时整条变红。

        ★★【深色实底】需求：
          「把球和提示条的背景色改为悬浮框的背景色」「添加背景色是为了能看清」
        【原来】面板区域 alpha=0（全透明）-> 桌面壁纸透上来 -> 字和背景糊在一起。
        【深色实底那一版】铺一块【不透明】的深色圆角板（色值 = _PANEL_BG）+ 硬描边。
        ★★【中间深、外围渐透】需求：
          「进度条的背景色中间深，渐变到外围就透明了」
        【现在】底板是一张【软边圆角矩形】的图（_panel_backing()）：中间 alpha=255、
          贴边 alpha=0、之间 smoothstep 平滑过渡，【没有硬描边、没有硬边界】。
          底板矩形的位置/尺寸、文字坐标、进度条轨道【一行没动】。
        【位置】没有动：板子就铺在原来的文字/进度条范围内，弹窗定位逻辑一行没改。
        """
        # ★ 面板底板 = 【中间深、外围渐透】的软边圆角矩形（见 _panel_backing）。
        #   范围：包住标题行到硬件条下方，左侧留 16px 给文字（x0 是文字左边界）。
        w = BAR_W - 16
        x0 = (ball_x + SIZE + GAP) if self.side == "right" else (ball_x - GAP - w)
        # ★ 2026-10-10 晚：状态行并进标题行后，少了一行 -> 底板下缘跟着收到 100
        #   （原来 WIN_H-10=118），整块面板更紧凑，上下留白也匀。
        _pb_y1, _pb_y2 = 4, WIN_H - 28
        _px0, _px1 = x0 - 16, x0 + w + _PANEL_RGROW
        # ★ 2026-10-10球在左边（side=right）时球箱是 [0,128]，
        #   x0 只有 136 -> 左边界 120 -> 底板会压掉球自己最右边 8px（辉光被切）。
        #   所以球在左时，左边界取 max(x0-16, 球右缘+2)，永远不吃到球身上。
        #
        # ★★ 2026-10-10【】上一版这里【无条件】取 max(x0-16, ball_x+130)：
        #   球在【右边】时（side=left，球贴屏幕右缘时的常见状态）ball_x=216，
        #   _px0 被夹到 346 > 窗口宽 344 -> 整块底板画到窗口外，
        #   实测（本轮开头的桌面截图）= 面板【完全没有底色】，字直接糊在游戏画面上。
        #   现在按"底板永远在球的反侧"分别夹：球在左 -> 左边界让开球；
        #   球在右 -> 右边界让开球。底板宽度公式、纵向范围、所有文字坐标【没动】。
        if self.side == "right":
            _px0 = max(_px0, int(ball_x + SIZE + 2))
        else:
            _px1 = min(_px1, int(ball_x - 2))
        # ⚠ 光取 max 还不够 —— 极端情况下
        #   _px0 可能超过右边界 x0+w，于是画底板抛 "x1 must be greater than or
        #   equal to x0"，每一帧都抛（33fps 刷屏）。所以右边也兜一下，最小留 8px。
        _px1 = max(_px1, _px0 + 8)
        _bw = int(round(_px1 - _px0))
        _bh = int(round(_pb_y2 - _pb_y1))
        _plate = None
        if _bw > 0 and _bh > 0:
            # ★ 用 alpha_composite（真实 source-over）：paste(x, x) 会把 alpha
            #   再乘一遍自己（实测 128 -> 64），渐变曲线会被"平方"，边缘陡然变硬。
            _plate = _panel_backing(_bw, _bh)
            img.alpha_composite(_plate, (int(_px0), int(_pb_y1)))
        d = ImageDraw.Draw(img)
        # ★★ 2026-10-10【】轨道不再直接写像素，改成由 _panel_bar 叠在
        #   底板上（source-over，不覆盖底板的 alpha）—— 理由见 _panel_bar 的说明。
        def _bar_rect(x1, y1, x2, y2, r, fill, fuse=True):
            """轨道/填充的圆角矩形：fuse=True -> 叠在底板上；False -> 老做法直接写像素
            （填充是不透明的，本来就没有"打穿"问题 —— 见 _PANEL_FUSE_FILL 的说明）。"""
            if fuse and _plate is not None:
                _panel_bar(img, _plate, _px0, _pb_y1, (x1, y1, x2, y2), r, fill)
            else:
                d.rounded_rectangle([x1, y1, x2, y2], radius=r, fill=fill)

        y0 = 12
        # ★★【把正在执行命令这一行放在第 12 步那一行，
        #    变为 正在执行命令第X步」）—— 两行并成一行：
        #    标题行 = 【在做什么】+【第 N 步】；上下文告警也并进来（并变橙红）。
        #    中间用两个空格分隔，读起来是两段信息，不会糊成一串。
        low = isinstance(cpct, (int, float)) and cpct >= 85
        _parts = []
        if task_label:
            _parts.append(task_label)
        elif doing:
            _parts.append(doing)
        elif running:
            _parts.append("正在执行")
        else:
            _parts.append("待机")
        if steps and steps > 0:
            _parts.append("第 %d 步" % steps)
        if low:
            _parts.append("上下文 %d%% 快满了" % int(cpct))
        _title = "  ".join(_parts)
        _tcol = (226, 86, 58, 255) if low else (170, 206, 240, 255)
        # 标题行右侧显示【自动判断的执行方式】：串行 / 多代理 N / 后台 N / 组合
        # 不是串行时用亮色，一眼能看出"正在多线干活"
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
            # ★ 按【可用像素宽】截断（不再写死 14 个字）：右边要给"方式"留位置
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
        # low 已在标题行那段算过（上下文 >=85% -> 橙红）
        col = (226, 86, 58, 255) if low else ((74, 170, 235, 255) if running else (92, 124, 152, 255))
        # 轨道（★ 深色底上要提亮：原来 28 是给"透壁纸"看的，实底上太暗会看不见）
        # ★ 2026-10-10【】轨道改成叠在底板上（fuse=True）：
        #   不再把底板该处的 alpha 覆盖成 46（那正是"打穿"）。
        ty = y0 + 30
        # ★ 条子实际画多宽：内容右边内收 _PANEL_RINSET（见该常量的说明）——
        #   目的是让底板左右两边露出的底色一样长（第三轮要求）。
        bw = max(20, int(w - _PANEL_RINSET))
        _bar_rect(x0, ty, x0 + bw, ty + 12, 6, (255, 255, 255, 46), True)
        if prog is None:
            if running:                       # 不确定态：一段来回滑
                seg = int(bw * 0.34)
                off = int((self.spin * 7) % max(1, bw - seg))
                _bar_rect(x0 + off, ty, x0 + off + seg, ty + 12, 6, col, _PANEL_FUSE_FILL)
                self.spin += 1
        else:
            fw = max(3, int(bw * max(0.0, min(100.0, float(prog))) / 100.0))
            _bar_rect(x0, ty, x0 + fw, ty + 12, 6, col, _PANEL_FUSE_FILL)
        # 百分比（右边，大字）；待机不显示进度时用 "--"（不显示"…"）
        pct_txt = "--" if (prog is None and not running) else (
            "…" if prog is None else ("%d%%" % int(prog)))
        try:
            tw = d.textlength(pct_txt, font=self.f_big)
        except Exception:
            tw = 44
        _ptext(d, (x0 + w - tw - _PANEL_RPAD, y0 + 6), pct_txt, self.f_big, col)
        # ★ 2026-10-10 晚：原来的「状态行（正在执行命令）」和「上下文快满了」
        #   两行【已并进标题行】（要求"正在执行命令第X步"放一行）——
        #   这里只留个记号：别再把它们画回来，否则标题行会重复一遍。
        # 余额已移到标题行右端，这里不再重复显示
        # ★【2026-10-10】面板底部再加一条：内存 / 显存 / CPU（三个小进度条并排）
        self._draw_hw(d, x0, bw, _bar_rect)

    def _draw_hw(self, d, x0, w, rrect=None):
        """面板底部一行：内存 / 显存 / CPU 三个小条并排。

        风格刻意和主条一致：同一个字体 f_sm、同一个蓝色系、圆角按高度等比。
        数据来自 HwMonitor 的快照（后台线程采的），这里【只画不查】。
        取不到的那条显示 --，并且不画填充 —— 宁可空着也不编数字。
        ★ 2026-10-10 新增 rrect：把轨道/填充叠在底板上的回调（同主条的做法）。
          不传就还是直接写像素（旧行为）。
        """
        hw = getattr(self, "hw", None)
        snap = hw.snap() if hw is not None else None
        if not snap:
            return
        gap = 6
        cw = int((w - gap * 2) / 3)
        # ★ 2026-10-10 晚（状态行并进标题行后，空出一行）：硬件行再上移，
        #   现在就紧跟在进度条下面，底板下缘同步收到 WIN_H-28=100。
        ly = WIN_H - 60                  # 标签行（13px 粗体）
        by = WIN_H - 45                  # 小条：高 7px，圆角 3（主条是 12px/圆角 6，等比）
        bh = 7
        cells = (("内存", snap.get("mem_pct")),
                 ("显存", snap.get("vram_pct")),
                 ("CPU", snap.get("cpu_pct")))
        for i, (name, pct) in enumerate(cells):
            cx = x0 + i * (cw + gap)
            if isinstance(pct, (int, float)):
                pct = max(0.0, min(100.0, float(pct)))
                ipct = int(round(pct))          # ★ 先取整再配色：免得显示 90% 而颜色还停在橙
                txt = "%s %d%%" % (name, ipct)
                col = _hw_color(ipct)
                # ★ 标签一律用亮色 + 粗体（要求更明显）；
                #   只有【快满】时才跟着数字一起变橙/红，那是警告语义，保留。
                lab = col if ipct >= 90 else (234, 245, 255, 255)
            else:
                txt = "%s --" % name
                ipct = None
                col = None
                lab = (168, 186, 208, 255)
            _ptext(d, (cx, ly), txt, self.f_sm_b, lab)
            # 轨道（★ 深色实底上提亮，理由同主条）
            # ★ 2026-10-10：走 rrect -> 叠在底板上（不再把底板 alpha 覆盖成 46）
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
            ball = base[self.idx % len(base)]
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
            if True:
                try:
                    self._draw_bar(img, ball_x, running, prog, cpct, doing, crem, self.balance, mode,
                               _lt or self.state.task_label, self.state.steps)
                except Exception as _e_bar:
                    # 进度条画失败【绝不能拖垮球体】：只记日志，球照常显示
                    print("[fairy] 进度条渲染失败（跳过该帧进度条）: %s" % _e_bar, flush=True)
            self.lw.blit(to_premultiplied_bgra(img))
            self.idx += 1
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


def selftest():
    files, bright, dim = load_frames()
    print("素材帧数:", len(files), "首帧:", files[0], "末帧:", files[-1])
    # ★ 2026-10-09 修：load_frames() 返回的是 PIL Image，没有 .shape / .dtype。
    #   原来直接 bright[0].shape -> AttributeError: 'Image' object has no attribute 'shape'
    #   转成 numpy 数组再取属性才对。
    _b0 = np.asarray(bright[0])
    _d0 = np.asarray(dim[0])
    print("亮帧数组:", _b0.shape, _b0.dtype)
    print("暗帧数组:", _d0.shape)
    print("虚拟桌面(含多屏):", virtual_screen())
    # ★【2026-10-10】把寻址情况打出来：现在到底走 DSH 还是本地
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
    """只测硬件采数（不建窗口）：把内存/显存/CPU 的各条路子逐个打出来。

    用法：py -3.12 fairy_ball.py --hwselftest
    这是"面板上那几个数字到底从哪来"的现场取证工具 —— 不带 Tk、不动界面。
    """
    def _gb(v):
        return "?" if v is None else "%.2f" % (v / float(2 ** 30))

    print("psutil:", getattr(_psutil, "__version__", "缺失"))
    print("显存总量(注册表 qwMemorySize):", HwMonitor._read_vram_total(),
          "=", _gb(HwMonitor._read_vram_total()), "GB")
    m = HwMonitor()
    print("显存-PDH :", m._vram_pdh(), "=", _gb(m._vram_pdh()), "GB")
    print("显存-WMI :", m._vram_wmi(), "=", _gb(m._vram_wmi()), "GB")
    if _psutil is not None:
        # ★ 2026-10-10这里读到的【是基线伪值，不是真实占用】：
        #   psutil 的 cpu_percent 基线按【调用线程 ident】分键（7.2.2 实测），
        #   下面这发是在主线程空打后立刻读的 -> 分母近 0 -> 恒为 0.0。
        #   真实的"第一发"由 HwMonitor._loop（采样线程内打基线 + 先等一个间隔）保证，
        #   见本函数末尾那段线程模型实测。
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
