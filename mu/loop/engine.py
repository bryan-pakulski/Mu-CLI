"""Loop mission engine (modes v2 §4.4).

The user owns the mission: ``start_mission`` (``/loop start``, the panel's
Start), the goal (``set_goal``), pause / resume / stop. The agent works it in
increments: ``record_increment`` (the ``loop_increment`` tool) replaces the old
4-line prose update and is what the timeline renders; a ``passed`` increment
needs a passing check run, a ``failed`` one opens (or links) a remediation work
item. The backlog is the session's work items, where ``done`` needs evidence.

``done_when`` criteria are stop conditions met by evidence (their declared
check, run by ``run_check``, or a linked passing run); when all are met the
mission is ``done`` and only the user can extend it. Budgets (increments, cost,
runtime, turns) move the mission to ``paused`` with a reason when exhausted.
A ``running`` mission is the only state the loop watchdog continues; a raised
blocker marks it ``blocked`` until the user answers.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from mu.modes.events import AGENT, GateError, is_user, normalize_actor, require_user
from mu.modes.evidence import CheckSpec, checks_state, run_check
from mu.modes.state import now
from mu.modes.work import work_state

from .state import BUDGET_KEYS, RESULTS, Criterion, Increment, LoopMission, loop_state


def _mission(owner: Any, *, required: bool = True) -> Optional[LoopMission]:
    mission = loop_state(owner).mission
    if mission is None and required:
        raise GateError("No loop mission. The user starts one with /loop start <goal> (or the "
                        "panel's Start).", code="no_mission")
    return mission


def _active(mission: LoopMission) -> None:
    if mission.status in ("stopped", "done"):
        raise GateError(f"The mission is {mission.status}.", code="mission_closed")


def normalize_budget(budget: Optional[Dict[str, Any]]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for key, value in (budget or {}).items():
        if key not in BUDGET_KEYS or value in (None, "", 0):
            continue
        number = float(value)
        if number <= 0:
            raise ValueError(f"budget {key} must be positive")
        out[key] = number
    return out


def _criteria(items: List[Any]) -> List[Criterion]:
    out = []
    for n, raw in enumerate(items or [], 1):
        if isinstance(raw, str):
            raw = {"text": raw}
        text = str((raw or {}).get("text") or "").strip()
        if not text:
            continue
        check = raw.get("check")
        if isinstance(check, str):
            check = {"command": check}
        if check is not None:
            if not str(check.get("command") or "").strip():
                raise ValueError(f"done_when {text!r}: check needs a command")
            check = {"command": str(check["command"]).strip(), "predicate": check.get("predicate") or {"exit": 0}}
        out.append(Criterion(id=f"c{n}", text=text, check=check))
    return out


def start_mission(owner: Any, goal: str, *, done_when: Optional[List[Any]] = None,
                  budget: Optional[Dict[str, Any]] = None, actor: str = "user:cli") -> LoopMission:
    actor = normalize_actor(actor)
    require_user(actor, "Starting a loop mission")
    goal = str(goal or "").strip()
    if not goal:
        raise ValueError("A mission needs a goal.")
    state = loop_state(owner)
    with state.lock:
        if state.mission is not None and state.mission.status not in ("stopped", "done"):
            raise GateError(f"A mission is already {state.mission.status}; stop it first "
                            "(/loop stop) or change its goal (/loop goal).", code="mission_exists")
        if state.mission is not None:
            state.past.append(state.mission.to_dict())
        state.counter += 1
        mission = LoopMission(id=f"m{state.counter}", goal=goal,
                              goal_history=[{"goal": goal, "actor": actor, "at": now()}],
                              done_when=_criteria(done_when or []), budget=normalize_budget(budget))
        state.mission = mission
        state.event("started", actor, goal)
        state.touch()
        return mission


def set_goal(owner: Any, goal: str, *, actor: str = "user:cli") -> LoopMission:
    actor = normalize_actor(actor)
    require_user(actor, "Changing the loop goal")
    goal = str(goal or "").strip()
    if not goal:
        raise ValueError("A goal cannot be empty.")
    state = loop_state(owner)
    mission = _mission(owner)
    with state.lock:
        mission.goal = goal
        mission.goal_history.append({"goal": goal, "actor": actor, "at": now()})
        state.event("goal", actor, goal)
        state.touch()
    return mission


def set_status(owner: Any, status: str, *, reason: str = "", actor: str = "user:cli") -> LoopMission:
    """Pause / resume / stop (user), or extend a done mission (resume, user)."""
    actor = normalize_actor(actor)
    state = loop_state(owner)
    mission = _mission(owner)
    with state.lock:
        if status == "running":
            require_user(actor, "Resuming the mission")
            if mission.status == "stopped":
                raise GateError("A stopped mission cannot resume; start a new one.", code="mission_closed")
            if mission.status == "done":
                # Extending a finished mission: the criteria are re-opened.
                for crit in mission.done_when:
                    crit.met = False
            exhausted = budget_exhausted(mission)
            if exhausted:
                raise GateError(f"The {exhausted} budget is used up; raise it first "
                                "(/loop budget ...).", code="budget_exhausted")
            mission.ended_at = None
        elif status in ("paused", "stopped"):
            if not is_user(actor) and actor != "system":
                raise GateError("Only the user pauses or stops a mission; raise a blocker instead.",
                                code="user_only")
            if status == "stopped":
                mission.ended_at = now()
        elif status not in ("blocked", "done"):
            raise ValueError(f"unknown mission status {status!r}")
        mission.status = status
        mission.status_reason = str(reason or "").strip()
        state.event(status, actor, mission.status_reason)
        state.touch()
    return mission


def set_budget(owner: Any, budget: Dict[str, Any], *, actor: str = "user:cli") -> LoopMission:
    actor = normalize_actor(actor)
    require_user(actor, "Changing the mission budget")
    state = loop_state(owner)
    mission = _mission(owner)
    with state.lock:
        mission.budget.update(normalize_budget(budget))
        state.event("budget", actor, str(mission.budget))
        state.touch()
    return mission


def budget_exhausted(mission: LoopMission) -> str:
    pairs = (("max_increments", "increments"), ("max_cost_usd", "cost_usd"),
             ("max_runtime_s", "runtime_s"), ("max_turns", "turns"))
    for limit, spent in pairs:
        cap = mission.budget.get(limit)
        if cap and float(mission.spent.get(spent, 0) or 0) >= float(cap):
            return spent.replace("_usd", "").replace("_s", "")
    return ""


def tightest_budget(mission: LoopMission) -> Optional[Dict[str, Any]]:
    """The budget closest to its cap, for the panel's meter."""
    best = None
    pairs = (("max_increments", "increments"), ("max_cost_usd", "cost_usd"),
             ("max_runtime_s", "runtime_s"), ("max_turns", "turns"))
    for limit, spent in pairs:
        cap = mission.budget.get(limit)
        if not cap:
            continue
        used = float(mission.spent.get(spent, 0) or 0)
        ratio = used / float(cap)
        if best is None or ratio > best["ratio"]:
            best = {"kind": spent, "used": round(used, 4), "cap": cap, "ratio": round(min(ratio, 1.0), 4)}
    return best


def _check_budget(owner: Any, mission: LoopMission) -> None:
    exhausted = budget_exhausted(mission)
    if exhausted and mission.status == "running":
        set_status(owner, "paused", reason=f"{exhausted} budget reached", actor="system")


def record_increment(owner: Any, *, objective: str, result: str, decision: str = "", next_step: str = "",
                     work_item: str = "", actions: Optional[List[str]] = None,
                     evidence: Optional[List[str]] = None, delegations: Optional[List[str]] = None,
                     actor: str = AGENT) -> Increment:
    mission = _mission(owner)
    _active(mission)
    result = str(result or "").strip().lower()
    if result not in RESULTS:
        raise ValueError(f"result must be one of {', '.join(RESULTS)}")
    if not str(objective or "").strip():
        raise ValueError("Say what this increment attempted (objective).")
    checks = checks_state(owner)
    refs = [str(e).strip() for e in evidence or [] if str(e).strip()]
    unknown = [r for r in refs if checks.get(r) is None]
    if unknown:
        raise GateError(f"Unknown check run(s): {', '.join(unknown)}.", code="not_found")
    if result == "passed" and not any(checks.require(r).passed for r in refs):
        raise GateError("A passed increment needs evidence: the id of a passing check run (r<n>). "
                        "Run the check (todo/criterion checks, or bash for tests), then record it.",
                        code="evidence_required")
    work = work_state(owner)
    item = work.get(work_item) if work_item else None
    if work_item and item is None:
        raise GateError(f"No work item {work_item!r}.", code="not_found")
    state = loop_state(owner)
    with state.lock:
        inc = Increment(n=len(mission.increments) + 1, objective=str(objective).strip(), result=result,
                        decision=str(decision or "").strip(), next=str(next_step or "").strip(),
                        work_item=item.id if item else "", actions=[str(a) for a in actions or []][:20],
                        evidence=refs, delegations=[str(d) for d in delegations or []][:20])
        if result == "failed":
            title = f"Remediate: {inc.objective[:100]}"
            existing = next((i for i in work.open_items() if i.title == title), None)
            remediation = existing or work.add(title, parent_id=item.id if item else None, mode="loop")
            inc.remediation_item = remediation.id
        mission.increments.append(inc)
        mission.spent["increments"] = len(mission.increments)
        state.event("increment", normalize_actor(actor), f"{inc.n} {result}: {inc.objective[:120]}")
        state.touch()
    _check_budget(owner, mission)
    return inc


def check_done_when(owner: Any, criterion_id: str = "", *, evidence: Optional[List[str]] = None,
                    timeout: int = 600, actor: str = AGENT) -> Dict[str, Any]:
    """Run the declared checks (or link passing runs) for one or all open criteria."""
    mission = _mission(owner)
    _active(mission)
    targets = [mission.criterion(criterion_id)] if criterion_id else [c for c in mission.done_when if not c.met]
    if criterion_id and targets[0] is None:
        raise GateError(f"No criterion {criterion_id!r}.", code="not_found")
    checks = checks_state(owner)
    refs = [str(e).strip() for e in evidence or [] if str(e).strip()]
    results = []
    for crit in targets:
        run = None
        if refs:
            runs = [checks.get(r) for r in refs]
            if any(r is None for r in runs):
                raise GateError(f"Unknown check run in {refs}.", code="not_found")
            run = next((r for r in runs if r.passed), runs[-1])
        elif crit.check:
            run = run_check(CheckSpec(command=crit.check["command"], predicate=crit.check["predicate"],
                                      purpose="exit_criterion", timeout_s=timeout, ref=crit.id,
                                      label=f"Done when: {crit.text[:60]}"), owner)
        else:
            results.append({"id": crit.id, "met": crit.met,
                            "detail": "no check declared: link a passing run (evidence) or ask the user to attest"})
            continue
        state = loop_state(owner)
        with state.lock:
            if run.id not in crit.evidence:
                crit.evidence.append(run.id)
            crit.met = bool(run.passed)
            state.touch()
        results.append({"id": crit.id, "met": crit.met, "run": run.id, "detail": run.detail})
    _maybe_done(owner, mission, actor)
    return {"criteria": results, "status": mission.status}


def attest(owner: Any, criterion_id: str, *, note: str, actor: str) -> Criterion:
    """A user marks a criterion met without a run (recorded as an attestation)."""
    actor = normalize_actor(actor)
    require_user(actor, "Attesting a done-when criterion")
    if not str(note or "").strip():
        raise ValueError("An attestation needs a note (why it is met).")
    mission = _mission(owner)
    crit = mission.criterion(criterion_id)
    if crit is None:
        raise GateError(f"No criterion {criterion_id!r}.", code="not_found")
    state = loop_state(owner)
    with state.lock:
        crit.met, crit.attested_by, crit.note = True, actor, str(note).strip()
        state.event("override", actor, f"{crit.id} attested: {note}")
        state.touch()
    _maybe_done(owner, mission, actor)
    return crit


def _maybe_done(owner: Any, mission: LoopMission, actor: str) -> None:
    if mission.done_when and all(c.met for c in mission.done_when) and mission.status in ("running", "blocked", "paused"):
        state = loop_state(owner)
        with state.lock:
            mission.status = "done"
            mission.status_reason = "every done-when criterion is met"
            mission.ended_at = now()
            state.event("done", "system", mission.status_reason)
            state.touch()


def account_turn(session: Any, *, cost_usd: float, runtime_s: float) -> None:
    """Charge one finished turn to the running mission and enforce its budgets."""
    states = getattr(getattr(session, "session_manager", None), "mode_state", None)
    state = states.peek("loop") if states is not None else None
    mission = state.mission if state is not None else None
    if mission is None or mission.status not in ("running", "blocked"):
        return
    with state.lock:
        mission.spent["turns"] = int(mission.spent.get("turns", 0) or 0) + 1
        mission.spent["cost_usd"] = round(float(mission.spent.get("cost_usd", 0) or 0) + max(0.0, cost_usd), 6)
        mission.spent["runtime_s"] = round(float(mission.spent.get("runtime_s", 0) or 0) + max(0.0, runtime_s), 2)
        state.touch()
    _check_budget(session, mission)


def on_blocker(session: Any) -> None:
    mission = _mission(session, required=False)
    if mission is not None and mission.status == "running":
        set_status(session, "blocked", reason="blocker raised", actor="system")


def on_blockers_resolved(session: Any) -> None:
    mission = _mission(session, required=False)
    if mission is not None and mission.status == "blocked":
        state = loop_state(session)
        with state.lock:
            mission.status = "running"
            mission.status_reason = ""
            state.event("running", "system", "blocker answered")
            state.touch()


def may_continue(session: Any) -> bool:
    """True when the watchdog may push the agent on (a running mission)."""
    mission = _mission(session, required=False)
    return mission is not None and mission.status == "running"


def _fmt_spent(mission: LoopMission) -> str:
    parts = []
    spent, budget = mission.spent, mission.budget
    if budget.get("max_increments"):
        parts.append(f"increments {int(spent.get('increments', 0))}/{int(budget['max_increments'])}")
    if budget.get("max_cost_usd"):
        parts.append(f"${float(spent.get('cost_usd', 0)):.2f} of ${float(budget['max_cost_usd']):.2f}")
    if budget.get("max_runtime_s"):
        left = max(0.0, float(budget["max_runtime_s"]) - float(spent.get("runtime_s", 0)))
        parts.append(f"{int(left // 60)}m left")
    if budget.get("max_turns"):
        parts.append(f"turns {int(spent.get('turns', 0))}/{int(budget['max_turns'])}")
    return ", ".join(parts) or "no budget set"


def status_text(owner: Any) -> str:
    mission = _mission(owner, required=False)
    if mission is None:
        return ("No loop mission. Ask the user to start one with /loop start <goal> (or the panel's "
                "Start) — do not assume their first message is the goal.")
    lines = [f"Mission {mission.id} [{mission.status}{': ' + mission.status_reason if mission.status_reason else ''}]: {mission.goal[:240]}",
             f"Budget: {_fmt_spent(mission)}"]
    if mission.done_when:
        lines.append("Done when: " + "; ".join(
            f"{c.id} {'✓' if c.met else '○'} {c.text[:80]}" for c in mission.done_when))
    if mission.increments:
        last = mission.increments[-1]
        lines.append(f"Last increment {last.n} [{last.result}] {last.objective[:100]}"
                     + (f" → next: {last.next[:100]}" if last.next else ""))
    work = work_state(owner)
    current = work.current()
    open_items = work.open_items()
    lines.append(f"Backlog: {len(open_items)} open" + (f"; in progress {current.id} {current.title[:80]}" if current else ""))
    if mission.status == "paused":
        lines.append("The mission is paused: answer the user, but do not continue mission work until they resume it.")
    elif mission.status in ("done", "stopped"):
        lines.append(f"The mission is {mission.status}; only the user can extend it.")
    return "\n".join(lines)


def capsule(session: Any) -> str:
    return status_text(session)


__all__ = [
    "account_turn", "attest", "budget_exhausted", "capsule", "check_done_when", "may_continue",
    "on_blocker", "on_blockers_resolved", "record_increment", "set_budget", "set_goal", "set_status",
    "start_mission", "status_text", "tightest_budget",
]


def _blocker_listener(owner: Any, kind: str) -> None:
    if kind == "raised":
        on_blocker(owner)
    elif kind == "all_resolved":
        on_blockers_resolved(owner)


def _register() -> None:
    from mu.modes.blockers import on_blocker_event

    on_blocker_event(_blocker_listener)


_register()
