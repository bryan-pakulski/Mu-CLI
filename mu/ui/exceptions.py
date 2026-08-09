"""Control-plane-neutral UI interruption signals."""

from __future__ import annotations

from typing import Any, Dict


class InteractionRequired(RuntimeError):
    """Raised by non-interactive UIs when execution needs a human decision.

    Agent execution must not translate this into an ordinary provider/tool
    failure: durable controllers catch it and move the owning job into a
    structured NEEDS_HUMAN state.
    """

    def __init__(self, kind: str, detail: str, *, payload: Dict[str, Any] | None = None):
        super().__init__(detail)
        self.kind = str(kind or "question")
        self.detail = str(detail or "Human input is required.")
        self.payload = dict(payload or {})
