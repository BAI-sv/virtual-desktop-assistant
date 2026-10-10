# -*- coding: utf-8 -*-
import sys
import json
import os
import re
import time
import urllib.request
import fairy_root

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
CONF = fairy_root.BRIEFING_CONF
CACHE = fairy_root.log("briefing_cache.json")

DEFAULTS = {
    "enabled": True,
    "city": "",
    "city_confirmed": False,
    "auto_city": "",
    "keywords": ["AI", "人工智能", "游戏", "科技", "英伟达", "显卡"],
    "news_count": 0,
    "keyword_news_count": 0,
    "headline_max_chars": 26,
    "rss": ["https://www.chinanews.com.cn/rss/scroll-news.xml"],
}


WEATHER_ZH = {
    "sunny": "晴", "clear": "晴", "partly cloudy": "多云", "cloudy": "阴",
    "overcast": "阴天", "mist": "薄雾", "fog": "雾", "smoky haze": "烟霾",
    "haze": "霾", "light rain": "小雨", "moderate rain": "中雨", "heavy rain": "大雨",
    "light snow": "小雪", "snow": "雪", "thunder": "雷", "thundery outbreaks possible": "可能雷阵雨",
    "patchy rain possible": "局部有雨", "light drizzle": "毛毛雨", "rain": "雨",
    "patchy rain nearby": "附近有雨", "light rain shower": "阵雨",
}

def _get(url, timeout=10):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()

def save_conf(patch):
    c = _load_conf()
    c.update(patch or {})
    try:
        os.makedirs(os.path.dirname(CONF), exist_ok=True)
        tmp = CONF + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONF)
    except Exception as e:
        print("[briefing] 配置写入失败（城市可能保不住）: %s" % e, flush=True)
    return c

def _load_conf():
    c = dict(DEFAULTS)
    if not os.path.exists(CONF):
        return c
    try:
        c.update(json.load(open(CONF, encoding="utf-8")))
    except Exception as e:
        print("[briefing] 配置解析失败，已退回默认值（原文件保留未动）: %s" % e, flush=True)
    return c

def get_location(city=""):
    if city:
        return city, None, None
    try:
        j = json.loads(_get("http://ip-api.com/json/?lang=zh-CN", 8).decode("utf-8"))
        if j.get("status") == "success":
            c = (j.get("city") or "").replace("市", "")
            return c or j.get("regionName") or "本地", j.get("lat"), j.get("lon")
    except Exception:
        pass
    return "", None, None

def _zh_weather(s):
    k = str(s or "").strip().lower()
    return WEATHER_ZH.get(k, s or "")

def geocode(name):
    if not name:
        return None, None
    try:
        import urllib.parse
        u = ("https://geocoding-api.open-meteo.com/v1/search?name=%s&count=1&language=zh&format=json"
             % urllib.parse.quote(str(name)))
        j = json.loads(_get(u, 10).decode("utf-8"))
        r0 = (j.get("results") or [None])[0]
        if r0:
            return r0.get("latitude"), r0.get("longitude")
    except Exception:
        pass
    return None, None

def get_weather(city, lat=None, lon=None):
    q = ("%s,%s" % (lat, lon)) if (lat is not None and lon is not None) else (city or "")
    if not q:
        return None
    try:
        w = json.loads(_get("https://wttr.in/%s?format=j1" % q, 12).decode("utf-8"))
    except Exception:
        return None
    try:
        c = w["current_condition"][0]
        out = {"now": {
            "temp": c.get("temp_C"), "feel": c.get("FeelsLikeC"),
            "humidity": c.get("humidity"), "wind": c.get("windspeedKmph"),
            "desc": _zh_weather((c.get("weatherDesc") or [{}])[0].get("value")),
        }, "days": []}
        for d in (w.get("weather") or [])[:3]:
            h = d.get("hourly") or []
            mid = h[len(h) // 2] if h else {}
            out["days"].append({
                "date": d.get("date"), "min": d.get("mintempC"), "max": d.get("maxtempC"),
                "desc": _zh_weather((mid.get("weatherDesc") or [{}])[0].get("value")),
            })
        return out
    except Exception:
        return None


WMO_ZH = {
    0: "晴", 1: "晴间多云", 2: "多云", 3: "阴",
    45: "雾", 48: "冻雾",
    51: "毛毛雨", 53: "小雨", 55: "中雨",
    56: "冻雨", 57: "冻雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    66: "冻雨", 67: "冻雨",
    71: "小雪", 73: "中雪", 75: "大雪", 77: "雪粒",
    80: "阵雨", 81: "阵雨", 82: "强阵雨",
    85: "阵雪", 86: "阵雪",
    95: "雷阵雨", 96: "雷阵雨伴冰雹", 99: "雷阵雨伴冰雹",
}

def _wmo_zh(code):
    try:
        return WMO_ZH.get(int(code), "")
    except Exception:
        return ""

def daily_forecast(lat, lon, days=3):
    if lat is None or lon is None:
        return []
    try:
        u = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
             "&daily=weather_code,temperature_2m_max,temperature_2m_min,"
             "precipitation_sum,rain_sum,snowfall_sum&timezone=auto&forecast_days=%d"
             % (lat, lon, max(1, min(7, int(days))) + 1))
        j = json.loads(_get(u, 12).decode("utf-8"))
        d = j.get("daily") or {}
        dates = (d.get("time") or [])[1:]
        out = []
        for i, ds in enumerate(dates[:days]):
            k = i + 1
            try:
                tmax = (d.get("temperature_2m_max") or [])[k]
                tmin = (d.get("temperature_2m_min") or [])[k]
            except Exception:
                continue
            if tmax is None or tmin is None:
                continue
            code = (d.get("weather_code") or [None])[k] if k < len(d.get("weather_code") or []) else None
            desc = _wmo_zh(code)
            rain = (d.get("rain_sum") or [0] * len(dates))[k] if k < len(d.get("rain_sum") or []) else 0
            snow = (d.get("snowfall_sum") or [0] * len(dates))[k] if k < len(d.get("snowfall_sum") or []) else 0
            precip = ""
            if snow and snow > 0:
                precip = "有雪"
            elif rain and rain > 0.1:
                precip = "有雨"
            out.append({"date": ds, "min": _num1(tmin), "max": _num1(tmax),
                        "desc": desc, "precip": precip})
        return out
    except Exception as e:
        print("[briefing] 未来三天(open-meteo) 取不到: %s: %s" % (type(e).__name__, str(e)[:80]),
              flush=True)
        return []

def _num1(v):
    try:
        return int(round(float(v)))
    except Exception:
        return v

def day_name(date_str, today=None):
    try:
        d = time.strptime(str(date_str), "%Y-%m-%d")
        t = today or time.localtime()
        off = int((time.mktime((d.tm_year, d.tm_mon, d.tm_mday, 12, 0, 0, 0, 0, -1))
                   - time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 12, 0, 0, 0, 0, -1))) // 86400)
        if off == 1:
            return "明天"
        if off == 2:
            return "后天"
        if off == 3:
            return "大后天"
        if off == 0:
            return "今天"
        return "%d 月 %d 日" % (d.tm_mon, d.tm_mday)
    except Exception:
        return str(date_str)

def _cnsh_logic():
    try:
        import importlib.util
        fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fairy_logic.py")
        if not os.path.exists(fp):
            return None
        s = importlib.util.spec_from_file_location("fairy_logic", fp)
        m = importlib.util.module_from_spec(s)
        sys.modules["fairy_logic"] = m
        s.loader.exec_module(m)
        return m
    except Exception:
        return None

def clothing_advice(t):
    try:
        t = float(t)
    except Exception:
        return ""
    m = _cnsh_logic()
    if m and hasattr(m, "穿衣建议"):
        try:
            return m.穿衣建议(t)
        except Exception:
            pass
    if t <= 0:
        return "零度以下，羽绒服加厚毛衣，手套围巾都带上"
    if t <= 8:
        return "很冷，羽绒服或厚大衣，里面加毛衣"
    if t <= 15:
        return "偏冷，外套加长袖，早晚温差大注意加衣"
    if t <= 22:
        return "舒适微凉，薄外套或卫衣就够了"
    if t <= 28:
        return "温暖，长袖或短袖都行，随身带件薄外搭"
    return "偏热，短袖短裤，注意防晒补水"

def get_news(cfg):
    out = []
    seen = set()
    for u in (cfg.get("rss") or []):
        try:
            d = _get(u, 8).decode("utf-8", "replace")
        except Exception:
            continue
        for t in re.findall(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", d, re.S):
            t = re.sub(r"\s+", " ", t).strip()
            if not t or len(t) < 8:
                continue
            if any(k in t for k in ("即时新闻", "滚动", "RSS", "首页")):
                continue
            if t in seen:
                continue
            seen.add(t)
            out.append(t)
    return out

def resolves_city(cfg=None):
    cfg = cfg or _load_conf()
    city = (cfg.get("city") or "").strip()
    if cfg.get("city_confirmed") and city:
        return city, False
    auto, lat, lon = get_location("")
    return (city or auto), (not cfg.get("city_confirmed"))

HEALTH_REPORT = fairy_root.log("health_last.json")

HEALTH_STATE = fairy_root.log("health_told.json")

def health_lines():
    try:
        if not os.path.exists(HEALTH_REPORT):
            return []
        j = json.load(open(HEALTH_REPORT, encoding="utf-8"))
        items = [x for x in (j.get("items") or []) if x.get("level") in ("bad", "warn")]

        now = {}
        for x in items:
            now[str(x.get("id") or x.get("name"))] = x.get("level")
        told = {}
        try:
            if os.path.exists(HEALTH_STATE):
                told = json.load(open(HEALTH_STATE, encoding="utf-8")).get("seen") or {}
        except Exception:
            told = {}

        RANK = {"warn": 1, "bad": 2}
        fresh, worse = [], []
        for x in items:
            k = str(x.get("id") or x.get("name"))
            lv = x.get("level")
            old = told.get(k)
            if old is None:
                fresh.append(x)
            elif RANK.get(lv, 0) > RANK.get(old, 0):
                worse.append(x)

        out = []
        if fresh or worse:
            if fresh:
                out.append("体检有 %d 个新情况。" % len(fresh))
            if worse:
                out.append("有 %d 个问题变严重了。" % len(worse))
            for x in (worse + fresh)[:2]:
                out.append("%s：%s" % (x.get("name") or x.get("id"), (x.get("detail") or "")[:60]))
                fx = (x.get("fix") or "").strip()
                if fx:
                    out.append("解决办法：" + fx[:70] + "。")
            out.append("要我处理吗？")


        try:
            os.makedirs(os.path.dirname(HEALTH_STATE), exist_ok=True)
            tmp = HEALTH_STATE + ".tmp"
            json.dump({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "seen": now},
                      open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            os.replace(tmp, HEALTH_STATE)
        except Exception:
            pass
        return out
    except Exception:
        return []

def hw_lines():
    try:
        if not os.path.exists(HW_STATE):
            return []
        j = json.load(open(HW_STATE, encoding="utf-8"))
        dw = j.get("delta_whea")
        db = j.get("delta_6008")
        out = []
        if isinstance(dw, int) and dw > 0:
            out.append("注意：硬件监控发现新增 %d 条 CPU 缓存错误。" % dw)
            out.append("上次记录时间是 %s。" % (j.get("prev_time") or "未知"))
        if isinstance(db, int) and db > 0:
            out.append("另外新增了 %d 次意外关机。" % db)
        if out:
            out.append("建议检查处理器的电压与内存设置，必要时把 BIOS 恢复默认观察。")
        return out
    except Exception:
        return []

BOOT_STATE = fairy_root.log("boot_lines_state.json")
DEV_KNOWN = fairy_root.log("devices_known.json")
INVENTORY = fairy_root.log("inventory.json")

DETECT_ONE = "检测到附近的热源信号"
DETECT_MANY = "检测到附近存在复数生物信号"
AUDIO_KEYS = ("<BT_SPEAKER>", "Stereo", "扬声器")

def _load_json(p, d=None):
    try:
        if os.path.exists(p):
            return json.load(open(p, encoding="utf-8"))
    except Exception:
        pass
    return d if d is not None else {}

def boot_lines():
    try:
        st = _load_json(BOOT_STATE, {})
        told_dev = set(st.get("known_devices") or [])
        last_cap = st.get("cap_count")
        last_cap_day = st.get("cap_report_day") or ""
        today = time.strftime("%Y-%m-%d")

        now_dev = _load_json(DEV_KNOWN, {})
        keys = set(now_dev.keys()) if isinstance(now_dev, dict) else set()

        inv = _load_json(INVENTORY, {})
        audio_now = [d.get("name") or "" for d in ((inv.get("devices") or {}).get("audio") or [])]

        out = []


        if told_dev and keys:
            fresh = keys - told_dev
            if fresh:
                names = []
                for k in list(fresh)[:3]:
                    nm = (now_dev.get(k) or {}).get("name") or ""
                    if nm and not any(c.isdigit() or c == ":" for c in nm[:2]):
                        names.append(nm)

                if len(fresh) >= 3 and DETECT_MANY:
                    out.append(DETECT_MANY)
                else:
                    out.append(DETECT_ONE)

                if names:
                    out.append("新出现的是：" + "、".join(dict.fromkeys(names)) + "。")


        if told_dev and audio_now:
            gone_audio = [a for a in (st.get("audio_snapshot") or [])
                          if any(k in a for k in AUDIO_KEYS) and a not in audio_now]
            if gone_audio:
                out.append("注意：音频设备 %s 不在了。" % gone_audio[0][:24])


        cap = (inv.get("capability") or []) if isinstance(inv, dict) else []


        inv_time = (inv.get("time") or "") if isinstance(inv, dict) else ""
        last_inv = st.get("last_inventory_time") or ""


        if last_inv and inv_time and inv_time != last_inv and len(cap):
            out.append("我重新自检了一遍，找到 %d 个能让自己变强的办法，要听吗？" % len(cap))


        n_cap = len(cap)
        if isinstance(last_cap, int) and n_cap > last_cap and last_cap_day != today:
            out.append("我找到 %d 个能让自己变强的办法，要听吗？" % n_cap)


        new_state = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "known_devices": sorted(keys),
            "audio_snapshot": audio_now,
            "cap_count": n_cap,
            "cap_report_day": (today if (isinstance(last_cap, int) and n_cap > last_cap) else last_cap_day),
            "last_inventory_time": inv_time,
            "last_told": out,
        }
        try:
            os.makedirs(os.path.dirname(BOOT_STATE), exist_ok=True)
            tmp = BOOT_STATE + ".tmp"
            json.dump(new_state, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            os.replace(tmp, BOOT_STATE)
        except Exception:
            pass
        return out
    except Exception:
        return []

def build(cfg=None):
    cfg = cfg or _load_conf()
    lines = []


    now = time.localtime()
    wd = "一二三四五六日"[now.tm_wday]
    hour = now.tm_hour
    greet = ("凌晨了" if hour < 5 else "早上好" if hour < 11 else
             "中午好" if hour < 13 else "下午好" if hour < 18 else
             "晚上好" if hour < 23 else "夜深了")
    lines.append("主人，%s。现在是 %d 年 %d 月 %d 日，星期%s，%02d 点 %02d 分。"
                 % (greet, now.tm_year, now.tm_mon, now.tm_mday, wd, hour, now.tm_min))


    city, need_confirm = resolves_city(cfg)
    lat = lon = None
    if city:
        lines.append("检测到当前位置是%s。" % city)
        if need_confirm:
            lines.append("如果我定位错了，请在球上右键选「位置」改一下。")


    if lat is None and lon is None:
        if cfg.get("lat") and cfg.get("lon"):
            lat, lon = cfg.get("lat"), cfg.get("lon")
        elif city:
            lat, lon = geocode(city)
    w = None


    _fc = []
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as _ex:
            _fut = _ex.submit(daily_forecast, lat, lon, 3)
            w = get_weather(city, lat, lon)
            _fc = _fut.result(timeout=20) or []
    except Exception as _e:
        print("[briefing] 并发取天气失败，退回顺序取: %s: %s"
              % (type(_e).__name__, str(_e)[:60]), flush=True)
        w = get_weather(city, lat, lon)
        _fc = daily_forecast(lat, lon, 3) or []
    if w:
        n = w["now"]
        lines.append("现在室外 %s 度，体感 %s 度，%s，湿度 %s%%，风速 %s 公里每小时。"
                     % (n["temp"], n["feel"], n["desc"] or "天气未知", n["humidity"], n["wind"]))


        _jia = []
        for _d in _fc:
            _jia.append({"称呼": day_name(_d.get("date")), "最低": _d.get("min"),
                         "最高": _d.get("max"), "天气": _d.get("desc") or "",
                         "降水": _d.get("precip") or ""})
        _yi = []
        for _d in (w.get("days") or []):
            _yi.append({"称呼": day_name(_d.get("date")), "最低": _d.get("min"),
                        "最高": _d.get("max")})
        _m = _cnsh_logic()
        _sent = ""
        if _m is not None and hasattr(_m, "三天天气句"):
            try:
                _sent = _m.三天天气句(_jia, _yi)
            except Exception as _e:
                print("[briefing] fairy_logic.三天天气句 失败，退回本文件拼: %s: %s"
                      % (type(_e).__name__, _e), flush=True)
                _sent = ""
        if not _sent:
            seg = []
            for d in (_jia or _yi):
                seg.append("%s %s 到 %s 度" % (d.get("称呼") or "", d.get("最低"), d.get("最高")))
            if seg:
                _sent = "未来三天：" + "；".join(seg) + "。"
        if _sent:
            lines.append(_sent)
        lines.append("穿衣建议：" + clothing_advice(n["temp"]) + "。")
    else:
        lines.append("天气没能取到，这次就不猜了。")


    try:
        import importlib.util as _iu
        _fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "balance_watch.py")
        if os.path.exists(_fp):
            _s = _iu.spec_from_file_location("fairy_balance", _fp)
            _bw = _iu.module_from_spec(_s)
            sys.modules["fairy_balance"] = _bw
            _s.loader.exec_module(_bw)
            _res = _bw.check()
            _line = _bw.sentence(_res, boot=True)
            if _line:
                lines.append(_line)
    except Exception as _e:
        print("[briefing] 余额检查失败: %s: %s" % (type(_e).__name__, _e), flush=True)


    _want_news = int(cfg.get("news_count") or 0) > 0
    news = get_news(cfg) if _want_news else []
    if not _want_news:
        pass
    elif news:


        lim = int(cfg.get("headline_max_chars") or 26)

        def short(x):
            x = str(x).strip()
            return x if len(x) <= lim else x[:lim].rstrip("，,。.、：: ") + "…"

        k = int(cfg.get("news_count") or 2)

        lines.append("今日要闻：" if k != 2 else "今日要闻两条：")
        for t in news[:k]:
            lines.append(short(t))
        kws = [x for x in (cfg.get("keywords") or []) if x]
        if kws:
            hit = [t for t in news if any(w0.lower() in t.lower() for w0 in kws)]
            if hit:
                kk = int(cfg.get("keyword_news_count") or 2)
                lines.append("你关心的%s有 %d 条：" % ("、".join(kws[:2]), len(hit)))
                for t in hit[:kk]:
                    lines.append(short(t))
            else:
                lines.append("今天暂时没有和你关注的关键词对上的新闻。")
    else:
        lines.append("新闻源这次没取到。")


    lines.extend(hw_lines())


    lines.extend(boot_lines())


    lines.extend(health_lines())

    lines.append("播报完毕，随时听候吩咐。")
    return lines

if __name__ == "__main__":
    import sys


    if "--news" in sys.argv:
        cfg = _load_conf()
        i = sys.argv.index("--news")
        kws = [x for x in sys.argv[i + 1:] if not x.startswith("-")] or (cfg.get("keywords") or [])
        items = get_news(cfg)
        print("=== 抓到 %d 条 ===" % len(items))
        for x in items[:15]:
            print("  ·", x)
        if kws:
            hit = [x for x in items if any(k.lower() in x.lower() for k in kws)]
            print("\n=== 与你关心的（%s）相关 %d 条 ===" % ("、".join(kws[:3]), len(hit)))
            for x in hit[:10]:
                print("  ★", x)
        sys.exit(0)

    if "--json" in sys.argv:
        print(json.dumps(build(), ensure_ascii=False, indent=1))
    else:
        for s in build():
            print(s)
