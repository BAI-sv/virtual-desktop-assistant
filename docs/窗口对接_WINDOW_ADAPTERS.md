# 窗口对接能力（自动发现 → 识别 → 读取 → 操作）交付报告

> 写于 2026-10-10 · 全部结论分「实测」与「推测」，实测都带原始输出或截图
> 代码：[window_adapters.py](../tools/window_adapters.py) · 配置：[fairy.example.json](../fairy.example.json) 的 `windows` 段（复制成 `fairy.json` 使用）

---

## 一、本机能力探测（实测）

**Python 3.12.0**（`%USERPROFILE%\AppData\Local\Programs\Python\Python312\python.exe`，FAIRY 用的就是它，无 venv）

| 包 | 有没有 | 说明 |
|---|---|---|
| `comtypes` | **有** | ★ 关键。用它直接调 UIAutomationCore.dll，等于拿到 UIA 能力 |
| `win32gui` / `win32com` / `win32process` / `win32api` | 有 | pywin32 全套 |
| `psutil` | 有 | 拿进程名 |
| `PIL` | 有 | 截图（做证据用） |
| `pyautogui` / `pyperclip` | 有 | 按键 / 剪贴板 |
| `numpy` | 有 | |
| **`uiautomation`** | **没有** | ✗ |
| **`pywinauto`** | **没有** | ✗ |
| **`pytesseract`** | **没有** | ✗ 也没有 tesseract 命令行 |

**结论：一个包都不用装。** 本机没装那两个 UIA 包，但 `comtypes` 已经够了 ——
我们直接 `comtypes.client.GetModule("UIAutomationCore.dll")` 把系统自带的 UIA 类型库
变成 Python 接口，实测 0.18 秒就绪，控件树读得跟 `uiautomation` 一样全。

**其它可用的后备资源**（本轮没用上，留给以后，都不需要新装东西）：
- `C:\Program Files\7-Zip\7z.exe` 是 **26.03**，实测**能解 zstd** → DSH 的完整会话流水（`session.v4.jsonl.zstd`）可读
- `<项目根>\tools\llama_vl_start.bat` 里有 **Qwen3-VL-8B** 视觉模型配置（8085 端口，当前没开）→ 截图理解/OCR 的兜底路线

**没有 OCR 能力**：pytesseract 没装、Windows.Media.Ocr 需要额外桥接、Qwen3-VL 没启动。
所以本轮**没做截图+OCR 兜底**。豆包能直接读，不需要兜底。

---

## 二、当前顶层窗口清单（实测）

**屏幕上真看得见的只有 9 个**（`--scan` 输出）：

| HWND | PID | 进程 | 类名 | 标题 |
|---|---|---|---|---|
| 396764 | 19320 | `DeepSeek Harness.exe` | `Chrome_WidgetWin_1` | 豆包FAIRY智能体制作与接手 — DeepSeek Harness ← **已登记: dsh** |
| 198218 | 6264 | `Doubao.exe` | `Chrome_WidgetWin_1` | 现在的仓库 = 纯发布版 - 豆包 ← **已登记: doubao** |
| 3605736 | 16728 | `everything.exe` | `EVERYTHING` | NVIDIA - Everything [管理员模式] |
| 19990082 | 13444 | `explorer.exe` | `CabinetWClass` | 系统 (C:) |
| 65856 | 4532 | `explorer.exe` | `Progman` | Program Manager |
| 6096240 | 4832 | `msedge.exe` | `Chrome_WidgetWin_1` | Strata 助手 - 个人 - Microsoft Edge |
| 1182710 | 14208 | `python.exe` | `TkTopLevel` | tk |
| 131198 | 3136 | `TextInputHost.exe` | `Windows.UI.Core.CoreWindow` | Microsoft Text Input Application |
| 26937476 | 14340 | `wgc.exe` | `Chrome_WidgetWin_0` | 360 Wargaming Game Center |

**含隐藏窗口的顶层窗口总数 = 245**（有标题、非工具窗）。按进程汇总前几名：
`explorer.exe 56 · amdow.exe 22 · quark.exe 11 · HYP.exe 10 · python.exe 9 · RadeonSoftware.exe 9 · Doubao.exe 8 · msedge.exe 8 · wgc.exe 8 · Weixin.exe 7 · 360Tray.exe 7 · TeamViewer.exe 7 · DeepSeek Harness.exe 6 · WeChatAppEx.exe 6`

> 隐藏窗口里绝大多数是输入法（Default IME）、`HintWnd`、`MSCTFIME UI`、`Sogou_TSF_UI` 这类壳窗口，
> 没有识别价值。所以模块默认只看「可见 + 有标题」的；排障时用 `--scan --all` 看全量。

**识别依据（不写死标题）**：进程名 + 窗口类名 + （可选）标题正则。三个都配了就都要满足。
窗口标题是会变的（DSH 那个标题就带着会话名），所以**绝不能拿标题当唯一特征**。

---

## 三、DSH 适配器：读到了什么（实测）

DSH 和别的程序不一样 —— 它有 **三条互补的通道**，全通：

```
$ py -3.12 tools\window_adapters.py --read dsh
程序   : dsh (dsh)  hwnd=396764
窗口   : 豆包FAIRY智能体制作与接手 — DeepSeek Harness
文本数 : 438   耗时 1739ms
通道   :
   http_reply         ok=True 149 字
   session_projcache  ok=True 条=8
   window_uia         ok=True 438
```

**① HTTP `/api/fairy/reply`** — 拿到助手最新回复（149 字），带 `seq / sessionId / turn / step`。
这条球本来就在轮询，零新增设施。

**② 会话投影（`session_projcache`）** — ★ 这条最有价值：**能读到「用户问了什么」**。
实测读到 **296 轮**，每轮有 `prompt`（用户说的）+ `response`（AI 答的）：

```
第 294 轮 (seq=14806)
  【用户问】 已经重启了 让fairy变成独立能运行的，不依靠DSH
  【AI 答】  # 🎯 根因找到了 —— **改的文件 ≠ 系统在用的文件** ...
第 296 轮 (seq=14889)
  【用户问】 要 Fairy 理解、甚至替你去操作那个窗口，
```

文件位置：`%USERPROFILE%\.dsh\storages\session_projcache\sessions\session-<uuid>.json`
（**明文 JSON，不用解压**，只挑 mtime 最新的那个就自动跟到当前会话）

**③ 窗口 UIA** — 直接读 DSH 窗口里**渲染出来的**对话正文：**438 个 Text 控件**。
不依赖任何接口权限，接口挂了它也能读。

**顺带摸清的接口现状（实测）**：

| 接口 | 结果 | 说明 |
|---|---|---|
| `GET /api/fairy/reply` | **200 ✓** | 可用 |
| `GET /api/fairy/meta` | **200 ✓** | 可用 |
| `GET /api/fairy/pending` | **200 ✓** | 只读待办查看器 |
| `GET /api/fairy/handoff` | **200 ✓** | 读交接单（age_h=0.3） |
| `POST /api/fairy/task` | **200 ✓** | 可用 |
| `POST /api/fairy/push` | **401 ✗** | ★ 源码里有，但**当前跑的 DSH 没加载**（要重启 DSH 才生效）|
| 其它 `/api/*` | **401** | 401 = 路由没注册，不是没权限 |

★ 这条很重要：**`/api/fairy/push` 现在用不了**（源码已写、运行中的实例没加载）。
按你的硬性要求我没重启 DSH。等哪天你方便重启，它就能用了 —— 但**本报告的所有能力都不依赖它**。

---

## 四、豆包：什么技术路线？读到了吗？（实测）

**技术路线判定：Chromium 外壳（不是原生 Win32）。**
证据：窗口类名 `Chrome_WidgetWin_1`，内部是 `RootView / NonClientView / FrameView / ChatView`，
网页内容是 `Document(aid=RootWebArea)`。

**★★ 关键实测：UIA 能读到豆包！**（我一开始差点判断错，记在这）
- 第一次浅层遍历（深度 6、上限 500）只看到 13 个节点，`ChatPageContainerView` 底下**全是空 Pane** —— 看着像读不到
- 真相是：**Chromium 的无障碍树是「按需激活」的** —— 第一次查询之后要等约 **1.5 秒**，树才长全
- 等过之后：**451 个节点**，有真 `Document` 和 **66~70 个 `Text` 控件**，里面是**真实的对话文字**

读到的正文样本（原文照抄）：

```
可以显示网页
✅ 已恢复。
E:\Strata\run-unsloth-ud-iq4_xs.bat
FairyX 悬浮球调用和网页界面
——Fairy 走 API，网页可手动深度对话 / 调试，互不冲突。
你帮我对他进行优化
Strata 优化结果汇报
跑了 Strata 官方
--calibrate
```

**怎么只读「对话正文」而不把「左侧会话列表」混进来** —— 靠**几何关系**，不靠类名：

1. 锚点 = `aid=input-engine-container`（输入框容器，每个会话页都在）
   - ★ 为什么不用 `chat-route-main`：**实测它会消失**（布局一变就没了），当锚点会突然瞎掉
2. 从输入框往上走，取「顶边最高、但仍明显在输入框上方」的那个祖先 = **正文滚动列**
   - 实测这一层正好把左侧栏（x 更小）和标题栏（y 更小）都排除掉了
3. 正文 = 该列里的 `Text` 控件，再去掉与输入框矩形重叠的

**效果**：59 条 / 1171 字。已验证**不含**这些噪音：
`老白` ✗ · `标准套餐` ✗ · `现在的仓库 = 纯发布版` ✗ · `AI 生成可能有误 请核实` ✗ ·
`归档对话` ✗ · `新对话 Ctrl N` ✗ · `发消息或创建任务` ✗ · `插件 · 技能 · 伙伴` ✗ · `对话摘要` ✗

**豆包输入框的真相**：不是 `Edit` 控件（全窗口 `Edit` 数量 = **0**）。
它是 **Tiptap / ProseMirror 的 contenteditable**，UIA 里暴露成 **`Group`**。
所以「按 ControlType=Edit 找输入框」这条路在豆包上**必然失败**。

---

## 五、能「操作」吗？做到哪一步（实测）

**做到了：① 找到窗口 ② 读全部对话正文 ③ 往输入框打字并验证 ④ 定位到发送按钮**
**没做：点发送**（按你的要求，MVP 只做读 + 打字；而且发送按钮被白名单挡着，一个都点不了）

### 打字实测（真打进去了，有截图）

```
$ py -3.12 tools\window_adapters.py --type doubao fairy-probe-1425 --yes
```

| 步骤 | 结果 |
|---|---|
| 1 写入前 | 输入框 = `''` |
| 2 打字 | `ok=True` · `method=focus+clipboard+ctrl-v` · `verified=True` |
| 3 **独立复核**（重开 UIA 会话重读） | `'fairy-probe-1425'` → 标记真的进去了 **✓** |
| 4 清空 | `ok=True` · `cleared=True` |
| 5 复核已清空 | `''` · 已恢复原状 **✓** |
| 6 **确认没发出去** | 会话正文里搜 `fairy-probe` → **False** **✓** |

**截图证据**（`<项目根>\logs\shots\`）：

- 打字前：占位符 + 右侧是**灰色麦克风**
- 打字后：`fairy-probe-1425` 带光标，右侧变成**蓝色发送箭头** ← 豆包自己承认有内容了
- 清空后：占位符回来了，按钮变回灰色麦克风

**怎么写的**（实测三种方法的真实结果）：
| 方法 | 结果 |
|---|---|
| `ValuePattern.SetValue()` | **失败** ✗ |
| `LegacyIAccessible.SetValue()` | **失败** ✗ |
| **UIA 聚焦 + 剪贴板 Ctrl+V** | **成功 ✓** ← 实际用的这条 |

**★ 豆包这里有个大坑，必须记住**：
`ValuePattern.CurrentValue` 对 ProseMirror 是**坏的** —— 空的时候返回 `''`，有字的时候返回 `None`，
两种情况都读不到真值。**所以「写完读回值验证」这条路在豆包上完全不成立**（我第一版就被它骗了，
报了 `verified: false`，其实字已经打进去了）。
**唯一可靠的读法是读输入框里的 `Text` 子控件**（`composer_text()`），已实测。

---

## 六、架构：加一个新 AI 要做什么

```
window_adapters.py
├── 发现层   enum_windows()  枚举顶层窗口 -> 标题/类名/进程/PID/矩形
│            identify()      按注册表规则认这是哪个程序
│            find_window()   同一进程多窗口时取面积最大的
├── 注册表   fairy.json 的 "windows" 段（唯一写死的地方就是配置，不是代码）
├── 适配器   Adapter 基类 -> read() / type_text()
│            ├── DshAdapter        HTTP + 会话投影 + 窗口 UIA（三通道）
│            ├── DoubaoAdapter     aid 锚点 + 几何关系
│            └── GenericUiaAdapter 通用兜底：任何窗口的 Text 控件按阅读顺序读
├── 安全闸   check_read / check_write / check_click / check_danger
└── 审计     window_ops.jsonl
```

**加一个新 AI / 新软件 = 改配置，不改代码**（进程名在任务管理器里就能看到）：

```json
"newai": {
  "enabled": true,
  "adapter": "generic_uia",
  "mode": "read",
  "match": { "process": ["SomeAI.exe"], "class": [], "title_regex": "" },
  "allow_click_names": []
}
```

`generic_uia` 是**通用兜底**：不用为它写任何代码，就能把那个窗口里的文字读出来。
读不到就**如实说读不到**（自绘/画布类程序 UIA 树是空的），不会假装读到了。

**要做深度对接**（像 DSH/豆包那样）才需要加一个适配器类 —— 就是实现 `read()`，
要能打字再加 `type_text()`，然后 `ADAPTERS` 字典里登记一行。

**为什么这么设计**：用户原话是「以后还会有其他的 AI，其他的软件也要能自动对接」。
所以**程序名绝不能写死在代码里** —— 早期把 `19387` 写死的教训已经吃过了（DSH 一关球就哑了）。

---

## 七、三道闸（安全）

| 闸 | 机制 | 实测 |
|---|---|---|
| **① 白名单** | 只有 `fairy.json` 里 `enabled=true` 的程序会被碰；没登记的窗口一律不碰 | `check_read("__不存在的程序__")` → **拒绝** ✓ |
| **② 默认只读** | `mode="read"` 只能看。要打字必须**显式**改成 `write` | 只读时打字 → **拒绝**并提示怎么开 ✓ |
| **③ 按钮白名单** | 点击比打字更危险：除了 `write`，按钮名还必须写进 `allow_click_names`。**默认空 = 一个都点不了** | 点「发送」→ **拒绝** ✓ |
| **④ 危险词** | 命中 `发送/删除/支付/关闭/…`（配置里 24 个词）一律要人确认 | 「删除全部对话」→ **拒绝** ✓ |
| **⑤ 前台守卫** | 按键是发给**当前前台窗口**的。发按键前必须确认前台就是目标进程，否则**拒绝执行** | 实测拦下过一次 ✓ |
| **⑥ 全程日志** | 每次读/写/点都写 `<项目根>\logs\window_ops.jsonl`，一行一条 JSON | 已落盘 ✓ |

**日志实例**（真文件内容）：
```json
{"t":"2026-10-10 13:35:10","op":"type","prog":"doubao","target":"现在的仓库 = 纯发布版 - 豆包","ok":true,"detail":"1 字 via focus+clipboard+ctrl-v · 已发送? 否（本工具不会按回车）","verified":true}
{"t":"2026-10-10 13:35:10","op":"click","prog":"doubao","target":"关闭","ok":false,"detail":"程序 'doubao' 没配 allow_click_names —— 按设计，一个按钮都不许点"}
```

**当前开关状态**（写清楚，方便你随时收紧）：
- `dsh`：`mode = "read"`（只读）
- `doubao`：`mode = "write"` ← **为了让 Fairy 能替你打字，显式打开的**；但 `allow_click_names` 是空的，**发送/删除一个都点不了**
- 想收紧：把 `doubao` 的 `mode` 改回 `"read"` 即可（改完最多 5 秒生效，不用重启 Fairy）

**★ 本工具永远不会按回车、不会点发送** —— 打字只负责「把字放进输入框」，按不按发送永远由人决定。

---

## 八、诚实说明：没做到什么、有什么限制

1. **没做 OCR / 截图兜底**。本机没有 OCR 能力（pytesseract 没装、也没 tesseract 命令行）。
   本来打算用截图+OCR 兜底「UIA 读不到」的程序，结果**不需要**（豆包直接读得到），
   所以没做，也没装东西。真要 OCR，现成路线是启动 `llama_vl_start.bat` 的 Qwen3-VL（8085 端口）。

2. **没做点击发送**。按你的要求 MVP 只做读 + 打字，点击被白名单挡着。

3. **`/api/fairy/push` 不可用**（401）。源码里有、运行中的 DSH 没加载，要重启 DSH 才生效。
   我没重启（你正在用 DSH 对话）。不影响本报告任何能力。

4. **DSH 会话投影是内部结构**。`session_projcache` 的 JSON 结构没有公开文档，DSH 升级可能变。
   代码里包了 try，读不到会**明说读不到**，不会假装。

5. **UIA 树会随界面变**。实测踩到两次：
   - `chat-route-main` 这个 AutomationId **会消失**（所以不能当锚点）
   - ProseMirror 的类名一聚焦就从 `tiptap ProseMirror` 变成 `tiptap ProseMirror ProseMirror-focused`（精确匹配会突然找不到）
   现在都改成了不依赖它们的写法，但**豆包大版本更新后可能需要重新校准**。

6. **★★ 写操作有「与人共享输入框」的竞态（实测观察到，原因未确定）**。
   有一次我打进去 `fairy-probe-1340`，紧接着再读变成 `fairy-probe-1340，还有` ——
   多出来的「还有」**不是我打的**（可能是你当时在打字，也可能是豆包自己的联想/补全）。
   而且那一次 Ctrl+A 清空**没清掉**（前台没抢到）。
   现在加了「前台守卫」后连续多轮复测都干净了，**但我必须如实说：往一个「人正在用」的程序里打字，
   本质上是共享状态，有竞态。** 建议：让 Fairy 打字时，人别同时在这个输入框里输入。

7. **只在本机验证过**。没有其它机器 / 其它分辨率 / 多显示器切换的验证。
   豆包的正文定位是**几何方法**，依赖「左侧栏在正文列左边、标题栏在它上边」这个布局关系 ——
   如果豆包出了「侧栏在右边」的皮肤，定位会失效（会读不到或读错，不会乱写）。

8. **没动任何现有文件逻辑**：`fairy_ball.py` / `fairy_ear.py` / `fairy.py` / `fairy-plugin/` **一个字没改**。
   `fairy.json` 只**新增**了 `windows` 段（原文件已备份）。

---

## 九、改了/新增了哪些文件

| 文件 | 动作 | 说明 |
|---|---|---|
| `<项目根>\tools\window_adapters.py` | **新增** | 全部新能力都在这一个文件里（发现层 + 注册表 + 3 个适配器 + 6 道闸 + 审计 + 命令行） |
| `<项目根>\fairy.json` | **改**（只加不删） | 新增 `windows` 段（白名单/危险词/开关）。**原文件已备份** |
| `<项目根>\fairy.json.bak_before_windows` | **新增** | 改之前的备份 |
| `<项目根>\logs\window_ops.jsonl` | **新增**（运行时生成） | 审计日志 |
| `<项目根>\logs\shots\*.png` | **新增**（证据） | 打字/清空的截图证据 |
| `<项目根>\docs\窗口对接_WINDOW_ADAPTERS.md` | **新增** | 本报告 |

**没改**：`fairy_ball.py` · `fairy_ear.py` · `fairy.py` · `E:\Downloads\fairy\fairy-plugin\` · DSH 任何配置
**没装**：任何新包
**没重启**：DSH

---

## 十、命令速查

```powershell
$py = "%USERPROFILE%\AppData\Local\Programs\Python\Python312\python.exe"
& $py <项目根>\tools\window_adapters.py --scan          # 列出看得见的顶层窗口（已登记的会标出来）
& $py <项目根>\tools\window_adapters.py --scan --all    # 连隐藏窗口一起列（排障用）
& $py <项目根>\tools\window_adapters.py --discover      # 已登记程序现在在不在线
& $py <项目根>\tools\window_adapters.py --read dsh      # 读 DSH（三通道）
& $py <项目根>\tools\window_adapters.py --read doubao   # 读豆包正文
& $py <项目根>\tools\window_adapters.py --selftest      # 自检（12 项，无副作用）
& $py <项目根>\tools\window_adapters.py --audit 20      # 看最近 20 条操作日志
& $py <项目根>\tools\window_adapters.py --type doubao 文字 --yes   # 打字（需 mode=write）
& $py <项目根>\tools\window_adapters.py --clear doubao --yes       # 清空输入框
& $py <项目根>\tools\window_adapters.py --click doubao 发送 --yes  # 点击（需白名单，默认必被拒）
```

代码里调用：

```python
import window_adapters as wa
wa.discover()                      # 自动发现
wa.read("doubao")                  # 读内容（永远安全）
wa.type_text("doubao", "你好")      # 打字（需 mode=write，永不发发送）
```
