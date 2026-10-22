# -*- coding: utf-8 -*-
"""robot_mock.py -- 模拟后端（没有硬件也能把全链路跑通，并能注入故障）

【为什么必须有它】
  1) 机器人还没买，但安全层、技能层、上层接口现在就能开发与自测
  2) 故障注入：真机上"断线""急停不响应""状态说谎"这些情况很难复现，
     仿真里可以按需制造 —— 这是唯一能在买硬件之前验证联锁的办法
  3) 自测的判据是【可观测的副作用】：
        · 下发次数 commands_sent      —— 干跑时这个数必须不变
        · 累计行程 distance_m         —— 用来验"限速/限程"有没有生效
        · 停机次数 stops              —— 用来验急停/断线联锁真的调到了硬件

【故障旋钮】（故障注入，默认全关）
    fail_act      = "raise" | "ok_false" | None    下发时抛异常 / 返回失败
    silent_stop   = True                           停机不响应（真机上叫"刹不住"）
    state_lie     = True                           状态里谎报电量 100%
    latency_s     = 0.0                            每次下发的人为延迟
    battery_pct   = 起始电量
    tilt_deg      = 起始倾角
"""
import time

import robot_adapters as ra
import robot_root as rr


class MockRobot:
    def __init__(self, dev_id, name, kind, dof, battery_pct=88.0):
        self.id = dev_id
        self.name = name
        self.kind = kind
        self.dof = dof
        self.battery_pct = battery_pct
        self.tilt_deg = 0.0
        self.online = True
        self.pose = {"x": 0.0, "y": 0.0, "yaw": 0.0}
        self.joints = [0.0] * dof
        self.gripper = 0.0            # 0 张开 -> 1 闭合
        self.commands_sent = 0        # ★ 可观测副作用（干跑时必须不变）
        self.distance_m = 0.0
        self.stops = 0
        self.errors = 0
        self.last_action = ""

    def to_dict(self):
        return {"id": self.id, "name": self.name, "kind": self.kind, "online": self.online,
                "dof": self.dof, "battery_pct": round(self.battery_pct, 1),
                "tilt_deg": round(self.tilt_deg, 1)}


class MockAdapter(ra.RobotAdapter):
    name = "mock"
    protocol = "模拟器（无硬件；可注入故障）"
    sim = True
    dof = 6

    # ★ 只列"真会动硬件"的动作：look/goto 这类在模拟器里是空操作，
    #   放进这里会让安全闸以为"这台机器人支持它"，反而绕过配置里的 allow 列表
    ACTIONS = ["move", "turn", "walk", "stand", "sit", "stop", "grip", "release",
               "home", "pose", "nod", "wave"]

    def __init__(self, fail_act=None, fail_actions=None, silent_stop=False, state_lie=False,
                 latency_s=0.0, battery_pct=88.0, tilt_deg=0.0, robots=None, **_ignored):
        self.fail_act = fail_act
        # ★ 只让指定动作失败（真机上更像"这个关节坏了"，而不是整机下线）
        self.fail_actions = set(fail_actions or [])
        self.silent_stop = bool(silent_stop)
        self.state_lie = bool(state_lie)
        self.latency_s = float(latency_s or 0.0)
        self._robots = {}
        if robots:
            for r in robots:
                self._robots[r["id"]] = MockRobot(r["id"], r.get("name", r["id"]),
                                                  r.get("kind", "wheeled"), int(r.get("dof", 0)),
                                                  float(r.get("battery_pct", 88.0)))
        else:
            # 默认两台：一台机械臂（桌面）、一台轮式底盘
            self._robots["mock:arm-01"] = MockRobot("mock:arm-01", "模拟机械臂", "arm", 6, 88.0)
            self._robots["mock:base-01"] = MockRobot("mock:base-01", "模拟底盘", "wheeled", 0, 62.0)
        # ★ per-robot actions：机械臂不该有 "look"（转头是云台/四足的事）
        self._robot_actions = {
            "mock:arm-01": ["move", "turn", "grip", "release", "home", "stop", "walk"],
            "mock:base-01": ["move", "turn", "walk", "stand", "sit", "stop", "look"],
        }
        if tilt_deg:
            for r in self._robots.values():
                r.tilt_deg = float(tilt_deg)

    # ---- 契约 ----
    def caps(self, dev_id=None):
        """★ 支持按机器人返回能力：不同机身能做的动作不一样（机械臂不会走路，底座不会回零）。

        安全闸的"只读闸"会拿这里返回的 actions 去比对；
        如果这里图省事把所有动作都报上去，等于把配置里的 allow 列表废掉。
        """
        c = super().caps()
        acts = self._robot_actions.get(dev_id) if dev_id else None
        c.update({"kind": "mixed", "dof": 6, "actions": list(acts or self.ACTIONS),
                  "discovery": "sim", "sim": True,
                  "limits": {"max_speed_mps": 0.6, "max_yaw_rps": 1.2}})
        return c

    def discover(self):
        return [r.to_dict() for r in self._robots.values()]

    def state(self, dev_id):
        r = self._robots.get(dev_id)
        if not r:
            return {"ok": False, "error": "没有这台机器人: %s" % dev_id}
        batt = 100.0 if self.state_lie else r.battery_pct
        return {"ok": True, "result": {
            "online": r.online, "battery_pct": round(batt, 1), "tilt_deg": round(r.tilt_deg, 2),
            "pose": dict(r.pose), "joints": list(r.joints), "gripper": r.gripper,
            "commands_sent": r.commands_sent, "distance_m": round(r.distance_m, 3),
            "stops": r.stops, "errors": r.errors, "last_action": r.last_action,
            "sim": True,
        }}

    def act(self, dev_id, action, params=None):
        p = params or {}
        r = self._robots.get(dev_id)
        if not r:
            return {"ok": False, "error": "没有这台机器人: %s" % dev_id}
        if not r.online:
            return {"ok": False, "error": "离线"}
        if self.latency_s:
            time.sleep(self.latency_s)
        # 故障注入
        if self.fail_act == "raise":
            raise RuntimeError("mock:act-raise（注入的下发异常）")
        if self.fail_act == "ok_false" or (action or "").lower() in self.fail_actions:
            r.errors += 1
            return {"ok": False, "error": "mock:act-ok-false（注入的下发失败）"}

        a = (action or "").lower()
        allowed = self._robot_actions.get(dev_id)
        if allowed is not None and a not in allowed:
            r.errors += 1
            return {"ok": False, "error": "模拟器：%s 不支持动作 %s（支持：%s）" % (dev_id, a, ",".join(allowed))}
        r.last_action = a
        if a == "stop":
            r.stops += 1
            r.pose["yaw"] = r.pose["yaw"]
            return {"ok": True, "result": {"stopped": True, "silent": self.silent_stop}}
        if a in ("state", "pose", "ping", "caps", "list", "status"):
            return {"ok": True, "result": r.to_dict()}
        # 会动到硬件的动作：计数（干跑验证靠它）
        r.commands_sent += 1
        if a in ("move", "walk"):
            sp = float(p.get("speed") or 0.0)
            dur = float(p.get("duration") or 0.5)
            r.distance_m += abs(sp) * dur
            r.pose["x"] += sp * dur
        elif a == "turn":
            r.pose["yaw"] += float(p.get("yaw") or 0.0) * float(p.get("duration") or 0.5)
        elif a == "grip":
            r.gripper = min(1.0, float(p.get("force") or 0.5))
        elif a == "release":
            r.gripper = 0.0
        elif a in ("home", "stand", "sit", "nod", "wave", "look", "goto"):
            pass
        else:
            return {"ok": False, "error": "模拟器不认识的动作: %s" % a}
        r.battery_pct = max(0.0, r.battery_pct - 0.05)
        return {"ok": True, "result": {"accepted": a, "commands_sent": r.commands_sent,
                                       "distance_m": round(r.distance_m, 3)}}

    def stop(self, dev_id=None, why=""):
        if self.silent_stop:
            # ★ 故障注入：假装停了，其实没停（用来验证"停了之后还要复查"）
            return {"ok": False, "error": "mock:silent-stop（停机无响应）", "why": why}
        ids = [dev_id] if dev_id else list(self._robots.keys())
        out = []
        for i in ids:
            r = self._robots.get(i)
            if r:
                r.stops += 1
            out.append({"id": i, "stopped": True})
        return {"ok": True, "result": out, "why": why}

    # ---- 测试辅助 ----
    def counters(self, dev_id):
        r = self._robots.get(dev_id)
        if not r:
            return {}
        return {"commands_sent": r.commands_sent, "distance_m": round(r.distance_m, 3),
                "stops": r.stops, "errors": r.errors}

    def set_fault(self, **kw):
        for k, v in kw.items():
            if hasattr(self, k):
                setattr(self, k, v)
        return {k: getattr(self, k) for k in kw if hasattr(self, k)}


# 自动登记（import 本模块即注册）
ra.register(MockAdapter(), "mock")
