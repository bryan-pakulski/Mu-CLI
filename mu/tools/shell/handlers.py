"""Shell + background-task `@tool` handlers.

The synchronous `bash` runs through `core.tools.bash_command`. The bg
family routes through `BackgroundTaskRegistry` on the session (or a
process-local fallback so the tools still work in session-less unit
tests). Implementations stay in `core/` for now; this module is the
registration surface.
"""

import json
from typing import Any, Dict

from mu.tools import tool


# ---------------------------------------------------------------- bg registry resolver


_STANDALONE_BG_REGISTRY = None


def _bg_registry(context):
    """Resolve the session's `BackgroundTaskRegistry`. Falls back to a
    process-global one if no Session is bound (mirrors the legacy
    `_bg_registry` in `core/tools.py`)."""
    session = getattr(context, "session", None) if context is not None else None
    if session is not None and hasattr(session, "background_tasks"):
        return session.background_tasks
    global _STANDALONE_BG_REGISTRY
    if _STANDALONE_BG_REGISTRY is None:
        from core.background_tasks import BackgroundTaskRegistry

        _STANDALONE_BG_REGISTRY = BackgroundTaskRegistry()
    return _STANDALONE_BG_REGISTRY


# ---------------------------------------------------------------- bash (synchronous)


@tool(
    name="bash",
    description=(
        "Executes a raw bash command in the attached workspace and returns "
        "combined STDOUT/STDERR."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The bash command to execute.",
            },
            "cwd": {
                "type": "string",
                "description": (
                    "Optional working directory. Must be within the "
                    "attached workspace."
                ),
            },
            "timeout_seconds": {
                "type": "integer",
                "description": (
                    "Maximum seconds before terminating the command "
                    "(default 120)."
                ),
                "default": 120,
            },
            "max_output_chars": {
                "type": "integer",
                "description": (
                    "Maximum combined output length to return (default 12000)."
                ),
                "default": 12000,
            },
        },
        "required": ["command"],
    },
    requires_approval=True,
    execution_kind="mutate",
    preview_policy="optional",
)
def bash(args: Dict[str, Any], context) -> str:
    from core.tools import bash_command

    return bash_command(
        args.get("command", ""),
        context.folder_context,
        cwd=args.get("cwd"),
        timeout_seconds=args.get("timeout_seconds", 120),
        max_output_chars=args.get("max_output_chars", 12000),
    )


# ---------------------------------------------------------------- background bash


@tool(
    name="bash_background",
    description=(
        "Start a long-running bash command in the background and return a "
        "task_id you can poll with `bash_status` or read with `bash_logs`. "
        "Use this for test watchers, dev servers, builds, or anything that "
        "would block the synchronous `bash` tool for too long."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The bash command to run in the background.",
            },
            "name": {
                "type": "string",
                "description": (
                    "Optional short label for the task (shown in /stats and logs)."
                ),
            },
            "cwd": {
                "type": "string",
                "description": "Optional working directory. Must exist.",
            },
        },
        "required": ["command"],
    },
    requires_approval=True,
    execution_kind="mutate",
    preview_policy="optional",
)
def bash_background(args: Dict[str, Any], context) -> str:
    from core.background_tasks import summarize_task

    registry = _bg_registry(context)
    command = str(args.get("command", "") or "").strip()
    if not command:
        return json.dumps({"error": "command is required"})
    try:
        task = registry.start(
            command,
            name=str(args.get("name", "") or "") or None,
            cwd=str(args.get("cwd", "") or "") or None,
        )
    except (ValueError, RuntimeError) as e:
        return json.dumps({"error": str(e)})
    summary = summarize_task(task, tail_lines=0)
    summary["message"] = f"Background task started: {task.task_id}"
    return json.dumps(summary, indent=2)


@tool(
    name="bash_status",
    description=(
        "Poll a background task's status (running/completed/failed/killed) "
        "and the tail of its stdout/stderr."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The task_id returned by `bash_background`.",
            },
            "tail_lines": {
                "type": "integer",
                "description": "Lines of stdout/stderr to include (default 20).",
                "default": 20,
            },
        },
        "required": ["task_id"],
    },
    requires_approval=False,
    execution_kind="read",
    preview_policy="none",
)
def bash_status(args: Dict[str, Any], context) -> str:
    from core.background_tasks import summarize_task

    registry = _bg_registry(context)
    task_id = str(args.get("task_id", "") or "").strip()
    task = registry.get(task_id)
    if task is None:
        return json.dumps({"error": f"no such task: {task_id}"})
    tail_lines = max(0, int(args.get("tail_lines", 20) or 20))
    return json.dumps(summarize_task(task, tail_lines=tail_lines), indent=2)


@tool(
    name="bash_logs",
    description=(
        "Read the tail of stdout / stderr from a background task. "
        "Stream selector: 'stdout', 'stderr', or 'both'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The task_id returned by `bash_background`.",
            },
            "stream": {
                "type": "string",
                "description": (
                    "Which stream to read: 'stdout', 'stderr', or 'both'."
                ),
                "default": "both",
            },
            "lines": {
                "type": "integer",
                "description": "Number of trailing lines to return (default 200).",
                "default": 200,
            },
        },
        "required": ["task_id"],
    },
    requires_approval=False,
    execution_kind="read",
    preview_policy="none",
)
def bash_logs(args: Dict[str, Any], context) -> str:
    from core.background_tasks import tail as _tail

    registry = _bg_registry(context)
    task_id = str(args.get("task_id", "") or "").strip()
    task = registry.get(task_id)
    if task is None:
        return json.dumps({"error": f"no such task: {task_id}"})
    stream = str(args.get("stream", "both") or "both").lower()
    lines = max(1, int(args.get("lines", 200) or 200))
    payload: dict = {"task_id": task_id, "status": task.status()}
    if stream in ("stdout", "both"):
        payload["stdout"] = _tail(task.stdout_buf, lines)
    if stream in ("stderr", "both"):
        payload["stderr"] = _tail(task.stderr_buf, lines)
    return json.dumps(payload, indent=2)


@tool(
    name="bash_kill",
    description=(
        "Terminate a background task. SIGTERM with a short grace, then SIGKILL."
    ),
    parameters={
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": "The task_id to kill.",
            }
        },
        "required": ["task_id"],
    },
    requires_approval=True,
    execution_kind="mutate",
    preview_policy="optional",
)
def bash_kill(args: Dict[str, Any], context) -> str:
    from core.background_tasks import summarize_task

    registry = _bg_registry(context)
    task_id = str(args.get("task_id", "") or "").strip()
    task = registry.kill(task_id)
    if task is None:
        return json.dumps({"error": f"no such task: {task_id}"})
    return json.dumps(summarize_task(task, tail_lines=5), indent=2)


@tool(
    name="bash_list",
    description="List every background task in the session — running or completed.",
    parameters={"type": "object", "properties": {}},
    requires_approval=False,
    execution_kind="read",
    preview_policy="none",
)
def bash_list(args: Dict[str, Any], context) -> str:
    from core.background_tasks import summarize_task

    registry = _bg_registry(context)
    tasks = [summarize_task(t, tail_lines=3) for t in registry.list()]
    return json.dumps({"tasks": tasks, "count": len(tasks)}, indent=2)
