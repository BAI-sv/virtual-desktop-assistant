#!/usr/bin/env python3
"""fairy_ui.py — 给 Strata 用的中文前端 + 反向代理

【为什么需要它】
  Strata 自带 web UI（端口 8080），但：
    · 界面是英文
    · 没有「直答 / 该想」的档位概念
    · 不显示实际用了什么档、思考预算、耗时
  Strata 的 server.py 只硬编码伺服 index.html / monitor.html 两个文件
  （见 server.py:4525），往它的 web 目录塞新 html 会被 404 ✗
  而且它的 CORS 默认关闭（cors_origins = []），别处的页面调不了它 ✗

  → 所以本工具做两件事：
     ① 在本地端口上伺服我们自己的中文界面
     ② 把 /v1/* 和 /api/* 请求【流式转发】给 Strata
  → 页面与服务同源 → 零 CORS 配置、不动 Strata 一个字节 ✓

【用法】
  py -3.12 fairy_ui.py                  # 默认 8081，自动打开浏览器
  py -3.12 fairy_ui.py --port 9000
  py -3.12 fairy_ui.py --no-open
  py -3.12 fairy_ui.py --upstream http://127.0.0.1:8080

【访问】http://127.0.0.1:8081/
"""

import argparse
import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
UI_FILE = os.path.join(HERE, "strata_ui.html")
DEFAULT_UPSTREAM = "http://127.0.0.1:8080"

# ---------------------------------------------------------------------------
# 语音：/stt 走本地 faster-whisper；/tts 先试 DSH 的 TTS，失败退回 IndexTTS
#
#   ★★ 三个实测过的坑（2026-10-10）：
#     1. whisper 首次加载约 50~60 秒 -> 必须【常驻单例】+ 启动时后台预热
#     2. faster-whisper 的 transcribe() 【不能传文件路径】！
#        传路径会走 PyAV 解码，而本机 PyAV 不认 metadata_errors 参数 ->
#        TypeError: open() got an unexpected keyword argument 'metadata_errors'
#        -> 必须自己解码成 numpy float32 数组再传 ✓
#     3. 浏览器 MediaRecorder 录的是 webm/opus，whisper 要 16kHz 单声道
#        -> 用 imageio-ffmpeg 自带的静态 ffmpeg 解码（管道，不落盘）
# ---------------------------------------------------------------------------
STT_MODEL = None
STT_LOCK = threading.Lock()
WHISPER_MODELS = os.environ.get("WHISPER_MODELS", r"D:\FAIRY\models\whisper")

def _dsh_api():
    """DSH 的基地址。★ 2026-10-17：DSH 0.2.1 起端口随机（不再是 19387），
    这里动态发现；环境变量 DSH_API 仍然优先（排障用）。"""
    _env = os.environ.get("DSH_API")
    if _env:
        return _env.rstrip("/")
    try:
        import sys as _sys
        _t = os.path.dirname(os.path.abspath(__file__))
        if _t not in _sys.path:
            _sys.path.insert(0, _t)
        import dsh_port as _dp
        _p = _dp.find_port()[0]
        if _p:
            return "http://127.0.0.1:%d" % int(_p)
    except Exception:
        pass
    return "http://127.0.0.1:19387"


DSH_API = _dsh_api()
INDEXTTS_URL = os.environ.get("FAIRY_INDEXTTS_URL", "http://127.0.0.1:9881")
REF_AUDIO = os.environ.get("FAIRY_REF_AUDIO", r"D:\FAIRY\voice\index_voices\fairy.wav")

_FFMPEG = None


def ffmpeg_exe():
    """imageio-ffmpeg 自带的静态 ffmpeg；找不到就退回 PATH 里的。"""
    global _FFMPEG
    if _FFMPEG:
        return _FFMPEG
    try:
        import imageio_ffmpeg
        _FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        _FFMPEG = "ffmpeg"
    return _FFMPEG


def decode_audio(data, timeout=120):
    """任意音频字节（webm/opus/wav/mp3/m4a…）-> (numpy float32 16k 单声道, err)

    ★ 走管道，不落临时文件。ffmpeg 从 stdin 读，往 stdout 吐 raw s16le。
    """
    try:
        import numpy as np
    except Exception as e:
        return None, "需要 numpy: %s" % e
    try:
        p = subprocess.run(
            [ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
             "-i", "pipe:0", "-vn",
             "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
            input=data, capture_output=True, timeout=timeout,
            # ★ 2026-10-10【黑窗】ffmpeg 是控制台程序。本进程若由 py.exe /
            #   python.exe 以外的方式起（无控制台），不加这个每解码一次音频
            #   就弹一个终端窗口。
            creationflags=0x08000000)
    except subprocess.TimeoutExpired:
        return None, "解码超时（音频太长？）"
    except Exception as e:
        return None, "ffmpeg 启动失败: %s: %s" % (type(e).__name__, e)
    if p.returncode != 0:
        err = (p.stderr or b"").decode("utf-8", "replace").strip()
        return None, "解码失败: %s" % (err[:200] or ("rc=%d" % p.returncode))
    a = np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    if len(a) == 0:
        return None, "解码后是空的（没听到声音？）"
    return a, None


def _load_whisper():
    """加载 whisper —— 全局单例，用锁保证只加载一次。"""
    global STT_MODEL
    with STT_LOCK:
        if STT_MODEL is None:
            from faster_whisper import WhisperModel
            STT_MODEL = WhisperModel("small", device="cpu", compute_type="int8",
                                     download_root=WHISPER_MODELS)
    return STT_MODEL


def warm_whisper():
    try:
        t0 = time.time()
        _load_whisper()
        print("  [stt] whisper 就绪（%.1fs，已常驻）" % (time.time() - t0), flush=True)
    except Exception as e:
        print("  [stt] whisper 加载失败：%s: %s" % (type(e).__name__, e), flush=True)


def do_stt(data):
    """音频字节 -> 文字。返回 (ok, text_or_errmsg)。"""
    t0 = time.time()
    try:
        m = _load_whisper()          # 首次会阻塞到加载完（约 60 秒）
    except Exception as e:
        return False, "whisper 加载失败: %s" % e
    load_s = time.time() - t0

    a, err = decode_audio(data)
    if err:
        return False, err
    dec_s = time.time() - t0 - load_s

    if len(a) < 1600:
        return False, "音频太短（%.2f 秒）" % (len(a) / 16000.0)
    try:
        t1 = time.time()
        segs, _info = m.transcribe(a, language="zh", beam_size=1, vad_filter=True)
        txt = "".join(s.text for s in segs).strip()
        print("  [stt] %.1fs 秒音频 -> %d 字（加载 %.1fs · 解码 %.1fs · 转写 %.1fs）"
              % (len(a) / 16000.0, len(txt), load_s, dec_s, time.time() - t1), flush=True)
        return True, txt
    except Exception as e:
        return False, "转写失败: %s: %s" % (type(e).__name__, e)


# ---------------- TTS：两条路 ----------------
def tts_via_dsh(text, timeout=120):
    """DSH 的 TTS 插件。POST /dsh-tts-api/speak {"text"} -> {"url"} -> GET 拿 mp3。

    ★ 注意：它返回的是【一个 URL】，不是音频本身 —— 要再 GET 一次（实测）。
    """
    body = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(DSH_API + "/dsh-tts-api/speak", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read().decode("utf-8", "replace"))
    if d.get("error"):
        raise RuntimeError("dsh-tts: %s" % d["error"])
    u = d.get("url")
    if not u:
        raise RuntimeError("dsh-tts 没返回 url: %s" % str(d)[:160])
    if u.startswith("/"):
        u = DSH_API + u
    with urllib.request.urlopen(u, timeout=timeout) as r2:
        data = r2.read()
        ctype = r2.headers.get("Content-Type") or "audio/mpeg"
    if not data:
        raise RuntimeError("dsh-tts 返回空音频")
    return data, ctype


def tts_via_indextts(text, timeout=300):
    """IndexTTS（GPU 克隆，慢）。POST /tts {"text","ref_audio"} -> WAV。"""
    payload = {"text": text}
    if os.path.exists(REF_AUDIO):
        payload["ref_audio"] = REF_AUDIO
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(INDEXTTS_URL + "/tts", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        ctype = r.headers.get("Content-Type") or "audio/wav"
    if not data:
        raise RuntimeError("IndexTTS 返回空音频")
    return data, ctype


# ★ 2026-10-17：DSH 0.2.1 起 /dsh-tts-api/* 需要登录态（外部进程一律 401），
#   这里记一次"DSH 的 TTS 插件用不了"，之后直接走 IndexTTS ——
#   否则每次说话都要先等一次注定失败的请求，日志还刷"DSH 不可用"。
_DSH_TTS_DEAD = {"v": False}


def do_tts(text):
    """文字 -> (bytes, ctype)。先 DSH（快、不占 GPU），失败退回 IndexTTS。"""
    errs = []
    # ① DSH TTS
    if not _DSH_TTS_DEAD["v"]:
        try:
            t0 = time.time()
            data, ctype = tts_via_dsh(text)
            print("  [tts] DSH 路线成功：%.1f KB · %s · %.1fs"
                  % (len(data) / 1024, ctype, time.time() - t0), flush=True)
            return True, (data, ctype)
        except urllib.error.HTTPError as e:
            if e.code in (400, 401, 403, 404, 405):
                _DSH_TTS_DEAD["v"] = True      # 没装/要鉴权：本轮进程内别再试
                print("  [tts] DSH 的 TTS 插件不可用（HTTP %d，要登录态或未启用），"
                      "改用 IndexTTS" % e.code, flush=True)
            errs.append("DSH: HTTPError %d" % e.code)
        except Exception as e:
            errs.append("DSH: %s: %s" % (type(e).__name__, str(e)[:120]))
    # ② IndexTTS
    try:
        t0 = time.time()
        data, ctype = tts_via_indextts(text)
        print("  [tts] IndexTTS 路线成功：%.1f KB · %s · %.1fs"
              % (len(data) / 1024, ctype, time.time() - t0), flush=True)
        return True, (data, ctype)
    except Exception as e:
        errs.append("IndexTTS: %s: %s" % (type(e).__name__, str(e)[:120]))
    return False, "TTS 两条路都不通 -> " + " | ".join(errs)


def tts_available():
    """启动时探一下哪条路通（只报告，不阻塞）。

    ★ 2026-10-17：DSH 0.2.1 起 /dsh-tts-api/* 对外部进程返回 401/404/405，
      这条路注定不通，不该打印成"报错"（体检的日志扫描会当故障）。
      改成中性说明：既然 DSH 插件路线用不了，直接说明将走 IndexTTS。
    """
    for name, fn in (("DSH", lambda: tts_via_dsh("测试")),
                     ("IndexTTS", lambda: tts_via_indextts("测试"))):
        try:
            t0 = time.time()
            d, ct = fn()
            print("  [tts] %s 可用（%.0f KB · %s · %.1fs）" % (name, len(d) / 1024, ct, time.time() - t0),
                  flush=True)
            return
        except urllib.error.HTTPError as e:
            if name == "DSH" and e.code in (400, 401, 403, 404, 405):
                _DSH_TTS_DEAD["v"] = True
                print("  [tts] DSH 的 TTS 插件不可用（HTTP %d，要登录态或未启用），"
                      "语音走 IndexTTS" % e.code, flush=True)
                continue
            print("  [tts] %s 不可用：HTTPError %d" % (name, e.code), flush=True)
        except Exception as e:
            print("  [tts] %s 不可用：%s: %s" % (name, type(e).__name__, str(e)[:100]), flush=True)


# ---------------------------------------------------------------------------
# ★ 让桌面球念：POST /say  ->  转发到 DSH 插件的 /api/fairy/push
#
#   【为什么要这个端点】Strata 界面里的回答【不在 DSH 会话里】，
#     所以球轮询的 GET /api/fairy/reply 取不到它 -> 球念不了 ✗
#     插件那边新加了 push 端点：把文本塞进 REPLY 槽【并推进 seq】，
#     球下一次轮询（约 900ms）就会念出来 ✓
#
#   【为什么要经过本代理】浏览器在 8081，DSH 在 19387 -- 跨源，
#     前端直接 fetch 会被 CORS 挡掉。走同源代理最省事 ✓
#
#   ★ 前端契约（strata_ui.html 就是这么调的）：
#       POST /say {"text":"...","source":"strata"}
#       -> 200 {"ok":true,"seq":N,"text":"...","truncated":bool}
#       -> 503 {"error":"..."}   比如 DSH 还没重启、插件里没有这个路由
# ---------------------------------------------------------------------------
def push_to_ball(text, source="strata", timeout=15):
    """把一段文字推给桌面球去念。返回 (ok, dict 或 错误字符串)。"""
    url = DSH_API.rstrip("/") + "/api/fairy/push"
    payload = json.dumps({"text": text, "source": source},
                         ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return True, json.loads(raw)
            except Exception:
                return True, {"ok": True, "raw": raw[:300]}
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        if e.code in (401, 404):
            # ★ 实测（2026-10-10）：DSH 的 /api/* 命名空间下，
            #   【没注册的路由一律返回 401 unauthorized】（不是 404）。
            #   对照实验：GET/POST /api/fairy/nosuchroute -> 都是 401；
            #             已注册的 /api/fairy/task POST -> 200。
            #   所以这里的 401 含义就是「插件里还没有这个路由」= 需要重启 DSH。
            # ★ 消息要短 —— 前端状态栏只显示前 90 字，关键信息必须放最前面。
            return False, "插件新路由还没加载，需要重启 DSH 才生效（重启会中断当前对话，请自己选时机）"
        return False, "DSH 返回 HTTP %d %s" % (e.code, body)
    except Exception as e:
        return False, "连不上 DSH（%s）：%s" % (url, e)


# 这些前缀的请求转发给 Strata；其余当静态文件
# ★ /metrics 是真正的遥测接口：live 实时速度 · hardware CPU/磁盘/内存 ·
#   engine 专家缓存/PCIe 占比/显存 · history 60 点历史 · totals 累计
PROXY_PREFIXES = ("/v1/", "/api/", "/status", "/health", "/props", "/slots",
                  "/models", "/metrics")
# ★ 这些路径我们自己处理，不走代理（/stt /tts /say 是前端实际调的；带 /api 的也留着兼容）
#   /say = 推给桌面球念（转发到 DSH 插件的 /api/fairy/push）
LOCAL_POST = ("/stt", "/tts", "/say", "/api/stt", "/api/tts", "/api/say")

UPSTREAM = DEFAULT_UPSTREAM


def upstream_parts():
    u = urllib.parse.urlparse(UPSTREAM)
    return u.hostname or "127.0.0.1", u.port or 8080


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FairyUI/1.0"

    def log_message(self, fmt, *args):
        # 静音常规访问日志，只留错误
        pass

    # ---------------- 代理 ----------------
    def _proxy(self, method):
        host, port = upstream_parts()
        body = None
        if method == "POST":
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""

        conn = http.client.HTTPConnection(host, port, timeout=900)
        headers = {}
        for k, v in self.headers.items():
            if k.lower() in ("host", "connection", "accept-encoding"):
                continue
            headers[k] = v
        if body is not None:
            headers["Content-Length"] = str(len(body))

        try:
            conn.request(method, self.path, body=body, headers=headers)
            resp = conn.getresponse()
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            msg = ('{"error":"upstream unreachable: %s"}' % str(e).replace('"', "'")).encode()
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            return

        self.send_response(resp.status)
        # 转发上游的响应头（跳过逐跳头和我们自己要重写的）
        skip = ("connection", "transfer-encoding", "content-length", "keep-alive")
        for k, v in resp.getheaders():
            if k.lower() in skip:
                continue
            self.send_header(k, v)
        # 不用 Content-Length：靠 Connection: close 界定 body，
        # 这样流式（SSE）能一块一块立刻到达，不会被缓冲
        self.send_header("Connection", "close")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        try:
            while True:
                chunk = resp.read(4096)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except Exception:
            pass
        finally:
            conn.close()

    # ---------------- 静态 ----------------
    def _static(self):
        if not os.path.exists(UI_FILE):
            self.send_response(500)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            msg = ("UI file missing: " + UI_FILE).encode("utf-8")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            return
        with open(UI_FILE, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        p = urllib.parse.urlparse(self.path).path
        if p == "/" or p == "/index.html":
            self._static()
        elif any(p.startswith(x) for x in PROXY_PREFIXES):
            self._proxy("GET")
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def _send(self, code, ctype, data):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        # ---- 本地端点：语音转写 / 语音合成（不走代理）----
        if p in LOCAL_POST:
            n = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(n) if n else b""
            if p.endswith("stt"):
                ok, out = do_stt(body)
                if ok:
                    self._send(200, "application/json; charset=utf-8",
                               json.dumps({"text": out}, ensure_ascii=False).encode("utf-8"))
                else:
                    self._send(500, "application/json; charset=utf-8",
                               json.dumps({"error": out}, ensure_ascii=False).encode("utf-8"))
            elif p.endswith("say"):
                # ---- ★ 推给桌面球念（球会用 Fairy 克隆音色念出来）----
                try:
                    o = json.loads(body.decode("utf-8")) or {}
                except Exception:
                    o = {}
                txt = str(o.get("text") or "")
                src = str(o.get("source") or "strata")
                if not txt.strip():
                    self._send(400, "application/json", b'{"error":"empty text"}')
                    return
                ok, res = push_to_ball(txt, src)
                if ok:
                    self._send(200, "application/json; charset=utf-8",
                               json.dumps(res, ensure_ascii=False).encode("utf-8"))
                else:
                    self._send(503, "application/json; charset=utf-8",
                               json.dumps({"error": res}, ensure_ascii=False).encode("utf-8"))
            else:
                try:
                    txt = (json.loads(body.decode("utf-8")) or {}).get("text", "")
                except Exception:
                    txt = ""
                if not txt.strip():
                    self._send(400, "application/json", b'{"error":"empty text"}')
                    return
                ok, res = do_tts(txt)
                if ok:
                    data, ctype = res
                    self._send(200, ctype, data)
                else:
                    self._send(503, "application/json; charset=utf-8",
                               json.dumps({"error": res}, ensure_ascii=False).encode("utf-8"))
            return
        if any(p.startswith(x) for x in PROXY_PREFIXES):
            self._proxy("POST")
        else:
            self._send(404, "text/plain", b"not found")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()


def wait_upstream(host, port, secs=40):
    """等上游起来（Strata 可能正在加载模型）。"""
    t0 = time.time()
    while time.time() - t0 < secs:
        s = socket.socket()
        s.settimeout(1)
        try:
            if s.connect_ex((host, port)) == 0:
                s.close()
                return True
        except Exception:
            pass
        finally:
            try:
                s.close()
            except Exception:
                pass
        time.sleep(1)
    return False


def main():
    global UPSTREAM
    ap = argparse.ArgumentParser(description="Strata 中文前端 + 反向代理")
    ap.add_argument("--port", type=int, default=8081, help="本地端口（默认 8081）")
    ap.add_argument("--upstream", default=DEFAULT_UPSTREAM, help="Strata 地址（默认 %s）" % DEFAULT_UPSTREAM)
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()

    UPSTREAM = args.upstream.rstrip("/")
    host, port = upstream_parts()

    print("=" * 62)
    print("  Strata 中文前端（Fairy UI）")
    print("=" * 62)
    print("  界面文件 : %s" % UI_FILE)
    if os.path.exists(UI_FILE):
        print("              %.1f KB" % (os.path.getsize(UI_FILE) / 1024))
    else:
        print("              ★ 不存在！请先把 strata_ui.html 放这里")
    print("  上游     : %s" % UPSTREAM)
    print("  本地地址 : http://127.0.0.1:%d/" % args.port)
    print("")

    ok = wait_upstream(host, port, secs=5)
    print("  上游状态 : %s" % ("在线 ✓" if ok else "连不上 ✗（界面会显示错误，但代理照样启动）"))
    print("")
    print("  按 Ctrl+C 停止")
    print("=" * 62)

    # ★ whisper 首次加载约 60 秒，后台预热（不阻塞服务启动）
    print("  [stt] 后台预热 whisper…（首次约 60 秒，期间语音输入按钮会慢）", flush=True)
    threading.Thread(target=warm_whisper, daemon=True, name="whisper-warm").start()
    # ★ 顺便探一下 TTS 哪条路通（DSH 优先，退回 IndexTTS）
    threading.Thread(target=tts_available, daemon=True, name="tts-probe").start()

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    srv.daemon_threads = True

    if not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open("http://127.0.0.1:%d/" % args.port)).start()

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止")
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
