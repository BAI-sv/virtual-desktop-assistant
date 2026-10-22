#!/usr/bin/env python3
"""strata_ask.py — 把「该想」类问题发给本地 Strata 模型

【为什么要这个东西】
  用户的输出策略把任务分成两类：
    · 直答类（数字/日期/定义/翻译/查表/回显）-> 主 agent 自己做，秒答
    · 该想类（多步推理/方案权衡/写作/调试/歧义需求）-> 推给别的 AI
  Strata 是本地部署的 OpenAI 兼容服务（E:\\Strata，端口 8080），
  调用【零 API 费用】，代价是慢（实测约 10 tok/s，600 token 的回答约 50 秒）。

【为什么不用 DSH 的 provider 系统】
  DSH 内置 provider 只有 deepseek-account / deepseek-official，
  加自定义 provider 要改 app.asar。而 Strata 是标准 HTTP 服务，直接调更简单。

【★★ 关键：思考会吃光 max_tokens】
  实测（2026-10-10）：一个"方案权衡"问题，max_tokens=700 全被思考用光，
  content 返回空字符串 —— 因为 reasoning 和 content 共享同一个 token 配额。
  Strata 提供两个字段治它（读 E:\\Strata\\serve\\server.py 确认）：
    · reasoning_effort        : none | low | medium | high
    · reasoning_budget_tokens : 整数，0 = 无预算；到点会强制收尾出答案
  本工具默认 reasoning_budget_tokens=800，就是为了保证答案一定有配额。

【用法】
  py -3.12 strata_ask.py "你的问题"
  py -3.12 strata_ask.py --status                     # 只看服务状态
  py -3.12 strata_ask.py --json "问题"                 # 输出 JSON，便于解析
  py -3.12 strata_ask.py --effort high --budget 1500 "难题"
  py -3.12 strata_ask.py --no-think "简单但需要它答的问题"
  py -3.12 strata_ask.py --reasoning "问题"            # 连思考过程一起打印
  py -3.12 strata_ask.py --system "你是审稿人" "问题"
  py -3.12 strata_ask.py --file 问题.txt
  py -3.12 strata_ask.py --stream "问题"               # 流式，能看进度
  py -3.12 strata_ask.py --save "问题"                 # 答案存到 logs/strata/

【★ 在 DSH 里调用时【必须用后台任务】
  中等问题约 50 秒，而工具默认超时约 30 秒，前台跑会被杀掉。

【配置】地址优先级：--url > 环境变量 STRATA_URL > fairy_root.STRATA_URL > 127.0.0.1:8080
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_URL = "http://127.0.0.1:8080"
DEFAULT_TIMEOUT = 600
DEFAULT_MAX_TOKENS = 2500
# ★★ 思考预算要【小】（实测 2026-10-10）：
#    budget=200 -> 思考 392 字就收尾，答案 2340 字 ✓
#    budget=1200 -> 思考照样吃光 2000，答案为空 ✗
#    不给预算  -> 思考膨胀到 7396 字，把 4000 全吃掉，答案为空 ✗
#   道理：预算是"到点强制收尾"，不是"允许它想这么多"。
DEFAULT_BUDGET = 300
EFFORTS = ("none", "low", "medium", "high")


def resolve_url(cli_url=None):
    """地址优先级：--url > 环境变量 > fairy_root 配置 > 默认。"""
    if cli_url:
        return cli_url.rstrip("/")
    env = os.environ.get("STRATA_URL")
    if env:
        return env.rstrip("/")
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import fairy_root
        u = getattr(fairy_root, "STRATA_URL", None)
        if u:
            return str(u).rstrip("/")
    except Exception:
        pass
    return DEFAULT_URL


def http_json(url, payload=None, timeout=30):
    """发一个 JSON 请求；返回 (ok, data_or_errmsg)。"""
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
        try:
            return True, json.loads(raw)
        except Exception:
            return True, {"_raw": raw}
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            pass
        return False, "HTTP %s: %s" % (e.code, body)
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, e)


def service_status(base):
    ok, d = http_json(base + "/api/health", timeout=8)
    if ok and isinstance(d, dict) and d.get("status") == "ok":
        return True, "model=%s ctx=%s loaded=%s" % (
            d.get("model", "?"), d.get("max_context", "?"), d.get("loaded")), d.get("model")
    return False, str(d)[:200], None


def pick_model(base, override=None):
    if override:
        return override
    ok, d = http_json(base + "/v1/models", timeout=10)
    if ok and isinstance(d, dict):
        arr = d.get("data") or []
        if arr and isinstance(arr[0], dict) and arr[0].get("id"):
            return arr[0]["id"]
    return "strata"


def build_payload(model, messages, max_tokens, temperature, effort, budget, stream):
    """组装请求体。effort/budget 见模块文档。"""
    p = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": stream,
    }
    if effort:
        p["reasoning_effort"] = effort
    if budget is not None and budget > 0:
        p["reasoning_budget_tokens"] = budget
    elif budget == 0:
        p["reasoning_budget_tokens"] = 0
    return p


def ask_stream(base, payload, timeout):
    """流式请求，边收边打印。返回 (content, reasoning, usage)。"""
    payload = dict(payload)
    payload["stream"] = True
    req = urllib.request.Request(
        base + "/v1/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    content, reasoning, usage = [], [], {}
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if chunk == "[DONE]":
                break
            try:
                d = json.loads(chunk)
            except Exception:
                continue
            if d.get("usage"):
                usage = d["usage"]
            for ch in (d.get("choices") or []):
                delta = ch.get("delta") or {}
                if delta.get("content"):
                    content.append(delta["content"])
                    sys.stdout.write(delta["content"])
                    sys.stdout.flush()
                if delta.get("reasoning_content"):
                    reasoning.append(delta["reasoning_content"])
    print()
    return "".join(content), "".join(reasoning), usage


def ask_once(base, payload, timeout):
    ok, d = http_json(base + "/v1/chat/completions", payload, timeout=timeout)
    if not ok:
        return None, None, {}, str(d)
    ch = (d.get("choices") or [{}])[0]
    msg = ch.get("message") or {}
    return ((msg.get("content") or ""), (msg.get("reasoning_content") or ""),
            (d.get("usage") or {}), None)


# ---------------------------------------------------------------------------
# 自适应分类：直答类不跑推理，该想类才跑
#
#   直答类（数字/日期/定义/翻译/查表/代码语法/事实回显）-> 不调用逻辑
#   该想类（多步推理/方案权衡/写作创作/代码调试/歧义需求/权衡建议）-> 进行逻辑推理
#
# 设计取舍：
#   · 规则优先 —— 零成本、毫秒级；拿不准【不拉满】，用 medium 中间档
#   · 宁可略偏"该想"也不要误判成"直答"，因为答错的代价大于多跑一次思考
# ---------------------------------------------------------------------------
DIRECT_HINTS = (
    "翻译", "什么意思", "是什么", "什么是", "定义", "谁是", "哪一年", "哪年",
    "多少", "几号", "几点", "星期几", "拼写", "怎么读", "全称", "缩写",
    "语法", "等于多少", "计算结果", "的首都", "属于哪个", "哪个版本",
)
THINK_HINTS = (
    "为什么", "为何", "如何", "怎么设计", "怎么选", "怎么调", "怎么样才能",
    "方案", "权衡", "取舍", "分析", "比较", "对比", "优劣", "优缺点",
    "调试", "排查", "根因", "定位问题", "架构", "重构", "优化", "设计一",
    "写一篇", "写个", "创作", "起草", "建议", "该不该", "评估", "评审",
    "计划", "步骤", "流程", "证明", "推导", "原理", "机制", "策略",
    "调度", "最省", "省钱", "哪种", "怎么做", "怎么办", "能不能",
)
CODE_HINTS = ("def ", "class ", "import ", "Traceback", "SELECT ", "function ",
              "报错", "异常", "error", "Error", "```")


def classify(q):
    """按「直答 vs 该想」策略判断该怎么跑。

    返回 (kind, effort, budget, why)：
      kind   : 'direct' | 'think'
      effort : reasoning_effort 的值
      budget : reasoning_budget_tokens（思考预算）
      why    : 为什么这么判（打印出来便于核对）
    """
    s = (q or "").strip()
    n = len(s)
    low = s.lower()

    think_hits = [h for h in THINK_HINTS if h in s]
    direct_hits = [h for h in DIRECT_HINTS if h in s]
    code_hit = [h for h in CODE_HINTS if (h in s or h in low)]

    # 1) 明确的该想信号
    if think_hits:
        return "think", "high", 400, "命中该想特征：" + "、".join(think_hits[:3])
    # 2) 含代码 / 报错 -> 该想
    if code_hit:
        return "think", "high", 400, "含代码或报错：" + "、".join(code_hit[:2])
    # 3) 明确的直答信号 + 不长 -> 直答
    if direct_hits and n <= 80:
        return "direct", "none", 0, "命中直答特征：" + "、".join(direct_hits[:3])
    # 4) 极短（回执 / 闲聊）-> 直答
    if n <= 15:
        return "direct", "none", 0, "极短（%d 字），按回执/闲聊处理" % n
    # 5) 短但没命中任何特征 -> 拿不准，中间档（不拉满，也不完全不思考）
    if n <= 60:
        return "think", "medium", 300, "短（%d 字）但未命中直答特征，用中间档" % n
    # 6) 很长 -> 多半要多步推理
    if n >= 150:
        return "think", "high", 400, "问题较长（%d 字），多半需要多步推理" % n
    # 7) 其余 -> 中间档
    return "think", "medium", 300, "未命中明确特征，用中间档（不拉满）"


def main():
    ap = argparse.ArgumentParser(
        description="把「该想」类问题发给本地 Strata 模型（零 API 费，但慢）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="提示：若 content 为空，说明思考吃光了配额 —— 调小 --budget 或调大 --max-tokens。",
    )
    ap.add_argument("question", nargs="?", help="问题正文（或用 --file）")
    ap.add_argument("--file", help="从文件读问题")
    ap.add_argument("--url", help="Strata 地址（默认 127.0.0.1:8080）")
    ap.add_argument("--model", help="模型名（默认自动取）")
    ap.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS,
                    help="最大输出 token（默认 %d，含思考）" % DEFAULT_MAX_TOKENS)
    ap.add_argument("--budget", type=int, default=None,
                    help="思考 token 预算，0=不限；不给则【自动判断】（默认自动）")
    ap.add_argument("--effort", choices=EFFORTS, default=None,
                    help="思考力度；不给则【自动判断】：直答类 none / 该想类 high 或 medium")
    ap.add_argument("--no-think", action="store_true", help="强制关掉思考（等于 --effort none）")
    ap.add_argument("--temperature", type=float, default=0.3, help="温度（默认 0.3）")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="超时秒（默认 600）")
    ap.add_argument("--system", help="系统提示词")
    ap.add_argument("--reasoning", action="store_true", help="连思考过程一起打印")
    ap.add_argument("--json", action="store_true", help="输出 JSON（便于程序解析）")
    ap.add_argument("--stream", action="store_true", help="流式输出（能看进度）")
    ap.add_argument("--save", action="store_true", help="答案存到 logs/strata/")
    ap.add_argument("--status", action="store_true", help="只查服务状态")
    args = ap.parse_args()

    base = resolve_url(args.url)

    if args.status:
        up, info, _ = service_status(base)
        print(("UP   " if up else "DOWN ") + base)
        print("     " + info)
        if not up:
            print("")
            print("提示：Strata 没在跑。启动命令（在 Strata 安装目录下执行）：")
            print("  .venv\\Scripts\\python.exe serve\\server.py "
                  "--engine strata --config <你的配置>.json --port 8080 --open")
        return 0 if up else 2

    question = args.question
    if args.file:
        with open(args.file, encoding="utf-8") as f:
            question = f.read()
    if not question:
        ap.print_help()
        return 2

    up, info, _ = service_status(base)
    if not up:
        print("★ Strata 服务没起来（%s）：%s" % (base, info), file=sys.stderr)
        return 2

    # ---- 自适应：没显式指定就自动分类（直答类不跑推理，该想类才跑）----
    if args.no_think:
        kind, effort, budget, why = "direct", "none", 0, "用户指定 --no-think"
    elif args.effort is not None or args.budget is not None:
        kind = "manual"
        effort = args.effort or "medium"
        budget = args.budget if args.budget is not None else DEFAULT_BUDGET
        why = "用户显式指定"
    else:
        kind, effort, budget, why = classify(question)

    # 直答类不需要思考预算
    if effort == "none":
        budget = 0

    model = pick_model(base, args.model)
    messages = []
    if args.system:
        messages.append({"role": "system", "content": args.system})
    messages.append({"role": "user", "content": question})

    payload = build_payload(model, messages, args.max_tokens, args.temperature,
                            effort, args.budget, args.stream)

    if not args.json:
        print("── Strata ──────────────────────────────────────")
        print("   %s · %s" % (base, model))
        print("   问题 %d 字 · max_tokens=%d" % (len(question), args.max_tokens))
        if kind == "direct":
            print("   ★ 判定：直答类 -> 不跑推理（reasoning_effort=none）")
        elif kind == "think":
            print("   ★ 判定：该想类 -> 跑推理（effort=%s，思考预算=%s）" %
                  (effort, budget if budget else "不限"))
        else:
            print("   模式：用户指定（effort=%s，思考预算=%s）" %
                  (effort, budget if budget else "不限"))
        print("      依据：%s" % why)
        print("   ★ 本地模型较慢（约 10 tok/s），请耐心等")
        print("")

    t0 = time.time()
    err = None
    if args.stream and not args.json:
        try:
            content, reasoning, usage = ask_stream(base, payload, args.timeout)
        except Exception as e:
            content, reasoning, usage, err = "", "", {}, "%s: %s" % (type(e).__name__, e)
    else:
        content, reasoning, usage, err = ask_once(base, payload, args.timeout)
    secs = time.time() - t0

    if err:
        print("★ 请求失败：%s" % err, file=sys.stderr)
        return 1

    ct = usage.get("completion_tokens") or 0
    tps = (ct / secs) if secs > 0 and ct else 0
    empty = not (content or "").strip()

    if args.json:
        print(json.dumps({
            "ok": True, "url": base, "model": model, "effort": effort,
            "budget": args.budget, "content": content,
            "reasoning": reasoning if args.reasoning else "",
            "content_empty": empty, "secs": round(secs, 1),
            "usage": usage, "tps": round(tps, 1),
        }, ensure_ascii=False, indent=2))
    else:
        if args.reasoning and reasoning:
            print("── 思考过程 ────────────────────────────────────")
            print(reasoning.strip())
            print("")
        print("── 答案 ────────────────────────────────────────")
        print((content or "").strip())
        print("")
        print("── 统计 ────────────────────────────────────────")
        print("   耗时 %.1fs · 完成 %s tokens · %.1f tok/s" % (secs, ct, tps))
        if empty:
            print("   ★★ content 为空！思考吃光了 max_tokens。")
            print("      对策：调小 --budget（如 500）或调大 --max-tokens（如 3000），重试。")

    if args.save:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import fairy_root
            d = os.path.join(fairy_root.LOGS, "strata")
        except Exception:
            d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs", "strata")
        os.makedirs(d, exist_ok=True)
        fn = os.path.join(d, time.strftime("ask_%Y%m%d_%H%M%S.md"))
        with open(fn, "w", encoding="utf-8") as f:
            f.write("# Strata 问答\n\n")
            f.write("- 时间：%s\n- 模型：%s\n- effort：%s · 思考预算：%s\n"
                    "- 耗时：%.1fs（%s tokens，%.1f tok/s）\n\n" %
                    (time.strftime("%Y-%m-%d %H:%M:%S"), model, effort, args.budget, secs, ct, tps))
            f.write("## 问题\n\n%s\n\n" % question)
            if reasoning:
                f.write("## 思考过程\n\n%s\n\n" % reasoning)
            f.write("## 答案\n\n%s\n" % (content or ""))
        if not args.json:
            print("   已存：%s" % fn)

    return 0 if not empty else 3


if __name__ == "__main__":
    sys.exit(main())
