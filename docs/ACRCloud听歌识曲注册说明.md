# 听歌识曲（ACRCloud 免费 Key）图文注册说明

FairyX 的听歌识曲功能（`--songid`）使用 **ACRCloud** 的免费识别服务。
首次使用需注册一个免费账号并拿到 2 个 Key，之后永久本地使用，识别一次约 1~3 秒。

---

## 第一步：打开注册页面

浏览器打开：**https://console.acrcloud.com/signup**

页面是英文的，按下面提示填：

| 字段 | 填写内容 |
|---|---|
| Email | 你的邮箱（用来收验证邮件） |
| Password / Confirm | 密码（至少 8 位） |
| Name | 随便填，如 FairyX |
| Company Name | 随便填，如 FairyX |

点绿色按钮 **Sign Up** 注册。

> 提示：如果页面显示 "Already have an account? Sign in"，说明你以前注册过，直接登录。

---

## 第二步：邮箱验证

1. 打开你填的邮箱，找到 ACRCloud 发来的验证邮件
2. 点邮件里的蓝色链接 **Activate Account**（激活账号）
3. 激活后自动回到控制台

---

## 第三步：创建项目拿 Key（关键步骤）

1. 登录后进入控制台 **Console**
2. 点顶部菜单 **Music Recognition**（音乐识别）
3. 页面出现项目列表，点 **Create Project**（创建项目）

填写项目信息：

| 字段 | 填写内容 |
|---|---|
| Project Name | 随便填，如 FairyX |
| Project Type | 选 **Audio / Video Recognition** |
| Bucket Name | 自动生成，不用改 |

点 **Submit** 提交。

4. 创建成功后，项目卡片上会出现两行关键信息：

```
Host:           identify-eu-west-1.acrcloud.com
Access Key:     xxxxxxxxxxxxxxxxxxxxxxxx（一串字符）
Access Secret:  yyyyyyyyyyyyyyyyyyyyyyyy（一串字符）
```

> ⚠️ **Access Secret 只显示一次**（点 Show 可见），请复制保存。
> 如果弄丢了，点项目的 **Settings → Reset Access Secret** 重新生成。

---

## 第四步：填进 FairyX

打开文件：**`<项目根>\tools\fairy_plugins.py`**（打包版为 `<项目根>\X-开箱即用\tools\fairy_plugins.py`）

找到文件靠后位置这两行（约第 280 行）：

```python
ACR_HOST = "identify-eu-west-1.acrcloud.com"
ACR_KEY = ""      # 免费注册 https://console.acrcloud.com 后填入（每月免费额度）
ACR_SECRET = ""
```

把 Key 填进去（用记事本编辑，保存）：

```python
ACR_KEY = "你的 Access Key"
ACR_SECRET = "你的 Access Secret"
```

---

## 第五步：使用

```bat
py -3.12 tools\fairy_plugins.py --songid
```

会听到提示"正在录音 12 秒"，把手机/电脑播放的音乐靠近麦克风，
12 秒后自动识别，返回：

```
识别到：海阔天空 - Beyond
[00:00.00]今天我 寒夜里看雪飘过
[00:06.00]怀着冷却了的心窝漂远方
...
```

---

## 常见问题

**Q：识别提示"识别失败"？**
A：A：1) 确认音乐声够大（靠麦克风近一点）；2) 网络能访问外网（ACRCloud 服务器在国外）；3) 免费额度用完（每月约 500~1000 次，个人使用足够）。

**Q：免费吗？**
A：ACRCloud 有免费开发版（Free Developer Plan），每月一定次数免费，个人用完全够。

**Q：Key 会不会泄露？**
A：Key 只存在你本地 `fairy_plugins.py`，不会上传。注意不要把带 Key 的文件分享给别人。
