"""Session blockers (modes v2 §3.4).

``raise_blocker`` is a core tool in every mode. It records a ``Blocker`` in
session state and ends the turn's autonomous continuation (the loop watchdog
and loop auto-continue stand down). A blocker is resolved by the user: their
next chat message, the panel's Resolve action, or ``/blocker resolve``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .events import Event, is_user, load_events, normalize_actor
from .state import ModeState, now, register_mode_state, state_for


@dataclass
class Blocker:
    id: str
    summary: str
    needs: str
    mode: str = ""
    ref: str = ""
    details: str = ""
    questions: List[str] = field(default_factory=list)
    raised_at: float = field(default_factory=now)
    raised_by: str = "agent"
    resolved_at: Optional[float] = None
    resolution: str = ""
    resolved_by: str = ""

    @property
    def open(self) -> bool:
        return self.resolved_at is None

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["open"] = self.open
        return out

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Blocker":
        return cls(
            id=str(raw.get("id") or ""), summary=str(raw.get("summary") or ""),
            needs=str(raw.get("needs") or ""), mode=str(raw.get("mode") or ""),
            ref=str(raw.get("ref") or ""), details=str(raw.get("details") or ""),
            questions=[str(q) for q in raw.get("questions") or []],
            raised_at=float(raw.get("raised_at") or 0.0),
            raised_by=str(raw.get("raised_by") or "agent"),
            resolved_at=raw.get("resolved_at"), resolution=str(raw.get("resolution") or ""),
            resolved_by=str(raw.get("resolved_by") or ""),
        )


# Engines that react to blockers (the loop mission pauses on one) register here.
# Each listener gets an owner object exposing ``mode_state`` and the event kind
# "raised" | "all_resolved".
_LISTENERS: List[Callable[[Any, str], None]] = []


def on_blocker_event(listener: Callable[[Any, str], None]) -> Callable[[Any, str], None]:
    if listener not in _LISTENERS:
        _LISTENERS.append(listener)
    return listener


class _Owner:
    def __init__(self, states: Any) -> None:
        self.mode_state = states


def _notify(state: "BlockerState", kind: str) -> None:
    if state._owner is None:
        return
    for listener in list(_LISTENERS):
        try:
            listener(_Owner(state._owner), kind)
        except Exception:  # noqa: BLE001 — a listener must not break the blocker
            pass


@register_mode_state
class BlockerState(ModeState):
    name = "blockers"

    def __init__(self) -> None:
        super().__init__()
        self.blockers: Dict[str, Blocker] = {}
        self.counter = 0
        self.events: List[Event] = []

    def to_dict(self) -> Dict[str, Any]:
        return {"counter": self.counter, "blockers": [b.to_dict() for b in self.blockers.values()],
                "events": [e.to_dict() for e in self.events[-100:]]}

    def load_dict(self, data: Optional[Dict[str, Any]]) -> None:
        data = data or {}
        self.blockers = {}
        for raw in data.get("blockers") or []:
            if isinstance(raw, dict) and raw.get("id"):
                blocker = Blocker.from_dict(raw)
                self.blockers[blocker.id] = blocker
        # Keep the history bounded: resolved blockers beyond the last 50 go.
        resolved = [b for b in self.blockers.values() if not b.open]
        for old in resolved[:-50]:
            self.blockers.pop(old.id, None)
        self.counter = int(data.get("counter") or len(self.blockers))
        self.events = load_events(data.get("events"), 100)

    def open_blockers(self, *, mode: Optional[str] = None, ref: Optional[str] = None) -> List[Blocker]:
        out = [b for b in self.blockers.values() if b.open]
        if mode:
            out = [b for b in out if b.mode == mode]
        if ref is not None:
            out = [b for b in out if b.ref == ref]
        return out

    def get(self, blocker_id: str) -> Optional[Blocker]:
        return self.blockers.get(str(blocker_id or "").strip())

    def raise_blocker(self, summary: str, needs: str, *, mode: str = "", ref: str = "",
                      details: str = "", questions: Optional[List[str]] = None,
                      actor: str = "agent") -> Blocker:
        summary = str(summary or "").strip()
        if not summary:
            raise ValueError("A blocker needs a summary.")
        actor = normalize_actor(actor)
        with self.lock:
            # The same open blocker raised again is one blocker, not two.
            for existing in self.open_blockers(mode=mode or None, ref=ref):
                if existing.summary == summary:
                    existing.needs = str(needs or existing.needs)
                    existing.details = details or existing.details
                    self.touch()
                    return existing
            self.counter += 1
            blocker = Blocker(id=f"b{self.counter}", summary=summary, needs=str(needs or "").strip(),
                              mode=mode, ref=str(ref or ""), details=str(details or "").strip(),
                              questions=[str(q).strip() for q in questions or [] if str(q).strip()],
                              raised_by=actor)
            self.blockers[blocker.id] = blocker
            self.events.append(Event(kind="raised", actor=actor, ref=blocker.id, detail=summary))
            self.touch()
        _notify(self, "raised")
        return blocker

    def resolve(self, blocker_id: str, resolution: str = "", *, actor: str = "user:gui") -> Blocker:
        actor = normalize_actor(actor)
        with self.lock:
            blocker = self.get(blocker_id)
            if blocker is None:
                raise KeyError(f"No blocker {blocker_id!r}.")
            if not blocker.open:
                return blocker
            if not is_user(actor) and actor != "system":
                raise PermissionError("Only the user resolves a blocker.")
            blocker.resolved_at = now()
            blocker.resolution = str(resolution or "").strip()[:2000]
            blocker.resolved_by = actor
            self.events.append(Event(kind="resolved", actor=actor, ref=blocker.id,
                                     detail=blocker.resolution[:200]))
            self.touch()
            remaining = bool(self.open_blockers())
        if not remaining:
            _notify(self, "all_resolved")
        return blocker

    def resolve_all(self, resolution: str = "", *, actor: str = "user:chat",
                    mode: Optional[str] = None) -> List[Blocker]:
        resolved = []
        for blocker in self.open_blockers(mode=mode):
            resolved.append(self.resolve(blocker.id, resolution, actor=actor))
        return resolved


def blocker_state(owner: Any) -> BlockerState:
    return state_for(owner, "blockers")


__all__ = ["Blocker", "BlockerState", "blocker_state"]
