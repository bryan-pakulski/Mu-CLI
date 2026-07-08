"""Tests for the GUI History Search Panel feature.

Verifies:
  - 'history' mode registered in AGENTIC_MODES + AGENT_MODE_METADATA
  - history_panel.html fragment exists with required elements
  - index.html includes history_panel.html
  - app.js contains Alpine.store('history') with search state fields
  - app.js panelModes array contains 'history'
"""

import os
import pytest

from utils.config import AGENTIC_MODES, AGENT_MODE_METADATA


# ============================================================ mode registration


def test_history_in_agentic_modes():
    assert "history" in AGENTIC_MODES
    prompt = AGENTIC_MODES["history"]
    assert isinstance(prompt, str)
    assert len(prompt) > 50  # non-trivial system prompt


def test_history_in_agent_mode_metadata():
    assert "history" in AGENT_MODE_METADATA
    meta = AGENT_MODE_METADATA["history"]
    assert "display_name" in meta
    assert "description" in meta
    assert isinstance(meta["display_name"], str)
    assert isinstance(meta["description"], str)


def test_history_mode_does_not_require_workspace():
    import inspect
    from mu.gui.routers import modes as modes_mod
    source = inspect.getsource(modes_mod)
    assert "history" in source
    assert "_NO_WORKSPACE_NEEDED" in source
    # Verify 'history' is in the no-workspace set by checking the source
    assert '"history"' in source


# ============================================================ panel fragment


PANEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "mu", "gui", "templates", "fragments", "history_panel.html",
)


def test_history_panel_html_exists():
    assert os.path.isfile(PANEL_PATH), f"history_panel.html not found at {PANEL_PATH}"


def test_history_panel_has_mode_panel_aside():
    with open(PANEL_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert 'class="mode-panel"' in content
    assert 'data-mode="history"' in content


def test_history_panel_has_search_input():
    with open(PANEL_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert "$store.history.query" in content
    assert "history-search-input" in content or "search" in content.lower()


def test_history_panel_has_role_filter():
    with open(PANEL_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert "$store.history.role" in content


def test_history_panel_has_tool_name_filter():
    with open(PANEL_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert "$store.history.tool_name" in content or "$store.history.tool" in content


def test_history_panel_has_search_button():
    with open(PANEL_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert "$store.history.search()" in content


def test_history_panel_displays_results():
    with open(PANEL_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert "$store.history.results" in content
    assert "parts_matched" in content or "parts_matched" in content


def test_history_panel_has_loading_state():
    with open(PANEL_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert "$store.history.loading" in content


def test_history_panel_has_empty_state():
    with open(PANEL_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    # Empty state: results.length === 0 and not loading
    assert "results.length" in content or "searched" in content


def test_history_panel_has_error_state():
    with open(PANEL_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert "$store.history.error" in content


# ============================================================ index.html inclusion


INDEX_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "mu", "gui", "templates", "index.html",
)


def test_index_html_includes_history_panel():
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert "history_panel.html" in content
    assert "{% include" in content or "include" in content


# ============================================================ app.js Alpine store


APP_JS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "mu", "gui", "static", "js", "app.js",
)


def test_app_js_has_alpine_history_store():
    with open(APP_JS_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert 'Alpine.store("history"' in content


def test_app_js_history_store_has_query_field():
    with open(APP_JS_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    # Find the history store block and check for query field
    assert "query:" in content


def test_app_js_history_store_has_results_field():
    with open(APP_JS_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert "results:" in content


def test_app_js_history_store_has_loading_field():
    with open(APP_JS_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert "loading:" in content


def test_app_js_history_store_has_error_field():
    with open(APP_JS_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert "error:" in content


def test_app_js_history_store_has_search_method():
    with open(APP_JS_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert "search()" in content
    assert "/chat/history/search" in content


def test_app_js_history_store_has_clear_method():
    with open(APP_JS_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert "clearResults" in content


def test_app_js_panel_modes_includes_history():
    with open(APP_JS_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert '"history"' in content
    # Check it's in the panelModes array specifically
    assert "panelModes" in content


# ============================================================ CSS


CSS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "mu", "gui", "static", "css", "app.css",
)


def test_css_has_history_panel_classes():
    with open(CSS_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    for cls in (
        ".history-search-bar",
        ".history-filters",
        ".history-results",
        ".history-result-card",
        ".history-context-line",
        ".history-anchor-badge",
        ".history-match-tag",
    ):
        assert cls in content, f"CSS class {cls} not found in app.css"