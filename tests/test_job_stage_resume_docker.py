"""A stage stopped by its time limit resumes its own conversation (harness latency plan, 5.1).

Opt-in (``MUCLI_ENV_DOCKER_TESTS=1``): the worker code runs from the runtime image, so the image
must be built from this tree (``python3 -m mu.jobs.sandbox.build [--tag T]``; set
``MUCLI_JOB_RUNTIME_IMAGE=T`` to use a non-default tag)."""
import os
import subprocess
import threading

import pytest

from mu.jobs import JobService, JobSpec, JobStore
from mu.jobs.sandbox.model_relay import ModelRelay
from mu.jobs.worktree import JobWorktreeManager
from providers.base import StreamEvent

pytestmark = pytest.mark.skipif(os.environ.get("MUCLI_ENV_DOCKER_TESTS") != "1",
                                reason="set MUCLI_ENV_DOCKER_TESTS=1 (and build the runtime image from this tree)")


def test_time_limited_stage_resumes_its_conversation(tmp_path, monkeypatch):
    image = os.environ.get("MUCLI_JOB_RUNTIME_IMAGE", "mucli-job-runtime:latest")
    if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode:
        pytest.skip(f"{image} not built")
    import mu.jobs.sandbox.policy as policy
    monkeypatch.setattr(policy, "IMAGE", image)
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    from mu.jobs.job_settings import save_settings
    save_settings({"sandbox": {"stage_timeout_s": {"implement": 8}}})
    repo = tmp_path / "repo"
    repo.mkdir()
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True)
    git("init", "-b", "main")
    git("config", "user.name", "T")
    git("config", "user.email", "t@example.com")
    (repo / "readme.txt").write_text("x")
    git("add", ".")
    git("commit", "-m", "base")
    service = JobService(JobStore(str(tmp_path / "jobs.db")))
    job = service.create(JobSpec(title="resume", repository=str(repo),
                                 execution={"provider": "openai", "model": "m", "auto_approve_writes": True}))
    JobWorktreeManager(service, root=str(tmp_path / "worktrees")).prepare(job)
    seen, release = [], threading.Event()

    class Slow:
        def stream(self, messages, **kwargs):
            seen.append(" ".join(p.text or "" for m in messages for p in m.parts))
            if len(seen) == 1:
                release.wait(60)  # outlives the 8 s stage limit
            yield StreamEvent(kind="text_delta", text="done")
            yield StreamEvent(kind="usage", input_tokens=1, output_tokens=1, total_tokens=2)
            yield StreamEvent(kind="done")
    provider = Slow()
    relay = lambda *a, **k: ModelRelay(*a, **k, provider_factory=lambda *x: provider)
    from mu.jobs.sandbox.runner import StagePaused, run_stage_in_container
    service.transition(job.id, "preparing")
    service.transition(job.id, "running")
    with pytest.raises(StagePaused, match="time limit"):
        run_stage_in_container(service, service.get(job.id), "implement", "Write the UNIQUE-PROMPT-7 widget.",
                               relay_factory=relay)
    release.set()
    assert "implement" in service.get(job.id).metadata["stage_resume"]
    result = run_stage_in_container(service, service.get(job.id), "implement", "Write the UNIQUE-PROMPT-7 widget.",
                                    relay_factory=relay)
    assert result["kind"] == "completed", result
    resumed = seen[-1]
    assert "UNIQUE-PROMPT-7" in resumed and "Continue this stage from where you left off" in resumed
    assert "Previous stage transcript" not in resumed
    assert "implement" not in (service.get(job.id).metadata.get("stage_resume") or {})


def test_suites_of_one_environment_share_a_container(tmp_path, monkeypatch):
    """Harness latency plan 5.5: two suites, one container start, per-suite results and output."""
    image = os.environ.get("MUCLI_JOB_RUNTIME_IMAGE", "mucli-job-runtime:latest")
    if subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode:
        pytest.skip(f"{image} not built")
    import mu.jobs.sandbox.policy as policy
    monkeypatch.setattr(policy, "IMAGE", image)
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True)
    git("init", "-b", "main")
    git("config", "user.name", "T")
    git("config", "user.email", "t@example.com")
    (repo / "readme.txt").write_text("x")
    git("add", ".")
    git("commit", "-m", "base")
    service = JobService(JobStore(str(tmp_path / "jobs.db")))
    job = service.create(JobSpec(title="batch", repository=str(repo), execution={"provider": "openai", "model": "m"}))
    JobWorktreeManager(service, root=str(tmp_path / "worktrees")).prepare(job)
    from mu.jobs.checks import Suite
    from mu.jobs.sandbox.check_runner import run_suites_in_container
    created = []
    from mu.container import docker_cli
    original = docker_cli.CommandRunner.run

    def spy(self, args, **kwargs):
        if len(args) > 1 and args[1] == "create":
            created.append(args)
        return original(self, args, **kwargs)
    monkeypatch.setattr(docker_cli.CommandRunner, "run", spy)
    job = service.get(job.id)
    results = run_suites_in_container(job, job.worktree, [
        Suite("one", "python3 -c \"print('suite-one')\""), Suite("two", "python3 -c \"import sys; sys.exit(3)\"")],
        service=service)
    assert len(created) == 1
    assert results[0]["passed"] and "suite-one" in results[0]["stdout"]
    assert not results[1]["passed"] and results[1]["return_code"] == 3
    outputs = [e.payload for e in service.events(job.id) if e.event_type == "checks_output"]
    assert any(o["suite"] == "one" and "suite-one" in o["text"] for o in outputs)
