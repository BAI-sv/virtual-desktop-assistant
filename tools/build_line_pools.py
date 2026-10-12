# -*- coding: utf-8 -*-
"""
build_line_pools.py —— 从语料库【机械】构建"执行提示"的台词池

【为什么要它】2026-10-09 用户抱怨："一直都是提示音用的解析进度50%，有没有其他的，
  不是有 1600 条语音，全都匹配了吗？"
  实测根因：`fairy_ball.py` 的 GAME_LINE 把 **84 个工具压到只 13 句**台词，其中
  `run_python`/`run_node` 共用「通信结构解析进度50」—— 主代理整天跑 Python，
  用户就一直听同一句。**素材利用率 13/1600 = 0.8%**。

【产物】`<项目根>/voice/line_pools.json`
  {"built":..,"count":370,"pools":{"search":[..],"scan":[..], ...}}
  · 每句都来自语料库，且【音频文件确实存在】（本脚本校验，缺文件的不收）
  · 两遍筛：① 严格（以状态/工作词【开头】）② 不够 MIN_POOL 的池用宽松筛补足
  · 仍不足 MIN_POOL 的池 → 并入 other
  · 池名固定，供 fairy_ball.py 的 TOOL_POOL 映射

【为什么用 Python 不用 CNSH】本脚本要读 JSON、遍历 1600 条、校验文件存在、写文件 ——
  **CNSH 没有文件/JSON 能力**，属于必须留在 Python 的那类（见记忆里的 CNSH 边界）。

用法: py -3.12 build_line_pools.py [--dry]
"""
import collections
import json
import os
import re
import sys
import time
import fairy_root

VOICE_INDEX = fairy_root.VOICE_INDEX
OUT = fairy_root.LINE_POOLS
MIN_POOL = 12
MIN_LEN, MAX_LEN = 5, 20

# ── 池规则：按【从上到下】取第一个命中的池（顺序会影响归类，别乱调） ──
RULES = [
    ("search", r"检索|搜索|查找|搜寻|寻找|定位|追踪|侦察"),
    ("read",   r"读取|采集|获取|接收|收录|识别|记录"),
    ("scan",   r"解析|扫描|检测|探测|监测|观测|勘察|检验"),
    ("fix",    r"修复|清理|清除|同步|更新|维护|校准|整理|修正"),
    ("plan",   r"规划|计算|分析|推算|路线|路径|方案|部署|安排"),
    ("target", r"目标|单位|信号|热源|敌方|坐标"),
    ("wait",   r"稍等|稍候|等待|待机|请稍"),
    ("notice", r"注意|提醒|告知|报告|警告|留意|小心"),
    ("done",   r"完成|就绪|完毕|成功|搞定|好了|启动完"),
]

# 第一遍（严格）：必须【以】状态词/工作动词开头 —— 这种最像"执行提示"
PREFIX_STRICT = (
    r"^(正在|已|检测到|探测到|发现|定位|识别|确认|当前|本次|系统|数据|信号|任务|进度|请|注意|警告|"
    r"开始|启动|完成|就绪|准备|需要|建议|为您|了解|收到|明白|主人请注意|主人请您|主人我|主人您|主人已|"
    r"修复|清理|清除|同步|更新|维护|校准|整理|修正|解析|扫描|检测|探测|监测|勘察|检验|"
    r"检索|搜索|查找|搜寻|寻找|追踪|侦察|读取|采集|获取|接收|收录|记录|录入|"
    r"规划|计算|分析|推算|路线|路径|方案|部署|安排|通讯|通信|连接|设备|程序|信息|资源|网络|工作)"
)
# 第二遍（宽松）：句中【任意位置】出现状态词即可（用来把池补足到 MIN_POOL）
STATUS_ANY = (r"正在|已经|已为|已成功|已确认|已完成|公告|当前|本次|请稍|请注意|警告|检测到|"
              r"探测到|发现|定位|识别|完成|就绪|启动|同步|更新|清理|修复|采集|读取")
# 明显是【剧情对白】而不是提示的，排除（宽松筛时用，避免"…表示…""我认为…"这类）
NARRATIVE = r"表示|认为|觉得|告诉|问道|回答|因为|所以|但是|而且|如果|难道|难道说|我们应|我会如同"


def build():
    j = json.load(open(VOICE_INDEX, encoding="utf-8"))
    lines = j.get("lines") or []
    vd = j.get("voices_dir") or "voices"
    base = vd if os.path.isabs(vd) else os.path.join(os.path.dirname(VOICE_INDEX), vd)

    # 先收集"所有可用候选"（音频存在 + 长度合规 + 去重）
    cand = []                       # (t, strict?)
    seen = set()
    n_missing = 0
    for e in lines:
        t = (e.get("t") or "").strip()
        w = e.get("w") or ""
        if not t or not w:
            continue
        if not os.path.exists(os.path.join(base, w)):
            n_missing += 1
            continue
        if t in seen or not (MIN_LEN <= len(t) <= MAX_LEN):
            continue
        seen.add(t)
        cand.append((t, bool(re.search(PREFIX_STRICT, t))))

    def put_of(t):
        for name, pat in RULES:
            if re.search(pat, t):
                return name
        return "other"

    pools = collections.OrderedDict((k, []) for k, _ in RULES)
    pools["other"] = []

    # 第一遍：严格
    for t, strict in cand:
        if strict:
            pools[put_of(t)].append(t)

    # 第二遍：给不足 MIN_POOL 的池补足（宽松，但要排除剧情对白）
    topped = {}
    for name in list(pools.keys()):
        if name == "other" or len(pools[name]) >= MIN_POOL:
            continue
        need = MIN_POOL - len(pools[name])
        added = []
        for t, strict in cand:
            if len(added) >= need:
                break
            if strict or t in pools[name]:
                continue
            if put_of(t) != name:
                continue
            if re.search(NARRATIVE, t):
                continue
            if not re.search(STATUS_ANY, t):
                continue
            added.append(t)
        pools[name].extend(added)
        if added:
            topped[name] = len(added)

    # 仍不足的池 → 并入 other
    merged = []
    for name in list(pools.keys()):
        if name == "other":
            continue
        if len(pools[name]) < MIN_POOL:
            merged.append("%s(%d)" % (name, len(pools[name])))
            pools["other"].extend(pools.pop(name))

    for k in pools:
        pools[k] = sorted(set(pools[k]))

    return {
        "built": time.strftime("%Y-%m-%d %H:%M:%S"),
        "count": sum(len(v) for v in pools.values()),
        "min_pool": MIN_POOL,
        "len_range": [MIN_LEN, MAX_LEN],
        "stats": {"corpus": len(lines), "candidates": len(cand),
                  "missing_audio": n_missing, "topped_up": topped,
                  "merged_into_other": merged},
        "pools": pools,
    }


def main():
    doc = build()
    st = doc["stats"]
    print("语料库 %d 条 · 可用候选 %d · 缺音频 %d" % (st["corpus"], st["candidates"], st["missing_audio"]))
    if st["topped_up"]:
        print("第二遍补足的池:", st["topped_up"])
    if st["merged_into_other"]:
        print("并入 other 的池:", ", ".join(st["merged_into_other"]))
    print("--- 各池句数 ---")
    for k, v in doc["pools"].items():
        print("   %-8s %3d 句   例: %s" % (k, len(v), " / ".join(v[:3])))
    print("   合计 %d 句（原来是 13 句）" % doc["count"])
    if "--dry" in sys.argv:
        print("(--dry 没写文件)")
        return 0
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    print("已写:", OUT, "%.1f KB" % (os.path.getsize(OUT) / 1024))
    return 0


if __name__ == "__main__":
    sys.exit(main())
