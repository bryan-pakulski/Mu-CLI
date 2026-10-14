"""Standalone web routes share the planos route shell.

/trace, /containers, /static/job_trace.html and /static/model_costs.html all
render the planos top bar (logo, crumbs, status chips, ghost icon tools with a
theme toggle and links back to chat and jobs) over panelled content, and use
the shared status vocabulary (glyph + label, never colour alone).
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "mu" / "gui"

PAGES = {
    "trace": GUI / "templates" / "trace.html",
    "containers": GUI / "templates" / "containers.html",
    "job_trace": GUI / "static" / "job_trace.html",
    "model_costs": GUI / "static" / "model_costs.html",
}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_every_route_renders_the_planos_top_bar_and_navigation():
    for name, path in PAGES.items():
        html = _read(path)
        assert "route-shell" in html, name
        assert "route-top" in html, name
        assert 'class="route-logo' in html, name
        assert 'class="route-crumbs"' in html, name
        assert 'class="route-chips"' in html, name
        # Ghost icon tools with a way back to chat and (except jobs pages' crumbs) jobs.
        assert 'href="/"' in html, name
        assert 'href="/work"' in html, name
        assert "btn ghost icon" in html, name
        assert 'aria-label="Toggle appearance"' in html or 'aria-label="Toggle theme"' in html, name


def test_route_shell_css_matches_planos_shell_geometry():
    css = _read(GUI / "static" / "css" / "route-product.css")
    assert ".route-shell > header.route-top" in css
    assert "height:48px" in css
    assert "background:var(--surface)" in css
    assert "border-bottom:1px solid var(--border)" in css
    assert '.route-crumbs li + li::before{content:"›"' in css
    # model pricing does not import trace.css, so it links the shell directly.
    assert "/static/css/route-product.css" in _read(PAGES["model_costs"])
    assert '@import url("/static/css/route-product.css")' in _read(GUI / "static" / "css" / "trace.css")


def test_analysis_sections_are_planos_panels_with_header_rows():
    css = _read(GUI / "static" / "css" / "trace.css")
    section = css.split(".trace-section {", 1)[1].split("}", 1)[0]
    assert "border: 1px solid var(--border)" in section
    assert "border-radius: var(--radius-lg)" in section
    assert "background: var(--surface)" in section
    header = css.split(".trace-h2 {", 1)[1].split("}", 1)[0]
    assert "border-bottom: 1px solid var(--border)" in header


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
    assert "/static/js/route_theme.js" in html and "data-route-theme" in html
