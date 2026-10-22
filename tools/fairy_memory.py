# -*- coding: utf-8 -*-
"""
fairy_memory.py —— Fairy 使用记忆与习惯学习

★ 2026-10-11【用户要求】"使用者经常说的话和功能，数据最好能自动保存，
   随着时间的推移和使用的增加，能让模型更准确的识别和分析。"

记录并学习：
  ① 常用短语/指令    —— 语音/文字指令拆词统计（去停用词），耳朵识别热词用
  ② 常用功能          —— 翻译 / 字幕 / 音乐 / 天气 / 听歌识曲… 调用次数
  ③ 活跃时段          —— 按小时统计使用分布，球/守护据此调整打扰策略
  ④ 偏好引擎          —— 最近选过的大模型引擎

数据文件：D:\\FAIRY\\data\\fairy_memory.json（不存在自动建，损坏自动重置）
自动保存：每次记录后写盘（小文件，原子写）。
"""
import json
import os
import re
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.dirname(HERE)
MEM_DIR = os.path.join(D, "data")
MEM_FILE = os.path.join(MEM_DIR, "fairy_memory.json")

# 拆词时去掉的弱词（中文 + 英文）
_STOP = set(("的 了 是 在 我 你 他 她 它 们 和 与 也 就 都 很 给 把 被 让 对 从 到 向 往 这 那 个 吗 呢 吧 啊 哦 呀 嗯 "
              "the a an to of in on for with at by from is are was were be do does did have has had not no can could "
              "would should will shall may might please 请 帮 我 想 要 能 可以 一下 现在 今天 明天 昨天 打开 关闭 启动 "
              "停止 开 关 查 看 说 讲 告诉 播放 暂停 继续 退出 再见").split())

# 功能关键词 → 功能名（指令/短语归类用）
_FEATURE_MAP = [
    (("翻译", "translate", "译"), "翻译"),
    (("字幕", "subtitle", "video_subtitle", "视频字幕"), "视频字幕"),
    (("音乐", "music", "歌", "播放音乐", "放歌"), "音乐"),
    (("天气", "weather", "气温", "下雨", "预报"), "天气"),
    (("听歌识曲", "识曲", "songrec", "这是什么歌"), "听歌识曲"),
    (("新闻", "news", "头条", "简报"), "新闻"),
    (("提醒", "定时", "待办", "任务", "闹钟"), "提醒/定时"),
    (("开关机", "关机", "重启", "睡眠"), "开关机"),
    (("电话", "通话", "call_translate", "通话翻译"), "通话翻译"),
    (("窗口", "播报", "guard", "window"), "窗口守护"),
    (("设备", "蓝牙", "ble", "扫描"), "设备扫描"),
]

_LOCK = threading.Lock()


def _default():
    return {"phrases": {}, "features": {}, "hours": {}, "engines": [],
            "total_uses": 0, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "updated": time.strftime("%Y-%m-%d %H:%M:%S")}


def load():
    """读记忆；损坏/缺失返回默认。"""
    try:
        if os.path.exists(MEM_FILE):
            with open(MEM_FILE, encoding="utf-8") as f:
                d = json.load(f)
            for k, v in _default().items():
                d.setdefault(k, v)
            return d
    except Exception:
        pass
    return _default()


def save(d):
    """原子写盘。"""
    d["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        os.makedirs(MEM_DIR, exist_ok=True)
        tmp = MEM_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, MEM_FILE)
        return True
    except Exception:
        return False


def _split_words(text):
    """拆中文词 + 英文单词，去掉弱词和标点。"""
    if not text:
        return []
    # 英文单词
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}", text)
    # 中文：连续中文串按 2 字滑动拆（简单有效，无需分词库）
    cn = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    for seg in cn:
        if len(seg) <= 4:
            words.append(seg)
        else:
            for i in range(len(seg) - 1):
                words.append(seg[i:i + 2])
    return [w.lower() for w in words if w.lower() not in _STOP and len(w) >= 2]


def record_phrase(text):
    """记录一句用户说的话/指令：拆词频率 +1。返回本次新增的词。"""
    with _LOCK:
        d = load()
        hit = []
        for w in _split_words(text):
            d["phrases"][w] = d["phrases"].get(w, 0) + 1
            hit.append(w)
        d["total_uses"] = d.get("total_uses", 0) + 1
        _record_hour(d)
        save(d)
        return hit


def record_use(feature, extra=""):
    """记录一次功能调用（翻译/字幕/音乐…）。feature 已归类的名称或原词。"""
    with _LOCK:
        d = load()
        name = map_feature(feature) or feature
        d["features"][name] = d["features"].get(name, 0) + 1
        d["total_uses"] = d.get("total_uses", 0) + 1
        _record_hour(d)
        if extra:
            d.setdefault("feature_detail", {}).setdefault(name, []).append(extra)
        save(d)
        return name


def record_engine(engine):
    """记录用户最近选过的引擎（strata/ollama/llm/custom…）。"""
    with _LOCK:
        d = load()
        eng = d.setdefault("engines", [])
        eng = [e for e in eng if e != engine]
        eng.insert(0, engine)
        d["engines"] = eng[:10]
        save(d)


def _record_hour(d):
    h = str(time.localtime().tm_hour)
    d["hours"][h] = d["hours"].get(h, 0) + 1


def map_feature(text):
    """把一句话/词归类成功能名；归不上返回 None。"""
    t = (text or "").lower()
    for kws, name in _FEATURE_MAP:
        if any(k.lower() in t for k in kws):
            return name
    return None


def top_phrases(n=10):
    """最高频短语（耳朵热词 / 球推荐用）。"""
    d = load()
    return sorted(d["phrases"].items(), key=lambda x: -x[1])[:n]


def top_features(n=8):
    """最常用功能。"""
    d = load()
    return sorted(d["features"].items(), key=lambda x: -x[1])[:n]


def hot_phrases(threshold=3):
    """超过阈值的热词（识别时优先匹配）。"""
    return [w for w, c in top_phrases(50) if c >= threshold]


def busy_hours():
    """使用最多的 3 个小时段。"""
    d = load()
    return [int(h) for h, _ in sorted(d["hours"].items(), key=lambda x: -x[1])[:3]]


def suggest_on_startup(min_uses=5):
    """★ 启动时建议自动启动的常用功能（使用次数超阈值）。
    返回 [功能名…]，供 fairy_host / 球启动后调用。"""
    return [f for f, c in top_features(20) if c >= min_uses]


def stats():
    """一行摘要，球菜单显示用。"""
    d = load()
    return {"total": d.get("total_uses", 0),
            "phrases": len(d["phrases"]), "features": d["features"],
            "top": top_features(3), "engines": d.get("engines", [])}


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--demo":
        record_phrase("帮我看一下今天的天气")
        record_phrase("给我翻译这句话")
        record_phrase("播放音乐")
        record_use("翻译")
        record_use("天气")
        record_engine("strata")
        print(json.dumps(stats(), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(stats(), ensure_ascii=False, indent=1))
        print("热词:", hot_phrases(1))
        print("建议自动启动:", suggest_on_startup(1))
