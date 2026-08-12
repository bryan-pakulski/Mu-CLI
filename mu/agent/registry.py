"""Async delegation registry with a persistent specialist pool and sparse mailbox."""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
import weakref
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from utils.threads import NamedThread

logger = logging.getLogger("mucli")


def _extract_partial_summary(child_sm) -> str:
    if not child_sm or not getattr(child_sm, "history", None):
        return ""
    out: list[str] = []
    for message in child_sm.history:
        for part in message.get("parts", []) or []:
            kind = part.get("type")
            if kind == "text" and part.get("text"):
                text = str(part["text"]).strip()
                if text and not text.startswith(("🔨 Running tool:", "Tokens:", "Final session tokens:")):
                    out.append(text)
            elif kind == "tool_result" and part.get("tool_result"):
                raw = part["tool_result"]
                if isinstance(raw, (dict, list)):
                    raw = json.dumps(raw, default=str)[:500]
                else:
                    raw = str(raw)[:500]
                out.append(f"[{part.get('tool_name', '?')}] {raw}")
    if not out:
        return ""
    return "⚠️ Partial findings recovered:\n\n" + "\n---\n".join(out[-8:])


@dataclass
class SpecialistRecord:
    worker_id: str
    specialist_key: str
    child: Any
    depth: int
    model: str
    provider_key: str = ""
    disabled_tools: tuple[str, ...] = ()
    status: str = "busy"
    created_at: float = field(default_factory=time.time)
    last_active_at: float = field(default_factory=time.time)
    delegation_count: int = 1


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
    done_event: threading.Event = field(default_factory=threading.Event)
    status: str = "running"
    summary: str = ""
    tokens: Dict[str, Any] = field(default_factory=dict)
    tool_calls: int = 0
    error: Optional[str] = None
    kill_reason: Optional[str] = None
    history_length: int = 0
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
    specialist_key: str = "general"
    worker_id: str = ""
    reused_specialist: bool = False
    actions: List[Dict[str, Any]] = field(default_factory=list)


class SubagentRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: Dict[str, SubagentRecord] = {}
        self._order: List[str] = []
        self._tracker: Any = None
        self._publish_fn: Optional[Callable[[Dict[str, Any]], None]] = None
        self._parent_ref: Optional[weakref.ReferenceType[Any]] = None
        self._artifact_store: Any = None
        self._batch_seq = 0
        self._active_batch_id = ""
        self._specialists: Dict[str, SpecialistRecord] = {}
        self._specialist_order: List[str] = []
        self._mailbox: List[Dict[str, Any]] = []
        self._mailbox_seq = 0
        self._durable_mailbox_seen: set[str] = set()

    def bind_parent(self, parent: Any) -> None:
        try:
            self._parent_ref = weakref.ref(parent)
        except TypeError:
            self._parent_ref = None
        artifact_registry = getattr(parent, "artifact_registry", None)
        session_dir = getattr(artifact_registry, "session_dir", None)
        if not session_dir:
            return
        try:
            from mu.agent.subagent_artifacts import SubagentArtifactStore
            if self._artifact_store is None or getattr(self._artifact_store, "session_dir", None) != session_dir:
                self._artifact_store = SubagentArtifactStore(session_dir, artifact_registry=artifact_registry)
            if not self._records:
                for state in self._artifact_store.list():
                    if str(state.get("status") or "") == "running":
                        task_id = str(state.get("task_id") or "")
                        if task_id:
                            self._artifact_store.record_event(task_id, {"kind": "subagent_recovered", "status": "error"}, state_patch={
                                "status": "error", "error": "worker process ended before a terminal event",
                                "summary": str(state.get("summary") or "").strip() or "Sub-agent execution was interrupted by a server restart; its durable history remains available.",
                                "finished_at": time.time(), "parent_notified": False,
                            })
            self._hydrate_durable_mailbox()
        except Exception:
            self._artifact_store = None

    def _parent(self) -> Any:
        return self._parent_ref() if self._parent_ref is not None else None

    def _hydrate_durable_mailbox(self) -> None:
        if self._artifact_store is None:
            return
        try:
            states = self._artifact_store.list()
        except Exception:
            return
        for state in states:
            task_id = str(state.get("task_id") or "")
            status = str(state.get("status") or "")
            if not task_id or status == "running" or state.get("parent_notified") or task_id in self._durable_mailbox_seen:
                continue
            self._durable_mailbox_seen.add(task_id)
            self._queue_mailbox_event({
                "kind": "completion", "task_id": task_id, "specialist": str(state.get("specialist_key") or "specialist"),
                "status": status or "done", "summary": str(state.get("summary") or state.get("error") or "").strip(),
                "importance": "high" if status == "error" else "normal",
                "artifact": state.get("artifact") if isinstance(state.get("artifact"), dict) else {},
                "result_path": state.get("result_path"), "durable": True,
            })

    @property
    def tracker(self):
        if self._tracker is None:
            from mu.ui.progress import SubagentProgressTracker
            self._tracker = SubagentProgressTracker()
            if self._publish_fn is not None:
                self._tracker._publish = self._emit
        return self._tracker

    @property
    def _publish(self):
        return self._publish_fn

    @_publish.setter
    def _publish(self, fn):
        self._publish_fn = fn
        if self._tracker is not None:
            self._tracker._publish = self._emit

    def _emit(self, event: Dict[str, Any]) -> None:
        task_id = str(event.get("task_id") or "")
        recent_actions: Optional[List[Dict[str, Any]]] = None
        if task_id:
            # Progress can beat the start event to a newly connected SSE
            # client. Make every event self-describing so a row never degrades
            # to only ``d=1`` and a tool count.
            with self._lock:
                record = self._records.get(task_id)
                if record is not None:
                    event.setdefault("task", record.task)
                    event.setdefault("depth", record.depth)
                    event.setdefault("model", record.model)
                    event.setdefault("batch_id", record.batch_id)
                    event.setdefault("specialist_key", record.specialist_key)
                    event.setdefault("worker_id", record.worker_id)
                    event.setdefault("iter", record.iter)
                    event.setdefault("max_iter", record.max_iter)
                    action = event.get("action")
                    if isinstance(action, dict):
                        seq = int(action.get("seq") or 0)
                        existing = next(
                            (
                                item
                                for item in record.actions
                                if int(item.get("seq") or 0) == seq
                            ),
                            None,
                        )
                        if existing is None:
                            record.actions.append(dict(action))
                        else:
                            existing.update(action)
                        if len(record.actions) > 100:
                            del record.actions[:-100]
                    recent_actions = [dict(item) for item in record.actions]
        if task_id and self._artifact_store is not None:
            patch = {k: event[k] for k in ("task", "depth", "model", "status", "tool_count", "last_tool", "stuck", "stall", "repeat_count", "elapsed", "context_pct", "iter", "max_iter", "tokens_in", "summary", "error", "kill_reason", "batch_id", "specialist_key", "worker_id") if k in event and event[k] is not None}
            if recent_actions is not None:
                patch["actions"] = recent_actions
            try:
                self._artifact_store.record_event(task_id, event, state_patch=patch or None)
            except Exception:
                pass
        if self._publish_fn is not None:
            try:
                self._publish_fn(event)
            except Exception:
                pass

    def _link_tracker_task_id(self, tracker_agent_id: Optional[str], task_id: str) -> None:
        if tracker_agent_id is not None:
            try:
                self.tracker.set_task_id(tracker_agent_id, task_id)
            except Exception:
                pass

    def update_child_live(self, task_id: str, *, context_pct: float = 0.0, iter: int = 0, max_iter: int = 0, tokens_in: int = 0) -> None:
        with self._lock:
            rec = self._records.get(task_id)
            if rec is None:
                return
            rec.context_pct, rec.iter, rec.max_iter, rec.tokens_in = float(context_pct), int(iter), int(max_iter), int(tokens_in)
            batch_id = rec.batch_id
        self._emit({"kind": "subagent_progress", "task_id": task_id, "batch_id": batch_id, "context_pct": float(context_pct), "iter": int(iter), "max_iter": int(max_iter), "tokens_in": int(tokens_in)})

    def _on_lifecycle_signal(self, record: SubagentRecord, lifecycle: Any) -> None:
        try:
            snap = lifecycle.snapshot()
            self.tracker.set_state(record.tracker_agent_id, stuck=bool(snap.get("stuck")), stall=bool(snap.get("stall")), repeat_count=int(snap.get("consecutive_repeats", 0)))
            self._emit({"kind": "subagent_progress", "task_id": record.task_id, "status": record.status, "stuck": bool(snap.get("stuck")), "stall": bool(snap.get("stall")), "consecutive_repeats": int(snap.get("consecutive_repeats", 0)), "consecutive_stalls": int(snap.get("consecutive_stalls", 0)), "batch_id": record.batch_id})
        except Exception:
            pass

    @staticmethod
    def _tool_profile(disabled_tools: Sequence[str] | None) -> tuple[str, ...]:
        return tuple(sorted(str(x) for x in (disabled_tools or []) if str(x)))

    def acquire_specialist(self, specialist_key: str, *, depth: int, model: str, provider_key: str = "", disabled_tools: Sequence[str] | None = None) -> Optional[SpecialistRecord]:
        profile, key = self._tool_profile(disabled_tools), str(specialist_key or "general")
        with self._lock:
            for wid in reversed(self._specialist_order):
                worker = self._specialists.get(wid)
                if worker and worker.status == "idle" and worker.specialist_key == key and worker.depth == int(depth) and worker.model == str(model or "") and worker.provider_key == str(provider_key or "") and worker.disabled_tools == profile:
                    worker.status = "busy"; worker.last_active_at = time.time(); worker.delegation_count += 1
                    return worker
        return None

    def register_specialist(self, child: Any, *, specialist_key: str, depth: int, model: str, provider_key: str = "", disabled_tools: Sequence[str] | None = None) -> SpecialistRecord:
        worker = SpecialistRecord(worker_id="sp-" + uuid.uuid4().hex[:8], specialist_key=str(specialist_key or "general"), child=child, depth=int(depth), model=str(model or ""), provider_key=str(provider_key or ""), disabled_tools=self._tool_profile(disabled_tools))
        with self._lock:
            self._specialists[worker.worker_id] = worker; self._specialist_order.append(worker.worker_id)
        return worker

    def _release_specialist(self, record: SubagentRecord, *, failed: bool = False) -> None:
        if not record.worker_id:
            return
        with self._lock:
            worker = self._specialists.get(record.worker_id)
            if worker:
                worker.status = "failed" if failed else "idle"; worker.last_active_at = time.time()

    def specialist_snapshot(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [{"worker_id": w.worker_id, "specialist_key": w.specialist_key, "status": w.status, "depth": w.depth, "model": w.model, "provider": w.provider_key, "delegations": w.delegation_count, "last_active_at": w.last_active_at} for w in (self._specialists[x] for x in self._specialist_order if x in self._specialists)]

    def _queue_mailbox_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._mailbox_seq += 1
            value = {"mailbox_id": self._mailbox_seq, "created_at": time.time(), "delivered": False, **event}
            self._mailbox.append(value)
            if len(self._mailbox) > 256:
                self._mailbox = [x for x in self._mailbox if not x.get("delivered")][-128:] + [x for x in self._mailbox if x.get("delivered")][-32:]
            return dict(value)

    def publish_finding(self, task_id: str, summary: str, *, importance: str = "normal", refs: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        summary = str(summary or "").strip()
        if not summary:
            return {"ok": False, "error": "empty finding"}
        snap = self.snapshot(task_id)
        if str(snap.get("status") or "missing") == "missing":
            return {"ok": False, "error": "unknown task"}
        event = self._queue_mailbox_event({"kind": "finding", "task_id": task_id, "specialist": str(snap.get("specialist_key") or "specialist"), "summary": summary[:5000], "importance": importance if importance in {"low", "normal", "high", "critical"} else "normal", "refs": [str(x) for x in (refs or [])][:12]})
        if self._artifact_store is not None:
            try:
                self._artifact_store.record_event(task_id, {"kind": "subagent_finding", **event})
            except Exception:
                pass
        return {"ok": True, "mailbox_id": event["mailbox_id"]}

    def acknowledge_completion(self, task_id: str) -> None:
        with self._lock:
            for item in self._mailbox:
                if item.get("task_id") == task_id and item.get("kind") == "completion":
                    item["delivered"] = True
            rec = self._records.get(task_id)
            if rec:
                rec.parent_notified = True
        if self._artifact_store is not None:
            try:
                self._artifact_store.record_event(task_id, {"kind": "subagent_parent_notified", "task_id": task_id}, state_patch={"parent_notified": True})
            except Exception:
                pass

    def context_block(self, parent: Any = None, *, max_chars: int = 7000) -> str:
        parent = parent or self._parent()
        with self._lock:
            unread = [x for x in self._mailbox if not x.get("delivered")]
            unread.sort(key=lambda x: ({"critical": 0, "high": 1, "normal": 2, "low": 3}.get(str(x.get("importance") or "normal"), 2), x.get("mailbox_id", 0)))
        if not unread:
            return ""
        lines, delivered = ["Unread specialist mailbox (new information only):"], []
        for item in unread:
            refs = item.get("refs") or []
            ref_text = f" refs={','.join(str(x) for x in refs[:6])}" if refs else ""
            artifact = item.get("artifact") if isinstance(item.get("artifact"), dict) else {}
            durable = f" artifact_id={artifact.get('artifact_id')}" if artifact.get("artifact_id") else (f" result_path={item.get('result_path')}" if item.get("result_path") else "")
            line = f"- [{item.get('kind')}/{item.get('importance', 'normal')}] {item.get('specialist')} {item.get('task_id')}: {str(item.get('summary') or '')[:3000]}{ref_text}{durable}"
            if len("\n".join(lines + [line])) > max_chars:
                break
            lines.append(line); delivered.append(int(item.get("mailbox_id") or 0))
            if parent is not None and item.get("summary"):
                try:
                    parent.task_memory.save(content=f"Specialist {item.get('specialist')} ({item.get('task_id')}): {str(item.get('summary'))[:5000]}", tags=["subagent", str(item.get("task_id")), "mailbox"], source=f"subagent:{item.get('task_id')}", kind="finding", status="active")
                except Exception:
                    pass
        with self._lock:
            for item in self._mailbox:
                if int(item.get("mailbox_id") or 0) in delivered:
                    item["delivered"] = True
        for item in unread:
            if int(item.get("mailbox_id") or 0) in delivered and item.get("kind") == "completion":
                self.acknowledge_completion(str(item.get("task_id") or ""))
        return "\n".join(lines)

    def register(self, child: Any, *, task: str, depth: int, lifecycle: Any, tracker_agent_id: Optional[str] = None, model: Optional[str] = None, specialist_key: str = "general", worker_id: str = "", reused_specialist: bool = False, max_iterations: int = 0) -> SubagentRecord:
        with self._lock:
            task_id = "sa-" + uuid.uuid4().hex[:8]
            if not any(r.status == "running" for r in self._records.values()):
                self._batch_seq += 1; self._active_batch_id = f"sab-{int(time.time()*1000):x}-{self._batch_seq}"
            batch_id = self._active_batch_id
        if tracker_agent_id is None:
            try:
                tracker_agent_id = self.tracker.open(depth=depth, task=task)
            except Exception:
                tracker_agent_id = None
        record = SubagentRecord(task_id=task_id, task=task, depth=depth, child=child, lifecycle=lifecycle, tracker_agent_id=tracker_agent_id, model=model or "", batch_id=batch_id, specialist_key=specialist_key, worker_id=worker_id, reused_specialist=bool(reused_specialist), max_iter=max(0, int(max_iterations or 0)))
        lifecycle.on_signal = lambda lc, r=record: self._on_lifecycle_signal(r, lc)
        with self._lock:
            self._records[task_id] = record; self._order.append(task_id)
        self._link_tracker_task_id(tracker_agent_id, task_id)
        if self._artifact_store is not None:
            try:
                self._artifact_store.start(task_id, {"task": task, "depth": depth, "model": model or "", "batch_id": batch_id, "specialist_key": specialist_key, "worker_id": worker_id, "reused_specialist": bool(reused_specialist)})
            except Exception:
                pass
        self._emit({"kind": "subagent_start", "task_id": task_id, "task": task, "depth": depth, "model": model or "", "batch_id": batch_id, "specialist_key": specialist_key, "worker_id": worker_id, "reused_specialist": bool(reused_specialist), "iter": 0, "max_iter": record.max_iter})
        return record

    def launch(self, record: SubagentRecord, task: str) -> None:
        thread = NamedThread(target=self._run_child, args=(record, task), daemon=True, name=f"subagent-{record.task_id}")
        with self._lock:
            record.thread = thread
        thread.start()

    def _run_child(self, record: SubagentRecord, task: str) -> None:
        child, lifecycle = record.child, record.lifecycle
        try:
            lifecycle.start_watchdog(child)
        except Exception:
            pass
        try:
            result = child.send_message(task) or {}
        except Exception as exc:
            logger.warning("subagent %s raised %s", record.task_id, exc)
            summary = _extract_partial_summary(getattr(child, "session_manager", None)) or f"Sub-agent failed after starting: {exc}"
            history = list(getattr(getattr(child, "session_manager", None), "history", []) or [])
            try:
                tool_calls = int(lifecycle.snapshot().get("tool_count", 0) or 0)
            except Exception:
                tool_calls = 0
            with self._lock:
                record.status, record.summary, record.error, record.tool_calls, record.history_length = "error", summary, str(exc), tool_calls, len(history)
                record.finished_at, record.finished_wall_at = time.monotonic(), time.time()
            self._persist_finish(record, history); record.done_event.set(); lifecycle.close(); self._release_specialist(record, failed=True); self._queue_completion(record)
            self._close_tracker(record, tool_count=tool_calls, summary=summary, error=str(exc), status="error")
            return
        raw_status = str(result.get("status") or ""); final_text = str(result.get("assistant_text") or "").strip(); kill_reason = None
        if raw_status == "killed" or getattr(child, "_subagent_cancelled", False):
            status = "killed"; kill_reason = getattr(child, "_subagent_kill_reason", None) or "killed"; lifecycle.cancel(kill_reason)
        elif raw_status == "error":
            status = "error"
        else:
            status = "done"
        if not final_text:
            final_text = _extract_partial_summary(child.session_manager) or "(sub-agent finished without producing a final text response)"
        tokens = result.get("tokens") or {}
        try:
            lc = lifecycle.snapshot()
        except Exception:
            lc = {}
        tool_calls = max(len(result.get("tool_calls") or []), int(lc.get("tool_count", 0) or 0)); history = list(getattr(child.session_manager, "history", []) or [])
        with self._lock:
            record.status, record.summary, record.tokens, record.tool_calls = status, final_text, tokens, tool_calls
            record.error, record.kill_reason, record.history_length = (None if status != "error" else str(result.get("error") or final_text)), kill_reason, len(history)
            record.finished_at, record.finished_wall_at = time.monotonic(), time.time()
        self._persist_finish(record, history); record.done_event.set(); lifecycle.close(); self._release_specialist(record, failed=(status == "error")); self._queue_completion(record)
        self._close_tracker(record, tool_count=tool_calls, summary=final_text, error=record.error, status=status, kill_reason=kill_reason)

    def _queue_completion(self, record: SubagentRecord) -> None:
        # The just-finished task already exists in durable storage. Mark it seen
        # before queuing the live event so recovery scans cannot enqueue a twin.
        with self._lock:
            self._durable_mailbox_seen.add(record.task_id)
        self._queue_mailbox_event({"kind": "completion", "task_id": record.task_id, "specialist": record.specialist_key, "status": record.status, "summary": record.summary, "importance": "high" if record.status == "error" else "normal", "artifact": dict(record.artifact), "result_path": self._artifact_store.relative_path(record.task_id, "result.json") if self._artifact_store is not None else None})

    def _persist_finish(self, record: SubagentRecord, history: List[Dict[str, Any]]) -> None:
        if self._artifact_store is None:
            return
        try:
            state = self._artifact_store.finish(record.task_id, {"task": record.task, "depth": record.depth, "model": record.model, "batch_id": record.batch_id, "status": record.status, "summary": record.summary, "tokens": dict(record.tokens), "tool_calls": record.tool_calls, "error": record.error, "kill_reason": record.kill_reason, "history_length": record.history_length, "started_at": record.started_wall_at, "finished_at": record.finished_wall_at or time.time(), "specialist_key": record.specialist_key, "worker_id": record.worker_id, "reused_specialist": record.reused_specialist, "parent_notified": False}, history)
            if isinstance(state.get("artifact"), dict):
                record.artifact = dict(state["artifact"])
        except Exception:
            pass

    def _close_tracker(self, record: SubagentRecord, *, tool_count: int, summary: str, error: Optional[str], status: str, kill_reason: Optional[str] = None) -> None:
        if record.tracker_agent_id is not None:
            try:
                self.tracker.close(record.tracker_agent_id, tool_count=tool_count, summary=summary, error=error, status=status, kill_reason=kill_reason)
            except Exception:
                pass
        self._emit({"kind": "subagent_end", "task_id": record.task_id, "status": status, "summary": summary, "tokens": dict(record.tokens), "tool_calls": tool_count, "elapsed": round(max(0.0, (record.finished_at or time.monotonic()) - record.started_at), 2), "kill_reason": kill_reason, "error": error, "batch_id": record.batch_id, "artifact": dict(record.artifact), "specialist_key": record.specialist_key, "worker_id": record.worker_id, "reused_specialist": record.reused_specialist})

    def get(self, task_id: str) -> Optional[SubagentRecord]:
        with self._lock:
            return self._records.get(task_id)

    def list(self) -> List[SubagentRecord]:
        with self._lock:
            return [self._records[x] for x in self._order]

    def has_active(self) -> bool:
        with self._lock:
            return any(r.status == "running" for r in self._records.values())

    def snapshot(self, task_id: str) -> Dict[str, Any]:
        with self._lock:
            rec = self._records.get(task_id)
            if rec is None:
                durable = self._artifact_store.load(task_id) if self._artifact_store is not None else None
                return durable or {"status": "missing", "task_id": task_id}
            base = {"task_id": rec.task_id, "task": rec.task, "depth": rec.depth, "status": rec.status, "summary": rec.summary, "tokens": dict(rec.tokens), "tool_calls": rec.tool_calls, "error": rec.error, "kill_reason": rec.kill_reason, "history_length": rec.history_length, "context_pct": round(float(rec.context_pct), 1), "iter": rec.iter, "max_iter": rec.max_iter, "tokens_in": rec.tokens_in, "model": rec.model, "batch_id": rec.batch_id, "started_at": rec.started_wall_at, "finished_at": rec.finished_wall_at, "artifact": dict(rec.artifact), "durable": bool(self._artifact_store), "state_path": self._artifact_store.relative_path(rec.task_id) if self._artifact_store is not None else None, "result_path": self._artifact_store.relative_path(rec.task_id, "result.json") if self._artifact_store is not None else None, "specialist_key": rec.specialist_key, "worker_id": rec.worker_id, "reused_specialist": rec.reused_specialist, "actions": [dict(item) for item in rec.actions]}
        try:
            lc = rec.lifecycle.snapshot() if rec.lifecycle is not None else {}
        except Exception:
            lc = {}
        base.update({"tool_count": lc.get("tool_count", base["tool_calls"]), "tool_diversity": lc.get("tool_diversity", 0), "last_tool": lc.get("last_tool"), "stuck": bool(lc.get("stuck", False)), "stall": bool(lc.get("stall", False)), "consecutive_repeats": int(lc.get("consecutive_repeats", 0)), "consecutive_stalls": int(lc.get("consecutive_stalls", 0)), "elapsed": lc.get("elapsed", 0.0)})
        return base

    def snapshot_all(self) -> List[Dict[str, Any]]:
        return [self.snapshot(x.task_id) for x in self.list()]

    def snapshot_active(self) -> List[Dict[str, Any]]:
        return [self.snapshot(x.task_id) for x in self.list() if x.status == "running"]

    def active_batch_id(self) -> str:
        with self._lock:
            return self._active_batch_id if any(r.status == "running" for r in self._records.values()) else ""

    def wait(self, task_id: str, *, timeout: Optional[float] = None) -> Dict[str, Any]:
        rec = self.get(task_id)
        if rec is None:
            return self.snapshot(task_id)
        if rec.status == "running":
            rec.done_event.wait(timeout)
        snap = self.snapshot(task_id)
        if str(snap.get("status") or "") != "running":
            self.acknowledge_completion(task_id)
        return snap

    def cancel(self, task_id: str, *, grace_seconds: float = 5.0) -> Dict[str, Any]:
        rec = self.get(task_id)
        if rec is None:
            return self.snapshot(task_id)
        if rec.status != "running":
            self.acknowledge_completion(task_id); return self.snapshot(task_id)
        try:
            rec.child._subagent_cancelled = True; rec.child._subagent_kill_reason = "killed_by_parent"; rec.lifecycle.cancel("killed_by_parent")
        except Exception:
            pass
        try:
            self.tracker.set_state(rec.tracker_agent_id, status="killed", kill_reason="killed_by_parent")
        except Exception:
            pass
        self._emit({"kind": "subagent_end", "task_id": task_id, "status": "killed", "kill_reason": "killed_by_parent", "elapsed": round(max(0.0, time.monotonic()-rec.started_at), 2), "batch_id": rec.batch_id})
        deadline = time.monotonic() + max(0.0, grace_seconds)
        while time.monotonic() < deadline:
            if rec.status != "running" or (rec.thread is not None and not rec.thread.is_alive()):
                break
            time.sleep(0.05)
        rec.done_event.set(); snap = self.snapshot(task_id)
        if str(snap.get("status") or "") != "running":
            self.acknowledge_completion(task_id)
        return snap

    def shutdown(self) -> None:
        running = [x for x in self.list() if x.status == "running"]
        for rec in running:
            try:
                rec.child._subagent_cancelled = True; rec.child._subagent_kill_reason = "session_shutdown"; rec.lifecycle.cancel("session_shutdown")
            except Exception:
                pass
        for rec in running:
            if rec.thread is not None and rec.thread.is_alive():
                try:
                    rec.thread.join(timeout=2.0)
                except Exception:
                    pass
        with self._lock:
            for worker in self._specialists.values():
                worker.status = "stopped"


__all__ = ["SpecialistRecord", "SubagentRecord", "SubagentRegistry", "_extract_partial_summary"]
