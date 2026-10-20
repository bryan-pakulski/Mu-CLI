"""Wash CSS rules that keep the design system intact (AGENTS.md: colours, type and spacing come
only from mu/design/tokens.json): no colour literal outside the generated tokens.css, and every
--wash-* custom property a stylesheet uses is one the generator emits."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WASH = ROOT / "mu" / "gui" / "static" / "css" / "wash"
TOKENS = WASH / "tokens.css"


def _strip_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def _wash_files() -> list[Path]:
    return sorted(p for p in WASH.rglob("*.css") if p != TOKENS)


def _rel(path: Path) -> str:
    return str(path.relative_to(WASH))


def test_no_colour_literals_outside_generated_tokens():
    offenders = []
    for path in _wash_files():
        text = _strip_comments(path.read_text(encoding="utf-8"))
        for match in re.finditer(r"#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(", text):
            offenders.append(f"{_rel(path)}: {match.group(0)}")
    assert not offenders, offenders


def test_every_wash_custom_property_used_exists_in_tokens():
    defined = set(re.findall(r"(--wash-[\w-]+)\s*:", TOKENS.read_text(encoding="utf-8")))
    missing = []
    for path in _wash_files():
        for name in re.findall(r"var\(\s*(--wash-[\w-]+)", path.read_text(encoding="utf-8")):
            if name not in defined:
                missing.append(f"{_rel(path)}: {name}")
    assert not missing, missing
