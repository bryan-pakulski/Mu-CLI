"""Pipeline-correctness regressions (documentation/job_mode_plan.md review round).

1. A RECOVERING job whose worktree prepare fails lands in ENVIRONMENT_ERROR;
   run_leased never silently swallows an illegal transition of a job it owns.
2. finish_attempt_owned fires tracker writeback and keeps started/completed_at.
7. Metadata merges are atomic (no lost concurrent updates).
8. Runtime deadlines only try statuses that can time out (logged once), and a
   retried (old-row) TIMED_OUT job gets a fresh runtime budget.
9. A cancel that races an attempt finish still records the attempt's cost.
"""

from __future__ import annotations

import pytest

from mu.jobs import AttentionReason, JobService, JobSpec, JobStatus, JobStore
from mu.jobs import worker as worker_mod
from mu.jobs.controller import JobController, WorkerHandle
from mu.jobs.lease import run_leased
from mu.jobs.service import JobStateError, LeaseLostError
from mu.jobs.worktree import WorktreeError

LEGACY = {"provider": "openai", "model": "test", "session_type": "workspace"}


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeProcess:
    pid = 4242
    returncode = None

    def __init__(self):
        self.signals = []

    def poll(self):
        return self.returncode

    def terminate(self):
        self.signals.append("TERM")

    def kill(self):
        self.signals.append("KILL")


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))


def make(tmp_path, clock=None):
    return JobService(JobStore(str(tmp_path / "jobs.sqlite3"), clock=clock or Clock()))


def recovering_job(service, tmp_path):
    job = service.create(JobSpec(title="Recover", repository=str(tmp_path), execution=LEGACY))
    for status in (JobStatus.PREPARING, JobStatus.RUNNING, JobStatus.RECOVERING):
        service.transition(job.id, status)
    assert service.acquire(job.id, "w", ttl_seconds=600)
    return job


# ------------------------------------------------------------------ 1. recovering + refused transitions


def test_recovering_job_with_failing_worktree_prepare_parks_in_environment_error(tmp_path, monkeypatch):
    service = make(tmp_path)
    job = recovering_job(service, tmp_path)

    class FailingManager:
        def __init__(self, *_a, **_k):
            pass

        def prepare(self, _job):
            raise WorktreeError("fatal: Needed a single revision", stage="base")

    monkeypatch.setattr(worker_mod, "get_default_job_service", lambda: service)
    monkeypatch.setattr(worker_mod, "JobWorktreeManager", FailingManager)
    monkeypatch.setattr(worker_mod, "_refresh_receipt", lambda *_a, **_k: None)

    assert worker_mod.run_job(job.id, "w") == 3
    current = service.get(job.id)
    assert current.status == JobStatus.ENVIRONMENT_ERROR  # not left RECOVERING to relaunch forever
    assert current.worker_id == ""


def test_run_leased_parks_an_owned_schedulable_job_on_illegal_transition(tmp_path):
    service = make(tmp_path)
    job = recovering_job(service, tmp_path)

    def body(fenced, job_, lease):
        raise JobStateError(f"cannot transition job {job_.id} from recovering to verifying")

    rc = run_leased(service, job.id, "w", phase="implementation", lease_ttl_seconds=600, body=body,
                    statuses=(JobStatus.RECOVERING,))
    assert rc == 1
    current = service.get(job.id)
    assert current.status == JobStatus.NEEDS_HUMAN
    assert current.attention_reason == AttentionReason.ENVIRONMENT_FAILURE
    assert "recovering to verifying" in current.attention_detail
    assert any(e.event_type == "worker_transition_refused" for e in service.events(job.id))
    assert current.worker_id == ""  # lease released


def test_run_leased_lease_loss_stays_quiet(tmp_path):
    service = make(tmp_path)
    job = recovering_job(service, tmp_path)

    def body(fenced, job_, lease):
        raise LeaseLostError("lost")

    assert run_leased(service, job.id, "w", phase="implementation", lease_ttl_seconds=600, body=body,
                      statuses=(JobStatus.RECOVERING,)) == 0
    assert service.get(job.id).status == JobStatus.RECOVERING
    assert not any(e.event_type == "worker_transition_refused" for e in service.events(job.id))


def test_run_leased_race_with_human_move_stays_quiet(tmp_path):
    service = make(tmp_path)
    job = recovering_job(service, tmp_path)

    def body(fenced, job_, lease):
        service.cancel(job_.id)  # a human cancelled while the stage ran
        raise JobStateError("cannot transition job from cancelled to verifying")

    assert run_leased(service, job.id, "w", phase="implementation", lease_ttl_seconds=600, body=body,
                      statuses=(JobStatus.RECOVERING,)) == 0
    assert service.get(job.id).status == JobStatus.CANCELLED


# ------------------------------------------------------------------ 2. + 9. finish_attempt_owned


def running_job(service):
    job = service.create(JobSpec(title="Attempt", execution=LEGACY))
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.RUNNING)
    assert service.acquire(job.id, "w", ttl_seconds=600)
    return job, service.start_attempt(job.id, worker_id="w")


def test_finish_attempt_owned_fires_tracker_writeback_and_bookkeeping(tmp_path, monkeypatch):
    from mu.jobs import tracker_sync
    seen = []
    monkeypatch.setattr(tracker_sync, "on_state_change",
                        lambda store, job, prior, new, reason="": seen.append((prior, new, reason)))
    clock = Clock()
    service = make(tmp_path, clock)
    job = service.create(JobSpec(title="Attempt", execution=LEGACY))
    service.transition(job.id, JobStatus.PREPARING)
    assert service.get(job.id).started_at is None  # PREPARING never sets it
    assert service.acquire(job.id, "w", ttl_seconds=600)
    attempt = service.start_attempt(job.id, worker_id="w")
    seen.clear()
    clock.advance(5)
    assert service.store.finish_attempt_owned(attempt.id, worker_id="w", status="completed",
                                              target_status=JobStatus.RUNNING, transition_reason="go")
    assert service.get(job.id).started_at == clock.now
    assert seen == [("provisioning", "implementing", "go")]
    service.store.finish_attempt_owned(attempt.id, worker_id="w", status="completed",
                                       target_status=JobStatus.VERIFYING, transition_reason="verify")
    assert seen[-1] == ("implementing", "checking", "verify")


def test_finish_attempt_owned_clears_completed_at_like_transition(tmp_path):
    service = make(tmp_path)
    job, attempt = running_job(service)
    with service.store._transaction() as conn:  # a completed_at left from an earlier pushed round
        conn.execute("UPDATE jobs SET completed_at = 1.0 WHERE id = ?", (job.id,))
    service.store.finish_attempt_owned(attempt.id, worker_id="w", status="completed",
                                       target_status=JobStatus.VERIFYING)
    assert service.get(job.id).completed_at is None


def test_cancel_race_still_records_attempt_cost_and_closes_attempt(tmp_path):
    service = make(tmp_path)
    job, attempt = running_job(service)
    service.cancel(job.id)  # lands between the agent run and the finish
    with pytest.raises(JobStateError):
        service.store.finish_attempt_owned(attempt.id, worker_id="w", status="completed", cost_usd=1.25,
                                           cost_add=1.25, target_status=JobStatus.VERIFYING)
    assert service.get(job.id).cost_usd == pytest.approx(1.25)
    assert service.get(job.id).status == JobStatus.CANCELLED
    stored = service.store.get_attempt(attempt.id)
    assert stored.status == "completed" and stored.finished_at is not None


# ------------------------------------------------------------------ 7. atomic metadata


def test_patch_metadata_merges_removes_and_mutates_atomically(tmp_path):
    from mu.jobs.stages import merge_metadata
    service = make(tmp_path)
    job = service.create(JobSpec(title="Meta", execution=LEGACY))
    stale = service.get(job.id)  # an old snapshot must not clobber keys written since
    merge_metadata(service, job.id, a=1, pr={"url": "u"})
    service.store.patch_metadata(job.id, {"b": 2})
    service.store.patch_metadata(job.id, remove=("a",), mutate=lambda m: m["pr"].update(state="open"))
    meta = service.get(job.id).metadata
    assert "a" not in meta and meta["b"] == 2 and meta["pr"] == {"url": "u", "state": "open"}
    assert stale.metadata.get("b") is None


def test_command_metadata_merge_does_not_drop_concurrent_keys(tmp_path, monkeypatch):
    from mu.jobs.core import commands
    service = make(tmp_path)
    job = service.create(JobSpec(title="Meta", execution=LEGACY))
    real_get = service.store.get_job
    raced = []

    def racing_get(job_id):
        snapshot = real_get(job_id)
        if not raced:  # another process writes right after the first read
            raced.append(True)
            service.store.patch_metadata(job_id, {"written_by_worker": True})
        return snapshot

    monkeypatch.setattr(service.store, "get_job", racing_get)
    commands._merge_meta(service, job.id, publish_override={"draft": True})
    meta = real_get(job.id).metadata
    assert meta["written_by_worker"] is True and meta["publish_override"] == {"draft": True}


# ------------------------------------------------------------------ 8. runtime deadline


def _controller(service, tmp_path, job_id, monkeypatch, wall):
    ctl = JobController(service, process_factory=lambda *a, **k: None, project_root=str(tmp_path))
    proc = FakeProcess()
    ctl._active[job_id] = WorkerHandle(job_id=job_id, phase="implementation", worker_id="w",
                                       process=proc, log_path=str(tmp_path / "log"))
    monkeypatch.setattr("mu.jobs.controller.time.time", lambda: wall[0])
    return ctl, proc


def test_deadline_skips_statuses_that_cannot_time_out(tmp_path, monkeypatch):
    clock = Clock()
    service = make(tmp_path, clock)
    job = service.create(JobSpec(title="Slow", execution=LEGACY, max_runtime_seconds=60))
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.RUNNING)
    clock.advance(120)
    service.transition(job.id, JobStatus.FAILED, reason="crash")  # FAILED cannot -> TIMED_OUT
    ctl, proc = _controller(service, tmp_path, job.id, monkeypatch, [clock.now])
    for _ in range(3):
        ctl._enforce_runtime_deadlines()
    types = [e.event_type for e in service.events(job.id)]
    assert "worker_timeout_transition_failed" not in types
    assert "worker_runtime_deadline_exceeded" not in types
    assert proc.signals == []


def test_runtime_limit_is_noted_once_and_pause_requested_once(tmp_path, monkeypatch):
    """Past the runtime limit the sweep never writes a status (no TIMED_OUT): it logs the limit
    once and, past the grace window, asks the agent stage to pause gracefully once."""
    clock = Clock()
    service = make(tmp_path, clock)
    job = service.create(JobSpec(title="Slow", execution=LEGACY, max_runtime_seconds=60))
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.RUNNING)
    clock.advance(60 + 121)
    ctl, proc = _controller(service, tmp_path, job.id, monkeypatch, [clock.now])

    def refuse(*_a, **_k):
        raise AssertionError("the runtime sweep must not transition the job")

    monkeypatch.setattr(service, "transition", refuse)
    for _ in range(3):
        ctl._enforce_runtime_deadlines()
    types = [e.event_type for e in service.events(job.id)]
    assert types.count("worker_runtime_limit_reached") == 1
    assert types.count("stage_pause_requested") == 1
    assert proc.signals == []
    assert service.get(job.id).status == JobStatus.RUNNING


def test_retried_timed_out_job_gets_a_fresh_runtime_budget(tmp_path, monkeypatch):
    """Old rows: a job stored as timed_out (no code writes that status now) is retried with a
    fresh runtime budget."""
    from mu.jobs.core.states import JobState
    clock = Clock()
    service = make(tmp_path, clock)
    job = service.create(JobSpec(title="Slow", execution=LEGACY, max_runtime_seconds=60))
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.RUNNING)
    clock.advance(120)
    service.store.transition(job.id, JobStatus.TIMED_OUT, reason="old row", core_target=JobState.FAILED)
    clock.advance(10)
    service.retry(job.id)
    service.transition(job.id, JobStatus.PREPARING)
    clock.advance(5)
    assert service.store.active_runtime_seconds(job.id) == pytest.approx(5)
    ctl, proc = _controller(service, tmp_path, job.id, monkeypatch, [clock.now])
    ctl._enforce_runtime_deadlines()
    assert service.get(job.id).status == JobStatus.PREPARING
    assert proc.signals == []
