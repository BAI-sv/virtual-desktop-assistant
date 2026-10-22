# 智能眼镜模块（glasses）

ESP32-S3 四合一智能眼镜：AI 语音 / 拍照翻译 / 视觉问答 / 高德导航。
本目录是 FairyX 的眼镜子模块，与 `tools/` 设备适配层打通：
**FairyX 通过 `glasses_adapter` 发现【在线】的眼镜，离线一律不显示、不播报**（强扩展铁律）。

## 目录结构

| 文件 | 作用 |
|---|---|
| `ai_center.py` | 眼镜 AI 中枢（HTTP 0.0.0.0:5000，零 Flask 依赖） |
| `keys.py` | 全部密钥（★ 已被 .gitignore 忽略，严禁上传 GitHub） |
| `esp32_glasses.ino` | ESP32-S3 固件 v3.3（按键版） |
| `README.md` | 本说明 |

## 一、跑 AI 中枢（Windows 本机）

```bat
py -3.12 D:\FAIRY\glasses\ai_center.py 5000
```

自检（浏览器或 curl）：

```
GET  http://127.0.0.1:5000/health          -> {"ok":true,"service":"glasses-ai-center"}
GET  http://127.0.0.1:5000/devices         -> 设备表（含在线状态）
POST http://127.0.0.1:5000/chat  {"text":"你好"} -> {"ok":true,"text":"...","provider":"zhipu"}
POST http://127.0.0.1:5000/translate {"text":"hello","to":"zh"}
POST http://127.0.0.1:5000/tts     {"text":"你好"} -> audio/wav
POST http://127.0.0.1:5000/vqa?mode=look  (raw JPEG body)
POST http://127.0.0.1:5000/nav?destination=范县东收费站
POST http://127.0.0.1:5000/device/register {"device_id":"x","name":"智能眼镜","type":"glasses"}
```

能力与免费 API（全部实测通）：

| 能力 | 实现 | 说明 |
|---|---|---|
| /chat 对话 | DSH → 智谱 glm-4.5-flash → 豆包 Doubao-Seed-2.1-turbo | 三级兜底，免费额度 |
| /asr 语音识别 | 本机 SenseVoice/Paraformer（asr_bridge） | 离线免费 |
| /translate | 百度翻译标准版 | 免费 |
| /tts 语音合成 | edge-tts（zh-CN-XiaoxiaoNeural） | 微软免费 |
| /vqa 视觉 | 豆包 turbo → 本机 Qwen3-VL(8085) | mode=look/shop/tr |
| /nav 导航 | 高德驾车（自动地理编码） | 免费配额 |

## 二、烧录固件

1. Arduino IDE 装 ESP32 开发板（esp32 by Espressif，S3 支持）
2. 库管理器装：`ArduinoJson`（bblanchon）
3. 打开 `esp32_glasses.ino`，改头部配置：
   - `WIFI_SSID / WIFI_PASS`：你的 WiFi
   - `CENTER_HOST / CENTER_PORT`：跑 ai_center.py 那台机器的 IP（4G 下填公网 IP/域名）
4. 选板 `ESP32S3 Dev Module`，USB 烧录
5. 串口监视器看日志：开机自动连 WiFi，失败自动拨 4G（SIM7xxx 系 AT）

引脚默认（ESP32-S3）：

| 功能 | GPIO |
|---|---|
| 按键 | 4 |
| I2S 时钟 BCK / WS | 41 / 42 |
| 麦克风 INMP441（IN） | 2 |
| 功放 MAX98357A（OUT） | 3 |
| 摄像头（OV2640） | 15/16/17/18/12/10/8/9/11/13/6/7/4/5 |
| 4G 模块 TX / RX | 17 / 16 |

## 三、按键操作（v3.3）

| 操作 | 功能 |
|---|---|
| 短按 | 识别当前画面并翻译文字，播报 |
| 双击 | 语音对话（录 5 秒 → 识别 → 回答 → 播放） |
| 长按 0.8~1.5 秒 | 高德导航到范县东收费站（播报里程/时长/前 3 条指令） |
| 长按 1.5~3 秒 | 购物点评（拍商品，点评值不值） |
| 长按 >3 秒 | 看图说话（详细描述画面） |

## 四、与 FairyX 的融合

已接入的部件（本目录外）：

- `D:\FAIRY\tools\glasses_adapter.py`：智能眼镜适配器（device_adapters 契约四件套）
- `D:\FAIRY\tools\device_adapters.py`：懒加载注册 `glasses`（与 robot 同模式）
- 发现链路：`fairy_extend --run` → `discover_all()` → glasses_adapter → 中枢 `GET /devices` → 只上报**在线**眼镜 → 设备池 `data/devices.json`

强扩展规则（全设备统一）：

1. 眼镜上线（开机/心跳）→ 出现在设备池 → FairyX 才知道它 → 才播报
2. 眼镜离线（60 秒无心跳）→ 从设备池下线 → 不播报、不显示操作项
3. 4G 模式只支持文本能力（AT 传图不可行），拍照功能会提示连 WiFi

## 五、安全

- `keys.py` 含全部密钥，已在 `.gitignore` 忽略（`/glasses/keys.py`）
- 不要把密钥贴进聊天、截图发群或推 GitHub
- 中枢地址 `0.0.0.0:5000` 对局域网开放；只本机用可改 `127.0.0.1`
