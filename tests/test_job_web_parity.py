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
    # Mu §4.3: Activity is a page under the header's More tab (no second sidebar level).
    assert 'href="#/activity" class="w-menu__item work-navlink" role="menuitem" data-nav="activity"' in HTML
    assert 'data-nav-group="activity settings setup"' in HTML
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


def test_header_status_sentence_follows_planos():
    # Mu §4.2 / §6.1: the status chips became the page header's state sentence (needs you ·
    # running · checks · spend today, zeros left out); Docker and the live connection are
    # silent while fine and show as the leaf alert only when broken.
    assert '{% block state_attrs %} id="work-state"' in HTML
    for part in ("need${st.needs_you === 1 ? 's' : ''} you", "`${st.agents} running`", "plural(st.checks, 'check')", "today`"):
        assert part in SHELL, part
    assert "if (st.needs_you)" in SHELL and "if (st.agents)" in SHELL and "if (st.checks)" in SHELL
    for name in ("engine", "live"):
        assert f'data-alert="{name}" hidden' in HTML
    assert "alert.hidden = engine !== 'down'" in SHELL
    assert "offlineTimer = setTimeout(() => { el.hidden = false; }, 3000)" in SHELL
    assert "'/api/jobs/vm/status'" in SHELL
    assert "setInterval(() => { if (document.visibilityState === 'visible') refreshStatus(); }, 10000)" in SHELL
    assert "docker ${engine}" in SHELL


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
    # Wash: numeric columns are right-aligned tabular via the .w-table [data-num] hook (G§9.19).
    for header in ('<th scope="col">Trigger</th>', '<th scope="col" data-num>Duration</th>', '<th scope="col">Started</th>'):
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


def test_work_uses_the_whole_leaf_with_a_dock_and_a_two_column_job_page():
    # Mu §11b (was §7.2's preview dock): Work is a workspace of panes — the list (compact job
    # rows, Now / Running / Recent, with a filter) beside the board on Now and beside the job,
    # which renders in place in the main pane with its context (actions, facts, stages,
    # budget, receipts) in the aside. The other routes take the main pane alone.
    assert '<div class="w-panes work-panes" id="work-panes" data-layout="list-main" data-show="main">' in HTML
    assert '<section class="w-pane work-list-pane" data-pane="list" aria-label="Jobs list">' in HTML
    assert 'class="w-pane work-main work-layout" data-pane="main" id="work-main"' in HTML
    assert '<aside class="w-pane work-aside-pane" data-pane="aside" id="work-aside" aria-label="Job context"></aside>' in HTML
    assert '<div class="w-pane-resize" data-pane-resize aria-hidden="true"></div>' in HTML
    view = (STATIC / "js" / "work" / "view.js").read_text(encoding="utf-8")
    # A row opens the job in place; the list stays; j / k walk the list and open the next job.
    assert "function openRow(id) { return open(id); }" in view and "WashSheet" not in view
    assert "const groups = [['Now', needs], ['Running', running], ['Recent', rest]]" in view
    assert "{id: 'list:next', label: 'Next job', group: 'Jobs', key: 'j'" in view
    assert "view.listMode(route.name);" in SHELL
    assert "panesEl.dataset.layout = route.name === 'job' ? 'list-main-aside' : route.name === 'dashboard' ? 'list-main'" in SHELL
    # The job: its work in the main pane, its context moved into the aside pane.
    assert '<aside class="work-job-side" aria-label="Job summary">' in view and '<div class="work-job-main">' in view
    assert "if (asideEl && side) asideEl.replaceChildren(side);" in view
    # Below three panes the context sits above the work, so the decisive action stays in view.
    assert 'grid-template-areas: "list aside" "list main";' in CSS
    # Now with nothing open is a board: needs you, running (stages as meters), cost by day.
    assert "cell('running-now', 'Running'" in view and "function spendCell(now)" in view
    assert "if (!buckets.some(Boolean)) return '';" in view       # no $0.00 tile
    assert 'class="work-meter__fill" width="${v}"' in view
    # Activity: rows, the run (containers + live log) docked in the aside; no zero counts, no engine chip.
    assert "asideEl.toggleAttribute('data-dock', route.name === 'activity')" in SHELL
    assert 'class="w-row work-activity-row"' in ACTIVITY and "param: { key: 'open', value: `run:${job.id}` }" in ACTIVITY
    assert '<span class="w-count" data-tone="running">${jobs.length}</span>' not in ACTIVITY
    assert "${orphans.length ? `<section class=\"w-group\"" in ACTIVITY
    # Status is a sign and a word, never a chip (§5.4).
    assert "const pill = (tone, html, title = '') => `<span class=\"work-tag\"" in view
