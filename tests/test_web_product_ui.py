"""Regression guards for the production web presentation layer."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "mu" / "gui" / "templates" / "index.html"
PANEL_TABS = ROOT / "mu" / "gui" / "templates" / "fragments" / "panel_tabs.html"
CONTAINERS = ROOT / "mu" / "gui" / "templates" / "containers.html"
PRODUCT_CSS = ROOT / "mu" / "gui" / "static" / "css" / "product.css"
CRYSTAL_CSS = ROOT / "mu" / "gui" / "static" / "css" / "crystal.css"
CLARITY_CSS = ROOT / "mu" / "gui" / "static" / "css" / "clarity.css"
ROUTE_CSS = ROOT / "mu" / "gui" / "static" / "css" / "route-product.css"
TRACE_CSS = ROOT / "mu" / "gui" / "static" / "css" / "trace.css"
CONTAINER_CRYSTAL_CSS = ROOT / "mu" / "gui" / "static" / "css" / "containers-crystal.css"
PRODUCT_JS = ROOT / "mu" / "gui" / "static" / "js" / "product.js"
WEB_SHELL_JS = ROOT / "mu" / "gui" / "static" / "js" / "web_shell.js"


def test_product_assets_are_loaded_by_main_web_shell():
    text = INDEX.read_text(encoding="utf-8")
    assert '/static/css/product.css' in text
    assert '/static/css/crystal.css' in text
    assert '/static/css/clarity.css' in text
    assert '/static/js/product.js' in text
    assert '/static/js/web_shell.js' in text
    assert 'class="app product-app"' in text
    assert 'class="panel-stage"' in text
    assert 'fragments/panel_tabs.html' in text


def test_product_shell_keeps_core_navigation_contracts():
    text = INDEX.read_text(encoding="utf-8")
    assert '$store.mode.setView(m.name)' in text
    assert '$store.mode.setView(v.name)' in text
    assert '$store.sessions.switchTo(s.name)' in text
    assert '$store.inspector.openDrawer()' in text
    assert '$store.yolo.toggle()' in text
    assert '$store.layout.togglePanel()' in text
    # RHS must be independently accessible when no panel view is active.
    assert "$store.mode.setView('memory'); $store.layout.panelOpen = true" in text
    assert 'aria-label="Toggle workspace panel"' in text


def test_rhs_panel_has_its_own_view_tabs():
    text = PANEL_TABS.read_text(encoding="utf-8")
    assert 'role="tablist"' in text
    assert '$store.mode.panelModes.includes(item.name)' in text
    assert '$store.mode.setView(m.name)' in text
    assert '$store.mode.setView(v.name)' in text
    assert '$store.layout.panelOpen = false' in text


def test_product_css_covers_primary_web_surfaces():
    legacy_product = PRODUCT_CSS.read_text(encoding="utf-8")
    crystal = CRYSTAL_CSS.read_text(encoding="utf-8")
    clarity = CLARITY_CSS.read_text(encoding="utf-8")
    combined = legacy_product + crystal + clarity
    for selector in (
        '.product-header',
        '.product-sidebar',
        '.chat-history',
        '.msg.user',
        '.composer',
        '.panel-stage',
        '.panel-tabs',
        '.inspector',
        '.welcome-entry',
        '.prompt-body .options label',
    ):
        assert selector in combined
    assert 'backdrop-filter' in combined
    assert '@media (max-width: 760px)' in combined
    assert '@media (prefers-reduced-motion: reduce)' in combined


def test_product_javascript_positions_overlays_and_is_presentation_only():
    js = PRODUCT_JS.read_text(encoding="utf-8")
    assert 'polishWelcomeCopy' in js
    assert 'focusComposer' in js
    assert 'preserveOverlayGeometry' in js
    assert 'refineComposerGeometry' in js
    assert 'positionFloatingLayer' in js
    assert 'installComposerFloatingLayers' in js
    assert 'installPanelTransitions' in js
    assert "dataset.placement = preferAbove ? 'top' : 'bottom'" in js
    assert "toolbar.style.bottom = '96px'" in js
    assert "main.style.flexDirection = 'row'" in js
    assert "node.style.position = 'fixed'" in js
    assert "event.key.toLowerCase() === 'k'" in js
    assert 'fetch(' not in js
    assert 'Alpine.store(' not in js


def test_session_history_hydrates_after_authoritative_focus():
    js = WEB_SHELL_JS.read_text(encoding="utf-8")
    assert 'sessions.load = async function' in js
    assert 'sessions.switchTo = async function' in js
    assert 'slot.historyHydrated' in js
    assert 'chat.focus(current)' in js
    assert 'chat.loadHistory(current, { force: true })' in js
    assert 'chat.loadHistory(name, { force: true })' in js
    assert 'slot.pendingReload = true' in js
    assert 'fetch(' not in js


def test_choice_picker_is_flat_not_card_based():
    css = CRYSTAL_CSS.read_text(encoding="utf-8")
    compact = css.replace(' ', '')
    assert '.prompt-body .options label' in css
    assert 'border-bottom:1pxsolidvar(--hairline)' in compact
    assert 'appearance: none' in css


def test_settings_drawer_is_wide_with_vertical_tabs():
    css = CLARITY_CSS.read_text(encoding="utf-8")
    assert 'width: min(980px, 94vw)' in css
    assert 'grid-template-columns: 172px minmax(0, 1fr)' in css
    assert '.inspector-tabs' in css and 'flex-direction: column' in css
    assert 'grid-template-columns: minmax(220px, 1fr) minmax(220px, 320px)' in css


def test_clarity_palette_is_restrained_cold_blue_not_teal_brand_wash():
    css = CLARITY_CSS.read_text(encoding="utf-8")
    route = ROUTE_CSS.read_text(encoding="utf-8")
    assert '--accent: #7f9db8' in css
    assert '--accent:#7f9db8' in route
    assert '#79c2cb' not in css
    assert '#79c2cb' not in route
    assert '--mist-a: rgba(118,153,184,.042)' in css


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
