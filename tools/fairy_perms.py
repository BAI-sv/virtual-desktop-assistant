# -*- coding: utf-8 -*-
"""
fairy_perms.py —— Fairy 权限说明与授权管理

★ 2026-10-11【用户要求】"安装启动时需要权限全部给使用者说清，
   不给相关权限相关功能不能使用。"

Fairy 用到的权限分 7 项，每项说明：用途 / 不授权会怎样。
授权状态存 fairy.json 的 perms 段（perms.authorized = ["mic", ...]）。
首次启动时 fairy.py start 会打印清单并初始化；球右键菜单可查看/改授权。

用法：
    py -3.12 fairy_perms.py            # 打印当前授权状态
    py -3.12 fairy_perms.py --grant mic,disk    # 授权指定项
    py -3.12 fairy_perms.py --revoke mic        # 撤销指定项
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.dirname(HERE)
CONFIG = os.path.join(D, "fairy.json")

# 权限清单：(id, 名称, 用途, 不授权的后果)
PERMS = [
    ("mic", "麦克风", "语音唤醒 / 语音对话 / 语音指令",
     "无法用语音与 Fairy 对话，只能打字"),
    ("record", "录音", "听歌识曲 / 环境声识别",
     "听歌识曲功能不可用"),
    ("disk", "磁盘扫描", "全盘扫描软件 / 模型 / 素材，自动部署",
     "无法自动发现与部署模型，只能手动指定路径"),
    ("autostart", "开机自启", "Fairy 随系统启动常驻",
     "需手动打开 Fairy，不会自动启动"),
    ("window", "窗口读取", "窗口守护 / 打开软件播报 / 视频字幕识别",
     "不自动播报你打开的软件，不显示视频字幕"),
    ("localport", "本地端口服务", "在 127.0.0.1 开放本地服务（球/耳朵/翻译）",
     "悬浮球与本地服务不可用"),
    ("network", "网络", "在线 AI / 翻译 / 天气 / 歌词 / 更新检查",
     "在线功能不可用，仅剩本地能力"),
]
_ID2NAME = {p[0]: p[1] for p in PERMS}


def _load_cfg():
    try:
        if os.path.exists(CONFIG):
            with open(CONFIG, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_cfg(c):
    try:
        tmp = CONFIG + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG)
        return True
    except Exception:
        return False


def authorized():
    """当前已授权 id 列表。"""
    c = _load_cfg()
    return list((c.get("perms") or {}).get("authorized") or [])


def check(perm):
    """某权限是否已授权（未初始化 = 未授权，功能禁用）。"""
    return perm in authorized()


def init_on_first_run(show=True):
    """首次运行：写默认授权（保守：全部未授权，等用户确认）+ 返回是否首次。"""
    c = _load_cfg()
    if "perms" in c:
        return False
    c["perms"] = {"authorized": [], "init_time": _now()}
    _save_cfg(c)
    if show:
        print("★ Fairy 需要以下权限（首次使用请确认；不给的对应功能不可用）：")
        for pid, name, use, no in PERMS:
            print("  · %s：%s（不授权 → %s）" % (name, use, no))
        print("  授权方式：球右键 → 设置 → 权限设置；或 py -3.12 fairy_perms.py --grant 全部")
    return True


def set_perms(ids):
    """授权指定项（ids 为 perm id 列表；'all' = 全部）。"""
    c = _load_cfg()
    if "all" in ids:
        ids = [p[0] for p in PERMS]
    ids = [i for i in ids if i in _ID2NAME]
    c["perms"] = {"authorized": ids, "updated": _now()}
    return _save_cfg(c)


def revoke(ids):
    """撤销指定项。"""
    cur = set(authorized())
    for i in ids:
        cur.discard(i)
    return set_perms(sorted(cur))


def _now():
    import time
    return time.strftime("%Y-%m-%d %H:%M:%S")


def summary():
    """一行摘要（球菜单显示）。"""
    au = authorized()
    if not au:
        return "权限：未授权（功能受限）"
    names = "、".join(_ID2NAME.get(i, i) for i in au)
    return "权限：已授权 %s" % names


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--grant" in a:
        i = a.index("--grant") + 1
        ids = a[i] if i < len(a) else ""
        ok = set_perms([x.strip() for x in ids.split(",") if x.strip()])
        print("授权成功" if ok else "授权失败")
    elif "--revoke" in a:
        i = a.index("--revoke") + 1
        ids = a[i] if i < len(a) else ""
        ok = revoke([x.strip() for x in ids.split(",") if x.strip()])
        print("撤销成功" if ok else "撤销失败")
    else:
        print("当前授权：%s" % ("、".join(authorized()) or "无（功能受限）"))
        print("完整权限清单：")
        for pid, name, use, no in PERMS:
            print("  · %s [%s]：%s（不授权 → %s）" % (name, pid, use, no))
