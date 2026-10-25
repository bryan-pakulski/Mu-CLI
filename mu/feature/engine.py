"""Feature plan engine (modes v2 §4.3).

A feature plan moves ``draft → approved → executing → review → done``.

* The agent proposes and revises a **draft**; only the user approves it
  (``approve``: actor, time and the plan hash are recorded). Revising an
  approved plan makes a new revision that needs re-approval; only the tasks
  that changed are reset.
* Execution has **one task cursor** (``start_task``). The git tree at task
  start is recorded so the task's diff is known.
* Each exit criterion is met by **evidence**: its declared check (run by
  ``run_check``), a passing check run, or a file reference with a short
  justification. A user may attest a criterion (recorded as such).
  ``complete_task`` refuses until every criterion is met; a user can override
  with a reason, recorded as an ``override`` event.
* ``review_task`` needs the task's diff and a passing check after the last
  edit; issues become follow-up tasks or blockers. Feature completion needs
  every task completed and reviewed, no open blockers, and a passing suite run
  after the last change; the user confirms it, the agent can only request it.
* Blockers are the session's core blockers with ``ref=t<id>``.

Every surface — tools, GUI, mobile, ``/feature`` — goes through these
functions; transitions take an ``actor``.

Plans persist as JSON under the session (``features/<id>.json``). The session
keeps only a pointer record per feature (``registry_record``).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from mu.modes.events import AGENT, GateError, is_user, normalize_actor, require_user
from mu.modes.state import invalidate_cached, load_cached
from utils.config import HISTORY_DIR

PLAN_STATUSES = ("draft", "approved", "executing", "review", "done")
TASK_STATUSES = ("not_started", "in_progress", "blocked", "completed", "archived")
STATUS_NOT_STARTED, STATUS_IN_PROGRESS, STATUS_BLOCKED, STATUS_COMPLETED, STATUS_ARCHIVED = TASK_STATUSES
OPEN_TASK = ("not_started", "in_progress", "blocked")
DONE_TASK = ("completed", "archived")
ISSUE_SEVERITIES = ("follow_up", "blocking")


def normalize_task_status(status: Any) -> str:
    raw = str(status or "").strip().lower()
    if raw in {"todo", "queued", "pending"}:
        return STATUS_NOT_STARTED
    if raw in {"done", "complete"}:
        return STATUS_COMPLETED
    return raw if raw in TASK_STATUSES else STATUS_NOT_STARTED


# ----------------------------------------------------------------- model


@dataclass
class Criterion:
    id: str
    text: str
    check: Optional[Dict[str, Any]] = None     # {command, predicate}
    met: bool = False
    evidence: List[Dict[str, Any]] = field(default_factory=list)  # {kind: run|file|attested, ref, justification, actor, at}


@dataclass
class FeatureTask:
    id: int
    title: str
    phase_id: Optional[int] = None
    objective: str = ""
    actions: List[str] = field(default_factory=list)
    criteria: List[Criterion] = field(default_factory=list)
    status: str = STATUS_NOT_STARTED
    notes: str = ""
    origin: str = "plan"                       # plan | review
    start_tree: str = ""
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    diff_stat: str = ""
    reviewed: bool = False
    override: Optional[Dict[str, Any]] = None

    def criterion(self, cid: str) -> Optional[Criterion]:
        return next((c for c in self.criteria if c.id == str(cid or "").strip()), None)

    @property
    def unmet(self) -> List[Criterion]:
        return [c for c in self.criteria if not c.met]


@dataclass
class FeaturePhase:
    id: int
    title: str
    goal: str = ""
    order: int = 0


@dataclass
class FeatureEvent:
    id: str
    kind: str
    entity: str
    entity_id: Any
    payload: Dict[str, Any] = field(default_factory=dict)
    actor: str = "system"
    created_at: float = field(default_factory=time.time)


@dataclass
class DiffProposal:
    id: str
    task_id: int
    diff: str
    rationale: str = ""
    status: str = "pending"                    # pending | approved | rejected
    decision_reason: str = ""
    decided_by: str = ""
    created_at: float = field(default_factory=time.time)
    decided_at: Optional[float] = None


@dataclass
class TaskReview:
    id: str
    task_id: int
    summary: str
    issues: List[Dict[str, Any]] = field(default_factory=list)
    diff_stat: str = ""
    check_run: str = ""
    actor: str = AGENT
    created_at: float = field(default_factory=time.time)


@dataclass
class FeaturePlan:
    feature_id: str
    feature_name: str
    feature_request: str
    directory: str
    metadata_path: str = ""
    status: str = "draft"
    revision: int = 1
    approval: Optional[Dict[str, Any]] = None
    approval_history: List[Dict[str, Any]] = field(default_factory=list)
    tasks: List[FeatureTask] = field(default_factory=list)
    phases_meta: List[FeaturePhase] = field(default_factory=list)
    event_log: List[FeatureEvent] = field(default_factory=list)
    review_records: List[TaskReview] = field(default_factory=list)
    diff_proposals: List[DiffProposal] = field(default_factory=list)
    current_task_id: Optional[int] = None
    completion_requested: bool = False
    completion: Optional[Dict[str, Any]] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def task(self, task_id: Any) -> Optional[FeatureTask]:
        try:
            wanted = int(str(task_id).lstrip("t"))
        except (TypeError, ValueError):
            return None
        return next((t for t in self.tasks if t.id == wanted), None)

    def phase(self, phase_id: Any) -> Optional[FeaturePhase]:
        return next((p for p in self.phases_meta if str(p.id) == str(phase_id)), None)

    def live_tasks(self) -> List[FeatureTask]:
        return [t for t in self.tasks if t.status != STATUS_ARCHIVED]

    def current_task(self) -> Optional[FeatureTask]:
        return next((t for t in self.tasks if t.status == STATUS_IN_PROGRESS), None)

    def next_task(self) -> Optional[FeatureTask]:
        current = self.current_task()
        if current is not None:
            return current
        order = {p.id: (p.order or p.id) for p in self.phases_meta}
        candidates = [t for t in self.tasks if t.status == STATUS_NOT_STARTED]
        candidates.sort(key=lambda t: (order.get(t.phase_id, 10 ** 6), t.id))
        return candidates[0] if candidates else None

    def tasks_completed(self) -> bool:
        live = self.live_tasks()
        return bool(live) and all(t.status == STATUS_COMPLETED for t in live)

    @property
    def approved(self) -> bool:
        return self.approval is not None and self.status != "draft"

    def add_event(self, *, kind: str, entity: str, entity_id: Any,
                  payload: Optional[Dict[str, Any]] = None, actor: str = "system") -> None:
        self.event_log.append(FeatureEvent(id=f"{int(time.time() * 1000)}-{len(self.event_log) + 1}",
                                           kind=kind, entity=entity, entity_id=entity_id,
                                           payload=payload or {}, actor=actor))
        self.event_log = self.event_log[-500:]


# ----------------------------------------------------------------- persistence

_LOCKS: Dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def plan_lock(path: str) -> threading.RLock:
    key = os.path.realpath(path or "")
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return slug or "feature"


def _workspace_root(folder_context) -> str:
    if folder_context and getattr(folder_context, "folders", None):
        return os.path.abspath(folder_context.folders[0])
    return os.getcwd()


def metadata_path_for(session_id: str, feature_id: str) -> str:
    base = os.path.join(os.path.expanduser(str(HISTORY_DIR)), "sessions", session_id, "features") \
        if session_id else os.path.join(os.path.expanduser(str(HISTORY_DIR)), "features")
    return os.path.join(base, f"{_slugify(feature_id)}.json")


def save_feature_plan(plan: FeaturePlan) -> FeaturePlan:
    plan.updated_at = time.time()
    os.makedirs(os.path.dirname(plan.metadata_path), exist_ok=True)
    tmp = f"{plan.metadata_path}.{uuid.uuid4().hex[:6]}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(asdict(plan), handle, indent=2, default=str)
    os.replace(tmp, plan.metadata_path)
    invalidate_cached(plan.metadata_path)
    return plan


def _pick(cls, raw: Any) -> Dict[str, Any]:
    names = set(cls.__dataclass_fields__)
    return {k: v for k, v in (raw or {}).items() if k in names} if isinstance(raw, dict) else {}


def _criteria_from(raw_task: Dict[str, Any]) -> List[Criterion]:
    if raw_task.get("criteria"):
        out = []
        for raw in raw_task["criteria"]:
            if isinstance(raw, dict) and raw.get("id"):
                out.append(Criterion(**_pick(Criterion, raw)))
        return out
    # Earlier plans: exit_criteria strings + a verified subset (self-attested).
    verified = {str(v).strip() for v in raw_task.get("verified_exit_criteria") or []}
    out = []
    for n, text in enumerate(raw_task.get("exit_criteria") or [], 1):
        text = str(text).strip()
        if not text:
            continue
        met = text in verified
        out.append(Criterion(id=f"c{n}", text=text, met=met,
                             evidence=[{"kind": "attested", "ref": "", "justification": "earlier plan",
                                        "actor": "legacy", "at": 0}] if met else []))
    return out


def _task_from(raw: Dict[str, Any]) -> FeatureTask:
    data = _pick(FeatureTask, raw)
    data["criteria"] = _criteria_from(raw)
    data["status"] = normalize_task_status(raw.get("status"))
    if not data.get("objective"):
        objectives = raw.get("objectives") or []
        data["objective"] = str(objectives[0]) if objectives else ""
    if not data.get("actions") and raw.get("action_points"):
        data["actions"] = [str(a) for a in raw["action_points"]]
    data.setdefault("title", f"Task {raw.get('id')}")
    return FeatureTask(**data)


def _parse_plan(path: str) -> FeaturePlan:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    base = _pick(FeaturePlan, data)
    base["metadata_path"] = path
    base["tasks"] = [_task_from(t) for t in data.get("tasks") or [] if isinstance(t, dict) and "id" in t]
    base["phases_meta"] = [FeaturePhase(**_pick(FeaturePhase, p)) for p in data.get("phases_meta") or []
                           if isinstance(p, dict) and "id" in p]
    base["event_log"] = [FeatureEvent(**_pick(FeatureEvent, e)) for e in data.get("event_log") or []
                         if isinstance(e, dict) and e.get("id")]
    base["review_records"] = [TaskReview(**_pick(TaskReview, r)) for r in data.get("review_records") or []
                              if isinstance(r, dict) and r.get("id") and "task_id" in r]
    base["diff_proposals"] = [DiffProposal(**_pick(DiffProposal, d)) for d in data.get("diff_proposals") or []
                              if isinstance(d, dict) and d.get("id") and "task_id" in d]
    plan = FeaturePlan(**base)
    if "status" not in data:
        # Earlier plans: an approved boolean and a review_status.
        if str(data.get("review_status") or "") == "completed":
            plan.status = "done"
        elif data.get("approved"):
            plan.status = "executing" if any(t.status != STATUS_NOT_STARTED for t in plan.tasks) else "approved"
        else:
            plan.status = "draft"
        if data.get("approved") and plan.approval is None:
            plan.approval = {"actor": "legacy", "at": plan.updated_at, "plan_hash": plan_hash(plan),
                             "revision": plan.revision}
    if plan.status not in PLAN_STATUSES:
        plan.status = "draft"
    for review in data.get("review_records") or []:
        task = plan.task(review.get("task_id")) if isinstance(review, dict) else None
        if task is not None and task.status == STATUS_COMPLETED and "reviewed" not in (
                next((t for t in data.get("tasks") or [] if t.get("id") == task.id), {})):
            task.reviewed = True
    return plan


def load_feature_plan(path: str) -> FeaturePlan:
    """A fresh, mutable plan from disk (raises FileNotFoundError / ValueError)."""
    if not path or not os.path.isfile(path):
        raise FileNotFoundError(f"feature plan not found: {path}")
    return _parse_plan(path)


def read_feature_plan(path: str) -> Optional[FeaturePlan]:
    """Cached, read-only view (panels, capsule, L3). Never mutate the result."""
    if not path or not os.path.isfile(path):
        return None
    try:
        return load_cached(path, _parse_plan)
    except (OSError, ValueError, TypeError):
        return None


# ----------------------------------------------------------------- hashing


def _task_hash(task: FeatureTask) -> str:
    body = json.dumps({"title": task.title, "phase": task.phase_id, "objective": task.objective,
                       "actions": task.actions,
                       "criteria": [(c.text, c.check) for c in task.criteria]}, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


def plan_hash(plan: FeaturePlan) -> str:
    """Hash of what the user approves: phases and the planned tasks (review
    follow-ups are within the approved scope and excluded)."""
    body = json.dumps({
        "goal": plan.feature_request,
        "phases": [(p.id, p.title, p.goal) for p in sorted(plan.phases_meta, key=lambda p: p.id)],
        "tasks": [(t.id, _task_hash(t)) for t in plan.tasks if t.origin == "plan" and t.status != STATUS_ARCHIVED],
    }, sort_keys=True)
    return hashlib.sha256(body.encode()).hexdigest()[:16]


# ----------------------------------------------------------------- session pointer


def registry_record(plan: FeaturePlan) -> Dict[str, Any]:
    """The pointer the session keeps for a feature (the one place it is built)."""
    live = plan.live_tasks()
    return {
        "type": "feature",
        "feature_id": plan.feature_id,
        "feature_name": plan.feature_name,
        "directory": plan.directory,
        "metadata_path": plan.metadata_path,
        "status": plan.status,
        "revision": plan.revision,
        "approved": plan.approved,
        "task_count": len(live),
        "completed_count": sum(1 for t in live if t.status == STATUS_COMPLETED),
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
    }


def _manager(owner: Any) -> Any:
    session = getattr(owner, "session", None) or owner
    return getattr(session, "session_manager", None) or session


def sync_registry(owner: Any, plan: FeaturePlan, *, activate: bool = True) -> Dict[str, Any]:
    manager = _manager(owner)
    record = registry_record(plan)
    existing = getattr(manager, "feature_registry", {}).get(plan.feature_id)
    if isinstance(existing, dict) and existing.get("archived"):
        record["archived"] = True
    if hasattr(manager, "upsert_feature"):
        manager.upsert_feature(record)
    if plan.status == "done":
        manager.feature_registry[plan.feature_id]["archived"] = True
        if getattr(manager, "active_feature_id", None) == plan.feature_id:
            manager.active_feature_id = None
            manager.feature_state = None
    elif activate:
        manager.active_feature_id = plan.feature_id
        manager.feature_state = dict(record)
    return record


def active_record(owner: Any) -> Optional[Dict[str, Any]]:
    manager = _manager(owner)
    state = getattr(manager, "feature_state", None)
    return dict(state) if isinstance(state, dict) and state.get("metadata_path") else None


def active_plan(owner: Any, *, cached: bool = False) -> Optional[FeaturePlan]:
    record = active_record(owner)
    if record is None:
        return None
    path = record["metadata_path"]
    if cached:
        return read_feature_plan(path)
    try:
        return load_feature_plan(path)
    except (OSError, ValueError, TypeError):
        return None


def require_plan(owner: Any) -> FeaturePlan:
    plan = active_plan(owner)
    if plan is None:
        raise GateError("No active feature. Propose one with propose_feature_plan, or load one "
                        "(/feature load <id>).", code="no_feature")
    return plan


def _commit(owner: Any, plan: FeaturePlan) -> FeaturePlan:
    save_feature_plan(plan)
    sync_registry(owner, plan, activate=plan.status != "done")
    manager = _manager(owner)
    if getattr(manager, "current_session_name", "") and hasattr(manager, "save_history"):
        try:
            session = getattr(owner, "session", None) or owner
            manager.save_history(getattr(session, "folder_context", None))
        except Exception:  # noqa: BLE001 — the plan file is the source of truth
            pass
    return plan


# ----------------------------------------------------------------- planning


def _parse_criteria(raw: Any, task_title: str) -> List[Criterion]:
    out: List[Criterion] = []
    for n, item in enumerate(raw or [], 1):
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict):
            raise ValueError(f"task {task_title!r}: exit criteria are strings or {{text, check}} objects")
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        check = item.get("check")
        if isinstance(check, str):
            check = {"command": check}
        if check is not None:
            if not isinstance(check, dict) or not str(check.get("command") or "").strip():
                raise ValueError(f"task {task_title!r}: criterion {text!r} check needs a command")
            check = {"command": str(check["command"]).strip(), "predicate": check.get("predicate") or {"exit": 0}}
        out.append(Criterion(id=f"c{n}", text=text, check=check))
    if not out:
        raise ValueError(f"task {task_title!r} needs at least one exit criterion")
    return out


def _parse_task(raw: Dict[str, Any], task_id: int, phase_id: Optional[int]) -> FeatureTask:
    if not isinstance(raw, dict):
        raise ValueError("tasks are objects: {title, objective, actions, exit_criteria}")
    title = str(raw.get("title") or "").strip()
    if not title:
        raise ValueError("every task needs a title")
    return FeatureTask(id=task_id, title=title, phase_id=phase_id,
                       objective=str(raw.get("objective") or "").strip(),
                       actions=[str(a).strip() for a in raw.get("actions") or [] if str(a).strip()],
                       criteria=_parse_criteria(raw.get("exit_criteria") or raw.get("criteria"), title))


def propose_plan(owner: Any, *, name: str, goal: str, phases: List[Dict[str, Any]],
                 actor: str = AGENT) -> FeaturePlan:
    """Persist a draft plan. Replaces the active draft; on an approved plan it
    becomes a new revision that needs re-approval."""
    actor = normalize_actor(actor)
    if not str(goal or "").strip():
        raise ValueError("A feature plan needs a goal (the user's request in one or two sentences).")
    if not phases:
        raise ValueError("A feature plan needs at least one phase with tasks.")
    parsed_phases: List[FeaturePhase] = []
    tasks: List[FeatureTask] = []
    for p_index, raw_phase in enumerate(phases, 1):
        if not isinstance(raw_phase, dict):
            raise ValueError("phases are objects: {title, goal?, tasks: [...]}")
        title = str(raw_phase.get("title") or f"Phase {p_index}").strip()
        parsed_phases.append(FeaturePhase(id=p_index, title=title, goal=str(raw_phase.get("goal") or "").strip(),
                                          order=p_index))
        raw_tasks = raw_phase.get("tasks") or []
        if not raw_tasks:
            raise ValueError(f"phase {title!r} has no tasks")
        for raw_task in raw_tasks:
            tasks.append(_parse_task(raw_task, len(tasks) + 1, p_index))
    existing = active_plan(owner)
    if existing is not None and existing.status != "done":
        return _replace_plan(owner, existing, name=name, goal=goal, phases=parsed_phases, tasks=tasks, actor=actor)
    return _new_plan(owner, name=name, goal=goal, phases=parsed_phases, tasks=tasks, actor=actor)


def _new_plan(owner: Any, *, name: str, goal: str, phases: List[FeaturePhase], tasks: List[FeatureTask],
              actor: str) -> FeaturePlan:
    manager = _manager(owner)
    session = getattr(owner, "session", None) or owner
    allocate = getattr(manager, "allocate_feature_id", None)
    feature_id = allocate(name or goal) if allocate else _slugify(name or goal)
    session_id = str(getattr(manager, "current_session_name", "") or "")
    plan = FeaturePlan(feature_id=feature_id, feature_name=str(name or "").strip() or feature_id,
                       feature_request=str(goal).strip(),
                       directory=_workspace_root(getattr(session, "folder_context", None)),
                       metadata_path=metadata_path_for(session_id, feature_id),
                       tasks=tasks, phases_meta=phases)
    plan.add_event(kind="proposed", entity="feature", entity_id=feature_id,
                   payload={"tasks": len(tasks), "hash": plan_hash(plan)}, actor=actor)
    return _commit(owner, plan)


def _replace_plan(owner: Any, plan: FeaturePlan, *, name: str, goal: str, phases: List[FeaturePhase],
                  tasks: List[FeatureTask], actor: str) -> FeaturePlan:
    with plan_lock(plan.metadata_path):
        old_by_title = {t.title: t for t in plan.tasks if t.origin == "plan"}
        for task in tasks:
            prior = old_by_title.get(task.title)
            if prior is not None and _task_hash(prior) == _task_hash(task):
                # Unchanged tasks keep their progress and evidence.
                prior.id, prior.phase_id = task.id, task.phase_id
                tasks[tasks.index(task)] = prior
        followups = [t for t in plan.tasks if t.origin == "review"]
        next_id = max([t.id for t in tasks] or [0])
        for extra in followups:
            next_id += 1
            extra.id = next_id
        plan.tasks = tasks + followups
        plan.phases_meta = phases
        plan.feature_request = str(goal).strip()
        if name:
            plan.feature_name = str(name).strip()
        return _after_revision(owner, plan, actor, "proposed")


def _after_revision(owner: Any, plan: FeaturePlan, actor: str, kind: str) -> FeaturePlan:
    changed = plan.approval is not None and plan.approval.get("plan_hash") != plan_hash(plan)
    if changed:
        plan.approval_history.append(dict(plan.approval))
        plan.approval = None
        plan.revision += 1
        plan.status = "draft"
        plan.completion_requested = False
        approved_hashes = set(plan.approval_history[-1].get("task_hashes") or [])
        for task in plan.tasks:
            if task.origin == "plan" and _task_hash(task) not in approved_hashes and task.status != STATUS_ARCHIVED:
                task.status = STATUS_NOT_STARTED
                task.reviewed = False
                task.completed_at = None
                for crit in task.criteria:
                    crit.met, crit.evidence = False, []
    elif plan.approval is None:
        plan.status = "draft"
    plan.current_task_id = (plan.current_task() or FeatureTask(id=0, title="")).id or None
    plan.add_event(kind=kind, entity="feature", entity_id=plan.feature_id,
                   payload={"revision": plan.revision, "hash": plan_hash(plan), "needs_approval": plan.approval is None},
                   actor=actor)
    return _commit(owner, plan)


def revise_plan(owner: Any, patch: Dict[str, Any], *, actor: str = AGENT) -> FeaturePlan:
    """Edit the plan: add_phases, add_tasks [{phase_id, ...}], update_tasks
    [{id, title?, objective?, actions?, exit_criteria?}], remove_tasks [ids]."""
    actor = normalize_actor(actor)
    plan = require_plan(owner)
    if plan.status == "done":
        raise GateError("The feature is done; propose a new feature.", code="feature_done")
    with plan_lock(plan.metadata_path):
        for raw_phase in patch.get("add_phases") or []:
            pid = max([p.id for p in plan.phases_meta] or [0]) + 1
            plan.phases_meta.append(FeaturePhase(id=pid, title=str(raw_phase.get("title") or f"Phase {pid}"),
                                                 goal=str(raw_phase.get("goal") or ""), order=pid))
        for raw in patch.get("add_tasks") or []:
            phase_id = raw.get("phase_id") or (plan.phases_meta[-1].id if plan.phases_meta else None)
            if phase_id is not None and plan.phase(phase_id) is None:
                raise ValueError(f"no phase {phase_id}")
            plan.tasks.append(_parse_task(raw, max([t.id for t in plan.tasks] or [0]) + 1,
                                          int(phase_id) if phase_id is not None else None))
        for raw in patch.get("update_tasks") or []:
            task = plan.task(raw.get("id"))
            if task is None:
                raise ValueError(f"no task {raw.get('id')}")
            if task.status in DONE_TASK and plan.approval is not None:
                raise GateError(f"t{task.id} is {task.status}; reopen it before changing it.",
                                code="task_closed")
            if raw.get("title"):
                task.title = str(raw["title"]).strip()
            if "objective" in raw:
                task.objective = str(raw.get("objective") or "").strip()
            if "actions" in raw:
                task.actions = [str(a).strip() for a in raw.get("actions") or [] if str(a).strip()]
            if raw.get("exit_criteria"):
                task.criteria = _parse_criteria(raw["exit_criteria"], task.title)
        for raw_id in patch.get("remove_tasks") or []:
            task = plan.task(raw_id)
            if task is None:
                raise ValueError(f"no task {raw_id}")
            if task.status in (STATUS_IN_PROGRESS, STATUS_COMPLETED):
                raise GateError(f"t{task.id} is {task.status}; it cannot be removed.", code="task_closed")
            plan.tasks.remove(task)
        if patch.get("goal"):
            plan.feature_request = str(patch["goal"]).strip()
        return _after_revision(owner, plan, actor, "revised")


def approve(owner: Any, *, actor: str) -> FeaturePlan:
    actor = normalize_actor(actor)
    require_user(actor, "Approving the feature plan")
    plan = require_plan(owner)
    with plan_lock(plan.metadata_path):
        if plan.status != "draft":
            raise GateError(f"The plan is {plan.status}; only a draft is approved.", code="wrong_status")
        if not plan.live_tasks():
            raise GateError("The plan has no tasks.", code="empty_plan")
        plan.approval = {"actor": actor, "at": time.time(), "plan_hash": plan_hash(plan),
                         "revision": plan.revision,
                         "task_hashes": [_task_hash(t) for t in plan.tasks if t.origin == "plan"]}
        plan.status = "executing" if any(t.status != STATUS_NOT_STARTED for t in plan.live_tasks()) else "approved"
        plan.add_event(kind="approved", entity="feature", entity_id=plan.feature_id,
                       payload={"revision": plan.revision, "hash": plan.approval["plan_hash"]}, actor=actor)
        return _commit(owner, plan)


def _require_approved(plan: FeaturePlan) -> None:
    if plan.approval is None or plan.status == "draft":
        raise GateError("The plan is a draft waiting for the user's approval (/feature approve or the "
                        "panel's Approve). Do not start implementation.", code="not_approved")
    if plan.approval.get("plan_hash") != plan_hash(plan):
        raise GateError("The plan changed since it was approved; it needs re-approval.", code="not_approved")


# ----------------------------------------------------------------- execution


def _git(root: str, *args: str) -> Tuple[int, str]:
    try:
        result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return result.returncode, (result.stdout or "").strip()


def current_tree(root: str) -> str:
    """A commit id for the working tree right now (``git stash create`` makes one
    without touching anything; HEAD when the tree is clean). '' outside git."""
    code, out = _git(root, "stash", "create")
    if code == 0 and out:
        return out.splitlines()[-1]
    code, out = _git(root, "rev-parse", "HEAD")
    return out if code == 0 else ""


def diff_stat(root: str, start_tree: str) -> str:
    if not start_tree:
        return ""
    code, out = _git(root, "diff", "--stat", start_tree)
    return out if code == 0 else ""


def start_task(owner: Any, task_id: Any, *, actor: str = AGENT) -> FeatureTask:
    actor = normalize_actor(actor)
    plan = require_plan(owner)
    _require_approved(plan)
    with plan_lock(plan.metadata_path):
        task = plan.task(task_id)
        if task is None:
            raise GateError(f"No task {task_id}.", code="not_found")
        current = plan.current_task()
        if current is not None and current.id != task.id:
            raise GateError(f"t{current.id} is in progress; complete or block it before starting t{task.id}.",
                            code="task_in_progress")
        if task.status not in (STATUS_NOT_STARTED, STATUS_BLOCKED, STATUS_IN_PROGRESS):
            raise GateError(f"t{task.id} is {task.status}.", code="wrong_status")
        if task.status != STATUS_IN_PROGRESS:
            task.start_tree = current_tree(plan.directory)
            task.started_at = time.time()
        task.status = STATUS_IN_PROGRESS
        plan.current_task_id = task.id
        if plan.status in ("approved", "review"):
            plan.status = "executing"
        plan.add_event(kind="started", entity="task", entity_id=task.id,
                       payload={"start_tree": task.start_tree}, actor=actor)
        _commit(owner, plan)
        return task


def check_criterion(owner: Any, task_id: Any, criterion_id: str, *, evidence: Optional[List[str]] = None,
                    file: str = "", justification: str = "", timeout: int = 600,
                    actor: str = AGENT) -> Tuple[Criterion, Any]:
    """Meet a criterion with evidence: cited passing runs, a file + justification,
    or (no evidence given) its declared check, run now."""
    from mu.modes.evidence import CheckSpec, checks_state, run_check

    actor = normalize_actor(actor)
    plan = require_plan(owner)
    _require_approved(plan)
    task = plan.task(task_id)
    if task is None:
        raise GateError(f"No task {task_id}.", code="not_found")
    if task.status != STATUS_IN_PROGRESS:
        raise GateError(f"t{task.id} is {task.status}; start it first.", code="wrong_status")
    crit = task.criterion(criterion_id)
    if crit is None:
        raise GateError(f"t{task.id} has no criterion {criterion_id!r}.", code="not_found")
    run = None
    item: Dict[str, Any]
    refs = [str(r).strip() for r in evidence or [] if str(r).strip()]
    if refs:
        checks = checks_state(owner)
        runs = [checks.get(r) for r in refs]
        if any(r is None for r in runs):
            raise GateError(f"Unknown check run in {refs}.", code="not_found")
        run = next((r for r in runs if r.passed), None)
        if run is None:
            raise GateError("None of the cited runs passed; a failing run is not evidence.", code="evidence_failed")
        item = {"kind": "run", "ref": run.id, "justification": str(justification or "").strip()}
    elif file:
        path = os.path.normpath(os.path.join(plan.directory, file))
        if not os.path.exists(path):
            raise ValueError(f"{file} does not exist")
        if not str(justification or "").strip():
            raise ValueError("A file reference needs a justification: how the file shows the criterion is met.")
        item = {"kind": "file", "ref": os.path.relpath(path, plan.directory), "justification": str(justification).strip()}
    elif crit.check:
        run = run_check(CheckSpec(command=crit.check["command"], predicate=crit.check.get("predicate") or {"exit": 0},
                                  purpose="exit_criterion", timeout_s=timeout, ref=f"t{task.id}.{crit.id}",
                                  label=f"t{task.id} {crit.id}: {crit.text[:50]}"), owner)
        if not run.passed:
            plan = require_plan(owner)
            return plan.task(task.id).criterion(crit.id), run
        item = {"kind": "run", "ref": run.id, "justification": ""}
    else:
        raise ValueError(f"{crit.id} has no declared check: cite a passing run (evidence) or a file with a "
                         "justification.")
    with plan_lock(plan.metadata_path):
        plan = require_plan(owner)
        task = plan.task(task.id)
        crit = task.criterion(crit.id)
        item.update({"actor": actor, "at": time.time()})
        crit.evidence.append(item)
        crit.met = True
        plan.add_event(kind="criterion_met", entity="task", entity_id=task.id,
                       payload={"criterion": crit.id, **{k: item[k] for k in ("kind", "ref")}}, actor=actor)
        _commit(owner, plan)
    return crit, run


def attest_criterion(owner: Any, task_id: Any, criterion_id: str, *, note: str, actor: str) -> Criterion:
    actor = normalize_actor(actor)
    require_user(actor, "Attesting a criterion")
    if not str(note or "").strip():
        raise ValueError("An attestation needs a note.")
    plan = require_plan(owner)
    with plan_lock(plan.metadata_path):
        task = plan.task(task_id)
        crit = task.criterion(criterion_id) if task else None
        if crit is None:
            raise GateError(f"No criterion {criterion_id} on task {task_id}.", code="not_found")
        crit.evidence.append({"kind": "attested", "ref": "", "justification": str(note).strip(),
                              "actor": actor, "at": time.time()})
        crit.met = True
        plan.add_event(kind="criterion_attested", entity="task", entity_id=task.id,
                       payload={"criterion": crit.id, "note": note}, actor=actor)
        _commit(owner, plan)
        return crit


def complete_task(owner: Any, task_id: Any, *, actor: str = AGENT, override_reason: str = "") -> FeatureTask:
    actor = normalize_actor(actor)
    plan = require_plan(owner)
    _require_approved(plan)
    with plan_lock(plan.metadata_path):
        task = plan.task(task_id)
        if task is None:
            raise GateError(f"No task {task_id}.", code="not_found")
        if task.status != STATUS_IN_PROGRESS:
            raise GateError(f"t{task.id} is {task.status}; only the in-progress task completes.",
                            code="wrong_status")
        unmet = task.unmet
        if unmet:
            if not is_user(actor):
                raise GateError(f"t{task.id} has unmet criteria: "
                                + "; ".join(f"{c.id} {c.text}" for c in unmet)
                                + ". Meet each with check_criterion first.", code="criteria_unmet",
                                unmet=[c.id for c in unmet])
            if not str(override_reason or "").strip():
                raise GateError("Completing with unmet criteria needs an override reason.",
                                code="override_reason_required")
            task.override = {"actor": actor, "reason": str(override_reason).strip(), "at": time.time(),
                             "unmet": [c.id for c in unmet]}
            plan.add_event(kind="override", entity="task", entity_id=task.id,
                           payload={"reason": override_reason, "unmet": [c.id for c in unmet]}, actor=actor)
        task.status = STATUS_COMPLETED
        task.completed_at = time.time()
        task.diff_stat = diff_stat(plan.directory, task.start_tree)
        plan.current_task_id = None
        if plan.tasks_completed():
            plan.status = "review"
        plan.add_event(kind="completed", entity="task", entity_id=task.id, actor=actor)
        _commit(owner, plan)
        return task


def block_task(owner: Any, task_id: Any, *, summary: str, needs: str, actor: str = AGENT) -> FeatureTask:
    from mu.modes.blockers import blocker_state

    actor = normalize_actor(actor)
    plan = require_plan(owner)
    with plan_lock(plan.metadata_path):
        task = plan.task(task_id)
        if task is None:
            raise GateError(f"No task {task_id}.", code="not_found")
        if task.status not in (STATUS_IN_PROGRESS, STATUS_NOT_STARTED):
            raise GateError(f"t{task.id} is {task.status}.", code="wrong_status")
        task.status = STATUS_BLOCKED
        if plan.current_task_id == task.id:
            plan.current_task_id = None
        plan.add_event(kind="blocked", entity="task", entity_id=task.id, payload={"summary": summary}, actor=actor)
        _commit(owner, plan)
    blocker_state(owner).raise_blocker(summary, needs, mode="feature", ref=f"t{task.id}", actor=actor)
    return task


def reopen_task(owner: Any, task_id: Any, *, actor: str, reason: str = "") -> FeatureTask:
    actor = normalize_actor(actor)
    plan = require_plan(owner)
    with plan_lock(plan.metadata_path):
        task = plan.task(task_id)
        if task is None:
            raise GateError(f"No task {task_id}.", code="not_found")
        if task.status == STATUS_IN_PROGRESS:
            return task
        task.status = STATUS_NOT_STARTED
        task.reviewed = False
        if plan.status in ("review", "done"):
            plan.status = "executing"
        plan.completion_requested = False
        plan.add_event(kind="reopened", entity="task", entity_id=task.id, payload={"reason": reason}, actor=actor)
        _commit(owner, plan)
        return task


def archive_task(owner: Any, task_id: Any, *, actor: str) -> FeatureTask:
    actor = normalize_actor(actor)
    require_user(actor, "Archiving a task")
    plan = require_plan(owner)
    with plan_lock(plan.metadata_path):
        task = plan.task(task_id)
        if task is None:
            raise GateError(f"No task {task_id}.", code="not_found")
        if task.status == STATUS_IN_PROGRESS:
            raise GateError(f"t{task.id} is in progress.", code="wrong_status")
        task.status = STATUS_ARCHIVED
        plan.add_event(kind="archived", entity="task", entity_id=task.id, actor=actor)
        if plan.tasks_completed() and plan.status == "executing":
            plan.status = "review"
        _commit(owner, plan)
        return task


def transition(owner: Any, task_id: Any, to_status: str, *, actor: str, reason: str = "") -> FeatureTask:
    """The generic move used by the panel, mobile and ``/feature move``: every
    target goes through its gated engine call."""
    target = normalize_task_status(to_status)
    if target == STATUS_IN_PROGRESS:
        return start_task(owner, task_id, actor=actor)
    if target == STATUS_COMPLETED:
        return complete_task(owner, task_id, actor=actor, override_reason=reason)
    if target == STATUS_BLOCKED:
        return block_task(owner, task_id, summary=reason or "Blocked by the user", needs=reason or "user input",
                          actor=actor)
    if target == STATUS_ARCHIVED:
        return archive_task(owner, task_id, actor=actor)
    return reopen_task(owner, task_id, actor=actor, reason=reason)


def on_blockers_resolved(owner: Any) -> None:
    """Resume blocked tasks whose blockers the user answered."""
    from mu.modes.blockers import blocker_state

    plan = active_plan(owner)
    if plan is None:
        return
    open_refs = {b.ref for b in blocker_state(owner).open_blockers()}
    changed = False
    with plan_lock(plan.metadata_path):
        for task in plan.tasks:
            if task.status == STATUS_BLOCKED and f"t{task.id}" not in open_refs:
                task.status = STATUS_NOT_STARTED
                plan.add_event(kind="unblocked", entity="task", entity_id=task.id, actor="system")
                changed = True
        if changed:
            _commit(owner, plan)


# ----------------------------------------------------------------- review


def _last_edit_at(owner: Any) -> float:
    from mu.modes.edits import edits_state

    try:
        return edits_state(owner).last_edit_at
    except Exception:  # noqa: BLE001
        return 0.0


def passing_run_after(owner: Any, after: float, *, purpose: Optional[str] = None) -> Optional[Any]:
    from mu.modes.evidence import checks_state

    runs = [r for r in checks_state(owner).list(purpose=purpose) if r.passed and r.started_at >= after]
    return runs[-1] if runs else None


def review_task(owner: Any, task_id: Any, *, summary: str, issues: Optional[List[Dict[str, Any]]] = None,
                actor: str = AGENT) -> TaskReview:
    from mu.modes.blockers import blocker_state

    actor = normalize_actor(actor)
    plan = require_plan(owner)
    task = plan.task(task_id)
    if task is None:
        raise GateError(f"No task {task_id}.", code="not_found")
    if task.status != STATUS_COMPLETED:
        raise GateError(f"t{task.id} is {task.status}; review a completed task.", code="wrong_status")
    if not str(summary or "").strip():
        raise ValueError("A review needs a summary of what you checked against the objective and criteria.")
    stat = diff_stat(plan.directory, task.start_tree) or task.diff_stat
    if not stat and task.start_tree:
        stat = "(no changes since the task started)"
    if not stat:
        from mu.modes.edits import edits_state

        started = task.started_at or 0
        files = sorted({e["path"] for e in edits_state(owner).since(started)})
        stat = "edited: " + ", ".join(files) if files else ""
    if not stat:
        raise GateError("The task's diff is unknown (no git tree and no recorded edits); review needs it.",
                        code="diff_unknown")
    run = passing_run_after(owner, _last_edit_at(owner))
    if run is None:
        raise GateError("No passing check ran after the last edit. Run the relevant tests (bash or "
                        "run_feature_suite), then review.", code="evidence_required")
    parsed: List[Dict[str, Any]] = []
    for raw in issues or []:
        if isinstance(raw, str):
            raw = {"title": raw}
        title = str((raw or {}).get("title") or "").strip()
        if not title:
            continue
        severity = str(raw.get("severity") or "follow_up").strip().lower()
        if severity not in ISSUE_SEVERITIES:
            raise ValueError(f"issue severity must be one of {', '.join(ISSUE_SEVERITIES)}")
        parsed.append({"title": title, "severity": severity, "detail": str(raw.get("detail") or "").strip()})
    with plan_lock(plan.metadata_path):
        plan = require_plan(owner)
        task = plan.task(task.id)
        review = TaskReview(id=f"r{len(plan.review_records) + 1}", task_id=task.id, summary=str(summary).strip(),
                            issues=parsed, diff_stat=stat[:4000], check_run=run.id, actor=actor)
        plan.review_records.append(review)
        task.reviewed = True
        for issue in parsed:
            if issue["severity"] == "follow_up":
                new_id = max(t.id for t in plan.tasks) + 1
                plan.tasks.append(FeatureTask(
                    id=new_id, title=f"Follow-up: {issue['title']}"[:200], phase_id=task.phase_id,
                    objective=issue["detail"] or issue["title"], origin="review",
                    criteria=[Criterion(id="c1", text=f"Resolved: {issue['title']}")]))
                issue["task_id"] = new_id
                if plan.status == "review":
                    plan.status = "executing"
        plan.add_event(kind="reviewed", entity="task", entity_id=task.id,
                       payload={"review": review.id, "issues": len(parsed), "check_run": run.id}, actor=actor)
        _commit(owner, plan)
    for issue in parsed:
        if issue["severity"] == "blocking":
            blocker_state(owner).raise_blocker(f"Review of t{task.id}: {issue['title']}",
                                               issue["detail"] or "a decision on this review issue",
                                               mode="feature", ref=f"t{task.id}", actor=actor)
    return review


def completion_gaps(owner: Any, plan: FeaturePlan) -> List[str]:
    from mu.modes.blockers import blocker_state

    gaps: List[str] = []
    if plan.approval is None:
        gaps.append("the plan is not approved")
    live = plan.live_tasks()
    pending = [t for t in live if t.status != STATUS_COMPLETED]
    if pending:
        gaps.append("tasks not completed: " + ", ".join(f"t{t.id}" for t in pending))
    unreviewed = [t for t in live if t.status == STATUS_COMPLETED and not t.reviewed]
    if unreviewed:
        gaps.append("tasks not reviewed: " + ", ".join(f"t{t.id}" for t in unreviewed))
    refs = {f"t{t.id}" for t in plan.tasks}
    blockers = [b for b in blocker_state(owner).open_blockers() if b.ref in refs or b.mode == "feature"]
    if blockers:
        gaps.append("open blockers: " + ", ".join(b.id for b in blockers))
    if passing_run_after(owner, _last_edit_at(owner), purpose="suite") is None:
        gaps.append("no passing full-suite run after the last change (run_feature_suite)")
    return gaps


def run_suite(owner: Any, command: str, *, timeout: int = 1800, actor: str = AGENT) -> Any:
    from mu.modes.evidence import CheckSpec, run_check

    plan = require_plan(owner)
    if not str(command or "").strip():
        raise ValueError("Give the full test-suite command.")
    return run_check(CheckSpec(command=command, purpose="suite", predicate={"exit": 0}, timeout_s=timeout,
                               ref=plan.feature_id, label="Full suite"), owner)


def request_completion(owner: Any, *, actor: str = AGENT) -> Tuple[FeaturePlan, List[str]]:
    plan = require_plan(owner)
    gaps = completion_gaps(owner, plan)
    if not gaps:
        with plan_lock(plan.metadata_path):
            plan.completion_requested = True
            plan.add_event(kind="completion_requested", entity="feature", entity_id=plan.feature_id,
                           actor=normalize_actor(actor))
            _commit(owner, plan)
    return plan, gaps


def complete_feature(owner: Any, *, actor: str) -> FeaturePlan:
    actor = normalize_actor(actor)
    require_user(actor, "Marking the feature complete")
    plan = require_plan(owner)
    gaps = completion_gaps(owner, plan)
    if gaps:
        raise GateError("The feature is not ready: " + "; ".join(gaps) + ".", code="not_ready", gaps=gaps)
    suite = passing_run_after(owner, _last_edit_at(owner), purpose="suite")
    with plan_lock(plan.metadata_path):
        plan.status = "done"
        plan.completion = {"actor": actor, "at": time.time(), "suite_run": suite.id if suite else ""}
        plan.add_event(kind="feature_completed", entity="feature", entity_id=plan.feature_id, actor=actor)
        return _commit(owner, plan)


# ----------------------------------------------------------------- diff proposals


def propose_diff(owner: Any, task_id: Any, *, diff: str, rationale: str = "", actor: str = AGENT) -> DiffProposal:
    plan = require_plan(owner)
    if plan.task(task_id) is None:
        raise GateError(f"No task {task_id}.", code="not_found")
    if not str(diff or "").strip():
        raise ValueError("A diff proposal needs a diff.")
    with plan_lock(plan.metadata_path):
        proposal = DiffProposal(id=f"p{len(plan.diff_proposals) + 1}", task_id=int(str(task_id).lstrip("t")),
                                diff=str(diff), rationale=str(rationale or "").strip())
        plan.diff_proposals.append(proposal)
        plan.add_event(kind="diff_proposed", entity="task", entity_id=proposal.task_id,
                       payload={"proposal": proposal.id}, actor=normalize_actor(actor))
        _commit(owner, plan)
        return proposal


def decide_diff(owner: Any, proposal_id: str, *, decision: str, reason: str = "", actor: str) -> DiffProposal:
    actor = normalize_actor(actor)
    require_user(actor, "Deciding a diff proposal")
    if decision not in ("approved", "rejected"):
        raise ValueError("decision is approved or rejected")
    plan = require_plan(owner)
    with plan_lock(plan.metadata_path):
        proposal = next((p for p in plan.diff_proposals if p.id == proposal_id), None)
        if proposal is None:
            raise GateError(f"No proposal {proposal_id}.", code="not_found")
        proposal.status, proposal.decision_reason = decision, str(reason or "").strip()
        proposal.decided_by, proposal.decided_at = actor, time.time()
        plan.add_event(kind=f"diff_{decision}", entity="task", entity_id=proposal.task_id,
                       payload={"proposal": proposal.id, "reason": reason}, actor=actor)
        _commit(owner, plan)
        return proposal


# ----------------------------------------------------------------- views


def task_view(task: FeatureTask) -> Dict[str, Any]:
    data = asdict(task)
    data["unmet"] = [c.id for c in task.unmet]
    return data


def plan_view(plan: FeaturePlan, owner: Any = None) -> Dict[str, Any]:
    data = asdict(plan)
    data["hash"] = plan_hash(plan)
    data["approved"] = plan.approved
    data["approval_current"] = bool(plan.approval and plan.approval.get("plan_hash") == data["hash"])
    data["tasks"] = [task_view(t) for t in plan.tasks]
    current = plan.current_task()
    nxt = plan.next_task()
    data["current_task"] = current.id if current else None
    data["next_task"] = nxt.id if nxt else None
    data["counts"] = {s: sum(1 for t in plan.tasks if t.status == s) for s in TASK_STATUSES}
    data["unreviewed"] = [t.id for t in plan.live_tasks() if t.status == STATUS_COMPLETED and not t.reviewed]
    if owner is not None:
        try:
            data["completion_gaps"] = completion_gaps(owner, plan)
        except Exception:  # noqa: BLE001
            data["completion_gaps"] = []
    data["event_log"] = data["event_log"][-60:]
    return data


def summarize_feature_plan(plan: FeaturePlan) -> Dict[str, Any]:
    """Read-only summary for the terminal dashboard and runtime metrics: the
    plan view plus per-task rows (``phases``) with criteria progress."""
    view = plan_view(plan)
    rows = []
    for task in plan.tasks:
        met = sum(1 for c in task.criteria if c.met)
        rows.append({"id": task.id, "number": task.id, "title": task.title, "status": task.status,
                     "phase_id": task.phase_id, "reviewed": task.reviewed,
                     "task_counts": {"completed": met, "in_progress": 0,
                                     "not_started": len(task.criteria) - met}})
    nxt = plan.next_task()
    phase = plan.phase(nxt.phase_id) if nxt is not None else None
    view.update({
        "phases": rows,
        "phase_count": len(plan.phases_meta),
        "task_count": len(plan.live_tasks()),
        "tasks_completed": plan.tasks_completed(),
        "review_status": "completed" if plan.status == "done" else ("pending" if plan.status == "review" else plan.status),
        "next_phase": {"id": phase.id, "title": phase.title} if phase else None,
        "next_task": {"id": nxt.id, "title": nxt.title} if nxt else None,
    })
    return view


def refresh_and_persist_feature_plan(locator: str, metadata_path: Optional[str] = None) -> FeaturePlan:
    """Load a plan by metadata path (``locator`` may be a session id or a directory
    when ``metadata_path`` is given)."""
    return load_feature_plan(metadata_path or locator)


def l3_lines(plan: FeaturePlan) -> List[str]:
    lines = [f"- feature_id: {plan.feature_id}", f"- status: {plan.status}"]
    nxt = plan.next_task()
    if nxt is not None:
        phase = plan.phase(nxt.phase_id)
        if phase is not None:
            lines.append(f"- active_phase: {phase.title}")
        lines.append(f"- next_task: t{nxt.id} {nxt.title}")
    return lines


def capsule(session: Any) -> str:
    plan = active_plan(session, cached=True)
    if plan is None:
        return ("No active feature. Propose a draft with propose_feature_plan (phases → tasks with exit "
                "criteria); the user approves it before implementation.")
    live = plan.live_tasks()
    done = sum(1 for t in live if t.status == STATUS_COMPLETED)
    lines = [f"Feature {plan.feature_id} \"{plan.feature_name}\" — {plan.status}, revision {plan.revision}, "
             f"{done} of {len(live)} tasks complete."]
    if plan.status == "draft":
        lines.append("Waiting for the user to approve the plan (/feature approve or the panel). Do not implement; "
                     "revise with revise_feature_plan if asked.")
    current = plan.current_task()
    if current is not None:
        lines.append(f"Current task t{current.id}: {current.title}")
        for crit in current.criteria:
            check = f" (check: {crit.check['command'][:80]})" if crit.check else ""
            lines.append(f"  {crit.id} {'✓' if crit.met else '○'} {crit.text[:120]}{check}")
    elif plan.status in ("approved", "executing"):
        nxt = plan.next_task()
        if nxt is not None:
            lines.append(f"Next: start_task(t{nxt.id}) — {nxt.title}")
    if plan.status == "review" or [t for t in live if t.status == STATUS_COMPLETED and not t.reviewed]:
        pending = [f"t{t.id}" for t in live if t.status == STATUS_COMPLETED and not t.reviewed]
        if pending:
            lines.append("To review: " + ", ".join(pending))
    if plan.completion_requested:
        lines.append("Completion requested; waiting for the user to confirm.")
    return "\n".join(lines)


def _install() -> None:
    from mu.agent.hooks import HookResult, HookSpec, default_registry
    from mu.modes.blockers import on_blocker_event

    def _listener(owner: Any, kind: str) -> None:
        if kind == "all_resolved":
            on_blockers_resolved(owner)

    on_blocker_event(_listener)

    def _gate(ctx):
        envelope = edit_gate(ctx.session, ctx.tool_name or "") if ctx.session is not None else None
        if envelope is None:
            return None
        return HookResult(action="short_circuit", payload=envelope, data={"reason": "feature_approval"})

    default_registry.remove("feature_approval_gate")
    default_registry.add(HookSpec(name="feature_approval_gate", point="pre_tool", priority=21, handler=_gate))


def edit_gate(session: Any, tool_name: str) -> Optional[Dict[str, Any]]:
    """Before approval, the first edit of a turn in feature mode is refused with
    a warning (``feature_require_approval`` blocks every edit)."""
    from mu.modes.edits import EDIT_TOOLS

    if tool_name not in EDIT_TOOLS:
        return None
    variables = getattr(session, "variables", None) or {}
    if str(variables.get("agent_mode", "default")).lower() != "feature":
        return None
    plan = active_plan(session, cached=True)
    if plan is not None and plan.approval is not None and plan.status != "draft":
        return None
    block = bool(variables.get("feature_require_approval", False))
    turn = getattr(session, "_current_turn_start_index", None)
    key = (turn, plan.feature_id if plan else "")
    if not block and getattr(session, "_feature_gate_warned", None) == key:
        return None
    session._feature_gate_warned = key
    what = "no feature plan yet" if plan is None else f"plan {plan.feature_id} is a draft awaiting the user's approval"
    message = f"Feature mode: {what}. Propose the plan and wait for approval before editing."
    message += (" Edits stay blocked until approval (feature_require_approval is on)." if block else
                " This edit was not applied; repeat it to proceed anyway.")
    return {"ok": False, "error_code": "feature_not_approved", "message": message,
            "data": {"blocking": block}, "artifacts": [],
            "telemetry": {"tool_name": tool_name, "execution_source": "feature_approval_gate"}}


_install()


__all__ = [
    "Criterion", "DiffProposal", "FeatureEvent", "FeaturePhase", "FeaturePlan", "FeatureTask",
    "PLAN_STATUSES", "TASK_STATUSES", "TaskReview", "active_plan", "active_record", "approve",
    "archive_task", "attest_criterion", "block_task", "capsule", "check_criterion", "complete_feature",
    "complete_task", "completion_gaps", "current_tree", "decide_diff", "diff_stat", "edit_gate",
    "l3_lines", "load_feature_plan", "metadata_path_for", "normalize_task_status", "plan_hash",
    "plan_view", "propose_diff", "propose_plan", "read_feature_plan", "registry_record",
    "refresh_and_persist_feature_plan", "reopen_task", "request_completion", "summarize_feature_plan", "require_plan", "review_task", "revise_plan", "run_suite",
    "save_feature_plan", "start_task", "sync_registry", "task_view", "transition",
]
