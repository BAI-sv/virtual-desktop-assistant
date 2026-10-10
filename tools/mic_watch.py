# -*- coding: utf-8 -*-
import argparse
import importlib.util
import json
import os
import re
import sys
import time
import fairy_root

T = os.path.dirname(os.path.abspath(__file__))
LOG = fairy_root.WORK_LOGS
STATE = fairy_root.log("mic_watch.json")
NOTICE = fairy_root.log("mic_notice.txt")
POLL_S = 10
BOOT_GRACE_S = 10
SAME_DEV_S = 60


SR_LADDER = [16000, None, 48000, 44100, 32000, 8000]


def load_logic():
    p = os.path.join(T, "mic_logic.py")
    if not os.path.exists(p):
        print("[mic] 找不到 mic_logic.py（这个文件应随项目提供，请检查是否被误删）", flush=True)
        return None
    try:
        s = importlib.util.spec_from_file_location("mic_logic", p)
        m = importlib.util.module_from_spec(s)
        sys.modules["mic_logic"] = m
        s.loader.exec_module(m)
        return m
    except Exception as e:
        print("[mic] 加载 mic_logic 失败: %s: %s" % (type(e).__name__, e), flush=True)
        return None


LOOPBACK_KW = ("立体声混音", "Stereo Mix", "Sound Mapper", "主声音捕获", "Nahimic",
               "mirroring", "回环", "Loopback", "主声音驱动程序")
BT_KW = ("Hands-Free", "Hands Free", "免提", "蓝牙", "Bluetooth", "bthhfenum")
USB_KW = ("USB", "麦克风阵列", "Array", "Microphone (", "无线", "Wireless", "接收器", "Dongle")

def classify(name):
    n = str(name or "")
    for k in LOOPBACK_KW:
        if k.lower() in n.lower():
            return 9, "回环/镜像"
    for k in BT_KW:
        if k.lower() in n.lower():
            return 3, "蓝牙免提"
    for k in USB_KW:
        if k.lower() in n.lower():
            return 2, "USB/无线"
    if ("麦克风" in n) or ("Microphone" in n) or ("Microphone" in n):
        return 1, "板载3.5mm"
    return 0, "未知"


def probe_open(dev_index, name, verbose=False):
    try:
        import sounddevice as sd
    except Exception as e:
        return False, None, "sounddevice 不可用: %s" % e
    try:
        info = sd.query_devices(dev_index)
        native = int(info.get("default_samplerate") or 0)
    except Exception as e:
        return False, None, "query 失败: %s" % (type(e).__name__,)
    tried = []
    for sr in SR_LADDER:
        s = native if sr is None else sr
        if not s:
            continue
        if s in tried:
            continue
        tried.append(s)
        try:
            st = sd.InputStream(device=dev_index, samplerate=int(s), channels=1)
            st.start()
            st.stop()
            st.close()
            return True, int(s), "能打开"
        except Exception as e:
            if verbose:
                print("[mic]   %s @%s 打不开: %s" % (str(name)[:40], s, str(e)[:70]), flush=True)
            continue
    return False, None, "所有采样率都打不开（试过 %s）" % tried

def enum_inputs():
    try:
        import sounddevice as sd
    except Exception as e:
        print("[mic] sounddevice 不可用: %s" % e, flush=True)
        return {}
    out = {}
    try:
        for i, d in enumerate(sd.query_devices()):
            if int(d.get("max_input_channels") or 0) > 0:
                nm = str(d.get("name") or "").strip()
                if nm:
                    out[nm] = i
    except Exception as e:
        print("[mic] 枚举失败: %s: %s" % (type(e).__name__, e), flush=True)
    return out


def write_state(obj):
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE)
    except Exception as e:
        print("[mic] 写状态失败: %s: %s" % (type(e).__name__, e), flush=True)

def post_notice(text, kind):
    try:
        os.makedirs(os.path.dirname(NOTICE), exist_ok=True)
        tmp = NOTICE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps({"text": text, "kind": kind,
                                "ts": time.strftime("%Y-%m-%d %H:%M:%S")},
                               ensure_ascii=False))
        os.replace(tmp, NOTICE)
        print("[mic] 已落通知文件: %s" % text, flush=True)
    except Exception as e:
        print("[mic] 写通知失败: %s: %s" % (type(e).__name__, e), flush=True)


class Watcher:
    def __init__(self):
        self.t0 = time.time()
        self.known = {}
        self.said = {}
        self.changes = 0
        self.real_mic = False
        self.best = None
        self.mod = load_logic()

    def scan(self, deep_when_new=True, verbose=False):
        cur = enum_inputs()
        new_names = [n for n in cur if n not in self.known]
        gone_names = [n for n in self.known if n not in cur]
        events = []
        now = time.time()


        deep = {}
        if deep_when_new:
            for n in new_names:
                k, desc = classify(n)
                if k == 9:
                    deep[n] = (False, None)
                    continue
                ok, sr, why = probe_open(cur[n], n, verbose)
                deep[n] = (ok, sr)
                if verbose:
                    print("[mic]   深探 %-44s kind=%d(%s) 能用=%s sr=%s" % (n[:44], k, desc, ok, sr), flush=True)


        for n in new_names:
            k, desc = classify(n)
            ok, sr = deep.get(n, (False, None))
            self.known[n] = k
            self.changes += 1

            if not ok and k != 9:
                print("[mic] 新增 %s（%s）但打不开 -> 不提示" % (n[:50], desc), flush=True)
                continue
            if ok:
                self.real_mic = True
                self.best = (n, k, sr)
            if self.mod is None:
                continue
            try:
                if not self.mod.该不该提示(int(now - self.t0), int(k), int(now - self.said.get(n, 0))):
                    print("[mic] %s 判断为不该提示（回环/未启动满/节流）" % n[:44], flush=True)
                    continue
                msg = self.mod.该提示什么(int(k), True)
            except Exception as e:
                print("[mic] CNSH 判断出错: %s: %s" % (type(e).__name__, e), flush=True)
                continue
            if msg:
                self.said[n] = now
                events.append({"name": n, "kind": k, "new": True, "text": msg})
                post_notice(msg, k)

        for n in gone_names:
            k = self.known.pop(n, 0)
            self.changes += 1
            if self.mod is None:
                continue
            try:
                if not self.mod.该不该提示(int(now - self.t0), int(k), int(now - self.said.get(n, 0))):
                    continue
                msg = self.mod.该提示什么(int(k), False)
            except Exception:
                continue
            if msg:
                self.said[n] = now
                events.append({"name": n, "kind": k, "new": False, "text": msg})
                post_notice(msg, k)

        write_state({"real_mic": self.real_mic, "device": (self.best or ["", 0, None])[0],
                     "kind": (self.best or ["", 0, None])[1],
                     "samplerate": (self.best or ["", 0, None])[2],
                     "changes": self.changes, "inputs": len(cur),
                     "events": events[-5:], "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
        return events, cur

def selftest():
    m = load_logic()
    if m is None:
        print("✗ 没有 mic_logic.py（这个文件应随项目提供，请检查是否被误删）")
        return 1
    print("用例1：新出现【蓝牙耳机 Hands-Free】")
    for nm in ("耳机 (<BT_HEADSET> Hands-Free AG Audio)",):
        k, d = classify(nm)
        ok = m.该不该提示(30, k, 999)
        msg = m.该提示什么(k, True) if ok else "(不该说)"
        print("   %-46s kind=%d(%s) 该说=%s -> %s" % (nm[:46], k, d, ok, msg))
    print("用例2：新出现【立体声混音（回环）】—— 必须拒绝")
    for nm in ("立体声混音 (Realtek(R) Audio)", "Sound Mapper - Input", "Nahimic mirroring device"):
        k, d = classify(nm)
        ok = m.该不该提示(30, k, 999)
        print("   %-46s kind=%d(%s) 该说=%s  %s" % (nm[:46], k, d, ok, "✓ 正确拒绝" if not ok else "✗ 危险！会自我对话"))
    print("用例3：拔掉蓝牙耳机（移除事件）")
    k, _ = classify("耳机 (<BT_HEADSET> Hands-Free AG Audio)")
    print("   移除 kind=%d -> %s" % (k, m.该提示什么(k, False)))
    print("用例4：节流与启动保护")
    print("   启动5秒就要说蓝牙  -> 该说=%s（应 False）" % m.该不该提示(5, 3, 999))
    print("   启动30秒但10秒前刚说过 -> 该说=%s（应 False）" % m.该不该提示(30, 3, 10))
    print("   启动30秒、距上次999   -> 该说=%s（应 True）" % m.该不该提示(30, 3, 999))
    return 0

def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--status-json", action="store_true")
    ap.add_argument("--seconds", type=int, default=30, help="--run 跑多久（默认30秒，便于测试）")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        return selftest()
    if a.status_json:
        if os.path.exists(STATE):
            print(open(STATE, encoding="utf-8").read().replace("\n", " "))
        else:
            print(json.dumps({"real_mic": False, "device": "", "note": "还没跑过"}, ensure_ascii=False))
        return 0

    w = Watcher()
    if a.once:

        cur = enum_inputs()
        print("当前输入设备 %d 个：" % len(cur))
        best = None
        for nm, idx in sorted(cur.items()):
            k, desc = classify(nm)
            ok, sr, why = probe_open(idx, nm, True)
            print("   %-50s kind=%d(%s) 能打开=%s sr=%s" % (nm[:50], k, desc, ok, sr))
            if ok and k not in (9, 0) and best is None:
                best = (nm, k, sr)
        if best:
            print("\n★ 判定：真麦克风 = %s（%s）@ %s Hz —— 能打开，Fairy 能听见" % (best[0][:44], best[1], best[2]))
        else:
            print("\n★ 判定：没找到可用的真麦克风（只有回环或都打不开）")
        return 0


    print("[mic] 开始监听（每 %ds 一次；启动 %ds 内不提示）…" % (POLL_S, BOOT_GRACE_S), flush=True)
    w.scan(deep_when_new=True, verbose=a.verbose)
    print("[mic] 基线已建立：%d 个输入设备（本次不提示）" % len(w.known), flush=True)
    t_end = time.time() + max(5, a.seconds)
    while time.time() < t_end:
        time.sleep(POLL_S)
        ev, cur = w.scan(deep_when_new=True, verbose=a.verbose)
        for e in ev:
            print("[mic] ★提示: %s   <- %s" % (e["text"], e["name"][:50]), flush=True)
    print("[mic] 退出（本次观测到 %d 次变化）" % w.changes, flush=True)
    return 0

if __name__ == "__main__":
    os.makedirs(LOG, exist_ok=True)
    sys.exit(main())
