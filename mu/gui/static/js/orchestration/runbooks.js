/* Orchestration — Runbook editor (documentation/plans/runbook_editor.md §4).
 *
 * #/studio/runbook/<id>/<v> edits a draft runbook in two views of one
 * document: structured step cards (tool picker, schema-driven params with a
 * literal/expression toggle, when:, on_failure, node selector) and the YAML
 * of the whole spec. Switching views converts through the server
 * (validateRunbook), so the two never disagree. Checks run debounced after
 * every edit on the server's validator: problems sit at their field, the
 * status strip says whether the draft can be submitted. Invalid drafts save
 * (even YAML that doesn't parse); they can't be submitted.
 *
 * Saving sends base_digest: a draft someone else (usually the AI) changed
 * since it was loaded is never overwritten — the save gets a 409 and the
 * stale banner offers Reload or Keep mine.
 *
 * The model functions (specToModel, modelToSpec, inputsToRows, rowsToInputs)
 * are pure: a spec survives form → spec unchanged.
 */
(() => {
    'use strict';

    /* ------------------------------------------------------------ pure model */
    const clone = v => (v === undefined ? undefined : JSON.parse(JSON.stringify(v)));
    const STEP_FIELDS = ['name', 'tool', 'params', 'when', 'on_failure', 'nodes'];
    const TOP_FIELDS = ['id', 'title', 'summary', 'inputs', 'steps'];

    function stepToModel(s) {
        const raw = s && typeof s === 'object' ? clone(s) : {};
        return {
            name: raw.name ?? '', tool: raw.tool ?? '', params: raw.params && typeof raw.params === 'object' ? raw.params : (raw.params ?? {}),
            when: raw.when ?? '', on_failure: raw.on_failure ?? 'stop', nodes: raw.nodes ?? '', raw,
        };
    }
    function specToModel(spec) {
        const raw = spec && typeof spec === 'object' ? clone(spec) : {};
        return {
            raw, id: raw.id ?? '', title: raw.title ?? '', summary: raw.summary ?? '',
            inputs: raw.inputs === undefined ? undefined : raw.inputs,
            steps: Array.isArray(raw.steps) ? raw.steps.map(stepToModel) : [],
        };
    }
    const isEmptyObject = v => v && typeof v === 'object' && !Array.isArray(v) && !Object.keys(v).length;
    function stepToSpec(step) {
        const raw = step.raw || {};
        const out = {};
        Object.keys(raw).forEach(k => { out[k] = STEP_FIELDS.includes(k) ? clone(step[k]) : clone(raw[k]); });
        STEP_FIELDS.forEach(k => {
            if (k in out) return;
            const v = step[k];
            if (k === 'name' || k === 'tool') { if (v !== '' && v != null) out[k] = v; return; }
            if (k === 'params') { if (v && !isEmptyObject(v)) out[k] = clone(v); return; }
            if (k === 'on_failure') { if (v && v !== 'stop') out[k] = v; return; }
            if (v !== '' && v != null) out[k] = v;
        });
        return out;
    }
    function modelToSpec(model) {
        const raw = model.raw || {};
        const out = {};
        const value = k => (k === 'steps' ? model.steps.map(stepToSpec) : clone(model[k]));
        Object.keys(raw).forEach(k => { out[k] = TOP_FIELDS.includes(k) ? value(k) : clone(raw[k]); });
        if (!('id' in out)) out.id = model.id;
        if (!('title' in out) && model.title) out.title = model.title;
        if (!('summary' in out) && model.summary) out.summary = model.summary;
        if (!('inputs' in out) && model.inputs !== undefined) out.inputs = clone(model.inputs);
        if (!('steps' in out)) out.steps = value('steps');
        Object.keys(out).forEach(k => { if (out[k] === undefined) delete out[k]; });
        return out;
    }

    /* Inputs table rows: {name, raw (the property schema), required, edits: {field: text}}.
     * Untouched rows give back their property exactly. */
    const ROW_FIELDS = ['type', 'default', 'enum', 'pattern', 'maxLength', 'description'];
    function inputsToRows(schema) {
        const props = (schema && schema.properties) || {};
        const req = new Set((schema && schema.required) || []);
        return Object.entries(props).map(([name, p]) => ({ name, raw: clone(p) || {}, required: req.has(name), edits: {} }));
    }
    function fieldText(row, f) {
        if (f in row.edits) return row.edits[f];
        const p = row.raw || {};
        switch (f) {
        case 'type': return Array.isArray(p.type) ? p.type.join(',') : (p.type || '');
        case 'default': return 'default' in p ? (typeof p.default === 'string' ? p.default : JSON.stringify(p.default)) : '';
        case 'enum': return Array.isArray(p.enum) ? p.enum.map(v => (typeof v === 'string' ? v : JSON.stringify(v))).join(', ') : '';
        case 'pattern': return p.pattern || '';
        case 'maxLength': return p.maxLength != null ? String(p.maxLength) : '';
        case 'description': return p.description || '';
        default: return '';
        }
    }
    function coerce(text, type) {
        const t = String(text).trim();
        if (type === 'integer' || type === 'number') { const n = Number(t); return t !== '' && !Number.isNaN(n) ? n : t; }
        if (type === 'boolean') { if (t === 'true') return true; if (t === 'false') return false; return t; }
        if (type === 'string') return String(text);
        try { return JSON.parse(t); } catch (_) { return String(text); }
    }
    function propOf(row) {
        const p = clone(row.raw || {}) || {};
        const type = 'type' in row.edits ? row.edits.type : (Array.isArray(p.type) ? '' : (p.type || ''));
        Object.entries(row.edits).forEach(([f, v]) => {
            const empty = String(v).trim() === '';
            if (f === 'type') { if (empty) delete p.type; else p.type = v; }
            else if (f === 'default') { if (empty) delete p.default; else p.default = coerce(v, type); }
            else if (f === 'enum') { if (empty) delete p.enum; else p.enum = String(v).split(',').map(s => s.trim()).filter(Boolean).map(x => coerce(x, type)); }
            else if (f === 'maxLength') { if (empty) delete p.maxLength; else { const n = Number(v); p.maxLength = Number.isNaN(n) ? v : n; } }
            else if (empty) delete p[f];
            else p[f] = v;
        });
        return p;
    }
    function rowsToInputs(rows, base) {
        const out = base ? clone(base) : { type: 'object', properties: {}, additionalProperties: false };
        const named = rows.filter(r => r.name);
        if (named.length || !base || 'properties' in base) {
            const props = {};
            named.forEach(r => { props[r.name] = propOf(r); });
            out.properties = props;
        }
        const req = named.filter(r => r.required).map(r => r.name);
        const same = base && Array.isArray(base.required) && base.required.length === req.length && base.required.every(n => req.includes(n));
        if (same) out.required = clone(base.required);
        else if (req.length || (base && 'required' in base)) out.required = req;
        else delete out.required;
        return out;
    }

    const pure = { specToModel, modelToSpec, inputsToRows, rowsToInputs, fieldText, stepToModel };
    if (typeof module !== 'undefined' && module.exports) module.exports = pure;
    if (typeof window === 'undefined' || !window.OrchUI) {
        if (typeof window !== 'undefined') { window.Orch = window.Orch || {}; window.Orch.runbooks = { ...pure }; }
        return;
    }

    /* ------------------------------------------------------------ page */
    const api = window.OrchAPI;
    const ui = window.OrchUI;
    const P = window.OrchPickers;
    const { esc, icon } = ui;
    const Orch = (window.Orch = window.Orch || { routes: {} });

    const ACTION_IDS = ['testRunbook', 'submitRunbook', 'discardRunbookDraft'];
    const editorHref = (id, v) => `#/studio/runbook/${encodeURIComponent(id)}/${encodeURIComponent(v)}`;
    const DECISION = {
        allow: { semantic: 'ok', glyph: '✓', label: 'Allowed' },
        confirm: { semantic: 'attention', glyph: '◆', label: 'Needs approval' },
        typed: { semantic: 'attention', glyph: '◆', label: 'Typed confirmation' },
        deny: { semantic: 'danger', glyph: '✗', label: 'Denied by policy' },
    };
    const errText = msg => `<span aria-hidden="true">✗</span> ${esc(msg)}`;
    const stepOf = path => { const m = /^steps\[(\d+)\]/.exec(path || ''); return m ? Number(m[1]) : null; };
    const errorsOf = problems => (problems || []).filter(p => (p.severity || 'error') === 'error');
    const warningsOf = problems => (problems || []).filter(p => p.severity === 'warning');
    const problemLabel = p => (p.path ? `${p.path}: ${p.message}` : p.message);

    function decisionChip(d) {
        if (!d) return '';
        return ui.chip(DECISION[d.level] || { semantic: 'neutral', glyph: '○', label: d.level || 'Unknown' }, { title: d.summary || '' });
    }

    /* ------------------------------------------------------------ new runbook */
    function newRunbook() { Orch.go('#/studio/runbook/new'); }

    async function renderNew(el, route, { setCrumbs }) {
        setCrumbs([{ label: 'Studio', to: '#/studio' }, { label: 'New runbook' }]);
        el.innerHTML = `
            <header class="w-page__head orch-head"><div class="orch-head__title"><h1 class="w-page__title">New runbook</h1>
                </div></header>
            <form class="w-card orch-card orch-form-narrow" novalidate><div class="w-card__body orch-stack">
                <div class="w-field"><label class="w-field__label" for="rb-new-id">Runbook id</label><input class="w-input" data-mono id="rb-new-id" placeholder="web.safe-deploy" autocomplete="off" spellcheck="false" data-autofocus>
                    <p class="w-field__hint">A dotted lowercase name. It can’t change after the runbook is created.</p></div>
                <div class="w-field"><label class="w-field__label" for="rb-new-title">Title</label><input class="w-input" id="rb-new-title" placeholder="Safe web deploy" autocomplete="off"></div>
                <p class="w-field__error" data-error role="alert"></p>
                <div class="w-btn-row orch-form-actions"><a class="w-btn" data-variant="ghost" href="#/studio">Cancel</a><button type="submit" class="w-btn" data-variant="primary">${icon('plus')}<span>Create draft</span></button></div>
            </div></form>`;
        const form = el.querySelector('form');
        el.querySelector('#rb-new-id').focus();
        form.addEventListener('submit', async e => {
            e.preventDefault();
            const id = el.querySelector('#rb-new-id').value.trim();
            const title = el.querySelector('#rb-new-title').value.trim();
            const err = form.querySelector('[data-error]');
            if (!/^[a-z0-9][a-z0-9_-]*(\.[a-z0-9][a-z0-9_-]*)+$/.test(id)) { err.innerHTML = errText('Use a dotted lowercase id like web.safe-deploy.'); el.querySelector('#rb-new-id').focus(); return; }
            if (!title) { err.innerHTML = errText('Give it a title.'); el.querySelector('#rb-new-title').focus(); return; }
            try {
                const res = await ui.busy(form.querySelector('[type=submit]'), () => api.cmd('saveRunbookDraft', { spec: { id, title, steps: [{ name: 'step1', tool: '' }] } }));
                ui.toast(`Draft ${res.data.ref} created`, 'ok');
                const [rid, v] = res.data.ref.split('@');
                Orch.go(editorHref(rid, v));
            } catch (e2) { err.innerHTML = errText(e2.message); }
        });
        return null;
    }

    /* ------------------------------------------------------------ test dialog */
    async function testDialog(r, { before = null } = {}) {
        if (before && !(await before())) return;
        const id = ui.uid('rbtest');
        const form = document.createElement('form');
        form.className = 'orch-stack';
        form.noValidate = true;
        form.innerHTML = `
            <p class="orch-muted">The test runs this draft on canary nodes. Its steps use published tools. It is approved like a tool test unless your access policy allows it; the run refuses to start if the draft changes after approval.</p>
            <div data-nodes></div>
            <fieldset class="orch-fieldset"><legend>Inputs</legend><div data-inputs></div></fieldset>
            <label class="w-check"><input class="w-check__input" type="checkbox" id="${id}-check" checked><span>Check mode (dry run: report, change nothing)</span></label>
            <p class="w-field__error" data-error role="alert"></p>
            <div class="w-btn-row orch-dialog__actions"><button type="button" class="w-btn" data-variant="secondary" data-cancel>Cancel</button><button type="submit" class="w-btn" data-variant="primary">${icon('flask-conical')}<span>Propose test</span></button></div>`;
        const d = ui.dialog({ title: `Test ${r.ref} on a canary`, body: form, size: 'md' });
        const nodes = P.nodePicker(form.querySelector('[data-nodes]'), { multi: true, test: true, label: 'Canary nodes' });
        const inputs = P.paramsForm(form.querySelector('[data-inputs]'), r.inputs || {}, { emptyText: 'This runbook takes no inputs.' });
        nodes.ready.then(() => {
            const first = form.querySelector('[data-pick]');
            if (first && !nodes.value().length) { first.checked = true; first.dispatchEvent(new Event('change')); }
        });
        form.querySelector('[data-cancel]').onclick = () => d.close();
        form.onsubmit = async e => {
            e.preventDefault();
            const err = form.querySelector('[data-error]');
            const picked = nodes.value();
            const read = inputs.read();
            if (!picked.length) { err.innerHTML = errText('Choose a canary node (mark one canary or start a test node).'); return; }
            if (read.errors.length) { err.innerHTML = errText(read.errors.join('; ')); return; }
            try {
                const res = await ui.busy(form.querySelector('[type=submit]'), () => api.cmd('proposeRunbookTest', { ref: r.ref, nodes: picked, params: read.values, check_mode: form.querySelector(`#${id}-check`).checked }));
                d.close();
                const run = res.data.run;
                ui.toast(run.state === 'proposed' ? 'Test proposed. Approve it to run.' : 'Test started (allowed by your access policy).', 'ok');
                Orch.go(`#/run/${encodeURIComponent(run.id)}`);
            } catch (e2) { err.innerHTML = errText(e2.message); }
        };
    }

    /* ------------------------------------------------------------ bulk upgrade */
    /* spec: the runbook spec; published: start a new version instead of editing;
     * onApply(upgradedSpec) for drafts. */
    async function upgradeDialog({ spec, ref = '', published = false, onApply = null }) {
        let report;
        try { report = (await api.cmd('upgradeRunbookTools', { spec })).data; } catch (err) { ui.toast(`Couldn’t check for upgrades: ${err.message}`, 'danger'); return; }
        const steps = report.steps || [];
        if (!steps.length) { ui.toast('Every step already uses the latest published version of its tool.', 'info'); return; }
        const form = document.createElement('form');
        form.className = 'orch-stack';
        form.noValidate = true;
        const card = s => {
            const eff = s.effect || {};
            const rb = s.rollback || {};
            const notes = [];
            if (eff.changed) notes.push(`<li>${ui.effectChip(eff.from_style, eff.from)} <span aria-hidden="true">→</span> ${ui.effectChip(eff.to_style, eff.to)} <span class="orch-sub">the effect changes</span></li>`);
            (s.rejected_params || []).forEach(p => notes.push(`<li class="orch-error"><span aria-hidden="true">✗</span> ${esc(problemLabel(p))}</li>`));
            (s.removed_referenced || []).forEach(x => notes.push(`<li class="orch-error"><span aria-hidden="true">✗</span> step <code class="orch-mono">${esc(x.step)}</code> uses output <code class="orch-mono">${esc(x.output)}</code>, which ${esc(s.to)} no longer declares</li>`));
            if ((s.removed_outputs || []).length && !(s.removed_referenced || []).length) notes.push(`<li>Outputs removed: ${s.removed_outputs.map(o => `<code class="orch-mono">${esc(o)}</code>`).join(', ')} (nothing here uses them)</li>`);
            if (rb.from !== rb.to) notes.push(`<li>Rollback ${rb.to ? (rb.from ? `changes to <code class="orch-mono">${esc(rb.to)}</code>` : `added: <code class="orch-mono">${esc(rb.to)}</code>`) : 'removed: changes can’t be undone automatically'}</li>`);
            const diff = s.diff ? (window.WashDiff?.renderDiffHTML ? window.WashDiff.renderDiffHTML(s.diff, { tree: false }) : ui.code(s.diff, { lang: 'diff', title: 'tool.yaml' })) : '<p class="orch-muted">The manifests are identical.</p>';
            return `<li class="w-card orch-card orch-rb-upgrade"><header class="w-card__head"><label class="w-check"><input class="w-check__input" type="checkbox" data-up="${esc(s.index)}" checked><span>Step <code class="orch-mono">${esc(s.name)}</code>: <span class="orch-mono">${esc(s.from)}</span> <span aria-hidden="true">→</span><span class="w-sr-only"> to </span> <span class="orch-mono">${esc(s.to)}</span></span></label></header>
                <div class="w-card__body orch-stack-sm">${notes.length ? `<ul class="orch-list">${notes.join('')}</ul>` : '<p class="orch-muted">No effect, parameter, output or rollback changes.</p>'}<details class="orch-details"><summary>Manifest diff</summary><div class="orch-diff">${diff}</div></details></div></li>`;
        };
        form.innerHTML = `
            <p class="orch-muted">Steps whose tool has a newer published version. Check what changes, then apply.${published ? ' Applying starts a new draft version with the upgraded tools.' : ' Applying edits this draft; nothing is saved until you save.'}</p>
            <div class="orch-head__meta"><span class="orch-label-text">Runbook effect</span>${ui.effectChip(report.effect.from_style, report.effect.from)}${report.effect.changed ? ` <span aria-hidden="true">→</span> ${ui.effectChip(report.effect.to_style, report.effect.to)}` : ''}
                <span class="orch-label-text">Your access decision</span>${decisionChip(report.decision.from)}${report.decision.from.level !== report.decision.to.level ? ` <span aria-hidden="true">→</span> ${decisionChip(report.decision.to)}` : ''}</div>
            <ul class="orch-rb-upgrades" role="list">${steps.map(card).join('')}</ul>
            <p class="w-field__error" data-error role="alert"></p>
            <div class="w-btn-row orch-dialog__actions"><button type="button" class="w-btn" data-variant="secondary" data-cancel>Cancel</button><button type="submit" class="w-btn" data-variant="primary">${icon('refresh-cw')}<span>${published ? 'New version with upgraded tools' : 'Apply upgrades'}</span></button></div>`;
        const d = ui.dialog({ title: 'Upgrade tools', body: form, size: 'lg' });
        form.querySelector('[data-cancel]').onclick = () => d.close();
        form.onsubmit = async e => {
            e.preventDefault();
            const chosen = [...form.querySelectorAll('[data-up]:checked')].map(c => Number(c.dataset.up));
            if (!chosen.length) { form.querySelector('[data-error]').innerHTML = errText('Select at least one step.'); return; }
            try {
                const res = (await ui.busy(form.querySelector('[type=submit]'), () => api.cmd('upgradeRunbookTools', { spec, steps: chosen }))).data;
                if (published) {
                    const out = await api.cmd('newRunbookVersion', { ref, spec: res.spec });
                    d.close();
                    ui.toast(`${out.data.ref} started with upgraded tools`, 'ok');
                    const [rid, v] = out.data.ref.split('@');
                    Orch.go(editorHref(rid, v));
                } else {
                    d.close();
                    onApply?.(res.spec);
                    ui.toast(`Upgraded ${ui.plural(chosen.length, 'step')}. Save to keep it.`, 'ok');
                }
            } catch (e2) { form.querySelector('[data-error]').innerHTML = errText(e2.message); }
        };
    }

    /* ------------------------------------------------------------ editor */
    async function renderEditor(el, route, { isCurrent, setCrumbs, setQuery }) {
        if (route.parts[1] === 'new') return renderNew(el, route, { setCrumbs });
        const [, rid, version] = route.parts;
        const r = await api.get(`runbooks/${encodeURIComponent(rid)}/${encodeURIComponent(version)}`);
        if (!isCurrent()) return null;
        setCrumbs([{ label: 'Studio', to: '#/studio' }, { label: r.ref }]);
        const editable = r.status === 'draft';
        let model = specToModel(r.spec);
        let rows = inputsToRows(model.inputs || {});
        let baseDigest = r.digest;
        let dirty = false;
        let tab = r.source ? 'yaml' : (route.query.get('tab') === 'yaml' ? 'yaml' : 'form');
        let yamlText = r.source || r.spec_yaml || '';
        let result = null;          // last validateRunbook result
        let resultAt = 0;
        let controller = null;
        let stale = '';
        const comps = [];            // per step: {combo, params, sel}
        let labelKeys = [];
        P.labels().then(ix => { labelKeys = Object.keys(ix.labels || {}); }).catch(() => {});

        el.innerHTML = `
            <header class="w-page__head orch-head">
                <div class="orch-head__title">
                    <h1 class="w-page__title" data-title>${esc(r.title)} <span class="orch-version-tag orch-mono">v${esc(r.version)}</span></h1>
                    <div class="orch-head__meta"><span data-rb-status>${ui.chip(r.style)}</span><span data-effect>${ui.effectChip(r.effect_style, r.effect)}</span><span class="orch-mono orch-sub">${esc(r.ref)}</span><span data-dirty hidden>${ui.chip({ semantic: 'caution', glyph: '◆', label: 'Unsaved changes' })}</span></div>
                </div>
            </header>
            <div class="orch-stack">
                <div class="orch-rb-strip" data-strip role="status" aria-live="polite"></div>
                <div data-stale></div>
                ${r.status === 'in_review' ? ui.banner('attention', '◆', 'In review: editing is locked. Request changes from the catalog page to reopen it.', `<a class="w-btn" data-variant="tonal" data-tone="attention" data-size="sm" href="${Orch.catalog.runbookHref(r)}">Review it</a>`) : ''}
                ${!['draft', 'in_review'].includes(r.status) ? ui.banner('info', '●', `This version is ${esc(r.style.label)} and can’t be edited. Start a new version from the catalog page.`, `<a class="w-btn" data-variant="tonal" data-tone="info" data-size="sm" href="${Orch.catalog.runbookHref(r)}">Open in catalog</a>`) : ''}
                <div class="orch-toolbar">
                    ${editable ? `<button type="button" class="w-btn" data-variant="primary" data-save disabled>${icon('check')}<span>Save draft</span></button>` : ''}
                    <span class="orch-actions" data-rb-actions>${ui.actionButtons(r.actions, { only: ACTION_IDS })}</span>
                    ${editable ? `<button type="button" class="w-btn" data-variant="secondary" data-upgrade>${icon('refresh-cw')}<span>Upgrade tools</span></button>` : ''}
                    <button type="button" class="w-btn" data-variant="ghost" data-chat>${icon('message-circle')}<span>Open discovery chat</span></button>
                    <a class="w-btn" data-variant="ghost" href="${Orch.catalog.runbookHref(r)}">Catalog view <span class="w-btn__chevron" aria-hidden="true">›</span></a>
                </div>
                <div class="orch-studio">
                    <section class="orch-studio__edit orch-stack" aria-label="Runbook editor">
                        ${ui.tabs('Runbook views', [{ id: 'form', label: 'Steps' }, { id: 'yaml', label: 'YAML' }], tab)}
                        <div class="w-tabs__panel orch-panel" id="orch-panel" role="tabpanel" aria-labelledby="orch-tab-${esc(tab)}"><fieldset class="orch-rb-doc" ${editable ? '' : 'disabled'}><legend class="w-sr-only">Runbook ${esc(r.ref)}</legend><div data-doc></div></fieldset></div>
                    </section>
                    <aside class="orch-studio__side orch-stack" aria-label="Checks">
                        <section class="orch-section" aria-labelledby="rb-checks-h"><header class="orch-section__head"><h2 class="orch-section__title" id="rb-checks-h">Checks</h2><span class="orch-spacer"></span><span class="orch-sub" data-age></span></header><div data-checks>${ui.loading('Checking…')}</div></section>
                        <section class="orch-section" aria-labelledby="rb-prev-h"><header class="orch-section__head"><h2 class="orch-section__title" id="rb-prev-h">Expression preview</h2></header>
                            <div class="orch-stack-sm"><p class="orch-muted">What each <code>when:</code> and parameter resolves to on one node, with sample inputs. Outputs of earlier steps aren’t known before a run, so they are null here.</p>
                            <div data-prev-node></div><details class="orch-details"><summary>Sample inputs</summary><div data-prev-inputs></div></details>
                            <div class="w-btn-row"><button type="button" class="w-btn" data-variant="secondary" data-size="sm" data-preview>${icon('eye', 'sm')}<span>Preview</span></button></div><div data-prev-out aria-live="polite"></div></div></section>
                        <section class="orch-section"><header class="orch-section__head"><h2 class="orch-section__title">Test results</h2></header><div data-rb-tests>${testsHtml(r)}</div></section>
                        ${r.previous ? `<section class="orch-section"><header class="orch-section__head"><h2 class="orch-section__title">Changes since ${esc(r.previous)}</h2></header>${r.diff ? `<div class="orch-diff">${window.WashDiff?.renderDiffHTML ? window.WashDiff.renderDiffHTML(r.diff, { tree: false }) : ui.code(r.diff, { lang: 'diff', title: 'diff' })}</div>` : '<p class="orch-muted">No saved changes yet.</p>'}</section>` : ''}
                        <section class="orch-section"><header class="orch-section__head"><h2 class="orch-section__title">Notes</h2></header><div data-rb-notes>${Orch.catalog.notesTimeline((r.notes || []).slice(-8))}</div></section>
                    </aside>
                </div>
            </div>`;
        const doc = el.querySelector('[data-doc]');

        /* ---------------- dirty + validation */
        let edits = 0;               // bumped on every edit: a save only cleans what it sent
        const markDirty = () => {
            edits += 1;
            dirty = true;
            el.querySelector('[data-dirty]').hidden = false;
            const save = el.querySelector('[data-save]');
            if (save) save.disabled = false;
            validateSoon();
        };
        let inflight = null;
        // The view element outlives this route (the router reuses it), so a late result must
        // check that this editor is still the one on screen.
        let disposed = false;
        async function validate() {
            if (disposed) return null;
            inflight?.abort();
            const ctrl = new AbortController();
            inflight = ctrl;
            const spec = tab === 'yaml' ? (el.querySelector('#rb-yaml')?.value ?? yamlText) : (flush(), modelToSpec(model));
            try {
                const res = await api.cmd('validateRunbook', { spec }, { signal: ctrl.signal });
                if (disposed || inflight !== ctrl) return null;
                result = res.data;
                resultAt = Date.now();
                paintChecks();
                return result;
            } catch (err) {
                if (err.name === 'AbortError' || disposed) return null;
                const box = el.querySelector('[data-checks]');
                if (box) box.innerHTML = `<p class="orch-error">${errText(`Couldn’t check the draft: ${err.message}`)}</p>`;
                return null;
            }
        }
        const validateSoon = ui.debounce(validate, 400);
        const ageTimer = setInterval(() => {
            const a = el.querySelector('[data-age]');
            if (a && resultAt) a.textContent = `checked ${ui.ago(resultAt / 1000)}`;
        }, 5000);

        function paintChecks() {
            if (!result || disposed || !el.querySelector('[data-strip]')) return;
            const errs = errorsOf(result.problems);
            const warns = warningsOf(result.problems);
            el.querySelector('[data-strip]').innerHTML = errs.length
                ? `${ui.chip({ semantic: 'danger', glyph: '✗', label: `${ui.plural(errs.length, 'problem')} — this draft can’t be submitted` })}${dirty ? '<span class="orch-sub">(including unsaved edits)</span>' : ''}`
                : ui.chip({ semantic: 'ok', glyph: '✓', label: warns.length ? `Valid · ${ui.plural(warns.length, 'warning')}` : 'Valid' });
            if (result.effect_style) el.querySelector('[data-effect]').innerHTML = ui.effectChip(result.effect_style, result.effect);
            el.querySelector('[data-age]').textContent = `checked ${ui.ago(resultAt / 1000)}`;
            const groups = new Map();
            [...errs, ...warns].forEach(p => { const i = stepOf(p.path); const key = i === null ? 'Runbook' : `Step ${i + 1}${model.steps[i]?.name ? ` · ${model.steps[i].name}` : ''}`; if (!groups.has(key)) groups.set(key, []); groups.get(key).push(p); });
            el.querySelector('[data-checks]').innerHTML = `<div class="orch-stack-sm">
                ${ui.kv([['Effect', result.effect_style ? ui.effectChip(result.effect_style, result.effect) : '—', true], ['Your access decision', result.decision ? `${decisionChip(result.decision)} <span class="orch-sub">${esc(result.decision.summary || '')}</span>` : '—', true]])}
                ${groups.size ? [...groups.entries()].map(([g, ps]) => `<div class="orch-rb-pgroup"><p class="orch-label-text">${esc(g)}</p><ul class="orch-rb-problems" role="list">${ps.map(p => `<li><button type="button" class="orch-rb-problem" data-tone="${p.severity === 'warning' ? 'caution' : 'danger'}" data-path="${esc(p.path || '')}"${p.line ? ` data-line="${esc(p.line)}"` : ''}><span aria-hidden="true">${p.severity === 'warning' ? '◆' : '✗'}</span> <span class="w-sr-only">${p.severity === 'warning' ? 'Warning' : 'Problem'}: </span>${p.path ? `<code class="orch-mono">${esc(p.path)}</code> ` : ''}${esc(p.message)}${p.line ? ` <span class="orch-sub">line ${esc(p.line)}</span>` : ''}</button></li>`).join('')}</ul></div>`).join('') : '<p class="orch-muted">No problems.</p>'}</div>`;
            el.querySelectorAll('[data-checks] [data-path]').forEach(b => b.addEventListener('click', () => focusPath(b.dataset.path, Number(b.dataset.line || 0))));
            if (tab === 'form') paintCardProblems();
            else paintGutter();
        }
        function focusPath(path, line) {
            if (tab === 'yaml') {
                const ta = el.querySelector('#rb-yaml');
                if (ta && line) { const lines = ta.value.split('\n'); const at = lines.slice(0, line - 1).reduce((a, l) => a + l.length + 1, 0); ta.focus(); ta.setSelectionRange(at, at + (lines[line - 1] || '').length); }
                return;
            }
            const i = stepOf(path);
            if (i === null) {
                const target = path.startsWith('inputs') ? el.querySelector('[data-input-row] input') : el.querySelector(path === 'title' ? '#rb-title' : '#rb-title');
                target?.focus();
                return;
            }
            const card = el.querySelector(`[data-step="${i}"]`);
            if (!card) return;
            const rest = path.slice(`steps[${i}]`.length);
            let target = null;
            if (rest.startsWith('.params.')) { if (comps[i]?.params?.focusField(rest.slice(8).split(/[.[]/)[0])) return; }
            if (rest.startsWith('.tool')) target = card.querySelector('.orch-combo__input');
            else if (rest.startsWith('.when')) target = card.querySelector('[data-when]');
            else if (rest.startsWith('.name')) target = card.querySelector('[data-name]');
            else if (rest.startsWith('.on_failure')) target = card.querySelector('[data-onfail] [aria-pressed="true"]');
            else if (rest.startsWith('.nodes')) target = card.querySelector('[data-nodes] input, [data-nodes] select, [data-nodes] button');
            (target || card.querySelector('[data-handle]'))?.focus();
            card.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        }
        function paintCardProblems() {
            el.querySelectorAll('[data-step]').forEach(card => {
                const i = Number(card.dataset.step);
                const ps = (result?.problems || []).filter(p => stepOf(p.path) === i);
                const box = card.querySelector('[data-card-problems]');
                box.innerHTML = ps.length ? `<ul class="orch-rb-problems" role="list">${ps.map(p => `<li class="${p.severity === 'warning' ? 'orch-rb-warn' : 'orch-error'}"><span aria-hidden="true">${p.severity === 'warning' ? '◆' : '✗'}</span> <span class="w-sr-only">${p.severity === 'warning' ? 'Warning' : 'Problem'}: </span><code class="orch-mono">${esc(p.path.slice(`steps[${i}]`.length).replace(/^\./, '') || 'step')}</code> ${esc(p.message)}</li>`).join('')}</ul>` : '';
                card.dataset.state = ps.some(p => (p.severity || 'error') === 'error') ? 'danger' : '';
                const info = (result?.steps_info || [])[i];
                const eff = card.querySelector('[data-step-effect]');
                if (eff) eff.innerHTML = info?.ok ? ui.effectChip(info.effect_style, info.effect) : '';
            });
        }

        /* ---------------- form view */
        function flush() {
            if (tab !== 'form') return;
            comps.forEach((c, i) => {
                const step = model.steps[i];
                if (!step || !c) return;
                if (c.params) step.params = c.params.read().values;
                if (c.sel) step.nodes = c.sel.value() || '';
            });
        }
        function completionsFor(i) {
            return () => {
                const out = [];
                Object.entries((model.inputs || {}).properties || {}).forEach(([n, p]) => out.push({ value: `inputs.${n}`, detail: p.type ? String(p.type) : 'input' }));
                model.steps.slice(0, i).forEach((s, j) => {
                    if (!s.name) return;
                    const v = comps[j]?.combo?.version();
                    const outs = (v?.outputs || (result?.steps_info || [])[j]?.outputs || {}).properties || {};
                    Object.entries(outs).forEach(([n, p]) => out.push({ value: `steps.${s.name}.outputs.${n}`, detail: p.type ? String(p.type) : 'output' }));
                    out.push({ value: `steps.${s.name}.state`, detail: 'succeeded | failed | skipped …' }, { value: `steps.${s.name}.exit_code`, detail: 'integer' });
                });
                labelKeys.forEach(k => out.push({ value: `node.labels.${k}`, detail: 'label' }));
                out.push({ value: 'node.name', detail: 'string' });
                return out;
            };
        }
        function paintForm() {
            comps.length = 0;
            const inputRows = rows.map((row, i) => `<tr data-input-row="${i}">
                <td><input class="w-input" data-mono data-in="name" value="${esc(row.name)}" aria-label="Input name" autocomplete="off" spellcheck="false"></td>
                <td><span class="w-select"><select class="w-select__control" data-in="type" aria-label="Type of ${esc(row.name)}">${['', 'string', 'integer', 'number', 'boolean', 'array', 'object'].map(t => `<option value="${t}"${fieldText(row, 'type') === t ? ' selected' : ''}>${t || 'any'}</option>`).join('')}${['', 'string', 'integer', 'number', 'boolean', 'array', 'object'].includes(fieldText(row, 'type')) ? '' : `<option selected>${esc(fieldText(row, 'type'))}</option>`}</select></span></td>
                <td><input class="w-input" data-mono data-in="default" value="${esc(fieldText(row, 'default'))}" aria-label="Default of ${esc(row.name)}" autocomplete="off"></td>
                <td><input class="w-input" data-mono data-in="enum" value="${esc(fieldText(row, 'enum'))}" placeholder="a, b" aria-label="Allowed values of ${esc(row.name)}" autocomplete="off"></td>
                <td><input class="w-input" data-mono data-in="pattern" value="${esc(fieldText(row, 'pattern'))}" aria-label="Pattern of ${esc(row.name)}" autocomplete="off"></td>
                <td><input class="w-input" type="number" min="0" data-in="maxLength" value="${esc(fieldText(row, 'maxLength'))}" aria-label="Maximum length of ${esc(row.name)}"></td>
                <td><input class="w-input" data-in="description" value="${esc(fieldText(row, 'description'))}" aria-label="Description of ${esc(row.name)}" autocomplete="off"></td>
                <td><label class="w-check"><input class="w-check__input" type="checkbox" data-in="required"${row.required ? ' checked' : ''}><span class="w-sr-only">${esc(row.name)} is required</span></label></td>
                <td><button type="button" class="w-btn" data-variant="icon" data-size="sm" data-in-del="${i}" aria-label="Remove input ${esc(row.name)}" title="Remove">${icon('trash-2', 'sm')}</button></td></tr>`).join('');
            doc.innerHTML = `<div class="orch-stack">
                <div class="w-card orch-card"><div class="w-card__body orch-stack-sm">
                    <div class="orch-form-grid">
                        <div class="w-field"><label class="w-field__label" for="rb-id">Id</label><input class="w-input" data-mono id="rb-id" value="${esc(model.id)}" readonly aria-describedby="rb-id-hint"><p class="w-field__hint" id="rb-id-hint">Fixed after creation.</p></div>
                        <div class="w-field"><label class="w-field__label" for="rb-title">Title</label><input class="w-input" id="rb-title" value="${esc(model.title)}" autocomplete="off"></div>
                    </div>
                    <div class="w-field"><label class="w-field__label" for="rb-summary">Summary <span class="orch-muted">(optional)</span></label><textarea class="w-textarea" id="rb-summary" rows="2">${esc(model.summary)}</textarea></div>
                </div></div>
                <section class="orch-section" aria-labelledby="rb-inputs-h"><header class="orch-section__head"><h2 class="orch-section__title" id="rb-inputs-h">Inputs</h2><span class="orch-spacer"></span><button type="button" class="w-btn" data-variant="ghost" data-size="sm" data-in-yaml>Edit as YAML</button><button type="button" class="w-btn" data-variant="secondary" data-size="sm" data-in-add>${icon('plus', 'sm')}<span>Add input</span></button></header>
                    ${rows.length ? `<div class="w-table-wrap orch-table-wrap"><table class="w-table orch-table orch-rb-inputs" aria-label="Runbook inputs"><thead><tr><th scope="col">Name</th><th scope="col">Type</th><th scope="col">Default</th><th scope="col">Allowed values</th><th scope="col">Pattern</th><th scope="col">Max length</th><th scope="col">Description</th><th scope="col">Required</th><th scope="col"><span class="w-sr-only">Actions</span></th></tr></thead><tbody>${inputRows}</tbody></table></div>` : '<p class="orch-muted">No inputs. Runs pass inputs to steps as <code>${{ inputs.name }}</code>.</p>'}
                </section>
                <section class="orch-section" aria-labelledby="rb-steps-h"><header class="orch-section__head"><h2 class="orch-section__title" id="rb-steps-h">Steps</h2><span class="w-count">${model.steps.length}</span><span class="orch-spacer"></span><span class="orch-sub">Drag a handle, use the arrows or Alt+↑/↓ to reorder.</span></header>
                    <ol class="orch-rb-steps" role="list">${model.steps.map((s, i) => stepCard(s, i)).join('')}</ol>
                    <div class="w-btn-row"><button type="button" class="w-btn" data-variant="secondary" data-step-add>${icon('plus')}<span>Add step</span></button></div>
                </section></div>`;
            bindForm();
            model.steps.forEach((s, i) => mountStep(i));
            if (result) paintCardProblems();
        }
        function stepCard(s, i) {
            const n = model.steps.length;
            return `<li class="w-card orch-card orch-rb-step" data-step="${i}" draggable="false" aria-labelledby="rb-step-${i}-h">
                <header class="w-card__head orch-rb-step__head">
                    <button type="button" class="w-btn orch-rb-handle" data-variant="icon" data-size="sm" data-handle="${i}" aria-label="Move step ${i + 1} (drag, or Alt+Arrow keys)" title="Drag to reorder">⠿</button>
                    <span class="orch-step-num" aria-hidden="true">${i + 1}</span>
                    <h3 class="w-card__title" id="rb-step-${i}-h"><span class="w-sr-only">Step ${i + 1}: </span>${esc(s.name || `step ${i + 1}`)}</h3>
                    <span data-step-effect></span><span class="orch-spacer"></span>
                    <span class="w-btn-row orch-rb-step__tools">
                        <button type="button" class="w-btn" data-variant="icon" data-size="sm" data-move="${i}:-1" aria-label="Move step ${i + 1} up" title="Move up"${i === 0 ? ' disabled' : ''}>${icon('arrow-up', 'sm')}</button>
                        <button type="button" class="w-btn" data-variant="icon" data-size="sm" data-move="${i}:1" aria-label="Move step ${i + 1} down" title="Move down"${i === n - 1 ? ' disabled' : ''}>${icon('arrow-down', 'sm')}</button>
                        <button type="button" class="w-btn" data-variant="icon" data-size="sm" data-dup="${i}" aria-label="Duplicate step ${i + 1}" title="Duplicate">${icon('copy', 'sm')}</button>
                        <button type="button" class="w-btn" data-variant="icon" data-size="sm" data-del="${i}" aria-label="Delete step ${i + 1}" title="Delete"${n === 1 ? ' disabled' : ''}>${icon('trash-2', 'sm')}</button>
                    </span>
                </header>
                <div class="w-card__body orch-stack-sm">
                    <div class="w-field orch-rb-name"><label class="w-field__label" for="rb-s${i}-name">Name</label><input class="w-input" data-mono id="rb-s${i}-name" data-name value="${esc(s.name)}" autocomplete="off" spellcheck="false"></div>
                    <div data-tool></div>
                    <fieldset class="orch-fieldset"><legend>Parameters</legend><div data-params>${ui.loading('Loading the tool…')}</div></fieldset>
                    <div class="w-field"><label class="w-field__label" for="rb-s${i}-when">Run only when <span class="orch-muted">(optional)</span></label><input class="w-input orch-expr" data-mono id="rb-s${i}-when" data-when value="${esc(s.when)}" placeholder="steps.check.outputs.free_pct < 20" autocomplete="off" spellcheck="false"><p class="w-field__hint">An expression; empty means always. Ctrl+Space lists what you can reference.</p></div>
                    <div class="w-field"><span class="w-field__label" id="rb-s${i}-of">If this step fails</span><div class="w-segmented" role="group" aria-labelledby="rb-s${i}-of" data-onfail>${[['stop', 'Stop'], ['continue', 'Continue'], ['rollback', 'Roll back']].map(([k, l]) => `<button type="button" class="w-segmented__option" data-of="${k}" aria-pressed="${s.on_failure === k}">${l}</button>`).join('')}</div><p class="w-field__hint" data-of-hint></p></div>
                    <details class="orch-details orch-rb-nodes"${s.nodes ? ' open' : ''}><summary>Only on some nodes${s.nodes ? `: <code class="orch-mono">${esc(s.nodes)}</code>` : ''}</summary><div data-nodes></div></details>
                    <div data-card-problems aria-live="polite"></div>
                </div></li>`;
        }
        function mountStep(i) {
            const card = el.querySelector(`[data-step="${i}"]`);
            const step = model.steps[i];
            const c = { combo: null, params: null, sel: null };
            comps[i] = c;
            const paramsEl = card.querySelector('[data-params]');
            const ofHint = () => {
                const v = c.combo?.version();
                const rb = card.querySelector('[data-of="rollback"]');
                const no = v && !v.rollback;
                rb.disabled = !!no && step.on_failure !== 'rollback';
                rb.title = no ? `${v.ref} has no rollback tool` : '';
                card.querySelector('[data-of-hint]').textContent = no ? `Roll back isn’t available: ${v.ref} has no rollback tool.` : (v?.rollback ? `Roll back runs ${v.rollback} for this node.` : '');
            };
            c.combo = P.combobox(card.querySelector('[data-tool]'), {
                purpose: 'step', kinds: ['tool'], value: step.tool, label: 'Tool', id: `rb-s${i}-tool`,
                onChange: (ref, v) => {
                    step.tool = ref || '';
                    if (c.params) c.params.rebuild(v ? v.inputs : {});
                    else c.params = P.paramsForm(paramsEl, v ? v.inputs : {}, { values: step.params || {}, fillDefaults: false, expressions: true, completions: completionsFor(i), onInput: () => { step.params = c.params.read().values; markDirty(); } });
                    step.params = c.params.read().values;
                    ofHint();
                    markDirty();
                },
            });
            c.combo.ready.then(() => {
                if (!document.contains(card) || comps[i] !== c) return;
                const v = c.combo.version();
                if (!c.params) c.params = P.paramsForm(paramsEl, v ? v.inputs : { type: 'object' }, { values: step.params || {}, fillDefaults: false, expressions: true, completions: completionsFor(i), onInput: () => { step.params = c.params.read().values; markDirty(); }, emptyText: step.tool ? 'This tool takes no parameters.' : 'Choose a tool first.' });
                ofHint();
            });
            const when = card.querySelector('[data-when]');
            P.completion(when, completionsFor(i));
            when.addEventListener('input', () => { step.when = when.value; markDirty(); });
            const name = card.querySelector('[data-name]');
            name.addEventListener('input', () => { step.name = name.value.trim(); card.querySelector(`#rb-step-${i}-h`).innerHTML = `<span class="w-sr-only">Step ${i + 1}: </span>${esc(step.name || `step ${i + 1}`)}`; markDirty(); });
            card.querySelectorAll('[data-of]').forEach(b => b.addEventListener('click', () => {
                if (b.disabled) return;
                step.on_failure = b.dataset.of;
                card.querySelectorAll('[data-of]').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
                markDirty();
            }));
            const nodesEl = card.querySelector('[data-nodes]');
            const mountSel = () => {
                if (c.sel) return;
                c.sel = P.selectorBuilder(nodesEl, { value: step.nodes || '', labelsOnly: true, label: 'Node selector for this step', idPrefix: `rb-s${i}-sel`, onChange: v => { step.nodes = v || ''; markDirty(); } });
            };
            const details = card.querySelector('.orch-rb-nodes');
            if (details.open) mountSel(); else details.addEventListener('toggle', mountSel, { once: true });
        }
        function moveStep(i, delta) {
            const j = i + delta;
            if (j < 0 || j >= model.steps.length) return;
            flush();
            const [s] = model.steps.splice(i, 1);
            model.steps.splice(j, 0, s);
            paintForm();
            markDirty();
            validate();
            el.querySelector(`[data-handle="${j}"]`)?.focus();
        }
        function bindForm() {
            const title = el.querySelector('#rb-title');
            title?.addEventListener('input', () => { model.title = title.value; markDirty(); });
            const summary = el.querySelector('#rb-summary');
            summary?.addEventListener('input', () => { model.summary = summary.value; markDirty(); });
            const syncInputs = () => { model.inputs = rowsToInputs(rows, model.inputs); markDirty(); };
            el.querySelectorAll('[data-input-row]').forEach(tr => {
                const row = rows[Number(tr.dataset.inputRow)];
                tr.querySelectorAll('[data-in]').forEach(ctl => {
                    const ev = ctl.type === 'checkbox' || ctl.tagName === 'SELECT' ? 'change' : 'input';
                    ctl.addEventListener(ev, () => {
                        const f = ctl.dataset.in;
                        if (f === 'name') row.name = ctl.value.trim();
                        else if (f === 'required') row.required = ctl.checked;
                        else row.edits[f] = ctl.value;
                        syncInputs();
                    });
                });
            });
            el.querySelectorAll('[data-in-del]').forEach(b => b.addEventListener('click', () => { flush(); rows.splice(Number(b.dataset.inDel), 1); syncInputs(); paintForm(); }));
            el.querySelector('[data-in-add]')?.addEventListener('click', () => {
                flush();
                let n = rows.length + 1;
                while (rows.some(x => x.name === `input${n}`)) n += 1;
                rows.push({ name: `input${n}`, raw: { type: 'string' }, required: false, edits: {} });
                syncInputs();
                paintForm();
                el.querySelector(`[data-input-row="${rows.length - 1}"] [data-in="name"]`)?.select();
            });
            el.querySelector('[data-in-yaml]')?.addEventListener('click', async () => { await switchTab('yaml'); focusYamlKey('inputs:'); });
            el.querySelector('[data-step-add]')?.addEventListener('click', () => {
                flush();
                let n = model.steps.length + 1;
                while (model.steps.some(s => s.name === `step${n}`)) n += 1;
                model.steps.push(stepToModel({ name: `step${n}`, tool: '' }));
                paintForm();
                markDirty();
                el.querySelector(`[data-step="${model.steps.length - 1}"] .orch-combo__input`)?.focus();
            });
            el.querySelectorAll('[data-move]').forEach(b => b.addEventListener('click', () => { const [i, d] = b.dataset.move.split(':').map(Number); moveStep(i, d); }));
            el.querySelectorAll('[data-dup]').forEach(b => b.addEventListener('click', () => {
                flush();
                const i = Number(b.dataset.dup);
                const copy = stepToModel(stepToSpec(model.steps[i]));
                let name = `${model.steps[i].name || 'step'}-copy`;
                let k = 2;
                while (model.steps.some(s => s.name === name)) name = `${model.steps[i].name || 'step'}-copy${k++}`;
                copy.name = name;
                model.steps.splice(i + 1, 0, copy);
                paintForm();
                markDirty();
                el.querySelector(`[data-step="${i + 1}"] [data-name]`)?.focus();
            }));
            el.querySelectorAll('[data-del]').forEach(b => b.addEventListener('click', async () => {
                const i = Number(b.dataset.del);
                if (!await ui.confirm({ title: `Delete step ${model.steps[i].name || i + 1}?`, message: 'Steps that reference it will show a problem. You can undo by not saving.', confirmLabel: 'Delete step', tone: 'danger' })) return;
                flush();
                model.steps.splice(i, 1);
                paintForm();
                markDirty();
                validate();
            }));
            el.querySelectorAll('.orch-rb-step').forEach(card => {
                const i = Number(card.dataset.step);
                card.addEventListener('keydown', e => {
                    if (!e.altKey || (e.key !== 'ArrowUp' && e.key !== 'ArrowDown')) return;
                    if (e.target.tagName === 'SELECT' || e.target.closest('.orch-combo, .orch-complete')) return;
                    e.preventDefault();
                    moveStep(i, e.key === 'ArrowUp' ? -1 : 1);
                });
                const handle = card.querySelector('[data-handle]');
                handle.addEventListener('mousedown', () => { card.draggable = true; });
                handle.addEventListener('touchstart', () => { card.draggable = true; }, { passive: true });
                card.addEventListener('dragstart', e => { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', String(i)); card.dataset.dragging = 'true'; });
                card.addEventListener('dragend', () => { card.draggable = false; delete card.dataset.dragging; el.querySelectorAll('[data-drop-target]').forEach(x => delete x.dataset.dropTarget); });
                card.addEventListener('dragover', e => { e.preventDefault(); card.dataset.dropTarget = 'true'; });
                card.addEventListener('dragleave', () => { delete card.dataset.dropTarget; });
                card.addEventListener('drop', e => {
                    e.preventDefault();
                    const from = Number(e.dataTransfer.getData('text/plain'));
                    if (Number.isNaN(from) || from === i) return;
                    moveStep(from, i - from);
                });
            });
        }

        /* ---------------- YAML view */
        function focusYamlKey(key) {
            const ta = el.querySelector('#rb-yaml');
            if (!ta) return;
            const at = ta.value.indexOf(`\n${key}`);
            const pos = at < 0 ? (ta.value.startsWith(key) ? 0 : -1) : at + 1;
            ta.focus();
            if (pos >= 0) ta.setSelectionRange(pos, pos + key.length);
        }
        function paintYaml() {
            comps.length = 0;
            doc.innerHTML = `<div class="orch-stack-sm">
                <p class="orch-muted">The whole runbook as YAML. Switching back to Steps checks it on the server; a YAML error keeps you here.</p>
                <div class="orch-rb-yaml"><div class="orch-rb-gutter" data-gutter aria-hidden="true"></div>
                    <label class="w-visually-hidden" for="rb-yaml">runbook.yaml</label>
                    <textarea class="w-textarea orch-editor orch-rb-yaml__text" data-mono id="rb-yaml" rows="26" spellcheck="false" wrap="off" aria-describedby="rb-yaml-problems">${esc(yamlText)}</textarea></div>
                <div id="rb-yaml-problems" data-yaml-problems aria-live="polite"></div></div>`;
            const ta = el.querySelector('#rb-yaml');
            ta.addEventListener('input', () => { yamlText = ta.value; paintGutter(); markDirty(); });
            ta.addEventListener('scroll', () => { el.querySelector('[data-gutter]').scrollTop = ta.scrollTop; });
            ta.addEventListener('keydown', e => {
                if (e.key === 'Tab' && !e.shiftKey && !e.ctrlKey && !e.altKey && !e.metaKey) {
                    e.preventDefault();
                    const s = ta.selectionStart;
                    ta.setRangeText('  ', s, ta.selectionEnd, 'end');
                    ta.dispatchEvent(new Event('input'));
                }
            });
            paintGutter();
        }
        function paintGutter() {
            const gutter = el.querySelector('[data-gutter]');
            const ta = el.querySelector('#rb-yaml');
            if (!gutter || !ta) return;
            const lines = ta.value.split('\n').length;
            const byLine = new Map();
            (result?.problems || []).forEach(p => { if (p.line) { if (!byLine.has(p.line)) byLine.set(p.line, []); byLine.get(p.line).push(p); } });
            const html = [];
            for (let n = 1; n <= lines; n += 1) {
                const ps = byLine.get(n);
                const sev = ps ? (ps.some(p => (p.severity || 'error') === 'error') ? 'danger' : 'caution') : '';
                html.push(`<span class="orch-rb-gutter__line"${sev ? ` data-tone="${sev}" title="${esc(ps.map(problemLabel).join('\n'))}"` : ''}>${sev ? (sev === 'danger' ? '✗' : '◆') : n}</span>`);
            }
            gutter.innerHTML = html.join('');
            gutter.scrollTop = ta.scrollTop;
            const list = el.querySelector('[data-yaml-problems]');
            const lined = (result?.problems || []).filter(p => p.line);
            list.innerHTML = lined.length ? `<ul class="orch-rb-problems" role="list">${lined.map(p => `<li><button type="button" class="orch-rb-problem" data-tone="${p.severity === 'warning' ? 'caution' : 'danger'}" data-yline="${esc(p.line)}"><span aria-hidden="true">${p.severity === 'warning' ? '◆' : '✗'}</span> Line ${esc(p.line)}${p.column ? `:${esc(p.column)}` : ''}: ${esc(problemLabel(p))}</button></li>`).join('')}</ul>` : '';
            list.querySelectorAll('[data-yline]').forEach(b => b.addEventListener('click', () => focusPath('', Number(b.dataset.yline))));
        }

        /* ---------------- tabs */
        async function switchTab(next, { focus = false } = {}) {
            if (next === tab) return true;
            if (next === 'yaml') {
                flush();
                const res = await validate();
                yamlText = res && res.yaml ? res.yaml : yamlText;
                tab = 'yaml';
                paintYaml();
                await validate();
            } else {
                yamlText = el.querySelector('#rb-yaml')?.value ?? yamlText;
                const res = await validate();
                if (!res || !res.parses) {
                    ui.toast('Fix the YAML error first: the Steps view needs YAML that parses.', 'danger');
                    paintGutter();
                    el.querySelector('#rb-yaml')?.focus();
                    return false;
                }
                model = specToModel(Object.assign(res.spec, {}));
                rows = inputsToRows(model.inputs || {});
                tab = 'form';
                paintForm();
                validate();
            }
            el.querySelectorAll('[role="tab"]').forEach(b => { const on = b.dataset.tab === tab; b.setAttribute('aria-selected', String(on)); b.tabIndex = on ? 0 : -1; });
            el.querySelector('#orch-panel').setAttribute('aria-labelledby', `orch-tab-${tab}`);
            setQuery({ tab: tab === 'yaml' ? 'yaml' : '' });
            if (focus) el.querySelector(`#orch-tab-${tab}`)?.focus();
            return true;
        }
        ui.bindTabs(el.querySelector('.orch-studio__edit'), (next, kb) => { switchTab(next, { focus: !!kb }).then(ok => { if (!ok) el.querySelectorAll('[role="tab"]').forEach(b => b.setAttribute('aria-selected', String(b.dataset.tab === tab))); }); });

        /* ---------------- save */
        async function save(button) {
            const fromYaml = tab === 'yaml';
            const spec = fromYaml ? el.querySelector('#rb-yaml').value : (flush(), modelToSpec(model));
            const sent = edits;
            try {
                const res = await ui.busy(button, () => api.cmd('saveRunbookDraft', { spec, runbook_id: r.runbook_id, base_digest: baseDigest }));
                baseDigest = res.data.digest;
                stale = '';
                el.querySelector('[data-stale]').innerHTML = '';
                if (edits === sent) {
                    dirty = false;
                    el.querySelector('[data-dirty]').hidden = true;
                    const sv = el.querySelector('[data-save]');
                    if (sv) sv.disabled = true;
                }
                const n = (res.data.problems || []).length;
                ui.toast(n ? `Draft saved with ${ui.plural(n, 'problem')}; it can’t be submitted until they’re fixed.` : 'Draft saved', n ? 'attention' : 'ok');
                await refreshChrome();
                return true;
            } catch (err) {
                if (err.status === 409 && err.data?.conflict) { showStale('Your save was refused: this draft changed since you opened it (the AI or another tab may have saved it). Your edits are still here.'); return false; }
                ui.toast(`Couldn’t save: ${err.message}`, 'danger');
                return false;
            }
        }
        /* Re-read the saved version for the toolbar, status, tests and notes. The document is
         * never re-rendered here: edits typed while this runs stay where they are. */
        async function refreshChrome() {
            let fresh;
            try { fresh = await api.get(`runbooks/${encodeURIComponent(r.runbook_id)}/${encodeURIComponent(r.version)}`); } catch (_) { return; }
            if (disposed) return;
            if (fresh.status !== r.status) { if (!dirty) Orch.refresh(); return; }
            Object.assign(r, { actions: fresh.actions, review: fresh.review, notes: fresh.notes, tests: fresh.tests, style: fresh.style, source: fresh.source });
            el.querySelector('[data-rb-status]').innerHTML = ui.chip(r.style);
            const box = el.querySelector('[data-rb-actions]');
            box.innerHTML = ui.actionButtons(r.actions, { only: ACTION_IDS });
            ui.bindActions(box, r.actions, actionCtx);
            el.querySelector('[data-rb-tests]').innerHTML = testsHtml(r);
            el.querySelector('[data-rb-notes]').innerHTML = Orch.catalog.notesTimeline((r.notes || []).slice(-8));
            if (!r.source && !stale) el.querySelector('[data-stale]').innerHTML = '';
        }
        function showStale(message) {
            stale = message;
            const box = el.querySelector('[data-stale]');
            box.innerHTML = ui.banner('attention', '◆', esc(message), '<span class="w-btn-row"><button type="button" class="w-btn" data-variant="tonal" data-tone="attention" data-size="sm" data-reload>Reload (discard my edits)</button><button type="button" class="w-btn" data-variant="ghost" data-size="sm" data-keep>Keep mine</button></span>');
            box.querySelector('[data-reload]').addEventListener('click', () => { dirty = false; Orch.refresh(); });
            box.querySelector('[data-keep]').addEventListener('click', () => {
                box.innerHTML = ui.banner('caution', '◆', 'Keeping your edits. Saving will be refused until you reload, because the draft changed elsewhere.');
            });
        }
        el.querySelector('[data-save]')?.addEventListener('click', e => save(e.currentTarget));
        el.querySelector('[data-chat]').addEventListener('click', e => Orch.studio?.openChat('', e.currentTarget));
        el.querySelector('[data-upgrade]')?.addEventListener('click', async () => {
            flush();
            const spec = tab === 'yaml' ? el.querySelector('#rb-yaml').value : modelToSpec(model);
            await upgradeDialog({
                spec, ref: r.ref, published: false,
                onApply: upgraded => {
                    model = specToModel(upgraded);
                    rows = inputsToRows(model.inputs || {});
                    if (tab === 'yaml') { tab = 'yaml'; switchTabForce(); } else paintForm();
                    markDirty();
                    validate();
                },
            });
        });
        async function switchTabForce() { tab = 'form'; await switchTab('yaml'); }
        const guard = fn => async (...a) => {
            if (dirty) {
                const ok = await ui.confirm({ title: 'Save your changes first?', message: 'Tests and review use the saved draft. Save now and continue?', confirmLabel: 'Save and continue' });
                if (!ok || !(await save(el.querySelector('[data-save]')))) return null;
            }
            return fn(...a);
        };
        const actionCtx = {
            args: { ref: r.ref }, subject: r.ref,
            done: (_res, action) => {
                if (action.id === 'discardRunbookDraft') { dirty = false; const prev = (r.versions || []).filter(v => v.version !== r.version).pop(); Orch.go(prev ? `#/runbook/${encodeURIComponent(r.runbook_id)}/${prev.version}` : '#/studio'); return; }
                Orch.refresh();
            },
            dialogs: { test: guard(() => testDialog(r)) },
            overrides: { submitRunbook: guard(async (action) => { try { await ui.command('submitRunbook', { ref: r.ref }, { ok: `${r.ref} submitted for review`, button: el.querySelector('[data-action="submitRunbook"]') }); Orch.refresh(); } catch (_) { /* toasted */ } }) },
        };
        ui.bindActions(el, r.actions, actionCtx);

        /* ---------------- expression preview */
        const prevNode = P.nodePicker(el.querySelector('[data-prev-node]'), { label: 'Node' });
        let prevInputs = P.paramsForm(el.querySelector('[data-prev-inputs]'), model.inputs || {}, { emptyText: 'No inputs.' });
        el.querySelector('[data-prev-inputs]').closest('details').addEventListener('toggle', () => { prevInputs = P.paramsForm(el.querySelector('[data-prev-inputs]'), model.inputs || {}, { emptyText: 'No inputs.' }); });
        el.querySelector('[data-preview]').addEventListener('click', async e => {
            const out = el.querySelector('[data-prev-out]');
            flush();
            const spec = tab === 'yaml' ? el.querySelector('#rb-yaml').value : modelToSpec(model);
            const read = prevInputs.read();
            try {
                const res = (await ui.busy(e.currentTarget, () => api.cmd('validateRunbook', { spec, preview: { node_id: prevNode.value() || '', inputs: read.values } }))).data;
                const p = res.preview;
                if (!p) { out.innerHTML = `<p class="orch-error">${errText('Fix the YAML first.')}</p>`; return; }
                out.innerHTML = ui.table({ label: 'Preview', stack: false, cols: [{ label: 'Step' }, { label: 'Runs here' }, { label: 'when:' }, { label: 'Params' }],
                    rows: p.steps.map(s => [`<code class="orch-mono">${esc(s.name)}</code>`, s.nodes_error ? `<span class="orch-error">${esc(s.nodes_error)}</span>` : (s.runs_here ? 'yes' : 'no (node selector)'),
                        s.when ? (s.when.error ? `<span class="orch-error">${esc(s.when.error)}</span>` : `<code class="orch-mono">${esc(JSON.stringify(s.when.value))}</code>`) : '<span class="orch-muted">always</span>',
                        s.params_error ? `<span class="orch-error">${esc(s.params_error)}</span>` : `<code class="orch-mono orch-break">${esc(JSON.stringify(s.params))}</code>`]) });
            } catch (err) { out.innerHTML = `<p class="orch-error">${errText(err.message)}</p>`; }
        });

        /* ---------------- boot */
        if (tab === 'yaml') paintYaml(); else paintForm();
        validate();
        if (r.source) {
            el.querySelector('[data-stale]').innerHTML = ui.banner('caution', '◆', 'This draft was saved with YAML that doesn’t parse. It reopens here with your text; the last valid version is kept until it parses.');
        }
        const onBeforeUnload = e => { if (dirty) { e.preventDefault(); e.returnValue = ''; } };
        window.addEventListener('beforeunload', onBeforeUnload);
        controller = {
            async onChange(change) {
                if (disposed || change.scope !== 'tool' || (change.id && change.id !== r.runbook_id)) return;
                let fresh;
                try { fresh = await api.get(`runbooks/${encodeURIComponent(r.runbook_id)}/${encodeURIComponent(r.version)}`); } catch (_) { if (!dirty) Orch.refresh(); return; }
                if (fresh.digest === baseDigest && fresh.status === r.status) { refreshChrome(); return; }   // our own save, or new test results
                if (!dirty) { Orch.refresh(); return; }
                showStale('This draft changed elsewhere (the AI may have updated it). Your unsaved edits are kept.');
            },
            dispose() { disposed = true; window.removeEventListener('beforeunload', onBeforeUnload); clearInterval(ageTimer); inflight?.abort(); },
        };
        return controller;
    }

    function testsHtml(r) {
        const tests = ((r.review || {}).tests || []).slice().reverse();
        if (!tests.length) return '<p class="orch-muted">Not tested yet. Test the draft on a canary in check mode before submitting it.</p>';
        return tests.map(t => `<article class="w-card orch-card orch-test" data-state="${t.passed ? 'ok' : 'danger'}">
            <header class="w-card__head">${ui.chip(t.passed ? { semantic: 'ok', glyph: '✓', label: 'Passed' } : { semantic: 'danger', glyph: '✗', label: 'Failed' })}${t.check_mode ? ui.meta('check mode') : ui.meta('real run')}<span class="orch-spacer"></span><span class="w-card__meta">${ui.time(t.at)} · <a href="#/run/${encodeURIComponent(t.run_id)}">run</a></span></header>
            <div class="w-card__body">${t.digest && t.digest !== r.digest ? '<p class="orch-sub"><span aria-hidden="true">◆</span> Ran against an earlier revision of this draft.</p>' : ''}
            ${ui.table({ label: 'Test results', stack: false, cols: [{ label: 'Node' }, { label: 'Result' }, { label: 'Steps' }, { label: 'Problems' }],
                rows: (t.results || []).map(x => [`<a class="orch-mono" href="#/node/${encodeURIComponent(x.node_id)}">${esc(x.node_id)}</a>`, x.passed ? ui.chip({ semantic: 'ok', glyph: '✓', label: 'Passed' }, { sm: true }) : ui.chip({ semantic: 'danger', glyph: '✗', label: 'Failed' }, { sm: true }),
                    (x.steps || []).map(s => `<span class="orch-nowrap">${esc(s.name)}: ${esc(s.state)}</span>`).join('<br>') || '—', (x.problems || []).map(esc).join('<br>') || '<span class="orch-muted">—</span>']) })}</div></article>`).join('');
    }

    Orch.runbooks = { ...pure, renderEditor, renderNew, newRunbook, testDialog, upgradeDialog, testsHtml, editorHref };
})();
