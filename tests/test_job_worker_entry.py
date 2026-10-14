"""One worker entry point: phase dispatch, compatibility shims, shared lease frame."""

from __future__ import annotations

import pytest

from mu.jobs import JobService, JobSpec, JobStatus, JobStore
from mu.jobs import publish, review_worker, verify_worker, worker
from mu.jobs.lease import run_leased
from mu.jobs.service import LeaseLostError


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


def make(tmp_path):
    clock = Clock()
    return JobService(JobStore(str(tmp_path / "jobs.sqlite3"), clock=clock)), clock


@pytest.mark.parametrize("phase,module,name", [
    ("implementation", worker, "run_job"),
    ("verification", verify_worker, "run_verification"),
    ("review", review_worker, "run_review"),
    ("publish", publish, "run_publish"),
])
def test_single_entry_point_dispatches_every_phase(monkeypatch, phase, module, name):
    calls = []
    monkeypatch.setattr(module, name, lambda job_id, worker_id, *, lease_ttl_seconds: calls.append(
        (job_id, worker_id, lease_ttl_seconds)) or 7)
    argv = ["--phase", phase, "--job-id", "j1", "--worker-id", "w1", "--lease-ttl", "30"]
    assert worker.main(argv) == 7
    assert calls == [("j1", "w1", 30)]


@pytest.mark.parametrize("module,phase", [
    (verify_worker, "verification"), (review_worker, "review"), (publish, "publish"),
])
def test_legacy_module_entry_points_are_shims(monkeypatch, module, phase):
    seen = []
    monkeypatch.setattr(worker, "run_phase", lambda p, job_id, worker_id, *, lease_ttl_seconds: seen.append(p) or 0)
    assert module.main(["--job-id", "j", "--worker-id", "w"]) == 0
    assert seen == [phase]


def test_run_leased_rejects_a_worker_without_the_lease(tmp_path):
    service, _ = make(tmp_path)
    job = service.create(JobSpec(title="t"))
    assert service.acquire(job.id, "owner", ttl_seconds=60)
    ran = []
    code = run_leased(service, job.id, "intruder", phase="review", lease_ttl_seconds=30,
                      body=lambda *a: ran.append(a) or 0, reject_event="review_worker_rejected")
    assert code == 2 and not ran
    assert service.get(job.id).worker_id == "owner"  # the owner's lease is untouched
    assert any(e.event_type == "review_worker_rejected" for e in service.events(job.id))


def test_run_leased_skips_jobs_outside_the_phase_and_always_releases(tmp_path):
    service, _ = make(tmp_path)
    job = service.create(JobSpec(title="t"))
    assert service.acquire(job.id, "w", ttl_seconds=60)
    code = run_leased(service, job.id, "w", phase="verification", lease_ttl_seconds=30,
                      body=lambda *a: pytest.fail("must not run"), statuses=(JobStatus.VERIFYING,))
    assert code == 0
    assert service.get(job.id).worker_id == ""


def test_phase_body_writes_are_lease_fenced(tmp_path):
    service, clock = make(tmp_path)
    job = service.create(JobSpec(title="t"))
    assert service.acquire(job.id, "w", ttl_seconds=60)

    def body(fenced, current, lease):
        assert fenced.lease_owner == "w" and lease.owned()
        fenced.transition(current.id, JobStatus.PREPARING)
        clock.now += 120  # the lease lapses mid-phase
        assert not lease.owned()
        with pytest.raises(LeaseLostError):
            fenced.transition(current.id, JobStatus.RUNNING)
        with pytest.raises(LeaseLostError):
            fenced.require_human(current.id, "question", "still there?")
        raise LeaseLostError("superseded")

    code = run_leased(service, job.id, "w", phase="implementation", lease_ttl_seconds=30, body=body)
    assert code == 0  # a lost lease is a clean exit, not a crash
    assert service.get(job.id).status == JobStatus.PREPARING
    assert service.lease_owner is None  # the shared service is never fenced itself
