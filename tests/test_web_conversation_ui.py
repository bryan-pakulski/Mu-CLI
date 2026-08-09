"""Regression guards for chat history, density and completed-turn timing."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_SHELL = ROOT / "mu" / "gui" / "static" / "js" / "web_shell.js"
CONVERSATION_CSS = ROOT / "mu" / "gui" / "static" / "css" / "conversation.css"


def test_history_waits_for_authoritative_session_focus():
    js = WEB_SHELL.read_text(encoding="utf-8")
    assert "const coreLoadHistory = chat.loadHistory.bind(chat)" in js
    assert "const target = name || this.currentName || sessions.current || null" in js
    assert "if (!target) return" in js
    assert "__authoritativeHydrated" in js
    assert "await chat.loadHistory(current, { force: true })" in js
    assert "await chat.loadHistory(name, { force: true })" in js


def test_busy_refresh_hydrates_saved_transcript_without_wiping_live_output():
    js = WEB_SHELL.read_text(encoding="utf-8")
    assert "function hasLiveTranscript(slot)" in js
    assert "if (wasBusy && hasLiveTranscript(slot))" in js
    assert "slot.pendingReload = true" in js
    assert "force: true" in js
    assert "hydrated.busy = true" in js
    assert "this._ensureBusyTrace(hydrated)" in js


def test_completed_model_turns_receive_worked_duration_metadata():
    js = WEB_SHELL.read_text(encoding="utf-8")
    assert "slot.__turnStartedAt = Date.now()" in js
    assert "finalResponse.workedMs" in js
    assert "Worked for ${minutes}m ${seconds}s" in js
    assert "Worked for ${seconds}s" in js
    assert "__skipWorkedFinish" in js
    assert "turn-worked-breadcrumb" in js
    assert "workedFingerprintMap" in js
    assert "restoreWorkedFingerprints" in js


def test_interim_updates_are_compact_and_do_not_stack_horizontal_rules():
    css = CONVERSATION_CSS.read_text(encoding="utf-8")
    assert ".chat-history .collapse-group" in css
    assert "margin: 3px 0 13px !important" in css
    assert ".chat-history .collapse-header" in css
    assert "min-height: 28px !important" in css
    assert ".chat-history .collapse-body" in css
    assert "border-left: 1px solid var(--hairline) !important" in css
    assert ".chat-history .collapse-body .trace" in css
    assert "border-top: 0 !important" in css
    assert ".turn-worked-breadcrumb" in css


def test_main_response_spacing_is_compact():
    css = CONVERSATION_CSS.read_text(encoding="utf-8")
    assert ".chat-history > .turn-wrap > .msg" in css
    assert "margin-bottom: 24px !important" in css
    assert "padding-top: 36px !important" in css
