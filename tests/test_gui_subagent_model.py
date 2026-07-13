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
    # uses), not a hardcoded set.
    assert "$store.inspector.models" in content
    # An "inherit parent" (empty) option is offered — empty = parent model.
    assert "inherit parent" in content