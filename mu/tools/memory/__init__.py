"""Session memory + scratchpad tools.

Seven `@tool`-registered handlers that operate on the live session's
stores:

  * `save_memory`        — durable task-scoped fact
  * `search_memory`      — query over the task memory
  * `list_memory`        — list recent task-memory entries
  * `save_scratchpad`    — short-lived per-turn note
  * `search_scratchpad`  — query over the turn scratchpad
  * `list_scratchpad`    — list recent scratchpad entries
  * `clear_scratchpad`   — wipe the turn scratchpad

Stores live on `context.session` as `task_memory` and `turn_scratchpad`
(see `core/memory.py:TaskMemoryStore` and `ScratchpadStore`). For a
session-less context (e.g. standalone unit tests) we fall back to a
process-local store so the tools still exercise their handler logic.
"""

from . import handlers  # noqa: F401 — registers the 7 tools at import time

__all__: list = []
