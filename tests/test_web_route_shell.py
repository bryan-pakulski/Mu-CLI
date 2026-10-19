"""Every web route shares the Mu frame (documentation/design/mu_style_guide.md §3).

/trace, /containers, /jobs/trace, /models/costs and /settings all extend
templates/_layout.html: the rail (wordmark, the four destinations, Insights
and Settings at its foot) beside the leaf, one glass sheet holding the page
header (title, state sentence, text tabs, one primary action). There is no
top bar. Routes use the shared status vocabulary (glyph + label, never colour
alone). The old static job trace and model pricing URLs 308-redirect to the
Jinja pages.
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
    # Intent (was: each page carried its own planos route-top bar, then the shared
    # Wash top bar): every page renders the one shared frame from _layout.html —
    # the rail with a way to every destination, and the leaf's page header.
    layout = _read(GUI / "templates" / "_layout.html")
    base = _read(GUI / "templates" / "base.html")
    sidebar = _read(GUI / "templates" / "partials" / "sidebar.html")
    macros = _read(GUI / "templates" / "macros" / "wash.html")
    assert '{% extends "_layout.html" %}' in base
    for name, path in dict(PAGES, settings=GUI / "templates" / "settings.html").items():
        html = _read(path)
        assert ('{% extends "_layout.html" %}' in html) or ('{% extends "base.html" %}' in html), name
        # Pages use the shared frame blocks, not their own chrome or the retired top bar.
        assert "{% block page %}" not in html, name
        for retired in ("{% block crumbs %}", "{% block topbar_chips %}", "{% block topbar_tools %}", "route-top"):
            assert retired not in html, (name, retired)
    # No top bar (Mu §3.1); the wordmark heads the rail; the leaf holds the page header.
    assert "w-topbar" not in layout and "w-breadcrumb" not in layout
    assert '<a href="/" class="w-wordmark w-sidebar__mark"' in sidebar
    assert '<main class="w-stage" id="w-main" tabindex="-1">' in layout
    head = layout.split('<header class="w-pagehead" id="w-pagehead"', 1)[1].split("</header>", 1)[0]
    for hook in ('class="w-pagehead__title" data-head-title', 'class="w-pagehead__actions" data-head-actions',
                 'class="w-state w-pagehead__state" data-head-state', 'class="w-pagetabs" data-head-tabs',
                 'class="w-pagehead__back" data-head-back'):
        assert hook in head, hook
    assert '<div class="w-leaf-alert" data-leaf-alert role="status" aria-live="polite">' in layout
    assert '{% include "partials/sidebar.html" %}' in layout
    # The rail: four destinations, then Insights and Settings; never a destination's sub-pages.
    for entry in ('("chat", "/", ', '("work", "/work", ', '("orchestration", "/orchestration", ',
                  '("environments", "/containers", ', '("insights", "/trace", ', '("settings", "/settings", '):
        assert entry in sidebar, entry
    assert 'aria-current="page"' in sidebar
    assert 'if active in ("trace", "job_trace", "model_costs") %}{% set active = "insights" %}' in sidebar
    # The theme moved to Settings (and the palette); the toggle macro stays for other surfaces.
    settings = _read(GUI / "templates" / "settings.html")
    assert "data-theme-select" in settings and "data-decoration-toggle" in settings
    assert 'data-variant="icon" data-route-theme' in macros
    assert 'aria-label="Toggle theme"' in macros
    # Insights is one destination with three tabs.
    assert '("runs", "/trace", "Runs"), ("jobs", "/jobs/trace", "Jobs"), ("costs", "/models/costs", "Costs")' in macros


def test_route_shell_css_matches_wash_shell_geometry():
    # Intent (was: one top bar geometry): the frame is the rail beside the leaf —
    # one glass plane inset from the window, the rail on the softened wash, and
    # the phone drawer below 840 where the leaf fills the window.
    layout = _read(GUI / "static" / "css" / "wash" / "layout.css")
    assert "w-topbar" not in layout
    shell = layout.split(".w-shell {", 1)[1].split("}", 1)[0]
    assert 'grid-template-areas: "side stage";' in shell
    assert "--w-leaf-gap: var(--wash-size-leaf-gap);" in shell
    leaf = layout.split(".w-shell::before {", 1)[1].split("}", 1)[0]
    assert "border-radius: var(--wash-radius-xl);" in leaf
    assert "margin: var(--w-leaf-gap) var(--w-leaf-gap) var(--w-leaf-gap) 0;" in leaf
    stage = layout.split(".w-stage {", 1)[1].split("}", 1)[0]
    assert "overflow: auto;" in stage and "border-radius: var(--wash-radius-xl);" in stage
    rail = layout.split(".w-sidebar {", 1)[1].split("}", 1)[0]
    assert "backdrop-filter" not in rail and "var(--w-rail-veil)" in rail
    phone = layout.split("@media (width < 840px) {", 1)[1]
    assert "--w-leaf-gap: 0px;" in phone and "transform: translateX(-104%);" in phone
    import json
    tokens = json.loads(_read(GUI.parent / "design" / "tokens.json"))
    assert tokens["size"]["sheet"] == 520 and "sheet" in tokens["density"]["keep"]
    generated = _read(GUI / "static" / "css" / "wash" / "tokens.css")
    assert "--wash-size-sheet: 520px;" in generated and "--wash-size-leaf-gap:" in generated
    # model pricing and the trace analyzer link their own pattern bundles;
    # the legacy route-product.css they used to share is gone (W4).
    assert "/static/css/wash/patterns/model-costs.css" in _read(PAGES["model_costs"])
    assert "/static/css/wash/patterns/trace.css" in _read(PAGES["trace"])
    assert not (GUI / "static" / "css" / "route-product.css").exists()


def test_frame_routes_settings_and_insights():
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from mu.gui.app import create_app
    app = create_app(args=SimpleNamespace(provider=None, model=None), build_session_fn=lambda *a, **k: None)
    client = TestClient(app)
    page = client.get("/settings")
    assert page.status_code == 200
    assert '<h1 class="w-pagehead__title" data-head-title>Settings</h1>' in page.text
    assert 'href="/settings" data-nav="settings" title="Settings" aria-current="page"' in page.text
    moved = client.get("/insights", follow_redirects=False)
    assert moved.status_code == 307 and moved.headers["location"] == "/trace"
    trace = client.get("/trace").text
    assert 'href="/trace" data-nav="insights" title="Insights" aria-current="page"' in trace


def test_wash_head_and_sheet_apis():
    # Hash-routed pages rewrite the page header per route; the detail sheet is
    # shared (Mu §7.2): Esc closes it and its state lives in the URL query.
    js = _read(GUI / "static" / "js" / "wash_shell.js")
    assert "window.WashHead = { set: setHead, esc: esc };" in js
    assert "window.WashSheet = {" in js and "url.searchParams.set(param.key, param.value)" in js
    assert "if (event.key !== 'Escape' || sheet !== entry) return;" in js
    # Wide screens dock the details beside the list (Mu §7.2); narrow ones float them.
    assert "var dock = spec.float ? null : visibleDock();" in js
    assert "docked: function () { return !!visibleDock(); }" in js
    mu = _read(GUI / "static" / "css" / "wash" / "components" / "mu.css")
    assert ".w-split__dock { display: none; }" in mu and "@media (width >= 1200px) {\n    .w-split {" in mu
    # The leaf is used whole: no narrow content cap on the header or the body.
    assert "max-width: var(--w-pagehead-max, none);" in mu
    palette = _read(GUI / "static" / "js" / "wash" / "palette.js")
    assert "var GO_KEYS = { c: '/', w: '/work', o: '/orchestration', e: '/containers', i: '/trace', t: '/trace', s: '/settings' };" in palette
    # Text from callers is escaped before it reaches innerHTML.
    assert "esc(part.text)" in js and "esc(tab.label)" in js and "esc(tab.href)" in js


def test_old_static_analyzer_urls_redirect_to_the_shared_layout_pages():
    app = _read(GUI / "app.py")
    assert 'app.add_api_route("/static/job_trace.html", _moved("/jobs/trace")' in app
    assert 'app.add_api_route("/static/model_costs.html", _moved("/models/costs")' in app
    assert not (GUI / "static" / "job_trace.html").exists()
    assert not (GUI / "static" / "model_costs.html").exists()


def test_analysis_sections_are_planos_panels_with_header_rows():
    # Wash W4 defined the card (surface, hairline, radius-lg from components/card.css).
    card = _read(GUI / "static" / "css" / "wash" / "components" / "card.css")
    section = card.split(".w-card {", 1)[1].split("}", 1)[0]
    assert "border: 1px solid var(--w-glass-line);" in section
    assert "border-radius: var(--wash-radius-lg);" in section
    assert "background: var(--w-glass-fill);" in section
    # Mu §5: analysis sections are groups — title-3 headings separated by space,
    # never cards or eyebrows (the card component itself is unchanged above).
    for page in ("trace", "job_trace"):
        html = _read(PAGES[page])
        assert html.count('<section class="w-group trace-section"') >= 6, page
        assert '<header class="w-group__head trace-head">' in html
        assert 'class="w-group__title trace-h2"' in html
        import re
        assert not re.search(r'class="[^"]*\bw-(?:eyebrow|card)\b', html), page


def test_routes_use_the_shared_status_vocabulary():
    trace_js = _read(GUI / "static" / "js" / "trace.js")
    for glyph in ("✓", "●", "◆", "✗", "○"):
        assert glyph in trace_js
    assert "statusGlyph(summary && summary.status)" in _read(PAGES["trace"])

    job_trace_js = _read(GUI / "static" / "js" / "job_trace.js")
    assert "applyStatusChip(els.status" in job_trace_js
    for glyph in ("✓", "●", "◆", "✗", "–", "○"):
        assert glyph in job_trace_js

    # Mu §5.4: environment status renders as a sign (glyph + the vocabulary status key).
    containers_js = _read(GUI / "static" / "js" / "containers.js")
    assert "running: {glyph: '●', tone: 'running', label: 'Running'}" in containers_js
    assert "missing: {glyph: '✗', tone: 'danger', label: 'Missing'}" in containers_js
    assert '<span class="w-sign" data-status="${style.sign}" aria-hidden="true">${style.glyph}</span>' in containers_js


def test_container_dialogs_are_labelled_and_close_on_escape():
    html = _read(PAGES["containers"])
    for modal, title in (
        ("create-modal", "environment-modal-title"),
        ("editor-modal", "editor-title"),
        ("snapshot-modal", "snapshot-title"),
        ("shell-modal", "shell-title"),
    ):
        # The backdrop is the shared .w-modal; the dialog semantics sit on
        # its .w-modal__dialog surface (dialogs.js MuDialog).
        backdrop = html.split(f'id="{modal}" hidden>', 1)[1]
        surface = backdrop.split(">", 1)[0]
        assert 'class="w-modal__dialog' in surface
        assert f'role="dialog" aria-modal="true" aria-labelledby="{title}"' in surface
        assert f'id="{title}"' in html
    script = _read(GUI / "static" / "js" / "containers.js")
    # Esc: the shared dialog stack dispatches `dialog-close`, which clicks the
    # dialog's own close control.
    assert "window.MuDialog.open(modal" in script and "window.MuDialog.close(modal)" in script
    assert "modal.addEventListener('dialog-close'" in script
    dialogs = _read(GUI / "static" / "js" / "dialogs.js")
    assert "event.key === 'Escape'" in dialogs and "new CustomEvent('dialog-close')" in dialogs
    assert "renderState()" in script
    # Wash W2: the theme controller comes from the shared layout; the theme control
    # lives in Settings (Mu §3.1), reachable from the rail on every page.
    layout = _read(GUI / "templates" / "_layout.html")
    assert "/static/js/route_theme.js" in layout
    assert "data-theme-select" in _read(GUI / "templates" / "settings.html")
    assert '{% extends "base.html" %}' in html
