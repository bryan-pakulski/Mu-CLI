"""Re-export of `SessionManager` via a deferred import.

The class body still lives in `core/session.py` while the legacy loop
exists. The physical move to this module will happen as part of the
step-7 cutover, at which point `core/session.py` itself is removed.

We use a lazy import inside `__getattr__` so this module can be referenced
from `core/session.py` (which provides `HistoryMixin` via `mu.session.history`)
without a circular-import partial-load problem.
"""


def __getattr__(name: str):
    if name == "SessionManager":
        from core.session import SessionManager
        return SessionManager
    raise AttributeError(f"module 'mu.session.manager' has no attribute {name!r}")


__all__ = ["SessionManager"]
