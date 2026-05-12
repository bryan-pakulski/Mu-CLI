"""Reproduce the user's bug: after Ctrl+C, `/continue` does nothing.

Traces the actual state transitions and what `/continue` re-sends, so we
can see whether the bug is (a) `paused_execution_text` being cleared,
(b) the resumed call producing no provider invocation, or (c) the
resumed prompt being a duplicate that the model treats as already-done.
"""

import pytest

from core.session import Session, SessionManager
from providers.base import LLMProvider, Message, MessagePart, ProviderResponse


class _CountingProvider(LLMProvider):
    """Records every prompt the harness sends. After the first call,
    raises KeyboardInterrupt to simulate the user pressing Ctrl+C while
    the model is generating."""

    def __init__(self):
        super().__init__("counter")
        self.name = "counter"
        self.calls: list = []
        self.interrupt_on_call = 1  # 1 = raise on first call

    def get_available_models(self):
        return ["counter"]

    def generate(self, messages, system_prompt=None, thinking=False, tools=None):
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_msgs": [
                    "\n".join(p.text or "" for p in m.parts if p.type == "text")
                    for m in messages
                    if m.role == "user"
                ],
            }
        )
        if len(self.calls) == self.interrupt_on_call:
            raise KeyboardInterrupt()
        # Subsequent calls return a clean text response so the loop exits.
        return ProviderResponse(
            text="Resumed and finished.",
            parts=[],
            input_tokens=0,
            output_tokens=0,
            total_tokens=0,
        )

    def upload_file(self, file_path, mime_type):
        return None


def _make_session():
    sm = SessionManager()
    return Session(_CountingProvider(), False, "sys", sm)


def test_keyboard_interrupt_records_paused_text():
    session = _make_session()
    session.agentic = False  # one-shot; avoids tool/loop machinery
    result = session.send_message("do the thing")
    assert result.get("status") == "interrupted"
    assert session.paused_execution_text == "do the thing", (
        f"expected paused text to be saved, got {session.paused_execution_text!r}"
    )


def test_continue_after_interrupt_actually_calls_provider_again():
    """The actual bug repro: Ctrl+C → /continue must trigger a second
    provider call. If `paused_execution_text` is cleared at the wrong
    point, or `/continue` no-ops, the provider only sees one call."""
    session = _make_session()
    session.agentic = False
    session.send_message("do the thing")
    assert session.paused_execution_text == "do the thing"

    # Simulate the /continue path from mucli.py:924.
    paused = session.paused_execution_text
    session.provider.interrupt_on_call = 99  # don't interrupt resume
    resume_result = session.send_message(paused)

    assert len(session.provider.calls) == 2, (
        f"expected provider to be called twice (initial + resume), "
        f"saw {len(session.provider.calls)} calls"
    )
    assert resume_result.get("status") != "interrupted"


def test_continue_resends_original_prompt_verbatim():
    """The current /continue implementation re-sends the ORIGINAL user
    text. This is observable: the second provider call sees the same
    prompt the user typed before Ctrl+C."""
    session = _make_session()
    session.agentic = False
    session.send_message("analyze this codebase")
    session.provider.interrupt_on_call = 99

    session.send_message(session.paused_execution_text)

    second = session.provider.calls[1]
    # The new user message in the second call is the original prompt.
    assert any("analyze this codebase" in m for m in second["user_msgs"]), (
        f"resumed call should re-send original prompt; got {second['user_msgs']!r}"
    )


def test_continue_command_resends_last_prompt_verbatim():
    """The /continue command must re-send the LAST PROMPT TO THE MODEL
    verbatim — a clean restart of the interrupted turn. No wrapping,
    no continuation directive, no synthetic markers. The user's mental
    model is "go back to the last prompt and re-send it"."""
    from mucli import handle_command

    session = _make_session()
    session.agentic = False
    session.send_message("audit the auth module")
    assert session.paused_execution_text == "audit the auth module"
    session.provider.interrupt_on_call = 99

    result = handle_command(session, "/continue", allow_prompt=False)
    assert result.get("ok") is True

    # Two provider calls total: initial (interrupted) + the continue.
    assert len(session.provider.calls) == 2
    second_user_msgs = session.provider.calls[1]["user_msgs"]

    # The new user message in the second call is the original prompt,
    # not a wrapped/decorated version.
    assert "audit the auth module" in "\n".join(second_user_msgs), (
        f"resumed call should re-send the original prompt verbatim, got "
        f"user_msgs={second_user_msgs!r}"
    )
    # Specifically: nothing should be added by /continue. The data echoes
    # the resumed_text exactly so callers can confirm.
    assert result["data"]["resumed_text"] == "audit the auth module"


def test_continue_with_no_paused_text_returns_clear_error():
    """If the user runs /continue without a prior interruption, they
    should get a clear "nothing to continue" message instead of a
    silent no-op."""
    from mucli import handle_command

    session = _make_session()
    assert session.paused_execution_text is None
    result = handle_command(session, "/continue", allow_prompt=False)
    assert result.get("ok") is False
    assert "no paused" in result.get("message", "").lower()


def test_interrupt_appends_interruption_marker_to_history():
    """The history must include a "User interrupted execution." marker
    so the model on resume knows it was paused."""
    session = _make_session()
    session.agentic = False
    session.send_message("do it")

    last_tool_results = [
        p.get("tool_result")
        for m in session.session_manager.history
        for p in m.get("parts", [])
        if p.get("type") == "tool_result"
    ]
    assert any(
        "interrupted" in str(r).lower() for r in last_tool_results
    ), "expected an 'interrupted' marker in history after Ctrl+C"
