

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


DEFAULT_BUDGET = 300
EFFORTS = ("none", "low", "medium", "high")

def resolve_url(cli_url=None):
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
    s = (q or "").strip()
    n = len(s)
    low = s.lower()

    think_hits = [h for h in THINK_HINTS if h in s]
    direct_hits = [h for h in DIRECT_HINTS if h in s]
    code_hit = [h for h in CODE_HINTS if (h in s or h in low)]


    if think_hits:
        return "think", "high", 400, "命中该想特征：" + "、".join(think_hits[:3])

    if code_hit:
        return "think", "high", 400, "含代码或报错：" + "、".join(code_hit[:2])

    if direct_hits and n <= 80:
        return "direct", "none", 0, "命中直答特征：" + "、".join(direct_hits[:3])

    if n <= 15:
        return "direct", "none", 0, "极短（%d 字），按回执/闲聊处理" % n

    if n <= 60:
        return "think", "medium", 300, "短（%d 字）但未命中直答特征，用中间档" % n

    if n >= 150:
        return "think", "high", 400, "问题较长（%d 字），多半需要多步推理" % n

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


    if args.no_think:
        kind, effort, budget, why = "direct", "none", 0, "用户指定 --no-think"
    elif args.effort is not None or args.budget is not None:
        kind = "manual"
        effort = args.effort or "medium"
        budget = args.budget if args.budget is not None else DEFAULT_BUDGET
        why = "用户显式指定"
    else:
        kind, effort, budget, why = classify(question)


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
