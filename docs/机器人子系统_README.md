# 机器人子系统（Tools + 硬约束 + 怎么接真机）

> 建于 2026-10-15 · 代码在 `<项目根>\tools\robot\` + `<项目根>\tools\robot_bridge.py`
> 自测：**122 项全过**（`py -3.12 <项目根>\tools\robot\robot_selftest.py`）
> 本文件是这套东西的说明书 + 铁律清单。改代码前先读第 2 节。

---

## 0. 它跟 Fairy 的融合状态（2026-10-15 13:2x 实测）

**已接上（有代码、有验证）**

| 接点 | 位置 | 实测 |
|---|---|---|
| 代码在 Fairy 的目录体系里 | `tools\robot\`（与 `tools\*.py` 同一层）+ 配置 `robot\robot.json` | 与 `fairy_root` 同一套路径约定 |
| 变成"设备适配层"的一个设备 | `device_adapters.py` 新增 `_ensure_robot_adapter()`（懒加载） | `discover_all()` 实测返回 **19 台设备，其中机器人 2 台**；适配器清单 `nearby / bluetooth_audio / fairy_http / serial / nearlink / homeassistant / robot` |
| 进体检 | `health_check.py` 新增 `check_robot()`（4~6 项） | 实测产出 4 项：急停未锁存 / 后端=mock 干跑中 / 审计（放行干跑 N 条，拦截 M 条）/ 已接入设备发现 |
| **进开机播报** | `briefing.py` 的设备那一句顺带报机器人 | ★ 端到端实测（球重启后原话）：`播报#2 已连接 78 个设备。其中有 2 台机器人（演练模式）。` |
| 收进设备台账的入口 | `tool_bridge.register_into_device_adapters()` / `robot_bridge.py register` | `成功: 已注册到 device_adapters.ADAPTERS['robot']` |

**还没接（诚实清单）**

| 缺什么 | 为什么现在不做 |
|---|---|
| 球右键菜单里的「机器人」子菜单 | 需要真机才能定菜单项（走/停/抓/回零…）。现在放进去只会是空壳，误导人 |
| `fairy.py` 的组件表 / 一键启停 | 机器人**不是常驻服务**（按需调用），没有需要 `start/stop` 的进程；真机常驻的心跳服务等接机后再加（`robot_bridge.py beatloop` 已备好） |
| 语音直接说"让机器人抓杯子" | 桥里的中文意图（`parse_intent`）已经能跑（含 22 项规则匹配自测），但**谁把球听到的话递给它**这一步没做 —— 属于球的语音路由，等有真机再动，避免空转 |

> ★ 融合过程中真踩到一个坑（值得记住）：`robot_root.py` 住在 `tools\robot\` 子目录，
> 而 `sys.path` 只有 `tools\` —— `briefing.py` 里直接 `import robot_root` 会 `ModuleNotFoundError`，
> 被 `except` 吞掉后表现就是「功能写了但没生效」。已改成显式把子目录加进 path，
> 且失败会写进 `_ROBOT_NOTE` 而不是静默（自测第 27 项专门盯这条，含反向用例）。

> 结论口径：**"接口层面已融合，交互层面留到有真机再接"** —— 这样不会出现"菜单点了没反应"的空壳。

---

## 1. 它是什么（一句话）

把"我（大模型）想让它做什么"翻译成**经过六道安全闸**的动作，交给机器人执行；
**我永远碰不到电机**，这是代码结构上的保证，不是靠自觉。

## 2. 分层与铁律（改代码前必读）

```
决策层（我/大模型，0.5~3 秒 一次决定）
   │  只能说"技能"（pick / move / patrol …），不能给关节角度、不能给 PWM
   ▼
技能层  robot_skills.py     组合技能 + 回退（抓失败要松爪）
   │
   ▼
安全层  robot_safety.py     六道闸 + 急停(锁存/跨进程) + 心跳联锁 + 看门狗
   │  只有它判"allow"才继续
   ▼
网关    robot_adapters.RobotGateway   唯一入口；下发失败自动补一次 stop
   │
   ▼
适配层  robot_mock / robot_serial / robot_http / robot_ros2     只发信号，不做判断
   │
   ▼
机器人板载固件（不在本仓库）※ 反射层：平衡/避障/限位/急停，<=10ms，【必须自己实现】
```

**六条铁律**
1. **大模型不能进控制回路**：`max_speed_mps=0.6`、生成速度 4.3 tok/s 这种事实决定了它只能做慢决策。
2. **默认干跑**：`safety.dry_run=true`，动作只过闸不下发。要真下发：配置改 false 或临时 `FAIRY_ROBOT_GO=1`。
3. **急停锁存且跨进程**：`robot/logs/estop_state.json` 是唯一真相；一个进程按下，其它进程立刻也动不了。**只有显式 `clear` 才解除**。
4. **断线必须停**：与"大脑"的心跳超过 `heartbeat_timeout_s`（默认 2 秒）→ 自动急停 + 真调后端 stop。
5. **不确定就停**：下发失败/超时/异常 → 立刻补发 `stop`。超时 ≠ 没执行。
6. **机器人身上必须有硬急停 + 板载看门狗**：WiFi/ROS2 断了、PC 死了，它得自己能停。这一条**不能靠 PC 端代码**。

## 3. 文件清单

| 文件 | 干什么 | 关键点 |
|---|---|---|
| `tools/robot/robot_root.py` | 配置根：路径 + 默认值 | 配置坏掉会留痕（`CONF_ERRORS`），不静默 |
| `tools/robot/robot_safety.py` | 六道闸 / 急停 / 心跳 / 看门狗 / 审计 | 所有判断都在这；异常一律降级为"拦截" |
| `tools/robot/robot_adapters.py` | 契约 + 网关（唯一入口） | `caps/discover/state/act` 四件套；`caps(dev_id)` 支持按机身返回能力 |
| `tools/robot/robot_skills.py` | 技能表 + 组合技能 | `pick` 失败自动 `release` 回退 |
| `tools/robot/robot_link.py` | JSON 行协议（串口/TCP/回环） | 超时、迟到回复、主动上报都处理了 |
| `tools/robot/robot_mock.py` | **模拟后端**（无硬件也能跑） | 故障注入：`fail_act` / `fail_actions` / `silent_stop` / `state_lie` / `latency_s` |
| `tools/robot/robot_serial.py` | 串口/星闪网关后端 | 客户端再夹一次速度/角速度（板子端要夹第三次） |
| `tools/robot/robot_http.py` | HTTP 后端（机器人自带 WiFi） | 接口：`GET /robot/state`、`POST /robot/act`、`POST /robot/stop` |
| `tools/robot/robot_ros2.py` | ROS2 后端（接线表） | 本机没装 ROS2 → 明确报"不可用"，不假装能用 |
| `tools/robot/robot_ui.py` | 命令行总入口 | `status/robots/state/skills/do/estop/clear/watchdog/beat/events/doctor/selftest` |
| `tools/robot/robot_selftest.py` | **122 项自测**（无硬件） | 每条铁律都有对应用例，含跨进程急停 |
| `tools/robot_bridge.py` | 接进 Fairy 的桥 | 变 `device_adapters` 的 `robot` 适配器；中文意图（规则）→ 技能 |

## 4. 常用命令（照抄就能用）

```powershell
cd <项目根>\tools\robot

py -3.12 robot_ui.py doctor          # 体检：配置/后端/串口/ROS2/依赖
py -3.12 robot_ui.py status          # 安全层快照（急停/看门狗/限值/统计）
py -3.12 robot_ui.py selftest        # 122 项自测
py -3.12 robot_ui.py do mock:arm-01 pick target=杯子     # 演练（默认不下发）
py -3.12 robot_ui.py estop 人靠太近   # 急停（锁存）
py -3.12 robot_ui.py clear 你的名字   # 解除（必须显式）

$env:FAIRY_ROBOT_GO=1                # 临时允许真下发（只对本次生效）

cd <项目根>\tools
py -3.12 robot_bridge.py register    # 注册成 Fairy 的设备适配器
py -3.12 robot_bridge.py say "抓起来" # 一句中文 -> 技能
py -3.12 robot_bridge.py say "巡逻3趟"
```

## 5. 接真机的顺序（**别跳步**）

```
① 先跑 mock：确认 122 项自测全过（证明安全层是好的）
② 接一块最小的板子（ESP32/STM32 + 串口），只实现 state + stop 两个命令
   —— 验收：robot_ui.py state serial:COM7 能读数、stop 能在 100ms 内返回
③ 再加动作（move/turn…），每加一个就在 robot.json 的 allow 里登记
④ 把 safety.dry_run 改 false，速度先设 0.1 m/s，场地清空，人站急停按钮旁边
⑤ 接硬急停：板子接一个物理开关，同时 robot.json 的 estop.pin_file 指向一个状态文件
   —— 验收：按下开关，PC 端 status 立刻显示急停
⑥ 断线测试：拔网线/关 PC —— 机器人必须在 1~2 秒内自己停（这是板载固件的责任）
⑦ 才轮到装机械臂/四足，按 ①②③ 重来一遍
```

## 6. 现在**没做**的部分（诚实清单）

| 项 | 为什么没做 | 怎么补 |
|---|---|---|
| 真机驱动（板载固件） | 没有硬件 | 按第 5 节 ② 起步 |
| ROS2 实跑 | 本机没装 ROS2（实测 `ros2` 不存在） | 装 ROS2 后填 `backend_opts.topics` |
| Isaac Sim / MuJoCo 仿真 | 安装包 10~40 GB，且本机内存只剩 8 GB | 加内存（4 槽已用 2）后再装 |
| LeRobot / 模仿学习训练 | 要机械臂到位 | 第 5 节 ⑦ |
| 视觉闭环（相机 → 策略） | 要相机 + GPU 预算（显存今天刚因占满黑屏） | 先做"看见但不控"的只读接入 |
| 数据集管理 | 要真数据 | 目录已留 `robot/data/` |

## 7. 三个已知坑（踩过了）

1. **别用 `✔/✘/✗` 这类符号 print**：本机控制台是 GBK，会抛 `UnicodeEncodeError`（本次已栽一次，已全改中文）。
2. **危险词不能扫参数键名**：`grip` 的 `force=0.4` 曾因为键名叫 force 被当成"强制"拦掉（已修 + 加了回归用例）。
3. **急停只存内存 = 假安全**：命令行一次一个进程，早先版本 `estop` 之后另起进程照样能下发（已改成落盘 + 跨进程同步 + 自测第 16 项盯着）。

## 7.5 自查发现并修掉的两个「不安全默认」（写在这里当依据）

| 项 | 原来的行为（不安全） | 现在 | 为什么 |
|---|---|---|---|
| **从没收到心跳时看门狗不触发** | 只在「曾经收到过心跳」之后才判断断线 → 大脑从未连上时机器人**永不自动停** | 看门狗一起来就按「必须有心跳」判定；给一次启动宽限期（`bootstrap_grace_s`，默认=心跳超时），过了就急停 | 「第一次心跳还没来」与「大脑已经死了」在机器人眼里分不清，只能选安全的那一侧 |
| **限幅只认 speed / linear / yaw** | 上游若写 `velocity: 99`，限速形同虚设 | 别名一起夹（`velocity/vel/v/linear`、`angular/omega/turn`、`dist/distance/radius`），并把别名归一化成规范键 | 少夹一个同义词 = 留一个绕过限速的后门 |

> 自测已覆盖这两条（第 8 项含"从未收到心跳"，第 19 项专测别名）。

## 7.6 独立复查（2026-10-15）发现并已修的问题

> 全过程见 [机器人子系统_独立复查报告.md](<项目根>\docs\机器人子系统_独立复查报告.md)。
> 复查方式：一个独立帮手只读源码 + 自己写探针实测，报出 **8 项必须修**，其中两项是真危险。

| 编号 | 问题（原状） | 后果 | 现在 |
|---|---|---|---|
| **M1** | `robots` 段为空时白名单闸被跳过 | 配置写错一层 → 所有动作直接放行且零提示 | 登记表为空 → 写动作一律拒绝（`no-robot-registered`），读状态仍允许 |
| **M2** | 急停状态文件写坏/内容非法 → 当成"没按过急停" | 掉电写坏/被杀软清理后，下一个进程照样能动机器人 | 读取失败/类型不对 → **保守取急停**并留痕；只有"文件不存在"才视为首次运行 |
| **M3** | 限幅只认 speed/linear/yaw，`accel`/`pitch`/未知键原样透传 | 换个键名就能绕过限速 | 别名归一 + 已知数值键全部限幅 + **未知数值键一律丢弃**（记 `dropped-unknown`） |
| **M4** | 参数类型错被静默忽略 | `speed='fast'` 放行、固件按自己默认跑、审计无痕 | 类型错 → **拒绝**（`bad-param`） |
| **M5** | 断线联锁只在 CLI 里起，而 CLI 200ms 就退出 | "断线自动停"在实际调用路径（球/语音）上根本不存在 | 网关**默认**起看门狗；`safety.watchdog=false` 可关；常驻喂心跳用 `robot_bridge.py beatloop` |
| **M6** | 没配硬件急停时一声不吭 | 用户以为"安全已经齐了" | `dry_run=false` 且未配 `estop.pin_file` → 每次运行提醒一次（审计 + status 显示） |
| **M7** | 审计写失败/字段不可序列化时 `check()` 直接抛 | 调用方拿到崩溃而不是"拦截"；另一种是放行但无审计 | 收尾全程包 try，异常降级为 `block`；审计序列化失败退化成 `repr` |
| **M8** | 跑自测会把**生产**急停锁存解除 | 一次"验证系统"就把操作员按下的急停解除了 | 自测用独立状态文件，并加断言"生产锁存文件一个字节都没变" |

> 顺手修掉的还有：`distance` 被误当"臂展半径"夹到 0.55（move 走两米静默变 0.55 还报成功）、
> `_seq` 并发只靠 GIL 巧合（已加锁）、`RobotGateway.caps` 吞异常无痕（已有 `CONF_ERRORS` 兜底）。

**当前状态：自测 122 项全过**（`py -3.12 robot_ui.py selftest`），跑完自动清理临时文件。

### 复查报出但**故意没做**的（附理由）

| 项 | 为什么先不做 |
|---|---|
| 单步 move/walk 中途的倾角保护 | 需要机器人板载 IMU 实时回报；PC 端做不到中途保护 —— 属固件责任，已写进接真机清单 |
| `max_obstacle_m` 目前无人读取 | 同倾角：需要板载测距，PC 端读数有延迟 |
| `estop.hotkey` 只存不注册 | 真机场景人不在电脑前，键盘热键意义有限；硬急停才是正解 |
| ROS2 超时可能"已发布未确认" | 本机没装 ROS2，改不了也测不了；装好后按"发一次 + 读回确认"重做 |
> 当前规模：**122 项自测全过**，跑完自动清理 `_selftest_*` 临时文件。

## 人话版

机器人这套东西的骨架已经搭好并且真的跑通了：现在没有硬件也能把整条链路跑一遍（122 项自测全过），
而且"我"在代码结构上就碰不到电机——所有动作必须先过六道安全闸，急停按下后连另开的程序也动不了，
断线一秒就把机器人停住；自查时还揪出两个"本来会害人"的默认值（大脑从没连上时不会自动停、参数换个同义词就能绕过限速），都改了。
等真有机器人了，只要把它接成串口或网络后端、在配置里登记一下，上层一行都不用改。
