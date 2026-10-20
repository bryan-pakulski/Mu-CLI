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
    assert len(calls[0]["messages"][-1].parts) == 2
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
