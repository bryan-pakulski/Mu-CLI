"""Per-parent registry of async sub-agent runs (control plane).

``SubagentRegistry`` is the async counterpart to ``BackgroundTaskRegistry``
(``mu/tools/shell/background.py``): a thread-safe, per-Session map of
``task_id`` -> ``SubagentRecord`` describing a child Session running in a
background daemon thread. It owns a long-lived ``SubagentProgressTracker``
(render plane) and feeds it lifecycle + completion events.

Split of concerns:

  * **Control plane** (this module) — task_id allocation, the child
    thread, the ``SubagentLifecycleManager``, result capture, kill/cancel,
    shutdown. ``spawn_agent`` registers here and returns immediately.
  * **Render plane** (``mu/ui/progress.py``) — the live panel state, fed
    exclusively by the registry via ``tracker.open`` / ``update_tool`` /
    ``set_state`` / ``close``. The registry is the single source of truth
    for ``has_active()``.

The child runs via ``child.send_message(task)`` on a daemon thread. Kill
is cooperative: ``cancel()`` sets ``child._subagent_cancelled`` (a bool
checked at the top of each ``run_turn`` iteration, mirroring
``_hook_abort_requested``); the child exits with ``status="killed"`` at
the next iteration boundary and this wrapper captures partial results via
``_extract_partial_summary``.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
import weakref
from dataclasses import dataclass, field

from utils.threads import NamedThread
from typing import Any, Callable, Dict, List, Optional


logger = logging.getLogger("mucli")


def _extract_partial_summary(child_sm) -> str:
    """Mine a child SessionManager's history for partial findings.

    When a sub-agent is killed, hits max_iterations, or errors before
    producing a final assistant response, its history still contains every
    tool result and intermediate assistant message. This extracts them into
    a formatted summary so the parent gets *something* useful instead of a
    placeholder that discards all work.

    Returns a non-empty string if any assistant text or tool results were
    found; empty string if the child did nothing.
    """
    if not child_sm or not child_sm.history:
        return ""

    parts: list[str] = []
    for turn in child_sm.history:
        for p in turn.get("parts", []):
            ptype = p.get("type", "")
            if ptype == "text" and p.get("text"):
                text = p["text"].strip()
                if not text:
                    continue
                if text.startswith("🔨 Running tool:"):
                    continue
                if text.startswith("Tokens:") or text.startswith("Final session tokens:"):
                    continue
                parts.append(text)
            elif ptype == "tool_result" and p.get("tool_result"):
                raw = p["tool_result"]
                if isinstance(raw, (dict, list)):
                    import json as _json

                    raw = _json.dumps(raw, default=str)[:500]
                else:
                    raw = str(raw)[:500]
                tool_name = p.get("tool_name", "?")
                parts.append(f"[{tool_name}] {raw}")

    if not parts:
        return ""
    if len(parts) > 8:
        parts = parts[-8:]
    return "⚠️ Partial findings recovered:\n\n" + "\n---\n".join(parts)


@dataclass
class SubagentRecord:
    task_id: str
    task: str
    depth: int
    child: Any
    lifecycle: Any
    thread: Optional[threading.Thread] = None
    tracker_agent_id: Optional[str] = None
    started_at: float = field(default_factory=time.monotonic)
    finished_at: Optional[float] = None
    # Signaled exactly once when the child reaches a terminal state
    # (done / killed / error). Lets a parent block on a single
    # ``await_subagent`` call instead of busy-polling ``snapshot()`` and
    # burning parent iterations (which trips loop detection). See
    # ``SubagentRegistry.wait``.
    done_event: threading.Event = field(default_factory=threading.Event)
    # running | done | killed | error
    status: str = "running"
    summary: str = ""
    tokens: Dict[str, Any] = field(default_factory=dict)
    tool_calls: int = 0
    error: Optional[str] = None
    kill_reason: Optional[str] = None
    history_length: int = 0
    # Live context-usage fields, written by the child loop (single writer =
    # the child thread) and read by snapshot() under the registry lock.
    context_pct: float = 0.0
    iter: int = 0
    max_iter: int = 0
    tokens_in: int = 0
    model: str = ""
    batch_id: str = ""
    started_wall_at: float = field(default_factory=time.time)
    finished_wall_at: Optional[float] = None
    artifact: Dict[str, Any] = field(default_factory=dict)
    parent_notified: bool = False


class SubagentRegistry:
    """Owns the lifecycle of all async sub-agent runs for one Session."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: Dict[str, SubagentRecord] = {}
        self._order: List[str] = []
        self._next_id = 0
        self._tracker: Any = None  # lazy SubagentProgressTracker
        # GUI live-push callback. Set by ``spawn_agent`` (WebUI-only) so the
        # chat feed gets subagent_start/progress/end events. ``None`` for
        # CLI/TUI runs → emit is a no-op, no behaviour change.
        self._publish_fn: Optional[Callable[[Dict[str, Any]], None]] = None
        # MUCLI_SUBAGENT_DURABLE_RESULTS_V1: durable recovery + delegation-window identity.
        self._parent_ref: Optional[weakref.ReferenceType[Any]] = None
        self._artifact_store: Any = None
        self._batch_seq = 0
        self._active_batch_id = ""

    def bind_parent(self, parent: Any) -> None:
        """Attach the owning parent and its session-scoped durable store."""
        try:
            self._parent_ref = weakref.ref(parent)
        except TypeError:
            self._parent_ref = None
        registry = getattr(parent, "artifact_registry", None)
        session_dir = getattr(registry, "session_dir", None)
        if not session_dir:
            return
        try:
            from mu.agent.subagent_artifacts import SubagentArtifactStore
            if self._artifact_store is None or getattr(self._artifact_store, "session_dir", None) != session_dir:
                self._artifact_store = SubagentArtifactStore(session_dir, artifact_registry=registry)
            # A fresh registry has no surviving worker threads. Any durable
            # state still marked running came from a previous process and must
            # become a terminal, retrievable record rather than a ghost.
            with self._lock:
                has_records = bool(self._records)
            if not has_records:
                for state in self._artifact_store.list():
                    if str(state.get("status") or "") != "running":
                        continue
                    task_id = str(state.get("task_id") or "")
                    if not task_id:
                        continue
                    self._artifact_store.record_event(
                        task_id,
                        {
                            "kind": "subagent_recovered",
                            "status": "error",
                            "error": "worker process ended before a terminal event",
                        },
                        state_patch={
                            "status": "error",
                            "error": "worker process ended before a terminal event",
                            "summary": (
                                str(state.get("summary") or "").strip()
                                or "Sub-agent execution was interrupted by a server restart. "
                                   "Its event journal and saved history remain available."
                            ),
                            "finished_at": time.time(),
                            "parent_notified": False,
                        },
                    )
        except Exception:
            self._artifact_store = None

    def _parent(self) -> Any:
        return self._parent_ref() if self._parent_ref is not None else None

    # ----------------------------------------------------------- tracker

    @property
    def tracker(self):
        if self._tracker is None:
            from mu.ui.progress import SubagentProgressTracker

            self._tracker = SubagentProgressTracker()
            # Propagate an already-attached publish callback to the tracker so
            # its per-tool / state-change emits reach the GUI too.
            if self._publish_fn is not None:
                self._tracker._publish = self._emit
        return self._tracker

    # ----------------------------------------------------------- GUI publish

    @property
    def _publish(self) -> Optional[Callable[[Dict[str, Any]], None]]:
        return self._publish_fn

    @_publish.setter
    def _publish(self, fn: Optional[Callable[[Dict[str, Any]], None]]) -> None:
        self._publish_fn = fn
        # The tracker may already exist (spawn pre-opens its row); keep it in
        # sync so per-tool emits route to the same bus.
        if self._tracker is not None:
            self._tracker._publish = self._emit

    def _emit(self, event: Dict[str, Any]) -> None:
        """Push a subagent event to the GUI bus. No-op when no ``_publish`` is
        attached (CLI/TUI). The WebUI's ``_publish`` stamps ``session_name``."""
        task_id = str(event.get("task_id") or "").strip()
        if task_id and self._artifact_store is not None:
            patch = {
                key: event[key]
                for key in (
                    "status", "tool_count", "last_tool", "stuck", "stall",
                    "repeat_count", "elapsed", "context_pct", "iter",
                    "max_iter", "tokens_in", "summary", "error", "kill_reason",
                    "batch_id",
                )
                if key in event and event[key] is not None
            }
            try:
                self._artifact_store.record_event(task_id, event, state_patch=patch or None)
            except Exception:
                pass
        fn = self._publish_fn
        if fn is None:
            return
        try:
            fn(event)
        except Exception:  # noqa: BLE001 — never let a UI push break the run
            pass

    def _link_tracker_task_id(self, tracker_agent_id: Optional[str], task_id: str) -> None:
        """Tell the tracker the registry task_id for a pre-opened row so its
        per-tool progress emits can carry the panel's upsert key."""
        if tracker_agent_id is None:
            return
        try:
            self.tracker.set_task_id(tracker_agent_id, task_id)
        except Exception:  # noqa: BLE001
            pass

    def update_child_live(
        self,
        task_id: str,
        *,
        context_pct: float = 0.0,
        iter: int = 0,
        max_iter: int = 0,
        tokens_in: int = 0,
    ) -> None:
        """Write the live context-usage fields on a child's record. Called by
        the child loop at the post-response seam (single writer = child
        thread). Reads happen under the same lock via ``snapshot()``."""
        with self._lock:
            rec = self._records.get(task_id)
            if rec is None:
                return
            rec.context_pct = float(context_pct)
            rec.iter = int(iter)
            rec.max_iter = int(max_iter)
            rec.tokens_in = int(tokens_in)
            batch_id = rec.batch_id
        if self._artifact_store is not None:
            try:
                self._artifact_store.record_event(
                    task_id,
                    {
                        "kind": "subagent_progress",
                        "task_id": task_id,
                        "batch_id": batch_id,
                        "context_pct": float(context_pct),
                        "iter": int(iter),
                        "max_iter": int(max_iter),
                        "tokens_in": int(tokens_in),
                    },
                    state_patch={
                        "context_pct": float(context_pct),
                        "iter": int(iter),
                        "max_iter": int(max_iter),
                        "tokens_in": int(tokens_in),
                    },
                )
            except Exception:
                pass

    def _on_lifecycle_signal(self, record: SubagentRecord, lifecycle: Any) -> None:
        """Push stuck/stall state into the live progress tracker."""
        try:
            snap = lifecycle.snapshot()
            stuck = bool(snap.get("stuck"))
            stall = bool(snap.get("stall"))
            self.tracker.set_state(
                record.tracker_agent_id,
                stuck=stuck,
                stall=stall,
                repeat_count=int(snap.get("consecutive_repeats", 0)),
            )
            self._emit(
                {
                    "kind": "subagent_progress",
                    "task_id": record.task_id,
                    "stuck": stuck,
                    "stall": stall,
                    "consecutive_repeats": int(snap.get("consecutive_repeats", 0)),
                    "consecutive_stalls": int(snap.get("consecutive_stalls", 0)),
                    "status": record.status,
                    "batch_id": record.batch_id,
                }
            )
        except Exception:  # noqa: BLE001
            pass

    # ----------------------------------------------------------- ops

    def register(
        self,
        child: Any,
        *,
        task: str,
        depth: int,
        lifecycle: Any,
        tracker_agent_id: Optional[str] = None,
        model: Optional[str] = None,
    ) -> SubagentRecord:
        """Register a child about to run on a background thread. Opens a
        progress-tracker row (unless ``tracker_agent_id`` is supplied —
        e.g. when the caller pre-opened the row to build the child UI) and
        wires the lifecycle signal callback. Does NOT start the run thread
        — caller starts it via ``launch``.
        """
        with self._lock:
            self._next_id += 1
            task_id = f"sa-{uuid.uuid4().hex[:8]}"
            if not any(r.status == "running" for r in self._records.values()):
                self._batch_seq += 1
                self._active_batch_id = f"sab-{int(time.time() * 1000):x}-{self._batch_seq}"
            batch_id = self._active_batch_id
        # Open the tracker row outside the registry lock (tracker has its own).
        if tracker_agent_id is None:
            try:
                tracker_agent_id = self.tracker.open(depth=depth, task=task)
            except Exception:  # noqa: BLE001
                tracker_agent_id = None

        record = SubagentRecord(
            task_id=task_id,
            task=task,
            depth=depth,
            child=child,
            lifecycle=lifecycle,
            tracker_agent_id=tracker_agent_id,
            model=model or "",
            batch_id=batch_id,
        )
        # Wire lifecycle -> tracker so stuck/stall render live.
        lifecycle.on_signal = lambda lc, r=record: self._on_lifecycle_signal(r, lc)

        with self._lock:
            self._records[task_id] = record
            self._order.append(task_id)
        # Link the pre-opened tracker row to this task_id so the tracker's
        # per-tool emits carry the panel's upsert key, then announce the
        # start to the GUI (no-op when no _publish is attached).
        self._link_tracker_task_id(tracker_agent_id, task_id)
        if self._artifact_store is not None:
            try:
                self._artifact_store.start(task_id, {
                    "task": task,
                    "depth": depth,
                    "model": model or "",
                    "batch_id": batch_id,
                })
            except Exception:
                pass
        self._emit(
            {
                "kind": "subagent_start",
                "task_id": task_id,
                "task": task,
                "depth": depth,
                "model": model or "",
                "batch_id": batch_id,
            }
        )
        return record

    def launch(self, record: SubagentRecord, task: str) -> None:
        """Start the daemon thread that runs the child."""
        thread = NamedThread(
            target=self._run_child,
            args=(record, task),
            daemon=True,
            name=f"subagent-{record.task_id}",
        )
        with self._lock:
            record.thread = thread
        thread.start()

    def _run_child(self, record: SubagentRecord, task: str) -> None:
        """Thread target: run the child to completion and capture results."""
        child = record.child
        lifecycle = record.lifecycle
        # The watchdog timer starts exactly when the child begins running.
        try:
            lifecycle.start_watchdog(child)
        except Exception:  # noqa: BLE001
            pass

        result: Dict[str, Any] = {}
        try:
            result = child.send_message(task) or {}
        except Exception as exc:  # noqa: BLE001
            logger.warning("subagent %s raised %s", record.task_id, exc)
            partial = _extract_partial_summary(getattr(child, "session_manager", None))
            summary = partial or f"Sub-agent failed after starting: {exc}"
            history = list(getattr(getattr(child, "session_manager", None), "history", []) or [])
            try:
                lc = lifecycle.snapshot()
            except Exception:
                lc = {}
            with self._lock:
                record.status = "error"
                record.summary = summary
                record.error = str(exc)
                record.tool_calls = int(lc.get("tool_count", 0) or 0)
                record.history_length = len(history)
                record.finished_at = time.monotonic()
                record.finished_wall_at = time.time()
            self._persist_finish(record, history)
            record.done_event.set()
            lifecycle.close()
            self._close_tracker(record, tool_count=record.tool_calls, summary=summary, error=str(exc), status="error")
            return

        # Determine final status. run_turn returns status="killed" when the
        # cooperative cancel flag fired; otherwise it returns the child's
        # own status (completed / max_iterations_reached / error / ...).
        raw_status = str(result.get("status") or "")
        final_text = str(result.get("assistant_text") or "").strip()
        kill_reason = None

        if raw_status == "killed" or getattr(child, "_subagent_cancelled", False):
            status = "killed"
            kill_reason = getattr(child, "_subagent_kill_reason", None) or "killed"
            lifecycle.cancel(kill_reason)
        elif raw_status == "error":
            status = "error"
        elif raw_status in ("", "completed"):
            status = "done"
        else:
            # max_iterations_reached etc. — still treat as done with partials.
            status = "done"

        if not final_text:
            partial = _extract_partial_summary(child.session_manager)
            final_text = partial or "(sub-agent finished without producing a final text response)"

        tokens = result.get("tokens") or {}
        try:
            lifecycle_snapshot = lifecycle.snapshot()
        except Exception:
            lifecycle_snapshot = {}
        tool_calls = max(
            len(result.get("tool_calls") or []),
            int(lifecycle_snapshot.get("tool_count", 0) or 0),
        )
        history = list(getattr(child.session_manager, "history", []) or [])

        with self._lock:
            record.status = status
            record.summary = final_text
            record.tokens = tokens
            record.tool_calls = tool_calls
            record.error = None if status != "error" else str(result.get("error") or final_text)
            record.kill_reason = kill_reason
            record.history_length = len(history)
            record.finished_at = time.monotonic()
            record.finished_wall_at = time.time()
        self._persist_finish(record, history)
        record.done_event.set()
        lifecycle.close()

        self._close_tracker(
            record,
            tool_count=tool_calls,
            summary=final_text,
            error=record.error,
            status=status,
            kill_reason=kill_reason,
        )

    def _persist_finish(self, record: SubagentRecord, history: List[Dict[str, Any]]) -> None:
        if self._artifact_store is None:
            return
        try:
            state = self._artifact_store.finish(
                record.task_id,
                {
                    "task": record.task,
                    "depth": record.depth,
                    "model": record.model,
                    "batch_id": record.batch_id,
                    "status": record.status,
                    "summary": record.summary,
                    "tokens": dict(record.tokens),
                    "tool_calls": record.tool_calls,
                    "error": record.error,
                    "kill_reason": record.kill_reason,
                    "history_length": record.history_length,
                    "started_at": record.started_wall_at,
                    "finished_at": record.finished_wall_at or time.time(),
                },
                history,
            )
            artifact = state.get("artifact")
            if isinstance(artifact, dict):
                with self._lock:
                    record.artifact = dict(artifact)
        except Exception:
            pass

    def _close_tracker(
        self,
        record: SubagentRecord,
        *,
        tool_count: int,
        summary: str,
        error: Optional[str],
        status: str,
        kill_reason: Optional[str] = None,
    ) -> None:
        if record.tracker_agent_id is not None:
            try:
                self.tracker.close(
                    record.tracker_agent_id,
                    tool_count=tool_count,
                    summary=summary,
                    error=error,
                    status=status,
                    kill_reason=kill_reason,
                )
            except Exception:  # noqa: BLE001
                pass
        # Announce completion to the GUI. Sent even when no tracker row existed
        # (tracker_agent_id is None) so the panel still clears the agent.
        self._emit(
            {
                "kind": "subagent_end",
                "task_id": record.task_id,
                "status": status,
                "summary": summary,
                "tokens": dict(record.tokens),
                "tool_calls": tool_count,
                "elapsed": round(
                    max(0.0, (record.finished_at or time.monotonic()) - record.started_at),
                    2,
                ),
                "kill_reason": kill_reason,
                "error": error,
                "batch_id": record.batch_id,
                "artifact": dict(record.artifact),
            }
        )

    # ----------------------------------------------------------- introspection

    def get(self, task_id: str) -> Optional[SubagentRecord]:
        with self._lock:
            return self._records.get(task_id)

    def list(self) -> List[SubagentRecord]:
        with self._lock:
            return [self._records[tid] for tid in self._order]

    def has_active(self) -> bool:
        with self._lock:
            return any(r.status == "running" for r in self._records.values())

    def snapshot(self, task_id: str) -> Dict[str, Any]:
        """Build the poll_subagent response for one task."""
        with self._lock:
            rec = self._records.get(task_id)
            if rec is None:
                durable = self._artifact_store.load(task_id) if self._artifact_store is not None else None
                return durable or {"status": "missing", "task_id": task_id}
            base = {
                "task_id": rec.task_id,
                "task": rec.task,
                "depth": rec.depth,
                "status": rec.status,
                "summary": rec.summary,
                "tokens": dict(rec.tokens),
                "tool_calls": rec.tool_calls,
                "error": rec.error,
                "kill_reason": rec.kill_reason,
                "history_length": rec.history_length,
                "context_pct": round(float(rec.context_pct), 1),
                "iter": rec.iter,
                "max_iter": rec.max_iter,
                "tokens_in": rec.tokens_in,
                "model": rec.model,
                "batch_id": rec.batch_id,
                "started_at": rec.started_wall_at,
                "finished_at": rec.finished_wall_at,
                "artifact": dict(rec.artifact),
                "durable": bool(self._artifact_store),
                "state_path": self._artifact_store.relative_path(rec.task_id) if self._artifact_store is not None else None,
                "result_path": self._artifact_store.relative_path(rec.task_id, "result.json") if self._artifact_store is not None else None,
            }
        # Merge in live lifecycle signals (running children especially).
        try:
            lc = rec.lifecycle.snapshot() if rec.lifecycle is not None else {}
        except Exception:  # noqa: BLE001
            lc = {}
        base.update(
            {
                "tool_count": lc.get("tool_count", base["tool_calls"]),
                "tool_diversity": lc.get("tool_diversity", 0),
                "last_tool": lc.get("last_tool"),
                "stuck": bool(lc.get("stuck", False)),
                "stall": bool(lc.get("stall", False)),
                "consecutive_repeats": int(lc.get("consecutive_repeats", 0)),
                "consecutive_stalls": int(lc.get("consecutive_stalls", 0)),
                "elapsed": lc.get("elapsed", 0.0),
            }
        )
        return base

    def snapshot_all(self) -> List[Dict[str, Any]]:
        return [self.snapshot(tid) for tid in [r.task_id for r in self.list()]]

    def snapshot_active(self) -> List[Dict[str, Any]]:
        with self._lock:
            ids = [tid for tid in self._order if self._records[tid].status == "running"]
        return [self.snapshot(tid) for tid in ids]

    def active_batch_id(self) -> str:
        with self._lock:
            return self._active_batch_id if any(record.status == "running" for record in self._records.values()) else ""

    def context_block(self, parent: Any = None, *, max_chars: int = 7000) -> str:
        """Return authoritative delegated-work context for the parent model."""
        parent = parent or self._parent()
        with self._lock:
            active_ids = [tid for tid in self._order if self._records[tid].status == "running"]
            completed = [
                self._records[tid]
                for tid in self._order
                if self._records[tid].status != "running" and not self._records[tid].parent_notified
            ]
        lines: List[str] = []
        if active_ids:
            lines.append("Delegated sub-agents currently running:")
            for task_id in active_ids:
                snap = self.snapshot(task_id)
                progress = f"iteration {snap.get('iter', 0)}/{snap.get('max_iter', 0)}" if snap.get("max_iter") else "running"
                if snap.get("last_tool"):
                    progress += f", last tool={snap.get('last_tool')}"
                lines.append(f"- {task_id}: {snap.get('task', '')} ({progress}). Do not report this delegation as missing.")
        completed_snaps: List[Dict[str, Any]] = [self.snapshot(record.task_id) for record in completed]
        known_ids = {str(snap.get("task_id") or "") for snap in completed_snaps}
        if self._artifact_store is not None:
            try:
                for snap in self._artifact_store.list():
                    task_id = str(snap.get("task_id") or "")
                    status = str(snap.get("status") or "")
                    if not task_id or task_id in known_ids or status == "running" or bool(snap.get("parent_notified")):
                        continue
                    completed_snaps.append(snap)
                    known_ids.add(task_id)
            except Exception:
                pass
        for snap in completed_snaps:
            task_id = str(snap.get("task_id") or "")
            task = str(snap.get("task") or "")
            status = str(snap.get("status") or "done")
            summary = str(snap.get("summary") or snap.get("error") or "").strip()
            artifact = snap.get("artifact") if isinstance(snap.get("artifact"), dict) else {}
            artifact_ref = f" artifact_id={artifact.get('artifact_id')}" if artifact.get("artifact_id") else f" result_path={snap.get('result_path')}"
            if not lines:
                lines.append("Newly completed delegated results:")
            lines.append(f"- {task_id} status={status}; task={task};{artifact_ref}. Result: {summary[:3000]}")
            if parent is not None and summary:
                try:
                    parent.task_memory.save(
                        content=(f"Sub-agent {task_id} completed task {task!r}. Result: {summary[:5000]}. Durable result: {artifact_ref.strip()}."),
                        tags=["subagent", task_id, "delegated-result"],
                        source=f"subagent:{task_id}",
                        kind="finding",
                        status="active",
                    )
                except Exception:
                    pass
            with self._lock:
                current = self._records.get(task_id)
                if current is not None:
                    current.parent_notified = True
            if self._artifact_store is not None and task_id:
                try:
                    self._artifact_store.record_event(
                        task_id,
                        {"kind": "subagent_parent_notified", "task_id": task_id},
                        state_patch={"parent_notified": True},
                    )
                except Exception:
                    pass
        if not lines:
            return ""
        lines.append("Treat the records above as authoritative. Use the completed result directly or call poll_subagent/await_subagent for its durable bundle; do not claim the child failed to run merely because its live panel closed.")
        return "\n".join(lines)[:max_chars]

    # ----------------------------------------------------------- blocking wait

    def wait(self, task_id: str, *, timeout: Optional[float] = None) -> Dict[str, Any]:
        """Block the caller until the child reaches a terminal state or
        ``timeout`` seconds elapse, then return its snapshot.

        This is the blocking counterpart to ``snapshot``: instead of the
        parent busy-polling ``poll_subagent`` (one tool call per
        iteration, which trips loop detection after
        ``loop_detection_repeat_threshold`` repeats), the parent issues a
        single ``await_subagent`` tool call that blocks here. The parent's
        agent-loop iteration does not advance while blocked, so no
        repeated-tool-sequence fingerprint accumulates.

        ``timeout`` semantics mirror ``threading.Event.wait``:
          * ``None``  — block forever (the tool layer always passes a
            finite timeout, but the registry primitive itself is unbounded).
          * ``0``     — non-blocking probe: returns the current snapshot
            immediately (running or terminal).
          * ``N``     — wake after N seconds if the child is still running;
            the returned snapshot then has ``status == "running"`` and the
            caller decides whether to re-await, kill, or continue.

        Returns the ``missing`` snapshot for an unknown ``task_id`` and
        the current snapshot immediately for an already-terminal child.
        Safe to call concurrently with ``cancel`` — ``cancel`` sets the
        event so this wakes promptly.
        """
        with self._lock:
            rec = self._records.get(task_id)
        if rec is None:
            return self.snapshot(task_id)
        if rec.status != "running":
            return self.snapshot(task_id)
        rec.done_event.wait(timeout)
        return self.snapshot(task_id)

    # ----------------------------------------------------------- control

    def cancel(self, task_id: str, *, grace_seconds: float = 5.0) -> Dict[str, Any]:
        """Cooperatively kill a running child. Sets the cancel flag, waits
        for the run thread to exit (bounded), then returns the snapshot
        with partial results. No-op (returns current snapshot) if the child
        is already finished or unknown.
        """
        with self._lock:
            rec = self._records.get(task_id)
        if rec is None:
            return self.snapshot(task_id)
        if rec.status != "running":
            return self.snapshot(task_id)

        # Set the cooperative cancel flag (read at the next loop iteration).
        try:
            rec.child._subagent_cancelled = True
            rec.child._subagent_kill_reason = "killed_by_parent"
        except Exception:  # noqa: BLE001
            pass
        try:
            rec.lifecycle.cancel("killed_by_parent")
        except Exception:  # noqa: BLE001
            pass
        # Push "killed" to the tracker immediately for responsiveness.
        try:
            self.tracker.set_state(rec.tracker_agent_id, status="killed", kill_reason="killed_by_parent")
        except Exception:  # noqa: BLE001
            pass
        # Reflect the kill on the GUI right away — the thread finalizes
        # rec.status="killed" (and re-emits via _close_tracker) shortly after.
        self._emit(
            {
                "kind": "subagent_end",
                "task_id": task_id,
                "status": "killed",
                "kill_reason": "killed_by_parent",
                "elapsed": round(
                    max(0.0, time.monotonic() - rec.started_at), 2
                ),
                "batch_id": rec.batch_id,
            }
        )

        # Wait for the thread wrapper to finalize (it writes rec.status).
        deadline = time.monotonic() + max(0.0, grace_seconds)
        thread = rec.thread
        while time.monotonic() < deadline:
            with self._lock:
                if rec.status != "running":
                    break
            if thread is not None and not thread.is_alive():
                break
            time.sleep(0.05)
        # Wake any parent blocked in ``wait`` so it returns promptly with
        # the killed snapshot rather than waiting out its own timeout.
        rec.done_event.set()
        return self.snapshot(task_id)

    def shutdown(self) -> None:
        """Cancel every still-running child and join briefly. Called on
        Session.shutdown() (REPL exit)."""
        with self._lock:
            running = [r for r in self._records.values() if r.status == "running"]
        for rec in running:
            try:
                rec.child._subagent_cancelled = True
                rec.child._subagent_kill_reason = "session_shutdown"
                rec.lifecycle.cancel("session_shutdown")
            except Exception:  # noqa: BLE001
                pass
        for rec in running:
            thread = rec.thread
            if thread is not None and thread.is_alive():
                try:
                    thread.join(timeout=2.0)
                except Exception:  # noqa: BLE001
                    pass


__all__ = ["SubagentRecord", "SubagentRegistry", "_extract_partial_summary"]