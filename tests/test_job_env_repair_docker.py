"""Environment repair against real Docker (requires Docker and the locally built
mucli-job-runtime image): a tiny Node repository whose check ``npx vitest run`` cannot run
without ``node_modules`` is repaired by a stub Claude Code harness that runs ``npm ci``; the checks
then run and pass in the repaired job-scoped image, which job cleanup removes."""
import json
import os
import subprocess
import textwrap
from pathlib import Path

import pytest

from mu.jobs import JobService, JobSpec, JobStore
from mu.jobs.worktree import JobWorktreeManager

# The stub harness: does what a repair agent would (project install + a system-level change the
# checks need), emits Claude stream-json, and ends with the REPAIR COMMANDS block.
FAKE_CLAUDE = textwrap.dedent('''\
    #!/usr/bin/env python3
    import json, os, subprocess, sys
    def out(obj):
        print(json.dumps(obj), flush=True)
    frame = json.loads(sys.stdin.readline())
    assert "RUNNABLE" in frame["message"]["content"], "not the repair prompt"
    assert os.getuid() == 0, "repair runs as root inside the container"
    out({"type": "system", "subtype": "init", "session_id": "repair-1", "model": "fake"})
    commands = ["npm ci --no-audit --no-fund --offline", "touch /opt/repair-marker"]
    for index, command in enumerate(commands):
        out({"type": "assistant", "message": {"id": f"m{index}", "model": "fake", "content": [{"type": "tool_use",
             "id": f"t{index}", "name": "Bash", "input": {"command": command}}]}})
        done = subprocess.run(command, shell=True, cwd="/workspace", capture_output=True, text=True)
        assert done.returncode == 0, done.stdout + done.stderr
        out({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"t{index}",
             "content": done.stdout[-2000:]}]}})
    text = "Installed.\\nREPAIR COMMANDS:\\n```\\n" + "\\n".join(commands) + "\\n```"
    out({"type": "assistant", "message": {"id": "end", "model": "fake", "content": [{"type": "text", "text": text}]}})
    out({"type": "result", "subtype": "success", "is_error": False, "session_id": "repair-1", "result": text})
    sys.stdin.read()
''')

# A stand-in for vitest, installed from the repository by `npm ci` (no registry needed). It only
# passes in the repaired image (the marker the repair wrote into the image's rootfs).
VITEST_BIN = textwrap.dedent('''\
    #!/usr/bin/env node
    const fs = require("fs");
    if (!fs.existsSync("/opt/repair-marker")) { console.error("not the repaired image"); process.exit(3); }
    console.log(" Test Files  1 passed (1)");
''')


@pytest.fixture
def docker():
    try:
        result = subprocess.run(["docker", "image", "inspect", "mucli-job-runtime:latest"], capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("Docker unavailable")
    if result.returncode:
        pytest.skip("Docker or mucli-job-runtime image unavailable")


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)


def _node_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "vendor" / "vitest").mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "package.json").write_text(json.dumps({"name": "tiny", "version": "1.0.0",
                                                   "devDependencies": {"vitest": "file:vendor/vitest"}}))
    (repo / "package-lock.json").write_text(json.dumps({
        "name": "tiny", "version": "1.0.0", "lockfileVersion": 3, "requires": True,
        "packages": {"": {"name": "tiny", "version": "1.0.0", "devDependencies": {"vitest": "file:vendor/vitest"}},
                     "node_modules/vitest": {"resolved": "vendor/vitest", "link": True},
                     "vendor/vitest": {"version": "0.0.1", "dev": True, "bin": {"vitest": "bin.js"}}}}))
    (repo / "vendor" / "vitest" / "package.json").write_text(json.dumps(
        {"name": "vitest", "version": "0.0.1", "bin": {"vitest": "bin.js"}}))
    (repo / "vendor" / "vitest" / "bin.js").write_text(VITEST_BIN)
    (repo / "vendor" / "vitest" / "bin.js").chmod(0o755)
    (repo / ".gitignore").write_text("node_modules/\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "tiny node repo")
    return repo


def test_repair_makes_npx_vitest_runnable_and_the_repaired_image_is_cleaned(docker, tmp_path, monkeypatch):
    from mu.jobs import job_settings
    from mu.jobs.checks import ChecksStage
    from mu.jobs.sandbox.environments import cleanup_job
    from mu.jobs.sandbox.repair import repair_and_recheck, repair_records
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-fake")
    fake = tmp_path / "bin" / "claude"
    fake.parent.mkdir()
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(0o755)
    # No egress for checks: `npx vitest` cannot fetch vitest from the registry, so only the repair helps.
    job_settings.save_settings({"sandbox": {"egress_extra": []},
                                "harness": {"claude": {"binary": str(fake), "auth": "api_key"}}})
    repo = _node_repo(tmp_path)
    service = JobService(JobStore(str(tmp_path / "jobs.db")))
    job = service.create(JobSpec(title="Repair smoke", repository=str(repo), validation_commands=["npx vitest run"],
                                 execution={"pipeline": "staged", "stage_agents": {"implement": "claude"}}))
    JobWorktreeManager(service, root=str(tmp_path / "worktrees")).prepare(job)
    job = service.get(job.id)
    image = ""
    try:
        first = ChecksStage(service).verify(job)
        assert first.summary["verified"] is False, first.summary  # could not run: no node_modules
        assert not (Path(job.worktree) / "node_modules").exists()
        run = repair_and_recheck(service, job.id, first, verify=lambda fresh: ChecksStage(service).verify(fresh))
        record = repair_records(service.get(job.id))[-1]
        image = record["image"]
        assert record["outcome"] == "repaired", record
        assert image == f"mucli/job-{job.id[:12]}-runtime-repair1"
        assert record["commands"] == ["npm ci --no-audit --no-fund --offline", "touch /opt/repair-marker"]
        assert record["project_commands"] == ["npm ci --no-audit --no-fund --offline"]
        # The re-run checks ran (trigger repair) and passed in the repaired image (the marker).
        assert run.summary["trigger"] == "repair" and run.summary["verified"] is True
        assert run.passed, run.summary
        assert "1 passed" in run.summary["suites"][0]["stdout"]
        assert service.get(job.id).metadata["runtime_repair_image"] == image
        labels = json.loads(subprocess.run(["docker", "image", "inspect", "--format", "{{json .Config.Labels}}", image],
                                           capture_output=True, text=True, check=True).stdout)
        assert labels["io.mucli.job"] == job.id and labels["io.mucli.role"] == "job-image"
        env = json.loads(subprocess.run(["docker", "image", "inspect", "--format", "{{json .Config.Env}}", image],
                                        capture_output=True, text=True, check=True).stdout)
        # No relay token or repair-only setting is baked into the image.
        assert not [e for e in env if e.startswith(("MUCLI_STAGE_TOKEN=", "MUCLI_MODEL_RELAY=", "IS_SANDBOX=",
                                                    "MUCLI_REPAIR_CHOWN=", "MUCLI_JOB_STAGE="))], env
        config = json.loads(subprocess.run(["docker", "image", "inspect", "--format", "{{json .Config}}", image],
                                           capture_output=True, text=True, check=True).stdout)
        base = json.loads(subprocess.run(["docker", "image", "inspect", "--format", "{{json .Config}}",
                                          "mucli-job-runtime:latest"], capture_output=True, text=True, check=True).stdout)
        # The repair wrapper (and the arguments carrying the stage environment) is not the image's command.
        assert config.get("Entrypoint") == base.get("Entrypoint") and (config.get("Cmd") or []) == (base.get("Cmd") or [])
        assert "MUCLI_STAGE_TOKEN" not in json.dumps(config)
        # The mounts were handed back to the host user; no tracked file changed.
        installed = Path(job.worktree) / "node_modules"
        assert installed.is_dir() and installed.lstat().st_uid == os.getuid()
        assert _git(job.worktree, "status", "--porcelain").stdout.strip() == ""
    finally:
        cleanup_job(job.id, service=service, reason="test done")
    if image:
        assert subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode != 0
    assert "runtime_repair_image" not in (service.get(job.id).metadata or {})
