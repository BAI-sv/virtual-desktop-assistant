"""Fairy 截图 OCR（★ 2026-10-11 新增）：屏幕取字工具。

基于 RapidOCR（PaddleOCR 模型转 ONNX，轻量离线 CPU 秒出）。
用法：
    py fairy_ocr.py --full                 # 全屏截图识别
    py fairy_ocr.py --region 100 200 800 600   # 区域截图识别
    py fairy_ocr.py --file x.png           # 识别图片文件
输出：每行文本。--json 输出结构化 JSON。
"""
import argparse
import json
import sys


def grab_screen(region=None):
    from PIL import ImageGrab
    if region:
        x, y, w, h = region
        return ImageGrab.grab(bbox=(int(x), int(y), int(x + w), int(y + h)))
    return ImageGrab.grab()


def ocr_image(img):
    from rapidocr import RapidOCR
    engine = RapidOCR()
    result = engine(img)
    # 新版（2.x）返回 RapidOCROutput 对象：用 .txts / .boxes / .scores
    if hasattr(result, "txts"):
        return [t for t in (result.txts or []) if t and str(t).strip()]
    # 旧版返回 (result_list, elapse)
    if isinstance(result, tuple):
        result = result[0]
    lines = []
    for item in result or []:
        if isinstance(item, dict):
            txt = item.get("text") or ""
        else:
            txt = item[1] if len(item) > 1 else item
        txt = str(txt).strip()
        if txt:
            lines.append(txt)
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="全屏截图识别")
    ap.add_argument("--region", nargs=4, type=int, metavar=("X", "Y", "W", "H"),
                    help="区域截图识别（屏幕坐标）")
    ap.add_argument("--file", help="识别图片文件")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args()

    try:
        if a.file:
            from PIL import Image
            img = Image.open(a.file)
        elif a.region:
            img = grab_screen(a.region)
        else:
            img = grab_screen()
        lines = ocr_image(img)
        if a.json:
            print(json.dumps({"ok": True, "lines": lines}, ensure_ascii=False))
        else:
            for ln in lines:
                print(ln)
        return 0
    except Exception as e:
        if a.json:
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        else:
            print("识别失败: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
