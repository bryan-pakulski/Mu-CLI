"""Harness latency plan, jobs part: bounded review prompt, real context window in stages,
pre-send overflow guard, no charge for rejected requests, and no futile fix attempts."""
import json
from types import SimpleNamespace

import pytest

from mu.jobs.sandbox.model_relay import RelayDenied, wire
from providers.base import Message, MessagePart
from test_job_run_rules import CachedProvider, relay


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))


def payload(text="hi"):
    return {"messages": wire([Message("user", [MessagePart("text", text=text)])])}


def test_review_patch_is_bounded_and_lists_what_it_left_out():
    from mu.jobs.stages import budget_patch
    files = [f"diff --git a/f{i}.py b/f{i}.py\n" + ("+line\n" * 2000) for i in range(6)]
    patch = "".join(files)
    small = budget_patch(patch, 30000)
    assert len(small) < 32000 and "diff --git a/f0.py" in small
    assert "Diffs not included" in small and "f5.py b/f5.py" in small
    assert budget_patch("diff --git a/x b/x\n+1\n", 30000) == "diff --git a/x b/x\n+1\n"
    huge = budget_patch("diff --git a/big b/big\n" + "+x\n" * 50000, 20000)
    assert "cut at 10,000 characters" in huge and len(huge) < 21000


def test_relay_refuses_a_certain_overflow_before_sending(tmp_path):
    class Windowed(CachedProvider):
        def effective_context_window(self, model):
            return 50
    obj, provider = relay(tmp_path, provider=Windowed())
    with pytest.raises(RelayDenied, match="prompt is too long"):
        list(obj.stream(obj.token, payload("word " * 400)))
    assert provider.calls == 0
    from mu.agent.retry import is_context_overflow_error
    assert is_context_overflow_error(RuntimeError(str(RelayDenied("prompt is too long: about 900 tokens > 50 maximum"))))


def test_rejected_request_is_not_charged(tmp_path):
    obj, provider = relay(tmp_path, provider=CachedProvider(fail=RuntimeError("400 prompt is too long")))
    with pytest.raises(RuntimeError):
        list(obj.stream(obj.token, payload()))
    assert obj.cost == 0 and obj.tokens == 0
    assert obj.artifacts.stage_costs() == {}


def test_stage_provider_reports_the_host_context_window():
    from mu.jobs.sandbox.provider import RelayProvider
    assert RelayProvider("anthropic", "claude-opus-5-5", 1_000_000).effective_context_window() == 1_000_000
    assert RelayProvider("anthropic", "claude-opus-5-5").effective_context_window() is None


def _run(suites, head="abc", trigger="auto"):
    return SimpleNamespace(summary={"suites": suites, "head_sha": head, "trigger": trigger, "dirty_worktree": False,
                                    "new_failures": sum(bool(s.get("new_failures")) for s in suites)})


def test_environment_failures_never_schedule_a_fix():
    from mu.jobs.verify_worker import futile_fix_reason
    job = SimpleNamespace(id="j1", metadata={})
    missing = {"suite": "unit", "new_failures": ["x"], "environment": "node-build",
               "stderr": "Error: Cannot find module '@photostructure/tz-lookup'"}
    reason = futile_fix_reason(job, _run([missing]))
    assert reason.startswith("environment:") and "`node-build`" in reason and "tz-lookup" in reason
    real = {"suite": "unit", "new_failures": ["test_x"], "stderr": "AssertionError: 1 != 2"}
    assert futile_fix_reason(job, _run([missing, real])) == ""


def test_a_fix_attempt_that_changed_nothing_is_not_repeated(tmp_path):
    from mu.jobs.core.artifacts import artifacts_for
    from mu.jobs.verify_worker import futile_fix_reason
    job = SimpleNamespace(id="j2", metadata={"last_fix_head": "abc"}, repository=str(tmp_path))
    art = artifacts_for(job).ensure()
    suite = {"suite": "unit", "new_failures": ["test_x"], "stderr": "AssertionError"}
    for n in (1, 2):
        art.write_json(f"checks/run-{n}/summary.json", _run([suite]).summary)
    assert "changed nothing" in futile_fix_reason(job, _run([suite]))
    moved = SimpleNamespace(id="j2", metadata={"last_fix_head": "old"}, repository=str(tmp_path))
    assert futile_fix_reason(moved, _run([suite])) == ""


def test_stage_brief_hands_findings_to_the_next_stage(tmp_path):
    from mu.jobs.core.artifacts import artifacts_for
    from mu.jobs.stages import stage_brief
    job = SimpleNamespace(id="brief1", repository=str(tmp_path), metadata={})
    art = artifacts_for(job).ensure()
    assert stage_brief(job, "plan") == ""
    art.write_json("logs/plan-transcript.json", [
        {"role": "user", "parts": [{"type": "text", "text": "plan it"}]},
        {"role": "assistant", "parts": [{"type": "tool_call", "tool_name": "read_file",
                                          "tool_args": {"filename": "/workspace/server.js"}}]},
        {"role": "assistant", "parts": [{"type": "tool_call", "tool_name": "search_for_string",
                                          "tool_args": {"query": "headcount"}}]},
        {"role": "assistant", "parts": [{"type": "tool_call", "tool_name": "bash", "tool_args": {"command": "npm test -- report"}}]},
        {"role": "assistant", "parts": [{"type": "text", "text": "Plan: change report.js and its test."}]},
    ])
    brief = stage_brief(job, "plan")
    assert "read_file: server.js" in brief and "search_for_string: headcount" in brief
    assert "npm test -- report" in brief and "change report.js" in brief
    assert art.read_text("briefs/plan.md") == brief
