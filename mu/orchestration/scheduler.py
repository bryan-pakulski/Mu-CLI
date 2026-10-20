"""Rollout engine (plan §5): batches, thresholds, pauses, retries, rollback. Pure functions.

A run's frozen **plan** is ``{"steps": [...], "batches": [[node ids], ...]}``. Every node walks
the steps in order (its *pipeline*); batches gate how many nodes start. ``tick`` looks at the
run, its executions and node liveness and returns the actions to take; ``service.py`` applies
them in one transaction. Nothing here touches the database or the clock, which is what makes
the rollout behaviour exhaustively testable.

Rules:

* Canary batch first; any failure there pauses the run. Later batches pause when the share of
  failed nodes exceeds ``failure_threshold`` (percent). A paused run waits for the operator:
  continue, roll back or cancel ("park, don't fail").
* ``max_parallel`` caps nodes with a live execution in the run; ``pause_between_s`` spaces batches.
* A node that stops polling mid-execution: idempotent tools are re-dispatched (up to
  ``max_attempts``); anything else becomes ``unreachable`` and needs a human.
* Executions past their timeout (plus grace) become ``timed_out`` and the node is told to cancel.
* A step's ``on_failure``: ``stop`` (default) ends that node's pipeline, ``continue`` moves on,
  ``rollback`` also queues the rollback tools of the node's completed steps, newest first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .models import Execution, Run
from .states import EXEC_FAILED, EXEC_IN_FLIGHT, EXEC_LIVE

DEFAULTS = {"offline_after_s": 90.0, "grace_s": 30.0, "queue_timeout_s": 300.0, "max_attempts": 3}


# --------------------------------------------------------------------- strategy and batches

def normalize_strategy(raw: Optional[Mapping[str, Any]], rollout: Optional[Mapping[str, Any]] = None
                       ) -> Dict[str, Any]:
    raw = dict(raw or {})
    rollout = dict(rollout or {})

    def _int(key, default, lo, hi):
        try:
            value = int(raw.get(key, default))
        except (TypeError, ValueError):
            value = default
        return max(lo, min(hi, value))

    batch = _int("batch", rollout.get("default_batch", 10), 1, 1000)
    try:
        threshold = float(raw.get("failure_threshold", 10))
    except (TypeError, ValueError):
        threshold = 10.0
    return {
        "canary": _int("canary", rollout.get("canary", 1), 0, 100),
        "batch": batch,
        "max_parallel": _int("max_parallel", batch, 1, 1000),
        "failure_threshold": max(0.0, min(100.0, threshold)),
        "pause_between_s": _int("pause_between_s", 0, 0, 86400),
    }


def make_batches(node_ids: Sequence[str], strategy: Mapping[str, Any],
                 canary_ids: Iterable[str] = ()) -> List[List[str]]:
    """Canary batch first (canary-labelled nodes preferred), then fixed-size batches."""
    canary_pref = [n for n in node_ids if n in set(canary_ids)]
    rest = [n for n in node_ids if n not in set(canary_pref)]
    ordered = canary_pref + rest
    batches: List[List[str]] = []
    canary = int(strategy.get("canary") or 0)
    if canary and len(ordered) > 1:
        batches.append(ordered[:canary])
        ordered = ordered[canary:]
    size = max(1, int(strategy.get("batch") or 1))
    for i in range(0, len(ordered), size):
        batches.append(ordered[i:i + size])
    return [b for b in batches if b]


# --------------------------------------------------------------------- actions

@dataclass
class Action:
    kind: str                     # queue | skip | rollback | pause | advance | finish | timeout |
    #                               unreachable | requeue | start_rollback
    node_id: str = ""
    step: int = -1
    exec_id: str = ""
    state: str = ""
    reason: str = ""
    batch: int = -1
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NodeProgress:
    status: str                   # ready | active | done | failed | waiting_rollback
    step: Optional[int] = None    # next/active step
    failed: bool = False          # any non-skipped step failed
    failed_step: Optional[int] = None


def _latest(execs: Iterable[Execution]) -> Dict[int, Execution]:
    out: Dict[int, Execution] = {}
    for ex in sorted(execs, key=lambda e: (e.created_at, e.attempts)):
        if ex.rollback_of:
            continue
        out[ex.step] = ex
    return out


def node_progress(steps: Sequence[Mapping[str, Any]], execs: Iterable[Execution]) -> NodeProgress:
    execs = list(execs)
    latest = _latest(execs)
    failed, failed_step = False, None
    for i, step in enumerate(steps):
        ex = latest.get(i)
        if ex is None:
            return NodeProgress("ready", i, failed, failed_step)
        if ex.state in EXEC_LIVE:
            return NodeProgress("active", i, failed, failed_step)
        if ex.state in ("succeeded", "skipped"):
            continue
        failed = True
        failed_step = i if failed_step is None else failed_step
        if str(step.get("on_failure") or "stop") == "continue":
            continue
        rollbacks = [e for e in execs if e.rollback_of]
        if any(e.state in EXEC_LIVE for e in rollbacks):
            return NodeProgress("waiting_rollback", i, True, failed_step)
        return NodeProgress("failed", i, True, failed_step)
    rollbacks = [e for e in execs if e.rollback_of]
    if any(e.state in EXEC_LIVE for e in rollbacks):
        return NodeProgress("waiting_rollback", None, failed, failed_step)
    return NodeProgress("done", None, failed, failed_step)


def node_settled(p: NodeProgress) -> bool:
    return p.status in ("done", "failed")


def rollback_targets(steps: Sequence[Mapping[str, Any]], execs: Iterable[Execution]) -> List[Execution]:
    """Succeeded, non-check-mode executions of steps whose tool has a rollback, newest first."""
    execs = list(execs)
    done_rollbacks = {e.rollback_of for e in execs if e.rollback_of}
    out = []
    for ex in sorted((e for e in execs if not e.rollback_of), key=lambda e: e.step, reverse=True):
        if ex.state != "succeeded" or ex.check_mode or ex.id in done_rollbacks:
            continue
        if ex.step < len(steps) and steps[ex.step].get("rollback"):
            out.append(ex)
    return out


# --------------------------------------------------------------------- health

def health(execs: Iterable[Execution], nodes: Mapping[str, Mapping[str, Any]], now: float, *,
           timeouts: Mapping[Tuple[str, int], float], config: Optional[Mapping[str, float]] = None
           ) -> List[Action]:
    """Timeouts, unreachable nodes and idempotent re-dispatch for live executions."""
    cfg = dict(DEFAULTS, **(config or {}))
    actions: List[Action] = []
    for ex in execs:
        if ex.state not in EXEC_LIVE:
            continue
        node = nodes.get(ex.node_id) or {}
        last_seen = float(node.get("last_seen") or 0.0)
        offline = now - last_seen > cfg["offline_after_s"]
        if ex.state in EXEC_IN_FLIGHT:
            begun = float(next(t for t in (ex.started_at, ex.dispatched_at, ex.created_at) if t is not None))
            limit = float(timeouts.get((ex.tool_id, ex.version), 300.0)) + cfg["grace_s"]
            if now - begun > limit:
                actions.append(Action("timeout", ex.node_id, ex.step, ex.id, "timed_out",
                                      f"no result {limit:.0f}s after start"))
                continue
            if offline:
                if ex.idempotent and ex.attempts < cfg["max_attempts"]:
                    actions.append(Action("requeue", ex.node_id, ex.step, ex.id, "queued",
                                          "node went offline; re-dispatching an idempotent tool"))
                else:
                    reason = ("node went offline during a non-idempotent execution; decide whether to retry"
                              if not ex.idempotent else "node went offline; retry limit reached")
                    actions.append(Action("unreachable", ex.node_id, ex.step, ex.id, "unreachable", reason))
        elif ex.state == "queued":
            if offline and now - float(ex.created_at) > cfg["queue_timeout_s"]:
                actions.append(Action("unreachable", ex.node_id, ex.step, ex.id, "unreachable",
                                      "node has not polled since the execution was queued"))
    return actions


# --------------------------------------------------------------------- tick

def tick(run: Run, execs: Sequence[Execution], now: float, *, dispatch_paused: bool = False,
         eligible: Optional[Mapping[str, str]] = None) -> List[Action]:
    """Decide what a running (or rolling-back) run does next.

    ``eligible`` maps node id -> reason for nodes that must not receive new work right now
    (quarantined, revoked); their pending steps are skipped with that reason.
    """
    eligible = eligible or {}
    plan = run.plan or {}
    steps: List[Mapping[str, Any]] = list(plan.get("steps") or [])
    batches: List[List[str]] = [list(b) for b in (plan.get("batches") or [])]
    strategy = run.strategy or {}
    by_node: Dict[str, List[Execution]] = {}
    for ex in execs:
        by_node.setdefault(ex.node_id, []).append(ex)

    if run.state == "rolling_back":
        live = [e for e in execs if e.rollback_of and e.state in EXEC_LIVE]
        if live:
            return []
        rollbacks = [e for e in execs if e.rollback_of]
        if any(e.state != "succeeded" for e in rollbacks):
            return [Action("finish", state="failed", reason="rollback incomplete: some rollback tools failed")]
        return [Action("finish", state="rolled_back", reason="all changes rolled back")]
    if run.state != "running":
        return []

    actions: List[Action] = []
    # Nodes in finished batches may still be rolling back their own failures; they don't
    # block progression but are counted.
    index = int(run.batch or 0)
    if index >= len(batches):
        return [_finish(run, steps, by_node)]
    batch = batches[index]
    progress = {n: node_progress(steps, by_node.get(n, [])) for n in batch}

    # Node-level rollback on failure (on_failure: rollback).
    for node_id, p in progress.items():
        if p.status == "failed" and p.step is not None and \
                str(steps[p.step].get("on_failure") or "stop") == "rollback":
            for target in rollback_targets(steps, by_node.get(node_id, [])):
                actions.append(Action("rollback", node_id, target.step, target.id,
                                      reason=f"step {steps[p.step].get('name')} failed"))
    if any(a.kind == "rollback" for a in actions):
        return actions

    all_settled = all(node_settled(p) for p in progress.values())
    if not all_settled:
        if dispatch_paused:
            return actions
        live_nodes = sum(1 for xs in by_node.values() if any(x.state in EXEC_LIVE for x in xs))
        room = int(strategy.get("max_parallel") or len(batch)) - live_nodes
        for node_id in batch:
            p = progress[node_id]
            if p.status != "ready":
                continue
            if node_id in eligible:
                actions.append(Action("skip", node_id, p.step, state="cancelled", reason=eligible[node_id]))
                continue
            # A ready node has no live execution, so starting it takes one parallel slot.
            if room <= 0:
                continue
            room -= 1
            actions.append(Action("queue", node_id, p.step, batch=index))
        return actions

    # The batch has settled: evaluate it once.
    failed = [n for n, p in progress.items() if p.failed]
    evaluated = int((run.summary or {}).get("evaluated_batch", -1))
    if evaluated < index:
        is_canary = index == 0 and int(strategy.get("canary") or 0) > 0 and len(batches) > 1
        share = 100.0 * len(failed) / max(1, len(batch))
        if is_canary and failed:
            return [Action("pause", batch=index, reason=f"canary failed on {len(failed)} node(s)",
                           data={"failed": failed})]
        if share > float(strategy.get("failure_threshold", 10)) and index + 1 < len(batches):
            return [Action("pause", batch=index,
                           reason=f"{len(failed)} of {len(batch)} nodes failed in batch {index + 1} "
                                  f"({share:.0f}% > {float(strategy.get('failure_threshold', 10)):.0f}%)",
                           data={"failed": failed})]
        actions.append(Action("evaluated", batch=index))
    if index + 1 >= len(batches):
        return actions + [_finish(run, steps, by_node)]
    pause_s = float(strategy.get("pause_between_s") or 0)
    finished_at = run.batch_finished_at
    if pause_s and finished_at is None:
        return actions + [Action("batch_finished", batch=index)]
    if pause_s and finished_at is not None and now - float(finished_at) < pause_s:
        return actions
    return actions + [Action("advance", batch=index + 1)]


def _finish(run: Run, steps, by_node: Mapping[str, List[Execution]]) -> Action:
    failed_nodes = [n for n in (run.nodes or []) if node_progress(steps, by_node.get(n, [])).failed]
    if failed_nodes:
        return Action("finish", state="failed", reason=f"{len(failed_nodes)} node(s) failed",
                      data={"failed": failed_nodes})
    return Action("finish", state="succeeded", reason="all nodes succeeded")


def summarize(run: Run, execs: Sequence[Execution]) -> Dict[str, Any]:
    """Counts the UI shows for a run (per execution state and per node outcome)."""
    steps = list((run.plan or {}).get("steps") or [])
    by_node: Dict[str, List[Execution]] = {}
    states: Dict[str, int] = {}
    for ex in execs:
        by_node.setdefault(ex.node_id, []).append(ex)
        states[ex.state] = states.get(ex.state, 0) + 1
    nodes = {"done": 0, "failed": 0, "active": 0, "pending": 0}
    for node_id in run.nodes or []:
        p = node_progress(steps, by_node.get(node_id, []))
        if p.failed:
            nodes["failed"] += 1
        elif p.status == "done":
            nodes["done"] += 1
        elif p.status in ("active", "waiting_rollback"):
            nodes["active"] += 1
        else:
            nodes["pending"] += 1
    return {"executions": states, "nodes": nodes, "total_nodes": len(run.nodes or []),
            "batches": len((run.plan or {}).get("batches") or []), "batch": int(run.batch or 0),
            "failed_states": sorted(s for s in states if s in EXEC_FAILED)}
