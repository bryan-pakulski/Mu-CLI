"""Background task primitives — fire-and-poll bash commands.

The agent needs to be able to kick off long-running commands (test
suites, build jobs, watchers, dev servers) without blocking the
synchronous tool loop. The `BackgroundTaskRegistry` is a per-session
registry of `BackgroundTask` records; each task owns a `Popen` plus a
rolling stdout / stderr buffer drained by daemon threads.

Tools exposed to the model:

  * `bash_background`  — start a command, return its `task_id`.
  * `bash_status`      — poll status + tail of stdout/stderr.
  * `bash_logs`        — return the last N lines of stdout/stderr.
  * `bash_kill`        — SIGTERM (escalating to SIGKILL after a grace).
  * `bash_list`        — summary of every task in the registry.

Buffers are bounded (rolling deques) so a chatty watcher won't blow
memory. On session shutdown the harness calls
`BackgroundTaskRegistry.shutdown()` which SIGTERMs everything still
alive — no orphans.

This module is intentionally dependency-free: no asyncio, no shells
spawned by the registry itself (the caller passes the command string,
which is run through `/bin/bash -c` like the synchronous tool).
"""

from __future__ import annotations

import collections
import os
import shlex
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional


@dataclass
class BackgroundTask:
    task_id: str
    name: str
    command: str
    cwd: str
    started_at: float
    process: subprocess.Popen
    stdout_buf: Deque[str] = field(default_factory=lambda: collections.deque(maxlen=2000))
    stderr_buf: Deque[str] = field(default_factory=lambda: collections.deque(maxlen=2000))
    exit_code: Optional[int] = None
    ended_at: Optional[float] = None
    # Set by the harness when the user kills the task explicitly.
    killed: bool = False

    def status(self) -> str:
        if self.process.poll() is None:
            return "running"
        if self.killed:
            return "killed"
        if (self.exit_code or 0) == 0:
            return "completed"
        return "failed"

    def runtime(self) -> float:
        end = self.ended_at if self.ended_at is not None else time.time()
        return max(0.0, end - self.started_at)


class BackgroundTaskRegistry:
    """Owns the lifecycle of all background tasks for a single session."""

    def __init__(self) -> None:
        self._tasks: Dict[str, BackgroundTask] = {}
        self._lock = threading.Lock()
        self._closed = False

    # ----------------------------------------------------------- ops

    def start(
        self,
        command: str,
        *,
        name: Optional[str] = None,
        cwd: Optional[str] = None,
    ) -> BackgroundTask:
        if self._closed:
            raise RuntimeError("BackgroundTaskRegistry is closed")
        if not command or not command.strip():
            raise ValueError("command must be a non-empty string")
        resolved_cwd = cwd or os.getcwd()
        if not os.path.isdir(resolved_cwd):
            raise ValueError(f"cwd does not exist: {resolved_cwd}")

        proc = subprocess.Popen(
            ["/bin/bash", "-lc", command],
            cwd=resolved_cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
            text=True,
            # New session so we can SIGTERM the whole tree on kill.
            start_new_session=True,
        )

        task = BackgroundTask(
            task_id=f"bg-{uuid.uuid4().hex[:10]}",
            name=(name or _derive_name(command)),
            command=command,
            cwd=resolved_cwd,
            started_at=time.time(),
            process=proc,
        )
        with self._lock:
            self._tasks[task.task_id] = task

        threading.Thread(
            target=self._pump_stream,
            args=(task, proc.stdout, task.stdout_buf),
            daemon=True,
        ).start()
        threading.Thread(
            target=self._pump_stream,
            args=(task, proc.stderr, task.stderr_buf),
            daemon=True,
        ).start()
        threading.Thread(
            target=self._reap,
            args=(task,),
            daemon=True,
        ).start()
        return task

    def get(self, task_id: str) -> Optional[BackgroundTask]:
        with self._lock:
            return self._tasks.get(task_id)

    def list(self) -> List[BackgroundTask]:
        with self._lock:
            return list(self._tasks.values())

    def kill(self, task_id: str, *, grace_seconds: float = 3.0) -> Optional[BackgroundTask]:
        task = self.get(task_id)
        if task is None:
            return None
        if task.process.poll() is not None:
            return task  # already done
        task.killed = True
        try:
            os.killpg(task.process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            try:
                task.process.terminate()
            except Exception:
                pass
        deadline = time.time() + grace_seconds
        while time.time() < deadline:
            if task.process.poll() is not None:
                break
            time.sleep(0.05)
        if task.process.poll() is None:
            try:
                os.killpg(task.process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                try:
                    task.process.kill()
                except Exception:
                    pass
        return task

    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        for task in self.list():
            if task.process.poll() is None:
                self.kill(task.task_id, grace_seconds=1.0)

    # -------------------------------------------------------- internals

    @staticmethod
    def _pump_stream(task: BackgroundTask, stream, buf: Deque[str]) -> None:
        if stream is None:
            return
        try:
            for line in iter(stream.readline, ""):
                buf.append(line.rstrip("\n"))
        except (ValueError, OSError):
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    @staticmethod
    def _reap(task: BackgroundTask) -> None:
        try:
            task.exit_code = task.process.wait()
        except Exception:
            task.exit_code = -1
        task.ended_at = time.time()


def _derive_name(command: str) -> str:
    """A short label for the UI — first token of the command."""
    try:
        parts = shlex.split(command)
        if parts:
            return os.path.basename(parts[0])[:32]
    except ValueError:
        pass
    return command.strip().split()[0][:32] if command.strip() else "bash"


def tail(buf: Deque[str], lines: int) -> List[str]:
    lines = max(0, min(lines, len(buf)))
    if lines == 0:
        return []
    return list(buf)[-lines:]


def summarize_task(task: BackgroundTask, *, tail_lines: int = 20) -> dict:
    return {
        "task_id": task.task_id,
        "name": task.name,
        "command": task.command,
        "cwd": task.cwd,
        "status": task.status(),
        "exit_code": task.exit_code,
        "runtime_sec": round(task.runtime(), 2),
        "started_at": task.started_at,
        "ended_at": task.ended_at,
        "stdout_tail": tail(task.stdout_buf, tail_lines),
        "stderr_tail": tail(task.stderr_buf, tail_lines),
        "killed": task.killed,
    }


__all__ = [
    "BackgroundTask",
    "BackgroundTaskRegistry",
    "summarize_task",
    "tail",
]
