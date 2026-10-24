# -*- coding: utf-8 -*-
"""FairyBall 的 Mixin 组件（2026-10-23）。

这些类里没有任何新逻辑 —— 方法体从 fairy_ball.py 原样搬过来，
只是把「定义在 FairyBall 里」改成「定义在 Mixin 里，由 FairyBall 继承」。

Mixin 共享同一个 self，所以 self.canvas / self.frames / self.pos 这些
状态仍然是同一个对象，行为不变。
"""

from ball_consts import *

# ★★ 2026-10-24【修·ImageDraw 未定义】
#   L131 用了 ImageDraw.Draw(img) 画面板圆角，但这个模块自己不 import，
#   bind_globals 的 _NEEDED 列表里也没它，于是每帧都刷
#     「进度条渲染失败: name 'ImageDraw' is not defined」
#   PIL 是硬依赖，直接在这里 import 最稳，不依赖注入。
from PIL import Image, ImageChops, ImageDraw, ImageFont
import numpy as np

class WindowMixin:
    """WindowMixin（2026-10-23 从 FairyBall 拆出）"""

    def _load_pos(self):
        # 存档是"球在屏幕上的 x"；启动时 side 默认 right（球在窗口左端），
        # 所以窗口 x 就等于球的 x —— 之后选边逻辑会挪窗口而球不动。
        try:
            d = json.load(open(POS_FILE, encoding="utf-8"))
            return self._clamp(d.get("x", 0), d.get("y", 0))
        except Exception:
            vx, vy, vw, vh = virtual_screen()
            return self._clamp(vx + vw - WIN_W - MARGIN, vy + MARGIN)

    def _clamp(self, x, y, w=WIN_W, h=WIN_H):
        vx, vy, vw, vh = virtual_screen()
        E = _EDGE_PX
        return (max(vx + E, min(int(x), vx + vw - w - E)),
                max(vy + E, min(int(y), vy + vh - h - E)))

    def _popup_pos(self, ww, wh):
        """★所有弹窗的统一位置：球+进度条面板的【正下方】。

        【布局】三种弹窗统一到球+进度条的正下方
        规则：① x 与面板左边缘对齐（受屏幕边界收拢）
              ② y = 面板下沿 + 6；下方放不下就【翻到上方】；上方也放不下就贴顶
              ③ 返回 (x, y, flip) —— flip=True 表示翻到上方了（尖角要反过来）
        """
        vx, vy, vw, vh = virtual_screen()
        px = self.winfo_x()
        # ★ 2026-10-17 右边界收拢：球在屏幕边缘时弹窗不再跑出屏幕右缘
        px = max(vx + _EDGE_PX, min(px, vx + vw - ww - _EDGE_PX))
        py = self.winfo_y() + WIN_H + 6
        flip = False
        if py + wh > vy + vh - _EDGE_PX:          # 下方放不下 -> 翻到面板上方
            py = self.winfo_y() - wh - 6
            flip = True
        if py < vy + _EDGE_PX:                     # 上方也放不下 -> 贴边缘（留 1cm）
            py = vy + _EDGE_PX
            flip = False
        px = max(vx + 16, min(px, vx + vw - ww - 16))   # ★ 2026-10-12：左右收拢 4->16，提示不再贴边
        return px, py, flip

    def _tail_x(self):
        """尖角在气泡内的水平位置 —— 对准球心（初始值，落点定了还会重画）。"""
        off = 0 if self.side == "right" else BAR_W + GAP
        return off + SIZE // 2

    def _ball_center_x(self):
        """球的【屏幕】中心 x —— 重画尖角时要对准它。"""
        off = 0 if self.side == "right" else BAR_W + GAP
        return self.winfo_x() + off + SIZE // 2

    def _ball_screen_x(self):
        """球在**屏幕上**的 x（窗口 x + 球在窗口里的偏移）。位置存档存这个。"""
        return self.winfo_x() + (0 if self.side == "right" else BAR_W + GAP)

    def _save_pos(self):
        # 【审计 P1】原来不带 with、非原子、异常静默 —— 断电/崩溃会写坏存档（球跳回默认位置）。
        # 改成 temp + os.replace（原子），失败要出声。
        try:
            tmp = POS_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"x": self._ball_screen_x(), "y": self.winfo_y()}, f)
            os.replace(tmp, POS_FILE)
        except Exception as _e:
            print("[fairy] 位置存档写入失败: %s: %s" % (type(_e).__name__, _e), flush=True)

    def _draw_bar(self, img, ball_x, running, prog, cpct, doing, crem=None, bal=None, mode="", task_label="", steps=0):
        """右侧面板：进度条 + 百分比 + 在做什么。额度不足时整条变红。

        ★★【深色实底】需求：
          「把球和提示条的背景色改为悬浮框的背景色」「添加背景色是为了能看清」
        【原来】面板区域 alpha=0（全透明）-> 桌面壁纸透上来 -> 字和背景糊在一起。
        【深色实底那一版】铺一块【不透明】的深色圆角板（色值 = _PANEL_BG）+ 硬描边。
        ★★【中间深、外围渐透】需求：
          「进度条的背景色中间深，渐变到外围就透明了」
        【现在】底板是一张【软边圆角矩形】的图（_panel_backing()）：中间 alpha=255、
          贴边 alpha=0、之间 smoothstep 平滑过渡，【没有硬描边、没有硬边界】。
          底板矩形的位置/尺寸、文字坐标、进度条轨道【一行没动】。
        【位置】没有动：板子就铺在原来的文字/进度条范围内，弹窗定位逻辑一行没改。
        """
        # ★ 面板底板 = 【中间深、外围渐透】的软边圆角矩形（见 _panel_backing）。
        #   范围：包住标题行到硬件条下方，左侧留 16px 给文字（x0 是文字左边界）。
        w = BAR_W - 24          # ★ 2026-10-19 左右各留 12px 内边距
        x0 = (ball_x + SIZE + GAP) if self.side == "right" else (ball_x - GAP - w)
        # ★ 2026-10-10 晚：状态行并进标题行后，少了一行 -> 底板下缘跟着收到 100
        #   （原来 WIN_H-10=118），整块面板更紧凑，上下留白也匀。
        # ★★ 2026-10-19：窗口加高 26px 后温度独立成行，底板必须同步往下延伸，
        #   否则温度行会掉到深色底板外面（实测截图：GPU/CPU 温度直接糊在壁纸上）。
        #   WIN_H-28 -> WIN_H-8，多包住 20px 给温度行用。
        _pb_y1, _pb_y2 = 6, WIN_H - 1   # ★ 2026-10-19 底板贴到窗口最底，不再悬空 6px
        _px0, _px1 = x0 - 12, x0 + w + 12  # ★ 底板四周一律离内容 12px
        # ★ 2026-10-10球在左边（side=right）时球箱是 [0,128]，
        #   x0 只有 136 -> 左边界 120 -> 底板会压掉球自己最右边 8px（辉光被切）。
        #   所以球在左时，左边界取 max(x0-16, 球右缘+2)，永远不吃到球身上。
        #
        # ★★ 2026-10-10【】上一版这里【无条件】取 max(x0-16, ball_x+130)：
        #   球在【右边】时（side=left，球贴屏幕右缘时的常见状态）ball_x=216，
        #   _px0 被夹到 346 > 窗口宽 344 -> 整块底板画到窗口外，
        #   实测（本轮开头的桌面截图）= 面板【完全没有底色】，字直接糊在游戏画面上。
        #   现在按"底板永远在球的反侧"分别夹：球在左 -> 左边界让开球；
        #   球在右 -> 右边界让开球。底板宽度公式、纵向范围、所有文字坐标【没动】。
        if self.side == "right":
            _px0 = max(_px0, int(ball_x + SIZE + 2))
        else:
            _px1 = min(_px1, int(ball_x - 2))
        # ⚠ 光取 max 还不够 —— 极端情况下
        #   _px0 可能超过右边界 x0+w，于是画底板抛 "x1 must be greater than or
        #   equal to x0"，每一帧都抛（33fps 刷屏）。所以右边也兜一下，最小留 8px。
        _px1 = max(_px1, _px0 + 8)
        _bw = int(round(_px1 - _px0))
        _bh = int(round(_pb_y2 - _pb_y1))
        _plate = None
        if _bw > 0 and _bh > 0:
            # ★ 用 alpha_composite（真实 source-over）：paste(x, x) 会把 alpha
            #   再乘一遍自己（实测 128 -> 64），渐变曲线会被"平方"，边缘陡然变硬。
            _plate = _panel_backing(_bw, _bh)
            img.alpha_composite(_plate, (int(_px0), int(_pb_y1)))
        d = ImageDraw.Draw(img)
        # ★★ 2026-10-10【】轨道不再直接写像素，改成由 _panel_bar 叠在
        #   底板上（source-over，不覆盖底板的 alpha）—— 理由见 _panel_bar 的说明。
        def _bar_rect(x1, y1, x2, y2, r, fill, fuse=True):
            """轨道/填充的圆角矩形：fuse=True -> 叠在底板上；False -> 老做法直接写像素
            （填充是不透明的，本来就没有"打穿"问题 —— 见 _PANEL_FUSE_FILL 的说明）。"""
            if fuse and _plate is not None:
                _panel_bar(img, _plate, _px0, _pb_y1, (x1, y1, x2, y2), r, fill)
            else:
                d.rounded_rectangle([x1, y1, x2, y2], radius=r, fill=fill)

        y0 = 22
        # ★★【把正在执行命令这一行放在第 12 步那一行，
        #    变为 正在执行命令第X步」）—— 两行并成一行：
        #    标题行 = 【在做什么】+【第 N 步】；上下文告警也并进来（并变橙红）。
        #    中间用两个空格分隔，读起来是两段信息，不会糊成一串。
        low = isinstance(cpct, (int, float)) and cpct >= 85
        _parts = []
        if task_label:
            _parts.append(task_label)
        elif doing:
            _parts.append(doing)
        elif running:
            _parts.append("正在执行")
        else:
            _parts.append("待机")
        if steps and steps > 0:
            _parts.append("第 %d 步" % steps)
        if low:
            _parts.append("上下文 %d%% 快满了" % int(cpct))
        _title = "  ".join(_parts)
        _tcol = (226, 86, 58, 255) if low else (170, 206, 240, 255)
        # 标题行右侧显示【自动判断的执行方式】：串行 / 多代理 N / 后台 N / 组合
        # 不是串行时用亮色，一眼能看出"正在多线干活"
        _mw = 0
        if mode:
            mt = mode
            col2 = (96, 200, 255, 255) if mode != "串行" else (150, 176, 202, 255)
            try:
                _mw = d.textlength(mt, font=self.f_sm)
            except Exception:
                _mw = 60
            _ptext(d, (x0 + w - _mw - _PANEL_RPAD, y0 - 4), mt, self.f_sm, col2)
        if _title:
            # ★ 按【可用像素宽】截断（不再写死 14 个字）：右边要给"方式"留位置
            _avail = max(40.0, float(w - _PANEL_RINSET) - _mw - 10)
            try:
                if d.textlength(_title, font=self.f_sm) > _avail:
                    _t = _title
                    while _t and d.textlength(_t + "…", font=self.f_sm) > _avail:
                        _t = _t[:-1]
                    _title = (_t + "…") if _t else ""
            except Exception:
                _title = _title[:14]
            if _title:
                _ptext(d, (x0, y0 - 4), _title, self.f_sm, _tcol)
        # low 已在标题行那段算过（上下文 >=85% -> 橙红）
        col = (226, 86, 58, 255) if low else ((74, 170, 235, 255) if running else (92, 124, 152, 255))
        # 轨道（★ 深色底上要提亮：原来 28 是给"透壁纸"看的，实底上太暗会看不见）
        # ★ 2026-10-10【】轨道改成叠在底板上（fuse=True）：
        #   不再把底板该处的 alpha 覆盖成 46（那正是"打穿"）。
        ty = y0 + 20
        # ★ 条子实际画多宽：内容右边内收 _PANEL_RINSET（见该常量的说明）——
        #   目的是让底板左右两边露出的底色一样长（第三轮要求）。
        bw = max(20, int(w - 56))     # ★ 2026-10-19 留 56px 给右侧百分比
        _bar_rect(x0, ty, x0 + bw, ty + 12, 6, (255, 255, 255, 46), True)
        if prog is None:
            if running:                       # 不确定态：一段来回滑
                seg = int(bw * 0.34)
                off = int((self.spin * 7) % max(1, bw - seg))
                _bar_rect(x0 + off, ty, x0 + off + seg, ty + 12, 6, col, _PANEL_FUSE_FILL)
                self.spin += 1
        else:
            fw = max(3, int(bw * max(0.0, min(100.0, float(prog))) / 100.0))
            _bar_rect(x0, ty, x0 + fw, ty + 12, 6, col, _PANEL_FUSE_FILL)
        # 百分比（贴主条右端，与主条垂直居中）
        # ★★ 2026-10-19【用户要求「比例更自然协调」】
        #   原来是 24px 大字、放在标题行右侧（y0+6），和标题挤成一片。
        #   现在降到 13px、贴主条右端，和下面几行标签同字号、同基线。
        pct_txt = "--" if (prog is None and not running) else (
            "…" if prog is None else ("%d%%" % int(prog)))
        _ptext(d, (x0 + bw + 6, ty + 1), pct_txt, self.f_sm_b, col)
        # ★ 2026-10-10 晚：原来的「状态行（正在执行命令）」和「上下文快满了」
        #   两行【已并进标题行】（要求"正在执行命令第X步"放一行）——
        #   这里只留个记号：别再把它们画回来，否则标题行会重复一遍。
        # 余额已移到标题行右端，这里不再重复显示
        # ★【2026-10-10】面板底部再加一条：内存 / 显存 / CPU（三个小进度条并排）
        self._draw_hw(d, x0, w, _bar_rect)

    def _draw_hw(self, d, x0, w, rrect=None):
        """面板底部一行：内存 / 显存 / CPU 三个小条并排。

        风格刻意和主条一致：同一个字体 f_sm、同一个蓝色系、圆角按高度等比。
        数据来自 HwMonitor 的快照（后台线程采的），这里【只画不查】。
        取不到的那条显示 --，并且不画填充 —— 宁可空着也不编数字。
        ★ 2026-10-10 新增 rrect：把轨道/填充叠在底板上的回调（同主条的做法）。
          不传就还是直接写像素（旧行为）。
        """
        hw = getattr(self, "hw", None)
        snap = hw.snap() if hw is not None else None
        if not snap:
            return
        gap = 6
        cw = int((w - gap * 2) / 3)
        # ★ 2026-10-10 晚（状态行并进标题行后，空出一行）：硬件行再上移，
        #   现在就紧跟在进度条下面，底板下缘同步收到 WIN_H-28=100。
        ly = WIN_H - 78                  # 行1 标签 (61)
        by = WIN_H - 58                  # 行1 条 (81，标签下 7px)
        bh = 7
        # ★★ 2026-10-19【用户要求「显示不出来就变两行」】
        #   上一版把温度拼在「显 46%30°」后面，但 1/3 列宽装不下，
        #   实测被隔壁标签压成「显 48% 3(」。现在窗口加高 26px，
        #   温度单独占一行，标签就能恢复全称、字号也不用缩。
        cells = (("内存", snap.get("mem_pct")),
                 ("显存", snap.get("vram_pct")),
                 ("CPU", snap.get("cpu_pct")))
        for i, (name, pct) in enumerate(cells):
            cx = x0 + i * (cw + gap)
            if isinstance(pct, (int, float)):
                pct = max(0.0, min(100.0, float(pct)))
                ipct = int(round(pct))          # ★ 先取整再配色：免得显示 90% 而颜色还停在橙
                txt = "%s %d%%" % (name, ipct)
                col = _hw_color(ipct)
                # ★ 标签一律用亮色 + 粗体（要求更明显）；
                #   只有【快满】时才跟着数字一起变橙/红，那是警告语义，保留。
                lab = col if ipct >= 90 else (234, 245, 255, 255)
            else:
                txt = "%s --" % name
                ipct = None
                col = None
                lab = (168, 186, 208, 255)
            _ptext(d, (cx, ly), txt, self.f_sm_b, lab)
            # 轨道（★ 深色实底上提亮，理由同主条）
            # ★ 2026-10-10：走 rrect -> 叠在底板上（不再把底板 alpha 覆盖成 46）
            if rrect is not None:
                rrect(cx, by, cx + cw, by + bh, 3, (255, 255, 255, 46), True)
            else:
                d.rounded_rectangle([cx, by, cx + cw, by + bh], radius=3,
                                    fill=(255, 255, 255, 46))
            if col is not None:
                fw = max(2, int(cw * ipct / 100.0))
                if rrect is not None:
                    rrect(cx, by, cx + fw, by + bh, 3, col, _PANEL_FUSE_FILL)
                else:
                    d.rounded_rectangle([cx, by, cx + fw, by + bh], radius=3, fill=col)

        # ★★ 2026-10-19：温度独立一行（窗口已加高 26px，不会挤到上面）
        #   ★ 2026-10-19 晚：用户要求「温度的实时提示条」——
        #     光有数字不够，还要像上面三条一样有进度条。
        #     温度按 0~100℃ 映射成百分比宽度，配色沿用 _hw_color
        #     （<60 绿蓝 / 60~85 黄橙 / >85 红），85℃ 是 5800X3D 温度墙前的一档预警。
        _gt = snap.get("gpu_temp")
        _ct = snap.get("cpu_temp")
        _gtxt = "GPU %s°" % (int(round(_gt)) if isinstance(_gt, (int, float)) else "--")
        _ctxt = "CPU %s°" % (int(round(_ct)) if isinstance(_ct, (int, float)) else "--")
        _ty = WIN_H - 44                 # 行2 标签 (95)
        _tby = WIN_H - 24                # 行2 条 (115，标签下 7px)
        _tbh = 7                          # 与行1 的小条等高，保持行高一致
        # 温度超过 85 才变橙（5800X3D 的温度墙是 89，RX 9070 XT 是 90 多）
        _gcol = (255, 170, 80, 255) if (isinstance(_gt, (int, float)) and _gt >= 85) else (180, 220, 255, 255)
        _ccol = (255, 170, 80, 255) if (isinstance(_ct, (int, float)) and _ct >= 85) else (180, 220, 255, 255)

        # ---- GPU 温度条 / CPU 温度条（各占一半宽）----
        _tgap = 10
        _tcw = int((w - _tgap) / 2)
        # ★★ 2026-10-19【用户要求「GPU/CPU 温度要在进度条中间」】
        #   原来是文字从 x0 左对齐、条也从 x0 开始，但文字只有 70~80px 宽、
        #   条有 ~100px，看着就是「字缩在条的左边」。现在改成
        #   **每段文字都居中放在它自己那条的正上方** —— 一眼对应得清楚。
        for _idx, (_tv, _tcolor, _ttxt) in enumerate(
                ((_gt, _gcol, _gtxt), (_ct, _ccol, _ctxt))):
            _tx = x0 + _idx * (_tcw + _tgap)
            # 文字居中在条的正上方
            try:
                _tw2 = d.textlength(_ttxt, font=self.f_sm_b)
            except Exception:
                _tw2 = 60
            _ptext(d, (_tx + (_tcw - _tw2) / 2.0, _ty), _ttxt, self.f_sm_b, _tcolor)
            # 轨道
            if rrect is not None:
                rrect(_tx, _tby, _tx + _tcw, _tby + _tbh, 3, (255, 255, 255, 46), True)
            else:
                d.rounded_rectangle([_tx, _tby, _tx + _tcw, _tby + _tbh], radius=3,
                                    fill=(255, 255, 255, 46))
            # 填充：温度按 0~100 映射
            if isinstance(_tv, (int, float)):
                _tpct = max(0.0, min(100.0, float(_tv)))
                _tfw = max(2, int(_tcw * _tpct / 100.0))
                _tfill = _hw_color(int(round(_tpct)))
                if rrect is not None:
                    rrect(_tx, _tby, _tx + _tfw, _tby + _tbh, 3, _tfill, _PANEL_FUSE_FILL)
                else:
                    d.rounded_rectangle([_tx, _tby, _tx + _tfw, _tby + _tbh], radius=3,
                                        fill=_tfill)

    def _open_panel(self):
        """★ 2026-10-11【Fairy 控制面板】入口：异常落盘日志。"""
        try:
            self._open_panel_impl()
        except Exception as _e:
            import traceback
            try:
                _h = io.open(r"D:\FAIRY\logs\panel_err.log", "a", encoding="utf-8")
                _h.write("%s\n%s\n" % (_e, traceback.format_exc()))
                _h.close()
            except Exception:
                pass

    def _open_panel_impl(self):
        """★ 2026-10-11【Fairy 控制面板】左侧圆角导航 + 右侧圆角按钮长页。"""
        win = tk.Toplevel(self)
        win.title("")
        win.attributes("-topmost", True)
        win.overrideredirect(True)
        win.configure(bg="#ffffff")
        win.resizable(False, False)
        # 固定屏幕中央（首次设置 + 窗口显示后再校准一次）
        def _center():
            _w, _h = 860, 600
            _sw = win.winfo_screenwidth()
            _sh = win.winfo_screenheight()
            win.geometry("%dx%d+%d+%d" % (_w, _h,
                                          max(0, (_sw - _w) // 2),
                                          max(0, (_sh - _h) // 2)))
        _center()
        win.after(120, _center)
        # —— 顶部标题栏：无系统标题栏，这里放关闭/退出按钮 ——
        tbar = tk.Frame(win, bg="#f2f3f5")
        tbar.pack(side="top", fill="x")

        def _tbtn(_txt, _fg, _bg, _bg_h, _cmd):
            _im = _pil_round(_txt, 86, 30, 10, _bg, _fg, True, "#dfe3ea")[0]
            _imh = _pil_round(_txt, 86, 30, 10, _bg_h, _fg, True, "#dfe3ea")[0]
            _b = tk.Button(tbar, image=_im, bd=0, relief="flat",
                           highlightthickness=0, bg="#f2f3f5", activebackground="#f2f3f5",
                           cursor="hand2", command=_cmd)
            _b.image = _im
            _b.bind("<Enter>", lambda _ev, b=_b, i=_imh: b.configure(image=i))
            _b.bind("<Leave>", lambda _ev, b=_b, i=_im: b.configure(image=i))
            return _b
        _tbtn("退出 Fairy", "#a8625e", "#ffffff", "#f4e6e4", self._quit_all).pack(
            side="right", padx=(0, 6), pady=8)
        _tbtn("关闭面板", "#46536a", "#ffffff", "#eef1f6", win.destroy).pack(
            side="right", padx=(0, 2), pady=8)
        # —— 右栏（占满）：长页滚动 ——
        body = tk.Frame(win, bg="#ffffff")
        body.pack(fill="both", expand=True)
        _cv = tk.Canvas(body, bg="#ffffff", highlightthickness=0)
        _sb = tk.Scrollbar(body, orient="vertical", command=_cv.yview)
        _cv.configure(yscrollcommand=_sb.set)
        _sb.pack(side="right", fill="y")
        _cv.pack(side="left", fill="both", expand=True)
        _inner = tk.Frame(_cv, bg="#ffffff")
        _wid = _cv.create_window((18, 6), window=_inner, anchor="nw")
        # ★ 2026-10-12【滚动条自动伸缩】展开/收起/内容增长时内层尺寸一变，
        #   滚动区立即重算 -> 滚动条自己伸长/缩短。
        _inner.bind("<Configure>", lambda _e: _cv.configure(
            scrollregion=_cv.bbox("all")))
        # ★ 无标题栏 -> 按住空白区域拖动窗口
        _drag = {"x": 0, "y": 0, "on": False}

        def _press(_e):
            _drag.update(x=_e.x_root, y=_e.y_root, on=True)

        def _move(_e):
            if _drag["on"]:
                vx, vy, vw, vh = virtual_screen()
                _E = _EDGE_PX
                _w, _h = win.winfo_width(), win.winfo_height()
                _nx = win.winfo_x() + _e.x_root - _drag["x"]
                _ny = win.winfo_y() + _e.y_root - _drag["y"]
                win.geometry("+%d+%d" % (max(vx + _E, min(_nx, vx + vw - _w - _E)),
                                         max(vy + _E, min(_ny, vy + vh - _h - _E))))
                _drag.update(x=_e.x_root, y=_e.y_root)

        def _rel(_e):
            _drag["on"] = False

        for _w in (body, _cv, _inner, tbar):
            try:
                _w.bind("<Button-1>", _press)
                _w.bind("<B1-Motion>", _move)
                _w.bind("<ButtonRelease-1>", _rel)
            except Exception:
                pass

        def _fit(_e):
            _cv.itemconfigure(_wid, width=max(100, _cv.winfo_width() - 36))
            _cv.configure(scrollregion=_cv.bbox("all"))
        _cv.bind("<Configure>", _fit)

        def _row(parent, title, items):
            if title:
                tk.Label(parent, text=title, bg="#ffffff", fg="#66727f",
                         font=("SimHei", 12, "bold")).pack(pady=(7, 3))
            r = tk.Frame(parent, bg="#ffffff")
            r.pack(fill="x")
            _imgs = [(_pil_round(_l, 100, 34, 10, "#ffffff", "#46536a", True)[0], _l, _c)
                     for _l, _c in items]
            _cur = tk.Frame(r, bg="#ffffff")
            _cur.pack()
            _w = 0
            for _im, _l, _c in _imgs:
                _bw = _im.width() + 10
                if _w > 0 and _w + _bw > 520:
                    _cur = tk.Frame(r, bg="#ffffff")
                    _cur.pack()
                    _w = 0
                _b = tk.Button(_cur, image=_im, command=_c, bd=0, relief="flat",
                               highlightthickness=0, bg="#ffffff",
                               activebackground="#ffffff", cursor="hand2")
                _b.image = _im
                _b.pack(side="left", padx=4, pady=3)
                _w += _bw

        def _exp_row(parent, title, build):
            """★ 2026-10-12【内嵌展开】点击标题行 -> 下方展开内容区，再点收起（替代子窗口）。"""
            _body = tk.Frame(parent, bg="#f2f5f8")
            _open = {"v": False}
            # ★ 2026-10-12【箭头符号修复】▾/▸ 是中文字体没有的字形，PIL 渲染成 ×；
            #   改用全角 ＋（收起）/ －（展开），所有中文字体都支持。
            _img_on = _pil_round(title + "  －", 300, 34, 10, "#ffffff", "#5f7ea5", True)[0]
            _img_off = _pil_round(title + "  ＋", 300, 34, 10, "#ffffff", "#5f7ea5", True)[0]

            def _refresh():
                try:
                    _fit(None)   # ★ 2026-10-12：展开/收起后同步内层宽度 + 滚动区
                    _cv.configure(scrollregion=_cv.bbox("all"))
                    # ★ 2026-10-12【展开自动定位】展开/收起后把滚动区定位到
                    #   该项附近，防止内容生成在视口外 -> 看起来"没内容"。
                    try:
                        _bb = _cv.bbox("all")
                        _rh = _bb[3] if _bb else 0
                        _vh = _cv.winfo_height()
                        if _rh > _vh:
                            _top = (_body.winfo_y() - 10) / float(_rh)
                            _cv.yview_moveto(max(0.0, min(1.0, _top)))
                    except Exception:
                        pass
                except Exception:
                    pass

            def _sync_body(_e=None):
                # ★ 2026-10-12【宽度跟随】父区块宽度一变，灰底展开区立即跟宽，
                #   内容 pack(anchor=center) 随之重新居中。
                try:
                    _body.configure(width=parent.winfo_width())
                except Exception:
                    pass
            parent.bind("<Configure>", _sync_body, add=True)

            def _toggle():
                """展开/收起。顶层包日志：任何异常都落盘，避免"点击无反应"。"""
                try:
                    _open["v"] = not _open["v"]
                    _b.configure(image=_img_on if _open["v"] else _img_off)
                    if _open["v"]:
                        if not _body.winfo_children():
                            try:
                                build(_body)
                            except Exception as _e:
                                # ★ 2026-10-12【展开诊断】异常既显示在展开区，也落盘
                                #   panel_err.log，便于定位"展开打不开"。
                                import traceback as _tb
                                try:
                                    _h = io.open(r"D:\FAIRY\logs\panel_err.log",
                                                 "a", encoding="utf-8")
                                    _h.write("EXPAND %s\n%s\n" % (
                                        title, _tb.format_exc()))
                                    _h.close()
                                except Exception:
                                    pass
                                tk.Label(_body, text="加载失败：" + str(_e)[:50],
                                         bg="#f2f5f8", fg="#a8625e",
                                         font=("SimHei", 11)).pack(pady=4)
                        # ★ 2026-10-12【空白兜底】build 后仍无任何子控件 ->
                        #   显示占位提示，保证展开区永远不会是一片空白。
                        if not _body.winfo_children():
                            tk.Label(_body, text="（此区域暂无内容）",
                                     bg="#f2f5f8", fg="#97a1ad",
                                     font=("SimHei", 11)).pack(pady=6)
                        # ★ 2026-10-12【展开空白修复】Tk pack 的请求高度在
                        #   build 后可能塌陷为 1px（子控件 reqh 正常但父 frame
                        #   不汇总）-> 强制按子控件请求高度总和撑开展开区。
                        try:
                            _body.update_idletasks()
                            _total = sum(
                                _c.winfo_reqheight()
                                for _c in _body.winfo_children()) + 40
                            _body.configure(height=_total)
                            try:
                                _body.configure(
                                    width=max(_body.winfo_width(),
                                              parent.winfo_width()))
                            except Exception:
                                pass
                        except Exception:
                            pass
                    else:
                        # ★ 2026-10-12【收起清空】仅收起时销毁内容；
                        #   修复：此前 else 误挂在诊断 try 上，导致每次展开
                        #   刚 build 完就被清空 -> 展开区永远空白。
                        for _c in _body.winfo_children():
                            _c.destroy()
                        # ★ 2026-10-12【收起高度复位】展开时曾显式撑高 _body，
                        #   收起时必须把高度重置，否则空白面板残留、收不回去。
                        try:
                            _body.configure(height=1)
                        except Exception:
                            pass
                    # ★ 2026-10-12【展开诊断】每次展开/收起记录控件数，便于定位
                    #   "展开没内容"到底是 build 没跑还是渲染不可见。
                    try:
                        _h = io.open(r"D:\FAIRY\logs\panel_err.log",
                                     "a", encoding="utf-8")
                        _h.write("EXPAND_OK %s open=%d children=%d bh=%d par=%s\n" % (
                            title, 1 if _open["v"] else 0,
                            len(_body.winfo_children()),
                            _body.winfo_height(), str(_body.master())[:50]))
                        _h.close()
                    except Exception:
                        pass
                    _refresh()
                except Exception as _e:
                    import traceback as _tb
                    try:
                        _h = io.open(r"D:\FAIRY\logs\panel_err.log",
                                     "a", encoding="utf-8")
                        _h.write("TOGGLE %s\n%s\n" % (title, _tb.format_exc()))
                        _h.close()
                    except Exception:
                        pass

            _b = tk.Button(parent, image=_img_off, command=_toggle, bd=0, relief="flat",
                           highlightthickness=0, bg="#ffffff", activebackground="#ffffff",
                           cursor="hand2")
            _b.image = _img_off
            _b.pack(pady=(3, 0))
            # ★ 2026-10-12【位置修复】内容区创建时就 pack 占位（紧跟标题按钮下方），
            #   展开/收起只控制内容填充与清空，位置不再跑到区块末尾。
            _body.pack(fill="x", pady=(2, 6))

        blocks = {}

        def _block(title, desc=""):
            f = tk.Frame(_inner, bg="#ffffff")
            _h = tk.Frame(f, bg="#ffffff")
            _h.pack(fill="x")
            tk.Frame(_h, bg="#5f7ea5", width=4, height=18).pack(side="left", padx=(8, 6))
            tk.Label(_h, text=title, bg="#ffffff", fg="#5f7ea5",
                     font=("SimHei", 17, "bold")).pack(side="left")
            tk.Frame(f, bg="#e8ecf2", height=1).pack(fill="x", pady=(4, 2))
            f.pack(fill="x")
            blocks[title] = f
            return f

        def _mk2(_parent, _maker, _toggle, _pady=(4, 2)):
            """★ 2026-10-12【两态按钮】点击切换状态并重建文字图片（始终显示当前状态）。"""
            _b = tk.Button(_parent, bd=0, relief="flat", highlightthickness=0,
                           bg="#ffffff", activebackground="#ffffff", cursor="hand2")

            def _upd():
                _img = _maker()
                _b.config(image=_img)
                _b.image = _img
            _b.config(command=lambda: (_toggle(), _upd()))
            _upd()
            _b.pack(pady=_pady)
            return _b

        # 2 AI 模型
        b2 = _block("AI 模型")
        # ★ 2026-10-18【已彻底移除】「屏幕灯效」整块随 fairy_rim.py 一起删掉了
        #   （用户：「屏幕rgb删掉吧，不要了」）。这里不再保留注释掉的恢复代码，
        #   免得以后有人照着注释去找一个已经不存在的文件和函数。
        _ai = self._ai_cfg()
        _be = _ai.get("backend") or "strata"
        _m = _ai.get("model") or ""
        _pt = _ai.get("port") or ""
        def _b_engine(f):
            _bg2 = "#f2f5f8"
            tk.Label(f, text="引擎类型", bg=_bg2, fg="#66727f",
                     font=("SimHei", 12, "bold")).pack(anchor="center", pady=(6, 2))
            _ev = tk.StringVar(value=_be)
            _PRESET_MODELS = {
                "strata": ["Qwen3.8-Flash-Next\u00b7UD-IQ4_XS",
                           "Qwen3.8-Flash-Next\u00b7Q4_K_M",
                           "Qwen3.8-Flash-Next\u00b7Q5_K_M",
                           "Qwen3.8-Flash-Next\u00b7Q6_K"],
                "ollama": ["qwen3-30b-a3b", "qwen3.8-27b", "qwen3.5-9b"],
                "llm": ["Qwen3-VL-8B-Q4_K_M", "Qwen3-VL-4B-Q4_K_M",
                        "Qwen2.5-VL-7B-Q4_K_M"],
            }
            _om_box = {"w": None}
            _mv = tk.StringVar()

            def _rebuild_pool():
                """★ 2026-10-12【模型列表兜底】选引擎即重建模型下拉：
                先渲染【常用模型全集 + 本机磁盘 gguf】（任何引擎/游戏模式都有真实选项），
                后台线程再探测正在运行的引擎模型并刷新 —— 不再出现"展开没内容"，
                也不卡主线程（探测/扫盘都在后台）。"""
                try:
                    if _om_box["w"] is not None:
                        try:
                            _om_box["w"].destroy()
                        except Exception:
                            pass
                    _be2 = _ev.get()
                    # ① 常用模型全集兜底：所有引擎预设合并，off/游戏模式也有项
                    _ALL = []
                    for _v in _PRESET_MODELS.values():
                        for _x in _v:
                            if _x not in _ALL:
                                _ALL.append(_x)
                    _pool = list(_ALL)
                    # ② 本机磁盘 gguf（扫描可能慢，放后台，先不阻塞渲染）
                    def _bg():
                        _extra = []
                        try:
                            import capability as _cap
                            _mo = _cap.scan_models() or {}
                            for _f in (_mo.get("gguf_files") or [])[:30]:
                                _nm = _f.get("name") or ""
                                if _nm:
                                    _extra.append(_nm)
                        except Exception:
                            pass
                        try:
                            _probed = self._ai_probe_models()
                            _have = [x for _, x in _probed]
                        except Exception:
                            _have = []
                        if not _extra and not _have:
                            return
                        def _upd():
                            try:
                                # ★ 2026-10-12【销毁防御】后台探测完成时展开区
                                #   可能已被收起（OptionMenu 已销毁）-> 静默退出，
                                #   绝不抛 TclError 打断主循环。
                                if _om_box["w"] is None:
                                    return
                                try:
                                    if not _om_box["w"].winfo_exists():
                                        return
                                except Exception:
                                    return
                                _pool2 = list(_pool)
                                for _x in (_extra + _have):
                                    if _x not in _pool2:
                                        _pool2.insert(0, _x)
                                _mv2 = tk.StringVar(value=_mv.get())
                                _om_box["w"].destroy()
                                _om_box["w"] = tk.OptionMenu(f, _mv2, *_pool2)
                                _om_box["w"].configure(
                                    bg="#ffffff", activebackground="#e4edf6",
                                    font=("SimHei", 11), highlightthickness=0)
                                _om_box["w"].pack(anchor="center", pady=3)
                                _mv.set(_mv2.get())
                            except Exception:
                                pass
                        try:
                            self.after(0, _upd)
                        except Exception:
                            pass
                    import threading as _th
                    _th.Thread(target=_bg, daemon=True).start()
                    # ③ 先用兜底池立即渲染（保证展开就有内容）
                    if _m and _m not in _pool:
                        _pool.insert(0, _m)
                    _pool = _pool or ["（未探测到模型）"]
                    _mv.set(_m if _m in _pool else _pool[0])
                    _om_box["w"] = tk.OptionMenu(f, _mv, *_pool)
                    _om_box["w"].configure(bg="#ffffff", activebackground="#e4edf6",
                                           font=("SimHei", 11), highlightthickness=0)
                    _om_box["w"].pack(anchor="center", pady=3)
                except Exception:
                    pass

            for _nm, _code in (("Strata（本地运算）", "strata"),
                               ("Ollama", "ollama"),
                               ("视觉 llama.cpp", "llm")):
                tk.Radiobutton(f, text=_nm, value=_code, variable=_ev,
                               command=lambda: (_pv.set(
                                   {"strata": "8080", "ollama": "11434",
                                    "llm": "8085"}.get(_ev.get(), "8080")),
                                   _rebuild_pool()),
                               bg=_bg2, fg="#46536a", activebackground=_bg2,
                               selectcolor=_bg2, font=("SimHei", 12)).pack(anchor="center", pady=1)
            tk.Label(f, text="模型（下拉：本机探测到的 + 常用预设）", bg=_bg2, fg="#66727f",
                     font=("SimHei", 12, "bold")).pack(anchor="center", pady=(6, 2))
            # ★ 2026-10-12【下拉修复】探测结果 + 常用预设兜底，引擎没跑也有下拉可选；
            #   原 _ai_probe_models 返回值解包方式错误导致展开区"加载失败"。
            _rebuild_pool()
            # ★ 2026-10-12【手动输入】引擎没跑时探测不到模型 -> 可直接手填模型名
            tk.Label(f, text="或手动输入模型名（引擎没跑时用这个）", bg=_bg2, fg="#97a1ad",
                     font=("SimHei", 10)).pack(anchor="center", pady=(4, 0))
            _me = tk.StringVar(value="")
            tk.Entry(f, textvariable=_me, width=24, font=("SimHei", 12)
                     ).pack(anchor="center", pady=3)
            tk.Label(f, text="端口（保留默认即可，可自行修改）", bg=_bg2, fg="#66727f",
                     font=("SimHei", 12, "bold")).pack(anchor="center", pady=(6, 2))
            _pv = tk.StringVar(value=_pt or {"strata": "8080", "ollama": "11434",
                                             "llm": "8085"}.get(_be, "8080"))
            tk.Entry(f, textvariable=_pv, width=12, font=("SimHei", 12)
                     ).pack(anchor="center", pady=3)

            def _save_eng():
                _b_ = _ev.get()
                _m_ = (_me.get().strip() or _mv.get())
                if not _m_ or _m_.startswith("（"):
                    _m_ = _m
                _p_ = _pv.get().strip() or {"strata": "8080", "ollama": "11434",
                                            "llm": "8085"}.get(_b_, "8080")
                self._save_ai_cfg({"backend": _b_, "model": _m_, "port": _p_})
                self._bubble("本地引擎已保存：%s / %s / 端口 %s"
                             % (_b_, _m_ or "默认", _p_), 4)
                try:
                    self.say("本地引擎设置已保存", "ok")
                except Exception:
                    pass
            _r0 = tk.Frame(f, bg=_bg2)
            _r0.pack(anchor="center", pady=4)
            self._dlg_btn(_r0, "保存", _save_eng)
        _exp_row(b2, "引擎 / 模型 / 端口", _b_engine)

        def _b_preset(f):
            _bg2 = "#f2f5f8"
            _PRESETS = [
                ("Qwen3.8-Flash-Next·IQ3_S", "strata", "8080",
                 ["Qwen3.8-Flash-Next\u00b7IQ3_S"],
                 "最强：8~11 tok/s，首 token 3~10s，吃 34G 内存 + 15.7G 显存（推荐日常写代码）"),
                ("Qwen3.8-27B·IQ3_XXS", "llm27", "8081",
                 ["qwen3.8-27b"],
                 "IQ3_XXS 全卡：31~37 tok/s，16K 上下文，显存吃满（要快用它）"),
                ("Qwen3-VL-8B", "vl", "8086",
                 ["qwen3-vl-8b"],
                 "看图问答：Qwen3-VL-8B，约 0.6 秒/图"),
                ("Qwen3.5-9B", "llm", "8085",
                 ["qwen3.5-9b"],
                 "9B Q4（5.2G）：冷启最快、最省显存，质量低于上面两个"),
                ("游戏模式", "off", "", [],
                 "释放全部引擎与模型，显存全给游戏；语音/轻功能照常"),
            ]
            _cur_p = (self._ai_cfg().get("preset") or "")
            _ai_now = self._ai_cfg()
            _vars = {}
            _btns = {}
            _imgs = {}

            def _refresh_all():
                # ★ 2026-10-12【实时两态】每次刷新实时读配置，点完立即更新 ✓，
                #   不再等关面板重开。
                _cur_now = (self._ai_cfg().get("preset") or "")
                for _i, (_nm, _eng, _pt2, _models, _desc) in enumerate(_PRESETS):
                    _sel = (_cur_now == _nm) or (_cur_now == "game" and _nm == "游戏模式")
                    _ck = "✓ " if _sel else "  "
                    _imgs[_i] = _pil_round(_ck + _nm, 260, 32, 10, "#ffffff",
                                           "#5f7ea5", True)[0]
                    _btns[_i].config(image=_imgs[_i])
                    _btns[_i].image = _imgs[_i]

            def _apply(idx):
                try:
                    # ★ 2026-10-12【修复】_apply_impl 是本展开区的嵌套函数，
                    #   不是 FairyBall 类方法 —— 之前写成 self._apply_impl 必报
                    #   AttributeError（"'tkinter.tkapp' object has no attribute"）
                    #   ，导致点组合切换永远失败。
                    _apply_impl(idx)
                except Exception as _e:
                    import traceback as _tb
                    try:
                        _h = io.open(r"D:\FAIRY\logs\panel_err.log",
                                     "a", encoding="utf-8")
                        _h.write("APPLY\n%s\n" % _tb.format_exc())
                        _h.close()
                    except Exception:
                        pass
                    try:
                        self._bubble("组合切换失败：" + str(_e)[:50], 5)
                    except Exception:
                        pass

            def _apply_impl(idx):
                # ★ 2026-10-17【消除重复实现】以前这里自己写了一套"清理线程 + 启动线程"的并发逻辑，
                #   与 _apply_preset 重复、且同样有"抢跑播报"的问题。现在统一走 _apply_preset：
                #   停旧引擎 -> 等端口释放 -> 起新引擎 -> 等端口就绪 -> 才播"已就绪"。
                _nm, _eng, _pt2, _models, _desc = _PRESETS[idx]
                _mdl = None
                if _eng != "off":
                    _mdl = _vars.get(idx).get() if _vars.get(idx) else _models[0]
                    if not _mdl or _mdl.startswith("（"):
                        _mdl = _models[0]
                self._apply_preset(_nm, _eng, _pt2, _models, _desc, mdl=_mdl)
                _refresh_all()
            tk.Label(f, text="点组合切换（右侧可先选该组合的模型）",
                     bg=_bg2, fg="#66727f", font=("SimHei", 11)
                     ).pack(anchor="center", pady=(2, 4))
            for _i, (_nm, _eng, _pt2, _models, _desc) in enumerate(_PRESETS):
                _rowf = tk.Frame(f, bg=_bg2)
                _rowf.pack(anchor="center", pady=1)
                _sel = (_cur_p == _nm) or (_cur_p == "game" and _nm == "游戏模式")
                _ck = "✓ " if _sel else "  "
                _imgs[_i] = _pil_round(_ck + _nm, 250, 32, 10, "#ffffff",
                                       "#5f7ea5", True)[0]
                _b = tk.Button(_rowf, image=_imgs[_i], bd=0, relief="flat",
                               highlightthickness=0, bg=_bg2, activebackground=_bg2,
                               cursor="hand2", command=lambda i=_i: _apply(i))
                _b.image = _imgs[_i]
                _b.pack(side="left", padx=2)
                _btns[_i] = _b
                if _models:
                    _cur_mdl = _ai_now.get("model") or _models[0]
                    if _cur_mdl not in _models:
                        _cur_mdl = _models[0]
                    _vars[_i] = tk.StringVar(value=_cur_mdl)
                    tk.OptionMenu(_rowf, _vars[_i], *_models).configure(
                        bg="#ffffff", activebackground="#e4edf6",
                        font=("SimHei", 10), highlightthickness=0)
                tk.Label(f, text="　" + _desc, bg=_bg2, fg="#97a1ad",
                         font=("SimHei", 10)).pack(anchor="center", pady=(0, 2))

        _exp_row(b2, "组合选择", _b_preset)
        # ★ 2026-10-12【DSH 可启动】"DSH 云端"按钮：切到 dsh 大脑模式，
        #   若 DeepSeek Harness（19387）没在跑会自动拉起（见 _set_pref -> _ensure_dsh）
        _row(b2, "网络配置", [(("✓ DSH 云端" if _be == "dsh" else "DSH 云端"),
                            lambda: self._set_pref("dsh"))])

        def _b_online(f):
            """★ 2026-10-12【在线引擎精简】只保留自定义接口（OpenAI 兼容），
            模型栏带常用预设下拉（自动填模型 + 接口地址）。DSH 云端内核入口保留在上方。"""
            _bg2 = "#f2f5f8"
            _ai = self._ai_cfg()
            _PRESETS = [
                ("DeepSeek（deepseek-chat）", "deepseek-chat",
                 "https://api.deepseek.com/v1"),
                ("DeepSeek 推理（deepseek-reasoner）", "deepseek-reasoner",
                 "https://api.deepseek.com/v1"),
                ("智谱 GLM（glm-4-flash 免费）", "glm-4-flash",
                 "https://open.bigmodel.cn/api/paas/v4"),
                ("通义千问（qwen-plus）", "qwen-plus",
                 "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                ("Kimi（moonshot-v1-8k）", "moonshot-v1-8k",
                 "https://api.moonshot.cn/v1"),
                ("硅基流动（Qwen2.5-7B）", "Qwen/Qwen2.5-7B-Instruct",
                 "https://api.siliconflow.cn/v1"),
                ("OpenAI（gpt-4o-mini）", "gpt-4o-mini",
                 "https://api.openai.com/v1"),
            ]
            _m_var = tk.StringVar(value=_ai.get("model") or "deepseek-chat")
            _k_var = tk.StringVar(value=_ai.get("api_key") or "")
            _u_var = tk.StringVar(value=_ai.get("base_url")
                                  or "https://api.deepseek.com/v1")

            tk.Label(f, text="在线引擎：自定义接口（OpenAI 兼容）",
                     bg=_bg2, fg="#66727f", font=("SimHei", 10, "bold")
                     ).pack(anchor="center", pady=(2, 2))
            tk.Label(f, text="常用服务（选中自动填模型与接口地址）",
                     bg=_bg2, fg="#97a1ad", font=("SimHei", 10)
                     ).pack(anchor="center")
            _cur_nm = _PRESETS[0][0]
            for _nm, _m, _u in _PRESETS:
                if _m_var.get() == _m:
                    _cur_nm = _nm
                    break
            _pv2 = tk.StringVar(value=_cur_nm)

            def _on_pre(_sel):
                for _nm, _m, _u in _PRESETS:
                    if _sel == _nm:
                        _m_var.set(_m)
                        _u_var.set(_u)
                        break
            tk.OptionMenu(f, _pv2, *[p[0] for p in _PRESETS],
                          command=_on_pre).configure(
                bg="#ffffff", activebackground="#e4edf6", font=("SimHei", 11),
                highlightthickness=0)
            tk.Label(f, text="模型（可改）", bg=_bg2, fg="#66727f",
                     font=("SimHei", 10)).pack(anchor="center", pady=(6, 1))
            tk.Entry(f, textvariable=_m_var, font=("SimHei", 11),
                     width=30).pack(anchor="center")
            tk.Label(f, text="API Key", bg=_bg2, fg="#66727f",
                     font=("SimHei", 10)).pack(anchor="center", pady=(4, 1))
            tk.Entry(f, textvariable=_k_var, font=("SimHei", 11),
                     width=30).pack(anchor="center")
            tk.Label(f, text="接口地址（选预设自动填，可改）", bg=_bg2, fg="#66727f",
                     font=("SimHei", 10)).pack(anchor="center", pady=(4, 1))
            tk.Entry(f, textvariable=_u_var, font=("SimHei", 11),
                     width=30).pack(anchor="center")

            def _ok():
                _m = _m_var.get().strip()
                _k = _k_var.get().strip()
                _u = _u_var.get().strip()
                self._save_ai_cfg({"backend": "custom", "model": _m,
                                   "api_key": _k, "base_url": _u})
                self._bubble("自定义接口已保存" + ("（%s）" % _m if _m else ""), 3)
            _r = tk.Frame(f, bg=_bg2)
            _r.pack(anchor="center", pady=3)
            self._dlg_btn(_r, "保存", _ok)
        _exp_row(b2, "自定义接口 / 模型 / Key", _b_online)

        # 1 声音
        b1 = _block("声音")

        def _b_voice(f):
            _bg2 = "#f2f5f8"
            try:
                import fairy_voice as _fv2
                _vs2 = _fv2.list_voices() or ["fairy.wav"]
                _cur2 = _fv2.current()
            except Exception:
                _vs2, _cur2 = ["fairy.wav"], ""
            for _vn in _vs2:
                _img = _pil_round(("✓ " if _vn == _cur2 else "  ") + _vn, 280, 34, 10,
                                  "#ffffff", "#46536a", True, "#dfe3ea")[0]
                _b = tk.Button(f, image=_img, bd=0, relief="flat", highlightthickness=0,
                               bg=_bg2, activebackground=_bg2, cursor="hand2",
                               command=(lambda n=_vn: (self._voice_set(n),
                                                       self._bubble("音色已切换：" + n, 3))))
                _b.image = _img
                _b.pack(pady=2)

        def _b_wake(f):
            _bg2 = "#f2f5f8"
            _old = ""
            try:
                with open(fairy_root.CONFIG, encoding="utf-8") as _f:
                    _old = "、".join((json.load(_f).get("ear") or {}).get("wakewords") or [])
            except Exception:
                pass
            tk.Label(f, text="英文词条，如 alice；多个用逗号分隔（保存后重启 Fairy 生效）",
                     bg=_bg2, fg="#66727f", font=("SimHei", 10)).pack(anchor="center", pady=(4, 2))
            _inp = tk.Entry(f, width=28, font=("SimHei", 12))
            _inp.insert(0, _old)
            _inp.pack(anchor="center", pady=2)

            def _ok():
                _words = [w.strip() for w in _inp.get().replace("，", ",").split(",")
                          if w.strip()]
                try:
                    with open(fairy_root.CONFIG, encoding="utf-8") as _f:
                        _j = json.load(_f)
                    _j.setdefault("ear", {})["wakewords"] = _words
                    with open(fairy_root.CONFIG, "w", encoding="utf-8") as _f:
                        json.dump(_j, _f, ensure_ascii=False, indent=2)
                    self._bubble("已保存唤醒词（重启 Fairy 生效）", 4)
                except Exception as _e:
                    self._bubble("保存失败：" + str(_e)[:50], 4)
            _r = tk.Frame(f, bg=_bg2)
            _r.pack(anchor="center", pady=3)
            self._dlg_btn(_r, "保存", _ok)

        _exp_row(b1, "音色", _b_voice)
        _exp_row(b1, "唤醒词", _b_wake)
        # 听歌识曲两态（★ 2026-10-12：点击后重建图片刷新文字）
        def _sr_maker():
            _on = getattr(self, "_songrec_on", True)
            return _pil_round(("✓ 听歌识曲（运行中）" if _on else "听歌识曲（已停止）"),
                              300, 34, 10, "#ffffff", "#46536a", True)[0]
        _mk2(b1, _sr_maker, self._songrec_toggle)
        # 免打扰两态（★ 2026-10-12：点击后重建图片刷新文字）
        def _dnd_maker():
            _on = getattr(self, "_dnd", False)
            return _pil_round(("✓ 免打扰（已开启）" if _on else "免打扰（已关闭）"),
                              300, 34, 10, "#ffffff", "#46536a", True)[0]
        _mk2(b1, _dnd_maker, self._dnd_toggle, (8, 2))
        # 天气预警两态（★ 2026-10-12：点击后重建图片刷新文字）
        def _wa_maker():
            _wa = True
            try:
                with open(fairy_root.CONFIG, encoding="utf-8") as _f:
                    _wa = bool((json.load(_f).get("briefing") or {}).get("weather_alert", True))
            except Exception:
                pass
            return _pil_round(("✓ 天气预警（已开启）" if _wa else "天气预警（已关闭）"),
                              300, 34, 10, "#ffffff", "#46536a", True)[0]
        _mk2(b1, _wa_maker, self._alert_toggle)

        # 3 视觉识别
        b3 = _block("视觉识别")
        _row(b3, None, [("截图标注", self._screenshot_anno),
                        ("识别文字 OCR", self._vision_ocr),
                        ("识别物体 YOLO", self._vision_detect),
                        ("屏幕取色", self._color_pick)])

        # 4 网络与设备
        b4 = _block("网络与设备")
        _row(b4, None, [("网络诊断", self._net_diag),
                        ("扫描新设备", self._iot_scan)])

        def _b_iot(f):
            """★ 2026-10-12【设备列表·内嵌版】滚动列表（已标记/未标记）+
            未标记行尾「标记」按钮 + 底部起名输入框，不再弹独立窗口。"""
            _bg2 = "#f2f5f8"
            # ★ 2026-10-12【居中】设备列表整体居中：包一层容器，_cv2+滚动条
            #   在容器内左右排布，容器本身锚定中间，不再铺满整个展开区。
            _wrap2 = tk.Frame(f, bg=_bg2)
            _wrap2.pack(anchor="center", pady=2)
            # ★★ 2026-10-18【修「滚动条不能滚」】
            #   根因：canvas 写死 width=640 且 `fill="y"`（宽度由它决定），
            #   于是 pack 到右边的 Scrollbar **拿不到剩余宽度**，
            #   被挤成一条几乎为 0 的细线（用户截图里右侧那条细条就是它），
            #   既拖不到、也点不中 —— 看起来就是"滚动条不能滚"。
            #   改法照独立窗口版（_iot_devices）的可靠写法：
            #   滚动条先占右边 14px，canvas 占剩下的并跟随容器伸缩。
            _SB_W = 14
            _sb2 = tk.Scrollbar(_wrap2, orient="vertical", width=_SB_W)
            _sb2.pack(side="right", fill="y")
            _cv2 = tk.Canvas(_wrap2, bg=_bg2, highlightthickness=0, height=150,
                             width=640 - _SB_W)
            _cv2.configure(yscrollcommand=_sb2.set)
            _cv2.pack(side="left", fill="both", expand=True)
            _sb2.configure(command=_cv2.yview)
            # ★ 2026-10-12【滚动修复】列表区域滚轮直接滚动（含 Canvas 空白处）；
            #   滚到边界时透传给外层面板，避免"卡住滚不动"。
            def _wheel_cv2(_ev):
                try:
                    _d = int(-1 * (_ev.delta / 120))
                    _top, _bot = _cv2.yview()
                    if (_d > 0 and _top > 0.0) or (_d < 0 and _bot < 1.0):
                        _cv2.yview_scroll(_d, "units")
                    else:
                        try:
                            _cv.yview_scroll(_d, "units")
                        except Exception:
                            pass
                except Exception:
                    pass
            _cv2.bind("<MouseWheel>", _wheel_cv2)
            # ★ 2026-10-18：把滚轮也绑到整个容器和滚动条上 —— 鼠标停在
            #   滚动条/边缘空白处时滚轮同样生效（以前只绑 canvas，
            #   指针一偏出去就"滚不动"，很像滚动条坏了）。
            try:
                _wrap2.bind("<MouseWheel>", _wheel_cv2)
                _sb2.bind("<MouseWheel>", _wheel_cv2)
            except Exception:
                pass
            _inner2 = tk.Frame(_cv2, bg=_bg2)
            _wid2 = _cv2.create_window((0, 0), window=_inner2, anchor="nw")

            def _fit2(_e=None):
                try:
                    _cv2.itemconfigure(_wid2, width=max(120, _cv2.winfo_width() - 6))
                    _cv2.configure(scrollregion=_cv2.bbox("all"))
                except Exception:
                    pass
            _cv2.bind("<Configure>", _fit2)
            # ★ 2026-10-18：内容尺寸一变就更新 scrollregion。
            #   只绑 <Configure> 在 canvas 上的话，canvas 尺寸没变（面板复用）
            #   而列表内容变多/变少时，scrollregion 会停在旧值 —— 表现就是
            #   "滚动条拖不动"或"拖到一半就到头"。
            _inner2.bind("<Configure>", _fit2)

            def _bind_w2(_w):
                _w.bind("<MouseWheel>", _wheel_cv2)
                for _c in _w.winfo_children():
                    _bind_w2(_c)

            _KCN = {"lan": "局域网", "bt": "蓝牙", "mdns": "组播/投屏",
                    "serial": "串口", "nearlink": "星闪", "nearby": "近距离"}
            _devs = {"list": []}

            def _fmt(x):
                _n = x.get("tag") or x.get("name") or x.get("id") or "?"
                _t = x.get("type") or ""
                _v = x.get("vendor") or ""
                _k = _KCN.get(str(x.get("kind") or x.get("adapter") or ""), "")
                _ip = str(x.get("ip") or "").strip()
                _mac = str(x.get("id") or "")
                for _p in ("ble:", "lan:", "nearlink:", "nearby:", "serial:"):
                    _mac = _mac.replace(_p, "")
                _mac = (_mac[-6:] if len(_mac) > 6 else _mac)
                _bits = [b for b in (_v, _k, _t, _ip, _mac) if b and b != "未知设备"]
                _s = " · ".join(_bits)
                return ("%s（%s）" % (_n, _s)) if _s else str(_n)

            def _paint():
                # ★ 2026-10-12【销毁防御】后台扫描完成回调时，展开区可能已被
                #   用户收起（_inner2 已销毁）-> 直接静默退出，绝不抛 TclError
                #   打断 Tk 事件循环（否则面板/球全"没反应"）。
                try:
                    if not _inner2.winfo_exists():
                        return
                except Exception:
                    return
                for _c in _inner2.winfo_children():
                    _c.destroy()
                _devs2 = _devs["list"]
                if not _devs2:
                    tk.Label(_inner2, text="还没有已连接设备（先去「扫描新设备」）",
                             bg=_bg2, fg="#66727f",
                             font=("SimHei", 11)).pack(pady=8)
                    _bind_w2(_inner2)
                    return
                _tagged = [x for x in _devs2 if (x.get("tag") or "").strip()]
                _untagged = [x for x in _devs2 if not (x.get("tag") or "").strip()]
                for _title, _items, _can_tag in (
                        ("已标记", _tagged, False), ("未标记", _untagged, True)):
                    if not _items:
                        continue
                    tk.Label(_inner2, text="%s（%d）" % (_title, len(_items)),
                             bg=_bg2, fg="#5f7ea5",
                             font=("SimHei", 11, "bold")).pack(anchor="w", pady=(6, 2))
                    for _x in _items:
                        _rowf = tk.Frame(_inner2, bg=_bg2)
                        _rowf.pack(fill="x", pady=1)
                        tk.Label(_rowf, text=_fmt(_x), bg=_bg2, fg="#46536a",
                                 font=("SimHei", 10), width=44,
                                 anchor="w").pack(side="left", padx=2)
                        if _can_tag:
                            _img = _pil_round("标记", 58, 30, 10, "#ffffff",
                                              "#5f7ea5", True, "#dfe3ea")[0]
                            _b = tk.Button(_rowf, image=_img, bd=0, relief="flat",
                                           highlightthickness=0, bg=_bg2,
                                           activebackground=_bg2, cursor="hand2",
                                           command=lambda x=_x: _mark(x))
                            _b.image = _img
                            _b.pack(side="right", padx=2)
                _bind_w2(_inner2)
                _fit2()

            _tag_pending = {"id": None}
            _hint = tk.Label(f, text="", bg=_bg2, fg="#66727f",
                             font=("SimHei", 10))
            _hint.pack(anchor="center", pady=(6, 0))
            _te = tk.Entry(f, width=24, font=("SimHei", 11))
            _te.pack(anchor="center", pady=2)

            def _mark(_x):
                _tag_pending["id"] = str(_x.get("id") or "")
                _hint.config(text="给「%s」起名：" % (_x.get("name") or _x.get("id")))
                _te.focus_force()

            def _save_tag():
                _id = _tag_pending["id"]
                _nm = _te.get().strip()
                if not _id or not _nm:
                    self._bubble("先点未标记设备的「标记」再输入名字", 3)
                    return
                try:
                    _r2 = _rnw([sys.executable,
                                   os.path.join(self._tools_dir(), "fairy_iot.py"),
                                   "--tag", _id, _nm, "--json"],
                               capture_output=True, timeout=30, text=True,
                               encoding="utf-8", errors="replace")
                    _d2 = json.loads(_r2.stdout or "{}")
                    self._bubble(("已标记：%s" % _nm) if _d2.get("ok")
                                 else ("标记失败：" + str(_d2.get("msg") or "")[:50]), 4)
                    _hint.config(text="")
                    _te.delete(0, "end")
                    _reload()
                except Exception as _e2:
                    self._bubble("标记失败：" + str(_e2)[:60], 4)
            _r5 = tk.Frame(f, bg=_bg2)
            _r5.pack(anchor="center", pady=3)
            self._dlg_btn(_r5, "保存标记", _save_tag)

            def _reload():
                import threading as _th2

                def _work():
                    try:
                        _rr = _rnw([sys.executable,
                                       os.path.join(self._tools_dir(), "fairy_iot.py"),
                                       "--devices", "--json"], capture_output=True,
                                      timeout=30, text=True, encoding="utf-8", errors="replace")
                        _dd = json.loads(_rr.stdout or "{}")
                        _devs["list"] = _dd.get("devices") or []
                    except Exception:
                        _devs["list"] = []
                    try:
                        f.after(0, _paint)
                    except Exception:
                        pass
                _th2.Thread(target=_work, daemon=True).start()
            _reload()
        _exp_row(b4, "设备列表（分类 / 标记）", _b_iot)

        # 5 设置
        b5 = _block("设置")

        def _b_pos(f):
            """★ 2026-10-12【位置·精简】只保留自定义城市输入：请输入城市
            + 保存 / 取消（点选城市列表已删除）。"""
            _bg2 = "#f2f5f8"
            tk.Label(f, text="请输入城市", bg=_bg2, fg="#66727f",
                     font=("SimHei", 11)).pack(anchor="center", pady=(4, 1))
            _cv2 = tk.StringVar(value=self.city or "")
            tk.Entry(f, textvariable=_cv2, width=22, font=("SimHei", 12)
                     ).pack(anchor="center", pady=3)

            def _save_pos():
                _c = _cv2.get().strip()
                if _c:
                    self._set_city(_c)
                    self._bubble("位置已设为 %s" % _c, 3)
                else:
                    self._bubble("请输入城市名", 3)

            def _cancel_pos():
                _cv2.set("")
            _r2 = tk.Frame(f, bg=_bg2)
            _r2.pack(anchor="center", pady=3)
            self._dlg_btn(_r2, "保存", _save_pos)
            self._dlg_btn(_r2, "取消", _cancel_pos)

        def _b_night(f):
            _bg2 = "#f2f5f8"
            _cur = {"night": True, "start": "22:00", "end": "07:00"}
            try:
                with open(fairy_root.CONFIG, encoding="utf-8") as _f:
                    _cur.update((json.load(_f).get("dnd") or {}))
            except Exception:
                pass
            _on2 = tk.BooleanVar(value=bool(_cur.get("night", True)))
            tk.Checkbutton(f, text="开启夜间模式（到点自动静音播报）", variable=_on2,
                           anchor="w", bg=_bg2, fg="#46536a", activebackground=_bg2,
                           selectcolor=_bg2, font=("SimHei", 12)).pack(anchor="center", pady=3)
            tk.Label(f, text="开始时间（如 22:00）", bg=_bg2, fg="#66727f",
                     font=("SimHei", 11)).pack(anchor="center")
            _st2 = tk.StringVar(value=_cur.get("start", "22:00"))
            tk.Entry(f, textvariable=_st2, width=12, font=("SimHei", 12)
                     ).pack(anchor="center", pady=2)
            tk.Label(f, text="结束时间（如 07:00）", bg=_bg2, fg="#66727f",
                     font=("SimHei", 11)).pack(anchor="center")
            _en2 = tk.StringVar(value=_cur.get("end", "07:00"))
            tk.Entry(f, textvariable=_en2, width=12, font=("SimHei", 12)
                     ).pack(anchor="center", pady=2)

            def _save_night():
                try:
                    with open(fairy_root.CONFIG, encoding="utf-8") as _f:
                        _j = json.load(_f)
                    (_j.setdefault("dnd", {}))["night"] = bool(_on2.get())
                    (_j.setdefault("dnd", {}))["start"] = _st2.get().strip() or "22:00"
                    (_j.setdefault("dnd", {}))["end"] = _en2.get().strip() or "07:00"
                    with open(fairy_root.CONFIG, "w", encoding="utf-8") as _f:
                        json.dump(_j, _f, ensure_ascii=False, indent=2)
                    self._bubble("夜间模式已保存", 3)
                except Exception as _e:
                    self._bubble("保存失败：" + str(_e)[:50], 4)
            _r4 = tk.Frame(f, bg=_bg2)
            _r4.pack(anchor="center", pady=3)
            self._dlg_btn(_r4, "保存", _save_night)

        def _b_remind(f):
            _bg2 = "#f2f5f8"
            tk.Label(f, text="多久后提醒（分钟）？", bg=_bg2, fg="#66727f",
                     font=("SimHei", 11)).pack(anchor="center", pady=(4, 2))
            _mins = tk.Entry(f, width=10, font=("SimHei", 12))
            _mins.insert(0, "10")
            _mins.pack(anchor="center", pady=2)
            tk.Label(f, text="提醒内容", bg=_bg2, fg="#66727f",
                     font=("SimHei", 11)).pack(anchor="center", pady=(4, 2))
            _msg = tk.Entry(f, width=30, font=("SimHei", 12))
            _msg.insert(0, "到时间了")
            _msg.pack(anchor="center", pady=2)

            def _ok3():
                try:
                    mins = float(_mins.get().strip())
                except ValueError:
                    self._bubble("分钟数不对", 3)
                    return
                t = _msg.get().strip() or "到时间了"
                self._reminders.append((time.time() + mins * 60, t))
                self.say("好的，%g 分钟后提醒你" % mins, "ok")
                self._bubble("已设提醒：%g 分钟后「%s」" % (mins, t), 4)
            _r = tk.Frame(f, bg=_bg2)
            _r.pack(anchor="center", pady=3)
            self._dlg_btn(_r, "确定", _ok3)

        def _b_todo(f):
            _bg2 = "#f2f5f8"
            _box = tk.Listbox(f, width=48, height=5, font=("SimHei", 11),
                              bg="#ffffff", fg="#46536a", relief="flat",
                              highlightthickness=1, highlightbackground="#d5d9e3")
            _box.pack(anchor="center", pady=3)

            def _refresh2():
                _box.delete(0, "end")
                try:
                    sys.path.insert(0, self._tools_dir())
                    import fairy_todo as _ft2
                    for i, x in enumerate(_ft2.list_items()):
                        _box.insert("end", "[%d] %s%s %s%s" % (
                            i, "完成" if x.get("done") else "待办", "  " + x["text"],
                            " @" + x["at"] if x.get("at") else "",
                            "  ←已到期" if (x.get("at") and x["at"] <= time.strftime("%H:%M")
                                           and not x.get("done")) else ""))
                except Exception:
                    pass
            _refresh2()
            _ent = tk.Entry(f, width=48, font=("SimHei", 12))
            _ent.pack(anchor="center", pady=2)
            tk.Label(f, text="添加：内容|时间（如 交报告|14:30，时间可省）", bg=_bg2,
                     fg="#97a1ad", font=("SimHei", 10)).pack(anchor="center")

            def _add2():
                t = _ent.get().strip()
                if not t:
                    return
                at = ""
                if "|" in t:
                    t, at = t.split("|", 1)
                try:
                    sys.path.insert(0, self._tools_dir())
                    import fairy_todo as _ft3
                    _ft3.add(t.strip(), at.strip())
                    self.say("已添加待办" + ("" if not at else "，" + at.strip() + " 提醒"), "ok")
                except Exception:
                    pass
                _ent.delete(0, "end")
                _refresh2()

            def _done2():
                try:
                    s = _box.get("active").split("]")[0].strip("[").strip()
                    sys.path.insert(0, self._tools_dir())
                    import fairy_todo as _ft4
                    _ft4.done(int(s))
                except Exception:
                    pass
                _refresh2()

            def _del2():
                try:
                    s = _box.get("active").split("]")[0].strip("[").strip()
                    sys.path.insert(0, self._tools_dir())
                    import fairy_todo as _ft5
                    _ft5.delete(int(s))
                except Exception:
                    pass
                _refresh2()
            _r = tk.Frame(f, bg=_bg2)
            _r.pack(anchor="center", pady=3)
            self._dlg_btn(_r, "添加", _add2)
            self._dlg_btn(_r, "完成（选中）", _done2)
            self._dlg_btn(_r, "删除（选中）", _del2, danger=True)

        _exp_row(b5, "定时提醒", _b_remind)
        _exp_row(b5, "待办清单", _b_todo)
        _exp_row(b5, "位置", _b_pos)

        def _b_cmd(f):
            _bg2 = "#f2f5f8"
            _box = tk.Listbox(f, width=56, height=5, font=("SimHei", 11),
                              bg="#ffffff", fg="#46536a", relief="flat",
                              highlightthickness=1, highlightbackground="#d5d9e3")
            _box.pack(anchor="center", pady=3)

            def _refresh3():
                _box.delete(0, "end")
                try:
                    sys.path.insert(0, self._tools_dir())
                    import fairy_custom_cmd as _fcc
                    for i, c in enumerate(_fcc._load()):
                        _box.insert("end", "[%d] %s → %s: %s" % (
                            i, c.get("trigger"), c.get("type"), c.get("target")))
                except Exception:
                    pass
            _refresh3()
            _f1 = tk.Frame(f, bg=_bg2)
            _f1.pack(anchor="center", pady=2)
            tk.Label(_f1, text="说：", bg=_bg2, fg="#46536a",
                     font=("SimHei", 11)).pack(side="left")
            _e1 = tk.Entry(_f1, width=12, font=("SimHei", 11))
            _e1.pack(side="left", padx=(0, 8))
            tk.Label(_f1, text="做：", bg=_bg2, fg="#46536a",
                     font=("SimHei", 11)).pack(side="left")
            _e2 = tk.Entry(_f1, width=22, font=("SimHei", 11))
            _e2.pack(side="left")
            _v = tk.StringVar(value="say")
            _f2 = tk.Frame(f, bg=_bg2)
            _f2.pack(anchor="center", pady=2)
            for _k, _nm in (("app", "打开软件"), ("url", "打开网页"),
                            ("say", "回复一句"), ("cmd", "运行命令")):
                tk.Radiobutton(_f2, text=_nm, variable=_v, value=_k, bg=_bg2,
                               fg="#46536a", activebackground=_bg2, selectcolor=_bg2,
                               font=("SimHei", 10)).pack(side="left", padx=3)

            def _add3():
                tr = _e1.get().strip()
                tg = _e2.get().strip()
                if not tr or not tg:
                    return
                try:
                    sys.path.insert(0, self._tools_dir())
                    import fairy_custom_cmd as _fcc2
                    _fcc2.add(tr, _v.get(), tg)
                    self.say("已添加自定义指令", "ok")
                except Exception:
                    pass
                _e1.delete(0, "end")
                _e2.delete(0, "end")
                _refresh3()

            def _del3():
                try:
                    s = _box.get("active").split("]")[0].strip("[").strip()
                    sys.path.insert(0, self._tools_dir())
                    import fairy_custom_cmd as _fcc3
                    _fcc3.remove(int(s))
                except Exception:
                    pass
                _refresh3()
            _r = tk.Frame(f, bg=_bg2)
            _r.pack(anchor="center", pady=3)
            self._dlg_btn(_r, "添加", _add3)
            self._dlg_btn(_r, "删除（选中）", _del3, danger=True)
            tk.Label(f, text="例：说「查快递」→ 打开「拼多多」；说「网怎么了」→ 运行「网络诊断」",
                     bg=_bg2, fg="#97a1ad", font=("SimHei", 10)).pack(anchor="center", pady=(4, 0))

        _exp_row(b5, "自定义指令", _b_cmd)
        # ★ 2026-10-18【已彻底移除】「启动播报内容」勾选面板连同 _b_brief /
        #   _briefing_choose 一起删了。启动播报现在固定 = 天气 + 穿衣建议；
        #   要增减项直接改 fairy.json 的 briefing 段（见 briefing.py 的 build()）。
        _exp_row(b5, "夜间模式", _b_night)
        tk.Label(b5, text="", bg="#ffffff", height=1).pack()




        _cv.update_idletasks()
        _cv.configure(scrollregion=_cv.bbox("all"))
        win.after(250, lambda: (_cv.update_idletasks(),
                                _cv.configure(scrollregion=_cv.bbox("all"))))
        # ★ 2026-10-12【宽度/滚动区加固】构建完立刻刷新内层宽度与滚动区，
        #   再在 400ms 后补一次 —— 避免 Canvas 首帧宽度为 0 时展开区宽度
        #   被卡成最小值的"灰条不铺满、内容不居中"问题。
        try:
            _fit(None)
        except Exception:
            pass
        win.after(400, lambda: (_fit(None), _cv.update_idletasks(),
                                _cv.configure(scrollregion=_cv.bbox("all"))))

        # 滚轮绑定到右侧所有子控件
        def _bind_wheel(_w):
            _w.bind("<MouseWheel>", lambda _e: _cv.yview_scroll(
                int(-1 * (_e.delta / 120)), "units"))
            for _c in _w.winfo_children():
                _bind_wheel(_c)
        _bind_wheel(_inner)

        # Esc 关闭面板（任意焦点都生效；面板销毁后自动失效）
        def _esc(_e):
            try:
                if win.winfo_exists():
                    win.destroy()
            except Exception:
                pass
        win.bind_all("<Escape>", _esc)

    def _pick_side(self):
        """球在屏幕**左半边** → 进度条在球的右边；在**右半边** → 条在球的左边。

        用屏幕中线判断（而不是"右边还剩多少空间"），因为这样更符合直觉，
        而且天然保证条不会跑出屏幕外。
        """
        try:
            vx, vy, vw, vh = virtual_screen()
            ball_x = self.winfo_x() + (0 if self.side == "right" else BAR_W + GAP)
            center = ball_x + SIZE / 2.0
            return "right" if center < (vx + vw / 2.0) else "left"
        except Exception:
            return "right"

    def _stack_relayout(self):
        """★ 2026-10-12【气泡栈】多个气泡/浮窗垂直排列，绝不叠加：
        从球正下方逐一向下排（间隔 8px）；放不下翻到球上方从下往上排；
        任一气泡超时消失后自动补位重排。"""
        try:
            _stack = []
            for _w in getattr(self, "_bub_stack", []):
                try:
                    if _w.winfo_exists():
                        _stack.append(_w)
                    else:
                        try:
                            _w.destroy()
                        except Exception:
                            pass
                except Exception:
                    pass
            self._bub_stack = _stack
            if not _stack:
                return
            vx, vy, vw, vh = virtual_screen()
            _heights = [max(1, _w.winfo_height()) for _w in _stack]
            _total = sum(h + 8 for h in _heights)
            _base_y = self.winfo_y() + WIN_H + 6
            _flip_up = False
            if _base_y + _total > vy + vh - 8:
                _flip_up = True
                _base_y = self.winfo_y() - 6
            _y = _base_y - _total if _flip_up else _base_y
            for _w, _h in zip(_stack, _heights):
                _ww2 = max(1, _w.winfo_width())
                _px = self._ball_center_x() - _ww2 // 2
                _px = max(vx + _EDGE_PX, min(_px, vx + vw - _ww2 - _EDGE_PX))
                _w.geometry("+%d+%d" % (_px, _y))
                _y += _h + 8
        except Exception:
            pass

    def _ball_progress(self, pct, label, on=True):
        """★ 2026-10-12【球体进度条】临时进度画在球右侧进度条面板（不弹气泡）。
        on=True 设置 (pct, label)；on=False 清空，回到宿主实时进度。"""
        try:
            if on:
                self._temp_prog = (int(pct), str(label))
            else:
                self._temp_prog = None
        except Exception:
            pass

    def _preset_progress(self, title, port, speak=True):
        """★ 2026-10-12【组合切换进度 v2】进度画在球右侧进度条面板。

        port 为字符串端口。
        ★ 2026-10-17 改动：
          · `speak=False` 时只画进度条、不播报 —— 由调用方（_apply_preset 的串行
            工作线程）在"端口真的通了"之后统一播一次，避免"已切换"和"已就绪"打架。
          · 就绪等待从 90 秒放宽到 360 秒：Strata 加载 40 GB 专家缓存实测要 3~5 分钟，
            90 秒必然误报"启动超时"。
        """
        try:
            self._ball_progress(5, "Fairy 组合切换 · %s" % title, True)

            def _poll():
                import urllib.request as _ur
                _t0 = time.time()
                _last = -1
                while time.time() - _t0 < 360:
                    _up = False
                    try:
                        with _ur.urlopen("http://127.0.0.1:%s/v1/models"
                                         % port, timeout=1.2) as _r:
                            _j = json.loads(_r.read().decode("utf-8", "ignore"))
                            _up = bool(_j.get("data") or _j.get("models"))
                    except Exception:
                        try:
                            with _ur.urlopen("http://127.0.0.1:%s/api/tags"
                                             % port, timeout=1.2) as _r2:
                                _j2 = json.loads(_r2.read().decode("utf-8", "ignore"))
                                _up = bool(_j2.get("models"))
                        except Exception:
                            _up = False
                    _p = 40 if not _up else 95
                    if _p != _last:
                        _last = _p
                        self._ball_progress(_p, "Fairy 组合切换 · %s" % title, True)
                    if _up:
                        self._ball_progress(100, "Fairy 组合切换 · %s" % title, True)
                        time.sleep(0.8)
                        self._ball_progress(0, "", False)
                        if speak:
                            try:
                                self._bubble("组合「%s」已就绪" % title, 4)
                                self.say("组合%s已就绪" % title, "ok")
                            except Exception:
                                pass
                        return
                    time.sleep(0.6)
                self._ball_progress(0, "", False)
                if speak:
                    try:
                        self._bubble("组合「%s」启动超时，请检查引擎" % title, 5)
                        self.say("组合%s启动超时" % title, "warn")
                    except Exception:
                        pass
            threading.Thread(target=_poll, daemon=True).start()
        except Exception:
            pass


class InputMixin:
    """InputMixin（2026-10-23 从 FairyBall 拆出）"""

    def _on_press(self, e):
        self._drag = (e.x_root, e.y_root, self.winfo_x(), self.winfo_y())
        self._moved = False

    def _on_drag(self, e):
        if not self._drag:
            return
        sx, sy, ox, oy = self._drag
        nx, ny = ox + (e.x_root - sx), oy + (e.y_root - sy)
        if abs(nx - ox) > 3 or abs(ny - oy) > 3:
            self._moved = True
        cx, cy = self._clamp(nx, ny)
        self.geometry("+%d+%d" % (cx, cy))

    def _on_release(self, e):
        if self._moved:
            self._save_pos()
        else:
            # ★★ 2026-10-19【用户要求】左键单击 = 临时看悬浮窗 10 秒。
            #   显隐本身只看运行状态（跑任务就显示、空闲就隐藏），
            #   左键只是"我主动想看一眼"，10 秒后自动收回。
            self._peek_panel(10.0)
        self._drag = None

    def _on_hover_in(self, e=None):
        """★★ 2026-10-24【用户要求：鼠标悬停就显示信息面板】

        原话「把这球和下面的框都删除 功能放在桌面的球上鼠标悬停上」——
        网页那套球和状态框已经关掉了，信息改在这里看。

        【怎么做的】不新画面板，直接复用 _panel_peek_until：
        _tick 里是 `_show = running or _tmp or (now < _panel_peek_until)`，
        所以把它推到一个很远的时刻，就等于「一直显示到鼠标移开」。
        移开时清 0，tick 立刻收起来。
        """
        self._hovering = True
        import time as _t
        self._panel_peek_until = _t.time() + 999999.0
        try:
            self._tick()      # 立刻画一帧，别等下一个 33fps 周期
        except Exception:
            pass

    def _on_hover_out(self, e=None):
        self._hovering = False
        # ★ 2026-10-24：移开就收面板（清掉悬停时推远的截止时刻）
        self._panel_peek_until = 0.0
        w = getattr(self, "_hover", None)
        if w is not None:
            try:
                w.destroy()
            except Exception:
                pass
            self._hover = None
        try:
            self._tick()
        except Exception:
            pass

    def _peek_panel(self, seconds=10.0):
        """★ 2026-10-19【用户要求】左键单击 = 临时看悬浮窗 10 秒。

        不切换任何状态：只是把 _panel_peek_until 推到 now+10，
        _tick 每帧判断一次，到点就自己收起来。
        正在跑任务时本来就一直显示，这时候点不点都一样。
        """
        import time as _t
        self._panel_peek_until = _t.time() + float(seconds)
        try:
            self._tick()        # 立刻画一帧，别等下一个 33fps 周期
        except Exception:
            pass
        print("[fairy] 悬浮窗: 临时查看 %.0f 秒" % seconds, flush=True)


class BubbleMixin:
    """BubbleMixin（2026-10-23 从 FairyBall 拆出）"""

    def _bubble(self, text, seconds=4):
        """对话气泡：只在 Fairy【主动说关键话】时弹一句，几秒后自动消失。

        为什么不常驻：
          · 球只有 128px，常驻气泡会把它从"发光小精灵"变成"聊天窗口"，还挡桌面
          · 正常对话的内容本来就在 DSH 界面里，气泡是重复信息
        为什么还是要：
          · Fairy 主动说话（开工/完工/告警）时你多半没看屏幕，纯语音会漏
          · 补一句短气泡和语音同步，就不会漏事
        ★ 2026-10-12：多个气泡垂直排列（气泡栈），不叠加；各自超时消失并补位。
        """
        try:
            if not hasattr(self, "_bub_stack"):
                self._bub_stack = []
            text = fix_mojibake(text)   # ★ 2026-10-12：乱码自愈，显示前还原
            w, ww, wh = make_bubble(self, text, tail="top", tail_x=self._tail_x())
            px, py, _flip = self._popup_pos(ww, wh)
            retail_bubble(w, self._ball_center_x(), px)   # ★按落点重画尖角，对准球
            w.geometry("+%d+%d" % (px, py))
            self._bub_stack.append(w)

            def _gone():
                try:
                    w.destroy()
                except Exception:
                    pass
                try:
                    if w in self._bub_stack:
                        self._bub_stack.remove(w)
                except Exception:
                    pass
                try:
                    self._stack_relayout()
                except Exception:
                    pass
            w.after(int(seconds * 1000), _gone)
            try:
                self._stack_relayout()
            except Exception:
                pass
        except Exception:
            pass

    def _show_text(self, lines, seconds=18):
        """屏幕通道：弹一个浮窗显示播报全文（不抢焦点，到点自动关）。

        —— 需求：Fairy 可以是声音设备、显示设备等等，
        所以同一段信息同时走【声音】和【屏幕】两条路。
        """
        try:
            w = tk.Toplevel(self)
            w.overrideredirect(True)
            w.attributes("-topmost", True)
            w.configure(bg="#0a1420")
            lines = [fix_mojibake(_l) for _l in lines]   # ★ 2026-10-12：乱码自愈
            tk.Label(w, text="\n".join(lines), justify="left", fg="#cfe3f5", bg="#0a1420",
                     font=("SimHei", 12), padx=16, pady=12).pack()
            w.update_idletasks()
            vx, vy, vw, vh = virtual_screen()
            ww, wh = w.winfo_width(), w.winfo_height()
            # 【布局】贴在【球 + 进度条】那个小窗的【正下方】，不要浮在屏幕中间。
            # 贴屏幕下沿时自动翻到面板上方；左右做边界收拢。
            px, py, _flip = self._popup_pos(ww, wh)   # ★与气泡/悬停面板同一套定位
            w.geometry("+%d+%d" % (px, py))
            # ★ 2026-10-12：播报浮窗也进气泡栈，与短气泡垂直排列不叠加
            if not hasattr(self, "_bub_stack"):
                self._bub_stack = []
            self._bub_stack.append(w)

            def _gone():
                try:
                    w.destroy()
                except Exception:
                    pass
                try:
                    if w in self._bub_stack:
                        self._bub_stack.remove(w)
                except Exception:
                    pass
                try:
                    self._stack_relayout()
                except Exception:
                    pass
            w.after(seconds * 1000, _gone)
            try:
                self._stack_relayout()
            except Exception:
                pass
        except Exception as _e:
            # 顺手去掉静默兜底（今天已多次因它藏住 bug）
            print("[fairy] 播报浮窗失败: %s: %s" % (type(_e).__name__, _e), flush=True)

    def _ui_bubble(self, text):
        """后台线程安全弹气泡：只入队，由主线程 _tick 取出再弹。"""
        try:
            self._adv_bubbles.append(text)
        except Exception:
            pass

    def _flash_bar(self, text):
        """★ 2026-10-12【播报提示条】主线程短暂显示提示条 3 秒，
        播报时也能看到进度条面板（不只在任务运行时显示）。"""
        try:
            def _on():
                self._local_task = text
                self.after(3000, lambda: setattr(self, "_local_task", None))
            self.after(0, _on)
        except Exception:
            pass

    # 这些工具名说明正在查资料 -> 播「正在搜索相关信息」，其余一律「正在思考问题」
    _SEARCH_TOOL_KEYS = (
        "search", "fetch", "web", "browse", "google", "bing",
        "http", "url", "curl", "grep", "glob", "read", "ls",
    )

    def _thinking_phrase(self, tool):
        """★ 2026-10-24【用户要求】思考中只播这两句之一。

        原话：「在思考时，只直接播报正在思考问题，或者正在搜索相关信息，
               思考完毕出现完整结果才进行结果全语音作答」

        判断依据是当前正在跑的工具名：命中搜索/读取类关键词就说"正在搜索相关信息"，
        否则（推理、写代码、改文件……）说"正在思考问题"。
        """
        t = (tool or "").lower()
        if t and any(k in t for k in self._SEARCH_TOOL_KEYS):
            return "正在搜索相关信息"
        return "正在思考问题"

    def _on_state(self, kind, text):
        """状态回调。

        **开始干活时只说"正在执行任务"**，不逐条念具体动作
        （具体动作仍在进度条旁边以文字显示）。
        另外两条保留：干完报"任务完成"、额度告警。
        去重逻辑在 Speaker 里，所以同一个任务里这句只会念一次。
        """
        try:
            if kind == "doing":
                # 从"待机"变成"开工"时，弹一次气泡（过程播报不弹，否则太吵）
                if not getattr(self, "_was_running", False):
                    # ★ 2026-10-10这里原来是 self.after(0, ...) ——
                    #   本函数跑在 State.poll_loop 的【后台线程】里，
                    #   而 Tk 的 after/_bubble 只能在主线程碰：
                    #   主线程一忙就抛 "main thread is not in main loop"（今日日志 15 次），
                    #   异常把整个 doing 分支吞掉（含下面的 _was_running = True）
                    #   -> 开工语音时有时无。
                    #   改成和 work_advisor 同一范式：只【入队】，主线程 _tick 里再弹。
                    self._adv_bubbles.append("了解。开始同步。")
                self._was_running = True
                # 【关键】按"刚才那个工具"选一句**语义对得上的游戏原声**，
                # 这样动态播报也是 100% 游戏声（播的是游戏原音文件）。
                # 具体文字仍然显示在进度条旁边。
                tool = ""
                try:
                    with self.state.lock:
                        tool = self.state.last_tool
                except Exception:
                    pass
                # ★★ 2026-10-24【用户要求：思考中只播这两句】
                #   原话：「在思考时，只直接播报正在思考问题，或者正在搜索相关信息，
                #           思考完毕出现完整结果才进行结果全语音作答」
                #   所以这里不再用游戏原声池随机抽，改成明确的两句话；
                #   全文播报交给 State._pending_answer（等这一轮跑完才念）。
                #   同一句话连续不重复播，避免一轮里反复念叨。
                _phrase = self._thinking_phrase(tool)
                if _phrase != getattr(self, "_last_doing_phrase", None):
                    self._last_doing_phrase = _phrase
                    self.speaker.say(_phrase)
            elif kind == "done":
                self.speaker.say(SPEAK_DONE)
                self._was_running = False
                self._last_doing_phrase = None      # ★ 下一轮可以重新播进度
                # ★ 同 #2：入队 -> 主线程弹（原来 self.after(0, ...) 是 Tk 跨线程调用）
                self._adv_bubbles.append("主人，我已完成既定目标。")
            elif kind == "answer":
                # ★ 助手回复 —— "Fairy 就是 DSH 的嘴"的关键一条。
                # level="answer"：_duck_around 只对 answer/alert 让位，
                # 因为这是"主动提问、在等答案"的场景，音乐应该让位。
                # 过程播报(doing) 绝不用这个 level，否则干活时音量被反复压低。
                # ★ 2026-10-12：播报时也短暂显示提示条（否则日常对话看不到提示条）
                try:
                    self._flash_bar((text or "")[:28])
                except Exception:
                    pass
                self.speaker.say(text, level="answer")
            elif kind == "token":
                self.speaker.say(text)
        except Exception as e:
            # 【不静默】原来这里是 except: pass，会把 bug 藏起来。
            # 今天正是因为静默兜底，一个 bug 被藏了三轮才发现。出错必须打出来。
            print("[fairy] _on_state(%s) 处理失败: %s" % (kind, e), flush=True)
            import traceback
            traceback.print_exc()

    def _maybe_brief(self):
        """开机简报：等 DSH 通了 + 过了等待时间，就播一次。"""
        if self._briefed or time.time() < self._brief_at:
            return
        with self.state.lock:
            ok = self.state.ok
        if not ok:
            return
        self._briefed = True
        _bt0 = time.time()
        print("[fairy] 播报开始 @ %s" % time.strftime("%H:%M:%S"), flush=True)
        try:
            import importlib.util
            fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "briefing.py")
            s = importlib.util.spec_from_file_location("fairy_briefing", fp)
            m = importlib.util.module_from_spec(s)
            sys.modules["fairy_briefing"] = m
            s.loader.exec_module(m)
            lines = m.build()
            print("[fairy] 开机播报 %d 句 @ %s（已全部入队）"
                  % (len(lines), time.strftime("%H:%M:%S")), flush=True)
            try:
                for _i, _l in enumerate(lines, 1):
                    print("[fairy]   播报#%d %s" % (_i, _l[:40]), flush=True)
            except Exception:
                pass
            # 通道1：声音
            self.speaker.say_all(lines)
            # 通道2：屏幕
            self.after(0, lambda L=list(lines): self._show_text(L))
            # 通道3：文件（可回看）
            try:
                with open(fairy_root.log("briefing_last.txt"), "w", encoding="utf-8") as f:
                    f.write(time.strftime("%Y-%m-%d %H:%M:%S\n"))
                    f.write("\n".join(lines))
            except Exception:
                pass
            # 记住当前定位（用于菜单显示）
            try:
                self.city, _nc = m.resolves_city()
            except Exception:
                pass
            # ★【总时长实测】开一条轻量看门线程：队列排空且最后一条播完 -> 记一笔总时长。
            #   一条总数比七条各自的时间戳更直观；也让下次调优有基线。
            def _watch_brief(_n=len(lines), _t0=_bt0):
                try:
                    _last = -1
                    _idle = 0
                    while True:
                        time.sleep(1.0)
                        _sp = getattr(self.speaker, "spoken", 0)
                        if self.speaker.q.empty() and _sp == _last:
                            _idle += 1
                            if _idle >= 2:
                                print("[fairy] 播报结束 %d 句，总用时 %.1fs（%.1f 秒/句）"
                                      % (_n, time.time() - _t0,
                                         (time.time() - _t0) / max(1, _n)), flush=True)
                                return
                        else:
                            _idle = 0
                        _last = _sp
                except Exception:
                    return
            threading.Thread(target=_watch_brief, daemon=True, name="fairy-brief-watch").start()
        except Exception as e:
            print("[fairy] 播报失败: %s" % e, flush=True)


class StrataMixin:
    """StrataMixin（2026-10-23 从 FairyBall 拆出）"""

    def _strata_probe(self, force=False):
        """探测本地运算状态。★ 绝不抛异常、绝不久阻塞（菜单弹出前要调）。

        返回 {"t","state","loaded","ram_used","ram_total","text"}
          state: "running"（在跑）| "unloaded"（进程在、模型已卸，内存已回收）
                 | "stopped"（进程没了）| "unknown"（探测失败）
        ★ 15 秒缓存：绝大多数菜单弹出是零延迟的。
        """
        import socket as _sock
        now = time.time()
        c = getattr(self, "_strata_cache", None)
        if not force and c and now - c.get("t", 0) < 15:
            return c
        st = {"t": now, "state": "unknown", "loaded": None,
              "ram_used": None, "ram_total": None, "text": "状态未知"}
        try:                                              # 1) 端口（本机，秒回）
            _s = _sock.create_connection(("127.0.0.1", STRATA_PORT), 0.8)
            _s.close()
        except OSError:
            st.update(state="stopped", text="已停止")
            self._strata_cache = st
            return st
        try:                                              # 2) HTTP 状态（短超时）
            with urllib.request.urlopen(STRATA_STATUS_URL, timeout=1.5) as r:
                d = json.loads(r.read().decode("utf-8", "replace") or "{}")
            loaded = bool(d.get("loaded"))
            ram = ((d.get("machine") or {}).get("ram") or {})
            st["loaded"] = loaded
            st["ram_used"] = ram.get("used_gib")
            st["ram_total"] = ram.get("total_gib")
            st["state"] = "running" if loaded else "unloaded"
            st["text"] = "运行中" if loaded else "已停止（内存已回收）"
        except Exception as _e:
            st["text"] = "状态未知"
            print("[fairy] 本地运算状态探测失败: %s: %s" % (type(_e).__name__, _e), flush=True)
        self._strata_cache = st
        return st

    def _strata_loop(self):
        """后台每 20 秒刷新一次状态 —— ★ 这样右键菜单弹出时【零延迟】。"""
        while True:
            try:
                self._strata_probe(force=True)
            except Exception:
                pass
            time.sleep(20)

    def _strata_feedback(self, text, speak=None):
        """★ 线程安全反馈：只入队，由主线程 _tick 弹气泡 / 播报（Tk 跨线程不安全）。"""
        try:
            self._adv_bubbles.append(text)
            if speak:
                self._strata_says.append(speak)
        except Exception:
            pass
        print("[fairy] " + text, flush=True)

    def _strata_post(self, url, timeout=30):
        """POST 一个空 JSON（服务端 _own_page 要求 application/json + 无外来 Origin）。"""
        body = json.dumps({}).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace") or "{}")

    def _strata_stop(self, quiet=False):
        """右键：停止本地运算 —— 回收内存（可逆，进程还在，下次用到会自动加载）。

        quiet=True：开机自动释放（游戏模式）时用，全程不出声。
        """
        if getattr(self, "_strata_busy", False):
            self._bubble("上一个操作还在进行…", 3)
            return
        self._strata_busy = True
        if not quiet:
            self._strata_feedback("正在停止本地运算…")
        threading.Thread(target=self._strata_stop_work, args=(quiet,), daemon=True).start()

    def _strata_stop_work(self, quiet=False):
        """★ 后台线程：POST /v1/unload，并实测回收了多少内存。"""
        try:
            # ★ 2026-10-14【静默优化】Strata(8080) 没跑时直接判"本来就没在跑"，
            #   避免 POST /v1/unload 抛 10061 -> 「停止失败」噪音（health_check 会误报）。
            import socket as _sock
            _s = _sock.socket()
            _s.settimeout(0.5)
            _up = (_s.connect_ex(("127.0.0.1", 8080)) == 0)
            _s.close()
            if not _up:
                self._strata_feedback("本地运算本来就没在跑",
                                      speak=None if quiet else "本地运算本来就没在跑")
                return
            try:
                _before = _free_ram_gb()
            except Exception:
                _before = None
            d = self._strata_post(STRATA_UNLOAD_URL)
            status = d.get("status")
            time.sleep(4)                       # 等引擎真正退出、内存归还
            self._strata_cache = None           # 强制下次重探
            after = self._strata_probe(force=True)
            _after = _free_ram_gb()
            if status == "unloaded":
                if _before is not None and _after is not None:
                    msg = "已停止本地运算，回收 %.1f GB" % (_after - _before)
                else:
                    msg = "已停止本地运算"
                self._strata_feedback(msg, speak=None if quiet else "本地运算已停止")
            elif status == "not loaded":
                self._strata_feedback("本地运算本来就没在跑",
                                      speak=None if quiet else "本地运算本来就没在跑")
            elif status == "busy":
                self._strata_feedback("有请求正在跑，等它跑完再试")
            else:
                self._strata_feedback("停止结果：" + str(status))
        except Exception as _e:
            # ★ 2026-10-17【409 不是失败】Strata 的 /v1/unload 在"模型还没加载完"
            #   或"状态正在变"时返回 409 Conflict —— 意思是"现在不用卸/卸不了"，
            #   属于正常状态，不是故障。以前一律打「停止失败：HTTP Error 409」，
            #   体检的日志扫描会把它当报错（实测：球每次启动自动切游戏模式都会刷一条）。
            _s = str(_e)
            if "409" in _s:
                self._strata_feedback("本地运算已在闲状态，无需停止",
                                      speak="本地运算已经在闲状态了")
            else:
                self._strata_feedback("停止失败：" + _s[:60])
        finally:
            # ★★ 2026-10-18【修「切到游戏模式模型又起来了 / 退不干净」】
            #   根因：`/v1/unload` 只是让引擎**把模型卸载、内存留在进程里**，
            #   `server.py`（外壳）和 `strata.exe`（真引擎，占 40+ GB）**都还活着**。
            #   实测遇到：pid 4864 的 server.py 跑了 40 分钟、pid 5548 的 strata.exe
            #   吃着 42.1 GB，而 8080 还握着 —— 下次切组合自然又被"拉起来"了。
            #   所以 unload 之后必须再把这两层进程真正结束，并确认 8080 释放。
            self._strata_kill_all(quiet)
            self._strata_busy = False
            self._strata_cache = None

    def _strata_kill_all(self, quiet=True):
        """把 Strata 的两层进程都杀掉：server.py（外壳）+ strata.exe（真引擎）。

        ★ 2026-10-18 新增。为什么必须两层都杀：
          - `/v1/unload` 只卸模型，进程还在，内存不归还；
          - 只杀外侧 server.py，里面那个 strata.exe 会变孤儿继续占 40 GB；
          - 只杀 strata.exe，server.py 会自己把它重启（日志里见过
            "the engine had stopped; starting it again"）。
        """
        import subprocess as _sp
        _kw = {"creationflags": getattr(_sp, "CREATE_NO_WINDOW", 0),
               "stdout": _sp.DEVNULL, "stderr": _sp.DEVNULL}
        # 1) 先杀真引擎（不然外壳会把它拉回来）
        try:
            _sp.run(["taskkill", "/F", "/IM", "strata.exe", "/T"], timeout=20, **_kw)
        except Exception:
            pass
        # 2) 再杀外壳（按命令行匹配 Strata 的 server.py，别误杀别的 python）
        try:
            _ps = ("Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
                   "Where-Object { $_.CommandLine -like '*strata*server.py*' -or "
                   "($_.CommandLine -like '*serve\\server.py*' -and $_.CommandLine -like '*Strata*') } | "
                   "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
            _sp.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _ps],
                    timeout=25, **_kw)
        except Exception:
            pass
        # 3) 如果 8080 还被占着（说明有人监听），按端口再补一刀
        for _ in range(6):
            try:
                import socket as _sock
                _s = _sock.socket()
                _s.settimeout(0.4)
                _busy = (_s.connect_ex(("127.0.0.1", 8080)) == 0)
                _s.close()
            except Exception:
                _busy = False
            if not _busy:
                break
            try:
                _sp.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                         "$c = Get-NetTCPConnection -LocalPort 8080 -State Listen "
                         "-ErrorAction SilentlyContinue | Select-Object -First 1; "
                         "if ($c) { $p = Get-CimInstance Win32_Process -Filter "
                         "\"ProcessId = $($c.OwningProcess)\" -ErrorAction SilentlyContinue; "
                         "if ($p -and ($p.CommandLine -like '*Strata*' -or "
                         "$p.CommandLine -like '*server.py*')) { Stop-Process -Id "
                         "$($c.OwningProcess) -Force -ErrorAction SilentlyContinue } }"],
                        timeout=20, **_kw)
            except Exception:
                pass
            time.sleep(0.6)
        # 4) 核对结果，只在真没退干净时才提一句
        try:
            _left = _sp.run(["tasklist", "/FI", "IMAGENAME eq strata.exe", "/NH"],
                            capture_output=True, timeout=10,
                            creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
            _txt = (_left.stdout or b"").decode("gbk", "replace")
            if "strata.exe" in _txt and not quiet:
                self._bubble("Strata 引擎没能完全退出（可能卡在内核态，重启可清）", 6)
        except Exception:
            pass

    def _strata_start(self):
        """右键：启动本地运算 —— 两级：进程在就 POST /v1/load，进程没了就按真实命令行拉起。

        ★ 2026-10-17 晚【"日常全能"要能一起打开 Strata】加卡死保护：
          原来只要 `_strata_busy=True` 就一律拒绝（"上一个操作还在进行…"），
          而 busy 由 3~6 分钟的加载线程持有 —— 期间再切组合会被直接挡掉，
          表现就是"选了日常全能但 Strata 没起来"。
          现在：busy 超过 6 分钟视为上一次卡住，强制复位后照常拉起。
        """
        if getattr(self, "_strata_busy", False):
            _busy_s = time.time() - float(getattr(self, "_strata_busy_at", 0) or 0)
            if _busy_s < 360:
                self._bubble("上一个操作还在进行…（本地运算启动中，已 %.0f 秒）" % _busy_s, 3)
                return
            self._strata_busy = False        # 卡死复位，继续往下走
        self._strata_busy = True
        self._strata_busy_at = time.time()
        st = self._strata_probe(force=True)
        if st.get("state") == "running":
            self._strata_busy = False
            self._strata_feedback("本地运算已经在跑了")
            return
        if st.get("state") == "unloaded":
            self._strata_feedback("启动本地运算（加载模型，约 1~2 分钟）…")
        else:
            self._strata_feedback("正在拉起本地运算服务…")
        threading.Thread(target=self._strata_start_work, args=(st.get("state"),), daemon=True).start()

    def _strata_start_work(self, state):
        try:
            if state == "unloaded":
                # 进程还在 -> 让服务把引擎重新拉起来
                self._strata_feedback("已通知服务加载模型，请稍候…")
                try:
                    self._strata_post(STRATA_LOAD_URL, timeout=180)
                except Exception as _e:
                    self._strata_feedback("加载调用失败：" + str(_e)[:50])
            else:
                # 进程没了 -> 按当前机器配置的命令行静默拉起
                _cmd = _strata_cmd()
                if not _cmd:
                    self._strata_feedback("未找到 Strata 安装目录（可在 fairy_root.json 配 strata_dir）")
                    return
                import subprocess as _sp
                _lf = open(STRATA_LOG, "a", encoding="utf-8", errors="replace")
                _d = STRATA_DIR or _resolve_strata_dir()
                _sp.Popen(_cmd, stdout=_lf, stderr=_sp.STDOUT,
                          stdin=_sp.DEVNULL, cwd=_d or os.getcwd(),
                          creationflags=0x00000008 | 0x00000200 | 0x08000000,
                          close_fds=True)
                self._strata_feedback("已拉起本地运算服务，等待就绪…")
                time.sleep(8)
            # 轮询等就绪（最多约 3 分钟）
            ok = False
            # ★ 2026-10-17：36 次 × 5s = 180s 对 Strata 不够（实测加载 40 GB
            #   专家缓存要 3~5 分钟），会误报"还没就绪"。放宽到 72 次 = 360s，
            #   与组合切换里的 _wait_port_up(360) 对齐。
            for _ in range(72):
                self._strata_cache = None
                st = self._strata_probe(force=True)
                if st.get("state") == "running":
                    ok = True
                    break
                time.sleep(5)
            self._strata_cache = None
            if ok:
                self._strata_feedback("本地运算已就绪", speak="本地运算已就绪")
            else:
                s2 = self._strata_probe(force=True)
                self._strata_feedback("还没就绪（当前：" + str(s2.get("text")) + "），再等一会儿看看")
        except Exception as _e:
            self._strata_feedback("启动失败：" + str(_e)[:60])
        finally:
            self._strata_busy = False
            self._strata_cache = None


class AiCfgMixin:
    """AiCfgMixin（2026-10-23 从 FairyBall 拆出）"""

    def _ai_cfg(self):
        """读取 fairy.json 的 ai 段（后端 / api_key / 模型 / 接口地址）。"""
        try:
            p = getattr(fairy_root, "CONFIG", None)
            c = {}
            if p and os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    c = json.load(f)
            return dict(c.get("ai") or {})
        except Exception:
            return {}

    def _save_ai_cfg(self, patch):
        """把 patch 合入 fairy.json 的 ai 段并保存（保留其它配置键）。"""
        try:
            path = fairy_root.CONFIG
            c = {}
            read_ok = not os.path.exists(path)
            if os.path.exists(path):
                try:
                    # utf-8-sig: survive the UTF-8 BOM PowerShell writes out.
                    with open(path, "r", encoding="utf-8-sig") as f:
                        c = json.load(f)
                    read_ok = True
                except Exception:
                    c = {}
            # 2026-10-21 FIX: if the read failed, write nothing.
            # Before this, a BOM made json.load throw, c stayed {},
            # and the dump below replaced the whole file with only the
            # "ai" section -- every other section was silently lost.
            if not read_ok:
                self._bubble("配置读取失败，为免覆盖已取消保存", 5)
                return False
            ai = dict(c.get("ai") or {})
            ai.update(patch)
            c["ai"] = ai
            with open(path, "w", encoding="utf-8") as f:
                json.dump(c, f, ensure_ascii=False, indent=2)
            # 让 fairy_root.CFG 里也能读到新值（本进程内立即生效）
            try:
                fairy_root.CFG["ai"] = ai
            except Exception:
                pass
            return True
        except Exception as e:
            self._bubble("保存 AI 配置失败：" + str(e)[:60], 4)
            return False

    def _ai_probe_models(self):
        """探测可用模型：Strata /v1/models + Ollama /api/tags + LLM·llama.cpp。返回 (标题, 列表)。"""
        found = []
        # Strata（本地运算，8080）
        try:
            req = urllib.request.Request(STRATA_STATUS_URL.replace("/v1/status", "/v1/models"),
                                         headers={"content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=6) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            for m in (d.get("data") or []):
                found.append(("Strata", m.get("id") or m.get("name") or str(m)[:40]))
        except Exception:
            pass
        # Ollama（11434）
        try:
            with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=6) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            for m in (d.get("models") or []):
                found.append(("Ollama", m.get("name") or m.get("model") or ""))
        except Exception:
            pass
        # LLM·llama.cpp（8085 优先，8080 备用）
        for _port in (8085, 8080):
            try:
                req = urllib.request.Request("http://127.0.0.1:%d/v1/models" % _port,
                                             headers={"content-type": "application/json"})
                with urllib.request.urlopen(req, timeout=3) as r:
                    d = json.loads(r.read().decode("utf-8", "replace"))
                for m in (d.get("data") or []):
                    found.append(("LLM(:%d)" % _port, m.get("id") or m.get("model") or str(m)[:40]))
            except Exception:
                pass
        return found

    def _ai_set_key(self):
        """弹窗设置 API Key（不回显明文，只显示已配置与否）。"""
        import tkinter.simpledialog as _sd
        cur = self._ai_cfg().get("api_key") or ""
        masked = ("（已配置，共 %d 位）" % len(cur)) if cur else "（未配置）"
        val = _sd.askstring("AI 大模型 - API Key", "粘贴你的 API Key（当前%s）：" % masked,
                            parent=self)
        if val is None:
            return
        val = val.strip()
        if not val:
            self._bubble("API Key 已清空", 3)
        else:
            self._bubble("API Key 已保存（%d 位）" % len(val), 3)
        self._save_ai_cfg({"api_key": val})

    def _doubao_cfg(self):
        """读 fairy.json online.doubao 段。"""
        try:
            p = getattr(fairy_root, "CONFIG", None)
            c = {}
            if p and os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    c = json.load(f)
            return dict(((c.get("online") or {}).get("doubao") or {}))
        except Exception:
            return {}

    def _save_doubao_cfg(self, patch):
        """合入 fairy.json online.doubao 段（保留其它配置键）。"""
        try:
            path = fairy_root.CONFIG
            c = {}
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        c = json.load(f)
                except Exception:
                    c = {}
            onl = dict(c.get("online") or {})
            dou = dict(onl.get("doubao") or {})
            dou.update(patch)
            onl["doubao"] = dou
            c["online"] = onl
            with open(path, "w", encoding="utf-8") as f:
                json.dump(c, f, ensure_ascii=False, indent=2)
            try:
                fairy_root.CFG["online"] = onl
            except Exception:
                pass
            return True
        except Exception as e:
            self._bubble("保存豆包配置失败：" + str(e)[:60], 4)
            return False

    def _set_doubao_key(self):
        """★ 2026-10-12【豆包（火山方舟）】填 API Key 启用在线豆包大脑。
        本地引擎不可用时，ear 自动降级到豆包回答（无需 DSH）。"""
        import tkinter.simpledialog as _sd
        _cur = (self._doubao_cfg().get("api_key") or "").strip()
        _masked = ("（已配置 %d 位）" % len(_cur)) if _cur else "（未配置）"
        _key = _sd.askstring("豆包 - 火山方舟 API Key",
                             "粘贴你的豆包 API Key（当前%s）：\n"
                             "获取：火山方舟控制台 → API Key 管理" % _masked,
                             parent=self)
        if _key is None:
            return
        _key = _key.strip()
        self._save_doubao_cfg({"enabled": bool(_key), "api_key": _key})
        self._bubble(("豆包已启用（%d 位 Key）" % len(_key)) if _key else "豆包已停用", 3)

    def _set_doubao_model(self):
        """弹窗设置豆包模型名（默认 doubao-seed-1-6-250615）。"""
        import tkinter.simpledialog as _sd
        _cur = self._doubao_cfg().get("model") or "doubao-seed-1-6-250615"
        _val = _sd.askstring("豆包 - 模型", "模型名（当前：%s）：\n"
                             "例：doubao-seed-1-6-250615 / doubao-pro-32k" % _cur,
                             parent=self, initialvalue=_cur)
        if _val is None:
            return
        _val = _val.strip()
        if _val:
            self._save_doubao_cfg({"model": _val})
            self._bubble("豆包模型已设为：" + _val, 3)

    def _ai_set_model(self):
        """★ 2026-10-12【本地模型选择】统一风格弹窗 + 下拉选择（探测到的模型）。"""
        win, wrap = self._dialog("本地模型选择", 440, 300)
        tk.Label(wrap, text="从下拉选择本机探测到的模型：", bg="#f2f3f5",
                 fg="#6e7888", font=("SimHei", 13)).pack(anchor="w", pady=(6, 4))
        import tkinter.ttk as _ttk
        _cur = self._ai_cfg().get("model") or ""
        _opts = [_cur] if _cur else []
        _src = {}
        try:
            for _s, _n in self._ai_probe_models():
                if _n not in _src:
                    _src[_n] = _s
                    _opts.append(_n)
        except Exception:
            pass
        _var = tk.StringVar(value=_cur or (_opts[0] if _opts else ""))
        _cb = _ttk.Combobox(wrap, textvariable=_var, values=_opts or ["（未探测到模型）"],
                            font=("SimHei", 13), state="readonly", width=28)
        _cb.pack(anchor="w", pady=(2, 8))
        tk.Label(wrap, text="也可以手动输入模型名（不选下拉就自己填）：", bg="#f2f3f5",
                 fg="#6e7888", font=("SimHei", 13)).pack(anchor="w")
        _man = tk.Entry(wrap, font=("SimHei", 13), width=30)
        _man.pack(anchor="w", pady=(2, 10))

        def _ok():
            _v = (_var.get() or "").strip()
            _m = (_man.get() or "").strip()
            _fin = _m or _v or ""
            self._save_ai_cfg({"model": _fin})
            self._bubble("本地模型已设为：" + (_fin or "（空，用引擎默认）"), 3)
            win.destroy()
        _row = tk.Frame(wrap, bg="#f2f3f5")
        _row.pack(anchor="w", pady=(6, 0))
        self._dlg_btn(_row, "保存", _ok)
        self._dlg_btn(_row, "取消", win.destroy)
        win.focus_force()

    def _ai_set_url(self):
        """弹窗设置自定义接口地址（OpenAI 兼容 base_url）。"""
        import tkinter.simpledialog as _sd
        cur = self._ai_cfg().get("base_url") or ""
        val = _sd.askstring("AI 大模型 - 接口地址", "OpenAI 兼容接口地址（当前：%s）" % (cur or "未设置"),
                            parent=self, initialvalue=cur)
        if val is None:
            return
        val = val.strip()
        self._save_ai_cfg({"base_url": val})
        self._bubble("接口地址已保存：" + (val or "（空）"), 3)

    def _ai_probe_now(self):
        def _work():
            found = self._ai_probe_models()
            if not found:
                self._bubble("没探测到模型（Strata / Ollama / LLM 都没在跑）", 4)
                return
            lines = ["可用模型："]
            for src, name in found[:12]:
                lines.append("· %s: %s" % (src, name))
            self._bubble("\n".join(lines), 6)
        threading.Thread(target=_work, daemon=True).start()

    def _ai_set_net_model(self):
        """★ 2026-10-11【网络选项卡】设置网络模型名（DSH / 自定义接口用的模型）。"""
        import tkinter.simpledialog as _sd
        cur = self._ai_cfg().get("net_model") or ""
        val = _sd.askstring("AI 大模型 - 网络模型",
                            "网络模型名称（当前：%s）" % (cur or "未设置，如 deepseek-chat"),
                            parent=self, initialvalue=cur)
        if val is None:
            return
        val = val.strip()
        self._save_ai_cfg({"net_model": val})
        self._bubble("网络模型已设为：" + (val or "（空）"), 3)


class VisionMixin:
    """VisionMixin（2026-10-23 从 FairyBall 拆出）"""

    def _vision_ocr(self):
        """★ 2026-10-12【选框OCR】微信风格截图选区域 -> 识别区域文字（不再全屏）。"""
        import subprocess as _sp

        def _work():
            self.say("拖拽选择要识别的区域", "doing")
            try:
                r = _rnw([sys.executable,
                             os.path.join(self._tools_dir(), "fairy_capture.py"),
                             "--mode", "ocr"], capture_output=True, timeout=120,
                            text=True, encoding="utf-8", errors="replace")
                d = json.loads((r.stdout or "{}").strip().splitlines()[-1] or "{}")
                if not d.get("ok"):
                    self.say("已取消" if "cancel" in str(d) else "识别失败", "warn")
                    return
                lines = d.get("lines") or []
                if not lines:
                    self.say("选区里没有识别到文字", "ok")
                    self._bubble("选区里没识别到文字", 3)
                    return
                self.say("识别到%d行文字" % len(lines), "ok")
                self._bubble("识别到 %d 行：%s" % (len(lines),
                                                " | ".join(lines)[:200]), 10)
            except Exception as e:
                self.say("识别失败", "warn")
                self._bubble("OCR 失败：" + str(e)[:60], 4)
        threading.Thread(target=_work, daemon=True).start()

    def _vision_ocr_region(self):
        """（已下线）区域截图识别改为微信风格选框，入口见 _vision_ocr。"""

    def _vision_detect(self):
        """★ 2026-10-12【选框识别物体】微信风格截图选区域 -> YOLO 检测区域物体。"""
        import subprocess as _sp

        def _work():
            self.say("拖拽选择要识别的区域", "doing")
            try:
                r = _rnw([sys.executable,
                             os.path.join(self._tools_dir(), "fairy_capture.py"),
                             "--mode", "detect"], capture_output=True, timeout=150,
                            text=True, encoding="utf-8", errors="replace")
                d = json.loads((r.stdout or "{}").strip().splitlines()[-1] or "{}")
                if not d.get("ok"):
                    self.say("已取消" if "cancel" in str(d) else "识别失败", "warn")
                    return
                objs = d.get("objects") or []
                if not objs:
                    self.say("选区里没有检测到物体", "ok")
                    self._bubble("选区里没检测到物体", 3)
                    return
                labels = "、".join(o["label"] for o in objs[:8])
                self.say("检测到%d个物体：%s" % (len(objs), labels), "ok")
                self._bubble("检测到 %d 个物体：%s" % (len(objs),
                                                   "、".join(o["label"] for o in objs[:10])), 10)
            except Exception as e:
                self.say("识别失败", "warn")
                self._bubble("检测失败：" + str(e)[:60], 4)
        threading.Thread(target=_work, daemon=True).start()

    def _overlay_ocr(self):
        """常驻屏幕取字：统一风格弹窗（居中+黑体+柔和色）。"""
        import subprocess as _sp
        win = tk.Toplevel(self)
        win.title("常驻屏幕取字")
        win.attributes("-topmost", True)
        win.configure(bg="#ffffff")
        win.resizable(False, False)
        _W, _H = 430, 250
        win.geometry("%dx%d+%d+%d" % (_W, _H,
                                      max(0, (win.winfo_screenwidth() - _W) // 2),
                                      max(0, (win.winfo_screenheight() - _H) // 2)))
        try:
            import ctypes
            _u = ctypes.windll.user32
            _g = ctypes.windll.gdi32
            _rgn = _g.CreateRoundRectRgn(0, 0, _W + 1, _H + 1, 16, 16)
            if _rgn:
                _u.SetWindowRgn(win.winfo_id(), _rgn, True)
        except Exception:
            pass
        _wrap = tk.Frame(win, bg="#ffffff")
        _wrap.pack(fill="both", expand=True, padx=24, pady=16)
        tk.Label(_wrap, text="常驻屏幕取字", bg="#ffffff", fg="#52749b",
                 font=("SimHei", 16, "bold")).pack(anchor="w", pady=(0, 8))
        tk.Label(_wrap, text="输入识别区域（屏幕坐标）：x y 宽 高",
                 bg="#ffffff", fg="#6e7888", font=("SimHei", 13)).pack(anchor="w")
        tk.Label(_wrap, text="例如：200 100 900 600", bg="#ffffff", fg="#8d97a8",
                 font=("SimHei", 12)).pack(anchor="w", pady=(2, 8))
        _v = tk.StringVar()
        tk.Entry(_wrap, textvariable=_v, font=("SimHei", 14), width=26).pack(anchor="w")

        def _ok():
            val = _v.get().strip()
            if not val:
                win.destroy()
                return
            parts = val.split()
            if len(parts) != 4 or not all(p.lstrip("-").isdigit() for p in parts):
                self._bubble("格式不对：需要 x y 宽 高 四个数字", 4)
                return
            win.destroy()
            try:
                _sp.Popen([sys.executable, os.path.join(self._tools_dir(), "fairy_overlay_ocr.py"),
                           "--region"] + parts, creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
                self.say("常驻取字窗口已开启", "ok")
            except Exception as e:
                self._bubble("启动失败：" + str(e)[:60], 4)

        _row = tk.Frame(_wrap, bg="#ffffff")
        _row.pack(fill="x", pady=(14, 0))
        tk.Button(_row, text="OK", command=_ok, bg="#e4edf6", fg="#46536a",
                  activebackground="#cdd9e6", font=("SimHei", 15, "bold"), bd=0,
                  highlightthickness=0, cursor="hand2", width=8).pack(side="left", padx=4)
        tk.Button(_row, text="Cancel", command=win.destroy, bg="#ffffff", fg="#46536a",
                  activebackground="#f2f3f5", font=("SimHei", 14), bd=0,
                  highlightthickness=1, highlightbackground="#d8dfe8", cursor="hand2",
                  width=8).pack(side="left", padx=4)
        win.bind("<Return>", lambda _e: _ok())
        win.focus_force()

    def _screenshot_anno(self):
        """★ 2026-10-12【微信风格截图标注】全屏遮罩拖拽选区域 -> 选区标注。
        用 pythonw 启动（无控制台），选区后进入标注窗口（画框/箭头/文字）。"""
        try:
            _d = self._tools_dir()
            _w = os.path.join(os.path.dirname(_d), "pythonw.exe")
            _p = os.path.join(_d, "fairy_capture.py")
            if not os.path.exists(_w):
                _w = sys.executable
            _rnw([_w, _p, "--mode", "annotate"], timeout=3)
            self._bubble("拖拽选择截图区域，松开后进入标注", 3)
        except Exception as _e:
            self._bubble("截图标注启动失败：" + str(_e)[:50], 4)

    def _color_pick(self):
        """屏幕取色器（独立窗口进程，点击复制颜色代码）。"""
        import subprocess as _sp
        try:
            _sp.Popen([sys.executable, os.path.join(self._tools_dir(), "fairy_color.py")],
                      creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
        except Exception as e:
            self._bubble("取色器启动失败：" + str(e)[:60], 4)


class IotMixin:
    """IotMixin（2026-10-23 从 FairyBall 拆出）"""

    def _iot_scan(self):
        """★ 2026-10-12【无窗扫描】扫描进度直接画在球右侧进度条面板；
        完成后语音+气泡播报结果，有新设备自动连接。不再弹任何窗口。
        防重入：扫描/连接期间重复点击只提示。"""
        import subprocess as _sp
        if getattr(self, "_iot_scanning", False):
            self._bubble("正在扫描设备，请稍候…", 3)
            return
        self._iot_scanning = True

        def _work():
            self.say("设备正在自检中，扫描局域网、蓝牙和智能设备", "doing")
            self._ball_progress(3, "设备自检 · 启动扫描", True)
            _json_buf = []
            try:
                _p = _sp.Popen([sys.executable,
                                os.path.join(self._tools_dir(), "fairy_iot.py"),
                                "--scan", "all", "--json", "--progress"],
                               stdout=_sp.PIPE, stderr=_sp.DEVNULL, text=True,
                               encoding="utf-8", errors="replace", bufsize=1,
                               creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
                for _line in _p.stdout:
                    _line = (_line or "").strip()
                    if not _line:
                        continue
                    if _line.startswith("P|"):
                        _ps = _line[2:].split("|")
                        if len(_ps) >= 3:
                            _st, _pct_v, _n = _ps[0], int(_ps[1]), int(_ps[2])
                            self._ball_progress(_pct_v, "设备自检 · %s · 已发现 %d 个"
                                                % (_st, _n), True)
                    else:
                        _json_buf.append(_line)
                _p.wait(timeout=200)
            except Exception as _pe:
                self._iot_scanning = False
                self._ball_progress(0, "", False)
                self.say("设备扫描失败", "warn")
                self._bubble("扫描失败：" + str(_pe)[:60], 4)
                return
            try:
                d = json.loads("".join(_json_buf) or "{}")
            except Exception:
                d = {}
            if not d.get("ok"):
                self._iot_scanning = False
                self._ball_progress(0, "", False)
                self.say("扫描失败", "warn")
                self._bubble("扫描失败：" + str(d.get("error"))[:60], 4)
                return
            newd = d.get("new_devs") or []
            total = d.get("count") or 0
            self._ball_progress(0, "", False)
            if not newd:
                self._iot_scanning = False
                self.say("没有新设备，当前%d个设备在线" % total, "ok")
                self._bubble("没有新设备，当前 %d 个设备在线" % total, 5)
                return
            self._ball_progress(60, "设备自检 · 连接 %d 个新设备…" % len(newd), True)
            try:
                r = _rnw([sys.executable,
                          os.path.join(self._tools_dir(), "fairy_iot.py"),
                          "--connect", "--yes", "--json"], capture_output=True,
                         timeout=180, text=True, encoding="utf-8", errors="replace")
                dd = json.loads(r.stdout or "{}")
            except Exception as e:
                dd = {"ok": False, "error": str(e)}
            self._ball_progress(0, "", False)
            self._iot_scanning = False
            if not dd.get("ok"):
                self.say("连接失败", "warn")
                self._bubble("连接失败：" + str(dd.get("error"))[:60], 4)
                return
            groups = {}
            for x in newd:
                k = self._kind_cn(x.get("kind"))
                groups.setdefault(k, []).append(str(x.get("name") or x.get("id")))
            parts = "。".join("发现%s%d个" % (k, len(v)) for k, v in groups.items())
            names = "、".join("%s%s" % (g, "：" + "、".join(vs[:3]))
                                  for g, vs in groups.items())
            self.say("已连接%d个新设备，%s" % (len(newd), parts), "ok")
            self._bubble("%s\n%s" % (parts, names), 8)
            # ★ 2026-10-12【未标记提醒】新设备里没起名的，语音+气泡提醒去标记
            _untag = [x for x in newd if not (x.get("tag") or "").strip()]
            if _untag:
                self.say("还有%d个新设备未标记，可以在控制面板的设备列表里给它们起名字"
                         % len(_untag), "warn")
                self._bubble("%d 个新设备未标记：面板→网络与设备→设备列表→点「标记」起名"
                             % len(_untag), 8)
        threading.Thread(target=_work, daemon=True).start()

    def _iot_devices(self):
        """★ 2026-10-12【设备列表】统一风格窗口：自动分「已标记 / 未标记」，
        未标记设备行尾带「标记」按钮，点一下直接起名（不再手动输 ID）。"""
        import subprocess as _sp
        import tkinter.simpledialog as _sd

        def _work():
            try:
                r = _rnw([sys.executable, os.path.join(self._tools_dir(), "fairy_iot.py"),
                             "--devices", "--json"], capture_output=True, timeout=30,
                            text=True, encoding="utf-8", errors="replace")
                d = json.loads(r.stdout or "{}")
                devs = d.get("devices") or []
            except Exception as e:
                self._bubble("查询设备失败：" + str(e)[:60], 4)
                return

            win, wrap = self._dialog("设备列表（分类 / 标记）", 520, 420)
            _cv = tk.Canvas(wrap, bg="#f2f3f5", highlightthickness=0)
            _sb = tk.Scrollbar(wrap, orient="vertical", command=_cv.yview)
            _cv.configure(yscrollcommand=_sb.set)
            _sb.pack(side="right", fill="y")
            _cv.pack(side="left", fill="both", expand=True)
            _inner = tk.Frame(_cv, bg="#f2f3f5")
            _wid = _cv.create_window((0, 0), window=_inner, anchor="nw")

            def _fit(_e=None):
                _cv.itemconfigure(_wid, width=max(120, _cv.winfo_width() - 8))
                _cv.configure(scrollregion=_cv.bbox("all"))
            _cv.bind("<Configure>", _fit)
            # ★ 2026-10-18：内容尺寸变化也要重算 scrollregion（面板复用/列表变长时
            #   只绑 canvas 的 <Configure> 会停在旧值 -> 滚动条拖不到底）。
            _inner.bind("<Configure>", _fit)

            def _bind_wheel(_w):
                _w.bind("<MouseWheel>", lambda _ev: _cv.yview_scroll(
                    int(-1 * (_ev.delta / 120)), "units"))
                for _c in _w.winfo_children():
                    _bind_wheel(_c)

            if not devs:
                tk.Label(_inner, text="还没有已连接设备（先去「扫描新设备」）",
                         bg="#f2f3f5", fg="#6e7888",
                         font=("SimHei", 13)).pack(anchor="w", pady=10)
                _bind_wheel(_inner)
                return

            _tagged = [x for x in devs if (x.get("tag") or "").strip()]
            _untagged = [x for x in devs if not (x.get("tag") or "").strip()]

            _KCN = {"lan": "局域网", "bt": "蓝牙", "mdns": "组播/投屏",
                    "serial": "串口", "nearlink": "星闪", "nearby": "近距离"}

            def _fmt(x):
                # ★ 2026-10-12【自动区分】每行带厂商 + 通道 + 类型 + IP + MAC(后6位)，
                #   同局域网设备凭 MAC/IP/厂商一眼区分谁是谁。
                _n = x.get("tag") or x.get("name") or x.get("id") or "?"
                _t = x.get("type") or ""
                _v = x.get("vendor") or ""
                _k = _KCN.get(str(x.get("kind") or x.get("adapter") or ""), "")
                _ip = str(x.get("ip") or "").strip()
                _mac = str(x.get("id") or "")
                for _p in ("ble:", "lan:", "nearlink:", "nearby:", "serial:"):
                    _mac = _mac.replace(_p, "")
                _mac = (_mac[-6:] if len(_mac) > 6 else _mac)
                _bits = [b for b in (_v, _k, _t, _ip, _mac) if b and b != "未知设备"]
                _s = " · ".join(_bits)
                return ("%s（%s）" % (_n, _s)) if _s else str(_n)

            def _do_tag(x):
                _tag = _sd.askstring("标记设备", "给「%s」起个名字（例如：卧室灯）："
                                     % (x.get("name") or x.get("id")), parent=win)
                if not (_tag or "").strip():
                    return
                try:
                    _r2 = _rnw([sys.executable,
                                   os.path.join(self._tools_dir(), "fairy_iot.py"),
                                   "--tag", str(x.get("id")), _tag.strip(), "--json"],
                                  capture_output=True, timeout=30, text=True,
                                  encoding="utf-8", errors="replace")
                    _d2 = json.loads(_r2.stdout or "{}")
                    self._bubble(("已标记：%s" % _tag.strip()) if _d2.get("ok")
                                 else ("标记失败：" + str(_d2.get("msg") or "")[:50]), 4)
                    win.destroy()
                    self._iot_devices()      # 刷新列表
                except Exception as _e2:
                    self._bubble("标记失败：" + str(_e2)[:60], 4)

            def _sec(title, items, can_tag):
                if not items:
                    return
                tk.Label(_inner, text=title + "（%d）" % len(items), bg="#f2f3f5",
                         fg="#52749b", font=("SimHei", 15, "bold")
                         ).pack(anchor="w", pady=(10, 4))
                for _x in items:
                    _rowf = tk.Frame(_inner, bg="#f2f3f5")
                    _rowf.pack(fill="x", pady=2)
                    tk.Label(_rowf, text=_fmt(_x), bg="#f2f3f5", fg="#46536a",
                             font=("SimHei", 12), width=38, anchor="w"
                             ).pack(side="left", padx=4)
                    if can_tag:
                        _img = _pil_round("标记", 64, 34, 10, "#ffffff", "#52749b",
                                          True, "#dfe3ea")[0]
                        _b = tk.Button(_rowf, image=_img, bd=0, relief="flat",
                                       highlightthickness=0, bg="#f2f3f5",
                                       activebackground="#f2f3f5", cursor="hand2",
                                       command=lambda x=_x: _do_tag(x))
                        _b.image = _img
                        _b.pack(side="right", padx=4)
                    else:
                        tk.Label(_rowf, text="✓ 已标记", bg="#f2f3f5", fg="#8d97a8",
                                 font=("SimHei", 12)).pack(side="right", padx=6)

            _sec("已标记", _tagged, False)
            _sec("未标记", _untagged, True)
            _bind_wheel(_inner)
        threading.Thread(target=_work, daemon=True).start()

    def _iot_tag(self):
        """★ 标记设备：选择设备后输入名字/用途（使用者告诉 Fairy 这是什么）。"""
        import subprocess as _sp
        import tkinter.simpledialog as _sd

        def _work():
            try:
                r = _rnw([sys.executable, os.path.join(self._tools_dir(), "fairy_iot.py"),
                             "--devices", "--json"], capture_output=True, timeout=30,
                            text=True, encoding="utf-8", errors="replace")
                d = json.loads(r.stdout or "{}")
                devs = d.get("devices") or []
                if not devs:
                    self._bubble("还没有已连接设备（先去「扫描新设备」）", 4)
                    return
                menu = "\n".join("%s  %s（%s）" %
                                 (x.get("id"), x.get("tag") or x.get("name") or x.get("id"),
                                  x.get("type") or x.get("kind") or "?") for x in devs[:15])
                did = _sd.askstring("标记设备 - 选择",
                                    "输入要标记设备的 ID：\n\n%s" % menu, parent=self)
                if did is None or not did.strip():
                    return
                did = did.strip()
                hit = next((x for x in devs if x.get("id") == did), None)
                if not hit:
                    self._bubble("没找到设备：%s" % did, 4)
                    return
                tag = _sd.askstring("标记设备 - 起名",
                                    "给「%s」起个名字或说明（例如：卧室小米温湿度计）："
                                    % (hit.get("name") or did), parent=self)
                if tag is None or not tag.strip():
                    return
                r2 = _rnw([sys.executable, os.path.join(self._tools_dir(), "fairy_iot.py"),
                              "--tag", did, tag.strip(), "--json"], capture_output=True,
                             timeout=30, text=True, encoding="utf-8", errors="replace")
                d2 = json.loads(r2.stdout or "{}")
                if d2.get("ok"):
                    self.say("已标记：%s" % tag.strip(), "ok")
                    self._bubble("已标记：%s" % tag.strip(), 4)
                else:
                    self._bubble("标记失败：" + str(d2.get("msg") or "")[:60], 4)
            except Exception as e:
                self._bubble("标记失败：" + str(e)[:60], 4)
        threading.Thread(target=_work, daemon=True).start()

    @staticmethod
    def _kind_cn(kind):
        return {"lan": "局域网", "bt": "蓝牙", "mdns": "智能设备", "serial": "串口",
                "nearlink": "星闪", "nearby": "星闪"}.get(kind, kind or "设备")


class AlarmMixin:
    """AlarmMixin（2026-10-23 从 FairyBall 拆出）"""

    def _remind_add(self):
        """★ 2026-10-12【定时提醒】统一风格弹窗：分钟 + 内容。"""
        win, wrap = self._dialog("定时提醒", 420, 260)
        tk.Label(wrap, text="多久后提醒（分钟）？", bg="#f2f3f5", fg="#6e7888",
                 font=("SimHei", 13)).pack(anchor="w", pady=(6, 2))
        _mins = tk.Entry(wrap, width=10, font=("SimHei", 13))
        _mins.insert(0, "10")
        _mins.pack(anchor="w", pady=(0, 10))
        tk.Label(wrap, text="提醒内容", bg="#f2f3f5", fg="#6e7888",
                 font=("SimHei", 13)).pack(anchor="w", pady=(0, 2))
        _msg = tk.Entry(wrap, width=34, font=("SimHei", 13))
        _msg.insert(0, "到时间了")
        _msg.pack(anchor="w")

        def _ok():
            try:
                mins = float(_mins.get().strip())
            except ValueError:
                self._bubble("分钟数不对", 3)
                return
            t = _msg.get().strip() or "到时间了"
            self._reminders.append((time.time() + mins * 60, t))
            self.say("好的，%g 分钟后提醒你" % mins, "ok")
            self._bubble("已设提醒：%g 分钟后「%s」" % (mins, t), 4)
            win.destroy()
        _row = tk.Frame(wrap, bg="#f2f3f5")
        _row.pack(anchor="w", pady=(12, 0))
        self._dlg_btn(_row, "确定", _ok)
        self._dlg_btn(_row, "取消", win.destroy)
        win.bind("<Return>", lambda _ev: _ok())
        _mins.focus_force()

    def _remind_loop(self):
        """提醒后台线程：到点播报（定时提醒 + 待办到期，已播的待办去重）。"""
        _todo_done = set()
        while True:
            try:
                now = time.time()
                due = [r for r in self._reminders if r[0] <= now]
                if due:
                    self._reminders = [r for r in self._reminders if r[0] > now]
                    for _, msg in due:
                        self.say("提醒：" + msg, "alert")
                        self._bubble("提醒：" + msg, 10)
                try:
                    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                    import fairy_todo as _ft
                    for _t in _ft.due():
                        _tid = _t.get("id")
                        if _tid not in _todo_done:
                            _todo_done.add(_tid)
                            self.say("待办到时间：" + _t.get("text", ""), "alert")
                            self._bubble("待办：" + _t.get("text", ""), 10)
                except Exception:
                    pass
            except Exception:
                pass
            time.sleep(1)   # 1 秒轮询：到点即报，误差不超过 1 秒

    def _dnd_toggle(self):
        """免打扰开关：静音所有语音播报（气泡保留）。"""
        self._dnd = not getattr(self, "_dnd", False)
        if self._dnd:
            self._bubble("已开启免打扰（语音播报静音）", 4)
        else:
            self._bubble("已关闭免打扰", 4)
            self.say("免打扰已关闭", "ok")

    def _alert_loop(self):
        """★ 2026-10-11【天气预警】每 30 分钟查一次，预警变化才播（免打扰/关闭时静音）。"""
        while True:
            try:
                with open(fairy_root.CONFIG, encoding="utf-8") as _f:
                    _j = json.load(_f)
                _on = bool((_j.get("briefing") or {}).get("weather_alert", True))
                if _on:
                    import fairy_alert as _fa
                    _ws = _fa.check()
                    _s = "|".join(_ws)
                    if _ws and _s != getattr(self, "_alert_last", ""):
                        self._alert_last = _s
                        if not getattr(self, "_dnd", False):
                            self.say("提醒：" + "；".join(_ws[:2]), "alert")
                            self._bubble("天气预警：" + "；".join(_ws), 10)
            except Exception:
                pass
            time.sleep(1800)

    def _night_loop(self):
        """★ 2026-10-11【夜间模式】按 fairy.json 的 dnd 段时段自动开关免打扰。"""
        while True:
            try:
                with open(fairy_root.CONFIG, encoding="utf-8") as _f:
                    _dn = (json.load(_f).get("dnd") or {})
                if _dn.get("night"):
                    _st = (_dn.get("start") or "22:00").strip()
                    _en = (_dn.get("end") or "07:00").strip()
                    _now = time.strftime("%H:%M")
                    _in = (_st <= _now) or (_now < _en)   # 跨天时段
                    if _in != bool(getattr(self, "_dnd", False)):
                        self._dnd = _in
            except Exception:
                pass
            time.sleep(60)

    def _alert_toggle(self):
        """★ 2026-10-11【天气预警推送开关】存 fairy.json briefing.weather_alert。"""
        _now = True
        try:
            with open(fairy_root.CONFIG, encoding="utf-8") as _f:
                _j = json.load(_f)
            _now = bool((_j.get("briefing") or {}).get("weather_alert", True))
            (_j.setdefault("briefing", {}))["weather_alert"] = not _now
            with open(fairy_root.CONFIG, "w", encoding="utf-8") as _f:
                json.dump(_j, _f, ensure_ascii=False, indent=2)
            self._bubble("天气预警推送已" + ("关闭" if _now else "开启"), 3)
        except Exception as _e:
            self._bubble("操作失败：" + str(_e)[:50], 4)

    def _night_set(self):
        """★ 2026-10-12【夜间模式设置】统一风格弹窗：时段 + 开关。"""
        _cur = {"night": True, "start": "22:00", "end": "07:00"}
        try:
            with open(fairy_root.CONFIG, encoding="utf-8") as _f:
                _cur.update((json.load(_f).get("dnd") or {}))
        except Exception:
            pass
        win, wrap = self._dialog("夜间模式", 420, 280)
        _bg = "#f2f3f5"
        _on = tk.BooleanVar(value=bool(_cur.get("night", True)))
        tk.Checkbutton(wrap, text="开启夜间模式（到点自动静音播报）", variable=_on,
                       anchor="w", bg=_bg, fg="#46536a", activebackground=_bg,
                       selectcolor=_bg, font=("SimHei", 12)
                       ).pack(fill="x", pady=6)
        _st = tk.StringVar(value=_cur.get("start", "22:00"))
        _en = tk.StringVar(value=_cur.get("end", "07:00"))
        tk.Label(wrap, text="开始时间（如 22:00）", bg=_bg, fg="#6e7888",
                 font=("SimHei", 13)).pack(anchor="w")
        tk.Entry(wrap, textvariable=_st, width=14, font=("SimHei", 13)
                 ).pack(anchor="w", pady=(2, 8))
        tk.Label(wrap, text="结束时间（如 07:00）", bg=_bg, fg="#6e7888",
                 font=("SimHei", 13)).pack(anchor="w")
        tk.Entry(wrap, textvariable=_en, width=14, font=("SimHei", 13)
                 ).pack(anchor="w", pady=(2, 6))

        def _save():
            try:
                with open(fairy_root.CONFIG, encoding="utf-8") as _f:
                    _j = json.load(_f)
                _j["dnd"] = {"night": bool(_on.get()), "start": _st.get().strip(),
                             "end": _en.get().strip()}
                with open(fairy_root.CONFIG, "w", encoding="utf-8") as _f:
                    json.dump(_j, _f, ensure_ascii=False, indent=2)
                self._bubble("夜间模式已保存", 3)
            except Exception as _e:
                self._bubble("保存失败：" + str(_e)[:50], 4)
            win.destroy()
        _row = tk.Frame(wrap, bg=_bg)
        _row.pack(anchor="w", pady=(8, 0))
        self._dlg_btn(_row, "保存", _save)
        self._dlg_btn(_row, "取消", win.destroy)

    def _wg_toggle(self, key, name):
        try:
            if getattr(self, "wguard", None) is None:
                self._bubble("窗口守护未加载", 4)
                return
            on = self.wguard.toggle(key)
            self._bubble("%s已%s" % (name, "开启" if on else "关闭"), 3)
        except Exception as _e:
            print("[fairy] 窗口守护开关失败: %s" % _e, flush=True)

    def _wg_review_now(self):
        try:
            if getattr(self, "wguard", None) is None:
                self._bubble("窗口守护未加载", 4)
                return
            if self._wg_running:
                self._bubble("上轮观察还在跑，稍等再点", 3)
                return
            self._wg_running = True

            def _job():
                try:
                    self.wguard.tick(force=True)
                except Exception as _e:
                    print("[fairy] 立即审核出错: %s: %s" % (type(_e).__name__, _e), flush=True)
                finally:
                    self._wg_running = False

            threading.Thread(target=_job, daemon=True).start()
            self._bubble("开始审核当前代码…", 3)
        except Exception as _e:
            print("[fairy] 立即审核失败: %s" % _e, flush=True)


class SongRecMixin:
    """SongRecMixin（2026-10-23 从 FairyBall 拆出）"""

    def _songrec_toggle(self):
        """★ 2026-10-12【听歌识曲两态】开/关后台循环（按钮显示当前状态）。
        默认关闭，用户点开才识别。"""
        self._songrec_on = not getattr(self, "_songrec_on", False)
        _st = "开启" if self._songrec_on else "停止"
        self._bubble("听歌识曲已" + _st, 3)
        try:
            self.say("听歌识曲已" + _st, "ok")
        except Exception:
            pass

    def _songrec_now(self):
        """★ 2026-10-11【快捷操作】立即听歌识曲（绕过 18 秒节流）。"""
        self._songrec_at = 0
        threading.Thread(target=self._songrec_identify, daemon=True).start()

    def _songrec_loop(self):
        """后台循环：每 18 秒抓系统声音识别一次（同一首歌不重复报）。
        两态开关：_songrec_on=False 时跳过识别。"""
        while True:
            time.sleep(5)
            try:
                if not getattr(self, "_songrec_on", False):
                    continue
                if time.time() - self._songrec_at < 18:
                    continue
                self._songrec_at = time.time()
                threading.Thread(target=self._songrec_identify, daemon=True).start()
            except Exception as _e:
                print("[fairy] 听歌识曲轮询出错: %s" % _e, flush=True)

    def _songrec_identify(self):
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import song_recognition as _sr
            r = _sr.identify_once(None, 8)
        except Exception as _e:
            print("[fairy] 听歌识曲识别出错: %s" % _e, flush=True)
            return
        if not r.get("ok"):
            return
        sig = "%s|%s" % (r.get("title") or "", r.get("artist") or "")
        if not sig.strip("|") or sig == self._songrec_last:
            return
        self._songrec_last = sig
        _title = fix_mojibake(r.get("title") or "?")
        _artist = fix_mojibake(r.get("artist") or "?")
        _t = "识别到：《%s》 - %s" % (_title, _artist)
        if r.get("link"):
            _t += "\n" + r["link"]
        self._wg_bubbles.append(_t)
        # ★★ 2026-10-24【用户要求：识别到就播报一下】
        #   原来这里只把结果塞进气泡队列（文字），一句都不念，
        #   所以"听到歌了"这件事只有你恰好看着球才知道。
        #   现在用语音念出来：只念歌名和歌手，不念链接（URL 念出来没法听）。
        try:
            _say = "现在放的是《%s》" % _title
            if _artist and _artist != "?":
                _say += "，%s" % _artist
            self.say(_say, "doing")
        except Exception as _e_say:
            print("[fairy] 识曲播报失败: %s" % _e_say, flush=True)


class ServiceMixin:
    """ServiceMixin（2026-10-23 从 FairyBall 拆出）"""

    def _wait_port_closed(self, port, timeout=30.0):
        """等端口真的不再监听（返回 True=已释放）。

        ★ 2026-10-17【组合切换串行化】为什么必须等：
          taskkill 返回成功只代表"信号送达"，进程可能还在退出、端口可能还在监听。
          旧引擎的显存/内存没释放干净就起新引擎 -> 两套抢资源，
          从视觉(8060) 切到 Strata(8080) 时正是 10-14 那次显存超时黑屏的机制。
        """
        import socket as _sk
        _t0 = time.time()
        while time.time() - _t0 < float(timeout):
            try:
                _s = _sk.socket()
                _s.settimeout(0.35)
                _open = _s.connect_ex(("127.0.0.1", int(port))) == 0
                _s.close()
            except Exception:
                _open = False
            if not _open:
                return True
            time.sleep(0.35)
        return False

    def _wait_port_up(self, port, timeout=360.0):
        """等端口真的能连上（返回 True=已就绪）。★ Strata 冷启动实测要 3~5 分钟。"""
        import socket as _sk
        _t0 = time.time()
        while time.time() - _t0 < float(timeout):
            try:
                _s = _sk.socket()
                _s.settimeout(0.6)
                _ok = _s.connect_ex(("127.0.0.1", int(port))) == 0
                _s.close()
                if _ok:
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

    def _stop_port_proc(self, port, wait=True, timeout=30.0):
        """停掉监听指定端口的整个进程树（如 Strata 8080），模型文件保留。

        ★ 2026-10-17：加 wait —— 停完要确认端口真的不再监听才返回。
          返回值仍是"原本有没有这个进程"（供调用方播报"已释放 X"）。
        """
        try:
            import subprocess as _sp2
            _out = _sp2.run(["netstat", "-ano"], capture_output=True, text=True,
                            encoding="gbk", errors="replace", timeout=10,
                            creationflags=getattr(_sp2, "CREATE_NO_WINDOW", 0)).stdout or ""
            _pids = set()
            for _ln in _out.splitlines():
                if ("127.0.0.1:%d" % port) in _ln and "LISTENING" in _ln:
                    _p = _ln.split()[-1]
                    if _p.strip().isdigit():
                        _pids.add(_p.strip())
            for _pid in _pids:
                _sp2.run(["taskkill", "/F", "/T", "/PID", _pid],
                         capture_output=True, timeout=10,
                         creationflags=getattr(_sp2, "CREATE_NO_WINDOW", 0))
            if _pids and wait:
                _freed = self._wait_port_closed(port, timeout)
                if not _freed:
                    try:
                        print("[fairy] 端口 %d 停后 %.0fs 仍在监听（可能有未清理的子进程）"
                              % (port, timeout), flush=True)
                    except Exception:
                        pass
            return bool(_pids)
        except Exception:
            return False

    def _spawn_host(self):
        """拉起本地大脑（fairy_host.py，端口 19388）——启动进度 66%
        「连接本地大脑」卡住时的自动重启（自愈）。"""
        try:
            import subprocess as _sp
            _dir = os.path.dirname(os.path.abspath(__file__))
            _py = sys.executable or "pythonw"
            _sp.Popen([_py, os.path.join(_dir, "fairy_host.py")],
                      cwd=_dir,
                      creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0),
                      stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        except Exception:
            pass

    def _probe_port(self, port, timeout=0.6):
        """探一个本地端口通不通。返回 True/False。"""
        import socket as _sock
        try:
            _s = _sock.create_connection(("127.0.0.1", port), timeout)
            _s.close()
            return True
        except Exception:
            return False

    def _ensure_dsh(self):
        """★ 2026-10-12【DSH 可启动】DeepSeek Harness 没在跑就自动拉起。

        探测 19387（dsh_base）；不通则后台启动 DeepSeek Harness.exe，
        最多等 90 秒，就绪后气泡+语音播报。绝不弹黑窗。
        """
        def _job():
            import socket as _sk
            import subprocess as _sp

            # ★ 2026-10-17：DSH 0.2.1 起端口是随机的，不能再写死 19387。
            #   以前这里探 19387 永远不通 -> 误判"DSH 没在跑" -> 重复启动一个 DSH 实例。
            def _dsh_port():
                try:
                    import dsh_port as _dp
                    _p, _src = _dp.find_port()
                    return int(_p or 0)
                except Exception:
                    return 0

            def _dsh_up():
                _p = _dsh_port()
                if not _p:
                    return False
                try:
                    _s = _sk.create_connection(("127.0.0.1", _p), timeout=1.5)
                    _s.close()
                    return True
                except Exception:
                    return False

            try:
                if _dsh_up():
                    self._bubble("DSH 云端已在运行", 3)
                    return
                _exe = os.path.join(os.path.expanduser("~"), "AppData", "Local", "Programs",
                                    "DeepSeek Harness", "DeepSeek Harness.exe")
                if not os.path.isfile(_exe):
                    self._bubble("找不到 DeepSeek Harness，请手动启动", 4)
                    return
                self._bubble("正在启动 DSH 云端…", 4)
                try:
                    self.say("正在启动 DSH 云端", "ok")
                except Exception:
                    pass
                # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP：完全脱离，不弹窗
                _sp.Popen([_exe], cwd=os.path.dirname(_exe), close_fds=True,
                          creationflags=0x00000008 | 0x00000200)
                for _i in range(90):
                    time.sleep(1)
                    if _dsh_up():
                        self._bubble("DSH 云端已就绪", 3)
                        try:
                            self.say("DSH 云端已就绪", "ok")
                        except Exception:
                            pass
                        return
                self._bubble("DSH 云端 90 秒内未就绪，请稍后再试", 5)
            except Exception as _e:
                try:
                    self._bubble("启动 DSH 失败：" + str(_e)[:50], 4)
                except Exception:
                    pass
        try:
            threading.Thread(target=_job, daemon=True).start()
        except Exception:
            pass

    def _probe_backend(self):
        """引擎自动选择：按 Strata → Ollama → llama.cpp → 自定义 顺序探测，返回第一个活的。"""
        import socket

        def _alive(port, timeout=1.0):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=timeout).close()
                return True
            except Exception:
                return False

        if _alive(8080):
            return "strata"
        if _alive(11434):
            return "ollama"
        if _alive(8085):
            return "llm"
        if (self._ai_cfg().get("base_url") or "").strip():
            return "custom"
        return "strata"  # 全都没跑时的兜底

    @staticmethod
    def _tools_dir():
        return os.path.dirname(os.path.abspath(__file__))

    @staticmethod
    def _funasr_py():
        p = r"D:\FAIRY\envs\funasr\Scripts\python.exe"
        return p if os.path.exists(p) else sys.executable


class EngineMixin:
    """EngineMixin（2026-10-23 从 FairyBall 拆出）"""

    def _cleanup_engines(self, _target, wait=False):
        """★ 2026-10-12【组合退出清理】切走旧组合 -> 停掉不用的引擎/模型，释放资源。

        _target = 新组合的引擎（strata / ollama / llm / vl / off）：
        · 不是 strata -> 停 8080 进程树（Strata + 模型，约释放几十 GB 内存）
        · 不是 llm    -> 停 8085 进程树（视觉 llama.cpp）
        · 不是 vl     -> 停 8086 进程树（视觉引擎）
        · 不是 ollama -> 不杀 ollama.exe（语音大脑 ask_brain 依赖它），
                        只把已加载的模型全部卸载（keep_alive=0）释放显存
        ★ 2026-10-17：加 wait —— 等端口真的释放完再返回；并把"停了哪些"返回给调用方，
          这样切换时可以播报"旧的已退出：Strata"，而不是空口说"正在切换"。
        """
        _bits = []
        if _target != "strata":
            try:
                if self._stop_port_proc(8080, wait=wait):
                    _bits.append("Strata")
            except Exception:
                pass
        if _target != "llm":
            try:
                if self._stop_port_proc(8085, wait=wait):
                    _bits.append("文本引擎(8085)")
            except Exception:
                pass
        if _target != "vl":
            try:
                if self._stop_port_proc(8086, wait=wait):
                    _bits.append("视觉引擎(8086)")
            except Exception:
                pass
        # ★ 2026-10-21【27B 快脑】8081 是 llama.cpp 的 27B（与 Strata 互斥）
        if _target != "llm27":
            try:
                if self._stop_port_proc(8081, wait=wait):
                    _bits.append("27B快脑(8081)")
            except Exception:
                pass
        if _target != "ollama":
            try:
                import urllib.request as _ur
                _r = _ur.urlopen("http://127.0.0.1:11434/api/tags", timeout=3)
                _j = json.loads(_r.read().decode("utf-8", "replace"))
                for _m in (_j.get("models") or []):
                    _mn = _m.get("name") or _m.get("model") or ""
                    if not _mn:
                        continue
                    try:
                        _req = _ur.Request(
                            "http://127.0.0.1:11434/api/generate",
                            data=json.dumps({"model": _mn, "keep_alive": 0})
                            .encode("utf-8"),
                            headers={"content-type": "application/json"})
                        _ur.urlopen(_req, timeout=8).read()
                        _bits.append(_mn)
                    except Exception:
                        pass
            except Exception:
                pass
        if _bits:
            try:
                self._bubble("已释放：%s" % "、".join(_bits), 4)
            except Exception:
                pass
        return _bits

    def _start_engine(self, eng):
        """按组合拉起对应引擎（幂等：端口已通就跳过）。返回 True=本次真的拉起了。

        ★ 2026-10-17 把两处重复的 _ensure() 抽出来，右键/面板共用一份，
          免得改一处漏一处。

        ★★ 2026-10-17 晚【"日常全能"要能把 Strata 一起打开】修三处：
          ① `_strata_probe()` 有 15 秒缓存 —— 刚被别的组合停掉的 Strata，
             缓存里可能还写着 running，于是这里误判"已在跑"而**跳过拉起**，
             用户就得自己去菜单里再点一次"启动本地运算"。现在强制 `force=True`。
          ② 拉起前先清一次缓存，避免后面 `_wait_port_up` 阶段读到旧状态。
          ③ 返回结果带原因，让上层能播报"已拉起 / 本来就在跑 / 没找到安装目录"。
        """
        import socket as _sk
        if eng == "strata":
            try:
                self._strata_cache = None
                st = self._strata_probe(force=True)
                _state = st.get("state") or ""
                if _state == "running":
                    return False              # 真在跑，不用拉
                self._strata_start()
                return True
            except Exception as _e:
                try:
                    self._bubble("拉起本地运算失败：" + str(_e)[:50], 5)
                except Exception:
                    pass
                return False
        _spec = {
            "llm": (8085, "fairy_llm.py", "本地文本引擎（8085）"),
            "vl":  (8086, "fairy_vl.py", "视觉引擎（8086）"),
            # ★ 2026-10-21【27B 快脑】Qwen3.8-27B Q4_K_M，40/64 层上卡，8K 上下文
            "llm27": (8081, "fairy_llm27.py", "Qwen3.8-27B·IQ3_XXS（8081）"),
        }
        if eng in _spec:
            _port, _script, _name = _spec[eng]
            _s = _sk.socket()
            _s.settimeout(0.4)
            try:
                _s.connect(("127.0.0.1", _port))
                _s.close()
                return False                  # 已经在跑
            except Exception:
                pass
            try:
                import subprocess as _sp4
                _p4 = os.path.join(os.path.dirname(os.path.abspath(__file__)), _script)
                _sp4.Popen([sys.executable, _p4],
                           creationflags=getattr(_sp4, "CREATE_NO_WINDOW", 0),
                           stdout=_sp4.DEVNULL, stderr=_sp4.DEVNULL)
                self._bubble("已拉起%s，请稍候" % _name, 4)
                return True
            except Exception:
                return False
        if eng == "ollama":
            _s = _sk.socket()
            _s.settimeout(0.4)
            try:
                _s.connect(("127.0.0.1", 11434))
                _s.close()
                return False                  # 进程在（模型加载由 _warm_model 负责）
            except Exception:
                pass
            try:
                import subprocess as _sp5
                _exe = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
                if os.path.exists(_exe):
                    _sp5.Popen([_exe, "serve"],
                               creationflags=getattr(_sp5, "CREATE_NO_WINDOW", 0),
                               stdout=_sp5.DEVNULL, stderr=_sp5.DEVNULL)
                    self._bubble("已拉起 Ollama（11434），请稍候", 4)
                    return True
            except Exception:
                return False
        return False

    def _warm_model(self, eng, mdl):
        """★ 2026-10-17：切到【同一引擎的另一个模型】时，光"端口通"不代表新模型好了。

        Ollama 是进程常驻、按需加载模型 —— 切模型不重启进程，但新模型要等第一次
        请求才加载（首次几十秒）。以前这里直接 return，导致播报"已就绪"但其实没加载，
        用户第一条对话卡很久。这里主动发一条最小请求触发加载，并给 keep_alive 保温。
        """
        if eng != "ollama" or not mdl:
            return False
        try:
            import urllib.request as _ur
            _body = json.dumps({
                "model": mdl, "prompt": "hi", "stream": False,
                "options": {"num_predict": 1}, "keep_alive": "30m",
            }).encode("utf-8")
            _req = _ur.Request("http://127.0.0.1:11434/api/generate", data=_body,
                               headers={"content-type": "application/json"})
            _ur.urlopen(_req, timeout=180).read()
            return True
        except Exception:
            return False

    def _engine_release(self, quiet=False):
        """★ 2026-10-11【退出引擎与模型释放】停止本地引擎，释放显存/内存。

        quiet=True：开机自动释放时用 —— 只留气泡，不出声（用户要求启动只播天气+穿衣）。
        """
        import subprocess as _sp
        _kw = {"creationflags": getattr(_sp, "CREATE_NO_WINDOW", 0),
               "stdout": _sp.DEVNULL, "stderr": _sp.DEVNULL}
        try:
            self._strata_stop(quiet=quiet)
        except Exception:
            pass
        for _p in ("llama-server.exe", "ollama.exe", "koboldcpp.exe"):
            try:
                _sp.Popen(["taskkill", "/F", "/IM", _p], **_kw)
            except Exception:
                pass
        self._bubble("引擎与模型已退出，显存/内存已释放", 4)
        if not quiet:
            self.say("引擎与模型已退出，资源已释放", "ok")

    def _apply_preset(self, nm, eng, port, models, desc, mdl=None):
        """★ 2026-10-17【组合切换·串行版】右键菜单 + 设置面板共用（唯一实现）。

        严格顺序（以前的实现是"清理线程 + 启动线程"并发 + 立刻播"已切换"，有三个坑）：
          ① 停旧组合引擎 -> ② **等端口真的不再监听** -> ③ 起新组合引擎
          ④ 等新引擎端口真的通了 -> 才播"组合XX已就绪"
        ★ 为什么必须串行：旧引擎显存/内存没释放干净就起新引擎，两套会抢资源 ——
          从视觉(8086) 切到 Strata(8080) 时，正是 10-14 那次显存超时黑屏的机制。
        ★ 为什么删掉那句「已切换」的播报：引擎还没起来，说了就是假成功。
        """
        try:
            if eng == "off":
                self._engine_release()
                self._save_ai_cfg({"preset": "game"})
                self._bubble("游戏模式：全部模型已释放，显存已空出，尽情玩吧", 5)
                try:
                    self.say("游戏模式：模型已全部释放，显存已空出，可以开始游戏", "ok")
                except Exception:
                    pass
                return
            mdl = mdl or (models[0] if models else "")
            self._save_ai_cfg({"backend": eng, "model": mdl,
                               "port": port, "preset": nm})
            self._bubble("正在切换组合：%s（%s）" % (nm, mdl), 4)
            try:
                self.say("正在切换组合：%s，先关掉旧的" % nm, "doing")
            except Exception:
                pass
            # 进度条只画不播（播报统一由 _work 在真就绪时做，避免两句打架）
            self._preset_progress(nm, port, speak=False)

            def _work():
                # ① 停旧引擎（wait=True：等端口真的释放）
                _stopped = []
                try:
                    _stopped = self._cleanup_engines(eng, wait=True) or []
                except Exception:
                    _stopped = []
                if _stopped:
                    try:
                        self._bubble("旧引擎已退出：%s" % "、".join(_stopped), 3)
                    except Exception:
                        pass
                # ② 起新引擎
                _started = False
                try:
                    _started = bool(self._start_engine(eng))
                except Exception:
                    _started = False
                # ★ 2026-10-17【"日常全能"要能把 Strata 一起打开】
                #   Strata 冷启动实测 3~5 分钟，以前这期间【一声不响】，
                #   用户以为"选了日常但本地运算没开"。现在先明确说一句。
                # ★★ 2026-10-18【用户反馈"切换好慢"—— 实测出来的真数据】
                #   strata_server.log 里同一次启动会出现两种极端：
                #     · 模型文件已被 Windows 文件缓存命中 -> ready 只用 28 秒
                #     · 完全冷读（37.7 GB 从 D 盘读进 40 GiB RAM + 填 GPU expert 缓存）
                #       -> 67s/88s/108s…一路到 **372 秒**，甚至 453 秒
                #   也就是"快的时候半分钟、慢的时候七八分钟"，取决于磁盘缓存。
                #   原来的超时是 360 秒 —— 正好卡在最慢那批的中间，
                #   所以经常"等满 6 分钟然后报启动超时"，看起来像死了。
                if eng == "strata" and _started:
                    try:
                        self._bubble("本地运算（Strata）正在加载，约 3~8 分钟…", 6)
                        self.say("本地运算正在加载，慢的时候要七八分钟", "doing")
                    except Exception:
                        pass
                # ③ 等端口真的起来（Strata 冷启动实测最慢 453 秒，留足 600）
                _ok = self._wait_port_up(port, 600)
                if _ok:
                    # 同一引擎换模型（Ollama）要主动预热，否则第一条对话会卡很久
                    try:
                        self._warm_model(eng, mdl)
                    except Exception:
                        pass
                    self._ball_progress(0, "", False)
                    try:
                        if eng == "strata":
                            self._bubble("组合「%s」已就绪，本地运算（Strata）已打开" % nm, 5)
                            self.say("组合%s已切换好，本地运算也开好了" % nm, "ok")
                        else:
                            self._bubble("组合「%s」已就绪" % nm, 5)
                            self.say("组合%s已切换好，可以用了" % nm, "ok")
                    except Exception:
                        pass
                    try:
                        if eng in ("strata", "ollama", "llm", "vl", "llm27"):
                            self._open_chat_web(eng, port, mdl)
                    except Exception:
                        pass
                    try:
                        _dp, _dm = self._DSH_MODEL_BY_ENG.get(eng, (None, None))
                        if _dp:
                            self._dsh_switch_model(_dp, _dm)
                    except Exception:
                        pass
                else:
                    self._ball_progress(0, "", False)
                    try:
                        self._bubble("组合「%s」启动超时（端口 %s 一直没通）" % (nm, port), 6)
                        self.say("组合%s启动超时，请检查引擎" % nm, "warn")
                    except Exception:
                        pass
            threading.Thread(target=_work, daemon=True).start()
        except Exception as _e:
            try:
                self._bubble("组合切换失败：" + str(_e)[:50], 5)
            except Exception:
                pass

    def _dsh_switch_model(self, provider, model):
        """★ 2026-10-17【DSH 模型联动】切 fairy 组合时，把 DSH 主对话默认模型
        （cordis.patch.yml 的 agent-default-model）换成对应 provider/model。
        DSH 在跑就自动重启使其立即生效；没跑则下次 DSH 启动生效。绝不抛异常。"""
        import re as _re
        _p = os.path.join(os.path.expanduser("~"), ".dsh", "profiles", "desktop",
                          "cordis.patch.yml")
        try:
            if not os.path.exists(_p):
                return False
            with open(_p, "r", encoding="utf-8") as _f:
                _txt = _f.read()
            _bak = _p + ".bak_fairy_combo"
            if not os.path.exists(_bak):
                try:
                    open(_bak, "w", encoding="utf-8").write(_txt)
                except Exception:
                    pass
            _m = _re.search(r"(- id: agent-default-model\s*\n(?:.*\n)*?)(?=\n- id: |\Z)", _txt)
            if not _m:
                return False
            _block = _m.group(1)
            _nb = _re.sub(r"(?m)^(    provider: ).*$", lambda _x: _x.group(1) + provider, _block)
            _nb = _re.sub(r"(?m)^(    model: ).*$", lambda _x: _x.group(1) + model, _nb)
            _new = _txt[:_m.start(1)] + _nb + _txt[_m.end(1):]
            if _new != _txt:
                with open(_p, "w", encoding="utf-8") as _f:
                    _f.write(_new)
            _up = False
            try:
                import socket as _sk4
                # ★ 2026-10-17：DSH 端口是动态的（0.2.1 起），别写死 19387
                _dp4 = 0
                try:
                    import dsh_port as _dpm
                    _dp4 = int(_dpm.find_port()[0] or 0)
                except Exception:
                    _dp4 = 0
                if _dp4:
                    _s4 = _sk4.socket(); _s4.settimeout(1.0)
                    _up = _s4.connect_ex(("127.0.0.1", _dp4)) == 0
                    _s4.close()
            except Exception:
                _up = False
            # ★★ 2026-10-21【不再擅自杀掉 DSH】原来这里 taskkill /F 掉整个 DeepSeek Harness
            #   再拉起 —— 用户看到的就是"换个组合 DSH 就崩了"，而且正在进行的对话会被打断。
            #   现在默认只写配置，等 DSH 自己下次启动生效；
            #   想恢复旧行为：fairy.json 里加 "dsh_auto_restart": true
            _auto = False
            try:
                _c = getattr(fairy_root, "CFG", None) or {}
                _auto = bool((_c or {}).get("dsh_auto_restart"))
            except Exception:
                _auto = False
            if _up and _auto:
                try:
                    import subprocess as _sp4
                    _sp4.run(["taskkill", "/F", "/IM", "DeepSeek Harness.exe"],
                             capture_output=True, timeout=10,
                             creationflags=getattr(_sp4, "CREATE_NO_WINDOW", 0))
                except Exception:
                    pass
                try:
                    self._bubble("DSH 模型已同步，正在重启 DSH 生效", 4)
                except Exception:
                    pass
                threading.Thread(target=self._ensure_dsh, daemon=True).start()
            elif _up:
                try:
                    self._bubble("DSH 模型已写入（%s / %s），下次启动 DSH 生效；本次不重启"
                                 % (provider, model), 6)
                except Exception:
                    pass
            return True
        except Exception:
            return False


class MenuMixin:
    """MenuMixin（2026-10-23 从 FairyBall 拆出）"""

    def _on_right(self, e):
        """★ 2026-10-11【右键菜单】圆角浮动面板，与设置面板同风格。"""
        self._close_menu()
        _m = tk.Toplevel(self)
        _m.overrideredirect(True)
        _m.attributes("-topmost", True)
        _m.configure(bg="#f2f3f5")
        _bgk = "#f2f3f5"
        try:
            _running = self._strata_probe().get("state") == "running"
        except Exception:
            _running = False
        # ★ 2026-10-19【用户要求】右键菜单里不再放悬浮窗切换 —— 那个功能只留左键单击。
        _items = [("Fairy 控制面板", self._open_panel), None,
                  ("本地运算 · 切换组合", self._on_right_combo),
                  ("全部退出 Fairy", self._quit_all)]
        _w = 264
        _h = sum(52 if it else 2 for it in _items) + 18
        vx, vy, vw, vh = virtual_screen()
        _E = _EDGE_PX
        _x = min(e.x_root, vx + vw - _w - _E)
        _y = min(e.y_root, vy + vh - _h - _E)
        _m.geometry("%dx%d+%d+%d" % (_w, _h, max(vx + _E, _x), max(vy + _E, _y)))
        # ★ 2026-10-11【窗口圆角】GDI 圆角区域裁剪（Win7+ 通用）
        try:
            import ctypes
            _u = ctypes.windll.user32
            _g = ctypes.windll.gdi32
            _hwnd = _m.winfo_id()
            _rgn = _g.CreateRoundRectRgn(0, 0, _w + 1, _h + 1, 22, 22)
            if _rgn:
                _u.SetWindowRgn(_hwnd, _rgn, True)
        except Exception:
            pass
        _wrap = tk.Frame(_m, bg=_bgk)
        _wrap.pack(fill="both", expand=True, padx=6, pady=8)

        def _mk(label, cmd, danger=False):
            _fg = "#a8625e" if danger else "#46536a"
            # 正常态：白底圆角；悬停态：浅蓝底
            _img = _pil_round(label, 248, 48, 12, "#ffffff", _fg, True, "#d8dfe8")[0]
            _img_h = _pil_round(label, 248, 48, 12, "#e4edf6", _fg, True, "#cdd9e6")[0]
            _b = tk.Button(_wrap, image=_img, bd=0, relief="flat",
                           highlightthickness=0, bg=_bgk,
                           activebackground=_bgk, cursor="hand2",
                           command=lambda c=cmd: self._menu_click(c))
            _b.image = _img
            _b.bind("<Enter>", lambda _ev, b=_b, n=_img, h=_img_h: b.configure(image=h))
            _b.bind("<Leave>", lambda _ev, b=_b, n=_img, h=_img_h: b.configure(image=n))
            _b.pack(fill="x", pady=2)

        for _it in _items:
            if _it is None:
                tk.Frame(_wrap, bg="#d8dfe8", height=1).pack(fill="x", pady=3)
            else:
                _mk(*_it)
        _m.bind("<FocusOut>", lambda _ev: self._close_menu())
        _m.bind("<Escape>", lambda _ev: self._close_menu())
        _m.focus_force()
        self._menu_win = _m

    def _on_right_combo(self):
        """★ 2026-10-17【右键组合切换】右键菜单弹组合选择面板，点选即切换。
           组合放进「本地运算」入口。"""
        self._close_menu()
        _PRESETS = [
            ("Qwen3.8-Flash-Next·IQ3_S", "strata", "8080",
             ["Qwen3.8-Flash-Next\u00b7IQ3_S"],
             "最强：8~11 tok/s，首 token 3~10s，吃 34G 内存 + 15.7G 显存"),
            ("Qwen3.8-27B·IQ3_XXS", "llm27", "8081",
             ["qwen3.8-27b"],
             "IQ3_XXS 全卡：31~37 tok/s，16K 上下文，显存吃满"),
            ("Qwen3-VL-8B", "vl", "8086",
             ["qwen3-vl-8b"],
             "看图问答：Qwen3-VL-8B，约 0.6 秒/图"),
            ("Qwen3.5-9B", "llm", "8085",
             ["qwen3.5-9b"],
             "9B Q4（5.2G）：冷启最快、最省显存，质量低于上面两个"),
            ("游戏模式", "off", "", [],
             "释放全部引擎与模型，显存全给游戏"),
        ]
        _m = tk.Toplevel(self)
        _m.overrideredirect(True)
        _m.attributes("-topmost", True)
        _m.configure(bg="#f2f3f5")
        _bgk = "#f2f3f5"
        try:
            _cur = (self._ai_cfg().get("preset") or "")
        except Exception:
            _cur = ""
        _w = 284
        _h = len(_PRESETS) * 56 + 18
        vx, vy, vw, vh = virtual_screen()
        _E = _EDGE_PX
        _x = min(self.winfo_pointerx(), vx + vw - _w - _E)
        _y = min(self.winfo_pointery(), vy + vh - _h - _E)
        _m.geometry("%dx%d+%d+%d" % (_w, _h, max(vx + _E, _x), max(vy + _E, _y)))
        try:
            import ctypes
            _u = ctypes.windll.user32
            _g = ctypes.windll.gdi32
            _hwnd = _m.winfo_id()
            _rgn = _g.CreateRoundRectRgn(0, 0, _w + 1, _h + 1, 22, 22)
            if _rgn:
                _u.SetWindowRgn(_hwnd, _rgn, True)
        except Exception:
            pass
        _wrap = tk.Frame(_m, bg=_bgk)
        _wrap.pack(fill="both", expand=True, padx=6, pady=8)

        def _mk(label, cmd):
            _fg = "#46536a"
            _img = _pil_round(label, 266, 48, 12, "#ffffff", _fg, True, "#d8dfe8")[0]
            _img_h = _pil_round(label, 266, 48, 12, "#e4edf6", _fg, True, "#cdd9e6")[0]
            _b = tk.Button(_wrap, image=_img, bd=0, relief="flat",
                           highlightthickness=0, bg=_bgk,
                           activebackground=_bgk, cursor="hand2",
                           command=lambda c=cmd: self._menu_click(c))
            _b.image = _img
            _b.bind("<Enter>", lambda _ev, b=_b, n=_img, h=_img_h: b.configure(image=h))
            _b.bind("<Leave>", lambda _ev, b=_b, n=_img, h=_img_h: b.configure(image=n))
            _b.pack(fill="x", pady=2)
        for _nm, _eng, _pt2, _models, _desc in _PRESETS:
            _sel = (_cur == _nm) or (_cur == "game" and _nm == "游戏模式")
            _ck = "✓ " if _sel else "  "
            _mk(_ck + _nm, lambda n=_nm, e=_eng, p=_pt2, ms=_models, d=_desc:
                self._apply_preset(n, e, p, ms, d))
        _m.bind("<FocusOut>", lambda _ev: self._close_menu())
        _m.bind("<Escape>", lambda _ev: self._close_menu())
        _m.focus_force()
        self._menu_win = _m

    def _close_menu(self):
        """★ 2026-10-11【右键菜单】关闭当前浮动菜单。"""
        _m = getattr(self, "_menu_win", None)
        if _m:
            try:
                _m.destroy()
            except Exception:
                pass
            self._menu_win = None

    def _menu_click(self, cmd):
        """★ 2026-10-12【菜单点击】先关菜单再执行命令；异常落盘 panel_err.log。"""
        try:
            self._close_menu()
        except Exception:
            pass
        try:
            cmd()
        except Exception as _e:
            import traceback as _tb
            try:
                _h = io.open(r"D:\FAIRY\logs\panel_err.log", "a", encoding="utf-8")
                _h.write("MENU_CLICK\n%s\n" % _tb.format_exc())
                _h.close()
            except Exception:
                pass
            try:
                self._bubble("菜单执行失败：" + str(_e)[:50], 5)
            except Exception:
                pass


class DialogMixin:
    """DialogMixin（2026-10-23 从 FairyBall 拆出）"""

    def _dialog(self, title, w=440, h=320, bg="#f2f3f5"):
        """★ 2026-10-12【统一子窗口】所有弹窗同主界面风格：浅灰底 + 圆角 + 黑体。
        返回 (win, wrap)，wrap 是内容容器（淡色底，往里面摆控件）。"""
        win = tk.Toplevel(self)
        win.title(title)
        win.attributes("-topmost", True)
        win.configure(bg=bg)
        win.resizable(False, False)
        win.geometry("%dx%d+%d+%d" % (w, h,
                                      max(0, (win.winfo_screenwidth() - w) // 2),
                                      max(0, (win.winfo_screenheight() - h) // 2)))
        try:
            import ctypes
            _u = ctypes.windll.user32
            _g = ctypes.windll.gdi32
            _rgn = _g.CreateRoundRectRgn(0, 0, w + 1, h + 1, 18, 18)
            if _rgn:
                _u.SetWindowRgn(win.winfo_id(), _rgn, True)
        except Exception:
            pass
        tk.Label(win, text=title, bg=bg, fg="#52749b",
                 font=("SimHei", 15, "bold")).pack(anchor="w", padx=24, pady=(14, 2))
        _wrap = tk.Frame(win, bg=bg)
        _wrap.pack(fill="both", expand=True, padx=24, pady=(4, 18))
        return win, _wrap

    def _dlg_btn(self, parent, text, cmd, danger=False, w=100):
        """统一弹窗按钮：圆角白底，悬停变色。"""
        _fg = "#a8625e" if danger else "#46536a"
        _img = _pil_round(text, w, 40, 12, "#ffffff", _fg, True, "#dfe3ea")[0]
        _img_h = _pil_round(text, w, 40, 12, "#e4edf6", _fg, True, "#cdd9e6")[0]
        _b = tk.Button(parent, image=_img, bd=0, relief="flat",
                       highlightthickness=0, bg="#f2f3f5",
                       activebackground="#f2f3f5", cursor="hand2",
                       command=cmd)
        _b.image = _img
        _b.bind("<Enter>", lambda _ev, b=_b, n=_img, h=_img_h: b.configure(image=h))
        _b.bind("<Leave>", lambda _ev, b=_b, n=_img, h=_img_h: b.configure(image=n))
        _b.pack(side="left", padx=5, pady=4)
        return _b

    def _result_win(self, title, text):
        """★ 2026-10-12【结果窗】统一风格弹窗显示多行结果（可滚动）。"""
        win, wrap = self._dialog(title, 540, 360)
        _cv = tk.Canvas(wrap, bg="#f2f3f5", highlightthickness=0)
        _sb = tk.Scrollbar(wrap, orient="vertical", command=_cv.yview)
        _cv.configure(yscrollcommand=_sb.set)
        _sb.pack(side="right", fill="y")
        _cv.pack(side="left", fill="both", expand=True)
        _inner = tk.Frame(_cv, bg="#f2f3f5")
        _wid = _cv.create_window((0, 0), window=_inner, anchor="nw")

        def _fit(_e):
            _cv.itemconfigure(_wid, width=max(120, _cv.winfo_width() - 8))
            _cv.configure(scrollregion=_cv.bbox("all"))
        _cv.bind("<Configure>", _fit)

        def _bind_wheel(_w):
            _w.bind("<MouseWheel>", lambda _ev: _cv.yview_scroll(
                int(-1 * (_ev.delta / 120)), "units"))
            for _c in _w.winfo_children():
                _bind_wheel(_c)
        for _ln in str(text).splitlines():
            tk.Label(_inner, text=_ln, bg="#f2f3f5", fg="#46536a",
                     font=("SimHei", 12), justify="left", anchor="w",
                     wraplength=460).pack(fill="x", padx=8, pady=2)
        _bind_wheel(_inner)


class PrefMixin:
    """PrefMixin（2026-10-23 从 FairyBall 拆出）"""

    def _pref(self):
        """读取 fairy.json 的 endpoints.prefer（local / dsh / auto）。
        ★ 2026-10-11【DSH 解耦】默认 local（单机模式，不连 DSH）。"""
        try:
            p = getattr(fairy_root, "CONFIG", None)
            c = {}
            if p and os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    c = json.load(f)
            return ((c.get("endpoints") or {}).get("prefer") or "local")
        except Exception:
            return "local"

    def _set_pref(self, k):
        """切换大脑模式：auto（DSH 优先）/ dsh（强制 DSH）/ local（只用本地）。"""
        try:
            p = getattr(fairy_root, "CONFIG", None)
            c = {}
            if p and os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    c = json.load(f)
            e = dict(c.get("endpoints") or {})
            e["prefer"] = k
            c["endpoints"] = e
            with open(p, "w", encoding="utf-8") as f:
                json.dump(c, f, ensure_ascii=False, indent=2)
            _nm = {"auto": "自动（本地优先）", "dsh": "DSH 云端",
                   "local": "本地内核（单机模式）"}.get(k, k)
            self._bubble("大脑模式已切换：" + _nm, 3)
            # ★ 2026-10-12【DSH 可启动】切到 dsh 模式时，若 DSH 没在跑，自动拉起
            if k == "dsh":
                self._ensure_dsh()
        except Exception as e:
            self._bubble("切换大脑模式失败：" + str(e)[:60], 4)

    def _briefing_mod(self):
        import importlib.util
        fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "briefing.py")
        s = importlib.util.spec_from_file_location("fairy_briefing", fp)
        m = importlib.util.module_from_spec(s)
        sys.modules["fairy_briefing"] = m
        s.loader.exec_module(m)
        return m

    def _set_city(self, city):
        """设置并**确认**位置（写进 briefing.json）。city='' = 重新自动定位。"""
        try:
            mod = self._briefing_mod()
            if city:
                mod.save_conf({"city": city, "city_confirmed": True})
                self.city = city
                self.speaker.say("位置已设定为" + city)
            else:
                auto, _lat, _lon = mod.get_location("")
                mod.save_conf({"city": "", "city_confirmed": False, "auto_city": auto or ""})
                self.city = auto or ""
                self.speaker.say("重新自动定位到" + (auto or "未知"))
        except Exception:
            pass

    def _sys_status(self):
        """★ 2026-10-11 系统状态：气泡+语音播报 CPU/内存/磁盘占用（不动硬件）。"""
        import subprocess as _sp
        try:
            _r = _rnw(["powershell", "-NoProfile", "-Command",
                          "$c=(Get-Counter '\\Processor(_Total)\\% Processor Time').CookedValues[0];"
                          "$m=Get-CimInstance Win32_OperatingSystem;"
                          "$mem=100*(1-$m.FreePhysicalMemory/$m.TotalVisibleMemorySize);"
                          "$d=Get-PSDrive C | Select-Object @{n='u';e={[math]::Round($_.Used/$_.Size*100,0)}};"
                          "Write-Output ('{0:N0}|{1:N0}|{2}' -f $c,$mem,$d.u)"],
                         capture_output=True, text=True, timeout=25, encoding="utf-8", errors="replace")
            _c, _m, _d = _r.stdout.strip().split("|")
            self._bubble("CPU %s%% · 内存 %s%% · C盘 %s%%" % (_c, _m, _d), 6)
            self.say("当前 CPU %s%%，内存 %s%%，C 盘占用 %s%" % (_c, _m, _d), "ok")
        except Exception as _e:
            self._bubble("系统状态读取失败：" + str(_e)[:50], 4)

    def _net_diag(self):
        """★ 2026-10-11【网络诊断】跑检测，气泡+语音播报结果。"""
        try:
            sys.path.insert(0, self._tools_dir())
            import fairy_netdiag as _fn
            _lines = _fn.report(_fn.diag())
            self._bubble("\n".join(_lines), 8)
            self.say(_lines[-1], "ok")
        except Exception as _e:
            self._bubble("网络诊断失败：" + str(_e)[:50], 4)

    def _security_check(self):
        import subprocess as _sp

        def _work():
            self.say("正在进行安全自检，检查病毒防护和系统漏洞", "doing")
            try:
                r = _rnw([sys.executable, os.path.join(self._tools_dir(), "fairy_security.py"),
                             "--check", "--json"], capture_output=True, timeout=60,
                            text=True, encoding="utf-8", errors="replace")
                d = json.loads(r.stdout or "{}")
                if not d.get("ok") and "items" not in d:
                    self.say("安全自检失败", "warn")
                    self._bubble("自检失败：" + str(d.get("error"))[:60], 4)
                    return
                bad = [it for it in d.get("items") or [] if not it.get("ok")]
                if not bad:
                    self.say("安全自检通过，病毒防护正常，系统无高危漏洞", "ok")
                    self._bubble("安全自检通过：\n" + "\n".join(
                        "✓ %s：%s" % (it["name"], it["detail"]) for it in d.get("items") or []), 10)
                else:
                    self.say("安全自检发现%d项异常" % len(bad), "warn")
                    self._bubble("安全自检发现异常：\n" + "\n".join(
                        "✗ %s：%s" % (it["name"], it["detail"]) for it in bad), 12)
            except Exception as e:
                self.say("安全自检失败", "warn")
                self._bubble("自检失败：" + str(e)[:60], 4)
        threading.Thread(target=_work, daemon=True).start()

    def _ask_perms_first_run(self):
        """首次使用弹一次权限申请（替代原来那套「逐项授权」面板）。

        用户 2026-10-18：「权限授权删除，第一次安装启动时向使用人申请权限」。
        只问一次：点「全部允许」= 全开；「稍后再说」= 保持未授权（对应功能关着）；
        直接关窗口 = 不标记，下次启动再问。
        """
        try:
            import fairy_perms as _fp
        except Exception:
            return
        try:
            if not _fp.first_run_pending():
                return
        except Exception:
            return
        try:
            self._bubble("第一次用，先跟你确认一下要用哪些权限", 4)
        except Exception:
            pass
        try:
            _ans = _fp.ask_with_tk(self)
        except Exception as _e:
            try:
                self._bubble("权限窗没弹出来：" + str(_e)[:50], 4)
            except Exception:
                pass
            return
        if _ans == "allow":
            try:
                _fp.set_perms(["all"])
                _fp.mark_asked()
                self._bubble("全部权限已授权，功能全开", 4)
                try:
                    self.say("权限已全部授权", "ok")
                except Exception:
                    pass
            except Exception:
                pass
        elif _ans == "later":
            try:
                _fp.mark_asked()
                self._bubble("先不授权也行，对应功能会关着；以后想改跑 py -3.12 fairy_perms.py --grant all", 5)
            except Exception:
                pass

    def _balance_loop(self):
        while True:
            b = read_balance()
            if b:
                self.balance = b
            time.sleep(BALANCE_REFRESH_S)

    def _boot_progress(self):
        """★ 2026-10-12【启动进度 v4】不再弹独立进度气泡，
        进度直接画在球右侧进度条面板：阶段名 + 百分比，全部就绪后自动清空。"""
        try:
            import socket as _sock
            import threading as _th

            def _port_up(_port, _host="127.0.0.1", _t=0.25):
                try:
                    _s = _sock.socket()
                    _s.settimeout(_t)
                    _s.connect((_host, _port))
                    _s.close()
                    return True
                except Exception:
                    return False

            # ★ 2026-10-12【启动默认游戏模式】启动进度跳过引擎三阶段：
            #   每次启动先进游戏模式 -> 引擎不探测、不拉起、不加载模型，
            #   启动完成后 _boot_pick_game 释放全部引擎；要对话时面板手动切组合。
            _STAGES = [
                ("加载语音素材", lambda: bool(os.path.exists(VOICE_INDEX)
                                           and os.path.getsize(VOICE_INDEX) > 0)),
                ("连接本地大脑", lambda: _port_up(19388)),
                ("语音就绪", lambda: _port_up(9881)),
            ]
            _n = len(_STAGES)
            _done = [False] * _n
            _idx = {"v": 0}                    # 当前阶段
            _enter = {"t": time.time()}        # 进入当前阶段的时间
            _deadline = time.time() + 90
            _pick = {"done": False}            # 启动完成后的默认组合只切一次
            _selfheal = {"done": False}        # 66% 卡住只自愈一次

            def _draw():
                _k = _idx["v"]
                _p = int(100 * _k / _n)
                if _k < _n:
                    self._ball_progress(_p, "Fairy 启动中 · " + _STAGES[_k][0], True)
                else:
                    self._ball_progress(100, "Fairy 启动完成", True)
                    # ★ 2026-10-12【启动默认游戏模式】启动完成后自动进入游戏模式
                    if not _pick["done"]:
                        _pick["done"] = True
                        try:
                            self.after(300, self._boot_pick_game)
                        except Exception:
                            pass
                if _k >= _n or time.time() > _deadline:
                    self._ball_progress(0, "", False)
                    # ★ 2026-10-12【启动收尾】无论进度走完还是超时，
                    #   启动都进入游戏模式（释放全部引擎），避免卡住时永不生效。
                    if not _pick["done"]:
                        _pick["done"] = True
                        try:
                            self.after(300, self._boot_pick_game)
                        except Exception:
                            pass
                    return False
                return True

            def _check():
                """★ 2026-10-12【逐阶段推进】只检查当前阶段；就绪后至少停留 1 秒
                再进下一阶段 -> 进度条按 0→16→33→50→66→83→100 渐进显示。
                模型加载阶段未就绪时停在「加载本地模型」等待真正加载完。"""
                _i = _idx["v"]
                if _i >= _n:
                    return
                # ★ 2026-10-12【66% 自愈】「连接本地大脑」卡住超 8 秒 ->
                #   播报"模型未能启动"并自动重启本地大脑（只做一次，不反复打扰）
                #   ★ 2026-10-12：启动阶段精简后「连接本地大脑」是第 1 阶段
                #   （索引 1），旧值 4 会导致自愈永不触发。
                if _i == 1 and not _selfheal["done"]:
                    # ★★ 2026-10-18【修「自动重启好几次」】
                    #   原来判据是「8 秒没起来就认为本地大脑挂了」——
                    #   但实测 fairy_host 从球的启动算起要 **12 秒**才监听 19388
                    #   （球 14:20:00 起，host 14:20:12 才起），
                    #   所以每次正常启动都会在 8 秒时误判一次，播
                    #   「模型未能启动，正在重新启动本地大脑」并重复拉起 host。
                    #   用户听感就是"老是自动重启"。
                    #   现在放宽到 25 秒（给 host 足够时间），并且连续两次探测
                    #   都不通才算真挂。
                    if time.time() - _enter["t"] >= 25.0 and not _port_up(19388):
                        _selfheal["done"] = True
                        try:
                            self.say("本地大脑没响应，正在重新启动", "warn")
                            self._bubble("本地大脑未响应，正在自动重启", 5)
                            self._spawn_host()
                        except Exception:
                            pass
                try:
                    if _STAGES[_i][1]():
                        if time.time() - _enter["t"] >= 1.0:
                            _done[_i] = True
                            _idx["v"] = _i + 1
                            _enter["t"] = time.time()
                            try:
                                self.after(0, _draw)
                            except Exception:
                                pass
                except Exception:
                    pass
                if _draw():
                    self.after(400, _check)

            _draw()
            _th.Thread(target=_check, daemon=True).start()
        except Exception:
            pass

    def _boot_pick_game(self):
        """★ 2026-10-12【启动默认游戏模式】Fairy 每次启动先进游戏模式：
        释放全部引擎和模型（显存全给游戏），语音唤醒/识别等轻功能照常；
        需要对话时在面板手动切换组合（Qwen3.8-Flash-Next·IQ3_S / Qwen3.8-27B·IQ3_XXS / Qwen3-VL-8B / Qwen3.5-9B）。"""
        try:
            _ai = self._ai_cfg()
            # ★★ 2026-10-18【修「切了组合模型又起来」的第二个根因】
            #   原来只判断 `preset != game` 才把配置写成游戏模式。
            #   但实测出现过 **preset 已经是 game、backend 却还写着 strata** 的矛盾状态
            #   （`{"preset":"game","backend":"strata","port":"8080"}`）——
            #   这时条件不成立，配置不会被修正，球和 8090 都以为还有个引擎在跑，
            #   于是又把 Strata 拉起来，看起来就是"切了组合模型又活了"。
            #   现在两个字段一起校验：preset 不是 game，**或者** backend 不是 off，
            #   都要重写成干净的游戏模式。
            _be = (_ai.get("backend") or "").strip().lower()
            if (_ai.get("preset") or "") != "game" or _be not in ("off", ""):
                self._save_ai_cfg({"backend": "off", "model": "", "port": "",
                                   "preset": "game"})
            # 释放全部引擎和模型（Strata + llama.cpp + Ollama 进程）
            # ★ 2026-10-17【用户要求：启动只播天气和穿衣】开机自动释放这一步
            #   以前会念「引擎与模型已退出，资源已释放」「本地运算本来就没在跑」，
            #   现在静默（气泡还留着，看得见但不吵）。
            self._engine_release(quiet=True)
        except Exception:
            pass
        # ★★ 2026-10-18【用户要求「第一次安装启动时向使用人申请权限」】
        #   走到这里说明球已经显示、启动播报也排好队了，再等 2.5 秒弹权限申请，
        #   不会和播报抢焦点。
        try:
            self.after(2500, self._ask_perms_first_run)
        except Exception:
            pass

    def _ask_help(self):
        """★ 2026-10-11【左键单击】语音询问 + 气泡提示。"""
        try:
            self.say("主人，有什么需要我帮忙的吗？", "ok")
            self._bubble("需要帮忙吗？可以说：打开软件 / 听歌识曲 / 提醒我…", 6)
        except Exception:
            pass


class UiLaunchMixin:
    """UiLaunchMixin（2026-10-23 从 FairyBall 拆出）"""

    def _open_ui(self):
        """★ 2026-10-12【打开Strata对话】右键菜单入口。
        Strata（8080）没跑就先用本地运算启动，再打开它的对话页。"""
        threading.Thread(target=self._open_ui_job, daemon=True,
                         name="fairy-openui").start()

    def _open_chat_web(self, engine="ollama", port=11434, model=""):
        """★ 2026-10-12【统一网页对话】Ollama/llm 组合切换后打开的对话页。
        起 8090 代理服务（幂等）-> 浏览器打开带参对话页，样式对齐 Strata。"""
        threading.Thread(target=self._open_chat_web_job,
                         args=(engine, port, model), daemon=True,
                         name="fairy-chatweb").start()

    def _open_chat_web_job(self, engine, port, model):
        import subprocess as _sp
        import webbrowser as _wb
        from urllib.parse import quote

        def _log(m):
            """★ 2026-10-21【为什么加日志】以前这里全程静默，弹没弹出来、
            是 8090 没起还是 webbrowser 失败，事后完全查不到。"""
            try:
                _d = getattr(fairy_root, "WORK_LOGS", None) or os.path.join(r"D:\FAIRY", "logs")
                with io.open(os.path.join(_d, "fairy_chat.log"), "a", encoding="utf-8") as _f:
                    _f.write("[%s] [chat-open] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), m))
            except Exception:
                pass

        try:
            _log("请求 engine=%s port=%s model=%s" % (engine, port, model))
            if not self._probe_port(8090, timeout=0.8):
                _exe = sys.executable.replace("python.exe", "pythonw.exe")
                _script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "fairy_chat.py")
                try:
                    _sp.Popen([_exe, _script],
                              creationflags=0x08000000,
                              stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
                except Exception:
                    _sp.Popen([sys.executable, _script],
                              creationflags=0x08000000,
                              stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
                # ★ 2026-10-21 实测 fairy_chat 冷启 4.2s；原来只等 6s，机器忙时不够 → 放宽到 20s
                for _ in range(40):
                    time.sleep(0.5)
                    if self._probe_port(8090, timeout=0.8):
                        break
            if self._probe_port(8090, timeout=0.8):
                _url = ("http://127.0.0.1:8090/?engine=%s&port=%d&model=%s"
                        % (quote(engine), int(port), quote(model or "")))
                _ok = False
                try:
                    _ok = bool(_wb.open(_url))
                except Exception as _e2:
                    _log("webbrowser.open 异常：%s" % _e2)
                if not _ok:
                    # ★ 2026-10-21 兜底：pythonw 下 webbrowser 偶尔静默失败，用系统关联直接开
                    try:
                        os.startfile(_url)
                        _ok = True
                        _log("webbrowser 失败，os.startfile 兜底成功")
                    except Exception as _e3:
                        _log("os.startfile 也失败：%s" % _e3)
                _log("打开 %s -> %s" % (_url, "成功" if _ok else "失败"))
                if not _ok:
                    self._ui_bubble("对话页已起，但浏览器没打开，请手动访问 127.0.0.1:8090", 6)
            else:
                _log("8090 启动失败（20s 未监听）")
                self._ui_bubble("对话页 8090 启动失败，请查看 logs\\fairy_chat.log")
        except Exception as _e:
            _log("异常：%s: %s" % (type(_e).__name__, _e))
            self._ui_bubble("打开对话页失败：" + str(_e)[:40])

    def _open_ui_job(self):
        """_open_ui 的实际工作（后台线程）：Strata 没跑先启动 -> 通了再开 8090 统一对话页。"""
        port = 8080
        alive = self._probe_port(port)
        if not alive:
            # Strata 没跑：先启动本地运算
            if getattr(self, "_strata_busy", False):
                self._ui_bubble("上一个操作还在进行，稍后再试。")
                return
            self._strata_start()
            for _ in range(40):
                time.sleep(3)
                if self._probe_port(port):
                    break
        if self._probe_port(port):
            # ★ 2026-10-15【统一使用】Strata 也统一打开 8090 对话页（代理到 strata），
            #   不再开 Strata 原生 8080 页；8090 由 _open_chat_web 幂等拉起。
            self._open_chat_web("strata", 8080, "")
        else:
            self._ui_bubble("Strata 还没就绪（加载模型约 1~2 分钟），稍后右键再开。")

    def _voice_set(self, name):
        """设置菜单：切换音色（写 fairy.json endpoints.ref_audio，下次播报生效）。"""
        import fairy_voice as _fv
        try:
            _n = _fv.set_voice(name)
            self._bubble("已切换音色：%s（下次播报生效）" % _n, 4)
        except Exception as _e:
            self._bubble("切换音色失败：%s" % _e, 5)

    def _voice_set_ui(self):
        """★ 2026-10-11【音色选择】统一风格弹窗：列出全部音色，点击即切换。"""
        try:
            import fairy_voice as _fv
        except Exception:
            self._bubble("音色模块不可用", 4)
            return
        _vs = _fv.list_voices() or ["fairy.wav"]
        _cur = _fv.current()
        win, wrap = self._dialog("选择音色", 360, 360)
        for _vn in _vs:
            _img = _pil_round(("✓ " if _vn == _cur else "  ") + _vn, 300, 42, 12,
                              "#ffffff", "#46536a", True, "#dfe3ea")[0]
            _b = tk.Button(wrap, image=_img, bd=0, relief="flat",
                           highlightthickness=0, bg="#f2f3f5",
                           activebackground="#f2f3f5", cursor="hand2",
                           command=(lambda n=_vn: (self._voice_set(n), win.destroy())))
            _b.image = _img
            _b.pack(pady=3)


# ---------------------------------------------------------------------------
# ★ 2026-10-23【延迟绑定】
#   Mixin 的方法体里用到了主文件的常量/函数（WIN_W / _panel_bar / make_bubble …），
#   但主文件又要 import 这些 Mixin，直接互相 import 会成环。
#   所以改成：Mixin 先定义好，主文件加载完之后调 bind_globals(globals()) 注入。
#   这样方法体一行不用改，import 也不会成环。
# ---------------------------------------------------------------------------
_NEEDED = None


def bind_globals(g):
    """由 fairy_ball 在模块加载完成后调用，把缺失的全局名注入本模块。"""
    global _NEEDED
    if _NEEDED is None:
        _NEEDED = (
            "BALANCE_REFRESH_S BAR_W GAP MARGIN POS_FILE SIZE SPEAK_DONE "
            "STRATA_DIR STRATA_LOAD_URL STRATA_LOG STRATA_PORT STRATA_STATUS_URL "
            "STRATA_UNLOAD_URL VOICE_INDEX WIN_H WIN_W _EDGE_PX "
            "_PANEL_FUSE_FILL _PANEL_RINSET _PANEL_RPAD "
            "_free_ram_gb _hw_color _panel_backing _panel_bar _pil_round _ptext "
            "load_frames make_bubble _ball_backing "
            "_resolve_strata_dir _rnw _strata_cmd "
            "ctypes fairy_root fix_mojibake io json make_bubble os read_balance "
            "retail_bubble sys threading time tk urllib virtual_screen"
        ).split()
    got, miss = [], []
    for k in _NEEDED:
        if k in g and k not in globals():
            globals()[k] = g[k]
            got.append(k)
        elif k not in globals():
            miss.append(k)
    return got, miss
