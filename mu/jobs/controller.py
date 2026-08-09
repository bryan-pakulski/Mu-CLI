"""Background scheduler/controller for durable engineering jobs."""

from __future__ import annotations

import logging
import os
import socket
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Dict, Optional

from .models import JobStatus
from .runner import JobRunOutcome, SessionJobRunner
from .service import JobService, JobStateError


logger = logging.getLogger(__name__)


class JobController:
    """Lease and execute queued jobs independently of any connected client."""

    def __init__(
        self,
        service: JobService,
        runner: SessionJobRunner,
        *,
        max_workers: int = 5,
        poll_interval: float = 1.0,
        lease_ttl_seconds: int = 45,
    ):
        self.service = service
        self.runner = runner
        self.max_workers = max(1, int(max_workers))
        self.poll_interval = max(0.1, float(poll_interval))
        self.lease_ttl_seconds = max(15, int(lease_ttl_seconds))
        self.controller_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="mucli-job")
        self._active: Dict[str, Future] = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.service.recover_expired_leases()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="mucli-job-controller", daemon=True)
        self._thread.start()

    def stop(self, *, wait: bool = False) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0 if not wait else None)
        self._executor.shutdown(wait=wait, cancel_futures=True)

    @property
    def active_job_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._active.keys())

    def snapshot(self) -> dict:
        return {
            "controller_id": self.controller_id,
            "running": bool(self._thread and self._thread.is_alive() and not self._stop.is_set()),
            "active_jobs": self.active_job_ids,
            "max_workers": self.max_workers,
        }

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("durable job controller tick failed")
            self._stop.wait(self.poll_interval)

    def tick(self) -> int:
        """One deterministic scheduler pass. Returns number of jobs submitted."""
        self._reap()
        self.service.recover_expired_leases()
        with self._lock:
            capacity = self.max_workers - len(self._active)
        if capacity <= 0:
            return 0

        candidates = self.service.list(
            statuses=[JobStatus.QUEUED, JobStatus.RECOVERING],
            limit=max(capacity * 4, 20),
        )
        submitted = 0
        for job in reversed(candidates):
            if submitted >= capacity:
                break
            with self._lock:
                if job.id in self._active:
                    continue
                future = self._executor.submit(self._execute_job, job.id)
                self._active[job.id] = future
            submitted += 1
        return submitted

    def _reap(self) -> None:
        finished: list[tuple[str, Future]] = []
        with self._lock:
            for job_id, future in list(self._active.items()):
                if future.done():
                    finished.append((job_id, future))
                    self._active.pop(job_id, None)
        for job_id, future in finished:
            try:
                future.result()
            except BaseException:
                logger.exception("durable job worker crashed for %s", job_id)

    def _worker_id(self, job_id: str) -> str:
        return f"{self.controller_id}:{job_id[:10]}"

    def _heartbeat_loop(self, job_id: str, worker_id: str, stop: threading.Event) -> None:
        interval = max(3.0, self.lease_ttl_seconds / 3.0)
        while not stop.wait(interval):
            try:
                if not self.service.heartbeat(
                    job_id, worker_id, ttl_seconds=self.lease_ttl_seconds
                ):
                    return
            except Exception:
                logger.exception("job heartbeat failed for %s", job_id)
                return

    def _execute_job(self, job_id: str) -> None:
        worker_id = self._worker_id(job_id)
        if not self.service.acquire(job_id, worker_id, ttl_seconds=self.lease_ttl_seconds):
            return

        heartbeat_stop = threading.Event()
        heartbeat_thread: Optional[threading.Thread] = None
        attempt = None
        try:
            job = self.service.get(job_id)
            if job.status == JobStatus.QUEUED:
                job = self.service.transition(job_id, JobStatus.PREPARING, reason="controller claimed queued job")
            elif job.status != JobStatus.RECOVERING:
                return

            session_name = self.runner.session_name(job)
            attempt = self.service.start_attempt(
                job_id,
                worker_id=worker_id,
                session_name=session_name,
                metadata={"controller_id": self.controller_id},
            )
            job = self.service.transition(job_id, JobStatus.RUNNING, reason="job attempt started")

            heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                args=(job_id, worker_id, heartbeat_stop),
                name=f"mucli-job-heartbeat-{job_id[:8]}",
                daemon=True,
            )
            heartbeat_thread.start()
            outcome = self.runner.run(job, attempt)
            self._finish_outcome(job_id, attempt.id, outcome)
        except JobStateError:
            logger.info("job %s changed state while worker was starting", job_id)
        except BaseException as exc:
            logger.exception("job %s execution failed", job_id)
            if attempt is not None:
                try:
                    self.service.finish_attempt(attempt.id, status="failed", error=str(exc))
                except Exception:
                    pass
            try:
                current = self.service.get(job_id)
                if current.status not in {JobStatus.CANCELLED, JobStatus.MERGED, JobStatus.FAILED}:
                    self.service.transition(job_id, JobStatus.FAILED, reason="job worker crashed", payload={"error": str(exc)})
            except Exception:
                pass
        finally:
            heartbeat_stop.set()
            if heartbeat_thread and heartbeat_thread.is_alive():
                heartbeat_thread.join(timeout=1.0)
            try:
                self.service.release(job_id, worker_id, reason="attempt finished")
            except Exception:
                pass

    def _finish_outcome(self, job_id: str, attempt_id: str, outcome: JobRunOutcome) -> None:
        current = self.service.get(job_id)
        total_cost = float(current.cost_usd or 0.0) + float(outcome.cost_usd or 0.0)
        self.service.store.update_runtime_fields(job_id, cost_usd=total_cost)

        if outcome.kind == "needs_human":
            self.service.finish_attempt(
                attempt_id,
                status="needs_human",
                cost_usd=outcome.cost_usd,
                metadata={"attention": outcome.attention_payload},
            )
            current = self.service.get(job_id)
            if current.status == JobStatus.CANCELLED:
                return
            self.service.require_human(
                job_id,
                outcome.attention_reason,
                outcome.attention_detail,
                payload=outcome.attention_payload,
            )
            return

        if outcome.kind == "completed":
            self.service.finish_attempt(
                attempt_id,
                status="completed",
                cost_usd=outcome.cost_usd,
                metadata={"agent_status": outcome.status},
            )
            current = self.service.get(job_id)
            if current.status == JobStatus.CANCELLED:
                return
            self.service.transition(job_id, JobStatus.VERIFYING, reason="implementation attempt completed")
            self.service.store.append_event(
                job_id,
                "verification_pending",
                reason="Milestone 3 verifier not implemented yet",
                payload={"attempt_id": attempt_id},
            )
            return

        self.service.finish_attempt(
            attempt_id,
            status="failed",
            error=outcome.error,
            cost_usd=outcome.cost_usd,
            metadata={"agent_status": outcome.status},
        )
        current = self.service.get(job_id)
        if current.status == JobStatus.CANCELLED:
            return
        target = JobStatus.ENVIRONMENT_ERROR if outcome.status == "environment_error" else JobStatus.FAILED
        self.service.transition(
            job_id,
            target,
            reason="job attempt failed",
            payload={"error": outcome.error, "agent_status": outcome.status},
        )
