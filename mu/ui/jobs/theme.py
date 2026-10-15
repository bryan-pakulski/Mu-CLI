"""planos design tokens for the terminal job dashboard.

Mirrors planos ``src/core/theme.ts``: semantic tokens with a truecolor value
and a 16-colour fallback, the shared status vocabulary, and ``color_mode``
detection (``NO_COLOR`` → none, ``COLORTERM``/``WT_SESSION`` → truecolor,
otherwise 16 colours). Views never use raw colours, only these tokens.
"""

from __future__ import annotations

import os
from typing import Dict, Mapping, Optional, Tuple

# token -> (truecolor, 16-colour name)
TOKENS: Dict[str, Tuple[Optional[str], Optional[str]]] = {
    "accent": ("#7aa2f7", "cyan"),
    "ok": ("#9ece6a", "green"),
    "warn": ("#e0af68", "yellow"),
    "danger": ("#f7768e", "red"),
    "running": ("#7dcfff", "cyan"),
    "muted": ("#565f89", "gray"),
    "text": (None, None),
}

ATTRS = ("bold", "dim", "underline", "inverse", "italic")

STATUS: Dict[str, Dict[str, str]] = {
    "done": {"icon": "✓", "token": "ok", "label": "done"},
    "active": {"icon": "●", "token": "running", "label": "in progress"},
    "waiting": {"icon": "◆", "token": "warn", "label": "needs you"},
    "failed": {"icon": "✗", "token": "danger", "label": "failed"},
    "skipped": {"icon": "–", "token": "muted", "label": "skipped"},
    "pending": {"icon": "○", "token": "muted", "label": "not started"},
}

_STATE_STATUS = {
    "selected": "pending",
    "provisioning": "active", "planning": "active", "implementing": "active",
    "checking": "active", "reviewing": "active", "pushing": "active",
    "plan_review": "waiting", "review_done": "waiting", "needs_input": "waiting",
    "pushed": "done", "done": "done",
    "failed": "failed",
    "cancelled": "skipped",
}

_STATE_LABEL = {
    "selected": "selected", "provisioning": "preparing", "planning": "planning",
    "plan_review": "plan to approve", "implementing": "implementing", "checking": "checking",
    "reviewing": "reviewing", "review_done": "review done", "pushing": "pushing",
    "pushed": "pushed", "done": "done", "needs_input": "needs input", "failed": "failed",
    "cancelled": "cancelled",
}

SEVERITY: Dict[str, Tuple[str, int]] = {
    "blocker": ("danger", 0),
    "major": ("danger", 1),
    "minor": ("warn", 2),
    "nit": ("muted", 3),
}

SPINNER = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
TICK_S = 0.120


def state_style(state: str) -> Dict[str, str]:
    """The single mapping from pipeline states onto the status vocabulary."""
    key = str(state or "").split(" ")[0].lower()
    status = _STATE_STATUS.get(key, "pending")
    spec = STATUS[status]
    return {"status": status, "token": spec["token"], "icon": spec["icon"],
            "label": _STATE_LABEL.get(key, key.replace("_", " "))}


def color_mode(env: Optional[Mapping[str, str]] = None) -> str:
    """``none`` | ``16`` | ``truecolor`` (same rules as planos ``colorMode``)."""
    env = os.environ if env is None else env
    if env.get("NO_COLOR") not in (None, ""):
        return "none"
    ct = str(env.get("COLORTERM") or "").lower()
    if ct in ("truecolor", "24bit") or env.get("WT_SESSION"):
        return "truecolor"
    return "16"


_PT16 = {"cyan": "ansicyan", "green": "ansigreen", "yellow": "ansiyellow", "red": "ansired",
         "gray": "ansibrightblack"}
_SGR16 = {"cyan": "36", "green": "32", "yellow": "33", "red": "31", "gray": "90"}
_SGR_ATTR = {"bold": "1", "dim": "2", "italic": "3", "underline": "4", "inverse": "7"}
_PT_ATTR = {"bold": "bold", "dim": "", "italic": "italic", "underline": "underline", "inverse": "reverse"}


def _split(style: str) -> Tuple[Optional[str], list]:
    color = None
    attrs = []
    for part in str(style or "").split():
        if part in TOKENS:
            color = part
        elif part in ATTRS:
            attrs.append(part)
    return color, attrs


def pt_style(style: str, mode: str) -> str:
    """Token style (``"accent bold"``) → prompt_toolkit style string."""
    color, attrs = _split(style)
    out = []
    if color and mode != "none":
        tc, c16 = TOKENS[color]
        if mode == "truecolor" and tc:
            out.append(f"fg:{tc}")
        elif c16:
            out.append(f"fg:{_PT16[c16]}")
    for a in attrs:
        if a == "dim" and mode != "none" and not color:
            out.append("fg:ansibrightblack" if mode == "16" else "fg:#565f89")
        elif _PT_ATTR.get(a):
            out.append(_PT_ATTR[a])
    return " ".join(out)


def sgr(style: str, mode: str) -> str:
    """Token style → ANSI SGR parameter list ("" when nothing applies)."""
    color, attrs = _split(style)
    codes = [_SGR_ATTR[a] for a in attrs if a in _SGR_ATTR]
    if color and mode != "none":
        tc, c16 = TOKENS[color]
        if mode == "truecolor" and tc:
            r, g, b = int(tc[1:3], 16), int(tc[3:5], 16), int(tc[5:7], 16)
            codes.append(f"38;2;{r};{g};{b}")
        elif c16:
            codes.append(_SGR16[c16])
    return ";".join(codes)
