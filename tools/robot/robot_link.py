# -*- coding: utf-8 -*-
"""robot_link.py -- 有线/无线链路（JSON 行协议）的公共实现

【为什么要单独一层】串口（USB/星闪）、TCP、WebSocket 上跑的是同一套协议：
    一行一个 JSON，UTF-8，\\n 结尾
    PC -> 机器人: {"cmd":"move","speed":0.3,"duration":1.0,"seq":12}
    机器人 -> PC: {"ok":true,"seq":12,"result":{...}}
    机器人主动上报: {"event":"state","battery_pct":82}   （没有 seq）
所以"怎么连"（传输）与"怎么说话"（协议）分开写，两边都能单独测。

【超时的语义】★ 重要
    · 命令超时（timeout）≠ 机器人没执行。所以超时一律返回 {"ok":False,"timeout":True}
    · 上层（robot_adapters.RobotGateway）在收到失败时会立刻再发一次 stop
      —— 因为"不知道它现在什么状态"本身就是危险状态

【本模块不 import 安全层】它是纯传输，不做判断。
"""
import json
import socket
import threading
import time


class Link:
    """传输接口。实现 open/close/write_line/read_line。"""

    def open(self):
        raise NotImplementedError

    def close(self):
        pass

    def write_line(self, text):
        raise NotImplementedError

    def read_line(self, timeout=3.0):
        raise NotImplementedError


class JsonLineChannel:
    """把 Link 变成"发命令 / 收回复"的通道。线程安全。"""

    def __init__(self, link, default_timeout=3.0):
        self.link = link
        self.timeout = float(default_timeout or 3.0)
        self._lock = threading.RLock()
        self._seq = 0
        self.events = []            # 机器人主动上报的事件（不带 seq 的行）
        self.errors = []

    def _next_seq(self):
        self._seq += 1
        return self._seq

    def send(self, cmd, timeout=None, **params):
        """发一条命令，等一条带 seq 的回复。返回 dict（不抛）。"""
        tmo = float(timeout or self.timeout)
        with self._lock:
            seq = self._next_seq()
            msg = {"cmd": cmd, "seq": seq}
            msg.update(params or {})
            try:
                self.link.open()
                self.link.write_line(json.dumps(msg, ensure_ascii=False))
            except Exception as e:
                err = {"ok": False, "error": "链路写失败: %s: %s" % (type(e).__name__, e), "seq": seq}
                self.errors.append(err)
                return err
            deadline = time.time() + tmo
            while time.time() < deadline:
                try:
                    line = self.link.read_line(timeout=max(0.05, deadline - time.time()))
                except Exception as e:
                    err = {"ok": False, "error": "链路读失败: %s: %s" % (type(e).__name__, e), "seq": seq}
                    self.errors.append(err)
                    return err
                if not line:
                    continue
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    self.errors.append({"ok": False, "error": "收到的不是 JSON: %s" % line[:80], "seq": seq})
                    continue
                if not isinstance(obj, dict):
                    continue
                if "event" in obj and "seq" not in obj:
                    self.events.append(obj)          # 主动上报，先存着
                    continue
                if obj.get("seq") != seq:
                    # 迟到的回复：丢弃但不报错（真机上很常见）
                    continue
                return obj
            err = {"ok": False, "error": "命令超时（%.1fs）—— 注意：超时≠没执行，上层必须再发 stop" % tmo,
                   "timeout": True, "seq": seq}
            self.errors.append(err)
            return err

    def drain_events(self):
        out = self.events[:]
        self.events = []
        return out


# ---------------------------------------------------------------------------
# 传输实现
# ---------------------------------------------------------------------------
class SerialLink(Link):
    """串口。需要 pyserial（本机已装）。"""

    def __init__(self, port=None, baud=115200, read_timeout=0.2):
        self.port = port
        self.baud = int(baud or 115200)
        self.read_timeout = float(read_timeout)
        self._ser = None

    def _find_port(self):
        import serial.tools.list_ports as lp
        ports = [(x.device, x.description or "") for x in lp.comports()]
        if not ports:
            raise RuntimeError("没找到串口设备（板子插上了吗？驱动装了吗？）")
        for dev, desc in ports:
            if any(k in desc for k in ("CH34", "CP210", "FTDI", "USB-SERIAL", "USB Serial", "Silicon", "Star")):
                return dev
        return ports[0][0]

    def open(self):
        if self._ser and getattr(self._ser, "is_open", False):
            return self._ser
        import serial
        port = self.port or self._find_port()
        self._ser = serial.Serial(port, self.baud, timeout=self.read_timeout)
        return self._ser

    def close(self):
        try:
            if self._ser:
                self._ser.close()
        except Exception:
            pass
        self._ser = None

    def write_line(self, text):
        ser = self.open()
        ser.write((text + "\n").encode("utf-8"))
        ser.flush()

    def read_line(self, timeout=3.0):
        ser = self.open()
        old = ser.timeout
        try:
            ser.timeout = max(0.05, float(timeout))
            raw = ser.readline()
        finally:
            try:
                ser.timeout = old
            except Exception:
                pass
        return raw.decode("utf-8", "replace") if raw else ""


class TcpLink(Link):
    """TCP（机器人自带 WiFi 模块时最常用）。"""

    def __init__(self, host="127.0.0.1", port=9000, connect_timeout=3.0):
        self.host = host
        self.port = int(port or 9000)
        self.connect_timeout = float(connect_timeout)
        self._sock = None
        self._buf = b""

    def open(self):
        if self._sock:
            return self._sock
        s = socket.create_connection((self.host, self.port), timeout=self.connect_timeout)
        s.settimeout(0.2)
        self._sock = s
        self._buf = b""
        return s

    def close(self):
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        self._sock = None
        self._buf = b""

    def write_line(self, text):
        s = self.open()
        s.sendall((text + "\n").encode("utf-8"))

    def read_line(self, timeout=3.0):
        s = self.open()
        deadline = time.time() + float(timeout)
        while time.time() < deadline:
            if b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                return line.decode("utf-8", "replace")
            try:
                chunk = s.recv(4096)
            except socket.timeout:
                continue
            except Exception:
                raise
            if not chunk:
                raise RuntimeError("对端关闭了连接")
            self._buf += chunk
        return ""


class LoopbackLink(Link):
    """回环链路：不需要任何硬件，用来测协议本身（自测用）。

    按脚本回应：responses 是一个 dict，{"move": {"ok": True, ...}}
    """

    def __init__(self, responses=None, delay_s=0.0, drop_reply=False):
        self.responses = responses or {}
        self.delay_s = float(delay_s or 0.0)
        self.drop_reply = bool(drop_reply)
        self.sent = []
        self.opened = False

    def open(self):
        self.opened = True
        return True

    def close(self):
        self.opened = False

    def write_line(self, text):
        self.sent.append(text)

    def read_line(self, timeout=3.0):
        if self.drop_reply:
            time.sleep(min(0.05, float(timeout)))
            return ""
        last = json.loads(self.sent[-1]) if self.sent else {}
        cmd = last.get("cmd", "")
        seq = last.get("seq")
        if self.delay_s:
            time.sleep(self.delay_s)
        r = {"ok": True, "seq": seq, "result": {"echo": cmd}}
        r.update(self.responses.get(cmd) or {})
        r["seq"] = seq
        return json.dumps(r, ensure_ascii=False) + "\n"
