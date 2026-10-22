# FairyX 开箱即用 · 中文使用说明

> 版本：2026-10-10 打包版 ｜ 说明：本包是《绝区零》Fairy 桌面 AI 助手的完整本地化版本。
> 全部功能离线可用，模型、环境、软件已备好，装上就能用。

---

## 1. 这是什么
FairyX 是运行在 Windows 上的桌面 AI 助手（悬浮球 + 语音 + 本地大脑 + 设备控制），本次打包版新增：

- **本地大脑**：Qwen3.8-27B / Qwen3.5-9B 完全本地运行（Ollama），不联网也能对话
- **视觉能力**：Qwen3-VL 看图/反推/翻译（语音一句话触发）
- **设备自动发现**：自动发现路由器、耳机、空调等局域网设备（tools\fairy_extend.py）
- **全盘扫描**：硬件/软件/存储资产秒级扫描（tools\fairy_scan.py，基于 Everything）
- **模型自适应部署**：自动检测显卡（独显/核显）选档位、自动导入、自动接入大脑（tools\fairy_deploy.py）
- **实时功能插件**：天气提醒 / 代码纠错 / 文案检查 / 翻译 / 视频字幕 / 歌词 / 听歌识曲 / 自动歌词 / 定时提醒 / 系统健康（tools\fairy_plugins.py）

## 2. 目录结构（都是中文好找的路径）
| 路径 | 内容 |
|---|---|
| `启动FairyX.bat` | 双击启动 FairyX（悬浮球） |
| `install.bat` | 新机器一键安装（Python → Ollama → 依赖 → 模型） |
| `downloads\OllamaSetup.exe` | Ollama 安装包（1.5GB，装一次即可） |
| `models\whisper\` | 本地语音识别模型（faster-whisper small，字幕/语音用） |
| `voice\` | Fairy 音色包（语音合成用） |
| `tools\` | 全部功能脚本（见第 5 节命令） |
| `config.json` | **总配置文件（要改参数在这里）** |
| `Modelfile-qwen38-27b` | 27B 大模型定义（Ollama 导入用） |
| `Modelfile-qwen35-9b` | 9B 快脑定义 |
| `docs\` | 架构文档、第三方致谢、功能全景等 |

## 3. 模型放哪里（重要，中文路径）
> 模型体积大（5~90GB），打包不包含，请从以下来源之一放好：
| 档位 | 推荐模型 | 放置位置（中文路径） |
|---|---|---|
| 大显存独显（≥14GB，如 9070XT） | Qwen3.8-27B 无审查 Q4_K_M（16GB） | `D:\llama.cpp\vulkan-qwen3.8-27B\...\model\` |
| 中显存（≥6GB） | Qwen3.5-9B Q4/Q6/Q8（5~9GB） | `F:\ComfyUI模型\models\LLM\` |
| 核显/无独显（内存≥32GB） | 大模型用 llama.cpp Vulkan 跑（工具里已备） | `tools\fairy_deploy.py` 会自动找 |
| 核显/小内存 | 1.5~4B 小模型 | 放到 `models\LLM\` 新建目录 |

**放置后运行**（自动检测硬件 → 选档 → 导入 → 接入大脑）：
```
py -3.12 tools\fairy_deploy.py --auto
```
也可以手动指定：
```
py -3.12 tools\fairy_deploy.py --model "D:\你的路径\模型.gguf"
```

**视觉模型**（看图/反推）：双击 `tools\llama_vl_start.bat` 启动视觉服务（端口 8085），加载约 30 秒。

## 4. 需要自己改的参数（config.json，中文标注）
用记事本打开 `config.json`，改这几处：

| 参数路径 | 说明 | 默认值 |
|---|---|---|
| `brain.providers[0].model` | 主大脑模型名（Ollama 里的名字） | qwen3.8-27b |
| `brain.providers[1].model` | 备用快脑 | qwen3.5-9b |
| `brain.providers` 里 DSH 的 `api_key` | 云端兜底 DeepSeek 密钥（留空=本地优先） | 空 |
| `voice.asr_mode` / `voice.whisper.download_root` | 语音识别模型 | whisper / models\whisper |
| `voice.tts_voice` | 播报音色（微软 Edge 语音） | zh-CN-XiaoxiaoNeural |
| `floating_ball.position_x/y` | 悬浮球屏幕位置 | 180, 180 |
| `comfy.base_url` | ComfyUI 出图地址 | http://127.0.0.1:8188 |

> 改了之后重启 FairyX 生效。看不懂的项别动，保持默认即可。
## 5. 常用命令（全部中文可懂）

```bat
启动FairyX.bat                  :: 启动悬浮球助手（语音+大脑）
py -3.12 tools\fairy_extend.py --run   :: 启动设备自动发现（局域网设备接入）
py -3.12 tools\fairy_scan.py           :: 全盘扫描硬件/软件/存储（4秒出报告）
py -3.12 tools\fairy_deploy.py --auto  :: 自动部署模型并接入大脑
py -3.12 tools\fairy_plugins.py --watch        :: 实时监控（天气+剪贴板文案/翻译）
py -3.12 tools\fairy_plugins.py --weather      :: 查天气（默认濮阳，可改坐标）
py -3.12 tools\fairy_plugins.py --lint 代码.py :: 代码实时纠错
py -3.12 tools\fairy_plugins.py --check 文案   :: 文案错别字/格式检查
py -3.12 tools\fairy_plugins.py --translate 文本 :: 中英互译（本地大脑）
py -3.12 tools\fairy_plugins.py --subtitle 视频.mp4 :: 视频离线生成字幕（srt）
py -3.12 tools\fairy_plugins.py --lyrics 歌名  :: 查歌词
py -3.12 tools\fairy_plugins.py --image 图片.jpg --mode 反推|描述|翻译 :: 看图/反推提示词（需先启动 tools\llama_vl_start.bat）
py -3.12 tools\fairy_plugins.py --songid  :: 听歌识曲（shazamio 免费免注册，录音12秒识别+歌词）
py -3.12 tools\fairy_plugins.py --autolyric  :: 自动歌词（检测正在播放→切歌自动显示歌词，网易云/QQ音乐等皆可）
py -3.12 tools\fairy_plugins.py --remind "10分钟后 喝水" :: 定时提醒（也支持 "8:30 开会"）
py -3.12 tools\fairy_plugins.py --sysinfo :: 系统健康一览（CPU/内存/磁盘/网络）
py -3.12 tools\fairy_apps.py --list     :: 列出全部可调度工具（IDE/播放器/下载器/文档）
py -3.12 tools\fairy_apps.py --catalog  :: 扫描26大软件分类总览（已装自动归类+缺类标注）
py -3.12 tools\fairy_apps.py --prep     :: 缺类代表软件询问预装（常用类优先：视频/下载/文档/音乐/上网）
py -3.12 tools\fairy_apps.py --report   :: 生成可视HTML分类报告并打开浏览器
py -3.12 tools\fairy_apps.py --install vscode :: 自动下载安装工具（vs/aria2 等）
py -3.12 tools\fairy_apps.py --write "D:\x\demo.py" "代码" :: 写代码+查错+IDE打开
py -3.12 tools\fairy_apps.py --discover  :: 扫描绿色/单文件软件（不写注册表，免安装直接调用）
py -3.12 tools\fairy_apps.py --run 名称    :: 调用绿色软件（如 --run koodo 启动阅读器）
```

> **使用人授权**：FairyX 默认**不扫描、不改动**。两种授权方式：
> - **弹窗**：首次启动弹窗询问三项授权（扫描本机/自动安装软件/修改系统设置），同意即开启
> - **语音**：直接说"**Fairy 授权**"（全开）或"**Fairy 同意扫描**"/"**同意安装**"（单项）
> 未授权时扫描、安装命令会提示需授权；随时可在 `config.json` 的 `consent` 段修改。

### 工具自动调度（说一句话就用工具）

启动 FairyX 后对**悬浮球/语音**说（大脑自动调用工具，没装就自动下载）：

| 说的话 | 效果 |
|---|---|
| "给我写个代码"（Python） | 生成 → 保存 → 语法检查 → VS Code 打开 |
| "写个鸿蒙应用" | DevEco Studio 打开（本机已装） |
| "用 Android Studio 写个 APP" | Android Studio 打开（已装） |
| "用 VC++ 写个程序" / "C++ 代码" | Visual Studio（没装会自动下载安装器） |
| "帮我下载一个视频" | aria2 下载器（没装自动下载） |
| "写个文档" | WPS 打开（已装） |

> 自动下载仅限**免费 + 官方直链**的软件（VS Code、aria2、VS 安装器）；大型 IDE（DevEco/Android Studio）需手动安装，Fairy 会提示官网链接。

### 视觉（看图/反推）语音一句话调用

启动 FairyX 后，**对麦克风说**（自动截当前屏幕 + Qwen3-VL 理解 + 语音播报）：

| 说的话 | 效果 |
|---|---|
| "Fairy 反推" / "Fairy 生成提示词" | 截屏 → 生成 SD 英文提示词 |
| "Fairy 看看这个" / "Fairy 看图" | 截屏 → 描述画面 |
| "Fairy 翻译屏幕" | 截屏 → OCR + 翻译图中文字 |

> 首次用视觉需先双击 `tools\llama_vl_start.bat` 启动视觉服务（端口 8085，加载约 30 秒）。

## 6. 常见问题

**Q：天气位置不对？** 改 `fairy_plugins.py` 里 `weather_check` 的 `lat/lon`（默认濮阳 35.76, 115.03），或直接用经纬度。

**Q：Ollama 没起来？** 双击 `%LOCALAPPDATA%\Programs\Ollama\ollama.exe`，或运行 `ollama serve`。

**Q：模型部署说找不到模型？** 模型没放到第 3 节的位置。放好后重跑 `fairy_deploy.py --auto`。

**Q：核显机器跑得动吗？** 可以。`fairy_deploy.py` 会自动识别核显：内存大就用 Vulkan llama.cpp 跑大模型（专家拆分），内存小就用小模型，都能用。

**Q：中文路径报错？** 本包已适配中文路径，脚本全部用引号包裹路径。若个别程序报错，把模型移到 `<项目根>\models\LLM\` 这种无空格路径即可。

**Q：360 弹窗拦截？** 首次运行请允许（FairyX 是本地程序，无联网上传行为，详见《安全风险评估.md》）。

**Q：听歌识曲没反应？** 需联网（shazamio 走 Shazam 公开接口），且声音靠近麦克风；接口限流时等 1 分钟再试。

**Q：自动歌词读不到歌？** 需使用支持系统媒体控制的播放器（网易云/QQ音乐/浏览器都支持）；老版 Windows Media Player 不支持。

**Q：用了哪些开源项目？** 见 `docs\第三方组件与致谢.md`（全部免费/开源组件、链接、协议）。

---

## 智能眼镜（glasses）模块

2026-10-22 已合入：ESP32-S3 四合一智能眼镜（AI语音/拍照翻译/视觉问答/高德导航）。

- 中枢：`py -3.12 glasses\ai_center.py 5000`
- 固件：`glasses\esp32_glasses.ino`（v3.3 按键版，短按翻译/双击语音/长按导航等）
- 发现：FairyX 通过 `glasses_adapter` 发现【在线】眼镜，离线不显示不播报（强扩展铁律）
- 文档：`docs\智能眼镜_GLASSES.md`、`glasses\README.md`
