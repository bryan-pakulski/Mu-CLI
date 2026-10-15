"""The Wash Rich theme: every ``wash.*`` named style.

Each style is declared once as ``(role, attributes, background)`` and
resolved per colour mode (see :mod:`mu.ui.theme.palette`):

* ``truecolor`` → the Paper or Dusk *ink* step (or wash, for backgrounds)
* ``256`` → the nearest xterm-256 colour
* ``16`` → the ANSI name from ``tokens.json`` ``terminal.ansi16``
* ``none`` → attributes only (muted text becomes ``dim``)

Backgrounds (``bg`` role) are painted only in truecolor; every other mode
falls back to ``fallback`` attributes (for example ``reverse`` for the
selected picker row). Rich markup cannot combine a theme name with extra
words (``[bold wash.ok]`` does not parse), so frequently-bold roles have a
``.bold`` twin (``wash.ok.bold``).
"""

from __future__ import annotations

from typing import Dict, NamedTuple, Optional

from rich.style import Style
from rich.theme import Theme

from mu.design import SEMANTICS, load_tokens, load_vocabulary

from .palette import role_color


class StyleSpec(NamedTuple):
    role: Optional[str]
    attrs: str = ""
    bg: Optional[str] = None  # role whose *wash* step paints the background (truecolor only)
    fallback: str = ""  # attributes used instead of the background outside truecolor
    step: str = "ink"


def _specs() -> Dict[str, StyleSpec]:
    specs: Dict[str, StyleSpec] = {
        "wash.ink": StyleSpec("ink"),
        "wash.ink2": StyleSpec("ink-2"),
        "wash.muted": StyleSpec("ink-3"),
        "wash.plum": StyleSpec("plum"),
        "wash.neutral": StyleSpec("neutral"),
    }
    for role in SEMANTICS:
        specs[f"wash.{role}"] = StyleSpec(role)
    for name in list(specs):
        role = specs[name].role
        specs[f"{name}.bold"] = StyleSpec(role, "bold")
    specs.update({
        "wash.title": StyleSpec("plum", "bold"),
        "wash.heading": StyleSpec("ink", "bold"),
        "wash.key": StyleSpec("accent", "bold"),
        "wash.hint": StyleSpec("ink-3"),
        "wash.border": StyleSpec("hairline-strong"),
        "wash.code": StyleSpec("ink-2"),
        "wash.path": StyleSpec("accent"),
        "wash.link": StyleSpec("accent", "underline"),
        "wash.thinking": StyleSpec("ink-3", "italic"),
        "wash.table.header": StyleSpec("ink-2", "bold"),
        "wash.diff.add": StyleSpec("ok"),
        "wash.diff.del": StyleSpec("danger"),
        "wash.diff.hunk": StyleSpec("info"),
        "wash.diff.add.line": StyleSpec("ok", bg="ok"),
        "wash.diff.del.line": StyleSpec("danger", bg="danger"),
        "wash.meter.fill": StyleSpec("ink-2"),
        "wash.meter.empty": StyleSpec("hairline-strong"),
        "wash.selected": StyleSpec("accent", "bold", bg="accent", fallback="reverse"),
        "wash.chip.plan": StyleSpec("plan", "bold"),
        "wash.chip.yolo": StyleSpec("caution", "bold"),
        "wash.wordmark": StyleSpec("plum", "bold"),
    })
    tokens = load_tokens()
    for mode in tokens["mode"]:
        specs[f"wash.mode.{mode}"] = StyleSpec(f"mode.{mode}")
    for layer in tokens["layer"]:
        # Layers are categorical graphics (bars), so they use the pigment step.
        specs[f"wash.layer.{layer}"] = StyleSpec(f"layer.{layer}", step="pigment")
    for index in range(len(tokens["series"])):
        specs[f"wash.series.{index}"] = StyleSpec(f"series.{index}")
    for name in tokens["series"] + ["plum"]:
        specs[f"wash.pigment.{name}"] = StyleSpec(f"pigment.{name}", step="pigment")
    # Rich built-in names (markdown, repr highlighting, prompts, rules) are
    # re-pointed at Wash roles so default Rich output matches the palette.
    specs.update({
        "markdown.code": StyleSpec("ink", "bold", bg="sunken"),
        "markdown.code_block": StyleSpec("ink-2"),
        "markdown.h1": StyleSpec("plum", "bold"),
        "markdown.h2": StyleSpec("ink", "bold"),
        "markdown.h3": StyleSpec("ink", "bold"),
        "markdown.h4": StyleSpec("ink-2", "bold"),
        "markdown.link": StyleSpec("accent"),
        "markdown.link_url": StyleSpec("accent", "underline"),
        "markdown.block_quote": StyleSpec("ink-3", "italic"),
        "markdown.item.bullet": StyleSpec("accent", "bold"),
        "markdown.item.number": StyleSpec("accent", "bold"),
        "markdown.hr": StyleSpec("hairline-strong"),
        "repr.number": StyleSpec("pigment.apricot"),
        "repr.str": StyleSpec("ok"),
        "repr.path": StyleSpec("accent"),
        "repr.filename": StyleSpec("accent"),
        "repr.url": StyleSpec("accent", "underline"),
        "repr.bool_true": StyleSpec("ok", "italic"),
        "repr.bool_false": StyleSpec("danger", "italic"),
        "repr.none": StyleSpec("plan", "italic"),
        "repr.tag_name": StyleSpec("plan", "bold"),
        "rule.line": StyleSpec("hairline-strong"),
        "prompt.choices": StyleSpec("accent", "bold"),
        "prompt.default": StyleSpec("ink-3", "bold"),
        "prompt.invalid": StyleSpec("danger"),
        "table.header": StyleSpec("ink-2", "bold"),
        "table.title": StyleSpec("ink", "bold"),
    })
    vocab = load_vocabulary()
    for status, spec in vocab["status"].items():
        specs[f"wash.status.{status}"] = StyleSpec(spec["semantic"])
    for severity, spec in vocab["severity"].items():
        specs[f"wash.severity.{severity}"] = StyleSpec(spec["semantic"])
    return specs


STYLE_SPECS: Dict[str, StyleSpec] = _specs()
STYLE_NAMES = tuple(sorted(STYLE_SPECS))


def resolve(name: str, mode: str, theme: str) -> str:
    """Rich style string for a ``wash.*`` name in one colour mode."""
    spec = STYLE_SPECS[name]
    parts = []
    if spec.attrs:
        parts.append(spec.attrs)
    color = role_color(spec.role, mode, theme, spec.step) if spec.role else None
    if mode == "none" and spec.role in ("ink-3", "neutral") and "dim" not in parts:
        parts.append("dim")
    if color:
        parts.append(color)
    if spec.bg:
        if mode == "truecolor":
            back = role_color(spec.bg, mode, theme, "wash")
            if back:
                parts.append(f"on {back}")
        elif spec.fallback:
            parts.append(spec.fallback)
    return " ".join(parts) or "none"


def build_styles(mode: str, theme: str) -> Dict[str, Style]:
    return {name: Style.parse(resolve(name, mode, theme)) for name in STYLE_SPECS}


def build_rich_theme(mode: str, theme: str) -> Theme:
    """A Rich ``Theme`` holding every ``wash.*`` style (inherits Rich defaults)."""
    return Theme(build_styles(mode, theme), inherit=True)


__all__ = ["STYLE_NAMES", "STYLE_SPECS", "StyleSpec", "build_rich_theme", "build_styles", "resolve"]
