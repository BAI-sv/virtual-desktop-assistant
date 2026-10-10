# 更新日志

## [1.1] - 2026-10-10

**范围扩充：把「现在真正在跑的 Fairy」整套开源**（桌面球 / 本地服务 / 助手窗口 / 窗口对接 /
设备接入 / 语音克隆链 / 运维工具 / 全套文档）。旧版单文件程序 `fairy.py` 保留。

### 新增
- **桌面球** `tools/fairy_ball.py`：球体动画、进度面板（任务 / 第 N 步 / 执行方式）、
  悬停气泡（状态 / 在做 / 进度 / 刚说 / 位置）、右键菜单（本地运算开关 / 位置 /
  打开助手窗口 / 全部退出）、实时硬件条（内存 / 显存 / CPU）
- **启动器** `tools/fairy.py`：`start/stop/restart/status` ＋ 15 个按需命令
- **助手窗口** `tools/fairy_ui.py` ＋ `tools/strata_ui.html`（三栏中文前端 + 反向代理 8081）
- **本地服务与寻址** `tools/fairy_host.py`（19388）＋ `tools/fairy_endpoints.py`（降级链中心）
- **脱离启动** `tools/fairy_detach.py` / `fairy_ui_start.py` / `fairy_shutdown_all.py`
  —— 常驻件不挂在宿主进程树上（关掉宿主也不受影响）
- **窗口对接** `tools/window_adapters.py`：发现窗口 → 识别程序 → 读内容 →（授权后）打字；
  默认只读、六道安全闸、全程审计日志
- **设备接入** `tools/device_adapters.py` / `fairy_device.py` / `ble_identify.py` / `nearby.py`
- **语音链**：`tools/indextts_server.py` / `indextts_start.py` / `indextts_guard.py`（守护自愈）、
  `voice_cache.py`（预合成缓存）、`voice_audit.py`（音色审计）、
  `fairy_wake_listen.py` / `wake_logic.py` / `mic_*.py`（唤醒与麦克风即插即用）
- **音频联动** `tools/media_duck.py` / `volume_duck.py`（说话时自动暂停 / 压低正在播放的音乐）
- **运维**：`tools/health_check.py`（全栈体检，分层报告）、`bootstrap.py`（开机自举）、
  `cpu_watch.py` / `balance_watch.py`、`work_advisor.py`
- **文档**：`docs/` 全套（架构 / 配置 / 语音 / 唤醒 / 排障 / 模块表）

### 变更
- README 与 index 明确**两个入口**：新版 `tools/fairy.py`（推荐）、旧版单文件 `fairy.py`
- 缺素材**不再报错**：球自动使用**占位外观**（纯色圆），并提示素材放置路径
- `requirements.txt` 按仓库**全部 `.py` 的实际 import** 重算
- 打包白名单放行 `.html`（助手窗口前端页此前会被漏掉）
- 新增示例配置 `fairy.example.json` 与配置根示例 `fairy_root.example.json`

## [1.0.1] - 2026-10-10

### 修复
- **不再弹终端窗口**：所有起子进程的地方补上 `CREATE_NO_WINDOW`（`0x08000000`，不是 `0x00000200`），
  并把 `guard` / `tts` 等常驻件改用 `pythonw.exe` 启动 —— 之前每次启动/重起都会弹出终端窗口
- **配置根读取**：`fairy_root.json` 若被存成「带 BOM 的 UTF-8」，`json.load` 会抛异常并被静默吞掉，
  导致整个配置文件形同不存在（路径全部悄悄退回默认值）。现改用 `utf-8-sig` 读取，
  并且**读取失败必须留痕**（`_CONFIG_ERRORS` / stderr），不再静默兜底
- **语音守护**：探测失败路径上打印的符号在 GBK 控制台下会抛 `UnicodeEncodeError`，
  使守护「活着但永远拉不起语音服务」；现统一容错

### 新增
- `tools/fairy_endpoints.py`：**寻址与降级中心** —— 统一决定「本地服务 / 外部内核」用哪个，
  并给出语音合成的降级链
- 语音合成优先**直连本地 IndexTTS**（克隆音色），不再依赖外部内核在线

### 变更
- 外部内核（DeepSeek Harness）从「必需」降为**可选**：不装也能跑
  （Ollama / llama.cpp / 任意 OpenAI 兼容接口均可作为大脑）

## [1.0] - 2026-10-10

FairyX 首版发布（源代码公开版）。

### 新增
- 主程序 `fairy.py`：悬浮球界面、大脑链（Ollama → llama.cpp → 云端备用）、语音交互、授权门控
- `tools/fairy_scan.py`：4 秒全盘扫描（Everything HTTP 加速），硬件/软件/文件清单
- `tools/fairy_deploy.py`：显存自适应模型部署（独显大/中/核显档位）
- `tools/fairy_plugins.py`：实时插件——天气 / 代码纠错 / 文案检查 / 翻译 / 视频字幕 / 歌词 / 听歌识曲 / 视觉看图
- `tools/fairy_apps.py`：26 类软件调度、官方直链安装、绿色/单文件软件发现与调用、写代码+查错
- `tools/fairy_ear.py`：唤醒词（Sherpa-ONNX KWS）+ 连续语音对话 + 语音授权
- `tools/fairy_extend.py`：局域网新设备接入（HTTP 注册/调用）
- `tools/fairy_gitsync.py`：一键自动同步到 GitHub（凭据管理器认证，无需密码）

### 合规
- 许可：CC BY-NC-SA 4.0（非商业）
- 本仓库不含任何第三方素材/模型权重，语音素材自行准备（`voice/README.md`）
