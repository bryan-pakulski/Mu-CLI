"""Wash Pygments styles (Paper and Dusk), replacing the hardcoded monokai.

Token colours come from ``tokens.json`` ``syntax`` (keyword → lilac, string
→ meadow, ...) using each theme's text-safe *ink* step. In 16-colour mode
:func:`syntax_theme` returns an ANSI theme built from the same mapping so
code stays readable on any terminal palette. Backgrounds are never painted:
code blocks sit on the terminal's own background.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Dict, Optional, Type

from pygments.style import Style as PygmentsStyle
from pygments.token import (
    Comment,
    Error,
    Generic,
    Keyword,
    Name,
    Number,
    Operator,
    Punctuation,
    String,
    Text,
    Token,
)
from rich.style import Style as RichStyle
from rich.syntax import ANSISyntaxTheme, SyntaxTheme

from mu.design import load_tokens

from .detect import background, color_depth
from .palette import role_color, role_hex


def _role(name: str) -> str:
    """``tokens.json`` syntax values are pigment names or neutral names."""
    return name if name.startswith("ink") else f"pigment.{name}"


def _token_roles() -> Dict[object, tuple]:
    syntax = load_tokens()["syntax"]
    return {
        Token: ("ink", ""),
        Text: ("ink", ""),
        Comment: (_role(syntax["comment"]), "italic"),
        Keyword: (_role(syntax["keyword"]), ""),
        Keyword.Type: (_role(syntax["type"]), ""),
        Name.Builtin: (_role(syntax["type"]), ""),
        Name.Class: (_role(syntax["type"]), ""),
        Name.Function: (_role(syntax["function"]), ""),
        Name.Decorator: (_role(syntax["function"]), ""),
        Name.Tag: (_role(syntax["keyword"]), ""),
        Name.Attribute: (_role(syntax["function"]), ""),
        String: (_role(syntax["string"]), ""),
        Number: (_role(syntax["number"]), ""),
        Operator: (_role(syntax["punctuation"]), ""),
        Punctuation: (_role(syntax["punctuation"]), ""),
        Error: ("danger", ""),
        Generic.Inserted: ("ok", ""),
        Generic.Deleted: ("danger", ""),
        Generic.Heading: ("info", "bold"),
        Generic.Subheading: ("info", ""),
        Generic.Emph: ("ink", "italic"),
        Generic.Strong: ("ink", "bold"),
    }


def _make_style(theme: str) -> Type[PygmentsStyle]:
    styles = {}
    for token, (role, attrs) in _token_roles().items():
        color = role_hex(role, theme)
        styles[token] = " ".join(part for part in (attrs, color or "") if part)
    name = "WashPaperStyle" if theme == "paper" else "WashDuskStyle"
    return type(name, (PygmentsStyle,), {
        "name": f"wash-{theme}",
        "background_color": None,
        "highlight_color": role_hex("accent", theme, "wash"),
        "styles": styles,
    })


WashPaperStyle = _make_style("paper")
WashDuskStyle = _make_style("dusk")


def pygments_style(theme: Optional[str] = None) -> Type[PygmentsStyle]:
    return WashPaperStyle if (theme or background()) == "paper" else WashDuskStyle


@lru_cache(maxsize=None)
def _ansi_theme(mode: str) -> SyntaxTheme:
    style_map = {}
    for token, (role, attrs) in _token_roles().items():
        color = role_color(role, mode, "dusk") if mode != "none" else None
        spec = " ".join(part for part in (attrs, color or "") if part)
        style_map[token] = RichStyle.parse(spec or "none")
    return ANSISyntaxTheme(style_map)


def syntax_theme(mode: Optional[str] = None, theme: Optional[str] = None):
    """Theme argument for ``rich.syntax.Syntax`` in the active colour mode."""
    mode = mode or color_depth()
    if mode in ("16", "none"):
        return _ansi_theme(mode)
    return pygments_style(theme)


__all__ = ["WashDuskStyle", "WashPaperStyle", "pygments_style", "syntax_theme"]
