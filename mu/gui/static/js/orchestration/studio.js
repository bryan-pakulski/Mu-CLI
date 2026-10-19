/* Orchestration — Tool studio (documentation/plans/orchestration.md §4):
 * open drafts with the discovery stepper, new drafts, the discovery chat
 * (an orchestration session), the draft editor (manifest YAML + files) with
 * lint and test results beside the manifest's declared tests, the canary
 * test dialog and the submit-for-review dialog.
 */
(() => {
    'use strict';
    const api = window.OrchAPI;
    const ui = window.OrchUI;
    const { esc, icon } = ui;
    const Orch = (window.Orch = window.Orch || { routes: {} });
    Orch.routes = Orch.routes || {};

    const OPEN = ['draft', 'testing', 'in_review'];
    const EDITABLE = ['draft', 'testing'];
    const stages = () => Object.keys(window.WASH?.vocabulary?.orch_stage || { intent: 1, spec: 1, discover: 1, implement: 1, test: 1, review: 1, publish: 1 });
    const stageLabel = s => (window.WASH?.vocabulary?.orch_stage?.[s]?.label) || ui.cap(s);
    const editorHref = t => `#/studio/${encodeURIComponent(t.tool_id)}/${encodeURIComponent(t.version)}`;

    function stepper(t, { compact = false } = {}) {
        const all = stages();
        const cur = Math.max(0, all.indexOf(t.stage));
        const published = ['published', 'deprecated', 'retired'].includes(t.status);
        return `<ol class="w-stepper orch-stepper"${compact ? ' data-compact' : ''} aria-label="Discovery progress for ${esc(t.ref)}">${all.map((s, i) => {
            let st = i < cur || published ? 'done' : (i === cur ? 'active' : 'pending');
            if (!published && i === cur && t.status === 'in_review') st = 'waiting';
            const glyph = { done: '✓', active: '●', waiting: '◆', pending: '○' }[st];
            const current = !published && i === cur;
            return `<li class="w-stepper__step" data-status="${st}"${current ? ' aria-current="step"' : ''}><span aria-hidden="true">${glyph}</span>${compact && !current ? `<span class="w-sr-only">${esc(stageLabel(s))}</span>` : esc(stageLabel(s))}</li>`;
        }).join('')}</ol>`;
    }

    /* ------------------------------------------------------------ discovery chat */
    async function openChat(ref, button) {
        const body = { ref: ref || '' };
        const attempt = async extra => {
            const res = await ui.busy(button, () => api.post('studio/session', { ...body, ...extra }));
            if (res?.url) window.location.href = res.url;
        };
        try { await attempt({}); }
        catch (err) {
            if (err.status !== 400 || !/provider/i.test(err.message)) { ui.toast(`Couldn’t open the discovery chat: ${err.message}`, 'danger'); return; }
            chooseModel(attempt);
        }
    }

    /* No active chat session to borrow a model from: pick one from the providers this GUI can
     * reach (GET /api/providers, /api/providers/<name>/models). Free text stays possible when a
     * provider can't list its models. The last choice is remembered. */
    const LAST_MODEL = 'orch.chat.model';
    async function chooseModel(attempt) {
        const form = document.createElement('form');
        form.className = 'orch-stack';
        form.noValidate = true;
        const id = ui.uid('chat');
        form.innerHTML = `<p>No chat session is active, so choose the model for the discovery chat.</p>
            <div class="w-field"><label class="w-field__label" for="${id}-p">Provider</label><span class="w-select"><select class="w-select__control" id="${id}-p" data-autofocus><option value="">Loading…</option></select></span><p class="w-field__hint" data-p-hint></p></div>
            <div class="w-field"><label class="w-field__label" for="${id}-m">Model</label><span class="w-select" data-m-select><select class="w-select__control" id="${id}-m" disabled><option value="">Choose a provider first</option></select></span>
                <input class="w-input" data-mono id="${id}-mt" autocomplete="off" spellcheck="false" placeholder="model name" hidden aria-label="Model name">
                <p class="w-field__hint" data-m-hint aria-live="polite"></p></div>
            <p class="w-field__error" data-error role="alert"></p>
            <div class="w-btn-row orch-dialog__actions"><button type="button" class="w-btn" data-variant="secondary" data-cancel>Cancel</button><button type="submit" class="w-btn" data-variant="primary">Open chat</button></div>`;
        const d = ui.dialog({ title: 'Open the discovery chat', body: form });
        const psel = form.querySelector(`#${id}-p`);
        const msel = form.querySelector(`#${id}-m`);
        const mtext = form.querySelector(`#${id}-mt`);
        const mhint = form.querySelector('[data-m-hint]');
        let last = {};
        try { last = JSON.parse(localStorage.getItem(LAST_MODEL) || '{}'); } catch (_) { /* none */ }
        const useText = (on, value = '') => {
            form.querySelector('[data-m-select]').hidden = on;
            mtext.hidden = !on;
            if (on) { mtext.value = value; }
        };
        let seq = 0;
        async function loadModels() {
            const provider = psel.value;
            const mine = ++seq;
            useText(false);
            msel.disabled = true;
            msel.innerHTML = '<option value="">Loading models…</option>';
            mhint.textContent = '';
            if (!provider) return;
            let res;
            try { res = await api.get(`/api/providers/${encodeURIComponent(provider)}/models`); }
            catch (err) { res = { models: [], error: err.message }; }
            if (mine !== seq) return;
            const models = (res.models || []).map(String);
            if (!models.length) {
                useText(true, last.provider === provider ? last.model || '' : '');
                mhint.textContent = res.error ? `Couldn’t list ${provider}’s models (${res.error}); type the model name.` : `${provider} listed no models; type the model name.`;
                return;
            }
            msel.innerHTML = models.map(m => `<option value="${esc(m)}">${esc(m)}</option>`).join('') + '<option value="__other">Other…</option>';
            msel.disabled = false;
            if (last.provider === provider && last.model) {
                if (models.includes(last.model)) msel.value = last.model;
                else useText(true, last.model);
            }
        }
        msel.addEventListener('change', () => { if (msel.value === '__other') { useText(true); mtext.focus(); } });
        psel.addEventListener('change', loadModels);
        try {
            const { providers } = await api.get('/api/providers');
            const ordered = [...providers].sort((a, b) => Number(b.configured) - Number(a.configured));
            psel.innerHTML = ordered.map(p => `<option value="${esc(p.name)}"${p.configured ? '' : ' disabled'}>${esc(p.name)}${p.configured ? '' : ` (needs ${esc(p.requires)})`}</option>`).join('');
            const usable = ordered.filter(p => p.configured).map(p => p.name);
            psel.value = usable.includes(last.provider) ? last.provider : (usable[0] || '');
            form.querySelector('[data-p-hint]').textContent = usable.length ? '' : 'No provider is configured on this machine: set an API key (or run Ollama) and reload.';
        } catch (err) {
            psel.innerHTML = ['anthropic', 'openai', 'gemini', 'ollama'].map(p => `<option value="${p}">${p}</option>`).join('');
            psel.value = last.provider || 'anthropic';
        }
        loadModels();
        form.querySelector('[data-cancel]').onclick = () => d.close();
        form.onsubmit = async e => {
            e.preventDefault();
            const provider = psel.value;
            const model = (mtext.hidden ? msel.value : mtext.value).trim();
            const err = form.querySelector('[data-error]');
            if (!provider) { err.innerHTML = '<span aria-hidden="true">✗</span> Choose a provider.'; return; }
            if (!model || model === '__other') { err.innerHTML = '<span aria-hidden="true">✗</span> Choose or type a model.'; return; }
            try {
                await attempt({ provider, model });
                try { localStorage.setItem(LAST_MODEL, JSON.stringify({ provider, model })); } catch (_) { /* private mode */ }
                d.close();
            } catch (e2) { err.innerHTML = `<span aria-hidden="true">✗</span> ${esc(e2.message)}`; }
        };
    }

    /* ------------------------------------------------------------ new tool */
    function newTool() {
        const form = document.createElement('form');
        form.className = 'orch-stack';
        form.noValidate = true;
        const id = ui.uid('new');
        form.innerHTML = `
            <div class="w-segmented" role="group" aria-label="Start from">
                <button type="button" class="w-segmented__option" aria-pressed="true" data-mode="intent">Describe it</button>
                <button type="button" class="w-segmented__option" aria-pressed="false" data-mode="yaml">Paste a manifest</button>
            </div>
            <div data-pane="intent" class="orch-stack">
                <div class="w-field"><label class="w-field__label" for="${id}-id">Tool id</label><input class="w-input" data-mono id="${id}-id" placeholder="nginx.rotate-logs" autocomplete="off" data-autofocus>
                    <p class="w-field__hint">A dotted lowercase name. Versions are numbered for you.</p></div>
                <div class="w-field"><label class="w-field__label" for="${id}-intent">What should it do?</label><textarea class="w-textarea" id="${id}-intent" rows="4" placeholder="Rotate and compress nginx logs older than N days, then reopen them. Must never delete the current log."></textarea>
                    <p class="w-field__hint">Goal, targets, what “done” looks like and what must never happen. The AI asks follow-up questions in the chat.</p></div>
            </div>
            <div data-pane="yaml" hidden><div class="w-field"><label class="w-field__label" for="${id}-yaml">Manifest YAML</label><textarea class="w-textarea orch-editor" data-mono id="${id}-yaml" rows="14" spellcheck="false" placeholder="id: team.my-tool&#10;title: …&#10;effect: read_only&#10;…"></textarea></div></div>
            <label class="w-check"><input class="w-check__input" type="checkbox" id="${id}-chat" checked><span>Open the discovery chat afterwards</span></label>
            <p class="w-field__error" data-error role="alert"></p>
            <div class="w-btn-row orch-dialog__actions"><button type="button" class="w-btn" data-variant="secondary" data-cancel>Cancel</button><button type="submit" class="w-btn" data-variant="primary">Create draft</button></div>`;
        const d = ui.dialog({ title: 'New tool', body: form, size: 'md' });
        let mode = 'intent';
        form.querySelectorAll('[data-mode]').forEach(b => b.addEventListener('click', () => {
            mode = b.dataset.mode;
            form.querySelectorAll('[data-mode]').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
            form.querySelectorAll('[data-pane]').forEach(p => { p.hidden = p.dataset.pane !== mode; });
        }));
        form.querySelector('[data-cancel]').onclick = () => d.close();
        form.onsubmit = async e => {
            e.preventDefault();
            const err = form.querySelector('[data-error]');
            const args = mode === 'yaml' ? { manifest: form.querySelector(`#${id}-yaml`).value } : { tool_id: form.querySelector(`#${id}-id`).value.trim(), intent: form.querySelector(`#${id}-intent`).value.trim() };
            if (mode === 'intent' && !args.tool_id) { err.innerHTML = '<span aria-hidden="true">✗</span> Give the tool an id.'; return; }
            if (mode === 'yaml' && !args.manifest.trim()) { err.innerHTML = '<span aria-hidden="true">✗</span> Paste a manifest.'; return; }
            try {
                const res = await ui.busy(form.querySelector('[type=submit]'), () => api.cmd('createDraft', args));
                const ref = res.data.ref;
                const chat = form.querySelector(`#${id}-chat`).checked;
                d.close();
                ui.toast(`Draft ${ref} created`, 'ok');
                const [tid, v] = ref.split('@');
                Orch.go(`#/studio/${encodeURIComponent(tid)}/${encodeURIComponent(v)}`);
                if (chat) openChat(ref);
            } catch (e2) { err.innerHTML = `<span aria-hidden="true">✗</span> ${esc(e2.message)}`; }
        };
    }

    /* ------------------------------------------------------------ test dialog */
    async function testDialog(t) {
        const P = window.OrchPickers;
        const tests = (t.manifest || {}).tests || [];
        const id = ui.uid('test');
        const form = document.createElement('form');
        form.className = 'orch-stack';
        form.noValidate = true;
        form.innerHTML = `
            <p class="orch-muted">The test becomes a run on a canary node. It waits for your approval unless your access policy has a rule that allows such tests. Check mode reports what would change without changing anything.</p>
            <div data-node></div>
            <div class="w-field"><label class="w-field__label" for="${id}-case">Test</label><span class="w-select"><select class="w-select__control" id="${id}-case">
                ${tests.map(c => `<option value="${esc(c.name)}">${esc(c.name)} (declared)</option>`).join('')}<option value="">Ad-hoc parameters</option></select></span></div>
            <div data-adhoc class="orch-stack" ${tests.length ? 'hidden' : ''}>
                <fieldset class="orch-fieldset"><legend>Parameters</legend><div data-params></div></fieldset>
                <label class="w-check"><input class="w-check__input" type="checkbox" id="${id}-check" ${t.check_mode || t.effect === 'read_only' ? 'checked' : ''}><span>Check mode (dry run)</span></label>
            </div>
            <p class="w-field__error" data-error role="alert"></p>
            <div class="w-btn-row orch-dialog__actions"><button type="button" class="w-btn" data-variant="secondary" data-cancel>Cancel</button><button type="submit" class="w-btn" data-variant="primary">Propose test</button></div>`;
        const d = ui.dialog({ title: `Test ${t.ref}`, body: form, size: 'md' });
        const node = P.nodePicker(form.querySelector('[data-node]'), { test: true, label: 'Canary node' });
        const params = paramsFormFor(form.querySelector('[data-params]'), t.manifest?.inputs || {});
        const caseSel = form.querySelector(`#${id}-case`);
        caseSel.addEventListener('change', () => { form.querySelector('[data-adhoc]').hidden = !!caseSel.value; });
        form.querySelector('[data-cancel]').onclick = () => d.close();
        form.onsubmit = async e => {
            e.preventDefault();
            const err = form.querySelector('[data-error]');
            const nodeId = node.value();
            if (!nodeId) { err.innerHTML = '<span aria-hidden="true">✗</span> Mark a node canary or start a test node first.'; return; }
            const args = { ref: t.ref, node_id: nodeId, test_name: caseSel.value };
            if (!caseSel.value) {
                const read = params.read();
                if (read.errors.length) { err.innerHTML = `<span aria-hidden="true">✗</span> ${esc(read.errors.join('; '))}`; return; }
                args.params = read.values;
                args.check_mode = form.querySelector(`#${id}-check`).checked;
            }
            try {
                const res = await ui.busy(form.querySelector('[type=submit]'), () => api.cmd('proposeTest', args));
                d.close();
                const run = res.data?.run;
                ui.toast(run?.state === 'proposed' ? 'Test proposed. Approve it to run.' : 'Test started (allowed by your access policy).', 'ok');
                if (run?.id) Orch.go(`#/run/${encodeURIComponent(run.id)}`);
            } catch (e2) { err.innerHTML = `<span aria-hidden="true">✗</span> ${esc(e2.message)}`; }
        };
    }
    const paramsFormFor = (container, schema) => window.OrchPickers.paramsForm(container, schema);

    /* ------------------------------------------------------------ submit dialog */
    function submitDialog(t, done) {
        const id = ui.uid('submit');
        const form = document.createElement('form');
        form.className = 'orch-stack';
        form.noValidate = true;
        form.innerHTML = `
            <p class="orch-muted">You review it next: the manifest, the diff against the previous version, lint and test results. Nothing can run on the fleet until it is published.</p>
            <div class="w-field"><label class="w-field__label" for="${id}-h">Headline</label><input class="w-input" id="${id}-h" value="${esc(t.review?.headline || '')}" placeholder="Rotate nginx logs with configurable retention" autocomplete="off" data-autofocus></div>
            <div class="w-field"><label class="w-field__label" for="${id}-r">Risks <span class="orch-muted">(one per line)</span></label><textarea class="w-textarea" id="${id}-r" rows="4" placeholder="Runs as root&#10;Reopens nginx, which drops in-flight log lines">${esc((t.review?.risks || []).join('\n'))}</textarea></div>
            <div class="w-field"><label class="w-field__label" for="${id}-n">Notes <span class="orch-muted">(optional)</span></label><textarea class="w-textarea" id="${id}-n" rows="3"></textarea></div>
            ${!t.lint?.ok || !t.lint?.current ? ui.banner('caution', '◆', 'Lint hasn’t passed on the current draft. Publishing needs a passing lint.') : ''}
            ${!t.tests?.count ? ui.banner('caution', '◆', 'This draft hasn’t been tested on a canary yet.') : ''}
            <p class="w-field__error" data-error role="alert"></p>
            <div class="w-btn-row orch-dialog__actions"><button type="button" class="w-btn" data-variant="secondary" data-cancel>Cancel</button><button type="submit" class="w-btn" data-variant="primary">Submit for review</button></div>`;
        const d = ui.dialog({ title: `Submit ${t.ref} for review`, body: form, size: 'md' });
        form.querySelector('[data-cancel]').onclick = () => d.close();
        form.onsubmit = async e => {
            e.preventDefault();
            const headline = form.querySelector(`#${id}-h`).value.trim();
            if (!headline) { form.querySelector('[data-error]').innerHTML = '<span aria-hidden="true">✗</span> Add a one-line headline.'; return; }
            const risks = form.querySelector(`#${id}-r`).value.split('\n').map(s => s.trim()).filter(Boolean);
            try {
                await ui.busy(form.querySelector('[type=submit]'), () => api.cmd('submitReview', { ref: t.ref, headline, risks, notes: form.querySelector(`#${id}-n`).value.trim() }));
                d.close();
                ui.toast(`${t.ref} submitted for review`, 'ok');
                done?.();
            } catch (e2) { form.querySelector('[data-error]').innerHTML = `<span aria-hidden="true">✗</span> ${esc(e2.message)}`; }
        };
    }

    /* ------------------------------------------------------------ list */
    async function renderList(el, route, { isCurrent }) {
        const data = await api.get('tools?all=true');
        if (!isCurrent()) return null;
        const drafts = data.tools.filter(v => OPEN.includes(v.status)).sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));
        const rbDrafts = (data.runbooks || []).filter(r => ['draft', 'in_review'].includes(r.status)).sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));
        const rbHref = r => (r.status === 'in_review' ? Orch.catalog.runbookHref(r) : `#/studio/runbook/${encodeURIComponent(r.runbook_id)}/${encodeURIComponent(r.version)}`);
        el.innerHTML = `
            <header class="w-page__head orch-head">
                <div><h1 class="w-page__title">Tool studio</h1><p class="w-page__lede">Design tools with the AI: describe the goal, shape the spec, test on a canary, then review and publish.</p></div>
                <span class="orch-spacer"></span>
                <div class="w-btn-row"><button type="button" class="w-btn" data-variant="secondary" data-chat>${icon('message-circle')}<span>Open discovery chat</span></button>
                <a class="w-btn" data-variant="secondary" href="#/studio/runbook/new">${icon('plus')}<span>New runbook</span></a>
                <button type="button" class="w-btn" data-variant="primary" data-new>${icon('plus')}<span>New tool</span></button></div>
            </header>
            <div class="orch-stack">
                ${drafts.length ? `<ul class="orch-draft-list" role="list">${drafts.map(t => `
                    <li><article class="w-card orch-card orch-draft"${t.status === 'in_review' ? ' data-state="attention"' : ''}>
                        <header class="w-card__head">${ui.chip(t.style)}${ui.effectChip(t.effect_style, t.effect)}<h2 class="w-card__title"><a href="${editorHref(t)}">${esc(t.title)}</a></h2><span class="orch-spacer"></span><span class="w-card__meta orch-mono">${esc(t.ref)}</span></header>
                        <div class="w-card__body orch-stack-sm">
                            ${stepper(t)}
                            ${t.intent ? `<p class="orch-clamp">${esc(t.intent)}</p>` : ''}
                            <div class="orch-head__meta">${Orch.catalog.lintSummary(t)}${Orch.catalog.testsSummary(t)}<span class="orch-sub">by ${esc(t.created_by)} · updated ${ui.time(t.updated_at)}</span></div>
                        </div>
                        <footer class="w-card__foot"><button type="button" class="w-btn" data-variant="ghost" data-size="sm" data-chat-ref="${esc(t.ref)}">${icon('message-circle', 'sm')}<span>${t.session_name ? 'Continue chat' : 'Discovery chat'}</span></button><a class="w-btn" data-variant="${t.status === 'in_review' ? 'tonal' : 'secondary'}"${t.status === 'in_review' ? ' data-tone="attention"' : ''} data-size="sm" href="${t.status === 'in_review' ? Orch.catalog.toolHref(t) : editorHref(t)}">${t.status === 'in_review' ? 'Review' : 'Open editor'} <span class="w-btn__chevron" aria-hidden="true">›</span></a></footer>
                    </article></li>`).join('')}</ul>`
                : ui.empty('No drafts in progress', 'Start a tool and the AI walks you through intent, spec, discovery, implementation and tests.', { icon: 'sparkles', action: '<button type="button" class="w-btn" data-variant="primary" data-new>New tool</button>', size: '' })}
                <section class="orch-section" aria-labelledby="orch-studio-rb-h">
                    <header class="orch-section__head"><h2 class="orch-section__title" id="orch-studio-rb-h">Runbooks</h2>${rbDrafts.length ? `<span class="w-count">${rbDrafts.length}</span>` : ''}<span class="orch-spacer"></span><a class="w-btn" data-variant="ghost" data-size="sm" href="#/studio/runbook/new">${icon('plus', 'sm')}<span>New runbook</span></a></header>
                    ${rbDrafts.length ? `<ul class="orch-draft-list" role="list">${rbDrafts.map(r => {
                        const n = (r.problems || []).length;
                        return `<li><article class="w-card orch-card orch-draft"${r.status === 'in_review' ? ' data-state="attention"' : (n ? ' data-state="danger"' : '')}>
                        <header class="w-card__head">${ui.chip(r.style)}${ui.effectChip(r.effect_style, r.effect)}<h3 class="w-card__title"><a href="${rbHref(r)}">${esc(r.title)}</a></h3><span class="orch-spacer"></span><span class="w-card__meta orch-mono">${esc(r.ref)}</span></header>
                        <div class="w-card__body"><div class="orch-head__meta">${ui.meta(ui.plural((r.steps || []).length, 'step'))}${n ? ui.chip({ semantic: 'danger', glyph: '✗', label: ui.plural(n, 'problem') }, { title: r.problems.join('\n') }) : ui.chip({ semantic: 'ok', glyph: '✓', label: 'Valid' })}${r.parses === false ? ui.chip({ semantic: 'caution', glyph: '◆', label: 'YAML doesn’t parse' }) : ''}<span class="orch-sub">by ${esc(r.created_by || '')} · updated ${ui.time(r.updated_at)}</span></div></div>
                        <footer class="w-card__foot"><a class="w-btn" data-variant="${r.status === 'in_review' ? 'tonal' : 'secondary'}"${r.status === 'in_review' ? ' data-tone="attention"' : ''} data-size="sm" href="${rbHref(r)}">${r.status === 'in_review' ? 'Review' : 'Open editor'} <span class="w-btn__chevron" aria-hidden="true">›</span></a></footer>
                    </article></li>`;
                    }).join('')}</ul>` : '<p class="orch-muted orch-empty-line">No runbook drafts. Compose published tools into ordered steps with <code>when:</code> conditions.</p>'}
                </section>
                <p class="orch-muted orch-footnote">The AI can draft, lint, test on canaries and submit for review. Only you publish, approve runs or change policy.</p>
            </div>`;
        el.querySelectorAll('[data-new]').forEach(b => b.addEventListener('click', newTool));
        el.querySelector('[data-chat]').addEventListener('click', e => openChat('', e.currentTarget));
        el.querySelectorAll('[data-chat-ref]').forEach(b => b.addEventListener('click', () => openChat(b.dataset.chatRef, b)));
        return null;
    }

    /* ------------------------------------------------------------ editor */
    async function renderEditor(el, route, { isCurrent, setCrumbs }) {
        const [id, version] = route.parts;
        const t = await api.get(`tools/${encodeURIComponent(id)}/${encodeURIComponent(version)}`);
        if (!isCurrent()) return null;
        setCrumbs([{ label: 'Studio', to: '#/studio' }, { label: t.ref }]);
        const editable = EDITABLE.includes(t.status);
        const files = { ...(t.files || {}) };
        const removed = new Set();
        const changed = new Set();
        let manifestDirty = false;
        let activeFile = Object.keys(files)[0] || '';
        const dirty = () => manifestDirty || changed.size > 0 || removed.size > 0;

        el.innerHTML = `
            <header class="w-page__head orch-head">
                <div class="orch-head__title">
                    <span class="w-eyebrow" data-tone="accent"><i class="w-eyebrow__swatch"></i>Draft</span>
                    <h1 class="w-page__title">${esc(t.title)} <span class="orch-version-tag orch-mono">v${esc(t.version)}</span></h1>
                    <div class="orch-head__meta">${ui.chip(t.style)}${ui.effectChip(t.effect_style, t.effect)}<span class="orch-mono orch-sub">${esc(t.ref)}</span><span data-dirty hidden>${ui.chip({ semantic: 'caution', glyph: '◆', label: 'Unsaved changes' })}</span></div>
                </div>
            </header>
            <div class="orch-stack">
                ${stepper(t)}
                <div data-stale></div>
                ${!OPEN.includes(t.status) ? ui.banner('info', '●', `This version is ${esc(t.style.label)} and can’t be edited.`, `<a class="w-btn" data-variant="tonal" data-tone="info" data-size="sm" href="${Orch.catalog.toolHref(t)}">Open in catalog</a>`) : ''}
                ${t.status === 'in_review' ? ui.banner('attention', '◆', 'In review: editing is locked. Request changes from the catalog page to reopen it.', `<a class="w-btn" data-variant="tonal" data-tone="attention" data-size="sm" href="${Orch.catalog.toolHref(t)}">Review it</a>`) : ''}
                <div class="orch-toolbar">
                    ${editable ? `<button type="button" class="w-btn" data-variant="primary" data-save disabled>${icon('check')}<span>Save draft</span></button>` : ''}
                    ${ui.actionButtons(t.actions, { only: ['lintDraft', 'testDraft', 'submitReview', 'discardDraft'] })}
                    <button type="button" class="w-btn" data-variant="ghost" data-chat>${icon('message-circle')}<span>${t.session_name ? 'Continue discovery chat' : 'Open discovery chat'}</span></button>
                    <a class="w-btn" data-variant="ghost" href="${Orch.catalog.toolHref(t)}">Catalog view <span class="w-btn__chevron" aria-hidden="true">›</span></a>
                </div>
                ${t.intent ? `<p class="orch-lede"><span class="orch-label-text">Intent</span> ${esc(t.intent)}</p>` : ''}
                <div class="orch-studio">
                    <section class="orch-studio__edit orch-stack" aria-label="Draft editor">
                        <div class="w-field"><label class="w-field__label" for="orch-manifest">Manifest (tool.yaml)</label>
                            <textarea class="w-textarea orch-editor" data-mono id="orch-manifest" rows="22" spellcheck="false" ${editable ? '' : 'readonly'}>${esc(t.manifest_yaml)}</textarea></div>
                        <div class="orch-files">
                            <div class="orch-files__head"><h2 class="orch-section__title">Files</h2><span class="orch-spacer"></span>${editable ? `<button type="button" class="w-btn" data-variant="ghost" data-size="sm" data-add-file>${icon('plus', 'sm')}<span>Add file</span></button>` : ''}</div>
                            <div data-file-tabs></div>
                            <div data-file-body></div>
                        </div>
                    </section>
                    <aside class="orch-studio__side orch-stack" aria-label="Checks">
                        <section class="orch-section"><header class="orch-section__head"><h2 class="orch-section__title">Lint</h2><span class="orch-spacer"></span>${Orch.catalog.lintSummary(t)}</header>${Orch.catalog.lintTable(t.lint_result)}</section>
                        <section class="orch-section"><header class="orch-section__head"><h2 class="orch-section__title">Declared tests</h2></header>${Orch.catalog.expectationsTable(t.manifest)}</section>
                        <section class="orch-section"><header class="orch-section__head"><h2 class="orch-section__title">Test results</h2><span class="orch-spacer"></span>${Orch.catalog.testsSummary(t)}</header>${Orch.catalog.testsTable(t)}</section>
                        <section class="orch-section"><header class="orch-section__head"><h2 class="orch-section__title">Notes</h2></header>${Orch.catalog.notesTimeline((t.notes || []).slice(-8))}</section>
                    </aside>
                </div>
            </div>`;

        const markDirty = () => {
            el.querySelector('[data-dirty]').hidden = !dirty();
            const save = el.querySelector('[data-save]');
            if (save) save.disabled = !dirty();
        };
        const paintFiles = () => {
            const names = Object.keys(files).filter(n => !removed.has(n));
            if (activeFile && !names.includes(activeFile)) activeFile = names[0] || '';
            el.querySelector('[data-file-tabs]').innerHTML = names.length ? `<div class="w-tabs orch-tabs" role="tablist" aria-label="Files">${names.map(n => `<button type="button" class="w-tabs__tab" role="tab" aria-selected="${n === activeFile}" tabindex="${n === activeFile ? 0 : -1}" data-file="${esc(n)}">${esc(n)}${changed.has(n) ? ' <span aria-label="modified">•</span>' : ''}</button>`).join('')}</div>` : '<p class="orch-muted">No files yet. The entry script named in the manifest goes here.</p>';
            const body = el.querySelector('[data-file-body]');
            if (!activeFile) { body.innerHTML = ''; return; }
            body.innerHTML = `<div class="w-field"><label class="w-visually-hidden" for="orch-file-editor">${esc(activeFile)}</label>
                <textarea class="w-textarea orch-editor" data-mono id="orch-file-editor" rows="18" spellcheck="false" ${editable ? '' : 'readonly'}>${esc(files[activeFile])}</textarea></div>
                ${editable ? `<div class="w-btn-row"><button type="button" class="w-btn" data-variant="ghost" data-size="sm" data-remove-file>${icon('trash-2', 'sm')}<span>Remove ${esc(activeFile)}</span></button></div>` : ''}`;
            const ta = body.querySelector('textarea');
            ta.addEventListener('input', () => { files[activeFile] = ta.value; changed.add(activeFile); markDirty(); });
            body.querySelector('[data-remove-file]')?.addEventListener('click', async () => {
                if (!await ui.confirm({ title: `Remove ${activeFile}?`, message: 'The file is removed from the draft when you save.', confirmLabel: 'Remove file', tone: 'danger' })) return;
                removed.add(activeFile); changed.delete(activeFile); markDirty(); paintFiles();
            });
            el.querySelectorAll('[data-file]').forEach(b => b.addEventListener('click', () => { activeFile = b.dataset.file; paintFiles(); }));
            const tabsEl = el.querySelector('[data-file-tabs] [role="tablist"]');
            tabsEl?.addEventListener('keydown', e => {
                const items = [...tabsEl.querySelectorAll('[role="tab"]')];
                const i = items.indexOf(document.activeElement);
                const next = e.key === 'ArrowRight' ? (i + 1) % items.length : e.key === 'ArrowLeft' ? (i - 1 + items.length) % items.length : -1;
                if (i < 0 || next < 0) return;
                e.preventDefault(); activeFile = items[next].dataset.file; paintFiles(); el.querySelector(`[data-file="${CSS.escape(activeFile)}"]`)?.focus();
            });
        };
        paintFiles();
        el.querySelector('#orch-manifest').addEventListener('input', () => { manifestDirty = true; markDirty(); });
        el.querySelector('[data-add-file]')?.addEventListener('click', async () => {
            const name = await ui.prompt({ title: 'Add a file', label: 'File name', hint: 'For example rotate.sh or check.py. Letters, digits, dots, dashes and underscores.', okLabel: 'Add file' });
            if (!name) return;
            const clean = name.trim();
            if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/.test(clean)) { ui.toast('Use letters, digits, dots, dashes and underscores in file names.', 'danger'); return; }
            if (!(clean in files) || removed.has(clean)) { files[clean] = files[clean] && !removed.has(clean) ? files[clean] : ''; removed.delete(clean); changed.add(clean); }
            activeFile = clean; markDirty(); paintFiles();
            el.querySelector('#orch-file-editor')?.focus();
        });
        const save = async button => {
            const args = { ref: t.ref };
            if (manifestDirty) args.manifest = el.querySelector('#orch-manifest').value;
            if (changed.size) args.files = Object.fromEntries([...changed].map(n => [n, files[n]]));
            if (removed.size) args.remove_files = [...removed];
            try {
                await ui.busy(button, () => api.cmd('updateDraft', args));
                manifestDirty = false; changed.clear(); removed.clear(); markDirty();
                ui.toast('Draft saved', 'ok');
                Orch.refresh();
                return true;
            } catch (err) { ui.toast(`Couldn’t save: ${err.message}`, 'danger'); return false; }
        };
        el.querySelector('[data-save]')?.addEventListener('click', e => save(e.currentTarget));
        el.querySelector('[data-chat]').addEventListener('click', e => openChat(t.ref, e.currentTarget));
        const guard = fn => async (...a) => {
            if (dirty()) {
                const ok = await ui.confirm({ title: 'Save your changes first?', message: 'Lint, tests and review use the saved draft. Save now and continue?', confirmLabel: 'Save and continue' });
                if (!ok || !(await save(el.querySelector('[data-save]')))) return null;
            }
            return fn(...a);
        };
        ui.bindActions(el, t.actions, {
            args: { ref: t.ref }, subject: t.ref, done: Orch.catalog.toolDone(t),
            dialogs: { test: guard(() => testDialog(t)) },
            overrides: {
                submitReview: guard(() => submitDialog(t, () => Orch.refresh())),
                lintDraft: guard(async () => {
                    try { await ui.command('lintDraft', { ref: t.ref }, { ok: 'Lint finished', button: el.querySelector('[data-action="lintDraft"]') }); Orch.refresh(); } catch (_) { /* toasted */ }
                }),
            },
        });
        const onBeforeUnload = e => { if (dirty()) { e.preventDefault(); e.returnValue = ''; } };
        window.addEventListener('beforeunload', onBeforeUnload);
        return {
            onChange(change) {
                if (change.scope !== 'tool' || (change.id && change.id !== t.tool_id)) return;
                if (!dirty()) { Orch.refresh(); return; }
                el.querySelector('[data-stale]').innerHTML = ui.banner('info', '●', 'This draft changed elsewhere (the AI may have updated it). Your unsaved edits are kept.', '<button type="button" class="w-btn" data-variant="tonal" data-tone="info" data-size="sm" data-reload>Discard my edits and reload</button>');
                el.querySelector('[data-reload]').addEventListener('click', () => { manifestDirty = false; changed.clear(); removed.clear(); Orch.refresh(); });
            },
            dispose() { window.removeEventListener('beforeunload', onBeforeUnload); },
        };
    }

    function render(el, route, opts) {
        if (route.parts[0] === 'runbook') return Orch.runbooks.renderEditor(el, route, opts);
        return route.parts.length >= 2 ? renderEditor(el, route, opts) : renderList(el, route, opts);
    }

    Orch.studio = { newTool, openChat, testDialog, submitDialog, stepper };
    Orch.routes.studio = { nav: 'studio', title: 'Studio', scopes: ['tool'], render, crumbs: r => (r.parts[0] === 'runbook'
        ? [{ label: 'Studio', to: '#/studio' }, { label: r.parts[1] === 'new' ? 'New runbook' : `${r.parts[1] || ''}@${r.parts[2] || ''}` }]
        : r.parts.length >= 2 ? [{ label: 'Studio', to: '#/studio' }, { label: `${r.parts[0]}@${r.parts[1]}` }] : [{ label: 'Studio' }]) };
})();
