"""Context-window guard: token estimates, preflight check, drift
calibration, and reactive overflow recovery.

Extracted from mu/agent/loop_body.py (which re-exports every name so the
test modules importing `from mu.agent.loop_body import _preflight_...`
keep working unchanged).
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
import time
from typing import Any, Dict

from mu.agent.retry import is_context_overflow_error, parse_overflow_token_counts
from providers.base import Message, MessagePart
from utils.logger import logger
from utils.token_estimator import estimate_messages_tokens, estimate_tokens, tool_schema_text


def _reinject_refreshed_summary(session, prompt: str) -> str:
    """Rebuild the layered hierarchy from the ORIGINAL persona base after a
    compaction rewrote the L2 conversation summary.

    Compaction rewrites L2, but the incoming ``prompt`` is already
    fully injected (base persona + L1-L3 layers + loop-appended
    scratchpad/durable-recall/L3B blocks). Re-running
    ``_inject_hierarchical_context`` on that prompt appends a SECOND copy of
    the L1-L3 layers on top of the old ones, so an emergency recovery
    GROWS the prompt it is trying to shrink, and repeated rounds duplicate
    layer content while history was already compacted.

    loop_body stashes the ORIGINAL pre-injection base on
    ``session._system_prompt_base`` every turn. Rebuilding from that base
    produces one fresh set of L1-L3 layers over the persona, with the
    per-turn caches intact.

    The injector records the exact old prefix. Turn-scoped blocks appended
    AFTER it (durable recall, scratchpad snapshot/evictions, L3B subagent
    context) are re-attached once after the rebuilt layers.
    Returns ``prompt`` unchanged when the base is unknown or the rebuild
    fails — callers treat this as best-effort and never crash the loop.
    """
    base = getattr(session, "_system_prompt_base", None)
    if not base:
        return prompt
    previous = getattr(session, "_last_injected_context_prompt", None)
    tail = (
        prompt[len(previous):]
        if isinstance(previous, str) and previous and prompt.startswith(previous)
        else None
    )
    try:
        rebuilt = session._inject_hierarchical_context(
            base,
            cached_skills=getattr(session, "_turn_skills_block", None),
            cached_context_files=getattr(session, "_turn_context_files_block", None),
            # Same assembly mode the loop used for this request (P4): a
            # prefix-stable prompt must not regain the volatile layers here.
            volatile_handoff=bool(getattr(session, "_last_injected_volatile_handoff", False)),
        )
    except Exception:
        logger.warning(
            "Emergency re-inject from base failed; keeping existing prompt.",
            exc_info=True,
        )
        return prompt
    if tail is not None:
        return rebuilt + tail
    if prompt.startswith(rebuilt):
        tail = prompt[len(rebuilt):]
    else:
        tail = ""
        logger.warning(
            "Emergency re-inject: rebuilt prompt is not a prefix of the "
            "incoming prompt; post-injection tail dropped.",
        )
    return rebuilt + tail


def _reattach_runtime_state(session, messages):
    """Rebuilt wire messages come from history, which never holds the
    request-only runtime-state message — re-append it (P4)."""
    try:
        from mu.session.runtime_state import append_runtime_state

        return append_runtime_state(session, messages)
    except Exception:  # noqa: BLE001
        return messages


def _estimate_messages_tokens(messages) -> int:
    """Visible-content token estimate for a list of Message objects.

    Delegates to ``utils.token_estimator.estimate_messages_tokens`` — the one
    estimator the job relay preflight shares — so provider replay blobs
    (``thought_signature``: thinking signatures, encrypted reasoning) never
    count toward a pressure gate."""
    return estimate_messages_tokens(messages)


_TOOL_SCHEMA_CACHE: Dict[tuple, int] = {}
_TOOL_SCHEMA_CACHE_LOCK = threading.Lock()
_TOOL_SCHEMA_CACHE_CAP = 8


def _estimate_tools_tokens(tools) -> int:
    """Estimate tool definitions, which are part of every agentic request.

    Round-46 F2: the JSON encoding + full tokenization is memoized by
    tool-set identity (names + schema hash), because the active tool set is
    stable across iterations — without the cache this re-encodes and
    re-tokenizes megabytes of unchanged schemas on EVERY provider call.
    Small LRU (insertion-order evict) keyed by the identity tuple.
    """
    if not tools:
        return 0
    serialized = tool_schema_text(tools)
    identity = (
        tuple(getattr(tool, "name", "") for tool in tools),
        hashlib.sha256(serialized.encode("utf-8", errors="replace")).hexdigest(),
    )
    with _TOOL_SCHEMA_CACHE_LOCK:
        cached = _TOOL_SCHEMA_CACHE.get(identity)
    if cached is not None:
        return cached
    count = estimate_tokens(serialized)
    with _TOOL_SCHEMA_CACHE_LOCK:
        if len(_TOOL_SCHEMA_CACHE) >= _TOOL_SCHEMA_CACHE_CAP and identity not in _TOOL_SCHEMA_CACHE:
            _TOOL_SCHEMA_CACHE.pop(next(iter(_TOOL_SCHEMA_CACHE)), None)
        _TOOL_SCHEMA_CACHE[identity] = count
    return count


def _estimate_request_tokens(
    system_prompt: str, messages, tools=None
) -> Dict[str, Any]:
    """ONE structured estimate of a full provider request (Round-46 F2).

    Returns a manifest ``{"system", "messages", "tools", "total"}`` — the
    single tokenization shared by the preflight budget check, the loop's
    ``request_token_estimate``, and the trace ``build_request_record``.
    Previously these three consumers each re-tokenized the entire system
    prompt + all messages + tool schemas (three full traversals per
    provider call); the trace record additionally re-encoded the tool
    schemas a fourth time.
    """
    system_tokens = estimate_tokens(system_prompt)
    msg_tokens = _estimate_messages_tokens(messages)
    tool_tokens = _estimate_tools_tokens(tools)
    return {
        "system": system_tokens,
        "messages": msg_tokens,
        "tools": tool_tokens,
        "total": system_tokens + msg_tokens + tool_tokens,
    }


def _preflight_context_check(
    session, system_prompt, messages, turn_start_index=None, tools=None
):
    """Emergency-compact history if the assembled prompt exceeds the
    provider's context window.

    The normal compaction pass (``roll_history_summary_to_token_budget``)
    fires *before* the system prompt is finalized — resumption briefings,
    hierarchical context injection, and per-iteration memory/scratchpad
    layers all grow the prompt after that pass.  This pre-flight check
    runs right before the provider call with the *actual* prompt and
    messages, and triggers a second compaction if the total would
    overflow.

    ``turn_start_index`` is forwarded to ``_prepare_runtime_history`` so
    that tool-window compression applies the same pair-grouping logic
    used during normal (non-emergency) calls.  Without it the early-exit
    at ``turn_start_index is None`` skips compression and sends more
    tokens than necessary — which can cascade into repeated emergency
    compaction attempts.

    Returns (system_prompt, messages) — unchanged if within budget,
    or with rebuilt messages after emergency compaction.
    """
    from mu.session.budgets import (
        drift_corrected_context_limit,
        resolve_response_reserve,
        resolve_keep_recent,
        resolve_tool_result_floor,
    )

    # Round-51 T1: resolve-guarded effective limit. The drift-corrected
    # limit is re-derived every iteration; a >5% rise vs the previously
    # observed value means the guard ceiling moved (e.g. learned drift
    # ratcheted down or the provider window resolved late). Re-resolve once
    # and use the stable value so the budget decision is not made against
    # an estimate that changed mid-flight, then track it on the session
    # for run-start comparability.
    _candidate_limit = drift_corrected_context_limit(session)
    _prev_limit = getattr(session, "_last_effective_limit", None)
    if (
        isinstance(_prev_limit, int)
        and _prev_limit > 0
        and _candidate_limit > _prev_limit * 1.05
    ):
        logger.warning(
            "Context governance: effective guard limit rose %d -> %d; "
            "re-resolving once for a stable budget.",
            _prev_limit, _candidate_limit,
        )
        try:
            _recheck = drift_corrected_context_limit(session)
            context_limit = int(_recheck) if _recheck else int(_candidate_limit)
        except Exception:
            context_limit = int(_candidate_limit)
    else:
        context_limit = int(_candidate_limit)
    try:
        session._last_effective_limit = int(context_limit)
    except Exception:
        pass
    # Round-51 T1: divergence telemetry — configured (user-visible)
    # context_token_limit vs the guard-effective limit. Recorded on the
    # session every iteration; warned once per run. This is the seam the
    # trace emitter reads so the run_start header can carry both limits.
    try:
        _configured = int(session.variables.get("context_token_limit", 0) or 0)
        if _configured > 0 and context_limit > 0:
            _delta_pct = (context_limit - _configured) / float(_configured) * 100.0
            if abs(_delta_pct) > 5.0:
                session._context_limit_divergence = {
                    "configured_limit": _configured,
                    "effective_limit": int(context_limit),
                    "delta_pct": round(_delta_pct, 2),
                }
                if not getattr(session, "_context_limit_divergence_warned", False):
                    session._context_limit_divergence_warned = True
                    logger.warning(
                        "Context governance divergence: configured "
                        "context_token_limit=%d but drift-corrected effective "
                        "guard limit is %d (%+.1f%%); the guard value "
                        "governs compaction.",
                        _configured, context_limit, _delta_pct,
                    )
                    if session.ui and hasattr(session.ui, "show_info"):
                        try:
                            session.ui.show_info(
                                f"Context limit divergence: configured {_configured}, "
                                f"effective guard {context_limit} ({_delta_pct:+.1f}%)."
                            )
                        except Exception:
                            pass
    except Exception:
        pass
    response_reserve = resolve_response_reserve(session)
    # FRAME CONTRACT: `context_limit` is already drift-corrected (window /
    # learned real-per-cl100k ratio), so the comparison below is in the
    # cl100k frame: raw cl100k `total` vs corrected limit. Do NOT also scale
    # `total` by the drift ratio — that would double-correct. The real-frame
    # view of the same decision is `budgets.effective_fill`.
    max_prompt = context_limit - response_reserve

    # Round-46 F2: ONE tokenization pass for this request. The manifest is
    # stashed on the session so the loop's request_token_estimate and the
    # trace build_request_record reuse it instead of each re-tokenizing the
    # full prompt + messages + tool schemas (3→1 full traversals).
    estimate = _estimate_request_tokens(system_prompt, messages, tools)
    prompt_tokens = estimate["system"]
    msg_tokens = estimate["messages"]
    tool_tokens = estimate["tools"]
    total = estimate["total"]
    try:
        session._request_estimate_manifest = estimate
    except Exception:
        pass
    # Stash the cl100k estimate of the assembled prompt for the cold-cache
    # drift calibration in the response handler: when Ollama's
    # prompt_eval_count is a strong full-prompt signal (>= half the cl100k
    # estimate), we can learn the real/cl100k drift ratio from it.
    try:
        session._last_prompt_cl100k_est = int(total)
    except Exception:
        pass

    if total <= max_prompt:
        return system_prompt, messages

    overshoot = total - max_prompt
    logger.warning(
        "Pre-flight context check: estimated %d tokens "
        "(limit %d, reserve %d, max_prompt %d) — %d over. "
        "Emergency compaction triggered.",
        total, context_limit, response_reserve, max_prompt, overshoot,
    )
    emergency_budget = max(512, max_prompt - prompt_tokens - tool_tokens)
    base_keep_recent = resolve_keep_recent(session, emergency=True)
    # R3 / FM-8: even emergency compaction must respect the per-turn
    # tool-result floor so tool results just received stay verbatim.
    session.session_manager._tool_result_floor = resolve_tool_result_floor(session)

    # Escalating loop: compact, rebuild, re-estimate. If the first pass
    # doesn't reach the budget (the cl100k estimate under-counts Ollama's
    # real BPE ~2.2x, and a large recent tool result can keep the tail
    # fat), shrink keep_recent and compact again — up to 3 rounds. The
    # reactive overflow recovery in `_generate_with_overflow_recovery`
    # is the estimation-independent backstop if this still isn't enough.
    for round_idx in range(3):
        _before_anchor = int(getattr(session.session_manager, "summary_anchor", 0) or 0)
        try:
            session.session_manager._pending_compaction_kind = "emergency_preflight"
            session.session_manager._pending_compaction_iter = int(
                getattr(session, "_trace_current_iter", 0) or 0
            )
            session.session_manager.roll_history_summary_to_token_budget(
                int(emergency_budget * 0.85),
                keep_recent=max(2, base_keep_recent - round_idx * 2),
                max_passes=6,
                provider=session.provider,
                estimate_tokens=_projected_history_estimator(session),
            )
            session._compaction_watermark = len(session.session_manager.history)
            _after_anchor = int(getattr(session.session_manager, "summary_anchor", 0) or 0)
            if _after_anchor > _before_anchor:
                try:
                    from mu.trace.emitter import emit_context_artifact
                    emit_context_artifact(session,
                        iteration=int(getattr(session, "_trace_current_iter", 0) or 0),
                        artifact_id=f"history:{_before_anchor}-{_after_anchor}",
                        state="compacted", reason="hard_context_preflight")
                except Exception:
                    pass
        except Exception as exc:
            logger.warning("Emergency compaction failed: %s", exc)
            return system_prompt, messages

        recent_history = session._prepare_runtime_history(
            turn_start_index=turn_start_index,
        )
        messages = session._build_messages_from_history(
            recent_history,
            {"role": "system", "parts": []},
        )[:-1]
        messages = _reattach_runtime_state(session, messages)

        new_msg_tokens = _estimate_messages_tokens(messages)
        new_total = prompt_tokens + new_msg_tokens + tool_tokens
        if new_total <= max_prompt:
            logger.info(
                "Emergency compaction round %d complete: messages %d -> %d tokens.",
                round_idx + 1, msg_tokens, new_msg_tokens,
            )
            # Compaction may have rewritten the L2 conversation summary —
            # re-run the layered injection so the provider call sees the
            # refreshed summary. The rebuild starts from the ORIGINAL
            # persona base (stashed at the first successful injection) so
            # the refreshed L1-L3 layers REPLACE the old ones instead of
            # stacking on top of the already-injected prompt, and the
            # loop-appended post-injection tail (durable recall, scratchpad,
            # L3B subagent block) is re-attached exactly once.
            system_prompt = _reinject_refreshed_summary(session, system_prompt)
            return system_prompt, messages
        logger.warning(
            "Emergency compaction round %d: still %d over (est %d vs max_prompt %d); "
            "escalating keep_recent.",
            round_idx + 1, new_total - max_prompt, new_total, max_prompt,
        )

    # Couldn't get under budget in 3 rounds — return the most-compacted
    # state; the provider call may still overflow, at which point reactive
    # overflow recovery catches it. This avoids throwing pre-emptively.
    logger.warning(
        "Emergency compaction exhausted 3 rounds; est %d still over max_prompt %d. "
        "Reactive overflow recovery will backstop the provider call.",
        prompt_tokens + _estimate_messages_tokens(messages), max_prompt,
    )
    # Even when the budget could not be met, compaction may have rewritten
    # the L2 summary — re-run the layered injection so the provider call
    # reflects it. Same base-rebuild as the success path: the refreshed
    # L1-L3 layers replace the previously injected copies instead of
    # stacking on top of them, with the loop-appended post-injection tail
    # preserved exactly once.
    system_prompt = _reinject_refreshed_summary(session, system_prompt)
    return system_prompt, messages


# The overflow recovery ladder. Each rung is stronger than the last:
#   compact        — aggressive compaction (keep_recent=4) toward the real window.
#   strip_degrade  — drop every replay blob from the retry and degrade large
#                    tool results, including the protected recent ones and
#                    provider-signed bundles (floor lifted, keep_recent=2).
#   full_compact   — the `/compact` equivalent: roll the whole conversation
#                    except the latest user/goal message into L2
#                    (ledger kind ``overflow_full_compact``).
#   truncate       — a single message larger than the window has its tool
#                    result / text body cut with a clear marker
#                    (ledger kind ``overflow_truncate``).
_OVERFLOW_RUNGS = ("compact", "strip_degrade", "full_compact", "truncate")

# Per-turn circuit breaker, counted in RUNGS applied (not identical retries).
# A later iteration in the same turn can overflow again as tool results
# accumulate — that's a *different* overflowing prompt and deserves its own
# recovery, so this is a count (not a boolean). The cap prevents a
# compact-fail loop when nothing can shrink the prompt. Sessions may raise it
# with ``overflow_recovery_max_rungs`` (job stages do: one stage is one turn).
_MAX_OVERFLOW_RECOVERIES_PER_TURN = len(_OVERFLOW_RUNGS)

# A rung that shrinks the estimated request by less than this fraction is not
# retried as-is: the next rung is applied before the provider is called again.
_MIN_OVERFLOW_SHRINK = 0.10

#: Marker left where rung 4 cut a message body (``[truncated N tokens ...]``).
OVERFLOW_TRUNCATION_MARKER = "[truncated {tokens:,} tokens to fit the context window]"


def _max_overflow_rungs(session) -> int:
    try:
        value = int((getattr(session, "variables", None) or {}).get("overflow_recovery_max_rungs", 0) or 0)
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else _MAX_OVERFLOW_RECOVERIES_PER_TURN


def _resolve_real_context_window(session) -> int:
    """The provider's *real* (un-safety-factored) input-context ceiling.

    Reactive overflow recovery targets this number, not the safety-factored
    `resolve_context_limit`, because the wire-level "prompt too long" 400 is
    ground truth about the real window — the safety factor is only a
    pre-flight hint. Falls back to the factored limit + a default if the
    provider doesn't expose one.
    """
    try:
        window = session.provider.effective_context_window(
            session.provider.model_name
        )
    except Exception:
        window = None
    if window and window > 0:
        return int(window)
    from mu.session.budgets import resolve_context_limit
    return int(resolve_context_limit(session) or 200_000)


def _overflow_drift_ratio(session, system_prompt, messages, overflow_error) -> float:
    """Real-tokens / cl100k-tokens for the prompt that just overflowed.

    Ground truth from the error (the daemon's own BPE count of the prompt)
    divided by the harness's cl100k estimate of the same prompt. Clamped to
    a sane [1.0, 6.0] band so a mis-parsed error can't blow up the budget.
    Falls back to the provider's static `compaction_safety_factor` (2.5 for
    Ollama, 1.0 otherwise) when the error body doesn't carry real counts.
    """
    cl100k_full = estimate_tokens(system_prompt) + _estimate_messages_tokens(messages)
    real_prompt, _real_max = parse_overflow_token_counts(overflow_error)
    if real_prompt and cl100k_full and cl100k_full > 0:
        drift = real_prompt / cl100k_full
        # Clamp: drift < 1.0 is impossible (cl100k never over-counts Ollama by
        # that much) and means a parse error; > 6.0 is pathological content.
        return max(1.0, min(6.0, drift))
    try:
        factor = float(session.provider.compaction_safety_factor())
    except Exception:
        factor = 1.0
    return max(1.0, factor or 1.0)


def _calibrate_drift_from_response(session, response) -> None:
    """EWMA-update ``session._observed_drift_ratio`` from a cold-cache
    provider response, if the response's ``input_tokens`` is a strong
    full-prompt signal.

    For Ollama, ``response.input_tokens`` is the streamed
    ``prompt_eval_count`` — normally only the non-cached delta (near-zero in
    a warm loop, useless for drift). But on a cold cache (first call of a
    session, or after the prompt changed substantially) it reflects (close
    to) the FULL prompt, so ``real_tokens / cl100k_tokens`` is learnable from
    it. The guard rejects the warm-cache near-zero delta: only calibrate when
    the reported count is >= half the stashed cl100k estimate AND > 500
    tokens AND that estimate is itself substantial (> 1000). Never raises.

    For prompt-caching providers (Anthropic) the streamed ``input_tokens``
    may be only the uncached delta while ``cache_read_tokens`` /
    ``cache_creation_tokens`` carry the rest; ``prompt_tokens_total`` folds
    them into the full prompt size so warm iterations calibrate too (trace
    mucli_run_355d1b39c2a7: 160/274 iterations were cache hits and never
    updated drift under the input-only rule). The full figure is stashed as
    ``session._last_prompt_tokens_real`` (with the iteration it belongs to)
    so the pressure gates can prefer ground truth over ``cl100k * drift``.
    """
    try:
        from providers.base import prompt_tokens_total

        cl100k_est = int(getattr(session, "_last_prompt_cl100k_est", 0) or 0)
        reported_in = prompt_tokens_total(
            int(getattr(response, "input_tokens", 0) or 0),
            int(getattr(response, "cache_read_tokens", 0) or 0),
            int(getattr(response, "cache_creation_tokens", 0) or 0),
        )
        if reported_in > 0:
            session._last_prompt_tokens_real = int(reported_in)
            session._last_prompt_tokens_real_iter = int(
                getattr(session, "_trace_current_iter", 0) or 0
            )
        if cl100k_est > 1000 and reported_in > 500 and reported_in >= cl100k_est // 2:
            from mu.session.budgets import update_observed_drift

            update_observed_drift(session, reported_in / float(cl100k_est))
    except Exception:  # noqa: BLE001 — telemetry must not break the loop
        pass


def _aggressive_compact_for_overflow(
    session, system_prompt, messages, *,
    overflow_error=None,
    tools=None,
    keep_recent: int = 4,
    margin: float = 0.20,
    lift_floor: bool = False,
):
    """Claude Code Tier 5-style reactive compaction: keep only the last
    ``keep_recent`` messages verbatim and summarize everything older into
    the rolling summary, then degrade oversized payloads left in the tail.

    Estimation-independent: the budget is derived from the wire-level
    overflow's ground-truth real token count (parsed from the error) paired
    with the harness cl100k estimate of the same prompt — giving the real
    per-content drift ratio — so the retry targets a *real* token count the
    daemon will accept. The static `compaction_safety_factor` alone can't:
    drift varies ~2.2–3.2x by content, so targeting "half the safety-factored
    limit" in cl100k still overflows on the wire when drift outruns the
    factor.

    Escalation is driven by the caller (`_generate_with_overflow_recovery`),
    which raises the aggression per still-overflowing retry:
      * ``keep_recent`` shrinks (4 → 2 → 1) so more history is summarized,
      * ``margin`` grows (0.20 → 0.30 → 0.40) so the target real-token budget
        drops further below the window,
      * ``lift_floor`` (from the 2nd attempt on) forces the per-turn
        tool-result floor to 0 so the protected recent tool results that
        alone exceed the budget can be degraded — the lesser evil vs
        crashing the turn with a hard overflow. The floor is restored in
        the `finally` so later in-turn compaction keeps FM-8 protection.
    """
    from mu.session.budgets import (
        resolve_response_reserve,
        resolve_tool_result_floor,
        update_observed_drift,
    )

    real_max = _resolve_real_context_window(session)
    response_reserve = resolve_response_reserve(session)
    drift = _overflow_drift_ratio(session, system_prompt, messages, overflow_error)
    # Persist the measured real/cl100k drift so the NEXT turn's proactive
    # compaction gates (turn-start roll, auto-hook, preflight) fire at the
    # right point instead of relying on the static safety factor. This is the
    # fix for the repeat overflow: after one 400, every subsequent turn sees
    # the learned drift via budgets.effective_drift_ratio.
    update_observed_drift(session, drift)

    # Target (1 - margin) of the real window so drift variance across the
    # compacted content + tool schemas (not in the cl100k estimate) + the
    # response reserve all fit under the wire limit. margin escalates with
    # each recovery attempt (0.20 → 0.30 → 0.40).
    target_real = max(1024, int((real_max - response_reserve) * (1.0 - margin)))
    target_full_cl100k = max(512, int(target_real / drift))
    # The system prompt already carries L1–L4 (inject_hierarchical_context),
    # so the non-history cl100k cost is just the system prompt; the rest of
    # the budget is the L5 history ceiling the compaction loop gates on.
    non_history_cl100k = estimate_tokens(system_prompt) + _estimate_tools_tokens(tools)
    budget = max(512, target_full_cl100k - non_history_cl100k)

    logger.warning(
        "Reactive overflow compaction: real_max=%d reserve=%d drift=%.2f "
        "margin=%.2f keep_recent=%d lift_floor=%s target_real=%d "
        "target_full_cl100k=%d non_history=%d L5_budget=%d (cl100k_full~%d).",
        real_max, response_reserve, drift, margin, keep_recent, lift_floor,
        target_real, target_full_cl100k, non_history_cl100k, budget,
        non_history_cl100k + _estimate_messages_tokens(messages),
    )

    floor_value = resolve_tool_result_floor(session)
    # lift_floor: let `_degrade_oldest_runtime_payload` reach the protected
    # recent tool results that alone exceed the budget — and provider-signed
    # bundles, whose replay data the retry no longer sends (rung 2 strips it).
    # Restored in `finally`.
    session.session_manager._tool_result_floor = 0 if lift_floor else floor_value
    session.session_manager._degrade_ignore_signatures = bool(lift_floor)
    if lift_floor:
        session.session_manager._pending_compaction_kind = (
            "reactive_overflow_floor_lift"
        )
    else:
        session.session_manager._pending_compaction_kind = "reactive_overflow"
    try:
        session.session_manager._pending_compaction_iter = int(
            getattr(session, "_trace_current_iter", 0) or 0
        )
        session.session_manager._compact_focus = (
            session.variables.get("compact_focus") or ""
        )
        # max_passes is generous (12) so a tight drift-corrected budget is
        # actually reached — each pass rolls a summary segment OR degrades
        # one oversized payload, and a fat recent tail needs several
        # degradation passes to get under.
        session.session_manager.roll_history_summary_to_token_budget(
            budget, keep_recent=keep_recent, max_passes=12,
            provider=session.provider,
            estimate_tokens=_projected_history_estimator(session),
        )

        session._compaction_watermark = len(session.session_manager.history)
    except Exception as exc:
        logger.warning("Aggressive overflow compaction failed: %s", exc)
    finally:
        # Restore the floor so later in-turn compaction keeps FM-8 protection.
        session.session_manager._tool_result_floor = floor_value
        session.session_manager._degrade_ignore_signatures = False


def _projected_history_estimator(session):
    from mu.session.context_maintenance import projected_tokens
    if hasattr(session, "_build_messages_from_history"):
        return lambda: projected_tokens(session)
    return session.session_manager.estimate_runtime_history_tokens


def _maybe_nudge_context_pressure(
    session, *, limit: int, manifest: Dict[str, Any] | None = None
) -> None:
    """Model-directed compaction prompt when the assembled request runs hot.

    Automatic working-history cleanup normally runs before this point.
    This nudge also helps opted-out sessions and runs where protected
    results or fixed prompt layers keep the assembled request large.

    This nudge fires ONE synthetic user message per threshold crossing
    (hysteresis: re-arms when the summary anchor advances past the armed
    position — i.e. compaction actually happened — or fill drops below the
    threshold again). Synthetic-tagged so compact_completed_turn can still
    locate the real turn boundary. Configurable via
    `context_pressure_nudge_pct` (default 80.0; 0 disables).

    Silent no-op when no estimate manifest exists (estimator seam absent
    that iteration). Every runtime path reaching the archive seam passes
    preflight in the same iteration, which is where the limit/manifest
    are stashed — so a missing manifest simply means the nudge waits
    for the next eligible iteration. The threshold parse is guarded;
    append/emit are the same primitives the empty-response nudge uses.
    """
    try:
        threshold = float(session.variables.get("context_pressure_nudge_pct", 80.0))
    except Exception:
        threshold = 80.0
    if threshold <= 0:  # opt-out knob
        return

    # Episode hysteresis. `_pressure_nudge_fired` latches the active
    # threshold-crossing episode. Two events end an episode: the summary
    # anchor advanced (compaction actually happened) or fill dropped back
    # below the threshold. Anchor comparison uses the last anchor value we
    # observed (`_pressure_nudge_armed_at`) so each compaction resets the
    # latch exactly once.
    anchor = int(getattr(session.session_manager, "summary_anchor", 0) or 0)
    seen = int(getattr(session, "_pressure_nudge_armed_at", 0) or 0)
    if anchor > seen:
        session._pressure_nudge_armed_at = anchor
        session._pressure_nudge_fired = False
        session._pressure_nudge_iters_over = 0
        session._pressure_nudge_level = 0
        session._pressure_forced_this_episode = False

    total = int((manifest or {}).get("total", 0) or 0)
    if not total or not limit or limit <= 0:
        return
    # Real-frame fill: `limit` is the drift-corrected ceiling the preflight
    # guard stashed; `effective_fill` projects the cl100k total by the
    # learned drift (or uses this iteration's provider-reported prompt size)
    # so the model is told the number the provider actually enforces.
    from mu.session.budgets import effective_fill

    fill_info = effective_fill(session, total, limit=int(limit))
    fill = float(fill_info["fill_pct"])
    real_total = int(fill_info["real_tokens"])
    real_limit = int(fill_info["real_limit"])
    if fill < threshold:
        # Dropped back below the threshold without compacting — re-arm.
        if getattr(session, "_pressure_nudge_fired", False):
            session._pressure_nudge_fired = False
        session._pressure_nudge_iters_over = 0
        session._pressure_nudge_level = 0
        session._pressure_forced_this_episode = False
        return

    # Escalation ladder (context_packaging_v2 P3-T8). Each turn-final
    # iteration that stays at/over the threshold without the anchor moving
    # counts. Level 1 fires on the first crossing; every
    # `context_pressure_escalate_after` further iterations climb one rung:
    #   L1 nudge -> L2 stronger nudge -> L3 forced compaction (once per
    #   episode; a manual_compact pass that advances the anchor resets the
    #   ladder through the hysteresis block above).
    try:
        escalate_after = int(session.variables.get("context_pressure_escalate_after", 3) or 0)
    except Exception:
        escalate_after = 3
    iters_over = int(getattr(session, "_pressure_nudge_iters_over", 0) or 0) + 1
    session._pressure_nudge_iters_over = iters_over
    level = int(getattr(session, "_pressure_nudge_level", 0) or 0)

    if not getattr(session, "_pressure_nudge_fired", False):
        session._pressure_nudge_fired = True
        new_level = 1
    elif escalate_after > 0 and iters_over - 1 >= escalate_after * level and level < 3:
        new_level = level + 1
    else:
        return
    session._pressure_nudge_level = new_level

    from mu.trace.emitter import emit_nudge

    iteration = int(getattr(session, "_trace_current_iter", 0) or 0)
    fill_line = (
        f"{fill:.0f}% of the context limit "
        f"(~{real_total:,} / {real_limit:,} provider tokens)"
    )

    if new_level == 3 and not getattr(session, "_pressure_forced_this_episode", False):
        session._pressure_forced_this_episode = True
        forced = None
        try:
            from .compactor import manual_compact

            forced = manual_compact(session, kind="pressure_forced")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Forced pressure compaction failed: %s", exc)
        ok = bool(forced and forced.get("ok"))
        compacted = bool(forced and forced.get("compacted"))
        text = (
            f"CONTEXT PRESSURE (forced): the request stayed at {fill_line} for "
            f"{iters_over} iterations after two nudges, so the harness ran "
            f"`compact` itself ({'history summarized' if compacted else 'nothing summarizable' if ok else 'compaction failed'}). "
            "Re-read `context_status` before continuing; recover archived evidence "
            "with `recall(cache_key)` only when needed."
        )
        emit_nudge(session, "context_pressure_forced", iteration, fill_pct=round(fill, 2),
                   iters_over=iters_over, compacted=compacted)
        logger.warning(
            "Context pressure L3: forced compaction after %d iterations at %.0f%% "
            "(compacted=%s).", iters_over, fill, compacted,
        )
    elif new_level == 2:
        text = (
            f"CONTEXT PRESSURE (escalated): still at {fill_line} after "
            f"{iters_over} iterations. Before ANY other tool call: run "
            "`context_status`, then `clear_tool_results` on every completed "
            "payload you no longer need, or `compact` with a checkpoint. "
            "If pressure persists the harness will compact for you."
        )
        emit_nudge(session, "context_pressure_l2", iteration, fill_pct=round(fill, 2),
                   iters_over=iters_over)
        logger.warning(
            "Context pressure L2: still %.0f%% after %d iterations — escalated nudge.",
            fill, iters_over,
        )
    else:
        text = (
            f"CONTEXT PRESSURE: assembled request is at {fill_line}. "
            "Inspect `context_status`, "
            "preserve active evidence, and use `clear_tool_results` to "
            "archive selected completed payloads without a summarizer call. "
            "If more space is needed, use `compact` with a resumable "
            "checkpoint and a completed-history boundary. "
            "This fires once per threshold crossing; after "
            "compaction it re-arms automatically."
        )
        emit_nudge(session, "context_pressure", iteration)
        logger.warning(
            "Context pressure nudge: request ~%d / %d real tokens (%.0f%%, "
            "cl100k %d / corrected limit %d, source=%s) crossed the %.0f%% "
            "threshold — compact nudge injected.",
            real_total, real_limit, fill, total, limit, fill_info["source"], threshold,
        )

    nudge_msg = {
        "role": "user",
        "parts": [{"type": "text", "text": text}],
        "synthetic": True,
    }
    session.session_manager.history.append(nudge_msg)


def _latest_goal_index(history) -> int | None:
    """Index of the latest real user request (text, not a tool result, not a
    synthetic nudge) — the message a full compaction keeps verbatim."""
    for index in range(len(history) - 1, -1, -1):
        message = history[index]
        if message.get("role") != "user" or message.get("synthetic"):
            continue
        parts = message.get("parts") or []
        if any(p.get("type") == "tool_result" for p in parts):
            continue
        if any(p.get("type") == "text" and str(p.get("text") or "").strip() for p in parts):
            return index
    return None


def _full_compact_for_overflow(session) -> bool:
    """Rung 3: the ``/compact`` equivalent. Roll the whole conversation except
    the latest user/goal message into the L2 summary (the existing summarizer,
    in bounded segments), recorded under ledger kind ``overflow_full_compact``.

    The goal message is pinned (``protected_indices``) so it is re-injected
    verbatim below the anchor; the newest call/result bundle may stay in the
    tail when the roll cannot split it. Returns True when the anchor moved."""
    sm = session.session_manager
    goal = _latest_goal_index(sm.history)
    if goal is not None:
        pinned = set(getattr(sm, "protected_indices", set()) or set())
        pinned.add(goal)
        sm.protected_indices = pinned
    floor_value = getattr(sm, "_tool_result_floor", 0)
    before_anchor = int(getattr(sm, "summary_anchor", 0) or 0)
    sm._tool_result_floor = 0
    sm._pending_compaction_kind = "overflow_full_compact"
    sm._pending_compaction_iter = int(getattr(session, "_trace_current_iter", 0) or 0)
    sm._compact_focus = (getattr(session, "variables", None) or {}).get("compact_focus") or ""
    try:
        sm.roll_history_summary_to_token_budget(
            1,
            keep_recent=1,
            max_passes=64,
            provider=getattr(session, "provider", None),
            max_segment_chars=200_000,
            allow_degrade=False,
            estimate_tokens=_projected_history_estimator(session),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Overflow full compaction failed: %s", exc)
    finally:
        sm._tool_result_floor = floor_value
    session._compaction_watermark = len(sm.history)
    return int(getattr(sm, "summary_anchor", 0) or 0) > before_anchor


def _truncate_text(value: str, keep_tokens: int) -> tuple[str, int]:
    """Cut ``value`` to about ``keep_tokens`` tokens; returns (text, tokens removed)."""
    total = estimate_tokens(value)
    if total <= keep_tokens:
        return value, 0
    keep_chars = max(0, int(len(value) * keep_tokens / max(1, total)))
    head = value[:keep_chars].rstrip()
    removed = max(1, total - estimate_tokens(head))
    return f"{head}\n{OVERFLOW_TRUNCATION_MARKER.format(tokens=removed)}", removed


def _truncate_oversized_messages(session, system_prompt, tools) -> int:
    """Rung 4: a single message whose visible content alone exceeds the room
    left in the window has its largest text / tool-result bodies truncated with
    ``[truncated N tokens to fit the context window]``. Ledger kind
    ``overflow_truncate``. Returns the number of parts cut."""
    from mu.session.budgets import drift_corrected_context_limit, resolve_response_reserve
    from mu.session.context_maintenance import retention_for_parts
    from utils.token_estimator import estimate_message_tokens, estimate_part_tokens

    sm = session.session_manager
    try:
        limit = int(drift_corrected_context_limit(session))
    except Exception:  # noqa: BLE001
        limit = _resolve_real_context_window(session)
    room = limit - int(resolve_response_reserve(session) or 0)
    room -= estimate_tokens(system_prompt or "") + _estimate_tools_tokens(tools)
    # One message may use at most half of what is left, so the rest of the
    # conversation (and the reply) still fits next to it.
    cap = max(512, int(room * 0.5))
    anchor = max(0, min(int(getattr(sm, "summary_anchor", 0) or 0), len(sm.history)))
    indexes = sorted(set(range(anchor, len(sm.history))) | {
        i for i in (getattr(sm, "protected_indices", set()) or set()) if 0 <= i < len(sm.history)
    })
    retention = retention_for_parts(sm)
    snapshot = sm._shrink_snapshot()
    cut = 0
    for index in indexes:
        message = sm.history[index]
        if estimate_message_tokens(message) <= cap:
            continue
        parts = [p for p in message.get("parts") or []
                 if p.get("type") in ("text", "tool_result")
                 and retention.get(id(p), {}).get("action") != "clear"]
        for part in sorted(parts, key=estimate_part_tokens, reverse=True):
            over = estimate_message_tokens(message) - cap
            if over <= 0:
                break
            key = "text" if part.get("type") == "text" else "tool_result"
            raw = part.get(key)
            body = raw if isinstance(raw, str) else json.dumps(raw, default=str)
            size = estimate_tokens(body)
            new_body, removed = _truncate_text(body, max(256, size - over))
            if removed:
                part[key] = new_body
                cut += 1
    if cut:
        sm._record_shrink_since(snapshot, kind="overflow_truncate", parts=cut, cap_tokens=cap)
    return cut


def _rebuild_request(session, system_prompt, turn_start_index, tools, *, anchor_before):
    """Rebuild the wire messages from live history after a recovery rung."""
    recent_history = session._prepare_runtime_history(turn_start_index=turn_start_index)
    messages = session._build_messages_from_history(
        recent_history,
        {"role": "system", "parts": []},
    )[:-1]
    messages = _reattach_runtime_state(session, messages)
    if int(getattr(session.session_manager, "summary_anchor", 0) or 0) > anchor_before:
        # The L2 summary changed: rebuild the layered prompt from the base.
        system_prompt = _reinject_refreshed_summary(session, system_prompt)
    # Final pre-flight (re-checks + may compact more) before the retry.
    return _preflight_context_check(
        session, system_prompt, messages, turn_start_index=turn_start_index,
        tools=tools,
    )


def _apply_overflow_rung(session, rung, *, exc, system_prompt, messages, tools, turn_start_index):
    """Apply one recovery rung; returns (system_prompt, messages, strip_replay)."""
    name = _OVERFLOW_RUNGS[min(rung, len(_OVERFLOW_RUNGS) - 1)]
    anchor_before = int(getattr(session.session_manager, "summary_anchor", 0) or 0)
    strip_replay = False
    if name in ("compact", "strip_degrade"):
        # Late-bind through loop_body so monkeypatching
        # `loop_body._aggressive_compact_for_overflow` (the documented
        # public seam, used by tests) intercepts this call even though
        # the implementation moved here.
        _lb = sys.modules.get("mu.agent.loop_body")
        _compact_fn = getattr(_lb, "_aggressive_compact_for_overflow", None) or _aggressive_compact_for_overflow
        if name == "compact":
            _compact_fn(session, system_prompt, messages, overflow_error=exc,
                        keep_recent=4, margin=0.20, lift_floor=False, tools=tools)
        else:
            strip_replay = True
            _compact_fn(session, system_prompt, messages, overflow_error=exc,
                        keep_recent=2, margin=0.30, lift_floor=True, tools=tools)
    elif name == "full_compact":
        strip_replay = True
        _full_compact_for_overflow(session)
    else:
        strip_replay = True
        _truncate_oversized_messages(session, system_prompt, tools)
    system_prompt, messages = _rebuild_request(
        session, system_prompt, turn_start_index, tools, anchor_before=anchor_before,
    )
    return system_prompt, messages, strip_replay


_RUNG_NOTICES = {
    "compact": "Context overflow from the model — compacting older history into a summary and retrying (no data lost).",
    "strip_degrade": "Context still too large — dropping replayed reasoning and shrinking large tool results, then retrying.",
    "full_compact": "Context still too large — compacting the whole conversation into a summary (like /compact) and retrying.",
    "truncate": "Context still too large — truncating the single oversized message so the request fits, then retrying.",
}


def _generate_with_overflow_recovery(
    session, *, messages, system_prompt, thinking, tools, turn_start_index,
):
    """Provider generate with a reactive context-overflow recovery ladder.

    If the provider (or the job relay) rejects the prompt as too long, the
    turn does not end: recovery climbs ``_OVERFLOW_RUNGS`` — aggressive
    compaction, then strip replay blobs + degrade large (even protected)
    tool results, then a full ``/compact``-equivalent roll into L2, then
    truncation of any single message larger than the window — rebuilding the
    prompt and retrying the provider after each rung. The retry lives inside
    the same loop, so a retry that still overflows is caught and climbs the
    next rung instead of surfacing as "API Error during agentic loop".

    A rung that shrinks the estimated request by less than
    ``_MIN_OVERFLOW_SHRINK`` (10%) is not retried as-is: the next rung is
    applied first. The per-turn circuit breaker
    (``_overflow_recoveries_this_turn``, capped at ``_max_overflow_rungs``)
    counts rungs applied; only when it trips does the overflow re-raise, and
    the loop turns that into a ``context_overflow`` result (job stages then
    retry once with a fresh, summary-seeded conversation).

    The compaction budget is drift-corrected from the error's ground truth
    (see `_aggressive_compact_for_overflow`); estimates count visible content
    only, so replay blobs never make the request look larger than it is.
    """
    from providers.base import REPLAY_NONE, project_replay

    rung = 0
    strip_replay = False
    first_notice = True
    while True:
        send = project_replay(messages, REPLAY_NONE) if strip_replay else messages
        try:
            return session._provider_generate_with_retry(
                messages=send,
                system_prompt=system_prompt,
                thinking=thinking,
                tools=tools,
            )
        except Exception as exc:
            if not is_context_overflow_error(exc):
                raise
            cap = _max_overflow_rungs(session)
            while True:
                recoveries = int(getattr(session, "_overflow_recoveries_this_turn", 0) or 0)
                if recoveries >= cap:
                    logger.error(
                        "Context overflow persisted after %d recovery rungs this "
                        "turn; re-raising (circuit breaker): %s",
                        recoveries, str(exc)[:300],
                    )
                    raise exc
                session._overflow_recoveries_this_turn = recoveries + 1
                name = _OVERFLOW_RUNGS[min(rung, len(_OVERFLOW_RUNGS) - 1)]
                before = _estimate_request_tokens(system_prompt, messages, tools)["total"] if messages else 0
                if session.ui and (first_notice or name != "compact"):
                    session.ui.show_info(_RUNG_NOTICES[name])
                first_notice = False
                logger.warning(
                    "Reactive overflow recovery #%d (rung %s): provider rejected the "
                    "prompt as too long. Error: %s",
                    recoveries + 1, name, str(exc)[:300],
                )
                system_prompt, messages, strip_now = _apply_overflow_rung(
                    session, rung, exc=exc, system_prompt=system_prompt,
                    messages=messages, tools=tools, turn_start_index=turn_start_index,
                )
                strip_replay = strip_replay or strip_now
                rung += 1
                if before <= 0 or rung >= len(_OVERFLOW_RUNGS):
                    break  # unmeasurable, or the last rung: retry the provider
                after = _estimate_request_tokens(system_prompt, messages, tools)["total"]
                if before - after >= before * _MIN_OVERFLOW_SHRINK:
                    break
                logger.warning(
                    "Overflow rung %s shrank the request only %d -> %d tokens "
                    "(< %.0f%%); escalating before retrying.",
                    name, before, after, _MIN_OVERFLOW_SHRINK * 100,
                )


from mu.agent.teacher_watcher import (  # noqa: F401
    _active_teacher_lesson,
    _run_teacher_watcher_user,
    _run_teacher_watcher_assistant,
    _persist_teacher_course,
    _render_learner_profile_block,
)
