"""App icon drawn with Pillow (no binary files in the project)."""
from __future__ import annotations

from PIL import Image, ImageDraw

DEEP = (4, 38, 35, 255)
GOLD = (185, 167, 121, 255)
GREEN = (0, 128, 0, 255)


def make_icon(size: int = 256) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    u = size / 64
    d.rounded_rectangle((2 * u, 2 * u, 62 * u, 62 * u), radius=14 * u, fill=DEEP)
    w = max(2, int(4 * u))
    # scan-frame corners
    for (x, y, dx, dy) in ((14, 14, 1, 1), (50, 14, -1, 1), (14, 50, 1, -1), (50, 50, -1, -1)):
        d.line((x * u, y * u, (x + 9 * dx) * u, y * u), fill=GOLD, width=w)
        d.line((x * u, y * u, x * u, (y + 9 * dy) * u), fill=GOLD, width=w)
    d.rounded_rectangle((12 * u, 30 * u, 52 * u, 34 * u), radius=2 * u, fill=GREEN)
    return img
