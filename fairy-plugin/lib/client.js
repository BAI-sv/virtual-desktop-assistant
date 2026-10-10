window.__ModuleLoader__.load({
  id: 'dsh-plugin-fairy',
  factory: (require) => {
    var module = { exports: {} };
    var exports = module.exports;
    const react = require('react');
    const { useEffect, useRef, useState, useCallback } = react;
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
    const DEBUG_FIXED_POS = { x: 24, y: 96 };
    function loadPos() {
      return { ...DEBUG_FIXED_POS };
    }
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
      const pull = useCallback(async () => {
        try {
          const r = await fetch('/api/fairy/meta', { cache: 'no-store' });
          if (r.ok) setMeta(await r.json());
        } catch {                 }
      }, []);
      useEffect(() => {
        pull();
        const t = setInterval(pull, 900);
        return () => clearInterval(t);
      }, [pull]);
      useEffect(() => {
        const n = meta.frames || 122;
        const fps = meta.fps || 33;
        const t = setInterval(() => setFrame((f) => (f + 1) % n), Math.max(16, 1000 / fps));
        return () => clearInterval(t);
      }, [meta.frames, meta.fps]);
      const onDown = (e) => {
        dragRef.current = { sx: e.clientX, sy: e.clientY, ox: pos.x, oy: pos.y, moved: false };
        try { e.currentTarget.setPointerCapture?.(e.pointerId); } catch {          }
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
        try { e.currentTarget.releasePointerCapture?.(e.pointerId); } catch {          }
        if (!d) return;
        if (d.moved) {
          try { localStorage.setItem(POS_KEY, JSON.stringify(pos)); } catch {          }
        } else {
          setHudOpen((v) => {
            try { localStorage.setItem(HUD_KEY, v ? '0' : '1'); } catch {          }
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
    class FairyBoundary extends react.Component {
      constructor(props) {
        super(props);
        this.state = { err: null };
      }
      static getDerivedStateFromError(err) {
        return { err };
      }
      componentDidCatch(err) {
        try { console.error('[fairy] 渲染异常:', err); } catch (e) {          }
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
    function apply(ctx) {
      void ctx; void FairyBoundary;
    }
    exports.apply = apply;
    exports.inject = inject;
    exports.name = name;
    return module.exports;
  },
});