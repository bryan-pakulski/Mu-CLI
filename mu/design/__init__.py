"""Wash design system: the single token and vocabulary source.

``tokens.json`` holds every colour, type, space, radius, elevation, motion
and z-index value for the Paper and Dusk themes. ``vocabulary.json`` holds
the status / job-state / attention / verdict / severity vocabulary shared
by the terminal, web and Android surfaces.

Python surfaces read these files directly through this module. Web and
Android consume generated copies (``scripts/design/build_tokens.py``);
``tests/design/test_wash_tokens.py`` fails when the generated copies are stale.

See ``documentation/design/wash_design_guide.md``.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

DESIGN_DIR = Path(__file__).resolve().parent
TOKENS_PATH = DESIGN_DIR / "tokens.json"
VOCABULARY_PATH = DESIGN_DIR / "vocabulary.json"

THEMES = ("paper", "dusk")
# Alternative palettes (tokens.json "palettes"): each has a light and a dark spec.
MODES = ("light", "dark")
SEMANTICS = ("accent", "running", "ok", "attention", "caution", "danger", "plan", "info")
STEPS = ("wash", "pigment", "ink")


@lru_cache(maxsize=None)
def load_tokens() -> Dict[str, Any]:
    return json.loads(TOKENS_PATH.read_text(encoding="utf-8"))


def palettes() -> Dict[str, Any]:
    """Alternative palettes: name → {label, note, light, dark} (the Wash theme is the default)."""
    return {k: v for k, v in load_tokens().get("palettes", {}).items() if not k.startswith("_")}


def palette_choices() -> list:
    """Theme picker rows (Wash first): name, label, note and swatches per mode
    (canvas, surface, ink and the accent / ok / attention / danger / plan pigments)."""
    tokens = load_tokens()
    semantic = tokens["semantic"]

    def swatches(spec: Dict[str, Any]) -> Dict[str, Any]:
        color, pig = spec["color"], spec["pigment"]
        return {
            "canvas": color["canvas"], "surface": color["surface"], "ink": color["ink"], "ink3": color["ink-3"],
            "hairline": color["hairline"],
            "dots": [pig[semantic[role]]["pigment"] for role in ("accent", "ok", "attention", "danger", "plan")],
        }

    rows = [{"name": "wash", "label": "Wash", "note": "Watercolour paper by day, dusk by night",
             "light": swatches(tokens["themes"]["paper"]), "dark": swatches(tokens["themes"]["dusk"])}]
    for name, spec in palettes().items():
        rows.append({"name": name, "label": spec["label"], "note": spec["note"],
                     "light": swatches(spec["light"]), "dark": swatches(spec["dark"])})
    return rows


@lru_cache(maxsize=None)
def load_vocabulary() -> Dict[str, Any]:
    return json.loads(VOCABULARY_PATH.read_text(encoding="utf-8"))


def normalize_theme(name: Optional[str]) -> str:
    """Map legacy theme names onto Wash: light → paper, dark → dusk."""
    value = str(name or "").strip().lower()
    if value in ("light", "paper"):
        return "paper"
    return "dusk"


def theme_colors(theme: str) -> Dict[str, str]:
    """Neutral colours (canvas, surface, ink, ...) for a theme."""
    return dict(load_tokens()["themes"][normalize_theme(theme)]["color"])


def pigment(theme: str, name: str, step: str = "ink") -> str:
    return load_tokens()["themes"][normalize_theme(theme)]["pigment"][name][step]


def semantic_pigment(semantic: str) -> Optional[str]:
    """Pigment name behind a semantic role (``None`` for ``neutral``)."""
    return load_tokens()["semantic"].get(semantic)


def semantic(theme: str, role: str, step: str = "ink") -> str:
    """Colour for a semantic role. ``neutral`` maps onto the ink/sunken scale."""
    if role == "neutral":
        colors = theme_colors(theme)
        return {"wash": colors["sunken"], "pigment": colors["ink-3"], "ink": colors["ink-3"]}[step]
    name = semantic_pigment(role)
    if name is None:
        raise KeyError(f"unknown semantic role: {role}")
    return pigment(theme, name, step)


def mode_pigment(mode: str) -> str:
    return load_tokens()["mode"].get(str(mode or "default"), load_tokens()["mode"]["default"])


def layer_pigment(layer: str) -> Optional[str]:
    return load_tokens()["layer"].get(str(layer))


# --- vocabulary --------------------------------------------------------------


def status_spec(status: str) -> Dict[str, str]:
    statuses = load_vocabulary()["status"]
    return dict(statuses.get(status, statuses["pending"]))


def state_style(state: Any) -> Dict[str, str]:
    """Job pipeline state → status, glyph, semantic role and label."""
    key = str(state or "").split(" ")[0].lower()
    vocab = load_vocabulary()
    row = vocab["job_state"].get(key) or vocab["job_status"].get(key)
    status = row["status"] if row else "pending"
    spec = status_spec(status)
    return {
        "status": status,
        "glyph": spec["glyph"],
        "semantic": spec["semantic"],
        "label": row["label"] if row else key.replace("_", " "),
    }


def table_style(table: str, key: Any) -> Dict[str, str]:
    """Any ``{key: {status, label}}`` vocabulary table (``orch_run_state`` ...) → status, glyph,
    semantic role and label, like ``state_style`` for job states."""
    name = str(key or "")
    row = load_vocabulary().get(table, {}).get(name)
    status = row["status"] if row else "pending"
    spec = status_spec(status)
    return {"status": status, "glyph": spec["glyph"], "semantic": spec["semantic"],
            "label": row["label"] if row else name.replace("_", " ")}


def attention_label(reason: Any) -> str:
    row = load_vocabulary()["attention_reason"].get(str(reason or ""))
    return row["label"] if row else str(reason or "").replace("_", " ")


# --- colour maths --------------------------------------------------------------


def hex_to_rgb(value: str) -> Tuple[int, int, int]:
    text = value.lstrip("#")
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def relative_luminance(value: str) -> float:
    channels = []
    for channel in hex_to_rgb(value):
        c = channel / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = channels
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: str, b: str) -> float:
    la, lb = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


_CUBE = (0, 95, 135, 175, 215, 255)


def _xterm_palette() -> Iterable[Tuple[int, Tuple[int, int, int]]]:
    for index in range(16, 232):
        n = index - 16
        yield index, (_CUBE[n // 36], _CUBE[(n // 6) % 6], _CUBE[n % 6])
    for index in range(232, 256):
        level = 8 + (index - 232) * 10
        yield index, (level, level, level)


@lru_cache(maxsize=None)
def nearest_xterm256(value: str) -> int:
    """Nearest xterm-256 colour index (cube + greyscale ramp) for a hex colour."""
    r, g, b = hex_to_rgb(value)
    best, best_d = 16, float("inf")
    for index, (cr, cg, cb) in _xterm_palette():
        d = (2 * (r - cr) ** 2) + (4 * (g - cg) ** 2) + (3 * (b - cb) ** 2)
        if d < best_d:
            best, best_d = index, d
    return best


__all__ = [
    "DESIGN_DIR", "TOKENS_PATH", "VOCABULARY_PATH", "THEMES", "SEMANTICS", "STEPS",
    "load_tokens", "load_vocabulary", "normalize_theme", "theme_colors", "pigment",
    "semantic_pigment", "semantic", "mode_pigment", "layer_pigment", "status_spec",
    "state_style", "attention_label", "hex_to_rgb", "relative_luminance",
    "contrast_ratio", "nearest_xterm256",
]
