/* Orchestration — node groups and the node dashboard (mu/orchestration/vm.py fleet_dashboard).
 *
 * A group is a name, an optional label selector and explicit members; a node belongs when it
 * is a member (dragged in) or the selector matches its labels.
 *
 *   Nodes   LIST: the groups (All nodes, each group with its health, Ungrouped, + Create
 *           group) above the node inventory of the chosen group. Node rows drag onto a group:
 *           dropped = added (moved when you were looking at another group); dropped on
 *           Ungrouped while looking at a group = taken out of it. "Group…" on a row does the
 *           same without dragging.
 *           MAIN (#/nodes, ?group=NAME, ?group=- ungrouped): the dashboard for that scope:
 *           health worst first with the reasons, resources, live work, the last day's runs,
 *           and "Run on this group".
 *   Groups  the Groups tab: LIST of groups, #/group/<name> to rename, describe, set the
 *           selector, edit members and delete.
 * Every change goes through POST /api/orchestration/cmd (putGroup, addToGroup,
 * removeFromGroup, renameGroup, deleteGroup) as the operator.
 */
(() => {
    'use strict';
    const api = window.OrchAPI;
    const ui = window.OrchUI;
    const { esc, icon } = ui;
    const Orch = (window.Orch = window.Orch || { routes: {} });
    Orch.routes = Orch.routes || {};

    const ALL = '*';
    const UNGROUPED = '-';
    const DRAG_TYPE = 'application/x-mucli-nodes';
    /* The scope the operator last chose (kept while a node page is open). */
    let currentScope = ALL;

    const scopeFromRoute = route => {
        const g = route?.query?.get('group');
        return g === null || g === undefined || g === '' ? ALL : g;
    };
    const scopeHref = scope => (scope === ALL ? '#/nodes?group=*' : `#/nodes?group=${encodeURIComponent(scope)}`);
    const isGroupName = scope => !!scope && scope !== ALL && scope !== UNGROUPED;

    /* ------------------------------------------------------------ drag and drop → command
     * Pure: which command a drop means (exported for tests). `from` is the scope the rows were
     * dragged from; `target` the group entry they were dropped on. null = nothing to do. */
    function dropCommand({ nodeIds, from, target }) {
        const ids = [...new Set((nodeIds || []).map(String).filter(Boolean))];
        if (!ids.length || !target || target === ALL) return null;
        if (target === UNGROUPED) {
            return isGroupName(from) ? { type: 'removeFromGroup', args: { name: from, node_ids: ids } } : null;
        }
        if (target === from) return null;
        const args = { name: target, node_ids: ids };
        if (isGroupName(from)) args.from = from;
        return { type: 'addToGroup', args };
    }
    const dropWords = (cmd, count) => {
        const n = ui.plural(count, 'node');
        if (cmd.type === 'removeFromGroup') return `${n} taken out of ${cmd.args.name}`;
        return cmd.args.from ? `${n} moved to ${cmd.args.name}` : `${n} added to ${cmd.args.name}`;
    };

    async function applyGroupCommand(cmd, count) {
        try {
            const res = await api.cmd(cmd.type, cmd.args);
            const still = res?.data?.still_matched_by_selector || [];
            ui.toast(dropWords(cmd, count) + (still.length ? `. ${ui.plural(still.length, 'node stays', 'nodes stay')} in it: the group’s selector matches ${still.length === 1 ? 'its' : 'their'} labels.` : ''), still.length ? 'attention' : 'ok');
            Orch.refresh?.();
            return true;
        } catch (err) {
            ui.toast(err.message, 'danger');
            return false;
        }
    }

    /* Rows carry draggable + data-node-id; group entries data-drop-group. Listeners live on the
     * document so rows in LIST and MAIN both work and survive re-renders. */
    let dragging = null;
    document.addEventListener('dragstart', e => {
        const row = e.target.closest?.('[data-node-id][draggable="true"]');
        if (!row) return;
        dragging = { nodeIds: [row.dataset.nodeId], from: currentScope };
        try {
            e.dataTransfer.setData(DRAG_TYPE, JSON.stringify(dragging));
            e.dataTransfer.setData('text/plain', row.dataset.nodeName || row.dataset.nodeId);
            e.dataTransfer.effectAllowed = 'copyMove';
        } catch (_) { /* old browsers */ }
        document.documentElement.setAttribute('data-orch-dragging', '');
        row.setAttribute('data-dragging', '');
    });
    document.addEventListener('dragend', e => {
        dragging = null;
        document.documentElement.removeAttribute('data-orch-dragging');
        e.target.closest?.('[data-dragging]')?.removeAttribute('data-dragging');
        document.querySelectorAll('[data-drop-over]').forEach(x => x.removeAttribute('data-drop-over'));
    });
    const dropTarget = e => {
        const t = e.target.closest?.('[data-drop-group]');
        if (!t || !dragging) return null;
        return dropCommand({ nodeIds: dragging.nodeIds, from: dragging.from, target: t.dataset.dropGroup }) ? t : null;
    };
    document.addEventListener('dragover', e => {
        const t = dropTarget(e);
        if (!t) return;
        e.preventDefault();
        e.dataTransfer.dropEffect = isGroupName(dragging.from) ? 'move' : 'copy';
        if (!t.hasAttribute('data-drop-over')) {
            document.querySelectorAll('[data-drop-over]').forEach(x => x.removeAttribute('data-drop-over'));
            t.setAttribute('data-drop-over', '');
        }
    });
    document.addEventListener('dragleave', e => {
        const t = e.target.closest?.('[data-drop-group]');
        if (t && !t.contains(e.relatedTarget)) t.removeAttribute('data-drop-over');
    });
    document.addEventListener('drop', e => {
        const t = e.target.closest?.('[data-drop-group]');
        if (!t || !dragging) return;
        e.preventDefault();
        t.removeAttribute('data-drop-over');
        const cmd = dropCommand({ nodeIds: dragging.nodeIds, from: dragging.from, target: t.dataset.dropGroup });
        if (cmd) applyGroupCommand(cmd, dragging.nodeIds.length);
    });

    /* The same without dragging (keyboard, touch): "Group…" on a node row. */
    async function groupMenu(node, groups) {
        const form = document.createElement('form');
        form.className = 'orch-stack';
        form.noValidate = true;
        const id = ui.uid('grp');
        const inScope = isGroupName(currentScope) && (node.groups || []).includes(currentScope);
        const others = groups.filter(g => !(node.groups || []).includes(g.name));
        form.innerHTML = `
            <p class="orch-muted">${esc(node.name)} is ${node.groups?.length ? `in ${node.groups.map(esc).join(', ')}` : 'in no group'}.</p>
            ${others.length ? `<div class="w-field"><label class="w-field__label" for="${id}">Group</label>
                <span class="w-select"><select class="w-select__control" id="${id}" data-autofocus>${others.map(g => `<option value="${esc(g.name)}">${esc(g.name)}</option>`).join('')}</select></span></div>`
                : '<p class="orch-muted">It is already in every group. Create another from the groups list.</p>'}
            <p class="w-field__error" data-error role="alert"></p>
            <div class="w-btn-row orch-dialog__actions">
                <button type="button" class="w-btn" data-variant="secondary" data-cancel>Cancel</button>
                ${inScope ? `<button type="button" class="w-btn" data-variant="secondary" data-remove>Take out of ${esc(currentScope)}</button>` : ''}
                ${others.length && inScope ? `<button type="button" class="w-btn" data-variant="secondary" data-move>Move</button>` : ''}
                ${others.length ? '<button type="submit" class="w-btn" data-variant="primary">Add to group</button>' : ''}
            </div>`;
        const d = ui.dialog({ title: `Group ${node.name}`, body: form });
        const run = async (cmd, button) => {
            if (!cmd) return;
            const ok = await ui.busy(button, () => applyGroupCommand(cmd, 1));
            if (ok) d.close();
        };
        const chosen = () => form.querySelector(`#${id}`)?.value;
        form.querySelector('[data-cancel]').onclick = () => d.close();
        form.querySelector('[data-remove]')?.addEventListener('click', e => run(dropCommand({ nodeIds: [node.id], from: currentScope, target: UNGROUPED }), e.currentTarget));
        form.querySelector('[data-move]')?.addEventListener('click', e => run(dropCommand({ nodeIds: [node.id], from: currentScope, target: chosen() }), e.currentTarget));
        form.onsubmit = e => { e.preventDefault(); run(dropCommand({ nodeIds: [node.id], from: ALL, target: chosen() }), form.querySelector('[type=submit]')); };
    }

    /* ------------------------------------------------------------ shared bits */
    const HEALTH_SIGNS = [['critical', 'failed'], ['warning', 'unverified'], ['pending', 'waiting']];
    /* A group's health as status signs with counts (✗ 2 ? 1), or ✓ when all are healthy. */
    function healthSigns(health, nodes) {
        const h = health || {};
        const parts = HEALTH_SIGNS.filter(([k]) => h[k]).map(([k, st]) => {
            const s = ui.vstyle('orch_node_health', k);
            return `<span class="orch-hcount" data-status="${st}" title="${esc(ui.plural(h[k], `${s.label} node`))}"><span class="w-sign" data-status="${st}" aria-hidden="true">${esc(s.glyph)}</span>${esc(h[k])}</span>`;
        });
        if (!parts.length) {
            if (!nodes) return '';
            const s = ui.vstyle('orch_node_health', 'ok');
            return `<span class="orch-hcount" data-status="done" title="All healthy"><span class="w-sign" data-status="done" aria-hidden="true">${esc(s.glyph)}</span></span>`;
        }
        return parts.join('');
    }
    const healthWords = h => HEALTH_SIGNS.filter(([k]) => (h || {})[k]).map(([k]) => `${h[k]} ${ui.vstyle('orch_node_health', k).label}`);

    async function createGroup(name, { description = '', button = null } = {}) {
        name = String(name || '').trim();
        if (!name) throw new Error('Name the group first.');
        await ui.busy(button, () => api.cmd('putGroup', { name, description }));
        ui.toast(`Group ${name} created`, 'ok');
        return name;
    }

    /* ------------------------------------------------------------ Nodes: the LIST pane */
    /* The Nodes LIST: the groups, then the chosen group's inventory. It follows the hash itself
     * (the main pane's dashboard renders separately), keeping its own record of the scope it
     * shows and numbering its renders, so a quick run of group switches can neither be skipped
     * nor finish out of order (only the newest render writes). */
    async function renderNodesList(el, route, ctx) {
        let seq = 0;
        let shown = null;
        const routeScope = () => { const r = Orch.parse?.(); return r && r.name === 'nodes' ? scopeFromRoute(r) : null; };
        async function paint(scope) {
            const mine = ++seq;
            shown = scope;
            const live = () => mine === seq && ctx.isCurrent();
            const data = await api.get('dashboard');
            if (!live()) return;
            const entry = (key, title, sub, health, nodes, { drop = true, href } = {}) => ui.row({
                title, href: href || scopeHref(key), sub, when: healthSigns(health, nodes),
                attrs: `${drop ? ` data-drop-group="${esc(key)}"` : ''} data-group-entry="${esc(key)}"${key === scope ? ' data-selected' : ''}`,
            });
            const counts = g => ui.subline([ui.plural(g.nodes, 'node'), g.nodes ? `${esc(g.online)} online` : '']);
            el.innerHTML = `
                <section class="orch-grouplist" aria-labelledby="orch-gl-h">
                    <div class="orch-grouplist__head"><h3 class="orch-label-text" id="orch-gl-h">Groups</h3>
                        <button type="button" class="w-textbtn" data-tone="quiet" data-new-group aria-expanded="false">${icon('plus', 'sm')}<span>Create group</span></button></div>
                    <form class="orch-grouplist__new" data-new-group-form hidden novalidate>
                        <label class="w-visually-hidden" for="orch-new-group">New group name</label>
                        <input class="w-input" data-mono id="orch-new-group" placeholder="group name" autocomplete="off" pattern="[A-Za-z0-9._-]+">
                        <button type="submit" class="w-btn" data-variant="primary" data-size="sm">Create</button>
                        <button type="button" class="w-btn" data-variant="ghost" data-size="sm" data-cancel>Cancel</button>
                        <p class="w-field__error" data-error role="alert"></p>
                    </form>
                    <ul class="w-rows orch-grouplist__rows" aria-label="Node groups">
                        ${entry(ALL, 'All nodes', counts(data.fleet), data.fleet.health, data.fleet.nodes, { drop: false })}
                        ${data.groups.map(g => entry(g.name, g.name, counts(g), g.health, g.nodes)).join('')}
                        ${entry(UNGROUPED, 'Ungrouped', counts(data.ungrouped), data.ungrouped.health, data.ungrouped.nodes)}
                    </ul>
                    ${data.groups.length ? '<p class="orch-grouplist__hint orch-muted">Drag a node onto a group to add it.</p>' : '<p class="orch-grouplist__hint orch-muted">Create a group, then drag nodes onto it.</p>'}
                </section>
                <section class="orch-grouplist__nodes" aria-labelledby="orch-gl-n"><h3 class="orch-label-text" id="orch-gl-n">${esc(scope === ALL ? 'All nodes' : scope === UNGROUPED ? 'Ungrouped nodes' : `Nodes in ${scope}`)}</h3><div data-inventory></div></section>`;
            const form = el.querySelector('[data-new-group-form]');
            const toggle = el.querySelector('[data-new-group]');
            const open = on => { form.hidden = !on; toggle.setAttribute('aria-expanded', String(on)); if (on) form.querySelector('input').focus(); };
            toggle.addEventListener('click', () => open(form.hidden));
            form.querySelector('[data-cancel]').addEventListener('click', () => open(false));
            form.addEventListener('submit', async e => {
                e.preventDefault();
                try {
                    const name = await createGroup(form.querySelector('input').value, { button: form.querySelector('[type=submit]') });
                    open(false);
                    Orch.go(scopeHref(name));
                } catch (err) { form.querySelector('[data-error]').innerHTML = `<span aria-hidden="true">✗</span> ${esc(err.message)}`; }
            });
            await Orch.nodes.renderInventory(el.querySelector('[data-inventory]'), route, { ...ctx, isCurrent: live, scopeGroup: scope, draggable: true, tools: null });
            if (ctx.tools) {
                ctx.tools.innerHTML = '<button type="button" class="w-textbtn" data-tone="quiet" data-enrol title="Create a join token for one machine">Quick enrol</button>';
                ctx.tools.querySelector('[data-enrol]').onclick = () => Orch.nodes.enrol();
            }
        }
        await paint(routeScope() ?? currentScope);
        const onHash = () => {
            const next = routeScope();
            if (next === null || next === shown) return;
            paint(next).catch(err => { if (ctx.isCurrent()) el.innerHTML = ui.errorState(err, { retry: false }); });
        };
        window.addEventListener('hashchange', onHash);
        return { dispose: () => { seq += 1; window.removeEventListener('hashchange', onHash); } };
    }

    /* ------------------------------------------------------------ Nodes: the dashboard (MAIN) */
    const pct = v => (v === null || v === undefined ? '—' : `${Math.round(Number(v))}%`);
    function nodeHealthRow(n, groups) {
        const h = n.health || {};
        const tone = h.level === 'critical' ? 'danger' : h.level === 'warning' ? 'attention' : '';
        const busy = n.busy ? (n.busy.state === 'running'
            ? `<span class="orch-busy" data-tone="running">Running ${esc(n.busy.title)}</span>` : `<span class="orch-busy">Queued: ${esc(n.busy.title)}</span>`) : '';
        const reasons = (h.reasons || []).length ? `<span${tone ? ` data-tone="${tone}"` : ''}>${(h.reasons || []).map(esc).join(' · ')}</span>` : '';
        return ui.row({
            status: n.busy?.state === 'running' && h.level === 'ok' ? 'active' : ui.statusKey(h.style),
            title: n.name, href: `#/node/${encodeURIComponent(n.id)}`,
            titleHtml: `${esc(n.name)}${n.canary ? ' <span class="orch-tag">canary</span>' : ''}`,
            sub: ui.subline([reasons, busy, Orch.nodes.meter('disk', n.max_disk_used_pct),
                (n.groups || []).length ? `<span class="orch-muted">${n.groups.map(esc).join(', ')}</span>` : ''])
                /* The node's own CPU, memory, network and disk I/O sparklines (filled from the history fetch). */
                + `<span class="orch-sparks-slot" data-node-spark="${esc(n.id)}"></span>`,
            when: esc(n.last_seen_ago || ''),
            actions: groups.length ? `<button type="button" class="w-textbtn" data-tone="quiet" data-group-node="${esc(n.id)}" aria-label="Group ${esc(n.name)}">Group…</button>` : '',
            attrs: ` draggable="true" data-node-id="${esc(n.id)}" data-node-name="${esc(n.name)}"`,
        });
    }

    const H = window.OrchHistory;
    const SORT_KEY = 'orch-health-sort';
    const healthSort = () => { const v = localStorage.getItem(SORT_KEY) || 'health'; return H.SORTS.some(([k]) => k === v) ? v : 'health'; };

    async function renderDashboard(el, route, { isCurrent }) {
        const scope = scopeFromRoute(route);
        currentScope = scope;
        const q = scope === ALL ? '' : `?group=${encodeURIComponent(scope)}`;
        let d;
        try { d = await api.get(`dashboard${q}`); }
        catch (err) {
            if (err.status === 404) {
                el.innerHTML = ui.empty('That group doesn’t exist', 'It may have been renamed or deleted.', { icon: 'layers', action: '<a class="w-btn" data-variant="secondary" href="#/nodes">All nodes</a>' });
                return null;
            }
            throw err;
        }
        if (!isCurrent()) return null;
        const t = d.totals;
        const r = d.resources;
        const group = isGroupName(scope) ? d.groups.find(g => g.name === scope) : null;
        const state = [
            { text: ui.plural(t.nodes, 'node') },
            t.presence.online ? { text: `${t.presence.online} online` } : null,
            t.presence.connecting ? { text: `${t.presence.connecting} connecting`, tone: 'quiet' } : null,
            t.presence.offline ? { text: `${t.presence.offline} offline`, tone: 'danger' } : null,
            t.busy ? { text: `${t.busy} busy` } : null,
            t.health.critical ? { text: `${t.health.critical} critical`, tone: 'danger' } : null,
            t.health.warning ? { text: `${ui.plural(t.health.warning, 'warning')}`, tone: 'attention' } : null,
            t.nodes && !t.health.critical && !t.health.warning && !t.health.pending ? { text: 'all healthy', tone: 'quiet' } : null,
        ];
        const loadTone = v => (v != null && v > (d.thresholds?.load_per_cpu_warn ?? 1.5) ? 'attention' : '');
        /* Secondary: the page's one primary is the header's New run. */
        const primary = group
            ? `<a class="w-btn" data-variant="secondary" href="#/new-run?groups=${encodeURIComponent(group.name)}">${icon('play')}<span>Run on this group</span></a>`
            : '';
        const runs = d.runs_24h;
        el.innerHTML = `
            <header class="orch-head"><h1 class="w-page__title">${esc(d.scope.label)}</h1></header>
            <div class="orch-dash" data-scope="${esc(scope)}">
                <div class="orch-dash__top">
                    <div class="orch-dash__title">
                        <h2 class="orch-dash__name">${esc(d.scope.label)}</h2>
                        <p class="w-state" data-dash-state>${ui.stateHtml(state)}</p>
                        ${group ? `<p class="orch-sub">${group.selector ? `Selector <code class="orch-mono">${esc(group.selector)}</code> · ` : 'Members only · '}${esc(ui.plural(group.members.length, 'member'))}${group.description ? ` · ${esc(group.description)}` : ''} · <a href="#/group/${encodeURIComponent(group.name)}">Manage group</a></p>` : ''}
                        ${scope === UNGROUPED ? '<p class="orch-sub">Nodes in no group. Drag one onto a group to add it.</p>' : ''}
                    </div>
                    ${primary ? `<div class="orch-dash__act">${primary}</div>` : ''}
                </div>
                ${t.nodes ? `<div class="w-board orch-dash__board">
                    <section class="w-board__cell" aria-labelledby="orch-dash-mem">
                        <header class="w-pane__head"><h3 class="w-pane__title" id="orch-dash-mem">Memory</h3></header>
                        <span class="w-board__big">${esc(pct(r.memory_used_avg))}<span class="orch-big__of"> average</span></span>
                        <p class="orch-board__line">${Orch.nodes.meter('fullest', r.memory_used_max) || '<span class="orch-muted">no figures yet</span>'}</p>
                    </section>
                    <section class="w-board__cell" aria-labelledby="orch-dash-disk">
                        <header class="w-pane__head"><h3 class="w-pane__title" id="orch-dash-disk">Disk</h3></header>
                        <span class="w-board__big">${esc(pct(r.disk_used_avg))}<span class="orch-big__of"> average</span></span>
                        <p class="orch-board__line">${Orch.nodes.meter('fullest', r.disk_used_max) || '<span class="orch-muted">no figures yet</span>'}</p>
                    </section>
                    <section class="w-board__cell" aria-labelledby="orch-dash-load">
                        <header class="w-pane__head"><h3 class="w-pane__title" id="orch-dash-load">Load per CPU</h3></header>
                        <span class="w-board__big"${loadTone(r.load_per_cpu_avg) ? ` data-tone="${loadTone(r.load_per_cpu_avg)}"` : ''}>${esc(r.load_per_cpu_avg ?? '—')}<span class="orch-big__of"> average</span></span>
                        <p class="orch-board__line"${loadTone(r.load_per_cpu_max) ? ` data-tone="${loadTone(r.load_per_cpu_max)}"` : ''}>busiest ${esc(r.load_per_cpu_max ?? '—')}</p>
                    </section>
                    <section class="w-board__cell" aria-labelledby="orch-dash-runs">
                        <header class="w-pane__head"><h3 class="w-pane__title" id="orch-dash-runs">Last 24 hours</h3></header>
                        <span class="w-board__big">${esc(runs.succeeded)}<span class="orch-big__of"> succeeded</span></span>
                        <p class="orch-board__line"${runs.failed ? ' data-tone="danger"' : ''}>${esc(ui.plural(runs.failed, 'failed execution'))} of ${esc(runs.executions)}</p>
                    </section>
                </div>` : ''}
                ${t.nodes ? `<section class="orch-section orch-hist" aria-labelledby="orch-dash-hist">
                    <header class="orch-section__head"><h3 class="orch-section__title" id="orch-dash-hist">History</h3>
                        <span class="orch-sub">${t.nodes > 1 ? 'CPU, memory and load averaged across these nodes; disk is the fullest; network and disk I/O are summed' : 'This node over time'}</span></header>
                    <div data-dash-history></div></section>` : ''}
                <section class="orch-section" aria-labelledby="orch-dash-health">
                    <header class="orch-section__head"><h3 class="orch-section__title" id="orch-dash-health">Health</h3>
                        <span class="orch-sub">${esc(healthWords(t.health).join(' · ') || (t.nodes ? 'all healthy' : ''))}</span>
                        ${d.nodes.length > 1 ? `<span class="orch-sort" role="group" aria-label="Sort nodes by">${H.SORTS.map(([k, label]) =>
                            `<button type="button" class="w-textbtn orch-sort__btn" data-health-sort="${k}" aria-pressed="${k === healthSort()}">${esc(label)}</button>`).join('')}</span>` : ''}</header>
                    ${ui.rows(d.nodes.map(n => nodeHealthRow(n, d.groups)), { label: `Nodes in ${d.scope.label}`,
                        empty: scope === ALL ? 'No nodes enrolled yet. Set one up from the page actions.' : scope === UNGROUPED ? 'Every node is in a group.' : 'No nodes in this group yet. Drag nodes onto it in the list, or manage its members.' })}
                </section>
                ${runs.recent.length ? `<section class="orch-section" aria-labelledby="orch-dash-recent">
                    <header class="orch-section__head"><h3 class="orch-section__title" id="orch-dash-recent">Recent runs here</h3></header>
                    ${ui.rows(runs.recent.map(x => ui.row({ status: ui.statusKey(x.style), title: x.title || x.id, href: `#/run/${encodeURIComponent(x.id)}`,
                        sub: esc(ui.cap(x.style?.label || x.state)), when: esc(x.created_ago || '') })), { label: 'Recent runs' })}
                </section>` : ''}
                ${scope === ALL ? '<details class="w-disclose orch-dash__fleet" data-fleet-setup><summary>Fleet setup: endpoint, agents, test nodes and join tokens</summary><div data-fleet></div></details>' : ''}
            </div>`;
        el.querySelectorAll('[data-group-node]').forEach(b => b.addEventListener('click', () => {
            const n = d.nodes.find(x => x.id === b.dataset.groupNode);
            if (n) groupMenu(n, d.groups);
        }));
        let fleetCtl = null;
        const fleet = el.querySelector('[data-fleet-setup]');
        if (fleet) {
            const box = fleet.querySelector('[data-fleet]');
            const load = async () => {
                fleetCtl?.dispose?.();
                fleetCtl = await Orch.nodes.renderFleet(box, route, { isCurrent });
                box.querySelector('.orch-head')?.remove();
            };
            if (sessionStorage.getItem('orch-fleet-setup') === 'open') { fleet.open = true; await load(); }
            fleet.addEventListener('toggle', () => {
                sessionStorage.setItem('orch-fleet-setup', fleet.open ? 'open' : '');
                if (fleet.open && !box.childElementCount) load();
            });
        }
        /* The Health rows: each node's sparklines from the history fetch (per_node), and the sort. */
        const healthList = el.querySelector('[aria-labelledby="orch-dash-health"] .w-rows');
        const order = d.nodes.map(n => n.id);
        let perNode = null;
        const applySort = () => {
            if (!healthList) return;
            const byId = new Map([...healthList.children].map(li => [li.dataset.nodeId, li]));
            H.sortNodes(order, perNode, perNode ? healthSort() : 'health').forEach(id => { const li = byId.get(id); if (li) healthList.appendChild(li); });
        };
        const fillSparks = data => {
            perNode = data.per_node || {};
            const scales = H.sparkScales(perNode);
            const outdated = new Set(data.needs_update || []);
            el.querySelectorAll('[data-node-spark]').forEach(slot => {
                const n = d.nodes.find(x => x.id === slot.dataset.nodeSpark);
                if (!n) return;
                const entry = perNode[n.id];
                if (!entry || !(entry.points || []).length) {
                    slot.innerHTML = n.status === 'pending' ? '' : `<span class="orch-sparks orch-sparks--none">${Orch.nodes.meter('mem', n.memory_used_pct) || ''}<span>${outdated.has(n.name) ? 'no history (agent &lt; 1.3.0)' : 'no samples yet'}</span></span>`;
                    return;
                }
                const sparks = H.nodeSparks(entry, scales, data);
                slot.innerHTML = Orch.history.sparksHtml(sparks, H.sparkSummary(n.name, sparks, data.range));
            });
            applySort();
        };
        el.querySelectorAll('[data-health-sort]').forEach(b => b.addEventListener('click', () => {
            localStorage.setItem(SORT_KEY, b.dataset.healthSort);
            el.querySelectorAll('[data-health-sort]').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
            applySort();
        }));
        /* The scope's history (history.js): the same charts, compact, combined across nodes. */
        const histBox = el.querySelector('[data-dash-history]');
        const history = histBox ? Orch.history?.mount(histBox, {
            url: `dashboard/metrics${q ? `${q}&per_node=1` : '?per_node=1'}`, compact: true, onData: fillSparks,
            notice: data => {
                const missing = (data.needs_update || []).length;
                if (!missing) return '';
                return `<p class="orch-sub orch-hist__note" data-tone="attention">◆ ${esc(ui.plural(missing, 'node'))} ${missing === 1 ? 'has' : 'have'} no history: ${missing === 1 ? 'its agent predates' : 'their agents predate'} 1.3.0 (${esc(data.needs_update.slice(0, 4).join(', '))}${missing > 4 ? '…' : ''}). <button type="button" class="w-textbtn" data-history-update>Update agents</button></p>`;
            },
            bind: box => box.querySelector('[data-history-update]')?.addEventListener('click', e => Orch.nodes.updateAgents(null, e.currentTarget)),
        }) : null;
        return { dispose: () => { fleetCtl?.dispose?.(); history?.dispose(); } };
    }

    /* ------------------------------------------------------------ Groups tab: LIST */
    async function renderGroupsList(el, route, { isCurrent, tools }) {
        const data = await api.get('groups');
        if (!isCurrent()) return null;
        const counts = g => ui.subline([ui.plural(g.nodes, 'node'), g.selector ? `<code class="orch-mono">${esc(g.selector)}</code>` : 'members only', g.description ? esc(g.description) : '']);
        el.innerHTML = `
            <header class="orch-head"><h1 class="w-page__title">Groups</h1></header>
            <div class="orch-listtools"><button type="button" class="w-btn" data-variant="secondary" data-size="sm" data-create-group>${icon('plus', 'sm')}<span>Create group</span></button></div>
            ${ui.rows(data.groups.map(g => ui.row({ title: g.name, href: `#/group/${encodeURIComponent(g.name)}`, sub: counts(g), when: healthSigns(g.health, g.nodes),
                attrs: ` data-drop-group="${esc(g.name)}"` })), { label: 'Groups', empty: '' })}
            ${data.groups.length ? '' : ui.empty('No groups yet', 'A group gathers nodes so you can watch their health together and run tools on just them. Add nodes by label or by dragging them in.', { icon: 'layers', action: '<button type="button" class="w-btn" data-variant="primary" data-create-group>Create group</button>' })}
            <p class="orch-muted orch-groups__foot">${esc(ui.plural(data.ungrouped.nodes, 'node'))} in no group.</p>`;
        if (tools) tools.innerHTML = '';
        el.querySelectorAll('[data-create-group]').forEach(b => b.addEventListener('click', async () => {
            const name = await ui.prompt({ title: 'Create a group', label: 'Name', hint: 'Letters, digits, “-”, “_” and “.”. You add nodes next: by label, or by picking them.', okLabel: 'Create group' });
            if (name === null) return;
            try { const made = await createGroup(name); Orch.go(`#/group/${encodeURIComponent(made)}`); }
            catch (err) { ui.toast(err.message, 'danger'); }
        }));
        return null;
    }

    /* ------------------------------------------------------------ Groups tab: one group */
    async function renderGroup(el, route, { isCurrent, setCrumbs }) {
        const name = route.parts[0] || '';
        const data = await api.get('groups');
        if (!isCurrent()) return null;
        const g = data.groups.find(x => x.name === name);
        if (!g) {
            el.innerHTML = ui.empty('That group doesn’t exist', 'It may have been renamed or deleted.', { icon: 'layers', action: '<a class="w-btn" data-variant="secondary" href="#/groups">All groups</a>' });
            return null;
        }
        setCrumbs([{ label: 'Groups', to: '#/groups' }, { label: g.name }]);
        const members = new Set(g.members || []);
        const viaSelector = new Set(data.nodes.filter(n => (n.groups || []).includes(g.name) && !members.has(n.id)).map(n => n.id));
        const id = ui.uid('group');
        el.innerHTML = `
            <header class="orch-head"><h1 class="w-page__title orch-item__title">${esc(g.name)}</h1></header>
            <div class="orch-group">
                <p class="w-state">${ui.stateHtml([{ text: ui.plural(g.nodes, 'node') }, { text: `${members.size} added by hand` },
                    g.selector ? { text: `${viaSelector.size} by selector` } : null, ...healthWords(g.health).map(w => ({ text: w, tone: /critical/.test(w) ? 'danger' : 'attention' }))])}</p>
                <div class="w-btn-row orch-group__acts">
                    <a class="w-btn" data-variant="secondary" href="#/new-run?groups=${encodeURIComponent(g.name)}">${icon('play')}<span>Run on this group</span></a>
                    <a class="w-btn" data-variant="secondary" href="${scopeHref(g.name)}">Open dashboard</a>
                </div>

                <section class="orch-section" aria-labelledby="${id}-about">
                    <header class="orch-section__head"><h2 class="orch-section__title" id="${id}-about">Name and description</h2></header>
                    <form class="orch-stack" data-about novalidate>
                        <div class="orch-form-grid">
                            <div class="w-field"><label class="w-field__label" for="${id}-name">Name</label><input class="w-input" data-mono id="${id}-name" value="${esc(g.name)}" autocomplete="off"></div>
                            <div class="w-field"><label class="w-field__label" for="${id}-desc">Description <span class="orch-muted">(optional)</span></label><input class="w-input" id="${id}-desc" value="${esc(g.description || '')}" autocomplete="off"></div>
                        </div>
                        <p class="w-field__error" data-error role="alert"></p>
                        <div class="w-btn-row"><button type="submit" class="w-btn" data-variant="secondary">Save</button></div>
                    </form>
                </section>

                <section class="orch-section" aria-labelledby="${id}-sel">
                    <header class="orch-section__head"><h2 class="orch-section__title" id="${id}-sel">By label</h2>
                        <span class="orch-sub">Nodes whose labels match join on their own, now and later. Leave it empty for a hand-picked group.</span></header>
                    <div data-selector></div>
                    <p class="w-field__error" data-sel-error role="alert"></p>
                    <div class="w-btn-row"><button type="button" class="w-btn" data-variant="secondary" data-save-selector>Save selector</button>${g.selector ? '<button type="button" class="w-textbtn" data-tone="quiet" data-clear-selector>Clear selector</button>' : ''}</div>
                </section>

                <section class="orch-section" aria-labelledby="${id}-mem">
                    <header class="orch-section__head"><h2 class="orch-section__title" id="${id}-mem">Members</h2>
                        <span class="orch-sub" data-mem-count></span></header>
                    <label class="w-visually-hidden" for="${id}-q">Search nodes</label>
                    <input class="w-input orch-search" id="${id}-q" type="search" placeholder="Search name, id or label" autocomplete="off">
                    <ul class="orch-members" data-members aria-label="Nodes"></ul>
                    <p class="orch-muted orch-members__note">Nodes the selector matches stay in the group; change their labels or the selector to take them out.</p>
                    <div class="w-btn-row"><button type="button" class="w-btn" data-variant="secondary" data-save-members disabled>Save members</button></div>
                </section>

                <section class="orch-section" aria-labelledby="${id}-del">
                    <header class="orch-section__head"><h2 class="orch-section__title" id="${id}-del">Delete</h2></header>
                    <p class="orch-muted">Runs and schedules that target ${esc(g.name)} will no longer match any node. The nodes themselves are unchanged.</p>
                    <div class="w-btn-row"><button type="button" class="w-btn" data-variant="danger" data-delete-group>Delete group</button></div>
                </section>
            </div>`;

        /* Name and description: a rename first (a new name), then the description. */
        const about = el.querySelector('[data-about]');
        about.addEventListener('submit', async e => {
            e.preventDefault();
            const newName = about.querySelector(`#${id}-name`).value.trim();
            const desc = about.querySelector(`#${id}-desc`).value.trim();
            try {
                await ui.busy(about.querySelector('[type=submit]'), async () => {
                    if (newName && newName !== g.name) await api.cmd('renameGroup', { name: g.name, new_name: newName });
                    const target = newName || g.name;
                    if (desc !== (g.description || '')) await api.cmd('putGroup', { name: target, description: desc });
                });
                ui.toast('Group saved', 'ok');
                if (newName && newName !== g.name) Orch.go(`#/group/${encodeURIComponent(newName)}`); else Orch.refresh();
            } catch (err) { about.querySelector('[data-error]').innerHTML = `<span aria-hidden="true">✗</span> ${esc(err.message)}`; }
        });

        /* The selector: the shared builder (labels only), with its live count. */
        let selector = g.selector || '';
        window.OrchPickers.selectorBuilder(el.querySelector('[data-selector]'), { value: selector, labelsOnly: true, label: 'Label selector', idPrefix: `${id}-sb`, emptyNote: 'No condition: only the members you add by hand.', onChange: v => { selector = typeof v === 'string' ? v : (v?.labels || ''); } });
        const saveSelector = async (value, button) => {
            try {
                await ui.busy(button, () => api.cmd('putGroup', { name: g.name, selector: value }));
                ui.toast(value ? 'Selector saved' : 'Selector cleared: members only', 'ok');
                Orch.refresh();
            } catch (err) { el.querySelector('[data-sel-error]').innerHTML = `<span aria-hidden="true">✗</span> ${esc(err.message)}`; }
        };
        el.querySelector('[data-save-selector]').addEventListener('click', e => saveSelector(selector.trim(), e.currentTarget));
        el.querySelector('[data-clear-selector]')?.addEventListener('click', e => saveSelector('', e.currentTarget));

        /* Members: a checklist of every node. */
        const chosen = new Set(members);
        const list = el.querySelector('[data-members]');
        const search = el.querySelector(`#${id}-q`);
        const save = el.querySelector('[data-save-members]');
        const same = () => chosen.size === members.size && [...chosen].every(x => members.has(x));
        const paintCount = () => {
            el.querySelector('[data-mem-count]').textContent = `${ui.plural(chosen.size, 'node')} added by hand${viaSelector.size ? `, ${viaSelector.size} by selector` : ''}`;
            save.disabled = same();
        };
        const paint = () => {
            const q = search.value.trim().toLowerCase();
            const nodes = data.nodes.filter(n => !q || [n.name, n.id, ui.formatLabels(n.labels)].join(' ').toLowerCase().includes(q));
            list.innerHTML = nodes.length ? nodes.map(n => {
                const auto = viaSelector.has(n.id);
                const on = auto || chosen.has(n.id);
                return `<li class="orch-members__row"><label class="w-check"><input class="w-check__input" type="checkbox" data-member="${esc(n.id)}"${on ? ' checked' : ''}${auto ? ' disabled' : ''}>
                    <span class="orch-members__name">${ui.sign(n.health?.style)} ${esc(n.name)} <span class="orch-sub orch-mono">${esc(ui.formatLabels(n.labels) || n.id)}</span>${auto ? ' <span class="orch-tag" title="Its labels match the selector">by selector</span>' : ''}</span></label></li>`;
            }).join('') : '<li class="orch-muted">No nodes match.</li>';
            list.querySelectorAll('[data-member]').forEach(c => c.addEventListener('change', () => {
                if (c.checked) chosen.add(c.dataset.member); else chosen.delete(c.dataset.member);
                paintCount();
            }));
            paintCount();
        };
        search.addEventListener('input', ui.debounce(paint, 120));
        paint();
        save.addEventListener('click', async e => {
            try {
                await ui.busy(e.currentTarget, () => api.cmd('putGroup', { name: g.name, members: [...chosen] }));
                ui.toast('Members saved', 'ok');
                Orch.refresh();
            } catch (err) { ui.toast(err.message, 'danger'); }
        });

        el.querySelector('[data-delete-group]').addEventListener('click', async e => {
            if (!await ui.confirm({ title: `Delete group ${g.name}?`, message: 'Runs and schedules that target this group will no longer match any node. The nodes are unchanged.', confirmLabel: 'Delete group', tone: 'danger' })) return;
            try { await ui.command('deleteGroup', { name: g.name }, { ok: `Group ${g.name} deleted`, button: e.currentTarget }); Orch.go('#/groups'); } catch (_) { /* toasted */ }
        });
        /* Live changes repaint the page, but never over edits not saved yet. */
        let dirty = false;
        el.addEventListener('input', e => { if (e.target !== search) dirty = true; });
        el.addEventListener('change', () => { dirty = true; });
        const refresh = ui.debounce(() => { if (!dirty) Orch.refresh(); }, 300);
        return { onChange: change => { if (change.scope === 'node') refresh(); } };
    }

    Orch.groups = { dropCommand, renderNodesList, renderDashboard, healthSigns, ALL, UNGROUPED };
    Orch.routes.nodes = { nav: 'nodes', title: 'Nodes', scopes: ['node', 'run'], render: renderDashboard, crumbs: () => [{ label: 'Nodes' }] };
    Orch.routes.groups = { nav: 'groups', title: 'Groups', scopes: ['node'], render: renderGroupsList, crumbs: () => [{ label: 'Groups' }] };
    Orch.routes.group = { nav: 'groups', title: 'Group', scopes: ['node'], render: renderGroup, crumbs: r => [{ label: 'Groups', to: '#/groups' }, { label: r.parts[0] || 'Group' }] };
})();
