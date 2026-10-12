"""Phase 1 exit tests: per-job artifact folder, stage handoff through files, backfill."""

from __future__ import annotations

import json
import os

import pytest

from mu.jobs import JobService, JobStore, stages
from mu.jobs.core.artifacts import JobArtifacts, artifacts_for, backfill
from mu.jobs.core.plan_doc import PlanDoc
from mu.jobs.models import JobStatus
from mu.jobs.worker import _maybe_run_plan_stage


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))


def _job(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create_from_payload({
        "title": "Ticket", "workspace_root": str(ws),
        "execution": {"provider": "ollama", "model": "small", "session_type": "workspace",
                      "plan_approval": False},
    })
    return service, job


PLAN = json.dumps({
    "summary": "Add the widget",
    "steps": ["edit widget.py", "add test"],
    "acceptance_criteria": ["widget renders"],
    "validation_commands": ["pytest -q"],
})


def test_artifact_folder_under_mucli_home(tmp_path):
    art = JobArtifacts("abc123").ensure()
    assert art.dir == os.path.join(str(tmp_path / "home"), "jobs", "abc123")
    for sub in ("plans", "checks", "logs"):
        assert os.path.isdir(os.path.join(art.dir, sub))


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "..", "x" * 200])
def test_unsafe_job_ids_rejected(bad):
    with pytest.raises(ValueError):
        JobArtifacts(bad)


def test_paths_cannot_escape_job_folder():
    art = JobArtifacts("j1").ensure()
    with pytest.raises(ValueError):
        art.path("..", "other", "x")


def test_json_roundtrip_and_costs():
    art = JobArtifacts("j2").ensure()
    art.write_json("review.json", {"a": 1})
    assert art.read_json("review.json") == {"a": 1}
    assert art.read_json("missing.json", fallback={}) == {}
    art.append_cost({"stage": "plan", "cost_usd": 0.5})
    art.append_cost({"stage": "plan", "cost_usd": 0.25})
    art.append_cost({"stage": "review", "cost_usd": 1.0})
    assert art.stage_costs() == {"plan": 0.75, "review": 1.0}


def test_plan_stage_writes_plan_md_and_metadata_pointers(tmp_path):
    service, job = _job(tmp_path)
    service.transition(job.id, JobStatus.PREPARING)

    def gen(job, prompt, workspace, model):
        return PLAN, {"cost_usd": 0.01}

    _maybe_run_plan_stage(service, job.id, generate_fn=gen)
    job = service.get(job.id)
    art = artifacts_for(job)
    text = art.read_text("plan.md")
    assert text and "Add the widget" in text
    doc = PlanDoc.parse(text)
    assert [c.text for c in doc.acceptance] == ["widget renders"]
    assert [c.cmd for c in doc.checks] == ["pytest -q"]
    pointers = job.metadata.get("artifacts") or {}
    assert pointers.get("plan") == "plan.md"
    # metadata stores pointers, not paths outside the folder
    assert all(not str(v).startswith("..") for v in pointers.values())


def test_review_artifact_written_in_report_schema(tmp_path):
    service, job = _job(tmp_path)
    stages._write_review_artifact(job, {
        "verdict": "request_changes", "summary": "needs a test",
        "feedback": ["add a unit test"], "head_sha": "deadbeef",
    }, 1)
    report = artifacts_for(job).read_json("review.json")
    assert report["version"] == 1
    assert report["reviewed_head"] == "deadbeef"
    assert report["findings"] and report["findings"][0]["disposition"] == "open"
    assert report["verdict"] in {"ready", "ready_with_nits", "needs_work", "do_not_merge"}


def test_backfill_from_legacy_metadata_is_non_destructive(tmp_path):
    service, job = _job(tmp_path)
    meta = {
        "plan": {"summary": "S", "steps": ["a"], "acceptance_criteria": ["ac one"],
                 "validation_commands": ["pytest -q"]},
        "review_rounds": [{"verdict": "approve", "summary": "fine", "feedback": []}],
        "jira": {"key": "PROJ-1", "title": "Ticket"},
    }
    job = service.store.update_runtime_fields(job.id, metadata_json=meta)
    art = backfill(job)
    assert art.read_json("ticket.json")["key"] == "PROJ-1"
    assert "ac one" in art.read_text("plan.md")
    assert art.read_json("review.json")["verdict"] == "ready"
    # existing files are never overwritten
    art.write_text("plan.md", "HUMAN EDIT\n")
    backfill(job)
    assert art.read_text("plan.md") == "HUMAN EDIT\n"
    assert service.get(job.id).metadata == meta


def test_artifacts_remove():
    art = JobArtifacts("j3").ensure()
    art.write_text("plan.md", "x")
    assert art.remove() is True
    assert not os.path.exists(art.dir)
    assert art.remove() is False
