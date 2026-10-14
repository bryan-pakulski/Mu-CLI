from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from providers.base import Message, MessagePart, StreamEvent
from mu.jobs.core.artifacts import JobArtifacts
from mu.jobs.sandbox.model_relay import ModelRelay, RelayDenied, wire, messages_from_wire
from mu.jobs.sandbox.policy import SandboxPolicy, container_args, validate_changes


class Provider:
    model_name = ""
    calls = 0

    def stream(self, messages, **kwargs):
        self.calls += 1
        yield StreamEvent(kind="text_delta", text="hello")
        yield StreamEvent(kind="usage", input_tokens=20, output_tokens=4, total_tokens=24)
        yield StreamEvent(kind="done")


def relay(tmp_path, **kwargs):
    provider = Provider()
    art = JobArtifacts("j", str(tmp_path)).ensure()
    obj = ModelRelay(SimpleNamespace(id="j"), "plan", {"provider": "openai", "model": "gpt-5.6-sol"},
                     SandboxPolicy(), art, provider_factory=lambda p, m: provider, **kwargs)
    return obj, provider, art


@pytest.mark.parametrize("stage", ["plan", "implement", "review", "checks"])
def test_stage_has_no_credentials_host_network_or_gitdir(tmp_path, monkeypatch, stage):
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GITHUB_TOKEN", "SSH_AUTH_SOCK", "JIRA_TOKEN"):
        monkeypatch.setenv(key, "secret-canary")
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / ".git").write_text("gitdir: /host/repo/.git/worktrees/job")
    dirs = [tmp_path / n for n in ("input", "output", "relay")]
    for path in dirs:
        path.mkdir()
    args = container_args("stage", stage, str(ws), *map(str, dirs), SandboxPolicy(), "stage-token")
    rendered = " ".join(args)
    assert "secret-canary" not in rendered
    assert "/host/repo" not in rendered
    assert "--network none" in rendered and "--cap-drop ALL" in rendered
    assert "--user" in args and "--read-only" in args
    workspace = next(x for x in args if "dst=/workspace" in x and ".git" not in x)
    assert workspace.endswith(",readonly") == (stage not in ("implement", "checks"))
    assert f"dst=/workspace/.git,readonly" in rendered
    assert "dst=/artifacts,readonly" in rendered
    assert "docker.sock" not in rendered


def _sandbox_ws(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / ".git").write_text("gitdir: /host/repo/.git/worktrees/job")
    dirs = [tmp_path / n for n in ("input", "output", "relay")]
    for path in dirs:
        path.mkdir()
    return ws, dirs


@pytest.mark.parametrize("name", [".env.example", ".env.inference.example", ".env.sample",
                                  "sub/.env.template", ".env.dist"])
def test_sandbox_allows_committed_env_templates(tmp_path, name):
    ws, dirs = _sandbox_ws(tmp_path)
    (ws / name).parent.mkdir(parents=True, exist_ok=True)
    (ws / name).write_text("API_KEY=changeme\n")
    container_args("stage", "plan", str(ws), *map(str, dirs), SandboxPolicy(), "t")


@pytest.mark.parametrize("name", [".env", ".env.local", ".env.production", ".env.example.local",
                                  "deploy/.env", "server.pem"])
def test_sandbox_still_rejects_real_secrets(tmp_path, name):
    ws, dirs = _sandbox_ws(tmp_path)
    (ws / name).parent.mkdir(parents=True, exist_ok=True)
    (ws / name).write_text("secret")
    with pytest.raises(ValueError, match="secret path must be removed"):
        container_args("stage", "plan", str(ws), *map(str, dirs), SandboxPolicy(), "t")


def test_relay_forces_host_model_and_revokes_token(tmp_path):
    obj, provider, art = relay(tmp_path)
    with pytest.raises(RelayDenied):
        list(obj.stream("wrong", {}))
    with pytest.raises(RelayDenied):
        list(obj.stream(obj.token, {"model": "expensive-model"}))
    events = list(obj.stream(obj.token, {"messages": wire([Message("user", [MessagePart("text", text="hi")])])}))
    assert events[0]["text"] == "hello"
    assert provider.model_name == "gpt-5.6-sol" and provider.calls == 1
    assert obj.tokens == 24 and obj.cost > 0
    assert list(art.iter_costs())[0]["stage"] == "plan"
    obj.close()
    with pytest.raises(RelayDenied):
        list(obj.stream(obj.token, {}))


def test_relay_rejects_exhausted_budget_and_lost_lease(tmp_path):
    obj, provider, _ = relay(tmp_path)
    obj.policy = replace(obj.policy, max_tokens=1)
    with pytest.raises(RelayDenied):
        list(obj.stream(obj.token, {"messages": []}))
    assert provider.calls == 0
    obj.policy = replace(obj.policy, max_tokens=1000, max_cost_usd=0)
    with pytest.raises(RelayDenied):
        list(obj.stream(obj.token, {}))
    obj.alive = lambda: False
    with pytest.raises(RelayDenied):
        list(obj.stream(obj.token, {}))


def test_interrupted_stream_usage_survives_into_the_next_stage_budget(tmp_path):
    obj, provider, art = relay(tmp_path)
    def interrupted(*args, **kwargs):
        yield StreamEvent(kind="text_delta", text="a partial answer before disconnect")
        raise RuntimeError("provider disconnected before usage")
    provider.stream = interrupted
    with pytest.raises(RuntimeError, match="disconnected"):
        list(obj.stream(obj.token, {"messages": wire([Message("user", [MessagePart("text", text="Explain the change")])])}))
    usage = list(art.iter_costs())[0]
    assert usage["estimated"] and usage["cost_usd"] > 0 and obj.tokens > 0
    # The old job cost has not yet been updated by a completed stage.
    resumed = ModelRelay(SimpleNamespace(id="j", max_cost_usd=obj.cost / 2, cost_usd=0),
                         "implement", obj.model, SandboxPolicy(), art, provider_factory=lambda p,m:provider)
    with pytest.raises(RelayDenied, match="budget exhausted"):
        list(resumed.stream(resumed.token, {}))


def test_relay_http_rejects_token_before_provider(tmp_path):
    obj, provider, _ = relay(tmp_path)
    with TestClient(obj.app()) as client:
        assert client.post("/stream", json={}).status_code == 403
        response = client.post("/stream", json={"messages": []}, headers={"X-Mucli-Stage-Token": obj.token})
        assert response.status_code == 200
        assert json.loads(response.text.splitlines()[0])["kind"] == "text_delta"
    assert provider.calls == 1


@pytest.mark.parametrize("name", ["../escape", "/etc/passwd", ".git/config", ".github/workflows/release.yml"])
def test_commit_guard_rejects_unsafe_paths(tmp_path, name):
    with pytest.raises(ValueError):
        validate_changes(str(tmp_path), [name])


def test_commit_guard_allows_only_approved_workflow_and_rejects_escape_and_size(tmp_path):
    name = ".github/workflows/check.yml"
    validate_changes(str(tmp_path), [name], allowed_workflows=[name])
    (tmp_path / "outside").symlink_to("/etc/passwd")
    with pytest.raises(ValueError, match="escapes"):
        validate_changes(str(tmp_path), ["outside"])
    (tmp_path / "big").write_text("1234")
    with pytest.raises(ValueError, match="size"):
        validate_changes(str(tmp_path), ["big"], max_file_bytes=3)


def test_stage_policies_only_allow_opt_in_registry_egress_for_writing_stages():
    settings = {"sandbox": {"egress_extra": ["pypi.org"]}}
    assert SandboxPolicy.for_stage("plan", settings).egress_extra == ()
    assert SandboxPolicy.for_stage("review", settings).egress_extra == ()
    assert SandboxPolicy.for_stage("implement", settings).egress_extra == ("pypi.org",)


def test_build_context_never_copies_host_state_or_reference_archive(tmp_path):
    from mu.jobs.sandbox.build import prepare_context
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    for name in ("mu", "providers", "utils", ".git", ".planos_review"):
        (source / name).mkdir()
        (source / name / "file.py").write_text("# source")
    (source / "mu" / ".env").write_text("secret")
    (source / "mucli.py").write_text("# entry")
    (source / "requirements.txt").write_text("httpx\n# For voice / audio\ntorch\n")
    prepare_context(source, target)
    assert not (target / ".git").exists() and not (target / ".planos_review").exists()
    assert not (target / "mu" / ".env").exists()
    assert (target / "mu" / "file.py").exists()


def test_production_stages_use_sandbox(monkeypatch):
    from mu.jobs import stages
    from mu.jobs.sandbox import runner
    calls = []
    monkeypatch.setattr(runner, "run_stage_in_container", lambda *a, **k: calls.append(a) or
                        {"kind": "completed", "text": "{}", "cost_usd": 0})
    result = stages.default_generate("service")("job", "prompt", "/workspace", {"stage": "review"})
    assert result == ("{}", {"cost_usd": 0})
    assert calls == [("service", "job", "review", "prompt")]
