"""Toast notification placement + styling.

Toasts are the one Wash toast component (G§9.16, components/toast.css): a
bottom-right stack (clear of every header control), raised notes whose tone
colours a status glyph (✓ ● ◆ ✗) paired with the message text, bleeding in
from the corner. app.js's $store.toast and window.WashToast render the same
markup.
"""

import os

CSS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "mu", "gui", "static", "css", "wash", "components", "toast.css",
)
APP_JS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "mu", "gui", "static", "js", "app.js",
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
    # Bottom-right placement, never over the header controls.
    assert ".w-toast-region {" in css
    block = css.split(".w-toast-region {", 1)[1].split("}", 1)[0]
    assert "position: fixed" in block
    assert "right: var(--wash-space-6)" in block
    assert "bottom: var(--wash-space-6)" in block
    # Must NOT pin to the top (where it overlapped the header controls).
    assert "top:" not in block


def test_toast_is_tone_bordered_note_with_type_glyph():
    with open(CSS_PATH, "r", encoding="utf-8") as f:
        css = f.read()
    block = css.split("\n.w-toast {", 1)[1].split("}", 1)[0]
    # Raised note on the Wash surface with the component radius.
    assert "background: var(--wash-raised)" in block
    assert "border: 1px solid var(--wash-hairline)" in block
    assert "border-radius: var(--wash-radius-md)" in block
    # Flat surface: no frosted backdrop blur.
    assert "backdrop-filter" not in css
    # The tone colours the type glyph for instant intent recognition.
    assert ".w-toast__glyph { flex: none; color: var(--w-tone-ink" in css
    region = os.path.join(os.path.dirname(BASE_HTML_PATH), "partials", "toast_region.html")
    with open(region, "r", encoding="utf-8") as f:
        html = f.read()
    assert ':data-tone="$store.toast.tone(m.type)"' in html
    assert 'x-text="$store.toast.glyph(m.type)"' in html
    # Shared status vocabulary glyphs.
    with open(APP_JS_PATH, "r", encoding="utf-8") as f:
        app = f.read()
    assert 'glyph(type) { return { ok: "✓", danger: "✗", attention: "◆" }[this._tone(type)] || "●"; }' in app


def test_toast_template_wraps_message_and_dismisses():
    # Wash W2: the toast template moved from base.html into the shared toast
    # region partial (rendered by _layout.html; base.html turns it on).
    with open(BASE_HTML_PATH, "r", encoding="utf-8") as f:
        base = f.read()
    assert "{% set alpine_toasts = true %}" in base
    region = os.path.join(os.path.dirname(BASE_HTML_PATH), "partials", "toast_region.html")
    with open(region, "r", encoding="utf-8") as f:
        html = f.read()
    assert 'class="w-toast"' in html
    assert 'class="w-toast__msg"' in html
    assert 'class="w-toast__close"' in html
    # Type is bound to the message type.
    assert "'toast--' + m.type" in html
    # Dismiss button wired to the store.
    assert "$store.toast.dismiss(m.id)" in html
    # Leaving fades out (entering bleeds in through the component animation).
    assert "x-transition:leave.opacity" in html


def test_toast_css_has_transition_classes():
    with open(CSS_PATH, "r", encoding="utf-8") as f:
        css = f.read()
    # Toasts bleed in (blur → sharp) and fade when leaving (G§5).
    assert "animation: w-toast-bleed" in css
    assert "@keyframes w-toast-bleed" in css
    assert ".w-toast[data-leaving]" in css
