# -*- coding: utf-8 -*-
"""robot_bridge.py -- 把机器人接进 Fairy（唯一对接点）

【它做什么】
  1) 变成一个 device_adapters 的适配器（name = "robot"），于是"发现设备"里就有机器人了
  2) 把话说人话：动作 -> 中文反馈，供球播报 / 界面显示
  3) 上游来的自然语言请求 -> 技能（这里是**规则匹配**，不是大模型；
     要让它"听得懂"复杂指令，就把意图识别交给大模型，再由它调用这个桥）

【三条使用路径】
  A. Python 内调用（球/其它模块）：
        import robot_bridge as rb
        rb.act("打开抓取")          ->  {"ok":True,"say":"好，我去抓了"}
        rb.safety_snapshot()
        rb.estop("人靠太近")
  B. 命令行：py -3.12 robot_bridge.py do mock:arm-01 pick target=杯子
  C. 作为适配器被 device_adapters 发现（caps/discover/state/act 四件套）

【★ 纪律】本文件不绕过安全层。所有动作都走 robot_gateway -> robot_safety。
"""
import json
import os
import sys
import threading
import time

# ★ 两个路径都要进 sys.path：
#   本文件所在目录（tools/，为了 import device_adapters）
#   tools/robot/（机器人子系统的模块都住在那儿）
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, "robot")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import robot_root as rr            # noqa: E402
import robot_safety as rs          # noqa: E402
import robot_adapters as ra        # noqa: E402

_GW = None
_LOCK = threading.RLock()
_SAY = []          # 待播报队列（球可以来取）


def gateway():
    """懒建单例网关（全进程一个安全层，避免两套急停互不知道）。"""
    global _GW
    with _LOCK:
        if _GW is None:
            for mod in ("robot_mock", "robot_serial", "robot_http", "robot_ros2"):
                try:
                    __import__(mod)
                except Exception as e:
                    rr.CONF_ERRORS.append("import-%s: %s" % (mod, e))
            # ★ 复查 M5/M8：网关默认会自己起看门狗（断线联锁），这里再补两件事：
            #   1) 播报挂上去（急停/拦截时能出声）
            #   2) 别用"生产锁存文件"做试验 —— 桥是给球/语音调的，它读到生产状态是对的，
            #      但自测/探针必须用各自独立的 state_file（见 robot_selftest.Fixture）
            _GW = ra.RobotGateway()
            _GW.safety.set_speaker(say)
        return _GW


def say(text):
    """把要播报的话放进队列（球会来取；也直接返回，方便调用方自己念）。"""
    item = {"t": time.time(), "text": text}
    _SAY.append(item)
    del _SAY[:-20]          # 只留最近 20 条
    return text


def drain_say():
    """取走待播报的话（球用）。"""
    with _LOCK:
        out = _SAY[:]
        del _SAY[:]
    return out


# ---------------------------------------------------------------------------
# 人话映射
# ---------------------------------------------------------------------------
_KIND_CN = {"arm": "机械臂", "wheeled": "轮式底盘", "quadruped": "四足机器人",
            "humanoid": "人形机器人", "gateway": "机器人网关", "mixed": "机器人", "sim": "模拟"}

_ACTION_CN = {
    "move": "往前走", "turn": "转身", "walk": "走一段", "stop": "停下",
    "grip": "抓住", "release": "松开", "home": "回零位", "stand": "站起来",
    "sit": "趴下", "look": "看过去", "pick": "抓起来", "place": "放下",
    "patrol": "巡逻一趟", "state": "看看状态",
}


def _cn_kind(k):
    return _KIND_CN.get((k or "").lower(), k or "机器人")


def _verdict_to_say(dev_id, action, r):
    """把裁决/结果翻成人话（球直接念）。"""
    dec = r.get("decision")
    act_cn = _ACTION_CN.get((action or "").lower(), action)
    if dec == "block":
        why = "；".join(r.get("reasons") or [])
        if "estop" in why:
            return "现在处于急停状态，我不会动。先确认安全，再让我解除急停。"
        if "whitelist" in why:
            return "这台机器人没登记过，我不碰。"
        if "danger" in why or "forbidden" in why:
            return "这个指令被安全规则拦下了，我不会执行。"
        if "readonly" in why:
            return "这个动作没被允许，我只读状态。"
        if "battery" in why:
            return "电量太低，我不做动作了，先充电。"
        return "被安全闸拦住了：%s" % why
    if dec == "dry-run":
        return "安全闸都过了（现在是演练模式，没有真的动）。"
    if r.get("ok"):
        return "好，%s了。" % act_cn
    return "%s没成功：%s" % (act_cn, r.get("error") or "未知原因")


# ---------------------------------------------------------------------------
# 对外动作（人话入口）
# ---------------------------------------------------------------------------
def status():
    gw = gateway()
    snap = gw.snapshot()
    return {"ok": True, "safety": snap["safety"], "backend": snap["backend"], "robots": snap["robots"]}


def safety_snapshot():
    return gateway().safety.snapshot()


def robots():
    return gateway().robots()


def estop(reason="manual"):
    gateway().estop(reason)
    return {"ok": True, "say": say("急停已触发。所有人退开，确认安全后再解除。")}


def clear_estop(who="human"):
    was = gateway().clear_estop(who)
    return {"ok": True, "was_active": was,
            "say": say("急停已解除。我会保持慢速，随时准备再停。")}


def heartbeat():
    return gateway().heartbeat()


def act(dev_id, action, params=None):
    """下发一个动作（必过安全层）。返回 {ok, decision, say, ...}"""
    gw = gateway()
    r = gw.do(dev_id, action, params or {})
    r["say"] = _verdict_to_say(dev_id, action, r)
    return r


def do_natural(dev_id, skill, params=None):
    """走技能层（组合技能会带回退）。"""
    import robot_skills as rk
    gw = gateway()
    sk = rk.Skills(gw)
    r = sk.run(dev_id, skill, params or {})
    r["say"] = _verdict_to_say(dev_id, skill, r)
    # 参数名写错时补一句人话（并让 CLI 用退出码 3 表示"请求有问题"）
    if r.get("ignored_params"):
        r["say"] = (r.get("say") or "") + "（忽略了不认识的参数：%s）" % "、".join(r["ignored_params"])
    return r


def find_robot(text=""):
    """按名字/类型挑一台机器人（自然语言里只有"机器人"时用）。"""
    rs_ = robots()
    rs_ = [x for x in rs_ if x.get("online")]
    if not rs_:
        return None
    t = (text or "").lower()
    for r in rs_:
        if r.get("name") and r["name"].lower() in t:
            return r["id"]
    # 关键词 -> 类型
    want = None
    if any(k in t for k in ("机械臂", "手", "arm")):
        want = ("arm",)
    elif any(k in t for k in ("狗", "四足", "quadruped", "go2")):
        want = ("quadruped",)
    elif any(k in t for k in ("车", "底盘", "wheeled")):
        want = ("wheeled",)
    elif any(k in t for k in ("人形", "humanoid")):
        want = ("humanoid",)
    if want:
        for r in rs_:
            if (r.get("kind") or "").lower() in want:
                return r["id"]
    return rs_[0]["id"]


# 中文动词表（自然语言 -> 技能名）。只做**规则**匹配；复杂意图交给大模型。
# ★ 顺序即优先级：先写"更具体"的词。例：
#     往前走 -> 前进(move) 而不是 walk；不带"前"的"走"才是 walk（四足）
_VERBS = [
    ("抓", "pick"), ("拿", "pick"), ("夹", "grip"),
    ("放", "place"), ("松开", "release"), ("松手", "release"), ("松", "release"),
    ("巡逻", "patrol"),
    ("前进", "move"), ("往前", "move"), ("向前", "move"), ("过来", "move"),
    ("走", "walk"), ("溜达", "walk"),
    ("转", "turn"), ("回头", "turn"),
    ("站", "stand"), ("起立", "stand"), ("趴", "sit"), ("坐下", "sit"),
    ("回零", "home"), ("归位", "home"), ("复位", "home"),
    ("看", "look"), ("停", "stop"), ("别动", "stop"),
]


def _fix_by_kind(sk, rid):
    """按机器人类型把方向类动词纠正一下（轮式没有"走"这个动作）。"""
    if sk != "walk" or not rid:
        return sk
    for r in robots():
        if r.get("id") == rid:
            if (r.get("kind") or "").lower() in ("wheeled", "arm", "gateway", "mixed"):
                return "move"
            return sk
    return sk


def parse_intent(text, rid=None):
    """把一句中文变成 (技能, 参数)。规则优先，认不出就返回 (None, {})。

    rid 可选：给了就按机器人类型纠正动词（轮式"往前走"应是 move 而不是 walk）。
    """
    t = (text or "").strip()
    if not t:
        return None, {}
    # 速度/时长之类的显式数字
    params = {}
    import re
    m = re.search(r"(?:速度|speed)\s*([0-9]*\.?[0-9]+)", t)
    if m:
        params["speed"] = float(m.group(1))
    m = re.search(r"([0-9]+)\s*(?:秒|s)\b", t)
    if m:
        params["duration"] = float(m.group(1))
    m = re.search(r"巡逻\s*([0-9]+)\s*趟", t)
    if m:
        params["legs"] = int(m.group(1))
    for kw, sk in _VERBS:
        if kw in t:
            return _fix_by_kind(sk, rid), params
    return None, params


def handle(text, dev_id=None):
    """一句话 -> 动作。这是给球/语音用的总入口。"""
    rid = dev_id or find_robot(text)
    sk, params = parse_intent(text, rid)
    if not rid:
        return {"ok": False, "say": "我没找到在线的机器人。", "error": "no-robot"}
    if not sk:
        return {"ok": False, "say": "这句我没听懂，你可以说：抓起来 / 放下 / 往前走 / 停下来 / 巡逻两趟。",
                "error": "no-intent", "robot": rid}
    r = do_natural(rid, sk, params)
    r["robot"] = rid
    r["skill"] = sk
    return r


# ---------------------------------------------------------------------------
# 变成一个 device_adapters 适配器（这样"发现设备"里就有机器人）
# ---------------------------------------------------------------------------
def as_device_adapter():
    """返回一个符合 device_adapters.Adapter 四件套的适配器实例。"""
    class _RobotDeviceAdapter:
        name = "robot"
        protocol = "机器人（经 robot_gateway 安全层）"
        needs_account = False
        needs_pairing = False

        def caps(self):
            gw = gateway()
            return {"protocol": self.protocol, "actions": ["state", "do", "estop", "clear", "robots"],
                    "needs_account": False, "needs_pairing": False, "discovery": "robot_gateway",
                    "backend": gw.backend_key,
                    "dry_run": bool((rr.CONF.get("safety") or {}).get("dry_run", True))}

        def discover(self):
            out = []
            for r in robots():
                out.append({"id": r.get("id"), "name": r.get("name") or r.get("id"),
                            "kind": "robot:" + str(r.get("kind") or "unknown"),
                            "online": bool(r.get("online")),
                            "adapter": "robot", "battery_pct": r.get("battery_pct")})
            return out

        def state(self, dev_id):
            return gateway().state(dev_id)

        def act(self, dev_id, action, params=None):
            p = params or {}
            if action == "estop":
                return {"ok": True, "result": estop(p.get("reason", "adapter"))}
            if action == "clear":
                return {"ok": True, "result": clear_estop(p.get("who", "human"))}
            if action == "robots":
                return {"ok": True, "result": robots()}
            try:
                return gateway().do(dev_id, action, p)
            except Exception as e:
                return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}

    return _RobotDeviceAdapter()


def register_into_device_adapters():
    """尽力注册到 device_adapters.ADAPTERS；失败不影响本模块单独使用。"""
    try:
        import device_adapters as da
        ad = as_device_adapter()
        da.ADAPTERS["robot"] = ad
        return True, "已注册到 device_adapters.ADAPTERS['robot']"
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------
def _kv(argv):
    out = {}
    for a in argv:
        if "=" in a:
            k, v = a.split("=", 1)
            try:
                out[k] = float(v)
            except Exception:
                out[k] = v
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    c = argv[0].lower()
    rest = argv[1:]
    if c == "status":
        print(json.dumps(status(), ensure_ascii=False, indent=1, default=str))
        return 0
    if c == "robots":
        for r in robots():
            print("%-20s %-10s %s" % (r.get("id"), r.get("kind"), r.get("name")))
        return 0
    if c == "say":
        # 说一句话 -> 走规则意图识别
        r = handle(" ".join(rest))
        print(json.dumps(r, ensure_ascii=False, indent=1, default=str))
        return 0 if r.get("ok") else 3
    if c == "do":
        if len(rest) < 2:
            print("用法: robot_bridge.py do <机器人id> <技能> [k=v...]")
            return 2
        r = do_natural(rest[0], rest[1], _kv(rest[2:]))
        print(json.dumps(r, ensure_ascii=False, indent=1, default=str))
        # ok 但参数被忽略时也算"请求有问题" -> 退出码 3，避免"以为生效了"
        if not r.get("ok") or r.get("ignored_params"):
            return 3
        return 0
    if c == "estop":
        print(json.dumps(estop(" ".join(rest) or "cli"), ensure_ascii=False))
        return 0
    if c == "clear":
        print(json.dumps(clear_estop(rest[0] if rest else "cli"), ensure_ascii=False))
        return 0
    if c == "beat":
        n = int(rest[0]) if rest else 1
        gw = gateway()
        for _ in range(max(1, n)):
            gw.heartbeat()
        print("已喂心跳 %d 次（%s）" % (max(1, n), time.strftime("%H:%M:%S")))
        return 0
    if c == "beatloop":
        # ★ 真机常驻用：它活着 = 大脑还在；它一停，看门狗会在 timeout 秒内自动急停。
        gw = gateway()
        period = float(rest[0]) if rest else 0.5
        print("开始常驻喂心跳，每 %.1fs 一次（Ctrl+C 停止）。停了就会自动急停 —— 这是设计如此。" % period)
        n = 0
        try:
            while True:
                gw.heartbeat()
                n += 1
                if n % 20 == 0:
                    print("  已喂 %d 次（%s）" % (n, time.strftime("%H:%M:%S")))
                time.sleep(period)
        except KeyboardInterrupt:
            print("\n停止喂心跳；看门狗会在超时后自动急停（需要继续就重新运行）")
            return 0
    if c == "register":
        ok, info = register_into_device_adapters()
        # ★ 别用 ✔/✘ 这类符号：本机控制台是 GBK，print 会抛 UnicodeEncodeError
        print(("成功: " if ok else "失败: ") + info)
        return 0 if ok else 1
    if c == "discover":
        # 证明"注册进去之后，上游的发现流程里真的有机器人"（同一个进程里验）
        ok, info = register_into_device_adapters()
        print(("注册: " if ok else "注册失败: ") + info)
        try:
            import device_adapters as da
            found = []
            for k, ad in (da.ADAPTERS or {}).items():
                if k != "robot":
                    continue
                found = ad.discover()
            print("device_adapters 里发现 %d 台机器人:" % len(found))
            for r in found:
                print("  %-16s %-18s online=%s 电池=%s" % (r.get("id"), r.get("name"),
                                                          r.get("online"), r.get("battery_pct")))
        except Exception as e:
            print("发现流程异常: %s: %s" % (type(e).__name__, e))
            return 1
        return 0
    print("未知命令: %s" % c)
    return 2


if __name__ == "__main__":
    sys.exit(main())
