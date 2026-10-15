"""Shared terminal glyphs, read from ``mu/design/vocabulary.json``.

No emoji in product chrome: status is carried by the shared glyph
vocabulary (``○ ● ◆ ✓ ✗ –``) and always paired with a text label; tools
are named by short mono kind labels (``read``, ``edit``, ``sh``, ...).
"""

from __future__ import annotations

import re
from typing import Dict, Tuple

from mu.design import load_vocabulary

_VOCAB = load_vocabulary()

#: status → glyph (``done`` → ``✓``, ``waiting`` → ``◆``, ...)
STATUS_GLYPHS: Dict[str, str] = {k: v["glyph"] for k, v in _VOCAB["status"].items()}
#: status → semantic role (``waiting`` → ``attention``)
STATUS_SEMANTICS: Dict[str, str] = {k: v["semantic"] for k, v in _VOCAB["status"].items()}
#: status → canonical label (``waiting`` → ``needs you``)
STATUS_LABELS: Dict[str, str] = {k: v["label"] for k, v in _VOCAB["status"].items()}

#: tool kind → short mono label (``shell`` → ``sh``)
TOOL_KIND_LABELS: Dict[str, str] = {k: v["label"] for k, v in _VOCAB["tool_kind"].items()}

SPINNER_FRAMES: Tuple[str, ...] = tuple(_VOCAB["spinner"]["frames"])
SPINNER_INTERVAL_S: float = _VOCAB["spinner"]["interval_ms"] / 1000.0

PENDING = STATUS_GLYPHS["pending"]
ACTIVE = STATUS_GLYPHS["active"]
WAITING = STATUS_GLYPHS["waiting"]
DONE = STATUS_GLYPHS["done"]
FAILED = STATUS_GLYPHS["failed"]
SKIPPED = STATUS_GLYPHS["skipped"]

# Chrome glyphs that are not statuses.
WORDMARK = "μcli"
PROMPT_MARK = "μ"
PROMPT_ARROW = "›"
CURSOR = "❯"
SEPARATOR = "·"
RULE = "━"
METER_FILL = "█"
METER_EMPTY = "░"
USER_GUTTER = "›"

#: notice kind → (glyph, semantic role) for ``show_notice``
NOTICE: Dict[str, Tuple[str, str]] = {
    "info": ("›", "info"),
    "ok": (DONE, "ok"),
    "attention": (WAITING, "attention"),
    "caution": ("!", "caution"),
    "danger": (FAILED, "danger"),
    "plan": ("◇", "plan"),
}
NOTICE_KINDS = tuple(NOTICE)


def status_glyph(status: str) -> str:
    return STATUS_GLYPHS.get(status, PENDING)


def spinner_frame(tick: int) -> str:
    return SPINNER_FRAMES[int(tick) % len(SPINNER_FRAMES)]


_KIND_RULES = (
    ("container", re.compile(r"container")),
    ("agent", re.compile(r"(sub_?agent|spawn_agent|agent_)")),
    ("shell", re.compile(r"^(bash|shell|run_command|exec)")),
    ("web", re.compile(r"(web_|fetch|url|arxiv|hackernews|reddit|doi_|scholar|wiki)")),
    ("memory", re.compile(r"(memory|recall|scratchpad|remember)")),
    ("edit", re.compile(r"(write|edit|apply_diff|patch|create_file|delete_file|move_file|rename|replace)")),
    ("search", re.compile(r"(search|grep|find|glob|retrieve)")),
    ("read", re.compile(r"(read|list_dir|get_chunk|result_|view|cat|open_|attachment|document)")),
    ("plan", re.compile(r"(task|phase|plan|goal|checkpoint|curriculum|feature)")),
)


def tool_kind(tool_name: str) -> str:
    """Classify a tool name into one of the shared tool kinds."""
    name = str(tool_name or "").lower()
    for kind, pattern in _KIND_RULES:
        if pattern.search(name):
            return kind
    return "other"


def tool_kind_label(tool_name: str) -> str:
    return TOOL_KIND_LABELS.get(tool_kind(tool_name), TOOL_KIND_LABELS["other"])


__all__ = [
    "ACTIVE", "CURSOR", "DONE", "FAILED", "METER_EMPTY", "METER_FILL", "NOTICE",
    "NOTICE_KINDS", "PENDING", "PROMPT_ARROW", "PROMPT_MARK", "RULE", "SEPARATOR",
    "SKIPPED", "SPINNER_FRAMES", "SPINNER_INTERVAL_S", "STATUS_GLYPHS", "STATUS_LABELS",
    "STATUS_SEMANTICS", "TOOL_KIND_LABELS", "USER_GUTTER", "WAITING", "WORDMARK",
    "spinner_frame", "status_glyph", "tool_kind", "tool_kind_label",
]
