# -*- coding: utf-8 -*-
"""生成竞速计时器图标 app.ico（秒表 + 金色，MH 风格）。"""
from PIL import Image, ImageDraw
import math

S = 1024  # 超采样画布


def main():
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 背景圆角方块（深色渐变）
    for i in range(S):
        t = i / S
        d.line([(0, i), (S, i)], fill=(int(38 + (24 - 38) * t), int(44 + (28 - 44) * t), int(56 + (34 - 56) * t), 255))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S, S], radius=200, fill=255)
    img.putalpha(mask)

    d = ImageDraw.Draw(img)
    # 秒表表冠
    d.rounded_rectangle([440, 170, 584, 330], radius=40, fill=(224, 160, 60, 255))
    d.rectangle([476, 200, 548, 330], fill=(168, 116, 44, 255))

    # 表壳金圈
    d.ellipse([152, 300, 872, 1020], fill=(232, 163, 61, 255))
    d.ellipse([184, 332, 840, 988], fill=(250, 244, 226, 255))

    # 刻度
    cx, cy = 512, 660
    for k in range(60):
        a = math.radians(k * 6 - 90)
        outer = 300 if k % 5 == 0 else 312
        inner = 258 if k % 5 == 0 else 278
        d.line([(cx + inner * math.cos(a), cy + inner * math.sin(a)),
                (cx + outer * math.cos(a), cy + outer * math.sin(a))],
               fill=(40, 46, 58, 255), width=14 if k % 5 == 0 else 6)

    # 指针（大秒针 + 小分针）
    a_sec = math.radians(7.5 * 6 - 90)
    a_min = math.radians(10 * 6 - 90)
    d.line([(cx, cy), (cx + 210 * math.cos(a_min), cy + 210 * math.sin(a_min))], fill=(52, 60, 74, 255), width=34)
    d.line([(cx, cy), (cx + 250 * math.cos(a_sec), cy + 250 * math.sin(a_sec))], fill=(226, 74, 56, 255), width=22)
    d.ellipse([cx - 42, cy - 42, cx + 42, cy + 42], fill=(226, 74, 56, 255))
    d.ellipse([cx - 18, cy - 18, cx + 18, cy + 18], fill=(250, 244, 226, 255))

    img = img.resize((256, 256), Image.LANCZOS)
    img.save("app.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("app.ico 已生成")


if __name__ == "__main__":
    main()
