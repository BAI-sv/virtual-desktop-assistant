# -*- coding: utf-8 -*-
"""
voice_cache.py —— Fairy 克隆音色【预合成缓存】

【为什么需要它】
    用户要求：所有语音输出都必须是【游戏里 Fairy 的音色】。
    现状痛点：IndexTTS(:9881) 一停，球就退回 Edge 通用中文音色（用户讨厌的那个）。
    对策：把球的【全部固定话术】用 IndexTTS + 游戏 Fairy 参考音【预先合成好】缓存起来。
        → 球查缓存命中就直接播，【零延迟、不依赖 IndexTTS 在线】，音色永远是 Fairy。
        → 只有【动态文本】（含具体数字/文件名的那种）才需要实时调 IndexTTS。

【用法】
    py -3.12 D:\\FAIRY\\tools\\voice_cache.py --collect     只盘清单，不合成（先看有多少条）
    py -3.12 D:\\FAIRY\\tools\\voice_cache.py --build       合成全部（可断点续跑，已存在的跳过）
    py -3.12 D:\\FAIRY\\tools\\voice_cache.py --build --force  强制重合成
    py -3.12 D:\\FAIRY\\tools\\voice_cache.py --build-one 文本
    py -3.12 D:\\FAIRY\\tools\\voice_cache.py --status      统计
    py -3.12 D:\\FAIRY\\tools\\voice_cache.py --verify      抽 10 条校验
    py -3.12 D:\\FAIRY\\tools\\voice_cache.py --clean       清理零字节/损坏产物

【给球调用的接口】
    import voice_cache
    p = voice_cache.lookup("主人请注意")     # 命中返回 wav 路径，未命中返回 None
    voice_cache.refresh()                    # 索引有变化时重载（球每次查前调一次，很轻）

【缓存格式】
    D:\\FAIRY\\voice\\clone_cache\\<sha1(文本 utf-8) 前 16 位>.wav
    D:\\FAIRY\\voice\\clone_cache\\index.json
        { "<hash>": {"t": 文本, "f": 文件名, "d": 时长秒, "ts": 生成时间戳} }
    ★ 索引与音频都是【原子写】（先写 .tmp 再 os.replace）
    ★ 2026-10-10多线程登记已加锁（_INDEX_LOCK，可重入），
      tmp 文件名带线程 ident + 随机后缀，不再撞名。

【IndexTTS 接口（已实测）】
    GET  :9881/health  -> {"ok":true,"device":"cuda:0","api":"infer_v2_5","loaded":true}
    POST :9881/tts     {"text":..,"ref_audio":..,"lang":"zh","emo_alpha":1.0} -> WAV 二进制
"""
import argparse
import glob
import hashlib
import io
import json
import os
import random
import re
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
import fairy_root

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
VOICE_DIR = os.path.join(ROOT, "voice")
CACHE_DIR = os.path.join(VOICE_DIR, "clone_cache")
INDEX_FILE = os.path.join(CACHE_DIR, "index.json")
POOLS_FILE = os.path.join(VOICE_DIR, "line_pools.json")
REF_AUDIO = os.path.join(VOICE_DIR, "index_voices", "fairy.wav")
TTS_URL = "http://127.0.0.1:9881/tts"
HEALTH_URL = "http://127.0.0.1:9881/health"

# ★ 缓存文件名前缀。为什么必须带前缀（2026-10-09）：
#   voice_audit.py 用正则 `播放 ([0-9a-f]{16})\.wav` 识别【游戏原声】。
#   如果缓存文件叫 `<16位hash>.wav`，它【会被错算成游戏原声】，
#   审计就分不出「原声 vs 克隆缓存」了。加前缀 cache_ 后，
#   `播放 cache_b874….wav` 不匹配该正则 -> 可单独统计。
FILE_PREFIX = "cache_"

# --------------------------------------------------------------------------
# ★★【2026-10-09 关键发现】只缓存【语料库里没有的】话术
#
#   球的播放顺序是（fairy_ball.py 的 _worker）：
#       ① corpus.find(text) 命中 -> 直接播【游戏原声】，continue
#       ② 没命中才查克隆缓存
#   而 LINE_POOLS 的 380 句 + GAME_LINE 的工具台词【全部是语料库里的原声台词】
#   -> 它们在【第①步就播成游戏原声了，永远走不到缓存】。
#   给它们做缓存在纯属浪费（原声本来就是最好的，克隆反而更差）。
#
#   实测代价：435 条 × 约 28 秒/条 ≈ 3.4 小时（而真正需要的只有约 60 条）。
#   -> 所以 all_phrases() 默认【剔除语料库能直出的那些】。
# --------------------------------------------------------------------------
VOICE_INDEX = os.path.join(VOICE_DIR, "voice_index.json")
# 与 fairy_ball.py 的 _PUNCT 逐字一致（归一化口径必须相同，否则过滤会漏）
_PUNCT = ("\u2026\u3002\uff0c\uff01\uff1f\u3001\uff1b\uff1a"
          "\u201c\u201d\u2018\u2019\u300a\u300b\u3010\u3011"
          "()[]{}<>!?,.;:\"'" + "\u3000 \t\n")


def _norm(s):
    """归一化：去掉标点和空白，只留文字（复刻 fairy_ball.py 的 _norm）。"""
    return "".join(ch for ch in str(s or "") if ch not in _PUNCT).strip()


def corpus_keys():
    """语料库里已有的（归一化）文本 —— 这些球会走原声直出，不需要克隆缓存。"""
    try:
        with open(VOICE_INDEX, encoding="utf-8") as f:
            j = json.load(f)
    except Exception as e:
        print("[cache] 读语料库失败（将不做过滤）: %s: %s" % (type(e).__name__, e), flush=True)
        return None
    out = set()
    for e in (j.get("lines") or []):
        for k in ("t", "r"):
            v = _norm(e.get(k))
            if v:
                out.add(v)
    return out


BALL_FILE = os.path.join(HERE, "fairy_ball.py")


def ball_plays_original():
    """★ 读球的 PLAY_GAME_ORIGINAL 开关（读不到就当 True）。

    【为什么必须读它，而不是写死】
      用户 2026-10-09 提了「先取消和游戏的关联」，球里加了开关：
          PLAY_GAME_ORIGINAL = False
          hit = self.corpus.find(text) if PLAY_GAME_ORIGINAL else None
      一旦它为 False，【原声直出这层就没了】—— 语料库里那 382 句也会掉到缓存这层，
      于是【它们必须缓存】。我当初按"球会走原声直出"的旧前提，把 435 条砍成 53 条做，
      前提一变就全错了。所以改成【自动跟随球的开关】，开关一动这边自动跟着变。
    """
    try:
        with open(BALL_FILE, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r"\s*PLAY_GAME_ORIGINAL\s*=\s*(True|False)", line)
                if m:
                    return m.group(1) == "True"
        print("[cache] ⚠ 球里没找到 PLAY_GAME_ORIGINAL，按 True（球会播原声）处理", flush=True)
    except Exception as e:
        print("[cache] ⚠ 读球开关失败（按 True 处理）: %s: %s" % (type(e).__name__, e), flush=True)
    return True


def effective_routes():
    """球的【实际生效】播放层（按 fakiry_ball.py 的开关算出来，用于审计/报告）。"""
    orig = ball_plays_original()
    return {
        "play_game_original": orig,
        "routes": (["原声直出"] if orig else []) + ["克隆缓存", "实时克隆"]
                  + (["原声兜底"] if orig else []) + ["Edge兜底"],
        "disabled": ([] if orig else ["原声直出", "原声兜底"]),
    }

# 盘清单时要扫的源文件（球的固定话术都在这些里面）
SCAN_FILES = [
    os.path.join(HERE, "fairy_ball.py"),
    os.path.join(HERE, "fairy_device.py"),
    os.path.join(HERE, "fairy_logic.py"),
    os.path.join(HERE, "briefing.py"),
    os.path.join(HERE, "work_advisor.py"),
    os.path.join(HERE, "balance_watch.py"),
]

# 明显不是"要说出口的话"的过滤词
_BAD_MARK = ("%s", "%d", "%.", "{", "}", "\\", "http", "utf-8", "OK", "Traceback",
             "open(", "print(", "def ", "import ", "sha1", "stdin", "stdout")


# --------------------------------------------------------------------------- 工具
def sha_of(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def wav_duration(path):
    """只读 WAV 头算时长，不解码全部（快）。"""
    try:
        with open(path, "rb") as f:
            head = f.read(64)
            if len(head) < 44 or head[:4] != b"RIFF":
                return 0.0
            # 找 fmt 块拿采样率/声道/位深
            f.seek(12)
            rate = ch = bits = 0
            data_size = 0
            while True:
                hdr = f.read(8)
                if len(hdr) < 8:
                    break
                cid, csz = struct.unpack("<4sI", hdr)
                if cid == b"fmt ":
                    b = f.read(csz)
                    # ★ WAV fmt 块布局（16 字节，PCM）：
                    #     wFormatTag(H2) 声道(H2) 采样率(I4) 字节率(I4) 块对齐(H2) 位深(H2)
                    #   —— 2026-10-09 我在这里连栽两次，记录一下免得再犯：
                    #     第一版 "<HHIIH"   : 漏了最后的位深字段，且少读字节
                    #     第二版 "<HIIHH"   : 完全错位（把 wFormatTag 当成声道），时长算成 0
                    #   实测样本 bc: 0100 0100 22560000 44ac0000 0200 1000
                    #                 =1(PCM) 1ch 22050Hz 44100B/s 2 16bit ✓
                    (_fmt, ch, rate, _byterate, _align, bits) = struct.unpack("<HHIIHH", b[:16])
                elif cid == b"data":
                    data_size = csz
                    break
                else:
                    f.seek(csz, 1)
            if rate and ch and bits:
                return data_size / float(rate * ch * bits / 8)
    except Exception:
        pass
    return 0.0


def _atomic_write(path, data, binary=True):
    """原子写。

    ★ 2026-10-10临时文件名必须【唯一】。
      原来固定叫 <path>.tmp —— 球的三个线程（worker / fairy-prefetch / fairy-warm）
      同时登记就会撞同一个 tmp：互相覆盖，严重的还会把 JSON 写坏
      （实测：12 线程 x 12 条 x 5 轮，每轮只活下来 1 条，共丢 55/60）。
      现在带线程 ident + 随机后缀，撞名概率可忽略。
    """
    tmp = "%s.tmp.%d.%08x" % (path, threading.get_ident(), random.getrandbits(32))
    mode = "wb" if binary else "w"
    kw = {} if binary else {"encoding": "utf-8"}
    try:
        with open(tmp, mode, **kw) as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:
        # 失败别留垃圾 tmp（否则下次 --clean 会捡到一堆没人认领的文件）
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise


def load_index():
    """读索引。

    ★ 2026-10-10原来 except 一律 return {} ——
      索引一旦被并发写坏，用户那 473 条（约 75MB）会【静默全部看不见】，
      而且下一次 save_index 就把 {} 覆盖回去 = 真丢。
      现在：坏文件先【备份】再返回空，并且【大声报警】。
    """
    try:
        with open(INDEX_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        bak = ""
        try:
            if os.path.exists(INDEX_FILE):
                bak = "%s.bad.%s" % (INDEX_FILE, time.strftime("%Y%m%d_%H%M%S"))
                import shutil
                shutil.copy2(INDEX_FILE, bak)
        except Exception:
            bak = ""
        print("[cache] ★★ 索引解析失败(%s: %s)，已备份到 %s；本次按空索引继续 —— "
              "音频文件还在 %s，别重新合成整批，先看看能不能修回来"
              % (type(e).__name__, str(e)[:120], bak or "(备份没成功)", CACHE_DIR), flush=True)
        return {}


def save_index(idx):
    os.makedirs(CACHE_DIR, exist_ok=True)
    _atomic_write(INDEX_FILE, json.dumps(idx, ensure_ascii=False, indent=1), binary=False)


# ★ 2026-10-10索引读改写锁。
#   可重入（RLock）：register() 会在锁内调 refresh()，refresh() 自己也要拿锁。
#   ★ 进程内有效 —— 球是单进程三线程，正好覆盖；跨进程（命令行 --build 撞上球）
#     靠 _atomic_write 的唯一 tmp 名 + os.replace 保证不会写坏文件。
_INDEX_LOCK = threading.RLock()


# --------------------------------------------------------------------------- 盘清单
def _looks_speakable(s):
    """宽松判据（只给旧调用用）。新流程用 _reject_reason 带分类。"""
    return _reject_reason(s) is None


# ★ 判据：这句话会被【念出来】吗？会 -> 收；只是 print/注释/异常/路径 -> 排除
#   —— 2026-10-09 实测教训：用正则抓中文字面量会混进大量日志/文档串，
#      755 条里大半是噪音，合成它们纯属白烧 GPU（755 条 x 4 秒 ≈ 50 分钟）。
_LOG_MARK = ("[fairy]", "[advisor]", "[mic]", "[cache]", "[voice]", "[goal]",
             "Traceback", "Errno", "错误:", "失败:", "Exception", "Error:",
             "ModuleNotFound", "FileNotFound", "PermissionError", "warning")
_PLACE_MARK = ("%s", "%d", "%f", "%(", "%r", "%x", "%X", "%g",
               # ★ 2026-10-09 补：原来漏了 %.Nf 这种精度写法，
               #   结果「紧急提醒：API 余额只剩 %.2f 元了…」被当成成品话术收了进来
               "%.", "% ", "{", "}",
               "format(", "f\"", "f'", "\\.", "\\n")


def _reject_reason(s):
    """返回 None 表示收；否则返回被排除的类别名（用于审计统计）"""
    s = s.strip()
    if not s:
        return "empty"
    if len(s) < 3:
        return "too_short"
    if len(s) > 60:
        return "too_long"
    if s[0] in "[（(":
        return "bad_prefix"
    if "\n" in s or "\t" in s:
        return "multiline"
    if not re.search(r"[\u4e00-\u9fff]", s):
        return "no_chinese"
    low = s.lower()
    for m in _LOG_MARK:
        if m in s or m.lower() in low:
            return "log_line"
    for m in _PLACE_MARK:
        if m in s:
            return "has_placeholder"
    if s.startswith(("---", "===", ">>>", "#", "//", "/*")):
        return "comment"
    # ★【2026-10-09 加】排除【分类标签 / 枚举值】—— 它们不是"要说出口的话"：
    #   例：关心型 / 说教型 / 鼓励型（fairy_logic 的语气分类）；ok / done / level2（字段值）
    if re.search(r"^.{1,3}(型|类|级)$", s):
        return "category_label"
    if re.fullmatch(r"[A-Za-z0-9_]+", s):
        return "enum_value"
    return None


# 会被"念出来"的调用名（球/模块里真正送进扬声器的入口）
SPEAK_FNS = {"speak", "say", "_bubble", "bubble", "put", "announce",
             "say_text", "speak_text", "tell", "_say", "voice", "speak_async"}


def _collect_from_py(path):
    """★ 用语法树只取【会念出来】的字面量：
        · 传给 speak/say/_bubble/put 等调用实参里的字符串
        · 函数 return 出去的字符串（模块把"要说的话"返回给调用方）
    排除：文档字符串、print/log 实参、注释（AST 里根本没有注释）
    返回 (speak_args:set, returned:set)
    """
    import ast
    try:
        src = open(path, encoding="utf-8", errors="replace").read()
        tree = ast.parse(src)
    except Exception:
        return set(), set()

    doc_ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                doc_ids.add(id(body[0].value))

    skip_ids = set()
    logfns = {"print", "log", "debug", "info", "warn", "warning", "error",
              "critical", "exception", "write", "stderr"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            nm = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if nm in logfns:
                for a in list(node.args) + [k.value for k in node.keywords]:
                    for sub in ast.walk(a):
                        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                            skip_ids.add(id(sub))

    speak_args, returned = set(), set()

    def _grab(container, node):
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                if id(sub) in doc_ids or id(sub) in skip_ids:
                    continue
                v = sub.value.strip()
                if v:
                    container.add(v)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            nm = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if nm in SPEAK_FNS:
                for a in list(node.args) + [k.value for k in node.keywords]:
                    _grab(speak_args, a)
        elif isinstance(node, ast.Return) and node.value is not None:
            _grab(returned, node.value)
    return speak_args, returned


def collect_strict():
    """严格收集。返回 (final:set, stats:dict, by_source:dict)"""
    stats = {"raw": 0, "kept": 0}
    by_src = {}
    drops = {}
    templates = set()

    def _add(bucket, items, tag):
        for s in items:
            stats["raw"] += 1
            r = _reject_reason(s)
            if r:
                drops[r] = drops.get(r, 0) + 1
                # ★ 含占位符的是"模板"（如 你不在（已空闲 %d 分钟））——
                #   合成带具体数字的废音频没意义，但记下来便于以后回看
                if r == "has_placeholder":
                    templates.add(s.strip())
                continue
            v = s.strip()
            if v in bucket:
                drops["duplicate"] = drops.get("duplicate", 0) + 1
                continue
            bucket.add(v)
            by_src[tag] = by_src.get(tag, 0) + 1

    final = set()

    # ① 台词池（最干净，全收）
    pools = set()
    try:
        with open(POOLS_FILE, encoding="utf-8") as f:
            j = json.load(f)
        for k, v in (j.get("pools") or {}).items():
            pools |= {str(x).strip() for x in v if str(x).strip()}
    except Exception as e:
        print("[cache] 读线池失败: %s: %s" % (type(e).__name__, e), flush=True)
    _add(final, pools, "line_pools")

    # ② 代码里会念出来的字面量
    for p in SCAN_FILES:
        if not os.path.exists(p):
            continue
        sp, rt = _collect_from_py(p)
        tag = "ball" if os.path.basename(p) == "fairy_ball.py" else "modules"
        _add(final, sp, tag + "_speak")
        _add(final, rt, tag + "_return")

    stats["kept"] = len(final)
    stats["dropped"] = drops
    stats["templates"] = sorted(templates)
    return final, stats, by_src


def collect_phrases(verbose=True):
    """兼容旧接口：返回 {类别: set}"""
    src = {}
    pools = set()
    try:
        with open(POOLS_FILE, encoding="utf-8") as f:
            j = json.load(f)
        for k, v in (j.get("pools") or {}).items():
            pools |= {str(x).strip() for x in v if str(x).strip()}
    except Exception:
        pass
    src["line_pools"] = {s for s in pools if _reject_reason(s) is None}
    code = set()
    for p in SCAN_FILES:
        if os.path.exists(p):
            sp, rt = _collect_from_py(p)
            code |= sp | rt
    src["code_literals"] = {s for s in code if _reject_reason(s) is None}
    if verbose:
        total = set()
        for k, v in src.items():
            print("   %-16s %4d 条" % (k, len(v)))
            total |= v
        print("   %-16s %4d 条（去重后）" % ("合计", len(total)))
    return src


def all_phrases(mode=None):
    """返回"需要合成缓存"的话术集合。

    mode:
        None（默认）【自动跟随球的开关 PLAY_GAME_ORIGINAL】：
              球播原声 -> 语料库能直出的不用缓存
              球不播原声 -> 语料库那批【也必须缓存】
        needed   等价于 original（保留旧名字，兼容旧调用）
        all      全部固定话术
        corpus   只留语料库能直出的（审计对照用）
        unlinked 强制"球不播原声"模式
        original 强制"球播原声"模式
    """
    final, _stats, _by = collect_strict()
    ck = corpus_keys()
    if ck is None:
        return final
    if mode is None:
        mode = "original" if ball_plays_original() else "unlinked"
    if mode in ("all",):
        return final
    if mode == "corpus":
        return {s for s in final if _norm(s) in ck}
    if mode in ("needed", "original"):
        return {s for s in final if _norm(s) not in ck}
    return final          # unlinked / 其它：全都要


# --------------------------------------------------------------------------- 合成
def health(timeout=6):
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "why": "%s: %s" % (type(e).__name__, str(e)[:80])}


def synth(text, timeout=90):
    """调 IndexTTS，返回 WAV bytes；失败抛异常。

    ★ timeout 必须有（2026-10-09 事故）：批量跑到一半时某一条请求把服务卡死，
      进程在那儿无限等待 —— 整个批次卡了 11 分钟没有任何进展。
      加上超时后，卡住的条目会被记为失败并继续跑后面的。
    """
    body = json.dumps({"text": text, "ref_audio": REF_AUDIO, "lang": "zh",
                       "emo_alpha": 1.0}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(TTS_URL, data=body,
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        b = r.read()
    if len(b) < 1000 or b[:4] != b"RIFF":
        raise ValueError("返回不是有效 WAV（%d 字节，头=%r）" % (len(b), b[:8]))
    return b


def warmup(rounds=1):
    """首次调用很慢（模型加载）—— 先预热，避免第一条被当成超时"""
    t0 = time.time()
    for i in range(rounds):
        try:
            synth("测试")
            print("[cache] 预热完成 %.1fs" % (time.time() - t0), flush=True)
            return True
        except Exception as e:
            print("[cache] 预热失败(%s): %s" % (type(e).__name__, str(e)[:100]), flush=True)
            return False
    return False


# --------------------------------------------------------------------------- 构建
def build(force=False, limit=None):
    os.makedirs(CACHE_DIR, exist_ok=True)
    h = health()
    if not h.get("ok"):
        print("[cache] ✗ IndexTTS 不可用，不能合成：%s" % h.get("why"), flush=True)
        return 1
    if not h.get("loaded"):
        print("[cache] ⚠ IndexTTS 在线但模型未加载完成，继续等…", flush=True)
    print("[cache] IndexTTS 在线：device=%s api=%s" % (h.get("device"), h.get("api")), flush=True)
    if not os.path.exists(REF_AUDIO):
        print("[cache] ✗ 参考音缺失：%s" % REF_AUDIO, flush=True)
        return 1

    phrases = sorted(all_phrases())
    if limit:
        phrases = phrases[:limit]
    idx = load_index()
    todo = []
    for s in phrases:
        hs = sha_of(s)
        f = os.path.join(CACHE_DIR, FILE_PREFIX + hs + ".wav")
        if not force and hs in idx and os.path.exists(f) and os.path.getsize(f) > 2000:
            continue
        todo.append((s, hs, f))
    print("[cache] 总 %d 条，已缓存 %d 条，本次待合成 %d 条" % (
        len(phrases), len(phrases) - len(todo), len(todo)), flush=True)
    if not todo:
        print("[cache] 没有要合成的，收工", flush=True)
        return 0

    print("[cache] 预热模型（首次调用很慢，请等）…", flush=True)
    warmup()

    ok = fail = 0
    failed = []
    t_all = time.time()
    for n, (s, hs, f) in enumerate(todo, 1):
        got = None
        for attempt in range(3):           # 首次 + 2 次重试
            try:
                got = synth(s)
                break
            except Exception as e:
                if attempt == 2:
                    print("[cache] ✗ 失败(3次) %s | %s" % (s[:24], str(e)[:90]), flush=True)
                    failed.append(s)
                else:
                    time.sleep(1.5)
        if got is None:
            fail += 1
            continue
        _atomic_write(f, got, binary=True)
        idx[hs] = {"t": s, "f": FILE_PREFIX + hs + ".wav", "d": round(wav_duration(f), 3),
                   "ts": int(time.time())}
        ok += 1
        if n % 20 == 0:
            save_index(idx)
            el = time.time() - t_all
            print("[cache] 进度 %d/%d  成功 %d 失败 %d  用时 %.0fs  约 %.2fs/条"
                  % (n, len(todo), ok, fail, el, el / n), flush=True)
    save_index(idx)
    print("[cache] 完成：成功 %d，失败 %d，用时 %.0fs" % (ok, fail, time.time() - t_all), flush=True)
    if failed:
        fp = os.path.join(CACHE_DIR, "failed.txt")
        _atomic_write(fp, "\n".join(failed), binary=False)
        print("[cache] 失败清单 -> %s（%d 条）" % (fp, len(failed)), flush=True)
    return 0


# --------------------------------------------------------------------------- 查询（给球用）
_MEM = {"mtime": 0.0, "size": -1, "idx": {}}


def _index_sig():
    """(mtime, size) —— ★ 只用 mtime 会漏：Windows 的 mtime 精度是 100ns，
    但同一毫秒内多次写入经常拿到【同一个】mtime 字符串 -> refresh 以为"没变"
    -> 内存里一直是旧索引（实测：32 条全在磁盘上，refresh 后内存读到 0 条）。"""
    st = os.stat(INDEX_FILE)
    return (st.st_mtime, st.st_size)


def refresh():
    """索引文件有变化才重载（球每次查前调一次，开销≈一次 stat）"""
    try:
        sig = _index_sig()
    except OSError:
        return
    if sig != (_MEM["mtime"], _MEM["size"]):
        with _INDEX_LOCK:              # ★ #4：和 register() 的"重读+写回"串行，别互相看见半截
            _MEM["idx"] = load_index()
            _MEM["mtime"], _MEM["size"] = sig


def lookup(text):
    """命中返回 wav 绝对路径；未命中 None。★ 很轻：内存字典 + 一次 os.path.exists"""
    if not text:
        return None
    refresh()
    e = _MEM["idx"].get(sha_of(text.strip()))
    if not e:
        return None
    p = os.path.join(CACHE_DIR, e.get("f") or "")
    return p if os.path.exists(p) else None


def register(text, src_path):
    """★ 2026-10-10 新增：把【刚合成出来的一句】登记进预合成缓存（球在合成后顺手调）。

    【为什么要它】开机播报里"现在天气/未来三天/穿衣建议"每天都变，永远命不中缓存，
    每次开机都要**实时合成 3~4 秒**；用户抱怨的"间隔太长"有一半出在这里。
    合成完顺手存一份，第二天同一句（比如"现在室外 30 度，体感 28 度…"重复出现时）
    就能秒出。存不下或登记失败都**不影响播放**，所以这里只报错不抛。
    返回入库后的路径；没入库（太小/读不到）返回 None。
    """
    try:
        if not text or not src_path or not os.path.exists(src_path):
            return None
        if os.path.getsize(src_path) < 2000:          # 太短的多半是坏产物，不污染索引
            return None
        ext = ".mp3" if str(src_path).lower().endswith(".mp3") else ".wav"
        hs = sha_of(text.strip())
        name = FILE_PREFIX + hs + ext
        dst = os.path.join(CACHE_DIR, name)
        if os.path.exists(dst) and os.path.getsize(dst) > 2000:
            return dst
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(src_path, "rb") as f:
            data = f.read()
        _atomic_write(dst, data, binary=True)
        # ★ 2026-10-10load+save 必须串行：
        #   三个线程（worker / fairy-prefetch / fairy-warm）同时登记时，
        #   各自"读旧索引 -> 改自己那条 -> 整体写回"，后写的会把先写的覆盖掉。
        #   实测 12 线程 x 12 条 x 5 轮：每轮只活下来 1 条，共丢 55/60。
        #   加锁后全部活下来（验证见 reviewfix_verify.py 的并发压测）。
        with _INDEX_LOCK:
            idx = load_index()                # ★ 锁内重读：拿的必须是最新一份
            idx[hs] = {"t": str(text).strip(), "f": name,
                       "d": round(wav_duration(dst), 3) if ext == ".wav" else 0,
                       "ts": int(time.time())}
            save_index(idx)
            refresh()                         # ★ 立刻让本次进程内的 lookup 也能命中
        return dst
    except Exception as e:
        print("[cache] 登记失败(不影响播放): %s: %s" % (type(e).__name__, e), flush=True)
        return None


# --------------------------------------------------------------------------- 其它命令
def cmd_status():
    idx = load_index()
    files = glob.glob(os.path.join(CACHE_DIR, "*.wav"))
    total = sum(os.path.getsize(f) for f in files)
    zero = [f for f in files if os.path.getsize(f) < 2000]
    phrases = all_phrases()
    have = sum(1 for s in phrases if sha_of(s) in idx)
    print("── 克隆缓存状态 ─────────────────────────")
    print("   目录      %s" % CACHE_DIR)
    print("   索引条数  %d" % len(idx))
    print("   音频文件  %d 个，共 %.1f MB" % (len(files), total / 1048576.0))
    if files:
        print("   平均大小  %.1f KB/条" % (total / len(files) / 1024.0))
    print("   异常文件  %d 个（<2KB）" % len(zero))
    print("   话术清单  %d 条，其中已缓存 %d 条（%.0f%%）"
          % (len(phrases), have, 100.0 * have / max(1, len(phrases))))
    print("   参考音    %s %s" % (REF_AUDIO, "在" if os.path.exists(REF_AUDIO) else "缺"))
    h = health()
    print("   IndexTTS  %s" % ("在线 " + str(h.get("device")) if h.get("ok") else "离线 " + str(h.get("why"))))
    if zero:
        print("   ⚠ 零字节/过小的文件（用 --clean 清）：")
        for z in zero[:10]:
            print("      %s  %d B" % (os.path.basename(z), os.path.getsize(z)))
    return 0


def cmd_verify(sample=10):
    import random
    idx = load_index()
    if not idx:
        print("[verify] 索引为空")
        return 1
    keys = list(idx.keys())
    random.shuffle(keys)
    bad = 0
    print("── 抽样校验 %d 条 ─────────────────────" % min(sample, len(keys)))
    for k in keys[:sample]:
        e = idx[k]
        p = os.path.join(CACHE_DIR, e.get("f") or "")
        exists = os.path.exists(p)
        size = os.path.getsize(p) if exists else 0
        dur = wav_duration(p) if exists else 0.0
        okk = exists and size > 2000 and 0.2 <= dur <= 60
        if not okk:
            bad += 1
        print("   %s %-22s %7.1fKB %5.2fs  %s"
              % ("OK " if okk else "BAD", (e.get("t") or "")[:22], size / 1024.0, dur,
                 "「%s」" % (e.get("t") or "")[:30] if not okk else ""))
    print("   结论：%d/%d 正常，%d 异常" % (min(sample, len(keys)) - bad,
                                          min(sample, len(keys)), bad))
    return 0 if bad == 0 else 1


def cmd_clean(dry=False):
    """清理坏记录与孤儿文件。

    ★【2026-10-09 事故后加的保险】我加 cache_ 前缀时忘了同步改孤儿判断，
      结果 54 个文件（含 21 个有效缓存）被一次性全删 ✗。
      现在：如果"要删的数量超过总文件数的一半"，就【拒绝执行】并打印待删清单，
      让人先看一眼 —— 因为这种比例几乎一定是判断逻辑写错了，不是真的都是孤儿。
    """
    idx = load_index()
    files = glob.glob(os.path.join(CACHE_DIR, "*.wav"))
    bad_records = []
    for k in list(idx.keys()):
        p = os.path.join(CACHE_DIR, idx[k].get("f") or "")
        if not os.path.exists(p) or os.path.getsize(p) < 2000:
            bad_records.append((k, p))
    orphan_files = []
    for f in files:
        base = os.path.splitext(os.path.basename(f))[0]
        hs = base[len(FILE_PREFIX):] if base.startswith(FILE_PREFIX) else base
        if hs not in idx:
            orphan_files.append(f)

    will_del = len(orphan_files) + len([1 for k, p in bad_records if os.path.exists(p)])
    print("[clean] 总文件 %d · 孤儿 %d · 坏记录 %d · 将删除 %d"
          % (len(files), len(orphan_files), len(bad_records), will_del))

    def _list_them(names):
        """列出【前 20 + 后 20】文件名 —— 只报数量看不出误判模式。"""
        n = len(names)
        head = names[:20]
        tail = names[-20:] if n > 20 else []
        for x in head:
            print("           %s" % x)
        if tail:
            print("           ...（中间省略 %d 个）..." % max(0, n - len(head) - len(tail)))
            for x in tail:
                print("           %s" % x)

    if dry and files and will_del > max(6, len(files) * 0.5):
        print("[clean] ✗ 拒绝执行：要删的数量(%d) 超过总文件(%d)的一半。" % (will_del, len(files)))
        print("        这几乎一定是【判断逻辑写错了】（上次就是这样把有效缓存全删了）。")
        print("        待删的孤儿文件名（前 20 + 后 20）：")
        _list_them([os.path.basename(f) for f in orphan_files])
        print("        确认无误后加 --clean-force 强制执行。")
        return 3
    if dry:
        print("[clean] 干跑模式，未删除任何文件")
        if orphan_files:
            print("        待删的孤儿文件名（前 20 + 后 20）：")
            _list_them([os.path.basename(f) for f in orphan_files])
        if bad_records:
            print("        坏记录（前 20 + 后 20）：")
            _list_them([os.path.basename(p) if p else k for k, p in bad_records])
        return 0

    removed = 0
    for k, p in bad_records:
        if os.path.exists(p):
            os.remove(p)
        del idx[k]
        removed += 1
    for f in orphan_files:
        os.remove(f)
    with _INDEX_LOCK:                      # ★ #4：读改写串行
        save_index(idx)
    print("[clean] 清掉 %d 条坏记录 + %d 个孤儿文件" % (removed, len(orphan_files)))
    return 0


def cmd_collect():
    print("── 固定话术清单（严格）────────────────")
    final, stats, by_src = collect_strict()
    print("   原始候选 %d 条 -> 最终 %d 条" % (stats["raw"], stats["kept"]))
    print("   来源分类：")
    for k, v in sorted(by_src.items(), key=lambda x: -x[1]):
        print("      %-18s %4d 条" % (k, v))
    os.makedirs(CACHE_DIR, exist_ok=True)
    out = os.path.join(CACHE_DIR, "phrases.txt")
    _atomic_write(out, "\n".join(sorted(final)), binary=False)
    print("   已写出 -> %s" % out)
    return 0


def cmd_audit(sample=30):
    """★ 清单审计报告（合成前先给人看）"""
    import random
    final, stats, by_src = collect_strict()
    print("=" * 62)
    print("  固定话术清单 —— 审计报告")
    print("=" * 62)
    print("  原始候选条数      = %d" % stats["raw"])
    print("  过滤掉合计        = %d" % (stats["raw"] - stats["kept"]))
    drops = stats.get("dropped") or {}
    print("  过滤明细（按原因分类）：")
    name_cn = {
        "log_line": "日志/调试行",
        "has_placeholder": "含格式化占位符(模板,不是成品话术)",
        "too_short": "太短(<3字)",
        "too_long": "太长(>60字)",
        "bad_prefix": "以 [ ( （ 开头",
        "multiline": "含换行/制表",
        "no_chinese": "不含中文",
        "comment": "注释式(--- === # //)",
        "duplicate": "重复",
        "empty": "空串",
        "category_label": "分类标签(关心型/说教型…)",
        "enum_value": "枚举值/字段名(ok/done/level2)",
    }
    for k, v in sorted(drops.items(), key=lambda x: -x[1]):
        print("      %-38s %4d" % (name_cn.get(k, k), v))
    print("  ★ 最终条数        = %d" % stats["kept"])
    print("")
    print("  按来源分类：")
    for k, v in sorted(by_src.items(), key=lambda x: -x[1]):
        print("      %-18s %4d 条" % (k, v))
    # ★ 球的实际播放层 + 需要缓存多少条（关键：读球的 PLAY_GAME_ORIGINAL 自动判断）
    rt = effective_routes()
    print("")
    print("  ★ 球当前的播放层（读 fairy_ball.py 的 PLAY_GAME_ORIGINAL）：")
    print("      PLAY_GAME_ORIGINAL = %s" % rt["play_game_original"])
    print("      生效顺序：%s" % " -> ".join(rt["routes"]))
    if rt["disabled"]:
        print("      ⚠ 已停用：%s" % "、".join(rt["disabled"]))

    ck = corpus_keys()
    need = sorted(final)
    if ck is not None:
        in_c = [s for s in final if _norm(s) in ck]
        if rt["play_game_original"]:
            need = [s for s in final if _norm(s) not in ck]
        else:
            need = sorted(final)
        print("")
        print("      固定话术总数              %4d" % len(final))
        print("      语料库能直出              %4d" % len(in_c))
        print("      ★★ 需要缓存              %4d" % len(need))
        if rt["play_game_original"]:
            print("          -> 球会播原声，语料库那 %d 条跳过（原声比克隆好）" % len(in_c))
        else:
            print("          -> ★ 球不播原声，语料库那 %d 条【也必须缓存】" % len(in_c))
    print("")
    print("  随机抽 %d 条（从【真正需要缓存】的那批里抽）：" % min(sample, len(need)))
    for i, s in enumerate(random.sample(need, min(sample, len(need))), 1):
        print("      %2d. %s" % (i, s))
    print("")
    print("  含占位符被排除的样本（确认判据没误伤成品话术）：")
    seen = 0
    for p in SCAN_FILES:
        if not os.path.exists(p):
            continue
        sp, rt = _collect_from_py(p)
        for s in sorted(sp | rt):
            if "has_placeholder" == _reject_reason(s):
                print("      - %s" % s[:70])
                seen += 1
                if seen >= 5:
                    break
        if seen >= 5:
            break
    os.makedirs(CACHE_DIR, exist_ok=True)
    body = list(sorted(final))
    tmpl = stats.get("templates") or []
    if tmpl:
        body.append("")
        body.append("# ===== 以下为含占位符的模板：不合成，仅留档 =====")
        body += ["#SKIP-template " + t for t in tmpl]
    _atomic_write(os.path.join(CACHE_DIR, "phrases.txt"), "\n".join(body), binary=False)
    print("")
    print("  清单已写出 -> %s（成品 %d 条 + 模板留档 %d 条）"
          % (os.path.join(CACHE_DIR, "phrases.txt"), len(final), len(tmpl)))
    return 0


def cmd_suspect():
    """★ 把"疑似 ASR 转写残次品/语义不通"的话术挑出来【单独列表】。

    【按主代理 2026-10-09 的要求】
      · 只【标记】，不从合成里剔除（它们在球的话术池里，球就可能念到）
      · 输出 phrases_suspect.txt，供以后人工筛
    判据（启发式，宁可多标不要漏标）：
      · 长度异常（>22 字，Fairy 的台词通常很短）
      · 含游戏专有名词/生僻搭配（人工小词表）
      · 含连续 3 个以上拉丁字母（中文台词里出现英文长串通常是转写问题）
      · 含罗马数字后缀（II / III）
    """
    # ★ 判据收紧记录（2026-10-09）：第一版把「以太」「邦布」「鸣徽」等游戏正经术语
    #   也当成残次品，还把 "Fairy愿竭尽全力支持您独立完成" 因为含英文被判异常 —— 误报太多。
    #   现在只留【人工确认为语义不通】的搭配 + 极长行 + 连续数字。
    SUSPECT_WORDS = ("元首", "身材优势", "抓物机", "言如句", "沙牙部", "太底",
                     "多佩冈亚", "法布提", "Random Play")
    final, stats, by = collect_strict()
    rows = []
    for s in sorted(final):
        why = []
        if len(s) > 24:
            why.append("过长(%d字)" % len(s))
        for w in SUSPECT_WORDS:
            if w in s:
                why.append("语义不通:" + w)
                break
        if re.search(r"\d{3,}", s):
            why.append("含多位数")
        if why:
            rows.append((s, "; ".join(why)))
    os.makedirs(CACHE_DIR, exist_ok=True)
    out = os.path.join(CACHE_DIR, "phrases_suspect.txt")
    body = ["# 疑似 ASR 残次品/语义不通（★ 只标记，不从合成里剔除）",
            "# 共 %d 条 / 总 %d 条（%.0f%%）" % (len(rows), len(final),
                                                 100.0 * len(rows) / max(1, len(final))), ""]
    body += ["%-34s | %s" % (s[:34], w) for s, w in rows]
    _atomic_write(out, "\n".join(body), binary=False)
    print("── 疑似残次品清单 ─────────────────────")
    print("   总 %d 条，疑似 %d 条（%.0f%%）" % (len(final), len(rows),
                                                 100.0 * len(rows) / max(1, len(final))))
    print("   已写出 -> %s" % out)
    print("   前 15 条：")
    for s, w in rows[:15]:
        print("      %-30s | %s" % (s[:30], w))
    return 0


def cmd_export(n=3, dest=None):
    """★ 导出 n 条缓存音频供人试听（主代理要求：不要播放）"""
    dest = dest or os.path.join(os.path.dirname(os.path.dirname(HERE)), "logs",
                                "voice_unified")
    # 更稳的默认：和球的日志同目录
    dest = os.path.join(fairy_root.WORK_LOGS, "voice_unified")
    os.makedirs(dest, exist_ok=True)
    idx = load_index()
    items = [e for e in idx.values() if e.get("f")]
    items.sort(key=lambda e: e.get("t") or "")
    made = []
    for i, e in enumerate(items[:n], 1):
        src = os.path.join(CACHE_DIR, e["f"])
        if not os.path.exists(src):
            continue
        dst = os.path.join(dest, "cache_%d.wav" % i)
        with open(src, "rb") as a, open(dst, "wb") as b:
            b.write(a.read())
        made.append((dst, e.get("t"), os.path.getsize(dst)))
    print("── 导出缓存音频（★ 不播放）─────────────")
    for d, t, sz in made:
        print("   %s  %.1f KB  「%s」" % (d, sz / 1024.0, (t or "")[:26]))
    if not made:
        print("   （缓存里还没有文件）")
    return 0


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--build-one")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--clean", action="store_true")
    ap.add_argument("--clean-force", action="store_true", dest="clean_force")
    ap.add_argument("--suspect", action="store_true")
    ap.add_argument("--export", nargs="?", const=3, type=int)
    a = ap.parse_args()
    if a.collect:
        return cmd_collect()
    if a.audit:
        return cmd_audit()
    if a.build:
        return build(force=a.force, limit=a.limit)
    if a.build_one is not None:
        os.makedirs(CACHE_DIR, exist_ok=True)
        s = a.build_one
        try:
            b = synth(s)
        except Exception as e:
            print("[cache] 合成失败: %s: %s" % (type(e).__name__, e))
            return 1
        hs = sha_of(s)
        f = os.path.join(CACHE_DIR, FILE_PREFIX + hs + ".wav")
        _atomic_write(f, b, binary=True)
        idx = load_index()
        idx[hs] = {"t": s, "f": FILE_PREFIX + hs + ".wav",
                   "d": round(wav_duration(f), 3), "ts": int(time.time())}
        with _INDEX_LOCK:                  # ★ #4：读改写串行
            save_index(idx)
        print("[cache] 已合成 %s -> %s（%.1f KB，%.2fs）"
              % (s[:24], f, os.path.getsize(f) / 1024.0, wav_duration(f)))
        return 0
    if a.status:
        return cmd_status()
    if a.verify:
        return cmd_verify()
    if a.clean or a.clean_force:
        return cmd_clean(dry=not a.clean_force)
    if a.suspect:
        return cmd_suspect()
    if a.export is not None:
        return cmd_export(a.export or 3)
    print(__doc__)
    return 0


if __name__ == "__main__":
    sys.exit(main())
