# -*- coding: utf-8 -*-
"""robot_ros2.py -- ROS2 后端的机器人适配器（骨架 + 明确的手工映射）

【现状】本机【没有】ROS2（实测：ros2 / ros1 命令都不存在）。所以这个文件是：
    · 一份可运行的"接线表"实现：所有动作都翻译成 ros2 CLI 命令
    · 装好 ROS2 之后，只要把 topics 段填对，不用改本文件
    · 没装 ROS2 时：discover() 返回空并说明原因，act() 返回明确错误，绝不抛

【为什么走 `ros2 topic pub` 而不是 import rclpy】
    rclpy 需要 source 过 ROS2 环境（且与 PC 的 Python 版本耦合）。
    用 CLI 的好处：本文件在【任何】Python 下都能 import、都能被自检；
    真正跑起来时只需子进程调用 ros2，环境由 source 命令提供。

【接线表（在 robot.json 的 backend_opts.topics 里配）】
    {
      "cmd_topic":  "/robot/cmd",       # std_msgs/String 或自定义；发布 JSON 文本
      "state_topic": "/robot/state",    # 订阅；ros2 topic echo --once
      "stop_topic":  "/robot/stop",     # ★ 急停尽量用独立 topic，避免与普通命令抢带宽
      "msg_type":    "std_msgs/msg/String",
      "ros_setup":   ""                 # 例：C:\\dev\\ros2_humble\\local_setup.bat
    }

【★ 真机纪律（写在这里，免得以后忘）】
    · 急停链路【不要】只依赖 WiFi/ROS2：机器人身上必须有硬急停与板载看门狗
    · cmd 与 stop 用不同 topic，避免"命令队列里堆着一堆 move，stop 排在最后"
    · 上线前用 ros2 topic hz 测一次实际吞吐与延迟；延迟 >200ms 时把安全层的
      max_speed_mps 调低（本模块只在 caps 里报实测值，不替你做决定）
"""
import json
import shutil
import subprocess
import time

import robot_adapters as ra
import robot_root as rr

CREATE_NO_WINDOW = 0x08000000


def ros2_available():
    """ROS2 在不在。返回 (bool, 说明)。"""
    exe = shutil.which("ros2")
    if not exe:
        return False, "PATH 里没有 ros2 命令（本机未安装 ROS2）"
    return True, exe


class Ros2RobotAdapter(ra.RobotAdapter):
    name = "ros2"
    protocol = "ROS2（标准机器人中间件）"
    dof = 0

    ACTIONS = ["move", "turn", "walk", "stop", "grip", "release", "home", "stand", "sit", "look", "goto"]

    def __init__(self, topics=None, msg_type="std_msgs/msg/String", ros_setup="",
                 dev_id="ros2:robot-01", timeout=3.0, **_ignored):
        t = topics or {}
        self.cmd_topic = t.get("cmd_topic", "/robot/cmd")
        self.state_topic = t.get("state_topic", "/robot/state")
        self.stop_topic = t.get("stop_topic", "/robot/stop")
        self.msg_type = msg_type
        self.ros_setup = ros_setup
        self.dev_id = dev_id
        self.timeout = float(timeout or 3.0)
        self._cached_state = {}
        self._last_error = ""

    # ---- 底层：跑一条 ros2 命令 ----
    def _run(self, args, timeout=None):
        ok, info = ros2_available()
        if not ok:
            self._last_error = info
            return {"ok": False, "error": info, "ros2_missing": True}
        cmd = list(args)
        if self.ros_setup:
            # 用 cmd 先 source 再执行（Windows 上 source 是 call）
            q = " ".join(['"%s"' % c if " " in c else c for c in cmd])
            shell_cmd = 'call "%s" && %s' % (self.ros_setup, q)
            cmd = ["cmd", "/c", shell_cmd]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=float(timeout or self.timeout),
                               encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
            if r.returncode != 0:
                return {"ok": False, "error": (r.stderr or r.stdout or "ros2 退出码 %s" % r.returncode)[:200]}
            return {"ok": True, "out": r.stdout or ""}
        except Exception as e:
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}

    def _publish(self, topic, payload, timeout=None):
        data = json.dumps(payload, ensure_ascii=False)
        return self._run(["ros2", "topic", "pub", "--once", topic, self.msg_type, "{data: '%s'}" % data],
                         timeout=timeout)

    def _echo_once(self, topic, timeout=None):
        r = self._run(["ros2", "topic", "echo", "--once", topic], timeout=timeout)
        if not r.get("ok"):
            return {"ok": False, "error": r.get("error")}
        txt = (r.get("out") or "").strip()
        # ros2 topic echo 输出是 YAML，里面 data: 后面才是我们发的 JSON
        for line in txt.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                raw = line.split("data:", 1)[1].strip().strip("'\"")
                try:
                    return {"ok": True, "result": json.loads(raw)}
                except Exception:
                    return {"ok": False, "error": "状态不是 JSON: %s" % raw[:120]}
        return {"ok": False, "error": "没解析到 data 字段"}

    # ---- 契约 ----
    def caps(self):
        c = super().caps()
        lim = rr.CONF.get("safety") or {}
        c.update({"kind": "ros2", "actions": list(self.ACTIONS), "discovery": "ros2",
                  "limits": {"max_speed_mps": lim.get("max_speed_mps"), "max_yaw_rps": lim.get("max_yaw_rps")},
                  "battery_pct": (self._cached_state or {}).get("battery_pct")})
        return c

    def discover(self):
        ok, info = ros2_available()
        if not ok:
            return [{"id": self.dev_id, "name": "ROS2 机器人（不可用）", "kind": "ros2",
                     "online": False, "error": info, "topics": self.cmd_topic}]
        return [{"id": self.dev_id, "name": "ROS2 机器人 (%s)" % self.cmd_topic, "kind": "ros2",
                 "online": True, "topics": {"cmd": self.cmd_topic, "state": self.state_topic,
                                            "stop": self.stop_topic}}]

    def state(self, dev_id):
        r = self._echo_once(self.state_topic)
        if r.get("ok"):
            self._cached_state = r.get("result") or {}
        return r

    def act(self, dev_id, action, params=None):
        payload = {"cmd": action}
        payload.update(params or {})
        topic = self.stop_topic if action == "stop" else self.cmd_topic
        r = self._publish(topic, payload)
        if not r.get("ok"):
            return {"ok": False, "error": r.get("error")}
        return {"ok": True, "result": {"published": action, "topic": topic}}

    def stop(self, dev_id=None, why=""):
        r = self._publish(self.stop_topic, {"cmd": "stop", "why": why or "safety"}, timeout=1.0)
        return {"ok": bool(r.get("ok")), "error": r.get("error"), "why": why}


ra.register(Ros2RobotAdapter(), "ros2")
