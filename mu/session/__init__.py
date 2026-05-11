"""Session-management primitives for the new harness.

Modules:
  * history  — `HistoryMixin` with token-budget rolling, summarization,
               and payload degradation. Extracted from `SessionManager`
               so it can be tested and reasoned about in isolation.
  * manager  — `SessionManager` (currently re-exported from
               `core/session.py`; physical move follows the step-7
               cutover).

`SessionManager` is exposed via a lazy attribute so this module can be
imported by `core/session.py` itself without causing a partial-import
cycle. Use `from mu.session.manager import SessionManager` or
`from mu.session import SessionManager`.
"""

from .history import HistoryMixin


def __getattr__(name: str):
    if name == "SessionManager":
        from .manager import SessionManager  # local import breaks cycle
        return SessionManager
    raise AttributeError(f"module 'mu.session' has no attribute {name!r}")


__all__ = ["HistoryMixin", "SessionManager"]
