"""Real isolation smoke tests; require Docker and the locally built shared image."""
import subprocess

import pytest

from mu.jobs import JobService, JobSpec, JobStore
from mu.jobs.worktree import JobWorktreeManager
from mu.jobs.sandbox.model_relay import ModelRelay
from mu.jobs.sandbox.runner import run_stage_in_container
from providers.base import StreamEvent


@pytest.fixture
def docker():
    try:
        result = subprocess.run(["docker", "image", "inspect", "mucli-job-runtime:latest"],
                                capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("Docker unavailable")
    if result.returncode:
        pytest.skip("Docker or mucli-job-runtime image unavailable")


def test_real_container_has_no_external_network(docker):
    code = "import socket; s=socket.socket(); s.settimeout(1); result=s.connect_ex(('1.1.1.1',443)); assert result != 0, result"
    result = subprocess.run(["docker", "run", "--rm", "--network", "none", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true", "--read-only", "--entrypoint", "python3",
        "mucli-job-runtime:latest", "-c", code], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_real_plan_stage_reaches_scoped_relay_without_provider_keys(docker, tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    git("init", "-b", "main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    (repo / "readme.txt").write_text("a workspace")
    git("add", ".")
    git("commit", "-m", "base")
    service = JobService(JobStore(str(tmp_path / "jobs.db")))
    job = service.create(JobSpec(title="Sandbox smoke", repository=str(repo),
        execution={"provider": "openai", "model": "gpt-5.6-sol", "pipeline": "staged"}))
    JobWorktreeManager(service, root=str(tmp_path / "worktrees")).prepare(job)
    calls = []
    class Provider:
        def stream(self, messages, **kwargs):
            calls.append(messages)
            yield StreamEvent(kind="text_delta", text='{"summary":"container relay works"}')
            yield StreamEvent(kind="usage", input_tokens=10, output_tokens=10, total_tokens=20)
            yield StreamEvent(kind="done")
    def relay(*args, **kwargs):
        return ModelRelay(*args, **kwargs, provider_factory=lambda *a: Provider())
    result = run_stage_in_container(service, service.get(job.id), "plan", "Return a JSON summary.", relay_factory=relay)
    assert result["kind"] == "completed", result
    assert "container relay works" in result["text"]
    assert calls
    from mu.jobs.checks import Suite
    from mu.jobs.sandbox.check_runner import run_suite_in_container
    check = run_suite_in_container(service.get(job.id), service.get(job.id).worktree,
                                   Suite("smoke", 'python3 -c "print(123)"'), service=service)
    assert check["passed"] and "123" in check["stdout"]
    assert any(e.event_type == "checks_output" and "123" in e.payload["text"] for e in service.events(job.id))

    class Implementer:
        def __init__(self):
            self.calls = 0

        def stream(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                yield StreamEvent(kind="tool_call_complete", tool_name="write_file", tool_call_id="write-1",
                                  tool_args={"filename":"/workspace/widget.txt", "content":"approved write"})
            else:
                yield StreamEvent(kind="text_delta", text="Implementation complete.")
            yield StreamEvent(kind="usage", input_tokens=10, output_tokens=10, total_tokens=20)
            yield StreamEvent(kind="done")

    def implement_relay(*args, **kwargs):
        return ModelRelay(*args, **kwargs, provider_factory=lambda *a: Implementer())

    from pathlib import Path
    service.transition(job.id, "preparing")
    service.transition(job.id, "running")
    # Jobs run unattended: the implement write runs inside the sandbox without parking.
    finished = run_stage_in_container(service, service.get(job.id), "implement", "Write widget.txt.", relay_factory=implement_relay)
    assert finished["kind"] == "completed", finished
    assert (Path(service.get(job.id).worktree) / "widget.txt").read_text() == "approved write"
    assert not (repo / "widget.txt").exists()  # only the job's worktree is writable
