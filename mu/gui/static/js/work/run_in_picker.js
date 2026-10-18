/* Jobs mode — the shared "Run in" picker (documentation/plans/job_templates.md §3.1).
 *
 * One component for every web surface that starts jobs: the New job sheet (work.js) and the
 * Jira import dialog (work.js chooseImportTarget). Options, grouped:
 *   ○ Default runtime        what the workspace resolves to without a choice (repo config,
 *                            session, Settings → Toolchains, or the shared runtime image)
 *   ◇ <template>             description · setup summary · size · GPU, plus fit warnings
 *                            ("◆ can't reach registry.npmjs.org" with Allow → PATCH egress)
 *   ⬢ <environment>          container-mode environments (snapshotted at start)
 *   Advanced…                the existing multi-environment editor (MuJobEnvironments.editor)
 *
 * Preselection: the repository's remembered template (Settings → Toolchains row) → the
 * session's environment → the last template used for this workspace on this device
 * (localStorage) → Default runtime.
 *
 * Data: GET /api/jobs/environments?workspace=&session= (templates with size, gpus, job_setup,
 * egress_allow, fit {missing_egress, toolchains}; top-level remembered). Allow: PATCH
 * /api/containers/templates/{name} {egress_allow}.
 *
 * Payload (``picker.payload()``, merged into ``execution``):
 *   template      → { template: "<name>", remember_template?: true }
 *   environment   → { environments: ["environment:<container>"] }
 *   advanced      → { environments: [bindings…] }
 *   default       → {}
 */
(() => {
    'use strict';
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const LAST_KEY = 'mucli.work.runIn.last';
    const GLYPH = {default:'○', template:'◇', environment:'⬢', advanced:'⬡'};
    let seq = 0;

    function isRepoUrl(text) { return /^(https?:\/\/|ssh:\/\/|git@)/i.test(String(text || '').trim()); }

    /* ``npm --prefix a ci`` → ``npm ci``; ``cargo fetch --manifest-path x`` → ``cargo fetch``. */
    function commandKey(command) {
        const tokens = String(command || '').trim().split(/\s+/).filter(Boolean);
        if (!tokens.length) return '';
        let skip = false;
        for (const token of tokens.slice(1)) {
            if (skip) { skip = false; continue; }
            if (token.startsWith('-')) { skip = !token.includes('='); continue; }
            if (/^[\w:@+-]+$/.test(token) && !token.includes('/')) return `${tokens[0]} ${token}`;
        }
        return tokens[0];
    }
    /* ``setup: npm ci ×3`` / ``setup: npm ci ×2, cargo fetch``. */
    function setupSummary(commands) {
        const list = (commands || []).filter(c => String(c || '').trim());
        if (!list.length) return '';
        const counts = new Map();
        list.forEach(c => { const k = commandKey(c); counts.set(k, (counts.get(k) || 0) + 1); });
        return 'setup: ' + [...counts].map(([k, n]) => n > 1 ? `${k} ×${n}` : k).join(', ');
    }
    function templateMeta(t) {
        return [t.description, setupSummary(t.job_setup), t.size, t.gpus ? 'GPU' : ''].filter(Boolean).join(' · ');
    }

    function lastChoices() { try { return JSON.parse(localStorage.getItem(LAST_KEY) || '{}') || {}; } catch (_) { return {}; } }
    function rememberLast(workspace, template) {
        if (!workspace) return;
        try {
            const data = lastChoices();
            if (template) data[workspace] = template; else delete data[workspace];
            localStorage.setItem(LAST_KEY, JSON.stringify(data));
        } catch (_) { /* private mode: no device memory */ }
    }

    /* The option a fresh picker selects (§3.1 preselection order). */
    function preselect(info, workspace) {
        const templates = info?.templates || [];
        const has = name => templates.some(t => t.name === name);
        const remembered = String(info?.remembered?.source || '');
        if (remembered.startsWith('template:') && has(remembered.slice(9))) return {kind:'template', template:remembered.slice(9), remembered:true};
        const resolved = info?.resolved || {};
        if (resolved.source === 'session' && (resolved.bindings || [])[0]) {
            const ref = resolved.bindings[0].ref;
            const env = (info?.environments || []).find(e => e.container === ref || e.name === ref);
            if (env) return {kind:'environment', spec:env.spec, name:env.name};
        }
        const last = lastChoices()[workspace];
        if (last && has(last)) return {kind:'template', template:last};
        return {kind:'default'};
    }

    function create(host, {api, ui, toast = () => {}, idPrefix = `run-in-${++seq}`, onChange = () => {}} = {}) {
        let info = null, choice = {kind:'default'}, ctx = {workspace:'', session:''}, request = 0, open = false, error = '';
        let advancedBindings = null;
        const listId = `${idPrefix}-list`;
        host.classList.add('work-runin');
        host.dataset.runIn = '';

        const template = name => (info?.templates || []).find(t => t.name === name);
        const isLocal = () => ctx.workspace && !isRepoUrl(ctx.workspace);
        function defaultMeta() {
            const r = info?.resolved;
            if (!ctx.workspace) return 'Choose a workspace first';
            if (isRepoUrl(ctx.workspace)) return 'Resolved from the repository after it is cloned';
            if (!r) return info ? 'Python 3.12 + Node 22 · no project tools' : 'Resolving…';
            if (r.error) return `misconfigured: ${r.error}`;
            if ((r.bindings || []).length) return `${window.MuJobEnvironments?.SOURCE_LABEL?.[r.source] || r.source}: ${r.summary || ''}`;
            return 'Python 3.12 + Node 22 · no project tools';
        }
        function label() {
            if (choice.kind === 'template') return {glyph:GLYPH.template, name:choice.template, meta:templateMeta(template(choice.template) || {})};
            if (choice.kind === 'environment') {
                const env = (info?.environments || []).find(e => e.spec === choice.spec);
                return {glyph:GLYPH.environment, name:choice.name || choice.spec, meta:env ? `environment · ${env.status || ''}` : 'environment'};
            }
            if (choice.kind === 'advanced') return {glyph:GLYPH.advanced, name:'Advanced', meta:window.MuJobEnvironments?.summary(advancedBindings || []) || ''};
            return {glyph:GLYPH.default, name:'Default runtime', meta:defaultMeta()};
        }
        function fitWarning(t) {
            const missing = t?.fit?.missing_egress || [];
            return missing.length ? `can't reach ${missing.join(', ')}` : '';
        }
        function option(kind, value, glyph, name, meta, selected, extra = '') {
            return `<div class="work-runin__opt" data-run-in-row><button type="button" class="w-menu__item work-runin__item" role="option" aria-selected="${selected}" data-run-in-option="${esc(kind)}" data-value="${esc(value)}">
                <span class="work-runin__glyph" aria-hidden="true">${glyph}</span><span class="w-menu__label work-runin__label"><span class="work-runin__name">${esc(name)}</span><span class="work-runin__meta">${esc(meta)}</span></span></button>${extra}</div>`;
        }
        function draw() {
            const l = label();
            const t = choice.kind === 'template' ? template(choice.template) : null;
            const warn = t ? fitWarning(t) : '';
            const templates = info?.templates || [];
            const envs = info?.environments || [];
            const rows = [
                option('default', '', GLYPH.default, 'Default runtime', defaultMeta(), choice.kind === 'default'),
                templates.length ? '<div class="w-menu__heading" role="presentation">Templates</div>' : '',
                ...templates.map(x => {
                    const w = fitWarning(x);
                    return option('template', x.name, GLYPH.template, x.name, templateMeta(x), choice.kind === 'template' && choice.template === x.name,
                        w ? `<span class="work-runin__warn" data-run-in-fit-row="${esc(x.name)}"><span class="work-runin__warn-glyph" aria-hidden="true">◆</span><span class="work-runin__warn-text">${esc(w)}</span><button type="button" class="w-btn" data-variant="tonal" data-tone="attention" data-size="sm" data-run-in-allow="${esc(x.name)}">Allow</button></span>` : '');
                }),
                envs.length ? '<div class="w-menu__heading" role="presentation">Environments (snapshotted at start)</div>' : '',
                ...envs.map(e => option('environment', e.spec, GLYPH.environment, e.name, `${e.status || ''}${e.gpus ? ` · GPU ${e.gpus}` : ''}`,
                    choice.kind === 'environment' && choice.spec === e.spec)),
                '<hr class="w-menu__separator" role="presentation">',
                `<button type="button" class="w-menu__item work-runin__advanced" data-run-in-advanced><span class="work-runin__glyph" aria-hidden="true">${GLYPH.advanced}</span><span class="w-menu__label work-runin__label"><span class="work-runin__name">Advanced…</span><span class="work-runin__meta">several environments, paths, Docker, GPUs</span></span></button>`,
            ].join('');
            const remember = choice.kind === 'template' && isLocal();
            host.innerHTML = `<div class="work-runin__row">
                    <span class="w-select work-runin__select"><button type="button" class="w-select__control work-runin__trigger" id="${esc(idPrefix)}" data-run-in-trigger aria-haspopup="listbox" aria-expanded="${open}" aria-controls="${esc(listId)}">
                        <span class="work-runin__glyph" aria-hidden="true">${l.glyph}</span><span class="work-runin__name">${esc(l.name)}</span><span class="work-runin__meta">${esc(l.meta)}</span></button></span>
                    <label class="w-check work-runin__remember"${remember ? '' : ' hidden'}><input class="w-check__input" type="checkbox" data-run-in-remember ${choice.remember ? 'checked' : ''}><span>Remember for this workspace</span></label>
                </div>
                <div class="w-menu work-runin__menu" role="listbox" id="${esc(listId)}" aria-label="Run in"${open ? '' : ' hidden'}>${rows}</div>
                ${warn ? `<div class="w-banner work-banner-sm work-runin__fit" data-tone="attention" role="status" data-run-in-fit><span class="w-banner__glyph" aria-hidden="true">◆</span><p class="w-banner__msg">${esc(choice.template)} ${esc(warn)}: dependency installs will fail.</p><button type="button" class="w-btn" data-variant="tonal" data-tone="attention" data-size="sm" data-run-in-allow="${esc(choice.template)}">Allow</button></div>` : ''}
                ${error ? `<p class="w-field__error" role="alert">${esc(error)}</p>` : ''}`;
            const trigger = host.querySelector('[data-run-in-trigger]');
            trigger.onclick = () => { open = !open; draw(); if (open) host.querySelector('[role="option"][aria-selected="true"]')?.focus(); };
            host.querySelectorAll('[data-run-in-option]').forEach(b => { b.onclick = () => {
                const kind = b.dataset.runInOption, value = b.dataset.value;
                if (kind === 'template') choice = {kind, template:value, remember:false};
                else if (kind === 'environment') choice = {kind, spec:value, name:(info?.environments || []).find(e => e.spec === value)?.name || value};
                else choice = {kind:'default'};
                if (choice.kind === 'template') choice.remember = String(info?.remembered?.source || '') === `template:${value}`;
                open = false; draw(); host.querySelector('[data-run-in-trigger]')?.focus(); onChange(value);
            }; });
            host.querySelectorAll('[data-run-in-allow]').forEach(b => { b.onclick = e => { e.stopPropagation(); void allow(b.dataset.runInAllow); }; });
            host.querySelector('[data-run-in-advanced]')?.addEventListener('click', () => { open = false; void advanced(); });
            host.querySelector('[data-run-in-remember]')?.addEventListener('change', e => { choice.remember = e.target.checked; });
            host.querySelector('.work-runin__menu')?.addEventListener('keydown', e => {
                const items = [...host.querySelectorAll('.work-runin__menu .w-menu__item')];
                const i = items.indexOf(document.activeElement);
                if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); items[(i + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length]?.focus(); }
                if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); open = false; draw(); host.querySelector('[data-run-in-trigger]')?.focus(); }
            });
        }
        async function allow(name) {
            const t = template(name);
            const missing = t?.fit?.missing_egress || [];
            if (!t || !missing.length) return;
            if (!await ui.confirm(`Add ${missing.join(', ')} to template ${name}'s network allowlist? Jobs started from it after this can reach ${missing.length === 1 ? 'that registry' : 'those registries'}; running jobs keep their snapshot.`,
                {title:'Allow registry for template', okLabel:'Allow'})) return;
            try {
                await api(`/api/containers/templates/${encodeURIComponent(name)}`, {method:'PATCH', body:{egress_allow:[...(t.egress_allow || []), ...missing]}});
                toast(`${name} can now reach ${missing.join(', ')}`, 'ok');
                await refresh(ctx, {keep:true});
            } catch (err) { error = err.message; draw(); }
        }
        async function advanced() {
            const E = window.MuJobEnvironments;
            if (!E) return;
            const initial = choice.kind === 'advanced' ? advancedBindings : (info?.resolved?.bindings || []);
            const result = await E.editor({api, ui, title:'Build environments for the new job', initial:initial || [],
                context:{repository:ctx.workspace, session:ctx.session}, clearLabel:'Use default'});
            if (result === null) { draw(); return; }
            if (result.length) { advancedBindings = result; choice = {kind:'advanced'}; } else { advancedBindings = null; choice = {kind:'default'}; }
            draw(); onChange('');
        }
        async function refresh(next = {}, {keep = false} = {}) {
            const changed = next.workspace !== undefined && next.workspace !== ctx.workspace;
            ctx = {...ctx, ...next};
            error = '';
            if (!ctx.workspace || isRepoUrl(ctx.workspace)) {
                info = info && !changed ? info : null;
                if (changed && choice.kind !== 'advanced') choice = {kind:'default'};
                draw();
                if (!ctx.workspace) return;
            }
            const mine = ++request;
            try {
                const q = new URLSearchParams({workspace:isRepoUrl(ctx.workspace) ? '' : ctx.workspace, session:ctx.session || '', images:'false'});
                const data = await api(`/api/jobs/environments?${q}`);
                if (mine !== request) return;
                info = data;
                if (!keep && (changed || choice.kind === 'default') && choice.kind !== 'advanced') choice = preselect(info, ctx.workspace);
                if (choice.kind === 'template' && !template(choice.template)) choice = {kind:'default'};
                draw();
            } catch (err) { if (mine === request) { error = err.message; draw(); } }
        }
        function payload() {
            if (choice.kind === 'template') return {template:choice.template, ...(choice.remember && isLocal() ? {remember_template:true} : {})};
            if (choice.kind === 'environment') return {environments:[choice.spec]};
            if (choice.kind === 'advanced' && advancedBindings?.length) return {environments:advancedBindings.map(({label: _l, ...b}) => b)};
            return {};
        }
        function commit() { if (isLocal()) rememberLast(ctx.workspace, choice.kind === 'template' ? choice.template : ''); }
        function reset() { choice = {kind:'default'}; advancedBindings = null; open = false; info = null; ctx = {workspace:'', session:''}; draw(); }
        draw();
        function setDefault() { choice = {kind:'default'}; advancedBindings = null; draw(); onChange(''); }
        return {refresh, payload, commit, reset, advanced, setDefault, value:() => ({...choice, bindings:advancedBindings}),
            templates:() => info?.templates || [], open:() => { open = true; draw(); }, info:() => info};
    }

    window.MuRunInPicker = {create, preselect, setupSummary, commandKey, LAST_KEY};
})();
