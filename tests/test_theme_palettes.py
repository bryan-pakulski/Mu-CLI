"""Alternative themes (Mu revision 2): Darcula, One, Material and Tokyo Night, each with a light
and a dark side, alongside the default Wash. Colours live in tokens.json and are generated
into tokens.css / wash_tokens.js; html[data-palette] picks one, html[data-theme] picks the side."""

from __future__ import annotations

import json
from pathlib import Path

from mu.design import load_tokens, palette_choices, palettes

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "mu" / "gui"
NAMES = ("darcula", "one", "material", "tokyo")


def test_four_palettes_each_with_a_full_light_and_dark_side():
    tokens = load_tokens()
    assert tuple(palettes()) == NAMES
    paper = tokens["themes"]["paper"]
    for name, spec in palettes().items():
        assert spec["label"] and spec["note"]
        for mode in ("light", "dark"):
            side = spec[mode]
            assert set(side["color"]) == set(paper["color"]), (name, mode)
            assert set(side["pigment"]) == set(paper["pigment"]), (name, mode)
            for pig in side["pigment"].values():
                assert set(pig) == {"wash", "pigment", "ink"}


def test_attention_stays_distinct_from_danger_in_every_palette():
    semantic = load_tokens()["semantic"]
    for name, spec in palettes().items():
        for mode in ("light", "dark"):
            pig = spec[mode]["pigment"]
            inks = {role: pig[semantic[role]]["ink"] for role in ("attention", "danger", "accent", "plan")}
            assert len(set(inks.values())) == 4, (name, mode, inks)


def test_generated_css_and_js_carry_every_palette_side():
    css = (GUI / "static" / "css" / "wash" / "tokens.css").read_text(encoding="utf-8")
    js = (GUI / "static" / "js" / "wash_tokens.js").read_text(encoding="utf-8")
    for name in NAMES:
        assert f'[data-palette="{name}"][data-theme="paper"], [data-palette="{name}"][data-theme="light"] {{' in css
        assert f'[data-palette="{name}"][data-theme="dusk"], [data-palette="{name}"][data-theme="dark"] {{' in css
        assert f'"{name}": {{' in js
    # Palettes are flat: no watercolour art (the mode tint remains in the shell).
    block = css.split('[data-palette="darcula"][data-theme="dusk"]', 1)[1].split("}", 1)[0]
    assert "--wash-art-ambient-dawn: none;" in block and "--wash-glass" not in block
    assert "var p = document.documentElement.getAttribute('data-palette');" in js


def test_palette_choice_is_applied_before_paint_and_everywhere():
    boot = (GUI / "templates" / "partials" / "theme_bootstrap.html").read_text(encoding="utf-8")
    assert "localStorage.getItem('mucli-palette')" in boot
    for name in NAMES:
        assert f"palette === '{name}'" in boot
    theme = (GUI / "static" / "js" / "route_theme.js").read_text(encoding="utf-8")
    assert "var NAMES = ['wash', 'darcula', 'one', 'material', 'tokyo'];" in theme
    assert "window.MuPalette = {" in theme and "mucli-palette-change" in theme
    shell = (GUI / "static" / "js" / "mu_shell.js").read_text(encoding="utf-8")
    assert "palette: window.MuPalette ? window.MuPalette.preference() : null," in shell
    embed = (GUI / "static" / "js" / "wash_shell.js").read_text(encoding="utf-8")
    assert "window.MuPalette.apply(data.palette)" in embed
    palette_js = (GUI / "static" / "js" / "wash" / "palette.js").read_text(encoding="utf-8")
    for name in NAMES:
        assert f"window.MuPalette.set('{name}')" in palette_js


def test_settings_offers_every_theme_with_both_sides():
    rows = palette_choices()
    assert [r["name"] for r in rows] == ["wash", *NAMES]
    for row in rows:
        assert len(row["light"]["dots"]) == len(row["dark"]["dots"]) == 5
    settings = (GUI / "templates" / "settings.html").read_text(encoding="utf-8")
    assert 'data-palette-choice="{{ p.name }}" aria-pressed="false"' in settings
    assert '<option value="paper">Light</option>' in settings and '<option value="dusk">Dark</option>' in settings
