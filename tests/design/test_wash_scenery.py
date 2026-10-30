"""Scenery packs for the Wash theme (tokens.json scenery): every pack ships its paintings for the web
and Android in Paper and Dusk (one of its own for every destination), recolours to a real pigment,
and is offered in Settings."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from mu.design import load_tokens, scenery, scenery_choices

ROOT = Path(__file__).resolve().parents[2]
WEB_ART = ROOT / "mu/gui/static/wash/art"
ANDROID_ART = ROOT / "mobile/android/assets/wash"
WEB_SIZES = {"{p}-{t}": (800, 1600, 3840), "ambient-{p}-{t}": (960, 1920, 3840), "spot-{p}-{t}": (320, 1024)}
ANDROID_FILES = ("{p}-{t}", "ambient-{p}-{t}", "spot-{p}-{t}")


def _packs():
    return {name: spec for name, spec in scenery().items() if spec.get("accent")}


def test_classic_is_the_default_and_packs_are_listed():
    names = list(scenery())
    assert names[0] == "classic" and not scenery()["classic"].get("accent")
    assert {"desert", "forest", "ocean", "space"} <= set(_packs())


def test_every_pack_ships_its_art_for_the_web_and_android_in_both_themes():
    for pack in _packs():
        for theme in ("paper", "dusk"):
            for pattern, widths in WEB_SIZES.items():
                for width in widths:
                    path = WEB_ART / f"{pattern.format(p=pack, t=theme)}-{width}.webp"
                    with Image.open(path) as im:
                        assert im.format == "WEBP" and im.width == width, path
            for pattern in ANDROID_FILES:
                for suffix in ("", "@2x"):
                    path = ANDROID_ART / f"{pattern.format(p=pack, t=theme)}{suffix}.webp"
                    with Image.open(path) as im:
                        assert im.format == "WEBP", path


def test_every_pack_paints_every_destination_for_the_web_and_android():
    destinations = [name for name in load_tokens()["destination"] if not name.startswith("_")]
    for pack, spec in _packs().items():
        assert spec.get("pages") == destinations, pack       # no destination reuses the general scene
        for page in destinations:
            for theme in ("paper", "dusk"):
                for name, widths in ((f"{page}-{theme}", (800, 1600, 3840)), (f"ambient-{page}-{theme}", (960, 1920, 3840))):
                    for width in widths:
                        path = WEB_ART / "pages" / pack / f"{name}-{width}.webp"
                        with Image.open(path) as im:
                            assert im.format == "WEBP" and im.width == width, path
                    for suffix in ("", "@2x"):
                        path = ANDROID_ART / "pages" / pack / f"{name}{suffix}.webp"
                        with Image.open(path) as im:
                            assert im.format == "WEBP", path


def test_every_pack_is_a_generated_scene_and_recolours_to_a_pigment():
    tokens = load_tokens()
    pigments = set(tokens["themes"]["paper"]["pigment"])
    for pack, spec in _packs().items():
        assert pack in tokens["glass"]["ambient"]        # generated and contrast-checked like the classic scenes
        assert spec["accent"] in pigments
        assert 0 < float(spec.get("leaf_clear", 1)) <= 1


def test_settings_offers_every_pack_with_both_thumbnails():
    rows = {row["name"]: row for row in scenery_choices()}
    assert list(rows) == list(scenery())
    for row in rows.values():
        for theme in ("paper", "dusk"):
            thumb = row[theme]["thumb"]
            assert (ROOT / "mu/gui" / thumb.lstrip("/")).exists(), thumb
            assert row[theme]["pigment"].startswith("#")
