# FairyX 模型自动配置清单（Strata 部署）

> 写给：使用人（白明伟）
> 更新：2026-10-11
> 适用引擎：Strata（ROCm/A 卡 HIP）—— 本机 9070 XT 16G + 64G 内存



***

## 〇、设备适配性说明（重要）

**作者测试环境**：



* GPU：**AMD Radeon RX 9070 XT（16G，gfx1201 架构）**

* 内存：DDR4 64GB（32G×2，3600MHz）

* 系统：Windows 桌面版

* 引擎后端：**HIP（ROCm）**，已用 gfx1201 调优表实测校准

**N 卡（CUDA）未测试** ⚠️：



* 以下所有 Strata 配置均在 **AMD/ROCm** 上验证运行，**N 卡没有测试**

* N 卡用户如需使用，预计要改三处（**未验证，请自行测试**）：

1. `backend`：`"hip"` → `"cuda"`

2. `lib_dirs`：ROCm 的 bin 目录 → CUDA 的库目录

3. 删除 `env.STRATA_HIPBLASLT_TUNING`（这是 A 卡专属调优表，N 卡用会报错）

* 专家拆分方案本身与显卡厂商无关，理论上 N 卡可跑，但**未经实测、不保证开箱即用**

**核显 / 无独显机器**：Strata 不支持纯核显；此类机器请改用 Ollama /llama.cpp 跑 27B 以下小模型（见第六节）。



***

## ★ 一、模型组合方案（★ 2026-10-12 新增，控制面板一键切换）

> 操作：球右键 → Fairy 控制面板 → AI 模型 → **组合选择**（展开）→ 点选对应组合。
> 切换后自动：保存配置 → 启动/释放对应引擎 → 语音播报结果。

### 1. 四套预设组合

| 组合 | 大脑模型 | 引擎 | 端口 | 显存/内存 | 适用场景 | 备注 |
|---|---|---|---|---|---|---|
| **日常全能**（推荐） | Qwen3.8-Flash-Next·UD-IQ4_XS（93.7G 整包） | Strata | 8080 | 内存 ≥64G | 写代码、日常问答、智能助手全功能 | 当前默认，质量 91-93%，甜点位 |
| **高精度** | qwen3:30b-a3b（MoE 30B，激活 3B） | Ollama | 11434 | 显存 ~18.5G 按需 | 长推理、疑难问题、方案权衡 | 又快又强，Ollama 已装 |
| **极速** | qwen3:8b（8B Q4） | Ollama | 11434 | 显存 ~6G | 日常直答、翻译、查表等轻任务 | 最快，显存占用最小 |
| **游戏模式** | 无（全部释放） | — | — | 显存 0（全给游戏） | 玩大型游戏前一键清空显存 | 释放 Strata/Ollama/视觉引擎，语音照常 |

### 2. 全任务配套模型（四套组合共用，不随大脑切换）

| 任务 | 模型 | 说明 |
|---|---|---|
| 唤醒词 | sherpa-onnx KWS | 4.8MB，实时 73 倍速，不占资源 |
| 语音识别（STT） | SenseVoice（FunASR） | 中文最准档，边说边出字，替代 whisper |
| 语音合成（TTS） | IndexTTS | 本地合成，音色可克隆，端口 9881 |
| 看图/屏幕理解 | Qwen3-VL-8B Q4_K_M（8085） | 截图问答、界面识别 |
| OCR | PaddleOCR | 印刷体识别准，CPU 可跑 |
| 物体检测 | YOLO11n | 最快最省，实时性好 |
| 听歌识曲 | SongRec | 本地免费 |

### 3. 组合切换注意事项

* **日常全能**：要求内存余量 ≥ 100G（整包 93.7G + 系统）；切过去会自动拉起 Strata（8080）
* **高精度 / 极速**：需要 Ollama 已导入对应模型（`ollama pull qwen3:30b-a3b` / `qwen3:8b`）；Ollama 没跑会自动拉起（11434）
* **游戏模式**：一键释放全部引擎与模型（等同右键"释放全部资源"），显存全给游戏；切回任意组合即可恢复
* 换组合后 Fairy 对话立即走新后端；未加载的模型需先下载（面板会提示）

***

## 二、当前部署（Strata 在线）



| 项      | 值                                               |
| ------ | ----------------------------------------------- |
| 引擎     | E:\Strata\engine\strata.exe（HIP 后端，gfx1201 调优）  |
| 模型     | Qwen3.8-Flash-Next・UD-IQ4\_XS（93.7GB 整包，专家拆分驻留） |
| 配置     | E:\Strata\strata-unsloth-ud-iq4\_xs.json        |
| 模型文件   | D:\Qwen3.8-Flash-Next-GGUF\UD-IQ4\_XS\（3 分卷）    |
| MTP 模块 | D:\Strata-data\mtp\（4B 多 token 预测，Q2\_0）        |
| 端口     | 8080（OpenAI 兼容）                                 |

## 二、全部量化档自动配置（已备好，下载后即用）



| 配置文件名（E:\Strata\）                    | 档位             | 整包体积        | 内存要求        | 质量           | 建议                  |
| ------------------------------------ | -------------- | ----------- | ----------- | ------------ | ------------------- |
| strata-unsloth-ud-iq2\_xs.json       | UD-IQ2\_XS     | 78.9 GB     | ≥90G        | \~88%        | 极限压缩，仅内存不够时用        |
| strata-unsloth-ud-iq3\_xs.json       | UD-IQ3\_XS     | 90.0 GB     | ≥100G       | \~89%        | 比 IQ4 只省 4G 但掉质，不推荐 |
| **strata-unsloth-ud-iq4\_xs.json** ✅ | **UD-IQ4\_XS** | **93.7 GB** | **≥64G 够跑** | **\~91-93%** | **甜点位，当前在用**        |
| strata-unsloth-ud-q4\_k\_xl.json     | UD-Q4\_K\_XL   | 111.3 GB    | ≥128G       | \~93%        | 高质量甜点，大内存机器         |
| strata-unsloth-ud-q5\_k\_m.json      | UD-Q5\_K\_M    | 128.6 GB    | ≥128G+      | \~92%+       | 高保真，内存要大            |
| strata-unsloth-ud-q6\_k.json         | UD-Q6\_K       | 148.9 GB    | ≥160G       | \~95%        | 近无损，仅工作站            |

**通用约定**：



* 各档的 --pack /tokenizer 复用 IQ4\_XS 的（同一模型，分词器通用）

* \--native 指向对应分卷（第 1 卷，Strata 自动读后续分卷）

* \--resident-budget-gib（专家驻留内存预算）已按档位预调：IQ2=32 / IQ3=36 / IQ4=40 / Q4KXL=48 / Q5=52 / Q6=58，可自行改

* **同一时刻只有一个档占 8080**：换档 = 先 `fairy.py stop`（自动关引擎）再启动新档

## 三、模型文件下载路径（modelscope / HuggingFace）

各档模型文件下载到对应目录（未下载的档位：先下载再启动，配置已就绪）：



```
D:\Qwen3.8-Flash-Next-GGUF\
├── UD-IQ2_XS\   Qwen3.8-Flash-Next-UD-IQ2_XS-00001-of-00003.gguf ...（3 分卷）
├── UD-IQ3_XS\   Qwen3.8-Flash-Next-UD-IQ3_XS-00001-of-00003.gguf ...（3 分卷）
├── UD-IQ4_XS\   Qwen3.8-Flash-Next-UD-IQ4_XS-00001-of-00003.gguf ...（3 分卷）✅ 已下载
├── UD-Q4_K_XL\  Qwen3.8-Flash-Next-UD-Q4_K_XL-00001-of-00004.gguf ...（4 分卷）
├── UD-Q5_K_M\   Qwen3.8-Flash-Next-UD-Q5_K_M-00001-of-00005.gguf ...（5 分卷）
└── UD-Q6_K\     Qwen3.8-Flash-Next-UD-Q6_K-00001-of-00005.gguf ...（5 分卷）
```

下载命令（任一）：



```
hf download unsloth/Qwen3.8-Flash-Next-GGUF --local-dir D:\Qwen3.8-Flash-Next-GGUF\UD-IQ4_XS --include "*UD-IQ4_XS*"
modelscope download --model unsloth/Qwen3.8-Flash-Next-GGUF --local_dir D:\Qwen3.8-Flash-Next-GGUF\UD-Q4_K_XL --include "*UD-Q4_K_XL*"
```

## 四、自动部署逻辑（Fairy 已接入）



1. **首次启动**：`fairy.py` 自动检测设备（GPU / 显存 / 内存）→ 扫 `E:\Strata\strata-*.json` → 优先 IQ4\_XS（已下载）→ 写入 fairy.json `model` 段并播报

2. **手动换档**：球右键 → AI 大模型 → 引擎选 "本地 Strata"；或直接编辑 fairy.json 的 `model.name` 后 `fairy.py restart`

3. **自动拉起**：Strata 未运行时 Fairy 自动按配置拉起（8080）

4. **新增档位**：下载 GGUF 到对应目录 + 复制任意 strata-\*.json 改 `--native` 路径 → Fairy 扫描自动识别

## 五、其他本地模型资产（config.json model\_assets，llama.cpp/Ollama 用）



| 模型                                  | 路径                                                            | 大小                 | 档位  |
| ----------------------------------- | ------------------------------------------------------------- | ------------------ | --- |
| Qwen3.8-27B 无审查 Q4\_K\_M            | D:\NAI-xiao\llama.cpp...\Qwen3.8-27B-Uncensored-Q4\_K\_M.gguf | 16 GB              | 大   |
| Qwen3.5-27B Q4\_K\_S                | F:\ComfyUI 模型...\Qwen3.5-27B-abliterated.Q4\_K\_S.gguf        | 14.5 GB            | 大   |
| Qwen3.5-9B Q4\_K\_M / Q6\_K / Q8\_0 | F:\ComfyUI 模型...                                              | 5.2 / 6.9 / 8.9 GB | 中   |
| Qwen3-VL-8B Q4\_K\_M（视觉）            | F:\ComfyUI 模型...                                              | 4.7 GB             | 中   |
| Ollama qwen3:30b-a3b                | Ollama 库（按需加载）                                                | \~18.5 GB          | 质量档 |

## 六、换档注意事项



* 换档前确认内存余量 ≥ 该档整包体积 + 8GB 系统余量

* 16G 显存 + 64G 内存：**推荐只留 IQ4\_XS**；Q4\_K\_XL 及以上会明显变卡

* 磁盘剩余：各档分卷都在 D 盘（SSD），换档前看 `D:\Qwen3.8-Flash-Next-GGUF` 大小

* 下载新档后可删旧档分卷腾空间（fairy.py 会自动按配置重新识别）

## 七、常见问题

**Q1：N 卡（CUDA）能不能直接跑这些配置？**

A：**不能直接跑**。所有配置都是 AMD HIP 后端（含 gfx1201 调优表），N 卡需要按第〇节改 backend/lib\_dirs/ 删调优表，**未经实测**。

**Q2：为什么换档后 8080 没反应？**

A：换档要等旧引擎完全退出（`fairy.py stop` 会自动关引擎），再启动新档。同一时刻只有一个档占 8080。

**Q3：模型文件在哪里下载？**

A：各档目录 D:\Qwen3.8-Flash-Next-GGUF <档>\，命令见第四节。国内建议用 modelscope（魔搭），不走外网。

**Q4：32G 内存的机器能跑吗？**

A：IQ4\_XS 整包 93.7GB 起，32G 内存跑不动 Flash-Next 任何档。建议改用 Ollama 跑 Qwen3.8-27B（16GB Q4，见第六节）。

**Q5：怎么切回 Ollama 的 30B-A3B？**

A：球右键 → AI 大模型 → 引擎选 "本地 Ollama（11434）"，或编辑 fairy.json 的 model.engine="ollama" 后 restart。



***

## 八、Linux 适配（★ 2026-10-11 新增）

### 1. Linux 下模型引擎用谁最好（结论）



| 机器情况                               | 首选引擎                    | 原因                                                        | 备选                               |
| ---------------------------------- | ----------------------- | --------------------------------------------------------- | -------------------------------- |
| **AMD 独显（9070 XT 等，gfx1200/1201）** | **vLLM**（ROCm 6.3+）     | 官方实测支持 RX 9070 XT；Linux 生产级性能第一梯队，显存调度最优                  | Strata（HIP 原生）、llama.cpp（Vulkan） |
| **N 卡（CUDA）**                      | **vLLM**                | CUDA 生态最成熟，并发吞吐最高                                         | Ollama、llama.cpp                 |
| **核显 / 无独显（CPU）**                  | **Ollama 或 llama.cpp**  | 轻量、自动选 CPU 优化（AVX2）；llama.cpp 支持 Vulkan 核显加速              | Xinference                       |
| **国内网络优先**                         | **Ollama / Xinference** | 国内镜像、中文生态好；Xinference 在 Linux 是主战场（Windows 起不来但 Linux 稳定） | llama.cpp                        |

**一句话**：有独立显卡（A/N 都行）→ **vLLM**；没独显 → **Ollama / llama.cpp**。

### 2. 部署（一条命令）



```
cd <项目根>\tools（Windows 侧保留）；Linux 上执行：
sudo bash fairy_linux_setup.sh --all
# 只装某个引擎：--with-vllm / --with-ollama / --with-llama
```

脚本自动：检测发行版（Ubuntu/Debian/Fedora/Arch）→ 检测显卡（N 卡 / A 卡 / 纯 CPU）→ 装依赖 → 按显卡装对应引擎。

### 3. 引擎启动示例（Linux）



```
# vLLM（AMD/N 卡，最快；OpenAI 兼容端口 8085）
vllm serve Qwen/Qwen3.5-27B --port 8085 --max-model-len 8192

# llama-server（视觉模型 Qwen3-VL-8B，Vulkan；Fairy 看图/反推）
~/llama.cpp/build/bin/llama-server \
  -m ~/models/qwen3-vl-8b.gguf --mmproj ~/models/mmproj-qwen3-vl-8b.gguf \
  --port 8085 -ngl 99

# Ollama（最省事）
ollama serve &
```

### 4. Fairy 组件 Linux 兼容表



| 组件                       | Windows | Linux | 说明                                                      |
| ------------------------ | ------- | ----- | ------------------------------------------------------- |
| `fairy.py`（启动 / 停止 / 状态） | ✅       | ✅ 已适配 | 命令层 Windows→PowerShell / Linux→bash+pgrep（★ 2026-10-11） |
| `host`（本地服务 19388）       | ✅       | ✅     | 纯 socket，跨平台                                            |
| `ear`（语音唤醒 / 对话）         | ✅       | ⚠️ 部分 | sounddevice 可用（ALSA/PulseAudio），WASAPI 专属逻辑需改（计划中）      |
| `vl`（视觉引擎 8085）          | ✅       | ✅     | llama-server Vulkan 在 Linux 原生支持                        |
| `tts`（IndexTTS 音色）       | ✅       | ✅     | PyTorch 跨平台                                             |
| `ball`（桌面球）              | ✅       | ⚠️ 部分 | Tk 可用；透明球 / 闪避是 pywin32 专属，Linux 先用普通 Tk 窗口             |
| `guard`（窗口播报 / 审核）       | ✅       | ⚠️ 部分 | 窗口枚举用 win32gui，Linux 走 X11 需改造（计划中）                     |
| 引擎联动关闭                   | ✅       | ✅ 已适配 | pkill strata/ollama/llama-server                        |

### 5. Linux 注意事项



* **AMD ROCm**：9070 XT 需 ROCm 6.3+（`amdgpu-install` 或发行版仓库），内核模块 `amdgpu` + 用户组 `render/video`

* **vLLM 安装**：AMD 直接 `pip install vllm`（新版已含 ROCm gfx1200/1201 支持）；老版需源码编译

* **模型文件**：GGUF 跨平台通用（Windows 下的 D:\ 模型可直接复制到 Linux 用）

* **Fairy 对接**：Linux 上 fairy.json 的 `ear.engine=llama`（8085 OpenAI 兼容）原样有效；视觉引擎进程名 llama-server 一致