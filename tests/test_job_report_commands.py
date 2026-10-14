import pytest

from mu.jobs.core.artifacts import artifacts_for
from mu.jobs.core.commands import CommandError, CommandRejected, dispatch
from mu.jobs.core.review_report import ReviewReport
from mu.jobs.stages import apply_review_verdict
from mu.jobs.checks import ChecksStage
from test_job_worker import running_job


def report_job(tmp_path, *, passed=True):
    service, _, job, _, _ = running_job(tmp_path)
    service.store.update_runtime_fields(job.id, validation_json=["pytest -q"])
    job = service.get(job.id)
    service.transition(job.id, "verifying")
    ChecksStage(service, run_suite=lambda *a: {"passed": passed, "return_code": 0 if passed else 1}, settings={}).verify(job)
    service.transition(job.id, "reviewing")
    apply_review_verdict(service, job.id, {"summary": "Report", "findings": [
        {"id": "F-1", "severity": "blocker", "title": "Fix widget", "disposition": "dismissed"},
        {"id": "F-2", "severity": "minor", "title": "Improve label"}]})
    return service, service.get(job.id)


@pytest.mark.parametrize("severity,coverage,checks,expected", [
    ("blocker", "met", 0, "do_not_merge"), ("major", "met", 0, "needs_work"),
    ("minor", "met", 0, "ready_with_nits"), ("nit", "met", 0, "ready_with_nits"),
    (None, "met", 0, "ready"), (None, "unmet", 0, "needs_work"), (None, "met", 1, "do_not_merge"),
])
def test_host_verdict_rules(severity, coverage, checks, expected):
    report = ReviewReport.from_dict({"verdict": "ready", "findings": [] if severity is None else [{"severity": severity}],
        "plan_coverage": [{"status": coverage}], "checks_context": {"new_failures": checks}})
    assert report.verdict == expected


def test_model_cannot_dismiss_findings_and_human_can_update_readiness(tmp_path):
    service, job = report_job(tmp_path)
    assert artifacts_for(job).read_json("review.json")["verdict"] == "do_not_merge"
    result = dispatch(service, {"type": "markFindings", "job_id": job.id, "ids": ["F-1"], "disposition": "dismissed"})
    assert result.data["verdict"] == "ready_with_nits"
    assert service.store.core_state(job.id).state.value == "review_done"


def test_push_override_is_audited_but_cannot_override_failed_checks(tmp_path):
    service, job = report_job(tmp_path, passed=False)
    with pytest.raises(CommandRejected, match="successful check"):
        dispatch(service, {"type": "push", "job_id": job.id, "override_reason": "I accept this"})
    assert service.get(job.id).status.value == "needs_human"


def test_successful_checks_still_require_reason_to_override_blocker(tmp_path):
    service, job = report_job(tmp_path)
    with pytest.raises(CommandRejected, match="override_reason"):
        dispatch(service, {"type": "push", "job_id": job.id})
    dispatch(service, {"type": "push", "job_id": job.id, "override_reason": "Confirmed false positive"})
    assert service.get(job.id).status.value == "publishing"
    assert any(e.event_type == "push_override" for e in service.events(job.id))


def test_recheck_runs_checks_without_implementation(tmp_path):
    service, job = report_job(tmp_path)
    dispatch(service, {"type": "recheck", "job_id": job.id})
    assert service.get(job.id).status.value == "verifying"
    assert service.store.core_state(job.id).state.value == "checking"


def test_unknown_command_and_invalid_id_arrays_are_bad_requests(tmp_path):
    service, job = report_job(tmp_path)
    with pytest.raises(CommandError):
        dispatch(service, {"type": "unknown", "job_id": job.id})
    with pytest.raises(CommandError):
        dispatch(service, {"type": "reprompt", "job_id": job.id, "finding_ids": "F-1"})
