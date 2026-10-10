# -*- coding: utf-8 -*-
import json
import os
import socket
import subprocess
import sys
import time
import fairy_root


CREATE_NO_WINDOW = fairy_root.CREATE_NO_WINDOW
NOWIN_FLAGS = fairy_root.NOWIN_FLAGS


for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass
del _s

T = os.path.dirname(os.path.abspath(__file__))
LOG = fairy_root.BALL_LOG_DIR
PY = sys.executable
CONF = fairy_root.CONFIG


NO_DETACH = "--no-detach"
FROM_DETACH = NO_DETACH in sys.argv
if FROM_DETACH:
    try:
        sys.argv.remove(NO_DETACH)
    except ValueError:
        pass

def in_dsh():
    return any(os.environ.get(k) for k in
               ("DSH_SESSION_ID", "DSH_PROFILE_DIR", "DSH_SHELL", "DSH_WEB_URL"))


DEFAULT_CONF = {
    "_说明": "Fairy 统一配置：所有组件的开关都在这。改完保存即可（多数项下次启动生效）。",


    "components": {"ball": True, "guard": True,
                   "tts": False,

                   "host": True},


    "silent": {"indextts_guard.py": True, "indextts_start.py": True,
               "bootstrap.py": True, "fairy_host.py": True, "fairy_ball.py": True},


    "endpoints": {
        "dsh_base": "http://127.0.0.1:19387",
        "local_base": "http://127.0.0.1:19388",
        "indextts_url": "http://127.0.0.1:9881/tts",
        "ref_audio": "<FAIRY_ROOT>\\voice\\index_voices\\fairy.wav",
        "prefer": "auto",
    },
    "ball": {"speak": True, "bubble_seconds": 4, "brief_seconds": 18},


    "advisor": {"enabled": True, "observe_min": 5, "min_gap_min": 10},
    "balance": {"warn_yuan": 30, "alert_yuan": 10},
    "news": {"count": 0, "keyword_count": 0},
}

def load_conf():
    try:
        if os.path.exists(CONF):
            with open(CONF, encoding="utf-8") as f:
                c = json.load(f)
            for k, v in DEFAULT_CONF.items():
                if k not in c:
                    c[k] = v
                elif isinstance(v, dict) and isinstance(c.get(k), dict):
                    for kk, vv in v.items():
                        c[k].setdefault(kk, vv)
            return c
    except Exception as e:
        print("[fairy] 读配置失败（用默认值）: %s: %s" % (type(e).__name__, e), flush=True)
    return json.loads(json.dumps(DEFAULT_CONF))

def save_conf(c):
    try:
        tmp = CONF + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONF)
        return True
    except Exception as e:
        print("[fairy] 写配置失败: %s: %s" % (type(e).__name__, e), flush=True)
        return False


COMPONENTS = {


    "ball": {"script": "fairy_ball.py", "match": "fairy_ball", "silent": True,
             "desc": "桌面球 —— Fairy 的门面（球+进度条+语音+气泡+闪避）"},


    "tts": {"script": "indextts_start.py", "match": "indextts_server",
            "desc": "Fairy 音色合成 IndexTTS（GPU 克隆）", "wait_s": 75},


    "guard": {"script": "indextts_guard.py", "match": "indextts_guard.py",
              "desc": "Fairy 音色保活守护（盯 9881，挂了自动拉起）", "wait_s": 6,
              "silent": True},


    "host": {"script": "fairy_host.py", "match": "fairy_host.py",
             "desc": "Fairy 本地服务（19388，DSH 不在时的备胎）",
             "wait_s": 3, "silent": True},
}

def _ps(cmd, t=60):
    try:


        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, timeout=t,
                           creationflags=CREATE_NO_WINDOW)
        return (r.stdout or b"").decode("utf-8", "replace").strip()
    except Exception as e:
        print("[fairy] PowerShell 失败: %s" % e, flush=True)
        return ""

def pids_of(match):
    return [x.strip() for x in _ps(
        '@(Get-CimInstance Win32_Process -Filter "Name=\'python.exe\' or Name=\'pythonw.exe\'"'
        ' | Where-Object {$_.CommandLine -like \'*%s*\'}).ProcessId' % match
    ).splitlines() if x.strip().isdigit()]

def start_one(name, conf=None):
    comp = COMPONENTS.get(name)
    if not comp:
        print("✗ 未知组件: %s" % name)
        return 1
    p = os.path.join(T, comp["script"])
    if not os.path.exists(p):
        print("✗ %s 的脚本还不存在: %s" % (name, comp["script"]))
        return 1
    have = pids_of(comp["match"])
    if have:
        print("· %s 已经在跑（PID %s）" % (name, " ".join(have)))
        return 0
    os.makedirs(LOG, exist_ok=True)
    lf = open(os.path.join(LOG, comp["script"] + ".log"), "ab")
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"


    exe = PY
    _silent = (conf or {}).get("silent") or {}
    _want_silent = (bool(comp.get("silent"))
                    or bool(_silent.get(name))
                    or bool(_silent.get(comp["script"])))
    if _want_silent:
        exe = fairy_root.pythonw_of(PY)

    subprocess.Popen([exe, p], stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                     creationflags=NOWIN_FLAGS, env=env)

    time.sleep(float(comp.get("wait_s", 2.5)))
    have = pids_of(comp["match"])
    if have:
        print("✓ %s 已启动（PID %s）" % (name, " ".join(have)))
        return 0
    print("✗ %s 起来后又没了 —— 看日志 %s" % (name, os.path.join(LOG, comp["script"] + ".log")))
    return 1

def stop_one(name):
    comp = COMPONENTS.get(name)
    if not comp:
        print("✗ 未知组件: %s" % name)
        return 1
    have = pids_of(comp["match"])
    if not have:
        print("· %s 本来就没在跑" % name)
        return 0
    _ps("Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | "
        "Where-Object {$_.CommandLine -like '*%s*'} | ForEach-Object {Stop-Process -Id $_.ProcessId -Force}"
        % comp["match"])
    time.sleep(1)
    left = pids_of(comp["match"])
    if left:
        print("⚠ %s 还有残留: %s" % (name, left))
        return 1
    print("✓ %s 已停（%d 个进程）" % (name, len(have)))
    return 0

def cmd_status():
    print("── Fairy 总管 ──────────────────────────────")
    print("   现在   %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    for name, comp in COMPONENTS.items():
        pids = pids_of(comp["match"])
        print("   %-5s  %-30s %s" % (name, comp["desc"][:30],
                                     ("在跑 PID %s" % " ".join(pids)) if pids else "**没在跑**"))
    for n, p in (("DSH+插件", 19387), ("Fairy本地", 19388), ("Ollama", 11434),
                 ("ComfyUI", 8188), ("IndexTTS", 9881), ("界面代理", 8081)):
        s = socket.socket()
        s.settimeout(0.6)
        ok = s.connect_ex(("127.0.0.1", p)) == 0
        s.close()
        print("   %-5s  :%-5d %s" % (n, p, "在跑" if ok else "停"))


    try:
        import fairy_endpoints as _ep
        _rep = _ep.route_report()
        _chain = " -> ".join(t["route"] for t in _rep["tts_chain"])
        print("   寻址   状态/回复=%s（来源=%s, prefer=%s）"
              % (_rep["api_base"], _rep["api_source"], _rep["prefer"]))
        print("   语音   降级链=%s" % _chain)
        print("   音色   参考音频%s %s" % ("存在 ✓" if _rep["ref_audio_exists"] else "缺失 ✗",
                                          os.path.basename(_rep["ref_audio"])))
    except Exception as e:
        print("   寻址   读不到: %s" % str(e)[:60])
    try:
        import urllib.request
        _b, _src = _ep.resolve_base()
        with urllib.request.urlopen(_b + "/api/fairy/meta", timeout=6) as r:
            j = json.loads(r.read().decode("utf-8"))
        ctx = j.get("context") or {}
        print("   我     running=%s  doing=%s  steps=%s  (接口来源=%s)"
              % (j.get("running"), j.get("doing"), j.get("steps"), _src))
        print("   上下文 %s%%  (%s/%s)  mode=%s" % (ctx.get("pct"), ctx.get("used"),
                                                   ctx.get("limit"), (j.get("mode") or {}).get("label")))
    except Exception as e:
        print("   我     读不到: %s" % str(e)[:60])
    for lbl, script, args in (("余额", "balance_watch.py", ["--json"])):
        p = os.path.join(T, script)
        if not os.path.exists(p):
            continue
        try:
            r = subprocess.run([PY, p] + args, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=40,
                               creationflags=CREATE_NO_WINDOW)
            out = (r.stdout or "").strip()
            if script == "balance_watch.py":
                j = json.loads(out)
                print("   余额   ¥%.2f（%s）" % (j["yuan"], j["level"]) if j.get("ok")
                      else "   余额   查不到: %s" % j.get("why"))
            else:
                print("   %-5s  %s" % (lbl, (out.splitlines() or ["?"])[0][:72]))
        except Exception as e:
            print("   %-5s  读取失败: %s" % (lbl, str(e)[:50]))
    return 0

CMDS = {
    "init": ("bootstrap.py", ["--full"]),
    "rescan": ("bootstrap.py", ["--full"]),
    "scan": ("bootstrap.py", ["--dry"]),
    "devices": ("device_adapters.py", []),
    "ble": ("ble_identify.py", []),
    "health": ("health_check.py", []),
    "fastcheck": ("health_check.py", ["--fast"]),
    "news": ("briefing.py", ["--news"]),
    "weather": ("briefing.py", []),
    "music": ("music_info.py", ["--selftest"]),
    "subtitle": ("video_subtitle.py", ["--run", "--translate", "--subtitle"]),
    "call": ("call_translate.py", ["--run"]),
    "cpu": ("cpu_watch.py", []),
}

def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    c, rest = a[0], a[1:]
    conf = load_conf()


    if c in ("start", "restart") and not FROM_DETACH and in_dsh():
        _det = os.path.join(T, "fairy_detach.py")
        if os.path.isfile(_det):
            print("★ 检测到我在 DSH 里运行：为避免【关掉 DSH 时把 Fairy 一起杀掉】，")
            print("  改用 fairy_detach.py 经由 WMI 重起（父进程 = WmiPrvSE，不在 DSH 树上）…")
            _rc = subprocess.call([PY, _det] + [c] + rest, cwd=T,
                                  creationflags=CREATE_NO_WINDOW)
            print("  （已转交；看结果：fairy.py status）")
            return _rc

    if c == "status":
        return cmd_status()
    if c == "config":
        if not os.path.exists(CONF):
            save_conf(conf)
            print("已生成默认配置: %s" % CONF)
        print(json.dumps(load_conf(), ensure_ascii=False, indent=2))
        return 0
    if c == "start":
        targets = rest or [k for k, v in (conf.get("components") or {}).items() if v]
        rc = 0
        for n in targets:
            rc |= start_one(n, conf)
        return rc
    if c == "stop":
        rc = 0
        for n in (rest or list(COMPONENTS.keys())):
            rc |= stop_one(n)
        return rc
    if c == "restart":

        for n in list(COMPONENTS.keys()):
            stop_one(n)
        time.sleep(0.5)
        rc = 0
        for n, v in (conf.get("components") or {}).items():
            if v:
                rc |= start_one(n, conf)
        return rc
    if c == "log":
        n = rest[0] if rest else "ball"
        comp = COMPONENTS.get(n)
        if not comp:
            print("✗ 未知组件: %s" % n)
            return 1
        p = os.path.join(LOG, comp["script"] + ".log")
        if not os.path.exists(p):
            print("（没有日志 %s）" % p)
            return 1
        with open(p, encoding="utf-8", errors="replace") as f:
            lines = f.read().strip().splitlines()
        for x in lines[-25:]:
            print("   ", x[:150])
        return 0
    if c in CMDS:
        script, args = CMDS[c]
        p = os.path.join(T, script)
        if not os.path.exists(p):
            print("✗ 找不到模块: %s" % script)
            return 1
        print("── %s ──────────────────────────────" % script)


        return subprocess.call([PY, p] + args + rest, cwd=T,
                               creationflags=CREATE_NO_WINDOW)
    print("✗ 未知命令: %s" % c)
    print("   跑 `fairy.py help` 看全部命令")
    return 2

if __name__ == "__main__":
    sys.exit(main())
