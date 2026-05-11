"""Agent-loop infrastructure for the new harness.

Modules:
  * hooks      — lifecycle hook registry (pre/post tool, pre/post provider, on_stop)
  * parallel   — asyncio-based parallel tool dispatch
  * compactor  — auto-compaction trigger for history → token budget
  * plan_mode  — read-only enforcement for tool dispatch
  * loop       — the new AgentLoop (deferred: existing Session is the loop today)

The pieces are intentionally usable independently of any AgentLoop, so they
can be wired into the legacy `core/session.py` loop incrementally before
the full cutover.
"""

from .hooks import HookRegistry, HookSpec, default_registry
from .loop import AgentLoop, TurnResult

__all__ = ["AgentLoop", "HookRegistry", "HookSpec", "TurnResult", "default_registry"]
