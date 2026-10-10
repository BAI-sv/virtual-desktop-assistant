# -*- coding: utf-8 -*-

class Adapter:

    name = "base"
    protocol = "unknown"
    needs_account = False
    needs_pairing = False

    def caps(self):
        return {
            "protocol": self.protocol,
            "actions": [],
            "needs_account": self.needs_account,
            "needs_pairing": self.needs_pairing,
            "discovery": "none",
        }

    def discover(self):
        return []

    def state(self, dev_id):
        return {"ok": False, "error": "not implemented"}

    def act(self, dev_id, action, params=None):
        return {"ok": False, "error": "not implemented"}


class NearbyAdapter(Adapter):
    name = "nearby"
    protocol = "本地发现（BLE + mDNS + SSDP + ARP）"

    def __init__(self):
        self._nearby = None

    def _mod(self):
        if self._nearby is None:
            import importlib.util, os
            fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nearby.py")
            s = importlib.util.spec_from_file_location("fairy_nearby_mod", fp)
            m = importlib.util.module_from_spec(s)
            s.loader.exec_module(m)
            self._nearby = m
        return self._nearby

    def caps(self):
        c = super().caps()
        c.update({"actions": ["scan", "identify", "watch"], "discovery": "ble+mdns+ssdp+arp"})
        return c

    def discover(self):
        m = self._mod()
        r = m.scan(ble_seconds=4, mdns_seconds=3, ssdp_seconds=3)
        out = []
        for d in (r.get("lan") or []):
            out.append({"id": "lan:" + d["mac"], "name": d.get("vendor") or d["mac"],
                        "kind": "lan", "ip": d["ip"], "mac": d["mac"], "online": True})
        for d in ((r.get("ble") or {}).get("devices") or []):
            out.append({"id": "ble:" + d["address"], "name": d.get("name") or d["address"],
                        "kind": "ble", "rssi": d.get("rssi"), "online": True,
                        "vendor": " ".join(d.get("companies") or [])})
        return out

    def state(self, dev_id):
        m = self._mod()
        addr = dev_id.split(":", 1)[-1] if ":" in dev_id else dev_id
        return {"ok": True, "identify": m.identify(addr)}

    def act(self, dev_id, action, params=None):
        if action == "scan":
            return {"ok": True, "result": self.discover()}
        if action == "watch":
            m = self._mod()
            sec = int((params or {}).get("seconds", 15))
            r = m.watch(seconds=sec, ble_seconds=4)
            return {"ok": True, "result": r.get("events")}
        return {"ok": False, "error": "这个适配器只支持 scan/identify/watch（只读）"}

class BluetoothAudioAdapter(Adapter):
    name = "bluetooth_audio"
    protocol = "蓝牙音频输出（A2DP）"
    needs_pairing = True

    def caps(self):
        c = super().caps()
        c.update({"actions": ["play_file", "list"], "discovery": "windows_audio"})
        return c

    def discover(self):
        import subprocess


        CREATE_NO_WINDOW = 0x08000000
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command",
                                "Get-PnpDevice -Class AudioEndpoint,MEDIA -Status OK | "
                                "Select-Object -ExpandProperty FriendlyName"],
                               capture_output=True, timeout=30,
                               creationflags=CREATE_NO_WINDOW)
            names = (r.stdout or b"").decode("gbk", "replace").splitlines()
        except Exception:
            names = []
        out = []
        for n in names:
            n = n.strip()
            if n and ("Stereo" in n or "扬声器" in n or "音箱" in n or "<BT_SPEAKER>" in n):
                out.append({"id": "audio:" + n, "name": n, "kind": "audio", "online": True})
        return out

    def state(self, dev_id):
        return {"ok": True, "note": "audio endpoint has no readable state; playable = usable"}

    def act(self, dev_id, action, params=None):
        if action == "play_file":
            p = (params or {}).get("path")
            if not p:
                return {"ok": False, "error": "需要 path"}
            try:
                import ctypes
                mci = ctypes.windll.winmm.mciSendStringW
                alias = "adapterplay"
                mci("close " + alias, None, 0, None)
                if mci('open "%s" type mpegvideo alias %s' % (p, alias), None, 0, None) != 0:
                    if mci('open "%s" alias %s' % (p, alias), None, 0, None) != 0:
                        return {"ok": False, "error": "打不开音频"}
                mci("play %s wait" % alias, None, 0, None)
                mci("close " + alias, None, 0, None)
                return {"ok": True, "result": "played"}
            except Exception as e:
                return {"ok": False, "error": str(e)}
        return {"ok": False, "error": "支持 play_file / list"}

class FairyBridgeAdapter(Adapter):
    name = "fairy_http"
    protocol = "HTTP 桥（最通用，任何能联网的设备都行）"


    BASE = "http://127.0.0.1:19387"

    def _base(self):
        try:
            import fairy_endpoints as _ep
            return _ep.resolve_base()[0]
        except Exception:
            return self.BASE

    def caps(self):
        c = super().caps()
        c.update({"actions": ["meta", "say", "task"], "discovery": "http"})
        return c

    def discover(self):
        b = self._base()
        src = "DSH" if ":19387" in b else "本地 Fairy 服务"
        return [{"id": "fairy:local", "name": "DSH / Fairy 本体（%s）" % src,
                 "kind": "service",
                 "endpoints": ["/api/fairy/meta", "/api/fairy/say", "/api/fairy/task"]}]

    def state(self, dev_id):
        import json as _json, urllib.request
        try:
            with urllib.request.urlopen(self._base() + "/api/fairy/meta", timeout=8) as r:
                return {"ok": True, "result": _json.loads(r.read().decode("utf-8"))}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def act(self, dev_id, action, params=None):
        import json as _json, urllib.request
        try:
            if action == "say":

                try:
                    import fairy_endpoints as _ep
                    r = _ep.tts_get((params or {}).get("text", ""))
                    if r.get("ok") and r.get("data"):
                        return {"ok": True, "result": "%d 字节音频（%s/%s）"
                                % (len(r["data"]), r.get("route"), r.get("voice"))}
                except Exception:
                    pass

                req = urllib.request.Request(self._base() + "/api/fairy/say",
                                             data=_json.dumps({"text": (params or {}).get("text", "")}).encode(),
                                             headers={"content-type": "application/json"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    return {"ok": True, "result": "%d 字节音频" % len(r.read())}
            if action == "task":
                req = urllib.request.Request(self._base() + "/api/fairy/task",
                                             data=_json.dumps(params or {}).encode(),
                                             headers={"content-type": "application/json"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    return {"ok": True, "result": _json.loads(r.read().decode("utf-8"))}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        return {"ok": False, "error": "支持 meta / say / task"}


class SerialAdapter(Adapter):

    name = "serial"
    protocol = "串口网关（板子无线、PC 有线）"
    needs_pairing = False

    def __init__(self, port=None, baud=115200):
        self.port = port
        self.baud = baud

    def _ports(self):
        try:
            import serial.tools.list_ports as lp
            return [(x.device, x.description or "") for x in lp.comports()]
        except Exception:
            return []

    def _open(self):
        import serial
        port = self.port
        if not port:
            ps = self._ports()
            if not ps:
                raise RuntimeError("没找到串口设备（板子插上了吗？驱动装了吗？）")

            for dev, desc in ps:
                if any(k in desc for k in ("CH34", "CP210", "FTDI", "USB-SERIAL", "USB Serial", "Silicon")):
                    port = dev
                    break
            port = port or ps[0][0]
        return serial.Serial(port, self.baud, timeout=3)

    def caps(self):
        c = super().caps()
        c.update({"actions": ["scan", "send", "list_ports"], "discovery": "serial"})
        return c

    def discover(self):
        out = []
        for dev, desc in self._ports():
            out.append({"id": "serial:" + dev, "name": "%s (%s)" % (desc or "串口设备", dev),
                        "kind": "serial", "online": True})
        return out

    def state(self, dev_id):
        return {"ok": True, "result": {"ports": self._ports(), "baud": self.baud}}

    def act(self, dev_id, action, params=None):
        import json as _json
        if action == "list_ports":
            return {"ok": True, "result": self._ports()}
        try:
            ser = self._open()
        except Exception as e:
            return {"ok": False, "error": str(e)}
        try:
            cmd = {"cmd": action}
            cmd.update(params or {})
            ser.write((_json.dumps(cmd, ensure_ascii=False) + "\n").encode("utf-8"))
            ser.flush()
            line = ser.readline().decode("utf-8", "replace").strip()
            if not line:
                return {"ok": False, "error": "板子没有回数据（超时）"}
            return {"ok": True, "result": _json.loads(line)}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        finally:
            try:
                ser.close()
            except Exception:
                pass

class NearLinkAdapter(Adapter):

    name = "nearlink"
    protocol = "星闪（SLE）开发板网关"
    BASE = "http://192.168.1.100"

    def __init__(self, base=None):
        if base:
            self.BASE = base

    def caps(self):
        c = super().caps()
        c.update({"actions": ["scan", "state", "act"], "discovery": "http+serial"})
        return c

    def _get(self, path, timeout=6):
        import json as _json, urllib.request
        with urllib.request.urlopen(self.BASE + path, timeout=timeout) as r:
            return _json.loads(r.read().decode("utf-8"))

    def _post(self, path, obj, timeout=15):
        import json as _json, urllib.request
        req = urllib.request.Request(self.BASE + path,
                                     data=_json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                                     headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return _json.loads(r.read().decode("utf-8"))

    def discover(self):
        try:
            j = self._get("/nearlink/scan")
            out = []
            for d in (j.get("devices") or []):
                out.append({"id": "sle:" + str(d.get("id") or d.get("addr")),
                            "name": d.get("name") or str(d.get("addr")),
                            "kind": "sle", "rssi": d.get("rssi"), "online": True})
            return out
        except Exception:

            return []

    def state(self, dev_id):
        try:
            return {"ok": True, "result": self._get("/nearlink/state?id=" + str(dev_id))}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def act(self, dev_id, action, params=None):
        try:
            return {"ok": True, "result": self._post("/nearlink/act",
                                                     {"id": dev_id, "action": action, "params": params or {}})}
        except Exception as e:
            return {"ok": False, "error": str(e)}

ADAPTERS = {
    NearbyAdapter.name: NearbyAdapter(),
    BluetoothAudioAdapter.name: BluetoothAudioAdapter(),
    FairyBridgeAdapter.name: FairyBridgeAdapter(),
    SerialAdapter.name: SerialAdapter(),
    NearLinkAdapter.name: NearLinkAdapter(),
}

def all_caps():
    return {k: v.caps() for k, v in ADAPTERS.items()}

def discover_all():
    out = []
    for a in ADAPTERS.values():
        try:
            for d in a.discover():
                d["adapter"] = a.name
                out.append(d)
        except Exception as e:
            out.append({"adapter": a.name, "error": str(e)[:100]})
    return out

if __name__ == "__main__":
    import json
    import sys
    if "--caps" in sys.argv:
        print(json.dumps(all_caps(), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(discover_all(), ensure_ascii=False, indent=1)[:2000])
