# -*- coding: utf-8 -*-
"""robot_http.py -- HTTP 后端的机器人适配器（机器人自带 WiFi、跑个小 HTTP 服务）

【适用】机器人板子/小电脑（ESP32 + WiFi、树莓派、Jetson）自己开一个端口，
      PC 通过 HTTP 发命令。比串口灵活（可跨网段），但要防"网络抖动导致命令丢"。

【机器人端要提供的接口】（与 FAIRY 其它服务同风格）
    GET  /robot/state          -> {"ok":true,"result":{"battery_pct":88,...}}
    POST /robot/act            <- {"cmd":"move","speed":0.3,"duration":0.5}
                               -> {"ok":true,"result":{...}}
    POST /robot/stop           <- {"why":"estop:manual"}   （必须幂等、必须最快）
    GET  /robot/caps           -> 能力表（可选；没有就用默认）

【生存期约定】
    · 每个请求都必须带超时（默认 3 秒）；超时=未知状态 -> 上层会补发 stop
    · 机器人端的 stop 必须在 100ms 内返回，不能等运动学结算

【安全】本适配器不做安全判断（那是 robot_safety 的事）；
       它只做"把命令发出去、把回复带回来"，以及参数再夹一次。
"""
import json
import urllib.error
import urllib.request

import robot_adapters as ra
import robot_root as rr


class HttpRobotAdapter(ra.RobotAdapter):
    name = "http"
    protocol = "HTTP（机器人自带 WiFi 服务）"
    dof = 0

    ACTIONS = ["move", "turn", "walk", "stop", "grip", "release", "home", "stand", "sit", "look"]

    def __init__(self, base="http://127.0.0.1:8890", dev_id="http:robot-01", timeout=3.0,
                 token=None, **_ignored):
        self.base = (base or "").rstrip("/")
        self.dev_id = dev_id
        self.timeout = float(timeout or 3.0)
        self.token = token
        lim = rr.CONF.get("safety") or {}
        self.max_speed = float(lim.get("max_speed_mps") or 0.6)
        self.max_yaw = float(lim.get("max_yaw_rps") or 1.2)
        self._last = {}

    def _req(self, path, payload=None, method=None):
        url = self.base + path
        data = None
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + str(self.token)
        m = method or ("POST" if payload is not None else "GET")
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=m)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                body = r.read().decode("utf-8", "replace")
            try:
                return json.loads(body)
            except Exception:
                return {"ok": False, "error": "回复不是 JSON: %s" % body[:120]}
        except urllib.error.HTTPError as e:
            return {"ok": False, "error": "HTTP %s" % e.code}
        except Exception as e:
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e), "timeout": "timed out" in str(e).lower()}

    # ---- 契约 ----
    def caps(self):
        c = super().caps()
        c.update({"kind": "gateway", "actions": list(self.ACTIONS), "discovery": "http",
                  "limits": {"max_speed_mps": self.max_speed, "max_yaw_rps": self.max_yaw},
                  "battery_pct": (self._last or {}).get("battery_pct")})
        r = self._req("/robot/caps")
        if r.get("ok") and isinstance(r.get("result"), dict):
            c.update(r["result"])
        return c

    def discover(self):
        r = self._req("/robot/state")
        online = bool(r.get("ok"))
        if online:
            self._last = r.get("result") or {}
        return [{"id": self.dev_id, "name": "HTTP 机器人 (%s)" % self.base, "kind": "gateway",
                 "online": online, "base": self.base,
                 "error": None if online else r.get("error")}]

    def state(self, dev_id):
        r = self._req("/robot/state")
        if r.get("ok"):
            self._last = r.get("result") or {}
        return {"ok": bool(r.get("ok")), "result": r.get("result"), "error": r.get("error"),
                "timeout": r.get("timeout")}

    def act(self, dev_id, action, params=None):
        p = dict(params or {})
        if "speed" in p:
            try:
                p["speed"] = max(-self.max_speed, min(self.max_speed, float(p["speed"])))
            except Exception:
                p.pop("speed", None)
        if "yaw" in p:
            try:
                p["yaw"] = max(-self.max_yaw, min(self.max_yaw, float(p["yaw"])))
            except Exception:
                p.pop("yaw", None)
        payload = {"cmd": action}
        payload.update(p)
        if action == "stop":
            r = self._req("/robot/stop", payload)
        else:
            r = self._req("/robot/act", payload)
        return {"ok": bool(r.get("ok")), "result": r.get("result"), "error": r.get("error"),
                "timeout": r.get("timeout")}

    def stop(self, dev_id=None, why=""):
        r = self._req("/robot/stop", {"why": why or "safety"}, method="POST")
        return {"ok": bool(r.get("ok")), "error": r.get("error"), "why": why}


ra.register(HttpRobotAdapter(), "http")
