# -*- coding: utf-8 -*-
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import fairy_root

CK = os.path.join(fairy_root.INDEXTTS_ROOT, "checkpoints")
REPO = "IndexTeam/IndexTTS-2.5"
REV = "master"
LOGDIR = fairy_root.LOGS
LOG = os.path.join(LOGDIR, "its25_full.txt")
KEEP_OLD = ("config.yaml.from_2.0_repo",)

def log(s):
    print(s, flush=True)
    os.makedirs(LOGDIR, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(s + "\n")

def list_repo():
    u = ("https://modelscope.cn/api/v1/models/%s/repo/files?" % REPO
         + urllib.parse.urlencode({"Revision": REV, "Recursive": "true"}))
    req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
    d = json.load(urllib.request.urlopen(req, timeout=30))
    out = []
    for f in (d.get("Data") or {}).get("Files") or []:
        path = str(f.get("Path") or f.get("Name") or "")
        size = int(f.get("Size") or 0)
        typ = str(f.get("Type") or "")
        if not path or path in KEEP_OLD:
            continue
        if typ.lower() == "tree" or path.endswith("/"):
            continue
        out.append((path, size))
    return out

def fetch(path, size):
    dst = os.path.join(CK, path.replace("/", os.sep))
    os.makedirs(os.path.dirname(dst),exist_ok=True)
    url = ("https://modelscope.cn/api/v1/models/%s/repo?" % REPO
           + urllib.parse.urlencode({"Revision": REV, "FilePath": path}))
    tmp = dst + ".part"
    t0 = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=180) as r, open(tmp, "wb") as f:
        got = 0
        last = 0
        while True:
            buf = r.read(1024 * 512)
            if not buf:
                break
            f.write(buf)
            got += len(buf)
            if got - last >= 100 * 2 ** 20:
                last = got
                log("     %-44s %6.1f/%.1f MB" % (path[-44:], got / 2 ** 20, size / 2 ** 20))
    os.replace(tmp, dst)
    log("   OK   %-46s %7.2f MB  %.0f 秒" % (path, os.path.getsize(dst) / 2 ** 20, time.time() - t0))

def main():
    if os.path.exists(LOG):
        os.remove(LOG)
    log("=== 补齐 IndexTTS 2.5 模型文件 ===")
    log("仓库 %s -> %s" % (REPO, CK))
    files = list_repo()
    log("仓库共 %d 个文件。逐个比对本地尺寸…" % len(files))
    todo, same = [], []
    for path, size in files:
        dst = os.path.join(CK, path.replace("/", os.sep))
        if os.path.exists(dst) and os.path.getsize(dst) == size:
            same.append(path)
        else:
            todo.append((path, size))
    log("已一致 %d 个；需要下载 %d 个，合计 %.2f GB"
        % (len(same), len(todo), sum(s for _, s in todo) / 2 ** 30))
    for p, s in todo:
        log("   待下 %-46s %8.2f MB" % (p, s / 2 ** 20))
    for path, size in todo:
        try:
            fetch(path, size)
        except Exception as e:
            log("   FAIL %-44s %s %s" % (path, type(e).__name__, str(e)[:90]))
    log("\n最终复核：")
    bad = 0
    for path, size in files:
        dst = os.path.join(CK, path.replace("/", os.sep))
        if os.path.exists(dst) and os.path.getsize(dst) == size:
            log("   OK   %-46s %7.2f MB" % (path, size / 2 ** 20))
        else:
            bad += 1
            log("   不符 %-46s 期望 %.2f MB / 实际 %s" % (
                path, size / 2 ** 20,
                ("%.2f MB" % (os.path.getsize(dst) / 2 ** 20)) if os.path.exists(dst) else "缺失"))
    log("FETCH25_DONE 不符=%d" % bad)

if __name__ == "__main__":
    main()
