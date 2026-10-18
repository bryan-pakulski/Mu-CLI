"""Prototype watercolour wash generator for the Wash design system.

Dev-only reference for scripts/design/paint_washes.py (see
wash_implementation_spec.md §3.5). Requires numpy + Pillow.

    python3 paint_wash_prototype.py OUT.webp --wash dawn|lagoon|meadow --theme paper|dusk [--seed N]
"""
import argparse

import numpy as np
from PIL import Image, ImageFilter

PIGMENT = {
    "apricot": (242, 163, 107), "rose": (229, 138, 158), "lilac": (179, 157, 219),
    "lagoon": (95, 163, 171), "sun": (242, 201, 76), "sky": (132, 174, 227),
    "meadow": (141, 191, 106), "plum": (156, 107, 152),
}
CANVAS = {"paper": (248, 246, 241), "dusk": (22, 19, 27)}
# (cx, cy, rx, ry) in a 1600x900 frame, pigment, strength
WASHES = {
    "dawn": [((300, 180, 620, 300), "apricot", .55), ((1200, 120, 640, 260), "rose", .45),
             ((800, 420, 700, 220), "lilac", .40), ((1350, 620, 520, 260), "lagoon", .45),
             ((250, 720, 600, 240), "meadow", .40), ((900, 60, 400, 160), "sun", .45)],
    "lagoon": [((400, 300, 700, 380), "sky", .50), ((1250, 500, 620, 340), "lagoon", .50),
               ((800, 800, 800, 200), "lilac", .35)],
    "meadow": [((350, 650, 700, 260), "meadow", .50), ((1200, 250, 600, 300), "sun", .40),
               ((900, 700, 700, 220), "lagoon", .40)],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--wash", default="dawn", choices=sorted(WASHES))
    ap.add_argument("--theme", default="paper", choices=sorted(CANVAS))
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--width", type=int, default=1600)
    a = ap.parse_args()
    rng = np.random.default_rng(a.seed)
    W, H = a.width, int(a.width * 9 / 16)
    k = W / 1600
    yy, xx = np.mgrid[0:H, 0:W]

    def noise(scale, octaves=5):
        out, amp, tot = np.zeros((H, W)), 1.0, 0.0
        for o in range(octaves):
            s = max(2, int(scale * 2 ** o))
            g = rng.random((s, int(s * W / H) + 1))
            im = Image.fromarray((g * 255).astype(np.uint8)).resize((W, H), Image.BICUBIC)
            out += amp * np.asarray(im) / 255
            tot += amp
            amp *= .5
        return out / tot

    base = np.array(CANVAS[a.theme], float) / 255
    img = np.ones((H, W, 3)) * base
    for (cx, cy, rx, ry), name, strength in WASHES[a.wash]:
        d = np.sqrt(((xx - cx * k) / (rx * k)) ** 2 + ((yy - cy * k) / (ry * k)) ** 2)
        d = d + (noise(3) - .5) * .9                       # ragged bleed edge
        m = np.clip(1 - d, 0, 1) ** .6
        edge = np.exp(-((d - .97) / .05) ** 2) * .35        # pigment pooling ring
        m = np.clip(m * (.75 + .5 * noise(12, 3)) + edge * (d < 1.05), 0, 1) * strength
        c = np.array(PIGMENT[name], float) / 255
        if a.theme == "paper":
            img = img * (1 - m[..., None] * (1 - c))       # multiply glaze
        else:
            img = img + m[..., None] * .55 * (c - img)     # luminous glaze on dark ground
    img = np.clip(img + ((noise(120, 2) - .5) * .05)[..., None], 0, 1)   # paper grain
    fade = np.clip((yy / H - .55) / .45, 0, 1)[..., None]                # dissolve into canvas
    img = img * (1 - fade) + base * fade
    Image.fromarray((img * 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(1.2 * k)).save(a.out, quality=82)


if __name__ == "__main__":
    main()
