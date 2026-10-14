"""W1 recovery and scheduling regressions (documentation/job_mode_plan.md).

1. PLANNING lease expiry re-plans instead of implementing without a plan.
2. Lease recovery reaps the job's non-engine stage containers.
3. The runtime deadline counts active stage time only.
4. Scheduler picks QUEUED work FIFO; heartbeating jobs never crowd it out.
5. Stage crash retries are capped, then the job parks on a human.
6. Recovering a RUNNING job closes the orphaned attempt as interrupted.
7. Transient retries use their own budget; fix attempts do not consume it.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from mu.jobs import AttentionReason, JobService, JobSpec, JobStatus, JobStore
from mu.jobs import worker as worker_mod
from mu.jobs.controller import JobController, WorkerHandle
from mu.jobs.runner import JobRunOutcome
from mu.jobs.sandbox.reaper import reap_stage_containers


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeProcess:
    def __init__(self, pid=4242):
        self.pid = pid
        self.returncode = None
        self.signals = []

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.signals.append("TERM")

    def kill(self):
        self.signals.append("KILL")
        self.returncode = -9


class Factory:
    def __init__(self):
        self.commands = []

    def __call__(self, command, **kwargs):
        self.commands.append(list(command))
        return FakeProcess(5000 + len(self.commands))


def make(tmp_path, clock=None, reaped=None):
    clock = clock or Clock()
    reaped = reaped if reaped is not None else []

    def reaper(job_id):
        reaped.append(job_id)
        return 0

    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3"), clock=clock), reaper=reaper)
    return service, clock, reaped


STAGED = {"provider": "openai", "model": "m", "session_type": "chat", "pipeline": "staged"}
LEGACY = {"provider": "openai", "model": "m", "session_type": "chat"}


def controller(service, tmp_path, factory=None, max_workers=5):
    return JobController(
        service, max_workers=max_workers, process_factory=factory or Factory(),
        project_root=str(tmp_path), python_executable="python-test",
        drift_check_interval_seconds=0, environment_sweep_interval_seconds=0,
    )


def expire(service, clock, job_id, worker="dead-worker"):
    assert service.acquire(job_id, worker, ttl_seconds=10)
    clock.advance(11)
    return service.recover_expired_leases()


# ------------------------------------------------------------------ 1. planning recovery


def test_planning_lease_expiry_replans_instead_of_recovering(tmp_path):
    service, clock, reaped = make(tmp_path)
    job = service.create(JobSpec(title="Plan me", execution=STAGED))
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.PLANNING)

    recovered = expire(service, clock, job.id)

    current = service.get(job.id)
    assert [j.id for j in recovered] == [job.id]
    assert current.status == JobStatus.PLANNING  # not RECOVERING -> implementation
    assert current.worker_id == ""
    assert service.store.core_state(job.id).state.value != "implementing"
    assert not any(e.to_status == JobStatus.RECOVERING for e in service.events(job.id))
    assert any(e.event_type == "planning_lease_expired" for e in service.events(job.id))
    assert reaped == [job.id]


def test_preparing_staged_job_without_plan_recovers_to_planning(tmp_path):
    service, clock, _ = make(tmp_path)
    job = service.create(JobSpec(title="Prep", execution=STAGED))
    service.transition(job.id, JobStatus.PREPARING)
    expire(service, clock, job.id)
    assert service.get(job.id).status == JobStatus.PLANNING


def test_run_job_refuses_to_implement_staged_job_without_approved_plan(tmp_path, monkeypatch):
    """The crash path: PLANNING -> RECOVERING (old recovery) -> run_job
    implemented with no plan. Now run_job parks the job instead."""
    service, clock, _ = make(tmp_path)
    job = service.create(JobSpec(title="No plan", execution=STAGED))
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.PLANNING)
    service.transition(job.id, JobStatus.RECOVERING)  # what the old recovery did
    assert service.acquire(job.id, "w", ttl_seconds=600)

    ran = []

    class Runner:
        def __init__(self, *_a, **_k):
            pass

        def run(self, *_a, **_k):
            ran.append(True)
            raise AssertionError("implementation must not start without an approved plan")

    monkeypatch.setattr("mu.jobs.sandbox.runner.SandboxJobRunner", Runner)
    monkeypatch.setattr(worker_mod, "get_default_job_service", lambda: service)
    monkeypatch.setattr(worker_mod, "_refresh_receipt", lambda *_a, **_k: None)

    rc = worker_mod.run_job(job.id, "w")

    current = service.get(job.id)
    assert ran == []
    assert rc == 28
    assert current.status == JobStatus.NEEDS_HUMAN
    assert current.attention_reason == AttentionReason.WORKER_LOST
    assert "approved plan" in current.attention_detail
    assert service.attempts(job.id) == []


def test_run_job_unapproved_plan_parks_on_plan_review(tmp_path, monkeypatch):
    service, clock, _ = make(tmp_path)
    job = service.create(JobSpec(title="Unapproved", execution=STAGED))
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.PLANNING)
    service.transition(job.id, JobStatus.RECOVERING)
    assert service.acquire(job.id, "w", ttl_seconds=600)

    class Store:
        def latest_version(self):
            return 3

    monkeypatch.setattr("mu.jobs.core.plan_ops.plan_store", lambda _job: Store())
    monkeypatch.setattr(worker_mod, "get_default_job_service", lambda: service)
    monkeypatch.setattr(worker_mod, "_refresh_receipt", lambda *_a, **_k: None)

    assert worker_mod.run_job(job.id, "w") == 28
    current = service.get(job.id)
    assert current.status == JobStatus.NEEDS_HUMAN
    assert current.attention_reason == AttentionReason.PLAN_REVIEW
    assert "v3" in current.attention_detail


# ------------------------------------------------------------------ 2. reaper


class FakeDocker:
    def __init__(self, label_rows="", name_rows="", ps_rc=0, rm_rc=0):
        self.calls = []
        self.label_rows, self.name_rows, self.ps_rc, self.rm_rc = label_rows, name_rows, ps_rc, rm_rc

    def __call__(self, args):
        args = list(args)
        self.calls.append(args)
        if args[0] == "ps":
            selector = args[args.index("--filter") + 1]
            out = self.label_rows if selector.startswith("label=") else self.name_rows
            return subprocess.CompletedProcess(args, self.ps_rc, out, "boom" if self.ps_rc else "")
        return subprocess.CompletedProcess(args, self.rm_rc, "", "")


def test_reaper_removes_stage_containers_but_keeps_engine():
    job_id = "abcdef1234567890"
    docker = FakeDocker(
        label_rows=(
            "c1\tmucli-job-abcdef123456-implement-1a2b\timplement\n"
            "c2\tmucli-job-abcdef123456-engine\tjob-engine\n"
            "c3\tmucli-job-abcdef123456-checks-9\tchecks\n"
        ),
        name_rows="c1\tmucli-job-abcdef123456-implement-1a2b\timplement\nc4\tmucli-job-abcdef123456-plan-x\t\n",
    )
    assert reap_stage_containers(job_id, runner=docker) == 3
    removed = [c[2] for c in docker.calls if c[0] == "rm"]
    assert removed == ["c1", "c3", "c4"]
    ps = [c for c in docker.calls if c[0] == "ps"]
    assert ps[0][ps[0].index("--filter") + 1] == f"label=io.mucli.job={job_id}"


def test_reaper_is_noop_when_docker_fails_or_missing(monkeypatch):
    assert reap_stage_containers("job1", runner=FakeDocker(ps_rc=1)) == 0

    def broken(args):
        raise FileNotFoundError("docker")

    assert reap_stage_containers("job1", runner=broken) == 0
    monkeypatch.setattr("mu.jobs.sandbox.reaper.shutil.which", lambda _name: None)
    assert reap_stage_containers("job1") == 0
    assert reap_stage_containers("", runner=FakeDocker()) == 0


def test_every_lease_recovery_reaps_before_redispatch(tmp_path):
    service, clock, reaped = make(tmp_path)
    running = service.create(JobSpec(title="Run", execution=LEGACY))
    service.transition(running.id, JobStatus.PREPARING)
    service.transition(running.id, JobStatus.RUNNING)
    verifying = service.create(JobSpec(title="Verify", execution=LEGACY))
    for status in (JobStatus.PREPARING, JobStatus.RUNNING, JobStatus.VERIFYING):
        service.transition(verifying.id, status)
    assert service.acquire(running.id, "a", ttl_seconds=10)
    assert service.acquire(verifying.id, "b", ttl_seconds=10)
    clock.advance(11)
    service.recover_expired_leases()
    assert sorted(reaped) == sorted([running.id, verifying.id])


def test_reaper_failure_does_not_block_recovery(tmp_path):
    clock = Clock()

    def reaper(_job_id):
        raise RuntimeError("docker exploded")

    service = JobService(JobStore(str(tmp_path / "jobs.sqlite3"), clock=clock), reaper=reaper)
    job = service.create(JobSpec(title="Run", execution=LEGACY))
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.RUNNING)
    expire(service, clock, job.id)
    assert service.get(job.id).status == JobStatus.RECOVERING


# ------------------------------------------------------------------ 3. runtime deadline


def test_runtime_deadline_ignores_time_parked_at_human_gate(tmp_path, monkeypatch):
    service, clock, _ = make(tmp_path)
    job = service.create(JobSpec(title="Slow human", execution=LEGACY, max_runtime_seconds=3600))
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.RUNNING)
    clock.advance(600)  # 10 minutes of real work
    service.transition(job.id, JobStatus.NEEDS_HUMAN, attention_reason=AttentionReason.REVIEW_DONE,
                       attention_detail="review finished")
    clock.advance(86_400)  # parked a whole day
    service.transition(job.id, JobStatus.RUNNING, reason="human re-prompted")

    ctl = controller(service, tmp_path)
    proc = FakeProcess()
    ctl._active[job.id] = WorkerHandle(job_id=job.id, phase="implementation", worker_id="w",
                                       process=proc, log_path=str(tmp_path / "log"))
    wall = [clock.now + 60]
    monkeypatch.setattr("mu.jobs.controller.time.time", lambda: wall[0])

    ctl._enforce_runtime_deadlines()
    assert service.get(job.id).status == JobStatus.RUNNING
    assert proc.signals == []
    assert service.store.active_runtime_seconds(job.id, now=wall[0]) == pytest.approx(660)

    wall[0] = clock.now + 3001  # 600 + 3001 active seconds > 3600: limit reached, no kill
    ctl._enforce_runtime_deadlines()
    assert service.get(job.id).status == JobStatus.RUNNING
    assert proc.signals == []
    assert any(e.event_type == "worker_runtime_limit_reached" for e in service.events(job.id))


# ------------------------------------------------------------------ 4. scheduler FIFO


def test_scheduler_is_fifo_and_heartbeats_do_not_crowd_out_queued_jobs(tmp_path):
    service, clock, _ = make(tmp_path)
    queued = []
    # Oldest job is still backing off and must be skipped, not block the line.
    backoff = service.create(JobSpec(title="backoff", execution=LEGACY))
    service.store.update_runtime_fields(backoff.id, metadata_json={"next_run_at": clock.now + 10_000})
    clock.advance(1)
    for i in range(3):
        queued.append(service.create(JobSpec(title=f"queued-{i}", execution=LEGACY)))
        clock.advance(1)
    # 30 newer jobs with live leases that heartbeat constantly.
    busy = []
    for i in range(30):
        job = service.create(JobSpec(title=f"busy-{i}", execution=LEGACY))
        for status in (JobStatus.PREPARING, JobStatus.RUNNING, JobStatus.VERIFYING):
            service.transition(job.id, status)
        assert service.acquire(job.id, f"verifier-{i}", ttl_seconds=600)
        busy.append(job)
        clock.advance(1)
    for i, job in enumerate(busy):
        assert service.heartbeat(job.id, f"verifier-{i}", ttl_seconds=600)

    factory = Factory()
    ctl = controller(service, tmp_path, factory=factory, max_workers=1)
    assert ctl.tick() == 1
    started = factory.commands[0][factory.commands[0].index("--job-id") + 1]
    assert started == queued[0].id  # oldest due QUEUED job, not newest/backoff

    candidates = service.store.schedulable_jobs([JobStatus.QUEUED, JobStatus.VERIFYING], now=clock.now)
    assert [j.id for j in candidates] == [q.id for q in queued[1:]]


# ------------------------------------------------------------------ 5. stage crash cap


def test_stage_crash_retries_are_capped_then_need_human(tmp_path):
    service, clock, reaped = make(tmp_path)
    job = service.create(JobSpec(title="Flaky verifier", execution=LEGACY))
    for status in (JobStatus.PREPARING, JobStatus.RUNNING, JobStatus.VERIFYING):
        service.transition(job.id, status)

    for n in (1, 2):
        expire(service, clock, job.id, worker=f"v{n}")
        assert service.get(job.id).status == JobStatus.VERIFYING
    expire(service, clock, job.id, worker="v3")

    current = service.get(job.id)
    assert current.status == JobStatus.NEEDS_HUMAN
    assert current.attention_reason == AttentionReason.WORKER_LOST
    assert "max_stage_crash_retries=2" in current.attention_detail
    assert len(reaped) == 3


def test_stage_crash_cap_reads_setting_and_resets_per_stage_entry(tmp_path, monkeypatch):
    service, clock, _ = make(tmp_path)
    monkeypatch.setattr("mu.jobs.service._job_default",
                        lambda path, fallback=None: 0 if path == "jobs.max_stage_crash_retries" else fallback)
    job = service.create(JobSpec(title="Reviewer", execution=LEGACY))
    for status in (JobStatus.PREPARING, JobStatus.RUNNING, JobStatus.VERIFYING):
        service.transition(job.id, status)
    expire(service, clock, job.id)
    assert service.get(job.id).status == JobStatus.NEEDS_HUMAN

    monkeypatch.setattr("mu.jobs.service._job_default",
                        lambda path, fallback=None: 1 if path == "jobs.max_stage_crash_retries" else fallback)
    service.transition(job.id, JobStatus.REVIEWING)
    expire(service, clock, job.id)  # first crash of this REVIEWING stint
    assert service.get(job.id).status == JobStatus.REVIEWING


# ------------------------------------------------------------------ 6. orphaned attempt


def test_running_recovery_closes_orphaned_attempt_as_interrupted(tmp_path):
    service, clock, _ = make(tmp_path)
    job = service.create(JobSpec(title="Crash", execution=LEGACY))
    service.transition(job.id, JobStatus.PREPARING)
    attempt = service.start_attempt(job.id, worker_id="dead")
    service.transition(job.id, JobStatus.RUNNING)
    expire(service, clock, job.id, worker="dead")

    assert service.get(job.id).status == JobStatus.RECOVERING
    closed = service.store.get_attempt(attempt.id)
    assert closed.status == "interrupted"
    assert closed.finished_at is not None
    assert closed.metadata["expired_worker_id"] == "dead"


# ------------------------------------------------------------------ 7. retry semantics


class NullManager:
    def __init__(self, *_a, **_k):
        pass

    def prepare(self, _job):
        return None

    def checkpoint(self, _job, label=""):
        return None


def _running(service, job_id, worker="worker"):
    service.transition(job_id, JobStatus.PREPARING)
    attempt = service.start_attempt(job_id, worker_id=worker)
    service.transition(job_id, JobStatus.RUNNING)
    assert service.acquire(job_id, worker, ttl_seconds=600)
    return attempt


def test_fix_attempts_do_not_consume_transient_retry_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(worker_mod, "_refresh_receipt", lambda *_a, **_k: None)
    service, clock, _ = make(tmp_path)
    job = service.create(JobSpec(title="Fixes", execution=LEGACY, max_retries=1))
    # Two earlier implementation rounds that went to verification and were
    # sent back for fixes (not transient retries).
    for _ in range(2):
        attempt = _running(service, job.id)
        service.store.finish_attempt_owned(attempt.id, worker_id="worker", status="completed",
                                           target_status=JobStatus.VERIFYING)
        service.transition(job.id, JobStatus.QUEUED, reason="verification failed; fix requested")
        service.release(job.id, "worker")

    attempt = _running(service, job.id)
    assert attempt.number == 3
    code = worker_mod._apply_outcome(
        service, NullManager(), job.id, attempt.id, attempt.number,
        JobRunOutcome(kind="failed", status="error", error="429", transient=True), worker_id="worker",
    )
    assert code == 10  # old code: attempt 3 > max_retries 1 -> FAILED
    assert service.get(job.id).status == JobStatus.QUEUED
    service.release(job.id, "worker")

    attempt = _running(service, job.id)
    code = worker_mod._apply_outcome(
        service, NullManager(), job.id, attempt.id, attempt.number,
        JobRunOutcome(kind="failed", status="error", error="429 again", transient=True), worker_id="worker",
    )
    assert code == 1  # the one transient retry is spent
    assert service.get(job.id).status == JobStatus.FAILED
    assert service.store.get_attempt(attempt.id).metadata["retry_budget_exhausted"] is True


def test_run_job_honours_transient_outcome_from_runner(tmp_path, monkeypatch):
    service, clock, _ = make(tmp_path)
    job = service.create(JobSpec(title="Rate limited", execution=LEGACY, max_retries=2))
    assert service.acquire(job.id, "w", ttl_seconds=600)

    class Runner:
        def __init__(self, *_a, **_k):
            pass

        def run(self, _job, _attempt):
            outcome = JobRunOutcome(kind="failed", status="error", error="429 Too Many Requests",
                                    cost_usd=0.02, transient=True)
            outcome.error_class = "rate_limit"
            return outcome

    monkeypatch.setattr("mu.jobs.sandbox.runner.SandboxJobRunner", Runner)
    monkeypatch.setattr(worker_mod, "get_default_job_service", lambda: service)
    monkeypatch.setattr(worker_mod, "JobWorktreeManager", NullManager)
    monkeypatch.setattr(worker_mod, "_refresh_receipt", lambda *_a, **_k: None)

    rc = worker_mod.run_job(job.id, "w")

    current = service.get(job.id)
    assert rc == 10
    assert current.status == JobStatus.QUEUED
    assert current.metadata["next_run_at"] == pytest.approx(clock.now + worker_mod._retry_backoff_seconds(1))
    finished = service.attempts(job.id)[-1]
    assert finished.metadata["retry_scheduled"] is True
    assert finished.metadata["error_class"] == "rate_limit"


# ------------------------------------------------------------------ lease-fenced worker writes


def test_lease_fenced_transition_rejects_superseded_worker(tmp_path):
    from mu.jobs.service import LeaseLostError

    service, clock, _ = make(tmp_path)
    job = service.create(JobSpec(title="Fence", execution=LEGACY))
    assert service.acquire(job.id, "old", ttl_seconds=10)
    clock.advance(11)
    assert service.acquire(job.id, "new", ttl_seconds=600)  # takeover
    with pytest.raises(LeaseLostError):
        service.transition(job.id, JobStatus.PREPARING, lease_owner="old")
    assert service.get(job.id).status == JobStatus.QUEUED
    assert service.transition(job.id, JobStatus.PREPARING, lease_owner="new").status == JobStatus.PREPARING


def test_run_job_with_lost_lease_does_not_move_the_job(tmp_path, monkeypatch):
    service, clock, _ = make(tmp_path)
    job = service.create(JobSpec(title="Zombie", execution=LEGACY))
    assert service.acquire(job.id, "w", ttl_seconds=10)
    clock.advance(11)  # lease lapsed before the worker got going
    monkeypatch.setattr(worker_mod, "get_default_job_service", lambda: service)
    monkeypatch.setattr(worker_mod, "_refresh_receipt", lambda *_a, **_k: None)
    assert worker_mod.run_job(job.id, "w") == 0
    assert service.get(job.id).status == JobStatus.QUEUED
