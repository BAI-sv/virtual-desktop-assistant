# -*- coding: utf-8 -*-
"""
device_adapters.py —— 【设备适配层】统一契约

【为什么需要它】
  用户要求："保留调用口，后期让手表、汽车等等都带进来。"
  现在的现实是每个设备各走各的路：
      BLE 设备(美的/海尔) -> nearby.py 扫广播
      易微联灯            -> 浏览器自动化点网页
      智慧生活/天猫精灵    -> LDPlayer + adb 点界面
      蓝牙音箱(方糖)       -> Windows 音频设备
  再加手表、汽车就会第三次、第四次重复造轮子。

  所以这里定一层**统一契约**：接入新设备 = 写一个 adapter（四个方法），不改上层。

【契约】每个适配器实现四个方法
    caps()                   -> dict   这个协议能干什么、要不要账号/配对
    discover()               -> list   发现设备  [{id,name,kind,online,...}]
    state(dev_id)            -> dict   读状态
    act(dev_id, action, p)   -> dict   执行动作  {"ok":bool,"result":...,"error":...}

【注册表】
    ADAPTERS = {"协议名": 适配器实例}
    上层（插件/球/未来的手表端）只认这套接口，不关心底下是 BLE 还是 ADB 还是 HTTP。

【给"其他设备"留的口（都已实测可用）】
    1) HTTP（最通用，任何能联网的设备都能用）
       GET  http://<PC>:19387/api/fairy/meta    读状态
       POST http://<PC>:19387/api/fairy/say     让 Fairy 说话
       POST http://<PC>:19387/api/fairy/task    上报进度
    2) 本地服务（可被复用）
       ComfyUI 8188 / IndexTTS 9881 / Ollama 11434 / llama-server 8080
    3) DSH 平台扩展点（写插件时用）
       ctx.webServer.register / ctx.get('tools').register / 槽位 / 事件
"""


class Adapter:
    """所有设备适配器的基类。照着写一个就能接一类设备。"""

    name = "base"              # 协议名（注册表的 key）
    protocol = "unknown"       # 人话协议名
    needs_account = False      # 需不需要用户账号
    needs_pairing = False      # 需不需要配对/确认

    def caps(self):
        """这个协议能干什么。"""
        return {
            "protocol": self.protocol,
            "actions": [],              # 支持的动作名，如 ["on","off","play","pause"]
            "needs_account": self.needs_account,
            "needs_pairing": self.needs_pairing,
            "discovery": "none",        # none / ble / mdns / ssdp / arp / adb / http
        }

    def discover(self):
        """发现设备。返回 list[dict]，每个至少要有 id 和 name。"""
        return []

    def state(self, dev_id):
        """读一台设备的状态。"""
        return {"ok": False, "error": "not implemented"}

    def act(self, dev_id, action, params=None):
        """对一台设备执行动作。"""
        return {"ok": False, "error": "not implemented"}


# ==========================================================================
# 已接入的适配器（都是实测跑通过的路径）
# ==========================================================================
class NearbyAdapter(Adapter):
    """BLE/mDNS/SSDP/ARP 扫描到的一切（美的空调、海尔家电、博流模组…）。

    只读：只监听广播和系统 ARP 表，不主动连设备、不配网。
    """
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
    """电脑蓝牙音频输出设备（天猫精灵「方糖」就走这条路）。

    这一类的能力是"当音箱用"：把声音播到它上面 —— 这是所有智能音箱
    最通用、最不依赖厂商协议的能力。
    """
    name = "bluetooth_audio"
    protocol = "蓝牙音频输出（A2DP）"
    needs_pairing = True

    def caps(self):
        c = super().caps()
        c.update({"actions": ["play_file", "list"], "discovery": "windows_audio"})
        return c

    def discover(self):
        """列系统里的音频输出端点（含蓝牙音箱）。"""
        import subprocess
        # ★ 2026-10-10【黑窗】0x08000000 = CREATE_NO_WINDOW（不是 0x00000200）。
        #   父进程没控制台时（pythonw / 计划任务），不加这个起 powershell 就弹终端。
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
            if n and ("Stereo" in n or "扬声器" in n or "音箱" in n or "方糖" in n):
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
    """【给未来的设备留的标准口】任何设备通过 HTTP 就能用 Fairy。

    这是最通用的一类：手表、汽车、手机 App、另一个 PC —— 只要能发 HTTP
    就能：读状态 / 让 Fairy 说话 / 上报进度。不需要认识 BLE 或 ADB。
    """
    name = "fairy_http"
    protocol = "HTTP 桥（最通用，任何能联网的设备都行）"
    # ★ 2026-10-10 独立运行改造：不再打死 19387。
    #   DSH 在 -> 用 DSH（行为与改造前一致）；DSH 不在 -> 自动切本地 fairy_host(19388)。
    #   两边契约相同，所以下面的代码只用 self._base()，别的什么都不用改。
    BASE = "http://127.0.0.1:19388"   # ★ 2026-10-18：兜底改本地（19387 那条在 DSH 0.2.1 上必然 401）

    def _base(self):
        """当前该用的 Fairy 接口地址（动态，带 5 秒缓存）。"""
        try:
            import fairy_endpoints as _ep
            return _ep.resolve_base()[0]
        except Exception:
            return self.BASE          # 拿不到配置根就退回默认，不许因此崩掉

    def caps(self):
        c = super().caps()
        c.update({"actions": ["meta", "say", "task"], "discovery": "http"})
        return c

    def discover(self):
        b = self._base()
        # ★ 2026-10-18：原来用 `":19387" in b` 判断是不是 DSH —— DSH 0.2.1 起端口
        #   随机（也可能是 58841），这个判断会误报。改成直接问 fairy_endpoints
        #   它这次到底选了哪一边（返回值第二项就是 'dsh' / 'local'）。
        try:
            import fairy_endpoints as _ep
            src = "DSH" if _ep.resolve_base()[1] == "dsh" else "本地 Fairy 服务"
        except Exception:
            src = "本地 Fairy 服务" if ":19388" in b else "Fairy"
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
                # ★ 说话走统一降级链（直连 IndexTTS 优先），不再依赖 DSH 当转发壳
                try:
                    import fairy_endpoints as _ep
                    r = _ep.tts_get((params or {}).get("text", ""))
                    if r.get("ok") and r.get("data"):
                        return {"ok": True, "result": "%d 字节音频（%s/%s）"
                                % (len(r["data"]), r.get("route"), r.get("voice"))}
                except Exception:
                    pass
                # 兜底：还是打 HTTP 接口（DSH 或本地 host 都认这个契约）
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


# ==========================================================================
# 注册表
# ==========================================================================
class SerialAdapter(Adapter):
    """串口网关：任何"用串口把数据转给 PC"的设备都走这条。

    典型用法：星闪开发板 / ESP32 / Arduino / 各种网关模块 —— 板子负责无线部分，
    通过串口把结果吐给 PC，PC 这边就是普通串口读写。

    协议（约定一个极简的 JSON 行协议，板子照这个发就行）：
        PC -> 板子:  {"cmd":"scan"}\n              下发命令
        板子 -> PC:  {"ok":true,"devices":[...]}\n 回一行 JSON
    这样板子端不用实现 HTTP，串口够用（省内存、省电）。
    """

    name = "serial"
    protocol = "串口网关（板子无线、PC 有线）"
    needs_pairing = False

    def __init__(self, port=None, baud=115200):
        self.port = port          # None = 自动找（接上板子后自动认）
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
            # 优先选描述里带常见 USB 串口芯片关键词的
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
    """星闪（NearLink / SLE）开发板网关。

    【为什么这样做】
      星闪是新的物理层空口，PC 直接收发需要星闪芯片。而 WS63 系列开发板
      自带 Wi-Fi + HTTP/MQTT，所以正确姿势是：
          星闪设备 <-SLE-> 开发板 <-Wi-Fi/HTTP 或 串口-> PC
      板子当网关，PC 侧就是普通 HTTP/串口 —— 本适配器把两条都兼容了。

    【板子端要实现的 HTTP 接口】（写固件时照这个来）
      GET  /nearlink/scan            -> {"ok":true,"devices":[{id,name,rssi,addr}]}
      GET  /nearlink/state?id=xxx    -> {"ok":true,"state":{...}}
      POST /nearlink/act  {id,action,params} -> {"ok":true,"result":...}
    """

    name = "nearlink"
    protocol = "星闪（SLE）开发板网关"
    BASE = "http://192.168.1.100"        # 板子的 IP —— 第一次跑 discover 时可以自动探

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
            # 板子还没配 IP / 还没烧固件 —— 这时给出串口那条路
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


# ==========================================================================
# Home Assistant 中枢适配器（统一接 ewelink / 米家 / 美的 等智能设备）
# ==========================================================================
def _ha_token_file():
    """从 logs/ha_token 读 HA 的 Long-Lived Access Token（无则空）。"""
    try:
        import os
        import fairy_root
        p = os.path.join(fairy_root.LOGS, "ha_token")
        if os.path.exists(p):
            return open(p, encoding="utf-8").read().strip()
    except Exception:
        pass
    return ""


class HomeAssistantAdapter(Adapter):
    """Home Assistant 中枢（统一接 ewelink / 米家 / 美的 等智能设备）。

    【为什么拿 HA 当中枢】
      各家设备协议不互通（ewelink/米家/美的各走各的云）。Home Assistant
      一个实例就能接上千种设备，对外统一暴露一套 REST API。FairyX 只认
      HA 这一套接口，就能控制接入 HA 的全部设备 —— 这就是"连世界设备"的正路。

    【双向联动】
      球指令 -> 设备：act() 走 HA 的 service（turn_on/turn_off/set 等）
      设备状态 -> 球播报：配 ha_bridge（轮询 /api/states，变化时调 /api/fairy/say）
    """
    name = "homeassistant"
    protocol = "Home Assistant 中枢"
    needs_account = True

    def __init__(self, base=None, token=None):
        import os
        self.base = (base or os.environ.get("HA_BASE") or
                     "http://127.0.0.1:8123").rstrip("/")
        self.token = token or os.environ.get("HA_TOKEN") or _ha_token_file()

    def _hdr(self):
        return {"Authorization": "Bearer " + self.token,
                "Content-Type": "application/json"}

    def _get(self, path, timeout=8):
        import json
        import urllib.parse
        import urllib.request
        req = urllib.request.Request(
            self.base + urllib.parse.quote(path, safe="/."), headers=self._hdr())
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def _post(self, path, obj=None, timeout=8):
        import json
        import urllib.parse
        import urllib.request
        data = json.dumps(obj or {}).encode("utf-8")
        req = urllib.request.Request(
            self.base + urllib.parse.quote(path, safe="/."), data=data,
            headers=self._hdr(), method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
            return json.loads(body) if body.strip() else {}

    def _ready(self):
        return bool(self.token)

    def caps(self):
        c = super().caps()
        c.update({"actions": ["on", "off", "toggle", "set"],
                  "discovery": "http",
                  "account_note": "需要 HA 的 Long-Lived Access Token（写 logs/ha_token）"})
        return c

    def discover(self):
        """把 HA 里所有 entity 变成 FairyX 设备。"""
        if not self._ready():
            return [{"id": "ha", "name": "Home Assistant（未配置 token）",
                     "kind": "hub", "online": False, "error": "no_token"}]
        try:
            states = self._get("/api/states")
        except Exception as e:
            return [{"id": "ha", "name": "Home Assistant", "kind": "hub",
                     "online": False, "error": str(e)[:80]}]
        _internal = ("homeassistant", "person", "zone", "scene",
                     "automation", "script", "group")
        out = []
        for st in states:
            eid = st.get("entity_id", "")
            if not eid:
                continue
            domain = eid.split(".")[0]
            if domain in _internal:
                continue
            attr = st.get("attributes", {}) or {}
            stt = st.get("state")
            out.append({"id": eid, "name": attr.get("friendly_name") or eid,
                        "kind": domain, "online": stt not in ("unavailable", "unknown"),
                        "state": stt})
        return out

    def state(self, dev_id):
        try:
            st = self._get("/api/states/" + dev_id)
            attr = st.get("attributes", {}) or {}
            keep = {k: attr[k] for k in ("temperature", "current_temperature",
                                         "brightness", "volume_level")
                    if k in attr}
            return {"ok": True, "state": st.get("state"),
                    "friendly": attr.get("friendly_name", dev_id),
                    "attributes": keep}
        except Exception as e:
            return {"ok": False, "error": str(e)[:100]}

    def act(self, dev_id, action, params=None):
        if not self._ready():
            return {"ok": False, "error": "未配置 HA token（logs/ha_token）"}
        domain = dev_id.split(".")[0]
        try:
            if action in ("on", "off", "toggle"):
                svc = {"on": "turn_on", "off": "turn_off", "toggle": "toggle"}[action]
                return self._service(domain, svc, dev_id)
            if action == "set":
                svc, data = self._resolve_set(domain, params or {})
                return self._service(domain, svc, dev_id, data)
            return {"ok": False, "error": "不支持的 action: %s" % action}
        except Exception as e:
            return {"ok": False, "error": str(e)[:100]}

    def _service(self, domain, service, dev_id, data=None):
        r = self._post("/api/services/%s/%s" % (domain, service),
                       dict(data or {}, entity_id=dev_id))
        return {"ok": True, "result": r}

    def _resolve_set(self, domain, params):
        if domain == "climate":
            data = {}
            if "temperature" in params:
                data["temperature"] = params["temperature"]
            if "hvac_mode" in params:
                data["hvac_mode"] = params["hvac_mode"]
            return "set_temperature", data
        if domain == "light":
            data = {}
            if "brightness" in params:
                data["brightness"] = int(params["brightness"])
            if "color_temp" in params:
                data["color_temp"] = int(params["color_temp"])
            return "turn_on", data
        return "turn_on", dict(params)


ADAPTERS = {
    NearbyAdapter.name: NearbyAdapter(),
    BluetoothAudioAdapter.name: BluetoothAudioAdapter(),
    FairyBridgeAdapter.name: FairyBridgeAdapter(),
    SerialAdapter.name: SerialAdapter(),
    NearLinkAdapter.name: NearLinkAdapter(),
    HomeAssistantAdapter.name: HomeAssistantAdapter(),
}

# ---------------------------------------------------------------------------
# ★ 2026-10-15【机器人融合】把 tools\robot 那套接成这里的一个适配器
#   为什么用"懒加载"而不是 import 时注册：
#     · robot_bridge 会连带 import 机器人安全层/后端，启动时没必要付这个成本
#     · 更重要的是：机器人那套【必须能在没有硬件、甚至没有 robot 目录时也不影响别的设备】
#   失败要留痕（不静默）：_ROBOT_LOAD_NOTE 记录原因，discover_all 会带出来
# ---------------------------------------------------------------------------
_ROBOT_LOAD_NOTE = ""
_ROBOT_TRIED = False


def _ensure_robot_adapter():
    """按需把机器人适配器装进 ADAPTERS（幂等；失败留痕，绝不抛）。"""
    global _ROBOT_LOAD_NOTE, _ROBOT_TRIED
    if "robot" in ADAPTERS:
        return True
    if _ROBOT_TRIED:
        return False
    _ROBOT_TRIED = True
    try:
        import robot_bridge as rb          # tools\robot_bridge.py
        ADAPTERS[rb.as_device_adapter().name] = rb.as_device_adapter()
        _ROBOT_LOAD_NOTE = "已接入（robot_bridge）"
        return True
    except Exception as e:
        _ROBOT_LOAD_NOTE = "未接入: %s: %s" % (type(e).__name__, str(e)[:80])
        return False


# ---------------------------------------------------------------------------
# ★ 2026-10-22【智能眼镜融合】把 glasses\ 那套接成这里的一个适配器
#   同样用"懒加载"：眼镜中枢没起 / 没装 flask 时不影响别的设备，失败留痕。
#   眼镜在线才上报（discover 只返回 online=True）—— 离线一律不显示、不播报。
# ---------------------------------------------------------------------------
_GLASSES_LOAD_NOTE = ""
_GLASSES_TRIED = False


def _ensure_glasses_adapter():
    """按需把智能眼镜适配器装进 ADAPTERS（幂等；失败留痕，绝不抛）。"""
    global _GLASSES_LOAD_NOTE, _GLASSES_TRIED
    if "glasses" in ADAPTERS:
        return True
    if _GLASSES_TRIED:
        return False
    _GLASSES_TRIED = True
    try:
        import glasses_adapter as ga       # tools\glasses_adapter.py
        ADAPTERS[ga.GlassesAdapter.name] = ga.GlassesAdapter()
        _GLASSES_LOAD_NOTE = "已接入（glasses_adapter）"
        return True
    except Exception as e:
        _GLASSES_LOAD_NOTE = "未接入: %s: %s" % (type(e).__name__, str(e)[:80])
        return False


# ---------------------------------------------------------------------------
# ★★ 2026-10-23【通用化 · 零改动接新设备】
#
#   原来每接一台新设备（机器人 10-15、眼镜 10-22）都要在这里手写一段
#   _ensure_xxx_adapter()，等于接一类设备改一次代码。
#
#   现在改成自动扫描：tools\ 下任何 *_adapter.py / *_bridge.py 模块，
#   只要里面有一个类实现了 name + caps/discover/state/act 四件套，就自动注册。
#
#   新设备接入 = 丢一个 car_adapter.py 进 tools\，不用碰这个文件。
#   扫描失败只留痕，绝不影响已经能用的设备。
# ---------------------------------------------------------------------------
_AUTO_SCAN_NOTE = ""
_AUTO_SCAN_TRIED = False


def _looks_like_adapter_class(obj):
    """判定一个类是不是合格的四件套适配器。"""
    if not isinstance(obj, type):
        return False
    for m in ("caps", "discover", "state", "act"):
        if not callable(getattr(obj, m, None)):
            return False
    return bool(getattr(obj, "name", ""))


def _auto_discover_adapters():
    """扫描 tools 目录下的 *_adapter.py 和 *_bridge.py，自动注册四件套。

    幂等；任何异常只记录不抛。返回本次新注册的名字列表。
    """
    global _AUTO_SCAN_NOTE, _AUTO_SCAN_TRIED
    if _AUTO_SCAN_TRIED:
        return []
    _AUTO_SCAN_TRIED = True

    try:
        import glob
        import importlib
        import os
        import sys
    except Exception as e:                     # pragma: no cover
        _AUTO_SCAN_NOTE = "扫描不可用: %s" % type(e).__name__
        return []

    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)

    # 两种命名都收：xxx_adapter.py（眼镜这种）、xxx_bridge.py（机器人这种）
    patterns = [os.path.join(here, "*_adapter.py"),
                os.path.join(here, "*_bridge.py")]
    paths = []
    for p in patterns:
        paths.extend(glob.glob(p))

    added, skipped = [], []
    for path in sorted(set(paths)):
        mod_name = os.path.splitext(os.path.basename(path))[0]
        if mod_name in ("device_adapters",):
            continue
        try:
            mod = importlib.import_module(mod_name)
        except Exception as e:
            skipped.append("%s(%s)" % (mod_name, type(e).__name__))
            continue
        for attr in dir(mod):
            if attr.startswith("_"):
                continue
            try:
                cls = getattr(mod, attr)
            except Exception:
                continue
            if not _looks_like_adapter_class(cls):
                continue
            try:
                inst = cls()
                nm = getattr(inst, "name", "")
                if nm and nm not in ADAPTERS:
                    ADAPTERS[nm] = inst
                    added.append(nm)
            except Exception as e:
                skipped.append("%s.%s(%s)" % (mod_name, attr, type(e).__name__))

    bits = []
    if added:
        bits.append("自动接入: " + ", ".join(added))
    if skipped:
        bits.append("跳过: " + ", ".join(skipped[:4]))
    _AUTO_SCAN_NOTE = "；".join(bits) or "扫描完成，无新适配器"
    return added


def auto_scan_note():
    """自动扫描的结果摘要（给体检/排障看）。"""
    _auto_discover_adapters()
    return _AUTO_SCAN_NOTE


def all_caps():
    """列所有协议的能力（给"以后接手表/汽车"做决策用）。"""
    _ensure_robot_adapter()
    _ensure_glasses_adapter()
    _auto_discover_adapters()
    return {k: v.caps() for k, v in ADAPTERS.items()}


def discover_all():
    """所有适配器一起发现。新接入的适配器会被自动扫描进来。"""
    _ensure_robot_adapter()
    _ensure_glasses_adapter()
    _auto_discover_adapters()
    out = []
    for a in ADAPTERS.values():
        try:
            for d in a.discover():
                d["adapter"] = a.name
                out.append(d)
        except Exception as e:
            out.append({"adapter": a.name, "error": str(e)[:100]})
    return out


def robot_note():
    """机器人的接入情况（给体检/排障看）。"""
    _ensure_robot_adapter()
    return _ROBOT_LOAD_NOTE or ("已接入" if "robot" in ADAPTERS else "未接入")


def glasses_note():
    """智能眼镜的接入情况（给体检/排障看）。"""
    _ensure_glasses_adapter()
    return _GLASSES_LOAD_NOTE or ("已接入" if "glasses" in ADAPTERS else "未接入")


if __name__ == "__main__":
    import json
    import sys
    if "--caps" in sys.argv:
        print(json.dumps(all_caps(), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(discover_all(), ensure_ascii=False, indent=1)[:2000])
