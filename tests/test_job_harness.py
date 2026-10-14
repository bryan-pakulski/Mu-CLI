"""Agent harnesses (mucli / Claude Code / Codex) per job stage: selection, settings, credentials,
command lines, event translation (recorded streams), relay accounting and container plumbing.
Design: documentation/plans/agent_harnesses.md."""
import json
import os
import stat
from types import SimpleNamespace

import pytest

from mu.jobs import harness, harness_cli as cli, job_settings


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    for key in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    return tmp_path


def job(**agents):
    return SimpleNamespace(execution={"stage_agents": agents} if agents else {})


# ------------------------------------------------------------------ selection


def test_stage_agents_are_validated_and_parsed():
    assert harness.clean_stage_agents({"plan": "claude", "implement": {"agent": "codex", "model": "gpt-5.5"}}) == {
        "plan": {"agent": "claude"}, "implement": {"agent": "codex", "model": "gpt-5.5"}}
    for bad in ({"checks": "claude"}, {"plan": "gemini"}, {"plan": {"agent": "claude", "model": "a b"}},
                {"plan": {"agent": "claude", "effort": "huge"}}, ["claude"]):
        with pytest.raises(ValueError):
            harness.clean_stage_agents(bad)
    assert harness.parse_agent_spec("claude") == {s: {"agent": "claude"} for s in harness.STAGES}
    assert harness.parse_agent_spec("plan=claude,implement=codex:gpt-5.5") == {
        "plan": {"agent": "claude"}, "implement": {"agent": "codex", "model": "gpt-5.5"}}


def test_stage_agent_precedence_job_then_settings():
    assert harness.stage_agent(job(), "plan") == {"agent": "mucli", "model": "", "effort": "", "source": "settings"}
    job_settings.save_settings({"harness": {"default": {"review": "codex"}, "codex": {"models": {"review": "gpt-5.5"}}},
                                "jobs": {"stage_effort": {"review": "high"}}})
    assert harness.stage_agent(job(), "review") == {"agent": "codex", "model": "gpt-5.5", "effort": "high", "source": "settings"}
    picked = harness.stage_agent(job(plan={"agent": "claude"}), "plan")
    assert picked["agent"] == "claude" and picked["model"] == "claude-opus-5-5" and picked["source"] == "job"
    assert harness.stage_agent(job(implement="claude"), "implement")["model"] == "claude-sonnet-5-5"
    assert harness.stage_agent(job(plan={"agent": "claude", "model": "claude-haiku-5-5"}), "plan")["model"] == "claude-haiku-5-5"


def test_harness_stages_need_no_mucli_model():
    assert job_settings.missing_stages({}) == ["plan", "implement", "review"]
    assert job_settings.missing_stages({"stage_agents": {"plan": "claude", "review": {"agent": "codex"}}}) == ["implement"]


def test_invalid_harness_settings_are_rejected():
    with pytest.raises(ValueError):
        job_settings.save_settings({"harness": {"default": {"plan": "gpt"}}})
    with pytest.raises(ValueError):
        job_settings.save_settings({"harness": {"claude": {"binary": "claude; rm"}}})
    assert any(section["id"] == "harness" for section in job_settings.RULES_SCHEMA)


def test_job_creation_stamps_agents(tmp_path):
    from mu.jobs import JobService, JobStore
    service = JobService(JobStore(str(tmp_path / "jobs.db")))
    plain = service.create_from_payload({"title": "plain"})
    assert "stage_agents" not in plain.execution
    chosen = service.create_from_payload({"title": "t", "execution": {"stage_agents": {"plan": {"agent": "claude", "model": "m-1"}}}})
    assert chosen.execution["stage_agents"] == {"plan": {"agent": "claude", "model": "m-1"}, "implement": {"agent": "mucli"},
                                                "review": {"agent": "mucli"}}
    job_settings.save_settings({"harness": {"default": {"implement": "codex"}}})
    defaulted = service.create_from_payload({"title": "d"})
    assert defaulted.execution["stage_agents"]["implement"] == {"agent": "codex"}
    with pytest.raises(ValueError):
        service.create_from_payload({"title": "bad", "execution": {"stage_agents": {"plan": "nope"}}})


# ------------------------------------------------------------------ credentials and secrets


def test_claude_credentials_follow_planos_order(home, monkeypatch):
    assert harness.credentials("claude") is None
    (home / "claude").mkdir()
    (home / "claude" / ".credentials.json").write_text(json.dumps({"claudeAiOauth": {"expiresAt": 4102444800000}}))
    login = harness.credentials("claude")
    assert login["kind"] == "login" and login["files"] and login["expires_at"] == 4102444800 and login["warning"]
    harness.write_secret("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-token")
    assert harness.credentials("claude")["env"] == {"CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-token"}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-key")
    assert harness.credentials("claude")["kind"] == "api_key"
    job_settings.save_settings({"harness": {"claude": {"auth": "login"}}})
    assert harness.credentials("claude")["kind"] == "login"


def test_codex_prefers_its_login_like_codex_does(home, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    assert harness.credentials("codex")["kind"] == "api_key"
    (home / "codex").mkdir()
    (home / "codex" / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt"}))
    assert harness.credentials("codex")["kind"] == "login"
    job_settings.save_settings({"harness": {"codex": {"auth": "api_key"}}})
    assert harness.credentials("codex")["env"] == {"OPENAI_API_KEY": "sk-openai"}


def test_secrets_file_is_private_and_never_echoed(home):
    harness.write_secret("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-very-secret-value")
    path = harness.secrets_path()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    status = harness.status()
    assert status["secrets"]["CLAUDE_CODE_OAUTH_TOKEN"] == "secrets.env"
    assert "very-secret" not in json.dumps(status)
    harness.write_secret("CLAUDE_CODE_OAUTH_TOKEN", "")
    assert harness.read_secrets() == {}
    for key, value in (("PATH", "x"), ("ANTHROPIC_API_KEY", "a\nb"), ("OPENAI_API_KEY", "it's")):
        with pytest.raises(ValueError):
            harness.write_secret(key, value)


def test_stage_credentials_are_written_privately(home, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-key")
    info = harness.stage_credentials("claude", str(home / "creds"))
    assert "env" not in info and info["kind"] == "api_key"
    assert json.loads((home / "creds" / "env.json").read_text()) == {"ANTHROPIC_API_KEY": "sk-ant-key"}
    assert stat.S_IMODE(os.stat(home / "creds" / "env.json").st_mode) == 0o600
    with pytest.raises(PermissionError):
        harness.stage_credentials("codex", str(home / "creds2"))


def test_status_reports_problems_and_setup(home, monkeypatch):
    job_settings.save_settings({"harness": {"claude": {"binary": str(home / "missing")}, "codex": {"binary": str(home / "missing")}}})
    status = harness.status()
    assert not status["claude"]["ready"] and any("not installed" in p for p in status["claude"]["problems"])
    assert any("No credentials" in p for p in status["codex"]["problems"]) and status["setup"]["codex"]
    fake = home / "bin" / "codex"
    fake.parent.mkdir()
    fake.write_text("#!/bin/sh\necho codex-cli 9.9\n")
    fake.chmod(0o755)
    monkeypatch.setenv("OPENAI_API_KEY", "sk")
    job_settings.save_settings({"harness": {"codex": {"binary": str(fake)}}})
    codex = harness.status()["codex"]
    assert codex["ready"] and codex["version"] == "codex-cli 9.9" and codex["binary_kind"] == "static"
    assert any("automatic approval review" in w for w in codex["warnings"])


def test_binary_mounts_and_state_copy(home):
    release = home / "rel"
    (release / "bin").mkdir(parents=True)
    (release / "codex-package.json").write_text("{}")
    (release / "bin" / "codex").write_text("#!/bin/sh\n")
    (release / "bin" / "codex").chmod(0o755)
    job_settings.save_settings({"harness": {"codex": {"binary": str(release / "bin" / "codex")}}})
    assert harness.binary_mounts("codex") == [(str(release), "/opt/harness/codex", True)]
    src = home / "state"
    (src / "a").mkdir(parents=True)
    (src / "a" / "s.jsonl").write_text("x")
    (src / "link").symlink_to("/etc/passwd")
    harness.copy_state(str(src), str(home / "copy"))
    assert (home / "copy" / "a" / "s.jsonl").read_text() == "x" and not (home / "copy" / "link").exists()


# ------------------------------------------------------------------ command lines


def test_claude_args_follow_the_approval_policy():
    gated = cli.claude_args(model="m", read_only=False, auto_approve=False, resume="sid", system="SYS", effort="max")
    assert gated[:2] == [cli.CLAUDE_BIN, "-p"] and "--dangerously-skip-permissions" not in gated
    assert gated[gated.index("--permission-mode") + 1] == "manual" and "--permission-prompt-tool" in gated
    assert gated[gated.index("--resume") + 1] == "sid" and gated[gated.index("--effort") + 1] == "max"
    assert "--strict-mcp-config" in gated
    plan = cli.claude_args(model="m", read_only=True, auto_approve=False)
    assert "--dangerously-skip-permissions" in plan and plan[plan.index("--disallowed-tools") + 1:][:3] == list(cli.READ_ONLY_DISALLOWED)
    yolo = cli.claude_args(model="m", read_only=False, auto_approve=True)
    assert "--dangerously-skip-permissions" in yolo and "--disallowed-tools" not in yolo
    assert cli.claude_env(60)["BASH_MAX_TIMEOUT_MS"] == "60000"


def test_codex_args_follow_the_approval_policy():
    gated = cli.codex_args(model="", read_only=False, auto_approve=False, effort="max")
    assert gated[:2] == [f"{cli.CODEX_ROOT}/bin/codex", "exec"] and "--approve-for-me" in gated and gated[-1] == "-"
    assert "-m" not in gated and "model_reasoning_effort=xhigh" in gated
    assert "--dangerously-bypass-approvals-and-sandbox" in cli.codex_args(model="x", read_only=True, auto_approve=False)
    resumed = cli.codex_args(model="gpt-5.5", read_only=False, auto_approve=True, resume="th")
    assert resumed[2:4] == ["resume", "th"] and resumed[resumed.index("-m") + 1] == "gpt-5.5"


def test_permission_replies_match_the_control_protocol():
    allow = json.loads(cli.claude_permission_reply("r1", allow=True, tool_input={"a": 1}))
    assert allow == {"type": "control_response", "response": {"subtype": "success", "request_id": "r1",
                                                               "response": {"behavior": "allow", "updatedInput": {"a": 1}}}}
    deny = json.loads(cli.claude_permission_reply("r2", allow=False, message="no"))["response"]["response"]
    assert deny == {"behavior": "deny", "message": "no"}
    assert json.loads(cli.claude_user_frame("hi"))["message"] == {"role": "user", "content": "hi"}


# ------------------------------------------------------------------ translation (recorded shapes)

CLAUDE_STREAM = [
    {"type": "system", "subtype": "init", "cwd": "/workspace", "session_id": "5e49", "model": "claude-haiku-5-5"},
    {"type": "assistant", "message": {"id": "m1", "model": "claude-haiku-5-5", "content": [
        {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/workspace/calc.py"}}]}},
    {"type": "user", "message": {"role": "user", "content": [{"tool_use_id": "t1", "type": "tool_result",
                                                              "content": "1\tdef add(a, b):"}]}},
    {"type": "assistant", "message": {"id": "m2", "model": "claude-haiku-5-5", "content": [{"type": "text", "text": "DONE"}]}},
    {"type": "result", "subtype": "success", "is_error": False, "num_turns": 2, "total_cost_usd": 0.0025,
     "session_id": "5e49", "result": "DONE", "usage": {"input_tokens": 4, "cache_creation_input_tokens": 100,
                                                        "cache_read_input_tokens": 900, "output_tokens": 89}},
]

CODEX_STREAM = [
    {"type": "thread.started", "thread_id": "01a1"}, {"type": "turn.started"},
    {"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "I'll read it."}},
    {"type": "item.started", "item": {"id": "item_1", "type": "command_execution", "command": "/usr/bin/bash -lc 'cat calc.py'",
                                      "aggregated_output": "", "exit_code": None, "status": "in_progress"}},
    {"type": "item.completed", "item": {"id": "item_1", "type": "command_execution", "command": "/usr/bin/bash -lc 'cat calc.py'",
                                        "aggregated_output": "def add", "exit_code": 0, "status": "completed"}},
    {"type": "item.started", "item": {"id": "item_2", "type": "command_execution", "command": "python3 a.py"}},
    {"type": "item.completed", "item": {"id": "item_2", "type": "command_execution", "command": "python3 a.py",
                                        "aggregated_output": "", "exit_code": None, "status": "declined"}},
    {"type": "item.completed", "item": {"id": "item_3", "type": "agent_message", "text": "{\"bug\": \"subtracts\"}"}},
    {"type": "turn.completed", "usage": {"input_tokens": 28629, "cached_input_tokens": 26368, "cache_write_input_tokens": 0,
                                         "output_tokens": 81, "reasoning_output_tokens": 0}},
]


def test_claude_stream_translates_to_mucli_history():
    state = cli.Translation(agent="claude")
    events = [ui for event in CLAUDE_STREAM for ui in cli.translate("claude", event, state)]
    assert [e["event"] for e in events] == ["runtime_info", "tool_call_ui", "tool_result_ui", "agent_message"]
    assert state.session_id == "5e49" and state.final_text == "DONE" and state.cost_usd == 0.0025 and not state.is_error
    assert state.usage["input_tokens"] == 1004 and state.usage["cached_tokens"] == 900
    call = state.history[0]["parts"][0]
    assert call["tool_name"] == "read_file" and call["tool_args"] == {"filename": "/workspace/calc.py"}
    assert state.history[1]["role"] == "tool" and state.history[1]["parts"][0]["tool_call_id"] == "t1"


def test_codex_stream_translates_to_mucli_history():
    state = cli.Translation(agent="codex")
    events = [ui for event in CODEX_STREAM for ui in cli.translate("codex", event, state)]
    assert state.session_id == "01a1" and state.final_text == "{\"bug\": \"subtracts\"}" and state.finished
    assert state.usage == {"input_tokens": 28629, "output_tokens": 81, "cached_tokens": 26368, "cache_write_tokens": 0,
                           "reasoning_tokens": 0}
    assert sum(1 for e in events if e["event"] == "tool_call_ui") == 2
    results = [p for m in state.history if m["role"] == "tool" for p in m["parts"]]
    assert results[1]["is_error"] and "declined" in results[1]["tool_result"]


def test_usage_limits_and_failures_are_detected():
    state = cli.Translation(agent="claude")
    cli.translate("claude", {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "resetsAt": 1760000000}}, state)
    assert state.usage_limit and state.resets_at == 1760000000
    other = cli.Translation(agent="claude")
    cli.translate("claude", {"type": "result", "subtype": "success", "is_error": True,
                             "result": "Claude AI usage limit reached|1760000000"}, other)
    assert other.usage_limit and other.is_error
    assert cli.claude_usage_limit("You've hit your limit · resets 3pm") and not cli.claude_usage_limit("all good")
    codex = cli.Translation(agent="codex")
    cli.translate("codex", {"type": "error", "message": "Reconnecting... 1/5"}, codex)
    assert not codex.is_error  # transient
    cli.translate("codex", {"type": "turn.failed", "error": {"message": "You've hit your usage limit."}}, codex)
    assert codex.is_error and codex.usage_limit and codex.finished


# ------------------------------------------------------------------ relay and container plumbing


def test_harness_relay_records_usage_and_enforces_the_budget(tmp_path):
    from mu.jobs.core.artifacts import JobArtifacts
    from mu.jobs.sandbox.model_relay import HarnessRelay, RelayDenied
    from mu.jobs.sandbox.policy import SandboxPolicy
    art = JobArtifacts("j1", root=str(tmp_path / "jobs")).ensure()
    relay = HarnessRelay(SimpleNamespace(max_cost_usd=None), "implement", {"provider": "claude", "model": "claude-sonnet-5-5"},
                         SandboxPolicy(max_cost_usd=1.0), art, agent="claude")
    token = relay.token
    with pytest.raises(RelayDenied):
        list(relay.stream(token, {}))
    assert relay.record_usage(token, {"input_tokens": 100_000, "output_tokens": 1000}) == {"stop": False}
    estimated = relay.cost
    assert estimated > 0 and relay.tokens == 101_000
    relay.record_usage(token, {"total_cost_usd": 0.5, "total_only": True})
    assert relay.cost == pytest.approx(0.5) and relay.tokens == 101_000  # the CLI's total replaces the estimate
    assert relay.record_usage(token, {"total_cost_usd": 1.2, "total_only": True})["stop"]
    assert sum(art.stage_costs().values()) == pytest.approx(1.2)
    with pytest.raises(RelayDenied):
        relay.record_usage("bad-token", {})
    codex = HarnessRelay(SimpleNamespace(max_cost_usd=None), "plan", {"provider": "codex", "model": ""}, SandboxPolicy(), art, agent="codex")
    codex.record_usage(codex.token, {"model": "gpt-5.5", "input_tokens": 1_000_000})
    assert codex.model["model"] == "gpt-5.5" and codex.cost == pytest.approx(10.0)


def test_container_args_accept_only_fixed_harness_mounts(tmp_path):
    from mu.jobs.sandbox.policy import SandboxPolicy, container_args
    dirs = [tmp_path / d for d in ("ws", "in", "out", "relay", "creds", "state")]
    for d in dirs:
        d.mkdir()
    binary = tmp_path / "claude"
    binary.write_text("x")
    base = (str(dirs[0]), *map(str, dirs[1:4]))
    args = container_args("n", "plan", *base, SandboxPolicy(), "tok", module="mu.jobs.sandbox.harness_worker",
                          harness_mounts=((str(binary), "/opt/harness/claude", True), (str(dirs[4]), "/harness/creds", True),
                                          (str(dirs[5]), "/harness/state", False)))
    joined = " ".join(args)
    assert f"src={binary},dst=/opt/harness/claude,readonly" in joined and "dst=/harness/state " in joined + " "
    assert args[-2:] == ["-m", "mu.jobs.sandbox.harness_worker"] and "--network none" in joined
    for bad in (((str(dirs[4]), "/etc", True),), ((str(dirs[4]), "/harness/creds", False),)):
        with pytest.raises(ValueError):
            container_args("n", "plan", *base, SandboxPolicy(), "tok", harness_mounts=bad)
    with pytest.raises(ValueError):
        container_args("n", "plan", *base, SandboxPolicy(), "tok", module="os")


def test_missing_setup_pauses_the_stage_with_a_fix(tmp_path, monkeypatch):
    from mu.jobs.sandbox import runner
    job_settings.save_settings({"harness": {"claude": {"binary": str(tmp_path / "nope")}}})
    monkeypatch.setattr(runner, "stage_environment", lambda service, job, stage: ([], None))

    class Docker:
        def require(self, name):
            return "docker"

        def run(self, *a, **k):
            return SimpleNamespace(returncode=0, stdout="", stderr="")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    service = SimpleNamespace(store=SimpleNamespace(event_cursor=lambda: 0))
    j = SimpleNamespace(id="j" * 12, execution={"stage_agents": {"plan": "claude"}}, worktree=str(worktree), metadata={})
    with pytest.raises(runner.StagePaused, match="Agent harnesses"):
        runner._run_stage_once(service, j, "plan", "p", runner=Docker())


# ------------------------------------------------------------------ API


def test_agents_api_never_returns_secret_values(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from mu.gui.routers.jobs import router
    app = FastAPI()
    app.include_router(router, prefix="/api/jobs")
    client = TestClient(app)
    saved = client.put("/api/jobs/agents/secret", json={"key": "CLAUDE_CODE_OAUTH_TOKEN", "value": "sk-ant-oat-zzz-secret"})
    assert saved.status_code == 200 and "zzz-secret" not in saved.text
    assert saved.json()["secrets"]["CLAUDE_CODE_OAUTH_TOKEN"] == "secrets.env"
    status = client.get("/api/jobs/agents").json()
    assert set(status) >= {"mucli", "claude", "codex", "defaults", "secrets", "setup"}
    assert client.put("/api/jobs/agents/secret", json={"key": "HOME", "value": "x"}).status_code == 400
