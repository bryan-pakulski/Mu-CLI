"""Tests for the TodoWrite-style task tool in `mu.tools.task.todo`."""

import json

import pytest

from mu.memory.stores import ScratchpadStore
from mu.tools import build_tool_context, execute
import mu.tools.task.todo as todo


class _Session:
    """Minimal session-like object that exposes a ScratchpadStore."""

    def __init__(self):
        self.turn_scratchpad = ScratchpadStore()


def _ctx():
    return build_tool_context(
        folder_context=None, ui=None, variables={}, session=_Session()
    )


def test_todo_write_adds_pending_entry():
    ctx = _ctx()
    res = execute("todo_write", {"content": "implement feature X"}, ctx)
    assert res["ok"] is True
    assert res["data"]["status"] == "pending"
    todo_id = res["data"]["id"]
    assert isinstance(todo_id, int)
    # Listing shows it.
    listing = execute("todo_list", {}, ctx)
    assert listing["data"]["count"] == 1
    assert listing["data"]["todos"][0]["id"] == todo_id


def test_todo_write_rejects_empty_content():
    ctx = _ctx()
    res = execute("todo_write", {"content": "   "}, ctx)
    assert res["ok"] is False
    assert res["error_code"] == "invalid_args"


def test_todo_write_rejects_unknown_status():
    ctx = _ctx()
    res = execute(
        "todo_write", {"content": "x", "status": "definitely-not-a-status"}, ctx
    )
    assert res["ok"] is False
    assert res["error_code"] == "invalid_args"


def test_todo_set_status_round_trip():
    ctx = _ctx()
    r = execute("todo_write", {"content": "do thing"}, ctx)
    todo_id = r["data"]["id"]

    upd = execute("todo_set_status", {"id": todo_id, "status": "in_progress"}, ctx)
    assert upd["ok"] is True
    assert upd["data"]["status"] == "in_progress"

    listing = execute("todo_list", {}, ctx)
    [entry] = listing["data"]["todos"]
    assert entry["status"] == "in_progress"

    done = execute("todo_set_status", {"id": todo_id, "status": "completed"}, ctx)
    assert done["data"]["status"] == "completed"


def test_todo_set_status_unknown_id():
    ctx = _ctx()
    res = execute("todo_set_status", {"id": 999, "status": "completed"}, ctx)
    assert res["ok"] is False
    assert res["error_code"] == "not_found"


def test_todo_list_filters_by_status():
    ctx = _ctx()
    a = execute("todo_write", {"content": "alpha", "status": "pending"}, ctx)
    b = execute("todo_write", {"content": "beta", "status": "in_progress"}, ctx)
    c = execute("todo_write", {"content": "gamma", "status": "completed"}, ctx)

    pending = execute("todo_list", {"status": "pending"}, ctx)
    assert {t["content"] for t in pending["data"]["todos"]} == {"alpha"}

    completed = execute("todo_list", {"status": "completed"}, ctx)
    assert {t["content"] for t in completed["data"]["todos"]} == {"gamma"}


def test_todo_list_orders_in_progress_first():
    ctx = _ctx()
    execute("todo_write", {"content": "first pending"}, ctx)
    second = execute("todo_write", {"content": "second pending"}, ctx)
    in_prog = execute(
        "todo_write", {"content": "actively working on this", "status": "in_progress"},
        ctx,
    )
    listing = execute("todo_list", {}, ctx)
    todos = listing["data"]["todos"]
    assert todos[0]["status"] == "in_progress"
    assert todos[0]["id"] == in_prog["data"]["id"]


def test_todo_message_format_has_status_markers():
    ctx = _ctx()
    execute("todo_write", {"content": "alpha"}, ctx)
    execute("todo_write", {"content": "beta", "status": "in_progress"}, ctx)
    listing = execute("todo_list", {}, ctx)
    msg = listing["message"]
    assert "[~]" in msg
    assert "[ ]" in msg


def test_separate_sessions_have_separate_todo_lists():
    ctx1 = _ctx()
    ctx2 = _ctx()
    execute("todo_write", {"content": "only in 1"}, ctx1)
    listing_2 = execute("todo_list", {}, ctx2)
    assert listing_2["data"]["count"] == 0


# ---------------------------------------------------------- delete + clear


def test_todo_delete_removes_entry():
    ctx = _ctx()
    r = execute("todo_write", {"content": "stale task"}, ctx)
    todo_id = r["data"]["id"]
    assert ctx.session.turn_scratchpad.entries  # has the entry

    res = execute("todo_delete", {"id": todo_id}, ctx)
    assert res["ok"] is True
    assert "Deleted todo" in res["message"]
    # Entry gone from the store.
    assert all(e.id != todo_id for e in ctx.session.turn_scratchpad.entries)


def test_todo_delete_unknown_id():
    ctx = _ctx()
    res = execute("todo_delete", {"id": 999}, ctx)
    assert res["ok"] is False
    assert res["error_code"] == "not_found"


def test_todo_delete_only_touches_todos():
    """A non-todo scratchpad note sharing an id space must not be deleted
    by todo_delete — the tool filters on the `todo` tag."""
    ctx = _ctx()
    # Save a plain (non-todo) scratchpad note directly to the store so it
    # shares the id sequence but lacks the todo tag.
    note = ctx.session.turn_scratchpad.save("plain note", tags=["plan"])
    res = execute("todo_delete", {"id": note.id}, ctx)
    assert res["ok"] is False
    assert res["error_code"] == "not_found"
    # The plain note survives.
    assert any(e.id == note.id for e in ctx.session.turn_scratchpad.entries)


def test_todo_clear_completed_prunes_only_finished():
    ctx = _ctx()
    execute("todo_write", {"content": "done one", "status": "completed"}, ctx)
    execute("todo_write", {"content": "still going", "status": "in_progress"}, ctx)
    execute("todo_write", {"content": "not started"}, ctx)

    res = execute("todo_clear", {}, ctx)  # default scope = completed
    assert res["ok"] is True
    assert res["data"]["removed"] == 1
    remaining = execute("todo_list", {}, ctx)["data"]["todos"]
    assert {t["content"] for t in remaining} == {"still going", "not started"}


def test_todo_clear_all_wipes_ledger():
    ctx = _ctx()
    execute("todo_write", {"content": "a"}, ctx)
    execute("todo_write", {"content": "b", "status": "in_progress"}, ctx)

    res = execute("todo_clear", {"status": "all"}, ctx)
    assert res["ok"] is True
    assert res["data"]["removed"] == 2
    assert execute("todo_list", {}, ctx)["data"]["count"] == 0


def test_todo_clear_rejects_unknown_scope():
    ctx = _ctx()
    execute("todo_write", {"content": "a"}, ctx)
    res = execute("todo_clear", {"status": "bogus"}, ctx)
    assert res["ok"] is False
    assert res["error_code"] == "invalid_args"
    # Ledger untouched.
    assert execute("todo_list", {}, ctx)["data"]["count"] == 1


def test_todo_clear_all_preserves_non_todo_scratchpad():
    """todo_clear('all') wipes the todo ledger but must not drop unrelated
    scratchpad notes that share the store."""
    ctx = _ctx()
    ctx.session.turn_scratchpad.save("ephemeral note", tags=["plan"])
    execute("todo_write", {"content": "task"}, ctx)

    res = execute("todo_clear", {"status": "all"}, ctx)
    assert res["ok"] is True
    # Non-todo note survives.
    notes = [e for e in ctx.session.turn_scratchpad.entries if "todo" not in e.tags]
    assert len(notes) == 1
    assert notes[0].content == "ephemeral note"
