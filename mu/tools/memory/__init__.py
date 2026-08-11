"""Session, durable-memory and scratchpad tools.

`@tool`-registered handlers operate on the live session's working stores
and its shared scoped Memory Ledger:

  * `save_memory`        — durable task-scoped fact
  * `search_memory`      — query over the task memory
  * `list_memory`        — list recent task-memory entries
  * `manage_durable_memory` — curate a durable UUID without approval prompts
  * `save_scratchpad`    — short-lived per-turn note
  * `search_scratchpad`  — query over the turn scratchpad
  * `list_scratchpad`    — list recent scratchpad entries
  * `clear_scratchpad`   — wipe the turn scratchpad

Stores live on `context.session` as `task_memory` and `turn_scratchpad`
(see `mu/memory/stores.py:TaskMemoryStore` and `ScratchpadStore`). For a
session-less context (e.g. standalone unit tests) we fall back to a
process-local store so the tools still exercise their handler logic.
"""

from . import handlers  # noqa: F401 — registers the tools at import time

__all__: list = []
