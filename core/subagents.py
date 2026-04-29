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


@dataclass
class SubAgentState:
    worker_id: str
    task_id: str
    title: str
    status: str
    started_at: float | None = None
    updated_at: float | None = None
    ended_at: float | None = None
    summary: str = ""
    error: str = ""


class SubAgentManager:
    def __init__(self, max_parallel: int = 3):
        self.max_parallel = max(1, min(int(max_parallel or 3), 16))
        self._lock = threading.Lock()
        self._pending: deque[tuple[SubAgentTask, Callable[[SubAgentTask], dict[str, Any]]]] = deque()
        self._states: dict[str, SubAgentState] = {}
        self._futures: dict[str, Any] = {}
        self._executor = ThreadPoolExecutor(max_workers=self.max_parallel, thread_name_prefix="mucli-subagent")

    def set_max_parallel(self, value: int):
        value = max(1, min(int(value or 1), 16))
        with self._lock:
            self.max_parallel = value

    def submit(self, title: str, payload: dict[str, Any], worker_fn: Callable[[SubAgentTask], dict[str, Any]]) -> str:
        task = SubAgentTask(task_id=str(uuid.uuid4()), title=title, payload=payload)
        worker_id = f"sa-{task.task_id[:8]}"
        now = time.time()
        with self._lock:
            self._states[worker_id] = SubAgentState(
                worker_id=worker_id,
                task_id=task.task_id,
                title=title,
                status="queued",
                started_at=None,
                updated_at=now,
            )
            self._pending.append((task, worker_fn))
        self._schedule()
        return worker_id

    def _schedule(self):
        with self._lock:
            running = sum(1 for s in self._states.values() if s.status == "running")
            while self._pending and running < self.max_parallel:
                task, worker_fn = self._pending.popleft()
                worker_id = f"sa-{task.task_id[:8]}"
                state = self._states.get(worker_id)
                if not state:
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
            if state:
                state.status = status
                state.summary = summary
                state.error = error
                state.ended_at = time.time()
                state.updated_at = state.ended_at
        self._schedule()

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [asdict(s) for s in self._states.values()]

    def counts(self) -> dict[str, int]:
        base = {"queued": 0, "running": 0, "completed": 0, "failed": 0, "cancelled": 0, "timed_out": 0}
        with self._lock:
            for state in self._states.values():
                base[state.status] = base.get(state.status, 0) + 1
        return base
