"""Toast notification placement + styling.

Toasts used to stack in the top-right, where they overlapped the header's
view / tools / yolo / inspector / theme / leave controls and read as a thin
border-left strip. They now anchor top-center just below the header, render
as a frosted pill with a colored type glyph, and slide down from the header.
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


def test_toast_container_is_top_center_below_header():
    with open(CSS_PATH, "r", encoding="utf-8") as f:
        css = f.read()
    # Anchored below the 56px header (not top-right anymore).
    assert ".toast-container" in css
    block = css.split(".toast-container", 1)[1].split("}", 2)[0]
    assert "top: 64px" in block
    assert "left: 50%" in block
    assert "translateX(-50%)" in block
    # Must NOT pin to the right edge (the old behavior that overlapped the
    # header controls).
    assert "right:" not in block


def test_toast_is_frosted_pill_with_type_glyph():
    with open(CSS_PATH, "r", encoding="utf-8") as f:
        css = f.read()
    # Pill shape.
    assert "border-radius: var(--radius-pill)" in css
    # Frosted backdrop (more legible over chat content).
    assert "backdrop-filter" in css
    # Colored type glyphs give instant intent recognition.
    assert '.toast--success::before' in css
    assert '.toast--error::before' in css
    assert '.toast--info::before' in css


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