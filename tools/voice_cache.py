# -*- coding: utf-8 -*-
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


FILE_PREFIX = "cache_"


VOICE_INDEX = os.path.join(VOICE_DIR, "voice_index.json")

_PUNCT = ("\u2026\u3002\uff0c\uff01\uff1f\u3001\uff1b\uff1a"
          "\u201c\u201d\u2018\u2019\u300a\u300b\u3010\u3011"
          "()[]{}<>!?,.;:\"'" + "\u3000 \t\n")

def _norm(s):
    return "".join(ch for ch in str(s or "") if ch not in _PUNCT).strip()

def corpus_keys():
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
    orig = ball_plays_original()
    return {
        "play_game_original": orig,
        "routes": (["原声直出"] if orig else []) + ["克隆缓存", "实时克隆"]
                  + (["原声兜底"] if orig else []) + ["Edge兜底"],
        "disabled": ([] if orig else ["原声直出", "原声兜底"]),
    }


SCAN_FILES = [
    os.path.join(HERE, "fairy_ball.py"),
    os.path.join(HERE, "fairy_device.py"),
    os.path.join(HERE, "fairy_logic.py"),
    os.path.join(HERE, "briefing.py"),
    os.path.join(HERE, "work_advisor.py"),
    os.path.join(HERE, "balance_watch.py"),
]


_BAD_MARK = ("%s", "%d", "%.", "{", "}", "\\", "http", "utf-8", "OK", "Traceback",
             "open(", "print(", "def ", "import ", "sha1", "stdin", "stdout")


def sha_of(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]

def wav_duration(path):
    try:
        with open(path, "rb") as f:
            head = f.read(64)
            if len(head) < 44 or head[:4] != b"RIFF":
                return 0.0

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
    tmp = "%s.tmp.%d.%08x" % (path, threading.get_ident(), random.getrandbits(32))
    mode = "wb" if binary else "w"
    kw = {} if binary else {"encoding": "utf-8"}
    try:
        with open(tmp, mode, **kw) as f:
            f.write(data)
        os.replace(tmp, path)
    except Exception:

        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise

def load_index():
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


_INDEX_LOCK = threading.RLock()


def _looks_speakable(s):
    return _reject_reason(s) is None


_LOG_MARK = ("[fairy]", "[advisor]", "[mic]", "[cache]", "[voice]", "[goal]",
             "Traceback", "Errno", "错误:", "失败:", "Exception", "Error:",
             "ModuleNotFound", "FileNotFound", "PermissionError", "warning")
_PLACE_MARK = ("%s", "%d", "%f", "%(", "%r", "%x", "%X", "%g",


               "%.", "% ", "{", "}",
               "format(", "f\"", "f'", "\\.", "\\n")

def _reject_reason(s):
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


    if re.search(r"^.{1,3}(型|类|级)$", s):
        return "category_label"
    if re.fullmatch(r"[A-Za-z0-9_]+", s):
        return "enum_value"
    return None


SPEAK_FNS = {"speak", "say", "_bubble", "bubble", "put", "announce",
             "say_text", "speak_text", "tell", "_say", "voice", "speak_async"}

def _collect_from_py(path):
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


    pools = set()
    try:
        with open(POOLS_FILE, encoding="utf-8") as f:
            j = json.load(f)
        for k, v in (j.get("pools") or {}).items():
            pools |= {str(x).strip() for x in v if str(x).strip()}
    except Exception as e:
        print("[cache] 读线池失败: %s: %s" % (type(e).__name__, e), flush=True)
    _add(final, pools, "line_pools")


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
    return final


def health(timeout=6):
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "why": "%s: %s" % (type(e).__name__, str(e)[:80])}

def synth(text, timeout=90):
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
        for attempt in range(3):
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


_MEM = {"mtime": 0.0, "size": -1, "idx": {}}

def _index_sig():
    st = os.stat(INDEX_FILE)
    return (st.st_mtime, st.st_size)

def refresh():
    try:
        sig = _index_sig()
    except OSError:
        return
    if sig != (_MEM["mtime"], _MEM["size"]):
        with _INDEX_LOCK:
            _MEM["idx"] = load_index()
            _MEM["mtime"], _MEM["size"] = sig

def lookup(text):
    if not text:
        return None
    refresh()
    e = _MEM["idx"].get(sha_of(text.strip()))
    if not e:
        return None
    p = os.path.join(CACHE_DIR, e.get("f") or "")
    return p if os.path.exists(p) else None

def register(text, src_path):
    try:
        if not text or not src_path or not os.path.exists(src_path):
            return None
        if os.path.getsize(src_path) < 2000:
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


        with _INDEX_LOCK:
            idx = load_index()
            idx[hs] = {"t": str(text).strip(), "f": name,
                       "d": round(wav_duration(dst), 3) if ext == ".wav" else 0,
                       "ts": int(time.time())}
            save_index(idx)
            refresh()
        return dst
    except Exception as e:
        print("[cache] 登记失败(不影响播放): %s: %s" % (type(e).__name__, e), flush=True)
        return None


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
    with _INDEX_LOCK:
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
    dest = dest or os.path.join(os.path.dirname(os.path.dirname(HERE)), "logs",
                                "voice_unified")

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
        with _INDEX_LOCK:
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
