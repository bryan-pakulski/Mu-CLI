"""Trace slash commands: /trace clear, /trace list.

Clears trace JSONL files from ``$MUCLI_HOME/trace/``. Mirrors the GUI's
DELETE endpoint so TUI users have the same surface. Never deletes the
trace directory itself, only the ``*.jsonl`` run files.
"""

from __future__ import annotations

import os
from typing import Any

from . import CommandResult, command


def _emit(session: Any, body: str, allow_prompt: bool) -> None:
    ui = getattr(session, "ui", None)
    console = getattr(ui, "console", None) if ui is not None else None
    if console is not None and allow_prompt:
        try:
            from utils.helpers import safe_markup

            console.print(safe_markup(body))
        except Exception:
            pass


@command("/trace", help="Clear trace runs: /trace clear [all|<run_id>], /trace list")
def trace_cmd(session: Any, args: str, *, allow_prompt: bool = True) -> CommandResult:
    parts = args.split()
    sub = parts[0] if parts else ""

    if sub == "clear":
        target = parts[1] if len(parts) > 1 else "all"
        return _clear(session, target, allow_prompt)
    if sub == "list":
        return _list(session, allow_prompt)

    _emit(session, "Usage: /trace clear [all|<run_id>] | /trace list", allow_prompt)
    return CommandResult(ok=False, message="no subcommand")


def _clear(session: Any, target: str, allow_prompt: bool) -> CommandResult:
    from mu.trace import find_trace_path, list_trace_runs, trace_dir

    try:
        if target == "all":
            d = trace_dir()
            if not d or not os.path.isdir(d):
                _emit(session, "No trace directory.", allow_prompt)
                return CommandResult(ok=True, message="no trace dir")
            files = [f for f in os.listdir(d) if f.endswith(".jsonl")]
            for f in files:
                try:
                    os.remove(os.path.join(d, f))
                except OSError as e:
                    _emit(session, f"Failed to remove {f}: {e}", allow_prompt)
            _emit(session, f"Cleared {len(files)} trace run(s).", allow_prompt)
            return CommandResult(ok=True, message=f"cleared {len(files)} runs")
        # single run_id
        path = find_trace_path(target)
        if path is None:
            _emit(session, f"No trace run: {target}", allow_prompt)
            return CommandResult(ok=False, message=f"run not found: {target}")
        os.remove(path)
        _emit(session, f"Cleared trace run {target}.", allow_prompt)
        return CommandResult(ok=True, message=f"cleared {target}")
    except Exception as e:
        _emit(session, f"Clear failed: {e}", allow_prompt)
        return CommandResult(ok=False, message=f"clear failed: {e}")


def _list(session: Any, allow_prompt: bool) -> CommandResult:
    from mu.trace import list_trace_runs

    try:
        runs = list_trace_runs()
    except Exception as e:
        _emit(session, f"List failed: {e}", allow_prompt)
        return CommandResult(ok=False, message=f"list failed: {e}")
    if not runs:
        _emit(session, "No trace runs recorded.", allow_prompt)
        return CommandResult(ok=True, message="no runs")
    lines = []
    for r in runs:
        run_id = r.get("run_id", "?")
        model = r.get("model", "?")
        iters = r.get("iters", "?")
        lines.append(f"{run_id}  {model}  {iters} iters")
    _emit(session, "\n".join(lines), allow_prompt)
    return CommandResult(ok=True, message=f"{len(runs)} runs")