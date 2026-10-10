# -*- coding: utf-8 -*-
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
    ok, yuan, cur, why = get_balance()
    if not ok:
        return {"ok": False, "level": "unknown", "yuan": None, "currency": cur, "why": why}
    return {"ok": True, "level": level(yuan, warn, alert), "yuan": yuan,
            "currency": cur, "why": ""}

def sentence(res, boot=False):
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
