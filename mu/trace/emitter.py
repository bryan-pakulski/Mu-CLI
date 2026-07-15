"""Per-run JSONL trace emitter.

Records one structured event per line to
``$MUCLI_HOME/trace/<session>_run_<hex12>.jsonl``. The record types are:

  * ``run_start``  — one header line per run (model, provider, mode, limits)
  * ``iter``       — one per agent-loop iteration, captured at the post-response
                     seam (context layers, real vs estimated tokens, drift,
                     subagent/memory snapshots, compaction summary)
  * ``tool``       — one per tool call (standalone; joined to iters by ``iter``)
  * ``nudge``      — one per corrective nudge injection (standalone)
  * ``compaction`` — one per compaction pass (standalone; drained from the
                     session manager's ``_compaction_log``)
  * ``turn_end``   — one per turn, with totals; flushes the file

The headline field is ``iter.context.drift_pct``: the signed percent difference
between the provider's *real* prompt token count (``response.input_tokens``) and
the harness's tiktoken ``cl100k_base`` estimate (the sum of context-layer
estimates). On a model whose tokenizer is not cl100k_base (e.g. glm), this drift
is systematic and is the primary signal for diagnosing long-horizon compaction
failures.

All public methods are no-ops once disabled/closed, and every write is wrapped
so an I/O error cannot propagate into the agent loop.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from typing import Any, Dict, List, Optional


logger = logging.getLogger("mucli")


def trace_dir() -> str:
    """Return the trace directory under ``$MUCLI_HOME`` (lazy-created on write)."""
    from utils.config import HISTORY_DIR

    return os.path.join(os.path.expanduser(str(HISTORY_DIR)), "trace")


def new_run_id() -> str:
    """Generate a run id matching the codebase's ``turn_id``/``sa-`` conventions."""
    return "run_" + uuid.uuid4().hex[:12]


def _safe_name(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", name or "session").strip("_") or "session"
    return s[:64]


class TraceEmitter:
    """Append-only JSONL writer for one run. Thread-safe; lazy-opens the file."""

    def __init__(self, session_name: str, run_id: str, path: str) -> None:
        self.session_name = session_name
        self.run_id = run_id
        self.path = path
        self._lock = threading.Lock()
        self._fh: Optional[Any] = None
        self._closed = False
        self.iter_count = 0

    # ----------------------------------------------------------- low-level

    def _open(self) -> None:
        if self._fh is None:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            self._fh = open(self.path, "a", encoding="utf-8")

    def emit(self, record: Dict[str, Any]) -> None:
        """Append one JSON line. Swallows all errors (telemetry must not break runs)."""
        if self._closed:
            return
        try:
            line = json.dumps(record, default=str, ensure_ascii=False)
            with self._lock:
                self._open()
                self._fh.write(line + "\n")
                self._fh.flush()
        except Exception as exc:  # noqa: BLE001
            logger.debug("trace emit failed: %s", exc)

    def flush(self) -> None:
        try:
            with self._lock:
                if self._fh is not None:
                    self._fh.flush()
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        try:
            with self._lock:
                if self._fh is not None:
                    self._fh.close()
                self._fh = None
                self._closed = True
        except Exception:  # noqa: BLE001
            pass

    # ----------------------------------------------------------- typed events

    def run_start(self, meta: Dict[str, Any]) -> None:
        rec: Dict[str, Any] = {"type": "run_start", "run_id": self.run_id}
        rec.update(meta)
        self.emit(rec)

    def iter_record(self, rec: Dict[str, Any]) -> None:
        self.iter_count += 1
        out = {"type": "iter", "run_id": self.run_id}
        out.update(rec)
        self.emit(out)

    def tool(self, rec: Dict[str, Any]) -> None:
        out = {"type": "tool", "run_id": self.run_id}
        out.update(rec)
        self.emit(out)

    def nudge(self, kind: str, iteration: int, **extra: Any) -> None:
        out = {"type": "nudge", "run_id": self.run_id, "kind": kind, "iteration": iteration}
        out.update(extra)
        self.emit(out)

    def compaction(self, rec: Dict[str, Any]) -> None:
        out = {"type": "compaction", "run_id": self.run_id}
        out.update(rec)
        self.emit(out)

    def turn_end(self, rec: Dict[str, Any]) -> None:
        out = {"type": "turn_end", "run_id": self.run_id}
        out.update(rec)
        self.emit(out)
        self.flush()


# ----------------------------------------------------------- accessor

def get_emitter(session: Any) -> Optional[TraceEmitter]:
    """Return the session's cached emitter, or build one. ``None`` when disabled.

    Caches on ``session._trace_emitter``. Generates ``session._trace_run_id``
    lazily. Never raises — a trace failure must not break the agent loop.
    """
    try:
        variables = getattr(session, "variables", None) or {}
        if not bool(variables.get("trace_enabled", True)):
            return None
        em = getattr(session, "_trace_emitter", None)
        if em is not None and not em._closed:
            return em
        sm = getattr(session, "session_manager", None)
        run_id = getattr(session, "_trace_run_id", None) or new_run_id()
        session._trace_run_id = run_id
        name = _safe_name(getattr(sm, "current_session_name", "") or "session")
        path = os.path.join(trace_dir(), f"{name}_{run_id}.jsonl")
        em = TraceEmitter(name, run_id, path)
        session._trace_emitter = em
        return em
    except Exception as exc:  # noqa: BLE001
        logger.debug("trace emitter init failed: %s", exc)
        return None


# ----------------------------------------------------------- convenience emit

def emit_nudge(session: Any, kind: str, iteration: int, **extra: Any) -> None:
    """One-line nudge emit for the loop's injection sites. Never raises."""
    try:
        em = get_emitter(session)
        if em is not None:
            em.nudge(kind, iteration, **extra)
    except Exception:  # noqa: BLE001
        pass


def emit_tool(
    session: Any,
    *,
    iteration: int,
    name: str,
    arg_fp: str = "",
    ok: Optional[bool] = None,
    error_code: Optional[str] = None,
    latency_ms: int = 0,
    cache_hit: bool = False,
    result_bytes: int = 0,
    path: str = "",
    preview: str = "",
) -> None:
    """One-line per-tool emit for the post-execution capture site. Never raises."""
    try:
        em = get_emitter(session)
        if em is not None:
            em.tool(
                {
                    "iter": iteration,
                    "name": name,
                    "arg_fp": arg_fp,
                    "ok": ok,
                    "error_code": error_code,
                    "latency_ms": int(latency_ms or 0),
                    "cache_hit": bool(cache_hit),
                    "result_bytes": int(result_bytes or 0),
                    "path": path,
                    "preview": (preview or "")[:200],
                }
            )
    except Exception:  # noqa: BLE001
        pass


# ----------------------------------------------------------- record builders

def _layer_tokens(session: Any) -> Dict[str, Any]:
    """Sum context-layer estimates via the harness's own estimator (cl100k_base).

    Returns ``{l0,l1,l1b,l2,l3,l4b,l5,total_est}``. Each value is the layer's
    estimated token ``current``; ``total_est`` is their sum — the harness's
    estimate of the assembled prompt, directly comparable to
    ``response.input_tokens``.
    """
    try:
        from utils.runtime_metrics import collect_context_layers

        layers = collect_context_layers(session) or []
    except Exception:  # noqa: BLE001
        layers = []
    out: Dict[str, int] = {}
    total = 0
    for layer in layers:
        key = (layer.get("layer") or "").lower()  # "l0","l1","l1b","l2","l3","l4b","l5"
        try:
            val = int(layer.get("current") or 0)
        except Exception:  # noqa: BLE001
            val = 0
        out[key] = val
        total += val
    return {
        "l0": out.get("l0", 0),
        "l1": out.get("l1", 0),
        "l1b": out.get("l1b", 0),
        "l2": out.get("l2", 0),
        "l3": out.get("l3", 0),
        "l4b": out.get("l4b", 0),
        "l5": out.get("l5", 0),
        "total_est": total,
    }


def _subagent_snapshot(session: Any) -> Dict[str, Any]:
    try:
        reg = getattr(session, "_subagent_registry", None)
        if reg is None:
            return {"active": 0, "stuck": 0, "stall": 0, "children": []}
        snap = reg.snapshot_all()
        children = []
        for s in snap:
            children.append(
                {
                    "task_id": s.get("task_id"),
                    "depth": s.get("depth"),
                    "status": s.get("status"),
                    "stuck": bool(s.get("stuck")),
                    "stall": bool(s.get("stall")),
                    "tool_calls": s.get("tool_calls"),
                    "elapsed": s.get("elapsed"),
                }
            )
        return {
            "active": sum(1 for s in snap if s.get("status") == "running"),
            "stuck": sum(1 for s in snap if s.get("stuck")),
            "stall": sum(1 for s in snap if s.get("stall")),
            "children": children,
        }
    except Exception:  # noqa: BLE001
        return {"active": 0, "stuck": 0, "stall": 0, "children": []}


def _memory_counts(session: Any) -> Dict[str, Any]:
    try:
        sm = session.session_manager
        tm = getattr(sm, "task_memory", None)
        entries = list(getattr(tm, "entries", []) or [])
        by_status: Dict[str, int] = {}
        for e in entries:
            st = getattr(e, "status", None) or "unknown"
            by_status[st] = by_status.get(st, 0) + 1
        sp = getattr(sm, "turn_scratchpad", None)
        scratch = len(getattr(sp, "entries", []) or [])
        return {
            "task_memory_count": len(entries),
            "by_status": by_status,
            "scratchpad_count": scratch,
        }
    except Exception:  # noqa: BLE001
        return {"task_memory_count": 0, "by_status": {}, "scratchpad_count": 0}


def drain_compactions(session: Any) -> List[Dict[str, Any]]:
    """Pull pending compaction entries off the session manager and clear the log.

    Called by the loop at the post-response seam so each compaction pass that
    fired before this iteration's provider response is emitted as a standalone
    trace line.
    """
    try:
        sm = session.session_manager
        log = getattr(sm, "_compaction_log", None)
        if not log:
            return []
        out = list(log)
        log.clear()
        return out
    except Exception:  # noqa: BLE001
        return []


def build_iter_record(
    session: Any,
    *,
    iteration: int,
    max_iter: int,
    response: Any,
    total_in: int,
    total_out: int,
    total_cost: float,
    has_text: bool,
    has_tool_call: bool,
    iter_start: float,
    cost_delta: float = 0.0,
    compaction: Optional[Dict[str, Any]] = None,
    status: str = "running",
) -> Dict[str, Any]:
    """Assemble the per-iteration trace record from in-scope loop state.

    Called at the post-response seam (after token accounting, before the
    ``if not has_tool_call`` branch). ``compaction`` is the last drained
    compaction entry for this iteration (standalone lines for every entry are
    emitted by the caller). All gathering is defensive.
    """
    import time as _time

    wall_ms = int((_time.monotonic() - iter_start) * 1000)
    layers = _layer_tokens(session)
    total_est = layers["total_est"]
    actual = int(getattr(response, "input_tokens", 0) or 0)
    drift_pct = (
        round((actual - total_est) / max(1, actual) * 100, 2) if actual else 0.0
    )
    # Drift-corrected real-prompt estimate + the drift ratio the compactor is
    # assuming. `actual` (Ollama prompt_eval_count) is the non-cached delta —
    # near-zero in a warm loop and a misleading "real" prompt size. The
    # drift-corrected cl100k estimate is the representative real fill; the
    # ratio makes the cl100k undercount auditable in the trace.
    try:
        from mu.session.budgets import effective_drift_ratio
        eff_drift = float(effective_drift_ratio(session))
    except Exception:  # noqa: BLE001
        eff_drift = 1.0
    real_est = int(total_est * eff_drift)

    tokens = {
        "in": int(getattr(response, "input_tokens", 0) or 0),
        "out": int(getattr(response, "output_tokens", 0) or 0),
        "cached": int(getattr(response, "cached_tokens", 0) or 0),
        "reasoning": int(getattr(response, "reasoning_tokens", 0) or 0),
        "cost_delta": round(float(cost_delta or 0.0), 6),
    }

    # Assistant text preview (first text part, truncated).
    preview = ""
    try:
        for p in getattr(response, "parts", []) or []:
            if getattr(p, "type", None) == "text" and getattr(p, "text", ""):
                preview = (p.text or "").strip()[:240]
                break
    except Exception:  # noqa: BLE001
        preview = ""

    compactions: List[Dict[str, Any]] = []
    # Embed a compact summary of the latest compaction this iteration; full
    # entries are emitted as standalone lines by the caller via drain_compactions.
    last_compaction = compaction

    return {
        "iter": iteration,
        "max_iter": max_iter,
        "wall_ms": wall_ms,
        "context": {
            "l0": layers["l0"],
            "l1": layers["l1"],
            "l1b": layers["l1b"],
            "l2": layers["l2"],
            "l3": layers["l3"],
            "l4b": layers["l4b"],
            "l5": layers["l5"],
            "total_est": total_est,
            "prompt_tokens_actual": actual,
            "prompt_tokens_real_est": real_est,
            "drift_ratio": round(eff_drift, 3),
            "drift_pct": drift_pct,
        },
        "tokens": tokens,
        "has_text": bool(has_text),
        "has_tool_call": bool(has_tool_call),
        "assistant_preview": preview,
        "subagents": _subagent_snapshot(session),
        "memory": _memory_counts(session),
        "compaction": last_compaction,
        "status": status,
    }