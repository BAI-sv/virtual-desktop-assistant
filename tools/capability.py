# -*- coding: utf-8 -*-
import json
import os
import re
import socket
import subprocess
import sys
import urllib.request


CREATE_NO_WINDOW = 0x08000000


HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.dirname(HERE)
CONFIG = os.path.join(D, "config.json")

def _load_cfg():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

_CFG = _load_cfg()
_cpaths = _CFG.get("paths") or {}


COMFY_MODELS = (_cpaths.get("comfy_models") or
                [r"C:\ComfyUI\models", r"D:\ComfyUI\models",
                 r"E:\ComfyUI\models", r"F:\ComfyUI\models",
                 os.path.join(D, "models")])
COMFY_MODELS = [p for p in COMFY_MODELS if os.path.isdir(p)]
REPO_EXT = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx")
CATS = ("checkpoints", "diffusion_models", "unet", "loras", "vae", "controlnet",
        "clip", "clip_vision", "text_encoders", "upscale_models", "llm",
        "audio_encoders", "model_patches", "embeddings", "ipadapter")

SERVICES = [("ComfyUI", 8188), ("IndexTTS 2.5", 9881), ("llama-server", 8080),
            ("Ollama", 11434), ("DSH GUI", 19387)]

def _up(port, host="127.0.0.1", t=1.0):
    s = socket.socket()
    s.settimeout(t)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()

def _json_get(url, timeout=8):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.load(r)
    except Exception:
        return None

def gpu_info():
    out = {"name": "", "vram_total": 0, "vram_free": 0, "src": ""}
    d = _json_get("http://127.0.0.1:8188/system_stats")
    if d and d.get("devices"):
        dev = d["devices"][0]
        out["name"] = dev.get("name", "")
        out["vram_total"] = int(dev.get("vram_total") or 0)
        out["vram_free"] = int(dev.get("vram_free") or 0)
        out["src"] = "ComfyUI /system_stats"
        return out

    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command",
                            "(Get-CimInstance Win32_VideoController | "
                            "Select-Object -First 1 Name,AdapterRAM | ConvertTo-Json)"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60,
                           creationflags=CREATE_NO_WINDOW)
        j = json.loads(r.stdout or "{}")
        out["name"] = j.get("Name", "")
        out["vram_total"] = int(j.get("AdapterRAM") or 0)
        out["src"] = "Win32 AdapterRAM（>4GB 不准，仅参考）"
    except Exception:
        pass
    return out

def sys_info():
    s = {"cpu": "", "cores": 0, "ram_total": 0, "ram_free": 0, "disks": []}
    try:
        import psutil
        s["cpu"] = subprocess.run(["powershell", "-NoProfile", "-Command",
                                   "(Get-CimInstance Win32_Processor).Name"],
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=60,
                                  creationflags=CREATE_NO_WINDOW).stdout.strip()
        s["cores"] = psutil.cpu_count(logical=True) or 0
        vm = psutil.virtual_memory()
        s["ram_total"] = vm.total
        s["ram_free"] = vm.available
        seen = set()
        for p in psutil.disk_partitions(all=False):
            if p.device[:2] in seen:
                continue
            seen.add(p.device[:2])
            try:
                u = psutil.disk_usage(p.mountpoint)
                s["disks"].append((p.device, u.total, u.free))
            except Exception:
                pass
    except Exception as e:
        s["err"] = str(e)
    return s

def scan_models():
    res = {"comfy": {}, "gguf_files": [], "ollama": [], "indextts": [], "whisper": []}


    seen = set()
    for base in COMFY_MODELS:
        if not os.path.isdir(base):
            continue
        for cat in CATS:
            d = os.path.join(base, cat)
            if not os.path.isdir(d):
                continue
            for r, _, fs in os.walk(d):
                for x in fs:
                    if not x.lower().endswith(REPO_EXT):
                        continue
                    p = os.path.join(r, x)
                    try:
                        sz = os.path.getsize(p)
                    except OSError:
                        continue
                    key = (x.lower(), sz)
                    if key in seen:
                        continue
                    seen.add(key)
                    res["comfy"].setdefault(cat, []).append(
                        {"name": os.path.relpath(p, d), "size": sz})
                    if x.lower().endswith(".gguf"):
                        res["gguf_files"].append({"name": os.path.relpath(p, d), "size": sz})

    d = _json_get("http://127.0.0.1:11434/api/tags")
    if d:
        for mm in d.get("models", []):
            res["ollama"].append({"name": mm.get("name"), "size": int(mm.get("size") or 0),
                                  "params": (mm.get("details") or {}).get("parameter_size", "")})

    ck = (_cpaths.get("indextts_dir") or "")
    if ck and os.path.isdir(ck):
        for n in ("gpt.pth", "s2mel.pth", "codec.pth"):
            p = os.path.join(ck, n)
            if os.path.exists(p):
                res["indextts"].append({"name": n, "size": os.path.getsize(p)})

    wd = os.path.join(D, "models", "whisper")
    if os.path.isdir(wd):
        for r, _, fs in os.walk(wd):
            for x in fs:
                if x.lower().endswith((".bin", ".pt", ".safetensors")):
                    p = os.path.join(r, x)
                    res["whisper"].append({"name": os.path.relpath(p, wd),
                                           "size": os.path.getsize(p)})
    return res

def verdict(size_bytes, vram_total):
    if not vram_total or not size_bytes:
        return "无法判断"
    gb = size_bytes / 2 ** 30
    v = vram_total / 2 ** 30
    if gb <= 0.85 * v:
        return "能直接跑（全进显存）"
    if gb <= 2.5 * v:
        return "能跑，但要 offload（慢，且吃内存）"
    return "基本跑不动，建议换量化版（GGUF Q4/Q8）"

def quant_of(name):
    m = re.search(r"(q\d[_a-z0-9]*|fp8|fp16|bf16|mxfp8|q4_k_[ms]|q8_0)", name, re.I)
    return m.group(1).upper() if m else ""

def report(full=False):
    g = gpu_info()
    s = sys_info()
    mo = scan_models()
    L = []
    L.append("=== 本机能力自检 %s ===" % __import__("time").strftime("%Y-%m-%d %H:%M:%S"))
    vram = g["vram_total"]
    L.append("【显卡】%s" % (g["name"] or "?"))
    L.append("   显存 %.1f GB 可用（共 %.1f GB，来源 %s）"
             % (g["vram_free"] / 2 ** 30, vram / 2 ** 30, g["src"] or "?"))
    L.append("【CPU】%s（%d 线程）" % (s["cpu"][:60] or "?", s["cores"]))
    L.append("【内存】%.1f GB 可用 / 共 %.1f GB"
             % (s["ram_free"] / 2 ** 30, s["ram_total"] / 2 ** 30))
    for dev, tot, free in s["disks"]:
        L.append("【磁盘】%s 可用 %.0f GB / %.0f GB" % (dev, free / 2 ** 30, tot / 2 ** 30))
    L.append("【后端】" + "  ".join("%s:%s" % (n, "开" if _up(p) else "关") for n, p in SERVICES))


    L.append("")
    L.append("--- 结论：按显存算，这些能跑 ---")
    big = []
    for cat, items in (mo.get("comfy") or {}).items():
        for it in items:
            if it["size"] > 2 * 2 ** 30:
                big.append((cat, it))
    big.sort(key=lambda x: -x[1]["size"])
    shown = 0
    for cat, it in big:
        if shown >= (60 if full else 14):
            break
        q = quant_of(it["name"])
        L.append("   %-18s %6.1f GB  %-9s %s"
                 % (cat, it["size"] / 2 ** 30, q, verdict(it["size"], vram)))
        L.append("        %s" % it["name"][-78:])
        shown += 1
    L.append("   （只列 >2GB 的；共 %d 个）" % len(big))

    if mo.get("ollama"):
        L.append("")
        L.append("--- Ollama 模型 ---")
        for it in mo["ollama"]:
            L.append("   %-22s %6.2f GB  %-12s %s"
                     % (it["name"], it["size"] / 2 ** 30, it["params"],
                        verdict(it["size"], vram)))
    if mo.get("indextts"):
        L.append("--- IndexTTS 2.5（语音克隆）---")
        tot = sum(x["size"] for x in mo["indextts"])
        L.append("   %s  合计 %.2f GB  %s"
                 % (", ".join(x["name"] for x in mo["indextts"]), tot / 2 ** 30,
                    verdict(tot, vram)))
    if mo.get("whisper"):
        L.append("--- Whisper 识别模型 ---")
        for it in mo["whisper"][:4]:
            L.append("   %-40s %6.2f GB" % (it["name"][-40:], it["size"] / 2 ** 30))
    if mo.get("gguf_files"):
        L.append("--- 注意：有 %d 个 GGUF，但要能加载得先装 GGUF 加载器 ---"
                 % len(mo["gguf_files"]))
        for it in mo["gguf_files"][:6]:
            L.append("   %-46s %5.1f GB" % (it["name"][-46:], it["size"] / 2 ** 30))
    return "\n".join(L)

if __name__ == "__main__":
    print(report(full="--full" in sys.argv))
