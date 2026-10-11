# -*- coding: utf-8 -*-
"""
balance_watch.py —— DeepSeek 余额预警（共享模块）

【用户要求】"需要加预警"
【为什么需要】余额见底 = API 调用直接失败 = Fairy 会突然变哑巴。
  用户不可能天天去网页查，所以必须【自己盯着、主动说】。

【三档】
  ok    : 余额 >= warn（默认 30 元）       → 不打扰
  warn  : warn > 余额 >= alert（默认 10）→ 开机播报/体检里提一句
  alert : 余额 < alert（默认 10）        → 用 alert 级别说出来（会暂停音乐，确保听见）
  另外：查询失败/没 key → unknown（不误报）

【被谁用】
  · briefing.py     开机播报里带一句
  · health_check.py 体检里作为一个检查项
  · fairy_ball.py   帧循环里【节流每 10 分钟查一次】，跨档就说话

【用法】
  py -3.12 balance_watch.py            打印当前余额与档位
  py -3.12 balance_watch.py --json     机器可读
  py -3.12 balance_watch.py --speak    跨档时输出应说的话（给球用）
"""
import json
import os
import re
import sys
import time
import urllib.request
import fairy_root

CRED = os.path.expanduser(r"~\.dsh\.credentials.yaml")
STATE = fairy_root.log("balance_state.json")
URL = "https://api.deepseek.com/user/balance"

WARN_YUAN = 30.0
ALERT_YUAN = 10.0


def _key():
    try:
        t = open(CRED, encoding="utf-8", errors="replace").read()
        m = re.search(r"DEEPSEEK_API_KEY:\s*(\S+)", t)
        return m.group(1).strip() if m else ""
    except Exception as e:
        print("[balance] 读凭据失败: %s: %s" % (type(e).__name__, e), flush=True)
        return ""


def get_balance(timeout=15):
    """返回 (ok, 余额float, 币种, 说明)。失败不抛异常。"""
    k = _key()
    if not k:
        return False, 0.0, "CNY", "没找到 API key"
    try:
        req = urllib.request.Request(URL, headers={
            "Authorization": "Bearer " + k, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            j = json.loads(r.read().decode("utf-8"))
        infos = j.get("balance_infos") or []
        if not infos:
            return False, 0.0, "CNY", "接口没返回余额"
        b = infos[0]
        return True, float(b.get("total_balance") or 0), b.get("currency") or "CNY", ""
    except Exception as e:
        return False, 0.0, "CNY", "%s: %s" % (type(e).__name__, str(e)[:80])


def level(yuan, warn=WARN_YUAN, alert=ALERT_YUAN):
    if yuan < alert:
        return "alert"
    if yuan < warn:
        return "warn"
    return "ok"


def check(warn=WARN_YUAN, alert=ALERT_YUAN):
    """给别的模块调：返回 dict。"""
    ok, yuan, cur, why = get_balance()
    if not ok:
        return {"ok": False, "level": "unknown", "yuan": None, "currency": cur, "why": why}
    return {"ok": True, "level": level(yuan, warn, alert), "yuan": yuan,
            "currency": cur, "why": ""}


def sentence(res, boot=False):
    """生成一句人话。boot=True 时哪怕 ok 也说一句（开机播报风格）。"""
    if not res.get("ok"):
        return ""
    y, lv = res["yuan"], res["level"]
    if lv == "alert":
        return "紧急提醒：API 余额只剩 %.2f 元了，快要不够用了，记得充值。" % y
    if lv == "warn":
        return "提醒一下，API 余额还有 %.2f 元，不多了。" % y
    if boot:
        return "API 余额 %.2f 元，够用。" % y
    return ""


def should_speak(res, force=False):
    """只在该说时说（跨档 / alert）。避免每 10 分钟念一遍余额。"""
    st = {}
    try:
        if os.path.exists(STATE):
            st = json.load(open(STATE, encoding="utf-8"))
    except Exception as e:
        print("[balance] 读状态失败: %s" % e, flush=True)
    prev = st.get("last_level") or "unknown"
    lv = res.get("level") or "unknown"
    st["last_level"] = lv
    st["last_yuan"] = res.get("yuan")
    st["last_check"] = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        os.replace(tmp, STATE)
    except Exception as e:
        print("[balance] 写状态失败: %s" % e, flush=True)
    if not res.get("ok"):
        return ""
    # alert 每次都值得说（别漏）；warn/ok 只在【跨档】时说，避免重复念
    if lv == "alert":
        return sentence(res)
    if lv != prev and lv in ("warn", "ok"):
        return sentence(res)
    return ""


def main():
    a = sys.argv[1:]
    res = check()
    if "--json" in a:
        print(json.dumps(res, ensure_ascii=False))
        return 0
    if "--speak" in a:
        s = should_speak(res, force="--force" in a)
        print(s)
        return 0
    if not res["ok"]:
        print("查询失败: %s" % res["why"])
        return 1
    print("余额 %.2f %s · 档位 %s（warn<%.0f / alert<%.0f）"
          % (res["yuan"], res["currency"], res["level"], WARN_YUAN, ALERT_YUAN))
    s = sentence(res, boot=True)
    if s:
        print("开机该说:", s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
