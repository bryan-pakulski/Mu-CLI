"""/design gallery gating, shared-layout pages and the 308 redirects (W2)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from mu.gui.app import create_app


def _no_session(*_args, **_kwargs):
    raise RuntimeError("no session in this test")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path))
    args = SimpleNamespace(workspace=None, session=None, provider=None, model=None)
    app = create_app(args=args, build_session_fn=_no_session, port=30399)
    return TestClient(app)


def test_design_gallery_is_404_unless_enabled(client, monkeypatch):
    monkeypatch.delenv("MUCLI_DESIGN_GALLERY", raising=False)
    assert client.get("/design").status_code == 404
    monkeypatch.setenv("MUCLI_DESIGN_GALLERY", "0")
    assert client.get("/design").status_code == 404


def test_design_gallery_renders_every_component_in_both_themes(client, monkeypatch):
    monkeypatch.setenv("MUCLI_DESIGN_GALLERY", "1")
    response = client.get("/design")
    assert response.status_code == 200
    html = response.text
    assert 'class="g-theme" data-theme="paper"' in html
    assert 'class="g-theme" data-theme="dusk"' in html
    for cls in ("w-btn", "w-chip", "w-eyebrow", "w-count", "w-card", "w-stats", "w-nav__item",
                "w-breadcrumb", "w-tabs", "w-segmented", "w-input", "w-textarea", "w-select",
                "w-switch", "w-check", "w-slider", "w-modal", "w-drawer", "w-menu", "w-popover",
                "w-toast", "w-banner", "w-empty", "w-table", "w-inkdrop", "w-progress",
                "w-skeleton", "w-stepper", "w-icon"):
        assert f'class="{cls}' in html, cls
    for variant in ("primary", "secondary", "ghost", "tonal", "danger", "icon"):
        assert f'data-variant="{variant}"' in html, variant
    # Status chips come from the shared vocabulary (provisioning → "Preparing").
    assert "Preparing" in html
    assert 'x-dialog="open"' in html


def test_old_static_pages_redirect_permanently_with_query(client):
    response = client.get("/static/job_trace.html?job=abc&compare=def", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == "/jobs/trace?job=abc&compare=def"
    response = client.get("/static/model_costs.html", follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == "/models/costs"


@pytest.mark.parametrize("path, marker", [
    ("/jobs/trace", 'id="jt-job-select"'),
    ("/models/costs", 'id="mc-models"'),
    ("/trace", 'x-data="traceApp()"'),
])
def test_standalone_pages_render_on_the_shared_layout(client, path, marker):
    response = client.get(path)
    assert response.status_code == 200
    html = response.text
    assert marker in html
    assert '<link rel="stylesheet" href="/static/css/wash/wash.css">' in html
    assert 'class="w-topbar"' in html and 'id="w-sidebar"' in html
    assert 'data-route-theme' in html
    assert 'aria-current="page"' in html
    assert "/static/vendor/htmx.min.js" not in html
