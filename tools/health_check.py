# -*- coding: utf-8 -*-
import glob
import json
import os
import re
import socket
import subprocess
import sys
import time
import fairy_root


CREATE_NO_WINDOW = fairy_root.CREATE_NO_WINDOW

LOG_DIR = fairy_root.LOGS
REPORT = os.path.join(LOG_DIR, "health_last.json")
REPORT_BAK = os.path.join(LOG_DIR, "health_prev.json")
INDEX = fairy_root.VOICE_INDEX
INDEX_BAK = os.path.join(fairy_root.VOICE, "voice_index.backup.json")
CONF = fairy_root.BRIEFING_CONF
BALL_POS = fairy_root.BALL_POS
BALL_LOG = os.path.join(LOG_DIR, "fairy_ball.log")


BALL_LOGS = [
    os.path.join(fairy_root.BALL_LOG_DIR, "fairy_ball.py.log"),
    os.path.join(fairy_root.BALL_LOG_DIR, "fairy_ball.log"),
    os.path.join(LOG_DIR, "fairy_ball.py.log"),
    BALL_LOG,
]
BALL_PY = os.path.join(fairy_root.TOOLS, "fairy_ball.py")
DSH = 19387


R = []


FAST = '--fast' in sys.argv

FULL = '--full' in sys.argv

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
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (DSH, path), timeout=timeout) as r:
            return r.status == 200, r.read()
    except Exception as e:
        return False, str(e)

def ps(cmd, t=8):
    try:


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


def check_dsh():
    if not port_open(DSH):
        add("dsh", "DSH 服务(19387)", "bad", "端口没开",
            "启动 DeepSeek Harness；若已启动，查 %s 是否被占用" % DSH)
        return
    ok, body = http_ok("/api/fairy/meta")
    if not ok:
        add("dsh", "DSH 服务(19387)", "warn", "端口在但 /api/fairy/meta 不通: %s" % str(body)[:80],
            "插件 dsh-plugin-fairy 可能没加载 —— 重启 DSH")
        return
    try:
        j = json.loads(body.decode("utf-8"))
    except Exception:
        j = {}
    vc = j.get("voiceCount")
    if vc == 0:
        add("dsh", "DSH 服务(19387)", "warn", "插件在跑但 voiceCount=0（语料库没读到）",
            "检查 <FAIRY_ROOT>\\voice\\voice_index.json 是否存在且能被解析")
    else:
        add("dsh", "DSH 服务(19387)", "ok", "正常，语料 %s 条，帧 %s" % (vc, j.get("frames")))

def check_services():

    ok, _ = http_ok("/dsh-tts-api/diagnose", timeout=4)
    if ok:
        add("tts", "语音合成(TTS 插件)", "ok", "可用")
    else:
        add("tts", "语音合成(TTS 插件)", "warn", "接口不通",
            "DSH 重启后插件可能没起；或 TTS 插件未启用")


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
            "跑 <FAIRY_ROOT>\\tools\\extend_voice_index.py 重建，或用备份 %s 恢复" % INDEX_BAK)
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

def check_ball():
    global BALL_PIDS
    pids = ball_running()
    BALL_PIDS = pids
    if not pids:
        add("ball", "桌面球进程", "bad", "没在跑",
            "启动：pythonw <FAIRY_ROOT>\\tools\\fairy_ball.py（或检查开机自启）")
        return
    add("ball", "桌面球进程", "ok", "在跑 (PID %s)" % ",".join(pids))

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
        if "Traceback" in tail or "Error" in tail:
            last = [l for l in tail.splitlines() if "Error" in l or "Traceback" in l]
            add("ball_log", "球运行日志", "warn", "日志里有报错：%s" % (last[-1][:70] if last else "?"),
                "看 %s 定位；多数是 TTS/网络取不到，球会自动降级不影响使用" % logp)
        else:
            add("ball_log", "球运行日志", "ok", "无报错（%s）" % os.path.basename(logp))
    except Exception as e:

        add("ball_log", "球运行日志", "warn", "日志读不了：%s" % str(e)[:60], "检查 %s 权限/占用" % logp)
    if not os.path.exists(BALL_PY):
        add("ball_src", "球脚本", "bad", "fairy_ball.py 不见了",
            "从 <WORKDIR>\\fairy_ball.py 拷回 <FAIRY_ROOT>\\tools\\")

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
    out = ps('Get-ItemProperty "HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run" | '
             'Select-Object -Property * | Out-String -Width 200')
    miss = []
    for key in ("FairyDSH", "FairyBall"):
        if key not in out:
            miss.append(key)
    if miss:
        add("autostart", "开机自启", "warn", "缺少自启项：%s" % "、".join(miss),
            "跑 py -3.12 <WORKDIR>\\autostart.py enable")
    else:
        add("autostart", "开机自启", "ok", "FairyDSH + FairyBall 都在")

def check_disk():
    for drv in ("C:", "D:"):
        try:
            import shutil as _sh
            u = _sh.disk_usage(drv + "\\")
            free_gb = u.free / (1024 ** 3)
            if free_gb < 5:
                add("disk" + drv[0], "%s 盘剩余空间" % drv, "bad", "只剩 %.1f GB" % free_gb,
                    "清理空间；日志在 <FAIRY_ROOT>\\logs，可删旧文件")
            elif free_gb < 15:
                add("disk" + drv[0], "%s 盘剩余空间" % drv, "warn", "只剩 %.1f GB" % free_gb,
                    "留意清理")
            else:
                add("disk" + drv[0], "%s 盘剩余空间" % drv, "ok", "%.0f GB" % free_gb)
        except Exception:
            pass

def check_pending():
    log = os.path.join(LOG_DIR, "extend_index.log")
    if os.path.exists(log):
        try:
            t = open(log, encoding="utf-8", errors="replace").read()
            if "完成：新增" not in t and "没有要补的" not in t:
                add("pending", "未完成的任务", "warn", "补索引好像没跑完",
                    "重跑：py -3.12 <FAIRY_ROOT>\\tools\\extend_voice_index.py")
        except Exception:
            pass


NOISE = (
    "Service Control Manager, 7001",
    "Security-SPP, 16385",
    "DistributedCOM, 10010",
    "Kernel-Boot, 153",
    "Bonjour Service, 100",
    "Defrag, 264",
)


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


DRV_JSON = r'''
try {
  $o.drv=@(Get-CimInstance Win32_PnPSignedDriver | Where-Object {$_.DeviceName -and $_.DriverProviderName -ne 'Microsoft'} | ForEach-Object {
    $dt=$null
    try { $dt=[datetime]$_.DriverDate } catch {}
    if($dt -and $dt.Year -gt 2005){ [ordered]@{n=("" + $_.DeviceName); v=("" + $_.DriverVersion); d=$dt.ToString('yyyy-MM-dd'); y=$dt.Year} }
  } | Sort-Object {$_['y']} | Select-Object -First 6)
} catch {}
'''

SYS = {}
SYS_ERR = ""

def ps_json(script, t=45):
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
    try:
        fn()
    except Exception as e:
        add(fn.__name__, fn.__name__, "warn", "检查本身出错: %s" % str(e)[:60], "忽略即可")

def _load_sys(t=45):
    global SYS, SYS_ERR
    SYS, SYS_ERR = ps_json(SYS_JSON.replace("DRV_SLOT", DRV_JSON if FULL else ""), t=t)
    SYS = SYS or {}

def _sys_ok(tag):
    if SYS:
        return True
    if FAST:
        return False
    add(tag, "系统数据采集", "warn", "取不到 —— " + (SYS_ERR or "未知原因"),
        "手动跑一次 py -3.12 health_check.py 看报错；确认 PowerShell 没被组策略限制")
    return False

def _noise(key):
    return any(x in key for x in NOISE)


def check_hardware():
    if not _sys_ok("hw"):
        return

    name = (SYS.get("cpu_name") or "").strip()
    cores, thr = SYS.get("cpu_cores"), SYS.get("cpu_threads")
    load = SYS.get("cpu_load")
    lv = "warn" if (isinstance(load, int) and load >= 90) else "ok"
    add("hw_cpu", "CPU", lv, "%s · %s核%s线程 · 负载 %s%%" % (name[:34], cores, thr, load),
        "持续 90%+ 时查是不是有失控进程" if lv != "ok" else "")

    tot, free, pct = SYS.get("ram_total"), SYS.get("ram_free"), SYS.get("ram_used_pct")
    lv = "bad" if (isinstance(pct, int) and pct >= 97) else ("warn" if (isinstance(pct, int) and pct >= 90) else "ok")
    add("hw_ram", "内存占用", lv, "%s%%（已用 %.1f GB / 共 %s GB，%s 条）" % (pct, (tot or 0) - (free or 0), tot, SYS.get("ram_sticks")),
        "接近满载：关掉不用的程序，或查内存泄漏" if lv != "ok" else "")

    spd, cfg = SYS.get("ram_speed"), SYS.get("ram_cfg")
    if spd and cfg:
        if str(spd) == str(cfg):
            add("hw_ram_xmp", "内存频率", "ok", "实跑 %s MHz = 标称 %s MHz（XMP/DOCP 已开）" % (cfg, spd))
        else:
            add("hw_ram_xmp", "内存频率", "warn", "实跑 %s MHz < 标称 %s MHz" % (cfg, spd),
                "进 BIOS 打开 XMP/DOCP（微星叫 A-XMP）；不开会白丢约 30%% 内存带宽")

    add("hw_gpu", "显卡", "ok", "%s · 驱动 %s（%s）" % (SYS.get("gpu"), SYS.get("gpu_drv"), SYS.get("gpu_date")))

    disks = SYS.get("disks") or []
    bad = [d for d in disks if (d.get("h") or "") != "Healthy"]
    if bad:
        add("hw_disk", "物理磁盘", "bad", "%d 块不健康：%s" % (len(bad), "、".join(d.get("n", "?")[:22] for d in bad)),
            "立刻备份重要数据，用 CrystalDiskInfo 看 SMART 详情")
    else:
        add("hw_disk", "物理磁盘", "ok", "%d 块全部 Healthy（%s）" % (
            len(disks), "、".join("%s %sG" % ((d.get("m") or "?")[:4], d.get("gb")) for d in disks[:4])))


def check_system():
    if not _sys_ok("sys"):
        return

    for tag, key, label in (("sys_err", "sys_err", "系统日志错误"), ("app_err", "app_err", "应用日志错误")):
        groups = [g for g in (SYS.get(key) or []) if not _noise(g.get("k") or "")]
        if groups:
            top3 = "、".join("%s ×%s" % (g.get("k"), g.get("n")) for g in groups[:3])
            more = "（共 %d 组，已剔除白名单噪音）" % len(groups) if len(groups) > 3 else ""
            add(tag, label, "warn", "近7天 TOP：%s%s" % (top3, more),
                "按 Provider+Id 在事件查看器里搜这条；先确认是不是白名单里那类无害噪音")
        else:
            add(tag, label, "ok", "近7天无（非白名单）严重/错误事件")


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


    n = SYS.get("power") or 0
    add("sys_power", "异常断电", "bad" if n else "ok", "%d 次（60天）" % n,
        "有 Kernel-Power 41/42 说明断电/电源/线材问题，查插座与电源" if n else "")


    n = SYS.get("unexpected") or 0
    add("sys_shutdown", "意外关机", "warn" if n else "ok", "%d 次（30天）" % n,
        "非正常关机。对照 WHEA：若同时有硬件错误，先修 CPU/内存稳定性" if n else "")


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


    md, mem = SYS.get("minidump") or 0, SYS.get("memdmp")
    if md or mem:
        add("sys_bsod", "蓝屏转储", "warn", "%d 个 minidump%s" % (md, " + MEMORY.DMP" if mem else ""),
            "用 BlueScreenView 看是哪个驱动引起的；结合 WHEA 判断硬件还是驱动")
    else:
        add("sys_bsod", "蓝屏转储", "ok", "无 minidump、无 MEMORY.DMP")


    rb = (SYS.get("reboot") or "").strip()
    add("sys_reboot", "待重启更新", "warn" if rb else "ok",
        ("待重启标记：" + rb) if rb else "无（CBS / WindowsUpdate / 待替换文件 都没有）",
        "尽快重启让补丁或驱动替换生效；若 PendingFileRename 长期不清，查是不是某软件反复写它" if rb else "")


def check_software():
    if not _sys_ok("soft"):
        return
    badpnp = [x for x in (SYS.get("badpnp") or []) if x]
    if badpnp:
        add("sw_pnp", "异常设备(驱动)", "warn", "%d 个非 OK：%s" % (len(badpnp), "、".join(x[:20] for x in badpnp[:3])),
            "设备管理器看黄色感叹号；按硬件 ID 去官网下驱动重装")
    else:
        add("sw_pnp", "异常设备(驱动)", "ok", "无黄色感叹号设备")


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


    add("sw_node", "Node", "ok",
        nd[:30] if nd.startswith("v") else "不在系统 PATH（DSH 自带，不影响运行）")

def check_app_crashes():
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


def check_app_proc():
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

LOG_SKIP = ("ai_opinion", "second_opinion", "opinion_", "mce_amd", "cpuz_report")


LOG_SKIP_STALE = ("indextts_out", "indextts_err")

def _read_text(p):
    b = open(p, "rb").read()
    try:
        return b.decode("utf-8")
    except Exception:
        return b.decode("gbk", "replace")

def check_app_log():
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
            hits = [l.strip() for l in _read_text(p).splitlines() if LOG_ERR.search(l)]
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
            "看 %s 定位；IndexTTS 的残留日志可忽略（在用语音走 DSH 的 TTS 插件）" % newest[1])

def check_drivers():
    if not FULL:
        return
    if not _sys_ok("drv"):
        return
    drv = [d for d in (SYS.get("drv") or []) if d.get("d")]
    if not drv:
        add("sw_drv", "驱动陈旧 TOP", "warn", "取不到第三方驱动清单（WMI 受限或需要管理员）",
            "管理员身份跑：py -3.12 <FAIRY_ROOT>\\tools\\health_check.py --full")
        return

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


def main():
    global SYS, SYS_ERR
    t0 = time.time()
    os.makedirs(LOG_DIR, exist_ok=True)


    import threading
    th = None
    if not FAST:
        th = threading.Thread(target=_load_sys, kwargs={"t": 45}, daemon=True)
        th.start()


    local = (check_dsh, check_services, check_corpus, check_ball,
             check_config, check_autostart, check_disk, check_pending)
    ths = []
    for fn in local:
        th2 = threading.Thread(target=_safe, args=(fn,), daemon=True)
        th2.start()
        ths.append(th2)


    if th is not None:
        th.join(timeout=50)
    for fn in (check_hardware, check_software, check_system, check_app_crashes, check_drivers):
        _safe(fn)
    for th2 in ths:
        th2.join(timeout=60)

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


    tmp = REPORT + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
        os.replace(tmp, REPORT)
    except Exception as e:
        print("[health] 报告写入失败: %s" % e, flush=True)

    if "--quiet" not in sys.argv:


        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        print("Fairy 全栈体检  %s  （%.2fs%s）" % (rep["time"], rep["elapsed"], "  快模式" if FAST else ""))

        for lv, tag in (("bad", "✗ 严重"), ("warn", "! 警告")):
            for x in R:
                if x["level"] == lv:
                    print("  %s  %s：%s" % (tag, x["name"], x["detail"]))
                    if x["fix"]:
                        print("        解决办法：%s" % x["fix"])


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
