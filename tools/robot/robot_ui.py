# -*- coding: utf-8 -*-
"""robot_ui.py -- 机器人子系统的唯一命令行入口（人看的 + 机器看的）

用法：
    py -3.12 robot_ui.py status               # 安全层快照 + 机器人在不在
    py -3.12 robot_ui.py robots               # 列出机器人（含后端类型）
    py -3.12 robot_ui.py state <id>           # 读一台机器人的状态
    py -3.12 robot_ui.py skills               # 技能表
    py -3.12 robot_ui.py do <id> <技能> [k=v ...]     # 下发一个动作（默认干跑）
    py -3.12 robot_ui.py estop [原因]         # 触发急停（锁存）
    py -3.12 robot_ui.py clear [人]           # 解除急停（必须显式）
    py -3.12 robot_ui.py watchdog             # 起看门狗（断线联锁）
    py -3.12 robot_ui.py selftest             # 跑全部自测
    py -3.12 robot_ui.py doctor               # 体检：配置/后端/串口/ROS2/权限
    py -3.12 robot_ui.py events [n]           # 看审计日志尾部

【设计约定】
  · 默认 **dry_run=True**：动作只走安全闸、不下发到硬件。要真下发：
      配置 robot.json 的 safety.dry_run=false，或环境变量 FAIRY_ROBOT_GO=1
  · 任何命令都不返回 traceback；致命错误给一行人话 + 非零退出码
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import robot_root as rr          # noqa: E402
import robot_safety as rs        # noqa: E402
import robot_adapters as ra      # noqa: E402


def _bar(t):
    print("=" * 64)
    print(t)
    print("=" * 64)


def build_gateway():
    """按配置建网关；顺手把 dry_run 的临时覆盖处理好。"""
    if os.environ.get("FAIRY_ROBOT_GO") == "1":
        rr.CONF.setdefault("safety", {})["dry_run"] = False
    # 导入后端（让 ADAPTERS 注册表里真的有东西）
    for mod in ("robot_mock", "robot_serial", "robot_http", "robot_ros2"):
        try:
            __import__(mod)
        except Exception as e:
            rr.CONF_ERRORS.append("import-%s: %s: %s" % (mod, type(e).__name__, e))
    gw = ra.RobotGateway()
    awk = os.environ.get("FAIRY_ROBOT_WATCHDOG")
    if awk == "1":
        gw.safety.start_watchdog()
    return gw


def cmd_status(gw):
    snap = gw.snapshot()
    s = snap["safety"]
    print("后端      : %s" % snap["backend"])
    print("干跑      : %s   （true = 只过闸不下发；要真下发设 FAIRY_ROBOT_GO=1）" % s["dry_run"])
    print("急停      : %s %s" % ("★已触发★" if s["estop"] else "未触发",
                                ("原因=" + s["estop_reason"]) if s["estop"] else ""))
    print("看门狗    : %s（心跳超时 %.1fs，当前心跳龄 %.2fs）" % (
        "在跑" if s["watchdog_alive"] else "没跑", s["limits"]["heartbeat_timeout_s"], s["heartbeat_age_s"]))
    if s.get("hardware_estop_warning"):
        print("硬件急停  : ★ %s" % s["hardware_estop_warning"])
    print("限值      : 速度<=%.2f m/s 角速度<=%.2f rad/s 力矩<=%.2f 半径<=%.2f m 时长<=%.0fs 电量>=%.0f%%" % (
        s["limits"]["max_speed_mps"], s["limits"]["max_yaw_rps"], s["limits"]["max_torque"],
        s["limits"]["max_reach_m"], s["limits"]["max_duration_s"], s["limits"]["min_battery_pct"]))
    print("裁决统计  : 检查 %d / 放行 %d / 干跑 %d / 拦截 %d / 急停 %d" % (
        s["stats"]["checks"], s["stats"]["allowed"], s["stats"]["dryrun"],
        s["stats"]["blocked"], s["stats"]["estops"]))
    print("机器人    : %d 台" % len(snap["robots"]))
    for r in snap["robots"]:
        print("   - %-18s %-10s online=%s" % (r.get("id"), r.get("kind"), r.get("online")))
    if rr.CONF_ERRORS:
        print("配置/环境提示:")
        for e in rr.CONF_ERRORS[-5:]:
            print("   ! %s" % e)
    print("配置      : %s" % rr.CONF_PATH)
    return 0


def cmd_robots(gw):
    rs_ = gw.robots()
    if not rs_:
        print("没发现机器人（后端=%s）。串口没插/ROS2 没装/HTTP 不通都会这样。" % gw.backend_key)
        return 1
    for r in rs_:
        print("%-20s %-10s online=%-5s %s" % (r.get("id"), r.get("kind"), r.get("online"),
                                              r.get("name") or ""))
        if r.get("error"):
            print("      ! %s" % r["error"])
    return 0


def cmd_state(gw, dev_id):
    st = gw.state(dev_id)
    print(json.dumps(st, ensure_ascii=False, indent=1))
    return 0 if st.get("ok") else 1


def cmd_skills(gw):
    import robot_skills as rk
    sk = rk.Skills(gw)
    for s in sk.catalog():
        mark = "开" if s["enabled"] else "关(配置)"
        print("%-8s %-6s %-8s %s  %s" % (s["name"], s["kind"], mark,
                                         ",".join(s["params"]) or "-", s["desc"]))
    return 0


def _parse_kv(argv):
    out = {}
    for a in argv:
        if "=" not in a:
            continue
        k, v = a.split("=", 1)
        try:
            out[k] = float(v)
        except Exception:
            if v.lower() in ("true", "false"):
                out[k] = (v.lower() == "true")
            else:
                out[k] = v
    return out


def cmd_do(gw, dev_id, skill, argv):
    import robot_skills as rk
    sk = rk.Skills(gw)
    params = _parse_kv(argv)
    t0 = time.time()
    r = sk.run(dev_id, skill, params)
    r["wall_ms"] = int((time.time() - t0) * 1000)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    if r.get("ok"):
        # 参数被忽略要提示（免得"我传了参数怎么没生效"查半天），并且退出码也算"请求有问题"
        if r.get("ignored_params"):
            print("注意：这些参数被忽略了 -> %s" % "、".join(r["ignored_params"]))
            return 3
        return 0
    # 被安全层拦 / 参数写错 / 技能不存在 / 组合技能失败 -> 一律 3
    # （3 = "被拒绝了"，不是崩溃；脚本可以据此区分）
    return 3


def cmd_estop(gw, reason):
    gw.estop(reason or "manual")
    print("急停已触发并锁存：%s" % (reason or "manual"))
    print("解除：py -3.12 robot_ui.py clear <你的名字>")
    return 0


def cmd_clear(gw, who):
    was = gw.clear_estop(who or "human")
    print("已解除（之前是锁存状态：%s）。请先确认现场安全再下发动作。" % was)
    return 0


def cmd_watchdog(gw):
    ok = gw.safety.start_watchdog()
    print("看门狗状态：%s" % ("已在运行" if gw.safety.snapshot()["watchdog_alive"] else "未运行"))
    print("规则：心跳超过 %.1fs 未刷新（或从没有心跳）-> 自动急停"
          % gw.safety.safety.get("heartbeat_timeout_s"))
    print("")
    print("★ 注意：本命令只对【当前这个进程】有效 —— 进程退出，线程也就没了。")
    print("  真机常驻时应该：让常驻程序调 RobotGateway()（网关默认自己把看门狗拉起来），")
    print("  或用一个常驻小服务定时调 gateway.heartbeat()。")
    print("  干跑阶段不需要常驻：mock 后端下每次 do/check 都会先起看门狗。")
    return 0 if ok else 1


def cmd_beat(gw):
    gw.heartbeat()
    print("心跳已刷新：%s" % time.strftime("%H:%M:%S"))
    return 0


def cmd_events(gw, n):
    for e in gw.safety.audit.tail(int(n or 15)):
        print(json.dumps(e, ensure_ascii=False))
    return 0


def cmd_doctor(gw):
    _bar("机器人子系统体检")
    print("配置路径  : %s" % rr.CONF_PATH)
    print("配置文件  : %s" % ("存在" if os.path.isfile(rr.CONF_PATH) else "不存在（用内置默认）"))
    print("项目根    : %s" % rr.ROOT)
    print("后端      : %s" % gw.backend_key)
    # 目录
    for name, p in (("logs", rr.log_path("")), ("calib", rr.calib_path("")),
                    ("desc", rr.desc_path("")), ("sim", rr.sim_path("")), ("data", rr.data_path(""))):
        print("目录 %-6s: %s %s" % (name, "存在" if os.path.isdir(p) else "不存在（首次运行会自动建）", p))
    # 依赖
    for mod, why in (("serial", "串口后端"), ("yaml", "配置编辑"), ("keyboard", "键盘急停"),
                     ("numpy", "视觉/标定计算")):
        try:
            __import__(mod)
            print("依赖 %-9s: 有    (%s)" % (mod, why))
        except Exception:
            print("依赖 %-9s: 没有  (%s) —— 相关功能会自动降级" % (mod, why))
    # ROS2
    import robot_ros2 as rros
    ok, info = rros.ros2_available()
    print("ROS2      : %s %s" % ("可用" if ok else "不可用", info))
    # 串口
    try:
        import serial.tools.list_ports as lp
        ps = list(lp.comports())
        print("串口      : %d 个 -> %s" % (len(ps), ", ".join(x.device for x in ps) or "无"))
    except Exception as e:
        print("串口      : 读不到（%s）" % e)
    # 安全
    s = gw.safety.snapshot()
    print("安全层    : 干跑=%s 急停=%s 看门狗=%s" % (s["dry_run"], s["estop"], s["watchdog_alive"]))
    # 结论
    print()
    print("结论：能跑的两条路 —— ① 现在就可以用 mock 后端把全链路与安全闸跑通；"
          "② 拿到硬件后把 backend 改成 serial/ros2/http 即可，上层不用改。")
    return 0


def cmd_selftest(gw):
    import robot_selftest as t
    return t.main()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd = argv[0].lower()
    rest = argv[1:]
    gw = build_gateway()
    try:
        if cmd == "status":
            return cmd_status(gw)
        if cmd == "robots":
            return cmd_robots(gw)
        if cmd == "state":
            return cmd_state(gw, rest[0] if rest else "mock:arm-01")
        if cmd == "skills":
            return cmd_skills(gw)
        if cmd == "do":
            if len(rest) < 2:
                print("用法: robot_ui.py do <机器人id> <技能> [k=v ...]")
                return 2
            return cmd_do(gw, rest[0], rest[1], rest[2:])
        if cmd == "estop":
            return cmd_estop(gw, " ".join(rest))
        if cmd == "clear":
            return cmd_clear(gw, rest[0] if rest else "human")
        if cmd == "watchdog":
            return cmd_watchdog(gw)
        if cmd == "beat":
            return cmd_beat(gw)
        if cmd == "events":
            return cmd_events(gw, rest[0] if rest else 15)
        if cmd == "doctor":
            return cmd_doctor(gw)
        if cmd == "selftest":
            return cmd_selftest(gw)
        print("未知命令: %s（试试 --help）" % cmd)
        return 2
    except KeyboardInterrupt:
        print("\n被中断（注意：这不会自动急停，需要时请显式 estop）")
        return 130
    except Exception as e:
        print("致命错误: %s: %s" % (type(e).__name__, e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
