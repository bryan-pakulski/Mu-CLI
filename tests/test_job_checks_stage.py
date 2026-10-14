from dataclasses import replace
from pathlib import Path

import pytest

from mu.jobs.checks import ChecksStage, parse_junit, push_check_reason, resolve_suites, triage
from mu.jobs.core.artifacts import artifacts_for
from test_job_worker import running_job, git


def test_junit_counts_cases_once_and_preserves_failure_identity():
    value = parse_junit('<testsuites tests="3"><testsuite tests="3"><testcase classname="C" name="a"/>'
                       '<testcase classname="C" name="b"><failure>broken</failure></testcase>'
                       '<testcase name="c"><skipped/></testcase></testsuite></testsuites>')
    assert (value["total"], value["passed"], value["failed"], value["skipped"]) == (3, 1, 1, 1)
    assert value["cases"][1]["id"] == "C::b"


def test_junit_rejects_entity_expansion():
    with pytest.raises(ValueError):
        parse_junit('<!DOCTYPE a [<!ENTITY a "b">]><testsuite/>')


def test_triage_does_not_confuse_different_failing_tests():
    current = {"junit": parse_junit('<testsuite><testcase name="old"><failure/></testcase><testcase name="new"><failure/></testcase></testsuite>')}
    baseline = {"junit": parse_junit('<testsuite><testcase name="old"><failure/></testcase></testsuite>')}
    assert triage(current, baseline) == (["::new"], ["::old"])
    assert triage({"passed": False, "return_code": 1}, {"passed": False, "return_code": 1})[0]


def test_configured_suite_overrides_planner_and_revalidates_commands(tmp_path):
    service, _, job, _, _ = running_job(tmp_path)
    job = replace(job, validation_commands=["pytest -q"])
    specs = resolve_suites(job, {"checks": {"suites": [{"name": "lint", "command": "ruff check ."}]}})
    assert [s.command for s in specs] == ["pytest -q", "ruff check ."]
    with pytest.raises(ValueError, match="shell operator"):
        resolve_suites(job, {"checks": {"suites": [{"name": "bad", "command": "pytest; curl example.com"}]}})


def test_checks_record_current_head_and_push_refuses_later_changes(tmp_path):
    service, _, job, _, _ = running_job(tmp_path)
    job = replace(job, validation_commands=["pytest -q"])
    stage = ChecksStage(service, run_suite=lambda *a: {"passed": True, "return_code": 0}, settings={})
    run = stage.verify(job)
    assert run.passed and run.head_sha == git(job.worktree, "rev-parse", "HEAD")
    assert push_check_reason(service, job) == ""
    summary = artifacts_for(job).read_json("checks/summary.json")
    assert summary["head_sha"] == run.head_sha and summary["passed"]
    (Path(job.worktree) / "code.txt").write_text("later change")
    assert "changed since checks" in push_check_reason(service, job)
    git(job.worktree, "add", "-A")
    git(job.worktree, "commit", "-m", "new head")
    assert "branch changed" in push_check_reason(service, job)


def test_preexisting_failure_does_not_count_as_regression_but_blocks_push(tmp_path):
    service, _, job, _, _ = running_job(tmp_path)
    job = replace(job, validation_commands=["pytest -q"])
    workspaces = []
    def suite(job, workspace, spec):
        workspaces.append(workspace)
        return {"passed": False, "return_code": 1, "stdout": "the same failing test",
                "junit": parse_junit('<testsuite><testcase name="old"><failure/></testcase></testsuite>')}
    run = ChecksStage(service, run_suite=suite, settings={}).verify(job)
    assert not run.passed and run.summary["new_failures"] == 0 and run.summary["preexisting_failures"] == 1
    assert len(workspaces) == 2 and workspaces[0] != workspaces[1]
    assert not Path(workspaces[1]).exists()
    assert "successful check" in push_check_reason(service, job)


def test_dirty_checks_can_never_pass(tmp_path):
    service, _, job, _, _ = running_job(tmp_path)
    job = replace(job, validation_commands=["pytest -q"])
    def suite(job, workspace, spec):
        (Path(workspace) / "code.txt").write_text("check mutated source")
        return {"passed": True, "return_code": 0}
    run = ChecksStage(service, run_suite=suite, settings={}).verify(job)
    assert not run.passed and run.summary["new_failures"] == 1


@pytest.mark.parametrize("new_failures,attempts,expected", [(1, 0, "queued"), (1, 2, "reviewing"), (0, 0, "reviewing")])
def test_fix_loop_is_bounded_and_excludes_preexisting_failures(tmp_path, new_failures, attempts, expected):
    from mu.jobs.verify_worker import apply_checks_result
    from mu.jobs.verification import VerificationRun
    service, _, job, _, _ = running_job(tmp_path)
    service.transition(job.id, "verifying")
    service.store.set_fix_attempts(job.id, attempts)
    run = VerificationRun(id="checks", job_id=job.id, status="failed", passed=False, started_at=1,
        finished_at=2, duration_ms=1000, base_sha=job.base_sha, head_sha=job.base_sha, branch=job.branch,
        worktree=job.worktree, summary={"new_failures": new_failures, "preexisting_failures": 1, "suites": []})
    apply_checks_result(service, job.id, run)
    assert service.get(job.id).status.value == expected
    assert service.store.core_state(job.id).fix_attempts == attempts + int(expected == "queued")
    assert Path(job.worktree).exists()


def test_publisher_refuses_missing_checks_without_touching_remote(tmp_path):
    from mu.jobs.publish import publish_job
    service, _, job, _, _ = running_job(tmp_path)
    service.store.update_runtime_fields(job.id, execution_json={**job.execution, "publish_mode": "gh"})
    for state in ("verifying", "reviewing", "publishing"):
        service.transition(job.id, state)
    calls = []
    assert publish_job(service, job.id, run=lambda *a: calls.append(a), env={}) != 0
    assert not calls
    assert "successful check" in service.get(job.id).attention_detail
