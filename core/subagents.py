import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from typing import Any, Callable


@dataclass
class SubAgentTask:
    task_id: str
    title: str
    payload: dict[str, Any]
    batch_id: str


@dataclass
class SubAgentState:
    worker_id: str
    task_id: str
    batch_id: str
    title: str
    status: str
    started_at: float | None = None
    updated_at: float | None = None
    ended_at: float | None = None
    summary: str = ""
    error: str = ""


class SubAgentManager:
    def __init__(self, max_parallel: int = 3, task_timeout_s: int = 900):
        self.max_parallel = max(1, min(int(max_parallel or 3), 16))
        self.task_timeout_s = max(1, int(task_timeout_s or 900))
        self._lock = threading.Lock()
        self._pending: deque[tuple[str, SubAgentTask, Callable[[SubAgentTask], dict[str, Any]]]] = deque()
        self._states: dict[str, SubAgentState] = {}
        self._futures: dict[str, Any] = {}
        self._executor = ThreadPoolExecutor(max_workers=self.max_parallel, thread_name_prefix="mucli-subagent")

    def set_limits(self, *, max_parallel: int | None = None, task_timeout_s: int | None = None):
        with self._lock:
            if max_parallel is not None:
                self.max_parallel = max(1, min(int(max_parallel or 1), 16))
            if task_timeout_s is not None:
                self.task_timeout_s = max(1, int(task_timeout_s or 1))

    def submit(self, title: str, payload: dict[str, Any], worker_fn: Callable[[SubAgentTask], dict[str, Any]], *, batch_id: str | None = None) -> str:
        batch = str(batch_id or f"batch-{uuid.uuid4().hex[:8]}")
        task = SubAgentTask(task_id=str(uuid.uuid4()), title=title, payload=payload, batch_id=batch)
        worker_id = f"sa-{task.task_id[:8]}"
        now = time.time()
        with self._lock:
            self._states[worker_id] = SubAgentState(
                worker_id=worker_id,
                task_id=task.task_id,
                batch_id=batch,
                title=title,
                status="queued",
                started_at=None,
                updated_at=now,
            )
            self._pending.append((worker_id, task, worker_fn))
        self._schedule()
        return worker_id

    def submit_many(self, tasks: list[dict[str, Any]], worker_fn: Callable[[SubAgentTask], dict[str, Any]], *, batch_id: str | None = None) -> tuple[str, list[str]]:
        batch = str(batch_id or f"batch-{uuid.uuid4().hex[:8]}")
        worker_ids = [self.submit(str(item.get("title", "task")), dict(item.get("payload", {})), worker_fn, batch_id=batch) for item in tasks]
        return batch, worker_ids

    def _schedule(self):
        with self._lock:
            running = sum(1 for s in self._states.values() if s.status == "running")
            while self._pending and running < self.max_parallel:
                worker_id, task, worker_fn = self._pending.popleft()
                state = self._states.get(worker_id)
                if not state or state.status == "cancelled":
                    continue
                now = time.time()
                state.status = "running"
                state.started_at = now
                state.updated_at = now
                future = self._executor.submit(self._run_worker, worker_id, task, worker_fn)
                self._futures[worker_id] = future
                running += 1

    def _run_worker(self, worker_id: str, task: SubAgentTask, worker_fn):
        try:
            result = worker_fn(task) or {}
            status = str(result.get("status", "completed"))
            summary = str(result.get("summary", ""))
            error = str(result.get("error", ""))
        except Exception as exc:
            status = "failed"
            summary = ""
            error = str(exc)
        with self._lock:
            state = self._states.get(worker_id)
            if state and state.status != "cancelled":
                state.status = status
                state.summary = summary
                state.error = error
                state.ended_at = time.time()
                state.updated_at = state.ended_at
        self._schedule()

    def cancel(self, worker_ids: list[str] | None = None, *, batch_id: str | None = None) -> int:
        targets = set(worker_ids or [])
        cancelled = 0
        with self._lock:
            for wid, state in self._states.items():
                if batch_id and state.batch_id != batch_id:
                    continue
                if targets and wid not in targets:
                    continue
                if state.status in {"completed", "failed", "cancelled", "timed_out"}:
                    continue
                state.status = "cancelled"
                state.updated_at = time.time()
                state.ended_at = state.updated_at
                cancelled += 1
        return cancelled

    def _apply_timeouts(self):
        now = time.time()
        with self._lock:
            for state in self._states.values():
                if state.status != "running" or not state.started_at:
                    continue
                if now - state.started_at > self.task_timeout_s:
                    state.status = "timed_out"
                    state.updated_at = now
                    state.ended_at = now
                    state.error = f"timeout after {self.task_timeout_s}s"

    def wait(self, worker_ids: list[str], timeout_s: int | None = None) -> dict[str, Any]:
        deadline = time.time() + max(1, int(timeout_s or self.task_timeout_s))
        worker_set = set(worker_ids)
        while time.time() < deadline:
            self._apply_timeouts()
            with self._lock:
                done = all(self._states.get(w) and self._states[w].status in {"completed", "failed", "cancelled", "timed_out"} for w in worker_set)
            if done:
                break
            time.sleep(0.05)
            self._schedule()
        snapshot = [s for s in self.snapshot() if s.get("worker_id") in worker_set]
        return {"workers": snapshot, "counts": self.counts()}

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [asdict(s) for s in self._states.values()]

    def counts(self) -> dict[str, int]:
        base = {"queued": 0, "running": 0, "completed": 0, "failed": 0, "cancelled": 0, "timed_out": 0}
        with self._lock:
            for state in self._states.values():
                base[state.status] = base.get(state.status, 0) + 1
        return base
