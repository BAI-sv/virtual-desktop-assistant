# 智能眼镜接入（GLASSES）—— FairyX 外接设备规范

> 接入时间：2026-10-22 · 版本：v3.3（按键版）
> 设计遵循项目铁律：**设备连接上了 FairyX 才知道它、才显示、才播报；离线一律不出现。**

## 一、架构

```
┌─────────────┐  WiFi/4G   ┌──────────────────┐   HTTP     ┌─────────────────────────┐
│  ESP32-S3   │ ─────────► │  ai_center.py    │ ◄────────► │  FairyX（本机）          │
│  摄像头/麦/键 │  注册+心跳  │  (D:\FAIRY\glasses)│  /devices  │  tools/glasses_adapter.py │
│  喇叭        │            │  /asr /chat/tts  │            │  device_adapters.ADAPTERS │
└─────────────┘            │  /vqa /nav/翻译   │            │  fairy_extend → 设备池    │
                           └──────────────────┘            └─────────────────────────┘
```

- 眼镜开机 `POST /device/register`，之后每 30 秒心跳
- 中枢 `GET /devices` 实时算在线（60 秒无心跳 = 离线）
- `glasses_adapter.discover()` 只上报 **online=true** 的设备 → 进 `data/devices.json` 设备池
- FairyX 播报/操作项只在设备在线时出现（与机器人/汽车等外接扩展同一套强扩展规则）

## 二、文件清单

| 路径 | 作用 |
|---|---|
| `glasses/ai_center.py` | 眼镜 AI 中枢（HTTP 0.0.0.0:5000，标准库+requests，零 Flask 依赖） |
| `glasses/keys.py` | 全部密钥（★ `.gitignore` 已忽略，严禁上传） |
| `glasses/esp32_glasses.ino` | ESP32-S3 固件 v3.3（按键版） |
| `glasses/README.md` | 眼镜模块自述（部署/烧录/操作） |
| `tools/glasses_adapter.py` | 智能眼镜适配器（device_adapters 契约四件套） |
| `tools/device_adapters.py` | 懒加载注册 `glasses`（与 robot 同模式，失败留痕不静默） |

## 三、部署三步

1. **跑中枢**：`py -3.12 D:\FAIRY\glasses\ai_center.py 5000`
   （自检：`GET http://127.0.0.1:5000/health`）
2. **烧固件**：Arduino IDE 打开 `esp32_glasses.ino`，改头部 WiFi/中枢地址，选 `ESP32S3 Dev Module` 烧录
3. **FairyX 发现**：`py -3.12 tools\fairy_extend.py --run`（常驻）→ 设备池自动含在线眼镜

## 四、能力与免费 API

| 能力 | 实现 | 状态 |
|---|---|---|
| /chat | DSH → 智谱 glm-4.5-flash → 豆包 turbo | 三级兜底，实测通 |
| /asr | 本机 SenseVoice / Paraformer（asr_bridge） | 离线免费 |
| /translate | 百度翻译标准版 | 免费 |
| /tts | edge-tts（微软） | 免费 |
| /vqa | 豆包 turbo → 本机 Qwen3-VL(8085) | mode=look/shop/tr |
| /nav | 高德驾车（自动地理编码） | 实测通 |

★ **火山方舟 Model ID 注意**：调用必须用官方 ID（如 `doubao-seed-2-1-turbo-260628`），
裸名（`Doubao-Seed-2.1-turbo`）会 404 `InvalidEndpointOrModel`。模型池见 `keys.py`。

## 五、按键操作（v3.3）

| 操作 | 功能 |
|---|---|
| 短按 | 识别画面文字并翻译（/vqa?mode=tr） |
| 双击 | 语音对话（录 5s → /asr → /chat → /tts → 播放） |
| 长按 0.8~1.5s | 高德导航到范县东收费站 |
| 长按 1.5~3s | 购物点评（mode=shop） |
| 长按 >3s | 看图说话（mode=look） |

## 六、强扩展铁律（全设备统一，含机器人/汽车）

1. 设备上线注册（心跳）→ 进设备池 → FairyX 才知道 → 才播报
2. 设备离线（超时无心跳）→ 移出设备池 → 不播报、不显示操作项
3. 适配器只上报在线设备（`discover()` 过滤 online=false）
4. 懒加载注册：设备中枢没起/依赖缺失 → 不影响其它设备（`glasses_note()` 留痕可查）

## 七、安全

- `keys.py` 已在 `.gitignore`（`/glasses/keys.py`），`git check-ignore` 验证通过
- 中枢默认 `0.0.0.0:5000`（局域网可访问）；只本机用改 `127.0.0.1`
- 4G 模式只走文本（AT 传图不可行），拍照功能会提示连 WiFi
