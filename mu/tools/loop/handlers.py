"""Loop-mode tools over the mission engine (``mu.loop.engine``)."""

from __future__ import annotations

from typing import Any, Dict

from mu.loop import engine
from mu.loop.state import RESULTS, loop_state
from mu.modes.tooling import envelope, guarded, owner
from mu.tools import tool


@tool(
    name="loop_increment",
    description=(
        "Record one loop increment (this is the timeline entry, replacing a prose update): the "
        "objective, the result, the evidence (passing check runs r<n> for 'passed'), your decision "
        "and the next step. A failed increment opens a remediation task."
    ),
    parameters={
        "type": "object",
        "properties": {
            "objective": {"type": "string", "description": "What this increment attempted."},
            "result": {"type": "string", "enum": list(RESULTS)},
            "evidence": {"type": "array", "items": {"type": "string"}, "description": "Check run ids (r<n>)."},
            "work_item": {"type": "string", "description": "The backlog item worked (w<n> or its number)."},
            "actions": {"type": "array", "items": {"type": "string"}, "description": "Key actions taken, short."},
            "decision": {"type": "string", "description": "What you concluded."},
            "next": {"type": "string", "description": "The next increment."},
            "delegations": {"type": "array", "items": {"type": "string"}, "description": "Sub-agents spawned for this increment."},
        },
        "required": ["objective", "result"],
    },
    requires_approval=False, phase="loop", execution_kind="memory", preview_policy="none",
    server_policy="session_only",
)
@guarded("loop_increment")
def loop_increment(args: Dict[str, Any], context) -> Dict[str, Any]:
    work_item = args.get("work_item")
    inc = engine.record_increment(
        owner(context), objective=str(args.get("objective") or ""), result=str(args.get("result") or ""),
        decision=str(args.get("decision") or ""), next_step=str(args.get("next") or ""),
        work_item=str(work_item) if work_item not in (None, "") else "",
        actions=args.get("actions") or [], evidence=args.get("evidence") or [],
        delegations=args.get("delegations") or [])
    mission = loop_state(owner(context)).mission
    message = f"Increment {inc.n} recorded ({inc.result})."
    if inc.remediation_item:
        message += f" Remediation task {inc.remediation_item} is open."
    if mission.status != "running":
        message += f" The mission is now {mission.status}: {mission.status_reason}. Stop and report."
    return envelope("loop_increment", True, message,
                    {"n": inc.n, "result": inc.result, "mission_status": mission.status,
                     "remediation_item": inc.remediation_item})


@tool(
    name="loop_status",
    description="The loop mission: goal, status, budget left, done-when criteria, last increments, backlog.",
    parameters={"type": "object", "properties": {}},
    requires_approval=False, phase="loop", execution_kind="read", preview_policy="none",
    server_policy="session_only",
)
@guarded("loop_status")
def loop_status(args: Dict[str, Any], context) -> Dict[str, Any]:
    state = loop_state(owner(context))
    mission = state.mission
    data = mission.to_dict() if mission else {}
    if mission:
        data["increments"] = data["increments"][-5:]
        data["tightest_budget"] = engine.tightest_budget(mission)
    return envelope("loop_status", True, engine.status_text(owner(context)), data)


@tool(
    name="loop_check_done",
    description=(
        "Check the mission's done-when criteria: runs each open criterion's declared check, or links "
        "passing runs you cite as evidence. When every criterion is met the mission is done."
    ),
    parameters={
        "type": "object",
        "properties": {
            "criterion": {"type": "string", "description": "One criterion id (c<n>); omit for all open ones."},
            "evidence": {"type": "array", "items": {"type": "string"}, "description": "Passing check run ids instead of the declared check."},
        },
    },
    requires_approval=True, phase="loop", execution_kind="mutate", preview_policy="optional",
    server_policy="session_only",
)
@guarded("loop_check_done")
def loop_check_done(args: Dict[str, Any], context) -> Dict[str, Any]:
    result = engine.check_done_when(owner(context), str(args.get("criterion") or ""),
                                    evidence=args.get("evidence") or [])
    met = sum(1 for c in result["criteria"] if c["met"])
    message = f"{met}/{len(result['criteria'])} checked criteria met; mission {result['status']}."
    return envelope("loop_check_done", True, message, result)


from mu.modes.preview import register_preview  # noqa: E402


@register_preview("loop_check_done")
def _preview(args, session):
    states = getattr(getattr(session, "session_manager", None), "mode_state", None)
    state = states.peek("loop") if states is not None else None
    mission = state.mission if state is not None else None
    if mission is None or args.get("evidence"):
        return {}
    wanted = str(args.get("criterion") or "")
    return {"will_run": [c.check["command"] for c in mission.done_when
                         if c.check and not c.met and (not wanted or c.id == wanted)]}
