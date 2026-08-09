from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORK_TEMPLATE = ROOT / "mu" / "gui" / "templates" / "work.html"
MANAGEMENT_JS = ROOT / "mu" / "gui" / "static" / "js" / "work_management.js"
TRACE_JS = ROOT / "mu" / "gui" / "static" / "js" / "work_trace.js"
DIAGNOSTICS_JS = ROOT / "mu" / "gui" / "static" / "js" / "work_diagnostics.js"
MANAGEMENT_CSS = ROOT / "mu" / "gui" / "static" / "css" / "work_management.css"
ROUTER = ROOT / "mu" / "gui" / "routers" / "jobs.py"


def test_engineering_work_loads_management_and_trace_assets():
    template = WORK_TEMPLATE.read_text(encoding="utf-8")

    assert 'id="work-manage"' in template
    assert 'id="work-management-backdrop"' in template
    assert '/static/css/work_management.css' in template
    assert '/static/js/work_trace.js' in template
    assert '/static/js/work_management.js' in template
    assert template.index('/static/js/work_trace.js') < template.index('/static/js/work_diagnostics.js')


def test_job_management_surface_queries_reports_archives_and_exports():
    js = MANAGEMENT_JS.read_text(encoding="utf-8")
    router = ROUTER.read_text(encoding="utf-8")

    assert '/api/jobs/history?' in js
    assert '/api/jobs/report?' in js
    assert '/api/jobs/report/export?' in js
    assert '/api/jobs/manage/bulk' in js
    assert '/debug-export' in js
    assert 'data-manage-action="archive"' in js
    assert 'data-manage-action="restore"' in js
    assert 'data-manage-action="delete"' in js

    assert '@router.get("/history")' in router
    assert '@router.get("/report")' in router
    assert '@router.get("/report/export")' in router
    assert '@router.post("/manage/bulk")' in router
    assert '@router.post("/{job_id}/archive")' in router
    assert '@router.post("/{job_id}/restore")' in router
    assert '@router.delete("/{job_id}")' in router
    assert '@router.get("/{job_id}/debug-export")' in router


def test_activity_and_diagnostics_are_filterable_drill_down_traces():
    trace = TRACE_JS.read_text(encoding="utf-8")
    diagnostics = DIAGNOSTICS_JS.read_text(encoding="utf-8")
    css = MANAGEMENT_CSS.read_text(encoding="utf-8")

    assert 'Filter trace…' in trace
    assert 'Signal only' in trace
    assert 'Errors only' in trace
    assert 'Copy JSON' in trace
    assert 'work-trace-event' in trace
    assert 'data-activity-trace' in trace
    assert 'trace.mount' in diagnostics
    assert 'Export debug bundle' in diagnostics
    assert 'Worker log' in diagnostics
    assert '.work-trace-toolbar' in css
    assert '.work-diagnostic-failure' in css
