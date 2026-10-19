from __future__ import annotations

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "mu" / "gui" / "templates" / "work.html"
# Wash W4: /work's rules are the Wash page bundle (work_polish.css retired).
WORK_POLISH = ROOT / "mu" / "gui" / "static" / "css" / "wash" / "patterns" / "work.css"
LAYOUT = ROOT / "mu" / "gui" / "templates" / "_layout.html"
WORK_ANALYSIS_LINK = ROOT / "mu" / "gui" / "static" / "js" / "work_analysis_link.js"
ROUTE_THEME = ROOT / "mu" / "gui" / "static" / "js" / "route_theme.js"
TRACE_HTML = ROOT / "mu" / "gui" / "templates" / "job_trace.html"
TRACE_JS = ROOT / "mu" / "gui" / "static" / "js" / "job_trace.js"
TRACE_SINGLE_TASK = ROOT / "mu" / "gui" / "static" / "js" / "job_trace_signed_delta.js"
MODEL_HTML = ROOT / "mu" / "gui" / "templates" / "model_costs.html"
# Stage sessions run in the sandbox worker; their harness trace must be on.
RUNNER = ROOT / "mu" / "jobs" / "sandbox" / "worker.py"
PRICING = ROOT / "config" / "model_pricing.json"


def test_engineering_work_uses_planos_chrome_and_theme_switcher():
    html = WORK.read_text(encoding="utf-8")
    css = WORK_POLISH.read_text(encoding="utf-8")
    theme = ROUTE_THEME.read_text(encoding="utf-8")
    layout = LAYOUT.read_text(encoding="utf-8")

    # Jobs mode does not load the chat chrome.
    assert '/static/css/product.css' not in html
    assert '/static/css/clarity.css' not in html
    # Wash W4: /work sits on the shared shell (_layout.html) and links one
    # page bundle of Wash rules; the legacy jobs stylesheets are gone.
    assert '{% extends "_layout.html" %}' in html and '{% block page %}' not in html
    assert '/static/css/wash/patterns/work.css' in html
    assert '@import' not in css
    for legacy in ("work.css", "work_polish.css", "work_management.css", "work_jira.css"):
        assert not (ROOT / "mu" / "gui" / "static" / "css" / legacy).exists(), legacy
    # Mu §3.2 / §4: sub-pages are tabs in the page header (the rail never grows a second
    # level); the header carries the state sentence instead of status chips.
    assert '{% block sidebar_extra %}' not in html
    assert '{% block page_tabs %}' in html
    assert 'class="w-pagetabs__tab work-navlink" data-nav="jobs"' in html
    assert '{% block state_attrs %} id="work-state"' in html
    assert '/static/js/work/ui.js' in html
    # Jobs mode does not re-declare a palette: it reads the generated Wash
    # tokens (both themes) directly; the legacy alias file is gone. Density
    # stays local.
    tokens = (ROOT / "mu" / "gui" / "static" / "css" / "wash" / "tokens.css").read_text(encoding="utf-8")
    assert not (ROOT / "mu" / "gui" / "static" / "css" / "tokens.css").exists()
    assert '--accent:' not in css
    assert re.search(r'#[0-9a-fA-F]{3,8}\b', css) is None
    for wash in ('accent-ink', 'ok-ink', 'attention-ink', 'danger-ink', 'running-ink'):
        assert f'--wash-{wash}:' in tokens
        assert f'var(--wash-{wash})' in css
    assert '[data-density="compact"]' in css
    # The shared theme controller comes from the layout; the theme is chosen in Settings.
    assert '/static/js/route_theme.js' in layout
    assert 'data-theme-select' in (ROOT / "mu" / "gui" / "templates" / "settings.html").read_text(encoding="utf-8")
    # The page tools are items of the header's ⋯ menu (Mu §4.4).
    assert 'class="w-menu__item work-header-icon' in html
    assert 'title="Analyze jobs"' in html
    assert 'title="Model pricing"' in html
    assert 'title="Manage jobs"' in html
    assert 'id="work-tools-menu"' in html
    assert 'id="work-refresh"' in html
    assert 'work-manage-button' not in html
    assert 'role="menuitem" id="work-refresh"' in html
    assert '.work-tools {' in css
    assert "localStorage.setItem('mucli-theme'" in theme


def test_job_management_boards_and_settings_are_routed_pages_not_dialogs():
    """planos: History, Boards, Settings and Activity are full pages (page-head + panels)."""
    html = WORK.read_text(encoding="utf-8")
    css = WORK_POLISH.read_text(encoding="utf-8")
    work_js = (ROOT / "mu" / "gui" / "static" / "js" / "work.js").read_text(encoding="utf-8")
    mgmt_js = (ROOT / "mu" / "gui" / "static" / "js" / "work_management.js").read_text(encoding="utf-8")
    jira_js = (ROOT / "mu" / "gui" / "static" / "js" / "work_jira.js").read_text(encoding="utf-8")
    stage_js = (ROOT / "mu" / "gui" / "static" / "js" / "work_stage_models.js").read_text(encoding="utf-8")

    # Ids the scripts and other tests rely on are kept, now inside route sections.
    for page_id in ('work-management-backdrop', 'work-jira-backdrop', 'work-jira-settings-backdrop'):
        assert f'id="{page_id}"' in html
    for route in ('activity', 'boards', 'history', 'settings'):
        section = html.index(f'data-route="{route}"')
        assert html.rfind('<section class="work-route w-leafbody"', 0, section) > html.rfind('</section>', 0, section)
        assert f'href="#/{route}"' in html
    assert html.index('id="work-management-backdrop"') > html.index('data-route="history"')
    assert html.index('id="work-jira-backdrop"') > html.index('data-route="boards"')
    assert html.index('id="work-jira-settings-backdrop"') > html.index('data-route="settings"')
    # No modal wrappers around them any more; nav entries are links that navigate.
    assert 'class="work-management-pane" role="dialog"' not in html
    assert 'work-jira-modal' not in html
    assert '<a href="#/history" id="work-manage"' in html
    assert '<a href="#/boards" id="work-jira-open"' in html
    assert '<a href="#/settings" id="work-jira-settings-open"' in html
    assert "PAGES = { activity: 'Activity', boards: 'Tickets', history: 'History', settings: 'Settings', setup: 'Setup' }" in work_js
    assert "mu:work-route" in work_js
    for script in (mgmt_js, jira_js, stage_js):
        assert "mu:work-route" in script
    assert 'body.work-management-open' not in css
    assert '.work-management-backdrop[hidden]' not in css
    # The New job sheet is still a viewport-level modal layer: a .w-modal in
    # the layout's overlays block (outside the shell), fixed over the viewport.
    overlays = html.split('{% block overlays %}', 1)[1]
    assert 'class="w-modal work-modal-backdrop" id="work-create-backdrop"' in overlays
    modal = (ROOT / "mu" / "gui" / "static" / "css" / "wash" / "components" / "modal.css").read_text(encoding="utf-8")
    rule = modal.split(".w-modal {", 1)[1].split("}", 1)[0]
    assert 'position: fixed;' in rule and 'inset: 0;' in rule


def test_job_trace_and_model_pricing_share_product_visual_language():
    trace = TRACE_HTML.read_text(encoding="utf-8")
    pricing = MODEL_HTML.read_text(encoding="utf-8")

    # Wash W2 / Mu §3: both pages share the app's visual language through the
    # shared layout (rail, leaf, page header, wash.css) and no longer load the
    # chat CSS stack; each links only its own page bundle. The theme is chosen
    # in Settings (Mu §3.1), and both pages sit under Insights' tabs.
    css_dir = ROOT / "mu" / "gui" / "static" / "css" / "wash" / "patterns"
    for html, bundle in ((trace, "job-trace.css"), (pricing, "model-costs.css")):
        assert '{% extends "_layout.html" %}' in html
        assert f'/static/css/wash/patterns/{bundle}' in html
        assert '/static/css/product.css' not in html
        bundle_css = (css_dir / bundle).read_text(encoding="utf-8")
        for chat_css in ("product.css", "crystal.css", "clarity.css", "refinement.css"):
            assert chat_css not in bundle_css
    assert 'data-theme-select' in (ROOT / "mu" / "gui" / "templates" / "settings.html").read_text(encoding="utf-8")
    assert '{{ w.insights_tabs("jobs") }}' in trace and '{{ w.insights_tabs("costs") }}' in pricing
    assert 'Pricing registry' in pricing
    assert 'Input / 1M' in pricing
    assert 'Output / 1M' in pricing
    assert 'Native inputs' in pricing
    assert 'Capabilities' in pricing
    assert 'Blended est. / 1M total' not in pricing
    assert 'Quick estimator' not in pricing


def test_job_trace_focuses_on_selected_individual_task_without_comparison_ui():
    work_link = WORK_ANALYSIS_LINK.read_text(encoding="utf-8")
    single_task = TRACE_SINGLE_TASK.read_text(encoding="utf-8")

    assert 'syncHeaderAnalysisLink' in work_link
    assert '/jobs/trace?job=${encodeURIComponent(jobId)}' in work_link
    assert "document.getElementById('jt-compare-select')?.remove()" in single_task
    assert "document.getElementById('jt-compare-section')?.remove()" in single_task
    assert "searchParams.delete('compare')" in single_task
    assert 'autoLoadFirstJob' in single_task
    assert "jobSelect.dispatchEvent(new Event('change'" in single_task


def test_job_analyzer_explains_state_residence_and_links_full_harness_trace():
    html = TRACE_HTML.read_text(encoding="utf-8")
    script = TRACE_JS.read_text(encoding="utf-8")
    runner = RUNNER.read_text(encoding="utf-8")

    assert 'Agent harness trace' in html
    assert 'state residence is separated from actual execution' in html
    assert 'Lifecycle drill-down' in html
    assert 'id="jt-intervals"' in html
    assert 'runtime_trace' in script
    assert 'interpretation' in script
    assert 'entry_event' in script
    assert 'exit_event' in script
    assert 'trace=True' in runner


def test_glm_cloud_default_estimate_uses_separate_input_and_output_rates():
    config = json.loads(PRICING.read_text(encoding="utf-8"))
    glm = next(row for row in config["models"] if row["provider"] == "ollama" and row["key"] == "glm-5.2:cloud")

    assert glm["billing"] == "estimated_token"
    assert glm["input_per_million"] == 1.4
    assert glm["output_per_million"] == 4.4
    assert glm["estimated_total_per_million"] is None
    assert 'separate measured input and output tokens' in glm["notes"].lower()
