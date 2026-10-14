"""Job settings stage models, MODELS_UNSET stop, plan approval gate, Jira context."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mu.jobs import JobService, JobStore
from mu.jobs import job_settings
from mu.jobs.models import AttentionReason, JobStatus, resolve_stage_model
from mu.jobs.review import JobReviewError, JobReviewService
from mu.jobs.worker import _maybe_run_plan_stage

PLAN = json.dumps({
    "summary": "Fix the timezone picker",
    "steps": ["edit picker", "add test"],
    "acceptance_criteria": ["timezone selection persists"],
    "validation_commands": ["pytest tests/test_tz.py -q"],
})


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))


def _job(tmp_path, **execution):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create_from_payload({
        "title": "SCRUM-1: Ticket", "workspace_root": str(ws),
        "execution": {"session_type": "workspace", **execution},
    })
    service.transition(job.id, JobStatus.PREPARING, reason="test")
    return service, job


def _gen(*_a, **_k):
    return PLAN, {"cost_usd": 0.0}


def _set_all(provider="anthropic", model="big", **extra):
    job_settings.save_settings({
        "stage_models": {s: {"provider": provider, "model": model} for s in ("plan", "implement", "review")},
        **extra,
    })


def test_settings_roundtrip_and_validation():
    assert job_settings.public_settings()["missing"] == ["plan", "implement", "review"]
    job_settings.save_settings({"stage_models": {"plan": {"provider": "anthropic", "model": "opus"}}})
    pub = job_settings.public_settings()
    assert pub["stage_models"]["plan"] == {"provider": "anthropic", "model": "opus"}
    assert pub["missing"] == ["implement", "review"] and pub["configured"] is False
    with pytest.raises(ValueError):
        job_settings.save_settings({"stage_models": {"deploy": {"provider": "x", "model": "y"}}})
    with pytest.raises(ValueError):
        job_settings.save_settings({"stage_models": {"plan": {"model": "no-provider"}}})


def test_resolution_falls_back_to_settings_but_job_wins():
    _set_all(model="settings-model")
    assert resolve_stage_model({}, "plan")["source"] == "settings"
    assert resolve_stage_model({}, "plan")["model"] == "settings-model"
    job = resolve_stage_model({"provider": "openai", "model": "job-model"}, "plan")
    assert (job["model"], job["source"]) == ("job-model", "job")
    stage = resolve_stage_model({"stages": {"plan": {"provider": "ollama", "model": "s"}}}, "plan")
    assert stage["source"] == "stage"


def test_unset_models_stop_job_with_prompt(tmp_path):
    service, job = _job(tmp_path)
    assert _maybe_run_plan_stage(service, job.id, generate_fn=_gen) == 27
    parked = service.get(job.id)
    assert parked.status == JobStatus.NEEDS_HUMAN
    assert parked.attention_reason == AttentionReason.MODELS_UNSET
    assert "Stage models" in parked.attention_detail


def test_plan_gate_parks_then_approve_with_edits_requeues(tmp_path):
    _set_all()
    service, job = _job(tmp_path)
    assert _maybe_run_plan_stage(service, job.id, generate_fn=_gen) == 0
    parked = service.get(job.id)
    assert parked.attention_reason == AttentionReason.PLAN_REVIEW
    assert parked.acceptance_criteria == ["timezone selection persists"]
    from mu.jobs.core.plan_ops import plan_store
    assert plan_store(parked).load().summary == "Fix the timezone picker"
    assert "plan" not in parked.metadata

    review = JobReviewService(service)
    with pytest.raises(JobReviewError):
        review.respond(job.id, decision="maybe")
    with pytest.raises(JobReviewError):
        review.respond(job.id, decision="approve", value={"validation_commands": ["rm -rf / && true"]})
    approved = review.respond(job.id, decision="approve", value={
        "acceptance_criteria": ["timezone selection persists", "time pickers available"],
        "validation_commands": ["pytest tests/test_tz.py -q"],
    })
    assert approved.status == JobStatus.QUEUED
    assert approved.acceptance_criteria == ["timezone selection persists", "time pickers available"]
    assert any(e.event_type == "plan_approved" for e in service.events(job.id))


def test_replan_clears_plan_and_feeds_back(tmp_path):
    _set_all()
    service, job = _job(tmp_path)
    _maybe_run_plan_stage(service, job.id, generate_fn=_gen)
    out = JobReviewService(service).respond(job.id, decision="replan", detail="Cover the time pickers too")
    assert out.status == JobStatus.QUEUED
    meta = out.metadata or {}
    assert "plan" not in meta and meta.get("replan_feedback") == "Cover the time pickers too"
    from mu.jobs.stages import build_plan_prompt
    assert "Cover the time pickers too" in build_plan_prompt(out)


def test_plan_approval_can_be_disabled(tmp_path):
    _set_all(plan_approval=False)
    service, job = _job(tmp_path)
    assert _maybe_run_plan_stage(service, job.id, generate_fn=_gen) is None
    assert service.get(job.id).status == JobStatus.PLANNING


def test_settings_api():
    from mu.gui.routers import jobs as jobs_router

    app = FastAPI()
    app.include_router(jobs_router.router, prefix="/api/jobs")
    client = TestClient(app)
    assert client.get("/api/jobs/settings").json()["configured"] is False
    r = client.put("/api/jobs/settings", json={"stage_models": {
        s: {"provider": "anthropic", "model": "m"} for s in ("plan", "implement", "review")}, "plan_approval": True})
    assert r.status_code == 200 and r.json()["configured"] is True
    assert client.put("/api/jobs/settings", json={"stage_models": {"x": {}}}).status_code == 400


def test_jira_issue_pulls_full_context():
    from mu.integrations.jira import issue_to_payload

    issue = {"key": "SCRUM-196", "fields": {
        "summary": "Fix timezone picker",
        "description": "Timezone selection doesn't work.",
        "status": {"name": "To Do"},
        "comment": {"comments": [{"author": {"displayName": "Ann"}, "body": "Repro on Safari only"}]},
        "parent": {"key": "SCRUM-10", "fields": {"summary": "Alerts epic", "status": {"name": "In Progress"}}},
        "subtasks": [{"key": "SCRUM-197", "fields": {"summary": "Time picker", "status": {"name": "To Do"}}}],
        "components": [{"name": "alerts"}],
    }}
    payload = issue_to_payload(issue, base_url="https://x.atlassian.net")
    desc = payload["description"]
    for needle in ("Timezone selection doesn't work.", "Repro on Safari only", "SCRUM-10", "SCRUM-197", "alerts"):
        assert needle in desc
    assert payload["metadata"]["jira"]["description"].startswith("Timezone")


def test_gui_surfaces_present():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "mu" / "gui"
    html = (root / "templates" / "work.html").read_text(encoding="utf-8")
    js = (root / "static" / "js" / "work.js").read_text(encoding="utf-8")
    assert 'id="work-stage-models-form"' in html and "work_stage_models.js" in html
    editor = (root / "static/js/work/plan_editor.js").read_text()
    view = (root / "static/js/work/view.js").read_text()
    assert "/static/js/work/plan_editor.js" in html
    # planos flow: the editor saves; approve / re-plan live in the job action bar.
    assert "data-plan-save" in editor and "data-plan-approve" in view
    assert "data-plan-reject" in view and "data-plan-edit" in view
