"""Regression tests for mobile view-flash / missing-final-output bug.

Root cause: ``useChatSession`` called ``loadHistory(false)`` on
``turn_complete`` and on busy→not-busy transitions in
``syncSessionState``. The server hadn't persisted the final assistant
message yet, so loadHistory replaced live-streamed messages with stale
history (missing the last turn) — the screen flashed and the final
output vanished. The web GUI never had this bug: it only calls
``chat.finishTurn(name)`` on ``turn_complete`` and waits for the
``history_refresh`` SSE event to reload.

Fix:
1. ``turn_complete`` handler: removed ``loadHistory(false)`` call.
   Added 3s fallback timer in case ``history_refresh`` never arrives.
2. ``syncSessionState`` busy→not-busy: skip ``loadHistory`` when SSE
   connected (wait for ``history_refresh``).
3. ``error`` handler: same guard — skip ``loadHistory`` when SSE
   connected.
4. ``history_refresh`` handler: cancel the 3s fallback timer.
5. Session-change + unmount: clear the fallback timer.
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "mobile/android/src/hooks/useChatSession.ts"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ─── turn_complete: no loadHistory ──────────────────────────────────

def test_turn_complete_does_not_call_load_history():
    """The turn_complete handler must NOT call loadHistory directly.

    The server may not have persisted the final message yet. Calling
    loadHistory here replaces live messages with stale history.
    """
    src = read(HOOK)
    # Extract the turn_complete block
    match = re.search(
        r"if \(kind === 'turn_complete'\) \{.*?\n    \}",
        src,
        re.DOTALL,
    )
    assert match, "turn_complete handler not found"
    block = match.group()
    assert "loadHistory(false)" not in block.replace(
        "historyFallbackRef.current = setTimeout(() => {\n"
        "        historyFallbackRef.current = null;\n"
        "        void loadHistory(false);\n"
        "      }, 3000);",
        "",
    ), "turn_complete must not call loadHistory directly (only via 3s fallback)"


def test_turn_complete_has_fallback_timer():
    """A 3s fallback timer must force loadHistory if history_refresh
    never arrives."""
    src = read(HOOK)
    match = re.search(
        r"if \(kind === 'turn_complete'\) \{.*?\n    \}",
        src,
        re.DOTALL,
    )
    assert match, "turn_complete handler not found"
    block = match.group()
    assert "historyFallbackRef" in block, "fallback timer ref not in turn_complete"
    assert "setTimeout" in block, "setTimeout not in turn_complete fallback"
    assert "3000" in block, "fallback timeout must be 3000ms"


def test_turn_complete_sets_busy_false():
    """turn_complete must still set busy=false and streaming=false."""
    src = read(HOOK)
    match = re.search(
        r"if \(kind === 'turn_complete'\) \{.*?\n    \}",
        src,
        re.DOTALL,
    )
    assert match, "turn_complete handler not found"
    block = match.group()
    assert "busyRef.current = false" in block
    assert "setStreaming(false)" in block


def test_turn_complete_increments_artifact_revision():
    """turn_complete must still increment artifactRevision."""
    src = read(HOOK)
    match = re.search(
        r"if \(kind === 'turn_complete'\) \{.*?\n    \}",
        src,
        re.DOTALL,
    )
    assert match, "turn_complete handler not found"
    block = match.group()
    assert "setArtifactRevision" in block


# ─── syncSessionState: skip loadHistory on busy→not-busy + SSE ──────

def test_sync_session_state_skips_load_history_when_sse_connected():
    """syncSessionState must skip loadHistory on busy→not-busy when
    SSE is connected — the history_refresh event will handle it."""
    src = read(HOOK)
    # Find the syncSessionState not-busy branch
    match = re.search(
        r"else \{.*?setWaitingForFirstToken\(false\).*?setActivityLabel\('Thinking'\).*?\}",
        src,
        re.DOTALL,
    )
    assert match, "syncSessionState not-busy branch not found"
    block = match.group()
    assert "sseConnectedRef.current" in block, (
        "syncSessionState must check sseConnectedRef before loadHistory"
    )
    # The wasBusy + sseConnectedRef guard must be present
    assert "wasBusy" in block, "wasBusy check missing"
    assert "sseConnectedRef.current" in block


def test_sync_session_state_still_loads_on_external_write():
    """syncSessionState must still loadHistory on external write
    (e.g. CLI wrote to session) even if not transitioning from busy."""
    src = read(HOOK)
    match = re.search(
        r"else \{.*?setWaitingForFirstToken\(false\).*?setActivityLabel\('Thinking'\).*?(?:\n      \}\n      \}|$)",
        src,
        re.DOTALL,
    )
    assert match, "syncSessionState not-busy branch not found"
    block = match.group()
    assert "sawExternalWrite" in block
    assert "loadHistory(false)" in block


def test_sync_session_state_still_loads_on_unhydrated():
    """syncSessionState must still loadHistory if the session hasn't
    been hydrated yet (e.g. initial load)."""
    src = read(HOOK)
    match = re.search(
        r"else \{.*?setWaitingForFirstToken\(false\).*?setActivityLabel\('Thinking'\).*?(?:\n      \}\n      \}|$)",
        src,
        re.DOTALL,
    )
    assert match, "syncSessionState not-busy branch not found"
    block = match.group()
    assert "historyHydratedRef" in block


# ─── error handler: skip loadHistory when SSE connected ─────────────

def test_error_handler_skips_load_history_when_sse_connected():
    """The error handler must skip loadHistory when SSE is connected."""
    src = read(HOOK)
    match = re.search(
        r"if \(kind === 'error'\) \{.*?\n    \}",
        src,
        re.DOTALL,
    )
    assert match, "error handler not found"
    block = match.group()
    assert "sseConnectedRef.current" in block, (
        "error handler must guard loadHistory with sseConnectedRef"
    )


# ─── history_refresh: cancel fallback timer ─────────────────────────

def test_history_refresh_cancels_fallback_timer():
    """history_refresh handler must cancel the 3s fallback timer —
    the server has persisted, so the fallback is no longer needed."""
    src = read(HOOK)
    match = re.search(
        r"if \(kind === 'history_refresh'\) \{.*?\n    \}",
        src,
        re.DOTALL,
    )
    assert match, "history_refresh handler not found"
    block = match.group()
    assert "historyFallbackRef" in block
    assert "clearTimeout" in block


def test_history_refresh_still_loads_history():
    """history_refresh must still call loadHistory — this is the
    correct trigger for reloading after server persistence."""
    src = read(HOOK)
    match = re.search(
        r"if \(kind === 'history_refresh'\) \{.*?\n    \}",
        src,
        re.DOTALL,
    )
    assert match, "history_refresh handler not found"
    block = match.group()
    assert "loadHistory(false)" in block


# ─── fallback timer ref + cleanup ───────────────────────────────────

def test_history_fallback_ref_declared():
    """historyFallbackRef must be declared as a useRef."""
    src = read(HOOK)
    assert "historyFallbackRef" in src
    assert "useRef" in src


def test_fallback_timer_cleared_on_session_change():
    """Session change must clear the fallback timer to prevent
    a stale timer firing loadHistory on the wrong session."""
    src = read(HOOK)
    # Find sessionChanged block — use greedy match to capture nested braces
    match = re.search(
        r"if \(sessionChanged\) \{.*?setSseConnected\(false\)",
        src,
        re.DOTALL,
    )
    assert match, "sessionChanged block not found"
    block = match.group()
    assert "historyFallbackRef" in block
    assert "clearTimeout" in block


def test_fallback_timer_cleared_on_unmount():
    """The unmount cleanup must clear the fallback timer."""
    src = read(HOOK)
    # Find the return cleanup function
    match = re.search(
        r"return \(\) => \{.*?subscriptionRef\.current\?\.close\(\);.*?\};",
        src,
        re.DOTALL,
    )
    assert match, "unmount cleanup not found"
    block = match.group()
    assert "historyFallbackRef" in block
    assert "clearTimeout" in block


def test_completion_probe_also_cleared_on_session_change():
    """The existing completionProbeRef timer should also be cleared
    on session change (was not before — minor cleanup)."""
    src = read(HOOK)
    match = re.search(
        r"if \(sessionChanged\) \{.*?\}",
        src,
        re.DOTALL,
    )
    assert match, "sessionChanged block not found"
    block = match.group()
    assert "completionProbeRef" in block


# ─── web GUI comparison: no loadHistory on turn_complete ────────────

def test_web_gui_turn_complete_does_not_call_load_history():
    """The web GUI must also NOT call loadHistory on turn_complete.
    This documents the pattern the mobile fix mirrors."""
    app_js = ROOT / "mu/gui/static/js/app.js"
    src = read(app_js)
    match = re.search(
        r'case "turn_complete":(.*?)(?:case "|break;|\n        \})',
        src,
        re.DOTALL,
    )
    assert match, "turn_complete case not found in app.js"
    block = match.group()
    assert "loadHistory" not in block, (
        "web GUI turn_complete must not call loadHistory — "
        "it waits for history_refresh"
    )
    assert "finishTurn" in block, "web GUI must call finishTurn"


def test_web_gui_history_refresh_calls_load_history():
    """The web GUI reloads on history_refresh — confirms this is
    the correct trigger."""
    app_js = ROOT / "mu/gui/static/js/app.js"
    src = read(app_js)
    match = re.search(
        r'case "history_refresh":(.*?)(?:case "|break;|\n        \})',
        src,
        re.DOTALL,
    )
    assert match, "history_refresh case not found in app.js"
    block = match.group()
    assert "loadHistory" in block