# -*- coding: utf-8 -*-
import json
import os
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

def _safe_stdio():
    for name in ("stdout", "stderr"):
        s = getattr(sys, name, None)
        if s is None:
            continue
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

_safe_stdio()

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import fairy_root
import fairy_endpoints as ep

PORT = 19388
for _cand in (os.environ.get("FAIRY_HOST_PORT"), None):
    if _cand:
        try:
            PORT = int(_cand)
        except Exception:
            pass

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


_SIZE = 128
_FRAMES = []
_FIRST = None

def log(msg):
    line = "[host %s] %s" % (time.strftime("%H:%M:%S"), msg)


    try:
        print(line, flush=True)
    except Exception:
        try:
            buf = getattr(sys.stdout, "buffer", None)
            if buf is not None:
                buf.write((line + "\n").encode("ascii", "replace"))
                buf.flush()
        except Exception:
            pass
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)

        if os.path.exists(LOG) and os.path.getsize(LOG) > 512 * 1024:
            with open(LOG, "r", encoding="utf-8", errors="replace") as f:
                keep = f.readlines()[-1000:]
            with open(LOG, "w", encoding="utf-8") as f:
                f.writelines(keep)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def scan_assets():
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


_STATE_LOCK = threading.RLock()

class StateError(Exception):
    pass

def _empty_state():
    return {"seq": 0, "text": "", "full": "", "truncated": False,
            "time": 0, "source": "", "sessionId": "", "turn": 0, "step": 0}

def _read_state(retries=4):
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
    tmp = "%s.tmp.%d.%d.%s" % (STATE, os.getpid(), threading.get_ident(),
                               uuid.uuid4().hex[:8])
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
            f.flush()
            try:
                os.fsync(f.fileno())
            except Exception:
                pass
        os.replace(tmp, STATE)
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
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[*_#>|]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

MAX_CHARS = 180

def _speakable(s, limit=MAX_CHARS):
    s = (s or "").strip()
    if len(s) <= limit:
        return s, False
    cut = s[:limit]
    for p in ("。", "！", "？", ".", "!", "?", "；", ";"):
        i = cut.rfind(p)
        if i > limit // 2:
            return cut[:i + 1], True
    return cut, True


def dsh_get_json(path, timeout=3.0):
    import urllib.request
    url = ep.dsh_base() + path
    try:
        req = urllib.request.Request(url, headers={"cache-control": "no-store"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None

def dsh_alive():
    if ep.prefer() == "local":
        return False
    return ep.probe(ep.dsh_base() + "/api/fairy/meta")


def do_reply():
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

        if (new["seq"] != st.get("seq")) or (new["text"] != st.get("text")):
            with _STATE_LOCK:
                _write_state(new)
            log("reply 同步自 DSH: seq=%s text=%r" % (new["seq"], new["text"][:30]))
        return {"ok": True, "seq": new["seq"], "text": new["text"],
                "truncated": new["truncated"], "fullLength": new["fullLength"],
                "time": new["time"], "sessionId": new["sessionId"],
                "turn": new["turn"], "step": new["step"],
                "source": new["source"], "_src": "dsh"}


    return {"ok": True, "seq": l_seq,
            "text": str(st.get("text") or ""),
            "truncated": bool(st.get("truncated")),
            "fullLength": int(st.get("fullLength") or 0),
            "time": l_time,
            "sessionId": str(st.get("sessionId") or ""),
            "turn": st.get("turn") or 0, "step": st.get("step") or 0,
            "source": str(st.get("source") or ""),
            "_src": "dsh" if (dsh_ok and d_seq == l_seq) else "local-file"}

def _write_prefer(v):
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
    if any(k in s for k in ("打开api", "打开apI", "api状态", "大模型状态", "模型状态",
                            "看看模型", "查看大脑", "大脑状态", "看看大脑", "打开大模型")):
        return "status"
    return None


def _cmd_status_text():
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


def do_push(text, source="external"):
    raw = str(text or "")
    cleaned = _clean_for_speech(raw)
    if len(cleaned) < 2:
        return 400, {"ok": False, "error": "清洗后为空（太短或全是代码块）"}
    src = str(source or "external")[:40]

    cmd = _match_cmd(cleaned)
    if cmd:
        return _apply_cmd(cmd, cleaned, src)

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
                    with _STATE_LOCK:
                        st = _read_state()
                        st.update({"seq": int(j.get("seq") or 0), "text": spoken,
                                   "full": cleaned, "truncated": bool(j.get("truncated")),
                                   "fullLength": int(j.get("fullLength") or len(cleaned)),
                                   "time": int(time.time() * 1000), "source": src,
                                   "persisted_at": int(time.time() * 1000)})
                        if not _write_state(st):
                            raise StateError("写状态文件失败")
                except StateError as e:


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

    return do_local_push(cleaned, src)

def do_local_push(text, source="local"):
    raw = str(text or "")
    cleaned = _clean_for_speech(raw)
    if len(cleaned) < 2:
        return 400, {"ok": False, "error": "清洗后为空（太短或全是代码块）"}
    spoken, truncated = _speakable(cleaned)
    try:
        with _STATE_LOCK:
            st = _read_state()
            st.update({
                "seq": int(st.get("seq") or 0) + 1,
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
            if _TASK["total"] == 0:
                _TASK["done"] = 0
                _TASK["label"] = ""
        return {"ok": True, "total": _TASK["total"], "done": _TASK["done"],
                "label": _TASK["label"]}

def do_meta():
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


class Handler(BaseHTTPRequestHandler):
    server_version = "fairy_host/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *a):
        pass


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


    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/api/fairy/meta", "/api/fairy/meta/"):
            self._send(200, do_meta())
        elif p in ("/api/fairy/reply", "/api/fairy/reply/"):
            try:
                self._send(200, do_reply())
            except StateError as e:


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


def selftest():
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
