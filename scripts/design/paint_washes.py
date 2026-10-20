#!/usr/bin/env python3
"""Export redesigned Wash paintings for web and Android.

Native originals live in mu/design/art/source. This exporter only crops,
resamples and encodes them; Paper and Dusk scenes have independent originals.
Ambient copies flatten a 24% painting over the theme canvas for text contrast.
Transparent spot pigments are shared between themes, like pigment tokens.
Legacy names remain available, with 3840px and Android @2x companions.
The 3840px exports are Lanczos upscales of 1672x941 originals, not native 4K.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "mu/design/art/source"
WEB_OUT = ROOT / "mu/gui/static/wash/art"
MOBILE_OUT = ROOT / "mobile/android/assets/wash"
THEMES = ("paper", "dusk")
SCENES = ("dawn", "lagoon", "meadow")
PIGMENTS = ("apricot", "lagoon", "lilac", "meadow", "rose", "sky")


def export(source: Path, target: Path, size: tuple[int, int], budget: int = 0,
           canvas: str | None = None) -> None:
    """Preserve aspect and alpha; honour legacy download budgets."""
    with Image.open(source) as original:
        image = ImageOps.fit(original, size, method=Image.Resampling.LANCZOS)
        if canvas is not None:
            image = Image.blend(Image.new("RGB", size, canvas), image.convert("RGB"), 0.24)
        for quality in (88, 82, 76, 68, 58, 45, 30, 15):
            encoded = io.BytesIO()
            image.save(encoded, "WEBP", quality=quality, method=6, exact=True)
            data = encoded.getvalue()
            if not budget or len(data) < budget:
                target.write_bytes(data)
                return
    raise ValueError(f"Cannot meet {budget}-byte budget: {target.name}")


def main() -> int:
    WEB_OUT.mkdir(parents=True, exist_ok=True)
    MOBILE_OUT.mkdir(parents=True, exist_ok=True)
    tokens = json.loads((ROOT / "mu/design/tokens.json").read_text(encoding="utf-8"))
    for scene in SCENES:
        for theme in THEMES:
            source = SOURCE / f"{scene}-{theme}.webp"
            for ambient in (False, True):
                name = f"{'ambient-' if ambient else ''}{scene}-{theme}"
                ratio = 10 / 16 if ambient else 9 / 16
                canvas = tokens["themes"][theme]["color"]["canvas"] if ambient else None
                widths = (960, 1920, 3840) if ambient else (800, 1600, 3840)
                for width in widths:
                    budget = (80_000 if ambient else 60_000) if width < 3840 else 0
                    export(source, WEB_OUT / f"{name}-{width}.webp",
                           (width, round(width * ratio)), budget, canvas)
                mobile_width = 960 if ambient else 800
                for scale in (1, 2):
                    suffix = "@2x" if scale == 2 else ""
                    width = mobile_width * scale
                    export(source, MOBILE_OUT / f"{name}{suffix}.webp",
                           (width, round(width * ratio)), canvas=canvas)
            reference = ROOT / f"documentation/design/reference/wash-{scene}-{theme}.webp"
            reference.write_bytes((WEB_OUT / f"{scene}-{theme}-1600.webp").read_bytes())
    for pigment in PIGMENTS:
        source = SOURCE / f"spot-{pigment}.webp"
        for theme in THEMES:
            name = f"spot-{pigment}-{theme}"
            for width in (320, 1024):
                export(source, WEB_OUT / f"{name}-{width}.webp", (width, width))
            for scale in (1, 2):
                suffix = "@2x" if scale == 2 else ""
                width = 320 * scale
                export(source, MOBILE_OUT / f"{name}{suffix}.webp", (width, width))
    print("Wash: exported 60 web assets, 48 Android assets and 6 reference paintings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
