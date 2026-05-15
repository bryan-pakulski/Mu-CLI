"""Feature-mode slash command: /feature <subcommand>.

Subcommands:
    /feature                              — list session features (alias of /feature list)
    /feature list                         — list session features
    /feature new <name>                   — create a feature record
    /feature load <id>                    — activate an existing feature
    /feature delete <id>                  — delete a feature record
    /feature exit | unload                — clear active feature without deleting
    /feature status | phases [<id>]       — show one feature's markdown view
    /feature help                         — workflow help text
    /feature create plan <name>           — staged-engine: create the plan shell
    /feature create phase <title> | <goal>
    /feature create task <phase_id> | <title> | <overview> | <exit1;exit2>
    /feature show <board|execution|reviews>
    /feature move <task_id> <status>
    /feature block <task_id> <reason>
    /feature review auto | /feature review <task_id> <summary>
    /feature archive <task_id>
    /feature monitor [refresh_seconds] [iterations|continuous]
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import CommandResult, command


# ----------------------------------------------------------------- helpers


def _ui(session: Any):
    return getattr(session, "ui", None)


def _console(session: Any):
    ui = _ui(session)
    return getattr(ui, "console", None) if ui is not None else None


def _refresh_hud(session: Any) -> None:
    try:
        from mucli import refresh_memory_hud

        refresh_memory_hud(session, _ui(session))
    except ImportError:
        pass


def _print_markdown(session: Any, markdown: str, allow_prompt: bool) -> None:
    if not allow_prompt:
        return
    console = _console(session)
    if console is None:
        return
    try:
        from rich.markdown import Markdown

        console.print(Markdown(markdown))
    except Exception:
        try:
            console.print(markdown, markup=False)
        except Exception:
            pass


def _mucli_helpers() -> Dict[str, Callable]:
    """Lazy-import the feature helpers from mucli — they touch globals
    (session_manager state, citation registry) so they live there."""
    try:
        import mucli

        return {
            "default_dir": getattr(mucli, "_default_feature_directory", lambda *a, **k: None),
            "build_md": getattr(mucli, "build_feature_markdown", lambda *a, **k: ""),
            "refresh": getattr(mucli, "refresh_feature_record", lambda *a, **k: None),
            "prompt_logged": getattr(mucli, "_feature_prompt_with_logging", None),
            "confirm_loop": getattr(mucli, "_feature_confirm_deny_edit_loop", None),
            "execute_tool": getattr(mucli, "_execute_feature_tool", None),
            "monitor_line": getattr(mucli, "_monitor_compact_line", lambda payload: str(payload)),
        }
    except ImportError:
        return {
            "default_dir": lambda *a, **k: None,
            "build_md": lambda *a, **k: "",
            "refresh": lambda *a, **k: None,
            "prompt_logged": None,
            "confirm_loop": None,
            "execute_tool": None,
            "monitor_line": lambda payload: str(payload),
        }


def _err(message: str, data: Optional[Dict] = None) -> CommandResult:
    return CommandResult(ok=False, message=message, data=data or {})


# ----------------------------------------------------------------- subcommands


def _exit(session: Any, allow_prompt: bool) -> CommandResult:
    if not isinstance(session.session_manager.get_feature_state(), dict):
        return _err("No active feature to exit.")
    session.session_manager.clear_feature_state(session.folder_context)
    session.sync_runtime_state()
    _refresh_hud(session)
    return CommandResult(
        ok=True,
        message="Exited active feature context.",
        data={
            "active_feature_id": session.session_manager.active_feature_id,
            "feature": session.session_manager.get_feature_state(),
        },
    )


def _new(session: Any, name: str, allow_prompt: bool) -> CommandResult:
    if not name:
        return _err("Usage: /feature new <feature_name>")
    helpers = _mucli_helpers()
    record = session.session_manager.create_feature_record(
        name,
        directory=helpers["default_dir"](session, name),
        feature_request=name,
    )
    session.sync_runtime_state()
    markdown = helpers["build_md"](
        {**record, "token_total": session.session_manager.token_counts.get("total", 0)}
    )
    _print_markdown(session, markdown, allow_prompt)
    _refresh_hud(session)
    return CommandResult(
        ok=True,
        message=f"Created feature: {record['feature_id']}",
        data={
            "feature": record,
            "markdown": markdown,
            "features": session.session_manager.list_features(),
        },
    )


def _list(session: Any, allow_prompt: bool) -> CommandResult:
    helpers = _mucli_helpers()
    features = [
        helpers["refresh"](session, feature["feature_id"]) or feature
        for feature in session.session_manager.list_features()
    ]
    if allow_prompt:
        console = _console(session)
        if console is not None:
            try:
                from rich import box
                from rich.table import Table

                table = Table(title="Session Features", box=box.ROUNDED)
                table.add_column("ID", style="cyan", no_wrap=True)
                table.add_column("Current", style="yellow", justify="center")
                table.add_column("Status", style="green")
                table.add_column("Name", style="white")
                table.add_column("Directory", style="magenta")
                if features:
                    for f in features:
                        table.add_row(
                            f.get("feature_id", ""),
                            "*" if f.get("feature_id") == session.session_manager.active_feature_id else "",
                            f.get("status", "unknown"),
                            f.get("feature_name", ""),
                            f.get("directory", ""),
                        )
                else:
                    table.add_row("-", "", "none", "No features saved", "")
                console.print(table)
            except Exception:
                pass
    return CommandResult(
        ok=True,
        message="Listed session features.",
        data={
            "features": features,
            "active_feature_id": session.session_manager.active_feature_id,
        },
    )


def _load(session: Any, feature_id: str, allow_prompt: bool) -> CommandResult:
    if not feature_id:
        return _err("Usage: /feature load <feature_id>")
    helpers = _mucli_helpers()
    record = helpers["refresh"](session, feature_id)
    if not isinstance(record, dict):
        return _err(f"Feature '{feature_id}' not found.")
    activated = session.session_manager.activate_feature(record["feature_id"])
    session.sync_runtime_state()
    markdown = helpers["build_md"](
        {**activated, "token_total": session.session_manager.token_counts.get("total", 0)}
    )
    _print_markdown(session, markdown, allow_prompt)
    _refresh_hud(session)
    return CommandResult(
        ok=True,
        message=f"Loaded feature: {record['feature_id']}",
        data={"feature": activated, "markdown": markdown},
    )


def _delete(session: Any, feature_id: str, allow_prompt: bool) -> CommandResult:
    if not feature_id:
        return _err("Usage: /feature delete <feature_id>")
    deleted = session.session_manager.delete_feature(feature_id)
    session.sync_runtime_state()
    _refresh_hud(session)
    if not isinstance(deleted, dict):
        return _err(f"Feature '{feature_id}' not found.")
    return CommandResult(
        ok=True,
        message=f"Deleted feature: {deleted['feature_id']}",
        data={"deleted_feature": deleted},
    )


_HELP_TEXT = (
    "Feature workflow commands:\n"
    "- /feature create plan <name>\n"
    "- /feature create phase <title> | <goal>\n"
    "- /feature create task <phase_id> | <title> | <overview> | <exit1;exit2>\n"
    "- /feature show <board|execution|reviews>\n"
    "- /feature move <task_id> <status>\n"
    "- /feature block <task_id> <reason>\n"
    "- /feature review auto\n"
    "- /feature review <task_id> <summary>\n"
    "- /feature archive <task_id>\n"
    "- /feature monitor [refresh_seconds] [iterations|continuous]\n"
    "\nExamples:\n"
    "- /feature create plan Checkout Cleanup\n"
    "- /feature create phase API Hardening | Reduce flaky retries\n"
    "- /feature create task 1 | Validate headers | Add strict parsing | Unit tests;Docs updated\n"
    "- /feature move 1 in_progress\n"
    "- /feature block 1 waiting_for_user_input\n"
    "- /feature show board\n"
    "- /feature monitor 1.0 10\n"
)


def _help(session: Any, allow_prompt: bool) -> CommandResult:
    if allow_prompt:
        console = _console(session)
        if console is not None:
            try:
                from rich.panel import Panel

                console.print(Panel(_HELP_TEXT, title="Feature Command Help", border_style="cyan"))
            except Exception:
                pass
    return CommandResult(ok=True, message="Rendered feature help.", data={"usage": _HELP_TEXT})


def _create(session: Any, payload: str, allow_prompt: bool) -> CommandResult:
    parts = payload.split(" ", 1) if payload else []
    if len(parts) < 2:
        return _err("Usage: /feature create <plan|phase|task> <args>")
    kind = parts[0].lower().strip()
    body = parts[1].strip()
    helpers = _mucli_helpers()
    exec_tool = helpers["execute_tool"]
    prompt_logged = helpers["prompt_logged"]
    confirm_loop = helpers["confirm_loop"]

    if exec_tool is None or prompt_logged is None or confirm_loop is None:
        return _err("Feature engine helpers unavailable (mucli not loaded).")

    if kind == "plan":
        mode_choice = prompt_logged(
            session,
            question="Select planning style",
            options=[
                ("balanced", "Balanced (Recommended): detail + speed"),
                ("fast", "Fast: minimal planning, rapid execution"),
                ("thorough", "Thorough: deep planning before coding"),
            ],
            prompt_id="plan_style",
            allow_prompt=allow_prompt,
            context={"feature_name": body},
        )
        confirm = confirm_loop(
            session,
            label="plan request",
            value=body,
            allow_prompt=allow_prompt,
            context={"kind": "plan_create"},
        )
        if confirm["decision"] == "deny":
            return _err("Plan creation cancelled. Re-run with a revised name.")
        resp = exec_tool(
            session,
            "create_feature",
            {
                "feature_name": confirm["value"],
                "feature_request": confirm["value"],
                "design_plan": f"cli_planning_style={mode_choice}",
            },
        )
        ok = bool(resp.get("ok"))
        return CommandResult(
            ok=ok,
            message="Created feature plan shell." if ok else str(resp.get("error", resp)),
            data=resp,
        )

    if kind == "phase":
        phase_parts = [p.strip() for p in body.split("|", 1)]
        if len(phase_parts) != 2:
            return _err("Usage: /feature create phase <title> | <goal>")
        confirm = confirm_loop(
            session,
            label="phase title",
            value=phase_parts[0],
            allow_prompt=allow_prompt,
            context={"kind": "phase_create"},
        )
        if confirm["decision"] == "deny":
            return _err("Phase creation cancelled. Re-run with updated title/goal.")
        state = session.session_manager.get_feature_state() or {}
        plan = (state.get("feature_plan") if isinstance(state, dict) else {}) or {}
        existing = list(plan.get("phases_meta", []))
        next_id = len(existing) + 1
        existing.append({"id": next_id, "title": confirm["value"], "goal": phase_parts[1], "order": next_id})
        resp = exec_tool(session, "create_phases", {"phases": existing, "replace_existing": True})
        ok = bool(resp.get("ok"))
        return CommandResult(
            ok=ok,
            message="Phase created." if ok else str(resp.get("error", resp)),
            data=resp,
        )

    if kind == "task":
        task_parts = [p.strip() for p in body.split("|")]
        if len(task_parts) != 4:
            return _err(
                "Usage: /feature create task <phase_id> | <title> | <overview> | <exit1;exit2>"
            )
        exit_criteria = [c.strip() for c in task_parts[3].split(";") if c.strip()]
        confirm = confirm_loop(
            session,
            label="task title",
            value=task_parts[1],
            allow_prompt=allow_prompt,
            context={"kind": "task_create", "phase_id": task_parts[0]},
        )
        if confirm["decision"] == "deny":
            return _err("Task creation cancelled. Re-run with an updated task payload.")
        resp = exec_tool(
            session,
            "create_task",
            {
                "phase_id": int(task_parts[0]),
                "title": confirm["value"],
                "overview": task_parts[2],
                "exit_criteria": exit_criteria,
            },
        )
        ok = bool(resp.get("ok"))
        return CommandResult(
            ok=ok,
            message="Task created." if ok else str(resp.get("error", resp)),
            data=resp,
        )

    return _err("Unknown create target. Use plan|phase|task.")


def _show(session: Any, view: str, allow_prompt: bool) -> CommandResult:
    view = (view or "board").strip().lower()
    helpers = _mucli_helpers()
    feature = helpers["refresh"](session, None)
    if not isinstance(feature, dict):
        return _err("No feature selected.")
    plan = feature.get("feature_plan", {}) if isinstance(feature.get("feature_plan"), dict) else {}

    if view == "execution":
        exec_tool = helpers["execute_tool"]
        if exec_tool is None:
            return _err("Feature engine helpers unavailable.")
        payload = exec_tool(session, "get_execution_state", {})
        return CommandResult(ok=True, message="Rendered execution view.", data=payload)
    if view == "reviews":
        return CommandResult(
            ok=True,
            message="Rendered review summaries.",
            data={
                "review_summaries": plan.get("review_summaries", []),
                "review_count": plan.get("review_count", 0),
            },
        )
    if view == "board":
        return CommandResult(
            ok=True,
            message="Rendered board snapshot.",
            data={
                "active_tasks": plan.get("active_tasks", []),
                "execution": plan.get("execution", {}),
            },
        )
    return _err("Unknown show target. Use board|execution|reviews.")


def _move(session: Any, rest: str, allow_prompt: bool) -> CommandResult:
    parts = rest.split(" ", 1)
    if len(parts) != 2:
        return _err("Usage: /feature move <task_id> <status>")
    helpers = _mucli_helpers()
    exec_tool = helpers["execute_tool"]
    if exec_tool is None:
        return _err("Feature engine helpers unavailable.")
    task_id = int(parts[0])
    status = parts[1].strip()
    args = {"task_id": task_id, "status": status}
    if status == "completed":
        feature = helpers["refresh"](session, None) or {}
        plan = feature.get("feature_plan", {}) if isinstance(feature, dict) else {}
        criteria: List[str] = []
        for task in plan.get("phases", []):
            if int(task.get("id", -1)) == task_id:
                criteria = list(task.get("exit_criteria", []))
                break
        args["verified_exit_criteria"] = criteria
    resp = exec_tool(session, "update_task_status", args)
    return CommandResult(
        ok=bool(resp.get("ok")),
        message="Task moved." if resp.get("ok") else str(resp.get("error", resp)),
        data=resp,
    )


def _block(session: Any, rest: str, allow_prompt: bool) -> CommandResult:
    parts = rest.split(" ", 1)
    if len(parts) != 2:
        return _err("Usage: /feature block <task_id> <reason>")
    helpers = _mucli_helpers()
    exec_tool = helpers["execute_tool"]
    if exec_tool is None:
        return _err("Feature engine helpers unavailable.")
    resp = exec_tool(session, "block_task", {"task_id": int(parts[0]), "reason": parts[1]})
    return CommandResult(
        ok=bool(resp.get("ok")),
        message="Task blocked." if resp.get("ok") else str(resp.get("error", resp)),
        data=resp,
    )


def _review(session: Any, rest: str, allow_prompt: bool) -> CommandResult:
    helpers = _mucli_helpers()
    exec_tool = helpers["execute_tool"]
    if exec_tool is None:
        return _err("Feature engine helpers unavailable.")
    if (rest or "").strip().lower() == "auto":
        resp = exec_tool(session, "review_all_completed_tasks", {})
        return CommandResult(
            ok=bool(resp.get("ok")),
            message="Auto-review completed tasks." if resp.get("ok") else str(resp.get("error", resp)),
            data=resp,
        )
    parts = rest.split(" ", 1)
    if len(parts) != 2:
        return _err(
            "Usage: /feature review <task_id> <summary> OR /feature review auto"
        )
    resp = exec_tool(
        session,
        "review_completed_tasks",
        {"task_id": int(parts[0]), "summary": parts[1], "issues": []},
    )
    return CommandResult(
        ok=bool(resp.get("ok")),
        message="Review recorded." if resp.get("ok") else str(resp.get("error", resp)),
        data=resp,
    )


def _archive(session: Any, rest: str, allow_prompt: bool) -> CommandResult:
    if not rest:
        return _err("Usage: /feature archive <task_id>")
    helpers = _mucli_helpers()
    exec_tool = helpers["execute_tool"]
    if exec_tool is None:
        return _err("Feature engine helpers unavailable.")
    resp = exec_tool(session, "archive_task", {"task_id": int(rest)})
    return CommandResult(
        ok=bool(resp.get("ok")),
        message="Task archived." if resp.get("ok") else str(resp.get("error", resp)),
        data=resp,
    )


def _monitor(session: Any, rest: str, allow_prompt: bool) -> CommandResult:
    refresh_seconds = 2.0
    mode_arg = ""
    if rest:
        try:
            refresh_seconds = max(0.5, float(rest))
        except ValueError:
            pieces = rest.split()
            try:
                refresh_seconds = max(0.5, float(pieces[0]))
            except (ValueError, IndexError):
                return _err(
                    "Usage: /feature monitor [refresh_seconds] [iterations|continuous]"
                )
            mode_arg = pieces[1].strip().lower() if len(pieces) > 1 else ""

    if not allow_prompt:
        iterations: Optional[int] = 1
    elif mode_arg == "continuous":
        iterations = None
    elif mode_arg:
        try:
            iterations = max(1, int(mode_arg))
        except ValueError:
            return _err(
                "Usage: /feature monitor [refresh_seconds] [iterations|continuous]"
            )
    else:
        iterations = 5

    helpers = _mucli_helpers()
    exec_tool = helpers["execute_tool"]
    monitor_line = helpers["monitor_line"]
    if exec_tool is None:
        return _err("Feature engine helpers unavailable.")

    snapshots: List[Dict[str, Any]] = []
    last_line: Optional[str] = None
    tick = 0
    console = _console(session)
    try:
        while iterations is None or tick < iterations:
            payload = exec_tool(session, "get_execution_state", {})
            snapshots.append(payload)
            if allow_prompt and console is not None:
                line = monitor_line(payload)
                if line != last_line:
                    try:
                        from utils.helpers import safe_markup as _esc

                        console.print(f"[cyan]{_esc(line)}[/cyan]")
                    except Exception:
                        pass
                    last_line = line
            tick += 1
            if allow_prompt and (iterations is None or tick < iterations):
                time.sleep(refresh_seconds)
    except KeyboardInterrupt:
        if allow_prompt and console is not None:
            try:
                console.print("[yellow]Monitor stopped by user.[/yellow]")
            except Exception:
                pass

    return CommandResult(
        ok=True,
        message="Rendered feature monitor.",
        data={
            "snapshots": snapshots,
            "refresh_seconds": refresh_seconds,
            "mode": mode_arg or "fixed",
            "iterations": iterations if iterations is not None else "continuous",
        },
    )


def _status_or_phases(session: Any, sub: str, rest: str, allow_prompt: bool) -> CommandResult:
    helpers = _mucli_helpers()
    feature = helpers["refresh"](session, rest or None)
    if not isinstance(feature, dict):
        return _err("No feature selected.")
    markdown = helpers["build_md"](
        {**feature, "token_total": session.session_manager.token_counts.get("total", 0)},
        include_phases=(sub == "phases"),
    )
    _print_markdown(session, markdown, allow_prompt)
    return CommandResult(
        ok=True,
        message=f"Rendered feature {sub}.",
        data={"feature": feature, "markdown": markdown},
    )


# ----------------------------------------------------------------- dispatch


@command(
    "/feature",
    help="Feature workflows: list/new/load/delete/exit, status/phases, create, show, move, block, review, archive, monitor, help.",
)
def feature_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    raw = (args or "").strip()
    if not raw:
        return _list(session, allow_prompt)

    head, _, rest = raw.partition(" ")
    sub = head.lower()
    rest = rest.strip()

    if sub in ("exit", "unload"):
        return _exit(session, allow_prompt)
    if sub == "new":
        return _new(session, rest, allow_prompt)
    if sub == "list":
        return _list(session, allow_prompt)
    if sub == "load":
        return _load(session, rest, allow_prompt)
    if sub == "delete":
        return _delete(session, rest, allow_prompt)
    if sub == "help":
        return _help(session, allow_prompt)
    if sub == "create":
        return _create(session, rest, allow_prompt)
    if sub == "show":
        return _show(session, rest, allow_prompt)
    if sub == "move":
        return _move(session, rest, allow_prompt)
    if sub == "block":
        return _block(session, rest, allow_prompt)
    if sub == "review":
        return _review(session, rest, allow_prompt)
    if sub == "archive":
        return _archive(session, rest, allow_prompt)
    if sub == "monitor":
        return _monitor(session, rest, allow_prompt)
    if sub in ("status", "phases"):
        return _status_or_phases(session, sub, rest, allow_prompt)

    return CommandResult(
        ok=False,
        message=f"Unknown feature command: {sub}. Use '/feature help' for guidance.",
    )
