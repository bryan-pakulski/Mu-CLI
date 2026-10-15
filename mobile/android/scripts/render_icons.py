"""Render the Wash drop mark as the Android icon, adaptive icon, splash and favicon.

Run from the repository root: python3 mobile/android/scripts/render_icons.py (needs Pillow).
Deterministic (fixed seed). Colours are the Paper canvas and apricot/rose/lilac pigments.
"""
import math
import random
from PIL import Image, ImageDraw, ImageFilter

PAPER = (0xF8, 0xF6, 0xF1, 255)
APRICOT = (0xF2, 0xA3, 0x6B)
ROSE = (0xE5, 0x8A, 0x9E)
LILAC = (0xB3, 0x9D, 0xDB)
PLUM = (0x2B, 0x14, 0x36)


def drop_polygon(cx, cy, scale):
    # Drop: point at the top, round bottom (matches the 24-grid "drop" icon:
    # tip at y=3.5, circle r=6 centred at y=13.7).
    pts = []
    r = 6 * scale
    ccy = cy + 2.0 * scale
    tip = (cx, cy - 8.2 * scale)
    for i in range(0, 361, 3):
        a = math.radians(i)
        x = cx + r * math.sin(a)
        y = ccy + r * math.cos(a)
        pts.append((x, y))
    # replace the top arc with straight-ish tangents to the tip
    out = [p for p in pts if p[1] > ccy - r * 0.55]
    out.sort(key=lambda p: math.atan2(p[0] - cx, p[1] - ccy))
    return [tip] + out


def render(size, with_paper, scale_frac, seed=7):
    random.seed(seed)
    img = Image.new('RGBA', (size, size), PAPER if with_paper else (0, 0, 0, 0))
    scale = size * scale_frac / 24
    cx, cy = size / 2, size / 2 - scale * 0.6
    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).polygon(drop_polygon(cx, cy, scale), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(size / 400))
    # vertical pigment wash apricot -> rose -> lilac with granulation noise
    paint = Image.new('RGBA', (size, size))
    px = paint.load()
    top, bottom = cy - 8.2 * scale, cy + 8 * scale
    for y in range(size):
        t = min(1, max(0, (y - top) / (bottom - top)))
        c1, c2, u = (APRICOT, ROSE, t / 0.6) if t < 0.6 else (ROSE, LILAC, (t - 0.6) / 0.4)
        base = [int(c1[k] + (c2[k] - c1[k]) * u) for k in range(3)]
        for x in range(size):
            n = random.randint(-6, 6)
            px[x, y] = (max(0, min(255, base[0] + n)), max(0, min(255, base[1] + n)), max(0, min(255, base[2] + n)), 255)
    # pigment pooling: a darker rim
    rim = mask.filter(ImageFilter.FIND_EDGES).filter(ImageFilter.GaussianBlur(size / 160))
    shade = Image.new('RGBA', (size, size), PLUM + (0,))
    shade.putalpha(rim.point(lambda v: int(v * 0.35)))
    paint = Image.alpha_composite(paint, shade)
    img.paste(paint, (0, 0), mask)
    return img


render(1024, True, 0.62).save('mobile/android/assets/icon.png')
render(1024, False, 0.42).save('mobile/android/assets/adaptive-icon.png')
render(1024, False, 0.30).save('mobile/android/assets/splash.png')
render(48, True, 0.62).resize((48, 48)).save('mobile/android/assets/favicon.png')
