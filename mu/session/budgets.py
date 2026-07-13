"""Token-budget arithmetic for the session compactor.

The three helpers here decide how aggressively the conversation
history gets compacted before each provider call:

  * `resolve_context_limit(session)`      — total token ceiling, min of
                                            user-set + provider window.
  * `resolve_response_reserve(session)`   — tokens to leave free for
                                            the model's reply.
  * `compaction_token_budget(session)`    — L5 (history) budget,
                                            = (ceiling − response reserve
                                              − non-L5 layers) × trim threshold.

The Ollama "prompt too long; exceeded max context length" bug that
drove `resolve_context_limit` is regression-pinned in
`tests/test_context_budget.py`. End-to-end coverage in
`tests/test_compaction_e2e.py`.

These functions take a `session` argument because they need access to
`session.provider` (for `effective_context_window` /
`effective_response_reserve`) and `session.variables` (for the
user-configurable knobs). They don't mutate the session.
"""

from __future__ import annotations

from typing import Any

from utils.config import _DEFAULT_CONTEXT_TOKEN_LIMIT


# ── Compaction keep-recent policy (R3, FM-8) ─────────────────────────────
# Single source of truth for how many trailing messages the compactor
# keeps verbatim. Previously these were inline magic numbers scattered
# across the call sites (12 / 12 / 2), which made the relationship
# between normal roll, auto-compaction, and emergency compaction opaque.
KEEP_RECENT_DEFAULT = 12
KEEP_RECENT_EMERGENCY = 2
# Per-turn tool-result floor: the last K tool-result messages of the
# active turn are never summarized or degraded, even under emergency
# compaction with a tiny keep_recent. Prevents the "compaction mid-turn
# drops tool results just received" failure mode (FM-8) at the cost of
# slightly higher steady-state token usage.
TOOL_RESULT_FLOOR_DEFAULT = 4


def resolve_keep_recent(session: Any, *, emergency: bool = False) -> int:
    """How many trailing messages the compactor keeps verbatim.

    Normal/auto compaction uses `compactor_keep_recent` (default
    `KEEP_RECENT_DEFAULT`). Emergency compaction (pre-flight context
    check) uses `emergency_keep_recent` (default `KEEP_RECENT_EMERGENCY`)
    — smaller so it can reclaim budget fast, but the tool-result floor
    (`resolve_tool_result_floor`) still protects recent tool results
    regardless of this value.
    """
    if emergency:
        raw = session.variables.get("emergency_keep_recent", KEEP_RECENT_EMERGENCY)
    else:
        raw = session.variables.get("compactor_keep_recent", KEEP_RECENT_DEFAULT)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return KEEP_RECENT_EMERGENCY if emergency else KEEP_RECENT_DEFAULT


def resolve_tool_result_floor(session: Any) -> int:
    """Number of trailing tool-result messages in the active turn that
    compaction must leave verbatim (R3, FM-8)."""
    raw = session.variables.get("tool_result_floor", TOOL_RESULT_FLOOR_DEFAULT)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return TOOL_RESULT_FLOOR_DEFAULT


def resolve_context_limit(session: Any) -> int:
    """Pick the smaller of (user-set `context_token_limit`, real
    provider window). Ollama models often have 4k–32k real windows
    while the user-set default is 256k, so without this the compactor
    never fires before the provider 400s with "prompt too long".
    """
    user_limit = max(
        1024,
        int(
            session.variables.get(
                "context_token_limit", _DEFAULT_CONTEXT_TOKEN_LIMIT
            )
            or _DEFAULT_CONTEXT_TOKEN_LIMIT
        ),
    )
    try:
        provider_window = session.provider.effective_context_window(
            session.provider.model_name
        )
    except Exception:
        provider_window = None
    if provider_window and provider_window > 0:
        return min(user_limit, int(provider_window))
    return user_limit


def resolve_response_reserve(session: Any) -> int:
    """How many tokens to leave free for the model's output.

    Preferred source is the provider's `effective_response_reserve()`
    — which reads `ollama_num_predict` / `max_tokens` / etc. — so the
    reserve tracks the actual configured output cap instead of a
    guessed constant. Only falls back to the `response_token_reserve`
    session variable when the provider has no configured cap.
    """
    try:
        provider_reserve = session.provider.effective_response_reserve(
            session.provider.model_name
        )
    except Exception:
        provider_reserve = None
    if provider_reserve and provider_reserve > 0:
        return int(provider_reserve)
    raw = session.variables.get("response_token_reserve", 4096)
    try:
        return max(0, int(raw)) if raw is not None else 4096
    except (TypeError, ValueError):
        return 4096


def compaction_token_budget(session: Any) -> int:
    """The token ceiling the compactor targets for L5 (conversation
    history) specifically.

    The global cap (`context_token_limit`, or the provider's actual
    window when smaller) covers all 7 prompt layers PLUS the model's
    response reserve. L5 gets whatever the cap minus the non-L5
    layers (workspace files, skills, summary, goal context, recent
    tool activity, retrieval snippets) leaves room for, with the
    trim threshold applied to that residual.

    Computing the non-L5 layer tokens here means a heavy AGENTS.md or
    many auto-expanded skills tighten the compactor's threshold
    instead of being silently piled on top of the L5 budget.
    """
    context_limit = resolve_context_limit(session)
    trim_threshold = float(
        session.variables.get("context_trim_threshold", 0.85) or 0.85
    )
    trim_threshold = max(0.10, min(trim_threshold, 1.0))
    response_reserve = resolve_response_reserve(session)

    non_l5_tokens = 0
    try:
        from utils.runtime_metrics import estimate_non_l5_context_tokens

        non_l5_tokens = int(estimate_non_l5_context_tokens(session) or 0)
    except Exception:
        non_l5_tokens = 0

    usable = max(1024, context_limit - response_reserve - non_l5_tokens)
    return max(512, int(usable * trim_threshold))


__all__ = [
    "resolve_context_limit",
    "resolve_response_reserve",
    "compaction_token_budget",
    "resolve_keep_recent",
    "resolve_tool_result_floor",
    "KEEP_RECENT_DEFAULT",
    "KEEP_RECENT_EMERGENCY",
    "TOOL_RESULT_FLOOR_DEFAULT",
]
