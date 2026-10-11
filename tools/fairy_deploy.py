r"""
FairyX 模型自适应部署器 fairy_deploy.py
自动检测硬件（独显显存/核显/内存）→ 选模型档位 → 从本地资产找模型 →
自动部署（Ollama / llama.cpp-Vulkan / Strata 拆分）→ 自动接入 FairyX 大脑。

用法：
  py -3.12 tools/fairy_deploy.py            # 检测+预览（不动任何东西）
  py -3.12 tools/fairy_deploy.py --auto     # 全自动部署并接大脑
  py -3.12 tools/fairy_deploy.py --model "模型目录\xx.gguf"  # 指定模型部署
核显也能跑：独显显存不足时自动降档，或对 MoE 模型走 Strata 专家拆分方案。

模型资产表优先读 config.json 的 model_assets 段（路径按你本机实际填写），
未配置时使用内置示例表（仅演示档位结构，路径为占位，请自行修改）。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import winreg

# ★ 2026-10-10【黑窗】0x08000000 = CREATE_NO_WINDOW（不是 0x00000200）。
#   父进程没控制台时（pythonw / 计划任务），不加这个起 powershell 就会弹终端。
CREATE_NO_WINDOW = 0x08000000

# FairyX 根目录：由脚本位置推导（tools/ 的上一级），任意目录部署都能用
HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.dirname(HERE)

CONFIG = os.path.join(D, "config.json")
OLLAMA = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")


def _load_cfg():
    """读 config.json（容错：不存在/损坏返回空）"""
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


_CFG = _load_cfg()
_paths = _CFG.get("paths") or {}

# 专用目录：优先 config.json 的 paths 段；否则自动探测
LLAMA_DIR = _paths.get("llama_dir") or ""
STRATA_DIR = _paths.get("strata_dir") or ""
if not LLAMA_DIR and os.path.isdir(r"E:\Strata"):
    STRATA_DIR = r"E:\Strata"
if not LLAMA_DIR:
    for _cand in (os.path.join(D, "tools", "llama"), r"C:\llama.cpp",
                  r"D:\llama.cpp"):
        if os.path.isdir(_cand):
            LLAMA_DIR = _cand
            break

# 本地模型资产：优先 config.json 的 model_assets（推荐：路径按你本机实际填写）
# 未配置时回退内置示例表（占位路径仅示意，部署前请修改）
_assets = _CFG.get("model_assets") or []
LOCAL_MODELS = _assets if _assets else [
    # (名称, 路径, 大小GB, 档位)
    ("Qwen3.8-27B Q4_K_M（示例）", r"<模型目录>\Qwen3.8-27B-Uncensored-Q4_K_M.gguf", 16.0, "大"),
    ("Qwen3.5-27B Q4_K_S（示例）", r"<模型目录>\Qwen3.5-27B-abliterated.Q4_K_S.gguf", 14.5, "大"),
    ("Qwen3.5-9B Q4_K_M（示例）",  r"<模型目录>\Qwen3.5-9B-Uncensored-Q4_K_M.gguf", 5.2, "中"),
    ("Qwen3-VL-8B Q4_K_M（示例）", r"<模型目录>\Qwen3-VL-8B-Instruct.Q4_K_M.gguf", 4.7, "中"),
]


def ps(cmd):
    try:
        # ★ 2026-10-10【黑窗】powershell.exe 是控制台程序；父进程没控制台时会新建一个
        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, text=True, timeout=30,
                           creationflags=CREATE_NO_WINDOW)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def _vendor(name):
    n = name.lower()
    if any(k in n for k in ("nvidia", "geforce", "rtx", "gtx", "quadro", "tesla")):
        return "nvidia"
    if any(k in n for k in ("radeon", "rx ", "amd", "vega", "firepro")):
        return "amd"
    return "intel"


def get_gpu_info():
    """GPU 名称 + 准确显存。
    ★ 2026-10-11【N 卡支持】优先 nvidia-smi（显存/型号最准，CUDA 后端识别）；
       AMD / Intel 回退注册表 qwMemorySize（64 位无溢出，参照驱动直读）。
    """
    gpus = []
    # ① N 卡：nvidia-smi 直读（准）
    nvsmi = r"C:\Windows\System32\nvidia-smi.exe"
    if not os.path.exists(nvsmi):
        nvsmi = shutil.which("nvidia-smi")
    if nvsmi:
        try:
            r = subprocess.run([nvsmi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                               capture_output=True, text=True, timeout=15,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for line in (r.stdout or "").splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 2 and parts[1].strip().isdigit():
                    gpus.append({"name": parts[0], "vram_gb": round(int(parts[1]) / 1024, 1),
                                 "vendor": "nvidia", "backend": "CUDA"})
        except Exception:
            pass
    # ② 注册表（AMD / Intel / 未装驱动的 N 卡兜底）
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
                if not any(g["name"] == desc for g in gpus):
                    v = _vendor(desc)
                    gpus.append({"name": desc, "vram_gb": round(ram / 1024**3, 1),
                                 "vendor": v,
                                 "backend": "CUDA" if v == "nvidia" else
                                             ("ROCm" if v == "amd" else "Vulkan/CPU")})
            except OSError:
                pass
            k.Close()
            i += 1
        except OSError:
            break
    if not gpus:
        names = ps("(Get-CimInstance Win32_VideoController | ForEach-Object Name) -join ';'")
        gpus = [{"name": n, "vram_gb": 0, "vendor": _vendor(n), "backend": "CPU"} for n in names.split(";") if n]
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
    """档位判定：独显显存 / 核显 / 内存 → (档位, 建议模型策略, 说明)
    ★ 2026-10-11【N 卡支持】说明里带后端：N 卡 CUDA、A 卡 ROCm、核显 Vulkan/CPU。"""
    discrete = [g for g in gpus if not is_integrated(g["name"]) and g["vram_gb"] > 0.5]
    igpu = [g for g in gpus if is_integrated(g["name"])]

    def _tail(g):
        be = g.get("backend") or _vendor(g["name"])
        return "（%s %s）" % (g["name"], be)

    if discrete:
        v = max(g["vram_gb"] for g in discrete)
        top = [g for g in discrete if g["vram_gb"] == v][0]
        if v >= 14:
            return "大", "27B Q4（100% 进显存，质量最强）", "独显 %s（%sG）%s" % (top["name"], v, _tail(top))
        if v >= 6:
            return "中", "9B-14B Q4（全进显存）", "独显 %s（%sG）%s" % (top["name"], v, _tail(top))
        if v >= 4:
            return "中低", "9B Q4（部分进显存，其余 CPU）", "独显 %s（%sG）%s 偏小" % (top["name"], v, _tail(top))
        return "小", "1.5B-4B Q4（CPU 为主）", "独显 %s %s 显存不足" % (top["name"], _tail(top))
    if igpu:
        if mem_gb >= 32:
            return "核显大内存", "MoE 拆分（llama.cpp 专家驻留）或 9B 慢跑（Vulkan）", "核显 %s + 内存 %sG（Vulkan/CPU）" % (igpu[0]["name"], round(mem_gb))
        if mem_gb >= 12:
            return "核显中内存", "1.5B-4B Q4（核显 Vulkan 加速）", "核显 %s + 内存 %sG（Vulkan）" % (igpu[0]["name"], round(mem_gb))
        return "核显小内存", "1B Q4（CPU 纯跑）", "核显 %s 内存紧张（CPU）" % igpu[0]["name"]
    return "未知", "需人工确认显卡", "未识别到显卡"


def find_model_for_tier(tier):
    """按档位从本地资产选模型"""
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
    # 小/核显档：优先挑最小的模型
    small = [m for m in LOCAL_MODELS if m[3] == "中" and os.path.exists(m[1])]
    if small:
        small.sort(key=lambda x: x[2])
        return small[0]
    return None


def ollama_available():
    return os.path.exists(OLLAMA)


def list_strata_configs():
    """★ 2026-10-11【模型自动配置清单】扫描 Strata 配置目录（E:\\Strata\\strata-*.json）
    -> 所有可部署的 Qwen3.8-Flash-Next 量化档位。按体积升序返回：
    [{name, file, size_gb, tier, note, ready}]，ready=模型文件已下载到位。"""
    base = STRATA_DIR or r"E:\Strata"
    out = []
    if not os.path.isdir(base):
        return out
    for _f in sorted(os.listdir(base)):
        if not _f.startswith("strata-") or not _f.endswith(".json"):
            continue
        try:
            with open(os.path.join(base, _f), encoding="utf-8") as _fh:
                _d = json.load(_fh)
            _native = ""
            _args = _d.get("args") or []
            for _i, _a in enumerate(_args):
                if _a == "--native" and _i + 1 < len(_args):
                    _native = _args[_i + 1]
                    break
            out.append({
                "name": _d.get("model_name") or _f[:-5],
                "file": _f,
                "size_gb": float(_d.get("_size_gb") or 0),
                "tier": _d.get("_tier") or "",
                "note": (_d.get("_note") or "").replace("\\n", " ")[:120],
                "ready": bool(_native and os.path.exists(_native)),
            })
        except Exception:
            continue
    out.sort(key=lambda x: x["size_gb"])
    return out


def strata_pick_default():
    """★ 2026-10-11【自动选档】从已下载的 Strata 档位里挑默认：
    优先 IQ4_XS（甜点位）；没有则挑体积最小的已下载档；都没有返回 None。"""
    cfgs = list_strata_configs()
    if not cfgs:
        return None
    for _c in cfgs:
        if _c["ready"] and "iq4_xs" in _c["file"]:
            return _c
    for _c in cfgs:
        if _c["ready"]:
            return _c
    return None


def ollama_import(name, model_path):
    """导入 GGUF 到 Ollama"""
    modelfile = os.path.join(D, "Modelfile-%s" % name)
    with open(modelfile, "w", encoding="utf-8") as f:
        f.write("FROM %s\n\nPARAMETER temperature 0.7\nPARAMETER top_p 0.9\nPARAMETER num_ctx 8192\n" % model_path)
    r = subprocess.run([OLLAMA, "create", name, "-f", modelfile],
                       capture_output=True, text=True, timeout=1200)
    return r.returncode == 0, (r.stdout or r.stderr or "")[-300:]


def wire_brain(model_name, note):
    """把模型接入 FairyX 大脑链首位"""
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
    # 去掉旧的同名 Ollama 链首，插到最前
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
    """核显/无 Ollama 场景：写 llama.cpp Vulkan 启动脚本 + 配置"""
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

    # 部署主路径
    if ollama_available():
        print(">>> 用 Ollama 导入 %s ..." % model[0])
        # 模型名从文件名提取（Qwen3.8-27B -> qwen3.8-27b；Qwen3.5-9B -> qwen3.5-9b）
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
