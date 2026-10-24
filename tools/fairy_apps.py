# -*- coding: utf-8 -*-
"""
fairy_apps.py —— FairyX 全类别工具调度中心
==========================================
用户说"给我写个代码 / 下载XXX / 看视频"时：
  1) 按类别匹配工具（代码/文档/播放器/下载器...）
  2) 已装 → 直接调用；没装 → 官方直链下载 → 静默安装
  3) 启动工具完成任务

安全原则：只从官方域名下载；只自动安装"免费 + 支持静默"的软件；
大型软件（如 DevEco 1GB+）只检测+提示下载，不自动装。

用法：
  py -3.12 tools\fairy_apps.py --list           列出全部可调度工具
  py -3.12 tools\fairy_apps.py --find 写代码    按类别找工具（返回已装/未装）
  py -3.12 tools\fairy_apps.py --install vscode 自动下载安装 VS Code
  py -3.12 tools\fairy_apps.py --open vscode    打开工具
  py -3.12 tools\fairy_apps.py --write "D:/x/demo.py" "print('hi')" 写文件并用IDE打开
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

import fairy_knowledge as FK  # ★ 软件知识库（核心 800+ 条：名 -> exe/类别/别名）

# ★ 2026-10-10【黑窗】0x08000000 = CREATE_NO_WINDOW（不是 0x00000200）。
#   父进程没控制台时（pythonw / 计划任务），不加这个起安装器就弹终端。
CREATE_NO_WINDOW = 0x08000000

# ---------------- 使用人授权 ----------------
# config.json 的 consent 段：scan/install/system_change 三项，默认 false
# 未授权 → 扫描/发现/安装/更改一律拒绝，返回引导提示。

def _cfg_path():
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.json"))


def _cfg():
    try:
        with open(_cfg_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# FairyX 根目录：由脚本位置推导（tools/ 的上一级）
D = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_cpaths = _cfg().get("paths") or {}

# ★ 2026-10-12【中文语音软件库】扫描后保存（名字/别名/功能类别），语音调用直接查
VOICE_LIB = os.path.join(D, "data", "voice_apps.json")
CAT_KEYWORDS = [
    ("浏览器", ["chrome", "edge", "firefox", "浏览器", "夸克", "极速", "opera", "safari"]),
    ("通讯", ["微信", "wechat", "weixin", "qq", "钉钉", "dingtalk", "telegram",
              "discord", "飞书", "whatsapp", "slack", "skype"]),
    ("办公", ["office", "word", "excel", "ppt", "powerpoint", "wps", "文档",
              "onenote", "outlook", "notion", "obsidian", "koodo", "阅读"]),
    ("开发", ["code", "studio", "dev", "git", "python", "idea", "android", "flutter",
              "vscode", "sublime", "node", "java", "docker", "postman", "harness",
              "strata", "llama"]),
    ("影音", ["player", "视频", "音乐", "网易云", "qq音乐", "bilibili", "爱奇艺",
              "potplayer", "vlc", "kmplayer", "网易云音乐", "酷狗", "酷我", "spotify"]),
    ("下载", ["下载", "idm", "迅雷", "thunder", "aria2", "qbit", "bitcomet"]),
    ("系统工具", ["everything", "任务", "优化", "clean", "管家", "360", "geek",
                  "uninstall", "hi", "bit", "driver", "驱动", "鲁大师", "revo"]),
    ("图像", ["photoshop", "ps", "画图", "图片", "截图", "snipaste", "paint",
              "lightroom", "美图", "gimp"]),
    ("AI", ["ollama", "deepseek", "豆包", "doubao", "gpt", "ai", "dash", "comfyui",
            "sherpa", "whisper", "funasr", "index", "tts"]),
    ("游戏", ["steam", "wegame", "游戏", "wargaming", "坦克", "lol", "epic", "origin",
              "加速", "加速器"]),
    ("安全", ["杀毒", "安全", "火绒", "360安全", "defender", "malware", "avast"]),
    ("输入法", ["输入法", "搜狗", "百度输入", "讯飞", "sogou"]),
]


def consent_ok(kind):
    """检查某项授权是否已获得"""
    try:
        with open(_cfg_path(), encoding="utf-8") as f:
            return bool(json.load(f).get("consent", {}).get(kind, False))
    except Exception:
        return False


def require_consent(kind, action_desc):
    """未授权时返回引导文案（None=已授权，可继续）"""
    if consent_ok(kind):
        return None
    return ("需要使用人授权后才能%s。\n"
            "请在 FairyX 启动时同意授权弹窗，或手动编辑 config.json 的 "
            "consent 段把 %s 改为 true。" % (action_desc, kind))

# ---------------- 工具目录（类别 -> 工具列表） ----------------
# key: 工具唯一名；cat: 类别；strong: 强特征词（命中即首选，如语言/平台名）；
# win: 通用词（兜底匹配）；exe: 检测/启动路径；url: 官方下载直链；silent: 静默参数；home: 官网
APP_CATALOG = {
    "vscode": dict(cat="代码", strong=["visual studio code", "vs code", "vscode", "python", "javascript", "js", "html", "web", "node", "typescript"],
                   win=["写代码", "编程", "ide", "编辑器", "代码编辑器"],
                   exe=[os.path.expandvars(r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe"),
                        r"C:\Program Files\Microsoft VS Code\Code.exe",
                        r"C:\Program Files (x86)\Microsoft VS Code\Code.exe"],
                   url="https://update.code.visualstudio.com/latest/win32-x64-user/stable",
                   silent=["/VERYSILENT", "/NORESTART", "/MERGETASKS=!runcode"],
                   home="https://code.visualstudio.com"),
    "deveco": dict(cat="代码", strong=["鸿蒙", "harmony", "deveco", "华为", "ets"],
                   win=["写代码"],
                   exe=[r"E:\DevEco Studio\bin\devecostudio64.exe",
                        r"E:\DevEco Studio\bin\deveco-studio64.exe",
                        r"C:\Program Files\Huawei\DevEco Studio\bin\devecostudio64.exe"],
                   url=None, silent=[], home="https://developer.huawei.com/consumer/cn/deveco-studio/"),
    "androidstudio": dict(cat="代码", strong=["android studio", "安卓studio", "kotlin", "apk", "安卓app"],
                          win=["android", "安卓", "java"],
                          exe=[r"C:\Program Files\Android\Android Studio\bin\studio64.exe",
                               r"C:\Program Files\Android\Android Studio\bin\studio.exe"],
                          url=None, silent=[], home="https://developer.android.com/studio"),
    "vs": dict(cat="代码", strong=["visual studio", "vc++", "vc", "c++", "cpp", "msvc", "c语言", "c语言", "c#", "c sharp", "mfc", "qt"],
               win=["c++", "vc", "visual studio"],
               exe=[r"C:\Program Files\Microsoft Visual Studio\2022\Community\Common7\IDE\devenv.exe",
                    r"C:\Program Files\Microsoft Visual Studio\2022\Professional\Common7\IDE\devenv.exe",
                    r"C:\Program Files\Microsoft Visual Studio\2022\Enterprise\Common7\IDE\devenv.exe"],
               url="https://aka.ms/vs/17/release/vs_community.exe",
               silent=[], home="https://visualstudio.microsoft.com/"),
    "codeblocks": dict(cat="代码", strong=["code::blocks", "codeblocks"],
                       win=["cb"],
                       exe=[r"C:\Program Files\CodeBlocks\codeblocks.exe"],
                       url=None, silent=[], home="https://www.codeblocks.org/"),
    "notepad3": dict(cat="代码", strong=["notepad3"],
                     win=["notepad", "记事本"],
                     exe=[r"C:\Program Files\Notepad3\notepad3.exe"],
                     url=None, silent=[], home="https://www.rizonesoft.com/downloads/notepad3/"),
    "wps": dict(cat="文档", strong=["wps"],
                win=["文档", "word", "表格", "ppt", "演示", "excel"],
                exe=[r"C:\Program Files (x86)\Kingsoft\WPS Office\office6\wps.exe",
                     r"C:\Program Files\Kingsoft\WPS Office\office6\wps.exe"],
                url=None, silent=[], home="https://www.wps.cn/"),
    "aria2": dict(cat="下载", strong=["aria2", "下载器"],
                  win=["下载", "磁力", "种子", "bt"],
                  exe=[os.path.join(D, "tools", "aria2c.exe"), r"C:\Program Files\aria2\aria2c.exe"],
                  url="https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-win-64bit-build1.zip",
                  silent=[], home="https://aria2.github.io/"),
    "everything": dict(cat="搜索", strong=["everything"],
                       win=["搜索文件", "找文件", "全盘"],
                       exe=[os.path.join(D, "everything", "everything.exe"),
                            r"C:\Program Files\Everything\everything.exe"],
                       url=None, silent=[], home="https://www.voidtools.com"),
    "ollama": dict(cat="AI", strong=["ollama"],
                   win=["模型", "大模型", "ai"],
                   exe=[os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")],
                   url=None, silent=[], home="https://ollama.com"),
}

# 类别 -> 默认首选工具（用户只说类别时用）
CAT_DEFAULT = {"代码": "vscode", "文档": "wps", "下载": "aria2",
               "搜索": "everything", "AI": "ollama"}

# ---------------- 全软件大分类体系（扫描自动归类 + 缺类代表预装） ----------------
# 分类名 -> {工具名: 可下载安装的官方直链 或 None(检测/手动)}
# 已装检测复用 APP_CATALOG 的 exe；"代表"即该类最值得预装的免费软件

# 常用优先类（视频/下载/文档/音乐/上网等高频，优先询问预装）
PRIORITY_CATS = ["媒体播放", "下载工具", "办公文档", "音乐", "浏览器",
                 "即时通讯", "阅读器", "压缩解压"]

CATEGORIES = {
    "开发工具": {"vscode": None, "deveco": None, "androidstudio": None, "vs": None, "codeblocks": None},
    "办公文档": {"wps": None},
    "浏览器": {"chrome": None},
    "即时通讯": {"wechat": None},
    "媒体播放": {"potplayer": None, "vlc": None},
    "视频剪辑": {"obs": None, "davinci": None},
    "音频处理": {"audacity": None},
    "图像设计": {"gimp": None, "krita": None},
    "3D建模": {"blender": None},
    "下载工具": {"aria2": None, "qbittorrent": None},
    "压缩解压": {"7zip": None},
    "系统工具": {"everything": None, "powertoys": None},
    "安全防护": {"huorong": None},
    "网络工具": {"wireshark": None, "putty": None},
    "远程控制": {"todesk": None, "rustdesk": None},
    "数据库": {"dbeaver": None, "sqlite": None},
    "虚拟机": {"virtualbox": None},
    "笔记效率": {"obsidian": None, "typora": None},
    "输入法": {"sougou": None},
    "翻译词典": {"youdao": None},
    "阅读器": {"sumatrapdf": None, "calibre": None},
    "音乐": {"netease": None, "qqmusic": None},
    "游戏平台": {"steam": None},
    "直播录屏": {"obs": None},
    "AI工具": {"ollama": None, "comfyui": None},
    "云盘同步": {"baidu": None},
}

# 每类代表软件的可下载源（官方直链，免费）—— 只有小而静默的填 url，大软件留 None 提示官网
REP_SOURCES = {
    "chrome": dict(url="https://dl.google.com/chrome/install/standalonesetup64.exe", silent=["/silent", "/install"],
                   home="https://www.google.com/chrome/", exe=[r"C:\Program Files\Google\Chrome\Application\chrome.exe"]),
    "potplayer": dict(url=None, silent=[], home="https://potplayer.daum.net/",
                      exe=[r"C:\Program Files\DAUM\PotPlayer\PotPlayerMini64.exe", r"C:\Program Files (x86)\DAUM\PotPlayer\PotPlayerMini64.exe"]),
    "vlc": dict(url="https://get.videolan.org/vlc/last/win64/vlc-3.0.21-win64.exe", silent=["/L=1033", "/S"],
                home="https://www.videolan.org/", exe=[r"C:\Program Files\VideoLAN\VLC\vlc.exe"]),
    "7zip": dict(url="https://www.7-zip.org/a/7z2408-x64.exe", silent=["/S"],
                 home="https://www.7-zip.org/", exe=[r"C:\Program Files\7-Zip\7zFM.exe"]),
    "powertoys": dict(url="https://github.com/microsoft/PowerToys/releases/latest/download/PowerToysUserSetup-x64.exe",
                      silent=["/quiet"], home="https://github.com/microsoft/PowerToys",
                      exe=[r"C:\Program Files\PowerToys\PowerToys.exe"]),
    "obsidian": dict(url="https://github.com/obsidianmd/obsidian-releases/releases/latest/download/Obsidian-1.5.12.exe",
                     silent=["/S"], home="https://obsidian.md/",
                     exe=[os.path.expandvars(r"%LOCALAPPDATA%\Obsidian\Obsidian.exe")]),
    "wireshark": dict(url=None, silent=[], home="https://www.wireshark.org/",
                      exe=[r"C:\Program Files\Wireshark\Wireshark.exe"]),
    "dbeaver": dict(url=None, silent=[], home="https://dbeaver.io/",
                    exe=[r"C:\Program Files\DBeaver\dbeaver.exe"]),
    "rustdesk": dict(url="https://github.com/rustdesk/rustdesk/releases/latest/download/rustdesk-1.2.3-x86_64.exe",
                     silent=["--silent-install"], home="https://rustdesk.com/",
                     exe=[r"C:\Program Files\RustDesk\RustDesk.exe"]),
    "gimp": dict(url=None, silent=[], home="https://www.gimp.org/",
                 exe=[r"C:\Program Files\GIMP 2\bin\gimp-2.10.exe"]),
    "blender": dict(url=None, silent=[], home="https://www.blender.org/",
                    exe=[r"C:\Program Files\Blender Foundation\Blender 3.6\blender.exe"]),
    "steam": dict(url=None, silent=[], home="https://store.steampowered.com/",
                  exe=[r"C:\Program Files (x86)\Steam\steam.exe"]),
    "obs": dict(url=None, silent=[], home="https://obsproject.com/",
                exe=[r"C:\Program Files\obs-studio\bin\64bit\obs64.exe"]),
    "wechat": dict(url=None, silent=[], home="https://weixin.qq.com/",
                   exe=[r"C:\Program Files\Tencent\WeChat\WeChat.exe", r"C:\Program Files (x86)\Tencent\WeChat\WeChat.exe"]),
    "audacity": dict(url=None, silent=[], home="https://www.audacityteam.org/",
                     exe=[r"C:\Program Files\Audacity\Audacity.exe"]),
    "krita": dict(url=None, silent=[], home="https://krita.org/",
                  exe=[r"C:\Program Files\Krita (x64)\bin\krita.exe"]),
    "virtualbox": dict(url=None, silent=[], home="https://www.virtualbox.org/",
                       exe=[r"C:\Program Files\Oracle\VirtualBox\VirtualBox.exe"]),
    "sumatrapdf": dict(url="https://github.com/sumatrapdfreader/sumatrapdf/releases/latest/download/SumatraPDF-3.5.2-64-install.exe",
                       silent=["/S"], home="https://www.sumatrapdfreader.org/",
                       exe=[r"C:\Program Files\SumatraPDF\SumatraPDF.exe"]),
    "typora": dict(url=None, silent=[], home="https://typora.io/",
                   exe=[r"C:\Program Files\Typora\Typora.exe"]),
    "youdao": dict(url=None, silent=[], home="https://cidian.youdao.com/",
                   exe=[r"C:\Program Files (x86)\Youdao\Dict\YoudaoDict.exe"]),
    "sougou": dict(url=None, silent=[], home="https://shurufa.sogou.com/",
                   exe=[r"C:\Program Files (x86)\SogouInput\9.0.0.5136\ImeUtil.exe"]),
    "netease": dict(url=None, silent=[], home="https://music.163.com/",
                    exe=[r"C:\Program Files (x86)\Netease\CloudMusic\cloudmusic.exe"]),
    "qqmusic": dict(url=None, silent=[], home="https://y.qq.com/",
                    exe=[r"C:\Program Files (x86)\Tencent\QQMusic\QQMusic.exe"]),
    "qbittorrent": dict(url=None, silent=[], home="https://www.qbittorrent.org/",
                        exe=[r"C:\Program Files\qBittorrent\qbittorrent.exe"]),
    "huorong": dict(url=None, silent=[], home="https://www.huorong.cn/",
                    exe=[r"C:\Program Files\Huorong\Sysdiag\HipsMain.exe"]),
    "davinci": dict(url=None, silent=[], home="https://www.blackmagicdesign.com/products/davinciresolve",
                    exe=[r"C:\Program Files\Blackmagic Design\DaVinci Resolve\Resolve.exe"]),
    "baidu": dict(url=None, silent=[], home="https://pan.baidu.com/",
                  exe=[r"C:\Program Files (x86)\Baidu\BaiduNetdisk\BaiduNetdisk.exe"]),
}


def _rep_info(name):
    """返回代表软件的信息（合并 APP_CATALOG 与 REP_SOURCES）"""
    if name in APP_CATALOG:
        return APP_CATALOG[name]
    return REP_SOURCES.get(name) or dict(url=None, silent=[], home="", exe=[])


def scan_catalog():
    """扫描全部大分类：已装软件 + 缺类代表。返回 (分类总览文本, 缺类清单)
    需要 scan 授权。"""
    need = require_consent("scan", "扫描本机软件分类")
    if need:
        return need, []
    lines = []
    missing = []
    for cat, tools in CATEGORIES.items():
        star = "★" if cat in PRIORITY_CATS else " "
        have, lack = [], []
        for name in tools:
            info = _rep_info(name)
            exe = next((e for e in info.get("exe", []) if os.path.exists(e)), None)
            if exe:
                have.append(name)
            else:
                lack.append(name)
        st = ("已装: " + "、".join(have)) if have else "无"
        lack_txt = ""
        if lack:
            dl = [n for n in lack if _rep_info(n).get("url")]
            lack_txt = " | 缺: %s" % "、".join(lack)
            if dl:
                lack_txt += "（可自动下载: %s）" % "、".join(dl)
        lines.append("【%s%s】%s%s" % (star, cat, st, lack_txt))
        missing.append((cat, lack))
    # ★ 2026-10-12：扫描后自动建立/更新中文语音软件库（语音调用直接查）
    try:
        build_voice_lib()
    except Exception:
        pass
    return "\n".join(lines), missing


def scan_installed_all():
    """从注册表读取本机已装软件（补充分类总览）——需要 scan 授权"""
    need = require_consent("scan", "读取已装软件清单")
    if need:
        return [need]
    import winreg
    names = set()
    roots = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")]
    for hkey, path in roots:
        try:
            k = winreg.OpenKey(hkey, path)
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(k, i)
                    i += 1
                    try:
                        sk = winreg.OpenKey(k, sub)
                        try:
                            nm, _ = winreg.QueryValueEx(sk, "DisplayName")
                            if nm and len(nm) < 60:
                                names.add(nm.strip())
                        finally:
                            sk.Close()
                    except OSError:
                        pass
                except OSError:
                    break
            k.Close()
        except OSError:
            pass
    return sorted(names)


def _detect_exe(app):
    """返回工具的可执行路径；未装返回 None"""
    for exe in APP_CATALOG[app]["exe"]:
        if os.path.exists(exe):
            return exe
    return None


def list_apps():
    lines = ["可调度工具（类别 | 名称 | 状态 | 官网）"]
    for name, info in APP_CATALOG.items():
        exe = _detect_exe(name)
        st = ("已装" if exe else "未装" + ("（可自动下载）" if info["url"] else ""))
        lines.append("%s | %s | %s | %s" % (info["cat"], name, st, info["home"]))
    return "\n".join(lines)


def find_by_text(text):
    """把用户的话匹配到工具名（强特征词优先，通用词兜底）"""
    t = text.lower()
    # 一阶段：强特征词（语言/平台/工具专名，如 c++、android、鸿蒙）
    for name, info in APP_CATALOG.items():
        if any(w in t for w in info.get("strong", [])):
            return name
    # 二阶段：工具名/通用词
    for name, info in APP_CATALOG.items():
        if name in t or any(w in t for w in info["win"]):
            return name
    # 类别兜底
    for cat, app in CAT_DEFAULT.items():
        if cat in text:
            return app
    return None


def install(app):
    """官方直链下载 + 静默安装（仅支持 url 的工具）。需要 install 授权。"""
    need = require_consent("install", "自动下载安装软件")
    if need:
        return need
    info = APP_CATALOG.get(app)
    if not info:
        return "未知工具: %s" % app
    if _detect_exe(app):
        return "%s 已安装，无需下载" % app
    if not info["url"]:
        return ("%s 暂不支持自动下载（大软件/需手动），官网：%s" % (app, info["home"]))
    dl = os.path.join(os.environ.get("TEMP", os.path.join(D, "downloads")),
                      "%s_installer%s" % (app, ".exe" if info["url"].endswith(".exe") else ".zip"))
    print("  下载中: %s -> %s（约几十MB，请稍候）..." % (info["url"], dl))
    try:
        urllib.request.urlretrieve(info["url"], dl)
    except Exception as e:
        return "下载失败: %s" % e
    # 静默安装
    if info["silent"]:
        print("  静默安装中...")
        subprocess.run([dl] + info["silent"], capture_output=True, timeout=300,
                       creationflags=CREATE_NO_WINDOW)
        time.sleep(5)
        if _detect_exe(app):
            return "%s 安装完成 ✅" % app
        return "%s 已下载安装器，但未检测到安装完成（可能需手动点下一步）: %s" % (app, dl)
    return "已下载: %s（非静默安装，请手动运行）" % dl


def open_app(app, args=None):
    """启动工具（参数可选）"""
    exe = _detect_exe(app)
    if not exe:
        r = install(app)
        if "安装完成" not in r and "已安装" not in r:
            return r
        exe = _detect_exe(app)
        if not exe:
            return "安装后仍找不到程序，请手动启动"
    try:
        cmd = [exe] + (args or [])
        subprocess.Popen(cmd)
        return "已启动 %s" % app
    except Exception as e:
        return "启动失败: %s" % e


def write_code(path, code):
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(code)
    except Exception as e:
        return "写入失败: %s" % e
    # 语法检查（.py 用 compile，其他文件跳过）
    err = None
    if path.lower().endswith(".py"):
        try:
            compile(code, path, "exec")
        except SyntaxError as e:
            err = "语法错误: %s（行 %s）" % (e.msg, e.lineno)
    # 按文件类型选 IDE（没装则自动找/下载）
    ext = os.path.splitext(path)[1].lower()
    if ext in (".ets", ".ts", ".json"):
        ide = _pick_ide(["deveco", "vscode", "notepad3"])      # 鸿蒙
    elif ext in (".kt", ".java"):
        ide = _pick_ide(["androidstudio", "vscode", "notepad3"])  # Android
    elif ext in (".c", ".cpp", ".cc", ".h", ".hpp", ".cxx"):
        ide = _pick_ide(["vs", "codeblocks", "vscode", "notepad3"])  # C/C++
    elif ext in (".py", ".js", ".html", ".css", ".md", ".json"):
        ide = _pick_ide(["vscode", "deveco", "notepad3"])       # Python/Web
    else:
        ide = _pick_ide(["vscode", "deveco", "notepad3"])
    r = open_app(ide, [path])
    if err:
        return "%s\n代码已保存: %s（用 %s 打开）\n%s" % (r, path, ide, err)
    return "%s\n代码已保存: %s（用 %s 打开）" % (r, path, ide)


def _pick_ide(cands):
    """按优先级返回第一个已装的 IDE；都没有则返回第一个（触发自动下载）"""
    for c in cands:
        if _detect_exe(c):
            return c
    return cands[0]


def preinstall_ask():
    """缺类代表软件 → 优先类先询问，其他类以后再说。需要 install 授权。"""
    need = require_consent("install", "自动下载安装软件")
    if need:
        return need
    overview, missing = scan_catalog()
    dl_all = [(cat, n) for cat, lack in missing for n in lack if _rep_info(n).get("url")]
    pri = [(c, n) for c, n in dl_all if c in PRIORITY_CATS]   # 常用优先
    rest = [(c, n) for c, n in dl_all if c not in PRIORITY_CATS]
    if not pri:
        return ("常用类（视频/下载/文档/音乐/上网）的代表软件都已安装。"
                "其他缺类：%s（以后需要时再说）" % ("、".join(n for _, n in rest) or "无"))
    print("=== 常用类代表软件缺失，是否预装？（免费/官方直链，优先装这些）===")
    for i, (cat, name) in enumerate(pri, 1):
        print("  [%d] %s（%s）%s" % (i, name, cat, _rep_info(name)["home"]))
    if rest:
        print("  （其他类别 %s 等，以后需要再问我）" % "、".join(n for _, n in rest[:5]))
    print("输入序号安装（多个用逗号，如 1,3），直接回车跳过：")
    try:
        ans = input("> ").strip()
    except EOFError:
        ans = ""
    if not ans:
        return "已跳过预装。"
    todo = []
    for part in ans.split(","):
        part = part.strip()
        if part.isdigit() and 1 <= int(part) <= len(pri):
            todo.append(pri[int(part) - 1])
    if not todo:
        return "没有有效的选择，已跳过。"
    results = []
    for cat, name in todo:
        results.append("[%s] %s -> %s" % (cat, name, install_rep(name)))
    return "\n".join(results)


def generate_report():
    """生成可视 HTML 分类报告并打开浏览器"""
    overview, missing = scan_catalog()
    rows = []
    for cat, tools in CATEGORIES.items():
        pri = "★ 常用" if cat in PRIORITY_CATS else ""
        have, lack = [], []
        for name in tools:
            info = _rep_info(name)
            exe = next((e for e in info.get("exe", []) if os.path.exists(e)), None)
            if exe:
                have.append(name)
            else:
                lack.append(name)
        dl = [n for n in lack if _rep_info(n).get("url")]
        st = "、".join(have) or "—"
        lk = "、".join(lack) or "—"
        auto = ("、".join(dl) + "（可自动下载）") if dl else ("手动/官网" if lack else "—")
        rows.append("<tr><td>%s</td><td class='pri'>%s</td><td class='ok'>%s</td>"
                    "<td>%s</td><td>%s</td></tr>" % (cat, pri, st, lk, auto))
    html = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<title>FairyX 软件分类总览</title><style>
body{font-family:'Microsoft YaHei',sans-serif;background:#0f172a;color:#e2e8f0;padding:24px;max-width:960px;margin:auto}
h1{color:#38bdf8;border-bottom:2px solid #38bdf8;padding-bottom:8px}
table{width:100%;border-collapse:collapse;margin-top:16px}
th,td{border:1px solid #334155;padding:10px 12px;text-align:left}
th{background:#1e293b;color:#94a3b8}
td.ok{color:#4ade80}.pri{color:#fbbf24;font-weight:bold}
tr:nth-child(even){background:#16213a}
.note{color:#94a3b8;margin-top:12px;font-size:13px}
.badge{background:#1e3a5f;color:#7dd3fc;padding:2px 8px;border-radius:10px;font-size:12px}
</style></head><body>
<h1>FairyX 软件分类总览</h1>
<p><span class="badge">★ 常用</span> 视频/下载/文档/音乐/上网等高频类，优先预装</p>
<table><tr><th>分类</th><th>优先级</th><th>已装</th><th>缺失</th><th>可自动安装</th></tr>
__ROWS__
</table>
<p class="note">可自动安装 = 免费官方直链，说"都装上"即自动下载安装；手动/官网 = 大软件需手动。</p>
</body></html>""".replace("__ROWS__", "\n".join(rows))
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data",
                        "fairy_catalog_report.html")
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    try:
        os.startfile(path)
        return "已生成并打开报告: %s" % path
    except Exception:
        return "报告已生成: %s（请手动打开）" % path


def install_rep(name):
    """安装代表软件（官方直链，能静默则静默；大软件提示官网）"""
    info = _rep_info(name)
    if not info.get("url"):
        return ("需手动安装（大软件/官网直链待补）：%s" % info.get("home") or name)
    dl = os.path.join(os.environ.get("TEMP", os.path.join(D, "downloads")), name + "_setup.exe")
    print("    下载 %s <- %s" % (name, info["url"]))
    try:
        urllib.request.urlretrieve(info["url"], dl)
    except Exception as e:
        return "下载失败: %s" % e
    if info.get("silent"):
        try:
            subprocess.run([dl] + info["silent"], capture_output=True, timeout=600,
                           creationflags=CREATE_NO_WINDOW)
            return "安装完成（静默）"
        except Exception as e:
            return "静默安装失败，安装包在: %s（%s）" % (dl, e)
    return "已下载: %s（请手动安装）" % dl


# ---------------- 绿色/单文件软件发现（不写注册表也能用） ----------------

# 绿色/单文件软件扫描目录：优先 config.json 的 paths.portable_dirs（按你本机实际填写）；
# 未配置时扫描 FairyX 根目录 + 常见绿色软件目录 + 各盘根（E:\ F:\ 整盘会较慢，可自行删减）
PORTABLE_DIRS = (_cpaths.get("portable_dirs") or
                 [os.path.join(D, "tools"), os.path.join(D, "everything"),
                  r"C:\Portable", r"D:\Portable", r"D:\Tools", r"D:\Green"])
PORTABLE_SKIP = ("unins", "updater", "wizard", "regfix", "trainer")
PORTABLE_BAD_WORDS = ("install", "setup", "unins", "卸载", "安装", "下载器", "online_installer",
                      "独立版", "regsetup", "regfix", "update", "repair", "runtime")


def _is_portable_exe(name):
    """过滤安装包/系统组件，只留绿色软件/单文件程序"""
    low = name.lower()
    if low.startswith(PORTABLE_SKIP):
        return False
    for w in PORTABLE_BAD_WORDS:
        if w in low:
            return False
    # 绿色/便携/工具特征
    return True


def discover_portable(depth=1, limit=100):
    """扫描常见绿色软件目录，找单文件/免安装 exe（不写注册表）"""
    found = []
    for d in PORTABLE_DIRS:
        if not os.path.isdir(d):
            continue
        try:
            for root, dirs, files in os.walk(d):
                if root[len(d):].count(os.sep) > depth:
                    dirs[:] = []
                    continue
                dirs[:] = [x for x in dirs if x.lower() not in
                           ("models", "model", "cache", "downloads", "runtime", "node_modules")]
                for f in files:
                    if not f.lower().endswith(".exe"):
                        continue
                    if not _is_portable_exe(f):
                        continue
                    try:
                        if os.path.getsize(os.path.join(root, f)) < 200 * 1024:
                            continue
                    except OSError:
                        continue
                    found.append((f, os.path.join(root, f)))
                    if len(found) >= limit:
                        return _dedup(found)
        except Exception:
            continue
    return _dedup(found)


def _dedup(found):
    """同 exe 名只留一个（取最短路径）"""
    seen = {}
    for f, p in found:
        if f not in seen or len(p) < len(seen[f]):
            seen[f] = p
    return sorted(seen.items())


def open_portable(name_part):
    """按名称片段调用绿色软件（不写注册表的单文件程序）"""
    found = discover_portable(depth=1, limit=200)
    hit = [p for f, p in found if name_part.lower() in f.lower()]
    if not hit:
        return "没有找到匹配的绿色软件：%s（可用 --discover 查看全部）" % name_part
    p = min(hit, key=len)
    try:
        subprocess.Popen([p])
        return "已启动绿色软件: %s" % os.path.basename(p)
    except Exception as e:
        return "启动失败: %s" % e


# --------------------------------------------------------------------------
# ★ 2026-10-12【打开 XX 多级递推】系统对象 -> 内置目录 -> 开始菜单 ->
#   Everything 全盘 -> 绿色软件。逐级扩展匹配范围，命中即启动。
# --------------------------------------------------------------------------
# 系统特殊对象（"打开电脑/回收站/设置"这类不是软件，先认出来）
SPECIAL_OPEN = {
    "电脑": "shell:MyComputerFolder",
    "此电脑": "shell:MyComputerFolder",
    "我的电脑": "shell:MyComputerFolder",
    "回收站": "shell:RecycleBinFolder",
    "控制面板": "shell:ControlPanelFolder",
    "网络": "shell:NetworkPlacesFolder",
    "文档": "shell:Personal",
    "下载": "shell:Downloads",
    "桌面": "shell:Desktop",
    "图片": "shell:MyPictures",
    "音乐": "shell:MyMusic",
    "视频": "shell:MyVideos",
    "设置": "ms-settings:",
}

# ★ 2026-10-12【设备语音路由】"打开摄像头/蓝牙/打印机/声音" -> 设备或对应设置页
DEVICE_OPEN = {
    "摄像头": "microsoft.windows.camera:",
    "相机": "microsoft.windows.camera:",
    "蓝牙": "ms-settings:bluetooth",
    "wifi": "ms-settings:wifi",
    "无线网络": "ms-settings:wifi",
    "网络设置": "ms-settings:network",
    "打印机": "ms-settings:printers",
    "扫描仪": "ms-settings:printers",
    "声音": "ms-settings:sound",
    "音量": "ms-settings:sound",
    "音频": "ms-settings:sound",
    "显示器": "ms-settings:display",
    "屏幕": "ms-settings:display",
    "设备管理器": "devmgmt.msc",
    "设备": "devmgmt.msc",
    "usb": "ms-settings:usb",
    "鼠标": "ms-settings:mousetouchpad",
    "键盘": "ms-settings:keyboard",
    "触摸板": "ms-settings:mousetouchpad",
    "电源": "ms-settings:power",
    "电池": "ms-settings:power",
    "存储": "ms-settings:storagesense",
    "应用和功能": "ms-settings:appsfeatures",
    "应用": "ms-settings:appsfeatures",
    "任务栏": "ms-settings:taskbar",
    "开始菜单": "ms-settings:personalization-start",
    "壁纸": "ms-settings:personalization-background",
    "主题": "ms-settings:themes",
    "锁屏": "ms-settings:lockscreen",
    "通知": "ms-settings:notifications",
    "投影": "ms-settings:project",
    "多任务": "ms-settings:multitasking",
    "共享": "ms-settings:shared-experiences",
    "剪贴板": "ms-settings:clipboard",
    "日期和时间": "ms-settings:dateandtime",
    "语言": "ms-settings:keyboard",
    "区域": "ms-settings:regionformatting",
    "辅助功能": "ms-settings:easeofaccess",
    "语音识别": "ms-settings:speech",
    "游戏设置": "ms-settings:gaming",
    "开发者选项": "ms-settings:developers",
    "默认应用": "ms-settings:defaultapps",
    "账户": "ms-settings:accounts",
    "登录选项": "ms-settings:signinoptions",
    "windows更新": "ms-settings:windowsupdate",
    "系统更新": "ms-settings:windowsupdate",
    "安全中心": "ms-settings:windowssecurity",
}
def _startmenu_lnks():
    """收集开始菜单快捷方式：[(显示名, lnk 路径)]（用户可见的软件名最直观）。"""
    out = []
    dirs = [os.path.join(os.environ.get("APPDATA", ""),
                         r"Microsoft\Windows\Start Menu\Programs"),
            os.path.join(os.environ.get("PROGRAMDATA", ""),
                         r"Microsoft\Windows\Start Menu\Programs")]
    for d in dirs:
        if not os.path.isdir(d):
            continue
        try:
            for root, _, files in os.walk(d):
                for f in files:
                    if f.lower().endswith(".lnk"):
                        out.append((os.path.splitext(f)[0].strip(),
                                    os.path.join(root, f)))
        except Exception:
            continue
    return out


def _es_find(name):
    """用 Everything 命令行（es.exe / everything.exe）全盘找 exe；返回第一个路径或 None。"""
    es = None
    for cand in (os.path.join(D, "everything", "es.exe"),
                 os.path.join(D, "everything", "everything.exe")):
        if os.path.exists(cand):
            es = cand
            break
    if not es:
        return None
    try:
        r = subprocess.run([es, "-path-part", "-ext", "exe", name],
                           capture_output=True, text=True, timeout=8)
        lines = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
        return lines[0] if lines else None
    except Exception:
        return None


def _installed_with_exe():
    """注册表已装软件 -> {显示名: exe 路径}（DisplayIcon / UninstallString / InstallLocation 解析）。"""
    import winreg
    out = {}
    roots = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")]
    for hkey, path in roots:
        try:
            k = winreg.OpenKey(hkey, path)
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(k, i)
                    i += 1
                    try:
                        sk = winreg.OpenKey(k, sub)
                        try:
                            nm = None
                            exe = None
                            try:
                                nm, _ = winreg.QueryValueEx(sk, "DisplayName")
                            except OSError:
                                pass
                            for field in ("DisplayIcon", "UninstallString", "InstallLocation"):
                                try:
                                    v, _ = winreg.QueryValueEx(sk, field)
                                except OSError:
                                    continue
                                if not v:
                                    continue
                                cand = str(v).split(",")[0].strip().strip('"')
                                if cand.lower().endswith(".exe") and os.path.exists(cand):
                                    exe = cand
                                    break
                            if nm and len(nm) < 60 and exe:
                                out[nm.strip()] = exe
                        finally:
                            sk.Close()
                    except OSError:
                        pass
                except OSError:
                    break
            k.Close()
        except OSError:
            pass
    return out


def open_smart(text):
    """打开软件多级递推：系统对象 -> 内置目录 -> 开始菜单 -> 注册表 -> Everything -> 绿色软件。
    命中即启动，返回给用户的播报文本。已装软件优先用系统默认方式，没装才自动下载。"""
    t = (text or "").strip().lower()
    if not t:
        return "请告诉我要打开什么软件"
    # 级 0：系统特殊对象（电脑/回收站/设置等）
    for _k, _sh in SPECIAL_OPEN.items():
        if _k in text:
            try:
                os.startfile(_sh)
                return "已打开：" + _k
            except Exception:
                continue
    # 级 0.4：设备路由（摄像头/蓝牙/打印机/声音/显示器/设备管理器等）
    for _k, _uri in DEVICE_OPEN.items():
        if _k in text:
            try:
                os.startfile(_uri)
                return "已打开：" + _k
            except Exception:
                continue
    # 级 0.2：智能家居控制（打开灯/空调/窗帘/扫地机等 -> smart_home 桥）
    # ★★ 2026-10-24【修：语音打不开任何软件，全被智能家居吃掉】
    #   原来这里【无条件】调 smart_home.control(text)，而它在未启用时
    #   也会返回一句"智能家居未启用…"（非 None），于是 `if _sr: return _sr`
    #   立刻短路，后面所有级（语音库 / 知识库 / 开始菜单 / 注册表 / Everything）
    #   根本没机会跑。实测：open_smart('记事本') 和 open_smart('谷歌')
    #   都只回了"智能家居未启用" —— 用户听到的是一句废话，软件没打开。
    #   现在只有目标确实是家电时才进这个分支。
    _SMART_WORDS = ("灯", "空调", "窗帘", "插座", "扫地", "风扇", "净化器",
                    "热水器", "加湿器", "取暖器", "电暖")
    if any(_w in text for _w in _SMART_WORDS):
        try:
            import smart_home
            _sr = smart_home.control(text)
            if _sr:
                return _sr
        except Exception:
            pass
    # 级 0.5：中文语音软件库（扫描后保存，本机已装，路径精确）
    for _nm, _info in load_voice_lib().items():
        if (t in _nm.lower()
                or any(t in _a.lower() for _a in _info.get("aliases", []))):
            try:
                os.startfile(_info["path"])
                return "已打开：" + _nm
            except Exception:
                continue
    # 级 0.6：软件知识库（全网常用软件字典：名字命中 -> exe -> 本机找）
    for _nm, _info in load_knowledge().items():
        if (t in _nm.lower()
                or any(t in _a.lower() for _a in _info.get("aliases", []))):
            _p = _find_exe_on_pc(_info.get("exe", ""))
            if _p:
                try:
                    os.startfile(_p)
                    return "已打开：" + _nm
                except Exception:
                    continue
            return "本机没有安装" + _nm + "，可以先让我下载安装"
    # 级 1：内置目录（强特征词/工具名/类别）—— exe 已装才直接开，没装先看系统里有没有同款
    _fallback_install = None
    hit = find_by_text(t)
    if hit:
        if _detect_exe(hit):
            r = open_app(hit)
            if "失败" not in r:
                return "已打开：" + hit
        else:
            _fallback_install = hit
    # 级 2：开始菜单快捷方式（用户可见软件名，最直观）
    for nm, lnk in _startmenu_lnks():
        if t in nm.lower() or nm.lower() in t:
            try:
                os.startfile(lnk)
                return "已打开：" + nm
            except Exception:
                continue
    # 级 3：注册表已装软件（DisplayIcon / UninstallString 解析 exe）
    for nm, exe in _installed_with_exe().items():
        if t in nm.lower() or nm.lower() in t:
            try:
                os.startfile(exe)
                return "已打开：" + nm
            except Exception:
                continue
    # 级 4：Everything 全盘 exe（有 es.exe 时）
    exe = _es_find(t)
    if exe:
        try:
            os.startfile(exe)
            return "已打开：" + os.path.basename(exe)
        except Exception:
            pass
    # 级 5：绿色软件（免安装单文件）
    r = open_portable(t)
    if not r.startswith("没有找到"):
        return r
    # 级 6：内置目录命中但没装 -> 自动下载安装（兜底）
    if _fallback_install:
        r = open_app(_fallback_install)
        if "失败" not in r:
            return "已打开：" + _fallback_install
    return "没有找到软件：" + text


# --------------------------------------------------------------------------
# ★ 2026-10-12【中文语音软件库】扫描已装 -> 分类 -> 保存；语音调用直接查
# --------------------------------------------------------------------------
def _classify(name):
    """按关键词给软件归类；未知返回"其他"。"""
    n = name.lower()
    for cat, kws in CAT_KEYWORDS:
        if any(k in n for k in kws):
            return cat
    return "其他"


def build_voice_lib():
    """扫描已装软件 -> 中文语音软件库（显示名/别名/功能类别/路径），保存 JSON。
    以后语音"打开 XX"直接查这个库，又快又准；开机扫描后自动调用。"""
    lib = {}
    # ① 开始菜单快捷方式
    for nm, lnk in _startmenu_lnks():
        if nm not in lib:
            lib[nm] = {"path": lnk, "cat": _classify(nm), "aliases": []}
    # ② 注册表已装（DisplayIcon 解析 exe）
    for nm, exe in _installed_with_exe().items():
        if nm not in lib:
            lib[nm] = {"path": exe, "cat": _classify(nm), "aliases": []}
    # ③ 内置目录（补精确类别 + 别名）
    for name, info in APP_CATALOG.items():
        exe = _detect_exe(name)
        if name not in lib:
            if exe:
                lib[name] = {"path": exe, "cat": info.get("cat", _classify(name)),
                             "aliases": list(info.get("win", []))}
        else:
            lib[name]["cat"] = info.get("cat", lib[name].get("cat", "其他"))
            _als = list(set(lib[name].get("aliases", []) + info.get("win", [])))
            lib[name]["aliases"] = _als
    # ④ 软件知识库（补类别 + 别名，让已装软件带知识库的丰富信息）
    try:
        for name, info in load_knowledge().items():
            if name in lib:
                if not lib[name].get("cat") or lib[name]["cat"] == "其他":
                    lib[name]["cat"] = info.get("cat", "其他")
                _als = list(set(lib[name].get("aliases", [])
                                + info.get("aliases", [])))
                lib[name]["aliases"] = _als
    except Exception:
        pass
    try:
        os.makedirs(os.path.dirname(VOICE_LIB), exist_ok=True)
        with open(VOICE_LIB, "w", encoding="utf-8") as f:
            json.dump(lib, f, ensure_ascii=False, indent=1)
    except Exception as e:
        return "语音库保存失败: %s" % e
    return "语音软件库已建立：%d 个软件，保存到 data/voice_apps.json" % len(lib)


def load_voice_lib():
    """读中文语音软件库；没有就返回空。"""
    try:
        with open(VOICE_LIB, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# --------------------------------------------------------------------------
# ★ 2026-10-12【软件知识库】全网常用软件字典（名 -> exe/类别/别名）
#   语音"打开 XX"：先语音库（本机已装）-> 知识库（名字命中 -> exe -> 本机找）
# --------------------------------------------------------------------------
KNOWLEDGE_FILE = os.path.join(D, "data", "app_knowledge.json")


def build_knowledge():
    """核心 KNOWN（800+ 条）-> data/app_knowledge.json；扫描后随语音库一起更新。"""
    lib = {}
    for nm, exe, cat, aliases in FK.KNOWN:
        lib[nm] = {"exe": exe, "cat": cat,
                   "aliases": aliases.split(";") if aliases else []}
    try:
        os.makedirs(os.path.dirname(KNOWLEDGE_FILE), exist_ok=True)
        with open(KNOWLEDGE_FILE, "w", encoding="utf-8") as f:
            json.dump(lib, f, ensure_ascii=False, indent=1)
    except Exception as e:
        return "知识库保存失败: %s" % e
    return "软件知识库已建立：%d 条" % len(lib)


def load_knowledge():
    """读软件知识库；没有就返回空。"""
    try:
        with open(KNOWLEDGE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _find_exe_on_pc(exe):
    """在已装软件里按 exe 文件名找完整路径（注册表 + 开始菜单 lnk 目标）。"""
    if not exe:
        return None
    e = exe.lower()
    try:
        for _nm, _p in _installed_with_exe().items():
            if _p and _p.lower().endswith(e):
                return _p
    except Exception:
        pass
    try:
        import win32com.client
        for _nm, _lnk in _startmenu_lnks():
            try:
                sh = win32com.client.Dispatch("WScript.Shell")
                tgt = sh.CreateShortcut(_lnk).TargetPath or ""
                if tgt.lower().endswith(e):
                    return tgt
            except Exception:
                continue
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------
# ★ 2026-10-12【关闭 XX 多级递推】进程名 -> 窗口标题(WM_CLOSE) -> taskkill 兜底
# --------------------------------------------------------------------------
def _kill_proc(proc):
    """按进程名优雅终止（先温和请求，失败再强杀）。成功返回 True。"""
    if not proc:
        return False
    try:
        r = subprocess.run(["taskkill", "/IM", proc, "/T"],
                           capture_output=True, text=True, timeout=15)
        if r.returncode == 0:
            return True
        r2 = subprocess.run(["taskkill", "/IM", proc, "/F", "/T"],
                            capture_output=True, text=True, timeout=15)
        return r2.returncode == 0
    except Exception:
        return False


def _close_window_by_title(part):
    """按窗口标题发 WM_CLOSE（优雅关窗，可保存数据）。成功返回 True。"""
    import ctypes
    user32 = ctypes.windll.user32
    found = []
    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _cb(hwnd, _):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if n <= 0 or n > 300:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if part.lower() in buf.value.lower():
                found.append(hwnd)
        except Exception:
            pass
        return True
    user32.EnumWindows(_cb, 0)
    if not found:
        return False
    for hwnd in found[:3]:
        try:
            user32.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE
        except Exception:
            pass
    return True


def _running_procs(name):
    """列出当前运行、名字含 name 的进程名。"""
    try:
        r = subprocess.run(["tasklist", "/fo", "csv", "/nh"],
                           capture_output=True, text=True, timeout=10)
    except Exception:
        return []
    procs = set()
    for line in r.stdout.splitlines():
        parts = line.split(",")
        if not parts:
            continue
        p = parts[0].strip().strip('"')
        if name.lower() in p.lower():
            procs.add(p)
    return sorted(procs)


def close_smart(text):
    """关闭软件/窗口多级递推：内置目录进程名 -> 窗口标题 -> 注册表已装 -> 运行进程。
    命中即关，返回给用户的播报文本。"""
    t = (text or "").strip().lower()
    if not t:
        return "请告诉我要关闭什么"
    # 级 1：内置目录（APP_CATALOG 名/别名 -> exe 进程名）
    for name, info in APP_CATALOG.items():
        if name.lower() in t or any(w in t for w in info["win"]):
            for exe in info["exe"]:
                proc = os.path.basename(exe)
                if _kill_proc(proc):
                    return "已关闭：" + name
    # 级 2：窗口标题（优雅 WM_CLOSE，可保存数据）
    if _close_window_by_title(t):
        return "已关闭窗口：" + t
    # 级 3：注册表已装（DisplayIcon 解析 exe 进程名）
    for nm, exe in _installed_with_exe().items():
        if t in nm.lower() or nm.lower() in t:
            if _kill_proc(os.path.basename(exe)):
                return "已关闭：" + nm
    # 级 4：运行进程名模糊（taskkill）
    for p in _running_procs(t):
        if _kill_proc(p):
            return "已关闭：" + p
    return "没有找到要关闭的软件：" + text


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="FairyX 工具调度中心")
    ap.add_argument("--list", action="store_true", help="列出全部可调度工具")
    ap.add_argument("--find", metavar="话", help="按需求匹配工具")
    ap.add_argument("--install", metavar="工具名", help="自动下载安装（vscode/aria2）")
    ap.add_argument("--open", metavar="工具名", help="打开工具")
    ap.add_argument("--write", nargs=2, metavar=("路径", "代码"), help="写代码并用IDE打开")
    ap.add_argument("--catalog", action="store_true", help="扫描全软件分类总览（自动归类）")
    ap.add_argument("--prep", action="store_true", help="缺类代表软件询问预装（常用类优先）")
    ap.add_argument("--report", action="store_true", help="生成可视HTML分类报告并打开")
    ap.add_argument("--discover", action="store_true", help="扫描绿色/单文件软件（不写注册表）")
    ap.add_argument("--run", metavar="名称片段", help="调用绿色软件（按文件名模糊匹配）")
    a = ap.parse_args()
    if a.list:
        print(list_apps())
    elif a.find:
        app = find_by_text(a.find)
        print("匹配工具: %s" % app if app else "没有匹配到工具")
        if app:
            exe = _detect_exe(app)
            print("状态: %s" % ("已装 → %s" % exe if exe else "未装（可自动下载）"))
    elif a.install:
        print(install(a.install))
    elif a.open:
        print(open_app(a.open))
    elif a.write:
        print(write_code(a.write[0], a.write[1]))
    elif a.catalog:
        ov, _ = scan_catalog()
        print(ov)
        print("\n=== 本机全部已装软件（注册表 %d 项，供归类参考）===" % len(scan_installed_all()))
        print("、".join(scan_installed_all()[:80]))
    elif a.prep:
        print(preinstall_ask())
    elif a.report:
        print(generate_report())
    elif a.discover:
        need = require_consent("scan", "扫描绿色/单文件软件")
        if need:
            print(need)
        else:
            found = discover_portable(depth=1, limit=120)
            print("发现绿色/单文件软件 %d 个（免安装，可直接调用）：" % len(found))
            for f, p in found[:60]:
                print("  %s -> %s" % (f, p))
    elif a.run:
        print(open_portable(a.run))
    else:
        ap.print_help()
