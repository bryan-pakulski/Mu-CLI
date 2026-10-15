"""Browser round trip against the real core API (no provider or controller)."""
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mu.gui.routers.jobs_v2 import router
from mu.jobs import JobService, JobStore
from mu.jobs.core.changes import ChangeBus, collect_durable_changes
from mu.jobs.core.plan_ops import record_planner_plan
from mu.jobs.models import AttentionReason, JobStatus

ROOT = Path(__file__).resolve().parents[1]


def fixture_job(service, tmp_path):
    job = service.create_from_payload({"title": "Browser ticket", "workspace_root": str(tmp_path),
                                       "execution": {"provider": "ollama", "model": "small"}})
    service.transition(job.id, JobStatus.PREPARING)
    service.transition(job.id, JobStatus.PLANNING)
    record_planner_plan(service.get(job.id), {"summary": "Original", "steps": ["Edit widget"],
                        "acceptance_criteria": ["Widget works"], "validation_commands": ["pytest -q"]})
    service.require_human(job.id, AttentionReason.PLAN_REVIEW, "Review plan")
    return job


def test_subprocess_events_reach_partial_change_bus(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    service = JobService(JobStore(str(tmp_path / "jobs.db")))
    job = fixture_job(service, tmp_path)
    cursor = service.store.event_cursor()
    other_store = JobStore(str(tmp_path / "jobs.db"))
    other_store.append_event(job.id, "sandbox_event", payload={"text": "working"})
    bus = ChangeBus(window_s=0)
    assert collect_durable_changes(service, bus, cursor) > cursor
    changes = bus.flush()
    parts = next(c.parts for c in changes if c.scope == "job")
    assert set(parts) == {"session", "costs"}


def test_browser_plan_edit_save_approve_and_partial_refresh(tmp_path, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    service = JobService(JobStore(str(tmp_path / "jobs.db")))
    job = fixture_job(service, tmp_path)
    app = FastAPI()
    app.state.job_service = service
    app.state.job_change_bus = ChangeBus(window_s=0)
    app.include_router(router, prefix="/api/jobs")
    with playwright.sync_playwright() as pw, TestClient(app) as client:
        if not Path(pw.chromium.executable_path).exists():
            pytest.skip("Playwright Chromium is not installed")
        browser = pw.chromium.launch()
        page = browser.new_page()
        requests = []

        def route(request):
            path = request.request.url.split("http://jobs.test", 1)[-1]
            if path == "/":
                request.fulfill(body='<div id="total"></div><div id="board"></div><div id="detail"></div>', content_type="text/html")
                return
            requests.append(path)
            response = client.request(request.request.method, path,
                                      content=request.request.post_data,
                                      headers={"content-type": "application/json"})
            request.fulfill(status=response.status_code, body=response.content,
                            content_type="application/json")

        page.route("http://jobs.test/**", route)
        page.goto("http://jobs.test/")
        page.evaluate("() => { window.EventSource = class { constructor() { window.stream=this; } addEventListener() {} close() {} }; }")
        # Wash W4: view.js reads the shared status vocabulary (wash_tokens.js,
        # loaded by _layout.html on every page) and renders diffs with
        # wash/work_diff.js, so the harness loads them first like work.html.
        for script in ("wash_tokens.js", "wash/work_diff.js", "work/plan_editor.js", "work/view.js"):
            page.add_script_tag(path=str(ROOT / f"mu/gui/static/js/{script}"))
        page.evaluate("""async () => {
            window.work = MuJobWork.create({
              api: async (path, opts={}) => { const r=await fetch(path, {method:opts.method, headers:{'content-type':'application/json'}, body:opts.body ? JSON.stringify(opts.body):undefined}); const data=await r.json(); if(!r.ok) throw Error(data.detail); return data; },
              toast: text => window.lastToast=text,
              boardEl:document.querySelector('#board'), detailEl:document.querySelector('#detail'), totalEl:document.querySelector('#total')
            }); await work.start();
        }""")
        # planos flow: the plan renders read-only; "Edit plan" opens the editor.
        page.locator('[data-plan-edit]').click()
        page.locator('[data-mode="structured"]').click()
        page.locator('[data-summary]').fill("Human approved content")
        assert page.locator('[data-plan-approve]').is_disabled()
        page.evaluate("stream.onmessage({data:JSON.stringify({scope:'job',id:work.selectedId(),parts:['session','costs']})})")
        assert page.locator('[data-summary]').input_value() == "Human approved content"
        assert any("parts=session%2Ccosts" in p for p in requests)
        page.locator('[data-plan-save]').click()
        page.wait_for_function("!document.querySelector('[data-plan-approve]').disabled")
        vm = client.get(f"/api/jobs/vm/job/{job.id}").json()
        assert vm["plan"]["version"] == 2
        stale = client.post("/api/jobs/cmd", json={"type":"approvePlan", "job_id":job.id, "args":{"version":1}})
        assert stale.status_code == 409
        page.locator('[data-plan-approve]').click()
        # Read-only after approval: no editor and no "Edit plan" entry point.
        page.wait_for_function("!document.querySelector('[data-plan-save]') && !document.querySelector('[data-plan-edit]')")
        updated = service.get(job.id)
        assert updated.metadata["plan_approved_version"] == 2
        from mu.jobs.core.plan_ops import approved_plan_text
        assert "Human approved content" in approved_plan_text(updated)
        browser.close()
