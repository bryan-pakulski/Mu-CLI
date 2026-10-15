#!/usr/bin/env python3
"""Paint the Wash watercolour art from mu/design/art/*.json (spec §3.5).

    python3 scripts/design/paint_washes.py            # all washes, both themes

Deterministic: each wash has a fixed seed. Dev-only dependencies: numpy +
Pillow (requirements-dev.txt). Outputs are committed:

* mu/gui/static/wash/art/<wash>-<theme>-{1600,800}.webp  (landscapes)
* mu/gui/static/wash/art/<spot>-<theme>-320.webp          (spot washes)
* mu/gui/static/wash/art/ambient-<wash>-<theme>-{1920,960}.webp (glass backdrops)
* mobile/android/assets/wash/<wash>-<theme>.webp          (800w / 320w)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from mu.design import THEMES, hex_to_rgb, load_tokens  # noqa: E402

ART_DIR = ROOT / "mu/design/art"
WEB_OUT = ROOT / "mu/gui/static/wash/art"
MOBILE_OUT = ROOT / "mobile/android/assets/wash"


def paint(spec: dict, theme: str, width: int) -> Image.Image:
    tokens = load_tokens()
    rng = np.random.default_rng(int(spec["seed"]))
    aspect = float(spec.get("aspect", 9 / 16))
    W, H = width, int(round(width * aspect))
    k = W / 1600
    ky = H / (1600 * aspect) if aspect != 9 / 16 else k
    yy, xx = np.mgrid[0:H, 0:W]

    def noise(scale: float, octaves: int = 5) -> np.ndarray:
        out, amp, tot = np.zeros((H, W)), 1.0, 0.0
        for octave in range(octaves):
            s = max(2, int(scale * 2 ** octave))
            grid = rng.random((s, max(2, int(s * W / H) + 1)))
            im = Image.fromarray((grid * 255).astype(np.uint8)).resize((W, H), Image.BICUBIC)
            out += amp * np.asarray(im) / 255
            tot += amp
            amp *= 0.5
        return out / tot

    base = np.array(hex_to_rgb(tokens["themes"][theme]["color"]["canvas"]), float) / 255
    pigments = tokens["themes"][theme]["pigment"]
    img = np.ones((H, W, 3)) * base
    for (cx, cy, rx, ry), name, strength in spec["blobs"]:
        d = np.sqrt(((xx - cx * k) / (rx * k)) ** 2 + ((yy - cy * ky) / (ry * ky)) ** 2)
        d = d + (noise(3) - 0.5) * 0.9                 # ragged bleed edge
        m = np.clip(1 - d, 0, 1) ** 0.6
        edge = np.exp(-((d - 0.97) / 0.05) ** 2) * 0.35  # pigment pooling ring
        m = np.clip(m * (0.75 + 0.5 * noise(12, 3)) + edge * (d < 1.05), 0, 1) * strength
        colour = np.array(hex_to_rgb(pigments[name]["pigment"]), float) / 255
        if theme == "paper":
            img = img * (1 - m[..., None] * (1 - colour))   # multiply glaze
        else:
            img = img + m[..., None] * 0.55 * (colour - img)  # luminous glaze
    img = np.clip(img + ((noise(120, 2) - 0.5) * 0.05)[..., None], 0, 1)  # paper grain
    if spec.get("fade", True):
        fade = np.clip((yy / H - 0.55) / 0.45, 0, 1)[..., None]
        img = img * (1 - fade) + base * fade
    out = Image.fromarray((img * 255).astype(np.uint8))
    return out.filter(ImageFilter.GaussianBlur(1.2 * k))


def main() -> int:
    WEB_OUT.mkdir(parents=True, exist_ok=True)
    MOBILE_OUT.mkdir(parents=True, exist_ok=True)
    for path in sorted(ART_DIR.glob("*.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        name = path.stem
        spot = name.startswith("spot-")
        ambient = name.startswith("ambient-")
        for theme in THEMES:
            widths = (320,) if spot else (1920, 960) if ambient else (1600, 800)
            for width in widths:
                target = WEB_OUT / f"{name}-{theme}-{width}.webp"
                paint(spec, theme, width).save(target, quality=82, method=6)
                print(f"{target.relative_to(ROOT)}  {target.stat().st_size // 1024} KB")
            mobile = MOBILE_OUT / f"{name}-{theme}.webp"
            paint(spec, theme, 320 if spot else 960 if ambient else 800).save(mobile, quality=80, method=6)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
