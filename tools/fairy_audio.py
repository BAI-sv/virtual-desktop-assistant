# -*- coding: utf-8 -*-
"""
fairy_audio.py —— 默认音频输出设备的【读取 / 保存 / 还原】

【为什么需要它】
  2026-10-09 事故：fairy_ear.py（耳朵）用了蓝牙耳机的麦克风 →
  Windows 自动把【默认输出设备】从扬声器切成了耳机（HFP 免提模式）→
  耳朵停掉后【没有还原】→ 球说话的声音全跑进耳机，用户"一点声音都没有了"。
  → 所以任何会用到蓝牙麦克风的程序，都必须在开始前 save()、结束后 restore()。

【用法】
  py -3.12 fairy_audio.py list          列出所有【激活中】的输出设备
  py -3.12 fairy_audio.py current       打印当前默认输出
  py -3.12 fairy_audio.py save          把当前默认输出存到 fairy_audio_state.json
  py -3.12 fairy_audio.py restore       还原到保存的那台
  py -3.12 fairy_audio.py set <名称片段> 切换默认输出到匹配的设备（三个角色都切）
  py -3.12 fairy_audio.py guard-on      保存 + 注册 atexit（同一进程内自动还原）

【原理】Core Audio 的 IPolicyConfig::SetDefaultEndpoint(vtable[13])。
  必须只对【激活中】(DEVICE_STATE_ACTIVE=1) 的设备切换 —— 否则 Windows 会静默退回原设备
  （2026-10-09 实测：切给未激活的蓝牙音箱无效，切给激活的设备立刻生效）。
"""
import atexit
import ctypes
import json
import os
import sys
import fairy_root

STATE = fairy_root.log("fairy_audio_state.json")


class GUID(ctypes.Structure):
    _fields_ = [("D1", ctypes.c_ulong), ("D2", ctypes.c_ushort),
                ("D3", ctypes.c_ushort), ("D4", ctypes.c_ubyte * 8)]


class PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", ctypes.c_ulong)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort),
                ("r3", ctypes.c_ushort), ("p", ctypes.c_void_p), ("pad", ctypes.c_byte * 8)]


def _G(a, b, c, d):
    return GUID(a, b, c, (ctypes.c_ubyte * 8)(*d))


CLSID_MMDE = _G(0xBCDE0395, 0xE52F, 0x467C, (0x8E, 0x3D, 0xC4, 0x57, 0x92, 0x91, 0x69, 0x2E))
IID_IMMDE = _G(0xA95664D2, 0x9614, 0x4F35, (0xA7, 0x46, 0xDE, 0x8D, 0xB6, 0x36, 0x17, 0xE6))
CLSID_PCC = _G(0x870AF99C, 0x171D, 0x4F9E, (0xAF, 0x0D, 0xE6, 0x3D, 0xF4, 0x0C, 0x2B, 0xC9))
IID_IPC = _G(0xF8679F50, 0x850A, 0x41CF, (0x9C, 0x72, 0x43, 0x0F, 0x29, 0x02, 0x90, 0xC8))
PKEY_FN = PROPERTYKEY(_G(0xa45c254e, 0xdf1c, 0x4efd,
                         (0x80, 0x20, 0x67, 0xd1, 0x46, 0xa8, 0x50, 0xe0)), 14)

_ole32 = ctypes.windll.ole32


def _vt(p):
    return ctypes.cast(p, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents


def _enumerate(active_only=True):
    """返回 [(名称, 设备ID), ...]"""
    _ole32.CoInitialize(None)
    pe = ctypes.c_void_p()
    hr = _ole32.CoCreateInstance(ctypes.byref(CLSID_MMDE), None, 1,
                                 ctypes.byref(IID_IMMDE), ctypes.byref(pe))
    if hr != 0:
        print("[audio] CoCreateInstance 失败 hr=0x%08X" % (hr & 0xFFFFFFFF), flush=True)
        return []
    evt = _vt(pe)
    EnumAE = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_int,
                                ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(evt[3])
    col = ctypes.c_void_p()
    mask = 0x00000001 if active_only else 0x0000000F
    if EnumAE(pe, 0, mask, ctypes.byref(col)) != 0:
        return []
    cvt = _vt(col)
    GetCount = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint))(cvt[3])
    Item = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint,
                              ctypes.POINTER(ctypes.c_void_p))(cvt[4])
    n = ctypes.c_uint()
    GetCount(col, ctypes.byref(n))
    out = []
    for i in range(n.value):
        dev = ctypes.c_void_p()
        Item(col, i, ctypes.byref(dev))
        dvt = _vt(dev)
        GetId = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                                   ctypes.POINTER(ctypes.c_wchar_p))(dvt[5])
        OpenStore = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_int,
                                       ctypes.POINTER(ctypes.c_void_p))(dvt[4])
        did = ctypes.c_wchar_p()
        GetId(dev, ctypes.byref(did))
        st = ctypes.c_void_p()
        name = "?"
        if OpenStore(dev, 0, ctypes.byref(st)) == 0:
            svt = _vt(st)
            GetValue = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                                          ctypes.POINTER(PROPERTYKEY),
                                          ctypes.POINTER(PROPVARIANT))(svt[5])
            pv = PROPVARIANT()
            if GetValue(st, ctypes.byref(PKEY_FN), ctypes.byref(pv)) == 0 and pv.vt == 31 and pv.p:
                name = ctypes.cast(pv.p, ctypes.c_wchar_p).value or "?"
            _ole32.PropVariantClear(ctypes.byref(pv))
        out.append((name, did.value or ""))
    return out


def current_device_id():
    """当前 eConsole 的默认输出设备 ID"""
    _ole32.CoInitialize(None)
    pe = ctypes.c_void_p()
    if _ole32.CoCreateInstance(ctypes.byref(CLSID_MMDE), None, 1,
                               ctypes.byref(IID_IMMDE), ctypes.byref(pe)) != 0:
        return ""
    evt = _vt(pe)
    GetDefault = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_int,
                                    ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(evt[4])
    dev = ctypes.c_void_p()
    if GetDefault(pe, 0, 0, ctypes.byref(dev)) != 0:      # eRender=0, eConsole=0
        return ""
    dvt = _vt(dev)
    GetId = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                               ctypes.POINTER(ctypes.c_wchar_p))(dvt[5])
    did = ctypes.c_wchar_p()
    GetId(dev, ctypes.byref(did))
    return did.value or ""


def set_default(device_id):
    """把三个角色(eConsole/eMultimedia/eCommunications)都切到该设备"""
    _ole32.CoInitialize(None)
    pc = ctypes.c_void_p()
    hr = _ole32.CoCreateInstance(ctypes.byref(CLSID_PCC), None, 1,
                                 ctypes.byref(IID_IPC), ctypes.byref(pc))
    if hr != 0:
        print("[audio] IPolicyConfig 拿不到 hr=0x%08X" % (hr & 0xFFFFFFFF), flush=True)
        return False
    pvt = _vt(pc)
    SetDefault = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                                    ctypes.c_wchar_p, ctypes.c_int)(pvt[13])
    ok = True
    for role in (0, 1, 2):
        h = SetDefault(pc, device_id, role)
        if h != 0:
            ok = False
    return ok


def name_of(device_id):
    for nm, did in _enumerate(active_only=False):
        if did == device_id:
            return nm
    return "?"


def save():
    did = current_device_id()
    nm = name_of(did)
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"id": did, "name": nm}, f, ensure_ascii=False)
        os.replace(tmp, STATE)
        print("[audio] 已保存当前默认输出: %s" % nm, flush=True)
    except Exception as e:
        print("[audio] 保存失败: %s: %s" % (type(e).__name__, e), flush=True)
    return did


def restore():
    try:
        with open(STATE, encoding="utf-8") as f:
            st = json.load(f)
    except Exception as e:
        print("[audio] 没有可还原的记录（%s）" % str(e)[:60], flush=True)
        return False
    want = st.get("id") or ""
    if not want:
        return False
    if current_device_id() == want:
        print("[audio] 默认输出没变，无需还原（%s）" % st.get("name"), flush=True)
        return True
    if set_default(want):
        print("[audio] 已还原默认输出 -> %s" % st.get("name"), flush=True)
        return True
    print("[audio] 还原失败（目标设备可能已拔掉）", flush=True)
    return False


def main():
    a = sys.argv[1:]
    if not a or a[0] == "list":
        print("=== 激活中的输出设备 ===")
        for i, (nm, did) in enumerate(_enumerate(True)):
            print("   [%d] %s" % (i, nm))
        print("\n=== 全部输出设备（含未激活）===")
        for i, (nm, did) in enumerate(_enumerate(False)):
            print("   [%d] %s" % (i, nm))
        return 0
    if a[0] == "current":
        print(name_of(current_device_id()))
        return 0
    if a[0] == "save":
        save()
        return 0
    if a[0] == "restore":
        return 0 if restore() else 1
    if a[0] == "guard-on":
        save()
        atexit.register(restore)
        print("[audio] 已开启守卫：本进程退出时自动还原默认输出", flush=True)
        return 0
    if a[0] == "set" and len(a) > 1:
        key = a[1]
        for nm, did in _enumerate(True):
            if key in nm:
                if set_default(did):
                    print("[audio] 已切到 %s" % nm)
                    return 0
                print("[audio] 切换失败: %s" % nm)
                return 1
        print("[audio] 没找到匹配「%s」的激活设备" % key)
        return 2
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
