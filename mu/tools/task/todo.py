"""TodoWrite-style task tools over the session's work items (``mu.modes.work``).

  * ``todo_write(content, status?)``         add a work item; returns its id
  * ``todo_set_status(id, status, evidence?)`` change status; ``done`` can cite
                                             passing check runs (``r<n>``), and
                                             must in loop mode
  * ``todo_list(status?)``                   list items
  * ``todo_delete(id)`` / ``todo_clear(status?)`` prune

Items are typed records in session state: never evicted, exactly one
``in_progress`` (starting one pauses the previous). ``completed`` is accepted
as an alias of ``done``. Ids are numeric in the tools (``#3``) and ``w3`` in
panels and evidence links.
"""

from typing import Any, Dict

from mu.modes.events import GateError
from mu.modes.work import STATUSES, normalize_status, work_state
from mu.tools import tool

VALID_STATUSES = ("pending", "in_progress", "done", "completed", "blocked", "dropped")
_MARKERS = {"pending": "[ ]", "in_progress": "[~]", "done": "[x]", "blocked": "[!]", "dropped": "[-]"}


def _envelope(tool_name: str, ok: bool, message: str, data: Dict[str, Any] | None = None,
              error_code: str | None = None) -> Dict[str, Any]:
    return {
        "ok": ok,
        "error_code": None if ok else (error_code or "invalid_args"),
        "message": message,
        "data": data or {},
        "artifacts": [],
        "telemetry": {"tool_name": tool_name},
    }


def _numeric(item_id: str) -> int:
    try:
        return int(str(item_id).lstrip("w"))
    except ValueError:
        return 0


def _item_dict(item) -> Dict[str, Any]:
    return {"id": _numeric(item.id), "ref": item.id, "content": item.title,
            "status": item.status, "evidence": list(item.evidence),
            "updated_at": item.updated_at}


def _mode(context) -> str:
    variables = getattr(context, "variables", None) or {}
    session = getattr(context, "session", None)
    if not variables and session is not None:
        variables = getattr(session, "variables", None) or {}
    return str(variables.get("agent_mode", "default") or "default").lower()


def _owner(context):
    return context if getattr(context, "session", None) is not None else None


# -------------------------------------------------------------------- write


@tool(
    name="todo_write",
    description=(
        "Add a task to the session's plan (work items). Returns the id to "
        "update later with todo_set_status."
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
        return _envelope("todo_write", False, "todo_write requires non-empty 'content'.")
    try:
        status = normalize_status(args.get("status") or "pending")
        if status == "done" and _mode(context) == "loop":
            raise GateError("In loop mode a task is marked done with evidence: add it, then "
                            "todo_set_status(id, 'done', evidence=['r<n>']).")
        item = work_state(_owner(context)).add(content, status=status, mode=_mode(context))
    except (ValueError, GateError) as exc:
        return _envelope("todo_write", False, str(exc))
    return _envelope("todo_write", True, f"Added todo #{_numeric(item.id)}: {content} ({item.status})",
                     _item_dict(item))


# ----------------------------------------------------------- set_status


@tool(
    name="todo_set_status",
    description=(
        "Update a task's status (one task is in_progress at a time; starting one "
        "pauses the previous). Cite passing check runs as evidence when marking done."
    ),
    parameters={
        "type": "object",
        "properties": {
            "id": {"type": "integer", "description": "The numeric id returned by todo_write."},
            "status": {"type": "string", "enum": list(VALID_STATUSES), "description": "New status."},
            "evidence": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Ids of passing check runs (r<n>) that show the task is done. Required in loop mode.",
            },
            "note": {"type": "string", "description": "Optional one-line note."},
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
        return _envelope("todo_set_status", False, "todo_set_status requires integer 'id'.")
    try:
        status = normalize_status(args.get("status"))
    except ValueError as exc:
        return _envelope("todo_set_status", False, str(exc))
    store = work_state(_owner(context))
    if store.get(todo_id) is None:
        return _envelope("todo_set_status", False, f"No todo with id #{todo_id}.", {"id": todo_id},
                         "not_found")
    evidence = args.get("evidence") or []
    if isinstance(evidence, str):
        evidence = [evidence]
    try:
        item = store.set_status(todo_id, status, evidence=list(evidence),
                                note=str(args.get("note") or ""),
                                require_evidence=_mode(context) == "loop")
    except GateError as exc:
        return _envelope("todo_set_status", False, str(exc), {"id": todo_id}, exc.code)
    return _envelope("todo_set_status", True, f"Todo #{todo_id} status: {item.status}", _item_dict(item))


# -------------------------------------------------------------------- list


@tool(
    name="todo_list",
    description="List the session's tasks. Optional 'status' filter.",
    parameters={
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": list(VALID_STATUSES), "description": "Filter to only this status."},
        },
    },
    requires_approval=False,
    execution_kind="memory",
    result_mode="json",
)
def todo_list(args: Dict[str, Any], context) -> Dict[str, Any]:
    try:
        status = normalize_status(args["status"]) if args.get("status") else None
    except ValueError as exc:
        return _envelope("todo_list", False, str(exc))
    items = work_state(_owner(context)).list(status)
    items.sort(key=lambda i: (i.status != "in_progress", _numeric(i.id)))
    todos = [_item_dict(i) for i in items]
    if not todos:
        message = "No todos."
    else:
        message = "\n".join(
            f"{_MARKERS.get(t['status'], '[?]')} #{t['id']}: {t['content']}"
            + (f" (evidence {', '.join(t['evidence'])})" if t["evidence"] else "")
            for t in todos
        )
    return _envelope("todo_list", True, message, {"todos": todos, "count": len(todos)})


# ------------------------------------------------------------------- delete


@tool(
    name="todo_delete",
    description=(
        "Remove a task by id. Use to prune stale or abandoned items so the plan "
        "stays honest; prefer status 'dropped' when the record matters."
    ),
    parameters={
        "type": "object",
        "properties": {"id": {"type": "integer", "description": "The numeric id returned by todo_write."}},
        "required": ["id"],
    },
    requires_approval=False,
    execution_kind="memory",
    result_mode="json",
)
def todo_delete(args: Dict[str, Any], context) -> Dict[str, Any]:
    try:
        todo_id = int(args.get("id"))
    except (TypeError, ValueError):
        return _envelope("todo_delete", False, "todo_delete requires integer 'id'.")
    store = work_state(_owner(context))
    if store.get(todo_id) is None:
        return _envelope("todo_delete", False, f"No todo with id #{todo_id}.", {"id": todo_id}, "not_found")
    item = store.delete(todo_id)
    return _envelope("todo_delete", True, f"Deleted todo #{todo_id}: {item.title}",
                     {"id": todo_id, "content": item.title})


# -------------------------------------------------------------------- clear


@tool(
    name="todo_clear",
    description=(
        "Bulk-remove tasks. With no argument, removes done tasks; status='all' "
        "wipes the plan (use when the user's ask has shifted)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "status": {"type": "string", "description": "'completed'/'done' (default) or 'all'."},
        },
    },
    requires_approval=False,
    execution_kind="memory",
    result_mode="json",
)
def todo_clear(args: Dict[str, Any], context) -> Dict[str, Any]:
    scope = str(args.get("status") or "done").strip().lower()
    store = work_state(_owner(context))
    if scope == "all":
        removed = store.clear_items(None)
    elif scope in ("completed", "done"):
        removed = store.clear_items("done")
    else:
        return _envelope("todo_clear", False, f"Unknown scope {scope!r}. Use 'done' (default) or 'all'.")
    return _envelope("todo_clear", True, f"Cleared {removed} todo(s) (scope={scope}).",
                     {"removed": removed, "scope": scope, "remaining": len(store.items)})


__all__ = ["todo_write", "todo_set_status", "todo_list", "todo_delete", "todo_clear",
           "VALID_STATUSES", "STATUSES"]
