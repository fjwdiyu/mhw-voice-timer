# -*- coding: utf-8 -*-
"""生成新图标 app.ico —— 深色渐变 + 玻璃高光 + 金色秒表（与界面风格一致）。"""
import math

from PIL import Image, ImageDraw

S = 1024          # 超采样画布
SUP = 2048        # 高光等细节用更大画布再缩


def lerp(c1, c2, t):
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def lerp3(c1, c2, c3, t):
    return lerp(c1, c2, t * 2) if t < 0.5 else lerp(c2, c3, (t - 0.5) * 2)


def main():
    # 1) 对角渐变底（与界面同色系）
    g = Image.new("RGB", (160, 160))
    px = g.load()
    for y in range(160):
        for x in range(160):
            px[x, y] = lerp3((8, 11, 22), (34, 22, 72), (10, 30, 56), (x + y) / 318)
    bg = g.resize((S, S), Image.BICUBIC).convert("RGBA")

    # 2) 玻璃高光
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([-S * 0.35, -S * 0.55, S * 0.95, S * 0.52], fill=(255, 255, 255, 30))
    gd.ellipse([S * 0.55, S * 0.30, S * 1.35, S * 1.05], fill=(109, 139, 255, 34))
    gd.ellipse([-S * 0.10, S * 0.62, S * 0.75, S * 1.25], fill=(232, 163, 61, 20))
    bg = Image.alpha_composite(bg, glow)

    # 3) 圆角遮罩
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=232, fill=255)
    bg.putalpha(mask)

    # 4) 秒表（画在独立层，再合成，保证半透明玻璃面能透出底）
    sw = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(sw)
    cx, cy = 512, 588
    R = 286
    RING = 46
    gold = (232, 163, 61, 255)
    gold_hi = (246, 199, 122, 255)

    # 表冠
    d.rounded_rectangle([cx - 74, cy - R - 128, cx + 74, cy - R + 46], radius=44, fill=gold)
    d.rounded_rectangle([cx - 34, cy - R - 104, cx + 34, cy - R + 40], radius=24, fill=(176, 122, 44, 255))

    # 外圈（金色渐变环：先铺暗金，再叠亮金弧）
    d.ellipse([cx - R, cy - R, cx + R, cy + R], fill=(198, 136, 46, 255))
    d.arc([cx - R, cy - R, cx + R, cy + R], start=150, end=330, fill=gold_hi, width=30)
    # 玻璃内面（半透明深色，透出底色渐变）
    r2 = R - RING
    d.ellipse([cx - r2, cy - r2, cx + r2, cy + r2], fill=(13, 19, 34, 220))
    # 内面高光弧
    d.arc([cx - r2 + 10, cy - r2 + 10, cx + r2 - 10, cy + r2 - 10],
          start=200, end=320, fill=(255, 255, 255, 40), width=16)

    # 刻度
    for k in range(60):
        a = math.radians(k * 6 - 90)
        big = (k % 5 == 0)
        outer = r2 - 12
        inner = r2 - (52 if big else 32)
        col = (232, 163, 61, 210) if big else (150, 160, 180, 150)
        d.line([cx + inner * math.cos(a), cy + inner * math.sin(a),
                cx + outer * math.cos(a), cy + outer * math.sin(a)],
               fill=col, width=14 if big else 6)

    # 指针（分针指向右上、秒针指向右下，避免重叠）
    a_sec = math.radians(27.5 * 6 - 90)
    a_min = math.radians(10 * 6 - 90)
    d.line([cx, cy, cx + (r2 - 92) * math.cos(a_min), cy + (r2 - 92) * math.sin(a_min)],
           fill=(226, 232, 245, 255), width=26)
    d.line([cx, cy, cx + (r2 - 52) * math.cos(a_sec), cy + (r2 - 52) * math.sin(a_sec)],
           fill=(242, 84, 91, 255), width=18)
    d.ellipse([cx - 34, cy - 34, cx + 34, cy + 34], fill=gold)
    d.ellipse([cx - 14, cy - 14, cx + 14, cy + 14], fill=(16, 22, 38, 255))

    bg = Image.alpha_composite(bg, sw)

    # 5) 描边
    d2 = ImageDraw.Draw(bg)
    d2.rounded_rectangle([3, 3, S - 4, S - 4], radius=230, outline=(255, 255, 255, 46), width=5)

    # 6) 输出多尺寸 ico
    img = bg.convert("RGBA").resize((256, 256), Image.LANCZOS)
    img.save("app.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("app.ico 已生成（深色渐变 + 金色秒表）")


if __name__ == "__main__":
    main()
