"""Fairy 文本引擎（27B 快脑）：llama-server（Vulkan）跑 Qwen3.8-27B IQ3_XXS，端口 8081。

被 fairy.py / 球的组合切换调用；8081 已监听时自动跳过。

为什么是这套参数（16 GB 显存，RX 9070 XT，2026-10-21 实测）：
    · IQ3_XXS 权重 9.75 GB，`-ngl 99` 全卡 + 16K 上下文 → 31~37 tok/s（prompt 88 tok/s）
    · 显存吃满 15.7 GB；32K 上下文会 ggml_vulkan device lost，别往上调
    · 对照（走过的弯路）：Q4_K_M 15.66 GB 塞不进 16 GB，只上 40/64 层时 25 层留在 CPU，
      实测仅 1.9 tok/s —— 27B 在 16 GB 上只有"全卡"这一条路

端口 8081 是刻意的：8080 归 Strata，两个不能同时占。
"""
import os
import socket
import subprocess
import sys

_WIN = (os.name == "nt")

# ★ 2026-10-21【跨平台】Linux 版不用另写一个启动器：用环境变量覆盖即可
#     FAIRY_LLM27_SERVER=/opt/llama.cpp/build/bin/llama-server
#     FAIRY_LLM27_MODEL=/models/Qwen3.8-27B-Uncensored-Q4_K_M.gguf
LLAMA = os.environ.get("FAIRY_LLM27_SERVER") or (
    r"D:\NAI-xiao\llama.cpp\vulkan-qwen3.8-27B\llama-b10839-bin-win-vulkan-x64\llama-server.exe"
    if _WIN else "/opt/llama.cpp/build/bin/llama-server")
LLAMA_DIR = os.path.dirname(LLAMA)
MODEL = os.environ.get("FAIRY_LLM27_MODEL") or (
    r"D:\models\LLM\llama.cpp-27B\Qwen3.8-27B-GSQ-RCO-IQ3_XXS-v1.1.gguf"
    if _WIN else "/models/Qwen3.8-27B-GSQ-RCO-IQ3_XXS-v1.1.gguf")
PORT = 8081
ALIAS = "qwen3.8-27b"

# ★ 2026-10-21 实测（IQ3_XXS 9.75 GB，全卡）：
#     ngl 99 + ctx 16384 → 31~37 tok/s，显存 15.7 GB（贴顶，32K 会 device lost）
#     对照：Q4_K_M 只上 40 层 → 1.9 tok/s（CPU 那 25 层是瓶颈）
NGL = int(os.environ.get("FAIRY_LLM27_NGL") or 99)
CTX = int(os.environ.get("FAIRY_LLM27_CTX") or 16384)
UBATCH = 256      # 物理批，显存紧张时别调大
KV = "q4_0"       # KV 量化（V 用 q4_0 必须开 flash-attn）

# Windows 三重压制不弹黑窗；Linux 没有这个概念
DETACHED = (0x00000008 | 0x00000200 | 0x08000000) if _WIN else 0

LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs", "fairy_llm27.log")
LOG = os.path.abspath(LOG)


def alive():
    try:
        socket.create_connection(("127.0.0.1", PORT), 1.0).close()
        return True
    except Exception:
        return False


def main():
    if alive():
        print("[llm27] %s 已在监听，跳过" % PORT)
        return 0
    if not os.path.exists(LLAMA):
        print("[llm27] 缺 llama-server: %s" % LLAMA)
        return 2
    if not os.path.exists(MODEL):
        print("[llm27] 缺模型: %s" % MODEL)
        return 2

    args = [
        LLAMA,
        "-m", MODEL,
        "-c", str(CTX),
        "-b", "2048",
        "-ub", str(UBATCH),
        "-ngl", str(NGL),
        "-t", "8",
        "--flash-attn", "on",
        "--cache-type-k", KV,
        "--cache-type-v", KV,
        "--jinja",
        "--chat-template-file", "chat_template.jinja",
        "--parallel", "1",
        "--fit", "on",
        "--fit-target", "768",
        "--metrics",
        "--host", "127.0.0.1",
        "--port", str(PORT),
        "--alias", ALIAS,
    ]
    try:
        # Windows: DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW —— 不弹黑窗
        # Linux: DETACHED=0，删掉父进程也不会带走子进程（靠 Popen 本身）
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        subprocess.Popen(
            args,
            creationflags=DETACHED,
            stdout=open(LOG, "w", encoding="utf-8"),
            stderr=subprocess.STDOUT,
            cwd=LLAMA_DIR,
            start_new_session=(not _WIN),
        )
        print("[llm27] 27B 已启动（%s，ngl=%d ctx=%d，加载约 1~2 分钟）" % (PORT, NGL, CTX))
        return 0
    except Exception as e:
        print("[llm27] 启动失败: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
