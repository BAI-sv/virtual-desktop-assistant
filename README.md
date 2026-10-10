# FairyX — 本地 AI 桌面助手

> **FairyX** 是一个完全本地的 Windows 桌面 AI 助手：桌面悬浮球 ＋ 本地大脑（Ollama / llama.cpp /
> 任意 OpenAI 兼容接口）＋ 语音（唤醒词、离线转写、音色克隆）＋ 设备自动发现 ＋ 全盘秒级扫描 ＋
> 模型自适应部署 ＋ 实时联动（天气 / 代码纠错 / 文案 / 翻译 / 视频字幕 / 歌词 / 听歌识曲 / 视觉看图）
> ＋ 窗口对接（读屏、代打字，带六道安全闸）。
>
> **© 米哈游版权所有** · 《绝区零》素材的权利归米哈游所有，其他内容的相关权利、利益均归各自所有者享有。
> 本项目与米哈游无任何隶属、合作、赞助、授权或背书关系，详见 [NOTICE.md](NOTICE.md)。

---

## 两个入口（先看这里）

| 入口 | 是什么 | 怎么启动 |
|---|---|---|
| **新版（推荐）** | `tools/fairy.py` **启动器** ＋ `tools/fairy_ball.py` **桌面球**：悬浮球 / 进度面板 / 悬停气泡 / 右键菜单 / 语音 / 硬件条 / 本地服务 / 助手窗口 | `py -3.12 tools/fairy.py start` |
| 旧版单文件 | 根目录 `fairy.py`：早期"单文件程序"形态（悬浮球 + 扫描 + 部署 + 插件 + 语音授权），保留可用 | `py -3.12 fairy.py` |

> 两个入口共用 `tools/` 下的能力模块与配置。**新版不依赖任何外部内核**：没有 DeepSeek Harness
> 也能跑（本地服务 `fairy_host.py` 顶上），有它则优先与它协作。

## 功能总览

### 新版：桌面球与本地服务

| 模块 | 文件 | 能力 |
|---|---|---|
| 启动器 | `tools/fairy.py` | `start/stop/restart/status` ＋ 15 个按需命令（体检、扫描、播报…） |
| 桌面球 | `tools/fairy_ball.py` | 球体动画、进度面板（任务/步骤/执行方式）、悬停气泡、右键菜单、语音播报、内存/显存/CPU 硬件条 |
| 助手窗口 | `tools/fairy_ui.py` ＋ `tools/strata_ui.html` | 三栏中文前端 + 反向代理（会话、上传、语音输入、监控侧栏） |
| 本地服务 | `tools/fairy_host.py` | 19388 端口，与外部内核同契约，内核不在时顶上 |
| 寻址中心 | `tools/fairy_endpoints.py` | 统一决定"本地服务 / 外部内核"用哪个，并给出语音合成的降级链 |
| 脱离启动 | `tools/fairy_detach.py` / `fairy_ui_start.py` / `fairy_shutdown_all.py` | 让常驻件不挂在宿主进程树上（关掉宿主也不受影响） |

### 语音

| 模块 | 文件 | 能力 |
|---|---|---|
| 唤醒 | `tools/fairy_wake_listen.py`、`wake_logic.py` | 唤醒词（Sherpa-ONNX KWS）→ 连续对话 |
| 转写 | `tools/fairy_ear.py`（faster-whisper） | 离线语音转文字、语音授权 |
| 音色克隆 | `tools/indextts_server.py` / `indextts_start.py` / `indextts_guard.py` | 接 IndexTTS 做**克隆音色**合成；守护进程盯着端口，挂了自动拉起 |
| 缓存 | `tools/voice_cache.py`、`voice_audit.py` | 预合成缓存（秒出）、音色审计（统计各条语音走的是哪种引擎） |
| 麦克风 | `tools/mic_watch.py`、`mic_capture.py`、`mic_live_kws.py` | 麦克风即插即用识别与热插拔 |

### 能力模块

| 模块 | 文件 | 能力 |
|---|---|---|
| 窗口对接 | `tools/window_adapters.py` | 发现窗口 → 识别程序 → 读内容 → （授权后）打字；**六道安全闸**，默认只读，永不自动回车/发送 |
| 设备接入 | `tools/device_adapters.py`、`fairy_device.py`、`ble_identify.py`、`nearby.py` | BLE / mDNS / 串口 / 局域网设备发现与统一适配层 |
| 全盘扫描 | `tools/fairy_scan.py`、`bootstrap.py` | 4 秒扫全盘（Everything HTTP 加速）、开机自举 |
| 自适应部署 | `tools/fairy_deploy.py` | 读显存 → 分档（大/中/核显拆分）→ 选模型 → 导入 Ollama → 接大脑 |
| 实时插件 | `tools/fairy_plugins.py`、`briefing.py`、`music_info.py`、`video_subtitle.py`、`call_translate.py` | 天气 / 代码纠错 / 文案 / 翻译 / 视频字幕 / 歌词 / 听歌识曲 / 视觉 |
| 应用调度 | `tools/fairy_apps.py` | 26 大软件分类、官方直链安装、绿色软件发现与调用、写代码+查错 |
| 音频联动 | `tools/media_duck.py`、`volume_duck.py` | 说话时自动暂停/压低正在播放的音乐 |
| 体检与运维 | `tools/health_check.py`、`cpu_watch.py`、`balance_watch.py` | 全栈体检、硬件与余额守护 |
| 工作建议 | `tools/work_advisor.py` | 观察在用软件 → 给建议（默认静默，需授权） |
| 外部内核插件 | `fairy-plugin/` | DeepSeek Harness 插件（可选）：推送文本给球念、状态与帧接口 |
| 纯逻辑 | `tools/fairy_logic.py`、`mic_logic.py`、`wake_logic.py` | 问候 / 穿衣 / 语气 / 天气点评 / 麦克风与唤醒的纯判断逻辑（无 IO，便于单测） |

## 快速开始

```bash
# 1. 安装依赖（Python 3.12）
pip install -r requirements.txt

# 2. 配置
copy config.example.json config.json          # 大脑 / 语音等（旧版单文件入口用）
copy fairy_root.example.json fairy_root.json  # 配置根：路径（可选，不配也能自动推断）
copy fairy.example.json fairy.json            # 组件开关 / 接口寻址 / 窗口对接白名单（新版用）

# 3. 本地大脑（二选一；也可以只用云端）
#    a) Ollama：装好后导入模型（如 qwen3.5-9b），改 config.json 的 brain.providers
#    b) llama.cpp：按上游文档起 llama-server（Vulkan/CUDA 均可）；
#       跑视觉模型可参考 tools/llama_vl_start.bat

# 4. 启动
py -3.12 tools/fairy.py start     # 新版：桌面球 + 守护 + 本地服务
py -3.12 fairy.py                 # 旧版：单文件程序
```

首次启动会**弹窗询问授权**（扫描本机 / 自动安装软件 / 修改系统设置，默认全关）；
也可以直接说「**Fairy 授权**」用语音开启。未授权时扫描、安装、系统更改一律拒绝。

### 素材自备（重要）

本仓库**不含任何素材**（球的外观帧图 / 图标 / 语音参考音 / 模型权重都不含）。
**缺素材不会崩**：球会自动使用**占位外观**（纯色圆），并提示你把素材放到
`assets/ball_128`（`*.png`，建议 128×128 带透明）。

```
[示例] 自备素材：把 128×128 透明 PNG 放进 assets/ball_128/，球会自动加载
[示例] 不备素材：直接跑，球用占位外观 —— 语音参考音同理（voice/README.md）
```

## 核心玩法

```
说「今天天气」          → 实时天气播报（Open-Meteo 免费）
说「帮我查这个代码」     → 语法检查 + 大脑全文纠错
说「翻译：你好」        → 本地大脑中英互译
说「给视频加字幕」      → ffmpeg 提音 + faster-whisper 离线转写
说「放首歌，显示歌词」  → 识别正在播放的音乐（可选）+ 网易云歌词
说「这是什么歌」        → 本地听歌识曲（Shazam 算法）
说「看看这张图」        → 本地视觉模型看图/反推（Qwen3-VL）
说「缺什么软件」        → 26 大分类扫描，常用类优先询问安装
说「打开阅读器」        → 已装软件 / 绿色软件自动匹配调用
说「授权」              → 语音授权（扫描/安装/系统更改）
```

右键桌面球还可以：换位置 / 停止·启动本地运算（回收显存）/ 打开助手窗口 / 全部退出。

## 模型怎么来

- **本地大脑**：Ollama 或 llama.cpp 加载 GGUF（Qwen 系列 Apache-2.0）。
  `tools/fairy_deploy.py --auto` 会自动检测显存并导入。**模型不随仓库分发**，自行下载放置。
- **语音模型**：见 [voice/README.md](voice/README.md)（参考音自备）。
- **音色克隆**：IndexTTS 为**外部服务**（独立目录 + 独立 venv），本仓库不含其权重；
  不装也能用：语音会走缓存 / edge-tts 兜底。
- **视觉模型**：Qwen3-VL（社区量化版），llama-server `--mmproj` 启动，端口 8085。

## 依赖与致谢

- 依赖清单见 [requirements.txt](requirements.txt)（按仓库全部 `.py` 的实际 import 统计）。
- 全部第三方组件及其许可证见 [docs/LICENSE_AUDIT.md](docs/LICENSE_AUDIT.md)（本机逐包核实）。
- 致谢与版权说明见 [docs/第三方组件与致谢.md](docs/第三方组件与致谢.md)。
- 核心依赖：Ollama / llama.cpp / faster-whisper / sherpa-onnx / edge-tts / Everything /
  Open-Meteo / shazamio / IndexTTS（外部服务）等，均按各自许可证使用。

## 许可

- **代码与文档**：CC BY-NC-SA 4.0（署名-非商业-相同方式共享），见 [LICENSE.md](LICENSE.md)。
- **素材**：本仓库**不包含**任何第三方素材（语音/图片/动画/模型权重）。
- **非官方接口**：网易云歌词、Shazam 识曲为非官方接口，仅供学习研究，请遵守对应平台服务条款。
- **无审查模型**：若使用社区去审查（abliterated）模型，相关许可与合规责任由使用者自行承担。

## 安全与隐私

- 一切扫描、安装、系统更改**默认关闭**，须使用人授权（弹窗或语音）。
- 窗口对接默认**只读**；要打字必须显式 `mode=write`，点击另需白名单，且**永不自动回车/发送**；
  全过程写审计日志。
- 扫描报告仅存本机，不联网上传。
- 云端大脑（DeepSeek）key 从环境变量读取，不写入配置文件。
- 端口：Ollama 11434 / llama-server 8080·8085 / Everything HTTP 8092 / Fairy 本地服务 19388 /
  助手窗口 8081 / 音色服务 9881（均仅本机）。
