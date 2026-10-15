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
import time as _time
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
_BASE = {"strata": "http://127.0.0.1:8080",
         "ollama": "http://127.0.0.1:11434",
         "llm": "http://127.0.0.1:8085",
         "vision": "http://127.0.0.1:8086",
         "dsh": "http://127.0.0.1:19387"}
if os.name == "nt":
    CREATE_NO_WINDOW = 0x08000000
else:
    CREATE_NO_WINDOW = 0

_HTML = """<!doctype html>
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
.iconBtn{width:32px;height:32px;border-radius:10px;background:var(--bg3);border:1px solid var(--line);color:var(--fg2);display:inline-flex;align-items:center;justify-content:center;transition:.18s;font-size:15px}
.iconBtn:hover{color:var(--fg);border-color:var(--brand1)}
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
.recOn{color:var(--err)!important;border-color:var(--err)!important;animation:pulse 1.1s infinite}
/* ============ 右栏：监控（Strata 风格） ============ */
.rHead{flex:none;font-size:13px;font-weight:700;letter-spacing:.8px;text-transform:uppercase;color:var(--fg2);
  padding:14px 16px 10px;border-bottom:1px solid var(--line)}
.rightBody{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:12px}
.card{background:linear-gradient(165deg,var(--bg2),var(--bg3));border:1px solid var(--line);border-radius:var(--r);padding:14px;box-shadow:var(--sh)}
.card .ct{margin:0 0 10px;font-size:11px;color:var(--fg2);font-weight:700;letter-spacing:.6px;text-transform:uppercase}
.stateCard{position:relative;overflow:hidden}
.stateCard::before{content:"";position:absolute;top:0;left:0;right:0;height:2px;background:linear-gradient(92deg,var(--brand1),var(--brand2));opacity:.55}
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
    <div class="logo">Fairy 对话</div>
    <button class="newBtn" id="newBtn">＋ 新对话</button>
    <div class="convList" id="convList"></div>
  </aside>

  <!-- 中间：聊天 -->
  <main class="col col-chat">
    <div class="chatTop">
      <div class="pill" id="pEngine" data-s="busy"><span class="d"></span><span id="tEngine">引擎加载中…</span></div>
      <div class="pill" id="pModel" data-s="busy"><span class="d"></span><span id="tModel">模型加载中…</span></div>
      <select class="engSel" id="engSel" title="AI 后端：本地 / 网络">
        <option value="strata">Strata 本地</option>
        <option value="llm">Qwen3.5-9B 文本</option>
        <option value="vision">Qwen3-VL-8B 视觉</option>
        <option value="dsh">网络AI</option>
      </select>
      <span class="spacer"></span>
      <button class="iconBtn" id="tTheme" title="切换深浅主题">◐</button>
    </div>
    <div class="msgs" id="msgs"><div class="empty" id="empty">和当前引擎说点什么吧（输入后回车发送）</div></div>
    <div class="attachBar" id="attachBar" style="display:none"><span id="attachInfo"></span><button class="iconBtn sm" id="attachClear" title="移除附件">✕</button></div>
    <div class="inputBar">
      <button class="iconBtn" id="btnImg" title="上传图片">🖼</button>
      <button class="iconBtn" id="btnFile" title="上传文件">📎</button>
      <button class="iconBtn" id="btnVoice" title="语音输入（录音→转文字）">🎤</button>
      <textarea id="inp" placeholder="输入消息，Enter 发送，Shift+Enter 换行"></textarea>
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
        <div class="mCard"><span class="mLab">GPU 负载</span><span class="mVal" id="mv-gpu">–</span><span class="mSub"></span></div>
        <div class="mCard"><span class="mLab">显存</span><span class="mVal" id="mv-vram">–</span><span class="mSub"></span></div>
        <div class="mCard"><span class="mLab">GPU 温度</span><span class="mVal" id="mv-temp">–</span><span class="mSub"></span></div>
        <div class="mCard"><span class="mLab">功耗</span><span class="mVal" id="mv-power">–</span><span class="mSub"></span></div>
        <div class="mCard"><span class="mLab">CPU</span><span class="mVal" id="mv-cpu">–</span><span class="mSub"></span></div>
        <div class="mCard mWide"><span class="mLab">磁盘读取</span><span class="mVal" id="mv-disk">–</span><span class="mSub"></span></div>
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
const $ = id => document.getElementById(id);
(function(){
  const q = new URLSearchParams(location.search);
  P.engine = q.get("engine") || P.engine;
  P.port = parseInt(q.get("port") || P.port, 10);
  P.model = q.get("model") || "";
  $("tEngine").textContent = P.engine + " :" + P.port;
  const EPORT = {strata:8080, llm:8085, vision:8086, dsh:19387, ollama:11434};
  $("engSel").value = EPORT[P.engine] ? P.engine : "strata";
  $("engSel").addEventListener("change", ()=>{
    const want = $("engSel").value;
    const port = EPORT[want] || 8080;
    const q2 = new URLSearchParams(location.search);
    q2.set("engine", want); q2.set("port", port);
    location.href = location.pathname + "?" + q2.toString();
  });
  $("tModel").textContent = P.model || "默认模型";
  setState("pEngine", "ok");
  setState("pModel", P.model ? "ok" : "busy");
  let t = "dark"; try{ t = localStorage.getItem("fairy.theme") || "dark"; }catch(e){}
  document.documentElement.dataset.theme = t;
  loadConvs();
  probe(); loadSys();
  setInterval(()=>{ loadSys(); loadMetrics(); }, 2500);
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
  for(const m of conv.msgs) addMsgEl(box, m);
  box.scrollTop = box.scrollHeight;
}
function addMsgEl(box, m){
  const role = m.role;
  const el = document.createElement("div"); el.className = "msg " + (role==="user"?"user":"ai");
  const av = document.createElement("div"); av.className="avatar"; av.textContent = role==="user"?"我":"F";
  const col = document.createElement("div"); col.className="body";
  const bub = document.createElement("div"); bub.className="bub"; bub.textContent = m.text || "（空回复）";
  col.appendChild(bub);
  if(m.img){ const im=document.createElement("img"); im.className="msgImg"; im.src=m.img; col.appendChild(im); }
  if(m.fname){ const ft=document.createElement("span"); ft.className="fileTag"; ft.textContent="📎 "+m.fname; col.appendChild(ft); }
  if(role==="ai"){
    const ops = document.createElement("div"); ops.className="opRow";
    ops.innerHTML = '<button class="op" data-op="play" title="语音播放">🔊 播放</button>'+
                    '<button class="op" data-op="compress" title="上下文压缩接续">↻ 压缩</button>'+
                    '<button class="op" data-op="copy" title="复制">⧉ 复制</button>'+
                    '<button class="op" data-op="share" title="分享给其他软件">📤 分享</button>';
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
  if(act==="play") playMsg(text);
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
    recOn = true; btn.classList.add("recOn"); btn.textContent = "⏹";
    $("sysNote").textContent = "正在录音…再点一次结束并识别";
  }catch(e){ $("sysNote").textContent = "麦克风不可用"; }
}
async function stopRec(){
  const btn = $("btnVoice");
  spNode.disconnect(); source.disconnect(); micStream.getTracks().forEach(t=>t.stop()); ac.close();
  const len = recFrames.reduce((a,f)=>a+f.length, 0);
  const all = new Float32Array(len); let o=0;
  for(const f of recFrames){ all.set(f, o); o += f.length; }
  recOn = false; btn.classList.remove("recOn"); btn.textContent = "🎤";
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
  try{
    const r = await fetch("/api/tts", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({text:text})});
    const j = await r.json();
    if(!j.ok){ $("sysNote").textContent = "语音合成失败: " + (j.error||"IndexTTS 不可用"); return; }
    const bin = atob(j.audio);
    const arr = new Uint8Array(bin.length);
    for(let i=0;i<bin.length;i++) arr[i] = bin.charCodeAt(i);
    const url = URL.createObjectURL(new Blob([arr], {type: j.mime || "audio/wav"}));
    const au = new Audio(url); au.play().catch(()=>{});
  }catch(e){ $("sysNote").textContent = "语音播放失败"; }
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
async function send(){
  const conv = curConv(); if(!conv || busy) return;
  const inp = $("inp"); const txt = inp.value.trim();
  let curText = txt;
  if(attach.ftext) curText = (curText ? curText + "\n\n" : "") + "【文件 " + attach.fname + " 内容】\n" + attach.ftext;
  if(!curText.trim() && !attach.img) return;
  inp.value = ""; busy = true; $("send").disabled = true;
  const messages = conv.msgs.map(m=>{
    if(m.img && m.role==="user"){
      return {role:"user", content:[{type:"text", text:m.text||""},{type:"image_url", image_url:{url:m.img}}]};
    }
    return {role:m.role==="user"?"user":"assistant", content:m.text};
  });
  const userMsg = {role:"user", text:curText.trim()};
  if(attach.img) userMsg.img = attach.img;
  if(attach.fname) userMsg.fname = attach.fname;
  if(attach.img){
    messages.push({role:"user", content:[
      {type:"text", text:curText.trim() || "（图片）"},
      {type:"image_url", image_url:{url:attach.img}}
    ]});
  } else {
    messages.push({role:"user", content: curText.trim()});
  }
  conv.msgs.push(userMsg);
  if(conv.title==="新对话") conv.title = (curText.trim() || "图片").slice(0,14);
  conv.msgs.push({role:"ai", text:"思考中…"});
  attach = {img:null,fname:null,ftext:null,fsize:0}; showAttach();
  save(); renderList(); renderMsgs();
  try{
    const r = await fetch("/api/chat", {method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({engine:P.engine, port:P.port, model:P.model, messages})});
    const j = await r.json();
    const reply = j.ok ? (j.reply||"（空回复）") : ("出错："+(j.error||r.status));
    conv.msgs[conv.msgs.length-1] = {role:"ai", text:reply};
    setState("pModel", j.ok?"ok":"err");
    if(j.ok && reply && reply!=="（空回复）") playMsg(reply); // 自动语音播报
  }catch(e){
    conv.msgs[conv.msgs.length-1] = {role:"ai", text:"请求失败："+e};
    setState("pModel","err");
  }
  busy = false; $("send").disabled = false;
  save(); renderList(); renderMsgs();
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
    .then(r=>r.json()).then(j=>{ if(j.ok) paintSys(j); }).catch(()=>{});
}
function paintSys(j){
  const s = j.sys || {};
  if(s.mem_pct!=null){ $("sys-mem").textContent = Math.round(s.mem_pct)+"% ("+(s.mem_used||"-")+" / "+(s.mem_total||"-")+")"; $("p-mem").style.width = s.mem_pct+"%"; }
  if(s.cpu!=null){ $("sys-cpu").textContent = Math.round(s.cpu)+"%"; $("p-cpu").style.width = s.cpu+"%"; }
  if(s.disk_pct!=null){ $("sys-disk").textContent = Math.round(s.disk_pct)+"%"; $("p-disk").style.width = s.disk_pct+"%"; }
  $("sysNote").textContent = (s.cpu_name||"--") + " · " + (s.gpu_name||"--");
}
function loadMetrics(){
  if(P.engine!=="strata"){ renderState(null); return; }
  fetch("/api/metrics", {method:"POST", headers:{"Content-Type":"application/json"},
    body:JSON.stringify({engine:P.engine, port:P.port})})
    .then(r=>r.json()).then(j=>{ if(j.ok) renderMetrics(j.metrics); else renderState(null); }).catch(()=>renderState(null));
}
function renderState(state){
  const map = {"idle":"空闲","reading":"读取中…","generating":"生成中…","queued":"排队中…","error":"错误"};
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
  renderState(lv.state || "idle");
  setMetric("speed", lv.tok_s!=null?Number(lv.tok_s).toFixed(1):null, "t/s", lv.prefill_tok_s_mean!=null?("预填充 "+(lv.prefill_tok_s_mean|0)+" t/s"):"");
  setMetric("gpu", lv.gpu_util!=null?Math.round(lv.gpu_util):null, "%");
  setMetric("vram", lv.gpu_mem_used!=null?Number(lv.gpu_mem_used).toFixed(1):null, "GB");
  setMetric("temp", lv.gpu_temp!=null?Math.round(lv.gpu_temp):null, "°C");
  setMetric("power", lv.gpu_power!=null?Math.round(lv.gpu_power):null, "W");
  setMetric("cpu", lv.cpu!=null?Math.round(lv.cpu):null, "%");
  setMetric("disk", lv.disk_read_mb!=null?Number(lv.disk_read_mb).toFixed(0):null, "MB/s");
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
    base = _BASE.get(engine)
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
            with urllib.request.urlopen(req, timeout=180) as r:
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
    url = base + "/v1/chat/completions"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            j = json.loads(r.read().decode("utf-8", "replace"))
        msg = (j.get("choices") or [{}])[0].get("message") or {}
        return True, (msg.get("content") or "").strip()
    except Exception as e:
        return False, "%s: %s" % (type(e).__name__, str(e)[:200])


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


def build_info(engine, port):
    info = {"ok": True,
            "engine": {"name": engine, "port": port,
                       "api": _BASE.get(engine, "")},
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


def tts_synth(text):
    """Fairy 克隆音色合成。返回 (audio_bytes, mime) 或 (None, None)。"""
    if not _fairy_tts_get:
        return None, None
    try:
        r = _fairy_tts_get(text, timeout=120)
        d = r.get("data") if isinstance(r, dict) else None
        if not d:
            return None, None
        return d, ("audio/mpeg" if _is_mp3(d) else "audio/wav")
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
        body = _HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
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
        port = int(data.get("port") or 11434)
        if self.path == "/api/ping":
            base = _BASE.get(engine)
            ok = False
            if base:
                try:
                    with urllib.request.urlopen(base + "/v1/models", timeout=3) as r:
                        ok = r.status == 200
                except Exception:
                    ok = False
            self._send(200, {"ok": ok})
            return
        if self.path == "/api/info":
            self._send(200, build_info(engine, port))
            return
        if self.path == "/api/metrics":
            if engine == "strata":
                self._send(200, _strata_metrics())
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


def main():
    if port_open(PORT):
        log("8090 已在跑，不动")
        return 0
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    except Exception as e:
        log("起服务失败: %s" % e)
        return 1
    log("Fairy 统一对话页已启动 http://127.0.0.1:%d/" % PORT)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
