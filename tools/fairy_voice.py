#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fairy_voice.py —— Fairy 音色管理（IndexTTS 克隆音色）。

作用：列出可用音色、切换当前音色、保存用户音色（参考音频）。
音色就是 voice/index_voices 下的 wav；当前用哪个写在 fairy.json 的
endpoints.ref_audio 里（tts_get 全链路读取，切换后下一次播报即生效）。

用法：
  py -3.12 fairy_voice.py list            列出可用音色
  py -3.12 fairy_voice.py get             当前音色名
  py -3.12 fairy_voice.py set <音色名.wav>  切换当前音色
  py -3.12 fairy_voice.py save <音频路径> [音色名]  保存用户音色并切换
"""
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
VOICE_DIR = os.path.join(ROOT, "voice")
INDEX_VOICES = os.path.join(VOICE_DIR, "index_voices")
CONFIG = os.path.join(ROOT, "fairy.json")


def list_voices():
    out = []
    if os.path.isdir(INDEX_VOICES):
        for f in sorted(os.listdir(INDEX_VOICES)):
            if f.lower().endswith(".wav"):
                out.append(f)
    return out


def current():
    try:
        j = json.load(open(CONFIG, encoding="utf-8"))
        p = (j.get("endpoints") or {}).get("ref_audio") or ""
        return os.path.basename(p) if p else "fairy.wav"
    except Exception:
        return "fairy.wav"


def set_voice(name):
    p = name if os.path.isabs(name) else os.path.join(INDEX_VOICES, name)
    if not os.path.isfile(p) or not p.lower().endswith(".wav"):
        raise FileNotFoundError("音色不存在: %s" % p)
    with open(CONFIG, encoding="utf-8") as f:
        j = json.load(f)
    j.setdefault("endpoints", {})["ref_audio"] = p
    tmp = CONFIG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(j, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG)
    return os.path.basename(p)


def save_user_voice(src, name):
    """保存用户音色：复制到 index_voices 并切为当前。"""
    if not os.path.isfile(src):
        raise FileNotFoundError(src)
    os.makedirs(INDEX_VOICES, exist_ok=True)
    dst = os.path.join(INDEX_VOICES,
                       name if name.lower().endswith(".wav") else name + ".wav")
    shutil.copy2(src, dst)
    return set_voice(dst)


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if a[0] == "list":
        for v in list_voices():
            print(v)
        return 0
    if a[0] == "get":
        print(current())
        return 0
    if a[0] == "set" and len(a) >= 2:
        try:
            print("已切换音色: %s" % set_voice(a[1]))
        except Exception as e:
            print("切换失败: %s" % e)
            return 1
        return 0
    if a[0] == "save" and len(a) >= 2:
        try:
            _n = a[2] if len(a) >= 3 else os.path.splitext(
                os.path.basename(a[1]))[0]
            print("已保存并切换: %s" % save_user_voice(a[1], _n))
        except Exception as e:
            print("保存失败: %s" % e)
            return 1
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
