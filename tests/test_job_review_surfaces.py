from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

APP = ROOT / "mu" / "gui" / "app.py"
INDEX = ROOT / "mu" / "gui" / "templates" / "index.html"
WORK_TEMPLATE = ROOT / "mu" / "gui" / "templates" / "work.html"
WORK_CSS = ROOT / "mu" / "gui" / "static" / "css" / "work.css"
WORK_JS = ROOT / "mu" / "gui" / "static" / "js" / "work.js"
JOBS_ROUTER = ROOT / "mu" / "gui" / "routers" / "jobs.py"
TUI_JOB = ROOT / "mu" / "commands" / "job.py"
REVIEW = ROOT / "mu" / "jobs" / "review.py"
JOB_UI = ROOT / "mu" / "jobs" / "ui.py"
MOBILE_API = ROOT / "mobile" / "android" / "src" / "api" / "jobs.ts"
MOBILE_NAV = ROOT / "mobile" / "android" / "src" / "navigation" / "AppNavigator.tsx"
MOBILE_HEADER = ROOT / "mobile" / "android" / "src" / "components" / "ModernHeader.tsx"
MOBILE_WORK = ROOT / "mobile" / "android" / "src" / "screens" / "WorkScreen.tsx"
MOBILE_DETAIL = ROOT / "mobile" / "android" / "src" / "screens" / "JobDetailScreen.tsx"


def test_gui_exposes_first_class_engineering_work_surface():
    app = APP.read_text(encoding="utf-8")
    index = INDEX.read_text(encoding="utf-8")
    template = WORK_TEMPLATE.read_text(encoding="utf-8")
    css = WORK_CSS.read_text(encoding="utf-8")
    script = WORK_JS.read_text(encoding="utf-8")

    assert '@app.get("/work"' in app
    assert "work.html" in app
    assert "product-work-button" in index
    assert "'/work?session='" in index
    assert 'aria-label="Open engineering work"' in index

    assert '/static/css/work.css' in template
    assert '/static/js/work.js' in template
    assert 'id="work-board"' in template
    assert 'id="work-detail"' in template
    assert 'id="work-create-form"' in template

    assert ".work-layout" in css
    assert ".work-job-card" in css
    assert ".work-action-panel" in css
    assert "clip-path" not in css

    assert "'/api/jobs/board'" in script
    assert "/receipt" in script
    assert "/diff" in script
    assert "/respond" in script
    assert "/request-changes" in script
    assert "/continue" in script
    assert "/discard" in script
    assert "READY TO REVIEW" not in script  # readiness comes from job state/evidence, not UI invention


def test_shared_job_api_exposes_board_evidence_and_review_actions():
    router = JOBS_ROUTER.read_text(encoding="utf-8")
    review = REVIEW.read_text(encoding="utf-8")

    for route in (
        '@router.get("/board")',
        '@router.get("/{job_id}/receipt")',
        '@router.get("/{job_id}/diff")',
        '@router.post("/{job_id}/respond")',
        '@router.post("/{job_id}/request-changes")',
        '@router.post("/{job_id}/continue")',
        '@router.post("/{job_id}/discard")',
    ):
        assert route in router

    assert "build_job_board" in router
    assert "JobReceiptBuilder" in router
    assert "build_job_diff" in router
    assert "JobReviewService" in router
    assert "review changes requested; requeued" in review
    assert '"verification_contract_updated"' in review
    assert "JobStatus.READY_FOR_REVIEW" in review
    assert "JobStatus.QUEUED" in review


def test_durable_human_responses_are_consumed_once_by_workers():
    ui = JOB_UI.read_text(encoding="utf-8")
    review = REVIEW.read_text(encoding="utf-8")

    assert 'event.event_type == "interaction_response"' in ui
    assert '"interaction_response_consumed"' in ui
    assert "_take_response" in ui
    assert 'decision == "approve"' in ui
    assert 'return "y", None' in ui
    assert '"interaction_response"' in review


def test_tui_covers_board_receipt_diff_attention_and_review_loop():
    tui = TUI_JOB.read_text(encoding="utf-8")

    assert "build_job_board" in tui
    assert "BOARD_ORDER" in tui
    assert "_show_receipt" in tui
    assert "_show_diff" in tui
    assert "_respond_job" in tui
    for command in ('"board"', '"receipt"', '"diff"', '"respond"', '"changes"', '"continue"', '"discard"'):
        assert command in tui
    assert "ensure_controller_daemon" in tui


def test_mobile_has_first_class_work_board_and_review_detail():
    api = MOBILE_API.read_text(encoding="utf-8")
    nav = MOBILE_NAV.read_text(encoding="utf-8")
    header = MOBILE_HEADER.read_text(encoding="utf-8")
    work = MOBILE_WORK.read_text(encoding="utf-8")
    detail = MOBILE_DETAIL.read_text(encoding="utf-8")

    assert "Work: undefined" in nav
    assert "JobDetail: { jobId: string }" in nav
    assert 'name="Work"' in nav
    assert 'name="JobDetail"' in nav
    assert "onOpenWork" in nav

    assert "onOpenWork: () => void" in header
    assert 'accessibilityLabel="Open engineering work"' in header
    assert 'name="briefcase-outline"' in header
    assert 'label="Engineering work"' in header

    assert "jobsApi.board()" in work
    assert "jobsApi.create(input)" in work
    assert "session_name" in work
    assert "BOARD_ORDER" in work

    for action in ("jobsApi.receipt", "jobsApi.diff", "jobsApi.respond", "jobsApi.requestChanges", "jobsApi.continue", "jobsApi.discard"):
        assert action in detail
    assert "verification_required" in detail
    assert "Ready for review" in detail

    for method in ("board:", "receipt:", "diff:", "respond:", "requestChanges:", "continue:", "discard:"):
        assert method in api
    assert "verification_required" in api


def test_mobile_work_screens_only_use_published_theme_tokens():
    work = MOBILE_WORK.read_text(encoding="utf-8")
    detail = MOBILE_DETAIL.read_text(encoding="utf-8")

    assert "accentLine" not in work
    assert "fontWeight: '650'" not in work
    assert "fontWeight: '650'" not in detail
