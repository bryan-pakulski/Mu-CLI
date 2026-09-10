"""Keep a bounded working history throughout long-running agent turns.

The provider window is a safety ceiling, not a working-set target. Roll
older activity into the summary at the smaller of the provider-aware L5
budget and the configured working-history limit, then aim for half that
budget so another small tool result does not immediately trigger a roll.
"""

from __future__ import annotations

import logging
from typing import Optional

from .hooks import HookContext, HookRegistry, HookResult, HookSpec, default_registry


logger = logging.getLogger("mucli")

from utils.config import _DEFAULT_CONTEXT_TOKEN_LIMIT


def _compact_history(ctx: HookContext, *, kind: str = "auto_hook") -> Optional[HookResult]:
    session = ctx.session
    if session is None:
        return None
    variables = getattr(session, "variables", None) or ctx.variables or {}
    session_manager = getattr(session, "session_manager", None)
    if session_manager is None or not hasattr(
        session_manager, "roll_history_summary_to_token_budget"
    ):
        return None
    # Respect saved opt-outs; emergency provider-window recovery remains on.
    if not variables.get("auto_compaction_enabled", True):
        return None

    # Retry/no-progress cooldown, including when protected recent results
    # alone exceed the soft budget. New work re-arms this within the SAME
    # turn; _compacted_this_turn is telemetry, never a lifetime lockout.
    watermark = getattr(session, "_compaction_watermark", 0)
    history_len = len(getattr(session_manager, "history", []))
    if watermark and history_len >= watermark and history_len - watermark < 4:
        return None

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
        # Zero capacity (non-L5 layers + reserve >= window) means compaction
        # cannot help — the fixed prompt itself must shrink. Return None so
        # the caller reports the condition instead of looping.
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

        if budget <= 0:
            return None
        working_limit = int(variables.get("auto_compaction_token_limit", 64_000))
        if working_limit > 0:
            budget = min(budget, working_limit)
        if session_manager.estimate_runtime_history_tokens() <= budget:
            return None
        target = max(1, budget // 2)
        # Mark attempts as well as successful rolls: an uncompactable tail
        # must not cause repeated summarization calls on provider retries.
        session._compaction_watermark = history_len
        session_manager._tool_result_floor = resolve_tool_result_floor(session)
        # Bridge the optional compact_focus variable (Claude Code
        # `/compact <focus>` style) so the LLM summarizer emphasizes it.
        session_manager._compact_focus = (
            getattr(session, "variables", None) or {}
        ).get("compact_focus") or ""
        # Tag this compaction for the run tracer (drained into the trace at the
        # post-response seam). `iter` comes from the loop's current-iter marker.
        session_manager._pending_compaction_kind = kind
        session_manager._pending_compaction_iter = int(
            getattr(session, "_trace_current_iter", 0) or 0
        )
        rolled = session_manager.roll_history_summary_to_token_budget(
            target,
            keep_recent=resolve_keep_recent(session),
            provider=getattr(session, "provider", None),
            allow_degrade=False,
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Auto-compaction raised %s; continuing without compacting", exc)
        return None
    if rolled:
        # Keep accounting and retry suppression in sync with the new tail.
        session._compacted_this_turn = True
        session._compaction_watermark = len(session_manager.history)
        logger.info(
            "Auto-compaction triggered (high watermark=%d, target=%d tokens).",
            budget,
            target,
        )
        return HookResult(action="continue", data={"compaction": True, "budget": target})
    return None


def manual_compact(session: any, *, focus: str = "") -> dict:
    """Run a compaction pass on demand — the back end for the `/compact`
    slash command and the agent `compact` tool.

    Unlike the auto-hook, this is an *explicit* user/agent action: it fires
    regardless of the automatic growth cooldown, and it rolls at least one bounded
    segment even when history is under the budget — so an explicit request
    always makes progress when there's anything left to summarize. Recent
    tool results are still protected by `resolve_tool_result_floor` /
    `resolve_keep_recent`, so a mid-turn agent invocation can't eat the
    active turn's own results.

    Re-baselines the growth watermark so automatic cleanup waits for fresh
    work after this explicit pass, then can resume within the same turn.
    """
    session_manager = getattr(session, "session_manager", None)
    if session_manager is None or not hasattr(
        session_manager, "roll_history_summary_to_token_budget"
    ):
        return {"ok": False, "error": "no session manager available"}
    if not hasattr(session, "_compaction_token_budget"):
        return {"ok": False, "error": "session has no _compaction_token_budget"}

    from mu.session.budgets import resolve_keep_recent, resolve_tool_result_floor

    keep_recent = resolve_keep_recent(session)
    provider = getattr(session, "provider", None)
    focus_val = (focus or "").strip() or (
        getattr(session, "variables", None) or {}
    ).get("compact_focus") or ""

    try:
        before_tokens = session_manager.estimate_runtime_history_tokens()
    except Exception:  # noqa: BLE001
        before_tokens = 0
    before_len = len(session_manager.history)
    before_anchor = int(getattr(session_manager, "summary_anchor", 0) or 0)

    try:
        session_manager._tool_result_floor = resolve_tool_result_floor(session)
        session_manager._compact_focus = focus_val
        session_manager._pending_compaction_kind = "manual"
        session_manager._pending_compaction_iter = int(
            getattr(session, "_trace_current_iter", 0) or 0
        )
        budget = int(session._compaction_token_budget())
        rolled = session_manager.roll_history_summary_to_token_budget(
            budget,
            keep_recent=keep_recent,
            provider=provider,
        )
        if not rolled:
            # Honor the explicit manual request: roll one bounded segment
            # even when the budget gate said we're under budget (matches
            # Claude Code's manual `/compact`). No-op if the anchor is
            # already at the keep-recent boundary — nothing left to summarize.
            rolled = session_manager.roll_history_summary(
                keep_recent=keep_recent,
                provider=provider,
            )
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Manual compaction raised %s", exc)
        return {"ok": False, "error": str(exc)}

    try:
        after_tokens = session_manager.estimate_runtime_history_tokens()
    except Exception:  # noqa: BLE001
        after_tokens = before_tokens
    after_len = len(session_manager.history)
    after_anchor = int(getattr(session_manager, "summary_anchor", 0) or 0)

    # An explicit pass restarts the automatic cleanup growth cooldown.
    session._compacted_this_turn = True
    session._compaction_watermark = after_len

    return {
        "ok": True,
        "compacted": bool(rolled),
        "budget_tokens": budget,
        "keep_recent": keep_recent,
        "focus": focus_val,
        "before": {
            "history_len": before_len,
            "summary_anchor": before_anchor,
            "est_tokens": before_tokens,
        },
        "after": {
            "history_len": after_len,
            "summary_anchor": after_anchor,
            "est_tokens": after_tokens,
        },
    }


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


__all__ = ["install", "manual_compact"]
