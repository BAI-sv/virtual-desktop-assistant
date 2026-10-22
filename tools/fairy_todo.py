# -*- coding: utf-8 -*-
"""Fairy 待办清单：增删/完成/到期播报。

数据：D:\\FAIRY\\data\\todo.json
用法：
    py -3.12 fairy_todo.py --add "内容|时间(可选)"   # 时间格式 HH:MM，如 "交报告|14:30"
    py -3.12 fairy_todo.py --list
    py -3.12 fairy_todo.py --done 0
    py -3.12 fairy_todo.py --del 0
    py -3.12 fairy_todo.py --due --json            # 球内到期检查用
"""
import json
import os
import sys
import time

DATA = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "data", "todo.json")


def _load():
    try:
        with open(DATA, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save(items):
    os.makedirs(os.path.dirname(DATA), exist_ok=True)
    with open(DATA, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


def add(text, at=""):
    items = _load()
    items.append({"id": int(time.time()), "text": text.strip(),
                  "at": at.strip(), "done": False})
    _save(items)
    return items


def list_items():
    return _load()


def done(i):
    items = _load()
    if 0 <= i < len(items):
        items[i]["done"] = True
        _save(items)
        return items[i]
    return None


def delete(i):
    items = _load()
    if 0 <= i < len(items):
        it = items.pop(i)
        _save(items)
        return it
    return None


def due(now=None):
    """返回已到时间的未完成项。now 格式 HH:MM。"""
    now = now or time.strftime("%H:%M")
    return [x for x in _load() if not x.get("done") and x.get("at") and x["at"] <= now]


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "--add":
        t = " ".join(a[1:])
        at = ""
        if "|" in t:
            t, at = t.split("|", 1)
        add(t, at)
        print("已添加：%s%s" % (t.strip(), " @" + at.strip() if at.strip() else ""))
    elif a and a[0] == "--list":
        for i, x in enumerate(list_items()):
            print("[%d] %s %s%s" % (i, "完成" if x.get("done") else "待办",
                                    x["text"], " @" + x["at"] if x.get("at") else ""))
    elif a and a[0] == "--done":
        print("已完成" if done(int(a[1])) else "无此条")
    elif a and a[0] == "--del":
        print("已删除" if delete(int(a[1])) else "无此条")
    elif a and a[0] == "--due":
        ds = due()
        if "--json" in a:
            print(json.dumps([{"id": x.get("id"), "text": x.get("text"),
                               "at": x.get("at")} for x in ds], ensure_ascii=False))
        else:
            for x in ds:
                print(x.get("text"))
    else:
        print("用法: --add '内容|时间' / --list / --done N / --del N / --due")
