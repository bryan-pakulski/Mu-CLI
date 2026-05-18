"""TodoWrite-style task tool, backed by `ScratchpadStore`.

Three operations, exposed as three tools the model can call:

  * `todo_write(content, status?)`   - add a new todo. `status` defaults to
                                       "pending". Returns the new id.
  * `todo_set_status(id, status)`    - update a todo's status. Valid
                                       statuses: pending, in_progress,
                                       completed, blocked.
  * `todo_list(status?)`             - list todos, optionally filtered by
                                       status. Returns a numbered list
                                       with id, status, and content.

Status is encoded in the entry's `tags` as `"status:<value>"`. We always
also attach the `"todo"` tag so todos can be distinguished from other
scratchpad entries (memory tools share the same store, and the model
may also save general scratchpad notes).

Why scratchpad and not task_memory: TodoWrite is per-turn / per-session
ephemeral planning. Durable cross-session facts go to `task_memory`.
"""

from typing import Any, Dict, Optional

from mu.tools import tool


VALID_STATUSES = ("pending", "in_progress", "completed", "blocked")
_TODO_TAG = "todo"


def _store(context):
    """Resolve the ScratchpadStore from the execution context.

    Context.session.turn_scratchpad is the live store on a real session.
    For unit tests that pass a session-less context we degrade to a
    transient per-process store.
    """
    session = getattr(context, "session", None)
    if session is not None and hasattr(session, "turn_scratchpad"):
        return session.turn_scratchpad
    # Fallback: keep a process-local store so the tool can still be
    # exercised standalone in tests.
    global _FALLBACK_STORE
    try:
        return _FALLBACK_STORE
    except NameError:
        pass
    from mu.memory.stores import ScratchpadStore
    _FALLBACK_STORE = ScratchpadStore()
    return _FALLBACK_STORE


_FALLBACK_STORE = None


def _status_tag(status: str) -> str:
    return f"status:{status}"


def _entry_status(entry) -> str:
    for tag in entry.tags:
        if tag.startswith("status:"):
            return tag.split(":", 1)[1]
    return "pending"


def _set_status_tag(entry, status: str) -> None:
    new_tags = [t for t in entry.tags if not t.startswith("status:")]
    new_tags.append(_status_tag(status))
    entry.tags = new_tags


# -------------------------------------------------------------------- write


@tool(
    name="todo_write",
    description=(
        "Add a new task to the current session's TodoWrite list. Returns "
        "the assigned id so it can be updated later with todo_set_status."
    ),
    parameters={
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "What the task is — a single short imperative sentence.",
            },
            "status": {
                "type": "string",
                "enum": list(VALID_STATUSES),
                "description": "Initial status. Defaults to 'pending'.",
            },
        },
        "required": ["content"],
    },
    requires_approval=False,
    execution_kind="memory",
    result_mode="json",
)
def todo_write(args: Dict[str, Any], context) -> Dict[str, Any]:
    content = str(args.get("content", "") or "").strip()
    if not content:
        return {
            "ok": False,
            "error_code": "invalid_args",
            "message": "todo_write requires non-empty 'content'.",
            "data": {},
            "artifacts": [],
            "telemetry": {"tool_name": "todo_write"},
        }
    status = str(args.get("status") or "pending").strip().lower()
    if status not in VALID_STATUSES:
        return {
            "ok": False,
            "error_code": "invalid_args",
            "message": (
                f"Unknown status {status!r}. Use one of: {', '.join(VALID_STATUSES)}."
            ),
            "data": {},
            "artifacts": [],
            "telemetry": {"tool_name": "todo_write"},
        }

    entry = _store(context).save(
        content,
        tags=[_TODO_TAG, _status_tag(status)],
        source="todo_write",
    )
    return {
        "ok": True,
        "error_code": None,
        "message": f"Added todo #{entry.id} ({status}): {content}",
        "data": {"id": entry.id, "content": entry.content, "status": status},
        "artifacts": [],
        "telemetry": {"tool_name": "todo_write"},
    }


# ----------------------------------------------------------- set_status


@tool(
    name="todo_set_status",
    description="Update a todo's status. Use to mark tasks in_progress, completed, or blocked.",
    parameters={
        "type": "object",
        "properties": {
            "id": {
                "type": "integer",
                "description": "The numeric id returned by todo_write.",
            },
            "status": {
                "type": "string",
                "enum": list(VALID_STATUSES),
                "description": "New status.",
            },
        },
        "required": ["id", "status"],
    },
    requires_approval=False,
    execution_kind="memory",
    result_mode="json",
)
def todo_set_status(args: Dict[str, Any], context) -> Dict[str, Any]:
    try:
        todo_id = int(args.get("id"))
    except (TypeError, ValueError):
        return {
            "ok": False,
            "error_code": "invalid_args",
            "message": "todo_set_status requires integer 'id'.",
            "data": {},
            "artifacts": [],
            "telemetry": {"tool_name": "todo_set_status"},
        }
    status = str(args.get("status") or "").strip().lower()
    if status not in VALID_STATUSES:
        return {
            "ok": False,
            "error_code": "invalid_args",
            "message": (
                f"Unknown status {status!r}. Use one of: {', '.join(VALID_STATUSES)}."
            ),
            "data": {},
            "artifacts": [],
            "telemetry": {"tool_name": "todo_set_status"},
        }

    store = _store(context)
    entry = next(
        (e for e in store.entries if e.id == todo_id and _TODO_TAG in e.tags),
        None,
    )
    if entry is None:
        return {
            "ok": False,
            "error_code": "not_found",
            "message": f"No todo with id #{todo_id}.",
            "data": {"id": todo_id},
            "artifacts": [],
            "telemetry": {"tool_name": "todo_set_status"},
        }
    _set_status_tag(entry, status)
    import time
    entry.updated_at = time.time()
    return {
        "ok": True,
        "error_code": None,
        "message": f"Todo #{entry.id} status: {status}",
        "data": {"id": entry.id, "content": entry.content, "status": status},
        "artifacts": [],
        "telemetry": {"tool_name": "todo_set_status"},
    }


# -------------------------------------------------------------------- list


@tool(
    name="todo_list",
    description="List todos in the current session. Optional 'status' filter.",
    parameters={
        "type": "object",
        "properties": {
            "status": {
                "type": "string",
                "enum": list(VALID_STATUSES),
                "description": "Filter to only this status.",
            },
        },
    },
    requires_approval=False,
    execution_kind="memory",
    result_mode="json",
)
def todo_list(args: Dict[str, Any], context) -> Dict[str, Any]:
    status_filter: Optional[str] = (
        str(args.get("status")).strip().lower() if args.get("status") else None
    )
    if status_filter and status_filter not in VALID_STATUSES:
        return {
            "ok": False,
            "error_code": "invalid_args",
            "message": (
                f"Unknown status {status_filter!r}. "
                f"Use one of: {', '.join(VALID_STATUSES)}."
            ),
            "data": {},
            "artifacts": [],
            "telemetry": {"tool_name": "todo_list"},
        }

    store = _store(context)
    todos = []
    for entry in store.entries:
        if _TODO_TAG not in entry.tags:
            continue
        entry_status = _entry_status(entry)
        if status_filter and entry_status != status_filter:
            continue
        todos.append(
            {
                "id": entry.id,
                "content": entry.content,
                "status": entry_status,
                "updated_at": entry.updated_at,
            }
        )
    todos.sort(key=lambda t: (t["status"] != "in_progress", t["id"]))

    if not todos:
        msg = "No todos."
    else:
        lines = []
        for t in todos:
            marker = {
                "pending": "[ ]",
                "in_progress": "[~]",
                "completed": "[x]",
                "blocked": "[!]",
            }.get(t["status"], "[?]")
            lines.append(f"{marker} #{t['id']}: {t['content']}")
        msg = "\n".join(lines)

    return {
        "ok": True,
        "error_code": None,
        "message": msg,
        "data": {"todos": todos, "count": len(todos)},
        "artifacts": [],
        "telemetry": {"tool_name": "todo_list"},
    }


__all__ = ["todo_write", "todo_set_status", "todo_list", "VALID_STATUSES"]
