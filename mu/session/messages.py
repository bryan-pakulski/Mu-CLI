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

from providers.base import FileReference, ImageData, LLMProvider, Message, MessagePart

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


def summarize_message_parts(
    msg_dict: dict,
    provider: Optional[LLMProvider] = None,
) -> str:
    """Render one history entry as a single-line summary for
    compressed-history blocks. Returns `- <role>: <summaries>` or
    `- <role>: [no serializable content]`.

    Char limits expanded from 120/140 to 500 to preserve more context
    in the mechanical fallback path. The LLM summarization path
    (_llm_summarize_tool_batch) is preferred when a provider is
    available — see prepare_runtime_history."""
    role = msg_dict.get("role", "message")
    summaries: List[str] = []
    for part in msg_dict.get("parts", []):
        p_type = part.get("type")
        if p_type == "text":
            text = str(part.get("text", "")).strip().replace("\n", " ")
            if text:
                summaries.append(text[:500])
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
            # Include cache key tag if present so model can recall full result
            cache_key = part.get("cache_key")
            cache_tag = f"[cache:{cache_key}] " if cache_key else ""
            if len(result) > 500:
                result = f"{result[:497]}..."
            summaries.append(
                f"tool_result:{part.get('tool_name')} => {cache_tag}{result}"
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


_LLM_TOOL_SUMMARY_SYSTEM_PROMPT = (
    "You are a tool-activity summarizer for an AI coding agent. "
    "Summarize the following tool call/result pairs into a concise "
    "but information-preserving summary. Keep file paths, function "
    "names, error messages, and key findings VERBATIM. Output a "
    "brief structured summary, not a narrative.\n\n"
    "Format:\n"
    "- <tool_name>: <key result or finding> (preserve paths/identifiers)\n\n"
    "Rules:\n"
    "- Preserve file paths, function names, and error messages verbatim.\n"
    "- Include cache key tags like [cache:KEY] so the agent can recall "
    "full results.\n"
    "- Be concise but complete. Target 100-300 words.\n"
    "- Do NOT add commentary or headers.\n"
)


def _llm_summarize_tool_batch(
    provider: Optional[LLMProvider],
    entries: List[dict],
) -> Optional[str]:
    """Generate an LLM summary of tool call/result pairs for L4 compression.

    Returns structured summary text, or None on any failure (caller
    falls back to mechanical summarize_message_parts).
    """
    if provider is None or not entries:
        return None

    try:
        rendered = "\n".join(summarize_message_parts(e) for e in entries)
        if not rendered.strip():
            return None

        messages = [
            Message(
                role="user",
                parts=[MessagePart(type="text", text=f"Summarize these tool activities:\n\n{rendered}")],
            ),
        ]

        response = provider.generate(
            messages=messages,
            system_prompt=_LLM_TOOL_SUMMARY_SYSTEM_PROMPT,
            thinking=False,
            tools=None,
        )

        summary_text = str(response.text or "").strip()
        if not summary_text:
            return None

        return summary_text

    except Exception:
        return None


# ── R4 / FM-2: first-class huge-message handling ────────────────────────
# When a single message (typically the user's turn prompt — a massive
# paste) alone consumes most of the L5 budget, the backward walk in
# `prepare_runtime_history` is forced to include it (the newest message
# is always kept), which overflows the provider window and triggers
# destructive mechanical truncation in `_degrade_oldest_runtime_payload`.
# Instead we chunk-summarize the oversized text via the provider (or a
# head+tail mechanical fallback when no provider is available) and
# substitute a labeled CONTEXT-OVERFLOW envelope so the model knows the
# full original is not in context.

_OVERFLOW_CHUNK_CHARS = 12_000  # ~3000 tokens per chunk
_OVERFLOW_MAX_CHUNKS = 12  # bound work on pathological inputs


def _chunk_summarize_text(
    provider: Optional[LLMProvider],
    text: str,
    budget_tokens: int,
) -> str:
    """Summarize an oversized text in chunks. Uses the provider when
    available (one generate() call per chunk, capped at
    `_OVERFLOW_MAX_CHUNKS`); falls back to a head+tail mechanical elision
    when no provider is present or a chunk call fails."""
    if not text:
        return ""
    if provider is None:
        return _mechanical_elide(text, budget_tokens)
    chunks = [
        text[i : i + _OVERFLOW_CHUNK_CHARS]
        for i in range(0, len(text), _OVERFLOW_CHUNK_CHARS)
    ][:_OVERFLOW_MAX_CHUNKS]
    system = (
        "You are a context summarizer for an AI coding agent. Summarize the "
        "following chunk concisely while preserving file paths, function "
        "names, error messages, and key findings VERBATIM. Be concise but "
        "complete. Output ONLY the summary, no headers or commentary."
    )
    summaries: List[str] = []
    for chunk in chunks:
        try:
            resp = provider.generate(
                messages=[
                    Message(
                        role="user",
                        parts=[MessagePart(type="text", text=chunk)],
                    )
                ],
                system_prompt=system,
                thinking=False,
                tools=None,
            )
            s = str(resp.text or "").strip()
            summaries.append(s if s else _mechanical_elide(chunk, budget_tokens))
        except Exception:
            summaries.append(_mechanical_elide(chunk, budget_tokens))
    if len(text) > _OVERFLOW_CHUNK_CHARS * _OVERFLOW_MAX_CHUNKS:
        elided = len(text) - _OVERFLOW_CHUNK_CHARS * _OVERFLOW_MAX_CHUNKS
        summaries.append(f"[...{elided} additional chars not summarized...]")
    return "\n\n".join(s for s in summaries if s).strip()


def _mechanical_elide(text: str, budget_tokens: int) -> str:
    """Head+tail elision fallback (no provider). Keeps a head and tail
    sized to roughly fit the budget (~4 chars/token)."""
    cap = max(4000, int(budget_tokens * 4))
    if len(text) <= cap:
        return text
    half = cap // 2
    return (
        text[:half]
        + f"\n[...{len(text) - cap} chars elided (no provider available "
        "for chunked summary)...]\n"
        + text[-half:]
    )


def _maybe_summarize_oversized(
    session: Any,
    abs_idx: int,
    msg: dict,
    budget_tokens: int,
    provider: Optional[LLMProvider],
    cache: dict,
) -> dict:
    """If `msg`'s text content alone exceeds the overflow threshold,
    substitute a chunk-summarized CONTEXT-OVERFLOW envelope. Returns the
    original message unchanged when it is not oversized. Summaries are
    cached per absolute history index so repeated `prepare_runtime_history`
    calls within a turn don't re-summarize."""
    if budget_tokens <= 0 or not isinstance(msg, dict):
        return msg
    text = None
    for p in msg.get("parts", []) or []:
        if p.get("type") == "text":
            text = p.get("text") or ""
            break
    if not text:
        return msg
    try:
        text_tokens = session.session_manager._estimate_tokens_from_text(text)
    except Exception:
        text_tokens = len(text) // 4
    # Threshold: a single message consuming more than 60% of the L5
    # budget AND non-trivially large. Below this, normal compaction
    # handles it; above it, proactive chunked summary avoids overflow.
    threshold = max(2000, int(budget_tokens * 0.6))
    if text_tokens <= threshold:
        return msg
    if abs_idx in cache:
        summarized = cache[abs_idx]
    else:
        summarized = _chunk_summarize_text(provider, text, budget_tokens)
        cache[abs_idx] = summarized
    envelope = (
        f"[CONTEXT-OVERFLOW — this message exceeded the context budget "
        f"(~{text_tokens} tokens) and was summarized in chunks. The full "
        f"original is NOT in context; ask the user to re-paste a specific "
        f"section if you need verbatim detail.]\n\n{summarized}"
    )
    new_parts = []
    replaced = False
    for p in msg.get("parts", []) or []:
        if (not replaced) and p.get("type") == "text" and (p.get("text") or "") == text:
            new_parts.append({"type": "text", "text": envelope})
            replaced = True
        else:
            new_parts.append(p)
    return {**msg, "parts": new_parts}


def prepare_runtime_history(
    session: Any,
    turn_start_index: Optional[int] = None,
    provider: Optional[LLMProvider] = None,
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
    # R4 / FM-2: if any single message in the runtime slice is oversized
    # relative to the L5 budget, substitute a chunk-summarized
    # CONTEXT-OVERFLOW envelope BEFORE assembling recent_history. Cached
    # per absolute history index on the session so repeated calls within
    # a turn (the loop calls this every iteration) don't re-summarize.
    raw_slice = session_manager.history[start_index:]
    cache = getattr(session, "_oversized_message_summaries", None)
    if cache is None:
        cache = {}
        session._oversized_message_summaries = cache
    runtime_slice = [
        _maybe_summarize_oversized(
            session, start_index + i, msg, token_budget, provider, cache
        )
        for i, msg in enumerate(raw_slice)
    ]
    # Inject protected messages that are below the summary anchor back
    # into the runtime history.  These messages were excluded from LLM
    # summarisation in roll_history_summary() and must appear verbatim
    # in L5 so the model retains the original user request and key
    # decisions even after compaction has advanced the anchor past them.
    protected = getattr(session_manager, "protected_indices", set())
    if protected:
        protected_below_anchor = [
            session_manager.history[idx]
            for idx in sorted(protected)
            if idx < start_index
        ]
        if protected_below_anchor:
            # Wrap protected messages in a labelled envelope so the model
            # understands these are intentionally preserved, not stale or
            # duplicated — they were excluded from L2 summarisation and
            # re-injected verbatim to keep important context (original user
            # request, key decisions) alive through compaction.
            preserved_marker = {
                "role": "user",
                "parts": [{
                    "type": "text",
                    "text": (
                        "[PRESERVED CONTEXT — These messages are kept verbatim "
                        "and protected from summarisation. They are NOT stale "
                        "or duplicated; they are intentionally preserved to "
                        "maintain important context through compaction.]"
                    ),
                }],
            }
            recent_history = (
                [preserved_marker] + protected_below_anchor + runtime_slice
            )
        else:
            recent_history = runtime_slice
    else:
        recent_history = runtime_slice
    tool_window = max(0, int(session.variables.get("tool_context_window", 6)))

    if turn_start_index is None:
        return recent_history

    start_in_recent = max(0, turn_start_index - start_index)
    prefix = recent_history[:start_in_recent]
    current_turn = recent_history[start_in_recent:]

    # Group current-turn messages into atomic compression units.
    # An assistant message containing tool_call(s) and its immediately
    # following tool message containing tool_result(s) form an
    # indivisible pair.  Compressing one without the other splits
    # tool_call from tool_result, which causes provider errors on the
    # next turn — the API requires every tool_call to have a matching
    # tool_result.
    groups: list[tuple[bool, list[dict]]] = []  # (is_tool_pair, msgs)
    i = 0
    while i < len(current_turn):
        msg = current_turn[i]
        if (
            msg.get("role") == "assistant"
            and i + 1 < len(current_turn)
            and current_turn[i + 1].get("role") == "tool"
            and not message_has_thought_signature(msg)
            and not message_has_thought_signature(current_turn[i + 1])
        ):
            groups.append((True, [msg, current_turn[i + 1]]))
            i += 2
            continue
        groups.append((False, [msg]))
        i += 1

    tool_pairs = [g for g in groups if g[0]]
    # tool_window counts individual messages; each pair = 2 messages.
    total_tool_msgs = sum(len(msgs) for _, msgs in tool_pairs)
    if total_tool_msgs <= tool_window:
        return recent_history

    # Keep enough complete pairs to cover at least tool_window individual
    # messages.  Rounding up avoids splitting a pair when tool_window is odd.
    keep_pair_count = -(-tool_window // 2)  # ceil division
    compress_pair_count = len(tool_pairs) - keep_pair_count
    pair_count = 0
    summarized_lines: List[str] = []
    # The exact messages handed to the LLM summarizer. MUST stay in sync
    # with `summarized_lines` (the mechanical fallback) — both must cover
    # the SAME set of compressed pairs, otherwise a pair can be dropped
    # from `compressed_turn` (via `continue` below) yet never reach the
    # LLM summarizer, losing its tool result entirely. See R1 in
    # documentation/harness-investigation.md (FM-1).
    summarized_pairs_msgs: List[dict] = []
    compressed_turn: List[dict] = []

    for is_pair, msgs in groups:
        if is_pair:
            if pair_count < compress_pair_count:
                for m in msgs:
                    summarized_lines.append(summarize_message_parts(m, provider=provider))
                summarized_pairs_msgs.extend(msgs)
                pair_count += 1
                continue
            pair_count += 1
        compressed_turn.extend(msgs)

    if summarized_lines:
        # Try LLM summarization first (Claude-style); fall back to
        # the expanded mechanical lines (500 chars per part, not 120).
        # `summarized_pairs_msgs` is the exact set the mechanical loop
        # compressed, so the LLM summary and the fallback cover the same
        # pairs — no tool result is silently dropped.
        llm_summary = _llm_summarize_tool_batch(
            provider,
            summarized_pairs_msgs,
        )
        if llm_summary is not None:
            summary_text = (
                "LAYER 4 — Recent tool activity (LLM-summarized for budget).\n"
                "Older tool call/result pairs from this turn were summarized.\n"
                + llm_summary
            )
        else:
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
    "_llm_summarize_tool_batch",
]
