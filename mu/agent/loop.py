"""Public AgentLoop wrapper.

Typed entry point for running a single agentic turn. `run_turn(text)`
calls `Session.send_message(text)` (whose body lives in
`mu/agent/loop_body.py:run_turn`) and wraps the raw dict result in a
`TurnResult`. `stop(reason)` fires the `on_stop` hook and asks the
session to halt its loop.

The constructor takes a `Session` because the per-turn state lives
there (history, folder context, memory stores, feature plan, UI).
Hooks (`pre_provider_call` / `post_provider_call` / `pre_tool` /
`post_tool`) fire from inside the loop body and the provider retry
wrapper — this façade just adds typed wrapping + the on_stop hook
point.
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
    """Typed wrapper around `Session.send_message`.

    Use:

        loop = AgentLoop(session)
        result = loop.run_turn("explain this file")

    Hooks (`pre_provider_call`, `post_provider_call`, `pre_tool`,
    `post_tool`, `on_stop`) fire from the loop body and provider retry
    wrapper; this class adds typed wrapping plus the on_stop hook point
    via `stop()`.
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
