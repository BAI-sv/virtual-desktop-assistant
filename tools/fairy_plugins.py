import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request


CREATE_NO_WINDOW = 0x08000000

PY = sys.executable or "py -3.12"
OLLAMA = r"http://127.0.0.1:11434/v1/chat/completions"
VISION = r"http://127.0.0.1:8085/v1/chat/completions"


HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.dirname(HERE)

WHISPER_DIR = os.path.join(D, "models", "whisper")
FFMPEG = None
for _cand in (os.path.join(D, "tools", "ffmpeg.exe"),
              os.path.join(D, "runtime", "ffmpeg.exe"),
              shutil.which("ffmpeg") or ""):
    if _cand and os.path.exists(_cand):
        FFMPEG = _cand
        break


def notify(title, msg):
    try:
        ps = ('powershell -NoProfile -Command "New-Object -ComObject WScript.Shell; '
              '[System.Windows.Forms.MessageBox]::Show(\\\"%s\\\", \\\"%s\\\")" '
              % (msg.replace('"', '\\"'), title.replace('"', '\\"')))
        subprocess.Popen(ps, creationflags=0x08000000, shell=True)
    except Exception:
        print("[提醒] %s: %s" % (title, msg))

def brain(text, system=""):
    payload = {
        "model": "qwen3.8-27b",
        "messages": [
            {"role": "system", "content": system or "你是FairyX的实时助手，简洁直接，用中文回复。"},
            {"role": "user", "content": text},
        ],
        "stream": False,
        "think": False,
    }
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/chat",
                                     data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        r = json.loads(urllib.request.urlopen(req, timeout=180).read().decode("utf-8"))
        return (r.get("message") or {}).get("content", "").strip()
    except Exception as e:
        return "[大脑不可用: %s]" % e

def http_get(url, timeout=20, headers=None):
    try:
        req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
        return urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
    except Exception as e:
        return ""


WCODE = {0: "晴", 1: "大部晴朗", 2: "多云", 3: "阴", 45: "雾", 48: "雾凇", 51: "毛毛雨",
         53: "小雨", 55: "中雨", 61: "小雨", 63: "中雨", 65: "大雨", 71: "小雪", 73: "中雪",
         75: "大雪", 80: "阵雨", 81: "强阵雨", 82: "暴雨", 85: "阵雪", 86: "强阵雪",
         95: "雷雨", 96: "雷雨伴冰雹", 99: "强雷雨伴冰雹"}

def weather_check(lat=39.90, lon=116.40, place="北京"):
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s"
           "&current=temperature_2m,weather_code,wind_speed_10m,relative_humidity_2m"
           "&timezone=Asia/Shanghai" % (lat, lon))
    raw = http_get(url)
    if not raw:
        return "天气查询失败（网络不可用）", False
    try:
        c = json.loads(raw)["current"]
        desc = WCODE.get(c.get("weather_code", 0), "未知")
        temp = c.get("temperature_2m", 0)
        wind = c.get("wind_speed_10m", 0)
        hum = c.get("relative_humidity_2m", 0)
        text = "%s当前天气：%s，%s℃，湿度%s%%，风速%s km/h" % (
            place, desc, temp, hum, wind)
        return text, False
    except Exception as e:
        return "天气解析失败: %s" % e, False


def code_lint(path):
    if not os.path.exists(path):
        return "文件不存在: %s" % path
    with open(path, encoding="utf-8", errors="ignore") as f:
        code = f.read()

    issues = []
    if path.endswith(".py"):
        try:
            compile(code, path, "exec")
        except SyntaxError as e:
            issues.append("语法错误 行%s: %s" % (e.lineno, e.msg))

    for i, line in enumerate(code.splitlines(), 1):
        if len(line) > 100:
            issues.append("行%s 过长(%d字符)，建议换行" % (i, len(line)))
    if not issues:
        return "✓ 语法检查通过（%s 行）" % len(code.splitlines())
    detail = brain("下面代码有这些错误：%s。请逐条给出修正建议，简洁：\n%s" % ("；".join(issues), code[:800]),
                   system="你是代码检查助手，只输出修正建议，每条一行。")
    return "发现 %d 个问题：%s\n\n大脑建议：\n%s" % (len(issues), "；".join(issues), detail)


def text_check(text):
    if not text.strip():
        return "没有可检查的文案"
    return brain("检查下面文案的错别字、标点、格式问题，逐条列出并给出修改后版本：\n%s" % text[:1500],
                 system="你是中文文案校对助手，输出：问题清单 + 修改后全文。")


def translate(text):
    if not text.strip():
        return "没有可翻译的内容"
    is_cn = bool(re.search(r"[\u4e00-\u9fff]", text[:200]))
    target = "英文" if is_cn else "中文"
    return brain("把下面内容翻译成%s，只输出译文：\n%s" % (target, text[:1500]),
                 system="你是专业翻译，只输出译文，不要解释。")


def video_subtitle(video_path, out_dir=None):
    if not os.path.exists(video_path):
        return "视频不存在: %s" % video_path
    out_dir = out_dir or os.path.dirname(video_path)
    base = os.path.splitext(os.path.basename(video_path))[0]
    raw_audio = os.path.join(out_dir, base + "_audio.f32")
    srt = os.path.join(out_dir, base + ".srt")

    if FFMPEG:
        r = subprocess.run([FFMPEG, "-y", "-i", video_path, "-vn", "-ac", "1", "-ar", "16000",
                            "-f", "f32le", raw_audio],
                           capture_output=True, text=True, timeout=1800,
                           creationflags=CREATE_NO_WINDOW)
        if r.returncode != 0:
            return "音轨提取失败: %s" % (r.stderr or "")[-300:]
    else:
        return "未找到 ffmpeg，无法提取音轨（需要 %s）" % video_path

    try:
        import numpy as np
    except ImportError:
        return "需要 numpy"
    try:
        audio = np.fromfile(raw_audio, dtype=np.float32)
    except Exception as e:
        return "音频读取失败: %s" % e
    if audio.size == 0:
        return "音频为空（该文件可能没有音轨）"

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return "需要 faster-whisper（pip install faster-whisper）"
    print("  正在离线转写（faster-whisper small），稍候...")
    model = WhisperModel("small", device="auto", compute_type="int8",
                         download_root=WHISPER_DIR)
    segments, info = model.transcribe(audio, language="zh" if base else None, vad_filter=True)
    lines = []
    i = 0
    for seg in segments:
        i += 1
        h, m, s = int(seg.start // 3600), int(seg.start % 3600 // 60), seg.start % 60
        h2, m2, s2 = int(seg.end // 3600), int(seg.end % 3600 // 60), seg.end % 60
        lines.append("%d\n%02d:%02d:%06.3f --> %02d:%02d:%06.3f\n%s\n" % (
            i, h, m, s, h2, m2, s2, seg.text.strip()))
    with open(srt, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(lines))
    try:
        os.remove(raw_audio)
    except OSError:
        pass
    return "字幕已生成：%s（%d 条）" % (srt, len(lines))


def lyrics(song):
    esc = urllib.parse.quote(song)
    raw = http_get("https://music.163.com/api/search/get/web?s=%s&type=1&limit=1" % esc)
    if not raw:
        return "歌词搜索失败（网络不可用）"
    try:
        sid = json.loads(raw)["result"]["songs"][0]["id"]
        name = json.loads(raw)["result"]["songs"][0]["name"]
    except Exception:
        return "没找到歌曲：%s" % song
    raw2 = http_get("https://music.163.com/api/song/lyric?id=%s&lv=1&kv=1&tv=-1" % sid)
    try:
        lrc = json.loads(raw2)["lrc"]["lyric"]
    except Exception:
        return "该歌曲暂无歌词"

    text = re.sub(r"\[\d+:\d+(?:\.\d+)?\]", "", lrc)
    return "《%s》歌词：\n%s" % (name, text.strip()[:1500])


def image_vision(img_path, mode="反推"):
    if not os.path.exists(img_path):
        return "图片不存在: %s" % img_path
    try:
        import requests as req
    except ImportError:
        return "需要 requests"
    with open(img_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    ext = os.path.splitext(img_path)[1].lstrip(".").lower() or "png"
    if mode == "反推":
        user = "为这张图片生成适合 Stable Diffusion 的详细英文提示词（只输出提示词）"
        system = "你是图片反推专家，输出英文文生图提示词，包含主体、构图、光影、风格、细节、质量词。"
    elif mode == "描述":
        user = "详细描述这张图片里有什么（中文）"
        system = "你是FairyX的视觉助手，仔细观察图片，用中文详细描述。"
    elif mode == "翻译":
        user = "把图片里的文字内容识别并翻译成中文"
        system = "你是OCR与翻译助手，输出图片中的文字及中文翻译。"
    else:
        return "模式可选：反推 / 描述 / 翻译"
    try:
        r = req.post(VISION, json={
            "model": "qwen3-vl-8b",
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": [
                             {"type": "text", "text": user},
                             {"type": "image_url", "image_url": {"url": "data:image/%s;base64,%s" % (ext, b64)}}]}],
            "stream": False, "max_tokens": 600,
        }, timeout=180)
        r.raise_for_status()
        return (r.json().get("choices") or [{}])[0].get("message", {}).get("content", "").strip()
    except Exception as e:
        return "视觉服务不可用（需先启动 Qwen3-VL：tools/llama_vl_start.bat）：%s" % e


def record_audio(seconds=12):
    try:
        import sounddevice as sd
        import numpy as np
        import wave
        import io
    except ImportError:
        return None, "需要 sounddevice（pip install sounddevice numpy）"
    fs = 16000
    try:
        rec = sd.rec(int(fs * seconds), samplerate=fs, channels=1, dtype="float32")
        sd.wait()
    except Exception as e:
        return None, "录音失败（请检查麦克风）: %s" % e
    data16 = (rec * 32767).clip(-32768, 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fs)
        w.writeframes(data16.tobytes())
    return buf.getvalue(), None

def song_identify(seconds=12):
    print("  正在录音 %d 秒（请让音乐/歌声进入麦克风）..." % seconds)
    wav, err = record_audio(seconds)
    if err:
        return err
    try:
        from shazamio import Shazam
        import asyncio
    except ImportError:
        return "需要 shazamio：py -3.12 -m pip install shazamio"
    try:
        out = asyncio.run(Shazam().recognize_song(data=wav))

        if isinstance(out, dict):
            matches = out.get("matches") or []
            track = (matches[0].get("track") or {}) if matches else {}
            title = track.get("title")
            artist = track.get("subtitle") or ""
        else:
            track = getattr(out, "track", None)
            title = getattr(track, "title", None)
            artist = getattr(track, "subtitle", "") or ""
    except Exception as e:
        return "识别失败（可能是 Shazam 接口限流，等一分钟再试）：%s" % e
    if not title:
        return "没有识别到歌曲（换个环境音/大点声试试）"
    out_txt = "识别到：%s - %s\n" % (title, artist)
    lrc = lyrics("%s %s" % (title, artist))
    out_txt += lrc if not lrc.startswith("没找到") else "（歌词未找到）"
    return out_txt


def get_now_playing():
    try:
        import asyncio
        from winrt.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as _Mgr)
    except ImportError:
        return "need winrt", None
    try:
        async def _g():
            mgr = await _Mgr.request_async()
            s = mgr.get_current_session()
            if s is None:
                return None
            info = await s.try_get_media_properties_async()
            title = info.title or ""
            artist = (info.artist or "").split(";")[0] if info.artist else ""
            return (title, artist)
        return asyncio.run(_g())
    except Exception:
        return None

def autolyric(interval=5):
    print("自动歌词监听中（检测到播放/切歌自动显示歌词，Ctrl+C 退出）...")
    last = None
    try:
        while True:
            r = get_now_playing()
            if r and r[0]:
                key = "%s - %s" % (r[0], r[1])
                if key != last:
                    last = key
                    print("\n♪ 正在播放：%s" % key)
                    print(lyrics(r[0] if not r[1] else "%s %s" % (r[0], r[1])))
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n已退出自动歌词监听")

def watch(interval=300):
    print("FairyX 实时监控已启动（每 %ds 检查一次，Ctrl+C 停止）" % interval)
    last_weather = None
    last_clip = ""
    while True:

        try:
            w, changed = weather_check()
            if changed or (last_weather and w != last_weather):
                notify("天气提醒", w)
            last_weather = w
        except Exception:
            pass

        try:
            clip = subprocess.run(["powershell", "-NoProfile", "-Command",
                                   "Get-Clipboard -Raw"], capture_output=True,
                                  text=True, timeout=5,
                                  creationflags=CREATE_NO_WINDOW).stdout.strip()
            if clip and clip != last_clip and len(clip) > 4 and len(clip) < 400:
                last_clip = clip
                if re.search(r"[\u4e00-\u9fff]", clip[:100]):

                    if len(re.findall(r"[\u4e00-\u9fff]", clip)) > 30:
                        r = text_check(clip)
                        notify("文案检查", r[:300])
                    elif re.search(r"[a-zA-Z]{6,}", clip):
                        r = translate(clip)
                        notify("翻译", r[:300])
        except Exception:
            pass
        time.sleep(interval)


def remind(expr):
    m = re.match(r"^(\d+)\s*(分钟|秒)后\s*(.+)$", expr)
    m2 = re.match(r"^(\d{1,2}):(\d{2})\s*(.+)$", expr)
    m3 = re.match(r"^(\d+)\s*小时后\s*(.+)$", expr)
    text, wait = None, 0
    if m:
        wait = int(m.group(1)) * (60 if m.group(2) == "分钟" else 1)
        text = m.group(3)
    elif m2:
        now = time.localtime()
        secs = int(m2.group(1)) * 3600 + int(m2.group(2)) * 60
        now_secs = now.tm_hour * 3600 + now.tm_min * 60
        wait = secs - now_secs
        if wait < 0:
            wait += 86400
        text = m2.group(3)
    elif m3:
        wait = int(m3.group(1)) * 3600
        text = m3.group(2)
    if not text:
        return ("格式：--remind \"10分钟后 喝水\" ｜ \"8:30 开会\" ｜ \"1小时后 起来活动\"")
    wait_txt = "%d秒" % wait if wait < 60 else "%d分%d秒" % (wait // 60, wait % 60)
    print("已设置提醒：%s 后 -> %s" % (wait_txt, text))

    def _fire():
        time.sleep(wait)
        try:
            notify("FairyX 提醒", text)
        except Exception:
            print("\n[提醒] %s" % text)
    threading.Thread(target=_fire, daemon=True).start()
    return "提醒已设置（%s 后）" % wait_txt

def sysinfo():
    import platform
    lines = ["===== FairyX 系统健康 ====="]
    lines.append("系统: %s %s" % (platform.system(), platform.release()))
    try:
        import psutil
        lines.append("CPU 使用: %s%%（%d 核）" % (psutil.cpu_percent(interval=1),
                                                psutil.cpu_count(logical=True)))
        vm = psutil.virtual_memory()
        lines.append("内存: 已用 %s / 共 %s（%s%%）" % (
            _fmt(vm.used), _fmt(vm.total), vm.percent))
        for dp in psutil.disk_partitions():
            if "cdrom" in dp.opts or not dp.fstype:
                continue
            try:
                u = psutil.disk_usage(dp.mountpoint)
                lines.append("磁盘 %s: %s / %s（%s%%）" % (
                    dp.mountpoint, _fmt(u.used), _fmt(u.total), u.percent))
            except Exception:
                pass
        lines.append("运行时长: %s" % _fmt_sec(time.time() - psutil.boot_time()))
        bt = psutil.net_io_counters()
        lines.append("网络: 已收 %s / 已发 %s（本次开机）" % (_fmt(bt.bytes_recv), _fmt(bt.bytes_sent)))
    except ImportError:
        lines.append("（未安装 psutil，跳过指标；py -3.12 -m pip install psutil）")
    return "\n".join(lines)

def _fmt(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return "%.1f%s" % (n, u)
        n /= 1024
    return "%.1fPB" % n

def _fmt_sec(s):
    d, r = divmod(int(s), 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    return "%d天%d小时%d分%d秒" % (d, h, m, s)


def main():
    ap = argparse.ArgumentParser(description="FairyX 实时功能插件")
    ap.add_argument("--weather", action="store_true", help="查询当前天气")
    ap.add_argument("--lint", metavar="文件", help="代码纠错（传 .py 文件路径）")
    ap.add_argument("--check", metavar="文案", help="文案格式检查")
    ap.add_argument("--translate", metavar="文本", help="中英互译")
    ap.add_argument("--subtitle", metavar="视频", help="视频离线字幕")
    ap.add_argument("--lyrics", metavar="歌名", help="查歌词")
    ap.add_argument("--image", metavar="图片", help="看图/反推（Qwen3-VL 视觉）")
    ap.add_argument("--mode", metavar="模式", choices=["反推", "描述", "翻译"],
                    default="反推", help="--image 的模式（默认反推）")
    ap.add_argument("--songid", action="store_true", help="听歌识曲（录音12秒识别+歌词）")
    ap.add_argument("--autolyric", action="store_true", help="自动歌词（检测正在播放→歌词）")
    ap.add_argument("--remind", metavar="提醒", help="定时提醒（如 \"10分钟后 喝水\"）")
    ap.add_argument("--sysinfo", action="store_true", help="系统健康一览（CPU/内存/磁盘/网络）")
    ap.add_argument("--watch", action="store_true", help="监控模式")
    a = ap.parse_args()

    if a.watch:
        watch()
    elif a.weather:
        print(weather_check()[0])
    elif a.lint:
        print(code_lint(a.lint))
    elif a.check:
        print(text_check(a.check))
    elif a.translate:
        print(translate(a.translate))
    elif a.subtitle:
        print(video_subtitle(a.subtitle))
    elif a.lyrics:
        print(lyrics(a.lyrics))
    elif a.image:
        print(image_vision(a.image, a.mode))
    elif a.songid:
        print(song_identify())
    elif a.autolyric:
        autolyric()
    elif a.remind:
        print(remind(a.remind))
    elif a.sysinfo:
        print(sysinfo())
    else:
        ap.print_help()

if __name__ == "__main__":
    main()
