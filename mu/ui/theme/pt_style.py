"""The single Wash prompt_toolkit style.

One dict covers the REPL prompt, the bottom toolbar, the completion menu and
all four pickers (choice picker, choice prompt, quiz, session picker). Class
names are a contract with tests and callers and are kept stable:
``prompt``, ``files``, ``mode-<name>`` for every mode in ``tokens.json``,
``plan-indicator``, ``yolo-indicator``, and the picker classes ``title``,
``subtitle``, ``current``, ``item``, ``description``, ``hint``, ``selected``,
``new``, ``warn``, ``other``, ``correct``, ``wrong``, ``input``, ``explain``.

Nothing blinks. Backgrounds (current row, completion menu) are painted only
in truecolor; other modes use ``reverse`` for the current row.
"""

from __future__ import annotations

from typing import Dict, Optional

from prompt_toolkit.styles import Style

from mu.design import load_tokens

from .detect import background, color_depth
from .palette import pt_color


def _pt(role: Optional[str], attrs: str = "", *, mode: str, theme: str,
        bg: Optional[str] = None, bg_step: str = "wash", fallback: str = "") -> str:
    parts = []
    color = pt_color(role, mode, theme) if role else None
    if color:
        parts.append(f"fg:{color}")
    if bg:
        back = pt_color(bg, mode, theme, bg_step) if mode == "truecolor" else None
        if back:
            parts.append(f"bg:{back}")
        elif fallback:
            parts.append(fallback)
    if attrs:
        parts.append(attrs)
    return " ".join(parts)


def style_dict(mode: Optional[str] = None, theme: Optional[str] = None) -> Dict[str, str]:
    mode = mode or color_depth()
    theme = theme or background()

    def s(role: Optional[str], attrs: str = "", **kw) -> str:
        return _pt(role, attrs, mode=mode, theme=theme, **kw)

    rules: Dict[str, str] = {
        # REPL prompt: μ session · mode [plan] [yolo] ›
        "prompt": s("ink-2"),
        "prompt-mark": s("plum", "bold"),
        "prompt-session": s("ink", "bold"),
        "prompt-sep": s("ink-3"),
        "prompt-arrow": s("accent", "bold"),
        "rprompt": s("ink-3"),
        "files": s("ink-3"),
        "plan-indicator": s("plan", "bold"),
        "yolo-indicator": s("caution", "bold"),
        "auto-suggestion": s("ink-3"),
        # Bottom toolbar: key in ink, description in ink-3, no reverse band.
        "bottom-toolbar": "noreverse " + s("ink-3", bg="sunken", bg_step="ink"),
        "bottom-toolbar.text": s("ink-3"),
        "toolbar-key": s("ink", "bold"),
        "toolbar-hint": s("ink-3"),
        # Completion menu: raised surface, accent selected row.
        "completion-menu": s("ink", bg="raised", bg_step="ink"),
        "completion-menu.completion": s("ink", bg="raised", bg_step="ink"),
        "completion-menu.completion.current": s("accent", "bold", bg="accent", fallback="reverse"),
        "completion-menu.meta.completion": s("ink-3", bg="raised", bg_step="ink"),
        "completion-menu.meta.completion.current": s("accent", bg="accent", fallback="reverse"),
        "completion-menu.multi-column-meta": s("ink-3"),
        "scrollbar.background": s(None, bg="sunken", bg_step="ink"),
        "scrollbar.button": s(None, bg="hairline-strong", bg_step="ink"),
        # Pickers (choice picker, choice prompt, quiz, session picker).
        "title": s("plum", "bold"),
        "subtitle": s("ink-3"),
        "current": s("accent", "bold", bg="accent", fallback="reverse"),
        "item": "",
        "description": s("ink-3"),
        "hint": s("ink-3"),
        "selected": s("ok"),
        "new": s("accent"),
        "warn": s("danger", "bold"),
        "other": s("ink-3", "italic"),
        "correct": s("ok"),
        "wrong": s("danger"),
        "input": s("ink"),
        "explain": s("ink-2"),
    }
    for name in load_tokens()["mode"]:
        rules[f"mode-{name}"] = s(f"mode.{name}", "bold")
    return rules


def prompt_style(mode: Optional[str] = None, theme: Optional[str] = None) -> Style:
    """The prompt_toolkit ``Style`` for the REPL prompt, toolbar and completions."""
    return Style.from_dict(style_dict(mode, theme))


def picker_style(mode: Optional[str] = None, theme: Optional[str] = None) -> Style:
    """The same style, for full-screen pickers (one style for every surface)."""
    return prompt_style(mode, theme)


__all__ = ["picker_style", "prompt_style", "style_dict"]
