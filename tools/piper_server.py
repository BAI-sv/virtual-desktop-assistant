# -*- coding: utf-8 -*-
"""
piper_server.py —— Fairy 的快速语音服务（Piper ONNX，常驻）

★ 为什么做这个（2026-10-20 实测数据）
    Piper 常驻后     16.3x 实时   2.4 秒音频 → 0.146 秒
    IndexTTS         1.15x 实时   2 秒音频   → 1.7 秒
    Qwen3-TTS         0.13x 实时   2 秒音频   → 17 秒

    Fairy 日常对话 90% 是短句（「好的」「收到」「已经打开了」），
    用 Piper 能真正做到秒回。长句仍走 IndexTTS 保克隆音色。

★ 为什么不用 torch
    Piper 走 ONNX，绕开 ROCm。这台机器是 AMD RX 9070 XT +
    Windows，torch/ROCm 那一路坑极多（内核缺失、算子不兼容），
    ONNX 路线完全没这些问题 —— 这就是 16 倍与 0.13 倍的差距来源。

★ 设计取舍
    - 常驻：首次加载 7.6 秒（含 g2pW 155MB 音素化器），之后 0.15 秒/句
    - 单线程 HTTP：Piper 本身 0.15 秒，不需要并发
    - 返回完整 wav（不真流式）：0.15 秒已经比人耳感知阈值低，
      再做 chunked 反而增加播放端复杂度

用法：
    python piper_server.py --port 9882
    curl -X POST http://127.0.0.1:9882/tts -d '{"text":"好的"}'
"""
import argparse
import io
import json
import os
import sys
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ---------------------------------------------------------------- 默认路径
_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MODEL = r"D:\PiperTTS\voices\zh_CN-xiao_ya-medium.onnx"
_REF = os.path.join(_HERE, "..", "fairy.json")


def _load_cfg():
    """从 fairy.json 读配置；读不到就用默认值。"""
    cfg = {}
    try:
        with open(_REF, encoding="utf-8") as f:
            j = json.load(f)
        cfg = j.get("voice", {}).get("piper", {}) or {}
    except Exception:
        pass
    return {
        "model": cfg.get("model") or DEFAULT_MODEL,
        "port": int(cfg.get("port") or 9882),
        "host": cfg.get("host") or "127.0.0.1",
    }


# ---------------------------------------------------------------- 全局状态
_VOICE = None
_LOAD_MS = 0
_STATS = {"count": 0, "total_ms": 0, "last_ms": 0}


def load_voice(model_path):
    """加载 Piper 模型。失败直接抛 —— 服务起不来比悄悄降级好。"""
    global _VOICE, _LOAD_MS
    t0 = time.time()
    # ★ 镜像：g2pW/bert 分词器都从 HF 拉，国内直连必超时
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    from piper import PiperVoice
    _VOICE = PiperVoice.load(model_path)
    _LOAD_MS = int((time.time() - t0) * 1000)
    return _LOAD_MS


def synth(text):
    """
    合成一句话，返回 wav 字节。

    ★ Piper 的 synthesize() 是生成器，按句返回多个 AudioChunk。
      这里拼成一个完整 wav —— 0.15 秒的合成时间不值得做流式。
    """
    import numpy as np

    chunks = list(_VOICE.synthesize(text))
    if not chunks:
        return b"", 0

    sr = chunks[0].sample_rate
    pcm = np.concatenate([np.frombuffer(c.audio_int16_bytes, dtype=np.int16) for c in chunks])

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)          # int16
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue(), len(pcm) / float(sr)


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # 静音默认日志（每句都打会把 fairy 日志刷爆）
        pass

    def _json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/health"):
            self._json(200, {
                "ok": _VOICE is not None,
                "engine": "piper",
                "voice": "xiao_ya",
                "load_ms": _LOAD_MS,
                "stats": dict(_STATS),
            })
            return
        self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if not self.path.startswith("/tts"):
            self._json(404, {"ok": False, "error": "not found"})
            return
        try:
            n = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(n) if n > 0 else b"{}"
            j = json.loads(raw.decode("utf-8") or "{}")
            text = (j.get("text") or "").strip()
            if not text:
                self._json(400, {"ok": False, "error": "text empty"})
                return

            t0 = time.time()
            wav, dur = synth(text)
            ms = (time.time() - t0) * 1000
            if not wav:
                self._json(500, {"ok": False, "error": "synthesize returned nothing"})
                return

            _STATS["count"] += 1
            _STATS["total_ms"] += int(ms)
            _STATS["last_ms"] = int(ms)

            self.send_response(200)
            self.send_header("content-type", "audio/wav")
            self.send_header("content-length", str(len(wav)))
            self.send_header("x-fairy-voice", "piper-xiao_ya")
            self.send_header("x-fairy-ms", str(int(ms)))
            self.send_header("x-fairy-duration", "%.2f" % dur)
            self.end_headers()
            self.wfile.write(wav)
        except Exception as e:
            # ★ 错误必须打出来。静默返回 500 会让排障变成猜谜。
            import traceback
            print("[piper_server] /tts 失败: %s: %s" % (type(e).__name__, str(e)[:300]), flush=True)
            traceback.print_exc(file=sys.stdout)
            sys.stdout.flush()
            try:
                self._json(500, {"ok": False,
                                 "error": "%s: %s" % (type(e).__name__, str(e)[:200])})
            except Exception:
                pass


def main():
    cfg = _load_cfg()
    ap = argparse.ArgumentParser(description="Fairy 的 Piper 语音服务")
    ap.add_argument("--host", default=cfg["host"])
    ap.add_argument("--port", type=int, default=cfg["port"])
    ap.add_argument("--model", default=cfg["model"])
    a = ap.parse_args()

    if not os.path.exists(a.model):
        print("[piper_server] 模型不存在: %s" % a.model, file=sys.stderr)
        return 2

    print("[piper_server] 加载模型 %s ..." % a.model, flush=True)
    ms = load_voice(a.model)
    print("[piper_server] [OK] 加载完成 %d ms" % ms, flush=True)

    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    srv.daemon_threads = True
    print("[piper_server] 监听 http://%s:%d  (POST /tts  GET /health)" % (a.host, a.port), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[piper_server] 收到 Ctrl+C，退出", flush=True)
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
