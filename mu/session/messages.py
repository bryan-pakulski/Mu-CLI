"""History → provider-message serialization helpers.

Four helpers, all consumed by the agent loop just before / after the
provider call:

  * `build_messages_from_history(recent, new_user)` — rehydrate the
    dict-shaped history records into the strongly-typed `Message` /
    `MessagePart` / `FileReference` / `ImageData` graph that providers
    accept. Handles text / file / image_input / tool_call / tool_result
    parts.

  * `prepare_runtime_history(session, turn_start_index)` — compute
    which slice of `session.session_manager.history` should be sent
    this turn. Walks backwards from the tail, summing per-message
    tokens, until the budget from `compaction_token_budget` is hit.
    Then, for the current turn, compresses older `assistant`/`tool`
    message pairs into a single summary block when the
    `tool_context_window` is exceeded.

  * `summarize_message_parts(msg_dict)` — render one history entry as
    a single-line summary used by `prepare_runtime_history` when
    compressing old tool activity.

  * `clip_preview(text, limit)` — shorten a string with an ellipsis
    when it exceeds `limit` chars. Used in tool-result previews and
    history summaries.

`message_has_thought_signature(msg)` is a small predicate (kept here
because `prepare_runtime_history` consults it) — messages carrying a
provider-supplied thought signature must never be compressed, since
the provider rejects subsequent calls that try to continue without
the original signature attached.

Tests: `tests/test_session.py` (history compression, ordering, image
rehydration), `tests/test_vision_e2e.py` (image_input round-trip),
`tests/test_mu_session_history.py` (token estimation pinning).
"""

from __future__ import annotations

import base64
from typing import Any, List, Optional

from providers.base import FileReference, ImageData, Message, MessagePart

from .helpers import _shorten_tool_args


def build_messages_from_history(
    recent_history_dicts: List[dict],
    new_user_message_dict: dict,
) -> List[Message]:
    """Rehydrate dict-shaped history records into provider-typed
    `Message` objects. Pass-through for text; decodes base64 image
    payloads back into `ImageData`; threads provider-supplied
    `thought_signature` through tool_call / tool_result parts."""
    messages: List[Message] = []
    for msg_dict in recent_history_dicts + [new_user_message_dict]:
        parts: List[MessagePart] = []
        for p in msg_dict.get("parts", []):
            p_type = p.get("type")
            if p_type == "text":
                parts.append(MessagePart(type="text", text=p["text"]))
            elif p_type == "file":
                fr_data = p.get("file_ref", {})
                parts.append(
                    MessagePart(
                        type="file",
                        file_ref=FileReference(
                            uri=fr_data.get("uri"),
                            mime_type=fr_data.get("mime_type"),
                            display_name=fr_data.get("display_name"),
                        ),
                    )
                )
            elif p_type == "image_input":
                img_data = p.get("image", {}) or {}
                raw = img_data.get("data_b64") or ""
                try:
                    decoded = base64.b64decode(raw) if raw else b""
                except Exception:
                    decoded = b""
                if decoded:
                    parts.append(
                        MessagePart(
                            type="image_input",
                            image=ImageData(
                                data=decoded,
                                mime_type=img_data.get("mime_type", "image/png"),
                                source=img_data.get("source"),
                            ),
                        )
                    )
            elif p_type == "tool_call":
                parts.append(
                    MessagePart(
                        type="tool_call",
                        tool_name=p["tool_name"],
                        tool_args=p.get("tool_args", {}),
                        thought_signature=p.get("thought_signature"),
                    )
                )
            elif p_type == "tool_result":
                parts.append(
                    MessagePart(
                        type="tool_result",
                        tool_name=p.get("tool_name", "tool"),
                        tool_result=p.get("tool_result", ""),
                        thought_signature=p.get("thought_signature"),
                    )
                )
        messages.append(Message(role=msg_dict["role"], parts=parts))
    return messages


def message_has_thought_signature(msg_dict: dict) -> bool:
    """True if any part of `msg_dict` carries a `thought_signature`
    (provider-supplied reasoning checksum). Such messages must not be
    compressed away or summarized."""
    for part in msg_dict.get("parts", []):
        if part.get("thought_signature"):
            return True
    return False


def clip_preview(text: Any, limit: int = 240) -> str:
    """Trim a string to `limit` chars, appending an ellipsis when
    truncated. Stripping leading/trailing whitespace first."""
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def summarize_message_parts(msg_dict: dict) -> str:
    """Render one history entry as a single-line summary for
    compressed-history blocks. Returns `- <role>: <summaries>` or
    `- <role>: [no serializable content]`."""
    role = msg_dict.get("role", "message")
    summaries: List[str] = []
    for part in msg_dict.get("parts", []):
        p_type = part.get("type")
        if p_type == "text":
            text = str(part.get("text", "")).strip().replace("\n", " ")
            if text:
                summaries.append(text[:120])
        elif p_type == "tool_call":
            summaries.append(
                f"tool_call:{part.get('tool_name')} "
                f"args={_shorten_tool_args(part.get('tool_args', {}))}"
            )
        elif p_type == "tool_result":
            raw_result = part.get("tool_result", "")
            if isinstance(raw_result, dict):
                result = str(
                    raw_result.get("summary") or raw_result.get("raw", "")
                )
            else:
                result = str(raw_result)
            result = result.strip().replace("\n", " ")
            if len(result) > 140:
                result = f"{result[:137]}..."
            summaries.append(
                f"tool_result:{part.get('tool_name')} => {result}"
            )
        elif p_type == "file":
            fr = part.get("file_ref", {})
            summaries.append(
                f"file:{fr.get('display_name', fr.get('uri', 'unknown'))}"
            )
        elif p_type == "image_input":
            img = part.get("image", {}) or {}
            source = img.get("source") or img.get("mime_type", "image")
            summaries.append(f"image:{source}")

    if not summaries:
        return f"- {role}: [no serializable content]"
    return f"- {role}: " + " | ".join(summaries)


def prepare_runtime_history(
    session: Any,
    turn_start_index: Optional[int] = None,
) -> List[dict]:
    """Pick the slice of `session.session_manager.history` to send to
    the provider this turn, then (within the current-turn region)
    compress older `assistant`/`tool` message pairs into a single
    LAYER 4 summary block when the `tool_context_window` is exceeded.

    Skips compression for any message carrying a thought signature —
    those must round-trip verbatim or the provider rejects subsequent
    calls."""
    session_manager = session.session_manager
    if session_manager.summary_anchor > len(session_manager.history):
        session_manager.summary_anchor = 0
    token_budget = session._compaction_token_budget()
    start_index = len(session_manager.history)
    running_tokens = 0
    while start_index > session_manager.summary_anchor:
        next_index = start_index - 1
        next_tokens = session_manager._estimate_message_tokens(
            session_manager.history[next_index]
        )
        if (
            running_tokens + next_tokens > token_budget
            and next_index < len(session_manager.history) - 1
        ):
            break
        running_tokens += next_tokens
        start_index = next_index
    recent_history = session_manager.history[start_index:]
    tool_window = max(0, int(session.variables.get("tool_context_window", 6)))

    if turn_start_index is None:
        return recent_history

    start_in_recent = max(0, turn_start_index - start_index)
    prefix = recent_history[:start_in_recent]
    current_turn = recent_history[start_in_recent:]

    tool_messages = [
        msg for msg in current_turn if msg.get("role") in {"assistant", "tool"}
    ]
    if len(tool_messages) <= tool_window:
        return recent_history

    compressible_tool_messages = [
        msg for msg in tool_messages if not message_has_thought_signature(msg)
    ]
    if len(compressible_tool_messages) <= tool_window:
        return recent_history

    keep_start = len(compressible_tool_messages) - tool_window
    compressed_tool_count = 0
    summarized_lines: List[str] = []
    compressed_turn: List[dict] = []

    for msg in current_turn:
        if msg.get("role") in {"assistant", "tool"}:
            if message_has_thought_signature(msg):
                compressed_turn.append(msg)
                continue
            if compressed_tool_count < keep_start:
                summarized_lines.append(summarize_message_parts(msg))
                compressed_tool_count += 1
                continue
            compressed_tool_count += 1
        compressed_turn.append(msg)

    if summarized_lines:
        summary_text = (
            "LAYER 4 — Recent tool activity (compressed for budget).\n"
            "Older tool call/result pairs from this turn were summarized.\n"
            + "\n".join(summarized_lines)
        )
        compressed_turn.insert(
            (
                1
                if compressed_turn and compressed_turn[0].get("role") == "user"
                else 0
            ),
            {
                "role": "system",
                "parts": [{"type": "text", "text": summary_text}],
            },
        )

    return prefix + compressed_turn


__all__ = [
    "build_messages_from_history",
    "message_has_thought_signature",
    "clip_preview",
    "summarize_message_parts",
    "prepare_runtime_history",
]
