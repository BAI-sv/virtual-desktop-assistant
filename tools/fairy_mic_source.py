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
                lo = ("混音" in n) or ("Stereo Mix" in n) or ("Stereo input" in n)
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
    """试开每个候选，选信号最强的可用设备（麦克风优先已排序）。"""
    if not devs:
        return None
    best, best_mean = None, -1.0
    for d in devs:
        m = _probe_mean(d["index"])
        if m >= 0:
            if best is None or m > best_mean:
                best, best_mean = d, m
    return best


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
