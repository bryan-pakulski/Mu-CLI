"""User-facing `/bash` slash command.

Runs a shell command from the REPL and prints stdout/stderr inline.
This is for the human at the keyboard — distinct from the agent-facing
`bash` tool. Useful for quick `/bash ls`, `/bash mkdir foo`, `/bash git
status` without leaving mucli.

Working directory: the first attached workspace folder, or the
process's cwd if none is attached. Bounded by `_BASH_TIMEOUT` so a
runaway command can't lock the REPL.

Not for interactive commands — there's no TTY plumbing, so `/bash vim`
will block until timeout. Use Ctrl+Z to suspend mucli and run those
in the real shell.
"""

from __future__ import annotations

import os
import subprocess
from typing import Any

from . import CommandResult, command


_BASH_TIMEOUT = 60.0


def _workspace_cwd(session: Any) -> str:
    folder_context = getattr(session, "folder_context", None)
    if folder_context is not None:
        folders = getattr(folder_context, "folders", None) or []
        for folder in folders:
            if folder and os.path.isdir(folder):
                return os.path.abspath(folder)
    return os.getcwd()


def _print(session: Any, message: str, *, style: str | None = None) -> None:
    if not message:
        return
    ui = getattr(session, "ui", None)
    if ui is None:
        return
    console = getattr(ui, "console", None)
    if console is not None:
        try:
            if style:
                console.print(message, style=style, markup=False, highlight=False)
            else:
                console.print(message, markup=False, highlight=False)
            return
        except Exception:
            pass
    if hasattr(ui, "show_info"):
        try:
            ui.show_info(message)
        except Exception:
            pass


@command(
    "/bash",
    "/sh",
    "/!",
    help=(
        "Run a shell command in your workspace folder. Usage: `/bash <cmd>`. "
        f"Bounded by a {_BASH_TIMEOUT:.0f}s timeout — not for interactive "
        "tools (vim, less). User-facing convenience, distinct from the "
        "agent's `bash` tool."
    ),
)
def bash_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    command_str = (args or "").strip()
    if not command_str:
        return CommandResult(
            ok=False,
            message=(
                "Usage: /bash <command>\n"
                "Examples:\n"
                "  /bash ls -la\n"
                "  /bash mkdir -p courses/perl/work\n"
                "  /bash git status\n"
                "Runs in the active workspace folder; bounded by a "
                f"{_BASH_TIMEOUT:.0f}s timeout."
            ),
        )

    cwd = _workspace_cwd(session)
    if allow_prompt:
        _print(session, f"$ {command_str}", style="dim cyan")
        if cwd != os.getcwd():
            _print(session, f"  (cwd: {cwd})", style="dim")

    try:
        proc = subprocess.run(
            ["/bin/bash", "-lc", command_str],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_BASH_TIMEOUT,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        exit_code = proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        exit_code = -1
        timed_out = True
    except FileNotFoundError:
        return CommandResult(
            ok=False,
            message="/bin/bash not found on this system — /bash is unavailable.",
        )

    if allow_prompt:
        if stdout:
            _print(session, stdout.rstrip("\n"))
        if stderr:
            _print(session, stderr.rstrip("\n"), style="yellow")
        if timed_out:
            _print(
                session,
                f"⏱  Timed out after {_BASH_TIMEOUT:.0f}s",
                style="red",
            )
        elif exit_code != 0:
            _print(session, f"  (exit {exit_code})", style="dim red")

    return CommandResult(
        ok=(not timed_out) and exit_code == 0,
        message=(
            "Bash command timed out"
            if timed_out
            else f"Bash command exited {exit_code}"
        ),
        data={
            "command": command_str,
            "cwd": cwd,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "timed_out": timed_out,
        },
    )
