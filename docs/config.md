# 配置

虚拟桌面助手 有 **3 个配置来源**，只有第 1 个是"主配置"；其余为播报配置与历史遗留。

---

## 一、★ `<FAIRY_ROOT>\fairy.json` —— 主配置

**由 `tools/fairy.py` 读写**（常量 `CONF = r"<FAIRY_ROOT>\fairy.json"`）。
`fairy.py config` 会**首次自动生成**它；改完**多数项下次启动生效**（不是立刻）。

### 1.1 完整结构（含默认值）

```json
{
  "_说明": "Fairy 统一配置：所有组件的开关都在这。改完保存即可（多数项下次启动生效）。",

  "components": { "ball": true, "guard": true, "tts": false, "host": true },

  "ball":    { "speak": true, "bubble_seconds": 4, "brief_seconds": 18 },
  "advisor": { "enabled": true, "observe_min": 5, "min_gap_min": 10 },
  "balance": { "warn_yuan": 30, "alert_yuan": 10 },
  "news":    { "count": 0, "keyword_count": 0 },

  "endpoints": { "prefer": "auto" },
  "ai": { "backend": "strata", "model": "", "base_url": "", "api_key": "" }
}
```

> 上面的默认值来自 `tools/fairy.py` 的 `DEFAULT_CONF` 常量，**逐字核实**。

### 1.2 字段说明

| 字段 | 含义 | 默认 | 取值 | 要重启吗 |
|---|---|---|---|---|
| `components.ball` | 是否随 `fairy.py start` 启动球 | `true` | 布尔 | 是（只影响 `start`） |
| `components.tts` | 是否随 `start` 启动语音合成服务。**★ 建议保持 `false`** —— 改 `true` 会与 `guard` 重复拉起（实测 4 个进程） | `false` | 布尔 | 是 |
| `components.guard` | 是否启动音色保活守护 | `true` | 布尔 | 是 |
| `components.host` | 是否启动本地服务（19388） | `true` | 布尔 | 是 |
| `ball.speak` | 球是否出声说话 | `true` | 布尔 | 是 |
| `ball.bubble_seconds` | 气泡显示时长（秒） | `4` | 数字 | 是 |
| `ball.brief_seconds` | 开机播报时长（秒） | `18` | 数字 | 是 |
| `advisor.enabled` | 是否启用实时工作建议 | `true` | 布尔 | 是 |
| `advisor.observe_min` | 观察间隔（分钟） | `5` | 数字 | 是 |
| `advisor.min_gap_min` | 两次建议最小间隔（分钟） | `10` | 数字 | 是 |
| `balance.warn_yuan` | 余额低于此值提醒（元） | `30` | 数字 | 是 |
| `balance.alert_yuan` | 余额低于此值告警（元） | `10` | 数字 | 是 |
| `news.count` | 新闻条数（`0` = 用默认） | `0` | 整数 | 是 |
| `news.keyword_count` | 关键词新闻条数 | `0` | 整数 | 是 |
| `endpoints.prefer` | 大脑模式：`auto` 自动（DSH 在线优先）；`dsh` 强制用 DSH；`local` 只用本地内核（Strata/Ollama/LLM）。可在球右键“AI 大模型 → 大脑模式”或语音“使用 DSH / 使用本地 / 自动模式”切换，切换即时生效 | `auto` | `auto`/`dsh`/`local` | 否（热读） |
| `ai.backend` | 本地推理后端：`strata`（8080）/ `ollama`（11434）/ `llm`（llama.cpp，8085）/ `custom`（自定义 OpenAI 兼容接口） | `strata` | 字符串 | 否（球菜单即改即用） |
| `ai.model` | 模型名称（留空时自动探测第一个可用模型） | `""` | 字符串 | 否 |
| `ai.base_url` | 自定义接口地址（仅 `custom` 后端用） | `""` | URL | 否 |
| `ai.api_key` | 自定义接口密钥（仅 `custom` 后端用） | `""` | 字符串 | 否 |

### 1.3 ★ 三个必须知道的坑

**坑 1：`components.tts` 默认是 `false` —— ★ 不要把它改成 `true`**

`DEFAULT_CONF` 的 `components` 是 `{ball, guard, tts, host}`，`tts` 默认 **`false`**。

**此默认值有实测依据（2026-10-09），非遗漏**：

```
start 的顺序是 ball -> guard -> tts：
  ① guard 先起 -> 它发现 9881 没在跑 -> 【拉起 IndexTTS】
     （但模型加载要几十秒，此刻还没 ready）
  ② tts 组件紧接着起 -> 它一检查，发现 server 还没 ready -> 【又拉起一个】
-> 结果：同时存在 4 个 indextts_server 进程（正常组 2 + 多余组 2）
-> 抢 9881 端口 + 双份显存
```

**★ 正确用法**：

```
· 首次：手动起一次
    py -3.12 <FAIRY_ROOT>\tools\fairy.py start tts
  （或直接 `fairy.py start`，让 guard 自动拉起 —— 两者不要同时用）
· 之后：交给 guard 保活，它挂了会自己拉起来，**不用管 tts 开关**
· 想单启合成服务：`fairy.py start tts`（显式调用，不冲突）
```

**一句话**：**`tts` 开关留着 `false`，让 `guard` 负责拉起与保活。**


**坑 2：配置文件"缺键"是正常的**

`load_conf()` 的补全逻辑是**递归 setdefault**：文件里缺的键会用默认值补上，
**但不会写回文件**。所以你看到 `fairy.json` 里只有少数几个组件是正常的。

**坑 3：`ear` 开关是 `fairy_ear.py` 内置的，不在 `fairy.json` 里**

`fairy.py` 的 `DEFAULT_CONF` 没有 `ear` 段，耳朵也不随 `fairy.py start` 启动（独立进程）。
`tools/fairy_ear.py` 的开关是它自己内置的 `CFG["enabled"]`（默认 `true`，第 27 行），**不读 `fairy.json`**。

**想关耳朵**：把 `fairy_ear.py` 顶部 `CFG` 里的 `"enabled"` 改成 `false`；或干脆不启动它。

---

## 二、`<FAIRY_ROOT>\briefing.json` —— 播报配置

**读取者**（已核实）：
- `briefing.py`（`CONF = r"<FAIRY_ROOT>\briefing.json"`）
- `fairy_ball.py`（写回：`设置并确认位置（写进 briefing.json）`）
- `health_check.py`（`CONF = r"<FAIRY_ROOT>\briefing.json"`）

### 2.1 实际字段

| 字段 | 例值 | 含义 |
|---|---|---|
| `enabled` | `true` | 播报总开关 |
| `city` | `"北京市朝阳区"` | 已确定/确认的城市（示例值） |
| `city_confirmed` | `true` | 城市是否已被用户确认过 |
| `auto_city` | `"郑州市"` | IP 自动定位到的城市 |
| `keywords` | `["AI","人工智能","游戏",...]` | 关心的新闻关键词 |
| `news_count` | `0` | 新闻条数（0 = 默认） |
| `keyword_news_count` | `0` | 关键词新闻条数 |
| `headline_max_chars` | `26` | 标题截断长度 |
| `rss` | `["https://www.chinanews.com.cn/rss/scroll-news.xml", ...]` | RSS 源列表 |

**改城市**：改 `city`（并把 `city_confirmed` 设 `true`），或让球重新定位。

---

## 三、`<FAIRY_ROOT>\config.json` —— ★ 历史遗留

**这是旧版"单文件智能体"的配置**，对应 `<FAIRY_ROOT>\fairy.py`（**不是** `tools/fairy.py` 这个总管）。

**仍会引用它的脚本**：`capability.py`、`fairy_apps.py`、`fairy_deploy.py`、`indextts_fix_aux.py`、`pack_fairyx.py`。

**它的内容非常丰富**（brain / voice / floating_ball / comfy / safety），例如：

| 段 | 关键字段 |
|---|---|
| `brain` | `provider`、`deepseek.{api_key,base_url,model,max_tokens,timeout}`、`ollama.{base_url,model}`、`system_prompt`、`history_limit`、`providers[]`（大脑优先级链） |
| `voice` | `enabled`、`asr_mode`、`whisper.*`、`tts_voice`、`fairy_pack.*`（含 `min_score`、`max_reply_len`）、`profiles.{fairy,custom}.*`、`engines.{indextts,gpstts,edge}.*` |
| `floating_ball` | `size`、`position_x`、`position_y` |
| `comfy` | `base_url`、`checkpoint`、`width/height/steps/cfg`、`save_dir` |
| `safety` | `confirm_dangerous` |

**⚠️ 两点提醒**：

1. **`brain.deepseek.api_key` 的值是 `"sk-local-llama"`** —— 这是**本地 llama-server 的占位串，不是真实密钥**。
   但它说明**这个字段会被当 key 用** —— **不要把自己的真 key 填进去然后误提交。**
2. **这个文件对当前架构基本是死的**。**当前生效的配置是 `fairy.json`。** 新改配置**不要改这里**。

---

## 四、配置文件总览

| 文件 | 谁读 | 现在还有效吗 |
|---|---|---|
| **`fairy.json`** | `fairy.py`（读写） | ✅ **主配置，有效** |
| `briefing.json` | `briefing.py`、`fairy_ball.py`、`health_check.py` | ✅ 有效 |
| `config.json` | `capability.py`、`fairy_apps.py`、`fairy_deploy.py`、`indextts_fix_aux.py`、`pack_fairyx.py` | ❌ **历史遗留，别改** |

---

## 五、依赖清单（`requirements.txt`）

`<FAIRY_ROOT>\requirements.txt` 按当前架构的实际 `import` 统计重算（2026-10-10），分组如下：

| 分组 | 包含 |
|---|---|
| 核心必装 | `numpy`、`Pillow`、`psutil`、`requests`、`PyAudio`、`pygame`、`PyAutoGUI`、`pywin32`、`edge-tts`、`faster-whisper` |
| 功能可选 | `sherpa-onnx`（KWS 唤醒）、`sounddevice`、`PyAudioWPatch`（回环录音）、`shazamio`（听歌识曲）、`winrt-Windows.Media.Control`（媒体监听）、`zeroconf`（设备发现）、`bleak`（BLE） |
| 重依赖 / 按需 | `scipy`、`torch`、`opencv-python`、`pyserial`（requirements.txt 中已注释，按需安装） |

> 代码与命令按 Python 3.12 编写（`py -3.12`），建议直接使用 3.12。