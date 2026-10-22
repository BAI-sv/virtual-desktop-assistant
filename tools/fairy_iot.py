"""Fairy 设备自动发现与连接中心（★ 2026-10-11 新增）。

像华为智慧生活一样：自动扫描新设备 -> 自动记录 -> 自动连接。
扫描通道：
  1. 局域网（ARP/ping）—— 智能家居 / 电脑 / 手机 / 树莓派等
  2. 蓝牙 BLE（bleak）—— 温湿度计 / 人体传感器 / 蓝牙设备
  3. mDNS 服务发现（zeroconf）—— 米家 / HomeKit / ESPHome / 打印机
  4. 串口（pyserial）—— Arduino / 传感器板 / USB 转串口
  5. 复用 device_adapters 的既有适配器（Nearby / 蓝牙音频 / 星闪）
自动连接：扫描到的设备写入 fairy.json 的 devices 段，标记在线状态，
          之后 Fairy 可通过统一接口查询传感器状态 / 执行动作（后期扩展）。
用法：
    py fairy_iot.py --scan          # 全通道扫描
    py fairy_iot.py --scan lan      # 只扫局域网
    py fairy_iot.py --scan bt       # 只扫蓝牙
    py fairy_iot.py --connect       # 扫描并自动连接（记录）
    py fairy_iot.py --devices       # 列出已记录设备
    py fairy_iot.py --json          # JSON 输出
"""
import argparse
import json
import os
import subprocess
# ★ 2026-10-12【不弹黑窗】扫描调用的 ipconfig/arp/ping 一律隐藏控制台窗口
_NW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
import sys
import time

FAIRY_JSON = r"D:\FAIRY\fairy.json"
_SELF = os.path.dirname(os.path.abspath(__file__))

VENDOR_OUI = [
    ("华为", ["28-6E-D4", "F4-9C-34", "94-29-37", "AC-8E-D7", "64-16-66",
              "18-A9-05", "58-11-22", "60-DE-44", "34-2C-02"]),
    ("小米", ["A4-49-76", "54-13-79", "64-09-80", "8C-6E-8E", "28-6C-07",
              "2C-C5-BE", "F0-B4-29"]),
    ("苹果", ["A4-83-E7", "AC-BC-32", "F0-18-98", "3C-22-FB", "88-66-5A",
              "D0-E1-40", "E8-B7-48"]),
    ("TP-Link", ["50-C7-BF", "C8-3A-35", "74-DA-38", "A0-63-91", "14-13-9B"]),
    ("联想", ["3C-F8-DF", "28-D2-44", "F4-71-58"]),
    ("树莓派", ["B8-27-EB", "DC-A6-32", "E4-5F-01", "28-CD-C1"]),
    ("三星", ["C8-5A-CF", "30-92-FB", "5C-F9-DD"]),
    ("vivo", ["18-26-3E", "20-1A-06", "02-00-00"]),
    ("OPPO", ["48-59-29", "7C-5C-F8"]),
    ("中兴", ["44-33-4C", "00-19-C0"]),
    ("海康", ["44-19-B6", "A0-06-03", "3C-A5-0B"]),
    ("大疆", ["60-60-1F", "00-00-2A"]),
    ("魅族", ["A4-77-33", "8C-F6-93"]),
    ("360", ["D8-5D-4C", "5C-87-9C"]),
]

# ★ 2026-10-18【BLE 广播里的厂商 ID（Bluetooth SIG Company Identifier）】
#   这是比 MAC OUI 更准的品牌来源：BLE 设备普遍用随机 MAC（隐私地址），
#   OUI 根本查不出来，但广播包里几乎都带 manufacturer data，前两个字节就是厂商 ID。
#   ★ 只列【有把握】的那几个；不确定的一律不猜（宁可显示"未知"也不报错品牌）。
BLE_COMPANY = {
    0x004C: "苹果",
    0x0006: "微软",
    0x0075: "三星",
    0x00E0: "谷歌",
    0x0157: "小米",
    0x027D: "华为",
    0x0087: "佳明",
    0x0059: "Nordic",
    0x0499: "瑞昱",
}

# ★ BLE 标准服务 UUID（用来判类型：这个比名字可靠得多）
BLE_SERVICE_HINTS = [
    # (类型, [uuid 片段])
    ("键鼠/输入设备", ["1812"]),          # HID
    ("蓝牙耳机", ["110b", "110a", "1108"]),  # A2DP / Audio Sink / Headset
    ("智能灯", ["fe07", "fe95", "fee7"]),   # 小米 MiBeacon / Yeelight 常见
    ("温湿度计", ["181a"]),               # Environmental Sensing
    ("电量传感器", ["180f"]),             # Battery Service
    ("心率设备", ["180d"]),               # Heart Rate
    ("苹果配件", ["fe9f", "fd6f"]),        # Fast Pair / Exposure
]

NAME_HINTS = [
    ("智能音箱", ["音响", "音箱", "speaker", "sound", "play", "soundbar"]),
    ("蓝牙耳机", ["耳机", "ear", "headset", "buds", "earbuds", "mi-", "airpods",
                  "freebuds", "wh-", "wf-", "redmi buds", "enco"]),
    ("手表/手环", ["手表", "手环", "watch", "band", "mi band", "amazfit", "gt "]),
    ("温湿度计", ["温度", "湿度", "temp", "humi", "thermo", "th-", "sensor"]),
    ("智能灯", ["灯", "light", "bulb", "lamp", "yeelight", "philips hue", "hue"]),
    ("打印机", ["打印", "print", "hp ", "canon ", "epson", "brother"]),
    ("路由器", ["路由", "router", "wifi", "xiaomi.r", "tp-link", "tplink",
                "asus", "netgear", "honor router"]),
    ("电视", ["电视", "tv", "mi tv", "hisense", "tcl", "skyworth", "changhong"]),
    ("摄像头", ["摄像", "camera", "cam", "ipc", "arlo", "tapo", "ezviz", "imou"]),
    ("门锁", ["锁", "lock", "door", "aqara", "kaadas", "desman"]),
    ("空调", ["空调", "air", "ac-", "gree", "midea", "haier", "aux ", "tcl ac"]),
    ("扫地机", ["扫地", "robot", "roborock", "viomi", "dreame", "ecovacs",
                "deebot", "tichello"]),
    ("插座", ["插座", "plug", "socket", "pdu", "gosund", "meross", "tapo p"]),
    ("体脂秤", ["秤", "scale", "mi scale", "yunmai"]),
    ("音箱/功放", ["amp", "receiver", "denon", "yamaha", "bose", "jbl", "sonos"]),
    ("手机/平板", ["phone", "iphone", "ipad", "pixel", "galaxy", "redmi",
                   "honor", "nova", "mate"]),
    ("电脑", ["pc-", "laptop", "thinkpad", "macbook", "desktop", "surface"]),
]


def recognize(dev):
    """自动识别设备：填 vendor（厂商）和 type（设备类型）。

    ★★ 2026-10-18【修「0 个厂商、93% 未知」】
      旧实现只读 dev["mac"]，但 BLE / 串口设备的地址在 dev["id"] 里
      （形如 ble:C8:0A:35:5B:F2:3D），所以 44 个 BLE 设备**一个厂商都认不出来**；
      而且 BLE 大量用随机 MAC（F1:22:33:…），OUI 本来也查不到 ——
      正路是看广播里的 manufacturer data（厂商 ID）和 service UUID。
    """
    dev = dict(dev)
    name = (dev.get("name") or "").lower()

    # ---- 取出 MAC（mac 字段优先，其次 id；去掉 ble:/lan: 之类的前缀）----
    raw = str(dev.get("mac") or dev.get("id") or "").upper()
    for p in ("BLE:", "LAN:", "NEARLINK:", "NEARBY:", "SERIAL:", "MDNS:"):
        raw = raw.replace(p, "")
    mac = raw.replace("-", "").replace(":", "").replace(".", "")

    # ---- 1) 厂商：先看 BLE 广播里的 manufacturer id（最准）----
    vendor = None
    mf = dev.get("manufacturer_id")
    try:
        if mf is not None:
            vendor = BLE_COMPANY.get(int(mf))
    except Exception:
        vendor = None
    # ---- 2) 退回 MAC OUI ----
    if not vendor and len(mac) >= 6:
        for v, ouis in VENDOR_OUI:
            hit = False
            for o in ouis:
                oo = o.upper().replace("-", "").replace(":", "")
                if oo and mac.startswith(oo):
                    vendor = v
                    hit = True
                    break
            if hit:
                break

    # ---- 3) 类型：service UUID > SSDP 设备类型 > 名字关键词 > 通道兜底 ----
    dtype = None
    uuids = [str(x).lower() for x in (dev.get("service_uuids") or [])]
    if uuids:
        for t, frags in BLE_SERVICE_HINTS:
            if any(f in u for f in frags for u in uuids):
                dtype = t
                break
    # ★ 2026-10-18：SSDP 回出来的 ST 是最硬的类型证据（比名字靠谱）
    if dtype is None:
        _ssdp = (str(dev.get("ssdp_st") or "") + " " + str(dev.get("ssdp_server") or "")).lower()
        if _ssdp.strip():
            _SSDP_TYPE = [
                ("路由器", ["internetgatewaydevice", "wanconnectiondevice", "wlanaccessdevice"]),
                ("打印机", ["printer", "printbasic"]),
                ("电视", ["mediarenderer", "tvdevice", "dial"]),
                ("音箱/功放", ["sonos", "mediaserver", "avtransport", "contentdirectory"]),
                ("摄像头", ["camera", "onvif"]),
                ("NAS/存储", ["mediaserver", "networkattachedstorage", "fileserver"]),
                ("投屏设备", ["googlecast", "airplay", "dial"]),
                ("智能家居中枢", ["basic:1", "homeassistant"]),
            ]
            for _t, _keys in _SSDP_TYPE:
                if any(_k in _ssdp for _k in _keys):
                    dtype = _t
                    break
    if dtype is None:
        for t, keys in NAME_HINTS:
            if any(k in name for k in keys):
                dtype = t
                break
    if dtype is None and dev.get("kind") == "mdns":
        t = (dev.get("type") or "")
        if "hap" in t:
            dtype = "苹果智能家居"
        elif "esphome" in t:
            dtype = "ESPHome 传感器"
        elif "ipp" in t:
            dtype = "打印机"
        elif "airplay" in t:
            dtype = "苹果投屏设备"
        elif "googlecast" in t:
            dtype = "投屏设备"
    if dtype is None and dev.get("kind") == "serial":
        dtype = "串口设备（传感器/开发板）"
    if dtype is None and vendor:
        dtype = "网络设备"
    if dtype is None:
        dtype = "未知设备"
    dev["vendor"] = vendor
    dev["type"] = dtype
    return dev



def _load_cfg():
    try:
        return json.load(open(FAIRY_JSON, encoding="utf-8"))
    except Exception:
        return {}


def _save_cfg(c):
    json.dump(c, open(FAIRY_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def _own_ips():
    """本机 IPv4 地址列表。"""
    ips = []
    try:
        r = subprocess.run(["ipconfig"], capture_output=True, text=True,
                           encoding="gbk", errors="replace", timeout=10,
                           creationflags=_NW)
        for line in (r.stdout or "").splitlines():
            if "IPv4" in line or "IP Address" in line:
                for part in line.split(":"):
                    p = part.strip()
                    if p.count(".") == 3 and p.replace(".", "").isdigit():
                        ips.append(p)
    except Exception:
        pass
    return ips


def scan_lan(timeout=0.3):
    """局域网设备扫描：ping 探测 + ARP 表读 MAC，识别在线设备。

    思路：对本机网段逐个 ping（1 秒内并行受限，取前 254 个），
    再读 arp -a 表拿到 MAC 地址，厂商前缀可反查（常见智能设备前缀硬编码）。
    """
    out = []
    ips = _own_ips()
    if not ips:
        return out
    for ip in ips:
        parts = ip.split(".")
        base = ".".join(parts[:3])
        # 快速 ping 本网段 .1~.254（每个 0.3s，串行约 76s -> 拆并发）
        procs = []
        for i in range(1, 255):
            target = "%s.%d" % (base, i)
            p = subprocess.Popen(["ping", "-n", "1", "-w", "300", target],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 creationflags=_NW)
            procs.append((target, p))
        for target, p in procs:
            try:
                p.wait(timeout=1.5)
            except Exception:
                p.kill()
        # 读 ARP 表
        try:
            r = subprocess.run(["arp", "-a"], capture_output=True, text=True,
                               encoding="gbk", errors="replace", timeout=10,
                               creationflags=_NW)
        except Exception:
            continue
        for line in (r.stdout or "").splitlines():
            if "." not in line:
                continue
            toks = line.split()
            if len(toks) >= 3 and toks[0].count(".") == 3:
                ip, mac = toks[0], toks[1]
                if ip.startswith(base + ".") and not ip.startswith(("224.", "239.", "0.")):
                    out.append(recognize({"id": ip, "name": ip, "kind": "lan",
                                          "online": True, "mac": mac.upper(),
                                          "adapter": "lan"}))
    return out


def scan_bt(timeout=8):
    """蓝牙 BLE 扫描（bleak）：发现蓝牙设备 / 传感器。

    ★★ 2026-10-18【识别率的关键】原来用 `BleakScanner.discover()` ——
      它**丢掉广播数据**，只剩地址和名字；而 BLE 设备大量用随机 MAC（隐私地址），
      光看地址什么都认不出，所以 44 个蓝牙设备全成了"未知设备"。
      现在用 `discover(return_adv=True)` 把广播里的
      manufacturer_data（厂商 ID）、service_uuids（服务类型）、local_name 一起收下来，
      recognize() 就能据此判品牌/类型。
    """
    out = []
    try:
        import asyncio
        from bleak import BleakScanner

        def _do():
            async def _scan():
                try:
                    # bleak 3.x：return_adv=True 返回 {地址: (BLEDevice, AdvertisementData)}
                    return await BleakScanner.discover(timeout=timeout, return_adv=True)
                except TypeError:
                    # 老版 bleak 没有这个关键字：退回旧写法（至少还能拿到地址/名字）
                    d = await BleakScanner.discover(timeout=timeout)
                    return {getattr(x, "address", str(i)): (x, None) for i, x in enumerate(d)}
            return asyncio.run(_scan())

        found = _do() or {}
        items = found.values() if isinstance(found, dict) else found
        for pair in items:
            try:
                d, adv = pair if isinstance(pair, (tuple, list)) and len(pair) == 2 else (pair, None)
            except Exception:
                continue
            try:
                addr = getattr(d, "address", None) or (d.get("address") if isinstance(d, dict) else "")
                nm = getattr(d, "name", None) or (adv.local_name if adv is not None else None) \
                    or (d.get("name") if isinstance(d, dict) else None) or addr
                rec = {"id": "ble:%s" % addr, "name": nm, "kind": "ble",
                       "online": True, "rssi": getattr(d, "rssi", None), "adapter": "bt"}
                if adv is not None:
                    # manufacturer data 的 key 就是厂商 ID（小端解码前 2 字节）
                    mfd = getattr(adv, "manufacturer_data", None) or {}
                    if isinstance(mfd, dict) and mfd:
                        try:
                            rec["manufacturer_id"] = int(list(mfd.keys())[0])
                        except Exception:
                            pass
                    su = getattr(adv, "service_uuids", None) or []
                    if su:
                        rec["service_uuids"] = [str(x) for x in su[:12]]
                    if getattr(adv, "local_name", None):
                        rec["local_name"] = adv.local_name
                out.append(recognize(rec))
            except Exception:
                continue
    except Exception as e:
        out.append({"adapter": "bt", "error": str(e)[:100]})
    return out


def scan_mdns(timeout=4):
    """mDNS 服务发现（zeroconf）：米家 / HomeKit / ESPHome / 打印机等。"""
    out = []
    try:
        from zeroconf import ServiceBrowser, Zeroconf
        found = {}

        class _Listener:
            def add_service(self, zc, typ, name):
                try:
                    info = zc.get_service_info(typ, name)
                    if info and info.addresses:
                        addr = ".".join(str(b) for b in info.addresses[0])
                        found[name] = recognize({"id": name, "name": name.split(".")[0],
                                                  "kind": "mdns", "online": True,
                                                  "ip": addr, "type": typ, "adapter": "mdns"})
                except Exception:
                    pass

        zc = Zeroconf()
        listener = _Listener()
        types = ["_http._tcp.local.", "_hap._tcp.local.", "_esphomelib._tcp.local.",
                 "_ipp._tcp.local.", "_airplay._tcp.local.", "_googlecast._tcp.local.",
                 "_workstation._tcp.local."]
        browsers = [ServiceBrowser(zc, t, listener) for t in types]
        time.sleep(timeout)
        zc.close()
        out = list(found.values())
    except Exception as e:
        out.append({"adapter": "mdns", "error": str(e)[:100]})
    return out


def scan_serial():
    """串口扫描（pyserial）：传感器板 / Arduino / USB 转串口。"""
    out = []
    try:
        import serial.tools.list_ports
        for p in serial.tools.list_ports.comports():
            out.append(recognize({"id": p.device, "name": p.description or p.device,
                                  "kind": "serial", "online": True, "adapter": "serial"}))
    except Exception as e:
        out.append({"adapter": "serial", "error": str(e)[:100]})
    return out


def _norm_key(d):
    """★ 2026-10-12【去重修复】同 MAC 会被扫到两次（scan_bt 无前缀 +
    nearby 带 ble:/lan: 前缀），统一去前缀大写做匹配键，避免重复/误判新设备。"""
    _i = str(d.get("id") or "").lower().strip()
    for _p in ("ble:", "lan:", "nearlink:", "nearby:", "serial:"):
        if _i.startswith(_p):
            _i = _i[len(_p):]
    return _i


def _is_mac_like(s):
    """名字是不是一串 MAC / 地址（BLE 没广播 local_name 时，name 会被填成地址）。"""
    import re
    t = str(s or "").strip()
    if not t:
        return True
    return bool(re.fullmatch(r"(?i)[0-9a-f]{2}([:\-][0-9a-f]{2}){5}", t))


def scan_ssdp(timeout=3):
    """★ 2026-10-18【新增】UPnP/SSDP 探测 —— 专治"局域网设备只剩一个 IP"。

    局域网里绝大多数智能设备（路由器/电视/音箱/打印机/NAS/摄像头）都会响应
    SSDP 的 M-SEARCH，并在回应里带上：
        SERVER:  含厂商标识和型号（如 Linux/4.4 UPnP/1.0 Sonos/57.0-...）
        ST:      设备类型（MediaRenderer / InternetGatewayDevice / printer ...）
        LOCATION: 设备描述文件地址（再抓一次能拿到 friendlyName / manufacturer）
    这比"扫到个 IP 但不知道是什么"有用得多 —— 也是把 32 个局域网设备
    从"未知"里捞出来的主要手段。
    """
    import socket
    import time as _t

    msg = (
        "M-SEARCH * HTTP/1.1\r\n"
        "HOST: 239.255.255.250:1900\r\n"
        'MAN: "ssdp:discover"\r\n'
        "MX: 2\r\n"
        "ST: ssdp:all\r\n\r\n"
    ).encode("ascii", "ignore")
    seen = {}
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(0.5)
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        except Exception:
            pass
        sock.sendto(msg, ("239.255.255.250", 1900))
        t0 = _t.time()
        while _t.time() - t0 < timeout:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except Exception:
                break
            ip = addr[0]
            txt = data.decode("utf-8", "replace")
            hdr = {}
            for ln in txt.splitlines():
                if ":" in ln:
                    k, _, v = ln.partition(":")
                    hdr[k.strip().upper()] = v.strip()
            rec = seen.get(ip)
            if rec is None:
                seen[ip] = {
                    "id": "lan:%s" % ip, "name": (hdr.get("SERVER") or ip),
                    "kind": "lan", "online": True, "adapter": "ssdp",
                    "ssdp_st": hdr.get("ST") or "",
                    "ssdp_server": hdr.get("SERVER") or "",
                    "ssdp_location": hdr.get("LOCATION") or "",
                }
            else:
                # 同一台设备会回多条（每种 ST 一条）：把类型和服务信息合并起来
                st = hdr.get("ST") or ""
                if st and st not in (rec.get("ssdp_st") or ""):
                    rec["ssdp_st"] = ((rec.get("ssdp_st") or "") + " | " + st).strip(" |")
                if not rec.get("ssdp_server") and hdr.get("SERVER"):
                    rec["ssdp_server"] = hdr["SERVER"]
    except Exception as e:
        return [{"adapter": "ssdp", "error": str(e)[:100]}]
    finally:
        try:
            if sock:
                sock.close()
        except Exception:
            pass
    # ★ 2026-10-18：SSDP 拿到的 ST/SERVER 是判断设备类型的硬证据，
    #   一定要过一遍 recognize()，否则进来的还是裸记录（type 为空）。
    return [recognize(x) for x in seen.values()]


def scan_all(progress=None):
    """全通道扫描（并行线程）。
    progress: 可选回调 fn(阶段名, 百分比, 已发现数)，每完成一个通道调一次。"""
    import threading
    results = {}
    _done = {"n": 0}

    def _run(key, fn, st):
        results[key] = fn()
        _done["n"] += 1
        if progress:
            _n = sum(1 for x in (results.get(key) or [])
                     if isinstance(x, dict) and "error" not in x)
            try:
                progress(st, int(_done["n"] / 4 * 100), _n)
            except Exception:
                pass

    threads = [
        threading.Thread(target=_run, args=("lan", scan_lan, "局域网")),
        threading.Thread(target=_run, args=("bt", scan_bt, "蓝牙")),
        threading.Thread(target=_run, args=("mdns", scan_mdns, "智能设备/投屏")),
        threading.Thread(target=_run, args=("serial", scan_serial, "串口设备")),
        # ★ 2026-10-18：SSDP/UPnP 独立一路 —— 局域网里大量设备只回 SSDP，
        #   能拿到型号和设备类型，是把"只剩一个 IP"的设备认出来的关键。
        threading.Thread(target=_run, args=("ssdp", scan_ssdp, "UPnP 设备")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    extra = []
    try:
        sys.path.insert(0, _SELF)
        import device_adapters as _da
        for d in _da.discover_all():
            if "error" in d:
                continue
            if d.get("adapter") in ("nearlink", "nearby"):
                d = recognize(d)
                d.setdefault("kind", d.get("adapter", "nearlink"))
                extra.append(d)
    except Exception:
        pass
    if progress:
        try:
            progress("收尾整理", 100, len(extra))
        except Exception:
            pass
    all_devices = []
    seen = {}

    for k in ("lan", "bt", "mdns", "serial", "ssdp", "extra"):
        for d in results.get(k) or (extra if k == "extra" else []):
            if isinstance(d, dict) and "error" not in d and d.get("id"):
                _k = _norm_key(d)
                if _k not in seen:
                    seen[_k] = len(all_devices)
                    all_devices.append(d)
                else:
                    # ★ 2026-10-18：同一台设备可能被多个通道扫到（比如 SSDP 和
                    #   纯 IP 扫描都命中同一个 192.168.x.x），这时候要把信息**合并**，
                    #   而不是像以前那样只在"旧条目没 rssi"时才替换 ——
                    #   否则 SSDP 拿到的型号/类型会被先到的纯 IP 记录顶掉。
                    _old = all_devices[seen[_k]]
                    if not _old.get("rssi") and d.get("rssi"):
                        all_devices[seen[_k]] = d
                    else:
                        _is_mac = _is_mac_like(_old.get("name"))
                        for _k2, _v2 in d.items():
                            if _v2 in (None, "", [], {}):
                                continue
                            if _k2 == "name" and not _is_mac:
                                continue          # 旧名字有意义就别覆盖
                            _old.setdefault(_k2, _v2)
                        # SSDP 的 server/type 信息优先（更具体）
                        for _k3 in ("ssdp_server", "ssdp_st", "ssdp_location"):
                            if d.get(_k3) and not _old.get(_k3):
                                _old[_k3] = d[_k3]
                        all_devices[seen[_k]] = recognize(_old)
    return all_devices


def _ask_confirm(devs):
    """★ 2026-10-11【连接授权】弹窗让使用者确认是否连接新设备。"""
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        names = "\n".join("%s（%s）" % (d.get("name") or d.get("id"),
                          d.get("type") or d.get("kind") or "?") for d in devs[:10])
        more = "" if len(devs) <= 10 else "\n…等 %d 个" % len(devs)
        ok = messagebox.askyesno(
            "Fairy 设备连接确认",
            "扫描到 %d 个新设备：\n%s%s\n\n是否自动连接这些设备？\n（拒绝后可在右键菜单再扫描）"
            % (len(devs), names, more))
        root.destroy()
        return ok
    except Exception:
        return True


def set_tag(dev_id, tag=None):
    """★ 2026-10-11【使用者标记】给已连接设备标记名称/用途。

    tag 为空时弹窗输入（使用者说"这是什么设备"）。
    """
    cfg = _load_cfg()
    devs = cfg.get("devices") or []
    hit = next((d for d in devs if d["id"] == dev_id or _norm_key(d) == _norm_key({"id": dev_id})), None)
    if not hit:
        return False, "没找到设备 %s（请先扫描连接）" % dev_id
    if not tag:
        try:
            import tkinter as tk
            import tkinter.simpledialog as _sd
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            tag = _sd.askstring(
                "标记设备",
                "给「%s」\n（识别为：%s%s）\n\n起个名字或说明：\n例如：卧室小米温湿度计"
                % (hit.get("name") or hit.get("id"),
                   hit.get("type") or hit.get("kind") or "未知",
                   " / " + hit.get("vendor") if hit.get("vendor") else ""),
                parent=root)
            root.destroy()
        except Exception:
            pass
    if not tag or not tag.strip():
        return False, "已取消标记"
    hit["tag"] = tag.strip()
    _save_cfg(cfg)
    return True, tag


def auto_connect(ask=True, progress=None):
    """扫描并连接新设备。

    ask=True：发现新设备时弹窗让使用者确认（默认，尊重使用者授权）；
    ask=False：静默连接（--yes 显式跳过询问）。
    progress: 可选回调 fn(阶段名, 百分比, 已发现数)，透传给 scan_all。
    返回 (devices, new_devs, confirmed)。
    """
    devs = scan_all(progress=progress)
    cfg = _load_cfg()
    known = {_norm_key(d): d for d in (cfg.get("devices") or [])}
    new = [d for d in devs if _norm_key(d) not in known]
    if ask and new:
        if not _ask_confirm(new):
            return (cfg.get("devices") or []), new, False
    for d in devs:
        _k = _norm_key(d)
        if _k in known:
            # ★★ 2026-10-18【修「重新扫描永远不更新识别结果」】
            #   这里原来只 update 了 online / last_seen ——
            #   新扫描拿到的 manufacturer_id（BLE 厂商 ID）、service_uuids、
            #   以及据此重新算出来的 vendor / type **全部被丢掉**。
            #   于是第一次扫成"未知设备"的，后面扫多少次都还是"未知设备"。
            #   现在：新数据的身份字段覆盖旧的（以最新一次扫描为准），
            #   但【用户起过的名字 tag】和旧的"有意义的 name"要保住。
            _old = known[_k]
            _keep_tag = _old.get("tag")
            _old_name = str(_old.get("name") or "").strip()
            _new_name = str(d.get("name") or "").strip()
            _old.update(d)
            # 新名字只是一串 MAC 就别覆盖旧的好名字
            if _old_name and (not _new_name or _is_mac_like(_new_name)):
                _old["name"] = _old_name
            if _keep_tag:
                _old["tag"] = _keep_tag
            _old.update({"online": True, "last_seen": time.time()})
            known[_k] = _old
        else:
            d["last_seen"] = time.time()
            d["connected"] = True
            known[_k] = d
    cfg["devices"] = list(known.values())
    _save_cfg(cfg)
    return cfg["devices"], new, True


def list_devices():
    cfg = _load_cfg()
    return cfg.get("devices") or []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", nargs="?", const="all", choices=["all", "lan", "bt", "mdns", "serial"],
                    help="扫描设备（all=全通道，可指定单一通道）")
    ap.add_argument("--connect", action="store_true", help="扫描并连接（发现新设备会弹窗让使用者确认）")
    ap.add_argument("--yes", action="store_true", help="跳过确认弹窗，直接连接（需配合 --connect）")
    ap.add_argument("--tag", nargs="+", metavar=("ID", "名字"),
                    help="给设备标记名称/用途（名字可省略，省略时弹窗输入）")
    ap.add_argument("--devices", action="store_true", help="列出已连接设备")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--progress", action="store_true", help="扫描过程逐行输出进度 P|阶段|百分比|已发现数")
    a = ap.parse_args()

    try:
        if a.tag:
            ok, msg = set_tag(a.tag[0], a.tag[1] if len(a.tag) > 1 else None)
            if a.json:
                print(json.dumps({"ok": ok, "msg": msg}, ensure_ascii=False))
            else:
                print(msg if ok else "标记失败：%s" % msg)
            return 0 if ok else 1
        if a.connect:
            def _pb(_st, _p, _n):
                print("P|%s|%d|%d" % (_st, _p, _n), flush=True)
            devs, new, confirmed = auto_connect(ask=not a.yes,
                                                progress=_pb if a.progress else None)
            if a.json:
                print(json.dumps({"ok": True, "count": len(devs), "new": len(new),
                                  "confirmed": confirmed, "devices": devs,
                                  "new_devs": new}, ensure_ascii=False))
            else:
                if new and not confirmed:
                    print("已取消连接（%d 个新设备未连接，扫描结果见上）" % len(new))
                elif new:
                    print("已连接 %d 个新设备，共 %d 个设备在线" % (len(new), len(devs)))
                else:
                    print("没有新设备，当前 %d 个设备在线" % len(devs))
        elif a.devices:
            devs = list_devices()
            if a.json:
                print(json.dumps({"ok": True, "count": len(devs), "devices": devs},
                                 ensure_ascii=False))
            else:
                if not devs:
                    print("还没有已连接设备")
                for d in devs:
                    print("%s  %s  %s  %s" % (d.get("id", "?"), d.get("name", "?"),
                                              d.get("kind", "?"), d.get("adapter", "?")))
        elif a.scan:
            def _pb2(_st, _p, _n):
                print("P|%s|%d|%d" % (_st, _p, _n), flush=True)
            fn = {"all": (lambda: scan_all(_pb2 if a.progress else None)),
                  "lan": scan_lan, "bt": scan_bt,
                  "mdns": scan_mdns, "serial": scan_serial}[a.scan]
            devs = fn()
            # ★ 2026-10-12【单窗扫描】--scan 也返回 new_devs（对照已记录设备），
            #   球端进度窗直接显示确认，不再另弹连接确认窗。
            _cfg0 = _load_cfg()
            _known = {_norm_key(d) for d in (_cfg0.get("devices") or [])}
            _new = [d for d in devs if _norm_key(d) not in _known]
            if a.json:
                print(json.dumps({"ok": True, "count": len(devs), "devices": devs,
                                  "new_count": len(_new), "new_devs": _new},
                                 ensure_ascii=False))
            else:
                if not devs:
                    print("没发现设备")
                for d in devs:
                    print("%s  %s  %s  %s" % (d.get("id", "?"), d.get("name", "?"),
                                              d.get("kind", "?"), d.get("adapter", "?")))
        else:
            ap.print_help()
            return 0
        return 0
    except Exception as e:
        if a.json:
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        else:
            print("扫描失败: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
