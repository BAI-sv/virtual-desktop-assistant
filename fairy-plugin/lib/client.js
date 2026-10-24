/**
 * ============================================================================
 * dsh-plugin-fairy —— 浏览器半侧（client half）
 * ============================================================================
 *
 * 【形态】DSH 的浏览器插件必须是这个特殊格式：
 *   window.__ModuleLoader__.load({ id, factory })
 *   - factory 收到一个**同步的 require**，用它拿 React（不能自己打包 React）
 *   - CSS 以字符串内联注入 <style>
 *   - 导出 Cordis 插件三件套：name / inject / apply
 *   （照 dsh-whale-girl-pet 的等价手写实现，零构建依赖）
 *
 * 【这一版做出来的东西（M1）】
 *   1. 悬浮球：Fairy 的 122 帧素材（逐像素透明），33fps 播放 —— 外形是 Fairy
 *   2. 可拖动：按住拖走，位置存 localStorage，下次还在原处
 *   3. 进度 HUD：球旁边显示百分比环形 + 当前状态
 *      - 百分比从宿主 /api/fairy/meta 的 progress 取（没有就显示不确定态）
 *      - **running 用宿主的权威字段**，不信本地边沿事件（防卡在"工作中"）
 *   4. 单击球 → 展开/收起 HUD；双击 → 立刻刷新一次状态
 *
 * 【下一版接的】
 *   - progress 的真实来源：宿主订阅工具执行事件，按"第 N 步 / 预估总步数"算
 *   - 语音：把 assistant-stream 的文本 POST 到 /api/fairy/say 播放
 *   - 唤醒词
 * ============================================================================
 */
window.__ModuleLoader__.load({
  id: 'dsh-plugin-fairy',

  factory: (require) => {
    var module = { exports: {} };
    var exports = module.exports;

    // React 由 DSH 外壳提供，这里只取不打包
    const react = require('react');
    const { useEffect, useRef, useState, useCallback } = react;
    // 直接用 createElement，**不依赖 react/jsx-runtime**：
    // 少一个模块依赖，就少一个"注册成功却渲染不出来"的可能。
    const h = react.createElement;

    const name = 'fairy';
    const inject = ['slots'];

    const NS = 'fairy';
    const CSS = `
.fairy-wrap{position:fixed;z-index:2147483000;user-select:none;-webkit-user-select:none;
  touch-action:none;pointer-events:auto}
.fairy-ball{width:100%;height:100%;cursor:grab;
  filter:brightness(.86) saturate(1.08) drop-shadow(0 5px 14px rgba(0,40,110,.30));
  transition:transform .15s ease,filter .2s ease}
.fairy-ball:active{cursor:grabbing;transform:scale(1.06)}
.fairy-ring{position:absolute;inset:-14px;pointer-events:none;opacity:.75}
.fairy-hud{position:absolute;left:calc(100% + 12px);top:6px;min-width:190px;
  padding:9px 11px;border-radius:12px;font:12px/1.5 ui-sans-serif,system-ui,"Segoe UI",sans-serif;
  color:#d3e3f5;background:rgba(4,10,20,.93);border:1px solid rgba(60,110,170,.32);
  backdrop-filter:blur(8px);box-shadow:0 8px 26px rgba(0,0,0,.55)}
.fairy-hud[data-side="left"]{left:auto;right:calc(100% + 12px)}
.fairy-t{font-weight:600;color:#6fa8dc;margin-bottom:5px;display:flex;gap:6px;align-items:center}
.fairy-dot{width:7px;height:7px;border-radius:50%;background:#3d5a75}
.fairy-dot[data-on="1"]{background:#2aa870;box-shadow:0 0 6px rgba(42,168,112,.7)}
.fairy-dot[data-busy="1"]{background:#c8891a;box-shadow:0 0 6px rgba(200,137,26,.7);animation:fairyPulse 1s infinite}
@keyframes fairyPulse{50%{opacity:.35}}
.fairy-bar{height:6px;border-radius:3px;background:rgba(255,255,255,.09);overflow:hidden;margin:5px 0 4px}
.fairy-fill{height:100%;background:linear-gradient(90deg,#2b6fb0,#4e9dc8);transition:width .3s ease}
.fairy-fill[data-indet="1"]{width:38%!important;animation:fairySlide 1.1s ease-in-out infinite}
@keyframes fairySlide{0%{margin-left:-38%}100%{margin-left:100%}}
.fairy-sub{color:#7c93ab;font-size:11px}
`;
    let cssDone = false;
    function ensureCss() {
      if (cssDone || typeof document === 'undefined') return;
      const el = document.createElement('style');
      el.setAttribute('data-fairy', '1');
      el.textContent = CSS;
      document.head.appendChild(el);
      cssDone = true;
    }

    const POS_KEY = 'fairy.ball.pos.v1';
    const HUD_KEY = 'fairy.hud.open.v1';

    // 【调试期固定位置】先钉在左上角的安全坐标。
    // 原因：如果 shell.overlay 的容器带 transform，`position:fixed` 会以**那个容器**
    // 为基准，我按 window.innerWidth 算出来的 left≈1702px 就会跑到容器外被裁掉
    // —— 那正好能解释"槽位注册成功、界面上却完全看不见"。
    // 小坐标在任何容器里都落在可见范围，先确认能看见，再恢复可拖动/靠右。
    const DEBUG_FIXED_POS = { x: 24, y: 96 };

    function loadPos() {
      // 调试期：**不读 localStorage**，确保每次都用那个安全坐标
      // （否则之前存过的旧位置会覆盖它，又变成看不见）
      return { ...DEBUG_FIXED_POS };
    }

    // 正式版恢复这一段（保留在这里便于切换回去）：
    // function loadPos() {
    //   try {
    //     const p = JSON.parse(localStorage.getItem(POS_KEY) || 'null');
    //     if (p && Number.isFinite(p.x) && Number.isFinite(p.y)) return p;
    //   } catch { /* 忽略 */ }
    //   return { x: Math.max(24, (window.innerWidth || 1280) - 320), y: 120 };
    // }

    /** 悬浮球组件：拖动 + 帧动画 + 进度 HUD。 */
    function FairyBall() {
      ensureCss();
      const size = 128;
      const hostRef = useRef(null);
      const dragRef = useRef(null);
      const [pos, setPos] = useState(loadPos);
      const [hudOpen, setHudOpen] = useState(() => {
        try { return localStorage.getItem(HUD_KEY) !== '0'; } catch { return true; }
      });
      const [meta, setMeta] = useState({ frames: 0, running: null, progress: null, fps: 33 });
      const [frame, setFrame] = useState(0);

      // ---- 元信息轮询：running 用宿主的权威字段 ----
      const pull = useCallback(async () => {
        try {
          const r = await fetch('/api/fairy/meta', { cache: 'no-store' });
          if (r.ok) setMeta(await r.json());
        } catch { /* 宿主没起来就先不动 */ }
      }, []);
      useEffect(() => {
        pull();
        const t = setInterval(pull, 900);
        return () => clearInterval(t);
      }, [pull]);

      // ---- 帧动画 ----
      useEffect(() => {
        const n = meta.frames || 122;
        const fps = meta.fps || 33;
        const t = setInterval(() => setFrame((f) => (f + 1) % n), Math.max(16, 1000 / fps));
        return () => clearInterval(t);
      }, [meta.frames, meta.fps]);

      // ---- 拖动：pointer 事件，钳进视口，松手落盘 ----
      const onDown = (e) => {
        // 增量式拖动：存"按下时的指针位置 + 当时的球位置"。
        // 不能用绝对坐标算（e.clientX - pos.x），因为 shell.overlay 的容器带
        // transform 时 left/top 是相对容器的，而 clientX 是相对视口的，会跳。
        dragRef.current = { sx: e.clientX, sy: e.clientY, ox: pos.x, oy: pos.y, moved: false };
        try { e.currentTarget.setPointerCapture?.(e.pointerId); } catch { /* 忽略 */ }
      };
      const onMove = (e) => {
        const d = dragRef.current;
        if (!d) return;
        const nx = d.ox + (e.clientX - d.sx);
        const ny = d.oy + (e.clientY - d.sy);
        if (Math.abs(nx - pos.x) > 3 || Math.abs(ny - pos.y) > 3) d.moved = true;
        setPos({
          x: Math.min(Math.max(0, nx), (window.innerWidth || 1280) - size),
          y: Math.min(Math.max(0, ny), (window.innerHeight || 800) - size),
        });
      };
      const onUp = (e) => {
        const d = dragRef.current;
        dragRef.current = null;
        try { e.currentTarget.releasePointerCapture?.(e.pointerId); } catch { /* 忽略 */ }
        if (!d) return;
        if (d.moved) {
          try { localStorage.setItem(POS_KEY, JSON.stringify(pos)); } catch { /* 忽略 */ }
        } else {
          // 单击：开合 HUD（把状态也存下来）
          setHudOpen((v) => {
            try { localStorage.setItem(HUD_KEY, v ? '0' : '1'); } catch { /* 忽略 */ }
            return !v;
          });
        }
      };

      const running = meta.running;
      const pct = Number.isFinite(meta.progress) ? Math.max(0, Math.min(100, meta.progress)) : null;
      const side = pos.x > (window.innerWidth || 1280) - size - 220 ? 'left' : 'right';
      const file = 'f' + String(frame).padStart(3, '0') + '.png';

      const ball = h('img', {
        className: 'fairy-ball',
        src: '/fairy/ball/' + file,
        draggable: false,
        alt: 'Fairy',
        onPointerDown: onDown,
        onPointerMove: onMove,
        onPointerUp: onUp,
        onPointerCancel: onUp,
        onDoubleClick: pull,
      });

      const ring = h('svg', {
        className: 'fairy-ring',
        viewBox: '0 0 100 100',
        width: size + 28,
        height: size + 28,
      },
        h('circle', { cx: 50, cy: 50, r: 46, fill: 'none', stroke: 'rgba(120,190,255,.18)', strokeWidth: 3 }),
        h('circle', {
          cx: 50, cy: 50, r: 46, fill: 'none',
          stroke: running === false ? 'rgba(90,126,168,.55)' : '#3ba7ff',
          strokeWidth: 3, strokeLinecap: 'round',
          strokeDasharray: (pct == null ? 35 : Math.max(1, pct)) * 2.89 + ' 999',
          transform: 'rotate(-90 50 50)',
        })
      );

      const hud = hudOpen && h('div', { className: 'fairy-hud', 'data-side': side },
        h('div', { className: 'fairy-t' },
          h('span', { className: 'fairy-dot', 'data-on': running === true ? '1' : '0', 'data-busy': running === true ? '1' : '0' }),
          running === true ? 'Fairy 正在干活' : (running === false ? 'Fairy 待机中' : 'Fairy 状态未知')
        ),
        h('div', { className: 'fairy-bar' },
          h('div', {
            className: 'fairy-fill',
            'data-indet': pct == null ? '1' : '0',
            style: { width: (pct == null ? 38 : pct) + '%' },
          })
        ),
        h('div', { className: 'fairy-sub' },
          pct == null
            ? ('已执行 ' + (meta.steps || 0) + ' 步'
               + (meta.lastTool ? ' ｜ 最近：' + meta.lastTool : '')
               + (meta.turnMs ? ' ｜ ' + Math.round(meta.turnMs / 1000) + 's' : ''))
            : ('进度：' + pct + '%')
        ),
        h('div', { className: 'fairy-sub' },
          '原声语料 ' + (meta.voiceCount || 0) + ' 条 ｜ 帧 ' + (meta.frames || 0) + ' ｜ 内核 DSH'
        )
      );

      // 关键样式全部内联：不依赖注入的 <style>，注入失败也能看见球
      const wrapStyle = {
        position: 'fixed', zIndex: 2147483000, pointerEvents: 'auto',
        userSelect: 'none', WebkitUserSelect: 'none', touchAction: 'none',
        left: pos.x + 'px', top: pos.y + 'px', width: size + 'px', height: size + 'px',
      };
      try {
        return h('div', {
          className: 'fairy-wrap',
          ref: hostRef,
          style: wrapStyle,
          title: '按住拖动我；单击开关面板；双击刷新状态',
        }, ring, ball, hud);
      } catch (e) {
        return h('div', { className: 'fairy-wrap', style: wrapStyle },
          h('div', {
            style: { padding: '8px 10px', borderRadius: '10px', color: '#fff',
                     background: 'rgba(190,30,30,.92)', font: '12px/1.4 sans-serif' },
          }, 'Fairy 渲染出错: ' + String((e && e.message) || e)));
      }
    }

    /**
     * 错误边界：渲染抛错时不再"静默什么都不显示"，而是画一个红框把错误写出来。
     * 没有这个，插件"注册成功但看不见"就没法查——这是常见坑。
     */
    class FairyBoundary extends react.Component {
      constructor(props) {
        super(props);
        this.state = { err: null };
      }
      static getDerivedStateFromError(err) {
        return { err };
      }
      componentDidCatch(err) {
        try { console.error('[fairy] 渲染异常:', err); } catch (e) { /* 忽略 */ }
      }
      render() {
        if (this.state.err) {
          return h('div', {
            style: { position: 'fixed', zIndex: 2147483000, left: '24px', top: '120px',
                     pointerEvents: 'auto' },
          }, h('div', {
            style: { padding: '10px 12px', borderRadius: '10px', color: '#fff', maxWidth: '420px',
                     background: 'rgba(190,30,30,.92)', font: '12px/1.5 sans-serif' },
          }, 'Fairy 渲染异常: ' + String((this.state.err && this.state.err.message) || this.state.err)));
        }
        return h(FairyBall, this.props);
      }
    }

    // ★★ 2026-10-18【方案 C 第 2 项：网页球 → 状态小组件】
    //   完整球（175 行、可拖动、放 122 帧素材）和桌面上的真球重复，
    //   所以默认不挂球；改挂一个**只读的小状态块**：组合 / 引擎 / 模型 / 在线与否。
    //   数据来自 /api/fairy/meta 的 combo 字段（宿主读 fairy.json + 实测端口），
    //   每 3 秒刷新一次；点一下可以收起/展开。
    function FairyStatusChip() {
      const [s, setS] = react.useState(null);
      const [open, setOpen] = react.useState(true);
      react.useEffect(() => {
        let alive = true;
        const tick = () => {
          try {
            fetch('/api/fairy/meta', { cache: 'no-store' })
              .then((r) => (r && r.ok ? r.json() : null))
              .then((j) => { if (alive) setS(j && j.combo ? j.combo : null); })
              .catch(() => { if (alive) setS(null); });
          } catch (e) { /* 忽略 */ }
        };
        tick();
        const t = setInterval(tick, 3000);
        return () => { alive = false; clearInterval(t); };
      }, []);
      if (!s) return null;
      const hasEngine = !!(s.backend && s.backend !== 'off');
      // 灯色：主服务不在=红；引擎不在线=橙；在线=绿；游戏模式=灰
      const st = !s.alive ? 'err' : (hasEngine ? (s.up ? 'on' : 'err') : 'idle');
      const dotColor = st === 'on' ? '#2aa870' : st === 'err' ? '#d05a5a' : '#6d7c8c';
      const text = hasEngine
        ? ((s.preset && s.preset !== 'game' ? s.preset + ' · ' : '') + s.backend + (s.port ? ' :' + s.port : ''))
        : '游戏模式（引擎已释放）';
      const box = {
        position: 'fixed', zIndex: 2147483000, right: '14px', bottom: '14px',
        pointerEvents: 'auto', cursor: 'pointer', userSelect: 'none',
        padding: '7px 10px', borderRadius: '10px',
        background: 'rgba(4,10,20,.92)', border: '1px solid rgba(60,110,170,.30)',
        color: '#cfe0f2', font: '11px/1.5 ui-sans-serif,system-ui,sans-serif',
        maxWidth: '320px', boxShadow: '0 6px 20px rgba(0,0,0,.45)',
      };
      const dot = h('span', {
        style: { display: 'inline-block', width: '7px', height: '7px', borderRadius: '50%',
                 background: dotColor, marginRight: '6px', verticalAlign: 'middle' },
      });
      const head = h('div', { style: { fontWeight: 600, color: '#8fb6d9' } },
        dot, 'Fairy', h('span', { style: { opacity: .55, fontWeight: 400, marginLeft: '6px' } },
          open ? '收起 ▾' : '展开 ▸'));
      if (!open) return h('div', { style: box, onClick: () => setOpen(true) }, head);
      return h('div', { style: box, onClick: () => setOpen(false) },
        head,
        h('div', { style: { marginTop: '4px' } }, text),
        h('div', { style: { opacity: .7, marginTop: '2px' } },
          '模型：' + (s.model || '无')),
        h('div', { style: { opacity: .55, marginTop: '2px' } },
          s.alive ? '桌面主服务：在跑' : '桌面主服务：没在跑'));
    }

    // =======================================================================
    // ★★ 2026-10-24【从 Chengzhibense/Fairy-DSH 借鉴的 DSH 官方 DOM 能力表】
    //
    //   来源：fairy-visual/src/client/dom-adapter.js
    //         （作者逐个对照已安装的 DSH 中英文字典核对过，比我们之前靠猜稳）
    //
    //   为什么要这张表：
    //     · 以前要操作 DSH 界面只能猜选择器，或者用 Win32 模拟键盘抢焦点
    //     · 拿到官方语义选择器后，可以【直接操作 DOM】，不抢焦点、不干扰输入
    //     · 特别是 composerTextarea / send 这两个，让"语音填进输入框"有了
    //       不依赖系统键盘的正路
    //
    //   注意：仍然优先用官方语义锚点（data-slot / data-* / aria-label），
    //         不要依赖 DSH 生成的样式类名，否则升级就会坏。
    // =======================================================================
    const DSH_ARIA = {
      sessionTree: ['会话', 'Sessions'],
      newSession: ['新建会话', 'New session', 'New Session'],
      openSidebar: ['打开侧边栏', 'Open sidebar'],
      collapseSidebar: ['收起侧边栏', 'Collapse sidebar'],
      send: ['发送消息', '停止生成', '停止', 'Send message', 'Stop generating', 'Stop'],
      sendOnly: ['发送消息', 'Send message'],
      context: ['上下文', 'Context'],
      command: ['命令', 'Commands', 'Command'],
      access: ['访问模式', 'Access mode'],
      model: ['选择模型', 'Select model'],
      reasoning: ['模型 ', 'Model '],
      workspace: ['选择工作区', 'Choose workspace'],
      toBottom: ['回到底部', 'Back to bottom'],
    };

    /** 拼出 `base[aria-label="中文"],base[aria-label="English"]` 这样的并集选择器。 */
    function ariaSel(key, base, mode) {
      const labels = DSH_ARIA[key] || [];
      const attr = mode === 'prefix' ? 'aria-label^' : 'aria-label';
      return labels.map((t) => base + '[' + attr + '="' + t + '"]').join(',');
    }

    const DSH_SEL = {
      rootSlot: 'body > #root > [data-slot="root"]',
      rootSurface: '[data-slot="root"]',
      sidebar: '[data-slot="sidebar"]',
      conversation: '[data-slot="conversation"]',
      shellOverlay: '[data-slot="shell.overlay"]',
      phaseHero: '[data-phase="hero"]',
      phaseActive: '[data-phase="active"]',
      composerSeat: '[data-composer-seat]',
      composerCard: '[data-composer-card="true"]',
      composerTextarea: 'textarea',
      conversationScroll: '[data-conversation-scroll]',
      inputScroll: '[data-input-scroll]',
      chatFlow: '[data-chat-flow]',
      sessionTree: ariaSel('sessionTree', '[role="tree"]'),
      sessionHeader: '[data-slot="conversation.session.header"]',
      sessionHeaderUtilities: '[data-slot="conversation.session.header.utilities"]',
      send: ariaSel('send', 'button'),
      sendOnly: ariaSel('sendOnly', 'button'),
      newSession: ariaSel('newSession', 'button'),
      model: ariaSel('model', 'button', 'prefix') + ',' + ariaSel('model', 'select', 'prefix'),
      workspace: ariaSel('workspace', 'button'),
      toBottom: ariaSel('toBottom', 'button'),
    };

    /** 找 DSH 的输入框。优先用官方组合锚点，退化到裸 textarea。 */
    function findComposer() {
      const sels = [
        DSH_SEL.composerCard + ' textarea',
        DSH_SEL.inputScroll + ' textarea',
        DSH_SEL.composerTextarea,
      ];
      for (const s of sels) {
        try {
          const el = document.querySelector(s);
          if (el) return el;
        } catch (e) { /* 选择器不合法就跳过 */ }
      }
      return null;
    }

    /**
     * 把文字写进 DSH 输入框。
     *
     * 【为什么要这么绕】DSH 的输入框是 React 受控组件：
     * 直接 `textarea.value = x` 只会改 DOM，React 内部状态没变，
     * 下一次渲染会把值覆盖回去，用户看起来就是"没填上"。
     * 正确做法是拿到原型上的原生 value setter，绕开 React 的拦截写进去，
     * 再派发一个冒泡的 input 事件，让 React 收到变更通知。
     * 这是 React 受控组件的通用注入手法。
     */
    function setComposerText(text, opts) {
      const ta = findComposer();
      if (!ta) return { ok: false, why: '没找到 DSH 输入框' };
      try {
        const proto = ta.tagName === 'TEXTAREA'
          ? HTMLTextAreaElement.prototype
          : HTMLInputElement.prototype;
        const desc = Object.getOwnPropertyDescriptor(proto, 'value');
        if (desc && desc.set) desc.set.call(ta, text);
        else ta.value = text;
        ta.dispatchEvent(new Event('input', { bubbles: true }));
        // 有的实现监听 change，一并补上
        ta.dispatchEvent(new Event('change', { bubbles: true }));
        try { ta.focus(); } catch (e) { /* 焦点拿不到也不影响赋值 */ }
        if (opts && opts.append) {
          // 追加模式：把光标放到末尾
          try {
            const len = String(text).length;
            ta.setSelectionRange(len, len);
          } catch (e) { /* ignore */ }
        }
        return { ok: true, length: String(text).length };
      } catch (e) {
        return { ok: false, why: String((e && e.message) || e) };
      }
    }

    /** 点 DSH 的发送按钮（只点"发送消息"，不碰"停止生成"）。 */
    function clickSend() {
      try {
        const btn = document.querySelector(DSH_SEL.sendOnly);
        if (!btn) return { ok: false, why: '没找到发送按钮' };
        if (btn.disabled) return { ok: false, why: '发送按钮当前不可用' };
        btn.click();
        return { ok: true };
      } catch (e) {
        return { ok: false, why: String((e && e.message) || e) };
      }
    }

    /** 把能力挂到 window 上，方便排查和外部调用。 */
    function installDomBridge() {
      try {
        window.__fairyDom = {
          version: 1,
          selectors: DSH_SEL,
          aria: DSH_ARIA,
          find: findComposer,
          set: setComposerText,
          send: clickSend,
          /** 一次性：填进去 + 可选回车发送 */
          say: function (text, submit) {
            const r = setComposerText(text);
            if (r.ok && submit) {
              // React 需要一帧把状态同步到按钮的 disabled 上
              setTimeout(function () { clickSend(); }, 120);
            }
            return r;
          },
        };
      } catch (e) { /* 没有 window 就算了 */ }
    }

    function apply(ctx) {
      // ★★ 2026-10-24【装上 DOM 桥接】
      //   把 DSH 官方选择器表和"填输入框 / 点发送"的能力挂到 window.__fairyDom，
      //   这样排查时可以在控制台直接试：
      //     __fairyDom.find()              看能不能定位输入框
      //     __fairyDom.say('测试一下')      只填不发
      //     __fairyDom.say('测试一下', true) 填完并发送
      installDomBridge();
      //   桌面上已经有一个真正的 Fairy 球（D:\FAIRY\tools\fairy_ball.py），
      //   所以默认只挂一个**状态小组件**（webChip），不重复画球；
      //   想要网页球才把 webBall 打开。
      //
      //   client 半侧拿不到插件的 config（DSH 客户端配置管线限制），
      //   所以启动时向宿主问一次 /api/fairy/ui-config；
      //   拿不到（宿主没起、接口没注册）就什么都不画 —— 绝不因此报错。
      //
      //   ★ 宿主半必须保留：桌面球的数据全靠它提供的 /api/fairy/meta。
      function mount(comp, id) {
        try {
          ctx.slots.inject('shell.overlay', function* () {
            yield ctx.slots.register(
              { name: 'shell.overlay', id: id, order: 900 },
              comp
            );
          });
        } catch { /* 注册失败不影响 DSH 本身 */ }
      }

      try {
        fetch('/api/fairy/ui-config', { cache: 'no-store' })
          .then((r) => (r && r.ok ? r.json() : null))
          .then((j) => {
            if (!j) return;
            if (j.webBall) mount(FairyBoundary, 'fairy-ball');        // 完整球（默认关）
            if (j.webChip) mount(FairyStatusChip, 'fairy-status-chip'); // 状态小组件（默认开）
          })
          .catch(() => { /* 问不到就不画 */ });
      } catch { /* fetch 都不可用就放弃 */ }
      void ctx; void FairyBoundary; void FairyStatusChip;
    }

    exports.apply = apply;
    exports.inject = inject;
    exports.name = name;
    return module.exports;
  },
});
