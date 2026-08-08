"""Regression guards for the production web presentation layer."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "mu" / "gui" / "templates" / "index.html"
CONTAINERS = ROOT / "mu" / "gui" / "templates" / "containers.html"
PRODUCT_CSS = ROOT / "mu" / "gui" / "static" / "css" / "product.css"
CRYSTAL_CSS = ROOT / "mu" / "gui" / "static" / "css" / "crystal.css"
ROUTE_CSS = ROOT / "mu" / "gui" / "static" / "css" / "route-product.css"
TRACE_CSS = ROOT / "mu" / "gui" / "static" / "css" / "trace.css"
CONTAINER_CRYSTAL_CSS = ROOT / "mu" / "gui" / "static" / "css" / "containers-crystal.css"
PRODUCT_JS = ROOT / "mu" / "gui" / "static" / "js" / "product.js"


def test_product_assets_are_loaded_by_main_web_shell():
    text = INDEX.read_text(encoding="utf-8")
    assert '/static/css/product.css' in text
    assert '/static/css/crystal.css' in text
    assert '/static/js/product.js' in text
    assert 'class="app product-app"' in text
    assert 'class="panel-stage"' in text


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
    legacy_product = PRODUCT_CSS.read_text(encoding="utf-8")
    crystal = CRYSTAL_CSS.read_text(encoding="utf-8")
    for selector in (
        '.product-header',
        '.product-sidebar',
        '.chat-history',
        '.msg.user',
        '.composer',
        '.panel-stage',
        '.inspector',
        '.welcome-entry',
        '.prompt-body .options label',
    ):
        assert selector in crystal or selector in legacy_product
    assert 'backdrop-filter' in crystal
    assert '@media (max-width: 760px)' in crystal
    assert '@media (prefers-reduced-motion: reduce)' in crystal


def test_product_javascript_positions_overlays_and_is_presentation_only():
    js = PRODUCT_JS.read_text(encoding="utf-8")
    assert 'polishWelcomeCopy' in js
    assert 'focusComposer' in js
    assert 'positionFloatingLayer' in js
    assert 'installComposerFloatingLayers' in js
    assert 'installPanelTransitions' in js
    assert "dataset.placement = preferAbove ? 'top' : 'bottom'" in js
    assert "main.style.flexDirection = 'row'" in js
    assert "event.key.toLowerCase() === 'k'" in js
    # Product JS must not own API requests or mutate agent-mode state.
    assert 'fetch(' not in js
    assert 'Alpine.store(' not in js


def test_choice_picker_is_flat_not_card_based():
    css = CRYSTAL_CSS.read_text(encoding="utf-8")
    compact = css.replace(' ', '')
    assert '.prompt-body .options label' in css
    assert 'border-bottom:1pxsolidvar(--hairline)' in compact
    assert 'appearance: none' in css


def test_trace_analyzer_uses_current_product_foundation_and_flat_sections():
    css = TRACE_CSS.read_text(encoding="utf-8")
    assert '@import url("/static/css/route-product.css")' in css
    assert '.trace-section{padding:0;background:transparent;border:0;border-radius:0}' in css
    assert '.trace-cards' in css
    assert 'border-top:1px solid var(--hairline)' in css
    assert '.trace-info-popout' in css and 'var(--glass-strong)' in css
    assert ROUTE_CSS.exists()


def test_container_management_uses_current_product_surface_without_breaking_ids():
    template = CONTAINERS.read_text(encoding="utf-8")
    assert '/static/css/route-product.css' in template
    assert '/static/css/containers-crystal.css' in template
    for dom_id in (
        'container-list',
        'template-list',
        'create-environment-button',
        'create-modal',
        'environment-name',
        'shell-modal',
        'shell-command',
    ):
        assert f'id="{dom_id}"' in template

    css = CONTAINER_CRYSTAL_CSS.read_text(encoding="utf-8")
    assert '.manager-card{display:grid' in css
    assert 'border-bottom:1px solid var(--hairline)' in css
    assert '.manager-monitor-grid' in css
    assert '.manager-overflow-menu' in css and 'var(--glass-strong)' in css
