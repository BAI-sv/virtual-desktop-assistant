# -*- coding: utf-8 -*-
"""
把 Fairy 原声包清洗成可匹配的索引（voice_index.json）。

豆包那份 fairy_lines.json 有三个坑，这里都处理掉：
  1) 380 条 text 为空 —— 空串参与匹配会恒真误命中
  2) 36 条含游戏富文本 <color=#FFAF2C>…</color> —— 朗读和匹配都会被污染
  3) 重复文本 —— 按归一化文本去重，保留最短（通常最干净）的那条

注意：标点集合用「码点集合」构造，不写进字符类字面量——
中文标点里含 ASCII 引号，直接塞进 r"[...]" 会把字符串截断。
"""
import collections
import json
import os
import re
import sys
import wave
import fairy_root

BASE = fairy_root.VOICE_SRC
VOICES = os.path.join(BASE, "voices")
OUT = os.path.join(fairy_root.WORKDIR, "fairyx", "voice", "voice_index.json")

TAG = re.compile(r"<[^>]*>")

# 归一化时要丢掉的字符：ASCII 可见非字母数字 + 常见中英文标点
PUNCT = {chr(c) for c in range(0x21, 0x7F) if not chr(c).isalnum()}
PUNCT |= set("，。！？、；：“”‘’（）《》〈〉【】「」『』…—～·　")
PUNCT -= set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")


def clean(s):
    """去掉游戏富文本标签与首尾空白。"""
    return TAG.sub("", s or "").strip()


def norm(s):
    """归一化：只留实义字符，用于匹配。"""
    return "".join(ch for ch in clean(s) if ch not in PUNCT)


def wav_sec(path):
    try:
        with wave.open(path) as w:
            return round(w.getnframes() / float(w.getframerate()), 2)
    except Exception:
        return -1.0


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    rows = []
    n_tag, n_empty, n_missing = 0, 0, 0
    types = collections.Counter()
    for fn in sorted(os.listdir(VOICES)):
        if not fn.endswith(".json"):
            continue
        wav = fn[:-5] + ".wav"
        wp = os.path.join(VOICES, wav)
        if not os.path.exists(wp):
            n_missing += 1
            continue
        try:
            meta = json.load(open(os.path.join(VOICES, fn), encoding="utf-8"))
        except Exception:
            continue
        raw = meta.get("transcription") or ""
        if TAG.search(raw):
            n_tag += 1
        text = clean(raw)
        key = norm(text)
        if not key:
            n_empty += 1
            continue
        types[meta.get("voiceType") or "Unknown"] += 1
        rows.append({"t": key, "r": text, "w": wav,
                     "y": meta.get("voiceType") or "Unknown",
                     "d": wav_sec(wp)})

    # 去重：同一句只留最短音频
    best = {}
    for r in rows:
        k = r["t"]
        if k not in best or (0 < r["d"] < best[k]["d"]):
            best[k] = r
    uniq = sorted(best.values(), key=lambda x: x["d"])

    json.dump({"voices_dir": "voices",              # 相对索引文件所在目录，整包可搬
               "src_voices_dir": VOICES,            # 原始来源，仅作记录
               "count": len(uniq),
               "lines": uniq},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))

    print("原始可读条目        : %d" % len(rows))
    print("  其中含富文本标签  : %d（已清标签）" % n_tag)
    print("  空文本（已丢弃）  : %d" % n_empty)
    print("  缺 wav（已丢弃）  : %d" % n_missing)
    print("去重后可用台词      : %d" % len(uniq))
    print("类型分布            : %s" % dict(types.most_common()))
    durs = [r["d"] for r in uniq if r["d"] > 0]
    print("音频时长 最短/中位/最长: %.2f / %.2f / %.2f 秒"
          % (min(durs), sorted(durs)[len(durs) // 2], max(durs)))
    print("索引输出            : %s (%.0f KB)" % (OUT, os.path.getsize(OUT) / 1024))
    print("\n最短的 8 条（会被选进「常用语」喂给模型）:")
    for r in uniq[:8]:
        print("   %5.2fs  [%s] %s" % (r["d"], r["y"], r["r"]))
    print("\n最长的 2 条（不适合当参考音频）:")
    for r in uniq[-2:]:
        print("   %5.2fs  %s" % (r["d"], r["r"][:60]))


if __name__ == "__main__":
    sys.exit(main() or 0)
