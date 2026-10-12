"""Durable job v2 task 1: jobs are anchored to the session workspace."""

from __future__ import annotations

import pytest

from mu.jobs import JobService, JobSpec, JobStore


def _service(tmp_path):
    return JobService(JobStore(str(tmp_path / "jobs.sqlite3")))


EXECUTION = {"provider": "ollama", "model": "m", "session_type": "workspace"}


@pytest.mark.parametrize("key", ["repository", "repo"])
def test_legacy_repository_keys_rejected(tmp_path, key):
    with pytest.raises(ValueError, match="no longer accepted|current workspace"):
        _service(tmp_path).create_from_payload({
            "title": "T", key: str(tmp_path), "execution": dict(EXECUTION),
        })


def test_non_git_workspace_allowed_with_git_disabled(tmp_path):
    ws = tmp_path / "plain"
    ws.mkdir()
    job = _service(tmp_path).create_from_payload({
        "title": "Plain dir job", "workspace_root": str(ws), "execution": dict(EXECUTION),
    })
    assert job.repository == str(ws)
    assert job.execution.get("git_enabled") is False
    assert job.base_sha == ""


def test_missing_workspace_rejected(tmp_path):
    with pytest.raises(ValueError, match="Workspace does not exist"):
        _service(tmp_path).create_from_payload({
            "title": "T", "workspace_root": str(tmp_path / "nope"), "execution": dict(EXECUTION),
        })


def test_legacy_persisted_repository_job_still_loads(tmp_path):
    service = _service(tmp_path)
    created = service.create(JobSpec(title="Old", repository="/legacy/repo", execution=dict(EXECUTION)))
    loaded = JobService(JobStore(str(tmp_path / "jobs.sqlite3"))).get(created.id)
    assert loaded.repository == "/legacy/repo"


def test_tui_create_rejects_repo_flag_and_uses_session_workspace(tmp_path):
    from mu.commands import job as job_cmd

    with pytest.raises(ValueError, match="--repo is no longer supported"):
        job_cmd._parse_create("Title --repo /x", {"execution": {}})
    payload = job_cmd._parse_create("Fix bug", {"workspace_root": str(tmp_path), "execution": {}})
    assert payload["workspace_root"] == str(tmp_path)
    assert payload["title"] == "Fix bug"
    assert "repository" not in payload
