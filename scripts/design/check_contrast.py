#!/usr/bin/env python3
"""WCAG contrast checks over mu/design/tokens.json (Wash spec §3.4).

Exit status is non-zero when any pair falls below its floor. The same
checks run in tests/design/test_wash_tokens.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import re  # noqa: E402

from mu.design import THEMES, contrast_ratio, load_tokens, palettes  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
ART = ROOT / "mu/gui/static/wash/art"
_RGBA = re.compile(r"rgba\(\s*(\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\s*\)")


def _lum(rgb) -> float:
    out = []
    for channel in rgb:
        c = channel / 255
        out.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]


def _hex_rgb(value: str):
    v = value.lstrip("#")
    return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))


def glass_backdrops(theme: str) -> list[tuple[str, tuple, tuple]]:
    """(wash, darkest, lightest) composited glass-over-art colours.

    The ambient art is blurred like ``backdrop-filter`` would, the glass
    surface colour is alpha-composited over it, and the extreme pixels are
    returned: text contrast is lowest against one of them.
    """
    from PIL import Image, ImageFilter

    tokens = load_tokens()
    match = _RGBA.match(tokens["themes"][theme]["glass"]["surface"])
    gr, gg, gb, alpha = (*map(int, match.groups()[:3]), float(match.group(4)))
    out = []
    for wash in tokens["glass"]["ambient"]:
        path = ART / f"ambient-{wash}-{theme}-960.webp"
        im = Image.open(path).convert("RGB").filter(ImageFilter.GaussianBlur(12)).resize((240, 150))
        pixels = [
            (r * (1 - alpha) + gr * alpha, g * (1 - alpha) + gg * alpha, b * (1 - alpha) + gb * alpha)
            for r, g, b in im.getdata()
        ]
        pixels.sort(key=_lum)
        out.append((wash, pixels[0], pixels[-1]))
    return out


# The Wash glass leaf at full strength (patterns/shell.css: 100% − 22% opacity) over the full
# scene paintings behind the persistent shell. Blur approximates the leaf's backdrop blur at
# screen size (the painting at 400px wide, 6px Gaussian).
GLASS_LEAF_ALPHA = 0.78          # blurred (Blur ≥ 60%)
GLASS_LEAF_ALPHA_SHARP = 0.88    # no blur: the painting behind text is sharp


def scene_backdrops(theme: str, alpha: float = GLASS_LEAF_ALPHA, *, sharp: bool = False) -> list[tuple[str, tuple, tuple]]:
    """(scene, darkest, lightest) for the surface at ``alpha`` over each scene painting: blurred
    like the leaf at 60% blur, or sharp (no blur, at 800px wide so fine detail counts)."""
    from PIL import Image, ImageFilter

    surface = _hex_rgb(load_tokens()["themes"][theme]["color"]["surface"])
    out = []
    for scene in load_tokens()["glass"]["ambient"]:
        im = Image.open(ART / f"{scene}-{theme}-1600.webp").convert("RGB")
        im = im.resize((800, 450)) if sharp else im.resize((400, 225)).filter(ImageFilter.GaussianBlur(6))
        mixed = Image.blend(im, Image.new("RGB", im.size, surface), alpha)
        # Extremes by luma, then exact luminance over the darkest and lightest few.
        luma = list(mixed.convert("L").getdata())
        order = sorted(range(len(luma)), key=luma.__getitem__)
        rgb = list(mixed.getdata())
        dark = min((rgb[i] for i in order[:64]), key=_lum)
        light = max((rgb[i] for i in order[-64:]), key=_lum)
        out.append((scene, dark, light))
    return out


def _ratio(fg_rgb, bg_rgb) -> float:
    a, b = sorted((_lum(fg_rgb), _lum(bg_rgb)), reverse=True)
    return (a + 0.05) / (b + 0.05)

TEXT_FLOOR = 4.5
UI_FLOOR = 3.0
TEXT_TOKENS = ("ink", "ink-2", "ink-3", "plum")
SURFACES = ("canvas", "surface", "sunken", "raised")


def failures() -> list[tuple[str, str, str, float, float]]:
    tokens = load_tokens()
    out = []
    for theme in THEMES:
        colors = tokens["themes"][theme]["color"]
        pigments = tokens["themes"][theme]["pigment"]
        fgs = {name: colors[name] for name in TEXT_TOKENS}
        fgs.update({f"{name}.ink": spec["ink"] for name, spec in pigments.items()})
        for fg_name, fg in fgs.items():
            for bg_name in SURFACES:
                ratio = contrast_ratio(fg, colors[bg_name])
                if ratio < TEXT_FLOOR:
                    out.append((theme, fg_name, bg_name, ratio, TEXT_FLOOR))
        for name, spec in pigments.items():
            ratio = contrast_ratio(spec["ink"], spec["wash"])
            if ratio < TEXT_FLOOR:
                out.append((theme, f"{name}.ink", f"{name}.wash", ratio, TEXT_FLOOR))
        ratio = contrast_ratio(colors["on-ink"], colors["ink"])
        if ratio < TEXT_FLOOR:
            out.append((theme, "on-ink", "ink", ratio, TEXT_FLOOR))
        # Form-control boundaries (inputs, checkboxes, switch tracks) are
        # WCAG 1.4.11 UI components; hairlines are decorative and exempt.
        for bg_name in ("surface", "sunken"):
            ratio = contrast_ratio(colors["control-border"], colors[bg_name])
            if ratio < UI_FLOOR:
                out.append((theme, "control-border", bg_name, ratio, UI_FLOOR))
        # Glass: text sits on translucent surfaces over the ambient art.
        for wash, dark, light in glass_backdrops(theme):
            for fg_name, fg in fgs.items():
                ratio = min(_ratio(_hex_rgb(fg), dark), _ratio(_hex_rgb(fg), light))
                if ratio < TEXT_FLOOR:
                    out.append((theme, fg_name, f"glass/{wash}", ratio, TEXT_FLOOR))
        # Glass leaf at full strength over the scene paintings (the persistent shell).
        for label, backdrops in (("leaf", scene_backdrops(theme)),
                                 ("leaf-sharp", scene_backdrops(theme, GLASS_LEAF_ALPHA_SHARP, sharp=True))):
            for scene, dark, light in backdrops:
                for fg_name, fg in fgs.items():
                    ratio = min(_ratio(_hex_rgb(fg), dark), _ratio(_hex_rgb(fg), light))
                    if ratio < TEXT_FLOOR:
                        out.append((theme, fg_name, f"{label}/{scene}", ratio, TEXT_FLOOR))
        accent = pigments[tokens["semantic"]["accent"]]["ink"]
        ratio = contrast_ratio(accent, colors["surface"])
        if ratio < UI_FLOOR:
            out.append((theme, "accent focus ring", "surface", ratio, UI_FLOOR))
    return out


def _flat_failures(label: str, colors: dict, pigments: dict, accent_name: str) -> list:
    """The text / wash / control / on-ink / focus floors for one flat colour set."""
    out = []
    fgs = {name: colors[name] for name in TEXT_TOKENS}
    fgs.update({f"{name}.ink": spec["ink"] for name, spec in pigments.items()})
    for fg_name, fg in fgs.items():
        for bg_name in SURFACES:
            ratio = contrast_ratio(fg, colors[bg_name])
            if ratio < TEXT_FLOOR:
                out.append((label, fg_name, bg_name, ratio, TEXT_FLOOR))
    for name, spec in pigments.items():
        ratio = contrast_ratio(spec["ink"], spec["wash"])
        if ratio < TEXT_FLOOR:
            out.append((label, f"{name}.ink", f"{name}.wash", ratio, TEXT_FLOOR))
    ratio = contrast_ratio(colors["on-ink"], colors["ink"])
    if ratio < TEXT_FLOOR:
        out.append((label, "on-ink", "ink", ratio, TEXT_FLOOR))
    for bg_name in ("surface", "sunken"):
        ratio = contrast_ratio(colors["control-border"], colors[bg_name])
        if ratio < UI_FLOOR:
            out.append((label, "control-border", bg_name, ratio, UI_FLOOR))
    ratio = contrast_ratio(pigments[accent_name]["ink"], colors["surface"])
    if ratio < UI_FLOOR:
        out.append((label, "accent focus ring", "surface", ratio, UI_FLOOR))
    return out


def palette_failures() -> list:
    """Every alternative palette (flat surfaces: no glass), light and dark."""
    accent = load_tokens()["semantic"]["accent"]
    out = []
    for name, spec in palettes().items():
        for mode in ("light", "dark"):
            out += _flat_failures(f"{name}/{mode}", spec[mode]["color"], spec[mode]["pigment"], accent)
    return out


def main() -> int:
    bad = failures() + palette_failures()
    if not bad:
        print("wash contrast: all pairs pass")
        return 0
    print(f"{'theme':6} {'foreground':22} {'background':16} ratio  floor")
    for theme, fg, bg, ratio, floor in bad:
        print(f"{theme:14} {fg:22} {bg:16} {ratio:5.2f}  {floor}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
