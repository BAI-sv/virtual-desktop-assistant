# -*- coding: utf-8 -*-
"""
briefing.py —— 「啰嗦的 Fairy」开机播报

【它做什么】
  设备开机、接上声音设备后，自动播报一段：
    当前时间 -> 位置 -> 现在天气 -> 未来三天 -> 穿衣建议 -> 今日要闻 -> 你关心的新闻

【设计原则】
  1. **每个数据源都独立降级**：哪个取不到就跳过那句，绝不因为一个接口挂了整段播报失败
  2. **不编数据**：取不到就说"取不到"，不假装知道天气
  3. **可配置**：关心的关键词放在配置文件（briefing.json），改文件即可
  4. **只在开机播一次**：由调用方（球）保证，本模块只负责"生成要说的话"

【数据源（都实测可达，免 key）】
  位置  http://ip-api.com/json/?lang=zh-CN
  天气  https://wttr.in/<lat>,<lon>?format=j1      （当前 + 未来 3 天）
  新闻  https://www.chinanews.com.cn/rss/scroll-news.xml
        （新浪那个 marquee 源实测返回 0 条，已弃用）
"""
import sys   # ← 必须模块级：球是按模块 import 调 build() 的，不是 __main__
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
    "city": "",                       # 留空 = IP 自动定位
    "city_confirmed": False,          # 用户是否确认过定位（未确认就先问一次）
    "auto_city": "",                  # IP 自动定位到的城市（用于让用户核对）
    "keywords": ["AI", "人工智能", "游戏", "科技", "英伟达", "显卡"],
    "news_count": 0,                  # 头条几条（0 = 开机不报新闻，等用户问）
    "keyword_news_count": 0,          # 关心的新闻几条（0 = 不报）
    "headline_max_chars": 26,         # 单条最多念多少字（超了截断加"…"）
    "rss": ["https://www.chinanews.com.cn/rss/scroll-news.xml"],
}

# ★ 2026-10-11【今日节日/节气】公历固定节日表 + 节气近似表（认不出就不报）
FESTIVALS = {
    "01-01": "元旦", "02-14": "情人节", "03-08": "妇女节", "03-12": "植树节",
    "04-01": "愚人节", "05-01": "劳动节", "05-04": "青年节", "06-01": "儿童节",
    "07-01": "建党节", "08-01": "建军节", "09-10": "教师节", "10-01": "国庆节",
    "12-24": "平安夜", "12-25": "圣诞节",
    "01-05": "小寒", "01-20": "大寒", "02-04": "立春", "02-19": "雨水",
    "03-05": "惊蛰", "03-20": "春分", "04-05": "清明", "04-20": "谷雨",
    "05-05": "立夏", "05-21": "小满", "06-06": "芒种", "06-21": "夏至",
    "07-07": "小暑", "07-23": "大暑", "08-07": "立秋", "08-23": "处暑",
    "09-07": "白露", "09-23": "秋分", "10-08": "寒露", "10-23": "霜降",
    "11-07": "立冬", "11-22": "小雪", "12-07": "大雪", "12-22": "冬至",
}

# wttr.in 的天气描述是英文，常见的那几个翻一下（翻不到就原样念）
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
    """把改动写回 briefing.json（位置确认就靠它持久化）。

    ★ 原子写入：先写 .tmp 再 os.replace。
      直接覆盖时若正好断电/被杀，文件会只剩半截 JSON → 下次 _load_conf 解析失败
      → 静默回默认值 → **忘掉用户确认过的城市**（又用 IP 定位的错城市并重问一遍）。
      失败必须打印，不许静默。
    """
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
    """读配置；坏文件要【报出来】而不是静默回默认值。

    先前这里 except: pass —— 配置一旦损坏，用户确认的城市会被悄悄丢掉，
    表面看"一切正常"，实际每次都重新 IP 定位并重复问一遍。
    """
    c = dict(DEFAULTS)
    if not os.path.exists(CONF):
        return c
    try:
        c.update(json.load(open(CONF, encoding="utf-8")))
    except Exception as e:
        print("[briefing] 配置解析失败，已退回默认值（原文件保留未动）: %s" % e, flush=True)
    return c


def get_location(city=""):
    """返回 (城市名, lat, lon)。city 非空就直接用它做天气查询名。"""
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
    """城市名 -> (lat, lon)。用 Open-Meteo 免费地理编码（无需 key）。

    为什么要它：wttr.in 直接吃中文城市名时常常落到附近一个奇怪的地名
    （实测中文地名常被解析到附近一个奇怪的地名），拿经纬度去查才准。
    """
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
    """返回 {now:{...}, days:[{date,min,max,desc}...]}；失败返回 None。"""
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


# ---- ★★ 2026-10-10【未来三天：温度 + 下雨/下雪】---------------------------------
# 【用户原话】「未来三天直接说温度和下雨还是下雪等等」
# 【为什么新接一个源】原来只有 wttr.in，它的"未来三天"只有温度、没有降水类型；
#   而它的逐时数组是**三小时一份、同一天三份一模一样**（实测），挑不出"当天主导天气"。
#   所以改用 Open-Meteo 的**每日天气代码**(WMO)：一个数字就同时给出温度区间和
#   下雨/下雪/晴，语义不会含糊。网络实测可达（本机 2.2 秒）。
#   ⚠ wttr 仍然保留：Open-Meteo 拿不到时，三天就退回 wttr 的温度 + 逐时降水概率。
#
# WMO 天气代码 -> 中文。分档口径按"人话优先"：能说"小雨"就不说"毛毛雨"。
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
    """WMO 代码 -> 中文说法；认不出就返回空串（不编）。"""
    try:
        return WMO_ZH.get(int(code), "")
    except Exception:
        return ""


def daily_forecast(lat, lon, days=3):
    """Open-Meteo 每日预报 -> [{'date','min','max','desc','precip'}]；失败返回 []。

    ★ 返回的是【接下来的 days 天】= 明天起，不含今天（用户要"未来三天"）。
      所以接口多要一天（forecast_days=days+1），再切掉今天那一格。
    【取不到的字段就不编】温度缺失的那天直接跳过。
    【降水口径】天气代码是雨/雪类 且 当天真有降水(雨量或雪量>0) -> precip 写"有雨"/"有雪"。
    """
    if lat is None or lon is None:
        return []
    try:
        u = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
             "&daily=weather_code,temperature_2m_max,temperature_2m_min,"
             "precipitation_sum,rain_sum,snowfall_sum&timezone=auto&forecast_days=%d"
             % (lat, lon, max(1, min(7, int(days))) + 1))
        j = json.loads(_get(u, 12).decode("utf-8"))
        d = j.get("daily") or {}
        dates = (d.get("time") or [])[1:]          # ★ 切掉今天
        out = []
        for i, ds in enumerate(dates[:days]):
            k = i + 1                               # ★ 原数组的下标（含今天）
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
    """温度取整（27.6 -> 28）；取不到就原样返回，不编。"""
    try:
        return int(round(float(v)))
    except Exception:
        return v


def day_name(date_str, today=None):
    """日期 -> 人话：明天 / 后天 / 大后天 / '10 月 14 日'（对不上就报日期，不硬凑）。"""
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
    """优先用 fairy_logic.py（纯判断逻辑），取不到就退回本文件的 Python 版。

    这样"穿什么、怎么问候"这类规则写在中文脚本里，用户可以直接看懂、直接改。
    """
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
    """按温度给穿衣建议（规则简单但明确）。"""
    try:
        t = float(t)
    except Exception:
        return ""
    m = _cnsh_logic()                      # ★ 先问 CNSH 中文逻辑
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
    """抓 RSS 标题列表（去重、去掉频道名那种条目）。"""
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
    """返回 (城市, 是否需要用户确认)。

    规则（用户要求：先自己定位，让使用人确认，不对就手动选）：
      已确认过 -> 直接用配置里的 city
      没确认过 -> 用 IP 自动定位，并返回 need_confirm=True（播报时会问一句）
    """
    cfg = cfg or _load_conf()
    city = (cfg.get("city") or "").strip()
    if cfg.get("city_confirmed") and city:
        return city, False
    auto, lat, lon = get_location("")
    return (city or auto), (not cfg.get("city_confirmed"))


HEALTH_REPORT = fairy_root.log("health_last.json")


HEALTH_STATE = fairy_root.log("health_told.json")


def health_lines():
    """读上次关机体检报告，**只报【新增或变严重】的问题**；没变化就不说话。

    【为什么改】原来每次开机都把整张问题清单念一遍（"发现 9 个问题，其中 3 个严重…"），
    用户质疑的就是这个（"开机就是发现XX…这些语音吗？"）—— 和 boot_lines/hw_lines 一个道理：
      · 数字没信息量（9 个问题里大半是老的、已知的）
      · 每次开机都念 = 啰嗦
      · 只有【变化】才值得打断主人
    规则：
      新问题（id 没见过）      -> 说
      老问题但级别变严重        -> 说
      老问题、级别没变          -> **不说**
      一个问题都没有          -> 不说
    状态文件记 id + level，供下次比对。
    """
    try:
        if not os.path.exists(HEALTH_REPORT):
            return []
        j = json.load(open(HEALTH_REPORT, encoding="utf-8"))
        items = [x for x in (j.get("items") or []) if x.get("level") in ("bad", "warn")]
        # 当前问题指纹（id -> level）
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

        # 落状态（原子写）
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
    """读【CPU/硬件监控】的最新状态，有新增就播报。

    背景：本机实测有 11 条 WHEA-Ldgger Id=18「致命硬件错误」
          （Machine Check Exception / Cache Hierarchy Error，Bank5 = Zen 的 L3 缓存），
          且近 90 天意外关机 54 次。5800X3D 的 V-Cache 正是 L3，怀疑
          BIOS 刷完后 PBO/Curve Optimizer/XMP 设置导致不稳。
    所以要每次开机报一次【有没有新增】，否则"改了设置到底有没有用"无从判断。
    """
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
# 【实测核实过的游戏原声】键必须能在 voice_index.json 的 t 字段精确匹配（去掉标点后相等）
DETECT_ONE = "检测到附近的热源信号"        # 命中 7ff56ca6cbeb65d2.wav（1.9s）
DETECT_MANY = "检测到附近存在复数生物信号"   # 命中（2.7s），用于"发现多台"
AUDIO_KEYS = ("方糖", "Stereo", "扬声器")   # 关键音频设备（掉了才说）


def _load_json(p, d=None):
    try:
        if os.path.exists(p):
            return json.load(open(p, encoding="utf-8"))
    except Exception:
        pass
    return d if d is not None else {}


def boot_lines():
    """开机只报【变化和机会】，**没变化就一句话都不说**。

    用户否掉了"开机念技术清单"（发现16个设备/4块磁盘/16条方向）：
      · 数字没信息量（9 个是邻居的蓝牙，磁盘数固定）
      · 每次开机都念 = 啰嗦（他刚把新闻从 8 条砍到 4 条）
    所以规则改成：
      · 有新设备          -> 说（优先用**游戏原声**，球会走原声直出）
      · 设备消失          -> 不说（除非是关键音频设备，如方糖掉了）
      · 有新的可扩展方向   -> 说，但**一天最多一次**
      · 什么都没变        -> **不说**
      · 硬件风险(WHEA/坏道) -> 已由 hw_lines() 负责，这里不重复

    状态文件记住"上次报过什么"，避免每次开机重复念。
    """
    try:
        st = _load_json(BOOT_STATE, {})
        told_dev = set(st.get("known_devices") or [])
        last_cap = st.get("cap_count")
        last_cap_day = st.get("cap_report_day") or ""
        today = time.strftime("%Y-%m-%d")

        now_dev = _load_json(DEV_KNOWN, {})
        keys = set(now_dev.keys()) if isinstance(now_dev, dict) else set()
        # 关键音频设备（从现场扫描拿不到就跳过；不硬凑）
        inv = _load_json(INVENTORY, {})
        audio_now = [d.get("name") or "" for d in ((inv.get("devices") or {}).get("audio") or [])]

        out = []

        # —— A) 新设备 ——
        # 首次运行（told_dev 为空）只记基线，**不报**，否则第一次开机会把所有设备当"新的"念一遍
        if told_dev and keys:
            fresh = keys - told_dev
            if fresh:
                names = []
                for k in list(fresh)[:3]:
                    nm = (now_dev.get(k) or {}).get("name") or ""
                    if nm and not any(c.isdigit() or c == ":" for c in nm[:2]):
                        names.append(nm)
                # 优先返回**游戏原声**（这两句在语料库里已实测命中）
                if len(fresh) >= 3 and DETECT_MANY:
                    out.append(DETECT_MANY)
                else:
                    out.append(DETECT_ONE)
                # 能认出名字就补充一句人话（这句是合成音，但信息量在这）
                if names:
                    out.append("新出现的是：" + "、".join(dict.fromkeys(names)) + "。")

        # —— B) 关键音频设备掉了 ——
        if told_dev and audio_now:
            gone_audio = [a for a in (st.get("audio_snapshot") or [])
                          if any(k in a for k in AUDIO_KEYS) and a not in audio_now]
            if gone_audio:
                out.append("注意：音频设备 %s 不在了。" % gone_audio[0][:24])

        # 可扩展方向（B2/C 都要用，先取）
        cap = (inv.get("capability") or []) if isinstance(inv, dict) else []

        # —— B2) 【新自举批次】——
        # 自举流水线每次跑完会写 inventory.json（带 time）。只有"新批次"才说一句。
        # ⚠ 措辞注意：用户**明确否掉**过"发现16个设备/4块磁盘"这种清单式播报
        #    （他的原话质疑："开机就是发现XX…这些语音吗？"），
        #    所以这里**只报"有新的机会"**，不念设备数/磁盘数。
        inv_time = (inv.get("time") or "") if isinstance(inv, dict) else ""
        last_inv = st.get("last_inventory_time") or ""
        # 首次运行（last_inv 为空）只记基线，**不报** —— 与设备逻辑一致：
        # "没有变化就不说话"。否则每次全新安装都会念一句，且那不是"变化"。
        if last_inv and inv_time and inv_time != last_inv and len(cap):
            out.append("我重新自检了一遍，找到 %d 个能让自己变强的办法，要听吗？" % len(cap))

        # —— C) 新的可扩展方向（一天最多一次） ——
        n_cap = len(cap)
        if isinstance(last_cap, int) and n_cap > last_cap and last_cap_day != today:
            out.append("我找到 %d 个能让自己变强的办法，要听吗？" % n_cap)

        # —— 落状态 ——
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


# ★ 2026-10-15 机器人融合：子目录 tools\robot 不在 sys.path 里，直接 import 会失败。
#   踩过的坑：失败被 except 吞掉 -> "功能写了但没生效"。所以失败必须留痕。
_ROBOT_NOTE = []


def _load_robot_root():
    """加载机器人子系统的配置根；失败返回 None 并留痕（不抛）。"""
    import sys as _sys
    _t = os.path.join(os.path.dirname(os.path.abspath(__file__)), "robot")
    if not os.path.isdir(_t):
        _ROBOT_NOTE.append("tools\\robot 目录不存在")
        return None
    if _t not in _sys.path:
        _sys.path.insert(0, _t)
    try:
        import robot_root as _rr
        return _rr
    except Exception as e:
        _ROBOT_NOTE.append("import robot_root 失败: %s: %s" % (type(e).__name__, e))
        return None


def build(cfg=None):
    """生成播报句子列表（每句一条，方便逐句播）。"""
    cfg = cfg or _load_conf()
    # ★ 2026-10-11【启动播报内容选择】fairy.json 的 briefing 段可逐项开关：
    #   time/location/weather/dress/news/status/devices，缺省全部播报。
    _seg = {}
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "fairy.json"), encoding="utf-8") as _f:
            _seg = json.load(_f).get("briefing") or {}
    except Exception:
        pass
    lines = []

    # 1) 时间
    if _seg.get("time", False):
        now = time.localtime()
        wd = "一二三四五六日"[now.tm_wday]
        hour = now.tm_hour
        greet = ("凌晨了" if hour < 5 else "早上好" if hour < 11 else
                 "中午好" if hour < 13 else "下午好" if hour < 18 else
                 "晚上好" if hour < 23 else "夜深了")
        lines.append("主人，%s。现在是 %d 年 %d 月 %d 日，星期%s，%02d 点 %02d 分。"
                     % (greet, now.tm_year, now.tm_mon, now.tm_mday, wd, hour, now.tm_min))

    # 1.5) ★ 2026-10-11 Fairy 功能状态（服务在线情况 + 已连接设备数）
    if _seg.get("status", False):
        try:
            _tips = []
            for _port, _nm in ((9881, "语音"), (8085, "视觉"),
                               (11434, "本地大脑"), (19388, "核心服务")):
                try:
                    import socket
                    _s = socket.create_connection(("127.0.0.1", _port), timeout=0.3)
                    _s.close()
                    _tips.append(_nm)
                except Exception:
                    pass
            if _tips:
                lines.append("Fairy 已就绪：%s在线。" % "、".join(_tips))
        except Exception:
            pass
    if _seg.get("devices", False):
        try:
            import json as _json
            _devs = (_json.load(open(os.path.join(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))), "fairy.json"),
                encoding="utf-8")).get("devices") or [])
            if _devs:
                line = "已连接 %d 个设备。" % len(_devs)
                # ★ 2026-10-15【机器人融合】顺带报一句机器人的情况。
                #   ★★ 踩过的坑：robot_root.py 住在 tools\robot\ 子目录里，
                #     而 sys.path 只有 tools\ —— 直接 import 会 ModuleNotFoundError，
                #     被 try 吞掉后表现为"功能写了但没生效"（本项目的老毛病）。
                #     所以这里显式把子目录加进 path，并且失败要留痕（_ROBOT_NOTE）。
                try:
                    _rr = _load_robot_root()
                    if _rr is None:
                        line += "机器人子系统没加载上。"
                    else:
                        _rd = _rr.CONF.get("robots") or {}
                        _rn = len([k for k, v in _rd.items()
                                   if isinstance(v, dict) and v.get("enabled") is not False])
                        if _rn:
                            _dry = bool((_rr.CONF.get("safety") or {}).get("dry_run", True))
                            line += "其中有 %d 台机器人%s。" % (_rn, "（演练模式）" if _dry else "")
                except Exception as _e:
                    _ROBOT_NOTE.append("%s: %s" % (type(_e).__name__, _e))
                    line += "机器人子系统出错。"
                lines.append(line)
        except Exception:
            pass

    # 1.6) 今日节日/节气（内置公历节日表 + 节气近似表，认不出就不报）
    if _seg.get("holiday", False):
        try:
            _md = time.strftime("%m-%d")
            _h = FESTIVALS.get(_md)
            if _h:
                lines.append("今天是%s。" % _h)
        except Exception:
            pass

    # 1.7) 今日待办（有待办才报：数量 + 最近一件）
    if _seg.get("todo", False):
        try:
            _td = (json.load(open(os.path.join(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))), "data", "todo.json"),
                encoding="utf-8")) or [])
            _pend = [x for x in _td if not x.get("done")]
            if _pend:
                _first = _pend[0].get("text", "")
                _with_at = any(x.get("at") for x in _pend)
                lines.append("今天有 %d 件待办%s，最近一件：%s。"
                             % (len(_pend), "，其中 %d 件定了时间" % sum(
                                 1 for x in _pend if x.get("at")) if _with_at else "",
                                _first))
        except Exception:
            pass

    # 1.8) 网络状态（快速探测外网，失败也不报错）
    if _seg.get("network", False):
        try:
            import socket as _sock
            _s = _sock.create_connection(("223.5.5.5", 53), timeout=1.5)
            _s.close()
            lines.append("网络正常。")
        except Exception:
            lines.append("网络当前不通，可能需要检查路由器。")

    # 1.9) API 余额（DeepSeek 账户，有凭据才报）
    if _seg.get("api_balance", False) or _seg.get("battery", False):
        try:
            _cred = os.path.expanduser(r"~/.dsh/.credentials.yaml")
            _key = None
            if os.path.exists(_cred):
                _t = open(_cred, encoding="utf-8", errors="replace").read()
                _mk = re.search(r"DEEPSEEK_API_KEY:\s*(\S+)", _t)
                if _mk:
                    _key = _mk.group(1).strip()
            if _key:
                import urllib.request as _ur
                _req = _ur.Request("https://api.deepseek.com/user/balance",
                                   headers={"Authorization": "Bearer " + _key,
                                            "Accept": "application/json"})
                with _ur.urlopen(_req, timeout=15) as _r:
                    _j = json.loads(_r.read().decode("utf-8"))
                _inf = (_j.get("balance_infos") or [None])[0]
                if _inf:
                    _sym = {"CNY": "\u00a5", "USD": "$"}.get(_inf.get("currency"), "")
                    _tb = str(_inf.get("total_balance", "")).strip()
                    if _tb:
                        lines.append("API 余额剩余 %s%s。" % (_sym, _tb))
        except Exception:
            pass

    # 2) 位置
    city, need_confirm = resolves_city(cfg)
    lat = lon = None
    if city and _seg.get("location", False):
        lines.append("检测到当前位置是%s。" % city)
        if need_confirm:
            lines.append("如果我定位错了，请在球上右键选「位置」改一下。")

    # 3) 天气
    # 优先用配置里存好的经纬度；没有就用城市名地理编码（比让 wttr 猜准）
    if _seg.get("weather", True):
        if lat is None and lon is None:
            if cfg.get("lat") and cfg.get("lon"):
                lat, lon = cfg.get("lat"), cfg.get("lon")
            elif city:
                lat, lon = geocode(city)
        w = None
        # ★★ 2026-10-10：未来三天【单独一路源】—— open-meteo 的每日预报带"下雨/下雪"。
        # 两路并发抓，省掉一个来回的等待（顺序抓实测要 4.7 秒，并发 ~2.5 秒）。
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
            # ★★ 未来三天：温度 + 下雨/下雪（直接说人话）
            # 甲 = open-meteo（带降水类型）；乙 = wttr（只有温度，兜底）
            _jia = []
            for _d in _fc:
                _jia.append({"称呼": day_name(_d.get("date")), "最低": _d.get("min"),
                             "最高": _d.get("max"), "天气": _d.get("desc") or "",
                             "降水": _d.get("precip") or ""})
            _yi = []
            for _d in (w.get("days") or []):
                _yi.append({"称呼": day_name(_d.get("date")), "最低": _d.get("min"),
                            "最高": _d.get("max")})
            _m = _cnsh_logic()                      # ★ 纯逻辑走 fairy_logic.py
            # ★★ 2026-10-18【用户要求「未来天气只播到明天」】
            #   forecast_days 控制播几天，默认 1 = 只讲到明天。
            #   想恢复"未来三天"就在 briefing.json 里加 "forecast_days": 3
            #   （注意是 briefing.json，不是 fairy.json —— 天气这些参数都在前者）。
            try:
                _fdays = int(cfg.get("forecast_days") or 1)
            except Exception:
                _fdays = 1
            _fdays = max(1, _fdays)
            _sent = ""
            if _m is not None and hasattr(_m, "三天天气句"):
                try:
                    _sent = _m.三天天气句(_jia, _yi, _fdays)
                except Exception as _e:
                    print("[briefing] fairy_logic.三天天气句 失败，退回本文件拼: %s: %s"
                          % (type(_e).__name__, _e), flush=True)
                    _sent = ""
            if not _sent:
                seg = []
                for d in (_jia or _yi)[:_fdays]:
                    seg.append("%s %s 到 %s 度" % (d.get("称呼") or "", d.get("最低"), d.get("最高")))
                if seg:
                    _pfx = "" if _fdays <= 1 else ("未来三天：" if _fdays >= 3 else "未来%d天：" % _fdays)
                    _sent = _pfx + "；".join(seg) + "。"
            if _sent:
                lines.append(_sent)
            if _seg.get("dress", True):
                lines.append("穿衣建议：" + clothing_advice(n["temp"]) + "。")
        else:
            lines.append("天气没能取到，这次就不猜了。")

    # 4) 新闻
    # news_count=0 时【连抓都不抓】，
    # 省掉 RSS 请求（开机更快），想看时用 `--news` 单独取。
    if _seg.get("news", False):
        _want_news = int(cfg.get("news_count") or 0) > 0
        news = get_news(cfg) if _want_news else []
        if not _want_news:
            pass          # 用户要求：开机不报新闻（连"取不到"都不说），等他想看时问我
        elif news:
            # 【为什么精简】用户反馈"新闻太多了"—— 8 条完整标题念出来要一分多钟。
            # 所以：声音只念少量 + 每条截断；完整列表照样进屏幕浮窗和文件（不丢信息）。
            lim = int(cfg.get("headline_max_chars") or 26)

            def short(x):
                x = str(x).strip()
                return x if len(x) <= lim else x[:lim].rstrip("，,。.、：: ") + "…"

            k = int(cfg.get("news_count") or 2)
            # 【修措辞】原来硬编码"先说两条"，k=1 时数字对不上。改成按实际条数说。
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

    # ★★ 2026-10-18【用户要求"启动只播天气和穿衣"】这三段原来**不受 briefing 开关控制**，
    #   所以即使把 weather/dress 之外的项都设成 false，它们照样会在开机时念：
    #     · hw_lines()     —— CPU 缓存错误 / 意外关机的新增量
    #     · boot_lines()   —— 开机自举的变化与机会
    #     · health_lines() —— "体检有 N 个新情况" + 解决办法 + "要我处理吗？"
    #   现在统一挂到配置项上，默认【全关】（只有显式写 true 才播）。
    # 这三个段各对应一个开关：hwline / bootline / health
    if _seg.get("hwline", False):
        lines.extend(hw_lines())

    if _seg.get("bootline", False):
        lines.extend(boot_lines())

    if _seg.get("health", False):
        lines.extend(health_lines())

    lines.append("播报完毕，随时听候吩咐。")
    return lines


if __name__ == "__main__":
    import sys                      # ← 必须最先导入（下面立刻要用 sys.argv）
    # 【按需取新闻】用户说"等我想你询问" —— 所以给一个单独的入口：
    #   py -3.12 briefing.py --news [关键词1 关键词2 ...]
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
