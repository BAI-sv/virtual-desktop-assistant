# -*- coding: utf-8 -*-
"""
health_check.py —— Fairy 系统体检

【用途】
  1) 关机时自动跑一次（Windows 任务计划，触发源：事件 1074 = 系统关机）
  2) 开机播报时读上次的结果，把问题念出来并给解决方法
  3) 用户/DSH 随时手动跑： py -3.12 health_check.py
     --quiet   只写报告，不打印
     --speak   把结果交给球念出来（POST /api/fairy/say）
     --fast    关机路径用：跳过全部 Windows 查询，只留本地端口/文件判断
     --full    额外查"驱动陈旧 TOP"（多约 0.5~3 秒，**默认跳过**）

【分层】本地层(原有 10 项) + 硬件层 hw_* + 系统核心层 sys_* + 软件层 sw_* + 应用层 app_*
  实测耗时（2026-10-08 10:20 本机，端到端含 Python 启动）：
    默认整跑 7.3 秒 / --full 7.8 秒 / --fast 1.9 秒 —— 都在 15 秒预算内。
  注意：关机批处理 D:\\FAIRY\\tools\\shutdown_check.bat 现在调用的是 `--quiet`（没带 --fast），
  所以关机时走的是 7.3 秒那条；想更快就在那行末尾加 --fast（Windows 关机只等几秒）。

【设计原则】
  · 每一项**独立判定**：某个服务没起不影响其他项
  · 每项都给**具体的解决方法**（不是"出错了"这种废话）
  · 报告写成 JSON 落盘：D:\\FAIRY\\logs\\health_last.json
  · 关机路径必须**快**（--fast 跳过 Windows 层，目标 <3 秒）

【退出码】
  0 = 全好    1 = 有警告    2 = 有严重问题（方便任务计划判断）
"""
import glob
import json
import os
import re
import socket
import subprocess
import sys
import time
import fairy_root

# ★ 2026-10-10【黑窗】0x08000000 = CREATE_NO_WINDOW（不是 0x00000200）
CREATE_NO_WINDOW = fairy_root.CREATE_NO_WINDOW

LOG_DIR = fairy_root.LOGS
REPORT = os.path.join(LOG_DIR, "health_last.json")
REPORT_BAK = os.path.join(LOG_DIR, "health_prev.json")
INDEX = fairy_root.VOICE_INDEX
INDEX_BAK = os.path.join(fairy_root.VOICE, "voice_index.backup.json")
CONF = fairy_root.BRIEFING_CONF
BALL_POS = fairy_root.BALL_POS
BALL_LOG = os.path.join(LOG_DIR, "fairy_ball.log")
# ★★ 2026-10-10 修：球的实际日志叫 fairy_ball.py.log（fairy.py 按【脚本名】重定向 stdout），
#   而且落在 BALL_LOG_DIR（= WORK_LOGS，通常是项目根的 logs）。原来写死 fairy_ball.log，
#   文件根本不存在 -> 这项检查永远报「无日志（首次运行正常）」，把真报错吞了
#   （health_last.json 里 ball_log=ok 却同时 app_log=warn，就是它）。
BALL_LOGS = [
    os.path.join(fairy_root.BALL_LOG_DIR, "fairy_ball.py.log"),
    os.path.join(fairy_root.BALL_LOG_DIR, "fairy_ball.log"),
    os.path.join(LOG_DIR, "fairy_ball.py.log"),
    BALL_LOG,
]
BALL_PY = os.path.join(fairy_root.TOOLS, "fairy_ball.py")
DSH = 19387

# 结果：每项 {id, name, level, detail, fix}
#   level: ok / warn / bad
R = []

# --fast：关机路径用。跳过要查事件日志的慢层（Windows 关机只等几秒）
FAST = '--fast' in sys.argv
# --full：额外查"驱动陈旧 TOP"（唯一超过 3 秒的新增项，默认跳过）
FULL = '--full' in sys.argv
# 桌面球 PID（check_ball 填，应用层 check_app_proc 读）
BALL_PIDS = []


def add(i, name, level, detail, fix=""):
    R.append({"id": i, "name": name, "level": level, "detail": detail, "fix": fix})


def port_open(p, host="127.0.0.1", t=0.6):
    s = socket.socket()
    s.settimeout(t)
    try:
        return s.connect_ex((host, p)) == 0
    finally:
        s.close()


def http_ok(path, timeout=2.5):
    """返回 (ok, body)。★ 2026-10-17：带上 HTTP 状态码，
    这样调用方能区分"接口坏了(500/404)"和"接口活着但要登录(401)"。"""
    import urllib.error
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (DSH, path), timeout=timeout) as r:
            return r.status == 200, r.read()
    except urllib.error.HTTPError as e:
        return False, ("HTTP %d" % e.code).encode()
    except Exception as e:
        return False, str(e)


def ps(cmd, t=8):
    try:
        # ★ 2026-10-10【黑窗】见 fairy_root.NOWIN_FLAGS 的说明。本脚本常被
        #   pythonw / 计划任务 FairyShutdownCheck 起（无控制台）-> 不加就弹窗。
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, timeout=t,
                           creationflags=CREATE_NO_WINDOW)
        return (r.stdout or b"").decode("gbk", "replace")
    except Exception:
        return ""


def ball_running():
    out = ps("Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | "
             "Where-Object {$_.CommandLine -like '*fairy_ball*'} | "
             "Select-Object -ExpandProperty ProcessId")
    return [x.strip() for x in out.splitlines() if x.strip()]


# ---------------------------------------------------------------- 各项检查
def _dsh_where():
    """返回 (port, src, base)。★ 2026-10-17：DSH 0.2.1 起端口是随机的，必须动态找。"""
    try:
        if fairy_root.TOOLS not in sys.path:
            sys.path.insert(0, fairy_root.TOOLS)
        import dsh_port as _dp
        p, src = _dp.find_port()
        if p:
            return p, src, "http://127.0.0.1:%d" % p
    except Exception as e:
        return DSH, "fallback(%s)" % type(e).__name__, ""
    return DSH, "fixed-fallback", ""


def check_dsh():
    port, src, base = _dsh_where()
    name = "DSH 服务(%s)" % port
    if not port_open(port):
        add("dsh", name, "bad", "端口没开",
            "启动 DeepSeek Harness；若已启动，可能是它又换了端口 —— 跑 "
            "py D:\\FAIRY\\tools\\dsh_port.py 看真实端口")
        return
    # 用发现到的端口查插件接口（http_ok 内部用全局 DSH，临时替换）
    old = globals().get("DSH")
    try:
        globals()["DSH"] = port
        ok, body = http_ok("/api/fairy/meta")
    finally:
        globals()["DSH"] = old
    if not ok:
        txt = str(body)[:90]
        if "401" in txt or "403" in txt:
            add("dsh", name, "warn",
                "DSH 在跑（%s），但 Web 要登录态，外部裸调被 401 挡住" % src,
                "这是 DSH 0.2.1 的新鉴权（启动令牌换会话 cookie）；"
                "Fairy 已改为「动态端口 + 本地降级」，DSH 插件直连需另接鉴权")
        else:
            add("dsh", name, "warn", "端口在但 /api/fairy/meta 不通: %s" % txt,
                "插件 dsh-plugin-fairy 可能没加载 —— 重启 DSH")
        return
    try:
        j = json.loads(body.decode("utf-8"))
    except Exception:
        j = {}
    vc = j.get("voiceCount")
    if vc == 0:
        add("dsh", name, "warn", "插件在跑但 voiceCount=0（语料库没读到）",
            "检查 D:\\FAIRY\\voice\\voice_index.json 是否存在且能被解析")
    else:
        add("dsh", name, "ok", "正常（%s），语料 %s 条，帧 %s" % (src or "固定端口", vc, j.get("frames")))


def check_services():
    # 语音：走用户装的 TTS 插件
    # ★ 2026-10-14【误报修复】dsh-plugin-tts 的 /diagnose 实测偶发 5.5s+ 才响应，
    #   原 timeout=4 常把它误判成"接口不通"（实际 status=200）。提到 10s。
    # ★ 2026-10-17：DSH 端口是动态的，且 0.2.1 起要登录态。
    port, src, _base = _dsh_where()
    old = globals().get("DSH")
    try:
        globals()["DSH"] = port
        ok, body = http_ok("/dsh-tts-api/diagnose", timeout=10)
    finally:
        globals()["DSH"] = old
    txt = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else str(body)
    if ok:
        add("tts", "语音合成(TTS 插件)", "ok", "可用（%s）" % src)
    elif "401" in txt or "403" in txt:
        add("tts", "语音合成(TTS 插件)", "warn",
            "插件路由在，但被 DSH 的登录态挡住（HTTP %s）" % ("401" if "401" in txt else "403"),
            "DSH 0.2.1 鉴权所致；球的语音已优先直连 IndexTTS(9881)，这条只影响插件那条兜底")
    elif "404" in txt:
        add("tts", "语音合成(TTS 插件)", "warn",
            "DSH 上找不到 /dsh-tts-api（插件没加载或未启用）",
            "在 DSH 插件里确认 dsh-plugin-tts 已启用，然后重启 DSH")
    else:
        add("tts", "语音合成(TTS 插件)", "warn", "接口不通：%s" % txt[:80],
            "DSH 重启后插件可能没起；或 TTS 插件未启用")
    # ★★ 2026-10-10 晚改口：IndexTTS(9881) 现在是【球的首选音色引擎】（克隆音色），
    #   不再是"用户说不用了、在跑只是占显存"。原提示会劝人把嗓子关掉 —— 与现状相反。
    if port_open(9881):
        add("indextts", "IndexTTS(9881)", "ok", "在跑（球的首选音色引擎，占显存约 2-4GB）",
            "想省显存：球上右键「停止本地运算」；停了语音会退回克隆缓存/云端音色")
    else:
        add("indextts", "IndexTTS(9881)", "warn", "没在跑 —— 球的新句子会退回克隆缓存/云端音色",
            "刚点过「停止本地运算」属正常；否则跑 %s 拉起（守护正常时会自己拉）"
            % os.path.join(fairy_root.TOOLS, "indextts_start.py"))


def check_corpus():
    if not os.path.exists(INDEX):
        add("corpus", "游戏原声语料库", "bad", "voice_index.json 不存在",
            "跑 D:\\FAIRY\\tools\\extend_voice_index.py 重建，或用备份 %s 恢复" % INDEX_BAK)
        return
    try:
        j = json.load(open(INDEX, encoding="utf-8"))
        n = j.get("count") or len(j.get("lines") or [])
    except Exception as e:
        add("corpus", "游戏原声语料库", "bad", "解析失败: %s" % str(e)[:60],
            "用备份恢复：copy %s %s" % (INDEX_BAK, INDEX))
        return
    if n < 1000:
        add("corpus", "游戏原声语料库", "warn", "只有 %d 条（正常应 1600 左右）" % n,
            "跑 extend_voice_index.py 补全")
    else:
        add("corpus", "游戏原声语料库", "ok", "%d 条" % n)
    if not os.path.exists(INDEX_BAK):
        add("corpus_bak", "语料库备份", "warn", "没有备份文件",
            "复制一份：copy %s %s" % (INDEX, INDEX_BAK))


def check_robot():
    """★ 2026-10-15 新增：机器人子系统（tools\\robot）的体检。

    为什么要有这一项：机器人那套有独立的安全层与审计，但它"在不在、干不干净"
    以前没人看 —— 六道闸拦了多少、有没有硬件急停、是不是还在干跑，
    这些是接真机前必须一眼看清的东西。
    """
    rd = os.path.join(fairy_root.TOOLS, "robot")
    if not os.path.isdir(rd):
        add("robot", "机器人子系统", "ok", "未安装（不影响其它功能）")
        return
    # 1) 配置文件在不在
    conf = os.path.join(fairy_root.ROOT, "robot", "robot.json")
    if not os.path.isfile(conf):
        add("robot_conf", "机器人配置", "warn", "没有 robot\\robot.json（用内置默认）",
            "默认全是干跑 + mock 后端，不会动硬件")
    # 2) 安全层状态：优先读快照（不起子进程），读不到就跳过
    backend, dry, estop, wd = "?", None, None, None
    try:
        sys.path.insert(0, rd)
        import robot_root as _rr          # noqa
        backend = _rr.CONF.get("backend") or "mock"
        dry = bool((_rr.CONF.get("safety") or {}).get("dry_run", True))
    except Exception as e:
        add("robot", "机器人子系统", "warn", "加载失败: %s" % str(e)[:60],
            "跑 py D:\\FAIRY\\tools\\robot\\robot_ui.py doctor 看详情")
        return
    # 3) 急停锁存文件：存在且 estop=true 就是"被按下了"
    estop_file = os.path.join(fairy_root.ROOT, "robot", "logs", "estop_state.json")
    estop_on = False
    try:
        if os.path.isfile(estop_file):
            _j = json.load(open(estop_file, encoding="utf-8"))
            estop_on = bool(isinstance(_j, dict) and _j.get("estop"))
    except Exception:
        # 读不了 = 安全层会保守取急停；这里也按"可能有急停"提示
        add("robot_estop", "机器人急停", "warn", "急停状态文件读不了（安全层会保守取急停）",
            "删掉 %s 或跑 robot_ui.py clear <你的名字>" % estop_file)
    if estop_on:
        add("robot_estop", "机器人急停", "warn", "★ 处于急停锁存状态",
            "确认现场安全后：py D:\\FAIRY\\tools\\robot\\robot_ui.py clear <你的名字>")
    else:
        add("robot_estop", "机器人急停", "ok", "未锁存")
    # 4) 干跑 / 真机 + 硬件急停
    if dry:
        add("robot", "机器人子系统", "ok", "后端=%s，干跑中（不会动硬件）" % backend)
    else:
        pin = (_rr.CONF.get("estop") or {}).get("pin_file") or ""
        if pin:
            add("robot", "机器人子系统", "ok", "后端=%s，★真机模式，已配硬件急停" % backend)
        else:
            add("robot", "机器人子系统", "warn",
                "后端=%s，★真机模式但没配硬件急停（estop.pin_file 为空）",
                "机器人身上必须有物理急停开关与板载看门狗 —— 断线时 PC 端一切联锁都失效")
    # 5) 审计：有没有被闸拦过（拦得多说明有人在试危险动作，值得看一眼）
    au = os.path.join(fairy_root.ROOT, "robot", "logs", "robot_audit.jsonl")
    if os.path.isfile(au):
        try:
            blocked = dr = 0
            with open(au, "r", encoding="utf-8", errors="replace") as f:
                for ln in f:
                    if '"decision": "block"' in ln:
                        blocked += 1
                    elif '"decision": "dry-run"' in ln:
                        dr += 1
            add("robot_audit", "机器人审计", "ok",
                "放行干跑 %d 条 / 拦截 %d 条（%s）" % (dr, blocked, os.path.basename(au)),
                "看详情：robot_ui.py events 30")
        except Exception as e:
            add("robot_audit", "机器人审计", "warn", "读不了: %s" % str(e)[:50])
    # 6) ★ 融合状态：机器人有没有接进"设备发现"那条链（device_adapters）
    try:
        if fairy_root.TOOLS not in sys.path:
            sys.path.insert(0, fairy_root.TOOLS)
        import device_adapters as _da
        note = _da.robot_note()
        if "robot" in (_da.ADAPTERS or {}):
            add("robot_link", "机器人已接入设备发现", "ok", "%s（adapter=robot）" % note)
        else:
            add("robot_link", "机器人已接入设备发现", "warn", note,
                "跑 py D:\\FAIRY\\tools\\robot_bridge.py register 看具体报错")
    except Exception as e:
        add("robot_link", "机器人已接入设备发现", "warn", "检查失败: %s" % str(e)[:60])


def check_ball():
    global BALL_PIDS
    pids = ball_running()
    BALL_PIDS = pids
    if not pids:
        add("ball", "桌面球进程", "bad", "没在跑",
            "启动：pythonw D:\\FAIRY\\tools\\fairy_ball.py（或检查开机自启）")
        return
    add("ball", "桌面球进程", "ok", "在跑 (PID %s)" % ",".join(pids))
    # 球日志里有没有崩溃痕迹
    logp = None
    for _p in BALL_LOGS:
        try:
            if os.path.isfile(_p) and (logp is None or os.path.getmtime(_p) > os.path.getmtime(logp)):
                logp = _p
        except Exception:
            continue
    if logp is None:
        add("ball_log", "球运行日志", "ok", "无日志（首次运行正常）")
        return
    try:
        t = open(logp, encoding="utf-8", errors="replace").read()
        tail = t[-4000:]
        # ★ 2026-10-17【时效性】只拿【最近 30 分钟】写过的东西当"当前报错"。
        #   老代码把文件里任何位置的历史报错都报出来 —— 修好之后仍会天天报警
        #   （实测：window_guard 的 10061 是修之前的旧行，却一直被当成本轮故障）。
        import time as _t
        if (_t.time() - os.path.getmtime(logp)) > 1800:
            add("ball_log", "球运行日志", "ok",
                "近 30 分钟没有新日志（最后写入 %s）" % _t.strftime("%H:%M", _t.localtime(os.path.getmtime(logp))))
            return
        # ★ 2026-10-14【误报修复】advisor 的 LLM 降级（本地 LLM 引擎未启用时
        #   内容分析/建议失败 -> 降级为"不说"）是预期行为，含 URLError/Error 字样
        #   会误触发报警；真实 Traceback（崩溃）仍照常报。
        def _advisor_downgrade(l):
            # ★ 2026-10-17：降级提示本身也不该算"报错"（它是说明，不是故障）：
            #   新加的"本地大脑（Ollama 11434）没在跑…先跳过"含 Error 字样会误报。
            #   另外"窗口守护翻译失败 10061"是"本地大脑没开"的另一种表现 —— 球会安静跳过。
            if "[advisor]" in l and ("LLM" in l or "内容分析" in l):
                return True
            if "本地大脑（Ollama" in l or ("没在跑" in l and "先跳过" in l):
                return True
            if "[window_guard]" in l and ("10061" in l or "翻译失败" in l or "审核" in l):
                return True
            if "语音通道全失败" in l:
                return True
            # ★ 2026-10-17：Strata 的 409 是"状态冲突/不用卸"，属正常；已改成
            #   「本地运算已在闲状态」播报，但历史日志里仍有旧格式的一句。
            if "停止失败" in l and "409" in l:
                return True
            return False
        tl = tail.splitlines()
        if "Traceback" in tail or any("Error" in l and not _advisor_downgrade(l) for l in tl):
            last = [l for l in tl if ("Error" in l or "Traceback" in l) and not _advisor_downgrade(l)]
            add("ball_log", "球运行日志", "warn", "日志里有报错：%s" % (last[-1][:70] if last else "?"),
                "看 %s 定位；多数是 TTS/网络取不到，球会自动降级不影响使用" % logp)
        else:
            add("ball_log", "球运行日志", "ok", "无报错（%s）" % os.path.basename(logp))
    except Exception as e:
        # ★ 读不了也算警告：原来这里 except -> ok，等于把故障吞成"正常"
        add("ball_log", "球运行日志", "warn", "日志读不了：%s" % str(e)[:60], "检查 %s 权限/占用" % logp)
    if not os.path.exists(BALL_PY):
        add("ball_src", "球脚本", "bad", "fairy_ball.py 不见了",
            "从 fairy_ball.py 拷回 D:\\FAIRY\\tools\\")


def check_config():
    if not os.path.exists(CONF):
        add("conf", "播报配置", "warn", "briefing.json 不存在（位置未确认）",
            "球上右键→位置 选一个城市确认；或手动建该文件")
        return
    try:
        c = json.load(open(CONF, encoding="utf-8"))
    except Exception as e:
        add("conf", "播报配置", "bad", "解析失败: %s" % str(e)[:60], "删掉重建，或修好 JSON 格式")
        return
    if not c.get("city_confirmed"):
        add("conf", "播报配置", "warn", "位置还没确认过",
            "球上右键→位置 选你的城市")
    else:
        add("conf", "播报配置", "ok", "位置已确认：%s" % c.get("city"))
    if not os.path.exists(BALL_POS):
        add("pos", "球位置存档", "warn", "fairy_ball_pos.json 不存在（球会回到默认位置）",
            "拖动球到想要的位置即可自动保存")


def check_autostart():
    # ★★ 2026-10-17 晚【用户明确要求：不要开机自启 DSH 和 Fairy】
    #   自启有三处，全都要看：
    #     · HKCU Run 的 FairyDSH（fairy_dsh_start.py：Fairy 全体 + DSH）
    #     · HKCU Run 的 FairyWatch（fairy_dsh_watch.py：DSH 一开就拉 Fairy）
    #     · 计划任务 FairyBootstrap / FairyVoice（LogonTrigger 登录即触发）
    #   所以不能再"看到没有 FairyDSH 就报缺少自启项" —— 那是用户主动关的，
    #   报 warn 等于天天狼来了。现在按"三处全关 = 正常（按用户要求关闭）"判定。
    #   （FairyShutdownCheck 是关机事件触发、不是自启，不计入。）
    run_out = ps('Get-ItemProperty "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" | '
                 'Select-Object -Property * | Out-String -Width 300')
    run_on = [k for k in ("FairyDSH", "FairyWatch") if k in (run_out or "")]
    task_on = []
    try:
        r = subprocess.run(["schtasks", "/query", "/fo", "csv", "/nh"],
                           capture_output=True, timeout=15, creationflags=CREATE_NO_WINDOW)
        txt = (r.stdout or b"").decode("gbk", "replace")
        for ln in txt.splitlines():
            for t in ("FairyBootstrap", "FairyVoice"):
                if t in ln and "Ready" in ln:
                    task_on.append(t)
    except Exception:
        pass
    task_on = sorted(set(task_on))
    if not run_on and not task_on:
        add("autostart", "开机自启", "ok",
            "已关闭 —— DSH 和 Fairy 都不会开机自启（Run 无项 · 计划任务已禁用）",
            "想恢复：照 D:\\FAIRY\\logs\\autostart_backup_20261017.txt 末尾的恢复命令执行")
        return
    if run_on and task_on:
        add("autostart", "开机自启", "ok",
            "开启：Run=%s ＋ 计划任务=%s" % ("、".join(run_on), "、".join(task_on)),
            "不想开机自启：删 Run 里的 FairyDSH/FairyWatch，并禁用 FairyBootstrap/FairyVoice")
        return
    add("autostart", "开机自启", "warn",
        "配置不一致：Run=%s，计划任务=%s"
        % ("、".join(run_on) or "无", "、".join(task_on) or "无"),
        "要全关：删 Run 里的 FairyDSH/FairyWatch 并 schtasks /change /tn NAME /disable；"
        "要全开：见 autostart_backup_20261017.txt")


def check_disk():
    for drv in ("C:", "D:"):
        try:
            import shutil as _sh
            u = _sh.disk_usage(drv + "\\")
            free_gb = u.free / (1024 ** 3)
            if free_gb < 5:
                add("disk" + drv[0], "%s 盘剩余空间" % drv, "bad", "只剩 %.1f GB" % free_gb,
                    "清理空间；日志在 D:\\FAIRY\\logs，可删旧文件")
            elif free_gb < 15:
                add("disk" + drv[0], "%s 盘剩余空间" % drv, "warn", "只剩 %.1f GB" % free_gb,
                    "留意清理")
            else:
                add("disk" + drv[0], "%s 盘剩余空间" % drv, "ok", "%.0f GB" % free_gb)
        except Exception:
            pass


def check_pending():
    """有没有跑了一半的活儿（比如补索引没跑完）。"""
    log = os.path.join(LOG_DIR, "extend_index.log")
    if os.path.exists(log):
        try:
            t = open(log, encoding="utf-8", errors="replace").read()
            if "完成：新增" not in t and "没有要补的" not in t:
                add("pending", "未完成的任务", "warn", "补索引好像没跑完",
                    "重跑：py -3.12 D:\\FAIRY\\tools\\extend_voice_index.py")
        except Exception:
            pass




# ==========================================================================
# ==========================================================================
# 全栈扩展：硬件层 / 系统核心层 / 软件层
#
# 【性能设计】所有 Windows 查询**合并成一次 PowerShell 调用**，返回一个 JSON。
#   为什么：PowerShell 每次启动约 0.4~0.6 秒，分 10 次查就是 5 秒以上，
#   而关机路径要求 <8 秒。合并成一次后，整份体检的 Windows 部分 ≈ 2~4 秒。
#   `--fast` 时**完全跳过**这次调用（只留本地端口/文件判断），关机<1 秒。
#
# 【白名单】有些日志是**无害噪音**，实测确认过，不能当问题报：
#   Service Control Manager 7001  = "客户体验改善计划的用户登录通知"，每次启动都写
#   Security-SPP 16385            = 激活服务安排重启失败，反复刷但不影响使用
#   DistributedCOM 10010          = COM 组件超时，很常见
#   Kernel-Boot 153               = "基于虚拟化的安全性 disabled"，这是**正常启动信息**
#   Bonjour Service 100 / Defrag 264
#   → 这些从"日志错误分组"里剔除；磁盘错误查询用 Id 7/11/51，本来就不含 153。
# ==========================================================================

NOISE = (
    "Service Control Manager, 7001",
    "Security-SPP, 16385",
    "DistributedCOM, 10010",
    "Kernel-Boot, 153",
    "Bonjour Service, 100",
    "Defrag, 264",
)

# 一次 PowerShell 调用取回全部系统数据（转成 JSON，UTF-8）
# ★ 每一段各自 try/catch，绝不能再整块包一个 try。
#   注意：整块一个 try 时，`node -v` 抛
#   CommandNotFoundException（本机 PATH 里没有 node）被末尾的 catch 吞掉，
#   于是它**后面所有字段**（sys_err/app_err/unexpected/power/whea/diskerr/
#   minidump/memdmp/reboot/crash_top）全都没赋值，报告一律显示成 0/"无"：
#   实测真值是 6008 意外关机 ×22、WHEA ×11、磁盘错误(Id 7/11/51) ×93、
#   Application Error ×247 —— 全被这条假阴性掩盖。分段后互不影响。
#   ⚠ 补充（2026-10-08 复查）：那 93 条磁盘错误**全部指向 \Device\Harddisk4**，
#     而本机 Get-Disk 只有 0~3 —— 是幽灵设备记录。下方 diskerr 查询已改为按设备过滤。
SYS_JSON = r'''
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$ErrorActionPreference='SilentlyContinue'
$o=[ordered]@{}
try {
  $cpu=Get-CimInstance Win32_Processor | Select-Object -First 1
  $o.cpu_name=("" + $cpu.Name).Trim()
  $o.cpu_cores=$cpu.NumberOfCores
  $o.cpu_threads=$cpu.NumberOfLogicalProcessors
  $o.cpu_load=$cpu.LoadPercentage
} catch {}
try {
  $os=Get-CimInstance Win32_OperatingSystem
  $o.ram_total=[math]::Round($os.TotalVisibleMemorySize/1MB,1)
  $o.ram_free=[math]::Round($os.FreePhysicalMemory/1MB,1)
  $o.ram_used_pct=[int](100-($os.FreePhysicalMemory/$os.TotalVisibleMemorySize*100))
  $mi=@(Get-CimInstance Win32_PhysicalMemory)
  $o.ram_sticks=$mi.Count
  if($mi.Count -gt 0){ $o.ram_speed=$mi[0].Speed; $o.ram_cfg=$mi[0].ConfiguredClockSpeed }
} catch {}
try {
  $g=Get-CimInstance Win32_VideoController | Where-Object {$_.Name -notlike '*Virtual*' -and $_.Name -notlike '*Basic*'} | Select-Object -First 1
  $o.gpu=("" + $g.Name).Trim()
  $o.gpu_drv=("" + $g.DriverVersion).Trim()
  if($g.DriverDate){ $o.gpu_date=$g.DriverDate.ToString('yyyy-MM-dd') }
} catch {}
try {
  $o.disks=@(Get-PhysicalDisk | ForEach-Object { [ordered]@{n=("" + $_.FriendlyName); h=("" + $_.HealthStatus); m=("" + $_.MediaType); gb=[math]::Round($_.Size/1GB)} })
} catch {}
try {
  $o.smart=@(Get-PhysicalDisk | ForEach-Object { $d=$_
    $c=$d|Get-StorageReliabilityCounter
    if($c){ [ordered]@{n=("" + $d.FriendlyName); t=$c.Temperature; poh=$c.PowerOnHours; rt=$c.ReadErrorsTotal; ru=$c.ReadErrorsUncorrected; wr=$c.WriteErrorsTotal; we=$c.Wear} } })
} catch {}
try {
  $o.badpnp=@(Get-PnpDevice -PresentOnly | Where-Object {$_.Status -ne 'OK' -and $_.Status -ne 'Unknown'} | Select-Object -First 6 -ExpandProperty FriendlyName)
} catch {}
try { $o.py=("" + (py -3.12 -V 2>&1 | Out-String)).Trim() } catch { $o.py="取不到" }
try {
  if(Get-Command node -ErrorAction SilentlyContinue){ $o.node=("" + (node -v 2>&1 | Out-String)).Trim() } else { $o.node="取不到" }
} catch { $o.node="取不到" }
try {
  $o.proc=@(Get-Process | Where-Object {$_.ProcessName -match 'fairy|DeepSeek|llama|ollama'} | ForEach-Object { [ordered]@{n=("" + $_.ProcessName); p=$_.Id} })
} catch {}
try {
  $s7=(Get-Date).AddDays(-7)
  $o.sys_err=@(Get-WinEvent -FilterHashtable @{LogName='System';Level=1,2;StartTime=$s7} | Group-Object ProviderName,Id | Sort-Object Count -Descending | Select-Object -First 12 | ForEach-Object { [ordered]@{k=("" + $_.Name); n=$_.Count} })
} catch {}
try {
  $o.app_err=@(Get-WinEvent -FilterHashtable @{LogName='Application';Level=1,2;StartTime=$s7} | Group-Object ProviderName,Id | Sort-Object Count -Descending | Select-Object -First 12 | ForEach-Object { [ordered]@{k=("" + $_.Name); n=$_.Count} })
} catch {}
try { $o.unexpected=@(Get-WinEvent -FilterHashtable @{LogName='System';Id=6008;StartTime=(Get-Date).AddDays(-30)}).Count } catch {}
try { $o.power=@(Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-Kernel-Power';Id=41,42;StartTime=(Get-Date).AddDays(-60)}).Count } catch {}
try {
  $w=@(Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-WHEA-Logger';StartTime=(Get-Date).AddDays(-60)})
  $o.whea=$w.Count
  if($w.Count -gt 0){ $o.whea_last=$w[0].TimeCreated.ToString('yyyy-MM-dd HH:mm') }
} catch {}
# ★ 磁盘类错误必须【按设备过滤】，否则会误报狼来了。
#   2026-10-08 实测：93 条 Id 7/11/51 全部指向 \Device\Harddisk4，
#   而本机 Get-Disk 的 Number 只有 0~3 —— Harddisk4 根本不存在
#   （多半是当时插过的移动盘/虚拟盘）。不过滤的话每次都报
#   "✗ 严重 磁盘错误事件…立刻备份并准备换盘"，既误导又让真问题失去可信度。
#   分不出设备的事件【保守算真实错误】，绝不静默丢弃。
try {
  $exist=@(Get-Disk -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Number)
  $o.disknum=($exist -join ',')
  $ev=@(Get-WinEvent -FilterHashtable @{LogName='System';Id=7,11,51;StartTime=(Get-Date).AddDays(-30)} -ErrorAction SilentlyContinue)
  $o.diskerr_all=$ev.Count
  $real=0; $ghost=0; $gdev=@{}
  foreach($e in $ev){
    $dev=""
    try { if(("" + $e.Message) -match '\\Device\\Harddisk(\d+)'){ $dev=$matches[1] } } catch {}
    if($dev -eq ""){ $real++; continue }
    if($exist -contains [int]$dev){ $real++ } else { $ghost++; $gdev[$dev]=$true }
  }
  $o.diskerr=$real
  $o.diskerr_ghost=$ghost
  $o.diskerr_ghostdev=(($gdev.Keys | Sort-Object) -join ',')
} catch {}
try { $o.minidump=@(Get-ChildItem "C:\Windows\Minidump\*.dmp").Count } catch {}
try { $o.memdmp=(Test-Path "C:\Windows\MEMORY.DMP") } catch {}
try {
  $rb=@()
  foreach($k in 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending','HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired','HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootInProgress'){ if(Test-Path $k){ $rb += ($k -split '\\')[-1] } }
  $pf=(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager' -Name PendingFileRenameOperations).PendingFileRenameOperations
  if($pf){ $rb += ("PendingFileRename×" + $pf.Count) }
  $o.reboot=($rb -join '、')
} catch { $o.reboot="" }
try {
  $o.crash_top=@(Get-WinEvent -FilterHashtable @{LogName='Application';ProviderName='Application Error';Id=1000;StartTime=(Get-Date).AddDays(-30)} | ForEach-Object { if ($_.Message -match '应用程序名称:\s*([^，,\r\n]+)') { $matches[1].Trim() } } | Group-Object | Sort-Object Count -Descending | Select-Object -First 3 | ForEach-Object { [ordered]@{n=("" + $_.Name); c=$_.Count} })
} catch {}
DRV_SLOT
$o | ConvertTo-Json -Depth 5 -Compress
'''

# --full 才追加：第三方驱动清单（Win32_PnPSignedDriver 约 3 秒，最慢的新增项）
#   DriverDate 在 CIM 里已经是 DateTime（实测 GetType()=System.DateTime），
#   直接排序即可；早先按 DMTF 字符串解析会抛"参数超出有效值范围"。
#   ★ 注意 `Sort-Object y` 对 [ordered]@{} 这种字典**不生效**（实测排不出来，
#     顺序还是枚举顺序），必须写成 `Sort-Object {$_['y']}` 才真的按年份排。
DRV_JSON = r'''
try {
  $o.drv=@(Get-CimInstance Win32_PnPSignedDriver | Where-Object {$_.DeviceName -and $_.DriverProviderName -ne 'Microsoft'} | ForEach-Object {
    $dt=$null
    try { $dt=[datetime]$_.DriverDate } catch {}
    if($dt -and $dt.Year -gt 2005){ [ordered]@{n=("" + $_.DeviceName); v=("" + $_.DriverVersion); d=$dt.ToString('yyyy-MM-dd'); y=$dt.Year} }
  } | Sort-Object {$_['y']} | Select-Object -First 6)
} catch {}
'''

SYS = {}      # 一次取回的系统数据；--fast 时为空
SYS_ERR = ""  # 取不到的原因


def ps_json(script, t=45):
    """跑 PowerShell 并把 stdout 当 JSON 解析。失败返回 (None, 原因)。"""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                           capture_output=True, timeout=t,
                           creationflags=CREATE_NO_WINDOW)
        s = (r.stdout or b"").decode("utf-8", "replace").strip()
        i = s.find("{")
        if i < 0:
            return None, "PowerShell 没有返回 JSON（可能被策略限制）"
        return json.loads(s[i:]), ""
    except subprocess.TimeoutExpired:
        return None, "PowerShell 查询超时（>%ds）" % t
    except Exception as e:
        return None, "%s: %s" % (type(e).__name__, str(e)[:60])


def _safe(fn):
    """跑一个检查函数，出错就记一条警告（不炸整体）。"""
    try:
        fn()
    except Exception as e:
        add(fn.__name__, fn.__name__, "warn", "检查本身出错: %s" % str(e)[:60], "忽略即可")


def _load_sys(t=45):
    """在线程里装载系统数据（和本地检查并行跑，省时间）。"""
    global SYS, SYS_ERR
    SYS, SYS_ERR = ps_json(SYS_JSON.replace("DRV_SLOT", DRV_JSON if FULL else ""), t=t)
    SYS = SYS or {}


def _sys_ok(tag):
    """系统数据是否可用。--fast 时不算问题（故意不查）。"""
    if SYS:
        return True
    if FAST:
        return False
    add(tag, "系统数据采集", "warn", "取不到 —— " + (SYS_ERR or "未知原因"),
        "手动跑一次 py -3.12 health_check.py 看报错；确认 PowerShell 没被组策略限制")
    return False


def _noise(key):
    return any(x in key for x in NOISE)


# ------------------------------------------------------------------ 硬件层
def check_hardware():
    """硬件层：CPU / 内存(含 XMP) / 显卡 / 物理盘。"""
    if not _sys_ok("hw"):
        return
    # CPU
    name = (SYS.get("cpu_name") or "").strip()
    cores, thr = SYS.get("cpu_cores"), SYS.get("cpu_threads")
    load = SYS.get("cpu_load")
    lv = "warn" if (isinstance(load, int) and load >= 90) else "ok"
    add("hw_cpu", "CPU", lv, "%s · %s核%s线程 · 负载 %s%%" % (name[:34], cores, thr, load),
        "持续 90%+ 时查是不是有失控进程" if lv != "ok" else "")
    # 内存占用
    tot, free, pct = SYS.get("ram_total"), SYS.get("ram_free"), SYS.get("ram_used_pct")
    lv = "bad" if (isinstance(pct, int) and pct >= 97) else ("warn" if (isinstance(pct, int) and pct >= 90) else "ok")
    add("hw_ram", "内存占用", lv, "%s%%（已用 %.1f GB / 共 %s GB，%s 条）" % (pct, (tot or 0) - (free or 0), tot, SYS.get("ram_sticks")),
        "接近满载：关掉不用的程序，或查内存泄漏" if lv != "ok" else "")
    # ★ 内存频率：Speed 是标称，ConfiguredClockSpeed 是实跑。不等 = BIOS 没开 XMP/DOCP
    spd, cfg = SYS.get("ram_speed"), SYS.get("ram_cfg")
    if spd and cfg:
        if str(spd) == str(cfg):
            add("hw_ram_xmp", "内存频率", "ok", "实跑 %s MHz = 标称 %s MHz（XMP/DOCP 已开）" % (cfg, spd))
        else:
            add("hw_ram_xmp", "内存频率", "warn", "实跑 %s MHz < 标称 %s MHz" % (cfg, spd),
                "进 BIOS 打开 XMP/DOCP（微星叫 A-XMP）；不开会白丢约 30%% 内存带宽")
    # 显卡
    add("hw_gpu", "显卡", "ok", "%s · 驱动 %s（%s）" % (SYS.get("gpu"), SYS.get("gpu_drv"), SYS.get("gpu_date")))
    # 物理盘
    disks = SYS.get("disks") or []
    bad = [d for d in disks if (d.get("h") or "") != "Healthy"]
    if bad:
        add("hw_disk", "物理磁盘", "bad", "%d 块不健康：%s" % (len(bad), "、".join(d.get("n", "?")[:22] for d in bad)),
            "立刻备份重要数据，用 CrystalDiskInfo 看 SMART 详情")
    else:
        add("hw_disk", "物理磁盘", "ok", "%d 块全部 Healthy（%s）" % (
            len(disks), "、".join("%s %sG" % ((d.get("m") or "?")[:4], d.get("gb")) for d in disks[:4])))


# -------------------------------------------------------------- 系统核心层
def check_system():
    """系统核心层：日志错误分组（带白名单）/ 意外关机 / 异常断电 / WHEA / 蓝屏 / 待重启 / 磁盘错误。"""
    if not _sys_ok("sys"):
        return
    # 日志错误分组：剔除白名单后，取最严重的那组
    for tag, key, label in (("sys_err", "sys_err", "系统日志错误"), ("app_err", "app_err", "应用日志错误")):
        groups = [g for g in (SYS.get(key) or []) if not _noise(g.get("k") or "")]
        if groups:
            top3 = "、".join("%s ×%s" % (g.get("k"), g.get("n")) for g in groups[:3])
            more = "（共 %d 组，已剔除白名单噪音）" % len(groups) if len(groups) > 3 else ""
            add(tag, label, "warn", "近7天 TOP：%s%s" % (top3, more),
                "按 Provider+Id 在事件查看器里搜这条；先确认是不是白名单里那类无害噪音")
        else:
            add(tag, label, "ok", "近7天无（非白名单）严重/错误事件")

    # ★ WHEA 硬件错误 —— 意外关机最硬的证据
    n = SYS.get("whea") or 0
    if n:
        add("sys_whea", "★WHEA 硬件错误", "bad",
            "%d 条（60天）最近 %s" % (n, SYS.get("whea_last") or "?"),
            "硬件级报错（Cache Hierarchy Error + 多核心 = CPU 不稳）。"
            "排查顺序【2026-10-08 已上网核实，非凭经验】："
            "① 回刷刷机前的旧 BIOS，或 Clear CMOS 后 Load Optimized Defaults —— 最快验证因果；"
            "② PCIe 插槽 Gen4→Gen3（新显卡对链路更敏感，X570 常见）；"
            "③ SOC/VDDCR 电压回 Auto（有时“更高”反而稳，别固定低压）；"
            "④ 测电源（老化电源在负载尖峰会不稳）；"
            "⑤ 仍报 → 送保 CPU 或试换显卡。"
            "⚠ 判据：BIOS 全默认下必须稳定，做不到就是硬件缺陷")
    else:
        add("sys_whea", "WHEA 硬件错误", "ok", "60 天 0 条（无硬件级报错）")

    # 异常断电
    n = SYS.get("power") or 0
    add("sys_power", "异常断电", "bad" if n else "ok", "%d 次（60天）" % n,
        "有 Kernel-Power 41/42 说明断电/电源/线材问题，查插座与电源" if n else "")

    # 意外关机
    n = SYS.get("unexpected") or 0
    add("sys_shutdown", "意外关机", "warn" if n else "ok", "%d 次（30天）" % n,
        "非正常关机。对照 WHEA：若同时有硬件错误，先修 CPU/内存稳定性" if n else "")

    # 磁盘类错误（7/11/51 = 坏道/IO 重试）
    #   ★ 分两类：落在【现有磁盘】上的才算严重；指向"当前不存在的设备"的降为通过并注明，
    #     否则每次开机都报一条假的"立刻备份准备换盘"（狼来了）。
    n = SYS.get("diskerr") or 0
    g = SYS.get("diskerr_ghost") or 0
    gdev = (SYS.get("diskerr_ghostdev") or "").strip()
    dnum = (SYS.get("disknum") or "").strip()
    if n:
        add("sys_diskerr", "磁盘错误事件", "bad",
            "在【现有磁盘 %s】上共 %d 条（30天）" % (dnum or "?", n),
            "有坏道/IO 重试：立刻备份并准备换盘（先换 SATA 线试）")
    elif g:
        add("sys_diskerr", "磁盘错误事件", "ok",
            "现有磁盘 %s 上 0 条；另有 %d 条指向不存在的设备 Harddisk%s（旧记录，已忽略）"
            % (dnum or "?", g, gdev or "?"))
    else:
        add("sys_diskerr", "磁盘错误事件", "ok", "Id 7/11/51 共 0 条（30天）")

    # 蓝屏
    md, mem = SYS.get("minidump") or 0, SYS.get("memdmp")
    if md or mem:
        add("sys_bsod", "蓝屏转储", "warn", "%d 个 minidump%s" % (md, " + MEMORY.DMP" if mem else ""),
            "用 BlueScreenView 看是哪个驱动引起的；结合 WHEA 判断硬件还是驱动")
    else:
        add("sys_bsod", "蓝屏转储", "ok", "无 minidump、无 MEMORY.DMP")

    # 待重启（三类标记都查：CBS 组件更新 / WU 自动更新 / 待替换文件）
    rb = (SYS.get("reboot") or "").strip()
    add("sys_reboot", "待重启更新", "warn" if rb else "ok",
        ("待重启标记：" + rb) if rb else "无（CBS / WindowsUpdate / 待替换文件 都没有）",
        "尽快重启让补丁或驱动替换生效；若 PendingFileRename 长期不清，查是不是某软件反复写它" if rb else "")


# ------------------------------------------------------------------ 软件层
def check_software():
    """软件层：异常 PnP 设备 / 磁盘 SMART / 运行环境版本。"""
    if not _sys_ok("soft"):
        return
    badpnp = [x for x in (SYS.get("badpnp") or []) if x]
    if badpnp:
        add("sw_pnp", "异常设备(驱动)", "warn", "%d 个非 OK：%s" % (len(badpnp), "、".join(x[:20] for x in badpnp[:3])),
            "设备管理器看黄色感叹号；按硬件 ID 去官网下驱动重装")
    else:
        add("sw_pnp", "异常设备(驱动)", "ok", "无黄色感叹号设备")

    # SMART（这项最值钱：能提前发现盘要坏）
    sm = SYS.get("smart") or []
    if not sm:
        add("sw_smart", "磁盘 SMART", "warn", "取不到（需管理员权限或盘不支持）",
            "用管理员身份跑一次；或装 CrystalDiskInfo 看")
    for d in sm:
        nm = (d.get("n") or "?")[:24]
        ru, rt, poh, t = d.get("ru") or 0, d.get("rt") or 0, d.get("poh"), d.get("t")
        if ru and int(ru) > 0:
            add("sw_smart_" + nm, nm, "bad", "未纠正读错误 %s 个 · 通电 %s 小时" % (ru, poh),
                "立刻备份重要数据并换盘（未纠正=数据已面临风险）")
        elif rt and int(rt) > 50:
            add("sw_smart_" + nm, nm, "warn", "%s 个读错误(已全部纠正) · 通电 %s 小时 · %s°C" % (rt, poh, t),
                "老化信号：别把孤本重要数据放这块盘，建议每月复查一次")
        else:
            add("sw_smart_" + nm, nm, "ok", "%s°C · 通电 %s 小时 · 无读错误" % (t, poh))

    py = (SYS.get("py") or "").strip()
    add("sw_py", "Python", "ok" if "3.12" in py else "warn", py[:40] or "取不到",
        "Fairy 全栈依赖 Python 3.12，版本不对会各种报错" if "3.12" not in py else "")
    nd = (SYS.get("node") or "").strip()
    # ★ 降级为 ok：DSH 自带 node（在同级 runtime 目录里），系统 PATH 里没有是【正常状态】。
    #   先前每次体检都报一条"警告 Node 取不到"，属于已知无害的假警告。
    add("sw_node", "Node", "ok",
        nd[:30] if nd.startswith("v") else "不在系统 PATH（DSH 自带，不影响运行）")


def check_app_crashes():
    """软件层：近 30 天崩溃最多的应用。"""
    if not _sys_ok("crash"):
        return
    top = SYS.get("crash_top") or []
    if not top:
        add("app_crash", "应用崩溃", "ok", "近 30 天无记录")
        return
    t0 = top[0]
    cnt = int(t0.get("c") or 0)
    lv = "bad" if cnt >= 50 else ("warn" if cnt >= 10 else "ok")
    add("app_crash", "应用崩溃 TOP1", lv, "%s 崩了 %d 次（30天）" % ((t0.get("n") or "?")[:40], cnt),
        "反复崩溃的程序建议更新/重装；若错误模块是 onnxruntime 类，"
        "多为推理库版本冲突，可禁用它的 AI 功能止血" if lv != "ok" else "")


# ------------------------------------------------------------------ 应用层
def check_app_proc():
    """应用层：关键进程在不在（DSH 主程序 / 桌面球 / 本地大脑）。"""
    if not _sys_ok("app"):
        return
    names = [(p.get("n") or "") for p in (SYS.get("proc") or [])]
    dsh = sum(1 for n in names if "DeepSeek" in n or n.lower().startswith("dsh"))
    brain = sum(1 for n in names if "llama" in n.lower() or "ollama" in n.lower())
    if dsh == 0:
        add("app_proc", "关键进程", "bad", "DSH 主程序进程不在（桌面球 ×%d、本地大脑 ×%d）" % (len(BALL_PIDS), brain),
            "启动 DeepSeek Harness（开机自启项见上面「开机自启」）；端口在但进程名对不上时可忽略")
    else:
        add("app_proc", "关键进程", "ok",
            "DSH 主程序 ×%d · 桌面球 ×%d · 本地大脑 ×%d（本地大脑按需启动，没起不影响 DSH 云端）"
            % (dsh, len(BALL_PIDS), brain))


LOG_ERR = re.compile(r"Traceback|Error|Exception|CRITICAL|错误|失败|拒绝连接|WinError", re.I)
# 名字像日志但不是【运行日志】的文件：AI 生成的文本里天然有"错误/Error"字样，会造成误报
LOG_SKIP = ("ai_opinion", "second_opinion", "opinion_", "mce_amd", "cpuz_report",
            # ★ 2026-10-14：deletions.log 是删除/操作审计，内容含"错误说法"等词，非运行报错
            "deletions")
# 已停用组件的残留日志：IndexTTS 已不再使用（语音改走 DSH 的 TTS 插件），
# 但 indextts_out.log 里会**永远**留着 48 行真的 RuntimeError。
# 每次体检都报它 = 狼来了（和 Node 那条同类）。仅这两条，不做通用排除。
LOG_SKIP_STALE = ("indextts_out", "indextts_err")


def _read_text(p):
    """日志编码不定（有 UTF-8 也有 GBK），先 UTF-8 再 GBK。"""
    b = open(p, "rb").read()
    try:
        return b.decode("utf-8")
    except Exception:
        return b.decode("gbk", "replace")


def check_app_log():
    """应用层：Fairy 自己的日志里有没有报错（只看近 7 天的 *.log，不碰 .txt 试验输出）。

    ★ 排除清单：有些文件名字是 .log，内容却是【AI 生成的文本】而不是运行日志 ——
      实测 `ai_opinion.log` / `second_opinion_*.log` 里写的是大模型对硬件问题的分析意见，
      里面自然会出现"错误/Error"这类词，于是被误当成"Fairy 日志报错"报警。
    """
    newest, scanned, stale = None, 0, 0
    for p in glob.glob(os.path.join(LOG_DIR, "*.log")):
        base = os.path.basename(p).lower()
        if any(k in base for k in LOG_SKIP):
            continue
        if any(k in base for k in LOG_SKIP_STALE):
            stale += 1
            continue
        try:
            if time.time() - os.path.getmtime(p) > 7 * 86400:
                continue
            # ★ 2026-10-17：降级/说明类日志不算报错（否则天天狼来了）：
            #   "本地大脑（Ollama …）没在跑，建议/分析先跳过" 是提示，不是故障。
            def _is_benign(l):
                if "本地大脑（Ollama" in l or ("没在跑" in l and "先跳过" in l):
                    return True
                if "[advisor]" in l and ("LLM" in l or "内容分析" in l):
                    return True
                if "[window_guard]" in l and ("10061" in l or "翻译失败" in l or "审核" in l):
                    return True
                if "语音通道全失败" in l:
                    return True
                # ★ 2026-10-17：HTTP 小服务的"客户端断开"是常态，不是故障
                #   （探活、超时、浏览器刷新都会让服务端看到 10054 / 10053 / BrokenPipe）
                if any(k in l for k in ("ConnectionResetError", "ConnectionAbortedError",
                                        "BrokenPipeError", "WinError 10054", "WinError 10053")):
                    return True
                # ★ 2026-10-17：Strata 的 409 = 状态冲突/无需卸载，属正常；
                #   已改成「本地运算已在闲状态」播报，历史日志里的旧格式也一并放过。
                if "停止失败" in l and "409" in l:
                    return True
                # fairy_ui 的「DSH 的 TTS 插件不可用/DSH 不可用」是降级说明，不是故障
                if "[tts]" in l and "DSH" in l:
                    return True
                # ★ 2026-10-18：fairy_ui 启动时探上游（Strata 8080），连不上照样启动 ——
                #   它自己就写着"界面会显示错误，但代理照样启动"，这是说明不是故障。
                if "上游状态" in l:
                    return True
                # socketserver 处理请求时客户端提前断开，就是这一句开头
                if "Exception occurred during processing of request" in l:
                    return True
                # ★ 2026-10-17：音色守护在 IndexTTS 冷启动期间的"9881 不可用（第 N 次）"
                #   是它的正常工作日志（它随后就把 9881 拉起来了），不是故障。
                if "[guard]" in l and ("9881 不可用" in l or "当前状态：离线" in l or "已后台拉起" in l):
                    return True
                return False

            # ★ 2026-10-17【Traceback 要连体看】Python 的 traceback 里，
            #   真正的原因在最后一行（ConnectionResetError 等），前面那行
            #   "Traceback (most recent call last):" 本身不含异常类型。
            #   所以：把一个 Traceback 块整体判为 benign 的条件是
            #   —— 它后面 12 行内出现的是连接类异常（客户端断开/探活超时）。
            _lines_all = _read_text(p).splitlines()
            _skip_idx = set()
            _BENIGN_EXC = ("ConnectionResetError", "ConnectionAbortedError",
                           "BrokenPipeError", "WinError 10054", "WinError 10053")
            _i = 0
            while _i < len(_lines_all):
                _l = _lines_all[_i]
                # 一个异常块可能从 "Exception occurred ..." 或 "Traceback" 开始；
                # 往后找【不以空白开头】的那行（就是异常类型行，如 ConnectionResetError: ...）
                if "Traceback (most recent call last)" in _l or "Exception occurred during processing" in _l:
                    _j = _i + 1
                    _exc = ""
                    while _j < len(_lines_all) and _j < _i + 60:
                        _c = _lines_all[_j]
                        if _c and not _c[0].isspace() and ":" in _c:
                            _exc = _c
                            break
                        _j += 1
                    if _exc and any(k in _exc for k in _BENIGN_EXC):
                        _skip_idx.update(range(_i, min(len(_lines_all), _j + 1)))
                        _i = _j + 1
                        continue
                _i += 1

            def _is_benign_ctx(i, l):
                return i in _skip_idx or _is_benign(l)

            hits = [_l.strip() for _i, _l in enumerate(_lines_all)
                    if LOG_ERR.search(_l) and not _is_benign_ctx(_i, _l)]
            # ★ 2026-10-17【只认最后一段】日志里可能既有历史崩溃、又有本次正常运行。
            #   看最后一次 Traceback/致命错误之后有没有"成功"字样；有就不算当前故障。
            #   （实测：strata_server.log 里 29 行旧 Traceback 一直让体检报错，
            #     而 Strata 其实正在正常加载、只是要几分钟。）
            _txt = _lines_all
            _last_bad = -1
            for _i, _l in enumerate(_txt):
                if LOG_ERR.search(_l) and not _is_benign_ctx(_i, _l):
                    _last_bad = _i
            if _last_bad >= 0:
                _after = _txt[_last_bad + 1:]
                _good = ("started", "ready", "ok", "正常", "已就绪", "listening")
                if any(any(k in _l.lower() for k in _good) for _l in _after):
                    hits = []          # 之后又正常起来了 -> 历史报错，不算当前故障
        except Exception:
            continue
        scanned += 1
        if hits and (newest is None or os.path.getmtime(p) > newest[0]):
            newest = (os.path.getmtime(p), p, len(hits), hits[-1][:70])
    _stale_note = "；已跳过 %d 个停用组件残留日志（IndexTTS）" % stale if stale else ""
    if newest is None:
        add("app_log", "Fairy 日志报错", "ok",
            "近 7 天 %d 个 .log 无报错%s" % (scanned, _stale_note))
    else:
        add("app_log", "Fairy 日志报错", "warn",
            "%s：%d 行报错，最后一行「%s」" % (os.path.basename(newest[1]), newest[2], newest[3]),
            "看 %s 定位。★ 2026-10-17 后：Ollama/Strata 没在跑时这类连接报错属正常降级"
            "（球已改为本地优先，不再依赖 DSH）；"
            "要启用本地大脑：启动 Ollama 或 py D:\\FAIRY\\tools\\strata_start.py" % newest[1])


def check_drivers():
    """软件层（--full）：第三方驱动陈旧 TOP。默认跳过——这是最慢的新增项（约 3 秒）。"""
    if not FULL:
        return
    if not _sys_ok("drv"):
        return
    drv = [d for d in (SYS.get("drv") or []) if d.get("d")]
    if not drv:
        add("sw_drv", "驱动陈旧 TOP", "warn", "取不到第三方驱动清单（WMI 受限或需要管理员）",
            "管理员身份跑：py -3.12 D:\\FAIRY\\tools\\health_check.py --full")
        return
    # 不信任 PowerShell 那边的顺序，这里再取一次最小的年份（双保险）
    oldest = min(drv, key=lambda d: int(d.get("y") or 9999))
    age = max(0, int(time.strftime("%Y")) - int(oldest.get("y") or time.strftime("%Y")))
    lst = "、".join("%s %s" % ((d.get("n") or "?")[:20], d.get("d")) for d in drv[:3])
    if age >= 5:
        add("sw_drv", "驱动陈旧 TOP", "warn",
            "最旧 %d 年：%s（共 %d 个第三方驱动）" % (age, lst, len(drv)),
            "去主板/网卡/声卡官网下新驱动；老驱动的典型症状是偶发断网、设备消失、掉盘")
    else:
        add("sw_drv", "驱动陈旧 TOP", "ok",
            "最旧 %d 年：%s（共 %d 个第三方驱动）" % (age, lst, len(drv)))


# ---------------------------------------------------------------- 主流程
def main():
    global SYS, SYS_ERR
    t0 = time.time()
    os.makedirs(LOG_DIR, exist_ok=True)

    # 【并行】把唯一那次 PowerShell 查询丢到线程里，主线程同时跑本地检查。
    #   PowerShell 查询约 6~7 秒（事件日志聚合是主要开销），本地检查约 1~2 秒，
    #   两者重叠后总耗时 ≈ PowerShell 那次，而不是相加。
    import threading
    th = None
    if not FAST:
        th = threading.Thread(target=_load_sys, kwargs={"t": 45}, daemon=True)
        th.start()

    # 本地快检查：彼此**互不依赖**，所以全部并行开线程。
    #   （慢点在于 check_ball / check_autostart 各起一次 PowerShell、
    #     check_services 要跑 TTS 自检约 1 秒 —— 串起来就是 4~5 秒，并行后≈最慢那个）
    #   R.append 在 CPython 里是原子的，多线程写这个 list 安全。
    local = (check_dsh, check_services, check_corpus, check_ball,
             check_robot,                      # ★ 2026-10-15 新增：机器人子系统
             check_config, check_autostart, check_disk, check_pending)
    ths = []
    for fn in local:
        th2 = threading.Thread(target=_safe, args=(fn,), daemon=True)
        th2.start()
        ths.append(th2)

    # 等系统数据到位（它和上面这些是并行跑的）
    if th is not None:
        th.join(timeout=50)
    for fn in (check_hardware, check_software, check_system, check_app_crashes, check_drivers):
        _safe(fn)
    for th2 in ths:
        th2.join(timeout=60)
    # 这两项依赖前面的结果（桌面球 PID / 系统数据），本身不再起 PowerShell，很便宜
    for fn in (check_app_proc, check_app_log):
        _safe(fn)

    bad = [x for x in R if x["level"] == "bad"]
    warn = [x for x in R if x["level"] == "warn"]
    layers = {"hw": "硬件层", "sys": "系统核心层", "sw": "软件层", "app": "应用层"}
    rep = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed": round(time.time() - t0, 2),
        "fast": FAST,
        "summary": {"total": len(R), "ok": len(R) - len(bad) - len(warn),
                    "warn": len(warn), "bad": len(bad)},
        "items": R,
    }
    if os.path.exists(REPORT):
        try:
            os.replace(REPORT, REPORT_BAK)
        except Exception:
            pass
    # ★ 原子写入：先写 .tmp 再 os.replace。
    #   直接覆盖时，球正好在读到一半会 JSONDecodeError → 静默不播报。
    #   失败要打印，不许静默（静默兜底会把 bug 藏起来）。
    tmp = REPORT + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
        os.replace(tmp, REPORT)
    except Exception as e:
        print("[health] 报告写入失败: %s" % e, flush=True)

    if "--quiet" not in sys.argv:
        # ★ 输出编码：stdout 被重定向（任务计划、管道、抓日志）时 Python 用 GBK，
        #   打印 "✗" 会直接 UnicodeEncodeError 崩掉。
        #   控制台里 Python 本来就是 utf-8，这里 reconfigure 是安全的。
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        print("Fairy 全栈体检  %s  （%.2fs%s）" % (rep["time"], rep["elapsed"], "  快模式" if FAST else ""))
        # 严重和警告集中列在前面，通过项按层汇总
        for lv, tag in (("bad", "✗ 严重"), ("warn", "! 警告")):
            for x in R:
                if x["level"] == lv:
                    print("  %s  %s：%s" % (tag, x["name"], x["detail"]))
                    if x["fix"]:
                        print("        解决办法：%s" % x["fix"])
        # 分层小计必须自洽：硬件+系统+软件+应用+本地 = 总数
        #   （先前只列了 4 个新层，打印 23 而总数 32 —— 漏了原有的 10 项本地层）
        _other = sum(1 for x in R if not any(x["id"].startswith(k) for k in layers))
        print("  " + " · ".join("%s %d 项" % (v, sum(1 for x in R if x["id"].startswith(k)))
                                for k, v in layers.items())
              + " · 本地层 %d 项" % _other)
        print("  —— %d 项通过，%d 警告，%d 严重 ——" % (rep["summary"]["ok"], len(warn), len(bad)))

    if "--speak" in sys.argv and (bad or warn):
        try:
            import urllib.request
            msg = "体检发现 %d 个问题。" % (len(bad) + len(warn))
            if bad:
                msg += "严重问题：%s。" % bad[0]["name"]
            if warn:
                msg += "警告：%s。" % warn[0]["name"]
            req = urllib.request.Request(
                "http://127.0.0.1:%d/dsh-tts-api/speak" % DSH,
                data=json.dumps({"text": msg}, ensure_ascii=False).encode("utf-8"),
                headers={"content-type": "application/json"})
            j = json.loads(urllib.request.urlopen(req, timeout=90).read().decode("utf-8"))
            u = j.get("url")
            if u:
                full = u if str(u).startswith("http") else ("http://127.0.0.1:%d" % DSH) + u
                audio = urllib.request.urlopen(full, timeout=90).read()
                tmp = os.path.join(LOG_DIR, "health_say.mp3")
                open(tmp, "wb").write(audio)
                import ctypes
                mci = ctypes.windll.winmm.mciSendStringW
                mci("close hchk", None, 0, None)
                if mci('open "%s" type mpegvideo alias hchk' % tmp, None, 0, None) == 0:
                    mci("play hchk wait", None, 0, None)
                    mci("close hchk", None, 0, None)
        except Exception:
            pass

    return 2 if bad else (1 if warn else 0)


if __name__ == "__main__":
    sys.exit(main())
