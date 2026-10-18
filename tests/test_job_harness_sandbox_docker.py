"""Sandbox permissions of job stage containers for every agent harness (mucli, Claude Code, Codex),
checked two ways on a real container (require Docker and the mucli-job-runtime image):

* host side: ``docker inspect`` of the created stage container (network, rootfs, capabilities,
  user, mounts and their modes, environment);
* inside: a probe run *as the agent process* (fake CLIs speaking the recorded protocols, or
  mucli's own bash tool) reporting capabilities, writable paths, network and egress results.
"""
import base64
import json
import os
import subprocess
import textwrap

import pytest

from mu.container.docker_cli import CommandRunner
from mu.jobs import JobService, JobSpec, JobStore, job_settings
from mu.jobs.sandbox.model_relay import ModelRelay
from mu.jobs.sandbox.runner import run_stage_in_container
from mu.jobs.worktree import JobWorktreeManager
from providers.base import StreamEvent

from mu.jobs.sandbox.selftest import PROBE, check_probe
PROBE_B64 = base64.b64encode(PROBE.encode()).decode()
PROBE_CMD = f"python3 -c \"import base64;exec(base64.b64decode('{PROBE_B64}'))\""

FAKE_CLAUDE = textwrap.dedent(f'''\
    #!/usr/bin/env python3
    import base64, contextlib, io, json, sys
    def out(obj):
        print(json.dumps(obj), flush=True)
    sys.stdin.readline()
    out({{"type": "system", "subtype": "init", "session_id": "probe-session", "model": "fake"}})
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        exec(base64.b64decode("{PROBE_B64}"))
    out({{"type": "assistant", "message": {{"id": "m1", "model": "fake", "content": [{{"type": "text", "text": buffer.getvalue()}}]}}}})
    out({{"type": "result", "subtype": "success", "is_error": False, "session_id": "probe-session", "num_turns": 1,
          "total_cost_usd": 0.0, "result": "done", "usage": {{}}}})
    sys.stdin.read()
''')

FAKE_CODEX = textwrap.dedent(f'''\
    #!/usr/bin/env python3
    import base64, contextlib, io, json, sys
    sys.stdin.read()
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        exec(base64.b64decode("{PROBE_B64}"))
    for event in ({{"type": "thread.started", "thread_id": "probe-thread"}},
                  {{"type": "item.completed", "item": {{"id": "i1", "type": "agent_message", "text": buffer.getvalue()}}}},
                  {{"type": "turn.completed", "usage": {{"input_tokens": 1, "output_tokens": 1}}}}):
        print(json.dumps(event), flush=True)
''')

SECRET = "sk-ant-sandbox-probe-secret"


class InspectingRunner(CommandRunner):
    """Records ``docker inspect`` of each stage container right before it starts."""

    def __init__(self):
        super().__init__()
        self.inspected = {}

    def run(self, args, **kwargs):
        args = [str(a) for a in args]
        if args[1:3] == ["start", "-a"]:
            info = subprocess.run([args[0], "inspect", args[3]], capture_output=True, text=True, check=True)
            self.inspected[args[3]] = json.loads(info.stdout)[0]
        return super().run(args, **kwargs)


@pytest.fixture
def docker():
    try:
        result = subprocess.run(["docker", "image", "inspect", "mucli-job-runtime:latest"], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("Docker unavailable")
    if result.returncode:
        pytest.skip("Docker or mucli-job-runtime image unavailable")


def _service(tmp_path, monkeypatch, agent):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    monkeypatch.setenv("OPENAI_API_KEY", SECRET.replace("ant", "oai"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))  # no login: Codex uses the API key
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    git("init", "-b", "main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    (repo / "readme.txt").write_text("a workspace")
    git("add", ".")
    git("commit", "-m", "base")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("claude", FAKE_CLAUDE), ("codex", FAKE_CODEX)):
        (bindir / name).write_text(body)
        (bindir / name).chmod(0o755)
    job_settings.save_settings({"harness": {"claude": {"binary": str(bindir / "claude")},
                                            "codex": {"binary": str(bindir / "codex")}}})
    service = JobService(JobStore(str(tmp_path / "jobs.db")))
    execution = {"pipeline": "staged", "auto_approve_writes": True, "provider": "openai", "model": "gpt-5.6-sol"}
    if agent != "mucli":
        execution["stage_agents"] = {"plan": agent, "implement": agent}
    job = service.create(JobSpec(title=f"sandbox {agent}", repository=str(repo), execution=execution))
    JobWorktreeManager(service, root=str(tmp_path / "worktrees")).prepare(job)
    return service, service.get(job.id)


class MucliProbe:
    """A stub model that runs the probe through mucli's bash tool (implement) or just answers (plan)."""

    def __init__(self, stage):
        self.stage, self.calls = stage, 0

    def stream(self, messages, **kwargs):
        self.calls += 1
        if self.stage == "implement" and self.calls == 1:
            yield StreamEvent(kind="tool_call_complete", tool_name="bash", tool_call_id="probe-1",
                              tool_args={"command": PROBE_CMD})
        else:
            yield StreamEvent(kind="text_delta", text="done")
        yield StreamEvent(kind="usage", input_tokens=10, output_tokens=10, total_tokens=20)
        yield StreamEvent(kind="done")


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _probe_from(service, job, stage, result):
    """The probe report: the agent's final text (Claude/Codex) or mucli's bash tool result."""
    from mu.jobs.core.artifacts import artifacts_for
    transcript = artifacts_for(service.get(job.id)).read_json(f"logs/{stage}-transcript.json", []) or []
    previews = [e.payload.get("payload") for e in service.events(job.id) if e.event_type == "sandbox_event"
                and e.payload.get("stage") == stage]
    for text in [result.get("text") or "", *_strings(previews), *_strings(transcript)]:
        if text.lstrip().startswith("{") and "PROBE:" in text:
            try:  # a JSON-encoded tool result
                yield_from = list(_strings(json.loads(text)))
            except ValueError:
                yield_from = []
            for inner in yield_from:
                found = _probe_line(inner)
                if found:
                    return found
        found = _probe_line(text)
        if found:
            return found
    return None


def _probe_line(text):
    for line in text.splitlines():
        if line.startswith("PROBE:"):
            try:
                return json.loads(line[len("PROBE:"):])
            except ValueError:
                continue  # a shortened copy (e.g. the folded transcript); keep looking
    return None


def _check_container(info, agent, stage):
    host, config = info["HostConfig"], info["Config"]
    assert host["NetworkMode"] == "none"
    assert host["ReadonlyRootfs"] is True and host["Privileged"] is False
    assert host["CapDrop"] == ["ALL"] and not host.get("CapAdd")
    assert "no-new-privileges:true" in (host.get("SecurityOpt") or [])
    assert config["User"] == f"{os.getuid()}:{os.getgid()}"
    assert config["Labels"]["io.mucli.role"] == "job-stage"
    mounts = {m["Destination"]: m["RW"] for m in info["Mounts"]}
    expected = {"/workspace": stage == "implement", "/workspace/.git": False, "/artifacts": False, "/output": True,
                "/relay": False}
    if stage == "implement":
        expected["/cache"] = True  # W9: shared dependency cache (implement and checks only)
    if agent == "claude":
        expected.update({"/opt/harness/claude": False, "/harness/creds": False, "/harness/state": True})
    elif agent == "codex":  # a lone binary, or the standalone release directory (bin/, codex-path/, codex-resources/)
        release = "/opt/harness/codex" if "/opt/harness/codex" in mounts else "/opt/harness/codex/bin/codex"
        expected.update({release: False, "/harness/creds": False, "/harness/state": True})
    # W8: read-only git metadata for VCS-aware tools (present when the workspace is a worktree).
    expected.update({d: False for d in mounts if d.startswith("/mucli-git/")})
    # The host's job code, read-only over the image's copy (build.code_overlay_mounts).
    from mu.jobs.sandbox.build import code_overlay_mounts
    expected.update({dst: False for _src, dst in code_overlay_mounts()})
    assert mounts == expected
    env = "\n".join(config.get("Env") or [])
    assert SECRET not in env and "ANTHROPIC_API_KEY" not in env and "OPENAI_API_KEY" not in env
    assert "MUCLI_JOB_STAGE=" + stage in env
    module = "mu.jobs.sandbox.worker" if agent == "mucli" else "mu.jobs.sandbox.harness_worker"
    assert config["Cmd"][-1] == module


def _check_probe(probe, agent, stage, secret_env=None):
    failures = [c for c in check_probe(probe, agent, stage, secret_env=secret_env) if not c.ok]
    assert not failures, failures


@pytest.mark.parametrize("agent", ["mucli", "claude", "codex"])
def test_stage_sandbox_permissions_for_every_harness(docker, tmp_path, monkeypatch, agent):
    service, job = _service(tmp_path, monkeypatch, agent)
    for stage in ("plan", "implement"):
        if stage == "implement":
            service.transition(job.id, "preparing")
            service.transition(job.id, "running")
        runner = InspectingRunner()
        kwargs = {"runner": runner}
        if agent == "mucli":
            kwargs["relay_factory"] = lambda *a, stage=stage, **k: ModelRelay(*a, **k, provider_factory=lambda *_: MucliProbe(stage))
        result = run_stage_in_container(service, service.get(job.id), stage, "Probe the sandbox.", **kwargs)
        assert result["kind"] == "completed", result
        (info,) = runner.inspected.values()
        _check_container(info, agent, stage)
        if agent == "mucli" and stage == "plan":
            continue  # mucli plan stages have no shell tool; the container checks above cover them
        probe = _probe_from(service, job, stage, result)
        assert probe, f"no probe report from {agent} {stage}: {result}"
        _check_probe(probe, agent, stage)
