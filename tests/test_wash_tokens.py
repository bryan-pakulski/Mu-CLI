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

ROOT = Path(__file__).resolve().parents[1]
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


def test_semantic_mode_layer_maps_point_at_pigments():
    tokens = load_tokens()
    pigments = set(tokens["themes"]["paper"]["pigment"])
    assert set(tokens["semantic"]) == set(SEMANTICS)
    for mapping in ("semantic", "mode", "layer"):
        assert set(tokens[mapping].values()) <= pigments, mapping
    assert set(tokens["series"]) <= pigments
    assert set(tokens["layer"]) == {"L0", "L1A", "L1B", "L2", "L3", "L4", "L4B", "L5"}
    assert set(tokens["layer_names"]) == set(tokens["layer"])


def test_contrast_floors_hold():
    result = _run("check_contrast.py")
    assert result.returncode == 0, result.stdout


def test_attention_is_distinct_from_danger_accent_and_plan():
    for theme in THEMES:
        inks = {role: semantic(theme, role) for role in ("attention", "danger", "accent", "plan")}
        assert len(set(inks.values())) == 4, (theme, inks)


def test_generated_token_files_are_current():
    result = _run("build_tokens.py", "--check")
    assert result.returncode == 0, result.stdout


def test_generated_icon_files_are_current():
    result = _run("build_icons.py", "--check")
    assert result.returncode == 0, result.stdout


def test_generated_css_exposes_both_themes_and_legacy_theme_names():
    css = (ROOT / "mu/gui/static/css/wash/tokens.css").read_text(encoding="utf-8")
    for selector in ('[data-theme="paper"]', '[data-theme="dusk"]', '[data-theme="light"]', '[data-theme="dark"]'):
        assert selector in css
    for name in ("--wash-canvas", "--wash-attention-ink", "--wash-layer-L4B", "--wash-mode-teacher", "--wash-radius-pill"):
        assert f"{name}:" in css


def test_fonts_and_art_are_vendored_with_licences():
    fonts = ROOT / "mu/gui/static/fonts"
    for name in ("InterVariable.woff2", "InterVariable-Italic.woff2", "JetBrainsMono-Variable.woff2",
                 "Inter-OFL.txt", "JetBrainsMono-OFL.txt"):
        assert (fonts / name).is_file(), name
    art = ROOT / "mu/gui/static/wash/art"
    for wash in ("dawn", "lagoon", "meadow"):
        for theme in THEMES:
            path = art / f"{wash}-{theme}-1600.webp"
            assert path.is_file() and path.stat().st_size < 60_000, path.name
    assert (ROOT / "mu/design/icons/LICENSE-lucide.txt").is_file()


@pytest.mark.parametrize("legacy,expected", [("light", "paper"), ("dark", "dusk"), ("paper", "paper"), (None, "dusk")])
def test_legacy_theme_names_normalise(legacy, expected):
    assert normalize_theme(legacy) == expected


def test_state_style_reads_the_shared_vocabulary():
    assert state_style("provisioning") == {"status": "active", "glyph": "●", "semantic": "running", "label": "preparing"}
    assert state_style("needs_human")["status"] == "waiting"
    assert state_style("unknown_state")["label"] == "unknown state"


def test_xterm_mapping_is_stable():
    assert nearest_xterm256("#000000") == 16
    assert nearest_xterm256("#FFFFFF") == 231
    vocab = load_vocabulary()
    assert vocab["status"]["waiting"]["semantic"] == "attention"
    # every pigment ink resolves to a 256-colour index
    for theme in THEMES:
        for steps in load_tokens()["themes"][theme]["pigment"].values():
            assert 16 <= nearest_xterm256(steps["ink"]) <= 255
    assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0)


def test_glass_tokens_exist_for_both_themes_and_respect_the_opacity_floor():
    tokens = load_tokens()
    keys = {"surface", "strong", "subtle", "border", "edge", "highlight", "shadow"}
    floor = tokens["glass"]["min_alpha"]
    for theme in THEMES:
        glass = tokens["themes"][theme]["glass"]
        assert set(glass) == keys, theme
        for name in ("surface", "strong"):
            alpha = float(re.search(r"rgba\([^)]*,\s*([\d.]+)\s*\)", glass[name]).group(1))
            assert alpha >= floor, (theme, name, alpha)
    assert tokens["glass"]["blur"] > 0 and tokens["glass"]["blur_strong"] >= tokens["glass"]["blur"]


def test_ambient_washes_are_vendored_for_web_and_android():
    for wash in load_tokens()["glass"]["ambient"]:
        for theme in THEMES:
            for width in (1920, 960):
                path = ROOT / f"mu/gui/static/wash/art/ambient-{wash}-{theme}-{width}.webp"
                assert path.is_file() and path.stat().st_size < 80_000, path.name
            assert (ROOT / f"mobile/android/assets/wash/ambient-{wash}-{theme}.webp").is_file()


def test_generated_css_exposes_glass_variables():
    css = (ROOT / "mu/gui/static/css/wash/tokens.css").read_text(encoding="utf-8")
    for name in ("--wash-glass:", "--wash-glass-strong:", "--wash-glass-subtle:", "--wash-glass-border:",
                 "--wash-glass-edge:", "--wash-glass-highlight:", "--wash-glass-shadow:", "--wash-glass-blur:",
                 "--wash-glass-saturate:", "--wash-art-ambient-dawn:"):
        assert name in css, name
