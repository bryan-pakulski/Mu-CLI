"""`/job delete <id> [--yes]` — TUI single-step cancel → archive → purge."""

from __future__ import annotations

import inspect

import pytest

from mu.commands import job as job_cmd
from mu.jobs import JobService, JobSpec, JobStatus, JobStore
from mu.jobs.management import JobManagementService


class _UI:
    def __init__(self, answer=True):
        self.answer = answer
        self.info = []
        self.errors = []
        self.prompts = []

    def show_info(self, text):
        self.info.append(text)

    def show_error(self, text):
        self.errors.append(text)

    def confirm(self, message, default=True):
        self.prompts.append(message)
        return self.answer


class _Session:
    def __init__(self, ui):
        self.ui = ui


@pytest.fixture
def service(tmp_path, monkeypatch):
    svc = JobService(JobStore(str(tmp_path / "jobs" / "jobs.sqlite3")))
    monkeypatch.setattr(job_cmd, "get_default_job_service", lambda: svc)
    monkeypatch.setattr(JobManagementService, "lease_poll_interval", 0.01)
    return svc


def _running_job(service):
    job = service.create(JobSpec(title="Live job", repository="/repo"))
    service.transition(job.id, JobStatus.PREPARING, reason="prepare")
    service.store.update_runtime_fields(job.id, branch=f"mu/job-{job.id[:8]}")
    return service.transition(job.id, JobStatus.RUNNING, reason="go")


def test_delete_with_yes_force_deletes_running_job_and_reports_branch(service):
    job = _running_job(service)
    ui = _UI()

    result = job_cmd.job_cmd(_Session(ui), f"delete {job.id} --yes", allow_prompt=True)

    assert result.ok is True
    assert result.data["deleted"] is True
    assert result.data["branch_preserved"] == job.branch
    assert "branch preserved" in result.message
    assert ui.prompts == []  # --yes skips the confirmation
    with pytest.raises(KeyError):
        service.get(job.id)


def test_delete_without_yes_and_no_prompt_refuses_without_deleting(service):
    job = _running_job(service)

    result = job_cmd.job_cmd(_Session(_UI()), f"delete {job.id}", allow_prompt=False)

    assert result.ok is False
    assert "--yes" in result.message
    assert service.get(job.id).status == JobStatus.RUNNING


def test_delete_interactive_confirm_declined_keeps_job(service):
    job = _running_job(service)
    ui = _UI(answer=False)

    result = job_cmd.job_cmd(_Session(ui), f"delete {job.id}", allow_prompt=True)

    assert result.ok is True
    assert result.data["deleted"] is False
    assert len(ui.prompts) == 1
    assert "branch is preserved" in ui.prompts[0].lower()
    assert service.get(job.id).status == JobStatus.RUNNING


def test_delete_missing_id_reports_usage(service):
    result = job_cmd.job_cmd(_Session(_UI()), "delete", allow_prompt=False)
    assert result.ok is False
    assert "Usage: /job delete" in result.message


def test_help_and_completer_advertise_delete():
    from pathlib import Path

    src = inspect.getsource(job_cmd)
    assert "delete <id> [--yes]" in src
    input_src = Path("mu/ui/input.py").read_text(encoding="utf-8")
    assert "_JOB_SUBCOMMAND_COMPLETER" in input_src
    assert '"/job": _JOB_SUBCOMMAND_COMPLETER' in input_src
    assert '"delete": None' in input_src
