# -*- coding: utf-8 -*-
import json
import os
import subprocess
import sys
import time
import fairy_root


CREATE_NO_WINDOW = fairy_root.CREATE_NO_WINDOW

STATE = fairy_root.log("media_duck_state.json")
UNDUCK_TIMEOUT_S = 90


PS_PRELUDE = r'''
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($t, $rt) {
    $m = $asTaskGeneric.MakeGenericMethod($rt)
    $nt = $m.Invoke($null, @($t))
    $nt.Wait(-1) | Out-Null
    $nt.Result
}
function Get-Mgr {
    [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager,Windows.Media.Control,ContentType=WindowsRuntime] | Out-Null
    Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
}
'''


PAUSE_HINTS = ("cloudmusic", "qqmusic", "kugou", "kuwo", "spotify", "foobar", "musicbee",
               "aimp", "music", "netease", "网易云", "qq音乐", "酷狗", "酷我", "potplayer",
               "vlc", "mpv", "wmplayer", "groove")
VOLUME_HINTS = ("zenlesszonezero", "genshinimpact", "starrail", "yuanshen",
                "chrome", "msedge", "firefox", "bilibili", "哔哩哔哩",
                "obs", "potplayer64", "直播", "live", "game", "steam")

def ps(script, t=60):
    full = PS_PRELUDE + "\n" + script
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", full],
                           capture_output=True, timeout=t,
                           creationflags=CREATE_NO_WINDOW)
        return (r.stdout or b"").decode("utf-8", "replace").strip(), \
               (r.stderr or b"").decode("gbk", "replace").strip()
    except Exception as e:
        return "", "%s: %s" % (type(e).__name__, e)


_call_mod = None
_call_load_tried = False
_warned_once = set()

_CALL_TTL_S = 3.0
_call_cache = (0.0, False, "")

def _warn_once(msg):
    if msg in _warned_once:
        return
    _warned_once.add(msg)
    print("[media_duck] %s" % msg, flush=True)

def _load_call_module():
    global _call_mod, _call_load_tried
    if _call_mod is not None or _call_load_tried:
        return _call_mod
    _call_load_tried = True
    try:
        import importlib.util

        if "call_translate" in sys.modules:
            m = sys.modules["call_translate"]
            if hasattr(m, "is_call_active"):
                _call_mod = m
                return _call_mod
        fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "call_translate.py")
        if not os.path.exists(fp):
            _warn_once("call_translate.py 不存在 -> 通话检测不可用（降级：不会误判为通话）")
            return None
        s = importlib.util.spec_from_file_location("call_translate", fp)
        m = importlib.util.module_from_spec(s)
        sys.modules["call_translate"] = m
        s.loader.exec_module(m)
        if not hasattr(m, "is_call_active"):
            _warn_once("call_translate 没有 is_call_active() -> 通话检测不可用（降级）")
            return None
        _call_mod = m
    except Exception as e:
        _warn_once("加载 call_translate 失败 -> 通话检测降级: %s" % str(e)[:120])
        _call_mod = None
    return _call_mod

def is_call_now():
    global _call_cache
    ts, act, why = _call_cache
    if ts and (time.time() - ts) < _CALL_TTL_S:
        return act, why
    mod = _load_call_module()
    if mod is None:
        _call_cache = (time.time(), False, "")
        return False, "通话检测不可用"
    try:
        r = mod.is_call_active()

        if isinstance(r, (tuple, list)) and len(r) >= 3:
            a, w, conf = r[0], r[1], r[2]
        elif isinstance(r, dict):
            a, w, conf = r.get("active"), r.get("why"), r.get("conf")
        else:
            a, w, conf = bool(r), "", "high" if r else "low"
        if a and conf == "high":
            res = (True, str(w or "高置信通话信号"))
        else:
            res = (False, "")


        _call_cache = (time.time(), res[0], res[1])
        return res
    except Exception as e:
        _warn_once("call_translate.is_call_active() 出错 -> 本次当作非通话: %s" % str(e)[:100])
        _call_cache = (time.time(), False, "")
        return False, ""

def classify(app="", title=""):
    s = (str(app) + " " + str(title)).lower()

    if is_call_now()[0]:
        return "call"

    for k in VOLUME_HINTS:
        if k in s:
            return "volume"
    for k in PAUSE_HINTS:
        if k in s:
            return "pause"
    return "ignore"

def snapshot():
    script = r'''
$mgr = Get-Mgr
$out = @()
foreach ($s in $mgr.GetSessions()) {
    $p = $s.SourceAppUserModelId
    $t = ""
    try { $t = (Await ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])).Title } catch {}
    $out += [PSCustomObject]@{
        app = $p
        title = $t
        status = "$($s.GetPlaybackInfo().PlaybackStatus)"
        can_pause = $s.GetPlaybackInfo().Controls.IsPauseEnabled
        can_play  = $s.GetPlaybackInfo().Controls.IsPlayEnabled
    }
}
if ($out.Count -eq 0) { "[]" } else { $out | ConvertTo-Json -Compress -Depth 4 }
'''
    out, err = ps(script, 60)
    if not out:
        return {"ok": False, "error": err[:200] or "no output", "sessions": []}
    try:
        j = json.loads(out)
        if isinstance(j, dict):
            j = [j]
        return {"ok": True, "sessions": j}
    except Exception as e:
        return {"ok": False, "error": "parse: %s / %s" % (str(e)[:60], out[:120]), "sessions": []}

def _ctrl(action):
    method = {"pause": "TryPauseAsync", "play": "TryPlayAsync", "toggle": "TryTogglePlayPauseAsync"}[action]
    script = r'''
$mgr = Get-Mgr
$s = $mgr.GetCurrentSession()
if (-not $s) { Write-Output "NOSESSION"; exit }
$r = Await ($s.%s()) ([System.Boolean])
Write-Output ("OK:" + $r)
''' % method
    out, err = ps(script, 45)
    return out, err

def duck(reason="user_ask", mode="auto"):

    if mode == "auto" and is_call_now()[0]:
        return {"ducked": False, "why": "通话中：不让位、也不出声", "want": "silent"}

    snap = snapshot()
    if not snap.get("ok"):
        return {"ducked": False, "why": "snapshot 失败: %s" % snap.get("error")}
    playing = [s for s in snap.get("sessions") or []
               if "Playing" in str(s.get("status"))]
    if not playing:
        return {"ducked": False, "why": "当前没有正在播放的媒体"}

    s0 = playing[0]
    how = mode if mode != "auto" else classify(s0.get("app"), s0.get("title"))
    if how == "call":

        return {"ducked": False, "why": "通话中：不让位、也不出声", "app": s0.get("app"),
                "want": "silent"}
    if how == "ignore":
        return {"ducked": False, "why": "分类为 ignore（不动它）", "app": s0.get("app")}
    if how == "volume":
        return {"ducked": False, "why": "该走音量闪避（volume_duck.py）", "app": s0.get("app"),
                "want": "volume"}
    if not s0.get("can_pause"):
        return {"ducked": False, "why": "该会话不允许暂停", "app": s0.get("app")}

    out, err = _ctrl("pause")
    ok = out.startswith("OK:True")
    tok = {
        "ducked": ok,
        "by_me": ok,
        "app": s0.get("app"),
        "title": s0.get("title"),
        "reason": reason,
        "at": time.time(),
        "deadline": time.time() + UNDUCK_TIMEOUT_S,
        "raw": out or err[:120],
    }
    _save_state(tok)
    return tok

def unduck(token=None, force=False):
    tok = token or _load_state()
    if not tok or not tok.get("ducked"):
        return {"ok": True, "restored": False, "why": "没有需要恢复的（不是我暂停的，或本就没暂停）"}
    if not tok.get("by_me"):
        return {"ok": True, "restored": False, "why": "不是我暂停的，不动"}


    snap = snapshot()
    cur = None
    for s in (snap.get("sessions") or []):
        if s.get("app") == tok.get("app"):
            cur = s
            break
    if cur is None:


        _save_state({})
        return {"ok": True, "restored": False, "why": "原会话已消失，不盲播",
                "app": tok.get("app")}
    if not force and "Paused" not in str(cur.get("status")):
        return {"ok": True, "restored": False,
                "why": "用户已经自己操作过（当前状态 %s），不覆盖" % cur.get("status")}

    out, err = _ctrl("play")
    ok = out.startswith("OK:True")
    _save_state({})
    return {"ok": ok, "restored": ok, "raw": out or err[:120], "app": tok.get("app"),
            "forced": bool(force)}

def tick():
    tok = _load_state()
    if tok and tok.get("ducked") and tok.get("by_me") and time.time() > (tok.get("deadline") or 0):
        r = unduck(tok, force=True)
        r["forced_by_timeout"] = True
        return r
    return {"ok": True, "restored": False, "why": "无需兜底"}

def _save_state(d):
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        tmp = STATE + ".tmp"
        json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, STATE)
    except Exception as e:
        _warn_once("写状态失败（恢复令牌可能丢失）: %s" % str(e)[:110])

def _load_state():
    try:
        if os.path.exists(STATE):
            return json.load(open(STATE, encoding="utf-8"))
    except Exception as e:
        _warn_once("读状态失败（按 无令牌 处理）: %s" % str(e)[:110])
    return {}


def selftest():
    ok = fail = 0

    def chk(name, cond, extra=""):
        nonlocal ok, fail
        if cond:
            ok += 1
            print("  ✓ %s" % name)
        else:
            fail += 1
            print("  ✗ %s  %s" % (name, extra))

    print("=== 自测（不依赖真实播放器）===")

    chk("游戏 -> volume", classify("ZenlessZoneZero.exe", "绝区零") == "volume")
    chk("浏览器 -> volume", classify("chrome.exe", "B站直播") == "volume")
    chk("网易云 -> pause", classify("cloudmusic.exe", "某首歌") == "pause")
    chk("QQ音乐 -> pause", classify("QQMusic.exe", "") == "pause")
    chk("不认识 -> ignore", classify("weird.exe", "") == "ignore")

    s = snapshot()
    chk("snapshot 不抛异常", isinstance(s, dict))
    chk("snapshot 有 sessions 字段", "sessions" in s)
    print("     实况: ok=%s sessions=%d %s" % (s.get("ok"), len(s.get("sessions") or []),
                                              (s.get("error") or "")[:60]))

    d = duck("selftest")
    chk("duck 无会话时安全返回", isinstance(d, dict) and d.get("ducked") in (True, False))
    print("     duck: %s" % json.dumps({k: d.get(k) for k in ("ducked", "why", "app")}, ensure_ascii=False))

    r = unduck({"ducked": True, "by_me": False, "app": "x"})
    chk("规矩2 只恢复自己暂停的", r.get("restored") is False)

    r = unduck({})
    chk("规矩2 无凭证不乱播", r.get("restored") is False)

    old = {"ducked": True, "by_me": True, "app": "x", "deadline": time.time() - 5}
    _save_state(old)
    t = tick()
    chk("规矩4 超时兜底触发", t.get("forced_by_timeout") is True or t.get("restored") is not None,
        "got=%s" % t.get("why"))
    _save_state({})
    print("\n  %d 通过 / %d 失败" % (ok, fail))
    return 0 if fail == 0 else 1

if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    if "--snapshot" in sys.argv:
        print(json.dumps(snapshot(), ensure_ascii=False, indent=1))
    elif "--pause" in sys.argv:
        print(_ctrl("pause"))
    elif "--play" in sys.argv:
        print(_ctrl("play"))
    elif "--duck" in sys.argv:
        print(json.dumps(duck("manual"), ensure_ascii=False, indent=1))
    elif "--unduck" in sys.argv:
        print(json.dumps(unduck(), ensure_ascii=False, indent=1))
    elif "--tick" in sys.argv:
        print(json.dumps(tick(), ensure_ascii=False, indent=1))
    else:
        print(__doc__)
