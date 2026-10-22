# -*- coding: utf-8 -*-
"""robot_root.py -- 机器人子系统的配置根（一个文件决定所有路径与阈值）

【为什么单独一份，不复用 fairy_root】
  robot 这套要能独立测试、独立搬走（以后可能跑在机器人身上的小电脑里，
  或者跑在一台只有 Python 的机器上）。所以这里只做两件事：
    1) 找到项目根 -> 定位 robot\\ 数据目录与配置
    2) 给配置一个【唯一的默认值表】，任何键缺失都能退回默认，绝不抛异常

【配置优先级】环境变量 FAIRY_ROBOT_CONF > <root>\\robot\\robot.json > 内置默认
【路径约定】
  <root>\\robot\\robot.json   配置
  <root>\\robot\\logs\\       审计与事件日志
  <root>\\robot\\calib\\      标定文件（★ 必须进版本库）
  <root>\\robot\\desc\\       URDF / MJCF / 网格
  <root>\\robot\\sim\\        仿真场景（大，可删可重建）
  <root>\\robot\\data\\       演示数据集（大，单独盘/定期归档）

【纪律】本模块只读配置，不写任何文件；写文件由调用方负责。
"""
import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))

# ★ 环境变量优先（即使进程运行中途才设，本模块被重新加载时也要认）
_ev = os.environ.get("FAIRY_ROBOT_CONF")
if _ev and os.path.isfile(_ev):
    CONF_PATH = _ev
    _ROOT_HINT = ""
else:
    CONF_PATH = ""
    _ROOT_HINT = ""

# 配置查找顺序：环境变量指定 -> 上层项目根 -> 本目录上层
_CANDIDATES = []
if _ev:
    _CANDIDATES.append(_ev)
for _up in (
    os.path.dirname(os.path.dirname(_HERE)),      # tools/robot -> tools -> <root>
    os.path.dirname(_HERE),                        # tools/robot -> tools
    _HERE,
):
    _CANDIDATES.append(os.path.join(_up, "robot", "robot.json"))
    _CANDIDATES.append(os.path.join(_up, "robot.json"))

for _c in _CANDIDATES:
    if _c and os.path.isfile(_c):
        CONF_PATH = _c
        break
if not CONF_PATH:
    CONF_PATH = _CANDIDATES[0] if _CANDIDATES else os.path.join(_HERE, "robot.json")

# 项目根：配置在 <root>\robot\robot.json 时，root 就是它的上上层
ROOT = os.path.dirname(os.path.dirname(CONF_PATH)) if CONF_PATH.endswith(os.path.join("robot", "robot.json")) else os.path.dirname(CONF_PATH)
CONF = {}
CONF_ERRORS = []


def _defaults():
    """内置默认：即使一个配置文件都没有，机器人子系统也能跑（用 mock 后端）。"""
    return {
        "enabled": True,
        "backend": "mock",              # mock / serial / ros2 / http
        "backend_opts": {},             # 例：{"port": "COM7", "baud": 115200}
        "estop": {
            "pin_file": "",             # 外部急停开关的状态文件（存在且内容为 1 = 急停按下）
            "hotkey": "ctrl+alt+f12",   # 键盘急停（软急停）
            "on_start": False           # 启动时是否就处于急停态（默认否；测试时用 True 更安全）
        },
        "safety": {
            "max_speed_mps": 0.6,       # 最大线速度
            "max_yaw_rps": 1.2,         # 最大角速度
            "max_joint_torque": 0.6,    # 关节力矩上限（归一化 0~1）
            "max_reach_m": 0.55,        # 工作半径（机械臂防止打到自己/桌面）
            "max_duration_s": 30.0,     # 单条技能最长执行时间
            "min_battery_pct": 15,      # 低于此电量拒绝动作（只允许回充/停机）
            "max_tilt_deg": 35.0,       # 四足/人形倾角上限，超过立即停
            "max_obstacle_m": 0.20,     # 前进方向最近障碍距离，低于此拒绝前进
            "heartbeat_timeout_s": 2.0, # 与"大脑"心跳超时 -> 联锁停机
            "dry_run": True             # ★ 默认干跑：所有动作只走安全闸、不下发
        },
        "gates": {
            "whitelist": True,          # 闸1 白名单：只有登记过的机器人与技能才能动
            "default_readonly": True,   # 闸2 默认只读：没显式允许的动作为只读
            "action_whitelist": True,   # 闸3 动作白名单
            "danger_words": True,       # 闸4 危险词（含中英拼音）
            "foreground": False,        # 闸5 前台守卫（机器人场景用不到，留接口）
            "audit": True               # 闸6 审计日志（每个动作都记）
        },
        "robots": {},                   # 每台机器人的登记：见 robot_adapters.py 的说明
        "skills": {},                   # 技能开关覆盖：{"arm.pick": false}
        "audit": {"max_mb": 8},         # 审计日志滚动上限
        "speak": {"on_block": True, "on_estop": True},   # 拦截/急停时要不要播报
    }


def _load():
    global CONF
    CONF = _defaults()
    # ★ 环境变量指向的配置不存在要留痕（否则"我改了配置怎么没生效"会查半天）；
    #   但如果它正好指向我们本来就会用的那份，就不必啰嗦
    _ev2 = os.environ.get("FAIRY_ROBOT_CONF")
    if _ev2 and not os.path.isfile(_ev2):
        try:
            same = os.path.abspath(_ev2) == os.path.abspath(CONF_PATH)
        except Exception:
            same = False
        if not same:
            CONF_ERRORS.append("env-conf-missing: FAIRY_ROBOT_CONF=%s（不存在 -> 改用 %s）" % (_ev2, CONF_PATH))
    if not os.path.isfile(CONF_PATH):
        CONF_ERRORS.append("no-config-file: %s（用内置默认）" % CONF_PATH)
        return
    try:
        with open(CONF_PATH, "r", encoding="utf-8-sig") as f:
            user = json.load(f)
    except Exception as e:
        # ★ 配置读失败必须留痕（历史教训：静默兜底会造假象）
        CONF_ERRORS.append("config-error: %s: %s" % (type(e).__name__, e))
        return
    if not isinstance(user, dict):
        CONF_ERRORS.append("config-not-dict: %s" % type(user).__name__)
        return
    # 浅合并一层（够用且可预期）；safety/gates 这类字典逐键合并
    for k, v in user.items():
        if isinstance(v, dict) and isinstance(CONF.get(k), dict):
            CONF[k].update(v)
        else:
            CONF[k] = v


_load()


# ---- 路径助手（只拼路径，不建目录；建目录由调用方按需做）----
def rp(*parts):
    return os.path.join(ROOT, "robot", *parts)


def log_path(*parts):
    return rp("logs", *parts)


def calib_path(*parts):
    return rp("calib", *parts)


def desc_path(*parts):
    return rp("desc", *parts)


def sim_path(*parts):
    return rp("sim", *parts)


def data_path(*parts):
    return rp("data", *parts)


def ensure_dir(p):
    """建目录；失败不抛（返回 False），调用方自己决定怎么办。"""
    try:
        os.makedirs(p, exist_ok=True)
        return True
    except Exception as e:
        CONF_ERRORS.append("mkdir-fail: %s: %s" % (p, e))
        return False
