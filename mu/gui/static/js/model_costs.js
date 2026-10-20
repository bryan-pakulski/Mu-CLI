(() => {
    'use strict';

    const state = { catalog: null, models: [], dirty: false, selected: null, provider: '' };
    const $ = id => document.getElementById(id);

    function esc(value) {
        return String(value ?? '')
            .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;').replaceAll("'", '&#039;');
    }
    function nullable(value) {
        const text = String(value ?? '').trim();
        if (!text) return null;
        const number = Number(text);
        return Number.isFinite(number) ? number : null;
    }
    // Every cell control is named for screen readers: "<column> for <model>".
    const FIELD_LABELS = {
        provider: 'Provider', key: 'Model', input_modalities: 'Native inputs', output_modalities: 'Outputs',
        capabilities: 'Capabilities', billing: 'Billing', input_per_million: 'Input per 1M tokens',
        cached_input_per_million: 'Cached input per 1M tokens', output_per_million: 'Output per 1M tokens',
        context_window: 'Context window', long_context_cutoff: 'Long-context cutoff',
        long_input_per_million: 'Long-context input per 1M', long_cached_input_per_million: 'Long-context cached input per 1M',
        long_output_per_million: 'Long-context output per 1M', aliases: 'Aliases', role: 'Role', notes: 'Notes',
    };
    let rowName = '';
    function label(field) { return esc(`${FIELD_LABELS[field] || field}${rowName ? ` for ${rowName}` : ''}`); }
    function input(value, field, type = 'text', extra = '') {
        const display = value == null ? '' : String(value);
        return `<input class="mc-cell-input" data-field="${field}" type="${type}" value="${esc(display)}" aria-label="${label(field)}" ${extra}>`;
    }
    function markDirty() {
        state.dirty = true;
        $('mc-dirty').hidden = false;
        $('mc-status').textContent = '';
    }
    // The state sentence (Mu §4.2): count · whose registry · the file, short (full path in the tooltip).
    function paintSource(data, { override } = {}) {
        const usingOverride = override ?? !!data.using_override;
        const path = usingOverride ? (data.active_config_path || data.config_path || '') : (data.config_path || '');
        $('mc-version').textContent = usingOverride ? 'Your pricing' : 'Packaged defaults';
        $('mc-version').title = `Registry ${data.version || ''}`.trim();
        $('mc-config-path').textContent = path ? path.split('/').pop() : '';
        $('mc-config-path').title = path ? (usingOverride ? `Active file: ${path}` : `Edit ${path} to override`) : '';
    }
    function paintCount() {
        const n = state.models.length;
        $('mc-count').textContent = `${n} model${n === 1 ? '' : 's'}`;
        $('mc-models-count').textContent = $('mc-count').textContent;
    }

    function billingSelect(value) {
        const options = [
            ['token', 'Token rates'],
            ['estimated_token', 'Estimated input/output'],
            ['local', 'Local / $0 API'],
            ['unknown', 'Unpriced'],
        ];
        return `<select class="mc-cell-input" data-field="billing" aria-label="${label('billing')}">${options.map(([key, label]) => `<option value="${key}" ${key === value ? 'selected' : ''}>${label}</option>`).join('')}</select>`;
    }

    // The list pane (Mu §11b): providers with their model counts filter the table.
    function renderProviders() {
        const list = $('mc-providers');
        if (!list) return;
        const counts = new Map();
        state.models.forEach(item => { const p = item.provider || 'unset'; counts.set(p, (counts.get(p) || 0) + 1); });
        if (state.provider && !counts.has(state.provider)) state.provider = '';
        const row = (key, name, n) => `<li class="w-row mc-provider" role="option" aria-selected="${state.provider === key ? 'true' : 'false'}">
                <button type="button" class="w-row__main" data-provider="${esc(key)}"><span class="w-row__title">${esc(name)}</span></button>
                <span class="w-row__when">${n}</span></li>`;
        list.innerHTML = row('', 'All providers', state.models.length) +
            [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).map(([p, n]) => row(p, p, n)).join('');
    }
    function render() {
        const needle = String($('mc-search').value || '').trim().toLowerCase();
        renderProviders();
        const rows = state.models.map((item, index) => {
            const search = `${item.provider} ${item.key} ${(item.aliases || []).join(' ')} ${(item.input_modalities || []).join(' ')} ${(item.output_modalities || []).join(' ')} ${(item.capabilities || []).join(' ')} ${item.role || ''} ${item.notes || ''}`.toLowerCase();
            const hidden = (needle && !search.includes(needle)) || (state.provider && (item.provider || 'unset') !== state.provider) ? ' hidden' : '';
            rowName = [item.provider, item.key].filter(Boolean).join(' ') || 'new model';
            return `<tr data-index="${index}" data-search="${esc(search)}"${hidden}>
                <td>${input(item.provider, 'provider')}</td>
                <td class="mc-model-cell">${input(item.key, 'key')}</td>
                <td>${input((item.input_modalities || ['text']).join(', '), 'input_modalities')}</td>
                <td>${input((item.output_modalities || ['text']).join(', '), 'output_modalities')}</td>
                <td>${input((item.capabilities || []).join(', '), 'capabilities')}</td>
                <td class="mc-billing-cell">${billingSelect(item.billing || 'token')}</td>
                <td>${input(item.input_per_million, 'input_per_million', 'number', 'min="0" step="0.001"')}</td>
                <td>${input(item.cached_input_per_million, 'cached_input_per_million', 'number', 'min="0" step="0.001"')}</td>
                <td>${input(item.output_per_million, 'output_per_million', 'number', 'min="0" step="0.001"')}</td>
                <td>${input(item.context_window, 'context_window', 'number', 'min="1" step="1"')}</td>
                <td>${input(item.long_context_cutoff, 'long_context_cutoff', 'number', 'min="1" step="1"')}</td>
                <td>${input(item.long_input_per_million, 'long_input_per_million', 'number', 'min="0" step="0.001"')}</td>
                <td>${input(item.long_cached_input_per_million, 'long_cached_input_per_million', 'number', 'min="0" step="0.001"')}</td>
                <td>${input(item.long_output_per_million, 'long_output_per_million', 'number', 'min="0" step="0.001"')}</td>
                <td>${input((item.aliases || []).join(', '), 'aliases')}</td>
                <td><div class="mc-notes-cell">
                    ${input(item.role || '', 'role', 'text', 'placeholder="Role"')}
                    <textarea class="mc-cell-input" data-field="notes" rows="1" placeholder="Notes" aria-label="${label('notes')}">${esc(item.notes || '')}</textarea>
                </div></td>
                <td class="w-table__actions"><button type="button" class="w-btn mc-row-remove" data-variant="icon" data-size="sm" data-remove title="Remove model" aria-label="Remove ${esc(rowName)}"><svg class="w-icon" aria-hidden="true" focusable="false"><use href="/static/icons/wash.svg#i-x"></use></svg></button></td>
            </tr>`;
        }).join('');
        $('mc-models').innerHTML = rows || '<tr><td colspan="17" class="mc-table-empty">No configured models.</td></tr>';
        $('mc-table').toggleAttribute('data-provider-filtered', !!state.provider);
        paintCount();
        wireRows();
        refreshDock();
    }

    function updateField(index, field, raw) {
        const item = state.models[index];
        if (!item) return;
        if (['aliases', 'input_modalities', 'output_modalities', 'capabilities'].includes(field)) {
            item[field] = String(raw || '').split(',').map(value => {
                const clean = value.trim();
                return field === 'aliases' ? clean : clean.toLowerCase().replaceAll(' ', '_');
            }).filter(Boolean);
        } else if ([
            'input_per_million', 'cached_input_per_million', 'output_per_million',
            'estimated_total_per_million', 'context_window', 'long_context_cutoff',
            'long_input_per_million', 'long_cached_input_per_million', 'long_output_per_million',
        ].includes(field)) {
            item[field] = nullable(raw);
        } else {
            item[field] = String(raw ?? '');
        }
        markDirty();
    }

    function wireRows() {
        $('mc-models').querySelectorAll('tr[data-index]').forEach(row => {
            const index = Number(row.dataset.index);
            row.querySelectorAll('[data-field]').forEach(control => {
                const eventName = control.tagName === 'SELECT' ? 'change' : 'input';
                control.addEventListener(eventName, () => {
                    updateField(index, control.dataset.field, control.value);
                    syncDock(index, control.dataset.field, control.value);
                });
            });
            // Focusing or clicking a row shows it in the dock (wide screens only).
            row.addEventListener('focusin', () => selectRow(index));
            row.addEventListener('click', () => selectRow(index));
            row.querySelector('[data-remove]')?.addEventListener('click', event => {
                event.stopPropagation();
                removeModel(index);
            });
        });
    }

    function removeModel(index) {
        state.models.splice(index, 1);
        if (state.selected === index) state.selected = null;
        else if (state.selected != null && state.selected > index) state.selected -= 1;
        markDirty();
        render();
    }

    // ------------------------------------------------------------ dock (Mu §7.2)
    // From 1200px the selected model docks beside the table with every field, so the
    // table keeps its six key columns. Edits go through updateField and mirror the row.
    const DOCK_FIELDS = [
        ['Identity', [['provider', 'text'], ['key', 'text'], ['aliases', 'text']]],
        ['Billing', [['billing', 'select'], ['input_per_million', 'number'], ['cached_input_per_million', 'number'], ['output_per_million', 'number']]],
        ['Context', [['context_window', 'number'], ['long_context_cutoff', 'number'], ['long_input_per_million', 'number'],
            ['long_cached_input_per_million', 'number'], ['long_output_per_million', 'number']]],
        ['Capabilities', [['input_modalities', 'text'], ['output_modalities', 'text'], ['capabilities', 'text']]],
        ['Notes', [['role', 'text'], ['notes', 'textarea']]],
    ];
    const LIST_FIELDS = ['aliases', 'input_modalities', 'output_modalities', 'capabilities'];
    function fieldValue(item, field) {
        const value = item[field];
        if (LIST_FIELDS.includes(field)) return (value || []).join(', ');
        return value == null ? '' : String(value);
    }
    function dockControl(item, field, type) {
        const id = `mc-dock-${field}`;
        const name = esc(FIELD_LABELS[field] || field);
        const value = fieldValue(item, field);
        let control;
        if (type === 'select') {
            rowName = '';
            control = `<span class="mc-dock-select">${billingSelect(item.billing || 'token').replace('class="mc-cell-input"', `class="mc-cell-input mc-dock-input" id="${id}"`)}</span>`;
        } else if (type === 'textarea') {
            control = `<textarea class="mc-cell-input mc-dock-input" id="${id}" data-field="${field}" rows="3">${esc(value)}</textarea>`;
        } else {
            const extra = type === 'number' ? ' min="0" step="any"' : '';
            control = `<input class="mc-cell-input mc-dock-input" id="${id}" data-field="${field}" type="${type}" value="${esc(value)}"${extra}>`;
        }
        return `<label class="mc-dock-label" for="${id}">${name}</label>${control}`;
    }
    function selectRow(index, { focus = false } = {}) {
        if (!window.WashSheet || !window.WashSheet.docked()) return;
        const item = state.models[index];
        if (!item) return;
        if (state.selected === index && window.WashSheet.isOpen()) return;
        state.selected = index;
        const row = $('mc-models').querySelector(`tr[data-index="${index}"]`);
        const body = DOCK_FIELDS.map(([group, fields]) => `<section class="mc-dock-group" aria-label="${esc(group)}">
                <h3 class="mc-dock-group__title">${esc(group)}</h3>
                <div class="mc-dock-grid">${fields.map(([field, type]) => dockControl(item, field, type)).join('')}</div>
            </section>`).join('') +
            `<div class="mc-dock-actions"><button type="button" class="w-textbtn" data-tone="danger" data-dock-remove>Remove this model</button></div>`;
        const handle = window.WashSheet.open({
            title: item.key || 'New model',
            state: `<span class="w-state__part">${esc(item.provider || '')}</span><span class="w-state__part">${esc(billingLabel(item.billing))}</span>`,
            body,
            row,
            focus,
            onClose: () => { state.selected = null; },
        });
        handle.body.querySelectorAll('[data-field]').forEach(control => {
            const eventName = control.tagName === 'SELECT' ? 'change' : 'input';
            control.addEventListener(eventName, () => {
                const idx = state.selected;
                if (idx == null) return;
                updateField(idx, control.dataset.field, control.value);
                const cell = $('mc-models').querySelector(`tr[data-index="${idx}"] [data-field="${control.dataset.field}"]`);
                if (cell && cell !== control) cell.value = control.value;
                if (control.dataset.field === 'key') {
                    const title = handle.el.querySelector('.w-sheet__title');
                    if (title) title.textContent = control.value || 'New model';
                }
            });
        });
        handle.body.querySelector('[data-dock-remove]')?.addEventListener('click', () => removeModel(index));
    }
    function billingLabel(value) {
        return ({ token: 'Token rates', estimated_token: 'Estimated input/output', local: 'Local / $0 API', unknown: 'Unpriced' })[value] || 'Token rates';
    }
    function syncDock(index, field, value) {
        if (state.selected !== index) return;
        const control = document.getElementById(`mc-dock-${field}`);
        if (control && control.value !== value) control.value = value;
    }
    // After a render (filter, add, remove, save): keep the dock on the same model, or the first.
    function refreshDock() {
        if (!window.WashSheet || !window.WashSheet.docked() || !state.models.length) return;
        const keep = state.selected != null && state.models[state.selected] ? state.selected : 0;
        state.selected = null;
        selectRow(keep);
    }

    function addModel() {
        state.models.unshift({
            provider: 'ollama', key: '', billing: 'unknown', aliases: [],
            input_modalities: ['text'], output_modalities: ['text'], capabilities: [],
            input_per_million: null, cached_input_per_million: null, output_per_million: null,
            estimated_total_per_million: null, context_window: null,
            long_context_cutoff: null, long_input_per_million: null,
            long_cached_input_per_million: null, long_output_per_million: null,
            role: '', notes: '', source: 'operator configuration',
        });
        state.selected = 0;
        markDirty();
        render();
        $('mc-models').querySelector('input[data-field="key"]')?.focus();
    }

    async function load() {
        const response = await fetch('/api/providers/pricing');
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
        state.catalog = data;
        state.models = JSON.parse(JSON.stringify(data.models || []));
        state.dirty = false;
        $('mc-dirty').hidden = true;
        paintSource(data);
        render();
    }

    async function save() {
        const invalid = state.models.find(item => !String(item.provider || '').trim() || !String(item.key || '').trim());
        if (invalid) {
            $('mc-status').textContent = 'Provider and model are required on every row.';
            return;
        }
        const invalidEstimate = state.models.find(item => {
            if (item.billing !== 'estimated_token') return false;
            const split = item.input_per_million != null || item.output_per_million != null;
            return split
                ? item.input_per_million == null || item.output_per_million == null
                : item.estimated_total_per_million == null;
        });
        if (invalidEstimate) {
            $('mc-status').textContent = 'Estimated pricing requires both input and output rates.';
            return;
        }
        $('mc-save').disabled = true;
        $('mc-status').textContent = 'Saving…';
        try {
            const response = await fetch('/api/providers/pricing', {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    version: new Date().toISOString(),
                    currency: state.catalog?.currency || 'USD',
                    unit: state.catalog?.unit || 'per_million_tokens',
                    models: state.models,
                }),
            });
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
            state.catalog = data;
            state.models = JSON.parse(JSON.stringify(data.models || []));
            state.dirty = false;
            $('mc-dirty').hidden = true;
            paintSource(data, { override: true });
            $('mc-status').textContent = 'Saved.';
            render();
        } catch (error) {
            $('mc-status').textContent = `Save failed: ${error.message}`;
        } finally {
            $('mc-save').disabled = false;
        }
    }

    async function reset() {
        const confirmed = await window.WashConfirm({
            title: 'Reset pricing to defaults?',
            message: 'This removes your pricing override and returns every model to the packaged defaults.',
            confirmLabel: 'Reset pricing',
            tone: 'danger',
        });
        if (!confirmed) return;
        $('mc-status').textContent = 'Resetting…';
        const response = await fetch('/api/providers/pricing/reset', { method: 'POST' });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            $('mc-status').textContent = `Reset failed: ${data.detail || response.status}`;
            return;
        }
        state.catalog = data;
        state.models = JSON.parse(JSON.stringify(data.models || []));
        state.dirty = false;
        $('mc-dirty').hidden = true;
        paintSource(data, { override: false });
        $('mc-status').textContent = 'Using packaged defaults.';
        render();
    }

    function toggleColumns() {
        const table = $('mc-table'), button = $('mc-columns');
        const all = table.dataset.columns !== 'all';
        table.dataset.columns = all ? 'all' : 'key';
        button.setAttribute('aria-pressed', String(all));
        button.textContent = all ? 'Key columns' : 'All columns';
    }

    async function init() {
        $('mc-search').addEventListener('input', render);
        $('mc-providers')?.addEventListener('click', event => {
            const button = event.target.closest('[data-provider]');
            if (!button) return;
            state.provider = button.dataset.provider;
            render();
            // The dock follows the filter: keep its model if it is still listed, else the first row shown.
            const shown = [...document.querySelectorAll('#mc-models tr[data-index]:not([hidden])')].map(tr => Number(tr.dataset.index));
            if (shown.length && !shown.includes(state.selected)) { state.selected = null; selectRow(shown[0]); }
        });
        $('mc-add').addEventListener('click', addModel);
        $('mc-save').addEventListener('click', save);
        $('mc-reset').addEventListener('click', () => {
            document.querySelector('.mc-more')?.removeAttribute('open');
            reset();
        });
        // The ⋯ menu closes on outside click and Esc.
        document.addEventListener('click', event => {
            const more = document.querySelector('.mc-more');
            if (more && more.open && !more.contains(event.target)) more.removeAttribute('open');
        });
        document.addEventListener('keydown', event => {
            const more = document.querySelector('.mc-more');
            if (event.key === 'Escape' && more && more.open) { more.removeAttribute('open'); more.querySelector('summary')?.focus(); }
        });
        // Six columns by default (Mu §5.6); "All columns" shows modalities, cached and long-context rates, aliases and notes.
        $('mc-columns').addEventListener('click', toggleColumns);
        // Command palette (static/js/wash/palette.js): this page's actions.
        if (window.WashPalette) {
            window.WashPalette.register('model-costs', () => [
                { id: 'mc:add', group: 'Actions', label: 'Add a model', icon: 'plus', run: addModel },
                { id: 'mc:save', group: 'Actions', label: 'Save pricing', icon: 'check', enabled: !$('mc-save').disabled, run: save },
                { id: 'mc:reset', group: 'Actions', label: 'Reset pricing to defaults…', icon: 'rotate-ccw', run: reset },
                { id: 'mc:columns', group: 'View', label: 'Show all pricing columns', icon: 'layers', run: toggleColumns },
            ], { priority: 10 });
        }
        window.addEventListener('beforeunload', event => {
            if (!state.dirty) return;
            event.preventDefault();
            event.returnValue = '';
        });
        try {
            await load();
            $('mc-loading').hidden = true;
            $('mc-main').hidden = false;
            refreshDock();   // the dock is only measurable once the registry shows
        } catch (error) {
            $('mc-loading').hidden = true;
            $('mc-error').hidden = false;
            $('mc-error').textContent = `Could not load model pricing: ${error.message}`;
        }
    }

    void init();
})();
