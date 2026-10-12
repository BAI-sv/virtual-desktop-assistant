"""Fairy 目标检测（★ 2026-10-11 新增）：截图中识别物体。

基于 Ultralytics YOLO11n（COCO 80 类，模型约 5MB，效果/速度平衡最好）。
注意：本脚本用 funasr 环境的 python 跑（复用其 torch）：
    用法示例：<项目根>/envs/funasr/Scripts/python.exe fairy_detect.py --full
用法：
    --full                全屏检测
    --region X Y W H      区域检测
    --file x.png          检测图片文件
    --model yolov11n.pt   指定模型（自动下载）
    --conf 0.35           置信度阈值
    --json                输出 JSON
"""
import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_DIR = os.environ.get("FAIRY_MODELS_DIR", os.path.join(_ROOT, "models"))
DEFAULT_MODEL = "yolov11n.pt"


def grab_screen(region=None):
    from PIL import ImageGrab
    if region:
        x, y, w, h = region
        return ImageGrab.grab(bbox=(int(x), int(y), int(x + w), int(y + h)))
    return ImageGrab.grab()


def load_model(path):
    from ultralytics import YOLO
    full = path if os.path.isabs(path) else os.path.join(MODEL_DIR, path)
    if not os.path.exists(full):
        full = path  # 让 ultralytics 自己下载到默认位置
    return YOLO(full)


def detect(img, model, conf):
    results = model(img, conf=conf, verbose=False)
    objs = []
    for r in results:
        names = r.names
        for box in r.boxes:
            cls = int(box.cls[0])
            score = float(box.conf[0])
            x1, y1, x2, y2 = [float(v) for v in box.xyxy[0]]
            objs.append({"label": names.get(cls, "?"), "score": round(score, 3),
                         "box": [round(x1), round(y1), round(x2), round(y2)]})
    return objs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--region", nargs=4, type=int, metavar=("X", "Y", "W", "H"))
    ap.add_argument("--file")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--conf", type=float, default=0.35)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    try:
        if a.file:
            from PIL import Image
            img = Image.open(a.file)
        elif a.region:
            img = grab_screen(a.region)
        else:
            img = grab_screen()
        model = load_model(a.model)
        objs = detect(img, model, a.conf)
        if a.json:
            print(json.dumps({"ok": True, "count": len(objs), "objects": objs},
                             ensure_ascii=False))
        else:
            if not objs:
                print("没检测到物体")
            for o in objs:
                print("%s（%.0f%%）" % (o["label"], o["score"] * 100))
        return 0
    except Exception as e:
        if a.json:
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        else:
            print("检测失败: %s" % e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
