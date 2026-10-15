# -*- coding: utf-8 -*-
"""robot_safety.py -- 机器人的安全层（六道闸 + 急停 + 断线联锁 + 看门狗）

【设计铁律】大模型永远不能直接碰电机。它只能"请求技能"，由这里决定放不放行。
  1) 反射（<=10ms）：平衡/避障/限位 —— 在机器人板载固件里，不经过本模块
  2) 技能（10~100ms）：本模块放行后，交给技能层执行
  3) 决策（0.5~3s）：大模型（我）—— 只能发"请求"，说话不算数

【六道闸】与 device_adapters.py 的既有设计保持一致，逐条可测：
  闸1 白名单     whitelist     只有登记过的机器人 + 登记过的技能才允许
  闸2 默认只读   readonly      没显式声明为"写"的动作，一律只读（读状态随便读）
  闸3 动作白名单 action_wl     动作名必须在允许集合里
  闸4 危险词     danger_words  中/英/拼音，命中即拒（并且默认拒绝未知动作）
  闸5 前台守卫   foreground    机器人场景默认关闭，但接口保留（PC 上跑时有意义）
  闸6 审计       audit         每一次裁决（放行/拦截/急停）都落盘

【三条联锁】
  · 急停：软（本模块标志）+ 硬（外部开关状态文件）+ 键盘快捷键；【锁存】，必须显式清除
  · 断线：与"大脑"的心跳超过 timeout -> 自动急停（这是"电脑挂了机器人要停住"）
  · 看门狗：独立线程，只读状态、只做停机，不做任何"聪明"决定

【为什么急停要锁存】
  一次抖动就让急停自动恢复 = 机器人在人还站在旁边时突然又动了。
  所以：任何触发源都必须由人（或显式的 clear_estop(reason=...)）清除。

【纪律】
  · 本模块不 import 任何后端；停机只调调用方注册的 stop 回调
  · 任何异常都不向上抛（安全层自己崩溃 = 最危险的情况），一律降级为"拦截"
"""
import json
import os
import threading
import time

import robot_root as rr

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
DEC_ALLOW = "allow"
DEC_BLOCK = "block"
DEC_DRYRUN = "dry-run"

# 危险词（中/英/拼音）—— 命中即拒绝。★ 与 device_adapters 的 85 条同一思路，这里只留机器人相关的
DANGER_WORDS = [
    "禁用急停", "关掉急停", "取消急停", "旁路", "绕过", "bypass", "ignore safety",
    "disable estop", "disable e-stop", "force", "强制", "解锁限位", "解除限位",
    "delete", "format", "rm -rf", "关机", "shutdown", "reboot", "重启系统",
    "改固件", "刷固件", "flash firmware", "dd if=", "mkfs",
    "qiangzhi", "jiaoting", "pojie", "shuaji",
]

# 只读动作（无论配置怎么写，这些永远允许）
READONLY_ACTIONS = {
    "state", "status", "caps", "discover", "list", "ping", "heartbeat",
    "read", "info", "battery", "pose", "joints", "list_ports",
}

# 永远禁止的动作（连白名单都不能开）
FORBIDDEN_ACTIONS = {
    "estop_disable", "safety_off", "firmware_flash", "raw_motor", "direct_pwm",
    "set_limit_off", "shell", "exec",
}


class Verdict:
    """一次安全裁决的结果。"""

    __slots__ = ("decision", "reasons", "action", "robot", "params", "ts",
                 "params_notes", "params_rejects")

    def __init__(self, decision, action, robot, reasons=None, params=None):
        self.decision = decision
        self.action = action
        self.robot = robot
        self.reasons = list(reasons or [])
        self.params = params or {}
        self.ts = time.time()
        self.params_notes = []       # 归一化/限幅/丢弃 的明细
        self.params_rejects = []     # 被拒的参数（类型错等）

    @property
    def ok(self):
        return self.decision in (DEC_ALLOW, DEC_DRYRUN)

    def to_dict(self):
        return {
            "decision": self.decision,
            "allowed": self.ok,
            "action": self.action,
            "robot": self.robot,
            "reasons": self.reasons,
            "params": self.params,
            "params_notes": self.params_notes,
            "params_rejects": self.params_rejects,
            "ts": self.ts,
        }

    def __repr__(self):
        return "<Verdict %s %s/%s %s>" % (self.decision, self.robot, self.action, ";".join(self.reasons))


# ---------------------------------------------------------------------------
# 审计
# ---------------------------------------------------------------------------
class Audit:
    """审计日志：只追加、可滚动、失败不抛（审计坏了不能拦住机器人停机）。"""

    def __init__(self, path=None, max_mb=None):
        self.path = path or rr.log_path("robot_audit.jsonl")
        self.max_bytes = int((max_mb or (rr.CONF.get("audit") or {}).get("max_mb") or 8) * 1024 * 1024)
        self._lock = threading.Lock()

    def write(self, event, **fields):
        rec = {"ts": time.time(), "time": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event}
        rec.update(fields)
        try:
            line = json.dumps(rec, ensure_ascii=False)
        except Exception:
            # ★ 参数里混进不可序列化的对象时退化成 repr，不能让整条审计丢
            try:
                line = json.dumps({k: repr(v) for k, v in rec.items()}, ensure_ascii=False)
            except Exception:
                line = json.dumps({"ts": rec["ts"], "time": rec["time"], "event": str(event),
                                   "note": "unserializable"})
        try:
            with self._lock:
                rr.ensure_dir(os.path.dirname(self.path))
                self._rotate_if_needed()
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception:
            pass          # ★ 审计失败也绝不抛

    def _rotate_if_needed(self):
        try:
            if os.path.exists(self.path) and os.path.getsize(self.path) > self.max_bytes:
                os.replace(self.path, self.path + ".1")
        except Exception:
            pass

    def tail(self, n=20):
        try:
            with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
            out = []
            for ln in lines[-n:]:
                try:
                    out.append(json.loads(ln))
                except Exception:
                    out.append({"raw": ln})
            return out
        except Exception:
            return []


# ---------------------------------------------------------------------------
# 安全层
# ---------------------------------------------------------------------------
class Safety:
    def __init__(self, conf=None, audit=None, speak=None, state_file=None):
        self.cfg = (conf or rr.CONF)
        self.safety = self.cfg.get("safety") or {}
        self.gates = self.cfg.get("gates") or {}
        self.audit = audit or Audit()
        self._speak = speak            # 可注入的播报函数（默认不播）
        self._lock = threading.RLock()
        # ★ 急停状态【跨进程持久化】：命令行一次一个进程，如果只存在内存里，
        #   "estop 之后另一个进程照样能动" —— 这是致命漏洞（自测第 16 项盯着它）
        self.state_file = state_file or rr.log_path("estop_state.json")
        self._state_read_error = ""
        self.st = self._load_state()
        # 急停（锁存）
        self._estop = bool(self.st.get("estop")) or bool((self.cfg.get("estop") or {}).get("on_start"))
        self._estop_reason = self.st.get("reason") or ("on_start" if self._estop else "")
        self._estop_ts = float(self.st.get("ts") or (time.time() if self._estop else 0.0))
        # ★ 两种情况都要落盘：
        #   ① 因为 on_start 进入急停  ② 状态文件不存在（第一次跑）—— 立刻建立"唯一真相"，
        #      这样以后文件被删/被写坏，都会走"存在但读不到 -> 保守取急停"那一支
        if not os.path.exists(self.state_file) or (self._estop and not self.st.get("estop")):
            self._save_state()
        if self._state_read_error and self._state_read_error != "missing(首次运行)":
            self.audit.write("estop_state_unreadable", err=self._state_read_error,
                             conservative_estop=bool(self._estop))
        # 心跳（与"大脑"的联通）
        self._hb_last = time.time()
        self._hb_seen = False
        self._hb_stop = False
        # 停机回调：注册后，急停/断线时会调用它
        self._stop_cbs = []
        self._watchdog = None
        self._wd_period = 0.5
        self._wd_stopped = False        # 看门狗本轮是否已经执行过停机（避免每 0.5s 重复调）
        self._require_hb = False        # 看门狗起来后置 True：没有心跳也算断线
        self._boot_ts = 0.0             # 启动宽限期截止时刻
        self._started_at = time.time()
        # 外部改了急停文件时，进程内回调（多进程协作）
        self._estop_cbs = []
        self._hw_warned = False        # 硬件急停缺失只提醒一次
        # 统计（自测要看）
        self.stats = {"checks": 0, "allowed": 0, "blocked": 0, "dryrun": 0, "estops": 0}

    # ---- 跨进程急停状态 ----
    def _load_state(self):
        """读急停状态文件。★ 三态区分，绝不把"读不到"当成"没急停"。

        历史教训（2026-10-15 独立复查 M2）：文件写坏/被删/内容不是对象时，
        旧实现返回 {} -> 新进程认为"从没按过急停" -> 照样能下发动作。
        而急停状态文件正是"唯一真相"，掉电写坏、被杀软清理、磁盘写失败都会发生。
        所以：读不到 / 解析失败 / 类型不对 —— 一律保守取 estop=True，并留痕。
        """
        p = self.state_file
        try:
            if not os.path.exists(p):
                # 文件不存在：全新机器（第一次跑）与"文件被人删了"无法区分。
                # 全新机器上直接锁急停会让人以为坏了，所以这里取"未急停"，
                # 但立刻把状态文件写出来 —— 之后任何人删文件都会变成"存在但读不到"，
                # 那一支是保守取急停的（见下面的 except）。
                self._state_read_error = "missing(首次运行)"
                return {"estop": bool((self.cfg.get("estop") or {}).get("on_start")), "reason": "", "ts": 0.0}
            with open(p, "r", encoding="utf-8") as f:
                raw = f.read()
            d = json.loads(raw)
            if not isinstance(d, dict):
                self._state_read_error = "not-dict:%s" % type(d).__name__
                return {"estop": True, "reason": "state-file-invalid(保守取急停)", "ts": time.time(),
                        "_uncertain": True}
            self._state_read_error = ""
            return d
        except Exception as e:
            self._state_read_error = "%s: %s" % (type(e).__name__, e)
            return {"estop": True, "reason": "state-file-unreadable(保守取急停)", "ts": time.time(),
                    "_uncertain": True}

    def _save_state(self):
        """原子写：先写 .tmp 再 replace（避免读到半截 JSON）。"""
        try:
            rr.ensure_dir(os.path.dirname(self.state_file))
            tmp = self.state_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"estop": bool(self._estop), "reason": self._estop_reason,
                           "ts": self._estop_ts, "who": self.st.get("who", ""),
                           "updated": time.time()}, f, ensure_ascii=False)
            os.replace(tmp, self.state_file)
            return True
        except Exception:
            return False

    def on_estop(self, fn):
        """注册"别人按了急停"时的回调（可选）。"""
        if callable(fn):
            self._estop_cbs.append(fn)

    def sync_external_estop(self):
        """读一次外部状态文件：如果别的进程按了急停，本进程也要认。"""
        d = self._load_state()
        ext = bool(d.get("estop"))
        with self._lock:
            if ext and not self._estop:
                self._estop = True
                self._estop_reason = d.get("reason") or "external"
                self._estop_ts = float(d.get("ts") or time.time())
                self.stats["estops"] += 1
                changed = True
            elif (not ext) and self._estop and (d.get("updated", 0) > self._started_at):
                # 别人解除了急停（且那次解除发生在本次启动之后）-> 跟着解除
                self._estop = False
                self._estop_reason = ""
                changed = True
            else:
                changed = False
        if changed:
            self.audit.write("estop_sync", estop=self._estop, reason=self._estop_reason, src="state_file")
            for cb in list(self._estop_cbs):
                try:
                    cb(self._estop, self._estop_reason)
                except Exception:
                    pass
        return changed

    # ---- 基础 ----
    def set_speaker(self, fn):
        self._speak = fn

    def on_stop(self, fn):
        """注册"停机"回调（由后端实现：真的发停止指令 / 松力矩 / 刹车）。"""
        if callable(fn):
            self._stop_cbs.append(fn)

    def _say(self, text, kind="on_block"):
        try:
            if self._speak and (self.cfg.get("speak") or {}).get(kind, True):
                self._speak(text)
        except Exception:
            pass

    # ---- 急停 ----
    @property
    def estop_active(self):
        with self._lock:
            return self._estop

    def estop(self, reason="manual", source="api"):
        """触发急停。幂等；已锁存时不重复触发动作，但会记一条审计。★ 会落盘（跨进程）"""
        with self._lock:
            first = not self._estop
            self._estop = True
            self._estop_reason = "%s(%s)" % (reason, source)
            self._estop_ts = time.time()
            self.st = {"estop": True, "reason": self._estop_reason, "ts": self._estop_ts,
                       "who": source}
            self.stats["estops"] += 1
        self._save_state()
        self.audit.write("estop", reason=reason, source=source, first=first)
        if first:
            self._do_stop("estop:%s" % reason)
            self._say("急停已触发，%s。所有人退开，确认安全后再解除。" % reason, "on_estop")
        return True

    def clear_estop(self, who="human", note=""):
        """解除急停。【必须显式调用】—— 自动恢复是禁止的。★ 会落盘（跨进程）"""
        with self._lock:
            was = self._estop
            self._estop = False
            self._estop_reason = ""
            self.st = {"estop": False, "reason": "", "ts": time.time(), "who": who}
        self._save_state()
        self.audit.write("estop_clear", who=who, note=note, was_active=was)
        return was

    def poll_hard_estop(self):
        """读外部急停开关的状态文件：内容为 1/true/on 视为按下。"""
        p = (self.cfg.get("estop") or {}).get("pin_file") or ""
        if not p:
            return False
        try:
            if not os.path.isfile(p):
                return False
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                v = (f.read() or "").strip().lower()
            return v in ("1", "true", "on", "yes", "pressed")
        except Exception:
            return False

    def _do_stop(self, why):
        for cb in list(self._stop_cbs):
            try:
                cb(why)
            except Exception:
                pass

    # ---- 心跳 / 断线联锁 ----
    def heartbeat(self, src="brain"):
        with self._lock:
            self._hb_last = time.time()
            self._hb_seen = True
        return True

    def heartbeat_age(self):
        with self._lock:
            return time.time() - self._hb_last

    def _wd_loop(self):
        tmo = float(self.safety.get("heartbeat_timeout_s") or 2.0)
        while not self._hb_stop:
            try:
                if self.poll_hard_estop() and not self.estop_active:
                    self.estop("hard-switch", "pin_file")
                # ★ 每轮也跟外部状态文件对齐（别的进程按了急停，本进程要停）
                self.sync_external_estop()
                if self.estop_active and not self._wd_stopped:
                    self._wd_stopped = True
                    self._do_stop("watchdog-saw-estop")
                with self._lock:
                    seen, last = self._hb_seen, self._hb_last
                age = time.time() - last
                # ★ 从来没有心跳也算断线（过了启动宽限期就停）——见 start_watchdog 的说明
                overdue = (age > tmo) if seen else (time.time() > (self._boot_ts or 0))
                if overdue and not self.estop_active:
                    self.estop("heartbeat-lost(%.1fs%s)" % (age, "" if seen else ",never-seen"), "watchdog")
            except Exception:
                pass
            time.sleep(self._wd_period)

    def start_watchdog(self, bootstrap_grace_s=None):
        """起看门狗。

        ★ 两条保守规则（2026-10-15 探针发现的不安全默认）：
          1. 看门狗一旦起来，就认为"必须有心跳"——从来没有心跳也算断线。
             理由：「第一次心跳还没来」与「大脑已经死了」在机器人眼里无法区分，
             而这两种情况里只有一种能安全地继续动。
          2. 宽限期（bootstrap_grace_s，默认取 heartbeat_timeout_s，可配）只给一次，
             过了就按超时处理；这样正常启动不会误停，但"永远连不上"最终一定会停。
        """
        if self._watchdog and self._watchdog.is_alive():
            return True
        self._hb_stop = False
        self._require_hb = True
        grace = float(bootstrap_grace_s if bootstrap_grace_s is not None
                      else (self.safety.get("bootstrap_grace_s")
                            or self.safety.get("heartbeat_timeout_s") or 2.0))
        self._boot_ts = time.time() + max(0.0, grace)
        self._watchdog = threading.Thread(target=self._wd_loop, name="robot-watchdog", daemon=True)
        self._watchdog.start()
        self.audit.write("watchdog_start", period=self._wd_period,
                         timeout=self.safety.get("heartbeat_timeout_s"), grace_s=grace)
        return True

    def stop_watchdog(self):
        self._hb_stop = True
        return True

    # ---- 参数限幅 ----
    # 数值键的别名归一到规范键；未知数值键一律丢弃（复查 M3/M4）
    ALIAS_MAP = {
        "velocity": "speed", "vel": "speed", "v": "speed", "linear": "speed",
        "angular": "yaw", "omega": "yaw", "turn": "yaw_rate",
        "radius": "reach", "dist": "reach",
    }
    NUM_KEYS = ("speed", "yaw", "yaw_rate", "torque", "force", "duration", "reach",
                "accel", "pitch", "roll", "height", "grip", "target_x", "target_y", "target_z")
    # 允许透传的文本键（放进参数只为审计/危险词检查；不参与数值限幅）
    TEXT_KEYS = ("note", "target", "why", "label", "tag", "name", "ref")

    def clamp_params(self, action, params):
        """归一化 + 类型检查 + 限幅。返回 (新参数, 记录列表)。

        ★ 三条原则（独立复查 2026-10-15 后收紧）：
          1. 别名归一到规范键（velocity/vel/v/linear -> speed）—— 只夹 speed 等于留后门
          2. **未知数值键一律丢弃**：固件不认识 accel/pitch 时，透传等于"键盘给个没限幅的量"
          3. **类型错的已知键直接拒绝**：speed='fast' 这种不能放行，也不能静默丢掉
             （静默丢会让机器人按自己的默认值跑，而审计里什么都没有）
        """
        p = {}
        notes = []
        rejects = []
        mx_s = float(self.safety.get("max_speed_mps") or 0.6)
        mx_y = float(self.safety.get("max_yaw_rps") or 1.2)
        mx_t = float(self.safety.get("max_joint_torque") or 0.6)
        mx_d = float(self.safety.get("max_duration_s") or 30.0)
        mx_r = float(self.safety.get("max_reach_m") or 0.55)

        def _lim(key, lo, hi):
            if key not in p:
                return
            try:
                v = float(p[key])
            except Exception:
                rejects.append("%s=非数值(%r)" % (key, p[key]))
                p.pop(key, None)
                return
            nv = max(lo, min(hi, v))
            if abs(nv - v) > 1e-9:
                notes.append("clamped:%s:%s->%s" % (key, v, nv))
                p[key] = nv

        for k, v in (params or {}).items():
            if k in self.TEXT_KEYS:
                p[k] = v if isinstance(v, str) else str(v)
                continue
            canon = self.ALIAS_MAP.get(k, k)
            if canon in self.NUM_KEYS:
                try:
                    p[canon] = float(v)
                except Exception:
                    rejects.append("%s=非数值(%r)" % (canon, v))
                if canon != k and canon in p:
                    notes.append("normalized:%s->%s" % (k, canon))
            else:
                notes.append("dropped-unknown:%s" % k)

        _lim("speed", 0.0, mx_s)
        _lim("yaw", -mx_y, mx_y)
        _lim("yaw_rate", -mx_y, mx_y)
        _lim("torque", 0.0, mx_t)
        _lim("force", 0.0, mx_t)
        _lim("grip", 0.0, 1.0)
        _lim("duration", 0.0, mx_d)
        _lim("reach", 0.0, mx_r)
        _lim("accel", 0.0, mx_s * 4.0)          # 加速度上限按速度的量级给
        _lim("pitch", -45.0, 45.0)
        _lim("roll", -45.0, 45.0)
        _lim("height", 0.0, 1.5)
        for k in ("target_x", "target_y", "target_z"):
            _lim(k, -mx_r, mx_r)
        return p, notes, rejects

    def hard_estop_configured(self):
        """PC 端是否配了外部（硬件）急停开关的状态文件。"""
        return bool((self.cfg.get("estop") or {}).get("pin_file"))

    def hardware_estop_warning(self):
        """★ 复查 M6：不接硬件急停时，不能一声不吭地让用户以为"安全已经齐了"。

        返回一句人话（该警告时），否则返回 ""。
        注意：这条【不能】靠 PC 端代码实现 —— 真正的硬急停必须在机器人身上
        （断线/断电时 PC 说不了话）。这里只负责"把风险说出来"。
        """
        if self.hard_estop_configured():
            return ""
        if bool(self.safety.get("dry_run", True)):
            return ""      # 干跑阶段不啰嗦
        return ("★ 未配硬件急停（estop.pin_file 为空）：真机运行时断线/断电时"
                "PC 端一切联锁都失效 —— 机器人身上【必须】有物理急停开关与板载看门狗。")

    # ---- 主裁决 ----
    def check(self, robot, action, params=None, caps=None, target=None):
        """六道闸 + 急停 + 联锁体检。返回 Verdict。

        robot : 后端里那台机器人的 id（如 "mock:arm-01"）
        action: 动作名（如 "move" / "grip" / "state"）
        caps  : robot_adapter.caps() 的结果（用来做闸2/闸3）
        target: 可选，动作目标（用于未来扩展；现在只做危险词检查）
        """
        self.stats["checks"] += 1
        reasons = []
        cfg = self.cfg
        try:
            # ★ 先跟外部状态文件对齐：别的进程按了急停，本进程必须立刻知道
            self.sync_external_estop()
            # 0) 硬件急停缺失：每次进程运行只提醒一次（写审计 + 交给播报）
            if not self._hw_warned:
                w = self.hardware_estop_warning()
                if w:
                    self._hw_warned = True
                    self.audit.write("hard_estop_missing", note=w)
                    self._say(w, "on_block")
            # 0) 急停优先：任何动作都不放行（连 state 也只读，见下面特例）
            if self.estop_active:
                v = Verdict(DEC_BLOCK, action, robot, ["estop-active:%s" % self._estop_reason], params)
                return self._finish(v)

            act = (action or "").strip().lower()

            # 1) 永远禁止 / 危险词
            if act in FORBIDDEN_ACTIONS:
                return self._finish(Verdict(DEC_BLOCK, action, robot, ["forbidden-action"], params))
            # ★ 危险词只扫【文本】：参数里的 force=0.4（夹爪力度）是数值，
            #   不能因为名字里有 force 就被当成"强制/暴力"拦掉。
            #   教训：把键名也丢进危险词扫描 = 自己把自己的正常参数封掉（本模块 2026-10-15 栽过）
            text_blob = [act, str(target or "")]
            for k, v in (params or {}).items():
                if isinstance(v, bool):
                    continue
                if isinstance(v, (int, float)):
                    continue
                text_blob.append(str(v))
            blob = " ".join(text_blob).lower()
            for w in DANGER_WORDS:
                if w and w.lower() in blob:
                    return self._finish(Verdict(DEC_BLOCK, action, robot, ["danger-word:%s" % w], params))

            # 2) 闸1 白名单：机器人必须在配置里登记过
            # ★ 「登记表为空」必须拒绝，而不是跳过检查（复查 M1）：
            #   旧写法 `and robots_cfg` 意味着配置写错一层/被清空 -> 所有动作直接放行且零提示。
            #   这正是本项目历史上栽过的"静默兜底造假象"。
            robots_cfg = cfg.get("robots") or {}
            act_l = act
            if self.gates.get("whitelist", True):
                if not robots_cfg:
                    if act_l not in READONLY_ACTIONS:
                        return self._finish(Verdict(DEC_BLOCK, action, robot,
                                                    ["no-robot-registered(robots 段为空，写动作一律拒绝)"], params))
                else:
                    if robot not in robots_cfg:
                        return self._finish(Verdict(DEC_BLOCK, action, robot, ["robot-not-whitelisted"], params))
                    rc0 = robots_cfg.get(robot) or {}
                    if rc0.get("enabled") is False:
                        return self._finish(Verdict(DEC_BLOCK, action, robot, ["robot-disabled"], params))

            # 3) 闸2 默认只读：非只读动作必须由 caps/配置显式允许
            readonly = act in READONLY_ACTIONS
            writing = not readonly
            if writing and self.gates.get("default_readonly", True):
                allowed_actions = set()
                if caps:
                    allowed_actions |= set(caps.get("actions") or [])
                rc = (robots_cfg.get(robot) or {}) if robots_cfg else {}
                allowed_actions |= set(rc.get("allow") or [])
                if act not in allowed_actions:
                    return self._finish(Verdict(DEC_BLOCK, action, robot,
                                                ["readonly-gate:action-not-allowed"], params))
                if rc.get("readonly") is True:
                    return self._finish(Verdict(DEC_BLOCK, action, robot, ["robot-readonly"], params))

            # 4) 闸3 动作白名单（与闸2互补：这里查全局禁用名单）
            if self.gates.get("action_whitelist", True):
                sks = cfg.get("skills") or {}
                if sks.get(action) is False or sks.get("%s.%s" % (robot, action)) is False:
                    return self._finish(Verdict(DEC_BLOCK, action, robot, ["action-disabled-in-config"], params))

            # 5) 闸5 前台守卫（机器人默认关；PC 上跑时才有意义）
            if self.gates.get("foreground") and writing:
                try:
                    import foreground_guard as fg       # 可选依赖，不在就跳过
                    if not fg.is_foreground_ok():
                        return self._finish(Verdict(DEC_BLOCK, action, robot, ["foreground-guard"], params))
                except Exception:
                    pass

            # 6) 参数：归一化 + 类型检查 + 限幅（类型错 = 拒绝，不确定就停）
            params2, notes, rejects = self.clamp_params(action, params)
            # ★ 用结构化字段带走明细（上层/自测可以按类取，不必去拆字符串）
            self._last_param_notes = notes
            self._last_param_rejects = rejects
            if rejects:
                return self._finish(Verdict(DEC_BLOCK, action, robot,
                                            ["bad-param:" + ",".join(rejects)], params2))
            reasons.extend(notes)

            # 7) 电量
            mb = float(self.safety.get("min_battery_pct") or 0)
            if writing and mb > 0 and caps and caps.get("battery_pct") is not None:
                try:
                    if float(caps["battery_pct"]) < mb and act not in ("dock", "stop", "charge"):
                        return self._finish(Verdict(DEC_BLOCK, action, robot,
                                                    ["battery-low:%.0f%%<%.0f%%" % (float(caps["battery_pct"]), mb)], params2))
                except Exception:
                    pass

            # 8) 干跑：默认 True —— 只放行到"不下发"这一步
            if writing and self.safety.get("dry_run", True) and not (self.gates.get("_force_real")):
                reasons.append("dry-run")
                return self._finish(Verdict(DEC_DRYRUN, action, robot, reasons, params2))

            return self._finish(Verdict(DEC_ALLOW, action, robot, reasons, params2))
        except Exception as e:
            # ★ 安全层自己出错 = 一律拦截（绝不"因为报错所以放行"）
            return self._finish(Verdict(DEC_BLOCK, action, robot, ["safety-error:%s:%s" % (type(e).__name__, e)], params))

    def _finish(self, v):
        """裁决收尾：统计 + 审计。★ 整个收尾过程不许向上抛（复查 M7）。

        历史教训：审计写失败/字段不可序列化时，旧实现让 check() 直接抛异常，
        调用方拿到的是崩溃而不是"拦截"；更糟的一种是动作放行了但审计 0 行。
        所以这里全程包 try，出任何事都降级成 DEC_BLOCK（宁可拦住）。
        """
        try:
            if v.decision == DEC_ALLOW:
                self.stats["allowed"] += 1
            elif v.decision == DEC_DRYRUN:
                self.stats["dryrun"] += 1
            else:
                self.stats["blocked"] += 1
        except Exception:
            pass
        # 把参数明细挂到裁决上，方便自测/上层按类判断（字符串里塞不下结构化信息）
        try:
            v.params_notes = list(getattr(self, "_last_param_notes", []) or [])
            v.params_rejects = list(getattr(self, "_last_param_rejects", []) or [])
        except Exception:
            pass
        try:
            if (self.gates or {}).get("audit", True):
                self.audit.write("verdict", **v.to_dict())
        except Exception as e:
            try:
                v.decision = DEC_BLOCK
                v.reasons = list(v.reasons or []) + ["audit-fail:%s" % type(e).__name__]
            except Exception:
                pass
        return v

    # ---- 便捷：状态回报（供 UI/球显示）----
    def snapshot(self):
        with self._lock:
            return {
                "estop": self._estop,
                "estop_reason": self._estop_reason,
                "estop_ts": self._estop_ts,
                "heartbeat_age_s": round(self.heartbeat_age(), 2),
                "heartbeat_seen": self._hb_seen,
                "watchdog_alive": bool(self._watchdog and self._watchdog.is_alive()),
                "dry_run": bool(self.safety.get("dry_run", True)),
                "stats": dict(self.stats),
                "limits": {
                    "max_speed_mps": self.safety.get("max_speed_mps"),
                    "max_yaw_rps": self.safety.get("max_yaw_rps"),
                    "max_torque": self.safety.get("max_joint_torque"),
                    "max_reach_m": self.safety.get("max_reach_m"),
                    "max_duration_s": self.safety.get("max_duration_s"),
                    "min_battery_pct": self.safety.get("min_battery_pct"),
                    "heartbeat_timeout_s": self.safety.get("heartbeat_timeout_s"),
                },
                "hardware_estop": self.hard_estop_configured(),
                "hardware_estop_warning": self.hardware_estop_warning(),
                "config_errors": list(rr.CONF_ERRORS),
            }
