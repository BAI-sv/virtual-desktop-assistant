# -*- coding: utf-8 -*-
"""Fairy 本地文本引擎启动器：llama-server（Vulkan）跑 Qwen3.5-9B，端口 8085。

被 fairy.py 的 llm 组件调用；8085 已在监听时自动跳过。
替代已卸载的 Ollama：纯文本 gguf，不带 mmproj（轻，9070 XT 带得动）。
"""
import os
import socket
import subprocess
import sys

LLAMA = r"D:\llama.cpp\vulkan-qwen3.8-27B\llama-b10839-bin-win-vulkan-x64\llama-server.exe"
MODEL = r"D:\ComfyUI\models\LLM\Qwen3.5-9B-Uncensored-HauhauCS-Aggressive-Q4_K_M.gguf"
PORT = 8085
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fairy_llm.log")


def alive():
    try:
        socket.create_connection(("127.0.0.1", PORT), 1.0).close()
        return True
    except Exception:
        return False


def main():
    if alive():
        print("[llm] 8085 已在监听，跳过")
        return 0
    if not os.path.exists(LLAMA):
        print("[llm] 缺 llama-server: %s" % LLAMA)
        return 2
    if not os.path.exists(MODEL):
        print("[llm] 缺文本模型: %s" % MODEL)
        return 2
    try:
        DETACHED = 0x00000008 | 0x00000200 | 0x08000000
        subprocess.Popen(
            [LLAMA, "-m", MODEL,
             "--port", str(PORT), "--host", "127.0.0.1",
             "--alias", "qwen3.5-9b", "-ngl", "99", "--ctx-size", "8192"],
            creationflags=DETACHED,
            stdout=open(LOG, "w", encoding="utf-8"),
            stderr=subprocess.STDOUT,
            cwd=os.path.dirname(LLAMA))
        print("[llm] 本地文本引擎已启动（%s，模型加载约 15 秒）" % PORT)
        return 0
    except Exception as e:
        print("[llm] 启动失败: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
