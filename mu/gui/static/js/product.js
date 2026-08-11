/* MuCLI Product UI — presentation enhancements and product-level settings surfaces.
 * Core runtime semantics stay server-side; this file owns lightweight web-shell
 * integration such as copy/focus behavior, floating geometry, transitions, and
 * globally-scoped operator settings that already have dedicated APIs.
 */
(function () {
    const FLOAT_MARGIN = 10;
    const FLOAT_GAP = 9;

    function installStylesheet(id, href) {
        if (document.getElementById(id)) return;
        const link = document.createElement('link');
        link.id = id;
        link.rel = 'stylesheet';
        link.href = href;
        document.head.appendChild(link);
    }

    // product.js is loaded synchronously by the main web shell, so install late
    // override sheets immediately and avoid first-open style flashes.
    installStylesheet('mucli-popouts-css', '/static/css/popouts.css');
    installStylesheet('mucli-pricing-settings-css', '/static/css/pricing_settings.css');

    document.addEventListener('alpine:init', () => {
        Alpine.store('pricingSettings', {
            loaded: false,
            loading: false,
            saving: false,
            dirty: false,
            error: '',
            provider: 'all',
            version: '',
            currency: 'USD',
            unit: 'per_million_tokens',
            models: [],
            configPath: '',
            activeConfigPath: '',
            defaultConfigPath: '',
            usingOverride: false,

            _applyCatalog(data) {
                const catalog = data && typeof data === 'object' ? data : {};
                this.version = String(catalog.version || 'custom');
                this.currency = String(catalog.currency || 'USD');
                this.unit = String(catalog.unit || 'per_million_tokens');
                this.models = Array.isArray(catalog.models)
                    ? catalog.models.map(row => ({ ...row }))
                    : [];
                this.configPath = String(catalog.config_path || '');
                this.activeConfigPath = String(catalog.active_config_path || '');
                this.defaultConfigPath = String(catalog.default_config_path || '');
                this.usingOverride = !!catalog.using_override;
                this.loaded = true;
                this.dirty = false;
                this.error = '';
            },

            async load(force = false) {
                if (this.loaded && !force) return;
                this.loading = true;
                this.error = '';
                try {
                    const response = await fetch('/api/providers/pricing', { cache: 'no-store' });
                    const data = await response.json().catch(() => ({}));
                    if (!response.ok) throw new Error(data.detail || `pricing load failed (${response.status})`);
                    this._applyCatalog(data);
                } catch (error) {
                    this.error = String(error instanceof Error ? error.message : error);
                } finally {
                    this.loading = false;
                }
            },

            filteredModels() {
                const provider = String(this.provider || 'all').toLowerCase();
                if (provider === 'all') return this.models;
                return this.models.filter(row => String(row.provider || '').toLowerCase() === provider);
            },

            providerCount(provider) {
                const target = String(provider || '').toLowerCase();
                return this.models.filter(row => String(row.provider || '').toLowerCase() === target).length;
            },

            displayRate(value) {
                return value === null || value === undefined ? '' : String(value);
            },

            setRate(row, field, raw) {
                if (!row || !field) return;
                const text = String(raw ?? '').trim();
                if (!text) {
                    row[field] = null;
                    this.dirty = true;
                    return;
                }
                const value = Number(text);
                if (!Number.isFinite(value) || value < 0) {
                    this.error = `${field} must be a non-negative number`;
                    return;
                }
                row[field] = value;
                this.error = '';
                this.dirty = true;
            },

            setBilling(row, value) {
                if (!row) return;
                row.billing = String(value || 'unknown');
                this.dirty = true;
            },

            sourceLabel() {
                if (!this.loaded) return 'Pricing registry not loaded';
                if (this.usingOverride) return `Operator override · ${this.activeConfigPath || this.configPath}`;
                return `Packaged defaults · ${this.activeConfigPath || this.defaultConfigPath}`;
            },

            async save() {
                if (this.saving) return;
                this.saving = true;
                this.error = '';
                try {
                    const response = await fetch('/api/providers/pricing', {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            version: this.version || 'operator',
                            currency: this.currency || 'USD',
                            unit: this.unit || 'per_million_tokens',
                            models: this.models.map(row => ({ ...row })),
                        }),
                    });
                    const data = await response.json().catch(() => ({}));
                    if (!response.ok) throw new Error(data.detail || `pricing save failed (${response.status})`);
                    this._applyCatalog(data);
                    Alpine.store('toast').show('Model pricing saved', 'success');
                } catch (error) {
                    this.error = String(error instanceof Error ? error.message : error);
                    Alpine.store('toast').show(this.error, 'error', 7000);
                } finally {
                    this.saving = false;
                }
            },

            async reset() {
                this.saving = true;
                this.error = '';
                try {
                    const response = await fetch('/api/providers/pricing/reset', { method: 'POST' });
                    const data = await response.json().catch(() => ({}));
                    if (!response.ok) throw new Error(data.detail || `pricing reset failed (${response.status})`);
                    this._applyCatalog(data);
                    Alpine.store('toast').show('Model pricing reset to packaged defaults', 'success');
                } catch (error) {
                    this.error = String(error instanceof Error ? error.message : error);
                    Alpine.store('toast').show(this.error, 'error', 7000);
                } finally {
                    this.saving = false;
                }
            },
        });
    });

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
        const toolbar = document.querySelector('.composer-toolbar');
        if (!toolbar) return;
        // Composer controls now live in normal flow above the input. Remove
        // any legacy inline geometry from earlier product passes so textarea
        // growth can never overlap mode/session controls.
        toolbar.style.removeProperty('bottom');
        toolbar.style.removeProperty('gap');
        const composer = toolbar.closest('.composer');
        if (composer) composer.style.removeProperty('padding-top');
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

    function installPricingSettings() {
        const tabs = document.querySelector('.inspector-tabs');
        const body = document.querySelector('.inspector-body');
        if (!tabs || !body || document.getElementById('pricing-settings-pane')) return;

        const tab = document.createElement('button');
        tab.type = 'button';
        tab.setAttribute(':class', "{ active: $store.inspector.tab === 'pricing' }");
        tab.setAttribute('@click', "$store.inspector.setTab('pricing'); $store.pricingSettings.load()");
        tab.setAttribute('role', 'tab');
        tab.textContent = 'pricing';
        const settingsTab = Array.from(tabs.querySelectorAll('button')).find(
            button => button.textContent.trim().toLowerCase() === 'settings'
        );
        tabs.insertBefore(tab, settingsTab || null);

        const pane = document.createElement('section');
        pane.id = 'pricing-settings-pane';
        pane.className = 'settings-pane pricing-settings-pane';
        pane.setAttribute('x-show', "$store.inspector.tab === 'pricing'");
        pane.innerHTML = `
            <div class="pricing-settings-head">
                <div>
                    <h3 class="pricing-settings-title">Model pricing</h3>
                    <p class="pricing-settings-copy">Edit the per-million token rates MuCLI uses for cost accounting. OpenAI, Gemini, and Ollama Cloud all use this registry. Local Ollama remains $0 attributable provider/API cost; host compute is intentionally separate.</p>
                </div>
                <a class="pricing-settings-advanced" href="/static/model_costs.html">Advanced registry</a>
            </div>

            <div class="pricing-settings-meta" x-show="$store.pricingSettings.loaded">
                <span class="pricing-settings-source" x-text="$store.pricingSettings.sourceLabel()" :title="$store.pricingSettings.activeConfigPath || $store.pricingSettings.configPath"></span>
                <span class="pricing-settings-state"
                      :class="{ 'is-override': $store.pricingSettings.usingOverride }"
                      x-text="$store.pricingSettings.usingOverride ? 'override active' : 'defaults'"></span>
            </div>

            <div class="pricing-provider-filter" x-show="$store.pricingSettings.loaded">
                <button type="button" :class="{ active: $store.pricingSettings.provider === 'all' }" @click="$store.pricingSettings.provider = 'all'">all</button>
                <button type="button" :class="{ active: $store.pricingSettings.provider === 'openai' }" @click="$store.pricingSettings.provider = 'openai'">OpenAI <span x-text="$store.pricingSettings.providerCount('openai')"></span></button>
                <button type="button" :class="{ active: $store.pricingSettings.provider === 'gemini' }" @click="$store.pricingSettings.provider = 'gemini'">Gemini <span x-text="$store.pricingSettings.providerCount('gemini')"></span></button>
                <button type="button" :class="{ active: $store.pricingSettings.provider === 'ollama' }" @click="$store.pricingSettings.provider = 'ollama'">Ollama <span x-text="$store.pricingSettings.providerCount('ollama')"></span></button>
                <button type="button" @click="$store.pricingSettings.load(true)">refresh</button>
            </div>

            <div class="pricing-settings-error" x-show="$store.pricingSettings.error" x-text="$store.pricingSettings.error"></div>
            <div class="pricing-settings-empty" x-show="$store.pricingSettings.loading">Loading model pricing…</div>

            <div class="pricing-model-list" x-show="$store.pricingSettings.loaded && !$store.pricingSettings.loading">
                <template x-for="row in $store.pricingSettings.filteredModels()" :key="row.provider + ':' + row.key">
                    <article class="pricing-model-row">
                        <div class="pricing-model-head">
                            <span class="pricing-provider-badge" x-text="row.provider"></span>
                            <span class="pricing-model-name" x-text="row.key" :title="row.key"></span>
                            <select class="pricing-billing-select" :value="row.billing" @change="$store.pricingSettings.setBilling(row, $event.target.value)">
                                <option value="token">token priced</option>
                                <option value="estimated_token">estimated token</option>
                                <option value="local">local / $0 API</option>
                                <option value="unknown">unpriced</option>
                            </select>
                        </div>
                        <div class="pricing-rate-grid">
                            <label class="pricing-rate-field">
                                <span>Input / 1M</span>
                                <input type="number" min="0" step="0.001"
                                       :disabled="row.billing === 'local' || row.billing === 'unknown'"
                                       :value="$store.pricingSettings.displayRate(row.input_per_million)"
                                       @input="$store.pricingSettings.setRate(row, 'input_per_million', $event.target.value)">
                            </label>
                            <label class="pricing-rate-field">
                                <span>Cached input / 1M</span>
                                <input type="number" min="0" step="0.001"
                                       :disabled="row.billing === 'local' || row.billing === 'unknown'"
                                       :value="$store.pricingSettings.displayRate(row.cached_input_per_million)"
                                       @input="$store.pricingSettings.setRate(row, 'cached_input_per_million', $event.target.value)">
                            </label>
                            <label class="pricing-rate-field">
                                <span>Output / 1M</span>
                                <input type="number" min="0" step="0.001"
                                       :disabled="row.billing === 'local' || row.billing === 'unknown'"
                                       :value="$store.pricingSettings.displayRate(row.output_per_million)"
                                       @input="$store.pricingSettings.setRate(row, 'output_per_million', $event.target.value)">
                            </label>
                        </div>
                        <p class="pricing-model-note" x-show="row.billing === 'local'">Local Ollama provider/API cost is recorded as $0. Host GPU/CPU economics are not included.</p>
                        <p class="pricing-model-note" x-show="row.billing === 'unknown'">This model remains unpriced until you select token pricing and enter rates.</p>
                        <p class="pricing-model-note" x-show="row.notes && row.billing !== 'local' && row.billing !== 'unknown'" x-text="row.notes"></p>
                    </article>
                </template>
            </div>

            <div class="pricing-settings-empty" x-show="$store.pricingSettings.loaded && !$store.pricingSettings.loading && $store.pricingSettings.filteredModels().length === 0">No pricing rows for this provider.</div>

            <div class="pricing-settings-actions" x-show="$store.pricingSettings.loaded">
                <span class="pricing-settings-dirty" x-text="$store.pricingSettings.dirty ? 'Unsaved pricing changes' : ($store.pricingSettings.usingOverride ? 'Operator pricing override active' : 'Using packaged defaults')"></span>
                <div class="pricing-settings-buttons">
                    <button type="button" @click="$store.confirm.ask('Reset all model pricing to packaged defaults?', $event, () => $store.pricingSettings.reset(), {danger:true})">reset</button>
                    <button type="button" class="primary" :disabled="!$store.pricingSettings.dirty || $store.pricingSettings.saving" @click="$store.pricingSettings.save()" x-text="$store.pricingSettings.saving ? 'saving…' : 'save pricing'"></button>
                </div>
            </div>
        `;
        body.appendChild(pane);

        if (window.Alpine && typeof Alpine.initTree === 'function') {
            Alpine.initTree(tab);
            Alpine.initTree(pane);
        }
    }

    document.addEventListener('DOMContentLoaded', () => {
        document.documentElement.classList.add('mucli-product-ui');
        polishWelcomeCopy();
        preserveOverlayGeometry();
        refineComposerGeometry();
        installComposerFloatingLayers();
        installPanelTransitions();
        installPricingSettings();
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
