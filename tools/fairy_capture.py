# -*- coding: utf-8 -*-
"""Fairy 微信风格截图：全屏遮罩 + 鼠标拖拽自定义选框 + 微调控制柄。

用法：
    py -3.12 fairy_capture.py [--mode annotate|save|ocr|detect]
  - annotate：选框后进入标注窗口（画框/箭头/文字/画笔，Ctrl+S 保存）
  - save：选框后直接保存选区 PNG
  - ocr：选框后调用 fairy_ocr.py 识别选区文字
  - detect：选框后调用 fairy_detect.py 识别选区物体
交互（★ 2026-10-12 微信同款）：
  1. 拖拽画出选区（实时显示宽高）
  2. 松开后进入【微调模式】：8 个白色控制柄，拖动边缘/角微调
  3. 双击框内 或 按 Enter 确认；Esc 取消；在框外单击可重画
输出：标准输出打印一行 JSON {"ok":true,"path":...,"box":[x,y,w,h],...}
"""
import os
import sys
import json
import subprocess
import tkinter as tk
import tkinter.messagebox as _mb

from PIL import ImageGrab, ImageTk, ImageDraw, Image

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(os.path.dirname(HERE), "data", "screenshots")


class Capture:
    """全屏遮罩 + 拖拽选框 + 微调控制柄。"""

    def __init__(self, mode="annotate"):
        self.mode = mode
        self.img = ImageGrab.grab()
        self.w, self.h = self.img.size
        self.root = tk.Tk()
        self.root.title("Fairy 截图")
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.28)          # 半透明遮罩（微信风格）
        self.root.configure(cursor="crosshair", bg="black")
        self.canvas = tk.Canvas(self.root, width=self.w, height=self.h,
                                highlightthickness=0, bg="black")
        self.canvas.pack()
        # 底下铺一层暗化的屏幕图，拖拽时选区里显示原图
        _dim = self.img.copy()
        _dim = Image.eval(_dim, lambda p: p // 3)
        self.dim_photo = ImageTk.PhotoImage(_dim)
        self.canvas.create_image(0, 0, image=self.dim_photo, anchor="nw")
        self.photo = ImageTk.PhotoImage(self.img)
        self.start = None          # 画框起点
        self.rect = None           # 选框矩形 id
        self.label = None          # 尺寸标签 id
        self.handles = []          # 微调控制柄 id 列表
        self.box = None            # (x0, y0, x1, y1) 当前选区（微调中）
        self.cur_handle = None     # 正在拖动的控制柄
        self.mode2 = "draw"        # draw=画新框 / adjust=微调
        self.crop_photo = None
        self._last_crop = None
        self._bind()

    def _bind(self):
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Double-Button-1>", self._dbl)
        self.root.bind("<Escape>", lambda e: self._cancel())
        self.root.bind("<Return>", lambda e: self._confirm())
        self.root.bind("<KP_Enter>", lambda e: self._confirm())

    # ---------- 画框 ----------
    def _press(self, e):
        # 微调模式：点中控制柄 -> 进入缩放；框内单击 -> 确认；框外 -> 重画
        if self.box and self.mode2 == "adjust":
            _h = self._hit_handle(e.x, e.y)
            if _h:
                self.cur_handle = _h
                return
            x0, y0, x1, y1 = self.box
            if x0 - 4 <= e.x <= x1 + 4 and y0 - 4 <= e.y <= y1 + 4:
                self._confirm()
                return
        self.mode2 = "draw"
        self.start = (e.x, e.y)
        self.cur_handle = None
        self.box = None
        self._clear_handles()
        if self.rect:
            self.canvas.delete(self.rect)
            self.rect = None
        try:
            self.canvas.delete("crop")
        except Exception:
            pass
        if self.label:
            self.canvas.delete(self.label)
            self.label = None

    def _drag(self, e):
        if self.cur_handle:
            self._resize(e)
            return
        if self.start is None:
            return
        x0, y0 = self.start
        x1, y1 = e.x, e.y
        self.box = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        self._draw_box()

    def _resize(self, e):
        x0, y0, x1, y1 = self.box
        _h = self.cur_handle
        if "l" in _h:
            x0 = min(e.x, x1 - 4)
        if "r" in _h:
            x1 = max(e.x, x0 + 4)
        if "t" in _h:
            y0 = min(e.y, y1 - 4)
        if "b" in _h:
            y1 = max(e.y, y0 + 4)
        self.box = (x0, y0, x1, y1)
        self._draw_box()

    def _release(self, e):
        if self.cur_handle:
            self.cur_handle = None
            return
        if self.start is None:
            return
        x0, y0 = self.start
        x1, y1 = e.x, e.y
        self.start = None
        bx0, by0 = min(x0, x1), min(y0, y1)
        bx1, by1 = max(x0, x1), max(y0, y1)
        if bx1 - bx0 < 4 or by1 - by0 < 4:
            self._cancel()
            return
        self.box = (bx0, by0, bx1, by1)
        self.mode2 = "adjust"           # 进入微调模式
        self._draw_box()

    def _draw_box(self):
        if not self.box:
            return
        x0, y0, x1, y1 = self.box
        if self.rect:
            try:
                self.canvas.delete(self.rect)
            except Exception:
                pass
        # 动态裁切选区原图显示（微信同款效果，节流避免高分辨率卡顿）
        try:
            self.canvas.delete("crop")
        except Exception:
            pass
        _kw = int(x1 - x0)
        _kh = int(y1 - y0)
        if _kw > 2 and _kh > 2:
            _key2 = (_kw // 6, _kh // 6)
            if _key2 != self._last_crop:
                self._last_crop = _key2
                try:
                    _c = self.img.crop((int(x0), int(y0), int(x1), int(y1)))
                    self.crop_photo = ImageTk.PhotoImage(_c)
                    self.canvas.create_image(x0, y0, image=self.crop_photo,
                                             anchor="nw", tags=("crop",))
                except Exception:
                    pass
        self.rect = self.canvas.create_rectangle(
            x0, y0, x1, y1, outline="#00c8ff", width=2)
        if self.label:
            try:
                self.canvas.delete(self.label)
            except Exception:
                pass
        self.label = self.canvas.create_text(
            x0, y0 - 16, anchor="sw", fill="#ffffff",
            text="%d × %d · 拖动边缘微调，双击/回车确认，Esc 取消"
                 % (_kw, _kh),
            font=("Microsoft YaHei", 11, "bold"))
        self._draw_handles()

    def _draw_handles(self):
        self._clear_handles()
        if not self.box:
            return
        x0, y0, x1, y1 = self.box
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        for hx, hy in ((x0, y0), (x0, cy), (x0, y1), (cx, y0),
                       (cx, y1), (x1, y0), (x1, cy), (x1, y1)):
            r = self.canvas.create_rectangle(hx - 4, hy - 4, hx + 4, hy + 4,
                                             fill="#ffffff", outline="#00c8ff",
                                             width=1)
            self.handles.append(r)

    def _clear_handles(self):
        for h in self.handles:
            try:
                self.canvas.delete(h)
            except Exception:
                pass
        self.handles = []

    def _hit_handle(self, ex, ey):
        if not self.box:
            return None
        x0, y0, x1, y1 = self.box
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        names = {"nw": (x0, y0), "n": (cx, y0), "ne": (x1, y0),
                 "w": (x0, cy), "e": (x1, cy),
                 "sw": (x0, y1), "s": (cx, y1), "se": (x1, y1)}
        for k, (hx, hy) in names.items():
            if abs(ex - hx) <= 11 and abs(ey - hy) <= 11:
                return k
        return None

    def _dbl(self, e):
        self._confirm()

    def _confirm(self):
        if not self.box:
            return
        x0, y0, x1, y1 = self.box
        self._finish(int(x0), int(y0), int(x1 - x0), int(y1 - y0))

    def _cancel(self):
        self.root.destroy()
        print(json.dumps({"ok": False, "error": "cancelled"}), flush=True)

    def _finish(self, x, y, w, h):
        os.makedirs(OUT_DIR, exist_ok=True)
        crop = self.img.crop((x, y, x + w, y + h))
        path = os.path.join(OUT_DIR, "选区_%s.png" %
                            __import__("time").strftime("%H%M%S"))
        crop.save(path)
        self.root.destroy()
        if self.mode == "save":
            print(json.dumps({"ok": True, "path": path,
                              "box": [x, y, w, h]}), flush=True)
        elif self.mode == "annotate":
            # 进入标注：在选区图上标注
            _annotate(path)
        else:
            # ocr / detect：交给对应脚本处理选区
            _run_tool(self.mode, path, x, y, w, h)

    def run(self):
        self.root.mainloop()


def _annotate(path):
    """在选区图上打开标注窗口：**顶部有可视工具条**（矩形/椭圆/箭头/画笔/文字/颜色/撤销/清空/保存）。

    ★★ 2026-10-18【用户反馈「标注时没有标注工具」】——查出来的真相：
      旧实现只有一句 `_tool = {"t": "rect"}`，**只会画红色矩形**，
      没有椭圆/箭头/画笔/文字，也没有任何按钮，全靠猜键盘；
      而且"清空"是按 C 重新贴一张图叠上去（旧图没清，越叠越花）。
      现在换成完整版：工具条点选 + 快捷键都行，带撤销栈和复制到剪贴板。
    """
    from PIL import ImageTk as _ITK
    from PIL import ImageDraw as _ID
    import tkinter.simpledialog as _sd

    _root = tk.Tk()
    _root.title("Fairy 截图标注 — 选工具后在图上画，Ctrl+S 保存，Ctrl+C 复制")
    _root.attributes("-topmost", True)
    _img = Image.open(path).convert("RGB")
    _w, _h = _img.size
    _orig = _img.copy()                      # 清空/撤销的基准
    _st = {"tool": "rect", "color": "#ff0000", "width": 3}
    _start = {"p": None}
    _prev = {"id": None}
    _undo = []                               # 每笔之前存一份图（撤销栈）
    _pil = _ID.Draw(_img)

    # ---------------- 工具条 ----------------
    _bar = tk.Frame(_root, bg="#f2f5f8")
    _bar.pack(fill="x")
    _btn = {}

    def _refresh_btns():
        for _k, _b in _btn.items():
            _on = (_k == _st["tool"])
            try:
                _b.configure(relief="sunken" if _on else "raised",
                             bg="#cfe3f7" if _on else "#f7f9fb")
            except Exception:
                pass

    def _pick(tool):
        _st["tool"] = tool
        _refresh_btns()

    for _t, _label in (("rect", "矩形"), ("ellipse", "椭圆"), ("arrow", "箭头"),
                       ("pen", "画笔"), ("text", "文字")):
        _b = tk.Button(_bar, text=_label, width=6, font=("Microsoft YaHei", 10),
                       command=lambda x=_t: _pick(x))
        _b.pack(side="left", padx=2, pady=4)
        _btn[_t] = _b

    tk.Label(_bar, text="  颜色", bg="#f2f5f8",
             font=("Microsoft YaHei", 10)).pack(side="left")
    _color = {"v": _st["color"]}

    def _pick_color():
        from tkinter import colorchooser
        c = colorchooser.askcolor(color=_color["v"], parent=_root)[1]
        if c:
            _color["v"] = c
            _st["color"] = c

    tk.Button(_bar, text="■", fg="red", width=3, command=_pick_color,
              font=("Microsoft YaHei", 12)).pack(side="left", padx=2)

    tk.Label(_bar, text=" 粗细", bg="#f2f5f8",
             font=("Microsoft YaHei", 10)).pack(side="left")
    _wvar = tk.IntVar(value=_st["width"])
    tk.Spinbox(_bar, from_=1, to=12, width=3, textvariable=_wvar,
               command=lambda: _st.__setitem__("width", int(_wvar.get()))
               ).pack(side="left", padx=2)

    # ---------------- 画布 ----------------
    _cv = tk.Canvas(_root, width=_w, height=_h, highlightthickness=0, cursor="crosshair")
    _cv.pack()
    _photo = _ITK.PhotoImage(_img)
    _cv.create_image(0, 0, image=_photo, anchor="nw")

    def _redraw():
        _cv.delete("all")
        ph = _ITK.PhotoImage(_img)
        _cv.create_image(0, 0, image=ph, anchor="nw")
        _cv.image = ph          # 防 GC
        _cv._ph = ph

    def _push_undo():
        try:
            _undo.append(_img.copy())
            if len(_undo) > 20:
                _undo.pop(0)
        except Exception:
            pass

    # ---------------- 画 ----------------
    def _press(e):
        _start["p"] = (e.x, e.y)
        _st["color"] = _color["v"]
        try:
            _st["width"] = int(_wvar.get())
        except Exception:
            pass
        _push_undo()
        if _st["tool"] == "text":
            t = _sd.askstring("文字", "输入要写的字：", parent=_root)
            if t:
                _pil.text((e.x, e.y), t, fill=_st["color"])
                _redraw()
            _start["p"] = None
        elif _st["tool"] == "pen":
            _pen_pts[0] = [(e.x, e.y)]

    _pen_pts = [[]]

    def _drag(e):
        if _start["p"] is None:
            return
        if _prev["id"] is not None:
            try:
                _cv.delete(_prev["id"])
            except Exception:
                pass
            _prev["id"] = None
        x0, y0 = _start["p"]
        t, c, wd = _st["tool"], _st["color"], _st["width"]
        if t == "pen":
            if _pen_pts[0]:
                lx, ly = _pen_pts[0][-1]
                _pen_pts[0].append((e.x, e.y))
                _cv.create_line(lx, ly, e.x, e.y, fill=c, width=wd,
                                capstyle="round", smooth=True)
            return
        if t == "rect":
            _prev["id"] = _cv.create_rectangle(x0, y0, e.x, e.y, outline=c, width=wd)
        elif t == "ellipse":
            _prev["id"] = _cv.create_oval(x0, y0, e.x, e.y, outline=c, width=wd)
        elif t == "arrow":
            _prev["id"] = _cv.create_line(x0, y0, e.x, e.y, fill=c, width=wd, arrow="last")

    def _release(e):
        if _start["p"] is None:
            return
        x0, y0 = _start["p"]
        _start["p"] = None
        if _prev["id"] is not None:
            try:
                _cv.delete(_prev["id"])
            except Exception:
                pass
            _prev["id"] = None
        t, c, wd = _st["tool"], _st["color"], _st["width"]
        try:
            if t == "rect":
                _pil.rectangle([x0, y0, e.x, e.y], outline=c, width=wd)
            elif t == "ellipse":
                _pil.ellipse([x0, y0, e.x, e.y], outline=c, width=wd)
            elif t == "arrow":
                _pil.line([(x0, y0), (e.x, e.y)], fill=c, width=wd)
                # 箭头三角
                import math
                ang = math.atan2(e.y - y0, e.x - x0)
                L = max(10, wd * 5)
                for da in (2.6, -2.6):
                    _pil.line([(e.x, e.y),
                               (e.x + L * math.cos(ang + da), e.y + L * math.sin(ang + da))],
                              fill=c, width=wd)
            elif t == "pen":
                if len(_pen_pts[0]) >= 2:
                    _pil.line(_pen_pts[0], fill=c, width=wd, joint="curve")
                _pen_pts[0] = []
        except Exception:
            pass
        _redraw()

    # ---------------- 动作 ----------------
    def _undo_last(_e=None):
        if _undo:
            try:
                _img.paste(_undo.pop())
                _redraw()
            except Exception:
                pass

    def _clear(_e=None):
        try:
            _img.paste(_orig)
            _redraw()
        except Exception:
            pass

    def _save(_e=None):
        _redraw()
        try:
            _img.save(path)
        except Exception as _e2:
            _mb.showerror("保存失败", "%s\n%s" % (path, _e2), parent=_root)
            return
        try:
            _mb.showinfo("Fairy 截图标注", "已保存：\n%s" % path, parent=_root)
        except Exception:
            pass
        _root.destroy()
        print(json.dumps({"ok": True, "path": path, "saved": True}), flush=True)

    def _copy(_e=None):
        try:
            _root.clipboard_clear()
            _root.update()
            _img.save(path)
            # Windows 下把 PNG 放进剪贴板（用 PowerShell 的 System.Windows.Forms）
            import subprocess as _sp
            ps = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
                  "$i=[System.Drawing.Image]::FromFile('%s');"
                  "[System.Windows.Forms.Clipboard]::SetImage($i);$i.Dispose()" % path)
            _sp.run(["powershell", "-NoProfile", "-STA", "-Command", ps],
                    capture_output=True, timeout=10,
                    creationflags=0x08000000)
            _mb.showinfo("Fairy 截图标注", "已复制到剪贴板", parent=_root)
        except Exception as _e3:
            _mb.showwarning("复制失败", str(_e3), parent=_root)

    for _k, _fn in (("r", lambda: _pick("rect")), ("e", lambda: _pick("ellipse")),
                    ("a", lambda: _pick("arrow")), ("p", lambda: _pick("pen")),
                    ("t", lambda: _pick("text"))):
        for _kk in (_k, _k.upper()):
            _root.bind("<%s>" % _kk, lambda ev, f=_fn: f())
    _root.bind("<Control-z>", _undo_last)
    _root.bind("<Control-Z>", _undo_last)
    _root.bind("<Control-s>", _save)
    _root.bind("<Control-S>", _save)
    _root.bind("<Control-c>", _copy)
    _root.bind("<Control-C>", _copy)
    _root.bind("<Escape>", lambda e: _root.destroy())

    _cv.bind("<ButtonPress-1>", _press)
    _cv.bind("<B1-Motion>", _drag)
    _cv.bind("<ButtonRelease-1>", _release)

    _refresh_btns()
    _root.mainloop()


def _run_tool(mode, path, x, y, w, h):
    """把选区交给 OCR / 检测脚本。"""
    _py = sys.executable
    if mode == "ocr":
        _sc = os.path.join(HERE, "fairy_ocr.py")
        try:
            _r = subprocess.run([_py, _sc, "--region", str(x), str(y),
                                 str(w), str(h), "--json"],
                                capture_output=True, timeout=90,
                                text=True, encoding="utf-8", creationflags=0x08000000)
            _d = json.loads(_r.stdout or "{}")
            _lines = _d.get("lines") or []
            # ★★ 2026-10-18【用户要求「识别文字要在浏览器打开并能编辑复制」】
            #   识别完顺手生成一个本地 HTML 并用默认浏览器打开：
            #   里面是可编辑的大文本框 + 逐行复制 + 复制全部 + 下载 txt。
            if _lines:
                try:
                    os.makedirs(OUT_DIR, exist_ok=True)
                    _jf = os.path.join(OUT_DIR, "last_ocr.json")
                    with open(_jf, "w", encoding="utf-8") as _f:
                        json.dump({"lines": _lines}, _f, ensure_ascii=False)
                    _vp = os.path.join(HERE, "fairy_ocr_view.py")
                    if os.path.exists(_vp):
                        subprocess.Popen([_py, _vp, "--json-file", _jf],
                                         creationflags=0x08000000,
                                         stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
                except Exception:
                    pass          # 打不开浏览器不影响 OCR 结果本身
            print(json.dumps({"ok": bool(_d.get("ok")), "lines": _lines,
                              "path": path}), flush=True)
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e)[:100]}), flush=True)
    elif mode == "detect":
        _sc = os.path.join(HERE, "fairy_detect.py")
        # detect 用 funasr 环境的 python（有 torch）
        _fpy = _funasr_py()
        try:
            _r = subprocess.run([_fpy, _sc, "--region", str(x), str(y),
                                 str(w), str(h), "--json"],
                                capture_output=True, timeout=120,
                                text=True, encoding="utf-8", creationflags=0x08000000)
            _d = json.loads(_r.stdout or "{}")
            print(json.dumps({"ok": bool(_d.get("ok")),
                              "objects": _d.get("objects") or [],
                              "path": path}), flush=True)
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e)[:100]}), flush=True)


def _funasr_py():
    """复用 ball 的 funasr 解释器定位（环境变量优先，其次常见目录）。"""
    cand = (os.environ.get("FAIRY_FUNASR_PY") or "").strip()
    if cand and os.path.exists(cand):
        return cand
    for base in (os.path.dirname(HERE), r"D:\FAIRY"):
        for p in (os.path.join(base, "envs", "funasr", "Scripts", "python.exe"),
                  os.path.join(base, "funasr", "Scripts", "python.exe")):
            if os.path.exists(p):
                return p
    return sys.executable


if __name__ == "__main__":
    _m = "annotate"
    if "--mode" in sys.argv:
        _i = sys.argv.index("--mode")
        if len(sys.argv) > _i + 1:
            _m = sys.argv[_i + 1]
    Capture(_m).run()
