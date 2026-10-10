# -*- coding: utf-8 -*-
import json
import os
import subprocess
import sys
import time
import fairy_root


CREATE_NO_WINDOW = fairy_root.CREATE_NO_WINDOW

STATE = fairy_root.log("hw_watch.json")
HIST = fairy_root.log("hw_watch_history.jsonl")
DSH = 19387

def ps(cmd, t=90):
    try:


        r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, timeout=t,
                           creationflags=CREATE_NO_WINDOW)
        return (r.stdout or b"").decode("gbk", "replace")
    except Exception:
        return ""

def collect():
    out = {"time": time.strftime("%Y-%m-%d %H:%M:%S"),
           "whea_total": None, "whea_newest": "", "whea_items": [],
           "boot6008_total": None, "boot6008_newest": "",
           "kernelpower41": None}


    t = ps('''$s=(Get-Date).AddDays(-400)
$e=Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-WHEA-Logger';StartTime=$s} -ErrorAction SilentlyContinue
if ($e) {
  "TOTAL=" + $e.Count
  foreach ($x in ($e | Sort-Object TimeCreated)) {
    $m = ($x.Message -replace "`r`n"," ") -replace "\\s+"," "
    $bank = ""
    if ($m -match 'Bank[^0-9]*([0-9]+)') { $bank = $matches[1] }
    "ITEM=" + $x.TimeCreated.ToString("yyyy-MM-dd HH:mm:ss") + "|Id" + $x.Id + "|Bank" + $bank + "|" + $m.Substring(0,[Math]::Min(120,$m.Length))
  }
} else { "TOTAL=0" }''', t=180)
    items = []
    for line in t.splitlines():
        line = line.strip()
        if line.startswith("TOTAL="):
            try:
                out["whea_total"] = int(line.split("=", 1)[1])
            except Exception:
                pass
        elif line.startswith("ITEM="):
            parts = line[5:].split("|", 3)
            if parts:
                items.append({"when": parts[0] if len(parts) > 0 else "",
                              "id": parts[1] if len(parts) > 1 else "",
                              "bank": parts[2] if len(parts) > 2 else "",
                              "msg": parts[3] if len(parts) > 3 else ""})
    out["whea_items"] = items[-5:]
    if items:
        out["whea_newest"] = items[-1]["when"]


    t2 = ps('''$s=(Get-Date).AddDays(-90)
$e=Get-WinEvent -FilterHashtable @{LogName='System';Id=6008;StartTime=$s} -ErrorAction SilentlyContinue
if ($e) { "TOTAL=" + $e.Count; "NEWEST=" + ($e | Sort-Object TimeCreated | Select-Object -Last 1).TimeCreated.ToString("yyyy-MM-dd HH:mm:ss") } else { "TOTAL=0" }''')
    for line in t2.splitlines():
        line = line.strip()
        if line.startswith("TOTAL="):
            try:
                out["boot6008_total"] = int(line.split("=", 1)[1])
            except Exception:
                pass
        elif line.startswith("NEWEST="):
            out["boot6008_newest"] = line.split("=", 1)[1]


    t3 = ps('''$s=(Get-Date).AddDays(-90)
"TOTAL=" + (@(Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-Kernel-Power';Id=41,42;StartTime=$s} -ErrorAction SilentlyContinue)).Count''')
    for line in t3.splitlines():
        if line.strip().startswith("TOTAL="):
            try:
                out["kernelpower41"] = int(line.strip().split("=", 1)[1])
            except Exception:
                pass
    return out

def main():
    cur = collect()
    prev = None
    try:
        if os.path.exists(STATE):
            prev = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        prev = None

    def delta(key):
        a, b = cur.get(key), (prev or {}).get(key)
        if a is None:
            return None
        if b is None:
            return None
        return a - b

    dw = delta("whea_total")
    db = delta("boot6008_total")
    cur["delta_whea"] = dw
    cur["delta_6008"] = db
    cur["prev_time"] = (prev or {}).get("time")


    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        tmp = STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=1)
        os.replace(tmp, STATE)
        with open(HIST, "a", encoding="utf-8") as f:
            f.write(json.dumps({k: cur[k] for k in
                                ("time", "whea_total", "boot6008_total", "kernelpower41",
                                 "delta_whea", "delta_6008")}, ensure_ascii=False) + "\n")
    except Exception:
        pass

    if "--json" in sys.argv:
        print(json.dumps(cur, ensure_ascii=False, indent=1))
        return 0


    print("CPU/硬件稳定性  %s" % cur["time"])
    if cur["whea_total"] is None:
        print("  WHEA：取不到（可能权限不足）")
    else:
        tag = ""
        if dw is None:
            tag = "（首次记录，无对比）"
        elif dw > 0:
            tag = "⚠ 新增 %d 条！" % dw
        else:
            tag = "✓ 无新增"
        print("  WHEA 硬件错误总数：%d  %s" % (cur["whea_total"], tag))
        if cur["whea_newest"]:
            print("     最近一条：%s" % cur["whea_newest"])
        for it in cur["whea_items"][-3:]:
            print("     %s %s %s" % (it["when"], it["bank"], it["msg"][:80]))
    if cur["boot6008_total"] is not None:
        tag = ""
        if db is None:
            tag = "（首次记录）"
        elif db > 0:
            tag = "⚠ 新增 %d 次！" % db
        else:
            tag = "✓ 无新增"
        print("  意外关机 6008（90天）：%d  %s" % (cur["boot6008_total"], tag))
    if cur["kernelpower41"] is not None:
        print("  异常断电 Kernel-Power 41/42：%d  %s" % (cur["kernelpower41"], "✓" if cur["kernelpower41"] == 0 else "⚠"))


    if dw and dw > 0:
        print("\n  ★ 结论：又出现了新的 CPU 缓存错误。")
        print("    若你已在 BIOS 关掉 XMP / Kombo Strike / Curve Optimizer，说明问题可能不在这些设置 ——")
        print("    下一步该跑 MemTest86 排除内存，或考虑 CPU 本体（5800X3D 有退化案例，可走保修）。")
    elif dw == 0 and prev:
        print("\n  ✓ 相比上次没有新增 —— 若你刚改过 BIOS 设置，这是一个好信号，继续观察 1-2 周。")


    if "--speak" in sys.argv and ((dw or 0) > 0 or (db or 0) > 0):
        try:
            import urllib.request
            msg = "注意，硬件监控发现"
            if (dw or 0) > 0:
                msg += "新增 %d 条 CPU 缓存错误。" % dw
            if (db or 0) > 0:
                msg += "新增 %d 次意外关机。" % db
            msg += "建议检查处理器的电压和内存设置。"
            req = urllib.request.Request(
                "http://127.0.0.1:%d/dsh-tts-api/speak" % DSH,
                data=json.dumps({"text": msg}, ensure_ascii=False).encode("utf-8"),
                headers={"content-type": "application/json"})
            j = json.loads(urllib.request.urlopen(req, timeout=90).read().decode("utf-8"))
            u = j.get("url")
            if u:
                full = u if str(u).startswith("http") else ("http://127.0.0.1:%d" % DSH) + u
                audio = urllib.request.urlopen(full, timeout=90).read()
                tmp = fairy_root.log("hw_say.mp3")
                open(tmp, "wb").write(audio)
                import ctypes
                mci = ctypes.windll.winmm.mciSendStringW
                mci("close hwchk", None, 0, None)
                if mci('open "%s" type mpegvideo alias hwchk' % tmp, None, 0, None) == 0:
                    mci("play hwchk wait", None, 0, None)
                    mci("close hwchk", None, 0, None)
        except Exception:
            pass
    return 0

if __name__ == "__main__":
    sys.exit(main())
