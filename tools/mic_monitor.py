# -*- coding: utf-8 -*-
"""
mic_monitor.py —— 实时麦克风电平监控（调音量专用）

★ 为什么需要这个（2026-10-20 实测）
  同一天里，同一个 USB 麦克风的峰值从 0.05 跳到 1.00，20 倍波动：
      太安静（0.05）  -> 唤醒词模型收不到，喊破嗓子也没反应
      太大声（1.00）  -> 音频削波成方波，语音识别把「打开bilibili」听成「滋味大了」
  商业音箱有硬件级自动增益（AGC）能稳住，普通 USB 麦克风没有，
  只能靠系统音量手动调。这个脚本就是让你边调边看的。

★ 目标区间
  0.15 ~ 0.40   正常说话   ← 最好
  0.40 ~ 0.85   偏大，勉强能用
  > 0.90        削波，识别必错
  < 0.05        太安静，唤醒不了

用法：
  python mic_monitor.py                 监控 30 秒
  python mic_monitor.py --secs 60       监控 60 秒
  python mic_monitor.py --dev 9         换设备
"""
import argparse
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import sounddevice as sd


def bar(level, width=28):
    """把 0~1 的电平画成条。"""
    n = int(max(0.0, min(1.0, level)) * width)
    return "[" + "#" * n + "-" * (width - n) + "]"


def judge(v):
    """返回 (中文说明, 短标记)。"""
    if v >= 0.90:
        return "削波！调低音量", "!!"
    if v >= 0.45:
        return "偏大", "ok"
    if v >= 0.12:
        return "刚好", "ok"
    if v >= 0.04:
        return "偏小", ".."
    return "太安静", "--"


def main():
    ap = argparse.ArgumentParser(description="实时麦克风电平监控")
    ap.add_argument("--dev", type=int, default=1, help="设备索引")
    ap.add_argument("--secs", type=float, default=30.0, help="监控秒数")
    ap.add_argument("--rate", type=int, default=16000)
    ap.add_argument("--chunk", type=float, default=0.1, help="每次采样时长")
    a = ap.parse_args()

    n = int(a.rate * a.chunk)
    peak_all = 0.0
    t0 = time.time()

    print("设备 idx=%d   采样率 %d   监控 %.0f 秒" % (a.dev, a.rate, a.secs))
    print("正常说话，把条子调进 [0.15 ~ 0.40] 这一段")
    print("调完后按 Ctrl+C 提前结束")
    print()

    try:
        while time.time() - t0 < a.secs:
            blk = sd.rec(n, samplerate=a.rate, channels=1, device=a.dev, dtype="int16")
            sd.wait()
            arr = np.abs(np.asarray(blk).reshape(-1).astype(np.float32)) / 32768.0
            pk = float(arr.max())
            peak_all = max(peak_all, pk)
            desc, _flag = judge(pk)
            print("\r  %s  %.3f  %-14s" % (bar(pk), pk, desc), end="", flush=True)
        print()
    except KeyboardInterrupt:
        print()
    except Exception as e:
        print()
        print("  失败: %s: %s" % (type(e).__name__, e))
        return 1

    print()
    print("  整个过程最大峰值 %.3f  ->  %s" % (peak_all, judge(peak_all)[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
