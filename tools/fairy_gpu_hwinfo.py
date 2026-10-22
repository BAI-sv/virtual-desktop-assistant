# -*- coding: utf-8 -*-
"""fairy_gpu_hwinfo.py —— 从 HWiNFO 的共享内存读 GPU 温度 / 功耗。

★ 2026-10-18【用户要求"能查 GPU 温度和功耗的工具多的是，选一个在监控栏显示"】

为什么走 HWiNFO：
  · AMD 在 RDNA4（RX 9070 XT）上把老的 ADL Overdrive 接口全砍了 —— 实测
    ADL2_Overdrive5_Temperature_Get / OD6_Temperature_Get / ODN_Temperature_Get
    / OD6_CurrentPower_Get 全部返回「不支持」；
  · Windows 自带的性能计数器只有 GPU 使用率和显存，**没有温度/功耗**；
  · HWiNFO 用户机器上已经装了（8.52），它能读这些传感器，
    只要在它设置里勾上「Shared Memory Support」，就会在内存里暴露一块结构体。

【前提（只需做一次）】
  打开 HWiNFO → Sensors 窗口 → 右下齿轮(Settings) → 找 "Shared Memory Support"
  → 勾上 → 确定。之后每次 HWiNFO 在跑，这块内存就在。

【结构体布局】按 HWiNFO 官方 SDK（x64 有对齐 padding，见下面的 struct）
"""
import ctypes
import struct
from ctypes import wintypes

STR_LEN = 128
UNIT_LEN = 16

SHARED_NAMES = (
    r"Global\HWiNFO_SENSORS_SharedFile",
    r"HWiNFO_SENSORS_SharedFile",
    r"Global\HWiNFO_SENSORS_SM2_SharedFile",
    r"HWiNFO_SENSORS_SM2_SharedFile",
)


def _read_shared():
    """映射共享内存，返回原始 bytes（读不到返回 None）。"""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ofm = k32.OpenFileMappingW
    ofm.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    ofm.restype = wintypes.HANDLE
    mvf = k32.MapViewOfFile
    mvf.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                    wintypes.DWORD, ctypes.c_size_t]
    mvf.restype = ctypes.c_void_p

    for name in SHARED_NAMES:
        h = ofm(0x0004, False, name)          # FILE_MAP_READ
        if not h:
            continue
        try:
            ptr = mvf(h, 0x0004, 0, 0, 0)
            if not ptr:
                continue
            # 头 48 字节拿到各段偏移/数量
            head = ctypes.string_at(ptr, 48)
            sig, ver, rev = struct.unpack_from("<III", head, 0)
            if sig != 0x53695748:              # 'HWiS'
                continue
            # 注意 x64 对齐：3×DWORD(12) + padding(4) + time_t(8) = 24 起
            (off_sens, sz_sens, n_sens,
             off_read, sz_read, n_read) = struct.unpack_from("<IIIIII", head, 24)
            total = max(off_read + sz_read * n_read, off_sens + sz_sens * n_sens, 48)
            data = ctypes.string_at(ptr, total)
            return {"sig": sig, "ver": ver, "rev": rev,
                    "off_sens": off_sens, "sz_sens": sz_sens, "n_sens": n_sens,
                    "off_read": off_read, "sz_read": sz_read, "n_read": n_read,
                    "data": data}
        finally:
            try:
                k32.CloseHandle(h)
            except Exception:
                pass
    return None


def _cstr(b):
    return b.split(b"\x00", 1)[0].decode("utf-8", "replace").strip()


def read_sensors():
    """返回 {label: value} 的读数字典（label 里带单位）。读不到返回 {}。"""
    m = _read_shared()
    if not m:
        return {}
    out = {}
    d = m["data"]
    for i in range(m["n_read"]):
        base = m["off_read"] + i * m["sz_read"]
        try:
            # dwSensorIndex(4) dwReadingID(4) label[128] user[128] unit[16] double value...
            sensor_idx, reading_id = struct.unpack_from("<II", d, base)
            p = base + 8
            label = _cstr(d[p:p + STR_LEN]); p += STR_LEN
            user = _cstr(d[p:p + STR_LEN]); p += STR_LEN
            unit = _cstr(d[p:p + UNIT_LEN]); p += UNIT_LEN
            val = struct.unpack_from("<d", d, p)[0]
            key = (user or label).strip()
            if key:
                out[key] = {"value": val, "unit": unit, "label": label}
        except Exception:
            continue
    return out


def gpu_temp_power():
    """专挑 GPU 的温度和功耗。

    返回 {"temp": float|None, "power": float|None, "source": "hwinfo", "raw": {...}}
    HWiNFO 的标签形如：
        "GPU Temperature"                 -> 温度
        "GPU Power" / "GPU Chip Power"    -> 功耗
        "GPU Hot Spot Temperature"        -> 热点温度（优先当温度用，更敏感）
    """
    s = read_sensors()
    res = {"temp": None, "power": None, "source": "hwinfo", "raw": {}}
    if not s:
        return res
    # 收集 GPU 相关
    for k, v in s.items():
        kl = k.lower()
        if "gpu" not in kl:
            continue
        if "temperature" in kl or "temp" in kl:
            res["raw"].setdefault("temps", {})[k] = round(v["value"], 1)
        if "power" in kl:
            res["raw"].setdefault("powers", {})[k] = round(v["value"], 1)
    temps = res["raw"].get("temps") or {}
    powers = res["raw"].get("powers") or {}
    # 温度优先级：Hot Spot > GPU Temperature > 第一个
    for key in ("GPU Hot Spot Temperature", "GPU Temperature",
                "GPU Memory Junction Temperature"):
        if key in temps:
            res["temp"] = temps[key]
            break
    if res["temp"] is None and temps:
        res["temp"] = list(temps.values())[0]
    # 功耗优先级：GPU Power > GPU Chip Power > 第一个
    for key in ("GPU Power", "GPU Chip Power", "GPU Package Power"):
        if key in powers:
            res["power"] = powers[key]
            break
    if res["power"] is None and powers:
        res["power"] = list(powers.values())[0]
    return res


def available():
    """HWiNFO 共享内存现在可不可读。"""
    return bool(_read_shared())


if __name__ == "__main__":
    print("  共享内存可读:", available())
    r = gpu_temp_power()
    print("  温度:", r["temp"], "℃")
    print("  功耗:", r["power"], "W")
    if r["raw"]:
        print("  原始读数:")
        for k, v in r["raw"].items():
            for kk, vv in (v or {}).items():
                print("    %-40s %s" % (kk, vv))
