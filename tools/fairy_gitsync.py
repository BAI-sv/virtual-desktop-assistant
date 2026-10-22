# -*- coding: utf-8 -*-
"""FairyX 自动上传：检测仓库变更 → 自动 commit → push 到 GitHub。
认证复用 Windows 凭据管理器（GitHub Desktop 登录时写入），全程无需输入密码。

用法：
  py -3.12 tools/fairy_gitsync.py            # 同步一次（有变更则 commit+push）
  py -3.12 tools/fairy_gitsync.py --cron 30  # 守护模式：每 30 分钟检查一次
  py -3.12 tools/fairy_gitsync.py --repo 路径 # 指定仓库（默认 fairyx_repo）
"""
import argparse, datetime, os, subprocess, sys, time

# ★ 2026-10-10【黑窗】0x08000000 = CREATE_NO_WINDOW（不是 0x00000200）。
#   父进程没控制台时（pythonw / 计划任务），不加这个起 git 就会弹终端。
CREATE_NO_WINDOW = 0x08000000

# 默认仓库：脚本上级的 opensource/fairyx_repo（不存在时用 --repo 指定）
DEFAULT_REPO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "opensource", "fairyx_repo")
if not os.path.isdir(DEFAULT_REPO):
    DEFAULT_REPO = ""
REMOTE = "origin"
BRANCH = "main"


def run(cmd, cwd):
    # ★ 2026-10-10【黑窗】git.exe 是控制台程序；本脚本会被 pythonw / 计划任务拉起
    #   （无控制台）-> 不加这个每次同步都弹一个终端窗口。
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120,
                       creationflags=CREATE_NO_WINDOW)
    return p.returncode, (p.stdout + p.stderr).strip()


def sync(repo):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    # 1) 有变更吗
    rc, status = run(["git", "status", "--porcelain"], repo)
    if rc != 0:
        return "git status 失败: %s" % status
    if not status.strip():
        return "无变更，跳过（%s）" % now
    # 2) 暂存并提交
    run(["git", "add", "-A"], repo)
    changed = [l.split(" ")[-1] for l in status.splitlines() if l.strip()][:8]
    msg = "FairyX 自动同步 %s\n\n变更: %s" % (now, "、".join(changed))
    rc, out = run(["git", "commit", "-m", msg], repo)
    if rc != 0 and "nothing to commit" not in out:
        return "commit 失败: %s" % out
    # 3) 推送（凭据管理器自动认证）
    rc, out = run(["git", "push", REMOTE, BRANCH], repo)
    if rc != 0:
        return "push 失败: %s" % out
    return "已自动上传 %s -> %s（%s）" % (REMOTE, BRANCH, now)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--cron", type=int, default=0, help="守护间隔分钟，0=只跑一次")
    a = ap.parse_args()
    if not os.path.isdir(os.path.join(a.repo, ".git")):
        print("不是 git 仓库: %s" % a.repo); sys.exit(1)
    if a.cron > 0:
        print("守护模式：每 %d 分钟检查一次 %s" % (a.cron, a.repo))
        while True:
            print(sync(a.repo), flush=True)
            time.sleep(a.cron * 60)
    else:
        print(sync(a.repo))


if __name__ == "__main__":
    main()
