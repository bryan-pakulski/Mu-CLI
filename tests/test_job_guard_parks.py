"""Host safety checks park a job for a human instead of failing it, and test fixtures are never
false positives (job_templates: unattended jobs; the user decides what a flagged file is)."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from mu.jobs import AttentionReason, JobStatus
from mu.jobs.runner import JobRunOutcome
from mu.jobs.sandbox.policy import SandboxGuardError
from mu.jobs.worker import _apply_outcome
from mu.security.diff_scan import scan_patch
from test_job_worker import git, running_job

AWS = "AKIAABCDEFGHIJKLMNOP"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("mu.jobs.core.plan_ops.approved_plan_text", lambda job: "")


def _patch(path, line):
    return f"diff --git a/{path} b/{path}\n--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,1 @@\n+{line}\n"


def test_fixture_paths_do_not_trip_the_pattern_scan_but_injected_credentials_always_do():
    assert scan_patch(_patch("tests/fixtures/aws.txt", f"key = '{AWS}'"), allow_fixtures=True) == []
    assert scan_patch(_patch("tests/fixtures/aws.txt", f"key = '{AWS}'"))  # off by default
    assert scan_patch(_patch("src/aws.txt", f"key = '{AWS}'"), allow_fixtures=True)  # outside fixtures
    found = scan_patch(_patch("tests/fixtures/x.txt", "token opaque-credential-0123456789"),
                       literals=["opaque-credential-0123456789"], allow_fixtures=True)
    assert [f.label for f in found] == ["injected credential"]


def test_a_blocked_implement_stage_parks(tmp_path, monkeypatch):
    from mu.jobs.sandbox import runner
    service, _, job, _, _ = running_job(tmp_path)

    def blocked(*a, **k):
        raise SandboxGuardError("secret path must be removed from the job worktree before sandboxing: deploy.key")
    monkeypatch.setattr(runner, "run_stage_in_container", blocked)
    monkeypatch.setattr(runner, "implement_prompt", lambda service, job: "prompt")
    outcome = runner.SandboxJobRunner(service).run(job, None)
    assert outcome.kind == "needs_human" and outcome.attention_reason == AttentionReason.UNSAFE_ACTION
    assert "deploy.key" in outcome.attention_detail and "resume" in outcome.attention_detail
    events = [e for e in service.events(job.id) if e.event_type == "guard_blocked"]
    assert events and events[-1].payload["stage"] == "implement"


def test_a_secret_file_at_checkpoint_parks_instead_of_failing(tmp_path):
    from mu.jobs.worktree import JobWorktreeManager
    service, manager, job, attempt, _ = running_job(tmp_path)
    (Path(job.worktree) / "code.txt").write_text("implemented\n")
    (Path(job.worktree) / "deploy.key").write_text("not for the repo\n")
    code = _apply_outcome(service, manager, job.id, attempt.id, attempt.number,
                          JobRunOutcome(kind="completed", status="completed", cost_usd=0.1), worker_id="worker")
    fresh = service.get(job.id)
    assert code == 20 and fresh.status == JobStatus.NEEDS_HUMAN
    assert fresh.attention_reason == AttentionReason.UNSAFE_ACTION and "deploy.key" in fresh.attention_detail


def test_test_fixtures_pass_the_checkpoint_guard(tmp_path):
    service, manager, job, attempt, _ = running_job(tmp_path)
    fixtures = Path(job.worktree) / "tests" / "fixtures"
    fixtures.mkdir(parents=True)
    (fixtures / "server.key").write_text("-----BEGIN PRIVATE KEY-----\nMIIfake\n-----END PRIVATE KEY-----\n")
    (fixtures / "creds.py").write_text(f"AWS = '{AWS}'\n")
    code = _apply_outcome(service, manager, job.id, attempt.id, attempt.number,
                          JobRunOutcome(kind="completed", status="completed", cost_usd=0.1), worker_id="worker")
    assert code == 0 and service.get(job.id).status == JobStatus.VERIFYING


def test_the_next_implement_prompt_explains_the_block_until_a_clean_checkpoint(tmp_path):
    from mu.jobs.runner import implement_prompt
    service, _, job, _, _ = running_job(tmp_path)
    service.store.append_event(job.id, "guard_blocked", reason="x", payload={"stage": "checks", "error": "secret in deploy.key"})
    assert "secret in deploy.key" in implement_prompt(service, job)
    service.store.append_event(job.id, "verification_pending", payload={})
    assert "secret in deploy.key" not in implement_prompt(service, job)


def _sandboxed_checks(service, monkeypatch, effect):
    """A ChecksStage on the sandboxed path, with the container run replaced by ``effect``."""
    from mu.jobs import checks
    from mu.jobs.sandbox import check_runner

    def run_suites(job, workspace, suites, **kwargs):
        assert kwargs.get("guard") is False  # the stage guards once, after byproducts
        effect(Path(workspace))
        return [{"passed": True, "return_code": 0, "stdout": "", "stderr": ""} for _ in suites]
    monkeypatch.setattr(check_runner, "run_suites_in_container", run_suites)
    monkeypatch.setattr(check_runner, "run_suite_in_container",
                        lambda job, ws, suite, **kw: run_suites(job, ws, [suite], **kw)[0])
    monkeypatch.setattr(checks.ChecksStage, "_assign_environments", lambda self, job, suites: None)
    return checks.ChecksStage(service, settings={})


def test_a_leftover_a_check_run_deletes_never_blocks_but_a_real_secret_parks(tmp_path, monkeypatch):
    service, _, job, _, _ = running_job(tmp_path)
    job = replace(job, validation_commands=["pytest -q"])
    stage = _sandboxed_checks(service, monkeypatch, lambda ws: (ws / "core.pem").write_text("crash leftover"))
    run = stage.verify(job)  # the untracked leftover is discarded before the guard runs
    assert run.passed and not (Path(job.worktree) / "core.pem").exists()
    tracked = Path(job.worktree) / "README.md"
    tracked.write_text("base\n")
    git(job.worktree, "add", "README.md")
    git(job.worktree, "commit", "-qm", "readme")
    git(job.worktree, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "--allow-empty", "-qm", "noop")
    (Path(job.worktree) / "README.md").write_text(f"key {AWS}\n")
    git(job.worktree, "commit", "-qam", "leak")
    stage = _sandboxed_checks(service, monkeypatch, lambda ws: None)
    with pytest.raises(SandboxGuardError, match="secrets"):
        stage.verify(job)
