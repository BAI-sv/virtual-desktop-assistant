@echo off
title Qwen3-VL Vision Service
cd /d "<INDEXTTS_ROOT>\llama.cpp\vulkan-qwen3.8-27B\llama-b10839-bin-win-vulkan-x64"
set "MODEL_DIR=<改成你的模型目录，例如 D:\models\LLM>"
start "" llama-server.exe -m "%MODEL_DIR%\Huihui-Qwen3-VL-8B-Instruct-abliterated.Q8_0.gguf" --mmproj "%MODEL_DIR%\Huihui-Qwen3-VL-8B-Instruct-abliterated.mmproj-Q8_0.gguf" --host 127.0.0.1 --port 8085 --ctx-size 4096 -ngl 99 --jinja
echo Vision service starting on port 8085...
echo Then use: py -3.12 tools\fairy_plugins.py --image 图片.jpg --mode 反推