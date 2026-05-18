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
    encoder = _get_encoder(model)
    return len(encoder.encode(text, disallowed_special=()))


def clear_cache() -> None:
    """Drop cached encoders. Used by tests; rarely useful otherwise."""
    with _LOCK:
        _ENCODERS.clear()


__all__ = ["estimate_tokens", "clear_cache"]
