"""Static wiring checks for the busy-aware chat composer."""

import os


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = os.path.join(REPO, "mu", "gui", "static", "js", "app.js")
CHAT_HTML = os.path.join(
    REPO, "mu", "gui", "templates", "fragments", "chat.html"
)
APP_CSS = os.path.join(REPO, "mu", "gui", "static", "css", "app.css")


def test_busy_composer_allows_only_slash_commands():
    with open(APP_JS, encoding="utf-8") as handle:
        script = handle.read()
    with open(CHAT_HTML, encoding="utf-8") as handle:
        markup = handle.read()
    with open(APP_CSS, encoding="utf-8") as handle:
        css = handle.read()

    assert "isSlashCommand(text)" in script
    assert "canSend(text)" in script
    assert "slot.busy && !isCommand" in script
    assert "$store.chat.busy && !$store.chat.isSlashCommand($refs.input.value)" in markup
    assert ".composer .send-btn:disabled" in css


def test_assistant_and_code_blocks_have_minimal_copy_controls():
    script = open(APP_JS, encoding="utf-8").read()
    markup = open(CHAT_HTML, encoding="utf-8").read()
    css = open(APP_CSS, encoding="utf-8").read()

    assert "$store.chat.copyMessage(t.text || '', t.id)" in markup
    assert "code-copy-btn" in script
    assert "copyToClipboard" in script
    assert 'title="Attach a file"><svg' in markup
    assert ".code-copy-btn" in css
