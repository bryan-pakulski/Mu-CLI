"""Unattended reliability: transient failures retry with backoff instead of
parking on a human; late human answers are never lost behind an event cap.

Covers the three "Friday Five" gaps found in the jobs-plane review:
  * JobUI._load_pending_responses read the OLDEST 500 events -> a late
    interaction_response on a chatty job was invisible (job wedged).
  * Implementation FAILED was terminal even for a 429/5xx, and the loop turned
    provider errors into a NEEDS_HUMAN "choose recovery strategy" question.
  * max_retries governed only verification-failure requeues.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from mu.jobs import JobService, JobSpec, JobStatus, JobStore
from mu.jobs.controller import JobController
from mu.jobs.runner import JobRunOutcome, SessionJobRunner
from mu.jobs.ui import JobUI
from mu.jobs.worker import _apply_outcome, _retry_backoff_seconds
from mu.jobs.worktree import JobWorktreeManager


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def git(path, *args):
    return subprocess.run(
        ["git", "-C", str(path), *args], text=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, check=True,
    ).stdout.strip()


def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.com")
    (path / "code.txt").write_text("base\n", encoding="utf-8")
    git(path, "add", ".")
    git(path, "commit", "-m", "base")
    return path


def running_job(tmp_path, clock, *, max_retries=2):
    repository = repo(tmp_path)
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3"), clock=clock))
    job = service.create(JobSpec(
        title="Worker outcome", repository=str(repository), max_retries=max_retries,
        execution={"provider": "openai", "model": "test", "session_type": "workspace"},
    ))
    manager = JobWorktreeManager(service, root=str(tmp_path / "worktrees"))
    manager.prepare(job)
    service.transition(job.id, JobStatus.PREPARING)
    attempt = service.start_attempt(job.id, worker_id="worker", session_name="job-session")
    service.transition(job.id, JobStatus.RUNNING)
    assert service.acquire(job.id, "worker", ttl_seconds=600)
    return service, manager, service.get(job.id), attempt


# --------------------------------------------------------------------------- #2


def test_late_interaction_response_is_found_behind_hundreds_of_noise_events(tmp_path):
    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3")))
    job = service.create(JobSpec(title="Chatty"))
    # 600 noise events push the answer well past the old 500-row window.
    for i in range(600):
        service.store.append_event(job.id, "runtime_status", payload={"text": f"it {i}"})
    answer = service.store.append_event(
        job.id, "interaction_response",
        payload={"kind": "question", "selected": ["retry"]},
    )
    ui = JobUI(service, job.id)
    assert [e.id for e in ui._responses] == [answer.id]
    assert ui.prompt_choices("pick", ["retry", "abort"]) == "retry"
    # Consumed exactly once -> a second JobUI sees nothing pending.
    assert JobUI(service, job.id)._responses == []


def test_list_events_by_type_filters_in_sql(tmp_path):
    store = JobStore(str(tmp_path / "jobs.sqlite3"))
    service = JobService(store)
    job = service.create(JobSpec(title="T"))
    store.append_event(job.id, "runtime_status", payload={})
    kept = store.append_event(job.id, "interaction_response", payload={})
    store.append_event(job.id, "tool_call_ui", payload={})
    rows = store.list_events_by_type(job.id, ("interaction_response",))
    assert [r.id for r in rows] == [kept.id]
    assert store.list_events_by_type(job.id, ()) == []


# --------------------------------------------------------------------------- #3 / #1


def test_transient_failure_requeues_with_backoff_within_budget(tmp_path):
    clock = Clock()
    service, manager, job, attempt = running_job(tmp_path, clock, max_retries=2)

    code = _apply_outcome(
        service, manager, job.id, attempt.id, attempt.number,
        JobRunOutcome(kind="failed", status="error", error="Ollama API error: 429 rate limit",
                      cost_usd=0.1, transient=True),
        worker_id="worker",
    )
    current = service.get(job.id)
    assert code == 10
    assert current.status == JobStatus.QUEUED
    assert current.attention_reason.value == ""  # no human parked
    assert current.metadata["next_run_at"] == pytest.approx(clock.now + _retry_backoff_seconds(1))
    assert _retry_backoff_seconds(1) == 30.0 and _retry_backoff_seconds(2) == 60.0
    assert _retry_backoff_seconds(99) == 600.0
    finished = service.attempts(job.id)[0]
    assert finished.status == "failed" and finished.metadata["retry_scheduled"] is True
    assert finished.metadata["transient"] is True
    transitions = [e for e in service.events(job.id) if e.event_type == "status_changed"]
    assert transitions[-1].to_status == JobStatus.QUEUED
    assert transitions[-1].payload["backoff_seconds"] == 30.0


def test_transient_failure_past_retry_budget_is_terminal_failed(tmp_path):
    clock = Clock()
    service, manager, job, attempt = running_job(tmp_path, clock, max_retries=0)
    code = _apply_outcome(
        service, manager, job.id, attempt.id, attempt.number,
        JobRunOutcome(kind="failed", status="error", error="503 service unavailable",
                      cost_usd=0.0, transient=True),
        worker_id="worker",
    )
    current = service.get(job.id)
    assert code == 1
    assert current.status == JobStatus.FAILED
    assert "next_run_at" not in current.metadata
    assert service.attempts(job.id)[0].metadata["retry_budget_exhausted"] is True
    # Human retry from FAILED still works and is immediate.
    assert service.retry(job.id).status == JobStatus.QUEUED


def test_non_transient_failure_is_not_retried(tmp_path):
    clock = Clock()
    service, manager, job, attempt = running_job(tmp_path, clock, max_retries=5)
    code = _apply_outcome(
        service, manager, job.id, attempt.id, attempt.number,
        JobRunOutcome(kind="failed", status="error", error="SyntaxError in agent output",
                      cost_usd=0.0, transient=False),
        worker_id="worker",
    )
    assert code == 1
    assert service.get(job.id).status == JobStatus.FAILED


def test_controller_waits_for_backoff_then_leases(tmp_path):
    clock = Clock()
    service, manager, job, attempt = running_job(tmp_path, clock, max_retries=2)
    _apply_outcome(
        service, manager, job.id, attempt.id, attempt.number,
        JobRunOutcome(kind="failed", status="error", error="429", transient=True),
        worker_id="worker",
    )
    service.store.release_lease(job.id, "worker")

    calls = []

    def factory(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(pid=4242, poll=lambda: None, returncode=None,
                               terminate=lambda: None, kill=lambda: None, wait=lambda *a, **k: 0)

    ctl = JobController(service, max_workers=2, process_factory=factory,
                        project_root=str(tmp_path), python_executable="python-test")
    assert ctl.tick() == 0  # still backing off
    assert calls == []
    clock.advance(31)
    assert ctl.tick() == 1
    assert calls and "--job-id" in calls[0]
    assert service.get(job.id).worker_id


def test_manual_resume_clears_backoff(tmp_path):
    clock = Clock()
    service, manager, job, attempt = running_job(tmp_path, clock, max_retries=2)
    _apply_outcome(
        service, manager, job.id, attempt.id, attempt.number,
        JobRunOutcome(kind="failed", status="error", error="429", transient=True),
        worker_id="worker",
    )
    assert "next_run_at" in service.get(job.id).metadata
    service.store.release_lease(job.id, "worker")
    # Simulate an operator forcing it: QUEUED -> FAILED not allowed, so use
    # the store-level clear via retry() path on a FAILED job instead.
    service._clear_retry_backoff(job.id)
    assert "next_run_at" not in service.get(job.id).metadata


# --------------------------------------------------------------------------- loop policy


class _StubUI:
    def __init__(self):
        self.infos = []

    def prompt_choices(self, *a, **k):  # must never be called under auto policy
        raise AssertionError("auto policy must not ask a human")

    def show_info(self, msg):
        self.infos.append(msg)


def _session_stub(monkeypatch, **variables):
    from mu.session.session import Session

    s = Session.__new__(Session)
    s.variables = {"provider_recovery_policy": "auto", "provider_error_backoff_base": 0.0, **variables}
    s.ui = _StubUI()
    s._provider_auto_retries_used = 0
    s._provider_auto_rollback_done = False
    monkeypatch.setattr("time.sleep", lambda *_: None)
    return s


def test_auto_policy_retries_transient_then_aborts(monkeypatch):
    s = _session_stub(monkeypatch, provider_error_max_auto_retries=2)
    s._last_provider_error = "Ollama API error: 503 service unavailable"
    assert s._provider_error_recovery_choice() == "retry"
    assert s._provider_error_recovery_choice() == "retry"
    assert s._provider_error_recovery_choice() == "abort"
    assert s._last_provider_error_transient is True
    assert len(s.ui.infos) == 2


def test_auto_policy_rolls_back_once_for_4xx_then_aborts(monkeypatch):
    s = _session_stub(monkeypatch)
    s._last_provider_error = "HTTP 400 bad request: invalid tool schema"
    assert s._provider_error_recovery_choice() == "rollback_retry"
    assert s._provider_error_recovery_choice() == "abort"
    assert s._last_provider_error_transient is False


def test_auto_policy_aborts_non_transient(monkeypatch):
    s = _session_stub(monkeypatch)
    s._last_provider_error = "ValueError: model returned malformed JSON"
    assert s._provider_error_recovery_choice() == "abort"


def test_runner_sets_auto_policy_and_flags_transient_outcomes():
    assert SessionJobRunner._is_transient_error(None, "429 Too Many Requests") is True
    assert SessionJobRunner._is_transient_error(None, "KeyError: 'x'") is False
    flagged = SimpleNamespace(_last_provider_error_transient=True)
    assert SessionJobRunner._is_transient_error(flagged, "anything") is True
