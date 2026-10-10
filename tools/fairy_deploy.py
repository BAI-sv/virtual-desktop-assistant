import json
import os
import re
import subprocess
import sys
import winreg


CREATE_NO_WINDOW = 0x08000000


HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.dirname(HERE)

CONFIG = os.path.join(D, "config.json")
OLLAMA = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")

def _load_cfg():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

_CFG = _load_cfg()
_paths = _CFG.get("paths") or {}


LLAMA_DIR = _paths.get("llama_dir") or ""
STRATA_DIR = _paths.get("strata_dir") or ""
if not STRATA_DIR:
    for _cand in (r"C:\Strata", r"D:\Strata", r"E:\Strata", r"F:\Strata",
                  os.path.join(os.path.expanduser("~"), "Strata")):
        if os.path.isdir(_cand):
            STRATA_DIR = _cand
            break
if not LLAMA_DIR:
    for _cand in (os.path.join(D, "tools", "llama"), r"C:\llama.cpp",
                  r"D:\llama.cpp"):
        if os.path.isdir(_cand):
            LLAMA_DIR = _cand
            break


_assets = _CFG.get("model_assets") or []
LOCAL_MODELS = _assets if _assets else [

    ("Qwen3.8-27B Q4_K_M（示例）", r"<模型目录>\Qwen3.8-27B-Uncensored-Q4_K_M.gguf", 16.0, "大"),
    ("Qwen3.5-27B Q4_K_S（示例）", r"<模型目录>\Qwen3.5-27B-abliterated.Q4_K_S.gguf", 14.5, "大"),
    ("Qwen3.5-9B Q4_K_M（示例）",  r"<模型目录>\Qwen3.5-9B-Uncensored-Q4_K_M.gguf", 5.2, "中"),
    ("Qwen3-VL-8B Q4_K_M（示例）", r"<模型目录>\Qwen3-VL-8B-Instruct.Q4_K_M.gguf", 4.7, "中"),
]

def ps(cmd):
    try:

        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, text=True, timeout=30,
                           creationflags=CREATE_NO_WINDOW)
        return (r.stdout or "").strip()
    except Exception:
        return ""

def get_gpu_info():
    gpus = []
    cls = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    i = 0
    while True:
        try:
            k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, cls + "\\%04d" % i)
            try:
                desc = winreg.QueryValueEx(k, "DriverDesc")[0]
                ram = 0
                try:
                    ram = int(winreg.QueryValueEx(k, "HardwareInformation.qwMemorySize")[0])
                except OSError:
                    try:
                        ram = int(winreg.QueryValueEx(k, "HardwareInformation.AdapterRAM")[0])
                    except OSError:
                        pass
                gpus.append({"name": desc, "vram_gb": round(ram / 1024**3, 1)})
            except OSError:
                pass
            k.Close()
            i += 1
        except OSError:
            break
    if not gpus:
        names = ps("(Get-CimInstance Win32_VideoController | ForEach-Object Name) -join ';'")
        gpus = [{"name": n, "vram_gb": 0} for n in names.split(";") if n]
    return gpus

def get_mem_gb():
    try:
        return int(ps("(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory")) / 1024**3
    except Exception:
        return 0

def is_integrated(name):
    n = name.lower()
    return ("radeon(tm) graphics" in n or "intel(r) uhd" in n or "intel(r) hd" in n
            or "intel(r) iris" in n or "集成" in n or "核显" in n)

def pick_tier(gpus, mem_gb):
    discrete = [g for g in gpus if not is_integrated(g["name"]) and g["vram_gb"] > 0.5]
    igpu = [g for g in gpus if is_integrated(g["name"])]
    if discrete:
        v = max(g["vram_gb"] for g in discrete)
        if v >= 14:
            return "大", "27B Q4（100% 进显存，质量最强）", "独显 %s（%sG）" % (discrete[0]["name"], v)
        if v >= 6:
            return "中", "9B-14B Q4（全进显存）", "独显 %s（%sG）" % (discrete[0]["name"], v)
        if v >= 4:
            return "中低", "9B Q4（部分进显存，其余 CPU）", "独显 %s（%sG）偏小" % (discrete[0]["name"], v)
        return "小", "1.5B-4B Q4（CPU 为主）", "独显 %s 显存不足" % discrete[0]["name"]
    if igpu:
        if mem_gb >= 32:
            return "核显大内存", "MoE 拆分（Strata/llama.cpp 专家驻留）或 9B 慢跑", "核显 %s + 内存 %sG" % (igpu[0]["name"], round(mem_gb))
        if mem_gb >= 12:
            return "核显中内存", "1.5B-4B Q4（核显 Vulkan 加速）", "核显 %s + 内存 %sG" % (igpu[0]["name"], round(mem_gb))
        return "核显小内存", "1B Q4（CPU 纯跑）", "核显 %s 内存紧张" % igpu[0]["name"]
    return "未知", "需人工确认显卡", "未识别到显卡"

def find_model_for_tier(tier):
    if tier in ("大",):
        for m in LOCAL_MODELS:
            if m[3] == "大" and os.path.exists(m[1]):
                return m
        for m in LOCAL_MODELS:
            if os.path.exists(m[1]):
                return m
    if tier in ("中", "中低"):
        for m in LOCAL_MODELS:
            if m[3] == "中" and os.path.exists(m[1]):
                return m

    small = [m for m in LOCAL_MODELS if m[3] == "中" and os.path.exists(m[1])]
    if small:
        small.sort(key=lambda x: x[2])
        return small[0]
    return None

def ollama_available():
    return os.path.exists(OLLAMA)

def ollama_import(name, model_path):
    modelfile = os.path.join(D, "Modelfile-%s" % name)
    with open(modelfile, "w", encoding="utf-8") as f:
        f.write("FROM %s\n\nPARAMETER temperature 0.7\nPARAMETER top_p 0.9\nPARAMETER num_ctx 8192\n" % model_path)
    r = subprocess.run([OLLAMA, "create", name, "-f", modelfile],
                       capture_output=True, text=True, timeout=1200)
    return r.returncode == 0, (r.stdout or r.stderr or "")[-300:]

def wire_brain(model_name, note):
    with open(CONFIG, encoding="utf-8") as f:
        d = json.load(f)
    provs = d["brain"]["providers"]
    new = {
        "name": "Ollama %s（本地大脑）" % model_name,
        "enabled": True,
        "base_url": "http://127.0.0.1:11434/v1",
        "model": model_name,
        "api_key": "ollama",
        "timeout": 300,
        "max_tokens": 1024,
        "extra_body": {},
        "note": note,
    }

    provs = [p for p in provs if "本地大脑" not in (p.get("name") or "") or "Ollama" not in (p.get("name") or "")]
    provs.insert(0, new)
    d["brain"]["providers"] = provs
    d["brain"]["active_provider"] = ""
    d["brain"]["chain_note"] = ("顺序=优先级：Ollama %s（本地大脑）-> 其他本地 -> 云端兜底。自动部署于 %s"
                                % (model_name, __import__("time").strftime("%Y-%m-%d %H:%M")))
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return True

def deploy_llama_server(model_path):
    bat = os.path.join(D, "tools", "llama_server_start.bat")
    with open(bat, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("@echo off\r\n")
        f.write('cd /d "%s"\r\n' % LLAMA_DIR)
        f.write('start "" llama-server.exe -m "%s" --host 127.0.0.1 --port 8080 --ctx-size 8192 -ngl 99 --threads 8 --jinja\r\n' % model_path)
    return bat

def main():
    auto = "--auto" in sys.argv
    print("=== FairyX 模型自适应部署 ===")
    gpus = get_gpu_info()
    mem = get_mem_gb()
    print("GPU: %s" % "; ".join("%s(%sG)" % (g["name"], g["vram_gb"]) for g in gpus))
    print("内存: %.1f GB" % mem)

    tier, strategy, why = pick_tier(gpus, mem)
    print("档位: %s | %s | 依据: %s" % (tier, strategy, why))

    model = find_model_for_tier(tier)
    if not model:
        print("!! 本地没有可用模型资产。请把 GGUF 路径填进 config.json 的 model_assets，"
              "或指定 --model 路径")
        return
    print("选中模型: %s（%sG）" % (model[0], model[2]))

    if not auto:
        print("\n[预览模式] 未做任何改动。加 --auto 执行部署。")
        return


    if ollama_available():
        print(">>> 用 Ollama 导入 %s ..." % model[0])

        base = os.path.basename(model[1])
        mm = re.search(r"Qwen3[.5]?([.-])?(\d+[.-]?\d*)\s*B", base, re.I)
        if mm:
            ver = base.lower()
            if "3.8" in ver:
                name = "qwen3.8-27b"
            elif "3.5" in ver:
                name = "qwen3.5-27b" if "27" in ver else "qwen3.5-9b"
            else:
                name = "qwen3.5-27b" if model[3] == "大" else "qwen3.5-9b"
        else:
            name = "qwen3.5-27b" if model[3] == "大" else "qwen3.5-9b"
        ok, msg = ollama_import(name, model[1])
        if ok:
            print("导入成功。接入大脑...")
            wire_brain(name, "%s 自动部署（%s）" % (model[0], why))
            print("完成！大脑链已切到 %s，可运行 启动FairyX.bat（FairyX 根目录下）" % name)
        else:
            print("导入失败: %s" % msg)
    else:
        bat = deploy_llama_server(model[1])
        print("未装 Ollama，已生成 Vulkan 启动脚本: %s（核显也能跑）" % bat)

if __name__ == "__main__":
    main()
