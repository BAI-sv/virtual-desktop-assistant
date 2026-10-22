# -*- coding: utf-8 -*-
"""fairy_gpu_ab.py —— 从 MSI Afterburner 的共享内存读 AMD 显卡温度 / 功耗。

★★ 2026-10-18【用户："GPU-Z、MSI Afterburner、TM5 都有，你选一个"】
   最终选 **MSI Afterburner**，理由（全部实测过）：

   · AMD 老 ADL 接口在 RDNA4（RX 9070 XT）上被砍了 ——
     OD5/OD6/ODN 的温度、功耗函数全部返回「不支持」；
   · Windows 性能计数器只有使用率和显存，**没有温度/功耗**；
   · HWiNFO 免费版的共享内存要**手动在 GUI 里勾**，而且命令行参数是 Pro 功能；
   · **Afterburner 已经在跑**，共享内存 `MAHMSharedMemory` 现成的，
     实测一次就读到了 77 条传感器，其中：
         [71] GPU temperature      31.00 °C   ← 显卡温度
         [72] Memory temperature   36.00 °C   ← 显存温度
         [74] Power                40.00 W    ← 显卡功耗（名字就叫 Power）
         [75] Fan speed            25.00 %
         [76] Fan tachometer     1089.00 RPM

   零配置、零手动开关，只要 Afterburner 开着就能读。

【共享内存结构】Afterburner 公开的 MAHM：
  HEADER（32 字节）: 'MAHM' | version | headerSize | numEntries | entrySize | time_t...
  ENTRY（1324 字节）: srcName[260] | srcUnits[260] | locName[260] | locUnits[260]
                      | recommendedFormat[260] | data(float) | ...
"""
import ctypes
import struct
from ctypes import wintypes

MAP_NAME = "MAHMSharedMemory"
STR_LEN = 260


def _open_map():
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ofm = k32.OpenFileMappingW
    ofm.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    ofm.restype = wintypes.HANDLE
    mvf = k32.MapViewOfFile
    mvf.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                    wintypes.DWORD, ctypes.c_size_t]
    mvf.restype = ctypes.c_void_p
    h = ofm(0x0004, False, MAP_NAME)          # FILE_MAP_READ
    if not h:
        return None, None, None
    ptr = mvf(h, 0x0004, 0, 0, 0)
    if not ptr:
        try:
            k32.CloseHandle(h)
        except Exception:
            pass
        return None, None, None
    return k32, h, ptr


def read_all():
    """返回 {传感器名: (值, 单位)}。读不到返回 {}。"""
    k32, h, ptr = _open_map()
    if not ptr:
        return {}
    try:
        head = ctypes.string_at(ptr, 64)
        sig, ver, hsize, n_ent, e_size = struct.unpack_from("<IIIII", head, 0)
        if sig != 0x4D41484D:                 # 'MAHM'
            return {}
        if not (0 < n_ent < 500 and 100 < e_size < 4096 and 0 < hsize < 256):
            return {}
        data = ctypes.string_at(ptr, hsize + n_ent * e_size)
        out = {}
        for i in range(n_ent):
            base = hsize + i * e_size
            p = base
            src = data[p:p + STR_LEN].split(b"\x00", 1)[0].decode("utf-8", "replace"); p += STR_LEN
            units = data[p:p + STR_LEN].split(b"\x00", 1)[0].decode("utf-8", "replace"); p += STR_LEN
            p += STR_LEN * 3                   # 跳过 locName / locUnits / recommendedFormat
            try:
                (val,) = struct.unpack_from("<f", data, p)
            except Exception:
                continue
            name = (src or "").strip()
            if name:
                out[name] = (round(float(val), 2), units.strip())
        return out
    except Exception:
        return {}
    finally:
        for fn, arg in ((k32.UnmapViewOfFile, ptr), (k32.CloseHandle, h)):
            try:
                fn(arg)
            except Exception:
                pass


# Afterburner 的传感器名（实测）
NAME_TEMP = ("GPU temperature", "GPU Temperature")
NAME_VRAM_TEMP = ("Memory temperature",)
NAME_POWER = ("Power", "GPU power", "GPU Power")
NAME_FAN = ("Fan speed",)
NAME_FAN_RPM = ("Fan tachometer",)
NAME_UTIL = ("GPU usage",)


def _pick(d, names):
    for n in names:
        if n in d:
            return d[n][0]
    # 退一步：不区分大小写找
    low = {k.lower(): v for k, v in d.items()}
    for n in names:
        if n.lower() in low:
            return low[n.lower()][0]
    return None


def gpu_snapshot():
    """返回 {"temp","vram_temp","power","fan_pct","fan_rpm","util","source"}。"""
    d = read_all()
    res = {"temp": None, "vram_temp": None, "power": None,
           "fan_pct": None, "fan_rpm": None, "util": None,
           "source": "afterburner" if d else "none"}
    if not d:
        return res
    res["temp"] = _pick(d, NAME_TEMP)
    res["vram_temp"] = _pick(d, NAME_VRAM_TEMP)
    res["power"] = _pick(d, NAME_POWER)
    res["fan_pct"] = _pick(d, NAME_FAN)
    res["fan_rpm"] = _pick(d, NAME_FAN_RPM)
    res["util"] = _pick(d, NAME_UTIL)
    return res


def available():
    return bool(read_all())


if __name__ == "__main__":
    print("  Afterburner 共享内存可读:", available())
    s = gpu_snapshot()
    print("  GPU 温度:", s["temp"], "℃")
    print("  显存温度:", s["vram_temp"], "℃")
    print("  功耗:", s["power"], "W")
    print("  风扇:", s["fan_pct"], "% /", s["fan_rpm"], "RPM")
    print("  使用率:", s["util"], "%")
