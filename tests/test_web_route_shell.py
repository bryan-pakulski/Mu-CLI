"""Standalone web routes share the Wash app shell.

/trace, /containers, /jobs/trace and /models/costs all extend
templates/_layout.html: the shared top bar (wordmark, breadcrumb, status
chips, tools and the sprite theme toggle) and sidebar navigation (Chat, Work,
Environments, Trace) over panelled content, and use the shared status
vocabulary (glyph + label, never colour alone). The old static job trace and
model pricing URLs 308-redirect to the Jinja pages.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "mu" / "gui"

PAGES = {
    "trace": GUI / "templates" / "trace.html",
    "containers": GUI / "templates" / "containers.html",
    "job_trace": GUI / "templates" / "job_trace.html",
    "model_costs": GUI / "templates" / "model_costs.html",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_every_route_renders_the_shared_wash_shell_and_navigation():
    # Intent (was: each page carried its own planos route-top bar): every
    # standalone page renders the one shared top bar + sidebar from
    # _layout.html, with a way back to chat and jobs and a theme toggle.
    layout = _read(GUI / "templates" / "_layout.html")
    base = _read(GUI / "templates" / "base.html")
    sidebar = _read(GUI / "templates" / "partials" / "sidebar.html")
    macros = _read(GUI / "templates" / "macros" / "wash.html")
    assert '{% extends "_layout.html" %}' in base
    for name, path in PAGES.items():
        html = _read(path)
        assert ('{% extends "_layout.html" %}' in html) or ('{% extends "base.html" %}' in html), name
        # Standalone pages use the shared shell blocks, not their own chrome.
        assert "{% block page %}" not in html, name
        assert "{% block crumbs %}" in html, name
        assert "{% block topbar_chips %}" in html, name
        assert "route-top" not in html, name
    assert '<header class="w-topbar">' in layout
    assert '<a href="/" class="w-wordmark"' in layout
    assert 'class="w-breadcrumb" aria-label="Breadcrumb"' in layout
    assert 'class="w-topbar__chips" aria-label="Status"' in layout
    assert '{{ w.theme_toggle() }}' in layout
    assert '{% include "partials/sidebar.html" %}' in layout
    assert '("chat", "/", ' in sidebar and '("work", "/work", ' in sidebar
    assert 'aria-current="page"' in sidebar
    assert 'data-variant="icon" data-route-theme' in macros
    assert 'aria-label="Toggle theme"' in macros


def test_route_shell_css_matches_wash_shell_geometry():
    # Intent (was: route-product.css 48px bar on --surface): one top bar
    # geometry from the Wash layout layer — the topbar height token on the
    # canvas with a hairline — and "›" breadcrumb separators.
    layout = _read(GUI / "static" / "css" / "wash" / "layout.css")
    nav = _read(GUI / "static" / "css" / "wash" / "components" / "nav.css")
    topbar = layout.split(".w-topbar {", 1)[1].split("}", 1)[0]
    assert "height: var(--wash-size-topbar);" in topbar
    assert "background: color-mix(in srgb, var(--wash-canvas)" in topbar
    assert "border-bottom: 1px solid var(--wash-hairline);" in topbar
    assert '.w-breadcrumb__item + .w-breadcrumb__item::before {\n    content: "›";' in nav
    assert "--wash-size-topbar: 56px;" in _read(GUI / "static" / "css" / "wash" / "tokens.css")
    # model pricing and the trace analyzer link their own pattern bundles;
    # the legacy route-product.css they used to share is gone (W4).
    assert "/static/css/wash/patterns/model-costs.css" in _read(PAGES["model_costs"])
    assert "/static/css/wash/patterns/trace.css" in _read(PAGES["trace"])
    assert not (GUI / "static" / "css" / "route-product.css").exists()


def test_old_static_analyzer_urls_redirect_to_the_shared_layout_pages():
    app = _read(GUI / "app.py")
    assert 'app.add_api_route("/static/job_trace.html", _moved("/jobs/trace")' in app
    assert 'app.add_api_route("/static/model_costs.html", _moved("/models/costs")' in app
    assert not (GUI / "static" / "job_trace.html").exists()
    assert not (GUI / "static" / "model_costs.html").exists()


def test_analysis_sections_are_planos_panels_with_header_rows():
    # Wash W4: every analysis section is a Wash card (surface, hairline,
    # radius-lg from components/card.css) with a header row: eyebrow + title.
    card = _read(GUI / "static" / "css" / "wash" / "components" / "card.css")
    section = card.split(".w-card {", 1)[1].split("}", 1)[0]
    assert "border: 1px solid var(--wash-hairline);" in section
    assert "border-radius: var(--wash-radius-lg);" in section
    assert "background: var(--wash-surface);" in section
    for page in ("trace", "job_trace"):
        html = _read(PAGES[page])
        assert html.count('<section class="w-card trace-section"') >= 6, page
        assert '<header class="w-card__head trace-head">' in html
        assert '<span class="w-eyebrow">' in html
        assert 'class="w-card__title trace-h2"' in html


def test_routes_use_the_shared_status_vocabulary():
    trace_js = _read(GUI / "static" / "js" / "trace.js")
    for glyph in ("✓", "●", "◆", "✗", "○"):
        assert glyph in trace_js
    assert "statusGlyph(summary && summary.status)" in _read(PAGES["trace"])

    job_trace_js = _read(GUI / "static" / "js" / "job_trace.js")
    assert "applyStatusChip(els.status" in job_trace_js
    for glyph in ("✓", "●", "◆", "✗", "–", "○"):
        assert glyph in job_trace_js

    crystal = _read(GUI / "static" / "css" / "containers-crystal.css")
    assert '.manager-status.running::before{content:"●"}' in crystal
    assert '.manager-status.missing::before{content:"✗"}' in crystal.replace(
        '.manager-status.error::before,', ''
    )


def test_container_dialogs_are_labelled_and_close_on_escape():
    html = _read(PAGES["containers"])
    for modal, title in (
        ("create-modal", "environment-modal-title"),
        ("editor-modal", "editor-title"),
        ("snapshot-modal", "snapshot-title"),
        ("shell-modal", "shell-title"),
    ):
        assert f'id="{modal}" role="dialog" aria-modal="true" aria-labelledby="{title}"' in html
        assert f'id="{title}"' in html
    script = _read(GUI / "static" / "js" / "containers.js")
    assert "event.key !== 'Escape'" in script
    assert "renderChips()" in script
    # Wash W2: the theme controller and toggle come from the shared layout.
    layout = _read(GUI / "templates" / "_layout.html")
    assert "/static/js/route_theme.js" in layout and "w.theme_toggle()" in layout
    assert '{% extends "base.html" %}' in html
