"""Background scheduler/controller for durable engineering-job subprocesses."""

from __future__ import annotations

import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Callable, Dict, Optional

from utils.config import HISTORY_DIR

from .models import JobStatus
from .service import JobService


logger = logging.getLogger(__name__)


@dataclass
class WorkerHandle:
    job_id: str
    phase: str
    worker_id: str
    process: subprocess.Popen
    log_path: str
    log_handle: Optional[IO[bytes]] = None
    # Round-35 F6: monotonic timestamp of the first SIGTERM sent for
    # cancellation — None until termination starts. Drives SIGKILL
    # escalation after the grace window (SIGTERM-ignoring workers must not
    # hold a controller slot forever).
    terminate_started: Optional[float] = None


def _retry_due(job, now: float) -> bool:
    """True when the job has no pending backoff or the backoff has elapsed."""
    try:
        next_run_at = float((job.metadata or {}).get("next_run_at") or 0.0)
    except (TypeError, ValueError):
        return True
    return next_run_at <= float(now)


class JobController:
    """Lease jobs and launch isolated implementation/verification processes."""

    # Round-35 F6: seconds between SIGTERM and SIGKILL-escalation for a
    # cancelled worker that has not exited.
    _TERMINATION_GRACE_S = 10.0
    # Round-41 F7a: per-phase worker log size cap before rotation.
    _LOG_MAX_BYTES = 32 * 1024 * 1024

    def __init__(
        self,
        service: JobService,
        *,
        max_workers: int = 5,
        poll_interval: float = 1.0,
        lease_ttl_seconds: int = 45,
        python_executable: Optional[str] = None,
        project_root: Optional[str] = None,
        process_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
        notify: Optional[Callable[[Dict[str, Any]], None]] = None,
        retention_days: Optional[float] = None,
        retention_interval_seconds: float = 3600.0,
        drift_check_interval_seconds: float = 300.0,
    ):
        self.service = service
        # Attention push: called (from the controller thread) with a
        # session-agnostic event dict whenever a job enters or leaves a
        # human-attention state. The GUI wires this to the SSE bus.
        self.notify = notify
        # Retention sweeper: purge evidence/logs/worktrees of ARCHIVED
        # historic jobs older than `retention_days` (None/0 disables).
        self.retention_days = retention_days
        self.retention_interval_seconds = max(60.0, float(retention_interval_seconds or 3600.0))
        self._last_retention_sweep = 0.0
        # Base-drift watch: re-assess READY_FOR_REVIEW jobs periodically so
        # a base branch that moves while the job waits for review flips it
        # to CONFLICTED (and pushes attention) instead of staying "ready".
        self.drift_check_interval_seconds = max(0.0, float(drift_check_interval_seconds or 0.0))
        self._last_drift_check = 0.0
        self._last_status_fp: Optional[Dict[str, tuple]] = None
        self.max_workers = max(1, int(max_workers))
        self.poll_interval = max(0.1, float(poll_interval))
        self.lease_ttl_seconds = max(15, int(lease_ttl_seconds))
        self.python_executable = python_executable or sys.executable or "python3"
        self.project_root = os.path.abspath(
            project_root or str(Path(__file__).resolve().parents[2])
        )
        self.process_factory = process_factory
        self.controller_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self.log_root = os.path.join(HISTORY_DIR, "jobs", "logs")
        os.makedirs(self.log_root, exist_ok=True)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._active: Dict[str, WorkerHandle] = {}
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.service.recover_expired_leases()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop,
            name="mucli-job-controller",
            daemon=True,
        )
        self._thread.start()

    def stop(self, *, wait: bool = False) -> None:
        """Stop scheduling without killing active child workers.

        Round-49 F12: wait=True previously joined the controller thread
        with timeout=None AND waited on every child process with NO
        timeout — a wedged worker made shutdown hang forever. Bounded:
        10s grace for children, then SIGTERM → 5s → SIGKILL.
        """
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=None if wait else 2.0)
        if wait:
            with self._lock:
                handles = list(self._active.values())
            for handle in handles:
                try:
                    handle.process.wait(timeout=10.0)
                except Exception:
                    # Wedged or already gone — escalate TERM → KILL.
                    try:
                        handle.process.terminate()
                        handle.process.wait(timeout=5.0)
                    except Exception:
                        try:
                            handle.process.kill()
                        except Exception:
                            pass
            self._reap()

    @property
    def active_job_ids(self) -> list[str]:
        with self._lock:
            return sorted(self._active.keys())

    def snapshot(self) -> dict:
        with self._lock:
            processes = {
                job_id: {
                    "pid": getattr(handle.process, "pid", None),
                    "phase": handle.phase,
                    "worker_id": handle.worker_id,
                    "log_path": handle.log_path,
                }
                for job_id, handle in self._active.items()
            }
        return {
            "controller_id": self.controller_id,
            "running": bool(self._thread and self._thread.is_alive() and not self._stop.is_set()),
            "active_jobs": sorted(processes.keys()),
            "processes": processes,
            "max_workers": self.max_workers,
            "execution_isolation": "subprocess+git-worktree",
        }

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("durable job controller tick failed")
            self._stop.wait(self.poll_interval)

    @staticmethod
    def _phase_for_status(status: JobStatus) -> str:
        return "verification" if status == JobStatus.VERIFYING else "implementation"

    def tick(self) -> int:
        """One deterministic scheduler pass; returns processes started."""
        self._reap()
        self._terminate_cancelled()
        self._enforce_runtime_deadlines()
        self.service.recover_expired_leases()
        self._maybe_check_base_drift()
        self._watch_attention()
        self._maybe_sweep_retention()
        with self._lock:
            capacity = self.max_workers - len(self._active)
        if capacity <= 0:
            return 0

        candidates = self.service.list(
            statuses=[JobStatus.QUEUED, JobStatus.RECOVERING, JobStatus.VERIFYING],
            limit=max(capacity * 5, 25),
        )
        started = 0
        now = float(self.service.store._clock())
        for job in reversed(candidates):
            if started >= capacity:
                break
            with self._lock:
                if job.id in self._active:
                    continue
            # Backoff gate: a transient-failure requeue stamps next_run_at;
            # leave the job QUEUED until that time has passed.
            if job.status == JobStatus.QUEUED and not _retry_due(job, now):
                continue
            phase = self._phase_for_status(job.status)
            if self._spawn(job.id, phase):
                started += 1
        return started

    # ------------------------------------------------------------ attention push

    _ATTENTION_STATUSES = frozenset({JobStatus.NEEDS_HUMAN.value, JobStatus.CONFLICTED.value})

    def _watch_attention(self) -> None:
        """Detect jobs entering/leaving a human-attention state between
        ticks and push one `job_attention` event per change plus the
        aggregate count. Workers run out-of-process, so the controller's
        SQLite poll is the only place that sees every transition."""
        if self.notify is None:
            return
        try:
            current = self.service.store.status_fingerprints()
        except Exception:
            logger.debug("attention watcher: fingerprint read failed", exc_info=True)
            return
        previous = self._last_status_fp
        self._last_status_fp = current
        if previous is None:
            # First tick after start: announce the standing backlog once so
            # a restarted daemon still surfaces jobs already waiting.
            waiting = [jid for jid, (st, _v) in current.items() if st in self._ATTENTION_STATUSES]
            if waiting:
                self._emit_attention(entered=waiting, left=[])
            return
        entered, left = [], []
        for job_id, (status, _version) in current.items():
            was = previous.get(job_id, ("", 0))[0]
            now_attn = status in self._ATTENTION_STATUSES
            was_attn = was in self._ATTENTION_STATUSES
            if now_attn and not was_attn:
                entered.append(job_id)
            elif was_attn and not now_attn:
                left.append(job_id)
        for job_id, (status, _v) in previous.items():
            if job_id not in current and status in self._ATTENTION_STATUSES:
                left.append(job_id)  # deleted while waiting
        if entered or left:
            self._emit_attention(entered=entered, left=left)

    def _emit_attention(self, *, entered: list, left: list) -> None:
        try:
            summary = self.service.attention_summary()
        except Exception:
            logger.debug("attention watcher: summary failed", exc_info=True)
            return
        by_id = {j["id"]: j for j in summary.get("jobs", [])}
        event = {
            "kind": "job_attention",
            "count": int(summary.get("count", 0)),
            "entered": [by_id[j] for j in entered if j in by_id],
            "left": list(left),
            "jobs": summary.get("jobs", []),
            "ts": float(self.service.store._clock()),
        }
        try:
            self.notify(event)
        except Exception:
            logger.debug("attention notify failed", exc_info=True)

    # ------------------------------------------------------------ base drift

    def _maybe_check_base_drift(self) -> None:
        if self.drift_check_interval_seconds <= 0:
            return
        now = time.monotonic()
        if self._last_drift_check and now - self._last_drift_check < self.drift_check_interval_seconds:
            return
        self._last_drift_check = now
        try:
            ready = self.service.list(statuses=[JobStatus.READY_FOR_REVIEW], limit=100)
        except Exception:
            logger.debug("drift watch: list failed", exc_info=True)
            return
        if not ready:
            return
        from .base_drift import apply_base_drift

        for job in ready:
            try:
                report = apply_base_drift(self.service, job.id, source="controller")
                if report.mergeable is False:
                    logger.warning(
                        "Job %s: base %s drifted (%d commits); branch no longer merges -> CONFLICTED",
                        job.id[:10], report.base_ref, report.commits_behind,
                    )
            except Exception:
                logger.debug("drift watch: job %s assessment failed", job.id, exc_info=True)

    # ------------------------------------------------------------ retention

    def _maybe_sweep_retention(self) -> None:
        if not self.retention_days or float(self.retention_days) <= 0:
            return
        now = time.monotonic()
        if now - self._last_retention_sweep < self.retention_interval_seconds:
            return
        self._last_retention_sweep = now
        try:
            from .management import JobManagementService

            result = JobManagementService(self.service).sweep_retention(
                older_than_days=float(self.retention_days)
            )
            if result.get("deleted"):
                logger.info(
                    "Job retention sweep: purged %d archived job(s) older than %.1f days",
                    len(result["deleted"]), float(self.retention_days),
                )
        except Exception:
            logger.exception("job retention sweep failed")

    def _worker_id(self, job_id: str, phase: str) -> str:
        return f"{self.controller_id}:{phase}:{job_id[:10]}:{uuid.uuid4().hex[:6]}"

    def _spawn(self, job_id: str, phase: str) -> bool:
        worker_id = self._worker_id(job_id, phase)
        if not self.service.acquire(
            job_id,
            worker_id,
            ttl_seconds=self.lease_ttl_seconds,
        ):
            return False

        module = "mu.jobs.verify_worker" if phase == "verification" else "mu.jobs.worker"
        log_path = os.path.join(self.log_root, f"{job_id}.{phase}.log")
        # Round-41 F7a: bound the log before re-opening it in append mode —
        # retries append forever to the same phase log, and a noisy worker
        # could grow it without bound. Rotate (rename) once past the cap;
        # keep the previous log as .1 for diagnosis, drop any older one.
        try:
            if os.path.exists(log_path) and os.path.getsize(log_path) > self._LOG_MAX_BYTES:
                older = f"{log_path}.1"
                if os.path.exists(older):
                    os.unlink(older)
                os.replace(log_path, older)
        except OSError:
            pass  # rotation is best-effort; keep spawning either way
        log_handle: Optional[IO[bytes]] = None
        try:
            log_handle = open(log_path, "ab", buffering=0)
            command = [
                self.python_executable,
                "-m",
                module,
                "--job-id",
                job_id,
                "--worker-id",
                worker_id,
                "--lease-ttl",
                str(self.lease_ttl_seconds),
            ]
            process = self.process_factory(
                command,
                cwd=self.project_root,
                env=os.environ.copy(),
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except Exception as exc:
            if log_handle is not None:
                log_handle.close()
            self.service.release(job_id, worker_id, reason=f"{phase} worker spawn failed")
            self.service.store.append_event(
                job_id,
                "worker_spawn_failed",
                reason=str(exc),
                payload={"phase": phase, "log_path": log_path},
            )
            return False

        handle = WorkerHandle(
            job_id=job_id,
            phase=phase,
            worker_id=worker_id,
            process=process,
            log_path=log_path,
            log_handle=log_handle,
        )
        with self._lock:
            self._active[job_id] = handle
        self.service.store.append_event(
            job_id,
            "worker_process_started",
            payload={
                "pid": getattr(process, "pid", None),
                "phase": phase,
                "worker_id": worker_id,
                "log_path": log_path,
                "controller_id": self.controller_id,
            },
        )
        return True

    def _reap(self) -> None:
        finished: list[tuple[str, WorkerHandle, int]] = []
        with self._lock:
            for job_id, handle in list(self._active.items()):
                code = handle.process.poll()
                if code is not None:
                    finished.append((job_id, handle, int(code)))
                    self._active.pop(job_id, None)
        for job_id, handle, code in finished:
            if handle.log_handle is not None:
                try:
                    handle.log_handle.close()
                except Exception:
                    pass
            self.service.store.append_event(
                job_id,
                "worker_process_exited",
                reason=f"exit code {code}",
                payload={
                    "pid": getattr(handle.process, "pid", None),
                    "phase": handle.phase,
                    "worker_id": handle.worker_id,
                    "exit_code": code,
                    "log_path": handle.log_path,
                },
            )
            try:
                current = self.service.get(job_id)
                if current.status == JobStatus.CANCELLED:
                    self.service.release(
                        job_id,
                        handle.worker_id,
                        reason="cancelled worker process exited",
                    )
            except Exception:
                pass

    def _enforce_runtime_deadlines(self) -> None:
        """Round-36 F4 (round-38 F3/F4): enforce Job.max_runtime_seconds.
        The deadline covers the WHOLE job (all attempts, implementation +
        verification phases), measured from started_at. A job past its
        deadline is transitioned to TIMED_OUT ONCE and handed to the same
        SIGTERM→grace→SIGKILL group escalation cancellations use
        (_terminate_cancelled handles both CANCELLED and TIMED_OUT)."""
        now = time.time()
        with self._lock:
            handles = list(self._active.values())
        for handle in handles:
            try:
                job = self.service.get(handle.job_id)
            except KeyError:
                continue
            limit = job.max_runtime_seconds
            if not limit or job.started_at is None:
                continue
            if now - float(job.started_at) < float(limit):
                continue
            if job.status in {JobStatus.CANCELLED, JobStatus.MERGED}:
                continue
            if job.status == JobStatus.TIMED_OUT or handle.terminate_started is not None:
                # Already timed out and escalating — no repeat transition
                # (TIMED_OUT→TIMED_OUT would be a no-op write) and no
                # second SIGTERM; escalation continues in
                # _terminate_cancelled.
                continue
            self.service.store.append_event(
                handle.job_id,
                "worker_runtime_deadline_exceeded",
                reason=f"exceeded max_runtime_seconds={limit}",
                payload={
                    "pid": getattr(handle.process, "pid", None),
                    "phase": handle.phase,
                    "runtime_seconds": round(now - float(job.started_at), 1),
                },
            )
            try:
                self.service.transition(
                    handle.job_id,
                    JobStatus.TIMED_OUT,
                    reason=f"max_runtime_seconds={limit} exceeded",
                )
            except Exception as exc:
                self.service.store.append_event(
                    handle.job_id,
                    "worker_timeout_transition_failed",
                    reason=str(exc),
                    payload={"pid": getattr(handle.process, "pid", None)},
                )
                continue
            # Kick off the shared escalation: mark the start time, send the
            # first SIGTERM. _terminate_cancelled performs the SIGKILL
            # escalation after the grace window.
            handle.terminate_started = time.monotonic()
            try:
                handle.process.terminate()
                self.service.store.append_event(
                    handle.job_id,
                    "worker_process_terminated",
                    reason="runtime deadline exceeded",
                    payload={
                        "pid": getattr(handle.process, "pid", None),
                        "phase": handle.phase,
                    },
                )
            except Exception as exc:
                self.service.store.append_event(
                    handle.job_id,
                    "worker_termination_failed",
                    reason=str(exc),
                    payload={"pid": getattr(handle.process, "pid", None)},
                )

    def _terminate_cancelled(self) -> None:
        """Round-35 F6 (round-38 F4): SIGTERM first, SIGKILL the process
        GROUP after the grace window. Workers started with
        start_new_session() lead their own process group, so kill()
        targets the group (descendants die too). Handles both CANCELLED
        and TIMED_OUT jobs (runtime-deadline enforcement marks the job
        TIMED_OUT and starts the same escalation). A worker that ignores
        SIGTERM no longer holds its controller slot forever."""
        with self._lock:
            handles = list(self._active.values())
        for handle in handles:
            try:
                job = self.service.get(handle.job_id)
            except KeyError:
                continue
            if job.status not in {JobStatus.CANCELLED, JobStatus.TIMED_OUT}:
                continue
            if handle.process.poll() is not None:
                continue
            if handle.terminate_started is None:
                try:
                    handle.process.terminate()
                    handle.terminate_started = time.monotonic()
                    self.service.store.append_event(
                        handle.job_id,
                        "worker_process_terminated",
                        reason="job cancelled",
                        payload={
                            "pid": getattr(handle.process, "pid", None),
                            "phase": handle.phase,
                        },
                    )
                except Exception as exc:
                    self.service.store.append_event(
                        handle.job_id,
                        "worker_termination_failed",
                        reason=str(exc),
                        payload={
                            "pid": getattr(handle.process, "pid", None),
                            "phase": handle.phase,
                        },
                    )
                continue
            if time.monotonic() - handle.terminate_started >= self._TERMINATION_GRACE_S:
                try:
                    # process group kill: start_new_session made the worker
                    # its group leader, so pid == pgid.
                    os.killpg(os.getpgid(handle.process.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError, OSError):
                    try:
                        handle.process.kill()
                    except OSError:
                        pass  # already dead; _reap will collect it
                self.service.store.append_event(
                    handle.job_id,
                    "worker_process_killed",
                    reason=f"SIGKILL after {self._TERMINATION_GRACE_S}s grace",
                    payload={
                        "pid": getattr(handle.process, "pid", None),
                        "phase": handle.phase,
                    },
                )
