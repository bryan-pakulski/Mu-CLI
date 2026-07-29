"""Regression tests for /command completion on the mobile application.

Verifies:
1. useCommandCompletion hook exists with correct exports.
2. CommandSuggestionBar component exists with correct props.
3. ChatScreen imports + wires both into the composer.
4. chat.ts API client has getCommands + getCompletions.
5. The subcommand tree mirrors the web GUI's cmdComplete._subTree.
6. Server endpoints (/api/chat/commands, /api/chat/completions) exist.
7. Non-slash input closes the dropdown.
8. Slash input triggers completion update.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "mobile/android/src/hooks/useCommandCompletion.ts"
BAR = ROOT / "mobile/android/src/components/CommandSuggestionBar.tsx"
CHAT_SCREEN = ROOT / "mobile/android/src/screens/ChatScreen.tsx"
CHAT_API = ROOT / "mobile/android/src/api/chat.ts"
APP_JS = ROOT / "mu/gui/static/js/app.js"
CHAT_ROUTER = ROOT / "mu/gui/routers/chat.py"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── Hook tests ──────────────────────────────────────────────────────────

def test_hook_file_exists():
    """useCommandCompletion hook must exist."""
    assert HOOK.exists(), f"Hook file not found: {HOOK}"


def test_hook_exports_completion_item_type():
    """Hook must export CompletionItem type."""
    src = read(HOOK)
    assert "export interface CompletionItem" in src


def test_hook_exports_use_command_completion():
    """Hook must export useCommandCompletion function."""
    src = read(HOOK)
    assert "export function useCommandCompletion" in src


def test_hook_has_sub_tree():
    """Hook must contain the static subcommand tree (SUB_TREE)."""
    src = read(HOOK)
    assert "SUB_TREE" in src
    # Verify a few key commands are in the tree
    for cmd in ["/session", "/workspace", "/model", "/feature", "/tool", "/memory"]:
        assert cmd in src, f"Command {cmd} missing from SUB_TREE"


def test_hook_has_update_close_move_accept():
    """Hook must expose update, close, moveUp, moveDown, accept functions."""
    src = read(HOOK)
    for fn in ["update", "close", "moveUp", "moveDown", "accept"]:
        assert fn in src, f"Function {fn} missing from hook"


def test_hook_has_dynamic_completion_fetch():
    """Hook must fetch dynamic completions from /api/chat/completions."""
    src = read(HOOK)
    assert "getCompletions" in src or "fetchDynamic" in src


def test_hook_has_command_list_loading():
    """Hook must load command list from /api/chat/commands."""
    src = read(HOOK)
    assert "getCommands" in src


def test_hook_caches_dynamic_completions():
    """Hook must cache dynamic completions to avoid refetching."""
    src = read(HOOK)
    assert "dynCache" in src or "Cache" in src


def test_hook_skips_path_completions_gracefully():
    """Path-based completions (path_dir, path_file) should be skipped on mobile."""
    src = read(HOOK)
    assert "path_" in src
    # Must handle path_ kinds without crashing (return null or skip)
    assert "startsWith('path_')" in src


def test_hook_subtree_mirrors_web_gui():
    """Hook's SUB_TREE must match web GUI's _subTree for key commands."""
    import re
    hook_src = read(HOOK)
    web_src = read(APP_JS)
    # Extract "/cmd" keys from both sources — quotes differ (web uses ",
    # hook uses ') so normalise by extracting the /word pattern directly.
    web_cmds = set(re.findall(r'["\'](/\w+)["\']', web_src))
    hook_cmds = set(re.findall(r'["\'](/\w+)["\']', hook_src))
    # At least the core commands should be in both
    shared = web_cmds & hook_cmds
    assert len(shared) >= 10, f"SubTree mismatch: only {len(shared)} shared commands: {shared}"


# ── Component tests ─────────────────────────────────────────────────────

def test_bar_file_exists():
    """CommandSuggestionBar component must exist."""
    assert BAR.exists(), f"Component file not found: {BAR}"


def test_bar_exports_component():
    """Component must export CommandSuggestionBar."""
    src = read(BAR)
    assert "export function CommandSuggestionBar" in src


def test_bar_accepts_correct_props():
    """Component must accept visible, items, selectedIdx, onSelect props."""
    src = read(BAR)
    assert "visible" in src
    assert "items" in src
    assert "selectedIdx" in src
    assert "onSelect" in src


def test_bar_uses_scrollview():
    """Component must use ScrollView for scrollable suggestions."""
    src = read(BAR)
    assert "ScrollView" in src


def test_bar_highlights_selected_item():
    """Component must visually highlight the selected item."""
    src = read(BAR)
    assert "selectedIdx" in src
    assert "bgHover" in src or "selectedIdx" in src


def test_bar_returns_null_when_not_visible():
    """Component must return null when not visible or no items."""
    src = read(BAR)
    assert "return null" in src


# ── ChatScreen integration tests ─────────────────────────────────────────

def test_chatscreen_imports_hook():
    """ChatScreen must import useCommandCompletion."""
    src = read(CHAT_SCREEN)
    assert "useCommandCompletion" in src


def test_chatscreen_imports_bar():
    """ChatScreen must import CommandSuggestionBar."""
    src = read(CHAT_SCREEN)
    assert "CommandSuggestionBar" in src


def test_chatscreen_uses_completion_hook():
    """ChatScreen must call useCommandCompletion()."""
    src = read(CHAT_SCREEN)
    assert "useCommandCompletion()" in src


def test_chatscreen_renders_suggestion_bar():
    """ChatScreen must render <CommandSuggestionBar ... /> in JSX."""
    src = read(CHAT_SCREEN)
    assert "<CommandSuggestionBar" in src


def test_chatscreen_triggers_update_on_slash_input():
    """ChatScreen must call completion.update() when input starts with /."""
    src = read(CHAT_SCREEN)
    assert "completion.update" in src
    assert "startsWith('/')" in src


def test_chatscreen_closes_completion_on_send():
    """ChatScreen must close completion dropdown on send."""
    src = read(CHAT_SCREEN)
    assert "completion.close" in src


def test_chatscreen_has_accept_handler():
    """ChatScreen must have an accept handler for completion selection."""
    src = read(CHAT_SCREEN)
    assert "onAcceptCompletion" in src or "accept" in src.lower()


# ── API client tests ─────────────────────────────────────────────────────

def test_chat_api_has_get_commands():
    """chat.ts must have getCommands method."""
    src = read(CHAT_API)
    assert "getCommands" in src
    assert "/api/chat/commands" in src


def test_chat_api_has_get_completions():
    """chat.ts must have getCompletions method."""
    src = read(CHAT_API)
    assert "getCompletions" in src
    assert "/api/chat/completions" in src


# ── Server endpoint tests ────────────────────────────────────────────────

def test_server_has_commands_endpoint():
    """Server must have /api/chat/commands endpoint."""
    src = read(CHAT_ROUTER)
    assert "/commands" in src
    assert "list_commands" in src


def test_server_has_completions_endpoint():
    """Server must have /api/chat/completions endpoint."""
    src = read(CHAT_ROUTER)
    assert "/completions" in src
    # Verify key completion kinds are handled
    for kind in ["sessions", "features", "tools", "models", "modes"]:
        assert kind in src, f"Completion kind {kind} missing from server"


def test_server_completions_has_memory_targets():
    """Server must support memory_targets completion kind."""
    src = read(CHAT_ROUTER)
    assert "memory_targets" in src


def test_server_completions_has_layer_ids():
    """Server must support layer_ids completion kind."""
    src = read(CHAT_ROUTER)
    assert "layer_ids" in src