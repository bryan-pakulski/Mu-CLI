"""``/feature`` — feature plans (``mu.feature.engine``). You approve; the agent executes.

    /feature                          — list session features (alias of /feature list)
    /feature list | new <name> | load <id> | delete <id> | exit
    /feature status | phases [<id>]   — the plan: approval, tasks, criteria and evidence
    /feature approve                  — approve the draft plan (records you and the plan hash)
    /feature start <task>             — start a task (one in progress at a time)
    /feature move <task> <status> [reason]  — the same gated move as the panel
    /feature attest <task> <criterion> <why> — mark a criterion met without a run (recorded)
    /feature done <task> <reason>     — complete a task with unmet criteria (override, recorded)
    /feature diff <proposal> approve|reject [reason]
    /feature review                   — what still blocks completion
    /feature complete                 — mark the feature complete (preconditions enforced)
"""

from __future__ import annotations

from typing import Any

from mu.feature import engine
from mu.modes.events import GateError

from . import CommandResult, command
from ._mode_util import reply

_ACTOR = "user:cli"


def _print_markdown(session: Any, markdown: str, allow_prompt: bool) -> None:
    ui = getattr(session, "ui", None)
    console = getattr(ui, "console", None) if ui is not None else None
    if not allow_prompt:
        return
    if console is not None:
        try:
            from rich.markdown import Markdown

            console.print(Markdown(markdown))
            return
        except Exception:
            pass
    if ui is not None and hasattr(ui, "show_info"):
        ui.show_info(markdown)


def _refresh_hud(session: Any) -> None:
    try:
        from mucli import refresh_memory_hud

        refresh_memory_hud(session, getattr(session, "ui", None))
    except ImportError:
        pass


def _list(session: Any, allow_prompt: bool) -> CommandResult:
    manager = session.session_manager
    features = manager.list_features()
    lines = [f"{'*' if f.get('feature_id') == manager.active_feature_id else ' '} {f.get('feature_id')} "
             f"[{f.get('status', '?')}{', archived' if f.get('archived') else ''}] {f.get('feature_name', '')} "
             f"({f.get('completed_count', 0)}/{f.get('task_count', 0)})" for f in features] or ["No features saved."]
    return reply(session, "\n".join(lines), allow_prompt,
                 data={"features": features, "active_feature_id": manager.active_feature_id})


def _status(session: Any, feature_id: str, include_phases: bool, allow_prompt: bool) -> CommandResult:
    from mu.cli.feature import build_feature_markdown, refresh_feature_record

    record = refresh_feature_record(session, feature_id or None)
    if not isinstance(record, dict):
        return reply(session, "No feature selected.", allow_prompt, ok=False)
    markdown = build_feature_markdown(record, include_phases=include_phases)
    _print_markdown(session, markdown, allow_prompt)
    return CommandResult(ok=True, message=markdown, data={"feature": record, "markdown": markdown})


def _queue_briefing(session: Any) -> None:
    if not hasattr(session, "queue_resumption_briefing"):
        return
    plan = engine.active_plan(session, cached=True)
    if plan is None:
        return
    session.queue_resumption_briefing(
        f"You resumed feature plan {plan.feature_id} ({plan.feature_name!r}), status {plan.status}. "
        "The mode state below shows the current task and its criteria; call feature_status for the full "
        "plan. Do not re-plan from scratch.")


@command(
    "/feature",
    help="Feature plans: list/new/load/delete/exit, status/phases, approve, start, move, attest, done, diff, review, complete.",
)
def feature_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    raw = (args or "").strip()
    head, _, rest = raw.partition(" ")
    sub = head.lower() or "list"
    rest = rest.strip()
    manager = session.session_manager
    try:
        if sub == "list":
            return _list(session, allow_prompt)
        if sub == "new":
            if not rest:
                return reply(session, "Usage: /feature new <name>", allow_prompt, ok=False)
            from mu.cli.feature import _default_feature_directory

            record = manager.create_feature_record(rest, directory=_default_feature_directory(session, rest),
                                                   feature_request=rest)
            _refresh_hud(session)
            return reply(session, f"Created draft feature {record['feature_id']}. Ask the agent to propose its plan.",
                         allow_prompt, data={"feature": record})
        if sub == "load":
            record = manager.get_feature(rest)
            if not isinstance(record, dict):
                return reply(session, f"Feature '{rest}' not found.", allow_prompt, ok=False)
            manager.activate_feature(record["feature_id"])
            _queue_briefing(session)
            _refresh_hud(session)
            return _status(session, record["feature_id"], True, allow_prompt)
        if sub == "delete":
            deleted = manager.delete_feature(rest)
            if not isinstance(deleted, dict):
                return reply(session, f"Feature '{rest}' not found.", allow_prompt, ok=False)
            return reply(session, f"Deleted feature {deleted['feature_id']}.", allow_prompt)
        if sub in ("exit", "unload"):
            if not isinstance(manager.get_feature_state(), dict):
                return reply(session, "No active feature to exit.", allow_prompt, ok=False)
            manager.clear_feature_state(session.folder_context)
            _refresh_hud(session)
            return reply(session, "Exited the active feature.", allow_prompt)
        if sub in ("status", "phases", "show"):
            return _status(session, rest, True, allow_prompt)
        if sub == "approve":
            plan = engine.approve(session, actor=_ACTOR)
            return reply(session, f"Approved {plan.feature_id} (revision {plan.revision}, hash "
                         f"{plan.approval['plan_hash']}).", allow_prompt)
        if sub == "start" and rest:
            task = engine.start_task(session, rest, actor=_ACTOR)
            return reply(session, f"t{task.id} in progress: {task.title}", allow_prompt)
        if sub == "move":
            parts = rest.split(None, 2)
            if len(parts) < 2:
                return reply(session, "Usage: /feature move <task> <status> [reason]", allow_prompt, ok=False)
            task = engine.transition(session, parts[0], parts[1], actor=_ACTOR,
                                     reason=parts[2] if len(parts) > 2 else "")
            return reply(session, f"t{task.id} is {task.status}.", allow_prompt)
        if sub == "attest":
            parts = rest.split(None, 2)
            if len(parts) < 3:
                return reply(session, "Usage: /feature attest <task> <criterion> <why>", allow_prompt, ok=False)
            crit = engine.attest_criterion(session, parts[0], parts[1], note=parts[2], actor=_ACTOR)
            return reply(session, f"{crit.id} marked met (attested).", allow_prompt)
        if sub == "done":
            parts = rest.split(None, 1)
            if len(parts) < 2:
                return reply(session, "Usage: /feature done <task> <override reason>", allow_prompt, ok=False)
            task = engine.complete_task(session, parts[0], actor=_ACTOR, override_reason=parts[1])
            return reply(session, f"t{task.id} completed" + (" (override recorded)." if task.override else "."),
                         allow_prompt)
        if sub == "diff":
            parts = rest.split(None, 2)
            if len(parts) < 2 or parts[1] not in ("approve", "reject"):
                return reply(session, "Usage: /feature diff <proposal> approve|reject [reason]", allow_prompt, ok=False)
            proposal = engine.decide_diff(session, parts[0], decision={"approve": "approved", "reject": "rejected"}[parts[1]],
                                          reason=parts[2] if len(parts) > 2 else "", actor=_ACTOR)
            return reply(session, f"Proposal {proposal.id} {proposal.status}.", allow_prompt)
        if sub == "review":
            plan = engine.require_plan(session)
            gaps = engine.completion_gaps(session, plan)
            body = ("Ready to complete: /feature complete." if not gaps
                    else "Before completion:\n" + "\n".join(f"- {g}" for g in gaps))
            return reply(session, body, allow_prompt, data={"gaps": gaps})
        if sub == "complete":
            plan = engine.complete_feature(session, actor=_ACTOR)
            return reply(session, f"Feature {plan.feature_id} is done and archived.", allow_prompt)
        if sub == "help":
            return reply(session, __doc__.strip(), allow_prompt)
    except GateError as exc:
        return reply(session, str(exc), allow_prompt, ok=False, data={"code": exc.code})
    except (ValueError, KeyError) as exc:
        return reply(session, str(exc), allow_prompt, ok=False)
    return reply(session, f"Unknown feature command: {sub}. /feature help lists them.", allow_prompt, ok=False)
