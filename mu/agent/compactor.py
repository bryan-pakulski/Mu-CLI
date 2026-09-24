"""Keep a bounded working history throughout long-running agent turns.

The model clears selected completed results and checkpoints before requesting
summaries. Automatic cleanup is a provider-aware fallback: measure projected
history, clear eligible payloads cheaply, then summarize if pressure remains.
An explicit smaller working-history limit is still respected.
"""

from __future__ import annotations

import logging
from typing import Optional

from .hooks import HookContext, HookRegistry, HookResult, HookSpec, default_registry


logger = logging.getLogger("mucli")

from utils.config import _DEFAULT_CONTEXT_TOKEN_LIMIT


def checkpoint_progress_if_due(session, iteration: int) -> bool:
    """Periodic summaries are opt-in; zero consistently disables them."""
    every = int(session.variables.get("progress_checkpoint_every", 0) or 0)
    if every > 0 and iteration > 1 and iteration % every == 0:
        return session.session_manager.force_progress_checkpoint(session.provider)
    return False


def _pressure_clear(ctx: HookContext, variables: dict) -> Optional[dict]:
    """Summarizer-free working-set cap (context_packaging_v2 P3-T7).

    Runs BEFORE the ``auto_compaction_enabled`` gate: even a session that
    opted out of automatic summarization must not ride to the provider
    ceiling with hundreds of completed tool payloads verbatim. When the
    drift-corrected fill of the assembled request reaches
    ``context_pressure_nudge_pct`` (the same threshold that wakes the
    model), clear completed, non-recent, non-protected tool results into
    the durable store — oldest first — until the projected fill drops
    ~10 points below the threshold or nothing clearable remains. Ledger
    kind ``pressure_clear``. Returns the ``edit_tool_results`` summary when
    anything was cleared, else ``None``.
    """
    session = ctx.session
    if session is None or ctx.system_prompt is None:
        return None
    if not hasattr(session, "_build_messages_from_history"):
        return None
    try:
        threshold = float(variables.get("context_pressure_nudge_pct", 80.0))
    except (TypeError, ValueError):
        threshold = 80.0
    if threshold <= 0:
        return None
    try:
        from mu.session.budgets import drift_corrected_context_limit, effective_fill
        from mu.session.context_maintenance import candidates, edit_tool_results, projected_tokens
        from .context_guard import _estimate_request_tokens

        sm = session.session_manager
        store = getattr(getattr(sm, "tool_result_cache", None), "_store", None)
        if store is None:
            return None
        limit = int(drift_corrected_context_limit(session))
        fixed = int(_estimate_request_tokens(ctx.system_prompt, [], ctx.tools)["total"])

        def fill_now() -> float:
            return float(effective_fill(session, fixed + projected_tokens(session), limit=limit)["fill_pct"])

        fill = fill_now()
        if fill < threshold:
            return None
        target = max(0.0, threshold - 10.0)
        selected = [
            item["result_id"]
            for item in sorted(candidates(session), key=lambda it: it["history_index"])
            if not item["blocked_reason"] and item["state"] != "clear"
        ]
        if not selected:
            return None
        cleared_total: dict = {"ok": True, "action": "clear", "changed": [], "skipped": [],
                               "before_tokens": 0, "after_tokens": 0, "saved_tokens": 0,
                               "summarizer_calls": 0, "fill_before": round(fill, 2)}
        # Clear in small batches, re-measuring between them so we stop as
        # soon as the projection is comfortably under the threshold.
        batch_size = 8
        for offset in range(0, len(selected), batch_size):
            batch = selected[offset:offset + batch_size]
            try:
                sm._pending_shrink_kind = "pressure_clear"
                part = edit_tool_results(session, batch)
            finally:
                sm._pending_shrink_kind = None
            if offset == 0:
                cleared_total["before_tokens"] = part.get("before_tokens", 0)
            cleared_total["after_tokens"] = part.get("after_tokens", 0)
            cleared_total["changed"] += list(part.get("changed", []))
            cleared_total["skipped"] += list(part.get("skipped", []))
            fill = fill_now()
            if fill <= target:
                break
        cleared_total["saved_tokens"] = max(
            0, int(cleared_total["before_tokens"]) - int(cleared_total["after_tokens"])
        )
        cleared_total["fill_after"] = round(fill, 2)
        if not cleared_total["changed"]:
            return None
        logger.info(
            "Pressure clear: archived %d completed tool result(s) (fill %.0f%% -> %.0f%%, "
            "threshold %.0f%%, saved ~%d projected tokens).",
            len(cleared_total["changed"]), cleared_total["fill_before"], fill, threshold,
            cleared_total["saved_tokens"],
        )
        return cleared_total
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("Pressure clear skipped: %s", exc)
        return None


def _pressure_only(pressure: Optional[dict]) -> Optional[HookResult]:
    """Surface a pressure-clear as a compaction result so the retry loop
    rebuilds the wire messages from live history; None when nothing cleared."""
    if pressure:
        return HookResult(action="continue", data={"compaction": True, "clearing": pressure})
    return None


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
    # Summarizer-free working-set cap runs regardless of the opt-out below.
    pressure = _pressure_clear(ctx, variables)
    # Respect saved opt-outs; emergency provider-window recovery remains on.
    if not variables.get("auto_compaction_enabled", True):
        if pressure:
            return HookResult(action="continue", data={"compaction": True, "clearing": pressure})
        return None

    # Retry/no-progress cooldown, including when protected recent results
    # alone exceed the soft budget. New work re-arms this within the SAME
    # turn; _compacted_this_turn is telemetry, never a lifetime lockout.
    watermark = getattr(session, "_compaction_watermark", 0)
    history_len = len(getattr(session_manager, "history", []))
    if watermark and history_len >= watermark and history_len - watermark < 4:
        return _pressure_only(pressure)

    try:
        threshold = float(variables.get("context_trim_threshold", 0.85) or 0.85)
    except (TypeError, ValueError):
        return _pressure_only(pressure)
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
            return _pressure_only(pressure)
        budget = int(context_limit * threshold)

    try:
        from mu.session.budgets import resolve_keep_recent, resolve_tool_result_floor

        if budget <= 0:
            return _pressure_only(pressure)
        from mu.session.context_maintenance import candidates, edit_tool_results, projected_tokens
        from .context_guard import _estimate_request_tokens
        from mu.session.budgets import drift_corrected_context_limit, resolve_response_reserve
        working_limit = int(variables.get("auto_compaction_token_limit", 0))
        if ctx.system_prompt is not None:
            fixed = _estimate_request_tokens(ctx.system_prompt, [], ctx.tools)
            available = drift_corrected_context_limit(session) - resolve_response_reserve(session)
            budget = min(budget, int(available * threshold) - fixed["total"])
        if working_limit > 0:
            budget = min(budget, working_limit)
        if budget <= 0:
            return _pressure_only(pressure)
        # cl100k frame throughout: `budget` derives from the drift-corrected
        # limit, `estimate` is the raw cl100k projection. Scaling `estimate`
        # by the drift ratio here would double-correct (see
        # budgets.effective_fill for the real-frame equivalent).
        estimate = (lambda: projected_tokens(session)) if hasattr(session, "_build_messages_from_history") else session_manager.estimate_runtime_history_tokens
        if estimate() <= budget:
            return _pressure_only(pressure)
        target = max(1, budget // 2 if working_limit > 0 else int(budget * 0.8))
        # Mark attempts as well as successful rolls: an uncompactable tail
        # must not cause repeated summarization calls on provider retries.
        session._compaction_watermark = history_len
        # Reclaim completed cached results without a provider call before
        # falling back to lossy summarization near the effective window.
        cleared = None
        if hasattr(session, "_build_messages_from_history"):
            selected = [item["result_id"] for item in candidates(session)
                        if not item["blocked_reason"] and item["state"] != "clear"][:100]
            if selected:
                cleared = edit_tool_results(session, selected)
            if estimate() <= budget:
                return HookResult(action="continue", data={"compaction": True, "clearing": cleared})
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
            estimate_tokens=estimate,
        )
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("Auto-compaction raised %s; continuing without compacting", exc)
        return _pressure_only(pressure)
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
    if cleared and cleared.get("saved_tokens", 0) > 0:
        return HookResult(action="continue", data={"compaction": True, "clearing": cleared})
    return _pressure_only(pressure)


def manual_compact(session: any, *, focus: str = "", checkpoint=None,
                   preserve_result_ids=None, clear_result_ids=None, through_index=None,
                   kind: str = "manual") -> dict:
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
    from mu.session.context_maintenance import candidates, edit_tool_results, projected_tokens, validate_checkpoint

    try:
        checkpoint = validate_checkpoint(checkpoint)
        if through_index is not None and (
            isinstance(through_index, bool) or not isinstance(through_index, int)
            or through_index < 0 or through_index >= len(session_manager.history)
        ):
            raise ValueError("through_index must identify a saved history message")
        for ids in (preserve_result_ids, clear_result_ids):
            if ids is not None and (not isinstance(ids, list) or len(ids) > 100
                                   or any(not isinstance(key, str) or not key or len(key) > 128 for key in ids)):
                raise ValueError("result identifiers must be a list of at most 100 identifiers")
        validate_checkpoint({**getattr(session_manager, "context_checkpoint", {}), **checkpoint})
        known = {item["result_id"] for item in candidates(session)} if preserve_result_ids else set()
        if set(preserve_result_ids or []) - known:
            raise ValueError("cannot preserve unknown result identifiers; inspect context_status first")
        if set(preserve_result_ids or []) & set(clear_result_ids or []):
            raise ValueError("the same result cannot be preserved and cleared")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if preserve_result_ids:
        edit_tool_results(session, preserve_result_ids, action="keep")
    clearing = edit_tool_results(session, clear_result_ids or [], checkpoint=checkpoint)

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
        session_manager._pending_compaction_kind = str(kind or "manual")
        session_manager._pending_compaction_iter = int(
            getattr(session, "_trace_current_iter", 0) or 0
        )
        budget = int(session._compaction_token_budget())
        rolled = session_manager.roll_history_summary_to_token_budget(
            budget,
            keep_recent=keep_recent,
            provider=provider,
            allow_degrade=False,
            estimate_tokens=lambda: projected_tokens(session),
            through_index=through_index,
        )
        if not rolled:
            # Honor the explicit manual request: roll one bounded segment
            # even when the budget gate said we're under budget (matches
            # Claude Code's manual `/compact`). No-op if the anchor is
            # already at the keep-recent boundary — nothing left to summarize.
            _snap = session_manager._shrink_snapshot()
            rolled = session_manager.roll_history_summary(
                keep_recent=keep_recent,
                provider=provider,
                max_segment_chars=24_000,
                through_index=through_index,
            )
            if rolled:
                session_manager._record_shrink_since(
                    _snap,
                    kind=str(kind or "manual"),
                    summarizer=getattr(session_manager, "_last_summary_mode", "unknown"),
                    keep_recent=keep_recent,
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
        "clearing": clearing,
        "projected_tokens": projected_tokens(session),
        "checkpoint": getattr(session_manager, "context_checkpoint", {}),
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
