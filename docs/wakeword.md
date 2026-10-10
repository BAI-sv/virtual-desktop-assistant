# 唤醒词（喊了就应）

> 方案：**Sherpa-ONNX KWS**（关键词检测）。
> 下文数据来自本机测试结果。

---

## 一、为什么不用 whisper（先说结论）

用 `faster-whisper` 做唤醒不可行：

| | whisper-small | Sherpa-ONNX KWS |
|---|---|---|
| 模型大小 | **461 MB** | **3.3 MB** |
| 加载耗时 | **63 秒** | ~0.7 秒 |
| 单次识别 | 1~2 秒 | **1.36 ms / 0.1 秒音频** |
| 内存 | ~900 MB | ~124 MB |
| 改唤醒词 | 要重训 | **改个文本文件即可** |
| 实际效果 | 把 "Fairy" 听成 `by` / `拜拜` ✗ | 命中 10/12 ✓ |

**whisper 是"语音转文字"，不是"听关键词"。** 用大模型干小模型的活，
必然又慢又错。**（这正是小艺 / 天猫精灵 / 讯飞的做法：专用小模型常听唤醒词，唤醒后才启动大模型。）**

---

## 二、★ 两个常见坑

### 坑 1：**必须流式喂**（最反直觉的一个）

同一段音频、同一个模型：

```
整段喂（一次 accept_waveform 把整段 + input_finished）  ×3  → 全不中 ✗
流式喂（每次 0.1 秒 = 1600 采样，边喂边 decode）        ×3  → 全中 ✓
```

**可复现。** 整段喂会出现"结果乱跳 0/2~2/2"，
容易被当成模型/参数问题 —— **其实是喂法错了。**

**正确写法**（和官方 demo 一致）：

```python
s = kws.create_stream()
for i in range(0, len(audio), 1600):        # 0.1 秒一块
    s.accept_waveform(16000, audio[i:i + 1600])
    while kws.is_ready(s):
        kws.decode_stream(s)
        r = kws.get_result(s)
        if r:
            # 命中
            kws.reset_stream(s)              # ★ 见坑 2
```

**音频要求**：16 kHz、单声道、float32、取值范围 -1.0 ~ 1.0。

### 坑 2：**命中后必须 `reset_stream()`**

不 reset，同一个 stream 会一直认为"刚命中过"，后续行为不正常。

---

## 三、关键词文件怎么生成

**模型词表和关键词文件必须匹配**：英文模型用 BPE，中文模型用 ppinyin（拼音）。
**两个模型的词表完全不同，不能混用。**

### 3.1 英文模型（gigaspeech）

用 `sentencepiece` 加载模型自带的 `bpe.model`：

```python
import sentencepiece as spm
sp = spm.SentencePieceProcessor()
sp.load(r"<FAIRY_ROOT>\wakeword\models\sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01\bpe.model")
pieces = sp.encode("FAIRY", out_type=str)     # ['▁FA', 'IR', 'Y']
line = " ".join(pieces) + " :1.0 @FAIRY"      # ▁FA IR Y :1.0 @FAIRY
```

**交叉验证**（确认算法对）：
`ALEXA` → `▁A LE X A`、`HELLO WORLD` → `▁HE LL O ▁WORLD`，
**与模型自带的 `keywords.txt` 逐字一致** ✓

### 3.2 中文模型（wenetspeech）★ 有坑

**官方 `sherpa-onnx-cli text2token` 在某些环境会报错**
（`TypeError: ... not 'NoneType'`）—— 那就得自己算。

**而且不能直接用 `pypinyin` 的 `Style.FINALS`**：
它给的是**底层韵母且丢声调**（瑞 → `uei`），**模型词表里没有这个 token** → 直接报错。

**正确的算法**：取整字**带调拼音**，再**剥掉声母**得**表层韵母**：

```python
from pypinyin import pinyin, Style

def ppinyin(ch):
    full = pinyin(ch, style=Style.TONE)[0][0]        # 瑞 -> ruì
    ini  = pinyin(ch, style=Style.INITIALS)[0][0]    # r
    if not ini and full[0] in "yw":                  # ★ y/w 开头要拆
        ini = full[0]                                #   艺 yì -> y + ì
    return (ini, full[len(ini):])                    # ('r', 'uì')
```

**交叉验证 7/7 与官方 `keywords.txt` 完全一致** ✓

```
你好军哥 -> n ǐ h ǎo j ūn g ē
小爱同学 -> x iǎo ài t óng x ué
小艺小艺 -> x iǎo y ì x iǎo y ì
林美丽   -> l ín m ěi l ì
```

**⚠️ 一个隐蔽的重复问题**：
`菲瑞` 和 `飞瑞` 的拼音**完全相同**（都是 `f ēi r uì`）。
**同一个词写两遍会让效果变差** —— 必须去重。

### 3.3 本项目实际在用的关键词

| 文件 | 内容 | 模型 |
|---|---|---|
| `kw\sw.txt` | `▁FA IR Y :1.0 @FAIRY` | 英文 |
| `kw\live_zh.txt` | `n ǐ h ǎo f a i r y` / `f a i r y` / `n ǐ h ǎo f ēi r uì` | 中文 |
| `kw\live_en.txt` | `▁FA IR Y :1.0 @FAIRY` | 英文 |

> ⚠️ **注意**：中文模型词表里**确实有** `f a i r y` 这些小写字母 token，
> 所以 `n ǐ h ǎo f a i r y`（你好Fairy，英文部分逐字母拼）**是合法的**。
> **但它是在中文拼音上训练的，对英文部分的识别率明显偏低** —— 更容易失败。
> **更可靠的读法是纯中文**（如"你好飞瑞"）**或纯英文**（"Fairy"）。

---

## 四、测试数据

**测试方法**：12 段**真含 Fairy 的游戏原声** + 20 段**不含 Fairy** 的音频。

| 配置 | 命中率 |
|---|---|
| **英文模型 + `FAIRY` / `HEY FAIRY`** | **10/12 = 83%** ★ 最好 |
| 中文模型 + `菲瑞` / `你好飞瑞` | 5/12 = 42% |
| **误报（20 段不含目标词的音频）** | **0/20** ✓ |

| 指标 | 数值 |
|---|---|
| 建模耗时 | 0.7 ~ 2.3 秒 |
| 处理速度 | **73.6 倍实时**（每 0.1 秒块 ≈ 1.36 ms） |
| 内存 | 加载后约 124 MB |
| 每块延迟 | 约 1.36 ms |

### 4.1 参数：哪个有用，哪个没用

```
keywords_threshold   0.30 -> 8/12
                     0.20 -> 9/12
                     0.10 -> 10/12   ★ 采用
                     0.05 -> 10/12

keywords_score       1.0 / 2.0 / 4.0  ->  结果【完全一样】，都是 10/12
```

**★ 结论**：**网上普遍说"调 `keywords_score` 提灵敏度" —— 无效。**
真正起作用的是 **`keywords_threshold`（越低越灵敏）**。

> 这条和很多教程说的不一样，以本项目测试为准。

### 4.2 ★ 只认真人嗓音

```
edge-tts 合成音  -> 7 条只中 1 条  ✗
真人游戏原声     -> 9~10/12        ✓
```

**这些 KWS 模型认的是真人说话的嗓音特征，不认 TTS 合成音。**
**所以验证唤醒必须让人真喊**，拿合成音频自测会得出错误结论。

---

## 五、怎么测

### 5.1 双引擎同时测（推荐）

```bat
py -3.12 <FAIRY_ROOT>\tools\kws_mic_test2.py 90
```

同时跑三个引擎，让你喊 90 秒，最后报各自命中次数：

```
A  ZH-你好fairy     = n ǐ h ǎo f a i r y      （中文模型）
B  EN-HELLO_FAIRY   = ▁HE LL O ▁FA IR Y       （英文模型）
C  EN-FAIRY         = ▁FA IR Y                （英文模型对照）
```

**注意**：这个脚本**只打印命中，不播应答**。

### 5.2 过渡版"喊了就应"

```bat
py -3.12 <FAIRY_ROOT>\tools\fairy_wake_listen.py          :: 一直听
py -3.12 <FAIRY_ROOT>\tools\fairy_wake_listen.py 120      :: 只听 120 秒
py -3.12 <FAIRY_ROOT>\tools\fairy_wake_listen.py --list   :: 列出麦克风
```

**双引擎同时听**，命中后**用克隆音色**播一句短应答（`我在` / `主人` / `嗯` / `好的主人`）。

**它每 3 秒会打一行麦克风音量**，用来确认麦克风真的在收音：

```
[ 3.1s] LVL max RMS over 3s = 0.05838   有声音
[ 6.2s] LVL max RMS over 3s = 0.00004   很安静
```

> 这行日志是排查"喊了没反应"的关键：
> **如果音量一直很低 → 麦克风/距离问题；如果有声音但没命中 → 词或门限问题。**

### 5.3 单引擎实测（旧版）

```bat
py -3.12 <FAIRY_ROOT>\tools\kws_mic_test.py 90
py -3.12 <FAIRY_ROOT>\tools\kws_mic_test.py 90 zh     :: 用中文模型
```

---

## 六、模型从哪来

官方 release（**不随本仓库分发**）：

```
https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/
    sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01.tar.bz2     :: 中文
    sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2      :: 英文
```

**解压到**：`<FAIRY_ROOT>\wakeword\models\`

**目录结构**：

```
sherpa-onnx-kws-zipformer-<lang>-3.3M-2024-01-01\
├── encoder-epoch-12-avg-2-chunk-16-left-64.onnx         11.6 MB
├── encoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx     4.6 MB   <- 推理用这个
├── decoder-epoch-12-avg-2-chunk-16-left-64.int8.onnx     0.2 MB
├── joiner-epoch-12-avg-2-chunk-16-left-64.int8.onnx      0.1 MB
├── tokens.txt                                            词表
└── bpe.model                                   （英文模型才有）
```

**推理只用 int8 那三个文件**，合计约 4.8 MB。

> ⚠️ **下载提示**：GitHub 直连很慢（约 5 KB/s），
> 用镜像前缀（如 `https://ghfast.top/`）才下得动。

---

## 七、KWS 的构造参数（Python API）

```python
import sherpa_onnx
kws = sherpa_onnx.KeywordSpotter(
    tokens=..., encoder=..., decoder=..., joiner=...,   # 三个 int8 onnx + tokens.txt
    num_threads=2,
    keywords_file=...,          # 关键词文件（见第三节）
    keywords_score=1.0,         # ★ 对本项目无影响
    keywords_threshold=0.10,    # ★ 这个才有用，越低越灵敏
    provider="cpu",
)
```

---

## 八、相关文件

| 路径 | 说明 |
|---|---|
| `<FAIRY_ROOT>\wakeword\models\` | 两个 KWS 模型 |
| `<FAIRY_ROOT>\wakeword\kw\` | 关键词文件（`sw.txt` / `live_*.txt` / 各种实验版本） |
| `<FAIRY_ROOT>\tools\kws_mic_test2.py` | ★ 双引擎测试工具 |
| `<FAIRY_ROOT>\tools\fairy_wake_listen.py` | 过渡版"喊了就应" |
| `<FAIRY_ROOT>\tools\wake_logic.py` | 唤醒判定逻辑（手写 Python） |
