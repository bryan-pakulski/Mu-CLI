/* Orchestration page shell: hash router, sidebar sub-navigation, top-bar
 * chips, live updates and the Overview screen.
 *
 * Routes (#/…): overview, nodes, node/<id>, catalog, tool/<id>/<v>,
 * runbook/<id>/<v>, studio, studio/<id>/<v>, runs, run/<id>, new-run,
 * approvals, audit, settings. Route modules (nodes.js, catalog.js, studio.js,
 * runs.js, settings.js) register on window.Orch.routes as
 *     {nav, title, scopes, render(el, route, {soft}) → controller?}
 * where scopes lists the SSE scopes (node | tool | run) that make the view
 * stale; a stale view is re-rendered softly (debounced, no loading state,
 * focus kept). A controller may instead take over with onChange(change), and
 * clean up in dispose().
 */
(() => {
    'use strict';
    const api = window.OrchAPI;
    const ui = window.OrchUI;
    const { esc, icon } = ui;
    const Orch = (window.Orch = window.Orch || { routes: {} });
    Orch.routes = Orch.routes || {};

    const app = document.getElementById('orch-app');
    const viewEl = document.getElementById('orch-view');
    const crumbsEl = document.getElementById('orch-crumbs');
    let current = { key: '', route: null, controller: null };
    let attention = {};
    let renderToken = 0;
    let firstRender = true;

    /* ------------------------------------------------------------ routing */
    function parse() {
        const raw = (location.hash || '#/').replace(/^#\/?/, '');
        const [path, qs] = raw.split('?');
        const parts = path.split('/').filter(Boolean).map(decodeURIComponent);
        return { name: parts[0] || 'overview', parts: parts.slice(1), query: new URLSearchParams(qs || ''), hash: location.hash || '#/' };
    }
    const go = hash => { if (location.hash === hash) applyRoute(); else location.hash = hash; };
    function setQuery(params, { replace = true } = {}) {
        const r = parse();
        const q = new URLSearchParams(r.query);
        Object.entries(params).forEach(([k, v]) => { if (v === null || v === undefined || v === '') q.delete(k); else q.set(k, v); });
        const base = '#/' + [r.name === 'overview' ? '' : r.name, ...r.parts].filter(Boolean).map(encodeURIComponent).join('/');
        const next = q.toString() ? `${base}?${q}` : base;
        if (replace) history.replaceState(null, '', next); else location.hash = next;
    }

    function setNav(nav) {
        document.querySelectorAll('[data-orch-nav]').forEach(a => {
            if (a.dataset.orchNav === nav) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
        });
    }
    function setCrumbs(trail) {
        if (!crumbsEl) return;
        const all = [{ label: 'Orchestration', to: '#/' }, ...trail];
        crumbsEl.innerHTML = all.map((c, i) => `<li class="w-breadcrumb__item">${c.to && i < all.length - 1 ? `<a href="${esc(c.to)}">${esc(c.label)}</a>` : `<span${i === all.length - 1 ? ' aria-current="page"' : ''}>${esc(c.label)}</span>`}</li>`).join('');
        document.title = `${all.length > 1 ? all[all.length - 1].label + ' · ' : ''}Orchestration · μcli`;
    }

    async function applyRoute({ soft = false } = {}) {
        const route = parse();
        const def = Orch.routes[route.name] || Orch.routes.overview;
        const key = `${route.name}/${route.parts.join('/')}`;
        if (!soft) {
            current.controller?.dispose?.();
            current = { key, route, def, controller: null };
            setNav(def.nav || route.name);
            setCrumbs(def.crumbs ? def.crumbs(route) : [{ label: def.title || route.name }]);
            viewEl.innerHTML = ui.loading();
        }
        const token = ++renderToken;
        const focusKey = soft ? focusSignature() : null;
        try {
            const controller = await def.render(viewEl, route, { soft, isCurrent: () => token === renderToken && current.key === key, setCrumbs, go, setQuery });
            if (token !== renderToken) return;
            if (soft && controller && current.controller !== controller) current.controller?.dispose?.();
            if (!soft || controller) current.controller = controller || null;
            if (soft) restoreFocus(focusKey);
            else if (!firstRender) moveFocus();
            firstRender = false;
        } catch (err) {
            if (token !== renderToken) return;
            if (soft) { ui.toast(`Couldn’t refresh: ${err.message}`, 'danger'); return; }
            viewEl.innerHTML = ui.errorState(err);
            viewEl.querySelector('[data-orch-retry]')?.addEventListener('click', () => applyRoute());
        }
    }
    function moveFocus() {
        const h = viewEl.querySelector('h1');
        if (h) { h.setAttribute('tabindex', '-1'); h.focus({ preventScroll: true }); }
        document.getElementById('w-main')?.scrollTo?.({ top: 0 });
    }
    function focusSignature() {
        const el = document.activeElement;
        if (!el || !viewEl.contains(el)) return null;
        return el.id ? `#${el.id}` : (el.dataset.action ? `[data-action="${el.dataset.action}"]` : (el.dataset.focusKey ? `[data-focus-key="${el.dataset.focusKey}"]` : null));
    }
    function restoreFocus(sig) {
        if (!sig) return;
        try { viewEl.querySelector(sig)?.focus({ preventScroll: true }); } catch (_) { /* selector from server text */ }
    }
    const softRender = ui.debounce(() => applyRoute({ soft: true }), 350);
    Orch.refresh = () => applyRoute({ soft: true });
    Orch.go = go;
    Orch.setQuery = setQuery;
    Orch.parse = parse;

    /* ------------------------------------------------------------ attention chips */
    function paintAttention(att) {
        attention = att || {};
        document.dispatchEvent(new CustomEvent('mucli:orch-attention', { detail: { ...attention, summary: describeAttention(attention) } }));
        const total = Number(attention.total || 0);
        const chip = document.querySelector('[data-orch-chip="needs"]');
        if (chip) {
            chip.dataset.tone = total ? 'attention' : 'neutral';
            chip.querySelector('[data-chip-text]').textContent = `${total} need${total === 1 ? 's' : ''} you`;
            chip.title = total ? describeAttention(attention) : 'Nothing needs you';
        }
        const badge = document.getElementById('orch-approvals-badge');
        if (badge) { badge.textContent = String(total); badge.hidden = !total; }
        const paused = document.querySelector('[data-orch-chip-slot="paused"]');
        if (paused) paused.hidden = !attention.dispatch_paused;
    }
    function describeAttention(a) {
        const parts = [];
        if (a.approvals) parts.push(ui.plural(a.approvals, 'run awaiting approval', 'runs awaiting approval'));
        if (a.paused_runs) parts.push(ui.plural(a.paused_runs, 'paused run'));
        if (a.reviews) parts.push(ui.plural(a.reviews, 'tool in review', 'tools in review'));
        if (a.pending_nodes) parts.push(ui.plural(a.pending_nodes, 'node awaiting acceptance', 'nodes awaiting acceptance'));
        if (a.offline_nodes) parts.push(ui.plural(a.offline_nodes, 'offline node'));
        return parts.join(' · ') || 'Nothing needs you';
    }
    async function refreshAttention() {
        try { paintAttention(await api.get('attention')); } catch (_) { /* next change retries */ }
    }
    Orch.refreshAttention = refreshAttention;
    Orch.attention = () => attention;
    const refreshAttentionSoon = ui.debounce(refreshAttention, 400);

    function setLive(on) {
        const chip = document.querySelector('[data-orch-chip="live"]');
        if (!chip) return;
        chip.dataset.tone = on ? 'ok' : 'caution';
        chip.querySelector('[data-chip-glyph]').textContent = on ? '●' : '○';
        chip.querySelector('[data-chip-text]').textContent = on ? 'Live' : 'Reconnecting…';
        chip.title = on ? 'Live updates connected' : 'Live updates reconnecting';
    }

    /* Components that follow the change stream themselves (pickers) subscribe here; they see
     * every change before the current view does. */
    const listeners = new Set();
    Orch.subscribe = fn => { listeners.add(fn); return () => listeners.delete(fn); };

    function onChange(change) {
        refreshAttentionSoon();
        listeners.forEach(fn => { try { fn(change); } catch (err) { console.error(err); } });
        const c = current.controller;
        if (c?.onChange) { c.onChange(change); return; }
        const scopes = current.def?.scopes || [];
        if (scopes.includes(change.scope)) softRender();
    }

    /* ------------------------------------------------------------ dispatch kill switch */
    async function setDispatch(paused, button) {
        if (paused) {
            const ok = await ui.confirm({
                title: 'Pause all dispatch?', confirmLabel: 'Pause dispatch', tone: 'danger',
                message: 'No new work is sent to any node until you resume. Executions already running on nodes keep going; cancel their runs to stop them.',
            });
            if (!ok) return;
        }
        try {
            await ui.command(paused ? 'pauseDispatch' : 'resumeDispatch', {}, { ok: paused ? 'Dispatch paused' : 'Dispatch resumed', button });
            await refreshAttention();
            Orch.refresh();
        } catch (_) { /* toasted */ }
    }
    Orch.setDispatch = setDispatch;
    Orch.pausedBanner = paused => paused ? ui.banner('danger', '✗', '<strong>Dispatch is paused.</strong> No new work goes to any node until you resume it.',
        '<button type="button" class="w-btn" data-variant="tonal" data-tone="danger" data-size="sm" data-orch-resume>Resume dispatch</button>') : '';
    document.addEventListener('click', e => {
        const r = e.target.closest('[data-orch-resume]');
        if (r) setDispatch(false, r);
    });

    /* ------------------------------------------------------------ overview */
    const NODE_ORDER = ['active', 'offline', 'pending', 'quarantined', 'revoked'];
    function attentionItems(o) {
        const a = o.attention || {};
        const items = [];
        (o.approvals || []).forEach(r => items.push({ tone: 'attention', glyph: '◆', title: r.title, meta: `${r.target} · ${r.blast_radius || ui.plural(r.nodes, 'node')} · proposed by ${r.proposed_by}`, href: `#/run/${encodeURIComponent(r.id)}`, cta: 'Review run', chips: ui.effectChip(r.effect_style, r.effect) }));
        (o.paused || []).forEach(r => items.push({ tone: 'attention', glyph: '◆', title: `Paused: ${r.title}`, meta: r.paused_reason || 'Waiting for you to continue, roll back or cancel', href: `#/run/${encodeURIComponent(r.id)}`, cta: 'Decide' }));
        if (a.reviews) items.push({ tone: 'attention', glyph: '◆', title: ui.plural(a.reviews, 'tool version is', 'tool versions are') + ' in review', meta: 'Read the manifest, diff, lint and tests, then publish or request changes', href: '#/approvals', cta: 'Open reviews' });
        if (a.pending_nodes) items.push({ tone: 'attention', glyph: '◆', title: ui.plural(a.pending_nodes, 'node is', 'nodes are') + ' awaiting acceptance', meta: 'Check the name and labels before accepting', href: '#/approvals', cta: 'Review nodes' });
        if (a.offline_nodes) items.push({ tone: 'danger', glyph: '✗', title: ui.plural(a.offline_nodes, 'node is', 'nodes are') + ' offline', meta: 'Not seen for over 90 seconds', href: '#/nodes?status=offline', cta: 'Show nodes' });
        (o.unreachable || []).forEach(x => items.push({ tone: 'danger', glyph: '?', title: `Unreachable: ${x.tool} on ${x.node_id}`, meta: 'The node dropped mid-run; this execution needs a decision', href: `#/run/${encodeURIComponent(x.run_id)}`, cta: 'Open run' }));
        return items;
    }

    async function renderOverview(el, route, { isCurrent }) {
        const o = await api.get('overview');
        if (!isCurrent()) return null;
        paintAttention(o.attention);
        const counts = o.nodes.by_status || {};
        const health = NODE_ORDER.filter(k => counts[k]).map(k => `<a class="orch-health__item" href="#/nodes?status=${esc(k)}">${ui.chip(ui.vstyle('orch_node_status', k), { label: `${counts[k]} ${ui.vstyle('orch_node_status', k).label}` })}</a>`).join('');
        const items = attentionItems(o);
        const stats = [
            ['Nodes', o.nodes.total, '#/nodes'], ['Online', counts.active || 0, '#/nodes?status=active'],
            ['Published tools', o.catalog.published, '#/catalog'], ['In review', o.catalog.in_review, '#/approvals'],
            ['Drafts', o.catalog.drafts, '#/studio'],
        ];
        const net = o.network || {};
        el.innerHTML = `
            <header class="w-hero orch-hero" data-art="work">
                <span class="w-eyebrow" data-tone="accent"><i class="w-eyebrow__swatch"></i>Orchestration</span>
                <h1 class="w-hero__title">Your fleet, one operator</h1>
                <p class="w-hero__lede">Run reviewed, signed tools across your machines. Nothing runs without a plan you can read first.</p>
                <div class="w-btn-row orch-hero__actions">
                    <a class="w-btn" data-variant="primary" data-size="lg" href="#/new-run">${icon('play')}<span>New run</span></a>
                    <a class="w-btn" data-variant="secondary" data-size="lg" href="#/setup">${icon('server')}<span>Set up a node</span></a>
                    <a class="w-btn" data-variant="ghost" data-size="lg" href="#/studio">Tool studio <span class="w-btn__chevron" aria-hidden="true">›</span></a>
                </div>
            </header>
            <div class="orch-stack orch-overview">
                ${Orch.pausedBanner(o.dispatch_paused)}
                ${!net.bind ? ui.banner('info', '●', 'The node endpoint isn’t configured yet, so nodes can’t enrol. Choose a private interface in Settings.', '<a class="w-btn" data-variant="tonal" data-tone="info" data-size="sm" href="#/settings?section=network">Open network settings</a>') : ''}
                <dl class="w-stats orch-stats">${stats.map(([label, value, href]) => `<div class="w-stat"><dt class="w-stat__label"><a href="${esc(href)}">${esc(label)}</a></dt><dd class="w-stat__value">${esc(value)}</dd></div>`).join('')}</dl>
                <div class="orch-grid-2">
                    <section class="orch-section" aria-labelledby="orch-att-h">
                        <header class="orch-section__head"><h2 class="orch-section__title" id="orch-att-h">Needs you</h2>${items.length ? `<span class="w-count" data-tone="attention">${items.length}</span>` : ''}</header>
                        ${items.length ? `<ul class="orch-attn" role="list">${items.map(it => `
                            <li><article class="w-card orch-attn__card" data-state="${it.tone === 'danger' ? 'danger' : 'attention'}">
                                <header class="w-card__head"><span class="w-chip" data-tone="${esc(it.tone)}"><span class="w-chip__glyph" aria-hidden="true">${esc(it.glyph)}</span><span>${it.tone === 'danger' ? 'Problem' : 'Needs you'}</span></span>${it.chips || ''}</header>
                                <div class="w-card__body orch-attn__body"><h3 class="orch-attn__title">${esc(it.title)}</h3><p class="orch-muted">${esc(it.meta)}</p></div>
                                <footer class="w-card__foot"><a class="w-btn" data-variant="tonal" data-tone="${esc(it.tone)}" data-size="sm" href="${esc(it.href)}">${esc(it.cta)} <span class="w-btn__chevron" aria-hidden="true">›</span></a></footer>
                            </article></li>`).join('')}</ul>` : ui.empty('You’re all caught up', 'No approvals, reviews or stuck runs.', { icon: 'circle-check', tone: 'ok' })}
                    </section>
                    <section class="orch-section" aria-labelledby="orch-health-h">
                        <header class="orch-section__head"><h2 class="orch-section__title" id="orch-health-h">Fleet health</h2><span class="orch-spacer"></span><a class="w-btn" data-variant="ghost" data-size="sm" href="#/nodes">All nodes <span class="w-btn__chevron" aria-hidden="true">›</span></a></header>
                        <div class="w-card orch-card"><div class="w-card__body">
                            ${o.nodes.total ? `<div class="orch-health">${health}</div>` : ui.empty('No nodes yet', 'The setup wizard hands out the agent, follows the enrolment and checks the node can run a tool.', { icon: 'server', action: '<a class="w-btn" data-variant="primary" href="#/setup">Set up a node</a><button type="button" class="w-btn" data-variant="ghost" data-orch-enrol>Quick enrol</button>' })}
                        </div></div>
                        <article class="w-card orch-card orch-kill" aria-labelledby="orch-kill-h">
                            <header class="w-card__head"><h3 class="w-card__title" id="orch-kill-h">Kill switch</h3>${o.dispatch_paused ? '<span class="w-chip" data-tone="danger"><span class="w-chip__glyph" aria-hidden="true">✗</span><span>Dispatch paused</span></span>' : '<span class="w-chip" data-tone="ok"><span class="w-chip__glyph" aria-hidden="true">✓</span><span>Dispatching</span></span>'}</header>
                            <div class="w-card__body"><p class="orch-muted">Stops all new work from going to any node. Runs stay where they are until you resume.</p></div>
                            <footer class="w-card__foot">${o.dispatch_paused
                                ? '<button type="button" class="w-btn" data-variant="primary" data-orch-dispatch="resume">' + icon('play') + '<span>Resume dispatch</span></button>'
                                : '<button type="button" class="w-btn" data-variant="secondary" data-orch-dispatch="pause">' + icon('pause') + '<span>Pause all dispatch</span></button>'}</footer>
                        </article>
                    </section>
                </div>
                <section class="orch-section" aria-labelledby="orch-recent-h">
                    <header class="orch-section__head"><h2 class="orch-section__title" id="orch-recent-h">Recent runs</h2><span class="orch-spacer"></span><a class="w-btn" data-variant="ghost" data-size="sm" href="#/runs">All runs <span class="w-btn__chevron" aria-hidden="true">›</span></a></header>
                    ${Orch.runsTable ? Orch.runsTable(o.recent_runs || []) : ''}
                </section>
            </div>`;
        el.querySelectorAll('[data-orch-enrol]').forEach(b => b.addEventListener('click', () => Orch.nodes?.enrol()));
        el.querySelector('[data-orch-dispatch]')?.addEventListener('click', e => setDispatch(e.currentTarget.dataset.orchDispatch === 'pause', e.currentTarget));
        return null;
    }
    Orch.routes.overview = { nav: 'overview', title: 'Overview', scopes: ['node', 'tool', 'run'], render: renderOverview, crumbs: () => [{ label: 'Overview' }] };

    /* ------------------------------------------------------------ palette */
    if (window.WashPalette) {
        const nav = [['overview', 'Overview', '#/', 'layout-dashboard'], ['nodes', 'Nodes', '#/nodes', 'server'], ['catalog', 'Catalog', '#/catalog', 'book-open'],
            ['studio', 'Tool studio', '#/studio', 'sparkles'], ['runs', 'Runs', '#/runs', 'play'], ['approvals', 'Approvals', '#/approvals', 'shield-check'],
            ['audit', 'Audit log', '#/audit', 'history'], ['settings', 'Orchestration settings', '#/settings', 'settings']];
        window.WashPalette.register('orchestration', () => [
            ...nav.map(([id, label, href, ic]) => ({ id: `orch:${id}`, group: 'Orchestration', label, icon: ic, run: () => go(href), keywords: 'orchestration fleet' })),
            { id: 'orch:new-run', group: 'Actions', label: 'New run', icon: 'play', run: () => go('#/new-run') },
            { id: 'orch:setup', group: 'Actions', label: 'Set up a node', icon: 'server', run: () => go('#/setup'), keywords: 'enrol node agent install' },
            { id: 'orch:enrol', group: 'Actions', label: 'Quick enrol (join token only)', icon: 'plus', run: () => Orch.nodes?.enrol() },
            { id: 'orch:new-tool', group: 'Actions', label: 'New tool draft', icon: 'sparkles', run: () => Orch.studio?.newTool() },
            attention.dispatch_paused
                ? { id: 'orch:resume', group: 'Actions', label: 'Resume dispatch', icon: 'play', run: () => setDispatch(false) }
                : { id: 'orch:pause', group: 'Actions', label: 'Pause all dispatch', icon: 'pause', run: () => setDispatch(true) },
        ], { priority: 5 });
    }

    /* ------------------------------------------------------------ boot */
    function boot() {
        if (!app || !viewEl) return;
        window.addEventListener('hashchange', () => applyRoute());
        applyRoute();
        refreshAttention();
        api.events({ onChange, onHello: data => paintAttention(data.attention), onState: setLive });
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
