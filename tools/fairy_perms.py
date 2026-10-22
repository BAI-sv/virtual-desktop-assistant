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
    """首次运行：写默认授权（保守：全部未授权，等用户确认）+ 返回是否首次。

    ★★ 2026-10-18【用户要求「权限授权删除，第一次安装启动时向使用人申请权限」】
      这里原来只 `print(...)` 一段说明 —— 但 Fairy 是 pythonw.exe（无控制台）启动的，
      **首次运行用户根本看不到这段话**，结果 perms.authorized 永远是空，
      麦克风/窗口读取/本地服务这些功能全被静默禁用，像"坏了"。
      现在这里只负责落一个"还没问过"的标记；真正的申请由球在启动后弹窗
      （见 ask_with_tk / fairy_ball._ask_perms_first_run）。
    """
    c = _load_cfg()
    if "perms" in c:
        return False
    c["perms"] = {"authorized": [], "init_time": _now(), "asked": False}
    _save_cfg(c)
    return True


def first_run_pending():
    """还要不要向用户申请权限：没写过 perms，或写过但没问过（asked=False）。"""
    c = _load_cfg()
    seg = c.get("perms")
    if not isinstance(seg, dict):
        return True
    return not bool(seg.get("asked"))


def mark_asked():
    """标记"已经问过用户了"（无论同意还是拒绝，都别再弹）。"""
    c = _load_cfg()
    seg = c.setdefault("perms", {})
    seg["asked"] = True
    seg["asked_time"] = _now()
    return _save_cfg(c)


def ask_with_tk(parent):
    """弹一个 Tk 窗，把权限清单和用途摊开，让用户一次性决定。

    返回 "allow"（全部允许）/"later"（稍后再说）/ None（窗口关掉了）。
    设计上只给两个按钮：逐项勾选那套已经被用户要求删掉了。
    """
    import tkinter as tk
    win = tk.Toplevel(parent)
    win.title("Fairy 首次使用 —— 请确认权限")
    win.attributes("-topmost", True)
    win.configure(bg="#f2f5f8")
    win.resizable(False, False)
    win.geometry("+%d+%d" % (max(0, parent.winfo_screenwidth() // 2 - 260),
                             max(0, parent.winfo_screenheight() // 2 - 240)))

    tk.Label(win, text="Fairy 首次使用，需要你确认这些权限", bg="#f2f5f8",
             fg="#52749b", font=("Microsoft YaHei", 14, "bold")
             ).pack(anchor="w", padx=20, pady=(16, 2))
    tk.Label(win, text="不给也能用，只是对应功能会关闭。以后想改：球右键 → 记忆与说明 → 权限说明。",
             bg="#f2f5f8", fg="#66727f", font=("Microsoft YaHei", 10),
             wraplength=470, justify="left").pack(anchor="w", padx=20, pady=(0, 10))

    box = tk.Frame(win, bg="#ffffff", bd=1, relief="solid")
    box.pack(fill="both", expand=True, padx=20, pady=(0, 12))
    for pid, name, use, no in PERMS:
        row = tk.Frame(box, bg="#ffffff")
        row.pack(fill="x", padx=10, pady=3)
        tk.Label(row, text="· " + name, bg="#ffffff", fg="#46536a", width=10,
                 anchor="w", font=("Microsoft YaHei", 10, "bold")).pack(side="left")
        tk.Label(row, text=use, bg="#ffffff", fg="#66727f", anchor="w",
                 font=("Microsoft YaHei", 10)).pack(side="left", fill="x", expand=True)
        tk.Label(row, text="不授权：" + no, bg="#ffffff", fg="#a1887f",
                 font=("Microsoft YaHei", 9)).pack(side="right")

    res = {"v": None}

    def _allow():
        res["v"] = "allow"
        win.destroy()

    def _later():
        res["v"] = "later"
        win.destroy()

    btns = tk.Frame(win, bg="#f2f5f8")
    btns.pack(pady=(0, 16))
    tk.Button(btns, text="全部允许", width=12, bg="#52749b", fg="#ffffff",
              font=("Microsoft YaHei", 11, "bold"), bd=0, cursor="hand2",
              command=_allow).pack(side="left", padx=8, ipady=4)
    tk.Button(btns, text="稍后再说", width=12, bg="#e6ebf2", fg="#46536a",
              font=("Microsoft YaHei", 11), bd=0, cursor="hand2",
              command=_later).pack(side="left", padx=8, ipady=4)

    try:
        win.transient(parent)
        win.grab_set()
    except Exception:
        pass
    parent.wait_window(win)
    return res["v"]


def set_perms(ids):
    """授权指定项（ids 为 perm id 列表；'all' = 全部）。

    ★ 2026-10-18：以前这里把整个 `perms` 段**替换掉** —— 于是 set_perms 之后
      `asked` 字段没了，first_run_pending() 又变 True，**每次启动都弹权限窗**。
      现在只改 authorized，其它字段（asked / init_time 等）原样保留。
    """
    c = _load_cfg()
    if "all" in ids:
        ids = [p[0] for p in PERMS]
    ids = [i for i in ids if i in _ID2NAME]
    seg = c.setdefault("perms", {})
    seg["authorized"] = ids
    seg["updated"] = _now()
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
