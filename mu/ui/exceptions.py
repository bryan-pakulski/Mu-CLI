"""Control-plane-neutral UI interruption signals."""

from __future__ import annotations

from typing import Any, Dict


class InteractionRequired(BaseException):
    """Non-error control flow for execution that requires a human decision.

    This intentionally derives directly from ``BaseException``. The existing
    agent loop has broad ``except Exception`` provider/tool recovery; a durable
    approval/question must bypass that recovery entirely so an outer job runner
    can persist NEEDS_HUMAN rather than translating the gate into an API error
    or retry. Controllers must catch this signal explicitly.
    """

    def __init__(self, kind: str, detail: str, *, payload: Dict[str, Any] | None = None):
        super().__init__(detail)
        self.kind = str(kind or "question")
        self.detail = str(detail or "Human input is required.")
        self.payload = dict(payload or {})
