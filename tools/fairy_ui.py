

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


STT_MODEL = None
STT_LOCK = threading.Lock()
WHISPER_MODELS = os.environ.get("WHISPER_MODELS", r"<FAIRY_ROOT>\models\whisper")

DSH_API = os.environ.get("DSH_API", "http://127.0.0.1:19387")
INDEXTTS_URL = os.environ.get("FAIRY_INDEXTTS_URL", "http://127.0.0.1:9881")
REF_AUDIO = os.environ.get("FAIRY_REF_AUDIO", r"<FAIRY_ROOT>\voice\index_voices\fairy.wav")

_FFMPEG = None

def ffmpeg_exe():
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
    t0 = time.time()
    try:
        m = _load_whisper()
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


def tts_via_dsh(text, timeout=120):
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

def do_tts(text):
    errs = []

    try:
        t0 = time.time()
        data, ctype = tts_via_dsh(text)
        print("  [tts] DSH 路线成功：%.1f KB · %s · %.1fs"
              % (len(data) / 1024, ctype, time.time() - t0), flush=True)
        return True, (data, ctype)
    except Exception as e:
        errs.append("DSH: %s: %s" % (type(e).__name__, str(e)[:120]))

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
    for name, fn in (("DSH", lambda: tts_via_dsh("测试")),
                     ("IndexTTS", lambda: tts_via_indextts("测试"))):
        try:
            t0 = time.time()
            d, ct = fn()
            print("  [tts] %s 可用（%.0f KB · %s · %.1fs）" % (name, len(d) / 1024, ct, time.time() - t0),
                  flush=True)
            return
        except Exception as e:
            print("  [tts] %s 不可用：%s: %s" % (name, type(e).__name__, str(e)[:100]), flush=True)


def push_to_ball(text, source="strata", timeout=15):
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


            return False, "插件新路由还没加载，需要重启 DSH 才生效（重启会中断当前对话，请自己选时机）"
        return False, "DSH 返回 HTTP %d %s" % (e.code, body)
    except Exception as e:
        return False, "连不上 DSH（%s）：%s" % (url, e)


PROXY_PREFIXES = ("/v1/", "/api/", "/status", "/health", "/props", "/slots",
                  "/models", "/metrics")


LOCAL_POST = ("/stt", "/tts", "/say", "/api/stt", "/api/tts", "/api/say")

UPSTREAM = DEFAULT_UPSTREAM

def upstream_parts():
    u = urllib.parse.urlparse(UPSTREAM)
    return u.hostname or "127.0.0.1", u.port or 8080

class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "FairyUI/1.0"

    def log_message(self, fmt, *args):

        pass


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

        skip = ("connection", "transfer-encoding", "content-length", "keep-alive")
        for k, v in resp.getheaders():
            if k.lower() in skip:
                continue
            self.send_header(k, v)


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


    print("  [stt] 后台预热 whisper…（首次约 60 秒，期间语音输入按钮会慢）", flush=True)
    threading.Thread(target=warm_whisper, daemon=True, name="whisper-warm").start()

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
