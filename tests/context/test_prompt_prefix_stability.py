"""context_packaging_v2 / P4-T9: byte-stable system prompt across a turn.

Volatile per-iteration blocks (memory snapshot, scratchpad, eviction
notices, delegated work, peer coordination, wall-clock) move out of the
system prompt into one trailing request-only user message. The system
prompt hash therefore stays constant across iterations even when the model
saves a memory or a scratchpad note mid-turn (trace mucli_run_355d1b39c2a7:
system prompt drifted 5125->7264 tokens within a turn; 114/274 iterations
were prefix-cache misses).
"""

from __future__ import annotations

import hashlib
import json
from typing import List

import pytest

from providers.base import LLMProvider, MessagePart, ProviderResponse


class _Recorder(LLMProvider):
    """Three iterations: save_scratchpad -> save_memory -> final text.
    Records the exact system prompt and messages of every call."""

    def __init__(self, model_name="rec"):
        super().__init__(model_name)
        self.model_name = model_name
        self.calls: List[dict] = []

    def get_available_models(self):
        return [self.model_name]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        self.calls.append({"system_prompt": system_prompt or "", "messages": list(messages)})
        n = len(self.calls)
        if n == 1:
            part = MessagePart(type="tool_call", tool_name="save_scratchpad",
                               tool_args={"content": "hypothesis: drift is 1.67x"}, tool_call_id="c1")
        elif n == 2:
            part = MessagePart(type="tool_call", tool_name="save_memory",
                               tool_args={"content": "verified: nudge never fired", "kind": "finding"},
                               tool_call_id="c2")
        else:
            part = MessagePart(type="text", text="done")
        return ProviderResponse(text=part.text or "", parts=[part],
                                input_tokens=100, output_tokens=2, total_tokens=102)

    def upload_file(self, *a, **kw):
        return None

    def effective_context_window(self, model_name=None):
        return 480_000


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path / "history"))
    from mu.session.session import Session, SessionManager

    sm = SessionManager()
    sess = Session(_Recorder(), False, "system", sm)
    sess.variables["agent_mode"] = "default"
    sess.variables["yolo"] = True
    return sess


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _runtime_msgs(messages):
    from mu.session.runtime_state import is_runtime_state_message
    return [m for m in messages if is_runtime_state_message(m)]


# ------------------------------------------------------------------ variable


def test_variable_registered():
    from utils.config import VARIABLE_SCHEMA

    assert VARIABLE_SCHEMA["prompt_prefix_stable"]["default"] is True


# ------------------------------------------------------------------ helpers


def test_runtime_state_helpers_roundtrip():
    from types import SimpleNamespace

    from providers.base import Message
    from mu.session.runtime_state import (
        RUNTIME_STATE_HEADER, append_runtime_state, is_runtime_state_message,
        render_runtime_state, strip_runtime_state,
    )

    assert render_runtime_state([]) == ""
    block = render_runtime_state(["LAYER 3 — Turn scratchpad snapshot:\n- note", "", "Current date/time: x"])
    assert block.startswith(RUNTIME_STATE_HEADER)
    assert "scratchpad" in block and "Current date/time" in block

    sess = SimpleNamespace(_runtime_state_block=block)
    base = [Message(role="user", parts=[MessagePart(type="text", text="hi")])]
    out = append_runtime_state(sess, base)
    assert len(out) == 1 and is_runtime_state_message(out[-1])  # user tail -> extra part
    assert out[-1].parts[0].text == "hi"
    assert base[0].parts == [MessagePart(type="text", text="hi")]  # input not mutated
    # Idempotent: a second append replaces rather than stacks.
    sess._runtime_state_block = block + "\nmore"
    again = append_runtime_state(sess, out)
    assert len(again) == 1 and len(again[-1].parts) == 2
    assert again[-1].parts[-1].text.endswith("more")
    assert strip_runtime_state(again) == base
    # Tool-result tail -> separate trailing user message.
    tool_tail = base + [Message(role="tool", parts=[MessagePart(type="tool_result", tool_name="t", tool_result="ok")])]
    out2 = append_runtime_state(sess, tool_tail)
    assert len(out2) == 3 and is_runtime_state_message(out2[-1]) and len(out2[-1].parts) == 1
    assert strip_runtime_state(out2) == tool_tail
    # Empty block -> untouched.
    sess._runtime_state_block = ""
    assert append_runtime_state(sess, base) is base or append_runtime_state(sess, base) == base


# ------------------------------------------------------------------ live turn


def test_system_prompt_hash_stable_while_memory_and_scratchpad_change(session):
    session.send_message("investigate compaction")
    calls = session.provider.calls
    assert len(calls) == 3

    hashes = [_hash(c["system_prompt"]) for c in calls]
    assert hashes[0] == hashes[1] == hashes[2], hashes
    # Volatile content did change between iterations...
    for c in calls:
        assert "Turn scratchpad snapshot" not in c["system_prompt"]
        assert "Persisted working memory snapshot" not in c["system_prompt"]
        assert "Current date/time" not in c["system_prompt"]
    from mu.session.runtime_state import runtime_state_text
    rs = [_runtime_msgs(c["messages"]) for c in calls]
    assert all(len(r) == 1 for r in rs), [len(r) for r in rs]
    assert all(c["messages"][-1] is r[0] for c, r in zip(calls, rs))  # trailing
    # Iteration 1: the live user prompt is the tail -> block rides as its
    # last PART (user text stays first). Later iterations: tool-result tail
    # -> separate trailing user message.
    assert calls[0]["messages"][-1].parts[0].text == "investigate compaction"
    # user text, the turn context (the turn-scoped goal), then the runtime state.
    assert len(calls[0]["messages"][-1].parts) == 3
    assert _turn_context_parts(calls[0]["messages"][-1]) == [calls[0]["messages"][-1].parts[1]]
    assert len(calls[1]["messages"][-1].parts) == 1
    texts = [runtime_state_text(r[0]) for r in rs]
    assert "Current date/time" in texts[0]
    assert "drift is 1.67x" not in texts[0]
    assert "drift is 1.67x" in texts[1]          # scratchpad saved in iter 1 shows in iter 2
    assert "nudge never fired" in texts[2]       # memory saved in iter 2 shows in iter 3
    # ...and the runtime-state message is never persisted.
    from mu.session.runtime_state import is_runtime_state_message
    for msg in session.session_manager.history:
        assert not is_runtime_state_message(msg)
        for p in msg.get("parts", []):
            assert "RUNTIME STATE" not in str(p.get("text", ""))


def test_prefix_unstable_restores_in_prompt_rendering(session):
    session.variables["prompt_prefix_stable"] = False
    session.send_message("investigate compaction")
    calls = session.provider.calls
    assert len(calls) == 3
    hashes = [_hash(c["system_prompt"]) for c in calls]
    assert hashes[1] != hashes[0] or hashes[2] != hashes[1]  # legacy: prompt churns
    assert "Turn scratchpad snapshot" in calls[1]["system_prompt"]
    assert "Persisted working memory snapshot" in calls[2]["system_prompt"]
    assert all("Current date/time" in c["system_prompt"] for c in calls)
    assert all(not _runtime_msgs(c["messages"]) for c in calls)


def test_trace_request_record_reports_stable_hash_and_volatile_tokens(session, tmp_path, monkeypatch):
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path / "history"))
    session.send_message("investigate compaction")
    trace_dir = tmp_path / "history" / "trace"
    files = sorted(trace_dir.glob("*.jsonl")) if trace_dir.exists() else []
    if not files:
        pytest.skip("trace emitter not active in this fixture")
    recs = [json.loads(l) for f in files for l in f.read_text().splitlines() if l.strip()]
    reqs = [r for r in recs if r.get("type") == "request"]
    assert len(reqs) >= 3
    assert len({r["system_prompt_hash"] for r in reqs}) == 1
    assert all("volatile_block_tokens" in r for r in reqs)
    assert all(r["volatile_block_tokens"] > 0 for r in reqs)
    assert all("volatile_block" in r["component_tokens"] for r in reqs)


# ------------------------------------------------------------------ across turns


# Text only in the commit-message skill's body (the L1B index lists name + description).
BODY = "git diff --cached"


def _turn_context_parts(message):
    from mu.session.runtime_state import TURN_CONTEXT_HEADER
    return [p for p in message.parts if (p.text or "").startswith(TURN_CONTEXT_HEADER)]


def test_system_prompt_stable_across_turns_with_triggered_skill(session):
    """A skill triggered by one turn's message changes that turn's context, never the system
    prompt: tools + system + earlier history stay cacheable from turn to turn."""
    session.send_message("investigate compaction")
    session.send_message("write a commit message for this change")
    calls = session.provider.calls
    assert len(calls) == 4
    assert len({_hash(c["system_prompt"]) for c in calls}) == 1
    assert BODY not in calls[3]["system_prompt"]
    assert "commit-message" in calls[3]["system_prompt"]  # still listed in the L1B index
    assert "write a commit message for this change" not in calls[3]["system_prompt"]  # turn goal too

    # Turn 2's request carries the triggered body on turn 2's own user message.
    last = calls[3]["messages"]
    turn_msg = [m for m in last if m.role == "user" and m.parts[0].text == "write a commit message for this change"]
    assert len(turn_msg) == 1
    ctx = _turn_context_parts(turn_msg[0])
    assert len(ctx) == 1 and BODY in ctx[0].text
    assert "Active Goal (session_goal, pinned): write a commit message" in ctx[0].text
    # It sits after the user's own text and before the runtime-state tail.
    texts = [p.text for p in turn_msg[0].parts]
    assert texts[0] == "write a commit message for this change"
    assert texts.index(ctx[0].text) == 1
    # Turn 1's message carries no turn context in turn 2's request.
    first = [m for m in last if m.role == "user" and m.parts[0].text == "investigate compaction"]
    assert first and not _turn_context_parts(first[0])
    # Never persisted.
    for msg in session.session_manager.history:
        for p in msg.get("parts", []):
            assert "TURN CONTEXT" not in str(p.get("text", ""))


def test_turn_context_rides_every_iteration_of_its_turn(session):
    session.send_message("write a commit for the hypothesis notes")
    calls = session.provider.calls
    assert len(calls) == 3
    for call in calls:
        turn_msg = [m for m in call["messages"] if m.role == "user" and m.parts[0].text == "write a commit for the hypothesis notes"]
        assert len(turn_msg) == 1
        ctx = _turn_context_parts(turn_msg[0])
        assert len(ctx) == 1 and BODY in ctx[0].text
    # Identical bytes in every iteration, so the history prefix through it is reused.
    assert len({_turn_context_parts([m for m in c["messages"] if m.role == "user"][0])[0].text for c in calls}) == 1


def test_turn_context_falls_back_to_runtime_state_when_turn_message_is_absent():
    from types import SimpleNamespace

    from providers.base import Message
    from mu.session.runtime_state import (
        TURN_CONTEXT_HEADER, append_runtime_state, render_runtime_state, render_turn_context, runtime_state_text,
    )

    sm = SimpleNamespace(history=[{"role": "user", "parts": [{"type": "text", "text": "gone"}]}])
    sess = SimpleNamespace(
        session_manager=sm, _current_turn_start_index=0,
        _turn_context_block=render_turn_context(["LAYER 2M — Durable cross-session recall:\n- fact"]),
        _runtime_state_block=render_runtime_state(["Current date/time: x"]),
    )
    out = append_runtime_state(sess, [Message(role="user", parts=[MessagePart(type="text", text="other")])])
    text = runtime_state_text(out[-1])
    assert TURN_CONTEXT_HEADER in text and "- fact" in text and "Current date/time" in text
