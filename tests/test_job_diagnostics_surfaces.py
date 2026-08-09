from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORK_TEMPLATE = ROOT / "mu" / "gui" / "templates" / "work.html"
WORK_DIAGNOSTICS = ROOT / "mu" / "gui" / "static" / "js" / "work_diagnostics.js"
COMMANDS = ROOT / "mu" / "commands" / "__init__.py"
JOB_DIAGNOSTICS = ROOT / "mu" / "commands" / "job_diagnostics.py"
MOBILE_DETAIL = ROOT / "mobile" / "android" / "src" / "screens" / "JobDetailScreen.tsx"


def test_web_work_page_loads_structured_job_diagnostics():
    template = WORK_TEMPLATE.read_text(encoding="utf-8")
    script = WORK_DIAGNOSTICS.read_text(encoding="utf-8")

    assert '/static/js/work_diagnostics.js' in template
    assert 'Job diagnostics' in script
    assert 'worktree_prepare_failed' in script
    assert 'job_base_resolved' in script
    assert 'repository_inspected' in script
    assert 'payload.stderr' in script
    assert 'payload.command' in script
    assert 'payload.log_path' in script


def test_tui_registers_job_diagnostics_command():
    commands = COMMANDS.read_text(encoding="utf-8")
    diagnostic = JOB_DIAGNOSTICS.read_text(encoding="utf-8")

    assert 'from . import job_diagnostics' in commands
    assert '"/jobdiag"' in diagnostic
    assert '"/job-diagnostics"' in diagnostic
    assert 'worktree_prepare_failed' in diagnostic
    assert 'attempted_refs' in diagnostic
    assert 'log_path' in diagnostic


def test_mobile_activity_can_surface_worktree_failure_reason():
    mobile = MOBILE_DETAIL.read_text(encoding="utf-8")
    # Non-status events fall through to event.reason; the durable worktree
    # manager emits `worktree_prepare_failed` with the exact failure as reason.
    assert 'if (event.reason) return event.reason;' in mobile
    assert 'events.slice(-35).reverse()' in mobile
