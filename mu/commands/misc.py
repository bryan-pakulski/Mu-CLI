"""Miscellaneous slash commands: /help, /quit, /clear, /history."""

from typing import Any

from . import CommandResult, command


@command("/quit", "/q", help="Exit the REPL.")
def quit_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    return CommandResult(ok=True, message="Goodbye!", data={"exit": True}, exit=True)


@command(
    "/clear",
    help="Clear the terminal screen (does NOT touch history — use /history clear for that).",
)
def clear_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    if allow_prompt:
        ui = getattr(session, "ui", None)
        console = getattr(ui, "console", None) if ui is not None else None
        if console is not None:
            try:
                console.clear()
            except Exception:
                pass
    return CommandResult(ok=True, message="Screen cleared.")


@command(
    "/history",
    help="Show conversation history; /history clear wipes it.",
)
def history_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    sub = (args or "").strip().lower()
    if sub == "clear":
        session.session_manager.clear_current_history()
        return CommandResult(ok=True, message="Conversation history cleared.")
    if sub and sub != "show":
        return CommandResult(
            ok=False,
            message=f"Unknown subcommand {sub!r}. Usage: /history [show|clear]",
        )
    if allow_prompt:
        session.session_manager.view_history()
    return CommandResult(
        ok=True,
        data={"history": session.session_manager.history},
    )


def _help_groups() -> list:
    """Pull the canonical help table from mucli, falling back to whatever
    is currently in the registry if mucli isn't importable (test envs)."""
    try:
        from mucli import _HELP_GROUPS

        return list(_HELP_GROUPS)
    except Exception:
        # Build a minimal grouping from the registry so tests / partial
        # environments still get useful output.
        from . import list_commands

        rows = []
        seen = set()
        for spec in list_commands():
            key = spec.names[0]
            if key in seen:
                continue
            seen.add(key)
            aliases = "/".join(spec.names[1:]) if len(spec.names) > 1 else ""
            rows.append((spec.names[0], aliases, spec.help))
        return [("Available commands", rows)]


@command("/help", "/h", help="Show this menu of slash commands.")
def help_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    groups = _help_groups()

    # Interactive: render the Rich tables via mucli.print_help so the
    # user sees the same view they always have.
    if allow_prompt:
        try:
            from mucli import print_help

            print_help()
        except Exception:
            # Fall back to plain text if mucli isn't importable.
            for name, rows in groups:
                print(name)
                for cmd, alias, desc in rows:
                    alias_str = f" ({alias})" if alias else ""
                    print(f"  {cmd}{alias_str} — {desc}")

    # Always populate `message` and `data` so non-interactive callers
    # (JSON output, tests) get the full surface.
    lines = []
    flat = []
    for name, rows in groups:
        lines.append(f"\n{name}:")
        for cmd, alias, desc in rows:
            alias_str = f" ({alias})" if alias else ""
            lines.append(f"  {cmd}{alias_str} — {desc}")
            flat.append({"command": cmd, "alias": alias, "description": desc, "group": name})

    body = "\n".join(lines).strip()
    return CommandResult(
        ok=True,
        message=body,
        data={"commands_help": True, "groups": groups, "commands": flat},
    )
