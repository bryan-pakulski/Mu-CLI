/* Jobs mode Settings → run rules. One planos-style panel per section of the server's rule
 * schema (GET /api/jobs/settings → rules.{schema,values,overrides}); every field renders
 * from its definition. Saves send only changed paths as a nested partial update; null resets
 * a rule to its default. Validation errors come back from the server (HTTP 400). */
(() => {
    'use strict';
    const host = document.getElementById('work-run-rules');
    if (!host) return;
    const ui = window.MuJobUI;
    const esc = ui.esc;
    const toast = (text, tone) => ui.toast(text, tone);
    let rules = null;
    const dirty = new Map(); // path -> value (null = reset)

    async function api(path, options = {}) {
        const init = { ...options, headers: { 'Content-Type': 'application/json' } };
        if (init.body && typeof init.body !== 'string') init.body = JSON.stringify(init.body);
        const response = await fetch(path, init);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : `${response.status} ${response.statusText}`);
        return data;
    }

    const fmt = (field, value) => {
        if (value === null || value === undefined || value === '') return field.kind === 'select' ? (field.options.find(o => o[0] === '')?.[1] || 'none') : 'none';
        if (field.kind === 'bool') return value ? 'on' : 'off';
        if (field.kind === 'int' || field.kind === 'float') return `${Number(value).toLocaleString()}${field.unit ? ` ${field.unit === 'USD' ? 'USD' : field.unit}` : ''}`;
        if (field.kind === 'select') return field.options.find(o => o[0] === value)?.[1] || value;
        if (field.kind === 'list') return value.length ? value.join(', ') : 'none';
        if (field.kind === 'suites') return value.length ? `${value.length} suite${value.length === 1 ? '' : 's'}` : 'none';
        if (field.kind === 'rows') return value.length ? `${value.length} row${value.length === 1 ? '' : 's'}` : 'none';
        return String(value);
    };
    const current = path => (dirty.has(path) ? (dirty.get(path) ?? FIELD[path].default) : rules.values[path]);
    let FIELD = {};

    function control(field, id) {
        const value = current(field.path);
        const attrs = `id="${id}" data-rule="${esc(field.path)}" aria-describedby="${id}-hint"`;
        switch (field.kind) {
            case 'bool':
                return `<button type="button" class="work-switch" role="switch" aria-checked="${Boolean(value)}" ${attrs} aria-label="${esc(field.label)}"></button>`;
            case 'int': case 'float':
                return `<span class="work-toolbar"><input class="w-input work-rule-number" type="number" ${attrs} value="${value ?? ''}" step="${field.kind === 'int' ? 1 : 'any'}"${field.min != null ? ` min="${field.min}"` : ''}${field.max != null ? ` max="${field.max}"` : ''} placeholder="${field.optional ? 'default' : ''}">${field.unit ? `<span class="work-meta">${esc(field.unit)}</span>` : ''}</span>`;
            case 'select':
                return `<span class="w-select"><select class="w-select__control" ${attrs}>${field.options.map(([v, l]) => `<option value="${esc(v)}" ${v === (value ?? '') ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select></span>`;
            case 'list':
                return `<textarea class="w-textarea" data-mono ${attrs} rows="3" placeholder="one per line">${esc((value || []).join('\n'))}</textarea>`;
            case 'suites':
                return `<div class="work-stack-tight" ${attrs.replace(`id="${id}"`, `id="${id}" role="group" aria-label="${esc(field.label)}"`)}>
                    ${(value || []).map((s, i) => `<div class="work-toolbar work-rule-suite" data-index="${i}">
                        <input class="w-input work-rule-suite__name" type="text" data-suite-key="name" aria-label="Suite name" placeholder="name" value="${esc(s.name || '')}">
                        <input class="w-input work-grow" data-mono type="text" data-suite-key="command" aria-label="Suite command" placeholder="command" value="${esc(s.command || '')}">
                        <input class="w-input work-rule-suite__junit" type="text" data-suite-key="junit" aria-label="JUnit report path (optional)" placeholder="junit path (optional)" value="${esc(s.junit || '')}">
                        <button type="button" class="w-btn" data-variant="icon" data-size="sm" data-tone="danger" data-suite-remove aria-label="Remove suite" title="Remove suite"><svg class="w-icon" aria-hidden="true" focusable="false"><use href="/static/icons/wash.svg#i-x"></use></svg></button></div>`).join('')}
                    <button type="button" class="w-btn work-rule-add" data-variant="secondary" data-size="sm" data-suite-add><svg class="w-icon" aria-hidden="true" focusable="false"><use href="/static/icons/wash.svg#i-plus"></use></svg><span>Add suite</span></button></div>`;
            case 'rows':
                return `<div class="work-stack-tight" ${attrs.replace(`id="${id}"`, `id="${id}" role="group" aria-label="${esc(field.label)}"`)}>
                    ${(value || []).map((row, i) => `<div class="work-toolbar work-rule-suite" data-index="${i}">
                        ${field.columns.map(c => `<input class="w-input work-grow" type="text" data-row-key="${esc(c.key)}" aria-label="${esc(c.label)}" placeholder="${esc(c.placeholder || c.label)}" value="${esc(row[c.key] || '')}">`).join('')}
                        <button type="button" class="w-btn" data-variant="icon" data-size="sm" data-tone="danger" data-row-remove aria-label="Remove row" title="Remove row"><svg class="w-icon" aria-hidden="true" focusable="false"><use href="/static/icons/wash.svg#i-x"></use></svg></button></div>`).join('')}
                    <button type="button" class="w-btn work-rule-add" data-variant="secondary" data-size="sm" data-row-add><svg class="w-icon" aria-hidden="true" focusable="false"><use href="/static/icons/wash.svg#i-plus"></use></svg><span>Add</span></button>
                    ${field.path === 'sandbox.toolchains' ? '<div class="work-stack-tight" data-toolchain-status></div>' : ''}</div>`;
            default:
                return `<input class="w-input" type="text" ${attrs} value="${esc(value ?? '')}">`;
        }
    }

    function render(errorSection = '', errorText = '') {
        FIELD = Object.fromEntries(rules.schema.flatMap(s => s.fields).map(f => [f.path, f]));
        host.innerHTML = rules.schema.map(section => {
            const changed = section.fields.some(f => dirty.has(f.path));
            return `<section class="w-card work-card" aria-labelledby="rules-${esc(section.id)}"><header class="w-card__head"><span class="w-eyebrow">Run rules</span><h2 class="w-card__title" id="rules-${esc(section.id)}">${esc(section.title)}</h2>${changed ? '<span class="w-chip" data-tone="attention"><span class="w-chip__glyph" aria-hidden="true">◆</span><span>Unsaved</span></span>' : ''}</header>
                <form class="w-card__body work-form" data-section="${esc(section.id)}" novalidate>
                    ${section.hint ? `<p class="work-jira-muted">${esc(section.hint)}</p>` : ''}
                    ${section.id === 'build_environments' ? '<div class="work-stack-tight work-env-status" data-env-status></div>' : ''}
                    ${section.id === 'harness' ? '<div class="work-stack-tight work-agents-status" data-agents-status></div>' : ''}
                    ${section.id === 'publish' ? '<div class="work-stack-tight work-publish-tokens" data-publish-tokens></div>' : ''}
                    ${section.fields.map((f, i) => {
                        const id = `rule-${section.id}-${i}`;
                        const overridden = Object.prototype.hasOwnProperty.call(rules.overrides, f.path) && dirty.get(f.path) !== null;
                        const meta = overridden || (dirty.has(f.path) && dirty.get(f.path) !== null)
                            ? `<span class="work-meta"><span class="work-accent">set</span> · default ${esc(fmt(f, f.default))} · <button type="button" class="work-link-btn" data-reset="${esc(f.path)}">Reset</button></span>`
                            : `<span class="work-meta">default</span>`;
                        return `<div class="work-field work-rule${errorSection === section.id && errorText.includes(f.label) ? ' has-error' : ''}">
                            <span><label class="w-field__label" for="${id}">${esc(f.label)}</label>${f.hint ? `<small class="w-field__hint" id="${id}-hint">${esc(f.hint)}</small>` : `<small id="${id}-hint"></small>`}</span>
                            <div class="work-stack-tight">${control(f, id)}${meta}</div></div>`;
                    }).join('')}
                    ${errorSection === section.id ? `<div class="w-banner" data-tone="danger" role="alert"><span class="w-banner__glyph" aria-hidden="true">✗</span><p class="w-banner__msg">${esc(errorText)}</p></div>` : ''}
                    <footer class="work-form-actions"><button type="button" class="w-btn" data-variant="secondary" data-discard ${changed ? '' : 'disabled'}>Discard</button><button type="submit" class="w-btn" data-variant="primary" ${changed ? '' : 'disabled'}>Save ${esc(section.title.toLowerCase())}</button></footer>
                </form></section>`;
        }).join('');
        bind();
        void environmentStatus(host.querySelector('[data-env-status]'));
        void window.MuJobAgents?.card(host.querySelector('[data-agents-status]'), { api, toast });
        void publishTokens(host.querySelector('[data-publish-tokens]'));
    }

    // Push: PR/MR provider tokens (GET/PUT /api/jobs/publish/tokens). Values are never shown;
    // stored tokens live host-side (0600) and never reach a job container.
    const PROVIDERS = [['gitea', 'Gitea / Forgejo'], ['gitlab', 'GitLab'], ['github', 'GitHub'], ['bitbucket', 'Bitbucket']];
    async function publishTokens(target) {
        if (!target) return;
        let tokens;
        try { tokens = (await api('/api/jobs/publish/tokens')).tokens || {}; }
        catch (error) { target.innerHTML = `<div class="w-banner" data-tone="danger" role="alert"><span class="w-banner__glyph" aria-hidden="true">✗</span><p class="w-banner__msg">${esc(error.message)}</p></div>`; return; }
        const where = source => source === 'environment' ? 'set in the environment' : source === 'stored' ? 'stored' : 'not set';
        target.innerHTML = `<div class="work-stack-tight"><span class="w-field__label">PR / MR tokens</span>
            <small class="w-field__hint">Used when a job pushes and opens its pull or merge request. Environment variables win over stored tokens.</small>
            ${PROVIDERS.map(([id, name]) => {
                const t = tokens[id] || {};
                return `<div class="work-stack-tight work-caption" role="group" aria-label="${esc(name)} token" data-publish-token="${id}">
                    <label for="publish-token-${id}" class="work-publish-token__label">${esc(name)} <span class="work-muted">${esc(t.variable || '')} · ${where(t.source)}</span></label>
                    <div class="w-btn-row work-publish-token__row">
                        <input id="publish-token-${id}" type="password" autocomplete="off" class="w-input work-grow" placeholder="paste a ${esc(name)} access token">
                        <button type="button" class="w-btn" data-variant="secondary" data-size="sm" data-token-save>Save</button>
                        ${t.source === 'stored' ? '<button type="button" class="w-btn" data-variant="ghost" data-size="sm" data-token-clear>Clear</button>' : ''}
                    </div>
                </div>`;
            }).join('')}</div>`;
        target.querySelectorAll('[data-publish-token]').forEach(row => {
            const provider = row.dataset.publishToken, input = row.querySelector('input');
            const put = async value => {
                try {
                    await api('/api/jobs/publish/tokens', { method: 'PUT', body: { provider, value } });
                    toast(value ? 'Token saved' : 'Token cleared', 'ok');
                } catch (error) { toast(error.message, 'danger'); }
                void publishTokens(target);
            };
            const save = () => { const value = input.value.trim(); input.value = ''; if (value) void put(value); };
            row.querySelector('[data-token-save]').addEventListener('click', save);
            // Inside the section's <form>: Enter saves the token instead of submitting the section.
            input.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); save(); } });
            row.querySelector('[data-token-clear]')?.addEventListener('click', () => void put(''));
        });
    }

    // Build environments: Docker-in-Docker (Sysbox) status + install steps, GPUs, kept jobs.
    let envStatus = null;
    async function environmentStatus(target, force = false) {
        if (!target) return;
        if (!envStatus || force) {
            try { envStatus = await api('/api/jobs/environments?images=false'); }
            catch (_) { target.innerHTML = ''; return; }
        }
        const fmtBytes = window.MuJobEnvironments?.bytes || (n => `${n} B`);
        const dind = envStatus.docker_in_docker || {}, gpu = envStatus.gpu || {}, kept = envStatus.kept_jobs || [];
        target.innerHTML = `<div class="w-chip-row">
                <span class="w-chip" data-tone="${dind.available ? 'ok' : 'neutral'}"><span class="w-chip__glyph" aria-hidden="true">${dind.available ? '✓' : '○'}</span><span>Docker-in-Docker: ${dind.available ? `available (${esc(dind.engine || 'sysbox')})` : 'Sysbox not installed'}</span></span>
                <span class="w-chip" data-tone="${gpu.supported ? 'ok' : 'neutral'}" title="${esc(gpu.reason || '')}"><span class="w-chip__glyph" aria-hidden="true">${gpu.supported ? '✓' : '○'}</span><span>GPUs: ${gpu.supported ? esc(gpu.reason || 'available') : 'unavailable'}</span></span></div>
            ${dind.available ? '' : `<details class="work-details"><summary>Install Sysbox to enable Docker = yes bindings</summary><ol>${(dind.install || []).map(step => `<li>${esc(step)}</li>`).join('')}</ol></details>`}
            <div class="work-stack-tight"><span><strong>Kept jobs</strong> <span class="work-meta">(exempt from idle retention)</span></span>
                ${kept.length ? `<ul class="work-list">${kept.map(j => `<li class="work-toolbar"><a class="work-mono" href="#/job/${esc(j.id)}">#${esc(String(j.id).slice(0, 8))}</a><span class="work-grow work-ellipsis" title="${esc(j.bindings || '')}">${esc(j.title || '')}${j.bindings ? ` · <span class="work-meta">${esc(j.bindings)}</span>` : ''}</span><span class="work-num work-meta">${esc(fmtBytes(j.bytes) || '—')}</span><button type="button" class="w-btn" data-variant="secondary" data-size="sm" data-unkeep="${esc(j.id)}">Unkeep</button></li>`).join('')}</ul>`
                    : '<span class="work-meta">No kept jobs.</span>'}</div>`;
        target.querySelectorAll('[data-unkeep]').forEach(b => b.addEventListener('click', async () => {
            b.disabled = true;
            try { await api('/api/jobs/cmd', { method: 'POST', body: { type: 'keepEnvironments', job_id: b.dataset.unkeep, args: { keep: false } } }); toast('Unkept: its images follow idle retention again', 'ok'); }
            catch (error) { toast(error.message, 'danger'); }
            void environmentStatus(target, true);
        }));
    }

    function read(field, el) {
        if (field.kind === 'bool') return el.getAttribute('aria-checked') === 'true';
        if (field.kind === 'int' || field.kind === 'float') {
            if (el.value.trim() === '') return null;
            const n = Number(el.value);
            return Number.isFinite(n) ? n : el.value;
        }
        if (field.kind === 'list') return el.value.split(/[\n,]/).map(v => v.trim()).filter(Boolean);
        if (field.kind === 'suites') return [...el.querySelectorAll('.work-rule-suite')].map(row => {
            const item = {};
            row.querySelectorAll('[data-suite-key]').forEach(i => { if (i.value.trim()) item[i.dataset.suiteKey] = i.value.trim(); });
            return item;
        });
        if (field.kind === 'rows') return [...el.querySelectorAll('.work-rule-suite')].map(row => {
            const item = {};
            row.querySelectorAll('[data-row-key]').forEach(i => { if (i.value.trim()) item[i.dataset.rowKey] = i.value.trim(); });
            return item;
        });
        return el.value;
    }

    function mark(path, value) {
        const field = FIELD[path];
        const original = rules.values[path];
        const same = JSON.stringify(value ?? field.default) === JSON.stringify(original);
        if (same && !(value === null && Object.prototype.hasOwnProperty.call(rules.overrides, path))) dirty.delete(path);
        else dirty.set(path, value);
    }

    function bind() {
        host.querySelectorAll('[data-rule]').forEach(el => {
            const path = el.dataset.rule, field = FIELD[path];
            if (field.kind === 'bool') el.addEventListener('click', () => { el.setAttribute('aria-checked', String(el.getAttribute('aria-checked') !== 'true')); mark(path, read(field, el)); render(); host.querySelector(`[data-rule="${CSS.escape(path)}"]`)?.focus(); });
            else if (field.kind === 'suites') {
                el.addEventListener('input', () => { mark(path, read(field, el)); refreshChrome(el); });
                el.querySelector('[data-suite-add]').addEventListener('click', () => { mark(path, [...read(field, el), { name: '', command: '' }]); render(); });
                el.querySelectorAll('[data-suite-remove]').forEach(b => b.addEventListener('click', () => {
                    const items = read(field, el); items.splice(Number(b.closest('[data-index]').dataset.index), 1); mark(path, items); render();
                }));
            } else if (field.kind === 'rows') {
                el.addEventListener('input', () => { mark(path, read(field, el)); refreshChrome(el); });
                el.querySelector('[data-row-add]').addEventListener('click', () => { mark(path, [...read(field, el), {}]); render(); });
                el.querySelectorAll('[data-row-remove]').forEach(b => b.addEventListener('click', () => {
                    const items = read(field, el); items.splice(Number(b.closest('[data-index]').dataset.index), 1); mark(path, items); render();
                }));
                if (path === 'sandbox.toolchains') void toolchainStatus(el.querySelector('[data-toolchain-status]'));
            } else el.addEventListener(field.kind === 'select' ? 'change' : 'input', () => { mark(path, read(field, el)); refreshChrome(el); });
        });
        host.querySelectorAll('[data-reset]').forEach(b => b.addEventListener('click', () => { dirty.set(b.dataset.reset, null); render(); }));
        host.querySelectorAll('form[data-section]').forEach(form => {
            form.querySelector('[data-discard]').addEventListener('click', () => {
                rules.schema.find(s => s.id === form.dataset.section).fields.forEach(f => dirty.delete(f.path)); render();
            });
            form.addEventListener('submit', event => { event.preventDefault(); void save(form.dataset.section); });
        });
    }

    function refreshChrome(el) {
        // Keep focus while typing: only toggle the section's save state, don't re-render it.
        const form = el.closest('form[data-section]');
        const changed = rules.schema.find(s => s.id === form.dataset.section).fields.some(f => dirty.has(f.path));
        form.querySelectorAll('[data-discard], [type=submit]').forEach(b => { b.disabled = !changed; });
    }

    async function save(sectionId) {
        const section = rules.schema.find(s => s.id === sectionId);
        const payload = {};
        for (const field of section.fields) {
            if (!dirty.has(field.path)) continue;
            const parts = field.path.split('.');
            let node = payload;
            parts.slice(0, -1).forEach(p => { node = node[p] = node[p] || {}; });
            node[parts[parts.length - 1]] = dirty.get(field.path);
        }
        try {
            const data = await api('/api/jobs/settings', { method: 'PUT', body: payload });
            section.fields.forEach(f => dirty.delete(f.path));
            rules = data.rules;
            render();
            toast(`${section.title} saved`, 'ok');
        } catch (error) {
            render(sectionId, error.message);
            toast(error.message, 'danger');
        }
    }

    // Derived toolchain images: status per saved row + Build (planos Setup's "build image" step).
    let toolchainTimer = 0;
    async function toolchainStatus(target) {
        if (!target) return;
        clearTimeout(toolchainTimer);
        let list = [];
        try { list = (await api('/api/jobs/toolchains')).toolchains || []; } catch (_) { target.innerHTML = ''; return; }
        if (!list.length) { target.innerHTML = '<span class="work-meta">Save a row to build its image.</span>'; return; }
        const tone = { built: 'ok', building: 'running', failed: 'danger', missing: 'neutral' };
        const glyph = { built: '✓', building: '●', failed: '✗', missing: '○' };
        target.innerHTML = list.map(t => `<div class="work-stack-tight"><div class="work-toolbar">
            <span class="w-chip" data-tone="${tone[t.status] || 'neutral'}">${t.status === 'building' ? '<span class="w-inkdrop" data-size="sm" aria-hidden="true"></span>' : `<span class="w-chip__glyph" aria-hidden="true">${glyph[t.status] || '○'}</span>`}<span>${esc(t.status === 'missing' ? 'not built' : t.status)}</span></span>
            <span class="work-mono work-ellipsis" title="${esc(t.repository)}">${esc(t.repository)}</span><span class="work-meta" aria-hidden="true">←</span><code>${esc(t.base_image)}</code>
            <span class="work-spacer"></span>${t.status === 'building' ? '' : `<button type="button" class="w-btn" data-variant="secondary" data-size="sm" data-build="${esc(t.base_image)}">${t.status === 'built' ? 'Rebuild' : 'Build'}</button>`}</div>
            ${t.error ? `<div class="work-del">${esc(t.error)}</div>` : ''}
            ${t.log_tail && t.status !== 'built' ? `<details class="work-details"><summary>Build log</summary><pre class="work-log">${esc(t.log_tail)}</pre></details>` : ''}</div>`).join('');
        target.querySelectorAll('[data-build]').forEach(b => b.addEventListener('click', async () => {
            b.disabled = true;
            try { await api('/api/jobs/toolchains/build', { method: 'POST', body: { base_image: b.dataset.build } }); toast('Building toolchain image…', 'running'); }
            catch (error) { toast(error.message, 'danger'); }
            void toolchainStatus(target);
        }));
        if (list.some(t => t.status === 'building')) toolchainTimer = setTimeout(() => toolchainStatus(target), 3000);
    }

    async function load() {
        try {
            const data = await api('/api/jobs/settings');
            if (!data.rules) { host.innerHTML = '<p class="work-empty-line">This server has no configurable run rules.</p>'; return; }
            rules = data.rules;
            if (!dirty.size) render();
        } catch (error) {
            host.innerHTML = `<div class="w-banner" data-tone="danger" role="alert"><span class="w-banner__glyph" aria-hidden="true">✗</span><p class="w-banner__msg">Could not load run rules: ${esc(error.message)}</p></div>`;
        }
    }

    window.addEventListener('mu:work-route', event => { if (event.detail?.name === 'settings') void load(); });
    window.addEventListener('beforeunload', event => { if (dirty.size) { event.preventDefault(); event.returnValue = ''; } });
    if (location.hash.startsWith('#/settings')) void load();
})();
