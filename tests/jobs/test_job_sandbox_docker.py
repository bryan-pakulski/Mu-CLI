"""Real isolation smoke tests; require Docker and the locally built shared image."""
import subprocess

import pytest

from mu.jobs import JobService, JobSpec, JobStore
from mu.jobs.worktree import JobWorktreeManager
from mu.jobs.sandbox.model_relay import ModelRelay
from mu.jobs.sandbox.runner import run_stage_in_container
from providers.base import StreamEvent
from docker_helpers import require_runtime_image


@pytest.fixture
def docker():
    require_runtime_image()


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


def test_real_stage_reads_job_attachments_read_only(docker, tmp_path, monkeypatch):
    """Files attached at creation reach the stage agent at /artifacts/attachments, and only there."""
    import base64
    import json
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-b", "main"], ["config", "user.name", "T"], ["config", "user.email", "t@example.com"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    (repo / "readme.txt").write_text("a workspace")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "base"], check=True, capture_output=True)
    service = JobService(JobStore(str(tmp_path / "jobs.db")))
    job = service.create_from_payload({
        "title": "Use the spec", "workspace_root": str(repo),
        "execution": {"provider": "openai", "model": "gpt-5.6-sol"},
        "attachments": [{"name": "spec.txt", "content_base64": base64.b64encode(b"the magic number is 4417").decode()}]})
    JobWorktreeManager(service, root=str(tmp_path / "worktrees")).prepare(job)
    seen = []

    class Reader:
        def __init__(self):
            self.calls = 0

        def stream(self, messages, **kwargs):
            self.calls += 1
            seen.append(json.dumps(messages, default=str))
            if self.calls == 1:
                yield StreamEvent(kind="tool_call_complete", tool_name="read_file", tool_call_id="read-1",
                                  tool_args={"filename": "/artifacts/attachments/spec.txt"})
            else:
                yield StreamEvent(kind="text_delta", text='{"summary":"read it"}')
            yield StreamEvent(kind="usage", input_tokens=10, output_tokens=10, total_tokens=20)
            yield StreamEvent(kind="done")

    reader = Reader()
    result = run_stage_in_container(service, service.get(job.id), "plan", "Read the attached spec.",
                                    relay_factory=lambda *a, **k: ModelRelay(*a, **k, provider_factory=lambda *x: reader))
    assert result["kind"] == "completed", result
    assert reader.calls >= 2
    assert "the magic number is 4417" in seen[-1]


def test_real_stage_screenshots_its_own_dev_server_as_evidence(docker, tmp_path, monkeypatch):
    """In the sandbox, browser_snapshot reaches the dev server the agent started on the container's
    localhost, clicks through it on a phone-sized screen, and the screenshot becomes job evidence."""
    import json
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    for args in (["init", "-b", "main"], ["config", "user.name", "T"], ["config", "user.email", "t@example.com"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    (repo / "readme.txt").write_text("a workspace")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "base"], check=True, capture_output=True)
    service = JobService(JobStore(str(tmp_path / "jobs.db")))
    job = service.create(JobSpec(title="Build a page", repository=str(repo),
                                 execution={"provider": "openai", "model": "gpt-5.6-sol", "pipeline": "staged"}))
    JobWorktreeManager(service, root=str(tmp_path / "worktrees")).prepare(job)
    page = ("<!doctype html><title>Greeter</title><button id=go onclick=\"document.getElementById('out')"
            ".textContent='clicked'\">Go</button><p id=out>idle</p>")
    script = [
        ("write_file", {"filename": "/workspace/site/index.html", "content": page}),
        ("bash", {"command": "cd /workspace/site && (nohup python3 -m http.server 8765 >/tmp/server.log 2>&1 &) && sleep 1"}),
        ("browser_snapshot", {"url": "http://localhost:8765/index.html", "name": "greeter", "device": "mobile",
                              "actions": [{"click": "#go"}]}),
    ]
    seen = []

    class Builder:
        def __init__(self):
            self.calls = 0

        def stream(self, messages, **kwargs):
            seen.append(json.dumps(messages, default=str))
            if self.calls < len(script):
                tool, args = script[self.calls]
                yield StreamEvent(kind="tool_call_complete", tool_name=tool, tool_call_id=f"c{self.calls}", tool_args=args)
            else:
                yield StreamEvent(kind="text_delta", text="Built and checked the page.")
            self.calls += 1
            yield StreamEvent(kind="usage", input_tokens=10, output_tokens=10, total_tokens=20)
            yield StreamEvent(kind="done")

    builder = Builder()
    service.transition(job.id, "preparing")
    service.transition(job.id, "running")
    result = run_stage_in_container(service, service.get(job.id), "implement", "Build the greeter page.",
                                    relay_factory=lambda *a, **k: ModelRelay(*a, **k, provider_factory=lambda *x: builder))
    assert result["kind"] == "completed", result
    # The tool result the model saw: the click ran and the page text changed.
    snapshot_turn = seen[3]
    assert "actions_run" in snapshot_turn and "clicked" in snapshot_turn and "image/png" in snapshot_turn
    from mu.jobs.core.artifacts import artifacts_for
    from mu.jobs.evidence import manifest
    evidence = manifest(artifacts_for(service.get(job.id)))
    shots = [e for e in evidence if e["name"] == "greeter.png"]
    assert shots and shots[0]["kind"] == "image" and "Greeter" in shots[0]["caption"]


def test_real_stage_edits_an_extra_source_and_checks_run_in_it(docker, tmp_path, monkeypatch):
    """An extra source (mu/jobs/multi_source.py) is a writable folder of the implement stage at
    /sources/<name>; the edit lands in that source's own worktree, and a check can run there."""
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    repos = {}
    for name in ("app", "lib"):
        path = tmp_path / name
        path.mkdir()
        for args in (["init", "-b", "main"], ["config", "user.name", "T"], ["config", "user.email", "t@example.com"]):
            subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
        (path / f"{name}.txt").write_text(name)
        subprocess.run(["git", "-C", str(path), "add", "."], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(path), "commit", "-m", "base"], check=True, capture_output=True)
        repos[name] = path
    from mu.jobs import multi_source
    service = JobService(JobStore(str(tmp_path / "jobs.db")))
    job = service.create_from_payload({"title": "Two repos", "workspace_root": str(repos["app"]),
                                       "sources": [{"kind": "folder", "path": str(repos["lib"])}],
                                       "execution": {"provider": "openai", "model": "gpt-5.6-sol"}})
    manager = JobWorktreeManager(service, root=str(tmp_path / "worktrees"))
    manager.prepare(job)
    multi_source.prepare(service, manager, service.get(job.id))
    job = service.get(job.id)

    class Editor:
        def __init__(self):
            self.calls = 0

        def stream(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                yield StreamEvent(kind="tool_call_complete", tool_name="write_file", tool_call_id="w1",
                                  tool_args={"filename": "/sources/lib/added.txt", "content": "from the job"})
            else:
                yield StreamEvent(kind="text_delta", text="Edited lib.")
            yield StreamEvent(kind="usage", input_tokens=10, output_tokens=10, total_tokens=20)
            yield StreamEvent(kind="done")

    editor = Editor()
    service.transition(job.id, "preparing")
    service.transition(job.id, "running")
    result = run_stage_in_container(service, service.get(job.id), "implement", "Add a file to lib.",
                                    relay_factory=lambda *a, **k: ModelRelay(*a, **k, provider_factory=lambda *x: editor))
    assert result["kind"] == "completed", result
    lib_worktree = multi_source.sources(service.get(job.id))[0]["worktree"]
    from pathlib import Path
    assert (Path(lib_worktree) / "added.txt").read_text() == "from the job"
    assert not (repos["lib"] / "added.txt").exists()  # the source repository itself is untouched
    from mu.jobs.checks import Suite
    from mu.jobs.sandbox.check_runner import run_suite_in_container
    check = run_suite_in_container(service.get(job.id), service.get(job.id).worktree,
                                   Suite("lib", "cat added.txt", cwd="/sources/lib"), service=service)
    assert check["passed"] and "from the job" in check["stdout"], check
