# -*- coding: utf-8 -*-
"""
fairy_root.py —— 虚拟桌面助手 的【配置根】

【为什么需要它】
  改造前，35 个文件里散落着 80 处硬编码绝对路径（项目根 / 旧工作目录各写死一份）。
  别人拿到源码后必须逐个文件改路径，否则跑不起来。
  有了这一层之后，【只改一个配置文件】就能跑。

【解析优先级】ROOT 为例
  1. 环境变量 FAIRY_ROOT
  2. 配置文件里的 root（见下方【配置文件位置】）
  3. 自动推断：本文件所在目录的父目录（即 tools/ 的上一级）
  -> 三档都拿不到才报错（实际上第 3 档总能拿到，所以永不报错）

【配置文件位置】按顺序找，第一个存在的生效
  1. 环境变量 FAIRY_ROOT_CONFIG 指向的文件
  2. <自动推断的根>/fairy_root.json
  3. <本文件所在目录>/fairy_root.json

【配置文件格式】（全部可选，缺省即用默认值）
  {
    "root":          "<FairyX 根目录>",
    "workdir":       "<工作目录>",
    "indextts_root": "<IndexTTS 安装目录>"
  }

【用法】
  其它模块：      import fairy_root  ->  fairy_root.LOGS / fairy_root.VOICE ...
  命令行自检：    py -3.12 fairy_root.py --show     打印所有解析出的路径
                  py -3.12 fairy_root.py --json     同上，机器可读
                  py -3.12 fairy_root.py --init     生成一份 fairy_root.json 模板

【硬约束】
  · 纯标准库，零依赖
  · ★ 绝不 import 任何项目内模块（否则会循环导入）
"""
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_INFERRED_ROOT = os.path.dirname(_HERE)          # tools/ 的上一级

CONFIG_NAME = "fairy_root.json"
ENV_ROOT = "FAIRY_ROOT"
ENV_CONFIG = "FAIRY_ROOT_CONFIG"
ENV_WORKDIR = "FAIRY_WORKDIR"
ENV_INDEXTTS = "INDEXTTS_ROOT"
ENV_VOICE_SRC = "FAIRY_VOICE_SRC"

# IndexTTS 的候选目录（未配置时的常见位置；实际路径请在 fairy_root.json 里指定）
# ★ 2026-10-10：本机把 IndexTTS 2.5 迁到了【独立目录】（复制 + 自带基础 Python，
#   已完全脱离原来的整合包），所以把它放候选表第一位优先选中。
#   ✅ 2026-10-10 晚：INDEXTTS_ROOT 已实现【环境变量 INDEXTTS_ROOT > fairy_root.json 的
#      indextts_root > 本候选列表】三级取值（见下方 _pick 那几行）。
#   ★★ 当时"配了也不生效"的真凶不是这里：fairy_root.json 被存成了【带 BOM 的 UTF-8】，
#      而 json.load(open(..., encoding="utf-8")) 遇到 BOM 会抛 JSONDecodeError，
#      又被 except 静默吞掉 -> 整个配置文件【形同不存在】。已用 utf-8-sig 兼容 + 留痕。
INDEXTTS_CANDIDATES = [
    r"D:\IndexTTS-2.5",
    os.path.join(_INFERRED_ROOT, "indextts"),
    r"C:\IndexTTS",
    r"D:\IndexTTS",
    r"E:\IndexTTS",
]
DEFAULT_INDEXTTS_ROOT = INDEXTTS_CANDIDATES[0]


def _first_existing(paths):
    for p in paths:
        if p and os.path.isdir(p):
            return p
    return None


_CONFIG_ERRORS = []


def _warn(msg):
    """配置出问题必须留痕。★ 只用 ASCII：GBK 控制台下 print 中文会抛异常
    （黑窗事故的同一根因）。静默失败让整个配置文件白配了一天（2026-10-10）。"""
    try:
        sys.stderr.write("[fairy_root] " + msg + "\n")
    except Exception:
        pass


def _load_config():
    """返回 (cfg_dict, cfg_path)。找不到就返回 ({}, None)。"""
    cands = []
    env_cfg = os.environ.get(ENV_CONFIG)
    if env_cfg:
        cands.append(env_cfg)
    # 设了 FAIRY_ROOT 就以它为准找配置，避免"根换了、配置还读旧的"这种混搭
    env_root = os.environ.get(ENV_ROOT)
    if env_root:
        cands.append(os.path.join(env_root, CONFIG_NAME))
    cands.append(os.path.join(_INFERRED_ROOT, CONFIG_NAME))
    cands.append(os.path.join(_HERE, CONFIG_NAME))
    for p in cands:
        if not p or not os.path.isfile(p):
            continue
        try:
            # ★ encoding="utf-8-sig"：兼容【带 BOM】的 UTF-8。
            #   记事本旧版 / PowerShell 5.1 的 Set-Content -Encoding UTF8 都会写 BOM，
            #   而 json.load(encoding="utf-8") 碰到 BOM 直接抛 JSONDecodeError。
            with open(p, encoding="utf-8-sig") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data, p
            _CONFIG_ERRORS.append("%s: top level is %s, not object"
                                  % (p, type(data).__name__))
            _warn("config top level is not an object: " + p)
            return {}, p
        except Exception as e:
            # 配置坏了不该让整个程序起不来；退到下一档 —— 但【必须留痕】
            _CONFIG_ERRORS.append("%s: %s: %s" % (p, type(e).__name__, e))
            _warn("bad config %s -> %s: %s" % (p, type(e).__name__, e))
            continue
    return {}, None


CFG, CFG_PATH = _load_config()


def _pick(env_name, cfg_key, fallback):
    v = os.environ.get(env_name)
    if v:
        return v
    v = CFG.get(cfg_key)
    if v:
        return v
    return fallback


def _abs(p):
    return os.path.abspath(os.path.expandvars(os.path.expanduser(str(p))))


# ---------------------------------------------------------------- 根
ROOT = _abs(_pick(ENV_ROOT, "root", _INFERRED_ROOT))
WORKDIR = _abs(_pick(ENV_WORKDIR, "workdir", ROOT))

_indextts = _pick(ENV_INDEXTTS, "indextts_root", None)
if _indextts:
    INDEXTTS_ROOT = _abs(_indextts)
else:
    INDEXTTS_ROOT = _first_existing(INDEXTTS_CANDIDATES) or DEFAULT_INDEXTTS_ROOT
INDEXTTS_ROOT = _abs(INDEXTTS_ROOT)

# ---------------------------------------------------------------- 由 ROOT 派生
TOOLS = os.path.join(ROOT, "tools")
VOICE = os.path.join(ROOT, "voice")
VOICES = os.path.join(VOICE, "voices")
INDEX_VOICES = os.path.join(VOICE, "index_voices")
CLONE_CACHE = os.path.join(VOICE, "clone_cache")
VOICE_INDEX = os.path.join(VOICE, "voice_index.json")
LINE_POOLS = os.path.join(VOICE, "line_pools.json")
ASSETS = os.path.join(ROOT, "assets")
LOGS = os.path.join(ROOT, "logs")
DATA = os.path.join(ROOT, "data")
MODELS = os.path.join(ROOT, "models")
WHISPER_MODELS = os.path.join(MODELS, "whisper")
WAKEWORD = os.path.join(ROOT, "wakeword")
WAKEWORD_MODELS = os.path.join(WAKEWORD, "models")
WAKEWORD_KW = os.path.join(WAKEWORD, "kw")
CONFIG = os.path.join(ROOT, "fairy.json")
BRIEFING_CONF = os.path.join(ROOT, "briefing.json")
BALL_POS = os.path.join(ROOT, "fairy_ball_pos.json")
PID_DIR = ROOT
BACKUP = os.path.join(ROOT, "backup")
DOCS = os.path.join(ROOT, "docs")
OPENSOURCE = os.path.join(ROOT, "opensource")

# ---------------------------------------------------------------- 由 WORKDIR 派生
# 老代码里球的日志写在【工作目录】下的 logs，不在项目根下的 logs。
# WORKDIR 缺省就是 ROOT，所以开源版（没有 workdir 配置）会自动落回 ROOT\logs。
WORK_LOGS = os.path.join(WORKDIR, "logs")
BALL_LOG_DIR = WORK_LOGS

# ---------------------------------------------------------------- 语音素材来源
# 早期脚本（build_voice_index.py）从一个【个人下载目录】读取原始语音包。
# 这个路径与项目布局无关，所以单独给一个可配置项；开源版请按需改。
VOICE_SRC = _abs(_pick(ENV_VOICE_SRC, "voice_src",
                       os.path.join(WORKDIR, "fairy_voice")))

# 本机整合包的主目录（= IndexTTS 根的上上级）。
# 开机自举时顺带探测同目录下其它整合包（ComfyUI 等）。
# 可从 fairy_root.json 的 nai_root 覆盖；未配置时由 INDEXTTS_ROOT 推导。
NAI_ROOT = _abs(_pick("FAIRY_NAI_ROOT", "nai_root",
                      os.path.dirname(os.path.dirname(INDEXTTS_ROOT))))

# 素材管线的原图目录（build_assets.py 用；原来是某台机器上的桌面路径）。
ASSET_SRC = _abs(_pick("FAIRY_ASSET_SRC", "asset_src",
                       os.path.join(WORKDIR, "fairy_assets")))

# 模板的值用【当前解析结果】填，不写死字面量
#   -> 源码里不出现本机路径；生成的配置也更对得上本机
# ---------------------------------------------------------------- 小工具
def as_dict():
    """所有解析结果，供 --json / 排障用。"""
    out = {
        "_config_file": CFG_PATH or "",
        "_config_errors": list(_CONFIG_ERRORS),
        "_inferred_root": _INFERRED_ROOT,
        "ROOT": ROOT,
        "WORKDIR": WORKDIR,
        "INDEXTTS_ROOT": INDEXTTS_ROOT,
        "TOOLS": TOOLS,
        "VOICE": VOICE,
        "VOICES": VOICES,
        "INDEX_VOICES": INDEX_VOICES,
        "CLONE_CACHE": CLONE_CACHE,
        "VOICE_INDEX": VOICE_INDEX,
        "LINE_POOLS": LINE_POOLS,
        "ASSETS": ASSETS,
        "LOGS": LOGS,
        "WORK_LOGS": WORK_LOGS,
        "BALL_LOG_DIR": BALL_LOG_DIR,
        "VOICE_SRC": VOICE_SRC,
        "NAI_ROOT": NAI_ROOT,
        "ASSET_SRC": ASSET_SRC,
        "DATA": DATA,
        "MODELS": MODELS,
        "WHISPER_MODELS": WHISPER_MODELS,
        "WAKEWORD": WAKEWORD,
        "WAKEWORD_MODELS": WAKEWORD_MODELS,
        "WAKEWORD_KW": WAKEWORD_KW,
        "CONFIG": CONFIG,
        "BRIEFING_CONF": BRIEFING_CONF,
        "BALL_POS": BALL_POS,
        "PID_DIR": PID_DIR,
        "BACKUP": BACKUP,
        "DOCS": DOCS,
        "OPENSOURCE": OPENSOURCE,
    }
    return out


def j(*parts):
    """拼一个 ROOT 下的绝对路径： j('logs','x.json')"""
    return os.path.join(ROOT, *parts)


def log(*parts):
    """拼一个 LOGS 下的绝对路径： log('x.json')"""
    return os.path.join(LOGS, *parts)


def ensure_dirs(*names):
    """按需创建目录（传属性名，如 'LOGS','CLONE_CACHE'）。失败不抛。"""
    g = globals()
    for n in names:
        p = g.get(n)
        if isinstance(p, str):
            try:
                os.makedirs(p, exist_ok=True)
            except Exception:
                pass


TEMPLATE = {
    "_说明": "虚拟桌面助手 配置根。开源版请按你的实际路径改好，另存为 fairy_root.json（放在根目录或 tools/ 下）。",
    "_优先级": "环境变量 FAIRY_ROOT > 本文件的 root > 自动推断（tools 的上一级）",
    "root": _INFERRED_ROOT,
    "workdir": WORKDIR,
    "indextts_root": INDEXTTS_ROOT,
    "voice_src": VOICE_SRC,
    "asset_src": ASSET_SRC,
}


# ==========================================================================
# ★★ 2026-10-10【消灭黑窗】统一的"起子进程不要弹窗"工具
# ==========================================================================
# 背景（用户反复抱怨、之前修了两次都没修干净）：
#   本机默认终端是 Windows Terminal。任何【控制台子系统】的程序
#   （python.exe / cmd.exe / powershell.exe / py.exe / conhost）一旦被
#   【没有控制台的父进程】拉起（pythonw.exe、计划任务、开机自启就是），
#   Windows 就会给它【新建一个控制台窗口】-> 整个 Windows Terminal 弹出来。
#   ★ 只有在父进程没有控制台时才会弹 —— 所以从 cmd 里跑往往看不出来。
#
# 三个常量（★ 别记错，0x00000200 不是 CREATE_NO_WINDOW）：
#   0x00000008 = DETACHED_PROCESS       脱离控制台（子进程根本没有控制台）
#   0x00000200 = CREATE_NEW_PROCESS_GROUP
#   0x08000000 = CREATE_NO_WINDOW       ★ 这才是"不显示窗口"（新建控制台但隐藏）
#
# ★ 最可靠的是【换解释器】：pythonw.exe 根本不创建控制台。
#   本模块顺手提供 pythonw_of() 把 python.exe 换成 pythonw.exe。
import subprocess as _subprocess

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

# 起后台常驻程序用这一套：脱离控制台 + 不显示窗口 + 关掉终端也不被 Ctrl+C 带走
NOWIN_FLAGS = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW


def pythonw_of(exe=None):
    """把 python.exe 换成同目录的 pythonw.exe（无控制台）。换不了就原样返回。

    ★ 为什么必须换解释器（而不是加 creationflags）：
      venv 的 Scripts\\python.exe 只是个启动器，它会按 pyvenv.cfg 找真正的
      python.exe【重新启动一次自己】—— 那次二次启动是 venv 内部做的，
      creationflags 管不到，于是下层进程照样挂一个 conhost 弹窗。
      pythonw.exe 不创建控制台，从根上解决。
    """
    if exe is None:
        exe = sys.executable
    if not exe:
        return exe
    base = os.path.basename(exe).lower()
    if base == "pythonw.exe":
        return exe
    if base in ("python.exe", "py.exe"):
        cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(cand):
            return cand
    return exe


def silent_kwargs(**kw):
    """给 subprocess 调用补上"绝不弹窗"的参数。已显式给过的以调用方为准。

    用法：
        p = subprocess.Popen([...], **fairy_root.silent_kwargs(stdout=f, stderr=f))
        r = subprocess.run([...], **fairy_root.silent_kwargs(capture_output=True))
    """
    kw.setdefault("creationflags", CREATE_NO_WINDOW)
    kw.setdefault("stdin", _subprocess.DEVNULL)
    return kw


def silent_run(args, **kw):
    """subprocess.run 的静默版（不会弹窗）。"""
    return _subprocess.run(args, **silent_kwargs(**kw))


def silent_popen(args, **kw):
    """subprocess.Popen 的静默版（不会弹窗）。"""
    return _subprocess.Popen(args, **silent_kwargs(**kw))


def cmd_init():
    dst = os.path.join(ROOT, CONFIG_NAME)
    if os.path.exists(dst):
        print("已存在，不覆盖：%s" % dst)
        return 0
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(TEMPLATE, f, ensure_ascii=False, indent=2)
    print("已生成模板：%s" % dst)
    return 0


def cmd_show(as_json=False):
    d = as_dict()
    if as_json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return 0
    print("=== 虚拟桌面助手 配置根解析结果 ===")
    print("  配置文件      : %s" % (d["_config_file"] or "(未使用，全部走自动推断)"))
    print("  自动推断根    : %s" % d["_inferred_root"])
    print("")
    for k in ("ROOT", "WORKDIR", "INDEXTTS_ROOT", "TOOLS", "VOICE", "VOICES",
              "INDEX_VOICES", "CLONE_CACHE", "VOICE_INDEX", "LINE_POOLS",
              "ASSETS", "LOGS", "WORK_LOGS", "BALL_LOG_DIR", "VOICE_SRC", "NAI_ROOT", "ASSET_SRC", "DATA", "MODELS",
              "WHISPER_MODELS", "WAKEWORD", "WAKEWORD_MODELS", "WAKEWORD_KW",
              "CONFIG", "BRIEFING_CONF", "BALL_POS", "PID_DIR"):
        p = d[k]
        mark = "OK " if os.path.exists(p) else "-- "
        print("  %s%-16s %s" % (mark, k, p))
    return 0


def main():
    a = sys.argv[1:]
    if "--json" in a:
        return cmd_show(True)
    if "--init" in a:
        return cmd_init()
    return cmd_show(False)


if __name__ == "__main__":
    sys.exit(main())
