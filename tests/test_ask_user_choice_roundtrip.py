"""tool_result_payload_unwrap / P2-T3: ask_user_choice selections reach the model.

The TUI picker worked all along; the loss was in envelope normalisation
(see tests/test_envelope_payload_unwrap.py). These tests pin the whole
path — handler → envelope → Session unwrap → structured history record —
for the picker and for every other handler shape that used to collapse
to the literal "ok".
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from types import SimpleNamespace

import pytest

from mu.tools._envelope import _envelope_from_handler_result
from mu.tools.prompt.handlers import ask_user_choice_tool


class _StubUI:
    def __init__(self, result):
        self._result = result
        self.calls = []

    def ask_user_choice(self, question, options, *, multi_select=False, description="", allow_other=False):
        self.calls.append((question, list(options), multi_select, description, allow_other))
        return dict(self._result)


class _S:
    """Minimal stand-in exposing Session._unwrap_tool_envelope's dependency."""

    def _parse_json_result(self, r):
        try:
            v = json.loads(str(r))
            return v if isinstance(v, dict) else {"value": v}
        except Exception:
            return {"preview": str(r)[:260]}


def _model_text(tool_name: str, handler_raw: str) -> tuple[str, dict]:
    from mu.session.session import Session

    envelope = _envelope_from_handler_result(tool_name, handler_raw)
    _, text = Session._unwrap_tool_envelope(_S(), json.dumps(envelope))
    return str(text), envelope


# ------------------------------------------------------------- picker round trip


def test_single_select_reaches_model_text():
    ui = _StubUI({"selected": ["Beta"], "other_text": "", "cancelled": False})
    ctx = SimpleNamespace(ui=ui, session=None)
    raw = ask_user_choice_tool(
        {"question": "Pick one", "options": ["Alpha", "Beta", "Gamma"]}, ctx
    )
    text, env = _model_text("ask_user_choice", raw)
    assert text != "ok"
    payload = json.loads(text)
    assert payload["selected"] == ["Beta"]
    assert payload["cancelled"] is False
    assert env["data"]["selected"] == ["Beta"]
    assert ui.calls[0][1] == ["Alpha", "Beta", "Gamma"]


def test_multi_select_with_other_text_reaches_model_text():
    ui = _StubUI({"selected": ["Alpha", "Gamma"], "other_text": "also delta", "cancelled": False})
    ctx = SimpleNamespace(ui=ui, session=None)
    raw = ask_user_choice_tool(
        {
            "question": "Pick many",
            "options": ["Alpha", "Beta", "Gamma"],
            "multi_select": True,
            "allow_other": True,
        },
        ctx,
    )
    text, _ = _model_text("ask_user_choice", raw)
    payload = json.loads(text)
    assert payload["selected"] == ["Alpha", "Gamma"]
    assert payload["other_text"] == "also delta"
    assert payload["multi_select"] is True


def test_cancelled_picker_is_visible_to_model():
    ui = _StubUI({"selected": [], "other_text": "", "cancelled": True})
    ctx = SimpleNamespace(ui=ui, session=None)
    raw = ask_user_choice_tool({"question": "Q", "options": ["A"]}, ctx)
    text, _ = _model_text("ask_user_choice", raw)
    payload = json.loads(text)
    assert payload["cancelled"] is True and payload["selected"] == []


def test_structured_history_record_carries_selection(tmp_path, monkeypatch):
    """The persisted tool_result envelope (what the model sees on later
    iterations and what the GUI renders) must not store raw='ok'."""
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path / "h"))
    from mu.session.session import Session, SessionManager
    from mu.session.tools_glue import build_structured_tool_result
    from providers.base import LLMProvider, ProviderResponse

    class _P(LLMProvider):
        def __init__(self):
            super().__init__("dummy")

        def get_available_models(self):
            return ["dummy"]

        def generate(self, messages, system_prompt=None, thinking=False, tools=None):
            return ProviderResponse(text="", parts=[], input_tokens=0, output_tokens=0, total_tokens=0)

        def upload_file(self, *a, **kw):
            return None

    sm = SessionManager()
    session = Session(_P(), False, "sys", sm)
    ui = _StubUI({"selected": ["Beta"], "other_text": "", "cancelled": False})
    raw = ask_user_choice_tool(
        {"question": "Q", "options": ["Alpha", "Beta"]}, SimpleNamespace(ui=ui, session=None)
    )
    envelope = _envelope_from_handler_result("ask_user_choice", raw)
    structured = build_structured_tool_result(
        session, "ask_user_choice", {"question": "Q", "options": ["Alpha", "Beta"]},
        json.dumps(envelope),
    )
    assert structured["raw"] != "ok"
    assert "Beta" in str(structured["raw"])
    assert "Beta" in str(structured["summary"])
    assert structured["telemetry"]["raw_char_count"] > 2


# ------------------------------------------------------- handler-shape audit

# Minimal `{ok: true, ...}` payloads mirroring what each affected handler
# emits (payload top-level, no message/data). Every one of these produced
# the literal "ok" at the model seam before the fix.
_AFFECTED_SHAPES = {
    "ask_user_choice": {"ok": True, "selected": ["Beta"], "other_text": "", "cancelled": False},
    "get_execution_state": {"ok": True, "feature_id": "f", "next_task": {"id": 3, "title": "T"}, "blocked": []},
    "create_task": {"ok": True, "task_id": 4, "title": "T", "phase_id": 1},
    "update_task_status": {"ok": True, "task_id": 4, "status": "completed", "verified_exit_criteria": ["a"]},
    "review_completed_tasks": {"ok": True, "review_id": "r1", "task_id": 4, "issues": 2},
    "review_all_completed_tasks": {"ok": True, "created": 3, "review_ids": ["r1", "r2", "r3"]},
    "set_session_goal": {"ok": True, "goal": "Ship it", "cleared": False},
    "create_feature": {"ok": True, "feature_id": "f", "status": "awaiting_approval"},
    "create_phases": {"ok": True, "feature_id": "f", "phases": 3},
    "block_task": {"ok": True, "task_id": 4, "status": "blocked", "reason": "need input"},
    "resume_task": {"ok": True, "task_id": 4, "status": "in_progress"},
    "approve_feature_task": {"ok": True, "feature_id": "f", "approved": True},
}


@pytest.mark.parametrize("tool_name", sorted(_AFFECTED_SHAPES))
def test_affected_handler_shapes_do_not_collapse_to_ok(tool_name):
    shape = _AFFECTED_SHAPES[tool_name]
    text, env = _model_text(tool_name, json.dumps(shape))
    assert text.strip().lower() != "ok", tool_name
    payload = json.loads(text)
    for key, value in shape.items():
        if key == "ok":
            continue
        assert payload[key] == value, (tool_name, key)
        assert env["data"][key] == value, (tool_name, key)


# ------------------------------------------------------------ pty smoke test

pexpect = pytest.importorskip("pexpect", reason="pexpect not installed")

_CHILD = """
import sys, json
sys.path.insert(0, {root!r})
mode = sys.argv[1]
from mu.ui.choice_prompt import run_interactive_choice_prompt
if mode == "single":
    r = run_interactive_choice_prompt("Q?", ["Alpha", "Beta", "Gamma"])
elif mode == "multi":
    r = run_interactive_choice_prompt("Q?", ["Alpha", "Beta", "Gamma"], multi_select=True)
else:
    r = run_interactive_choice_prompt("Q?", ["Alpha", "Beta"], allow_other=True)
print("RESULT=" + json.dumps(r), flush=True)
"""


def _has_pty() -> bool:
    try:
        import pty  # noqa: F401

        fd, _ = os.openpty()
        os.close(fd)
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _has_pty(), reason="no pty available")
@pytest.mark.parametrize(
    "mode,keys,expected",
    [
        ("single", ["\x1b[B", "\r"], {"selected": ["Beta"], "other_text": ""}),
        ("multi", [" ", "\x1b[B", "\x1b[B", " ", "\r"], {"selected": ["Alpha", "Gamma"], "other_text": ""}),
        ("other", ["\x1b[B", "\x1b[B", "\r", "hello there", "\r"], {"selected": [], "other_text": "hello there"}),
    ],
)
def test_interactive_picker_returns_selection_via_pty(tmp_path, mode, keys, expected):
    import time

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = tmp_path / "pick_child.py"
    script.write_text(_CHILD.format(root=root))
    child = pexpect.spawn(
        sys.executable, [str(script), mode], encoding="utf-8", timeout=20, dimensions=(40, 120),
        env={**os.environ, "TERM": "xterm-256color"},
    )
    try:
        child.expect("Alpha")
        for key in keys:
            time.sleep(0.15)
            child.send(key)
        child.expect(r"RESULT=(.*)\r?\n")
        result = json.loads(child.match.group(1).strip())
    finally:
        try:
            child.close(force=True)
        except Exception:
            pass
    assert result["cancelled"] is False
    for key, value in expected.items():
        assert result[key] == value, (mode, key)
