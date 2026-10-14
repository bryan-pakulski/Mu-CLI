"""Phase 3 exit tests: commands, view models, change stream, jobs_v2 API, import guard."""

from __future__ import annotations

import ast
import json
import pathlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mu.gui.routers import jobs_v2
from mu.jobs import JobService, JobStore
from mu.jobs.core import vm
from mu.jobs.core.changes import ChangeBus
from mu.jobs.core.commands import CommandRejected, dispatch
from mu.jobs.models import AttentionReason, JobStatus

ROOT = pathlib.Path(__file__).resolve().parents[1]

PLAN = {
    "summary": "Add the widget",
    "steps": ["edit widget.py"],
    "acceptance_criteria": ["widget renders"],
    "validation_commands": ["pytest -q"],
}


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))


def _service(tmp_path):
    return JobService(JobStore(str(tmp_path / "jobs.sqlite3")))


def _job(service, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    return service.create_from_payload({
        "title": "Ticket", "workspace_root": str(ws),
        "execution": {"provider": "ollama", "model": "small", "session_type": "workspace"},
    })


def _at_plan_review(service, tmp_path):
    from mu.jobs.core.plan_ops import record_planner_plan
    from mu.jobs.stages import merge_metadata

    job = _job(service, tmp_path)
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.PLANNING)
    merge_metadata(service, job.id, plan=PLAN)
    record_planner_plan(service.get(job.id), PLAN)
    service.require_human(job.id, AttentionReason.PLAN_REVIEW, "review the plan")
    return service.get(job.id)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


# ------------------------------------------------------------------ change bus
def test_change_bus_coalesces_within_window():
    clock = FakeClock()
    bus = ChangeBus(window_s=0.150, clock=clock)
    bus.emit("job", "j1", ["state"])
    bus.emit("job", "j1", ["pipeline"])
    bus.emit("job", "j1", ["state", "plan"])
    clock.t = 0.100
    assert bus.flush() == []  # still inside the window
    clock.t = 0.151
    out = bus.flush()
    assert len(out) == 1
    assert out[0].scope == "job" and out[0].id == "j1"
    assert sorted(out[0].parts) == ["pipeline", "plan", "state"]


def test_change_bus_rejects_unknown_scope_and_drops_unknown_parts():
    bus = ChangeBus(window_s=0.0)
    with pytest.raises(ValueError):
        bus.emit("everything", "x", ["state"])
    bus.emit("job", "j", ["not-a-part"])
    assert bus.flush(force=True) == []


def test_burst_of_changes_never_requests_full_reload():
    """The reload-loop fix: N rapid writes become one partial refetch hint."""
    clock = FakeClock()
    bus = ChangeBus(window_s=0.150, clock=clock)
    for _ in range(50):
        bus.emit_job("j1", ["costs"])
    clock.t = 1.0
    changes = bus.flush()
    job_changes = [c for c in changes if c.scope == "job"]
    assert len(job_changes) == 1
    assert job_changes[0].parts == ["costs"]  # not the full PARTS set
    assert all(set(c.parts) != {"state", "pipeline", "plan", "diff", "review",
                                "checks", "session", "costs"} for c in changes)


# ------------------------------------------------------------------ vm
def test_job_vm_contract_shape(tmp_path):
    service = _service(tmp_path)
    job = _at_plan_review(service, tmp_path)
    data = vm.job_vm(service, job.id)
    for key in ("id", "state", "pipeline", "actions", "plan"):
        assert key in data, key
    assert data["state"] == "plan_review"
    assert [s["id"] for s in data["pipeline"]] == ["plan", "approve", "implement", "checks", "review", "push"]
    actions = {a["id"]: a for a in data["actions"]}
    assert actions["approvePlan"]["enabled"] is True
    assert actions["push"]["enabled"] is False and actions["push"]["reason"]
    json.dumps(data)  # JSON-serialisable


def test_job_vm_parts_subset(tmp_path):
    service = _service(tmp_path)
    job = _at_plan_review(service, tmp_path)
    data = vm.job_vm(service, job.id, parts=["plan"])
    assert "plan" in data and "state" in data
    assert "review" not in data


def test_home_vm_needs_you(tmp_path):
    service = _service(tmp_path)
    job = _at_plan_review(service, tmp_path)
    home = vm.home_vm(service)
    assert [r["id"] for r in home["needs_you"]] == [job.id]
    assert home["needs_you"][0]["primary_action"] == "approvePlan"


# ------------------------------------------------------------------ commands
def test_disabled_command_rejected_with_reason(tmp_path):
    service = _service(tmp_path)
    job = _job(service, tmp_path)
    with pytest.raises(CommandRejected) as exc:
        dispatch(service, {"type": "push", "job_id": job.id}, bus=ChangeBus())
    assert str(exc.value)


def test_approve_plan_command_emits_change_and_audits(tmp_path):
    service = _service(tmp_path)
    job = _at_plan_review(service, tmp_path)
    bus = ChangeBus(window_s=0.0)
    version = vm.job_vm(service, job.id)["plan"]["version"]
    result = dispatch(service, {"type": "approvePlan", "job_id": job.id,
                                "args": {"version": version}}, bus=bus)
    assert result.ok
    assert service.get(job.id).metadata["plan_approved_version"] == version
    assert any(c.scope == "job" and c.id == job.id for c in bus.flush(force=True))
    assert any(e.event_type == "command" for e in service.events(job.id))


# ------------------------------------------------------------------ HTTP
@pytest.fixture
def client(tmp_path):
    service = _service(tmp_path)
    app = FastAPI()
    app.state.job_service = service
    app.state.job_change_bus = ChangeBus(window_s=0.0)
    app.include_router(jobs_v2.router, prefix="/api/jobs")
    with TestClient(app) as test_client:
        test_client.service = service
        yield test_client


def test_http_cmd_409_on_disabled(client, tmp_path):
    job = _job(client.service, tmp_path)
    resp = client.post("/api/jobs/cmd", json={"type": "push", "job_id": job.id})
    assert resp.status_code == 409
    assert resp.json()["detail"]


def test_http_stale_plan_version_409(client, tmp_path):
    job = _at_plan_review(client.service, tmp_path)
    resp = client.post("/api/jobs/cmd", json={"type": "approvePlan", "job_id": job.id,
                                             "args": {"version": 99}})
    assert resp.status_code == 409


def test_http_cmd_400_and_404(client):
    assert client.post("/api/jobs/cmd", json={"job_id": "x"}).status_code == 400
    assert client.post("/api/jobs/cmd", json={"type": "retry", "job_id": "nope"}).status_code == 404


def test_http_vm_endpoints(client, tmp_path):
    job = _at_plan_review(client.service, tmp_path)
    assert client.get("/api/jobs/vm/home").status_code == 200
    assert client.get("/api/jobs/vm/jobs").status_code == 200
    resp = client.get(f"/api/jobs/vm/job/{job.id}", params={"parts": "plan"})
    assert resp.status_code == 200 and "plan" in resp.json()
    assert client.get("/api/jobs/vm/job/missing").status_code == 404


def test_http_events_stream_sends_partial_changes(client, tmp_path):
    job = _at_plan_review(client.service, tmp_path)
    bus = client.app.state.job_change_bus
    bus.emit_job(job.id, ["plan"])
    resp = client.get("/api/jobs/events", params={"after": 0, "once": True})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    frames = [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: {\"scope")]
    assert any(f["scope"] == "job" and f["id"] == job.id and f["parts"] == ["plan"] for f in frames)


# ------------------------------------------------------------------ import guard
ALLOWED_CORE = {"mu.jobs.core.vm", "mu.jobs.core.commands", "mu.jobs.core.changes",
                "mu.jobs.core.actions", "mu.jobs.core"}


def _job_imports(path: pathlib.Path):
    tree = ast.parse(path.read_text())
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mod = node.module
            if mod == "mu.jobs.core":
                out += [f"mu.jobs.core.{a.name}" for a in node.names]
            else:
                out.append(mod)
        elif isinstance(node, ast.Import):
            out += [a.name for a in node.names]
    return [m for m in out if m == "mu.jobs" or m.startswith("mu.jobs.")]


def test_jobs_v2_router_imports_only_core_ui_modules():
    bad = [m for m in _job_imports(ROOT / "mu/gui/routers/jobs_v2.py") if m not in ALLOWED_CORE]
    assert bad == [], f"jobs_v2 must only import core vm/commands/changes/actions, got {bad}"


def test_tui_imports_only_core_ui_modules():
    bad = [m for m in _job_imports(ROOT / "mu/commands/job.py") if m not in ALLOWED_CORE]
    assert bad == [], bad


def test_tui_show_and_approve_use_shared_actions(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from mu.commands import job as terminal
    service = _service(tmp_path)
    job = _at_plan_review(service, tmp_path)
    monkeypatch.setattr(terminal, "get_default_job_service", lambda: service)
    monkeypatch.setattr(terminal, "ensure_controller_daemon", lambda: None)
    shown = terminal.job_cmd(SimpleNamespace(ui=None), f"show {job.id}", allow_prompt=False)
    assert shown.data["state"] == "plan_review"
    assert len(shown.data["pipeline"]) == 6
    assert next(a for a in shown.data["actions"] if a["id"] == "approvePlan")["enabled"]
    version = shown.data["plan"]["version"]
    result = terminal.job_cmd(SimpleNamespace(ui=None), f"approve {job.id} {version}", allow_prompt=False)
    assert result.ok
    assert service.get(job.id).metadata["plan_approved_version"] == version
