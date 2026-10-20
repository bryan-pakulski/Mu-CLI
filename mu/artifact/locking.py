"""One lock per registry file, shared by every registry object that opens it.

The GUI routers, the running session and a container worker (another process, same mounted
directory) each build their own ``ArtifactRegistry`` / ``AttachmentRegistry`` for a session. Their
read-modify-write of ``registry.json`` must not interleave, or one writer drops the other's entry.
``path_lock`` returns a re-entrant lock shared in this process for the path, which also holds an
advisory ``flock`` on the lock file while held (outermost acquire only), so another process
waits too.
"""
from __future__ import annotations

import os
import threading

try:  # POSIX; elsewhere the in-process lock still serialises this process
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]


class _PathLock:
    def __init__(self, path: str):
        self.path = path
        self._rlock = threading.RLock()
        self._depth = 0
        self._fd: int | None = None

    def __enter__(self) -> "_PathLock":
        self._rlock.acquire()
        if self._depth == 0 and fcntl is not None:
            try:
                fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
                fcntl.flock(fd, fcntl.LOCK_EX)
                self._fd = fd
            except OSError:
                self._fd = None
        self._depth += 1
        return self

    def __exit__(self, *_exc) -> None:
        self._depth -= 1
        if self._depth == 0 and self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None
        self._rlock.release()


_LOCKS: dict[str, _PathLock] = {}
_LOCKS_GUARD = threading.Lock()


def path_lock(path: str) -> _PathLock:
    """The shared lock for ``path`` (normalised); the lock file is created on first acquire."""
    key = os.path.abspath(path)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = _LOCKS[key] = _PathLock(key)
        return lock
