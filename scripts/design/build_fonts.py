#!/usr/bin/env python3
"""Subset the vendored OFL fonts for web (woff2) and Android (ttf).

    python3 scripts/design/build_fonts.py --inter DIR --jetbrains DIR

DIR is an unpacked upstream release (Inter 4.x: InterVariable*.ttf;
JetBrains Mono 2.x: fonts/variable/JetBrainsMono*[wght].ttf). Needs
fonttools + brotli (dev-only; see requirements-dev.txt).
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from fontTools import subset

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "mu/gui/static/fonts"
MOBILE = ROOT / "mobile/android/assets/fonts"

# Latin, Latin-1, Latin Extended-A, Greek (μ), punctuation, arrows, the
# Wash status glyphs, box drawing (terminal panels) and common symbols.
UNICODES = (
    "U+0000-00FF,U+0100-017F,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,"
    "U+0391-03C9,U+2000-206F,U+20AC,U+2122,U+2190-21FF,U+2212,U+2318,U+2325,U+2387,U+238B,"
    "U+23CE,U+2500-257F,U+2588-258F,U+25A0-25FF,U+2713-2717,U+FEFF,U+FFFD"
)


def _subset(src: Path, dest: Path, flavor: str | None) -> None:
    options = subset.Options()
    options.flavor = flavor
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.notdef_outline = True
    options.drop_tables += ["DSIG"]
    font = subset.load_font(str(src), options)
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=subset.parse_unicodes(UNICODES))
    subsetter.subset(font)
    dest.parent.mkdir(parents=True, exist_ok=True)
    subset.save_font(font, str(dest), options)
    print(f"{dest.relative_to(ROOT)}  {dest.stat().st_size // 1024} KB")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inter", type=Path, required=True)
    parser.add_argument("--jetbrains", type=Path, required=True)
    args = parser.parse_args()
    jobs = [
        (args.inter / "InterVariable.ttf", "InterVariable"),
        (args.inter / "InterVariable-Italic.ttf", "InterVariable-Italic"),
        (args.jetbrains / "fonts/variable/JetBrainsMono[wght].ttf", "JetBrainsMono-Variable"),
    ]
    for src, name in jobs:
        _subset(src, WEB / f"{name}.woff2", "woff2")
    # Android: static-friendly variable TTFs (expo-font loads them by family).
    _subset(args.inter / "InterVariable.ttf", MOBILE / "InterVariable.ttf", None)
    _subset(args.jetbrains / "fonts/variable/JetBrainsMono[wght].ttf", MOBILE / "JetBrainsMono-Variable.ttf", None)
    for licence_src, licence_name in (
        (args.inter / "LICENSE.txt", "Inter-OFL.txt"),
        (args.jetbrains / "OFL.txt", "JetBrainsMono-OFL.txt"),
    ):
        for target in (WEB, MOBILE):
            shutil.copyfile(licence_src, target / licence_name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
