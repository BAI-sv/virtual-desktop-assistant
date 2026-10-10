---
layout: default
title: FairyX — 本地 AI 桌面助手
---

# FairyX — 本地 AI 桌面助手

> 完全本地的 Windows 桌面 AI 助手：**桌面悬浮球 ＋ 本地大脑（Ollama / llama.cpp / 任意 OpenAI
> 兼容接口）＋ 语音（唤醒 / 离线转写 / 音色克隆）＋ 设备发现 ＋ 全盘秒级扫描 ＋ 模型自适应部署
> ＋ 实时联动（天气/代码纠错/文案/翻译/视频字幕/歌词/听歌识曲/视觉看图）＋ 窗口对接**，
> 全部离线可用；不依赖任何外部内核。

**© 米哈游版权所有** · 《绝区零》素材的权利归米哈游所有，其他内容的相关权利、利益均归各自所有者享有。
本项目与米哈游无任何隶属、合作、赞助、授权或背书关系。

---

## 两个入口

| 入口 | 怎么启动 |
|---|---|
| **新版（推荐）**：`tools/fairy.py` 启动器 ＋ `tools/fairy_ball.py` 桌面球 | `py -3.12 tools/fairy.py start` |
| 旧版单文件程序：根目录 `fairy.py` | `py -3.12 fairy.py` |

## 功能

| 模块 | 能力 |
|---|---|
| `tools/fairy.py` | 启动器：start/stop/restart/status ＋ 15 个按需命令 |
| `tools/fairy_ball.py` | 桌面球：动画、进度面板、悬停气泡、右键菜单、语音、内存/显存/CPU 硬件条 |
| `tools/fairy_ui.py` ＋ `strata_ui.html` | 助手窗口（三栏中文前端 + 反向代理） |
| `tools/fairy_host.py` / `fairy_endpoints.py` | 本地服务（19388）与寻址降级中心 |
| `tools/fairy_ear.py` / `fairy_wake_listen.py` | 唤醒词（Sherpa-ONNX KWS）+ 离线转写 + 语音授权 |
| `tools/indextts_*.py` / `voice_cache.py` | 克隆音色合成、守护自愈、预合成缓存与音色审计 |
| `tools/window_adapters.py` | 窗口对接：读内容 / 代打字（默认只读 + 六道安全闸） |
| `tools/device_adapters.py` / `nearby.py` / `ble_identify.py` | 设备发现与统一适配（BLE / mDNS / 串口 / 局域网） |
| `tools/fairy_scan.py` / `fairy_deploy.py` | 4 秒全盘扫描、显存自适应模型部署 |
| `tools/fairy_plugins.py` / `briefing.py` / `music_info.py` / `video_subtitle.py` / `call_translate.py` | 天气 / 代码纠错 / 文案 / 翻译 / 字幕 / 歌词 / 识曲 / 视觉 |
| `tools/fairy_apps.py` | 26 类软件调度、绿色软件发现、写代码查错 |
| `tools/health_check.py` | 全栈体检 |
| `fairy-plugin/` | DeepSeek Harness 插件（**可选**，不装也能跑） |

## 快速开始

```bash
pip install -r requirements.txt
copy config.example.json config.json          # 大脑/语音（旧版入口用）
copy fairy.example.json fairy.json            # 组件开关/寻址/窗口白名单（新版用）
copy fairy_root.example.json fairy_root.json  # 路径（可选，不配也能自动推断）
py -3.12 tools/fairy.py start                 # 新版
```

首次启动弹窗询问授权（扫描/安装/系统更改，默认全关）；也可语音说「Fairy 授权」。
**本仓库不含任何素材**：缺球的外观帧图时自动使用**占位外观**（纯色圆），不会崩；
语音参考音与模型权重均自备（见 `voice/README.md`）。

## 许可

- 代码与文档：**CC BY-NC-SA 4.0**（署名-非商业-相同方式共享）
- 本仓库**不包含**任何第三方素材（语音/图片/动画/模型权重）
- 非官方接口（网易云歌词、Shazam 识曲）仅供学习研究，请遵守对应平台服务条款

---

完整说明见 [README.md](README.md) · 更新记录 [CHANGELOG.md](CHANGELOG.md) · 许可证 [LICENSE.md](LICENSE.md) · 版权声明 [NOTICE.md](NOTICE.md)
