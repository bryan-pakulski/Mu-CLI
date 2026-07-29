"""Regression tests for mobile streaming performance fixes.

Four root causes of slow mobile streaming (web GUI on same session was fine):

1. SSE pollingInterval: 3000 → react-native-sse batches events and polls on
   a 3s interval instead of pushing them. Web uses native EventSource (push).
2. scrollToEnd called with animation on every onContentSizeChange (every token)
   — animated scroll per token is extremely expensive on mobile.
3. Markdown re-parsed on every streaming delta — react-native-markdown-display
   rebuilds the full AST on every text change. Plain Text while streaming.
4. Session poll timer (2.5s) fires syncSessionState during active SSE streaming
   — redundant network calls + potential history reloads mid-stream.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SSE = ROOT / "mobile/android/src/api/sse.ts"
CHAT_SCREEN = ROOT / "mobile/android/src/screens/ChatScreen.tsx"
CHAT_HOOK = ROOT / "mobile/android/src/hooks/useChatSession.ts"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ── Fix 1: SSE pollingInterval ──────────────────────────────────────────────

def test_sse_polling_interval_is_zero():
    """pollingInterval must be 0 (push-based), not 3000 (batched polling)."""
    src = read(SSE)
    assert "pollingInterval: 0" in src, "pollingInterval not set to 0"
    assert "pollingInterval: 3000" not in src, "old pollingInterval: 3000 still present"


def test_sse_has_comment_explaining_polling():
    """Verify the rationale comment exists so no one reverts it accidentally."""
    src = read(SSE)
    assert "push" in src.lower() or "disable polling" in src.lower(), \
        "Missing rationale comment for pollingInterval: 0"


# ── Fix 2: Throttled scrollToEnd ─────────────────────────────────────────────

def test_chatscreen_has_throttled_scroll():
    """scrollToEnd must be throttled, not called on every content-size change."""
    src = read(CHAT_SCREEN)
    assert "scrollThrottleRef" in src, "Throttle ref not found"
    assert "setTimeout" in src, "setTimeout not used for throttle"
    assert "animated: false" in src, "scrollToEnd still uses animation"
    # The old animated-per-token pattern must be gone
    assert "scrollToEnd({ animated: messages.length > 0 })" not in src, \
        "Old animated scrollToEnd pattern still present"


def test_chatscreen_clears_throttle_on_unmount():
    """Throttle timer must be cleaned up on unmount to avoid leaks."""
    src = read(CHAT_SCREEN)
    assert "clearTimeout(scrollThrottleRef" in src, \
        "Throttle cleanup on unmount not found"


def test_chatscreen_uses_callback_for_scroll():
    """onContentSizeChange and onLayout should use the throttled callback."""
    src = read(CHAT_SCREEN)
    assert "onContentSizeChange={scrollToBottom}" in src, \
        "onContentSizeChange not using throttled callback"
    assert "onLayout={scrollToBottom}" in src, \
        "onLayout not using throttled callback"


# ── Fix 3: Plain Text during streaming ───────────────────────────────────────

def test_chatscreen_skips_markdown_while_streaming():
    """Assistant messages with streaming=true must render plain Text, not Markdown."""
    src = read(CHAT_SCREEN)
    assert "item.streaming" in src, \
        "No streaming check before Markdown render found"
    # The pattern: isUser ? Text : item.streaming ? Text : Markdown
    assert "? (" in src and "item.streaming" in src, \
        "Conditional streaming render path not found"


def test_chatscreen_markdown_only_on_finalize():
    """Markdown component must only render for non-streaming assistant messages."""
    src = read(CHAT_SCREEN)
    # After the streaming Text branch, Markdown should be the else case
    assert "item.streaming ? (" in src, "Streaming branch not found"
    # Ensure Markdown import still exists (not removed)
    assert "from 'react-native-markdown-display'" in src, \
        "Markdown import missing — was it accidentally removed?"


# ── Fix 4: Skip session polling during active streaming ─────────────────────

def test_hook_skips_poll_during_streaming():
    """syncSessionState poll must skip when SSE connected + busy."""
    src = read(CHAT_HOOK)
    assert "sseConnectedRef.current && busyRef.current" in src, \
        "Skip-poll guard not found in useChatSession"
    # The guard must be inside the interval callback
    assert "return;" in src, "Guard doesn't return early"


def test_hook_poll_guard_before_sync():
    """The skip-poll guard must come before the syncSessionState call."""
    src = read(CHAT_HOOK)
    guard_idx = src.find("sseConnectedRef.current && busyRef.current")
    sync_idx = src.find("void syncSessionState();", guard_idx)
    assert guard_idx >= 0 and sync_idx > guard_idx, \
        "Guard must appear before syncSessionState call in poll"


def test_hook_still_polls_when_idle():
    """The poll guard must only skip when BOTH connected AND busy."""
    src = read(CHAT_HOOK)
    # The guard uses && (both conditions), not || (either condition)
    guard_idx = src.find("sseConnectedRef.current && busyRef.current")
    assert guard_idx >= 0, "Guard expression not found"
    snippet = src[guard_idx:guard_idx + 100]
    assert "&&" in snippet, "Guard should use && not ||"
    assert "||" not in snippet, "Guard should use && not ||"