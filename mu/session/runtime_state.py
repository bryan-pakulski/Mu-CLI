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

Turn context (prefix stability across turns). Blocks that are constant for a
turn but differ between turns — the durable recall, the bodies of skills
triggered by the user's message, a resumption briefing — would rewrite the
system prompt at every turn and turn the first request of each turn into a full
cache write. With ``prompt_prefix_stable`` they ride as one request-only part on
the turn's own user message instead: cached with the history for the rest of the
turn, and dropped at the next turn from a message the turn-end fold rewrites
anyway. The system prompt then only changes when L2/L3 legitimately do.
"""

from __future__ import annotations

from typing import Any, List

from providers.base import Message, MessagePart

RUNTIME_STATE_HEADER = "RUNTIME STATE (request-only, rebuilt every iteration; not part of the conversation):"
TURN_CONTEXT_HEADER = "TURN CONTEXT (request-only, for this turn; not part of the conversation):"


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


def render_turn_context(blocks: List[str]) -> str:
    """Join the turn-constant blocks under the turn-context header ('' when none)."""
    body = "\n\n".join(str(b).strip() for b in blocks if str(b or "").strip())
    if not body:
        return ""
    return f"{TURN_CONTEXT_HEADER}\n\n{body}"


def _is_turn_context_part(part: Any) -> bool:
    text = _part_text(part)
    return isinstance(text, str) and text.startswith(TURN_CONTEXT_HEADER)


def _own_texts(message: Any) -> tuple:
    parts = message.get("parts") if isinstance(message, dict) else getattr(message, "parts", None)
    out = []
    for part in parts or []:
        ptype = part.get("type") if isinstance(part, dict) else getattr(part, "type", None)
        if ptype != "text" or _is_runtime_state_part(part) or _is_turn_context_part(part):
            continue
        out.append(str(_part_text(part) or ""))
    return tuple(out)


def _turn_message_index(session: Any, messages: List[Message]) -> Any:
    """Index in ``messages`` of the current turn's user message (matched on its text
    parts against ``history[_current_turn_start_index]``), or None."""
    try:
        history = session.session_manager.history
        start = int(getattr(session, "_current_turn_start_index", -1))
        turn = history[start] if 0 <= start < len(history) else None
    except Exception:  # noqa: BLE001
        return None
    if not turn or (turn.get("role") if isinstance(turn, dict) else getattr(turn, "role", None)) != "user":
        return None
    want = _own_texts(turn)
    if not want:
        return None
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if getattr(msg, "role", None) == "user" and _own_texts(msg) == want:
            return i
    return None


def attach_turn_context(session: Any, messages: List[Message]) -> tuple:
    """Return (messages, leftover): ``messages`` with the session's turn-context block as
    an extra part of the turn's user message (after its own parts, before any runtime-state
    part), exactly once. ``leftover`` is the block when that message isn't in the request
    (it then rides in the runtime state instead), else ''."""
    out: List[Message] = []
    for msg in messages or []:
        parts = list(getattr(msg, "parts", None) or [])
        kept = [p for p in parts if not _is_turn_context_part(p)]
        out.append(msg if len(kept) == len(parts) else Message(role=msg.role, parts=kept))
    block = str(getattr(session, "_turn_context_block", "") or "").strip()
    if not block:
        return out, ""
    idx = _turn_message_index(session, out)
    if idx is None:
        return out, block
    parts = list(out[idx].parts)
    at = len(parts) - 1 if parts and _is_runtime_state_part(parts[-1]) else len(parts)
    parts.insert(at, MessagePart(type="text", text=block))
    out[idx] = Message(role="user", parts=parts)
    return out, ""


def append_runtime_state(session: Any, messages: List[Message]) -> List[Message]:
    """Return ``messages`` with the session's current runtime-state block
    attached at the tail, exactly once, and the turn context on the turn's user
    message (:func:`attach_turn_context`).

    When the last wire message is already user-role (first iteration of a
    turn: the live user prompt), the block is appended as an extra text
    PART of a copy of that message — the user's own text and media stay
    first and "the last message is the user's message" keeps holding for
    providers and tests. Otherwise (tool-result tail) a separate trailing
    user message is added. No-op when the block is empty; idempotent across
    rebuilds of the wire messages (preflight compaction, hook rebuild).
    """
    out, leftover = attach_turn_context(session, list(messages or []))
    block = str(getattr(session, "_runtime_state_block", "") or "").strip()
    if leftover:
        # The turn's message isn't in this request (rebuilt or compacted away): keep the
        # turn context with the runtime state rather than drop it.
        body = block[len(RUNTIME_STATE_HEADER):].strip() if block.startswith(RUNTIME_STATE_HEADER) else block
        block = render_runtime_state([leftover, body])
    if not block:
        return out
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
    "TURN_CONTEXT_HEADER",
    "attach_turn_context",
    "render_turn_context",
    "append_runtime_state",
    "is_runtime_state_message",
    "render_runtime_state",
    "runtime_state_text",
    "strip_runtime_state",
]
