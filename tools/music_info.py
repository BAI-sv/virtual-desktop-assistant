# -*- coding: utf-8 -*-
"""
music_info.py —— Fairy 的「听歌识曲 · 歌词 · 创作背景 · 内涵分析」模块

【用户要求】
  "当我播放音乐的时候你应该自己去通过声音，音乐的比对去搜索歌词，
   并给我分析这首歌的创作人，内涵等等类似的功能，你要自己学会扩展"

【设计核心：可插拔的 InfoProvider 注册表】
  每种能力 = 一个 provider，注册进 PROVIDERS。加新能力**不用改主流程**。
      class InfoProvider:
          name = ""
          def available(self) -> bool
          def fetch(self, meta: dict) -> dict
  collect() 会**依次调用**每个 provider，且**每个独立 try** —— 一个挂了不影响别的。

【⚠️ 关于"通过声音比对识别歌曲"（音频指纹）—— 本机没装，但【有免费路径】】
  ❌ "本机没装"不等于"做不到"，免费/开源路径确实存在：

  1. **SongRec** —— 开源免费的 Shazam 客户端（Rust 写的）
     https://github.com/marin-m/SongRec ；包管理：Snap/FreeBSD ports，Windows 有第三方构建
  2. **Chromaprint + AcoustID** —— 开源免费（AcoustID 只需注册一个【免费】API key）
     Python 绑定：`pip install pyacoustid`；另需 `fpcalc.exe`（Chromaprint 自带的命令行工具）
  3. **AudD** —— 有免费试用额度
  4. **ACRCloud** —— 这类才是付费的

  → 所以"听歌识曲"是**可实现的**，只是**需要装一个工具 + （AcoustID 路线）注册免费 key**。
     用户没同意装东西之前，我不擅自装（用户明确说过"不要自作主张"）。

【本模块当前的实际路径】
  **从播放器元数据（GSMTC）拿歌名/歌手 → 再去查歌词/资料**。
  - 播放器给了元数据 → 正常工作
  - 播放器没给（浏览器放网页音频、无名音源）→ **诚实返回"元数据缺失，无法识别"**，绝不瞎猜歌名
  - 想要真指纹识别 → 见下面的 `fingerprint` 扩展位（已写好接口形状，等装了工具就能接）

【数据源（已实测可用）】
  元数据  Windows GSMTC（WinRT，无需装东西）—— 一次 PowerShell 调用返回 JSON
  歌词    LRCLIB  https://lrclib.net/api/search?q=...   （免费开源，无需 key，需 User-Agent）
          返回 plainLyrics（纯歌词）+ syncedLyrics（LRC 时间轴）

【用法】
  py -3.12 music_info.py              看当前在播什么 + 收集信息
  py -3.12 music_info.py --selftest   自测（含构造 meta 测歌词）
  py -3.12 music_info.py --json       只输出 JSON
  py -3.12 music_info.py --which lyrics   只要某一项
"""
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import fairy_root

# ★ 2026-10-10【黑窗】0x08000000 = CREATE_NO_WINDOW（不是 0x00000200）
CREATE_NO_WINDOW = fairy_root.CREATE_NO_WINDOW

LOG = fairy_root.LOGS
CACHE = os.path.join(LOG, "lyrics_cache.json")
UA = {"User-Agent": "VirtualDesktopAssistant/1.0 (personal; DSH plugin)"}

# ==========================================================================
# provider 基类 + 注册表
# ==========================================================================


class InfoProvider:
    """一个"信息提供者"。加新能力就继承它并 register()。"""
    name = ""
    label = ""
    #: 需要什么才能真正工作（用于 available() 说明原因）
    requires = ""

    def available(self):
        """能不能用。返回 False 时 collect() 会记一条 skipped，而不是报错。"""
        return True

    def fetch(self, meta):
        """meta: {title, artist, album, album_artist, app, playing}
        返回 dict（内容自由，建议带 "ok" 字段）。**不要在里面对 meta 做假设。**"""
        raise NotImplementedError


PROVIDERS = {}
ORDER = []


def register(p):
    PROVIDERS[p.name] = p
    if p.name not in ORDER:
        ORDER.append(p.name)
    return p


# ==========================================================================
# ① meta provider —— 曲目基本信息
# ==========================================================================
class MetaProvider(InfoProvider):
    name = "meta"
    label = "曲目信息"

    def fetch(self, meta):
        if not (meta.get("title") or meta.get("artist")):
            return {"ok": False,
                    "why": "播放器没有提供歌名/歌手（网页音频、无名音源常见）—— 无法识别"}
        out = {"ok": True}
        for k, cn in (("title", "歌名"), ("artist", "歌手"), ("album", "专辑"),
                      ("album_artist", "专辑艺术家"), ("app", "播放器"), ("playing", "播放状态")):
            v = meta.get(k)
            if v:
                out[cn] = v
        return out


# ==========================================================================
# ② lyrics provider —— LRCLIB 查歌词（纯歌词 + 时间轴）
# ==========================================================================
def _load_cache():
    try:
        if os.path.exists(CACHE):
            return json.load(open(CACHE, encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_cache(c):
    try:
        os.makedirs(LOG, exist_ok=True)
        tmp = CACHE + ".tmp"
        json.dump(c, open(tmp, "w", encoding="utf-8"), ensure_ascii=False)
        os.replace(tmp, CACHE)
    except Exception:
        pass


def _http_json(url, timeout=12):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


class LyricsProvider(InfoProvider):
    """LRCLIB：免费开源歌词库，无需 key。返回纯歌词 + LRC 时间轴。"""
    name = "lyrics"
    label = "歌词（LRCLIB）"
    requires = "能联网；歌名/歌手"

    def fetch(self, meta):
        title = (meta.get("title") or "").strip()
        artist = (meta.get("artist") or "").strip()
        if not title:
            return {"ok": False, "why": "没有歌名，无法查歌词（不做猜测）"}

        key = "%s|%s" % (title.lower(), artist.lower())
        cache = _load_cache()
        if key in cache:
            hit = dict(cache[key])
            hit["cached"] = True
            return hit

        res = {"ok": False, "source": "lrclib.net", "title": title, "artist": artist}

        # 1) 精确接口（要 artist + track）
        if artist:
            try:
                u = ("https://lrclib.net/api/get?artist_name=%s&track_name=%s"
                     % (urllib.parse.quote(artist), urllib.parse.quote(title)))
                j = _http_json(u)
                if j and (j.get("plainLyrics") or j.get("syncedLyrics")):
                    res.update({"ok": True, "album": j.get("albumName"),
                                "duration": j.get("duration"),
                                "plain": j.get("plainLyrics") or "",
                                "synced": j.get("syncedLyrics") or "",
                                "matched_by": "精确匹配"})
            except Exception:
                pass  # 落到搜索

        # 2) 搜索接口兜底
        if not res.get("ok"):
            try:
                q = "%s %s" % (title, artist) if artist else title
                u = "https://lrclib.net/api/search?q=%s" % urllib.parse.quote(q.strip())
                arr = _http_json(u)
                if isinstance(arr, list) and arr:
                    best = arr[0]
                    res.update({"ok": True, "album": best.get("albumName"),
                                "duration": best.get("duration"),
                                "plain": best.get("plainLyrics") or "",
                                "synced": best.get("syncedLyrics") or "",
                                "matched_by": "搜索匹配（%d 个候选，取第一个）" % len(arr),
                                "candidates": [{"t": x.get("trackName"), "a": x.get("artistName")}
                                               for x in arr[:5]]})
            except Exception as e:
                res["why"] = "LRCLIB 请求失败: %s" % str(e)[:90]

        if not res.get("ok") and "why" not in res:
            res["why"] = "LRCLIB 没找到这首歌（换 web_search 搜'歌名 歌词'是下一步）"

        # 3) 简繁提示（LRCLIB 上不少中文歌词是繁体 —— 实测"晴天"就是）
        if res.get("ok"):
            pl = res.get("plain") or ""
            if pl:
                # 只做提示，不擅自转换（用户偏好：不要自作主张）
                common_trad = "嗎麼樣這個還聽無法對說過時間點"
                hits = sum(1 for ch in common_trad if ch in pl)
                if hits >= 2:
                    res["note"] = "歌词可能是繁体（检测到 %d 个繁体常用字），已原样返回未转换" % hits
        if res.get("ok"):
            cache[key] = {k: v for k, v in res.items() if k != "cached"}
            _save_cache(cache)
        return res


# ==========================================================================
# ③④ 扩展位 —— 需要 LLM / 联网搜索，由调用方（主代理或球）补上
# ==========================================================================
class BackgroundProvider(InfoProvider):
    """创作背景。**需要搜索或 LLM，本模块不自己实现**，只提供结构化接口。

    调用方拿到 fetch() 的返回后，应当：
        web_search(f"{artist} {title} 创作背景 创作故事")
    再把结果写回 meta 或直接展示。
    """
    name = "background"
    label = "创作背景（需调用方搜索）"
    requires = "调用方的 web_search 或 LLM"

    def available(self):
        return False  # 本模块内不可用 —— 诚实标记，不假装能查

    def fetch(self, meta):
        return {"ok": False,
                "why": "本模块不实现搜索，需要调用方用 web_search 补上",
                "query_template": "%s %s 创作背景 创作故事 专辑" % (
                    meta.get("artist") or "", meta.get("title") or ""),
                "expected_shape": {"ok": True, "text": "……", "sources": ["url"]}}


class MeaningProvider(InfoProvider):
    """内涵分析。**需要 LLM**，同样只留接口。"""
    name = "meaning"
    label = "内涵分析（需 LLM）"
    requires = "调用方把歌词交给 LLM"

    def available(self):
        return False

    def fetch(self, meta):
        return {"ok": False,
                "why": "本模块不做 LLM 推理，需要调用方把歌词 + 元数据交给模型",
                "prompt_template": (
                    "这是歌曲《{title}》（{artist}）的歌词：\n{lyrics}\n\n"
                    "请分析：1) 主题与情感 2) 关键意象与隐喻 3) 结构与叙事视角 "
                    "4) 可能的创作意图。用中文，简洁分点。"),
                "expected_shape": {"ok": True, "text": "……"}}


class FingerprintProvider(InfoProvider):
    """音频指纹识别（真·听歌识曲）。**需先装工具，本模块内暂不可用。**

    免费/开源路径（2026-08-10 上网核实，之前我误判成"只能付费"）：
      A) SongRec（开源 Shazam 客户端，Rust）—— 直接出歌名/歌手
      B) Chromaprint(fpcalc) + AcoustID（免费 API key）—— pyacoustid 绑定
    装上并配好后，把下面的 fetch() 实现即可（接口形状已经定好，主流程不用改）。
    """
    name = "fingerprint"
    label = "音频指纹识别"
    requires = "SongRec 或 fpcalc+pyacoustid（都未安装）"

    def available(self):
        # 装好了就自动可用 —— 探测 fpcalc / songrec 是否在 PATH
        import shutil as _sh
        return bool(_sh.which("fpcalc") or _sh.which("songrec"))

    def fetch(self, meta):
        if not self.available():
            return {"ok": False,
                    "why": "未安装指纹工具（不擅自装：用户明确说过不要自作主张）",
                    "free_options": [
                        "SongRec  https://github.com/marin-m/SongRec",
                        "Chromaprint+AcoustID  pip install pyacoustid（另需 fpcalc.exe）",
                    ]}
        return {"ok": False, "why": "工具在但本模块还没实现调用（接口已留好）"}


register(MetaProvider())
register(LyricsProvider())
register(BackgroundProvider())
register(MeaningProvider())
register(FingerprintProvider())


# ==========================================================================
# GSMTC —— 拿当前在播的曲目元数据（一次 PowerShell 调用返回 JSON）
# ==========================================================================
_PS = r'''
$ErrorActionPreference='SilentlyContinue'
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
  $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $t) {
  $m = $asTask.MakeGenericMethod($t); $task = $m.Invoke($null, @($op))
  $task.Wait(-1) | Out-Null; $task.Result
}
$out = @{ ok = $false }
try {
  $M = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager,Windows.Media.Control,ContentType=WindowsRuntime]
  $mgr = Await ($M::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
  $s = $mgr.GetCurrentSession()
  if ($s -eq $null) { $out.why = 'no_session' }
  else {
    $p = Await ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
    $out.ok      = $true
    $out.title   = [string]$p.Title
    $out.artist  = [string]$p.Artist
    $out.album   = [string]$p.AlbumTitle
    $out.album_artist = [string]$p.AlbumArtist
    $out.app     = [string]$s.SourceAppUserModelId
    $out.playing = [string]$s.GetPlaybackInfo().PlaybackStatus
  }
} catch { $out.why = 'exception: ' + $_.Exception.Message }
$out | ConvertTo-Json -Compress
'''


def now_playing(timeout=40):
    """返回 {ok, title, artist, album, album_artist, app, playing} 或 {ok:False, why}。

    ⚠ 一次 PowerShell 调用（0.4~0.6s 启动开销，所以绝不每步启一次）。
    ⚠ 只读：绝不 pause/play，不干扰用户正在听的东西。
    """
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS],
                           capture_output=True, timeout=timeout,
                           creationflags=CREATE_NO_WINDOW)
        out = (r.stdout or b"").decode("utf-8", "replace").strip()
        # PowerShell 输出可能是单行 JSON
        if not out:
            return {"ok": False, "why": "PowerShell 无输出"}
        j = json.loads(out)
        if not isinstance(j, dict):
            return {"ok": False, "why": "返回格式异常"}
        # 规范化（GSMTC 的空字段可能给空串）
        for k in ("title", "artist", "album", "album_artist", "app", "playing"):
            if k in j and isinstance(j[k], str):
                j[k] = j[k].strip()
        if not j.get("ok"):
            j.setdefault("why", "没有活动的媒体会话（没有播放器在播）")
        return j
    except subprocess.TimeoutExpired:
        return {"ok": False, "why": "PowerShell 超时"}
    except Exception as e:
        return {"ok": False, "why": "读取失败: %s" % str(e)[:120]}


# ==========================================================================
# collect —— 依次调 providers，每个独立 try
# ==========================================================================
LAST_KEY = {"v": None}


def collect(meta=None, which=None):
    """收集信息。meta=None 时自己取当前在播。

    which: None=全部 / "lyrics" / "meta" / [...]
    返回 {"now": meta, "items": {name: result}, "changed": bool}
    """
    if meta is None:
        meta = now_playing()
    res = {"now": meta, "items": {}}
    if not meta or not meta.get("ok"):
        res["items"]["_"] = {"ok": False,
                            "why": (meta or {}).get("why") or "拿不到元数据"}
        return res

    key = "%s|%s" % ((meta.get("title") or "").lower(), (meta.get("artist") or "").lower())
    res["changed"] = (key != LAST_KEY["v"])
    LAST_KEY["v"] = key

    names = [which] if isinstance(which, str) else (which or ORDER)
    for n in names:
        p = PROVIDERS.get(n)
        if not p:
            res["items"][n] = {"ok": False, "why": "没有注册这个 provider"}
            continue
        try:
            if not p.available():
                res["items"][n] = {"ok": False, "skipped": True,
                                   "why": "本模块内不可用（%s）" % (p.requires or "缺依赖")}
                continue
            res["items"][n] = p.fetch(meta)
        except Exception as e:
            # 独立 try —— 一个 provider 挂了不影响其他
            res["items"][n] = {"ok": False, "why": "provider 异常: %s" % str(e)[:120]}
    return res


# ==========================================================================
# 自测
# ==========================================================================
def selftest():
    print("=" * 66)
    print("music_info 自测   %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 66)
    passed = failed = 0

    def chk(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print("  ✓ %s %s" % (name, detail))
        else:
            failed += 1
            print("  ✗ %s %s" % (name, detail))

    print("\n[1] provider 注册表")
    chk("provider 已注册（>=5）", len(PROVIDERS) >= 5, "-> %s" % list(PROVIDERS))
    chk("fingerprint 扩展位存在", "fingerprint" in PROVIDERS)
    chk("fingerprint 诚实标注未装工具",
        not PROVIDERS["fingerprint"].available()
        and "未安装" in (PROVIDERS["fingerprint"].fetch({}).get("why") or ""))
    chk("lyrics 可用", PROVIDERS["lyrics"].available())
    chk("background 不可用（诚实标记）", not PROVIDERS["background"].available())
    chk("meaning 不可用（诚实标记）", not PROVIDERS["meaning"].available())

    print("\n[2] 扩展位接口形状")
    bg = PROVIDERS["background"].fetch({"artist": "A", "title": "B"})
    chk("background 返回 why + query_template",
        (not bg.get("ok")) and "query_template" in bg, "-> %s" % bg.get("query_template"))
    mn = PROVIDERS["meaning"].fetch({"artist": "A", "title": "B"})
    chk("meaning 返回 prompt_template", "prompt_template" in mn)

    print("\n[3] now_playing（当前系统真实状态）")
    np = now_playing()
    print("      ->", json.dumps(np, ensure_ascii=False)[:200])
    if np.get("ok"):
        chk("拿到了会话", True, "title=%r artist=%r app=%r playing=%r"
            % (np.get("title"), np.get("artist"), np.get("app"), np.get("playing")))
    else:
        chk("正确报告 没有元数据 这一边界（不崩、不瞎猜）",
            "why" in np, "-> %s" % np.get("why"))

    print("\n[4] 元数据缺失时的行为（构造）")
    r = collect(meta={"ok": True, "title": "", "artist": "", "app": "chrome.exe", "playing": "Playing"})
    m = r["items"].get("meta", {})
    l = r["items"].get("lyrics", {})
    chk("meta 诚实报告无法识别", m.get("ok") is False and "无法识别" in (m.get("why") or ""))
    chk("lyrics 拒绝瞎猜", l.get("ok") is False and "不做猜测" in (l.get("why") or ""))

    print("\n[5] 歌词 provider 真取（构造 meta：晴天/周杰伦）")
    t0 = time.time()
    lr = PROVIDERS["lyrics"].fetch({"title": "晴天", "artist": "周杰伦"})
    dt = time.time() - t0
    if lr.get("ok"):
        chk("LRCLIB 取到歌词", True, "%.1fs 纯歌词 %d 字 / 时间轴 %d 行 / %s"
            % (dt, len(lr.get("plain") or ""), len((lr.get("synced") or "").splitlines()),
               lr.get("matched_by")))
        if lr.get("note"):
            print("      备注:", lr["note"])
    else:
        chk("LRCLIB 请求（失败也要有 why）", bool(lr.get("why")), "-> %s" % lr.get("why"))

    print("\n[6] 缓存（同一首不重复请求）")
    t0 = time.time()
    lr2 = PROVIDERS["lyrics"].fetch({"title": "晴天", "artist": "周杰伦"})
    dt2 = time.time() - t0
    chk("第二次走缓存", lr2.get("cached") is True, "%.3fs（首次 %.1fs）" % (dt2, dt) if lr.get("ok") else "")

    print("\n[7] 去重（同一首歌 changed=False）")
    a = collect(meta={"ok": True, "title": "X", "artist": "Y"}, which="meta")
    b = collect(meta={"ok": True, "title": "X", "artist": "Y"}, which="meta")
    chk("第二次 changed=False", a.get("changed") is True and b.get("changed") is False)

    print("\n[8] 独立降级（一个 provider 抛异常不影响别的）")
    class Boom(InfoProvider):
        name = "boom"
        label = "故意炸"
        def fetch(self, meta):
            raise RuntimeError("我炸了")
    register(Boom())
    r = collect(meta={"ok": True, "title": "T", "artist": "A"}, which=["boom", "meta"])
    chk("boom 报异常但 meta 仍成功",
        r["items"]["boom"].get("ok") is False and r["items"]["meta"].get("ok") is True)
    PROVIDERS.pop("boom", None)
    ORDER.remove("boom")

    print("\n" + "=" * 66)
    print("%d 通过 / %d 失败" % (passed, failed))
    return 0 if failed == 0 else 1


def main():
    if "--selftest" in sys.argv:
        return selftest()
    which = None
    if "--which" in sys.argv:
        i = sys.argv.index("--which")
        if i + 1 < len(sys.argv):
            which = sys.argv[i + 1]
    r = collect(which=which)
    if "--json" in sys.argv:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0
    np = r["now"]
    print("当前在播：")
    if np.get("ok"):
        for k, cn in (("title", "歌名"), ("artist", "歌手"), ("album", "专辑"),
                      ("app", "播放器"), ("playing", "状态")):
            if np.get(k):
                print("   %-6s %s" % (cn, np[k]))
    else:
        print("   （拿不到）%s" % np.get("why"))
    print("\n收集结果：")
    for n, v in r["items"].items():
        if n == "_":
            print("   %s" % v.get("why"))
            continue
        lab = PROVIDERS[n].label if n in PROVIDERS else n
        if v.get("ok"):
            extra = ""
            if n == "lyrics":
                extra = "（纯歌词 %d 字 / 时间轴 %d 行）" % (
                    len(v.get("plain") or ""), len((v.get("synced") or "").splitlines()))
            print("   ✓ %-22s %s%s" % (lab, v.get("matched_by") or "", extra))
            if v.get("note"):
                print("       %s" % v["note"])
        else:
            print("   ✗ %-22s %s" % (lab, v.get("why")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
