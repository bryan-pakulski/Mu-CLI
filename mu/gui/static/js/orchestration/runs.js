/* Orchestration — Runs: runs list, the run wizard and plan view, run detail
 * (live per-node grid, execution drawer, rollbacks, batches, notes, actions)
 * (documentation/plans/orchestration.md §3.4, §5, §8). Things waiting for a decision are on
 * Now (view.js); a run row (Mu §5.1) is reused there and in the catalog's usage lists.
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
    function progressWords(r) {
        const n = (r.summary || {}).nodes;
        if (!n) return esc(ui.plural(r.nodes || 0, 'node'));
        const total = (r.summary || {}).total_nodes || r.nodes || 0;
        const pct = total ? Math.round(100 * ((n.done || 0) + (n.failed || 0)) / total) : 0;
        const bar = total ? `<span class="w-bar orch-rowbar" style="--v: ${pct}%"${n.failed ? ' data-tone="danger"' : ''} aria-hidden="true"></span>` : '';
        return bar + [`${n.done || 0} of ${total} done`, n.failed ? `<span data-tone="danger">${n.failed} failed</span>` : '', n.active ? `${n.active} running` : ''].filter(Boolean).join(' · ');
    }
    /* One run as a row: sign, title, then the state in words — progress, effect, test/check
     * mode and (only when it was the AI) who proposed it. */
    function runRow(r) {
        const st = ui.statusKey(r.style);
        const stateWord = ['waiting', 'active', 'unverified'].includes(st) || r.state === 'paused' ? ui.cap(r.style?.label || r.state || '') : '';
        return ui.row({
            status: st, title: r.title || r.id, href: runHref(r),
            sub: ui.subline([esc(stateWord), progressWords(r), r.kind === 'test' || r.kind === 'runbook_test' ? 'test' : '', r.check_mode ? 'check mode' : '',
                ui.effectWord(r.effect_style, r.effect), /^ai:/.test(String(r.proposed_by || '')) ? 'by the AI' : '',
                r.state === 'paused' && r.paused_reason ? esc(r.paused_reason) : '']),
            when: ui.time(r.created_at),
        });
    }
    function runRows(runs, { empty = 'No runs yet.' } = {}) {
        return ui.rows((runs || []).map(runRow), { empty, label: 'Runs' });
    }
    const runsTable = runRows;
    Orch.runsTable = runsTable;

    /* ------------------------------------------------------------ runs list */
    const STATE_FILTERS = [['', 'All'], ['proposed', 'Awaiting approval'], ['running,approved,rolling_back', 'Running'], ['paused', 'Paused'], ['succeeded', 'Succeeded'], ['failed', 'Failed'], ['cancelled,rejected,rolled_back,expired', 'Stopped']];
    async function renderList(el, route, { isCurrent, setQuery }) {
        const state = route.query.get('state') || '';
        const data = await api.get(`runs?limit=200${state ? `&state=${encodeURIComponent(state)}` : ''}`);
        if (!isCurrent()) return null;
        el.innerHTML = `
            <div class="orch-stack">
                <div class="orch-textfilters orch-filters" role="group" aria-label="Filter by state">${STATE_FILTERS.map(([k, l]) => `<button type="button" class="orch-textfilter" aria-pressed="${state === k}" data-state="${esc(k)}">${esc(l)}</button>`).join('')}</div>
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
            ${ui.kv([
                ['Blast radius', p.blast_radius || ui.plural(nodes.length, 'node')],
                ['Effect', ui.effectChip(es, p.effect), true],
                ['Rollback', p.rollback_available ? 'Available' : ui.chip({ semantic: 'caution', glyph: '◆', label: 'Not available' }), true],
                ['Mode', p.check_mode ? 'Check mode (dry run)' : 'Real run'],
                ['Access', `${ui.chip(ds)}<span class="orch-sub orch-access-why">${esc(decision.summary || '')}${rules.length > 1 ? ` · rules: ${rules.map(esc).join(' · ')}` : ''}</span>${(decision.reasons || []).length ? `<ul class="orch-list">${decision.reasons.map(r => `<li>${esc(typeof r === 'string' ? r : JSON.stringify(r))}</li>`).join('')}</ul>` : ''}`, true],
            ])}
            ${(p.warnings || []).length ? `<ul class="orch-warnings" role="list">${p.warnings.map(w => `<li>${ui.banner('caution', '◆', esc(w))}</li>`).join('')}</ul>` : ''}
            ${(p.secrets || []).length ? `<p class="orch-sub">Secrets passed to the node: ${p.secrets.map(s => `<code class="orch-mono">${esc(typeof s === 'string' ? s : s.name)}</code>`).join(' ')}</p>` : ''}
            <div class="orch-grid-2">
                <section class="orch-section"><header class="orch-section__head"><h3 class="orch-section__title">${esc(ui.plural(nodes.length, 'node'))}</h3></header>
                    ${ui.table({ label: 'Resolved nodes', empty: 'No nodes resolved.', stack: false, cols: [{ label: 'Node' }, { label: 'Labels' }, { label: 'Decision' }],
                        rows: nodes.map(n => { const d = perNode.get(n.id); return [`<a href="#/node/${encodeURIComponent(n.id)}">${esc(n.name || n.id)}</a>`, ui.labelChips(n.labels), d ? `<span title="${esc(d.rule || '')}">${ui.chip(DECISION[d.level] || { semantic: 'neutral', glyph: '○', label: d.level }, { sm: true })}</span>` : '—']; }) })}</section>
                <section class="orch-section"><header class="orch-section__head"><h3 class="orch-section__title">Excluded</h3></header>
                    ${ui.table({ label: 'Excluded nodes', empty: 'No matched node was excluded.', stack: false, cols: [{ label: 'Node' }, { label: 'Why' }],
                        rows: (p.excluded || []).map(x => [`<a href="#/node/${encodeURIComponent(x.node_id)}">${esc(x.name || x.node_id)}</a>`, esc(x.reason)]) })}</section>
            </div>
            <section class="orch-section"><header class="orch-section__head"><h3 class="orch-section__title">Batches</h3><span class="orch-sub">${esc(strategyText(p.strategy))}</span></header>
                ${(p.batches || []).length ? `<ol class="orch-batches">${p.batches.map((b, i) => `<li><span class="orch-label-text">${i === 0 && (p.strategy || {}).canary ? 'Canary' : `Batch ${i + 1}`}</span> ${b.map(id => esc(nameOf(id))).join(', ')}</li>`).join('')}</ol>` : '<p class="orch-muted">No batches.</p>'}</section>
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
            <header class="orch-head"><h1 class="w-page__title">New run</h1></header>
            <div class="orch-detail orch-wizard-layout">
                <div class="orch-detail__main">
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
                </div>
                <aside class="orch-detail__aside" aria-label="Plan">
                <p class="orch-muted" data-plan-placeholder>Preview the plan to see the nodes it reaches, its batches and the access decision before you propose it.</p>
                <section class="orch-section" aria-labelledby="wz-plan-h" data-plan-section hidden>
                    <header class="orch-section__head"><h2 class="orch-section__title" id="wz-plan-h" tabindex="-1">Review the plan</h2></header>
                    <div data-plan></div>
                    <div class="orch-propose">
                        <div class="w-field"><label class="w-field__label" for="wz-title">Run title <span class="orch-muted">(optional)</span></label><input class="w-input" id="wz-title" autocomplete="off"></div>
                        <div class="w-btn-row"><button type="button" class="w-btn" data-variant="primary" data-propose>${icon('send')}<span>Propose run</span></button></div>
                    </div>
                </section>
                </aside>
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
                el.querySelector('[data-plan-placeholder]')?.remove();
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

    /* One execution: state, reason, exit, duration, output and the log (the drawer on narrow
     * leaves, the run's ASIDE on wide ones). */
    function execBody(x) {
        return `<div class="orch-stack">
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
        body.innerHTML = execBody(x);
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

    const KIND_WORD = { test: 'test run', runbook_test: 'runbook test', agent_update: 'agent update', runbook: 'runbook run' };
    function runHeader(run) {
        return `<header class="orch-head"><h1 class="w-page__title">${esc(run.title || run.id)}</h1></header>`;
    }
    /* The detail page's state sentence: the state in words, then what it is. */
    function runState(run) {
        const st = ui.statusKey(run.style);
        return [
            { text: ui.cap(run.style?.label || run.state || ''), tone: st === 'waiting' ? 'attention' : (st === 'failed' ? 'danger' : '') },
            KIND_WORD[run.kind] ? { text: KIND_WORD[run.kind] } : null,
            run.effect_style ? { text: run.effect_style.label, tone: run.effect === 'destructive' ? 'danger' : '' } : null,
            run.check_mode ? { text: 'check mode' } : null,
            String(run.title || '').includes(run.blast_radius || '\u0000') ? null : { text: run.blast_radius || ui.plural(run.nodes || 0, 'node') },
        ].filter(Boolean);
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
        const deny = decision.level === 'deny';
        const decisionIds = run.state === 'proposed' ? ['approveRun', 'rejectRun'] : (run.state === 'paused' ? ['continueRun', 'rollbackRun', 'cancelRun'] : []);
        const totalNodes = sum.total_nodes || run.nodes || 0;
        const pct = totalNodes ? Math.round(100 * ((ncount.done || 0) + (ncount.failed || 0)) / totalNodes) : 0;
        const progress = [
            ui.plural(sum.total_nodes || run.nodes || 0, 'node'), ncount.done ? `${ncount.done} done` : '', ncount.active ? `${ncount.active} running` : '',
            ncount.failed ? `<span class="w-state__part" data-tone="danger">${esc(ncount.failed)} failed</span>` : '',
            sum.batches ? `batch ${Math.min((sum.batch || 0) + 1, sum.batches)} of ${sum.batches}` : '',
        ].filter(Boolean).map(x => x.startsWith('<span') ? x : `<span class="w-state__part">${esc(x)}</span>`).join('');
        /* Fleet results lead a finished run (what came back matters most); while it runs they
         * follow the grid and fill in as nodes report. */
        const resultsSection = `<section class="w-group orch-results" aria-labelledby="orch-results-h"><div class="w-group__head"><h2 class="w-group__title" id="orch-results-h">Results</h2></div>
            <div data-results></div></section>`;
        el.innerHTML = `${runHeader(run)}
            <div class="orch-detail">
                <div class="orch-detail__main">
                ${run.state === 'proposed' ? `<div class="w-decision orch-decision" data-tone="${deny ? 'danger' : 'attention'}">
                    <p class="w-decision__q" id="orch-approve-h" tabindex="-1">${esc(deny ? 'The access policy denies this run' : `Approve ${run.target} on ${run.blast_radius || ui.plural(run.nodes, 'node')}?`)}</p>
                    <p class="w-decision__r">${esc(decision.summary || '')} · proposed by ${esc(run.proposed_by)} · ${ui.time(run.created_at)}</p>
                    <div class="w-decision__acts">${ui.actionButtons(run.actions, { only: decisionIds, showDisabled: false })}</div></div>` : ''}
                ${run.state === 'paused' ? `<div class="w-decision orch-decision" data-tone="attention">
                    <p class="w-decision__q">Continue, roll back or cancel?</p>
                    <p class="w-decision__r">${esc(run.paused_reason || 'Waiting for you.')}</p>
                    <div class="w-decision__acts">${ui.actionButtons(run.actions, { only: decisionIds, showDisabled: false })}</div></div>` : ''}
                ${run.reason && !['proposed', 'paused'].includes(run.state) ? `<p class="orch-muted">${esc(run.reason)}</p>` : ''}
                ${run.finished_at ? resultsSection : ''}
                ${run.state === 'proposed' ? planView(run, i => names[i] || i) : `
                    <section class="w-group" aria-labelledby="orch-grid-h"><div class="w-group__head"><h2 class="w-group__title" id="orch-grid-h">Nodes</h2></div>
                        <div class="orch-progressline"><span class="w-bar" style="--v: ${esc(pct)}%; --w-bar-w: 100%"${ncount.failed ? ' data-tone="danger"' : ''} role="img" aria-label="${esc(pct)}% of nodes done"></span><p class="w-state orch-progress">${progress}</p></div>
                        <div data-grid>${gridTable(run)}</div>
                        <p class="orch-sub">Select a cell for its log and output.</p></section>
                    ${run.finished_at ? '' : resultsSection}`}
                <section class="w-group" aria-labelledby="orch-notes-h"><h2 class="w-group__title" id="orch-notes-h">Timeline</h2>${Orch.catalog.notesTimeline(run.notes)}</section>
                </div>
                <aside class="orch-detail__aside" aria-label="About this run">
                    <div class="orch-toolbar">${ui.actionBar(run.actions, { label: 'Run actions', exclude: decisionIds })}</div>
                    ${run.state === 'proposed' ? '' : '<section class="w-group orch-exec" data-exec-panel aria-live="polite"></section>'}
                    ${ui.kv(run.state === 'proposed'
                        /* While it waits, the plan beside shows effect, mode, rollback, access and batches. */
                        ? [['Target', targetLink(run.target, run.kind), true], ['Proposed by', run.proposed_by], ['Created', ui.when(run.created_at)]]
                        : [
                            ['Target', targetLink(run.target, run.kind), true],
                            ['Effect', ui.effectChip(run.effect_style || effectStyle(run.effect), run.effect), true],
                            ['Mode', run.check_mode ? 'Check mode (dry run)' : 'Real run'],
                            ['Decision', `${esc(decision.summary || '')}`, true], ['Proposed by', run.proposed_by], ['Approved by', run.approved_by],
                            ['Created', ui.when(run.created_at)], ['Started', run.started_at ? ui.when(run.started_at) : ''], ['Finished', run.finished_at ? ui.when(run.finished_at) : ''],
                            ['Strategy', strategyText(run.strategy)], ['Rollback', run.rollback_available ? 'available' : 'not available'],
                        ])}
                    <details class="w-disclose orch-run-details"><summary>Details</summary>
                        ${ui.kv([
                            ['Run', `<span class="w-mono">${esc(run.id)}</span>`, true],
                            ['Selector', `<code class="orch-mono orch-break">${esc(JSON.stringify(run.selector || {}))}</code>`, true],
                            ['Parameters', `<code class="orch-mono orch-break">${esc(JSON.stringify(run.params || {}))}</code>`, true],
                            ['Batches', run.state !== 'proposed' && (run.batches || []).length ? run.batches.map((b, i) => `Batch ${i + 1}: ${b.map(n => esc(names[n] || n)).join(', ')}`).join('<br>') : '', true],
                            ['Excluded', run.state !== 'proposed' && (run.excluded || []).length ? run.excluded.map(x => `${esc(x.name || x.node_id)}: ${esc(x.reason)}`).join('<br>') : '', true],
                        ])}
                        ${(run.warnings || []).length && run.state !== 'proposed' ? run.warnings.map(w => ui.banner('caution', '◆', esc(w))).join('') : ''}
                    </details>
                </aside>
            </div>`;
        Orch.itemState(runState(run));
        ui.bindActions(el, run.actions, { args: { run_id: run.id }, subject: '', done: runDone(run) });
        /* The selected execution sits at the top of the run's ASIDE when it shows (wide leaves,
         * or the Details toggle); otherwise a cell opens the drawer. ?exec=<id> keeps the choice. */
        const panel = el.querySelector('[data-exec-panel]');
        const allExecs = (run.grid || []).flatMap(g => g.cells.filter(c => c.exec).map(c => ({ node: g, cell: c })));
        const asideShows = () => { const a = document.getElementById('orch-aside'); return !!a && a.getClientRects().length > 0 && getComputedStyle(a).display !== 'none'; };
        const pickDefault = () => {
            const wanted = route.query.get('exec');
            if (wanted && allExecs.some(e => e.cell.exec.id === wanted)) return wanted;
            const bad = allExecs.find(e => RETRYABLE.includes(e.cell.exec.state));
            const latest = allExecs.slice().sort((a, b) => (b.cell.exec.started_at || 0) - (a.cell.exec.started_at || 0))[0];
            return (bad || latest)?.cell.exec.id || '';
        };
        let selectedExec = pickDefault();
        const markCells = () => el.querySelectorAll('[data-exec]').forEach(b => b.toggleAttribute('data-selected', b.dataset.exec === selectedExec));
        async function showExec(id, { user = false } = {}) {
            if (!panel) return;
            selectedExec = id;
            markCells();
            if (!id) { panel.innerHTML = '<h2 class="w-group__title">Execution</h2><p class="orch-muted">Nothing has run yet.</p>'; return; }
            const prevLog = panel.querySelector('.orch-log');
            const keep = prevLog && panel.dataset.exec === id ? prevLog.scrollTop : null;
            let x;
            try { x = await api.get(`executions/${encodeURIComponent(id)}`); } catch (err) { panel.innerHTML = ui.errorState(err, { retry: false }); return; }
            if (selectedExec !== id) return;
            const node = (run.grid || []).find(g => g.node_id === x.node_id);
            panel.dataset.exec = id;
            panel.innerHTML = `<div class="w-group__head"><h2 class="w-group__title">${esc(node?.name || x.node_id)} · ${esc(x.step_name || `step ${x.step + 1}`)}</h2><span class="orch-sub orch-mono">${esc(x.tool)}</span></div>${execBody(x)}`;
            const log = panel.querySelector('.orch-log');
            if (log) log.scrollTop = keep != null ? keep : log.scrollHeight;
            if (user) Orch.setQuery({ exec: id });
        }
        if (panel) showExec(selectedExec);
        /* Fleet results: every node's outputs as one table (mu/orchestration/results.py);
         * choosing a node shows its execution like a grid cell does. */
        Orch.results?.mount(el.querySelector('[data-results]'), {
            url: `runs/${encodeURIComponent(run.id)}/results`,
            csv: (step, view) => `/api/orchestration/runs/${encodeURIComponent(run.id)}/results.csv?step=${encodeURIComponent(step)}&view=${encodeURIComponent(view)}`,
            empty: ['running', 'approved', 'scheduled'].includes(run.state) ? 'Results appear here as nodes report back.' : 'No node returned output.',
            onRow: row => { if (panel && asideShows()) showExec(row.exec_id, { user: true }); else openExecution(row.exec_id); },
            ask: v => `Look at the fleet results of run ${run.id} (${run.target}, ${v.label} view${v.filter ? `, filtered by "${v.filter}"` : ''}) with fleet_results and tell me what needs attention.`,
        });
        const bindGrid = () => {
            el.querySelectorAll('[data-exec]').forEach(b => b.addEventListener('click', () => {
                if (panel && asideShows()) showExec(b.dataset.exec, { user: true });
                else openExecution(b.dataset.exec);
            }));
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
    Orch.runs = { paramsForm, planView, runsTable, runRow, runRows, openExecution, effectStyle };
    Orch.routes.runs = { nav: 'runs', title: 'Runs', scopes: ['run'], render: renderList, crumbs: () => [{ label: 'Runs' }] };
    Orch.routes.run = { nav: 'runs', title: 'Run', scopes: ['run'], render: renderRun, crumbs: r => [{ label: 'Runs', to: '#/runs' }, { label: r.parts[0] || 'Run' }] };
    Orch.routes['new-run'] = { nav: 'runs', title: 'New run', scopes: [], render: renderWizard, crumbs: () => [{ label: 'Runs', to: '#/runs' }, { label: 'New run' }] };
})();
