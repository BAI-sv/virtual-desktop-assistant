"""Fairy 屏幕常驻 OCR 悬浮窗（★ 2026-10-11 新增）：框选区域实时取字。

用法：py fairy_overlay_ocr.py --region X Y W H
    半透明悬浮窗实时显示该区域的识别文字（每 2 秒刷新一次），
    可拖动、可调大小；Esc 或右键退出。
"""
import argparse
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk

from PIL import ImageGrab

INTERVAL = 2.0


def ocr_worker(region, out_q, stop):
    while not stop.is_set():
        try:
            r = subprocess.run([sys.executable, "fairy_ocr.py", "--region",
                                str(region[0]), str(region[1]),
                                str(region[2]), str(region[3]), "--json"],
                               capture_output=True, timeout=8, text=True,
                               encoding="utf-8", cwd=sys.path[0] or ".")
            import json
            d = json.loads(r.stdout or "{}")
            lines = d.get("lines") or []
            out_q.put("\n".join(lines) if lines else "")
        except Exception:
            pass
        for _ in range(int(INTERVAL * 2)):
            if stop.is_set():
                return
            time.sleep(0.5)


class OverlayOCR:
    def __init__(self, region):
        self.region = region
        self.stop = threading.Event()
        self.q = queue.Queue()
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.88)
        self.root.geometry("%dx%d+%d+%d" % (region[2], region[3], region[0], region[1]))
        self.root.config(bg="#101018")
        self.txt = tk.Text(self.root, bg="#101018", fg="#E8E8FF",
                           font=("Microsoft YaHei", 11), wrap="word",
                           insertbackground="#E8E8FF", relief="flat",
                           padx=8, pady=6)
        self.txt.pack(fill="both", expand=True)
        self.txt.insert("1.0", "常驻取字中…（Esc 退出）")
        self.txt.config(state="disabled")
        self._drag()
        self.root.bind("<Escape>", lambda e: self._close())
        self.root.bind("<Button-3>", lambda e: self._close())
        threading.Thread(target=ocr_worker, args=(region, self.q, self.stop),
                         daemon=True).start()
        self._poll()

    def _drag(self):
        self._ox = self._oy = None

        def down(e):
            self._ox, self._oy = e.x_root, e.y_root

        def move(e):
            if self._ox is not None:
                x, y = self.root.winfo_x() + (e.x_root - self._ox), \
                       self.root.winfo_y() + (e.y_root - self._oy)
                self.root.geometry("+%d+%d" % (x, y))
                self._ox, self._oy = e.x_root, e.y_root

        def up(_e):
            self._ox = self._oy = None
        self.txt.bind("<Button-1>", down)
        self.txt.bind("<B1-Motion>", move)
        self.txt.bind("<ButtonRelease-1>", up)

    def _poll(self):
        try:
            while True:
                text = self.q.get_nowait()
                self.txt.config(state="normal")
                self.txt.delete("1.0", "end")
                self.txt.insert("1.0", text if text else "（未识别到文字）")
                self.txt.config(state="disabled")
        except queue.Empty:
            pass
        self.root.after(500, self._poll)

    def _close(self):
        self.stop.set()
        self.root.destroy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", nargs=4, type=int, metavar=("X", "Y", "W", "H"),
                    required=True, help="常驻识别区域（屏幕坐标）")
    a = ap.parse_args()
    OverlayOCR(a.region).root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
