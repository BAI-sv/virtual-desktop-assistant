# -*- coding: utf-8 -*-
import json
import os
import socket
import subprocess
import sys
import threading
import time
import fairy_root


CREATE_NO_WINDOW = fairy_root.CREATE_NO_WINDOW

D = fairy_root.ROOT
LOG = os.path.join(D, "logs")
TOOLS = os.path.join(D, "tools")
INV = os.path.join(LOG, "inventory.json")
REPORT = os.path.join(LOG, "bootstrap_report.txt")
STATE = os.path.join(LOG, "bootstrap_state.json")
DSH = 19387

QUICK = "--quick" in sys.argv


AUTO = "--auto" in sys.argv
FULL = "--full" in sys.argv
MARKER = fairy_root.log(".initialized")

def is_first_run():
    return not os.path.exists(MARKER)

def mark_initialized():
    try:
        os.makedirs(os.path.dirname(MARKER), exist_ok=True)
        with open(MARKER, "w", encoding="utf-8") as f:
            f.write("initialized at %s\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            f.write("删除本文件可让下次 --auto 重新做一次全量扫描\n")
    except Exception as e:
        print("[bootstrap] 写初始化标记失败: %s" % e, flush=True)
DRY = "--dry" in sys.argv
SPEAK = "--speak" in sys.argv

RESULT = {}
LOCK = threading.Lock()
T0 = time.time()

def log(s):
    print(s, flush=True)

def put(key, val):
    with LOCK:
        RESULT[key] = val


QUICK_PS_CAP = 30

def ps(cmd, t=120, enc="gbk"):
    if QUICK:
        t = min(t, QUICK_PS_CAP)
    try:


        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, timeout=t,
                           creationflags=CREATE_NO_WINDOW)
        return (r.stdout or b"").decode(enc, "replace")
    except subprocess.TimeoutExpired:
        print("   [warn] PowerShell 超时（%ss），该项跳过" % t, flush=True)
        return ""
    except Exception as e:
        print("   [warn] PowerShell 调用失败: %s" % e, flush=True)
        return ""

def phase(n, name):
    log("\n[阶段 %d] %s" % (n, name))


def stage1_devices():
    phase(1, "设备扫描（BLE + mDNS + SSDP + ARP + 音频）")
    out = {"ble": [], "lan": [], "audio": [], "service": [], "error": ""}
    try:
        import importlib.util
        s = importlib.util.spec_from_file_location("da_bs", os.path.join(TOOLS, "device_adapters.py"))
        m = importlib.util.module_from_spec(s)
        sys.modules["da_bs"] = m
        s.loader.exec_module(m)
        devs = m.discover_all()
        for d in devs:
            k = d.get("kind") or d.get("adapter") or "other"
            out.setdefault(k, []).append(d)
        log("   发现 %d 个设备 / 端点" % len(devs))
        for k, v in out.items():
            if isinstance(v, list) and v:
                log("     %-10s %d" % (k, len(v)))
    except Exception as e:
        out["error"] = str(e)[:120]
        log("   失败: %s" % out["error"])
    put("devices", out)


def stage2_hardware():
    phase(2, "硬件清点")
    hw = {}
    try:
        cpu = ps("Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors,LoadPercentage | ConvertTo-Json -Compress", 60)
        hw["cpu"] = json.loads(cpu) if cpu.strip().startswith(("{", "[")) else {}
        if isinstance(hw["cpu"], list):
            hw["cpu"] = hw["cpu"][0] if hw["cpu"] else {}
    except Exception:
        hw["cpu"] = {}
    try:
        mem = ps("Get-CimInstance Win32_PhysicalMemory | Select-Object @{n='GB';e={[math]::Round($_.Capacity/1GB)}},Speed,ConfiguredClockSpeed | ConvertTo-Json -Compress", 60)
        hw["mem"] = json.loads(mem) if mem.strip().startswith(("{", "[")) else []
    except Exception:
        hw["mem"] = []
    try:
        os_ = ps("$o=Get-CimInstance Win32_OperatingSystem; '{0:N1}|{1:N1}' -f ($o.TotalVisibleMemorySize/1MB),($o.FreePhysicalMemory/1MB)", 60)
        a, b = (os_.strip().split("|") + ["0", "0"])[:2]
        hw["mem_total_gb"] = float(a)
        hw["mem_free_gb"] = float(b)
    except Exception:
        pass
    try:
        gpu = ps("Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,DriverDate | ConvertTo-Json -Compress", 60)
        hw["gpu"] = json.loads(gpu) if gpu.strip().startswith(("{", "[")) else []
    except Exception:
        hw["gpu"] = []
    try:
        disk = ps("Get-PhysicalDisk | Select-Object DeviceId,FriendlyName,MediaType,BusType,HealthStatus,@{n='GB';e={[math]::Round($_.Size/1GB)}} | ConvertTo-Json -Compress", 90)
        hw["disks"] = json.loads(disk) if disk.strip().startswith(("{", "[")) else []
    except Exception:
        hw["disks"] = []

    try:
        sp, cc = set(), set()
        for m0 in (hw.get("mem") or []):
            sp.add(m0.get("Speed"))
            cc.add(m0.get("ConfiguredClockSpeed"))
        hw["xmp_ok"] = (sp == cc)
        hw["mem_speed"] = sorted(x for x in sp if x)
        hw["mem_configured"] = sorted(x for x in cc if x)
    except Exception:
        hw["xmp_ok"] = None
    log("   CPU: %s" % (hw.get("cpu") or {}).get("Name", "?"))
    log("   内存: %s GB（Speed %s / Configured %s）→ XMP %s"
        % (hw.get("mem_total_gb"), hw.get("mem_speed"), hw.get("mem_configured"),
           "已开" if hw.get("xmp_ok") else "**未开或异常**"))
    log("   磁盘: %d 块，全部 %s" % (len(hw.get("disks") or []),
        "Healthy" if all((d.get("HealthStatus") == "Healthy") for d in (hw.get("disks") or [])) else "**有异常**"))
    put("hardware", hw)


def probe_tools():
    import shutil as _sh
    out = {}

    def _run(args, timeout=5):
        try:


            r = subprocess.run(args, capture_output=True, timeout=timeout,
                               creationflags=CREATE_NO_WINDOW)
            return r.returncode
        except Exception:
            return None

    def _has(path):
        try:
            return os.path.exists(path)
        except Exception:
            return False


    for name, cmd in (("git", ["git", "--version"]),
                      ("curl", ["curl", "--version"]),
                      ("ffmpeg", ["ffmpeg", "-version"]),
                      ("smartctl", ["smartctl", "--version"]),
                      ("adb", ["adb", "version"]),
                      ("docker", ["docker", "--version"]),
                      ("java", ["java", "-version"]),
                      ("dotnet", ["dotnet", "--version"]),
                      ("7z", ["7z"])):
        exe = _sh.which(cmd[0])
        if not exe:
            out[name] = False
            continue
        out[name] = (_run(cmd) is not None)


    wsl_exe = _sh.which("wsl") or (r"C:\Windows\System32\wsl.exe" if _has(r"C:\Windows\System32\wsl.exe") else None)
    if wsl_exe:
        rc = _run([wsl_exe, "--status"], timeout=5)
        if rc is None:
            out["WSL"] = None
        else:

            out["WSL"] = True
    else:
        out["WSL"] = False


    oll = None
    if _sh.which("ollama"):
        oll = True
    for d in (r"D:\Ollama",
              os.path.expanduser(r"~\.ollama"), r"C:\Program Files\Ollama"):
        if _has(d):
            oll = True
            break
    if oll is None:

        for f in ("ollama.pid",):
            if _has(os.path.join(fairy_root.ROOT, f)):
                oll = True
                break
    out["Ollama"] = bool(oll)


    for name, dirs in (("GPT-SoVITS", [r"<GPT_SOVITS_ROOT>"]),
                       ("ComfyUI", [r"<COMFY_ROOT>", r"<COMFY_ROOT>",
                                fairy_root.NAI_ROOT]),
                       ("DevEco Studio", [r"E:\DevEco Studio", r"C:\Program Files\Huawei\DevEco Studio"]),
                       ("HIP/ROCm", [r"C:\Program Files\AMD\ROCm"]),
                       ("HWiNFO", [r"C:\Program Files\HWiNFO64"]),
                       ("CPU-Z", [r"C:\Program Files\CPUID"]),
                       ("GPU-Z", [r"C:\Program Files (x86)\GPU-Z"]),
                       ("Ryzen Master SDK", [r"C:\Program Files\AMD\RyzenMasterSDK"]),
                       ("Afterburner", [r"C:\Program Files (x86)\MSI Afterburner"]),
                       ("雷电模拟器", [r"D:\LDPlayer"])):
        out[name] = any(_has(x) for x in dirs)

    return out

def stage3_software():
    phase(3, "软件清点")
    sw = {"installed": [], "portable": [], "py_pkgs": [], "cli": {}, "notable": {}}
    try:
        out = ps('''$k=@("HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*",
 "HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*",
 "HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*")
(Get-ItemProperty $k -ErrorAction SilentlyContinue | Where-Object {$_.DisplayName} |
 Select-Object -ExpandProperty DisplayName -Unique | Sort-Object) -join "`n"''', 180)
        sw["installed"] = [x.strip() for x in out.splitlines() if x.strip()]
        log("   已安装程序 %d 个" % len(sw["installed"]))
    except Exception:
        pass

    try:
        for root in (r"D:\\", r"E:\\"):
            if os.path.isdir(root):
                for x in sorted(os.listdir(root)):
                    p = os.path.join(root, x)
                    if os.path.isdir(p) and not x.startswith(("$", "System Volume")):
                        sw["portable"].append({"dir": p, "name": x})
        log("   便携/绿色目录 %d 个" % len(sw["portable"]))
    except Exception:
        pass

    NOTABLE = {
        "HWiNFO": ["hwinfo"], "CPU-Z": ["cpu-z", "cpuz"], "GPU-Z": ["gpu-z", "gpuz"],
        "Ryzen Master SDK": ["ryzenmaster"], "Afterburner": ["afterburner"],
        "smartmontools": ["smartmontools", "smartctl"], "ffmpeg": ["ffmpeg"],
        "GPT-SoVITS": ["gpt-sovits"], "IndexTTS": ["index-tts", "indextts"],
        "ComfyUI": ["comfyui"], "DevEco Studio": ["deveco"], "Android Studio": ["android studio"],
        "HIP/ROCm": ["hip sdk"], "Everything": ["everything"], "格式工厂": ["格式工厂"],
        "爱思助手": ["爱思助手"], "TeamViewer": ["teamviewer"], "雷电模拟器": ["雷电模拟器"],
        "Ollama": ["ollama"], "Docker": ["docker"], "WSL": ["wsl"],
    }
    hay = "\n".join(sw["installed"]).lower() + "\n" + "\n".join(p["dir"] for p in sw["portable"]).lower()
    for nice, kws in NOTABLE.items():
        sw["notable"][nice] = any(k in hay for k in kws)


    sw["probe"] = probe_tools()

    for k, v in sw["probe"].items():
        if v is True:
            sw["notable"][k] = True
        elif v is False and sw["notable"].get(k):

            pass
    log("   关键工具(名单): " + " · ".join("%s%s" % (k, "✓" if v else "✗") for k, v in sw["notable"].items()))
    pr = sw.get("probe") or {}
    log("   关键工具(实探测): " + " · ".join(
        "%s=%s" % (k, {True: "✓", False: "✗", None: "?"}[v]) for k, v in pr.items()))

    try:


        r = subprocess.run(["py", "-3.12", "-m", "pip", "list", "--disable-pip-version-check"],
                           capture_output=True, text=True, timeout=180, encoding="utf-8",
                           errors="replace", creationflags=CREATE_NO_WINDOW)
        sw["py_pkgs"] = [l.split()[0] for l in (r.stdout or "").splitlines()[2:] if l.strip()]
        log("   Python 包 %d 个" % len(sw["py_pkgs"]))
    except Exception:
        pass

    import shutil as _sh
    for t in ("git", "curl", "py", "ffmpeg", "7z", "smartctl", "adb", "winget", "choco",
              "scoop", "docker", "wsl", "node", "npm", "java", "dotnet"):
        sw["cli"][t] = _sh.which(t) or ""
    log("   命令行可用: " + " · ".join(k for k, v in sw["cli"].items() if v))
    put("software", sw)


def stage4_network():
    phase(4, "网络勘察")
    net = {}
    try:
        ipc = ps("Get-NetIPConfiguration | Where-Object {$_.IPv4Address} | "
                 "Select-Object InterfaceAlias,@{n='IP';e={$_.IPv4Address.IPAddress}},"
                 "@{n='GW';e={$_.IPv4DefaultGateway.NextHop}} | ConvertTo-Json -Compress", 90)
        net["ifaces"] = json.loads(ipc) if ipc.strip().startswith(("{", "[")) else []
        if isinstance(net["ifaces"], dict):
            net["ifaces"] = [net["ifaces"]]
    except Exception:
        net["ifaces"] = []
    try:
        dns = ps("(Resolve-DnsName www.baidu.com -ErrorAction SilentlyContinue | "
                 "Where-Object {$_.IPAddress} | Select-Object -First 1).IPAddress", 30)
        net["dns_ok"] = bool(dns.strip())
    except Exception:
        net["dns_ok"] = None

    try:
        s = socket.socket()
        s.settimeout(2.5)
        net["internet"] = s.connect_ex(("223.5.5.5", 443)) == 0
        s.close()
    except Exception:
        net["internet"] = None

    hosts = []
    try:
        for i in net.get("ifaces") or []:
            gw = i.get("GW") or ""
            if not gw:
                continue
            base = ".".join(gw.split(".")[:3])
            for last in (1, 2, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111, 112):
                ip = "%s.%d" % (base, last)
                for port in (80, 443, 8080, 8000):
                    sk = socket.socket()
                    sk.settimeout(0.25)
                    if sk.connect_ex((ip, port)) == 0:
                        hosts.append({"ip": ip, "port": port})
                        sk.close()
                        break
                    sk.close()
            break
    except Exception:
        pass
    net["lan_hosts"] = hosts
    log("   网卡 %d 个 · 公网 %s · DNS %s · 局域网发现 %d 台开网页的机器"
        % (len(net.get("ifaces") or []), net.get("internet"), net.get("dns_ok"), len(hosts)))
    put("network", net)


def stage5_capability():
    phase(5, "可扩展性评估（能做什么，而不是有什么问题）")
    hw = RESULT.get("hardware") or {}
    sw = RESULT.get("software") or {}
    net = RESULT.get("network") or {}
    dev = RESULT.get("devices") or {}
    cap = []

    def add(area, can, how, blocked=""):
        cap.append({"area": area, "can": can, "how": how, "blocked": blocked})

    nb = sw.get("notable") or {}

    if nb.get("GPT-SoVITS"):
        add("语音克隆", "已装 GPT-SoVITS v2pro，可做更贴近游戏音色的克隆",
            "启动 GPT-SoVITS-v2pro，用 fairy.wav 做参考音；接进 TTS 插件的 provider")
    add("语音输出", "已有 TTS 插件（Edge TTS）+ 1600 条游戏原声语料",
        "原声优先 -> 命不中走 TTS 克隆")

    ble = dev.get("ble") or []
    if ble:
        add("蓝牙设备", "扫到 %d 个 BLE 设备，其中 %d 个名字可识别"
            % (len(ble), sum(1 for x in ble if x.get("name"))),
            "Python 已装 bleak -> 可写 BLE central 连上去读 GATT 服务，认出未知设备",
            "需要写 BLE central 模块（库已就绪，未写）")
    audio = dev.get("audio") or []
    if audio:
        add("音频设备", "有 %d 个音频端点（含<BT_SPEAKER>）" % len(audio), "已接入 bluetooth_audio 适配器")

    add("摄像头", "可接 USB 摄像头做视觉输入", "需要摄像头硬件 + 抓帧组件",
        "本机当前无摄像头")

    if net.get("lan_hosts"):
        add("局域网", "局域网发现 %d 个开网页端口的设备" % len(net["lan_hosts"]),
            "可逐个嗅探是什么（HTTP 头/设备指纹）-> 进设备适配层")
    add("远程访问", "已装 TeamViewer / UU远程" if (nb.get("TeamViewer")) else "可装远程工具",
        "让你不在家时也能看到屏幕")

    if nb.get("HIP/ROCm"):
        add("GPU 计算", "已装完整 HIP/ROCm SDK", "本地推理/训练可走 ROCm 而不是只靠 DirectML")
    if nb.get("ComfyUI"):
        add("绘图", "已装 ComfyUI（多套）", "已接入 aigc 适配（8188）")
    add("本地大脑", "Ollama + llama-server 可作离线兜底",
        "按需拉起，省显存（当前都停着）")

    if nb.get("HWiNFO") or nb.get("CPU-Z"):
        add("硬件监控", "已装 HWiNFO / CPU-Z / GPU-Z",
            "可拿传感器数据做权威体检（比 WMI 全）；HWiNFO 需管理员权限")
    if nb.get("Ryzen Master SDK"):
        add("CPU 设置读取", "已装 Ryzen Master SDK（含 CLI）",
            "AMDRyzenMasterCLI.exe 可编程读 PBIOS/CPU 设置 -> 查 PBO/Curve 是否过激")
    if not nb.get("smartmontools"):
        add("磁盘深度体检", "缺 smartmontools",
            "装它可拿 SMART 原始属性 + 错误日志（能定位 E 盘坏道产生时间）",
            "未装：winget install smartmontools")

    if nb.get("DevEco Studio"):
        add("星闪/鸿蒙", "已装 DevEco Studio（鸿蒙 IDE）",
            "是接星闪/华为生态的正规入口；比另买开发板更直接")

    add("设备接入", "适配层已就绪（5 个适配器：nearby/audio/http/serial/nearlink）",
        "新设备 = 写一个 Adapter 子类，接口形状不变")
    add("对外接口", "插件已暴露 HTTP：/api/fairy/meta · say · task",
        "任何能联网的设备（手表/汽车/车机）都能直接调")

    log("   评估出 %d 条可扩展方向" % len(cap))
    for c in cap[:8]:
        log("     · %s：%s" % (c["area"], c["can"][:56]))
    if len(cap) > 8:
        log("     ...还有 %d 条" % (len(cap) - 8))
    put("capability", cap)


def stage6_autoconfigure():
    phase(6, "自动扩展（用相关软硬件真的去做）")
    actions = []

    def do(name, fn, note=""):
        if DRY:
            actions.append({"action": name, "result": "dry-run 跳过", "note": note})
            log("   [dry] %s" % name)
            return
        try:
            r = fn()
            actions.append({"action": name, "result": str(r)[:160], "note": note})
            log("   ✓ %s -> %s" % (name, str(r)[:80]))
        except Exception as e:
            actions.append({"action": name, "result": "失败: %s" % str(e)[:100], "note": note})
            log("   ✗ %s : %s" % (name, str(e)[:80]))


    def a_devices():
        p = os.path.join(LOG, "devices_known.json")
        dev = RESULT.get("devices") or {}
        old = {}
        try:
            if os.path.exists(p):
                old = json.load(open(p, encoding="utf-8"))
        except Exception:
            old = {}
        merged = dict(old)
        for k, v in dev.items():
            if isinstance(v, list):
                for d in v:
                    key = str(d.get("id") or d.get("name") or "")
                    if key:
                        e = merged.get(key, {})
                        e.update({"last_seen": time.strftime("%Y-%m-%d %H:%M:%S"),
                                  "kind": d.get("kind"), "name": d.get("name"),
                                  "seen": (e.get("seen") or 0) + 1})
                        merged[key] = e
        tmp = p + ".tmp"
        json.dump(merged, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, p)
        return "已记账 %d 台设备" % len(merged)
    do("设备入库", a_devices, "把扫描结果并进 devices_known.json（带出现次数，能看出哪些常在）")


    def b_unused():
        sw = RESULT.get("software") or {}
        nb = sw.get("notable") or {}
        unused = [k for k, v in nb.items() if v]
        p = os.path.join(LOG, "extend_todo.md")
        cap = RESULT.get("capability") or []
        with open(p, "w", encoding="utf-8") as f:
            f.write("# Fairy 扩展待办（由 bootstrap 自动生成）\n\n")
            f.write("生成时间：%s\n\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
            f.write("## 已装可用的重器（发现却没在用）\n")
            for k in unused:
                f.write("- %s\n" % k)
            f.write("\n## 可扩展方向（按价值）\n")
            for i, c in enumerate(cap, 1):
                f.write("%d. **%s** — %s\n" % (i, c["area"], c["can"]))
                f.write("   做法：%s\n" % c["how"])
                if c.get("blocked"):
                    f.write("   受阻：%s\n" % c["blocked"])
        return "生成 %s（%d 条方向）" % (os.path.basename(p), len(cap))
    do("扩展待办", b_unused, "把'已装未用'和'可扩展方向'落成 Markdown，供决策")


    def c_risk():
        hw = RESULT.get("hardware") or {}
        hwid = os.path.join(LOG, "hw_watch.json")
        whea = None
        try:
            if os.path.exists(hwid):
                whea = json.load(open(hwid, encoding="utf-8")).get("whea_total")
        except Exception:
            pass
        notes = []
        if hw.get("xmp_ok") is False:
            notes.append("XMP 未开启但频率不一致")
        if isinstance(whea, int) and whea > 0:
            notes.append("历史 WHEA 硬件错误 %d 条" % whea)
        if notes:
            p = os.path.join(LOG, "hardware_risk.md")
            with open(p, "w", encoding="utf-8") as f:
                f.write("# 硬件风险提示（bootstrap 自动生成）\n\n")
                for n in notes:
                    f.write("- %s\n" % n)
                f.write("\n## 建议动作\n")
                f.write("1. BIOS：关 XMP/EXPO 跑默认，观察 WHEA 是否停止（最快区分内存/SoC vs CPU 本体）\n")
                f.write("2. BIOS：关 Kombo Strike；Curve Optimizer 归 Auto；X3D 建议 VSOC ≤1.10V\n")
                f.write("3. 更新 BIOS/AGESA\n")
                f.write("4. MemTest86 排内存；OCCT/Prime95 试复现\n")
                f.write("5. 默认频率仍报 MCE -> CPU 本体缺陷，走保修\n")
            return "已生成 hardware_risk.md（%s）" % "；".join(notes)
        return "无硬件风险项"
    do("硬件风险", c_risk, "把 WHEA/XMP 风险落成清单 + 处理步骤")

    put("actions", actions)


def summarize():
    hw = RESULT.get("hardware") or {}
    dev = RESULT.get("devices") or {}
    net = RESULT.get("network") or {}
    sw = RESULT.get("software") or {}
    cap = RESULT.get("capability") or []
    el = round(time.time() - T0, 1)
    inv = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_s": el,
        "quick": QUICK,
        "devices": dev, "hardware": hw, "software": sw, "network": net,
        "capability": cap, "actions": RESULT.get("actions") or [],
    }
    try:
        os.makedirs(LOG, exist_ok=True)
        tmp = INV + ".tmp"
        json.dump(inv, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        os.replace(tmp, INV)
    except Exception:
        pass

    lines = []
    lines.append("Fairy 自举报告  %s  （%.1fs%s）" % (inv["time"], el, "，快速模式" if QUICK else ""))
    lines.append("")
    lines.append("设备：%d 个（BLE %d · 局域网 %d · 音频 %d）"
                 % (sum(len(v) for v in dev.values() if isinstance(v, list)),
                    len(dev.get("ble") or []), len(dev.get("lan") or []), len(dev.get("audio") or [])))
    cpu = hw.get("cpu") or {}
    lines.append("硬件：%s · 内存 %s GB(XMP %s) · 磁盘 %d 块"
                 % (cpu.get("Name", "?"), hw.get("mem_total_gb"),
                    "已开" if hw.get("xmp_ok") else "未开/异常", len(hw.get("disks") or [])))
    lines.append("软件：已装 %d 个 · 便携目录 %d 个 · Python 包 %d 个"
                 % (len(sw.get("installed") or []), len(sw.get("portable") or []), len(sw.get("py_pkgs") or [])))
    lines.append("网络：网卡 %d · 公网 %s · 局域网活跃 %d"
                 % (len(net.get("ifaces") or []), net.get("internet"), len(net.get("lan_hosts") or [])))
    lines.append("可扩展方向：%d 条（详见 %s）" % (len(cap), os.path.basename(INV)))
    lines.append("自动扩展动作：%d 项" % len(inv["actions"]))
    for a in inv["actions"]:
        lines.append("   · %s -> %s" % (a["action"], a["result"]))
    txt = "\n".join(lines)
    print("\n" + txt)
    try:
        open(REPORT, "w", encoding="utf-8").write(txt)
    except Exception:
        pass

    if SPEAK:
        try:
            import urllib.request
            msg = ("自举完成。扫描到 %d 个设备，%d 块磁盘，%d 个可扩展方向。"
                   % (sum(len(v) for v in dev.values() if isinstance(v, list)),
                      len(hw.get("disks") or []), len(cap)))
            req = urllib.request.Request(
                "http://127.0.0.1:%d/dsh-tts-api/speak" % DSH,
                data=json.dumps({"text": msg}, ensure_ascii=False).encode("utf-8"),
                headers={"content-type": "application/json"})
            j = json.loads(urllib.request.urlopen(req, timeout=90).read().decode("utf-8"))
            u = j.get("url")
            if u:
                full = u if str(u).startswith("http") else ("http://127.0.0.1:%d" % DSH) + u
                audio = urllib.request.urlopen(full, timeout=90).read()
                tmp = os.path.join(LOG, "boot_say.mp3")
                open(tmp, "wb").write(audio)
                import ctypes
                mci = ctypes.windll.winmm.mciSendStringW
                mci("close bsay", None, 0, None)
                if mci('open "%s" type mpegvideo alias bsay' % tmp, None, 0, None) == 0:
                    mci("play bsay wait", None, 0, None)
                    mci("close bsay", None, 0, None)
        except Exception:
            pass
    return 0

def main():

    if AUTO:
        if not is_first_run():

            return 0
        log("首次安装：执行【全量】扫描与扩展 …")

        globals()["QUICK"] = False
    log("=" * 64)
    log("Fairy 自举流水线   %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    log("模式: %s" % ("快速" if QUICK else "完整"))
    log("=" * 64)


    t2 = threading.Thread(target=stage2_hardware)
    t3 = threading.Thread(target=stage3_software)
    t2.start(); t3.start()
    if not QUICK:
        t1 = threading.Thread(target=stage1_devices)
        t4 = threading.Thread(target=stage4_network)
        t1.start(); t4.start()
        t1.join(); t4.join()
    else:
        log("\n[快速模式] 跳过 设备扫描 / 网络勘察")
        put("devices", {})
        put("network", {})
    t2.join(); t3.join()
    stage5_capability()
    stage6_autoconfigure()
    rc = summarize()
    if AUTO:
        mark_initialized()
        log("\n✅ 已标记初始化完成（以后开机不再自动全量扫描）。")
        log("   想重做全量：删掉 %s 再跑一次，或直接 py -3.12 bootstrap.py --full" % MARKER)
    elif FULL:
        mark_initialized()
    return rc

if __name__ == "__main__":
    sys.exit(main())
