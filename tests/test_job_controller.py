from __future__ import annotations

from mu.jobs import JobService, JobSpec, JobStatus, JobStore
from mu.jobs.controller import JobController


class FakeProcess:
    _next_pid = 5000

    def __init__(self):
        type(self)._next_pid += 1
        self.pid = type(self)._next_pid
        self.code = None
        self.terminated = False
        self.waited = False

    def poll(self):
        return self.code

    def terminate(self):
        self.terminated = True
        self.code = -15

    def wait(self):
        self.waited = True
        if self.code is None:
            self.code = 0
        return self.code


class ProcessFactory:
    def __init__(self):
        self.calls = []
        self.processes = []

    def __call__(self, command, **kwargs):
        process = FakeProcess()
        self.calls.append((list(command), kwargs))
        self.processes.append(process)
        return process


def service(tmp_path):
    return JobService(JobStore(str(tmp_path / "jobs.sqlite3")))


def queued_job(svc, title="Ticket"):
    return svc.create(JobSpec(
        title=title,
        repository=f"/repo/{title}",
        execution={
            "provider": "openai",
            "model": "test-model",
            "agent_mode": "default",
            "session_type": "workspace",
        },
    ))


def controller(tmp_path, svc, factory, *, max_workers=5):
    return JobController(
        svc,
        max_workers=max_workers,
        process_factory=factory,
        project_root=str(tmp_path),
        python_executable="python-test",
    )


def test_scheduler_starts_five_isolated_workers_and_leaves_sixth_queued(tmp_path):
    svc = service(tmp_path)
    jobs = [queued_job(svc, f"Ticket {index}") for index in range(6)]
    factory = ProcessFactory()
    ctl = controller(tmp_path, svc, factory, max_workers=5)

    assert ctl.tick() == 5
    assert len(factory.calls) == 5
    assert len(ctl.active_job_ids) == 5
    assert len([job for job in jobs if svc.get(job.id).worker_id]) == 5
    assert len([job for job in jobs if not svc.get(job.id).worker_id]) == 1

    for command, kwargs in factory.calls:
        assert command[:3] == ["python-test", "-m", "mu.jobs.worker"]
        assert "--job-id" in command
        assert "--worker-id" in command
        assert kwargs["cwd"] == str(tmp_path)
        assert kwargs["start_new_session"] is True

    snapshot = ctl.snapshot()
    assert snapshot["max_workers"] == 5
    assert snapshot["execution_isolation"] == "subprocess+git-worktree"
    assert len(snapshot["processes"]) == 5


def test_second_controller_cannot_duplicate_live_leased_jobs(tmp_path):
    svc = service(tmp_path)
    jobs = [queued_job(svc, f"Ticket {index}") for index in range(3)]
    first_factory = ProcessFactory()
    second_factory = ProcessFactory()
    first = controller(tmp_path, svc, first_factory)
    second = controller(tmp_path, svc, second_factory)

    assert first.tick() == 3
    assert second.tick() == 0
    assert len(first_factory.calls) == 3
    assert second_factory.calls == []
    assert all(svc.get(job.id).worker_id for job in jobs)


def test_cancelled_job_terminates_only_its_worker(tmp_path):
    svc = service(tmp_path)
    one = queued_job(svc, "One")
    two = queued_job(svc, "Two")
    factory = ProcessFactory()
    ctl = controller(tmp_path, svc, factory)
    ctl.tick()

    handles = dict(ctl._active)
    svc.cancel(one.id)
    ctl.tick()

    assert handles[one.id].process.terminated is True
    assert handles[two.id].process.terminated is False
    assert svc.get(one.id).status == JobStatus.CANCELLED
    assert any(event.event_type == "worker_process_terminated" for event in svc.events(one.id))


def test_controller_stop_does_not_kill_running_workers(tmp_path):
    svc = service(tmp_path)
    job = queued_job(svc)
    factory = ProcessFactory()
    ctl = controller(tmp_path, svc, factory)
    ctl.tick()
    handle = ctl._active[job.id]

    ctl.stop(wait=False)

    assert handle.process.terminated is False
    assert handle.process.poll() is None
    assert svc.get(job.id).worker_id == handle.worker_id


def test_reap_records_worker_exit_and_clears_cancelled_lease(tmp_path):
    svc = service(tmp_path)
    job = queued_job(svc)
    factory = ProcessFactory()
    ctl = controller(tmp_path, svc, factory)
    ctl.tick()
    handle = ctl._active[job.id]
    svc.cancel(job.id)
    handle.process.code = -15

    ctl._reap()

    assert job.id not in ctl.active_job_ids
    assert svc.get(job.id).worker_id == ""
    events = svc.events(job.id)
    assert any(event.event_type == "worker_process_exited" for event in events)


def test_worker_spawn_failure_releases_lease_and_keeps_job_retryable(tmp_path):
    svc = service(tmp_path)
    job = queued_job(svc)

    def fail(*args, **kwargs):
        raise OSError("cannot spawn")

    ctl = JobController(
        svc,
        max_workers=5,
        process_factory=fail,
        project_root=str(tmp_path),
        python_executable="python-test",
    )
    assert ctl.tick() == 0
    current = svc.get(job.id)
    assert current.status == JobStatus.QUEUED
    assert current.worker_id == ""
    assert any(event.event_type == "worker_spawn_failed" for event in svc.events(job.id))
