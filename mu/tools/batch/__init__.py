"""Dispatcher-adjacent tools: `batch_job` (composite) and `flush`
(collation-buffer drain).

These were deferred during Phase 1.7 because both are tightly coupled
to the dispatcher and the session loop. After Phase 2 made
`mu/tools/_dispatcher.py:dispatch` the canonical entry, batch_job can
call it cleanly; flush remains a no-op tool whose real work happens
in the session's collation buffer.
"""

from . import handlers  # noqa: F401 — registers batch_job + flush at import time

__all__: list = []
