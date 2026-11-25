#!/usr/bin/env python3
"""Make plain placeholder LiveArea images in assets/livearea (icon0.png 128x128, bg.png 840x500, startup.png 280x158).

They are original, abstract images (a gradient, a few lines and the title in a plain font), so the repository
ships no game art. To use your own, drop 8-bit indexed PNGs with the same names and sizes into assets/livearea;
this script never overwrites existing images.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "assets" / "livearea"
TOP, BOTTOM = (24, 18, 48), (70, 22, 74)
ACCENT, TEXT = (64, 224, 200), (236, 228, 240)


def gradient(width, height):
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        t = y / max(1, height - 1)
        draw.line([(0, y), (width, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)))
    return image


def diamond(draw, cx, cy, radius, width):
    points = [(cx, cy - radius), (cx + radius, cy), (cx, cy + radius), (cx - radius, cy), (cx, cy - radius)]
    draw.line(points, fill=ACCENT, width=width)


def font(size):
    for name in ("DejaVuSans-Bold.ttf", "Arial Bold.ttf", "Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def save(image, name):
    path = OUT / name
    if path.exists():
        print(f"keeping your {path.relative_to(OUT.parents[1])}")
        return
    image.convert("P", palette=Image.Palette.ADAPTIVE, colors=256).save(path, optimize=True)
    print(f"wrote {path.relative_to(OUT.parents[1])}")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    icon = gradient(128, 128)
    draw = ImageDraw.Draw(icon)
    diamond(draw, 64, 60, 34, 5)
    diamond(draw, 64, 60, 14, 3)
    draw.text((64, 112), "HLD", font=font(16), fill=TEXT, anchor="mm")
    save(icon, "icon0.png")

    background = gradient(840, 500)
    draw = ImageDraw.Draw(background)
    for i, radius in enumerate((150, 110, 70)):
        diamond(draw, 640, 250, radius, 6 - i * 2)
    draw.text((60, 210), "Hyper Light Drifter", font=font(44), fill=TEXT)
    draw.text((62, 270), "PS Vita port (unofficial)", font=font(22), fill=ACCENT)
    save(background, "bg.png")

    startup = gradient(280, 158)
    draw = ImageDraw.Draw(startup)
    diamond(draw, 140, 64, 34, 4)
    draw.text((140, 132), "START", font=font(20), fill=TEXT, anchor="mm")
    save(startup, "startup.png")


if __name__ == "__main__":
    main()
