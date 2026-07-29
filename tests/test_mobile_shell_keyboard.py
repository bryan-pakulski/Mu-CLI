"""Regression tests: mobile shell keyboard stays open after send.

Bug: after each command send in ShellScreen, keyboard auto-dismissed.
Root cause: TextInput default ``blurOnSubmit=true`` blurs on submit +
``setInput('')`` in ``send()`` can cause keyboard dismissal on some
platforms. Fix: ``blurOnSubmit={false}`` + explicit ``inputRef.focus()``
re-focus in ``send()`` after clearing input.

These tests verify the source-level fixes are present.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHELL_SCREEN = ROOT / "mobile/android/src/screens/ShellScreen.tsx"


def read() -> str:
    return SHELL_SCREEN.read_text(encoding="utf-8")


# ── inputRef ──────────────────────────────────────────────────────────


def test_input_ref_declared():
    """A ref for the TextInput must exist so send() can re-focus."""
    src = read()
    assert "inputRef" in src, "inputRef not found in ShellScreen"
    assert "useRef<TextInput>" in src, "inputRef must be useRef<TextInput>"


def test_input_ref_attached_to_textinput():
    """The ref must be wired to the TextInput element."""
    src = read()
    assert "ref={inputRef}" in src, "ref={inputRef} not attached to TextInput"


# ── blurOnSubmit ──────────────────────────────────────────────────────


def test_blur_on_submit_is_false():
    """``blurOnSubmit={false}`` prevents keyboard dismiss on enter."""
    src = read()
    assert "blurOnSubmit={false}" in src, "blurOnSubmit={false} missing"


# ── send() re-focus ──────────────────────────────────────────────────


def test_send_refocuses_input():
    """send() must re-focus input after clearing to keep keyboard open."""
    src = read()
    assert "inputRef.current?.focus()" in src, "send() does not re-focus input"


def test_send_uses_request_animation_frame_for_refocus():
    """Re-focus wrapped in rAF to avoid race with state update."""
    src = read()
    send_start = src.index("const send = useCallback(")
    send_end = src.index("}, [input, appendOutput]);", send_start)
    send_body = src[send_start:send_end]
    assert "requestAnimationFrame" in send_body, "rAF not in send()"
    assert "inputRef.current?.focus()" in send_body, "focus() not in send() rAF"


def test_send_clears_input():
    """send() must still clear input (basic functionality preserved)."""
    src = read()
    assert "setInput('')" in src, "send() does not clear input"


# ── no auto-dismiss patterns ──────────────────────────────────────────


def test_no_keyboard_dismiss_import():
    """No Keyboard.dismiss() call should exist in the shell screen."""
    src = read()
    assert "Keyboard.dismiss" not in src, "Keyboard.dismiss present — would close keyboard"


def test_no_blur_on_submit_true():
    """No blurOnSubmit={true} should appear anywhere."""
    src = read()
    assert "blurOnSubmit={true}" not in src, "blurOnSubmit={true} found — would dismiss keyboard"


# ── integration: send + refocus flow ─────────────────────────────────


def test_send_function_has_refocus_after_clear():
    """Verify send() flow: clear input then re-focus, in that order."""
    src = read()
    send_start = src.index("const send = useCallback(")
    send_end = src.index("}, [input, appendOutput]);", send_start)
    send_body = src[send_start:send_end]
    clear_idx = send_body.index("setInput('')")
    focus_idx = send_body.index("inputRef.current?.focus()")
    assert clear_idx < focus_idx, "Re-focus must come after input clear in send()"


def test_textinput_has_return_key_send():
    """returnKeyType='send' must still be present for UX."""
    src = read()
    assert 'returnKeyType="send"' in src, "returnKeyType='send' missing"