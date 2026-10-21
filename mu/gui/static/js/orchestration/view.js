/* Orchestration page shell (Mu §10.3): hash router, the page header (title,
 * state sentence, tabs Now · Runs · Nodes · Catalog · More, actions — through
 * window.WashHead), the leaf alert (dispatch paused, reconnecting), live updates
 * and the Now screen. Approvals is no longer a destination: #/approvals is Now.
 *
 * Routes (#/…): overview, nodes, node/<id>, catalog, tool/<id>/<v>,
 * runbook/<id>/<v>, studio, studio/<id>/<v>, runs, run/<id>, new-run,
 * approvals (= Now), audit, settings. Route modules (nodes.js, catalog.js, studio.js,
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
    let current = { key: '', route: null, controller: null };
    let attention = {};
    let fleet = null;          // the last overview: nodes, catalog, network (the state sentence)
    let live = true;
    let renderToken = 0;
    let firstRender = true;
    /* Crossing from one item to an item in another group (a node → its update run) leaves a
     * "Back to web-1" link, so the operator returns to where they were without re-finding it. */
    let shownItem = null;      // {href, label, group} of the item on screen
    let backTo = null;         // the item to offer a way back to

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

    /* ------------------------------------------------------------ workspace (Mu §11b) */
    /* #orch-app is a .w-panes: list | main | aside, each scrolling on its own.
     *   Runs, Nodes, Catalog — the LIST pane keeps the group's list while a row's hash route
     *   (#/run/<id>, #/node/<id>, #/tool/<id>/<v>, #/runbook/<id>/<v>) fills MAIN with the item
     *   itself and ASIDE with its context (the route module's .orch-detail__aside moves there:
     *   a run's execution log and facts, a node's actions and facts, a tool's actions and
     *   checks). With no row chosen, Runs and Catalog open their first row; Nodes shows the
     *   fleet (endpoint, groups, join tokens) in MAIN.
     *   Settings — the section index in LIST, the sections in MAIN.
     *   Now — a board in MAIN. Everything else (studio, wizard, setup, audit) — MAIN only.
     * Below 1200 the aside opens on demand (data-aside="open", the Details toggle); below 840
     * one pane shows at a time (data-show="list|main") with a back bar. */
    const panes = app;
    const listPane = document.getElementById('orch-list');
    const listBody = document.getElementById('orch-list-body');
    const mainPane = document.getElementById('orch-main');
    const asidePane = document.getElementById('orch-aside');
    const GROUPS = {
        runs: { list: 'runs', items: ['run'], title: 'Runs' },
        nodes: { list: 'nodes', items: ['node'], title: 'Nodes' },
        groups: { list: 'groups', items: ['group'], title: 'Groups' },
        catalog: { list: 'catalog', items: ['tool', 'runbook'], title: 'Catalog' },
    };
    const groupOf = name => Object.keys(GROUPS).find(g => g === name || GROUPS[g].items.includes(name)) || null;
    const listQuery = { runs: new URLSearchParams(), nodes: new URLSearchParams(), groups: new URLSearchParams(), catalog: new URLSearchParams() };
    const narrow = () => !!window.matchMedia?.('(width < 840px)').matches;
    let listGroup = null;
    let listToken = 0;
    let listController = null;
    /* Nodes' LIST is the groups (with + Create group) above the chosen group's inventory (groups.js). */
    const listRenderer = g => (g === 'nodes' ? Orch.groups?.renderNodesList : Orch.routes[GROUPS[g].list]?.render);

    function listSetQuery(g, params) {
        const q = listQuery[g];
        Object.entries(params).forEach(([k, v]) => { if (v === null || v === undefined || v === '') q.delete(k); else q.set(k, v); });
        if (parse().name === g) setQuery(params);
    }
    async function renderList(g, { soft = false } = {}) {
        const render = listRenderer(g);
        if (!render) return;
        if (!soft || listGroup !== g) {
            listController?.dispose?.();
            listController = null;
            listGroup = g;
            listPane.querySelector('[data-list-title]').textContent = GROUPS[g].title;
            listPane.querySelector('[data-list-tools]').innerHTML = '';
            listBody.innerHTML = ui.loading();
        }
        const token = ++listToken;
        const scroll = listPane.scrollTop;
        const route = { name: g, parts: [], query: listQuery[g], hash: `#/${g}` };
        try {
            const controller = await render(listBody, route, { soft, isCurrent: () => token === listToken && listGroup === g, setQuery: p => listSetQuery(g, p), go, setCrumbs: () => {}, inPane: true, tools: listPane.querySelector('[data-list-tools]') });
            if (token !== listToken) return;
            listController?.dispose?.();
            listController = controller || null;
            listBody.querySelectorAll('.orch-head').forEach(h => { h.hidden = true; });
            if (soft) listPane.scrollTop = scroll;
            markSelected();
        } catch (err) {
            if (token === listToken) listBody.innerHTML = ui.errorState(err, { retry: false });
        }
    }
    function itemKey(href) {
        const m = /^#\/(run|node|tool|runbook|group)\/([^?]+)/.exec(href || '');
        return m ? `${m[1]}/${m[2]}` : null;
    }
    function markSelected() {
        const want = itemKey(location.hash);
        /* Group entries in the Nodes list mark themselves (groups.js): they aren't items. */
        listBody.querySelectorAll('.w-row:not([data-group-entry])').forEach(r => {
            const a = r.querySelector('a.w-row__main[href]');
            const on = !!want && !!a && itemKey(a.getAttribute('href')) === want;
            r.toggleAttribute('data-selected', on);
            if (a) { if (on) a.setAttribute('aria-current', 'true'); else a.removeAttribute('aria-current'); }
        });
        const sel = listBody.querySelector('.w-row[data-selected]');
        if (sel) {
            const top = sel.offsetTop - listPane.scrollTop;
            if (top < 0 || top > listPane.clientHeight - sel.offsetHeight) sel.scrollIntoView({ block: 'nearest' });
        }
    }
    function firstItemHref() {
        const a = listBody.querySelector('a.w-row__main[href^="#/run/"], a.w-row__main[href^="#/node/"], a.w-row__main[href^="#/tool/"], a.w-row__main[href^="#/runbook/"], a.w-row__main[href^="#/group/"]');
        return a ? a.getAttribute('href') : null;
    }
    /* Lay the panes out for a route: which panes show, the back bar, the aside toggle. */
    function layout(route, { hasList, hasAside }) {
        const g = groupOf(route.name);
        panes.dataset.layout = hasList && hasAside ? 'list-main-aside' : (hasList ? 'list-main' : (hasAside ? 'main-aside' : 'main'));
        panes.dataset.route = route.name;
        panes.removeAttribute('data-aside');
        /* Narrow: a chosen group's dashboard (#/nodes?group=…) shows MAIN, not the list again. */
        panes.dataset.show = hasList && route.name === (g ? GROUPS[g].list : 'settings') && !(g === 'nodes' && (route.query.get('fleet') || route.query.get('group'))) ? 'list' : 'main';
        const bar = mainPane.querySelector('[data-backbar]');
        bar.hidden = !hasList;
        bar.querySelector('[data-backbar-label]').textContent = g ? GROUPS[g].title : 'Sections';
    }
    mainPane.querySelector('[data-show-list]').addEventListener('click', () => {
        const g = groupOf(parse().name);
        if (g && parse().name !== GROUPS[g].list) go(`#/${GROUPS[g].list}`);
        panes.dataset.show = 'list';
    });
    /* After a group item renders: its aside column moves into the ASIDE pane (listeners move
     * with it), and a Details toggle is offered for narrow leaves. */
    function placeAside() {
        const aside = viewEl.querySelector('.orch-detail > .orch-detail__aside');
        asidePane.innerHTML = '';
        if (!aside) return false;
        aside.closest('.orch-detail').setAttribute('data-paned', '');
        const close = document.createElement('div');
        close.className = 'orch-aside__close';
        close.innerHTML = `<button type="button" class="w-btn" data-variant="icon" data-aside-close aria-label="Close details" title="Close details">${icon('x')}</button>`;
        asidePane.append(close, aside);
        const head = viewEl.querySelector('.orch-head');
        if (head && !head.querySelector('[data-aside-toggle]')) {
            head.insertAdjacentHTML('beforeend', `<button type="button" class="w-textbtn orch-aside-toggle" data-tone="quiet" data-aside-toggle aria-controls="orch-aside">${icon('panel-right', 'sm')}<span>Details</span></button>`);
        }
        return true;
    }
    document.addEventListener('click', e => {
        if (e.target.closest('[data-aside-toggle]')) { panes.dataset.aside = panes.dataset.aside === 'open' ? '' : 'open'; return; }
        if (e.target.closest('[data-aside-close]')) panes.removeAttribute('data-aside');
    });
    /* Settings: the section index moves to the LIST pane. */
    function placeSettingsNav() {
        const nav = viewEl.querySelector('.orch-settings__nav');
        if (!nav) return false;
        listGroup = null;
        listController?.dispose?.();
        listController = null;
        listPane.querySelector('[data-list-title]').textContent = 'Settings';
        listPane.querySelector('[data-list-tools]').innerHTML = '';
        listBody.innerHTML = '';
        listBody.append(nav);
        /* The index follows the scroll: the section at the top of MAIN is current. */
        const links = [...nav.querySelectorAll('[data-section-link]')];
        const sections = links.map(l => viewEl.querySelector(`#orch-set-${l.dataset.sectionLink}`)).filter(Boolean);
        const mark = () => {
            const top = mainPane.getBoundingClientRect().top + 24;
            let cur = sections[0];
            sections.forEach(sec => { if (sec.getBoundingClientRect().top <= top) cur = sec; });
            links.forEach(l => { if (cur && `orch-set-${l.dataset.sectionLink}` === cur.id) l.setAttribute('aria-current', 'true'); else l.removeAttribute('aria-current'); });
        };
        mainPane.onscroll = ui.debounce(mark, 60);
        mark();
        return true;
    }
    /* Keys (Mu §9.6): j / k move between rows of the list (or the board), Enter opens. */
    document.addEventListener('keydown', e => {
        if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.altKey) return;
        const t = e.target;
        if (t && (t.isContentEditable || /^(input|textarea|select)$/i.test(t.tagName || ''))) return;
        if (document.querySelector('.w-modal:not([hidden]), .w-drawer:not([hidden])')) return;
        if (e.key !== 'j' && e.key !== 'k') return;
        const scope = panes.dataset.layout.startsWith('list') && listBody.querySelector('a.w-row__main') ? listBody : viewEl;
        const links = [...scope.querySelectorAll('a.w-row__main[href]')];
        if (!links.length) return;
        e.preventDefault();
        const i = links.indexOf(document.activeElement);
        const sel = scope.querySelector('.w-row[data-selected] a.w-row__main');
        const from = i >= 0 ? i : (sel ? links.indexOf(sel) : -1);
        const next = links[Math.max(0, Math.min(links.length - 1, from + (e.key === 'j' ? 1 : -1)))];
        next.focus();
        if (scope === listBody && !narrow()) next.click();
    });

    /* ------------------------------------------------------------ page header (Mu §4) */
    /* Groups is a primary tab by the operator's request (node groups: membership, selector,
     * the health of each); the rest live under More. */
    const TABS = [['now', 'Now', '#/'], ['runs', 'Runs', '#/runs'], ['nodes', 'Nodes', '#/nodes'], ['groups', 'Groups', '#/groups'], ['catalog', 'Catalog', '#/catalog']];
    const MORE = [['studio', 'Studio', '#/studio'], ['audit', 'Audit', '#/audit'], ['settings', 'Settings', '#/settings']];
    const NAV_TAB = { overview: 'now', approvals: 'now' };
    /* Top routes show "Orchestration" with tabs; anything with a parent (a run, a node, a
     * tool, the wizard, setup, an editor) is a detail page: its own name and a back link. */
    const isDetail = (def, route) => !!(def.crumbs && def.crumbs(route).some(c => c.to));
    let navTab = 'now';
    let detail = null;         // {back: {label, href}} on detail routes

    /* "Ask": the fleet chat (chat.js), on every route. */
    const askButton = () => `<button type="button" class="w-textbtn" data-orch-ask aria-controls="orch-chat" aria-expanded="${Orch.chat?.isOpen() ? 'true' : 'false'}" title="Ask about your nodes and runs (.)">${icon('message-circle')}<span>Ask</span></button>`;
    function bindAsk(wrap) { wrap.querySelector('[data-orch-ask]')?.addEventListener('click', () => Orch.chat?.toggle()); }
    function headActions() {
        if (detail) {
            const only = document.createElement('div');
            only.className = 'orch-head-actions';
            only.innerHTML = askButton();
            bindAsk(only);
            return only;
        }
        const paused = !!attention.dispatch_paused;
        const wrap = document.createElement('div');
        wrap.className = 'orch-head-actions';
        wrap.innerHTML = `${askButton()}<a class="w-textbtn" href="#/setup">Set up a node</a>
            <a class="w-btn" data-variant="primary" href="#/new-run">${icon('play')}<span>New run</span></a>
            <details class="orch-pop" data-orch-page-menu>
                <summary class="w-btn" data-variant="icon" aria-label="More actions" title="More actions">${icon('more-horizontal')}</summary>
                <div class="w-menu orch-pop__menu" role="menu">
                    <button type="button" class="w-menu__item" role="menuitem" data-orch-enrol><span class="w-menu__label">Quick enrol (join token only)</span></button>
                    <button type="button" class="w-menu__item" role="menuitem" data-orch-new-tool><span class="w-menu__label">New tool draft</span></button>
                    <hr class="w-menu__separator">
                    ${paused
                        ? '<button type="button" class="w-menu__item" role="menuitem" data-orch-dispatch="resume"><span class="w-menu__label">Resume dispatch</span></button>'
                        : '<button type="button" class="w-menu__item" role="menuitem" data-tone="danger" data-orch-dispatch="pause"><span class="w-menu__label">Pause all dispatch</span></button>'}
                </div>
            </details>`;
        bindAsk(wrap);
        wrap.querySelector('[data-orch-enrol]').addEventListener('click', () => { closePops(); Orch.nodes?.enrol(); });
        wrap.querySelector('[data-orch-new-tool]').addEventListener('click', () => { closePops(); Orch.studio?.newTool(); });
        wrap.querySelector('[data-orch-dispatch]').addEventListener('click', e => { closePops(); setDispatch(e.currentTarget.dataset.orchDispatch === 'pause', e.currentTarget); });
        return wrap;
    }
    function closePops() { document.querySelectorAll('details.orch-pop[open]').forEach(d => d.removeAttribute('open')); }
    document.addEventListener('click', e => { if (!e.target.closest('details.orch-pop')) closePops(); });
    document.addEventListener('keydown', e => { if (e.key === 'Escape') closePops(); });

    /* The state sentence (Mu §4.2): what is true now, zeros left out. */
    function stateParts() {
        const a = attention || {};
        const parts = [];
        const total = Number(a.total || 0);
        if (total) parts.push({ text: `${total} need${total === 1 ? 's' : ''} you`, tone: 'attention', href: '#/' });
        if (fleet) {
            const n = fleet.nodes || {};
            const by = n.by_status || {};
            const offline = by.offline || 0;
            if (!n.total) parts.push({ text: 'No nodes yet' });
            else if (!offline && (by.active || 0) === n.total) parts.push({ text: `${ui.plural(n.total, 'node')}, all online` });
            else {
                parts.push({ text: ui.plural(n.total, 'node') });
                if (offline) parts.push({ text: `${offline} offline`, tone: 'danger', href: '#/nodes?status=offline' });
                if (by.connecting) parts.push({ text: `${by.connecting} connecting`, tone: 'quiet', href: '#/nodes?status=connecting' });
            }
            const c = fleet.catalog || {};
            if (c.published) parts.push({ text: ui.plural(c.published, 'tool') });
            if (c.drafts) parts.push({ text: ui.plural(c.drafts, 'draft') });
        }
        parts.push(a.dispatch_paused ? { text: 'dispatch paused', tone: 'danger' } : { text: 'dispatching', tone: 'quiet', href: '#pause-dispatch' });
        return parts;
    }
    function paintHead() {
        if (!window.WashHead) return;
        const spec = { actions: headActions() };
        if (!detail) spec.state = stateParts();     // detail pages set their own (WashHead.set({state}))
        const more = MORE.find(([k]) => k === navTab);
        if (!detail) {
            spec.title = 'Orchestration';
            spec.back = null;
            spec.tabs = TABS.map(([k, label, href]) => ({ label, href, current: k === navTab }));
        } else {
            spec.back = detail.back;
            spec.tabs = [];
        }
        window.WashHead.set(spec);
        if (!detail) {
            const tabs = document.querySelector('#w-pagehead [data-head-tabs]');
            if (tabs && !tabs.querySelector('.orch-pop')) {
                /* Studio, Audit and Settings live under More; when one is open, More takes its
                 * name and is the current tab. */
                const d = document.createElement('details');
                d.className = 'orch-pop orch-tabs-more';
                d.innerHTML = `<summary class="w-pagetabs__tab"${more ? ' aria-current="page"' : ''}>${esc(more ? more[1] : 'More')} <span aria-hidden="true">▾</span></summary><div class="w-menu orch-pop__menu" role="menu">${MORE.map(([k, label, href]) => `<a class="w-menu__item" role="menuitem" href="${href}"${k === navTab ? ' aria-current="page"' : ''}><span class="w-menu__label">${esc(label)}</span></a>`).join('')}</div>`;
                d.addEventListener('click', e => { if (e.target.closest('a')) closePops(); });
                tabs.appendChild(d);
            }
        }
        paintAlert();
    }
    /* The leaf alert (Mu §3.1): one line, only while something is broken. */
    function paintAlert() {
        const el = document.querySelector('[data-leaf-alert]');
        if (!el) return;
        if (attention.dispatch_paused) {
            el.dataset.tone = 'danger';
            el.innerHTML = '<span>Dispatch is paused. Nothing new runs.</span><button type="button" class="w-textbtn" data-tone="danger" data-orch-resume>Resume</button>';
        } else if (!live) {
            el.dataset.tone = 'caution';
            el.innerHTML = '<span>Reconnecting to live updates…</span>';
        } else {
            delete el.dataset.tone;
            el.innerHTML = '';
        }
    }
    document.addEventListener('click', e => {
        const a = e.target.closest('a[href="#pause-dispatch"]');
        if (!a) return;
        e.preventDefault();
        setDispatch(true, a);
    });
    function setNav(nav) { navTab = NAV_TAB[nav] || nav; }
    /* Kept for route modules (setCrumbs in the render context): a trail of {label, to}. The
     * last label names the page; the nearest linked crumb is the back link. */
    function setCrumbs(trail) {
        const all = trail || [];
        const back = [...all].reverse().find(c => c.to);
        detail = back ? { back: { label: back.label, href: back.to } } : null;
        const last = all.length ? all[all.length - 1].label : '';
        document.title = `${last && detail ? last + ' · ' : ''}Orchestration · μcli`;
        if (detail && window.WashHead) window.WashHead.set({ state: [] });
        paintHead();
        if (detail && last && window.WashHead) window.WashHead.set({ title: last });
    }
    const isItem = route => { const g = groupOf(route.name); return !!g && route.name !== GROUPS[g].list; };
    /* The item's state sentence (Mu §4.2): under its heading in MAIN for list items, in the
     * bar on other detail routes. Route modules call Orch.itemState(parts). */
    function itemState(parts) {
        if (!isItem(parse())) { window.WashHead?.set({ state: parts }); return; }
        const head = viewEl.querySelector('.orch-head');
        if (!head) return;
        let p = head.querySelector('.orch-item__state');
        if (!p) { p = document.createElement('p'); p.className = 'w-state orch-item__state'; head.append(p); }
        p.innerHTML = ui.stateHtml(parts);
    }
    Orch.itemState = itemState;
    Orch.markSelected = () => markSelected();
    /* Route modules render their own heading (<h1> in .orch-head): the page header takes its
     * text on detail pages, and the in-view heading and its lede are hidden (said once). */
    /* Tab and item changes fade (Mu §4.3): applyRoute fades motionTargets out, then in here. */
    let routeSeq = 0;
    let fading = false;
    let motionTargets = [];
    let slowTimer = 0;
    let loadingShown = false;  // a slow page already faded its loading state in: fade the result in too
    function arrive({ loading = false } = {}) {
        fading = false;
        clearTimeout(slowTimer);
        const els = [listPane, mainPane, asidePane].filter(el => el.hasAttribute('data-leaving')
            || (loadingShown && (el === mainPane || el === asidePane)));
        loadingShown = loading;
        els.forEach(el => (window.WashMotion ? window.WashMotion.enter(el) : el.removeAttribute('data-leaving')));
    }
    function placeBack(route, g) {
        if (!isItem(route)) return;
        const title = viewEl.querySelector('.orch-item__title');
        shownItem = { href: location.hash || '#/', label: (title?.textContent || '').trim() || 'previous', group: g };
        if (!backTo || viewEl.querySelector('[data-orch-back]')) return;
        const a = document.createElement('a');
        a.className = 'w-textbtn orch-back';
        a.dataset.orchBack = '';
        a.dataset.tone = 'quiet';
        a.href = backTo.href;
        a.textContent = `\u2190 Back to ${backTo.label}`;
        viewEl.prepend(a);
    }
    function liftHeading() {
        const head = viewEl.querySelector('.orch-head');
        const h1 = (head || viewEl).querySelector('h1');
        /* A list item (a run, a node, a tool) keeps its heading at the top of MAIN: the bar
         * stays Orchestration with its tabs, so moving between items never loses the way. */
        if (isItem(parse())) {
            if (h1) { h1.hidden = false; h1.classList.add('orch-item__title'); }
            if (head) head.hidden = false;
            return;
        }
        if (h1 && detail && window.WashHead) window.WashHead.set({ title: h1.textContent.trim() });
        if (h1) h1.hidden = true;
        viewEl.querySelectorAll('.orch-head .w-page__lede').forEach(p => { p.hidden = true; });
        /* A wrapper left holding only hidden things goes too, then the header if nothing is left. */
        if (head) {
            const shows = node => [...node.childNodes].some(c => (c.nodeType === 3 && c.textContent.trim())
                || (c.nodeType === 1 && !c.hidden && (c.matches('[data-dirty], [data-rb-status], [data-effect], a, button, input, select') || shows(c))));
            [...head.children].forEach(c => { if (!c.hidden && !shows(c)) c.hidden = true; });
            head.hidden = !shows(head);
        }
    }

    async function applyRoute({ soft = false } = {}) {
        /* A live refresh waits for a route change that is still fading the old content out. */
        if (soft && fading) return;
        let route = parse();
        const def = Orch.routes[route.name] || Orch.routes.overview;
        const g = groupOf(route.name);
        const key = `${route.name}/${route.parts.join('/')}`;
        if (!soft) {
            /* Fade the old content out first (another row, tab or page); a newer route wins. */
            const seq = ++routeSeq;
            motionTargets = g && g === listGroup ? [mainPane, asidePane] : [listPane, mainPane, asidePane];
            if (!firstRender && window.WashMotion) {
                fading = true;
                await window.WashMotion.leave(motionTargets);
                if (seq !== routeSeq) return;
                fading = false;
                /* A slow page shows its loading state instead of staying blank. */
                clearTimeout(slowTimer);
                slowTimer = setTimeout(() => { if (seq === routeSeq) arrive({ loading: true }); }, 400);
            }
            const here = location.hash || '#/';
            if (!isItem(route)) backTo = null;
            else if (backTo && backTo.href === here) backTo = null;
            else if (shownItem && shownItem.href !== here && shownItem.group !== g) backTo = shownItem;
            shownItem = null;
            current.controller?.dispose?.();
            current = { key, route, def, controller: null };
            setNav(def.nav || route.name);
            const trail = def.crumbs ? def.crumbs(route) : [{ label: def.title || route.name }];
            setCrumbs(g ? trail.filter(c => !c.to) : trail);
            if (g || !isDetail(def, route)) detail = null;
            if (detail) viewEl.dataset.detail = ''; else delete viewEl.dataset.detail;
            delete viewEl.dataset.wide;
            viewEl.innerHTML = ui.loading();
            asidePane.innerHTML = '';
            mainPane.onscroll = null;
            if (!g) { listGroup = null; listController?.dispose?.(); listController = null; }
        }
        /* The group's list stays in LIST; only a new group (or a live change) re-renders it. */
        if (g && (listGroup !== g || soft)) await renderList(g, { soft: soft && listGroup === g });
        /* Runs and Catalog with no row chosen open their first row (wide leaves). */
        if (!soft && g && g !== 'nodes' && route.name === GROUPS[g].list && !narrow()) {
            const href = firstItemHref();
            if (href) {
                history.replaceState(null, '', href);
                return applyRoute();
            }
        }
        const token = ++renderToken;
        const focusKey = soft ? focusSignature() : null;
        const crumbs = g ? (trail => { const last = (trail || []).slice(-1)[0]; document.title = `${last ? last.label + ' · ' : ''}Orchestration · μcli`; }) : setCrumbs;
        try {
            const controller = await def.render(viewEl, route, { soft, isCurrent: () => token === renderToken && current.key === key, setCrumbs: crumbs, go, setQuery });
            if (token !== renderToken) return;
            liftHeading();
            placeBack(route, g);
            if (!soft) arrive();
            const hasAside = g ? placeAside() : false;
            const hasList = !!g || (route.name === 'settings' && placeSettingsNav());
            if (!soft) layout(route, { hasList, hasAside });
            else if (g) { panes.dataset.layout = hasAside ? 'list-main-aside' : 'list-main'; }
            markSelected();
            if (soft && controller && current.controller !== controller) current.controller?.dispose?.();
            if (!soft || controller) current.controller = controller || null;
            if (soft) restoreFocus(focusKey);
            else if (!firstRender) moveFocus();
            firstRender = false;
        } catch (err) {
            if (token !== renderToken) return;
            if (soft) { ui.toast(`Couldn’t refresh: ${err.message}`, 'danger'); return; }
            arrive();
            viewEl.innerHTML = ui.errorState(err);
            layout(route, { hasList: !!g, hasAside: false });
            viewEl.querySelector('[data-orch-retry]')?.addEventListener('click', () => applyRoute());
        }
    }
    function moveFocus() {
        mainPane.scrollTo?.({ top: 0 });
        asidePane.scrollTo?.({ top: 0 });
        /* A row chosen from the list keeps focus in the list (j/k keep working). */
        if (listPane.contains(document.activeElement)) return;
        const h = isItem(parse()) ? viewEl.querySelector('.orch-item__title') : document.querySelector('#w-pagehead [data-head-title]');
        if (h) { h.setAttribute('tabindex', '-1'); h.focus({ preventScroll: true }); }
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
    const softList = ui.debounce(() => { if (listGroup) renderList(listGroup, { soft: true }); }, 350);
    /* What the operator is looking at, for the fleet chat's "about this" context. */
    Orch.context = () => {
        const route = parse();
        const kind = { node: 'node', run: 'run', tool: 'tool', runbook: 'runbook' }[route.name];
        if (!kind || !route.parts[0]) return null;
        const label = shownItem && shownItem.href === (location.hash || '#/') ? shownItem.label : route.parts[0];
        return { kind, id: route.parts[0], label };
    };
    Orch.repaintHead = () => paintHead();
    Orch.refresh = () => applyRoute({ soft: true });
    Orch.go = go;
    Orch.setQuery = setQuery;
    Orch.parse = parse;

    /* ------------------------------------------------------------ attention */
    function paintAttention(att) {
        attention = att || {};
        document.dispatchEvent(new CustomEvent('mucli:orch-attention', { detail: { ...attention, summary: describeAttention(attention) } }));
        paintHead();
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
        try {
            const o = await api.get('overview');
            fleet = { nodes: o.nodes, catalog: o.catalog, network: o.network };
            paintAttention(o.attention);
        } catch (_) { /* next change retries */ }
    }
    Orch.refreshAttention = refreshAttention;
    Orch.attention = () => attention;
    const refreshAttentionSoon = ui.debounce(refreshAttention, 400);

    function setLive(on) {
        live = !!on;
        paintAlert();
    }

    /* Components that follow the change stream themselves (pickers) subscribe here; they see
     * every change before the current view does. */
    const listeners = new Set();
    Orch.subscribe = fn => { listeners.add(fn); return () => listeners.delete(fn); };

    function onChange(change) {
        refreshAttentionSoon();
        listeners.forEach(fn => { try { fn(change); } catch (err) { console.error(err); } });
        const c = current.controller;
        const scopes = current.def?.scopes || [];
        /* The list follows its own scope; a main re-render (soft) refreshes it too. */
        if (listGroup && !c?.onChange && !scopes.includes(change.scope) && (Orch.routes[GROUPS[listGroup].list]?.scopes || []).includes(change.scope)) softList();
        if (c?.onChange) { c.onChange(change); return; }
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
    /* Dispatch paused is the leaf alert's line now (said once); modules may still call this. */
    Orch.pausedBanner = () => '';
    document.addEventListener('click', e => {
        const r = e.target.closest('[data-orch-resume]');
        if (r) setDispatch(false, r);
    });

    /* ------------------------------------------------------------ Now (Mu §5.2) */
    /* Everything that needs you, in the order you must act: safety decisions (runs to approve,
     * paused runs, stuck executions) first, then nodes to accept, reviews, then problems. Each
     * row carries its decisive action; approving still goes through runAction, so typed
     * confirmation and policy apply exactly as on the run page. */
    const byAI = who => /^ai:/.test(String(who || ''));
    function nowRows(o, a) {
        const out = [];
        const bind = [];
        (a.runs || []).forEach((r, i) => {
            const deny = r.decision?.level === 'deny';
            out.push(ui.row({
                status: deny ? 'failed' : 'waiting', title: `${deny ? 'Denied' : 'Approve'} · ${r.title}`, href: `#/run/${encodeURIComponent(r.id)}`,
                sub: ui.subline([String(r.title || '').includes(r.blast_radius || '\u0000') ? '' : esc(r.blast_radius || ui.plural(r.nodes || 0, 'node')), r.check_mode ? 'check mode' : '', ui.effectWord(r.effect_style, r.effect),
                    esc(r.decision?.summary || ''), byAI(r.proposed_by) ? `proposed by the AI (${esc(r.proposed_by.slice(3))})` : '']),
                when: ui.time(r.created_at), pinned: true, attrs: ` data-now-run="${i}"`,
                actions: ui.rowActions(r.actions, { only: ['approveRun', 'rejectRun'] }),
            }));
            bind.push(['[data-now-run="' + i + '"]', r.actions, { args: { run_id: r.id } }]);
        });
        (a.paused || []).forEach((r, i) => {
            out.push(ui.row({
                status: 'waiting', title: `Paused · ${r.title}`, href: `#/run/${encodeURIComponent(r.id)}`,
                sub: esc(r.paused_reason || 'Waiting for you to continue, roll back or cancel.'),
                when: ui.time(r.created_at), pinned: true, attrs: ` data-now-paused="${i}"`,
                actions: r.actions ? ui.rowActions(r.actions, { only: ['continueRun', 'rollbackRun', 'cancelRun'] }) : `<a class="w-textbtn" href="#/run/${encodeURIComponent(r.id)}">Decide</a>`,
            }));
            if (r.actions) bind.push(['[data-now-paused="' + i + '"]', r.actions, { args: { run_id: r.id } }]);
        });
        (o.unreachable || []).forEach(x => out.push(ui.row({
            status: 'unverified', title: `Unreachable · ${x.tool} on ${x.node_id}`, href: `#/run/${encodeURIComponent(x.run_id)}`,
            sub: 'The node dropped mid-run; this execution needs a decision.', pinned: true,
            actions: `<a class="w-textbtn" href="#/run/${encodeURIComponent(x.run_id)}">Decide</a>`,
        })));
        (a.nodes || []).forEach((n, i) => {
            out.push(ui.row({
                status: 'waiting', title: `Accept · ${n.name || n.id}`, href: `#/node/${encodeURIComponent(n.id)}`,
                sub: ui.subline([esc(n.address || ''), Object.keys(n.labels || {}).length ? esc(ui.formatLabels(n.labels)) : 'no labels', esc(n.summary?.os || '')]),
                when: ui.time(n.created_at), pinned: true, attrs: ` data-now-node="${i}"`,
                actions: ui.rowActions(n.actions, { only: ['acceptNode', 'revokeNode'] }),
            }));
            bind.push(['[data-now-node="' + i + '"]', n.actions, { args: { node_id: n.id }, subject: n.name }]);
        });
        (a.reviews || []).forEach(t => out.push(ui.row({
            status: 'waiting', title: `Review · ${t.title}`, href: `${Orch.catalog.toolHref(t)}?tab=diff`,
            sub: ui.subline([`<span class="w-mono">${esc(t.ref)}</span>`, ui.effectWord(t.effect_style, t.effect), t.created_by ? `by ${esc(t.created_by)}` : '']),
            when: ui.time(t.created_at), pinned: true,
            actions: `<a class="w-textbtn" href="${esc(Orch.catalog.toolHref(t))}?tab=diff">Review</a>`,
        })));
        (a.runbook_reviews || []).forEach((r, i) => {
            out.push(ui.row({
                status: 'waiting', title: `Review · ${r.title}`, href: Orch.catalog.runbookHref(r),
                sub: ui.subline([`<span class="w-mono">${esc(r.ref)}</span>`, ui.plural((r.steps || []).length, 'step'), ui.effectWord(r.effect_style, r.effect),
                    (r.problems || []).length ? `<span data-tone="danger">${r.problems.map(esc).join('; ')}</span>` : '']),
                pinned: true, attrs: ` data-now-rb="${i}"`,
                actions: ui.rowActions(r.actions, { only: ['publishRunbook', 'requestRunbookChanges'] }) || `<a class="w-textbtn" href="${esc(Orch.catalog.runbookHref(r))}">Review</a>`,
            }));
            bind.push(['[data-now-rb="' + i + '"]', r.actions, { args: { ref: r.ref }, subject: r.ref }]);
        });
        const att = o.attention || {};
        if (att.offline_nodes) out.push(ui.row({
            status: 'failed', title: `${ui.plural(att.offline_nodes, 'node is', 'nodes are')} offline`, href: '#/nodes?status=offline',
            sub: 'Not seen for over 90 seconds.',
        }));
        const reboot = o.reboot || { count: 0, nodes: [] };
        if (reboot.count) {
            out.push(ui.row({
                status: 'waiting', title: `${ui.plural(reboot.count, 'node needs', 'nodes need')} a reboot`, href: '#/nodes',
                sub: esc(reboot.nodes.slice(0, 6).map(x => x.name).join(', ') + (reboot.count > 6 ? ` and ${reboot.count - 6} more` : '')
                    + (reboot.nodes[0]?.reasons?.[0] ? ` · ${reboot.nodes[0].reasons[0]}` : '')),
                pinned: true, attrs: ' data-now-reboot',
                actions: '<button type="button" class="w-textbtn" data-reboot-needed>Reboot one at a time…</button>',
            }));
            bind.push(['[data-now-reboot]', null, { custom: node => node.querySelector('[data-reboot-needed]')?.addEventListener('click', async e => {
                const button = e.currentTarget;
                const ok = await ui.confirm({ title: `Reboot ${ui.plural(reboot.count, 'node')}?`,
                    message: `${reboot.nodes.map(x => x.name).join(', ')} reboot one at a time, each only if it still needs one. The run waits for your approval as usual.`,
                    confirmLabel: 'Propose reboots', tone: 'danger' });
                if (!ok) return;
                try {
                    const res = await ui.command('nodeMaintenance', { node_ids: reboot.nodes.map(x => x.node_id), action: 'reboot', params: { only_if_required: true } },
                        { ok: 'Reboots proposed', button });
                    Orch.go(`#/run/${encodeURIComponent(res.data.run.id)}`);
                } catch (_) { /* toasted */ }
            }) }]);
        }
        if (!(o.network || {}).bind) out.push(ui.row({
            status: 'waiting', title: 'Choose a network for nodes', href: '#/settings?section=network',
            sub: 'The node endpoint isn’t configured yet, so nodes can’t enrol.', pinned: true,
            actions: '<a class="w-textbtn" href="#/settings?section=network">Open network settings</a>',
        }));
        return { out, bind };
    }

    /* The board (Mu §11b): Now spans the top when anything waits; then the fleet with its
     * meters, dispatch and the catalog, recent runs with progress, and the audit trail. */
    const AUDIT_WORDS = {
        'run.approve': 'approved', 'run.reject': 'rejected', 'run.propose': 'proposed', 'run.start': 'started', 'run.cancel': 'cancelled',
        'run.continue': 'continued', 'run.rollback': 'rolled back', 'node.accept': 'accepted', 'node.enrol': 'enrolled', 'node.quarantine': 'quarantined',
        'tool.publish': 'published', 'tool.submit': 'submitted', 'dispatch.pause': 'paused dispatch', 'dispatch.resume': 'resumed dispatch',
    };
    const actorWord = a => (a === 'operator' ? 'You' : (/^ai:/.test(a || '') ? 'The AI' : (/^node:/.test(a || '') ? a.slice(5) : ui.cap(a || 'system'))));
    function auditRow(e) {
        const what = AUDIT_WORDS[e.action] || String(e.action || '').replace(/[._]/g, ' ');
        const href = /^r_/.test(e.subject || '') ? `#/run/${encodeURIComponent(e.subject)}` : (/^n_/.test(e.subject || '') ? `#/node/${encodeURIComponent(e.subject)}` : '');
        return ui.row({ title: `${actorWord(e.actor)} ${what}`, href, sub: `<span class="w-mono">${esc(e.subject || '')}</span>`, when: ui.time(e.at) });
    }
    async function renderOverview(el, route, { isCurrent }) {
        const [o, a, nodes, audit] = await Promise.all([api.get('overview'), api.get('approvals'),
            api.get('nodes').catch(() => ({ nodes: [] })), api.get('audit?limit=10').catch(() => ({ entries: [] }))]);
        if (!isCurrent()) return null;
        fleet = { nodes: o.nodes, catalog: o.catalog, network: o.network };
        paintAttention(o.attention);
        const { out, bind } = nowRows(o, a);
        const recent = o.recent_runs || [];
        const firstRun = !o.nodes.total && !recent.length;
        const fleetNodes = (nodes.nodes || []).slice().sort((x, y) => {
            const rank = n => (n.status === 'pending' ? 0 : (n.status === 'active' && (n.presence || (n.online ? 'online' : 'offline')) === 'offline' ? 1 : 2));
            const load = n => Math.max(n.summary?.max_disk_used_pct ?? 0, n.summary?.memory_used_pct ?? 0);
            return rank(x) - rank(y) || load(y) - load(x);
        });
        const c = o.catalog || {};
        const paused = !!(o.attention || {}).dispatch_paused;
        const entries = (audit.entries || []).slice().sort((x, y) => (y.seq || 0) - (x.seq || 0)).slice(0, 10);
        el.innerHTML = `
            <header class="orch-head"><h1 class="w-page__title">Now</h1></header>
            ${firstRun ? '<div class="orch-firstrun">' + ui.empty('Set up your first node', 'Run reviewed tools across your machines. Nothing runs without a plan you can read first.', { icon: 'server', size: '', action: '<a class="w-btn" data-variant="primary" href="#/setup">Set up a node</a><button type="button" class="w-textbtn" data-orch-enrol>Quick enrol</button>' }) + '</div>' : ''}
            <div class="w-board orch-board orch-nowboard">
                ${out.length ? `<section class="w-board__cell orch-now" data-span="all" aria-labelledby="orch-now-h">
                    <header class="w-pane__head"><h2 class="w-pane__title" id="orch-now-h">Now</h2><span class="orch-sub">${esc(describeAttention(o.attention || {}))}</span></header>
                    ${ui.rows(out, { label: 'Needs you' })}</section>` : ''}
                ${firstRun ? '' : `<section class="w-board__cell" data-span="2" aria-labelledby="orch-fleet-h">
                    <header class="w-pane__head"><h2 class="w-pane__title" id="orch-fleet-h">Fleet</h2><a class="w-group__more" href="#/nodes">All nodes</a></header>
                    ${ui.rows(fleetNodes.slice(0, 8).map(n => Orch.nodes.nodeRow(n, { pinnedAccept: false })), { label: 'Fleet', empty: 'No nodes enrolled yet.' })}
                    ${fleetNodes.length > 8 ? `<p class="orch-sub">and ${esc(fleetNodes.length - 8)} more</p>` : ''}</section>
                <section class="w-board__cell orch-dispatch" aria-labelledby="orch-disp-h">
                    <header class="w-pane__head"><h2 class="w-pane__title" id="orch-disp-h">Dispatch</h2></header>
                    <span class="w-board__big"${paused ? ' data-tone="danger"' : ''}>${paused ? 'Paused' : 'Dispatching'}</span>
                    <p class="orch-sub">${paused ? 'No new work goes to any node until you resume.' : 'Approved runs go out as soon as their batch is due.'}</p>
                    <p>${paused ? '<button type="button" class="w-textbtn" data-orch-resume>Resume dispatch</button>' : '<button type="button" class="w-textbtn" data-tone="danger" data-board-pause>Pause all dispatch</button>'}</p>
                    <dl class="w-facts orch-kv orch-catalog-facts">
                        <dt>Published tools</dt><dd><a href="#/catalog">${esc(c.published || 0)}</a></dd>
                        ${c.in_review ? `<dt>In review</dt><dd><a href="#/catalog?show=open" data-tone="attention">${esc(c.in_review)}</a></dd>` : ''}
                        ${c.drafts ? `<dt>Drafts</dt><dd><a href="#/catalog?show=open">${esc(c.drafts)}</a></dd>` : ''}
                    </dl>
                </section>
                <section class="w-board__cell" data-span="2" aria-labelledby="orch-recent-h">
                    <header class="w-pane__head"><h2 class="w-pane__title" id="orch-recent-h">Recent runs</h2><a class="w-group__more" href="#/runs">All runs</a></header>
                    ${Orch.runs.runRows(recent.slice(0, 8), { empty: 'No runs yet. Start one from a published tool.' })}</section>
                <section class="w-board__cell" aria-labelledby="orch-act-h">
                    <header class="w-pane__head"><h2 class="w-pane__title" id="orch-act-h">Activity</h2><a class="w-group__more" href="#/audit">Audit log</a></header>
                    ${ui.rows(entries.map(auditRow), { label: 'Activity', empty: 'Nothing recorded yet.' })}</section>`}
            </div>`;
        const done = () => { Orch.refresh(); Orch.refreshAttention?.(); };
        bind.forEach(([sel, actions, ctx]) => {
            const node = el.querySelector(sel);
            if (!node) return;
            if (ctx && typeof ctx.custom === 'function') ctx.custom(node);
            else if (actions) ui.bindActions(node, actions, { ...ctx, done });
        });
        el.querySelectorAll('[data-orch-enrol]').forEach(b => b.addEventListener('click', () => Orch.nodes?.enrol()));
        el.querySelector('[data-board-pause]')?.addEventListener('click', e => setDispatch(true, e.currentTarget));
        return null;
    }
    Orch.routes.overview = { nav: 'overview', title: 'Now', scopes: ['node', 'tool', 'run'], render: renderOverview, crumbs: () => [{ label: 'Now' }] };
    /* Approvals is Now (Mu §10.3): the old route keeps working. */
    Orch.routes.approvals = { nav: 'approvals', title: 'Now', scopes: ['node', 'tool', 'run'], render: renderOverview, crumbs: () => [{ label: 'Now' }] };

    /* ------------------------------------------------------------ palette */
    if (window.WashPalette) {
        const nav = [['overview', 'Now', '#/', 'layout-dashboard'], ['nodes', 'Nodes', '#/nodes', 'server'], ['catalog', 'Catalog', '#/catalog', 'book-open'],
            ['studio', 'Tool studio', '#/studio', 'sparkles'], ['runs', 'Runs', '#/runs', 'play'],
            ['audit', 'Audit log', '#/audit', 'history'], ['settings', 'Orchestration settings', '#/settings', 'settings']];
        window.WashPalette.register('orchestration', () => [
            ...nav.map(([id, label, href, ic]) => ({ id: `orch:${id}`, group: 'Orchestration', label, icon: ic, run: () => go(href), keywords: id === 'overview' ? 'orchestration fleet approvals needs you' : 'orchestration fleet' })),
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
        api.events({ onChange, onHello: data => paintAttention(data.attention), onState: setLive,
                     onResync: () => { refreshAttention(); Orch.refresh(); } });
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
