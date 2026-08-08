/* MuCLI Product UI — presentation-only enhancements.
 * No backend/store semantics live here. This file handles copy, focus,
 * floating-layer geometry and visual transitions only.
 */
(function () {
    const FLOAT_MARGIN = 10;
    const FLOAT_GAP = 9;

    function setText(selector, value) {
        const node = document.querySelector(selector);
        if (node) node.textContent = value;
    }

    function polishWelcomeCopy() {
        setText('.welcome-kicker', 'AI workspace');
        setText('.welcome-hero h1', 'What are you working on?');
        setText('.welcome-hero .lede', 'Start a new workspace or pick up where you left off.');

        const entries = Array.from(document.querySelectorAll('.welcome-entry'));
        const copy = [
            ['Continue a session', 'Open recent work and keep going'],
            ['Start something new', 'Create a chat, workspace, or isolated environment'],
            ['Environments', 'Manage isolated development environments'],
        ];
        entries.slice(0, copy.length).forEach((entry, index) => {
            const strong = entry.querySelector('.welcome-entry-copy strong');
            const small = entry.querySelector('.welcome-entry-copy small');
            if (strong) strong.textContent = copy[index][0];
            if (small && !small.hasAttribute('x-text')) small.textContent = copy[index][1];
        });

        const footnote = document.querySelector('.welcome-footnote > span');
        if (footnote) footnote.textContent = 'Your model, workspace, and execution environment stay under your control.';
    }

    function focusComposer() {
        const input = document.querySelector('.composer textarea');
        if (!input) return false;
        input.focus();
        input.setSelectionRange(input.value.length, input.value.length);
        return true;
    }

    function preserveOverlayGeometry() {
        const overlays = [
            ['.product-app > .inspector-backdrop', 120],
            ['.product-app > .file-browser-backdrop', 220],
            ['.product-app > .modal-backdrop', 240],
        ];
        for (const [selector, zIndex] of overlays) {
            document.querySelectorAll(selector).forEach(node => {
                node.style.position = 'fixed';
                node.style.zIndex = String(zIndex);
            });
        }
    }

    function refineComposerGeometry() {
        const composer = document.querySelector('.composer');
        const toolbar = document.querySelector('.composer-toolbar');
        if (!composer || !toolbar) return;
        // The legacy toolbar sat on top of the textarea. Keep it in the
        // composer hierarchy, but give it its own quiet line above input.
        composer.style.paddingTop = '58px';
        toolbar.style.bottom = '96px';
        toolbar.style.gap = '2px';
        const value = toolbar.querySelector('.composer-mode-pill .value');
        if (value) value.style.textTransform = 'capitalize';
    }

    function isVisible(node) {
        if (!node || node.hidden) return false;
        const style = window.getComputedStyle(node);
        return style.display !== 'none' && style.visibility !== 'hidden';
    }

    function clamp(value, min, max) {
        return Math.min(Math.max(value, min), Math.max(min, max));
    }

    function floatingLayerProfile(layer) {
        if (layer.classList.contains('composer-settings-popout')) {
            return { minWidth: 460, maxWidth: 590, align: 'end' };
        }
        if (layer.classList.contains('composer-mode-popout')) {
            return { minWidth: 310, maxWidth: 420, align: 'start' };
        }
        return { minWidth: 260, maxWidth: 440, align: 'start' };
    }

    function positionFloatingLayer(layer, anchor) {
        if (!layer || !anchor || !isVisible(layer)) return;

        layer.dataset.productFloating = 'true';
        layer.style.visibility = 'hidden';
        // Clear the previous inline constraints before measuring. This lets
        // each popout size from its actual content on every open/reflow rather
        // than inheriting a stale width or height from the previous viewport.
        layer.style.width = '';
        layer.style.maxHeight = '';

        const profile = floatingLayerProfile(layer);
        const anchorRect = anchor.getBoundingClientRect();
        const layerRect = layer.getBoundingClientRect();
        const viewportMaxWidth = Math.max(220, window.innerWidth - FLOAT_MARGIN * 2);
        const effectiveMaxWidth = Math.min(profile.maxWidth, viewportMaxWidth);
        const effectiveMinWidth = Math.min(profile.minWidth, effectiveMaxWidth);
        const naturalWidth = Math.max(layerRect.width || 0, layer.scrollWidth || 0, effectiveMinWidth);
        const width = clamp(naturalWidth, effectiveMinWidth, effectiveMaxWidth);

        const naturalHeight = Math.max(layerRect.height || 0, layer.scrollHeight || 0);
        const roomAbove = anchorRect.top - FLOAT_MARGIN;
        const roomBelow = window.innerHeight - anchorRect.bottom - FLOAT_MARGIN;
        const preferAbove = roomAbove >= Math.min(naturalHeight + FLOAT_GAP, 340) || roomAbove > roomBelow;
        const available = Math.max(170, (preferAbove ? roomAbove : roomBelow) - FLOAT_GAP);
        const height = Math.min(naturalHeight, available);

        let top = preferAbove
            ? anchorRect.top - height - FLOAT_GAP
            : anchorRect.bottom + FLOAT_GAP;
        top = clamp(top, FLOAT_MARGIN, window.innerHeight - height - FLOAT_MARGIN);

        let left = profile.align === 'end'
            ? anchorRect.right - width
            : anchorRect.left;
        left = clamp(left, FLOAT_MARGIN, window.innerWidth - width - FLOAT_MARGIN);

        layer.style.width = `${Math.round(width)}px`;
        layer.style.maxHeight = `${Math.round(available)}px`;
        layer.style.left = `${Math.round(left)}px`;
        layer.style.top = `${Math.round(top)}px`;
        layer.style.visibility = '';
        layer.dataset.placement = preferAbove ? 'top' : 'bottom';
        layer.dataset.alignment = profile.align;
    }

    function installFloatingLayer(wrapperSelector, layerSelector, anchorSelector) {
        const wrapper = document.querySelector(wrapperSelector);
        if (!wrapper) return;
        const layer = wrapper.querySelector(layerSelector);
        const anchor = wrapper.querySelector(anchorSelector);
        if (!layer || !anchor) return;

        const reposition = () => requestAnimationFrame(() => positionFloatingLayer(layer, anchor));
        anchor.addEventListener('click', reposition);
        window.addEventListener('resize', reposition, { passive: true });
        document.addEventListener('scroll', reposition, { passive: true, capture: true });
        if (typeof ResizeObserver !== 'undefined') new ResizeObserver(reposition).observe(layer);
    }

    function installComposerFloatingLayers() {
        installFloatingLayer('.composer-mode-picker', '.composer-mode-popout', '.composer-mode-pill');
        installFloatingLayer('.composer-settings', '.composer-settings-popout', '.composer-settings-btn');
    }

    function animateVisiblePanel(panel) {
        if (!panel || !isVisible(panel) || panel.dataset.productEntering === 'true') return;
        panel.dataset.productEntering = 'true';
        panel.classList.add('product-panel-enter');
        requestAnimationFrame(() => requestAnimationFrame(() => {
            panel.classList.remove('product-panel-enter');
            window.setTimeout(() => { delete panel.dataset.productEntering; }, 240);
        }));
    }

    function installPanelTransitions() {
        const stage = document.querySelector('.panel-stage');
        if (!stage) return;
        const main = stage.closest('.main');
        if (main) main.style.flexDirection = 'row';

        const panels = Array.from(stage.querySelectorAll('.mode-panel'));
        const observer = new MutationObserver(records => {
            for (const record of records) {
                const panel = record.target;
                if (panel.classList && panel.classList.contains('mode-panel') && isVisible(panel)) {
                    animateVisiblePanel(panel);
                }
            }
        });
        panels.forEach(panel => {
            observer.observe(panel, { attributes: true, attributeFilter: ['style'] });
            if (isVisible(panel)) animateVisiblePanel(panel);
        });
    }

    document.addEventListener('DOMContentLoaded', () => {
        document.documentElement.classList.add('mucli-product-ui');
        polishWelcomeCopy();
        preserveOverlayGeometry();
        refineComposerGeometry();
        installComposerFloatingLayers();
        installPanelTransitions();
    });

    document.addEventListener('keydown', (event) => {
        if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
            const active = document.activeElement;
            const isEditable = active && (
                active.tagName === 'INPUT' || active.tagName === 'TEXTAREA' || active.isContentEditable
            );
            if (!isEditable && focusComposer()) event.preventDefault();
        }
    });
})();
