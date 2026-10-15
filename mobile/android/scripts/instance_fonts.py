#!/usr/bin/env python3
"""Instance the bundled variable fonts into static per-weight TTFs.

Android cannot select weights inside a custom font family loaded through
expo-font (it synthesises bold from the regular face), so the app registers
one family per weight (`fontFamilies` in src/theme/tokens.ts):

* Inter 400/500/600 (opsz 14) -> Inter-Regular/Medium/SemiBold.ttf
* JetBrains Mono 400/600       -> JetBrainsMono-Regular/SemiBold.ttf

    python3 mobile/android/scripts/instance_fonts.py

Requires fontTools. The variable sources stay in assets/fonts (OFL 1.1) and
are not bundled, because nothing requires them.
"""

from __future__ import annotations

from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

FONTS = Path(__file__).resolve().parents[1] / "assets" / "fonts"

JOBS = [
    ("InterVariable.ttf", {"opsz": 14}, "Inter",
     [(400, "Inter-Regular", "Regular"), (500, "Inter-Medium", "Medium"), (600, "Inter-SemiBold", "SemiBold")]),
    ("JetBrainsMono-Variable.ttf", {}, "JetBrains Mono",
     [(400, "JetBrainsMono-Regular", "Regular"), (600, "JetBrainsMono-SemiBold", "SemiBold")]),
]


def main() -> None:
    for source, fixed, family, weights in JOBS:
        for wght, out, style in weights:
            font = instancer.instantiateVariableFont(TTFont(FONTS / source), {**fixed, "wght": wght})
            name = font["name"]
            full = f"{family} {style}"
            for nid in (16, 17, 25):  # typographic family/subfamily, variations prefix
                name.removeNames(nameID=nid)
            # Each weight is its own single-face family.
            name.setName(family if style == "Regular" else full, 1, 3, 1, 0x409)
            name.setName("Regular", 2, 3, 1, 0x409)
            name.setName(full, 4, 3, 1, 0x409)
            name.setName(out, 6, 3, 1, 0x409)
            font["OS/2"].usWeightClass = wght
            font["OS/2"].fsSelection = (font["OS/2"].fsSelection & ~0b0100001) | 0b1000000  # REGULAR
            font["head"].macStyle = 0
            font.save(FONTS / f"{out}.ttf")
            print(f"wrote {out}.ttf")


if __name__ == "__main__":
    main()
