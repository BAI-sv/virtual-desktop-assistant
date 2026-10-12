# -*- coding: utf-8 -*-
r"""
fairy_endpoints.py —— Fairy 的【接口寻址与自动降级】中心

【为什么需要这个文件】
  以前球的地址是写死的：
      SAY_URL   = http://127.0.0.1:19387/api/fairy/say      <- DSH 插件
      REPLY_URL = http://127.0.0.1:19387/api/fairy/reply
      META_URL  = http://127.0.0.1:19387/api/fairy/meta
  只要 DSH 不开，球就【哑了 + 瞎了】（不知道状态、念不出话）。
  【关键事实·2026-10-10 实测】DSH 那个 /api/fairy/say 只是个【转发壳】：
      它自己也是 POST http://127.0.0.1:9881/tts {text, ref_audio, lang, emo_alpha}
      参考音频就是 D:\\FAIRY\\voice\\index_voices\\fairy.wav
  -> 所以【直连 9881】和【走 DSH】产出的是同一个音色（log-mel 余弦 0.9998）。
     既然 IndexTTS 就在本机，中间那个 DSH 壳对"说话"来说就是多余的。

【本文件提供什么】
  · tts_urls()           发音地址候选链（有序）
  · api_base()           状态/文本接口的 base（DSH 优先，自动切本地）
  · tts_get()            ★ 主入口：给一句话，拿回音频字节 + 音色标签 + 走的哪条路
  · probe() / probe_all() 探活（带短 TTL 缓存，不刷屏）

【降级链】（tts_get 内部顺序）
  1) 直连 IndexTTS 9881 /tts        -> 音色 indextts-clone ✓（DSH 不在也能出声）
  2) DSH 插件 /api/fairy/say        -> 音色 indextts-clone（DSH 在时的老路径，行为不变）
  3) 本地 fairy_host 19388 /api/fairy/say -> 音色 indextts-clone
  4) DSH 的 /dsh-tts-api/speak      -> 音色 edge-tts-fallback ✗（通用音色，最后手段）
  5) 全都失败                        -> 返回 None，调用方自己决定（球会退回游戏原声）

【配置】优先级：环境变量 > <项目根>/fairy.json 的 "endpoints" 段 > 默认值
  {
    "endpoints": {
      "dsh_base":     "http://127.0.0.1:19387",
      "local_base":   "http://127.0.0.1:19388",
      "indextts_url": "http://127.0.0.1:9881/tts",
      "ref_audio":    "<配置根>\\voice\\index_voices\\fairy.wav",
      "prefer":       "auto"
    }
  }
  prefer: auto（默认，DSH 在就 DSH）/ local（强制本地）/ dsh（强制 DSH）
  ★ 排障用：把 dsh_base 指到一个不通的端口，球就会自动切本地 —— 不用真去关 DSH。

【纪律】本文件不 import 球/耳朵，只被它们 import，避免循环依赖。
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request

# ---- 配置根（拿 logs 目录 / fairy.json）。缺了也不该让调用方崩 ----
try:
    import fairy_root as _root
    _LOGS = _root.LOGS
except Exception:                                    # pragma: no cover
    _root = None
    _LOGS = None

_HERE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- 默认值
DEFAULTS = {
    "dsh_base": "http://127.0.0.1:19387",
    "local_base": "http://127.0.0.1:19388",
    "indextts_url": "http://127.0.0.1:9881/tts",
    "ref_audio": os.path.join(
        (_root.VOICE if _root else os.path.join(os.path.dirname(_HERE), "voice")),
         "index_voices", "fairy.wav"),
    # ★ 2026-10-11【DSH 解耦】默认 local（单机模式）：不再强关联 DSH。
    #   要用 DSH 内核时：球右键 → 大脑模式 → DSH 内核（此时才连接/侦测 DSH）。
    "prefer": "local",
}

# 环境变量名（★ 排障口：临时改地址不用动代码）
ENV = {
    "dsh_base": "FAIRY_DSH_BASE",
    "local_base": "FAIRY_LOCAL_BASE",
    "indextts_url": "FAIRY_INDEXTTS_URL",
    "ref_audio": "FAIRY_REF_AUDIO",
    "prefer": "FAIRY_ENDPOINT_PREFER",
}


def _cfg():
    """读 fairy.json 的 endpoints 段（读不到就返回空 dict）。"""
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


# ---------------------------------------------------------------- 对外配置读取
def dsh_base():
    return _get("dsh_base").rstrip("/")


def local_base():
    return _get("local_base").rstrip("/")


def indextts_url():
    return _get("indextts_url")


def ref_audio():
    return _get("ref_audio")


def prefer():
    """auto / local / dsh —— 热读 fairy.json（不缓存，切换后即时生效）。"""
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


# ---------------------------------------------------------------- 探活（带缓存）
_LOCK = threading.Lock()
_PROBE = {}                    # url -> (ts, bool)
PROBE_TTL = 20.0               # 秒。★ 别太大：DSH 中途起来要能自动接回去


def probe(url, timeout=2.5, ttl=None):
    """True = 这个地址现在通。带 TTL 缓存，避免球每 900ms 轮询一次就打一次。"""
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
        # ★ 405/404 也算「通」—— 端口活着就说明服务在
        ok = e.code in (404, 405, 400)
    except Exception:
        ok = False
    with _LOCK:
        # ★★ 重要-8 同源问题：时间戳必须取在【探测之后】。
        #   取在探测前的话，一次 2.5s 超时的探测会把缓存写成"已经过期 2.5s"，
        #   下次调用立刻再探一遍 —— 缓存形同虚设。
        _PROBE[url] = (time.time(), ok)
    return ok


def invalidate(url=None):
    """把探活缓存清掉（换地址 / 排障后强制重探）。"""
    with _LOCK:
        if url is None:
            _PROBE.clear()
        else:
            _PROBE.pop(url, None)


def probe_all():
    """给 status / health_check 用：一次看清谁在。"""
    out = {}
    for name, u in (("dsh", dsh_base() + "/api/fairy/meta"),
                    ("local_host", local_base() + "/api/fairy/meta"),
                    ("indextts", _indextts_health())):
        if u:
            out[name] = {"url": u, "up": probe(u, ttl=0)}
    return out


def _indextts_health():
    """从 tts 地址推出 /health（9881/tts -> 9881/health）。"""
    u = indextts_url()
    i = u.find("/", len("http://"))
    if i < 0:
        return None
    return u[:i] + "/health"


# ---------------------------------------------------------------- base 选择
#  ★ 顺序（本机实测）：DSH 在就 DSH，DSH 不在就本地。
#    本地 19388 由 fairy_host.py 提供，契约与 DSH 插件一致。
_BASE_CACHE = {"t": 0.0, "base": None, "src": None}
BASE_TTL = 5.0          # ★ 别太长：DSH 中途开关要能自动接回去/切走


def resolve_base(force=False):
    """
    状态/文本接口该用哪个 base。返回 (base_url, source)，source ∈ {'dsh','local'}
      prefer=dsh   -> 永远 DSH
      prefer=local -> 永远本地
      prefer=auto  -> 探 DSH 通不通；通就用 DSH（行为与以前完全一致），不通切本地
    """
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

    # ★★ 重要-7：这里以前是 guess = d + for/else —— else 只改了 src 没改 guess，
    #   于是「两个都不通」时会返回【死掉的 DSH】而 src 写着 local（和注释正好相反），
    #   球拿着死地址去轮询，超时 4s -> 变暗、状态全丢。
    #   ★★ 重要-8：时间戳以前在【探测前】取、探测后才写缓存 —— 探两个黑洞端口要花 5s，
    #   而 BASE_TTL 也是 5s，写进去就已经过期，缓存等于没有（实测三次各花 5.00s）。
    #   现在：时间戳在探测【之后】取 + 给探活一个 1.5s 的总预算。
    t0 = time.time()
    budget = 1.5

    base, src = l, "local"          # ★ 悲观默认：两个都不通就用【本地】（自己的，更有希望回来）
    for cand, s in ((d, "dsh"), (l, "local")):
        left = budget - (time.time() - t0)
        if left <= 0.05:
            break                    # 预算用完 —— 不再等第二个，直接用默认的本地
        if probe(cand + "/api/fairy/meta", ttl=BASE_TTL, timeout=min(1.2, left)):
            base, src = cand, s
            break
    with _LOCK:
        _BASE_CACHE.update({"t": time.time(), "base": base, "src": src})
    return base, src


# 兼容旧调用名
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
    """
    ★ 给球在【启动时打印/排障】看的：语音候选链（有序）。
    tts_get() 就是按这个顺序依次尝试的。
    """
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
    """给日志/界面看：现在到底走的哪条。"""
    b, s = resolve_base()
    return {"base": b, "source": s, "prefer": prefer(),
            "dsh": dsh_base(), "local": local_base()}


# ---------------------------------------------------------------- ★ 语音主入口
_TTS_LOG = []                  # 最近几次路由，供 status 展示


def _log_route(route, voice, nbytes, ms, err=None):
    rec = {"t": time.strftime("%H:%M:%S"), "route": route, "voice": voice,
           "bytes": nbytes, "ms": int(ms)}
    if err:
        rec["err"] = str(err)[:120]
    _TTS_LOG.append(rec)
    del _TTS_LOG[:-40]
    # ★ 路由变化才写盘：球可能每几秒说一句，全写会把日志刷爆
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
    """
    发一个 POST，返回 (raw_bytes, voice_label)。
    voice_label 取 x-fairy-voice 头；没有就按 url 猜。
    """
    req = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        v = r.headers.get("x-fairy-voice")
    return raw, v


def tts_get(text, timeout=120, min_bytes=500):
    """
    ★★ 球/耳朵 取语音的唯一入口。

    返回 dict：
      {
        "ok":       True/False,
        "data":     bytes 或 None,     # 音频字节（wav 或 mp3）
        "voice":    "indextts-clone" / "edge-tts-fallback" / "?",
        "route":    "indextts-direct" / "dsh-plugin" / "local-host" / "dsh-tts-api",
        "ms":       耗时毫秒,
        "tried":    [ {route, error}, ... ]   # 失败的那些，便于排障
      }
    ★ 失败不抛异常 —— 调用方（球）自己决定要不要退回游戏原声。
    """
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

    # ---- 1) ★ 直连 IndexTTS（DSH 不在也能出声）----
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

    # ---- 2) DSH 插件（DSH 在时与改造前行为一致）----
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

    # ---- 3) 本地 fairy_host ----
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

    # ---- 4) DSH 的 tts-api（Edge 通用音色，最后手段）----
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


# ---------------------------------------------------------------- ★ AI 对话降级中心
def ask_get(text, history=None, timeout=120):
    """
    ★ 本地模型失败后的在线 AI 对话降级中心（ear / ball 共用）：
       DSH（OpenAI 兼容 chat）-> 豆包（火山方舟，需配置 api_key）。
    返回 (answer, source) 或 None（全失败）。
    """
    text = (text or "").strip()
    if not text:
        return None
    msgs = [{"role": "user", "content": text}]
    if history:
        for _u, _a in list(history)[-8:]:
            if _u:
                msgs.insert(len(msgs) - 1, {"role": "user", "content": _u})
            if _a:
                msgs.insert(len(msgs) - 1, {"role": "assistant", "content": _a})

    def _chat(url, payload, headers, to=timeout):
        req = urllib.request.Request(
            url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers)
        with urllib.request.urlopen(req, timeout=to) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    # ---- 1) DSH（DeepSeek Harness：POST push 拿同步回复；先探活）----
    try:
        if probe(dsh_base() + "/api/fairy/meta", timeout=2.0, ttl=0):
            _req = urllib.request.Request(
                dsh_base() + "/api/fairy/push",
                data=json.dumps({"text": text, "source": "fairy-ear"},
                                ensure_ascii=False).encode("utf-8"),
                headers={"content-type": "application/json"})
            with urllib.request.urlopen(_req, timeout=min(timeout, 90)) as _r:
                _jd = json.loads(_r.read().decode("utf-8"))
            if isinstance(_jd, dict) and _jd.get("ok"):
                _t = str(_jd.get("text") or "").strip()
                if _t:
                    return _t, "dsh"
    except Exception:
        pass

    # ---- 2) 豆包（火山方舟；fairy.json 配了 api_key 才启用）----
    try:
        seg = _cfg().get("online") or {}
        dou = (seg.get("doubao") or {}) if isinstance(seg, dict) else {}
        key = (dou.get("api_key") or "").strip() if isinstance(dou, dict) else ""
        if key:
            bu = (dou.get("base_url") or
                  "https://ark.cn-beijing.volces.com/api/v3/chat/completions")
            mo = dou.get("model") or "doubao-seed-1-6-250615"
            d2 = _chat(bu, {"model": mo, "messages": msgs, "stream": False},
                       {"content-type": "application/json",
                        "authorization": "Bearer " + key}, to=min(timeout, 60))
            a2 = (d2.get("choices") or [{}])[0].get("message", {}).get("content") or ""
            if a2.strip():
                return a2.strip(), "doubao"
    except Exception:
        pass
    return None


def tts_audio_and_ext(text, timeout=120):
    """
    ★ 给会自行播放音频的组件用（call_translate / work_advisor 等）：
      它们不需要知道降级链，只要拿到【音频字节 + 该用什么扩展名】。

    返回 (data, ext, route, voice) —— 全失败返回 (None, None, "none", "?")。
    ★ 为什么要 ext：以前这些组件把文件写死成 .mp3，
      但 IndexTTS 出的是 wav —— 写错扩展名会让 MCI 播不出来。
    """
    r = tts_get(text, timeout=timeout)
    if not (r.get("ok") and r.get("data")):
        return None, None, r.get("route") or "none", r.get("voice") or "?"
    d = r["data"]
    is_mp3 = (d[:3] == b"ID3"
              or (len(d) > 1 and d[0] == 0xFF and (d[1] & 0xE0) == 0xE0))
    return d, (".mp3" if is_mp3 else ".wav"), r.get("route"), r.get("voice")


def route_report():
    """给 status 命令用。"""
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
