"""GUI wiring for the subagent-model picker.

Sub-agents default to the parent model; the user can override that with a
`subagent_model` session variable (also settable via /set in the TUI). In
the GUI the override is exposed in the chat composer's session-settings
popout as a dynamic model picker that lists the active provider's models
(the same list the main `model` picker uses), plus an "inherit parent"
option.
"""

import os

APP_JS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "mu", "gui", "static", "js", "app.js",
)
CHAT_HTML_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "mu", "gui", "templates", "fragments", "chat.html",
)


def test_inspector_store_exposes_subagent_model_field():
    with open(APP_JS_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    assert "subagentModel" in content
    assert "onSubagentModelChange" in content
    # The picker reads the live variable list so /set edits stay in sync.
    assert "_readVariable" in content


def test_composer_settings_has_subagent_model_picker():
    with open(CHAT_HTML_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    # The picker label + binding exist in the provider & model section.
    assert "subagent model" in content
    assert '$store.inspector.subagentModel' in content
    assert "$store.inspector.onSubagentModelChange" in content
    # It uses the dynamic models list (the same one the main model picker
    # uses), via modelOptions() so the bound value is always selectable —
    # not a hardcoded set.
    assert "modelOptions" in content
    # An "inherit parent" (empty) option is offered — empty = parent model.
    assert "inherit parent" in content


def test_model_picker_shows_loaded_model_not_placeholder():
    """The main model picker must reflect the loaded session's model instead
    of falling back to the '—' placeholder when the model isn't an exact
    member of the fetched list (e.g. an elided :latest tag)."""
    with open(CHAT_HTML_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    # The model picker's options come from modelOptions(currentModel) so the
    # bound value is guaranteed present.
    assert "$store.inspector.modelOptions($store.inspector.currentModel)" in content


def test_header_crumb_updates_live_on_model_switch():
    """The header crumb's provider/model must bind to the reactive inspector
    store (not just server-rendered Jinja) so it updates immediately when the
    model is switched — previously it stayed on the old name until refresh."""
    index_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "mu", "gui", "templates", "index.html",
    )
    with open(index_path, "r", encoding="utf-8") as f:
        content = f.read()
    # The crumb binds to the live store values, with the Jinja values kept
    # only as the pre-Alpine fallback inside the span.
    assert "$store.inspector.currentProvider" in content
    assert "$store.inspector.currentModel" in content


def test_inspector_store_confirms_model_changes():
    """Changing the parent or subagent model surfaces a toast so the user
    knows it succeeded (or failed)."""
    with open(APP_JS_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    # Parent model switch → success toast on ok.
    assert "model →" in content
    # Subagent model change → success toast (both branches).
    assert "subagent model →" in content
    assert "inherit parent" in content
    # Failures use the toast store, not a blocking alert.
    assert 'Alpine.store("toast").show' in content