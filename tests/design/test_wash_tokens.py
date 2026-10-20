"""Wash token contract: schema, contrast floors, and generated outputs in sync."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from mu.design import (
    SEMANTICS, STEPS, THEMES, contrast_ratio, load_tokens, load_vocabulary, nearest_xterm256,
    normalize_theme, semantic, state_style,
)

ROOT = Path(__file__).resolve().parents[2]
HEX = re.compile(r"^#[0-9A-F]{6}$")


def _run(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/design" / script), *args],
        capture_output=True, text=True, cwd=ROOT,
    )


def test_both_themes_define_the_same_keys():
    tokens = load_tokens()
    paper, dusk = (tokens["themes"][t] for t in THEMES)
    assert paper["color"].keys() == dusk["color"].keys()
    assert paper["pigment"].keys() == dusk["pigment"].keys()
    for theme in THEMES:
        spec = tokens["themes"][theme]
        for name, value in spec["color"].items():
            assert HEX.match(value) or value.startswith("rgba("), (theme, name, value)
        for name, steps in spec["pigment"].items():
            assert set(steps) == set(STEPS)
            assert all(HEX.match(v) for v in steps.values()), (theme, name)


def test_contrast_floors_hold():
    result = _run("check_contrast.py")
    assert result.returncode == 0, result.stdout


def test_generated_token_files_are_current():
    result = _run("build_tokens.py", "--check")
    assert result.returncode == 0, result.stdout


def test_generated_icon_files_are_current():
    result = _run("build_icons.py", "--check")
    assert result.returncode == 0, result.stdout


def test_state_style_reads_the_shared_vocabulary():
    assert state_style("provisioning") == {"status": "active", "glyph": "●", "semantic": "running", "label": "preparing"}
    assert state_style("needs_human")["status"] == "waiting"
    assert state_style("unknown_state")["label"] == "unknown state"

