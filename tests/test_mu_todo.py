"""Tests for the TodoWrite-style task tool in `mu.tools.task.todo`."""

import json

import pytest

from core.memory import ScratchpadStore
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
