# -*- coding: utf-8 -*-
"""Fairy 截图标注：截屏后画框/箭头/文字/画笔，保存 PNG。

用法：
    py -3.12 fairy_screenshot.py
快捷键：R 矩形 · E 椭圆 · A 箭头 · P 画笔 · T 文字 · C 清空 · Ctrl+S 保存
"""
import os
import sys
import tkinter as tk
from tkinter import filedialog

from PIL import ImageGrab, ImageTk, ImageDraw

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "data", "screenshots")


class Annotator:
    def __init__(self):
        self.img = ImageGrab.grab()
        self.w, self.h = self.img.size
        self.root = tk.Tk()
        self.root.title("Fairy 截图标注（画完 Ctrl+S 保存）")
        self.root.attributes("-topmost", True)
        self.canvas = tk.Canvas(self.root, width=self.w, height=self.h,
                                highlightthickness=0)
        self.canvas.pack()
        self.photo = ImageTk.PhotoImage(self.img)
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        self.tool = "rect"
        self.color = "#ff0000"
        self.width = 3
        self.start = None
        self.last = None
        self.items = []      # (canvas_item, [PIL 绘制参数])
        self.pil = ImageDraw.Draw(self.img)
        self._bind()

    def _bind(self):
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.root.bind("<Key>", self._key)
        self.root.bind("<Control-s>", lambda e: self._save())
        self.root.bind("<Control-S>", lambda e: self._save())

    def _key(self, e):
        k = e.keysym.lower()
        if k == "r":
            self.tool = "rect"
        elif k == "e":
            self.tool = "ellipse"
        elif k == "a":
            self.tool = "arrow"
        elif k == "p":
            self.tool = "pen"
        elif k == "t":
            self.tool = "text"
        elif k == "c":
            self._clear()
        elif k == "s" and e.state & 4:
            self._save()

    def _press(self, e):
        self.start = (e.x, e.y)
        self.last = (e.x, e.y)
        if self.tool == "text":
            import tkinter.simpledialog as _sd
            t = _sd.askstring("文字", "输入要写的字：", parent=self.root)
            if t:
                self.canvas.create_text(e.x, e.y, text=t, fill=self.color,
                                        font=("Microsoft YaHei", int(self.width * 5)))
                self.pil.text((e.x, e.y), t, fill=self.color)
            self.start = None
        elif self.tool == "pen":
            self.items.append(self.canvas.create_line(e.x, e.y, e.x + 1, e.y + 1,
                                                      fill=self.color, width=self.width,
                                                      capstyle="round"))

    def _drag(self, e):
        if self.start is None or self.tool == "text":
            return
        if self.tool == "pen":
            self.canvas.create_line(self.last[0], self.last[1], e.x, e.y,
                                    fill=self.color, width=self.width, capstyle="round")
            self.pil.line([self.last, (e.x, e.y)], fill=self.color, width=self.width)
            self.last = (e.x, e.y)
            return
        # 矩形/椭圆/箭头：实时预览（先删上一个预览项）
        if getattr(self, "_preview", None) is not None:
            try:
                self.canvas.delete(self._preview)
            except Exception:
                pass
            self._preview = None
        x0, y0 = self.start
        if self.tool == "rect":
            self._preview = self.canvas.create_rectangle(
                x0, y0, e.x, e.y, outline=self.color, width=self.width)
        elif self.tool == "ellipse":
            self._preview = self.canvas.create_oval(
                x0, y0, e.x, e.y, outline=self.color, width=self.width)
        elif self.tool == "arrow":
            self._preview = self.canvas.create_line(
                x0, y0, e.x, e.y, fill=self.color, width=self.width, arrow="last")

    def _release(self, e):
        if self.start is None or self.tool in ("pen", "text"):
            self.start = None
            return
        x0, y0 = self.start
        self.start = None
        if self.tool == "rect":
            self.pil.rectangle([x0, y0, e.x, e.y], outline=self.color, width=self.width)
        elif self.tool == "ellipse":
            self.pil.ellipse([x0, y0, e.x, e.y], outline=self.color, width=self.width)
        elif self.tool == "arrow":
            self.pil.line([(x0, y0), (e.x, e.y)], fill=self.color, width=self.width)
        self._preview = None

    def _clear(self):
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        self.img = ImageGrab.grab()
        self.pil = ImageDraw.Draw(self.img)

    def _save(self):
        os.makedirs(OUT_DIR, exist_ok=True)
        p = os.path.join(OUT_DIR, "标注_%s.png" %
                         __import__("time").strftime("%H%M%S"))
        try:
            self.img.save(p)
            tk.messagebox.showinfo("Fairy 截图标注", "已保存：\n%s" % p, parent=self.root)
        except Exception as e:
            tk.messagebox.showerror("保存失败", str(e), parent=self.root)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    Annotator().run()
