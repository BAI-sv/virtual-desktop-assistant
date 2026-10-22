# -*- coding: utf-8 -*-
"""dsh_port.py -- 找到"DSH 的 Web 服务这会儿到底在哪个端口"

【为什么需要它】2026-10-17 实测：DSH Desktop 重启后【不再固定 19387】，而是随机端口
  （本次是 58841）。Fairy 里有 50 处写死 19387 的地址，于是：
     球的大脑 = 连不上 / TTS 插件 = 不通 / 体检报 DSH 停止 / advisor 掉线
  但 DSH 本身完全正常 —— 只是"地址变了"。

【本模块只做一件事】按优先级找出真实的 DSH Web 端口，带给缓存：
  1) 环境变量 DSH_WEB_URL（从 DSH 里起的子进程天然带着它，最准）
  2) 扫系统监听表：监听者是 DeepSeek Harness.exe 且绑定 127.0.0.1 的端口
  3) 已知候选探测（19387 优先，兼容 DSH 又变回固定端口的情况）

【纪律】
  · 绝不抛异常：找不到就返回空，让调用方走降级
  · 短 TTL 缓存（默认 10 秒）：DSH 重启换端口后最多 10 秒自愈
  · 只用标准库（不依赖 psutil）
"""
import os
import socket
import subprocess
import time
import urllib.parse

CREATE_NO_WINDOW = 0x08000000

_CACHE = {"ts": 0.0, "port": 0, "src": ""}
_TTL = 10.0

# 兼容旧固定端口的候选（DSH 哪天又改回固定端口也不会坏）
CANDIDATES = [19387, 58841, 8080, 3000]


def _from_env():
    u = (os.environ.get("DSH_WEB_URL") or "").strip()
    if not u:
        return 0
    try:
        p = urllib.parse.urlparse(u)
        if p.hostname in ("127.0.0.1", "localhost", "::1") and p.port:
            return int(p.port)
    except Exception:
        pass
    return 0


def _netstat_listeners():
    """返回 [(port, pid)]，只取 127.0.0.1/::1 上的 LISTEN。"""
    out = []
    try:
        r = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=8,
                           creationflags=CREATE_NO_WINDOW)
        for ln in (r.stdout or "").splitlines():
            parts = ln.split()
            if len(parts) < 5 or parts[0].upper() != "TCP":
                continue
            local, state, pid = parts[1], parts[3].upper(), parts[4]
            if state != "LISTENING":
                continue
            host, _, port = local.rpartition(":")
            if host not in ("127.0.0.1", "0.0.0.0", "[::1]", "::", ""):
                continue
            if not port.isdigit() or not pid.isdigit():
                continue
            out.append((int(port), int(pid)))
    except Exception:
        return []
    return out


def _dsh_pids():
    """DeepSeek Harness.exe 的全部 pid。"""
    pids = set()
    try:
        r = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq DeepSeek Harness.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=8,
            creationflags=CREATE_NO_WINDOW)
        for ln in (r.stdout or "").splitlines():
            ln = ln.strip().strip('"')
            if not ln or ln.lower().startswith("info:"):
                continue
            cols = [c.strip('"') for c in ln.split('","')]
            if len(cols) >= 2 and cols[1].isdigit():
                pids.add(int(cols[1]))
    except Exception:
        pass
    return pids


def _http_alive(port, timeout=0.6):
    """端口上是不是 DSH（返回 True 只代表有 HTTP 服务在答）。
    不判定"是不是 DSH"太严 —— 太严会误杀；调用方真正需要的是"能连的 DSH 地址"。
    """
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


def find_port(force=False):
    """返回 (port, src)。找不到返回 (0, "not-found")。"""
    now = time.time()
    if not force and _CACHE["port"] and (now - _CACHE["ts"]) < _TTL:
        return _CACHE["port"], _CACHE["src"]

    p = _from_env()
    if p and _http_alive(p):
        _CACHE.update({"ts": now, "port": p, "src": "env:DSH_WEB_URL"})
        return p, _CACHE["src"]

    # 扫监听表，挑"监听者是 DSH 进程"的那些端口
    listeners = _netstat_listeners()
    dsh = _dsh_pids()
    hits = [port for (port, pid) in listeners if pid in dsh]
    # 优先选"能连上且不在候选黑名单"的
    for port in sorted(hits):
        if _http_alive(port):
            _CACHE.update({"ts": now, "port": port, "src": "netstat:pid"})
            return port, _CACHE["src"]

    # 候选兜底（DSH 又回固定端口时）
    for port in CANDIDATES:
        if any(pp == port for (pp, _pid) in listeners) and _http_alive(port):
            _CACHE.update({"ts": now, "port": port, "src": "candidate"})
            return port, _CACHE["src"]

    _CACHE.update({"ts": now, "port": 0, "src": "not-found"})
    return 0, "not-found"


def dsh_base():
    """返回 "http://127.0.0.1:<port>"；找不到返回 ""。"""
    p, _src = find_port()
    return ("http://127.0.0.1:%d" % p) if p else ""


def dsh_url(path="/"):
    b = dsh_base()
    if not b:
        return ""
    if not path.startswith("/"):
        path = "/" + path
    return b + path


def diag():
    """排障用：一次把"怎么找到的"打全。"""
    p, src = find_port(force=True)
    return {"port": p, "src": src, "base": dsh_base(),
            "dsh_pids": sorted(_dsh_pids()),
            "env": os.environ.get("DSH_WEB_URL") or ""}


if __name__ == "__main__":
    import json
    print(json.dumps(diag(), ensure_ascii=False, indent=1))
