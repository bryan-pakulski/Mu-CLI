"""Wash web shell (W3 signature components, W4 `/` page) contracts.

Covers the safety-critical approval card (no auto-focused Approve, card-scoped
shortcuts, assertive announcement, unchanged /api/prompts payloads), DOMPurify
in renderMarkdown, the single shared tool-card renderer, the additive
`auto: true` flag on YOLO `prompt_resolved` events, and the page-agnostic
command palette registration API.
"""

from __future__ import annotations

import re
from pathlib import Path

import mu.tools  # noqa: F401  (registers tool descriptors)
from mu.agent.approval import auto_approved_by_yolo, notify_auto_approval
from mu.gui.bus import EventBus
from mu.gui.prompts import PromptStore
from mu.gui.web_ui import WebUI

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "mu" / "gui"
APP_JS = (GUI / "static/js/app.js").read_text(encoding="utf-8")
CHAT = (GUI / "templates/fragments/chat.html").read_text(encoding="utf-8")
INDEX = (GUI / "templates/index.html").read_text(encoding="utf-8")
BASE = (GUI / "templates/base.html").read_text(encoding="utf-8")
TOOLCARD_JS = (GUI / "static/js/wash/toolcard.js").read_text(encoding="utf-8")
DIFF_JS = (GUI / "static/js/wash/diff.js").read_text(encoding="utf-8")
PALETTE_JS = (GUI / "static/js/wash/palette.js").read_text(encoding="utf-8")
CHAT_PALETTE_JS = (GUI / "static/js/wash/chat_palette.js").read_text(encoding="utf-8")
LOOP_BODY = (ROOT / "mu/agent/loop_body.py").read_text(encoding="utf-8")


def _approval_card() -> str:
    start = CHAT.index('<section class="msg prompt w-approval"')
    return CHAT[start:CHAT.index("</section>", start)]


def _func(source: str, name: str) -> str:
    start = source.index(name)
    return source[start:start + 2400]


# ------------------------------------------------------------------ approval card

def test_approval_card_never_autofocuses_approve():
    card = _approval_card()
    approve = re.search(r"<button[^>]*data-approve[^>]*>", card, flags=re.S).group(0)
    assert 'data-variant="primary"' in approve
    assert "autofocus" not in card
    assert "focus" not in approve
    # Focus goes to the heading on appear, never to a button.
    assert 'x-init="$nextTick(() => focusOnAppear($el))"' in card
    assert "data-approval-heading" in card
    focus = _func(APP_JS, "focusOnAppear(el) {")
    assert 'el.querySelector("[data-approval-heading]")' in focus
    assert "data-approve" not in focus
    # Typing elsewhere keeps focus where it is (the announcement still fires).
    assert "if (typing) return;" in focus


def test_approval_shortcuts_are_scoped_to_card_focus():
    card = _approval_card()
    assert '@keydown="onCardKey($event, $el)"' in card
    assert "@keydown.escape.window" not in card  # Esc denies only from the card
    handler = _func(APP_JS, "onCardKey(event, el) {")
    # Only when the card or its heading has focus, and only once armed by a
    # user move into the card (programmatic focus does not arm it).
    assert 't === el || (t && t.hasAttribute && t.hasAttribute("data-approval-heading"))' in handler
    assert 'if (el.dataset.armed !== "true")' in handler
    assert 'key === "Enter"' in handler and 'key === "a"' in handler and 'key === "d"' in handler
    assert '@pointerdown="arm($el)"' in card


def test_approval_card_announces_assertively_and_shows_the_queue():
    assert 'id="w-approval-live" role="alert" aria-live="assertive"' in CHAT
    assert 'x-text="$store.prompts.announcement"' in CHAT
    assert 'Alpine.store("prompts").announce(this.announcement())' in APP_JS
    assert "Mucli needs your approval: " in APP_JS
    card = _approval_card()
    assert "(index + 1) + ' of ' + $store.prompts.visibleQueue.length" in card
    assert '$store.prompts.step(-1)' in card and '$store.prompts.step(1)' in card
    assert 'x-show="index === $store.prompts.cursorIndex"' in card
    # Top-bar ◆ N attention chip jumps to the queue.
    assert '@click="$store.prompts.focusCard()"' in INDEX


def test_approval_card_is_a_state_card_with_danger_escalation():
    card = _approval_card()
    assert ':data-tone="tone()"' in card
    tone = _func(APP_JS, "tone() {")
    assert 'if (this.risky()) return "danger";' in tone
    assert 'this.isApproval() ? "attention" : "accent"' in tone
    risky = _func(APP_JS, "risky() {")
    assert "this.prompt.risk" in risky and '"always_human"' in risky
    css = (GUI / "static/css/wash/components/approval.css").read_text(encoding="utf-8")
    # State shows in a tone-wash header strip, not a side stripe (G§3.4, §9.9).
    assert "background: color-mix(in srgb, var(--w-approval-wash) 72%, var(--w-glass-fill));" in css
    assert "inset 4px 0" not in css and "--w-approval-pigment" not in css


def test_approval_payloads_are_unchanged():
    submit = _func(APP_JS, "async submit() {")
    assert 'case "tool_approval": payload = { approved: true, remember: this.remember }; break;' in submit
    assert 'case "diff":          payload = { approved: true }; break;' in submit
    assert 'case "confirm":       payload = { value: true }; break;' in submit
    assert 'case "quiz":          payload = { answers: this.answers }; break;' in submit
    assert 'Alpine.store("prompts").answer(this.id, payload)' in submit
    assert "fetch(`/api/prompts/${id}/answer`" in APP_JS
    assert "fetch(`/api/prompts/${id}/cancel`, { method: \"POST\" })" in APP_JS
    # "Always allow" is the request's existing `remember` flag.
    card = _approval_card()
    assert 'x-model="remember"' in card
    assert "Always allow" in card
    assert '@click="cancel()"' in card and '@click="submit()"' in card


def test_resolved_breadcrumb_renders_auto_approvals():
    assert "t.cancelled ? 'denied' : (t.auto ? 'auto' : 'approved')" in CHAT
    assert '<span class="w-chip w-resolved__auto" data-tone="caution" x-show="t.auto">auto</span>' in CHAT
    assert 'return t.auto ? "Auto-approved" : "Approved";' in APP_JS
    assert "auto: !!(record && record.auto)," in APP_JS


# ------------------------------------------------------------------ prompt_resolved auto flag

class _RecordingBus(EventBus):
    def __init__(self):  # skip the real subscriber plumbing
        self.events: list[dict] = []

    def publish_threadsafe(self, event):  # type: ignore[override]
        self.events.append(event)


def test_yolo_auto_approval_publishes_prompt_resolved_with_auto_flag():
    bus = _RecordingBus()
    ui = WebUI(bus, PromptStore(), session_name="s1")
    ui.notify_auto_approved("write_file", {"path": "src/server/routes.py", "content": "x" * 5000})
    events = [e for e in bus.events if e.get("kind") == "prompt_resolved"]
    assert len(events) == 1
    event = events[0]
    assert event["auto"] is True
    assert event["shape"] == "tool_approval"
    assert event["tool_name"] == "write_file"
    assert event["target"] == "src/server/routes.py"
    assert "content" not in event  # only a short target is surfaced
    assert event["id"].startswith("auto-")


def test_answered_prompts_never_carry_the_auto_flag():
    bus = _RecordingBus()
    store = PromptStore()
    ui = WebUI(bus, store, session_name="s1")

    import threading

    def answer_when_open():
        for _ in range(200):
            pending = store.pending()
            if pending:
                store.answer(pending[0]["id"], {"approved": True, "remember": False})
                return
            threading.Event().wait(0.01)

    thread = threading.Thread(target=answer_when_open)
    thread.start()
    result = ui.request_tool_approval("write_file", {"path": "a.py"})
    thread.join()
    assert result == {"approved": True, "remember": False}
    resolved = [e for e in bus.events if e.get("kind") == "prompt_resolved"]
    assert resolved and all("auto" not in e for e in resolved)


def test_auto_approval_is_detected_only_when_yolo_skipped_a_prompt(monkeypatch):
    # Pin the descriptor answers so the check does not depend on the global
    # tool registry state other tests may reset.
    import mu.agent.approval as approval

    monkeypatch.setattr(approval, "tool_requires_approval", lambda name, args: name in {"write_file", "push"})
    monkeypatch.setattr(approval, "tool_approval_policy",
                        lambda name, args: "always_human" if name == "push" else "default")
    write = ("write_file", {"path": "a.py", "content": "x"})
    read = ("read_file", {"path": "a.py"})
    assert auto_approved_by_yolo("push", {}, yolo=True) is False  # still prompts under YOLO
    assert auto_approved_by_yolo(*write, yolo=True) is True
    assert auto_approved_by_yolo(*write, yolo=False) is False
    assert auto_approved_by_yolo(*read, yolo=True) is False
    assert auto_approved_by_yolo(*read, yolo=True, strict_mode=True) is True


def test_notify_auto_approval_is_display_only():
    calls = []

    class _UI:
        def notify_auto_approved(self, name, args):
            calls.append((name, args))

    class _Broken:
        def notify_auto_approved(self, name, args):
            raise RuntimeError("boom")

    notify_auto_approval(_UI(), "bash", {"command": "ls"})
    notify_auto_approval(_Broken(), "bash", {"command": "ls"})  # swallowed
    notify_auto_approval(object(), "bash", {"command": "ls"})  # no method: no-op
    assert calls == [("bash", {"command": "ls"})]
    # The loop only notifies; the execution decision is unchanged.
    assert "notify_auto_approval(session.ui, part.tool_name, part.tool_args)" in LOOP_BODY
    hook = LOOP_BODY[LOOP_BODY.index("# No approval needed — defer to exec phase."):]
    hook = hook[:hook.index("# --- PHASE 2")]
    assert hook.index("pending_executions.append(i)") < hook.index("notify_auto_approval(")
    # The browser records it as a breadcrumb.
    assert "if (ev.auto) {" in APP_JS and "auto: true," in APP_JS


# ------------------------------------------------------------------ markdown / DOMPurify

def test_render_markdown_sanitises_with_dompurify():
    render = _func(APP_JS, "function renderMarkdown(text) {")
    assert "const clean = sanitizeHtml(raw);" in render
    assert "if (clean === null) return escapeHtml(text);" in render
    assert "return wrapCodeBlocks(clean);" in render
    sanitize = _func(APP_JS, "function sanitizeHtml(html) {")
    assert "DOMPurify.sanitize(String(html || \"\"), MUCLI_PURIFY_CONFIG)" in sanitize
    assert "return null;" in sanitize  # never hand unsanitised output to x-html
    config = _func(APP_JS, "const MUCLI_PURIFY_CONFIG")
    assert '"style"' in config and "ALLOW_DATA_ATTR: false" in config
    inline = _func(APP_JS, "function renderMarkdownInline(text) {")
    assert "sanitizeHtml(marked.parseInline(" in inline
    # Vendored DOMPurify loads before app.js; MathJax still typesets after.
    assert BASE.index("/static/vendor/purify.min.js") < BASE.index("/static/js/app.js")
    assert (GUI / "static/vendor/purify.min.js").is_file()
    assert "function typesetMath(elements)" in APP_JS


def test_code_blocks_use_the_wash_code_component():
    wrap = _func(APP_JS, "function wrapCodeBlocks(html) {")
    assert 'class="w-code"' in wrap and 'class="w-code__lang"' in wrap
    assert "w-code__copy code-copy-btn" in wrap
    assert 'button.closest(".w-code")' in APP_JS


# ------------------------------------------------------------------ tool card

def test_tool_card_renderer_is_shared_by_streaming_history_and_subagents():
    # Live and history traces both render through renderToolCards …
    assert CHAT.count("renderToolCards(") == 2
    assert 'x-html="renderToolCards(t.events, { running: $store.chat.isActiveTrace(t), now: $store.chat.clock })"' in CHAT
    assert 'x-html="renderToolCards(ct.events, { running: false })"' in CHAT
    # … and the sub-agent action timeline uses the same card renderer.
    assert "renderToolCard(WashToolCard.subagentActionCard(action, a)" in CHAT
    assert "return renderToolCard(item, options);" in TOOLCARD_JS
    assert "window.renderToolCard = renderToolCard;" in TOOLCARD_JS
    # Both event producers feed the raw args the card needs.
    assert APP_JS.count("args: part.tool_args,") >= 1
    assert "                args,\n                jsonHtml: renderJSON(args)," in APP_JS
    assert '<script src="/static/js/wash/toolcard.js" defer></script>' in INDEX


def test_tool_card_groups_and_settles():
    assert "if (run.length >= 3)" in TOOLCARD_JS  # 3+ same-kind calls group
    assert "var SETTLE_MS = 1200;" in TOOLCARD_JS
    assert "data-fresh style=\"--w-age: " in TOOLCARD_JS
    css = (GUI / "static/css/wash/components/toolcard.css").read_text(encoding="utf-8")
    assert "animation: w-toolcard-settle 1.2s step-end forwards;" in css
    assert "@keyframes w-toolcard-settle { to { color: var(--wash-ink-3); } }" in css
    assert '<span class="w-inkdrop" data-size="sm" data-tone="running"></span>' in TOOLCARD_JS


# ------------------------------------------------------------------ diff

def test_diff_component_is_exported_for_chat_approvals_and_work():
    assert "window.WashDiff = api;" in DIFF_JS
    assert "window.renderDiff = renderDiff;" in DIFF_JS
    for name in ("parseUnified", "diffContents", "renderDiff", "renderDiffHTML", "stats"):
        assert f"{name}: {name}," in DIFF_JS
    assert "window.WashDiff.renderDiffHTML(diff" in APP_JS  # approval preview
    assert "WashDiff.renderDiffHTML(t.unified" in CHAT  # chat `diff` events
    assert "chat.addDiff(ev.filename, ev.original, ev.new, name);" in APP_JS


# ------------------------------------------------------------------ palette

def test_palette_is_page_agnostic_with_a_registration_api():
    for member in ("register: register,", "unregister: unregister,", "commands: commands,",
                   "open: open,", "close: close,", "toggle: toggle,", "configure: configure,"):
        assert member in PALETTE_JS
    assert "window.WashPalette = {" in PALETTE_JS
    assert "Alpine" not in PALETTE_JS  # no page store coupling
    assert "var GROUP_ORDER = ['Navigate', 'Sessions', 'Actions', 'Commands'];" in PALETTE_JS
    assert "typeof items === 'function'" in PALETTE_JS  # live sources
    assert 'role="dialog" aria-modal="true" aria-labelledby="' in PALETTE_JS
    assert "'k'" in PALETTE_JS and "event.metaKey || event.ctrlKey" in PALETTE_JS
    # The chat page registers its own sources.
    for source in ("chat:sessions", "chat:views", "chat:actions", "chat:commands"):
        assert f"WashPalette.register('{source}'" in CHAT_PALETTE_JS
    # Wash final pass: _layout.html loads the palette (not deferred) for every
    # page, ahead of each page's own registrations.
    layout = (ROOT / "mu" / "gui" / "templates" / "_layout.html").read_text(encoding="utf-8")
    assert '<script src="/static/js/wash/palette.js"></script>' in layout
    assert "/static/js/wash/palette.js" not in INDEX
    assert '{% extends "base.html" %}' in INDEX and "/static/js/wash/chat_palette.js" in INDEX


# ------------------------------------------------------------------ gallery

def test_gallery_replays_chat_through_the_real_renderers():
    gallery = (GUI / "templates/design/gallery.html").read_text(encoding="utf-8")
    states = (GUI / "templates/design/_chat_components.html").read_text(encoding="utf-8")
    replay = (GUI / "static/js/wash/gallery_chat.js").read_text(encoding="utf-8")
    assert '{% include "fragments/chat.html" %}' in gallery
    assert "window.MUCLI_GALLERY_REPLAY = true;" in gallery
    assert "if (window.MUCLI_GALLERY_REPLAY) return;" in APP_JS  # no session boot
    assert "{{ chat_components(theme) }}" in gallery
    for section in ("composer", "messages", "toolcards", "approval", "diff", "meters", "palette"):
        assert f'g_section("{section}"' in states, section
    for kind in ("assistant_delta", "thinking_delta", "tool_call", "tool_result", "diff", "prompt", "prompt_resolved"):
        assert f"kind: '{kind}'" in replay, kind
    assert "window.routeEvent(" in replay and "window.renderToolCards(" in replay
