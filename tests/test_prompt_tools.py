"""Tests for the refinement-surface prompt tools.

`request_text` and `gather_requirements` are exercised against a stub
UI so the tests never block on a TTY.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from mu.tools.prompt.handlers import (
    gather_requirements_tool,
    propose_change_tool,
    propose_stopping_point_tool,
    request_text_tool,
)


# ----- stub UI ----------------------------------------------------------------


class StubUI:
    """Scriptable UI. Pre-load responses; methods pop the next response
    and append the call into `calls` so tests can assert on what was
    asked."""

    def __init__(
        self,
        *,
        prompt_responses=None,
        choice_responses=None,
        prompt_raises=None,
        choice_raises=None,
    ):
        self._prompts = list(prompt_responses or [])
        self._choices = list(choice_responses or [])
        self._prompt_raises = list(prompt_raises or [])
        self._choice_raises = list(choice_raises or [])
        self.calls: list[dict] = []

    def prompt(self, message, default=None):
        self.calls.append({"kind": "prompt", "message": message, "default": default})
        if self._prompt_raises:
            exc = self._prompt_raises.pop(0)
            if exc is not None:
                raise exc
        return self._prompts.pop(0) if self._prompts else ""

    def ask_user_choice(
        self,
        question,
        options,
        *,
        multi_select=False,
        description="",
        allow_other=False,
    ):
        self.calls.append(
            {
                "kind": "choice",
                "question": question,
                "options": list(options),
                "multi_select": multi_select,
                "allow_other": allow_other,
            }
        )
        if self._choice_raises:
            exc = self._choice_raises.pop(0)
            if exc is not None:
                raise exc
        return self._choices.pop(0) if self._choices else {
            "selected": [],
            "other_text": "",
            "cancelled": True,
        }

    def show_info(self, _message):
        self.calls.append({"kind": "info"})


def _ctx(ui):
    return SimpleNamespace(ui=ui, session=None, folder_context=None, variables={})


# ----- request_text -----------------------------------------------------------


def test_request_text_returns_value_when_user_replies():
    ui = StubUI(prompt_responses=["kalman.py"])
    raw = request_text_tool(
        {"prompt": "Module name?", "default": "module.py"},
        _ctx(ui),
    )
    payload = json.loads(raw)
    assert payload == {"ok": True, "value": "kalman.py", "cancelled": False}
    assert ui.calls == [
        {"kind": "prompt", "message": "Module name?", "default": "module.py"}
    ]


def test_request_text_blank_reply_is_cancelled():
    ui = StubUI(prompt_responses=[""])
    raw = request_text_tool({"prompt": "Anything?"}, _ctx(ui))
    payload = json.loads(raw)
    assert payload["cancelled"] is True
    assert payload["value"] == ""


def test_request_text_keyboard_interrupt_is_cancel():
    ui = StubUI(prompt_raises=[KeyboardInterrupt()])
    raw = request_text_tool({"prompt": "Yes?"}, _ctx(ui))
    payload = json.loads(raw)
    assert payload == {"ok": True, "value": "", "cancelled": True}


def test_request_text_no_ui_returns_error_envelope():
    ctx = SimpleNamespace(ui=None, session=None)
    raw = request_text_tool({"prompt": "Anything?"}, ctx)
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert "No interactive UI" in payload["error"]
    assert payload["cancelled"] is True


def test_request_text_missing_prompt_is_error():
    ui = StubUI()
    raw = request_text_tool({}, _ctx(ui))
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert "requires a `prompt`" in payload["error"]


# ----- gather_requirements ----------------------------------------------------


def test_gather_requirements_mixed_fields_returns_answers():
    ui = StubUI(
        choice_responses=[
            {"selected": ["rust"], "other_text": "", "cancelled": False},
            {"selected": ["pytest"], "other_text": "", "cancelled": False},
        ],
        prompt_responses=["kalman.py"],
    )
    fields = [
        {"key": "lang", "label": "Language?", "kind": "choice", "options": ["python", "rust", "go"]},
        {"key": "tests", "label": "Test framework?", "kind": "choice", "options": ["pytest", "unittest"]},
        {"key": "name", "label": "Filename?", "kind": "text", "default": "module.py"},
    ]
    raw = gather_requirements_tool(
        {"headline": "Before I start:", "fields": fields},
        _ctx(ui),
    )
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["cancelled"] is False
    assert payload["answers"] == {
        "lang": "rust",
        "tests": "pytest",
        "name": "kalman.py",
    }
    assert payload["skipped_keys"] == []
    # Headline surfaces via show_info before any field.
    assert any(call.get("kind") == "info" for call in ui.calls)


def test_gather_requirements_multi_select_returns_list():
    ui = StubUI(
        choice_responses=[
            {"selected": ["aws", "gcp"], "other_text": "", "cancelled": False},
        ],
    )
    fields = [
        {
            "key": "clouds",
            "label": "Deploy targets?",
            "kind": "choice",
            "options": ["aws", "gcp", "azure"],
            "multi_select": True,
        }
    ]
    raw = gather_requirements_tool({"fields": fields}, _ctx(ui))
    payload = json.loads(raw)
    assert payload["answers"] == {"clouds": ["aws", "gcp"]}


def test_gather_requirements_allow_other_appends_prose():
    ui = StubUI(
        choice_responses=[
            {"selected": [], "other_text": "swift", "cancelled": False},
        ],
    )
    fields = [
        {
            "key": "lang",
            "label": "Language?",
            "kind": "choice",
            "options": ["python", "rust"],
            "allow_other": True,
        }
    ]
    raw = gather_requirements_tool({"fields": fields}, _ctx(ui))
    payload = json.loads(raw)
    assert payload["answers"] == {"lang": "swift"}


def test_gather_requirements_cancelled_field_is_recorded():
    ui = StubUI(
        choice_responses=[
            {"selected": [], "other_text": "", "cancelled": True},
        ],
        prompt_responses=["x.py"],
    )
    fields = [
        {"key": "lang", "label": "Language?", "kind": "choice", "options": ["py", "rs"]},
        {"key": "name", "label": "Filename?", "kind": "text"},
    ]
    raw = gather_requirements_tool({"fields": fields}, _ctx(ui))
    payload = json.loads(raw)
    # Cancelled choice → no answer for that key; subsequent fields still run.
    assert "lang" not in payload["answers"]
    assert payload["answers"]["name"] == "x.py"
    assert "lang" in payload["skipped_keys"]


def test_gather_requirements_keyboard_interrupt_returns_partial():
    ui = StubUI(
        choice_responses=[
            {"selected": ["py"], "other_text": "", "cancelled": False},
        ],
        prompt_raises=[KeyboardInterrupt()],
    )
    fields = [
        {"key": "lang", "label": "Language?", "kind": "choice", "options": ["py", "rs"]},
        {"key": "name", "label": "Filename?", "kind": "text"},
    ]
    raw = gather_requirements_tool({"fields": fields}, _ctx(ui))
    payload = json.loads(raw)
    assert payload["cancelled"] is True
    # First answer captured before the interrupt fired on field 2.
    assert payload["answers"]["lang"] == "py"
    assert "name" in payload["skipped_keys"]


def test_gather_requirements_invalid_field_skipped():
    """Fields without required props or with unknown kind are reported
    in skipped_keys but don't abort the whole form."""
    ui = StubUI(prompt_responses=["ok"])
    fields = [
        {"key": "", "label": "missing key", "kind": "text"},
        {"key": "bad_kind", "label": "X", "kind": "audio"},
        {"key": "good", "label": "Filename?", "kind": "text"},
    ]
    raw = gather_requirements_tool({"fields": fields}, _ctx(ui))
    payload = json.loads(raw)
    assert payload["answers"] == {"good": "ok"}
    assert "<unnamed>" in payload["skipped_keys"]
    assert "bad_kind" in payload["skipped_keys"]


def test_gather_requirements_no_fields_is_error():
    ui = StubUI()
    raw = gather_requirements_tool({"fields": []}, _ctx(ui))
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert "at least one field" in payload["error"]


# ----- propose_change ---------------------------------------------------------


def test_propose_change_applies_on_approval(tmp_path):
    file = tmp_path / "auth.py"
    file.write_text("old\n")
    ui = StubUI(
        choice_responses=[
            {"selected": ["Approve and apply"], "other_text": "", "cancelled": False},
        ],
    )
    raw = propose_change_tool(
        {
            "file": str(file),
            "after": "new content\n",
            "rationale": "switch to jwt",
            "kind": "edit",
        },
        _ctx(ui),
    )
    payload = json.loads(raw)
    assert payload["applied"] is True
    assert payload["file"] == str(file)
    assert file.read_text() == "new content\n"


def test_propose_change_rejected_leaves_file(tmp_path):
    file = tmp_path / "auth.py"
    file.write_text("old\n")
    ui = StubUI(
        choice_responses=[
            {"selected": ["Reject"], "other_text": "", "cancelled": False},
        ],
    )
    raw = propose_change_tool(
        {"file": str(file), "after": "x\n", "rationale": "why", "kind": "edit"},
        _ctx(ui),
    )
    payload = json.loads(raw)
    assert payload["applied"] is False
    assert payload["revision_request"] is None
    assert file.read_text() == "old\n"


def test_propose_change_revision_returns_note(tmp_path):
    file = tmp_path / "auth.py"
    file.write_text("old\n")
    ui = StubUI(
        choice_responses=[
            {"selected": ["Request revision"], "other_text": "", "cancelled": False},
        ],
        prompt_responses=["use session cookies instead"],
    )
    raw = propose_change_tool(
        {"file": str(file), "after": "x\n", "rationale": "why", "kind": "edit"},
        _ctx(ui),
    )
    payload = json.loads(raw)
    assert payload["applied"] is False
    assert payload["revision_request"] == "use session cookies instead"
    assert file.read_text() == "old\n"


def test_propose_change_new_file(tmp_path):
    target = tmp_path / "newmod.py"
    ui = StubUI(
        choice_responses=[
            {"selected": ["Approve and apply"], "other_text": "", "cancelled": False},
        ],
    )
    raw = propose_change_tool(
        {
            "file": str(target),
            "after": "def foo(): pass\n",
            "rationale": "scaffold",
            "kind": "new",
        },
        _ctx(ui),
    )
    payload = json.loads(raw)
    assert payload["applied"] is True
    assert target.read_text() == "def foo(): pass\n"


def test_propose_change_delete(tmp_path):
    file = tmp_path / "dead.py"
    file.write_text("# dead code\n")
    ui = StubUI(
        choice_responses=[
            {"selected": ["Approve and apply"], "other_text": "", "cancelled": False},
        ],
    )
    raw = propose_change_tool(
        {"file": str(file), "rationale": "unused", "kind": "delete"},
        _ctx(ui),
    )
    payload = json.loads(raw)
    assert payload["applied"] is True
    assert not file.exists()


def test_propose_change_refuses_edit_of_missing_file(tmp_path):
    target = tmp_path / "ghost.py"
    ui = StubUI()
    raw = propose_change_tool(
        {"file": str(target), "after": "x", "rationale": "r", "kind": "edit"},
        _ctx(ui),
    )
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert "does not exist" in payload["error"]


def test_propose_change_refuses_new_over_existing(tmp_path):
    file = tmp_path / "existing.py"
    file.write_text("x\n")
    ui = StubUI()
    raw = propose_change_tool(
        {"file": str(file), "after": "y", "rationale": "r", "kind": "new"},
        _ctx(ui),
    )
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert "already exists" in payload["error"]


def test_propose_change_requires_rationale(tmp_path):
    file = tmp_path / "f.py"
    file.write_text("x\n")
    raw = propose_change_tool(
        {"file": str(file), "after": "y", "rationale": "", "kind": "edit"},
        _ctx(StubUI()),
    )
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert "rationale" in payload["error"]


def test_propose_change_no_ui_returns_error_envelope(tmp_path):
    file = tmp_path / "f.py"
    file.write_text("x\n")
    ctx = SimpleNamespace(ui=None, session=None, folder_context=None)
    raw = propose_change_tool(
        {"file": str(file), "after": "y", "rationale": "r", "kind": "edit"},
        ctx,
    )
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert "No interactive UI" in payload["error"]


def test_propose_change_keyboard_interrupt_is_cancel(tmp_path):
    file = tmp_path / "f.py"
    file.write_text("x\n")
    ui = StubUI(choice_raises=[KeyboardInterrupt()])
    raw = propose_change_tool(
        {"file": str(file), "after": "y", "rationale": "r", "kind": "edit"},
        _ctx(ui),
    )
    payload = json.loads(raw)
    assert payload["applied"] is False
    assert payload["cancelled"] is True
    assert file.read_text() == "x\n"


# ----- propose_stopping_point -------------------------------------------------


def test_stopping_point_stop_choice():
    ui = StubUI(
        choice_responses=[
            {"selected": ["Stop here"], "other_text": "", "cancelled": False},
        ],
    )
    raw = propose_stopping_point_tool(
        {
            "done": "refactored auth.py to use JWT",
            "could_also": ["add tests", "update docs"],
            "recommendation": "stop",
        },
        _ctx(ui),
    )
    payload = json.loads(raw)
    assert payload == {"ok": True, "choice": "stop", "cancelled": False}


def test_stopping_point_continue_with_followup():
    ui = StubUI(
        choice_responses=[
            {"selected": ["add tests"], "other_text": "", "cancelled": False},
        ],
    )
    raw = propose_stopping_point_tool(
        {"done": "refactored", "could_also": ["add tests", "update docs"]},
        _ctx(ui),
    )
    payload = json.loads(raw)
    assert payload["choice"] == "add tests"
    assert payload["cancelled"] is False


def test_stopping_point_cancel_means_stop():
    ui = StubUI(
        choice_responses=[
            {"selected": [], "other_text": "", "cancelled": True},
        ],
    )
    raw = propose_stopping_point_tool(
        {"done": "x", "could_also": ["a", "b"]},
        _ctx(ui),
    )
    payload = json.loads(raw)
    assert payload == {"ok": True, "choice": "stop", "cancelled": True}


def test_stopping_point_requires_done_and_could_also():
    ui = StubUI()
    raw = propose_stopping_point_tool({"done": "", "could_also": ["x"]}, _ctx(ui))
    assert json.loads(raw)["ok"] is False
    raw = propose_stopping_point_tool({"done": "x", "could_also": []}, _ctx(ui))
    assert json.loads(raw)["ok"] is False


def test_gather_requirements_no_ui_returns_error_envelope():
    ctx = SimpleNamespace(ui=None, session=None)
    raw = gather_requirements_tool(
        {"fields": [{"key": "k", "label": "L", "kind": "text"}]},
        ctx,
    )
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert "No interactive UI" in payload["error"]
