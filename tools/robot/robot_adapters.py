# -*- coding: utf-8 -*-
"""robot_adapters.py -- 机器人适配层【统一契约】+ 网关（唯一入口）

【为什么需要它】跟 device_adapters.py 同一个道理：接新机器人 = 写一个 adapter（四个方法），
不改上层。机械臂 / 四足 / 人形 / 仿真，在"我"看来长得一模一样。

【契约】每个适配器实现四个方法（与 device_adapters.Adapter 完全一致，便于共用上层）
    caps()                   -> dict   能力、限制、电量、坐标系
    discover()               -> list   发现机器人  [{id,name,kind,online,...}]
    state(dev_id)            -> dict   读状态（电量/关节/位姿/故障码）
    act(dev_id, action, p)   -> dict   下发动作  {"ok":bool,"result":...,"error":...}

【★ 铁律】act() 只负责"把动作变成电信号"，它【不做】安全判断。
   所有动作必须经过 robot_gateway.RobotGateway.do()，由 robot_safety 裁决。
   —— 这样"大模型绕过安全直接动电机"在代码结构上就不可能发生。

【线协议】串口/星闪/网线都走同一套 JSON 行协议（一行一个 JSON，UTF-8，\n 结尾）：
    PC -> 机器人: {"cmd":"move","speed":0.3,"duration":1.0,"seq":12}\n
    机器人 -> PC: {"ok":true,"seq":12,"result":{...}}\n
    机器人主动上报: {"event":"state","battery_pct":82,"tilt_deg":3.1}\n
    ★ 机器人【必须】自己维护：
        · 急停优先级最高（收到 {"cmd":"stop"} 立刻停，不等 PC 下一步）
        · 与 PC 失联超过 X 秒自己停（板载看门狗，不依赖本模块）
"""
import json
import threading
import time

import robot_root as rr


class RobotAdapter:
    """所有机器人适配器的基类。照着写一个就能接一类机器人。"""

    name = "base"                 # 注册表 key
    protocol = "unknown"          # 人话协议名
    dof = 0                       # 自由度（0=轮式/未知）
    needs_pairing = False
    needs_account = False
    sim = False                   # 是不是仿真/模拟后端

    # ---- 四个必须实现的方法 ----
    def caps(self):
        return {
            "protocol": self.protocol,
            "kind": "unknown",            # wheeled / arm / quadruped / humanoid / sim
            "dof": self.dof,
            "actions": [],                # ★ 允许的动作名（闸2/闸3 会查这里）
            "readonly_actions": ["state", "caps", "ping"],
            "battery_pct": None,
            "limits": {},                 # 机器人自报的限制（与配置取更严的那个）
            "sim": self.sim,
        }

    def discover(self):
        return []

    def state(self, dev_id):
        return {"ok": False, "error": "not implemented"}

    def act(self, dev_id, action, params=None):
        return {"ok": False, "error": "not implemented"}

    # ---- 可选：停机/心跳（后端建议实现，安全层会调用）----
    def stop(self, dev_id=None, why=""):
        """立刻停止。安全层在急停/断线时会调它。默认回退到 act(stop)。"""
        try:
            if dev_id:
                return self.act(dev_id, "stop", {"why": why})
            out = []
            for d in self.discover():
                out.append(self.act(d.get("id"), "stop", {"why": why}))
            return {"ok": True, "result": out}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def heartbeat(self, dev_id):
        """探活（可选）。默认读 state。"""
        r = self.state(dev_id)
        return bool(r.get("ok"))


# 注册表：写一个新适配器，import 之后自己登记进来
ADAPTERS = {}


def register(adapter, key=None):
    """注册一个适配器实例。返回它的 key。"""
    k = key or getattr(adapter, "name", None) or adapter.__class__.__name__.lower()
    ADAPTERS[k] = adapter
    return k


def get(key):
    return ADAPTERS.get(key)


# ---------------------------------------------------------------------------
# 网关：上层（技能/我/球/CLI）唯一该碰的东西
# ---------------------------------------------------------------------------
class RobotGateway:
    """机器人网关 —— 所有动作的唯一入口，安全层的唯一前线。

    用法：
        gw = RobotGateway(safety)         # safety 为 None 时自动建一个
        gw.robots()                       # 列出机器人
        gw.do("mock:arm-01", "move", {"speed": 0.2, "duration": 1})   # 过闸 -> 下发
        gw.estop("person-in-range"); gw.clear_estop("human")
    """

    def __init__(self, safety=None, adapters=None, backend=None, backend_opts=None,
                 start_watchdog=None):
        import robot_safety as rs
        self.safety = safety or rs.Safety()
        self.adapters = adapters if adapters is not None else ADAPTERS
        self.backend_key = backend or (rr.CONF.get("backend") or "mock")
        self.backend_opts = backend_opts or (rr.CONF.get("backend_opts") or {})
        self._adapter = None
        self._seq = 0
        self._seq_lock = threading.Lock()      # 并发 do() 时 seq 唯一（复查：原来靠 GIL 巧合）
        # ★ 把安全层的停机回调接到后端：急停/断线时真的停
        self.safety.on_stop(self._on_safety_stop)
        # ★ 复查 M5：断线联锁必须默认就开着（以前只在 CLI 里起，而 CLI 200ms 就退出）。
        #   ★★ 但 2026-10-15 下午实测踩到它的反面：干跑/mock 后端没有物理风险，
        #      若还默认起看门狗，任何"起来看一眼就退"的进程都会因为没喂心跳
        #      而【把全局急停锁存写进去】—— 结果把球和语音一起锁住，
        #      安全机制反成了故障源（体检当时就报"机器人急停锁存"）。
        #   所以：真机后端（serial/http/ros2）默认起；mock/干跑默认不起（可显式 true 打开）。
        want_wd = start_watchdog
        if want_wd is None:
            want_wd = self._watchdog_should_run()
        if want_wd:
            try:
                self.safety.start_watchdog()
            except Exception as e:
                self.safety.audit.write("watchdog_start_fail", error=str(e))

    def _watchdog_should_run(self):
        """看门狗该不该【默认】起：配置 safety.watchdog 优先，其次看后端类型。

        ★ 配置统一从【安全层自己那份 conf】读（self.safety.cfg），不要读全局 rr.CONF：
          自测每个用例用的是独立 conf；读全局会让用例互相污染（实测栽过）。
        """
        cfg = getattr(self.safety, "cfg", None) or rr.CONF
        conf_want = (cfg.get("safety") or {}).get("watchdog")
        if conf_want is False:
            return False                                  # 显式关掉
        sim = str(self.backend_key or "").lower() in ("mock", "sim", "simulator")
        dry = bool((cfg.get("safety") or {}).get("dry_run", True))
        if sim or dry:
            return conf_want is True                      # 没接硬件：只有显式 true 才起
        return True                                       # 真机 + 非干跑：默认起（M5 的要求）

    # ---- 后端懒加载（默认 mock，保证"没有硬件也能跑通全链路"）----
    @property
    def adapter(self):
        if self._adapter is None:
            self._adapter = self._make_adapter(self.backend_key)
        return self._adapter

    def _make_adapter(self, key):
        k = (key or "mock").lower()
        if k in self.adapters:
            return self.adapters[k]
        if k == "mock":
            import robot_mock as rm
            return rm.MockAdapter(**self.backend_opts)
        if k == "serial":
            import robot_serial as rser
            return rser.SerialRobotAdapter(**self.backend_opts)
        if k == "ros2":
            import robot_ros2 as rros
            return rros.Ros2RobotAdapter(**self.backend_opts)
        if k == "http":
            import robot_http as rhttp
            return rhttp.HttpRobotAdapter(**self.backend_opts)
        raise RuntimeError("未知后端: %s（可选 mock/serial/ros2/http）" % key)

    def use_backend(self, key, **opts):
        """切换后端（测试用）。"""
        self.backend_key = key
        self.backend_opts = opts or {}
        self._adapter = None
        return True

    # ---- 发现/状态 ----
    def robots(self):
        try:
            return self.adapter.discover()
        except Exception as e:
            return [{"id": "error", "name": "后端异常", "online": False, "error": "%s: %s" % (type(e).__name__, e)}]

    def caps(self, dev_id):
        try:
            d = [x for x in self.robots() if x.get("id") == dev_id]
            if not d:
                return {}
            # ★ 优先问后端"这台机器人"的能力（不同机身动作集不同）；
            #   旧后端没有 dev_id 参数时会抛 TypeError，退回无参调用
            try:
                c = dict(self.adapter.caps(dev_id) or {})
            except TypeError:
                c = dict(self.adapter.caps() or {})
            c.update(d[0].get("caps") or {})
            return c
        except Exception:
            return {}

    def state(self, dev_id):
        """读状态：只读动作，永远允许（急停时也允许读，方便排障）。"""
        try:
            r = self.adapter.state(dev_id)
            self.safety.audit.write("state", robot=dev_id, ok=bool(r.get("ok")))
            return r
        except Exception as e:
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}

    def snapshot(self):
        return {"safety": self.safety.snapshot(), "backend": self.backend_key,
                "robots": self.robots()}

    # ---- 动作：唯一入口 ----
    def do(self, dev_id, action, params=None, timeout=None):
        """请求一个动作。返回 dict（永远不抛）。"""
        with self._seq_lock:
            self._seq += 1
            seq = self._seq
        caps = self.caps(dev_id)
        v = self.safety.check(dev_id, action, params, caps=caps)
        out = {"ok": False, "decision": v.decision, "reasons": v.reasons, "seq": seq,
               "robot": dev_id, "action": action}
        if not v.ok:
            if (rr.CONF.get("speak") or {}).get("on_block", True):
                pass          # 播报交给上层（网关不主动说话，避免刷屏）
            return out
        if v.decision == "dry-run":
            out["ok"] = True
            out["dry_run"] = True
            out["result"] = {"note": "干跑：安全闸已通过，未下发到硬件"}
            self.safety.audit.write("dryrun", robot=dev_id, action=action, seq=seq, params=v.params)
            return out
        # 真下发
        t0 = time.time()
        try:
            r = self.adapter.act(dev_id, action, v.params) or {}
        except Exception as e:
            r = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        dt = time.time() - t0
        out["ok"] = bool(r.get("ok"))
        out["result"] = r.get("result")
        out["error"] = r.get("error")
        out["latency_ms"] = int(dt * 1000)
        self.safety.audit.write("act", robot=dev_id, action=action, seq=seq,
                                ok=out["ok"], error=out.get("error"),
                                latency_ms=out["latency_ms"], params=v.params)
        # ★ 下发失败也要停：不确定机器人现在什么状态，就让它停住
        if not out["ok"]:
            try:
                self.adapter.stop(dev_id, "act-failed")
            except Exception:
                pass
        return out

    # ---- 安全层转发 ----
    def estop(self, reason="manual"):
        return self.safety.estop(reason, "gateway")

    def clear_estop(self, who="human", note=""):
        return self.safety.clear_estop(who, note)

    def heartbeat(self):
        return self.safety.heartbeat()

    def _on_safety_stop(self, why):
        """安全层要求停机 —— 真的去停（失败也不抛）。"""
        try:
            d = self.backend_opts.get("dev_id")
            if d:
                self.adapter.stop(d, why)
            else:
                self.adapter.stop(None, why)
            self.safety.audit.write("backend_stop", why=why)
        except Exception as e:
            self.safety.audit.write("backend_stop_fail", why=why, error=str(e))
