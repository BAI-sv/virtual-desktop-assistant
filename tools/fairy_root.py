# -*- coding: utf-8 -*-
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_INFERRED_ROOT = os.path.dirname(_HERE)

CONFIG_NAME = "fairy_root.json"
ENV_ROOT = "FAIRY_ROOT"
ENV_CONFIG = "FAIRY_ROOT_CONFIG"
ENV_WORKDIR = "FAIRY_WORKDIR"
ENV_INDEXTTS = "INDEXTTS_ROOT"
ENV_VOICE_SRC = "FAIRY_VOICE_SRC"


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
    try:
        sys.stderr.write("[fairy_root] " + msg + "\n")
    except Exception:
        pass

def _load_config():
    cands = []
    env_cfg = os.environ.get(ENV_CONFIG)
    if env_cfg:
        cands.append(env_cfg)

    env_root = os.environ.get(ENV_ROOT)
    if env_root:
        cands.append(os.path.join(env_root, CONFIG_NAME))
    cands.append(os.path.join(_INFERRED_ROOT, CONFIG_NAME))
    cands.append(os.path.join(_HERE, CONFIG_NAME))
    for p in cands:
        if not p or not os.path.isfile(p):
            continue
        try:


            with open(p, encoding="utf-8-sig") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data, p
            _CONFIG_ERRORS.append("%s: top level is %s, not object"
                                  % (p, type(data).__name__))
            _warn("config top level is not an object: " + p)
            return {}, p
        except Exception as e:

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


ROOT = _abs(_pick(ENV_ROOT, "root", _INFERRED_ROOT))
WORKDIR = _abs(_pick(ENV_WORKDIR, "workdir", ROOT))

_indextts = _pick(ENV_INDEXTTS, "indextts_root", None)
if _indextts:
    INDEXTTS_ROOT = _abs(_indextts)
else:
    INDEXTTS_ROOT = _first_existing(INDEXTTS_CANDIDATES) or DEFAULT_INDEXTTS_ROOT
INDEXTTS_ROOT = _abs(INDEXTTS_ROOT)


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


WORK_LOGS = os.path.join(WORKDIR, "logs")
BALL_LOG_DIR = WORK_LOGS


VOICE_SRC = _abs(_pick(ENV_VOICE_SRC, "voice_src",
                       os.path.join(WORKDIR, "fairy_voice")))


NAI_ROOT = _abs(_pick("FAIRY_NAI_ROOT", "nai_root",
                      os.path.dirname(os.path.dirname(INDEXTTS_ROOT))))


ASSET_SRC = _abs(_pick("FAIRY_ASSET_SRC", "asset_src",
                       os.path.join(WORKDIR, "fairy_assets")))


def as_dict():
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
    return os.path.join(ROOT, *parts)

def log(*parts):
    return os.path.join(LOGS, *parts)

def ensure_dirs(*names):
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


import subprocess as _subprocess

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


NOWIN_FLAGS = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW

def pythonw_of(exe=None):
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
    kw.setdefault("creationflags", CREATE_NO_WINDOW)
    kw.setdefault("stdin", _subprocess.DEVNULL)
    return kw

def silent_run(args, **kw):
    return _subprocess.run(args, **silent_kwargs(**kw))

def silent_popen(args, **kw):
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
