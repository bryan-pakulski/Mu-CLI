from __future__ import annotations

from mu.jobs import JobService, JobSpec, JobStatus, JobStore
from mu.jobs.controller import JobController


class FakeProcess:
    _pid = 7000

    def __init__(self):
        type(self)._pid += 1
        self.pid = type(self)._pid
        self.code = None

    def poll(self):
        return self.code

    def terminate(self):
        self.code = -15

    def wait(self):
        if self.code is None:
            self.code = 0
        return self.code


class Factory:
    def __init__(self):
        self.calls = []

    def __call__(self, command, **kwargs):
        process = FakeProcess()
        self.calls.append((list(command), kwargs, process))
        return process


def service(tmp_path):
    return JobService(JobStore(str(tmp_path / "jobs.sqlite3")))


def test_verifying_job_launches_verifier_not_implementation_worker(tmp_path):
    svc = service(tmp_path)
    job = svc.create(JobSpec(title="Verify", repository="/repo"))
    svc.transition(job.id, JobStatus.PREPARING)
    svc.transition(job.id, JobStatus.RUNNING)
    svc.transition(job.id, JobStatus.VERIFYING)
    factory = Factory()
    controller = JobController(
        svc,
        max_workers=5,
        process_factory=factory,
        project_root=str(tmp_path),
        python_executable="python-test",
    )

    assert controller.tick() == 1
    command = factory.calls[0][0]
    assert command[:3] == ["python-test", "-m", "mu.jobs.verify_worker"]
    assert controller.snapshot()["processes"][job.id]["phase"] == "verification"


def test_queued_job_launches_implementation_worker(tmp_path):
    svc = service(tmp_path)
    job = svc.create(JobSpec(title="Implement", repository="/repo"))
    factory = Factory()
    controller = JobController(
        svc,
        max_workers=5,
        process_factory=factory,
        project_root=str(tmp_path),
        python_executable="python-test",
    )

    assert controller.tick() == 1
    command = factory.calls[0][0]
    assert command[:3] == ["python-test", "-m", "mu.jobs.worker"]
    assert controller.snapshot()["processes"][job.id]["phase"] == "implementation"


def test_expired_verifier_lease_releases_ownership_without_reimplementation(tmp_path):
    class Clock:
        def __init__(self):
            self.value = 1000.0
        def __call__(self):
            return self.value

    clock = Clock()
    svc = JobService(JobStore(str(tmp_path / "jobs.sqlite3"), clock=clock))
    job = svc.create(JobSpec(title="Verify again"))
    svc.transition(job.id, JobStatus.PREPARING)
    svc.transition(job.id, JobStatus.RUNNING)
    svc.transition(job.id, JobStatus.VERIFYING)
    assert svc.acquire(job.id, "verify-worker", ttl_seconds=5)
    clock.value += 6

    recovered = svc.recover_expired_leases()
    current = svc.get(job.id)
    assert current.status == JobStatus.VERIFYING
    assert current.worker_id == ""
    assert [value.id for value in recovered] == [job.id]
    assert any(event.event_type == "verification_lease_expired" for event in svc.events(job.id))
