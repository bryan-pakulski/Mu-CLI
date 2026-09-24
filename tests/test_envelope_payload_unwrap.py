"""tool_result_payload_unwrap / P1-T1: handler payloads survive normalisation.

Handlers that return ``{"ok": true, <payload>}`` without ``message``/``data``
used to be backfilled with ``message="ok"`` and ``data={}``; the model-facing
unwrap then delivered the literal string "ok" and the user's ask_user_choice
selection (and 11 other tools' results) vanished.
"""
from __future__ import annotations

import json

from mu.tools._envelope import _envelope_from_handler_result


def test_top_level_payload_lands_in_data_and_message():
    raw = json.dumps(
        {"ok": True, "selected": ["Beta"], "other_text": "", "cancelled": False}
    )
    env = _envelope_from_handler_result("ask_user_choice", raw)
    assert env["ok"] is True
    assert env["data"]["selected"] == ["Beta"]
    assert env["data"]["cancelled"] is False
    assert "Beta" in env["message"]
    assert env["message"] != "ok"
    # Backward compatibility: payload keys still readable at top level.
    assert env["selected"] == ["Beta"]


def test_ok_without_payload_keeps_bare_ok_message():
    env = _envelope_from_handler_result("noop_tool", json.dumps({"ok": True}))
    assert env["message"] == "ok"
    assert env["data"] == {}


def test_explicit_message_and_data_are_not_altered():
    env = _envelope_from_handler_result(
        "custom_tool",
        {"ok": True, "message": "custom", "data": {"a": 1}, "extra": 2},
    )
    assert env["message"] == "custom"
    assert env["data"] == {"a": 1}
    assert env["extra"] == 2


def test_error_payload_keeps_error_message_but_preserves_payload_in_data():
    env = _envelope_from_handler_result(
        "ask_user_choice",
        json.dumps({"ok": False, "error": "no ui", "selected": [], "cancelled": True}),
    )
    assert env["ok"] is False
    assert env["message"] == "no ui"
    assert env["data"]["cancelled"] is True


def test_dict_handler_result_gets_same_treatment_as_json_string():
    env = _envelope_from_handler_result(
        "get_execution_state",
        {"ok": True, "next_task": {"id": 3, "title": "Do X"}, "blocked": []},
    )
    assert env["data"]["next_task"]["title"] == "Do X"
    assert "Do X" in env["message"]


def test_canonical_envelope_passthrough_untouched():
    canonical = {
        "ok": True, "error_code": None, "message": "done", "data": {"k": 1},
        "artifacts": [], "telemetry": {"tool_name": "x"},
    }
    env = _envelope_from_handler_result("x", json.dumps(canonical))
    assert env["message"] == "done" and env["data"] == {"k": 1}


# ------------------------------------------------------------- T2: unwrap seam


def _unwrap(raw):
    from mu.session.session import Session

    class _S:
        def _parse_json_result(self, r):
            try:
                v = json.loads(str(r))
                return v if isinstance(v, dict) else {"value": v}
            except Exception:
                return {"preview": str(r)[:260]}

    return Session._unwrap_tool_envelope(_S(), raw)


def _canonical(**over):
    env = {"ok": True, "error_code": None, "message": "", "data": {},
           "artifacts": [], "telemetry": {"tool_name": "t"}}
    env.update(over)
    return json.dumps(env)


def test_unwrap_prefers_data_over_bare_ok_message():
    _, text = _unwrap(_canonical(message="ok", data={"selected": ["Beta"], "cancelled": False}))
    assert "Beta" in text
    assert json.loads(text)["selected"] == ["Beta"]


def test_unwrap_prefers_top_level_payload_over_bare_ok_when_data_empty():
    """Older persisted envelopes left the payload top-level with data={}."""
    _, text = _unwrap(_canonical(message="ok", data={}, selected=["Gamma"], cancelled=False))
    assert json.loads(text)["selected"] == ["Gamma"]


def test_unwrap_keeps_real_message_over_data():
    _, text = _unwrap(_canonical(message="Wrote 3 files", data={"files": 3}))
    assert text == "Wrote 3 files"


def test_unwrap_bare_ok_without_payload_stays_ok():
    _, text = _unwrap(_canonical(message="ok", data={}))
    assert text == "ok"


def test_unwrap_success_ack_treated_like_ok():
    _, text = _unwrap(_canonical(message="success", data={"count": 2}))
    assert json.loads(text) == {"count": 2}
