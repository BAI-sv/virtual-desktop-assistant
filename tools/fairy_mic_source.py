# -*- coding: utf-8 -*-
"""
FairyMicSource —— 兜底麦克风采集模块（2026-10-12）
系统音频引擎（MME/WASAPI/DirectSound）输入端点注册损坏时，
PortAudio 持续流（InputStream）全打不开；但 WDM-KS 一次性 rec 可采。
本模块用 sounddevice.rec 轮询直读 WDM-KS 输入，降采样后按 0.1s 块
喂给 ear 主循环，接口与 pyaudio 流 read() 兼容。
"""
import io, json, os, time, queue, threading
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


def _probe_mean(idx, sr=None, secs=0.5):
    """rec 试开一次，返回平均幅值（>0 且非 NaN 才算可用）。

    ★★ 2026-10-24【悬空输入识别】
      实测本机「麦克风 (Realtek HD Audio Mic input)」在 WDM-KS 直读下
      恒定输出满量程（峰值 0.9999，跟有没有人说话无关）—— 这是接口上
      没接麦克风时放大器自激/驱动返回全 1 的典型特征。
      这样的设备一旦被选中，KWS 听到的全是削波噪声，喊破喉咙都不会醒。
      所以这里把「接近满量程」也判为不可用（返回 -1），
      让 pick_input 继续往下挑真正有起伏的设备。

    ★★ 2026-10-24【探测采样率必须跟实际采集一致】
      原来固定用 48000 探，但 FairyMicSource 用的是设备自己声明的 rate
      （本机 Realtek 报 44100）。两个采样率下设备行为不同：
      48k 探的时候读数正常、44100 采的时候全满档 —— 探测因此漏判。
      改成默认用设备声明的采样率，跟实际采集对齐。

    ★★ 2026-10-24【满档是间歇性的，必须多探几次】
      实测本机 Realtek 麦克风不是每段都满档：0.5 秒里有时抓到 32766，
      有时只读到个位数。单次探测会漏判，于是这个悬空设备又被选回去。
      改成连续采 2 段，任一段满档就判死。
    """
    try:
        if sr is None:
            try:
                info = sd.query_devices(idx)
                sr = int(info.get("default_samplerate") or 48000)
            except Exception:
                sr = 48000
        worst_peak = -1.0
        for _round in range(2):
            a = sd.rec(int(sr * secs), samplerate=sr, channels=1, device=idx, dtype="int16")
            sd.wait()
            arr = np.asarray(a).reshape(-1).astype(np.float32)
            if np.isnan(arr).any():
                return -1.0
            pk = float(np.max(np.abs(arr)))
            if pk > worst_peak:
                worst_peak = pk
            # 任一段满量程（>=0.99）就视为悬空/饱和输入，不可用
            if pk >= 32768.0 * 0.99:
                return -1.0
            time.sleep(0.05)
        arr_last = arr  # 用最后一段算均值
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

    # ★★ 2026-10-24【用户指定设备：环境变量 FAIRY_MIC_INDEX】
    #   本机主板麦克风接口是空的（实测满档自激），蓝牙耳机是唯一能用的，
    #   但自动挑选有时会挑回那台悬空设备。用户选定后用这个口子固定下来：
    #     PowerShell:  $env:FAIRY_MIC_INDEX = '1'; 启动 fairy_ear.py
    #   找不到指定索引就继续走自动挑选，不会因此起不来。
    try:
        _force = (os.environ.get("FAIRY_MIC_INDEX") or "").strip()
        if not _force:
            # 环境变量没设就读 fairy.json 的 ear.mic_index（持久化，重启不丢）
            try:
                _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                with io.open(os.path.join(_root, "fairy.json"), encoding="utf-8") as _f:
                    _cfg = json.load(_f)
                _force = str((_cfg.get("ear") or {}).get("mic_index") or "").strip()
            except Exception:
                _force = ""
        if _force:
            _fi = int(_force)
            for d in devs:
                if int(d.get("index", -1)) == _fi:
                    print("[mic] 用户指定设备 idx %d：%s" % (_fi, d["name"][:42]), flush=True)
                    return d
            print("[mic] 指定的 idx %s 不在设备列表里，回退自动挑选" % _force, flush=True)
    except Exception as _e:
        print("[mic] FAIRY_MIC_INDEX 解析失败(%s)，回退自动挑选" % _e, flush=True)

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
    #    ★★ 2026-10-24【排除 Sound Mapper】
    #      「Microsoft Sound Mapper - Input」是 Windows 的映射层，
    #      不是真实设备，它会把请求转发给当前默认设备 —— 而本机默认
    #      正指向那台满档悬空的 Realtek，走它等于绕回一个死胡同。
    #      探测时它自己的读数（2.1）又会让它挤掉真正的麦克风。
    cands = []
    for d in devs:
        if d["kind"] in ("麦克风", "USB"):
            if "SOUND MAPPER" in (d.get("name") or "").upper():
                continue
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
        # ★★ 2026-10-24【修·降采样把音调改变了，导致喊不醒】
        #   原来写的是 step = sr_src // self.rate，然后 arr[::step] 抽稀：
        #     44100 // 16000 = 2  ->  arr[::2] 得到 22050Hz
        #   但下游（KWS / SenseVoice）都按 16000Hz 解释这段数据，
        #   等于把声音【加速 1.38 倍、音调升高】，唤醒词「Fairy」彻底变形。
        #   实测这台机器麦克风报的 rate 正是 44100，所以一直走的就是这条错路。
        #   改成按比例线性插值重采样，得到真正的 self.rate。
        need_resample = (sr_src != self.rate)

        # ★★ 2026-10-24【改成持久流 —— 蓝牙麦克风原来慢 14 倍】
        #   原实现每 0.1 秒调一次 sd.rec() + sd.wait()，等于每次采集都
        #   重新打开/关闭一次音频流。有线声卡开流只要几毫秒，但蓝牙 HFP
        #   开流要将近 1 秒 —— 实测每块 1.4 秒才出来（应该 0.1 秒）。
        #   后果：队列长期空，read() 一直超时返回静音块，
        #   ear 的「300 块巡检」要等 1500 秒才打印一次，看起来像卡死。
        #   现在改成 sd.InputStream 持久打开，回调里按 hop 切块入队。
        block = max(64, int(sr_src * 0.1))      # 100ms 一次回调

        def _on_audio(indata, _frames, _t, _status):
            try:
                arr = np.asarray(indata).reshape(-1).astype(np.float32) / 32768.0
                if need_resample and arr.size > 1:
                    n_out = int(round(arr.size * float(self.rate) / sr_src))
                    if n_out > 1:
                        xi = np.linspace(0.0, arr.size - 1.0, n_out)
                        arr = np.interp(xi, np.arange(arr.size, dtype=np.float32), arr
                                        ).astype(np.float32)
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

        try:
            with sd.InputStream(samplerate=sr_src, channels=1, device=dev,
                                dtype="int16", blocksize=block,
                                callback=_on_audio):
                while not self._stop.is_set():
                    time.sleep(0.1)
            return
        except Exception as e:
            # 持久流打不开就退回原来的轮询方式（慢，但总比没有强）
            self._err = "InputStream 打不开(%s)，退回轮询采集" % e
            try:
                print("[mic] %s" % self._err, flush=True)
            except Exception:
                pass

        while not self._stop.is_set():
            try:
                a = sd.rec(frames, samplerate=sr_src, channels=1, device=dev, dtype="int16")
                sd.wait()
                arr = np.asarray(a).reshape(-1).astype(np.float32) / 32768.0
                if need_resample and arr.size > 1:
                    n_out = int(round(arr.size * float(self.rate) / sr_src))
                    if n_out > 1:
                        xi = np.linspace(0.0, arr.size - 1.0, n_out)
                        arr = np.interp(xi, np.arange(arr.size, dtype=np.float32), arr
                                        ).astype(np.float32)
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
