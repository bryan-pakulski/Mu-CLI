"""New job sources (session / Settings → Repositories / folder) and files attached at creation."""

from __future__ import annotations

import base64
import json
import os
import subprocess

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mu.gui.routers import jobs as jobs_router
from mu.jobs import JobService, JobStore, attachments, job_settings
from mu.jobs.core.artifacts import artifacts_for


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("MUCLI_HOME", str(home))
    import utils.config
    monkeypatch.setattr(utils.config, "HISTORY_DIR", str(home))
    return home


def _service(tmp_path):
    return JobService(JobStore(str(tmp_path / "jobs.sqlite3")))


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _repo(path, branch="main"):
    path.mkdir()
    for args in (["init", "-b", branch], ["config", "user.name", "T"], ["config", "user.email", "t@example.com"]):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)
    (path / "readme.txt").write_text("hi")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(path), "commit", "-m", "base"], check=True, capture_output=True)
    return path


def _session(home, name, *, folders, history=(), summary=""):
    directory = home / "sessions" / name
    directory.mkdir(parents=True)
    (directory / "session.json").write_text(json.dumps({
        "history": list(history), "conversation_summary": summary,
        "folder_context": {"folders": [str(f) for f in folders]},
        "provider_config": {"provider": "ollama", "model": "small"}, "variables": {}}))
    return directory


def _payload(ws, **extra):
    return {"title": "Ticket", "workspace_root": str(ws),
            "execution": {"provider": "ollama", "model": "small"}, **extra}


# ------------------------------------------------------------------ attachments

def test_uploaded_files_are_stored_listed_and_named_in_every_stage_prompt(tmp_path):
    from mu.jobs.runner import implement_prompt
    from mu.jobs.stages import build_plan_prompt, build_stage_review_prompt

    ws = tmp_path / "ws"
    ws.mkdir()
    service = _service(tmp_path)
    job = service.create_from_payload(_payload(ws, attachments=[
        {"name": "../spec.md", "content_base64": _b64("# Spec\nDo the thing")},
        {"name": "spec.md", "content_base64": _b64("second")},
    ]))
    names = [a["name"] for a in job.metadata["attachments"]]
    assert names == ["spec.md", "spec-2.md"]  # flattened, then de-duplicated
    assert job.metadata["attachments"][0]["size"] == len("# Spec\nDo the thing")
    stored = artifacts_for(job).path("attachments", "spec.md")
    assert open(stored).read() == "# Spec\nDo the thing"
    for prompt in (build_plan_prompt(job), implement_prompt(service, job),
                   build_stage_review_prompt(job, "", "", 1)):
        assert "/artifacts/attachments/spec.md" in prompt and "/artifacts/attachments/spec-2.md" in prompt

    stage_inputs = tmp_path / "input"
    stage_inputs.mkdir()
    assert attachments.copy_into(job, str(stage_inputs)) == 2
    assert (stage_inputs / "attachments" / "spec-2.md").read_text() == "second"


def test_jobs_without_attachments_mention_none():
    from mu.jobs.models import Job, JobStatus
    job = Job(id="x" * 12, title="t", description="", repository="", base_branch="main", base_sha="",
              acceptance_criteria=[], validation_commands=[], status=JobStatus.QUEUED)
    assert attachments.prompt_lines(job) == []


@pytest.mark.parametrize("items, message", [
    ([{"name": "a.txt", "content_base64": "not base64!!"}], "not valid base64"),
    ([{"name": "a.txt", "content_base64": ""}] * (attachments.MAX_FILES + 1), "at most"),
    ([{"content_base64": _b64("x")}], "name is required"),
    ([{"session": "nope", "attachment_id": "abc"}], "does not exist"),
    ([{"whatever": 1}], "needs content_base64"),
])
def test_bad_attachments_reject_creation_and_leave_nothing_behind(tmp_path, items, message):
    ws = tmp_path / "ws"
    ws.mkdir()
    service = _service(tmp_path)
    with pytest.raises(ValueError, match=message):
        service.create_from_payload(_payload(ws, attachments=items))
    assert service.list() == []


def test_oversized_attachment_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(attachments, "MAX_FILE_BYTES", 10)
    ws = tmp_path / "ws"
    ws.mkdir()
    with pytest.raises(ValueError, match="exceeds"):
        _service(tmp_path).create_from_payload(_payload(ws, attachments=[{"name": "big", "content_base64": _b64("x" * 11)}]))


def test_session_attachments_artifacts_and_conversation_can_be_attached(tmp_path, _home):
    from mu.artifact import ArtifactRegistry
    from mu.attachment import AttachmentRegistry

    ws = tmp_path / "ws"
    ws.mkdir()
    directory = _session(_home, "chat-1", folders=[ws], summary="Earlier we agreed on OAuth.", history=[
        {"role": "user", "content": "Please fix the login bug"},
        {"role": "assistant", "content": [{"type": "text", "text": "The callback drops the state param."}]},
        {"role": "tool", "content": "SECRET TOOL OUTPUT"},
    ])
    src = tmp_path / "log.txt"
    src.write_text("traceback here")
    upload = AttachmentRegistry(str(directory)).add(name="log.txt", source_path=str(src), mime_type="text/plain")
    chart = tmp_path / "report.csv"
    chart.write_text("a,b\n1,2\n")
    artifact = ArtifactRegistry(str(directory)).add(name="report.csv", source_path=str(chart), mime_type="text/csv")

    job = _service(tmp_path).create_from_payload(_payload(ws, attachments=[
        {"session": "chat-1", "attachment_id": upload["attachment_id"]},
        {"session": "chat-1", "artifact_id": artifact["artifact_id"]},
        {"session": "chat-1", "conversation": True},
    ]))
    listed = {a["name"]: a for a in job.metadata["attachments"]}
    assert set(listed) == {"log.txt", "report.csv", "conversation-chat-1.md"}
    assert listed["log.txt"]["origin"] == {"session": "chat-1", "attachment_id": upload["attachment_id"]}
    conversation = open(attachments.path_for(job, "conversation-chat-1.md")).read()
    assert "Please fix the login bug" in conversation and "drops the state param" in conversation
    assert "Earlier we agreed on OAuth." in conversation
    assert "SECRET TOOL OUTPUT" not in conversation
    assert open(attachments.path_for(job, "report.csv")).read() == "a,b\n1,2\n"
    assert attachments.path_for(job, "../jobs.sqlite3") is None


# ------------------------------------------------------------------ sources

def test_folder_source_accepts_a_plain_folder_and_records_the_source(tmp_path):
    folder = tmp_path / "notes"
    folder.mkdir()
    job = _service(tmp_path).create_from_payload({"title": "t", "source": {"kind": "folder", "path": str(folder)},
                                                  "execution": {"provider": "ollama", "model": "small"}})
    # A plain folder runs on a git snapshot of it (test_job_folder_snapshot.py).
    assert job.repository == job.metadata["folder_snapshot"]["snapshot_path"]
    assert job.metadata["source"] == {"kind": "folder", "path": str(folder)}
    with pytest.raises(ValueError, match="does not exist"):
        _service(tmp_path).create_from_payload({"title": "t", "source": {"kind": "folder", "path": str(tmp_path / "gone")}})


def test_repository_source_uses_the_settings_list(tmp_path):
    repo = _repo(tmp_path / "backend")
    subprocess.run(["git", "-C", str(repo), "branch", "release"], check=True, capture_output=True)
    job_settings.save_settings({"jobs": {"repositories": [{"name": "backend", "url": str(repo), "branch": "release"}]}})
    service = _service(tmp_path)
    job = service.create_from_payload({"title": "t", "source": {"kind": "repository", "name": "backend"},
                                       "execution": {"provider": "ollama", "model": "small"}})
    assert job.repository == str(repo) and job.base_branch == "release"
    assert job.metadata["source"] == {"kind": "repository", "name": "backend", "url": str(repo), "branch": "release"}
    with pytest.raises(ValueError, match="no repository named 'frontend'"):
        service.create_from_payload({"title": "t", "source": {"kind": "repository", "name": "frontend"}})
    with pytest.raises(ValueError, match="URL or path is required"):
        job_settings.save_settings({"jobs": {"repositories": [{"name": "broken", "url": ""}]}})


def test_repository_source_url_carries_the_branch_to_the_clone(tmp_path, monkeypatch):
    from mu.jobs import clone
    seen = []

    def fake_clone(url, **kwargs):
        seen.append(url)
        return clone.CloneResult(path=str(_repo(tmp_path / "clone")), url="https://example.com/o/r.git",
                                 branch="dev", base_sha="abc", created=True)
    monkeypatch.setattr(clone, "ensure_clone", fake_clone)
    job_settings.save_settings({"jobs": {"repositories": [
        {"name": "remote", "url": "https://example.com/o/r.git", "branch": "dev"}]}})
    job = _service(tmp_path).create_from_payload({"title": "t", "source": {"kind": "repository", "name": "remote"},
                                                  "execution": {"provider": "ollama", "model": "small"}})
    assert seen == ["https://example.com/o/r.git#dev"]
    assert job.base_branch == "dev" and job.metadata["repository_url"] == "https://example.com/o/r.git"


def test_session_source_runs_in_the_session_workspace_and_can_attach_the_conversation(tmp_path, _home):
    ws = tmp_path / "ws"
    ws.mkdir()
    _session(_home, "chat-2", folders=[ws], history=[{"role": "user", "content": "build the export"}])
    _session(_home, "bare", folders=[])
    service = _service(tmp_path)
    job = service.create_from_payload({"title": "t", "execution": {"provider": "ollama", "model": "small"},
                                       "source": {"kind": "session", "session": "chat-2", "include_conversation": True}})
    # ws is a plain folder, so the job runs on its git snapshot.
    assert job.metadata["folder_snapshot"]["source_path"] == str(ws.resolve())
    assert job.metadata["source"] == {"kind": "session", "session": "chat-2"}
    assert [a["name"] for a in job.metadata["attachments"]] == ["conversation-chat-2.md"]
    with pytest.raises(ValueError, match="has no workspace"):
        service.create_from_payload({"title": "t", "source": {"kind": "session", "session": "bare"}})
    with pytest.raises(ValueError, match="not both"):
        service.create_from_payload({"title": "t", "workspace_root": str(ws), "source": {"kind": "folder", "path": str(ws)}})


# ------------------------------------------------------------------ HTTP

def test_http_create_with_source_and_attachment_then_download(tmp_path, _home):
    ws = tmp_path / "ws"
    ws.mkdir()
    _session(_home, "chat-3", folders=[ws])
    app = FastAPI()
    app.include_router(jobs_router.router, prefix="/api/jobs")
    app.state.job_service = _service(tmp_path)
    with TestClient(app) as client:
        resp = client.post("/api/jobs", json={
            "title": "From a session", "source": {"kind": "session", "session": "chat-3"},
            "attachments": [{"name": "notes.txt", "content_base64": _b64("remember the edge case")}]})
        assert resp.status_code == 200, resp.text
        job = resp.json()["job"]
        # The session supplies the workspace and its model, and the job records where it came from.
        assert job["metadata"]["folder_snapshot"]["source_path"] == str(ws.resolve())
        assert job["metadata"]["origin_session"] == "chat-3"
        assert job["execution"]["stages"]["plan"] == {"provider": "ollama", "model": "small"}
        download = client.get(f"/api/jobs/{job['id']}/attachments/notes.txt")
        assert download.status_code == 200 and download.text == "remember the edge case"
        assert download.headers["content-disposition"].startswith("attachment")
        assert client.get(f"/api/jobs/{job['id']}/attachments/other.txt").status_code == 404
        bad = client.post("/api/jobs", json={"title": "x", "source": {"kind": "repository", "name": "missing"}})
        assert bad.status_code == 400 and "Settings" in bad.json()["detail"]
