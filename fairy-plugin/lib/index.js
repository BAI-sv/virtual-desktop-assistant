import { createReadStream, existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { join, normalize, resolve as presolve, sep } from 'node:path';
import Schema from '@deepseek-ai/schemastery';
export const name = 'fairy';
export const inject = ['webServer'];
export const Config = Schema.object({
  assetsDir: Schema.string().default('<FAIRY_ROOT>\\assets\\ball_128').volatile(),
  size: Schema.number().min(40).max(400).default(128).volatile(),
  ttsUrl: Schema.string().default('http://127.0.0.1:9881/tts').volatile(),
  speakUrl: Schema.string().default('http://127.0.0.1:19387/dsh-tts-api/speak').volatile(),
  refAudio: Schema.string().default('<FAIRY_ROOT>\\voice\\index_voices\\fairy.wav').volatile(),
  voiceIndex: Schema.string().default('<FAIRY_ROOT>\\voice\\voice_index.json').volatile(),
  autoSpeak: Schema.boolean().default(true).volatile(),
});
const DEFAULTS = {
  assetsDir: '<FAIRY_ROOT>\\assets\\ball_128',
  size: 128,
  ttsUrl: 'http://127.0.0.1:9881/tts',
  speakUrl: 'http://127.0.0.1:19387/dsh-tts-api/speak',
  refAudio: '<FAIRY_ROOT>\\voice\\index_voices\\fairy.wav',
  voiceIndex: '<FAIRY_ROOT>\\voice\\voice_index.json',
  autoSpeak: true,
};
function pick(config, key, want, fallback) {
  const v = config ? config[key] : undefined;
  if (want === 'string' && typeof v === 'string' && v.trim()) return v.trim();
  if (want === 'number' && typeof v === 'number' && Number.isFinite(v)) return v;
  if (want === 'boolean' && typeof v === 'boolean') return v;
  return fallback;
}
function resolveConfig(config) {
  return {
    assetsDir: pick(config, 'assetsDir', 'string', DEFAULTS.assetsDir),
    size: pick(config, 'size', 'number', DEFAULTS.size),
    ttsUrl: pick(config, 'ttsUrl', 'string', DEFAULTS.ttsUrl),
    speakUrl: pick(config, 'speakUrl', 'string', DEFAULTS.speakUrl),
    refAudio: pick(config, 'refAudio', 'string', DEFAULTS.refAudio),
    voiceIndex: pick(config, 'voiceIndex', 'string', DEFAULTS.voiceIndex),
    autoSpeak: pick(config, 'autoSpeak', 'boolean', DEFAULTS.autoSpeak),
  };
}
function safeJoin(root, rel) {
  const target = normalize(join(root, rel));
  const base = presolve(root) + sep;
  const full = presolve(target);
  return full.startsWith(base) ? full : null;
}
function listFrames(dir) {
  try {
    return readdirSync(dir).filter((f) => /\.png$/i.test(f)).sort();
  } catch {
    return [];
  }
}
function anyAgentRunning(ctx) {
  try {
    const agents = ctx.get('agents');
    if (agents === undefined || agents === null || typeof agents.list !== 'function') return undefined;
    const list = agents.list();
    if (!Array.isArray(list)) return undefined;
    return list.some((agent) => agent !== null && agent !== undefined && agent.status === 'running');
  } catch {
    return undefined;
  }
}
const TOOL_PHRASE = {
  run_python: '正在跑 Python', run_node: '正在跑 Node',
  pwsh: '正在执行命令', bash: '正在执行命令',
  read: '正在读文件', write: '正在写文件', edit: '正在改代码',
  glob: '正在找文件', grep: '正在搜代码',
  web_search: '正在查资料', web_fetch: '正在读网页',
  subagent: '正在派子代理', subagent_fork: '正在派子代理',
  todo_write: '正在整理计划', skill: '正在加载技能',
  goal: '正在推进目标', job_output: '正在收任务结果',
  present: '正在交付文件', memory_update: '正在记东西',
  present: '正在交付文件',
  ego_status: '正在查看网页', ego_snapshot: '正在查看网页', ego_page_info: '正在查看网页',
  ego_read_element: '正在查看网页', ego_help: '正在查手册', ego_doctor: '正在自检浏览器',
  ego_navigate: '正在打开网页', ego_click: '正在点击页面', ego_fill: '正在填写表单',
  ego_key: '正在输入内容', ego_select: '正在选择选项', ego_screenshot: '正在截图',
  ego_js: '正在操作网页', ego_cdp: '正在操作网页', ego_cli: '正在操作浏览器',
  ego_script: '正在操作浏览器', ego_hover: '正在操作网页', ego_scroll: '正在滚动页面',
  ego_drag: '正在拖拽页面', ego_check: '正在勾选选项', ego_dialog: '正在处理弹窗',
  ego_http: '正在发网络请求', ego_wait: '正在等页面加载',
  ego_wait_for_selector: '正在等页面出现', ego_wait_for_url: '正在等页面跳转',
  ego_wait_for_response: '正在等接口返回', ego_upload: '正在上传文件',
  ego_download: '正在下载文件', ego_captcha: '正在检查人机验证',
  ego_space_open: '正在打开浏览器', ego_space_close: '正在关闭浏览器',
  ego_auth_flush: '正在保存登录状态', ego_login_import: '正在导入登录状态',
  aigc_get_provider_info: '正在检查绘图服务', aigc_provider_set_instructions: '正在配置绘图服务',
  aigc_http_request: '正在调用绘图接口', aigc_canvas_place: '正在摆放到画布',
  aigc_canvas_link: '正在连接画布元素', aigc_canvas_unlink: '正在断开画布连接',
  aigc_canvas_list_elements: '正在查看画布', aigc_media_edit: '正在编辑媒体',
  mineru_health: '正在检查解析服务', mineru_submit_parse_job: '正在提交解析',
  mineru_parse_document: '正在解析文档', mineru_get_parse_status: '正在等解析完成',
  mineru_get_parse_result: '正在读取解析结果',
  job_output: '正在收任务结果', job_list: '正在查看任务', job_kill: '正在停止任务',
  send_message: '正在联系子代理', interrupt_agent: '正在叫停子代理', list_agents: '正在查看子代理',
  workflow: '正在编排多代理干活',
  get_goal: '正在查看目标', create_goal: '正在建立目标', update_goal: '正在更新目标',
  ask_user_question: '正在等你确认', exit_plan_mode: '正在整理方案',
  schedule_create: '正在设置定时', schedule_list: '正在查看定时',
  schedule_delete: '正在删除定时', schedule_update: '正在修改定时',
  sleep: '正在等待', plugin_manager: '正在管理插件',
  load_workspace_dependencies: '正在准备运行环境',
  cordis_inspect_list: '正在查插件接口', cordis_inspect_query: '正在查插件接口',
  read_image: '正在看图片',
};
function inferMood(hour, turnMs, errStreak) {
  if (hour >= 0 && hour < 6) {
    return { key: 'late', label: '深夜了', inferred: true };
  }
  if (errStreak >= 2) {
    return { key: 'stuck', label: '连着碰壁', inferred: true };
  }
  if (turnMs > 10 * 60 * 1000) {
    return { key: 'long', label: '这轮干挺久', inferred: true };
  }
  if (hour >= 22 || hour < 8) {
    return { key: 'night', label: '夜深了', inferred: true };
  }
  return { key: 'ok', label: '正常', inferred: true };
}
function readContextUsage(ctx) {
  const out = { pct: null, used: null, limit: null, source: '' };
  try {
    const tm = ctx.get('tokenMeter');
    if (!tm || typeof tm.measure !== 'function') return out;
    const agents = ctx.get('agents');
    const list = agents && typeof agents.list === 'function' ? agents.list() : [];
    const agent = Array.isArray(list) && list.length ? list[0] : null;
    if (!agent) return out;
    const sessions = ctx.get('sessions');
    let session = null;
    if (sessions) {
      if (typeof sessions.get === 'function') session = sessions.get(agent.id);
      else if (typeof sessions.current === 'function') session = sessions.current();
    }
    if (!session) return out;
    const m = tm.measure(session);
    const used = Number(m && m.surfaceTokens);
    let limit = NaN;
    try {
      const rc = typeof session.requestContext === 'function' ? session.requestContext() : null;
      limit = Number(rc && rc.contextWindow);
    } catch {          }
    if (Number.isFinite(used)) {
      out.used = used;
      out.source = 'tokenMeter.surfaceTokens';
      if (Number.isFinite(limit) && limit > 0) {
        out.limit = limit;
        out.pct = Math.max(0, Math.min(100, Math.round((used / limit) * 100)));
      }
    }
  } catch {                        }
  return out;
}
function humanFile(f) {
  const s = String(f || '').split(/[\\/]/).pop() || '';
  const low = s.toLowerCase();
  if (/voice|语音|\.wav|\.mp3/.test(low)) return '语音数据';
  if (/\.gguf|\.safetensors|\.pth|\.bin|model|模型/.test(low)) return '模型文件';
  if (/voice_index|config|\.json$/.test(low)) return '配置数据';
  if (/log|\.log/.test(low)) return '日志';
  if (/\.png|\.jpg|\.jpeg|\.webp/.test(low)) return '图片';
  if (/\.md$/.test(low)) return '文档';
  if (/\.(js|mjs|ts|py|json|ps1|bat|cmd|yml|yaml)$/.test(low)) return '代码文件';
  return s.length > 16 ? s.slice(0, 16) + '…' : (s || '文件');
}
function describeAction(name, args) {
  const a = (args && typeof args === 'object') ? args : {};
  const p = a.path || a.file_path || a.filePath || a.pattern || a.query || '';
  switch (String(name || '')) {
    case 'read': return '正在读取 ' + humanFile(p);
    case 'write': return '正在写入 ' + humanFile(p);
    case 'edit': return '正在修改 ' + humanFile(p);
    case 'glob': case 'grep': return '正在搜索文件';
    case 'pwsh': case 'bash': return '正在执行命令';
    case 'run_python': case 'run_node': return '正在计算分析';
    case 'web_search': return '正在搜索网页';
    case 'web_fetch': return '正在读取网页';
    case 'subagent': case 'subagent_fork': return '正在派子代理干活';
    case 'scan_nearby': return '正在扫描附近设备';
    case 'identify_device': return '正在识别设备';
    case 'watch_devices': return '正在监听设备上下线';
    case 'capability_check': return '正在自检本机能力';
    case 'security_status': return '正在做安全体检';
    case 'comfy_generate': return '正在生成图片';
    case 'comfy_status': return '正在检查 ComfyUI';
    case 'memory_update': return '正在记录记忆';
    case 'todo_write': return '正在整理计划';
    case 'skill': return '正在加载技能';
    case 'present': return '正在交付文件';
    default: return TOOL_PHRASE[name] || ('正在使用 ' + name);
  }
}
const BURST = { ts: 0, n: 0 };
const BURST_WINDOW_MS = 3000;
function noteTool() {
  const now = Date.now();
  if (now - BURST.ts > BURST_WINDOW_MS) BURST.n = 0;
  BURST.n += 1;
  BURST.ts = now;
  return BURST.n;
}
function readMode(ctx) {
  const out = { agents: 0, jobs: 0, concurrent: 0, label: '串行' };
  try {
    const a = ctx.get('agents');
    const list = a && typeof a.list === 'function' ? a.list() : [];
    if (Array.isArray(list)) out.agents = Math.max(0, list.length - 1);
  } catch {          }
  try {
    const j = ctx.get('jobs');
    let L = null;
    if (j) {
      if (typeof j.list === 'function') L = j.list();
      else if (typeof j.all === 'function') L = j.all();
    }
    if (Array.isArray(L)) {
      out.jobs = L.filter((x) => x && (x.status === 'running' || x.running === true)).length;
    }
  } catch {          }
  if (Date.now() - BURST.ts < BURST_WINDOW_MS && BURST.n >= 2) out.concurrent = BURST.n;
  const parts = [];
  if (out.concurrent >= 2) parts.push('并行 ' + out.concurrent + ' 路');
  if (out.agents > 0) parts.push('多代理 ' + out.agents);
  if (out.jobs > 0) parts.push('后台 ' + out.jobs);
  out.label = parts.length ? parts.join(' + ') : '串行';
  return out;
}
function createProgressTracker() {
  let steps = 0;
  let lastTool = '';
  let lastPhrase = '';
  let errStreak = 0;
  let turnStartedAt = 0;
  return {
    onTool(name, args) {
      if (steps === 0) turnStartedAt = Date.now();
      steps += 1;
      lastTool = String(name || '');
      lastPhrase = describeAction(lastTool, args);
    },
    onError() {
      errStreak += 1;
    },
    onIdle() {
      turnStartedAt = 0;
    },
    snapshot() {
      const turnMs = turnStartedAt ? Date.now() - turnStartedAt : 0;
      return {
        steps,
        lastTool,
        doing: lastPhrase,
        turnMs,
        mood: inferMood(new Date().getHours(), turnMs, errStreak),
      };
    },
  };
}
const TASK = { total: 0, done: 0, label: '' };
const REPLY = {
  seq: 0,
  text: '',
  full: '',
  truncated: false,
  time: 0,
  sessionId: '',
  turn: 0,
  step: 0,
  source: 'dsh',
  rawShape: '',
};
const REPLY_MAX_CHARS = 150;
function extractAssistantText(msg) {
  const out = [];
  const blocks = msg && msg.content;
  if (Array.isArray(blocks)) {
    for (const b of blocks) {
      if (b && b.type === 'text' && typeof b.text === 'string') out.push(b.text);
    }
  } else if (typeof blocks === 'string') {
    out.push(blocks);
  }
  return out.join('\n');
}
function cleanForSpeech(s) {
  let t = String(s || '');
  t = t.replace(/```[\s\S]*?```/g, ' ');
  t = t.replace(/`[^`\n]*`/g, ' ');
  t = t.replace(/^\s*\|.*\|\s*$/gm, ' ');
  t = t.replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1');
  t = t.replace(/https?:\/\/\S+/g, ' ');
  t = t.replace(/^\s{0,3}#{1,6}\s*/gm, '');
  t = t.replace(/(\*\*|__|\*|_|~~)/g, '');
  t = t.replace(/^\s*[-:| ]{3,}\s*$/gm, ' ');
  t = t.replace(/[ \t]+/g, ' ').replace(/\n{2,}/g, '\n').trim();
  return t;
}
function speakablePrefix(t, maxChars) {
  if (t.length <= maxChars) return { text: t, truncated: false };
  const head = t.slice(0, maxChars);
  let cut = -1;
  for (const m of ['。', '！', '？', '\n', '. ', '；']) {
    const i = head.lastIndexOf(m);
    if (i > cut) cut = i;
  }
  const body = cut >= Math.floor(maxChars * 0.5) ? head.slice(0, cut + 1) : head;
  return { text: body.trim(), truncated: true };
}
export function apply(ctx, config) {
  const cfg = resolveConfig(config);
  const frames = listFrames(cfg.assetsDir);
  const startedAt = Date.now();
  const tracker = createProgressTracker();
  try {
    ctx.on('tools/pre-execute', (exec, next) => {
      try {
        tracker.onTool(exec && exec.name, exec && exec.arguments);
        noteTool();
      } catch {
      }
      return next();
    });
    ctx.on('agent/error', () => {
      try { tracker.onError(); } catch {          }
    });
    ctx.on('agent/status', (payload) => {
      try {
        if (payload && payload.status === 'idle') tracker.onIdle();
      } catch {
      }
    });
    ctx.on('session/event', (session, event) => {
      try {
        if (!event || event.type !== 'assistant/message') return;
        const hdr = session && session.header;
        if (hdr && (hdr.origin === 'subagent' || (hdr.delegationDepth || 0) > 0)) return;
        const msg = event.data && event.data.message;
        const raw = extractAssistantText(msg);
        if (!raw.trim()) return;
        const cleaned = cleanForSpeech(raw);
        if (!cleaned || cleaned.length < 2) return;
        const seq = Math.max(Number(event.seq) || 0, REPLY.seq + 1);
        if (seq <= REPLY.seq) return;
        const { text, truncated } = speakablePrefix(cleaned, REPLY_MAX_CHARS);
        REPLY.seq = seq;
        REPLY.text = text;
        REPLY.full = cleaned;
        REPLY.truncated = truncated;
        REPLY.time = Date.now();
        REPLY.sessionId = String((session && session.id) || '');
        REPLY.turn = (event.data && event.data.turn) || 0;
        REPLY.step = (event.data && event.data.step) || 0;
        REPLY.source = 'dsh';
        if (!REPLY.rawShape) {
          try {
            REPLY.rawShape = JSON.stringify({
              eventType: event.type,
              keys: Object.keys(event.data || {}),
              msgKeys: msg ? Object.keys(msg) : [],
              blockTypes: Array.isArray(msg && msg.content)
                ? msg.content.map((b) => (b && b.type) || '?') : [],
              textLen: raw.length,
            }).slice(0, 800);
          } catch {                    }
        }
      } catch (e) {
        console.error('[dsh-plugin-fairy] session/event 处理失败:', e);
      }
    });
  } catch {
  }
  ctx.effect(
    () =>
      ctx.webServer.register({
        kind: 'prefix',
        path: '/fairy/ball',
        handler: async (req, res) => {
          const url = new URL(req.url ?? '/', 'http://localhost');
          const rel = decodeURIComponent(url.pathname.slice('/fairy/ball'.length + 1));
          const full = safeJoin(cfg.assetsDir, rel);
          if (!full || !existsSync(full)) {
            res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
            res.end('not found');
            return;
          }
          res.writeHead(200, {
            'content-type': 'image/png',
            'cache-control': 'public, max-age=86400',
          });
          createReadStream(full).pipe(res);
        },
      }),
    'dsh-plugin-fairy: /fairy/ball 素材路由'
  );
  ctx.effect(
    () =>
      ctx.webServer.register({
        kind: 'exact',
        path: '/api/fairy/reply',
        handler: async (req, res) => {
          if (req.method !== 'GET') {
            res.writeHead(405, { 'content-type': 'text/plain; charset=utf-8' });
            res.end('method not allowed');
            return;
          }
          let debug = false;
          try {
            debug = new URL(req.url ?? '/', 'http://localhost').searchParams.get('debug') === '1';
          } catch {                            }
          res.writeHead(200, {
            'content-type': 'application/json; charset=utf-8',
            'cache-control': 'no-store',
          });
          res.end(
            JSON.stringify({
              ok: true,
              seq: REPLY.seq,
              text: REPLY.text,
              truncated: REPLY.truncated,
              fullLength: REPLY.full ? REPLY.full.length : 0,
              time: REPLY.time,
              sessionId: REPLY.sessionId,
              turn: REPLY.turn,
              step: REPLY.step,
              source: REPLY.source,
              ...(debug ? { rawShape: REPLY.rawShape } : {}),
            })
          );
        },
      }),
    'dsh-plugin-fairy: /api/fairy/reply 助手回复路由'
  );
  ctx.effect(
    () =>
      ctx.webServer.register({
        kind: 'exact',
        path: '/api/fairy/push',
        handler: async (req, res) => {
          const sendJson = (code, obj) => {
            res.writeHead(code, {
              'content-type': 'application/json; charset=utf-8',
              'cache-control': 'no-store',
            });
            res.end(JSON.stringify(obj));
          };
          if (req.method !== 'POST') {
            res.writeHead(405, { 'content-type': 'text/plain; charset=utf-8' });
            res.end('method not allowed');
            return;
          }
          try {
            let body = '';
            for await (const chunk of req) body += chunk;
            let raw = '';
            let source = 'external';
            try {
              const o = JSON.parse(body || '{}');
              raw = typeof o.text === 'string' ? o.text : '';
              if (typeof o.source === 'string' && o.source) source = o.source.slice(0, 40);
            } catch {
              sendJson(400, { ok: false, error: 'body 不是合法 JSON' });
              return;
            }
            const cleaned = cleanForSpeech(raw);
            if (!cleaned || cleaned.length < 2) {
              sendJson(400, { ok: false, error: '清洗后为空（太短或全是代码块）' });
              return;
            }
            const { text: spoken, truncated } = speakablePrefix(cleaned, REPLY_MAX_CHARS);
            REPLY.seq += 1;
            REPLY.text = spoken;
            REPLY.full = cleaned;
            REPLY.truncated = truncated;
            REPLY.time = Date.now();
            REPLY.sessionId = '';
            REPLY.turn = 0;
            REPLY.step = 0;
            REPLY.source = source;
            sendJson(200, {
              ok: true,
              seq: REPLY.seq,
              text: spoken,
              truncated: truncated,
              fullLength: cleaned.length,
              source: source,
            });
          } catch (e) {
            console.error('[dsh-plugin-fairy] /api/fairy/push 失败:', e);
            try { sendJson(500, { ok: false, error: String((e && e.message) || e) }); } catch {            }
          }
        },
      }),
    'dsh-plugin-fairy: /api/fairy/push 外部文本入队'
  );
  ctx.effect(
    () =>
      ctx.webServer.register({
        kind: 'exact',
        path: '/api/fairy/meta',
        handler: async (req, res) => {
          if (req.method !== 'GET') {
            res.writeHead(405, { 'content-type': 'text/plain; charset=utf-8' });
            res.end('method not allowed');
            return;
          }
          let voiceCount = 0;
          try {
            const j = JSON.parse(await readFile(cfg.voiceIndex, 'utf-8'));
            if (j && Number.isFinite(j.count)) voiceCount = j.count;
            else if (Array.isArray(j?.lines)) voiceCount = j.lines.length;
            else if (Array.isArray(j)) voiceCount = j.length;
            else if (j && typeof j === 'object') {
              for (const k of ['lines', 'entries', 'voices', 'items']) {
                if (Array.isArray(j[k])) { voiceCount = j[k].length; break; }
              }
            }
          } catch {
            voiceCount = 0;
          }
          res.writeHead(200, {
            'content-type': 'application/json; charset=utf-8',
            'cache-control': 'no-store',
          });
          res.end(
            JSON.stringify({
              ok: true,
              code: 'v2-pick-and-tracker',
              size: cfg.size,
              frames: frames.length,
              firstFrame: frames[0] || null,
              fps: 33,
              autoSpeak: !!cfg.autoSpeak,
              voiceCount,
              ttsUrl: cfg.ttsUrl,
              uptimeMs: Date.now() - startedAt,
              running: anyAgentRunning(ctx) ?? null,
              ...tracker.snapshot(),
              progress: (function () {
                try {
                  const g = ctx.get('goals');
                  if (g && typeof g.get === 'function') {
                    const agents = ctx.get('agents');
                    const list = agents && typeof agents.list === 'function' ? agents.list() : [];
                    const agent = Array.isArray(list) && list.length ? list[0] : null;
                    if (agent) {
                      const v = g.get(agent);
                      if (v && v.phase === 'active') {
                        const max = Number(v.maxGoalRounds);
                        if (Number.isFinite(max) && max > 0) {
                          return Math.max(0, Math.min(100,
                            Math.round((Number(v.roundsStarted) / max) * 100)));
                        }
                      }
                    }
                  }
                } catch {          }
                if (TASK.total > 0) {
                  return Math.max(0, Math.min(100, Math.round((TASK.done / TASK.total) * 100)));
                }
                return null;
              })(),
              task: { total: TASK.total, done: TASK.done, label: TASK.label },
              mode: readMode(ctx),
              context: readContextUsage(ctx),
            })
          );
        },
      }),
    'dsh-plugin-fairy: /api/fairy/meta 路由'
  );
  ctx.effect(
    () =>
      ctx.webServer.register({
        kind: 'exact',
        path: '/api/fairy/task',
        handler: async (req, res) => {
          if (req.method === 'POST') {
            let body = '';
            for await (const chunk of req) body += chunk;
            try {
              const o = JSON.parse(body || '{}');
              if (Number.isFinite(Number(o.total))) TASK.total = Math.max(0, Number(o.total));
              if (Number.isFinite(Number(o.done))) TASK.done = Number(o.done);
              if (typeof o.label === 'string') TASK.label = o.label;
              if (TASK.total === 0) { TASK.done = 0; TASK.label = ''; }
            } catch {                }
          }
          res.writeHead(200, {
            'content-type': 'application/json; charset=utf-8',
            'cache-control': 'no-store',
          });
          res.end(JSON.stringify({ ok: true, total: TASK.total, done: TASK.done, label: TASK.label }));
        },
      }),
    'dsh-plugin-fairy: /api/fairy/task 路由'
  );
  ctx.effect(
    () =>
      ctx.webServer.register({
        kind: 'exact',
        path: '/api/fairy/say',
        handler: async (req, res) => {
          if (req.method !== 'POST') {
            res.writeHead(405, { 'content-type': 'text/plain; charset=utf-8' });
            res.end('method not allowed');
            return;
          }
          let body = '';
          for await (const chunk of req) body += chunk;
          let text = '';
          try {
            text = String(JSON.parse(body || '{}').text || '').trim();
          } catch {
            text = '';
          }
          if (!text) {
            res.writeHead(400, { 'content-type': 'text/plain; charset=utf-8' });
            res.end('text required');
            return;
          }
          try {
            try {
              const r = await fetch(cfg.ttsUrl, {
                method: 'POST',
                headers: { 'content-type': 'application/json' },
                body: JSON.stringify({
                  text,
                  ref_audio: cfg.refAudio,
                  lang: 'zh',
                  emo_alpha: 1.0,
                }),
              });
              if (r.ok) {
                const buf = Buffer.from(await r.arrayBuffer());
                if (buf.length > 500) {
                  res.writeHead(200, {
                    'content-type': 'audio/wav',
                    'content-length': String(buf.length),
                    'cache-control': 'no-store',
                    'x-fairy-voice': 'indextts-clone',
                  });
                  res.end(buf);
                  return;
                }
                console.error('[fairy] IndexTTS 返回过短(' + buf.length + 'B) → 回退 TTS 插件');
              } else {
                console.error('[fairy] IndexTTS HTTP ' + r.status + ' → 回退 TTS 插件');
              }
            } catch (e) {
              console.error('[fairy] IndexTTS 连不上(' + (e && e.message ? e.message : e) + ') → 回退 TTS 插件');
            }
            try {
              const sr = await fetch(cfg.speakUrl, {
                method: 'POST',
                headers: { 'content-type': 'application/json' },
                body: JSON.stringify({ text }),
              });
              if (sr.ok) {
                const j = await sr.json().catch(() => null);
                const u = j && j.url ? String(j.url) : '';
                if (u) {
                  const full = /^https?:/i.test(u)
                    ? u
                    : new URL(cfg.speakUrl).origin + u;
                  const ar = await fetch(full);
                  if (ar.ok) {
                    const ab = Buffer.from(await ar.arrayBuffer());
                    const head = ab.subarray(0, 3).toString('latin1');
                    const isMp3 = head === 'ID3' || (ab[0] === 0xff && (ab[1] & 0xe0) === 0xe0);
                    res.writeHead(200, {
                      'content-type': isMp3 ? 'audio/mpeg' : 'audio/wav',
                      'content-length': String(ab.length),
                      'cache-control': 'no-store',
                      'x-fairy-voice': 'edge-tts-fallback',
                    });
                    res.end(ab);
                    return;
                  }
                }
              }
            } catch (e) {
              console.error('[fairy] 回退 TTS 插件也失败: ' + (e && e.message ? e.message : e));
            }
            res.writeHead(502, { 'content-type': 'text/plain; charset=utf-8' });
            res.end('both tts paths failed (IndexTTS + dsh-plugin-tts)');
          } catch (e) {
            res.writeHead(502, { 'content-type': 'text/plain; charset=utf-8' });
            res.end('tts unreachable: ' + String(e && e.message ? e.message : e));
          }
        },
      }),
    'dsh-plugin-fairy: /api/fairy/say 语音路由'
  );
}
