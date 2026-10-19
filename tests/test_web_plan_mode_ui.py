"""Web GUI: plan mode has an unmistakable header pill, a plan banner under the
top bar, a dashed lilac composer and a plan-mode placeholder, all driven by
the planMode store."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "mu/gui/static/js/app.js").read_text()
INDEX = (ROOT / "mu/gui/templates/index.html").read_text()
CHAT = (ROOT / "mu/gui/templates/fragments/chat.html").read_text()
WASH = ROOT / "mu/gui/static/css/wash"
CHIP_CSS = (WASH / "components/chip.css").read_text()
BANNER_CSS = (WASH / "components/banner.css").read_text()
COMPOSER_CSS = (WASH / "components/composer.css").read_text()
TOOLCARD_JS = (ROOT / "mu/gui/static/js/wash/toolcard.js").read_text()


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
    # Mu §10.1: there is no top bar; the plan-mode pill sits in the composer
    # foot, before the auto-approve word, and shows only while plan mode is on.
    pill = CHAT.index('data-testid="plan-mode-pill"')
    assert pill < CHAT.index('class="w-composer__yolo"')
    button = CHAT.rfind("<button", 0, pill)
    assert 'x-show="$store.planMode.active"' in CHAT[button:pill + 200]
    # Wash: a lock sprite icon replaces the 🔒 emoji (no emoji in chrome, G§4).
    assert "Plan mode" in CHAT[pill:pill + 800] and 'w.icon("lock"' in CHAT[pill:pill + 800]
    assert "🔒" not in INDEX and "🔒" not in CHAT
    assert "turnOff({ confirmFirst: true })" in CHAT
    assert "File edits, shell writes and other write tools are blocked." in APP


def test_composer_strip_and_placeholder():
    # Wash W3 (G§9.16): the plan strip is a lilac banner in the top-bar area of
    # the chat stage (index.html), above the transcript and composer.
    strip = INDEX.index('data-testid="plan-mode-strip"')
    assert 'x-show="$store.planMode.active"' in INDEX[strip:strip + 300]
    assert 'data-tone="plan"' in INDEX[INDEX.rfind("<div", 0, strip):strip]
    assert "read-only. Writing is blocked." in INDEX
    assert "$store.planMode.turnOff()" in INDEX and ">Turn off<" in INDEX
    assert strip < INDEX.index('{% include "fragments/chat.html" %}')
    assert re.search(r":placeholder=\"\$store\.planMode\.active\s*\?\s*'Plan mode", CHAT)
    # The composer itself adds a lilac dashed outline in plan mode (G§9.6).
    assert ":data-plan=\"$store.planMode.active ? '' : null\"" in CHAT
    assert ".w-composer[data-plan] .w-composer__form" in COMPOSER_CSS


def test_plan_mode_has_its_own_colour_in_both_themes():
    # Wash: plan mode is the `plan` semantic (lilac ink, defined per theme by
    # the generated tokens), dashed so it is distinct from research and from
    # attention/caution.
    plan_chip = CHIP_CSS[CHIP_CSS.index('.w-chip[data-tone="plan"] {'):]
    assert "1px dashed var(--wash-plan-ink)" in plan_chip.split("}")[0]
    assert '.w-banner[data-tone="plan"] { outline: 1px dashed var(--wash-plan-ink);' in BANNER_CSS
    composer = COMPOSER_CSS[COMPOSER_CSS.index(".w-composer[data-plan] .w-composer__form {"):]
    rule = composer.split("}")[0]
    assert "dashed var(--wash-plan-ink)" in rule
    assert "attention" not in rule and "caution" not in rule
    assert 'class="w-chip plan-mode-pill" data-tone="plan"' in CHAT


def test_blocked_tool_results_are_labelled():
    assert "function isPlanModeBlockedResult" in APP
    assert '"plan_mode_blocked"' in APP
    assert APP.count("planBlocked: isPlanModeBlockedResult(") == 2  # live + history
    # Wash W3: the shared tool card renders a plan-blocked result as its own
    # state (lock icon, lilac rule) with the label, for live and history traces.
    assert "ev.planBlocked ? 'blocked'" in TOOLCARD_JS
    assert "Blocked by plan mode" in TOOLCARD_JS
    assert CHAT.count("renderToolCards(") == 2


