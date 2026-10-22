# -*- coding: utf-8 -*-
"""
ai_center.py —— 智能眼镜 AI 中枢（HTTP 服务，零 Flask 依赖）

【它是什么】
  眼镜（ESP32-S3）通过 WiFi/4G 连到这台中枢，所有 AI 能力都在这：
      /asr       语音识别（本机 SenseVoice/Paraformer，免费离线）
      /chat      对话（兜底链：DSH -> 智谱 glm-4.5-flash -> 豆包 turbo）
      /translate 拍照/文字翻译（百度翻译，免费）
      /tts       文字转语音（edge-tts，微软免费）
      /vqa       图片问答（豆包视觉；mode=look|shop|tr 三种意图）
      /nav       高德开车导航（实测通）
      /device/register + /devices   设备注册/心跳/发现（FairyX 靠它发现眼镜）

【运行】
  py -3.12 ai_center.py [端口]    默认 0.0.0.0:5000
  （本机 Windows 上跑；眼镜和 FairyX 都通过 HTTP 找它）

【与 FairyX 的关系】
  D:/FAIRY/tools/glasses_adapter.py 调 GET /devices 发现【在线】眼镜，
  离线一律不显示不播报（强扩展铁律）。
"""
import json
import os
import sys
import time
import base64
import threading
import subprocess
import tempfile
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

# ★ 黑窗：父进程没控制台时不弹终端
CREATE_NO_WINDOW = 0x08000000

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                      # D:\FAIRY
DATA_DIR = os.path.join(ROOT, "data")
DEVICES_FILE = os.path.join(DATA_DIR, "glasses_devices.json")
TOOLS = os.path.join(ROOT, "tools")

sys.path.insert(0, HERE)
import keys as K   # noqa: E402

import requests    # noqa: E402（py -3.12 已装）

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
HEARTBEAT_TTL = 60                                # 超过 60 秒没心跳 = 离线

_devices = {}
_dev_lock = threading.Lock()


# ---------------------------------------------------------------- 设备表
def _load_devices():
    global _devices
    try:
        if os.path.exists(DEVICES_FILE):
            with open(DEVICES_FILE, encoding="utf-8") as f:
                _devices = json.load(f)
    except Exception:
        _devices = {}


def _save_devices():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(DEVICES_FILE, "w", encoding="utf-8") as f:
            json.dump(_devices, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def _refresh_online():
    now = time.time()
    for d in _devices.values():
        d["online"] = (now - d.get("last_seen", 0)) <= HEARTBEAT_TTL


# ---------------------------------------------------------------- 设备类型表
# ★★ 2026-10-23【通用化】不只为眼镜服务了
#   任何外接设备走同一套：POST /device/register + 每 30 秒心跳。
#   type 决定默认显示名和默认能力；不认识的 type 也照样入表（归 other），
#   这样将来接机器人、汽车、手表、音箱，中枢这边零改动。
TYPE_DEFAULT_NAME = {
    "glasses": "智能眼镜",
    "robot": "机器人",
    "car": "汽车",
    "watch": "手表",
    "speaker": "音箱",
}

# 设备不主动报 actions 时，按 type 给一套默认能力
TYPE_DEFAULT_ACTIONS = {
    "glasses": ["devices", "status"],
    "robot": ["devices", "status"],
    "car": ["devices", "status"],
}

KNOWN_TYPES = set(TYPE_DEFAULT_NAME.keys())


def _norm_type(t):
    """type 归一：小写去空格；不认识的保留但算 other。空值兼容老固件当眼镜。"""
    t = str(t or "").strip().lower()
    if not t:
        return "glasses"
    return t if t in KNOWN_TYPES else "other"


def _register_device(payload):
    did = str(payload.get("device_id") or "").strip()
    if not did:
        return {"ok": False, "error": "缺 device_id"}
    t = _norm_type(payload.get("type"))
    with _dev_lock:
        now = time.time()
        d = _devices.get(did, {})
        d.update({
            "device_id": did,
            "type": t,
            # 名字优先级：设备自报 > 按 type 给默认 > 兜底
            "name": (str(payload.get("name") or "").strip()
                     or TYPE_DEFAULT_NAME.get(t)
                     or "外接设备"),
            "ip": payload.get("ip") or "",
            "info": payload.get("info") or "",
            # 能力：设备自报优先，否则按 type 给默认
            "actions": payload.get("actions")
                       or TYPE_DEFAULT_ACTIONS.get(t, ["devices", "status"]),
            "last_seen": now,
        })
        _devices[did] = d
        _save_devices()
        _refresh_online()
        return {"ok": True, "device": d}


# ---------------------------------------------------------------- 外部调用
def _post_json(url, payload, timeout=30):
    r = requests.post(url, json=payload, timeout=timeout)
    return r.json()


def _chat_ark(messages, model=None, timeout=40):
    """火山方舟豆包。"""
    payload = {
        "model": model or K.DEFAULT_MODEL,
        "messages": messages,
        "temperature": 0.6,
    }
    r = requests.post(K.ARK_BASE + "/chat/completions",
                      json=payload, timeout=timeout,
                      headers={"Authorization": "Bearer " + K.ARK_KEY})
    j = r.json()
    return j["choices"][0]["message"]["content"]


def _chat_zhipu(messages, timeout=15):
    """智谱 glm-4.5-flash（免费实测通）。15 秒没回就降级，别拖慢兜底链。"""
    payload = {"model": K.ZP_MODEL, "messages": messages}
    r = requests.post(K.ZP_BASE, json=payload, timeout=timeout,
                      headers={"Authorization": "Bearer " + K.ZP_KEY})
    j = r.json()
    return j["choices"][0]["message"]["content"]


def _chat_dsh(messages, timeout=4):
    """本机 DSH（DeepSeek Harness，OpenAI 兼容口）。本地服务 4 秒没回就当不在。"""
    r = requests.post(K.DSH_BASE + "/v1/chat/completions",
                      json={"model": "fairy-local", "messages": messages},
                      timeout=timeout)
    return r.json()["choices"][0]["message"]["content"]


def _chat(messages):
    """兜底链：DSH -> 智谱 -> 豆包。逐级降级，绝不崩。"""
    chain = []
    try:
        return _chat_dsh(messages), "dsh"
    except Exception as e:
        chain.append("dsh:%s" % str(e)[:40])
    try:
        return _chat_zhipu(messages), "zhipu"
    except Exception as e:
        chain.append("zhipu:%s" % str(e)[:40])
    try:
        return _chat_ark(messages), "ark:" + K.DEFAULT_MODEL
    except Exception as e:
        chain.append("ark:%s" % str(e)[:40])
    return None, "全部失败: " + "; ".join(chain)


def _baidu_translate(text, to="zh"):
    """百度翻译（免费标准版）。"""
    salt = str(int(time.time() * 1000))
    import hashlib
    sign = hashlib.md5((K.BD_APPID + text + salt + K.BD_KEY).encode()).hexdigest()
    r = requests.post(K.BD_URL, data={
        "q": text, "from": "auto", "to": to,
        "appid": K.BD_APPID, "salt": salt, "sign": sign,
    }, timeout=15)
    j = r.json()
    if "trans_result" not in j:
        return None, str(j)
    return "".join(x["dst"] for x in j["trans_result"]), None


def _tts(text, out_path):
    """edge-tts 合成中文语音（微软免费）。"""
    import asyncio
    import edge_tts
    async def run():
        c = edge_tts.Communicate(text, "zh-CN-XiaoxiaoNeural", rate="+0%")
        await c.save(out_path)
    asyncio.run(run())


def _geocode(name):
    r = requests.get("https://restapi.amap.com/v3/geocode/geo",
                     params={"key": K.AMAP_KEY, "address": name}, timeout=15)
    j = r.json()
    gc = (j.get("geocodes") or [{}])[0]
    return gc.get("location") or ""


def _nav(origin, destination):
    """高德驾车导航：返回可读步骤。"""
    org = origin or K.PUYANG
    params = {"key": K.AMAP_KEY, "origin": org, "destination": destination,
              "extensions": "all"}
    # 地名 -> 经纬度
    for k, v in (("origin", org), ("destination", destination)):
        if "," not in v:
            loc = _geocode(v)
            if loc:
                params[k] = loc
    r = requests.get(K.AMAP_DIRECTION, params=params, timeout=20)
    j = r.json()
    if j.get("status") != "1":
        return None, "高德返回: " + str(j.get("info"))
    route = (j.get("route") or {}).get("paths") or []
    if not route:
        return None, "没有路线"
    p = route[0]
    steps = []
    for s in p.get("steps") or []:
        for x in s.get("cities") or []:
            if x.get("instruction"):
                steps.append(x["instruction"])
        if s.get("instruction"):
            steps.append(s["instruction"])
    return {
        "distance_km": round(float(p.get("distance", 0)) / 1000, 1),
        "duration_min": round(float(p.get("duration", 0)) / 60),
        "steps": steps,
    }, None


def _vqa(image_bytes, mode="look"):
    """豆包视觉问答。mode 决定意图：look=看到什么 / shop=购物点评 / tr=拍照翻译。"""
    prompt = {
        "look": "请描述你在这张照片里看到的内容，用中文，简洁清晰，像助手一样告诉我重点。",
        "shop": "这是购物场景。请告诉我这是什么商品、大概价格档位、值不值得买，用中文简洁点评。",
        "tr": "把这张图片里的文字识别并翻译成中文，逐条列出。",
    }.get(mode, "请描述图片内容，用中文。")
    b64 = base64.b64encode(image_bytes).decode()
    messages = [{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
            {"type": "text", "text": prompt},
        ],
    }]
    try:
        return _chat_ark(messages), None
    except Exception as e:
        # 兜底：本机 Qwen3-VL（8085）
        try:
            payload = {"model": "qwen3-vl-8b",
                       "messages": [{"role": "user", "content": [
                           {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
                           {"type": "text", "text": prompt}]}]}
            r = requests.post(K.VISION_LOCAL, json=payload, timeout=60)
            return r.json()["choices"][0]["message"]["content"], None
        except Exception as e2:
            return None, "豆包视觉失败: %s；本机视觉失败: %s" % (str(e)[:60], str(e2)[:60])


def _asr(wav_bytes):
    """本机语音识别：写临时 wav -> asr_bridge（SenseVoice 优先）。"""
    fd, tmp = tempfile.mkstemp(suffix=".wav", dir=DATA_DIR)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(wav_bytes)
        cmd = [sys.executable, os.path.join(TOOLS, "asr_bridge.py"),
               "--file", tmp, "--backend", "sensevoice", "--quiet"]
        r = subprocess.run(cmd, capture_output=True, timeout=120,
                           creationflags=CREATE_NO_WINDOW)
        out = r.stdout.decode("utf-8", "replace").strip()
        try:
            j = json.loads(out.splitlines()[-1])
            if j.get("ok"):
                return j.get("text", ""), None
            return None, "asr_bridge: " + str(j.get("error") or j)
        except Exception:
            return None, "asr 无有效输出: " + out[-200:]
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "glasses-ai-center/3.3"

    def _send(self, code, obj, ctype="application/json"):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8") \
            if ctype.startswith("application/json") else obj
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body_bytes(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def _read_json(self):
        try:
            return json.loads(self._body_bytes().decode("utf-8") or "{}")
        except Exception:
            return {}

    # ---- 路由 ----
    def do_GET(self):
        p = self.path
        if p.startswith("/health"):
            return self._send(200, {"ok": True, "service": "glasses-ai-center",
                                    "version": "3.3"})
        if p.startswith("/devices"):
            # ★★ 2026-10-23【通用化】支持 ?type=robot 之类过滤
            #   不传就是全部；传了只返回该类型。离线设备依然返回，
            #   但 online 字段是 false —— 由调用方（FairyX）决定显不显示。
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(p).query)
            want = (q.get("type") or [""])[0].strip().lower()
            with _dev_lock:
                _refresh_online()
                devs = list(_devices.values())
            if want:
                devs = [d for d in devs
                        if str(d.get("type") or "").lower() == want]
            return self._send(200, {"ok": True, "count": len(devs),
                                    "type": want or "all",
                                    "devices": devs})
        if p.startswith("/nav"):
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(p).query)
            res, err = _nav(q.get("origin", [None])[0], q.get("destination", [None])[0])
            if err:
                return self._send(200, {"ok": False, "error": err})
            return self._send(200, {"ok": True, **res})
        self._send(404, {"ok": False, "error": "no such route: " + p})

    def do_POST(self):
        p = self.path
        if p.startswith("/device/register"):
            return self._send(200, _register_device(self._read_json()))
        if p.startswith("/chat"):
            j = self._read_json()
            text = str(j.get("text") or "").strip()
            if not text:
                return self._send(200, {"ok": False, "error": "缺 text"})
            msgs = [{"role": "system", "content": "你是智能眼镜助手 Fairy，简洁、口语化、中文。"},
                    {"role": "user", "content": text}]
            ans, err = _chat(msgs)
            return self._send(200, {"ok": bool(ans), "text": ans or "",
                                    "provider": err or ""})
        if p.startswith("/translate"):
            j = self._read_json()
            text = str(j.get("text") or "").strip()
            to = str(j.get("to") or "zh").strip()
            if not text:
                return self._send(200, {"ok": False, "error": "缺 text"})
            ans, err = _baidu_translate(text, to)
            return self._send(200, {"ok": bool(ans), "text": ans or "", "error": err or ""})
        if p.startswith("/tts"):
            j = self._read_json()
            text = str(j.get("text") or "").strip()
            if not text:
                return self._send(200, {"ok": False, "error": "缺 text"})
            try:
                fd, tmp = tempfile.mkstemp(suffix=".wav", dir=DATA_DIR)
                os.close(fd)
                _tts(text, tmp)
                with open(tmp, "rb") as f:
                    wav = f.read()
                os.remove(tmp)
                return self._send(200, wav, "audio/wav")
            except Exception as e:
                return self._send(200, {"ok": False, "error": "tts: %s" % str(e)[:80]})
        if p.startswith("/asr"):
            wav = self._body_bytes()
            if not wav:
                return self._send(200, {"ok": False, "error": "缺音频"})
            text, err = _asr(wav)
            return self._send(200, {"ok": bool(text), "text": text or "", "error": err or ""})
        if p.startswith("/vqa"):
            from urllib.parse import parse_qs, urlparse
            q = parse_qs(urlparse(p).query)
            mode = (q.get("mode") or ["look"])[0]
            img = self._body_bytes()
            if not img:
                return self._send(200, {"ok": False, "error": "缺图片"})
            ans, err = _vqa(img, mode)
            return self._send(200, {"ok": bool(ans), "text": ans or "", "error": err or ""})
        self._send(404, {"ok": False, "error": "no such route: " + p})

    def log_message(self, *a):
        pass


def main():
    _load_devices()
    _refresh_online()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("glasses-ai-center 启动: 0.0.0.0:%d" % PORT, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
