# -*- coding: utf-8 -*-
import asyncio
import json
import os
import sys
import time
import fairy_root

OUT = fairy_root.log("ble_identify.json")
SCAN_CACHE = fairy_root.log("ble_scan_raw.json")


COMPANY = {
    0x004C: "Apple", 0x0006: "Microsoft", 0x0075: "Samsung", 0x0157: "Xiaomi",
    0x027D: "Huawei", 0x0087: "Garmin", 0x00E0: "Google", 0x0059: "Nordic",
    0x000D: "Texas Instruments", 0x02E5: "Espressif(乐鑫)", 0x038F: "Xiaomi",
    0x0171: "Amazon", 0x0001: "Nokia", 0x0002: "Intel", 0x000A: "CSR",
    0x0499: "Ruuvi", 0x0617: "Xiaomi", 0x0822: "Huawei", 0x0224: "Huawei",
    0x00D2: "Bose", 0x00E7: "Sony", 0x017E: "JBL", 0x0310: "Xiaomi",
    0x0700: "Huawei", 0x0A0C: "Huawei", 0x027D0: "Huawei",
}

SVC = {
    "00001800": "Generic Access(通用访问)",
    "00001801": "Generic Attribute(通用属性)",
    "0000180a": "★Device Information(设备信息)",
    "0000180f": "Battery(电量)",
    "00001809": "Health Thermometer(体温计)",
    "0000180d": "Heart Rate(心率)",
    "00001812": "HID(人机接口)",
    "0000181a": "Environmental Sensing(环境传感)",
    "00001816": "Cycling Speed and Cadence",
    "00001818": "Cycling Power",
    "00001826": "Fitness Machine(健身器材)",
    "0000fdab": "★Huawei 私有服务",
    "0000fe95": "★Xiaomi 私有服务",
    "0000fee7": "★微信硬件",
    "0000fee9": "★腾讯",
    "0000fef5": "★Dialog/其他",
    "00001805": "Current Time(当前时间)",
    "00001804": "Tx Power(发射功率)",
}
CHARS = {
    "00002a29": "Manufacturer Name(制造商名)",
    "00002a24": "Model Number(型号)",
    "00002a25": "Serial Number(序列号)",
    "00002a26": "Firmware Revision(固件版本)",
    "00002a27": "Hardware Revision(硬件版本)",
    "00002a28": "Software Revision(软件版本)",
    "00002a19": "Battery Level(电量%)",
    "00002a00": "Device Name(设备名)",
}

def svc_name(uuid):
    k = str(uuid).lower().replace("-", "")[:8]
    full = str(uuid).lower().replace("-", "")
    if full in SVC:
        return SVC[full]
    if k in SVC:
        return SVC[k]
    if k.startswith("000018") or k.startswith("00002a"):
        return "标准 UUID(%s)" % k
    if full.endswith("00001000800000805f9b34fb"):
        return "蓝牙 SIG 自定义(%s)" % k
    return "未知厂商私有(%s)" % k

def char_name(uuid):
    full = str(uuid).lower().replace("-", "")
    k = full[:8]
    return CHARS.get(full) or CHARS.get(k) or ""

def _atomic_json(path, obj, tag=""):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
        return True
    except Exception as e:
        print("   [warn] 写 %s 失败%s: %s" % (path, ("（" + tag + "）") if tag else "", e), flush=True)
        return False

async def scan(sec=10):
    from bleak import BleakScanner
    print("[阶段1] 扫描 %d 秒（拿完整广播）..." % sec, flush=True)
    found = await BleakScanner.discover(timeout=sec, return_adv=True)
    rows = []
    for addr, (dev, adv) in found.items():
        md = {}
        for cid, data in (adv.manufacturer_data or {}).items():
            md[hex(cid)] = {
                "company": COMPANY.get(cid, "未知厂商ID"),
                "bytes": data.hex()[:40],
            }
        rows.append({
            "address": dev.address,
            "name": adv.local_name or dev.name or "",
            "rssi": adv.rssi,
            "manufacturer_data": md,
            "service_uuids": list(adv.service_uuids or []),
            "service_data": {str(k): bytes(v).hex()[:40] for k, v in (adv.service_data or {}).items()},
            "tx_power": adv.tx_power,
        })
    rows.sort(key=lambda r: -(r["rssi"] or -999))
    _atomic_json(SCAN_CACHE, rows, "扫描缓存")
    print("   发现 %d 个，已存 %s" % (len(rows), SCAN_CACHE))
    for r in rows:
        names = ",".join(v["company"] for v in r["manufacturer_data"].values()) or "-"
        svcs = ",".join(svc_name(u) for u in r["service_uuids"][:4]) or "-"
        print("   %-20s %5s dBm  %-24s 厂商ID=%s  服务=%s"
              % (r["address"], r["rssi"], (r["name"] or "(无名)")[:24], names, svcs[:60]))
    return rows

async def gatt(rows):
    from bleak import BleakClient
    print("\n[阶段2] 逐个连接读 GATT（只读，不写）", flush=True)
    out = []
    for r in rows:
        addr = r["address"]
        rec = dict(r)
        rec.update({"connect": False, "gatt": [], "device_info": {}, "verdict": "", "confidence": "low"})
        t0 = time.time()
        try:
            async with BleakClient(addr, timeout=10.0) as cli:
                rec["connect"] = True
                svcs = []
                for s in cli.services:
                    sn = svc_name(s.uuid)
                    chars = []
                    for c in s.characteristics:
                        cn = char_name(c.uuid)
                        val = None
                        if "read" in c.properties:
                            try:
                                b = await cli.read_gatt_char(c)
                                try:
                                    val = bytes(b).decode("utf-8", "replace").strip("\x00").strip()
                                except Exception:
                                    val = bytes(b).hex()[:32]
                            except Exception as e:
                                val = "<读失败:%s>" % str(e)[:24]
                        chars.append({"uuid": str(c.uuid), "name": cn,
                                      "props": list(c.properties), "value": val})

                        if str(s.uuid).lower().startswith("0000180a") and cn and val:
                            rec["device_info"][cn] = val
                    svcs.append({"uuid": str(s.uuid), "name": sn, "chars": chars})
                rec["gatt"] = svcs
        except Exception as e:
            rec["error"] = "%s: %s" % (type(e).__name__, str(e)[:120])
        rec["elapsed"] = round(time.time() - t0, 1)


        if rec["device_info"]:
            rec["verdict"] = "设备信息: " + "; ".join("%s=%s" % (k, v) for k, v in rec["device_info"].items())
            rec["confidence"] = "high"
        elif rec["connect"]:
            names = [s["name"] for s in rec["gatt"] if not s["name"].startswith("未知")]
            if names:
                rec["verdict"] = "GATT 服务: " + ", ".join(names[:6])
                rec["confidence"] = "medium"
            else:
                rec["verdict"] = "连上了但只有通用服务，读不出身份"
                rec["confidence"] = "low"
        elif rec["manufacturer_data"]:
            comp = [v["company"] for v in rec["manufacturer_data"].values()]
            rec["verdict"] = "连不上；广播厂商ID = " + ",".join(comp)
            rec["confidence"] = "medium" if any(c != "未知厂商ID" for c in comp) else "low"
        else:
            rec["verdict"] = "连不上、广播也没厂商信息 -> 认不出（原因: %s）" % rec.get("error", "未知")
            rec["confidence"] = "none"

        print("   %-20s %s  %s  [%s] (%.1fs)"
              % (addr, "连上" if rec["connect"] else "连不上", rec["verdict"][:70],
                 rec["confidence"], rec["elapsed"]), flush=True)
        out.append(rec)


        _atomic_json(OUT, out, "增量保存 %s" % addr)
    _atomic_json(OUT, out, "最终结果")
    print("\n结果已写 %s" % OUT)
    return out

async def main():
    rows = None
    if "--gatt" not in sys.argv:
        rows = await scan(10)
    if "--scan" in sys.argv:
        return
    if rows is None:
        rows = json.load(open(SCAN_CACHE, encoding="utf-8"))
    await gatt(rows)

if __name__ == "__main__":
    asyncio.run(main())
