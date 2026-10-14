"""Toast notification placement + styling.

Toasts follow the planos design: a bottom-right stack (clear of every
header control), raised notes with a 4px tone left border and a status glyph
(✓ ● ◆ ✗) paired with the message text, rising in from the corner.
"""

import os

CSS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "mu", "gui", "static", "css", "app.css",
)
BASE_HTML_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "mu", "gui", "templates", "base.html",
)


def test_toast_css_exists():
    assert os.path.isfile(CSS_PATH)


def test_toast_container_is_bottom_right_clear_of_header():
    with open(CSS_PATH, "r", encoding="utf-8") as f:
        css = f.read()
    # planos placement: bottom-right, never over the header controls.
    assert ".toast-container" in css
    block = css.split(".toast-container", 1)[1].split("}", 2)[0]
    assert "right: var(--space-4)" in block
    assert "bottom: var(--space-4)" in block
    # Must NOT pin to the top (where it overlapped the header controls).
    assert "top:" not in block


def test_toast_is_tone_bordered_note_with_type_glyph():
    with open(CSS_PATH, "r", encoding="utf-8") as f:
        css = f.read()
    block = css.split("\n.toast {", 1)[1].split("}", 1)[0]
    # planos toast: raised surface, 4px tone left border, control radius.
    assert "background: var(--raised)" in block
    assert "border-left-width: 4px" in block
    assert "border-radius: var(--radius-sm)" in block
    # Flat surface: no frosted backdrop blur anywhere in app.css.
    assert "backdrop-filter" not in css
    # Colored type glyphs give instant intent recognition.
    assert '.toast--success::before' in css
    assert '.toast--error::before' in css
    assert '.toast--info::before' in css
    # Shared status vocabulary glyphs.
    assert 'content: "✓"' in css and 'content: "✗"' in css


def test_toast_template_wraps_message_and_dismisses():
    with open(BASE_HTML_PATH, "r", encoding="utf-8") as f:
        html = f.read()
    assert 'class="toast"' in html
    assert 'class="toast__msg"' in html
    assert 'class="toast__close"' in html
    # Type is bound to the message type.
    assert "'toast--' + m.type" in html
    # Dismiss button wired to the store.
    assert "$store.toast.dismiss(m.id)" in html
    # Slide-down enter/leave transition classes are declared.
    assert "toast--enter-start" in html
    assert "toast--leave-end" in html


def test_toast_css_has_transition_classes():
    with open(CSS_PATH, "r", encoding="utf-8") as f:
        css = f.read()
    for cls in (".toast--enter", ".toast--enter-start", ".toast--enter-end",
                ".toast--leave", ".toast--leave-start", ".toast--leave-end"):
        assert cls in css, f"missing transition class {cls}"