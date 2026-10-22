"""Fairy 剪贴板历史（★ 2026-10-11 新增）：自动记录最近复制的文本。

存储：data/clip_history.json（最多 30 条，去重）。
用法：
    py fairy_clip.py --add 文本       # 追加一条
    py fairy_clip.py --list           # 列出历史
    py fairy_clip.py --get N          # 取第 N 条（写入剪贴板）
    py fairy_clip.py --watch          # 前台监控剪贴板（供集成用，球内嵌轮询更省事）
"""
import argparse
import json
import os
import sys
import time

HIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "clip_history.json")
MAX = 30


def _load():
    try:
        with open(HIST, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save(items):
    os.makedirs(os.path.dirname(HIST), exist_ok=True)
    with open(HIST, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)


def add(text):
    text = (text or "").strip()
    if not text or len(text) > 5000:
        return False
    items = _load()
    items = [x for x in items if x.get("text") != text]
    items.insert(0, {"text": text, "time": time.strftime("%m-%d %H:%M")})
    del items[MAX:]
    _save(items)
    return True


def list_items():
    return _load()


def get(n):
    items = _load()
    if 0 <= n < len(items):
        return items[n]["text"]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--add")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--get", type=int)
    ap.add_argument("--watch", action="store_true")
    a = ap.parse_args()
    try:
        if a.add:
            print("ok" if add(a.add) else "skip")
        elif a.list:
            for i, x in enumerate(list_items()):
                print("[%d] %s  %s" % (i, x["time"], x["text"][:120]))
        elif a.get is not None:
            t = get(a.get)
            if t is None:
                print("无此条")
            else:
                try:
                    import subprocess
                    p = subprocess.run(["powershell", "-NoProfile", "-Command",
                                        "Set-Clipboard -Value $input"],
                                       input=t, text=True, capture_output=True,
                                       timeout=10, encoding="utf-8")
                    print("已复制：" + t[:80])
                except Exception as e:
                    print("复制失败：" + str(e))
        elif a.watch:
            last = ""
            while True:
                try:
                    import subprocess
                    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                                        "Get-Clipboard -Raw"], capture_output=True,
                                       text=True, timeout=5, encoding="utf-8")
                    cur = (r.stdout or "").strip()
                except Exception:
                    cur = ""
                if cur and cur != last:
                    last = cur
                    if add(cur):
                        print("记录：" + cur[:80], flush=True)
                time.sleep(1.5)
        else:
            ap.print_help()
        return 0
    except Exception as e:
        print("错误: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
