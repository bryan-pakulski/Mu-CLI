"""Real template job setup (documentation/plans/job_templates.md §4): a tiny Node repository, a
build environment whose job setup runs ``npm ci`` through the egress bridge, then a checks run
that executes vitest from the installed node_modules. Requires Docker, the locally built
``mucli-job-runtime`` image (it has node, npm and mucli's check worker) and access to
registry.npmjs.org; skipped otherwise, like the other Docker smoke tests."""
import json
import socket
import subprocess

import pytest

from mu.jobs import JobService, JobSpec, JobStore
from mu.jobs.worktree import JobWorktreeManager
from docker_helpers import require_runtime_image, require_network

IMAGE = "mucli-job-runtime:latest"


@pytest.fixture
def docker():
    require_runtime_image(IMAGE)
    require_network("registry.npmjs.org")


def test_real_setup_installs_node_modules_then_checks_run_vitest(docker, tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)
    git("init", "-b", "main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    (repo / "package.json").write_text(json.dumps({
        "name": "fixture", "version": "1.0.0", "type": "module", "private": True,
        "scripts": {"test": "vitest run"}, "devDependencies": {"vitest": "2.1.9"}}))
    (repo / "sum.js").write_text("export const sum = (a, b) => a + b;\n")
    (repo / "sum.test.js").write_text("import { expect, test } from 'vitest';\nimport { sum } from './sum.js';\n"
                                      "test('adds', () => { expect(sum(1, 2)).toBe(3); });\n")
    (repo / ".gitignore").write_text("node_modules/\n")
    git("add", ".")
    git("commit", "-m", "base")
    service = JobService(JobStore(str(tmp_path / "jobs.db")))
    job = service.create(JobSpec(title="Template setup smoke", repository=str(repo), validation_commands=["npm test"],
                                 execution={"provider": "openai", "model": "gpt", "pipeline": "staged"}))
    JobWorktreeManager(service, root=str(tmp_path / "worktrees")).prepare(job)
    job = service.get(job.id)
    state = {"name": "node-build", "source": "template", "ref": "node-build", "paths": [], "status": "ready",
             "job_image": IMAGE, "python": "python3", "egress_allow": ["registry.npmjs.org"],
             # The lockfile is generated first (the fixture keeps none in git), then installed with `npm ci`.
             "job_setup": ["npm install --package-lock-only --no-audit --no-fund", "npm ci --no-audit --no-fund"]}
    meta = dict(job.metadata or {})
    meta["build_environments"] = [state]
    service.store.update_runtime_fields(job.id, metadata_json=meta)

    from mu.jobs.sandbox.job_setup import run_job_setup, setup_files, setup_records
    run_job_setup(service, service.get(job.id), [state], settings={})
    record = setup_records(service.get(job.id))["node-build"]
    assert record["status"] == "ok", record
    worktree = service.get(job.id).worktree
    assert (tmp_path / "worktrees").exists() and subprocess.run(
        ["test", "-x", f"{worktree}/node_modules/.bin/vitest"]).returncode == 0
    # The generated lockfile is not ignored: listed, and ignored by the dirty-worktree rule.
    assert "package-lock.json" in setup_files(service.get(job.id))

    from mu.jobs.checks import ChecksStage
    stage = ChecksStage(service, settings={})
    monkeypatch.setattr("mu.jobs.sandbox.environments.ensure_environments", lambda service, job, **kw: [state])
    run = stage.verify(service.get(job.id))
    suite = run.summary["suites"][0]
    assert suite["status"] == "passed", (suite.get("stdout"), suite.get("stderr"))
    assert run.passed and run.summary["verified"] is True and run.summary["dirty_worktree"] is False
