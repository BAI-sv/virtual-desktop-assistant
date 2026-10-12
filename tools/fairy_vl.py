"""Fairy 视觉引擎启动器：llama-server（Vulkan）跑 Qwen3-VL-8B，端口 8085。

被 fairy.py 的 vl 组件调用；8085 已在监听时自动跳过。
底层是 llama.cpp Vulkan 后端（A 卡实测 GPU 加速，看图约 0.6 秒）。
模型文件：Qwen3-VL-8B Q4_K_M + mmproj Q8_0（路径见下方配置）。
"""
import os
import socket
import subprocess
import sys

# 本机路径请在下方配置（或改从 config.json 的 paths 读取）：
LLAMA = r"<llama.cpp 目录>\llama-server.exe"
MODEL = r"<Qwen3-VL-8B 模型 GGUF 路径>"
MMPROJ = r"<Qwen3-VL-8B mmproj 路径>"
PORT = 8085
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fairy_vl.log")


def alive():
    try:
        socket.create_connection(("127.0.0.1", PORT), 1.0).close()
        return True
    except Exception:
        return False


def main():
    if alive():
        print("[vl] 8085 已在监听，跳过")
        return 0
    if not os.path.exists(LLAMA):
        print("[vl] 缺 llama-server: %s" % LLAMA)
        return 2
    if not os.path.exists(MODEL):
        print("[vl] 缺视觉模型: %s" % MODEL)
        return 2
    try:
        DETACHED = 0x00000008 | 0x00000100
        subprocess.Popen(
            [LLAMA, "-m", MODEL, "--mmproj", MMPROJ,
             "--port", str(PORT), "--host", "127.0.0.1",
             "--alias", "qwen3-vl-8b", "-ngl", "99", "--ctx-size", "8192"],
            creationflags=DETACHED | subprocess.CREATE_NEW_PROCESS_GROUP,
            stdout=open(LOG, "w", encoding="utf-8"),
            stderr=subprocess.STDOUT,
            cwd=os.path.dirname(LLAMA))
        print("[vl] 视觉引擎已启动（%s，模型加载约 15 秒）" % PORT)
        return 0
    except Exception as e:
        print("[vl] 启动失败: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
