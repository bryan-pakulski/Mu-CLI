/* Orchestration — dynamic pickers (documentation/plans/runbook_editor.md §4.1).
 *
 * One set of components for everywhere a target or input is chosen: the
 * runbook editor's step cards, the run wizard, the schedule form, the tool
 * and runbook test dialogs and the bulk-upgrade dialog. Nothing is baked into
 * the page: options come from GET /api/orchestration/catalog/options and
 * /labels and refresh when the change stream reports a `tool` or `node`
 * change (Orch.subscribe).
 *
 *   combobox(el, {purpose, kinds, value, label, onChange})   tool/runbook + version
 *   paramsForm(el, schema, {values, fillDefaults, expressions, completions})
 *   completion(input, source)                                 expression completion
 *   selectorBuilder(el, {value, test, labelsOnly, onChange})  labels, groups, nodes + live count
 *   nodePicker(el, {multi, test, value, onChange})
 *
 * Fields validate as you type with OrchSchema (the node's schema subset);
 * the server's planRun / validateRunbook stay the authority.
 */
(() => {
    'use strict';
    const api = window.OrchAPI;
    const ui = window.OrchUI;
    const S = window.OrchSchema;
    const { esc, icon } = ui;
    const Orch = (window.Orch = window.Orch || { routes: {} });

    /* ------------------------------------------------------------ live data */
    const cache = { options: new Map(), labels: null };
    const watchers = new Set();
    let hooked = false;
    const pending = {};
    function hook() {
        if (hooked || !Orch.subscribe) return;
        hooked = true;
        Orch.subscribe(change => {
            if (change.scope === 'tool') { cache.options.clear(); notify('tool'); }
            if (change.scope === 'node') { cache.labels = null; notify('node'); }
        });
    }
    function notify(scope) {
        clearTimeout(pending[scope]);
        pending[scope] = setTimeout(() => {
            watchers.forEach(w => {
                if (w.el && !document.contains(w.el)) { watchers.delete(w); return; }
                if (w.scope === scope) w.fn();
            });
        }, 250);
    }
    /* Run fn on every `tool` or `node` change while el is in the page. */
    function watch(scope, el, fn) { hook(); const w = { scope, el, fn }; watchers.add(w); return () => watchers.delete(w); }
    function options(purpose = 'run') {
        hook();
        if (!cache.options.has(purpose)) {
            cache.options.set(purpose, api.get(`catalog/options?purpose=${encodeURIComponent(purpose)}`).catch(err => { cache.options.delete(purpose); throw err; }));
        }
        return cache.options.get(purpose);
    }
    function labels() {
        hook();
        if (!cache.labels) cache.labels = api.get('labels').catch(err => { cache.labels = null; throw err; });
        return cache.labels;
    }
    const splitRef = ref => { const m = /^(.+)@(\d+)$/.exec(String(ref || '')); return m ? { id: m[1], version: Number(m[2]) } : null; };
    const errLine = (text, id) => `<p class="w-field__error" ${id ? `id="${esc(id)}" ` : ''}data-err role="alert">${text ? `<span aria-hidden="true">✗</span> ${esc(text)}` : ''}</p>`;

    /* ------------------------------------------------------------ listbox keyboard (shared) */
    /* A popup listbox driven from a text input (ARIA combobox): arrows move, Enter
     * or Tab (completion only) picks, Escape closes, Home/End jump.
     *
     * Cards and dialog bodies clip their content (overflow: hidden / auto), so while
     * open the list is lifted out of its container — into the open dialog's scrim or
     * the page body — and placed against the viewport under the input (above it when
     * there's more room there). Closing puts it back next to its input. */
    const GAP = 4;
    function place(input, list) {
        if (!input.isConnected) { list.remove(); return; }
        const r = input.getBoundingClientRect();
        const below = window.innerHeight - r.bottom - GAP * 2;
        const above = r.top - GAP * 2;
        const up = below < 200 && above > below;
        list.style.position = 'fixed';
        list.style.left = `${Math.max(GAP, Math.min(r.left, window.innerWidth - Math.max(r.width, 280) - GAP))}px`;
        list.style.width = `${Math.max(r.width, 280)}px`;
        list.style.right = 'auto';
        list.style.maxHeight = `${Math.max(120, Math.min(360, up ? above : below))}px`;
        if (up) { list.style.top = 'auto'; list.style.bottom = `${window.innerHeight - r.top + GAP}px`; }
        else { list.style.bottom = 'auto'; list.style.top = `${r.bottom + GAP}px`; }
    }
    function popup(input, list, { onPick, tabPicks = false }) {
        let active = -1;
        const home = list.parentElement;
        const reposition = () => { if (!list.hidden) place(input, list); };
        const items = () => [...list.querySelectorAll('[role="option"]:not([aria-disabled="true"])')];
        const open = () => !list.hidden;
        const show = on => {
            const was = !list.hidden;
            list.hidden = !on;
            input.setAttribute('aria-expanded', String(on));
            if (on) {
                const host = input.closest('.w-modal') || document.body;
                if (list.parentElement !== host) host.append(list);
                list.dataset.floating = '';
                place(input, list);
                if (!was) { window.addEventListener('scroll', reposition, true); window.addEventListener('resize', reposition); }
            } else {
                active = -1;
                input.removeAttribute('aria-activedescendant');
                window.removeEventListener('scroll', reposition, true);
                window.removeEventListener('resize', reposition);
                delete list.dataset.floating;
                if (home && list.parentElement !== home) home.append(list);
            }
        };
        const move = i => {
            const all = items();
            if (!all.length) return;
            active = (i + all.length) % all.length;
            all.forEach((el, j) => el.setAttribute('aria-selected', String(j === active)));
            input.setAttribute('aria-activedescendant', all[active].id);
            all[active].scrollIntoView({ block: 'nearest' });
        };
        input.addEventListener('keydown', e => {
            if (e.key === 'ArrowDown') { e.preventDefault(); if (!open()) { input.dispatchEvent(new CustomEvent('popup-open')); } move(active + 1); }
            else if (e.key === 'ArrowUp') { if (!open()) return; e.preventDefault(); move(active - 1); }
            else if ((e.key === 'Home' || e.key === 'End') && open() && e.altKey) { e.preventDefault(); move(e.key === 'Home' ? 0 : items().length - 1); }
            else if (e.key === 'Enter' && open() && active >= 0) { e.preventDefault(); onPick(items()[active]); }
            else if (e.key === 'Tab' && tabPicks && open() && active >= 0) { e.preventDefault(); onPick(items()[active]); }
            else if (e.key === 'Escape' && open()) { e.preventDefault(); e.stopPropagation(); show(false); }
        });
        list.addEventListener('mousedown', e => {
            const opt = e.target.closest('[role="option"]');
            if (!opt || opt.getAttribute('aria-disabled') === 'true') return;
            e.preventDefault();
            onPick(opt);
        });
        input.addEventListener('blur', () => setTimeout(() => { if (document.activeElement !== input) show(false); }, 120));
        return { show, move, reset: () => { active = -1; input.removeAttribute('aria-activedescendant'); } };
    }

    /* ------------------------------------------------------------ tool / runbook combobox */
    function combobox(container, { purpose = 'run', kinds = ['tool'], value = '', label = 'Tool', hint = '', onChange = null, id = ui.uid('pick') } = {}) {
        container.innerHTML = `
            <div class="orch-pick">
                <div class="w-field orch-pick__main"><label class="w-field__label" for="${id}">${esc(label)}</label>
                    <div class="orch-combo">
                        <input class="w-input orch-combo__input" id="${id}" role="combobox" aria-autocomplete="list" aria-expanded="false" aria-controls="${id}-list" aria-describedby="${id}-info" autocomplete="off" spellcheck="false" placeholder="Search by id, title, summary or category">
                        <span class="orch-combo__chevron" aria-hidden="true">${icon('chevron-down', 'sm')}</span>
                        <ul class="orch-combo__list" id="${id}-list" role="listbox" aria-label="${esc(label)}" hidden></ul>
                    </div>${hint ? `<p class="w-field__hint">${esc(hint)}</p>` : ''}</div>
                <div class="w-field orch-pick__version"><label class="w-field__label" for="${id}-v">Version</label><span class="w-select"><select class="w-select__control" id="${id}-v" disabled><option value="">—</option></select></span></div>
            </div>
            <div class="orch-pick__info" id="${id}-info" data-info></div>`;
        const input = container.querySelector(`#${id}`);
        const list = container.querySelector(`#${id}-list`);
        const verSel = container.querySelector(`#${id}-v`);
        const info = container.querySelector('[data-info]');
        let entries = [];
        let entry = null;
        let ref = '';
        let wanted = splitRef(value);
        let loadError = '';

        const textOf = e => (e ? `${e.title} (${e.id})` : '');
        const version = () => (entry ? (entry.versions || []).find(v => v.ref === ref) || null : null);
        function paintInfo() {
            const v = version();
            if (loadError) { info.innerHTML = `<p class="orch-error">${esc(loadError)}</p>`; return; }
            if (!entry) {
                info.innerHTML = wanted ? ui.banner('caution', '◆', `<strong>${esc(`${wanted.id}@${wanted.version}`)}</strong> isn’t available for this purpose any more (retired, unpublished or deleted). Choose another.`) : '';
                return;
            }
            const bits = [];
            if (v) {
                bits.push(ui.effectChip(v.effect_style, v.effect));
                if (v.status !== 'published') bits.push(ui.chip(v.style, { sm: true }));
                if (v.check_mode) bits.push(ui.meta('supports check mode'));
                if (v.rollback) bits.push(ui.meta(`rollback ${v.rollback}`));
                if ((v.preconditions || []).length) bits.push(ui.meta(`preconditions run first: ${v.preconditions.join(', ')}`));
            }
            const warn = [];
            if (v && v.status === 'deprecated') warn.push(ui.banner('caution', '◆', `${esc(v.ref)} is deprecated: runs refuse it until it is restored.`));
            if (!v && ref) warn.push(ui.banner('caution', '◆', `${esc(ref)} isn’t available any more. Choose a version.`));
            const newer = entry.latest && v && v.version < entry.latest && v.status !== 'draft';
            const upd = newer ? `<p class="orch-pick__update">v${esc(entry.latest)} is available. <button type="button" class="w-textbtn" data-use-latest>Use ${esc(`${entry.id}@${entry.latest}`)}</button></p>` : '';
            info.innerHTML = `${bits.length ? `<div class="orch-head__meta">${bits.join('')}</div>` : ''}${entry.summary ? `<p class="orch-muted orch-clamp">${esc(entry.summary)}</p>` : ''}${upd}${warn.join('')}`;
            info.querySelector('[data-use-latest]')?.addEventListener('click', () => choose(entry, `${entry.id}@${entry.latest}`, true));
        }
        function fillVersions() {
            const vs = entry ? entry.versions || [] : [];
            const extra = ref && !vs.some(v => v.ref === ref) ? `<option value="${esc(ref)}">v${esc(splitRef(ref)?.version)} · not available</option>` : '';
            verSel.innerHTML = vs.map(v => `<option value="${esc(v.ref)}">v${esc(v.version)}${v.status !== 'published' ? ` · ${esc(v.style?.label || v.status)}${v.status === 'deprecated' ? ' ⚠' : ''}` : ''}${entry.latest === v.version && v.status === 'published' ? ' · latest' : ''}</option>`).join('') + extra || '<option value="">—</option>';
            verSel.disabled = !vs.length;
            verSel.value = ref;
        }
        function choose(e, chosenRef, notifyChange = true) {
            entry = e;
            ref = chosenRef || (e ? e.default : '');
            wanted = splitRef(ref);
            input.value = textOf(e);
            fillVersions();
            paintInfo();
            pop.show(false);
            if (notifyChange) onChange?.(ref, version(), entry);
        }
        function paintList(query) {
            const q = String(query || '').trim().toLowerCase();
            const terms = q.split(/\s+/).filter(Boolean);
            const matches = entries.filter(e => !terms.length || terms.every(t => [e.id, e.title, e.summary, e.category].join(' ').toLowerCase().includes(t)));
            const groups = new Map();
            matches.forEach(e => { const c = e.kind === 'runbook' ? 'runbooks' : (e.category || 'other'); if (!groups.has(c)) groups.set(c, []); groups.get(c).push(e); });
            if (!matches.length) { list.innerHTML = `<li class="orch-combo__empty" role="presentation">${esc(entries.length ? 'Nothing matches.' : 'Nothing available yet.')}</li>`; return; }
            list.innerHTML = [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([cat, es], gi) => `<li role="group" aria-labelledby="${id}-g${gi}"><span class="orch-combo__group" id="${id}-g${gi}" role="presentation">${esc(ui.cap(cat))}</span><ul role="none" class="orch-combo__options">${es.map(e => {
                const head = (e.versions || []).find(v => v.ref === e.default) || {};
                return `<li role="option" class="orch-combo__option" id="${id}-o-${esc(e.kind)}-${esc(e.id.replace(/[^a-z0-9_-]/gi, '_'))}" data-entry="${esc(`${e.kind}:${e.id}`)}" aria-selected="false">
                    <span class="orch-combo__title">${esc(e.title)}</span><span class="orch-combo__meta">${ui.effectChip(e.effect_style, e.effect)}<span class="orch-mono">${esc(e.default)}</span>${head.status && head.status !== 'published' ? ui.chip(head.style, { sm: true }) : ''}${e.latest && head.version && head.version !== e.latest ? `<span class="orch-sub">latest v${esc(e.latest)}</span>` : ''}</span>
                    ${e.summary ? `<span class="orch-combo__summary">${esc(e.summary)}</span>` : ''}</li>`;
            }).join('')}</ul></li>`).join('');
            pop.reset();
        }
        const pop = popup(input, list, {
            onPick: opt => {
                const [kind, eid] = opt.dataset.entry.split(/:(.*)/s);
                const e = entries.find(x => x.kind === kind && x.id === eid);
                if (e) choose(e);
            },
        });
        input.addEventListener('popup-open', () => { paintList(''); pop.show(true); });
        input.addEventListener('focus', () => input.select());
        input.addEventListener('click', () => { if (list.hidden) { paintList(input.value === textOf(entry) ? '' : input.value); pop.show(true); } });
        input.addEventListener('input', () => { paintList(input.value); pop.show(true); });
        input.addEventListener('blur', () => setTimeout(() => { if (document.activeElement !== input && entry) input.value = textOf(entry); }, 150));
        verSel.addEventListener('change', () => { ref = verSel.value; wanted = splitRef(ref); paintInfo(); onChange?.(ref, version(), entry); });

        async function load() {
            try {
                const data = await options(purpose);
                loadError = '';
                entries = [...(kinds.includes('tool') ? data.tools : []), ...(kinds.includes('runbook') ? data.runbooks : [])];
            } catch (err) { loadError = `Couldn’t load the catalog: ${err.message}`; entries = []; }
            const keep = wanted ? `${wanted.id}@${wanted.version}` : ref;
            const found = wanted ? entries.find(e => e.id === wanted.id) : null;
            entry = found || null;
            ref = found ? (keep || found.default) : keep;
            if (document.activeElement !== input) input.value = entry ? textOf(entry) : (ref ? ref : '');
            fillVersions();
            paintInfo();
            if (!list.hidden) paintList(input.value);
        }
        const ready = load();
        watch('tool', container, load);
        return {
            ready,
            value: () => ref,
            version,
            entry: () => entry,
            set(next) { wanted = splitRef(next); ref = next || ''; const e = wanted ? entries.find(x => x.id === wanted.id) : null; if (e) choose(e, next, false); else { entry = null; input.value = next || ''; fillVersions(); paintInfo(); } },
            focus: () => input.focus(),
            reload: load,
            input,
        };
    }

    /* ------------------------------------------------------------ expression completion */
    const TOKEN = /[A-Za-z0-9_.\[\]'"-]*$/;
    function completion(input, source) {
        if (input.dataset.completion) return;
        input.dataset.completion = '1';
        const id = input.id || (input.id = ui.uid('expr'));
        const wrap = document.createElement('div');
        wrap.className = 'orch-complete';
        input.replaceWith(wrap);
        wrap.append(input);
        const list = document.createElement('ul');
        list.className = 'orch-combo__list orch-complete__list';
        list.id = `${id}-complete`;
        list.setAttribute('role', 'listbox');
        list.setAttribute('aria-label', 'Completions');
        list.hidden = true;
        wrap.append(list);
        input.setAttribute('role', 'combobox');
        input.setAttribute('aria-autocomplete', 'list');
        input.setAttribute('aria-expanded', 'false');
        input.setAttribute('aria-controls', list.id);
        const token = () => (TOKEN.exec(input.value.slice(0, input.selectionStart ?? input.value.length)) || [''])[0];
        const paint = force => {
            const t = token();
            const items = (source() || []).filter(x => (t ? x.value.startsWith(t) && x.value !== t : force)).slice(0, 14);
            if (!items.length || (!t && !force)) { pop.show(false); return; }
            list.innerHTML = items.map((x, i) => `<li role="option" class="orch-combo__option orch-complete__option" id="${id}-c${i}" data-value="${esc(x.value)}" aria-selected="false"><span class="orch-mono">${esc(x.value)}</span>${x.detail ? `<span class="orch-sub">${esc(x.detail)}</span>` : ''}</li>`).join('');
            pop.reset();
            pop.show(true);
            pop.move(0);   // like an editor: the first suggestion is ready for Tab or Enter
        };
        const pop = popup(input, list, {
            tabPicks: true,
            onPick: opt => {
                const caret = input.selectionStart ?? input.value.length;
                const before = input.value.slice(0, caret).replace(TOKEN, '');
                input.value = before + opt.dataset.value + input.value.slice(caret);
                const at = (before + opt.dataset.value).length;
                input.setSelectionRange(at, at);
                pop.show(false);
                input.dispatchEvent(new Event('input', { bubbles: true }));
            },
        });
        input.addEventListener('popup-open', () => paint(true));
        input.addEventListener('input', e => { if (e.isTrusted !== false) paint(false); });
        input.addEventListener('keydown', e => { if (e.key === ' ' && e.ctrlKey) { e.preventDefault(); paint(true); } });
    }

    /* ------------------------------------------------------------ params form */
    const isTemplate = v => typeof v === 'string' && /\$\{\{/.test(v);
    const wholeTemplate = v => { const m = typeof v === 'string' ? /^\s*\$\{\{\s*([\s\S]*?)\s*\}\}\s*$/.exec(v) : null; return m ? m[1] : null; };
    function cons(p) {
        return [p.minimum != null ? `min ${p.minimum}` : '', p.maximum != null ? `max ${p.maximum}` : '', p.minLength != null ? `min length ${p.minLength}` : '',
            p.maxLength != null ? `max length ${p.maxLength}` : '', p.pattern ? `pattern ${p.pattern}` : '', p.maxItems != null ? `≤ ${p.maxItems} items` : ''].filter(Boolean).join(' · ');
    }
    const short = v => { const s = JSON.stringify(v); return s.length > 60 ? `${s.slice(0, 57)}…` : s; };

    /* opts.values: initial values (literals or ${{ }} templates); fillDefaults: pre-fill schema
     * defaults (runs) or leave optional fields empty = "default at run time" (runbook steps);
     * expressions: per-field literal/expression toggle with completion from opts.completions(). */
    function paramsForm(container, schema, opts = {}) {
        const { fillDefaults = true, expressions = false, completions = null, onInput = null, emptyText = 'No parameters.' } = opts;
        const pid = ui.uid('param');
        let current = schema || {};
        let fields = [];          // {name, prop, kind, mode, original, touched, el}
        let rejected = [];        // {name, value, reason}
        let extras = {};          // values for names the schema doesn't declare but accepts

        function control(f, i) {
            const id = `${pid}-${i}`;
            const p = f.prop || {};
            const def = p.default;
            const val = f.mode === 'expr' ? null : f.value;
            const has = val !== undefined && val !== null;
            if (f.mode === 'expr') return `<input class="w-input orch-expr" data-mono id="${id}" data-param value="${esc(f.expr || '')}" placeholder="steps.check.outputs.free_pct" autocomplete="off" spellcheck="false" aria-describedby="${id}-hint ${id}-err">`;
            switch (f.kind) {
            case 'enum': return `<span class="w-select"><select class="w-select__control" id="${id}" data-param aria-describedby="${id}-hint ${id}-err">${f.required ? '' : `<option value="">${fillDefaults ? 'None' : `Default${def !== undefined ? ` (${esc(short(def))})` : ''}`}</option>`}${(p.enum || []).map(v => `<option value="${esc(JSON.stringify(v))}"${has && JSON.stringify(v) === JSON.stringify(val) ? ' selected' : ''}>${esc(typeof v === 'string' ? v : JSON.stringify(v))}</option>`).join('')}</select></span>`;
            case 'boolean': return `<span class="w-select"><select class="w-select__control" id="${id}" data-param aria-describedby="${id}-hint ${id}-err">${f.required && fillDefaults ? '' : `<option value="">${fillDefaults ? 'Not set' : `Default${def !== undefined ? ` (${def ? 'yes' : 'no'})` : ''}`}</option>`}<option value="true"${val === true ? ' selected' : ''}>Yes</option><option value="false"${val === false ? ' selected' : ''}>No</option></select></span>`;
            case 'integer': case 'number': return `<input class="w-input" type="number" id="${id}" data-param${p.minimum != null ? ` min="${esc(p.minimum)}"` : ''}${p.maximum != null ? ` max="${esc(p.maximum)}"` : ''} step="${f.kind === 'integer' ? 1 : 'any'}" value="${has && typeof val === 'number' ? esc(val) : (has ? esc(String(val)) : '')}" placeholder="${def !== undefined ? esc(def) : ''}" aria-describedby="${id}-hint ${id}-err">`;
            case 'list': return `<textarea class="w-textarea" data-mono rows="3" id="${id}" data-param placeholder="One per line${def ? `: ${esc((def || []).join(', '))}` : ''}" aria-describedby="${id}-hint ${id}-err">${has && Array.isArray(val) ? esc(val.join('\n')) : (has ? esc(JSON.stringify(val)) : '')}</textarea>`;
            case 'string': return `<input class="w-input" id="${id}" data-param value="${has ? esc(typeof val === 'string' ? val : JSON.stringify(val)) : ''}" placeholder="${def !== undefined ? esc(def) : ''}" autocomplete="off" spellcheck="false" aria-describedby="${id}-hint ${id}-err">`;
            default: return `<textarea class="w-textarea" data-mono rows="3" id="${id}" data-param placeholder="JSON${def !== undefined ? `: ${esc(short(def))}` : ''}" aria-describedby="${id}-hint ${id}-err">${has ? esc(JSON.stringify(val, null, 2)) : ''}</textarea>`;
            }
        }

        function paint() {
            if (!fields.length) { container.innerHTML = `<p class="orch-muted">${esc(emptyText)}</p>${rejectedHtml()}`; bindRejected(); return; }
            container.innerHTML = `<div class="orch-form-grid orch-params">${fields.map((f, i) => {
                const id = `${pid}-${i}`;
                const p = f.prop || {};
                const hint = [p.description || '', cons(p), !fillDefaults && p.default !== undefined && f.mode !== 'expr' ? `default ${short(p.default)}` : '', f.extra ? 'not declared by the schema' : '', f.mode === 'expr' ? 'Resolved on each node at run time' : ''].filter(Boolean).join(' · ');
                const toggle = expressions ? `<button type="button" class="w-btn orch-expr-toggle" data-variant="ghost" data-size="sm" data-toggle="${i}" aria-pressed="${f.mode === 'expr'}" title="${f.mode === 'expr' ? 'Use a literal value' : 'Use an expression'}" aria-label="${f.mode === 'expr' ? `Use a literal value for ${esc(f.name)}` : `Use an expression for ${esc(f.name)}`}"><span aria-hidden="true">\${{ }}</span></button>` : '';
                return `<div class="w-field orch-param" data-field="${i}"><div class="orch-param__label"><label class="w-field__label" for="${id}"><span class="orch-mono">${esc(f.name)}</span>${f.required ? ' <span class="orch-req" title="required">required</span>' : ' <span class="orch-muted">(optional)</span>'}</label>${toggle}</div>
                    ${control(f, i)}<p class="w-field__hint" id="${id}-hint">${esc(hint)}</p>${errLine('', `${id}-err`)}</div>`;
            }).join('')}</div>${rejectedHtml()}`;
            fields.forEach((f, i) => {
                f.el = container.querySelector(`#${pid}-${i}`);
                f.el.addEventListener('input', () => { f.touched = true; check(f); onInput?.(); });
                f.el.addEventListener('change', () => { f.touched = true; check(f); onInput?.(); });
                if (f.mode === 'expr' && completions) completion(f.el, completions);
                if (f.touched || f.showErrors) check(f);
            });
            container.querySelectorAll('[data-toggle]').forEach(b => b.addEventListener('click', () => {
                const f = fields[Number(b.dataset.toggle)];
                const raw = readField(f);
                if (f.mode === 'expr') { f.mode = 'literal'; f.value = undefined; }
                else { f.mode = 'expr'; f.expr = raw.ok && raw.value !== undefined && !isTemplate(raw.value) ? '' : (wholeTemplate(raw.value) ?? ''); }
                f.touched = true;
                paint();
                container.querySelector(`#${pid}-${Number(b.dataset.toggle)}`)?.focus();
                onInput?.();
            }));
            bindRejected();
        }
        function rejectedHtml() {
            if (!rejected.length) return '';
            return `<div class="orch-rejected" role="group" aria-label="Values the new schema rejects"><p class="orch-label-text">Values the selected version rejects</p><ul class="orch-rejected__list">${rejected.map((r, i) => `<li><code class="orch-mono">${esc(r.name)}: ${esc(short(r.value))}</code> <span class="orch-error">${esc(r.reason)}</span> <button type="button" class="w-btn" data-variant="ghost" data-size="sm" data-drop="${i}">${icon('x', 'sm')}<span>Remove</span></button></li>`).join('')}</ul></div>`;
        }
        function bindRejected() {
            container.querySelectorAll('[data-drop]').forEach(b => b.addEventListener('click', () => {
                const r = rejected[Number(b.dataset.drop)];
                rejected.splice(Number(b.dataset.drop), 1);
                const f = fields.find(x => x.name === r.name);
                if (f) { f.value = undefined; f.expr = ''; f.mode = 'literal'; f.touched = true; f.original = undefined; }
                delete extras[r.name];
                paint();
                onInput?.();
            }));
        }

        /* {ok, value | undefined (empty), error} for one field. */
        function readField(f) {
            if (!f.el) return { ok: true, value: f.original };
            if (!f.touched && f.original !== undefined) return { ok: true, value: f.original };
            if (f.mode === 'expr') {
                const t = f.el.value.trim();
                return t ? { ok: true, value: `\${{ ${t} }}` } : { ok: true, value: undefined };
            }
            const raw = f.el.value;
            const text = raw.trim();
            if (text === '') return { ok: true, value: undefined };
            switch (f.kind) {
            case 'enum': try { return { ok: true, value: JSON.parse(text) }; } catch (_) { return { ok: true, value: text }; }
            case 'boolean': return { ok: true, value: text === 'true' };
            case 'integer': case 'number': {
                if (isTemplate(text)) return { ok: true, value: text };
                const n = Number(text);
                if (Number.isNaN(n)) return { ok: false, error: `${f.name}: expected ${f.kind}` };
                return { ok: true, value: n };
            }
            case 'list': return { ok: true, value: text.split('\n').map(s => s.trim()).filter(Boolean) };
            case 'string': return { ok: true, value: raw };
            default:
                if (isTemplate(text)) return { ok: true, value: text };
                try { return { ok: true, value: JSON.parse(text) }; } catch (_) { return { ok: false, error: `${f.name}: not valid JSON` }; }
            }
        }
        function problemsOf(f, r) {
            if (!r.ok) return [r.error];
            if (r.value === undefined) return f.required && !('default' in (f.prop || {})) ? [`${f.name} is required`] : [];
            if (isTemplate(r.value) || f.extra) return [];
            return S ? S.validate(r.value, f.prop || {}, f.name) : [];
        }
        function check(f) {
            if (!f.el) return [];
            const errs = problemsOf(f, readField(f));
            const p = container.querySelector(`#${f.el.id}-err`);
            if (p) p.innerHTML = errs.length ? `<span aria-hidden="true">✗</span> ${esc(errs[0])}` : '';
            if (errs.length) f.el.setAttribute('aria-invalid', 'true'); else f.el.removeAttribute('aria-invalid');
            return errs;
        }

        function build(schemaIn, values) {
            current = schemaIn || {};
            const props = current.properties || {};
            const required = new Set(current.required || []);
            const extra = current.additionalProperties;
            const next = [];
            rejected = [];
            extras = {};
            Object.entries(props).forEach(([name, prop]) => {
                const given = values[name];
                const f = { name, prop: prop || {}, kind: S ? S.kindOf(prop) : 'string', required: required.has(name), mode: 'literal', touched: false, original: undefined, value: undefined, expr: '' };
                if (given !== undefined) {
                    const tmpl = wholeTemplate(given);
                    if (tmpl !== null && (expressions || f.kind !== 'string')) { f.mode = expressions ? 'expr' : 'literal'; f.expr = tmpl; f.value = given; f.original = given; }
                    else {
                        const errs = isTemplate(given) ? [] : (S ? S.validate(given, f.prop, name) : []);
                        f.value = given;
                        f.original = given;
                        if (errs.length) { rejected.push({ name, value: given, reason: errs[0] }); f.showErrors = true; }
                    }
                } else if (fillDefaults && 'default' in (prop || {})) {
                    f.value = prop.default;
                }
                next.push(f);
            });
            Object.entries(values).forEach(([name, value]) => {
                if (name in props) return;
                if (extra === false) rejected.push({ name, value, reason: 'not a parameter of this version' });
                else {
                    next.push({ name, prop: (extra && typeof extra === 'object') ? extra : {}, kind: 'json', required: false, mode: wholeTemplate(value) !== null && expressions ? 'expr' : 'literal', expr: wholeTemplate(value) || '', touched: false, original: value, value, extra: true });
                }
            });
            fields = next;
            paint();
        }

        function read() {
            const values = {};
            const errors = [];
            fields.forEach(f => {
                const r = readField(f);
                const errs = problemsOf(f, r);
                if (f.el && (f.touched || errs.length)) check(f);
                errors.push(...errs);
                if (r.ok && r.value !== undefined) values[f.name] = r.value;
            });
            rejected.forEach(r => { if (!fields.some(f => f.name === r.name)) { values[r.name] = r.value; errors.push(`${r.name}: ${r.reason}`); } });
            return { values, errors };
        }

        build(schema, { ...(opts.values || {}) });
        return {
            read,
            /* Rebuild for another version's schema: compatible values are kept, others listed. */
            rebuild(nextSchema) { const { values } = read(); build(nextSchema, values); return { rejected: rejected.slice() }; },
            setValues(values) { build(current, values || {}); },
            rejected: () => rejected.slice(),
            focusField(name) { const f = fields.find(x => x.name === name); f?.el?.focus(); return !!f; },
            showErrors() { fields.forEach(f => check(f)); },
        };
    }

    /* ------------------------------------------------------------ selectors */
    const OPS = [['eq', '='], ['ne', '≠'], ['in', 'in'], ['notin', 'not in'], ['exists', 'exists'], ['absent', 'absent']];
    const KEY_RE = /^[A-Za-z0-9][A-Za-z0-9_./-]{0,62}$/;
    function splitTerms(text) {
        const out = []; let depth = 0; let cur = '';
        for (const ch of String(text || '')) {
            if (ch === '(') depth += 1; else if (ch === ')') depth -= 1;
            if (ch === ',' && depth === 0) { out.push(cur.trim()); cur = ''; } else cur += ch;
        }
        out.push(cur.trim());
        return out.filter(Boolean);
    }
    /* Parse the label-selector grammar of mu/orchestration/selectors.py into rows; null if it can't. */
    function parseSelector(text) {
        const t = String(text || '').trim();
        if (!t || t === '*') return [];
        const rows = [];
        for (const part of splitTerms(t)) {
            let m;
            if ((m = /^([^\s!=]+)\s+(notin|in)\s*\(([^)]*)\)$/.exec(part))) rows.push({ op: m[2], key: m[1], values: m[3].split(',').map(s => s.trim()).filter(Boolean) });
            else if ((m = /^([^\s!=]+)\s*!=\s*(\S+)$/.exec(part))) rows.push({ op: 'ne', key: m[1], values: [m[2]] });
            else if ((m = /^([^\s!=]+)\s*==?\s*(\S+)$/.exec(part))) rows.push({ op: 'eq', key: m[1], values: [m[2]] });
            else if ((m = /^!\s*(\S+)$/.exec(part))) rows.push({ op: 'absent', key: m[1], values: [] });
            else if (KEY_RE.test(part)) rows.push({ op: 'exists', key: part, values: [] });
            else return null;
        }
        return rows.every(r => KEY_RE.test(r.key)) ? rows : null;
    }
    function formatSelector(rows) {
        return rows.filter(r => r.key).map(r => {
            if (r.op === 'exists') return r.key;
            if (r.op === 'absent') return `!${r.key}`;
            if (r.op === 'in' || r.op === 'notin') return r.values.length ? `${r.key} ${r.op} (${r.values.join(',')})` : '';
            if (!r.values[0]) return '';
            return `${r.key}${r.op === 'ne' ? '!=' : '='}${r.values[0]}`;
        }).filter(Boolean).join(',');
    }

    /* value: {labels, groups, nodes} (or a label-selector string with labelsOnly). */
    /* `emptyNote` (labelsOnly): what an empty selector means here, e.g. a group's (matches no
     * node, hand-picked members only); with it, an empty selector isn't previewed as "every node". */
    function selectorBuilder(container, { value = {}, test = false, labelsOnly = false, label = 'Target nodes', onChange = null, idPrefix = ui.uid('sel'), allowAll = false, emptyNote = '' } = {}) {
        let sel = typeof value === 'string' ? { labels: value } : { labels: value.labels || '', groups: [...(value.groups || [])], nodes: [...(value.nodes || [])], all: !!value.all };
        let rows = parseSelector(sel.labels);
        let mode = rows === null ? 'text' : 'build';
        if (rows === null) rows = [];
        let index = { labels: {}, groups: [], nodes: [] };
        let previewSeq = 0;
        const id = idPrefix;

        function emit() {
            if (mode === 'build') sel.labels = formatSelector(rows);
            onChange?.(get());
            countSoon();
        }
        function get() {
            if (labelsOnly) return sel.labels || '';
            const out = {};
            if (sel.labels) out.labels = sel.labels;
            if ((sel.groups || []).length) out.groups = [...sel.groups];
            if ((sel.nodes || []).length) out.nodes = [...sel.nodes];
            if (sel.all && !Object.keys(out).length) out.all = true;
            return out;
        }
        const keyOptions = current => {
            const keys = Object.keys(index.labels);
            if (current && !keys.includes(current)) keys.push(current);
            return `<option value="">Choose a label</option>${keys.sort().map(k => { const n = Object.values(index.labels[k] || {}).reduce((a, b) => a + b, 0); return `<option value="${esc(k)}"${k === current ? ' selected' : ''}>${esc(k)}${n ? ` (${n})` : ' (no nodes)'}</option>`; }).join('')}`;
        };
        function rowHtml(r, i) {
            const vals = Object.entries(index.labels[r.key] || {});
            r.values.forEach(v => { if (v && !vals.some(([x]) => x === v)) vals.push([v, 0]); });
            let valueCtl = '';
            if (r.op === 'eq' || r.op === 'ne') valueCtl = `<span class="w-select"><select class="w-select__control" data-row-value="${i}" aria-label="Value for ${esc(r.key || 'label')}"${r.key ? '' : ' disabled'}><option value="">Choose a value</option>${vals.map(([v, n]) => `<option value="${esc(v)}"${r.values[0] === v ? ' selected' : ''}>${esc(v)} (${n})</option>`).join('')}</select></span>`;
            else if (r.op === 'in' || r.op === 'notin') valueCtl = `<fieldset class="orch-fieldset orch-sel__values"><legend class="w-sr-only">Values for ${esc(r.key || 'label')}</legend>${vals.length ? vals.map(([v, n]) => `<label class="w-check"><input class="w-check__input" type="checkbox" data-row-multi="${i}" value="${esc(v)}"${r.values.includes(v) ? ' checked' : ''}><span>${esc(v)} <span class="orch-sub">${n}</span></span></label>`).join('') : '<span class="orch-muted">No values on any node</span>'}</fieldset>`;
            return `<li class="orch-sel__row"><span class="w-select"><select class="w-select__control" data-row-key="${i}" aria-label="Label key">${keyOptions(r.key)}</select></span>
                <span class="w-select orch-sel__op"><select class="w-select__control" data-row-op="${i}" aria-label="Operator">${OPS.map(([k, l]) => `<option value="${k}"${r.op === k ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select></span>
                ${valueCtl}<button type="button" class="w-btn" data-variant="icon" data-size="sm" data-row-del="${i}" aria-label="Remove this condition" title="Remove">${icon('x', 'sm')}</button></li>`;
        }
        function paint() {
            const nodes = (index.nodes || []).filter(n => !test || (n.status === 'active' && n.canary));
            container.innerHTML = `<fieldset class="orch-fieldset orch-sel"><legend>${esc(label)}</legend>
                <div class="orch-stack-sm">
                    <div class="orch-sel__head"><div class="w-segmented" role="group" aria-label="How to choose labels"><button type="button" class="w-segmented__option" aria-pressed="${mode === 'build'}" data-mode="build">Pick labels</button><button type="button" class="w-segmented__option" aria-pressed="${mode === 'text'}" data-mode="text">Type a selector</button></div></div>
                    ${mode === 'build' ? `<ul class="orch-sel__rows" role="list">${rows.map(rowHtml).join('')}</ul>
                        <div class="w-btn-row"><button type="button" class="w-btn" data-variant="ghost" data-size="sm" data-row-add>${icon('plus', 'sm')}<span>Add a label condition</span></button>${rows.length ? `<code class="orch-mono orch-sub">${esc(formatSelector(rows) || '…')}</code>` : `<span class="orch-sub">${labelsOnly ? (emptyNote ? esc(emptyNote) : 'No condition: every node the run targets.') : 'No label condition.'}</span>`}</div>`
                        : `<div class="w-field"><label class="w-field__label" for="${id}-text">Label selector</label><input class="w-input" data-mono id="${id}-text" value="${esc(sel.labels || '')}" placeholder="env=prod,role in (web,api),!legacy" autocomplete="off" spellcheck="false">
                            <p class="w-field__hint">Comma means AND: <code>k=v</code>, <code>k!=v</code>, <code>k in (a,b)</code>, <code>k notin (a,b)</code>, <code>k</code> (present), <code>!k</code> (absent)${labelsOnly ? '' : ', <code>*</code> for every node'}.</p>${errLine('')}</div>`}
                    ${!labelsOnly && (index.groups || []).length ? `<div class="orch-sel__block"><p class="orch-label-text">Groups</p><div class="orch-checks">${index.groups.map(g => `<label class="w-check"><input class="w-check__input" type="checkbox" data-group="${esc(g.name)}"${(sel.groups || []).includes(g.name) ? ' checked' : ''}><span>${esc(g.name)} <span class="orch-sub">${esc(g.count)} · <code class="orch-mono">${esc(g.selector)}</code></span></span></label>`).join('')}</div></div>` : ''}
                    ${!labelsOnly ? `<div class="orch-sel__block"><p class="orch-label-text">${test ? 'Canary nodes' : 'Explicit nodes'}</p>${nodes.length ? `<div class="orch-checks">${nodes.map(n => `<label class="w-check"><input class="w-check__input" type="checkbox" data-node="${esc(n.id)}"${(sel.nodes || []).includes(n.id) ? ' checked' : ''}><span>${esc(n.name)} ${ui.chip(n.style, { sm: true })}${n.canary && !test ? ' <span class="orch-tag">canary</span>' : ''}</span></label>`).join('')}</div>` : `<p class="orch-muted">${test ? 'No active canary nodes. Mark one canary on its node page, or start a test node.' : 'No nodes yet.'}</p>`}</div>` : ''}
                    <div class="orch-sel__count" data-count aria-live="polite"></div>
                </div></fieldset>`;
            bind();
            countSoon();
        }
        function bind() {
            container.querySelectorAll('[data-mode]').forEach(b => b.addEventListener('click', () => {
                if (b.dataset.mode === mode) return;
                if (b.dataset.mode === 'build') {
                    const parsed = parseSelector(sel.labels);
                    if (parsed === null) { ui.toast('This selector can’t be shown as label conditions; keep typing it.', 'attention'); return; }
                    rows = parsed;
                }
                mode = b.dataset.mode;
                paint();
                container.querySelector(`[data-mode="${mode}"]`)?.focus();
            }));
            container.querySelector('[data-row-add]')?.addEventListener('click', () => {
                rows.push({ op: 'eq', key: '', values: [] });
                paint();
                container.querySelector(`[data-row-key="${rows.length - 1}"]`)?.focus();
            });
            container.querySelectorAll('[data-row-key]').forEach(s => s.addEventListener('change', () => { const r = rows[Number(s.dataset.rowKey)]; r.key = s.value; r.values = []; paint(); container.querySelector(`[data-row-key="${s.dataset.rowKey}"]`)?.focus(); emit(); }));
            container.querySelectorAll('[data-row-op]').forEach(s => s.addEventListener('change', () => { const r = rows[Number(s.dataset.rowOp)]; r.op = s.value; if (!['in', 'notin'].includes(r.op)) r.values = r.values.slice(0, 1); if (['exists', 'absent'].includes(r.op)) r.values = []; paint(); container.querySelector(`[data-row-op="${s.dataset.rowOp}"]`)?.focus(); emit(); }));
            container.querySelectorAll('[data-row-value]').forEach(s => s.addEventListener('change', () => { rows[Number(s.dataset.rowValue)].values = s.value ? [s.value] : []; emit(); paintSummary(); }));
            container.querySelectorAll('[data-row-multi]').forEach(c => c.addEventListener('change', () => {
                const r = rows[Number(c.dataset.rowMulti)];
                r.values = [...container.querySelectorAll(`[data-row-multi="${c.dataset.rowMulti}"]:checked`)].map(x => x.value);
                emit(); paintSummary();
            }));
            container.querySelectorAll('[data-row-del]').forEach(b => b.addEventListener('click', () => { rows.splice(Number(b.dataset.rowDel), 1); paint(); emit(); }));
            const text = container.querySelector(`#${id}-text`);
            text?.addEventListener('input', () => { sel.labels = text.value.trim(); emit(); });
            container.querySelectorAll('[data-group]').forEach(c => c.addEventListener('change', () => { sel.groups = [...container.querySelectorAll('[data-group]:checked')].map(x => x.dataset.group); emit(); }));
            container.querySelectorAll('[data-node]').forEach(c => c.addEventListener('change', () => { sel.nodes = [...container.querySelectorAll('[data-node]:checked')].map(x => x.dataset.node); emit(); }));
        }
        function paintSummary() {
            const code = container.querySelector('.orch-sel__rows + .w-btn-row code, .orch-sel__rows + .w-btn-row .orch-sub');
            if (code) code.outerHTML = rows.length ? `<code class="orch-mono orch-sub">${esc(formatSelector(rows) || '…')}</code>` : `<span class="orch-sub">No label condition.</span>`;
        }
        const countSoon = ui.debounce(count, 300);
        async function count() {
            const box = container.querySelector('[data-count]');
            if (!box) return;
            const s = get();
            if (labelsOnly && emptyNote && !String(s || '').trim()) { box.innerHTML = ''; return; }
            const empty = labelsOnly ? false : !Object.keys(s).length;
            if (empty) { box.innerHTML = `<p class="orch-sub">${allowAll ? 'No targets yet. A fact trigger may leave this empty (it runs on the node whose metric fired); other schedules need targets.' : 'Choose labels, groups or nodes.'}</p>`; return; }
            const seq = ++previewSeq;
            try {
                const res = await api.cmd('previewSelector', { selector: labelsOnly ? (s || '*') : s, test });
                if (seq !== previewSeq || !document.contains(box)) return;
                const p = res.data;
                const err = container.querySelector(`#${id}-text`)?.closest('.w-field')?.querySelector('[data-err]');
                if (err) err.innerHTML = p.ok ? '' : `<span aria-hidden="true">✗</span> ${esc(p.error)}`;
                if (!p.ok) { box.innerHTML = `<p class="orch-error">${esc(p.error)}</p>`; return; }
                const ex = p.excluded || [];
                box.innerHTML = `<p class="orch-sel__summary">${ui.chip(p.count ? { semantic: 'ok', glyph: '✓', label: `Matches ${ui.plural(p.count, 'node')}` } : { semantic: 'caution', glyph: '◆', label: 'Matches no node' })}${ex.length ? ` <span class="orch-sub">${esc(ex.length)} excluded</span>` : ''}${labelsOnly ? ' <span class="orch-sub">in the fleet now</span>' : ''}</p>
                    ${ex.length ? `<details class="orch-details"><summary>Why ${ex.length === 1 ? 'one node is' : `${ex.length} nodes are`} excluded</summary><ul class="orch-list">${ex.map(x => `<li>${esc(x.name || x.node_id)}: ${esc(x.reason)}</li>`).join('')}</ul></details>` : ''}`;
            } catch (e) {
                if (seq === previewSeq) box.innerHTML = `<p class="orch-error">${esc(e.message)}</p>`;
            }
        }
        async function load() {
            try { index = await labels(); } catch (e) { index = { labels: {}, groups: [], nodes: [] }; }
            const focusSig = document.activeElement && container.contains(document.activeElement) ? (document.activeElement.id ? `#${document.activeElement.id}` : null) : null;
            paint();
            if (focusSig) container.querySelector(focusSig)?.focus();
        }
        paint();
        const ready = load();
        watch('node', container, load);
        return {
            ready,
            value: get,
            set(next) { sel = typeof next === 'string' ? { labels: next } : { labels: next?.labels || '', groups: [...(next?.groups || [])], nodes: [...(next?.nodes || [])] }; const p = parseSelector(sel.labels); mode = p === null ? 'text' : 'build'; rows = p || []; paint(); },
            refresh: count,
        };
    }

    /* ------------------------------------------------------------ node picker */
    function nodePicker(container, { multi = false, test = false, value = multi ? [] : '', label = test ? 'Canary node' : 'Node', onChange = null, includeOffline = true, id = ui.uid('node') } = {}) {
        let selected = multi ? [...(value || [])] : value;
        let nodes = [];
        function paint() {
            const shown = nodes.filter(n => (!test || (n.status === 'active' && n.canary)) && (includeOffline || n.online));
            if (multi) {
                container.innerHTML = `<fieldset class="orch-fieldset"><legend>${esc(label)}</legend>${shown.length ? `<div class="orch-checks">${shown.map(n => `<label class="w-check"><input class="w-check__input" type="checkbox" data-pick="${esc(n.id)}"${selected.includes(n.id) ? ' checked' : ''}><span>${esc(n.name)} ${ui.chip(n.style, { sm: true })}${n.kind === 'container' ? ' <span class="orch-tag">test node</span>' : ''}</span></label>`).join('')}</div>` : `<p class="orch-muted">${test ? 'No active canary nodes. Mark one canary or start a test node.' : 'No nodes.'}</p>`}</fieldset>`;
                container.querySelectorAll('[data-pick]').forEach(c => c.addEventListener('change', () => { selected = [...container.querySelectorAll('[data-pick]:checked')].map(x => x.dataset.pick); onChange?.(selected); }));
            } else {
                container.innerHTML = `<div class="w-field"><label class="w-field__label" for="${id}">${esc(label)}</label><span class="w-select"><select class="w-select__control" id="${id}">${shown.length ? '' : `<option value="">${test ? 'No active canary nodes' : 'No nodes'}</option>`}${!test ? '<option value="">No node (labels only)</option>' : ''}${shown.map(n => `<option value="${esc(n.id)}"${n.id === selected ? ' selected' : ''}>${esc(n.name)} · ${esc(n.style?.label || n.status)}${Object.keys(n.labels || {}).length ? ` · ${esc(ui.formatLabels(n.labels))}` : ''}</option>`).join('')}</select></span></div>`;
                const s = container.querySelector('select');
                if (!selected && test && shown[0]) { selected = shown[0].id; s.value = selected; }
                s.addEventListener('change', () => { selected = s.value; onChange?.(selected); });
            }
        }
        async function load() {
            try { nodes = (await labels()).nodes || []; } catch (_) { nodes = []; }
            paint();
        }
        const ready = load();
        watch('node', container, load);
        return { ready, value: () => selected, nodes: () => nodes };
    }

    window.OrchPickers = { options, labels, watch, combobox, paramsForm, completion, selectorBuilder, nodePicker, parseSelector, formatSelector, splitRef, wholeTemplate };
})();
