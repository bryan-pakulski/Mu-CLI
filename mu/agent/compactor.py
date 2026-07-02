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
        budget = int(session._compaction_token_budget() * threshold)
    else:
        try:
            context_limit = max(
                1024, int(variables.get("context_token_limit", 256000) or 256000)
            )
        except (TypeError, ValueError):
            return None
        budget = int(context_limit * threshold)

    try:
        rolled = session_manager.roll_history_summary_to_token_budget(
            budget,
            keep_recent=int(variables.get("compactor_keep_recent", 12) or 12),
            provider=getattr(session, "provider", None),
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Auto-compaction raised %s; continuing without compacting", exc)
        return None
    if rolled:
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
