/* Orchestration — Catalog: tools by category, tool version detail (overview,
 * manifest, files, diff, lint, tests, versions, notes, usage) and runbooks
 * (documentation/plans/orchestration.md §3.2, §3.3, §4 review, §8).
 */
(() => {
    'use strict';
    const api = window.OrchAPI;
    const ui = window.OrchUI;
    const { esc, icon } = ui;
    const Orch = (window.Orch = window.Orch || { routes: {} });
    Orch.routes = Orch.routes || {};

    const OPEN = ['draft', 'testing', 'in_review'];
    const toolHref = v => `#/tool/${encodeURIComponent(v.tool_id)}/${encodeURIComponent(v.version)}`;
    const runbookHref = r => `#/runbook/${encodeURIComponent(r.runbook_id)}/${encodeURIComponent(r.version)}`;

    /* ------------------------------------------------------------ schema table */
    function typeOf(p) {
        if (!p) return '';
        const t = Array.isArray(p.type) ? p.type.join(' | ') : (p.type || (p.enum ? 'enum' : 'any'));
        if (t === 'array' && p.items) return `array of ${typeOf(p.items)}`;
        return t;
    }
    function constraints(p) {
        const out = [];
        if (p.enum) out.push(`one of ${p.enum.map(v => JSON.stringify(v)).join(', ')}`);
        if (p.minimum != null) out.push(`≥ ${p.minimum}`);
        if (p.maximum != null) out.push(`≤ ${p.maximum}`);
        if (p.minLength != null) out.push(`min length ${p.minLength}`);
        if (p.maxLength != null) out.push(`max length ${p.maxLength}`);
        if (p.pattern) out.push(`matches ${p.pattern}`);
        if (p.maxItems != null) out.push(`≤ ${p.maxItems} items`);
        return out.join(' · ');
    }
    function schemaTable(schema, { empty = 'This tool takes no inputs.' } = {}) {
        const props = Object.entries((schema || {}).properties || {});
        const required = new Set((schema || {}).required || []);
        return ui.table({
            label: 'Inputs', empty,
            cols: [{ label: 'Name' }, { label: 'Type' }, { label: 'Default' }, { label: 'Constraints' }, { label: 'Description' }],
            rows: props.map(([name, p]) => [`<code class="orch-mono">${esc(name)}</code>${required.has(name) ? ' <span class="orch-tag">required</span>' : ''}`,
                esc(typeOf(p)), p.default !== undefined ? `<code class="orch-mono">${esc(JSON.stringify(p.default))}</code>` : '<span class="orch-muted">—</span>', esc(constraints(p)), esc(p.description || '')]),
        });
    }

    function notesTimeline(notes) {
        if (!(notes || []).length) return '<p class="orch-muted">No notes yet.</p>';
        const TONE = { ok: ['ok', '✓'], danger: ['danger', '✗'], attention: ['attention', '◆'], running: ['running', '●'], warn: ['attention', '◆'], info: ['info', '●'] };
        return `<ol class="orch-timeline">${notes.map(n => {
            const [tone, glyph] = TONE[n.tone] || ['neutral', '○'];
            return `<li class="orch-timeline__item"><span class="w-status orch-timeline__glyph" data-tone="${esc(tone)}"><span class="w-status__glyph" aria-hidden="true">${glyph}</span></span><div class="orch-timeline__body"><p>${esc(n.text)}</p><p class="orch-sub">${ui.time(n.at)} · ${esc(n.actor || '')}${n.action ? ` · <span class="orch-mono">${esc(n.action)}</span>` : ''}</p></div></li>`;
        }).join('')}</ol>`;
    }

    function lintTable(lint) {
        const findings = (lint || {}).findings || [];
        const SEV = { error: { status: 'failed', glyph: '✗' }, warning: { status: 'unverified', glyph: '◆' }, info: { status: 'pending', glyph: '●' } };
        return ui.table({
            label: 'Lint findings', empty: lint && !lint.stale && lint.at ? 'No findings. Lint passed.' : 'Lint hasn’t run on the current content yet.',
            cols: [{ label: 'Severity' }, { label: 'Rule' }, { label: 'Where' }, { label: 'Message' }],
            rows: findings.map(f => {
                const sev = SEV[f.severity] || { status: 'pending', glyph: '○' };
                return [ui.chip({ ...sev, label: ui.cap(f.severity) }),
                    `<code class="orch-mono">${esc(f.rule)}</code>`, `<span class="orch-mono">${esc(f.file || '')}${f.line ? `:${esc(f.line)}` : ''}</span>`, esc(f.message)];
            }),
        });
    }
    function lintSummary(t) {
        const l = t.lint || {};
        const c = l.counts || {};
        if (!t.lint_result && !l.current && !l.ok) return ui.chip({ semantic: 'neutral', glyph: '○', label: 'Lint not run' });
        if (!l.ok) return ui.chip({ semantic: 'danger', glyph: '✗', label: `Lint: ${ui.plural(c.error || 0, 'error')}` });
        if (!l.current) return ui.chip({ semantic: 'caution', glyph: '◆', label: 'Lint stale' }, { title: 'The draft changed since lint last ran' });
        return ui.chip({ semantic: 'ok', glyph: '✓', label: c.warning ? `Lint passed · ${ui.plural(c.warning, 'warning')}` : 'Lint passed' });
    }
    function testsSummary(t) {
        const s = t.tests || {};
        if (!s.count) return ui.chip({ semantic: 'neutral', glyph: '○', label: 'Not tested' });
        const last = s.last || {};
        return ui.chip(last.passed ? { semantic: 'ok', glyph: '✓', label: `Last test passed · ${s.passed}/${s.count}` } : { semantic: 'danger', glyph: '✗', label: `Last test failed · ${s.passed}/${s.count}` });
    }
    function testsTable(tool) {
        const tests = ((tool.review || {}).tests || []).slice().reverse();
        if (!tests.length) return '<p class="orch-muted">No test runs yet. Test on a canary node in check mode before submitting for review.</p>';
        return tests.map(t => `<article class="w-card orch-card orch-test" data-state="${t.passed ? 'ok' : 'danger'}">
            <header class="w-card__head">${ui.chip(t.passed ? { semantic: 'ok', glyph: '✓', label: 'Passed' } : { semantic: 'danger', glyph: '✗', label: 'Failed' })}<h3 class="w-card__title">${esc(t.name || 'ad-hoc')}</h3>${t.check_mode ? ui.meta('check mode') : ui.meta('real run')}<span class="orch-spacer"></span><span class="w-card__meta">${ui.time(t.at)} · <a href="#/run/${encodeURIComponent(t.run_id)}">run</a></span></header>
            <div class="w-card__body">${t.digest && t.digest !== tool.manifest_digest ? '<p class="orch-sub"><span aria-hidden="true">◆</span> Ran against an earlier revision of this draft.</p>' : ''}
            ${ui.table({ label: 'Test results', stack: false, cols: [{ label: 'Node' }, { label: 'State' }, { label: 'Exit', num: true }, { label: 'Duration', num: true }, { label: 'Problems' }],
                rows: (t.results || []).map(r => [`<a class="orch-mono" href="#/node/${encodeURIComponent(r.node_id)}">${esc(r.node_id)}</a>`, ui.chip(ui.vstyle('orch_exec_state', r.state)), r.exit_code == null ? '—' : esc(r.exit_code), esc(ui.dur(r.duration_s)), (r.problems || []).map(esc).join('<br>') || '<span class="orch-muted">—</span>']) })}</div></article>`).join('');
    }
    function expectationsTable(manifest) {
        const tests = (manifest || {}).tests || [];
        return ui.table({ label: 'Declared tests', empty: 'The manifest declares no tests.', stack: false,
            cols: [{ label: 'Name' }, { label: 'Params' }, { label: 'Check mode' }, { label: 'Expect' }],
            rows: tests.map(t => [esc(t.name), `<code class="orch-mono">${esc(JSON.stringify(t.params || {}))}</code>`, t.check_mode === false ? 'no' : 'yes', `<code class="orch-mono">${esc(JSON.stringify(t.expect || {}))}</code>`]) });
    }

    /* ------------------------------------------------------------ list */
    async function renderList(el, route, { isCurrent, setQuery, soft }) {
        const data = await api.get('tools?all=true');
        if (!isCurrent()) return null;
        const show = route.query.get('show') || 'published';
        const search = soft ? (el.querySelector('#orch-cat-search')?.value ?? '') : (route.query.get('q') || '');
        const byTool = new Map();
        data.tools.forEach(v => { if (!byTool.has(v.tool_id)) byTool.set(v.tool_id, []); byTool.get(v.tool_id).push(v); });
        byTool.forEach(list => list.sort((a, b) => a.version - b.version));
        el.innerHTML = `
            <header class="orch-head"><h1 class="w-page__title">Catalog</h1></header>
            <div class="orch-stack">
                <div class="orch-filters">
                    <div class="w-segmented" role="group" aria-label="Show">
                        ${[['published', 'Published'], ['open', 'Drafts and reviews'], ['all', 'All versions']].map(([k, l]) => `<button type="button" class="w-segmented__option" aria-pressed="${show === k}" data-show="${k}">${l}</button>`).join('')}
                    </div>
                    <label class="w-visually-hidden" for="orch-cat-search">Search tools</label>
                    <input class="w-input orch-search" id="orch-cat-search" type="search" placeholder="Search tools" value="${esc(search)}" autocomplete="off">
                </div>
                <div data-cat-body></div>
                <section class="orch-section" aria-labelledby="orch-rb-h">
                    <header class="orch-section__head"><h2 class="orch-section__title" id="orch-rb-h" title="Ordered steps over pinned tool versions">Runbooks</h2><span class="orch-spacer"></span><a class="w-textbtn" href="#/studio/runbook/new">New runbook</a></header>
                    ${ui.rows(data.runbooks.map(r => ui.row({
                        status: ui.statusKey(r.style), title: r.title, href: runbookHref(r),
                        sub: ui.subline([`<span class="w-mono">${esc(r.runbook_id)} v${esc(r.version)}</span>`, ['published', 'deprecated'].includes(r.status) ? '' : esc(r.style?.label || r.status),
                            ui.plural((r.steps || []).length, 'step'), ui.effectWord(r.effect_style, r.effect),
                            (r.problems || []).length ? `<span data-tone="danger" title="${esc(r.problems.join('\n'))}">${esc(ui.plural(r.problems.length, 'problem'))}</span>` : '']),
                    })), { label: 'Runbooks', empty: 'No runbooks yet. Write one in the studio, or let the AI draft it in the studio chat.' })}
                </section>
            </div>`;
        const paint = () => {
            const q = el.querySelector('#orch-cat-search').value.toLowerCase();
            const mode = route.query.get('show') || 'published';
            const tools = [...byTool.values()].map(list => {
                const visible = list.filter(v => mode === 'all' || (mode === 'open' ? OPEN.includes(v.status) : ['published', 'deprecated'].includes(v.status)));
                return { list, visible, head: visible[visible.length - 1] || list[list.length - 1] };
            }).filter(t => t.visible.length && (!q || [t.head.tool_id, t.head.title, t.head.summary, t.head.category].join(' ').toLowerCase().includes(q)));
            const cats = new Map();
            tools.forEach(t => { const c = t.head.category || 'uncategorised'; if (!cats.has(c)) cats.set(c, []); cats.get(c).push(t); });
            const body = el.querySelector('[data-cat-body]');
            if (!tools.length) {
                body.innerHTML = ui.empty(mode === 'open' ? 'No open drafts' : 'No tools match', mode === 'open' ? 'Start one in the studio. The AI helps you write the spec, script and tests.' : 'Try another filter, or install the starter pack in Settings.', { icon: 'book-open', action: mode === 'open' ? '<a class="w-btn" data-variant="primary" href="#/studio">Open the studio</a>' : '' });
                return;
            }
            body.innerHTML = [...cats.entries()].sort((a, b) => a[0].localeCompare(b[0])).map(([cat, list]) => `
                <section class="w-group" aria-label="${esc(ui.cap(cat))}">
                    <h2 class="w-group__title">${esc(ui.cap(cat))}</h2>
                    ${ui.rows(list.map(t => ui.row({
                        status: ui.statusKey(t.head.style), title: t.head.title, href: toolHref(t.head),
                        sub: ui.subline([`<span class="w-mono">${esc(t.head.tool_id)}</span>`, ui.effectWord(t.head.effect_style, t.head.effect),
                            t.visible.map(v => `v${esc(v.version)}${['published'].includes(v.status) ? '' : ` ${esc(v.style.label)}`}`).join(', '), esc(t.head.summary || '')]),
                    })), { label: `${cat} tools` })}
                </section>`).join('');
        };
        paint();
        el.querySelector('#orch-cat-search').addEventListener('input', ui.debounce(() => { setQuery({ q: el.querySelector('#orch-cat-search').value }); paint(); }, 150));
        el.querySelectorAll('[data-show]').forEach(b => b.addEventListener('click', () => {
            setQuery({ show: b.dataset.show === 'published' ? '' : b.dataset.show });
            el.querySelectorAll('[data-show]').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
            paint();
        }));
        return null;
    }

    /* The detail page's state sentence: status, effect, ref (Mu §4.2). */
    function versionState(v) {
        const st = ui.statusKey(v.style);
        Orch.itemState([
            { text: ui.cap(v.style?.label || v.status || ''), tone: st === 'waiting' ? 'attention' : (st === 'failed' ? 'danger' : '') },
            v.effect_style ? { text: v.effect_style.label, tone: v.effect === 'destructive' ? 'danger' : '' } : null,
            { text: v.ref },
        ].filter(Boolean));
    }

    /* ------------------------------------------------------------ tool detail */
    const TABS = ['overview', 'results', 'manifest', 'files', 'diff', 'lint', 'tests', 'versions', 'notes', 'usage'];

    function toolDone(t) {
        return (result, action) => {
            const ref = result?.data?.ref;
            if (action.id === 'newVersion' && ref) { const [id, v] = ref.split('@'); Orch.go(`#/studio/${encodeURIComponent(id)}/${encodeURIComponent(v)}`); return; }
            if (action.id === 'discardDraft') {
                const prev = (t.versions || []).filter(v => v.version !== t.version).pop();
                Orch.go(prev ? `#/tool/${encodeURIComponent(t.tool_id)}/${prev.version}` : '#/catalog');
                return;
            }
            Orch.refresh();
        };
    }
    function toolDialogs(t) {
        return {
            test: () => Orch.studio?.testDialog(t),
            run: () => Orch.go(`#/new-run?kind=tool&target=${encodeURIComponent(t.ref)}`),
        };
    }

    function overviewPanel(t) {
        const review = t.review || {};
        return `<div class="orch-stack">
            ${t.status === 'in_review' && (review.headline || (review.risks || []).length) ? `<article class="w-card orch-card" data-state="attention"><header class="w-card__head">${ui.chip({ status: 'waiting', glyph: '◆', label: 'In review' })}<h2 class="w-card__title">${esc(review.headline || 'Submitted for review')}</h2><span class="w-card__meta">by ${esc(review.submitted_by || t.created_by)} · ${ui.time(review.submitted_at)}</span></header>
                <div class="w-card__body">${(review.risks || []).length ? `<p class="orch-label-text">Risks the author called out</p><ul class="orch-list">${review.risks.map(r => `<li>${esc(r)}</li>`).join('')}</ul>` : ''}${review.notes ? `<p>${esc(review.notes)}</p>` : ''}
                <p class="orch-muted">Check the Diff, Lint and Tests tabs before publishing.${t.effect === 'destructive' ? ' Destructive tools are published by typing the tool id.' : ''}</p></div></article>` : ''}
            ${t.summary ? `<p class="orch-lede">${esc(t.summary)}</p>` : ''}
            <div class="orch-grid-2">
                <article class="w-card orch-card"><header class="w-card__head"><h2 class="w-card__title">Behaviour</h2></header><div class="w-card__body">${ui.kv([
                    ['Effect', ui.effectChip(t.effect_style, t.effect), true], ['Runs as', t.run_as || 'default'], ['Idempotent', t.idempotent ? 'yes' : 'no'],
                    ['Check mode', t.check_mode ? 'supported (dry run reports, changes nothing)' : 'not supported'],
                    ['Rollback', t.rollback ? `<code class="orch-mono">${esc(t.rollback)}</code>` : '<span class="orch-muted">none: changes can’t be undone automatically</span>', true],
                    ['Secrets', (t.secrets || []).length ? t.secrets.map(s => `<code class="orch-mono">${esc(typeof s === 'string' ? s : (s.name || JSON.stringify(s)))}</code>`).join(' ') : '<span class="orch-muted">none</span>', true],
                    ['Limits', t.manifest?.limits ? Object.entries(t.manifest.limits).map(([k, v]) => `${k}=${v}`).join(' · ') : ''],
                    ['Targets', t.manifest?.targets ? [(t.manifest.targets.os || []).join(', '), (t.manifest.targets.requires || []).length ? `requires ${t.manifest.targets.requires.join(', ')}` : ''].filter(Boolean).join(' · ') : ''],
                ])}</div></article>
                <article class="w-card orch-card"><header class="w-card__head"><h2 class="w-card__title">Provenance</h2></header><div class="w-card__body">${ui.kv([
                    ['Category', t.category], ['Origin', t.origin], ['Created by', t.created_by], ['Created', ui.when(t.created_at)],
                    ['Published', t.published_at ? ui.when(t.published_at) : ''], ['Signing key', t.key_id ? `<code class="orch-mono">${esc(t.key_id)}</code>` : '', true],
                    ['Manifest digest', `<code class="orch-mono orch-break">${esc(t.manifest_digest || '')}</code>`, true], ['Signature', t.signature ? `<code class="orch-mono">${esc(t.signature)}</code>` : '', true],
                    ['Intent', t.intent], ['Studio chat', t.session_name ? `<span class="orch-mono">${esc(t.session_name)}</span>` : '', true],
                ])}</div></article>
            </div>
            <section class="orch-section"><header class="orch-section__head"><h2 class="orch-section__title">Inputs</h2></header>${schemaTable(t.manifest?.inputs)}</section>
            ${t.manifest?.outputs?.properties ? `<section class="orch-section"><header class="orch-section__head"><h2 class="orch-section__title">Outputs</h2></header>${schemaTable(t.manifest.outputs, { empty: 'No declared outputs.' })}</section>` : ''}
        </div>`;
    }

    function filesPanel(t) {
        const files = Object.entries(t.files || {});
        if (!files.length) return '<p class="orch-muted">This version has no files.</p>';
        return `<div class="orch-stack">${files.map(([name, text]) => `${ui.code(text, { lang: ui.langFor(name), title: name })}<p class="orch-sub orch-mono">sha256 ${esc((t.blobs || {})[name] || '')}</p>`).join('')}</div>`;
    }
    function diffPanel(t) {
        if (!t.previous) return '<p class="orch-muted">This is the first published version: there is nothing to compare with.</p>';
        if (!t.diff) return `<p class="orch-muted">No changes against ${esc(t.previous)}.</p>`;
        const html = window.WashDiff?.renderDiffHTML ? window.WashDiff.renderDiffHTML(t.diff, { tree: true }) : ui.code(t.diff, { lang: 'diff', title: 'diff' });
        return `<p class="orch-muted">Changes against <a href="#/tool/${encodeURIComponent(t.previous.split('@')[0])}/${encodeURIComponent(t.previous.split('@')[1])}" class="orch-mono">${esc(t.previous)}</a> (manifest and files).</p><div class="orch-diff">${html}</div>`;
    }
    function lintPanel(t) {
        const l = t.lint_result;
        const analyzers = l?.analyzers ? Object.entries(l.analyzers).map(([k, v]) => `${k}: ${v}`).join(' · ') : '';
        return `<div class="orch-stack"><div class="orch-head__meta">${lintSummary(t)}${l?.at ? `<span class="orch-sub">ran ${ui.time(l.at)}</span>` : ''}${analyzers ? `<span class="orch-sub">${esc(analyzers)}</span>` : ''}</div>${lintTable(l)}</div>`;
    }
    function versionsPanel(t) {
        return ui.table({ label: 'Versions', cols: [{ label: 'Version' }, { label: 'Status' }, { label: '' }],
            rows: (t.versions || []).slice().reverse().map(v => ({ attrs: v.version === t.version ? ' aria-selected="true"' : '', cells: [`<a class="orch-mono" href="#/tool/${encodeURIComponent(t.tool_id)}/${v.version}">${esc(v.ref)}</a>`, ui.chip(v.style), v.version === t.version ? '<span class="orch-sub">this version</span>' : ''] })) });
    }

    async function renderTool(el, route, { isCurrent, setCrumbs, setQuery }) {
        const [id, version] = route.parts;
        const t = await api.get(`tools/${encodeURIComponent(id)}/${encodeURIComponent(version)}`);
        if (!isCurrent()) return null;
        setCrumbs([{ label: 'Catalog', to: '#/catalog' }, { label: t.ref }]);
        let tab = TABS.includes(route.query.get('tab')) ? route.query.get('tab') : 'overview';
        const findings = ((t.lint_result || {}).findings || []).length;
        const tabList = [
            { id: 'overview', label: 'Overview' }, { id: 'results', label: 'Results' }, { id: 'manifest', label: 'Manifest' }, { id: 'files', label: 'Files', count: Object.keys(t.files || {}).length },
            { id: 'diff', label: 'Diff' }, { id: 'lint', label: 'Lint', count: findings || '', tone: t.lint?.ok ? '' : 'attention' },
            { id: 'tests', label: 'Tests', count: t.tests?.count || '' }, { id: 'versions', label: 'Versions', count: (t.versions || []).length },
            { id: 'notes', label: 'Notes' }, { id: 'usage', label: 'Usage', count: (t.usage || []).length || '' },
        ];
        const editable = OPEN.includes(t.status);
        el.innerHTML = `
            <header class="orch-head"><h1 class="w-page__title">${esc(t.title)} v${esc(t.version)}</h1></header>
            <div class="orch-detail">
                <div class="orch-detail__main">
                    ${ui.tabs('Tool views', tabList, tab)}
                    <div class="w-tabs__panel orch-panel" id="orch-panel" role="tabpanel" aria-labelledby="orch-tab-${esc(tab)}" tabindex="0"></div>
                </div>
                <aside class="orch-detail__aside" aria-label="About this tool">
                    <div class="orch-toolbar">${ui.actionBar(t.actions, { label: 'Tool actions' })}${editable ? `<a class="w-btn" data-variant="ghost" href="#/studio/${encodeURIComponent(t.tool_id)}/${t.version}">${icon('pencil')}<span>Open in studio</span></a>` : ''}</div>
                    <p class="orch-head__meta orch-vchecks">${lintSummary(t)}${testsSummary(t)}</p>
                    ${ui.kv([['Effect', ui.effectChip(t.effect_style, t.effect), true], ['Runs as', t.run_as || 'default'], ['Check mode', t.check_mode ? 'supported' : 'not supported'],
                        ['Rollback', t.rollback ? `<code class="orch-mono">${esc(t.rollback)}</code>` : 'none', true], ['Category', t.category]])}
                    <section class="w-group" aria-labelledby="orch-usage-h"><div class="w-group__head"><h2 class="w-group__title" id="orch-usage-h">Recent runs</h2>${(t.usage || []).length > 6 ? '<button type="button" class="w-textbtn" data-tone="quiet" data-tab-link="usage">All runs</button>' : ''}</div>
                        ${Orch.runs ? Orch.runs.runRows((t.usage || []).slice(0, 6), { empty: 'This version hasn’t run yet.' }) : ''}</section>
                </aside>
            </div>`;
        versionState(t);
        const panel = el.querySelector('#orch-panel');
        const paint = () => {
            panel.setAttribute('aria-labelledby', `orch-tab-${tab}`);
            el.querySelectorAll('[role="tab"]').forEach(b => { const on = b.dataset.tab === tab; b.setAttribute('aria-selected', String(on)); b.tabIndex = on ? 0 : -1; });
            if (tab === 'results') {
                /* Fleet results: each node's latest successful output of this tool, any version. */
                panel.innerHTML = '<div data-results></div>';
                Orch.results?.mount(panel.querySelector('[data-results]'), {
                    url: `tools/${encodeURIComponent(t.tool_id)}/results`,
                    csv: (_step, view) => `/api/orchestration/tools/${encodeURIComponent(t.tool_id)}/results.csv?view=${encodeURIComponent(view)}`,
                    empty: 'No node has returned results for this tool yet. Run it to see them here.',
                    onRow: row => { location.hash = `#/run/${encodeURIComponent(row.run_id)}?exec=${encodeURIComponent(row.exec_id)}`; },
                    ask: v => `Look at the latest ${t.tool_id} results across the fleet (${v.label} view${v.filter ? `, filtered by "${v.filter}"` : ''}) with fleet_results and tell me what needs attention.`,
                });
                return;
            }
            panel.innerHTML = tab === 'overview' ? overviewPanel(t)
                : tab === 'manifest' ? ui.code(t.manifest_yaml, { lang: 'yaml', title: 'tool.yaml' })
                : tab === 'files' ? filesPanel(t)
                : tab === 'diff' ? diffPanel(t)
                : tab === 'lint' ? lintPanel(t)
                : tab === 'tests' ? `<div class="orch-stack"><section class="orch-section"><header class="orch-section__head"><h2 class="orch-section__title">Declared tests</h2></header>${expectationsTable(t.manifest)}</section><section class="orch-section"><header class="orch-section__head"><h2 class="orch-section__title">Results</h2></header>${testsTable(t)}</section></div>`
                : tab === 'versions' ? versionsPanel(t)
                : tab === 'notes' ? notesTimeline(t.notes)
                : (Orch.runsTable ? Orch.runsTable(t.usage || [], { empty: 'This version hasn’t run yet.' }) : '');
        };
        paint();
        ui.bindTabs(el, (next) => { tab = next; setQuery({ tab: next === 'overview' ? '' : next }); paint(); });
        el.querySelector('[data-tab-link]')?.addEventListener('click', e => el.querySelector(`[role="tab"][data-tab="${e.currentTarget.dataset.tabLink}"]`)?.click());
        ui.bindActions(el, t.actions, { args: { ref: t.ref }, subject: t.ref, done: toolDone(t), dialogs: toolDialogs(t),
            overrides: { submitReview: () => Orch.studio?.submitDialog(t, () => Orch.refresh()) } });
        return null;
    }

    /* ------------------------------------------------------------ runbook detail */
    const RB_TABS = ['overview', 'spec', 'diff', 'tests', 'versions', 'notes', 'usage'];
    function runbookProblems(r) {
        const details = r.problem_details || [];
        const errs = details.filter(p => (p.severity || 'error') === 'error');
        const warns = details.filter(p => p.severity === 'warning');
        const item = p => `<li>${p.path ? `<code class="orch-mono">${esc(p.path)}</code> ` : ''}${esc(p.message)}${p.line ? ` <span class="orch-sub">line ${esc(p.line)}</span>` : ''}</li>`;
        return `${errs.length ? ui.banner('danger', '✗', `<strong>${esc(ui.plural(errs.length, 'problem'))}:</strong> this runbook can’t be submitted, published or run until ${errs.length === 1 ? 'it is' : 'they are'} fixed.`) + `<ul class="orch-list orch-error">${errs.map(item).join('')}</ul>` : ''}
            ${warns.length ? `<details class="orch-details"${errs.length ? '' : ' open'}><summary>${esc(ui.plural(warns.length, 'warning'))}</summary><ul class="orch-list">${warns.map(item).join('')}</ul></details>` : ''}`;
    }
    function runbookOverview(r) {
        return `<div class="orch-stack">
            ${r.summary ? `<p class="orch-lede">${esc(r.summary)}</p>` : ''}
            ${runbookProblems(r)}
            <section class="orch-section"><header class="orch-section__head"><h2 class="orch-section__title">Steps</h2></header>
                ${ui.table({ label: 'Steps', empty: 'No steps.', cols: [{ label: '#', num: true }, { label: 'Step' }, { label: 'Tool' }, { label: 'Params' }, { label: 'When' }, { label: 'Nodes' }, { label: 'On failure' }],
                    rows: (r.steps || []).map((s, i) => [String(i + 1), esc(s.name || ''), s.tool ? `<a class="orch-mono" href="#/tool/${encodeURIComponent(String(s.tool).split('@')[0])}/${encodeURIComponent(String(s.tool).split('@')[1] || '')}">${esc(s.tool)}</a>` : '—',
                        `<code class="orch-mono orch-break">${esc(JSON.stringify(s.params || {}))}</code>`, s.when ? `<code class="orch-mono">${esc(s.when)}</code>` : '—', s.nodes ? `<code class="orch-mono">${esc(s.nodes)}</code>` : 'all', esc(s.on_failure || 'stop')]) })}</section>
            <section class="orch-section"><header class="orch-section__head"><h2 class="orch-section__title">Inputs</h2></header>${schemaTable(r.inputs, { empty: 'This runbook takes no inputs.' })}</section>
        </div>`;
    }
    function runbookDiff(r) {
        if (!r.previous) return '<p class="orch-muted">There is no earlier published version to compare with.</p>';
        if (!r.diff) return `<p class="orch-muted">No changes against ${esc(r.previous)}.</p>`;
        const html = window.WashDiff?.renderDiffHTML ? window.WashDiff.renderDiffHTML(r.diff, { tree: false }) : ui.code(r.diff, { lang: 'diff', title: 'diff' });
        const [pid, pv] = r.previous.split('@');
        return `<p class="orch-muted">Changes against <a class="orch-mono" href="${runbookHref({ runbook_id: pid, version: pv })}">${esc(r.previous)}</a>.</p><div class="orch-diff">${html}</div>`;
    }

    async function renderRunbook(el, route, { isCurrent, setCrumbs, setQuery }) {
        const [id, version] = route.parts;
        const r = await api.get(`runbooks/${encodeURIComponent(id)}/${encodeURIComponent(version)}`);
        if (!isCurrent()) return null;
        setCrumbs([{ label: 'Catalog', to: '#/catalog' }, { label: r.ref }]);
        let tab = RB_TABS.includes(route.query.get('tab')) ? route.query.get('tab') : 'overview';
        const errs = (r.problem_details || []).filter(p => (p.severity || 'error') === 'error').length;
        const tabList = [
            { id: 'overview', label: 'Overview', count: errs || '', tone: errs ? 'danger' : '' }, { id: 'spec', label: 'Spec' }, { id: 'diff', label: 'Diff' },
            { id: 'tests', label: 'Tests', count: r.tests?.count || '' }, { id: 'versions', label: 'Versions', count: (r.versions || []).length },
            { id: 'notes', label: 'Notes' }, { id: 'usage', label: 'Usage', count: (r.usage || []).length || '' },
        ];
        const editorHref = `#/studio/runbook/${encodeURIComponent(r.runbook_id)}/${encodeURIComponent(r.version)}`;
        el.innerHTML = `
            <header class="orch-head"><h1 class="w-page__title">${esc(r.title)} v${esc(r.version)}</h1></header>
            <p class="orch-head__meta orch-vchecks">${errs ? ui.chip({ semantic: 'danger', glyph: '✗', label: ui.plural(errs, 'problem') }) : ''}${r.tests?.count ? ui.chip(r.tests.last?.passed ? { semantic: 'ok', glyph: '✓', label: `Last test passed · ${r.tests.passed}/${r.tests.count}` } : { semantic: 'danger', glyph: '✗', label: `Last test failed · ${r.tests.passed}/${r.tests.count}` }) : ''}</p>
            <div class="orch-stack">
                <div class="orch-toolbar">${ui.actionBar(r.actions, { label: 'Runbook actions' })}${['published', 'deprecated'].includes(r.status) ? `<button type="button" class="w-btn" data-variant="ghost" data-upgrade>${icon('refresh-cw')}<span>New version with upgraded tools</span></button>` : ''}</div>
                ${!r.parses ? ui.banner('caution', '◆', 'The draft’s YAML doesn’t parse. The last valid version is shown here; open the editor to fix it.', `<a class="w-btn" data-variant="tonal" data-tone="caution" data-size="sm" href="${editorHref}">Open editor</a>`) : ''}
                ${ui.tabs('Runbook views', tabList, tab)}
                <div class="w-tabs__panel orch-panel" id="orch-panel" role="tabpanel" aria-labelledby="orch-tab-${esc(tab)}" tabindex="0"></div>
            </div>`;
        versionState(r);
        const panel = el.querySelector('#orch-panel');
        const paint = () => {
            panel.setAttribute('aria-labelledby', `orch-tab-${tab}`);
            el.querySelectorAll('[role="tab"]').forEach(b => { const on = b.dataset.tab === tab; b.setAttribute('aria-selected', String(on)); b.tabIndex = on ? 0 : -1; });
            panel.innerHTML = tab === 'overview' ? runbookOverview(r)
                : tab === 'spec' ? `<p class="orch-sub orch-mono">digest ${esc(r.digest || '')}</p>${ui.code(r.source || r.spec_yaml, { lang: 'yaml', title: 'runbook.yaml' })}`
                : tab === 'diff' ? runbookDiff(r)
                : tab === 'tests' ? (Orch.runbooks?.testsHtml ? Orch.runbooks.testsHtml(r) : '')
                : tab === 'versions' ? ui.table({ label: 'Versions', cols: [{ label: 'Version' }, { label: 'Status' }, { label: '' }],
                    rows: (r.versions || []).slice().reverse().map(v => ({ attrs: v.version === r.version ? ' aria-selected="true"' : '', cells: [`<a class="orch-mono" href="${runbookHref({ runbook_id: r.runbook_id, version: v.version })}">${esc(v.ref)}</a>`, ui.chip(v.style), v.version === r.version ? '<span class="orch-sub">this version</span>' : ''] })) })
                : tab === 'notes' ? notesTimeline(r.notes)
                : (Orch.runsTable ? Orch.runsTable(r.usage || [], { empty: 'This runbook hasn’t run yet.' }) : '');
        };
        paint();
        ui.bindTabs(el, next => { tab = next; setQuery({ tab: next === 'overview' ? '' : next }); paint(); });
        const goEditor = ref => { const [rid, v] = String(ref).split('@'); Orch.go(`#/studio/runbook/${encodeURIComponent(rid)}/${encodeURIComponent(v)}`); };
        ui.bindActions(el, r.actions, {
            args: { ref: r.ref }, subject: r.ref,
            done: (result, action) => {
                if (action.id === 'newRunbookVersion' && result?.data?.ref) { goEditor(result.data.ref); return; }
                if (action.id === 'discardRunbookDraft') {
                    const prev = (r.versions || []).filter(v => v.version !== r.version).pop();
                    Orch.go(prev ? runbookHref({ runbook_id: r.runbook_id, version: prev.version }) : '#/catalog');
                    return;
                }
                Orch.refresh();
            },
            dialogs: {
                edit: () => Orch.go(editorHref),
                test: () => Orch.runbooks?.testDialog(r),
                run: () => Orch.go(`#/new-run?kind=runbook&target=${encodeURIComponent(r.ref)}`),
            },
        });
        el.querySelector('[data-upgrade]')?.addEventListener('click', () => Orch.runbooks?.upgradeDialog({ spec: r.spec, ref: r.ref, published: true }));
        return null;
    }

    Orch.catalog = { schemaTable, notesTimeline, lintTable, lintSummary, testsSummary, testsTable, expectationsTable, toolHref, runbookHref, toolDone, toolDialogs };
    Orch.routes.catalog = { nav: 'catalog', title: 'Catalog', scopes: ['tool'], render: renderList, crumbs: () => [{ label: 'Catalog' }] };
    Orch.routes.tool = { nav: 'catalog', title: 'Tool', scopes: ['tool'], render: renderTool, crumbs: r => [{ label: 'Catalog', to: '#/catalog' }, { label: `${r.parts[0] || ''}@${r.parts[1] || ''}` }] };
    Orch.routes.runbook = { nav: 'catalog', title: 'Runbook', scopes: ['tool'], render: renderRunbook, crumbs: r => [{ label: 'Catalog', to: '#/catalog' }, { label: `${r.parts[0] || ''}@${r.parts[1] || ''}` }] };
})();
