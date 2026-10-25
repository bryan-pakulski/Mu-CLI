"""Actors and events for mode state transitions (modes v2 §3.5).

Every transition names who made it: ``agent`` (a model tool call),
``user:<surface>`` (``user:gui``, ``user:mobile``, ``user:cli``, ``user:chat``)
or ``system`` (the harness itself). A user override — completing something
without the evidence the gate wants — is only accepted from a user actor and
is recorded as an ``override`` event with its reason.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .state import now

AGENT = "agent"
SYSTEM = "system"
USER_SURFACES = ("gui", "mobile", "cli", "chat")


class GateError(Exception):
    """A gate refused a transition. ``code`` is a stable machine word."""

    def __init__(self, message: str, code: str = "gate_refused", **data: Any) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


def normalize_actor(actor: Any) -> str:
    text = str(actor or AGENT).strip().lower()
    if text in (AGENT, SYSTEM):
        return text
    if text == "user":
        return "user:gui"
    if text.startswith("user:") and text.split(":", 1)[1] in USER_SURFACES:
        return text
    raise ValueError(f"unknown actor: {actor!r}")


def is_user(actor: str) -> bool:
    return str(actor or "").startswith("user:")


def require_user(actor: str, what: str) -> None:
    if not is_user(actor):
        raise GateError(f"{what} is the user's decision; the agent cannot make it.",
                        code="user_only")


@dataclass
class Event:
    kind: str
    actor: str
    ref: str = ""
    detail: str = ""
    at: float = field(default_factory=now)
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Event":
        return cls(
            kind=str(raw.get("kind") or ""),
            actor=str(raw.get("actor") or AGENT),
            ref=str(raw.get("ref") or ""),
            detail=str(raw.get("detail") or ""),
            at=float(raw.get("at") or 0.0),
            data=dict(raw.get("data") or {}),
        )


def load_events(raw: Any, limit: Optional[int] = None) -> List[Event]:
    events = [Event.from_dict(e) for e in (raw or []) if isinstance(e, dict)]
    return events[-limit:] if limit else events


__all__ = [
    "AGENT",
    "SYSTEM",
    "Event",
    "GateError",
    "is_user",
    "load_events",
    "normalize_actor",
    "require_user",
]
