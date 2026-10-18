"""Web GUI: effort picker in the composer and option-aware settings groups."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "mu/gui/static/js/app.js").read_text()
INDEX = (ROOT / "mu/gui/templates/index.html").read_text()
CHAT = (ROOT / "mu/gui/templates/fragments/chat.html").read_text()
COMPOSER_CSS = (ROOT / "mu/gui/static/css/wash/components/composer.css").read_text()


def test_effort_store_reads_current_provider_and_writes_the_variable():
    assert 'Alpine.store("effort", {' in APP
    assert 'fetch("/api/providers/current")' in APP and "d.effort_levels" in APP
    assert '"/api/variables/reasoning_effort"' in APP and 'method: "DELETE"' in APP
    # Refreshed on start-up, session switch and provider/model switch.
    assert APP.count('Alpine.store("effort").load()') >= 3


def test_composer_has_an_effort_picker_next_to_the_mode_chip():
    # Wash W3 (G§9.6): the effort picker is a composer control, "[◉ Mode ▾]
    # [Effort ▾]", between the mode chip and Send — no longer a header chip.
    chip = CHAT.index('class="w-chip w-composer__effort"')
    assert CHAT.index("composer-mode-pill") < chip < CHAT.index("send-btn")
    assert 'aria-label="Model effort"' in CHAT and "$store.effort.set($event.target.value)" in CHAT
    assert "Anthropic effort, OpenAI reasoning_effort, Ollama think" in CHAT
    assert ".w-composer__effort-select" in COMPOSER_CSS
    assert "effort-chip" not in INDEX


def test_settings_render_options_as_selects_with_group_labels_and_notes():
    assert "v.options && v.options.length" in CHAT and "settings-var-options" in CHAT
    assert "$store.inspector.groupLabel(g.name)" in CHAT and "$store.inspector.groupNote(g.name)" in CHAT
    assert '"speed & effort": "Speed & effort"' in APP and '"openai": "OpenAI"' in APP
    assert "never use num_ctx or keep_alive" in APP and "at least 64k" in APP
