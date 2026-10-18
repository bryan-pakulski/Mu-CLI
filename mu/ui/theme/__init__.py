"""Wash terminal theme: one console, one Rich theme, one prompt_toolkit style.

Public API:

* :func:`get_console` returns the process-wide Rich console. Every terminal
  module prints through it (``RichUI.console`` *is* this console).
* :func:`style` resolves a ``wash.*`` style name to a Rich ``Style``.
* :func:`status` maps a job/pipeline state onto glyph, label and style.
* :data:`T` is a read-only view of the resolved palette for the active mode.
* :func:`esc` escapes non-literal values before they go into markup.

``wash.*`` styles are also installed into Rich's default theme, so any
console (including the recording consoles tests and ``render_plain`` build)
resolves the names. Colour depth and background come from
:mod:`mu.ui.theme.detect` and are cached per process; :func:`refresh`
re-detects (tests, ``/set`` theme changes).
"""

from __future__ import annotations

import sys
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional

import rich.themes
from rich.console import Console
from rich.markup import escape as esc
from rich.style import Style

from mu.design import state_style as _state_style

from . import detect
from .rich_theme import STYLE_NAMES, STYLE_SPECS, build_styles, resolve

_RICH_COLOR_SYSTEM = {"none": None, "16": "standard", "256": "256", "truecolor": "truecolor"}


class WashConsole(Console):
    """The process console. Counts prints so tool markers can be rewritten
    in place only when nothing else has been printed since."""

    print_count = 0

    def print(self, *args: Any, **kwargs: Any) -> None:  # noqa: A003 - Rich API
        self.print_count += 1
        super().print(*args, **kwargs)


_state: Dict[str, Any] = {"mode": None, "theme": None, "console": None, "probed": False}


def mode() -> str:
    """Active colour depth: ``none`` | ``16`` | ``256`` | ``truecolor``."""
    if _state["mode"] is None:
        _install()
    return _state["mode"]


def theme() -> str:
    """Active background theme: ``paper`` | ``dusk``."""
    if _state["theme"] is None:
        _install()
    return _state["theme"]


def _register_spinner() -> None:
    from rich._spinners import SPINNERS

    from .glyphs import SPINNER_FRAMES, SPINNER_INTERVAL_S

    SPINNERS["wash"] = {"interval": int(SPINNER_INTERVAL_S * 1000), "frames": list(SPINNER_FRAMES)}


def _install(probe: bool = True) -> None:
    _register_spinner()
    depth = detect.color_depth()
    bg = detect.background(probe=probe)
    rich.themes.DEFAULT.styles.update(build_styles(depth, bg))
    _state["mode"], _state["theme"] = depth, bg
    _state["probed"] = _state["probed"] or probe


def refresh(*, probe: bool = False) -> None:
    """Re-detect depth/background and re-resolve every ``wash.*`` style.

    The single console keeps working: it reads styles from the shared
    default theme, which is updated in place.
    """
    detect.reset_cache()
    _install(probe=probe)
    console = _state["console"]
    if console is not None:
        system = _color_system_for(console.file)
        if system != "auto":
            console._color_system = _rich_color_system_enum(system)


def _color_system_for(stream: Any) -> Optional[str]:
    try:
        is_tty = bool(stream.isatty())
    except (AttributeError, ValueError):
        is_tty = False
    if not is_tty:
        return "auto"
    return _RICH_COLOR_SYSTEM[detect.color_depth()]


def _rich_color_system_enum(name: Optional[str]):
    from rich.console import COLOR_SYSTEMS

    return None if name is None else COLOR_SYSTEMS[name]


def get_console() -> Console:
    """The single process-wide Rich console, themed with ``wash.*`` styles."""
    console = _state["console"]
    if console is None:
        if not _state["probed"]:
            _install(probe=True)
        system = _color_system_for(sys.stdout)
        kwargs = {} if system == "auto" else {"color_system": system}
        console = WashConsole(theme=rich.themes.DEFAULT, **kwargs)
        _state["console"] = console
    return console


def style(name: str) -> Style:
    """Rich ``Style`` for a ``wash.*`` name in the active colour mode."""
    return Style.parse(resolve(name, mode(), theme()))


def status(state: Any) -> Dict[str, str]:
    """Job/pipeline state → status, glyph, semantic, label and Rich style name."""
    row = dict(_state_style(state))
    row["style"] = f"wash.status.{row['status']}"
    return row


class _Palette(Mapping):
    """Read-only ``T["ok"]`` → resolved Rich colour string (or ``""``)."""

    def __getitem__(self, key: str) -> str:
        from .palette import role_color

        return role_color(key, mode(), theme()) or ""

    def __iter__(self):
        from mu.design import SEMANTICS

        return iter(("ink", "ink-2", "ink-3", "plum") + tuple(SEMANTICS))

    def __len__(self) -> int:
        return len(tuple(iter(self)))


T: Mapping[str, str] = _Palette()

# Resolve every wash.* name into Rich's default theme at import, without
# touching the terminal; get_console() probes the background (OSC 11) once.
_install(probe=False)
STYLES: Mapping[str, Any] = MappingProxyType(STYLE_SPECS)

__all__ = [
    "STYLE_NAMES", "STYLES", "T", "WashConsole", "esc", "get_console", "mode",
    "refresh", "status", "style", "theme",
]
