"""Rich-markup helpers shared by every UI surface.

Two rules keep markup safe across the terminal, web, and mobile UIs:

* Values we did not write ourselves (tool args, model output, paths,
  exception text, session/model names) go through :func:`esc` before they
  are interpolated into a markup string.
* Messages that intentionally carry markup are sent with
  :func:`show_markup`, never ``ui.show_info``. ``show_info`` always prints
  its argument literally. UIs that can render markup implement
  ``show_info_markup``; every other UI receives the plain text.
"""

from __future__ import annotations

import io
from typing import Any

from rich.console import Console
from rich.errors import MarkupError
from rich.markup import escape as esc
from rich.text import Text

__all__ = ["esc", "to_plain", "render_plain", "show_markup", "show_renderable"]


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
