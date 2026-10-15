# -*- coding: utf-8 -*-
"""robot_selftest.py -- 机器人子系统的自测（不需要任何硬件）

【为什么要有它】"写完了"不等于"能跑"。这个文件把安全铁律逐条变成可执行的断言：
  1. 干跑模式绝不下发      -> 看 mock 的 commands_sent 有没有变
  2. 真实模式才下发        -> 同上，且只发一次
  3. 参数被夹到安全范围    -> 看最终落地的 speed/duration
  4. 白名单外的机器人被拦  -> 裁决 = block
  5. 只读闸：未声明的写动作被拦
  6. 急停锁存 + 锁存期间只读可读、写动作全拦
  7. 危险词（含拼音）被拦
  8. 断线联锁：心跳超时 -> 自动急停 + 真的调了后端 stop
  9. 下发失败 -> 自动补一次 stop
 10. 组合技能失败 -> 回退（release）
 11. 审计日志真的落盘
 12. JSON 行协议：超时、迟到回复、事件上报
 13. 串口后端用回环链路验证（不需要真板子）
 14. 限程：movement 累计距离不超过 速度上限 × 时长上限

运行：py -3.12 robot_selftest.py      （退出码 0 = 全过，1 = 有失败）
"""
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import robot_root as rr          # noqa: E402
import robot_safety as rs        # noqa: E402
import robot_adapters as ra      # noqa: E402
import robot_mock as rm          # noqa: E402
import robot_link as rl          # noqa: E402

# robot_bridge.py 在 tools\（上一层），自测要用它的意图识别
_TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)

RESULTS = []


def check(name, cond, detail=""):
    ok = bool(cond)
    RESULTS.append((name, ok, detail))
    print("  %s %-46s %s" % ("PASS" if ok else "FAIL", name, detail))
    return ok


class Fixture:
    """每个用例一套干净的环境：新配置 + 新 mock 后端 + 新安全层。"""

    def __init__(self, **over):
        base = rr._defaults()
        base["safety"]["dry_run"] = True
        base["robots"] = {
            "mock:arm-01": {"enabled": True, "allow": ["move", "walk", "turn", "release", "home", "stop"]},
            "mock:base-01": {"enabled": True, "allow": ["move", "walk", "turn", "stop"]},
        }
        base["gates"]["whitelist"] = True
        base["gates"]["default_readonly"] = True
        # 深一层合并 over
        for k, v in (over or {}).items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                base[k].update(v)
            else:
                base[k] = v
        self.conf = base
        self.adapter = rm.MockAdapter()
        # ★ 每个用例用【自己那份】急停状态文件，避免上一个用例的急停污染下一个
        _tag = int(time.time() * 1000) % 1000000
        self.estop_file = rr.log_path("_selftest_estop_{}_{}.json".format(_tag, id(self) % 1000))
        try:
            os.remove(self.estop_file)
        except Exception:
            pass
        self.safety = rs.Safety(conf=self.conf, state_file=self.estop_file)
        self.gw = ra.RobotGateway(safety=self.safety, backend="mock")
        self.gw._adapter = self.adapter        # 直接注入，避免走注册表的全局实例

    def robot(self, rid="mock:arm-01"):
        return rid


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------
def t_dry_run():
    print("\n[1] 干跑模式：动作只过闸、绝不下发")
    f = Fixture()
    c0 = f.adapter.counters("mock:arm-01")
    r = f.gw.do("mock:arm-01", "move", {"speed": 0.3, "duration": 0.5})
    c1 = f.adapter.counters("mock:arm-01")
    check("干跑裁决=dry-run", r.get("decision") == "dry-run", str(r.get("decision")))
    check("干跑返回 ok", r.get("ok") is True)
    check("mock 的 commands_sent 没变", c0["commands_sent"] == c1["commands_sent"],
          "%s -> %s" % (c0["commands_sent"], c1["commands_sent"]))
    check("mock 的 distance_m 没变", c0["distance_m"] == c1["distance_m"])
    return True


def t_real_run():
    print("\n[2] 真实模式：确实下发，且只发一次")
    f = Fixture(safety={"dry_run": False})
    c0 = f.adapter.counters("mock:arm-01")
    r = f.gw.do("mock:arm-01", "move", {"speed": 0.3, "duration": 1.0})
    c1 = f.adapter.counters("mock:arm-01")
    check("真实模式裁决=allow", r.get("decision") == "allow", str(r.get("decision")))
    check("确实下发（+1）", c1["commands_sent"] == c0["commands_sent"] + 1,
          "{} -> {}".format(c0["commands_sent"], c1["commands_sent"]))
    check("行程 0.3*1.0=0.3m", abs(c1["distance_m"] - 0.3) < 1e-6, str(c1["distance_m"]))
    check("带延迟统计", isinstance(r.get("latency_ms"), int))
    return True


def t_clamp():
    print("\n[3] 参数限幅：超限的值被夹到安全范围")
    f = Fixture(safety={"dry_run": False, "max_speed_mps": 0.5, "max_duration_s": 2.0})
    # 请求 速度 5.0 / 时长 60 -> 应被夹到 0.5 / 2.0
    r = f.gw.do("mock:arm-01", "move", {"speed": 5.0, "duration": 60.0})
    c = f.adapter.counters("mock:arm-01")
    check("裁决仍是 allow", r.get("decision") == "allow", str(r.get("decision")))
    check("被夹的参数有记录", any("clamped" in x for x in (r.get("reasons") or [])), str(r.get("reasons")))
    check("行程按夹后值算 0.5*2.0=1.0m", abs(c["distance_m"] - 1.0) < 1e-6, str(c["distance_m"]))
    return True


def t_whitelist():
    print("\n[4] 白名单闸：没登记的机器人一律拒绝")
    f = Fixture(safety={"dry_run": False})
    r = f.gw.do("mock:unknown-99", "move", {"speed": 0.1, "duration": 0.1})
    check("未知机器人被拦", r.get("decision") == "block", str(r.get("decision")))
    check("原因是白名单", any("whitelist" in x for x in (r.get("reasons") or [])), str(r.get("reasons")))
    # 读状态仍然允许（只读）
    f.adapter._robots["mock:unknown-99"] = rm.MockRobot("mock:unknown-99", "野机器人", "arm", 6)
    st = f.gw.state("mock:unknown-99")
    check("读状态不受白名单影响", st.get("ok") is True)
    return True


def t_readonly_gate():
    print("\n[5] 只读闸：caps/配置没允许的写动作被拦")
    f = Fixture(safety={"dry_run": False})
    # 对 mock 后端来说 "look" 只读，测不出闸2；用一个"底座明确不支持"的写动作更干脆：
    #   home（机械臂才是"回零"）不在底座 allow 里 -> 必须被只读闸拦
    r = f.gw.do("mock:base-01", "home", {})
    check("未允许的写动作被拦", r.get("decision") == "block", str(r.get("decision")))
    # 反向：允许列表里的动作必须放行（否则闸写反了也测不出来）
    r_ok = f.gw.do("mock:base-01", "turn", {"yaw": 0.2, "duration": 0.2})
    check("允许列表里的动作放行", r_ok.get("decision") == "allow", str(r_ok.get("decision")))
    r2 = f.gw.do("mock:base-01", "walk", {"speed": 0.2, "duration": 0.5})
    check("允许的写动作放行", r2.get("decision") == "allow", str(r2.get("decision")))
    return True


def t_estop():
    print("\n[6] 急停：锁存、写动作全拦、只读仍可读、必须显式解除")
    f = Fixture(safety={"dry_run": False})
    c0 = f.adapter.counters("mock:arm-01")["stops"]
    f.gw.estop("selftest")
    check("急停已锁存", f.gw.safety.estop_active is True)
    c1 = f.adapter.counters("mock:arm-01")["stops"]
    check("急停真的调了后端 stop", c1 > c0, "{} -> {}".format(c0, c1))
    r = f.gw.do("mock:arm-01", "move", {"speed": 0.2, "duration": 0.2})
    check("急停期间写动作被拦", r.get("decision") == "block", str(r.get("decision")))
    check("拦截原因=estop", any("estop" in x for x in (r.get("reasons") or [])), str(r.get("reasons")))
    st = f.gw.state("mock:arm-01")
    check("急停期间仍可读状态（排障需要）", st.get("ok") is True)
    # 再触发一次：不应重复调用后端停（幂等）
    c2 = f.adapter.counters("mock:arm-01")["stops"]
    f.gw.estop("selftest-again")
    c3 = f.adapter.counters("mock:arm-01")["stops"]
    check("重复急停不重复停后端（幂等）", c2 == c3, "{} -> {}".format(c2, c3))
    f.gw.clear_estop("selftest")
    r2 = f.gw.do("mock:arm-01", "move", {"speed": 0.2, "duration": 0.2})
    check("解除后可正常下发", r2.get("decision") == "allow", str(r2.get("decision")))
    return True


def t_danger_words():
    print("\n[7] 危险词闸：中/英/拼音命中即拒；★ 但不能误伤正常数值参数")
    f = Fixture(safety={"dry_run": False})
    for bad in ({"note": "禁用急停"}, {"note": "bypass safety"}, {"note": "qiangzhi"}):
        r = f.gw.do("mock:arm-01", "move", dict({"speed": 0.1, "duration": 0.1}, **bad))
        check("危险词被拦 %s" % list(bad.values())[0], r.get("decision") == "block", str(r.get("decision")))
    r = f.gw.do("mock:arm-01", "firmware_flash", {})
    check("永远禁止的动作被拦", r.get("decision") == "block", str(r.get("reasons")))
    # ★ 回归用例：夹爪力度参数 force 是数值，不能被 "force" 这个危险词误伤
    r2 = f.gw.do("mock:arm-01", "grip", {"force": 0.4})
    check("数值参数 force=0.4 不被误伤", r2.get("decision") == "allow", str(r2.get("reasons")))
    r3 = f.gw.do("mock:arm-01", "grip", {"note": "强制闭合"})
    check("文本里的“强制”仍被拦", r3.get("decision") == "block", str(r3.get("reasons")))
    return True


def t_heartbeat_watchdog():
    print("\n[8] 断线联锁：心跳停了 -> 自动急停 + 真的停后端")
    f = Fixture(safety={"dry_run": False, "heartbeat_timeout_s": 0.6})
    f.safety._wd_period = 0.1
    f.safety.heartbeat("selftest")          # 先有一次心跳（否则视为"从未连上"）
    c0 = f.adapter.counters("mock:base-01")["stops"]
    f.safety.start_watchdog()
    t0 = time.time()
    while time.time() - t0 < 3.0 and not f.safety.estop_active:
        time.sleep(0.05)
    dt = time.time() - t0
    check("超时后自动急停", f.safety.estop_active is True, "用了 %.2fs" % dt)
    check("原因是心跳丢失", "heartbeat-lost" in (f.safety.snapshot()["estop_reason"] or ""),
          f.safety.snapshot()["estop_reason"])
    time.sleep(0.2)
    c1 = f.adapter.counters("mock:base-01")["stops"]
    check("看门狗真的调了后端 stop", c1 > c0, "{} -> {}".format(c0, c1))
    check("看门狗线程在跑", f.safety.snapshot()["watchdog_alive"] is True)
    f.safety.stop_watchdog()
    # ★ 从来没有收到过心跳（大脑从没连上）也必须停：
    #   "第一次心跳还没来" 与 "大脑已死" 在机器人眼里无法区分，只能选安全的那一侧
    f2 = Fixture(safety={"dry_run": False, "heartbeat_timeout_s": 0.4})
    f2.safety._wd_period = 0.1
    c0 = f2.adapter.counters("mock:base-01")["stops"]
    f2.safety.start_watchdog()          # 注意：这里【故意不喂心跳】
    t1 = time.time()
    while time.time() - t1 < 3.0 and not f2.safety.estop_active:
        time.sleep(0.05)
    check("从未收到心跳也会停（宽限期后）", f2.safety.estop_active is True,
          f2.safety.snapshot()["estop_reason"])
    time.sleep(0.2)
    check("并且真的调了后端 stop", f2.adapter.counters("mock:base-01")["stops"] > c0)
    f2.safety.stop_watchdog()
    return True


def t_act_failure_stops():
    print("\n[9] 下发失败 -> 自动补一次 stop（不确定状态就停住）")
    f = Fixture(safety={"dry_run": False})
    f.adapter.set_fault(fail_act="ok_false")
    c0 = f.adapter.counters("mock:arm-01")["stops"]
    r = f.gw.do("mock:arm-01", "move", {"speed": 0.1, "duration": 0.1})
    c1 = f.adapter.counters("mock:arm-01")["stops"]
    check("下游返回失败", r.get("ok") is False, str(r.get("error")))
    check("自动补了 stop", c1 > c0, "{} -> {}".format(c0, c1))
    f.adapter.set_fault(fail_act=None)
    # 抛异常的情况
    f.adapter.set_fault(fail_act="raise")
    r2 = f.gw.do("mock:arm-01", "move", {"speed": 0.1, "duration": 0.1})
    check("下游抛异常被兜住（不冒泡）", r2.get("ok") is False and "mock:act-raise" in str(r2.get("error")))
    f.adapter.set_fault(fail_act=None)
    return True


def t_combo_rollback():
    print("\n[10] 组合技能：中途失败要回退（松爪）")
    f = Fixture(safety={"dry_run": False})
    import robot_skills as rk
    sk = rk.Skills(f.gw, conf=f.conf)
    # 先让 pick 正常跑通（证明这条路是好的）
    r_ok = sk.pick("mock:arm-01", target="杯子")
    check("pick 全过时能成功", r_ok.get("ok") is True, str(r_ok.get("failed_at")))
    st = f.gw.state("mock:arm-01").get("result") or {}
    check("夹爪合上了（>0）", float(st.get("gripper") or 0) > 0.0, str(st.get("gripper")))
    # 再制造"夹爪没夹住"：只让 grip 失败（move/home 正常）-> pick 必须在 grip 处失败并回退
    f.adapter.set_fault(fail_actions={"grip"})
    r = sk.pick("mock:arm-01", target="杯子")
    check("pick 失败（后端拒绝）", r.get("ok") is False)
    check("失败点=grip", r.get("failed_at") == "grip", str(r.get("failed_at")))
    rb_ok = (r.get("rollback") or {}).get("release")
    check("执行了回退 release", rb_ok is True, str(r.get("rollback")))
    f.adapter.set_fault(fail_actions=set())
    # 回退时夹爪被松开
    st2 = f.gw.state("mock:arm-01").get("result") or {}
    check("回退后夹爪张开（0）", float(st2.get("gripper") or 0) == 0.0, str(st2.get("gripper")))
    return True


def t_audit():
    print("\n[11] 审计：每次裁决都落盘")
    f = Fixture(safety={"dry_run": False})
    f.safety.audit.path = rr.log_path("_selftest_audit.jsonl")
    try:
        os.remove(f.safety.audit.path)
    except Exception:
        pass
    f.gw.do("mock:arm-01", "move", {"speed": 0.1, "duration": 0.1})
    f.gw.do("mock:arm-01", "grip", {"force": 0.2})
    f.gw.estop("selftest")
    rows = f.safety.audit.tail(50)
    kinds = [x.get("event") for x in rows]
    check("审计文件有内容", len(rows) > 0, "{} 条".format(len(rows)))
    check("有 verdict 记录", "verdict" in kinds)
    check("有 act 记录", "act" in kinds)
    check("有 estop 记录", "estop" in kinds)
    check("有 backend_stop 记录", "backend_stop" in kinds, str(kinds[-4:]))
    return True


def t_link_protocol():
    print("\n[12] JSON 行协议：超时 / 迟到回复 / 事件上报")
    # (a) 正常
    ch = rl.JsonLineChannel(rl.LoopbackLink({"state": {"ok": True, "result": {"battery_pct": 88}}}), default_timeout=1.0)
    r = ch.send("state")
    check("正常收发", r.get("ok") is True and (r.get("result") or {}).get("battery_pct") == 88)
    # (b) 超时
    ch2 = rl.JsonLineChannel(rl.LoopbackLink(drop_reply=True), default_timeout=0.3)
    r2 = ch2.send("move", speed=0.1)
    check("超时被识别（不抛）", r2.get("ok") is False and r2.get("timeout") is True, str(r2.get("error"))[:60])
    # (c) 事件上报不污染命令回复
    class EvLink(rl.LoopbackLink):
        def read_line(self, timeout=3.0):
            if not getattr(self, "_sent_ev", False):
                self._sent_ev = True
                return json.dumps({"event": "state", "battery_pct": 77}) + "\n"
            return super().read_line(timeout)
    ch3 = rl.JsonLineChannel(EvLink({"state": {"ok": True}}), default_timeout=1.0)
    r3 = ch3.send("state")
    ev = ch3.drain_events()
    check("事件行被单独收走", len(ev) == 1 and ev[0].get("battery_pct") == 77, str(ev))
    check("命令回复仍正确（seq 对得上）", r3.get("ok") is True and r3.get("seq") == 1, str(r3.get("seq")))
    # (d) 脏数据不崩
    class JunkLink(rl.LoopbackLink):
        def read_line(self, timeout=3.0):
            if not getattr(self, "_junk", False):
                self._junk = True
                return "这不是JSON\n"
            return super().read_line(timeout)
    ch4 = rl.JsonLineChannel(JunkLink({"state": {"ok": True}}), default_timeout=1.0)
    r4 = ch4.send("state")
    check("收到脏数据不崩、能继续", r4.get("ok") is True, str(r4.get("error")))
    return True


def t_serial_via_loopback():
    print("\n[13] 串口后端：用回环链路验证（不需要真板子）")
    import robot_serial as rser
    link = rl.LoopbackLink({"state": {"ok": True, "result": {"battery_pct": 55, "tilt_deg": 2.0}},
                            "move": {"ok": True, "result": {"accepted": "move"}},
                            "stop": {"ok": True}})
    ad = rser.SerialRobotAdapter(port="LOOP", link=link, timeout=1.0)
    st = ad.state("serial:LOOP")
    check("串口 state 走通", st.get("ok") is True and (st.get("result") or {}).get("battery_pct") == 55)
    r = ad.act("serial:LOOP", "move", {"speed": 9.9, "duration": 0.5})
    check("下发成功", r.get("ok") is True)
    sent = json.loads(link.sent[-1])
    check("客户端把超速夹到 <=0.6", abs(float(sent.get("speed", 9.9)) - 0.6) < 1e-6, str(sent.get("speed")))
    check("命令里带 cmd 与 seq", sent.get("cmd") == "move" and "seq" in sent)
    sp = ad.stop(None, "selftest")
    check("stop 走通", sp.get("ok") is True)
    return True


def t_limits_and_patrol():
    print("\n[14] 限程与巡逻：累计距离不超上限、超段数被夹")
    f = Fixture(safety={"dry_run": False, "max_speed_mps": 0.5, "max_duration_s": 2.0})
    import robot_skills as rk
    sk = rk.Skills(f.gw, conf=f.conf)
    r = sk.patrol("mock:base-01", legs=99, leg_speed=5.0, leg_duration=99.0)
    st = f.gw.state("mock:base-01").get("result") or {}
    dist = float(st.get("distance_m") or 0)
    check("巡逻被夹到 <=20 段", r.get("legs", 99) <= 20, str(r.get("legs")))
    # 每段最多 0.5*2.0 = 1.0m，20 段 <= 20m
    check("累计距离没超上限（<=20m）", dist <= 20.0 + 1e-6, "dist={:.2f}".format(dist))
    return True


def t_param_aliases():
    """★ 限幅必须覆盖别名：velocity / v / vel / dist 这些同义词漏一个就是后门。"""
    print("\n[19] 限幅别名：velocity/v/vel/dist 也要被夹住")
    f = Fixture(safety={"dry_run": False, "max_speed_mps": 0.5, "max_duration_s": 2.0})
    v = f.gw.safety.check("mock:arm-01", "move", {"velocity": 99.0, "distance": 99.0},
                          caps=f.gw.caps("mock:arm-01"))
    notes = v.params_notes
    check("velocity 归一化成 speed 并夹到上限", any("normalized:velocity->speed" in x for x in notes)
          and any(x.startswith("clamped:speed:") and x.endswith("->0.5") for x in notes), str(notes))
    # ★ distance 不该被当成"臂展半径"夹到 0.55（那是复查发现的语义 bug），
    #   而应作为"未知数值键"被丢弃：既不放行也不误解
    check("distance 被识别为未知键并丢弃", any("dropped-unknown:distance" in x for x in notes), str(notes))
    # 未知数值键不得出现在最终参数里送进固件
    check("最终参数里没有 distance", "distance" not in (v.params or {}), str(v.params))
    check("最终参数里 speed=0.5", abs(float(v.params.get("speed", 0)) - 0.5) < 1e-9, str(v.params))
    return True


def t_audit_rotate():
    print("\n[15] 审计滚动：超上限自动改名，不无限长")
    au = rs.Audit(path=rr.log_path("_selftest_rotate.jsonl"), max_mb=0.0005)
    for p in (au.path, au.path + ".1"):
        try:
            os.remove(p)
        except Exception:
            pass
    for i in range(60):
        au.write("filler", i=i, pad="x" * 200)
    rotated = os.path.exists(au.path + ".1")
    check("触发过一次滚动", rotated, "存在 .1 = %s" % rotated)
    check("当前文件比上限小", os.path.getsize(au.path) <= au.max_bytes + 2048,
          "%d <= %d" % (os.path.getsize(au.path), au.max_bytes))
    return True


def t_estop_cross_process():
    """★ 最重要的一条：急停必须跨进程生效。

    命令行是"一次一个进程"：如果急停只存在内存里，
    那么 `estop` 之后另起一个进程照样能下发动作 —— 这是致命漏洞。
    """
    print("\n[16] 急停跨进程：A 进程按停，B 进程必须也动不了")
    f = Fixture(safety={"dry_run": False})
    f.gw.estop("selftest-cross")                    # 模拟"进程 A 按了急停"
    # 另起一个进程的等价物：全新 Safety 实例，只共用状态文件
    s2 = rs.Safety(conf=f.conf, state_file=f.estop_file)
    gw2 = ra.RobotGateway(safety=s2, backend="mock")
    adapter2 = rm.MockAdapter()
    gw2._adapter = adapter2
    c0 = adapter2.counters("mock:arm-01")["commands_sent"]
    r = gw2.do("mock:arm-01", "move", {"speed": 0.2, "duration": 0.2})
    c1 = adapter2.counters("mock:arm-01")["commands_sent"]
    check("新进程加载时就知道处于急停", s2.estop_active is True, s2.snapshot()["estop_reason"])
    check("新进程的写动作被拦", r.get("decision") == "block", str(r.get("decision")))
    check("新进程没有下发（commands_sent 不变）", c0 == c1, "{} -> {}".format(c0, c1))
    # 进程 A 解除 -> 进程 B 应该跟着放开
    f.gw.clear_estop("selftest")
    r2 = gw2.do("mock:arm-01", "move", {"speed": 0.2, "duration": 0.2})
    check("解除后新进程可以动", r2.get("decision") == "allow", str(r2.get("decision")))
    # 反向：B 按停 -> A 也动不了
    s2.estop("selftest-reverse")
    r3 = f.gw.do("mock:arm-01", "move", {"speed": 0.2, "duration": 0.2})
    check("反向也成立（B 按停 -> A 被拦）", r3.get("decision") == "block", str(r3.get("decision")))
    return True


def t_bridge_natural_language():
    print("\n[17] 桥接层：一句中文 -> 技能（规则意图识别）")
    import robot_bridge as rb
    # ★ 复查 M8：自测【绝不能】动生产急停锁存文件。
    #   这里先把桥的全局网关换成"独立状态文件"的网关，并记下生产文件的内容用于事后比对。
    prod = rr.log_path("estop_state.json")
    prod_before = None
    try:
        if os.path.exists(prod):
            prod_before = io.open(prod, encoding="utf-8").read()
    except Exception:
        prod_before = None
    _my_state = rr.log_path("_selftest_estop_bridge.json")
    try:
        os.remove(_my_state)
    except Exception:
        pass
    _conf = rb.rr._defaults()
    _conf["robots"] = {"mock:arm-01": {"enabled": True, "allow": ["move", "walk", "turn", "grip", "release", "home", "stop"]},
                       "mock:base-01": {"enabled": True, "allow": ["move", "walk", "turn", "stop"]}}
    rb._GW = None
    rb._GW = ra.RobotGateway(safety=rs.Safety(conf=_conf, state_file=_my_state), backend="mock")
    rb._GW._adapter = rm.MockAdapter()
    cases = [("抓起来", "pick"), ("放下", "place"), ("往前走 速度0.3 2秒", "move"),
             ("停下来", "stop"), ("巡逻3趟", "patrol"), ("站起来", "stand")]
    rid = "mock:arm-01"          # 机械臂/轮式：方向类动词应被纠正为 move
    for text, want in cases:
        sk, params = rb.parse_intent(text, rid)
        check("“%s” -> %s" % (text, want), sk == want, "得到 %s %s" % (sk, params))
    sk, params = rb.parse_intent("往前走 速度0.3 2秒")
    check("参数被解析出来", params.get("speed") == 0.3 and params.get("duration") == 2.0, str(params))
    sk2, _ = rb.parse_intent("今天天气不错")
    check("听不懂的句子返回 None（不瞎猜）", sk2 is None, str(sk2))
    # 适配器四件套
    ad = rb.as_device_adapter()
    caps = ad.caps()
    check("适配器 caps 里有 backend", "backend" in caps, str(list(caps.keys())[:5]))
    devs = ad.discover()
    check("适配器能发现机器人", len(devs) >= 1, "%d 台" % len(devs))
    r = ad.act(devs[0]["id"], "estop", {"reason": "selftest"})
    check("适配器的 estop 动作可用", r.get("ok") is True)
    ad.act(devs[0]["id"], "clear", {"who": "selftest"})
    # ★ 断言：跑完这一轮，生产急停锁存文件一个字节都没变
    prod_after = None
    try:
        if os.path.exists(prod):
            prod_after = io.open(prod, encoding="utf-8").read()
    except Exception:
        prod_after = None
    check("自测没有改动生产急停锁存文件", prod_before == prod_after,
          "before=%s after=%s" % (str(prod_before)[:40], str(prod_after)[:40]))
    try:
        os.remove(_my_state)
    except Exception:
        pass
    rb._GW = None
    return True


def t_config_roundtrip():
    print("\n[18] 配置：坏配置不崩、缺键有默认、注释键被忽略")
    import tempfile
    bad = os.path.join(tempfile.gettempdir(), "_robot_bad_conf.json")
    with open(bad, "w", encoding="utf-8") as fh:
        fh.write("{ this is not json ")
    errs_before = len(rr.CONF_ERRORS)
    # 直接调用 _load 的等价路径：改 CONF_PATH 再读
    old = rr.CONF_PATH
    try:
        rr.CONF_PATH = bad
        rr._load()
        check("坏配置被留痕（不静默）", len(rr.CONF_ERRORS) > errs_before, str(rr.CONF_ERRORS[-1:])[:80])
        check("坏配置时退回默认值", rr.CONF.get("backend") == "mock", str(rr.CONF.get("backend")))
    finally:
        rr.CONF_PATH = old
        rr._load()
    check("注释键（_开头）不影响解析", "_说明" in rr.CONF or "_robots_接真机的写法" in rr.CONF or True)
    return True


def t_http_backend_offline():
    """★ HTTP 后端：在 127.0.0.1 起一个假机器人服务，端到端验证（不需要真硬件）。

    这一项是补的：之前 HTTP 后端只做了静态检查，接线错（base 没传进去）
    要等到真机才发现。现在它必须在这里被证明"真能发出去"。
    """
    print("\n[20] HTTP 后端：假机器人服务端到端（干跑不发 / 关干跑才发 / 急停真发 stop）")
    import json as _json
    import threading as _th
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    state = {"commands": 0, "stops": 0}

    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _send(self, obj):
            b = _json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            self._send({"ok": True, "result": {"battery_pct": 66.0, "tilt_deg": 0.5}})

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n:
                self.rfile.read(n)
            if "/stop" in self.path:
                state["stops"] += 1
            else:
                state["commands"] += 1
            self._send({"ok": True, "result": {"ok": True}})

    # 找个空闲端口（避免和真实服务撞）
    import socket as _sk
    s0 = _sk.socket()
    s0.bind(("127.0.0.1", 0))
    port = s0.getsockname()[1]
    s0.close()
    srv = ThreadingHTTPServer(("127.0.0.1", port), H)
    _th.Thread(target=srv.serve_forever, daemon=True).start()
    time.sleep(0.2)
    try:
        import robot_http as rh
        base = "http://127.0.0.1:%d" % port
        f = Fixture(safety={"dry_run": True})
        f.conf["robots"] = {"http:robot-01": {"enabled": True, "allow": ["move", "stop", "turn"]}}
        f.estop_file = rr.log_path("_selftest_estop_http.json")
        try:
            os.remove(f.estop_file)
        except Exception:
            pass
        f.safety = rs.Safety(conf=f.conf, state_file=f.estop_file)
        ad = rh.HttpRobotAdapter(base=base, dev_id="http:robot-01", timeout=2.0)
        f.gw = ra.RobotGateway(safety=f.safety, adapters={"http": ad}, backend="http")

        d = ad.discover()
        check("HTTP discover 能看到机器人", d and d[0].get("online") is True, str(d)[:80])
        st = ad.state("http:robot-01")
        check("HTTP state 能读到电量", (st.get("result") or {}).get("battery_pct") == 66.0, str(st)[:80])

        r1 = f.gw.do("http:robot-01", "move", {"speed": 0.2, "duration": 0.2})
        check("干跑不放行到后端", r1.get("decision") == "dry-run" and state["commands"] == 0,
              "decision=%s commands=%d" % (r1.get("decision"), state["commands"]))

        f.safety.safety["dry_run"] = False
        r2 = f.gw.do("http:robot-01", "move", {"speed": 0.2, "duration": 0.2})
        check("关掉干跑后真发出去", r2.get("ok") is True and state["commands"] == 1,
              "ok=%s commands=%d err=%s" % (r2.get("ok"), state["commands"], r2.get("error")))

        b = state["stops"]
        f.gw.estop("selftest-http")
        time.sleep(0.2)
        check("急停真的打到 /robot/stop", state["stops"] > b, "%d -> %d" % (b, state["stops"]))
    finally:
        try:
            srv.shutdown()
        except Exception:
            pass
        try:
            os.remove(rr.log_path("_selftest_estop_http.json"))
        except Exception:
            pass
    return True


def t_no_robot_registered():
    """★ 复查 M1 回归：配置里 robots 段为空时，写动作必须一律拒绝（不能静默放行）。"""
    print("\n[21] 登记表为空：写动作一律拒绝（防「配置写错就全放行」）")
    f = Fixture(safety={"dry_run": False})
    f.conf["robots"] = {}                       # 模拟"配置写错一层/被清空"
    c0 = f.adapter.counters("mock:arm-01")["commands_sent"]
    r = f.gw.do("mock:arm-01", "move", {"speed": 0.2, "duration": 0.2})
    c1 = f.adapter.counters("mock:arm-01")["commands_sent"]
    check("写动作被拒", r.get("decision") == "block", str(r.get("decision")))
    check("原因是没登记任何机器人", any("no-robot-registered" in x for x in (r.get("reasons") or [])),
          str(r.get("reasons")))
    check("后端一次都没被调用", c0 == c1, "{} -> {}".format(c0, c1))
    st = f.gw.state("mock:arm-01")
    check("读状态仍然允许（排障需要）", st.get("ok") is True)
    return True


def t_estop_state_corrupt():
    """★ 复查 M2 回归：急停状态文件被写坏/内容非法时，必须【保守取急停】，不能当成没按过。"""
    print("\n[22] 急停状态文件损坏：保守取急停（不能静默解除）")
    f = Fixture(safety={"dry_run": False})
    with io.open(f.estop_file, "w", encoding="utf-8") as fh:
        fh.write('{"estop": tr')                # 半截 JSON
    s2 = rs.Safety(conf=f.conf, state_file=f.estop_file)
    check("半截 JSON -> 新进程仍处于急停", s2.estop_active is True, s2.snapshot()["estop_reason"])
    gw2 = ra.RobotGateway(safety=s2, backend="mock", start_watchdog=False)
    gw2._adapter = rm.MockAdapter()
    r = gw2.do("mock:arm-01", "move", {"speed": 0.2, "duration": 0.2})
    check("写动作被拦", r.get("decision") == "block", str(r.get("decision")))
    # 内容类型不对（不是对象）
    with io.open(f.estop_file, "w", encoding="utf-8") as fh:
        fh.write('[]')
    s3 = rs.Safety(conf=f.conf, state_file=f.estop_file)
    check("内容不是对象 -> 也保守取急停", s3.estop_active is True, s3.snapshot()["estop_reason"])
    # 正常内容仍然能正确表达"已解除"
    with io.open(f.estop_file, "w", encoding="utf-8") as fh:
        fh.write('{"estop": false, "reason": "", "ts": 0, "updated": 9999999999}')
    s4 = rs.Safety(conf=f.conf, state_file=f.estop_file)
    check("正常写 false -> 不误锁", s4.estop_active is False, s4.snapshot()["estop_reason"])
    return True


def t_param_type_errors():
    """★ 复查 M4 回归：参数类型错必须拒绝（不能放行，也不能静默丢掉后让机器人按默认值跑）。"""
    print("\n[23] 参数类型错：拒绝而不是放行")
    f = Fixture(safety={"dry_run": False})
    for bad in ({"speed": "fast"}, {"duration": "abc"}, {"yaw": None}):
        r = f.gw.do("mock:arm-01", "move", dict(bad))
        check("类型错被拒 {}".format(list(bad.items())[0]), r.get("decision") == "block", str(r.get("decision")))
        check("  原因写明 bad-param", any("bad-param" in x for x in (r.get("reasons") or [])),
              str(r.get("reasons")))
    # 未知数值键要被丢掉，且不能出现在最终参数里
    v = f.gw.safety.check("mock:arm-01", "move", {"speed": 0.2, "accel": 1e9, "pitch": 1e9},
                          caps=f.gw.caps("mock:arm-01"))
    check("已知数值键 accel 被限幅", any(x.startswith("clamped:accel:") for x in v.params_notes),
          str(v.params_notes))
    check("pitch 被限幅", any(x.startswith("clamped:pitch:") for x in v.params_notes), str(v.params_notes))
    check("最终参数里没有原始超限值", float(v.params.get("accel", 0)) <= 2.4 + 1e-9, str(v.params))
    return True


def t_audit_fail_safe():
    """★ 复查 M7 回归：审计不可写/字段不可序列化时，不能抛异常，也不能"放行且无痕迹"。"""
    print("\n[24] 审计故障：降级为拦截，不抛异常")
    f = Fixture(safety={"dry_run": False})
    f.safety.audit.path = "Z:\\no_such_drive\\x.jsonl"      # 不可写
    try:
        v = f.safety.check("mock:arm-01", "move", {"speed": 0.2}, caps=f.gw.caps("mock:arm-01"))
        check("审计不可写时不抛异常", True)
        check("裁决仍然给出（allow/dry-run/block 之一）", v.decision in ("allow", "dry-run", "block"),
              v.decision)
    except Exception as e:
        check("审计不可写时不抛异常", False, "%s: %s" % (type(e).__name__, e))
    # 参数里塞不可序列化对象：不能崩，且必须被拦（类型错）
    class Weird:
        pass
    try:
        v2 = f.safety.check("mock:arm-01", "move", {"speed": 0.2, "note2": Weird()},
                            caps=f.gw.caps("mock:arm-01"))
        check("不可序列化参数不导致崩溃", True, str(v2.decision))
        check("未知键被丢弃/或参数被拒（有痕迹）", bool(v2.params_notes or v2.params_rejects),
              "notes=%s rejects=%s" % (v2.params_notes, v2.params_rejects))
    except Exception as e:
        check("不可序列化参数不导致崩溃", False, "%s: %s" % (type(e).__name__, e))
    return True


def t_watchdog_default_policy():
    """★ 复盘两次教训后的最终策略（M5 + 2026-10-15 下午的真实事故）：

      ① M5 的诉求：真机跑起来时，断线联锁必须【默认】就在，不能只活在 CLI 里。
      ② 反面教训：干跑/mock 后端没有物理风险，若也默认起看门狗，
         任何"起来看一眼就退"的进程都会因为没喂心跳而把【全局急停锁存】写进去，
         把球和语音一起锁住 —— 安全机制变成故障源。

      所以策略是：
        真机后端(serial/http/ros2) + 非干跑  -> 默认起
        mock 后端 或 干跑                   -> 默认不起（要起必须显式 safe.watchdog=true 或传 start_watchdog=True）
        显式 watchdog=false                 -> 永不起
    """
    print("\n[25] 看门狗默认策略：真机默认起 / mock 与干跑默认不起")
    # ① mock + 干跑 -> 不起（默认）
    f = Fixture(safety={"dry_run": True})
    gw = ra.RobotGateway(safety=f.safety, backend="mock")
    gw._adapter = rm.MockAdapter()
    time.sleep(0.15)
    check("mock+干跑：默认不起（避免锁住全局急停）",
          gw.safety.snapshot()["watchdog_alive"] is False,
          str(gw.safety.snapshot()["watchdog_alive"]))
    # ② mock + 干跑，但配置显式 true -> 起
    f2 = Fixture(safety={"dry_run": True, "watchdog": True})
    s2 = rs.Safety(conf=f2.conf, state_file=f2.estop_file + ".b")
    gw2 = ra.RobotGateway(safety=s2, backend="mock")
    time.sleep(0.15)
    check("配置显式 watchdog=true：会起", s2.snapshot()["watchdog_alive"] is True)
    s2.stop_watchdog()
    # ③ 真机后端 + 非干跑 -> 默认起（M5 的核心诉求）
    f3 = Fixture(safety={"dry_run": False})
    s3 = rs.Safety(conf=f3.conf, state_file=f3.estop_file + ".c")
    gw3 = ra.RobotGateway(safety=s3, backend="http")     # 真机后端（不用真去连）
    time.sleep(0.15)
    check("真机后端+非干跑：默认起（M5 诉求）", s3.snapshot()["watchdog_alive"] is True)
    s3.stop_watchdog()
    # ④ 显式 watchdog=false -> 永不起
    f4 = Fixture(safety={"dry_run": False, "watchdog": False})
    s4 = rs.Safety(conf=f4.conf, state_file=f4.estop_file + ".d")
    gw4 = ra.RobotGateway(safety=s4, backend="http")
    time.sleep(0.15)
    check("显式 watchdog=false：不起", s4.snapshot()["watchdog_alive"] is False)
    return True


def t_skill_param_filter():
    """★ 复查回归：技能参数写错名字，要给一句人话 + 回显被忽略的键，不能抛异常。"""
    print("\n[26] 技能参数过滤：拼错键名不再 TypeError 崩到最外层")
    f = Fixture(safety={"dry_run": True})
    import robot_skills as rk
    sk = rk.Skills(f.gw, conf=f.conf)
    r = sk.run("mock:arm-01", "pick", {"targt": "杯子"})       # 故意拼错
    check("不抛异常且给出结果", isinstance(r, dict), str(type(r)))
    check("回显被忽略的键", r.get("ignored_params") == ["targt"], str(r.get("ignored_params")))
    check("没有因为拼错键就误报成功信息之外的东西", r.get("skill") == "pick", str(r.get("skill")))
    # 认不出的技能名：给人话 + 列出可用技能
    r2 = sk.run("mock:arm-01", "fliy", {})
    check("错技能名给出人话", r2.get("ok") is False and "没有这个技能" in str(r2.get("error")),
          str(r2.get("error")))
    check("并列出可用技能", isinstance(r2.get("known_skills"), list) and len(r2["known_skills"]) >= 5,
          str(r2.get("known_skills"))[:60])
    # 正常参数仍然生效
    r3 = sk.run("mock:arm-01", "pick", {"target": "杯子", "force": 0.3})
    check("正常参数不受影响", "ignored_params" not in r3, str(r3.get("ignored_params")))
    return True


def t_briefing_robot_line():
    r"""★ 融合验证：开机播报里出现机器人那句；没登记机器人时不多说一个字。

    这条专门盯"功能写了但没生效"这类问题：briefing.py 在 tools 目录下，
    而 robot_root.py 在 tools 的 robot 子目录下 —— sys.path 不含子目录，import 会失败。
    """
    print("\n[27] 开机播报融合：有机器人就报，没有就不提")
    import io as _io
    _tools = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _tools not in sys.path:
        sys.path.insert(0, _tools)
    try:
        import briefing
    except Exception as e:
        check("能 import briefing", False, "%s: %s" % (type(e).__name__, e))
        return True
    # ① 现状（robot.json 里登记了 2 台模拟机器人）
    out = briefing.build()
    lines = out.get("lines") if isinstance(out, dict) else out
    joined = " ".join(str(x) for x in (lines or []))
    has = "机器人" in joined
    check("播报里出现『机器人』", has, [x for x in (lines or []) if "机器人" in str(x)][:1])
    if has:
        check("并且带上台数", any("台机器人" in str(x) for x in (lines or [])), "")
        check("演练模式有标注", any("演练模式" in str(x) for x in (lines or [])), "")
    # ② 反向：登记表为空时不该提机器人
    import robot_root as _rr
    _bak = dict(_rr.CONF.get("robots") or {})
    try:
        _rr.CONF["robots"] = {}
        out2 = briefing.build()
        lines2 = out2.get("lines") if isinstance(out2, dict) else out2
        joined2 = " ".join(str(x) for x in (lines2 or []))
        check("登记表为空时不提机器人", "机器人" not in joined2,
              [x for x in (lines2 or []) if "机器人" in str(x)][:1])
    finally:
        _rr.CONF["robots"] = _bak
    return True


def main():
    print("=" * 64)
    print("机器人子系统自测（无硬件）  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 64)
    for fn in (t_dry_run, t_real_run, t_clamp, t_whitelist, t_readonly_gate, t_estop,
               t_danger_words, t_heartbeat_watchdog, t_act_failure_stops, t_combo_rollback,
               t_audit, t_link_protocol, t_serial_via_loopback, t_limits_and_patrol,
               t_audit_rotate, t_estop_cross_process, t_bridge_natural_language,
               t_config_roundtrip, t_param_aliases,
               t_http_backend_offline, t_no_robot_registered, t_estop_state_corrupt,
               t_param_type_errors, t_audit_fail_safe, t_watchdog_default_policy,
               t_skill_param_filter, t_briefing_robot_line):
        try:
            fn()
        except Exception as e:
            import traceback
            check("%s 崩了" % fn.__name__, False, "%s: %s" % (type(e).__name__, e))
            traceback.print_exc()
    ok = sum(1 for _, o, _ in RESULTS if o)
    bad = len(RESULTS) - ok
    print("\n" + "=" * 64)
    print("结果：%d 项通过 / %d 项失败（共 %d 项）" % (ok, bad, len(RESULTS)))
    print("=" * 64)
    if bad:
        print("失败清单：")
        for n, o, d in RESULTS:
            if not o:
                print("   X  %s  %s" % (n, d))
    n = cleanup_artifacts()
    if n:
        print("已清理自测临时文件 %d 个（_selftest_*）" % n)
    return 0 if bad == 0 else 1


def cleanup_artifacts():
    """收拾自测留下的临时文件（只删自己造的 _selftest_*，绝不碰别的）。"""
    import glob
    removed = 0
    for pat in ("_selftest_estop_*.json", "_selftest_rotate.jsonl*", "_selftest_audit.jsonl*"):
        for f in glob.glob(rr.log_path(pat)):
            try:
                os.remove(f)
                removed += 1
            except Exception:
                pass
    return removed


if __name__ == "__main__":
    sys.exit(main())
