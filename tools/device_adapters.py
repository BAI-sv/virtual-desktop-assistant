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
    BASE = "http://127.0.0.1:19387"

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


ADAPTERS = {
    NearbyAdapter.name: NearbyAdapter(),
    BluetoothAudioAdapter.name: BluetoothAudioAdapter(),
    FairyBridgeAdapter.name: FairyBridgeAdapter(),
    SerialAdapter.name: SerialAdapter(),
    NearLinkAdapter.name: NearLinkAdapter(),
}


def all_caps():
    """列所有协议的能力（给"以后接手表/汽车"做决策用）。"""
    return {k: v.caps() for k, v in ADAPTERS.items()}


def discover_all():
    """所有适配器一起发现（互不依赖，可以并行加线程）。"""
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
