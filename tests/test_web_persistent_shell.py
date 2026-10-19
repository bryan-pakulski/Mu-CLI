"""The persistent shell (mu/gui/shell.py; Mu revision 2): browsers get one shell document
and every destination becomes a keep-alive view inside it, so moving between destinations
never reloads a page. API clients and ?standalone=1 still get the standalone pages."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "mu" / "gui"
BROWSER = {"accept": "text/html,application/xhtml+xml,*/*;q=0.8", "sec-fetch-dest": "document"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from mu.gui.app import create_app
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path / "home"))
    app = create_app(args=SimpleNamespace(provider=None, model=None), build_session_fn=lambda *a, **k: None)
    return TestClient(app)


@pytest.mark.parametrize("path,nav,mode", [
    ("/", "chat", "chat"), ("/work", "work", "work"), ("/orchestration", "orchestration", "orchestration"),
    ("/containers", "environments", "environments"), ("/trace", "insights", "insights"),
    ("/models/costs", "insights", "insights"), ("/settings", "settings", "settings"),
])
def test_browsers_get_the_shell_for_every_destination(client, path, nav, mode):
    page = client.get(path, headers=BROWSER).text
    assert '<html lang="en" data-shell-root data-mode="%s">' % mode in page
    assert '<main class="w-stage mu-views" id="w-main" tabindex="-1" data-views></main>' in page
    assert '/static/js/mu_shell.js' in page and "window.MU_DESTINATIONS = " in page
    assert 'data-nav="%s" title=' % nav in page and 'aria-current="page"' in page
    # Two watercolour layers cross-fade between modes.
    assert page.count("data-ambient-layer") == 2


def test_views_api_clients_and_standalone_get_the_page(client):
    # A view inside the shell: embed mode, no rail, no ambient wash plane.
    view = client.get("/work?embed=1", headers=BROWSER).text
    assert "<html lang=\"en\" data-embed data-mode=\"work\">" in view
    assert 'class="w-sidebar"' not in view and "data-shell-menu" not in view
    framed = client.get("/work", headers={**BROWSER, "sec-fetch-dest": "iframe"}).text
    assert "data-embed" in framed and "data-shell-root" not in framed
    # Non-browser clients and ?standalone=1 keep the standalone page with its rail.
    api = client.get("/work").text
    assert "data-shell-root" not in api and 'class="w-sidebar"' in api
    standalone = client.get("/work?standalone=1", headers=BROWSER).text
    assert "data-shell-root" not in standalone and 'class="w-sidebar"' in standalone
    # Non-destination pages never get the shell.
    assert "data-shell-root" not in client.get("/design", headers=BROWSER).text


def test_chat_sessions_become_the_views_own_column(client):
    from mu.gui import shell
    assert shell.DESTINATIONS["/"] == ("chat", "chat")
    layout = (GUI / "templates" / "_layout.html").read_text(encoding="utf-8")
    assert '<aside class="w-sidebar mu-viewside" id="w-sidebar"' in layout


def test_shell_keeps_views_alive_and_in_step_with_the_address_bar():
    js = (GUI / "static" / "js" / "mu_shell.js").read_text(encoding="utf-8")
    # One view per destination, created on first visit and never torn down.
    assert "var view = views.get(key);\n        if (!view) view = create(key, target);" in js
    assert "frame.remove()" not in js and "removeChild" not in js
    # Links and the palette switch views; history and titles follow the active view.
    assert "window.MuNav = { go: function (href) { navigate(href); } };" in js
    assert "history.pushState({ mu: key }, '', target)" in js
    assert "window.addEventListener('popstate'" in js
    assert "data.type === 'mu:url'" in js and "data.type === 'mu:go'" in js
    # Messages are only accepted from same-origin views the shell created.
    assert "if (event.origin !== location.origin) return;" in js
    assert "if (v.frame.contentWindow === event.source)" in js
    embed = (GUI / "static" / "js" / "wash_shell.js").read_text(encoding="utf-8")
    assert "toShell({ type: 'mu:url', href: href, title: document.title });" in embed
    assert "event.origin !== location.origin || event.source !== window.parent" in embed
    palette = (GUI / "static" / "js" / "wash" / "palette.js").read_text(encoding="utf-8")
    assert "if (window.MuNav && typeof window.MuNav.go === 'function') window.MuNav.go(href);" in palette


def test_modes_fade_slowly_and_surfaces_are_flat():
    shell_css = (GUI / "static" / "css" / "wash" / "patterns" / "shell.css").read_text(encoding="utf-8")
    assert "@property --mu-accent {" in shell_css
    assert "--mu-fade: 1400ms;" in shell_css
    assert "transition: opacity var(--mu-fade) var(--wash-ease-bleed);" in shell_css
    for mode in ("chat", "work", "orchestration", "environments", "insights", "settings"):
        assert f'[data-mode="{mode}"] {{ --mu-accent:' in shell_css
        assert f'.mu-ambient__layer[data-mode="{mode}"]' in shell_css
    assert "@media (prefers-reduced-motion: reduce) {\n    :root { --mu-fade: 0ms; }" in shell_css
    base = (GUI / "static" / "css" / "wash" / "base.css").read_text(encoding="utf-8")
    assert "--w-glass-filter: none;" in base and "--w-glass-fill: var(--wash-surface);" in base
