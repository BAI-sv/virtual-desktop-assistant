# -*- coding: utf-8 -*-
r"""
fairy_host.py —— Fairy 自己的本地小服务（让球在没有 DSH 时也能活）

【为什么要有它】
  以前球的接口全部来自 DSH 插件（127.0.0.1:19387）。DSH 一关，球就：
    · 读不到状态  -> 一直压暗、不显示绿点
    · 读不到回复  -> 不会念话
    · 说不出话    -> 哑巴
  这个服务用【和 DSH 插件一样的接口契约】在本地把这些补上，
  于是球只要换个 base 地址就能继续工作 —— 球自己不用知道谁在服务它。

【端口】19388（可用 FAIRY_LOCAL_BASE 或 fairy.json 的 endpoints.local_base 改）
  ★ 选 19388 前已实测：2026-10-10 该端口未被占用。

【实现的接口】（★ 契约与 DSH 插件一致，见 E:\\Downloads\\fairy\\fairy-plugin\\lib\\index.js）
  GET  /api/fairy/meta   -> 球的外形/状态。frames/size/fps 由本地 assets 现算；
                            running/progress 是【DSH 专属的活体状态】，
                            本地算不出来就返回 null（球本来就按 null=未知 处理）。
  GET  /api/fairy/reply  -> {ok,seq,text,truncated,fullLength,time,source,...}
                            ★ 有 DSH 就转发 DSH 的（保证内容一致），
                              并落盘持久化；DSH 不在就读【持久化文件】。
  POST /api/fairy/push   -> {"text":"..","source":".."} 入队（推进 seq）+ 落盘
                            ★ DSH 在线时会【转给 DSH】，否则写本地槽。
                            （为什么：球在线时轮询的是 DSH 的 reply，只写本地它读不到）
  POST /api/fairy/local_push -> 同上，但【只写本地槽，不转 DSH】。
                            想把文本"留给 DSH 不在时的球"就用它。
  POST /api/fairy/say    -> {"text":".."} -> WAV，内部直连 IndexTTS 9881
  POST /api/fairy/task   -> {"total","done","label"} 进度
  GET  /health           -> 自检（含 IndexTTS 通不通）

【持久化】D:\\FAIRY\\logs\\fairy_reply.json
  ★ 为什么必须是文件：球重启后要能接着取到"最后一条要念的话"，
    而不是拿到一个空槽（空槽会让球的 seq 基线错位）。

【★ seq 语义（很容易做错，特别说明）】
  球用 seq 单调递增来去重：只有 seq 变大才念。
  所以本服务【绝不凭空造 seq】：
    · 代理 DSH 时，seq 原样透传 DSH 的
    · DSH 不在时，读文件里【上一次记下的 seq】，seq 不变
    -> 于是球不会因为"切到了本地"就把同一句话再念一遍（也不会漏念已入队的）。

【静默启动】用 pythonw.exe，或 creationflags=0x00000008|0x00000200|0x08000000
  （★ 用户明确抱怨过黑窗，这里两个口子都堵上：启动脚本用 pythonw + 本文件不动窗口）

【用法】
  py -3.12 fairy_host.py            前台跑（排障看日志）
  py -3.12 fairy_host.py --selftest 只自检接口，起完就打一遍再退出
"""
import json
import os
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _safe_stdio():
    """
    ★★ 致命-2 的第一道兜底：启动就把标准输出/错误切成 utf-8 + errors=replace。

    【为什么要这么做·实测】stdout 是【管道】且编码是 gbk 时，日志里只要有
    '✓' / '★' 这种字符，print 就抛 UnicodeEncodeError。
    改造前 print 还在 try 外面 —— 于是【一行日志把整个请求打断】：
    24 个并发读里 14 个 'Remote end closed connection'。
    reconfigure 之后同样的字节流不会再抛；实在不行还有 log() 里的 try 兜第二道。
    """
    for name in ("stdout", "stderr"):
        s = getattr(sys, name, None)
        if s is None:            # pythonw.exe 下就是 None，正常
            continue
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_safe_stdio()

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import fairy_root                                  # noqa: E402  配置根
import fairy_endpoints as ep                       # noqa: E402  寻址/降级

PORT = 19388
for _cand in (os.environ.get("FAIRY_HOST_PORT"), None):
    if _cand:
        try:
            PORT = int(_cand)
        except Exception:
            pass
# 允许用 local_base 直接指定端口
try:
    _lb = ep.local_base()
    if ":" in _lb:
        PORT = int(_lb.rsplit(":", 1)[1].split("/")[0])
except Exception:
    pass

STATE = os.path.join(fairy_root.LOGS, "fairy_reply.json")
LOG = os.path.join(fairy_root.LOGS, "fairy_host.log")
ASSETS = fairy_root.ASSETS
VOICE_INDEX = fairy_root.VOICE_INDEX

_LOCK = threading.Lock()
_TASK = {"total": 0, "done": 0, "label": ""}
_STARTED = time.time()

# 球的外形信息：本地自己算（不再问 DSH）
_SIZE = 128
_FRAMES = []
_FIRST = None


def log(msg):
    line = "[host %s] %s" % (time.strftime("%H:%M:%S"), msg)
    # ★★ 致命-2：print 必须在 try 里面。
    #   改造前它裸在 try 外 —— gbk 管道 + '✓' = UnicodeEncodeError，
    #   异常直接冲出 do_GET/do_POST，连接被撕掉（实测 24 并发里挂 14 个）。
    #   这里再兜一层：编码坏了就退化成「丢掉不可编码字符」的字节写入，绝不抛。
    try:
        print(line, flush=True)
    except Exception:
        try:
            buf = getattr(sys.stdout, "buffer", None)
            if buf is not None:
                buf.write((line + "\n").encode("ascii", "replace"))
                buf.flush()
        except Exception:
            pass                      # ★ 打不出日志也绝不能连累请求
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        # ★ 控制大小：超过 512KB 就砍一半，别让日志无限长
        if os.path.exists(LOG) and os.path.getsize(LOG) > 512 * 1024:
            with open(LOG, "r", encoding="utf-8", errors="replace") as f:
                keep = f.readlines()[-1000:]
            with open(LOG, "w", encoding="utf-8") as f:
                f.writelines(keep)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ---------------------------------------------------------------- 素材扫描
def scan_assets():
    """扫球的两个素材目录（ball_128 / ball_96），和插件口径一致。"""
    global _SIZE, _FRAMES, _FIRST
    best = None
    for size in (128, 96, 64, 256):
        d = os.path.join(ASSETS, "ball_%d" % size)
        if os.path.isdir(d):
            fs = sorted(f for f in os.listdir(d)
                        if f.lower().endswith((".png", ".webp", ".gif", ".jpg")))
            if fs and (best is None or len(fs) > len(best[1])):
                best = (size, fs, d)
    if best:
        _SIZE, _FRAMES, d = best[0], best[1], best[2]
        _FIRST = _FRAMES[0] if _FRAMES else None
        log("素材: %s -> %d 帧 size=%d first=%s" % (d, len(_FRAMES), _SIZE, _FIRST))
    else:
        log("★ 警告：%s 下没找到 ball_* 素材目录" % ASSETS)


def voice_count():
    try:
        with open(VOICE_INDEX, encoding="utf-8") as f:
            j = json.load(f)
        if isinstance(j, dict) and isinstance(j.get("count"), int):
            return j["count"]
        if isinstance(j, dict) and isinstance(j.get("lines"), list):
            return len(j["lines"])
        if isinstance(j, list):
            return len(j)
    except Exception:
        pass
    return 0


# ---------------------------------------------------------------- 持久化
# ★★ 致命-1：整个「读 seq -> +1 -> 写回」必须是一把锁里的一个原子动作。
#   ThreadingHTTPServer 是【每个请求一个线程】—— 32 个并发 push 就是 32 个线程
#   同时读同一个 seq、各加 1、再各自覆写，于是 32 条只剩 5 条，seq 只前进 5。
#   更糟的是第二次压测里 seq 从 15153 掉回 4（有线程读失败返回了 seq=0，
#   再 +1 写回），而球靠 rseq > reply_seq 去重 —— seq 一退，球从此【永久不念】。
#   所以这里三件事一起做：① 一把锁 ② tmp 名唯一（别再互相撞 .tmp）
#   ③ 读失败【抛异常】让这次写放弃，绝不返回 seq=0。
_STATE_LOCK = threading.RLock()


class StateError(Exception):
    """状态文件读/写失败。★ 抛出来 = 这次写放弃；绝不拿 seq=0 去覆盖真实状态。"""


def _empty_state():
    """从来没写过状态时的初始值。★ 只有【文件不存在】才允许用这个。"""
    return {"seq": 0, "text": "", "full": "", "truncated": False,
            "time": 0, "source": "", "sessionId": "", "turn": 0, "step": 0}


def _read_state(retries=4):
    """
    读状态。★ 语义（很重要，别改回去）：
      · 文件【不存在】 -> 返回空状态（seq=0 是事实：还没人 push 过）
      · 文件存在但读不动/不是 JSON 对象 -> 抛 StateError，让调用方【放弃这次写】
    改造前是一律 return seq=0 —— 这正是 15153 掉到 4 的直接原因。
    瞬时错误（os.replace 撞车 / 杀软扫码）给几次重试。
    """
    last = None
    for i in range(max(1, retries)):
        try:
            with open(STATE, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                return d
            last = ValueError("状态文件不是 JSON 对象（%s）" % type(d).__name__)
        except FileNotFoundError:
            return _empty_state()
        except Exception as e:
            last = e
        time.sleep(0.03 * (i + 1))
    raise StateError("%s: %s" % (type(last).__name__, str(last)[:120]))


def _write_state(d):
    """
    原子写状态。★ 调用方必须持有 _STATE_LOCK（读改写整段一起锁）。
    返回 True/False；tmp 名字带 pid+线程号+随机后缀 —— 固定 ".tmp" 在高并发下
    必然互相撞车（实测几十条 WinError 32 / Permission denied）。
    """
    tmp = "%s.tmp.%d.%d.%s" % (STATE, os.getpid(), threading.get_ident(),
                               uuid.uuid4().hex[:8])
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
            f.flush()
            try:
                os.fsync(f.fileno())        # ★ 落盘再 replace，断电/崩溃也不留空文件
            except Exception:
                pass
        os.replace(tmp, STATE)              # 原子替换，避免读到半个文件
        return True
    except Exception as e:
        log("★ 写状态失败: %s" % e)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        return False


def _clean_for_speech(s):
    """
    卫生处理：把不该念的东西去掉（代码块、markdown 符号、链接）。
    ★ 只做保守清理 —— 宁可原样念，也不要把用户的话吃掉。
    """
    if not s:
        return ""
    out = []
    in_fence = False
    for line in str(s).splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        out.append(line)
    t = "\n".join(out)
    import re
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)   # markdown 链接 -> 文字
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[*_#>|]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


MAX_CHARS = 180


def _speakable(s, limit=MAX_CHARS):
    """太长就截断到最后一个句号，返回 (要念的, 是否截断)。"""
    s = (s or "").strip()
    if len(s) <= limit:
        return s, False
    cut = s[:limit]
    for p in ("。", "！", "？", ".", "!", "?", "；", ";"):
        i = cut.rfind(p)
        if i > limit // 2:
            return cut[:i + 1], True
    return cut, True


# ---------------------------------------------------------------- 代理 DSH
def dsh_get_json(path, timeout=3.0):
    """从 DSH 取 JSON（DSH 不在就返回 None）。"""
    import urllib.request
    url = ep.dsh_base() + path
    try:
        req = urllib.request.Request(url, headers={"cache-control": "no-store"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def dsh_alive():
    """DSH 是否可用。大脑模式 prefer=local（只用本地）时，视为 DSH 离线。"""
    if ep.prefer() == "local":
        return False
    return ep.probe(ep.dsh_base() + "/api/fairy/meta")


def _write_prefer(v):
    """写 fairy.json 的 endpoints.prefer（auto / dsh / local）。"""
    try:
        p = fairy_root.CONFIG
        c = {}
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                c = json.load(f)
        e = dict(c.get("endpoints") or {})
        e["prefer"] = v
        c["endpoints"] = e
        with open(p, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def _match_cmd(t):
    """语音命令识别（去掉空格和标点后匹配关键词）。返回 local/dsh/auto/status 或 None。"""
    s = str(t or "").lower().replace(" ", "").replace("，", "").replace(",", "").replace(".", "")
    if any(k in s for k in ("不用dsh", "关闭dsh", "停用dsh", "关掉dsh", "关dsh",
                            "使用本地", "用本地", "本地模式", "本地大脑", "切到本地",
                            "切换到本地", "切本地", "使用strata", "用strata", "用本地模型")):
        return "local"
    if any(k in s for k in ("使用dsh", "用dsh", "切到dsh", "切换到dsh", "打开dsh",
                            "启用dsh", "开dsh", "dsh模式", "用dsh大脑", "dsd模式",
                            "使用dsd", "用dsd")):
        return "dsh"
    if any(k in s for k in ("自动模式", "用自动", "恢复自动", "自动大脑", "自动切换", "用自动模式", "自动")):
        return "auto"
    if any(k in s for k in ("打开api", "api状态", "大模型状态", "模型状态",
                            "看看模型", "查看大脑", "大脑状态", "看看大脑", "打开大模型")):
        return "status"
    return None


def _cmd_status_text():
    """状态摘要文本：大脑模式 + DSH / Strata / Ollama 在线情况。"""
    _names = {"auto": "自动（DSH 优先）", "dsh": "DSH 内核", "local": "本地内核（单机模式）"}
    p = ep.prefer()
    lines = ["当前大脑模式：%s" % _names.get(p, p)]
    lines.append("DSH：%s" % ("在线" if ep.probe(ep.dsh_base() + "/api/fairy/meta") else "离线"))
    _st = None
    try:
        import socket as _sock
        _s = _sock.create_connection(("127.0.0.1", 8080), 0.8)
        _s.close()
        _st = "在线"
    except OSError:
        _st = "已停止"
    lines.append("Strata（8080）：%s" % _st)
    _ol = "离线"
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=2) as r:
            _ol = "在线"
    except Exception:
        pass
    lines.append("Ollama（11434）：%s" % _ol)
    return "，".join(lines) + "。"


def _bump_seq(text, src):
    """把命令确认/状态文本写入本地状态槽（seq+1），让球能念出来。"""
    try:
        with _STATE_LOCK:
            st = _read_state()
            st.update({"seq": int(st.get("seq") or 0) + 1, "text": text, "full": text,
                       "truncated": False, "fullLength": len(text),
                       "time": int(time.time() * 1000), "source": src,
                       "sessionId": "", "turn": 0, "step": 0,
                       "persisted_at": int(time.time() * 1000)})
            _write_state(st)
            return st["seq"]
    except StateError:
        return None


def _apply_cmd(cmd, cleaned, src):
    """执行语音命令：status 返回状态摘要；local/dsh/auto 切换 prefer 并确认。"""
    if cmd == "status":
        _t = _cmd_status_text()
        _s = _bump_seq(_t, src)
        return (200, {"ok": True, "seq": _s or 0, "text": _t, "truncated": False,
                      "fullLength": len(_t), "source": src, "_via": "cmd"})
    _names = {"dsh": "DSH 内核", "local": "本地内核（单机模式）", "auto": "自动（DSH 优先）"}
    _old = ep.prefer()
    if _write_prefer(cmd):
        _t = "已切换到%s。" % _names[cmd]
        log("语音命令: prefer %s -> %s" % (_old, cmd))
    else:
        _t = "切换失败：配置写入失败，当前仍是%s。" % _names.get(_old, _old)
        log("语音命令: 切换 %s 失败（写入错误）" % cmd)
    _s = _bump_seq(_t, src)
    return (200, {"ok": True, "seq": _s or 0, "text": _t, "truncated": False,
                  "fullLength": len(_t), "source": src, "_via": "cmd"})


# ---------------------------------------------------------------- 业务
def do_reply():
    """
    ★ 核心：有 DSH 就用 DSH 的（内容与以前完全一致），没有就用持久化文件。
    两种情况都会把结果落盘，所以球重启 / DSH 重启都不丢。

    【★ 优先级为什么不能"无脑信 DSH"】
      自检时实测踩到过：本地 push 推进到 seq=14820 之后，
      下一次 reply 又无条件抄了 DSH 的 seq=14819 —— 本地那条就被抹掉了。
      所以规则是【谁新信谁】：
        · DSH 的 seq 更大            -> 用 DSH 的（正常情况，内容与以前一致）
        · 两边 seq 一样              -> 比时间戳，新的那个赢
        · 本地 seq 更大（本地 push 过）-> 用本地的，别被过期的 DSH 覆盖
    """
    d = None
    if dsh_alive():
        d = dsh_get_json("/api/fairy/reply")
    dsh_ok = bool(d and d.get("ok"))

    d_seq = int(d.get("seq") or 0) if dsh_ok else -1
    d_time = int(d.get("time") or 0) if dsh_ok else 0

    try:
        with _STATE_LOCK:
            st = _read_state()
    except StateError as e:
        # ★ 本地状态读不动时【绝不退回 seq=0】。有 DSH 就如实以 DSH 为准（本轮不写本地槽）；
        #   连 DSH 都没有就抛出去 —— 调用方返回 500，球那边只是记一条错误、不会动它的基线。
        if not dsh_ok:
            log("★ reply: 本地状态读失败，且 DSH 无数据 -> 返回 500（绝不返回 seq=0）: %s" % e)
            raise
        log("★ reply: 本地状态读失败(%s) —— 本轮用 DSH 数据，不写本地槽" % e)
        return {"ok": True, "seq": d_seq, "text": str(d.get("text") or ""),
                "truncated": bool(d.get("truncated")),
                "fullLength": int(d.get("fullLength") or 0), "time": d_time,
                "sessionId": str(d.get("sessionId") or ""),
                "turn": d.get("turn") or 0, "step": d.get("step") or 0,
                "source": str(d.get("source") or "dsh"), "_src": "dsh"}

    l_seq = int(st.get("seq") or 0)
    l_time = int(st.get("time") or 0)

    use_dsh = dsh_ok and (d_seq > l_seq or (d_seq == l_seq and d_time > l_time))

    if use_dsh:
        new = {
            "seq": d_seq,
            "text": str(d.get("text") or ""),
            "full": st.get("full") or "",
            "truncated": bool(d.get("truncated")),
            "fullLength": int(d.get("fullLength") or 0),
            "time": d_time,
            "source": str(d.get("source") or "dsh"),
            "sessionId": str(d.get("sessionId") or ""),
            "turn": d.get("turn") or 0,
            "step": d.get("step") or 0,
            "persisted_at": int(time.time() * 1000),
        }
        # ★ 只在真的变了才写盘（球 900ms 轮一次，别每轮都写）
        if (new["seq"] != st.get("seq")) or (new["text"] != st.get("text")):
            with _STATE_LOCK:                 # ★ 读改写都在锁里
                _write_state(new)
            log("reply 同步自 DSH: seq=%s text=%r" % (new["seq"], new["text"][:30]))
        return {"ok": True, "seq": new["seq"], "text": new["text"],
                "truncated": new["truncated"], "fullLength": new["fullLength"],
                "time": new["time"], "sessionId": new["sessionId"],
                "turn": new["turn"], "step": new["step"],
                "source": new["source"], "_src": "dsh"}

    # ★ DSH 不在（或被本地新增的内容压过）：seq 原样返回
    #   绝不 +1 —— 那样球会把同一句话再念一遍
    return {"ok": True, "seq": l_seq,
            "text": str(st.get("text") or ""),
            "truncated": bool(st.get("truncated")),
            "fullLength": int(st.get("fullLength") or 0),
            "time": l_time,
            "sessionId": str(st.get("sessionId") or ""),
            "turn": st.get("turn") or 0, "step": st.get("step") or 0,
            "source": str(st.get("source") or ""),
            "_src": "dsh" if (dsh_ok and d_seq == l_seq) else "local-file"}


def do_push(text, source="external"):
    """
    【语义说明·别搞混】本方法是【代理到 DSH + 落盘】，不是纯本地。
      因为球在 DSH 在线时轮询的是 DSH 的 reply，如果这里只写本地，
      球就读不到这条 push（实测确认过这个坑）。
    所以：
      · 想让【DSH 在线时的球】念   -> 用 /api/fairy/push（本方法，会转给 DSH）
      · 想【保证本地槽被写入】     -> 用 /api/fairy/local_push（见 do_local_push）
    """
    raw = str(text or "")
    cleaned = _clean_for_speech(raw)
    if len(cleaned) < 2:
        return 400, {"ok": False, "error": "清洗后为空（太短或全是代码块）"}
    src = str(source or "external")[:40]

    # ★ 语音命令路由：使用/切换 DSH、本地、自动；查看 API 状态
    cmd = _match_cmd(cleaned)
    if cmd:
        return _apply_cmd(cmd, cleaned, src)

    # ★ 先试着交给 DSH —— 只有 DSH 推进了它的 seq，在线时的球才念得到
    if dsh_alive():
        import urllib.request
        try:
            req = urllib.request.Request(
                ep.dsh_base() + "/api/fairy/push",
                data=json.dumps({"text": cleaned, "source": src},
                                ensure_ascii=False).encode("utf-8"),
                headers={"content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=8) as r:
                j = json.loads(r.read().decode("utf-8"))
            if isinstance(j, dict) and j.get("ok"):
                spoken = str(j.get("text") or "")
                try:
                    with _STATE_LOCK:                 # ★ 致命-1：读改写成原子
                        st = _read_state()
                        st.update({"seq": int(j.get("seq") or 0), "text": spoken,
                                   "full": cleaned, "truncated": bool(j.get("truncated")),
                                   "fullLength": int(j.get("fullLength") or len(cleaned)),
                                   "time": int(time.time() * 1000), "source": src,
                                   "persisted_at": int(time.time() * 1000)})
                        if not _write_state(st):
                            raise StateError("写状态文件失败")
                except StateError as e:
                    # ★ DSH 已经收下这条 push 了 —— 这时【绝不能】再退回本地槽重推一遍
                    #   （那会把 seq 推两次）。如实返回：DSH 成功、本地槽没记上。
                    log("★ push: DSH 已收下，但本地槽没写上(%s)" % e)
                    return 200, {"ok": True, "seq": int(j.get("seq") or 0), "text": spoken,
                                 "truncated": bool(j.get("truncated")),
                                 "fullLength": int(j.get("fullLength") or len(cleaned)),
                                 "source": src, "_via": "dsh",
                                 "_persist": "failed: %s" % str(e)[:80]}
                log("push->DSH: seq=%s source=%s text=%r" % (st["seq"], src, spoken[:40]))
                return 200, {"ok": True, "seq": st["seq"], "text": spoken,
                             "truncated": st["truncated"],
                             "fullLength": st["fullLength"], "source": src,
                             "_via": "dsh"}
        except Exception as e:
            log("push 转 DSH 失败(%s) -> 退回本地槽" % str(e)[:80])
    # 退回：只写本地槽
    return do_local_push(cleaned, src)


def do_local_push(text, source="local"):
    """★ 纯本地入队：直接写持久化槽并推进 seq（DSH 不在时球走的就是这里）。"""
    raw = str(text or "")
    cleaned = _clean_for_speech(raw)
    if len(cleaned) < 2:
        return 400, {"ok": False, "error": "清洗后为空（太短或全是代码块）"}
    spoken, truncated = _speakable(cleaned)
    try:
        with _STATE_LOCK:                 # ★★ 致命-1：读 seq -> +1 -> 写回，全程一把锁
            st = _read_state()
            st.update({
                "seq": int(st.get("seq") or 0) + 1,   # ★ push 是"新内容"，必须推进 seq
                "text": spoken, "full": cleaned, "truncated": truncated,
                "fullLength": len(cleaned), "time": int(time.time() * 1000),
                "source": str(source or "external")[:40],
                "sessionId": "", "turn": 0, "step": 0,
                "persisted_at": int(time.time() * 1000),
            })
            if not _write_state(st):
                raise StateError("写状态文件失败（磁盘满/权限/杀软？）")
    except StateError as e:
        log("★ local_push 放弃（不推进 seq，也不覆盖旧状态）: %s" % e)
        return 500, {"ok": False,
                     "error": "状态持久化失败，已放弃这次入队（避免 seq 倒退或覆盖）：%s"
                              % str(e)[:140]}
    log("local_push: seq=%s source=%s text=%r" % (st["seq"], st["source"], spoken[:40]))
    return 200, {"ok": True, "seq": st["seq"], "text": spoken,
                 "truncated": truncated, "fullLength": len(cleaned),
                 "source": st["source"], "_via": "local"}


def do_task(o):
    global _TASK
    with _LOCK:
        if isinstance(o, dict):
            for k in ("total", "done"):
                try:
                    if o.get(k) is not None:
                        _TASK[k] = int(o[k])
                except Exception:
                    pass
            if isinstance(o.get("label"), str):
                _TASK["label"] = o["label"]
            if _TASK["total"] == 0:                # 清空
                _TASK["done"] = 0
                _TASK["label"] = ""
        return {"ok": True, "total": _TASK["total"], "done": _TASK["done"],
                "label": _TASK["label"]}


def do_meta():
    """
    外形/状态。★ frames/size/fps 本地算得出来就给真值；
       running/progress/mode/context 是 DSH 的活体状态 ——
       本地没有 agent，返回 null（球按 null=未知 处理，不会误判"已完成"）。
    """
    b, src = ep.api_base()
    return {
        "ok": True,
        "code": "v2-local-host",
        "size": _SIZE,
        "frames": len(_FRAMES),
        "firstFrame": _FIRST,
        "fps": 33,
        "autoSpeak": True,
        "voiceCount": voice_count(),
        "ttsUrl": ep.indextts_url(),
        "uptimeMs": int((time.time() - _STARTED) * 1000),
        # ★ 活体状态：本地服务不冒充 DSH，一律 null
        "running": None,
        "steps": 0,
        "lastTool": "",
        "doing": "",
        "mood": {"label": ""},
        "mode": {"label": "本地独立模式"},
        "progress": (max(0, min(100, round(_TASK["done"] * 100.0 / _TASK["total"])))
                     if _TASK["total"] > 0 else None),
        "task": dict(_TASK),
        "context": {},
        "_src": src,
        "_server": "fairy_host",
    }


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "fairy_host/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        pass                                  # ★ 静默：默认实现会往 stderr 刷

    # ---- 小工具 ----
    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _read_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except Exception:
            n = 0
        raw = self.rfile.read(n) if n > 0 else b""
        try:
            o = json.loads(raw or b"{}")
            return o if isinstance(o, dict) else {}
        except Exception:
            return None

    # ---- GET ----
    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/api/fairy/meta", "/api/fairy/meta/"):
            self._send(200, do_meta())
        elif p in ("/api/fairy/reply", "/api/fairy/reply/"):
            try:
                self._send(200, do_reply())
            except StateError as e:
                # ★ 读不到本地状态时明确报 500 —— 球收到错误只记一条日志、不动它的基线；
                #   绝不能返回 seq=0（球会把基线重置，之后要么重复念、要么永久哑）。
                self._send(500, {"ok": False, "error": "state unreadable: %s" % str(e)[:140]})
        elif p in ("/health", "/healthz", "/"):
            ok_tts = ep.probe(ep._indextts_health() or "", ttl=0) if ep._indextts_health() else False
            self._send(200, {"ok": True, "server": "fairy_host", "port": PORT,
                             "uptimeMs": int((time.time() - _STARTED) * 1000),
                             "frames": len(_FRAMES), "voiceCount": voice_count(),
                             "dsh_up": dsh_alive(), "indextts_up": ok_tts,
                             "endpoints": ep.status_base()})
        else:
            self._send(404, {"ok": False, "error": "not found", "path": p})

    # ---- POST ----
    def do_POST(self):
        p = self.path.split("?")[0]
        if p in ("/api/fairy/say", "/api/fairy/say/"):
            o = self._read_json()
            if o is None:
                self._send(400, {"ok": False, "error": "body 不是合法 JSON"})
                return
            text = str(o.get("text") or "").strip()
            if not text:
                self._send(400, "text required", "text/plain; charset=utf-8")
                return
            r = ep.tts_get(text)
            if r.get("ok") and r.get("data"):
                self._send(200, r["data"], "audio/wav",
                           extra={"x-fairy-voice": r.get("voice") or "indextts-clone",
                                  "x-fairy-route": r.get("route") or "?"})
                log("say: route=%s voice=%s %dB" % (r.get("route"), r.get("voice"),
                                                    len(r["data"])))
            else:
                self._send(502, {"ok": False, "route": r.get("route"),
                                 "error": "所有语音通道都失败", "tried": r.get("tried")})
                log("say 失败: %s" % json.dumps(r.get("tried"), ensure_ascii=False)[:300])
            return

        if p in ("/api/fairy/push", "/api/fairy/push/"):
            o = self._read_json()
            if o is None:
                self._send(400, {"ok": False, "error": "body 不是合法 JSON"})
                return
            try:
                code, body = do_push(o.get("text"), o.get("source") or "external")
            except StateError as e:
                code, body = 500, {"ok": False, "error": "state unreadable: %s" % str(e)[:140]}
            self._send(code, body)
            return

        # ★ 纯本地入队（不转 DSH）：写本地槽并推进 seq
        if p in ("/api/fairy/local_push", "/api/fairy/local_push/"):
            o = self._read_json()
            if o is None:
                self._send(400, {"ok": False, "error": "body 不是合法 JSON"})
                return
            try:
                code, body = do_local_push(o.get("text"), o.get("source") or "local")
            except StateError as e:
                code, body = 500, {"ok": False, "error": "state unreadable: %s" % str(e)[:140]}
            self._send(code, body)
            return

        if p in ("/api/fairy/task", "/api/fairy/task/"):
            o = self._read_json()
            self._send(200, do_task(o or {}))
            return

        self._send(404, {"ok": False, "error": "not found", "path": p})

    def do_HEAD(self):
        self._send(200, b"")


# ---------------------------------------------------------------- 入口
def selftest():
    """起服务 -> 打一遍所有接口 -> 关掉。给排障用。"""
    import urllib.error
    import urllib.request
    B = "http://127.0.0.1:%d" % PORT
    out = {}
    print("== fairy_host 自检 (%s) ==" % B)

    def get(p):
        with urllib.request.urlopen(B + p, timeout=30) as r:
            return r.status, r.read()

    def post(p, obj):
        req = urllib.request.Request(
            B + p, data=json.dumps(obj, ensure_ascii=False).encode("utf-8"),
            headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            # ★ 自检不该因为某个接口返回 502 就自己崩掉（语音通道全挂时正是会这样）——
            #   如实把状态码和错误体打出来，继续往下自检。
            return e.code, e.read(), dict(e.headers or {})

    st, b = get("/health")
    out["health"] = json.loads(b)
    print("GET  /health        ->", st, json.dumps(out["health"], ensure_ascii=False))

    st, b = get("/api/fairy/meta")
    out["meta"] = json.loads(b)
    print("GET  /api/fairy/meta->", st, "frames=%s size=%s voiceCount=%s"
          % (out["meta"]["frames"], out["meta"]["size"], out["meta"]["voiceCount"]))

    st, b = get("/api/fairy/reply")
    out["reply_before"] = json.loads(b)
    print("GET  /api/fairy/reply ->", st, "seq=%s text=%r"
          % (out["reply_before"]["seq"], out["reply_before"]["text"][:30]))

    st, b, _ = post("/api/fairy/push", {"text": "自检：本地服务已经跑起来了。", "source": "selftest"})
    out["push"] = json.loads(b)
    print("POST /api/fairy/push ->", st, "seq=%s text=%r"
          % (out["push"]["seq"], out["push"]["text"][:30]))

    st, b = get("/api/fairy/reply")
    out["reply_after"] = json.loads(b)
    print("GET  /api/fairy/reply ->", st, "seq=%s text=%r src=%s"
          % (out["reply_after"]["seq"], out["reply_after"]["text"][:30], out["reply_after"].get("_src")))

    st, b, _ = post("/api/fairy/task", {"total": 4, "done": 1, "label": "自检"})
    out["task"] = json.loads(b)
    st, b = get("/api/fairy/meta")
    out["meta_after_task"] = {"progress": json.loads(b)["progress"]}
    print("POST /api/fairy/task ->", st, json.dumps(out["task"], ensure_ascii=False),
          " progress=", json.loads(b)["progress"])

    st, b, h = post("/api/fairy/say", {"text": "主人，本地服务已经能说话了。"})
    out["say"] = {"status": st, "bytes": len(b), "ctype": h.get("Content-Type"),
                  "voice": h.get("x-fairy-voice"), "route": h.get("x-fairy-route")}
    p = os.path.join(fairy_root.LOGS, "host_selftest.wav")
    open(p, "wb").write(b)
    print("POST /api/fairy/say  ->", st, out["say"], "->", p)

    print("\n自检汇总:", json.dumps({k: v for k, v in out.items()
                                    if k != "meta"}, ensure_ascii=False)[:800])
    return out


def main():
    if "--selftest" in sys.argv:
        scan_assets()
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        time.sleep(0.4)
        try:
            selftest()
        finally:
            srv.shutdown()
        return 0

    scan_assets()
    log("启动 fairy_host :%d  logs=%s  state=%s" % (PORT, fairy_root.LOGS, STATE))
    log("路由: %s" % json.dumps(ep.status_base(), ensure_ascii=False))
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    srv.daemon_threads = True
    log("就绪: http://127.0.0.1:%d" % PORT)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
