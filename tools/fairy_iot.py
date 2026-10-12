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
import sys
import time

_SELF = os.path.dirname(os.path.abspath(__file__))
FAIRY_JSON = os.environ.get("FAIRY_JSON", os.path.join(os.path.dirname(_SELF), "fairy.json"))

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

NAME_HINTS = [
    ("智能音箱", ["音响", "音箱", "speaker", "sound", "play", "soundbar"]),
    ("蓝牙耳机", ["耳机", "ear", "headset", "buds", "earbuds", "mi-"]),
    ("手表/手环", ["手表", "手环", "watch", "band", "手环"]),
    ("温湿度计", ["温度", "湿度", "temp", "humi", "thermo", "th-"]),
    ("智能灯", ["灯", "light", "bulb", "lamp", "yeelight"]),
    ("打印机", ["打印", "print", "hp ", "canon ", "epson"]),
    ("路由器", ["路由", "router", "wifi", "xiaomi.r", "tp-link"]),
    ("电视", ["电视", "tv", "mi tv", "hisense", "tcl"]),
    ("摄像头", ["摄像", "camera", "cam", "ipc"]),
    ("门锁", ["锁", "lock", "door"]),
    ("空调", ["空调", "air", "ac-", "gree", "midea"]),
    ("扫地机", ["扫地", "robot", "roborock", "viomi"]),
    ("插座", ["插座", "plug", "socket", "pdu"]),
]


def recognize(dev):
    """自动识别设备：填 vendor（厂商）和 type（设备类型）。"""
    dev = dict(dev)
    name = (dev.get("name") or "").lower()
    mac = (dev.get("mac") or "").upper().replace("-", "")[:8]
    vendor = None
    for v, ouis in VENDOR_OUI:
        if any(o.replace("-", "") == mac[:8] or mac.startswith(o.replace("-", ""))
               for o in ouis):
            vendor = v
            break
    dtype = None
    for t, keys in NAME_HINTS:
        if any(k in name for k in keys):
            dtype = t
            break
    if dev.get("kind") == "mdns":
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
    if dev.get("kind") == "serial":
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
                           encoding="gbk", errors="replace", timeout=10)
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
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            procs.append((target, p))
        for target, p in procs:
            try:
                p.wait(timeout=1.5)
            except Exception:
                p.kill()
        # 读 ARP 表
        try:
            r = subprocess.run(["arp", "-a"], capture_output=True, text=True,
                               encoding="gbk", errors="replace", timeout=10)
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
    """蓝牙 BLE 扫描（bleak）：发现蓝牙设备 / 传感器。"""
    out = []
    try:
        import asyncio
        from bleak import BleakScanner

        def _do():
            devices = []
            async def _scan():
                d = await BleakScanner.discover(timeout=timeout)
                devices.extend(d)
            asyncio.run(_scan())
            return devices

        devices = _do()
        for d in devices:
            out.append(recognize({"id": d.address, "name": d.name or d.address,
                                  "kind": "ble", "online": True, "rssi": getattr(d, "rssi", None),
                                  "adapter": "bt"}))
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


def scan_all():
    """全通道扫描（并行线程）。"""
    import threading
    results = {}

    def _run(key, fn):
        results[key] = fn()

    threads = [
        threading.Thread(target=_run, args=("lan", scan_lan)),
        threading.Thread(target=_run, args=("bt", scan_bt)),
        threading.Thread(target=_run, args=("mdns", scan_mdns)),
        threading.Thread(target=_run, args=("serial", scan_serial)),
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
    all_devices = []
    seen = set()
    for k in ("lan", "bt", "mdns", "serial", "extra"):
        for d in results.get(k) or (extra if k == "extra" else []):
            if isinstance(d, dict) and "error" not in d and d.get("id"):
                if d["id"] not in seen:
                    seen.add(d["id"])
                    all_devices.append(d)
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
    hit = next((d for d in devs if d["id"] == dev_id), None)
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


def auto_connect(ask=True):
    """扫描并连接新设备。

    ask=True：发现新设备时弹窗让使用者确认（默认，尊重使用者授权）；
    ask=False：静默连接（--yes 显式跳过询问）。
    返回 (devices, new_devs, confirmed)。
    """
    devs = scan_all()
    cfg = _load_cfg()
    known = {d["id"]: d for d in (cfg.get("devices") or [])}
    new = [d for d in devs if d["id"] not in known]
    if ask and new:
        if not _ask_confirm(new):
            return (cfg.get("devices") or []), new, False
    for d in devs:
        did = d["id"]
        if did in known:
            known[did].update({"online": True, "last_seen": time.time()})
        else:
            d["last_seen"] = time.time()
            d["connected"] = True
            known[did] = d
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
            devs, new, confirmed = auto_connect(ask=not a.yes)
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
            fn = {"all": scan_all, "lan": scan_lan, "bt": scan_bt,
                  "mdns": scan_mdns, "serial": scan_serial}[a.scan]
            devs = fn()
            if a.json:
                print(json.dumps({"ok": True, "count": len(devs), "devices": devs},
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
