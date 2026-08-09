from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORK_TEMPLATE = ROOT / "mu" / "gui" / "templates" / "work.html"
WORK_DIAGNOSTICS = ROOT / "mu" / "gui" / "static" / "js" / "work_diagnostics.js"
WORK_TRACE = ROOT / "mu" / "gui" / "static" / "js" / "work_trace.js"
WORK_MANAGEMENT = ROOT / "mu" / "gui" / "static" / "js" / "work_management.js"
WORK_MANAGEMENT_CSS = ROOT / "mu" / "gui" / "static" / "css" / "work_management.css"
JOBS_ROUTER = ROOT / "mu" / "gui" / "routers" / "jobs.py"
COMMANDS = ROOT / "mu" / "commands" / "__init__.py"
JOB_DIAGNOSTICS = ROOT / "mu" / "commands" / "job_diagnostics.py"
MOBILE_API = ROOT / "mobile" / "android" / "src" / "api" / "jobs.ts"
MOBILE_DETAIL = ROOT / "mobile" / "android" / "src" / "screens" / "JobDetailScreen.tsx"


def test_web_work_page_loads_shared_structured_job_diagnostics():
    template = WORK_TEMPLATE.read_text(encoding="utf-8")
    script = WORK_DIAGNOSTICS.read_text(encoding="utf-8")
    router = JOBS_ROUTER.read_text(encoding="utf-8")

    assert '/static/js/work_trace.js' in template
    assert '/static/js/work_diagnostics.js' in template
    assert template.index('/static/js/work_trace.js') < template.index('/static/js/work_diagnostics.js')
    assert 'Job diagnostics' in script
    assert '/diagnostics?event_limit=700&log_tail_bytes=65536' in script
    assert 'worker_log_tail' in script
    assert 'worker_log_truncated' in script
    assert 'trace.mount' in script
    assert 'Export debug bundle' in script
    assert 'build_job_diagnostics' in router
    assert '@router.get("/{job_id}/diagnostics")' in router
    assert '@router.get("/{job_id}/debug-export")' in router


def test_web_activity_trace_is_filterable_and_drillable():
    script = WORK_TRACE.read_text(encoding="utf-8")
    css = WORK_MANAGEMENT_CSS.read_text(encoding="utf-8")

    assert 'Filter trace…' in script
    assert 'Signal only' in script
    assert 'Errors only' in script
    assert 'Copy JSON' in script
    assert 'data-activity-trace' in script
    assert 'work-trace-event' in script
    assert '.work-trace-toolbar' in css
    assert '.work-diagnostic-failure' in css


def test_web_job_management_queries_archives_reports_and_exports():
    template = WORK_TEMPLATE.read_text(encoding="utf-8")
    script = WORK_MANAGEMENT.read_text(encoding="utf-8")
    router = JOBS_ROUTER.read_text(encoding="utf-8")

    assert 'id="work-manage"' in template
    assert 'id="work-management-backdrop"' in template
    assert '/static/css/work_management.css' in template
    assert '/static/js/work_management.js' in template
    assert '/api/jobs/history?' in script
    assert '/api/jobs/report?' in script
    assert '/api/jobs/report/export?' in script
    assert '/api/jobs/manage/bulk' in script
    assert '/debug-export' in script
    assert 'data-manage-action="archive"' in script
    assert 'data-manage-action="restore"' in script
    assert 'data-manage-action="delete"' in script
    assert '@router.get("/history")' in router
    assert '@router.get("/report")' in router
    assert '@router.get("/report/export")' in router
    assert '@router.post("/manage/bulk")' in router
    assert '@router.post("/{job_id}/archive")' in router
    assert '@router.post("/{job_id}/restore")' in router
    assert '@router.delete("/{job_id}")' in router


def test_tui_registers_shared_job_diagnostics_command():
    commands = COMMANDS.read_text(encoding="utf-8")
    diagnostic = JOB_DIAGNOSTICS.read_text(encoding="utf-8")

    assert 'from . import job_diagnostics' in commands
    assert '"/jobdiag"' in diagnostic
    assert '"/job-diagnostics"' in diagnostic
    assert 'build_job_diagnostics' in diagnostic
    assert 'worker_log_tail' in diagnostic
    assert 'attempted_refs' in diagnostic


def test_mobile_exposes_shared_diagnostics_and_worker_log_tail():
    api = MOBILE_API.read_text(encoding="utf-8")
    mobile = MOBILE_DETAIL.read_text(encoding="utf-8")

    assert 'export interface JobDiagnosticsSnapshot' in api
    assert 'diagnostics: (jobId: string)' in api
    assert '/diagnostics`' in api
    assert 'type JobDiagnosticsSnapshot' in mobile
    assert 'jobsApi.diagnostics(jobId)' in mobile
    assert '<DetailSection title="Job diagnostics">' in mobile
    assert 'diagnostics.worker_log_tail' in mobile
    # Old status events also reveal their underlying payload.error now.
    assert "const underlying = String(payload.error || '');" in mobile
