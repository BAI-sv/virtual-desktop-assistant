# 架构

---

## 一、三层解耦

虚拟桌面助手 的核心设计决定：**把"聪明"和"好看"彻底分开。**

```
┌──────────────────────────────────────────────────────────────┐
│  ① 内核层 —— 两种模式                                        │
│     A. DSH 模式：DSH（DeepSeek Harness）+ dsh-plugin-fairy     │
│     B. 单机模式：fairy_host.py 本地内核（19388，DSH 不在时）   │
│     完整的编码智能体：工具调用、多轮对话、上下文管理、插件体系      │
│     它【不知道】桌面上有个球，也不知道有人在听它说话              │
└────────────────────────┬─────────────────────────────────────┘
                         │ HTTP /api/fairy/*（fairy_host 统一出口）
                         ▼
┌──────────────────────────────────────────────────────────────┐
│  ② 外形层 —— fairy_ball.py                     │
│     悬浮球、进度条、气泡、语音队列、设备监听、音乐让位、本地运算     │
│     它【不知道】内核有多聪明，只认 HTTP 契约                     │
└────────────────────────┬─────────────────────────────────────┘
                         │ 需要"一段音频"
                         ▼
┌──────────────────────────────────────────────────────────────┐
│  ③ 语音层 —— indextts_server.py（外部） + 若干本仓库脚本        │
│     音色克隆合成、预合成缓存、保活、音频设备管理                  │
│     它【不知道】谁在说话，只认"文本进、音频出"                    │
└──────────────────────────────────────────────────────────────┘
```

**运行模式**：`fairy.py start` 默认启动 `ball + guard + host`。
`fairy_host.py`（19388）是 `/api/fairy/*` 的**统一出口**：DSH 在线时把请求转发给 DSH 插件，
DSH 不在线时**本地应答**（`local_push` / 本地 `say` / 本地 `meta`），所以 Fairy 可以**脱离 DSH 单独运行**。
球的右键菜单还有"本地运算"开关（内置 Strata/Ollama 调用），可直连本地大模型。

**为什么这么分（设计取舍）**

| 决定 | 理由 | 代价 |
|---|---|---|
| 内核与外形**用 HTTP 解耦**，不用进程内调用 | 内核可以换（DSH / 本地内核 / 别的）；改内核不用重编外形 | 有轮询延迟（900 ms） |
| 外形与语音**用文件 + HTTP 解耦** | 语音可以换成任意 TTS | 多一次 I/O |
| 合成服务**做成独立常驻进程** | 模型加载一次用很久（35 s）；球重启不用重新加载 | 需要保活（否则挂了没人管） |
| 语音**四层降级**而不是单一路径 | 任何一层挂掉都不能哑巴 | 复杂度；需要审计工具看走了哪层 |

---

## 二、数据流

**"回复"是怎么变成"你听到的声音"的（两条来源）：**

```
 ① 来源 A（DSH 模式）：你在 DSH 里说话，助手产生回复
                    插件监听会话事件，把助手回复的【纯文本】抽出来
    · 只认 TextBlock，忽略 reasoning / tool-call / image / file
    · 清洗：去代码块、去 Markdown 强调符、去表格分隔线、压空白
    · 截断到 150 字（REPLY_MAX_CHARS），尽量断在句末（。！？\n. ；）
    · 存进 REPLY{seq, text, truncated, full, ...}
    来源 B（单机模式）：fairy_host 本地内核直接产生回复
    （本地大模型 / API 直连，走 /api/fairy/push 或 local_push 入队）
                    │
 ② fairy_host.py（19388）统一出口 /api/fairy/reply
    · 单机模式自己应答；DSH 模式转发 DSH 插件
                    │
 ③ 球每 900 ms 轮询 GET /api/fairy/reply
    · 用【单调递增的 seq】去重（比比较字符串可靠）
    · 首次只建立基线，不补念历史
    · 若 truncated=true，追加一句提示后仍念出来
                    │
 ④ 文本进入语音队列，_worker 线程按四层降级选路线
    （详见 docs/voice.md）
                    │
 ⑤ 让位音乐（duck）→ 播放 → 恢复音量（unduck）
```

**关键数字**

| 项 | 值 | 来源 |
|---|---|---|
| 轮询间隔 | 900 ms | `fairy_ball.py` 的 `POLL_MS` |
| 单次念出上限 | **150 字** | 插件的 `REPLY_MAX_CHARS` |
| 播放设备 | 系统默认输出 | 见 `fairy_audio.py` |

---

## 三、HTTP 契约

**由 `fairy_host.py`（19388）统一提供**：DSH 在线时转发给 DSH 插件（改插件后**必须重启 DSH**）；
DSH 不在线时由 host **本地应答**（`local_push` / 本地 `say`）。改 `fairy_host.py` 后重启 `host` 组件即可：

| 路径 | 方法 | 作用 |
|---|---|---|
| `/api/fairy/meta` | GET | 权威状态：帧数/球大小/在不在干活/音色数/上下文占用 |
| `/api/fairy/reply` | GET | 助手最新回复 `{ok, seq, text, truncated, fullLength, time, sessionId, turn, step}` |
| `/api/fairy/say` | POST | **文本 -> 音频**。返回 WAV，响应头 `x-fairy-voice` 标明走的哪条路 |
| `/api/fairy/task` | GET | 当前任务/步骤 |
| `/fairy/ball` | GET | 球的静态素材（帧图等） |

**`/api/fairy/say` 的响应头（判断音色的关键）**

```
x-fairy-voice: indextts-clone        <- 目标音色（克隆成功）
x-fairy-voice: edge-tts-fallback     <- ★ 降级了，音色不对
```

**由本仓库的合成服务提供**（端口 9881）：

| 路径 | 方法 | 作用 |
|---|---|---|
| `/health` | GET | `{"ok":true,"device":"cuda:0","api":"infer_v2_5","loaded":true,"voices":3,"root":"..."}` |
| `/tts` | POST | `{"text","ref_audio","lang","emo_alpha"}` -> WAV 二进制 |
| `/api/v1/voices` | GET | 兼容 `dsh-plugin-tts` 的接口 |
| `/api/v1/tts/tasks` | POST | 同上 |
| `/api/v1/upload` | POST | 同上 |

**★ 一个排障要点**：**DSH 对未注册的路径返回 401（不是 404）**（仅 DSH 模式）。
所以"401"= 路由没注册（需要重启 DSH），"200"= 已加载。

---

## 四、模块职责

### 4.1 入口层

| 模块 | 一句话职责 | 关键点 |
|---|---|---|
| `fairy.py` | **唯一入口/总管**：启停组件、查状态、转发按需命令 | `COMPONENTS` 定义 4 个常驻组件；`CMDS` 定义 15 个按需命令 |

**组件表**（`COMPONENTS`，4 个）：

| 组件名 | 脚本 | 匹配串 | 等待秒数 |
|---|---|---|---|
| `ball` | `fairy_ball.py` | `fairy_ball` | 2.5（默认） |
| `tts` | `indextts_start.py` | `indextts_server` | **75** |
| `guard` | `indextts_guard.py` | `indextts_guard.py` | **6** |
| `host` | `fairy_host.py` | `fairy_host.py` | 3（默认） |

**按需命令**（`CMDS`，15 个）：

| 命令 | 实际调用 |
|---|---|
| `init` / `rescan` | `bootstrap.py --full` |
| `scan` | `bootstrap.py --dry` |
| `devices` | `device_adapters.py` |
| `ble` | `ble_identify.py` |
| `health` | `health_check.py` |
| `fastcheck` | `health_check.py --fast` |
| `news` | `briefing.py --news` |
| `weather` | `briefing.py` |
| `music` | `music_info.py --selftest` |
| `subtitle` | `video_subtitle.py --run --translate --subtitle` |
| `call` | `call_translate.py --run` |
| `cpu` | `cpu_watch.py` |

### 4.2 外形层

| 模块 | 职责 |
|---|---|
| `fairy_ball.py` | 桌面悬浮球：分层窗口渲染、帧动画、进度条面板、气泡、**语音队列 `_worker`**、设备热插拔监听、余额查询、音乐让位、**本地运算（Strata/Ollama 开关）** |
| `fairy_ear.py` | 耳朵：常驻监听 + 端点检测 + 唤醒 + 连续对话 |
| `fairy_wake_listen.py` | 过渡版"喊了就应"（双引擎 KWS，应答走克隆） |

### 4.3 语音层

| 模块 | 职责 |
|---|---|
| `indextts_server.py` | 合成服务本体（HTTP 包装 + 启动预热） |
| `indextts_start.py` | 一次性启动器（找安装 -> 起服务 -> 阻塞等就绪，最长 900 s） |
| `indextts_guard.py` | 保活看门狗（15 s 探活 / 30 s 冷却 / 拉起后暖机） |
| `voice_cache.py` | 预合成缓存（把固定话术用克隆音色合成好） |
| `voice_audit.py` | 语音路由审计（统计每句话走了哪条路） |
| `fairy_audio.py` | 默认音频输出设备读写/切换/保存/还原 |
| `media_duck.py` / `volume_duck.py` | 音乐让位（说话时压低系统音量/媒体） |

### 4.4 播报与记忆

| 模块 | 职责 |
|---|---|
| `briefing.py` | 开机播报（问候 + 天气 + 新闻） |
| `work_advisor.py` | 实时工作建议（观察 -> 建议 -> 可交给 DSH） |
| `balance_watch.py` | API 余额监控与预警（跨档才说，避免唠叨） |

### 4.5 设备与其他

| 模块 | 职责 |
|---|---|
| `device_adapters.py` | 设备接入统一适配层 |
| `nearby.py` | 附近设备发现/识别/监控 |
| `ble_identify.py` | BLE 设备身份识别 |
| `mic_watch.py` / `mic_logic.py` | 麦克风热插拔提示 |
| `fairy_device.py` | 设备插拔的"该说什么"（原 CNSH，已改手写） |
| `fairy_logic.py` | 问候/穿衣/语气/天气点评（原 CNSH，已改手写） |
| `music_info.py` | 当前在放什么歌 |
| `video_subtitle.py` | 视频实时字幕 |
| `call_translate.py` | 通话翻译 |
| `health_check.py` | 体检（软件层 + 硬件层） |
| `cpu_watch.py` | CPU 与硬件错误（WHEA）趋势 |
| `bootstrap.py` | 首次安装与自检 |

### 4.6 素材管线与测试

| 模块 | 职责 |
|---|---|
| `build_assets.py` | 构建球的帧图素材 |
| `build_voice_index.py` / `extend_voice_index.py` | 构建/扩充语音索引 |
| `build_line_pools.py` | 从语料库机械构建"执行提示"台词池 |

> 完整模块清单见 [`modules.md`](modules.md)。

---

## 五、几个关键设计取舍（"为什么不那样做"）

代码注释里散落着很多"为什么"，这里汇总：

### 5.1 为什么用 900 ms 轮询，不用 WebSocket？

**简单、可断线自愈。** 球挂一下、内核（DSH 或 fairy_host）重启一下，下一代轮询就恢复了，
不用处理重连状态机。900 ms 对人的对话节奏够用。

### 5.2 为什么用 `seq` 去重，不用比较文本？

**文本可能重复**（同一句话出现两次），用文本比较会漏。
`seq` 单调递增，只要"大于上次"就念，逻辑简单且不会漏。

### 5.3 为什么首次轮询只建立基线、不补念？

**避免球一启动就把历史回复全念一遍。** 用户重启球时不想听旧消息。

### 5.4 为什么回复要截断到 150 字？

**用户明确讨厌啰嗦。** 而且语音是线性的 —— 500 字念出来要几分钟。
截断时**尽量断在句末**，不要在句子中间断。

### 5.5 为什么缓存的文件夹名要带 `cache_` 前缀？

**否则会污染审计统计。** `<hash>.wav` 会被 `voice_audit.py` 的正则
误认成"游戏原声"，导致四类路线永远分不开。

### 5.6 为什么合成服务要单独保活？

**它挂了，声音就掉成通用音色** —— 用户会立刻察觉（"这不是她的声音"）。
而它的失败是**静默的**（球会降级，用户只听到音色变了）。
所以需要外部进程盯着。

### 5.7 为什么音频设备要单独一个工具？

**Windows 的默认播放设备会被外部抢走**（最典型：用蓝牙麦克风会连带把输出切到耳机）。
而这个切换**不会自动还原**。所以需要能读、能存、能还原的工具。

### 5.8 为什么有的脚本要用 `pythonw` 跑、有的要重定向 stdout？

`pythonw` 没有控制台，**`print()` 会被直接丢弃**。
球平时是 `pythonw`（不弹黑框），**代价是没日志**。
所以 `fairy.py start ball` 会主动把 stdout 重定向到文件 —— **排查问题必须这样启动**。

---

## 六、扩展指引

| 想做的事 | 改哪里 |
|---|---|
| 换内核（不用 DSH / 换本地内核） | 改 `fairy_host.py` 的内核适配；外形层只认 `/api/fairy/*` 契约，不用改 |
| 换形象（不做球了） | 写一个新的"外形层"程序，实现 `/api/fairy/reply` 轮询 + `/api/fairy/say` 调用 |
| 换 TTS（不用 IndexTTS） | 换掉 `indextts_*`，实现 `/tts` 契约（文本进、WAV 出） |
| 换唤醒词 | 改 `<FAIRY_ROOT>\wakeword\kw\` 下的关键词文件（**不用训练**，见 `wakeword.md`） |
| 加一个"按需命令" | 在 `fairy.py` 的 `CMDS` 字典里加一行 |
| 加一个常驻组件 | 在 `fairy.py` 的 `COMPONENTS` 里加一项 + 在 `fairy.json` 的 `components` 里加开关 |
| 调语音让位 | 已在代码里（`media_duck.py` / `volume_duck.py`）；原 `audio_policy.json` 已删（死配置） |
| 调让位等级 | 同上 |
