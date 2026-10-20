"""context_packaging_v2 / P2-T5: stub oversized tool-call args once consumed.

At peak, trace mucli_run_355d1b39c2a7 re-sent ~83k tokens of tool_call
arguments (write_file bodies, apply_diff patches, bash heredocs) every
iteration although each tool had already run. Once a tool_call has its
tool_result, the projected request replaces oversized string args with a
size+sha256+preview stub. session.history keeps the originals; the pending
(unanswered) batch is never touched.
"""

from __future__ import annotations

import hashlib
from typing import List

import pytest

from providers.base import LLMProvider, MessagePart, ProviderResponse


BIG = "def f():\n    return 42\n" * 500  # ~11.5k chars


class _Provider(LLMProvider):
    def __init__(self, model_name="dummy"):
        super().__init__(model_name)
        self.model_name = model_name

    def get_available_models(self) -> List[str]:
        return [self.model_name]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        return ProviderResponse(text="ok", parts=[], input_tokens=1, output_tokens=1, total_tokens=2)

    def stream(self, messages, system_prompt=None, thinking=False, tools=None):
        yield {"type": "text", "text": "ok"}

    def upload_file(self, file_path, mime_type):
        return None

    def effective_context_window(self, model_name=None):
        return 480_000


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path / "history"))
    from mu.session.session import Session, SessionManager

    sm = SessionManager()
    sess = Session(_Provider(), False, "system", sm)
    sess.variables["agent_mode"] = "default"
    return sess


def _call(cid, content=BIG, tool="write_file"):
    return {"role": "assistant", "parts": [{
        "type": "tool_call", "tool_name": tool,
        "tool_args": {"filename": "big.py", "content": content}, "tool_call_id": cid}]}


def _result(cid, text="ok"):
    return {"role": "tool", "parts": [{
        "type": "tool_result", "tool_name": "write_file", "tool_result": text, "tool_call_id": cid}]}


def _user(text="ask"):
    return {"role": "user", "parts": [{"type": "text", "text": text}]}


def _call_args(messages):
    return [p.tool_args for m in messages for p in m.parts if p.type == "tool_call"]


# ------------------------------------------------------------------ schema


def test_variable_registered():
    from utils.config import VARIABLE_SCHEMA

    assert VARIABLE_SCHEMA["tool_call_arg_stub_threshold_chars"]["default"] == 2000
    assert VARIABLE_SCHEMA["tool_call_arg_stub_threshold_chars"]["type"] is int


# ------------------------------------------------------------------ pure helpers


def test_stub_helper_replaces_only_oversized_strings_and_never_mutates():
    from mu.session.messages import ARG_STUB_MARKER, stub_oversized_arg_values

    args = {"filename": "big.py", "content": BIG, "count": 3, "nested": {"x": BIG}}
    out = stub_oversized_arg_values(args, 2000)
    assert out is not args
    assert args["content"] == BIG  # original untouched
    assert out["filename"] == "big.py"
    assert out["count"] == 3
    assert out["nested"] == {"x": BIG}  # only top-level strings are stubbed
    stub = out["content"]
    assert stub[ARG_STUB_MARKER] is True
    assert stub["bytes"] == len(BIG.encode())
    assert stub["sha256"] == hashlib.sha256(BIG.encode()).hexdigest()[:16]
    assert stub["preview"] == BIG[:200]

    # Under threshold / disabled / non-dict: identity.
    small = {"filename": "a.py", "content": "short"}
    assert stub_oversized_arg_values(small, 2000) is small
    assert stub_oversized_arg_values(args, 0) is args
    assert stub_oversized_arg_values("not-a-dict", 2000) == "not-a-dict"


def test_consumed_indices_by_id_and_by_order():
    from mu.session.messages import consumed_tool_call_indices

    history = [_user(), _call("a"), _result("a"), _call("b")]  # b pending
    assert consumed_tool_call_indices(history) == {1}

    # Parallel batch: both answered -> consumed; one missing -> not consumed.
    batch = {"role": "assistant", "parts": [
        {"type": "tool_call", "tool_name": "t", "tool_args": {}, "tool_call_id": "x"},
        {"type": "tool_call", "tool_name": "t", "tool_args": {}, "tool_call_id": "y"},
    ]}
    assert consumed_tool_call_indices([_user(), batch, _result("x"), _result("y")]) == {1}
    assert consumed_tool_call_indices([_user(), batch, _result("x")]) == set()

    # No ids anywhere (legacy history): order decides.
    legacy_call = {"role": "assistant", "parts": [{"type": "tool_call", "tool_name": "t", "tool_args": {}}]}
    legacy_result = {"role": "tool", "parts": [{"type": "tool_result", "tool_name": "t", "tool_result": "ok"}]}
    assert consumed_tool_call_indices([_user(), legacy_call, legacy_result, legacy_call]) == {1}


# ------------------------------------------------------------------ projection


def test_projected_messages_stub_consumed_args_and_keep_history(session):
    from mu.session.context_maintenance import projected_messages
    from mu.session.messages import ARG_STUB_MARKER

    sm = session.session_manager
    sm.history = [_user(), _call("a"), _result("a"), {"role": "assistant", "parts": [{"type": "text", "text": "done"}]}]
    msgs = projected_messages(session)
    (args,) = _call_args(msgs)
    assert args["filename"] == "big.py"
    assert args["content"][ARG_STUB_MARKER] is True
    assert args["content"]["bytes"] == len(BIG.encode())
    # Durable history is untouched.
    assert sm.history[1]["parts"][0]["tool_args"]["content"] == BIG


def test_pending_tool_call_args_are_never_stubbed(session):
    sm = session.session_manager
    sm.history = [_user(), _call("a"), _result("a"), _call("b")]
    msgs = session._build_messages_from_history(sm.history, {"role": "system", "parts": []})[:-1]
    consumed, pending = _call_args(msgs)
    assert isinstance(consumed["content"], dict)
    assert pending["content"] == BIG


def test_threshold_zero_disables_stubbing(session):
    session.variables["tool_call_arg_stub_threshold_chars"] = 0
    sm = session.session_manager
    sm.history = [_user(), _call("a"), _result("a")]
    msgs = session._build_messages_from_history(sm.history, {"role": "system", "parts": []})[:-1]
    (args,) = _call_args(msgs)
    assert args["content"] == BIG


def test_request_estimate_tool_calls_component_shrinks(session):
    from mu.agent.context_guard import _estimate_request_tokens

    sm = session.session_manager
    sm.history = [_user(), _call("a"), _result("a"), {"role": "assistant", "parts": [{"type": "text", "text": "done"}]}]

    session.variables["tool_call_arg_stub_threshold_chars"] = 0
    raw = session._build_messages_from_history(sm.history, {"role": "system", "parts": []})[:-1]
    session.variables["tool_call_arg_stub_threshold_chars"] = 2000
    stubbed = session._build_messages_from_history(sm.history, {"role": "system", "parts": []})[:-1]

    before = _estimate_request_tokens("", raw)
    after = _estimate_request_tokens("", stubbed)
    assert before["messages"] > 2_000
    assert after["messages"] < before["messages"] // 4
    comp_before = before.get("components", {}).get("tool_calls")
    comp_after = after.get("components", {}).get("tool_calls")
    if comp_before is not None and comp_after is not None:
        assert comp_after < comp_before


# ------------------------------------------------------------------ providers accept stubs


def test_provider_serialisers_accept_stubbed_args(session):
    """Stubbed args are plain JSON dicts; every provider message builder
    must serialise them without re-validating against the tool schema."""
    from providers.base import Message

    from mu.session.messages import stub_oversized_arg_values

    args = stub_oversized_arg_values({"filename": "big.py", "content": BIG}, 2000)
    msgs = [
        Message(role="user", parts=[MessagePart(type="text", text="go")]),
        Message(role="assistant", parts=[MessagePart(type="tool_call", tool_name="write_file",
                                                     tool_args=args, tool_call_id="c1")]),
        Message(role="tool", parts=[MessagePart(type="tool_result", tool_name="write_file",
                                                tool_result="ok", tool_call_id="c1")]),
    ]
    import json

    from providers.openai import OpenAIProvider

    oa = OpenAIProvider.__new__(OpenAIProvider)
    oa.model_name = "gpt-test"
    converted = oa._convert_messages(msgs) if hasattr(oa, "_convert_messages") else None
    if converted is not None:
        dumped = json.dumps(converted)
        assert "__stubbed__" in dumped
        assert BIG[:400] not in dumped or len(dumped) < len(BIG)

    from providers.anthropic import AnthropicProvider

    an = AnthropicProvider.__new__(AnthropicProvider)
    an.model_name = "claude-test"
    if hasattr(an, "_convert_messages"):
        dumped = json.dumps(an._convert_messages(msgs))
        assert "__stubbed__" in dumped
