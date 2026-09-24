"""Selective, reversible context edits. Durable history is never rewritten."""
from __future__ import annotations

import json
import time
from typing import Any

from .tool_cache import ToolResultCache


def result_id(part: dict) -> str:
    return str(part.get("cache_key") or result_fingerprint(part))


def result_fingerprint(part: dict) -> str:
    return ToolResultCache._make_key(
        "context", str(part.get("tool_name") or "tool"), part.get("tool_result")
    )


def result_locator(part: dict, history_index: int, part_index: int) -> str:
    # Identical receipts from distinct operations remain separately selectable.
    return f"{result_id(part)}@{history_index}:{part_index}"


def retention_for_parts(sm) -> dict:
    """Ephemeral object lookup, rebuilt and fingerprint-checked on every use."""
    decisions = {}
    for value in getattr(sm, "context_retention", {}).values():
        index, part_index = value.get("history_index"), value.get("part_index")
        if not isinstance(index, int) or not isinstance(part_index, int) or not 0 <= index < len(sm.history):
            continue
        parts = sm.history[index].get("parts", [])
        if 0 <= part_index < len(parts) and result_fingerprint(parts[part_index]) == value.get("fingerprint"):
            decisions[id(parts[part_index])] = value
    return decisions


def result_failed(part: dict) -> bool:
    raw = part.get("tool_result")
    if isinstance(raw, dict):
        return (raw.get("ok") is False or bool(raw.get("error_code") or raw.get("error"))
                or raw.get("status") in {"pending", "running", "awaiting_approval"})
    return str(raw or "").lstrip().lower().startswith(("error", "failed", "denied"))


def protected_indices(sm, *, include_signatures=False) -> set[int]:
    """Keep pinned evidence, failures, and their call bundles together.

    Signed bundles may be archived whole, but never edited individually.
    """
    protected = set(getattr(sm, "protected_indices", set()))
    retention = retention_for_parts(sm)
    signed_bundle = False
    for index, message in enumerate(sm.history):
        parts = message.get("parts", [])
        if message.get("role") in {"user", "assistant"}:
            signed_bundle = any(part.get("thought_signature") for part in parts)
        if include_signatures and (signed_bundle or any(part.get("thought_signature") for part in parts)):
            protected.add(index)
        for part in parts:
            if part.get("type") != "tool_result":
                continue
            if result_failed(part) or retention.get(id(part), {}).get("action") == "keep":
                protected.add(index)
                # All parallel calls in this assistant message must survive.
                for previous in range(index - 1, -1, -1):
                    candidate = sm.history[previous]
                    if candidate.get("role") == "user":
                        break
                    if any(p.get("type") == "tool_call" for p in candidate.get("parts", [])):
                        protected.update(range(previous, index + 1))
                        break
    # A pin/failure in one parallel result retains its entire call bundle,
    # including sibling results that occur after the protected result.
    bundle = []
    for index, message in enumerate(sm.history + [{"role": "user"}]):
        if message.get("role") in {"user", "assistant"}:
            if any(item in protected for item in bundle):
                protected.update(bundle)
            bundle = []
            if any(part.get("type") == "tool_call" for part in message.get("parts", [])):
                bundle = [index]
        elif bundle:
            bundle.append(index)
    return protected


def projected_messages(session):
    """Estimate the complete unsummarized projection, before tail selection."""
    sm = session.session_manager
    anchor = max(0, min(int(sm.summary_anchor or 0), len(sm.history)))
    indexes = sorted(protected_indices(sm) | set(range(anchor, len(sm.history))))
    history = [sm.history[index] for index in indexes if 0 <= index < len(sm.history)]
    return session._build_messages_from_history(history, {"role": "system", "parts": []})[:-1]


def projected_tokens(session) -> int:
    from mu.agent.context_guard import _estimate_request_tokens
    return int(_estimate_request_tokens("", projected_messages(session))["messages"])


def context_status(session, *, candidate_offset=0, candidate_limit=30) -> dict:
    from .budgets import drift_corrected_context_limit, effective_fill, resolve_response_reserve
    sm = session.session_manager
    # The current injected prefix is available during tool execution. Keep
    # the request's fixed/tool overhead from the last measured manifest.
    previous = getattr(session, "_request_estimate_manifest", None) or {}
    history_tokens = projected_tokens(session)
    fixed = int(previous.get("system", 0)) + int(previous.get("tools", 0))
    limit = drift_corrected_context_limit(session)
    reserve = resolve_response_reserve(session)
    if (type(candidate_offset) is not int or candidate_offset < 0
            or type(candidate_limit) is not int or not 1 <= candidate_limit <= 100):
        raise ValueError("candidate_offset must be nonnegative and candidate_limit must be 1..100")
    all_candidates = candidates(session)
    end = candidate_offset + candidate_limit
    fill = effective_fill(session, fixed + history_tokens, limit=limit)
    return {
        "projected_history_tokens": history_tokens,
        "stored_history_tokens": sm.estimate_runtime_history_tokens(),
        "request_tokens": fixed + history_tokens,
        "fixed_tokens": fixed,
        "effective_context_limit": limit,
        "response_reserve": reserve,
        "available_tokens": max(0, limit - reserve - fixed - history_tokens),
        # Real-frame view: what the provider will actually count.
        "fill_pct_real": fill["fill_pct"],
        "real_request_tokens": fill["real_tokens"],
        "real_context_limit": fill["real_limit"],
        "drift_ratio": fill["drift_ratio"],
        "fill_source": fill["source"],
        "checkpoint": getattr(sm, "context_checkpoint", {}),
        "summary_usage": getattr(sm, "_summary_usage", {}),
        "fixed_tokens_source": "last_provider_request",
        "candidates": all_candidates[candidate_offset:end],
        "candidate_count": len(all_candidates),
        "next_candidate_offset": end if end < len(all_candidates) else None,
    }


def candidates(session, *, floor_override: int | None = None) -> list[dict]:
    """Every selectable tool result at/after the summary anchor with its
    retention state and, when it cannot be cleared, the reason.

    ``floor_override`` replaces the in-turn ``tool_result_floor`` (how many
    trailing results count as ``recent``). The turn-boundary fold passes a
    smaller value because the turn is over and its trailing results are no
    longer evidence the model is mid-way through consuming.
    """
    from .budgets import resolve_tool_result_floor
    sm = session.session_manager
    protected = protected_indices(sm, include_signatures=True)
    retention = getattr(sm, "context_retention", {})
    valid_retention = retention_for_parts(sm)
    retained_indexes = {value.get("history_index") for value in retention.values() if value.get("action") == "keep"}
    result_indexes = [i for i, m in enumerate(sm.history) if (i >= sm.summary_anchor or i in retained_indexes) and any(
        p.get("type") == "tool_result" for p in m.get("parts", [])
    )]
    if floor_override is None:
        floor = resolve_tool_result_floor(session)
    else:
        floor = max(0, int(floor_override))
    recent = set(result_indexes[-floor:]) if floor else set()
    if recent:
        for index in range(min(recent) - 1, -1, -1):
            message = sm.history[index]
            if message.get("role") == "user":
                break
            if any(part.get("type") == "tool_call" for part in message.get("parts", [])):
                recent.update(range(index, len(sm.history)))
                break
    items = []
    for index in result_indexes:
        for part_index, part in enumerate(sm.history[index].get("parts", [])):
            if part.get("type") != "tool_result":
                continue
            key = result_locator(part, index, part_index)
            reason = (
                "provider_signature" if part.get("thought_signature") else
                "protected" if index in protected else
                "recent" if index in recent else ""
            )
            items.append({"result_id": key, "history_index": index,
                          "part_index": part_index, "tool": part.get("tool_name"),
                          "state": valid_retention.get(id(part), {}).get("action", "active"),
                          "blocked_reason": reason})
    return items


def fold_completed_turn(session, *, turn_start_index: int | None = None) -> dict:
    """Turn-boundary fold (context_packaging_v2 P2).

    Move the FINISHED turn's completed tool results into the durable result
    store via :func:`edit_tool_results` (retention action ``clear``), so the
    next turn's prompt carries a one-line stub per result instead of the
    payload, while ``recall(cache_key)`` still returns the exact text. No
    summarizer call. Honors ``turn_fold_enabled`` /
    ``turn_fold_keep_recent_results``; never touches protected, failed,
    provider-signed or already-cleared results; a no-op when the durable
    store is unavailable (``edit_tool_results`` refuses to clear without
    verified durable recall).

    ``turn_start_index`` bounds the fold to the turn that just ended;
    defaults to ``session_manager._active_turn_start_index``. Returns the
    ``edit_tool_results`` summary (``changed`` empty when nothing folded)
    plus ``eligible`` (number of clearable candidates seen).
    """
    variables = getattr(session, "variables", None) or {}
    empty = {"ok": True, "action": "clear", "changed": [], "skipped": [], "eligible": 0,
             "before_tokens": 0, "after_tokens": 0, "saved_tokens": 0, "summarizer_calls": 0}
    if not variables.get("turn_fold_enabled", True):
        return {**empty, "reason": "disabled"}
    sm = getattr(session, "session_manager", None)
    if sm is None or not getattr(sm, "history", None):
        return {**empty, "reason": "no_history"}
    store = getattr(getattr(sm, "tool_result_cache", None), "_store", None)
    if store is None:
        return {**empty, "reason": "durable_store_unavailable"}
    try:
        keep = max(0, int(variables.get("turn_fold_keep_recent_results", 2)))
    except (TypeError, ValueError):
        keep = 2
    if turn_start_index is None:
        turn_start_index = getattr(sm, "_active_turn_start_index", None)
    try:
        start = int(turn_start_index) if turn_start_index is not None else 0
    except (TypeError, ValueError):
        start = 0
    start = max(0, min(start, len(sm.history)))
    selected = [
        item["result_id"]
        for item in candidates(session, floor_override=keep)
        if item["history_index"] >= start
        and not item["blocked_reason"]
        and item["state"] != "clear"
    ]
    if not selected:
        return {**empty, "reason": "nothing_clearable"}
    result: dict = {**empty, "eligible": len(selected)}
    # edit_tool_results caps at 100 ids per call; fold in batches.
    for offset in range(0, len(selected), 100):
        batch = selected[offset:offset + 100]
        try:
            sm._pending_shrink_kind = "turn_fold"
            part = edit_tool_results(session, batch, floor_override=keep)
        finally:
            sm._pending_shrink_kind = None
        result["changed"] = list(result["changed"]) + list(part.get("changed", []))
        result["skipped"] = list(result["skipped"]) + list(part.get("skipped", []))
        if offset == 0:
            result["before_tokens"] = part.get("before_tokens", 0)
        result["after_tokens"] = part.get("after_tokens", 0)
    result["saved_tokens"] = max(0, int(result["before_tokens"]) - int(result["after_tokens"]))
    result["reason"] = "folded" if result["changed"] else "nothing_changed"
    return result


def projected_turn_tokens(session, turn_start_index: int) -> int:
    """Projected (post-retention, post-stub) tokens of history[turn_start:]."""
    from mu.agent.context_guard import _estimate_request_tokens
    sm = session.session_manager
    start = max(0, min(int(turn_start_index or 0), len(sm.history)))
    history = sm.history[start:]
    if not history:
        return 0
    messages = session._build_messages_from_history(history, {"role": "system", "parts": []})[:-1]
    return int(_estimate_request_tokens("", messages)["messages"])


def roll_completed_turn(session, *, turn_start_index: int | None = None, provider=None) -> dict:
    """Turn-boundary roll (context_packaging_v2 P2).

    When the FINISHED turn still projects above ``turn_keep_budget_tokens``
    after the fold and arg stubs, roll it into the L2 conversation summary
    via ``roll_history_summary`` bounded by ``through_index`` (the turn's
    last message) with ``keep_recent=1`` so the final assistant answer
    stays live. The summary anchor advancing also re-arms the context
    pressure nudge. Ledger kind ``turn_roll``. Returns a small report and
    never raises.
    """
    variables = getattr(session, "variables", None) or {}
    report = {"rolled": False, "reason": "", "turn_tokens": 0, "budget": 0,
              "anchor_before": 0, "anchor_after": 0}
    if not variables.get("turn_roll_enabled", True):
        return {**report, "reason": "disabled"}
    sm = getattr(session, "session_manager", None)
    if sm is None or not getattr(sm, "history", None):
        return {**report, "reason": "no_history"}
    try:
        budget = max(0, int(variables.get("turn_keep_budget_tokens", 6000)))
    except (TypeError, ValueError):
        budget = 6000
    if turn_start_index is None:
        turn_start_index = getattr(sm, "_active_turn_start_index", None)
    try:
        start = int(turn_start_index) if turn_start_index is not None else 0
    except (TypeError, ValueError):
        start = 0
    start = max(0, min(start, len(sm.history)))
    anchor_before = int(getattr(sm, "summary_anchor", 0) or 0)
    report["anchor_before"] = anchor_before
    report["budget"] = budget
    try:
        turn_tokens = projected_turn_tokens(session, start)
    except Exception:  # noqa: BLE001
        turn_tokens = 0
    report["turn_tokens"] = turn_tokens
    if turn_tokens <= budget:
        return {**report, "reason": "under_budget", "anchor_after": anchor_before}
    end_index = len(sm.history) - 1
    if end_index <= anchor_before:
        return {**report, "reason": "nothing_to_roll", "anchor_after": anchor_before}
    provider = provider if provider is not None else getattr(session, "provider", None)
    snap = sm._shrink_snapshot() if hasattr(sm, "_shrink_snapshot") else None
    sm._compact_focus = variables.get("compact_focus") or ""
    # The turn is over: its tool results are no longer floor-protected.
    prev_floor = getattr(sm, "_tool_result_floor", 0)
    sm._tool_result_floor = 0
    sm._pending_compaction_kind = "turn_roll"
    try:
        rolled = bool(sm.roll_history_summary(
            keep_recent=1, provider=provider, through_index=end_index,
        ))
    finally:
        sm._tool_result_floor = prev_floor
        sm._pending_compaction_kind = None
    anchor_after = int(getattr(sm, "summary_anchor", 0) or 0)
    if rolled and snap is not None and hasattr(sm, "_record_shrink_since"):
        sm._record_shrink_since(
            snap, kind="turn_roll",
            summarizer=getattr(sm, "_last_summary_mode", "unknown"),
            keep_recent=1, turn_tokens=turn_tokens, budget=budget,
        )
    return {**report, "rolled": rolled, "anchor_after": anchor_after,
            "reason": "rolled" if rolled else "roll_declined"}


def validate_checkpoint(checkpoint: Any) -> dict:
    if checkpoint is None:
        return {}
    fields = {"progress", "decisions", "resume", "exceptions", "next_action", "constraints"}
    if not isinstance(checkpoint, dict) or set(checkpoint) - fields:
        raise ValueError("checkpoint must contain only progress, decisions, resume, exceptions, next_action, constraints")
    if any(not isinstance(value, str) for value in checkpoint.values()):
        raise ValueError("checkpoint fields must be strings")
    if len(json.dumps(checkpoint, ensure_ascii=False)) > 6000:
        raise ValueError("checkpoint exceeds 6000 characters; shorten it explicitly")
    return dict(checkpoint)


def edit_tool_results(session, result_ids, *, action="clear", checkpoint=None,
                      floor_override: int | None = None) -> dict:
    """Clear only named, completed results after verifying durable recall.

    ``floor_override`` is forwarded to :func:`candidates` so a caller that
    legitimately operates with a different recency floor (the turn-boundary
    fold) sees the same eligibility it selected against.
    """
    started = time.monotonic()
    if action not in {"clear", "keep", "restore"}:
        raise ValueError("action must be clear, keep, or restore")
    if not isinstance(result_ids, list) or len(result_ids) > 100 or any(
        not isinstance(key, str) or not key or len(key) > 128 for key in result_ids
    ):
        raise ValueError("result_ids must be a list of at most 100 nonempty identifiers")
    sm = session.session_manager
    checkpoint = validate_checkpoint({**getattr(sm, "context_checkpoint", {}), **validate_checkpoint(checkpoint)})
    before = projected_tokens(session)
    retention = dict(getattr(sm, "context_retention", {}))
    known = {item["result_id"]: item for item in candidates(session, floor_override=floor_override)}
    changed, skipped = [], []
    for key in dict.fromkeys(result_ids):
        item = known.get(key)
        if item is None:
            skipped.append({"result_id": key, "reason": "unknown_result"})
            continue
        if action == "restore":
            retention.pop(key, None)
            changed.append(key)
            continue
        if action == "keep":
            part = sm.history[item["history_index"]]["parts"][item["part_index"]]
            retention[key] = {"action": "keep", "history_index": item["history_index"], "part_index": item["part_index"],
                              "fingerprint": result_fingerprint(part)}
            changed.append(key)
            continue
        if item["blocked_reason"]:
            skipped.append({"result_id": key, "reason": item["blocked_reason"]})
            continue
        if item["state"] == "clear":
            skipped.append({"result_id": key, "reason": "already_cleared"})
            continue
        part = sm.history[item["history_index"]]["parts"][item["part_index"]]
        cache = sm.tool_result_cache
        store = getattr(cache, "_store", None)
        if store is None:
            skipped.append({"result_id": key, "reason": "durable_store_unavailable"})
            continue
        # History already contains the approved/scrubbed tool result. Store
        # that exact payload; never re-execute the original tool for recall.
        cache_key = result_id(part)
        try:
            store.put(cache_key, str(part.get("tool_name") or "tool"), None, part.get("tool_result"))
            saved = store.get(cache_key)
            if saved is None or saved.get("result") != part.get("tool_result"):
                raise ValueError("durable result verification failed")
        except Exception:
            skipped.append({"result_id": key, "reason": "durable_store_failed"})
            continue
        retention[key] = {"action": "clear", "cache_key": cache_key,
                          "history_index": item["history_index"], "part_index": item["part_index"],
                          "fingerprint": result_fingerprint(part)}
        changed.append(key)
    sm.context_retention = retention
    if checkpoint:
        sm.context_checkpoint = {**getattr(sm, "context_checkpoint", {}), **checkpoint}
    after = projected_tokens(session)
    result = {"ok": True, "action": action, "changed": changed, "skipped": skipped,
              "before_tokens": before, "after_tokens": after,
              "saved_tokens": max(0, before - after), "summarizer_calls": 0,
              "elapsed_ms": round((time.monotonic() - started) * 1000, 2)}
    sm._last_context_edit = {name: value for name, value in result.items() if name not in {"changed", "skipped"}}
    sm._last_context_edit.update(changed_count=len(changed), skipped_count=len(skipped),
                                 iteration=getattr(session, "_trace_current_iter", None))
    if action == "clear" and changed and after < before and hasattr(sm, "record_history_shrink"):
        # Projected-context shrink with no summarizer: still a history-size
        # change the trace must be able to explain.
        sm._pending_compaction_iter = int(getattr(session, "_trace_current_iter", 0) or 0)
        sm.record_history_shrink(
            kind=str(getattr(sm, "_pending_shrink_kind", None) or "result_clear"),
            tokens_before=before, tokens_after=after,
            msgs_before=len(sm.history), anchor_before=int(getattr(sm, "summary_anchor", 0) or 0),
            tokens_basis="projected", cleared=len(changed),
        )
    return result


def recall_cleared_result(sm, cache_key):
    """Recover receipts even after per-run cache GC or a server restart."""
    for key, decision in getattr(sm, "context_retention", {}).items():
        if decision.get("action") != "clear" or decision.get("cache_key") != cache_key:
            continue
        index, part_index = decision.get("history_index"), decision.get("part_index")
        if not isinstance(index, int) or not isinstance(part_index, int):
            continue
        if not (0 <= index < len(sm.history)):
            continue
        parts = sm.history[index].get("parts", [])
        if 0 <= part_index < len(parts) and result_fingerprint(parts[part_index]) == decision.get("fingerprint"):
            part = parts[part_index]
            return {"tool_name": part.get("tool_name"), "result": part.get("tool_result"),
                    "cache_key": cache_key, "from_history": True}
    return None
