r"""
FairyX 打包器 pack_fairyx.py
把 FairyX 程序 + Ollama 安装包 + whisper 模型 打包到 <FairyX 上级目录>\FairyX-开箱即用\
并生成：install.bat（一键安装）、README_中文使用说明.md、安全风险评估.md
模型本体太大不复制，说明书里写明来源与放置位置（中文路径）。
用法：py -3.12 tools/pack_fairyx.py
"""
import os
import shutil

# FairyX 根目录：由脚本位置推导（tools/ 的上一级）
SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(os.path.dirname(SRC), "FairyX-开箱即用")

# 根目录要打包的文件
# ★ 2026-10-11 体检清理：清单里原挂着 Modelfile-qwen38-27b / Modelfile-qwen35-9b /
#   整合优化方案.md / 安全风险评估.md —— 这几个文件已删除，故从清单移除，
#   免得以后看清单以为是"漏打包"。（打包本来就有 os.path.exists 兜底，不会报错。）
ROOT_FILES = ["fairy.py", "config.json", "启动FairyX.bat", "install.bat",
              "README_中文使用说明.md"]
# tools 里要打包的核心脚本（排除 archive/bak/探针/测试）
TOOLS_KEEP = ["fairy_ball.py", "fairy_ear.py", "fairy_logic.py", "fairy.py",
              "device_adapters.py", "nearby.py", "fairy_extend.py", "fairy_scan.py",
              "fairy_deploy.py", "fairy_plugins.py", "fairy_apps.py", "llama_start.py", "comfy_start.py",
              "indextts_start.py", "gpstts_start.py", "music_info.py", "llama_vl_start.bat"]
# 其它要带的目录
EXTRA_DIRS = [("models", "models"), ("voice", "voice"), ("docs", "docs")]
EXTRA_FILES = [("downloads", "OllamaSetup.exe", "OllamaSetup.exe")]


def main():
    if os.path.exists(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)

    # 根文件
    for f in ROOT_FILES:
        s = os.path.join(SRC, f)
        if os.path.exists(s):
            shutil.copy2(s, OUT)
            print("复制  %s" % f)

    # tools 核心
    os.makedirs(os.path.join(OUT, "tools"))
    for f in TOOLS_KEEP:
        s = os.path.join(SRC, "tools", f)
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(OUT, "tools", f))
            print("复制  tools\\%s" % f)

    # 目录（whisper 模型等）
    for sub, dst in EXTRA_DIRS:
        s = os.path.join(SRC, sub)
        if os.path.isdir(s):
            shutil.copytree(s, os.path.join(OUT, dst), ignore=shutil.ignore_patterns(
                "*.pyc", "__pycache__", ".locks", "*.lock"))
            print("复制目录 %s\\" % sub)

    # Ollama 安装包
    for sub, f, dst in EXTRA_FILES:
        s = os.path.join(SRC, sub, f)
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(OUT, dst))
            print("复制  %s" % f)

    # 统计大小
    total = 0
    for root, _, files in os.walk(OUT):
        for f in files:
            total += os.path.getsize(os.path.join(root, f))
    print("\n打包完成：%s（%.2f GB）" % (OUT, total / 1024**3))


if __name__ == "__main__":
    main()
