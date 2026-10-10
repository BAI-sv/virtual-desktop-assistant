# -*- coding: utf-8 -*-
import os
import re
import sys
import time
import fairy_root

LOGS = [
    os.path.join(fairy_root.WORK_LOGS, "fairy_ball.py.log"),
    os.path.join(fairy_root.WORK_LOGS, "fairy_ball.log"),
    os.path.join(fairy_root.WORK_LOGS, "fairy_ear.log"),
]

RE_ORIG = re.compile(r"播放 ([0-9a-f]{16})\.wav")
RE_CLONE = re.compile(r"播放 fairy_say\.wav")
RE_EDGE = re.compile(r"播放 fairy_say\.mp3")
RE_GEN = re.compile(r"语音=克隆路由\(indextts-clone\)")
RE_VOICE = re.compile(r"语音=")


RE_CACHE = re.compile(r"播放 cache_([0-9a-f]{16})\.wav")

RE_FB = re.compile(r"语音=原声兜底")

def _ball_plays_original():
    try:
        with open(os.path.join(fairy_root.TOOLS, "fairy_ball.py"), encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r"\s*PLAY_GAME_ORIGINAL\s*=\s*(True|False)", line)
                if m:
                    return m.group(1) == "True"
    except Exception:
        pass
    return None

def audit(path, tail=0):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()
    mtime = os.path.getmtime(path)
    if tail and len(lines) > tail:
        lines = lines[-tail:]
    t = "\n".join(lines)
    orig = len(RE_ORIG.findall(t))
    clone = len(RE_CLONE.findall(t))
    edge = len(RE_EDGE.findall(t))
    gen = len(RE_GEN.findall(t))
    cache = len(RE_CACHE.findall(t))
    fb = len(RE_FB.findall(t))
    total = orig + clone + edge + cache
    return {
        "path": path,
        "lines": len(lines),
        "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime)),
        "original": orig,
        "clone_file": clone,
        "edge": edge,
        "cache": cache,
        "fallback_orig": fb,
        "clone_generated": gen,
        "total_played": total,
        "clone_pct": (100.0 * (orig + clone + cache) / total) if total else 0.0,
    }

def show(r):
    if r is None:
        return
    print("  %s" % r["path"])
    print("    lines=%d  mtime=%s" % (r["lines"], r["mtime"]))
    print("    GAME ORIGINAL  : %d" % r["original"])
    print("    CLONE cache    : %d   (pre-synthesised, cache_<hash>.wav)" % r["cache"])
    print("    CLONE live     : %d   (.wav, generated on the fly)" % r["clone_file"])
    print("    ORIG fallback  : %d   (clone unavailable -> played a game line)" % r.get("fallback_orig", 0))
    print("    EDGE  (.mp3)   : %d   %s" % (r["edge"], "<< MUST BE 0" if r["edge"] else "OK"))
    print("    clone generated: %d" % r["clone_generated"])
    print("    -> game-Fairy share = %.1f%%  (of %d played)" % (r["clone_pct"], r["total_played"]))
    allsum = r["original"] + r["cache"] + r["clone_file"] + r["edge"]
    if allsum != r["total_played"]:
        print("    !! category sum %d != total %d (a route is not counted)" % (allsum, r["total_played"]))

    po = _ball_plays_original()
    if po is False:
        print("    ⚠ 已停用（球的 PLAY_GAME_ORIGINAL=False）：原声直出 / 原声兜底")
        print("      -> 所以 GAME ORIGINAL 与 ORIG fallback 会恒为 0，【这不是 bug】")
        print("      -> 实际生效顺序：克隆缓存 -> 实时克隆 -> Edge兜底")
    elif po is True:
        print("    （球的 PLAY_GAME_ORIGINAL=True：原声直出 / 原声兜底 都在生效）")

def main():
    a = sys.argv[1:]
    tail = 0
    if "--tail" in a:
        i = a.index("--tail")
        try:
            tail = int(a[i + 1])
        except Exception:
            tail = 200
        del a[i:i + 2]
    if a and a[0] == "--watch":
        secs = int(a[1]) if len(a) > 1 else 60
        t0 = time.time()
        while time.time() - t0 < secs:
            os.system("cls" if os.name == "nt" else "clear")
            print("=== VOICE AUDIT (watch, %.0fs left) ===" % (secs - (time.time() - t0)))
            for p in LOGS:
                show(audit(p, tail=tail))
            time.sleep(10)
        return 0
    targets = a if a else LOGS
    print("=== VOICE AUDIT: which route did each utterance take ===")
    any_ = False
    for p in targets:
        r = audit(p, tail=tail)
        if r:
            show(r)
            any_ = True
    if not any_:
        print("  no log found. check paths:")
        for p in LOGS:
            print("    %s  %s" % (p, "exists" if os.path.exists(p) else "missing"))
    return 0

if __name__ == "__main__":
    sys.exit(main())
