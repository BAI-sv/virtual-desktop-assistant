# -*- coding: utf-8 -*-
r"""fairy_ocr_view.py —— 把 OCR 结果放进浏览器，可编辑、可复制。

★ 2026-10-18【用户要求「识别文字要在浏览器打开并能编辑复制」】
  原来 OCR 只把文字打到 stdout / 命令行，用户没法改也没法方便地复制。
  现在生成一个本地 HTML 页面，用默认浏览器打开：
    · 上面是**可编辑**的大文本框（识别结果直接进去，随手改错字）
    · 每一行也能单独点一下复制
    · 「复制全部」按 navigator.clipboard，失败自动退回 execCommand
    · 「下载 txt」存成文件

用法：
    py fairy_ocr_view.py --json-file data/ocr/last.json   # {"lines":[...]}
    py fairy_ocr_view.py --text "识别的文字"
    py fairy_ocr_view.py --lines-file data/ocr/last.txt

不启服务，纯静态页面 + file:// 打开，关掉浏览器就结束。
"""
import argparse
import html
import json
import os
import sys
import time
import webbrowser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "data", "ocr")


def _read_lines(args):
    lines = []
    if args.json_file:
        try:
            with open(args.json_file, encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict):
                lines = d.get("lines") or []
                if not lines and d.get("text"):
                    lines = [x for x in str(d["text"]).splitlines()]
            elif isinstance(d, list):
                lines = d
        except Exception as e:
            print("[ocr_view] 读 json 失败: %s" % e, file=sys.stderr)
    elif args.lines_file:
        try:
            with open(args.lines_file, encoding="utf-8") as f:
                lines = [x.rstrip("\n") for x in f]
        except Exception as e:
            print("[ocr_view] 读 txt 失败: %s" % e, file=sys.stderr)
    elif args.text:
        lines = [x for x in args.text.splitlines()]
    return [str(x) for x in lines if str(x).strip()]


_HTML = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>Fairy OCR 结果 — 可编辑</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{--bg:#0f141b;--bg2:#161d27;--fg:#dbe7f3;--fg2:#8aa0b6;--line:#26313f;--brand:#3d8bfd}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.6 ui-sans-serif,system-ui,"Microsoft YaHei",sans-serif}
header{display:flex;align-items:center;gap:10px;padding:12px 16px;background:var(--bg2);border-bottom:1px solid var(--line);position:sticky;top:0}
h1{font-size:15px;margin:0;font-weight:600}
.meta{color:var(--fg2);font-size:12px;margin-left:auto}
button{background:#1d2734;color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:6px 12px;font-size:13px;cursor:pointer}
button:hover{border-color:var(--brand);color:#fff}
button.primary{background:var(--brand);border-color:var(--brand);color:#04121f;font-weight:600}
main{display:grid;grid-template-columns:1fr 380px;gap:14px;padding:14px;align-items:start}
@media(max-width:900px){main{grid-template-columns:1fr}}
.panel{background:var(--bg2);border:1px solid var(--line);border-radius:12px;padding:12px}
.panel h2{font-size:13px;margin:0 0 8px;color:var(--fg2);font-weight:600}
textarea{width:100%;min-height:56vh;background:#0b1017;color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:12px;font:14px/1.7 ui-monospace,Consolas,"Microsoft YaHei",monospace;resize:vertical;outline:none}
textarea:focus{border-color:var(--brand)}
.line{display:flex;gap:8px;align-items:flex-start;padding:5px 6px;border-radius:6px;cursor:pointer}
.line:hover{background:#1b2431}
.line .no{color:var(--fg2);font-size:11px;min-width:22px;text-align:right;padding-top:2px}
.line .tx{flex:1;word-break:break-all}
.tip{color:var(--fg2);font-size:12px;margin:8px 0 0}
.toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%);background:var(--brand);color:#04121f;padding:8px 16px;border-radius:99px;font-weight:600;opacity:0;transition:.2s;pointer-events:none}
.toast.on{opacity:1}
</style></head><body>
<header>
  <h1>Fairy OCR 结果</h1>
  <span class="meta" id="meta"></span>
  <button class="primary" onclick="copyAll()">复制全部</button>
  <button onclick="saveTxt()">下载 txt</button>
</header>
<main>
  <section class="panel">
    <h2>识别文本（可直接编辑）</h2>
    <textarea id="ta" spellcheck="false"></textarea>
    <p class="tip">改完想带走：点「复制全部」或「下载 txt」。也可 Ctrl+A / Ctrl+C。</p>
  </section>
  <section class="panel">
    <h2>逐行（点一下复制这一行）</h2>
    <div id="list"></div>
  </section>
</main>
<div class="toast" id="toast"></div>
<script>
const LINES = __LINES__;
const ta = document.getElementById('ta');
ta.value = LINES.join('\n');
document.getElementById('meta').textContent =
  LINES.length + ' 行 · ' + ta.value.length + ' 字 · ' + __WHEN__;
const list = document.getElementById('list');
LINES.forEach((s, i) => {
  const d = document.createElement('div');
  d.className = 'line';
  d.innerHTML = '<span class="no">' + (i + 1) + '</span><span class="tx"></span>';
  d.querySelector('.tx').textContent = s;
  d.onclick = () => copyText(s, '已复制第 ' + (i + 1) + ' 行');
  list.appendChild(d);
});
function toast(m){ const t = document.getElementById('toast'); t.textContent = m;
  t.classList.add('on'); setTimeout(() => t.classList.remove('on'), 1400); }
async function copyText(s, msg){
  try{
    if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(s); }
    else { const x = document.createElement('textarea'); x.value = s;
      document.body.appendChild(x); x.select(); document.execCommand('copy'); x.remove(); }
    toast(msg || '已复制');
  }catch(e){
    const x = document.createElement('textarea'); x.value = s;
    document.body.appendChild(x); x.select(); document.execCommand('copy'); x.remove();
    toast('已复制');
  }
}
function copyAll(){ copyText(ta.value, '已复制全部 ' + ta.value.length + ' 字'); }
function saveTxt(){
  const blob = new Blob([ta.value], {type: 'text/plain;charset=utf-8'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'ocr_' + new Date().toISOString().slice(0,19).replace(/[:T]/g,'') + '.txt';
  a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 3000);
  toast('已开始下载');
}
</script></body></html>
"""


def build_html(lines):
    data = json.dumps(lines, ensure_ascii=False).replace("</", "<\\/")
    when = json.dumps(time.strftime("%Y-%m-%d %H:%M:%S"))
    return _HTML.replace("__LINES__", data).replace("__WHEN__", when)


def main():
    ap = argparse.ArgumentParser(description="把 OCR 结果放进浏览器（可编辑/可复制）")
    ap.add_argument("--json-file", default="")
    ap.add_argument("--lines-file", default="")
    ap.add_argument("--text", default="")
    ap.add_argument("--no-open", action="store_true", help="只生成 HTML，不自动开浏览器")
    ap.add_argument("--out", default="", help="指定输出 html 路径")
    a = ap.parse_args()

    lines = _read_lines(a)
    os.makedirs(OUT_DIR, exist_ok=True)
    out = a.out or os.path.join(OUT_DIR, "ocr_%s.html" % time.strftime("%Y%m%d_%H%M%S"))
    try:
        with open(out, "w", encoding="utf-8") as f:
            f.write(build_html(lines))
    except Exception as e:
        print(json.dumps({"ok": False, "error": "写页面失败: %s" % e}, ensure_ascii=False))
        return 1

    if not a.no_open:
        try:
            webbrowser.open("file:///" + out.replace("\\", "/"))
        except Exception as e:
            print("[ocr_view] 开浏览器失败: %s（手动打开下面这个文件）" % e, file=sys.stderr)

    print(json.dumps({"ok": True, "html": out, "lines": len(lines)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
