# -*- coding: utf-8 -*-
import os
import re
import socket
import subprocess
import sys
import time


CREATE_NO_WINDOW = 0x08000000


OUI = {

    "24:0A:C4": "Espressif(ESP32)", "3C:71:BF": "Espressif", "84:CC:A8": "Espressif",
    "7C:DF:A1": "Espressif", "A4:CF:12": "Espressif", "C8:C9:A3": "Espressif",
    "D8:F1:5B": "Espressif", "34:AB:95": "Espressif", "48:27:E2": "Espressif",
    "50:02:91": "Espressif", "B4:E6:2D": "Espressif", "CC:50:E3": "Espressif",
    "68:C6:3A": "Espressif", "EC:FA:BC": "Espressif", "8C:AA:B5": "Espressif",
    "18:FE:34": "Espressif", "5C:CF:7F": "Espressif", "60:01:94": "Espressif",
    "DC:4F:22": "Sonoff/ITEAD", "84:0D:8E": "Sonoff/ITEAD",
    "D8:BF:C0": "Tuya/涂鸦", "10:52:1C": "Tuya/涂鸦", "68:57:2D": "Tuya/涂鸦",
    "50:8A:06": "Tuya/涂鸦", "18:69:D8": "Tuya/涂鸦", "3C:61:05": "Tuya/涂鸦",
    "00:1E:C0": "Broadlink", "78:A0:04": "Broadlink", "34:EA:34": "Broadlink",

    "28:6C:07": "Xiaomi", "64:09:80": "Xiaomi", "78:11:DC": "Xiaomi", "50:EC:50": "Xiaomi",
    "8C:DE:52": "Yeelight", "04:CF:8C": "Yeelight", "68:AB:BC": "Yeelight",
    "00:1A:79": "Huawei", "EC:FA:5C": "Huawei", "5C:7D:5E": "Huawei",
    "AC:CF:23": "Hi-Flying(易微联模组)",
    "4C:CC:6A": "Sonos",
    "B8:27:EB": "Raspberry Pi", "DC:A6:32": "Raspberry Pi",
    "E4:5F:01": "Raspberry Pi", "D8:3A:DD": "Raspberry Pi",
    "44:17:93": "Shelly", "C4:5B:BE": "Shelly",

    "00:E0:4C": "Realtek", "18:C0:4D": "Giga-Byte", "1C:1B:0D": "Giga-Byte",
    "BC:24:11": "Proxmox/虚拟", "00:15:5D": "Hyper-V 虚拟网卡",
}


BLE_COMPANY = {
    0x004C: "Apple", 0x0006: "Microsoft", 0x0075: "Samsung", 0x00E0: "Google",
    0x0087: "Garmin", 0x0059: "Nordic", 0x02E5: "Espressif", 0x02E1: "Alibaba(阿里)",
    0x038F: "Xiaomi", 0x06A8: "Midea(美的)", 0x0157: "Anhui Huami",
    0x0078: "Bose", 0x008A: "Huawei", 0x01AB: "Xiaomi", 0x0171: "Amazon",
}


SVC_MEAN = {
    "0000fe95": "小米 MiBeacon", "0000fe9f": "Google Fast Pair",
    "0000feb0": "阿里/涂鸦系私有", "0000feb3": "MTK/涂鸦系私有",
    "0000fef6": "厂商私有", "0000fd6f": "接触者追踪",
    "0000180f": "电量服务", "0000180a": "设备信息服务", "00001800": "通用访问",
    "00001812": "HID(人机接口)", "0000180d": "心率", "0000fe59": "Nordic DFU",
    "0000fff0": "厂商私有", "0000fff1": "厂商私有", "0000fff2": "厂商私有",
}


MDNS_MEAN = {
    "_ewelink._tcp.local.": "易微联设备", "_tuya._tcp.local.": "涂鸦设备",
    "_miio._udp.local.": "小米 MiIO 设备", "_hap._tcp.local.": "HomeKit 设备",
    "_matter._tcp.local.": "Matter 设备", "_matterc._udp.local.": "Matter 待配网",
    "_googlecast._tcp.local.": "Chromecast/Google 设备", "_airplay._tcp.local.": "AirPlay 设备",
    "_raop._tcp.local.": "AirPlay 音频", "_spotify-connect._tcp.local.": "Spotify Connect",
    "_amzn-wplay._tcp.local.": "Amazon 设备", "_sleep-proxy._udp.local.": "Apple 睡眠代理",
    "_printer._tcp.local.": "打印机", "_ipp._tcp.local.": "网络打印机",
    "_smb._tcp.local.": "文件共享", "_workstation._tcp.local.": "工作站",
    "_http._tcp.local.": "HTTP 服务", "_device-info._tcp.local.": "设备信息",
    "_teamviewer._tcp.local.": "TeamViewer",
}


LOCAL_PORTS = [
    (55443, "Yeelight 局域网（开放协议，需在该 App 里开 LAN 控制）"),
    (9999, "TP-Link Kasa 局域网（开放协议）"),
    (8009, "Chromecast / Google Cast（开放协议）"),
    (6466, "Android TV Remote v2（需在电视上确认配对）"),
    (6053, "ESPHome（开放协议）"),
    (80, "HTTP：Tasmota / Shelly / ESPHome / 路由器管理页"),
    (443, "HTTPS：路由器 / 摄像头管理页"),
    (6445, "Midea 局域网（需一次性 token，来自美的云账号）"),
    (1900, "UPnP / DLNA（媒体设备）"),
    (56700, "LIFX（开放协议）"),
]

CONTROL_HINT = [
    (("yeelight",), "可本地控制（Yeelight 开放协议，需先在 App 开 LAN 控制）"),
    (("tasmota", "esphome", "shelly"), "可本地控制（开放 HTTP/MQTT，无需账号）"),
    (("kasa", "tp-link"), "可本地控制（Kasa 开放协议）"),
    (("chromecast", "google"), "可本地控制（Cast 协议）"),
    (("lifx",), "可本地控制（LIFX 开放协议）"),
    (("sonos",), "可本地控制（UPnP/SOAP）"),
    (("philips", "hue", "signify"), "可本地控制（Hue 桥，需按一下桥上的按钮配对）"),
    (("midea", "美的"), "需一次性 token（美的云账号取一次，之后全本地）"),
    (("haier", "海尔"), "需云端账号（海尔没公开局域网协议）"),
    (("gree", "格力"), "需云端账号或红外遥控"),
    (("hisense", "海信", "tcl", "skyworth"), "多数需云端账号；部分支持 DLNA 投屏"),
    (("xiaomi", "小米", "miio"), "需设备 token（米家 App 里取一次）"),
    (("tuya", "涂鸦"), "需 local_key（涂鸦云取一次），之后可全本地"),
    (("broadlink", "博联"), "需学习/配对（红外类）"),
    (("espressif", "bouffalo", "beken", "realtek semiconductor"),
     "是模组厂，具体看它跑什么固件；若是 Tasmota/ESPHome 则可直接本地控制"),
    (("apple",), "Apple 设备：AirPlay/HomeKit，HomeKit 需配对码"),
]

def control_hint(vendor, name=""):
    blob = ((vendor or "") + " " + (name or "")).lower()
    for keys, text in CONTROL_HINT:
        if any(k in blob for k in keys):
            return text
    return ""


PORT_HINT = {
    55443: "可本地控制（Yeelight 开放协议）",
    9999: "可本地控制（TP-Link Kasa 开放协议）",
    8009: "可本地控制（Google Cast）",
    6466: "可本地控制（Android TV Remote，需在电视上确认配对）",
    6053: "可本地控制（ESPHome，无需账号）",
    56700: "可本地控制（LIFX 开放协议）",
    6445: "需一次性 token（美的云账号，之后可全本地）",
    80: "有 Web 管理页 —— 是不是可控设备要看型号（路由器/摄像头/开关都可能）",
    443: "有 HTTPS 管理页 —— 同上，看型号",
    1900: "支持 UPnP/DLNA（媒体类设备可投屏/控制）",
}

def verdict_from_ports(hits):
    ranks = []
    for h in hits or []:
        v = PORT_HINT.get(h.get("port"))
        if v:
            ranks.append((0 if h["port"] not in (80, 443) else 1, v))
    if not ranks:
        return ""
    ranks.sort()
    return ranks[0][1]

def probe_local(ip, timeout=0.7, ports=None):
    hits = []
    for port, meaning in (ports or LOCAL_PORTS):
        s = socket.socket()
        s.settimeout(timeout)
        try:
            if s.connect_ex((ip, port)) == 0:
                hits.append({"port": port, "meaning": meaning})
        except Exception:
            pass
        finally:
            try:
                s.close()
            except Exception:
                pass
    return hits

def _norm_uuid(u):
    return (u or "").lower().replace("-", "")

def _norm_mac(mac):
    return (mac or "").upper().replace("-", "").replace(":", "").replace(".", "")

_OUI_DB = None
_OUI_PATH = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "..", "data", "oui.json"))

def _load_oui():
    global _OUI_DB
    if _OUI_DB is not None:
        return _OUI_DB
    _OUI_DB = {}
    try:
        import json
        with open(_OUI_PATH, encoding="utf-8") as f:
            _OUI_DB = json.load(f)
    except Exception:
        _OUI_DB = {}
    return _OUI_DB

def vendor_of(mac):
    m = _norm_mac(mac)
    if len(m) >= 6:
        db = _load_oui()
        if db:
            for n in (12, 9, 7, 6):
                if len(m) >= n and m[:n] in db:
                    return db[m[:n]]
    return OUI.get((mac or "").upper().replace("-", ":")[:8], "") or ""

def guess_kind(vendor, name="", companies=None, services=None):
    v = (vendor or "") + " " + (name or "") + " " + " ".join(companies or []) \
        + " " + " ".join(services or [])
    low = v.lower()
    K = [
        (("haier", "海尔"), "家电（海尔）"),
        (("midea", "美的"), "家电（美的）"),
        (("gree", "格力"), "家电（格力）"),
        (("hisense", "海信"), "家电（海信）"),
        (("tcl",), "家电（TCL）"),
        (("xiaomi", "miio", "mi beacon", "小米"), "小米生态设备"),
        (("tuya", "涂鸦", "sonoff", "itead", "espressif", "bouffalo", "beken",
          "realtek semiconductor", "hi-flying", "模组"), "智能家居 / IoT 设备（模组厂）"),
        (("apple",), "Apple 设备"),
        (("samsung",), "Samsung 设备"),
        (("huawei", "honor"), "华为/荣耀设备"),
        (("broadlink",), "博联（红外/遥控类）"),
        (("yeelight",), "Yeelight 灯具"),
        (("shelly",), "Shelly 智能开关"),
        (("sonos",), "Sonos 音箱"),
        (("raspberry",), "树莓派"),
        (("virtual", "hyper-v", "proxmox", "vmware", "qemu"), "虚拟网卡（不是实体设备）"),
        (("hewlett", "dell", "lenovo", "asustek", "giga-byte", "micro-star",
          "intel", "realtek"), "电脑/网卡"),
    ]
    for keys, label in K:
        if any(k in low for k in keys):
            return label
    return ""

def svc_meaning(uuids):
    out = []
    for u in uuids or []:
        k = _norm_uuid(u)[:8]
        if k in SVC_MEAN:
            out.append(SVC_MEAN[k])
    return sorted(set(out))


def scan_arp():
    try:
        r = subprocess.run(["arp", "-a"], capture_output=True, text=True,
                           encoding="gbk", errors="replace", timeout=60,
                           creationflags=CREATE_NO_WINDOW)
        out = r.stdout or ""
    except Exception:
        return []
    rows = []
    for l in out.splitlines():
        m = re.match(r"\s*(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F-]{17})\s+(\w+)", l)
        if not m:
            continue
        ip, mac = m.group(1), m.group(2).upper()
        if mac.startswith(("FF-FF", "01-00", "00-00")):
            continue
        rows.append({"ip": ip, "mac": mac, "vendor": vendor_of(mac)})
    return rows

def scan_ssdp(seconds=3):
    msg = ("M-SEARCH * HTTP/1.1\r\nHOST: 239.255.255.250:1900\r\n"
           "MAN: \"ssdp:discover\"\r\nMX: 2\r\nST: ssdp:all\r\n\r\n").encode()
    found = {}
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        s.settimeout(1.0)
        s.sendto(msg, ("239.255.255.250", 1900))
        t0 = time.time()
        while time.time() - t0 < seconds:
            try:
                data, addr = s.recvfrom(65507)
            except socket.timeout:
                continue
            t = data.decode("utf-8", "replace")
            srv = re.search(r"(?i)^SERVER:\s*(.+)$", t, re.M)
            usn = re.search(r"(?i)^USN:\s*(.+)$", t, re.M)
            if addr[0] not in found:
                found[addr[0]] = {"server": (srv.group(1).strip() if srv else ""),
                                  "usn": (usn.group(1).strip() if usn else "")}
        s.close()
    except Exception:
        pass
    return [dict(ip=k, **v) for k, v in found.items()]

MDNS_TYPES = list(MDNS_MEAN.keys()) + ["_services._dns-sd._udp.local."]

def scan_mdns(seconds=4):
    try:
        from zeroconf import Zeroconf, ServiceBrowser
    except Exception as e:
        return {"error": "zeroconf 未安装：%s" % e, "services": []}
    found = {}

    class H:
        def add_service(self, zc, type_, name):
            try:
                info = zc.get_service_info(type_, name, timeout=2000)
            except Exception:
                info = None
            addrs = info.parsed_addresses() if info else []
            label = name
            if type_ and label.endswith("." + type_):
                label = label[: -(len(type_) + 1)]
            found[name] = {"type": type_, "name": name, "label": label,
                           "addresses": list(addrs or []),
                           "server": (info.server if info else "") or "",
                           "port": (info.port if info else 0) or 0,
                           "meaning": MDNS_MEAN.get(type_, "")}

        def update_service(self, *a):
            pass

        def remove_service(self, *a):
            pass

    zc = Zeroconf()
    try:
        for t in MDNS_TYPES:
            try:
                ServiceBrowser(zc, t, H())
            except Exception:
                pass
        time.sleep(seconds)
    finally:
        try:
            zc.close()
        except Exception:
            pass

    svcs = [v for k, v in found.items() if v.get("addresses") or v.get("meaning")]
    return {"error": None, "services": svcs}

def scan_ble(seconds=6):
    try:
        import asyncio
        from bleak import BleakScanner
    except Exception as e:
        return {"error": "bleak 未安装：%s" % e, "devices": []}
    devs = {}

    def cb(device, adv):
        try:
            mf = adv.manufacturer_data or {}
            companies = []
            for k in list(mf.keys())[:3]:
                companies.append(BLE_COMPANY.get(k, "0x%04X" % k))
            svcs = list(adv.service_uuids or [])
            meaning = svc_meaning(svcs)
            nm = (device.name or "").strip()
            devs[device.address] = {
                "address": device.address, "name": nm,
                "rssi": adv.rssi, "companies": companies,
                "service_uuids": svcs[:6], "service_meaning": meaning,
                "kind": guess_kind(" ".join(companies), nm, companies, meaning)
                        or (guess_kind(vendor_of(device.address)) if vendor_of(device.address) else ""),
            }
        except Exception:
            devs[device.address] = {"address": device.address,
                                    "name": getattr(device, "name", "") or "",
                                    "rssi": getattr(adv, "rssi", None),
                                    "companies": [], "service_uuids": [], "service_meaning": []}

    async def go():
        sc = BleakScanner(detection_callback=cb)
        await sc.start()
        await asyncio.sleep(seconds)
        await sc.stop()

    try:
        asyncio.run(go())
    except Exception as e:
        return {"error": "BLE 扫描失败 %s: %s" % (type(e).__name__, str(e)[:120]), "devices": []}
    return {"error": None,
            "devices": sorted(devs.values(), key=lambda d: -(d.get("rssi") or -999))}


def scan(ble_seconds=6, mdns_seconds=4, ssdp_seconds=3,
         do_ble=True, do_mdns=True, do_ssdp=True, do_arp=True, probe=False):
    t0 = time.time()
    r = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "lan": [], "upnp": [],
         "mdns": {"error": None, "services": []}, "ble": {"error": None, "devices": []},
         "notes": []}
    try:
        import psutil
        for name, addrs in psutil.net_if_addrs().items():
            for a in addrs:
                if a.family == socket.AF_INET and not a.address.startswith(("127.", "169.254.")):
                    r["notes"].append("网卡 %s -> %s" % (name, a.address))
    except Exception:
        pass
    if do_arp:
        r["lan"] = scan_arp()

        if probe:
            for d in r["lan"]:
                d["ports"] = probe_local(d["ip"])
                d["control"] = (verdict_from_ports(d["ports"])
                                or control_hint(d.get("vendor", "")))
        else:
            for d in r["lan"]:
                d["control"] = control_hint(d.get("vendor", ""))
    if do_ssdp:
        r["upnp"] = scan_ssdp(ssdp_seconds)
    if do_mdns:
        r["mdns"] = scan_mdns(mdns_seconds)
    if do_ble:
        r["ble"] = scan_ble(ble_seconds)
    r["elapsed"] = round(time.time() - t0, 1)
    return r

def identify(addr):
    a = (addr or "").strip().upper()
    out = {"addr": a, "vendor": "", "kind": "", "hint": []}
    if not a:
        return out
    out["vendor"] = vendor_of(a)
    hx = _norm_mac(a)
    if len(hx) >= 2:
        try:


            if int(hx[1], 16) in (2, 6, 10, 14):
                out["randomized"] = True
        except Exception:
            pass
    if re.match(r"^([0-9A-F]{2}[:-]){5}[0-9A-F]{2}$", a):
        out["kind"] = "网卡/BLE 地址（MAC）"
    g = guess_kind(out["vendor"])
    if g:
        out["hint"].append("可能是：%s" % g)
    if "虚拟" in g:
        out["hint"].append("虚拟网卡不算附近设备")
    if out.get("randomized"):
        out["hint"].append("这是【本地管理地址/随机化 MAC】（U/L 位=1）——"
                           "手机和电脑为保护隐私会随机生成，"
                           "查不到厂牌是正常的、不代表设备异常")
    elif not out["vendor"]:
        db = _load_oui()
        if not db:
            out["hint"].append("本地 OUI 表还没抓（跑一次 tools\\fetch_oui.py），"
                               "内置小表也没命中 —— 我不猜")
        else:
            out["hint"].append("OUI 表里查不到这个前缀 —— 我不猜")
    return out

def _snapshot(ble_seconds=4):
    snap = {}
    for d in scan_arp():
        snap["lan:" + d["mac"]] = {"kind": "lan", "key": d["mac"], "ip": d["ip"],
                                   "name": d.get("vendor") or d["mac"]}
    b = scan_ble(ble_seconds)
    for d in b.get("devices", []):
        nm = d.get("name") or (d.get("companies") or [""])[0] or d["address"]
        snap["ble:" + d["address"]] = {"kind": "ble", "key": d["address"],
                                       "ip": "", "name": nm, "rssi": d.get("rssi")}
    return snap

def watch(seconds=20, ble_seconds=4, on_event=None):
    events = []
    first = _snapshot(ble_seconds)
    known = dict(first)
    if on_event:
        for k, v in first.items():
            on_event("seen", v)
    t0 = time.time()
    while time.time() - t0 < seconds:
        time.sleep(max(2, ble_seconds))
        cur = _snapshot(ble_seconds)
        for k, v in cur.items():
            if k not in known:
                ev = ("appear", v)
                events.append(ev)
                if on_event:
                    on_event(*ev)
        for k, v in known.items():
            if k not in cur:
                ev = ("leave", v)
                events.append(ev)
                if on_event:
                    on_event(*ev)
        known = cur
    return {"events": events, "baseline": list(first.values()),
            "elapsed": round(time.time() - t0, 1)}

def summary(r):
    L = []
    L.append("=== 附近设备（%s，耗时 %ss）===" % (r.get("ts", ""), r.get("elapsed", "?")))
    for n in r.get("notes", []):
        L.append("  " + n)
    lan = r.get("lan") or []
    L.append("【局域网】%d 台" % len(lan))
    for d in lan:
        L.append("   %-15s %-18s %s" % (d["ip"], d["mac"], d.get("vendor") or ""))
        if d.get("ports"):
            for h in d["ports"]:
                L.append("        端口 %-5d 开  %s" % (h["port"], h["meaning"]))
        if d.get("control"):
            L.append("        → %s" % d["control"])
    up = r.get("upnp") or []
    L.append("【UPnP/SSDP】%d 台" % len(up))
    for d in up:
        L.append("   %-15s %s" % (d["ip"], (d.get("server") or "")[:64]))
    m = r.get("mdns") or {}
    if m.get("error"):
        L.append("【mDNS】不可用：%s" % m["error"])
    else:
        svcs = m.get("services") or []
        L.append("【mDNS】%d 条" % len(svcs))
        for s in svcs[:12]:


            _nm = s.get("label") or ""
            if not _nm or _nm.isdigit():
                _nm = (s.get("type") or "").strip(".")
            L.append("   %-30s %-20s %s:%s" % (_nm[:30],
                                               s.get("meaning") or s.get("type", ""),
                                               ",".join(s.get("addresses") or [])[:20], s.get("port")))
    b = r.get("ble") or {}
    if b.get("error"):
        L.append("【BLE】不可用：%s" % b["error"])
    else:
        ds = b.get("devices") or []
        L.append("【BLE 蓝牙广播】%d 个" % len(ds))
        for d in ds[:20]:
            tag = " ".join([x for x in [(d.get("name") or ""),
                                        d.get("kind") or "",
                                        ",".join(d.get("companies") or []),
                                        ",".join(d.get("service_meaning") or [])] if x])
            ch = control_hint("", (d.get("name") or "") + " " + " ".join(d.get("companies") or []))
            L.append("   %-20s %5s dBm  %s" % (d["address"], d.get("rssi"), tag[:64]))
            if ch:
                L.append("        → %s" % ch)
    return "\n".join(L)

if __name__ == "__main__":
    sec = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    res = scan(ble_seconds=sec)
    print(summary(res))
