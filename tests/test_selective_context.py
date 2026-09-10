"""Model context edits preserve durable evidence and measure the wire projection."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from mu.agent.compactor import _compact_history, manual_compact
from mu.agent.hooks import HookContext
from mu.session.context_maintenance import (
    candidates, context_status, edit_tool_results, projected_messages,
    projected_tokens, result_id,
)
from mu.session.result_store import ResultStore
from mu.session.session import Session, SessionManager
from providers.base import LLMProvider, ProviderResponse, StreamEvent


class Provider(LLMProvider):
    def __init__(self):
        super().__init__("large-window")
        self.calls = 0

    def get_available_models(self):
        return ["large-window"]

    def effective_context_window(self, model_name=None):
        return 1_000_000

    def upload_file(self, *args):
        return None

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        self.calls += 1
        return ProviderResponse(text="### Progress\nCompleted older work.", parts=[],
                                input_tokens=100, output_tokens=10)

    def stream(self, messages, system_prompt=None, thinking=False, tools=None):
        self.request = (system_prompt, messages)
        yield StreamEvent(kind="text_delta", text="Continuing the next batch.")


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.config.HISTORY_DIR", str(tmp_path / "history"))
    sm = SessionManager(session_name="selective")
    session = Session(Provider(), False, "Keep user instructions intact.", sm)
    session.agentic = False
    session.variables["context_token_limit"] = 950_000
    session.variables["tool_result_floor"] = 2
    sm.tool_result_cache.set_store(ResultStore("test", root=str(tmp_path / "results")))
    sm.history = [{"role": "user", "parts": [{"type": "text", "text": "Classify mail. Never send replies."}]}]
    sm.protected_indices = {0}
    sm._active_turn_start_index = 0
    session._current_turn_start_index = 0
    for index in range(12):
        sm.history.extend([
            {"role": "assistant", "parts": [{"type": "tool_call", "tool_name": "mail_batch", "tool_args": {"cursor": index}}]},
            {"role": "tool", "parts": [{"type": "tool_result", "tool_name": "mail_batch",
                                        "tool_result": {"ok": True, "raw": f"receipt-{index}: " + "detail " * 7000}}]},
        ])
    return session


def keys(session):
    return [item["result_id"] for item in candidates(session)]


def test_only_selected_results_clear_without_a_summary_or_history_mutation(session):
    sm = session.session_manager
    before = deepcopy(sm.history)
    ids = keys(session)
    outcome = edit_tool_results(session, ids[:2], checkpoint={"resume": "batch-2", "progress": "2 batches confirmed"})
    assert outcome["changed"] == ids[:2]
    assert outcome["saved_tokens"] > 10_000
    assert outcome["summarizer_calls"] == session.provider.calls == 0
    assert sm.history == before
    messages = projected_messages(session)
    assert "ref:" in str(messages[2].parts[0].tool_result)
    assert "receipt-2" in str(messages[6].parts[0].tool_result)
    assert "Never send replies" in messages[0].parts[0].text
    assert sm.context_checkpoint["resume"] == "batch-2"
    restored = edit_tool_results(session, ids[:1], action="restore")
    assert restored["after_tokens"] > outcome["after_tokens"]
    assert "receipt-0" in str(projected_messages(session)[2].parts[0].tool_result)


def test_clearing_protects_recent_failed_signed_and_pinned_results(session):
    sm = session.session_manager
    sm.history[4]["parts"][0]["tool_result"]["ok"] = False
    sm.history[6]["parts"][0]["thought_signature"] = "signed"
    ids = keys(session)
    edit_tool_results(session, ids[:1], action="keep")
    outcome = edit_tool_results(session, [ids[0], ids[1], ids[2], *ids[-2:]])
    assert outcome["changed"] == []
    assert {entry["reason"] for entry in outcome["skipped"]} == {"protected", "provider_signature", "recent"}
    assert session.provider.calls == 0


def test_failed_durable_write_does_not_clear_anything(session, monkeypatch):
    before = projected_tokens(session)
    monkeypatch.setattr(session.tool_result_cache._store, "put", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    outcome = edit_tool_results(session, keys(session)[:1])
    assert outcome["changed"] == []
    assert outcome["skipped"][0]["reason"] == "durable_store_failed"
    assert projected_tokens(session) == before
    assert session.session_manager.context_retention == {}


def test_cleared_receipts_and_checkpoint_survive_reload_and_cache_eviction(session):
    from mu.tools.memory.handlers import recall
    sm = session.session_manager
    original = deepcopy(sm.history[2]["parts"][0]["tool_result"])
    key = keys(session)[0]
    edit_tool_results(session, [key], checkpoint={"resume": "cursor-exact", "exceptions": "receipt-9 pending"})
    sm.save_history()
    reloaded = SessionManager(session_name="selective")
    other = Session(Provider(), False, "base", reloaded)
    assert reloaded.context_checkpoint["resume"] == "cursor-exact"
    assert reloaded.context_retention == sm.context_retention
    cache_key = reloaded.context_retention[key]["cache_key"]
    assert other.tool_result_cache.recall(cache_key) is None
    recovered = json.loads(recall({"cache_key": cache_key}, SimpleNamespace(session=other)))
    assert recovered["result"] == original
    assert recovered["from_history"] is True
    assert "ref:" in str(projected_messages(other)[2].parts[0].tool_result)


def test_trigger_counts_projected_results_and_retains_large_active_context(session):
    sm = session.session_manager
    assert session.variables["auto_compaction_token_limit"] == 0
    assert projected_tokens(session) > 64_000
    assert _compact_history(HookContext(point="pre_provider_call", session=session)) is None
    assert session.provider.calls == 0
    edit_tool_results(session, keys(session)[:-2])
    assert sm.estimate_runtime_history_tokens() > 64_000
    assert projected_tokens(session) < 20_000
    session.variables["auto_compaction_token_limit"] = 64_000
    assert _compact_history(HookContext(point="pre_provider_call", session=session)) is None
    assert session.provider.calls == 0
    assert context_status(session)["projected_history_tokens"] == projected_tokens(session)


def test_automatic_pressure_clears_cheaply_before_summarizing(session):
    session.variables["auto_compaction_token_limit"] = 30_000
    result = _compact_history(HookContext(point="pre_provider_call", session=session))
    assert result.data["clearing"]["saved_tokens"] > 40_000
    assert projected_tokens(session) < 30_000
    assert session.provider.calls == 0
    assert session.session_manager.summary_anchor == 0
    assert _compact_history(HookContext(point="pre_provider_call", session=session)) is None


def test_model_compact_preserves_selected_evidence_and_checkpoint(session):
    sm = session.session_manager
    before = deepcopy(sm.history)
    key = keys(session)[0]
    outcome = manual_compact(session, preserve_result_ids=[key], through_index=10,
                             checkpoint={"resume": "batch-5", "constraints": "Never send replies"})
    assert outcome["ok"] and outcome["compacted"]
    assert 0 < sm.summary_anchor <= 11
    assert sm.history == before
    messages = projected_messages(session)
    assert any("receipt-0" in str(p.tool_result) for m in messages for p in m.parts)
    assert any(p.tool_name == "mail_batch" and p.tool_args == {"cursor": 0} for m in messages for p in m.parts)
    prompt = session._inject_hierarchical_context("base")
    assert "batch-5" in prompt and "Never send replies" in prompt
    assert sm._summary_usage["calls"] == session.provider.calls


def test_invalid_or_oversized_checkpoint_is_atomic(session):
    sm = session.session_manager
    before = deepcopy(sm.history)
    with pytest.raises(ValueError):
        edit_tool_results(session, keys(session)[:1], checkpoint={"progress": "x" * 6001})
    assert sm.context_retention == {}
    assert sm.history == before
    outcome = manual_compact(session, preserve_result_ids=keys(session)[:1], through_index=999)
    assert outcome["ok"] is False
    assert sm.context_retention == {}


def test_identical_results_are_separately_selectable(session):
    sm = session.session_manager
    sm.history[4]["parts"][0]["tool_result"] = deepcopy(sm.history[2]["parts"][0]["tool_result"])
    first, second = keys(session)[:2]
    assert first != second
    edit_tool_results(session, [first])
    messages = projected_messages(session)
    assert "ref:" in str(messages[2].parts[0].tool_result)
    assert "receipt-0" in str(messages[4].parts[0].tool_result)


def test_active_request_fixed_layers_and_tools_tighten_the_fallback(session):
    session.variables["context_token_limit"] = 100_000
    ctx = HookContext(point="pre_provider_call", session=session, system_prompt="policy " * 55_000)
    result = _compact_history(ctx)
    assert result is not None
    assert result.data.get("compaction") is True
    assert session.session_manager.context_retention


def test_first_provider_request_uses_cleared_projection_and_checkpoint(session):
    from mu.agent.context_guard import _estimate_request_tokens
    session.variables["auto_compaction_token_limit"] = 30_000
    edit_tool_results(session, [], checkpoint={"resume": "cursor-12", "constraints": "Never send replies"})
    session._system_prompt_base = "Continue classifying."
    prompt = session._inject_hierarchical_context(session._system_prompt_base)
    session._provider_generate_with_retry(
        messages=projected_messages(session), system_prompt=prompt, thinking=False, tools=[],
    )
    sent_prompt, messages = session.provider.request
    assert "cursor-12" in sent_prompt and "Never send replies" in sent_prompt
    assert "ref:" in str(messages[2].parts[0].tool_result)
    assert "receipt-11" in str(messages[-1].parts[0].tool_result)
    assert session._request_estimate_manifest == _estimate_request_tokens(sent_prompt, messages, [])
    assert session.provider.calls == 0
    assert session.session_manager.summary_anchor == 0


def test_emergency_degradation_preserves_cleared_and_kept_receipts(session):
    sm = session.session_manager
    ids = keys(session)
    edit_tool_results(session, ids[:2])
    edit_tool_results(session, ids[2:], action="keep")
    original = deepcopy(sm.history)
    assert sm._degrade_oldest_runtime_payload(max_chars=100, provider=session.provider) is False
    assert sm.history == original
    assert session.provider.calls == 0


def test_pin_retains_later_sibling_results_in_parallel_bundle(session):
    from mu.session.context_maintenance import protected_indices
    sm = session.session_manager
    sm.history[1]["parts"].extend(sm.history[3]["parts"])
    del sm.history[3]
    edit_tool_results(session, keys(session)[:1], action="keep")
    assert {1, 2, 3} <= protected_indices(sm)
    outcome = edit_tool_results(session, keys(session)[1:2])
    assert outcome["changed"] == []
    assert outcome["skipped"][0]["reason"] == "protected"


def test_candidates_page_and_reject_stale_fingerprints(session):
    sm = session.session_manager
    sm.history[2]["parts"][0]["cache_key"] = "existing-cache-key"
    first = context_status(session, candidate_limit=3)
    second = context_status(session, candidate_offset=first["next_candidate_offset"], candidate_limit=3)
    assert first["candidate_count"] == 12
    assert {item["result_id"] for item in first["candidates"]}.isdisjoint(
        item["result_id"] for item in second["candidates"])
    edit_tool_results(session, keys(session)[:1])
    sm.history[2]["parts"][0]["tool_result"]["raw"] = "changed evidence"
    assert "changed evidence" in str(projected_messages(session)[2].parts[0].tool_result)
    assert candidates(session)[0]["state"] == "active"


def test_signed_bundles_can_be_archived_whole_but_not_selectively_cleared(session):
    sm = session.session_manager
    sm.history[1]["parts"][0]["thought_signature"] = "provider-signed-call"
    original = deepcopy(sm.history)
    assert edit_tool_results(session, keys(session)[:1])["changed"] == []
    outcome = manual_compact(session, through_index=4)
    assert outcome["compacted"] is True
    assert sm.summary_anchor > 2
    assert sm.history == original
    messages = projected_messages(session)
    assert not any(p.tool_args == {"cursor": 0} for m in messages for p in m.parts)
    assert not any("receipt-0" in str(p.tool_result) for m in messages for p in m.parts)


def test_uncleared_cached_results_keep_native_media():
    from mu.session.messages import build_messages_from_history
    from providers.base import MediaData
    media = MediaData(data=b"image-bytes", mime_type="image/png")
    part = {"type": "tool_result", "tool_name": "browser_snapshot", "cache_key": "cached",
            "tool_result": {"ok": True}, "media_inputs": [{"artifact_id": "shot"}]}
    history = [{"role": "tool", "parts": [part]}]
    def build(retention=None):
        return build_messages_from_history(history, {"role": "system", "parts": []},
                                           auto_clear=False, retention=retention,
                                           media_resolver=lambda ref: media)[0].parts[0]
    assert build().media_inputs == [media]
    assert build({id(part): {"action": "keep"}}).media_inputs == [media]
    assert build({id(part): {"action": "clear"}}).media_inputs == []
