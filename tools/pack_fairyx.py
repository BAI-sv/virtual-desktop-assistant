import os
import shutil


SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(os.path.dirname(SRC), "FairyX-开箱即用")


ROOT_FILES = ["fairy.py", "config.json", "启动FairyX.bat", "install.bat",
              "README_中文使用说明.md"]

TOOLS_KEEP = ["fairy_ball.py", "fairy_ear.py", "fairy_logic.py", "fairy.py",
              "device_adapters.py", "nearby.py", "fairy_extend.py", "fairy_scan.py",
              "fairy_deploy.py", "fairy_plugins.py", "fairy_apps.py", "llama_start.py", "comfy_start.py",
              "indextts_start.py", "gpstts_start.py", "music_info.py", "llama_vl_start.bat"]

EXTRA_DIRS = [("models", "models"), ("voice", "voice"), ("docs", "docs")]
EXTRA_FILES = [("downloads", "OllamaSetup.exe", "OllamaSetup.exe")]

def main():
    if os.path.exists(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)


    for f in ROOT_FILES:
        s = os.path.join(SRC, f)
        if os.path.exists(s):
            shutil.copy2(s, OUT)
            print("复制  %s" % f)


    os.makedirs(os.path.join(OUT, "tools"))
    for f in TOOLS_KEEP:
        s = os.path.join(SRC, "tools", f)
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(OUT, "tools", f))
            print("复制  tools\\%s" % f)


    for sub, dst in EXTRA_DIRS:
        s = os.path.join(SRC, sub)
        if os.path.isdir(s):
            shutil.copytree(s, os.path.join(OUT, dst), ignore=shutil.ignore_patterns(
                "*.pyc", "__pycache__", ".locks", "*.lock"))
            print("复制目录 %s\\" % sub)


    for sub, f, dst in EXTRA_FILES:
        s = os.path.join(SRC, sub, f)
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(OUT, dst))
            print("复制  %s" % f)


    total = 0
    for root, _, files in os.walk(OUT):
        for f in files:
            total += os.path.getsize(os.path.join(root, f))
    print("\n打包完成：%s（%.2f GB）" % (OUT, total / 1024**3))

if __name__ == "__main__":
    main()
