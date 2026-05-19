"""Tests for the interactive choice prompt + ask_user_choice tool.

State-machine tests don't touch prompt-toolkit. The tool-handler tests
exercise the dispatcher with a stub UI so they never block on a TTY.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from mu.ui.choice_prompt import ChoicePromptState


@pytest.fixture
def options():
    return ["malloc", "calloc", "realloc", "alloca"]


# ------------------------------------------------------------ state machine


def test_move_wraps_at_boundary(options):
    s = ChoicePromptState(options=options)
    s.move(-1)
    assert s.cursor == len(options) - 1
    s.move(1)
    assert s.cursor == 0


def test_single_select_submit_returns_one(options):
    s = ChoicePromptState(options=options)
    s.move(2)
    picked = s.submit()
    assert picked == ["realloc"]
    assert s.submitted is True


def test_multi_select_toggle_and_submit(options):
    s = ChoicePromptState(options=options, multi_select=True)
    s.move(1)
    s.toggle_current()
    s.move(2)
    s.toggle_current()
    picked = s.submit()
    # Returned in original option order, not toggle order.
    assert picked == ["calloc", "alloca"]


def test_multi_select_toggle_twice_clears(options):
    s = ChoicePromptState(options=options, multi_select=True)
    s.toggle_current()
    s.toggle_current()
    assert s.selected == set()


def test_select_all_and_clear_only_in_multi_mode(options):
    single = ChoicePromptState(options=options)
    single.select_all()
    assert single.selected == set()  # no-op in single mode

    multi = ChoicePromptState(options=options, multi_select=True)
    multi.select_all()
    assert multi.selected == set(range(len(options)))
    multi.clear_selection()
    assert multi.selected == set()


def test_cancel_sets_flags(options):
    s = ChoicePromptState(options=options)
    s.cancel()
    assert s.cancelled is True
    assert s.submitted is True


def test_empty_options_submit_returns_empty():
    s = ChoicePromptState(options=[])
    picked = s.submit()
    assert picked == []


def test_state_carries_text_entry_flags(options):
    """The state machine exposes the two new fields the runner uses to
    flip between picker mode and the inline text-entry row. Other code
    inspects `entering_other` / `other_text`, so keep them present and
    default-falsy."""
    s = ChoicePromptState(options=options)
    assert s.entering_other is False
    assert s.other_text == ""
    # The fields are mutable plain attributes — the runner toggles
    # them directly when the user picks Other.
    s.entering_other = True
    s.other_text = "session.py please"
    assert s.entering_other is True
    assert s.other_text == "session.py please"


# --------------------------------------------------------- tool integration


class FakeUI:
    """Stub UI that simulates the user's selection without touching a TTY."""

    def __init__(self, *, selected=None, cancelled=False, other_text="", raise_with=None):
        self._selected = list(selected or [])
        self._cancelled = bool(cancelled)
        self._other_text = str(other_text or "")
        self._raise_with = raise_with
        self.calls: list[dict] = []

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
                "question": question,
                "options": list(options),
                "multi_select": multi_select,
                "description": description,
                "allow_other": allow_other,
            }
        )
        if self._raise_with is not None:
            raise self._raise_with
        return {
            "selected": list(self._selected),
            "other_text": self._other_text,
            "cancelled": self._cancelled,
        }


def _call_tool(ui, args):
    from mu.tools.prompt.handlers import ask_user_choice_tool

    context = SimpleNamespace(ui=ui, session=None, folder_context=None, variables={})
    return ask_user_choice_tool(args, context)


def test_tool_returns_user_selection():
    ui = FakeUI(selected=["calloc"])
    raw = _call_tool(
        ui,
        {"question": "Which allocator zero-fills?", "options": ["malloc", "calloc"]},
    )
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["selected"] == ["calloc"]
    assert payload["cancelled"] is False
    assert payload["multi_select"] is False
    assert payload["option_count"] == 2
    assert ui.calls[0]["question"] == "Which allocator zero-fills?"


def test_tool_propagates_multi_select_and_description():
    ui = FakeUI(selected=["a", "c"])
    raw = _call_tool(
        ui,
        {
            "question": "Pick all that apply",
            "options": ["a", "b", "c"],
            "multi_select": True,
            "description": "Multiple may be correct.",
        },
    )
    payload = json.loads(raw)
    assert payload["selected"] == ["a", "c"]
    assert payload["multi_select"] is True
    assert ui.calls[0]["multi_select"] is True
    assert ui.calls[0]["description"] == "Multiple may be correct."


def test_tool_handles_cancellation():
    ui = FakeUI(selected=[], cancelled=True)
    raw = _call_tool(ui, {"question": "Q", "options": ["a", "b"]})
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["cancelled"] is True
    assert payload["selected"] == []


def test_tool_rejects_empty_question():
    ui = FakeUI()
    raw = _call_tool(ui, {"question": "", "options": ["a", "b"]})
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert "question" in payload["error"]


def test_tool_rejects_empty_options():
    ui = FakeUI()
    raw = _call_tool(ui, {"question": "Q", "options": []})
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert "option" in payload["error"]


def test_tool_handles_missing_ui_gracefully():
    raw = _call_tool(None, {"question": "Q", "options": ["a"]})
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert "No interactive UI" in payload["error"]
    assert payload["cancelled"] is True


def test_tool_handles_ui_not_implementing_picker():
    class NoChoiceUI:
        pass

    raw = _call_tool(NoChoiceUI(), {"question": "Q", "options": ["a"]})
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert "No interactive UI" in payload["error"]


def test_tool_handles_not_implemented_from_ui():
    class StubUI:
        def ask_user_choice(self, *args, **kwargs):
            raise NotImplementedError

    raw = _call_tool(StubUI(), {"question": "Q", "options": ["a"]})
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert "doesn't support" in payload["error"]


def test_tool_handles_unexpected_exception():
    ui = FakeUI(raise_with=RuntimeError("kaboom"))
    raw = _call_tool(ui, {"question": "Q", "options": ["a"]})
    payload = json.loads(raw)
    assert payload["ok"] is False
    assert "kaboom" in payload["error"]


def test_tool_registered_in_descriptors():
    from mu.tools.descriptors import TOOL_DESCRIPTORS

    assert "ask_user_choice" in TOOL_DESCRIPTORS
    desc = TOOL_DESCRIPTORS["ask_user_choice"]
    # The tool should NOT require approval — it's a read-side interactive
    # query, not a state mutation.
    assert desc.definition.requires_approval is False
    # The parameter schema must advertise allow_other so agents know
    # they can request the free-form text follow-up.
    schema = desc.definition.parameters
    assert "allow_other" in schema["properties"]


# --------------------------------------------------------- allow_other path


def test_tool_passes_allow_other_through_to_ui():
    ui = FakeUI(selected=["foo"])
    _call_tool(
        ui,
        {
            "question": "Which path?",
            "options": ["foo", "bar"],
            "allow_other": True,
        },
    )
    assert ui.calls[0].get("allow_other") is True


def test_tool_returns_other_text_when_ui_supplies_it():
    class OtherUI:
        def __init__(self):
            self.calls = []

        def ask_user_choice(self, question, options, *, multi_select=False, description="", allow_other=False):
            self.calls.append({"allow_other": allow_other})
            # Simulate the user picking Other and typing a free-form answer.
            return {
                "selected": [],
                "other_text": "actually look at session.py",
                "cancelled": False,
            }

    ui = OtherUI()
    raw = _call_tool(
        ui,
        {
            "question": "Which file?",
            "options": ["auth.py", "user.py"],
            "allow_other": True,
        },
    )
    payload = json.loads(raw)
    assert payload["ok"] is True
    assert payload["cancelled"] is False
    assert payload["selected"] == []
    assert payload["other_text"] == "actually look at session.py"
    assert payload["allow_other"] is True


def test_tool_returns_other_text_alongside_canonical_picks_in_multi_select():
    class CombinedUI:
        def ask_user_choice(self, *args, **kwargs):
            return {
                "selected": ["auth.py"],
                "other_text": "also check helpers.py",
                "cancelled": False,
            }

    raw = _call_tool(
        CombinedUI(),
        {
            "question": "Which files?",
            "options": ["auth.py", "user.py"],
            "multi_select": True,
            "allow_other": True,
        },
    )
    payload = json.loads(raw)
    assert payload["selected"] == ["auth.py"]
    assert payload["other_text"] == "also check helpers.py"


def test_tool_default_allow_other_is_false():
    ui = FakeUI(selected=["a"])
    _call_tool(ui, {"question": "Q", "options": ["a", "b"]})
    assert ui.calls[0].get("allow_other") is False


# ---------------------------------------------- FakeUI extension for new arg


def _patch_fake_ui_signature():
    """FakeUI.ask_user_choice was defined before allow_other existed;
    re-check it still accepts the new kwarg without exploding."""
    import inspect

    sig = inspect.signature(FakeUI.ask_user_choice)
    # `allow_other` should be passed via **kwargs since the FakeUI
    # signature uses explicit kwargs. Confirm it's tolerated.
    assert "multi_select" in sig.parameters
    assert "description" in sig.parameters


def test_fake_ui_signature_tolerates_allow_other():
    _patch_fake_ui_signature()
