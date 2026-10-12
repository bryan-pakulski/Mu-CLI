"""Copied ``__stubbed__`` placeholders must never execute as real tool input.

Context packaging elides long arguments of already-answered tool calls in the
projected request. A model that copied such a placeholder into a new call had
bash run ``{__stubbed__: ...}`` literally ("command not found"), which made
the agent believe new commands over ~2000 chars were truncated.
"""

from __future__ import annotations

import json

from mu.session.messages import stub_oversized_arg_values
from mu.tools._dispatcher import _find_stub_placeholder, dispatch


def _stub(text: str = "x" * 3000) -> dict:
    return stub_oversized_arg_values({"command": text}, 2000)["command"]


def test_detects_dict_placeholder():
    assert _find_stub_placeholder({"command": _stub()}) == "command"


def test_detects_serialised_placeholder():
    assert _find_stub_placeholder({"content": json.dumps(_stub())}) == "content"


def test_ignores_real_long_values():
    assert _find_stub_placeholder({"command": "echo " + "a" * 5000}) is None
    assert _find_stub_placeholder({"content": '{"key": "__stubbed__ later in text"' + "x" * 100}) is None


def test_dispatch_rejects_placeholder_without_running(tmp_path):
    out = json.loads(dispatch("bash", {"command": _stub()}, None))
    assert out["ok"] is False
    assert out["error_code"] == "invalid_args"
    assert "no size limit" in out["message"]
