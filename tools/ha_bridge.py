# -*- coding: utf-8 -*-
"""
ha_bridge.py —— FairyX <-> Home Assistant 双向联动桥

【作用】
  FairyX 通过本桥连上 Home Assistant 中枢，实现双向联动：
    设备状态 -> 球播报 ：轮询 HA 状态，变化时调 fairy_host 播报
    球指令   -> 设备   ：act 走 HA 的 service（turn_on/off/set…）
  HA 里接入的所有设备（ewelink / 米家 / 美的 …）都统一走这里，
  这比每个厂商单独对接省事得多 —— 只认 HA 一套接口。

【用法】
  py -3.12 ha_bridge.py --discover             列出 HA 里的设备
  py -3.12 ha_bridge.py --state switch.xxx     读一台设备状态
  py -3.12 ha_bridge.py --act switch.xxx on    控制（on/off/toggle/set）
  py -3.12 ha_bridge.py watch --interval 5     轮询，状态变化自动播报
  py -3.12 ha_bridge.py watch --only 客厅      只监听名字含「客厅」的

【前置】logs/ha_token 里要有 HA 的 Long-Lived Access Token。
"""
import argparse
import json
import os
import sys
import time

# Windows 控制台默认 gbk，输出中文会炸 ascii —— 统一按 utf-8 输出
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from device_adapters import HomeAssistantAdapter  # noqa: E402

HOST_PUSH = "http://127.0.0.1:19388/api/fairy/push"
_STATE_ZH = {"on": "打开", "off": "关闭", "open": "开启", "closed": "关闭",
             "home": "在家", "not_home": "离家", "unavailable": "离线",
             "playing": "播放中", "paused": "暂停", "idle": "待机",
             "unlocked": "已解锁", "locked": "已锁定", "motion": "有人移动",
             "clear": "无人移动"}


def _speak(text):
    """让球把 text 念出来（走 fairy_host 本地服务，DSH 不在也能用）。"""
    import urllib.request
    try:
        data = json.dumps({"text": text, "source": "ha"}).encode("utf-8")
        req = urllib.request.Request(HOST_PUSH, data=data,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=6) as r:
            return r.read().decode("utf-8", "replace")[:200]
    except Exception as e:
        return "播报失败（fairy_host 没起?）: %s" % e


def _describe(name, state, attr):
    """把实体状态翻译成一句人话。"""
    zh = _STATE_ZH.get(str(state), str(state))
    if state in ("on", "off") and attr:
        if "current_temperature" in attr:
            return "%s%s，当前温度 %.1f 度" % (name, zh, float(attr["current_temperature"]))
        if "brightness" in attr:
            return "%s 亮度调到 %s" % (name, attr["brightness"])
    return "%s%s" % (name, zh)


def _watch(interval, only):
    a = HomeAssistantAdapter()
    if not a._ready():
        print("✗ 未配置 HA token —— 把 Long-Lived Access Token 写入 logs/ha_token")
        return
    prev = {}
    print("开始监听 HA 状态变化（每 %.1fs 一轮），Ctrl+C 退出" % interval)
    while True:
        try:
            devs = a.discover()
        except Exception as e:
            print("获取状态失败: %s" % e)
            time.sleep(interval)
            continue
        cur = {d["id"]: d for d in devs if not d.get("error")}
        for eid, d in cur.items():
            if only and only not in str(d.get("name", eid)):
                continue
            st = d.get("state")
            if eid in prev and prev[eid] != st:
                msg = _describe(d.get("name", eid), st, {})
                print("[%s] %s" % (time.strftime("%H:%M:%S"), msg), flush=True)
                _speak(msg)
            prev[eid] = st
        time.sleep(interval)


def main():
    argv = sys.argv[1:]
    # 兼容用户习惯：--discover/--state/--act/--watch 也当命令
    _flag = {"--discover": "discover", "--state": "state", "--act": "act",
             "--watch": "watch", "--list": "discover"}
    if argv and argv[0] in _flag:
        argv = [_flag[argv[0]]] + argv[1:]
    ap = argparse.ArgumentParser(description="FairyX <-> Home Assistant 双向联动桥")
    ap.add_argument("cmd", nargs="?", default="watch", help="discover/state/act/watch")
    ap.add_argument("--entity", default="", help="实体 id，如 switch.客厅灯")
    ap.add_argument("--action", default="", help="on/off/toggle/set")
    ap.add_argument("--interval", type=float, default=5.0, help="watch 轮询间隔秒")
    ap.add_argument("--only", default="", help="只监听名字含此关键词的实体")
    ap.add_argument("--set", action="append", default=[], help="set 参数 key=value")
    ap.add_argument("pos", nargs="*", help="位置参数: entity [action] [key=val...]")
    args = ap.parse_args(argv)

    a = HomeAssistantAdapter()
    if args.cmd == "discover":
        for d in a.discover():
            print(json.dumps(d, ensure_ascii=False))
    elif args.cmd == "state":
        entity = args.entity or (args.pos[0] if len(args.pos) > 0 else "")
        if not entity:
            print("需 --entity 或位置参数"); return
        print(json.dumps(a.state(entity), ensure_ascii=False))
    elif args.cmd == "act":
        entity = args.entity or (args.pos[0] if len(args.pos) > 0 else "")
        action = args.action or (args.pos[1] if len(args.pos) > 1 else "")
        if not (entity and action):
            print("需 entity 和 action"); return
        p = {}
        for kv in (args.set + args.pos[2:]):
            k, _, v = kv.partition("=")
            p[k.strip()] = v.strip()
        print(json.dumps(a.act(entity, action, p), ensure_ascii=False))
    else:
        _watch(args.interval, args.only)


if __name__ == "__main__":
    main()
