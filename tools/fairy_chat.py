# -*- coding: utf-8 -*-
"""
fairy_chat.py —— Fairy 统一对话页（8090），三栏工作台 + 多模态 + 语音

引擎组合：
    strata -> http://127.0.0.1:8080/v1/chat/completions
    ollama -> http://127.0.0.1:11434/v1/chat/completions
    llm    -> http://127.0.0.1:8085/v1/chat/completions

功能（v2.1，2026-10-15）：
  · 三栏：左会话列表 / 中对话 / 右监控（Strata 风格：模型状态、推理指标、系统区）
  · 输入栏：🖼 图片（需引擎视觉）、📎 文件（读取文本内容）、🎤 语音输入（录音→本地 ASR 转文字）
  · AI 回复自动用 Fairy 克隆音色语音播报；回复下方按钮：🔊 语音播放 / ↻ 上下文压缩接续 / ⧉ 复制 / 📤 分享
  · 语音合成走 Fairy 的 IndexTTS 9881（fairy_endpoints.tts_get），ASR 走 Fairy 的 asr_bridge（SenseVoice/Paraformer）
"""
import base64
import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
# ★ 2026-10-17：GPU 采集要后台线程 —— 原来没 import threading，
#   threading.Thread(...) 直接 NameError，又被 except 吞掉，于是"线程静默不启动"。
import threading
import time as _time
import urllib.error
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from fairy_endpoints import tts_get as _fairy_tts_get
except Exception:
    _fairy_tts_get = None

try:
    import asr_bridge as _asr_bridge
except Exception:
    _asr_bridge = None

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8090
LOG = os.path.join(HERE, "..", "logs", "fairy_chat.log")
def _dsh_base():
    """DSH 的基地址。★ 2026-10-17：DSH 0.2.1 起端口随机（不再是 19387），动态发现。"""
    try:
        import sys as _sys
        _t = os.path.dirname(os.path.abspath(__file__))
        if _t not in _sys.path:
            _sys.path.insert(0, _t)
        import dsh_port as _dp
        _p = _dp.find_port()[0]
        if _p:
            return "http://127.0.0.1:%d" % int(_p)
    except Exception:
        pass
    return "http://127.0.0.1:19387"


_BASE = {"strata": "http://127.0.0.1:8080",
         "ollama": "http://127.0.0.1:11434",
         "llm": "http://127.0.0.1:8085",
         "vision": "http://127.0.0.1:8086",
         "vl": "http://127.0.0.1:8086",
         # ★ 2026-10-21【27B 快脑】llama.cpp 27B，8081
         "llm27": "http://127.0.0.1:8081",
         "dsh": _dsh_base()}
if os.name == "nt":
    CREATE_NO_WINDOW = 0x08000000
else:
    CREATE_NO_WINDOW = 0

# ★★ 2026-10-17【页面全空/一直"加载中"的真凶】
#   这里原先是普通字符串 """..."""，于是内嵌 JS 里的 "\n" 被 Python 提前解释成
#   【真正的换行】——  JS 字符串被迫跨行，整个 <script> 块 SyntaxError，
#   浏览器一个字符都不执行：状态灯永远停在"引擎加载中…"，右侧监控全是「-」。
#   改成 raw string（r"""）后，\n 原样送到浏览器，由 JS 自己解释。
#   ⚠ 以后在 _HTML 里写 JS 的换行必须是 \n（raw 下就是字面量），不要再加转义。
# ★ 2026-10-17：页面构建时间（显示在左上角标题旁），用于区分"新页面 / 旧缓存"
_BUILT_AT = _time.strftime("%m-%d %H:%M")

_HTML = r"""<!doctype html>
<html lang="zh-CN" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fairy 对话</title>
<style>
:root{
  --bg:#0a0e15; --bg2:#101720; --bg3:#17202c;
  --line:rgba(255,255,255,.075);
  --fg:#e9edf4; --fg2:#96a2b5; --fg3:#5f6c80;
  --brand1:#3da9ff; --brand2:#2de2c1;
  --ok:#2fd68a; --warn:#f7b84b; --err:#f2695f;
  --r:14px;
  --sh:0 8px 24px rgba(0,0,0,.30);
}
[data-theme="light"]{
  --bg:#eef2f8; --bg2:#ffffff; --bg3:#f0f4fa;
  --line:rgba(20,35,60,.09);
  --fg:#1c2634; --fg2:#5a6678; --fg3:#8e99ac;
  --sh:0 6px 20px rgba(40,70,110,.08);
}
*{box-sizing:border-box}
html,body{height:100%;margin:0}
body{
  background:
    radial-gradient(1100px 520px at 12% -12%, rgba(61,169,255,.14), transparent 58%),
    radial-gradient(900px 460px at 92% 112%, rgba(45,226,193,.09), transparent 58%),
    var(--bg);
  color:var(--fg);
  font:14.5px/1.6 "Segoe UI","Microsoft YaHei",system-ui,sans-serif;
  display:flex;overflow:hidden;
}
button,input,textarea{font:inherit;color:inherit}
button{cursor:pointer}
/* ============ 三栏 ============ */
.shell{flex:1;display:grid;grid-template-columns:248px 1fr 336px;min-width:0;min-height:0}
.col{display:flex;flex-direction:column;min-width:0;min-height:0}
.col-left{border-right:1px solid var(--line);background:color-mix(in srgb,var(--bg2) 55%,transparent);backdrop-filter:blur(10px)}
.col-right{border-left:1px solid var(--line);background:color-mix(in srgb,var(--bg2) 55%,transparent);backdrop-filter:blur(10px)}
/* ============ 左栏：会话列表 ============ */
.logo{font-weight:800;font-size:16px;letter-spacing:.3px;padding:16px 16px 8px;user-select:none;
  background:linear-gradient(92deg,var(--brand1),var(--brand2));
  -webkit-background-clip:text;background-clip:text;color:transparent}
.newBtn{margin:8px 12px 10px;padding:9px;border-radius:11px;border:1px dashed var(--line);
  background:var(--bg3);color:var(--fg2);font-weight:600;transition:.18s;text-align:center}
.newBtn:hover{color:var(--fg);border-color:var(--brand1)}
.convList{flex:1;overflow-y:auto;padding:2px 10px 12px;display:flex;flex-direction:column;gap:4px}
.conv{display:flex;align-items:center;gap:6px;padding:9px 11px;border-radius:11px;cursor:pointer;
  border:1px solid transparent;transition:.15s;color:var(--fg2)}
.conv:hover{background:var(--bg3);color:var(--fg)}
.conv.on{background:linear-gradient(135deg,rgba(61,169,255,.16),rgba(45,226,193,.10));border-color:rgba(61,169,255,.28);color:var(--fg)}
.conv .t{flex:1;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.conv .del{flex:none;border:0;background:transparent;color:var(--fg3);opacity:0;border-radius:6px;width:20px;height:20px;transition:.15s}
.conv:hover .del{opacity:1}
.conv .del:hover{color:var(--err);background:var(--bg3)}
/* ============ 中间：聊天 ============ */
.col-chat{background:transparent}
.chatTop{flex:none;display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:12px 16px;
  border-bottom:1px solid var(--line);background:color-mix(in srgb,var(--bg2) 60%,transparent)}
.pill{display:inline-flex;align-items:center;gap:7px;padding:4px 12px;border-radius:20px;
  background:var(--bg3);border:1px solid var(--line);font-size:12px;color:var(--fg2);white-space:nowrap}
.pill .d{width:8px;height:8px;border-radius:50%;background:var(--fg3)}
.pill[data-s="ok"] .d{background:var(--ok);box-shadow:0 0 8px var(--ok)}
.pill[data-s="busy"] .d{background:var(--warn);animation:pulse 1.1s infinite}
.pill[data-s="err"] .d{background:var(--err);box-shadow:0 0 8px var(--err)}
@keyframes pulse{50%{opacity:.22}}
.engSel{background:var(--bg3);border:1px solid var(--line);color:var(--fg);border-radius:9px;padding:4px 8px;font-size:12px;outline:none;transition:.18s}
.engSel:hover{border-color:var(--brand1)}
.spacer{flex:1}
.iconBtn{width:34px;height:34px;border-radius:11px;background:var(--bg3);border:1px solid var(--line);color:var(--fg2);display:inline-flex;align-items:center;justify-content:center;transition:all .18s;font-size:15px;cursor:pointer;position:relative}
.iconBtn svg{transition:transform .18s,color .18s}
.iconBtn:hover{color:var(--brand1);border-color:var(--brand1);background:color-mix(in srgb,var(--bg3) 60%,var(--brand1) 14%);transform:translateY(-1px);box-shadow:0 5px 14px rgba(0,0,0,.22)}
.iconBtn:hover svg{transform:scale(1.12)}
.iconBtn:active{transform:translateY(0) scale(.94)}
.iconBtn.sm{width:24px;height:24px;border-radius:7px;font-size:13px}
.msgs{flex:1;overflow-y:auto;padding:20px 18px;display:flex;flex-direction:column;gap:14px;scroll-behavior:smooth}
.msg{display:flex;gap:11px;max-width:86%;align-items:flex-start;animation:rise .25s ease}
@keyframes rise{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.msg .avatar{flex:none;width:32px;height:32px;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:13px;font-weight:800;color:#fff;user-select:none}
.msg.ai .avatar{background:linear-gradient(135deg,var(--brand1),var(--brand2));box-shadow:0 4px 14px rgba(61,169,255,.32)}
.msg.user .avatar{background:linear-gradient(135deg,#2563eb,#38bdf8);box-shadow:0 4px 14px rgba(56,189,248,.25)}
.msg .body{flex:1;display:flex;flex-direction:column;min-width:0}
.msg .bub{padding:11px 15px;border-radius:15px;font-size:14px;white-space:pre-wrap;word-break:break-word;border:1px solid var(--line);box-shadow:var(--sh)}
.msg.user{align-self:flex-end;flex-direction:row-reverse}
.msg.user .bub{background:linear-gradient(135deg,rgba(61,169,255,.20),rgba(45,226,193,.12));border-color:rgba(61,169,255,.32);border-bottom-right-radius:6px}
.msg.ai .bub{background:color-mix(in srgb,var(--bg2) 90%,transparent);border-bottom-left-radius:6px}
.msgImg{max-width:220px;max-height:220px;border-radius:10px;margin-top:6px;display:block;border:1px solid var(--line)}
.fileTag{display:inline-flex;align-items:center;gap:6px;margin-top:6px;padding:4px 10px;border-radius:9px;background:var(--bg3);border:1px solid var(--line);font-size:12px;color:var(--fg2)}
.opRow{display:flex;gap:6px;margin-top:7px;flex-wrap:wrap}
.op{font-size:11px;padding:3px 9px;border-radius:8px;background:var(--bg3);border:1px solid var(--line);color:var(--fg3);transition:.15s}
.op:hover{color:var(--brand1);border-color:var(--brand1)}
.empty{align-self:center;color:var(--fg3);font-size:13px;margin-top:70px;text-align:center;line-height:2}
.inputBar{flex:none;display:flex;gap:8px;padding:12px 16px 4px;border-top:1px solid var(--line);background:color-mix(in srgb,var(--bg2) 70%,transparent);align-items:flex-end}
.inputBar textarea{flex:1;resize:none;background:var(--bg3);border:1px solid var(--line);border-radius:14px;padding:11px 15px;min-height:48px;max-height:150px;outline:none;transition:.18s}
.inputBar textarea:focus{border-color:var(--brand1);box-shadow:0 0 0 3px rgba(61,169,255,.15)}
.sendBtn{background:linear-gradient(92deg,var(--brand1),var(--brand2));color:#04131f;border:0;border-radius:14px;padding:0 22px;font-weight:800;box-shadow:0 5px 18px rgba(61,169,255,.30);transition:.18s;height:50px}
.sendBtn:hover{filter:brightness(1.09);transform:translateY(-1px)}
.sendBtn:disabled{opacity:.5;cursor:default;transform:none;filter:none}
.attachBar{flex:none;display:flex;align-items:center;gap:8px;padding:2px 16px 6px;font-size:12px;color:var(--fg2)}
.recOn{color:var(--err)!important;border-color:var(--err)!important;background:color-mix(in srgb,var(--err) 12%,var(--bg3))!important;animation:pulse 1.1s infinite}
.recOn::after{content:"";position:absolute;top:5px;right:5px;width:7px;height:7px;border-radius:50%;background:var(--err);animation:recDot 1.1s infinite}
@keyframes recDot{0%{box-shadow:0 0 0 0 rgba(242,105,95,.55)}70%{box-shadow:0 0 0 7px transparent}100%{box-shadow:0 0 0 0 transparent}}
/* ============ 右栏：监控（Strata 风格） ============ */
.rHead{flex:none;font-size:13px;font-weight:700;letter-spacing:.8px;text-transform:uppercase;color:var(--fg2);
  padding:14px 16px 10px;border-bottom:1px solid var(--line)}
.rightBody{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:12px}
.card{background:linear-gradient(165deg,var(--bg2),var(--bg3));border:1px solid var(--line);border-radius:var(--r);padding:14px;box-shadow:var(--sh)}
.card .ct{margin:0 0 10px;font-size:11px;color:var(--fg2);font-weight:700;letter-spacing:.6px;text-transform:uppercase}
.stateCard{position:relative;overflow:hidden}
.stateCard::before{content:"";position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(92deg,var(--brand1),var(--brand2));opacity:.55}
/* ★ 2026-10-18：原顶部两个状态框搬到这里后的样式 */
.liveBox{margin:0 0 10px;padding:9px 11px;border-radius:11px;background:var(--bg3);border:1px solid var(--line)}
.liveRow{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--fg2);line-height:1.7}
.liveRow+.liveRow{margin-top:2px}
.liveDot{flex:none;width:8px;height:8px;border-radius:50%;background:var(--fg3);transition:background .2s,box-shadow .2s}
.liveDot[data-s="ok"]{background:var(--ok);box-shadow:0 0 6px var(--ok)}
.liveDot[data-s="err"]{background:var(--err);box-shadow:0 0 6px var(--err)}
.liveDot[data-s="busy"]{background:var(--warn);box-shadow:0 0 6px var(--warn)}
.liveTxt{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.liveModel{color:var(--fg);font-weight:600}
.badges{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}
.badge{padding:3px 10px;border-radius:12px;font-size:11.5px;border:1px solid var(--line);color:var(--fg3);opacity:.5}
.badge.on{opacity:1;font-weight:600}
.badge[data-s="idle"].on{color:var(--ok);border-color:var(--ok)}
.badge[data-s="reading"].on{color:var(--warn);border-color:var(--warn)}
.badge[data-s="generating"].on{color:var(--brand1);border-color:var(--brand1)}
.badge[data-s="queued"].on{color:var(--warn);border-color:var(--warn)}
.badge[data-s="error"].on{color:var(--err);border-color:var(--err)}
.sLabel{font-size:12.5px;color:var(--fg2);min-height:18px}
.prog{height:7px;border-radius:5px;background:var(--bg3);overflow:hidden;margin-top:10px}
.prog i{display:block;height:100%;background:linear-gradient(92deg,var(--brand1),var(--brand2));border-radius:5px;transition:width .5s ease}
.mGrid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}
.mCard{padding:12px;border-radius:13px;border:1px solid var(--line);background:color-mix(in srgb,var(--bg2) 82%,transparent)}
.mLab{display:block;font-size:11px;color:var(--fg3);margin-bottom:6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mVal{font-size:19px;font-weight:800;font-variant-numeric:tabular-nums}
.mVal small{font-size:11px;color:var(--fg3);font-weight:600;margin-left:2px}
.mSub{display:block;font-size:10.5px;color:var(--fg3);margin-top:3px;min-height:13px}
.mWide{grid-column:1/-1}
.sysRow{display:flex;justify-content:space-between;font-size:12px;color:var(--fg2);margin:2px 0 5px}
.sysRow b{font-variant-numeric:tabular-nums}
.prog.sm{height:6px;margin:0 0 12px}
.sysNote{margin-top:2px;font-size:11px;color:var(--fg3);line-height:1.6}
</style>
</head>
<body>
<div class="shell">
  <!-- 左栏：会话列表 -->
  <aside class="col col-left">
    <div class="logo">Fairy 对话 <span style="font-size:10px;color:#66727f;font-weight:400">__BUILT__</span></div>
    <button class="newBtn" id="newBtn">＋ 新对话</button>
    <div class="convList" id="convList"></div>
  </aside>

  <!-- 中间：聊天 -->
  <main class="col col-chat">
    <div class="chatTop">
      <!-- ★★ 2026-10-18【用户要求】顶部这两个状态框挪到右侧「模型状态」卡片里，
           这里只留主题切换按钮。 -->
      <span class="spacer"></span>
      <button class="iconBtn" id="tTheme" title="切换深浅主题">◐</button>
    </div>
    <div class="msgs" id="msgs"><div class="empty" id="empty">和当前引擎说点什么吧（输入后回车发送）</div></div>
    <div class="attachBar" id="attachBar" style="display:none"><span id="attachInfo"></span><button class="iconBtn sm" id="attachClear" title="移除附件">✕</button></div>
    <div class="inputBar">
      <button class="iconBtn" id="btnImg" title="上传图片"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="17" height="17"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg></button>
      <button class="iconBtn" id="btnFile" title="上传文件"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="17" height="17"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/></svg></button>
      <button class="iconBtn" id="btnVoice" title="语音输入（录音→转文字）"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="17" height="17"><path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" x2="12" y1="19" y2="22"/></svg></button>
      <textarea id="inp" placeholder="输入消息，Enter 发送，Shift+Enter 换行"></textarea>
      <label id="ctxNewWrap" title="勾上：这句单独发，不带上文（自动判断误判时手动兜底）"
             style="display:flex;align-items:center;gap:4px;font-size:11px;color:#7b8a9a;white-space:nowrap;cursor:pointer;user-select:none">
        <input type="checkbox" id="ctxNew" style="cursor:pointer">独立
      </label>
      <button class="sendBtn" id="send">发送</button>
    </div>
    <input type="file" id="fImg" accept="image/*" hidden>
    <input type="file" id="fFile" accept=".txt,.md,.py,.json,.csv,.log,.js,.ts,.html,.css,.xml,.ini,.yaml,.yml,.sql,.sh,.bat" hidden>
  </main>

  <!-- 右栏：监控 -->
  <aside class="col col-right">
    <div class="rHead">监控</div>
    <div class="rightBody">
      <div class="card stateCard">
        <div class="ct">模型状态</div>
        <!-- ★★ 2026-10-18【用户要求】顶上那两个状态框挪到这里，并且要实时显示
             实际在跑的引擎/模型（每 2.5 秒刷新一次，数据来自 /api/info 的实测结果）。
             圆点颜色：绿=端口在线 / 红=离线 / 灰=游戏模式（引擎已释放）。 -->
        <div class="liveBox">
          <div class="liveRow"><span class="liveDot" id="pEngine" data-s="busy"></span><span class="liveTxt" id="tEngine">读取中…</span></div>
          <div class="liveRow"><span class="liveDot" id="pModel" data-s="busy"></span><span class="liveTxt liveModel" id="tModel">…</span></div>
        </div>
        <div class="badges" id="sBadges">
          <span class="badge" data-s="idle">空闲</span>
          <span class="badge" data-s="reading">读取</span>
          <span class="badge" data-s="generating">生成中</span>
          <span class="badge" data-s="queued">排队</span>
          <span class="badge" data-s="error">错误</span>
        </div>
        <div class="sLabel" id="sLabel">等待请求</div>
        <div class="prog"><i id="stateBar" style="width:0%"></i></div>
      </div>

      <div class="mGrid">
        <div class="mCard"><span class="mLab">推理速度</span><span class="mVal" id="mv-speed">–</span><span class="mSub" id="ms-speed"></span></div>
        <div class="mCard"><span class="mLab">GPU 负载</span><span class="mVal" id="mv-gpu">–</span><span class="mSub" id="ms-gpu"></span></div>
        <div class="mCard"><span class="mLab">显存</span><span class="mVal" id="mv-vram">–</span><span class="mSub" id="ms-vram"></span></div>
        <div class="mCard"><span class="mLab">GPU 温度</span><span class="mVal" id="mv-temp">–</span><span class="mSub" id="ms-temp"></span></div>
        <div class="mCard"><span class="mLab">功耗</span><span class="mVal" id="mv-power">–</span><span class="mSub" id="ms-power"></span></div>
        <div class="mCard"><span class="mLab">CPU</span><span class="mVal" id="mv-cpu">–</span><span class="mSub" id="ms-cpu"></span></div>
        <div class="mCard mWide"><span class="mLab">磁盘读取</span><span class="mVal" id="mv-disk">–</span><span class="mSub" id="ms-disk"></span></div>
      </div>

      <div class="card">
        <div class="ct">系统</div>
        <div class="sysRow"><span>系统内存</span><b id="sys-mem">–</b></div>
        <div class="prog sm"><i id="p-mem" style="width:0%"></i></div>
        <div class="sysRow"><span>系统 CPU</span><b id="sys-cpu">–</b></div>
        <div class="prog sm"><i id="p-cpu" style="width:0%"></i></div>
        <div class="sysRow"><span>磁盘占用</span><b id="sys-disk">–</b></div>
        <div class="prog sm"><i id="p-disk" style="width:0%"></i></div>
      </div>
      <div class="sysNote" id="sysNote"></div>
    </div>
  </aside>
</div>

<script>
let P = {engine:"strata", port:8080, model:""};
// ★★ 2026-10-18【用户要求】自动播报开关：
//   本地打开（127.0.0.1/localhost）-> 不自动念，避免自己看着屏幕还被吵
//   网络打开（局域网 IP 等）      -> 自动念
//   URL 参数可以覆盖：?speak=1 强制开 / ?speak=0 强制关
const AUTO_SPEAK = (function(){
  try{
    const q0 = new URLSearchParams(location.search);
    if(q0.has("speak")) return q0.get("speak") !== "0";
    const h = (location.hostname || "").toLowerCase();
    return !(h === "127.0.0.1" || h === "localhost" || h === "::1" || h === "");
  }catch(e){ return false; }
})();
const $ = id => document.getElementById(id);
(function(){
  // ★★ 2026-10-17【为什么之前右侧监控全空】初始化原本是一整段裸代码：
  //   只要其中任意一步抛错（哪怕只是会话列表渲染），后面的 probe()/loadSys()/
  //   setInterval 就【全都不执行】—— 表现就是状态灯永远"加载中"、监控全是「-」。
  //   现在每一步独立 try/catch，互不牵连；并且把脚本错误显示到右栏底部，便于排查。
  window.onerror = function(m, s, l){
    try{
      var e = document.getElementById("sysNote");
      if(e) e.textContent = "页面脚本错误：" + m + "（第 " + l + " 行）";
    }catch(_){}
    return false;
  };
  function safe(name, fn){
    try{ fn(); }
    catch(err){ try{ console.error("[" + name + "]", err); }catch(_){} }
  }
  let EPORT = {strata:8080, llm:8085, vision:8086, dsh:58841, ollama:11434};
  // ★ 2026-10-17【用户要求删除引擎下拉框】没有 UI 可切了，只认 URL 上的 ?engine=&port=。
  // ★★ 2026-10-18【用户要求"状态要实时"】注意：URL 参数只是**初始值**，
  //   真正的状态每 2.5 秒由 /api/info 的 preset 字段覆盖（见 paintTop）。
  //   以前这里无条件 setState("pEngine","ok") 硬点亮绿灯 —— 引擎挂了也是绿的。
  P.engine = P.engine || "strata";
  safe("params", function(){
    const q = new URLSearchParams(location.search);
    P.engine = q.get("engine") || P.engine || "strata";
    P.port = parseInt(q.get("port") || P.port, 10) || 8080;
    P.model = q.get("model") || "";
    // ★★ 2026-10-18【修「带参数打开却没反应」】URL 显式指定了 engine/port 时标记锁定，
    //   否则下面 paintTop 每 2.5 秒会用「Fairy 当前组合」覆盖 P.port：
    //   fairy.json 是游戏模式 -> p.port 为空 -> P.port=0 -> send() 里被拦下并提示
    //   "现在没有引擎在跑（游戏模式）"，看起来就是「点了发送没反应」。
    P.locked = q.has("engine") || q.has("port");
    $("tEngine").textContent = "正在读取状态…";
    $("tModel").textContent = "…";
  });
  safe("theme", function(){
    let t = "dark"; try{ t = localStorage.getItem("fairy.theme") || "dark"; }catch(e){}
    document.documentElement.dataset.theme = t;
  });
  safe("convs", function(){ loadConvs(); });
  // ★ 2026-10-18：不再需要 probe() 那个单独的探测 —— loadSys 每次都会带回
  //   后端实测的"当前组合 + 端口是否在线"，paintTop 直接用它点亮/点红。
  safe("sys", function(){ loadSys(); });
  safe("metrics", function(){ loadMetrics(); });
  setInterval(function(){
    try{ loadSys(); }catch(e){}      // 里面会刷新顶部两个状态灯（组合/模型 + 在线状态）
    try{ loadMetrics(); }catch(e){}
  }, 2500);
})();
function setState(id, s){ $(id).dataset.s = s; }
function toggleTheme(){
  const h = document.documentElement;
  h.dataset.theme = h.dataset.theme === "dark" ? "light" : "dark";
  try{ localStorage.setItem("fairy.theme", h.dataset.theme); }catch(e){}
}
document.getElementById("tTheme").addEventListener("click", toggleTheme);

/* ============ 会话管理 ============ */
let convs = [], cur = null;
function save(){ try{ localStorage.setItem("fairy.convs", JSON.stringify(convs)); }catch(e){} }
function curConv(){ return convs.find(c=>c.id===cur) || convs[0] || null; }
function loadConvs(){
  try{ convs = JSON.parse(localStorage.getItem("fairy.convs") || "[]"); }catch(e){ convs = []; }
  cur = convs[0] ? convs[0].id : null;
  renderList(); renderMsgs();
  if(!convs.length) newConv();
}
function newConv(){
  const id = Date.now();
  convs.unshift({id, title:"新对话", msgs:[]});
  cur = id; save(); renderList(); renderMsgs();
}
function switchConv(id){ cur = id; renderList(); renderMsgs(); }
function delConv(id){
  convs = convs.filter(c=>c.id!==id);
  if(cur===id) cur = convs[0] ? convs[0].id : null;
  save(); renderList(); renderMsgs();
  if(!convs.length) newConv();
}
function renderList(){
  $("convList").innerHTML = convs.map(c=>
    `<div class="conv ${c.id===cur?"on":""}" data-id="${c.id}">
       <span class="t">${esc(c.title||"新对话")}</span>
       <button class="del" title="删除">✕</button>
     </div>`).join("");
  $("convList").querySelectorAll(".conv").forEach(el=>{
    el.addEventListener("click", e=>{
      if(e.target.classList.contains("del")) delConv(+el.dataset.id);
      else switchConv(+el.dataset.id);
    });
  });
}
function renderMsgs(){
  const box = $("msgs"); box.innerHTML = "";
  const conv = curConv();
  if(!conv || !conv.msgs.length){ box.innerHTML = '<div class="empty" id="empty">和当前引擎说点什么吧（输入后回车发送）</div>'; return; }
  for(let i=0;i<conv.msgs.length;i++) addMsgEl(box, conv.msgs[i], i);
  box.scrollTop = box.scrollHeight;
}
function addMsgEl(box, m, idx){
  const role = m.role;
  const el = document.createElement("div"); el.className = "msg " + (role==="user"?"user":"ai");
  if(idx!=null) el.dataset.idx = idx;
  const av = document.createElement("div"); av.className="avatar"; av.textContent = role==="user"?"我":"F";
  const col = document.createElement("div"); col.className="body";
  const bub = document.createElement("div"); bub.className="bub"; bub.textContent = m.text || "（空回复）";
  col.appendChild(bub);
  if(m.img){ const im=document.createElement("img"); im.className="msgImg"; im.src=m.img; col.appendChild(im); }
  if(m.fname){ const ft=document.createElement("span"); ft.className="fileTag"; ft.textContent="📎 "+m.fname; col.appendChild(ft); }
  if(m.standalone){
    // ★ 2026-10-21：让用户一眼看出这句被当作"新对话"发了（没带上文）
    const tg=document.createElement("span"); tg.className="fileTag"; tg.textContent="🆕 独立提问（未带上文）";
    col.appendChild(tg);
  }
  if(role==="ai"){
    const ops = document.createElement("div"); ops.className="opRow";
    ops.innerHTML = '<button class="op" data-op="resend" title="重新生成这条回答">↻ 重发</button>'+
                    '<button class="op" data-op="play" title="语音播放">🔊 播放</button>'+
                    '<button class="op" data-op="compress" title="上下文压缩接续">↻ 压缩</button>'+
                    '<button class="op" data-op="copy" title="复制">⧉ 复制</button>'+
                    '<button class="op" data-op="share" title="分享给其他软件">📤 分享</button>';
    col.appendChild(ops);
  } else {
    // ★ 2026-10-21【用户要求：消息重发】用户消息也有操作条，只放一个「重发」
    const ops = document.createElement("div"); ops.className="opRow"; ops.style.justifyContent="flex-end";
    ops.innerHTML = '<button class="op" data-op="resend" title="把这条重新发一次（会丢掉它之后的回答）">↻ 重发</button>';
    col.appendChild(ops);
  }
  el.appendChild(av); el.appendChild(col);
  box.appendChild(el);
}
$("msgs").addEventListener("click", e=>{
  const op = e.target.closest(".op"); if(!op) return;
  const body = op.closest(".body"); if(!body) return;
  const bub = body.querySelector(".bub"); const text = bub ? bub.textContent : "";
  const act = op.dataset.op;
  const msgEl = op.closest(".msg");
  const idx = msgEl ? parseInt(msgEl.dataset.idx, 10) : NaN;
  if(act==="resend") resend(idx);
  else if(act==="play") playMsg(text);
  else if(act==="compress") compressCont();
  else if(act==="copy") copyText(text);
  else if(act==="share") shareText(text);
});
function esc(s){ const d=document.createElement("div"); d.textContent=s; return d.innerHTML; }

/* ============ 附件：图片 / 文件 ============ */
let attach = {img:null, fname:null, ftext:null, fsize:0};
function showAttach(){
  const bar = $("attachBar"), info = $("attachInfo");
  if(attach.img || attach.fname){
    bar.style.display = "flex";
    info.textContent = attach.img ? "已附加图片" : ("已附加文件: " + attach.fname + " (" + attach.fsize + " B)");
  } else bar.style.display = "none";
}
$("btnImg").addEventListener("click", ()=>{ $("fImg").click(); });
$("fImg").addEventListener("change", e=>{
  const f = e.target.files[0]; if(!f) return;
  const r = new FileReader();
  r.onload = ()=>{ attach.img = r.result; attach.fname = null; attach.ftext = null; attach.fsize = 0; showAttach(); };
  r.readAsDataURL(f); e.target.value = "";
});
$("btnFile").addEventListener("click", ()=>{ $("fFile").click(); });
$("fFile").addEventListener("change", e=>{
  const f = e.target.files[0]; if(!f) return;
  if(f.size > 200*1024){ $("sysNote").textContent = "文件过大（>200KB），只读文本小文件"; e.target.value=""; return; }
  const r = new FileReader();
  r.onload = ()=>{ attach.fname = f.name; attach.fsize = f.size; attach.ftext = r.result; attach.img = null; showAttach(); };
  r.readAsText(f, "utf-8"); e.target.value = "";
});
$("attachClear").addEventListener("click", ()=>{ attach = {img:null,fname:null,ftext:null,fsize:0}; showAttach(); });

/* ============ 语音输入：录音 → ASR ============ */
let ac, source, spNode, micStream, recFrames, recOn = false;
async function toggleRec(){
  const btn = $("btnVoice");
  if(recOn){
    stopRec(); return;
  }
  try{
    micStream = await navigator.mediaDevices.getUserMedia({audio:true});
    ac = new (window.AudioContext||window.webkitAudioContext)();
    source = ac.createMediaStreamSource(micStream);
    spNode = ac.createScriptProcessor(4096, 1, 1);
    recFrames = [];
    spNode.onaudioprocess = e=>{ recFrames.push(new Float32Array(e.inputBuffer.getChannelData(0))); };
    source.connect(spNode); spNode.connect(ac.destination);
    recOn = true; btn.classList.add("recOn");
    $("sysNote").textContent = "正在录音…再点一次结束并识别";
  }catch(e){ $("sysNote").textContent = "麦克风不可用"; }
}
async function stopRec(){
  const btn = $("btnVoice");
  spNode.disconnect(); source.disconnect(); micStream.getTracks().forEach(t=>t.stop()); ac.close();
  const len = recFrames.reduce((a,f)=>a+f.length, 0);
  const all = new Float32Array(len); let o=0;
  for(const f of recFrames){ all.set(f, o); o += f.length; }
  recOn = false; btn.classList.remove("recOn");
  if(len < 1600){ $("sysNote").textContent = "录音太短，请重试"; return; }
  const wav = encodeWav(all, ac.sampleRate);
  const b64 = b64FromBuf(wav);
  $("sysNote").textContent = "识别中…";
  try{
    const r = await fetch("/api/asr", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({audio:b64})});
    const j = await r.json();
    if(j.ok){
      $("inp").value += ($("inp").value ? " " : "") + j.text;
      $("inp").focus();
      $("sysNote").textContent = "已识别";
    } else $("sysNote").textContent = "语音识别失败";
  }catch(e){ $("sysNote").textContent = "识别请求失败"; }
}
$("btnVoice").addEventListener("click", toggleRec);
function encodeWav(samples, sr){
  const buf = new ArrayBuffer(44 + samples.length*2);
  const dv = new DataView(buf);
  const w = (o,s)=>{ for(let i=0;i<s.length;i++) dv.setUint8(o+i, s.charCodeAt(i)); };
  w(0,"RIFF"); dv.setUint32(4, 36+samples.length*2, true); w(8,"WAVE");
  w(12,"fmt "); dv.setUint32(16,16,true); dv.setUint16(20,1,true); dv.setUint16(22,1,true);
  dv.setUint32(24,sr,true); dv.setUint32(28,sr*2,true); dv.setUint16(32,2,true); dv.setUint16(34,16,true);
  w(36,"data"); dv.setUint32(40, samples.length*2, true);
  let off = 44;
  for(let i=0;i<samples.length;i++){
    const s2 = Math.max(-1, Math.min(1, samples[i]));
    dv.setInt16(off, s2<0 ? s2*0x8000 : s2*0x7FFF, true); off += 2;
  }
  return buf;
}
function b64FromBuf(buf){
  const bytes = new Uint8Array(buf); let bin = "";
  const chunk = 0x8000;
  for(let i=0;i<bytes.length;i+=chunk){
    bin += String.fromCharCode.apply(null, bytes.subarray(i, i+chunk));
  }
  return btoa(bin);
}

/* ============ 语音播放（Fairy 克隆音色） ============ */
async function playMsg(text){
  if(!text || !text.trim()) return;
  // ★★ 2026-10-19【用户要求「继续往下读，不要用一句，要读到界面结束」】
  //   原来是把整条回复一次性 POST /api/tts。问题：
  //     · IndexTTS 对超长文本会慢到超时（tts_get 默认 timeout=120s）
  //     · 一旦失败或截断，就只听到开头一句，后面全没了
  //   改成：按标点切成不超过 120 字的小段，**一段播完（onended）再合成下一段**，
  //   长回复也能从开头一路念到结尾，第一句出声也更快。
  const segs = splitForTTS(text, 120);
  if(!segs.length) return;
  stopSpeak();
  _speakAbort = false;
  for (let i = 0; i < segs.length; i++) {
    if (_speakAbort) break;
    $("sysNote").textContent = "语音播报 " + (i+1) + "/" + segs.length + "…";
    await playOne(segs[i]);
  }
  if (!_speakAbort) $("sysNote").textContent = "";
}

let _speakAbort = false;
let _curAudio = null;

function stopSpeak(){
  _speakAbort = true;
  if (_curAudio) { try { _curAudio.pause(); _curAudio.src = ""; } catch(e){} _curAudio = null; }
}

function splitForTTS(text, maxLen){
  const t = String(text).replace(/\r/g, "").trim();
  if (!t) return [];
  const parts = t.split(/(?<=[。！？；!?;\n])/);
  const out = [];
  let cur = "";
  for (const p of parts){
    if (!p) continue;
    if (cur && (cur.length + p.length) > maxLen){
      out.push(cur); cur = p;
    } else {
      cur += p;
    }
    while (cur.length > maxLen * 1.5){
      out.push(cur.slice(0, maxLen));
      cur = cur.slice(maxLen);
    }
  }
  if (cur.trim()) out.push(cur);
  return out.filter(s => s.trim().length > 0);
}

function playOne(seg){
  return new Promise(async (resolve) => {
    let done = false;
    const finish = () => { if(!done){ done = true; resolve(); } };
    try{
      const r = await fetch("/api/tts", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({text:seg})});
      const j = await r.json();
      if(!j.ok){ $("sysNote").textContent = "语音合成失败: " + (j.error||"IndexTTS 不可用"); finish(); return; }
      const bin = atob(j.audio);
      const arr = new Uint8Array(bin.length);
      for(let i=0;i<bin.length;i++) arr[i] = bin.charCodeAt(i);
      const url = URL.createObjectURL(new Blob([arr], {type: j.mime || "audio/wav"}));
      const au = new Audio(url);
      au.volume = 1.0;
      _curAudio = au;
      au.onended = () => { try{ URL.revokeObjectURL(url); }catch(e){} finish(); };
      au.onerror = () => { try{ URL.revokeObjectURL(url); }catch(e){} finish(); };
      au.play().catch(() => finish());
    }catch(e){
      $("sysNote").textContent = "语音播放失败";
      finish();
    }
  });
}

/* ============ 复制 / 分享 ============ */
async function copyText(text){
  try{ await navigator.clipboard.writeText(text); $("sysNote").textContent = "已复制到剪贴板"; }
  catch(e){
    const t = document.createElement("textarea"); t.value = text; document.body.appendChild(t);
    t.select(); document.execCommand("copy"); t.remove(); $("sysNote").textContent = "已复制到剪贴板";
  }
}
function shareText(text){ copyText(text); } // 分享给其他软件 = 复制，可粘贴到任意应用

/* ============ 上下文压缩接续 ============ */
async function compressCont(){
  const conv = curConv(); if(!conv || !conv.msgs.length){ return; }
  const msgs = conv.msgs.map(m=>({role:m.role==="user"?"user":"assistant", content:m.text||""}));
  $("sysNote").textContent = "压缩上下文中…";
  try{
    const r = await fetch("/api/compress", {method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({engine:P.engine, port:P.port, model:P.model, messages:msgs})});
    const j = await r.json();
    if(j.ok && j.summary){
      conv.msgs = [{role:"user", text:"（上下文已压缩接续）上轮要点：\n" + j.summary}];
      save(); renderList(); renderMsgs();
      $("sysNote").textContent = "上下文已压缩，可继续对话";
    } else $("sysNote").textContent = "压缩失败: " + (j.error||"引擎无响应");
  }catch(e){ $("sysNote").textContent = "压缩请求失败"; }
}

/* ============ 聊天发送（多会话上下文 + 附件 + 自动语音播报） ============ */
let busy = false;
/* ★ 2026-10-21【上下文自动判断】按用户选择：**没有关联上下文时，一句话就是一句新对话**。
   规则（简单、可预期，不猜语义）：
     · 出现承接信号（它/这个/刚才/继续/然后/那…）  -> 接上文
     · 没有任何承接信号                            -> 独立发送（不带上文）
   想手动覆盖：勾输入框右边的「独立」可强制独立；
   反过来想强制接上文，句子开头带个承接词即可（如「那…」「这个…」）。 */
const _CTX_REF = /(它|他|她|它们|这个|那个|这些|那些|上面|上述|刚才|刚刚|之前|前面|上次|继续|接着|然后|还有|再说|另外|同样|这样|那样|这么|那么|呢\s*[？?]?$|吗\s*[？?]?$|改|换成|替换|优化|调整|详细|具体|举例|例子|展开|对比|去掉|加上|补充|删掉|试试)/;
const _CTX_LEAD = /^(那|所以|但是|不过|而且|并且|然后|还有|继续|接着|再|也|要不再|换成|改成)/;
function needsContext(t){
  t = (t||"").trim();
  if(!t) return false;
  if(_CTX_LEAD.test(t)) return true;   // 开头就是承接词
  if(_CTX_REF.test(t)) return true;    // 句中有指代/承接
  return false;                        // 没有承接信号 = 新话题，独立发送
}

/* 从最近一条"独立"的用户消息开始取上下文；没有独立消息就从会话开头取。 */
function buildMessages(conv){
  let start = 0;
  for(let i=conv.msgs.length-1;i>=0;i--){
    const m = conv.msgs[i];
    if(m.role==="user" && m.standalone){ start = i; break; }
  }
  return conv.msgs.slice(start).map(m=>{
    if(m.img && m.role==="user"){
      return {role:"user", content:[{type:"text", text:m.text||""},{type:"image_url", image_url:{url:m.img}}]};
    }
    return {role:m.role==="user"?"user":"assistant", content:m.text};
  });
}

async function runFrom(conv, userIdx){
  conv.msgs = conv.msgs.slice(0, userIdx+1);
  const messages = buildMessages(conv);
  conv.msgs.push({role:"ai", text:"思考中…"});
  busy = true; $("send").disabled = true;
  save(); renderList(); renderMsgs();
  try{
    const r = await fetch("/api/chat", {method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({engine:P.engine, port:P.port, model:P.model, messages})});
    const j = await r.json();
    const reply = j.ok ? (j.reply||"（空回复）") : ("出错："+(j.error||r.status));
    conv.msgs[conv.msgs.length-1] = {role:"ai", text:reply};
    setState("pModel", j.ok?"ok":"err");
    if(j.ok && reply && reply!=="（空回复）" && AUTO_SPEAK) playMsg(reply);
  }catch(e){
    conv.msgs[conv.msgs.length-1] = {role:"ai", text:"请求失败："+e};
    setState("pModel","err");
  }
  busy = false; $("send").disabled = false;
  save(); renderList(); renderMsgs();
}

function resend(idx){
  if(!Number.isFinite(idx)) return;
  const conv = curConv(); if(!conv) return;
  if(busy){ $("sysNote").textContent = "上一条还在生成，等它结束再重发"; return; }
  if(!P.port){ $("sysNote").textContent = "现在没有引擎在跑（游戏模式）——先在球里切一个组合"; return; }
  let i = idx;
  // 点的是 AI 消息时，回溯到它对应的那条用户消息
  while(i>=0 && conv.msgs[i].role!=="user") i--;
  if(i<0) return;
  $("sysNote").textContent = "正在重发第 " + (i+1) + " 条…";
  runFrom(conv, i);
}

async function send(){
  // ★★ 2026-10-18【修「回车没反应」】原来这两个判断是 `return` 静默返回：
  //     `const conv = curConv(); if(!conv || busy) return;`
  //   没有会话（localStorage 被清）或上一条还在等回复时，
  //   按回车**一点反馈都没有**，看起来就是"输入不进去/回车没用"。
  //   现在每种情况都往 sysNote 写一句，让人知道到底卡在哪。
  // ★★ 2026-10-18【用户要求】没有选中任何对话时，直接发消息就**自动建一个新对话**，
  //   而不是像之前那样弹一句"还没有会话，去点新对话"再 return。
  //   点「+ 新对话」按钮的行为不变（见 newBtn 的 click 绑定）。
  //   ★ 注意必须用 let 并在新建后重新取 —— 写成 const 的话新建完 conv 还是 null，
  //     下面 conv.msgs 会直接崩。
  let conv = curConv();
  if(!conv){
    newConv();
    conv = curConv();
    if(!conv){ $("sysNote").textContent = "新建对话失败，点左上角「+ 新对话」手动建一个"; return; }
  }
  if(busy){
    $("sysNote").textContent = "上一条还在等回复，稍等一下再发";
    return;
  }
  if(!P.port){
    $("sysNote").textContent = "现在没有引擎在跑（游戏模式）——先在球里切一个组合，再发消息";
    return;
  }
  const inp = $("inp"); const txt = inp.value.trim();
  let curText = txt;
  if(attach.ftext) curText = (curText ? curText + "\n\n" : "") + "【文件 " + attach.fname + " 内容】\n" + attach.ftext;
  if(!curText.trim() && !attach.img) return;
  inp.value = ""; busy = true; $("send").disabled = true;
  const _forceNew = !!($("ctxNew") && $("ctxNew").checked);
  const _standalone = _forceNew || !needsContext(curText);
  const userMsg = {role:"user", text:curText.trim(), standalone:_standalone};
  if(attach.img) userMsg.img = attach.img;
  if(attach.fname) userMsg.fname = attach.fname;
  if(!userMsg.text && attach.img) userMsg.text = "（图片）";
  conv.msgs.push(userMsg);
  if(conv.title==="新对话") conv.title = (curText.trim() || "图片").slice(0,14);
  if($("ctxNew")) $("ctxNew").checked = false;
  if(_standalone) $("sysNote").textContent = "这句按【新对话】发送（不带上文）";
  attach = {img:null,fname:null,ftext:null,fsize:0}; showAttach();
  await runFrom(conv, conv.msgs.length-1);
}
$("send").addEventListener("click", send);
$("inp").addEventListener("keydown", e=>{ if(e.key==="Enter"&&!e.shiftKey){ e.preventDefault(); send(); } });
$("newBtn").addEventListener("click", newConv);

/* ============ 引擎探测 ============ */
function probe(){
  fetch("/api/ping", {method:"POST", headers:{"Content-Type":"application/json"},
    body:JSON.stringify({engine:P.engine, port:P.port})})
    .then(r=>r.json()).then(j=>{
      setState("pEngine", j.ok?"ok":"err");
      setState("pModel", j.ok?(P.model?"ok":"busy"):"err");
    }).catch(()=>{ setState("pEngine","err"); setState("pModel","err"); });
}

/* ============ 右栏监控 ============ */
function loadSys(){
  fetch("/api/info", {method:"POST", headers:{"Content-Type":"application/json"},
    body:JSON.stringify({engine:P.engine, port:P.port})})
    .then(r=>r.json()).then(j=>{ if(j.ok){ paintTop(j.preset); paintSys(j); } }).catch(()=>{
      // 拿不到状态 = 后端自己都不通，两个灯都变红
      setState("pEngine","err"); setState("pModel","err");
      $("tEngine").textContent = "状态读取失败";
    });
}
// ★★ 2026-10-18【用户要求"状态要实时、切了组合就显示那个组合"】
//   数据源是后端读 fairy.json 的 ai 段（球里一切组合就变），每 2.5 秒刷一次：
//     · 组合名 + 引擎 + 端口   （游戏模式显示"引擎已释放"，端口空就不瞎报端口）
//     · 模型名                 （组合里的 model 字段）
//     · 灯色：后端实测该端口通不通 —— 通=绿，不通=红，游戏模式=灰(busy)
function paintTop(p){
  p = p || {};
  const be = p.backend && p.backend !== "off" ? p.backend : "";
  const isGame = (p.preset === "game") || (!be && !p.port);
  const presetName = p.preset === "game" ? "游戏模式" : (p.preset || "");
  let label;
  if (isGame) {
    label = "游戏模式（引擎已释放）";
  } else if (!be) {
    label = "未选组合";
  } else {
    label = (presetName && presetName !== "game" ? presetName + " · " : "") + be + (p.port ? " :" + p.port : "");
  }
  // ★★ 2026-10-21【用户要求：连接状态要跟着模型走】
  //   模型真加载好了才显示「已连接」；还在加载显示「加载中」；没启动显示「未连接」。
  //   判据来自后端 _model_ready()（查 /v1/models 的 status，而不是只看端口）。
  const _ld = !!p.loading;
  const _conn = isGame ? "" : (_ld ? " · 加载中" : (p.on ? " · 已连接" : " · 未连接"));
  $("tEngine").textContent = label + _conn + ((_ld && p.hint) ? " — " + p.hint : "");
  if (isGame) {
    setState("pEngine", "busy");
  } else if (_ld) {
    setState("pEngine", "busy");          // 加载中：黄灯
  } else {
    setState("pEngine", p.on ? "ok" : "err");
  }
  const mdl = p.model || "";
  if (mdl) {
    $("tModel").textContent = mdl;
    setState("pModel", (p.on || _ld) ? (p.on ? "ok" : "busy") : "err");
    P.model = mdl;
  } else {
    $("tModel").textContent = isGame ? "无模型" : "模型未加载";
    setState("pModel", isGame ? "busy" : (p.on ? "busy" : "err"));
  }
  P.engine = be || P.engine;
  // ★★ 2026-10-18【修「带参数打开却没反应」】
  //   这里原来无条件 `P.port = parseInt(p.port||0,10) || 0`：
  //   只要 Fairy 当前是游戏模式（fairy.json 的 ai.port 为空），
  //   页面打开 2.5 秒后 P.port 就被清成 0，然后 send() 里的
  //   `if(!P.port)` 直接把消息拦下 —— 表现就是「点发送没反应」。
  //   现在：URL 显式锁定了目标（?engine=&port=）就不被清空；
  //   没锁定（普通打开）仍跟随 Fairy 的真实组合，保持「状态要实时」。
  if (p.port) {
    P.port = parseInt(p.port, 10) || 0;
  } else if (!P.locked) {
    P.port = 0;
  }
  // ★★ 2026-10-21【页面锁在旧组合上】URL 带 ?engine=&port= 打开后，球里又切了组合，
  //   页面还死盯旧端口 —— 表现就是「引擎没在运行」或「TimeoutError」。
  //   现在：当前组合已就绪且端口不同 → 自动跟随，并在底栏说一声。
  if (P.locked && p.port && String(p.port) !== String(P.port) && (p.state === "ready" || p.on)) {
    P.engine = p.backend || P.engine;
    P.port = parseInt(p.port, 10) || P.port;
    if (p.model) P.model = p.model;
    P.locked = false;
    $("sysNote").textContent = "已自动跟随当前组合：" + (presetName || "") + " → 端口 " + P.port;
  }
}
function paintSys(j){
  const s = j.sys || {};
  if(s.mem_pct!=null){ $("sys-mem").textContent = Math.round(s.mem_pct)+"% ("+(s.mem_used||"-")+" / "+(s.mem_total||"-")+")"; $("p-mem").style.width = s.mem_pct+"%"; }
  if(s.cpu!=null){ $("sys-cpu").textContent = Math.round(s.cpu)+"%"; $("p-cpu").style.width = s.cpu+"%"; }
  if(s.disk_pct!=null){ $("sys-disk").textContent = Math.round(s.disk_pct)+"%"; $("p-disk").style.width = s.disk_pct+"%"; }
  // ★ 2026-10-17：说明只留一句、且写在格子里（放底部会把一行撑得很长，很难看）。
  $("sysNote").textContent = (s.cpu_name||"--") + " · " + (s.gpu_name||"--");
}
function loadMetrics(){
  if(P.engine!=="strata"){ renderState(null); return; }
  fetch("/api/metrics", {method:"POST", headers:{"Content-Type":"application/json"},
    body:JSON.stringify({engine:P.engine, port:P.port})})
    .then(r=>r.json()).then(j=>{ if(j.ok) renderMetrics(j.metrics); else renderState(null); }).catch(()=>renderState(null));
}
function renderState(state){
  const map = {"idle":"空闲","reading":"读取中…","generating":"生成中…","queued":"排队中…",
               "error":"错误",
               // ★ 2026-10-17：Strata 的 live.state 还会出现 unloaded/loading，
               //   原来没映射 -> 徽章全灰、标签只剩"等待请求"，看不出模型到底加载了没有。
               "unloaded":"模型未加载（发一句话会自动加载）","loading":"加载中…"};
  for(const b of document.querySelectorAll("#sBadges .badge")) b.classList.toggle("on", b.dataset.s===(state||"idle") || (state==="error"&&b.dataset.s==="error"));
  $("sLabel").textContent = (state && map[state]) ? map[state] : (P.engine==="strata" ? "等待请求" : "非 strata 引擎，无推理指标");
  $("stateBar").style.width = (state==="generating"||state==="reading"||state==="queued") ? "100%" : "0%";
  if(state==="idle") $("stateBar").style.width = "0%";
}
function setMetric(key, value, unit, sub){
  const el = $("mv-"+key); if(!el) return;
  el.innerHTML = (value==null||value==="") ? "–" : (value + (unit?`<small>${unit}</small>`:""));
  const subEl = $("ms-"+key); if(subEl && sub!=null) subEl.textContent = sub;
}
function renderMetrics(m){
  const lv = (m && (m.live||m)) || {};
  const hw = (m && m.hardware) || {};
  const hs = (m && m.hardware_static) || {};
  const hist = (m && m.history) || {};
  // ★ 2026-10-17：URL 里没带 model 时，用后端报上来的真实模型名补齐，
  //   不然顶部永远显示"默认模型"+黄点，看不出到底在跑哪个模型。
  try{
    if(!P.model && m && m.engine && m.engine.model){
      P.model = m.engine.model;
      $("tModel").textContent = P.model;
      setState("pModel", "ok");
    }
  }catch(e){}
  renderState(lv.state || "idle");
  // ★★ 2026-10-17【监控格子一直显示 – 的原因】原来这 7 格全从 live 里取，
  //   但 Strata 的 /metrics 把硬件指标放在 hardware / history 里，live 只有推理指标。
  //   而且 hardware_static.gpu_note 明确写着：AMD 显卡在 Windows 上读不到
  //   GPU 负载/显存/温度/功耗（Linux 才有 amdgpu 数据源）。
  //   所以：能读的（CPU、磁盘）从 hardware 取；读不到的 GPU 四格显示「—」+一句说明，
  //   而不是留个空白让用户以为是坏了。
  setMetric("speed", lv.tok_s!=null?Number(lv.tok_s).toFixed(1):null, "t/s",
            lv.prefill_tok_s_mean!=null?("预填充 "+(lv.prefill_tok_s_mean|0)+" t/s"):"");
  function lastOf(key){
    const a = hist[key];
    if(!a || !a.length) return null;
    for(let i=a.length-1;i>=0;i--){ if(a[i]!=null) return a[i]; }
    return null;
  }
  // ★★ 2026-10-17：Strata 在 AMD+Windows 上读不到 GPU 数据，所以 Fairy 自己用
  //   Windows 性能计数器补了一份（后端 /api/metrics 的 fairy_gpu 字段）。
  //   四格逐个判断：有数就显示数，没有就「—」+ 小字说明，不再整体一刀切。
  const fg = (m && m.fairy_gpu) || {};
  const _gu = (fg.util!=null) ? fg.util : lastOf("gpu_util");
  const _vm = (fg.mem_gb!=null) ? fg.mem_gb
             : (lastOf("gpu_mem_used")!=null ? lastOf("gpu_mem_used")/1e9 : null);
  // ★ 2026-10-18【用户要求】温度/功耗先从 Fairy 自己采的 fairy_gpu 取
  //   （来自 HWiNFO 共享内存），没有再退回引擎自己的 metrics。
  const _tp = (fg.temp!=null) ? fg.temp : lastOf("gpu_temp");
  const _pw = (fg.power!=null) ? fg.power : lastOf("gpu_power");
  setMetric("gpu", _gu!=null?Math.round(_gu):null, "%");
  setMetric("vram", _vm!=null?Number(_vm).toFixed(1):null, "GB");
  setMetric("temp", _tp!=null?Math.round(_tp):null, "°C");
  setMetric("power", _pw!=null?Math.round(_pw):null, "W");
  function note(k, t){ const e = $("ms-"+k); if(e) e.textContent = t; }
  if(_gu==null) note("gpu", "读不到");
  else if(fg.util!=null) note("gpu", "Fairy 实测");
  if(_vm==null) note("vram", "读不到");
  else if(fg.mem_gb!=null) note("vram", "Fairy 实测");
  if(_tp==null) note("temp", "打开 MSI Afterburner 就有");
  else if(fg.temp!=null) note("temp", "Fairy 实测 · Afterburner");
  if(_pw==null) note("power", "打开 MSI Afterburner 就有");
  else if(fg.power!=null) note("power", "Fairy 实测 · Afterburner");
  setMetric("cpu", hw.cpu!=null?Math.round(hw.cpu):null, "%");
  setMetric("disk", hw.disk_read_mb!=null?Number(hw.disk_read_mb).toFixed(2):null, "MB/s");
}
</script>
</body>
</html>
"""


def log(msg):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def port_open(port, timeout=0.5):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        try:
            s.close()
        except Exception:
            pass


def _forward(engine, port, payload):
    """转发对话请求。返回 (ok, 文本或错误)。"""
    # ★ 2026-10-21 修：必须用调用方给的 port。
    #   原来只认 _BASE 里写死的端口，组合一旦改用别的端口（比如 27B 走 8081），
    #   请求就会打到错的地方。port 非法时退回 _BASE。
    try:
        _port_i = int(port) if port else 0
    except (TypeError, ValueError):
        _port_i = 0
    base = ("http://127.0.0.1:%d" % _port_i) if _port_i else _BASE.get(engine)
    if not base:
        return False, "未知引擎: %s" % engine
    if engine == "ollama":
        _mdl = (payload.get("model") or "").strip()
        if _mdl and _mdl not in _ollama_tags():
            _mdl = ""
        if not _mdl:
            _tags = _ollama_tags()
            _mdl = _tags[0] if _tags else ""
            if not _mdl:
                return False, "Ollama 没有任何已安装模型（先 ollama pull）"
        body = json.dumps({"model": _mdl, "messages": payload["messages"],
                           "stream": False, "keep_alive": "2h",
                           "options": {"temperature": 0.6}},
                          ensure_ascii=False).encode("utf-8")
        try:
            req = urllib.request.Request(base + "/api/chat", data=body,
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=600) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
            return True, (j.get("message") or {}).get("content") or ""
        except Exception as e:
            return False, "%s: %s" % (type(e).__name__, str(e)[:200])
    if engine == "dsh":
        # 网络 AI：走 DSH（DeepSeek Harness）push 接口（单文本问答）
        last = None
        for _m in reversed(payload.get("messages") or []):
            _c = _m.get("content")
            if isinstance(_c, str) and _m.get("role") == "user":
                last = _c
                break
        if not last:
            return False, "无文本消息可发给 DSH"
        try:
            req = urllib.request.Request(
                base + "/api/fairy/push",
                data=json.dumps({"text": last, "source": "fairy-chat"},
                                ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=120) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
            if j.get("ok"):
                return True, str(j.get("text") or "").strip()
            return False, str(j.get("error") or "DSH 无响应")
        except Exception as e:
            return False, "%s: %s" % (type(e).__name__, str(e)[:200])
    # llama.cpp 系：OpenAI 兼容（Strata/Ollama-openai 转发原样 payload，含图片 image_url）
    # ★★ 2026-10-21【图发给纯文本模型会 500】用户把截图发给了 Qwen3.8-27B（没有 mmproj），
    #   llama-server 直接回 500 "image input is not supported"，页面只看到 HTTPError 500。
    #   这里提前拦下来，给一句能照做的提示。
    def _has_image(_msgs):
        for _m in (_msgs or []):
            _c = _m.get("content")
            if isinstance(_c, list):
                for _p in _c:
                    if isinstance(_p, dict) and _p.get("type") in ("image_url", "image"):
                        return True
        return False

    if engine != "vl" and _has_image(payload.get("messages")):
        return False, ("当前组合（%s）是纯文本模型，不能看图。\n"
                       "把组合切成「Qwen3-VL-8B」再发图片。" % engine)
    url = base + "/v1/chat/completions"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    # ★★ 2026-10-21【503 不是坏了，是模型还在加载】
    #   llama-server 在 load 期间对所有请求回 503：VL 引擎冷启实测 33 秒，
    #   用户点完组合、页面一开就发消息，十有八九撞在加载窗口里，
    #   以前直接把 "HTTPError: HTTP Error 503" 甩到对话里，看着像程序坏了。
    #   现在：503/502/URLError 自动重试最多 90 秒，超时才给一句人话。
    _deadline = _time.time() + 90
    while True:
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                j = json.loads(r.read().decode("utf-8", "replace"))
            msg = (j.get("choices") or [{}])[0].get("message") or {}
            return True, (msg.get("content") or "").strip()
        except urllib.error.HTTPError as _e:
            _body = ""
            try:
                _body = _e.read().decode("utf-8", "replace")[:600]
            except Exception:
                pass
            if "image input is not supported" in _body:
                return False, ("这个组合的模型没有视觉能力（没加载 mmproj），看不了图。\n"
                               "切到「Qwen3-VL-8B」再发。")
            if _e.code in (503, 502) and _time.time() < _deadline:
                _time.sleep(2.0)
                continue
            if _e.code == 503:
                return False, ("模型还在加载（HTTP 503），已等 90 秒仍未就绪。\n"
                               "稍等片刻再发一次，或等球播报「已就绪」。")
            return False, "%s: %s" % (type(_e).__name__, str(_e)[:200])
        except urllib.error.URLError as _e:
            _reason = getattr(_e, "reason", _e)
            _rs = str(_reason)
            # 连接被拒绝 = 进程根本没起，重试 90 秒没有意义 → 立刻回人话
            if isinstance(_reason, ConnectionRefusedError) or "refused" in _rs.lower() or "10061" in _rs:
                return False, ("引擎没在运行（连接被拒绝）。\n"
                               "先在球里切一个带模型的组合，再发消息。")
            if _time.time() < _deadline:
                _time.sleep(2.0)
                continue
            return False, "连不上引擎：可能还在启动，稍后重试"
        except TimeoutError:
            # ★ 2026-10-21：连接通了但 600 秒没返回（模型加载中/卡住/上下文过长）
            return False, ("生成超时：引擎 600 秒没有返回。\n"
                           "可能是模型正在加载或卡住；切到「Qwen3.8-27B·IQ3_XXS」会快很多。")
        except Exception as _e:
            return False, "%s: %s" % (type(_e).__name__, str(_e)[:200])


_OL_TAGS_CACHE = (0.0, [])


def _ollama_tags():
    global _OL_TAGS_CACHE
    if _time.time() - _OL_TAGS_CACHE[0] < 5 and _OL_TAGS_CACHE[1]:
        return _OL_TAGS_CACHE[1]
    out = []
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:11434/api/tags", timeout=3) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        out = [m.get("name") for m in j.get("models", [])]
    except Exception:
        pass
    _OL_TAGS_CACHE = (_time.time(), out)
    return out


def _gb(n):
    return "%.1fGB" % (n / 1024 ** 3)


def _gpu_name():
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_VideoController).Name"],
            capture_output=True, text=True, timeout=8,
            creationflags=CREATE_NO_WINDOW).stdout.strip()
        lines = [x.strip() for x in out.splitlines() if x.strip()]
        return lines[0] if lines else None
    except Exception:
        return None


def _gpu_temp():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=6,
            creationflags=CREATE_NO_WINDOW).stdout.strip()
        first = (out.splitlines() or [""])[0].strip()
        return float(first) if first.isdigit() else None
    except Exception:
        return None


def _strata_loading():
    """★ 2026-10-18【用户要求"能实时显示加载过程"】

    Strata 冷启动要 1~8 分钟（读 87 GB 模型 + 把专家搬进 40 GB 内存缓存 +
    填 GPU 专家缓存），这段时间 8080 还没监听、页面只能显示"离线"，
    用户完全不知道它在干嘛、还要多久 —— 看着就像卡死。

    这里直接读 Strata 自己的启动日志尾部，把当前阶段翻译成人话：
        reading the model's weights      -> 读取模型权重
        loading the most-used experts    -> 加载专家到内存
        filling the GPU's expert cache   -> 填充显存缓存
        still starting (N s)             -> 加载中 N 秒
        almost ready                     -> 快好了
        ready: http://...                -> 已就绪
    返回 {"loading": bool, "hint": str, "secs": int|None}
    """
    out = {"loading": False, "hint": "", "secs": None}
    try:
        p = r"E:\Strata\strata-unsloth-ud-iq4_xs.log"
        if not os.path.exists(p):
            return out
        sz = os.path.getsize(p)
        with open(p, "rb") as f:
            f.seek(max(0, sz - 8192))          # 只看最后 8 KB，别整文件读
            tail = f.read().decode("utf-8", "replace")
        lines = [x.strip() for x in tail.splitlines() if x.strip()]
        if not lines:
            return out
        last = " || ".join(lines[-6:])
        import re as _re
        m = _re.findall(r"still starting \((\d+) s\)", last)
        if m:
            out["loading"] = True
            out["secs"] = int(m[-1])
            out["hint"] = "加载中，已等 %d 秒" % out["secs"]
        elif "filling the GPU's expert cache" in last:
            out["loading"] = True
            out["hint"] = "填充显存专家缓存"
        elif "almost ready" in last:
            out["loading"] = True
            out["hint"] = "快好了"
        elif "loading the most-used experts" in last:
            out["loading"] = True
            out["hint"] = "加载专家到内存"
        elif "reading the model's weights" in last:
            out["loading"] = True
            out["hint"] = "读取模型权重"
        elif "loading the model" in last:
            out["loading"] = True
            out["hint"] = "开始加载模型"
        elif "ready:" in last:
            out["loading"] = False
            out["hint"] = "已就绪"
    except Exception:
        pass
    return out


def _model_ready(backend, port):
    """★ 2026-10-21【连接状态 = 模型真的加载好了，不是端口通了】
    返回 ("ready"|"loading"|"down", model_id)。

    为什么必须区分：
      · llama-server 加载期间**端口已经开着**，但所有请求回 503 —— 以前只看端口，
        于是页面亮绿灯、用户一发消息就撞 503（10-21 12:59 那次就是这个）。
      · Strata 更麻烦：加载中 /v1/models 也可能 200，得看 status.value 是不是 "loaded"。
    """
    # ★ 2026-10-21 修：必须优先用传进来的 port。
    #   原来直接取 _BASE[backend]（写死 8086/8080…），导致「实际端口没开」也判定在线。
    try:
        _port_i = int(port) if port else 0
    except (TypeError, ValueError):
        _port_i = 0          # 传了非数字端口（前端异常）时按"没有端口"处理，别让 /api/ping 500
    if _port_i:
        base = "http://127.0.0.1:%d" % _port_i
    else:
        base = _BASE.get(backend, "")
    if not base:
        return "down", ""
    if backend == "ollama":
        try:
            with urllib.request.urlopen(base + "/api/tags", timeout=2) as _r:
                _j = json.loads(_r.read().decode("utf-8", "replace"))
            return ("ready" if (_j.get("models") or []) else "down"), ""
        except Exception:
            return "down", ""
    try:
        with urllib.request.urlopen(base + "/v1/models", timeout=2) as _r:
            _j = json.loads(_r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as _e:
        return ("loading" if _e.code in (503, 502) else "down"), ""
    except Exception:
        return "down", ""
    _data = _j.get("data") or []
    if not _data:
        return "loading", ""
    _st = (((_data[0] or {}).get("status") or {}).get("value") or "")
    if _st in ("", "loaded"):
        return "ready", str(_data[0].get("id") or "")
    if _st in ("loading", "unloaded", "unloading"):
        return "loading", ""
    return "down", ""


def _current_preset():
    """读 fairy.json 的 ai 段：当前组合 / 引擎 / 模型 / 端口 + 该端口是否在线。

    ★ 2026-10-18【用户要求"状态要实时、切了组合就显示那个组合"】
      以前顶部那行读的是 URL 参数（?engine=&port=&model=），页面一打开就固定了：
      球里切到别的组合、或者引擎挂了，页面还照样绿着显示旧值。
      现在由这里汇报"实际在用的组合"，前端每 2.5 秒拉一次刷新。
    ★ 2026-10-18 追加：再带上 Strata 的**实时加载阶段**（_strata_loading），
      这样冷启动那几分钟页面能显示"加载中，已等 N 秒"，而不是干巴巴一个"离线"。
    """
    out = {"preset": "", "backend": "", "model": "", "port": "", "on": False,
           "loading": False, "hint": "", "secs": None}
    try:
        with open(r"D:\FAIRY\fairy.json", encoding="utf-8-sig") as _f:
            ai = (json.load(_f) or {}).get("ai") or {}
        out["preset"] = str(ai.get("preset") or "")
        out["backend"] = str(ai.get("backend") or "")
        out["model"] = str(ai.get("model") or "")
        out["port"] = str(ai.get("port") or "")
        try:
            _p = int(out["port"])
        except Exception:
            _p = 0
        out["on"] = bool(_p) and port_open(_p, 0.5)
        # ★ 2026-10-21【连接状态判据改了】端口通 ≠ 能用：
        #   llama-server 加载中端口就开了但回 503；Strata 加载中 /v1/models 也 200。
        #   现在以「模型真的 loaded」为准，前端据此显示 已连接 / 加载中 / 未连接。
        _pk, _pid = _model_ready(out["backend"], _p)
        out["on"] = (_pk == "ready")
        out["state"] = _pk
        if _pk == "ready":
            out["hint"] = "已就绪"
            out["loaded_model"] = _pid
            out["loading"] = False
        elif _pk == "loading":
            out["loading"] = True
            out["hint"] = "模型加载中…"
            _ld = _strata_loading()
            if _ld.get("secs"):
                out["secs"] = _ld.get("secs")
        else:
            out["loading"] = False
            _ld = _strata_loading()
            if _ld.get("loading"):
                out["loading"] = True
                out["hint"] = _ld.get("hint") or "正在启动…"
                out["secs"] = _ld.get("secs")
            else:
                out["hint"] = "引擎未启动"
    except Exception:
        pass
    return out


def build_info(engine, port):
    info = {"ok": True,
            "engine": {"name": engine, "port": port,
                       "api": _BASE.get(engine, "")},
            # ★ 当前真实组合（球里切组合会改 fairy.json，这里跟着变）
            "preset": _current_preset(),
            "sys": {"host": platform.node(),
                    "os": platform.platform().split("-")[0]}}
    try:
        import psutil
        s = info["sys"]
        s["cpu"] = psutil.cpu_percent(interval=0.2)
        s["cpu_cores"] = psutil.cpu_count(logical=True)
        s["cpu_name"] = platform.processor() or ("%d 核" % s["cpu_cores"])
        vm = psutil.virtual_memory()
        s["mem_pct"] = vm.percent
        s["mem_used"] = _gb(vm.used)
        s["mem_total"] = _gb(vm.total)
        du = psutil.disk_usage("C:\\")
        s["disk_pct"] = du.percent
        s["disk_used"] = _gb(du.used)
        s["disk_free"] = _gb(du.free)
        s["gpu_temp"] = _gpu_temp()
        s["gpu_name"] = _gpu_name()
    except Exception as e:
        info["err"] = str(e)[:120]
    return info


def _strata_metrics():
    base = _BASE.get("strata")
    if not base:
        return {"ok": False, "error": "无 strata 引擎"}
    try:
        with urllib.request.urlopen(base + "/metrics", timeout=4) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        return {"ok": True, "metrics": j}
    except Exception as e:
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, str(e)[:100])}


# ---------------------------------------------------------------- 语音 TTS / ASR
def _is_mp3(d):
    return (d[:3] == b"ID3"
            or (len(d) > 1 and d[0] == 0xFF and (d[1] & 0xE0) == 0xE0))


def _normalize_audio(data):
    """★ 2026-10-18【用户反馈"浏览器里播放声音很小"】

    实测 IndexTTS 出来的 wav：
        "你好"   峰值 0.47  有效值 0.07
        长句     峰值 0.77  有效值 0.15
    峰值看着不算低，但**有效值只有 0.07~0.15**，听感就是"声音很小"。
    这里把峰值归一化到 0.95（只放大不缩小），增益上限 4 倍防爆音。
    失败就原样返回，绝不影响播放。
    """
    try:
        import array as _arr
        import io as _io
        import wave as _wv
        w = _wv.open(_io.BytesIO(data), "rb")
        n, sw, ch, sr = w.getnframes(), w.getsampwidth(), w.getnchannels(), w.getframerate()
        frames = w.readframes(n)
        w.close()
        if sw != 2 or not frames:
            return data
        a = _arr.array("h")
        a.frombytes(frames)
        if not a:
            return data
        peak = max(max(a), -min(a))
        if peak < 500:
            return data
        gain = (0.95 * 32767.0) / peak
        if gain > 4.0:
            gain = 4.0
        if gain <= 1.05:
            return data
        for i in range(len(a)):
            v = int(a[i] * gain)
            a[i] = 32767 if v > 32767 else (-32768 if v < -32768 else v)
        out = _io.BytesIO()
        w2 = _wv.open(out, "wb")
        w2.setnchannels(ch)
        w2.setsampwidth(sw)
        w2.setframerate(sr)
        w2.writeframes(a.tobytes())
        w2.close()
        return out.getvalue()
    except Exception:
        return data


def tts_synth(text):
    """Fairy 克隆音色合成。返回 (audio_bytes, mime) 或 (None, None)。"""
    if not _fairy_tts_get:
        return None, None
    try:
        r = _fairy_tts_get(text, timeout=120)
        d = r.get("data") if isinstance(r, dict) else None
        if not d:
            return None, None
        # ★ 合成后做一次音量归一化（解决"播放声音很小"）
        d2 = _normalize_audio(d)
        return d2, ("audio/mpeg" if _is_mp3(d2) else "audio/wav")
    except Exception:
        return None, None


def asr_recognize(b64):
    """本地 ASR 识别（wav base64 -> 文字）。返回 (text, backend)。"""
    if not _asr_bridge:
        return "", ""
    try:
        raw = base64.b64decode(b64)
        tmp = os.path.join(tempfile.gettempdir(), "fairy_chat_asr.wav")
        with open(tmp, "wb") as f:
            f.write(raw)
        samples, sr = _asr_bridge._read_wav(tmp)
        text, used, _cost = _asr_bridge.recognize(samples, sr)
        return text, used
    except Exception:
        return "", ""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj, ctype="application/json; charset=utf-8"):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/"):
            self._send(404, {"ok": False, "error": "GET 不支持 /api"})
            return
        # ★ 2026-10-17：页面上打一个构建时间 —— 用户一看就知道浏览器拿到的是
        #   新页面还是旧缓存（旧页面没有这个标记、也还带着已删除的下拉框）。
        body = _HTML.replace("__BUILT__", _BUILT_AT).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        # ★ 2026-10-17：不缓存 —— 以前改完页面，浏览器还拿旧的，
        #   显示不出新增的小字说明，看着像"改了没生效"。
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            ln = int(self.headers.get("Content-Length", 0) or 0)
            data = json.loads(self.rfile.read(ln).decode("utf-8", "replace"))
        except Exception as e:
            self._send(400, {"ok": False, "error": "请求解析失败: %s" % e})
            return
        engine = data.get("engine") or "ollama"
        try:
            port = int(data.get("port") or 11434)
        except (TypeError, ValueError):
            # ★ 2026-10-21：端口传了非数字时别再抛异常（会把连接直接掐断）
            port = 11434
        if self.path == "/api/ping":
            # ★ 2026-10-21：和 _current_preset 用同一套判据（模型 loaded 才算连接）
            _pk, _mid = _model_ready(engine, port)
            self._send(200, {"ok": _pk == "ready", "state": _pk, "model": _mid})
            return
        if self.path == "/api/info":
            self._send(200, build_info(engine, port))
            return
        if self.path == "/api/engines":
            # ★ 2026-10-17：下拉框里 4 个引擎，实测只有 strata 在线，其它三个
            #   （llm 8085 / vision 8086 / dsh）根本连不上 —— 但它照样能被选中，
            #   选了就静默失败。这里把在线状态告诉前端，由前端在下拉框里标注。
            _out = []
            for _k in ("strata", "llm", "vision", "dsh"):
                _b = _BASE.get(_k) or ""
                _p = 0
                try:
                    _p = int((_b.rsplit(":", 1)[-1]).split("/")[0])
                except Exception:
                    _p = 0
                _up = port_open(_p) if _p else False
                _out.append({"name": _k, "port": _p, "up": _up, "api": _b})
            self._send(200, {"ok": True, "engines": _out})
            return
        if self.path == "/api/metrics":
            if engine == "strata":
                d = _strata_metrics()
                # ★ 2026-10-17：Strata 在 AMD+Windows 上读不到 GPU 数据，
                #   这里把 Fairy 自己采到的补进去（前端优先用它）。
                if d.get("ok") and _GPU.get("t"):
                    _fg = {
                        "mem_gb": round((_GPU.get("mem_mb") or 0) / 1024.0, 1),
                        "util": _GPU.get("util"),
                        # ★ 2026-10-18【用户要求】温度/功耗来自 HWiNFO 共享内存
                        "temp": _GPU.get("temp"),
                        "power": _GPU.get("power"),
                        "age_s": round(_time.time() - _GPU["t"], 1),
                        "source": "windows-perfcounter + afterburner",
                        "vram_temp": (_GPU.get("extra") or {}).get("vram_temp"),
                        "fan_pct": (_GPU.get("extra") or {}).get("fan_pct"),
                        "fan_rpm": (_GPU.get("extra") or {}).get("fan_rpm"),
                    }
                    d["fairy_gpu"] = _fg
                    # ★ 前端 renderMetrics 拿到的是【metrics 子对象】，所以必须也塞进去一份，
                    #   只放顶层的话前端永远读不到（这正是"后端有数、页面还是 —"的原因）。
                    if isinstance(d.get("metrics"), dict):
                        d["metrics"]["fairy_gpu"] = _fg
                self._send(200, d)
            else:
                self._send(200, {"ok": False, "error": "仅 strata 引擎提供推理指标"})
            return
        # ---- 语音合成（Fairy 克隆音色）----
        if self.path == "/api/tts":
            text = (data.get("text") or "").strip()
            if not text:
                self._send(200, {"ok": False, "error": "空文本"})
                return
            aud, mime = tts_synth(text)
            if not aud:
                self._send(200, {"ok": False,
                                 "error": "语音合成失败（IndexTTS 9881 不可用？）"})
                return
            self._send(200, {"ok": True, "audio": base64.b64encode(aud).decode("utf-8"),
                             "mime": mime})
            return
        # ---- 语音识别（录音转文字）----
        if self.path == "/api/asr":
            b64 = data.get("audio") or ""
            if not b64:
                self._send(200, {"ok": False, "error": "无音频"})
                return
            text, used = asr_recognize(b64)
            self._send(200, {"ok": bool(text), "text": text, "backend": used})
            return
        # ---- 上下文压缩接续 ----
        if self.path == "/api/compress":
            messages = data.get("messages")
            if not messages:
                self._send(200, {"ok": False, "error": "无上下文"})
                return
            parts = []
            for m in messages:
                c = m.get("content")
                if isinstance(c, str):
                    parts.append("%s: %s" % (m.get("role"), c))
                else:
                    parts.append("%s: [图片/附件]" % m.get("role"))
            history = "\n".join(parts)[-12000:]
            prompt = ("把下面这段对话压缩成一段简洁的中文摘要（保留关键信息、结论和未决事项，250 字内）：\n"
                      + history)
            payload = {"model": (data.get("model") or "default"),
                       "messages": [{"role": "user", "content": prompt}],
                       "stream": False}
            ok, rep = _forward(engine, port, payload)
            self._send(200, {"ok": ok, "summary": rep if ok else None,
                             "error": None if ok else rep})
            return
        if self.path != "/api/chat":
            self._send(404, {"ok": False, "error": "未知接口"})
            return
        messages = data.get("messages")
        text = (data.get("text") or "").strip()
        if not messages and text:
            messages = [{"role": "user", "content": text}]
        if not messages:
            self._send(200, {"ok": False, "error": "消息为空"})
            return
        model = (data.get("model") or "").strip()
        payload = {"model": model or "default",
                   "messages": messages, "stream": False}
        ok, rep = _forward(engine, port, payload)
        log("chat engine=%s port=%d model=%s -> %s" % (engine, port, model or "-",
                                                       "ok" if ok else "err"))
        self._send(200, {"ok": ok, "reply": rep if ok else None,
                         "error": None if ok else rep})


def _gpu_sample():
    r"""用 Windows 性能计数器读 AMD 显卡：返回 (显存MB, 利用率%)，读不到返回 (None, None)。

    ★ 2026-10-17【为什么要有这个】Strata 自己的 /metrics 在 AMD 显卡 + Windows 上
      读不到 GPU 数据（它的 hardware_static.gpu_note 原话：no GPU load or VRAM readings
      for AMD cards on Windows yet）。但 Windows 自己的性能计数器可以读到 ——
      实测 \GPU Adapter Memory(*)\Dedicated Usage = 14996 MB，
      \GPU Engine(*)\Utilization Percentage 有 350 个实例。
      单次查询约 1 秒，所以只在后台线程每 4 秒采一次，接口直接读缓存。
    """
    try:
        import subprocess as _sp
        # 显存走 Get-Counter（约 1 秒）；利用率走 CIM 的 GPUEngine（约 0.3 秒，
        # 比 CIM 的 GPUAdapterMemory 快 20 倍 —— 后者首次枚举要 8 秒）。
        ps = (
            "$m=(Get-Counter '\\GPU Adapter Memory(*)\\Dedicated Usage' -ErrorAction SilentlyContinue)"
            ".CounterSamples | Measure-Object CookedValue -Sum; "
            "$u=(Get-CimInstance Win32_PerfFormattedData_GPUPerformanceCounters_GPUEngine "
            "-ErrorAction SilentlyContinue | Measure-Object UtilizationPercentage -Maximum).Maximum; "
            "Write-Output ('{0}|{1}' -f $m.Sum, $u)"
        )
        r = _sp.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                    capture_output=True, timeout=20,
                    creationflags=0x08000000)
        out = (r.stdout or b"").decode("utf-8", "replace").strip()
        if "|" not in out:
            return None, None
        mem_s, util_s = out.split("|", 1)
        mem = float(mem_s.replace(",", ".")) if mem_s.strip() else None
        util = float(util_s.replace(",", ".")) if util_s.strip() else None
        return (mem / (1024.0 * 1024.0) if mem else None), util
    except Exception:
        return None, None


def _gpu_temp_power():
    """★ 2026-10-18【用户要求：GPU 温度和功耗要显示出来】

    先后试过、全部实测过的路子：
      · AMD ADL（atiadlxx.dll，驱动自带）—— RDNA4 上 OD5/OD6/ODN 的温度、
        功耗函数**全部返回「不支持」**（RX 9070 XT 实测）；
      · Windows 性能计数器 —— 只有使用率和显存，**没有温度/功耗**；
      · HWiNFO —— 免费版共享内存要在 GUI 里手动勾，命令行参数还是 Pro 功能；
      · **MSI Afterburner** —— 用户机器上一直在跑，共享内存 `MAHMSharedMemory`
        现成可读，实测一次就拿到 77 条传感器：
            GPU temperature 31℃ · Memory temperature 34℃ · Power 33W
            Fan speed 25% · Fan tachometer 1089RPM
        零配置、零手动开关 → **选它**（见 fairy_gpu_ab.py）。

    返回 (温度℃, 功耗W, 额外dict)，读不到返回 (None, None, {})。
    """
    try:
        import fairy_gpu_ab as _ab
        s = _ab.gpu_snapshot()
        if s.get("temp") is not None or s.get("power") is not None:
            return (s.get("temp"), s.get("power"),
                    {"vram_temp": s.get("vram_temp"), "fan_pct": s.get("fan_pct"),
                     "fan_rpm": s.get("fan_rpm"), "util": s.get("util"),
                     "source": s.get("source")})
    except Exception:
        pass
    # 退路：HWiNFO（如果哪天用户在 HWiNFO 里开了共享内存）
    try:
        import fairy_gpu_hwinfo as _hw
        r = _hw.gpu_temp_power()
        return r.get("temp"), r.get("power"), {"source": "hwinfo"}
    except Exception:
        return None, None, {}


_GPU = {"mem_mb": None, "util": None, "temp": None, "power": None, "extra": {}, "t": 0.0}


def _gpu_loop():
    # 单次采样约 3.5 秒（Get-Counter 的性能计数器模块加载开销），
    # 所以间隔给 15 秒 —— GPU 显存/利用率本来也不需要秒级。
    while True:
        try:
            m, u = _gpu_sample()
            # ★ 2026-10-18：温度/功耗走 HWiNFO 共享内存（比 Get-Counter 快得多，几十毫秒）
            tp, pw, _extra = _gpu_temp_power()
            if m is not None or u is not None or tp is not None or pw is not None:
                _GPU.update(mem_mb=m, util=u, temp=tp, power=pw, extra=_extra or {}, t=_time.time())
                log("GPU 采样：显存 %.0f MB · 利用率 %s · 温度 %s ℃ · 功耗 %s W"
                    % (m or 0, u, tp if tp is not None else "-", pw if pw is not None else "-"))
            else:
                log("GPU 采样：没读到（性能计数器不可用；温度/功耗需在 HWiNFO 里开 Shared Memory Support）")
        except Exception as _e:
            log("GPU 采样异常：%s: %s" % (type(_e).__name__, _e))
        _time.sleep(15)


def main():
    if port_open(PORT):
        log("8090 已在跑，不动")
        return 0
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    except Exception as e:
        log("起服务失败: %s" % e)
        return 1
    # ★ 后台采 AMD 显卡数据（Strata 在 Windows 读不到，由 Fairy 补）
    try:
        threading.Thread(target=_gpu_loop, daemon=True).start()
    except Exception:
        pass
    log("Fairy 统一对话页已启动 http://127.0.0.1:%d/" % PORT)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
