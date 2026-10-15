"""Read the Wash stylesheets as rules for source-level GUI tests.

The legacy app.css / theme.css / tokens.css are gone (Wash final pass); the
rules they carried live in static/css/wash/ (base.css, patterns/*.css,
components/*.css). Tests that pin a surface's styling look the rule up by
selector here instead of matching one file's exact formatting.

    css("patterns/panels.css", ...)   → concatenated text (comments removed)
    decls(text, ".selector")          → {property: value} merged over every
                                        rule whose selector list contains it
    has_selector(text, ".selector")   → bool
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WASH = ROOT / "mu" / "gui" / "static" / "css" / "wash"

#: Files that carry the chat page's surfaces (wash.css + the chat bundle).
CHAT_SURFACES = (
    "base.css", "patterns/conversation.css", "patterns/overlays.css", "patterns/panels.css",
    "patterns/settings.css", "patterns/welcome.css", "patterns/chat.css",
)


def _strip_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def css(*names: str) -> str:
    names = names or CHAT_SURFACES
    return "\n".join(_strip_comments((WASH / name).read_text(encoding="utf-8")) for name in names)


def _norm(selector: str) -> str:
    return re.sub(r"\s+", " ", selector.strip())


def _split(prelude: str) -> list[str]:
    parts, depth, cur = [], 0, ""
    for ch in prelude:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    return [_norm(p) for p in parts if p.strip()]


def rules(text: str):
    """Yield (selectors, declarations, media) for every style rule."""
    stack = []
    start = 0
    for i, ch in enumerate(text):
        if ch == "{":
            stack.append((text[start:i].strip(), i + 1))
            start = i + 1
        elif ch == "}":
            if stack:
                prelude, body_start = stack.pop()
                body = text[body_start:i]
                if not prelude.startswith("@") and "{" not in body:
                    decls = {}
                    for d in re.split(r";(?![^(]*\))", body):
                        if ":" in d:
                            k, v = d.split(":", 1)
                            decls[k.strip().lower()] = _norm(v)
                    media = [p for p, _ in stack if p.startswith("@")]
                    yield _split(prelude), decls, media
            start = i + 1
        elif ch == ";" and not stack:
            start = i + 1


def decls(text: str, selector: str, media: str | None = None) -> dict:
    """Declarations merged (in source order) over every rule listing `selector`."""
    want = _norm(selector)
    out: dict = {}
    for selectors, d, m in rules(text):
        if want in selectors and (media is None or any(media in x for x in m)):
            out.update(d)
    return out


def has_selector(text: str, selector: str) -> bool:
    want = _norm(selector)
    return any(want in selectors for selectors, _, _ in rules(text))
