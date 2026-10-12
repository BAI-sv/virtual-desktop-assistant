"""Fairy 屏幕取色器（★ 2026-10-11 新增）：放大镜取色，点击复制颜色代码。

用法：py fairy_color.py
    全屏半透明取色窗：鼠标移动实时显示颜色（放大镜预览 + RGB + HEX），
    左键点击复制 HEX 到剪贴板，右键或 Esc 退出。
"""
import tkinter as tk
import sys

from PIL import Image, ImageGrab


class ColorPicker:
    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.9)
        self.screen_w = self.root.winfo_screenwidth()
        self.screen_h = self.root.winfo_screenheight()
        self.root.geometry("%dx%d+0+0" % (self.screen_w, self.screen_h))
        self.root.config(cursor="crosshair")
        self.canvas = tk.Canvas(self.root, width=self.screen_w,
                                height=self.screen_h, highlightthickness=0)
        self.canvas.pack()
        self._panel = None
        self._follow()
        self.canvas.bind("<Motion>", self._motion)
        self.canvas.bind("<Button-1>", self._pick)
        self.canvas.bind("<Button-3>", lambda e: self.root.destroy())
        self.root.bind("<Escape>", lambda e: self.root.destroy())

    def _follow(self):
        try:
            x, y = self.root.winfo_pointerx(), self.root.winfo_pointery()
            self._motion(None, x, y)
        except Exception:
            pass
        self.root.after(50, self._follow)

    def _rgb_at(self, x, y):
        img = ImageGrab.grab(bbox=(max(0, x - 12), max(0, y - 12),
                                   min(self.screen_w, x + 12),
                                   min(self.screen_h, y + 12)))
        img = img.resize((120, 120))
        return img, ImageGrab.grab(bbox=(x, y, x + 1, y + 1)).getpixel((0, 0))

    def _motion(self, _e, x=None, y=None):
        if x is None:
            x, y = self.root.winfo_pointerx(), self.root.winfo_pointery()
        try:
            img, px = self._rgb_at(x, y)
            from PIL import ImageTk
            self._tk = ImageTk.PhotoImage(img)
            if self._panel:
                self.canvas.delete(self._panel)
            self._panel = self.canvas.create_image(x + 20 if x < self.screen_w - 150 else x - 140,
                                                   y + 20 if y < self.screen_h - 150 else y - 140,
                                                   image=self._tk, anchor="nw")
            hexv = "#%02X%02X%02X" % px
            self.canvas.delete("txt")
            self.canvas.create_text(
                x + 20 if x < self.screen_w - 150 else x - 140,
                y + 20 if y < self.screen_h - 150 else y - 140,
                text="%s  RGB%s" % (hexv, px), fill="#00FF00",
                font=("Consolas", 14, "bold"), tags="txt", anchor="nw")
        except Exception:
            pass

    def _pick(self, e):
        try:
            px = ImageGrab.grab(bbox=(e.x, e.y, e.x + 1, e.y + 1)).getpixel((0, 0))
            hexv = "#%02X%02X%02X" % px
            import subprocess
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Set-Clipboard -Value '%s'" % hexv],
                           capture_output=True, timeout=8)
            self.root.destroy()
            print(hexv)
        except Exception as ex:
            print("取色失败: %s" % ex)
            self.root.destroy()


if __name__ == "__main__":
    ColorPicker().root.mainloop()
