"""The one container progress line, shared by ``/container`` and the container TUI.

``● container · build image  step 1/3``: the active glyph and stage label in
the running colour, the message in ink-2. Both parts are literal ``Text`` so
stage names and Docker output (``[1/3]``, ``[/red]``) are never parsed as
Rich markup.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text

from mu.ui.theme.glyphs import ACTIVE, SEPARATOR


def progress_text(stage: Any, message: Any) -> Text:
    label = str(stage or "").replace("_", " ").strip() or "working"
    line = Text(f"{ACTIVE} container {SEPARATOR} {label}", style="wash.running")
    text = str(message or "").strip()
    if text:
        line.append(f"  {text}", style="wash.ink2")
    return line


def print_progress(console: Any, stage: Any, message: Any) -> None:
    """Print the progress line on ``console`` (callers pass their module console)."""
    console.print(progress_text(stage, message), highlight=False)


__all__ = ["print_progress", "progress_text"]
