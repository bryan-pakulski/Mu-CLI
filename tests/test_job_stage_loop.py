"""Durable job v2 task 4: plan + review stages and the automated review loop."""

from __future__ import annotations

import json

from mu.jobs import JobService, JobStore
from mu.jobs.controller import JobController
from mu.jobs.models import AttentionReason, JobStatus
from mu.jobs.runner import SessionJobRunner
from mu.jobs import stages
from mu.jobs.worker import _maybe_run_plan_stage


def _job(tmp_path, **execution):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    # plan_approval off: these tests cover the automated loop; the human
    # plan-approval gate has its own tests in test_job_plan_gate.py.
    exe = {"provider": "ollama", "model": "small", "session_type": "workspace",
           "plan_approval": False, **execution}
    job = service.create_from_payload({"title": "Ticket", "workspace_root": str(ws), "execution": exe})
    return service, job


def _to(service, job_id, *statuses):
    for status in statuses:
        service.transition(job_id, status, reason="test")


class FakeGen:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, job, prompt, workspace, model):
        self.calls.append({"prompt": prompt, "workspace": workspace, "model": dict(model)})
        return self.responses.pop(0), {"cost_usd": 0.01}


PLAN = json.dumps({
    "summary": "Add the widget",
    "steps": ["edit widget.py", "add test"],
    "acceptance_criteria": ["widget renders"],
    "validation_commands": ["pytest -q"],
})


def test_new_jobs_default_to_staged_pipeline(tmp_path):
    _, job = _job(tmp_path)
    assert stages.is_staged(job)
    assert "max_review_rounds" not in job.execution


def test_plan_stage_uses_plan_model_and_stores_plan(tmp_path):
    service, job = _job(tmp_path, stages={"plan": {"provider": "anthropic", "model": "big"}})
    _to(service, job.id, JobStatus.PREPARING)
    gen = FakeGen(PLAN)
    assert _maybe_run_plan_stage(service, job.id, generate_fn=gen) is None
    current = service.get(job.id)
    assert current.status == JobStatus.PLANNING
    assert gen.calls[0]["model"]["model"] == "big"
    assert gen.calls[0]["model"]["provider"] == "anthropic"
    from mu.jobs.core.plan_ops import plan_store
    plan = plan_store(current).load()
    assert plan.summary == "Add the widget"
    assert plan.approach == ["edit widget.py", "add test"]
    event = next(e for e in service.events(job.id) if e.event_type == "plan_created")
    assert event.payload["model"] == {"provider": "anthropic", "model": "big"}
    assert "plan" not in current.metadata
    assert any(e.event_type == "plan_created" for e in service.events(job.id))


def test_plan_stage_runs_once(tmp_path):
    service, job = _job(tmp_path)
    _to(service, job.id, JobStatus.PREPARING)
    gen = FakeGen(PLAN)
    _maybe_run_plan_stage(service, job.id, generate_fn=gen)
    _to(service, job.id, JobStatus.RUNNING, JobStatus.QUEUED, JobStatus.PREPARING)
    assert _maybe_run_plan_stage(service, job.id, generate_fn=gen) is None
    assert len(gen.calls) == 1


def test_planner_failure_parks_job_on_human(tmp_path):
    service, job = _job(tmp_path)
    _to(service, job.id, JobStatus.PREPARING)

    def boom(*_a, **_k):
        raise RuntimeError("provider down")

    assert _maybe_run_plan_stage(service, job.id, generate_fn=boom) == 26
    assert service.get(job.id).status == JobStatus.NEEDS_HUMAN


def _reviewing(tmp_path, **execution):
    service, job = _job(tmp_path, **execution)
    _to(service, job.id, JobStatus.PREPARING, JobStatus.PLANNING, JobStatus.RUNNING,
        JobStatus.VERIFYING, JobStatus.REVIEWING)
    return service, job


def test_review_approve_stops_for_human_and_records_report(tmp_path):
    service, job = _reviewing(tmp_path, stages={"review": {"model": "big-reviewer"}})
    gen = FakeGen(json.dumps({"verdict": "approve", "summary": "looks good", "feedback": []}))
    assert stages.run_review_stage(service, job.id, generate_fn=gen) == 0
    current = service.get(job.id)
    assert current.status == JobStatus.NEEDS_HUMAN
    assert current.attention_reason == AttentionReason.REVIEW_DONE
    assert service.store.core_state(job.id).state.value == "review_done"
    assert gen.calls[0]["model"]["model"] == "big-reviewer"
    from mu.jobs.core.artifacts import artifacts_for
    rounds = [artifacts_for(current).read_json("logs/review.round1.json")]
    assert len(rounds) == 1 and rounds[0]["verdict"] == "ready" and rounds[0]["round"] == 1


def test_request_changes_waits_until_human_reprompts_selected_findings(tmp_path):
    service, job = _reviewing(tmp_path)
    gen = FakeGen(json.dumps({
        "verdict": "request_changes", "summary": "missing test",
        "feedback": ["add a regression test for widget"],
    }))
    assert stages.run_review_stage(service, job.id, generate_fn=gen) == 0
    current = service.get(job.id)
    assert current.status == JobStatus.NEEDS_HUMAN
    assert current.attention_reason == AttentionReason.REVIEW_DONE
    assert not any(e.event_type == "review_changes_requested" for e in service.events(job.id))
    from mu.jobs.core.commands import dispatch
    dispatch(service, {"type": "reprompt", "job_id": job.id, "finding_ids": ["F-1"]})
    current = service.get(job.id)
    assert current.status == JobStatus.QUEUED
    runner = SessionJobRunner(service, build_session_fn=None, base_args=None)
    prompt = runner._prompt(current)
    assert "add a regression test for widget" in prompt


def test_unparseable_review_is_a_blocking_report(tmp_path):
    service, job = _reviewing(tmp_path)
    gen = FakeGen("I am not JSON")
    assert stages.run_review_stage(service, job.id, generate_fn=gen) == 0
    assert service.get(job.id).attention_reason == AttentionReason.REVIEW_DONE
    from mu.jobs.core.artifacts import artifacts_for
    assert artifacts_for(job).read_json("review.json")["verdict"] == "do_not_merge"


def test_old_review_round_limit_never_starts_an_automatic_loop(tmp_path):
    service, job = _reviewing(tmp_path, max_review_rounds=2)
    changes = json.dumps({"verdict": "request_changes", "feedback": ["again"]})
    assert stages.run_review_stage(service, job.id, generate_fn=FakeGen(changes)) == 0
    # Invoking a reviewer again at the human gate does no work.
    gen = FakeGen(changes)
    assert stages.run_review_stage(service, job.id, generate_fn=gen) == 0
    assert not gen.calls
    current = service.get(job.id)
    assert current.status == JobStatus.NEEDS_HUMAN
    assert current.attention_reason == AttentionReason.REVIEW_DONE
    assert len(service.store.list_events_by_type(job.id, ("review_completed",))) == 1
    assert "review_rounds" not in current.metadata


def test_controller_dispatches_review_phase():
    assert JobController._phase_for_status(JobStatus.REVIEWING) == "review"
    assert JobController._phase_for_status(JobStatus.VERIFYING) == "verification"
    assert JobController._phase_for_status(JobStatus.QUEUED) == "implementation"
