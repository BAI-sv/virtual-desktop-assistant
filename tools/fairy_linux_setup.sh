#!/usr/bin/env bash
# ============================================================
# FairyX Linux 引擎一键部署（★ 2026-10-11 新增 Linux 适配）
# 适用：Ubuntu 22.04+ / Debian 12+ / Fedora 40+ / Arch
# 推荐：AMD RX 9070 XT（gfx1201）+ ROCm 6.3+（官方实测支持）
# 用法：sudo bash fairy_linux_setup.sh [--with-vllm|--with-llama|--all]
# ============================================================
set -e
MODE="${1:---all}"

echo "==> FairyX Linux 引擎部署开始"

# ---------- 1. 发行版检测 ----------
detect_distro() {
  if command -v apt-get >/dev/null 2>&1; then echo "debian"
  elif command -v dnf >/dev/null 2>&1; then echo "fedora"
  elif command -v pacman >/dev/null 2>&1; then echo "arch"
  else echo "unknown"; fi
}
DISTRO=$(detect_distro)
echo "    发行版: $DISTRO"

# ---------- 2. 显卡检测 ----------
detect_gpu() {
  if command -v nvidia-smi >/dev/null 2>&1; then echo "nvidia"
  elif [ -d /opt/rocm ] || (command -v rocminfo >/dev/null 2>&1); then echo "amd-rocm"
  elif lspci 2>/dev/null | grep -qi "Radeon"; then echo "amd"
  else echo "cpu-only"; fi
}
GPU=$(detect_gpu)
echo "    显卡: $GPU"

# ---------- 3. 基础依赖 ----------
echo "==> 安装基础依赖"
case "$DISTRO" in
  debian)
    apt-get update -y
    apt-get install -y python3 python3-pip python3-venv git curl build-essential cmake
    ;;
  fedora)
    dnf install -y python3 python3-pip git curl gcc g++ cmake
    ;;
  arch)
    pacman -Syu --noconfirm python python-pip git curl base-devel cmake
    ;;
  *)
    echo "!! 未知发行版，请手动安装 python3/git/cmake"
    ;;
esac

# ---------- 4. 引擎安装 ----------
install_ollama() {
  echo "==> 安装 Ollama（简单首选，核显/CPU 都能跑）"
  curl -fsSL https://ollama.com/install.sh | sh || echo "!! Ollama 安装失败（可手动装）"
}

install_vllm() {
  echo "==> 安装 vLLM（Linux 生产级首选）"
  case "$GPU" in
    nvidia)
      pip3 install vllm
      ;;
    amd-rocm|amd)
      # AMD：需要 ROCm 6.3+；pip 装官方 ROCm 版
      pip3 install vllm  # 新版 vllm 自带 ROCm 支持（gfx1200/1201）
      ;;
    *)
      echo "!! vLLM 需要 GPU；核显/纯 CPU 机器请用 Ollama/llama.cpp"
      ;;
  esac
}

install_llamacpp() {
  echo "==> 安装 llama.cpp（Vulkan/CPU 通用，最轻量）"
  if [ ! -d "$HOME/llama.cpp" ]; then
    git clone https://github.com/ggml-org/llama.cpp "$HOME/llama.cpp"
  fi
  cd "$HOME/llama.cpp"
  cmake -B build -DGGML_VULKAN=ON -DGGML_CUDA=OFF 2>/dev/null \
    || cmake -B build -DGGML_VULKAN=OFF
  cmake --build build --config Release -j"$(nproc)"
  echo "    llama-server: $HOME/llama.cpp/build/bin/llama-server"
}

case "$MODE" in
  --with-ollama) install_ollama ;;
  --with-vllm) install_vllm ;;
  --with-llama) install_llamacpp ;;
  --all)
    install_ollama
    install_vllm
    install_llamacpp
    ;;
esac

# ---------- 5. 模型目录与启动示例 ----------
echo ""
echo "==> 部署完成。模型文件放 $HOME/models/（GGUF 或 safetensors）"
echo ""
echo "启动示例（选一个）："
echo "  # vLLM（AMD/N 卡，最快）"
echo "  vllm serve Qwen/Qwen3.5-27B --port 8085 --max-model-len 8192"
echo ""
echo "  # llama-server（视觉模型，Vulkan）"
echo "  $HOME/llama.cpp/build/bin/llama-server -m $HOME/models/qwen3-vl-8b.gguf \\"
echo "      --mmproj $HOME/models/mmproj-qwen3-vl-8b.gguf --port 8085 -ngl 99"
echo ""
echo "  # Ollama（最省事）"
echo "  ollama serve &"
echo ""
echo "Fairy 对接：把 fairy.json 的 ear.engine 保持 llama（8085 OpenAI 兼容），"
echo "或按需改为 ollama。组件差异见 models_deploy.md「八、Linux 适配」表。"
echo "==> 完成"
