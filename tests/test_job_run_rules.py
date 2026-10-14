"""Stage budgets count new tokens, relay failures say why, and every run rule is a setting."""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from providers.base import Message, MessagePart, StreamEvent
from mu.jobs.core.artifacts import JobArtifacts
from mu.jobs.sandbox.model_relay import ModelRelay, RelayDenied, wire
from mu.jobs.sandbox.policy import SandboxPolicy


class CachedProvider:
    """Each turn re-sends ~100k tokens of context, 90k of them cache reads."""
    model_name = ""

    def __init__(self, fail=None):
        self.calls, self.fail = 0, fail

    def stream(self, messages, **kwargs):
        self.calls += 1
        if self.fail:
            raise self.fail
        yield StreamEvent(kind="text_delta", text="ok")
        yield StreamEvent(kind="usage", input_tokens=100_000, output_tokens=200, total_tokens=100_200,
                          cached_tokens=90_000, cache_read_tokens=90_000)
        yield StreamEvent(kind="done")


def relay(tmp_path, policy=None, provider=None):
    provider = provider or CachedProvider()
    art = JobArtifacts("j", str(tmp_path)).ensure()
    obj = ModelRelay(SimpleNamespace(id="j"), "implement", {"provider": "anthropic", "model": "claude-sonnet-5-5"},
                     policy or SandboxPolicy(), art, provider_factory=lambda p, m: provider)
    return obj, provider


def turn(obj):
    return list(obj.stream(obj.token, {"messages": wire([Message("user", [MessagePart("text", text="next step")])])}))


def test_default_budget_is_50m_new_tokens_per_stage():
    policy = SandboxPolicy.for_stage("implement", {"sandbox": {}})
    assert policy.max_tokens == 50_000_000 and policy.count_cache_reads is False


def test_cache_reads_are_not_charged_by_default(tmp_path):
    obj, _ = relay(tmp_path)
    turn(obj)
    assert obj.tokens == 10_200  # 100_200 total − 90_000 cache reads


def test_cache_reads_are_charged_when_enabled(tmp_path):
    obj, _ = relay(tmp_path, SandboxPolicy(count_cache_reads=True))
    turn(obj)
    assert obj.tokens == 100_200


def test_scrum40_shape_no_longer_exhausts_a_2m_budget(tmp_path):
    """21 turns of ~100k re-sent context used to exhaust 2M; new-token counting fits easily."""
    obj, provider = relay(tmp_path, SandboxPolicy(max_tokens=2_000_000))
    for _ in range(40):
        turn(obj)
    assert provider.calls == 40 and obj.tokens == 40 * 10_200


def test_budget_refusal_reaches_the_stage_and_host_diagnostics(tmp_path):
    obj, provider = relay(tmp_path, SandboxPolicy(max_tokens=15_000))
    recorded = []
    obj.on_error = recorded.append
    turn(obj)  # 10_200 charged
    turn(obj)  # 20_400 charged → budget now exhausted
    with pytest.raises(RelayDenied, match="Stage token budget exhausted"):
        turn(obj)
    client = TestClient(obj.app())
    lines = client.post("/stream", headers={"x-mucli-stage-token": obj.token}, json={"messages": []}).text.splitlines()
    error = json.loads(lines[-1])
    assert error["kind"] == "error"
    assert "Stage token budget exhausted: 20,400 of 15,000" in error["text"]
    assert "sandbox.max_tokens" in error["text"] and "inspect host diagnostics" not in error["text"]
    assert recorded and recorded[-1]["kind"] == "denied" and recorded[-1]["max_tokens"] == 15_000


def test_provider_errors_are_named_and_redacted(tmp_path):
    leaky = RuntimeError("401 Unauthorized: invalid x-api-key sk-ant-api03-" + "A" * 40)
    obj, _ = relay(tmp_path, provider=CachedProvider(fail=leaky))
    recorded = []
    obj.on_error = recorded.append
    text = json.loads(TestClient(obj.app()).post("/stream", headers={"x-mucli-stage-token": obj.token},
                                                  json={"messages": []}).text.splitlines()[-1])["text"]
    assert "RuntimeError" in text and "401 Unauthorized" in text
    assert "sk-ant-api03-AAAA" not in text and "sk-ant-api03-AAAA" not in json.dumps(recorded)
    assert recorded[-1]["kind"] == "provider"


def test_stage_runner_records_relay_errors_as_diagnostic_events():
    from mu.jobs.diagnostics import DIAGNOSTIC_EVENT_TYPES
    import inspect
    from mu.jobs.sandbox import runner
    assert "model_relay_error" in DIAGNOSTIC_EVENT_TYPES
    assert '"model_relay_error"' in inspect.getsource(runner._run_stage_once)


# --------------------------------------------------------------------------- settings

@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path))
    return tmp_path


def test_every_run_rule_is_described_with_a_default(home):
    from mu.jobs.job_settings import FIELD_BY_PATH, public_settings
    expected = {"sandbox.max_tokens", "sandbox.count_cache_reads", "sandbox.max_cost_usd", "limits.max_iterations",
                "sandbox.stage_timeout_s.implement", "checks.max_fix_attempts", "checks.baseline_triage", "checks.suites",
                "sandbox.image", "sandbox.cpu", "sandbox.memory", "sandbox.max_file_bytes", "sandbox.egress_extra", "publish.mode"}
    assert expected <= set(FIELD_BY_PATH)
    rules = public_settings()["rules"]
    assert rules["values"]["sandbox.max_tokens"] == 50_000_000 and rules["overrides"] == {}
    assert all(field["label"] and field["kind"] for section in rules["schema"] for field in section["fields"])


def test_rules_save_validate_and_reset(home):
    from mu.jobs.job_settings import public_settings, save_settings
    from mu.jobs.sandbox.policy import SandboxPolicy
    save_settings({"sandbox": {"max_tokens": 80_000_000, "count_cache_reads": True, "stage_timeout_s": {"implement": 7200}}})
    policy = SandboxPolicy.for_stage("implement")
    assert (policy.max_tokens, policy.count_cache_reads, policy.timeout) == (80_000_000, True, 7200)
    save_settings({"sandbox": {"stage_timeout_s": {"implement": None}, "count_cache_reads": None}})
    assert SandboxPolicy.for_stage("implement").timeout == 3600
    assert public_settings()["rules"]["overrides"] == {"sandbox.max_tokens": 80_000_000}
    for bad in ({"checks": {"max_fix_attempts": -1}}, {"limits": {"max_iterations": 0}}, {"sandbox": {"count_cache_reads": "yes"}},
                {"publish": {"mode": "ftp"}}, {"checks": {"suites": [{"name": "unit"}]}}, {"sandbox": {"egress_extra": ["bad domain"]}}):
        with pytest.raises(ValueError):
            save_settings(bad)


def test_iteration_and_publish_rules_apply_to_jobs_without_their_own(home):
    from mu.jobs.job_settings import save_settings
    from mu.jobs.models import stage_iteration_limit
    from mu.jobs.stages import publish_mode
    job = SimpleNamespace(max_iterations=None, execution={})
    assert stage_iteration_limit(job, "implement") == 1000 and publish_mode(job) == "none"
    save_settings({"limits": {"max_iterations": 1500, "stage_max_iterations": {"review": 300}}, "publish": {"mode": "gh"}})
    assert stage_iteration_limit(job, "implement") == 1500 and stage_iteration_limit(job, "review") == 300
    assert publish_mode(job) == "gh"
    own = SimpleNamespace(max_iterations=42, execution={"publish_mode": "none"})
    assert stage_iteration_limit(own, "implement") == 42 and publish_mode(own) == "none"


def test_settings_endpoint_round_trips_rules(home):
    from fastapi import FastAPI
    from mu.gui.routers.jobs import router
    app = FastAPI()
    app.include_router(router, prefix="/api/jobs")
    client = TestClient(app)
    body = client.put("/api/jobs/settings", json={"sandbox": {"max_tokens": 60_000_000}}).json()
    assert body["rules"]["values"]["sandbox.max_tokens"] == 60_000_000
    assert client.put("/api/jobs/settings", json={"checks": {"max_fix_attempts": 999}}).status_code == 400


def test_job_time_limits_default_to_an_hour_and_stage_bash_uses_the_tool_timeout(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import mu.tools.shell.handlers as shell
    from mu.jobs.job_settings import FIELD_BY_PATH
    from mu.jobs.sandbox.policy import SandboxPolicy
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    assert {s: SandboxPolicy.for_stage(s, {}).timeout for s in ("plan", "implement", "review", "checks")} == \
        dict.fromkeys(("plan", "implement", "review", "checks"), 3600)
    for path in ("sandbox.tool_timeout_s", "sandbox.model_request_timeout_s", "checks.command_timeout_s", "publish.timeout_s"):
        assert FIELD_BY_PATH[path]["default"] == 3600, path
    seen = []
    monkeypatch.setattr(shell, "bash_command", lambda *a, **k: seen.append(k["timeout_seconds"]))
    stage = SimpleNamespace(folder_context=None, variables={"bash_default_timeout_s": 3600}, session=None)
    shell._bash_tool({"command": "true"}, stage)
    shell._bash_tool({"command": "true", "timeout_seconds": 30}, stage)
    shell._bash_tool({"command": "true"}, SimpleNamespace(folder_context=None, variables={}, session=None))
    assert seen == [3600, 30, 120]
