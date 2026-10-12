"""Regressions for the silently-stalled PLANNING job (SCRUM-196 / rsq-cloud).

(A) An unattended stage hit a tool-approval gate; InteractionRequired is a
    BaseException, escaped the planner handler and the worker exited 4 without
    recording anything because no attempt existed yet.
(B) Jira import ignored the top-level ``session_name`` so the job had no
    workspace and the worker ran in the GUI process's own repository.
"""

from __future__ import annotations

import pytest

from mu.jobs import AttentionReason, JobService, JobSpec, JobStatus, JobStore
from mu.jobs import stages
from mu.jobs import worker as worker_mod
from mu.ui.exceptions import InteractionRequired


def _service(tmp_path):
    return JobService(JobStore(str(tmp_path / "jobs.sqlite3")))


def test_stage_sessions_cannot_use_bash():
    assert "bash" not in stages.STAGE_TOOL_ALLOWLIST
    assert "read_file" in stages.STAGE_TOOL_ALLOWLIST or "search_for_string" in stages.STAGE_TOOL_ALLOWLIST


def test_stage_interaction_error_is_ordinary_exception():
    assert issubclass(stages.StageInteractionError, Exception)


def _queued_job(service, tmp_path, *, repository):
    job = service.create(JobSpec(
        title="T",
        repository=repository,
        execution={"provider": "p", "model": "m", "session_type": "workspace", "pipeline": "staged"},
    ))
    assert service.acquire(job.id, "w", ttl_seconds=600)
    return job


def test_crash_before_attempt_parks_job_instead_of_stalling(tmp_path, monkeypatch):
    service = _service(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    job = _queued_job(service, tmp_path, repository=str(ws))

    class _Manager:
        def __init__(self, *_a, **_k):
            pass

        def prepare(self, _job):
            return None

    def _gate(*_a, **_k):
        raise InteractionRequired("approval_required", "Approval required for bash")

    monkeypatch.setattr(worker_mod, "get_default_job_service", lambda: service)
    monkeypatch.setattr(worker_mod, "JobWorktreeManager", _Manager)
    monkeypatch.setattr(worker_mod, "_maybe_run_plan_stage", _gate)
    monkeypatch.setattr(worker_mod, "_refresh_receipt", lambda *_a, **_k: None)

    rc = worker_mod.run_job(job.id, "w")
    current = service.get(job.id)
    assert rc == 4
    assert current.status == JobStatus.NEEDS_HUMAN
    assert current.attention_reason == AttentionReason.PROVIDER_ERROR
    assert "InteractionRequired" in current.attention_detail


def test_empty_workspace_fails_loudly(tmp_path, monkeypatch):
    service = _service(tmp_path)
    job = _queued_job(service, tmp_path, repository="")
    monkeypatch.setattr(worker_mod, "get_default_job_service", lambda: service)
    monkeypatch.setattr(worker_mod, "_refresh_receipt", lambda *_a, **_k: None)

    rc = worker_mod.run_job(job.id, "w")
    assert rc == 3
    assert service.get(job.id).status == JobStatus.ENVIRONMENT_ERROR


def test_jira_import_requires_workspace_and_uses_top_level_session(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from mu.gui.routers import jobs as jobs_router
    from mu.integrations import jira

    service = _service(tmp_path)
    ws = tmp_path / "rsq"
    ws.mkdir()
    created = []

    def _import(svc, client, *, keys=(), jql="", defaults=None, max_results=20):
        created.append(dict(defaults or {}))
        return {"created": [], "skipped": []}

    monkeypatch.setattr(jira, "import_issues", _import)
    monkeypatch.setattr(jobs_router, "_jira_client", lambda: object())

    session = SimpleNamespace(
        folder_context=SimpleNamespace(folders=[str(ws)]),
        provider=SimpleNamespace(name="anthropic", model_name="m"),
        variables={"session_type": "workspace"},
    )
    app = FastAPI()
    app.include_router(jobs_router.router, prefix="/api/jobs")
    app.state.job_service = service
    app.state.session_by_name = lambda name: session if name == "rsq-cloud" else None

    with TestClient(app) as client:
        ok = client.post("/api/jobs/jira/import", json={"keys": ["SCRUM-196"], "session_name": "rsq-cloud"})
        assert ok.status_code == 200, ok.text
        assert created[-1]["workspace_root"] == str(ws)

        missing = client.post("/api/jobs/jira/import", json={"keys": ["SCRUM-196"]})
        assert missing.status_code == 400
        assert "workspace" in missing.json()["detail"].lower()


@pytest.mark.parametrize("needle", ["editor?.isDirty()", "signatures.get(part) === signature", "refreshParts(change.parts)"])
def test_work_page_background_refresh_preserves_drilldown(needle):
    from pathlib import Path

    js = (Path(__file__).resolve().parents[1] / "mu/gui/static/js/work/view.js").read_text(encoding="utf-8")
    assert needle in js
