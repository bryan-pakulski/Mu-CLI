"""Web jobs mode reaches planos parity (CONTRACT §1-§7): activity page, push dialog, deny reason,
checks history, server-side viewed marks and generic action fields."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "mu" / "gui" / "static"
HTML = (ROOT / "mu" / "gui" / "templates" / "work.html").read_text(encoding="utf-8")
VIEW = (STATIC / "js" / "work" / "view.js").read_text(encoding="utf-8")
ACTIVITY = (STATIC / "js" / "work" / "activity.js").read_text(encoding="utf-8")
SHELL = (STATIC / "js" / "work.js").read_text(encoding="utf-8")
UI = (STATIC / "js" / "work" / "ui.js").read_text(encoding="utf-8")
CSS = (STATIC / "css" / "wash" / "patterns" / "work.css").read_text(encoding="utf-8")  # Wash W4: /work rules live in its page bundle


def test_activity_page_route_nav_and_assets():
    assert 'data-route="activity"' in HTML and 'id="work-activity"' in HTML
    # Wash W4: the Work sub-pages are .w-nav__item links in the shared sidebar.
    assert 'href="#/activity" class="w-nav__item work-navlink" data-nav="activity"' in HTML
    assert HTML.index('/static/js/work/view.js') < HTML.index('/static/js/work/activity.js') < HTML.index('/static/js/work.js')
    assert "key: 'g a'" in SHELL and "['g a', 'activity']" in UI


def test_activity_polls_keeps_samples_and_uses_contract_endpoints():
    assert "'/api/jobs/vm/activity'" in ACTIVITY
    assert "/api/jobs/vm/activity/logs/" in ACTIVITY and "tail_bytes=65536" in ACTIVITY
    assert "/api/jobs/activity/containers/" in ACTIVITY and "/remove" in ACTIVITY
    assert "POLL_MS = 5000" in ACTIVITY and "SAMPLES = 60" in ACTIVITY
    assert "document.visibilityState === 'visible'" in ACTIVITY
    # Removal is confirmed (danger) and blocked for containers of active jobs.
    assert "tone: 'danger'" in ACTIVITY and "Remove container" in ACTIVITY
    assert "ACTIVE.has(job.state)" in ACTIVITY
    # Orphans + engine status + hand-written sparklines with an accessible summary.
    assert "Orphan containers" in ACTIVITY and "docker down" in ACTIVITY
    assert 'class="work-spark' in ACTIVITY and 'role="img"' in ACTIVITY
    assert ".work-spark .line" in CSS


def test_header_status_chips_follow_planos():
    for name in ("agents", "checks", "needs", "engine", "live"):
        assert f'data-chip="{name}"' in HTML
    assert "'/api/jobs/vm/status'" in SHELL
    assert "setInterval(() => { if (document.visibilityState === 'visible') refreshStatus(); }, 10000)" in SHELL
    assert "`docker ${engine}`" in SHELL


def test_deny_tool_carries_an_optional_reason():
    assert "data-deny-reason" in VIEW and 'maxlength="2000"' in VIEW
    assert "denyTool:'message'" in VIEW  # input text → args.message


def test_push_opens_the_push_dialog():
    assert "/push-preview" in VIEW
    assert "dialogOf(action) === 'push'" in VIEW
    for arg in ("pr_title:", "pr_body:", "draft:", "args.override_reason"):
        assert arg in VIEW
    assert 'maxlength="200"' in VIEW and 'maxlength="20000"' in VIEW
    assert "data-push-view=\"preview\"" in VIEW
    assert "A reason is required to override the review." in VIEW


def test_checks_tab_matches_planos_checks_tab():
    for header in ('<th scope="col">Trigger</th>', '<th scope="col" class="num">Duration</th>', '<th scope="col">Started</th>'):
        assert header in VIEW
    assert "job.checks?.failed_cases" in VIEW and "job.checks?.history" in VIEW
    assert "Failed test cases" in VIEW and "data-failure-filter" in VIEW
    assert "Duration history" in VIEW and "bar-ok" in VIEW and "bar-danger" in VIEW
    assert ".work-chart .bar-ok" in CSS


def test_diff_viewed_marks_are_server_side():
    assert "send('markViewed', {path, viewed:on})" in VIEW
    assert "job?.diff?.viewed" in VIEW
    assert "mucli-job-viewed" not in VIEW and "localStorage.setItem(viewedKey()" not in VIEW


def test_action_runner_uses_server_action_fields_with_fallbacks():
    for field in ("a.tone", "a.confirm", "a.input", "a.dialog", "a.hidden"):
        assert field in VIEW
    assert "FALLBACK_CONFIRM" in VIEW and "FALLBACK_INPUT" in VIEW
    assert "HIDDEN = new Set(['markFindings', 'markViewed'])" in VIEW
    assert "!hiddenOf(a)" in VIEW
