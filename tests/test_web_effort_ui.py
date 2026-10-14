"""Web GUI: effort picker in the header chips and option-aware settings groups."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "mu/gui/static/js/app.js").read_text()
INDEX = (ROOT / "mu/gui/templates/index.html").read_text()
CHAT = (ROOT / "mu/gui/templates/fragments/chat.html").read_text()
PRODUCT_CSS = (ROOT / "mu/gui/static/css/product.css").read_text()


def test_effort_store_reads_current_provider_and_writes_the_variable():
    assert 'Alpine.store("effort", {' in APP
    assert 'fetch("/api/providers/current")' in APP and "d.effort_levels" in APP
    assert '"/api/variables/reasoning_effort"' in APP and 'method: "DELETE"' in APP
    # Refreshed on start-up, session switch and provider/model switch.
    assert APP.count('Alpine.store("effort").load()') >= 3


def test_header_has_an_effort_picker_next_to_provider_model():
    chip = INDEX.index('class="chip effort-chip"')
    assert INDEX.index("provider-model") < chip < INDEX.index("yolo-toggle")
    assert 'aria-label="Model effort"' in INDEX and "$store.effort.set($event.target.value)" in INDEX
    assert "Anthropic effort, OpenAI reasoning_effort, Ollama think" in INDEX
    assert ".effort-chip .effort-select" in PRODUCT_CSS


def test_settings_render_options_as_selects_with_group_labels_and_notes():
    assert "v.options && v.options.length" in CHAT and "settings-var-options" in CHAT
    assert "$store.inspector.groupLabel(g.name)" in CHAT and "$store.inspector.groupNote(g.name)" in CHAT
    assert '"speed & effort": "Speed & effort"' in APP and '"openai": "OpenAI"' in APP
    assert "never use num_ctx or keep_alive" in APP and "at least 64k" in APP
