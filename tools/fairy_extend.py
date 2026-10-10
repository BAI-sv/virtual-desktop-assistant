# -*- coding: utf-8 -*-


import os
import sys
import json
import time
import socket
import threading
import importlib.util
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA_DIR = os.path.join(ROOT, "data")
LOG_DIR = os.path.join(ROOT, "logs")
DEVICES_FILE = os.path.join(DATA_DIR, "devices.json")
PID_FILE = os.path.join(DATA_DIR, "fairy_extend.pid")
LOG_FILE = os.path.join(LOG_DIR, "fairy_extend.log")


CONF = {
    "host": "0.0.0.0",
    "port": 8090,
    "scan_interval": 60,
    "device_ttl": 300,
}

_lock = threading.Lock()
_stop_evt = threading.Event()
_started = time.time()


class DeviceRegistry:

    def __init__(self, data_file):
        self._file = data_file
        self._devs = {}
        self._load()

    def _load(self):
        try:
            if os.path.exists(self._file):
                with open(self._file, encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    self._devs = raw
        except Exception:
            self._devs = {}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._file), exist_ok=True)
            tmp = self._file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._devs, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._file)
        except Exception as e:
            self._log("save fail: %s" % e)

    @staticmethod
    def _log(msg):
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
        except Exception:
            pass

    def upsert_many(self, found):
        now = time.time()
        seen = set()
        added, updated, gone = 0, 0, 0
        with _lock:
            for d in found:
                did = d.get("id")
                if not did:
                    continue
                seen.add(did)
                prev = self._devs.get(did)
                if prev is None:
                    rec = dict(d)
                    rec["first_seen"] = rec["last_seen"] = now
                    rec["online"] = True
                    self._devs[did] = rec
                    added += 1
                    self._log("上线新设备: %s (%s)" % (rec.get("name", did), rec.get("kind", "?")))
                else:
                    self._devs[did].update(d)
                    self._devs[did]["last_seen"] = now
                    self._devs[did]["online"] = True
                    updated += 1

            for did in list(self._devs.keys()):
                rec = self._devs[did]
                if rec.get("online") and did not in seen:
                    if now - rec.get("last_seen", now) > CONF["device_ttl"]:
                        rec["online"] = False
                        gone += 1
                        self._log("设备离线: %s" % rec.get("name", did))
            self._save()
        return {"added": added, "updated": updated, "offline": gone}

    def list(self, kind=None):
        with _lock:
            items = list(self._devs.values())
        if kind:
            items = [x for x in items if x.get("kind") == kind]
        return items

    def get(self, did):
        with _lock:
            return self._devs.get(did)

    def register_external(self, name, meta=None):
        did = "ext:%s" % name
        now = time.time()
        with _lock:
            rec = self._devs.get(did) or {}
            rec.update({
                "id": did, "name": name, "kind": "external",
                "adapter": "fairy", "online": True,
                "last_seen": now,
                "meta": meta or {},
            })
            if "first_seen" not in rec:
                rec["first_seen"] = now
            self._devs[did] = rec
            self._save()
        self._log("外部设备注册接入: %s" % name)
        return rec


_ADAPTERS = None

def _adapters():
    global _ADAPTERS
    if _ADAPTERS is None:
        fp = os.path.join(HERE, "device_adapters.py")
        s = importlib.util.spec_from_file_location("fairy_device_adapters", fp)
        m = importlib.util.module_from_spec(s)
        s.loader.exec_module(m)
        _ADAPTERS = m
    return _ADAPTERS


class Scanner:
    def __init__(self, registry):
        self._reg = registry
        self._thread = None
        self.scanning = False

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True, name="ext-scan")
        self._thread.start()

    def _loop(self):
        while not _stop_evt.is_set():
            try:
                self.scan_once()
            except Exception as e:
                DeviceRegistry._log("scan loop err: %s" % e)
            _stop_evt.wait(CONF["scan_interval"])

    def scan_once(self):
        self.scanning = True
        try:
            found = _adapters().discover_all()
            return self._reg.upsert_many(found)
        finally:
            self.scanning = False

    def force_scan(self):
        return self.scan_once()


def _json(obj, code=200, extra=None):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }
    if extra:
        headers.update(extra)
    return code, headers, body

def _read_body(handler):
    try:
        n = int(handler.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        raw = handler.rfile.read(n)
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}

class ExtHandler(BaseHTTPRequestHandler):
    registry = None
    scanner = None
    server_version = "FairyExt/1.0"

    def log_message(self, *a):
        pass

    def _send(self, code, headers, body):
        self.send_response(code)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        code, h, b = _json({})
        self._send(code, h, b)

    def do_GET(self):
        p = self.path.split("?", 1)[0]
        try:
            if p == "/v1/status":
                self.handle_status()
            elif p == "/v1/devices":
                self.handle_list()
            elif p.startswith("/v1/devices/"):
                self.handle_one(p[len("/v1/devices/"):])
            else:
                self._send(*_json({"ok": False, "error": "not found: " + p}, 404))
        except Exception as e:
            self._send(*_json({"ok": False, "error": str(e)[:200]}, 500))

    def do_POST(self):
        p = self.path.split("?", 1)[0]
        body = _read_body(self)
        try:
            if p == "/v1/scan":
                r = self.scanner.force_scan()
                self._send(*_json({"ok": True, "result": r, "count": len(self.registry.list())}))
            elif p == "/v1/control":
                self.handle_control(body)
            elif p == "/v1/register":
                self.handle_register(body)
            else:
                self._send(*_json({"ok": False, "error": "not found: " + p}, 404))
        except Exception as e:
            self._send(*_json({"ok": False, "error": str(e)[:200]}, 500))

    def handle_status(self):
        self._send(*_json({
            "ok": True,
            "service": "FairyX device extension",
            "uptime_s": int(time.time() - _started),
            "scanning": self.scanner.scanning,
            "device_count": len(self.registry.list()),
            "devices": {k: len(self.registry.list(k)) for k in ("lan", "ble", "audio", "serial", "sle", "external")},
        }))

    def handle_list(self):
        devs = self.registry.list()
        self._send(*_json({"ok": True, "count": len(devs), "devices": devs}))

    def handle_one(self, did):
        import urllib.parse
        did = urllib.parse.unquote(did)
        rec = self.registry.get(did)
        if not rec:
            self._send(*_json({"ok": False, "error": "no such device: " + did}, 404))
            return
        self._send(*_json({"ok": True, "device": rec}))

    def handle_control(self, body):
        did = body.get("id")
        action = body.get("action")
        params = body.get("params") or {}
        if not did or not action:
            self._send(*_json({"ok": False, "error": "need id + action"}, 400))
            return
        rec = self.registry.get(did)
        if not rec:
            self._send(*_json({"ok": False, "error": "no such device: " + did}, 404))
            return
        adapter_name = rec.get("adapter") or "nearby"
        ad = _adapters().ADAPTERS.get(adapter_name)
        if ad is None:
            self._send(*_json({"ok": False, "error": "unknown adapter: " + adapter_name}, 400))
            return
        try:
            r = ad.act(did, action, params)
        except Exception as e:
            r = {"ok": False, "error": str(e)[:200]}
        self._send(*_json({"ok": True, "result": r}))

    def handle_register(self, body):
        name = (body.get("name") or "").strip()
        if not name:
            self._send(*_json({"ok": False, "error": "need name"}, 400))
            return
        rec = self.registry.register_external(name, body.get("meta"))
        self._send(*_json({"ok": True, "device": rec}))


def _print(s=""):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        print(s)
    except Exception:
        print(str(s).encode("utf-8", "replace").decode("utf-8", "replace"))

def run_server(reg, scanner):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(PID_FILE, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))
    ExtHandler.registry = reg
    ExtHandler.scanner = scanner
    httpd = ThreadingHTTPServer((CONF["host"], CONF["port"]), ExtHandler)
    host = CONF["host"] if CONF["host"] != "0.0.0.0" else _lan_ip() or "127.0.0.1"
    _print("FairyX 设备扩展已启动")
    _print("  自动扫描周期 : %s 秒" % CONF["scan_interval"])
    _print("  HTTP 服务    : http://%s:%s" % (host, CONF["port"]))
    _print("  端点          : /v1/status /v1/devices /v1/devices/<id> "
          "/v1/scan /v1/control /v1/register")
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            os.remove(PID_FILE)
        except Exception:
            pass

def _lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None

def _find_pid():
    try:
        with open(PID_FILE) as f:
            return int(f.read().strip())
    except Exception:
        return None

def _stop():
    pid = _find_pid()
    if pid:
        try:
            os.kill(pid, 15)
            _print("已发送停止信号给 %s" % pid)
        except Exception as e:
            _print("停止失败: %s" % e)
    else:
        _print("没有运行中的服务（无 pid）")

def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    c = a[0]
    reg = DeviceRegistry(DEVICES_FILE)
    scanner = Scanner(reg)
    if c == "--run":
        scanner.start()
        run_server(reg, scanner)
        return 0
    if c == "--scan":
        r = scanner.scan_once()
        _print("扫描完成: %s" % r)
        for d in reg.list():
            _print("  [%s] %s  %s  online=%s" % (d.get("kind"), d.get("name"), d.get("id"), d.get("online")))
        return 0
    if c == "--list":
        devs = reg.list()
        _print("设备池共 %d 台:" % len(devs))
        for d in devs:
            _print("  [%s] %s  %s  online=%s" % (d.get("kind"), d.get("name"), d.get("id"), d.get("online")))
        return 0
    if c == "--stop":
        _stop()
        return 0
    if c == "--selftest":
        _print("== 自检 ==")
        r = scanner.scan_once()
        _print("扫描: %s" % r)
        _print("设备池: %d 台" % len(reg.list()))

        import urllib.request
        try:
            with urllib.request.urlopen("http://127.0.0.1:%s/v1/status" % CONF["port"], timeout=3) as resp:
                j = json.loads(resp.read().decode("utf-8"))
            _print("HTTP /v1/status: ok, devices=%s" % j.get("device_count"))
        except Exception as e:
            _print("HTTP 未连通（服务未启动属正常）: %s" % str(e)[:60])
        _print("== 自检完成 ==")
        return 0
    print("未知命令: %s（跑 `fairy_extend.py --help` 看用法）" % c)
    return 2

if __name__ == "__main__":
    sys.exit(main())
