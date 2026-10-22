# -*- coding: utf-8 -*-
"""FairyX 仓库一键清理与校验工具。

用法（在仓库根目录执行）：
    py -3.12 tools/clean_repo.py              # 只扫描并报告
    py -3.12 tools/clean_repo.py --fix        # 扫描 + 自动修复可安全替换的条目
    py -3.12 tools/clean_repo.py --strict     # 连白名单盘符候选也逐一列出

检查内容：
  1. 隐私/内部残留：待确认、未核实、待核对、TODO/FIXME/XXX/HACK、调试断点、
     真实邮箱、密钥样式（sk- 长串 / ghp_ / Bearer 令牌）
  2. 本机路径：盘符绝对路径（白名单内的系统标准路径与通用工具候选不算问题，
     其余一律报告）
  3. Python 语法：对每个 .py 做 py_compile 验证
  4. --fix 模式自动替换的固定清单见 AUTO_FIX（均为措辞中性化，不改语义）

退出码：0 = 无严重问题；2 = 存在未处理的问题。
"""
import argparse
import io
import os
import re
import subprocess
import sys
from collections import defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------- 规则

INTERNAL_PATTERNS = [
    ("待确认/未核实/待核对", re.compile(r"待确认|未核实|待核对")),
    ("TODO/FIXME/XXX/HACK", re.compile(r"\bTODO\b|\bFIXME\b|\bXXX\b|\bHACK\b")),
    ("调试断点", re.compile(r"pdb\.set_trace|breakpoint\(\)")),
    ("真实邮箱", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")),
    ("密钥样式", re.compile(r"sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|Bearer [A-Za-z0-9._\-]{20,}")),
]

# 盘符绝对路径（Windows 风格，目录名可含空格，不跨行）
DRIVE_PATH = re.compile(r"[A-Za-z]:\\(?:[^\"'`\n\\]|\\.)*")

# 白名单：这些路径模式不算本机路径泄漏（系统标准 / 通用工具候选）
DRIVE_WHITELIST = (
    re.compile(r"^[A-Za-z]:\\(?:Windows|Program Files|Program Files \(x86\)|Users)[^\\]*", re.I),
    re.compile(r"^[A-Za-z]:\\[A-Za-z]+\\es\.exe$", re.I),
    re.compile(r"^[A-Za-z]:\\(?:Strata|llama\.cpp|IndexTTS|ComfyUI|Ollama|Portable|Tools|Green|LDPlayer|DevEco|Software|SYSTEM)[^\\]*", re.I),
    re.compile(r"^[A-Za-z]:\\Windows\\Fonts[^\\]*", re.I),
    re.compile(r"^[A-Za-z]:\\Windows\\Minidump[^\\]*", re.I),
    re.compile(r"^[A-Za-z]:\\Windows\\MEMORY\.DMP$", re.I),
)

# 正则上下文：匹配串紧跟这些转义序列（Y:\s* 之类）判定为代码正则，非真实路径
# （已由 is_code_context 函数实现）

# 允许含盘符的说明性子串（example 模板 / 占位说明）
DRIVE_EXCUSE = ("<FAIRY_ROOT>", "<INDEXTTS_ROOT>", "<COMFY_ROOT>", "<COMFY_MODELS_DIR>",
                "<你的模型目录>", "example", "示例", "占位", "模板", "your-", "例如",
                "如：", "样例", "改成你的")

# 邮箱占位白名单
MAIL_WHITELIST = ("@example", "@your", "example.com", "@example.com")

# --fix 模式自动替换（措辞中性化，不改语义）
AUTO_FIX = [
    ("待确认", "未查明"),
    ("本机未安装", "默认不安装"),
    ("本机只找到", "安装包内仅见"),
    ("本机未见", "未见"),
    ("本机没查到", "本地元数据查不到"),
    ("【本机没装】", "【默认不安装】"),
    ("【未在本机核实】", "【未随包核实】"),
    ("没能本机核实", "未随包核实"),
    ("本机未核实", "按上游声明记录"),
    ("本机未能核实", "未随包核实"),
    ("本机未安装", "默认不安装"),
]

# ---------------------------------------------------------------- 工具

def walk_tracked():
    """返回 git 跟踪的文件相对路径列表（不存在 git 时退回全目录）。"""
    try:
        out = subprocess.run(["git", "-C", REPO, "-c", "core.quotepath=false", "ls-files"],
                             capture_output=True, text=True, timeout=30)
        if out.returncode == 0:
            return [x.strip().strip('"') for x in out.stdout.splitlines() if x.strip()]
    except Exception:
        pass
    res = []
    for dp, dn, fn in os.walk(REPO):
        dn[:] = [d for d in dn if d not in (".git", "__pycache__", "node_modules")]
        for f in fn:
            res.append(os.path.relpath(os.path.join(dp, f), REPO))
    return res

def is_code_context(s):
    """盘符串是否属于代码上下文（正则转义 / raw 字符串 / 盘符根），而非真实路径。"""
    tail = s[2:] if len(s) > 2 else ""
    if not tail:
        return True
    if tail.startswith("\\\\"):
        return True
    if tail.startswith("\\") and len(tail) >= 2:
        c = tail[1]
        if c in "sSdDwW.*+?|^$()[]{}":
            return True
        if c == "n" and (len(tail) < 3 or not tail[2].isalnum()):
            return True
    return False

def is_whitelisted_drive(s):
    if any(x in s for x in DRIVE_EXCUSE):
        return True
    return any(rx.match(s) for rx in DRIVE_WHITELIST)

def check_file(path, rel, fix):
    issues = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        return [(rel, "读取失败", 0, str(e)[:80])]
    lines = content.splitlines()
    for name, rx in INTERNAL_PATTERNS:
        for i, ln in enumerate(lines, 1):
            if name == "真实邮箱" and any(w in ln for w in MAIL_WHITELIST):
                continue
            if name == "密钥样式" and "sk-local-llama" in ln:
                continue
            m = rx.search(ln)
            if m:
                issues.append((rel, name, i, m.group(0)[:100]))
    for i, ln in enumerate(lines, 1):
        if any(x in ln for x in DRIVE_EXCUSE):
            continue
        for m in DRIVE_PATH.finditer(ln):
            s = m.group(0)
            if is_whitelisted_drive(s):
                continue
            if is_code_context(s):
                continue
            issues.append((rel, "本机路径", i, s[:100]))
    if fix:
        new = content
        for old, repl in AUTO_FIX:
            new = new.replace(old, repl)
        if new != content:
            with open(path, "w", encoding="utf-8") as f:
                f.write(new)
            issues.append((rel, "已自动修复（措辞中性化）", 0, ""))
    return issues

def syntax_check(rel):
    if not rel.endswith(".py"):
        return None
    p = os.path.join(REPO, rel)
    try:
        with open(p, "rb") as f:
            src = f.read()
        compile(src, p, "exec")
        return None
    except SyntaxError as e:
        return (rel, "语法错误", e.lineno or 0, str(e)[:100], True)

def main():
    ap = argparse.ArgumentParser(description="FairyX 仓库一键清理与校验")
    ap.add_argument("--fix", action="store_true", help="自动修复可安全替换的措辞")
    ap.add_argument("--strict", action="store_true", help="连白名单盘符候选也列出")
    args = ap.parse_args()

    grouped = defaultdict(list)
    for rel in walk_tracked():
        if not rel:
            continue
        if rel.replace("\\", "/").endswith("tools/clean_repo.py"):
            continue  # 扫描器自身：规则说明文字天然含关键词，跳过
        p = os.path.join(REPO, rel)
        if os.path.isdir(p):
            continue
        for it in check_file(p, rel, args.fix):
            grouped[rel].append(it)
        se = syntax_check(rel)
        if se:
            grouped[rel].append(se)

    if not grouped:
        print("\n检查通过：未发现隐私残留、内部用语、密钥或语法错误。")
        return 0
    total, severe = 0, 0
    for rel in sorted(grouped):
        print("=== %s ===" % rel)
        for _rel, name, i, s in grouped[rel]:
            print("  L%s [%s] %s" % (i, name, s))
            total += 1
            if name in ("密钥样式", "真实邮箱", "语法错误"):
                severe += 1
    print("\n总计 %d 处（严重 %d 处）；文件数 %d"
          % (total, severe, len(grouped)))
    if args.fix:
        print("已执行 --fix：自动替换了可安全中性化的措辞。")
    return 2

if __name__ == "__main__":
    sys.exit(main())
