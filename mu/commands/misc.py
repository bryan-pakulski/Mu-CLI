"""Miscellaneous slash commands: /help, /quit, /clear, /view."""

from typing import Any

from . import CommandResult, command


@command("/quit", "/q", help="Exit the REPL.")
def quit_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    return CommandResult(ok=True, message="Goodbye!", data={"exit": True}, exit=True)


@command("/clear", help="Clear the conversation history for the current session.")
def clear_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    session.session_manager.clear_current_history()
    return CommandResult(ok=True, message="Conversation history cleared.")


@command("/view", help="View the conversation history for the current session.")
def view_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    if allow_prompt:
        session.session_manager.view_history()
    return CommandResult(
        ok=True,
        data={"history": session.session_manager.history},
    )


@command("/help", help="Show this help.")
def help_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    from . import list_commands

    lines = []
    seen = set()
    for spec in list_commands():
        key = spec.names[0]
        if key in seen:
            continue
        seen.add(key)
        aliases = ", ".join(spec.names)
        lines.append(f"  {aliases}: {spec.help}")
    body = "Available commands:\n" + "\n".join(sorted(lines))
    if allow_prompt:
        print(body)
    return CommandResult(ok=True, message=body, data={"commands_help": True})
