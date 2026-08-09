from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "mu" / "gui" / "templates" / "work.html"
WORK_POLISH = ROOT / "mu" / "gui" / "static" / "css" / "work_polish.css"
ROUTE_THEME = ROOT / "mu" / "gui" / "static" / "js" / "route_theme.js"
TRACE_HTML = ROOT / "mu" / "gui" / "static" / "job_trace.html"
TRACE_JS = ROOT / "mu" / "gui" / "static" / "js" / "job_trace.js"
MODEL_HTML = ROOT / "mu" / "gui" / "static" / "model_costs.html"
RUNNER = ROOT / "mu" / "jobs" / "runner.py"
PRICING = ROOT / "config" / "model_pricing.json"


def test_engineering_work_uses_primary_mucli_chrome_and_theme_switcher():
    html = WORK.read_text(encoding="utf-8")
    css = WORK_POLISH.read_text(encoding="utf-8")
    theme = ROUTE_THEME.read_text(encoding="utf-8")

    assert '/static/css/product.css' in html
    assert '/static/css/clarity.css' in html
    assert '/static/css/work_polish.css' in html
    assert '/static/js/route_theme.js' in html
    assert 'work-header product-header' in html
    assert 'data-route-theme' in html
    assert 'product-icon-button work-header-icon' in html
    assert 'title="Analyze jobs"' in html
    assert 'title="Model pricing"' in html
    assert 'title="Manage jobs"' in html
    assert 'work-pane-head-controls' in html
    assert 'id="work-refresh"' in html
    assert 'work-manage-button' not in html
    assert '.work-header.product-header' in css
    assert '.work-board-refresh' in css
    assert "localStorage.setItem('mucli-theme'" in theme


def test_job_trace_and_model_pricing_share_product_visual_language():
    trace = TRACE_HTML.read_text(encoding="utf-8")
    pricing = MODEL_HTML.read_text(encoding="utf-8")

    for html in (trace, pricing):
        assert '/static/css/product.css' in html
        assert '/static/css/clarity.css' in html
        assert 'product-header' in html
    assert 'id="jt-theme"' in trace
    assert 'id="mc-theme"' in pricing
    assert 'Pricing registry' in pricing
    assert 'Blended est. / 1M total' in pricing
    assert 'Quick estimator' not in pricing


def test_job_analyzer_explains_state_residence_and_links_full_harness_trace():
    html = TRACE_HTML.read_text(encoding="utf-8")
    script = TRACE_JS.read_text(encoding="utf-8")
    runner = RUNNER.read_text(encoding="utf-8")

    assert 'Agent harness trace' in html
    assert 'state residence is separated from actual execution' in html
    assert 'Lifecycle drill-down' in html
    assert 'jt-phase-drill' in html
    assert 'runtime_trace' in script
    assert 'interpretation' in script
    assert 'entry_event' in script
    assert 'exit_event' in script
    assert 'args.trace = True' in runner


def test_glm_cloud_default_estimate_is_configured_not_silently_unpriced():
    config = json.loads(PRICING.read_text(encoding="utf-8"))
    glm = next(row for row in config["models"] if row["provider"] == "ollama" and row["key"] == "glm-5.2:cloud")

    assert glm["billing"] == "estimated_token"
    assert glm["estimated_total_per_million"] == 1.4
    assert 'estimate' in glm["notes"].lower()
