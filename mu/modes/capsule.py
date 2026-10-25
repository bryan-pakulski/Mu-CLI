"""Mode capsules: volatile mode state in the request-only TURN CONTEXT (modes v2 §3.6).

Each mode contributes a short block — the current task and remaining criteria,
the open debug case, the loop mission and budget, due teacher reviews and the
learner profile. It rides on the turn's user message in the projected request
(``runtime_state.attach_turn_context``) and is never saved to history, so the
system prompt stays byte-stable and memory recall only sees the user's text.

Builders live with their engines and are imported lazily (``_BUILDERS``).
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

CAPSULE_LIMIT = 1200

# mode -> "module:function"; each returns the mode's lines ('' when nothing to say).
_BUILDERS: Dict[str, str] = {
    "default": "mu.modes.receipt:capsule",
    "debug": "mu.debug.engine:capsule",
    "security": "mu.security.engine:capsule",
    "feature": "mu.feature.engine:capsule",
    "loop": "mu.loop.engine:capsule",
    "teacher": "mu.teacher.gates:capsule",
}


def _builder(mode: str) -> Callable[[Any], str] | None:
    target = _BUILDERS.get(mode)
    if not target:
        return None
    module, _, func = target.partition(":")
    try:
        return getattr(importlib.import_module(module), func)
    except Exception:  # noqa: BLE001
        logger.debug("capsule builder %s unavailable", target, exc_info=True)
        return None


def _common_lines(session: Any, mode: str) -> List[str]:
    from .blockers import blocker_state
    from .work import work_state

    lines: List[str] = []
    try:
        for blocker in blocker_state(session).open_blockers()[:3]:
            lines.append(f"Open blocker {blocker.id}: {blocker.summary} — needs: {blocker.needs}")
    except Exception:  # noqa: BLE001
        pass
    if mode in ("default", "debug"):
        try:
            work = work_state(session)
            current = work.current()
            open_items = work.open_items()
            if open_items:
                head = f"Plan: {len(open_items)} open work item(s)"
                if current is not None:
                    head += f"; in progress {current.id} {current.title}"
                lines.append(head)
        except Exception:  # noqa: BLE001
            pass
    return lines


def build_capsule(session: Any, mode: str) -> str:
    mode = str(mode or "default").strip().lower() or "default"
    lines: List[str] = []
    builder = _builder(mode)
    if builder is not None:
        try:
            body = str(builder(session) or "").strip()
        except Exception:  # noqa: BLE001 — never break prompt assembly
            logger.debug("capsule for %s failed", mode, exc_info=True)
            body = ""
        if body:
            lines.append(body)
    lines.extend(_common_lines(session, mode))
    if not lines:
        return ""
    text = f"MODE STATE — {mode}:\n" + "\n".join(lines)
    if len(text) > CAPSULE_LIMIT:
        text = text[: CAPSULE_LIMIT - 1].rstrip() + "…"
    return text


__all__ = ["CAPSULE_LIMIT", "build_capsule"]
