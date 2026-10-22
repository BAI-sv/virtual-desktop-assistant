# -*- coding: utf-8 -*-
"""fairy_detect_onnx.py —— YOLO 的 ONNX + DirectML 推理（替代 torch+DirectML）。

★★ 2026-10-18【用户要求"按你说的做"】为什么要换：

  原来 fairy_detect.py 走 `ultralytics + torch_directml`，实测有两个问题：
    1) `torch + DirectML` 直接报错：
         RuntimeError: Cannot set version_counter for inference tensor
    2) ultralytics 8.4 会**自动 pip install onnxruntime（CPU 版）**，
       把已经装好的 onnxruntime-directml 覆盖掉 —— 它甚至会在推理前打印
         "Using ONNX Runtime 1.31.0 with CPUExecutionProvider"

  换成纯 onnxruntime 推理（不经过 ultralytics）后实测：
        ONNX + DirectML   3.0 毫秒/次   336 FPS
        ONNX + CPU       23.3 毫秒/次    43 FPS
  快了约 7.8 倍，而且不会被任何东西覆盖。

  用法（跑在 funasr 环境的 python 里）：
      "D:/FAIRY/envs/funasr/Scripts/python.exe" fairy_detect_onnx.py --file x.png --json
"""
import argparse
import json
import os

IMGSZ = 640
MODEL_DIR = r"D:\FAIRY\models"
DEFAULT_ONNX = "yolov11n.onnx"

# COCO 80 类（YOLO 官方顺序）
COCO = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
    "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack",
    "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
    "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
    "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
    "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier",
    "toothbrush",
]


def _sess(path):
    import onnxruntime as ort
    provs = ort.get_available_providers()
    use = ["DmlExecutionProvider", "CPUExecutionProvider"] \
        if "DmlExecutionProvider" in provs else ["CPUExecutionProvider"]
    s = ort.InferenceSession(path, providers=use)
    return s, use


def _nms(boxes, scores, iou_thres):
    """纯 numpy 的 NMS（YOLO 后处理只需要这一种）。"""
    import numpy as np
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(x1[i], x1[rest])
        yy1 = np.maximum(y1[i], y1[rest])
        xx2 = np.minimum(x2[i], x2[rest])
        yy2 = np.minimum(y2[i], y2[rest])
        w = np.maximum(0.0, xx2 - xx1)
        h = np.maximum(0.0, yy2 - yy1)
        inter = w * h
        iou = inter / (areas[i] + areas[rest] - inter + 1e-9)
        order = rest[iou <= iou_thres]
    return keep


def detect(img, sess, conf=0.35, iou=0.45):
    """img: PIL.Image -> [{label, score, box}]，box 是原图坐标 [x1,y1,x2,y2]。"""
    import numpy as np
    from PIL import Image

    ow, oh = img.size
    r = min(IMGSZ / float(ow), IMGSZ / float(oh))
    nw, nh = max(1, int(round(ow * r))), max(1, int(round(oh * r)))
    resized = img.convert("RGB").resize((nw, nh), Image.BILINEAR)
    canvas = Image.new("RGB", (IMGSZ, IMGSZ), (114, 114, 114))
    dx, dy = (IMGSZ - nw) // 2, (IMGSZ - nh) // 2
    canvas.paste(resized, (dx, dy))

    arr = np.asarray(canvas, dtype=np.float32) / 255.0      # HWC
    arr = np.ascontiguousarray(arr.transpose(2, 0, 1)[None])  # 1,3,H,W

    iname = sess.get_inputs()[0].name
    out = sess.run(None, {iname: arr})[0]                   # [1,84,8400]
    pred = np.squeeze(out)                                  # 84,8400
    if pred.shape[0] < pred.shape[1]:                       # 保证是 [84,8400]
        pred = pred.T
    # 前 4 列是 cx,cy,w,h；后面是类别分数
    boxes_cxcywh = pred[:, :4]
    scores_all = pred[:, 4:]
    cls_ids = scores_all.argmax(axis=1)
    scores = scores_all.max(axis=1)

    m = scores >= conf
    if not np.any(m):
        return []
    boxes_cxcywh = boxes_cxcywh[m]
    scores = scores[m]
    cls_ids = cls_ids[m]

    cx, cy, w, h = (boxes_cxcywh[:, k] for k in range(4))
    x1 = cx - w / 2.0
    y1 = cy - h / 2.0
    x2 = cx + w / 2.0
    y2 = cy + h / 2.0
    boxes = np.stack([x1, y1, x2, y2], axis=1)

    keep = _nms(boxes, scores, iou)

    # 从 letterbox 坐标映射回原图
    objs = []
    for i in keep:
        bx1 = (boxes[i][0] - dx) / r
        by1 = (boxes[i][1] - dy) / r
        bx2 = (boxes[i][2] - dx) / r
        by2 = (boxes[i][3] - dy) / r
        bx1 = max(0.0, min(float(ow), float(bx1)))
        by1 = max(0.0, min(float(oh), float(by1)))
        bx2 = max(0.0, min(float(ow), float(bx2)))
        by2 = max(0.0, min(float(oh), float(by2)))
        ci = int(cls_ids[i])
        objs.append({
            "label": COCO[ci] if 0 <= ci < len(COCO) else str(ci),
            "score": round(float(scores[i]), 3),
            "box": [round(bx1), round(by1), round(bx2), round(by2)],
        })
    objs.sort(key=lambda o: -o["score"])
    return objs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--region", nargs=4, type=int, metavar=("X", "Y", "W", "H"))
    ap.add_argument("--file")
    ap.add_argument("--model", default=DEFAULT_ONNX)
    ap.add_argument("--conf", type=float, default=0.35)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    try:
        from PIL import Image, ImageGrab
        if a.file:
            img = Image.open(a.file)
        else:
            box = None
            if a.region:
                x, y, w, h = a.region
                box = (int(x), int(y), int(x + w), int(y + h))
            img = ImageGrab.grab(bbox=box)

        path = a.model if os.path.isabs(a.model) else os.path.join(MODEL_DIR, a.model)
        if not os.path.exists(path):
            print(json.dumps({"ok": False, "error": "ONNX 模型不存在: %s" % path},
                             ensure_ascii=False))
            return 1
        sess, provs = _sess(path)
        objs = detect(img, sess, a.conf)
        if a.json:
            print(json.dumps({"ok": True, "count": len(objs), "objects": objs,
                              "backend": "onnx+" + (provs[0] if provs else "?")},
                             ensure_ascii=False))
        else:
            if not objs:
                print("没检测到物体")
            for o in objs:
                print("%-16s %.2f  %s" % (o["label"], o["score"], o["box"]))
        return 0
    except Exception as e:
        import traceback
        if "--json" in __import__("sys").argv:
            print(json.dumps({"ok": False, "error": "%s: %s" % (type(e).__name__, e)},
                             ensure_ascii=False))
        else:
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
