# 模块清单

`<FAIRY_ROOT>\tools\` 下共 **58 个 `.py`**（另有 `.bat` / `.yaml` / `.html` / 文档；下表为常用模块，完整清单以目录为准）。
下表逐个说明：**一句话职责 + 关键点 + 什么时候用得上**。

> 大小取自实测；职责来自各文件实现。

---

## 一、入口与总管

| 模块 | 大小 | 职责 | 关键点 |
|---|---|---|---|
| **`fairy.py`** | 12.2 KB | ★ **唯一入口/总管** | `COMPONENTS`（4 个常驻组件）+ `CMDS`（16 个按需命令）；`start/stop/restart/status/log/config`；**启动时接管 stdout 到日志** |

---

## 二、外形层（门面）

| 模块 | 大小 | 职责 | 关键点 |
|---|---|---|---|
| **`fairy_ball.py`** | 88.4 KB | ★ **桌面悬浮球** —— 球 + 进度条面板 + 气泡 + **语音队列** + 设备监听 + 余额 + 让位 | 分层窗口渲染；`POLL_MS=900` 轮询；`_worker` 是语音四层降级；常量 `PLAY_GAME_ORIGINAL` 在这；**外观素材路径 `ASSETS` 硬编码（第 47 行）`load_frames()` 有占位回退 —— 缺素材用纯色圆占位，不崩** |
| `fairy_ear.py` | 32.9 KB | 耳朵：常驻监听 + 端点检测 + 唤醒 + 连续对话 | ⚠️ **唤醒仍是"门限+whisper"老方案，KWS 待集成**；`--run/--list/--selftest/--selftest-e2e/--status-json/--stop/--no-dialogue/--stt-mode` |
| `fairy_wake_listen.py` | 8.8 KB | 过渡版"喊了就应" | **双引擎 KWS**（中/英）；命中后用**克隆音色**应答；**每 3 秒打一行麦克风音量**（排障关键） |

---

## 三、语音层

| 模块 | 大小 | 职责 | 关键点 |
|---|---|---|---|
| `indextts_server.py` | 26.4 KB | 合成服务本体（HTTP 包装 + 启动预热） | 监听 `:9881`；路由 `/health`、`/tts`、`/api/v1/{voices,tts/tasks,upload}`；启动后主动预热 |
| `indextts_start.py` | 3.8 KB | **一次性启动器** | 按 `CANDIDATES` 找安装 → 后台起服务 → **阻塞**轮询 `/health`（最长 900 s） |
| **`indextts_guard.py`** | 13.4 KB | ★ **保活看门狗** | 15 s 探活 / 30 s 冷却 / **拉起后自动暖机**；`--run/--once/--status-json/--stop` |
| **`voice_cache.py`** | 34.8 KB | ★ **预合成缓存** | `--build/--status/--verify/--export/--audit/--collect/--clean/--build-one/--force/--limit/--suspect`；**可断点续跑**、索引**原子写** |
| **`voice_audit.py`** | 5.2 KB | ★ **语音路由审计** | 从日志统计四类路线占比；`--tail N` / `--watch N` |
| `voice_timing.py` | 3.1 KB | 量真实合成耗时（按文本长度分档） | 短(<=10字)/中(11~20)/长(21~45)；**量过的句子会一并写入缓存** |
| `fairy_audio.py` | 8.9 KB | 默认音频输出设备管理 | `current/list/set/save/restore/guard-on`；Core Audio `IPolicyConfig`；**只对激活中的设备切换** |
| `media_duck.py` | 20.3 KB | 媒体让位（压低正在播放的媒体） | 令牌**原子写**；读不到令牌时当"无需恢复" |
| `volume_duck.py` | 20.7 KB | 音量让位（压系统音量） | 同样的原子写；原 `except: pass` 已修为**必须出声** |

---

## 四、唤醒

| 模块 | 大小 | 职责 | 关键点 |
|---|---|---|---|
| `kws_mic_test2.py` | 6.1 KB | ★ **双引擎唤醒实测**（真麦克风） | 同时跑中文/英文模型；用法 `... 90`（听 90 秒） |
| `kws_mic_test.py` | 6.2 KB | 单引擎唤醒实测 | `... 90` 或 `... 90 zh` |
| `wake_logic.py` | 3.1 KB | 唤醒判定逻辑（手写） | **原 CNSH 版本，已改写为纯 Python**；导出 `该唤醒/应答序号/该提示设备/声音够大/设备得分` |

---

## 五、播报与记忆

| 模块 | 大小 | 职责 | 关键点 |
|---|---|---|---|
| `briefing.py` | 25.4 KB | 开机播报（问候 + 天气 + 新闻） | 读 `briefing.json`；`--news` / `--json` |
| `work_advisor.py` | 54.8 KB | 实时工作建议 | 可独立常驻（球没接时的备用）；Ollama 调用**必须带 `keep_alive`** |
| `balance_watch.py` | 5.0 KB | API 余额监控与预警 | **跨档才说**（避免每 10 分钟念一遍）；`--json` |

---

## 六、设备

| 模块 | 大小 | 职责 | 关键点 |
|---|---|---|---|
| `device_adapters.py` | 17.3 KB | 设备接入统一适配层 | 各适配器可并行发现 |
| `nearby.py` | 25.0 KB | 附近设备发现/识别/监控 | 输出给人/给智能体看的中文摘要 |
| `ble_identify.py` | 9.6 KB | BLE 设备身份识别 | 增量保存用**原子写** |
| `mic_watch.py` | 13.8 KB | 麦克风热插拔监视 | **逐个试开**判断"真能用"；`--once/--run/--selftest/--status-json/--verbose/--seconds` |
| `mic_logic.py` | 2.5 KB | 麦克风判断逻辑（手写） | 启动 10 s 内不提示 / 回环不提示 / 非真麦克风不提示 / 60 s 不重复；**原 CNSH，已改写** |
| `fairy_device.py` | 2.3 KB | 设备插拔"该说什么"（手写） | 已说过的 20 s 内不重复；**原 CNSH，已改写** |
| `fairy_logic.py` | 2.7 KB | 问候/穿衣/语气/天气点评（手写） | 被 `briefing.py` 调用；**原 CNSH，已改写** |

---

## 七、媒体

| 模块 | 大小 | 职责 | 关键点 |
|---|---|---|---|
| `music_info.py` | 22.9 KB | 当前在放什么歌 | `--selftest`；返回 `{now, items, changed}` |
| `video_subtitle.py` | 25.4 KB | 视频实时字幕 | `--run --translate --subtitle`；依赖 faster-whisper |
| `call_translate.py` | 26.2 KB | 通话翻译 | `--run`；可 `--speak` 播到默认设备 |

---

## 八、系统

| 模块 | 大小 | 职责 | 关键点 |
|---|---|---|---|
| `health_check.py` | 41.1 KB | 体检（软件层 + 硬件层） | `--fast` 跳过最慢项（约省 3 秒）；`--full/--quiet/--speak` |
| `bootstrap.py` | 32.3 KB | 首次安装与自检 | `--full/--quick/--dry/--auto/--status/--speak`；**实测探测，不靠名单匹配** |
| `cpu_watch.py` | 8.9 KB | CPU 与硬件错误（WHEA）趋势 | 采集一次事实并对比历史 |

---

## 九、语音素材管线

| 模块 | 大小 | 职责 |
|---|---|---|
| `build_voice_index.py` | 4.1 KB | 从音频目录构建 `voice_index.json` |
| `extend_voice_index.py` | 6.0 KB | 增量扩充索引（去标点空白作为触发键） |
| `build_line_pools.py` | 7.0 KB | 从语料库**机械**构建"执行提示"台词池（9 个池 / 380 句） |
| `build_assets.py` | 6.2 KB | 构建球的帧图素材 |
| `indextts_fetch25.py` | 4.2 KB | 补齐 IndexTTS 2.5 模型文件（按尺寸比对，只拉不匹配的） |
| `indextts_fix_aux.py` | 4.2 KB | 把辅助权重拼装成官方要的扁平结构 |

> ⚠️ 这些是**一次性的素材准备工具**，日常运行不需要它们。

---


---

## 十一、不属于运行时（可以忽略）

| 文件 | 说明 |
|---|---|
| `<FAIRY_ROOT>\fairy.py` | ★ **单文件球程序（可选入口）**：主要功能已并入 `tools/fairy.py` 总管 + `tools/fairy_ball.py`，保留供单文件使用。 |
| `tools\archive\` | 归档脚本（含已弃用的 `mic_watch.py` 旧版） |
| `tools\*.bak_before_*` | ★ **历史备份，共 15 个，不要提交到仓库** |
| `tools\comfy_extra_model_paths.yaml` | ComfyUI 的模型路径配置（供外部使用） |

---

## 十二、模块总表（按大小降序，供快速定位）

| # | 模块 | KB | 分组 |
|---|---|---|---|
| 1 | `fairy_ball.py` | 88.4 | 外形 |
| 2 | `work_advisor.py` | 54.8 | 播报 |
| 3 | `health_check.py` | 41.1 | 系统 |
| 4 | `voice_cache.py` | 34.8 | 语音 |
| 5 | `fairy_ear.py` | 32.9 | 外形 |
| 6 | `bootstrap.py` | 32.3 | 系统 |
| 7 | `indextts_server.py` | 26.4 | 语音 |
| 8 | `call_translate.py` | 26.2 | 媒体 |
| 9 | `briefing.py` | 25.4 | 播报 |
| 10 | `video_subtitle.py` | 25.4 | 媒体 |
| 11 | `nearby.py` | 25.0 | 设备 |
| 12 | `music_info.py` | 22.9 | 媒体 |
| 13 | `volume_duck.py` | 20.7 | 语音 |
| 14 | `media_duck.py` | 20.3 | 语音 |
| 15 | `device_adapters.py` | 17.3 | 设备 |
| 16 | `mic_watch.py` | 13.8 | 设备 |
| 17 | `indextts_guard.py` | 13.4 | 语音 |
| 18 | `fairy.py` | 12.2 | 入口 |
| 19 | `ble_identify.py` | 9.6 | 设备 |
| 20 | `cpu_watch.py` | 8.9 | 系统 |
| 21 | `fairy_audio.py` | 8.9 | 语音 |
| 22 | `fairy_wake_listen.py` | 8.8 | 外形 |
| 23 | `build_line_pools.py` | 7.0 | 管线 |
| 24 | `build_assets.py` | 6.2 | 管线 |
| 25 | `kws_mic_test.py` | 6.2 | 唤醒 |
| 26 | `kws_mic_test2.py` | 6.1 | 唤醒 |
| 27 | `extend_voice_index.py` | 6.0 | 管线 |
| 28 | `voice_audit.py` | 5.2 | 语音 |
| 29 | `balance_watch.py` | 5.0 | 播报 |
| 32 | `indextts_fetch25.py` | 4.2 | 管线 |
| 33 | `indextts_fix_aux.py` | 4.2 | 管线 |
| 34 | `build_voice_index.py` | 4.1 | 管线 |
| 35 | `indextts_start.py` | 3.8 | 语音 |
| 36 | `wake_logic.py` | 3.1 | 唤醒 |
| 37 | `fairy_logic.py` | 2.7 | 设备 |
| 38 | `mic_logic.py` | 2.5 | 设备 |
| 39 | `fairy_device.py` | 2.3 | 设备 |
| 40 | `voice_timing.py` | 3.1 | 语音 |