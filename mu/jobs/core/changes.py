"""Typed change stream (guide §3.4).

``ChangeBus.emit(scope, id, parts)`` records that some parts of a view changed.
Emissions are coalesced over ``window_s`` (150 ms default) per ``(scope, id)``
and their ``parts`` unioned, so a burst of writes produces ONE event telling
the client exactly which parts to refetch — never a full reload.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

SCOPES = ("home", "job", "board")
PARTS = ("state", "pipeline", "plan", "diff", "review", "checks", "session", "costs")


@dataclass
class Change:
    scope: str
    id: str
    parts: List[str]
    seq: int = 0

    def to_dict(self) -> Dict[str, object]:
        return {"scope": self.scope, "id": self.id, "parts": list(self.parts), "seq": self.seq}


@dataclass
class _Pending:
    parts: Set[str] = field(default_factory=set)
    first_at: float = 0.0


class ChangeBus:
    def __init__(self, *, window_s: float = 0.150, clock: Callable[[], float] = time.monotonic,
                 history: int = 500):
        self.window_s = float(window_s)
        self._clock = clock
        self._lock = threading.Condition()
        self._pending: Dict[Tuple[str, str], _Pending] = {}
        self._log: List[Change] = []
        self._history = int(history)
        self._seq = 0

    # ------------------------------------------------------------ produce
    def emit(self, scope: str, id: str = "", parts: Iterable[str] = ("state",)) -> None:
        if scope not in SCOPES:
            raise ValueError(f"unknown change scope {scope!r}")
        clean = {p for p in parts if p in PARTS}
        if not clean:
            return
        with self._lock:
            key = (scope, str(id or ""))
            pending = self._pending.get(key)
            if pending is None:
                pending = self._pending[key] = _Pending(first_at=self._clock())
            pending.parts |= clean
            self._lock.notify_all()

    def emit_job(self, job_id: str, parts: Iterable[str]) -> None:
        """Job-level change also dirties the dashboard row."""
        parts = list(parts)
        self.emit("job", job_id, parts)
        home_parts = [p for p in parts if p in ("state", "pipeline", "review", "costs")]
        if home_parts:
            self.emit("home", "", home_parts)

    # ------------------------------------------------------------ flush
    def flush(self, *, force: bool = False) -> List[Change]:
        """Publish pending changes whose window elapsed (all when ``force``)."""
        now = self._clock()
        out: List[Change] = []
        with self._lock:
            for key in list(self._pending):
                pending = self._pending[key]
                if force or now - pending.first_at >= self.window_s:
                    del self._pending[key]
                    self._seq += 1
                    change = Change(key[0], key[1], [p for p in PARTS if p in pending.parts], self._seq)
                    out.append(change)
                    self._log.append(change)
            if len(self._log) > self._history:
                del self._log[: len(self._log) - self._history]
            if out:
                self._lock.notify_all()
        return out

    def since(self, seq: int) -> List[Change]:
        with self._lock:
            return [c for c in self._log if c.seq > seq]

    @property
    def seq(self) -> int:
        return self._seq

    def wait(self, after_seq: int, timeout: float) -> List[Change]:
        """Block until a change with seq > after_seq is published (or timeout)."""
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            self.flush()
            ready = self.since(after_seq)
            if ready:
                return ready
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return []
            with self._lock:
                self._lock.wait(min(remaining, self.window_s))


_DEFAULT: Optional[ChangeBus] = None
_DEFAULT_LOCK = threading.Lock()


def default_bus() -> ChangeBus:
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = ChangeBus()
        return _DEFAULT
