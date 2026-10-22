# -*- coding: utf-8 -*-
"""robot_serial.py -- 串口/星闪网关后端的机器人适配器

【适用】机器人板子（ESP32/STM32/星闪模组）负责无线与舵机，通过 USB 串口把结果吐给 PC。
      这是"先把链路跑通"最便宜的一条路：一块板子 + 一根线。

【板子端要实现的最小协议】（一行一个 JSON，UTF-8）
    PC -> 板子: {"cmd":"state","seq":1}
    板子 -> PC: {"ok":true,"seq":1,"result":{"battery_pct":88,"tilt_deg":1.2}}
    PC -> 板子: {"cmd":"move","speed":0.3,"duration":0.5,"seq":2}
    板子 -> PC: {"ok":true,"seq":2,"result":{"accepted":"move"}}
    PC -> 板子: {"cmd":"stop","why":"estop:manual","seq":3}
    板子 -> PC: {"ok":true,"seq":3}

【★ 板子端必须自己做到三件事（PC 端靠不住）】
    1. 收到的任何命令若解析失败 —— 回复 {"ok":false,"error":"..."}，绝不静默
    2. 连续 X 秒没收到 PC 的命令 —— 自己停（板载看门狗；X 建议 1~2 秒）
    3. 本地急停按钮/限位开关 —— 优先级高于串口来的任何命令

【若本机没装 pyserial】discover() 返回空、act() 返回明确错误，不抛异常。
"""
import robot_adapters as ra
import robot_link as rl
import robot_root as rr


class SerialRobotAdapter(ra.RobotAdapter):
    name = "serial"
    protocol = "串口/星闪网关（JSON 行协议）"
    dof = 0

    ACTIONS = ["move", "turn", "walk", "stop", "grip", "release", "home", "stand", "sit", "look"]

    def __init__(self, port=None, baud=115200, dev_id=None, timeout=3.0,
                 link=None, max_speed_mps=None, max_yaw_rps=None, **_ignored):
        self.port = port
        self.baud = int(baud or 115200)
        self.timeout = float(timeout or 3.0)
        self.dev_id = dev_id or ("serial:%s" % (port or "auto"))
        lim = rr.CONF.get("safety") or {}
        self.max_speed = float(max_speed_mps if max_speed_mps is not None else (lim.get("max_speed_mps") or 0.6))
        self.max_yaw = float(max_yaw_rps if max_yaw_rps is not None else (lim.get("max_yaw_rps") or 1.2))
        self._link = link or rl.SerialLink(port=self.port, baud=self.baud)
        self.ch = rl.JsonLineChannel(self._link, default_timeout=self.timeout)
        self._last_state = {}

    # ---- 契约 ----
    def caps(self):
        c = super().caps()
        c.update({"kind": "gateway", "actions": list(self.ACTIONS), "discovery": "serial",
                  "limits": {"max_speed_mps": self.max_speed, "max_yaw_rps": self.max_yaw},
                  "battery_pct": (self._last_state or {}).get("battery_pct")})
        return c

    def discover(self):
        out = []
        try:
            import serial.tools.list_ports as lp
            for x in lp.comports():
                desc = x.description or ""
                out.append({"id": "serial:" + x.device, "name": "%s (%s)" % (desc or "串口设备", x.device),
                            "kind": "gateway", "online": True, "port": x.device})
        except Exception as e:
            self.ch.errors.append({"error": "pyserial 不可用: %s" % e})
            return []
        if self.port and not any(d["id"] == "serial:" + self.port for d in out):
            out.append({"id": "serial:" + self.port, "name": self.port, "kind": "gateway", "online": True,
                        "port": self.port})
        return out

    def state(self, dev_id):
        r = self.ch.send("state")
        if r.get("ok"):
            res = r.get("result") or {}
            self._last_state = res if isinstance(res, dict) else {}
        return {"ok": bool(r.get("ok")), "result": r.get("result"), "error": r.get("error"),
                "timeout": r.get("timeout")}

    def act(self, dev_id, action, params=None):
        p = dict(params or {})
        # ★ 客户端也夹一次（板子端还要再夹一次：双保险；安全层是第三道）
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
        if action in ("stop",):
            p.setdefault("why", "pc-request")
        r = self.ch.send(action, **p)
        return {"ok": bool(r.get("ok")), "result": r.get("result"), "error": r.get("error"),
                "timeout": r.get("timeout")}

    def stop(self, dev_id=None, why=""):
        r = self.ch.send("stop", timeout=1.0, why=why or "safety")
        return {"ok": bool(r.get("ok")), "error": r.get("error"), "why": why}


ra.register(SerialRobotAdapter(port=None), "serial")
