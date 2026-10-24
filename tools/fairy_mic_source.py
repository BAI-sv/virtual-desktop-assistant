# -*- coding: utf-8 -*-
"""
FairyMicSource —— 兜底麦克风采集模块（2026-10-12）
系统音频引擎（MME/WASAPI/DirectSound）输入端点注册损坏时，
PortAudio 持续流（InputStream）全打不开；但 WDM-KS 一次性 rec 可采。
本模块用 sounddevice.rec 轮询直读 WDM-KS 输入，降采样后按 0.1s 块
喂给 ear 主循环，接口与 pyaudio 流 read() 兼容。
"""
import io, time, queue, threading
import numpy as np

try:
    import sounddevice as sd
except Exception as e:
    sd = None
    _SD_ERR = str(e)


def list_inputs():
    """返回 [{index,name,kind,ch,rate}...]，只列 WDM-KS 真输入（排除回环/输出）。"""
    out = []
    if sd is None:
        return out
    try:
        for hi in range(len(sd.query_hostapis())):
            h = sd.query_hostapis()[hi]
            for di in h["devices"]:
                try:
                    d = sd.query_devices(di)
                except Exception:
                    continue
                if d["max_input_channels"] <= 0:
                    continue
                n = d["name"] or ""
                # ★★ 2026-10-20【修复·唤不醒】补「主声音捕获」
                #   这台机器（RX 9070 XT + Realtek）上，Stereo Mix 在 WDM-KS
                #   驱动下显示的名字是「主声音捕获驱动程序」，不含「混音」
                #   也不含「Stereo」，老过滤条件漏掉了它。
                #   它录的是【系统播放的声音】而不是麦克风 —— 喊「Fairy」时
                #   电脑并没有在播「Fairy」，KWS 永远收不到，这就是
                #   「喊了没反应」的真正根因（日志里唤醒记录一条都没有）。
                #   顺带补上「What U Hear」（创新声卡对同一个东西的叫法）。
                lo = (("混音" in n) or ("Stereo Mix" in n) or ("Stereo input" in n)
                      or ("主声音捕获" in n) or ("What U Hear" in n))
                if lo:
                    continue
                kind = ("蓝牙免提" if ("Hands" in n or "耳机" in n)
                        else ("USB" if "USB" in n.upper()
                              else ("线路" if ("Line" in n or "线路" in n)
                                    else ("麦克风" if "麦克风" in n or "Mic" in n else "其他"))))
                out.append({"index": di, "name": n, "lo": False, "kind": kind,
                            "ch": int(d["max_input_channels"]),
                            "rate": int(d.get("default_samplerate") or 48000),
                            "hostapi": hi, "hostname": h["name"]})
    except Exception:
        pass
    # 麦克风优先，线路/其他次之，蓝牙最后
    order = {"麦克风": 0, "线路": 1, "USB": 2, "其他": 3, "蓝牙免提": 9}
    out.sort(key=lambda x: order.get(x["kind"], 9))
    return out


def _probe_mean(idx, sr=48000, secs=0.2):
    """rec 试开一次，返回平均幅值（>0 且非 NaN 才算可用）。"""
    try:
        a = sd.rec(int(sr * secs), samplerate=sr, channels=1, device=idx, dtype="int16")
        sd.wait()
        arr = np.asarray(a).reshape(-1).astype(np.float32)
        if np.isnan(arr).any():
            return -1.0
        return float(np.mean(np.abs(arr)))
    except Exception:
        return -1.0


def pick_input(devs):
    """★ 2026-10-17【修复·听不见人声】优先用【系统默认输入设备】（用户明确要求
    "直接用默认的"）—— 之前用 Sound Mapper(idx0) 采集不到人声；其次才按 kind
    挑真麦克风（信号可用即可收），不再按"信号最强"选（会误选削波的线路输入）。
    顺序：AMD 降噪虚拟麦 → 默认输入 → 真麦克风 → 线路/其他 → 蓝牙免提（最后）。

    ★★ 2026-10-18【AMD 降噪】2026-10-18 实测：开启 AMD Noise Suppression 后，
      系统里会多出一个录音设备「麦克风 (AMD Streaming Audio Device)」，
      **那才是降噪后的输出**。同一个环境下对比（各录 3 秒）：
          USB Audio Device 原始麦：峰值 0.395 有效值 0.244 波动 1.0 倍（全是噪音）
          AMD Streaming 虚拟麦  ：峰值 0.129 有效值 0.008 波动 999 倍（安静，有起伏）
      底噪差了 30 倍。而 Fairy 原来优先用"系统默认输入"（=USB 原始麦），
      等于把 AMD 降噪绕过去了 —— 这就是"降噪开了却没效果"的真正原因。
      所以现在只要系统里有这个虚拟麦，就直接用它。
    """
    if not devs:
        return None

    # ★★ 2026-10-20【修复·唤不醒】AMD 虚拟麦加信号门槛
    #   原来第 0 步只要 _probe_mean >= 0（能打开就算）就直接选，
    #   根本不看信号强弱。2026-10-20 实测：
    #       AMD Streaming 虚拟麦  峰值 0.029（几乎没输入）
    #       USB Audio Device 麦克风 峰值 0.307（正常）
    #   结果 Fairy 一直挂在那个没信号的虚拟麦上，
    #   麦克风峰值长期 0.02~0.03，KWS 阈值 0.04 永远达不到，
    #   喊「Fairy」怎么都不应 —— 这就是「喊了没反应」的根因。
    #   现在要求虚拟麦的信号至少达到 MIN_USABLE，不够就继续往下挑。
    #   ★ 量纲注意：_probe_mean 用的是 int16 采样，返回的是 0~32768 的
    #     平均绝对幅值，不是 0~1。2026-10-20 各设备实测：
    #         AMD Streaming      421 / 588   （弱，等于没输入）
    #         USB Audio Device  5200 / 5252  （正常）
    #         Realtek Mic      16275         （最清楚）
    #     所以门槛取 2000：AMD 那两个会被刷掉，USB/Realtek 都能过。
    MIN_USABLE = 2000.0

    # 0) AMD 降噪虚拟麦克风（信号够才用）
    for d in devs:
        nm = (d.get("name") or "").upper()
        if "AMD STREAMING" in nm or "AMD 降噪" in nm:
            try:
                lvl = _probe_mean(d["index"])
                if lvl >= MIN_USABLE:
                    return d
            except Exception:
                pass
    # 1) 系统默认输入设备（用户要"直接用默认的"）
    #    ★★ 2026-10-24【修复·默认设备可能是哑的】
    #    原来这里判 `_probe_mean(di) >= 0`（能打开就算），
    #    结果这台机器上 Windows 默认输入是「麦克风 (AMD Streaming Audio Device)」，
    #    实测峰值恒为 0.0000 —— 它是显卡的 HDMI/DP 音频设备，根本没有输入。
    #    Fairy 挂上去之后 KWS 5700 块一次没命中，喊破喉咙也没反应。
    #    现在和上面 AMD 那步用同一个门槛：信号够 MIN_USABLE 才认，
    #    不够就记一笔原因、继续往下挑真麦克风。
    try:
        if sd is not None and sd.default.device and sd.default.device[0] is not None:
            di = sd.default.device[0]
            for d in devs:
                if d["index"] == di:
                    lvl = _probe_mean(di)
                    if lvl >= MIN_USABLE:
                        return d
                    try:
                        print("[mic] 系统默认输入「%s」信号 %.0f < 门槛 %.0f，跳过"
                              % ((d.get("name") or "?")[:40], lvl, MIN_USABLE),
                              flush=True)
                    except Exception:
                        pass
                    break
    except Exception:
        pass
    # 2) 真麦克风 / USB，按信号从强到弱挑（不再拿第一个能打开的）
    cands = []
    for d in devs:
        if d["kind"] in ("麦克风", "USB"):
            try:
                lvl = _probe_mean(d["index"])
            except Exception:
                lvl = -1.0
            if lvl >= 0:
                cands.append((lvl, d))
    if cands:
        cands.sort(key=lambda x: -x[0])
        return cands[0][1]
    # 3) 线路 / 其他
    #    ★★ 2026-10-24：这里原来也是 `>= 0`（能打开就算），
    #    结果 idx 0「Microsoft Sound Mapper - Input」信号恒为 0 却被选中，
    #    根本走不到下面的兜底排序。现在同样要求 MIN_USABLE。
    for d in devs:
        if d["kind"] in ("线路", "其他"):
            try:
                if _probe_mean(d["index"]) >= MIN_USABLE:
                    return d
            except Exception:
                pass

    # 4) 兜底：能打开的设备里挑【信号最强】的
    #    ★★ 2026-10-24：原来直接 `return` 第一个能打开的，
    #    结果选中 idx 0「Microsoft Sound Mapper」—— 它的信号恒为 0，
    #    等于给 Fairy 塞了个聋耳朵。现在按实测信号排序，
    #    至少能挑到蓝牙耳机那种「有微弱起伏」的设备。
    best = None
    best_lvl = -1.0
    for d in devs:
        try:
            lvl = _probe_mean(d["index"])
        except Exception:
            continue
        if lvl > best_lvl:
            best_lvl = lvl
            best = d
    if best is not None:
        try:
            print("[mic] 没有达标设备（门槛 %.0f），退而选信号最强的：%s（%.0f）"
                  % (MIN_USABLE, (best.get("name") or "?")[:40], best_lvl), flush=True)
        except Exception:
            pass
        return best
    return None


class FairyMicSource:
    """
    轮询采集源：rec(0.5s@48k) -> 降采样到目标率 -> 按 hop 切块 -> 队列。
    read(hop) 阻塞返回 float32 (hop,1) 块，与 pyaudio 流 read 兼容。
    """

    def __init__(self, device, rate=16000, hop=1600, frame_s=0.5):
        self.device = device
        self.rate = rate
        self.hop = hop
        self.frame_s = frame_s
        self._q = queue.Queue(maxsize=64)
        self._stop = threading.Event()
        self._thread = None
        self._err = None
        self._leftover = np.zeros(0, dtype=np.float32)
        if sd is None:
            raise RuntimeError("sounddevice 不可用: %s" % (_SD_ERR if "_SD_ERR" in globals() else "?"))
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        dev = self.device["index"]
        sr_src = int(self.device.get("rate") or 48000)
        if sr_src not in (48000, 44100, 24000):
            sr_src = 48000
        frames = int(sr_src * self.frame_s)
        step = sr_src // self.rate  # 降采样抽稀步长
        while not self._stop.is_set():
            try:
                a = sd.rec(frames, samplerate=sr_src, channels=1, device=dev, dtype="int16")
                sd.wait()
                arr = np.asarray(a).reshape(-1).astype(np.float32) / 32768.0
                if arr.size >= step:
                    arr = arr[::step]           # 简单降采样到 self.rate
                else:
                    arr = np.repeat(arr, max(1, self.rate // sr_src))
                # 与上一帧余数拼接后按 hop 切块
                data = np.concatenate([self._leftover, arr])
                self._leftover = data[self.hop * (len(data) // self.hop):]
                for k in range(len(data) // self.hop):
                    blk = data[k * self.hop:(k + 1) * self.hop].copy().reshape(-1, 1)
                    try:
                        self._q.put_nowait(blk)
                    except queue.Full:
                        try:
                            self._q.get_nowait()
                        except Exception:
                            pass
                        try:
                            self._q.put_nowait(blk)
                        except Exception:
                            pass
            except Exception as e:
                self._err = str(e)
                time.sleep(0.5)

    def read(self, hop=None, exception_on_overflow=False):
        n = hop or self.hop
        try:
            blk = self._q.get(timeout=5.0)
        except queue.Empty:
            if self._err:
                raise IOError("FairyMicSource: %s" % self._err)
            # 静音块（线程可能短暂卡顿）
            return np.zeros((n, 1), dtype=np.float32)
        if blk.shape[0] != n:
            # 补齐/截断对齐
            if blk.shape[0] < n:
                blk = np.concatenate([blk, np.zeros((n - blk.shape[0], 1), dtype=np.float32)])
            else:
                blk = blk[:n]
        return blk

    def stop(self):
        self._stop.set()


if __name__ == "__main__":
    devs = list_inputs()
    print("WDM-KS 输入设备：")
    for d in devs:
        print("  idx %-3d %-6s %s (ch%d @%d)" % (d["index"], d["kind"], d["name"][:44], d["ch"], d["rate"]))
    p = pick_input(devs)
    print("选定：", p)
    if p:
        src = FairyMicSource(p, rate=16000, hop=1600)
        for i in range(10):
            b = src.read(1600)
            lv = float(np.max(np.abs(b)))
            print("  blk%d peak=%.4f" % (i, lv))
        src.stop()
