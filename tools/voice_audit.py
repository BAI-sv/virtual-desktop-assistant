# -*- coding: utf-8 -*-
"""
voice_audit.py —— 语音路由审计：从球的日志里统计【每句话走了哪条路】

【为什么需要】
  目标：虚拟桌面助手 的所有语音输出都必须是"游戏里 Fairy 的音色"。
  球的日志里藏着可靠的判据（实测确认，2026-10-09）：
      播放 <16位hex>.wav      -> GAME ORIGINAL  游戏原声（最好）
      播放 fairy_say.wav      -> CLONE          用 fairy.wav 克隆（好）
      播放 fairy_say.mp3      -> EDGE           通用音色 ★必须为 0★
      [fairy] 语音=克隆路由(indextts-clone) N 字节 -> 克隆生成成功
  ⚠ 千万别只搜 'edge' 关键字 —— 球的日志里根本不写 edge 这个词，
    它只体现在【文件扩展名 .mp3】上。（这是我 2026-10-09 犯过的错：
    搜 edge 得到 0，就以为没问题，其实 mp3 出现了 7 次。）

【用法】
  py -3.12 D:\\FAIRY\\tools\\voice_audit.py                    审计默认日志
  py -3.12 D:\\FAIRY\\tools\\voice_audit.py <logfile>          审计指定日志
  py -3.12 D:\\FAIRY\\tools\\voice_audit.py --tail 200          只看最近 200 行
  py -3.12 D:\\FAIRY\\tools\\voice_audit.py --watch 60          监控 60 秒（每 10 秒刷新）
"""
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
# ★【2026-10-09 加】本地克隆【预合成缓存】—— voice_cache.py 的产物，命名 cache_<16hex>.wav
#   为什么必须单独统计：缓存命中播的也是克隆音色（好），但它不是"游戏原声"，
#   混在一起会看不出缓存有没有真正被用上。
#   也正因为带 cache_ 前缀，它【不会】被上面的 RE_ORIG 误匹配（RE_ORIG 前面要求是空格）。
RE_CACHE = re.compile(r"播放 cache_([0-9a-f]{16})\.wav")
# 兜底路径（克隆不可用时改播一句游戏原声，见 fairy_ball.py 的 corpus.fallback）
RE_FB = re.compile(r"语音=原声兜底")


def _ball_plays_original():
    """★ 读球的 PLAY_GAME_ORIGINAL 开关。

    【为什么要有这个】用户 2026-10-09 要求「先取消和游戏的关联」，球里设了
        PLAY_GAME_ORIGINAL = False
    它【同时关闭】了「原声直出」和「原声兜底」两层 —— 于是
        GAME ORIGINAL 与 ORIG fallback 会【恒为 0】。
    那是【预期行为，不是 bug】。不标注出来，以后看到 ORIG=0 一定有人以为坏了。
    返回 True / False / None（读不到）
    """
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
    # ★ 已停用层的标注（从 fairy_ball.py 读开关，不写死）
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
