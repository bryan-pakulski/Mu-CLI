"""Regression guards for chat history, density and completed-turn timing."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB_SHELL = ROOT / "mu" / "gui" / "static" / "js" / "web_shell.js"
WASH = ROOT / "mu" / "gui" / "static" / "css" / "wash"
MESSAGE_CSS = WASH / "components" / "message.css"
CHAT_CSS = WASH / "patterns" / "chat.css"
CHAT_HTML = ROOT / "mu" / "gui" / "templates" / "fragments" / "chat.html"


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
    # Wash W3 (G§9.7): interim turns collapse into one sunken "Worked for 2m ·
    # 14 steps ›" row (radius-md, 36px); expanded children hang off a single
    # hairline, never a stack of rules. conversation.css is retired.
    css = MESSAGE_CSS.read_text(encoding="utf-8")
    html = CHAT_HTML.read_text(encoding="utf-8")
    row = css.split(".w-worked__row {", 1)[1].split("}", 1)[0]
    assert "min-height: 36px" in row
    assert "background: var(--wash-sunken)" in row
    assert "border-radius: var(--wash-radius-md)" in row
    body = css.split(".w-worked__body {", 1)[1].split("}", 1)[0]
    assert "border-left: 1px solid var(--wash-hairline)" in body
    assert "border-top" not in css.split(".w-worked", 1)[1]
    assert 'class="collapse-group w-worked"' in html
    assert 'x-text="summarizeCollapse(t)"' in html
    assert ".turn-worked-breadcrumb" in CHAT_CSS.read_text(encoding="utf-8")
    assert "!important" not in css


def test_main_response_spacing_is_compact():
    # Wash W3: the transcript is one flex column with a 12px rhythm (gap) and
    # a 24px top inset; messages carry no margins of their own.
    css = MESSAGE_CSS.read_text(encoding="utf-8")
    transcript = css.split(".w-transcript {", 1)[1].split("}", 1)[0]
    assert "gap: var(--wash-space-3)" in transcript
    assert "padding: var(--wash-space-6)" in transcript
    assert ".chat-history .msg { margin: 0; border: 0; }" in CHAT_CSS.read_text(encoding="utf-8")


