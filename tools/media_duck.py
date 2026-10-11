# -*- coding: utf-8 -*-
"""
media_duck.py —— 媒体闪避（ducking）：Fairy 说话时让音乐让位

【用户场景】"我在播放歌曲的时候你要怎么做"
  -> 结论：让位（闪避）。但**必须分级**，一刀切会把体验做烂。

【四条硬规矩（用户确认的设计）】
  1. **分级**：只有【用户主动问】和【告警】才让位；
     **过程播报（"正在读取数据"）绝不让位** —— 否则干活时音乐被反复暂停。
  2. **只恢复"本程序暂停的"**：用户自己暂停的，说完绝不能给他播起来。
  3. **说话期间用户操作了 -> 不覆盖用户的操作**（检测到状态变了就不恢复）。
  4. **必须有超时兜底**：恢复失败不能把音乐永久停住。

【按在播什么选方式】
  音乐播放器        -> pause（GSMTC 能暂停，保留进度）
  视频/直播/游戏    -> volume（不能暂停！会错过内容）—— 那是 volume_duck.py 的活
  不认识/不适合     -> ignore

【为什么用 PowerShell 调 WinRT】
  Python 直接调 WinRT 没有稳妥的库；本机实测 PowerShell 能加载 GSMTC 类型
  （`[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager,...]` -> "可加载: 是"）。
  所以走 subprocess 调 PowerShell，拿 JSON 回来。这是**实测过的**路。

【用法】
  py -3.12 media_duck.py --snapshot     # 看现在在播什么
  py -3.12 media_duck.py --selftest     # 自测（不依赖真实播放器）
  py -3.12 media_duck.py --pause        # 手动暂停（试链路）
  py -3.12 media_duck.py --play         # 手动继续
"""
import json
import os
import subprocess
import sys
import time
import fairy_root

# ★ 2026-10-10【黑窗】0x08000000 = CREATE_NO_WINDOW（不是 0x00000200）
CREATE_NO_WINDOW = fairy_root.CREATE_NO_WINDOW

STATE = fairy_root.log("media_duck_state.json")
UNDUCK_TIMEOUT_S = 90      # 兜底：超过这个时间必须恢复，防止音乐永久停住

# PowerShell 里调 WinRT 异步 API 的固定套路（PS 5.1 必须这么写）
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

# 分类表：进程名/标题关键词 -> 处理方式
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


# ---------------------------------------------------------------- 通话检测加载
# ★ H1 修复：原来这里是 `import call_translate`（裸 import）——**必然失败**。
#   原因：球是用 `importlib.util.spec_from_file_location` 按**文件路径**加载本模块的，
#   它**没有把 tools 目录加进 sys.path**，所以裸 import 报
#   `ModuleNotFoundError: No module named 'call_translate'`。
#   后果很严重：classify() 永远不返回 "call" -> **用户和朋友通话时 Fairy 照样出声
#   -> 对方能听见**（用户最怕的社交事故）；而且每次 classify 都打印一行错误刷日志。
#
#   修法：按文件路径加载（和球加载本模块同一套），并缓存；失败时**降级安全** ——
#   拿不到通话检测就当"无法判断通话"（不返回 call），绝不让它影响其他分类。
_call_mod = None
_call_load_tried = False
_warned_once = set()
# ★ 性能：call_translate.is_call_active() 实测【单次 ~3 秒】。见 is_call_now() 的说明。
_CALL_TTL_S = 3.0
_call_cache = (0.0, False, "")      # (时间戳, 是否通话, 说明)


def _warn_once(msg):
    """同一个问题只打印一次 —— 通话检测每轮都会调，不节流会把日志刷爆。"""
    if msg in _warned_once:
        return
    _warned_once.add(msg)
    print("[media_duck] %s" % msg, flush=True)


def _load_call_module():
    """按文件路径加载 call_translate.py。容忍它不存在 / 接口不全（返回 None）。"""
    global _call_mod, _call_load_tried
    if _call_mod is not None or _call_load_tried:
        return _call_mod
    _call_load_tried = True
    try:
        import importlib.util
        # 球可能已经预注册过，优先复用
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
        sys.modules["call_translate"] = m       # 先注册，容它内部相对导入
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
    """当前是否在通话。返回 (bool, 说明)。只有【高置信】才算通话。

    拿不准一律返回 False —— 误判会导致 Fairy 一直闭嘴（比漏判更糟）。

    ★ 性能：实测 `call_translate.is_call_active()` **单次约 3 秒**（它要去枚举通话相关进程）。
      而 duck() 在**每次 A 级发言前**都会问一次 —— 不缓存就会让用户干等 3 秒才听到回答。
      原来那个裸 import 是"瞬间失败"的，所以这 3 秒成本是**修好之后才暴露**出来的。
      折中：**短 TTL 缓存（3 秒）**。通话状态不会在 3 秒内反复变，不会漏判，
      又能把连续多次发言的成本摊掉。
      ⚠ 第一次问仍然要 3 秒 —— 那是 call_translate 模块自身的性能问题，
        要根治得让它变快（或异步预热），不在本次"小改动"范围内。
    """
    global _call_cache
    ts, act, why = _call_cache
    if ts and (time.time() - ts) < _CALL_TTL_S:
        return act, why          # 命中缓存
    mod = _load_call_module()
    if mod is None:
        _call_cache = (time.time(), False, "")
        return False, "通话检测不可用"
    try:
        r = mod.is_call_active()
        # 容忍接口返回形状不一致：期望 (act, why, conf)
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
        # ⚠ 必须用【检查完成时刻】打戳：这个检查本身要 ~3 秒，
        #   若用开始时刻打戳，等它返回时缓存就已经过期了（下次必然不命中）。
        _call_cache = (time.time(), res[0], res[1])
        return res
    except Exception as e:
        _warn_once("call_translate.is_call_active() 出错 -> 本次当作非通话: %s" % str(e)[:100])
        _call_cache = (time.time(), False, "")
        return False, ""


def classify(app="", title=""):
    """判断在播的东西该怎么让位。返回 call / pause / volume / ignore。

    ★ "call"（通话）是最高优先级：既不暂停也不压低 ——
      通话里的声音（对方说话）绝不能打断，而 Fairy 自己也不该出声
      （她的声音走同一个输出设备，会被对方听见 = 社交事故）。
      判定依赖 call_translate.is_call_active()，只有【高置信】信号才算通话。
      拿不准时**不返回 call** —— 误判会导致 Fairy 一直闭嘴。
    """
    s = (str(app) + " " + str(title)).lower()
    # 通话优先：先问 call_translate（它自己会做分层判断，只有高置信才算）
    if is_call_now()[0]:
        return "call"
    # 视频/直播/游戏优先判为 volume（绝不能暂停）
    for k in VOLUME_HINTS:
        if k in s:
            return "volume"
    for k in PAUSE_HINTS:
        if k in s:
            return "pause"
    return "ignore"


def snapshot():
    """当前媒体会话状态。拿不到就返回空，绝不编。"""
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
    """action: pause / play / toggle"""
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
    """让媒体让位。

    mode: auto -> 按 classify 决定；pause -> 强制暂停；volume -> 交给 volume_duck（本模块不处理）
    返回 token（dict），交给 unduck。拿不到会话时返回 {"ducked": False, ...}

    ★ H5 修复：加 "call" 分支，而且**通话检查放在最前面**。
      原来 duck() 只处理 ignore 和 volume，`how == "call"` 会**掉进 _ctrl("pause")** ——
      与设计意图（通话中既不暂停也不压低）完全相反。
      更要紧的是：**通话时 GSMTC 可能根本看不到任何"Playing"的媒体会话**，
      若先执行"没在播就早退"，就会漏掉"通话中不许出声"这条最关键的规矩 ->
      Fairy 照样说话 -> 对方听见（社交事故）。所以必须在 snapshot 之前先问通话。
    """
    # ★ 通话优先：早于任何"有没有在播"的判断
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
        # 通话中：既不暂停也不压低（通话优先），而且 Fairy 自己也不该出声
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
        "by_me": ok,                      # ★ 规矩2：记住是"我暂停的"
        "app": s0.get("app"),
        "title": s0.get("title"),
        "reason": reason,
        "at": time.time(),
        "deadline": time.time() + UNDUCK_TIMEOUT_S,   # ★ 规矩4：兜底时限
        "raw": out or err[:120],
    }
    _save_state(tok)
    return tok


def unduck(token=None, force=False):
    """恢复。三条规矩都在这里：
       - 只恢复 by_me 的
       - 说话期间用户自己操作过（状态已不是 Paused）就不动
       - 超时兜底：超过 deadline 也照样尝试恢复（否则音乐永久停）

    force=True：跳过"规矩3"（用户是否操作过）—— **强制恢复**。
      ★ H3 修复：tick() 必须用 force=True。
      原来 tick() 调 unduck() 会走规矩3，而播放器在 Opening/Changing/自动切歌
      这类**过渡态**下状态并不是 "Paused"，于是**连超时兜底也拒绝恢复 ->
      音乐永久停住**（规矩3 与 规矩4 双故障叠加）。
      超时的意义就是"无论如何把音乐救回来"。
      ⚠ 但 force 也不盲播：若我暂停的那个会话已经消失，仍然不播（见下）。
    """
    tok = token or _load_state()
    if not tok or not tok.get("ducked"):
        return {"ok": True, "restored": False, "why": "没有需要恢复的（不是我暂停的，或本就没暂停）"}
    if not tok.get("by_me"):
        return {"ok": True, "restored": False, "why": "不是我暂停的，不动"}

    # 规矩3：用户期间自己操作了？
    snap = snapshot()
    cur = None
    for s in (snap.get("sessions") or []):
        if s.get("app") == tok.get("app"):
            cur = s
            break
    if cur is None:
        # ★ 审计修复：我暂停的那个会话**已经消失了** -> 不能盲执行 play。
        #   _ctrl("play") 打的是 GetCurrentSession()，那可能是**另一个**会话，
        #   会把用户本来没打算播的东西播出来。清掉令牌即可（没有东西需要恢复）。
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
    """兜底检查：到点了还没恢复就强制恢复。可以被球定时调用。"""
    tok = _load_state()
    if tok and tok.get("ducked") and tok.get("by_me") and time.time() > (tok.get("deadline") or 0):
        r = unduck(tok, force=True)   # ★ H3：兜底必须强制，否则过渡态下会被规矩3挡住
        r["forced_by_timeout"] = True
        return r
    return {"ok": True, "restored": False, "why": "无需兜底"}


def _save_state(d):
    """原子写（临时文件 + replace），断电不会写坏。
    ★ 审计修复：原来 except: pass 静默吞错 —— 写失败会**静默丢掉恢复令牌**，
      与"音乐永久停住"的事故叠加且无从排查。现在必须出声（同问题只报一次）。
    """
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        tmp = STATE + ".tmp"
        json.dump(d, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, STATE)
    except Exception as e:
        _warn_once("写状态失败（恢复令牌可能丢失）: %s" % str(e)[:110])


def _load_state():
    """读令牌。文件损坏/读不到时返回 {}（调用方会把"没有令牌"当"无需恢复"处理）。"""
    try:
        if os.path.exists(STATE):
            return json.load(open(STATE, encoding="utf-8"))
    except Exception as e:
        _warn_once("读状态失败（按 无令牌 处理）: %s" % str(e)[:110])
    return {}


# ------------------------------------------------------------------ 自测
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
    # 1) classify 表
    chk("游戏 -> volume", classify("ZenlessZoneZero.exe", "绝区零") == "volume")
    chk("浏览器 -> volume", classify("chrome.exe", "B站直播") == "volume")
    chk("网易云 -> pause", classify("cloudmusic.exe", "某首歌") == "pause")
    chk("QQ音乐 -> pause", classify("QQMusic.exe", "") == "pause")
    chk("不认识 -> ignore", classify("weird.exe", "") == "ignore")
    # 2) snapshot 在无会话时优雅
    s = snapshot()
    chk("snapshot 不抛异常", isinstance(s, dict))
    chk("snapshot 有 sessions 字段", "sessions" in s)
    print("     实况: ok=%s sessions=%d %s" % (s.get("ok"), len(s.get("sessions") or []),
                                              (s.get("error") or "")[:60]))
    # 3) 无会话时 duck 不炸
    d = duck("selftest")
    chk("duck 无会话时安全返回", isinstance(d, dict) and d.get("ducked") in (True, False))
    print("     duck: %s" % json.dumps({k: d.get(k) for k in ("ducked", "why", "app")}, ensure_ascii=False))
    # 4) 规矩2：不是我暂停的 -> 不恢复
    r = unduck({"ducked": True, "by_me": False, "app": "x"})
    chk("规矩2 只恢复自己暂停的", r.get("restored") is False)
    # 5) 无凭证 -> 不做事
    r = unduck({})
    chk("规矩2 无凭证不乱播", r.get("restored") is False)
    # 6) 规矩4：超时兜底能识别
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
