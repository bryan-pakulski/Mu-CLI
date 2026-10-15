"""Rich-markup helpers shared by every UI surface.

Two rules keep markup safe across the terminal, web, and mobile UIs:

* Values we did not write ourselves (tool args, model output, paths,
  exception text, session/model names) go through :func:`esc` before they
  are interpolated into a markup string.
* Messages that intentionally carry markup are sent with
  :func:`show_markup`, never ``ui.show_info``. ``show_info`` always prints
  its argument literally. UIs that can render markup implement
  ``show_info_markup``; every other UI receives the plain text.

Mode banners and toggles go through :func:`show_notice` (``kind`` in
``info|ok|attention|caution|danger|plan``): ``glyph message`` in the
semantic colour on the terminal, an additive ``notice`` field on web/mobile
events, and ``glyph message`` plain text everywhere else.
"""

from __future__ import annotations

import io
from typing import Any

from rich.console import Console
from rich.errors import MarkupError
from rich.markup import escape as esc
from rich.text import Text

from mu.ui.theme.glyphs import NOTICE, NOTICE_KINDS

__all__ = [
    "NOTICE_KINDS", "esc", "notice_plain", "to_plain", "render_plain", "show_markup",
    "show_notice", "show_renderable",
]


def to_plain(message: Any) -> str:
    """Return the visible text of a markup string (tags removed).

    Malformed markup is returned unchanged instead of raising.
    """
    if isinstance(message, Text):
        return message.plain
    text = str(message)
    try:
        return Text.from_markup(text).plain
    except MarkupError:
        return text


def render_plain(renderable: Any, *, width: int = 100) -> str:
    """Render a Rich renderable (Table, Panel, ...) to uncoloured text."""
    buffer = io.StringIO()
    console = Console(
        file=buffer, width=width, color_system=None, force_terminal=False
    )
    console.print(renderable)
    return buffer.getvalue().rstrip("\n")


def _class_method(ui: Any, name: str):
    # Look the method up on the class, not the instance: headless UIs use
    # ``__getattr__`` to turn unknown attributes into no-ops, and test doubles
    # are often bare objects that only define ``show_info``.
    return getattr(type(ui), name, None)


def show_markup(ui: Any, message: str, *, error: bool = False) -> None:
    """Show an intentionally marked-up message on any UI."""
    if ui is None:
        return
    method = _class_method(ui, "show_error_markup" if error else "show_info_markup")
    if method is not None:
        method(ui, message)
        return
    fallback = getattr(ui, "show_error" if error else "show_info", None)
    if fallback is None and error:
        fallback = getattr(ui, "show_info", None)
    if fallback is not None:
        fallback(to_plain(message))


def show_renderable(ui: Any, renderable: Any) -> None:
    """Show a Rich renderable, falling back to its plain-text rendering."""
    if ui is None:
        return
    method = _class_method(ui, "show_renderable")
    if method is not None:
        method(ui, renderable)
        return
    show_info = getattr(ui, "show_info", None)
    if show_info is not None:
        show_info(render_plain(renderable))


def notice_plain(kind: str, message: Any) -> str:
    """Plain-text form of a notice: ``glyph message``."""
    glyph, _ = NOTICE.get(kind, NOTICE["info"])
    return f"{glyph} {message}"


def show_notice(ui: Any, kind: str, message: Any) -> None:
    """Show a mode banner / toggle notice. ``message`` is printed literally."""
    if ui is None:
        return
    if kind not in NOTICE:
        kind = "info"
    method = _class_method(ui, "show_notice")
    if method is not None:
        method(ui, kind, str(message))
        return
    target = getattr(ui, "show_error" if kind == "danger" else "show_info", None)
    if target is None:
        target = getattr(ui, "show_info", None)
    if target is not None:
        target(notice_plain(kind, message))
