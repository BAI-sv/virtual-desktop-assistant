# 虚拟桌面助手 第三方组件许可证审计

> 生成时间：2026-10-09 · 依据：**本机已安装包的 `*.dist-info/METADATA` 元数据**（发行方自己声明的许可）
> 说明：本文件是**事实清点**，**不构成法律意见**。

---

## 一、审计方法（怎么核实的）

```
① 先确定【真实用到】的组件：
   · 扫描 <FairyX 根目录>\tools\ 全部 .py 的 import 语句（AST 解析，非正则）
   · 读 <FairyX 根目录>\requirements.txt
   · 加上【外部组件】（IndexTTS / DSH / 自有插件）

② 再查许可证，优先级：
   本地 dist-info METADATA 的 License-Expression（SPDX，最可信）
   > License 字段 > Classifier: License :: OSI Approved :: ...
   > 上游项目 LICENSE 文件 > 官网（本地元数据查不到的才上网）

③ ★ 查不到的按「上游声明缺失」记录，不猜测。
```

**两个站点包目录都查了**：
```
%USERPROFILE%\AppData\Local\Programs\Python\Python312\Lib\site-packages\   （106 个 dist-info）
<IndexTTS 安装目录>\venv\Lib\site-packages\                                 （169 个 dist-info）
```

**Python 版本**：3.12.0（CPython → PSF License）

---

## 二、★ 完整对照表

| # | 组件 | 版本 | 用途 | 许可证 | 核实方式 |
|---|---|---|---|---|---|
| 1 | Python (CPython) | 3.12.0 | 运行时 | **PSF License** | 官方事实 |
| 2 | `numpy` | 2.5.3 | 数值计算 | **BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0** | dist-info `License-Expression` |
| 3 | `sounddevice` | 0.5.6 | 录音/播放（PortAudio 绑定） | **MIT** | dist-info `License-Expression` |
| 4 | `Pillow` | 12.3.0 | 图像处理（球帧/气泡） | **MIT-CMU** | dist-info `License-Expression` |
| 5 | `psutil` | 7.2.2 | 进程/系统监控 | **BSD-3-Clause** | dist-info |
| 6 | `scipy` | 1.18.1 | 图像/信号处理 | **BSD-3-Clause** | dist-info（Classifier: BSD License） |
| 7 | `PyAudioWPatch` | 0.2.12.9 | WASAPI 回环录音（听歌识曲/字幕） | **Apache-2.0** | dist-info |
| 8 | `pywin32` | 312 | 窗口/剪贴板/进程 | **PSF** | dist-info（Classifier） |
| 9 | `sherpa-onnx` | 1.13.8 | 关键词检测 KWS（唤醒） | **Apache-2.0** | dist-info（含 `sherpa-onnx-core` = Apache-2.0） |
| 10 | Sherpa-ONNX KWS 模型 | 3.3M | 中/英文唤醒模型 | **Apache-2.0**（随上游项目）<br>⚠ 训练数据许可**待上游确认** | 上游 release + 项目元数据 |
| 11 | `faster-whisper` | 1.2.1 | 语音转写（唤醒后指令） | **MIT** | dist-info |
| 12 | `sentencepiece` | 0.2.2 | BPE 分词（生成英文 KWS 关键词） | **Apache-2.0** | dist-info `License-Expression` |
| 13 | `pypinyin` | 0.55.0 | 拼音（生成中文 KWS 关键词） | **MIT** | dist-info |
| 14 | `onnxruntime` | 1.30.0 | 模型推理 | **MIT** | dist-info |
| 15 | `bleak` | 3.0.2 | BLE 设备识别 | **MIT** | dist-info `License-Expression` |
| 16 | **`zeroconf`** | 0.151.5 | 附近设备发现（mDNS） | **LGPL-2.1-or-later** ★ | dist-info `License-Expression` |
| 17 | `pyserial` | ⚠ 未安装 | 串口设备（惰性 import） | **BSD-3-Clause**（上游声明）⚠ 默认不安装 | 上游项目页 |
| 18 | `torch` | 2.11.0+rocm7.1 | 可选（IndexTTS 侧） | **BSD-3-Clause** | dist-info（IndexTTS venv） |
| 19 | `comtypes` | 1.4.17 | 备用（本项目实际用纯 ctypes） | **MIT** | dist-info `License-Expression` |
| 20 | `pycaw` | 20260927 | 备用（同上，未使用） | **MIT** | dist-info `License-Expression` |
| 21 | `imageio-ffmpeg` | 0.6.0 | 已随桌宠组件移除，不再使用 | **BSD-2-Clause** | dist-info |
| 22 | `requests` | 2.34.2 | 传递依赖（未直接 import） | **Apache-2.0** | dist-info |
| 23 | `websockets` | 15.0.1 | 传递依赖（未直接 import） | **BSD-3-Clause** | dist-info |
| 24 | **`edge-tts`** | 7.2.8 | 兜底 TTS（最后一道） | **LGPLv3** ★ | dist-info（Classifier） |
| 25 | **IndexTTS**（bilibili indextts2） | 本地部署 | 音色克隆合成（外部服务） | **《bilibili 模型使用许可协议》**<br>（自定义，**非 SPDX 开源许可**）★ | 本地 `LICENSE_ZH.txt` 全文 |
| 26 | **DSH**（DeepSeek Harness） | 已装 | 智能体内核（外部依赖） | ⚠ 未查明 | 未见 DSH 自身许可文件 |
| 27 | `dsh-plugin-fairy` | 0.3.8 | 本项目自有 DSH 插件 | **MIT** | 项目 `package.json` |

---

## 三、★★ 值得注意的：copyleft 与自定义许可

### 3.1 弱 copyleft（LGPL）—— **两个**

```
① zeroconf        LGPL-2.1-or-later     用于：nearby.py（附近设备发现 mDNS）
② edge-tts        LGPLv3                用于：兜底 TTS（语音合成不可用时的最后一道）
```

**LGPL 的性质（一般理解）**：
```
· LGPL 是【弱】copyleft：允许被【闭源/其他许可】的程序作为库调用，
  通常不要求调用方也改用 LGPL（动态链接、不修改其源码的情况下）。
· 但若【修改了这两个库的源码】并分发，需按 LGPL 公开修改部分。
· 本项目【未修改】它们，以依赖包方式使用。
```

**★ 本项目实测情况**：
```
· zeroconf  —— nearby.py 真 import（`from zeroconf import ...` 之类）→ 属"作为库调用"
· edge-tts  —— ★ 本项目 tools/ 里【没有一处真 import】它！
              它实际是【DSH 的 dsh-plugin-tts 插件】的依赖，走插件路由，
              本项目代码并不直接调用它。
```

### 3.2 自定义模型许可（非 SPDX）—— **一个**

```
★ IndexTTS（bilibili indextts2）
  许可文件：<IndexTTS 安装目录>\LICENSE_ZH.txt（《bilibili模型使用许可协议》，2680 字）
```

**已逐条读出的关键条款**：
```
· 免费使用；但若【月活 > 1 亿】或【年收入 > 1 亿人民币】（含关联方）
  → 必须向 bilibili 申请【商业许可】，由其自行决定是否授予        （第 2.2 条）
· 不得用它改进其他 AI 模型（非商业用途的 AI 模型除外）           （第 3 条 c）
· 不得用于违法/监管禁止用途（虚假信息、歧视内容、侵犯隐私等）      （第 3 条 c）
· 禁止高风险场景                                                （第 4.2 条）
· 使用产生的侵权/违法责任由使用者独自承担                        （第 3.2 条）
```

**★ 对本项目的适用性（事实层面）**：
```
· 本项目为【非商业性质、个人使用】→ 未触及"月活/收入"门槛 ✓
· 未用其改进其他 AI 模型 ✓
· ★★ 但该模型【不随本仓库分发】—— 使用者需自行部署
   → 使用者自行承担与其部署/使用相关的合规责任
```

### 3.3 ⚠️ 尚有两项未能确认

```
① DSH（DeepSeek Harness）自身许可
   安装目录 %LOCALAPPDATA%\Programs\DeepSeek Harness\
   只找到：7zip-installer-COPYING.txt · 7zip-installer-LICENSE.txt ·
           LICENSE.electron.txt · LICENSES.chromium.html
   ★ 没找到 DSH 自身的许可文件（应用代码在 app.asar 内，未解包）
   → 结论：DSH 是外部依赖，本项目只是它的插件宿主，许可未随包分发。

② pyserial
   · requirements.txt 里列着；device_adapters.py 有【函数内惰性 import】
   · ★ 但【默认不安装】（import 失败、无 dist-info）
   · 上游声明为 BSD-3-Clause（未随包核实）
   → 结论：**【未随包核实】**；且它属于"列了但没装"，见第五节。
```

---

## 四、★ 许可证兼容性提示（不构成法律意见）

**本项目以 CC BY-NC-SA 4.0（署名-非商业-相同方式共享）发布。**

```
★ CC BY-NC-SA 的 "SA（相同方式共享）" 要求：
  基于本作品的衍生作品，须以【相同许可】分发。

★ 与依赖的关系（一般理解）：
  · 依赖库【不会因为被本程序调用】而改变自己的许可 ——
    "本程序用 NC-SA 发布 + 依赖各自保持原许可"是常见且通常可行的做法。
  · ★ 但 SA 条款与 LGPL 同时存在时，具体交互属于【法律判断】，
    建议由专业人员确认（尤其是若未来打算修改 zeroconf/edge-tts 源码并分发）。

★ 与 IndexTTS 许可的关系：
  · IndexTTS 协议要求"改进 AI 模型"不得用于非许可情形；
    本项目【未用其训练/改进任何模型】，只是调用它做语音合成。
  · 该模型不随仓库分发 → 仓库本身不涉及该许可的分发义务。

★ 需要注意的三条：
  ① 两个 LGPL 依赖：未修改源码时通常只需保留其许可声明（本文件即为此用途）
  ② IndexTTS：属外部服务，其许可与部署方式由使用者自行负责
  ③ DSH：许可未随包分发，若将来要随包分发需先查清
```

---

## 五、★ 附带发现（组件与依赖清单的问题）

```
① ★ pyserial 列在 requirements.txt 里，但【默认不安装】
   · device_adapters.py 里有函数内惰性 import
   → 影响：串口设备功能会不可用（但不影响启动）
   → 建议：要么装上，要么在 requirements 里标为「可选」

② ★ 以下包在本项目代码里【完全没被提到】（不 import、不引用）：
   · imageio-ffmpeg  —— 原用于 convert_whale.py（已随桌宠组件删除）
   · requests        —— 传递依赖（本项目用标准库 urllib）
   · websockets      —— 传递依赖
   · edge-tts        —— 实际是 DSH 插件的依赖，本项目不直接调用
   · comtypes        —— 只在 volume_duck.py 注释里出现（"不用 pycaw、不用 comtypes"）
   · pycaw           —— 同上
   · onnxruntime     —— 只在 health_check.py 的提示文案里出现
   → 这些【不是错误】，但 requirements.txt 把它们列为"必装"会误导使用者
   → 建议：降级为"可选/传递依赖"分组，或注明"本项目不直接使用"

③ ★ IndexTTS 的 venv 与主环境是【两套】（169 vs 106 个包）
   · torch 只装在 IndexTTS 的 venv 里 → 主环境不必装 torch ✓（已在 requirements 注明）
```

---

## 六、诚实说明

```
✗ 【本审计不是法律意见】—— 我只做"事实清点 + 一般性提示"，
  许可证兼容性与合规结论请由专业人员判断。
✗ DSH 自身许可【没能确认】（app.asar 未解包，安装目录里只有 electron/chromium 的许可）
✗ pyserial 许可【未随包核实】（默认不安装），用的是上游公开声明
✗ KWS 模型的【训练数据许可】没能确认：
  两个模型来自 k2-fsa/sherpa-onnx 的 release，项目整体 Apache-2.0，
  但模型目录里只有 README（无独立 LICENSE）；
  训练数据（WenetSpeech / GigaSpeech）本身可能有独立使用条款 → 已在表中标注
✗ 未逐项联网复核（本地元数据已覆盖绝大多数），少数项按规则记录为「未查明」
✗ 只审了 Python 侧与自有插件；DSH 宿主与 electron 运行时未展开
```

---

## 七、人话版

**这次就是把项目用到的每一个"别人家的库"，逐个查清它是什么许可。**

**方法**：先从代码中扫出实际用到的库，再核对已装包元数据中的许可证声明（由发行方自行声明，最可靠）。查不到的，记录为「未查明」，不编造。

**查完的结果，两个需要你知道**：

**第一，有两个库是"弱 copyleft"** —— **一个负责附近设备发现，一个是语音合成的备用方案。** **这类许可的性质是：你把它们当库调用通常没问题，但如果你改了它们的源码还分发出去，就得公开你改的部分。** **我们没改过它们。** **另外我要说清一件事：其中一个其实我们的代码根本没直接用** —— **它是另一个组件的依赖。**

**第二，那个语音合成引擎用的不是标准开源协议**，**是 B 站的一份自定义模型协议**。**我把关键条款读出来了**：**免费用；但如果月活超过一亿或者年收入超过一亿，就得申请商业许可；另外不能用它去改进别的 AI 模型。** **我们这个是非商业自用，远没到那个门槛。** **而且那个模型本身不随仓库分发，用的人自己部署、自己担责。**

**两项按「未查明」记录**：一是智能体内核自身的许可（安装包内只有其内嵌浏览器的许可）；二是一个串口库（写在依赖清单中，默认不随项目安装）。

**顺带发现清单里有点乱**：**有几个包列着"必须装"，其实代码里一次都没用到**（**包括一个随桌宠组件一起删掉的**）。**不影响运行，但开源后会让用的人白装一堆东西** —— **建议改成分组。**

**最后一句**：**我做的只是"把事实摆清楚"，不是法律意见。** **真的要发布，兼容性那部分最好找个懂行的确认一下。**
