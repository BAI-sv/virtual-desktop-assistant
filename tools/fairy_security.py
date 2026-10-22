"""Fairy 安全自检（★ 2026-10-11 新增）：病毒与漏洞轻量检查。

只做【只读】检查，不修改系统、不扫描用户文件：
  1. Windows Defender 状态与病毒库更新时间
  2. 系统已装补丁数量与最近补丁日期
  3. 防火墙各配置档是否开启
  4. 常见高危端口（445/3389/23/135/139/5900）是否对外监听
  5. 给出逐项结论与建议

用法：
    py fairy_security.py --check          # 完整自检
    py fairy_security.py --check --json   # JSON 输出
"""
import argparse
import json
import socket
import subprocess
import sys
import time

HIGH_RISK_PORTS = [445, 3389, 23, 135, 139, 5900]


def _ps(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=25)
        return (r.stdout or "") + (r.stderr or "")
    except Exception:
        return ""


def check_defender():
    out = _ps(["powershell", "-NoProfile", "-Command",
               "Get-MpComputerStatus | Select-Object AntivirusEnabled,"
               "RealTimeProtectionEnabled,AntivirusSignatureLastUpdated | "
               "ConvertTo-Json"])
    try:
        d = json.loads(out or "{}")
        if isinstance(d, list):
            d = d[0] if d else {}
    except Exception:
        d = {}
    return {
        "ok": bool(d.get("AntivirusEnabled") or d.get("RealTimeProtectionEnabled")),
        "enabled": bool(d.get("AntivirusEnabled") or d.get("RealTimeProtectionEnabled")),
        "realtime": bool(d.get("RealTimeProtectionEnabled")),
        "sig_updated": str(d.get("AntivirusSignatureLastUpdated") or "未知"),
    }


def check_hotfix():
    out = _ps(["powershell", "-NoProfile", "-Command",
               "$h=Get-HotFix; $l=$h|Sort-Object InstalledOn -Descending|Select-Object -First 1;"
               "ConvertTo-Json -Compress @([pscustomobject]@{Count=$h.Count;"
               "Last=$l.InstalledOn.ToString('yyyy-MM-dd')})"])
    try:
        d = json.loads(out or "{}")
        if isinstance(d, list):
            d = d[0] if d else {}
    except Exception:
        d = {}
    return {"count": int(d.get("Count") or 0), "last": str(d.get("Last") or "未知")}


def check_firewall():
    out = _ps(["powershell", "-NoProfile", "-Command",
               "Get-NetFirewallProfile | ForEach-Object "
               "{'{0}={1}' -f $_.Name,$_.Enabled}"])
    profiles = {}
    for line in (out or "").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            profiles[k.strip()] = v.strip().lower() == "true"
    return profiles


def check_ports():
    out = _ps(["netstat", "-ano"])
    found = []
    for line in (out or "").splitlines():
        if "LISTENING" not in line:
            continue
        for p in HIGH_RISK_PORTS:
            if ":%d " % p in line and "127.0.0.1:%d" % p not in line:
                found.append(p)
    return sorted(set(found))


def check_all():
    t0 = time.time()
    result = {"ok": True, "items": [], "time_s": 0.0}
    d = check_defender()
    result["items"].append({
        "name": "病毒防护", "ok": d["ok"],
        "detail": "Defender %s，病毒库更新于 %s"
                  % ("已开启" if d["ok"] else "未开启", d["sig_updated"]),
    })
    if not d["ok"]:
        result["ok"] = False
    h = check_hotfix()
    result["items"].append({
        "name": "系统补丁", "ok": h["count"] > 0,
        "detail": "已装 %d 个补丁，最近补丁 %s" % (h["count"], h["last"]),
    })
    fw = check_firewall()
    fw_on = sum(1 for v in fw.values() if v)
    fw_off = [k for k, v in fw.items() if not v]
    result["items"].append({
        "name": "防火墙", "ok": bool(fw) and fw_off == [],
        "detail": "%d/%d 档开启%s" % (fw_on, len(fw),
                   "（关闭：" + "、".join(fw_off) + "）" if fw_off else ""),
    })
    if fw_off:
        result["ok"] = False
    ports = check_ports()
    result["items"].append({
        "name": "高危端口", "ok": not ports,
        "detail": "未发现对外监听的高危端口" if not ports else
                  "发现对外监听：" + "、".join(str(p) for p in ports),
    })
    if ports:
        result["ok"] = False
    result["time_s"] = round(time.time() - t0, 1)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="执行安全自检")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    a = ap.parse_args()
    if not a.check:
        ap.print_help()
        return 0
    try:
        r = check_all()
        if a.json:
            print(json.dumps(r, ensure_ascii=False))
        else:
            print("安全自检：%s" % ("通过" if r["ok"] else "发现异常"))
            for it in r["items"]:
                print("%s  %s：%s" % ("✓" if it["ok"] else "✗", it["name"], it["detail"]))
            if not r["ok"]:
                print("提示：以上异常项请及时处理（本自检只读，不做任何修改）")
            print("用时 %s 秒" % r["time_s"])
        return 0 if r["ok"] else 2
    except Exception as e:
        if a.json:
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        else:
            print("自检失败: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
