# -*- coding: utf-8 -*-
"""
虚拟桌面助手 素材管线：把 C:\\Users\\Administrator\\Desktop\\fairy 里的
图标(PNG) 和 动图(GIF) 处理成「透明背景 + 已裁剪 + 已降采样」的 PNG 帧序列。

GIF 原图是黑底（无 alpha），直接贴到桌面上会带一块黑方块；这里用
alpha = max(R,G,B) 做黑底抠图（发光边缘自然变成半透明），
再按所有帧的并集包围盒裁剪成正方形，最后 LANCZOS 缩放到目标尺寸。
"""
import os
import sys
import json

from PIL import Image
import numpy as np
import fairy_root

_HERE = os.path.dirname(os.path.abspath(__file__))
_DESKTOP = fairy_root.ASSET_SRC
# 原图就在本项目里时直接用它；否则回退到桌面那个原始素材目录
SRC = _HERE if any(f.lower().endswith(".gif") for f in os.listdir(_HERE)) else _DESKTOP
OUT = os.path.join(_HERE, "assets")

# 三张 GIF 内容几乎一致，取帧最多/最细腻的那张做动画源
ANIM_GIF = "41a013df43470df3221b2e5477cde5f9_7764283308033030876.gif"
ICON_PNG = "0c4d72ee711e71143c3e16884532e923_6764812457818070858.png"

SIZES = (128, 96)          # 悬浮球直径（像素）
ALPHA_FLOOR = 12           # 低于此值算纯背景，直接抠掉
ALPHA_GAIN = 1.15          # 发光区域 alpha 增益
CORE_THRESHOLD = 150       # 用「亮环本体」定裁剪框（用低阈值会被光晕撑大，徽章显小）
CORE_PAD = 1.12            # 亮环外再留 12% 给光晕


def key_black(im):
    """黑底 → 透明。返回 RGBA。"""
    rgb = np.asarray(im.convert("RGB"), dtype=np.float32)
    a = rgb.max(axis=2)                       # 黑底为 0，蓝白徽章为高值
    a = np.clip((a - ALPHA_FLOOR) * ALPHA_GAIN, 0, 255)
    out = np.dstack([rgb, a]).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def union_bbox(frames, thr=24):
    """所有帧 alpha 的并集包围盒（避免逐帧裁剪导致抖动）。"""
    x0 = y0 = 10 ** 9
    x1 = y1 = -1
    for f in frames:
        a = np.asarray(f)[:, :, 3]
        ys, xs = np.where(a > thr)
        if len(xs) == 0:
            continue
        x0, y0 = min(x0, xs.min()), min(y0, ys.min())
        x1, y1 = max(x1, xs.max()), max(y1, ys.max())
    if x1 < 0:
        return None
    # 取正方形（以中心为准），保证等比缩放不变形
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    half = max(x1 - x0, y1 - y0) / 2.0 + 6      # 留 6px 余量给光晕
    return square_box(cx, cy, half)


def square_box(cx, cy, half):
    return (int(cx - half), int(cy - half), int(cx + half), int(cy + half))


def core_bbox(frames, thr=CORE_THRESHOLD, pad=CORE_PAD):
    """用亮环本体定框：徽章在画面里占比更大，观感明显更好。"""
    x0 = y0 = 10 ** 9
    x1 = y1 = -1
    for f in frames:
        a = np.asarray(f)[:, :, 3]
        ys, xs = np.where(a > thr)
        if len(xs) == 0:
            continue
        x0, y0 = min(x0, xs.min()), min(y0, ys.min())
        x1, y1 = max(x1, xs.max()), max(y1, ys.max())
    if x1 < 0:
        return union_bbox(frames)
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    return square_box(cx, cy, max(x1 - x0, y1 - y0) / 2.0 * pad)


def main():
    os.makedirs(OUT, exist_ok=True)
    src = Image.open(os.path.join(SRC, ANIM_GIF))
    n = getattr(src, "n_frames", 1)
    duration = src.info.get("duration", 40)
    print("[1/3] 读入动图 %s : %d 帧, %dms/帧 (%.2fs 一轮)"
          % (ANIM_GIF[:16], n, duration, n * duration / 1000.0))

    raw = []
    for i in range(n):
        src.seek(i)
        raw.append(key_black(src))
    print("[2/3] 黑底抠图完成，逐帧 %.0fKB → RGBA" % (len(open(os.path.join(SRC, ANIM_GIF), 'rb').read()) / n / 1024))

    box = core_bbox(raw)
    print("      裁剪框 = %s（按亮环本体 %.0f 阈值定，外留 %.0f%% 光晕）"
          % (box, CORE_THRESHOLD, (CORE_PAD - 1) * 100))

    for size in SIZES:
        sub = os.path.join(OUT, "ball_%d" % size)
        os.makedirs(sub, exist_ok=True)
        for i, f in enumerate(raw):
            f.crop(box).resize((size, size), Image.LANCZOS).save(
                os.path.join(sub, "f%03d.png" % i), optimize=True)
        with open(os.path.join(sub, "_meta.json"), "w", encoding="utf-8") as fh:
            json.dump({"size": size, "frames": n,
                       "fps": round(1000.0 / max(1, duration), 2),
                       "duration_ms": duration,
                       "src_gif": ANIM_GIF,
                       "bbox": list(box)}, fh, ensure_ascii=False, indent=1)
        print("      → %s  (%d 帧 @ %dpx, %.1f fps)" % (sub, n, size, 1000.0 / max(1, duration)))

    # 静态图标：PNG 素材本身是深蓝底（不是纯黑），改用「去掉暗部」的方式
    icon = Image.open(os.path.join(SRC, ICON_PNG)).convert("RGB")
    arr = np.asarray(icon, dtype=np.float32)
    lum = arr.max(axis=2)
    a = np.clip((lum - 26) * 1.5, 0, 255)
    ic = Image.fromarray(np.dstack([arr, a]).astype(np.uint8), "RGBA")
    ib = union_bbox([ic], thr=40)
    if ib:
        ic = ic.crop(ib)
    side = max(ic.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(ic, ((side - ic.width) // 2, (side - ic.height) // 2))
    for size in (256, 64, 32):
        canvas.resize((size, size), Image.LANCZOS).save(
            os.path.join(OUT, "icon_%d.png" % size), optimize=True)
    canvas.resize((256, 256), Image.LANCZOS).save(os.path.join(OUT, "icon.png"), optimize=True)
    print("[3/3] 静态图标 → icon.png / icon_256 / icon_64 / icon_32 (源 %s)" % (icon.size,))

    # 预览图：把第 0/30/60 帧贴到棋盘格上，肉眼验证透明是否干净
    prev = Image.new("RGB", (128 * 3 + 40, 128 + 20), (40, 40, 46))
    for k, i in enumerate((0, n // 3, 2 * n // 3)):
        fr = raw[i].crop(box).resize((128, 128), Image.LANCZOS)
        bg = Image.new("RGB", (128, 128), (255, 255, 255))
        for y in range(0, 128, 16):
            for x in range(0, 128, 16):
                if (x // 16 + y // 16) % 2:
                    bg.paste((150, 150, 158), (x, y, x + 16, y + 16))
        bg.paste(fr, (0, 0), fr)
        prev.paste(bg, (10 + k * (128 + 10), 10))
    prev.save(os.path.join(OUT, "_preview_checker.png"))
    print("      预览（棋盘格=透明区）→ assets/_preview_checker.png")


if __name__ == "__main__":
    main()
