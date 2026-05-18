"""Public AgentLoop façade.

This is the canonical entry point for running an agentic turn. Today it
delegates to the legacy `mu.session.session.Session.send_message`, which still
houses the production loop body (loop detection, approval batching,
collation buffer, hierarchical context layers, loop-mode watchdog —
~700 LOC of dense interdependent state). The façade exists so that:

  * New callers depend on `mu.agent.AgentLoop` rather than reaching into
    `mu.session.session` directly.
  * Hooks fire from the same module they live in (pre/post tool, pre/post
    provider) — already wired into `Session._execute_tool_with_memory`
    and `Session._provider_generate_with_retry`.
  * Future replacement of the loop body lands here, in a single class,
    rather than as a sweeping refactor across `core/session.py`.

The constructor accepts a Session because the legacy class owns the
full state (history, folder context, memory stores, feature plan, UI).
Once the body migrates, those attributes will move with it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from .hooks import HookContext, default_registry


@dataclass
class TurnResult:
    """Structured result of a single agent turn.

    Mirrors the dict produced by `Session._collect_turn_response()` so
    callers don't need to deal with two shapes.
    """

    ok: bool
    status: str
    message: str = ""
    data: Dict[str, Any] = None
    raw: Dict[str, Any] = None

    def __post_init__(self) -> None:
        if self.data is None:
            self.data = {}
        if self.raw is None:
            self.raw = {}


class AgentLoop:
    """Public façade around the agent's per-turn execution.

    Use:

        loop = AgentLoop(session)
        result = loop.run_turn("explain this file")

    Hooks (`pre_provider_call`, `post_provider_call`, `pre_tool`,
    `post_tool`, `on_stop`) fire from the legacy `Session` methods today.
    They will continue firing identically once the loop body moves here.
    """

    def __init__(self, session: Any, *, registry=None) -> None:
        self.session = session
        self.registry = registry or default_registry

    # ----------------------------------------------------------------- run

    def run_turn(self, user_message: str) -> TurnResult:
        """Execute one user turn end-to-end and return a `TurnResult`."""

        raw = self.session.send_message(user_message)
        return self._wrap(raw)

    # ----------------------------------------------------------------- stop

    def stop(self, reason: str = "user_stop") -> None:
        """Fire the on_stop hooks and request the underlying session stop."""
        ctx = HookContext(
            point="on_stop",
            session=self.session,
            variables=getattr(self.session, "variables", {}) or {},
            stop_reason=reason,
        )
        self.registry.fire("on_stop", ctx)
        if hasattr(self.session, "stop_loop"):
            self.session.stop_loop()

    # ------------------------------------------------------------- internals

    @staticmethod
    def _wrap(raw: Any) -> TurnResult:
        if isinstance(raw, dict):
            return TurnResult(
                ok=bool(raw.get("ok", True)),
                status=str(raw.get("status", "completed")),
                message=str(raw.get("message", "") or ""),
                data=raw,
                raw=raw,
            )
        return TurnResult(ok=True, status="completed", data={}, raw={"value": raw})


__all__ = ["AgentLoop", "TurnResult"]
