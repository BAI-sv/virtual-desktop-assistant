"""Fairy 视觉引擎启动器：llama-server（Vulkan）跑 Qwen3-VL-8B，端口 8086。

被 fairy.py 的 vl 组件调用；8086 已在监听时自动跳过。
底层是 llama.cpp Vulkan 后端（AMD 9070 XT 实测 GPU 加速，看图 0.6 秒）。
模型文件：F 盘 ComfyUI 模型库（Qwen3-VL-8B Q4_K_M + mmproj Q8_0）。
"""
import os
import socket
import subprocess
import sys

# ★ 换机器可跑：用环境变量 VL_LLAMA_SERVER / VL_MODEL / VL_MMPROJ 覆盖，或改成你自己的路径。
LLAMA = os.environ.get("VL_LLAMA_SERVER", r"<llama.cpp目录>\llama-server.exe")
MODEL = os.environ.get("VL_MODEL", r"<你的模型目录>\Qwen3-VL-8B-Instruct-abliterated.Q4_K_M.gguf")
MMPROJ = os.environ.get("VL_MMPROJ", r"<你的模型目录>\Qwen3-VL-8B-Instruct-abliterated.mmproj-Q8_0.gguf")
PORT = 8086
LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fairy_vl.log")


def alive():
    try:
        socket.create_connection(("127.0.0.1", PORT), 1.0).close()
        return True
    except Exception:
        return False


def main():
    if alive():
        print("[vl] %s 已在监听，跳过" % PORT)
        return 0
    if not os.path.exists(LLAMA):
        print("[vl] 缺 llama-server: %s" % LLAMA)
        return 2
    if not os.path.exists(MODEL):
        print("[vl] 缺视觉模型: %s" % MODEL)
        return 2
    try:
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW —— 三重压制，
        # 确保 llama-server（控制台程序）启动也不弹任何黑窗。
        DETACHED = 0x00000008 | 0x00000200 | 0x08000000
        subprocess.Popen(
            [LLAMA, "-m", MODEL, "--mmproj", MMPROJ,
             "--port", str(PORT), "--host", "127.0.0.1",
             "--alias", "qwen3-vl-8b", "-ngl", "99", "--ctx-size", "8192"],
            creationflags=DETACHED,
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
