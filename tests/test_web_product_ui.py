"""Regression guards for the production web presentation layer."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "mu" / "gui" / "templates" / "base.html"
INDEX = ROOT / "mu" / "gui" / "templates" / "index.html"
INSPECTOR = ROOT / "mu" / "gui" / "templates" / "fragments" / "inspector.html"
PANEL_TABS = ROOT / "mu" / "gui" / "templates" / "fragments" / "panel_tabs.html"
FILES_PANEL = ROOT / "mu" / "gui" / "templates" / "fragments" / "files_panel.html"
CONTAINERS = ROOT / "mu" / "gui" / "templates" / "containers.html"
LEGACY_CSS = ROOT / "mu" / "gui" / "static" / "css"
WASH = LEGACY_CSS / "wash"
CHAT_CSS = WASH / "patterns" / "chat.css"
PANEL_CSS = WASH / "components" / "panel.css"
COMPOSER_CSS = WASH / "components" / "composer.css"
PROMPT_CSS = WASH / "components" / "prompt.css"
CHAT_HTML = ROOT / "mu" / "gui" / "templates" / "fragments" / "chat.html"
SIDEBAR = ROOT / "mu" / "gui" / "templates" / "partials" / "sidebar.html"
# The chat page's retired legacy stylesheets (W4, web-shell ledger).
RETIRED_CHAT_CSS = ("product.css", "crystal.css", "clarity.css", "refinement.css",
                    "conversation.css", "popouts.css", "pricing_settings.css", "pricing_rows.css")
ROUTE_CSS = ROOT / "mu" / "gui" / "static" / "css" / "route-product.css"
TRACE_CSS = ROOT / "mu" / "gui" / "static" / "css" / "trace.css"
CONTAINER_CRYSTAL_CSS = ROOT / "mu" / "gui" / "static" / "css" / "containers-crystal.css"
PRODUCT_JS = ROOT / "mu" / "gui" / "static" / "js" / "product.js"
WEB_SHELL_JS = ROOT / "mu" / "gui" / "static" / "js" / "web_shell.js"
OVERRIDES_CSS = ROOT / "mu" / "gui" / "static" / "css" / "wash" / "patterns" / "legacy-overrides.css"


def test_product_assets_are_loaded_by_main_web_shell():
    text = INDEX.read_text(encoding="utf-8")
    # Wash W4: the chat page sits on the shared shell (`content` block, not its
    # own `page` chrome) and links one page bundle; the chat page's own legacy
    # stylesheets are retired, so the bundle imports none of them.
    bundle = CHAT_CSS.read_text(encoding="utf-8")
    assert '/static/css/wash/patterns/chat.css' in text
    assert '{% block content %}' in text and '{% block page %}' not in text
    for name in RETIRED_CHAT_CSS:
        assert name not in bundle
        assert not (LEGACY_CSS / name).exists(), name
    for asset in ('/static/js/product.js', '/static/js/web_shell.js'):
        assert asset in text
    assert 'class="app product-app w-chat"' in text
    assert 'class="panel-stage w-chat__stage"' in text
    assert 'fragments/panel_tabs.html' in text


def test_left_navigation_groups_threads_and_sessions():
    """Sidebar contract: Threads section = current session's thread-group roster
    (hidden for singleton groups); Sessions section = the FULL sessions list
    (main session always visible) with hover unload/delete row actions.
    Wash W4: the sections render inside the shared sidebar (partials/sidebar.html
    `sidebar_extra`), after the Navigate list; rows are role=button with
    keyboard support for threads and sessions alike (G§9.20)."""
    text = INDEX.read_text(encoding="utf-8")
    sidebar = text.split('{% set sidebar_extra %}', 1)[1].split('{% endset %}', 1)[0]
    assert '{%- if sidebar_extra is defined %}{{ sidebar_extra }}{% endif %}' in SIDEBAR.read_text(encoding="utf-8")
    assert '>Threads</span>' in sidebar
    assert '>Sessions</span>' in sidebar
    # Threads scoped to the group roster and hidden when it is a singleton.
    assert '$store.threads.groupRoster.length > 1' in sidebar
    assert 'x-for="t in $store.threads.groupRoster"' in sidebar
    # Sessions section iterates the full store list, not a roster-filtered view.
    assert 'x-for="s in $store.sessions.list"' in sidebar
    assert 'otherSessions' not in sidebar
    # Row actions: switch on row click, unload + confirm-guarded delete.
    assert '$store.threads.switchTo(t)' in sidebar
    assert '$store.sessions.switchTo(s.name)' in sidebar
    assert '$store.sessions.unload(s.name)' in sidebar
    assert '$store.sessions.remove(s.name)' in sidebar
    assert '$store.confirm.ask(' in sidebar
    assert 'New thread' in sidebar
    assert '+ new session' in sidebar
    # Keyboard access on both row kinds.
    assert sidebar.count('role="button" tabindex="0"') >= 2
    assert sidebar.count('@keydown.enter.prevent="$el.click()"') >= 2
    assert '<span>Work</span>' not in sidebar
    assert '<span>Workspace</span>' not in sidebar
    assert '$store.mode.setView(m.name)' not in sidebar
    assert '$store.mode.setView(v.name)' not in sidebar


def test_header_has_trace_action_and_centered_settings_cog():
    # Wash W4 (G§8.1): Chat/Work/Environments/Trace are primary navigation in
    # the sidebar, not top-bar icons. The session-scoped trace link lives in
    # the stage view menu; the top bar keeps the settings cog (sprite icon).
    text = INDEX.read_text(encoding="utf-8")
    tabs = PANEL_TABS.read_text(encoding="utf-8")
    assert "'/trace?session=' + encodeURIComponent($store.chat.currentName || '')" in tabs
    assert 'aria-label="Open Trace Analyzer"' in tabs
    assert '("trace", "/trace", "activity", "Trace")' in SIDEBAR.read_text(encoding="utf-8")
    assert 'class="w-btn settings-cog-button" data-variant="icon"' in text
    assert 'aria-label="Open settings">{{ w.icon("settings") }}' in text


def test_product_shell_keeps_core_navigation_contracts():
    text = INDEX.read_text(encoding="utf-8")
    assert '$store.sessions.switchTo(s.name)' in text
    assert '$store.inspector.openDrawer()' in text
    assert '$store.yolo.toggle()' in text
    assert "$store.mode.setView('memory'); $store.layout.panelOpen = true" in text
    assert 'aria-label="Toggle workspace panel"' in text
    # Environments is a shared sidebar destination (Wash W4).
    assert '("environments", "/containers", "box", "Environments")' in SIDEBAR.read_text(encoding="utf-8")


def test_rhs_panel_uses_a_segmented_control_and_menu_not_selects():
    # Wash W4 retires the two-<select> stage picker (G§8.1: "Panels are chosen
    # from a segmented list or menu, not two <select>s"). One stage header:
    # title over a mode-tint band, a segmented control for everyday views, a
    # menu with every mode panel and tool, and close.
    text = PANEL_TABS.read_text(encoding="utf-8")
    assert '<select ' not in text and '</select>' not in text
    assert 'class="panel-tabs panel-select-nav w-panel__head w-stage-head"' in text
    assert ':data-mode-tint="$store.mode.active"' in text
    assert 'class="w-segmented panel-segmented"' in text
    assert 'role="menu"' in text and 'aria-haspopup="menu"' in text
    assert 'Modes</div>' in text and 'Tools</div>' in text
    assert '$store.mode.setView(name)' in text
    assert '$store.mode.setView(m.name)' in text and '$store.mode.setView(v.name)' in text
    assert '$store.layout.panelOpen = false' in text
    assert 'panel-tabs-strip' not in text
    assert 'class="panel-tab"' not in text
    panel = PANEL_CSS.read_text(encoding="utf-8")
    assert '.w-panel__head::before' in panel
    assert 'linear-gradient(to bottom, var(--w-mode-wash, var(--wash-accent-wash)), transparent)' in panel
    assert 'border-bottom: 1px solid var(--wash-hairline)' in panel
    # Panels fill the stage under the header.
    assert '.w-chat__panels > .mode-panel {' in CHAT_CSS.read_text(encoding="utf-8")


def test_product_css_covers_primary_web_surfaces():
    # Wash W4: the chat page is styled by real Wash rules (components + the
    # chat page patterns), not the retired product/crystal/clarity layers.
    files = sorted((WASH / "components").glob("*.css")) + [
        WASH / "layout.css", CHAT_CSS, WASH / "patterns" / "panels.css",
        WASH / "patterns" / "settings.css", WASH / "patterns" / "welcome.css",
    ]
    combined = ''.join(path.read_text(encoding="utf-8") for path in files)
    for selector in (
        '.w-topbar', '.w-sidebar', '.w-transcript', '.w-msg[data-role="user"]',
        '.w-composer', '.w-chat__stage', '.w-stage-head', '.inspector',
        '.w-welcome__choice', '.w-prompt__option',
        '.composer-mode-popout', '.composer-settings-popout',
    ):
        assert selector in combined, selector
    page = CHAT_CSS.read_text(encoding="utf-8")
    assert 'backdrop-filter' not in page
    assert '@media (width < 840px)' in page
    assert '@media (prefers-reduced-motion: reduce)' in page
    # Body padding: the shared shell owns the page frame; no override needed.
    assert 'body { padding: 0; }' not in OVERRIDES_CSS.read_text(encoding="utf-8")
    assert '!important' not in page


def test_alpine_sunrise_atmosphere_is_retired_for_flat_surfaces():
    # clarity.css is retired (Wash W4); the chat page patterns carry no
    # sunrise atmosphere — Wash art lives only in .w-hero zones (G§6.2).
    css = CHAT_CSS.read_text(encoding="utf-8")
    route = ROUTE_CSS.read_text(encoding="utf-8")
    for token in (
        '--sky-blue', '--glacier-blue', '--sunrise-pink', '--sunrise-peach',
        '--alpine-green', '--snow-glow',
    ):
        assert token not in css
        assert token not in route
    assert 'radial-gradient' not in css
    assert 'radial-gradient' not in route
    assert 'backdrop-filter' not in css
    assert 'clip-path' not in css
    assert 'clip-path' not in route
    assert '--mountain-near' not in css
    assert '--mountain-far' not in css
    assert '--mountain-near' not in route
    assert '--mountain-far' not in route
    assert '.product-app::after' not in css
    assert 'body::after' not in route


def test_product_javascript_positions_overlays_and_is_presentation_only():
    js = PRODUCT_JS.read_text(encoding="utf-8")
    # Wash W4: popout geometry lives in the chat page patterns (popouts.css is
    # retired); product.js injects no stylesheets at runtime.
    assert 'installPresentationStylesheet' not in js
    assert '.composer .composer-mode-popout {' in CHAT_CSS.read_text(encoding="utf-8")
    assert 'polishWelcomeCopy' in js
    assert 'focusComposer' in js
    assert 'preserveOverlayGeometry' in js
    assert 'refineComposerGeometry' in js
    assert 'positionFloatingLayer' in js
    assert 'floatingLayerProfile' in js
    assert 'installComposerFloatingLayers' in js
    assert 'installPanelTransitions' in js
    assert "dataset.placement = preferAbove ? 'top' : 'bottom'" in js
    assert "dataset.alignment = profile.align" in js
    assert "toolbar.style.removeProperty('bottom')" in js
    assert "toolbar.style.bottom = '96px'" not in js
    assert "composer.style.removeProperty('padding-top')" in js
    assert "main.style.flexDirection = 'row'" in js
    assert "node.style.position = 'fixed'" in js
    assert "event.key.toLowerCase() === 'k'" in js
    assert 'fetch(' not in js
    assert 'Alpine.store(' not in js


def test_composer_is_single_pane_with_controls_outside_typing_surface():
    # Wash W3 composer (G§9.6): one surface (radius-lg, level 1) holding the
    # auto-growing textarea and, below it, the control bar; the hint row sits
    # outside the surface.
    css = COMPOSER_CSS.read_text(encoding="utf-8")
    html = CHAT_HTML.read_text(encoding="utf-8")
    form = css.split('.w-composer__form {', 1)[1].split('}', 1)[0]
    assert 'border-radius: var(--wash-radius-lg)' in form
    assert 'background: var(--wash-surface)' in form
    assert 'flex-direction: column' in form
    assert '.w-composer__bar {' in css
    assert '.w-composer__hint {' in css
    assert html.index('class="composer-input-wrap w-composer__input"') < html.index('class="composer-toolbar w-composer__bar"')
    assert html.index('class="composer-toolbar w-composer__bar"') < html.index('</form>') < html.index('class="hint w-composer__hint"')
    # The textarea is unframed inside the single surface.
    textarea = css.split('.w-composer__textarea {', 1)[1].split('}', 1)[0]
    assert 'border: 0' in textarea and 'background: transparent' in textarea


def test_composer_feature_and_provider_popouts_fit_content_cleanly():
    # Wash W4: the popout sizing moved from popouts.css into the chat patterns.
    css = CHAT_CSS.read_text(encoding="utf-8")
    js = PRODUCT_JS.read_text(encoding="utf-8")
    assert 'min-width: min(310px, calc(100vw - 20px))' in css
    assert 'max-width: min(420px, calc(100vw - 20px))' in css
    assert '.composer-mode-popout .mode-option .mode-desc' in css
    assert 'max-width: 44ch' in css
    assert 'min-width: min(460px, calc(100vw - 20px))' in css
    assert 'max-width: min(590px, calc(100vw - 20px))' in css
    assert '.composer-settings-popout .settings-header' in css
    assert 'position: sticky' in css
    assert 'grid-template-columns: minmax(110px, 142px) minmax(0, 1fr)' in css
    assert '.composer-settings-popout .settings-var-select' in css
    assert "return { minWidth: 460, maxWidth: 590, align: 'end' }" in js
    assert "return { minWidth: 310, maxWidth: 420, align: 'start' }" in js
    assert 'layer.scrollWidth' in js
    assert "layer.style.width = ''" in js
    assert "layer.style.maxHeight = ''" in js


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


def test_embedded_file_editor_and_codemirror_dependencies_are_removed():
    base = BASE.read_text(encoding="utf-8")
    files = FILES_PANEL.read_text(encoding="utf-8")
    for token in ('codemirror.min.css', 'codemirror.min.js', 'codemirror-simple.js', 'codemirror-modes.min.js'):
        assert token not in base
    for path in (
        ROOT / 'mu/gui/static/vendor/codemirror.min.css',
        ROOT / 'mu/gui/static/vendor/codemirror.min.js',
        ROOT / 'mu/gui/static/vendor/codemirror-simple.js',
        ROOT / 'mu/gui/static/vendor/codemirror-modes.min.js',
    ):
        assert not path.exists()
    assert 'CodeMirror' not in files
    assert 'files-editor-pane' not in files
    assert 'files-cm-host' not in files
    assert 'saveCurrent' not in files
    assert 'reloadFile' not in files
    assert 'filesBrowserPanel()' in files
    assert 'Browse the attached workspace' in files


def test_choice_picker_is_flat_not_card_based():
    # Wash W3 prompts (G§9.10): options are full-width selectable rows
    # (radius-md, sunken hover, accent wash + ✓ when selected), not cards; the
    # native input stays for keyboard/AT but is visually hidden.
    css = PROMPT_CSS.read_text(encoding="utf-8")
    html = CHAT_HTML.read_text(encoding="utf-8")
    row = css.split('.w-prompt__option {', 1)[1].split('}', 1)[0]
    assert 'border-radius: var(--wash-radius-md)' in row
    assert 'border:' not in row and 'box-shadow' not in row
    assert '.w-prompt__option:hover { background: var(--wash-sunken); }' in css
    assert '.w-prompt__option[data-selected] { background: var(--wash-accent-wash); }' in css
    hidden = css.split('.w-prompt__input {', 1)[1].split('}', 1)[0]
    assert 'opacity: 0' in hidden
    assert 'class="w-prompt__option"' in html


def test_settings_drawer_is_spacious_clean_and_consistent():
    # Wash W4: the settings drawer is a wide .w-drawer styled by
    # wash/patterns/settings.css (replacing the clarity.css / refinement.css
    # settings blocks): a 176px vertical tab rail beside a scrolling pane.
    settings = (ROOT / "mu" / "gui" / "static" / "css" / "wash" / "patterns" / "settings.css").read_text(encoding="utf-8")
    template = INSPECTOR.read_text(encoding="utf-8")
    assert 'width: min(1040px, 94vw)' in settings
    assert 'grid-template-columns: 176px minmax(0, 1fr)' in settings
    assert 'class="inspector-backdrop w-drawer"' in template
    assert 'class="inspector w-drawer__panel"' in template
    assert 'aria-orientation="vertical"' in template
    assert 'class="settings-pane"' in template
    assert 'class="settings-pane-head"' in template
    assert 'Configuration' in template
    assert 'Manage skills' in template
    assert '.settings-pane .var-group-head' in settings
    assert '.settings-pane .var-row' in settings
    # Wash Settings: each group is a hairline-separated section (no nested
    # cards); fields stack label above control with a muted hint, an inline
    # error and role=switch booleans.
    assert '.settings-pane .var-group {' in settings
    assert 'border-top: 1px solid var(--wash-hairline)' in settings
    assert '.settings-pane .var-hint' in settings
    assert '.settings-pane .var-error' in settings
    assert 'class="switch" role="switch"' in template
    assert 'class="var-hint w-field__hint"' in template
    assert 'class="var-row w-field"' in template
    var_row = settings.split('.settings-pane .var-row {', 1)[1].split('}', 1)[0]
    assert 'background: transparent;' in var_row
    assert '.settings-pane .var-row > input[aria-invalid="true"] ~ .var-error' in settings
    assert 'border-bottom: 1px solid var(--wash-hairline)' in settings


def test_trace_analyzer_uses_floating_glass_panes_and_themed_charts():
    css = TRACE_CSS.read_text(encoding="utf-8")
    route = ROUTE_CSS.read_text(encoding="utf-8")
    assert '@import url("/static/css/route-product.css")' in css
    assert '.trace-section {' in css
    assert 'border-radius: 22px' not in css
    assert 'background: color-mix(in srgb, var(--glass) 82%, transparent)' not in css
    assert 'backdrop-filter' not in css
    assert '.trace-canvas-wrap {' in css
    assert 'border-radius: var(--radius-sm)' in css
    assert 'filter: saturate' not in css
    assert 'html[data-theme="light"] .trace-canvas' not in css
    assert '.trace-card {' in css
    assert 'background: transparent' in css
    assert '.trace-info-popout' in css
    assert '--accent:' not in route
    assert '#79c2cb' not in route
    assert '#e0a040' not in route
    assert '.trace-mu{color:var(--text-secondary)}' in route


def test_container_management_uses_current_product_surface_without_breaking_ids():
    template = CONTAINERS.read_text(encoding="utf-8")
    # Wash W2: /containers links its page bundle importing the route styles.
    bundle = (ROOT / "mu" / "gui" / "static" / "css" / "wash" / "patterns" / "containers.css").read_text(encoding="utf-8")
    assert '/static/css/wash/patterns/containers.css' in template
    assert '@import url("../../route-product.css") layer(legacy);' in bundle
    assert '@import url("../../containers-crystal.css") layer(legacy);' in bundle
    for dom_id in (
        'container-list', 'template-list', 'create-environment-button',
        'create-modal', 'environment-name', 'shell-modal', 'shell-command',
    ):
        assert f'id="{dom_id}"' in template

    css = CONTAINER_CRYSTAL_CSS.read_text(encoding="utf-8")
    assert '.manager-card{display:grid' in css
    assert 'border-bottom:1px solid var(--hairline)' in css
    assert '.manager-monitor-grid' in css
    assert '.manager-overflow-menu' in css and 'var(--bg-raised)' in css
