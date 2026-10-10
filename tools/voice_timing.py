# -*- coding: utf-8 -*-
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import voice_cache as vc

def main():
    per = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    phrases = sorted(vc.all_phrases())
    bands = {
        "短(<=10字)": [s for s in phrases if len(s) <= 10],
        "中(11~20字)": [s for s in phrases if 11 <= len(s) <= 20],
        "长(21~45字)": [s for s in phrases if 21 <= len(s) <= 45],
    }
    print("── 分档抽样（清单共 %d 条）──" % len(phrases))
    for k, v in bands.items():
        print("   %-12s %4d 条" % (k, len(v)))
    print("")

    idx = vc.load_index()
    allrows = []
    for name, pool in bands.items():
        if not pool:
            continue
        picks = random.sample(pool, min(per, len(pool)))
        print("== %s ==" % name)
        for s in picks:
            hs = vc.sha_of(s)
            dst = os.path.join(vc.CACHE_DIR, vc.FILE_PREFIX + hs + ".wav")
            t0 = time.time()
            try:
                b = vc.synth(s)
                dt = time.time() - t0
            except Exception as e:
                print("   ✗ %-28s %s: %s" % (s[:28], type(e).__name__, str(e)[:60]))
                continue
            vc._atomic_write(dst, b, binary=True)
            dur = vc.wav_duration(dst)
            idx[hs] = {"t": s, "f": vc.FILE_PREFIX + hs + ".wav",
                       "d": round(dur, 3), "ts": int(time.time())}
            allrows.append((name, len(s), dt, dur))
            print("   %-30s %2d字  合成 %5.1fs  音频 %4.2fs  比值 %4.1fx"
                  % (s[:30], len(s), dt, dur, dt / max(0.01, dur)))
    vc.save_index(idx)

    print("")
    print("── 汇总与投影 ──")
    tot_n = 0
    tot_s = 0.0
    for name in bands:
        rows = [r for r in allrows if r[0] == name]
        if not rows:
            continue
        avg = sum(r[2] for r in rows) / len(rows)
        n = len(bands[name])
        print("   %-12s 本档 %4d 条 × 平均 %5.1fs ≈ %5.1f 分钟"
              % (name, n, avg, n * avg / 60.0))
        tot_n += n
        tot_s += n * avg
    if tot_n:
        print("   %-12s %4d 条 × 综合 ≈ %5.1f 分钟（%.1f 小时）"
              % ("合计", tot_n, tot_s / 60.0, tot_s / 3600.0))
    have = len([1 for s in phrases if vc.sha_of(s) in idx])
    print("   已缓存 %d / %d 条，还差 %d 条" % (have, len(phrases), len(phrases) - have))
    return 0

if __name__ == "__main__":
    sys.exit(main())
