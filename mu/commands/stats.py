"""Mode-toggle and stats slash commands: /thinking, /agentic, /yolo, /stats."""

from typing import Any

from . import CommandResult, command


def _emit_toggle_banner(session: Any, label: str, state: bool) -> None:
    """Print a visible '<label>: ON|OFF' banner via the session UI."""
    ui = getattr(session, "ui", None)
    if ui is None or not hasattr(ui, "show_info"):
        return
    marker = "[bold green]ON[/bold green]" if state else "[bold]OFF[/bold]"
    try:
        ui.show_info(f"{label}: {marker}")
    except Exception:
        pass


@command("/thinking", help="Toggle extended thinking / reasoning mode.")
def thinking_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    session.thinking = not session.thinking
    _emit_toggle_banner(session, "🧠 Thinking mode", session.thinking)
    return CommandResult(
        ok=True,
        message=f"Thinking mode: {'ON' if session.thinking else 'OFF'}",
        data={"thinking": session.thinking},
    )


@command("/agentic", help="Toggle agentic tool-calling mode.")
def agentic_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    session.agentic = not session.agentic
    _emit_toggle_banner(session, "🛠 Agentic mode", session.agentic)
    return CommandResult(
        ok=True,
        message=f"Agentic mode: {'ON' if session.agentic else 'OFF'}",
        data={"agentic": session.agentic},
    )


@command("/yolo", help="Toggle YOLO mode — auto-approve modifying tool calls.")
def yolo_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    current = bool(session.variables.get("yolo", False))
    session.variables["yolo"] = not current
    # Persist to the session file so the setting survives restart.
    if hasattr(session, "session_manager") and hasattr(session, "folder_context"):
        session.session_manager.save_history(session.folder_context)
    _emit_toggle_banner(session, "⚡ YOLO mode", session.variables["yolo"])
    return CommandResult(
        ok=True,
        message=f"YOLO mode: {'ON' if session.variables['yolo'] else 'OFF'}",
        data={"yolo": session.variables["yolo"]},
    )


@command(
    "/show-thinking",
    help=(
        "Toggle display of reasoning/thinking deltas. When OFF, the model "
        "still generates thinking (controlled by /thinking) but the dim-"
        "italic text is hidden from the terminal. Teacher mode hides "
        "thinking by default — calling /show-thinking pins your preference "
        "across mode switches."
    ),
)
def show_thinking_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    arg = (args or "").strip().lower()
    current = bool(session.variables.get("show_thinking", True))
    if arg in ("", "toggle"):
        new_value = not current
    elif arg in ("on", "true", "1", "yes", "enable"):
        new_value = True
    elif arg in ("off", "false", "0", "no", "disable"):
        new_value = False
    else:
        return CommandResult(
            ok=False,
            message=f"Unknown /show-thinking argument: {args!r}. Use 'on', 'off', or 'toggle'.",
        )
    session.variables["show_thinking"] = new_value
    # Mark the preference as explicit so mode switches stop applying
    # their own defaults (teacher mode hides by default; the explicit
    # flag pins the user's choice across `/mode` toggles).
    session.variables["show_thinking_explicit"] = True
    if hasattr(session, "session_manager") and hasattr(session, "folder_context"):
        session.session_manager.save_history(session.folder_context)
    _emit_toggle_banner(session, "💭 Show thinking", new_value)
    return CommandResult(
        ok=True,
        message=f"Show thinking: {'ON' if new_value else 'OFF'}",
        data={"show_thinking": new_value},
    )


@command(
    "/verbose",
    help=(
        "Toggle verbose rendering. When OFF (default) the UI hides tool-arg "
        "dumps, per-turn token lines, result previews, the compaction notice, "
        "and the user-echo panel. The compact `→ tool_name` indicator stays."
    ),
)
def verbose_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    arg = (args or "").strip().lower()
    current = bool(session.variables.get("verbose", False))
    if arg in ("", "toggle"):
        new_value = not current
    elif arg in ("on", "true", "1", "yes", "enable"):
        new_value = True
    elif arg in ("off", "false", "0", "no", "disable"):
        new_value = False
    else:
        return CommandResult(
            ok=False,
            message=f"Unknown /verbose argument: {args!r}. Use 'on', 'off', or 'toggle'.",
        )
    session.variables["verbose"] = new_value
    if hasattr(session, "session_manager") and hasattr(session, "folder_context"):
        session.session_manager.save_history(session.folder_context)
    _emit_toggle_banner(session, "📢 Verbose rendering", new_value)
    return CommandResult(
        ok=True,
        message=f"Verbose rendering: {'ON' if new_value else 'OFF'}",
        data={"verbose": new_value},
    )


def _fmt_age(seconds: float) -> str:
    seconds = max(0.0, float(seconds or 0.0))
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def _ago(timestamp: Any, now: float) -> str:
    try:
        ts = float(timestamp)
    except (TypeError, ValueError):
        return "—"
    return _fmt_age(now - ts) + " ago"


def _empty_stats(now: float) -> dict:
    return {
        "session_started_at": now,
        "first_call_at": None,
        "last_call_at": None,
        "tools": {},
        "skills": {},
        "approvals": {"approved": 0, "denied": 0},
        "errors": {},
    }


def _render_stats(session: Any, snapshot: dict, allow_prompt: bool) -> None:
    """Print a Rich-formatted view of the stats snapshot + tracker."""
    if not allow_prompt:
        return
    ui = getattr(session, "ui", None)
    if ui is None:
        return
    console = getattr(ui, "console", None)
    if console is None:
        return

    import time as _time_mod

    try:
        from rich import box
        from rich.table import Table
    except Exception:
        return

    tokens = snapshot.get("tokens", {}) or {}
    ctx = snapshot.get("ctx", {}) or {}
    mode = (snapshot.get("mode", {}) or {}).get("name", "default")
    yolo_on = (snapshot.get("yolo", {}) or {}).get("enabled", False)
    plan_on = (snapshot.get("plan", {}) or {}).get("enabled", False)
    tool_stats = snapshot.get("tool_stats") or {}
    tools = tool_stats.get("tools") or {}
    skills = tool_stats.get("skills") or {}
    errors = tool_stats.get("errors") or {}
    approvals = tool_stats.get("approvals") or {}
    now = _time_mod.time()

    # Header / runtime line
    runtime_table = Table(box=box.SIMPLE, show_header=False)
    runtime_table.add_column("Field", style="cyan")
    runtime_table.add_column("Value", style="white")
    runtime_table.add_row("Mode", mode)
    runtime_table.add_row(
        "Toggles",
        f"yolo={'ON' if yolo_on else 'off'} · plan={'ON' if plan_on else 'off'}",
    )
    runtime_table.add_row(
        "Context",
        f"{ctx.get('current', 0):,} / {ctx.get('maximum', 0):,} tokens",
    )
    runtime_table.add_row(
        "Tokens (lifetime)",
        f"in={tokens.get('input', 0):,} · out={tokens.get('output', 0):,} · "
        f"total={tokens.get('total', 0):,} · cached={tokens.get('cached', 0):,} · "
        f"reasoning={tokens.get('reasoning', 0):,}",
    )
    cost = tokens.get("total_cost", 0.0) or 0.0
    runtime_table.add_row("Estimated cost", f"${cost:.4f}")
    started = tool_stats.get("session_started_at") or now
    runtime_table.add_row(
        "Session age",
        f"{_fmt_age(now - started)} (last tool: {_ago(tool_stats.get('last_call_at'), now)})",
    )
    total_tool_calls = sum(int(b.get("count", 0) or 0) for b in tools.values())
    total_failed = sum(int(b.get("count", 0) or 0) - int(b.get("success", 0) or 0) for b in tools.values())
    approve_n = int(approvals.get("approved", 0) or 0)
    deny_n = int(approvals.get("denied", 0) or 0)
    runtime_table.add_row(
        "Activity",
        f"{total_tool_calls} tool call(s) · {total_failed} failed · "
        f"{approve_n} approved · {deny_n} denied",
    )
    console.print(runtime_table)

    # Tool-usage table
    if tools:
        tool_table = Table(
            title="Tools used (top by call count)", box=box.SIMPLE
        )
        tool_table.add_column("Tool", style="cyan", no_wrap=True)
        tool_table.add_column("Calls", style="green", justify="right")
        tool_table.add_column("Success", style="green", justify="right")
        tool_table.add_column("Fail", style="red", justify="right")
        tool_table.add_column("Avg ms", style="yellow", justify="right")
        tool_table.add_column("Last used", style="dim")
        tool_table.add_column("Last args", style="dim")
        ranked = sorted(
            tools.items(), key=lambda kv: int(kv[1].get("count", 0) or 0), reverse=True
        )
        from rich.text import Text as _Text

        for name, bucket in ranked[:15]:
            count = int(bucket.get("count", 0) or 0)
            success = int(bucket.get("success", 0) or 0)
            failed = int(bucket.get("failed", 0) or 0)
            total_ms = float(bucket.get("total_ms", 0.0) or 0.0)
            avg_ms = total_ms / count if count else 0.0
            tool_table.add_row(
                _Text(str(name)),
                str(count),
                str(success),
                str(failed) if failed else "-",
                f"{avg_ms:.0f}",
                _ago(bucket.get("last_used_at"), now),
                _Text(str(bucket.get("last_args") or "")[:60]),
            )
        console.print(tool_table)
    else:
        console.print("[dim]No tool calls recorded yet this session.[/dim]")

    # Skills table
    if skills:
        skill_table = Table(title="Skills invoked", box=box.SIMPLE)
        skill_table.add_column("Skill", style="cyan", no_wrap=True)
        skill_table.add_column("Invocations", style="green", justify="right")
        skill_table.add_column("Last used", style="dim")
        ranked_skills = sorted(
            skills.items(),
            key=lambda kv: int(kv[1].get("invocations", 0) or 0),
            reverse=True,
        )
        from rich.text import Text as _Text

        for name, bucket in ranked_skills:
            skill_table.add_row(
                _Text(str(name)),
                str(int(bucket.get("invocations", 0) or 0)),
                _ago(bucket.get("last_used_at"), now),
            )
        console.print(skill_table)

    # Error tally
    if errors:
        err_table = Table(title="Tool errors", box=box.SIMPLE)
        err_table.add_column("error_code", style="red")
        err_table.add_column("count", style="yellow", justify="right")
        from rich.text import Text as _Text

        for code, n in sorted(errors.items(), key=lambda kv: -int(kv[1] or 0)):
            err_table.add_row(_Text(str(code)), str(int(n or 0)))
        console.print(err_table)


@command(
    "/stats",
    help="Show runtime stats (tokens, cost, tool/skill usage). /stats clear wipes the tracker.",
)
def stats_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    sub = (args or "").strip().lower()

    if sub == "clear":
        # Wipe only the per-session usage tracker. Token counts live on
        # `session_manager.token_counts` (lifetime spend on real money)
        # and stay put — that's not "metadata", that's accounting.
        import time as _time_mod

        session.tool_stats = _empty_stats(_time_mod.time())
        ui = getattr(session, "ui", None)
        if ui is not None and hasattr(ui, "show_info") and allow_prompt:
            ui.show_info(
                "[bold green]Stats tracker cleared.[/bold green] "
                "Token counts kept (lifetime accounting)."
            )
        return CommandResult(
            ok=True,
            message="Stats tracker cleared.",
            data={"tool_stats": session.tool_stats},
        )
    if sub:
        return CommandResult(
            ok=False, message=f"Unknown subcommand {sub!r}. Usage: /stats [clear]"
        )

    # Reuse the canonical collector from utils.runtime_metrics so the
    # numbers match the live status line.
    from utils.runtime_metrics import collect_runtime_metrics

    snapshot = collect_runtime_metrics(session)
    snapshot["tool_stats"] = getattr(session, "tool_stats", {}) or {}

    _render_stats(session, snapshot, allow_prompt)
    return CommandResult(ok=True, message="ok", data=snapshot)
