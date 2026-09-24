"""Trailing runtime-state message (context_packaging_v2 P4).

Per-iteration volatile context — the in-task memory snapshot, turn
scratchpad, eviction notices, delegated-work roster, peer-thread
coordination, live wall-clock — used to be appended to the SYSTEM prompt.
Every change (a saved memory, a scratchpad note, the clock ticking to the
next minute) rewrote the system prompt bytes and invalidated the provider's
prefix cache for the whole system+tools+history prefix: 114/274 iterations
in trace mucli_run_355d1b39c2a7 were cache misses (29M uncached input
tokens).

With ``prompt_prefix_stable`` (default on) those blocks are rendered into
ONE request-only user message appended AFTER history. The system prompt
stays byte-stable across the iterations of a turn; the volatile block sits
at the tail where a change only invalidates itself. The message is never
written to ``session.history``.
"""

from __future__ import annotations

from typing import Any, List

from providers.base import Message, MessagePart

RUNTIME_STATE_HEADER = "RUNTIME STATE (request-only, rebuilt every iteration; not part of the conversation):"


def _part_text(part: Any) -> Any:
    return part.get("text") if isinstance(part, dict) else getattr(part, "text", None)


def _is_runtime_state_part(part: Any) -> bool:
    text = _part_text(part)
    return isinstance(text, str) and text.startswith(RUNTIME_STATE_HEADER)


def is_runtime_state_message(message: Any) -> bool:
    """True when the message is (or carries, as its last part) the
    request-only runtime-state block. Typed ``Message`` or dict shape."""
    try:
        role = message.get("role") if isinstance(message, dict) else getattr(message, "role", None)
        parts = message.get("parts") if isinstance(message, dict) else getattr(message, "parts", None)
    except Exception:  # noqa: BLE001
        return False
    if role != "user" or not parts:
        return False
    return _is_runtime_state_part(parts[-1])


def runtime_state_text(message: Any) -> str:
    """The runtime-state block carried by ``message`` ('' when none)."""
    if not is_runtime_state_message(message):
        return ""
    parts = message.get("parts") if isinstance(message, dict) else message.parts
    return str(_part_text(parts[-1]) or "")


def render_runtime_state(blocks: List[str]) -> str:
    """Join the volatile layer blocks under the runtime-state header."""
    body = "\n\n".join(str(b).strip() for b in blocks if str(b or "").strip())
    if not body:
        return ""
    return f"{RUNTIME_STATE_HEADER}\n\n{body}"


def append_runtime_state(session: Any, messages: List[Message]) -> List[Message]:
    """Return ``messages`` with the session's current runtime-state block
    attached at the tail, exactly once.

    When the last wire message is already user-role (first iteration of a
    turn: the live user prompt), the block is appended as an extra text
    PART of a copy of that message — the user's own text and media stay
    first and "the last message is the user's message" keeps holding for
    providers and tests. Otherwise (tool-result tail) a separate trailing
    user message is added. No-op when the block is empty; idempotent across
    rebuilds of the wire messages (preflight compaction, hook rebuild).
    """
    block = str(getattr(session, "_runtime_state_block", "") or "").strip()
    if not block:
        return messages
    out = list(messages or [])
    new_part = MessagePart(type="text", text=block)
    if out and is_runtime_state_message(out[-1]):
        last = out[-1]
        out[-1] = Message(role="user", parts=list(last.parts[:-1]) + [new_part])
        return out
    if out and getattr(out[-1], "role", None) == "user":
        last = out[-1]
        out[-1] = Message(role="user", parts=list(last.parts) + [new_part])
        return out
    out.append(Message(role="user", parts=[new_part]))
    return out


def strip_runtime_state(messages: List[Message]) -> List[Message]:
    """Remove the runtime-state block (trailing message or trailing part)."""
    out = list(messages or [])
    if out and is_runtime_state_message(out[-1]):
        last = out[-1]
        rest = list(last.parts[:-1])
        if rest:
            out[-1] = Message(role="user", parts=rest)
        else:
            out.pop()
    return out


__all__ = [
    "RUNTIME_STATE_HEADER",
    "append_runtime_state",
    "is_runtime_state_message",
    "render_runtime_state",
    "runtime_state_text",
    "strip_runtime_state",
]
