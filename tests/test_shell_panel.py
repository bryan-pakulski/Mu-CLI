"""Tests for the container-mode Shell panel in the GUI tools menu.

Verifies:
1. "shell" is registered in GUI_VIEW_PANELS with needs_container=True.
2. modes.py _is_container_session correctly detects container sessions.
3. /api/modes returns the shell view with needs_container + disabled logic.
4. app.js panelModes includes "shell" and Alpine.store("shell") exists.
5. shell_panel.html fragment exists and is included in index.html.
6. CSS classes for the shell panel exist in app.css.
"""

import json
import os
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CONFIG_PY = REPO / "utils" / "config.py"
MODES_PY = REPO / "mu" / "gui" / "routers" / "modes.py"
APP_JS = REPO / "mu" / "gui" / "static" / "js" / "app.js"
INDEX_HTML = REPO / "mu" / "gui" / "templates" / "index.html"
SHELL_PANEL_HTML = REPO / "mu" / "gui" / "templates" / "fragments" / "shell_panel.html"
APP_CSS = REPO / "mu" / "gui" / "static" / "css" / "app.css"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# config.py — GUI_VIEW_PANELS registration
# ---------------------------------------------------------------------------

def test_shell_in_gui_view_panels():
    """'shell' must be a registered GUI_VIEW_PANELS entry."""
    src = read(CONFIG_PY)
    assert '"name": "shell"' in src, "shell panel not found in GUI_VIEW_PANELS"


def test_shell_needs_container_flag():
    """Shell panel must have needs_container=True."""
    src = read(CONFIG_PY)
    # Find the shell panel block and verify needs_container is True.
    match = re.search(r'"name":\s*"shell".*?"needs_container":\s*True', src, re.DOTALL)
    assert match, "shell panel must have needs_container=True"


# ---------------------------------------------------------------------------
# modes.py — container session detection + view payload
# ---------------------------------------------------------------------------

def test_is_container_session_helper_exists():
    """modes.py must have _is_container_session helper."""
    src = read(MODES_PY)
    assert "_is_container_session" in src, "missing _is_container_session helper"


def test_modes_returns_needs_container_field():
    """The views list in /api/modes must include needs_container per view."""
    src = read(MODES_PY)
    assert '"needs_container"' in src, "views payload missing needs_container field"


def test_modes_returns_has_container():
    """The /api/modes response must include has_container."""
    src = read(MODES_PY)
    assert '"has_container"' in src, "modes response missing has_container"


def test_modes_disabled_includes_container_logic():
    """The disabled field must account for needs_container + has_container."""
    src = read(MODES_PY)
    assert "needs_container" in src and "has_container" in src
    # The disabled expression must reference both.
    assert re.search(
        r'needs_container.*not.*has_container|has_container.*not.*needs_container',
        src,
        re.DOTALL,
    ), "disabled logic doesn't combine needs_container + has_container"


# ---------------------------------------------------------------------------
# app.js — Alpine store + panelModes
# ---------------------------------------------------------------------------

def test_shell_in_panel_modes():
    """panelModes array must include 'shell'."""
    src = read(APP_JS)
    # The panelModes line lists all view-panel mode names.
    match = re.search(r'panelModes:\s*\[([^\]]+)\]', src)
    assert match, "panelModes array not found"
    assert '"shell"' in match.group(1), "shell not in panelModes"


def test_alpine_shell_store_exists():
    """Alpine.store('shell') must be defined."""
    src = read(APP_JS)
    assert 'Alpine.store("shell"' in src, "Alpine.store('shell') not defined"


def test_shell_store_has_websocket_connect():
    """Shell store must use WebSocket to connect to /api/containers/{name}/shell."""
    src = read(APP_JS)
    assert "new WebSocket(" in src, "WebSocket not used in shell store"
    assert "/api/containers/" in src, "container shell WS endpoint not referenced"


def test_shell_store_has_send_disconnect_clear():
    """Shell store must have send, disconnect, and clear methods."""
    src = read(APP_JS)
    # Within the shell store block, verify these methods exist.
    # Find the shell store block.
    match = re.search(r'Alpine\.store\("shell".*?\}\);', src, re.DOTALL)
    assert match, "could not isolate shell store block"
    block = match.group(0)
    assert "send(" in block, "shell store missing send()"
    assert "disconnect(" in block, "shell store missing disconnect()"
    assert "clear(" in block, "shell store missing clear()"


# ---------------------------------------------------------------------------
# HTML fragment — panel template + inclusion
# ---------------------------------------------------------------------------

def test_shell_panel_html_exists():
    """shell_panel.html fragment must exist."""
    assert SHELL_PANEL_HTML.is_file(), "shell_panel.html not found"


def test_shell_panel_has_correct_mode():
    """shell_panel.html must have data-mode='shell' and x-show for shell."""
    src = read(SHELL_PANEL_HTML)
    assert 'data-mode="shell"' in src, "shell_panel.html missing data-mode='shell'"
    assert "$store.mode.active === 'shell'" in src, "shell_panel.html missing x-show for shell"


def test_shell_panel_included_in_index():
    """index.html must include the shell_panel fragment."""
    src = read(INDEX_HTML)
    assert 'fragments/shell_panel.html' in src, "shell_panel.html not included in index.html"


# ---------------------------------------------------------------------------
# CSS — shell panel styles
# ---------------------------------------------------------------------------

def test_shell_panel_css_exists():
    """app.css must contain shell panel styles."""
    src = read(APP_CSS)
    assert ".shell-panel" in src, ".shell-panel CSS class not found"
    assert ".shell-output" in src, ".shell-output CSS class not found"
    assert ".shell-input" in src, ".shell-input CSS class not found"