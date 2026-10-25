"""Loop-mode records: the mission (modes v2 §4.4)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from mu.modes.events import Event, load_events
from mu.modes.state import ModeState, now, register_mode_state, state_for

STATUSES = ("running", "paused", "stopped", "done", "blocked")
RESULTS = ("passed", "failed", "partial")
BUDGET_KEYS = ("max_increments", "max_cost_usd", "max_runtime_s", "max_turns")
SPENT_KEYS = ("increments", "cost_usd", "runtime_s", "turns")


@dataclass
class Criterion:
    id: str
    text: str
    check: Optional[Dict[str, Any]] = None     # {command, predicate}
    met: bool = False
    evidence: List[str] = field(default_factory=list)
    attested_by: str = ""                       # "" | user:<surface> (attested without a run)
    note: str = ""


@dataclass
class Increment:
    n: int
    objective: str
    result: str
    decision: str = ""
    next: str = ""
    work_item: str = ""
    actions: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    delegations: List[str] = field(default_factory=list)
    remediation_item: str = ""
    at: float = field(default_factory=now)


@dataclass
class LoopMission:
    id: str
    goal: str
    status: str = "running"
    status_reason: str = ""
    goal_history: List[Dict[str, Any]] = field(default_factory=list)
    done_when: List[Criterion] = field(default_factory=list)
    budget: Dict[str, float] = field(default_factory=dict)
    spent: Dict[str, float] = field(default_factory=lambda: {k: 0 for k in SPENT_KEYS})
    increments: List[Increment] = field(default_factory=list)
    started_at: float = field(default_factory=now)
    ended_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "LoopMission":
        names = set(cls.__dataclass_fields__)
        data = {k: v for k, v in raw.items() if k in names}
        data["done_when"] = [Criterion(**{k: v for k, v in c.items() if k in Criterion.__dataclass_fields__})
                             for c in raw.get("done_when") or [] if isinstance(c, dict) and c.get("id")]
        data["increments"] = [Increment(**{k: v for k, v in i.items() if k in Increment.__dataclass_fields__})
                              for i in raw.get("increments") or [] if isinstance(i, dict) and "n" in i]
        spent = {k: 0 for k in SPENT_KEYS}
        spent.update({k: v for k, v in (raw.get("spent") or {}).items() if k in SPENT_KEYS})
        data["spent"] = spent
        data["budget"] = {k: v for k, v in (raw.get("budget") or {}).items() if k in BUDGET_KEYS and v}
        if data.get("status") not in STATUSES:
            data["status"] = "paused"
        return cls(**data)

    def criterion(self, cid: str) -> Optional[Criterion]:
        return next((c for c in self.done_when if c.id == str(cid or "").strip()), None)


@register_mode_state
class LoopState(ModeState):
    name = "loop"

    def __init__(self) -> None:
        super().__init__()
        self.mission: Optional[LoopMission] = None
        self.past: List[Dict[str, Any]] = []
        self.counter = 0
        self.events: List[Event] = []

    def to_dict(self) -> Dict[str, Any]:
        return {"counter": self.counter, "mission": self.mission.to_dict() if self.mission else None,
                "past": self.past[-10:], "events": [e.to_dict() for e in self.events[-300:]]}

    def load_dict(self, data: Optional[Dict[str, Any]]) -> None:
        data = data or {}
        raw = data.get("mission")
        try:
            self.mission = LoopMission.from_dict(raw) if isinstance(raw, dict) and raw.get("id") else None
        except (TypeError, ValueError):
            self.mission = None
        self.past = [p for p in data.get("past") or [] if isinstance(p, dict)][-10:]
        self.counter = int(data.get("counter") or (1 if self.mission else 0))
        self.events = load_events(data.get("events"), 300)

    def event(self, kind: str, actor: str, detail: str = "", **data: Any) -> None:
        ref = self.mission.id if self.mission else ""
        self.events.append(Event(kind=kind, actor=actor, ref=ref, detail=detail[:300], data=data))
        self.events = self.events[-300:]


def loop_state(owner: Any) -> LoopState:
    return state_for(owner, "loop")


__all__ = ["BUDGET_KEYS", "Criterion", "Increment", "LoopMission", "LoopState", "RESULTS",
           "SPENT_KEYS", "STATUSES", "loop_state"]
