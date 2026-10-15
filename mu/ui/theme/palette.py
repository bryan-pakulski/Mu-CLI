"""Resolve Wash colour roles for a terminal colour mode and background.

A *role* names a colour in ``mu/design/tokens.json``:

* neutrals: ``ink``, ``ink-2``, ``ink-3`` (alias ``muted``), ``plum``,
  ``hairline``, ``hairline-strong``, ``sunken``, ``surface``, ``raised``
* semantics: ``accent``, ``running``, ``ok``, ``attention``, ``caution``,
  ``danger``, ``plan``, ``info``, ``neutral``
* categories: ``mode.<name>``, ``layer.<L>``, ``series.<n>``,
  ``pigment.<name>``

Each role resolves, per colour mode, to the truecolor hex of the detected
theme (Paper or Dusk), the nearest xterm-256 index, the ANSI-16 name from
``tokens.json`` ``terminal.ansi16``, or nothing at all (``none``).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Dict, Optional

from mu.design import (
    SEMANTICS,
    layer_pigment,
    load_tokens,
    mode_pigment,
    nearest_xterm256,
    pigment,
    semantic,
    theme_colors,
)

NEUTRALS = (
    "ink", "ink-2", "ink-3", "plum", "hairline", "hairline-strong",
    "control-border", "sunken", "surface", "raised", "canvas",
)
_ALIASES = {"muted": "ink-3", "ink2": "ink-2", "ink3": "ink-3"}


def canonical(role: str) -> str:
    return _ALIASES.get(role, role)


def ansi16_table() -> Dict[str, str]:
    return dict(load_tokens()["terminal"]["ansi16"])


@lru_cache(maxsize=None)
def pigment_ansi16() -> Dict[str, str]:
    """Pigment name → ANSI-16 name, derived from the semantic mapping."""
    table = ansi16_table()
    out: Dict[str, str] = {}
    for role, name in load_tokens()["semantic"].items():
        if role in table:
            out.setdefault(name, table[role])
    out.setdefault("plum", table.get("plum", "magenta"))
    return out


def series(index: int) -> str:
    names = load_tokens()["series"]
    return names[int(index) % len(names)]


def role_pigment(role: str) -> Optional[str]:
    """Pigment behind a categorical or semantic role (``None`` for neutrals)."""
    role = canonical(role)
    if role.startswith("mode."):
        return mode_pigment(role[5:])
    if role.startswith("layer."):
        return layer_pigment(role[6:])
    if role.startswith("series."):
        return series(int(role[7:]))
    if role.startswith("pigment."):
        return role[8:]
    if role in SEMANTICS:
        return load_tokens()["semantic"][role]
    return None


def role_hex(role: str, theme: str, step: str = "ink") -> Optional[str]:
    """Truecolor hex for a role in ``paper`` or ``dusk``."""
    role = canonical(role)
    if role in NEUTRALS:
        value = theme_colors(theme).get(role)
        return value if value and value.startswith("#") else None
    if role == "neutral":
        return semantic(theme, "neutral", step)
    name = role_pigment(role)
    if name is None:
        return None
    return pigment(theme, name, step)


def role_ansi16(role: str) -> Optional[str]:
    """ANSI-16 colour name for a role (``None``: no colour; ``default``: terminal fg)."""
    role = canonical(role)
    table = ansi16_table()
    if role in ("ink", "ink-2"):
        # Explicit terminal default so ink text overrides an enclosing colour
        # (e.g. a panel title sitting on a muted border).
        return table.get(role, "default")
    if role in ("surface", "raised", "canvas", "sunken"):
        return None
    if role in ("ink-3", "neutral", "hairline", "hairline-strong", "control-border"):
        return table.get("muted", "bright_black")
    if role in table:
        return table[role]
    name = role_pigment(role)
    if name is not None:
        return pigment_ansi16().get(name)
    return None


def role_color(role: str, mode: str, theme: str, step: str = "ink") -> Optional[str]:
    """Rich colour string for a role, or ``None`` when no colour applies."""
    if mode == "none":
        return None
    if mode == "16":
        return role_ansi16(role)
    value = role_hex(role, theme, step)
    if value is None:
        return None
    if mode == "256":
        return f"color({nearest_xterm256(value)})"
    return value


def pt_color(role: str, mode: str, theme: str, step: str = "ink") -> Optional[str]:
    """prompt_toolkit colour (``#rrggbb`` or ``ansi…``) for a role."""
    if mode == "none":
        return None
    if mode == "16":
        name = role_ansi16(role)
        if not name or name == "default":
            return None
        return f"ansi{name.replace('_', '')}"
    return role_hex(role, theme, step)


_SGR16 = {
    "black": 30, "red": 31, "green": 32, "yellow": 33, "blue": 34, "magenta": 35,
    "cyan": 36, "white": 37, "bright_black": 90, "bright_red": 91, "bright_green": 92,
    "bright_yellow": 93, "bright_blue": 94, "bright_magenta": 95, "bright_cyan": 96,
    "bright_white": 97,
}


def sgr_color(role: str, mode: str, theme: str, step: str = "ink") -> Optional[str]:
    """SGR foreground parameter for a role (``None`` when nothing applies)."""
    if mode == "none":
        return None
    if mode == "16":
        name = role_ansi16(role)
        return str(_SGR16[name]) if name in _SGR16 else None
    value = role_hex(role, theme, step)
    if value is None:
        return None
    if mode == "256":
        return f"38;5;{nearest_xterm256(value)}"
    r, g, b = int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)
    return f"38;2;{r};{g};{b}"


__all__ = [
    "NEUTRALS", "ansi16_table", "canonical", "pigment_ansi16", "pt_color", "role_ansi16",
    "role_color", "role_hex", "role_pigment", "series", "sgr_color",
]
