# 配置

虚拟桌面助手 有 **4 个配置来源**，但只有第 1 个是"主配置"。另外几个有的是子模块配置，有的**疑似已失效**。

---

## 一、★ `<项目根>\fairy.json` —— 主配置

**由 `tools/fairy.py` 读写**（常量 `CONF = r"<项目根>\fairy.json"`）。
`fairy.py config` 会**首次自动生成**它；改完**多数项下次启动生效**（不是立刻）。

### 1.1 完整结构（含默认值）

```json
{
  "_说明": "Fairy 统一配置：所有组件的开关都在这。改完保存即可（多数项下次启动生效）。",

  "components": { "ball": true, "guard": true, "tts": false },

  "ball":    { "speak": true, "bubble_seconds": 4, "brief_seconds": 18 },
  "advisor": { "enabled": true, "observe_min": 5, "min_gap_min": 10 },
  "balance": { "warn_yuan": 30, "alert_yuan": 10 },
  "news":    { "count": 0, "keyword_count": 0 }
}
```

> 上面的默认值来自 `tools/fairy.py` 的 `DEFAULT_CONF` 常量，**逐字核实**。

### 1.2 字段说明

| 字段 | 含义 | 默认 | 取值 | 要重启吗 |
|---|---|---|---|---|
| `components.ball` | 是否随 `fairy.py start` 启动球 | `true` | 布尔 | 是（只影响 `start`） |
| `components.tts` | 是否随 `start` 启动语音合成服务。**★ 建议保持 `false`** —— 改 `true` 会与 `guard` 重复拉起（实测 4 个进程） | `false` | 布尔 | 是 |
| `components.guard` | 是否启动音色保活守护 | `true` | 布尔 | 是 |
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

### 1.3 ★ 三个必须知道的坑

**坑 1：`components.tts` 默认是 `false` —— ★ 不要把它改成 `true`**

`DEFAULT_CONF` 的 `components` 是 `{ball, guard, tts}`，`tts` 默认 **`false`**。

**这个默认值是故意的，不是漏配。** 理由（2026-10-09 实测）：

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
    py -3.12 <项目根>\tools\fairy.py start tts
  （或直接 `fairy.py start`，让 guard 自动拉起 —— 两者不要同时用）
· 之后：交给 guard 保活，它挂了会自己拉起来，**不用管 tts 开关**
· 想单启合成服务：`fairy.py start tts`（显式调用，不冲突）
```

**一句话**：**`tts` 开关留着 `false`，让 `guard` 负责拉起与保活。**


**坑 2：配置文件"缺键"是正常的**

`load_conf()` 的补全逻辑是**递归 setdefault**：文件里缺的键会用默认值补上，
**但不会写回文件**。所以你看到 `fairy.json` 里只有少数几个组件是正常的。

**坑 3：`ear` 段不存在（文档与实现不一致，待确认）**

`tools/fairy_ear.py` 的文件头写着：
> 想关掉：fairy.json 里的 `ear.enabled` 设 false，或本文件 ENABLED 改 False。

**但 `fairy.py` 的 `DEFAULT_CONF` 里根本没有 `ear` 段**，
在 `tools/` 下也没搜到读取 `ear.enabled` 的代码。

→ **结论：这条说明可能是过期的（写的时候有、后来删了），或者耳朵的开关另有实现。**
→ **要关耳朵，现在更可靠的办法是直接改 `fairy_ear.py` 顶部的 `ENABLED` 常量，或者干脆不起它。**
→ **（标"待确认" —— 没有实际验证过改这个方法是否生效。）**

---

## 二、`<项目根>\briefing.json` —— 播报配置

**读取者**（已核实）：
- `briefing.py`（`CONF = r"<项目根>\briefing.json"`）
- `fairy_ball.py`（写回：`设置并确认位置（写进 briefing.json）`）
- `health_check.py`（`CONF = r"<项目根>\briefing.json"`）

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
| `lat` / `lon` | `35.7564` / `115.0436` | 纬度 / 经度 |

**改城市**：改 `city`（并把 `city_confirmed` 设 `true`），或让球重新定位。

---

## 三、`<项目根>\audio_policy.json` —— 音频让位策略

> ★ 2026-10-11：本文件（`<FAIRY_ROOT>\audio_policy.json`）**已删除**。下面记录的是历史情况与判定依据（它从来没有生效过）。

**内容**：

| 字段 | 例值 | 含义 |
|---|---|---|
| `_说明` | `"改完立刻生效，不用重启。"` | 自述 |
| `enabled` | `true` | 总开关 |
| `levels.P0` | `0.2` | 紧急：压到 20% |
| `levels.P1` | `0.35` | 任务：压到 35% |
| `levels.P2` | `1.0` | 过程：不压 |
| `levels.P3` | `0.3` | 日常：压到 30% |
| `watchdog_seconds` | `30` | 看门狗超时（防止永久压低） |
| `fade_steps` / `fade_step_ms` | `5` / `40` | 渐变步数与每步间隔 |
| `skip_when_voice_app` | `true` | 通话类程序在用时不让位 |
| `skip_when_game` | `false` | 游戏在跑时不让位 |
| `min_gap_seconds` | `2.0` | 两次让位最小间隔 |
| `不可压的程序` | `["Weixin.exe","QQ.exe","TencentMeeting.exe",...]` | 白名单：不压 |
| `可压的程序` | `["QQMusic.exe","cloudmusic.exe","Spotify.exe",...]` | 名单：可压 |

### ⚠️ 待确认（重要）

**在 `<项目根>\tools\` 下的全部 46 个 `.py` 里，搜 `audio_policy` / `POLICY` 都没有命中读取点。**
（`media_duck.py`、`volume_duck.py` 里也没搜到。）

→ **所以这个文件可能是「配置已写好但读取代码没接上」，或者读取逻辑在我没搜到的位置
（别的目录、别的脚本、或插件侧）。**
→ **在确认之前，不要相信改它"立刻生效"这句自述。**
→ **（实测建议：要调让位比例，先去看 `media_duck.py` / `volume_duck.py` 里的实际默认值。）**

**文件本身确实存在且格式合法**（1 个 JSON 对象），这一点是核实过的。

---

## 四、`<项目根>\config.json` —— ★ 历史遗留

**这是一个旧版"单文件智能体"的配置**，对应 `<项目根>\fairy.py`（107 KB、2574 行、
**不是** `tools/fairy.py` 这个总管）。

**已核实**：在 `tools/` 下只有 **`indextts_fix_aux.py`** 引用了 `config.json`。

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
2. **这个文件对当前架构基本是死的**（只有 `indextts_fix_aux.py` 读它）。
   **当前生效的配置是 `fairy.json`。** 新改配置**不要改这里**。

---

## 五、配置文件总览

| 文件 | 谁读 | 现在还有效吗 |
|---|---|---|
| **`fairy.json`** | `fairy.py`（读写）、`fairy_ear.py`（注释提到） | ✅ **主配置，有效** |
| `briefing.json` | `briefing.py`、`fairy_ball.py`、`health_check.py` | ✅ 有效 |
| `audio_policy.json` | **（未搜到读取点）** | ⚠️ **待确认 / 疑似失效** |
| `config.json` | 仅 `indextts_fix_aux.py` | ❌ **历史遗留，别改** |

---

## 六、依赖清单（`requirements.txt` 的实际情况）

`<项目根>\requirements.txt` 的内容是：

```
pyautogui, pywin32, requests, faster-whisper, numpy, pyaudio, edge-tts, pygame
```

**⚠️ 这个清单不完整。** 按实际 `import` 统计，至少还要补：

| 包 | 用途 | 少了会怎样 |
|---|---|---|
| `sounddevice` | 播放/采集音频 | 球不能说话、耳朵不能听 |
| `sherpa-onnx` | 唤醒词引擎 | 唤醒功能不可用 |
| `sentencepiece` | 英文唤醒词的 BPE 分词 | 生成英文关键词失败 |
| `pypinyin` | 中文唤醒词的拼音 | 生成中文关键词失败 |
| `psutil` | CPU/内存上报 | 状态里的 CPU 显示缺 |
| `Pillow` | 帧图处理 | 球渲染失败 |
| `comtypes` / `pycaw` | Core Audio（音量、设备） | 让位、设备切换失败 |
| `scipy` | 图像/信号处理 | 部分素材管线失败 |
| `onnxruntime` | 模型推理 | KWS/ASR 不可用 |

> **`requirements.txt` 里写的是"Python 3.9+，推荐 3.10/3.11/3.12"**，
> 但本项目的代码和命令**全部按 3.12 写**（`py -3.12`）。**建议就直接用 3.12。**
