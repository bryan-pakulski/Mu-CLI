"""Accurate token estimation across providers.

`tiktoken` is a hard requirement — install it with the rest of
`requirements.txt`. The naive `len(text) // 4` heuristic systematically
under-counts for code- and symbol-dense content, which causes
compaction to under-trim and triggers provider-side overflow rejections.

Encoder choice (cached per model on first use):

  * GPT / o-family → `tiktoken.encoding_for_model(model)` with
    `cl100k_base` fallback if the name is unknown.
  * Claude / Gemini / Ollama / unknown → `cl100k_base` (Anthropic's
    published guidance recommends this; for Gemini it tracks within
    roughly ±10–15%).
"""

from __future__ import annotations

import functools
import json
import threading
from typing import Any, Optional

import tiktoken


_LOCK = threading.Lock()
_ENCODERS: dict = {}


def _encoder_name_for_model(model: Optional[str]) -> str:
    if not model:
        return "cl100k_base"
    m = model.lower()
    if "gpt" in m or m.startswith("o1") or m.startswith("o3") or m.startswith("o4"):
        return ""  # signal: try encoding_for_model first
    return "cl100k_base"


def _get_encoder(model: Optional[str]):
    key = (model or "").lower()
    with _LOCK:
        cached = _ENCODERS.get(key)
    if cached is not None:
        return cached

    preferred = _encoder_name_for_model(model)
    encoder = None
    if preferred == "":
        try:
            encoder = tiktoken.encoding_for_model(model)
        except Exception:
            encoder = None
    if encoder is None:
        encoder = tiktoken.get_encoding(preferred or "cl100k_base")

    with _LOCK:
        _ENCODERS[key] = encoder
    return encoder


def estimate_tokens(text: Any, model: Optional[str] = None) -> int:
    """Estimate the token count of `text` for the given model.

    Non-string inputs are coerced via `str(...)`. Empty / falsy inputs
    return 0.
    """
    if not text:
        return 0
    if not isinstance(text, str):
        try:
            text = str(text)
        except Exception:
            return 0
    if len(text) >= _MEMO_MIN_CHARS:
        return _memo_count(text, (model or "").lower())
    encoder = _get_encoder(model)
    return len(encoder.encode(text, disallowed_special=()))


# The agent loop re-estimates the same large strings (history entries, system prompt,
# tool results) several times per iteration; BPE is the expensive part, a str hash is
# cached on the object. Small strings are cheaper to encode than to memoize.
_MEMO_MIN_CHARS = 2048


@functools.lru_cache(maxsize=4096)
def _memo_count(text: str, model_key: str) -> int:
    encoder = _get_encoder(model_key or None)
    return len(encoder.encode(text, disallowed_special=()))


def clear_cache() -> None:
    """Drop cached encoders. Used by tests; rarely useful otherwise."""
    with _LOCK:
        _ENCODERS.clear()
    _memo_count.cache_clear()


# ---------------------------------------------------------------- visible-content estimates
#
# Provider replay data is opaque to the model's context accounting: Anthropic thinking
# signatures (and redacted_thinking blobs), OpenAI Responses ``encrypted_content`` reasoning
# items and Gemini thought signatures all ride on ``MessagePart.thought_signature``. One can be
# tens of thousands of characters, but providers do not count earlier turns' replay data toward
# the context window (and mucli replays it for the current tool loop only). Counting it under
# cl100k inflated a ~210k-token job request to ~1M. Every request estimate therefore counts
# visible content only: text, tool-call arguments, tool results, the system prompt and the tool
# schemas, plus a fixed cost per media item (its bytes are not text the model reads).

#: Part fields that carry provider replay data and never count toward an estimate.
REPLAY_FIELDS = frozenset({"thought_signature"})

#: Tokens charged per inline image or media item (providers bill images by size, not bytes).
MEDIA_PART_TOKENS = 1_600


def _field(part: Any, name: str) -> Any:
    if isinstance(part, dict):
        return part.get(name)
    return getattr(part, name, None)


def estimate_part_tokens(part: Any, model: Optional[str] = None) -> int:
    """Visible tokens of one message part (a ``MessagePart`` or its dict / wire form)."""
    total = 0
    text = _field(part, "text")
    result = _field(part, "tool_result")
    args = _field(part, "tool_args")
    if text:
        total += estimate_tokens(text, model)
    elif result is not None:
        total += estimate_tokens(str(result), model)
    elif args is not None:
        try:
            total += estimate_tokens(json.dumps(args), model)
        except (TypeError, ValueError):
            total += estimate_tokens(json.dumps(args, default=str), model)
    elif _field(part, "image") or _field(part, "media") or _field(part, "inline_data"):
        total += MEDIA_PART_TOKENS
    media_inputs = _field(part, "media_inputs") or []
    if isinstance(media_inputs, (list, tuple)):
        total += MEDIA_PART_TOKENS * len(media_inputs)
    return total


def estimate_message_tokens(message: Any, model: Optional[str] = None) -> int:
    """Visible tokens of one message; replay data (``REPLAY_FIELDS``) is never counted."""
    parts = _field(message, "parts") or []
    return sum(estimate_part_tokens(part, model) for part in parts)


def estimate_messages_tokens(messages: Any, model: Optional[str] = None) -> int:
    return sum(estimate_message_tokens(message, model) for message in messages or [])


def tool_schema_text(tools: Any) -> str:
    """The tool definitions as an estimate counts them (name, description, JSON schema)."""
    payload = [
        {
            "name": _field(tool, "name") or "",
            "description": _field(tool, "description") or "",
            "parameters": _field(tool, "parameters") or {},
        }
        for tool in tools or []
    ]
    return json.dumps(payload, sort_keys=True, default=str) if payload else ""


def estimate_payload_tokens(
    system_prompt: Any = "",
    messages: Any = None,
    tools: Any = None,
    model: Optional[str] = None,
) -> int:
    """Visible-content estimate of a whole provider request: system prompt, messages and tool
    schemas. Replay blobs and media bytes are excluded (see ``REPLAY_FIELDS``)."""
    return (
        estimate_tokens(system_prompt or "", model)
        + estimate_messages_tokens(messages, model)
        + estimate_tokens(tool_schema_text(tools), model)
    )


def strip_replay_fields(history: Any, *, tool_results_only: bool = False) -> int:
    """Remove replay data from dict-shaped history in place; returns the parts changed.

    ``tool_results_only`` strips only ``tool_result`` parts, which never need it (older mucli
    builds copied the preceding call's signature onto every tool result)."""
    changed = 0
    for message in history or []:
        if not isinstance(message, dict):
            continue
        for part in message.get("parts") or []:
            if not isinstance(part, dict):
                continue
            if tool_results_only and part.get("type") != "tool_result":
                continue
            for name in REPLAY_FIELDS:
                if name in part:
                    part.pop(name, None)
                    changed += 1
    return changed


__all__ = [
    "MEDIA_PART_TOKENS",
    "REPLAY_FIELDS",
    "clear_cache",
    "estimate_message_tokens",
    "estimate_messages_tokens",
    "estimate_part_tokens",
    "estimate_payload_tokens",
    "estimate_tokens",
    "strip_replay_fields",
    "tool_schema_text",
]
