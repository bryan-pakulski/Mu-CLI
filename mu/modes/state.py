"""Session-owned mode state (modes v2 §3.1).

Each mode that needs records keeps one typed ``ModeState`` on its session
(``SessionManager.mode_state``), persisted under the session key
``mode_state: {name: dict}``. Tool dispatch binds the session's states for the
call (``use_mode_states``) so engines resolve THIS session's records and never
reach for ad-hoc session attributes or process globals.

A state class registers itself with ``@register_mode_state``. ``ModeStates``
creates states lazily on first access and loads them from the raw snapshot
then, so a session file written by a newer build (an unknown state name) keeps
its data untouched, and a state whose module was never imported still round
trips.

Engines that keep large records on disk (feature plans, courses, scans) store
only a pointer + revision in their state and read the file through
``load_cached`` (mtime-keyed).
"""

from __future__ import annotations

import contextlib
import contextvars
import copy
import importlib
import os
import threading
import time
from typing import Any, Callable, ClassVar, Dict, Iterator, Optional, Type, TypeVar

MODE_STATE_TYPES: Dict[str, Type["ModeState"]] = {}

# Modules that define a state, imported on first access to that name.
_STATE_MODULES: Dict[str, str] = {
    "checks": "mu.modes.evidence",
    "security": "mu.security.state",
    "work": "mu.modes.work",
    "blockers": "mu.modes.blockers",
    "debug": "mu.debug.state",
    "loop": "mu.loop.state",
    "edits": "mu.modes.edits",
    "receipts": "mu.modes.receipt",
}

S = TypeVar("S", bound="ModeState")


def now() -> float:
    return round(time.time(), 3)


class ModeState:
    """Base class: a lock, a dirty flag and dict round-tripping."""

    name: ClassVar[str] = ""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self._owner: Optional["ModeStates"] = None

    # Subclasses implement these two; load_dict must tolerate missing and
    # unknown keys (straight load, no migrations).
    def to_dict(self) -> Dict[str, Any]:  # pragma: no cover - abstract
        raise NotImplementedError

    def load_dict(self, data: Optional[Dict[str, Any]]) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def clear(self) -> None:
        self.load_dict(None)
        self.touch()

    def touch(self) -> None:
        """Mark the owning session dirty so dispatch saves it after the call."""
        if self._owner is not None:
            self._owner.dirty = True


def register_mode_state(cls: Type[S]) -> Type[S]:
    if not cls.name:
        raise ValueError(f"{cls.__name__} has no state name")
    MODE_STATE_TYPES[cls.name] = cls
    return cls


def _state_type(name: str) -> Optional[Type[ModeState]]:
    cls = MODE_STATE_TYPES.get(name)
    if cls is None and name in _STATE_MODULES:
        importlib.import_module(_STATE_MODULES[name])
        cls = MODE_STATE_TYPES.get(name)
    return cls


class ModeStates:
    """All mode states of one session."""

    def __init__(self) -> None:
        self._states: Dict[str, ModeState] = {}
        self._raw: Dict[str, Any] = {}
        self._lock = threading.RLock()
        self.dirty = False

    def get(self, name: str, cls: Optional[Type[S]] = None) -> S:
        with self._lock:
            state = self._states.get(name)
            if state is not None:
                return state  # type: ignore[return-value]
            state_cls = cls or _state_type(name)
            if state_cls is None:
                raise KeyError(f"unknown mode state: {name!r}")
            state = state_cls()
            state._owner = self
            raw = self._raw.pop(name, None)
            try:
                state.load_dict(raw if isinstance(raw, dict) else None)
            except Exception:  # noqa: BLE001 — a corrupt record loads empty
                state.load_dict(None)
            self._states[name] = state
            return state  # type: ignore[return-value]

    def peek(self, name: str) -> Optional[ModeState]:
        """The state if it exists (created or persisted), without creating it."""
        with self._lock:
            if name in self._states:
                return self._states[name]
            if name in self._raw:
                return self.get(name)
            return None

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            out = copy.deepcopy(self._raw)
            for name, state in self._states.items():
                with state.lock:
                    out[name] = state.to_dict()
            return out

    def load_dict(self, data: Any) -> None:
        """Replace every state from a snapshot. States keep their identity, so a
        binding taken by an in-flight tool call stays valid."""
        with self._lock:
            self._raw = {
                str(k): v for k, v in (data or {}).items() if isinstance(v, dict)
            } if isinstance(data, dict) else {}
            for name, state in self._states.items():
                raw = self._raw.pop(name, None)
                with state.lock:
                    try:
                        state.load_dict(raw if isinstance(raw, dict) else None)
                    except Exception:  # noqa: BLE001
                        state.load_dict(None)
            self.dirty = False

    def clear(self) -> None:
        self.load_dict(None)

    def consume_dirty(self) -> bool:
        with self._lock:
            was, self.dirty = self.dirty, False
            return was


# ------------------------------------------------------------------ binding

_DEFAULT = ModeStates()
_CURRENT: contextvars.ContextVar[Optional[ModeStates]] = contextvars.ContextVar(
    "mu_mode_states", default=None
)


@contextlib.contextmanager
def use_mode_states(states: ModeStates) -> Iterator[ModeStates]:
    token = _CURRENT.set(states)
    try:
        yield states
    finally:
        _CURRENT.reset(token)


def current_mode_states() -> ModeStates:
    return _CURRENT.get() or _DEFAULT


def states_of(owner: Any) -> ModeStates:
    """Resolve the ``ModeStates`` of a session, a session manager, a tool
    context, or the bound/default states when ``owner`` has none."""
    for candidate in (
        owner,
        getattr(owner, "session_manager", None),
        getattr(getattr(owner, "session", None), "session_manager", None),
    ):
        states = getattr(candidate, "mode_state", None)
        if isinstance(states, ModeStates):
            return states
    return current_mode_states()


def state_for(owner: Any, name: str) -> Any:
    return states_of(owner).get(name)


# ------------------------------------------------------------------ cached loader

_FILE_CACHE: Dict[str, tuple] = {}
_FILE_CACHE_LOCK = threading.Lock()


def load_cached(path: str, loader: Callable[[str], Any]) -> Any:
    """Load ``path`` with ``loader``, reusing the parsed object while the
    file's mtime and size are unchanged. Callers must treat the result as
    read-only or copy it before mutating."""
    real = os.path.realpath(path)
    stat = os.stat(real)
    key = (stat.st_mtime_ns, stat.st_size, id(loader))
    with _FILE_CACHE_LOCK:
        hit = _FILE_CACHE.get(real)
        if hit is not None and hit[0] == key:
            return hit[1]
    value = loader(real)
    with _FILE_CACHE_LOCK:
        _FILE_CACHE[real] = (key, value)
    return value


def invalidate_cached(path: str) -> None:
    with _FILE_CACHE_LOCK:
        _FILE_CACHE.pop(os.path.realpath(path), None)


__all__ = [
    "MODE_STATE_TYPES",
    "ModeState",
    "ModeStates",
    "current_mode_states",
    "invalidate_cached",
    "load_cached",
    "now",
    "register_mode_state",
    "state_for",
    "states_of",
    "use_mode_states",
]
