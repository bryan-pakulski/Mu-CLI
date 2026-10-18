"""Jobs TUI theme: a compatibility shim over ``mu.ui.theme`` / ``mu.design``.

The jobs dashboard keeps its token-style strings (``"accent bold"``,
``"warn"``, ``"muted"``) and this module's signatures: ``TOKENS``,
``STATUS``, ``SEVERITY``, ``SPINNER``, ``TICK_S``, ``state_style()``,
``color_mode()``, ``pt_style()`` and ``sgr()``. The values come from
``mu/design/tokens.json`` (resolved for the detected Paper/Dusk background)
and the status tables from ``mu/design/vocabulary.json``.

Token names are the legacy jobs names plus the Wash semantics:
``warn`` is the ``attention`` colour (it marks "needs you"), ``text`` is
the terminal default. ``vocabulary.json`` ``legacy_tone`` maps semantics
onto these names.
"""

from __future__ import annotations

from typing import Dict, Iterator, Mapping, Optional, Tuple

from mu.design import load_vocabulary, state_style as _state_style
import mu.ui.theme as _wash
from mu.ui.theme import detect
from mu.ui.theme.glyphs import SPINNER_FRAMES, SPINNER_INTERVAL_S
from mu.ui.theme.palette import pt_color, role_ansi16, role_hex, sgr_color

# jobs token → Wash colour role
_TOKEN_ROLES: Dict[str, Optional[str]] = {
    "accent": "accent",
    "ok": "ok",
    "warn": "attention",
    "attention": "attention",
    "caution": "caution",
    "danger": "danger",
    "running": "running",
    "plan": "plan",
    "info": "info",
    "muted": "ink-3",
    "text": None,
}

ATTRS = ("bold", "dim", "underline", "inverse", "italic")


class _Tokens(Mapping):
    """``TOKENS[name]`` → ``(truecolor hex, ANSI-16 name)`` for the detected theme.

    Resolved lazily so importing the jobs TUI never probes the terminal.
    """

    def __getitem__(self, key: str) -> Tuple[Optional[str], Optional[str]]:
        role = _TOKEN_ROLES[key]
        if role is None:
            return (None, None)
        return (role_hex(role, _wash.theme()), role_ansi16(role))

    def __iter__(self) -> Iterator[str]:
        return iter(_TOKEN_ROLES)

    def __len__(self) -> int:
        return len(_TOKEN_ROLES)

    def __contains__(self, key: object) -> bool:
        return key in _TOKEN_ROLES


TOKENS: Mapping[str, Tuple[Optional[str], Optional[str]]] = _Tokens()

_VOCAB = load_vocabulary()
_LEGACY_TONE: Dict[str, str] = dict(_VOCAB["legacy_tone"])

STATUS: Dict[str, Dict[str, str]] = {
    status: {"icon": spec["glyph"], "token": _LEGACY_TONE[spec["semantic"]], "label": spec["label"]}
    for status, spec in _VOCAB["status"].items()
}

# Derived views of the shared vocabulary (kept for callers of the old tables).
_STATE_STATUS: Dict[str, str] = {k: v["status"] for k, v in _VOCAB["job_state"].items()}
_STATE_LABEL: Dict[str, str] = {k: v["label"] for k, v in _VOCAB["job_state"].items()}

# Environment repair outcome → {status, label} (job_templates.md §7).
REPAIR_OUTCOME: Dict[str, Dict[str, str]] = {k: dict(v) for k, v in (_VOCAB.get("repair_outcome") or {}).items()}

# severity → (token, rank). Tokens are the Wash semantics: major = attention.
SEVERITY: Dict[str, Tuple[str, int]] = {
    name: ("muted" if spec["semantic"] == "neutral" else spec["semantic"], int(spec["rank"]))
    for name, spec in _VOCAB["severity"].items()
}

SPINNER: Tuple[str, ...] = SPINNER_FRAMES
TICK_S: float = SPINNER_INTERVAL_S


def state_style(state: str) -> Dict[str, str]:
    """The single mapping from pipeline states onto the status vocabulary."""
    row = _state_style(state)
    return {"status": row["status"], "token": STATUS[row["status"]]["token"],
            "icon": row["glyph"], "label": row["label"]}


def color_mode(env: Optional[Mapping[str, str]] = None) -> str:
    """``none`` | ``16`` | ``256`` | ``truecolor`` (see ``mu.ui.theme.detect``)."""
    if env is None:
        return detect.color_depth()
    return detect.color_depth(env)


_SGR_ATTR = {"bold": "1", "dim": "2", "italic": "3", "underline": "4", "inverse": "7"}
_PT_ATTR = {"bold": "bold", "dim": "", "italic": "italic", "underline": "underline", "inverse": "reverse"}


def _split(style: str) -> Tuple[Optional[str], list]:
    color = None
    attrs = []
    for part in str(style or "").split():
        if part in _TOKEN_ROLES:
            color = part
        elif part in ATTRS:
            attrs.append(part)
    return color, attrs


def pt_style(style: str, mode: str) -> str:
    """Token style (``"accent bold"``) → prompt_toolkit style string."""
    color, attrs = _split(style)
    out = []
    theme = _wash.theme() if mode in ("256", "truecolor") else "dusk"
    role = _TOKEN_ROLES.get(color) if color else None
    if role and mode != "none":
        value = pt_color(role, mode, theme)
        if value:
            out.append(f"fg:{value}")
    for a in attrs:
        if a == "dim" and mode != "none" and not color:
            value = pt_color("ink-3", mode, theme)
            if value:
                out.append(f"fg:{value}")
        elif _PT_ATTR.get(a):
            out.append(_PT_ATTR[a])
    return " ".join(out)


def sgr(style: str, mode: str) -> str:
    """Token style → ANSI SGR parameter list (``""`` when nothing applies)."""
    color, attrs = _split(style)
    codes = [_SGR_ATTR[a] for a in attrs if a in _SGR_ATTR]
    role = _TOKEN_ROLES.get(color) if color else None
    if role and mode != "none":
        theme = _wash.theme() if mode in ("256", "truecolor") else "dusk"
        code = sgr_color(role, mode, theme)
        if code:
            codes.append(code)
    return ";".join(codes)
