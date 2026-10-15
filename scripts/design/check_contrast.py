#!/usr/bin/env python3
"""WCAG contrast checks over mu/design/tokens.json (Wash spec §3.4).

Exit status is non-zero when any pair falls below its floor. The same
checks run in tests/test_wash_tokens.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mu.design import THEMES, contrast_ratio, load_tokens  # noqa: E402

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
        accent = pigments[tokens["semantic"]["accent"]]["ink"]
        ratio = contrast_ratio(accent, colors["surface"])
        if ratio < UI_FLOOR:
            out.append((theme, "accent focus ring", "surface", ratio, UI_FLOOR))
    return out


def main() -> int:
    bad = failures()
    if not bad:
        print("wash contrast: all pairs pass")
        return 0
    print(f"{'theme':6} {'foreground':22} {'background':16} ratio  floor")
    for theme, fg, bg, ratio, floor in bad:
        print(f"{theme:6} {fg:22} {bg:16} {ratio:5.2f}  {floor}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
