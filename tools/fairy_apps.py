# -*- coding: utf-8 -*-
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request


CREATE_NO_WINDOW = 0x08000000


def _cfg_path():
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.json"))

def _cfg():
    try:
        with open(_cfg_path(), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


D = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_cpaths = _cfg().get("paths") or {}

def consent_ok(kind):
    try:
        with open(_cfg_path(), encoding="utf-8") as f:
            return bool(json.load(f).get("consent", {}).get(kind, False))
    except Exception:
        return False

def require_consent(kind, action_desc):
    if consent_ok(kind):
        return None
    return ("需要使用人授权后才能%s。\n"
            "请在 FairyX 启动时同意授权弹窗，或手动编辑 config.json 的 "
            "consent 段把 %s 改为 true。" % (action_desc, kind))


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


CAT_DEFAULT = {"代码": "vscode", "文档": "wps", "下载": "aria2",
               "搜索": "everything", "AI": "ollama"}


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
    if name in APP_CATALOG:
        return APP_CATALOG[name]
    return REP_SOURCES.get(name) or dict(url=None, silent=[], home="", exe=[])

def scan_catalog():
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
    return "\n".join(lines), missing

def scan_installed_all():
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
    t = text.lower()

    for name, info in APP_CATALOG.items():
        if any(w in t for w in info.get("strong", [])):
            return name

    for name, info in APP_CATALOG.items():
        if name in t or any(w in t for w in info["win"]):
            return name

    for cat, app in CAT_DEFAULT.items():
        if cat in text:
            return app
    return None

def install(app):
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

    err = None
    if path.lower().endswith(".py"):
        try:
            compile(code, path, "exec")
        except SyntaxError as e:
            err = "语法错误: %s（行 %s）" % (e.msg, e.lineno)

    ext = os.path.splitext(path)[1].lower()
    if ext in (".ets", ".ts", ".json"):
        ide = _pick_ide(["deveco", "vscode", "notepad3"])
    elif ext in (".kt", ".java"):
        ide = _pick_ide(["androidstudio", "vscode", "notepad3"])
    elif ext in (".c", ".cpp", ".cc", ".h", ".hpp", ".cxx"):
        ide = _pick_ide(["vs", "codeblocks", "vscode", "notepad3"])
    elif ext in (".py", ".js", ".html", ".css", ".md", ".json"):
        ide = _pick_ide(["vscode", "deveco", "notepad3"])
    else:
        ide = _pick_ide(["vscode", "deveco", "notepad3"])
    r = open_app(ide, [path])
    if err:
        return "%s\n代码已保存: %s（用 %s 打开）\n%s" % (r, path, ide, err)
    return "%s\n代码已保存: %s（用 %s 打开）" % (r, path, ide)

def _pick_ide(cands):
    for c in cands:
        if _detect_exe(c):
            return c
    return cands[0]

def preinstall_ask():
    need = require_consent("install", "自动下载安装软件")
    if need:
        return need
    overview, missing = scan_catalog()
    dl_all = [(cat, n) for cat, lack in missing for n in lack if _rep_info(n).get("url")]
    pri = [(c, n) for c, n in dl_all if c in PRIORITY_CATS]
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


PORTABLE_DIRS = (_cpaths.get("portable_dirs") or
                 [os.path.join(D, "tools"), os.path.join(D, "everything"),
                  r"C:\Portable", r"D:\Portable", r"D:\Tools", r"D:\Green"])
PORTABLE_SKIP = ("unins", "updater", "wizard", "regfix", "trainer")
PORTABLE_BAD_WORDS = ("install", "setup", "unins", "卸载", "安装", "下载器", "online_installer",
                      "独立版", "regsetup", "regfix", "update", "repair", "runtime")

def _is_portable_exe(name):
    low = name.lower()
    if low.startswith(PORTABLE_SKIP):
        return False
    for w in PORTABLE_BAD_WORDS:
        if w in low:
            return False

    return True

def discover_portable(depth=1, limit=100):
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
    seen = {}
    for f, p in found:
        if f not in seen or len(p) < len(seen[f]):
            seen[f] = p
    return sorted(seen.items())

def open_portable(name_part):
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
