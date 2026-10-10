# -*- coding: utf-8 -*-
import ctypes
import json
import os
import sys
import time
import urllib.request
import fairy_root

D = fairy_root.ROOT
LOGS = os.path.join(D, "logs")
CONF = os.path.join(D, "advisor.json")
STATE = os.path.join(LOGS, "advisor_state.json")
JLOG = os.path.join(LOGS, "advisor_log.jsonl")

OLLAMA = "http://127.0.0.1:11434"
OLLAMA_MODEL = "qwen3:30b-a3b"
META_URL = "http://127.0.0.1:19387/api/fairy/meta"

DEFAULTS = {
    "enabled": True,
    "interval_min": 5,
    "min_gap_min": 10,
    "max_per_hour": 3,
    "idle_skip_min": 10,
    "work_remind_min": 60,
    "away_greet_min": 20,
    "cpu_high": 90,
    "mem_high": 90,
    "disk_low_pct": 10,
    "use_llm": True,
    "llm_timeout_s": 20,
    "llm_max_chars": 30,
    "say_level": "advice",
    "alert_level": "alert",
    "bubble_seconds": 6,
}


def load_conf():
    c = dict(DEFAULTS)
    try:
        if os.path.exists(CONF):
            c.update(json.load(open(CONF, encoding="utf-8")))
    except Exception as e:
        print("[advisor] 配置读取失败，用默认值: %s" % e, flush=True)
    return c

def save_conf(patch):
    c = load_conf()
    c.update(patch or {})
    try:
        os.makedirs(os.path.dirname(CONF), exist_ok=True)
        tmp = CONF + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONF)
    except Exception as e:
        print("[advisor] 配置写入失败: %s" % e, flush=True)
    return c

def load_state():
    s = {}
    try:
        if os.path.exists(STATE):
            s = json.load(open(STATE, encoding="utf-8")) or {}
    except Exception as e:
        print("[advisor] 状态读取失败（当空处理）: %s" % e, flush=True)
    return s

def save_state(s):
    try:
        os.makedirs(LOGS, exist_ok=True)
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(s, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE)
    except Exception as e:
        print("[advisor] 状态写入失败: %s" % e, flush=True)

def jlog(rec):
    try:
        os.makedirs(LOGS, exist_ok=True)
        rec["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(JLOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print("[advisor] 日志写入失败: %s" % e, flush=True)


def _idle_seconds():
    try:
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
        li = LASTINPUTINFO()
        li.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
            return None
        tick = ctypes.windll.kernel32.GetTickCount()
        return max(0.0, (tick - li.dwTime) / 1000.0)
    except Exception as e:
        print("[advisor] 取空闲时间失败: %s" % e, flush=True)
        return None

def _foreground():
    try:
        import win32gui
        import win32process
        import psutil
        h = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(h) or ""
        try:
            _, pid = win32process.GetWindowThreadProcessId(h)
            pname = psutil.Process(pid).name()
        except Exception:
            pname = ""
        return title, pname
    except Exception as e:
        print("[advisor] 取前台窗口失败: %s" % e, flush=True)
        return "", ""

def _load():
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=None)
        vm = psutil.virtual_memory()
        disks = {}
        for d in ("C:\\", "D:\\", "E:\\", "F:\\"):
            if os.path.exists(d):
                try:
                    u = psutil.disk_usage(d)
                    disks[d[0]] = round(u.percent, 1)
                except Exception:
                    pass
        return {"cpu": cpu, "mem": vm.percent, "disks": disks}
    except Exception as e:
        print("[advisor] 取系统负载失败: %s" % e, flush=True)
        return {"cpu": None, "mem": None, "disks": {}}

def _agent():
    try:
        with urllib.request.urlopen(META_URL, timeout=6) as r:
            j = json.loads(r.read().decode("utf-8"))
        return {"running": j.get("running"), "doing": j.get("doing") or "",
                "steps": j.get("steps") or 0, "progress": j.get("progress"),
                "ok": True}
    except Exception:
        return {"ok": False}

def _media_class(title, app):
    try:
        import importlib.util
        fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media_duck.py")
        s = importlib.util.spec_from_file_location("fairy_media_duck_adv", fp)
        m = importlib.util.module_from_spec(s)
        sys.modules["fairy_media_duck_adv"] = m
        s.loader.exec_module(m)
        cls = m.classify(app or "", title or "")

        try:
            import win32gui
            h = win32gui.GetForegroundWindow()
            l, t, r, b = win32gui.GetWindowRect(h)
            sw = ctypes.windll.user32.GetSystemMetrics(0)
            sh = ctypes.windll.user32.GetSystemMetrics(1)
            if (r - l) >= sw - 4 and (b - t) >= sh - 4 and cls == "ignore":
                cls = "volume"
                return cls, "全屏"
        except Exception:
            pass
        return cls, "media_duck"
    except Exception as e:
        print("[advisor] 媒体分类失败: %s" % e, flush=True)
        return "ignore", "分类不可用"

def observe(cfg=None):
    title, app = _foreground()
    load = _load()
    cls, why = _media_class(title, app)
    obs = {
        "t": time.time(),
        "title": title[:80],
        "app": app,
        "idle_s": _idle_seconds(),
        "cpu": load.get("cpu"),
        "mem": load.get("mem"),
        "disks": load.get("disks") or {},
        "media": cls,
        "media_why": why,
        "agent": _agent(),
        "hour": int(time.strftime("%H")),
    }
    return obs


def rule_hits(obs, st, cfg):
    out = []
    today = time.strftime("%Y-%m-%d")

    low = cfg.get("disk_low_pct", 10)
    for d, pct in (obs.get("disks") or {}).items():
        if pct is not None and (100.0 - pct) < low:
            out.append(("disk_low_" + d,
                        "%s 盘快满了，只剩不到一成空间。" % d,
                        "alert"))

    cpu = obs.get("cpu")
    if cpu is not None and cpu >= cfg.get("cpu_high", 90):
        st["cpu_streak"] = int(st.get("cpu_streak") or 0) + 1
    else:
        st["cpu_streak"] = 0
    if int(st.get("cpu_streak") or 0) >= 3:
        out.append(("cpu_high", "处理器一直满载，可能有东西在抢资源。", "alert"))

    mem = obs.get("mem")
    if mem is not None and mem >= cfg.get("mem_high", 90):
        out.append(("mem_high", "内存快用完了，建议关掉几个不用的程序。", "alert"))

    since = st.get("active_since")
    if since:
        mins = (time.time() - float(since)) / 60.0
        if mins >= cfg.get("work_remind_min", 60):
            out.append(("work_long", "已经连续忙了一个多小时，起来走两步吧。", None))

    ag = obs.get("agent") or {}
    prev = st.get("prev_agent_running")
    if prev is True and ag.get("running") is False and ag.get("ok"):
        out.append(("agent_done", "你交代的活我这边刚告一段落。", None))
    st["prev_agent_running"] = ag.get("running") if ag.get("ok") else prev
    st["prev_app"] = obs.get("app") or ""

    away = st.get("away_since")
    if away:
        amins = (time.time() - float(away)) / 60.0
        idle = obs.get("idle_s")
        if amins >= cfg.get("away_greet_min", 20) and idle is not None and idle < 60:
            out.append(("back", "欢迎回来，需要我做什么？", None))
    _ = today
    return out


LLM_PROMPT = """你是桌面助理 Fairy，正在安静地陪着主人工作。
下面是刚刚观察到的情境（只有窗口标题、进程名、系统指标，没有别的）：

- 前台窗口：{title}
- 前台程序：{app}
- 系统：CPU {cpu}% · 内存 {mem}% · 磁盘占用 {disks}
- 在播媒体类型：{media}
- 助手自己的状态：{agent}
- 主人已连续活跃：{active_min} 分钟

**正常范围（低于这些就别提）**：CPU 低于 70% 正常 · 内存低于 80% 正常 ·
磁盘占用低于 85% 正常（即剩余高于 15%）· 连续工作低于 50 分钟正常。

请判断：**此刻是否真有一条对主人有用、且他不会觉得烦的建议？**
规则：
1. **只要各项都在正常范围内、也没有明显异常，就直接回 NONE**（宁少勿多，这是首要原则）
2. 真要说时：一句中文，不超过 25 字，不要客套，不要标点堆砌
3. **禁止**说"多喝水""注意休息""清理缓存提升效率"这类放之四海皆准的空话
4. 不要描述你看到了什么，直接给建议；不要输出思考过程
直接输出那一句，或输出 NONE。"""

def _noteworthy(obs, st, cfg):
    try:
        if (obs.get("cpu") or 0) >= 70:
            return True
        if (obs.get("mem") or 0) >= 80:
            return True
        for d, pct in (obs.get("disks") or {}).items():
            if pct is not None and (100.0 - pct) < 20:
                return True
        if st.get("prev_app") != (obs.get("app") or ""):
            return True
        if st.get("prev_agent_running") != (obs.get("agent") or {}).get("running"):
            return True
    except Exception as e:
        print("[advisor] _noteworthy 出错（保守起见：不问）: %s" % e, flush=True)
        return False
    return False

def llm_advice(obs, cfg, active_min=None):
    if not cfg.get("use_llm", True):
        return None
    try:
        ag = obs.get("agent") or {}
        ag_s = "空闲" if ag.get("running") is False else ("干活中：" + (ag.get("doing") or "") if ag.get("running") else "未知")
        prompt = LLM_PROMPT.format(
            title=obs.get("title") or "(无)", app=obs.get("app") or "(未知)",
            cpu=obs.get("cpu"), mem=obs.get("mem"),
            disks=", ".join("%s %s%%" % (k, v) for k, v in (obs.get("disks") or {}).items()) or "?",
            media=obs.get("media") or "无", agent=ag_s,
            active_min=int(active_min or 0),
        )
        body = json.dumps({
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False,


            "keep_alive": "2h",


            "options": {"num_predict": 60, "temperature": 0.4},
        }).encode("utf-8")
        req = urllib.request.Request(OLLAMA + "/api/generate", data=body,
                                     headers={"content-type": "application/json"})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=cfg.get("llm_timeout_s", 20)) as r:
            j = json.loads(r.read().decode("utf-8"))
        took = time.time() - t0
        txt = (j.get("response") or "").strip()

        txt = txt.splitlines()[0].strip().strip('"').strip("'").strip()
        if not txt or txt.upper() == "NONE" or "NONE" in txt.upper()[:6]:
            jlog({"event": "llm_none", "took_s": round(took, 2), "raw": txt[:60]})
            return None
        if len(txt) > cfg.get("llm_max_chars", 30):
            jlog({"event": "llm_too_long", "took_s": round(took, 2), "raw": txt[:80]})
            return None
        jlog({"event": "llm_ok", "took_s": round(took, 2), "text": txt})
        return ("llm", txt, None)
    except Exception as e:

        print("[advisor] LLM 建议失败（降级为不说）: %s: %s" % (type(e).__name__, e), flush=True)
        jlog({"event": "llm_fail", "err": "%s: %s" % (type(e).__name__, e)})
        return None


def gate(hit, obs, st, cfg, now=None):
    now = now or time.time()
    key, text, level = hit
    if not cfg.get("enabled", True):
        return False, "总开关关闭"
    if st.get("muted"):
        return False, "手动静音中"

    if obs.get("media") == "call":
        return False, "通话中（说了会被对方听见）"
    if obs.get("media") == "volume":
        return False, "全屏视频/游戏中"

    idle = obs.get("idle_s")
    if idle is not None and idle > cfg.get("idle_skip_min", 10) * 60:
        return False, "你不在（已空闲 %d 分钟）" % int(idle / 60)

    gap = now - float(st.get("last_say_ts") or 0)
    if gap < cfg.get("min_gap_min", 10) * 60:
        return False, "距上次发言仅 %.1f 分钟" % (gap / 60.0)

    if now - float(st.get("hour_ts") or 0) > 3600:
        st["hour_ts"] = now
        st["hour_n"] = 0
    if int(st.get("hour_n") or 0) >= cfg.get("max_per_hour", 3):
        return False, "本小时已达上限"

    today = time.strftime("%Y-%m-%d")
    once = st.get("once") or {}
    if once.get(key) == today:
        return False, "同类今天已说过（%s）" % key
    return True, "ok"

def commit_say(hit, st, now=None):
    now = now or time.time()
    key, text, level = hit
    st["last_say_ts"] = now
    st["hour_n"] = int(st.get("hour_n") or 0) + 1
    if not st.get("hour_ts"):
        st["hour_ts"] = now
    once = st.get("once") or {}
    once[key] = time.strftime("%Y-%m-%d")

    if len(once) > 60:
        once = dict(sorted(once.items(), key=lambda kv: kv[1])[-60:])
    st["once"] = once


import hashlib

ASSIST_LOG = os.path.join(LOGS, "assist_log.jsonl")
ASSIST_DEFAULTS = {
    "assist_enabled": True,
    "clipboard": True,
    "watch_files": [],
    "assist_min_gap_s": 20,
    "speak_important_only": True,
    "max_content_chars": 4000,
    "assist_use_llm": True,
    "slides_enabled": False,
    "polish_enabled": False,
}


PROVIDERS = {}

def register_provider(key, cfg):
    PROVIDERS[key] = cfg


register_provider("spell", {
    "label": "错字/病句",
    "kinds": ["zh"],
    "enabled": True,
    "level": "advice",
    "ok": "OK",
    "prompt": """你在帮一位中文作者校对稿子。下面是他的原文：
---
{content}
---
只找出**确定的**错误：错别字、别字、病句、明显用词不当、标点误用。
输出格式（每条一行，最多 3 条）：
错误: <原文片段> | 应为: <改法> | 原因: <一句话>
如果没有任何问题，只输出：OK
不要解释、不要客套、不要复述原文、不要输出思考过程。""",
})

register_provider("code", {
    "label": "代码逻辑",
    "kinds": ["code"],
    "enabled": True,
    "level": "advice",
    "ok": "OK",
    "prompt": """你是资深工程师。只审查下面这段代码里**确定存在的逻辑缺陷**：
死循环、数组越界、空引用、未处理的异常、条件写反、资源未释放、明显边界错误。
---
{content}
---
输出（每条一行，最多 3 条）：
问题: <一句话> | 位置: <代码片段> | 建议: <一句话>
如果看不出确定的问题，只输出：OK
不要夸代码、不要讲代码风格、不要复述、不要输出思考过程。""",
})

register_provider("translate", {
    "label": "翻译",
    "kinds": ["en", "other"],
    "enabled": True,
    "level": "advice",
    "ok": None,
    "prompt": """把下面这段翻译成自然流畅的简体中文。只输出译文，不要解释、不要复述原文：
---
{content}
---""",
})

register_provider("slides", {
    "label": "PPT 配图/文字效果",
    "kinds": ["zh"],
    "enabled": False,
    "level": "advice",
    "ok": "OK",
    "prompt": """用户在准备 PPT。下面是某一页的文字：
---
{content}
---
给 1~2 条建议：这页配什么图/图形最合适，文字层级/对齐/强调怎么调。
输出（每条一行）：建议: <一句话> | 图: <该配什么图>
没问题就输出 OK。不要客套、不要输出思考过程。""",
})

register_provider("polish", {
    "label": "润色",
    "kinds": ["zh"],
    "enabled": False,
    "level": "advice",
    "ok": "OK",
    "prompt": """把下面这段中文润色得更通顺自然，保持原意与信息量，只输出润色后的全文：
---
{content}
---""",
})

def _clip_text():
    try:
        import win32clipboard
        import win32con
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT) or ""
        finally:
            win32clipboard.CloseClipboard()
    except Exception as e:
        print("[advisor] 读剪贴板失败: %s: %s" % (type(e).__name__, e), flush=True)
    return ""

def _kind(text):
    t = (text or "").strip()
    if not t:
        return "empty"
    code_sig = ("def ", "class ", "function ", "import ", "while ", "for (", "for(",
                "if (", "if(", "return ", "};", "});", "public ", "private ", "void ",
                "const ", "let ", "var ", "#!/", "SELECT ", "</", "=>", "===", "!=",
                "console.log", "println", "printf(")
    nl = t.count("\n")
    hits = sum(1 for k in code_sig if k in t)
    if hits >= 2 or (nl >= 2 and hits >= 1 and ("{" in t or ";" in t or "(" in t)):
        return "code"
    cjk = sum(1 for ch in t if "\u4e00" <= ch <= "\u9fff")
    if cjk >= max(3, int(len(t) * 0.15)):
        return "zh"
    letters = sum(1 for ch in t if ch.isascii() and ch.isalpha())
    if letters >= max(10, int(len(t) * 0.4)):
        return "en"
    return "other"

def _ask_llm(prompt, cfg, timeout=None):
    body = json.dumps({
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,


        "options": {"num_predict": 300, "temperature": 0.3},
    }, ensure_ascii=False).encode("utf-8")
    try:
        req = urllib.request.Request(OLLAMA + "/api/generate", data=body,
                                     headers={"content-type": "application/json"})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=timeout or cfg.get("llm_timeout_s", 20) + 40) as r:
            j = json.loads(r.read().decode("utf-8"))
        return (j.get("response") or "").strip(), time.time() - t0
    except Exception as e:
        print("[advisor] 内容分析 LLM 失败: %s: %s" % (type(e).__name__, e), flush=True)
        return None, 0.0

def _pending_for_dsh(kind, summary, content="", why="", context="", local_said=""):
    return None


UNCERTAIN_HINTS = ("thread", "threading", "lock", "mutex", "semaphore", "async", "await",
                   "coroutine", "synchronized", "volatile", "原子", "并发", "死锁", "状态机",
                   "state machine", "race", "atomic")
UNCERTAIN_WORDS = ("拿不准", "不确定", "无法确定", "无法判断", "不清楚", "uncertain",
                   "cannot determine", "can't determine", "insufficient")

def uncertain_reason(text, kind, nlines, model_out):
    low = (text or "").lower()
    if kind == "code":
        if nlines > 30:
            return "代码 %d 行，超出本地快速审查的可靠范围（>30 行）" % nlines
        hit = [k for k in UNCERTAIN_HINTS if k in low]
        if hit:
            return "涉及并发/状态机等深度逻辑（命中：%s），需要跨函数推理" % "、".join(hit[:3])
    if kind in ("en", "other") and nlines > 40:
        return "待翻译长文 %d 行，本地容易前后不一致（>40 行）" % nlines
    if kind == "zh" and nlines > 40:
        return "中文长文 %d 行，全局校对超出本地可靠范围（>40 行）" % nlines
    if model_out:
        mo = str(model_out)
        if any(w in mo.lower() for w in UNCERTAIN_WORDS):
            return "本地模型自己表示拿不准"
    return None

def _al(rec):
    try:
        rec["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(ASSIST_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print("[advisor] assist 日志写入失败: %s" % e, flush=True)

def assist_tick(cfg, st, obs=None):
    a = dict(ASSIST_DEFAULTS)
    a.update(cfg or {})
    if not a.get("assist_enabled", True):
        return {"skipped": "内容分析总开关关闭"}

    if (obs or {}).get("media") == "call":
        return {"skipped": "通话中"}

    src, txt = "", ""
    if a.get("clipboard", True):
        txt = _clip_text()
        src = "clipboard"
    files = a.get("watch_files") or []
    if files:
        try:
            newest, nt = None, 0
            for fp in files:
                if os.path.exists(fp):
                    mt = os.path.getmtime(fp)
                    if mt > nt:
                        newest, nt = fp, mt
            if newest and nt > float(st.get("watch_file_mt") or 0):
                st["watch_file_mt"] = nt
                with open(newest, "r", encoding="utf-8", errors="replace") as f:
                    txt = f.read()[:a.get("max_content_chars", 4000)]
                src = "file:" + os.path.basename(newest)
        except Exception as e:
            print("[advisor] 读监视文件失败: %s" % e, flush=True)
    txt = (txt or "").strip()
    if len(txt) < 8:
        return {"skipped": "没有新内容（剪贴板太短）", "src": src}
    txt = txt[:a.get("max_content_chars", 4000)]
    h = hashlib.sha1(txt.encode("utf-8", "replace")).hexdigest()[:16]
    if st.get("assist_hash") == h:
        return {"skipped": "同一内容已分析过", "hash": h}

    now = time.time()
    gap = now - float(st.get("assist_ts") or 0)
    if gap < a.get("assist_min_gap_s", 20):
        return {"skipped": "距上次内容分析仅 %.0f 秒" % gap}
    kind = _kind(txt)

    order = []
    if kind == "code":
        order = ["code"]
    elif kind in ("en", "other"):
        order = ["translate"]
    elif kind == "zh":
        order = ["spell"]
        if a.get("slides_enabled"):
            order.append("slides")
        if a.get("polish_enabled"):
            order.append("polish")
    if not order:
        return {"skipped": "内容类型无法判断", "kind": kind}

    nl = txt.count("\n") + 1

    _why = uncertain_reason(txt, kind, nl, None)
    if _why:
        st["assist_hash"] = h
        st["assist_ts"] = now
        return {"kind": kind, "lines": nl, "why": _why,
                "say": "这个我拿不准，建议你让大脑（DSH）细看。", "level": "advice"}
    if not a.get("assist_use_llm", True):
        return {"skipped": "LLM 分析已关"}

    results = []
    for key in order:
        pv = PROVIDERS.get(key)
        if not pv or not pv.get("enabled"):
            continue
        out, took = _ask_llm(pv["prompt"].format(content=txt), cfg)
        if out is None:
            continue
        ok_tok = pv.get("ok")
        clean = out.strip()
        good = (ok_tok is not None and clean.upper().startswith(str(ok_tok).upper()))
        results.append({"provider": key, "label": pv.get("label"), "took_s": round(took, 1),
                        "raw": clean[:800], "ok": good, "level": pv.get("level", "advice")})
        _al({"event": "analyzed", "provider": key, "kind": kind, "src": src,
             "took_s": round(took, 1), "ok": good, "out": clean[:400]})

        _w2 = uncertain_reason(txt, kind, nl, clean)
        if _w2 and _w2 == "本地模型自己表示拿不准":
            results.append({"provider": key, "label": pv.get("label"), "took_s": round(took, 1),
                            "raw": "本地拿不准，建议用大脑（DSH）细看", "ok": False, "level": "advice"})
            break
        if not good:
            break
    st["assist_hash"] = h
    st["assist_ts"] = now
    spoken = None
    lvl = "advice"
    for r in results:
        if not r["ok"]:
            spoken = r["raw"]
            lvl = r["level"]
            break
    return {"kind": kind, "src": src, "results": results,
            "say": spoken, "level": lvl, "hash": h}

class Advisor:

    def __init__(self, say=None, bubble=None, conf=None):
        self.cfg = conf or load_conf()
        self.say = say
        self.bubble = bubble
        self.last_obs_ts = 0.0
        self._cpu_warmed = False

    def tick(self, force=False, obs=None):
        cfg = self.cfg
        now = time.time()
        if not force and (now - self.last_obs_ts) < cfg.get("interval_min", 5) * 60:
            return {"skipped": "还没到观察间隔"}


        if not self._cpu_warmed:
            try:
                import psutil
                psutil.cpu_percent(interval=None)
            except Exception as e:
                print("[advisor] CPU 预热失败: %s" % e, flush=True)
            self._cpu_warmed = True
            return {"skipped": "CPU 采样预热（不消耗观察间隔）"}
        self.last_obs_ts = now
        st = load_state()
        o = obs or observe(cfg)

        idle = o.get("idle_s")
        if idle is not None:
            if idle > 300:
                if not st.get("away_since"):
                    st["away_since"] = now
                st["active_since"] = None
            else:
                if st.get("away_since"):
                    st["away_since"] = None
                if not st.get("active_since"):
                    st["active_since"] = now

        hits = rule_hits(o, st, cfg)
        chosen = None
        src = "rule"
        for h in hits:
            ok, why = gate(h, o, st, cfg, now)
            jlog({"event": "gate_rule", "key": h[0], "ok": ok, "why": why})
            if ok:
                chosen = h
                break


        if chosen is None and cfg.get("use_llm", True) and _noteworthy(o, st, cfg):

            if o.get("media") not in ("call", "volume") and \
               (idle is None or idle <= cfg.get("idle_skip_min", 10) * 60):
                active_min = ((now - float(st["active_since"])) / 60.0) if st.get("active_since") else 0
                h = llm_advice(o, cfg, active_min)
                if h:
                    ok, why = gate(h, o, st, cfg, now)
                    jlog({"event": "gate_llm", "ok": ok, "why": why})
                    if ok:
                        chosen = h
                        src = "llm"
        out = {"obs": o, "hits": [h[0] for h in hits], "chosen": None,
               "src": src, "said": False, "why_not": ""}
        if chosen:
            key, text, level = chosen
            lvl = cfg.get("alert_level", "alert") if level == "alert" else cfg.get("say_level", "advice")
            out["chosen"] = {"key": key, "text": text, "level": lvl}
            if self.say:
                try:
                    self.say(text, lvl)
                    out["said"] = True
                except Exception as e:
                    print("[advisor] 说话失败: %s: %s" % (type(e).__name__, e), flush=True)
            if self.bubble:
                try:
                    self.bubble(text)
                except Exception as e:
                    print("[advisor] 气泡失败: %s: %s" % (type(e).__name__, e), flush=True)
            commit_say(chosen, st, now)
            jlog({"event": "said", "key": key, "text": text, "level": lvl, "src": src})
        else:
            out["why_not"] = "无规则命中且 LLM 未给建议"
            jlog({"event": "silent", "hits": out["hits"],
                  "media": o.get("media"), "idle_s": idle})

        try:
            ar = assist_tick(cfg, st, o) or {}
            out["assist"] = {k: v for k, v in ar.items() if k != "results"}
            say_txt = ar.get("say")
            if say_txt:
                lvl = ar.get("level", "advice")


                if self.bubble:
                    try:
                        self.bubble(say_txt)
                    except Exception as _e3:
                        print("[advisor] 建议气泡失败: %s" % _e3, flush=True)
                speak = (not cfg.get("speak_important_only", True)) or lvl == "alert"
                if speak and self.say:
                    try:
                        self.say(say_txt, lvl)
                    except Exception as _e4:
                        print("[advisor] 建议说话失败: %s" % _e4, flush=True)
                out["assist_said"] = {"text": say_txt, "level": lvl, "spoken": speak}
                jlog({"event": "assist_say", "text": say_txt, "level": lvl, "spoken": speak})
            save_state(st)
        except Exception as _e:
            print("[advisor] 内容分析出错: %s: %s" % (type(_e).__name__, _e), flush=True)
        return out


def _fake_obs(**kw):
    o = {"t": time.time(), "title": "测试窗口 — 某程序", "app": "test.exe",
         "idle_s": 5, "cpu": 20.0, "mem": 40.0, "disks": {"C": 50.0, "D": 60.0},
         "media": "ignore", "media_why": "fake", "agent": {"ok": True, "running": False},
         "hour": int(time.strftime("%H"))}
    o.update(kw)
    return o

def selftest():
    print("=== work_advisor 自测 ===")
    cfg = dict(DEFAULTS)
    passed = failed = 0

    def ck(name, cond, extra=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print("  ✓ %s %s" % (name, extra))
        else:
            failed += 1
            print("  ✗ %s %s" % (name, extra))


    st = {}

    hits = rule_hits(_fake_obs(disks={"C": 95.0}), st, cfg)
    ck("磁盘低水位命中", any(h[0] == "disk_low_C" for h in hits), str([h[0] for h in hits]))
    ck("磁盘告警级别=alert", any(h[2] == "alert" for h in hits if h[0] == "disk_low_C"))

    st2 = {}
    rule_hits(_fake_obs(cpu=95.0), st2, cfg)
    rule_hits(_fake_obs(cpu=95.0), st2, cfg)
    h3 = rule_hits(_fake_obs(cpu=95.0), st2, cfg)
    ck("CPU 需连续 3 次才报", any(h[0] == "cpu_high" for h in h3), "streak=%s" % st2.get("cpu_streak"))

    st3 = {"active_since": time.time() - 61 * 60}
    ck("连续工作提醒命中", any(h[0] == "work_long" for h in rule_hits(_fake_obs(), st3, cfg)))

    st4 = {"prev_agent_running": True}
    h4 = rule_hits(_fake_obs(agent={"ok": True, "running": False}), st4, cfg)
    ck("AI 代理完工提醒命中", any(h[0] == "agent_done" for h in h4), str([h[0] for h in h4]))

    ok, why = gate(("work_long", "x", None), _fake_obs(media="call"), {}, cfg)
    ck("通话中拒绝", not ok, why)

    ok, why = gate(("work_long", "x", None), _fake_obs(media="volume"), {}, cfg)
    ck("视频/游戏中拒绝", not ok, why)

    ok, why = gate(("work_long", "x", None), _fake_obs(idle_s=1800), {}, cfg)
    ck("空闲太久拒绝", not ok, why)

    ok, why = gate(("work_long", "x", None), _fake_obs(), {"last_say_ts": time.time() - 60}, cfg)
    ck("间隔不足拒绝", not ok, why)

    today = time.strftime("%Y-%m-%d")
    ok, why = gate(("work_long", "x", None), _fake_obs(), {"once": {"work_long": today}}, cfg)
    ck("同类当天去重", not ok, why)

    st5 = {"last_say_ts": time.time() - 3600, "hour_ts": time.time(), "hour_n": 3}
    ok, why = gate(("work_long", "x", None), _fake_obs(), st5, cfg)
    ck("每小时上限", not ok, why)

    ok, why = gate(("work_long", "x", None), _fake_obs(), {}, cfg)
    ck("正常情况放行", ok, why)

    st6 = {}
    commit_say(("work_long", "x", None), st6)
    ok2, why2 = gate(("work_long", "x", None), _fake_obs(), st6, cfg)
    ck("记账后同类被挡", not ok2, why2)

    ok, why = gate(("work_long", "x", None), _fake_obs(), {"muted": True}, cfg)
    ck("静音生效", not ok, why)

    ok, why = gate(("work_long", "x", None), _fake_obs(), {}, dict(cfg, enabled=False))
    ck("总开关关闭", not ok, why)

    print("\n  —— %d 通过 / %d 失败 ——" % (passed, failed))
    return 0 if failed == 0 else 1

def assist_selftest():
    cfg = load_conf()
    print("=== 内容分析自测 ===")
    cases = [
        ("中文错字", "zh", "这个方案我非常满意，我们会尽快给于回复，请你耐心等待以下结果。"),
        ("代码逻辑", "code", "def find(xs, t):\n    i = 0\n    while True:\n        if xs[i] == t:\n            return i\n        i += 1\n\nprint(find([1,2,3], 9))"),
        ("英文翻译", "en", "The build failed because the cache directory was not writable."),
    ]
    ok = fail = 0
    for name, want_kind, text in cases:
        k = _kind(text)
        kind_ok = (k == want_kind)

        if k == "code":
            pv = PROVIDERS["code"]
        elif k == "zh":
            pv = PROVIDERS["spell"]
        else:
            pv = PROVIDERS["translate"]
        out, took = _ask_llm(pv["prompt"].format(content=text), cfg)
        got = bool(out)
        ok_tok = pv.get("ok")
        good_cnt = got and not (ok_tok and str(out).strip().upper().startswith(str(ok_tok).upper()))
        print("\n--- %s（判定类型=%s，期望=%s %s）---" % (name, k, want_kind, "✓" if kind_ok else "✗"))
        print("    %.1fs  模型输出: %s" % (took, (out or "(空/失败)")[:220].replace("\n", " / ")))
        if kind_ok and got and (good_cnt or k in ("zh", "code")):
            ok += 1
            print("    ✓ 有输出")
        else:
            fail += 1
            print("    ✗ 无输出或类型判错")

    print("\n  —— %d 通过 / %d 失败 ——" % (ok, fail))
    return 0 if fail == 0 else 1

def status():
    st = load_state()
    cfg = load_conf()
    print("=== work_advisor 状态 ===")
    print("  开关:", "开" if cfg.get("enabled") else "**关**", "| 静音:", "是" if st.get("muted") else "否")
    print("  间隔 %s 分钟 · 最短间隔 %s 分钟 · 每小时上限 %s" %
          (cfg.get("interval_min"), cfg.get("min_gap_min"), cfg.get("max_per_hour")))
    last = st.get("last_say_ts")
    print("  上次发言:", time.strftime("%H:%M:%S", time.localtime(last)) if last else "（还没有）")
    print("  本小时已说:", st.get("hour_n") or 0)
    print("  活跃起点:", time.strftime("%H:%M:%S", time.localtime(st["active_since"])) if st.get("active_since") else "（未记录）")
    once = st.get("once") or {}
    today = time.strftime("%Y-%m-%d")
    print("  今天已说过的类别:", [k for k, v in once.items() if v == today] or "（无）")
    if os.path.exists(JLOG):
        print("  最近 8 条记录:")
        lines = open(JLOG, encoding="utf-8", errors="replace").read().strip().splitlines()[-8:]
        for x in lines:
            print("    " + x[:120])
    return 0

def once(verbose=True):
    cfg = load_conf()
    st = load_state()

    try:
        import psutil
        psutil.cpu_percent(interval=None)
        time.sleep(0.35)
    except Exception:
        pass
    obs = observe(cfg)
    if verbose:
        print("=== 观察 ===")
        for k in ("title", "app", "idle_s", "cpu", "mem", "media", "disks"):
            print("   %-9s %s" % (k, obs.get(k)))
        print("   agent     %s" % (obs.get("agent") or {}).get("running"))
    hits = rule_hits(obs, st, cfg)
    if verbose:
        print("=== 规则命中:", [h[0] for h in hits] or "（无）")
        for h in hits:
            ok, why = gate(h, obs, st, cfg)
            print("   %-16s 说吗=%-5s  %s" % (h[0], ok, why))
    chosen = None
    for h in hits:
        ok, _ = gate(h, obs, st, cfg)
        if ok:
            chosen = h
            break
    if chosen is None and cfg.get("use_llm") and obs.get("media") not in ("call", "volume"):
        if verbose:
            print("=== 问本地 LLM …")
        h = llm_advice(obs, cfg)
        if h:
            ok, why = gate(h, obs, st, cfg)
            if verbose:
                print("   LLM 建议: %r  说吗=%s  %s" % (h[1], ok, why))
            if ok:
                chosen = h
    if chosen:
        key, text, level = chosen
        lvl = cfg.get("alert_level", "alert") if level == "alert" else cfg.get("say_level", "advice")
        print("\n★ 会说：%r  (level=%s)" % (text, lvl))

        try:
            body = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request("http://127.0.0.1:19387/dsh-tts-api/speak",
                                         data=body, headers={"content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                j = json.loads(r.read().decode("utf-8"))
            print("   TTS 已合成:", (j.get("url") or "")[:60])
        except Exception as e:
            print("   TTS 失败:", str(e)[:70])
        commit_say(chosen, st)
    else:
        print("\n（这一轮不说 —— 宁少勿多）")
    save_state(st)
    return 0

def run():
    cfg = load_conf()
    adv = Advisor(say=lambda t, l: print("[说/%s] %s" % (l, t), flush=True),
                  bubble=lambda t: None, conf=cfg)
    print("work_advisor 常驻运行（间隔 %s 分钟）… Ctrl+C 停" % cfg.get("interval_min"))
    while True:
        try:
            r = adv.tick(force=True)
            if r.get("said"):
                print("  →", r["chosen"]["text"], flush=True)
        except Exception as e:
            print("[advisor] 轮次出错: %s: %s" % (type(e).__name__, e), flush=True)
        time.sleep(60)

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--status" in sys.argv:
        sys.exit(status())
    if "--mute" in sys.argv:
        save_conf({"enabled": True})
        st = load_state(); st["muted"] = True; save_state(st)
        print("已静音（球下次读到就不再说话）")
        sys.exit(0)
    if "--unmute" in sys.argv:
        st = load_state(); st["muted"] = False; save_state(st)
        print("已取消静音")
        sys.exit(0)
    if "--mute-all" in sys.argv:
        save_conf({"enabled": False})
        print("已关闭总开关")
        sys.exit(0)
    if "--clip" in sys.argv:
        _cfg = load_conf(); _st = load_state()
        print("=== 分析当前剪贴板 ===")
        r = assist_tick(_cfg, _st, {"media": "ignore"})
        save_state(_st)
        print(json.dumps(r, ensure_ascii=False, indent=1)[:1500])
        sys.exit(0)
    if "--assist-selftest" in sys.argv:
        sys.exit(assist_selftest())
    if "--once" in sys.argv:
        sys.exit(once())
    if "--run" in sys.argv:
        sys.exit(run())
    print(__doc__)
    sys.exit(0)
