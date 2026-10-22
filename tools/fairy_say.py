# -*- coding: utf-8 -*-
"""
fairy_say.py —— Fairy 语音播报小工具（独立可用，不依赖球/DSH）

用法：
    py -3.12 fairy_say.py "要播报的文本"
    py -3.12 fairy_say.py --async "文本"     # 异步播（不阻塞）

链路（★ 2026-10-11 增加）：
    IndexTTS 克隆音色(9881) → 降级 DSH tts-api / Edge TTS → mci 播放。
    供 fairy.py 启动播报 / bootstrap 扫描播报 / guard 事件播报复用。
"""
import ctypes
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fairy_endpoints as ep

_ASYNC = "--async" in sys.argv
if _ASYNC:
    sys.argv.remove("--async")

_MCI = ctypes.windll.winmm.mciSendStringW


def _play(data, alias="fsay"):
    """mci 播放 wav/mp3；_ASYNC 时异步（play 不带 wait）。"""
    tmp = os.path.join(tempfile.gettempdir(), "fairy_say_%d.wav" % os.getpid())
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        _MCI("close %s" % alias, None, 0, 0)
        if _MCI('open "%s" type mpegvideo alias %s' % (tmp, alias), None, 0, 0) == 0:
            _MCI("play %s%s" % (alias, "" if _ASYNC else " wait"), None, 0, 0)
            if not _ASYNC:
                _MCI("close %s" % alias, None, 0, 0)
        return True
    except Exception as e:
        print("[fairy_say] 播放失败: %s" % e)
        return False


def say(text):
    """合成并播放一句话。返回 (ok, route)。"""
    try:
        r = ep.tts_get(text)
    except Exception as e:
        return False, "tts_get 异常: %s" % e
    if r.get("ok") and r.get("data"):
        return _play(r["data"]), r.get("route", "?")
    why = "; ".join((x.get("error") or "?") for x in r.get("tried", [])) or "无音频"
    return False, why


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: py -3.12 fairy_say.py [--async] \"文本\"")
        sys.exit(2)
    ok, why = say(" ".join(sys.argv[1:]))
    print("播报 %s（%s）" % ("成功" if ok else "失败", why))
    sys.exit(0 if ok else 1)
