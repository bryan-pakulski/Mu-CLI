"""Auto-compaction: fire at `pre_provider_call` when history approaches
the context window.

This wraps the existing `SessionManager.roll_history_summary_to_token_budget`
algorithm — the algorithm is correct and tested, the only thing missing
is a hook-based trigger so the loop fires it automatically when the
estimated history size crosses a threshold.

Threshold is configurable via `session.variables["context_trim_threshold"]`
(default 0.85). When the estimated history token count exceeds
`context_token_limit * threshold`, the compactor invokes the existing
roll path with `keep_recent=4`.
"""

from __future__ import annotations

import logging
from typing import Optional

from .hooks import HookContext, HookRegistry, HookResult, HookSpec, default_registry


logger = logging.getLogger("mucli")

from utils.config import _DEFAULT_CONTEXT_TOKEN_LIMIT


def _compact_history(ctx: HookContext) -> Optional[HookResult]:
    # `run_turn` calls `roll_history_summary_to_token_budget()` once
    # before entering its iteration loop. Within a single turn we
    # therefore want exactly one auto-compaction pass — suppress
    # this hook when the session already rolled this turn.
    session = ctx.session
    if session is None:
        return None
    session_manager = getattr(session, "session_manager", None)
    if session_manager is None or not hasattr(
        session_manager, "roll_history_summary_to_token_budget"
    ):
        return None

    # Once-per-turn proactive-compaction gate (Claude Code fires autocompact
    # once per turn at the boundary; mid-turn overshoot is handled by the
    # emergency preflight + reactive-overflow backstops, not by re-firing the
    # proactive pass after every tool call). The turn-start roll sets this
    # flag when it actually compacts; this hook sets it on its own first
    # compaction. Either way: at most one proactive compaction per turn —
    # reset to False in `_collect_turn_response`. Without this the hook fired
    # on every `pre_provider_call`, and a turn with N tool calls compacted up
    # to N times.
    if getattr(session, "_compacted_this_turn", False):
        return None

    # Re-compaction gate: allow when history has grown since the last
    # compaction pass.  The previous boolean flag suppressed ALL
    # re-compaction within a turn, which meant long turns with many tool
    # calls grew history unbounded until emergency compaction fired.
    watermark = getattr(session, "_compaction_watermark", 0)
    history_len = len(getattr(session_manager, "history", []))
    if history_len <= watermark:
        return None

    variables = getattr(session, "variables", None) or ctx.variables or {}
    try:
        threshold = float(variables.get("context_trim_threshold", 0.85) or 0.85)
    except (TypeError, ValueError):
        return None
    threshold = max(0.10, min(threshold, 1.0))
    # Use the provider-aware compaction budget when available — it
    # accounts for the actual model context window, not just the
    # user-set harness default.  Falls back to the raw variable for
    # sessions that don't expose _compaction_token_budget.
    if hasattr(session, "_compaction_token_budget"):
        # `compaction_token_budget()` already applies `context_trim_threshold`
        # internally (see mu/session/budgets.py: `usable * trim_threshold`).
        # Do NOT multiply by `threshold` again here — the prior `* threshold`
        # double-applied it, collapsing the target from 85% to ~72% of the
        # residual window and triggering compaction far too often.
        budget = int(session._compaction_token_budget())
    else:
        try:
            context_limit = max(
                1024,
                int(
                    variables.get(
                        "context_token_limit", _DEFAULT_CONTEXT_TOKEN_LIMIT
                    )
                    or _DEFAULT_CONTEXT_TOKEN_LIMIT
                ),
            )
        except (TypeError, ValueError):
            return None
        budget = int(context_limit * threshold)

    try:
        from mu.session.budgets import resolve_keep_recent, resolve_tool_result_floor

        session_manager._tool_result_floor = resolve_tool_result_floor(session)
        # Bridge the optional compact_focus variable (Claude Code
        # `/compact <focus>` style) so the LLM summarizer emphasizes it.
        session_manager._compact_focus = (
            getattr(session, "variables", None) or {}
        ).get("compact_focus") or ""
        # Tag this compaction for the run tracer (drained into the trace at the
        # post-response seam). `iter` comes from the loop's current-iter marker.
        session_manager._pending_compaction_kind = "auto_hook"
        session_manager._pending_compaction_iter = int(
            getattr(session, "_trace_current_iter", 0) or 0
        )
        rolled = session_manager.roll_history_summary_to_token_budget(
            budget,
            keep_recent=resolve_keep_recent(session),
            provider=getattr(session, "provider", None),
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Auto-compaction raised %s; continuing without compacting", exc)
        return None
    if rolled:
        # Mark this turn's proactive compaction as done so the hook (and the
        # turn-start roll next turn) don't fire again, and re-baseline the
        # watermark to the post-compaction history length.
        session._compacted_this_turn = True
        session._compaction_watermark = len(session_manager.history)
        logger.info(
            "Auto-compaction triggered (budget=%d tokens, threshold=%.2f).",
            budget,
            threshold,
        )
        return HookResult(action="continue", data={"compaction": True, "budget": budget})
    return None


def install(registry: Optional[HookRegistry] = None) -> None:
    reg = registry or default_registry
    reg.remove("auto_compact_pre_call")
    reg.add(
        HookSpec(
            name="auto_compact_pre_call",
            point="pre_provider_call",
            priority=50,
            handler=_compact_history,
        )
    )


install()


__all__ = ["install"]
