"""Web GUI: plan mode has an unmistakable header pill, a composer strip and a
plan-mode placeholder, all driven by the planMode store."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "mu/gui/static/js/app.js").read_text()
INDEX = (ROOT / "mu/gui/templates/index.html").read_text()
CHAT = (ROOT / "mu/gui/templates/fragments/chat.html").read_text()
PRODUCT_CSS = (ROOT / "mu/gui/static/css/product.css").read_text()


def test_plan_mode_store_reads_and_writes_the_variable():
    assert 'Alpine.store("planMode", {' in APP
    assert 'v.key === "plan_mode"' in APP
    assert 'fetch("/api/variables/plan_mode"' in APP
    assert "Turn off plan mode?" in APP  # header pill confirms before turning off
    # Loaded on start-up and on both session-switch paths.
    assert APP.count('Alpine.store("planMode").load()') >= 4


def test_plan_mode_updates_live_from_sse_and_slash_commands():
    assert 'case "plan_mode_changed":' in APP
    assert 'Alpine.store("planMode").apply(ev.plan_mode)' in APP
    assert "ev.result.data.plan_mode" in APP


def test_header_pill_only_shows_while_on_and_sits_before_approvals():
    pill = INDEX.index('data-testid="plan-mode-pill"')
    assert pill < INDEX.index("yolo-toggle product-yolo")
    li = INDEX.rfind("<li", 0, pill)
    assert 'x-show="$store.planMode.active"' in INDEX[li:pill]
    assert "Plan mode" in INDEX[pill:pill + 800] and "🔒" in INDEX[pill:pill + 800]
    assert "turnOff({ confirmFirst: true })" in INDEX
    assert "File edits, shell writes and other write tools are blocked." in APP


def test_composer_strip_and_placeholder():
    strip = CHAT.index('data-testid="plan-mode-strip"')
    assert 'x-show="$store.planMode.active"' in CHAT[strip:strip + 300]
    assert "read-only. Writing is blocked." in CHAT
    assert "$store.planMode.turnOff()" in CHAT and ">Turn off<" in CHAT
    assert re.search(r":placeholder=\"\$store\.planMode\.active\s*\?\s*'Plan mode", CHAT)
    # Strip sits above the composer input.
    assert strip < CHAT.index('x-ref="input"')


def test_plan_mode_has_its_own_colour_in_both_themes():
    # Wash W1: plan mode is the `plan` semantic (lilac ink), which the
    # generated tokens define per theme, so one declaration covers both.
    assert ':root, [data-theme] { --plan-mode: var(--wash-plan-ink); }' in PRODUCT_CSS
    assert ".chip.plan-mode-pill" in PRODUCT_CSS and ".plan-mode-strip" in PRODUCT_CSS
    pill_rules = PRODUCT_CSS[PRODUCT_CSS.index(".chip.plan-mode-pill {"):]
    assert "var(--warn)" not in pill_rules.split("}")[0]


def test_blocked_tool_results_are_labelled():
    assert "function isPlanModeBlockedResult" in APP
    assert '"plan_mode_blocked"' in APP
    assert "Blocked by plan mode" in APP
    assert APP.count("planBlocked: isPlanModeBlockedResult(") == 2  # live + history
    assert CHAT.count("te-plan-blocked") == 2
