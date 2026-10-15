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
    """在选区图上打开标注窗口（复用原标注能力）。"""
    from PIL import ImageTk as _ITK
    from PIL import ImageDraw as _ID
    import tkinter.filedialog as _fd
    _root = tk.Tk()
    _root.title("Fairy 截图标注（画完 Ctrl+S 保存 / Esc 退出）")
    _root.attributes("-topmost", True)
    _img = Image.open(path)
    _w, _h = _img.size
    _cv = tk.Canvas(_root, width=_w, height=_h, highlightthickness=0)
    _cv.pack()
    _ph = _ITK.PhotoImage(_img)
    _cv.create_image(0, 0, image=_ph, anchor="nw")
    _tool = {"t": "rect", "color": "#ff0000", "width": 3}
    _start = {"p": None}
    _prev = {"id": None}
    _pil = _ID.Draw(_img)

    def _press(e):
        _start["p"] = (e.x, e.y)

    def _drag(e):
        if _start["p"] is None:
            return
        if _prev["id"] is not None:
            try:
                _cv.delete(_prev["id"])
            except Exception:
                pass
        x0, y0 = _start["p"]
        _prev["id"] = _cv.create_rectangle(x0, y0, e.x, e.y,
                                           outline=_tool["color"], width=_tool["width"])

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
        _pil.rectangle([x0, y0, e.x, e.y], outline=_tool["color"], width=_tool["width"])

    def _key(e):
        k = e.keysym.lower()
        if k == "c":
            _img2 = Image.open(path)
            _cv.create_image(0, 0, image=_ITK.PhotoImage(_img2), anchor="nw")
            _img.paste(_img2)
        elif k == "s" and e.state & 4:
            _img.save(path)
            _mb.showinfo("Fairy 截图标注", "已保存：\n%s" % path)
            _root.destroy()
        elif k == "escape":
            _root.destroy()

    _cv.bind("<ButtonPress-1>", _press)
    _cv.bind("<B1-Motion>", _drag)
    _cv.bind("<ButtonRelease-1>", _release)
    _root.bind("<Key>", _key)
    _root.mainloop()
    print(json.dumps({"ok": True, "path": path}), flush=True)


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
            print(json.dumps({"ok": bool(_d.get("ok")), "lines": _d.get("lines") or [],
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
    for base in (os.path.dirname(HERE), r"<FAIRY_ROOT>"):
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
