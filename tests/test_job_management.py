from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from mu.jobs import JobService, JobSpec, JobStore, JobStatus
from mu.jobs.board import build_job_board
from mu.jobs.management import JobManagementError, JobManagementService


def make_service(tmp_path):
    return JobService(JobStore(str(tmp_path / "jobs" / "jobs.sqlite3")))


def stopped_job(service, title="Historic job"):
    job = service.create(JobSpec(title=title, repository="/repo"))
    return service.cancel(job.id, reason="test complete")


def failed_job(service, title="Failed job"):
    job = service.create(JobSpec(title=title, repository="/repo"))
    service.transition(job.id, JobStatus.PREPARING, reason="prepare")
    return service.transition(job.id, JobStatus.ENVIRONMENT_ERROR, reason="boom")


def test_archive_hides_job_from_operational_board_but_keeps_history(tmp_path):
    service = make_service(tmp_path)
    job = stopped_job(service)
    management = JobManagementService(service)

    assert any(item.id == job.id for item in build_job_board(service).done)

    state = management.archive(job.id, reason="old ticket")

    assert state["archived"] is True
    assert not any(item.id == job.id for item in build_job_board(service).done)
    history = management.query_jobs(archive="archived", scope="history")
    assert history["total"] == 1
    assert history["jobs"][0]["id"] == job.id
    assert history["jobs"][0]["archived_reason"] == "old ticket"

    restored = management.restore(job.id)
    assert restored["archived"] is False
    assert any(item.id == job.id for item in build_job_board(service).done)


def test_only_stopped_jobs_can_be_archived_or_deleted(tmp_path):
    service = make_service(tmp_path)
    management = JobManagementService(service)
    active = service.create(JobSpec(title="Still queued", repository="/repo"))

    with pytest.raises(JobManagementError, match="cannot be archived"):
        management.archive(active.id)

    historic = stopped_job(service, "Needs archive first")
    with pytest.raises(JobManagementError, match="Archive a historic job"):
        management.delete(historic.id, purge_artifacts=False)


def test_query_and_report_filter_history_and_aggregate_cost(tmp_path):
    service = make_service(tmp_path)
    management = JobManagementService(service)
    done = stopped_job(service, "Drone integration finished")
    failed = failed_job(service, "Drone integration failed")
    service.store.update_runtime_fields(done.id, cost_usd=1.25)
    service.store.update_runtime_fields(failed.id, cost_usd=2.75)
    service.start_attempt(done.id, worker_id="test")
    attempt = service.attempts(done.id)[0]
    service.finish_attempt(attempt.id, status="completed", cost_usd=1.25)

    query = management.query_jobs(q="Drone integration", scope="history", limit=20)
    report = management.report(q="Drone integration", scope="history")

    assert query["total"] == 2
    assert {item["status"] for item in query["jobs"]} == {"cancelled", "environment_error"}
    assert report["total_jobs"] == 2
    assert report["failure_count"] == 1
    assert report["failure_rate"] == pytest.approx(0.5)
    assert report["total_cost_usd"] == pytest.approx(4.0)
    assert report["attempts"] == 1
    assert report["status_counts"]["cancelled"] == 1
    assert report["status_counts"]["environment_error"] == 1


def test_delete_archived_job_cascades_durable_history_and_artifacts(tmp_path):
    service = make_service(tmp_path)
    job = stopped_job(service, "Delete me")
    management = JobManagementService(service)
    service.store.append_event(job.id, "runtime_error", reason="old error")
    management.archive(job.id)

    root = Path(service.store.path).parent
    evidence = root / "evidence" / job.id
    evidence.mkdir(parents=True)
    (evidence / "work-receipt.json").write_text("{}", encoding="utf-8")
    logs = root / "logs"
    logs.mkdir(parents=True)
    log_path = logs / f"{job.id}.log"
    log_path.write_text("worker output", encoding="utf-8")

    result = management.delete(job.id)

    assert result["deleted"] is True
    assert result["branch_preserved"] == job.branch
    with pytest.raises(KeyError):
        service.get(job.id)
    assert not evidence.exists()
    assert not log_path.exists()
    conn = service.store._connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM job_events WHERE job_id = ?", (job.id,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM job_attempts WHERE job_id = ?", (job.id,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM job_management WHERE job_id = ?", (job.id,)).fetchone()[0] == 0
    finally:
        conn.close()


def test_debug_bundle_exports_complete_trace_and_bounded_worker_log(tmp_path):
    service = make_service(tmp_path)
    job = stopped_job(service, "Export telemetry")
    management = JobManagementService(service)
    service.store.append_event(job.id, "runtime_error", reason="provider exploded", payload={"error": "boom"})

    log_dir = Path(service.store.path).parent / "logs"
    log_dir.mkdir(parents=True)
    (log_dir / f"{job.id}.log").write_text("trace line\nprovider stack\n", encoding="utf-8")

    content = management.debug_bundle(job.id)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = set(archive.namelist())
        assert {
            "manifest.json",
            "job.json",
            "work-receipt.json",
            "diagnostics.json",
            "events.json",
            "events.ndjson",
            "attempts.json",
            "verifications.json",
            "worker.log",
        }.issubset(names)
        manifest = json.loads(archive.read("manifest.json"))
        events = json.loads(archive.read("events.json"))
        assert manifest["schema"] == "mucli-job-debug-bundle/v1"
        assert manifest["job_id"] == job.id
        assert any(event["event_type"] == "runtime_error" for event in events)
        assert b"provider stack" in archive.read("worker.log")


def test_report_exports_json_and_csv(tmp_path):
    service = make_service(tmp_path)
    stopped_job(service, "Report row")
    management = JobManagementService(service)

    json_bytes, json_type, json_name = management.report_export("json", scope="history")
    csv_bytes, csv_type, csv_name = management.report_export("csv", scope="history")

    payload = json.loads(json_bytes)
    assert payload["schema"] == "mucli-job-report/v1"
    assert payload["report"]["total_jobs"] == 1
    assert json_type == "application/json"
    assert json_name.endswith(".json")
    assert b"Report row" in csv_bytes
    assert csv_type.startswith("text/csv")
    assert csv_name.endswith(".csv")
