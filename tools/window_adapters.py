# -*- coding: utf-8 -*-
r"""
window_adapters.py —— Fairy 的【窗口发现 / 识别 / 读取 / 操作】层

【一句话】
  自动发现屏幕上的窗口 -> 认出它是哪个程序 -> 读到里面的内容 -> （显式授权后）操作它。

【为什么要做成「适配器 + 注册表」】
  用户的原话是：以后还会有其他的 AI、其他的软件，都要能自动对接。
  所以这里【绝不写死程序名】（写死 19387 会导致 DSH 一关球就哑）。
  · 发现层   : 枚举顶层窗口 -> 拿 标题/类名/进程/PID，这是「这是什么程序」的唯一依据
  · 注册表   : 配置在 fairy.json 的 "windows" 段。想接一个新软件 = 加一条配置（可能再加一个适配器）
  · 适配器   : 每个程序一个类，实现 read() / type_text()。共用发现层与安全闸
  · 安全闸   : 白名单 + 默认只读 + 危险动作要确认 + 全程日志（三道闸，见 Safety）

【三道闸·硬要求】用户明确要求过「操作」能力，但误操作后果是真的，所以：
  ① 白名单：只有 fairy.json 里 enabled=true 的程序才会被碰；没登记的窗口一律不碰
  ② 默认只读：mode="read" 只能看。要打字/点击必须显式把 mode 改成 "write"；
     点击还要额外把按钮名写进 allow_click_names —— 没写 = 一个都点不了
  ③ 全程日志：每次读/写/点都写 <项目根>/logs/window_ops.jsonl，可追溯

【本机实测到的关键事实】（2026-10-10，详见交付报告）
  · 本机【没装】uiautomation / pywinauto；但【装了 comtypes】
    -> 用 comtypes 直接调 Windows UIAutomationCore.dll 就能拿到完整控件树，零新依赖
  · DSH 的 GUI 是 Chromium：窗口 UIA 树 802 节点 / 438 个 Text 控件，能读到对话正文
  · 豆包 PC 客户端也是 Chromium 外壳（Chrome_WidgetWin_1）
    -> ★ 它的 a11y 树是【按需激活】的：第一次查询之后要等 ~1.5 秒才完整，
       浅层遍历只会看到空 Pane（我第一次就以为读不到，实际读得到）
  · 豆包输入框是 Tiptap/ProseMirror 的 contenteditable，UIA 里是 Group 不是 Edit
    -> 所以「按 Ctrl 找 Edit 控件」这条路在豆包上必然失败

【用法】
  命令行：
    py -3.12 window_adapters.py --scan              列出当前所有顶层窗口（识别基础）
    py -3.12 window_adapters.py --discover          已登记程序现在在不在线
    py -3.12 window_adapters.py --read dsh          读 DSH（HTTP + 窗口双通道）
    py -3.12 window_adapters.py --read doubao       读豆包窗口正文
    py -3.12 window_adapters.py --type doubao 文字 --yes      往输入框打字（需 mode=write）
    py -3.12 window_adapters.py --clear doubao --yes          清空输入框
    py -3.12 window_adapters.py --click doubao 发送 --yes     点击（需白名单 + 确认）
    py -3.12 window_adapters.py --click doubao 发送 --yes --danger-ack "确认执行:发送"
                                                              ★ 危险按钮：还要人给的显式口令
    py -3.12 window_adapters.py --selftest          自检（只读，安全）
  代码里：
    import window_adapters as wa
    wa.discover() / wa.read("doubao") / wa.type_text("doubao", "hi", confirm=True)

【硬约束】纯标准库 + comtypes/psutil（本机已装）；缺了不崩，只是对应能力降级。
         不 import 球/耳朵，避免循环依赖。
"""
import json
import os
import re
import sys
import time
import fnmatch

# ---------------------------------------------------------------- 配置根
try:
    import fairy_root as _root
    _LOGS = _root.LOGS
    _CONFIG = _root.CONFIG
except Exception:                                        # pragma: no cover
    _root = None
    _HERE = os.path.dirname(os.path.abspath(__file__))
    _LOGS = os.path.join(os.path.dirname(_HERE), "logs")
    _CONFIG = os.path.join(os.path.dirname(_HERE), "fairy.json")

AUDIT_FILE = os.path.join(_LOGS, "window_ops.jsonl")

# ---------------------------------------------------------------- 默认注册表
# ★ 这里只是「出厂默认」。真正生效的是 fairy.json 的 "windows" 段 —— 用户改配置即可，
#   不用改这个文件。配置里的同名字段会覆盖这里的默认值。
DEFAULT_PROGRAMS = {
    "dsh": {
        "enabled": True,
        "adapter": "dsh",
        "mode": "read",
        "match": {"process": ["DeepSeek Harness.exe"], "class": ["Chrome_WidgetWin_1"]},
        "note": "DeepSeek Harness：HTTP 接口读回复/会话，窗口 UIA 读对话正文",
    },
    "doubao": {
        "enabled": True,
        "adapter": "doubao",
        "mode": "read",
        # ★ 重要-5：只写 process 是【只认进程名】的弱绑定 —— 谁把 evil.exe 改名成
        #   Doubao.exe，就继承了这条程序的全部权限（实测可复现）。
        #   要收紧就往 process_path 里写 exe 完整路径（支持 * 通配），例如：
        #     "match": {"process": ["Doubao.exe"],
        #               "process_path": ["C:\\Program Files\\Doubao\\Doubao.exe"]}
        #   两个都给 = 双重校验（名字对 + 路径对才认）。
        "match": {"process": ["Doubao.exe"], "process_path": []},
        "note": "豆包 PC 客户端（Chromium 外壳，UIA 可读；输入框是 ProseMirror）",
    },
    "_新增程序示例": {
        "enabled": False,
        "adapter": "generic_uia",
        "mode": "read",
        "match": {"process": ["Weixin.exe"], "process_path": [], "class": [],
                  "title_regex": ""},
        "allow_click_names": [],
        "note": "复制这一条改名即可接入新软件：process 填进程名，mode 改 write 才能打字；"
                "★ 想防「改名冒充」就填 process_path（exe 完整路径，支持 * 通配）",
    },
}

DEFAULT_DANGER_WORDS = [
    # ---- 中文 ----
    "发送", "发出", "提交", "确定", "确认", "删除", "清空", "移除", "卸载",
    "支付", "付款", "购买", "下单", "转账", "关闭", "退出", "结束", "格式化",
    "重启", "关机", "注销", "恢复出厂", "覆盖", "替换",
    "同意", "授权", "允许", "解绑", "退订", "永久", "全部删",
    # ---- ★ 英文（2026-10-10 补：以前只认中文，实测 'SEND'/'Send'/'Delete all'/
    #      'Submit'/'Pay now'/'Confirm' 命中都是 []，一个按钮就能绕过安全闸）----
    "send", "submit", "delete", "remove", "erase", "wipe", "clear", "reset",
    "uninstall", "pay", "purchase", "buy", "transfer", "confirm", "agree",
    "accept", "allow", "close", "quit", "exit", "logout", "log out", "sign out",
    "shutdown", "shut down", "restart", "reboot", "format", "overwrite",
    "replace", "discard", "delete all", "remove all",
    # ---- ★ 拼音（配置/按钮是拼音时同样要拦）----
    "fasong", "tijiao", "queren", "shanchu", "qingkong", "yichu", "xiezai",
    "zhifu", "fukuan", "goumai", "xiadan", "zhuanzhang", "guanbi", "tuichu",
    "jieshu", "geshihua", "chongqi", "guanji", "zhuxiao", "fugai", "tihuan",
]

# ★★ 危险动作的【唯一放行通道】：人给的显式口令，格式 确认执行:<按钮全名>
#   为什么不是 confirm=True：一个布尔量谁都能传（模型自己就能填 True），
#   实测 confirm=True 会让 'Click to delete account' 直接点下去 —— 那等于没有闸。
DANGER_ACK_PREFIX = "确认执行:"

DEFAULT_CFG = {
    "enabled": True,
    "audit_log": True,
    "danger_words": DEFAULT_DANGER_WORDS,
    "programs": DEFAULT_PROGRAMS,
}


def load_cfg():
    """读 fairy.json 的 windows 段；读不到就用出厂默认。"""
    seg = {}
    try:
        with open(_CONFIG, encoding="utf-8") as f:
            data = json.load(f)
        w = data.get("windows")
        if isinstance(w, dict):
            seg = w
    except Exception:
        seg = {}
    # ★★ 重要-1（关键一环）：配置里的 danger_words 以前是【整条替换】默认表。
    #   实测项目根 fairy.json 里正好存着一份【只有中文】的旧词表 ——
    #   于是就算代码里补了英文词，运行时也还是漏（SEND/Delete all 照样过）。
    #   现在改成【并集】：内置词表 ∪ 配置词表。
    #   确实想用自己那一份：在 windows 段写 "danger_words_replace": true。
    user_danger = seg.get("danger_words")
    if isinstance(user_danger, list) and user_danger and not seg.get("danger_words_replace"):
        danger = list(DEFAULT_DANGER_WORDS)
        low = set(str(x).lower() for x in danger)
        for x in user_danger:
            s = str(x or "").strip()
            if s and s.lower() not in low:
                danger.append(s)
                low.add(s.lower())
    elif isinstance(user_danger, list) and user_danger:
        danger = [str(x) for x in user_danger if str(x or "").strip()]
    else:
        danger = list(DEFAULT_DANGER_WORDS)
    cfg = {
        "enabled": bool(seg.get("enabled", DEFAULT_CFG["enabled"])),
        "audit_log": bool(seg.get("audit_log", DEFAULT_CFG["audit_log"])),
        "danger_words": danger,
        "programs": {},
    }
    # 配置里的 programs 覆盖默认（同名整条替换，语义更可预期，不做字段级深合并）
    progs = dict(DEFAULT_PROGRAMS)
    user_progs = seg.get("programs")
    if isinstance(user_progs, dict):
        for k, v in user_progs.items():
            if isinstance(v, dict):
                progs[k] = v
    cfg["programs"] = progs
    return cfg


_CFG = load_cfg()
_CFG_TIME = time.time()
CFG_TTL = 5.0


def cfg(refresh=False):
    """配置带短 TTL 缓存 —— 改完 fairy.json 最多 5 秒生效，不用重启 Fairy。"""
    global _CFG, _CFG_TIME
    if refresh or (time.time() - _CFG_TIME) > CFG_TTL:
        _CFG = load_cfg()
        _CFG_TIME = time.time()
    return _CFG


# ---------------------------------------------------------------- 审计日志
_RING = []


def _audit(op, prog=None, target=None, ok=None, detail=None, extra=None):
    """③ 全程日志：谁被读了、谁被点了、点了什么。写 JSONL，可追溯。"""
    rec = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "op": op, "prog": prog,
           "target": target, "ok": ok, "detail": (detail or "")[:400]}
    if extra:
        rec.update(extra)
    _RING.append(rec)
    del _RING[:-100]
    if not cfg().get("audit_log", True):
        return rec
    try:
        os.makedirs(_LOGS, exist_ok=True)
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return rec


def audit_tail(n=20):
    """读最近 n 条审计记录（从文件，文件没了就用内存环）。"""
    out = []
    try:
        with open(AUDIT_FILE, encoding="utf-8") as f:
            lines = f.readlines()[-n:]
        for ln in lines:
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
    except Exception:
        out = _RING[-n:]
    return out


# ================================================================ 发现层
#  枚举顶层窗口 -> 标题 / 类名 / 进程名 / PID / 矩形
#  这是「这是什么程序」的唯一依据。不猜、不靠窗口标题写死。
import ctypes
import ctypes.wintypes as _wt

_user32 = ctypes.windll.user32

# ★ 重要-3：既然要拿 hwnd 做比较，就必须把返回类型声明对 ——
#   默认 restype 是 c_int，64 位句柄会被截断成负数，比较永远不相等（"守卫永远拦住"）。
try:
    _user32.GetForegroundWindow.restype = _wt.HWND
    _user32.GetWindow.restype = _wt.HWND
    _user32.GetWindow.argtypes = [_wt.HWND, ctypes.c_uint]
    _user32.SetForegroundWindow.argtypes = [_wt.HWND]
    _user32.IsChild.argtypes = [_wt.HWND, _wt.HWND]
    _user32.IsChild.restype = _wt.BOOL
    _user32.GetGUIThreadInfo.argtypes = [_wt.DWORD, ctypes.c_void_p]
except Exception:                                        # pragma: no cover
    pass

try:
    import psutil
    _PSUTIL = True
except Exception:                                        # pragma: no cover
    _PSUTIL = False

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080


class WinInfo(object):
    __slots__ = ("hwnd", "pid", "proc", "cls", "title", "visible", "tool",
                 "left", "top", "right", "bottom", "exe")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def width(self):
        try:
            return int(self.right) - int(self.left)
        except Exception:
            return 0

    @property
    def height(self):
        try:
            return int(self.bottom) - int(self.top)
        except Exception:
            return 0

    @property
    def area(self):
        return self.width * self.height

    def as_dict(self):
        return {k: getattr(self, k) for k in self.__slots__}

    def __repr__(self):
        return "<WinInfo %s pid=%s hwnd=%s %r>" % (self.proc, self.pid, self.hwnd, self.title[:40])


def _proc_name(pid):
    if not _PSUTIL:
        return "?"
    try:
        return psutil.Process(pid).name()
    except Exception:
        return "?"


def _proc_path(pid):
    """
    ★ 重要-5：进程 exe 的【完整路径】。
    为什么需要它：进程名只是一根字符串，把任意 exe 改名成 Doubao.exe 就继承了
    豆包的全部权限（实测：这种做法能让白名单认错程序）。完整路径改不了 ——
    除非真的去替换那个文件。拿不到路径就返回 ""，调用方按【不放行】处理。
    """
    if not _PSUTIL:
        return ""
    try:
        return psutil.Process(pid).exe() or ""
    except Exception:          # 系统进程/权限不足 -> 拿不到
        return ""


def _want_exe(c=None):
    """注册表里有程序用 process_path 校验吗？★ 没有就不查 exe（省掉热路径开销）。"""
    try:
        for prog, spec in ((c or cfg()).get("programs") or {}).items():
            if prog.startswith("_") or not isinstance(spec, dict):
                continue
            m = spec.get("match")
            if isinstance(m, dict) and m.get("process_path"):
                return True
    except Exception:
        pass
    return False


def enum_windows(include_hidden=False, with_title_only=True):
    """
    枚举顶层窗口。返回 [WinInfo]。
      include_hidden   : 是否包含不可见窗口（默认否，只看屏幕上真有的）
      with_title_only  : 是否只留有标题的（默认是，能砍掉 200 多个 IME/隐藏壳窗口）
    """
    rows = []
    _EP = ctypes.WINFUNCTYPE(ctypes.c_bool, _wt.HWND, _wt.LPARAM)
    _need_exe = _want_exe()

    def _cb(hwnd, lparam):
        try:
            n = _user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            _user32.GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value
            cbuf = ctypes.create_unicode_buffer(256)
            _user32.GetClassNameW(hwnd, cbuf, 256)
            cls = cbuf.value
            pid = _wt.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            vis = bool(_user32.IsWindowVisible(hwnd))
            ex = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if not include_hidden and not vis:
                return True
            if with_title_only and not title:
                return True
            rc = _wt.RECT()
            _user32.GetWindowRect(hwnd, ctypes.byref(rc))
            rows.append(WinInfo(hwnd=int(hwnd), pid=pid.value, proc=_proc_name(pid.value),
                                cls=cls, title=title, visible=vis,
                                tool=bool(ex & WS_EX_TOOLWINDOW),
                                exe=(_proc_path(pid.value) if _need_exe else ""),
                                left=rc.left, top=rc.top, right=rc.right, bottom=rc.bottom))
        except Exception:
            pass
        return True

    _user32.EnumWindows(_EP(_cb), 0)
    return rows


def _path_match(win_exe, pats):
    """
    ★ 重要-5：exe【完整路径】比对（大小写不敏感，支持 * 和 ? 通配）。
    进程名可以随便改，路径不行 —— 这是挡住「改名继承权限」的关键一道。
    ★ 拿不到路径时返回 False（拒绝）：失败方向必须偏安全。
    """
    if not pats:
        return True
    if isinstance(pats, str):
        pats = [pats]
    exe = (win_exe or "").replace("/", "\\").strip().lower()
    if not exe:
        return False
    for p in pats:
        p = str(p or "").replace("/", "\\").strip().lower()
        if not p:
            continue
        if ("*" in p or "?" in p) and fnmatch.fnmatch(exe, p):
            return True
        if exe == p:
            return True
    return False


def _match_one(win, m):
    """窗口是否命中一条 match 规则。字段之间是「与」，字段内是「或」。"""
    if not isinstance(m, dict):
        return False
    procs = m.get("process") or []
    if isinstance(procs, str):
        procs = [procs]
    if procs and (win.proc or "").lower() not in [str(p).lower() for p in procs]:
        return False
    # ★ 重要-5：process_path —— 校验 exe 完整路径（和 process 一起给就是双重校验）
    paths = m.get("process_path")
    if not _path_match(getattr(win, "exe", ""), paths):
        return False
    classes = m.get("class") or []
    if isinstance(classes, str):
        classes = [classes]
    if classes and (win.cls or "") not in classes:
        return False
    rx = m.get("title_regex")
    if rx:
        try:
            if not re.search(rx, win.title or ""):
                return False
        except Exception:
            return False
    return bool(procs or classes or rx or paths)


def identify(win, c=None):
    """这个窗口是注册表里的哪个程序？返回 (prog, spec) 或 (None, None)。"""
    c = c or cfg()
    for prog, spec in (c.get("programs") or {}).items():
        if prog.startswith("_"):
            continue
        if not isinstance(spec, dict) or not spec.get("enabled"):
            continue
        if _match_one(win, spec.get("match")):
            return prog, spec
    return None, None


def find_window(prog, c=None):
    """
    找一个已登记程序的可见窗口。同一进程有多个窗口时，取面积最大的那个。
    返回 (WinInfo, spec) 或 (None, None)。
    """
    c = c or cfg()
    spec = (c.get("programs") or {}).get(prog)
    if not isinstance(spec, dict):
        return None, None
    if not spec.get("enabled"):
        return None, spec
    hits = [w for w in enum_windows() if _match_one(w, spec.get("match"))]
    if not hits:
        return None, spec
    hits.sort(key=lambda w: -w.area)
    return hits[0], spec


def scan(only_registered=False):
    """给报告/排障用：当前所有顶层窗口 + 它是不是已登记的程序。"""
    c = cfg()
    out = []
    for w in sorted(enum_windows(include_hidden=False, with_title_only=True), key=lambda x: (x.proc or "").lower()):
        prog, _ = identify(w, c)
        if only_registered and not prog:
            continue
        d = w.as_dict()
        d["registered_as"] = prog
        out.append(d)
    return out


def discover():
    """★ 自动发现：注册表里每个程序，现在在不在屏幕上、窗口是哪个。"""
    c = cfg()
    found = []
    for prog, spec in (c.get("programs") or {}).items():
        if prog.startswith("_") or not isinstance(spec, dict) or not spec.get("enabled"):
            continue
        w, _ = find_window(prog, c)
        item = {"prog": prog, "adapter": spec.get("adapter"), "mode": spec.get("mode", "read"),
                "online": bool(w), "note": spec.get("note", "")}
        if w:
            item.update({"hwnd": w.hwnd, "pid": w.pid, "process": w.proc,
                         "class": w.cls, "title": w.title, "rect": [w.left, w.top, w.right, w.bottom]})
        found.append(item)
    _audit("discover", ok=True, detail="在线 %d/%d" % (sum(1 for f in found if f["online"]), len(found)))
    return found


# ================================================================ UIA 后端
#  ★ 本机没装 uiautomation/pywinauto —— 但装了 comtypes。
#    comtypes 可以直接把 UIAutomationCore.dll 的类型库变成 Python 接口，零新依赖。
CT_NAMES = {
    50000: "Button", 50001: "Calendar", 50002: "CheckBox", 50003: "ComboBox",
    50004: "Edit", 50005: "Hyperlink", 50006: "Image", 50007: "ListItem",
    50008: "List", 50009: "Menu", 50010: "MenuBar", 50011: "MenuItem",
    50012: "ProgressBar", 50013: "RadioButton", 50014: "ScrollBar", 50015: "Slider",
    50016: "Spinner", 50017: "StatusBar", 50018: "Tab", 50019: "TabItem",
    50020: "Text", 50021: "ToolBar", 50022: "ToolTip", 50023: "Tree",
    50024: "TreeItem", 50025: "Custom", 50026: "Group", 50027: "Thumb",
    50028: "DataGrid", 50029: "DataItem", 50030: "Document", 50031: "SplitButton",
    50032: "Window", 50033: "Pane", 50034: "Header", 50035: "HeaderItem",
    50036: "Table", 50037: "TitleBar", 50038: "Separator",
}
CT_TEXT = 50020
CT_BUTTON = 50000
CT_EDIT = 50004


class UiaUnavailable(Exception):
    pass


class Uia(object):
    """UIA 单例。懒加载 —— 没装 comtypes 时只是这里报错，不影响模块导入。"""

    _inst = None

    def __init__(self):
        import comtypes.client
        comtypes.client.GetModule("UIAutomationCore.dll")
        import comtypes.gen.UIAutomationClient as UIA
        self.UIA = UIA
        self.uia = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)
        self.walker = self.uia.RawViewWalker

    @classmethod
    def get(cls):
        if cls._inst is None:
            cls._inst = cls()
        return cls._inst

    # ---- 基础 ----
    def element_from_handle(self, hwnd):
        return self.uia.ElementFromHandle(hwnd)

    def cond(self, prop, value):
        return self.uia.CreatePropertyCondition(prop, value)

    def find_first(self, el, prop, value):
        """
        ★ 注意：comtypes 把「没找到」包成一个【NULL 指针对象】而不是 Python None。
          用 `x or fallback` 是没用的（那个对象是真值），后面一取属性就
          'NULL COM pointer access'。所以这里主动探活，拿不到就返回 None。
        """
        try:
            r = el.FindFirst(self.UIA.TreeScope_Descendants, self.cond(prop, value))
        except Exception:
            return None
        if r is None:
            return None
        try:
            r.CurrentControlType          # 探活
        except Exception:
            return None
        return r

    def find_all(self, el, prop=None, value=None, scope=None):
        U = self.UIA
        c = self.cond(prop, value) if prop is not None else self.uia.RawViewCondition
        return el.FindAll(scope if scope is not None else U.TreeScope_Descendants, c)

    def children(self, el):
        U = self.UIA
        try:
            return el.FindAll(U.TreeScope_Children, self.uia.RawViewCondition)
        except Exception:
            return None

    @staticmethod
    def rect(el):
        try:
            r = el.CurrentBoundingRectangle
        except Exception:
            return None
        try:
            return (int(r.left), int(r.top), int(r.right), int(r.bottom))
        except Exception:
            try:
                return (int(r[0]), int(r[1]), int(r[2]), int(r[3]))
            except Exception:
                return None

    def ancestors(self, el, maxn=40):
        out = []
        cur = el
        while cur is not None and len(out) < maxn:
            out.append(cur)
            try:
                cur = self.walker.GetParentElement(cur)
            except Exception:
                break
        return out

    def texts(self, el, limit=3000):
        """按树序（= DOM 阅读顺序）收集 Text 控件的文字 + 位置。"""
        out = []
        try:
            arr = self.find_all(el, self.UIA.UIA_ControlTypePropertyId, CT_TEXT)
        except Exception:
            return out
        for i in range(min(arr.Length, limit)):
            try:
                e = arr.GetElement(i)
                nm = (e.CurrentName or "").strip()
                if nm:
                    out.append({"text": nm, "rect": self.rect(e), "el": e})
            except Exception:
                pass
        return out

    def value_of(self, el):
        U = self.UIA
        for pid_ in (U.UIA_ValuePatternId, U.UIA_LegacyIAccessiblePatternId):
            try:
                p = el.GetCurrentPattern(pid_)
                if pid_ == U.UIA_ValuePatternId:
                    return p.QueryInterface(U.IUIAutomationValuePattern).CurrentValue
                return p.QueryInterface(U.IUIAutomationLegacyIAccessiblePattern).CurrentValue
            except Exception:
                continue
        return None

    def set_value(self, el, text):
        """返回值: (成功?, 用了哪个方法, 错误说明)。"""
        U = self.UIA
        tried = []
        for name, pid_ in (("ValuePattern", U.UIA_ValuePatternId),
                           ("LegacyIAccessible", U.UIA_LegacyIAccessiblePatternId)):
            try:
                p = el.GetCurrentPattern(pid_)
                if pid_ == U.UIA_ValuePatternId:
                    p.QueryInterface(U.IUIAutomationValuePattern).SetValue(text)
                else:
                    p.QueryInterface(U.IUIAutomationLegacyIAccessiblePattern).SetValue(text)
                return True, name, ""
            except Exception as e:
                tried.append("%s:%s" % (name, str(e)[:70]))
        return False, "", " | ".join(tried)

    def focus(self, el):
        try:
            el.SetFocus()
            return True, ""
        except Exception as e:
            return False, str(e)[:90]

    def invoke(self, el):
        """点一下。★ 只应由 check_click() 放行之后调用。"""
        U = self.UIA
        try:
            p = el.GetCurrentPattern(U.UIA_InvokePatternId)
            p.QueryInterface(U.IUIAutomationInvokePattern).Invoke()
            return True, "InvokePattern"
        except Exception as e1:
            try:
                p = el.GetCurrentPattern(U.UIA_LegacyIAccessiblePatternId)
                p.QueryInterface(U.IUIAutomationLegacyIAccessiblePattern).DoDefaultAction()
                return True, "LegacyIAccessible.DoDefaultAction"
            except Exception as e2:
                return False, "InvokePattern:%s / Legacy:%s" % (str(e1)[:50], str(e2)[:50])


def uia():
    try:
        return Uia.get()
    except Exception as e:
        raise UiaUnavailable("UIA 不可用：%s" % str(e)[:120])


# ---- 键盘/剪贴板（只在显式授权后用到，全部懒加载）----
GW_OWNER = 4


def _fg_hwnd():
    """当前前台窗口句柄（0 = 拿不到）。"""
    try:
        return int(_user32.GetForegroundWindow() or 0)
    except Exception:
        return 0


def _hwnd_pid(hwnd):
    try:
        pid = _wt.DWORD()
        _user32.GetWindowThreadProcessId(_wt.HWND(int(hwnd)), ctypes.byref(pid))
        return pid.value
    except Exception:
        return None


def _same_window_tree(win_hwnd, hwnd):
    """
    hwnd 属不属于目标窗口这一棵「窗口树」（本体 / 子控件 / 它自己的弹出菜单）。
    ★ 重要-3：判断"前台是不是目标"必须比 hwnd，不能比 pid ——
      本机实测有同 pid 多窗口（pythonw.exe 2 个窗口、explorer 2 个），
      以前 `foreground_pid() == win.pid` 会把字打进了【另一个窗口】。
    """
    try:
        a, b = int(win_hwnd or 0), int(hwnd or 0)
        if not a or not b:
            return False
        if a == b:
            return True
        if _user32.IsChild(_wt.HWND(a), _wt.HWND(b)):
            return True
        if int(_user32.GetWindow(_wt.HWND(b), GW_OWNER) or 0) == a:
            return True
    except Exception:
        return False
    return False


def foreground_pid():
    """当前前台窗口属于哪个进程。★ 粘贴之前必须核对它 —— 见 type_text 里的守卫。"""
    return _hwnd_pid(_fg_hwnd())


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", _wt.DWORD), ("flags", _wt.DWORD),
                ("hwndActive", _wt.HWND), ("hwndFocus", _wt.HWND),
                ("hwndCapture", _wt.HWND), ("hwndMenuOwner", _wt.HWND),
                ("hwndMoveSize", _wt.HWND), ("hwndCaret", _wt.HWND),
                ("rcCaret", _wt.RECT)]


def focused_hwnd():
    """
    当前【真正有键盘焦点】的控件。粘贴的 Ctrl+V 是发给它的，不是发给前台窗口的，
    所以重要-3 要求"粘贴前再核一次焦点控件"。拿不到就返回 0。
    """
    try:
        tid = _user32.GetWindowThreadProcessId(_wt.HWND(_fg_hwnd()), None)
        if not tid:
            return 0
        gti = _GUITHREADINFO()
        gti.cbSize = ctypes.sizeof(_GUITHREADINFO)
        if _user32.GetGUIThreadInfo(tid, ctypes.byref(gti)):
            return int(gti.hwndFocus or 0)
    except Exception:
        pass
    return 0


def _check_focus(win):
    """
    ★ 重要-3 的复核：前台窗口必须是目标 hwnd（不是同 pid 的别的窗口），
    且焦点控件必须属于目标进程。返回 (ok, 说明)。
    拿不到焦点信息时不否决（前台已经核对过），但绝不因为"pid 一样"就放行。
    """
    fg = _fg_hwnd()
    if not _same_window_tree(win.hwnd, fg):
        return False, ("前台 hwnd=%s 不是目标 hwnd=%s（目标 pid=%s，前台 pid=%s）"
                       % (fg, win.hwnd, win.pid, _hwnd_pid(fg)))
    f = focused_hwnd()
    if f:
        fpid = _hwnd_pid(f)
        if fpid is not None and win.pid is not None and fpid != win.pid:
            return False, "焦点控件 hwnd=%s 属于 pid=%s，不是目标 pid=%s" % (f, fpid, win.pid)
    return True, "前台与焦点都在目标窗口上"


def _ensure_foreground(win, tries=12):
    """
    ★★ 把目标窗口置前台，并【确认前台真的是它】。
    为什么必须做：粘贴/按键是发给「当前前台窗口」的，不是发给「你手里那个 hwnd」的。
    ★ 实测踩到过：不置前台就发 Ctrl+A/Delete，字没清掉（按键打到别处去了）。
    返回 (ok, 说明)。拿不到前台就【拒绝】，绝不硬发按键。
    """
    try:
        if _same_window_tree(win.hwnd, _fg_hwnd()):
            return True, "已经在前台"
        _user32.SetForegroundWindow(_wt.HWND(int(win.hwnd)))
        for _ in range(tries):
            time.sleep(0.12)
            if _same_window_tree(win.hwnd, _fg_hwnd()):
                return True, "已置前台"
    except Exception as e:
        return False, "置前台失败: %s" % str(e)[:80]
    return False, "没能把窗口 %s 置到前台（现在前台 hwnd=%s）" % (win.hwnd, _fg_hwnd())


def _pyautogui():
    import pyautogui
    return pyautogui


_CLIP_UNKNOWN = "unknown"       # 判断不了剪贴板里有没有文本 -> 宁可不动它


def _clip_read_text():
    """
    ★ 重要-4：备份前先问一句「剪贴板里到底有没有文本」。
    返回 (状态, 文本)，状态 ∈ {True, False, _CLIP_UNKNOWN}。
    为什么：剪贴板里放的是图片/文件时 pyperclip.paste() 返回空串，
    拿这个空串当备份去还原，等于把用户复制的图片抹掉了。
    """
    try:
        if not _user32.IsClipboardFormatAvailable(13):      # 13 = CF_UNICODETEXT
            return False, ""
    except Exception:
        return _CLIP_UNKNOWN, ""
    try:
        import pyperclip
        return True, pyperclip.paste()
    except Exception:
        return _CLIP_UNKNOWN, ""


def _clip_write_text(text):
    try:
        import pyperclip
        pyperclip.copy(text)
        return True
    except Exception:
        return False


def _paste(text):
    """
    剪贴板粘贴。返回 (成功?, 说明)。中文必须走这条路，逐字符 SendInput 会乱。
    ★★ 重要-4：用完要【还原用户原来的剪贴板】。
      实测：改造前 `pyperclip.copy(text)` 之后直接 Ctrl+V 就完事 ——
      用户原来剪贴板里的东西被永久换成了我们刚打的字。
    """
    state, backup = _clip_read_text()
    try:
        import pyperclip
        pyperclip.copy(text)
    except Exception as e:
        return False, "剪贴板写入失败: %s" % str(e)[:80]
    try:
        _pyautogui().hotkey("ctrl", "v")
    except Exception as e:
        if state is True:                       # 失败也要还原，别留我们的字在里面
            _clip_write_text(backup)
        return False, "粘贴按键失败: %s" % str(e)[:80]
    if state is True:
        # ★ 必须等目标程序真的处理完 Ctrl+V 再还原，立刻还原会粘出旧内容
        time.sleep(0.45)
        if not _clip_write_text(backup):
            return True, "clipboard+ctrl-v（★ 原剪贴板还原失败）"
        return True, "clipboard+ctrl-v（原剪贴板已还原）"
    if state is False:
        return True, "clipboard+ctrl-v（原剪贴板不是文本，未动它）"
    return True, "clipboard+ctrl-v（原剪贴板读不到，未还原）"


def _select_all_delete():
    """全选 + 删除。★ 删除后再按一下 Esc —— Ctrl+A 会在被控程序里弹出「复制/朗读」那种
    浮动工具条，不关掉的话用户的屏幕上会留一块东西（实测过）。"""
    try:
        g = _pyautogui()
        g.hotkey("ctrl", "a")
        time.sleep(0.15)
        g.press("delete")
        time.sleep(0.2)
        g.press("escape")
        return True, ""
    except Exception as e:
        return False, str(e)[:90]


# ================================================================ 适配器
class Adapter(object):
    """适配器基类。加一个新软件 = 实现 read()（要能操作再加 type_text()）。"""
    name = "base"

    def __init__(self, prog, spec):
        self.prog = prog
        self.spec = spec or {}

    def read(self, win):
        raise NotImplementedError

    def type_text(self, win, text):
        return {"ok": False, "error": "适配器 %s 不支持打字" % self.name}


class GenericUiaAdapter(Adapter):
    """
    通用兜底：任何窗口，把 UIA 树里的 Text 控件按阅读顺序读出来。
    读不到文字时【如实说读不到】，不做猜测。
    """
    name = "generic_uia"

    def read(self, win):
        t0 = time.time()
        u = uia()
        root = u.element_from_handle(win.hwnd)
        time.sleep(1.5)          # ★ Chromium 的 a11y 是按需激活的，等它一次
        items = u.texts(root)
        text = "\n".join(i["text"] for i in items)
        return {"ok": True, "adapter": self.name, "items": items, "text": text,
                "count": len(items), "ms": int((time.time() - t0) * 1000),
                "meta": {"method": "uia-text-descendants",
                         "honest_note": "" if items else "UIA 树里没读到任何文字（可能是自绘界面/画布程序）"}}


class DshAdapter(Adapter):
    """
    DeepSeek Harness。★ 双通道 —— 这是和别的程序最大的不同：
      ① HTTP：/api/fairy/reply 拿「助手最新回复」（球本来就在轮询它，零新增设施）
      ② 会话投影：读 DSH 的 session_projcache，拿「每一轮：用户问了什么 + AI 答了什么」
      ③ 窗口 UIA：读 DSH 窗口里【渲染出来的对话正文】（不依赖任何接口权限）
    三者互补：HTTP 最实时，投影最结构化，窗口最不怕接口变动。
    """
    name = "dsh"

    def __init__(self, prog, spec):
        Adapter.__init__(self, prog, spec)
        self.sessions_root = os.path.expanduser(r"~\.dsh\storages\session_projcache\sessions")

    # ---- ① HTTP ----
    def _http_reply(self):
        base = None
        try:
            import fairy_endpoints as ep
            base = ep.dsh_base()
        except Exception:
            try:
                with open(_CONFIG, encoding="utf-8") as f:
                    base = (json.load(f).get("endpoints") or {}).get("dsh_base")
            except Exception:
                base = None
        base = (base or "http://127.0.0.1:19387").rstrip("/")
        import urllib.request
        try:
            req = urllib.request.Request(base + "/api/fairy/reply",
                                         headers={"accept": "application/json",
                                                  "cache-control": "no-store"})
            with urllib.request.urlopen(req, timeout=5) as r:
                return json.loads(r.read().decode("utf-8")), ""
        except Exception as e:
            return None, "%s: %s" % (type(e).__name__, str(e)[:90])

    # ---- ② 会话投影 ----
    def turns(self, session_id=None, limit=8):
        """
        读 DSH 的 journal 投影，拿每一轮的 prompt / response。
        ★ 这是【未文档化的内部结构】，DSH 升级可能变 —— 所以外面包了 try，
          读不到就返回空并说明原因，绝不假装读到了。
        """
        if not os.path.isdir(self.sessions_root):
            return [], "会话投影目录不存在：%s" % self.sessions_root
        sid = session_id
        if not sid:
            # 没指定就挑最近改动的那个
            try:
                fs = [os.path.join(self.sessions_root, f) for f in os.listdir(self.sessions_root)
                      if f.startswith("session-") and f.endswith(".json")]
                fs.sort(key=lambda p: -os.path.getmtime(p))
                if not fs:
                    return [], "投影目录里没有 session-*.json"
                sid = os.path.basename(fs[0])[len("session-"):-len(".json")]
            except Exception as e:
                return [], "挑会话失败: %s" % str(e)[:90]
        p = os.path.join(self.sessions_root, "session-%s.json" % sid)
        try:
            with open(p, encoding="utf-8") as f:
                j = json.load(f)
            rows = j["record"]["rows"]
            val = rows.get("turnOutline", {}).get("val") or {}
            turns = val.get("turns") or []
            title = (rows.get("title", {}).get("val") or "")
        except Exception as e:
            return [], "解析投影失败: %s: %s" % (type(e).__name__, str(e)[:90])
        out = []
        for t in turns[-limit:]:
            out.append({"turn": t.get("turn"), "seq": t.get("seq"),
                        "prompt": (t.get("prompt") or "").strip(),
                        "response": (t.get("response") or "").strip()})
        return out, {"session_id": sid, "title": title,
                     "total_turns": len(turns), "file": p}

    def read(self, win):
        t0 = time.time()
        out = {"ok": True, "adapter": self.name, "items": [], "text": "",
               "meta": {"channels": {}}}
        # ① HTTP
        reply, err = self._http_reply()
        if reply:
            out["meta"]["channels"]["http_reply"] = {
                "ok": True, "text": reply.get("text", ""), "seq": reply.get("seq"),
                "sessionId": reply.get("sessionId"), "turn": reply.get("turn"),
                "truncated": reply.get("truncated"), "fullLength": reply.get("fullLength")}
            out["text"] = reply.get("text", "")
        else:
            out["meta"]["channels"]["http_reply"] = {"ok": False, "error": err}
        # ② 会话投影
        turns, tinfo = self.turns()
        if isinstance(tinfo, dict):
            out["meta"]["channels"]["session_projcache"] = dict(tinfo, ok=True, turns=turns)
            out["meta"]["turns"] = turns
        else:
            out["meta"]["channels"]["session_projcache"] = {"ok": False, "error": tinfo}
        # ③ 窗口 UIA
        if win is not None:
            try:
                u = uia()
                root = u.element_from_handle(win.hwnd)
                time.sleep(1.5)
                items = u.texts(root)
                out["meta"]["channels"]["window_uia"] = {
                    "ok": True, "count": len(items),
                    "preview": [i["text"][:80] for i in items[:12]]}
                out["items"] = items
                if not out["text"]:
                    out["text"] = "\n".join(i["text"] for i in items)
            except Exception as e:
                out["meta"]["channels"]["window_uia"] = {"ok": False, "error": str(e)[:110]}
        out["ms"] = int((time.time() - t0) * 1000)
        out["count"] = len(out["items"])
        return out


class DoubaoAdapter(Adapter):
    """
    豆包 PC 客户端（Chromium 外壳）。

    【怎么定位到「对话正文」而不是「左侧会话列表」】—— 纯几何 + 稳定 AutomationId：
      1) 主区    = aid == "chat-route-main"（豆包自己给的稳定 ID）
      2) 输入区  = aid == "input-engine-container"（内含 Tiptap/ProseMirror 编辑器）
      3) 正文列  = 从输入区往上走，取「最高的、顶边比主区低 10px 以上」的祖先
                   —— 实测这一层正好是滚动正文区，天然排除了左侧栏（x 更小）和标题栏（y 更小）
      4) 正文    = 正文列里的 Text 控件，再去掉与输入区矩形重叠的那些
    为什么不用类名：豆包的类名是 Tailwind/CSS-Module 哈希（panel-fvxXfj / container-KCOKpE），
    升级一次就变；aid 和几何关系稳得多。
    """
    name = "doubao"
    AID_MAIN = "chat-route-main"
    AID_INPUT = "input-engine-container"
    CLS_EDITOR = "tiptap ProseMirror"
    # 输入框里那排固定按钮的文字，读"现在输入了什么"时要滤掉
    CHROME_WORDS = {"项目", "全部允许", "技能", "插件"}
    PLACEHOLDER_HINTS = ("发消息或创建任务", "使用技能", "添加资料")

    def _anchor(self, win):
        u = uia()
        root = u.element_from_handle(win.hwnd)
        time.sleep(1.5)                      # ★ 等 Chromium 按需激活 a11y，否则树是浅的
        composer = u.find_first(root, u.UIA.UIA_AutomationIdPropertyId, self.AID_INPUT)
        editor = self._find_editor(u, root)
        # chat-route-main 只是诊断用：★ 实测它会随布局变化消失，不能当锚点
        main = u.find_first(root, u.UIA.UIA_AutomationIdPropertyId, self.AID_MAIN)
        return u, root, main, composer, editor

    def _find_editor(self, u, root):
        """
        找 ProseMirror 编辑区。
        ★ 不能用【类名精确匹配】：输入框一聚焦，类名就从 'tiptap ProseMirror' 变成
          'tiptap ProseMirror ProseMirror-focused'，精确匹配会突然找不到
          （这个坑真踩到了：打完字之后就读不到编辑器了）。改成找【包含 ProseMirror】的。
        """
        cont = u.find_first(root, u.UIA.UIA_AutomationIdPropertyId, self.AID_INPUT)
        if cont is None:
            return None
        try:
            arr = cont.FindAll(u.UIA.TreeScope_Descendants, u.uia.RawViewCondition)
        except Exception:
            return None
        for i in range(arr.Length):
            try:
                e = arr.GetElement(i)
                if "ProseMirror" in (e.CurrentClassName or ""):
                    return e
            except Exception:
                pass
        return None

    def composer_text(self, u, composer, target):
        """
        读输入框里【现在到底有什么字】。
        ★ 这是唯一可靠的读法：ProseMirror 的 ValuePattern.CurrentValue 实测是坏的
          —— 空的时候返回 ''，有字的时候返回 None，两种情况都不对。
          只能用 Text 子控件，再滤掉占位符和「项目/技能/插件」那排按钮。
        """
        el = composer if composer is not None else target
        out = []
        for it in u.texts(el):
            t = (it["text"] or "").strip()
            if not t or t in self.CHROME_WORDS:
                continue
            if any(h in t for h in self.PLACEHOLDER_HINTS):
                continue
            out.append(t)
        return "\n".join(out)

    def _body_column(self, u, main, composer):
        """
        ★ 唯一锚点是【输入框】—— 它每个会话页都在（实测 chat-route-main 会消失，它不会）。
        规则：从输入框往上走，取「顶边最高、但仍明显在输入框上方」的那个祖先。
              实测这一层正好是正文滚动区：
                · 左侧栏在它左边（x 更小）  -> 天然排除会话列表
                · 标题栏在它上方（y 更小）  -> 天然排除「现在的仓库 = 纯发布版」
        """
        if composer is None:
            return None
        cr = u.rect(composer)
        if not cr:
            return None
        best, best_top = None, None
        for el in u.ancestors(composer, 25):
            r = u.rect(el)
            if not r:
                continue
            if r[1] < cr[1] - 50:            # 明显在输入框上方
                if best_top is None or r[1] > best_top:
                    best, best_top = el, r[1]
        return best

    def read(self, win):
        t0 = time.time()
        u, root, main, composer, editor = self._anchor(win)
        col = self._body_column(u, main, composer)
        if col is None:
            return {"ok": False, "adapter": self.name, "items": [], "text": "",
                    "error": "定位不到正文列（豆包界面结构可能变了）",
                    "meta": {"found": {"main": bool(main), "composer": bool(composer),
                                       "editor": bool(editor)}},
                    "ms": int((time.time() - t0) * 1000)}
        cr = u.rect(composer) if composer is not None else None
        col_r = u.rect(col)
        items = []
        for it in u.texts(col):
            r = it["rect"]
            if cr and r:
                # 去掉和输入框矩形重叠的（占位符、「项目/技能/插件」那排）
                if not (r[3] <= cr[1] + 2 or r[1] >= cr[3] - 2 or r[2] <= cr[0] or r[0] >= cr[2]):
                    continue
            items.append(it)
        text = "\n".join(i["text"] for i in items)
        return {"ok": True, "adapter": self.name, "items": items, "text": text,
                "count": len(items), "ms": int((time.time() - t0) * 1000),
                "meta": {"method": "aid-anchor + geometry", "body_col_rect": col_r,
                         "composer_rect": cr,
                         "input_now": self.composer_text(u, composer, editor),
                         "honest_note": "" if items else "正文列里没读到文字"}}

    # ---- 写：只碰输入框，绝不碰发送键 ----
    def type_text(self, win, text):
        t0 = time.time()
        u, root, main, composer, editor = self._anchor(win)
        target = editor or composer
        if target is None:
            return {"ok": False, "error": "找不到豆包输入框（aid=%s）" % self.AID_INPUT,
                    "ms": int((time.time() - t0) * 1000)}
        before = self.composer_text(u, composer, target)
        ok, method, err = u.set_value(target, text)
        if not ok:
            # ★ 顺序很重要：先让 UIA SetFocus 把焦点给目标元素
            #   —— UIA 的 SetFocus 通常能把窗口带到前台；而 SetForegroundWindow
            #   对「不是前台进程的后台程序」会被 Windows 前台锁挡掉，单独用它必失败。
            #   两步都试完还是不在前台 -> 拒绝发按键（绝不把字打进别的窗口）。
            foc, ferr = u.focus(target)
            time.sleep(0.35)
            # ★ 重要-3：比 hwnd，不比 pid（同 pid 多窗口的机器上 pid 比较会放错窗口）
            if not _same_window_tree(win.hwnd, _fg_hwnd()):
                fg, fgmsg = _ensure_foreground(win)
                if not fg:
                    return {"ok": False,
                            "error": "SetValue 失败(%s)；聚焦也没把窗口带到前台（%s；UIA聚焦=%s）"
                                     " —— 已拒绝发按键，避免把文字打进别的窗口" % (err, fgmsg, foc),
                            "ms": int((time.time() - t0) * 1000)}
            if not foc:
                return {"ok": False, "error": "SetValue 失败(%s)；聚焦也失败(%s)" % (err, ferr),
                        "ms": int((time.time() - t0) * 1000)}
            time.sleep(0.35)
            # ★★ 守卫：剪贴板粘贴是发给【当前有焦点的控件】的。
            #    如果前台/焦点没落到目标窗口上，字就会打进别的窗口 —— 那正是"误操作"。
            #    所以粘贴之前再核一次（前台 hwnd + 焦点控件 pid），不对就【拒绝粘贴】。
            fok, fmsg = _check_focus(win)
            if not fok:
                return {"ok": False,
                        "error": "焦点没落到目标窗口上（%s）—— 已拒绝粘贴，"
                                 "避免把文字打进别的窗口。请先把该窗口点到前台再试。" % fmsg,
                        "ms": int((time.time() - t0) * 1000)}
            ok2, how = _paste(text)
            if not ok2:
                return {"ok": False, "error": "SetValue 失败(%s)；粘贴也失败(%s)" % (err, how),
                        "ms": int((time.time() - t0) * 1000)}
            method = "focus+" + how
        time.sleep(0.5)
        # ★ 复核靠 composer_text（读 Text 子控件），不靠 ValuePattern —— 后者在这里是坏的
        after = self.composer_text(u, composer, target)
        return {"ok": True, "adapter": self.name, "method": method,
                "value_before": before, "value_after": after,
                "verified": bool(text) and (text.strip()[:24] in after),
                "note": "已写入但【没有按回车、没有点发送】。",
                "ms": int((time.time() - t0) * 1000)}

    def clear_input(self, win):
        """把输入框恢复原状（Ctrl+A 全选 + Delete）。不发送、不提交。"""
        t0 = time.time()
        u, root, main, composer, editor = self._anchor(win)
        target = editor or composer
        if target is None:
            return {"ok": False, "error": "找不到输入框"}
        # 同样先 UIA 聚焦（能把窗口带前台），不行再试 SetForegroundWindow，还不行就拒绝
        u.focus(target)
        time.sleep(0.35)
        # ★ 重要-3：比 hwnd（同 pid 多窗口 / 同进程别的窗口都不能算"前台是它"）
        if not _same_window_tree(win.hwnd, _fg_hwnd()):
            fg, fgmsg = _ensure_foreground(win)
            if not fg:
                return {"ok": False, "error": "清空前无法把窗口带到前台：%s —— 已拒绝发按键，避免清错窗口" % fgmsg}
        fok, fmsg = _check_focus(win)
        if not fok:
            return {"ok": False, "error": "清空前焦点不在目标窗口上（%s）—— 已拒绝发按键" % fmsg}
        time.sleep(0.25)
        ok, err = _select_all_delete()
        time.sleep(0.4)
        after = self.composer_text(u, composer, target)
        return {"ok": ok, "value_after": after, "error": err,
                "cleared": not (after or "").strip(), "ms": int((time.time() - t0) * 1000)}


ADAPTERS = {
    "generic_uia": GenericUiaAdapter,
    "dsh": DshAdapter,
    "doubao": DoubaoAdapter,
}


def _adapter_for(prog, spec):
    cls = ADAPTERS.get((spec or {}).get("adapter") or "generic_uia")
    if cls is None:
        return None
    return cls(prog, spec)


# ================================================================ 三道闸
class Denied(Exception):
    """安全闸拒绝。异常里带的是【人话原因】。"""


def _norm(s):
    return (s or "").strip().lower()


def check_read(prog, c=None):
    """闸①：白名单。只有登记且 enabled 的程序才让读。"""
    c = c or cfg()
    if not c.get("enabled", True):
        raise Denied("窗口对接总开关 windows.enabled=false，整层被关掉了")
    spec = (c.get("programs") or {}).get(prog)
    if not isinstance(spec, dict):
        raise Denied("程序 %r 不在白名单里（fairy.json 的 windows.programs）" % prog)
    if not spec.get("enabled"):
        raise Denied("程序 %r 已登记但 enabled=false" % prog)
    return spec


def check_write(prog, c=None):
    """闸②：默认只读。要写必须显式 mode=write。"""
    spec = check_read(prog, c)
    if _norm(spec.get("mode", "read")) != "write":
        raise Denied("程序 %r 是只读模式（mode=%s）。要打字请把 fairy.json 里它的 mode 改成 \"write\""
                     % (prog, spec.get("mode", "read")))
    return spec


def check_click(prog, name, c=None):
    """
    闸②的加强版：点击比打字更危险，所以除了 mode=write，还要按钮名在白名单里。
    ★ 默认 allow_click_names 是空的 —— 也就是【开箱一个按钮都点不了】。
    """
    spec = check_write(prog, c)
    allow = spec.get("allow_click_names") or []
    if not allow:
        raise Denied("程序 %r 没配 allow_click_names —— 按设计，一个按钮都不许点" % prog)
    n = (name or "").strip()
    for a in allow:
        if _norm(a) == _norm(n):
            return spec, False
        # 通配：允许配置 "复制*" 这种前缀，但下面还要过危险词检查
        if str(a).endswith("*") and n.lower().startswith(_norm(a)[:-1]):
            danger = danger_hits(n)          # ★ 重要-1：小写比较 + 中英拼音三套词表
            if danger:
                raise Denied("按钮 %r 命中危险词 %s —— 必须在 allow_click_names 里写【精确全名】"
                             "且由人给出确认口令「%s%s」才放行"
                             % (n, danger, DANGER_ACK_PREFIX, n))
            return spec, True
    raise Denied("按钮 %r 不在 %r 的 allow_click_names 里" % (n, prog))


def danger_hits(name, c=None):
    """
    命中哪些危险词。★ 重要-1：必须先 .lower() 再比 ——
    改造前是【区分大小写的子串比较】，于是 'SEND' / 'Delete all' / 'Submit' /
    'Pay now' / 'Confirm' 一个都拦不住（实测命中全是 []）。
    """
    c = c or cfg()
    n = (name or "").lower()
    if not n:
        return []
    hits = []
    for w in (c.get("danger_words") or []):
        w = str(w or "").strip()
        if w and w.lower() in n:
            hits.append(w)
    return hits


def check_danger(prog, name, c=None, ack=None):
    """
    闸②之三：危险动作（发送/删除/支付/send/delete/pay…）★★ 默认拒绝。

    【为什么改成默认拒绝】以前是「命中危险词就抛 Denied，但 click(confirm=True) 会
    先过 check_click 的通配、再在别处被执行」—— 实测 click("doubao",
    "Click to delete account", confirm=True) 能一路点下去。confirm 只是个布尔量，
    谁都能传（模型自己就能填 True），等于没有闸。

    现在唯一的放行通道是【人给的显式口令】：
        ack 必须精确等于  「确认执行:<按钮全名>」
    """
    hits = danger_hits(name, c)
    if not hits:
        return True
    n = (name or "").strip()
    want = DANGER_ACK_PREFIX + n
    if (ack or "").strip() == want:
        _audit("danger-ack", prog, target=n, ok=True,
               detail="人给了显式确认口令，放行危险词 %s" % hits)
        return True
    raise Denied("动作 %r 命中危险词 %s —— ★ 默认拒绝。若确实要执行，必须由【人】"
                 "显式给出确认口令：%s" % (n, hits, want))


# ================================================================ 对外 API
def read(prog, allow_write=False):
    """
    读一个程序的内容。★ 只读，永远安全。
    返回 {ok, prog, adapter, hwnd, title, text, items, meta, ms}
    """
    t0 = time.time()
    try:
        spec = check_read(prog)
    except Denied as e:
        _audit("read", prog, ok=False, detail=str(e))
        return {"ok": False, "prog": prog, "error": str(e)}
    win, _ = find_window(prog)
    ad = _adapter_for(prog, spec)
    if ad is None:
        msg = "适配器 %r 不存在（fairy.json 里 adapter 写错了？）" % spec.get("adapter")
        _audit("read", prog, ok=False, detail=msg)
        return {"ok": False, "prog": prog, "error": msg}
    # ★ DSH 允许「窗口不在但 HTTP 通」—— 它有两个通道，不该因为窗口最小化就全瞎
    if win is None and not (spec.get("adapter") == "dsh"):
        msg = "程序 %r 现在没有可见窗口（没开？最小化了？）" % prog
        _audit("read", prog, ok=False, detail=msg)
        return {"ok": False, "prog": prog, "error": msg}
    try:
        res = ad.read(win)
    except UiaUnavailable as e:
        _audit("read", prog, ok=False, detail=str(e))
        return {"ok": False, "prog": prog, "error": str(e)}
    except Exception as e:
        _audit("read", prog, ok=False, detail="%s: %s" % (type(e).__name__, str(e)[:150]))
        return {"ok": False, "prog": prog, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}
    res.update({"prog": prog, "adapter": ad.name,
                "hwnd": win.hwnd if win else None, "pid": win.pid if win else None,
                "title": win.title if win else "",
                "proc": win.proc if win else "",
                "ms_total": int((time.time() - t0) * 1000)})
    _audit("read", prog, target=(win.title if win else ""), ok=bool(res.get("ok")),
           detail="%d 字" % len(res.get("text") or ""), extra={"hwnd": res.get("hwnd")})
    return res


def type_text(prog, text, confirm=False):
    """
    往程序的输入框打字。★ 需要 mode=write。★ 绝不自动按回车/点发送。
    confirm=True 只是表达「人已经知情」，不绕过 mode 闸。
    """
    try:
        spec = check_write(prog)
    except Denied as e:
        _audit("type", prog, ok=False, detail=str(e))
        return {"ok": False, "prog": prog, "error": str(e)}
    win, _ = find_window(prog)
    if win is None:
        return {"ok": False, "prog": prog, "error": "程序 %r 没有可见窗口" % prog}
    ad = _adapter_for(prog, spec)
    method = getattr(ad, "type_text", None)
    if method is None or not callable(method):
        msg = "适配器 %r 不支持打字" % spec.get("adapter")
        _audit("type", prog, ok=False, detail=msg)
        return {"ok": False, "prog": prog, "error": msg}
    try:
        res = ad.type_text(win, text)
    except UiaUnavailable as e:
        _audit("type", prog, ok=False, detail=str(e))
        return {"ok": False, "prog": prog, "error": str(e)}
    except Exception as e:
        _audit("type", prog, ok=False, detail="%s: %s" % (type(e).__name__, str(e)[:150]))
        return {"ok": False, "prog": prog, "error": "%s: %s" % (type(e).__name__, str(e)[:200])}
    res.update({"prog": prog, "confirmed": bool(confirm)})
    _audit("type", prog, target=(win.title or ""), ok=bool(res.get("ok")),
           detail="%d 字 via %s · 已发送? 否（本工具不会按回车）" % (len(text or ""), res.get("method")),
           extra={"verified": res.get("verified")})
    return res


def clear_input(prog):
    """把输入框恢复原状。这是【恢复】动作，不算危险操作，但仍走 mode=write 闸。"""
    try:
        spec = check_write(prog)
    except Denied as e:
        _audit("clear", prog, ok=False, detail=str(e))
        return {"ok": False, "prog": prog, "error": str(e)}
    win, _ = find_window(prog)
    if win is None:
        return {"ok": False, "prog": prog, "error": "程序 %r 没有可见窗口" % prog}
    ad = _adapter_for(prog, spec)
    fn = getattr(ad, "clear_input", None)
    if fn is None:
        return {"ok": False, "prog": prog, "error": "适配器 %r 不支持清空" % spec.get("adapter")}
    res = fn(win)
    _audit("clear", prog, target=(win.title or ""), ok=bool(res.get("ok")),
           detail="已清空=%s" % res.get("cleared"))
    return res


def click(prog, name, confirm=False, danger_ack=None):
    """
    点一个按钮。★★ 本工具里最危险的一步，所以三道闸全开：
      白名单(mode=write) -> allow_click_names 精确匹配 -> 危险词检查
    confirm=True 才能执行；否则【只报告将要做什么】，不真点。
    ★★ 重要-1：危险动作上 confirm=True 【不再够用】—— 那只是个布尔量，谁都能传。
      必须由人给出显式口令 danger_ack="确认执行:<按钮全名>" 才放行（见 check_danger）。
    """
    try:
        spec, wildcard = check_click(prog, name)
        check_danger(prog, name, ack=danger_ack)
    except Denied as e:
        _audit("click", prog, target=name, ok=False, detail=str(e))
        return {"ok": False, "prog": prog, "error": str(e), "executed": False}
    win, _ = find_window(prog)
    if win is None:
        return {"ok": False, "prog": prog, "error": "程序 %r 没有可见窗口" % prog, "executed": False}
    if not confirm:
        msg = "已通过白名单，但 confirm=False —— 只报告不执行。要真点请显式确认。"
        _audit("click", prog, target=name, ok=False, detail="未匹配：" + msg)
        return {"ok": False, "prog": prog, "error": msg, "executed": False, "needs_confirm": True}
    u = uia()
    root = u.element_from_handle(win.hwnd)
    time.sleep(1.5)
    el = u.find_first(root, u.UIA.UIA_NamePropertyId, name)
    if el is None:
        msg = "窗口里没找到名字为 %r 的控件" % name
        _audit("click", prog, target=name, ok=False, detail=msg)
        return {"ok": False, "prog": prog, "error": msg, "executed": False}
    ok, how = u.invoke(el)
    _audit("click", prog, target=name, ok=ok, detail="method=%s" % how,
           extra={"wildcard": bool(wildcard)})
    return {"ok": ok, "prog": prog, "target": name, "method": how, "executed": ok}


def status():
    """一体状态：开关、白名单、在线情况、最近审计。"""
    c = cfg()
    return {
        "enabled": c.get("enabled"),
        "audit_log": c.get("audit_log"),
        "audit_file": AUDIT_FILE,
        "programs": {k: {"enabled": v.get("enabled"), "adapter": v.get("adapter"),
                         "mode": v.get("mode", "read"),
                         "allow_click_names": v.get("allow_click_names") or []}
                     for k, v in (c.get("programs") or {}).items() if not k.startswith("_")},
        "online": discover(),
        "recent_ops": audit_tail(8),
    }


def selftest():
    """自检：全是只读动作，不改任何东西。"""
    rows = []

    def add(name, ok, detail):
        rows.append({"check": name, "ok": bool(ok), "detail": str(detail)[:160]})

    c = cfg()
    add("配置加载", True, "程序数=%d 总开关=%s 审计=%s"
        % (len([k for k in (c.get('programs') or {}) if not k.startswith('_')]),
           c.get("enabled"), c.get("audit_log")))
    try:
        u = uia()
        add("UIA 后端(comtypes)", True, "已就绪，无需 uiautomation 包")
    except Exception as e:
        add("UIA 后端(comtypes)", False, "%s —— 读取/操作能力全部不可用" % str(e)[:100])
    wins = enum_windows()
    add("枚举顶层窗口", len(wins) > 0, "可见且有标题的顶层窗口 = %d" % len(wins))
    for prog in [k for k in (c.get("programs") or {}) if not k.startswith("_")]:
        spec = (c.get("programs") or {})[prog]
        w, _ = find_window(prog, c)
        add("识别 %s" % prog,
            bool(w) or spec.get("adapter") == "dsh",
            ("在线 hwnd=%s proc=%s title=%r" % (w.hwnd, w.proc, w.title[:40])) if w
            else ("不在线（%s）" % ("DSH 仍可走 HTTP 通道" if spec.get("adapter") == "dsh" else "窗口没开")))
        if w and spec.get("adapter") in ADAPTERS:
            try:
                r = read(prog)
                add("读 %s" % prog, r.get("ok") and bool(r.get("text")),
                    "%d 字 / %d 个文本控件 / %dms" % (len(r.get("text") or ""),
                                                      r.get("count") or 0, r.get("ms") or 0))
            except Exception as e:
                add("读 %s" % prog, False, "%s: %s" % (type(e).__name__, str(e)[:100]))
    # ---- 三道闸自检 ----
    # ★ 这里【只测闸的判定逻辑】，绝不真的去打字/点击。
    #   第一版自检直接调 type_text() 去试——结果在 write 模式下真往用户输入框里
    #   打了一个 x，还得回头清理。自检必须无副作用，这个坑记在这。
    try:
        check_read("__不存在的程序__")
        add("闸① 白名单", False, "不该通过！")
    except Denied as e:
        add("闸① 白名单", True, str(e))
    dspec = (c.get("programs") or {}).get("doubao") or {}
    try:
        check_write("doubao")
        add("闸② 写入开关", _norm(dspec.get("mode")) == "write",
            "doubao 当前 mode=%s —— write 是【显式授权】的结果，不是默认值"
            % dspec.get("mode", "read"))
    except Denied as e:
        add("闸② 写入开关", True, str(e))
    try:
        check_click("doubao", "发送")
        add("闸③ 按钮白名单", False, "不该通过！")
    except Denied as e:
        add("闸③ 按钮白名单", True, str(e))
    try:
        check_danger("doubao", "删除全部对话")
        add("闸④ 危险词(中文)", False, "不该通过！")
    except Denied as e:
        add("闸④ 危险词(中文)", True, str(e))
    # ★ 重要-1 回归：英文按钮以前一个都拦不住
    for w in ("SEND", "Send", "Delete all", "Submit", "Pay now", "Confirm",
              "Click to delete account"):
        try:
            check_danger("doubao", w)
            add("闸④ 危险词(英文 %s)" % w, False, "不该通过！")
        except Denied:
            add("闸④ 危险词(英文 %s)" % w, True, "已拦下")
    # ★ 重要-1：给了确认口令才放行（默认拒绝通道）
    try:
        check_danger("doubao", "删除全部对话", ack="确认执行:删除全部对话")
        add("闸④ 显式口令放行", True, "只有人给的「确认执行:<按钮全名>」能放行")
    except Denied as e:
        add("闸④ 显式口令放行", False, "口令被拒了：%s" % str(e)[:80])
    add("审计日志", os.path.exists(AUDIT_FILE),
        AUDIT_FILE + ("（还没产生记录）" if not os.path.exists(AUDIT_FILE) else ""))
    return rows


# ================================================================ CLI
def _print_windows(rows, mark_registered=True):
    print("%-10s %-7s %-26s %-24s %s" % ("HWND", "PID", "进程", "类名", "标题"))
    print("-" * 150)
    for r in rows:
        tag = ""
        if mark_registered and r.get("registered_as"):
            tag = "  <= 已登记: %s" % r["registered_as"]
        print("%-10d %-7d %-26s %-24s %s%s" % (r["hwnd"], r["pid"], (r["proc"] or "")[:25],
                                               (r["cls"] or "")[:23], (r["title"] or "")[:52], tag))
    print("\n合计 %d 个顶层窗口" % len(rows))


def main(argv=None):
    a = list(sys.argv[1:] if argv is None else argv)

    def opt(name, default=None):
        if name in a:
            i = a.index(name)
            if i + 1 < len(a) and not a[i + 1].startswith("--"):
                return a[i + 1]
            return True
        return default

    if "--scan" in a:
        # 默认只看「真在屏幕上的」；--all 连隐藏窗口一起列（排障/写 match 规则时有用）
        if "--all" in a:
            rows = [w.as_dict() for w in
                    sorted(enum_windows(include_hidden=True, with_title_only=True),
                           key=lambda x: (x.proc or "").lower())]
            c = cfg()
            for r in rows:
                w = WinInfo(**r)
                prog, _ = identify(w, c)
                r["registered_as"] = prog
            print("（--all：含不可见窗口。屏幕上真看得见的只有 %d 个）" % len(enum_windows()))
        else:
            rows = scan(only_registered=("--only-registered" in a))
        _print_windows(rows)
        return 0
    if "--discover" in a:
        print(json.dumps(discover(), ensure_ascii=False, indent=2))
        return 0
    if "--status" in a:
        print(json.dumps(status(), ensure_ascii=False, indent=2))
        return 0
    if "--audit" in a:
        n = opt("--audit", 20)
        try:
            n = int(n)
        except Exception:
            n = 20
        for r in audit_tail(n):
            print(json.dumps(r, ensure_ascii=False))
        return 0
    if "--read" in a:
        prog = opt("--read")
        if not prog or prog is True:
            print("用法: --read <程序名>（先 --discover 看有哪些）")
            return 2
        r = read(prog)
        if not r.get("ok"):
            print("读取失败：%s" % r.get("error"))
            return 1
        print("程序   : %s (%s)  hwnd=%s" % (r["prog"], r.get("adapter"), r.get("hwnd")))
        print("窗口   : %s" % r.get("title"))
        print("文本数 : %s   耗时 %sms" % (r.get("count"), r.get("ms")))
        meta = r.get("meta") or {}
        ch = meta.get("channels")
        if ch:
            print("通道   :")
            for k, v in ch.items():
                print("   %-18s ok=%s %s" % (k, v.get("ok"),
                      ("条=%s" % len(v.get("turns") or [])) if k == "session_projcache"
                      else ("%d 字" % len(v.get("text") or "") if v.get("text") else
                            str(v.get("error") or v.get("count") or "")[:80])))
        print("-" * 90)
        print(r.get("text") or "(空)")
        print("-" * 90)
        if "--json" in a:
            print(json.dumps({k: v for k, v in r.items() if k != "items"}, ensure_ascii=False, indent=2))
        return 0
    if "--type" in a:
        prog = opt("--type")
        rest = [x for x in a if x not in ("--type", "--yes", prog)]
        text = rest[0] if rest else ""
        r = type_text(prog, text, confirm=("--yes" in a))
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r.get("ok") else 1
    if "--clear" in a:
        prog = opt("--clear")
        print(json.dumps(clear_input(prog), ensure_ascii=False, indent=2))
        return 0
    if "--click" in a:
        prog = opt("--click")
        rest = [x for x in a if x not in ("--click", "--yes", prog, "--danger-ack")
                and x != opt("--danger-ack")]
        name = rest[0] if rest else ""
        print(json.dumps(click(prog, name, confirm=("--yes" in a),
                               danger_ack=opt("--danger-ack")),
                         ensure_ascii=False, indent=2))
        return 0
    if "--selftest" in a:
        rows = selftest()
        bad = 0
        for r in rows:
            print("[%s] %-22s %s" % ("OK " if r["ok"] else "!! ", r["check"], r["detail"]))
            if not r["ok"]:
                bad += 1
        print("\n自检：%d 项通过 / %d 项失败" % (len(rows) - bad, bad))
        return 0 if bad == 0 else 1

    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
