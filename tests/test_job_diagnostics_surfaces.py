from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORK_TEMPLATE = ROOT / "mu" / "gui" / "templates" / "work.html"
WORK_DIAGNOSTICS = ROOT / "mu" / "gui" / "static" / "js" / "work_diagnostics.js"
JOBS_ROUTER = ROOT / "mu" / "gui" / "routers" / "jobs.py"
COMMANDS = ROOT / "mu" / "commands" / "__init__.py"
JOB_DIAGNOSTICS = ROOT / "mu" / "commands" / "job_diagnostics.py"
MOBILE_API = ROOT / "mobile" / "android" / "src" / "api" / "jobs.ts"
MOBILE_DETAIL = ROOT / "mobile" / "android" / "src" / "screens" / "JobDetailScreen.tsx"


def test_web_work_page_loads_shared_structured_job_diagnostics():
    template = WORK_TEMPLATE.read_text(encoding="utf-8")
    script = WORK_DIAGNOSTICS.read_text(encoding="utf-8")
    router = JOBS_ROUTER.read_text(encoding="utf-8")

    assert '/static/js/work_diagnostics.js' in template
    assert 'Job diagnostics' in script
    assert '/diagnostics?event_limit=500&log_tail_bytes=65536' in script
    assert 'worker_log_tail' in script
    assert 'worker_log_truncated' in script
    assert 'payload.stderr' in script
    assert 'payload.command' in script
    assert 'build_job_diagnostics' in router
    assert '@router.get("/{job_id}/diagnostics")' in router


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
