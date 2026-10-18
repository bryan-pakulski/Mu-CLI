"""Job stages never stop dead on a context overflow (documentation/design/ledger/no-overflow-stop.md).

The incident: an implement stage on the mucli harness died with "prompt is too long: about
1,015,872 tokens > 1,000,000 maximum (refused by the job relay before sending)". The relay counted
every Anthropic thinking signature (base64 up to ~60k chars, duplicated onto tool results) under
cl100k; the real request was ~210k tokens. Reactive recovery could not shrink an estimate made of
bytes it does not touch, the breaker re-raised, and the stage ended.

The fixture ``fixtures/overflow_transcript_shape.json`` records that transcript's shape (roles, part
kinds, signature and content sizes, which tool results carried a copied signature); the content
here is synthesised, deterministically.
"""
from __future__ import annotations

import base64
import json
import random
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from mu.agent.retry import is_context_overflow_error
from mu.session.session import Session, SessionManager
from providers.anthropic import _encode_thinking
from providers.base import (REPLAY_CURRENT_TURN, REPLAY_LAST_TOOL_TURN, REPLAY_NONE, LLMProvider, Message,
                            MessagePart, ProviderResponse, StreamEvent, project_replay)
from utils.token_estimator import (estimate_message_tokens, estimate_messages_tokens, estimate_payload_tokens,
                                   estimate_tokens, strip_replay_fields)

FIXTURE = Path(__file__).parent / "fixtures" / "overflow_transcript_shape.json"
_WORDS = ("const", "await", "client", "query", "rows", "deleted_at", "camera", "asset", "return", "function",
          "select", "from", "where", "update", "history", "undo", "service", "error", "status", "json")


def _filler(rng, chars):
    out, size = [], 0
    while size < chars:
        line = " ".join(rng.choice(_WORDS) for _ in range(rng.randint(4, 12))) + ";\n"
        out.append(line)
        size += len(line)
    return "".join(out)[:chars]


def _signature(rng, chars):
    raw = base64.b64encode(rng.randbytes(chars)).decode("ascii")[:chars]
    return _encode_thinking([{"type": "thinking", "thinking": "", "signature": raw}])


def build_transcript(seed=7):
    """The incident transcript, synthesised from its recorded shape."""
    rng = random.Random(seed)
    shape = json.loads(FIXTURE.read_text())["messages"]
    history, signatures = [], {}
    for index, message in enumerate(shape):
        parts = []
        for slot, part in enumerate(message["parts"]):
            kind = part["type"]
            entry = {"type": kind}
            if kind == "text":
                entry["text"] = _filler(rng, part["text_chars"])
            elif kind == "tool_call":
                entry["tool_name"] = part["tool_name"]
                entry["tool_args"] = {"command": _filler(rng, max(0, part["args_chars"] - 16))}
            elif kind == "tool_result":
                entry["tool_name"] = part["tool_name"]
                entry["tool_result"] = _filler(rng, part["result_chars"])
                entry["cache_key"] = f"key-{index}-{slot}"
            if part.get("sig_chars"):
                key = (part["sig_chars"], part.get("tool_name"))
                # Tool results carried a COPY of the preceding call's signature (the bug).
                if kind == "tool_result" and key in signatures:
                    entry["thought_signature"] = signatures[key]
                else:
                    entry["thought_signature"] = signatures.setdefault(key, _signature(rng, part["sig_chars"]))
            parts.append(entry)
        history.append({"role": message["role"], "parts": parts})
    return history


@pytest.fixture(scope="module")
def transcript():
    return build_transcript()


def _old_estimate(history):
    """How the relay counted before: cl100k of the serialized messages, signatures included."""
    return sum(estimate_tokens(json.dumps(message)) for message in history)


# ------------------------------------------------------------------ 1. estimation


def test_fixture_reproduces_the_incident_scale(transcript):
    assert len(transcript) == 81
    signed_results = [i for i, m in enumerate(transcript)
                      if any(p["type"] == "tool_result" and p.get("thought_signature") for p in m["parts"])]
    assert {74, 76, 78, 80} <= set(signed_results)
    assert _old_estimate(transcript) > 900_000  # the ~1M the relay refused


def test_visible_estimate_ignores_replay_blobs(transcript):
    visible = estimate_messages_tokens(transcript)
    assert visible < 150_000, visible  # near the visible size, not ~1M
    stripped = deepcopy(transcript)
    strip_replay_fields(stripped)
    assert estimate_messages_tokens(stripped) == visible
    # Message objects (the wire shape) estimate the same as their dict form.
    session = Session(_Provider(), False, "sys", SessionManager())
    messages = session._build_messages_from_history(transcript[1:], transcript[0])
    from mu.agent.context_guard import _estimate_messages_tokens
    assert _estimate_messages_tokens(messages) < 150_000


def test_relay_preflight_forwards_the_incident_request(tmp_path, transcript):
    from mu.jobs.core.artifacts import JobArtifacts
    from mu.jobs.sandbox.model_relay import ModelRelay, wire
    from mu.jobs.sandbox.policy import SandboxPolicy

    provider = _RelayProvider(window=1_000_000)
    art = JobArtifacts("j", str(tmp_path)).ensure()
    relay = ModelRelay(SimpleNamespace(id="j"), "implement", {"provider": "anthropic", "model": "claude-opus-5"},
                       SandboxPolicy(), art, provider_factory=lambda p, m: provider)
    relay.policy = relay.policy.__class__(**{**relay.policy.__dict__, "max_tokens": 10_000_000,
                                             "max_cost_usd": 1_000_000.0})
    # The wire payload as the incident's runtime image sent it: every signature, including the
    # copies on tool results.
    messages = [Message(m["role"], [MessagePart(**{k: v for k, v in p.items() if k != "cache_key"})
                                    for p in m["parts"]]) for m in transcript]
    payload = wire({"messages": messages, "system_prompt": "You are the implement stage.", "tools": []})
    assert _old_estimate([payload]) > 900_000  # what the old preflight measured
    events = list(relay.stream(relay.token, payload))
    assert provider.calls == 1 and any(e.get("kind") == "text_delta" for e in events)


def test_relay_still_refuses_a_request_certain_to_overflow(tmp_path):
    from mu.jobs.core.artifacts import JobArtifacts
    from mu.jobs.sandbox.model_relay import ModelRelay, RelayDenied, wire
    from mu.jobs.sandbox.policy import SandboxPolicy

    provider = _RelayProvider(window=2_000)
    art = JobArtifacts("j", str(tmp_path)).ensure()
    relay = ModelRelay(SimpleNamespace(id="j"), "implement", {"provider": "anthropic", "model": "claude-opus-5"},
                       SandboxPolicy(), art, provider_factory=lambda p, m: provider)
    payload = wire({"messages": [Message("user", [MessagePart("text", text="word " * 5_000)])]})
    with pytest.raises(RelayDenied) as denied:
        list(relay.stream(relay.token, payload))
    assert provider.calls == 0
    assert "refused by the job relay before sending" in str(denied.value)
    assert is_context_overflow_error(denied.value)  # the loop's ladder reacts to it


def test_budgets_and_pressure_gates_ignore_signatures(transcript):
    from mu.session.context_maintenance import context_status, projected_tokens

    def session_with(history):
        sm = SessionManager()
        sm.history = deepcopy(history)
        return Session(_Provider(), False, "sys", sm)

    stripped = deepcopy(transcript)
    strip_replay_fields(stripped)
    signed, plain = session_with(transcript), session_with(stripped)
    assert signed.session_manager.estimate_runtime_history_tokens() == \
        plain.session_manager.estimate_runtime_history_tokens()
    assert projected_tokens(signed) == projected_tokens(plain)
    assert context_status(signed)["request_tokens"] == context_status(plain)["request_tokens"]
    assert projected_tokens(signed) < 150_000


def test_payload_estimate_counts_system_prompt_and_tools():
    from providers.base import ToolDefinition
    tool = ToolDefinition(name="bash", description="Run a shell command", parameters={"type": "object"})
    message = Message("assistant", [MessagePart("tool_call", tool_name="bash", tool_args={"command": "ls"},
                                                thought_signature=_signature(random.Random(1), 40_000))])
    base = estimate_payload_tokens("", [message])
    assert base == estimate_tokens(json.dumps({"command": "ls"}))
    assert estimate_payload_tokens("system words", [message], [tool]) > base
    assert estimate_message_tokens({"parts": [{"type": "tool_result", "tool_result": "ok",
                                               "thought_signature": "x" * 50_000}]}) == estimate_tokens("ok")


# ------------------------------------------------------------------ 2. request projection


def _sig(text):
    return _encode_thinking([{"type": "thinking", "thinking": text, "signature": f"s-{text}"}])


def _tool_loop():
    return [
        Message("user", [MessagePart("text", text="first request")]),
        Message("assistant", [MessagePart("tool_call", tool_name="a", tool_args={}, thought_signature=_sig("one"))]),
        Message("tool", [MessagePart("tool_result", tool_name="a", tool_result="r1", thought_signature=_sig("one"))]),
        Message("assistant", [MessagePart("text", text="done with the first")]),
        Message("user", [MessagePart("text", text="second request")]),
        Message("assistant", [MessagePart("tool_call", tool_name="b", tool_args={}, thought_signature=_sig("two"))]),
        Message("tool", [MessagePart("tool_result", tool_name="b", tool_result="r2")]),
        Message("assistant", [MessagePart("tool_call", tool_name="c", tool_args={}, thought_signature=_sig("three"))]),
        Message("tool", [MessagePart("tool_result", tool_name="c", tool_result="r3")]),
        Message("user", [MessagePart("text", text="RUNTIME STATE")]),
    ]


def _signatures(messages):
    return [(i, p.type) for i, m in enumerate(messages) for p in m.parts if p.thought_signature]


def test_project_replay_scopes_and_never_mutates():
    messages = _tool_loop()
    before = deepcopy(messages)
    assert _signatures(project_replay(messages, REPLAY_LAST_TOOL_TURN)) == [(7, "tool_call")]
    # The trailing RUNTIME STATE message does not open a new turn.
    assert _signatures(project_replay(messages, REPLAY_CURRENT_TURN)) == [(5, "tool_call"), (7, "tool_call")]
    assert _signatures(project_replay(messages, REPLAY_NONE)) == []
    assert messages == before  # inputs untouched


def test_anthropic_drops_older_thinking_and_keeps_the_open_tool_loop():
    from providers.anthropic import AnthropicProvider
    provider = AnthropicProvider.__new__(AnthropicProvider)
    converted = provider._convert_messages(_tool_loop(), replay_thinking=True)
    thinking = [(i, b["thinking"]) for i, m in enumerate(converted) for b in m["content"]
                if isinstance(b, dict) and b.get("type") == "thinking"]
    assert thinking == [(next(i for i, m in enumerate(converted) if any(
        isinstance(b, dict) and b.get("id") == "mucli_toolu_3" for b in m["content"])), "three")]
    # A finished turn (latest assistant message is text) replays no thinking at all.
    finished = _tool_loop()[:4]
    converted = provider._convert_messages(finished, replay_thinking=True)
    assert not any(b.get("type") == "thinking" for m in converted for b in m["content"] if isinstance(b, dict))


def test_projection_leaves_session_history_unchanged(transcript):
    from providers.anthropic import AnthropicProvider
    sm = SessionManager()
    sm.history = deepcopy(transcript)
    session = Session(_Provider(), False, "sys", sm)
    snapshot = deepcopy(sm.history)
    messages = session._build_messages_from_history(sm.history[1:], sm.history[0])
    assert not any(p.thought_signature for m in messages for p in m.parts if p.type == "tool_result")
    AnthropicProvider.__new__(AnthropicProvider)._convert_messages(messages, replay_thinking=True)
    project_replay(messages, REPLAY_NONE)
    assert sm.history == snapshot


def test_relay_provider_sends_only_the_open_loop_signature(monkeypatch):
    from mu.jobs.sandbox import provider as relay_provider
    sent = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self):
            yield json.dumps({"kind": "done"})

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def stream(self, method, url, json=None, headers=None):
            sent.update(json)
            return _Response()

    monkeypatch.setattr(relay_provider.httpx, "Client", _Client)
    monkeypatch.setattr(relay_provider.httpx, "HTTPTransport", lambda **k: None)
    monkeypatch.setenv("MUCLI_MODEL_RELAY", "/tmp/none.sock")
    monkeypatch.setenv("MUCLI_STAGE_TOKEN", "t")
    anthropic = relay_provider.RelayProvider("anthropic", "claude-opus-5")
    list(anthropic.stream(_tool_loop()))
    signed = [(i, p["type"]) for i, m in enumerate(sent["messages"]) for p in m["parts"] if p.get("thought_signature")]
    assert signed == [(7, "tool_call")]
    gemini = relay_provider.RelayProvider("gemini", "gemini-3-pro")
    list(gemini.stream(_tool_loop()))
    signed = [(i, p["type"]) for i, m in enumerate(sent["messages"]) for p in m["parts"] if p.get("thought_signature")]
    assert signed == [(5, "tool_call"), (7, "tool_call")]


def test_openai_responses_replays_reasoning_for_the_current_turn_only():
    from providers.openai_responses import build_input, encode_reasoning

    def reasoning(tag):
        return encode_reasoning([{"type": "reasoning", "id": tag, "encrypted_content": "e" * 100}])

    messages = [
        Message("user", [MessagePart("text", text="old")]),
        Message("assistant", [MessagePart("tool_call", tool_name="a", tool_args={}, tool_call_id="c1",
                                          thought_signature=reasoning("r-old"))]),
        Message("tool", [MessagePart("tool_result", tool_name="a", tool_result="x", tool_call_id="c1")]),
        Message("assistant", [MessagePart("text", text="ok")]),
        Message("user", [MessagePart("text", text="new")]),
        Message("assistant", [MessagePart("tool_call", tool_name="b", tool_args={}, tool_call_id="c2",
                                          thought_signature=reasoning("r-new"))]),
        Message("tool", [MessagePart("tool_result", tool_name="b", tool_result="y", tool_call_id="c2")]),
    ]
    provider = SimpleNamespace(replay_scope=REPLAY_CURRENT_TURN, supports_input_mime=lambda *a: True)
    items = build_input(provider, messages)
    assert [i["id"] for i in items if i.get("type") == "reasoning"] == ["r-new"]


def test_gemini_drops_thought_signatures_from_earlier_turns():
    pytest.importorskip("google.genai")
    from providers.gemini import GeminiProvider
    provider = GeminiProvider.__new__(GeminiProvider)
    provider.model_name = "gemini-3-pro"
    messages = [
        Message("user", [MessagePart("text", text="old")]),
        Message("assistant", [MessagePart("tool_call", tool_name="a", tool_args={}, thought_signature="aa")]),
        Message("tool", [MessagePart("tool_result", tool_name="a", tool_result="x")]),
        Message("assistant", [MessagePart("text", text="ok")]),
        Message("user", [MessagePart("text", text="new")]),
        Message("assistant", [MessagePart("tool_call", tool_name="b", tool_args={}, thought_signature="bb")]),
        Message("tool", [MessagePart("tool_result", tool_name="b", tool_result="y")]),
    ]
    contents = provider._convert_to_gemini_contents(messages)
    signed = [p.thought_signature for c in contents for p in c.parts if getattr(p, "function_call", None)]
    assert signed == [None, bytes.fromhex("bb")]


# ------------------------------------------------------------------ 3. tool results never carry signatures


class _Provider(LLMProvider):
    """Scripted: one signed tool call, then a final answer."""

    def __init__(self, window=1_000_000):
        super().__init__("scripted")
        self.name, self.window, self.n = "scripted", window, 0

    def get_available_models(self):
        return ["scripted"]

    def effective_context_window(self, model_name=None):
        return self.window

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        self.n += 1
        if self.n == 1:
            return ProviderResponse(text="", parts=[MessagePart(type="tool_call", tool_name="list_dir",
                                                                tool_args={"path": "."},
                                                                thought_signature=_sig("plan"))],
                                    input_tokens=1, output_tokens=1, total_tokens=2)
        return ProviderResponse(text="### Task\nt\n### Progress\np", parts=[MessagePart(type="text", text="done")],
                                input_tokens=1, output_tokens=1, total_tokens=2)

    def upload_file(self, *a, **kw):
        return None


def _loop_session(tmp_path, provider, monkeypatch):
    from mu.workspace.folder_context import FolderContext
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path / "history"))
    session = Session(provider, False, "system", SessionManager())
    folder = FolderContext()
    folder.add_folder(str(tmp_path))
    session.folder_context = folder
    session.sync_runtime_state()
    session.variables["yolo"] = True
    return session


def test_tool_results_never_carry_thought_signature(tmp_path, monkeypatch):
    session = _loop_session(tmp_path, _Provider(), monkeypatch)
    session.send_message("look around")
    history = session.session_manager.history
    calls = [p for m in history for p in m.get("parts", []) if p.get("type") == "tool_call"]
    results = [p for m in history for p in m.get("parts", []) if p.get("type") == "tool_result"]
    assert calls and calls[0].get("thought_signature")  # the call keeps its replay data
    assert results and not any("thought_signature" in p for p in results)


def test_loading_old_history_strips_tool_result_signatures(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path))
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path / "history"))
    from mu.session import manager as manager_module
    sm = SessionManager()
    old = [
        {"role": "user", "parts": [{"type": "text", "text": "q"}]},
        {"role": "assistant", "parts": [{"type": "tool_call", "tool_name": "a", "tool_args": {},
                                         "thought_signature": "SIG"}]},
        {"role": "tool", "parts": [{"type": "tool_result", "tool_name": "a", "tool_result": "r",
                                    "thought_signature": "SIG"}]},
    ]
    monkeypatch.setattr(sm, "read_session_data", lambda name: {"history": deepcopy(old)})
    monkeypatch.setattr(manager_module, "ensure_thread_meta", lambda *a, **k: {})
    sm._load_session("legacy")
    assert sm.history[1]["parts"][0]["thought_signature"] == "SIG"
    assert "thought_signature" not in sm.history[2]["parts"][0]


def test_stage_worker_and_runner_strip_old_transcripts():
    root = Path(__file__).resolve().parents[1]
    assert "strip_replay_fields(loaded, tool_results_only=True)" in (root / "mu/jobs/sandbox/worker.py").read_text()
    assert "strip_replay_fields(previous, tool_results_only=True)" in (root / "mu/jobs/sandbox/runner.py").read_text()


# ------------------------------------------------------------------ 4. recovery ladder


class _OverflowProvider(LLMProvider):
    """Every model request overflows; summaries (no tools) succeed."""

    def __init__(self, window=20_000):
        super().__init__("overflow")
        self.name, self.window, self.calls = "overflow", window, 0

    def get_available_models(self):
        return ["overflow"]

    def effective_context_window(self, model_name=None):
        return self.window

    def effective_response_reserve(self, model_name=None):
        return 512

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        if tools is None and system_prompt and "summar" in system_prompt.lower():
            return ProviderResponse(text="### Task\nship it\n### Progress\nhalf done", parts=[])
        self.calls += 1
        raise RuntimeError("prompt is too long: 250000 tokens > 200000 maximum")

    def upload_file(self, *a, **kw):
        return None


def _stuffed_session(provider, turns=12, size=3000):
    sm = SessionManager()
    session = Session(provider, False, "sys", sm)
    session.agentic = False
    for i in range(turns):
        sm.history.append({"role": "user", "parts": [{"type": "text", "text": f"request {i} " + "u" * size}]})
        sm.history.append({"role": "assistant", "parts": [{"type": "text", "text": f"answer {i} " + "a" * size}]})
    return session


def test_non_shrinking_rung_escalates_before_retrying(monkeypatch):
    import mu.agent.loop_body as lb
    from mu.agent.context_guard import _generate_with_overflow_recovery
    session = _stuffed_session(_OverflowProvider())
    applied, calls = [], []
    monkeypatch.setattr(lb, "_aggressive_compact_for_overflow",
                        lambda *a, **k: applied.append(("aggressive", k["keep_recent"])))
    import mu.agent.context_guard as cg
    monkeypatch.setattr(cg, "_full_compact_for_overflow", lambda s: applied.append(("full",)) or False)
    monkeypatch.setattr(cg, "_truncate_oversized_messages", lambda *a: applied.append(("truncate",)) or 0)

    def generate(*, messages, system_prompt, thinking, tools):
        calls.append(len(messages))
        if len(calls) == 1:
            raise RuntimeError("prompt is too long: 250000 tokens > 200000 maximum")
        return ProviderResponse(text="ok", parts=[])

    session._provider_generate_with_retry = generate
    messages = session._build_messages_from_history(session._prepare_runtime_history(turn_start_index=0),
                                                    {"role": "system", "parts": []})[:-1]
    resp = _generate_with_overflow_recovery(session, messages=messages, system_prompt="sys", thinking=False,
                                            tools=None, turn_start_index=0)
    assert resp.text == "ok"
    # No rung changed anything, so every rung ran before the single retry.
    assert applied == [("aggressive", 4), ("aggressive", 2), ("full",), ("truncate",)]
    assert len(calls) == 2
    assert session._overflow_recoveries_this_turn == 4


def test_full_compaction_rolls_everything_but_the_goal_into_l2():
    from mu.agent.context_guard import _full_compact_for_overflow
    session = _stuffed_session(_OverflowProvider())
    sm = session.session_manager
    sm.history.append({"role": "user", "parts": [{"type": "text", "text": "THE GOAL: finish soft delete"}]})
    sm.history.append({"role": "assistant", "parts": [{"type": "tool_call", "tool_name": "bash",
                                                       "tool_args": {"command": "ls"}}]})
    sm.history.append({"role": "tool", "parts": [{"type": "tool_result", "tool_name": "bash", "tool_result": "x" * 9000}]})
    sm.history.append({"role": "assistant", "parts": [{"type": "text", "text": "progress " * 500}]})
    goal = len(sm.history) - 4
    assert _full_compact_for_overflow(session)
    assert sm.summary_anchor >= len(sm.history) - 1
    assert goal in sm.protected_indices
    assert "ship it" in sm.conversation_summary
    kinds = [entry["kind"] for entry in getattr(sm, "_compaction_log", [])]
    assert "overflow_full_compact" in kinds
    runtime = session._prepare_runtime_history(turn_start_index=None)
    assert any("THE GOAL" in json.dumps(m) for m in runtime)


def test_single_oversized_message_is_truncated_with_a_marker():
    from mu.agent.context_guard import _truncate_oversized_messages
    session = _stuffed_session(_OverflowProvider(window=20_000), turns=1, size=100)
    sm = session.session_manager
    sm.history.append({"role": "tool", "parts": [{"type": "tool_result", "tool_name": "bash",
                                                  "tool_result": "line of output\n" * 20_000}]})
    cut = _truncate_oversized_messages(session, "sys", None)
    assert cut == 1
    body = sm.history[-1]["parts"][0]["tool_result"]
    assert "to fit the context window]" in body and body.rstrip().endswith("context window]")
    assert estimate_tokens(body) < 12_000
    assert "overflow_truncate" in [e["kind"] for e in getattr(sm, "_compaction_log", [])]


def test_overflow_never_ends_a_turn_as_an_api_error(tmp_path, monkeypatch):
    provider = _OverflowProvider()
    session = _loop_session(tmp_path, provider, monkeypatch)
    from unittest.mock import MagicMock
    shown = []
    session.ui = MagicMock()  # every other UI hook is a no-op
    session.ui.show_error.side_effect = lambda text, *a, **k: shown.append(text)
    session.variables["provider_recovery_policy"] = "auto"
    raw = session.send_message("do the thing")
    assert raw["status"] == "context_overflow"
    assert "context_overflow" in raw["error"]
    assert not any("API Error during agentic loop" in str(text) for text in shown)
    assert any("Context overflow" in str(text) for text in shown)
    assert provider.calls >= 2  # the ladder retried before giving up
    assert raw["status"] != "error"


def test_stage_worker_reports_context_overflow_with_its_summary():
    from mu.jobs.sandbox.worker import classify_error, overflow_result
    session = SimpleNamespace(session_manager=SimpleNamespace(conversation_summary="### Task\nsoft delete"))
    result = overflow_result(session, {"kind": "needs_human", "status": "context_overflow", "error": "context_overflow: x",
                                       "text": "last words"})
    assert result["kind"] == "failed" and result["error_class"] == "context_overflow"
    assert result["summary"] == "### Task\nsoft delete" and result["text"] == "last words"
    assert classify_error("context_overflow", "") == "context_overflow"
    assert classify_error("error", "prompt is too long: about 1,015,872 tokens > 1,000,000 maximum "
                                   "(refused by the job relay before sending)") == "context_overflow"


def test_runner_retries_a_context_overflow_stage_once_with_a_fresh_conversation(monkeypatch):
    from mu.jobs.sandbox import runner
    calls, events = [], []

    def once(service, job, stage, prompt, **kwargs):
        calls.append((prompt, kwargs.get("fresh", False)))
        if len(calls) == 1:
            return {"kind": "failed", "status": "context_overflow", "error_class": "context_overflow",
                    "error": "context_overflow: too long", "summary": "### Progress\nmigration written",
                    "cost_usd": 1.0}
        return {"kind": "completed", "status": "completed", "cost_usd": 0.5, "text": "done"}

    monkeypatch.setattr(runner, "_run_stage_once", once)
    service = SimpleNamespace(store=SimpleNamespace(append_event=lambda *a, **k: events.append((a, k))),
                              get=lambda job_id: SimpleNamespace(id=job_id))
    result = runner.run_stage_in_container(service, SimpleNamespace(id="j"), "implement", "Implement SCRUM-215.")
    assert result["kind"] == "completed" and result["cost_usd"] == 1.5
    assert [fresh for _, fresh in calls] == [False, True]
    retry_prompt = calls[1][0]
    assert retry_prompt.startswith("Implement SCRUM-215.")
    assert "migration written" in retry_prompt and "fresh conversation" in retry_prompt
    assert events[0][0][1] == "stage_context_overflow"


def test_runner_retries_context_overflow_only_once(monkeypatch):
    from mu.jobs.sandbox import runner
    calls = []

    def once(service, job, stage, prompt, **kwargs):
        calls.append(kwargs.get("fresh", False))
        return {"kind": "failed", "status": "context_overflow", "error_class": "context_overflow",
                "error": "too long", "cost_usd": 0.25}

    monkeypatch.setattr(runner, "_run_stage_once", once)
    service = SimpleNamespace(store=SimpleNamespace(append_event=lambda *a, **k: None),
                              get=lambda job_id: SimpleNamespace(id=job_id))
    result = runner.run_stage_in_container(service, SimpleNamespace(id="j"), "implement", "p")
    assert calls == [False, True]
    assert result["error_class"] == "context_overflow" and result["cost_usd"] == 0.5


def test_fresh_retry_ignores_the_paused_transcript():
    source = (Path(__file__).resolve().parents[1] / "mu/jobs/sandbox/runner.py").read_text()
    assert 'paused = not fresh and stage in' in source


# ------------------------------------------------------------------ 5. external harnesses


def test_claude_code_config_turns_auto_compact_on(tmp_path):
    from mu.jobs import harness_cli as cli
    from mu.jobs.sandbox.harness_worker import write_claude_config
    assert cli.claude_global_config({"theme": "dark", "autoCompactEnabled": False}) == \
        {"theme": "dark", "autoCompactEnabled": True}
    (tmp_path / ".claude.json").write_text(json.dumps({"autoCompactEnabled": False, "numStartups": 3}))
    path = write_claude_config(tmp_path)
    assert json.loads(path.read_text()) == {"autoCompactEnabled": True, "numStartups": 3}
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert set(cli.CLAUDE_COMPACT_DISABLERS) == {"DISABLE_AUTO_COMPACT", "DISABLE_COMPACT"}
    assert not set(cli.claude_env(60)) & set(cli.CLAUDE_COMPACT_DISABLERS)


def test_codex_args_set_the_auto_compact_threshold():
    from mu.jobs import harness_cli as cli
    assert cli.codex_auto_compact_limit(400_000) == 340_000
    assert cli.codex_auto_compact_limit(None) == 0
    args = cli.codex_args(model="gpt-5.5-codex", read_only=False, auto_compact_tokens=340_000)
    assert args[-3:] == ["-c", "model_auto_compact_token_limit=340000", "-"]
    assert "model_auto_compact_token_limit" not in " ".join(cli.codex_args(model="m", read_only=False))


def test_harness_overflow_detection():
    from mu.jobs import harness_cli as cli
    assert cli.harness_context_overflow("Prompt is too long")
    assert cli.harness_context_overflow("API Error: 400 prompt is too long: 1015872 tokens > 1000000 maximum")
    assert cli.harness_context_overflow("stream error: context_window_exceeded")
    assert cli.harness_context_overflow("Your input exceeds the context window of this model.")
    assert not cli.harness_context_overflow("You've hit your usage limit")
    assert not cli.harness_context_overflow("")


class _HarnessRun:
    def __init__(self, monkeypatch, tmp_path, agent, outcomes, request=None):
        from mu.jobs.sandbox import harness_worker
        monkeypatch.setenv("MUCLI_JOB_STAGE", "implement")
        monkeypatch.setattr(harness_worker, "DELIVERED", tmp_path / "delivered.json")
        monkeypatch.setattr(harness_worker, "PENDING", tmp_path / "pending.json")
        monkeypatch.setattr(harness_worker, "relay_post", lambda *a, **k: {})
        self.events = []
        monkeypatch.setattr(harness_worker, "emit", lambda event, payload=None, reason="": self.events.append(
            (event, payload or {})))
        self.run = harness_worker.Run(agent, request or {"prompt": "go"})
        self.prompts, self.envs = [], []
        outcomes = list(outcomes)

        def run_once(full_env, prompt, resume):
            self.prompts.append((prompt, resume))
            self.envs.append(full_env)
            error = outcomes.pop(0) if outcomes else ""
            self.run.state.session_id = "sid-1"
            self.run.state.finished = True
            self.run.state.is_error = bool(error)
            self.run.state.error = error
            return (1 if error else 0), ""

        self.run._run_once = run_once


def test_claude_code_overflow_runs_compact_and_continues(monkeypatch, tmp_path):
    monkeypatch.setenv("DISABLE_AUTO_COMPACT", "1")
    h = _HarnessRun(monkeypatch, tmp_path, "claude", ["Prompt is too long", "", ""])
    code, _ = h.run.run({})
    from mu.jobs import harness_cli as cli
    assert [p for p, _ in h.prompts] == ["go", "/compact", cli.OVERFLOW_CONTINUE_PROMPT]
    assert [r for _, r in h.prompts][1:] == ["sid-1", "sid-1"]
    assert code == 0 and h.run.result(code, "")["kind"] == "completed"
    assert all("DISABLE_AUTO_COMPACT" not in env for env in h.envs)
    assert any("compacting" in e[1].get("text", "") for e in h.events)


def test_codex_overflow_resumes_its_thread(monkeypatch, tmp_path):
    h = _HarnessRun(monkeypatch, tmp_path, "codex", ["context_window_exceeded", ""],
                    request={"prompt": "go", "context_window": 400_000})
    code, _ = h.run.run({})
    from mu.jobs import harness_cli as cli
    assert [p for p, _ in h.prompts][1:] == [cli.OVERFLOW_CONTINUE_PROMPT]
    assert "model_auto_compact_token_limit=340000" in " ".join(h.run.command())
    assert code == 0


def test_harness_overflow_resumes_are_bounded(monkeypatch, tmp_path):
    from mu.jobs import harness_cli as cli
    h = _HarnessRun(monkeypatch, tmp_path, "codex", ["context_window_exceeded"] * 10)
    code, _ = h.run.run({})
    assert len(h.prompts) == 1 + cli.OVERFLOW_RESUMES
    result = h.run.result(code, "")
    assert result["kind"] == "failed"
    from mu.jobs.sandbox.worker import classify_error
    assert classify_error(result["status"], result["error"]) == "context_overflow"  # host retries once, fresh


# ------------------------------------------------------------------ helpers


class _RelayProvider:
    model_name = ""

    def __init__(self, window):
        self.window, self.calls = window, 0

    def effective_context_window(self, model_name=None):
        return self.window

    def stream(self, messages, **kwargs):
        self.calls += 1
        yield StreamEvent(kind="text_delta", text="ok")
        yield StreamEvent(kind="usage", input_tokens=20, output_tokens=2, total_tokens=22)
