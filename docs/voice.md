# 语音与音色

> 本文说明 虚拟桌面助手 的语音是怎么发出来的、音色怎么配、出问题怎么查。

---

## 一、四层降级链

球说话时，`fairy_ball.py` 的 `_worker()` 按**从好到差**的顺序依次尝试：

```
队列拿到一句话 text
   │
   ├─① 语料原声直出 ─ 命中语料库 → 播原文件
   │                 ★ 默认【已停用】，见第四节
   │
   ├─② 预合成缓存 ─── 命中 cache_<hash>.wav → 直接播
   │                 零延迟、不依赖合成服务在线
   │
   ├─③ 实时克隆 ───── POST /api/fairy/say → 拿到 WAV → 播
   │                 任意文本都能说，3~5 秒/句
   │
   ├─④ 原声兜底 ───── 克隆失败时随机播一句语料原声
   │                 ★ 默认【已停用】
   │
   └─⑤ Edge TTS ───── POST /dsh-tts-api/speak → MP3 → 播
                     保命用。音色会变成通用中文音色。
```

**每一层为什么存在**

| 层 | 存在理由 | 代价 |
|---|---|---|
| ① 原声直出 | 100% 是目标音色、零合成成本 | 只覆盖预先有素材的句子；★ 涉及素材版权 |
| ② 缓存 | 零延迟、服务挂了也能说 | 需要预先合成；只覆盖固定话术 |
| ③ 实时克隆 | 任意文本 | 3~5 秒延迟 |
| ④ 原声兜底 | 不退回通用音色 | 同上①；★ 涉及素材版权 |
| ⑤ Edge | 宁可音色不对也不能哑巴 | 音色不是目标音色 |

**现在的实际顺序是**：`② → ③ → ⑤`（①④已被 `PLAY_GAME_ORIGINAL = False` 关掉）。

---

## 二、★ 日志判据（最有用的一节）

`voice_audit.py` 靠这些字符串区分路线（来自其实际输出）：

| 日志里出现 | 含义 |
|---|---|
| `播放 <16位hex>.wav` | **游戏原声直出**（已停用；若还出现说明开关没生效） |
| `播放 cache_<hash>.wav` | **预合成缓存** ✓ |
| `播放 fairy_say.wav` | **实时克隆** ✓ |
| `播放 fairy_say.mp3` | ★ **Edge 通用音色** ← **要极力避免的** |
| `语音=克隆路由(indextts-clone) N 字节` | 克隆生成成功 |
| `语音=缓存(<hash>) <文本前12字>` | 缓存命中 |
| `语音=原声兜底（克隆不可用，拒绝退回 Edge）<文件>` | 走了原声兜底 |
| `★★ 语音=Edge兜底（警告：原声兜底也失败）text=...` | ★ **最坏情况，前面四层全废** |

### ⚠️ 一个非常容易忽略的坑

**不要用 `edge` 关键字去搜日志。**
球的日志里**根本不写 "edge" 这个词** —— Edge 只体现在**文件扩展名 `.mp3`** 上。

> 注意：搜 `edge` 得到 0 次并不代表没问题 ——
> 实际 `.mp3` 出现了 10 次 —— **声音早就掉到通用音色了。**

**正确的做法**：直接跑 `voice_audit.py`，或搜 `fairy_say.mp3`。

---

## 三、怎么用：审计 / 缓存 / 设备

### 3.1 语音路由审计（最常用的自检）

```bat
py -3.12 <FAIRY_ROOT>\tools\voice_audit.py
py -3.12 <FAIRY_ROOT>\tools\voice_audit.py --tail 200     :: 只看最近 200 行
py -3.12 <FAIRY_ROOT>\tools\voice_audit.py --watch 60     :: 监控 60 秒，每 10 秒刷新
```

输出长这样：

```
<WORKDIR>\logs\fairy_ball.py.log
  lines=62  mtime=2026-10-09 10:41:46
  GAME ORIGINAL  : 0
  CLONE cache    : 12   (pre-synthesised, cache_<hash>.wav)
  CLONE live     : 3    (.wav, generated on the fly)
  ORIG fallback  : 0
  EDGE  (.mp3)   : 0   OK
  -> game-Fairy share = 100.0%  (of 15 played)
```

**判定标准**：目标音色占比（`game-Fairy share`）越高越好，`EDGE (.mp3)` 必须为 0。

> ⚠️ 默认审计的日志路径写在脚本头部（`LOGS` 常量）。
> `fairy.py` 把日志落在 **`<WORKDIR>\logs\`**（见 `tools/fairy.py` 的 `LOG` 常量），
> 如果你换了部署位置，**要改这个常量**。

### 3.2 预合成缓存

**目的**：把"球会说的固定话术"预先生成好，播放时零延迟、且不依赖合成服务在线。

```bat
py -3.12 <FAIRY_ROOT>\tools\voice_cache.py --audit      :: 先看数字（总数/能直出/真正需要）
py -3.12 <FAIRY_ROOT>\tools\voice_cache.py --build      :: 批量合成（可断点续跑）
py -3.12 <FAIRY_ROOT>\tools\voice_cache.py --status     :: 已合成多少 / 占多大
py -3.12 <FAIRY_ROOT>\tools\voice_cache.py --verify     :: 抽 10 条验证（存在/可解码/时长合理/非空）
py -3.12 <FAIRY_ROOT>\tools\voice_cache.py --export     :: 导出若干条供人试听
py -3.12 <FAIRY_ROOT>\tools\voice_cache.py --suspect    :: 列出疑似语义不通的句子
py -3.12 <FAIRY_ROOT>\tools\voice_cache.py --clean      :: 清理失败/零字节产物
```

**产出位置**：`<FAIRY_ROOT>\voice\clone_cache\`
**文件命名**：`cache_<sha1前16位>.wav` + 一个 `index.json`

> ★ **命名为什么带 `cache_` 前缀**：如果直接叫 `<hash>.wav`，它会**被
> `voice_audit.py` 的正则误认成"游戏原声"**，导致四类路线永远分不开。
> 前缀就是为了把统计口径分开。

**关键实现细节**（改脚本时别破坏）：
- 已存在的跳过 → **可断点续跑**
- 索引**原子写**（先写 `.tmp` 再 `os.replace`）
- 失败重试 2 次，仍失败记入失败清单并继续

### 3.3 默认音频输出设备

**为什么需要专门一个工具**：Windows 上"默认播放设备"会被外部因素抢走 ——
**最典型的是：一旦某个程序用了蓝牙耳机的麦克风，Windows 会把默认输出也切到那个耳机。**

```bat
py -3.12 <FAIRY_ROOT>\tools\fairy_audio.py current            :: 现在默认是哪台
py -3.12 <FAIRY_ROOT>\tools\fairy_audio.py list               :: 列出激活中的输出设备
py -3.12 <FAIRY_ROOT>\tools\fairy_audio.py set <名称片段>      :: 切到匹配的设备
py -3.12 <FAIRY_ROOT>\tools\fairy_audio.py save                :: 记成"已知正常"
py -3.12 <FAIRY_ROOT>\tools\fairy_audio.py restore             :: 还原到刚才保存的
py -3.12 <FAIRY_ROOT>\tools\fairy_audio.py guard-on            :: 保存 + 本进程退出时自动还原
```

**实现原理**：Core Audio 的 `IPolicyConfig::SetDefaultEndpoint`（vtable 第 13 项），
通过 `ctypes` 直接调 COM，**不依赖任何第三方库**。

**★ 一条硬约束**：
**必须只对「激活中」(DEVICE_STATE_ACTIVE) 的设备切换。**
切给一个未激活的设备，`SetDefaultEndpoint` 会返回成功（`hr=0`）但**实际不生效**，
Windows 静默退回原设备。所以 `list` 分两段显示：激活中的 / 全部。

---

## 四、与游戏素材解绑（`PLAY_GAME_ORIGINAL`）

为了能公开发布，项目里有一个**总开关**，默认关闭"直接播原文件"：

```python
# <FAIRY_ROOT>\tools\fairy_ball.py   第 82 行
PLAY_GAME_ORIGINAL = False

# <FAIRY_ROOT>\tools\fairy_ear.py    第 40 行
PLAY_GAME_ORIGINAL = False
```

**关闭时（默认）**：
- 球的 ① 和 ④ 两层被跳过 → 所有语音都经过合成（缓存 / 实时克隆）
- 耳朵的应答跳过"先找语料库原声" → 直接走克隆
- **不会播放任何语料原文件**

**打开时**（改回 `True`）：恢复"命中原声就播原文件"的原始行为。

**改完要重启对应进程才生效。**

---

## 五、★ 语音自备指南

**本仓库不含任何语音文件。** 你需要自备**一段参考音**。

### ★★ 先读：合规须知（与 NOTICE.md 第二节口径一致）

> **© 米哈游版权所有**
>
> **《绝区零》素材的权利归米哈游所有，其他内容的相关权利、利益均归各自所有者享有。**

```
① 对游戏语音（角色配音 / 声音样本）的使用，【仅限于个人非商业性质使用】
② 须放置上述法律声明
③ ★★ 配音演员的声音是【独立于米哈游的另一层权利】
   米哈游官方指引明确要求："自行与该配音人员确认并获得充分、合法、有效的授权"
   ★ 本项目作者【未取得】任何配音演员的授权，因此：
     · 仓库中不包含任何克隆模型、参考音或合成产物
     · 使用者若自行克隆游戏角色音色，应【自行】取得相关权利人（含配音演员）
       的合法授权，并独立承担全部责任
     · 本项目作者不提供、不分发、不引导获取任何游戏素材或音色样本
④ 不得以商业目的出售或分发任何包含上述声音内容的作品
```

**★ 为什么 `PLAY_GAME_ORIGINAL` 默认为 `False`（见第四节）？**
这不只是技术选择 —— **不直接播放游戏原始音频文件，是降低合规风险的做法**：
播放原文件 = 在运行时使用游戏素材本体；而走克隆合成 = 使用"由使用者自备参考音生成的声音"。
**两者在法律性质上不同**，默认选后者。

**本节以下内容均为技术说明，不构成法律意见。** 使用者需自行确认并承担合规责任。

### ★ 顺带说明：球的外观素材同样不含（NOTICE 2.6）

与语音同理，**球的外观（角色形象）是米哈游的美术素材**，
**仓库是分发渠道，不能含素材本体** —— 所以 `assets/` 被排除
**是合规要求，不是缺陷**。

- 球使用的是米哈游《绝区零》中 Fairy 的角色形象，版权属米哈游。
- **开源版需要你自备外观素材**（或做一套占位外观）。
  **代码已内置"缺素材也能跑"的占位回退**（纯色圆）—— 详见
  [`README.md`](README.md) 的「外观素材也需要自备」一节（含目录、格式、帧数要求）。
- `assets/whale/`（桌宠动画）**已整体移出**；`assets/*.gif` 的来源未收录于本仓库。

---

### 5.1 参考音要求

| 项 | 要求 | 为什么 |
|---|---|---|
| **时长** | **3~10 秒** | ★ **低于 3 秒会被合成服务直接拒绝**（参考音频需在 3~10 秒范围外） |
| 内容 | 单人、连续说话 | 多人会污染音色 |
| 背景 | **无背景音乐、无环境噪音** | 会把噪声一起克隆进去 |
| 采样率 | 16 kHz 以上 | 太低会闷 |
| 格式 | wav（推荐）/ mp3 | |

**放在**：`<FAIRY_ROOT>\voice\index_voices\`

**参考现有文件的量级**（用于对照）：

| 文件 | 时长 | 采样率 | 声道 | 大小 |
|---|---|---|---|---|
| `fairy.wav` | 5.99 s | 48000 Hz | 1 | 562 KB |
| `custom.wav` | ? | ? | ? | 52 KB |
| `test_upload.wav` | ? | ? | ? | 52 KB |

### 5.2 让合成服务用你的参考音

合成服务在 `:9881`，接口：

```
GET  /health          -> {"ok":true,"device":"cuda:0","api":"infer_v2_5","loaded":true,"voices":3,"root":"..."}
POST /tts             {"text":"...", "ref_audio":"<绝对路径 .wav>", "lang":"zh", "emo_alpha":1.0}
                      -> 返回 WAV 二进制
```

**最简单的方式**：把参考音丢进 `<FAIRY_ROOT>\voice\index_voices\`，
然后调 `/tts` 时把 `ref_audio` 指向它。

**服务还兼容另一套接口**（为了对接社区的 `dsh-plugin-tts`）：

```
GET  /api/v1/voices
POST /api/v1/tts/tasks
POST /api/v1/upload
```

### 5.3 关于语料索引（可选）

如果你**有很多条**语音（不是一个参考音），可以用素材管线建索引：

```bat
py -3.12 <FAIRY_ROOT>\tools\build_voice_index.py      :: 从音频目录建 voice_index.json
py -3.12 <FAIRY_ROOT>\tools\extend_voice_index.py     :: 增量补充
py -3.12 <FAIRY_ROOT>\tools\build_line_pools.py       :: 从语料库构建"执行提示"台词池
```

> ⚠️ **注意**：`PLAY_GAME_ORIGINAL = False` 时，
> **即使你建了索引，球也不会直接播那些文件** —— 索引只在开关打开时才有用。

---

## 六、外部服务：IndexTTS

**这是"Fairy 的嗓子"。** 它是一个**独立的外部程序**，不是本仓库的代码。

| 项 | 值 |
|---|---|
| 默认端口 | `9881` |
| 界面 | 无（纯 HTTP 服务） |
| 模型加载 | 约 35 秒（GPU） |
| 首次合成 | 约 15~25 秒（JIT / 显存分配） |
| 稳态合成 | 约 3~5 秒/句 |
| 日志 | `<FAIRY_ROOT>\logs\indextts_out.log` / `indextts_err.log` |

**三个相关脚本**（这些在本仓库里）：

| 脚本 | 作用 |
|---|---|
| `indextts_start.py` | **一次性启动器**：找可用的安装 → 后台起服务 → 阻塞轮询 `/health` 直到就绪（最长 900 秒） |
| `indextts_guard.py` | **保活看门狗**：每 15 秒探活，挂了自动拉起，30 秒冷却，**拉起后自动暖机** |
| `indextts_server.py` | 服务本体（HTTP 包装 + 启动预热） |

**安装位置**（启动器会按优先级找）：

```
<INDEXTTS_ROOT>\apps\Index-TTS        （venv\Scripts\python.exe）   ← 首选
<INDEXTTS_ROOT>（便携版目录） （env\python.exe）              ← 后备
```

**★ 换安装位置**：改 `indextts_start.py` 里的 `CANDIDATES` 列表。

### 6.1 暖机（为什么需要）

```
冷启动第一次合成：15~25 秒   ← 用户会以为坏了
暖机之后再合成：  3~5 秒
```

所以 `indextts_guard.py` 在**拉起成功且 loaded=true 之后**会自动合成一句短文本并丢弃，
把耗时记到状态文件的 `warmup_s` 字段。

> 暖机失败**不算启动失败** —— 不能因为暖机报错就去重启服务。

### 6.2 保活

```bat
py -3.12 <FAIRY_ROOT>\tools\indextts_guard.py --run            :: 常驻守护
py -3.12 <FAIRY_ROOT>\tools\indextts_guard.py --once           :: 探一次，挂了就拉，然后退出
py -3.12 <FAIRY_ROOT>\tools\indextts_guard.py --status-json    :: 一行 JSON
py -3.12 <FAIRY_ROOT>\tools\indextts_guard.py --stop           :: 停守护（不杀合成服务）
```

**测试**：杀掉合成服务后，守护在 **60 秒内**自动拉回（`restarts=1`，`up=true`）。

**状态文件**：`<FAIRY_ROOT>\logs\indextts_guard.json`

> ⚠️ **一个已知的坑**：守护进程用 `Start-Process` 直接起时，
> 它自己的 stdout 没有接收方，所以 `indextts_guard.log` 可能不会生成。
> 这不影响功能（状态看 JSON 文件），但会让你以为守护没在跑 —— 用 `--status-json` 判断更可靠。

---

## 七、常见症状对照表

| 症状 | 最可能的原因 | 怎么办 |
|---|---|---|
| **完全没声音** | 默认输出被切到蓝牙耳机 | `fairy_audio.py current` → `set <正确的>` |
| **音色变成"通用中文"** | 合成服务挂了，降级到 Edge | `voice_audit.py` 看 `.mp3` 次数；`fairy.py start guard` |
| **重启后第一句卡十几秒** | 冷启动 | 等守护暖机；或手动打一次 `/tts` |
| **说话断断续续 / 被截** | 合成超时或音频被抢 | 看日志里有没有 `语音=Edge兜底` |
| **音乐被永久压低** | 让位后没恢复 | 重启球；见 `troubleshooting.md` |
| **球根本没念助手回复** | 插件路由没生效 | 插件改动**必须重启 DSH**；重启后验证回复链 |

**更多 → [`troubleshooting.md`](troubleshooting.md)**
