"""Background scheduler/controller for durable engineering-job subprocesses."""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Callable, Dict, Optional

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


class JobController:
    """Lease jobs and launch isolated implementation/verification processes."""

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
    ):
        self.service = service
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
        """Stop scheduling without killing active child workers."""
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=None if wait else 2.0)
        if wait:
            with self._lock:
                handles = list(self._active.values())
            for handle in handles:
                try:
                    handle.process.wait()
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
        self.service.recover_expired_leases()
        with self._lock:
            capacity = self.max_workers - len(self._active)
        if capacity <= 0:
            return 0

        candidates = self.service.list(
            statuses=[JobStatus.QUEUED, JobStatus.RECOVERING, JobStatus.VERIFYING],
            limit=max(capacity * 5, 25),
        )
        started = 0
        for job in reversed(candidates):
            if started >= capacity:
                break
            with self._lock:
                if job.id in self._active:
                    continue
            phase = self._phase_for_status(job.status)
            if self._spawn(job.id, phase):
                started += 1
        return started

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

    def _terminate_cancelled(self) -> None:
        with self._lock:
            handles = list(self._active.values())
        for handle in handles:
            try:
                job = self.service.get(handle.job_id)
            except KeyError:
                continue
            if job.status != JobStatus.CANCELLED or handle.process.poll() is not None:
                continue
            try:
                handle.process.terminate()
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
