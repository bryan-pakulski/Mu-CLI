/* Orchestration — Runs: runs list, the run wizard and plan view, run detail
 * (live per-node grid, execution drawer, rollbacks, batches, notes, actions)
 * and the Approvals inbox (documentation/plans/orchestration.md §3.4, §5, §8).
 */
(() => {
    'use strict';
    const api = window.OrchAPI;
    const ui = window.OrchUI;
    const { esc, icon } = ui;
    const Orch = (window.Orch = window.Orch || { routes: {} });
    Orch.routes = Orch.routes || {};

    const runHref = r => `#/run/${encodeURIComponent(r.id)}`;
    const RETRYABLE = ['failed', 'unreachable', 'timed_out', 'refused', 'cancelled'];
    function effectStyle(effect) {
        const row = (window.WASH?.vocabulary?.orch_effect || {})[effect];
        return row ? { semantic: row.semantic, label: row.label } : { semantic: 'danger', label: effect || 'unknown' };
    }
    const DECISION = {
        allow: { semantic: 'ok', glyph: '✓', label: 'Allowed' },
        confirm: { semantic: 'attention', glyph: '◆', label: 'Needs your approval' },
        typed: { semantic: 'attention', glyph: '◆', label: 'Typed confirmation' },
        deny: { semantic: 'danger', glyph: '✗', label: 'Denied by policy' },
    };
    const targetLink = (ref, kind) => {
        const [id, v] = String(ref || '').split('@');
        if (!id) return '—';
        if (kind === 'agent_update') return '<span class="orch-mono">mucli-node</span>';
        return `<a class="orch-mono" href="#/${kind === 'runbook' || kind === 'runbook_test' ? 'runbook' : 'tool'}/${encodeURIComponent(id)}/${encodeURIComponent(v || '')}">${esc(ref)}</a>`;
    };

    /* ------------------------------------------------------------ runs table */
    function progressText(r) {
        const n = (r.summary || {}).nodes;
        if (!n) return `<span class="orch-muted">${esc(ui.plural(r.nodes || 0, 'node'))}</span>`;
        const total = (r.summary || {}).total_nodes || r.nodes || 0;
        const parts = [`${n.done || 0}/${total} done`];
        if (n.failed) parts.push(`${n.failed} failed`);
        if (n.active) parts.push(`${n.active} running`);
        return esc(parts.join(' · '));
    }
    function runsTable(runs, { empty = 'No runs yet.' } = {}) {
        return ui.table({
            label: 'Runs', empty,
            cols: [{ label: 'Run' }, { label: 'State' }, { label: 'Effect' }, { label: 'Target' }, { label: 'Progress' }, { label: 'Proposed by' }, { label: 'Created' }],
            rows: (runs || []).map(r => [`<a href="${runHref(r)}">${esc(r.title || r.id)}</a><span class="orch-sub orch-mono">${esc(r.id)}${r.kind === 'test' || r.kind === 'runbook_test' ? ' · test' : ''}${r.check_mode ? ' · check mode' : ''}</span>`,
                ui.chip(r.style, { title: r.paused_reason || r.reason || '' }), ui.effectChip(r.effect_style, r.effect), targetLink(r.target, r.kind), progressText(r), esc(r.proposed_by || ''), ui.time(r.created_at)]),
        });
    }
    Orch.runsTable = runsTable;

    /* ------------------------------------------------------------ runs list */
    const STATE_FILTERS = [['', 'All'], ['proposed', 'Awaiting approval'], ['running,approved,rolling_back', 'Running'], ['paused', 'Paused'], ['succeeded', 'Succeeded'], ['failed', 'Failed'], ['cancelled,rejected,rolled_back,expired', 'Stopped']];
    async function renderList(el, route, { isCurrent, setQuery }) {
        const state = route.query.get('state') || '';
        const data = await api.get(`runs?limit=200${state ? `&state=${encodeURIComponent(state)}` : ''}`);
        if (!isCurrent()) return null;
        el.innerHTML = `
            <header class="w-page__head orch-head">
                <div><h1 class="w-page__title">Runs</h1><p class="w-page__lede">Every request to run a tool or runbook across the fleet, from proposal to result.</p></div>
                <span class="orch-spacer"></span>
                <a class="w-btn" data-variant="primary" href="#/new-run">${icon('play')}<span>New run</span></a>
            </header>
            <div class="orch-stack">
                ${Orch.pausedBanner?.(Orch.attention?.().dispatch_paused) || ''}
                <div class="w-chip-row" role="group" aria-label="Filter by state">${STATE_FILTERS.map(([k, l]) => `<button type="button" class="w-chip" data-variant="filter" aria-pressed="${state === k}" data-state="${esc(k)}">${esc(l)}</button>`).join('')}</div>
                ${runsTable(data.runs, { empty: state ? 'No runs in this state.' : 'No runs yet. Start one from a published tool.' })}
            </div>`;
        el.querySelectorAll('[data-state]').forEach(b => b.addEventListener('click', () => { setQuery({ state: b.dataset.state }); Orch.refresh(); }));
        return null;
    }

    /* ------------------------------------------------------------ params form */
    /* Kept for callers of Orch.runs.paramsForm: the shared schema-driven form (pickers.js). */
    const paramsForm = (container, schema, opts) => window.OrchPickers.paramsForm(container, schema, opts);

    /* ------------------------------------------------------------ plan view */
    /* p: a plan (planRun) or a run detail; nameOf(id) → node name. */
    function planView(p, nameOf = id => id) {
        const decision = p.decision_full || p.decision || {};
        const ds = DECISION[decision.level] || { semantic: 'neutral', glyph: '○', label: decision.level || 'Unknown' };
        const es = p.effect_style || effectStyle(p.effect);
        const nodes = p.node_rows || (p.grid || []).map(g => ({ id: g.node_id, name: g.name, labels: g.labels })) || [];
        const perNode = new Map((decision.per_node || []).map(d => [d.node_id, d]));
        const rules = [...new Set((decision.per_node || []).map(d => d.rule).filter(Boolean))];
        const steps = p.steps || [];
        return `<div class="orch-plan orch-stack">
            ${(p.errors || []).length ? ui.banner('danger', '✗', `<strong>This plan can’t run:</strong> ${p.errors.map(esc).join('; ')}`) : ''}
            <dl class="w-stats orch-stats orch-plan__stats">
                <div class="w-stat"><dt class="w-stat__label">Blast radius</dt><dd class="w-stat__value orch-stat-text">${esc(p.blast_radius || ui.plural(nodes.length, 'node'))}</dd></div>
                <div class="w-stat"><dt class="w-stat__label">Effect</dt><dd class="w-stat__value orch-stat-text">${ui.effectChip(es, p.effect)}</dd></div>
                <div class="w-stat"><dt class="w-stat__label">Rollback</dt><dd class="w-stat__value orch-stat-text">${p.rollback_available ? ui.chip({ semantic: 'ok', glyph: '✓', label: 'Available' }) : ui.chip({ semantic: 'caution', glyph: '◆', label: 'Not available' })}</dd></div>
                <div class="w-stat"><dt class="w-stat__label">Mode</dt><dd class="w-stat__value orch-stat-text">${p.check_mode ? ui.chip({ semantic: 'plan', glyph: '○', label: 'Check mode (dry run)' }) : ui.meta('Real run')}</dd></div>
            </dl>
            <article class="w-card orch-card"${decision.level === 'deny' ? ' data-state="danger"' : (decision.level === 'confirm' || decision.level === 'typed' ? ' data-state="attention"' : ' data-state="ok"')}>
                <header class="w-card__head">${ui.chip(ds)}<h3 class="w-card__title">Access decision</h3></header>
                <div class="w-card__body"><p>${esc(decision.summary || '')}</p>${rules.length > 1 ? `<p class="orch-sub">Rules applied: ${rules.map(esc).join(' · ')}</p>` : ''}${(decision.reasons || []).length ? `<ul class="orch-list">${decision.reasons.map(r => `<li>${esc(typeof r === 'string' ? r : JSON.stringify(r))}</li>`).join('')}</ul>` : ''}</div>
            </article>
            ${(p.warnings || []).length ? `<ul class="orch-warnings" role="list">${p.warnings.map(w => `<li>${ui.banner('caution', '◆', esc(w))}</li>`).join('')}</ul>` : ''}
            ${(p.secrets || []).length ? `<p class="orch-sub">Secrets passed to the node: ${p.secrets.map(s => `<code class="orch-mono">${esc(typeof s === 'string' ? s : s.name)}</code>`).join(' ')}</p>` : ''}
            <div class="orch-grid-2">
                <section class="orch-section"><header class="orch-section__head"><h3 class="orch-section__title">Nodes</h3><span class="w-count">${nodes.length}</span></header>
                    ${ui.table({ label: 'Resolved nodes', empty: 'No nodes resolved.', stack: false, cols: [{ label: 'Node' }, { label: 'Labels' }, { label: 'Decision' }],
                        rows: nodes.map(n => { const d = perNode.get(n.id); return [`<a href="#/node/${encodeURIComponent(n.id)}">${esc(n.name || n.id)}</a>`, ui.labelChips(n.labels), d ? `<span title="${esc(d.rule || '')}">${ui.chip(DECISION[d.level] || { semantic: 'neutral', glyph: '○', label: d.level }, { sm: true })}</span>` : '—']; }) })}</section>
                <section class="orch-section"><header class="orch-section__head"><h3 class="orch-section__title">Excluded</h3><span class="w-count">${(p.excluded || []).length}</span></header>
                    ${ui.table({ label: 'Excluded nodes', empty: 'No matched node was excluded.', stack: false, cols: [{ label: 'Node' }, { label: 'Why' }],
                        rows: (p.excluded || []).map(x => [`<a href="#/node/${encodeURIComponent(x.node_id)}">${esc(x.name || x.node_id)}</a>`, esc(x.reason)]) })}</section>
            </div>
            <section class="orch-section"><header class="orch-section__head"><h3 class="orch-section__title">Batches</h3><span class="orch-sub">${esc(strategyText(p.strategy))}</span></header>
                ${(p.batches || []).length ? `<ol class="orch-batches">${p.batches.map((b, i) => `<li><span class="orch-label-text">${i === 0 && (p.strategy || {}).canary ? 'Canary' : `Batch ${i + 1}`}</span> ${b.map(id => `<span class="w-chip" data-variant="meta">${esc(nameOf(id))}</span>`).join('')}</li>`).join('')}</ol>` : '<p class="orch-muted">No batches.</p>'}</section>
            ${steps.length > 1 || (steps[0] && Object.keys(steps[0].params || {}).length) ? `<section class="orch-section"><header class="orch-section__head"><h3 class="orch-section__title">Steps</h3></header>
                ${ui.table({ label: 'Steps', stack: false, cols: [{ label: 'Step' }, { label: 'Tool' }, { label: 'Effect' }, { label: 'Params' }, { label: 'On failure' }],
                    rows: steps.map(s => [esc(s.title || s.name), `<span class="orch-mono">${esc(s.version ? `${s.tool_id}@${s.version}` : s.tool_id)}</span>${s.precondition ? ' ' + ui.meta('precondition') : ''}`, ui.effectChip(effectStyle(s.effect), s.effect), `<code class="orch-mono orch-break">${esc(JSON.stringify(s.params || {}))}</code>`, esc(s.on_failure || 'stop')]) })}</section>` : ''}
        </div>`;
    }
    function strategyText(s) {
        if (!s) return '';
        return [s.canary ? `canary ${s.canary}` : 'no canary', `batch ${s.batch}`, s.max_parallel ? `max ${s.max_parallel} parallel` : '', s.failure_threshold != null ? `stop above ${s.failure_threshold}% failed` : '', s.pause_between_s ? `${s.pause_between_s}s between batches` : ''].filter(Boolean).join(' · ');
    }

    /* ------------------------------------------------------------ wizard */
    /* Every choice comes from the live catalog and fleet (pickers.js): publishing a version
     * while the wizard is open updates the options, changing the version rebuilds the
     * parameters (compatible values kept, rejected ones listed) and the selector shows how many
     * nodes it reaches now. */
    async function renderWizard(el, route, { isCurrent }) {
        const P = window.OrchPickers;
        const nodesData = await api.get('labels');
        if (!isCurrent()) return null;
        let kind = route.query.get('kind') === 'runbook' ? 'runbook' : 'tool';
        let lastPlan = null;
        let params = null;
        let picker = null;
        let where = null;
        let paramsSchemaRef = '';

        el.innerHTML = `
            <header class="w-page__head orch-head">
                <div><h1 class="w-page__title">New run</h1><p class="w-page__lede">Pick what to run and where, preview the plan, then propose it. Nothing runs until the plan is approved.</p></div>
            </header>
            <div class="orch-stack">
                ${Orch.pausedBanner?.(Orch.attention?.().dispatch_paused) || ''}
                <form class="orch-wizard orch-stack" novalidate>
                    <section class="w-card orch-card" aria-labelledby="wz-what"><header class="w-card__head"><span class="orch-step-num" aria-hidden="true">1</span><h2 class="w-card__title" id="wz-what">What to run</h2></header>
                        <div class="w-card__body orch-stack">
                            <div class="w-segmented" role="group" aria-label="Kind">
                                <button type="button" class="w-segmented__option" aria-pressed="${kind === 'tool'}" data-kind="tool">Tool</button>
                                <button type="button" class="w-segmented__option" aria-pressed="${kind === 'runbook'}" data-kind="runbook">Runbook</button>
                            </div>
                            <div data-target></div>
                        </div></section>
                    <section class="w-card orch-card" aria-labelledby="wz-params"><header class="w-card__head"><span class="orch-step-num" aria-hidden="true">2</span><h2 class="w-card__title" id="wz-params">Parameters</h2></header>
                        <div class="w-card__body" data-params></div></section>
                    <section class="w-card orch-card" aria-labelledby="wz-where"><header class="w-card__head"><span class="orch-step-num" aria-hidden="true">3</span><h2 class="w-card__title" id="wz-where">Where</h2></header>
                        <div class="w-card__body" data-where></div></section>
                    <section class="w-card orch-card" aria-labelledby="wz-how"><header class="w-card__head"><span class="orch-step-num" aria-hidden="true">4</span><h2 class="w-card__title" id="wz-how">How</h2></header>
                        <div class="w-card__body orch-stack">
                            <div class="orch-form-grid">
                                <div class="w-field"><label class="w-field__label" for="wz-canary">Canary nodes</label><input class="w-input" type="number" min="0" id="wz-canary" placeholder="default"></div>
                                <div class="w-field"><label class="w-field__label" for="wz-batch">Batch size</label><input class="w-input" type="number" min="1" id="wz-batch" placeholder="default"></div>
                                <div class="w-field"><label class="w-field__label" for="wz-par">Max parallel</label><input class="w-input" type="number" min="1" id="wz-par" placeholder="default"></div>
                                <div class="w-field"><label class="w-field__label" for="wz-thr">Failure threshold (%)</label><input class="w-input" type="number" min="0" max="100" id="wz-thr" placeholder="default"></div>
                                <div class="w-field"><label class="w-field__label" for="wz-pause">Pause between batches (s)</label><input class="w-input" type="number" min="0" id="wz-pause" placeholder="default"></div>
                            </div>
                            <p class="w-field__hint">Blank fields use the rollout defaults from Settings. A batch that fails above the threshold pauses the run for you to decide.</p>
                            <label class="w-switch"><span class="w-switch__label">Check mode (dry run: report, change nothing)</span><input class="w-switch__input" type="checkbox" role="switch" id="wz-check"></label>
                            <p class="w-field__hint" data-check-hint></p>
                        </div></section>
                    <p class="w-field__error" data-error role="alert"></p>
                    <div class="w-btn-row orch-wizard__actions"><a class="w-btn" data-variant="ghost" href="#/runs">Cancel</a><button type="submit" class="w-btn" data-variant="secondary" data-preview>${icon('eye')}<span>Preview plan</span></button></div>
                </form>
                <section class="orch-section" aria-labelledby="wz-plan-h" data-plan-section hidden>
                    <header class="orch-section__head"><span class="w-eyebrow" data-tone="plan"><i class="w-eyebrow__swatch"></i>Plan</span><h2 class="orch-section__title" id="wz-plan-h" tabindex="-1">Review the plan</h2></header>
                    <div data-plan></div>
                    <div class="orch-propose">
                        <div class="w-field"><label class="w-field__label" for="wz-title">Run title <span class="orch-muted">(optional)</span></label><input class="w-input" id="wz-title" autocomplete="off"></div>
                        <div class="w-btn-row"><button type="button" class="w-btn" data-variant="primary" data-propose>${icon('send')}<span>Propose run</span></button></div>
                    </div>
                </section>
            </div>`;
        const form = el.querySelector('form');
        const stale = () => {
            if (!lastPlan) return;
            lastPlan = null;
            el.querySelector('[data-propose]').disabled = true;
            el.querySelector('[data-plan]').insertAdjacentHTML('afterbegin', `<div data-stale-note>${ui.banner('info', '●', 'You changed the inputs. Preview the plan again before proposing.')}</div>`);
        };
        /* The chosen version's inputs drive the form; a version change keeps what still fits. */
        const targetChanged = (ref, version) => {
            const schema = version ? version.inputs || {} : {};
            const box = el.querySelector('[data-params]');
            if (!params || !ref) params = paramsForm(box, schema, { emptyText: ref ? 'This version takes no parameters.' : 'Choose what to run first.', onInput: stale });
            else if (ref !== paramsSchemaRef) {
                const { rejected } = params.rebuild(schema);
                if (rejected.length) ui.toast(`${ui.plural(rejected.length, 'value')} no longer fit ${ref}; see the list under the parameters.`, 'attention');
            }
            paramsSchemaRef = ref;
            const check = el.querySelector('#wz-check');
            const supports = !version || kind === 'runbook' || version.check_mode || version.effect === 'read_only';
            check.disabled = !supports;
            if (!supports) check.checked = false;
            el.querySelector('[data-check-hint]').textContent = supports ? '' : 'This tool doesn’t support check mode.';
            stale();
        };
        const mountTarget = value => {
            params = null;
            paramsSchemaRef = '';
            picker = P.combobox(el.querySelector('[data-target]'), { purpose: 'run', kinds: [kind], value, label: kind === 'tool' ? 'Published tool' : 'Published runbook', onChange: targetChanged });
            picker.ready.then(() => targetChanged(picker.value(), picker.version()));
        };
        mountTarget(route.query.get('target') || '');
        where = P.selectorBuilder(el.querySelector('[data-where]'), { value: { labels: route.query.get('labels') || '' }, label: 'Target nodes', onChange: stale });
        el.querySelectorAll('[data-kind]').forEach(b => b.addEventListener('click', () => {
            if (kind === b.dataset.kind) return;
            kind = b.dataset.kind;
            el.querySelectorAll('[data-kind]').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
            mountTarget('');
        }));
        form.addEventListener('input', stale);
        form.addEventListener('change', stale);

        const num = id => { const v = el.querySelector(id).value.trim(); return v === '' ? undefined : Number(v); };
        const collect = () => {
            const read = params ? params.read() : { values: {}, errors: [] };
            const strategy = {};
            [['canary', '#wz-canary'], ['batch', '#wz-batch'], ['max_parallel', '#wz-par'], ['failure_threshold', '#wz-thr'], ['pause_between_s', '#wz-pause']].forEach(([k, id]) => { const v = num(id); if (v !== undefined) strategy[k] = v; });
            const selector = where.value();
            const target = picker ? picker.value() : '';
            const errors = [...read.errors];
            if (!target) errors.push(`choose a published ${kind}`);
            if (!Object.keys(selector).length) errors.push('choose where to run: labels, groups or nodes');
            return { args: { kind, target, params: read.values, selector, strategy, check_mode: el.querySelector('#wz-check').checked }, errors };
        };
        const names = Object.fromEntries((nodesData.nodes || []).map(n => [n.id, n.name]));
        form.addEventListener('submit', async e => {
            e.preventDefault();
            const err = el.querySelector('[data-error]');
            err.textContent = '';
            const { args, errors } = collect();
            if (errors.length) { err.innerHTML = `<span aria-hidden="true">✗</span> ${esc(errors.join('; '))}`; return; }
            try {
                const res = await ui.busy(el.querySelector('[data-preview]'), () => api.plan(args));
                lastPlan = { args, plan: res.data.plan };
                const section = el.querySelector('[data-plan-section]');
                section.hidden = false;
                el.querySelector('[data-plan]').innerHTML = planView(res.data.plan, id => names[id] || id);
                el.querySelector('[data-propose]').disabled = !res.data.plan.ok || res.data.plan.decision?.level === 'deny';
                const h = el.querySelector('#wz-plan-h');
                h.scrollIntoView({ behavior: 'smooth', block: 'start' });
                h.focus({ preventScroll: true });
            } catch (e2) { err.innerHTML = `<span aria-hidden="true">✗</span> ${esc(e2.message)}`; }
        });
        el.querySelector('[data-propose]').addEventListener('click', async e => {
            if (!lastPlan) return;
            try {
                const res = await ui.busy(e.currentTarget, () => api.cmd('proposeRun', { ...lastPlan.args, title: el.querySelector('#wz-title').value.trim() }));
                const run = res.data.run;
                ui.toast(run.state === 'proposed' ? 'Run proposed. It needs your approval.' : 'Run started', 'ok');
                Orch.go(`#/run/${encodeURIComponent(run.id)}`);
            } catch (err) { ui.toast(`Couldn’t propose the run: ${err.message}`, 'danger'); }
        });
        // A form in progress is never re-rendered under the operator; the pickers follow the
        // change stream themselves.
        return { onChange() {} };
    }

    /* ------------------------------------------------------------ execution drawer */
    let drawer = null;
    async function openExecution(execId, { refresh = false } = {}) {
        if (!drawer || !document.contains(drawer.el)) {
            if (refresh) return;
            const opener = document.activeElement;
            const titleId = ui.uid('exec');
            const el = document.createElement('div');
            el.className = 'w-drawer orch-drawer';
            el.hidden = true;
            el.innerHTML = `<aside class="w-drawer__panel orch-drawer__panel" role="dialog" aria-modal="true" aria-labelledby="${titleId}">
                <header class="w-drawer__head"><h2 class="w-drawer__title" id="${titleId}">Execution</h2><button type="button" class="w-btn" data-variant="icon" aria-label="Close" title="Close" data-close>${icon('x')}</button></header>
                <div class="w-drawer__body" data-body>${ui.loading()}</div></aside>`;
            document.body.append(el);
            const close = () => { window.MuDialog?.close(el); ui.removeAfterClose(el); drawer = null; if (opener && document.contains(opener)) opener.focus(); };
            el.addEventListener('dialog-close', close);
            el.addEventListener('mousedown', e => { if (e.target === el) close(); });
            el.querySelector('[data-close]').onclick = close;
            if (window.MuDialog) window.MuDialog.open(el, { focus: el.querySelector('[data-close]') }); else { el.hidden = false; el.querySelector('[data-close]').focus(); }
            drawer = { el, close, id: execId };
        }
        drawer.id = execId;
        const body = drawer.el.querySelector('[data-body]');
        const scroll = body.querySelector('.orch-log')?.scrollTop;
        let x;
        try { x = await api.get(`executions/${encodeURIComponent(execId)}`); }
        catch (err) { body.innerHTML = ui.errorState(err, { retry: false }); return; }
        if (!drawer || drawer.id !== execId) return;
        drawer.el.querySelector('.w-drawer__title').textContent = `${x.tool} · ${x.step_name || `step ${x.step}`}`;
        body.innerHTML = `<div class="orch-stack">
            <div class="orch-head__meta">${ui.chip(x.style)}${x.check_mode ? ui.meta('check mode') : ''}${x.rollback_of ? ui.meta('rollback') : ''}</div>
            ${x.reason ? `<p class="${['failed', 'refused', 'timed_out', 'unreachable'].includes(x.state) ? 'orch-error' : 'orch-muted'}">${esc(x.reason)}</p>` : ''}
            ${ui.kv([['Node', `<a href="#/node/${encodeURIComponent(x.node_id)}" class="orch-mono">${esc(x.node_id)}</a>`, true], ['Exit code', x.exit_code == null ? '—' : String(x.exit_code)], ['Duration', ui.dur(x.duration_s)], ['Attempts', x.attempts], ['Batch', x.batch + 1],
                ['Started', ui.when(x.started_at)], ['Finished', ui.when(x.finished_at)], ['Redactions', x.redactions ? String(x.redactions) : '']])}
            ${(x.output_errors || []).length ? ui.banner('danger', '✗', `<strong>Output didn’t match the declared schema:</strong> ${x.output_errors.map(esc).join('; ')}`) : ''}
            <section class="orch-section"><header class="orch-section__head"><h3 class="orch-section__title">Output</h3></header>${ui.outputView(x.output)}</section>
            <section class="orch-section"><header class="orch-section__head"><h3 class="orch-section__title">Log</h3>${x.log_truncated ? '<span class="orch-sub">truncated on the node</span>' : ''}<span class="orch-spacer"></span>${x.log ? `<button type="button" class="w-btn" data-variant="ghost" data-size="sm" data-orch-copy data-copy-value="${esc(x.log)}">${icon('copy', 'sm')}<span>Copy</span></button>` : ''}</header>
                ${x.log ? `<pre class="orch-log" tabindex="0" aria-label="Execution log">${esc(x.log)}</pre>` : '<p class="orch-muted">No log output.</p>'}</section>
            ${x.params && Object.keys(x.params).length ? `<details class="orch-details"><summary>Parameters</summary>${ui.code(ui.json(x.params), { lang: 'json', title: 'params.json' })}</details>` : ''}
        </div>`;
        const log = body.querySelector('.orch-log');
        if (log && scroll != null) log.scrollTop = scroll;
    }

    /* ------------------------------------------------------------ run detail */
    function gridTable(run) {
        const steps = run.steps || [];
        const canRetry = ['running', 'paused'].includes(run.state);
        const hasRollbacks = (run.grid || []).some(g => (g.rollbacks || []).length);
        const cell = c => {
            const label = `${c.name || `step ${c.step + 1}`}: ${c.style?.label || ''}${c.attempts > 1 ? ` (${c.attempts} attempts)` : ''}`;
            if (!c.exec) return `<span class="orch-cell" data-tone="${esc(c.style?.semantic || 'neutral')}" title="${esc(label)}"><span aria-hidden="true">${esc(c.style?.glyph || '○')}</span><span class="w-sr-only">${esc(label)}</span></span>`;
            return `<button type="button" class="orch-cell" data-tone="${esc(c.style.semantic)}" data-status="${esc(c.style.status)}" data-exec="${esc(c.exec.id)}" title="${esc(label)}" aria-label="${esc(label)}. Open details"><span aria-hidden="true">${esc(c.style.glyph)}</span><span class="orch-cell__label">${esc(c.style.label)}</span></button>`;
        };
        return ui.table({
            label: 'Per-node progress', stack: false, cls: 'orch-grid-wrap',
            cols: [{ label: 'Node' }, { label: 'Batch', num: true }, ...steps.map((s, i) => ({ label: s.title && steps.length > 1 ? `${i + 1}. ${s.name}` : (s.title || s.name || `Step ${i + 1}`) })), ...(hasRollbacks ? [{ label: 'Rollback' }] : []), { label: 'Actions', sr: true, cls: 'w-table__actions orch-actions-col' }],
            rows: (run.grid || []).map(g => {
                const failed = g.failed || g.cells.some(c => c.exec && RETRYABLE.includes(c.exec.state));
                return [`<a href="#/node/${encodeURIComponent(g.node_id)}">${esc(g.name)}</a>${g.online ? '' : ' <span class="orch-sub">offline</span>'}`, String((g.batch || 0) + 1),
                    ...g.cells.map(cell),
                    ...(hasRollbacks ? [(g.rollbacks || []).map(x => cell({ name: 'rollback', step: x.step, exec: x, style: x.style, attempts: 1 })).join(' ') || '<span class="orch-muted">—</span>'] : []),
                    canRetry && failed ? `<button type="button" class="w-btn" data-variant="ghost" data-size="sm" data-retry="${esc(g.node_id)}">${icon('refresh-cw', 'sm')}<span>Retry</span></button>` : ''];
            }),
        });
    }

    function runHeader(run) {
        return `<header class="w-page__head orch-head">
            <div class="orch-head__title">
                <span class="w-eyebrow" data-tone="${run.kind === 'test' || run.kind === 'runbook_test' ? 'plan' : 'accent'}"><i class="w-eyebrow__swatch"></i>${run.kind === 'test' ? 'Test run' : run.kind === 'runbook_test' ? 'Runbook test' : run.kind === 'agent_update' ? 'Agent update' : run.kind === 'runbook' ? 'Runbook run' : 'Run'}</span>
                <h1 class="w-page__title">${esc(run.title || run.id)}</h1>
                <div class="orch-head__meta">${ui.chip(run.style)}${ui.effectChip(run.effect_style, run.effect)}${run.check_mode ? ui.chip({ semantic: 'plan', glyph: '○', label: 'Check mode' }) : ''}${targetLink(run.target, run.kind)}<span class="orch-sub orch-mono">${esc(run.id)}</span></div>
            </div>
        </header>`;
    }

    function runDone(run) {
        return (_result, action) => { Orch.refresh(); if (action.id === 'approveRun' || action.id === 'rejectRun') Orch.refreshAttention?.(); };
    }

    async function renderRun(el, route, { isCurrent, setCrumbs }) {
        const id = route.parts[0];
        const run = await api.get(`runs/${encodeURIComponent(id)}`);
        if (!isCurrent()) return null;
        setCrumbs([{ label: 'Runs', to: '#/runs' }, { label: run.title || run.id }]);
        const names = Object.fromEntries((run.grid || []).map(g => [g.node_id, g.name]));
        const sum = run.summary || {};
        const ncount = sum.nodes || {};
        const decision = run.decision || {};
        el.innerHTML = `${runHeader(run)}
            <div class="orch-stack">
                ${Orch.pausedBanner?.(Orch.attention?.().dispatch_paused && ['running', 'approved'].includes(run.state)) || ''}
                ${run.state === 'proposed' ? `<article class="w-card orch-card" data-state="${decision.level === 'deny' ? 'danger' : 'attention'}"><header class="w-card__head"><span class="w-chip" data-tone="${decision.level === 'deny' ? 'danger' : 'attention'}"><span class="w-chip__glyph" aria-hidden="true">${decision.level === 'deny' ? '✗' : '◆'}</span><span>${decision.level === 'deny' ? 'Denied' : 'Needs you'}</span></span><h2 class="w-card__title" id="orch-approve-h" tabindex="-1">${esc(decision.level === 'deny' ? 'The access policy denies this run' : `Approve ${run.target} on ${run.blast_radius || ui.plural(run.nodes, 'node')}?`)}</h2></header>
                    <div class="w-card__body"><p>${esc(decision.summary || '')}</p><p class="orch-sub">Proposed by ${esc(run.proposed_by)} · ${ui.time(run.created_at)}</p></div></article>` : ''}
                ${run.state === 'paused' ? ui.banner('attention', '◆', `<strong>Paused.</strong> ${esc(run.paused_reason || 'Waiting for you.')} Continue, roll back or cancel.`) : ''}
                ${run.reason && !['proposed', 'paused'].includes(run.state) ? `<p class="orch-muted">${esc(run.reason)}</p>` : ''}
                <div class="orch-toolbar">${ui.actionBar(run.actions, { label: 'Run actions' })}</div>
                ${run.state === 'proposed' ? planView(run, i => names[i] || i) : `
                    <dl class="w-stats orch-stats">
                        <div class="w-stat"><dt class="w-stat__label">Nodes</dt><dd class="w-stat__value">${esc(sum.total_nodes || run.nodes || 0)}</dd></div>
                        <div class="w-stat" data-tone="ok"><dt class="w-stat__label">Done</dt><dd class="w-stat__value">${esc(ncount.done || 0)}</dd></div>
                        <div class="w-stat" data-tone="running"><dt class="w-stat__label">Running</dt><dd class="w-stat__value">${esc(ncount.active || 0)}</dd></div>
                        <div class="w-stat" data-tone="danger"><dt class="w-stat__label">Failed</dt><dd class="w-stat__value">${esc(ncount.failed || 0)}</dd></div>
                        <div class="w-stat"><dt class="w-stat__label">Batch</dt><dd class="w-stat__value">${esc(sum.batches ? `${Math.min((sum.batch || 0) + 1, sum.batches)}/${sum.batches}` : '—')}</dd></div>
                    </dl>
                    <section class="orch-section" aria-labelledby="orch-grid-h"><header class="orch-section__head"><h2 class="orch-section__title" id="orch-grid-h">Nodes</h2><span class="orch-sub">Select a cell for its log and output.</span></header><div data-grid>${gridTable(run)}</div></section>`}
                <div class="orch-grid-2">
                    <section class="orch-section" aria-labelledby="orch-notes-h"><header class="orch-section__head"><h2 class="orch-section__title" id="orch-notes-h">Timeline</h2></header>${Orch.catalog.notesTimeline(run.notes)}</section>
                    <section class="orch-section" aria-labelledby="orch-req-h"><header class="orch-section__head"><h2 class="orch-section__title" id="orch-req-h">Request</h2></header>
                        <div class="w-card orch-card"><div class="w-card__body">${ui.kv([
                            ['Decision', `${esc(decision.summary || '')}`, true], ['Proposed by', run.proposed_by], ['Approved by', run.approved_by], ['Created', ui.when(run.created_at)], ['Started', run.started_at ? ui.when(run.started_at) : ''], ['Finished', run.finished_at ? ui.when(run.finished_at) : ''],
                            ['Selector', `<code class="orch-mono orch-break">${esc(JSON.stringify(run.selector || {}))}</code>`, true], ['Strategy', strategyText(run.strategy)],
                            ['Parameters', `<code class="orch-mono orch-break">${esc(JSON.stringify(run.params || {}))}</code>`, true],
                            ['Rollback', run.rollback_available ? 'available' : 'not available'],
                        ])}</div></div>
                        ${run.state !== 'proposed' && (run.batches || []).length ? `<h3 class="orch-subhead">Batches</h3><ol class="orch-batches">${run.batches.map((b, i) => `<li><span class="orch-label-text">Batch ${i + 1}</span> ${b.map(n => `<span class="w-chip" data-variant="meta">${esc(names[n] || n)}</span>`).join('')}</li>`).join('')}</ol>` : ''}
                        ${run.state !== 'proposed' && (run.excluded || []).length ? `<h3 class="orch-subhead">Excluded nodes</h3>${ui.table({ label: 'Excluded nodes', stack: false, cols: [{ label: 'Node' }, { label: 'Why' }], rows: run.excluded.map(x => [esc(x.name || x.node_id), esc(x.reason)]) })}` : ''}
                        ${(run.warnings || []).length && run.state !== 'proposed' ? run.warnings.map(w => ui.banner('caution', '◆', esc(w))).join('') : ''}
                    </section>
                </div>
            </div>`;
        ui.bindActions(el, run.actions, { args: { run_id: run.id }, subject: '', done: runDone(run) });
        const bindGrid = () => {
            el.querySelectorAll('[data-exec]').forEach(b => b.addEventListener('click', () => openExecution(b.dataset.exec)));
            el.querySelectorAll('[data-retry]').forEach(b => b.addEventListener('click', async () => {
                const name = names[b.dataset.retry] || b.dataset.retry;
                if (!await ui.confirm({ title: `Retry on ${name}?`, message: 'The failed steps are dispatched to this node again.', confirmLabel: 'Retry' })) return;
                try { await ui.command('retryNode', { run_id: run.id, node_id: b.dataset.retry }, { ok: `Retrying on ${name}`, button: b }); Orch.refresh(); } catch (_) { /* toasted */ }
            }));
        };
        bindGrid();
        if (run.state === 'proposed' && route.query.get('focus') !== 'none') el.querySelector('#orch-approve-h')?.focus({ preventScroll: true });
        return {
            onChange(change) {
                if (change.scope !== 'run' || (change.id && change.id !== run.id)) return;
                Orch.refreshSoon();
                if (drawer) openExecution(drawer.id, { refresh: true });
            },
        };
    }
    Orch.refreshSoon = ui.debounce(() => Orch.refresh(), 300);

    /* ------------------------------------------------------------ approvals */
    async function renderApprovals(el, route, { isCurrent }) {
        const a = await api.get('approvals');
        if (!isCurrent()) return null;
        const total = a.runs.length + a.paused.length + a.reviews.length + a.runbook_reviews.length + a.nodes.length;
        el.innerHTML = `
            <header class="w-page__head orch-head">
                <div><h1 class="w-page__title">Approvals</h1><p class="w-page__lede">Everything waiting for your decision. The AI proposes; only you approve, publish and accept.</p></div>
            </header>
            <div class="orch-stack">
                ${!total ? ui.empty('You’re all caught up', 'No runs, reviews or nodes are waiting for you.', { icon: 'circle-check', tone: 'ok', size: '' }) : ''}
                ${a.runs.length ? `<section class="orch-section" aria-labelledby="ap-runs"><header class="orch-section__head"><h2 class="orch-section__title" id="ap-runs">Proposed runs</h2><span class="w-count" data-tone="attention">${a.runs.length}</span></header>
                    <div class="orch-stack">${a.runs.map((r, i) => {
                        const names = Object.fromEntries((r.grid || []).map(g => [g.node_id, g.name]));
                        const deny = r.decision?.level === 'deny';
                        return `<article class="w-card orch-card orch-approval" data-state="${deny ? 'danger' : 'attention'}" data-run-card="${i}" aria-labelledby="ap-run-${i}">
                            <header class="w-card__head"><span class="w-chip" data-tone="${deny ? 'danger' : 'attention'}"><span class="w-chip__glyph" aria-hidden="true">${deny ? '✗' : '◆'}</span><span>${deny ? 'Denied' : 'Needs you'}</span></span>${ui.effectChip(r.effect_style, r.effect)}<h3 class="w-card__title" id="ap-run-${i}"><a href="${runHref(r)}">${esc(r.title)}</a></h3><span class="orch-spacer"></span><span class="w-card__meta">by ${esc(r.proposed_by)} · ${ui.time(r.created_at)}</span></header>
                            <div class="w-card__body"><details class="orch-details" ${a.runs.length === 1 ? 'open' : ''}><summary>Plan: ${esc(r.blast_radius || ui.plural(r.nodes, 'node'))} · ${esc(r.decision?.summary || '')}</summary>${planView(r, x => names[x] || x)}</details></div>
                            <footer class="w-card__foot">${ui.actionButtons(r.actions, { only: ['rejectRun', 'approveRun'], primaryLast: true })}</footer>
                        </article>`;
                    }).join('')}</div></section>` : ''}
                ${a.paused.length ? `<section class="orch-section" aria-labelledby="ap-paused"><header class="orch-section__head"><h2 class="orch-section__title" id="ap-paused">Paused runs</h2><span class="w-count" data-tone="attention">${a.paused.length}</span></header>
                    <div class="orch-stack">${a.paused.map((r, i) => `<article class="w-card orch-card" data-state="attention" data-paused-card="${i}">
                        <header class="w-card__head">${ui.chip(r.style)}<h3 class="w-card__title"><a href="${runHref(r)}">${esc(r.title)}</a></h3><span class="orch-spacer"></span><span class="w-card__meta">${progressText(r)}</span></header>
                        <div class="w-card__body"><p>${esc(r.paused_reason || 'Waiting for you to continue, roll back or cancel.')}</p></div>
                        <footer class="w-card__foot">${r.actions ? ui.actionButtons(r.actions, { only: ['cancelRun', 'rollbackRun', 'continueRun'], showDisabled: false, primaryLast: true }) : `<a class="w-btn" data-variant="tonal" data-tone="attention" data-size="sm" href="${runHref(r)}">Decide <span class="w-btn__chevron" aria-hidden="true">›</span></a>`}</footer></article>`).join('')}</div></section>` : ''}
                ${a.reviews.length ? `<section class="orch-section" aria-labelledby="ap-reviews"><header class="orch-section__head"><h2 class="orch-section__title" id="ap-reviews">Tools in review</h2><span class="w-count" data-tone="attention">${a.reviews.length}</span></header>
                    ${ui.table({ label: 'Tools in review', cols: [{ label: 'Tool' }, { label: 'Effect' }, { label: 'Checks' }, { label: 'Submitted by' }, { label: 'Actions', sr: true, cls: 'orch-actions-col' }],
                        rows: a.reviews.map(t => [`<a href="${Orch.catalog.toolHref(t)}">${esc(t.title)}</a><span class="orch-sub orch-mono">${esc(t.ref)}</span>`, ui.effectChip(t.effect_style, t.effect),
                            `<span class="orch-head__meta">${Orch.catalog.lintSummary(t)}${Orch.catalog.testsSummary(t)}</span>`, esc(t.created_by), `<a class="w-btn" data-variant="tonal" data-tone="attention" data-size="sm" href="${Orch.catalog.toolHref(t)}?tab=diff">Review <span class="w-btn__chevron" aria-hidden="true">›</span></a>`]) })}</section>` : ''}
                ${a.runbook_reviews.length ? `<section class="orch-section" aria-labelledby="ap-rb"><header class="orch-section__head"><h2 class="orch-section__title" id="ap-rb">Runbooks in review</h2><span class="w-count" data-tone="attention">${a.runbook_reviews.length}</span></header>
                    ${ui.table({ label: 'Runbooks in review', cols: [{ label: 'Runbook' }, { label: 'Effect' }, { label: 'Steps', num: true }, { label: 'Problems' }, { label: 'Actions', sr: true, cls: 'orch-actions-col' }],
                        rows: a.runbook_reviews.map((r, i) => [`<a href="${Orch.catalog.runbookHref(r)}">${esc(r.title)}</a><span class="orch-sub orch-mono">${esc(r.ref)}</span>`, ui.effectChip(r.effect_style, r.effect), String((r.steps || []).length),
                            (r.problems || []).length ? `<span class="orch-error">${r.problems.map(esc).join('; ')}</span>` : '<span class="orch-muted">—</span>',
                            `<span class="w-btn-row" data-rb-actions="${i}">${ui.actionButtons(r.actions, { only: ['requestRunbookChanges', 'publishRunbook'], size: 'sm', primaryLast: true })}</span>`]) })}</section>` : ''}
                ${a.nodes.length ? `<section class="orch-section" aria-labelledby="ap-nodes"><header class="orch-section__head"><h2 class="orch-section__title" id="ap-nodes">Nodes awaiting acceptance</h2><span class="w-count" data-tone="attention">${a.nodes.length}</span></header>
                    ${ui.table({ label: 'Pending nodes', cols: [{ label: 'Node' }, { label: 'Labels' }, { label: 'Address' }, { label: 'OS' }, { label: 'Enrolled' }, { label: 'Actions', sr: true, cls: 'orch-actions-col' }],
                        rows: a.nodes.map((n, i) => [Orch.nodes.nodeName(n), ui.labelChips(n.labels), `<span class="orch-mono">${esc(n.address || '')}</span>`, esc(n.summary?.os || '—'), ui.time(n.created_at),
                            `<span class="w-btn-row" data-node-actions="${i}">${ui.actionButtons(n.actions, { only: ['revokeNode', 'acceptNode'], size: 'sm', primaryLast: true })}</span>`]) })}</section>` : ''}
            </div>`;
        const done = () => { Orch.refresh(); Orch.refreshAttention?.(); };
        el.querySelectorAll('[data-run-card]').forEach(card => { const r = a.runs[Number(card.dataset.runCard)]; ui.bindActions(card, r.actions, { args: { run_id: r.id }, done }); });
        el.querySelectorAll('[data-paused-card]').forEach(card => { const r = a.paused[Number(card.dataset.pausedCard)]; if (r.actions) ui.bindActions(card, r.actions, { args: { run_id: r.id }, done }); });
        el.querySelectorAll('[data-rb-actions]').forEach(span => { const r = a.runbook_reviews[Number(span.dataset.rbActions)]; ui.bindActions(span, r.actions, { args: { ref: r.ref }, subject: r.ref, done }); });
        el.querySelectorAll('[data-node-actions]').forEach(span => { const n = a.nodes[Number(span.dataset.nodeActions)]; ui.bindActions(span, n.actions, { args: { node_id: n.id }, subject: n.name, done }); });
        return null;
    }

    Orch.runs = { paramsForm, planView, runsTable, openExecution, effectStyle };
    Orch.routes.runs = { nav: 'runs', title: 'Runs', scopes: ['run'], render: renderList, crumbs: () => [{ label: 'Runs' }] };
    Orch.routes.run = { nav: 'runs', title: 'Run', scopes: ['run'], render: renderRun, crumbs: r => [{ label: 'Runs', to: '#/runs' }, { label: r.parts[0] || 'Run' }] };
    Orch.routes['new-run'] = { nav: 'runs', title: 'New run', scopes: [], render: renderWizard, crumbs: () => [{ label: 'Runs', to: '#/runs' }, { label: 'New run' }] };
    Orch.routes.approvals = { nav: 'approvals', title: 'Approvals', scopes: ['node', 'tool', 'run'], render: renderApprovals, crumbs: () => [{ label: 'Approvals' }] };
})();
