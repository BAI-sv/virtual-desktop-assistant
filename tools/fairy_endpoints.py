# -*- coding: utf-8 -*-
import json
import os
import threading
import time
import urllib.error
import urllib.request


try:
    import fairy_root as _root
    _LOGS = _root.LOGS
except Exception:
    _root = None
    _LOGS = None

_HERE = os.path.dirname(os.path.abspath(__file__))


DEFAULTS = {
    "dsh_base": "http://127.0.0.1:19387",
    "local_base": "http://127.0.0.1:19388",
    "indextts_url": "http://127.0.0.1:9881/tts",
    "ref_audio": os.path.join(
        (_root.VOICE if _root else r"<FAIRY_ROOT>\voice"), "index_voices", "fairy.wav"),
    "prefer": "auto",
}


ENV = {
    "dsh_base": "FAIRY_DSH_BASE",
    "local_base": "FAIRY_LOCAL_BASE",
    "indextts_url": "FAIRY_INDEXTTS_URL",
    "ref_audio": "FAIRY_REF_AUDIO",
    "prefer": "FAIRY_ENDPOINT_PREFER",
}

def _cfg():
    p = None
    if _root is not None:
        p = getattr(_root, "CONFIG", None)
    p = p or os.path.join(os.path.dirname(_HERE), "fairy.json")
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
        seg = d.get("endpoints")
        return seg if isinstance(seg, dict) else {}
    except Exception:
        return {}

_CFG = _cfg()

def _get(key):
    v = os.environ.get(ENV[key])
    if v:
        return str(v).strip()
    v = _CFG.get(key)
    if isinstance(v, str) and v.strip():
        return v.strip()
    return DEFAULTS[key]


def dsh_base():
    return _get("dsh_base").rstrip("/")

def local_base():
    return _get("local_base").rstrip("/")

def indextts_url():
    return _get("indextts_url")

def ref_audio():
    return _get("ref_audio")

def prefer():
    v = os.environ.get(ENV["prefer"])
    if v:
        return str(v).strip().lower()
    try:
        seg = _cfg()
        v = seg.get("prefer")
        if isinstance(v, str) and v.strip():
            return v.strip().lower()
    except Exception:
        pass
    return (DEFAULTS.get("prefer") or "auto").lower()


_LOCK = threading.Lock()
_PROBE = {}
PROBE_TTL = 20.0

def probe(url, timeout=2.5, ttl=None):
    if not url:
        return False
    ttl = PROBE_TTL if ttl is None else ttl
    now = time.time()
    with _LOCK:
        hit = _PROBE.get(url)
        if hit and (now - hit[0]) < ttl:
            return hit[1]
    ok = False
    try:
        req = urllib.request.Request(url, headers={"cache-control": "no-store"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ok = 200 <= r.status < 500
    except urllib.error.HTTPError as e:

        ok = e.code in (404, 405, 400)
    except Exception:
        ok = False
    with _LOCK:


        _PROBE[url] = (time.time(), ok)
    return ok

def invalidate(url=None):
    with _LOCK:
        if url is None:
            _PROBE.clear()
        else:
            _PROBE.pop(url, None)

def probe_all():
    out = {}
    for name, u in (("dsh", dsh_base() + "/api/fairy/meta"),
                    ("local_host", local_base() + "/api/fairy/meta"),
                    ("indextts", _indextts_health())):
        if u:
            out[name] = {"url": u, "up": probe(u, ttl=0)}
    return out

def _indextts_health():
    u = indextts_url()
    i = u.find("/", len("http://"))
    if i < 0:
        return None
    return u[:i] + "/health"


_BASE_CACHE = {"t": 0.0, "base": None, "src": None}
BASE_TTL = 5.0

def resolve_base(force=False):
    p = prefer()
    d, l = dsh_base(), local_base()
    if p == "dsh":
        return d, "dsh"
    if p == "local":
        return l, "local"

    with _LOCK:
        if (not force) and _BASE_CACHE["base"] and \
                (time.time() - _BASE_CACHE["t"]) < BASE_TTL:
            return _BASE_CACHE["base"], _BASE_CACHE["src"]


    t0 = time.time()
    budget = 1.5

    base, src = l, "local"
    for cand, s in ((d, "dsh"), (l, "local")):
        left = budget - (time.time() - t0)
        if left <= 0.05:
            break
        if probe(cand + "/api/fairy/meta", ttl=BASE_TTL, timeout=min(1.2, left)):
            base, src = cand, s
            break
    with _LOCK:
        _BASE_CACHE.update({"t": time.time(), "base": base, "src": src})
    return base, src


api_base = resolve_base

def meta_url():
    b, _ = resolve_base()
    return b + "/api/fairy/meta"

def reply_url():
    b, _ = resolve_base()
    return b + "/api/fairy/reply"

def push_url():
    b, _ = resolve_base()
    return b + "/api/fairy/push"

def tts_targets():
    return [
        {"route": "indextts-direct", "url": indextts_url(),
         "voice": "indextts-clone", "note": "直连本机 IndexTTS，不经过 DSH"},
        {"route": "dsh-plugin", "url": dsh_base() + "/api/fairy/say",
         "voice": "indextts-clone", "note": "DSH 插件转发（改造前的老路）"},
        {"route": "local-host", "url": local_base() + "/api/fairy/say",
         "voice": "indextts-clone", "note": "本地 fairy_host"},
        {"route": "dsh-tts-api", "url": dsh_base() + "/dsh-tts-api/speak",
         "voice": "edge-tts-fallback", "note": "最后手段：Edge 通用音色"},
    ]

def status_base():
    b, s = resolve_base()
    return {"base": b, "source": s, "prefer": prefer(),
            "dsh": dsh_base(), "local": local_base()}


_TTS_LOG = []

def _log_route(route, voice, nbytes, ms, err=None):
    rec = {"t": time.strftime("%H:%M:%S"), "route": route, "voice": voice,
           "bytes": nbytes, "ms": int(ms)}
    if err:
        rec["err"] = str(err)[:120]
    _TTS_LOG.append(rec)
    del _TTS_LOG[:-40]

    try:
        if _LOGS:
            os.makedirs(_LOGS, exist_ok=True)
            p = os.path.join(_LOGS, "fairy_tts_route.json")
            old = {}
            try:
                with open(p, encoding="utf-8") as f:
                    old = json.load(f)
            except Exception:
                old = {}
            if old.get("last_route") != route:
                with open(p, "w", encoding="utf-8") as f:
                    json.dump({"last_route": route, "last_voice": voice,
                               "last_bytes": nbytes, "last_time": rec["t"],
                               "prefer": prefer(), "source": resolve_base()[1],
                               "recent": _TTS_LOG[-10:]},
                              f, ensure_ascii=False, indent=1)
    except Exception:
        pass

def _post_bytes(url, body, timeout, want_voice_header=False):
    req = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        v = r.headers.get("x-fairy-voice")
    return raw, v

def tts_get(text, timeout=120, min_bytes=500):
    text = (text or "").strip()
    if len(text) < 1:
        return {"ok": False, "data": None, "voice": "?", "route": "none",
                "ms": 0, "tried": [], "error": "text empty"}

    tried = []
    t_all = time.time()

    def finish(data, voice, route, extra=None):
        ms = (time.time() - t_all) * 1000
        _log_route(route, voice, len(data), ms)
        out = {"ok": True, "data": data, "voice": voice, "route": route,
               "ms": int(ms), "tried": tried}
        if extra:
            out.update(extra)
        return out


    u = indextts_url()
    try:
        raw, hdr = _post_bytes(u, {"text": text, "ref_audio": ref_audio(),
                                   "lang": "zh", "emo_alpha": 1.0},
                               timeout=timeout)
        if len(raw) > min_bytes:
            return finish(raw, hdr or "indextts-clone", "indextts-direct")
        tried.append({"route": "indextts-direct", "error": "返回过短(%dB)" % len(raw)})
    except Exception as e:
        tried.append({"route": "indextts-direct",
                      "error": "%s: %s" % (type(e).__name__, str(e)[:80])})


    p = prefer()
    if p != "local":
        du = dsh_base() + "/api/fairy/say"
        try:
            raw, hdr = _post_bytes(du, {"text": text}, timeout=timeout)
            if len(raw) > min_bytes:
                return finish(raw, hdr or "?", "dsh-plugin")
            tried.append({"route": "dsh-plugin", "error": "返回过短(%dB)" % len(raw)})
        except Exception as e:
            tried.append({"route": "dsh-plugin",
                          "error": "%s: %s" % (type(e).__name__, str(e)[:80])})


    if p != "dsh":
        lu = local_base() + "/api/fairy/say"
        try:
            raw, hdr = _post_bytes(lu, {"text": text}, timeout=timeout)
            if len(raw) > min_bytes:
                return finish(raw, hdr or "indextts-clone", "local-host")
            tried.append({"route": "local-host", "error": "返回过短(%dB)" % len(raw)})
        except Exception as e:
            tried.append({"route": "local-host",
                          "error": "%s: %s" % (type(e).__name__, str(e)[:80])})


    if p != "local":
        su = dsh_base() + "/dsh-tts-api/speak"
        try:
            req = urllib.request.Request(
                su, data=json.dumps({"text": text}, ensure_ascii=False).encode("utf-8"),
                headers={"content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=min(timeout, 90)) as r:
                j = json.loads(r.read().decode("utf-8"))
            u2 = (j or {}).get("url") if isinstance(j, dict) else None
            if u2:
                full = u2 if str(u2).startswith("http") else (dsh_base() + u2)
                with urllib.request.urlopen(full, timeout=90) as r:
                    raw = r.read()
                if len(raw) > min_bytes:
                    return finish(raw, "edge-tts-fallback", "dsh-tts-api")
            tried.append({"route": "dsh-tts-api", "error": "没有 url 字段或过短"})
        except Exception as e:
            tried.append({"route": "dsh-tts-api",
                          "error": "%s: %s" % (type(e).__name__, str(e)[:80])})

    _log_route("none", "?", 0, (time.time() - t_all) * 1000,
               err="; ".join("%s:%s" % (t["route"], t["error"]) for t in tried))
    return {"ok": False, "data": None, "voice": "?", "route": "none",
            "ms": int((time.time() - t_all) * 1000), "tried": tried}

def tts_audio_and_ext(text, timeout=120):
    r = tts_get(text, timeout=timeout)
    if not (r.get("ok") and r.get("data")):
        return None, None, r.get("route") or "none", r.get("voice") or "?"
    d = r["data"]
    is_mp3 = (d[:3] == b"ID3"
              or (len(d) > 1 and d[0] == 0xFF and (d[1] & 0xE0) == 0xE0))
    return d, (".mp3" if is_mp3 else ".wav"), r.get("route"), r.get("voice")

def route_report():
    b, s = resolve_base()
    return {
        "prefer": prefer(),
        "api_base": b,
        "api_source": s,
        "dsh_base": dsh_base(),
        "local_base": local_base(),
        "indextts_url": indextts_url(),
        "ref_audio": ref_audio(),
        "ref_audio_exists": os.path.exists(ref_audio()),
        "tts_chain": tts_targets(),
        "probe": probe_all(),
        "recent_tts": _TTS_LOG[-10:],
    }

if __name__ == "__main__":
    print(json.dumps(route_report(), ensure_ascii=False, indent=2))
