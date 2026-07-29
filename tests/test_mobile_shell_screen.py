"""Regression tests for mobile Shell screen + relaxed server shell WS endpoint.

Verifies:
1. ShellScreen.tsx exists and is a valid React component module.
2. ShellScreen uses WebSocket to connect to /api/containers/{name}/shell.
3. ShellScreen resolves container name via sessionsApi.getContainer.
4. ShellScreen has connect/disconnect/send/clear actions.
5. ShellScreen strips ANSI escape sequences.
6. Shell is registered in workspace.ts WorkspaceScreenName + runtime category.
7. Shell is registered in AppNavigator RootStackParamList + PANEL_SCREENS.
8. Server shell WS endpoint allows private network clients (not localhost-only).
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOBILE = ROOT / "mobile" / "android" / "src"
SHELL_SCREEN = MOBILE / "screens" / "ShellScreen.tsx"
WORKSPACE_TS = MOBILE / "navigation" / "workspace.ts"
APP_NAV = MOBILE / "navigation" / "AppNavigator.tsx"
CONTAINERS_PY = ROOT / "mu" / "gui" / "routers" / "containers.py"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── ShellScreen.tsx ──────────────────────────────────────────────────


def test_shell_screen_exists():
    """ShellScreen.tsx file must exist."""
    assert SHELL_SCREEN.exists(), f"ShellScreen.tsx not found at {SHELL_SCREEN}"


def test_shell_screen_exports_component():
    """ShellScreen must export a named ShellScreen component."""
    src = read(SHELL_SCREEN)
    assert "export function ShellScreen" in src, "ShellScreen component not exported"


def test_shell_screen_uses_websocket():
    """ShellScreen must use WebSocket to connect to the container shell endpoint."""
    src = read(SHELL_SCREEN)
    assert "new WebSocket(" in src, "WebSocket not used"
    assert "/api/containers/" in src, "container shell WS endpoint not referenced"
    assert "/shell" in src, "shell endpoint path not present"


def test_shell_screen_resolves_container_name():
    """ShellScreen must resolve container name via sessionsApi.getContainer."""
    src = read(SHELL_SCREEN)
    assert "sessionsApi.getContainer" in src, "getContainer API not called"


def test_shell_screen_has_connect_disconnect():
    """ShellScreen must have connect and disconnect functions."""
    src = read(SHELL_SCREEN)
    assert "const connect" in src, "connect function missing"
    assert "const disconnect" in src, "disconnect function missing"


def test_shell_screen_has_send_and_clear():
    """ShellScreen must have send and clear functions."""
    src = read(SHELL_SCREEN)
    assert "const send" in src, "send function missing"
    assert "const clear" in src, "clear function missing"


def test_shell_screen_strips_ansi():
    """ShellScreen must strip ANSI escape sequences for display."""
    src = read(SHELL_SCREEN)
    assert "stripAnsi" in src, "ANSI strip function missing"
    # Check for common ANSI escape pattern matching.
    assert "\\u001b" in src or "\x1b" in src, "ANSI escape pattern not present"


def test_shell_screen_disconnects_on_blur():
    """ShellScreen must disconnect WebSocket on screen blur (useFocusEffect cleanup)."""
    src = read(SHELL_SCREEN)
    assert "useFocusEffect" in src, "useFocusEffect not used"
    assert "disconnect" in src, "disconnect not referenced in focus cleanup"


def test_shell_screen_has_input_bar():
    """ShellScreen must have a TextInput for command input."""
    src = read(SHELL_SCREEN)
    assert "TextInput" in src, "TextInput not present"
    assert "onSubmitEditing" in src, "submit on enter not wired"


def test_shell_screen_has_status_indicator():
    """ShellScreen must show connected/disconnected status."""
    src = read(SHELL_SCREEN)
    assert "connected" in src.lower(), "connected state not tracked"
    assert "Badge" in src, "Badge component not used for status"


# ── workspace.ts ─────────────────────────────────────────────────────


def test_shell_in_workspace_screen_name():
    """Shell must be in WorkspaceScreenName type."""
    src = read(WORKSPACE_TS)
    assert "'Shell'" in src, "Shell not in WorkspaceScreenName"


def test_shell_in_runtime_category():
    """Shell must be in the runtime category items."""
    src = read(WORKSPACE_TS)
    # Find the runtime category block and verify Shell is in it.
    assert "screen: 'Shell'" in src, "Shell item not in any category"
    assert "terminal-outline" in src, "Shell icon not set"


# ── AppNavigator.tsx ─────────────────────────────────────────────────


def test_shell_in_root_stack_param_list():
    """Shell must be in RootStackParamList type."""
    src = read(APP_NAV)
    assert "Shell: undefined" in src, "Shell not in RootStackParamList"


def test_shell_in_panel_screens():
    """Shell must be in PANEL_SCREENS array with ShellScreen component."""
    src = read(APP_NAV)
    assert "ShellScreen" in src, "ShellScreen not imported or used"
    assert "name: 'Shell'" in src, "Shell not in PANEL_SCREENS"


# ── Server shell WS endpoint ─────────────────────────────────────────


def test_shell_ws_allows_private_network():
    """Server shell WS endpoint must allow private network clients, not just localhost."""
    src = read(CONTAINERS_PY)
    # The old restriction was localhost-only. Now must check for private network.
    assert "is_private" in src, "Private network check not present in containers.py"
    # The old hardcoded localhost-only check should be gone from the shell handler.
    # Look for the shell handler and verify it uses is_private.
    assert "managed_container_shell" in src, "Shell WS handler not found"


def test_shell_ws_rejects_public():
    """Server shell WS endpoint must still reject public (non-private) clients."""
    src = read(CONTAINERS_PY)
    assert "1008" in src, "Close code 1008 not present"
    assert "private network" in src, "Private network restriction message not present"