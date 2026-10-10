# -*- coding: utf-8 -*-
import atexit
import ctypes
import json
import os
import sys
import time
import fairy_root

STATE = fairy_root.log("volume_duck_state.json")
TIMEOUT_S = 60

ole32 = ctypes.windll.ole32


_warned_once = set()
_atexit_done = False

def _warn_once(msg):
    if msg in _warned_once:
        return
    _warned_once.add(msg)
    print("[volume_duck] %s" % msg, flush=True)

class GUID(ctypes.Structure):
    _fields_ = [("d1", ctypes.c_ulong), ("d2", ctypes.c_ushort),
                ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]

    def __init__(self, a, b, c, d):
        super().__init__(a, b, c, (ctypes.c_ubyte * 8)(*d))

CLSID_MMDeviceEnumerator = GUID(0xBCDE0395, 0xE52F, 0x467C,
                                [0x8E, 0x3D, 0xC4, 0x57, 0x92, 0x91, 0x69, 0x2E])
IID_IMMDeviceEnumerator = GUID(0xA95664D2, 0x9614, 0x4F35,
                               [0xA7, 0x46, 0xDE, 0x8D, 0xB6, 0x36, 0x17, 0xE6])
IID_IAudioEndpointVolume = GUID(0x5CDF2C82, 0x841E, 0x4546,
                                [0x97, 0x22, 0x0C, 0xF7, 0x40, 0x78, 0x22, 0x9A])

class VolumeDucker:

    def __init__(self):
        self._com_ready = False
        self._vol = None
        self._saved = None
        self._ducked_to = None
        self._ducked_at = 0.0
        self._last_error = ""
        self._open()


        try:
            _r = self.emergency_restore()
            if _r.get("restored"):
                print("[volume_duck] 启动自愈：音量已还原到 %.2f" % (_r.get("to") or 0),
                      flush=True)
        except Exception as e:
            _warn_once("启动自愈失败: %s" % str(e)[:110])

        global _atexit_done
        if not _atexit_done:
            try:
                atexit.register(self.emergency_restore)
                _atexit_done = True
            except Exception as e:
                _warn_once("注册 atexit 失败: %s" % str(e)[:110])


    def _open(self):
        try:

            hr = ole32.CoInitialize(None)
            self._com_ready = True

            p = ctypes.c_void_p()
            hr = ole32.CoCreateInstance(ctypes.byref(CLSID_MMDeviceEnumerator), None,
                                        0x17, ctypes.byref(IID_IMMDeviceEnumerator),
                                        ctypes.byref(p))
            if hr != 0 or not p.value:
                self._last_error = "CoCreateInstance hr=0x%08X" % (hr & 0xFFFFFFFF)
                return
            self._enum = p
            fn = ctypes.cast(ctypes.cast(p, ctypes.POINTER(ctypes.c_void_p))[0],
                             ctypes.POINTER(ctypes.c_void_p))
            GetDefault = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_int,
                                            ctypes.c_int, ctypes.POINTER(ctypes.c_void_p))(fn[4])
            dev = ctypes.c_void_p()
            hr = GetDefault(p, 0, 0, ctypes.byref(dev))
            if hr != 0 or not dev.value:
                self._last_error = "GetDefaultAudioEndpoint hr=0x%08X" % (hr & 0xFFFFFFFF)
                return
            self._dev = dev
            dfn = ctypes.cast(ctypes.cast(dev, ctypes.POINTER(ctypes.c_void_p))[0],
                              ctypes.POINTER(ctypes.c_void_p))
            Activate = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                                          ctypes.POINTER(GUID), ctypes.c_ulong,
                                          ctypes.c_void_p,
                                          ctypes.POINTER(ctypes.c_void_p))(dfn[3])
            vol = ctypes.c_void_p()
            hr = Activate(dev, ctypes.byref(IID_IAudioEndpointVolume), 0x17, None,
                          ctypes.byref(vol))
            if hr != 0 or not vol.value:
                self._last_error = "Activate hr=0x%08X" % (hr & 0xFFFFFFFF)
                return
            self._vol = vol
            vfn = ctypes.cast(ctypes.cast(vol, ctypes.POINTER(ctypes.c_void_p))[0],
                              ctypes.POINTER(ctypes.c_void_p))
            self._get_v = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                                             ctypes.POINTER(ctypes.c_float))(vfn[9])
            self._set_v = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_float,
                                             ctypes.c_void_p)(vfn[7])
            self._get_m = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p,
                                             ctypes.POINTER(ctypes.c_int))(vfn[15])
            self._set_m = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_int,
                                             ctypes.c_void_p)(vfn[14])
        except Exception as e:
            self._last_error = "%s: %s" % (type(e).__name__, e)

    @property
    def ok(self):
        return self._vol is not None

    def last_error(self):
        return self._last_error


    def get_volume(self):
        if not self.ok:
            return None
        try:
            f = ctypes.c_float()
            hr = self._get_v(self._vol, ctypes.byref(f))
            if hr != 0:
                return None
            v = round(float(f.value), 4)


            if not (0.0 <= v <= 1.0):
                _warn_once("get_volume 返回异常值 %.4f（已忽略，按读不到处理）" % v)
                return None
            return v
        except Exception as e:
            _warn_once("get_volume 异常: %s" % str(e)[:110])
            return None

    def get_mute(self):
        if not self.ok:
            return None
        try:
            m = ctypes.c_int()
            hr = self._get_m(self._vol, ctypes.byref(m))
            return bool(m.value) if hr == 0 else None
        except Exception:
            return None


    def _set_volume(self, v):
        v = max(0.0, min(1.0, float(v)))
        hr = self._set_v(self._vol, ctypes.c_float(v), None)
        return hr == 0

    def duck(self, level=0.2, smooth=True, steps=4, gap=0.03):
        if not self.ok:
            return {"ducked": False, "why": "音量接口不可用: %s" % self._last_error}
        cur = self.get_volume()
        if cur is None:
            return {"ducked": False, "why": "读不到当前音量"}
        target = max(0.0, min(1.0, float(level)))


        self._saved = cur
        self._ducked_to = target
        self._ducked_at = time.time()
        tok = {"ducked": False, "saved": cur, "target": target,
               "at": self._ducked_at, "deadline": self._ducked_at + TIMEOUT_S}

        try:
            if smooth and steps > 1:
                for i in range(1, steps + 1):
                    v = cur + (target - cur) * (i / float(steps))
                    self._set_volume(v)
                    time.sleep(gap)
            else:
                self._set_volume(target)
            after = self.get_volume()
            ok = after is not None and abs(after - target) < 0.05
            tok["ducked"] = bool(ok)
            tok["after"] = after
            if not ok:

                self._set_volume(cur)
                tok["why"] = "压低后读回不符（期望 %.2f 实得 %s），已回滚" % (target, after)
                self._saved = None
            else:
                tok["by_me"] = True
                self._save(tok)
        except Exception as e:
            try:
                self._set_volume(cur)
            except Exception as e2:


                _warn_once("回滚也失败（音量可能停在压低态！）: %s" % str(e2)[:110])
            self._saved = None
            tok["ducked"] = False
            tok["why"] = "异常已回滚: %s" % str(e)[:100]
        return tok

    def unduck(self, token=None):
        tok = token or self._load()
        if not tok or not tok.get("ducked"):
            return {"ok": True, "restored": False, "why": "没有需要恢复的"}
        if not tok.get("by_me"):
            return {"ok": True, "restored": False, "why": "不是我压的，不动"}
        saved = tok.get("saved")
        if saved is None:


            self._save({})
            return {"ok": True, "restored": False, "why": "没记录到原值（不动，已清令牌）"}


        cur = self.get_volume()
        want = tok.get("target")
        if cur is not None and want is not None and abs(cur - want) > 0.06:
            self._saved = None
            self._save({})
            return {"ok": True, "restored": False,
                    "why": "用户已自己调过音量（当前 %.2f，我压的是 %.2f），不覆盖" % (cur, want)}

        if not self.ok:
            return {"ok": False, "restored": False, "why": "接口不可用"}
        okr = self._set_volume(saved)
        after = self.get_volume()
        good = okr and after is not None and abs(after - saved) < 0.06
        self._saved = None
        self._save({})
        return {"ok": good, "restored": good, "to": saved, "after": after}

    def tick(self):
        tok = self._load()
        if tok and tok.get("ducked") and tok.get("by_me"):
            if time.time() > (tok.get("deadline") or 0):
                r = self.unduck(tok)
                r["forced_by_timeout"] = True
                return r
        return {"ok": True, "restored": False, "why": "无需兜底"}

    def emergency_restore(self):
        try:
            tok = self._load()
            if tok and tok.get("saved") is not None:
                self._set_volume(tok["saved"])
                self._save({})
                return {"ok": True, "restored": True, "to": tok["saved"]}
        except Exception as e:

            _warn_once("emergency_restore 失败: %s" % str(e)[:110])
        return {"ok": True, "restored": False}


    def _save(self, d):
        try:
            os.makedirs(os.path.dirname(STATE), exist_ok=True)
            tmp = STATE + ".tmp"
            json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            os.replace(tmp, STATE)
        except Exception as e:
            _warn_once("写状态失败（恢复令牌可能丢失）: %s" % str(e)[:110])

    def _load(self):
        try:
            if os.path.exists(STATE):
                return json.load(open(STATE, encoding="utf-8"))
        except Exception as e:
            _warn_once("读状态失败（按 无令牌 处理）: %s" % str(e)[:110])
        return {}


def selftest():
    print("=== volume_duck 自测（会真的改音量，测完立刻恢复）===")
    vd = VolumeDucker()
    oks = fails = 0

    def chk(name, cond, extra=""):
        nonlocal oks, fails
        if cond:
            oks += 1
            print("  ✓ %s" % name)
        else:
            fails += 1
            print("  ✗ %s  %s" % (name, extra))

    chk("COM/接口可用", vd.ok, vd.last_error())
    if not vd.ok:
        print("\n  %d 通过 / %d 失败（接口不可用，后续测试跳过）" % (oks, fails))
        return 1

    v0 = vd.get_volume()
    m0 = vd.get_mute()
    print("  初始：音量=%.3f 静音=%s" % (v0 if v0 is not None else -1, m0))
    chk("get_volume 返回 0~1", v0 is not None and 0.0 <= v0 <= 1.0)
    chk("get_mute 返回布尔", isinstance(m0, bool))


    target = 0.2 if (v0 or 0.5) > 0.4 else 0.5
    tok = vd.duck(target, smooth=True, steps=3, gap=0.03)
    after = vd.get_volume()
    print("  duck(%s) -> ducked=%s after=%.3f %s" % (target, tok.get("ducked"), after or -1,
                                                    tok.get("why", "")))
    chk("duck 真的压低（读回接近目标）", after is not None and abs(after - target) < 0.06,
        "after=%s target=%s" % (after, target))
    chk("duck 记录了原值", tok.get("saved") == v0, "saved=%s v0=%s" % (tok.get("saved"), v0))
    chk("duck 打了 by_me 标记", tok.get("by_me") is True)


    vd._set_volume(0.77 if abs(target - 0.77) > 0.1 else 0.66)
    r = vd.unduck(tok)
    cur = vd.get_volume()
    print("  模拟用户改音量后 unduck -> restored=%s why=%s" % (r.get("restored"), r.get("why")))
    chk("规矩2 用户动过就不覆盖", r.get("restored") is False, "restored=%s" % r.get("restored"))


    vd._set_volume(v0)
    tok2 = vd.duck(target, smooth=True, steps=3, gap=0.03)
    r2 = vd.unduck(tok2)
    v1 = vd.get_volume()
    print("  正常路径 unduck -> restored=%s to=%s after=%.3f" % (r2.get("restored"),
                                                                r2.get("to"), v1 or -1))
    chk("正常路径恢复成功", r2.get("restored") is True, str(r2))
    chk("恢复到原值", v1 is not None and abs(v1 - v0) < 0.06, "v1=%s v0=%s" % (v1, v0))


    vd._save({"ducked": True, "by_me": True, "saved": v0, "target": 0.2,
              "deadline": time.time() - 5})
    vd._set_volume(0.2)
    t = vd.tick()
    vt = vd.get_volume()
    print("  tick 超时兜底 -> restored=%s forced=%s after=%.3f" % (t.get("restored"),
                                                                  t.get("forced_by_timeout"), vt or -1))
    chk("规矩3 超时兜底触发并恢复", t.get("forced_by_timeout") is True, str(t))
    chk("兜底后回到原值", vt is not None and abs(vt - v0) < 0.06, "vt=%s v0=%s" % (vt, v0))


    vd.emergency_restore()
    vd._set_volume(v0)
    vf = vd.get_volume()
    print("  收尾：音量=%.3f（初始 %.3f）" % (vf or -1, v0))
    chk("收尾已复原", vf is not None and abs(vf - v0) < 0.06)
    print("\n  %d 通过 / %d 失败" % (oks, fails))
    return 0 if fails == 0 else 1

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        try:
            sys.exit(selftest())
        finally:

            try:
                VolumeDucker().emergency_restore()
            except Exception as _e:


                print("[volume_duck] 自测收尾复原失败（请手动检查系统音量）: %s"
                      % str(_e)[:110], flush=True)
    vd = VolumeDucker()
    if "--tick" in sys.argv:
        print(json.dumps(vd.tick(), ensure_ascii=False, indent=1))
    elif "--restore" in sys.argv:
        print(json.dumps(vd.emergency_restore(), ensure_ascii=False, indent=1))
    elif "--duck" in sys.argv:
        i = sys.argv.index("--duck")
        lv = float(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 0.2
        print(json.dumps(vd.duck(lv), ensure_ascii=False, indent=1))
    elif "--unduck" in sys.argv:
        print(json.dumps(vd.unduck(), ensure_ascii=False, indent=1))
    else:
        print(json.dumps({"ok": vd.ok, "volume": vd.get_volume(), "mute": vd.get_mute(),
                          "error": vd.last_error()}, ensure_ascii=False, indent=1))
