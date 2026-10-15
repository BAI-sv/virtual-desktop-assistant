# -*- coding: utf-8 -*-
"""
fairy.py —— Fairy 的【统一总管】

【用户要求】"上面所有的要求全部集成到 Fairy 里"
  → 不再散着一堆脚本各跑各的，而是【一个入口统管所有组件】

【用法】
    py -3.12 D:\\FAIRY\\tools\\fairy.py <命令> [参数...]

【生命周期（常驻组件）】
    start        启动所有（球 + 本地服务）
    stop         停掉所有
    restart      重启所有
    status       ★一眼看清：每个组件在不在、我在干什么、余额多少

【单组件】
    start ball                   只起某一个
    stop  ball                   只停某一个
    log   ball                   看日志尾巴

【按需功能（用完就退）】
    health / fastcheck     体检
    news / weather         新闻 / 天气
    music                  当前在放什么
    devices / ble          扫设备 / BLE 认出身份
    cpu                    CPU 硬件错误趋势
    subtitle / call        视频字幕 / 通话翻译

【安装与配置】
    init / rescan / scan   首次安装 / 重做全量 / 只看不扩展
    config                 查看（并首次生成）统一配置 D:\\FAIRY\\fairy.json
"""
import json
import os
import socket
import subprocess
import sys
import time
import fairy_root

# ★ 2026-10-10【黑窗】统一的"不弹窗"常量与封装都在 fairy_root 里。
#   0x08000000 = CREATE_NO_WINDOW（★ 不是 0x00000200，那个是 CREATE_NEW_PROCESS_GROUP）
CREATE_NO_WINDOW = fairy_root.CREATE_NO_WINDOW
NOWIN_FLAGS = fairy_root.NOWIN_FLAGS

# ★ 2026-10-10（第二批审查顺带修掉）：本文件的提示里带 '✓'，
#   而 stdout 是【管道 + gbk】时 print('✓') 会抛 UnicodeEncodeError ——
#   于是 `fairy.py stop host` / `start host` 明明把事做了，却打出回溯、退出码 1。
#   ★ 只改【错误处理】，不改编码：控制台是 gbk 就继续按 gbk 输出（不会乱码），
#     编不出来的字符降级成 '?'，绝不因为一行提示把自己打断。
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

# ==========================================================================
# ★★ 2026-10-10 晚【DSH 独立性】—— 硬约束，已做成机制
#   从 DSH 里（Web GUI 的 pwsh / 主代理）起的进程会挂在 DSH 的进程树上，
#   用户【一关 DSH】整棵树被杀 -> 球 / 守护 / 本地服务 / 语音全没。
#   实测证据：17:50:16 用户关 DSH，ball/guard/host/tts 全死；
#     唯一活下来的 8081（fairy_ui）—— 因为它当初是用 WMI 起的（父进程 = WmiPrvSE）。
#   -> 这里认 DSH_* 环境变量；认出自己在 DSH 里，就把 start / restart 交给
#      fairy_detach.py 用 WMI 重做一遍（父进程变 WmiPrvSE，不在 DSH 树上）。
# ==========================================================================
NO_DETACH = "--no-detach"           # 内层（已脱离的那次）带上它，防无限自我调用
FROM_DETACH = NO_DETACH in sys.argv
if FROM_DETACH:
    try:
        sys.argv.remove(NO_DETACH)
    except ValueError:
        pass


def in_dsh():
    """是否跑在 DSH 里 —— DSH 会给自己拉起的进程注入 DSH_* 环境变量。"""
    return any(os.environ.get(k) for k in
               ("DSH_SESSION_ID", "DSH_PROFILE_DIR", "DSH_SHELL", "DSH_WEB_URL"))

# ---------------------------------------------------------------- 统一配置
DEFAULT_CONF = {
    "_说明": "Fairy 统一配置：所有组件的开关都在这。改完保存即可（多数项下次启动生效）。",
    # ★ tts 默认【关】—— 由 guard 负责拉起它。理由（2026-10-09 实测）：
    #   ① guard 的职责就是"盯 9881，挂了自动拉起"，它【本来就会】起 IndexTTS
    #   ② 若 tts 也开，start 会依次启 guard 和 tts：
    #      guard 先拉起 indextts_start.py -> 紧接着 tts 组件检查
    #      pids_of("indextts_server") 时 server 还没起来 -> 【又拉起一个 indextts_start.py】
    #      -> 两个 start -> 两个 server -> 抢 9881 端口 + 双份显存 ✗
    #      （实测确实看到过两个 indextts_server.py 进程）
    #   ③ 开 tts 还会让 `fairy.py start` 卡 75 秒（tts 的 wait_s=75）；
    #      交给 guard 则 start 只需等 6 秒，IndexTTS 在后台慢慢加载
    #   ★ 手动单启仍然可用：py -3.12 fairy.py start tts
    "components": {"ball": True, "guard": True,
                   "tts": False,
                   # ★ 2026-10-10：host 默认开 —— DSH 不在时的备胎，必须预先跑着
                   "host": True,
                   # ★ 2026-10-11【统一管理】耳朵纳入 fairy.py：
                   #   语音唤醒/对话（SenseVoice）随 Fairy 一起启停
                   "ear": True,
                   # ★ 2026-10-15【屏幕边缘 RGB 灯效】随 Fairy 一起启停
                   "rim": True},
    # ★★ 2026-10-10【黑窗】silent 名单：这些脚本一律用 pythonw.exe 起（不建控制台）。
    #   组件名（guard）或脚本名（indextts_guard.py）都认。
    #   ★ 之前只有 COMPONENTS 里写死的 silent 生效，guard/tts 永远用 python.exe
    #     起 -> 每次 start 都挂一个可见控制台（实测 guard 进程名就是 python.exe）。
    "silent": {"indextts_guard.py": True, "indextts_start.py": True,
               "bootstrap.py": True, "fairy_host.py": True, "fairy_ball.py": True,
               "fairy_ear.py": True},
    # ★ 2026-10-10 新增【接口寻址】段（独立运行改造）。
    #   优先级：环境变量 > 这里 > 代码默认值。改完下次启动生效。
    #   ★ 排障用法：把 dsh_base 指到一个不通的端口，就能模拟"DSH 不在"，
    #     验证球会不会自动切到本地 19388 —— 全程不用真去关 DSH。
    "endpoints": {
        "dsh_base": "http://127.0.0.1:19387",
        "local_base": "http://127.0.0.1:19388",
        "indextts_url": "http://127.0.0.1:9881/tts",
        "ref_audio": "D:\\FAIRY\\voice\\index_voices\\fairy.wav",
        "prefer": "auto",
    },
    "ball": {"speak": True, "bubble_seconds": 4, "brief_seconds": 18},
# ★ 2026-10-09【删除桌宠组件】：
    #   主程序 whale_pet.py 与 160MB 动画素材均为第三方，已移出（本机待删目录）。
    #   本组件已移除；若以后恢复，请一并恢复自备素材。
    "advisor": {"enabled": True, "observe_min": 5, "min_gap_min": 10},
    "balance": {"warn_yuan": 30, "alert_yuan": 10},
    "news": {"count": 0, "keyword_count": 0},
}


def load_conf():
    try:
        if os.path.exists(CONF):
            with open(CONF, encoding="utf-8") as f:
                c = json.load(f)
            for k, v in DEFAULT_CONF.items():          # 补全缺失键，不覆盖用户改的
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


# ---------------------------------------------------------------- 组件表
COMPONENTS = {
    #   silent=True -> 用 pythonw.exe 起，绝不弹黑窗（用户明确抱怨过黑窗）。
    #   ★ 2026-10-10：ball 之前【没有】silent -> 每次重启用 python.exe 起，
    #     控制台窗口（conhost）会跟着弹出来。球的窗口是 WS_EX_TOOLWINDOW，
    #     不占任务栏，所以那个黑窗很容易被忽略掉 —— 实测补上。
    "ball": {"script": "fairy_ball.py", "match": "fairy_ball", "silent": True,
             "desc": "桌面球 —— Fairy 的门面（球+进度条+语音+气泡+闪避）"},
    # ★ 音色合成：不跑它，/api/fairy/say 会降级成 Edge TTS 的通用音色
    #   （它就是"Fairy 的嗓子"—— 用 fairy.wav 克隆；模型加载约 35s，所以 wait_s 给足）
    "tts": {"script": "indextts_start.py", "match": "indextts_server",
            "desc": "Fairy 音色合成 IndexTTS（GPU 克隆）", "wait_s": 75},
    # ★ IndexTTS 保活守护：每 15 秒探 :9881，挂了立刻自动拉起。
    #   没有它，IndexTTS 一挂球就退回 Edge 通用音色（用户明确讨厌的那个）。
    "guard": {"script": "indextts_guard.py", "match": "indextts_guard.py",
              "desc": "Fairy 音色保活守护（盯 9881，挂了自动拉起）", "wait_s": 6,
              "silent": True},
    # ★★ 2026-10-10【独立运行改造】本地小服务：让球在没有 DSH 时也能活。
    #   它用【和 DSH 插件一样的契约】提供 meta/reply/push/say/task，
    #   于是 DSH 关掉后球仍然：能显示状态、能念助手回复、能用 Fairy 音色说话。
    #   ★ 为什么必须默认开：它就是"DSH 不在时的备胎"，备胎不预先装好等于没有。
    #   silent=True -> 用 pythonw.exe 起，绝不弹黑窗（用户明确抱怨过）。
    "host": {"script": "fairy_host.py", "match": "fairy_host.py",
             "desc": "Fairy 本地服务（19388，DSH 不在时的备胎）",
             "wait_s": 3, "silent": True},
    # ★ 2026-10-11【统一管理】耳朵（语音唤醒/对话）：纳入 start/stop/restart。
    #   SenseVoice 加载约 2 秒；KWS 约 1.5 秒，wait_s 给 8 秒足够。
    "ear": {"script": "fairy_ear.py", "match": "fairy_ear",
            "desc": "Fairy 耳朵 —— 语音唤醒/对话（SenseVoice）",
            "wait_s": 8, "silent": True},
    # ★ 2026-10-11【视觉引擎】本地视觉大脑（llama-server Vulkan，8085，
    #   Qwen3-VL-8B 可看图可对话）。ear.engine=llama 时 Fairy 大脑走这里。
    #   模型加载约 15 秒，wait_s 给 20；进程名是 llama-server。
    # ★ 2026-10-15【本地文本引擎】替代已卸载的 Ollama：llama-server 跑 Qwen3.5-9B，8085。
    "llm": {"script": "fairy_llm.py", "match": "Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q4_K_M",
            "desc": "本地文本引擎（8085，Qwen3.5-9B 替代 Ollama）",
            "wait_s": 20, "silent": True},
    "vl": {"script": "fairy_vl.py", "match": "Qwen3-VL-8B-Instruct-abliterated",
           "desc": "Fairy 视觉引擎（8086，Qwen3-VL-8B）",
           "wait_s": 20, "silent": True},
    # ★ 2026-10-12【统一网页对话】8090：所有引擎的网页对话页（代理转发）。
    #   Strata 用自带 8080 页面；Ollama/llm 引擎用这页。幂等常驻，不占资源。
    "chat": {"script": "fairy_chat.py", "match": "fairy_chat",
             "desc": "Fairy 统一对话页（8090，代理转发各引擎）",
             "wait_s": 3, "silent": True},
    # ★ 2026-10-15【屏幕边缘 RGB 灯效】全屏透明灯带，随模型运行状态呼吸变色。
    "rim": {"script": "fairy_rim.py", "match": "fairy_rim",
            "desc": "屏幕边缘呼吸 RGB 灯效（随模型状态变色）",
            "wait_s": 3, "silent": True},
}


def _ps(cmd, t=60):
    """★ 2026-10-11【Linux 适配】跨平台命令执行：Windows 用 PowerShell，
    Linux 用 bash -c。返回 stdout 文本。"""
    try:
        if sys.platform.startswith("win"):
            # ★ 2026-10-10【黑窗】powershell.exe 是控制台程序。本进程若由
            #   pythonw / 计划任务拉起（没有控制台），不加这个就会【新建一个
            #   Windows Terminal 窗口】—— 而 pids_of() 每次 start/stop/status
            #   都会调用它，所以这就是"启动时弹一堆黑窗"的主要来源之一。
            r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                               capture_output=True, timeout=t,
                               creationflags=CREATE_NO_WINDOW)
            return (r.stdout or b"").decode("utf-8", "replace").strip()
        r = subprocess.run(["bash", "-c", cmd], capture_output=True, timeout=t)
        return (r.stdout or b"").decode("utf-8", "replace").strip()
    except Exception as e:
        print("[fairy] 命令执行失败: %s" % e, flush=True)
        return ""


def pids_of(match):
    """按命令行匹配找 PID（比按进程名可靠 —— python 有很多个）。
    ★ 2026-10-11：非 python 引擎（llama-server / strata / ollama 等）按进程名兜底查。"""
    if sys.platform.startswith("win"):
        ids = [x.strip() for x in _ps(
            '@(Get-CimInstance Win32_Process -Filter "Name=\'python.exe\' or Name=\'pythonw.exe\'"'
            ' | Where-Object {$_.CommandLine -like \'*%s*\'}).ProcessId' % match
        ).splitlines() if x.strip().isdigit()]
        if ids:
            return ids
        # 非 python 进程（llama-server / strata / ollama 等）：按命令行模糊匹配，
        #   这样能区分多个同名引擎（如文本 9B 和视觉 8B 两个 llama-server）。
        return [x.strip() for x in _ps(
            "@(Get-CimInstance Win32_Process | Where-Object {$_.CommandLine -like '*%s*'}).ProcessId" % match
        ).splitlines() if x.strip().isdigit()]
    # Linux：pgrep 按命令行匹配（引擎进程也能命中）
    return [x.strip() for x in _ps("pgrep -f '%s'" % match.replace("'", "'\\''"))
            .splitlines() if x.strip().isdigit()]


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
    # ★ 2026-10-10：用 pythonw.exe 起 —— 绝不弹黑窗。
    #   （用户明确抱怨过黑窗；原来的 creationflags 只挡住了"自己"那次，
    #     挡不住 venv 启动器二次启动出来的 conhost，所以这里直接换解释器。）
    # ★★ 2026-10-10【黑窗·第二次修】原来只认 COMPONENTS 里写死的 comp["silent"]，
    #   而 guard / tts 没写 -> 它们永远用 python.exe 起，于是【每次 start 都挂一个
    #   可见控制台】。实测证据：guard 的进程名一直是 python.exe（不是 pythonw.exe）。
    #   现在三个口子都认：① 组件自带 silent ② 统一配置里写组件名 ③ 写脚本名。
    exe = PY
    _silent = (conf or {}).get("silent") or {}
    _want_silent = (bool(comp.get("silent"))
                    or bool(_silent.get(name))
                    or bool(_silent.get(comp["script"])))
    if sys.platform.startswith("win"):
        if _want_silent:
            exe = fairy_root.pythonw_of(PY)
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW (0x08000000) —— 脱离控制台，关掉终端也不死
        subprocess.Popen([exe, p], stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         creationflags=NOWIN_FLAGS, env=env)
    else:
        # ★ 2026-10-11【Linux 适配】无 pythonw/NOWIN_FLAGS，直接 python 起，nohup 风格脱离终端
        subprocess.Popen([exe, p], stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         start_new_session=True, env=env)
    # 等多久按组件定（IndexTTS 要加载 GPU 模型约 35s，球几秒就够）
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


def _progress_bar(filled, total, width=26):
    """★ 2026-10-12【启动进度条】深色风格，青色高亮（参考 NAI-晓 配色）。
    控制台输出：[██████████--------] 45%。"""
    pct = int(filled * 100 / total) if total else 100
    n = int(filled * width / total) if total else width
    n = max(0, min(n, width))
    bar = ("\033[36m" + "█" * n + "\033[90m" + "─" * (width - n) + "\033[0m")
    return "[%s] %3d%%" % (bar, pct)


COMPONENT_NAMES = {"ball": "悬浮球", "guard": "TTS 守护", "tts": "语音合成",
                   "host": "本地大脑", "ear": "语音耳朵", "vl": "视觉模型"}


def stop_engines():
    """★ 2026-10-11【用户要求】Fairy 关闭时联动关闭相关大模型引擎（Strata / Ollama / 视觉 llama-server）。
    模型文件全部保留：下次启动 / 使用时自动加载，不会重复下载。"""
    killed = []
    if sys.platform.startswith("win"):
        for proc in ("strata", "ollama", "llama-server"):
            try:
                ids = _ps("(Get-Process %s -ErrorAction SilentlyContinue).Id" % proc)
                for pid in ids.splitlines():
                    if pid.strip().isdigit():
                        _ps("Stop-Process -Id %s -Force" % pid.strip())
                        killed.append(proc)
            except Exception:
                pass
    else:
        # ★ 2026-10-11【Linux 适配】pkill 按进程名杀引擎
        for proc in ("strata", "ollama", "llama-server"):
            _ps("pkill -f %s 2>/dev/null; true" % proc)
            killed.append(proc)
    if killed:
        print("✓ 已联动关闭引擎: %s" % ", ".join(sorted(set(killed))))
    return killed


def _startup_say(ready):
    """★ 2026-10-11【用户要求】fairy 启动后自动播报，同类功能合并只播一句。
    ready: 已启动组件名列表。按功能大类合并文案，播完不阻塞（--async）。
    另读使用记忆：常用功能（超阈值）播报"已准备"。"""
    try:
        cats = []
        if any(n in ready for n in ("ball", "host")):
            cats.append("桌面助手")
        if "tts" in ready:
            cats.append("语音播报")
        if "guard" in ready:
            cats.append("音色守护")
        if not cats:
            return
        text = "Fairy 已启动，" + "、".join(cats) + "就绪，你可以说话了"
        # 习惯学习：常用功能保持就绪
        try:
            import fairy_memory as _fm
            _sug = _fm.suggest_on_startup(5)
            if _sug:
                text += "，常用%s已准备" % "、".join(_sug[:3])
        except Exception:
            pass
        subprocess.Popen([PY, os.path.join(T, "fairy_say.py"), "--async", text],
                         creationflags=CREATE_NO_WINDOW,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _ollama_first_model():
    """查 Ollama 本地已导入的模型名（列表第一行），失败给默认名。"""
    try:
        _exe = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
        r = subprocess.run([_exe, "list"], capture_output=True, text=True,
                           timeout=15, creationflags=CREATE_NO_WINDOW)
        for _ln in (r.stdout or "").splitlines()[1:]:
            _nm = _ln.split()[0] if _ln.strip() else ""
            if _nm:
                return _nm
    except Exception:
        pass
    return "qwen3.5-9b"


def ensure_model_conf(conf):
    """★ 2026-10-11【用户要求】初始运行默认本地模型：
    首次启动自动检测设备 -> 匹配本地可用模型/引擎 -> 写入 fairy.json。
    只做一次；之后使用者随时可在球菜单「AI大模型」手动更换引擎与模型。
    返回 (引擎, 模型名, 设备说明)；未匹配到返回 (None, None, None)。"""
    m = conf.get("model") or {}
    if m.get("configured"):
        return None, None, None
    try:
        import fairy_deploy as _dp
        gpus, mem = _dp.get_gpu_info(), _dp.get_mem_gb()
        tier, _s, desc = _dp.pick_tier(gpus, mem)
        # 引擎判定：Strata 已安装且配置在 -> strata（restart 时引擎已被停，
        # 不能只看端口，否则会误判成 ollama）；否则 Ollama；都没有就不配置
        engine = name = ""
        if os.path.isdir(r"E:\Strata"):
            # ★ 2026-10-11【模型自动配置清单】从 Strata 扫描选档（IQ4 甜点位优先）
            _sc = _dp.strata_pick_default()
            if _sc:
                engine, name = "strata", _sc["name"]
        if not engine and _dp.ollama_available():
            engine, name = "ollama", _ollama_first_model()
        if not engine:
            return None, None, None
        conf["model"] = {
            "configured": True, "engine": engine, "name": name,
            "tier": tier, "desc": desc,
            "note": "可在球菜单「AI大模型」中手动更换引擎与模型"
        }
        save_conf(conf)
        return engine, name, desc
    except Exception as e:
        print("[fairy] 初始模型自动匹配失败: %s" % e, flush=True)
        return None, None, None


def _say_model_setup(engine, name, desc):
    """首次配置本地模型后播报一句（不阻塞），提示可在球菜单更改。"""
    try:
        _t = "已自动匹配你的设备：%s。使用本地模型「%s」（%s 引擎），可在球菜单 AI 大模型 中更改。" % (
            (desc or "已检测"), name, engine)
        subprocess.Popen([PY, os.path.join(T, "fairy_say.py"), "--async", _t],
                         creationflags=CREATE_NO_WINDOW,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


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
    # ★ 2026-10-10【独立运行改造】把"球现在会走哪条路"直接打出来 ——
    #   不然 DSH 没开时用户只能看到一串"停"，不知道球其实还能活。
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
    for lbl, script, args in [("余额", "balance_watch.py", ["--json"])]:
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
    "ha": ("ha_bridge.py", []),   # Home Assistant 中枢：fairy.py ha discover/act/state/watch
}


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    c, rest = a[0], a[1:]
    conf = load_conf()

    # ★ 在 DSH 里执行 start / restart -> 自动换成脱离方式（见 in_dsh 的说明）
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
        # ★ 2026-10-11【权限说明】首次启动打印权限清单（不给的对应功能禁用）
        try:
            import fairy_perms
            fairy_perms.init_on_first_run(show=True)
        except Exception:
            pass
        # ★ 2026-10-11【初始本地模型引导】首次启动自动匹配设备并配置
        if not rest:
            _e, _mn, _ds = ensure_model_conf(conf)
            if _e:
                _say_model_setup(_e, _mn, _ds)
        # ★ 2026-10-12【游戏模式开局】全套启动时不预拉视觉引擎（vl）：
        #   球启动先进游戏模式会释放 8085，"起了又被杀"反而报错。
        #   需要视觉时（切到 llm/视觉组合）球会自动拉起 8085；手动 start vl 仍可用。
        _full = not rest
        targets = rest or [k for k, v in (conf.get("components") or {}).items()
                           if v and k != "vl"]
        if _full and (conf.get("components") or {}).get("vl"):
            print("· 视觉引擎：待命（游戏模式，切到视觉组合时自动启动）", flush=True)
        rc = 0
        started = []
        _total = len(targets)
        for _i, n in enumerate(targets, 1):
            before = pids_of(COMPONENTS[n]["match"]) if n in COMPONENTS else []
            _nm = COMPONENT_NAMES.get(n, n)
            print("\r%s 启动 %s…" % (_progress_bar(_i - 1, _total), _nm),
                  end="", flush=True)
            r = start_one(n, conf)
            rc |= r
            if r == 0 and n in COMPONENTS and pids_of(COMPONENTS[n]["match"]):
                started.append(n)
            print("\r%s ✓ %s 已启动  " % (_progress_bar(_i, _total), _nm), flush=True)
        print()
        # ★ 2026-10-11：全量启动后播报一句（同类功能合并），不阻塞
        if not rest and started:
            _startup_say(started)
        return rc
    if c == "stop":
        rc = 0
        for n in (rest or list(COMPONENTS.keys())):
            rc |= stop_one(n)
        # ★ 2026-10-11【用户要求】停全部时联动关闭大模型引擎
        if not rest:
            stop_engines()
        return rc
    if c == "restart":
        # ⚠ 不能递归调 main()（会无限重启）—— 直接停完再起
        for n in list(COMPONENTS.keys()):
            stop_one(n)
        stop_engines()
        time.sleep(0.5)
        # ★ 2026-10-11【初始本地模型引导】首次启动自动匹配设备并配置
        _e, _mn, _ds = ensure_model_conf(conf)
        if _e:
            _say_model_setup(_e, _mn, _ds)
        rc = 0
        started = []
        # ★ 2026-10-12【游戏模式开局】全套重启同样不预拉视觉引擎（理由同 start）
        _targets = [(n, v) for n, v in (conf.get("components") or {}).items()
                    if v and n != "vl"]
        if (conf.get("components") or {}).get("vl"):
            print("· 视觉引擎：待命（游戏模式，切到视觉组合时自动启动）", flush=True)
        _total = len(_targets)
        for _i, (_n, _v) in enumerate(_targets, 1):
            _nm = COMPONENT_NAMES.get(_n, _n)
            print("\r%s 启动 %s…" % (_progress_bar(_i - 1, _total), _nm),
                  end="", flush=True)
            r = start_one(_n, conf)
            rc |= r
            if r == 0 and pids_of(COMPONENTS[_n]["match"]):
                started.append(_n)
            print("\r%s ✓ %s 已启动  " % (_progress_bar(_i, _total), _nm), flush=True)
        print()
        if started:
            _startup_say(started)
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
        # ★ 2026-10-14【BUG 修复】原 subprocess.call(..., creationflags=CREATE_NO_WINDOW)
        #   会把子进程 stdout 全部吞掉（实测 0 行输出）—— CREATE_NO_WINDOW 让 console
        #   子进程写进隐藏控制台，父进程管道收不到。改 capture_output 手动写回：
        #   既保留「无黑窗」，又不丢输出。
        try:
            r = subprocess.run([PY, p] + args + rest, cwd=T, capture_output=True,
                               creationflags=CREATE_NO_WINDOW, timeout=900)
        except subprocess.TimeoutExpired:
            print("✗ 命令超时（>15 分钟）：%s" % p, flush=True)
            return 1
        try:
            sys.stdout.buffer.write(r.stdout)
            sys.stdout.buffer.write(r.stderr)
            sys.stdout.buffer.flush()
        except Exception:
            print((r.stdout or b"").decode("utf-8", "replace"), flush=True)
            print((r.stderr or b"").decode("utf-8", "replace"), flush=True)
        return r.returncode
    print("✗ 未知命令: %s" % c)
    print("   跑 `fairy.py help` 看全部命令")
    return 2


if __name__ == "__main__":
    sys.exit(main())
