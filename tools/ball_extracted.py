# -*- coding: utf-8 -*-
"""从 fairy_ball.py 抽出的可独立部分（2026-10-23）。

只搬家，方法体一行未改。
"""
import ctypes
import ctypes.wintypes as _wt
import json
import os
import re
import subprocess
import sys
import threading
import time

import numpy as np
import tkinter as tk
from PIL import Image, ImageChops, ImageDraw, ImageFont

import fairy_root
import fairy_endpoints as ep

# ★ 2026-10-23 HwMonitor 搬过来后需要 psutil；主文件那边是 try/except
try:
    import psutil as _psutil
except Exception:
    _psutil = None


def _rnw(*_a, **_kw):
    _kw.setdefault("creationflags", getattr(_sp_mod, "CREATE_NO_WINDOW", 0))
    return _sp_mod.run(*_a, **_kw)


ASSETS = os.path.join(fairy_root.ASSETS, "ball_128")


STRATA_PORT = 8080


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
    _cmd = [_py, os.path.join(_d, "serve", "server.py"),
            "--engine", "strata", "--config", _cfg,
            "--port", str(STRATA_PORT)]
    # ★★ 2026-10-17【"Strata 的浏览器为什么不显示"的根因】
    #   server.py 里只有传了 `--open` 才会在模型就绪后打开浏览器
    #   （见 server.py: `if a.open and cfg.get("open_browser") is not False: webbrowser.open(...)`）。
    #   而 Fairy 以前拉起 Strata 时【没传 --open】—— 所以它从来不会自动弹，
    #   用户只能自己猜地址去开，且加载那 6~7 分钟里 8080 根本不通，看起来就是"不显示"。
    #   现在默认带上 --open；不想每次弹浏览器就在 fairy.json 顶层写
    #   "strata_open_browser": false（等价于 Strata 配置里的 open_browser:false）。
    if not (isinstance(_c, dict) and _c.get("strata_open_browser") is False):
        _cmd.append("--open")
    return _cmd


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


CRED_FILE = os.path.expanduser(r"~/.dsh/.credentials.yaml")
BALANCE_API = "https://api.deepseek.com/user/balance"


LINE_POOLS_FILE = fairy_root.LINE_POOLS


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


POLL_MS = 900              # 拉状态的间隔


_user32 = ctypes.windll.user32


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


_PUNCT = "\u2026\u3002\uff0c\uff01\uff1f\u3001\uff1b\uff1a\u201c\u201d\u2018\u2019\u300a\u300b\u3010\u3011()[]{}<>!?,.;:\"'\u3000 \t\n"


def _norm(s):
    """归一化：去掉标点和空白，只留文字，好做命中判断。"""
    return "".join(ch for ch in str(s or "") if ch not in _PUNCT).strip()


_PANEL_BG = "#0b1622"        # ★ 悬浮提示框的背景色（深蓝黑实底）
_PANEL_BG_RGB = (11, 22, 34)  # 同一个色的 RGB 元组（PIL 用）


def _ptext(d, xy, txt, font, fill):
    """在面板上写一个字。

    ★★ 2026-10-19【用户反馈「边缘有点撕裂」】
      原来这里固定加 1px 深色描边（stroke_width=1），本意是给「外围渐透」的
      底板兜底 —— 贴边的字在渐隐段会变淡。但实测现在底板是实色
      （_PANEL_BG_RGB，alpha 恒 255），描边根本用不上，反而在笔画外侧
      糊出一圈深色硬边，看起来就是锯齿/撕裂感。
      所以改成：**正常写字，不加描边**。
      如果哪天底板又改回渐变，把下面那两行 note 的描边方案恢复即可。
    """
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
    # ★★ 2026-10-19【用户反馈「边缘有点撕裂」】
    #   ImageDraw.rounded_rectangle 画圆角时是【硬像素】，没有抗锯齿 ——
    #   尤其在 7px 高的小条上，圆角只有 3px，边缘会呈阶梯状（用户说的"撕裂"）。
    #   这里改成【4 倍超采样】：在 4x 尺寸上画圆角，再用 LANCZOS 缩回来，
    #   边缘就平滑了。成本：一块最大 800x56 的临时图 + 一次缩放，对 33fps 无影响。
    SS = 4
    big = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    ImageDraw.Draw(big).rounded_rectangle(
        [0, 0, w * SS - 1, h * SS - 1], radius=max(1, int(radius) * SS), fill=fill)
    tile = big.resize((w, h), Image.LANCZOS)
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


def _split_for_speech(text, max_len=42, min_len=4):
    """把一句话切成适合逐句合成的小段。

    为什么要有：
      IndexTTS 是自回归生成，文本越长线性越慢（30 秒音频 ≈ 26 秒）。
      但播放器是流式的 —— 只要第一段好了就能开口。
      所以按标点切成 10~40 字的短句，让「合成」和「播放」并行起来。

    切分规则（优先级从高到低）：
      1) 句末标点 。！？!?… 后面切
      2) 逗号、分号、顿号 ，；,; 后面切（仅当已经攒够 min_len）
      3) 单段超过 max_len 时硬切（避免一句太长又变回慢）
    不切的情况：
      · 文本本来就短（<= max_len），原样返回 —— 避免把一句话拆得太碎、语调断裂
    """
    import re as _re
    t = (text or "").strip()
    if not t:
        return []
    if len(t) <= max_len:
        return [t]

    out = []
    buf = ""
    # 按字符扫，遇到合适标点就断
    for ch in t:
        buf += ch
        is_end = ch in "。！？!?…"
        is_pause = ch in "，；,;"
        if is_end and len(buf) >= 2:
            out.append(buf.strip())
            buf = ""
        elif is_pause and len(buf) >= min_len and len(buf) < max_len:
            # 逗号只在「攒够了一点、但还没超长」时切
            out.append(buf.strip())
            buf = ""
        elif len(buf) >= max_len:
            out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())

    # 去掉纯标点的碎片，避免合成出奇怪的声音
    out = [x for x in out if x and not _re.fullmatch(r"[\s\W_]+", x)]
    return out or [t]


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
                    # ★★ 2026-10-19【用户要求「运行任务时实时进度条按 % 显示，
                    #   出结果的时候才是 100%」】
                    #   真实情况：DSH 宿主只在自己声明了任务计划时（POST /api/fairy/task
                    #   {total,done}）才给 progress，否则返回 null —— 实测一直是 null。
                    #   所以这里做本地估算：
                    #     · running 且没有真 progress -> 按已执行工具步数线性推进，
                    #       封顶 92%（留一点余量，避免看着满了却还在跑）
                    #     · running 从 True 变 False（一个回合结束）-> 立刻 100%
                    #   估算步数用 12 步封顶，因为实测多数任务在 5~12 步之间。
                    _real = j.get("progress")
                    if isinstance(_real, (int, float)):
                        self.progress = max(0.0, min(100.0, float(_real)))
                    elif self.running:
                        _n = int(self.steps or 0)
                        self.progress = round(min(92.0, _n * 100.0 / 12.0), 1)
                    elif prev_run is True and self.running is False:
                        self.progress = 100.0        # 出结果那一刻 = 100%
                    else:
                        self.progress = None
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


HW_REFRESH_S = 1.0          # ★ 2026-10-19 用户要求「实时」，2s -> 1s


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

        # ★★ 2026-10-19【用户要求：GPU 和 CPU 温度都显示在悬浮框】
        #   数据源走 MSI Afterburner 的 MAHM 共享内存（fairy_gpu_ab.py）：
        #     · AMD ADL 在 RDNA4(RX 9070 XT) 上温度接口全部返回不支持，实测过
        #     · Windows 性能计数器只有使用率和显存，没有温度
        #     · HWiNFO 免费版共享内存要手动在 GUI 勾，命令行参数还是 Pro 功能
        #     · Afterburner 一直在跑，共享内存现成可读 —— 实测一次拿到 77 条传感器
        #   读失败就留 None，界面显示 --（绝不编数字）。
        gpu_temp = cpu_temp = None
        try:
            import fairy_gpu_ab as _ab
            _d = _ab.read_all()
            _g = _d.get("GPU temperature")
            _c = _d.get("CPU temperature")
            if _g and _g[0] is not None:
                gpu_temp = float(_g[0])
            if _c and _c[0] is not None:
                cpu_temp = float(_c[0])
        except Exception:
            pass

        with self.lock:
            self._snap = {"mem_pct": mem_pct, "mem_used": mem_used, "mem_total": mem_total,
                          "vram_pct": vram_pct, "vram_used": used, "vram_total": total,
                          "cpu_pct": cpu, "vram_src": src,
                          "gpu_temp": gpu_temp, "cpu_temp": cpu_temp}

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
