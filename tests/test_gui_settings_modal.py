"""Static checks for the full-height side settings modal."""

import os


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHAT_HTML = os.path.join(REPO, "mu", "gui", "templates", "fragments", "chat.html")
APP_CSS = os.path.join(REPO, "mu", "gui", "static", "css", "app.css")


def test_settings_uses_a_consistent_side_modal_layout():
    markup = open(CHAT_HTML, encoding="utf-8").read()
    css = open(APP_CSS, encoding="utf-8").read()

    assert "settings-side-modal" in markup
    assert "settings-close-btn" in markup
    assert "width: min(620px, 48vw);" in css
    assert "grid-template-columns: minmax(150px, 1fr) minmax(220px, 1.35fr);" in css
