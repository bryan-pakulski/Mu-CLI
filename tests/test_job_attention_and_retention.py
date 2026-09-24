"""Attention push + retention sweeper (Friday Five: learn when a job needs
you without polling; artifacts do not grow forever)."""

from __future__ import annotations

from pathlib import Path

from types import SimpleNamespace

from mu.jobs import AttentionReason, JobService, JobSpec, JobStatus, JobStore
from mu.jobs.controller import JobController
from mu.jobs.management import JobManagementService

ROOT = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self, now=1_700_000_000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def make_service(tmp_path, clock=None):
    return JobService(JobStore(str(tmp_path / "jobs" / "jobs.sqlite3"), clock=clock or Clock()))


def blocked_job(service, title="Blocked", reason=AttentionReason.QUESTION):
    job = service.create(JobSpec(title=title, repository="/repo"))
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.RUNNING)
    return service.require_human(job.id, reason, f"{title} needs input")


def controller(tmp_path, service, **kw):
    return JobController(
        service, max_workers=1, process_factory=lambda *a, **k: None,
        project_root=str(tmp_path), python_executable="python-test", **kw,
    )


# ------------------------------------------------------------------ summary


def test_attention_summary_counts_needs_human_and_conflicted_only(tmp_path):
    service = make_service(tmp_path)
    blocked_job(service, "A")
    blocked_job(service, "B", AttentionReason.APPROVAL_REQUIRED)
    running = service.create(JobSpec(title="Running", repository="/repo"))
    service.transition(running.id, JobStatus.PREPARING)
    summary = service.attention_summary()
    assert summary["count"] == 2
    assert {j["title"] for j in summary["jobs"]} == {"A", "B"}
    assert all(j["attention_reason"] for j in summary["jobs"])


# ------------------------------------------------------------------ push


def test_controller_pushes_attention_on_enter_and_leave(tmp_path):
    service = make_service(tmp_path)
    events = []
    ctl = controller(tmp_path, service, notify=events.append)

    ctl.tick()  # nothing waiting -> no event
    assert events == []

    job = blocked_job(service, "Needs answer")
    ctl.tick()
    assert len(events) == 1
    ev = events[0]
    assert ev["kind"] == "job_attention" and ev["count"] == 1
    assert [j["id"] for j in ev["entered"]] == [job.id]
    assert ev["entered"][0]["attention_reason"] == "question"

    ctl.tick()  # steady state -> no duplicate push
    assert len(events) == 1

    service.resume(job.id, detail="use option B")
    ctl.tick()
    assert len(events) == 2
    assert events[1]["count"] == 0 and events[1]["left"] == [job.id]


def test_controller_announces_standing_backlog_on_first_tick(tmp_path):
    """A restarted daemon must surface jobs that were already waiting."""
    service = make_service(tmp_path)
    a = blocked_job(service, "Old A")
    b = blocked_job(service, "Old B")
    events = []
    ctl = controller(tmp_path, service, notify=events.append)
    ctl.tick()
    assert len(events) == 1
    assert events[0]["count"] == 2
    assert {j["id"] for j in events[0]["entered"]} == {a.id, b.id}


def test_notify_failure_never_breaks_tick(tmp_path):
    service = make_service(tmp_path)
    blocked_job(service)

    def boom(_event):
        raise RuntimeError("bus down")

    ctl = controller(tmp_path, service, notify=boom)
    assert ctl.tick() == 0  # no exception propagates


# ------------------------------------------------------------------ retention


def _seed_artifacts(service, job_id):
    root = Path(service.store.path).parent
    evidence = root / "evidence" / job_id
    evidence.mkdir(parents=True)
    (evidence / "work-receipt.json").write_text("{}", encoding="utf-8")
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log = logs / f"{job_id}.log"
    log.write_text("out", encoding="utf-8")
    return evidence, log


def test_sweep_purges_only_archived_historic_jobs_past_cutoff(tmp_path):
    clock = Clock()
    service = make_service(tmp_path, clock)
    mgmt = JobManagementService(service)

    old_archived = service.cancel(service.create(JobSpec(title="old archived", repository="/repo")).id)
    old_unarchived = service.cancel(service.create(JobSpec(title="old live history", repository="/repo")).id)
    mgmt.archive(old_archived.id)
    ev_old, log_old = _seed_artifacts(service, old_archived.id)
    ev_keep, _ = _seed_artifacts(service, old_unarchived.id)

    clock.advance(40 * 86400)  # 40 days later

    recent_archived = service.cancel(service.create(JobSpec(title="recent archived", repository="/repo")).id)
    mgmt.archive(recent_archived.id)
    still_waiting = blocked_job(service, "waiting")

    result = mgmt.sweep_retention(older_than_days=30)
    assert result["deleted"] == [old_archived.id]
    assert result["skipped"] == []
    assert not ev_old.exists() and not log_old.exists()
    # Unarchived history, recent archives and live jobs are untouched.
    assert ev_keep.exists()
    assert service.get(old_unarchived.id).status == JobStatus.CANCELLED
    assert service.get(recent_archived.id).status == JobStatus.CANCELLED
    assert service.get(still_waiting.id).status == JobStatus.NEEDS_HUMAN

    # Idempotent.
    assert mgmt.sweep_retention(older_than_days=30)["deleted"] == []


def test_sweep_skips_jobs_holding_a_lease(tmp_path):
    clock = Clock()
    service = make_service(tmp_path, clock)
    mgmt = JobManagementService(service)
    job = service.cancel(service.create(JobSpec(title="leased", repository="/repo")).id)
    mgmt.archive(job.id)
    assert service.acquire(job.id, "w1", ttl_seconds=600)
    clock.advance(100 * 86400)
    assert mgmt.retention_candidates(older_than_days=30) == []


def test_controller_runs_sweeper_on_interval(tmp_path, monkeypatch):
    clock = Clock()
    service = make_service(tmp_path, clock)
    mgmt = JobManagementService(service)
    job = service.cancel(service.create(JobSpec(title="old", repository="/repo")).id)
    mgmt.archive(job.id)
    clock.advance(45 * 86400)

    ctl = controller(tmp_path, service, retention_days=30, retention_interval_seconds=60)
    mono = [10_000.0]
    monkeypatch.setattr("mu.jobs.controller.time.monotonic", lambda: mono[0])
    ctl.tick()
    assert service.list(limit=10) == []  # swept on first eligible tick

    job2 = service.cancel(service.create(JobSpec(title="old2", repository="/repo")).id)
    mgmt.archive(job2.id)
    clock.advance(45 * 86400)
    ctl.tick()  # interval not elapsed -> not swept yet
    assert [j.id for j in service.list(limit=10)] == [job2.id]
    mono[0] += 61
    ctl.tick()
    assert service.list(limit=10) == []


def test_controller_retention_disabled_by_default(tmp_path):
    clock = Clock()
    service = make_service(tmp_path, clock)
    mgmt = JobManagementService(service)
    job = service.cancel(service.create(JobSpec(title="old", repository="/repo")).id)
    mgmt.archive(job.id)
    clock.advance(400 * 86400)
    controller(tmp_path, service).tick()
    assert service.get(job.id).status == JobStatus.CANCELLED


# ------------------------------------------------------------------ surfaces


def test_surfaces_wire_attention_endpoint_badge_and_tui_banner():
    router = (ROOT / "mu/gui/routers/jobs.py").read_text(encoding="utf-8")
    app_js = (ROOT / "mu/gui/static/js/app.js").read_text(encoding="utf-8")
    work_js = (ROOT / "mu/gui/static/js/work.js").read_text(encoding="utf-8")
    index = (ROOT / "mu/gui/templates/index.html").read_text(encoding="utf-8")
    gui_app = (ROOT / "mu/gui/app.py").read_text(encoding="utf-8")
    cli = (ROOT / "mucli.py").read_text(encoding="utf-8")

    assert '@router.get("/attention")' in router
    assert 'case "job_attention"' in app_js and "/api/jobs/attention" in app_js
    assert "job_attention" in work_js
    assert "product-attention-badge" in index and "jobAttentionCount" in index
    assert "notify=_job_attention_notify" in gui_app
    assert "MUCLI_JOBS_RETENTION_DAYS" in gui_app
    assert "attention_banner" in cli


def test_tui_attention_banner_text(tmp_path, monkeypatch):
    from mu.commands import job as job_cmd

    service = make_service(tmp_path)
    monkeypatch.setattr(job_cmd, "get_default_job_service", lambda: service)
    assert job_cmd.attention_banner() == ""
    blocked_job(service, "Fix the loader", AttentionReason.APPROVAL_REQUIRED)
    text = job_cmd.attention_banner()
    assert "1 engineering job need" in text
    assert "approval required" in text and "Fix the loader" in text


# ------------------------------------------------------------------ /job summary


def test_job_summary_digest_orders_attention_first_and_is_five_lines_per_job(tmp_path, monkeypatch):
    from mu.commands import job as job_cmd

    service = make_service(tmp_path)
    monkeypatch.setattr(job_cmd, "get_default_job_service", lambda: service)
    queued = service.create(JobSpec(title="Queued one", repository="/repo"))
    blocked = blocked_job(service, "Blocked one", AttentionReason.SECRET_REQUIRED)
    failed = service.create(JobSpec(title="Failed one", repository="/repo"))
    service.transition(failed.id, JobStatus.PREPARING)
    attempt = service.start_attempt(failed.id, worker_id="w", session_name="s")
    service.finish_attempt(attempt.id, status="failed", error="boom: provider exploded", cost_usd=0.3)
    service.transition(failed.id, JobStatus.FAILED)

    class UI:
        def __init__(self):
            self.out = []

        def show_info(self, text):
            self.out.append(text)

        def show_error(self, text):
            self.out.append("ERR " + text)

    ui = UI()
    result = job_cmd.job_cmd(SimpleNamespace(ui=ui), "summary")
    assert result.ok and result.data["attention"] == 1
    text = ui.out[0]
    header, *blocks = text.split("\n\n")
    assert "3 job(s) · 1 need you" in header
    # Attention first, then running/queued, then failed.
    assert blocks[0].startswith(blocked.id[:10]) and "NEEDS YOU · secret required" in blocks[0]
    assert blocks[0].splitlines()[-1].strip().startswith("→ Blocked one needs input")
    assert [b.split()[0] for b in blocks] == [blocked.id[:10], queued.id[:10], failed.id[:10]]
    for block in blocks:
        lines = block.splitlines()
        assert len(lines) == 5, block
        assert lines[1].lstrip().split(" · ")[1].startswith("$")
    assert "→ boom: provider exploded" in blocks[2]
    # Empty ledger.
    for job in service.list(limit=10):
        pass
    assert job_cmd.job_cmd(SimpleNamespace(ui=UI()), "summary 1").data["jobs"][0]["id"] == blocked.id
