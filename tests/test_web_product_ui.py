"""Regression guards for the production web presentation layer."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "mu" / "gui" / "templates" / "index.html"
PRODUCT_CSS = ROOT / "mu" / "gui" / "static" / "css" / "product.css"
PRODUCT_JS = ROOT / "mu" / "gui" / "static" / "js" / "product.js"


def test_product_assets_are_loaded_by_main_web_shell():
    text = INDEX.read_text(encoding="utf-8")
    assert '/static/css/product.css' in text
    assert '/static/js/product.js' in text
    assert 'class="app product-app"' in text


def test_product_shell_keeps_core_navigation_contracts():
    text = INDEX.read_text(encoding="utf-8")
    # View switching must remain presentation-only; backend mode is still
    # selected from the chat composer fragment.
    assert '$store.mode.setView(m.name)' in text
    assert '$store.mode.setView(v.name)' in text
    assert '$store.sessions.switchTo(s.name)' in text
    assert '$store.inspector.openDrawer()' in text
    assert '$store.yolo.toggle()' in text
    assert '$store.layout.togglePanel()' in text


def test_product_css_covers_primary_web_surfaces():
    css = PRODUCT_CSS.read_text(encoding="utf-8")
    for selector in (
        '.product-header',
        '.sidebar-inner',
        '.chat-history',
        '.msg.user',
        '.composer',
        '.mode-panel',
        '.inspector',
        '.welcome.welcome-launcher',
    ):
        assert selector in css
    assert '@media (max-width: 760px)' in css
    assert '@media (prefers-reduced-motion: reduce)' in css


def test_product_javascript_is_presentation_only():
    js = PRODUCT_JS.read_text(encoding="utf-8")
    assert 'polishWelcomeCopy' in js
    assert 'focusComposer' in js
    assert "event.key.toLowerCase() === 'k'" in js
    # Product JS must not own API requests or mutate agent-mode state.
    assert 'fetch(' not in js
    assert 'Alpine.store(' not in js
