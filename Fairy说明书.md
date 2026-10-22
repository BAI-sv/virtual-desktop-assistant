# Fairy（虚拟桌面助手）使用说明书

> 版本：2026-10-17　·　主目录：`<项目根>\`
> 一句话：**Fairy 是一个以 DSH（DeepSeek Harness）为内核、能说话能干活能控制设备的桌面悬浮球助手。** 球只是显示器，大脑在 DSH，耳朵、嗓子、手脚（设备控制）都是本地独立进程。

---

## 1. 它是什么 / 怎么跑起来

### 1.1 核心概念

- **球（ball）**：桌面上那个悬浮的小精灵，真正浮在所有窗口之上，逐像素透明。
- **内核是 DSH**：球每 900ms 拉一次 DSH 接口拿状态（在干嘛/进度/步骤），自己**没有大脑**。
- **DSH 不在也活着**：DSH 关了，球自动切到本地小服务 `fairy_host.py`（19388），照样能念话、能显示状态。
- **所有组件都是独立进程**，用 WMI 脱离 DSH 进程树启动——所以关 DSH 不会把 Fairy 一起带走。

### 1.2 启动 / 停止

| 操作 | 方式 |
|---|---|
| 一键启动 Fairy | 运行 `fairy_dsh_start.py`（先拉 Fairy 再启 DSH）或直接 `pythonw fairy.py` |
| 打开 DSH 自动带 Fairy | `fairy_dsh_watch.py` 常驻监视，发现 DSH 启动就自动拉起 Fairy |
| 全部退出 | 球的右键菜单「全部退出 Fairy」，或 `fairy_shutdown_all.py` |
| 只重启球 | 找到 `fairy_ball.py` 进程，结束再 `pythonw fairy_ball.py` |

所有常驻脚本用 **pythonw.exe** 启动（绝不弹黑窗），日志写进 `<项目根>\logs\*.log`。

---

## 2. 核心架构与数据流

```
                    ┌─────────────┐
   说话/干活指令 ──▶ │    DSH 内核  │（大脑，19387）
                    └──────┬──────┘
                          │ /api/fairy/meta（状态）
                 ┌────────▼────────┐
                 │  fairy_ball.py  │ 桌面悬浮球（显示器）
                 │  球+面板+气泡   │
                 └───┬────┬────┬──┘
        ┌────────────┤    │    └───────────┐
   ┌────▼───┐  ┌─────▼───┐ ┌──▼────────┐ ┌─▼──────────┐
   │ fairy_ear│ │fairy_host│ │IndexTTS   │ │ fairy_rim  │
   │ 耳朵/唤醒│ │本地兜底   │ │克隆音色    │ │屏幕RGB灯效 │
   └─────────┘ └─────────┘ └───────────┘ └────────────┘
   + 引擎(8085 llm / 8086 vl / 8080 strata) + 设备控制(智能家居) + 工具(OCR/识曲/截图...)
```

- **语音链路**：`fairy_ear.py`（听）→ `asr_bridge.py`（识别）→ 交给内核 → `IndexTTS`（合成 Fairy 音色）→ 播报。全部本地、零云请求。
- **接口寻址**：都走 `fairy_endpoints.py`——DSH 在就用 DSH，不在就切本地 host，自动降级。

---

## 3. 配置文件（改配置不碰代码）

| 文件 | 作用 | 关键项 |
|---|---|---|
| **`fairy.json`** | 总配置：所有组件开关 + 接口地址 + 窗口对接 + 模型 + 权限 | `components`（各组件启停）、`endpoints`（地址/音色/降级）、`windows`（对接哪些程序）、`model`（引擎与模型）、`online`（在线降级链） |
| `fairy_root.json` | 配置根：统一所有路径（改这一个就能整体挪项目） | `root`、`work` 等路径 |
| `briefing.json` | 开机播报配置 | 城市、新闻 RSS、播报项开关 |
| `config.json` | 通用配置 | 视功能而定 |
| `fairy_ball_pos.json` | 球在屏幕上的位置记忆 | 拖动后自动保存 |

> **改完多数项下次启动生效，不用改代码。**

---

## 4. 文件清单（每个文件干什么）

### 4.1 核心运行时（Fairy 本体）

| 文件 | 作用 |
|---|---|
| **`fairy.py`** | **统一总管**。按 `fairy.json` 的 `components` 启动/停止所有组件（球/耳朵/引擎/灯效/对话页）。**加组件改这里** |
| **`fairy_ball.py`** | **桌面悬浮球**（主界面）。球体渲染、进度条、气泡、右键菜单、控制面板、窗口定位、拖拽。**改界面/弹窗/菜单都在这** |
| `fairy_ear.py` | **耳朵**：常驻监听麦克风，喊「Fairy」就应答 |
| `fairy_host.py` | **本地小服务**（19388）：DSH 不在时兜底状态/回复/播报 |
| `fairy_chat.py` | **统一对话页**（8090）：三栏工作台（会话列表/对话/监控），多模态+语音 |
| `fairy_rim.py` | **屏幕边缘 RGB 灯效**：全屏透明灯带，随模型状态呼吸变色。**改灯带宽度/颜色/渐变在这** |
| `fairy_endpoints.py` | 接口寻址与自动降级中心（DSH/本地/IndexTTS 怎么选） |
| `fairy_root.py` | 配置根：路径统一入口 |
| `fairy_ui.py` / `fairy_ui_start.py` | Strata 的中文前端 + 反向代理（8081，脱离 DSH 启动） |
| `fairy_detach.py` | 用 WMI 独立启动 Fairy，脱离 DSH 进程树 |
| `fairy_dsh_start.py` | DSH 与 Fairy 联动启动（先 Fairy 后 DSH） |
| `fairy_dsh_watch.py` | 常驻监视：DSH 一起来就自动带 Fairy |
| `fairy_shutdown_all.py` | 一键全部退出（球+服务+语音+引擎） |

### 4.2 AI 引擎（本地大模型）

| 文件 | 作用 |
|---|---|
| `fairy_llm.py` | 本地文本引擎（llama-server Vulkan，Qwen3.5-9B，**8085**） |
| `fairy_vl.py` | 视觉引擎（llama-server Vulkan，Qwen3-VL-8B，**8086**） |
| `strata_start.py` | 拉起本机 Strata 大模型（**8080**，脱离 DSH 进程树） |
| `strata_ask.py` | 把「该想类」问题发给本地 Strata（直答类主代理自己做） |
| `fairy_deploy.py` | 模型自适应部署器（检测硬件→选模型档→自动部署） |
| `capability.py` | 模型能力探测/扫描 |

### 4.3 语音（TTS 合成 + ASR 识别）

| 文件 | 作用 |
|---|---|
| `indextts_server.py` | IndexTTS 常驻合成服务（跑在 IndexTTS 自己的 venv，**9881**） |
| `indextts_start.py` | 拉起 IndexTTS 服务并等端口就绪 |
| `indextts_guard.py` | IndexTTS 保活守护（挂了立刻拉起来，保住 Fairy 音色） |
| `indextts_fetch25.py` / `indextts_fix_aux.py` | 补齐/修复 IndexTTS 2.5 模型文件 |
| `fairy_voice.py` | 音色管理（列出/切换/保存克隆音色） |
| `fairy_say.py` | 语音播报小工具（独立可用） |
| `voice_cache.py` | 克隆音色预合成缓存（固定话术零延迟、不依赖在线） |
| `voice_timing.py` / `voice_audit.py` | 合成测速 / 语音路由审计 |
| `asr_bridge.py` / `asr_sensevoice.py` / `funasr_worker.py` | 本地语音识别（SenseVoice + Paraformer 自动降级） |
| `fairy_mic_source.py` | 兜底麦克风采集（音频端点损坏时的 WDM-KS 直读） |
| `mic_live_kws.py` / `fairy_wake_listen.py` / `wake_logic.py` | 唤醒词监听 / 即时应答版 / 唤醒判定逻辑 |
| `mic_capture.py` / `mic_watch.py` / `mic_logic.py` | 麦克风采集 / 插拔提示 / 类型判断 |
| `fairy_audio.py` | 默认音频输出设备读取/保存/还原 |

### 4.4 设备发现与智能家居控制

| 文件 | 作用 |
|---|---|
| `fairy_iot.py` | 设备自动发现与连接中心（局域网/蓝牙/mDNS/串口） |
| `fairy_scan.py` | 设备扫描器（硬件/软件/服务/资产，出扫描报告） |
| `fairy_extend.py` | 设备池（内存+落盘） |
| `device_adapters.py` | 设备适配层统一契约 |
| `window_adapters.py` | 窗口发现/识别/读取/操作层（对接程序的核心） |
| `nearby.py` | 附近设备发现/识别/上下线监控（只读纯本地） |
| `ble_identify.py` | 用 bleak 真连接读 BLE 设备信息 |
| `smart_home.py` | 智能家居语音控制 |
| `ewelink_bridge.py` | 易微联（eWeLink）云 API 直连桥 |
| `ha_bridge.py` | Home Assistant 双向联动桥 |
| `robot_bridge.py` | 机器人接入 Fairy 的唯一对接点 |
| `fairy_device.py` | 布尔特征→设备类别名 |
| `fairy_knowledge.py` | 软件知识库（软件名→启动程序） |
| `fairy_apps.py` | 全类别工具调度中心 |

### 4.5 功能工具（截图/OCR/识曲/翻译/待办…）

| 文件 | 作用 |
|---|---|
| `fairy_ocr.py` | 截图 OCR（RapidOCR 离线秒出） |
| `fairy_detect.py` | 截图中识别物体（YOLO11n） |
| `fairy_capture.py` | 微信风格截图（选区+微调+标注/OCR/识别） |
| `fairy_screenshot.py` | 截图标注（画框/箭头/文字/画笔） |
| `fairy_overlay_ocr.py` | 屏幕常驻 OCR 悬浮窗（框选区域实时取字） |
| `fairy_hotkeys.py` | 全局快捷键（Ctrl+Alt+O 截图OCR / C 剪贴板 / S 安全自检） |
| `fairy_clip.py` | 剪贴板历史 |
| `fairy_color.py` | 屏幕取色器（放大镜取色复制 HEX） |
| `fairy_memory.py` | 使用记忆与习惯学习 |
| `fairy_todo.py` | 待办清单（增删/完成/到期播报） |
| `fairy_alert.py` | 天气预警主动提醒 |
| `briefing.py` | 开机播报（时间/天气/要闻/穿衣建议） |
| `work_advisor.py` | 实时工作建议 |
| `song_recognition.py` | 听歌识曲（Shazam 协议） |
| `music_info.py` | 歌词/创作背景/内涵分析 |
| `call_translate.py` | 语音通话实时翻译字幕 |
| `video_subtitle.py` | 看视频实时转写字幕+翻译+记要点 |
| `fairy_custom_cmd.py` | 自定义语音指令 |
| `fairy_plugins.py` | 实时功能插件集（天气/代码纠错/文案检查…） |
| `fairy_security.py` | 安全自检（病毒/漏洞只读检查） |
| `fairy_netdiag.py` | 网络一键诊断 |
| `health_check.py` | 系统体检 |
| `window_guard.py` | 窗口播报 + 代码审核守护 |
| `volume_duck.py` / `media_duck.py` | 音量闪避 / 媒体闪避（Fairy 说话时让音乐让位） |
| `balance_watch.py` | DeepSeek 余额预警 |
| `cpu_watch.py` | CPU/硬件稳定性监控（WHEA 报错趋势） |
| `build_assets.py` / `build_voice_index.py` / `build_line_pools.py` / `extend_voice_index.py` | 素材/语音索引/语料构建 |

### 4.6 维护 / 发布 / 联动

| 文件 | 作用 |
|---|---|
| `fairy_gitsync.py` | 自动上传（检测变更→commit→push 到 GitHub） |
| `clean_repo.py` | 仓库清理与校验（隐私残留/路径检查） |
| `pack_fairyx.py` | 打包成「开箱即用」版（install.bat + 说明书） |
| `restart_dsh.py` | 重启 DSH（改插件源码后必须重启才生效） |
| `fairy_perms.py` | 权限说明与授权管理 |
| `bootstrap.py` | Fairy 自举 / 自扩展流水线 |

### 4.7 临时 / 一次性脚本（不是日常功能，可放心忽略）

`tmp_*.py`（tmp_list / tmp_read_mahm / tmp_fix_mic_priv 等）、`_video_sample\` 等——是排查问题时写的临时脚本/样例，不属于功能。

---

## 5. 怎么改功能（实操指南）

### 5.1 通用改法（所有功能都适用）

```
① 用文本编辑器打开对应 .py（推荐 VS Code，装 Python 插件）
② 找到要改的常量/函数，改
③ 校验语法：  py -3.12 -m py_compile tools\要改的文件.py     （有报错就说明改坏了）
④ 重启对应进程：结束该进程 → pythonw tools\xxx.py
   （改 fairy_ball.py 就重启球；改 fairy_rim.py 就重启灯效，等等）
```

> 改前建议备份：`copy 文件 文件.bak_日期`。改完先 `py_compile` 再重启，别直接重启（容易黑窗/闪退）。

### 5.2 具体场景

**① 开关某个组件（不开灯效/不要耳朵…）**
- 打开 `fairy.json` → `components` 段，把对应项改成 `false`：
  - `"rim": false` = 关闭屏幕 RGB 灯效；`"ear": false` = 关闭喊话应答；`"llm": false` = 不启动文本引擎
- 保存，重启 Fairy（`fairy_shutdown_all.py` 再 `fairy_dsh_start.py`）。

**② 换 Fairy 的声音**
- 改 `fairy.json` → `endpoints.ref_audio`，指向另一个 wav（放到 `voice\index_voices\`）
- 或用命令：`py -3.12 tools\fairy_voice.py list` / `set <音色名.wav>`

**③ 改球的外观 / 气泡 / 弹窗位置**
- 都在 `fairy_ball.py`：
  - 球的尺寸：文件顶部 `SIZE`、`WIN_W`/`WIN_H`
  - 气泡停留秒数：`fairy.json` → `ball.bubble_seconds`
  - 弹窗/窗口离屏边距离：`_EDGE_PX`（默认 40px≈1cm）
  - 右键菜单项：`_on_right` 里的 `_items` 列表
  - 控制面板：`_open_panel_impl`（加区块/开关按钮）

**④ 改屏幕 RGB 灯效（宽/色/呼吸）**
- `fairy_rim.py`：
  - 灯带宽度：`_RIM_T_IN`（亮带）/ `_RIM_T_OUT`（光晕）
  - 颜色：`_COLORS`（idle/reading/generating/error 各是什么色）
  - 呼吸速度：`_PERIOD`
- 改完重启灯效进程。

**⑤ 让 Fairy 对接一个新软件**
- `fairy.json` → `windows.programs`，复制 `_新增程序示例` 那条，填 `process`（进程名）+ `mode`（read 只读 / write 可打字）
- 不用改代码。详情看 `fairy.json` 里的 `_windows_说明`。

**⑥ 加一个新 AI / 引擎 / 组件**
- 引擎：`fairy.py` 的组件表（`COMPONENTS`，约 140~215 行）加一条 `{script, match, desc}`，并在 `fairy.json` 的 `components` 加上开关。
- 组合切换、模型映射：`fairy_ball.py` 里的组合/`_DSH_MODEL_BY_ENG` 相关逻辑。

**⑦ 改语音链路（识别引擎 / 音色来源）**
- 识别：`asr_bridge.py`（SenseVoice/Paraformer 谁优先、降级）
- 音色来源：`fairy_endpoints.py` + `fairy.json` 的 `endpoints`
- 语音兜底顺序：`fairy_ball.py` 顶部注释里有完整降级链说明。

**⑧ 改开机播报**
- 播报内容开关：`fairy.json` → `briefing`（time/location/weather/news/todo… true/false）
- 城市/新闻源：`briefing.json`；播报实现：`briefing.py`

**⑨ 改快捷键**
- `fairy_hotkeys.py` 顶部的默认热键（Ctrl+Alt+O / C / S）。

**⑩ 常用调试命令**

```
py -3.12 tools\fairy_netdiag.py            # 网络诊断
py -3.12 tools\health_check.py --speak     # 体检并让球念
py -3.12 tools\fairy_scan.py               # 全面扫描出报告
py -3.12 tools\clean_repo.py --fix         # 清仓库隐私残留
py -3.12 tools\fairy_gitsync.py            # 手动同步 GitHub
```

---

## 6. 常见问题速查

| 现象 | 原因 / 改哪里 |
|---|---|
| 球不回应喊话 | `fairy_ear.py` 没在跑 → `fairy.json` 里 `ear:true`，重启 Fairy |
| 球声音是通用音色（不是 Fairy） | IndexTTS 挂了 → `indextts_guard.py` 没守护；看 `fairy_voice.py list` |
| 选不到本地模型 | 引擎没起（8085/8086/8080 未监听）→ 看日志 `logs\*.log`，或 `fairy.py` 引擎组件 |
| 窗口跑出屏幕 | `fairy_ball.py` 的 `_EDGE_PX` / `_popup_pos` 已做 1cm 约束 |
| 灯效太窄/太宽 | `fairy_rim.py` 的 `_RIM_T_IN` / `_RIM_T_OUT` |
| 改了代码没效果 | 没重启对应进程；或改错文件（组件启动的是另一个脚本） |

---

*本说明书由 Fairy 项目维护者整理，会随功能迭代更新。核心可改文件：`fairy.json`（配置）、`fairy_ball.py`（界面）、`fairy_rim.py`（灯效）、`fairy.py`（组件）、`fairy_endpoints.py`（链路）。*

---

## 智能眼镜（glasses）模块（2026-10-22 合入）

ESP32-S3 四合一智能眼镜：AI 语音 / 拍照翻译 / 视觉问答 / 高德导航。

- AI 中枢：`glasses\ai_center.py`（0.0.0.0:5000，零 Flask 依赖）
- 固件：`glasses\esp32_glasses.ino` v3.3 按键版（短按翻译、双击语音、长按0.8~1.5s导航、1.5~3s购物点评、>3s看图）
- 接入：`tools\glasses_adapter.py` 已注册进 `device_adapters`（与 robot 同模式懒加载）
- 铁律：**眼镜在线才进设备池、才播报；离线不显示**（强扩展，与机器人/汽车统一）
- 密钥：`glasses\keys.py` 已 .gitignore，严禁上传
- 详见 `docs\智能眼镜_GLASSES.md`
