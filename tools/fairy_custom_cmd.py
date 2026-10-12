# -*- coding: utf-8 -*-
"""Fairy 自定义语音指令：使用者定义"说XX→做XX"。

数据：fairy.json 的 custom_cmds 段：
    [{trigger:"查快递", type:"app", target:"拼多多"},
     {trigger:"开机自检", type:"cmd", target:"..."},
     {trigger:"查天气", type:"say", target:"现在外面 29 度"}]
type: app(打开软件) / url(打开网页) / say(回复一句话) / cmd(运行命令)
被 fairy_ear.py 识别后优先匹配；被 fairy_ball 菜单管理。
"""
import json
import os
import subprocess
import sys

CONF = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "fairy.json")


def _load():
    try:
        with open(CONF, encoding="utf-8") as f:
            return (json.load(f).get("custom_cmds") or [])
    except Exception:
        return []


def _save(cmds):
    try:
        with open(CONF, encoding="utf-8") as f:
            j = json.load(f)
        j["custom_cmds"] = cmds
        with open(CONF, "w", encoding="utf-8") as f:
            json.dump(j, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def add(trigger, ctype, target):
    cmds = _load()
    cmds.append({"trigger": trigger.strip(), "type": ctype, "target": target.strip()})
    return _save(cmds)


def remove(i):
    cmds = _load()
    if 0 <= i < len(cmds):
        cmds.pop(i)
        return _save(cmds)
    return False


def match(text):
    """返回命中的指令（识别文本包含 trigger 即命中；None = 未命中）。"""
    t = (text or "").strip().lower()
    for c in _load():
        if c.get("trigger") and c["trigger"].lower() in t:
            return c
    return None


def run(cmd, say=None, bubble=None):
    """执行指令动作。say/bubble 为可选回调（球/ear 传入）。返回说明文字。"""
    ctype = (cmd or {}).get("type")
    target = (cmd or {}).get("target") or ""
    if ctype == "say":
        if say:
            say(target, "ok")
        return target
    if ctype == "url":
        try:
            import webbrowser
            webbrowser.open(target)
            return "已打开网页：" + target
        except Exception as e:
            return "打开网页失败：" + str(e)[:40]
    if ctype == "app":
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import fairy_apps as _fa
            _r = _fa.launch_by_name(target)
            if _r and "ok" in str(_r).lower():
                return "已打开：" + target
            return "已尝试打开：" + target
        except Exception as e:
            return "打开软件失败：" + str(e)[:40]
    if ctype == "cmd":
        try:
            subprocess.Popen(target, shell=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return "已执行：" + target
        except Exception as e:
            return "执行命令失败：" + str(e)[:40]
    return "未知指令类型：" + str(ctype)


if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "--add":
        if len(a) < 4:
            print("用法: --add 触发词 类型(app/url/say/cmd) 目标")
        else:
            print("已添加" if add(a[1], a[2], " ".join(a[3:])) else "保存失败")
    elif a and a[0] == "--list":
        for i, c in enumerate(_load()):
            print("[%d] %s -> %s: %s" % (i, c.get("trigger"), c.get("type"), c.get("target")))
    elif a and a[0] == "--del":
        print("已删除" if remove(int(a[1])) else "无此条")
    elif a and a[0] == "--match":
        c = match(" ".join(a[1:]))
        print(c if c else "未命中")
    else:
        print("用法: --add 触发 类型 目标 / --list / --del N / --match 文本")
