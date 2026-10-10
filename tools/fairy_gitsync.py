# -*- coding: utf-8 -*-
import argparse, datetime, os, subprocess, sys, time


CREATE_NO_WINDOW = 0x08000000


DEFAULT_REPO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "opensource", "fairyx_repo")
if not os.path.isdir(DEFAULT_REPO):
    DEFAULT_REPO = ""
REMOTE = "origin"
BRANCH = "main"

def run(cmd, cwd):


    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120,
                       creationflags=CREATE_NO_WINDOW)
    return p.returncode, (p.stdout + p.stderr).strip()

def sync(repo):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    rc, status = run(["git", "status", "--porcelain"], repo)
    if rc != 0:
        return "git status 失败: %s" % status
    if not status.strip():
        return "无变更，跳过（%s）" % now

    run(["git", "add", "-A"], repo)
    changed = [l.split(" ")[-1] for l in status.splitlines() if l.strip()][:8]
    msg = "FairyX 自动同步 %s\n\n变更: %s" % (now, "、".join(changed))
    rc, out = run(["git", "commit", "-m", msg], repo)
    if rc != 0 and "nothing to commit" not in out:
        return "commit 失败: %s" % out

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
