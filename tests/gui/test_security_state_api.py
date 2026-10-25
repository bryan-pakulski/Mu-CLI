"""Security API isolation and the same gates for web/mobile operator actions."""
import threading

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mode_helpers import agent_session
from security_helpers import finding, fixed_finding, scan_session
from mu.gui.app import session_by_name
from mu.gui.routers import security
from mu.security import engine
from mu.security.state import security_state


def client_for(sessions):
    app = FastAPI()
    app.state.sessions = sessions
    app.state.current_session_name = next(iter(sessions))
    app.state.session_locks = {name: threading.Lock() for name in sessions}
    app.state._fallback_lock = threading.Lock()
    app.state.session_by_name = lambda name=None: session_by_name(app, name)
    app.state.session_lock_for = lambda name=None: app.state.session_locks.get(name) or app.state._fallback_lock
    app.include_router(security.router, prefix="/api/security")
    return TestClient(app)


def test_security_scan_is_session_scoped_even_in_shared_workspace(tmp_path):
    alpha = scan_session(tmp_path)
    report, _ = finding(alpha)
    beta = agent_session(tmp_path, mode="security", workspace=tmp_path / "ws")
    client = client_for({"alpha": alpha, "beta": beta})
    a = client.get("/api/security/state?session_name=alpha").json()
    b = client.get("/api/security/state?session_name=beta").json()
    assert a["report"]["scan_id"] == report.scan_id and a["runner"] == "host"
    assert b["report"] is None and b["findings"] == []
    assert client.post("/api/security/findings/f001/refute?session_name=beta", json={"reason": "no"}).status_code == 409
    # A deliberate open adopts a scan; host consent does not carry across sessions.
    opened = client.post("/api/security/open?session_name=beta", json={"scan_id": report.scan_id}).json()
    assert opened["report"]["scan_id"] == report.scan_id and opened["runner"] == "docker"
    beta.variables["agent_mode"] = "default"
    assert not client.get("/api/security/state?session_name=beta").json()["active"]


def test_web_and_mobile_approval_use_integrity_gates(tmp_path):
    session = scan_session(tmp_path)
    report, f = fixed_finding(session)
    client = client_for({"s": session})
    approved = client.post(f"/api/security/findings/{f.finding_id}/approve", headers={"Authorization": "Bearer mobile"}).json()
    assert approved["ok"] and approved["findings"][0]["status"] == "approved"
    assert engine.active_report(session).events[-1]["actor"] == "user:mobile"
    assert client.post(f"/api/security/findings/{f.finding_id}/refute", json={"reason": "changed mind"}).status_code == 409
    generated = client.post("/api/security/report").json()
    assert "Approved findings" in generated["report_markdown"] and len(generated["runs"]) == 3


def test_api_plan_mode_blocks_decisions_and_runner_opt_in(tmp_path):
    session = scan_session(tmp_path)
    finding(session)
    session.variables["plan_mode"] = True
    client = client_for({"s": session})
    before = security_state(session).to_dict()
    for path, body in (("runner", {"runner": "docker"}), ("report", {}),
                       ("findings/f001/refute", {"reason": "no"}), ("findings/f001/approve", {})):
        assert client.post("/api/security/"+path, json=body).status_code == 409
    assert security_state(session).to_dict() == before
    assert client.get("/api/security/state").status_code == 200


def test_poc_run_approval_requires_desktop_but_mobile_can_cancel():
    from mu.gui.prompts import PromptStore
    from mu.gui.routers import prompts
    app = FastAPI()
    app.state.prompts = PromptStore()
    app.include_router(prompts.router, prefix="/api/prompts")
    client = TestClient(app)
    pid, event = app.state.prompts.open({"shape": "tool_approval", "requires_desktop": True})
    assert client.post(f"/api/prompts/{pid}/answer", headers={"Authorization": "Bearer mobile"}, json={"approved": True}).status_code == 403
    assert not event.is_set()
    assert client.post(f"/api/prompts/{pid}/answer", json={"approved": True}).status_code == 200
    assert event.is_set()
    pid, event = app.state.prompts.open({"shape": "tool_approval", "requires_desktop": True})
    assert client.post(f"/api/prompts/{pid}/cancel", headers={"Authorization": "Bearer mobile"}).status_code == 200
    assert event.is_set()


def test_state_remains_readable_while_turn_waits_for_approval(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    session = scan_session(tmp_path)
    finding(session)
    client = client_for({"s": session})
    lock = client.app.state._fallback_lock
    with ThreadPoolExecutor() as pool:
        lock.acquire()
        try:
            request = pool.submit(client.get, "/api/security/state")
            assert request.result(timeout=5).json()["findings"][0]["finding_id"] == "f001"
        finally:
            lock.release()


def test_report_links_read_only_the_active_scans_generated_artifacts(tmp_path):
    session = scan_session(tmp_path)
    report, f = fixed_finding(session)
    engine.generate_report(report)
    other = agent_session(tmp_path / "other", mode="security")
    client = client_for({"s": session, "other": other})
    item = client.get(f"/api/security/artifacts/findings/{f.finding_id}.md?session_name=s").json()
    assert item["format"] == "markdown" and "fabricated data exposed" in item["text"]
    assert client.get(f"/api/security/artifacts/findings/{f.finding_id}.md?session_name=other").status_code == 404
    assert client.get("/api/security/artifacts/report.json?session_name=s").status_code == 404


def test_reopened_scan_reads_its_own_output_despite_session_run_id_collision(tmp_path):
    from mu.modes.evidence import CheckSpec, run_check
    session = scan_session(tmp_path)
    report, f = fixed_finding(session)
    other = agent_session(tmp_path, mode="security", workspace=tmp_path / "ws")
    engine.open_report(other, report.scan_id)
    run_check(CheckSpec(command="echo unrelated-output"), other)
    client = client_for({"other": other})
    payload = client.get("/api/security/state").json()
    proof_run = payload["runs"][0]
    assert proof_run["id"] != "r1" and proof_run["check_id"] == "r1"
    output = client.get('/api/security/artifacts/' + proof_run["security_output_path"]).json()["text"]
    assert "EXPLOIT" in output and "unrelated-output" not in output
