"""Long-running work should retain a working set, not an ever-growing log."""

from copy import deepcopy

import pytest

from mu.agent.compactor import _compact_history, manual_compact
from mu.agent.context_guard import _estimate_request_tokens, _reinject_refreshed_summary
from mu.agent.hooks import HookContext
from mu.session.budgets import resolve_tool_result_floor
from mu.session.context_maintenance import projected_tokens
from mu.session.session import Session, SessionManager
from providers.base import LLMProvider, ProviderResponse, StreamEvent


class BatchProvider(LLMProvider):
    def __init__(self):
        super().__init__("large-window")
        self.summaries = []
        self.requests = []

    def get_available_models(self):
        return ["large-window"]

    def effective_context_window(self, model_name=None):
        return 1_000_000

    def upload_file(self, *args):
        return None

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        self.summaries.append((system_prompt, messages))
        return ProviderResponse(text=(
            "### Progress\nCompleted batches are recorded.\n"
            "### Current state\nResume cursor: checkpoint-42.\n"
            "### Open items\nRetry exception-7; do not repeat confirmed writes."
        ), parts=[])

    def stream(self, messages, system_prompt=None, thinking=False, tools=None):
        self.requests.append((system_prompt, messages))
        yield StreamEvent(kind="text_delta", text="Continuing the next batch.")


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path / "history"))
    s = Session(BatchProvider(), False, "Process the queue safely.", SessionManager())
    s.agentic = False
    s.variables["context_token_limit"] = 480_000
    s.variables["session_goal"] = "Process every queued item once; retain exceptions."
    sm = s.session_manager
    sm.history = [{"role": "user", "parts": [{"type": "text", "text": s.variables["session_goal"]}]}]
    sm.summary_anchor = 0
    sm.conversation_summary = ""
    sm.protected_indices = {0}
    sm._active_turn_start_index = 0
    s._current_turn_start_index = 0
    return s


def add_batch(session, index, size=800):
    session.session_manager.history.extend([
        {"role": "assistant", "parts": [{
            "type": "tool_call", "tool_name": "process_batch",
            "tool_call_id": f"batch-{index}", "tool_args": {"cursor": index},
        }]},
        {"role": "tool", "parts": [{
            "type": "tool_result", "tool_name": "process_batch",
            "tool_call_id": f"batch-{index}",
            "tool_result": f"batch-receipt-{index:04d} confirmed. " + "detail " * size,
        }]},
    ])


@pytest.mark.parametrize("mode", ["default", "loop", "feature"])
def test_long_task_compacts_repeatedly_without_restart_or_losing_receipts(session, mode):
    session.variables["agent_mode"] = mode
    assert session.variables["auto_compaction_enabled"] is True
    assert session.variables["auto_compaction_token_limit"] == 0
    # Explicit small working sets remain available; the universal default
    # now follows the effective provider window (tested in selective_context).
    session.variables["auto_compaction_token_limit"] = 64_000
    sm = session.session_manager
    ctx = HookContext(point="pre_provider_call", session=session)
    peaks, lows, anchors = [], [], []
    original = deepcopy(sm.history)
    for index in range(180):
        add_batch(session, index)
        original.extend(deepcopy(sm.history[-2:]))
        before = projected_tokens(session)
        floor = resolve_tool_result_floor(session)
        protected = deepcopy(sm.history[-floor * 2:])
        result = _compact_history(ctx)
        if result:
            peaks.append(before)
            lows.append(projected_tokens(session))
            anchors.append(sm.summary_anchor)
            assert sm.history[-floor * 2:] == protected
            runtime = session._prepare_runtime_history(turn_start_index=0)
            assert all(entry in runtime for entry in protected)
            assert not any(part["type"] == "tool_result" for part in sm.history[sm.summary_anchor]["parts"])
            assert _compact_history(ctx) is None, "an unchanged retry compacted again"
    assert len(anchors) >= 3, "long tasks must compact multiple times within one turn"
    assert anchors == sorted(set(anchors))
    assert max(peaks) < 68_000, "working history approached the large provider ceiling"
    assert max(lows) <= 32_000, "cleanup did not reclaim breathing room"
    assert sm.history == original, "soft cleanup must preserve the durable transcript"
    runtime = session._prepare_runtime_history(turn_start_index=0)
    assert original[0] in runtime, "the active user request was lost"
    assert sm.search_history("batch-receipt-0000")["total_matches"] >= 1
    assert "checkpoint-42" in sm.conversation_summary
    assert "exception-7" in sm.conversation_summary


def test_protected_tail_can_exceed_soft_limit_without_degradation_or_retry_storm(session, monkeypatch):
    session.variables["auto_compaction_token_limit"] = 1000
    add_batch(session, 1, size=10_000)
    sm = session.session_manager
    before = deepcopy(sm.history)
    calls = []
    roll = sm.roll_history_summary_to_token_budget

    def observe(*args, **kwargs):
        calls.append(1)
        return roll(*args, **kwargs)

    monkeypatch.setattr(sm, "roll_history_summary_to_token_budget", observe)
    ctx = HookContext(point="pre_provider_call", session=session)
    for _ in range(10):
        assert _compact_history(ctx) is None
    assert len(calls) == 1
    assert sm.history == before
    assert session.provider.summaries == []


def test_manual_compaction_allows_later_automatic_cleanup_in_same_turn(session):
    session.variables["auto_compaction_token_limit"] = 64_000
    for index in range(24):
        add_batch(session, index)
    result = manual_compact(session)
    assert result["compacted"]
    anchor = session.session_manager.summary_anchor
    assert _compact_history(HookContext(point="pre_provider_call", session=session)) is None
    for index in range(24, 110):
        add_batch(session, index)
        _compact_history(HookContext(point="pre_provider_call", session=session))
    assert session.session_manager.summary_anchor > anchor


def test_first_request_after_hook_compaction_gets_fresh_summary_and_accurate_metrics(session):
    session.variables["auto_compaction_token_limit"] = 8000
    for index in range(30):
        add_batch(session, index, size=400)
    session._system_prompt_base = "Process the queue safely."
    session._turn_skills_block = "CACHED SKILL POLICY"
    session._turn_context_files_block = "CACHED WORKSPACE POLICY"
    prompt = session._inject_hierarchical_context(
        session._system_prompt_base,
        cached_skills=session._turn_skills_block,
        cached_context_files=session._turn_context_files_block,
    )
    tail = "\n\nLAYER 2M — Durable cross-session recall:\nKeep customer exceptions.\n\nScratchpad: next batch pending."
    prompt += tail
    messages = session._build_messages_from_history(
        session._prepare_runtime_history(turn_start_index=0),
        {"role": "system", "parts": []},
    )[:-1]
    before = _estimate_request_tokens(prompt, messages)
    session._provider_generate_with_retry(
        messages=messages, system_prompt=prompt, thinking=False, tools=[],
    )
    sent_prompt, sent_messages = session.provider.requests[-1]
    assert session.session_manager.summary_anchor > 0
    assert "checkpoint-42" in sent_prompt
    assert "exception-7" in sent_prompt
    assert sent_prompt.endswith(tail)
    assert sent_prompt.count("Hierarchical runtime context") == 1
    assert sent_prompt.count("CACHED SKILL POLICY") == 1
    assert sent_prompt.count("CACHED WORKSPACE POLICY") == 1
    estimate = _estimate_request_tokens(sent_prompt, sent_messages)
    assert estimate["total"] < before["total"]
    assert session._request_estimate_manifest == estimate
    assert session._last_prompt_cl100k_est == estimate["total"]
    assert any(part.text == session.variables["session_goal"] for msg in sent_messages for part in msg.parts)


def test_rebuilding_summary_preserves_appended_context_across_multiple_compactions(session):
    session._system_prompt_base = "base"
    prompt = session._inject_hierarchical_context("base", cached_skills="skills", cached_context_files="files")
    session._turn_skills_block = "skills"
    session._turn_context_files_block = "files"
    tail = "\n\nWorking memory: keep pending-write-7 until its result is known."
    prompt += tail
    for index in range(3):
        session.session_manager.conversation_summary = f"### Current state\nCursor {index}"
        prompt = _reinject_refreshed_summary(session, prompt)
        assert prompt.endswith(tail)
        assert prompt.count("Hierarchical runtime context") == 1
        assert f"Cursor {index}" in prompt
