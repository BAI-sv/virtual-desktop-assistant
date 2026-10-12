# -*- coding: utf-8 -*-
"""Fairy 天气预警：高温/低温/暴雨/雷电主动提醒（规则判断，不依赖专用预警接口）。

用法：
    py -3.12 fairy_alert.py           # 打印当前预警（无则空）
    py -3.12 fairy_alert.py --json    # JSON 输出（球内线程调用）
被 fairy_ball.py 的预警线程每 30 分钟调用一次，预警变化时才播报。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import briefing


def _num(x):
    try:
        return float(x)
    except Exception:
        return None


def _load_conf():
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "fairy.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def check(cfg=None):
    """返回预警文本列表（空列表 = 无预警）。失败也返回空列表，不抛。"""
    cfg = cfg or _load_conf()
    try:
        city, need = briefing.resolves_city(cfg)
        lat, lon = cfg.get("lat"), cfg.get("lon")
        if lat is None or lon is None:
            if cfg.get("lat") and cfg.get("lon"):
                lat, lon = cfg.get("lat"), cfg.get("lon")
            elif city:
                lat, lon = briefing.geocode(city)
        if not (lat and lon and city):
            return []
        w = briefing.get_weather(city, lat, lon)
        fc = briefing.daily_forecast(lat, lon, 2) or []
        if not w:
            return []
        warns = []
        n = w["now"]
        t = _num(n.get("temp"))
        if t is not None and t >= 35:
            warns.append("高温预警：当前 %s 度，注意防暑补水" % int(t))
        if t is not None and t <= -5:
            warns.append("低温预警：当前 %s 度，注意保暖" % int(t))
        d = (n.get("desc") or "") + "".join((f.get("desc") or "") for f in fc)
        if any(k in d for k in ("雷", "冰雹")):
            warns.append("雷电预警：有雷雨，注意避开空旷地")
        for f in fc[:2]:
            dd = (f.get("desc") or "").strip()
            if any(k in dd for k in ("暴雨", "大雨", "中雨", "强阵雨")):
                warns.append("%s有%s，出门带伞注意安全" % (f.get("称呼") or "明天", dd))
        # 去重 + 限 3 条
        seen, out = set(), []
        for x in warns:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out[:3]
    except Exception:
        return []


if __name__ == "__main__":
    ws = check()
    if "--json" in sys.argv:
        print(json.dumps({"ok": True, "warns": ws}, ensure_ascii=False))
    elif ws:
        for x in ws:
            print(x)
    else:
        print("无预警")
