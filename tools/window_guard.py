# -*- coding: utf-8 -*-
"""
window_guard.py —— Fairy 的「窗口播报 + 代码审核」守护。

【窗口播报】检测到新的前台应用窗口就播报一声（"检测到 XX 窗口"）。
【代码审核】检测到编辑器窗口时，提取当前文件名 → 找到文件 → 调本地大脑
            （Strata 8080 OpenAI 兼容接口）审核 → 有错误时播报。

用法：
  py -3.12 window_guard.py --once         跑一轮就退（便于测试）
  py -3.12 window_guard.py --selftest     注入假情境自测（不用真等）
  py -3.12 window_guard.py --status       看节流状态 / 上次播报 / 上次审核
  py -3.12 window_guard.py --review-now   立即审核当前编辑器文件（找不到就读剪贴板）
  py -3.12 window_guard.py --mute         临时静音窗口播报
  py -3.12 window_guard.py --unmute       取消静音

球（fairy_ball.py）在 daemon 线程里驱动本模块的 tick()；
气泡文本只入队，由球主线程弹出（与 work_advisor 同一范式）。
"""

import json
import os
import re
import subprocess
import sys
import time
import glob
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
LOG = os.path.join(os.path.dirname(HERE), "logs")
STATE_FILE = os.path.join(LOG, "window_guard.json")

# Strata 本地大脑（OpenAI 兼容接口）
STRATA_BASE = "http://127.0.0.1:8080"
# 兜底值：Strata 没响应时用它
STRATA_MODEL_FALLBACK = "qwen3.8-flash-next-unsloth-ud-iq4_xs"


def strata_model():
    """动态查 Strata 当前加载的模型名。

    ★ 2026-10-21【修复硬编码】原来这里写死了
      "qwen3.8-flash-next-unsloth-ud-iq4_xs"。
      但模型可以在球菜单换、也能直接改配置换（今天就从 ud-iq4_xs
      换成了 abliterated-iq3_xxs），写死会让审核请求被 Strata 拒掉
      （model not found），而且界面上显示的名字永远不对。
      现在每次向 /v1/models 拿第一个 —— Strata 是单模型服务，列表只有一个。
      服务不在时退回兜底值，行为和以前完全一样。
    """
    try:
        with urllib.request.urlopen(STRATA_BASE + "/v1/models", timeout=3) as r:
            j = json.loads(r.read().decode("utf-8"))
        data = j.get("data") or []
        if data and data[0].get("id"):
            return data[0]["id"]
    except Exception:
        pass
    return STRATA_MODEL_FALLBACK

# 常见应用 → 中文名（窗口播报用）
APP_ZH = {
    "chrome.exe": "Chrome 浏览器", "msedge.exe": "Edge 浏览器", "firefox.exe": "Firefox 浏览器",
    "360se.exe": "360 浏览器", "qqbrowser.exe": "QQ 浏览器", "sogouexplorer.exe": "搜狗浏览器",
    "code.exe": "VS Code", "devenv.exe": "Visual Studio", "studio64.exe": "Android Studio",
    "devecostudio64.exe": "DevEco Studio", "pycharm64.exe": "PyCharm", "pycharm.exe": "PyCharm",
    "idea64.exe": "IntelliJ IDEA", "sublime_text.exe": "Sublime Text", "notepad++.exe": "Notepad++",
    "notepad.exe": "记事本", "codeblocks.exe": "Code::Blocks", "eclipse.exe": "Eclipse",
    "wechat.exe": "微信", "qq.exe": "QQ", "tim.exe": "TIM",
    "winword.exe": "Word", "excel.exe": "Excel", "powerpnt.exe": "PowerPoint",
    "wps.exe": "WPS 文字", "wpp.exe": "WPS 演示", "et.exe": "WPS 表格",
    "potplayermini64.exe": "PotPlayer 播放器", "wmplayer.exe": "Windows 播放器", "vlc.exe": "VLC 播放器",
    "cloudmusic.exe": "网易云音乐", "qqmusic.exe": "QQ 音乐", "kugou.exe": "酷狗音乐",
    "explorer.exe": "资源管理器", "typora.exe": "Typora", "obsidian.exe": "Obsidian",
    "windowsterminal.exe": "终端", "cmd.exe": "命令行", "powershell.exe": "PowerShell",
    "acrobat.exe": "PDF 阅读器", "foxitreader.exe": "福昕阅读器", "mspaint.exe": "画图",
    "photoshop.exe": "Photoshop", "ps.exe": "Photoshop", "paint.net": "画图",
    "kmplayer.exe": "KMPlayer", "mpv.exe": "mpv 播放器",
}

# 编辑器进程名（代码审核只对这些窗口做）
EDITOR_EXES = {
    "code.exe", "devenv.exe", "studio64.exe", "devecostudio64.exe",
    "pycharm64.exe", "pycharm.exe", "idea64.exe", "sublime_text.exe",
    "notepad++.exe", "notepad.exe", "codeblocks.exe", "eclipse.exe",
    "vim.exe", "gvim.exe",
}

# 播放器 / 音乐应用（检测到这些窗口时自动查当前歌曲与歌词）
MUSIC_APPS = {
    "potplayermini64.exe", "wmplayer.exe", "vlc.exe", "kmplayer.exe", "mpv.exe",
    "cloudmusic.exe", "qqmusic.exe", "kugou.exe", "kuwo.exe",
}

# 浏览器（剪贴板外文 -> 自动翻译）
BROWSER_APPS = {
    "chrome.exe", "msedge.exe", "firefox.exe", "360se.exe", "qqbrowser.exe",
    "sogouexplorer.exe", "opera.exe", "brave.exe",
}

# 文档/写作应用（剪贴板中文文案 -> 自动校对）
DOC_APPS = {
    "winword.exe", "wps.exe", "wpp.exe", "et.exe", "typora.exe", "obsidian.exe",
    "notepad++.exe", "notepad.exe", "wordpad.exe",
}

# 通话/会议应用（检测到 -> 轻提示"通话翻译可用"）
CALL_APPS = {
    "teams.exe", "zoom.exe", "tencentmeeting.exe", "wemeetapp.exe",
    "wechat.exe", "wechatwork.exe", "qq.exe", "skype.exe", "dingtalk.exe",
    "feishu.exe", "lark.exe",
}

# Fairy 自己的进程（播报时要跳过，别自己报自己）
SELF_EXES = {"pythonw.exe", "python.exe", "fairy_ball.exe", "fairy_ui.exe", "py.exe"}

# 从窗口标题里找带扩展名的文件名
_FILE_RE = re.compile(
    r"([\w\-.]+\.(?:py|js|ts|jsx|tsx|java|c|h|cpp|hpp|cc|cs|go|rs|kt|kts|"
    r"html|htm|css|scss|less|json|md|sql|sh|bat|ps1|xml|yml|yaml|ini|toml|"
    r"txt|vue|php|rb|swift|m|mm|gradle|lock|conf|cfg|env))",
    re.I,
)

# 找文件时扫描的目录（深度受限，避免全盘慢扫）
SEARCH_DIRS = [
    HERE,
    os.path.join(os.path.dirname(HERE), "docs"),
    os.path.expanduser("~/Desktop"),
    os.path.expanduser("~/Documents"),
]

# 默认配置（fairy.json 的 window_guard 段可覆盖）
DEFAULTS = {
    "announce": True,          # 窗口播报总开关
    "announce_interval_s": 30, # 同一应用两次播报的最小间隔
    "explorer_interval_s": 90, # 资源管理器（高频切换）特殊拉长
    "review": True,            # 代码审核总开关
    "review_interval_s": 120,  # 同一文件两次审核的最小间隔
    "muted": False,            # 静音（--mute / --unmute 写这里）
    "search_dirs": SEARCH_DIRS,
    "review_max_lines": 250,   # 送审代码的最大行数（超过截断，避免喂爆上下文）
}


def load_config():
    """读 fairy.json 的 window_guard 段，缺失用默认；再叠加状态文件里的运行时开关。"""
    cfg = dict(DEFAULTS)
    try:
        p = os.path.join(os.path.dirname(HERE), "fairy.json")
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                j = json.load(f)
            seg = (j.get("window_guard") or {})
            for k in DEFAULTS:
                if k in seg and seg[k] is not None:
                    cfg[k] = seg[k]
    except Exception as e:
        print("[window_guard] 读配置失败（用默认）: %s" % e, flush=True)
    # 运行时开关（球右键菜单切的）优先于 fairy.json
    try:
        st = load_state()
        for k in ("announce", "review", "muted"):
            if k in st and st[k] is not None:
                cfg[k] = bool(st[k])
    except Exception:
        pass
    return cfg


def load_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_state(st):
    try:
        os.makedirs(LOG, exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print("[window_guard] 写状态失败: %s" % e, flush=True)


def foreground():
    """前台窗口标题 + 进程名 + 进程 PID。只取这三个，不读窗口内容。"""
    try:
        import win32gui
        import win32process
        import psutil
        h = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(h) or ""
        pid = 0
        try:
            _, pid = win32process.GetWindowThreadProcessId(h)
        except Exception:
            pass
        try:
            pname = psutil.Process(pid).name() if pid else ""
        except Exception:
            pname = ""
        return title, pname, pid
    except Exception as e:
        print("[window_guard] 取前台窗口失败: %s" % e, flush=True)
        return "", "", 0


def pid_has_window(pid):
    """★ 2026-10-11【启动完毕判定】该 PID 是否还有可见窗口（EnumWindows 找）。"""
    if not pid:
        return False
    try:
        import win32gui
        import win32process
        found = []

        def _cb(hwnd, _arg):
            if win32gui.IsWindowVisible(hwnd):
                try:
                    _, wpid = win32process.GetWindowThreadProcessId(hwnd)
                except Exception:
                    wpid = 0
                if wpid == pid:
                    found.append(hwnd)
                    return False          # 找到即停
            return True

        win32gui.EnumWindows(_cb, None)
        return bool(found)
    except Exception:
        # 枚举失败时保守视为还在（宁可多报一次完毕）
        return True

def app_zh(pname, title):
    """进程名 → 中文名；未知进程用标题前段兜底。"""
    if pname:
        zh = APP_ZH.get(pname.lower())
        if zh:
            return zh
    t = (title or "").strip()
    if t:
        # 去掉常见的" - 应用名"后缀，取第一段
        seg = re.split(r"\s+[—-]\s+", t)[0]
        return seg[:14] or t[:14]
    return ""


def pick_file(title):
    """从窗口标题里提取文件名（带扩展名）。找不到返回 None。"""
    m = _FILE_RE.findall(title or "")
    if not m:
        return None
    # 取最后一个（标题里路径在前、文件名在后的情况更常见）
    return m[-1]


def find_file(name, cfg):
    """在候选目录里找文件（深度受限）。找不到返回 None。"""
    if not name:
        return None
    for d in cfg.get("search_dirs") or SEARCH_DIRS:
        if not os.path.isdir(d):
            continue
        # 深度 0~2
        for depth in (0, 1, 2):
            pat = os.path.join(d, "*" * (depth + 1), name) if depth else os.path.join(d, name)
            try:
                hit = glob.glob(pat, recursive=False)
            except Exception:
                continue
            if hit:
                return hit[0]
    return None


def clip_text():
    """读剪贴板纯文本（审核兜底用）。失败返回 None。"""
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32clipboard.CF_UNICODETEXT):
                return win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        pass
    return None


def system_volume():
    """读系统主音量（0~100）。失败返回 None。"""
    try:
        from pycaw.pycaw import AudioUtilities
        d = AudioUtilities.GetSpeakers()
        return int(round(d.volume_percent))
    except Exception:
        return None


_BRAIN_NOTED = {"down": False}


class BrainUnavailable(RuntimeError):
    """大脑（Strata 8080）没在跑 —— 和"请求出错"区分开，避免刷屏成真故障。"""


def _brain_up(timeout=0.35):
    """快速探一下 8080 在不在听（不建 HTTP 请求，省时间）。"""
    import socket
    try:
        s = socket.create_connection(("127.0.0.1", 8080), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


def chat(messages, max_tokens=900, timeout=240):
    """调 Strata 本地大脑（OpenAI 兼容）。返回回复文本；失败抛异常。"""
    # ★ 2026-10-17：本地大脑没起来时，以前每复制一次英文/改一次文件就刷一条
    #   "翻译失败: WinError 10061"，看起来像系统坏了。改成：
    #   · 先探端口，没开就抛 BrainUnavailable（调用方安静跳过）
    #   · 只记一次"大脑不在"的说明，不刷屏
    if not _brain_up():
        if not _BRAIN_NOTED["down"]:
            _BRAIN_NOTED["down"] = True
            print("[window_guard] 本地大脑没在跑（8080），翻译/审核先跳过。"
                  "要启用：球右键「启动本地运算」，或 py D:\\FAIRY\\tools\\strata_start.py", flush=True)
        raise BrainUnavailable("local brain (8080) not running")
    _BRAIN_NOTED["down"] = False
    body = {
        "model": strata_model(),
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "stream": False,
        # Qwen3 默认开思考链，会把 token 全吃掉导致 content 为空；
        # 审核要的是直接答案，关掉思考。
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        STRATA_BASE + "/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        j = json.loads(r.read().decode("utf-8"))
    return (j["choices"][0]["message"]["content"] or "").strip()


def review_prompt(fname, code):
    return (
        "你是代码审核助手。检查下面的代码，只输出 JSON，不要输出其它文字：\n"
        '{"ok": true或false, "issues": [{"line": 行号数字或0, "type": "错误|警告|建议", "msg": "简短中文说明"}]}\n'
        "规则：只报真实问题（语法错误、明显逻辑 bug、未定义变量、危险操作）；没有就 ok=true。\n"
        "文件名：%s\n代码：\n```\n%s\n```" % (fname or "未知文件", code)
    )


def parse_review(text):
    """解析模型的 JSON 回复；解析失败返回降级结果。"""
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    try:
        j = json.loads(t)
        issues = j.get("issues") or []
        return bool(j.get("ok")), issues
    except Exception:
        # 降级：把整段当一条"建议"播报
        return None, [{"line": 0, "type": "结果", "msg": t[:160]}]


class WindowGuard:
    def __init__(self, say=None, bubble=None, progress=None):
        self.cfg = load_config()
        self.st = load_state()
        self.say = say
        self.bubble = bubble
        self.progress_cb = progress   # progress(start: bool, label: str) —— 球显示/隐藏提示条
        self.muted = bool(self.cfg.get("muted") or self.st.get("muted"))
        self._last_review_sig = self.st.get("review_sig", "")
        self._last_song = ""
        self._known_pids = {}      # pid -> 应用名（本会话：新进程出现才提示）
        self._pending_done = {}    # ★ 2026-10-11：pid -> {zh, t} 等"启动完毕"播报
        self._done_wait_s = 8      # 新进程出现后等多久确认"启动完毕"
        self._first_seen_done = False   # ★ 2026-10-11：是否已播过"已打开"（仅首次）
        # ★ 2026-10-11【已启动窗口不重复播报】按进程名去重：
        #   Chrome 多标签多进程、已开窗口来回切换都不再播
        self._announce_by_name = {}     # 进程名 -> 上次播报时间
        self._announce_window_s = 1800  # 同一进程名 30 分钟内只播一次
        self._prewarm_known()           # 启动时预热：当前已开窗口记为已知
        self._last_clip_hash = ""
        self._last_clip_at = 0.0
        self._last_vol = None      # 上一次读到的音量（变化 >= 5% 才提醒）
        self._vol_at = 0.0

    def _emit(self, text, level="notice"):
        try:
            if self.say:
                self.say(text, level=level)
        except Exception:
            pass
        try:
            if self.bubble:
                self.bubble(text)
        except Exception:
            pass

    def _prewarm_known(self):
        """★ 2026-10-11【已启动窗口不播报】启动时把当前已开着的窗口进程记为已知
        （跳过当前前台窗口，它留给首播"已打开"）。之后这些窗口来回切换都不再播。"""
        try:
            import win32gui
            import win32process
            import psutil
            _fg = win32gui.GetForegroundWindow()
            _skip = 0
            try:
                _, _skip = win32process.GetWindowThreadProcessId(_fg)
            except Exception:
                pass
            _names = set()

            def _cb(hwnd, _arg):
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                try:
                    _, _wpid = win32process.GetWindowThreadProcessId(hwnd)
                except Exception:
                    return True
                if _wpid == _skip or not _wpid:
                    return True
                try:
                    _pn = (psutil.Process(_wpid).name() or "").lower()
                except Exception:
                    _pn = ""
                if _pn and _pn not in SELF_EXES:
                    _names.add(_pn)
                return True

            win32gui.EnumWindows(_cb, None)
            _t = time.time()
            for _n in _names:
                # 标成"刚播过"：未来 30 分钟内同进程名不再播
                self._announce_by_name[_n] = _t - self._announce_window_s
        except Exception:
            pass

    def toggle(self, key):
        """切换运行时开关（announce / review），写状态文件持久化。"""
        self.cfg[key] = not bool(self.cfg.get(key, True))
        self.st[key] = self.cfg[key]
        save_state(self.st)
        return self.cfg[key]

    def tick(self, force=False, obs=None):
        """跑一轮观察。force=True 时无视节流（供 --review-now / 手动触发）。"""
        if obs is None:
            title, pname, pid = foreground()
        else:
            title = obs.get("title") or ""
            pname = obs.get("app") or ""
            pid = obs.get("pid") or 0

        now = time.time()
        low = pname.lower() if pname else ""
        self._announce_open(low, title, pid, now)
        self._check_pending_done(pid, now)

        if self.cfg.get("review") and low in EDITOR_EXES and title:
            self._maybe_review(title, now, force)

        self._maybe_lyrics(low, now)
        self._maybe_subtitle(low, now)
        self._maybe_clipboard(low, title, now)
        self._maybe_call(low, now)
        self._maybe_volume(now)

    # ---- 窗口播报：三阶段文案（★ 2026-10-12 用户要求：去掉"正在"）----
    #   ① 已打开的窗口（Fairy 刚启动/首次观察到的前台窗口）-> "已打开「XX」"
    #   ② 有启动过程的新进程                              -> "启动「XX」"
    #   ③ 启动完毕（8 秒后窗口仍在）                      -> "「XX」启动完毕"
    #   ★ 2026-10-11【已启动窗口不重复播报】：
    #      - 启动时预热：当前已开着的窗口进程记为已知，来回切换不再播
    #      - 按进程名 30 分钟去重：Chrome 多标签多进程、反复切换都不再吵
    def _announce_open(self, low, title, pid, now):
        if not self.cfg.get("announce") or self.muted:
            return
        if not title.strip() or pid <= 0:
            return                      # 桌面/后台：不报
        if low in SELF_EXES:
            return                      # 不报自己
        zh = app_zh(low, title)
        if not zh:
            return
        # ★ 按进程名 30 分钟去重（已启动窗口切换、多标签切换都不再播）
        if now - (self._announce_by_name.get(low) or 0) < self._announce_window_s:
            if pid not in self._known_pids:      # 首次见也记上，防"启动完毕"误判
                self._known_pids[pid] = zh
            return
        if pid in self._known_pids:
            return
        if len(self._known_pids) > 200:      # 防膨胀：清掉一半最旧的
            for _old in list(self._known_pids)[:100]:
                self._known_pids.pop(_old, None)
        self._known_pids[pid] = zh
        self._announce_by_name[low] = now
        if not self._first_seen_done:
            # ★ Fairy 刚启动：当前已开着的窗口 -> "已打开"
            self._first_seen_done = True
            self._emit("已打开「%s」" % zh)
        else:
            self._emit("启动「%s」" % zh)
            self._pending_done[pid] = {"zh": zh, "t": now}

    def _check_pending_done(self, now_pid, now):
        """★ 2026-10-11：新进程出现 8 秒后窗口还在 -> 播报"启动完毕"。"""
        if not self._pending_done:
            return
        for _p, _info in list(self._pending_done.items()):
            if now - _info.get("t", 0) < self._done_wait_s:
                continue
            self._pending_done.pop(_p, None)
            if _p == now_pid or pid_has_window(_p):
                self._emit("「%s」启动完毕" % _info.get("zh", ""))

    # ---- 自动歌词（任何窗口 + GSMTC 有播放元数据 -> LRCLIB 歌词）----
    def _maybe_lyrics(self, low, now):
        if self.muted:
            return
        # 60 秒节流：GSMTC 探测 + 歌词联网查询有成本，不必每 20 秒跑
        if now - (self.st.get("lyrics_ts") or 0) < 60:
            return
        # 只在【播放器/音乐应用】或【GSMTC 确有播放】时查
        if low not in MUSIC_APPS:
            try:
                import music_info as _mi
                m = _mi.now_playing() or {}
                if not m.get("ok") or not (m.get("title") or "").strip():
                    return
            except Exception:
                return
        self.st["lyrics_ts"] = now
        save_state(self.st)
        try:
            import music_info as _mi
            r = _mi.collect(which=["meta", "lyrics"])
        except Exception as e:
            print("[window_guard] 音乐信息失败: %s" % e, flush=True)
            return
        meta = r.get("now") or {}
        if not meta.get("ok"):
            return
        sig = "%s|%s" % (meta.get("title") or "", meta.get("artist") or "")
        if not sig.strip("|") or sig == self._last_song:
            return
        self._last_song = sig
        head = "♪ %s - %s" % (meta.get("title") or "?", meta.get("artist") or "?")
        ly = (r.get("items") or {}).get("lyrics") or {}
        if ly.get("ok"):
            lines = (ly.get("plain") or "").strip().splitlines()[:2]
            if lines:
                self._emit(head + "\n" + "\n".join(lines))
            else:
                self._emit(head)
        else:
            self._emit(head + "（暂无歌词）")

    # ---- 剪贴板自动翻译（浏览器窗口）/ 自动校对（文档窗口）----
    def _maybe_clipboard(self, low, title, now):
        if self.muted or not title.strip():
            return
        want = None
        if low in BROWSER_APPS:
            want = "translate"
        elif low in DOC_APPS:
            want = "proof"
        else:
            return
        # 剪贴板内容变化才处理；处理间隔 >= 30s（避免复制一下就查一次）
        txt = clip_text()
        if not txt or len(txt.strip()) < 15:
            return
        h = hash(txt)
        if h == self._last_clip_hash:
            return
        if now - self._last_clip_at < 30:
            self._last_clip_hash = h
            return
        self._last_clip_hash = h
        self._last_clip_at = now
        if want == "translate":
            self._translate_clip(txt.strip())
        else:
            self._proof_clip(txt.strip())

    def _translate_clip(self, text):
        # 已有大段中文就不翻（可能只是复制了自己的文案）
        cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
        if cjk / max(len(text), 1) > 0.3:
            return
        if self.progress_cb:
            try:
                self.progress_cb(True, "正在翻译…")
            except Exception:
                pass
        try:
            out = chat([{"role": "user", "content":
                         "你是在线翻译。把下面的文本翻译成简体中文。只输出译文，不要任何解释、不要引号：\n" + text[:1200]}],
                        max_tokens=500, timeout=120)
            out = (out or "").strip().strip('"').strip("“”")
            if out:
                self._emit("🌐 " + out)
            else:
                self._emit("翻译没返回内容")
        except BrainUnavailable:
            pass          # 大脑没开：安静跳过（只在 chat() 里提示一次，不刷屏）
        except Exception as e:
            print("[window_guard] 翻译失败: %s" % e, flush=True)
        finally:
            if self.progress_cb:
                try:
                    self.progress_cb(False, "")
                except Exception:
                    pass

    def _proof_clip(self, text):
        if self.progress_cb:
            try:
                self.progress_cb(True, "正在校对文案…")
            except Exception:
                pass
        try:
            out = chat([{"role": "user", "content":
                         "你是中文校对助手。检查下面的文案的错别字、病句、标点、格式问题。"
                         '只输出 JSON：{"ok": true或false, "issues": [{"msg": "问题", "suggest": "改法"}]}'
                         "没有就 ok=true。文案：\n" + text[:1500]}],
                        max_tokens=600, timeout=150)
            _ok, issues = parse_review(out)
            real = [i for i in issues if (i.get("type") or "") not in ("建议",)]
            if real:
                first = real[0]
                self._emit("✏️ 校对发现 %d 处：%s（改：%s）"
                           % (len(real), (first.get("msg") or "")[:50],
                              (first.get("suggest") or "")[:40]))
            else:
                print("[window_guard] 文案校对通过", flush=True)
        except Exception as e:
            print("[window_guard] 校对失败: %s" % e, flush=True)
        finally:
            if self.progress_cb:
                try:
                    self.progress_cb(False, "")
                except Exception:
                    pass

    # ---- 视频字幕：播放器窗口 + 无歌手元数据（疑似视频）-> 启动字幕 ----
    def _maybe_subtitle(self, low, now):
        if low not in MUSIC_APPS or self.muted:
            return
        # 10 分钟一次（启动字幕是重操作，别反复试）
        if now - (self.st.get("subtitle_ts") or 0) < 600:
            return
        self.st["subtitle_ts"] = now
        save_state(self.st)
        try:
            # 已在跑就不重复启动
            import psutil
            for p in psutil.process_iter(["name", "cmdline"]):
                try:
                    if (p.info.get("name") or "").lower().startswith("python") and \
                            any("video_subtitle" in (c or "") for c in (p.info.get("cmdline") or [])):
                        return
                except Exception:
                    continue
        except Exception:
            pass
        try:
            import music_info as _mi
            meta = _mi.now_playing() or {}
        except Exception as e:
            print("[window_guard] 取播放元数据失败: %s" % e, flush=True)
            return
        if not meta.get("ok") or not (meta.get("title") or "").strip():
            return
        if meta.get("artist"):
            return          # 有歌手 = 音乐，歌词已覆盖；视频才开字幕
        self._emit("正在为你开启视频字幕…")
        try:
            import fairy_root
            exe = fairy_root.pythonw_of(sys.executable)
            script = os.path.join(HERE, "video_subtitle.py")
            subprocess.Popen([exe, script, "--run", "--subtitle"],
                             stdout=open(os.devnull, "w"), stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, creationflags=fairy_root.CREATE_NO_WINDOW)
        except Exception as e:
            print("[window_guard] 启动字幕失败: %s" % e, flush=True)

    # ---- 通话翻译：检测到会议/通话应用 -> 轻提示一次 ----
    def _maybe_call(self, low, now):
        if low not in CALL_APPS or self.muted:
            return
        if now - (self.st.get("call_ts") or 0) < 600:
            return
        self.st["call_ts"] = now
        save_state(self.st)
        self._emit("📞 通话翻译可用：说「翻译」或点右键开启")

    # ---- 音量变化提醒：变化 >= 5% 时播报"现在音量为 XX" ----
    def _maybe_volume(self, now):
        if self.muted:
            return
        v = system_volume()
        if v is None:
            return
        if now - self._vol_at < 30:
            return
        if self._last_vol is None:
            self._last_vol = v
            return
        if abs(v - self._last_vol) < 5:
            return
        self._vol_at = now
        self._last_vol = v
        self._emit("现在音量为 %d%%" % v)

    # ---- 代码审核 ----
    def _maybe_review(self, title, now, force=False):
        fname = pick_file(title)
        path = find_file(fname, self.cfg) if fname else None
        if path and os.path.isfile(path):
            self._review_file(path, fname, now, force)
        elif fname and force:
            # 手动审核：找不到文件 → 读剪贴板兜底
            self._review_clipboard(force)

    def _review_file(self, path, fname, now, force=False):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                code = f.read()
        except Exception as e:
            print("[window_guard] 读文件失败 %s: %s" % (path, e), flush=True)
            return
        sig = "%s|%s|%s" % (path, os.path.getsize(path), os.path.getmtime(path))
        if not force and sig == self._last_review_sig:
            return
        if not force and now - (self.st.get("review_ts") or 0) < self.cfg.get("review_interval_s", 120):
            return
        self.st["review_ts"] = now
        self.st["review_file"] = path
        self.st["review_sig"] = sig
        save_state(self.st)
        self._run_review(fname or os.path.basename(path), code)

    def _review_clipboard(self, force=False):
        if not force:
            return
        txt = clip_text()
        if not txt or len(txt.strip()) < 30:
            self._emit("没找到当前文件，剪贴板里也没有代码")
            return
        self._emit("剪贴板里有一段代码，开始审核")
        self._run_review("剪贴板", txt)

    def _run_review(self, fname, code):
        if self.progress_cb:
            try:
                self.progress_cb(True, "正在审核代码…")
            except Exception:
                pass
        lines = code.splitlines()
        if len(lines) > self.cfg.get("review_max_lines", 400):
            code = "\n".join(lines[: self.cfg.get("review_max_lines", 400)])
            code += "\n# ...（截断，只审前 %d 行）" % self.cfg.get("review_max_lines", 400)
        try:
            out = chat([{"role": "user", "content": review_prompt(fname, code)}])
            print("[window_guard] 审核回复(截断): %s" % out[:120].replace("\n", " "), flush=True)
            ok, issues = parse_review(out)
        except BrainUnavailable:
            # 大脑没开：安静跳过（chat() 里已提示过一次），不假装"审核失败"
            return
        except Exception as e:
            print("[window_guard] 审核调用失败: %s" % e, flush=True)
            self._emit("代码审核调用失败，大脑没响应")
            return
        finally:
            if self.progress_cb:
                try:
                    self.progress_cb(False, "")
                except Exception:
                    pass
        real = [i for i in issues if (i.get("type") or "") in ("错误", "警告", "error", "warning")]
        if real:
            first = real[0]
            line = first.get("line") or 0
            msg = (first.get("msg") or "").strip()
            head = "行%s：" % line if line else ""
            self._emit("代码审核发现 %d 个问题：%s%s" % (len(real), head, msg[:60]))
        elif issues and ok is None:
            self._emit(issues[0].get("msg") or "代码审核没解析出结果")
        else:
            # 没报错就不吵（低打扰）
            print("[window_guard] %s 审核通过（%d 条建议）"
                  % (fname, len([i for i in issues if (i.get("type") or "") == "建议"])), flush=True)


# ---- CLI ----
def cmd_once(cfg):
    g = WindowGuard()
    title, pname = foreground()
    print("前台窗口: %s | %s" % (title, pname))
    print("应用名  : %s" % app_zh(pname, title))
    print("文件名  : %s" % pick_file(title))
    g.tick(force=False)
    print("播报状态: last_app=%s muted=%s" % (g.st.get("last_app"), g.muted))


def cmd_selftest():
    g = WindowGuard(say=lambda t, level="notice": print("[播报] %s" % t),
                    bubble=lambda t: print("[气泡] %s" % t))
    cases = [
        ("main.py — 我的项目 — Visual Studio Code", "code.exe", 1001),
        ("未命名 - 记事本", "notepad.exe", 1002),
        ("B站 - Google Chrome", "chrome.exe", 1003),
        ("", "explorer.exe", 1004),
        ("Fairy", "pythonw.exe", 1005),
        # 同一应用再切一次 -> 不再提示（验证只提示一次）
        ("另一个窗口 - Google Chrome", "chrome.exe", 1003),
        ("新建进程的 Chrome", "chrome.exe", 2001),
    ]
    for title, app, pid in cases:
        print("--- %s | %s | pid=%s" % (title, app, pid))
        g.tick(force=False, obs={"title": title, "app": app, "pid": pid})
    # 审核链路只测到"找文件"为止（不真调大脑）
    print("--- 审核找文件测试（只测提取+查找，不调大脑）")
    t = "fairy.py — FairyX — Visual Studio Code"
    f = pick_file(t)
    print("提取: %s" % f)
    print("找到: %s" % find_file(f, g.cfg))


def cmd_status():
    g = WindowGuard()
    print("=== window_guard 状态 ===")
    print("配置: announce=%s review=%s muted=%s" % (g.cfg.get("announce"), g.cfg.get("review"), g.muted))
    print("上次播报: %s（%s）" % (g.st.get("last_app"), time.strftime("%H:%M:%S", time.localtime(g.st.get("last_ts") or 0))))
    print("上次审核: %s" % (g.st.get("review_file") or "无"))
    print("审核时间: %s" % time.strftime("%H:%M:%S", time.localtime(g.st.get("review_ts") or 0)))


def cmd_review_now():
    g = WindowGuard()
    title, pname = foreground()
    print("前台: %s | %s" % (title, pname))
    g.tick(force=True)
    print("完成（结果走播报/气泡；直接跑时无回调，看日志）")


def cmd_mute(unmute=False):
    g = WindowGuard()
    g.muted = not unmute
    g.st["muted"] = g.muted
    save_state(g.st)
    print("窗口播报已%s" % ("静音" if g.muted else "恢复"))


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Fairy 窗口播报 + 代码审核守护")
    ap.add_argument("--once", action="store_true", help="跑一轮就退")
    ap.add_argument("--selftest", action="store_true", help="注入假情境自测")
    ap.add_argument("--status", action="store_true", help="看状态")
    ap.add_argument("--review-now", action="store_true", help="立即审核当前编辑器文件/剪贴板")
    ap.add_argument("--mute", action="store_true", help="静音窗口播报")
    ap.add_argument("--unmute", action="store_true", help="取消静音")
    a = ap.parse_args()
    if a.selftest:
        cmd_selftest()
    elif a.status:
        cmd_status()
    elif a.review_now:
        cmd_review_now()
    elif a.mute:
        cmd_mute(False)
    elif a.unmute:
        cmd_mute(True)
    else:
        cmd_once(load_config())


if __name__ == "__main__":
    main()
