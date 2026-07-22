"""Efficiency metrics aggregation (spec #12).

Aggregates per-turn efficiency signals from the durable ResultStore /
ToolResultCache counters and the per-tool-result telemetry stamped by the
observation transform (``raw_token_count``, ``injected_token_count``,
``compression_ratio``, ``delivery_mode``):

  * raw tool-output tokens vs. tokens actually injected
  * compression ratio (raw − injected) / raw
  * cache eviction / invalidation / disk-hit / locator-hit / dup-avoided counts
  * retrieval rate (recall + result_* calls ÷ total tool calls)
  * tool-output share of context (from the request manifest, when available)

The aggregator is pure: it reads counters off the session and the cache, then
returns a dict. The caller (``session._emit_turn_end``) folds it into the
``turn_end`` trace record. ``/memory`` surfaces the latest snapshot.

Counters on the cache are NOT reset here — the cache owns its counters; the
snapshot is a point-in-time read. Per-turn deltas are computed by the caller
if needed (subtract the prior snapshot stored on the session).
"""

from __future__ import annotations

from typing import Any, Dict, Optional


_RETRIEVAL_TOOLS = frozenset(
    {
        "recall",
        "result_range",
        "result_head",
        "result_tail",
        "result_search",
        "result_diagnostics",
        "result_json_path",
        "compare_results",
    }
)


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:  # noqa: BLE001
        return default


def collect_efficiency_metrics(
    session: Any,
    *,
    tool_calls_this_turn: int = 0,
    retrieval_calls_this_turn: int = 0,
    tool_result_tokens: Optional[int] = None,
    total_context_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """Build an efficiency-metrics dict for the current turn.

    Reads the ``ToolResultCache`` counter snapshot and the per-turn
    accumulation that loop_body stamps on the session
    (``_eff_raw_tokens`` / ``_eff_injected_tokens`` / ``_eff_omitted`` /
    ``_eff_retrievals``). All best-effort: missing pieces degrade to zeros.
    """
    metrics: Dict[str, Any] = {}

    # --- Cache counters ---
    cache = getattr(session, "tool_result_cache", None)
    if cache is not None:
        try:
            metrics["cache"] = cache.metrics_snapshot()
        except Exception:  # noqa: BLE001
            metrics["cache"] = {}
    else:
        metrics["cache"] = {}

    # --- Observation compression (per-turn accumulation) ---
    raw = _safe_int(getattr(session, "_eff_raw_tokens", 0))
    injected = _safe_int(getattr(session, "_eff_injected_tokens", 0))
    omitted = _safe_int(getattr(session, "_eff_omitted", 0))
    retrievals = _safe_int(getattr(session, "_eff_retrievals", 0))
    metrics["raw_tool_tokens"] = raw
    metrics["injected_tool_tokens"] = injected
    metrics["omitted_results"] = omitted
    metrics["compression_ratio"] = (
        round((raw - injected) / raw, 3) if raw > 0 else 0.0
    )
    metrics["tokens_saved"] = max(0, raw - injected)

    # --- Retrieval rate ---
    calls = max(0, int(tool_calls_this_turn))
    retr = max(0, int(retrieval_calls_this_turn))
    metrics["retrieval_calls"] = retr
    metrics["tool_calls"] = calls
    metrics["retrieval_rate"] = round(retr / calls, 3) if calls > 0 else 0.0

    # --- Tool-output share of context ---
    if tool_result_tokens is not None and total_context_tokens:
        metrics["tool_result_tokens"] = int(tool_result_tokens)
        metrics["total_context_tokens"] = int(total_context_tokens)
        metrics["tool_output_share"] = (
            round(int(tool_result_tokens) / int(total_context_tokens), 3)
            if int(total_context_tokens) > 0
            else 0.0
        )

    return metrics


def is_retrieval_tool(tool_name: str) -> bool:
    """True for the recall + result_* family (used to count retrievals)."""
    return tool_name in _RETRIEVAL_TOOLS


def reset_per_turn_accumulators(session: Any) -> None:
    """Zero the per-turn efficiency accumulators on the session. Call at
    turn start so each turn's metrics reflect that turn only."""
    try:
        session._eff_raw_tokens = 0
        session._eff_injected_tokens = 0
        session._eff_omitted = 0
        session._eff_retrievals = 0
    except Exception:  # noqa: BLE001
        pass


def accumulate_tool_result(session: Any, structured: Any) -> None:
    """Fold a structured tool result's telemetry into the per-turn
    accumulators. Best-effort; never raises."""
    try:
        if not isinstance(structured, dict):
            return
        tele = structured.get("telemetry")
        if not isinstance(tele, dict):
            return
        session._eff_raw_tokens = _safe_int(
            getattr(session, "_eff_raw_tokens", 0)
        ) + _safe_int(tele.get("raw_token_count"))
        session._eff_injected_tokens = _safe_int(
            getattr(session, "_eff_injected_tokens", 0)
        ) + _safe_int(tele.get("injected_token_count"))
        if tele.get("delivery_mode") == "observed" or (
            isinstance(structured.get("data"), dict)
            and structured["data"].get("omitted")
        ):
            session._eff_omitted = _safe_int(
                getattr(session, "_eff_omitted", 0)
            ) + 1
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "collect_efficiency_metrics",
    "is_retrieval_tool",
    "reset_per_turn_accumulators",
    "accumulate_tool_result",
]