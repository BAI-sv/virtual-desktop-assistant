# -*- coding: utf-8 -*-
"""FairyX 易微联（eWeLink）云 API 直连桥
========================================
参考 skydiver/ewelink-api（MIT）的登录/控制协议实现：
- 登录：Sign 签名（HMAC-SHA256）+ appid/phoneNumber/email 账号密码
- 拉设备：REST GET /api/user/device
- 控制：优先 REST 老接口，失败走 WebSocket（dispatch -> userOnline -> update）

配置（config.json）：
  "smart_home": { "ewelink": {
      "email": "易微联账号（邮箱或手机号）",
      "password": "密码",
      "region": "cn"   # cn/as/eu/us；留空自动探测
  } }
"""
import base64
import hashlib
import hmac
import json
import os
import random
import string
import time
import urllib.error
import urllib.parse
import urllib.request

D = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG_PATH = os.path.join(D, "config.json")

# 易微联官方 HA 插件凭证（来自 CoolKit-Technologies/ha-addon-backEnd）
APP_ID = "4s1FXKC9FaGfoqXhmXSJneb3qcm1gOak"
APP_SECRET = "oKvCM06gvwkRbfetd6qWRrbC3rFrbIpV"

REGION_API = {
    "cn": "https://cn-api.coolkit.cc:8080",
    "as": "https://as-api.coolkit.cc:8080",
    "eu": "https://eu-api.coolkit.cc:8080",
    "us": "https://us-api.coolkit.cc:8080",
}

_cache = {"at": None, "api_key": None, "region": None, "ts": 0.0}


def _cfg():
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _ew():
    return ((_cfg().get("smart_home") or {}).get("ewelink") or {})


def _nonce():
    return "".join(random.choice(string.ascii_lowercase + string.digits)
                   for _ in range(8))


def _ts():
    return int(time.time())


def _sign(secret, body):
    """JS JSON.stringify 等价：无空格、键序保持，HMAC-SHA256 -> base64。"""
    msg = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    return base64.b64encode(
        hmac.new(secret.encode("utf-8"), msg.encode("utf-8"),
                 hashlib.sha256).digest()).decode("ascii")


def _request(method, url, body=None, headers=None, timeout=15):
    data = None
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode("utf-8", errors="replace"))
        except Exception:
            return {"error": e.code, "msg": str(e.reason)}
    except Exception as e:
        return {"error": -1, "msg": str(e)}


def login(force=False):
    """登录易微联云，支持邮箱或手机号，region 自动跳转。返回 (at, api_key, region)。"""
    ew = _ew()
    account = (ew.get("email") or "").strip()
    password = (ew.get("password") or "").strip()
    region = (ew.get("region") or "").strip().lower() or "cn"
    if not account or not password:
        return None
    if not force and _cache["at"] and time.time() - _cache["ts"] < 1800:
        return (_cache["at"], _cache["api_key"], _cache["region"])
    base = REGION_API.get(region, REGION_API["cn"])
    is_phone = account.startswith("+") or (account.isdigit() and len(account) >= 6)
    body = {
        "appid": APP_ID,
        "email": None if is_phone else account,
        "phoneNumber": account if is_phone else None,
        "password": password,
        "ts": _ts(),
        "version": 8,
        "nonce": _nonce(),
    }
    hdrs = {"Authorization": "Sign " + _sign(APP_SECRET, body)}
    r = _request("POST", base + "/api/user/login", body, hdrs)
    # 301：区域不对 -> 按返回 region 重试一次
    if r.get("error") == 301 and r.get("region"):
        region = r["region"]
        base = REGION_API.get(region, base)
        r = _request("POST", base + "/api/user/login", body, hdrs)
    if r.get("error") == 0 and r.get("at"):
        _cache.update({"at": r["at"],
                       "api_key": (r.get("user") or {}).get("apikey"),
                       "region": region, "ts": time.time()})
        return (_cache["at"], _cache["api_key"], region)
    return None


def get_devices():
    """拉取设备清单。返回 [{deviceid, name, type, switch, params}]；失败返回 None。"""
    cred = login()
    if not cred:
        return None
    at, _, region = cred
    base = REGION_API.get(region, REGION_API["cn"])
    qs = urllib.parse.urlencode({
        "lang": "en", "appid": APP_ID, "ts": _ts(), "version": 8, "getTags": 1})
    r = _request("GET", base + "/api/user/device?" + qs,
                 headers={"Authorization": "Bearer " + at})
    if r.get("error") != 0:
        return None
    out = []
    for d in (r.get("devicelist") or []):
        out.append({
            "deviceid": d.get("deviceid"),
            "name": (d.get("name") or "").strip() or (d.get("deviceid") or ""),
            "type": (d.get("extra") or {}).get("uiid"),
            "switch": (d.get("params") or {}).get("switch"),
            "params": d.get("params") or {},
        })
    return out


def control_device(deviceid, params):
    """控制单个设备。先试 REST 老接口，失败走 WebSocket。"""
    cred = login()
    if not cred:
        return False
    at, api_key, region = cred
    base = REGION_API.get(region, REGION_API["cn"])
    # 1) REST 老接口
    r = _request("POST", base + "/api/device/status",
                 {"deviceid": deviceid, "params": params},
                 headers={"Authorization": "Bearer " + at})
    if r.get("error") == 0:
        return True
    # 2) WebSocket 控制
    try:
        return _ws_control(cred, deviceid, params)
    except Exception:
        return False


def _ws_control(cred, deviceid, params):
    import websocket  # websocket-client
    at, api_key, region = cred
    base = REGION_API.get(region, REGION_API["cn"])
    # dispatch 拿 ws 地址
    d = _request("POST", base + "/dispatch/app",
                 {"accept": "ws", "appid": APP_ID,
                  "nonce": _nonce(), "ts": _ts(), "version": 8},
                 headers={"Authorization": "Bearer " + at})
    if d.get("error") != 0:
        return False
    ws_url = "wss://%s:%s/api/ws" % (d["domain"], d["port"])
    ws = websocket.create_connection(ws_url, timeout=10)
    try:
        seq = int(time.time() * 1000)
        ws.send(json.dumps({
            "action": "userOnline", "version": 8, "ts": _ts(), "at": at,
            "userAgent": "app", "apikey": api_key,
            "appid": APP_ID, "nonce": _nonce(), "sequence": seq}))
        time.sleep(0.5)
        ws.send(json.dumps({
            "action": "update", "deviceid": deviceid, "apikey": api_key,
            "userAgent": "app", "sequence": seq + 1, "ts": _ts(), "params": params}))
        time.sleep(0.8)
        return True
    finally:
        try:
            ws.close()
        except Exception:
            pass


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "devices":
        ds = get_devices()
        if ds is None:
            print("登录失败或未配置账号（检查 config.json smart_home.ewelink 与网络）")
        else:
            for d in ds:
                print("%s | %s | switch=%s" % (d["deviceid"], d["name"], d.get("switch")))
    else:
        print("用法: ewelink_bridge.py devices")
