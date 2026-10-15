"""A check whose command is not installed in the sandbox image is recorded unverified with a clear
message (jobs run unattended: the job continues to review) instead of crashing the stage or
sending the implementer into a repair loop."""
from dataclasses import replace

import pytest

from mu.jobs.checks import ChecksStage
from mu.jobs.models import JobStatus
from mu.jobs.sandbox.check_runner import _missing_executable
from test_job_worker import running_job

REAL_TRACEBACK = """command failed (1): /usr/bin/docker start -a mucli-job-e7d1b0d56698-checks-a8cbb800
Traceback (most recent call last):
  File "/opt/mucli/mu/jobs/sandbox/check_worker.py", line 40, in main
    child = subprocess.Popen(argv, cwd="/workspace", shell=False,
  File "/usr/local/lib/python3.12/subprocess.py", line 1955, in _execute_child
    raise child_exception_type(errno_num, err_msg, err_filename)
FileNotFoundError: [Errno 2] No such file or directory: 'cargo'"""


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))


def test_older_runtime_crash_is_recognised_as_a_missing_tool():
    assert _missing_executable(REAL_TRACEBACK) == "cargo"
    assert _missing_executable("FileNotFoundError: [Errno 2] No such file or directory: '/etc/x'") is None
    assert _missing_executable("some other docker failure") is None


def test_missing_tool_continues_unverified_without_a_fix_loop_or_baseline_rerun(tmp_path):
    from mu.jobs.verify_worker import apply_checks_result
    service, _, job, _, _ = running_job(tmp_path)
    job = replace(job, validation_commands=["cargo test --workspace"])
    calls = []

    def run_suite(job, workspace, suite):
        calls.append(workspace)
        return {"passed": False, "return_code": 127, "stdout": "", "missing_executable": "cargo",
                "stderr": "cargo: not installed in the job sandbox image mucli-job-runtime:latest"}

    run = ChecksStage(service, run_suite=run_suite, settings={}).verify(job)
    assert len(calls) == 1  # no baseline re-run on the base commit
    assert run.summary["missing_tools"] == ["cargo"] and run.summary["new_failures"] == 0
    service.transition(job.id, JobStatus.VERIFYING, reason="test")
    assert apply_checks_result(service, job.id, run) == 0
    reviewed = service.get(job.id)
    assert reviewed.status == JobStatus.REVIEWING  # no park: review next, verdict capped there
    note = [e for e in service.events(job.id) if e.event_type == "checks_unverified"][-1]
    assert "Checks need `cargo`" in note.reason and "Settings → Sandbox" in note.reason
    assert note.payload["verified"] is False and note.payload["missing_tools"] == ["cargo"]
    assert service.store.core_state(job.id).fix_attempts == 0
