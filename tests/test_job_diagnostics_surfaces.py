from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORK_TEMPLATE = ROOT / "mu" / "gui" / "templates" / "work.html"
WORK_DIAGNOSTICS = ROOT / "mu" / "gui" / "static" / "js" / "work_diagnostics.js"
WORK_TRACE = ROOT / "mu" / "gui" / "static" / "js" / "work_trace.js"
WORK_ANALYSIS_LINK = ROOT / "mu" / "gui" / "static" / "js" / "work_analysis_link.js"
WORK_POLISH = ROOT / "mu" / "gui" / "static" / "css" / "work_polish.css"
WORK_MANAGEMENT = ROOT / "mu" / "gui" / "static" / "js" / "work_management.js"
WORK_MANAGEMENT_CSS = ROOT / "mu" / "gui" / "static" / "css" / "work_management.css"
JOB_TRACE_HTML = ROOT / "mu" / "gui" / "static" / "job_trace.html"
JOB_TRACE_JS = ROOT / "mu" / "gui" / "static" / "js" / "job_trace.js"
JOB_TRACE_DELTA_JS = ROOT / "mu" / "gui" / "static" / "js" / "job_trace_signed_delta.js"
JOB_TRACE_CSS = ROOT / "mu" / "gui" / "static" / "css" / "job_trace.css"
JOB_ANALYSIS_ROUTER = ROOT / "mu" / "gui" / "routers" / "job_analysis.py"
JOB_PERFORMANCE = ROOT / "mu" / "jobs" / "performance.py"
JOB_ANALYSIS_DETAIL = ROOT / "mu" / "jobs" / "analysis_detail.py"
ROUTERS_INIT = ROOT / "mu" / "gui" / "routers" / "__init__.py"
JOBS_ROUTER = ROOT / "mu" / "gui" / "routers" / "jobs.py"
COMMANDS = ROOT / "mu" / "commands" / "__init__.py"
JOB_DIAGNOSTICS = ROOT / "mu" / "commands" / "job_diagnostics.py"
JOB_ANALYSIS_COMMAND = ROOT / "mu" / "commands" / "job_analysis.py"
MOBILE_API = ROOT / "mobile" / "android" / "src" / "api" / "jobs.ts"
MOBILE_DETAIL = ROOT / "mobile" / "android" / "src" / "screens" / "JobDetailScreen.tsx"
MOBILE_ANALYSIS = ROOT / "mobile" / "android" / "src" / "screens" / "JobAnalysisScreen.tsx"
MOBILE_NAV = ROOT / "mobile" / "android" / "src" / "navigation" / "AppNavigator.tsx"


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


def test_engineering_work_reuses_primary_product_chrome_and_theme_semantics():
    template = WORK_TEMPLATE.read_text(encoding="utf-8")
    script = WORK_ANALYSIS_LINK.read_text(encoding="utf-8")
    css = WORK_POLISH.read_text(encoding="utf-8")

    assert 'work-header product-header' in template
    assert 'product-icon-button work-header-icon' in template
    assert 'Toggle appearance' in template
    assert 'data-route-theme' in template
    assert 'title="Analyze jobs"' in template
    assert 'title="Model pricing"' in template
    assert 'title="Manage jobs"' in template
    assert 'id="work-refresh"' in template
    assert 'enhanceChrome' not in script
    assert 'installPolishStyles' not in script
    assert '.work-product-app::before' in css
    assert 'var(--header-bg)' in css
    assert '.work-header-icon' in css


def test_engineering_work_exposes_split_model_cost_evidence():
    script = WORK_ANALYSIS_LINK.read_text(encoding="utf-8")

    assert 'modelApi.cost_components' in script
    assert 'components.input_usd' in script
    assert 'components.output_usd' in script
    assert 'Configured provider estimate: input ~' in script
    assert 'output ~$' in script


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


def test_web_job_trace_analyzer_matches_product_chrome_and_drills_lifecycle():
    template = WORK_TEMPLATE.read_text(encoding="utf-8")
    html = JOB_TRACE_HTML.read_text(encoding="utf-8")
    script = JOB_TRACE_JS.read_text(encoding="utf-8")
    signed = JOB_TRACE_DELTA_JS.read_text(encoding="utf-8")
    css = JOB_TRACE_CSS.read_text(encoding="utf-8")
    router = JOB_ANALYSIS_ROUTER.read_text(encoding="utf-8")
    performance = JOB_PERFORMANCE.read_text(encoding="utf-8")
    detail = JOB_ANALYSIS_DETAIL.read_text(encoding="utf-8")
    routers_init = ROUTERS_INIT.read_text(encoding="utf-8")

    assert 'href="/static/job_trace.html"' in template
    assert 'Job Trace Analyzer' in html
    assert 'product-app job-trace-shell' in html
    assert 'header product-header job-trace-header' in html
    assert 'id="jt-theme"' in html
    assert 'Compare with…' in html
    assert 'Agent harness trace' in html
    assert 'state residence is separated from actual execution' in html
    assert 'id="jt-intervals"' in html
    assert 'Execution timeline' in html
    assert '/static/js/job_trace_signed_delta.js' in html
    assert '/api/jobs/${encodeURIComponent(jobId)}/analysis' in script
    assert '/api/jobs/analysis/compare?' in script
    assert 'renderRuntimeTrace' in script
    assert 'renderIntervals' in script
    assert 'stopped after errors' in script
    assert 'no activity' in script
    assert 'formatSigned' in signed
    assert 'primary - reference' in signed
    assert 'Export analysis JSON' in html
    assert '.jt-interval' in css
    assert '.jt-runtime-grid' in css
    assert '@router.get("/analysis/compare")' in router
    assert '@router.get("/{job_id}/analysis")' in router
    assert 'enrich_job_analysis' in performance
    assert 'Stopped-state residence' in detail
    assert 'No harness trace was recorded' in detail
    assert '_jobs.router.include_router(_job_analysis.router)' in routers_init


def test_tui_registers_shared_job_diagnostics_command():
    commands = COMMANDS.read_text(encoding="utf-8")
    diagnostic = JOB_DIAGNOSTICS.read_text(encoding="utf-8")

    assert 'from . import job_diagnostics' in commands
    assert '"/jobdiag"' in diagnostic
    assert '"/job-diagnostics"' in diagnostic
    assert 'build_job_diagnostics' in diagnostic
    assert 'worker_log_tail' in diagnostic
    assert 'attempted_refs' in diagnostic


def test_tui_registers_retrospective_job_trace_command():
    commands = COMMANDS.read_text(encoding="utf-8")
    analysis = JOB_ANALYSIS_COMMAND.read_text(encoding="utf-8")

    assert 'from . import job_analysis' in commands
    assert '"/jobtrace"' in analysis
    assert '"/job-analysis"' in analysis
    assert 'build_job_performance' in analysis
    assert 'Active execution' in analysis
    assert 'Stopped/error residences' in analysis
    assert 'Harness trace:' in analysis
    assert 'Top tools' in analysis


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
    assert "const underlying = String(payload.error || '');" in mobile


def test_mobile_job_review_links_to_native_job_performance_analyzer():
    nav = MOBILE_NAV.read_text(encoding="utf-8")
    screen = MOBILE_ANALYSIS.read_text(encoding="utf-8")

    assert "JobAnalysis: { jobId: string }" in nav
    assert "navigation.navigate('JobAnalysis'" in nav
    assert 'component={JobAnalysisScreen}' in nav
    assert '/api/jobs/${encodeURIComponent(jobId)}/analysis' in screen
    assert 'Job performance' in screen
    assert 'Harness trace' in screen
    assert 'State drill-down' in screen
    assert 'Passive' in screen
    assert 'Stopped' in screen
    assert 'Tool profile' in screen
    assert 'Interventions & failures' in screen
