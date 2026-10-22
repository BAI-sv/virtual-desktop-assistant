# -*- coding: utf-8 -*-
"""Fairy 网络一键诊断：测连通性并给出建议。

用法：
    py -3.12 fairy_netdiag.py             # 打印诊断结果
    py -3.12 fairy_netdiag.py --json      # JSON 输出
    py -3.12 fairy_netdiag.py --say       # 诊断后直接语音播报（供自定义指令/菜单用）
"""
import json
import os
import socket
import subprocess
import sys

_NW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _ping(host, count=2):
    try:
        r = subprocess.run(["ping", "-n", str(count), "-w", "1500", host],
                           capture_output=True, timeout=12,
                           creationflags=_NW)
        return r.returncode == 0
    except Exception:
        return False


def _port(host, port, t=1.5):
    try:
        s = socket.create_connection((host, port), t)
        s.close()
        return True
    except Exception:
        return False


def _gateway():
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "(Get-NetRoute -DestinationPrefix '0.0.0.0/0').NextHop"
                            " | Select-Object -First 1"],
                           capture_output=True, text=True, timeout=10,
                           creationflags=_NW)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def diag():
    """返回 [(名称, 是否正常), ...]。"""
    items = [("本机回环", _ping("127.0.0.1"))]
    gw = _gateway()
    if gw:
        items.append(("网关 %s" % gw, _ping(gw)))
    items.append(("公网 DNS 223.5.5.5", _ping("223.5.5.5")))
    items.append(("外网 www.baidu.com", _ping("www.baidu.com")))
    return items


def report(items):
    lines = []
    for name, ok in items:
        lines.append("%s：%s" % (name, "正常" if ok else "不通"))
    if all(ok for _, ok in items):
        lines.append("网络一切正常")
    else:
        bad = [n for n, ok in items if not ok]
        lines.append("发现异常：%s" % "、".join(bad))
        if any(not ok for n, ok in items if n.startswith("网关") or n.startswith("公网")):
            lines.append("建议：先检查路由器/光猫是否断电，网线是否松动，再试试重启网卡")
        else:
            lines.append("建议：可能是 DNS 或外网问题，稍后再试或检查代理设置")
    return lines


def _say(text):
    try:
        _d = os.path.dirname(os.path.abspath(__file__))
        subprocess.Popen([sys.executable, os.path.join(_d, "fairy_say.py"), "--async", text],
                         creationflags=_NW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


if __name__ == "__main__":
    items = diag()
    if "--json" in sys.argv:
        print(json.dumps({"ok": all(x[1] for x in items),
                          "items": [{"name": n, "ok": b} for n, b in items]},
                         ensure_ascii=False))
    else:
        lines = report(items)
        for x in lines:
            print(x)
        if "--say" in sys.argv:
            _say("网络诊断：" + "。".join(lines))
