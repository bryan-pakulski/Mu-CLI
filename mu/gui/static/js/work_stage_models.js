/* Engineering Work — Job settings: per-stage model picker + "models unset" prompt.
 *
 * Stores defaults via PUT /api/jobs/settings ($MUCLI_HOME/jobs/settings.json).
 * Provider list: GET /api/providers; models: GET /api/providers/{name}/models.
 * If any stage has no model, a banner on the Work page asks the user to set them.
 */
(function () {
    'use strict';

    const form = document.getElementById('work-stage-models-form');
    const rowsEl = document.getElementById('work-stage-model-rows');
    if (!form || !rowsEl) return;

    const $ = id => document.getElementById(id);
    const warning = $('work-stage-models-warning');
    const message = $('work-stage-models-message');
    const approval = $('work-plan-approval');
    const settingsBtn = $('work-jira-settings-open');
    const STAGE_LABELS = { plan: 'Plan', implement: 'Implement', review: 'Review' };
    const STAGE_HINTS = {
        plan: 'Bigger model: reads the ticket, writes plan + acceptance criteria',
        implement: 'Smaller/faster model: makes the code changes',
        review: 'Bigger model: reviews the diff, can send it back',
    };

    let settings = null;
    let providers = [];
    const modelCache = {};

    async function api(path, init = {}) {
        init = { ...init };
        if (init.body && typeof init.body !== 'string') {
            init.headers = { ...(init.headers || {}), 'Content-Type': 'application/json' };
            init.body = JSON.stringify(init.body);
        }
        const response = await fetch(path, init);
        let data = {};
        try { data = await response.json(); } catch (_) { data = {}; }
        if (!response.ok) {
            const detail = data && data.detail !== undefined ? data.detail : response.statusText;
            throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
        }
        return data;
    }

    function say(el, text, kind = '') {
        if (!el) return;
        el.textContent = text || '';
        el.dataset.kind = kind;
        el.hidden = !text;
    }

    function esc(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g, ch => (
            { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
    }

    function modelName(m) {
        return typeof m === 'string' ? m : String((m && (m.name || m.id || m.model)) || '');
    }

    async function loadModels(provider) {
        if (!provider) return [];
        if (!modelCache[provider]) {
            modelCache[provider] = api(`/api/providers/${encodeURIComponent(provider)}/models`)
                .then(d => (d.models || []).map(modelName).filter(Boolean))
                .catch(() => []);
        }
        return modelCache[provider];
    }

    function stageRow(stage) {
        const current = ((settings && settings.stage_models) || {})[stage] || {};
        const provOpts = ['<option value="">— provider —</option>'].concat(providers.map(p => {
            const name = p.name || p;
            const note = p.configured === false ? ' (not configured)' : '';
            return `<option value="${esc(name)}"${name === current.provider ? ' selected' : ''}>${esc(name + note)}</option>`;
        })).join('');
        return `
            <fieldset class="work-stage-model-row" data-stage="${esc(stage)}">
                <legend>${esc(STAGE_LABELS[stage] || stage)} <small class="work-jira-muted">${esc(STAGE_HINTS[stage] || '')}</small></legend>
                <label class="work-field">
                    <span>Provider</span>
                    <select data-role="provider" aria-label="${esc(STAGE_LABELS[stage])} provider">${provOpts}</select>
                </label>
                <label class="work-field">
                    <span>Model</span>
                    <input data-role="model" type="text" list="work-stage-models-${esc(stage)}"
                        value="${esc(current.model || '')}" placeholder="Pick or type a model"
                        aria-label="${esc(STAGE_LABELS[stage])} model">
                    <datalist id="work-stage-models-${esc(stage)}"></datalist>
                </label>
            </fieldset>`;
    }

    async function fillModelList(row) {
        const provider = row.querySelector('[data-role="provider"]').value;
        const list = row.querySelector('datalist');
        const models = await loadModels(provider);
        list.innerHTML = models.map(m => `<option value="${esc(m)}"></option>`).join('');
    }

    function renderWarning() {
        const missing = (settings && settings.missing) || [];
        if (missing.length) {
            say(warning, `No model set for: ${missing.map(s => STAGE_LABELS[s] || s).join(', ')}. ` +
                'Jobs will stop and ask until each stage has a model.', 'error');
        } else {
            say(warning, '');
        }
        renderBanner();
    }

    function renderBanner() {
        let banner = $('work-stage-models-banner');
        const missing = (settings && settings.missing) || [];
        if (!missing.length) {
            if (banner) banner.remove();
            return;
        }
        if (!banner) {
            banner = document.createElement('div');
            banner.id = 'work-stage-models-banner';
            banner.className = 'work-attention-banner work-stage-models-banner';
            banner.setAttribute('role', 'alert');
            const host = document.querySelector('.work-layout') || document.body;
            host.parentNode.insertBefore(banner, host);
        }
        banner.innerHTML = `<strong>Set job models</strong> No model is set for the ` +
            `${esc(missing.map(s => STAGE_LABELS[s] || s).join(', '))} stage(s). ` +
            `<button type="button" class="work-primary" id="work-stage-models-banner-open">Choose models</button>`;
        $('work-stage-models-banner-open').addEventListener('click', () => openStageModels());
    }

    async function render() {
        rowsEl.innerHTML = ((settings && settings.stages) || ['plan', 'implement', 'review']).map(stageRow).join('');
        approval.checked = !settings || settings.plan_approval !== false;
        rowsEl.querySelectorAll('.work-stage-model-row').forEach(row => {
            const select = row.querySelector('[data-role="provider"]');
            select.addEventListener('change', () => {
                row.querySelector('[data-role="model"]').value = '';
                fillModelList(row);
            });
            fillModelList(row);
        });
        renderWarning();
    }

    async function load() {
        const [s, p] = await Promise.all([
            api('/api/jobs/settings'),
            providers.length ? Promise.resolve({ providers }) : api('/api/providers').catch(() => ({ providers: [] })),
        ]);
        settings = s;
        providers = p.providers || [];
        return settings;
    }

    function payload() {
        const stageModels = {};
        rowsEl.querySelectorAll('.work-stage-model-row').forEach(row => {
            const provider = row.querySelector('[data-role="provider"]').value.trim();
            const model = row.querySelector('[data-role="model"]').value.trim();
            stageModels[row.dataset.stage] = model ? { provider, model } : {};
        });
        return { stage_models: stageModels, plan_approval: approval.checked };
    }

    form.addEventListener('submit', async event => {
        event.preventDefault();
        try {
            settings = await api('/api/jobs/settings', { method: 'PUT', body: payload() });
            renderWarning();
            say(message, 'Saved.', 'ok');
        } catch (err) {
            say(message, err.message, 'error');
        }
    });

    async function openStageModels() {
        if (settingsBtn) settingsBtn.click();
        try { await load(); await render(); } catch (err) { say(message, err.message, 'error'); }
        const first = rowsEl.querySelector('select');
        if (first) first.focus();
    }
    window.muOpenStageModels = openStageModels;

    if (settingsBtn) {
        settingsBtn.addEventListener('click', async () => {
            say(message, '');
            try { await load(); await render(); } catch (err) { say(message, err.message, 'error'); }
        });
    }

    load().then(renderWarning).catch(() => {});
})();
