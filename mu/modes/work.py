"""Work items: the session's plan, in typed records (modes v2 §3.3).

Replaces todos stored as tagged scratchpad entries. Items are never evicted,
the engine keeps exactly one ``in_progress`` item (starting one pauses the
previous back to ``pending``), and ``done`` can carry evidence (``CheckRun``
ids) — required when the state's ``require_evidence`` is on (loop mode).

``todo_write`` / ``todo_set_status`` / ``todo_list`` keep their names and
arguments and are thin wrappers over this store; ``completed`` is accepted as
an alias of ``done``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from .events import AGENT, Event, GateError, is_user, load_events, normalize_actor
from .state import ModeState, now, register_mode_state, state_for

STATUSES = ("pending", "in_progress", "blocked", "done", "dropped")
OPEN_STATUSES = ("pending", "in_progress", "blocked")
_ALIASES = {"completed": "done", "complete": "done", "todo": "pending", "active": "in_progress",
            "cancelled": "dropped", "canceled": "dropped"}


def normalize_status(value: Any) -> str:
    text = str(value or "pending").strip().lower()
    text = _ALIASES.get(text, text)
    if text not in STATUSES:
        raise ValueError(f"Unknown status {value!r}. Use one of: {', '.join(STATUSES)}.")
    return text


@dataclass
class WorkItem:
    id: str
    title: str
    status: str = "pending"
    parent_id: Optional[str] = None
    priority: int = 0
    evidence: List[str] = field(default_factory=list)
    note: str = ""
    created_at: float = field(default_factory=now)
    updated_at: float = field(default_factory=now)
    mode: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "WorkItem":
        try:
            status = normalize_status(raw.get("status"))
        except ValueError:
            status = "pending"
        return cls(
            id=str(raw.get("id") or ""), title=str(raw.get("title") or ""), status=status,
            parent_id=raw.get("parent_id") or None, priority=int(raw.get("priority") or 0),
            evidence=[str(e) for e in raw.get("evidence") or []], note=str(raw.get("note") or ""),
            created_at=float(raw.get("created_at") or 0.0),
            updated_at=float(raw.get("updated_at") or 0.0), mode=str(raw.get("mode") or ""),
        )


@register_mode_state
class WorkState(ModeState):
    name = "work"

    def __init__(self) -> None:
        super().__init__()
        self.items: Dict[str, WorkItem] = {}
        self.counter = 0
        self.events: List[Event] = []

    def to_dict(self) -> Dict[str, Any]:
        return {"counter": self.counter, "items": [i.to_dict() for i in self.items.values()],
                "events": [e.to_dict() for e in self.events[-200:]]}

    def load_dict(self, data: Optional[Dict[str, Any]]) -> None:
        data = data or {}
        self.items = {}
        for raw in data.get("items") or []:
            if isinstance(raw, dict) and raw.get("id"):
                item = WorkItem.from_dict(raw)
                self.items[item.id] = item
        self.counter = int(data.get("counter") or len(self.items))
        self.events = load_events(data.get("events"), 200)

    # ------------------------------------------------------------ queries

    def get(self, item_id: Any) -> Optional[WorkItem]:
        key = str(item_id or "").strip()
        if key.isdigit():
            key = f"w{key}"
        return self.items.get(key)

    def require(self, item_id: Any) -> WorkItem:
        item = self.get(item_id)
        if item is None:
            raise KeyError(f"No work item with id {item_id!r}.")
        return item

    def list(self, status: Optional[str] = None) -> List[WorkItem]:
        items = list(self.items.values())
        if status:
            wanted = normalize_status(status)
            items = [i for i in items if i.status == wanted]
        return items

    def current(self) -> Optional[WorkItem]:
        return next((i for i in self.items.values() if i.status == "in_progress"), None)

    def open_items(self) -> List[WorkItem]:
        return [i for i in self.items.values() if i.status in OPEN_STATUSES]

    def counts(self) -> Dict[str, int]:
        out = {s: 0 for s in STATUSES}
        for item in self.items.values():
            out[item.status] += 1
        return out

    # ------------------------------------------------------------ mutations

    def _event(self, kind: str, actor: str, ref: str, detail: str = "", **data: Any) -> None:
        self.events.append(Event(kind=kind, actor=actor, ref=ref, detail=detail, data=data))
        self.events = self.events[-200:]

    def add(self, title: str, *, status: str = "pending", parent_id: Optional[str] = None,
            priority: int = 0, note: str = "", actor: str = AGENT, mode: str = "") -> WorkItem:
        title = str(title or "").strip()
        if not title:
            raise ValueError("A work item needs a title.")
        status = normalize_status(status)
        actor = normalize_actor(actor)
        with self.lock:
            if parent_id and self.get(parent_id) is None:
                raise KeyError(f"No parent work item {parent_id!r}.")
            self.counter += 1
            item = WorkItem(id=f"w{self.counter}", title=title, status="pending",
                            parent_id=parent_id, priority=int(priority or 0), note=note, mode=mode)
            self.items[item.id] = item
            self._event("added", actor, item.id, title)
            if status != "pending":
                self.set_status(item.id, status, actor=actor)
            self.touch()
            return item

    def set_status(self, item_id: Any, status: str, *, evidence: Optional[List[str]] = None,
                   note: str = "", actor: str = AGENT, require_evidence: bool = False,
                   override_reason: str = "") -> WorkItem:
        status = normalize_status(status)
        actor = normalize_actor(actor)
        with self.lock:
            item = self.require(item_id)
            refs = [str(e).strip() for e in (evidence or []) if str(e).strip()]
            if refs:
                from .evidence import checks_state

                checks = checks_state(self._owner_obj())
                unknown = [r for r in refs if checks.get(r) is None]
                if unknown:
                    raise GateError(f"Unknown check run(s): {', '.join(unknown)}.", code="not_found")
                failing = [r for r in refs if not checks.require(r).passed]
                if failing and status == "done":
                    raise GateError(
                        f"Check run(s) {', '.join(failing)} did not pass; a failing run is not "
                        "evidence of done.", code="evidence_failed")
            if status == "done" and require_evidence and not (refs or item.evidence):
                if not is_user(actor):
                    raise GateError(
                        f"{item.id} can only be marked done with evidence: pass the id of a passing "
                        "check run (r<n>).", code="evidence_required")
                if not override_reason.strip():
                    raise GateError("Completing without evidence needs an override reason.",
                                    code="override_reason_required")
                self._event("override", actor, item.id, override_reason.strip(), to=status)
            if status == "in_progress":
                for other in self.items.values():
                    if other.id != item.id and other.status == "in_progress":
                        other.status = "pending"
                        other.updated_at = now()
                        self._event("paused", "system", other.id, f"{item.id} started")
            previous = item.status
            item.status = status
            for ref in refs:
                if ref not in item.evidence:
                    item.evidence.append(ref)
            if note:
                item.note = note
            item.updated_at = now()
            self._event("status", actor, item.id, f"{previous} → {status}", evidence=refs)
            self.touch()
            return item

    def delete(self, item_id: Any, *, actor: str = AGENT) -> WorkItem:
        with self.lock:
            item = self.require(item_id)
            del self.items[item.id]
            self._event("deleted", normalize_actor(actor), item.id, item.title)
            self.touch()
            return item

    def clear_items(self, status: Optional[str] = None, *, actor: str = AGENT) -> int:
        with self.lock:
            doomed = [i.id for i in self.list(status)]
            for item_id in doomed:
                del self.items[item_id]
            if doomed:
                self._event("cleared", normalize_actor(actor), "", f"{len(doomed)} items")
                self.touch()
            return len(doomed)

    def _owner_obj(self) -> Any:
        # Evidence lives in the same ModeStates as this state.
        return _OwnerShim(self._owner)


class _OwnerShim:
    def __init__(self, states: Any) -> None:
        self.mode_state = states


def work_state(owner: Any) -> WorkState:
    return state_for(owner, "work")


def render_items(items: List[WorkItem]) -> str:
    marks = {"pending": "○", "in_progress": "▸", "blocked": "!", "done": "✓", "dropped": "–"}
    lines = []
    for item in items:
        evidence = f" [{', '.join(item.evidence)}]" if item.evidence else ""
        lines.append(f"{marks.get(item.status, '·')} {item.id} [{item.status}] {item.title}{evidence}")
    return "\n".join(lines)


__all__ = [
    "OPEN_STATUSES",
    "STATUSES",
    "WorkItem",
    "WorkState",
    "normalize_status",
    "render_items",
    "work_state",
]
