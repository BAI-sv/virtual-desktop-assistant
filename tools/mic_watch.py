# -*- coding: utf-8 -*-
"""
mic_watch.py —— 麦克风/耳机 插拔实时提示（第 2 层：探测 + 播报）

【用户原话】"我的蓝牙耳机连接了，为什么你没提示和提示音"
【怎么分层】
  第 1 层 mic_logic.py = 纯判断（说什么、该不该说）
  第 2 层 本文件 = 枚举设备、真试开验证、分类、调用 CNSH 判断、落通知
  ⚠ CNSH 没有字符串操作，所以【设备名分类】在 Python 做，把 kind 码传给 CNSH。

【kind 码】1 板载3.5mm  2 USB   3 蓝牙免提  4 无线接收器  5 阵列  9 回环/镜像  0 未知

【用法】
  py -3.12 mic_watch.py --run          常驻监听（默认）
  py -3.12 mic_watch.py --once         只探一次并打印，退出
  py -3.12 mic_watch.py --selftest      注入假设备，验证 CNSH 判断（不依赖真插拔）
  py -3.12 mic_watch.py --status-json   输出一行 JSON
"""
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
POLL_S = 10          # 多久看一次（名字枚举很快）
BOOT_GRACE_S = 10    # 启动后多久才开始提示
SAME_DEV_S = 60      # 同一设备最短提示间隔

# 采样率阶梯（记忆里的实测要点：蓝牙免提常只支持 8k/16k）
SR_LADDER = [16000, None, 48000, 44100, 32000, 8000]


# ---------------------------------------------------------------- CNSH 层
def load_logic():
    """加载 CNSH 编译产物（纯判断）。失败返回 None，调用方要好降级。"""
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


# ---------------------------------------------------------------- 分类（字符串，Python 做）
LOOPBACK_KW = ("立体声混音", "Stereo Mix", "Sound Mapper", "主声音捕获", "Nahimic",
               "mirroring", "回环", "Loopback", "主声音驱动程序")
BT_KW = ("Hands-Free", "Hands Free", "免提", "蓝牙", "Bluetooth", "bthhfenum")
USB_KW = ("USB", "麦克风阵列", "Array", "Microphone (", "无线", "Wireless", "接收器", "Dongle")


def classify(name):
    """设备名 -> kind 码。这是 CNSH 做不了的字符串活，放 Python。"""
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


# ---------------------------------------------------------------- 真试开（判断能不能用）
def probe_open(dev_index, name, verbose=False):
    """逐个试开 + 采样率阶梯。返回 (能用吗, 用到的采样率, 说明)。"""
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
    """枚举当前输入设备 -> {名字: 索引}。只用名字做快速差异。"""
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


# ---------------------------------------------------------------- 状态/通知
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
    """球没有可被外部调用的播报接口 -> 落一份通知文件，由球读取后播报。"""
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


# ---------------------------------------------------------------- 核心：一次扫描
class Watcher:
    def __init__(self):
        self.t0 = time.time()
        self.known = {}          # 名字 -> kind（已知设备）
        self.said = {}           # 名字 -> 上次提示时间
        self.changes = 0
        self.real_mic = False
        self.best = None         # (名字, kind, 采样率)
        self.mod = load_logic()

    def scan(self, deep_when_new=True, verbose=False):
        cur = enum_inputs()
        new_names = [n for n in cur if n not in self.known]
        gone_names = [n for n in self.known if n not in cur]
        events = []
        now = time.time()

        # 1) 先把新出现、名字像"真麦克风"的做深探（1~3 秒，所以只对新的做）
        deep = {}
        if deep_when_new:
            for n in new_names:
                k, desc = classify(n)
                if k == 9:
                    deep[n] = (False, None)      # 回环不试开（省时间）
                    continue
                ok, sr, why = probe_open(cur[n], n, verbose)
                deep[n] = (ok, sr)
                if verbose:
                    print("[mic]   深探 %-44s kind=%d(%s) 能用=%s sr=%s" % (n[:44], k, desc, ok, sr), flush=True)

        # 2) 逐个判断该不该提示
        for n in new_names:
            k, desc = classify(n)
            ok, sr = deep.get(n, (False, None))
            self.known[n] = k
            self.changes += 1
            # 只有"真能打开的真麦克风"才算数
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
    """不依赖真插拔：喂假设备列表，验证 CNSH 判断 + 回环保护 + 节流。"""
    m = load_logic()
    if m is None:
        print("✗ 没有 mic_logic.py（这个文件应随项目提供，请检查是否被误删）")
        return 1
    print("用例1：新出现【蓝牙耳机 Hands-Free】")
    for nm in ("耳机 (SANSUI  W68 Hands-Free AG Audio)",):
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
    k, _ = classify("耳机 (SANSUI  W68 Hands-Free AG Audio)")
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
        # 首次扫描会"把现有设备当基线"，但 --once 的用途是【验证耳机麦克风能不能用】
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

    # --run
    print("[mic] 开始监听（每 %ds 一次；启动 %ds 内不提示）…" % (POLL_S, BOOT_GRACE_S), flush=True)
    w.scan(deep_when_new=True, verbose=a.verbose)      # 首次 = 基线
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
