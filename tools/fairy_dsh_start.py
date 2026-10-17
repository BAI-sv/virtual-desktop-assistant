# -*- coding: utf-8 -*-
"""fairy_dsh_start.py -- DSH 与 Fairy 联动启动：先拉起 Fairy，再启动 DeepSeek Harness。

★ 2026-10-17 用户要求【双向关联】：关 fairy 时 DSH 一起关（fairy_shutdown_all.py），
  启动 DSH 时 fairy 自启动（本脚本）。让两者"要开一起开，要关一起关"。
- 用法：开机自启（HKCU Run 的 FairyDSH）指向本脚本；手动启动 DSH 也建议用它。
- 顺序：先 fairy start（脱机、不阻塞），再 DeepSeek Harness.exe（同样脱机）。
  两个都后台拉起，互不等待；fairy.py start 自带幂等（已在跑的组件会跳过）。
"""
import os
import subprocess
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TOOLS)
FAIRY = os.path.join(TOOLS, "fairy.py")
PY = sys.executable
DSH = r"C:\Users\Administrator\AppData\Local\Programs\DeepSeek Harness\DeepSeek Harness.exe"
CREATE_NO_WINDOW = 0x08000000          # 后台运行，不弹任何窗口


def log(msg):
    try:
        os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
        with open(os.path.join(ROOT, "logs", "dsh_start.log"), "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def main():
    # 1) 先起 Fairy（全体组件：球/耳朵/守护/语音/本地模型，按配置拉起；自带幂等）
    try:
        log("== DSH 联动启动：先起 Fairy ==")
        subprocess.Popen([PY, FAIRY, "start"], cwd=TOOLS,
                         creationflags=CREATE_NO_WINDOW, close_fds=True)
        log("fairy start 已送出")
    except Exception as e:
        log("fairy start 失败: %s" % type(e).__name__)
    # 2) 再启动 DeepSeek Harness
    try:
        subprocess.Popen([DSH], creationflags=CREATE_NO_WINDOW, close_fds=True)
        log("DeepSeek Harness 已启动")
    except Exception as e:
        log("DSH 启动失败: %s" % type(e).__name__)


if __name__ == "__main__":
    main()
