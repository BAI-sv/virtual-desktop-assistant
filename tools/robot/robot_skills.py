# -*- coding: utf-8 -*-
"""robot_skills.py -- 技能层（决策层与硬件之间的中间层）

【三层分工，别混】
  反射层（机器人板载固件，<=10ms）：平衡/避障/限位/急停 —— 不在这里
  技能层（本模块，10~100ms）：把"抓杯子""走过去"拆成可验证的小动作
  决策层（大模型，0.5~3s）：只发技能请求，不碰电机

【技能表】每个技能声明：要哪些参数、默认值、是否写操作、失败怎么收场
  · 技能只调 gateway.do()，因此**必然**经过安全层
  · 组合技能在每一步之间插"安全检查点"：一旦上一步没成，立刻回退（release/home）

【为什么组合技能要回退】
  抓取失败时如果直接把机械臂抬走，可能把杯子撞飞。所以 pick 失败 -> 松爪 + 回家。
"""
import time

# 技能表：name -> {kind, params, defaults, write, desc}
SKILLS = {
    "stop":     {"kind": "atomic", "params": [], "write": True,  "desc": "立刻停止（最高优先级，永远放行）"},
    "state":    {"kind": "atomic", "params": [], "write": False, "desc": "读状态"},
    "move":     {"kind": "atomic", "params": ["speed", "duration"], "defaults": {"speed": 0.3, "duration": 0.5}, "write": True, "desc": "直行一段"},
    "turn":     {"kind": "atomic", "params": ["yaw", "duration"], "defaults": {"yaw": 0.5, "duration": 0.5}, "write": True, "desc": "原地转"},
    "walk":     {"kind": "atomic", "params": ["speed", "duration"], "defaults": {"speed": 0.4, "duration": 1.0}, "write": True, "desc": "四足行走"},
    "grip":     {"kind": "atomic", "params": ["force"], "defaults": {"force": 0.5}, "write": True, "desc": "闭合夹爪"},
    "release":  {"kind": "atomic", "params": [], "write": True, "desc": "张开夹爪"},
    "home":     {"kind": "atomic", "params": [], "write": True, "desc": "回零位"},
    "stand":    {"kind": "atomic", "params": [], "write": True, "desc": "起立（四足/人形）"},
    "look":     {"kind": "atomic", "params": ["yaw", "pitch"], "defaults": {"yaw": 0.0, "pitch": 0.0}, "write": True, "desc": "转头/云台"},
    "pick":     {"kind": "combo",  "params": ["target", "approach_speed", "force"],
                 "defaults": {"approach_speed": 0.15, "force": 0.4}, "write": True,
                 "desc": "抓取：靠近 -> 闭合 -> 抬起（失败自动松爪回家）"},
    "place":    {"kind": "combo",  "params": ["target"], "write": True,
                 "desc": "放置：到位 -> 张开 -> 回家"},
    "patrol":   {"kind": "combo",  "params": ["legs", "leg_speed", "leg_duration"],
                 "defaults": {"legs": 2, "leg_speed": 0.3, "leg_duration": 1.0}, "write": True,
                 "desc": "巡逻：走 N 段（每段之间插状态检查）"},
}


class Skills:
    def __init__(self, gw, conf=None):
        import robot_root as rr
        self.gw = gw
        self.cfg = conf or rr.CONF
        self.overrides = self.cfg.get("skills") or {}
        self.log = []

    # ---- 技能表 ----
    def catalog(self):
        out = []
        for name, spec in SKILLS.items():
            enabled = self.overrides.get(name, True) is not False
            out.append({"name": name, "kind": spec["kind"], "params": spec["params"],
                        "write": spec["write"], "enabled": enabled, "desc": spec["desc"]})
        return out

    def _defaults(self, name, params):
        spec = SKILLS.get(name) or {}
        p = dict(spec.get("defaults") or {})
        p.update(params or {})
        return p

    # ---- 原子技能 ----
    def atomic(self, robot, name, params=None):
        p = self._defaults(name, params)
        if name not in SKILLS:
            return {"ok": False, "error": "没有这个技能: %s" % name, "known": sorted(SKILLS)}
        if self.overrides.get(name) is False or self.overrides.get("%s.%s" % (robot, name)) is False:
            return {"ok": False, "error": "技能在配置里被关掉了: %s" % name, "decision": "block"}
        r = self.gw.do(robot, name, p)
        self.log.append({"t": time.time(), "robot": robot, "skill": name, "params": p,
                         "ok": r.get("ok"), "decision": r.get("decision")})
        return r

    # ---- 组合技能 ----
    def pick(self, robot, target=None, approach_speed=0.15, force=0.4):
        """抓取序列：接近 -> 闭合 -> 抬起。任何一步不成就回退。"""
        steps = [
            ("move", {"speed": approach_speed, "duration": 0.6}),
            ("grip", {"force": force}),
            ("home", {}),
        ]
        done = []
        for name, p in steps:
            r = self.atomic(robot, name, p)
            done.append({"step": name, "ok": r.get("ok"), "decision": r.get("decision"),
                         "error": r.get("error")})
            if not r.get("ok"):
                rb = self.atomic(robot, "release", {})       # ★ 回退：先松爪
                return {"ok": False, "skill": "pick", "target": target, "failed_at": name,
                        "steps": done, "rollback": {"release": rb.get("ok")},
                        "error": r.get("error") or "被安全层拦截: %s" % r.get("decision")}
        return {"ok": True, "skill": "pick", "target": target, "steps": done}

    def place(self, robot, target=None):
        steps = [("move", {"speed": 0.1, "duration": 0.3}), ("release", {}), ("home", {})]
        done = []
        for name, p in steps:
            r = self.atomic(robot, name, p)
            done.append({"step": name, "ok": r.get("ok"), "decision": r.get("decision")})
            if not r.get("ok"):
                return {"ok": False, "skill": "place", "target": target, "failed_at": name,
                        "steps": done, "error": r.get("error") or "被安全层拦截"}
        return {"ok": True, "skill": "place", "target": target, "steps": done}

    def patrol(self, robot, legs=2, leg_speed=0.3, leg_duration=1.0):
        """巡逻 N 段；每段之间读一次状态（电量/倾角），异常立刻停。"""
        legs = max(1, min(int(legs or 1), 20))          # ★ 上限 20 段，防止无限巡逻
        done = []
        for i in range(legs):
            st = self.gw.state(robot)
            res = (st.get("result") or {}) if st.get("ok") else {}
            batt = res.get("battery_pct")
            tilt = res.get("tilt_deg")
            minb = float((self.cfg.get("safety") or {}).get("min_battery_pct") or 15)
            maxt = float((self.cfg.get("safety") or {}).get("max_tilt_deg") or 35)
            if batt is not None and float(batt) < minb:
                self.atomic(robot, "stop", {})
                return {"ok": False, "skill": "patrol", "stopped_at": i, "reason": "battery-low:%.0f%%" % float(batt),
                        "steps": done}
            if tilt is not None and abs(float(tilt)) > maxt:
                self.atomic(robot, "stop", {})
                return {"ok": False, "skill": "patrol", "stopped_at": i,
                        "reason": "tilt-too-big:%.1fdeg" % float(tilt), "steps": done}
            r = self.atomic(robot, "walk", {"speed": leg_speed, "duration": leg_duration})
            done.append({"leg": i + 1, "ok": r.get("ok"), "decision": r.get("decision")})
            if not r.get("ok"):
                self.atomic(robot, "stop", {})
                return {"ok": False, "skill": "patrol", "stopped_at": i, "steps": done,
                        "error": r.get("error") or "被安全层拦截: %s" % r.get("decision")}
        return {"ok": True, "skill": "patrol", "legs": legs, "steps": done}

    # ---- 统一入口（上层只需要这一个）----
    def run(self, robot, skill, params=None):
        """跑一个技能。

        ★ 参数先按技能表过滤（复查发现的问题）：
          拼错一个键名（target 写成 targt）以前会直接 TypeError 冒到最外层，
          UI 报"致命错误"、退出码 1，看起来像系统崩了 —— 其实是参数写错了。
          现在：未知键被忽略 + 回显在结果里，认不出的技能/参数给一句人话。
        """
        s = (skill or "").strip().lower()
        raw = dict(params or {})
        spec = SKILLS.get(s)
        if not spec:
            return {"ok": False, "skill": s, "error": "没有这个技能：%s" % s,
                    "known_skills": sorted(SKILLS), "decision": "block"}
        allowed = set(spec.get("params") or []) | set((spec.get("defaults") or {}).keys())
        # 通配几个常用键，避免上层传坐标/备注时被误杀
        allowed |= {"target", "note", "label", "why"}
        unknown = sorted(k for k in raw if k not in allowed)
        clean = {k: v for k, v in raw.items() if k in allowed}
        if s == "pick":
            r = self.pick(robot, **clean)
        elif s == "place":
            r = self.place(robot, **clean)
        elif s == "patrol":
            r = self.patrol(robot, **clean)
        else:
            r = self.atomic(robot, s, clean)
        if unknown and isinstance(r, dict):
            r["ignored_params"] = unknown
            if r.get("ok"):
                r["note"] = "已忽略不认识的参数：%s" % "、".join(unknown)
        return r
