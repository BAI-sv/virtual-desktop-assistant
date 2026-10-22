# -*- coding: utf-8 -*-
import json
import os
import re
import sys
import time
import queue
import random
import threading
import subprocess
import socket
import datetime
import ast
import math
import tempfile
import webbrowser
import base64


if sys.platform != "win32":
    print("[FairyX] 当前版本面向 Windows。请在你的 Windows 电脑上运行本程序。")
    sys.exit(0)

import ctypes
import ctypes.wintypes as _wt
import tkinter as tk
from tkinter import ttk, messagebox
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
import pyautogui
import win32api
import win32gui
import win32con
import requests
import shutil


_HERE = os.path.dirname(os.path.abspath(__file__))


if not os.environ.get("HF_ENDPOINT"):
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

CONFIG_PATH = os.path.join(_HERE, "config.json")
if not os.path.exists(CONFIG_PATH):
    CONFIG_PATH = os.path.join(_HERE, "..", "config.json")
CONFIG_PATH = os.path.abspath(CONFIG_PATH)

TRANSPARENT = "#ff00fe"

STATE_IDLE = "idle"
STATE_LISTENING = "listening"
STATE_THINKING = "thinking"
STATE_SPEAKING = "speaking"

STATE_COLORS = {
    STATE_IDLE: "#2dd4bf",
    STATE_LISTENING: "#38bdf8",
    STATE_THINKING: "#fbbf24",
    STATE_SPEAKING: "#fb7185",
}


STATE_SPEED = {
    STATE_IDLE: 1.0,
    STATE_LISTENING: 1.35,
    STATE_THINKING: 2.1,
    STATE_SPEAKING: 1.6,
}


FAIRY_BLUE = "#3b82f6"
FAIRY_BLUE_DEEP = "#1e40af"
FAIRY_WHITE = "#f8fafc"
FAIRY_EYE_BLUE = "#60a5fa"
FAIRY_EYE_CORE = "#1d4ed8"

def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)

CONFIG = load_config()


DL_DIR = (CONFIG.get("paths") or {}).get("dl_dir") or ""
DL_TARGET_GB = float((CONFIG.get("paths") or {}).get("dl_target_gb") or 0.0)

def log(msg):

    print("[FairyX %s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg),
          flush=True)


DANGEROUS_KEYWORDS = [
    "format", "rmdir", "rm -rf", "diskpart", "shutdown", "reg delete",
    "cipher /w", "net user", "del /f /s", "rd /s", "del /q",
]

def safe_eval(expr):
    allowed_calls = {name: getattr(math, name) for name in
                     ("sqrt", "sin", "cos", "tan", "log", "log10", "exp",
                      "floor", "ceil", "pow", "pi", "e")}
    allowed_calls["round"] = round
    allowed_calls["abs"] = abs

    class Guard(ast.NodeVisitor):
        def visit(self, node):
            if isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp,
                                 ast.Num, ast.Constant, ast.Name, ast.Load,
                                 ast.Add, ast.Sub, ast.USub, ast.UAdd,
                                 ast.Mult, ast.Div,
                                 ast.Pow, ast.Mod, ast.Call, ast.Attribute)):
                if isinstance(node, ast.Name) and node.id not in allowed_calls and node.id != "math":
                    raise ValueError("不支持的变量: " + node.id)
                if isinstance(node, ast.Constant):
                    v = node.value
                    if isinstance(v, bool) or not isinstance(v, (int, float)):
                        raise ValueError("只允许数字常量")
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Attribute):
                        if not (isinstance(node.func.value, ast.Name) and
                                node.func.value.id == "math"):
                            raise ValueError("只允许 math 白名单函数")
                        if node.func.attr not in allowed_calls:
                            raise ValueError("不支持的函数: " + node.func.attr)
                    elif isinstance(node.func, ast.Name):
                        if node.func.id not in allowed_calls:
                            raise ValueError("不支持的函数: " + node.func.id)
                    else:
                        raise ValueError("不允许的调用形式")
                return self.generic_visit(node)
            raise ValueError("表达式包含不允许的元素")

    tree = ast.parse(expr, mode="eval")
    Guard().visit(tree)
    ns = dict(allowed_calls)
    ns["math"] = math
    return eval(compile(tree, "<expr>", "eval"), {"__builtins__": {}}, ns)

def _clipboard_set(text):
    try:
        data = (str(text) + "\x00").encode("utf-16-le")
        ctypes.windll.user32.OpenClipboard(0)
        ctypes.windll.user32.EmptyClipboard()
        h = ctypes.windll.kernel32.GlobalAlloc(0x0042, len(data))
        p = ctypes.windll.kernel32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        ctypes.windll.kernel32.GlobalUnlock(h)
        ctypes.windll.user32.SetClipboardData(13, h)
        ctypes.windll.user32.CloseClipboard()
        return "已写入剪贴板"
    except Exception as e:
        return "写入剪贴板失败: %s" % e

def _clipboard_get():
    try:
        ctypes.windll.user32.OpenClipboard(0)
        if ctypes.windll.user32.IsClipboardFormatAvailable(13):
            h = ctypes.windll.user32.GetClipboardData(13)
            p = ctypes.windll.kernel32.GlobalLock(h)
            size = ctypes.windll.kernel32.GlobalSize(h)
            buf = ctypes.create_string_buffer(size)
            ctypes.windll.kernel32.RtlMoveMemory(buf, p, size)
            ctypes.windll.kernel32.GlobalUnlock(h)
            ctypes.windll.user32.CloseClipboard()
            return buf.raw.decode("utf-16-le", errors="ignore").rstrip("\x00")
        ctypes.windll.user32.CloseClipboard()
        return ""
    except Exception as e:
        return "[读取剪贴板失败: %s]" % e

def _find_window(title_part):
    found = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            t = win32gui.GetWindowText(hwnd)
            if t and title_part.lower() in t.lower():
                found.append((hwnd, t))
    win32gui.EnumWindows(cb, None)
    return found

def _set_volume(percent):
    v = max(0, min(100, int(percent)))
    val = int(v / 100 * 0xFFFF)
    packed = (val & 0xFFFF) | ((val & 0xFFFF) << 16)
    ctypes.windll.winmm.waveOutSetVolume(0, packed)
    return "音量已设为 %d%%" % v

def _decode_console(raw):
    if not raw:
        return ""
    for enc in ("mbcs", "utf-8"):
        try:
            s = raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        if "\ufffd" not in s:
            return s
    return raw.decode("mbcs", errors="replace")

def _run_shell(command, confirm=True):
    if confirm and any(k in command.lower() for k in DANGEROUS_KEYWORDS):
        return "SECURITY_CONFIRM:" + command
    try:
        r = subprocess.run(command, shell=True, capture_output=True, timeout=120)
        out = _decode_console(r.stdout)[:2000]
        err = _decode_console(r.stderr)[:1000]
        if not out and not err:
            return "命令已执行（无输出）"
        return ("输出:\n" + out + ("\n错误:\n" + err if err else "")).strip()
    except subprocess.TimeoutExpired:
        return "命令执行超时（120秒）"
    except Exception as e:
        return "执行失败: %s" % e

def _strip_html(s):
    s = re.sub(r"<[^>]+>", "", s)
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
                 ("&#39;", "'"), ("&nbsp;", " "), ("&#x27;", "'")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()

def _bing_search(query, limit=5):
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) "
                             "Chrome/124.0.0.0 Safari/537.36",
               "Accept-Language": "zh-CN,zh;q=0.9"}
    last = ""
    for base in ("https://cn.bing.com/search?q=", "https://www.bing.com/search?q="):
        try:
            r = requests.get(base + requests.utils.quote(query), headers=headers, timeout=15)
            if r.status_code != 200:
                last = "HTTP %d" % r.status_code
                continue
            html = r.text
            hits = list(re.finditer(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                                    html, re.S))
            out = []
            for m in hits:
                url = m.group(1)
                title = _strip_html(m.group(2))
                if not title or url.startswith("javascript"):
                    continue
                tail = html[m.end():m.end() + 1500]
                ms = re.search(r"<p[^>]*>(.*?)</p>", tail, re.S)
                snip = _strip_html(ms.group(1))[:160] if ms else ""
                out.append("- %s\n  %s%s" % (title, url, ("\n  " + snip) if snip else ""))
                if len(out) >= limit:
                    break
            if out:
                return "\n".join(out)
            last = last or "页面里没解析出结果"
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, e)
    return "搜索失败（%s）。可以先改用 open_target 打开搜索引擎。" % last

def _note(text):
    path = os.path.join(os.path.expanduser("~"), "FairyX_notes.txt")
    with open(path, "a", encoding="utf-8") as f:
        f.write("[%s] %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M"), text))
    return "已记入便签: %s" % path

def _keyboard_type(text):
    if not text:
        return "没有要输入的内容"
    _clipboard_set(text)
    pyautogui.hotkey("ctrl", "v")
    return "已输入: %s" % text[:50]

def _open_target(target):
    if not target:
        return "目标为空"
    if target.startswith(("http://", "https://")):
        webbrowser.open(target)
        return "已在浏览器打开: %s" % target
    try:
        os.startfile(target)
        return "已打开: %s" % target
    except Exception as e:
        return "打开失败: %s" % e

def _screenshot(save_to):
    path = save_to or os.path.join(os.path.expanduser("~"), "Desktop",
                                   "FairyX_%s.png" % time.strftime("%Y%m%d_%H%M%S"))
    try:
        pyautogui.screenshot().save(path)
        return "截图已保存: %s" % path
    except Exception as e:
        return "截图失败: %s" % e

VISION_URL = "http://127.0.0.1:8085/v1/chat/completions"

def _app_use(need, args=""):
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
        import fairy_apps
        app = fairy_apps.find_by_text(need)
        if not app:
            return ("没有匹配到工具。可用：vscode(写代码) deveco(鸿蒙) androidstudio(安卓) "
                    "vs(C++/VC) wps(文档) aria2(下载) everything(找文件)")
        exe = fairy_apps._detect_exe(app)
        if exe:
            return fairy_apps.open_app(app, [args] if args else None)
        return fairy_apps.install(app)
    except Exception as e:
        return "工具调度失败: %s" % e

def _write_code_tool(path, code):
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
        import fairy_apps
        return fairy_apps.write_code(path, code)
    except Exception as e:
        return "写代码失败: %s" % e

def _catalog_check():
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
        import fairy_apps
        ov, missing = fairy_apps.scan_catalog()
        pri = getattr(fairy_apps, "PRIORITY_CATS", [])
        dl = [(cat, n) for cat, lack in missing for n in lack
              if fairy_apps._rep_info(n).get("url") and cat in pri]
        return ov, dl
    except Exception as e:
        return "分类扫描失败: %s" % e, []

def _catalog_install():
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
        import fairy_apps
        pri = getattr(fairy_apps, "PRIORITY_CATS", [])
        _, missing = fairy_apps.scan_catalog()
        dl = [(cat, n) for cat, lack in missing for n in lack
              if fairy_apps._rep_info(n).get("url") and cat in pri]
        if not dl:
            return "常用类代表软件都已安装，没有需要自动安装的"
        outs = []
        for cat, n in dl:
            outs.append("[%s] %s -> %s" % (cat, n, fairy_apps.install_rep(n)))
        return "\n".join(outs)
    except Exception as e:
        return "自动安装失败: %s" % e

def _vision(mode, save_to=""):
    mode = mode or "反推"
    path = save_to or os.path.join(os.path.expanduser("~"), "Desktop",
                                   "FairyX_vision_%s.png" % time.strftime("%Y%m%d_%H%M%S"))
    try:
        pyautogui.screenshot().save(path)
    except Exception as e:
        return "截图失败: %s" % e
    try:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
    except Exception as e:
        return "读取截图失败: %s" % e
    if mode == "反推":
        user = "为这张图片生成适合 Stable Diffusion 的详细英文提示词（只输出提示词）"
        system = "你是图片反推专家，输出英文文生图提示词，包含主体、构图、光影、风格、细节、质量词。"
    elif mode == "翻译":
        user = "把图片里的文字内容识别并翻译成中文"
        system = "你是OCR与翻译助手，输出图片中的文字及中文翻译。"
    else:
        user = "详细描述这张图片里有什么（中文）"
        system = "你是FairyX的视觉助手，仔细观察图片，用中文详细描述。"
    try:
        r = requests.post(VISION_URL, json={
            "model": "qwen3-vl-8b",
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": [
                             {"type": "text", "text": user},
                             {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}]}],
            "stream": False, "max_tokens": 600,
        }, timeout=180)
        r.raise_for_status()
        out = (r.json().get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
        return "（已截屏 %s）\n%s" % (os.path.basename(path), out)
    except Exception as e:
        return "视觉服务不可用（先运行 tools\\llama_vl_start.bat）：%s" % e

def _read_file(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read(8000)
    except Exception as e:
        return "读取失败: %s" % e

def _write_file(path, content):
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return "已写入: %s" % path
    except Exception as e:
        return "写入失败: %s" % e

def _list_dir(path):
    try:
        items = os.listdir(path)
        if not items:
            return "目录为空"
        return "\n".join(items[:100]) + ("\n…共%d项" % len(items) if len(items) > 100 else "")
    except Exception as e:
        return "列出失败: %s" % e

def _window_manage(action, title):
    found = _find_window(title)
    if not found:
        return "未找到标题含「%s」的窗口" % title
    hwnd, t = found[0]
    if action == "minimize":
        win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
        return "已最小化窗口: %s" % t
    if action == "close":
        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        return "已发送关闭: %s" % t
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        return "已前置窗口: %s" % t
    except Exception as e:
        return "前置失败: %s" % e


COMFY_DEFAULT = {
    "base_url": "http://127.0.0.1:8188",
    "checkpoint": "v1-5-pruned-emaonly-fp16.safetensors",
    "width": 512, "height": 512, "steps": 20, "cfg": 7.0,
    "timeout": 600,
    "save_dir": os.path.join(os.path.expanduser("~"), "Desktop", "FairyX_images"),
    "start_script": r"<WORKDIR>\comfy_start.py",
}

def _comfy_cfg():
    c = dict(COMFY_DEFAULT)
    c.update(CONFIG.get("comfy") or {})
    return c

def _comfy_offline_msg(err):
    c = _comfy_cfg()
    return ("ComfyUI 不可达：%s\n"
            "启动方式（二选一）：\n"
            "  1) py -3.12 \"%s\"\n"
            "  2) 双击 ComfyUI 便携版的 run_amd_gpu.bat，"
            "确认 8188 端口起来" % (err, c["start_script"]))

def _comfy_status(params=None):
    c = _comfy_cfg()
    base = c["base_url"].rstrip("/")
    try:
        d = requests.get(base + "/system_stats", timeout=6).json()
    except Exception as e:
        return _comfy_offline_msg(e)
    dev = (d.get("devices") or [{}])[0]
    cks = []
    try:
        info = requests.get(base + "/object_info/CheckpointLoaderSimple", timeout=15).json()
        cks = info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0]
    except Exception:
        pass
    return ("ComfyUI 在线：v%s ｜ 设备 %s (%s) ｜ VRAM 空闲 %.1f/%.1f GB\n"
            "当前默认 checkpoint: %s（%s）\n"
            "可见 checkpoint %d 个：%s"
            % (d.get("system", {}).get("comfyui_version"), dev.get("name"), dev.get("type"),
               dev.get("vram_free", 0) / 2 ** 30, dev.get("vram_total", 0) / 2 ** 30,
               c["checkpoint"],
               "可见" if c["checkpoint"] in cks else "**不可见，需要换**",
               len(cks), "、".join(cks[:8])))

def _comfy_generate(p):
    c = _comfy_cfg()
    base = c["base_url"].rstrip("/")
    prompt = (p.get("prompt") or "").strip()
    if not prompt:
        return "prompt 为空，没说要生成什么"
    w = int(p.get("width") or c["width"])
    h = int(p.get("height") or c["height"])
    steps = int(p.get("steps") or c["steps"])
    seed = int(p.get("seed") or random.randint(1, 2 ** 31 - 1))
    ckpt = p.get("checkpoint") or c["checkpoint"]
    neg = p.get("negative") or "low quality, blurry, watermark, text, extra fingers"

    graph = {
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "5": {"class_type": "EmptyLatentImage",
              "inputs": {"width": w, "height": h, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1], "text": prompt}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1], "text": neg}},
        "3": {"class_type": "KSampler",
              "inputs": {"seed": seed, "steps": steps, "cfg": float(c["cfg"]),
                         "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0,
                         "model": ["4", 0], "positive": ["6", 0],
                         "negative": ["7", 0], "latent_image": ["5", 0]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage",
              "inputs": {"filename_prefix": "FairyX", "images": ["8", 0]}},
    }
    try:
        r = requests.post(base + "/prompt", json={"prompt": graph}, timeout=30)
    except Exception as e:
        return _comfy_offline_msg(e)
    if r.status_code != 200:
        return "ComfyUI 拒绝了任务(%d)：%s" % (r.status_code, r.text[:400])
    pid = r.json().get("prompt_id")
    t0 = time.time()
    hist = None
    while time.time() - t0 < float(c["timeout"]):
        time.sleep(2)
        try:
            hj = requests.get(base + "/history/" + str(pid), timeout=15).json()
        except Exception:
            continue
        if pid in hj and hj[pid].get("outputs"):
            hist = hj[pid]
            break
    if hist is None:
        return ("生成超时（%.0f 秒）。ComfyUI 可能还在跑，"
                "可以用 comfy_status 看状态，或稍后到 %s 找结果。"
                % (float(c["timeout"]), c["save_dir"]))

    outdir = c["save_dir"]
    os.makedirs(outdir, exist_ok=True)
    saved = []
    for node in (hist.get("outputs") or {}).values():
        for im in node.get("images", []):
            try:
                blob = requests.get(base + "/view",
                                    params={"filename": im["filename"],
                                            "subfolder": im.get("subfolder", ""),
                                            "type": im.get("type", "output")},
                                    timeout=60).content
            except Exception:
                continue
            dst = os.path.join(outdir, im["filename"])
            with open(dst, "wb") as f:
                f.write(blob)
            saved.append(dst)
    if not saved:
        return "任务已完成，但没有图片输出（图可能被别的节点处理掉了）"
    try:
        os.startfile(saved[0])
    except Exception:
        pass
    return ("已生成 %d 张，用时 %.0f 秒（seed=%d，%dx%d，%d 步）：\n%s"
            % (len(saved), time.time() - t0, seed, w, h, steps, "\n".join(saved)))

def _capture(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, timeout=timeout)
        return _decode_console(r.stdout)
    except Exception as e:
        return "[执行失败: %s]" % e

def _ps(cmd, timeout=120):
    try:
        r = subprocess.run(["powershell.exe", "-NoProfile", "-Command", cmd],
                           capture_output=True, timeout=timeout)
        return _decode_console(r.stdout).strip(), _decode_console(r.stderr).strip()
    except Exception as e:
        return "", str(e)

def _startup_items():
    import winreg
    keys = ((winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run"),
            (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run"),
            (winreg.HKEY_LOCAL_MACHINE,
             r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"))
    items = []
    for hive, path in keys:
        try:
            k = winreg.OpenKey(hive, path)
        except OSError:
            continue
        i = 0
        while True:
            try:
                name, val, _ = winreg.EnumValue(k, i)
            except OSError:
                break
            items.append("%s → %s" % (name, str(val)[:70]))
            i += 1
        winreg.CloseKey(k)
    return items


def _nearby_module():
    import importlib.util
    import sys as _sys
    here = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(here, "tools", "nearby.py")
    if not os.path.exists(p):
        return None, "找不到 %s" % p
    try:
        spec = importlib.util.spec_from_file_location("fairy_nearby", p)
        m = importlib.util.module_from_spec(spec)
        _sys.modules["fairy_nearby"] = m
        spec.loader.exec_module(m)
        return m, ""
    except Exception as e:
        return None, "加载 nearby.py 失败: %s" % e

def _i(p, k, d, lo, hi):
    try:
        v = int((p or {}).get(k, d))
    except Exception:
        v = d
    return max(lo, min(hi, v))

def _nearby_scan(params=None):
    p = params or {}
    m, err = _nearby_module()
    if not m:
        return err
    r = m.scan(ble_seconds=_i(p, "ble_seconds", 6, 1, 30),
               mdns_seconds=_i(p, "mdns_seconds", 4, 1, 20),
               ssdp_seconds=_i(p, "ssdp_seconds", 3, 1, 15),
               do_ble=bool(p.get("ble", True)),
               do_mdns=bool(p.get("mdns", True)),
               do_ssdp=bool(p.get("ssdp", True)),
               probe=bool(p.get("probe", False)))
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "logs", "nearby_last.json"), "w", encoding="utf-8") as f:
            json.dump(r, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
    return m.summary(r)

def _nearby_identify(params=None):
    p = params or {}
    addr = (p.get("addr") or p.get("mac") or "").strip()
    if not addr:
        return "需要 addr（MAC 或 BLE 地址）"
    m, err = _nearby_module()
    if not m:
        return err
    d = m.identify(addr)
    L = ["地址 %s" % d["addr"], "厂牌：%s" % (d["vendor"] or "（不在小厂牌表里，不猜）")]
    if d.get("kind"):
        L.append("类型：%s" % d["kind"])
    for h in d.get("hint", []):
        L.append("· " + h)
    return "\n".join(L)

def _nearby_watch(params=None):
    p = params or {}
    m, err = _nearby_module()
    if not m:
        return err
    sec = _i(p, "seconds", 20, 5, 120)
    ble = _i(p, "ble_seconds", 4, 2, 15)
    lines = []
    r = m.watch(seconds=sec, ble_seconds=ble,
                on_event=lambda kind, d: lines.append("%s %s %s"
                                                      % ("＋出现" if kind == "appear" else
                                                         ("－离线" if kind == "leave" else "·开始前已在"),
                                                         d.get("kind"), d.get("name"))))
    ev = r.get("events") or []
    head = "监控 %.0f 秒：起始 %d 个设备，期间 %d 次变化" % (r.get("elapsed", sec),
                                                          len(r.get("baseline") or []), len(ev))
    if not ev:
        return head + "\n（期间没有设备上下线）"
    return head + "\n" + "\n".join(lines)

def _capability_check(params=None):
    import importlib.util
    import sys as _sys
    here = os.path.dirname(os.path.abspath(__file__))
    p = os.path.join(here, "tools", "capability.py")
    if not os.path.exists(p):
        return "找不到 %s" % p
    try:
        spec = importlib.util.spec_from_file_location("fairy_cap", p)
        mod = importlib.util.module_from_spec(spec)
        _sys.modules["fairy_cap"] = mod
        spec.loader.exec_module(mod)
        return mod.report(full=bool((params or {}).get("full")))
    except Exception as e:
        return "能力自检失败: %s: %s" % (type(e).__name__, e)

def _voice_status(params=None):
    v = CONFIG.get("voice") or {}
    fp = v.get("fairy_pack") or {}
    profs = v.get("profiles") or {}
    act = v.get("active_profile") or "(未设置)"
    out = []
    out.append("【当前音色】%s（%s）"
               % (act, (profs.get(act) or {}).get("label", "")))
    if profs:
        out.append("  可选：%s" % "；".join("%s=%s" % (k, x.get("label", ""))
                                          for k, x in profs.items()))
    for k, x in profs.items():
        ref = x.get("ref_audio") or ("自动（语料库原声）" if x.get("auto_ref") else "未设置")
        out.append("    %s 参考音频 = %s%s" % (k, ref,
                   "｜优先原声直出" if x.get("original_first") else ""))
    for name in ("indextts", "gpstts"):
        en, base, alive = _engine_state(name)
        if not en:
            out.append("【引擎·%s】未启用" % name)
        else:
            out.append("【引擎·%s】已启用 %s｜%s"
                       % (name, base, "服务在线" if alive else "**服务未启动**"))
    out.append("【兜底】edge-tts（各音色可单独配 edge_voice）")
    try:
        pack = _get_pack()
        out.append("【原声语料库】%d 条（%s）"
                   % (len(pack), "启用" if fp.get("enabled", True) else "关闭"))
    except Exception as e:
        out.append("【原声语料库】加载失败: %s" % e)
    try:
        import pyaudio
        pa = pyaudio.PyAudio()
        try:
            dflt = str(pa.get_default_input_device_info().get("name"))
        except Exception:
            dflt = "(无)"
        cands = _probe_inputs(pa)
        total_in = sum(1 for i in range(pa.get_device_count())
                       if pa.get_device_info_by_index(i).get("maxInputChannels", 0) > 0)
        out.append("【录音设备】输入端点 %d 个，其中【真能打开】的只有 %d 个"
                   % (total_in, len(cands)))
        out.append("  系统默认输入 = %s%s" % (dflt, "" if _looks_like_mic(dflt) else "（不是麦克风！）"))
        for c in cands[:6]:
            out.append("   [%d] %-34s %-6s %-18s %dHz%s"
                       % (c["index"], c["name"][:34], c.get("kind", "?"), c["api"], c["rate"],
                          "  ← 回环，会录到系统声音" if c["loopback"] else ""))
        mics = [c for c in cands if not c["loopback"] and _looks_like_mic(c["name"])]
        if mics:
            out.append("  结论：可用麦克风 %d 个，自动选 [%d] %s（%s）"
                       % (len(mics), mics[0]["index"], mics[0]["name"], mics[0].get("kind")))
            out.append("  即插即用已开启：USB / 3.5mm / 蓝牙免手持 / 无线接收器"
                       "插上后会自动切过去，拔掉自动退回。")
        else:
            out.append("  结论：**当前没有可用的真麦克风**，只有回环设备。")
            out.append("  插上 USB 麦克风 / 蓝牙耳机 / 无线接收器，"
                       "或在 Windows 声音设置里启用板载麦克风，然后再跑一次本检查即可。")
        wanted = v.get("input_device") or ""
        out.append("  config.voice.input_device = %s"
                   % (repr(wanted) if wanted else "空（自动）"))
        pa.terminate()
    except Exception as e:
        out.append("【录音设备】探测失败: %s" % e)
    md = (v.get("whisper") or {}).get("download_root") or "models/whisper"
    if not os.path.isabs(md):
        md = os.path.join(_HERE, md)
    sz = 0
    if os.path.isdir(md):
        sz = sum(os.path.getsize(os.path.join(r, x)) for r, _, fs in os.walk(md) for x in fs)
    ws = v.get("whisper") or {}
    out.append("【语音识别】模型 %s / 语言 %s｜已缓存 %.0f MB in %s"
               % (ws.get("model_size", "small"), ws.get("language", "zh"), sz / 2 ** 20, md))
    return "\n".join(out)

def _security_status(params=None):
    rep = []
    so, se = _ps("Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct | "
                 "Select-Object displayName,productState | ConvertTo-Json -Compress")
    if so:
        try:
            av = json.loads(so)
            av = av if isinstance(av, list) else [av]
            rep.append("【杀毒软件】已注册 %d 个：" % len(av) +
                       "；".join("%s（状态码 %s）" % (a.get("displayName"), a.get("productState"))
                                 for a in av))
        except Exception:
            rep.append("【杀毒软件】" + so[:200])
    else:
        rep.append("【杀毒软件】查询失败：%s" % (se[:120] or "无返回"))

    so, se = _ps("Get-MpComputerStatus | Select-Object AntivirusEnabled,"
                 "RealTimeProtectionEnabled,AntivirusSignatureLastUpdated | "
                 "ConvertTo-Json -Compress", timeout=90)
    if so:
        rep.append("【Windows Defender】" + so[:200])
    else:
        rep.append("【Windows Defender】不可用（模块不存在，通常是被第三方杀软接管）")

    fw = []
    cur = None
    for ln in _capture("netsh advfirewall show allprofiles state").splitlines():
        m = re.search(r"(\S*配置文件)", ln)
        if m:
            cur = m.group(1)
        m2 = re.search(r"状态\s+(\S+)", ln)
        if m2 and cur:
            fw.append("%s %s" % (cur, m2.group(1)))
    rep.append("【防火墙】" + ("；".join(fw) if fw else "读取失败"))

    listen = []
    for ln in _capture("netstat -ano -p TCP").splitlines():
        if "LISTENING" not in ln:
            continue
        parts = ln.split()
        if len(parts) >= 5:
            listen.append((parts[1], parts[-1]))
    wild = [x for x in listen if x[0].startswith("0.0.0.0:")]
    rep.append("【监听端口】TCP LISTENING 共 %d 条，其中对外监听 %d 条：%s"
               % (len(listen), len(wild),
                  "、".join(sorted({w[0].split(":")[-1] for w in wild}))[:200]))

    items = _startup_items()
    rep.append("【开机启动项】共 %d 条：%s" % (len(items), "；".join(items[:10])))

    rep.append("说明：以上只做读取。要改防火墙/杀软设置，我会先跟你确认。")
    return "\n".join(rep)


def _mouse_move(p):
    pyautogui.moveTo(int(p.get("x")), int(p.get("y")), duration=0.3)
    return "鼠标移动到 (%s,%s)" % (p.get("x"), p.get("y"))

def _mouse_click(p):
    x = p.get("x")
    y = p.get("y")
    if x is not None and y is not None:
        pyautogui.click(int(x), int(y), button=p.get("button", "left"),
                        clicks=int(p.get("clicks", 1)),
                        interval=float(p.get("interval", 0.05)))
    else:
        pyautogui.click(button=p.get("button", "left"),
                        clicks=int(p.get("clicks", 1)),
                        interval=float(p.get("interval", 0.05)))
    return "已%s点击" % p.get("button", "left")

def _mouse_scroll(p):
    pyautogui.scroll(int(p.get("amount", 0)))
    return "滚动 %s" % p.get("amount")

def _keyboard_hotkey(p):
    pyautogui.hotkey(*p.get("keys", []))
    return "按下快捷键 %s" % "+".join(p.get("keys", []))

TOOL_REGISTRY = {
    "get_screen_info": (lambda p: "屏幕分辨率: %dx%d" % (
        win32api.GetSystemMetrics(0), win32api.GetSystemMetrics(1)),
        "获取屏幕分辨率"),
    "mouse_move": (_mouse_move, "移动鼠标到坐标 {x,y}（像素）"),
    "mouse_click": (_mouse_click, "鼠标点击，参数 {x,y,button,clicks}"),
    "mouse_scroll": (_mouse_scroll, "滚动鼠标滚轮 {amount}（正数向上）"),
    "keyboard_type": (lambda p: _keyboard_type(p.get("text", "")),
        "输入文字 {text}（支持中文，通过剪贴板）"),
    "keyboard_hotkey": (_keyboard_hotkey, "按下组合键 {keys:[\"ctrl\",\"c\"]}"),
    "open_target": (lambda p: _open_target(p.get("target", "")),
        "打开应用/文件/文件夹/网址 {target}"),
    "run_command": (lambda p: _run_shell(p.get("command", ""), True),
        "执行命令行命令 {command}（危险命令需确认）"),
    "screenshot": (lambda p: _screenshot(p.get("save_to", "")),
        "全屏截图保存 {save_to}（留空存桌面）"),
    "vision": (lambda p: _vision(p.get("mode", "反推"), p.get("save_to", "")),
        "截屏并理解图片：{mode:反推|描述|翻译}（反推=生成SD提示词）"),
    "app_use": (lambda p: _app_use(p.get("need", ""), p.get("args", "")),
        "调度工具：{need}需求(如写代码/下载/写文档/鸿蒙/安卓/C++),{args}可选参数；没装会自动下载安装"),
    "write_code": (lambda p: _write_code_tool(p.get("path", ""), p.get("code", "")),
        "写代码并打开IDE：{path}文件路径,{code}代码内容（自动查错+按语言选IDE打开）"),
    "catalog": (lambda p: _catalog_check()[0],
        "软件分类总览：查看缺哪些类软件（已装自动归类+缺类代表）"),
    "install_missing": (lambda p: _catalog_install(),
        "自动安装全部缺类代表软件（免费官方直链）"),
    "read_file": (lambda p: _read_file(p.get("path", "")),
        "读取文件内容 {path}"),
    "write_file": (lambda p: _write_file(p.get("path", ""), p.get("content", "")),
        "写入文件 {path,content}"),
    "list_dir": (lambda p: _list_dir(p.get("path", ".")),
        "列出目录内容 {path}"),
    "clipboard_get": (lambda p: _clipboard_get(), "读取剪贴板内容"),
    "clipboard_set": (lambda p: _clipboard_set(p.get("text", "")),
        "写入剪贴板 {text}"),
    "calc": (lambda p: str(safe_eval(p.get("expression", "0"))),
        "安全计算数学表达式 {expression}"),
    "volume": (lambda p: _set_volume(p.get("percent", 50)),
        "设置系统音量 {percent}（0-100）"),
    "window_manage": (lambda p: _window_manage(p.get("action", "activate"), p.get("title", "")),
        "窗口管理 {action:activate|minimize|close, title:窗口标题片段}"),
    "search": (lambda p: _bing_search(p.get("query", "")),
        "网页搜索 {query}，返回结果摘要"),
    "note": (lambda p: _note(p.get("text", "")),
        "记一条便签 {text}"),
    "get_time": (lambda p: datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "获取当前日期时间"),
    "comfy_status": (lambda p: _comfy_status(p),
        "检查本地 ComfyUI 服务与可见模型（只读，不生成）"),
    "comfy_generate": (lambda p: _comfy_generate(p),
        "调用 ComfyUI 生成图片 {prompt,negative,width,height,steps,seed}，返回图片路径"),
    "security_status": (lambda p: _security_status(p),
        "本机安全体检（只读）：杀软/Defender/防火墙/监听端口/开机启动项"),
    "voice_status": (lambda p: _voice_status(p),
        "语音链体检（只读）：音色档案/克隆引擎/录音设备/识别模型，排查听不到或听不清"),
    "voice_list": (lambda p: _voice_switch({}),
        "列出所有音色与当前音色"),
    "voice_switch": (lambda p: _voice_switch(p),
        "切换音色 {profile}：fairy=Fairy本体音色 / custom=自定义音色"),
    "voice_set_custom": (lambda p: _voice_set_custom(p),
        "注册自定义音色 {audio: 音频文件路径, profile, text, label, activate}"),
    "scan_nearby": (lambda p: _nearby_scan(p),
        "扫描附近设备（只读/纯本地，不发云请求）：BLE蓝牙广播+mDNS+UPnP+局域网。"
        "参数 {ble_seconds,mdns_seconds,ssdp_seconds}，都可不传"),
    "identify_device": (lambda p: _nearby_identify(p),
        "按 MAC/BLE 地址推断厂牌与设备类型 {addr}"),
    "watch_devices": (lambda p: _nearby_watch(p),
        "监控附近设备上下线 {seconds,ble_seconds}，返回期间新出现/离线的设备"),
    "capability_check": (lambda p: _capability_check(p),
        "本机能力自检（只读）：显存/内存/CPU/磁盘/在跑的后端 + 本地模型清单，"
        "按显存给「能直接跑 / 要 offload / 跑不动」的结论 {full}"),
}

def exec_tool(tool_call):
    name = tool_call.get("tool", "")
    params = tool_call.get("params", {}) or {}
    if name not in TOOL_REGISTRY:
        return "未知工具: %s（可用: %s）" % (name, ", ".join(TOOL_REGISTRY))
    fn, _ = TOOL_REGISTRY[name]
    try:
        return str(fn(params))
    except Exception as e:
        return "工具 %s 执行出错: %s" % (name, e)

def tools_description():
    lines = ["可用工具："]
    for name, (_, desc) in TOOL_REGISTRY.items():
        lines.append("- %s：%s" % (name, desc))
    return "\n".join(lines)


MAX_TOOLS_PER_REPLY = 3
MAX_TOOLS_PER_TURN = 6

def extract_tool_calls(reply):
    text = reply or ""
    blocks = re.findall(r"```(?:tool|json|tool_call)\s*(.*?)```", text, re.S)
    if not blocks:
        blocks = re.findall(r"<<<TOOL>>>(.*?)<<<END>>>", text, re.S)
    out = []
    for b in blocks:
        m = re.search(r"\{.*\}", b, re.S)
        if not m:
            continue
        try:
            obj = json.loads(m.group(0))
        except Exception:
            continue
        if isinstance(obj, dict):
            if obj.get("tool"):
                out.append(obj)
            elif isinstance(obj.get("tool_calls"), list):
                out.extend([x for x in obj["tool_calls"] if isinstance(x, dict)])
        elif isinstance(obj, list):
            out.extend([x for x in obj if isinstance(x, dict) and x.get("tool")])
    return out[:MAX_TOOLS_PER_REPLY]

def clean_reply(text):
    s = text or ""
    s = re.sub(r"```(?:tool|json|tool_call)\s*.*?```", "", s, flags=re.S)
    s = re.sub(r"<<<TOOL>>>.*?<<<END>>>", "", s, flags=re.S)
    s = re.sub(r"</?think>", "", s)
    s = re.sub(r"</?(?:tool|params|tool_call)>", "", s)
    s = re.sub(r"\[请等待[^\]]*\]", "", s)
    return re.sub(r"\n{3,}", "\n\n", s).strip()

def _dsh_api_key():
    p = os.path.join(os.path.expanduser("~"), ".dsh", ".credentials.yaml")
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            t = f.read()
    except Exception as e:
        log("读 DSH 凭据失败: %s" % e)
        return ""
    m = re.search(r"DEEPSEEK_API_KEY:\s*(\S+)", t)
    return m.group(1).strip() if m else ""

def _need_thinking(text):
    t = (text or "").strip()
    if not t:
        return False
    st = (CONFIG.get("brain") or {}).get("strata_thinking") or {}
    simple_kw = st.get("simple") or [
        "等于", "是多少", "是几", "几点", "日期", "星期", "翻译", "怎么说",
        "什么意思", "定义", "查一下", "查询", "查表", "语法", "怎么写",
        "多少钱", "多长", "多大", "多高", "回显", "刚才", "我刚才",
        "拼音", "英文", "快捷键", "网址", "链接", "数字", "计算",
    ]
    hard_kw = st.get("hard") or [
        "为什么", "怎么办", "分析", "对比", "比较", "权衡", "取舍",
        "方案", "建议", "推荐", "优化", "设计", "调试", "排查", "修复",
        "原理", "解释", "创作", "写一", "写个", "文章", "规划", "策略",
        "影响", "区别", "优缺点", "利弊", "步骤", "流程", "推理",
        "证明", "论证", "代码调试", "bug", "报错怎么",
    ]
    hard_min = int(st.get("hard_min_len") or 60)

    if any(k in t for k in simple_kw) and not any(k in t for k in
            ("调试", "修复", "排查", "报错怎么", "bug")):
        return False

    if any(k in t for k in hard_kw):
        return True

    if len(t) > hard_min:
        return True
    return False

class Brain:
    def __init__(self, confirm_callback=None):
        self.cfg = CONFIG["brain"]
        self.history = []
        self.lock = threading.Lock()
        self.confirm = confirm_callback

    def _system_prompt(self):
        base = self.cfg["system_prompt"] + "\n\n" + tools_description() + \
            ("\n\n需要操作电脑或使用工具时，【只】输出下面这种围栏代码块，不要写别的东西：\n"
             "```tool\n{\"tool\":\"工具名\",\"params\":{...}}\n```\n"
             "一次只调用一个工具，等我把执行结果给你之后再决定下一步。"
             "工具名必须是上面列表里的名字，不要自创 <tool> 之类的别的格式。"
             "任务完成就直接给最终回复，不要重复调用同一个工具。")
        pack = _get_pack()
        if pack and len(pack):
            hints = pack.style_hints(36)
            if hints:
                base += ("\n\n【常用语】下面这些句子你有现成的游戏原声，只要意思合适，"
                         "就【原样】用其中一句回答（一个字都不要改、不要加字），这样能用原声说话：\n"
                         + "；".join(hints))
        return base

    def call(self, messages):
        provider = self.cfg.get("provider", "deepseek")
        if provider == "deepseek":
            return self._call_deepseek(messages)
        return self._call_ollama(messages)


    def call(self, messages):
        provs = [p for p in (self.cfg.get("providers") or []) if p.get("enabled", True)]
        if not provs:

            if self.cfg.get("provider", "deepseek") == "deepseek":
                return self._call_deepseek(messages)
            return self._call_ollama(messages)
        active = self.cfg.get("active_provider") or ""

        ordered = sorted(provs, key=lambda p: 0 if p.get("name") == active else 1)
        errs = []
        for p in ordered:
            name = p.get("name") or p.get("base_url") or "?"
            try:
                out, err = self._call_openai_like(p, messages)
            except Exception as e:
                out, err = None, "%s: %s" % (type(e).__name__, e)
            if out:
                if self.cfg.get("active_provider") != name:
                    self.cfg["active_provider"] = name
                    CONFIG["brain"]["active_provider"] = name
                    log("大脑已切到：%s" % name)
                return out
            errs.append("  · %s -> %s" % (name, err))
            log("大脑「%s」不可用：%s" % (name, err))
        return "所有大脑都不可用：\n" + "\n".join(errs)

    def _call_openai_like(self, p, messages):
        key = p.get("api_key") or ""
        if p.get("key_from_dsh") and not key:
            key = _dsh_api_key()
            if not key:
                return None, "DSH 凭据里没有 DEEPSEEK_API_KEY"
        base = (p.get("base_url") or "").rstrip("/")
        if not base:
            return None, "没有配 base_url"
        payload = {"model": p.get("model"), "messages": messages, "stream": False}
        if p.get("strata"):

            last_user = ""
            for m in reversed(messages):
                if m.get("role") == "user":
                    last_user = (m.get("content") or "").strip()
                    break
            think = _need_thinking(last_user)
            eb = dict(p.get("extra_body") or {})
            eb["chat_template_kwargs"] = {"enable_thinking": think}
            payload.update(eb)

            payload["max_tokens"] = 2048 if think else 512
        else:
            payload.update(p.get("extra_body") or {})
            if p.get("max_tokens"):
                payload["max_tokens"] = int(p["max_tokens"])
        try:
            r = requests.post(base + "/chat/completions",
                              headers={"Authorization": "Bearer " + (key or "none"),
                                       "Content-Type": "application/json"},
                              json=payload, timeout=int(p.get("timeout") or 120))
        except Exception as e:

            if p.get("strata") and self._ensure_strata():
                try:
                    r = requests.post(base + "/chat/completions",
                                      headers={"Authorization": "Bearer " + (key or "none"),
                                               "Content-Type": "application/json"},
                                      json=payload, timeout=int(p.get("timeout") or 120))
                except Exception as e2:
                    return None, "连接失败(%s): %s" % (base, e2)
            else:
                return None, "连接失败(%s): %s" % (base, e)
        if r.status_code != 200:
            return None, "HTTP %d: %s" % (r.status_code, r.text[:160])
        try:
            msg = r.json()["choices"][0]["message"]
        except Exception as e:
            return None, "返回无法解析: %s" % e
        content = (msg.get("content") or "").strip()
        if not content and msg.get("reasoning_content"):

            rc = str(msg.get("reasoning_content") or "").strip()
            if p.get("strata") and rc:
                return "（深度思考过程，截取尾部）\n" + rc[-1500:], ""
            return None, "只给了思考内容（推理模型没关思考）"
        if not content:
            return None, "返回空内容"
        return content, ""

    def _ensure_strata(self):
        strata_dir = (CONFIG.get("paths") or {}).get("strata_dir") or ""
        bat = os.path.join(strata_dir, "run-unsloth-ud-iq4_xs.bat") if strata_dir else ""
        if not bat or not os.path.isfile(bat):
            log("找不到 Strata 启动脚本：%s" % bat)
            return False
        try:
            subprocess.Popen(["cmd", "/c", bat], cwd=strata_dir,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            log("Strata 启动失败: %s" % e)
            return False
        log("176B 大脑未运行，已自动拉起，等待加载（约1-2分钟）…")
        for _ in range(18):
            time.sleep(5)
            try:
                s = socket.create_connection(("127.0.0.1", 8080), timeout=2)
                s.close()
                log("176B 大脑（Strata）就绪")
                return True
            except OSError:
                continue
        log("Strata 等待就绪超时（90秒），可稍后重试")
        return False

    def _call_deepseek(self, messages):
        provs = [p for p in (self.cfg.get("providers") or []) if p.get("enabled", True)]
        if provs:
            out, err = self._call_openai_like(provs[0], messages)
            return out or ("大脑不可用：%s" % err)
        return ("还没有配置大脑：config.json 的 brain.providers 为空。\n"
                "推荐顺序：① DSH/DeepSeek 云端（可自动从 DSH 凭据取 key）"
                "② 本地 llama-server（py -3.12 tools\\llama_start.py）")

    def _call_ollama(self, messages):
        o = self.cfg["ollama"]
        try:
            resp = requests.post(o["base_url"].rstrip("/") + "/api/chat",
                                 json={"model": o["model"], "messages": messages,
                                       "stream": False},
                                 timeout=180)
            if resp.status_code != 200:
                return "Ollama 请求失败(%d): %s" % (resp.status_code, resp.text[:300])
            return resp.json()["message"]["content"]
        except requests.ConnectionError:
            return "无法连接 Ollama（请先运行 ollama serve，并确认模型已 pull）"

    def _maybe_confirm(self, r):
        if not r.startswith("SECURITY_CONFIRM:"):
            return r
        cmd = r[len("SECURITY_CONFIRM:"):]
        if self.confirm:
            try:
                ok = self.confirm("FairyX 想执行命令：\n%s\n\n是否允许？" % cmd)
            except Exception:
                ok = False
        else:
            ok = False
        if ok:
            return _run_shell(cmd, confirm=False)
        return "用户拒绝了该命令，未执行。请向用户说明。"

    def think(self, user_text):
        with self.lock:
            messages = [{"role": "system", "content": self._system_prompt()}]
            for m in self.history[-self.cfg.get("history_limit", 20):]:
                messages.append(m)
            messages.append({"role": "user", "content": user_text})

            final_reply = None
            reply = ""
            executed = 0
            last_sig = None
            for _ in range(4):
                reply = self.call(messages)
                calls = extract_tool_calls(reply)
                if not calls:
                    final_reply = reply
                    break
                results = []
                for call in calls:
                    sig = json.dumps(call, sort_keys=True, ensure_ascii=False)
                    if sig == last_sig:
                        results.append({"tool": call.get("tool"),
                                        "result": "（与上一次完全相同的调用，已跳过，别重复）"})
                        continue
                    if executed >= MAX_TOOLS_PER_TURN:
                        results.append({"tool": call.get("tool"),
                                        "result": "（本轮工具调用已达上限，请直接给最终回复）"})
                        continue
                    last_sig = sig
                    executed += 1
                    try:
                        r = self._maybe_confirm(exec_tool(call))
                    except Exception as e:
                        r = "工具执行异常: %s" % e
                    results.append({"tool": call.get("tool"), "result": r})
                messages.append({"role": "assistant", "content": reply})
                messages.append({"role": "user",
                                 "content": "工具执行结果：\n" +
                                            json.dumps(results, ensure_ascii=False) +
                                            "\n根据结果继续。任务已完成就直接给最终回复，不要再调用工具。"})

            if final_reply is None:
                final_reply = reply
            final_reply = clean_reply(final_reply)

            self.history.append({"role": "user", "content": user_text})
            self.history.append({"role": "assistant", "content": final_reply})
            limit = self.cfg.get("history_limit", 20)
            if len(self.history) > limit * 2:
                self.history = self.history[-limit * 2:]
            return final_reply

    def clear_history(self):
        with self.lock:
            self.history = []


def _voice_cfg():
    return CONFIG.get("voice") or {}

def _active_profile():
    v = _voice_cfg()
    profs = v.get("profiles") or {}
    name = v.get("active_profile") or ""
    if name in profs:
        p = dict(profs[name])
        p["_key"] = name
        return p
    return {"_key": "", "label": "默认（原声直出）", "original_first": True}

def _resolve_rel(p):
    if p and not os.path.isabs(p):
        return os.path.normpath(os.path.join(_HERE, p))
    return p

def _engine_state(name):
    e = (_voice_cfg().get("engines") or {}).get(name) or {}
    base = e.get("base_url") or ""
    alive = False
    if e.get("enabled") and base:
        try:
            requests.get(base.rstrip("/") + "/health", timeout=3)
            alive = True
        except Exception:
            alive = False
    return bool(e.get("enabled")), base, alive

def _voice_switch(params=None):
    v = _voice_cfg()
    profs = v.get("profiles") or {}
    cur = v.get("active_profile") or "(未设置)"
    name = str((params or {}).get("profile") or "").strip()
    if not name:
        lines = ["当前音色：%s" % cur]
        for k, p in profs.items():
            mark = "●" if k == cur else "○"
            ref = p.get("ref_audio") or ("自动（语料库原声）" if p.get("auto_ref") else "未设置")
            lines.append("  %s %-8s %-14s 参考音频=%s"
                         % (mark, k, p.get("label", ""), ref))
        lines.append("切换：voice_switch {\"profile\":\"custom\"}")
        return "\n".join(lines)
    if name not in profs:
        return "没有这个音色：%s。可选：%s" % (name, "、".join(profs) or "（无）")
    v["active_profile"] = name
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(CONFIG, f, ensure_ascii=False, indent=2)
        saved = "已写入 config.json"
    except Exception as e:
        saved = "写配置失败: %s" % e
    return "已切换到音色「%s」（%s，%s）" % (name, profs[name].get("label", ""), saved)

def _voice_set_custom(params):
    p = params or {}
    src = str(p.get("audio") or p.get("path") or "").strip().strip('"')
    key = str(p.get("profile") or "custom").strip() or "custom"
    if not src:
        return "请给出音频文件路径，例如 {\"audio\":\"C:\\\\my_voice.wav\"}"
    if not os.path.exists(src):
        return "文件不存在：%s" % src
    dst_dir = os.path.join(_HERE, "voice", "profiles")
    os.makedirs(dst_dir, exist_ok=True)
    ext = os.path.splitext(src)[1].lower() or ".wav"
    if ext not in (".wav", ".mp3", ".m4a", ".flac", ".ogg"):
        return "不支持的音频格式：%s（建议先转成 wav）" % ext
    dst = os.path.join(dst_dir, "%s%s" % (key, ext))
    try:
        if os.path.abspath(src) != os.path.abspath(dst):
            shutil.copy2(src, dst)
    except Exception as e:
        return "复制音频失败：%s" % e
    v = CONFIG.setdefault("voice", {})
    profs = v.setdefault("profiles", {})
    prof = profs.setdefault(key, {})
    prof.setdefault("label", "自定义音色")
    prof.setdefault("edge_voice", "zh-CN-YunxiNeural")
    prof.setdefault("emo_alpha", 1.0)
    prof["original_first"] = False
    prof["ref_audio"] = os.path.relpath(dst, _HERE)
    if p.get("text"):
        prof["ref_text"] = str(p["text"])
    if p.get("label"):
        prof["label"] = str(p["label"])
    if p.get("activate", True):
        v["active_profile"] = key
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(CONFIG, f, ensure_ascii=False, indent=2)
        saved = "配置已保存"
    except Exception as e:
        saved = "写配置失败: %s" % e
    return ("自定义音色「%s」已登记为 %s（%s）\n参考音频：%s\n%s"
            % (prof["label"], key, saved, prof["ref_audio"],
               "已设为当前音色" if v.get("active_profile") == key else
               "用 voice_switch {\"profile\":\"%s\"} 切过去" % key))


_MIC_GOOD = ("麦克风", "microphone", "mic", "headset", "耳机", "耳麦", "收音",
             "array", "阵列", "usb audio", "usb pnp", "usb mic", "usb microphone",
             "hands-free", "handsfree", "hands free", "免手持", "ag audio", "hfp",
             "bluetooth", "蓝牙", "wireless", "无线", "receiver", "接收器", "dongle",
             "yeti", "blue ", "razer", "hyperx", "samson", "rode", "shure", "maono",
             "fifine", "audio-technica", "jabra", "logitech", "airpods", "buds",
             "wh-1000", "wf-1000", "soundcore", "edifier", "漫步者")

_LOOPBACK = ("立体声混音", "stereo mix", "混音", "what u hear", "loopback",
             "捕获", "主声音", "sound mapper", "mapper", "stereo input")

_MIC_BAD = _LOOPBACK + ("line input", "线路输入", "digital output", "数字输出",
                        "speakers", "扬声器", "hdmi", "display audio", "nahimic",
                        "mirroring", "virtual", "虚拟", "vb-audio", "voicemeeter")

def _is_loopback(name):
    return any(k in (name or "").lower() for k in _LOOPBACK)

def _mic_score(name, is_default=False):
    low = (name or "").lower()
    s = 0
    if any(k in low for k in _MIC_GOOD):
        s += 6
    if any(k in low for k in _MIC_BAD):
        s -= 8
    if _is_loopback(name):
        s -= 6
    if is_default:
        s += 3
    return s

def _looks_like_mic(name):
    return _mic_score(name) > 0

def _input_kind(name):
    low = (name or "").lower()
    if _is_loopback(name):
        return "回环(系统声音)"
    if any(k in low for k in ("line input", "线路输入")):
        return "线路输入(非麦)"
    if any(k in low for k in ("array", "阵列", "智音")):
        return "阵列麦克风"
    if any(k in low for k in ("hands-free", "handsfree", "hands free", "免手持",
                              "ag audio", "bluetooth", "蓝牙")):
        return "蓝牙免手持"
    if any(k in low for k in ("wireless", "无线", "receiver", "接收器", "dongle")):
        return "无线接收器"
    if any(k in low for k in ("usb audio", "usb pnp", "usb mic", "usb microphone", "usb")):
        return "USB"
    if any(k in low for k in ("realtek", "hd audio", "板载", "onboard")):
        return "板载/3.5mm"
    return "其他"

def _probe_inputs(pa, with_default=True):
    try:
        dflt = str(pa.get_default_input_device_info().get("name", "")) if with_default else ""
    except Exception:
        dflt = ""
    cands = []
    for i in range(pa.get_device_count()):
        try:
            d = pa.get_device_info_by_index(i)
        except Exception:
            continue
        if d.get("maxInputChannels", 0) <= 0:
            continue
        name = str(d.get("name", ""))
        try:
            api = pa.get_host_api_info_by_index(d["hostApi"])["name"]
        except Exception:
            api = "?"
        native = int(d.get("defaultSampleRate") or 44100)
        rate = None
        for r in (16000, native, 48000, 44100, 32000, 8000):
            try:
                st = pa.open(format=8, channels=1, rate=r, input=True,
                             frames_per_buffer=max(160, r // 10), input_device_index=i)
                st.stop_stream()
                st.close()
                rate = r
                break
            except Exception:
                continue
        if rate:
            cands.append({"index": i, "name": name, "api": api, "rate": rate,
                          "loopback": _is_loopback(name),
                          "kind": _input_kind(name),
                          "score": _mic_score(name, name == dflt)})
    cands.sort(key=lambda c: (-c["score"], 0 if c["rate"] == 16000 else 1))
    return cands


_PACK = None

class FairyVoicePack:
    def __init__(self, index_path, cfg=None):
        self.cfg = cfg or {}
        self.dir = ""
        self.by_key = {}
        if not index_path or not os.path.exists(index_path):
            log("原声语料库不存在（%s），只用 TTS" % index_path)
            return
        try:
            d = json.load(open(index_path, encoding="utf-8"))
            vd = d.get("voices_dir") or ""
            if not vd:
                vd = os.path.dirname(index_path)
            elif not os.path.isabs(vd):
                vd = os.path.join(os.path.dirname(index_path), vd)
            self.dir = vd
            for r in d.get("lines", []):
                k = r.get("t")
                if k and k not in self.by_key:
                    self.by_key[k] = r
            log("原声语料库已加载：%d 条（%s）" % (len(self.by_key), index_path))
        except Exception as e:
            log("原声语料库加载失败: %s" % e)

    def __len__(self):
        return len(self.by_key)

    @staticmethod
    def _norm(s):
        s = re.sub(r"<[^>]*>", "", s or "")
        return re.sub(r"[\s，。！？、；：\"'（）《》…—～·,.!?;:()\[\]{}<>~\-]+", "", s)

    def path_of(self, row):
        return os.path.join(self.dir, row["w"])

    def match(self, text):
        key = self._norm(text)
        if not key or not self.by_key:
            return None
        row = self.by_key.get(key)
        if row:
            return (self.path_of(row), 1.0, row["r"])
        max_len = int(self.cfg.get("max_reply_len", 40))
        if len(key) > max_len:
            return None
        min_score = float(self.cfg.get("min_score", 0.86))
        import difflib
        best, best_row = 0.0, None
        for k, r in self.by_key.items():
            s = difflib.SequenceMatcher(None, key, k).ratio()
            if s > best:
                best, best_row = s, r
                if best >= 0.999:
                    break
        if best_row is not None and best >= min_score:
            return (self.path_of(best_row), best, best_row["r"])
        return None

    def style_hints(self, n=36):
        bad_head = re.compile(r"^[…—\-]+|^[0-9０-９一二三四五六七八九十百]+(分钟|秒|小时|天|月|日|年|点)")
        rows = sorted(self.by_key.values(), key=lambda r: (len(r["t"]), r["d"]))
        out = []
        for r in rows:
            t = r["r"].strip()
            if not (2 <= len(t) <= 12):
                continue
            if not t.endswith(("。", "！", "？", "!", "?")):
                continue
            if bad_head.search(t) or "…" in t or "——" in t:
                continue
            if re.search(r"[<>]", t):
                continue
            out.append(t)
            if len(out) >= n:
                break
        return out

def _get_pack():
    global _PACK
    if _PACK is None:
        fp = (CONFIG.get("voice") or {}).get("fairy_pack") or {}
        idx = ""
        if fp.get("enabled", True):
            idx = fp.get("index") or os.path.join("voice", "voice_index.json")
            if not os.path.isabs(idx):
                idx = os.path.join(_HERE, idx)
        _PACK = FairyVoicePack(idx, fp)
    return _PACK

class VoiceEngine:
    def __init__(self, on_result, on_state):
        self.cfg = CONFIG["voice"]
        self.on_result = on_result
        self.on_state = on_state
        self.listening = False
        self.muted = False
        self._asr = None
        self._audio = None
        self._thread = None
        self._stop = threading.Event()
        self.pack = _get_pack()
        self._input_name = None
        self._input_rate = 16000
        self._cands = None
        self._dev = None
        self._probe_t = 0
        self.real_mic = False
        self.last_error = ""
        self._ref_cache = None


    def ensure(self):
        errs = []
        if self._audio is None:
            try:
                import pyaudio
                self._audio = pyaudio.PyAudio()
            except Exception as e:
                errs.append("pyaudio 未安装或初始化失败: %s" % e)
        if self._audio is not None and self._cands is None:
            try:
                self._refresh_devices(force=True)
                if not self._cands:
                    errs.append("没有任何可打开的录音设备")
                else:
                    self._dev = self._dev or self._pick_input_device(self._cands)
                    reals = [c for c in self._cands
                             if not c["loopback"] and _looks_like_mic(c["name"])]
                    self.real_mic = bool(reals)
                    if self._dev:
                        log("录音设备: [%d] %s（%s，%dHz，%s）%s"
                            % (self._dev["index"], self._dev["name"],
                               self._dev.get("kind", "?"), self._dev["rate"],
                               self._dev["api"],
                               " [回环：录的是系统声音]" if _is_loopback(self._dev["name"]) else ""))
                    log("可用麦克风 %d 个；插拔 USB/蓝牙/无线接收器会自动切换" % len(reals))
            except Exception as e:
                errs.append("枚举录音设备失败: %s" % e)
        if self._asr is None:
            try:
                from faster_whisper import WhisperModel
                w = self.cfg.get("whisper") or {}
                size = w.get("model_size", "small")
                root = w.get("download_root") or ""
                if root and not os.path.isabs(root):
                    root = os.path.join(_HERE, root)
                log("正在加载 Whisper 模型(%s)…（首次运行会联网下载，约 0.5GB）" % size)
                self._asr = WhisperModel(size, device="cpu", compute_type="int8",
                                         download_root=(root or None))
            except Exception as e:
                errs.append("faster-whisper 加载失败: %s" % e)
        if errs:
            log("语音引擎部分组件缺失：" + "；".join(errs))
        return not errs

    def _pick_input_device(self, cands):
        want = self.cfg.get("input_device") or ""
        if isinstance(want, int) or (isinstance(want, str) and str(want).strip().isdigit()):
            idx = int(want)
            for c in cands:
                if c["index"] == idx:
                    return c
            self.last_error = "配置指定的录音设备 [%d] 打不开（WDM-KS 设备常如此）" % idx
            log(self.last_error + "，改回自动选择")
        elif str(want).strip():
            w = str(want).strip().lower()
            for c in cands:
                if w in c["name"].lower():
                    return c
            self.last_error = "配置指定的录音设备「%s」不在可打开列表里" % want
            log(self.last_error + "，改回自动选择")
        return cands[0] if cands else None

    def _open_input(self):
        if self._dev is None:
            self._cands = _probe_inputs(self._audio)
            self._dev = self._pick_input_device(self._cands)
        if self._dev is None:
            return None, 16000
        dev = self._dev
        return (self._audio.open(format=8, channels=1, rate=dev["rate"], input=True,
                                 frames_per_buffer=max(160, dev["rate"] // 10),
                                 input_device_index=dev["index"]),
                dev["rate"])

    def _refresh_devices(self, force=False):
        now = time.time()
        if not force and (now - getattr(self, "_probe_t", 0)) < 10:
            return False
        self._probe_t = now
        try:
            cands = _probe_inputs(self._audio)
        except Exception as e:
            log("重新探测录音设备失败: %s" % e)
            return False
        if not cands:
            return False
        old = self._dev
        new = self._pick_input_device(cands)
        self._cands = cands
        if new and (not old or new["index"] != old["index"]):
            self._dev = new
            log("录音设备%s：[%d] %s（%s，%dHz）"
                % ("自动切换" if old else "选定", new["index"], new["name"],
                   new.get("kind", "?"), new["rate"]))
            return True
        self._dev = new or old
        return False

    @staticmethod
    def _to_16k(raw, rate):
        import numpy as np
        a = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        if rate == 16000 or a.size == 0:
            return a / 32768.0
        n = int(a.size * 16000 / float(rate))
        if n <= 1:
            return a[:0] / 32768.0
        x = np.linspace(0.0, a.size - 1.0, n)
        return (np.interp(x, np.arange(a.size, dtype=np.float32), a) / 32768.0).astype(np.float32)


    def _record_phrase(self):
        if self._audio is None:
            return None, 16000
        try:
            stream, rate = self._open_input()
        except Exception as e:
            log("录音失败: %s" % e)
            return None, 16000
        if stream is None:
            return None, 16000
        CHUNK = max(160, rate // 10)
        frames = []
        preroll = []
        preroll_max = max(1, int(0.5 * rate / CHUNK))
        silent = 0.0
        started = False
        silence_limit = self.cfg.get("vad_silence_sec", 2.0)
        max_sec = self.cfg.get("max_record_sec", 15)
        start_time = time.time()
        try:
            while True:
                data = stream.read(CHUNK, exception_on_overflow=False)
                rms = (ctypes.c_short * (len(data) // 2)).from_buffer_copy(data)
                energy = sum(abs(s) for s in rms) / max(1, len(rms))
                if energy > 500:
                    if not started:
                        frames.extend(preroll)
                        preroll = []
                    started = True
                    silent = 0.0
                elif started:
                    silent += CHUNK / float(rate)
                else:
                    preroll.append(data)
                    if len(preroll) > preroll_max:
                        preroll.pop(0)
                if started:
                    frames.append(data)
                if started and silent >= silence_limit:
                    break
                if time.time() - start_time > max_sec:
                    break
        finally:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass
        if not frames:
            return None, rate
        return b"".join(frames), rate


    def _transcribe(self, raw, rate=16000):
        samples = self._to_16k(raw, rate)
        if samples.size == 0:
            return ""
        w = self.cfg.get("whisper") or {}
        kw = {"language": w.get("language", "zh"), "vad_filter": True, "beam_size": 1}


        prompt = w.get("initial_prompt")
        if prompt:
            kw["initial_prompt"] = prompt
        segments, _ = self._asr.transcribe(samples, **kw)
        return "".join(s.text for s in segments).strip()


    def _loop(self):
        while not self._stop.is_set():
            if not self.listening:
                time.sleep(0.2)
                continue
            self._refresh_devices()
            got = self._record_phrase()
            if not got:
                continue
            raw, rate = got
            if raw is None:
                continue
            self.on_state(STATE_THINKING)
            try:
                text = self._transcribe(raw, rate)
            except Exception as e:
                log("识别失败: %s" % e)
                self.on_state(STATE_LISTENING)
                continue
            if text:
                log("识别到: %s" % text)
                self.on_result(text)

    def start_listening(self):
        if not self.ensure() or self._audio is None or self._asr is None:
            if not self.last_error:
                self.last_error = "语音引擎未就绪（缺 pyaudio 或 Whisper 模型未下载）"
            return False
        if self._dev and _is_loopback(self._dev["name"]) and not self.real_mic and \
                not self.cfg.get("allow_loopback_input"):
            self.last_error = (
                "当前唯一能打开的录音设备是回环设备「%s」——它录到的是系统在放什么，"
                "不是你的说话声。\n开着监听我就会听到自己的播报，然后自己跟自己聊起来，"
                "所以先拒绝开启。\n\n解决办法（任一）：\n"
                "  · 在 Windows 声音设置里启用麦克风\n"
                "  · 插上 3.5mm 麦克风 / 连上蓝牙耳机\n"
                "  · 确实要用回环：config.json 里设 voice.allow_loopback_input = true"
                % self._dev["name"])
            log("拒绝开启监听：" + self.last_error.replace("\n", " "))
            return False
        self.last_error = ""
        self.listening = True
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        self.on_state(STATE_LISTENING)
        return True

    def stop_listening(self):
        self.listening = False
        self.on_state(STATE_IDLE)


    def active_profile(self):
        return _active_profile()

    def _ref_audio(self, prof):
        r = _resolve_rel((prof or {}).get("ref_audio") or "")
        if r and os.path.exists(r):
            return r
        if r:
            log("参考音频不存在，改自动挑选: %s" % r)
        if (prof or {}).get("auto_ref") and self.pack and len(self.pack):
            if self._ref_cache and os.path.exists(self._ref_cache):
                return self._ref_cache
            rows = [x for x in self.pack.by_key.values() if 4.0 <= x.get("d", 0) <= 7.0]
            if rows:
                rows.sort(key=lambda x: x["d"])
                pick = rows[len(rows) // 2]
                self._ref_cache = self.pack.path_of(pick)
                log("自动选定参考音频：%s（%.2fs）" % (os.path.basename(self._ref_cache), pick["d"]))
                return self._ref_cache
        return ""

    def _http_tts(self, base, payload, timeout=300):
        r = requests.post(base.rstrip("/") + "/tts", json=payload, timeout=timeout)
        if r.status_code != 200:
            log("合成服务返回 %d：%s" % (r.status_code, r.text[:180]))
            return None
        tmp = os.path.join(tempfile.gettempdir(),
                           "fairyx_clone_%s.wav"
                           % datetime.datetime.now().strftime("%H%M%S%f"))
        with open(tmp, "wb") as f:
            f.write(r.content)
        return tmp

    def _call_clone(self, engine, text, prof, ref):
        e = (_voice_cfg().get("engines") or {}).get(engine) or {}
        base = e.get("base_url") or ""
        if not base:
            return None
        if engine == "indextts":
            return self._http_tts(base, {"text": text, "ref_audio": ref,
                                         "emo_alpha": (prof or {}).get("emo_alpha", 1.0)})
        if engine == "gpstts":
            return self._http_tts(base, {"text": text,
                                         "text_lang": e.get("text_lang", "zh"),
                                         "ref_audio_path": ref,
                                         "prompt_text": (prof or {}).get("ref_text", ""),
                                         "prompt_lang": e.get("prompt_lang", "zh"),
                                         "streaming_mode": False})
        return None

    def _edge_tts(self, text, voice=None):
        import asyncio
        import edge_tts

        tmp = os.path.join(tempfile.gettempdir(),
                           "fairyx_%s.mp3" % datetime.datetime.now().strftime("%H%M%S%f"))

        async def synth():
            c = edge_tts.Communicate(text, voice or self.cfg.get("tts_voice",
                                                                "zh-CN-XiaoxiaoNeural"))
            await c.save(tmp)

        asyncio.run(synth())
        return tmp

    def make_audio(self, text):
        prof = self.active_profile()
        label = prof.get("label", prof.get("_key") or "默认")
        fp = self.cfg.get("fairy_pack") or {}
        if prof.get("original_first") and fp.get("enabled", True) and self.pack and len(self.pack):
            try:
                hit = self.pack.match(text)
            except Exception as e:
                hit = None
                log("原声匹配出错: %s" % e)
            if hit:
                log("[%s] 原声直出（相似度 %.2f）：%s" % (label, hit[1], hit[2][:26]))
                return hit[0]
        ref = self._ref_audio(prof)
        for engine in ("indextts", "gpstts"):
            en, base, alive = _engine_state(engine)
            if not en:
                continue
            if not ref:
                log("[%s] %s 已启用但没有参考音频，跳过" % (label, engine))
                continue
            if not alive:
                log("[%s] %s 服务没起（%s），跳过" % (label, engine, base))
                continue
            try:
                p = self._call_clone(engine, text, prof, ref)
                if p:
                    log("[%s] %s 克隆合成完成" % (label, engine))
                    return p
            except Exception as e:
                log("[%s] %s 合成失败，继续降级: %s" % (label, engine, str(e)[:120]))
        return self._edge_tts(text, prof.get("edge_voice"))

    def speak(self, text, when_done=None):
        if self.muted or not self.cfg.get("enabled", True):
            if when_done:
                when_done()
            return
        was_listening = self.listening
        self.listening = False

        def _done():
            self.on_state(STATE_IDLE)
            if was_listening:
                self.listening = True
                self.on_state(STATE_LISTENING)
            if when_done:
                when_done()

        def _run():
            tmp = None
            try:
                path = self.make_audio(text)
                if not path or not os.path.exists(path):
                    log("没有可用的语音通道，跳过播报")
                    _done()
                    return
                import pygame
                if os.path.dirname(os.path.abspath(path)) == os.path.abspath(tempfile.gettempdir()):
                    tmp = path
                pygame.mixer.init()
                pygame.mixer.music.load(path)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    time.sleep(0.1)
                pygame.mixer.quit()
                if tmp:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
                _done()
            except Exception as e:
                log("语音播报失败（降级为文字）: %s" % e)
                _done()

        threading.Thread(target=_run, daemon=True).start()

    def shutdown(self):
        self._stop.set()
        self.listening = False
        if self._audio:
            try:
                self._audio.terminate()
            except Exception:
                pass


WS_EX_LAYERED = 0x00080000
GWL_EXSTYLE = -20
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
BI_RGB = 0
DIB_RGB_COLORS = 0

_user32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32

class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_byte), ("AlphaFormat", ctypes.c_byte)]

class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", _wt.DWORD), ("biWidth", _wt.LONG), ("biHeight", _wt.LONG),
                ("biPlanes", _wt.WORD), ("biBitCount", _wt.WORD),
                ("biCompression", _wt.DWORD), ("biSizeImage", _wt.DWORD),
                ("biXPelsPerMeter", _wt.LONG), ("biYPelsPerMeter", _wt.LONG),
                ("biClrUsed", _wt.DWORD), ("biClrImportant", _wt.DWORD)]

class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", _wt.DWORD * 3)]

_user32.GetDC.restype = _wt.HDC
_user32.GetDC.argtypes = [_wt.HWND]
_gdi32.CreateCompatibleDC.restype = _wt.HDC
_gdi32.CreateCompatibleDC.argtypes = [_wt.HDC]
_gdi32.CreateDIBSection.restype = _wt.HANDLE
_gdi32.CreateDIBSection.argtypes = [_wt.HDC, ctypes.POINTER(_BITMAPINFO), _wt.UINT,
                                    ctypes.POINTER(ctypes.c_void_p), _wt.HANDLE, _wt.DWORD]
_user32.UpdateLayeredWindow.argtypes = [
    _wt.HWND, _wt.HDC, ctypes.POINTER(_wt.POINT), ctypes.POINTER(_wt.SIZE),
    _wt.HDC, ctypes.POINTER(_wt.POINT), _wt.DWORD,
    ctypes.POINTER(_BLENDFUNCTION), _wt.DWORD]
_user32.GetParent.restype = _wt.HWND
_user32.GetParent.argtypes = [_wt.HWND]

_user32.GetWindowLongW.restype = ctypes.c_long
_user32.GetWindowLongW.argtypes = [_wt.HWND, ctypes.c_int]
_user32.SetWindowLongW.restype = ctypes.c_long
_user32.SetWindowLongW.argtypes = [_wt.HWND, ctypes.c_int, ctypes.c_long]
_gdi32.SelectObject.restype = _wt.HANDLE
_gdi32.SelectObject.argtypes = [_wt.HDC, _wt.HANDLE]

class LayeredWindow:

    def __init__(self, tk_window, width, height):
        self.win = tk_window
        self.w, self.h = width, height
        child = _wt.HWND(tk_window.winfo_id())
        parent = _user32.GetParent(child)
        self.hwnd = parent if parent else child
        ex = _user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
        _user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED)
        self.layered_ok = bool(_user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE) & WS_EX_LAYERED)
        self._screen_dc = _user32.GetDC(None)
        self._mem_dc = _gdi32.CreateCompatibleDC(self._screen_dc)
        bmi = _BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        self._ppv = ctypes.c_void_p()
        self._hbmp = _gdi32.CreateDIBSection(self._mem_dc, ctypes.byref(bmi),
                                             DIB_RGB_COLORS, ctypes.byref(self._ppv),
                                             None, 0)
        _gdi32.SelectObject(self._mem_dc, self._hbmp)

    def blit(self, bgra):
        ctypes.memmove(self._ppv, bgra.ctypes.data, bgra.nbytes)
        pt_src = _wt.POINT(0, 0)
        pt_dst = _wt.POINT(self.win.winfo_x(), self.win.winfo_y())
        size = _wt.SIZE(self.w, self.h)
        bf = _BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        return _user32.UpdateLayeredWindow(
            self.hwnd, self._screen_dc, ctypes.byref(pt_dst), ctypes.byref(size),
            self._mem_dc, ctypes.byref(pt_src), 0, ctypes.byref(bf), ULW_ALPHA)

def _premultiply(im):
    a = np.asarray(im.convert("RGBA"), dtype=np.uint16)
    al = a[:, :, 3:4]
    rgb = (a[:, :, :3] * al // 255).astype(np.uint8)
    return np.ascontiguousarray(np.dstack([rgb[:, :, 2], rgb[:, :, 1], rgb[:, :, 0],
                                           a[:, :, 3].astype(np.uint8)]))

def _key_black(im, floor=12, gain=1.15):
    rgb = np.asarray(im.convert("RGB"), dtype=np.float32)
    al = np.clip((rgb.max(axis=2) - floor) * gain, 0, 255)
    return Image.fromarray(np.dstack([rgb, al]).astype(np.uint8), "RGBA")

def _core_box(pils, thr=150, pad=1.12):
    x0 = y0 = 10 ** 9
    x1 = y1 = -1
    for im in pils:
        al = np.asarray(im)[:, :, 3]
        ys, xs = np.where(al > thr)
        if not len(xs):
            continue
        x0, y0 = min(x0, int(xs.min())), min(y0, int(ys.min()))
        x1, y1 = max(x1, int(xs.max())), max(y1, int(ys.max()))
    if x1 < 0:
        w, h = pils[0].size
        return (0, 0, w, h)
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    half = max(x1 - x0, y1 - y0) / 2.0 * pad
    return (int(cx - half), int(cy - half), int(cx + half), int(cy + half))

class _Ball:
    def __init__(self, frames, fps, source):
        self.frames = frames
        self.fps = fps
        self.source = source

def _virtual_screen():
    try:
        u = ctypes.windll.user32
        x = u.GetSystemMetrics(76)
        y = u.GetSystemMetrics(77)
        w = u.GetSystemMetrics(78)
        h = u.GetSystemMetrics(79)
        if w > 0 and h > 0:
            return x, y, w, h
    except Exception:
        pass
    return 0, 0, 1920, 1080

def _clamp_pos(px, py, size):
    vx, vy, vw, vh = _virtual_screen()
    try:
        px = max(vx, min(int(px), vx + vw - size))
        py = max(vy, min(int(py), vy + vh - size))
    except Exception:
        return 180, 180
    return px, py

class FloatingBall(tk.Tk):
    def __init__(self):
        super().__init__()
        self.cfg = CONFIG["floating_ball"]
        self.size = int(self.cfg.get("size", 72))
        self.state = STATE_IDLE
        self.brain = Brain(confirm_callback=self.request_confirm)
        self.voice = VoiceEngine(on_result=self.on_voice_result,
                                 on_state=self.set_state)
        self.confirmer_req = queue.Queue()
        self.confirmer_res = queue.Queue()
        self._drag = None
        self._press_time = 0
        self._press_pos = None
        self._moved = False
        self._pending_click = None
        self._last_release = 0
        self._panel = None


        self._anim_t = 0.0
        self._blink_phase = 0.0
        self._next_blink = random.uniform(1.5, 4.0)
        self._cur_color = STATE_COLORS[self.state]
        self._anim_after = None


        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg="#000000")
        pos = self.cfg

        _bx, _by = _clamp_pos(pos.get("position_x", 180),
                              pos.get("position_y", 180), self.size)
        self.geometry("%dx%d+%d+%d" % (self.size, self.size, _bx, _by))

        self.canvas = tk.Canvas(self, width=self.size, height=self.size,
                                bg="#000000", highlightthickness=0)
        self.canvas.pack()
        self.update_idletasks()
        self.update()


        self._ball = None
        self._layered = None
        self._frame_idx = -1
        self._halo_dirty = True
        self._halo_cache = {}
        self._anim_t0 = time.time()
        self._speed = 1.0
        self._ring_mask = None
        self._init_ball_assets()
        self._anim_tick()


        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", self._on_double)
        self.canvas.bind("<Button-3>", self._on_right)


        self.after(200, self._pump_confirm)
        self.protocol("WM_DELETE_WINDOW", self.shutdown)


        self._dl_bar = None
        self._dl_bar_canvas = None
        self.after(1500, self._pump_dl_progress)


    def _init_ball_assets(self):
        here = os.path.dirname(os.path.abspath(__file__))
        frames, fps, source = [], 33.0, ""
        for base in (os.path.join(here, "assets"),
                     os.path.join(os.path.dirname(here), "assets"),
                     os.path.join(os.path.expanduser("~"), "Desktop", "fairy", "assets")):
            sub = os.path.join(base, "ball_%d" % self.size)
            if not os.path.isdir(sub):
                continue
            files = sorted(f for f in os.listdir(sub) if f.endswith(".png"))
            if not files:
                continue
            meta = os.path.join(sub, "_meta.json")
            if os.path.exists(meta):
                try:
                    fps = float(json.load(open(meta, encoding="utf-8")).get("fps", fps))
                except Exception:
                    pass
            for f in files:
                frames.append(_premultiply(Image.open(os.path.join(sub, f))))
            source = sub
            break
        if not frames:

            gif = ""
            for raw in (here, os.path.join(here, "assets"),
                        os.path.join(os.path.dirname(here), "assets"),
                        os.path.join(os.path.expanduser("~"), "Desktop", "fairy")):
                if not os.path.isdir(raw):
                    continue
                gs = sorted(f for f in os.listdir(raw) if f.lower().endswith(".gif"))
                if gs:
                    gif = os.path.join(raw, gs[0])
                    break
            if gif:
                log("assets 缓存缺失，现场从 %s 生成动画帧…" % os.path.basename(gif))
                im = Image.open(gif)
                n = getattr(im, "n_frames", 1)
                fps = 1000.0 / max(1, (im.info.get("duration") or 40))
                pils = []
                for i in range(n):
                    im.seek(i)
                    pils.append(_key_black(im))
                box = _core_box(pils)
                for p in pils:
                    frames.append(_premultiply(
                        p.crop(box).resize((self.size, self.size), Image.LANCZOS)))
                source = gif + "（现场生成）"
        if not frames:
            messagebox.showerror(
                "FairyX",
                "找不到悬浮球素材。\n\n请把 assets 目录放到 fairy.py 同级，\n"
                "或把 Fairy 的图标/动图放回：\n%s" % os.path.join(
                    os.path.expanduser("~"), "Desktop", "fairy"))
            return
        self._ball = _Ball(frames, fps, source)
        self._layered = LayeredWindow(self, self.size, self.size)
        self._ring_mask = self._make_ring_mask()
        log("悬浮球素材已加载：%d 帧 @ %.1ffps（%s）｜分层窗口 %s"
            % (len(frames), fps, source, "OK" if self._layered.layered_ok else "失败"))

    def _make_ring_mask(self):
        s = self.size
        mask = Image.new("L", (s, s), 0)
        d = ImageDraw.Draw(mask)
        pad = max(1, int(s * 0.045))
        d.ellipse([pad, pad, s - 1 - pad, s - 1 - pad], outline=255,
                  width=max(2, int(s * 0.055)))
        mask = mask.filter(ImageFilter.GaussianBlur(max(1.0, s * 0.03)))
        return np.asarray(mask, dtype=np.uint16)

    def _halo_rgba(self, color_hex):
        if self._ring_mask is None:
            return None
        r, g, b = (int(color_hex[1:3], 16), int(color_hex[3:5], 16), int(color_hex[5:7], 16))
        key = (r // 8, g // 8, b // 8)
        hit = self._halo_cache.get(key)
        if hit is not None:
            return hit
        al = (self._ring_mask * 0.85).astype(np.uint16)
        rgb = np.array([r, g, b], dtype=np.uint16)[None, None, :]
        pre = (rgb * al[:, :, None] // 255).astype(np.uint8)
        halo = np.ascontiguousarray(np.dstack([pre[:, :, 2], pre[:, :, 1], pre[:, :, 0],
                                               al.astype(np.uint8)]))
        if len(self._halo_cache) > 64:
            self._halo_cache.clear()
        self._halo_cache[key] = halo
        return halo

    def _anim_tick(self):
        if self._ball and self._ball.frames:
            idx = int((time.time() - self._anim_t0) * self._ball.fps * self._speed)
            idx %= len(self._ball.frames)
            if idx != self._frame_idx or self._halo_dirty:
                self._frame_idx = idx
                self._draw_ball()
        self._anim_after = self.after(20, self._anim_tick)

    @staticmethod
    def _lerp_color(c1, c2, t):
        r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
        r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
        return "#%02x%02x%02x" % (
            int(r1 + (r2 - r1) * t),
            int(g1 + (g2 - g1) * t),
            int(b1 + (b2 - b1) * t))

    def _draw_ball(self):
        if not self._ball or not self._ball.frames or self._layered is None:
            return
        self._cur_color = self._lerp_color(self._cur_color, STATE_COLORS[self.state], 0.15)
        frame = self._ball.frames[self._frame_idx % len(self._ball.frames)]
        halo = self._halo_rgba(self._cur_color)
        if halo is not None:
            fa = frame[:, :, 3:4].astype(np.uint16)
            out = frame.astype(np.uint16) + (halo.astype(np.uint16) * (255 - fa) // 255)
            buf = np.ascontiguousarray(np.clip(out, 0, 255).astype(np.uint8))
        else:
            buf = frame
        self._halo_dirty = False
        self._layered.blit(buf)

    def set_state(self, state):
        self.after(0, lambda: self._apply_state(state))

    def _apply_state(self, state):
        self.state = state
        self._speed = STATE_SPEED.get(state, 1.0)
        self._halo_dirty = True
        self._draw_ball()


    def _on_press(self, e):
        self._press_time = time.time()
        self._press_pos = (e.x_root, e.y_root)
        self._moved = False
        self._drag = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())

    def _on_drag(self, e):
        if not self._drag:
            return
        if abs(e.x_root - self._press_pos[0]) > 4 or \
           abs(e.y_root - self._press_pos[1]) > 4:
            self._moved = True
        if self._moved:
            self.geometry("+%d+%d" % (e.x_root - self._drag[0],
                                      e.y_root - self._drag[1]))
            if self._dl_bar is not None and self._dl_bar.winfo_viewable():
                self._dl_bar.geometry("+%d+%d" % (e.x_root - self._drag[0] + self.size + 8,
                                                  e.y_root - self._drag[1] + self.size // 2 - 15))


    def _dl_dir_gb(self):
        try:
            s = 0
            for r, _, fs in os.walk(DL_DIR):
                for f in fs:
                    try:
                        s += os.path.getsize(os.path.join(r, f))
                    except OSError:
                        pass
            return s / (1024 ** 3)
        except Exception:
            return 0.0

    def _pump_dl_progress(self):
        try:
            if not os.path.isdir(DL_DIR):
                if self._dl_bar is not None:
                    self._dl_bar.withdraw()
                self.after(5000, self._pump_dl_progress)
                return
            cur = self._dl_dir_gb()
            pct = min(100.0, cur / DL_TARGET_GB * 100.0) if DL_TARGET_GB else 0.0
            if pct < 0.5:
                if self._dl_bar is not None:
                    self._dl_bar.withdraw()
                self.after(5000, self._pump_dl_progress)
                return
            if self._dl_bar is None:
                self._make_dl_bar()
            self._dl_bar_draw(pct, cur)
            self._dl_bar.deiconify()
            self._dl_bar.lift()
            if pct >= 100.0:
                self.after(4000, self._hide_dl_bar)
            self.after(2000, self._pump_dl_progress)
        except Exception:
            self.after(5000, self._pump_dl_progress)

    def _make_dl_bar(self):
        w = tk.Toplevel(self)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.attributes("-alpha", 0.95)
        w.configure(bg="#000000")
        c = tk.Canvas(w, width=230, height=30, bg="#000000", highlightthickness=0)
        c.pack()
        w.geometry("230x30+%d+%d" % (self.winfo_x() + self.size + 8,
                                     self.winfo_y() + self.size // 2 - 15))
        self._dl_bar = w
        self._dl_bar_canvas = c

    def _dl_bar_draw(self, pct, cur):
        try:
            c = self._dl_bar_canvas
            c.delete("all")
            w, h = 230, 30
            c.create_rectangle(2, 2, w - 2, h - 2, fill="#222222", outline="#444444")
            fw = int((w - 8) * min(1.0, pct / 100.0))
            if fw > 0:
                c.create_rectangle(4, 4, 4 + fw, h - 4, fill="#00b3ff", outline="")
            txt = "模型下载 %d%%（%.1f / %.0fGB）" % (pct, cur, DL_TARGET_GB)
            c.create_text(w // 2, h // 2, text=txt, fill="#ffffff",
                          font=("Microsoft YaHei", 9))
        except Exception:
            pass

    def _hide_dl_bar(self):
        if self._dl_bar is not None:
            self._dl_bar.withdraw()

    def _save_pos(self):
        try:
            px, py = _clamp_pos(self.winfo_x(), self.winfo_y(), self.size)
            CONFIG.setdefault("floating_ball", {})
            CONFIG["floating_ball"]["position_x"] = px
            CONFIG["floating_ball"]["position_y"] = py
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(CONFIG, f, ensure_ascii=False, indent=2)
            log("悬浮球位置已保存：(%d, %d)" % (px, py))
        except Exception as e:
            log("保存悬浮球位置失败: %s" % e)

    def _on_release(self, e):
        now = time.time()
        quick = not self._moved and (now - self._press_time) < 0.4
        if self._moved:
            px, py = _clamp_pos(self.winfo_x(), self.winfo_y(), self.size)
            if (px, py) != (self.winfo_x(), self.winfo_y()):
                self.geometry("+%d+%d" % (px, py))
            self._save_pos()
        if quick:
            if now - self._last_release < 0.3:

                self._last_release = now
                return
            self._last_release = now
            if self._pending_click:
                self.after_cancel(self._pending_click)
            self._pending_click = self.after(260, self._toggle_listening)
        self._drag = None

    def _on_double(self, e):
        if self._pending_click:
            self.after_cancel(self._pending_click)
            self._pending_click = None
        self._open_panel()

    def _on_right(self, e):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="语音对话 %s" % ("开" if not self.voice.listening else "关"),
                         command=lambda: self._toggle_listening())
        menu.add_command(label="打开文字面板", command=self._open_panel)

        vm = tk.Menu(menu, tearoff=0)
        vcfg = CONFIG.get("voice") or {}
        cur = vcfg.get("active_profile") or ""
        for k, pr in (vcfg.get("profiles") or {}).items():
            vm.add_command(label="%s %s" % ("●" if k == cur else "○", pr.get("label", k)),
                           command=lambda k=k: self._switch_voice(k))
        if not vm.index("end"):
            vm.add_command(label="（config.json 里还没配置音色）", state="disabled")
        vm.add_separator()
        vm.add_command(label="语音体检", command=self._show_voice_status)
        menu.add_cascade(label="音色（当前:%s）" % (cur or "未设置"), menu=vm)
        menu.add_command(label="切换模型（当前:%s）" %
                         CONFIG["brain"]["provider"],
                         command=self._switch_provider)
        menu.add_separator()
        menu.add_command(label="静音AI声音" if not self.voice.muted else "恢复AI声音",
                         command=self._toggle_mute)
        menu.add_command(label="测试语音", command=self._test_voice)
        menu.add_command(label="清空对话记忆", command=self.brain.clear_history)
        menu.add_separator()
        menu.add_command(label="退出", command=self.shutdown)
        menu.tk_popup(e.x_root, e.y_root)
        menu.grab_release()

    def _switch_voice(self, key):
        log(_voice_switch({"profile": key}))
        prof = (CONFIG.get("voice", {}).get("profiles") or {}).get(key) or {}
        self.voice.speak("已切换到%s" % prof.get("label", key))

    def _show_voice_status(self):
        messagebox.showinfo("FairyX · 语音体检", _voice_status())


    def _toggle_listening(self):
        if self.voice.listening:
            self.voice.stop_listening()
            self.set_state(STATE_IDLE)
            return
        if not self.voice.start_listening():
            self.set_state(STATE_IDLE)
            messagebox.showinfo("FairyX · 语音未就绪",
                                self.voice.last_error or
                                "语音引擎未就绪，改用文字面板吧（双击悬浮球）")
            if not self.voice.listening:
                self._open_panel()

    def on_voice_result(self, text):
        def _process():
            self.set_state(STATE_THINKING)

            t = text.lower()
            if any(k in t for k in ("反推", "提示词", "看图", "看下这个", "看看这个",
                                    "看看图", "看看屏幕", "这张图", "图片里", "屏幕上",
                                    "屏幕翻译", "翻译屏幕")):
                if "反推" in t or "提示词" in t:
                    mode = "反推"
                elif "翻译" in t:
                    mode = "翻译"
                else:
                    mode = "描述"
                log("语音快捷：vision %s <- %s" % (mode, text))
                reply = _vision(mode, "")
                self.set_state(STATE_SPEAKING)
                self.voice.speak(reply[:600])
                if self._panel:
                    self.after(0, lambda: self._panel_add("FairyX", reply))
                return

            if any(k in t for k in ("缺什么软件", "软件清单", "看看我缺", "有哪些软件",
                                    "装了什么", "都装上", "帮我装", "自动安装", "安装软件",
                                    "预装", "装软件")):
                ov, dl = _catalog_check()
                if any(w in t for w in ("都装上", "帮我装", "自动安装", "安装软件")):
                    reply = _catalog_install()
                elif dl:
                    names = "、".join(n for _, n in dl[:6])
                    reply = ("扫描完成。常用类（视频/下载/文档/音乐/上网）缺 %d 个代表软件，"
                             "可自动安装的有 %s 等。说 都装上 我就下载安装；其他类别以后需要再问我。"
                             % (len(dl), names))
                else:
                    reply = "常用类的代表软件都已安装，没有需要预装的。"
                self.set_state(STATE_SPEAKING)
                self.voice.speak(reply[:600])
                if self._panel:
                    self.after(0, lambda: self._panel_add("FairyX", reply))
                return

            if any(k in t for k in ("授权", "同意扫描", "允许扫描", "同意安装", "允许安装",
                                    "开启扫描", "开启安装", "允许")):
                log("语音快捷：consent <- %s" % text)
                if any(w in t for w in ("不授权", "拒绝", "不同意", "不要")):
                    reply = "好的，保持未授权状态。需要时再说 授权 即可开启。"
                else:

                    if "安装" in t and "扫描" not in t:
                        reply = grant_consent(["install"])
                    elif "扫描" in t and "安装" not in t:
                        reply = grant_consent(["scan"])
                    else:
                        reply = grant_consent()
                self.set_state(STATE_SPEAKING)
                self.voice.speak(reply[:300])
                if self._panel:
                    self.after(0, lambda: self._panel_add("FairyX", reply))
                return
            reply = self.brain.think(text)
            log("FairyX: %s" % reply)
            self.set_state(STATE_SPEAKING)
            self.voice.speak(reply)
            if self._panel:
                self.after(0, lambda: self._panel_add("FairyX", reply))
        threading.Thread(target=_process, daemon=True).start()


    def _open_panel(self):
        if self._panel is not None:
            try:
                self._panel.deiconify()
                self._panel.lift()
                return
            except Exception:
                self._panel = None
        panel = tk.Toplevel(self)
        panel.title("FairyX · 文字对话")
        panel.geometry("480x420")
        panel.attributes("-topmost", True)
        self._panel = panel

        self._log_area = tk.Text(panel, bg="#0f172a", fg="#e2e8f0",
                                 font=("Microsoft YaHei", 10), wrap="word")
        self._log_area.pack(fill="both", expand=True, padx=6, pady=6)
        self._log_area.insert("end", "FairyX：你好，我是你的AI助手。\n")

        bottom = tk.Frame(panel)
        bottom.pack(fill="x", padx=6, pady=(0, 6))
        self._entry = tk.Entry(bottom, font=("Microsoft YaHei", 11))
        self._entry.pack(side="left", fill="x", expand=True)
        self._entry.bind("<Return>", lambda e: self._panel_send())
        btn = ttk.Button(bottom, text="发送", command=self._panel_send)
        btn.pack(side="left", padx=4)
        panel.protocol("WM_DELETE_WINDOW", lambda: panel.withdraw())
        self._entry.focus_set()

    def _panel_send(self):
        text = self._entry.get().strip()
        if not text:
            return
        self._entry.delete(0, "end")
        self._panel_add(text, None, user=True)
        self.voice.stop_listening()
        self.set_state(STATE_THINKING)

        def _process():
            reply = self.brain.think(text)
            log("FairyX: %s" % reply)
            self.set_state(STATE_SPEAKING)
            self.voice.speak(reply)
            self.after(0, lambda: self._panel_add("FairyX", reply))
        threading.Thread(target=_process, daemon=True).start()

    def _panel_add(self, who, what, user=False):
        if what is None:
            what = ""
        tag = "user" if user else "ai"
        self._log_area.insert("end", "%s：%s\n" % (who, what), tag)
        self._log_area.tag_config("user", foreground="#38bdf8")
        self._log_area.tag_config("ai", foreground="#e2e8f0")
        self._log_area.see("end")


    def _switch_provider(self):
        provs = [p for p in ((CONFIG.get("brain") or {}).get("providers") or [])
                 if p.get("enabled", True)]
        if not provs:
            log("没有配置 brain.providers，无法切换")
            self.voice.speak("还没有配置多个大脑")
            return
        names = [p.get("name") or p.get("base_url") for p in provs]
        cur = (CONFIG["brain"].get("active_provider") or names[0])
        nxt = names[(names.index(cur) + 1) % len(names)] if cur in names else names[0]
        CONFIG["brain"]["active_provider"] = nxt
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(CONFIG, f, ensure_ascii=False, indent=2)
            saved = "已写入 config.json"
        except Exception as e:
            saved = "写入配置失败: %s" % e
        log("已切换大脑: %s（%s）" % (nxt, saved))
        self.voice.speak("已切到%s" % nxt)

    def _toggle_mute(self):
        self.voice.muted = not self.voice.muted
        log("语音播报 %s" % ("静音" if self.voice.muted else "恢复"))

    def _test_voice(self):
        self.voice.speak("你好，我是FairyX，你的桌面AI助手。")


    def request_confirm(self, message):
        self.confirmer_req.put(message)
        return self.confirmer_res.get(timeout=120)

    def _pump_confirm(self):
        try:
            msg = self.confirmer_req.get_nowait()
        except queue.Empty:
            pass
        else:
            ok = messagebox.askokcancel("FairyX 安全确认", msg)
            self.confirmer_res.put(ok)
        self.after(200, self._pump_confirm)


    def shutdown(self):
        log("正在退出…")
        self.voice.shutdown()
        self.destroy()
        os._exit(0)

def grant_consent(kinds=None):
    consent = CONFIG.setdefault("consent", {})
    kinds = kinds or ["scan", "install", "system_change"]
    for k in kinds:
        consent[k] = True
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(CONFIG, f, ensure_ascii=False, indent=2)
        names = {"scan": "扫描本机", "install": "自动安装软件", "system_change": "修改系统设置"}
        return "已授权：%s" % "、".join(names.get(k, k) for k in kinds)
    except Exception as e:
        return "授权写入失败: %s" % e

def ensure_consent():
    consent = CONFIG.setdefault("consent", {})
    if consent.get("scan"):
        return
    from tkinter import messagebox as _mb
    ok = _mb.askyesno(
        "FairyX 使用人授权",
        "FairyX 需要你的授权才能提供以下功能（默认全部关闭，可随时改 config.json 的 consent 段）：\n\n"
        "1) 扫描本机（软件/硬件/文件清单、软件分类）\n"
        "2) 自动下载安装软件（仅免费官方直链）\n"
        "3) 修改系统设置\n\n"
        "是否允许扫描本机？")
    if ok:
        consent["scan"] = True
        consent["install"] = True
        consent["system_change"] = True
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(CONFIG, f, ensure_ascii=False, indent=2)
            log("使用人已授权：扫描/安装/系统更改")
        except Exception as e:
            log("授权写入失败: %s" % e)
    else:
        log("使用人未授权：保持全部关闭（可手动编辑 config.json 的 consent 段开启）")

def main():
    log("FairyX 启动中…（配置文件: %s）" % CONFIG_PATH)
    ensure_consent()
    app = FloatingBall()
    if "--selftest" in sys.argv:
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "selftest_ball.png")

        def _grab():
            from PIL import ImageGrab
            x, y, s = app.winfo_x(), app.winfo_y(), app.size
            pad = 34
            ImageGrab.grab(bbox=(x - pad, y - pad, x + s + pad, y + s + pad)).save(out)
            log("自检截图: %s" % out)
            log("自检状态: 帧数=%d 分层窗口=%s"
                % (len(app._ball.frames) if app._ball else 0,
                   getattr(app._layered, "layered_ok", None)))
            app.after(250, app.shutdown)

        app.after(2200, _grab)
    app.mainloop()

if __name__ == "__main__":
    main()
