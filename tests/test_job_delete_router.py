"""DELETE /api/jobs/{id} and /manage/bulk honour the single-step force delete."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mu.gui.routers import jobs as jobs_router
from mu.jobs import JobService, JobSpec, JobStore, JobStatus
from mu.jobs.management import JobManagementService


@pytest.fixture
def service(tmp_path):
    return JobService(JobStore(str(tmp_path / "jobs" / "jobs.sqlite3")))


@pytest.fixture
def client(service):
    app = FastAPI()
    app.include_router(jobs_router.router, prefix="/api/jobs", tags=["jobs"])
    app.state.job_service = service
    JobManagementService.lease_poll_interval = 0.01
    with TestClient(app) as test_client:
        yield test_client


def test_force_delete_endpoint_cancels_active_job_and_purges(client, service):
    job = service.create(JobSpec(title="Kill me", repository="/repo"))
    service.transition(job.id, JobStatus.PREPARING, reason="prepare")
    service.transition(job.id, JobStatus.RUNNING, reason="run")

    response = client.delete(f"/api/jobs/{job.id}", params={"force": 1})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] is True
    assert body["branch_preserved"] == job.branch
    with pytest.raises(KeyError):
        service.get(job.id)


def test_delete_without_force_still_refuses_unarchived_job(client, service):
    job = service.create(JobSpec(title="Stopped", repository="/repo"))
    service.cancel(job.id, reason="done")

    response = client.delete(f"/api/jobs/{job.id}")

    assert response.status_code == 409
    assert "Archive a historic job" in response.json()["detail"]
    assert service.get(job.id).id == job.id


def test_force_delete_endpoint_returns_409_while_lease_persists(client, service):
    job = service.create(JobSpec(title="Leased", repository="/repo"))
    service.transition(job.id, JobStatus.PREPARING, reason="prepare")
    assert service.store.acquire_lease(job.id, "worker-1", ttl_seconds=60)

    response = client.delete(f"/api/jobs/{job.id}", params={"force": 1, "lease_wait_seconds": 0})

    assert response.status_code == 409
    assert "still cancelling" in response.json()["detail"]
    assert service.get(job.id).status == JobStatus.CANCELLED
    assert service.get(job.id).id == job.id


def test_delete_missing_job_returns_404(client):
    response = client.delete("/api/jobs/does-not-exist", params={"force": 1})
    assert response.status_code == 404


def test_bulk_delete_forwards_force(client, service):
    active = service.create(JobSpec(title="Active", repository="/repo"))
    stopped = service.create(JobSpec(title="Stopped", repository="/repo"))
    service.cancel(stopped.id, reason="done")

    refused = client.post("/api/jobs/manage/bulk", json={"action": "delete", "job_ids": [active.id, stopped.id]})
    assert refused.status_code == 200
    assert refused.json()["succeeded"] == 0
    assert refused.json()["failed"] == 2

    forced = client.post(
        "/api/jobs/manage/bulk",
        json={"action": "delete", "job_ids": [active.id, stopped.id], "force": True},
    )
    assert forced.status_code == 200, forced.text
    assert forced.json()["succeeded"] == 2
    for job_id in (active.id, stopped.id):
        with pytest.raises(KeyError):
            service.get(job_id)
