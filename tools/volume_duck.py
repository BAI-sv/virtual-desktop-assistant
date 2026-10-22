# -*- coding: utf-8 -*-
"""
volume_duck.py —— 音量闪避（压低而不是暂停）

【为什么需要它】
  GSMTC（Windows 媒体控制）**只能暂停/继续，不能调音量**。
  而视频、直播、游戏**不能暂停**（会错过内容），只能用"压低音量"。
  所以调音量必须走 **Core Audio API（WASAPI）** 的 IAudioEndpointVolume。

【怎么调通的（实测，不需要任何额外库）】
  **纯 ctypes 直接调 COM vtable** —— 不用 pycaw、不用 comtypes、不用装东西。
  实测：CoCreateInstance hr=0 / GetDefaultAudioEndpoint hr=0 /
        Activate(IAudioEndpointVolume) hr=0 / GetMasterVolumeLevelScalar = 0.260
  ⚠ **关键坑**：不先调 `CoInitialize` 会报 `0x800401F0`（CO_E_NOTINITIALIZED）。

  实测确认的 vtable 索引（IUnknown 占 0~2）：
    IAudioEndpointVolume[ 7] = SetMasterVolumeLevelScalar(float, LPCGUID)
    IAudioEndpointVolume[ 9] = GetMasterVolumeLevelScalar(float*)
    IAudioEndpointVolume[14] = SetMute(BOOL, LPCGUID)
    IAudioEndpointVolume[15] = GetMute(BOOL*)
    IMMDevice[3]             = Activate
    IMMDeviceEnumerator[4]   = GetDefaultAudioEndpoint

【四条必须做对的规矩】
  1. **只恢复自己压的**（_saved 标记）—— 用户本来把音量调低了，我不能给他调高
  2. **用户中途自己调了就不覆盖** —— 检测当前值和我压下去的值是否一致，不一致说明用户动过
  3. **超时兜底** —— 默认 60 秒必须恢复，否则音量永久压低比不闪避更糟
  4. **任何失败路径都要保证原值被恢复** —— 这是会改系统状态的操作，不能留下烂摊子

【调用示例】（给球的语音链用；本模块不自带集成）
    from volume_duck import VolumeDucker
    vd = VolumeDucker()
    tok = vd.duck(0.2)        # {"ducked":True,"saved":0.26,...}
    ... 说话 ...
    vd.unduck(tok)            # 只恢复自己压的
    vd.tick()                 # 兜底：超时自动恢复（球可以定时调）
    vd.emergency_restore()    # 进程退出/异常时的保险

【用法】
  py -3.12 volume_duck.py                 # 只读：打印当前音量
  py -3.12 volume_duck.py --selftest      # 实测：压低 -> 读回 -> 恢复 -> 再读回
  py -3.12 volume_duck.py --tick          # 兜底检查
  py -3.12 volume_duck.py --restore       # 强制恢复到上次保存的值
"""
import atexit
import ctypes
import json
import os
import sys
import time
import fairy_root

STATE = fairy_root.log("volume_duck_state.json")
TIMEOUT_S = 60          # 兜底：超过这个时间必须恢复

ole32 = ctypes.windll.ole32

# 同一个问题只打印一次（tick() 每秒都可能被调，不能刷日志）
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
    """主输出设备（默认播放设备，例如方糖蓝牙音箱）的音量控制。"""

    def __init__(self):
        self._com_ready = False
        self._vol = None
        self._saved = None          # 我压下去之前的原值
        self._ducked_to = None      # 我压到了多少
        self._ducked_at = 0.0
        self._last_error = ""
        self._open()
        # ★ H4 修复之一：启动自愈。
        # 【为什么必修】原来 __init__ 只 _open()，没有开机自愈；而 emergency_restore()
        #   全库**从不被调用**（无 atexit、球也不调）。后果链：
        #     压到 20% -> 球进程被杀 -> 下次启动不还原 ->
        #     下次 duck() 把 20% 当成"原值"存下 -> **20% 变成永久音量**
        #     （用户会以为音箱坏了，而且越用越小声）。
        #   一上电就还原上次残留的"已压低"记录，链条就断了。
        try:
            _r = self.emergency_restore()
            if _r.get("restored"):
                print("[volume_duck] 启动自愈：音量已还原到 %.2f" % (_r.get("to") or 0),
                      flush=True)
        except Exception as e:
            _warn_once("启动自愈失败: %s" % str(e)[:110])
        # ★ H4 修复之二：注册 atexit —— 正常退出时也还原（进程被杀时靠上面的启动自愈兜）
        global _atexit_done
        if not _atexit_done:
            try:
                atexit.register(self.emergency_restore)
                _atexit_done = True
            except Exception as e:
                _warn_once("注册 atexit 失败: %s" % str(e)[:110])

    # ------------------------------------------------------------ COM
    def _open(self):
        """打开默认播放设备的 IAudioEndpointVolume。失败不抛，记录原因。"""
        try:
            # ★ 必须 CoInitialize，否则 CoCreateInstance 报 0x800401F0
            hr = ole32.CoInitialize(None)
            self._com_ready = True
            # 已经初始化过（S_FALSE / RPC_E_CHANGED_MODE）都算可用
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
            hr = GetDefault(p, 0, 0, ctypes.byref(dev))      # eRender=0, eConsole=0
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

    # ------------------------------------------------------------ 读
    def get_volume(self):
        """当前主音量 0.0~1.0；失败返回 None（不编）。"""
        if not self.ok:
            return None
        try:
            f = ctypes.c_float()
            hr = self._get_v(self._vol, ctypes.byref(f))
            if hr != 0:
                return None
            v = round(float(f.value), 4)
            # ★ 审计修复：取值范围校验。
            #   COM 调用拿到脏值（NaN/inf/>1）时，如果原样返回，
            #   duck() 会把它当"原值"存进令牌 -> unduck/emergency_restore 会写回一个坏值
            #   （音量直接失控）。宁可返回 None（=读不到，调用方会放弃这次闪避）。
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

    # ------------------------------------------------------------ 写
    def _set_volume(self, v):
        v = max(0.0, min(1.0, float(v)))
        hr = self._set_v(self._vol, ctypes.c_float(v), None)
        return hr == 0

    def duck(self, level=0.2, smooth=True, steps=4, gap=0.03):
        """压低到 level。返回 token（给 unduck 用）。

        smooth：分几步降，避免一次性跳到 20% 的"咔"声。**失败一定回滚**。
        """
        if not self.ok:
            return {"ducked": False, "why": "音量接口不可用: %s" % self._last_error}
        cur = self.get_volume()
        if cur is None:
            return {"ducked": False, "why": "读不到当前音量"}
        target = max(0.0, min(1.0, float(level)))

        # 先存原值（规矩：任何失败都要能恢复）
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
                # 没压下去 -> 立刻回滚，不留半吊子状态
                self._set_volume(cur)
                tok["why"] = "压低后读回不符（期望 %.2f 实得 %s），已回滚" % (target, after)
                self._saved = None
            else:
                tok["by_me"] = True          # ★ 规矩1：记住是我压的
                self._save(tok)
        except Exception as e:
            try:
                self._set_volume(cur)        # ★ 规矩4：异常也要恢复
            except Exception as e2:
                # ★ 审计修复：原来静默 pass —— 这是**回滚也失败**的最坏情况
                #   （音量可能停在压低态），必须出声；否则用户只感觉"音量莫名变小"，
                #   而日志里什么都没有（正是 H4 那条事故链的入口）。
                _warn_once("回滚也失败（音量可能停在压低态！）: %s" % str(e2)[:110])
            self._saved = None
            tok["ducked"] = False
            tok["why"] = "异常已回滚: %s" % str(e)[:100]
        return tok

    def unduck(self, token=None):
        """恢复。三条规矩都在这里。"""
        tok = token or self._load()
        if not tok or not tok.get("ducked"):
            return {"ok": True, "restored": False, "why": "没有需要恢复的"}
        if not tok.get("by_me"):
            return {"ok": True, "restored": False, "why": "不是我压的，不动"}
        saved = tok.get("saved")
        if saved is None:
            # ★ 审计修复：清掉令牌。否则 tick() 每秒都会命中这个分支，
            #   永远"无需恢复"却一直留着脏令牌，下次 tick 再来一遍。
            self._save({})
            return {"ok": True, "restored": False, "why": "没记录到原值（不动，已清令牌）"}

        # 规矩2：用户中途自己调过？当前值和我压下去的目标差太多 -> 说明用户动过
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
        """规矩3 兜底：超时还没恢复就强制恢复。"""
        tok = self._load()
        if tok and tok.get("ducked") and tok.get("by_me"):
            if time.time() > (tok.get("deadline") or 0):
                r = self.unduck(tok)
                r["forced_by_timeout"] = True
                return r
        return {"ok": True, "restored": False, "why": "无需兜底"}

    def emergency_restore(self):
        """进程退出/异常时的保险：无条件把保存的值写回去。"""
        try:
            tok = self._load()
            if tok and tok.get("saved") is not None:
                self._set_volume(tok["saved"])
                self._save({})
                return {"ok": True, "restored": True, "to": tok["saved"]}
        except Exception as e:
            # ★ 审计修复：原来静默 pass —— 保险失效却没人知道（正是 H4 的成因之一）
            _warn_once("emergency_restore 失败: %s" % str(e)[:110])
        return {"ok": True, "restored": False}

    # ------------------------------------------------------------ 状态落盘
    def _save(self, d):
        """原子写（临时文件 + replace）。
        ★ 审计修复：原来 except: pass —— 写失败会**静默丢恢复令牌**，
          与"音量永久卡在低处"叠加且无从排查。现在必须出声。
        """
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


# ------------------------------------------------------------------ 自测
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

    # 压低（目标选一个明显不同于当前值的：当前>0.4 就压到 0.2，否则压到 0.5）
    target = 0.2 if (v0 or 0.5) > 0.4 else 0.5
    tok = vd.duck(target, smooth=True, steps=3, gap=0.03)
    after = vd.get_volume()
    print("  duck(%s) -> ducked=%s after=%.3f %s" % (target, tok.get("ducked"), after or -1,
                                                    tok.get("why", "")))
    chk("duck 真的压低（读回接近目标）", after is not None and abs(after - target) < 0.06,
        "after=%s target=%s" % (after, target))
    chk("duck 记录了原值", tok.get("saved") == v0, "saved=%s v0=%s" % (tok.get("saved"), v0))
    chk("duck 打了 by_me 标记", tok.get("by_me") is True)

    # 规矩2：模拟"用户自己调过" -> 不该恢复
    vd._set_volume(0.77 if abs(target - 0.77) > 0.1 else 0.66)
    r = vd.unduck(tok)
    cur = vd.get_volume()
    print("  模拟用户改音量后 unduck -> restored=%s why=%s" % (r.get("restored"), r.get("why")))
    chk("规矩2 用户动过就不覆盖", r.get("restored") is False, "restored=%s" % r.get("restored"))

    # 正常路径：重新 duck 再 unduck，应恢复到原值
    vd._set_volume(v0)                      # 先回到初始
    tok2 = vd.duck(target, smooth=True, steps=3, gap=0.03)
    r2 = vd.unduck(tok2)
    v1 = vd.get_volume()
    print("  正常路径 unduck -> restored=%s to=%s after=%.3f" % (r2.get("restored"),
                                                                r2.get("to"), v1 or -1))
    chk("正常路径恢复成功", r2.get("restored") is True, str(r2))
    chk("恢复到原值", v1 is not None and abs(v1 - v0) < 0.06, "v1=%s v0=%s" % (v1, v0))

    # 规矩3：超时兜底
    vd._save({"ducked": True, "by_me": True, "saved": v0, "target": 0.2,
              "deadline": time.time() - 5})
    vd._set_volume(0.2)
    t = vd.tick()
    vt = vd.get_volume()
    print("  tick 超时兜底 -> restored=%s forced=%s after=%.3f" % (t.get("restored"),
                                                                  t.get("forced_by_timeout"), vt or -1))
    chk("规矩3 超时兜底触发并恢复", t.get("forced_by_timeout") is True, str(t))
    chk("兜底后回到原值", vt is not None and abs(vt - v0) < 0.06, "vt=%s v0=%s" % (vt, v0))

    # 收尾：确保一定回到初始音量（不留烂摊子）
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
            # 万一日志/断言中途退出，也要保证音量回到保存值
            try:
                VolumeDucker().emergency_restore()
            except Exception as _e:
                # ★ 审计修复：原来静默 pass —— 自测收尾都复原不了，说明接口有问题，
                #   必须让人看见（用户可能正戴着耳机，音量被压在低处）。
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
