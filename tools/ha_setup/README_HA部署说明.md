# FairyX × Home Assistant 部署说明

> 用途：让 Fairy 能语音控制你的智能家居设备（易微联 / 海尔智家 / 华为智慧生活等）
> 更新：2026-10-12（**已完成部署**）

---

## 一、当前状态（已完成）
- ✅ WSL（适用于 Linux 的 Windows 子系统）已启用（内核 3.0.1.0）
- ✅ Ubuntu 26.04 发行版已安装
- ✅ Home Assistant Core 2026.10.1 已安装（WSL 内 ~/ha_venv）
- ✅ 系统依赖已补齐（python3-venv / ffmpeg 8.0.1 / sqlite3）
- ✅ HA 已启动并监听 **http://localhost:8123**（REST API 正常）
- ✅ 管理员账号已自动创建：用户名 **fairy** / 密码 **fairy@2026**（网页登录用，可自行修改）
- ✅ 长期访问令牌已自动生成并写入 `<项目根>/config.json` → `smart_home.ha`
- ✅ 开机自启已安装（启动文件夹 FairyX-HA.vbs，登录时自动拉起 HA）

**雷电 ADB 桥（华为设备，免重启即用）** 也已接通，优先于 HA：
- 说「关闭音箱」→ 直接点智慧生活里的快捷开关（音箱/电脑/摄像头等）

---

## 二、HA 手动启停（平时一般不用管）
- 启动：双击 `<项目根>/tools/start_ha.bat`（或重启电脑后自动）
- 停止：任务管理器里结束 wsl 常驻进程，或在 WSL 里 `pkill -f "hass --config"`
- 看日志：`wsl -d Ubuntu -u root -- tail -f /root/ha.log`
- 手动启动命令：`wsl -d Ubuntu -u root -- bash /root/start_ha.sh`

---

## 三、接入各生态（把真实设备接进 HA）
| 生态 | App | 接入方式 | 稳定性 |
|---|---|---|---|
| 易微联 | 易微联 | HA 网页「设置 → 设备与服务」添加 ewelink 集成（登录易微联账号）| ✅ 成熟 |
| 海尔智家 | 海尔智家 | HACS 搜索 haier 安装（社区集成）| ✅ 可用 |
| 华为智慧生活 | 华为智慧生活 | HACS 搜索 huawei（社区集成，配置较麻烦）| ⚠️ 不稳定 |
| 天猫精灵 | 天猫精灵 | 无官方/社区稳定方案 | ❌ 暂不支持 |
| H3C 魔术家 | H3C 魔术家 | 路由器管理类，非家居控制 | ❌ 不支持 |
| 比亚迪闪充 | 比亚迪 | 车/充电类，非家居 | ❌ 不支持 |

**推荐顺序**：先把易微联接上（最稳），再试海尔，华为设备直接用雷电 ADB 桥控制。

---

## 四、把新设备实体配给 Fairy

1. HA 网页「设置 → 设备与服务 → 实体」查到实体 ID（如 `light.客厅灯`）
2. 编辑 `<项目根>/config.json` 的 `smart_home.ha.entities`：
```json
"smart_home": {
  "ha": {
    "url": "http://localhost:8123",
    "token": "（已自动填好）",
    "entities": {
      "灯": "light.客厅灯",
      "空调": "climate.空调",
      "插座": "switch.插座"
    }
  }
}
```
3. 改完说「重启 Fairy」或重启 ear 生效。

> `entities` 的键是你说的话（灯/空调/插座…），值是 HA 里的实体 ID。

---

## 五、Fairy 语音怎么用（装好后）
对 Fairy 说：
- 「打开灯」/「关灯」/「调亮」/「调暗」
- 「打开空调」/「关空调」/「调高」
- 「打开插座」/「关插座」
- 「打开窗帘」/「关窗帘」…（目标词表在 smart_home.py 里，可自行加）

控制顺序：雷电 ADB 桥（华为设备）→ HA REST API → 米家 miio → 提示未接桥。

---

## 六、常见问题
**Q1：HA 网页打不开？**
确认 HA 在跑：`wsl -d Ubuntu -u root -- bash /root/start_ha.sh`，看日志 `tail -f /root/ha.log`。

**Q2：设备在 HA 里看不到？**
先去「设置 → 设备与服务」添加对应集成（易微联/海尔），添加成功后才能用。

**Q3：重启后 HA 没自动起？**
检查启动文件夹里是否有 FairyX-HA.vbs；也可手动双击 `<项目根>/tools/start_ha.bat`。

**Q4：不想用 HA，只想控制华为设备？**
直接用雷电 ADB 桥（已接通）：对 Fairy 说「关闭音箱 / 打开摄像头 / 打开电脑」即可。
