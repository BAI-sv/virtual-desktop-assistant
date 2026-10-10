# -*- coding: utf-8 -*-
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


PREFIX_STRICT = (
    r"^(正在|已|检测到|探测到|发现|定位|识别|确认|当前|本次|系统|数据|信号|任务|进度|请|注意|警告|"
    r"开始|启动|完成|就绪|准备|需要|建议|为您|了解|收到|明白|主人请注意|主人请您|主人我|主人您|主人已|"
    r"修复|清理|清除|同步|更新|维护|校准|整理|修正|解析|扫描|检测|探测|监测|勘察|检验|"
    r"检索|搜索|查找|搜寻|寻找|追踪|侦察|读取|采集|获取|接收|收录|记录|录入|"
    r"规划|计算|分析|推算|路线|路径|方案|部署|安排|通讯|通信|连接|设备|程序|信息|资源|网络|工作)"
)

STATUS_ANY = (r"正在|已经|已为|已成功|已确认|已完成|公告|当前|本次|请稍|请注意|警告|检测到|"
              r"探测到|发现|定位|识别|完成|就绪|启动|同步|更新|清理|修复|采集|读取")

NARRATIVE = r"表示|认为|觉得|告诉|问道|回答|因为|所以|但是|而且|如果|难道|难道说|我们应|我会如同"

def build():
    j = json.load(open(VOICE_INDEX, encoding="utf-8"))
    lines = j.get("lines") or []
    vd = j.get("voices_dir") or "voices"
    base = vd if os.path.isabs(vd) else os.path.join(os.path.dirname(VOICE_INDEX), vd)


    cand = []
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


    for t, strict in cand:
        if strict:
            pools[put_of(t)].append(t)


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
