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
    from .budgets import drift_corrected_context_limit, resolve_response_reserve
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
    return {
        "projected_history_tokens": history_tokens,
        "stored_history_tokens": sm.estimate_runtime_history_tokens(),
        "request_tokens": fixed + history_tokens,
        "fixed_tokens": fixed,
        "effective_context_limit": limit,
        "response_reserve": reserve,
        "available_tokens": max(0, limit - reserve - fixed - history_tokens),
        "checkpoint": getattr(sm, "context_checkpoint", {}),
        "summary_usage": getattr(sm, "_summary_usage", {}),
        "fixed_tokens_source": "last_provider_request",
        "candidates": all_candidates[candidate_offset:end],
        "candidate_count": len(all_candidates),
        "next_candidate_offset": end if end < len(all_candidates) else None,
    }


def candidates(session) -> list[dict]:
    from .budgets import resolve_tool_result_floor
    sm = session.session_manager
    protected = protected_indices(sm, include_signatures=True)
    retention = getattr(sm, "context_retention", {})
    valid_retention = retention_for_parts(sm)
    retained_indexes = {value.get("history_index") for value in retention.values() if value.get("action") == "keep"}
    result_indexes = [i for i, m in enumerate(sm.history) if (i >= sm.summary_anchor or i in retained_indexes) and any(
        p.get("type") == "tool_result" for p in m.get("parts", [])
    )]
    floor = resolve_tool_result_floor(session)
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


def edit_tool_results(session, result_ids, *, action="clear", checkpoint=None) -> dict:
    """Clear only named, completed results after verifying durable recall."""
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
    known = {item["result_id"]: item for item in candidates(session)}
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
