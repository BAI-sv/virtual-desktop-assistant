# -*- coding: utf-8 -*-
"""fairy_detach.py -- 让 Fairy 真正【独立于 DSH】：用 WMI 起 fairy.py，脱离 DSH 进程树。

为什么必须这样起（2026-10-10 实测，用户当场发现的问题）：
  · 从 DSH 里（主代理的 pwsh）跑 fairy.py 起组件时，球 / 守护 / 本地服务 / 语音服务
    都成了 DSH 的子进程。用户【一关 DSH】，DSH 会把这棵树一起收走 -> Fairy 全没了。
    实测证据：17:50:16 DSH 重启，ball / guard / host / tts 全死；
    唯一活下来的 8081（fairy_ui）—— 正因为它当初是用 WMI 起的。
  · WMI 起出来的进程，父进程是 WmiPrvSE.exe，不在 DSH 的进程树上 -> 关 DSH 杀不到。

用法（参数原样转给 fairy.py）：
    python fairy_detach.py start        # 按 fairy.json 起全部组件（推荐）
    python fairy_detach.py restart      # 先停后起
    python fairy_detach.py status
★ 只用 pythonw 起内层，绝不弹黑窗（本项目红线）。
★ 这条路径【不产生】Windows 计划任务，也不改注册表。
"""
import os
import subprocess
import sys
from datetime import datetime

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
FAIRY = os.path.join(TOOLS, "fairy.py")
LOG = os.path.join(ROOT, "logs", "fairy_detach.log")
CREATE_NO_WINDOW = 0x08000000        # ★ 不是 0x200


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        print(line)
    except Exception:
        pass


def pythonw_of(exe):
    """有 pythonw.exe 就用它（无控制台 -> 不弹窗、也不会被 conhost 牵连）。"""
    if os.path.basename(exe).lower() == "python.exe":
        c = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.isfile(c):
            return c
    return exe


def main():
    args = sys.argv[1:] or ["status"]
    inner = '"%s" "%s" --no-detach %s' % (pythonw_of(sys.executable), FAIRY, " ".join(args))
    # 单引号包住内层命令行（其中的双引号在 PS 单引号串里是普通字符）
    ps = ("Invoke-CimMethod -ClassName Win32_Process -MethodName Create "
          "-Arguments @{CommandLine='%s'}" % inner.replace("'", "''"))
    log("WMI 起（父进程将是 WmiPrvSE，不在 DSH 树上）: %s" % inner)
    try:
        p = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
                           capture_output=True, text=True, timeout=120,
                           creationflags=CREATE_NO_WINDOW)
    except Exception as e:
        log("!! 失败：%s: %s" % (type(e).__name__, e))
        return 1
    out = ((p.stdout or "") + (p.stderr or "")).strip().replace("\r", "")
    log("返回：%s" % " | ".join(l.strip() for l in out.splitlines() if l.strip())[:300])
    if "ReturnValue" in out and " 0 " in (" " + out.replace("|", " ") + " "):
        return 0
    # 解析不出来就不装懂：原样返回 powershell 的退出码
    return 0 if p.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
