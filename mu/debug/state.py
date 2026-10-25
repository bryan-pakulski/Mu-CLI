"""Debug-mode records: the session's debug cases (modes v2 §4.2)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from mu.modes.events import Event, load_events
from mu.modes.state import ModeState, now, register_mode_state, state_for

# The ladder a case climbs; ``unreproducible`` and ``closed`` are terminal.
LADDER = ("open", "reproduced", "root_caused", "fixed", "verified")
STATUSES = LADDER + ("closed", "unreproducible")
HYPOTHESIS_STATUSES = ("untested", "supported", "refuted", "confirmed")
EVIDENCE_KINDS = ("run", "location", "search", "note")
STANCES = ("supports", "refutes")


@dataclass
class Hypothesis:
    id: str
    statement: str
    status: str = "untested"
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=now)
    updated_at: float = field(default_factory=now)


@dataclass
class Suspect:
    path: str
    reason: str
    line: Optional[int] = None
    symbol: str = ""


@dataclass
class DebugCase:
    id: str
    title: str
    symptom: str
    status: str = "open"
    repro: Dict[str, Any] = field(default_factory=dict)        # command, predicate, expect, runs
    hypotheses: List[Hypothesis] = field(default_factory=list)
    suspects: List[Suspect] = field(default_factory=list)
    fix: Dict[str, Any] = field(default_factory=dict)          # files, summary, diff_stat
    verification: Dict[str, Any] = field(default_factory=dict)  # repro_after, suite, repeats
    regression_test: Optional[Dict[str, Any]] = None
    root_cause: Optional[Dict[str, Any]] = None
    bisect: Optional[Dict[str, Any]] = None
    unreproducible_reason: str = ""
    memory_id: str = ""
    created_at: float = field(default_factory=now)
    updated_at: float = field(default_factory=now)

    def hypothesis(self, hyp_id: str) -> Optional[Hypothesis]:
        return next((h for h in self.hypotheses if h.id == str(hyp_id or "").strip()), None)

    @property
    def rung(self) -> int:
        return LADDER.index(self.status) if self.status in LADDER else len(LADDER)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "DebugCase":
        names = set(cls.__dataclass_fields__)
        data = {k: v for k, v in raw.items() if k in names}
        data["hypotheses"] = [
            Hypothesis(**{k: v for k, v in h.items() if k in Hypothesis.__dataclass_fields__})
            for h in raw.get("hypotheses") or [] if isinstance(h, dict) and h.get("id")
        ]
        data["suspects"] = [
            Suspect(**{k: v for k, v in s.items() if k in Suspect.__dataclass_fields__})
            for s in raw.get("suspects") or [] if isinstance(s, dict) and s.get("path")
        ]
        if data.get("status") not in STATUSES:
            data["status"] = "open"
        return cls(**data)


@register_mode_state
class DebugState(ModeState):
    name = "debug"

    def __init__(self) -> None:
        super().__init__()
        self.cases: Dict[str, DebugCase] = {}
        self.current: str = ""
        self.counter = 0
        self.events: List[Event] = []

    def to_dict(self) -> Dict[str, Any]:
        return {"counter": self.counter, "current": self.current,
                "cases": [c.to_dict() for c in self.cases.values()],
                "events": [e.to_dict() for e in self.events[-300:]]}

    def load_dict(self, data: Optional[Dict[str, Any]]) -> None:
        data = data or {}
        self.cases = {}
        for raw in data.get("cases") or []:
            if isinstance(raw, dict) and raw.get("id"):
                try:
                    case = DebugCase.from_dict(raw)
                except (TypeError, ValueError):
                    continue
                self.cases[case.id] = case
        self.counter = int(data.get("counter") or len(self.cases))
        self.current = str(data.get("current") or "")
        if self.current not in self.cases:
            self.current = ""
        self.events = load_events(data.get("events"), 300)

    def current_case(self) -> Optional[DebugCase]:
        return self.cases.get(self.current) if self.current else None

    def event(self, kind: str, actor: str, ref: str, detail: str = "", **data: Any) -> None:
        self.events.append(Event(kind=kind, actor=actor, ref=ref, detail=detail[:300], data=data))
        self.events = self.events[-300:]


def debug_state(owner: Any) -> DebugState:
    return state_for(owner, "debug")


__all__ = ["DebugCase", "DebugState", "EVIDENCE_KINDS", "HYPOTHESIS_STATUSES", "Hypothesis",
           "LADDER", "STANCES", "STATUSES", "Suspect", "debug_state"]
