(() => {
    'use strict';

    const state = { catalog: null };
    const $ = id => document.getElementById(id);

    function esc(value) {
        return String(value ?? '')
            .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;').replaceAll("'", '&#039;');
    }
    function money(value) { return value == null ? '—' : `$${Number(value).toFixed(Number(value) < 1 ? 3 : 2)}`; }
    function number(value) { return value == null ? '—' : Number(value).toLocaleString(); }
    function rowSearch(row, needle) { return !needle || String(row.dataset.search || '').includes(needle); }

    function renderNotes(catalog) {
        $('mc-notes').innerHTML = Object.entries(catalog.provider_notes || {}).map(([key, value]) => `
            <div class="mc-note"><span>${esc(key.replaceAll('_', ' '))}</span><p>${esc(value)}</p></div>
        `).join('');
    }

    function renderTokenModels(catalog) {
        const rows = catalog.models || [];
        $('mc-token-models').innerHTML = rows.map(item => {
            const high = item.long_context_cutoff
                ? `>${number(item.long_context_cutoff)}: ${money(item.long_input_per_million)} / ${money(item.long_cached_input_per_million)} / ${money(item.long_output_per_million)}`
                : (item.context_window ? number(item.context_window) : 'standard tier');
            const search = `${item.provider} ${item.key} ${item.role || ''} ${item.notes || ''}`.toLowerCase();
            return `<tr data-model-row data-search="${esc(search)}">
                <td>${esc(item.provider)}</td>
                <td><strong>${esc(item.key)}</strong>${item.notes ? `<small>${esc(item.notes)}</small>` : ''}</td>
                <td>${money(item.input_per_million)}</td>
                <td>${money(item.cached_input_per_million)}</td>
                <td>${money(item.output_per_million)}</td>
                <td>${esc(high)}</td>
                <td>${esc(item.role || '—')}</td>
            </tr>`;
        }).join('');
    }

    function renderOllama(catalog) {
        $('mc-ollama').innerHTML = (catalog.ollama || []).map(item => {
            const cloud = String(item.key || '').endsWith(':cloud');
            const search = `${item.key} ${item.role || ''} ${item.notes || ''}`.toLowerCase();
            return `<tr data-model-row data-search="${esc(search)}">
                <td><strong>${esc(item.key)}</strong>${item.notes ? `<small>${esc(item.notes)}</small>` : ''}</td>
                <td>${cloud ? 'plan / usage' : '$0 provider API'}</td>
                <td>${number(item.context_window)}</td>
                <td>${esc(item.local_size || '—')}</td>
                <td>${esc(item.usage_tier || '—')}</td>
                <td>${esc(item.role || '—')}</td>
            </tr>`;
        }).join('');
    }

    function activeRates(item, input) {
        const high = item.long_context_cutoff && input > Number(item.long_context_cutoff);
        return {
            high,
            input: high && item.long_input_per_million != null ? Number(item.long_input_per_million) : Number(item.input_per_million || 0),
            cached: high && item.long_cached_input_per_million != null
                ? Number(item.long_cached_input_per_million)
                : Number(item.cached_input_per_million != null ? item.cached_input_per_million : item.input_per_million || 0),
            output: high && item.long_output_per_million != null ? Number(item.long_output_per_million) : Number(item.output_per_million || 0),
        };
    }

    function estimate() {
        const catalog = state.catalog;
        if (!catalog) return;
        const key = $('mc-est-model').value;
        const item = (catalog.models || []).find(model => model.key === key);
        if (!item) { $('mc-est-cost').textContent = '—'; return; }
        const input = Math.max(0, Number($('mc-est-in').value || 0));
        const cached = Math.max(0, Math.min(input, Number($('mc-est-cache').value || 0)));
        const output = Math.max(0, Number($('mc-est-out').value || 0));
        const rates = activeRates(item, input);
        const cost = ((input - cached) * rates.input + cached * rates.cached + output * rates.output) / 1_000_000;
        $('mc-est-cost').textContent = `$${cost.toFixed(cost < 1 ? 4 : 2)}`;
        $('mc-est-detail').textContent = `${rates.high ? 'high-context tier · ' : ''}${money(rates.input)} input · ${money(rates.cached)} cached · ${money(rates.output)} output / 1M`;
    }

    function renderEstimator(catalog) {
        $('mc-est-model').innerHTML = (catalog.models || []).map(item => `<option value="${esc(item.key)}">${esc(item.provider)} · ${esc(item.key)}</option>`).join('');
        ['mc-est-model', 'mc-est-in', 'mc-est-cache', 'mc-est-out'].forEach(id => {
            $(id).addEventListener(id === 'mc-est-model' ? 'change' : 'input', estimate);
        });
        estimate();
    }

    function applySearch() {
        const needle = String($('mc-search').value || '').trim().toLowerCase();
        document.querySelectorAll('[data-model-row]').forEach(row => { row.hidden = !rowSearch(row, needle); });
    }

    function toggleTheme() {
        const current = document.documentElement.getAttribute('data-theme') || 'dark';
        const next = current === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', next);
        try { localStorage.setItem('mucli-theme', next); } catch (_) {}
        $('mc-theme').textContent = next === 'dark' ? '☾' : '☼';
    }

    async function init() {
        $('mc-search').addEventListener('input', applySearch);
        $('mc-theme').addEventListener('click', toggleTheme);
        try {
            const response = await fetch('/api/providers/pricing');
            const data = await response.json().catch(() => ({}));
            if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
            state.catalog = data;
            $('mc-version').textContent = `· baseline ${data.version}`;
            renderNotes(data); renderTokenModels(data); renderOllama(data); renderEstimator(data);
            $('mc-loading').hidden = true;
            $('mc-main').hidden = false;
        } catch (error) {
            $('mc-loading').hidden = true;
            $('mc-error').hidden = false;
            $('mc-error').textContent = `Could not load model pricing: ${error.message}`;
        }
    }

    void init();
})();
