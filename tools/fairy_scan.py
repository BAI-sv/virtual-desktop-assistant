# -*- coding: utf-8 -*-
import ctypes
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import winreg
from concurrent.futures import ThreadPoolExecutor


CREATE_NO_WINDOW = 0x08000000


HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.dirname(HERE)

OUT_MD = os.path.join(D, "data", "scan_report.md")
OUT_JSON = os.path.join(D, "data", "scan_report.json")


SERVICES = [
    (11434, "Ollama 本地大脑", "qwen3.5-9b/27b"),
    (8080,  "llama-server 备用大脑", "Vulkan 小模型"),
    (8188,  "ComfyUI 出图", "SD 工作流"),
    (9881,  "IndexTTS 语音合成", "Fairy 音色"),
    (9880,  "GPT-SoVITS 语音", "备用音色"),
    (8090,  "FairyX 设备扩展", "设备发现/控制"),
    (19387, "DSH 插件网关", "DeepSeek 云端"),
    (8000,  "Web UI 常见端口", "待识别"),
]


SOFTWARE_PROBES = [
    ("Ollama",            r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe"),
    ("Everything",        r"%LOCALAPPDATA%\Everything\Everything.exe"),
    ("Everything(备)",    r"C:\Program Files\Everything\Everything.exe"),
    ("Everything(备2)",   r"C:\Program Files (x86)\Everything\Everything.exe"),
    ("Chrome",            r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
    ("Edge",              r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
    ("微信",              r"%ProgramFiles(x86)%\Tencent\WeChat\WeChat.exe"),
    ("QQ",                r"%ProgramFiles(x86)%\Tencent\QQ\QQ.exe"),
    ("剪映",              r"%LOCALAPPDATA%\JianyingPro\JianyingPro.exe"),
    ("网易云音乐",        r"%LOCALAPPDATA%\NetEase\CloudMusic\cloudmusic.exe"),
    ("PotPlayer",         r"%ProgramFiles%\DAUM\PotPlayer\PotPlayer64.exe"),
    ("VLC",               r"%ProgramFiles%\VideoLAN\VLC\vlc.exe"),
    ("VS Code",           r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"),
    ("PyCharm",           r"%ProgramFiles%\JetBrains\*\bin\pycharm64.exe"),
    ("Python",            r"%LOCALAPPDATA%\Programs\Python\Python*\python.exe"),
    ("Git",               r"%ProgramFiles%\Git\cmd\git.exe"),
    ("7-Zip",             r"%ProgramFiles%\7-Zip\7z.exe"),
    ("EverythingSDK-es",  r"D:\Everything\es.exe"),
    ("EverythingSDK-es2", r"C:\Everything\es.exe"),
]


MODEL_EXTS = (".gguf", ".safetensors", ".onnx", ".bin", ".ckpt", ".pt", ".pth", ".mlx", ".ot")

INSTALL_EXTS = (".exe", ".msi", ".msix", ".zip", ".7z", ".rar", ".tar", ".gz", ".whl", ".apk")


ROOTS = []

def expand(p):
    p = os.path.expandvars(p)
    if "*" in p:
        base, pat = os.path.split(p)
        if os.path.isdir(base):
            try:
                return [os.path.join(base, x) for x in os.listdir(base) if re.match(pat.replace("*", ".*"), x)]
            except Exception:
                return []
    return [p] if os.path.exists(p) else []

def probe_software():
    found = []
    for name, pat in SOFTWARE_PROBES:
        for fp in expand(pat):
            try:
                found.append({"软件": name, "路径": fp})
                break
            except Exception:
                pass
    return found

def reg_installed():
    apps = {}
    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    for hive, path in keys:
        try:
            k = winreg.OpenKey(hive, path)
        except OSError:
            continue
        try:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                try:
                    sk = winreg.OpenKey(k, sub)
                    name = ""
                    ver = ""
                    try:
                        name = winreg.QueryValueEx(sk, "DisplayName")[0]
                    except OSError:
                        pass
                    try:
                        ver = winreg.QueryValueEx(sk, "DisplayVersion")[0]
                    except OSError:
                        pass
                    if name:
                        apps[name] = ver
                    sk.Close()
                except OSError:
                    pass
            k.Close()
        except OSError:
            pass
    return apps

def ps_out(cmd):
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, text=True, timeout=40,
                           creationflags=CREATE_NO_WINDOW)
        return (r.stdout or "").strip()
    except Exception:
        return ""

def collect_hardware():
    hw = {}
    script = r"""
$o = @{}
try { $o.cpu = (Get-CimInstance Win32_Processor).Name } catch { $o.cpu = '未知' }
try { $o.gpu = ((Get-CimInstance Win32_VideoController | ForEach-Object Name) -join '; ') } catch { $o.gpu = '未知' }
try { $o.mem = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory } catch { $o.mem = 0 }
try { $o.disks = ((Get-PSDrive -PSProvider FileSystem | Where-Object {$_.Free -ne $null} | ForEach-Object { $_.Root + ' ' + [math]::Round($_.Used/1GB,0) + 'G已用 ' + [math]::Round($_.Free/1GB,0) + 'G剩余' }) -join ';') } catch { $o.disks = @() }
try { $o.nets = ((Get-NetAdapter -Physical | Where-Object Status -eq 'Up' | ForEach-Object Name) -join ';') } catch { $o.nets = '未知' }
try { $o.audio = ((Get-CimInstance Win32_SoundDevice | ForEach-Object Name) -join ';') } catch { $o.audio = '未知' }
try { $o.monitor = ((Get-CimInstance -Namespace root\wmi -ClassName WmiMonitorBasicDisplayParams | ForEach-Object { $_.InstanceName -replace '.*_','' }) -join ';') } catch { $o.monitor = '未知' }
$o | ConvertTo-Json -Compress
"""
    raw = ps_out(script)
    try:
        j = json.loads(raw)
        hw["cpu"] = j.get("cpu") or "未知"
        hw["gpu"] = j.get("gpu") or "未知"
        try:
            hw["内存"] = "%.1f GB" % (int(j.get("mem") or 0) / 1024**3)
        except Exception:
            hw["内存"] = "未知"
        hw["磁盘"] = [d for d in (j.get("disks") or "").split(";") if d.strip()]
        hw["网卡"] = j.get("nets") or "未知"
        hw["音频设备"] = j.get("audio") or "未知"
        hw["显示器"] = j.get("monitor") or "未知"
    except Exception:
        hw = {"cpu": "未知", "gpu": "未知", "内存": "未知", "磁盘": [],
              "网卡": "未知", "音频设备": "未知", "显示器": "未知"}
    return hw

def probe_services():
    def check(port, name, note):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.3)
        try:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return {"端口": port, "服务": name, "说明": note}
        finally:
            s.close()
        return None

    with ThreadPoolExecutor(max_workers=len(SERVICES)) as ex:
        results = list(ex.map(lambda t: check(*t), SERVICES))
    return [r for r in results if r]

def find_everything_es():
    for fp in [r"C:\Everything\es.exe", r"D:\Everything\es.exe",
               r"C:\Program Files\Everything\es.exe",
               r"C:\Program Files (x86)\Everything\es.exe",
               r"%LOCALAPPDATA%\Everything\es.exe"]:
        fp = os.path.expandvars(fp)
        if os.path.exists(fp):
            return fp
    return None

def everything_search(es, pattern, limit=300):
    try:
        r = subprocess.run([es, "-s", pattern], capture_output=True, text=True, timeout=60,
                           creationflags=CREATE_NO_WINDOW)
        lines = [l for l in (r.stdout or "").splitlines() if l.strip()][:limit]
        return lines
    except Exception:
        return []

def fallback_scan():
    found = {"models": [], "installs": []}
    roots = []
    for d in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        r = d + ":\\"
        if os.path.exists(r):
            roots.append(r)
    roots += [os.path.expanduser("~\\Downloads"), os.path.expanduser("~\\Desktop")]
    for root in roots:
        try:
            for name in os.listdir(root):
                full = os.path.join(root, name)
                if os.path.isdir(full):
                    continue
                low = name.lower()
                if low.endswith(MODEL_EXTS):
                    found["models"].append(full)
                elif low.endswith(INSTALL_EXTS):
                    found["installs"].append(full)
        except Exception:
            pass
    return found

def parse_everything_size(s):
    try:
        s = s.strip().replace(",", "")
        m = re.match(r"([\d.]+)\s*(TB|GB|MB|KB|B)?", s)
        if not m:
            return 0
        v = float(m.group(1))
        u = (m.group(2) or "B").upper()
        return int(v * {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3, "TB": 1024 ** 4}.get(u, 1))
    except Exception:
        return 0

def everything_http_search(query, max_items=6000):
    items = {}
    offset = 0
    while offset < max_items:
        url = "http://127.0.0.1:8092/?search=%s&count=500&offset=%d" % (
            urllib.parse.quote(query), offset)
        try:
            html = urllib.request.urlopen(url, timeout=30).read().decode("utf-8", "ignore")
        except Exception:
            break

        rows = re.split(r'<tr class="trdata\d">', html)[1:]
        got = 0
        for row in rows:
            m = re.search(r'href="(/[^"]+)"[^>]*><img class="icon" src="/file\.gif"', row)
            if not m:
                continue
            p = urllib.parse.unquote(m.group(1))
            if p.startswith("/"):
                p = p[1:]
            p = p.replace("/", "\\")
            sm = re.search(r'<td class="sizedata">.*?<nobr>([\d,.]+\s*(?:TB|GB|MB|KB|B)?)</nobr>', row, re.S)
            size = parse_everything_size(sm.group(1)) if sm else 0
            if p not in items:
                items[p] = size
                got += 1
        if got < 500:
            break
        offset += 500
    return list(items.items())

def everything_online():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.8)
    try:
        return s.connect_ex(("127.0.0.1", 8092)) == 0
    finally:
        s.close()

def scan_storage():
    res = {"engine": "", "models": [], "installs": []}
    if everything_online():
        res["engine"] = "Everything HTTP 服务（端口 8092，MFT 索引直读）"

        for p, sz in everything_http_search(
                "ext:gguf | ext:safetensors | ext:onnx | ext:ckpt | ext:pt | ext:pth | ext:mlx | ext:ot"):
            low = p.lower()
            if low.endswith((".gguf", ".safetensors", ".onnx", ".ckpt", ".pt", ".pth", ".mlx", ".ot")):
                res["models"].append((p, sz))

        for p, sz in everything_http_search(
                "ext:exe size:>1mb | ext:msi | ext:whl | ext:apk | ext:zip size:>100mb | ext:7z size:>100mb"):
            res["installs"].append((p, sz))
    else:
        res["engine"] = "Everything 未运行，回退浅层全盘扫描"
        fb = fallback_scan()
        res["models"] = [(p, 0) for p in fb["models"]]
        res["installs"] = [(p, 0) for p in fb["installs"]]
    res["models"].sort(key=lambda x: x[1], reverse=True)
    res["installs"].sort(key=lambda x: x[1], reverse=True)
    return res

def fmt_size(n):
    try:
        n = float(n)
    except Exception:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%.1f%s" % (n, unit)) if unit != "B" else ("%d%s" % (int(n), unit))
        n /= 1024
    return "?"

def build_report(hw, sw_reg, sw_probe, svc, storage, start):
    lines = []
    lines.append("# FairyX 设备扫描报告\n")
    lines.append("> 扫描时间：%s | 耗时 %.0f 秒\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), time.time() - start))
    lines.append("## 一、硬件\n")
    lines.append("- CPU：%s" % hw.get("cpu", "未知"))
    lines.append("- GPU：%s" % hw.get("gpu", "未知"))
    lines.append("- 内存：%s" % hw.get("内存", "未知"))
    lines.append("- 磁盘：\n" + "\n".join("  - %s" % d for d in hw.get("磁盘", [])))
    lines.append("- 网卡：%s" % hw.get("网卡", "未知"))
    lines.append("- 音频设备：%s" % hw.get("音频设备", "未知"))
    lines.append("- 显示器：%s\n" % hw.get("显示器", "未知"))

    lines.append("## 二、可用功能服务（端口探测）\n")
    if svc:
        for s in svc:
            lines.append("- **%s**（端口 %d）：%s" % (s["服务"], s["端口"], s["说明"]))
    else:
        lines.append("- 无已启动服务（FairyX 相关服务未运行）")
    lines.append("\n> 未启动但可用的服务：Ollama(11434)、ComfyUI(8188)、IndexTTS(9881)、FairyX设备扩展(8090)，由对应脚本启动。\n")

    lines.append("## 三、软件安装情况\n")
    lines.append("### 3.1 注册表已装软件（共 %d 项，前 60）\n" % len(sw_reg))
    names = sorted(sw_reg.keys())
    for n in names[:60]:
        lines.append("- %s%s" % (n, "（%s）" % sw_reg[n] if sw_reg[n] else ""))
    if len(names) > 60:
        lines.append("- …（其余 %d 项）" % (len(names) - 60))

    lines.append("\n### 3.2 常用软件探测\n")
    if sw_probe:
        for s in sw_probe:
            lines.append("- ✓ %s：%s" % (s["软件"], s["路径"]))
    else:
        lines.append("- 未探测到常用软件")

    lines.append("\n## 四、存储可部署资产（%s）\n" % storage["engine"])
    lines.append("### 4.1 模型包（共 %d 个，前 30，按大小排序）\n" % len(storage["models"]))
    if storage["models"]:
        for p, sz in storage["models"][:30]:
            lines.append("- [%s] %s" % (fmt_size(sz), p))
    else:
        lines.append("- 未找到模型包")

    lines.append("\n### 4.2 软件安装包（共 %d 个，前 30，按大小排序）\n" % len(storage["installs"]))
    if storage["installs"]:
        for p, sz in storage["installs"][:30]:
            lines.append("- [%s] %s" % (fmt_size(sz), p))
    else:
        lines.append("- 未找到安装包")

    lines.append("\n---\n*本报告由 fairy_scan.py 自动生成，硬件信息来自 WMI，软件来自注册表与目录探测，存储资产来自 Everything 服务。*")
    return "\n".join(lines)

def main():
    start = time.time()
    print("[1/5] 采集硬件信息 ...")
    hw = collect_hardware()
    print("[2/5] 探测可用服务 ...")
    svc = probe_services()
    print("[3/5] 读取已装软件 ...")
    sw_reg = reg_installed()
    sw_probe = probe_software()
    print("[4/5] 扫描存储资产（Everything）...")
    storage = scan_storage()
    print("[5/5] 生成报告 ...")
    md = build_report(hw, sw_reg, sw_probe, svc, storage, start)

    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write(md)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({"hardware": hw, "services": svc, "software_reg": list(sw_reg.keys()),
                   "software_probe": sw_probe,
                   "storage": {"engine": storage["engine"],
                               "models": [{"path": p, "size": s} for p, s in storage["models"]],
                               "installs": [{"path": p, "size": s} for p, s in storage["installs"]]}},
                  f, ensure_ascii=False, indent=1)
    print("报告已生成：%s" % OUT_MD)
    print("CPU: %s" % hw.get("cpu", "?"))
    print("GPU: %s" % hw.get("gpu", "?"))
    print("内存: %s | 磁盘: %s" % (hw.get("内存", "?"), "; ".join(hw.get("磁盘", []))))
    print("服务在线: %d | 已装软件: %d | 模型包: %d | 安装包: %d" % (
        len(svc), len(sw_reg), len(storage["models"]), len(storage["installs"])))

if __name__ == "__main__":
    main()
