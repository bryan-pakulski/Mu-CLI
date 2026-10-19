/* Orchestration — Nodes: inventory, node detail, enrolment, groups and
 * disposable test nodes (documentation/plans/orchestration.md §3.1, §6.2,
 * §6.7, §6.8, §8).
 */
(() => {
    'use strict';
    const api = window.OrchAPI;
    const ui = window.OrchUI;
    const { esc, icon } = ui;
    const Orch = (window.Orch = window.Orch || { routes: {} });
    Orch.routes = Orch.routes || {};

    const STATUSES = ['active', 'offline', 'pending', 'quarantined', 'revoked'];
    const statusKey = n => (n.status === 'active' && !n.online ? 'offline' : n.status);

    /* ------------------------------------------------------------ shared bits */
    function endpointCard(ep) {
        ep = ep || {};
        const running = !!ep.running;
        const st = running ? { semantic: 'ok', glyph: '✓', label: 'Listening', status: 'done' }
            : (ep.error ? { semantic: 'danger', glyph: '✗', label: 'Not running', status: 'failed' } : { semantic: 'neutral', glyph: '○', label: 'Not configured', status: 'pending' });
        return `<article class="w-card orch-card orch-endpoint" aria-labelledby="orch-ep-h">
            <header class="w-card__head"><h2 class="w-card__title" id="orch-ep-h">Node endpoint</h2>${ui.chip(st)}<span class="orch-spacer"></span><a class="w-btn" data-variant="ghost" data-size="sm" href="#/settings?section=network">Network settings <span class="w-btn__chevron" aria-hidden="true">›</span></a></header>
            <div class="w-card__body">
                ${ep.error ? `<p class="orch-error" role="alert"><span aria-hidden="true">✗</span> ${esc(ep.error)}</p>` : ''}
                ${ui.kv([
                    ['Address', ep.bind ? `${ep.bind}:${ep.port}` : 'Not set'],
                    ['Kind', ep.kind], ['URL', ep.url],
                    ['TLS fingerprint', ep.fingerprint ? `<code class="orch-mono orch-break">${esc(ep.fingerprint)}</code> <button type="button" class="w-btn" data-variant="icon" data-size="sm" data-orch-copy data-copy-value="${esc(ep.fingerprint)}" aria-label="Copy fingerprint" title="Copy fingerprint">${icon('copy', 'sm')}</button>` : '', true],
                ])}
                ${!ep.bind ? '<p class="orch-muted">Nodes reach the master on a private address you choose. Public and any-address binds are refused.</p>' : ''}
            </div></article>`;
    }

    function nodeName(n, { link = true } = {}) {
        const name = esc(n.name || n.id);
        const extras = `${n.canary ? ' <span class="w-chip orch-tag" data-variant="meta" title="Canary node: tests and first batches run here">canary</span>' : ''}${n.kind === 'container' ? ' <span class="w-chip orch-tag" data-variant="meta" title="Disposable test node">test node</span>' : ''}`;
        return `<span class="orch-node-name">${link ? `<a href="#/node/${encodeURIComponent(n.id)}">${name}</a>` : name}${extras}</span><span class="orch-sub orch-mono">${esc(n.id)}</span>`;
    }
    function sandboxCell(n) {
        if (n.sandbox === 'none') return '<span class="w-chip" data-tone="caution" title="Tools run without a sandbox on this node"><span class="w-chip__glyph" aria-hidden="true">◆</span><span>No sandbox</span></span>';
        return n.sandbox ? esc(n.sandbox) : '<span class="orch-muted">—</span>';
    }
    /* The node's agent version, flagged when it's older than the master's. */
    function agentCell(n) {
        const a = n.agent || {};
        if (!a.outdated) return esc(n.agent_version || '—');
        return `${esc(n.agent_version)} <span class="w-chip" data-tone="${a.updatable ? 'attention' : 'caution'}" data-size="sm" title="${esc(a.updatable ? `Can be updated to ${a.current}` : a.reason)}"><span class="w-chip__glyph" aria-hidden="true">◆</span><span>${esc(a.updatable ? 'update available' : 'outdated')}</span></span>`;
    }
    /* Propose an update job (an ordinary run: plan, approval per the access policy, canary first). */
    async function updateAgents(nodeIds, button) {
        try {
            const res = await ui.command('proposeAgentUpdate', nodeIds ? { nodes: nodeIds } : {}, { quiet: true, button });
            const run = res.data.run;
            ui.toast(run.state === 'proposed' ? 'Update job proposed. Review and approve it to start.' : 'Update job started', 'ok');
            Orch.go(`#/run/${encodeURIComponent(run.id)}`);
        } catch (_) { /* toasted */ }
    }
    function agentBanner(agents) {
        const a = agents || {};
        if (!a.outdated) return '';
        if (a.open_run) return ui.banner('info', '●', `An agent update job is open (agent ${esc(a.current)}).`, `<a class="w-btn" data-variant="tonal" data-tone="info" data-size="sm" href="#/run/${encodeURIComponent(a.open_run)}">Open the update job</a>`);
        const n = a.updatable;
        const rest = a.outdated - n;
        return ui.banner('attention', '◆', `<strong>${esc(ui.plural(a.outdated, 'node runs', 'nodes run'))} an older agent</strong> than this master (${esc(a.current)}).${n ? ` ${esc(ui.plural(n, 'node'))} can update ${n === 1 ? 'itself' : 'themselves'}.` : ''}${rest ? ` ${esc(rest)} need a manual update or <code>allow_agent_updates: true</code> in their policy.yaml (see each node).` : ''}${a.auto ? ' Automatic updates are on.' : ''}`,
            n ? '<button type="button" class="w-btn" data-variant="tonal" data-tone="attention" data-size="sm" data-update-agents>Update agents</button>' : '');
    }

    function policyCell(n) {
        const errs = n.policy_errors || [];
        if (!errs.length) return '<span class="orch-muted">—</span>';
        return `<span class="w-chip" data-tone="danger" title="${esc(errs.join('\n'))}"><span class="w-chip__glyph" aria-hidden="true">✗</span><span>${esc(ui.plural(errs.length, 'policy error'))}</span></span>`;
    }

    /* ------------------------------------------------------------ enrol */
    function enrol() {
        const form = document.createElement('form');
        form.className = 'orch-stack';
        form.noValidate = true;
        const id = ui.uid('enrol');
        form.innerHTML = `
            <p class="orch-muted">A join token lets one machine enrol. It expires after an hour and can be used once. The node appears as <em>awaiting acceptance</em> until you accept it.</p>
            <div class="w-field"><label class="w-field__label" for="${id}-labels">Labels</label>
                <input class="w-input" data-mono id="${id}-labels" placeholder="env=prod, role=web" autocomplete="off" data-autofocus>
                <p class="w-field__hint">Comma-separated key=value pairs. Runs target nodes by label.</p></div>
            <div class="w-field"><label class="w-field__label" for="${id}-name">Node name <span class="orch-muted">(optional)</span></label>
                <input class="w-input" id="${id}-name" placeholder="web-01" autocomplete="off"></div>
            <div class="w-field"><label class="w-field__label" for="${id}-note">Note <span class="orch-muted">(optional)</span></label>
                <input class="w-input" id="${id}-note" placeholder="Rack 3, replaces web-old" autocomplete="off"></div>
            <label class="w-check"><input class="w-check__input" type="checkbox" id="${id}-canary"><span>Canary node (tests and first batches run here)</span></label>
            <p class="w-field__error" data-error role="alert"></p>
            <div class="w-btn-row orch-dialog__actions"><button type="button" class="w-btn" data-variant="secondary" data-cancel>Cancel</button><button type="submit" class="w-btn" data-variant="primary">Create join token</button></div>`;
        const d = ui.dialog({ title: 'Enrol a node', body: form, size: 'md' });
        form.querySelector('[data-cancel]').onclick = () => d.close();
        form.onsubmit = async e => {
            e.preventDefault();
            const labels = ui.parseLabels(form.querySelector(`#${id}-labels`).value);
            const args = { labels, canary: form.querySelector(`#${id}-canary`).checked, name: form.querySelector(`#${id}-name`).value.trim(), note: form.querySelector(`#${id}-note`).value.trim() };
            try {
                const res = await ui.busy(form.querySelector('[type=submit]'), () => api.cmd('createJoinToken', args));
                showToken(d, res.data || {});
                Orch.refresh?.();
            } catch (err) {
                form.querySelector('[data-error]').innerHTML = `<span aria-hidden="true">✗</span> ${esc(err.message)}`;
            }
        };
    }
    function showToken(d, t) {
        d.el.querySelector('.w-modal__title').textContent = 'Join token created';
        d.body.innerHTML = `<div class="orch-stack">
            ${t.command ? `<p>Run this on the machine you want to enrol. The installer’s digest is checked before it runs, and the node pins this master’s TLS fingerprint.</p>${ui.code(t.command, { lang: 'bash', title: 'Install command', cls: 'orch-code--wrap' })}`
                : ui.banner('caution', '◆', 'The node endpoint isn’t configured, so there is no install command yet. Choose a private interface in Settings › Network, then create a new token.', '<a class="w-btn" data-variant="tonal" data-tone="caution" data-size="sm" href="#/settings?section=network" data-close-dialog>Open network settings</a>')}
            ${ui.kv([
                ['Token', `<code class="orch-mono orch-break">${esc(t.token || '')}</code> <button type="button" class="w-btn" data-variant="icon" data-size="sm" data-orch-copy data-copy-value="${esc(t.token || '')}" aria-label="Copy token" title="Copy token">${icon('copy', 'sm')}</button>`, true],
                ['Master', t.master_url || 'not configured'],
                ['TLS fingerprint', `<code class="orch-mono orch-break">${esc(t.fingerprint || '')}</code>`, true],
                ['Labels', ui.labelChips(t.labels), true],
                ['Expires', t.expires_at ? ui.when(t.expires_at) : ''],
            ])}
            <p class="orch-muted">The token is shown once. Create another if you lose it.</p>
            <div class="w-btn-row orch-dialog__actions"><button type="button" class="w-btn" data-variant="primary" data-done>Done</button></div></div>`;
        d.body.querySelector('[data-done]').onclick = () => d.close();
        d.body.querySelector('[data-close-dialog]')?.addEventListener('click', () => d.close());
        d.body.querySelector('[data-done]').focus();
    }

    /* ------------------------------------------------------------ labels dialog */
    function labelsDialog(node, done) {
        return new Promise(resolve => {
            const form = document.createElement('form');
            form.className = 'orch-stack';
            const id = ui.uid('labels');
            form.innerHTML = `<div class="w-field"><label class="w-field__label" for="${id}">Labels for ${esc(node.name)}</label>
                <textarea class="w-textarea" data-mono id="${id}" rows="6" data-autofocus spellcheck="false">${esc(Object.entries(node.labels || {}).map(([k, v]) => `${k}=${v}`).join('\n'))}</textarea>
                <p class="w-field__hint">One key=value per line. Access rules and run selectors match these labels.</p><p class="w-field__error" data-error role="alert"></p></div>
                <div class="w-btn-row orch-dialog__actions"><button type="button" class="w-btn" data-variant="secondary" data-cancel>Cancel</button><button type="submit" class="w-btn" data-variant="primary">Save labels</button></div>`;
            const d = ui.dialog({ title: 'Edit labels', body: form, onClose: () => resolve(null) });
            form.querySelector('[data-cancel]').onclick = () => d.close();
            form.onsubmit = async e => {
                e.preventDefault();
                try {
                    const res = await ui.busy(form.querySelector('[type=submit]'), () => api.cmd('setNodeLabels', { node_id: node.id, labels: ui.parseLabels(form.querySelector(`#${id}`).value) }));
                    ui.toast('Labels saved', 'ok');
                    d.close();
                    done?.(res);
                } catch (err) { form.querySelector('[data-error]').innerHTML = `<span aria-hidden="true">✗</span> ${esc(err.message)}`; }
            };
        });
    }

    /* ------------------------------------------------------------ groups */
    function groupDialog(group, done) {
        const form = document.createElement('form');
        form.className = 'orch-stack';
        const id = ui.uid('group');
        form.innerHTML = `
            <div class="w-field"><label class="w-field__label" for="${id}-name">Name</label><input class="w-input" data-mono id="${id}-name" value="${esc(group?.name || '')}" ${group ? 'readonly' : 'data-autofocus'} autocomplete="off" placeholder="web-prod"></div>
            <div class="w-field"><label class="w-field__label" for="${id}-sel">Selector</label><input class="w-input" data-mono id="${id}-sel" value="${esc(group?.selector || '')}" ${group ? 'data-autofocus' : ''} autocomplete="off" placeholder="env=prod,role in (web,api),!legacy">
                <p class="w-field__hint">Comma means AND. Use <code>k=v</code>, <code>k!=v</code>, <code>k in (a,b)</code>, <code>k</code> (present) or <code>!k</code> (absent).</p></div>
            <div class="w-field"><label class="w-field__label" for="${id}-desc">Description <span class="orch-muted">(optional)</span></label><input class="w-input" id="${id}-desc" value="${esc(group?.description || '')}" autocomplete="off"></div>
            <p class="w-field__error" data-error role="alert"></p>
            <div class="w-btn-row orch-dialog__actions"><button type="button" class="w-btn" data-variant="secondary" data-cancel>Cancel</button><button type="submit" class="w-btn" data-variant="primary">${group ? 'Save group' : 'Add group'}</button></div>`;
        const d = ui.dialog({ title: group ? `Edit group ${group.name}` : 'New group', body: form });
        form.querySelector('[data-cancel]').onclick = () => d.close();
        form.onsubmit = async e => {
            e.preventDefault();
            const args = { name: form.querySelector(`#${id}-name`).value.trim(), selector: form.querySelector(`#${id}-sel`).value.trim(), description: form.querySelector(`#${id}-desc`).value.trim() };
            try {
                await ui.busy(form.querySelector('[type=submit]'), () => api.cmd('putGroup', args));
                ui.toast('Group saved', 'ok');
                d.close();
                done?.();
            } catch (err) { form.querySelector('[data-error]').innerHTML = `<span aria-hidden="true">✗</span> ${esc(err.message)}`; }
        };
    }

    async function startTestNode(button, done) {
        const image = await ui.prompt({ title: 'Start a disposable test node', label: 'Container image', optional: true, value: '', okLabel: 'Start test node', hint: 'Leave blank for the default image. The node runs in a local container without a sandbox, is marked canary and is revoked when you stop it.' });
        if (image === null) return;
        try {
            await ui.command('startTestNode', { image: image.trim() }, { ok: 'Test node starting', button });
            done?.();
        } catch (_) { /* toasted */ }
    }

    /* ------------------------------------------------------------ list */
    async function renderList(el, route, { isCurrent, setQuery, soft }) {
        const data = await api.get('nodes');
        if (!isCurrent()) return null;
        const filter = route.query.get('status') || '';
        const prev = soft ? el.querySelector('#orch-node-search')?.value : null;
        const search = prev ?? (route.query.get('q') || '');
        const counts = {};
        data.nodes.forEach(n => { const k = statusKey(n); counts[k] = (counts[k] || 0) + 1; });
        const testStatuses = Object.values(data.test_nodes || {});

        el.innerHTML = `
            <header class="w-page__head orch-head">
                <div><h1 class="w-page__title">Nodes</h1><p class="w-page__lede">Machines enrolled with this master, their facts and their own local policy.</p></div>
                <span class="orch-spacer"></span>
                <div class="w-btn-row"><button type="button" class="w-btn" data-variant="secondary" data-test-node>${icon('box')}<span>Start test node</span></button>
                <button type="button" class="w-btn" data-variant="secondary" data-enrol>${icon('plus')}<span>Quick enrol</span></button>
                <a class="w-btn" data-variant="primary" href="#/setup">${icon('server')}<span>Set up a node</span></a></div>
            </header>
            <div class="orch-stack">
                ${agentBanner(data.agents)}
                ${endpointCard(data.endpoint)}
                ${testStatuses.length ? `<section class="orch-section" aria-labelledby="orch-tn-h"><header class="orch-section__head"><h2 class="orch-section__title" id="orch-tn-h">Test nodes</h2></header>
                    ${ui.table({ label: 'Test nodes', cols: [{ label: 'Container' }, { label: 'Image' }, { label: 'State' }, { label: 'Node' }, { label: 'Actions', sr: true, cls: 'w-table__actions' }],
                        rows: testStatuses.map(t => [`<span class="orch-mono">${esc(t.container)}</span>`, `<span class="orch-mono">${esc(t.image)}</span>`,
                            ui.chip(t.state === 'ready' ? { semantic: 'ok', glyph: '✓', label: 'Ready' } : t.state === 'failed' ? { semantic: 'danger', glyph: '✗', label: 'Failed' } : { semantic: 'running', glyph: '●', label: 'Starting' }, { title: t.error || '' }) + (t.error ? `<span class="orch-sub orch-error">${esc(t.error)}</span>` : ''),
                            t.node_id ? `<a href="#/node/${encodeURIComponent(t.node_id)}" class="orch-mono">${esc(t.node_id)}</a>` : '—',
                            t.node_id && (data.test_node_ids || []).includes(t.node_id) ? `<button type="button" class="w-btn" data-variant="ghost" data-size="sm" data-stop-test="${esc(t.node_id)}">Stop</button>` : ''] ) })}</section>` : ''}
                <section class="orch-section" aria-labelledby="orch-inv-h">
                    <header class="orch-section__head"><h2 class="orch-section__title" id="orch-inv-h">Inventory</h2><span class="w-count">${data.nodes.length}</span></header>
                    <div class="orch-filters">
                        <div class="w-chip-row" role="group" aria-label="Filter by status">
                            <button type="button" class="w-chip" data-variant="filter" aria-pressed="${!filter}" data-filter="">All <span class="orch-count">${data.nodes.length}</span></button>
                            ${STATUSES.filter(s => counts[s]).map(s => `<button type="button" class="w-chip" data-variant="filter" aria-pressed="${filter === s}" data-filter="${esc(s)}">${esc(ui.cap(ui.vstyle('orch_node_status', s).label))} <span class="orch-count">${counts[s]}</span></button>`).join('')}
                        </div>
                        <label class="w-visually-hidden" for="orch-node-search">Search nodes</label>
                        <input class="w-input orch-search" id="orch-node-search" type="search" placeholder="Search name, id or label" value="${esc(search)}" autocomplete="off">
                    </div>
                    <div data-node-table></div>
                </section>
                <section class="orch-section" aria-labelledby="orch-groups-h">
                    <header class="orch-section__head"><div><h2 class="orch-section__title" id="orch-groups-h">Groups</h2><p class="orch-muted">Named label selectors you can target runs and access rules at.</p></div><span class="orch-spacer"></span><button type="button" class="w-btn" data-variant="secondary" data-size="sm" data-add-group>${icon('plus')}<span>New group</span></button></header>
                    ${ui.table({ label: 'Groups', empty: 'No groups yet.', cols: [{ label: 'Name' }, { label: 'Selector' }, { label: 'Members', num: true }, { label: 'Description' }, { label: 'Actions', sr: true, cls: 'w-table__actions' }],
                        rows: data.groups.map((g, i) => [`<span class="orch-mono">${esc(g.name)}</span>`, `<code class="orch-mono">${esc(g.selector)}</code>`,
                            String(data.nodes.filter(n => (n.groups || []).includes(g.name)).length), esc(g.description || ''),
                            `<button type="button" class="w-btn" data-variant="ghost" data-size="sm" data-edit-group="${i}">Edit</button><button type="button" class="w-btn" data-variant="ghost" data-size="sm" data-del-group="${i}">Delete</button>`]) })}
                </section>
                <section class="orch-section" aria-labelledby="orch-tokens-h">
                    <header class="orch-section__head"><h2 class="orch-section__title" id="orch-tokens-h">Join tokens</h2></header>
                    ${ui.table({ label: 'Join tokens', empty: 'No join tokens yet.', cols: [{ label: 'Created' }, { label: 'Labels' }, { label: 'Canary' }, { label: 'Status' }, { label: 'Node' }, { label: 'Note' }],
                        rows: (data.tokens || []).slice(0, 15).map(t => {
                            const expired = !t.used_at && t.expires_at && t.expires_at * 1000 < Date.now();
                            const st = t.used_at ? { semantic: 'ok', glyph: '✓', label: 'Used' } : expired ? { semantic: 'neutral', glyph: '–', label: 'Expired' } : { semantic: 'info', glyph: '●', label: `Open until ${ui.when(t.expires_at)}` };
                            return [ui.time(t.created_at), ui.labelChips(t.labels), t.canary ? 'yes' : 'no', ui.chip(st), t.node_id ? `<a href="#/node/${encodeURIComponent(t.node_id)}" class="orch-mono">${esc(t.node_id)}</a>` : '—', esc(t.note || '')];
                        }) })}
                </section>
            </div>`;

        const paintTable = () => {
            const q = (el.querySelector('#orch-node-search').value || '').toLowerCase();
            const f = Orch.parse().query.get('status') || '';
            const rows = data.nodes.filter(n => (!f || statusKey(n) === f) && (!q || [n.name, n.id, n.address, ui.formatLabels(n.labels)].join(' ').toLowerCase().includes(q)));
            el.querySelector('[data-node-table]').innerHTML = ui.table({
                label: 'Nodes', empty: data.nodes.length ? 'No nodes match this filter.' : 'No nodes enrolled yet. Create a join token to add one.',
                cols: [{ label: 'Name' }, { label: 'Status' }, { label: 'Labels' }, { label: 'Last seen' }, { label: 'Agent' }, { label: 'OS / kernel' }, { label: 'Sandbox' }, { label: 'Policy' }, { label: 'Actions', sr: true, cls: 'orch-actions-col' }],
                rows: rows.map(n => {
                    const accept = (n.actions || []).find(a => a.id === 'acceptNode' && a.enabled);
                    return [nodeName(n), ui.chip(n.style), ui.labelChips(n.labels), ui.time(n.last_seen), agentCell(n),
                        `${esc(n.summary?.os || '—')}<span class="orch-sub">${esc([n.summary?.kernel, n.summary?.arch].filter(Boolean).join(' · '))}</span>`,
                        sandboxCell(n), policyCell(n),
                        accept ? `<button type="button" class="w-btn" data-variant="tonal" data-tone="ok" data-size="sm" data-accept="${esc(n.id)}">${icon('check', 'sm')}<span>Accept</span></button>` : `<a class="w-btn" data-variant="ghost" data-size="sm" href="#/node/${encodeURIComponent(n.id)}">Open</a>`];
                }),
            });
            el.querySelectorAll('[data-accept]').forEach(b => b.addEventListener('click', async () => {
                const n = data.nodes.find(x => x.id === b.dataset.accept);
                const ok = await ui.confirm({ title: `Accept ${n.name}?`, message: `It will receive work that targets its labels (${ui.formatLabels(n.labels) || 'no labels'}). Check the name and labels match the machine you enrolled.`, confirmLabel: 'Accept node' });
                if (!ok) return;
                try { await ui.command('acceptNode', { node_id: n.id }, { ok: `${n.name} accepted`, button: b }); Orch.refresh(); } catch (_) { /* toasted */ }
            }));
        };
        paintTable();
        el.querySelector('#orch-node-search').addEventListener('input', ui.debounce(() => { setQuery({ q: el.querySelector('#orch-node-search').value }); paintTable(); }, 150));
        el.querySelectorAll('[data-filter]').forEach(b => b.addEventListener('click', () => {
            setQuery({ status: b.dataset.filter });
            el.querySelectorAll('[data-filter]').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
            paintTable();
        }));
        el.querySelector('[data-update-agents]')?.addEventListener('click', e => updateAgents(null, e.currentTarget));
        el.querySelector('[data-enrol]').addEventListener('click', enrol);
        el.querySelector('[data-test-node]').addEventListener('click', e => startTestNode(e.currentTarget, () => Orch.refresh()));
        el.querySelector('[data-add-group]').addEventListener('click', () => groupDialog(null, () => Orch.refresh()));
        el.querySelectorAll('[data-edit-group]').forEach(b => b.addEventListener('click', () => groupDialog(data.groups[Number(b.dataset.editGroup)], () => Orch.refresh())));
        el.querySelectorAll('[data-del-group]').forEach(b => b.addEventListener('click', async () => {
            const g = data.groups[Number(b.dataset.delGroup)];
            if (!await ui.confirm({ title: `Delete group ${g.name}?`, message: 'Runs and schedules that target this group will no longer match any node.', confirmLabel: 'Delete group', tone: 'danger' })) return;
            try { await ui.command('deleteGroup', { name: g.name }, { ok: 'Group deleted' }); Orch.refresh(); } catch (_) { /* toasted */ }
        }));
        el.querySelectorAll('[data-stop-test]').forEach(b => b.addEventListener('click', async () => {
            if (!await ui.confirm({ title: 'Stop this test node?', message: 'The container is removed and the node is revoked.', confirmLabel: 'Stop test node', tone: 'danger' })) return;
            try { await ui.command('stopTestNode', { node_id: b.dataset.stopTest }, { ok: 'Test node stopped', button: b }); Orch.refresh(); } catch (_) { /* toasted */ }
        }));
        // Test nodes report progress outside the change feed: poll while one is starting.
        const starting = testStatuses.some(t => t.state === 'starting');
        const timer = starting ? setTimeout(() => { if (isCurrent()) Orch.refresh(); }, 3000) : 0;
        return { dispose: () => clearTimeout(timer) };
    }

    /* ------------------------------------------------------------ detail */
    const POLICY_ROWS = [
        ['max_effect', 'Highest effect'], ['max_privilege', 'Highest privilege'], ['allowed_run_as', 'Run as'],
        ['allowed_runtimes', 'Runtimes'], ['allowed_tools', 'Allowed tools'], ['denied_tools', 'Denied tools'],
        ['accept_categories', 'Categories'], ['quiet_hours', 'Quiet hours'], ['require_check_mode_first', 'Check mode first'],
        ['deny_all', 'Deny everything'], ['sandbox', 'Sandbox'],
    ];
    const listText = v => Array.isArray(v) ? (v.length ? v.join(', ') : 'none') : (typeof v === 'boolean' ? (v ? 'yes' : 'no') : String(v ?? ''));

    async function renderDetail(el, route, { isCurrent, setCrumbs }) {
        const id = route.parts[0];
        const n = await api.get(`nodes/${encodeURIComponent(id)}`);
        if (!isCurrent()) return null;
        setCrumbs([{ label: 'Nodes', to: '#/nodes' }, { label: n.name || n.id }]);
        const s = n.summary || {};
        const policy = n.policy || {};
        const execs = n.recent_executions || [];
        el.innerHTML = `
            <header class="w-page__head orch-head">
                <div class="orch-head__title">
                    <span class="w-eyebrow" data-tone="accent"><i class="w-eyebrow__swatch"></i>Node</span>
                    <h1 class="w-page__title">${esc(n.name || n.id)}</h1>
                    <div class="orch-head__meta">${ui.chip(n.style)}${n.canary ? ui.meta('canary', 'Tests and first batches run here') : ''}${n.kind ? ui.meta(n.kind) : ''}${ui.labelChips(n.labels, { empty: 'no labels' })}</div>
                </div>
            </header>
            <div class="orch-stack">
                <div class="orch-toolbar">${ui.actionBar(n.actions, { label: 'Node actions' })}${n.kind === 'container' && n.status !== 'revoked' ? '<button type="button" class="w-btn" data-variant="secondary" data-stop-test>' + icon('box') + '<span>Stop test node</span></button>' : ''}</div>
                ${n.agent?.outdated ? ui.banner(n.agent.updatable ? 'attention' : 'caution', '◆', `<strong>Agent ${esc(n.agent_version)} is older than this master’s ${esc(n.agent.current)}.</strong> ${n.agent.updatable ? 'It can update itself.' : esc(n.agent.reason)}`, n.agent.updatable ? '<button type="button" class="w-btn" data-variant="tonal" data-tone="attention" data-size="sm" data-update-agent>Update this node</button>' : '') : ''}
                ${n.sandbox === 'none' ? ui.banner('caution', '◆', '<strong>No sandbox.</strong> This node runs tools directly on the host (its local policy sets <code>sandbox: none</code>). Keep it to test or low-risk machines.') : ''}
                ${(n.policy_errors || []).length ? ui.banner('danger', '✗', `<strong>The node’s local policy has errors</strong>, so it refuses work until they’re fixed: ${n.policy_errors.map(esc).join('; ')}`) : ''}
                <div class="orch-grid-2">
                    <article class="w-card orch-card" aria-labelledby="orch-facts-h"><header class="w-card__head"><h2 class="w-card__title" id="orch-facts-h">Facts</h2><span class="w-card__meta">${n.facts_at ? `collected ${esc(ui.ago(n.facts_at))}` : 'not collected yet'}</span></header>
                        <div class="w-card__body">${ui.kv([
                            ['Hostname', s.hostname], ['OS', s.os], ['Kernel', s.kernel], ['Architecture', s.arch], ['CPUs', s.cpus],
                            ['Memory', s.memory_gb != null ? `${s.memory_gb} GB${s.memory_used_pct != null ? ` · ${s.memory_used_pct}% used` : ''}` : ''],
                            ['Fullest disk', s.max_disk_used_pct != null ? `${s.max_disk_used_pct}% used` : ''], ['Uptime', s.uptime_days != null ? `${s.uptime_days} days` : ''],
                            ['Load (1 min)', s.load], ['systemd', s.systemd == null ? '' : (s.systemd ? 'yes' : 'no')], ['Services', s.services],
                        ])}
                        ${(s.provides || []).length ? `<details class="orch-details"><summary>Provides ${esc(s.provides.length)} commands and services</summary><p class="orch-labels">${s.provides.map(p => `<span class="w-chip" data-variant="meta">${esc(p)}</span>`).join('')}</p></details>` : ''}</div></article>
                    <article class="w-card orch-card" aria-labelledby="orch-pol-h"><header class="w-card__head"><h2 class="w-card__title" id="orch-pol-h">Node-local policy</h2><span class="w-card__meta">what this node will refuse</span></header>
                        <div class="w-card__body">${Object.keys(policy).length ? ui.kv(POLICY_ROWS.filter(([k]) => k in policy).map(([k, label]) => [label, listText(policy[k])])) : '<p class="orch-muted">The node hasn’t reported a policy yet.</p>'}</div></article>
                </div>
                <div class="orch-grid-2">
                    <article class="w-card orch-card" aria-labelledby="orch-id-h"><header class="w-card__head"><h2 class="w-card__title" id="orch-id-h">Identity</h2></header>
                        <div class="w-card__body">${ui.kv([['Node id', `<code class="orch-mono">${esc(n.id)}</code>`, true], ['Address', n.address], ['Agent version', n.agent_version],
                            ['Last seen', `${ui.time(n.last_seen)}${n.online ? ' · online' : ''}`, true], ['Enrolled', ui.when(n.created_at)]])}</div></article>
                    <article class="w-card orch-card" aria-labelledby="orch-grp-h"><header class="w-card__head"><h2 class="w-card__title" id="orch-grp-h">Groups</h2></header>
                        <div class="w-card__body">${(n.groups || []).length ? `<p class="orch-labels">${n.groups.map(g => `<span class="w-chip" data-variant="meta">${esc(g)}</span>`).join('')}</p>` : '<p class="orch-muted">Not in any group. Groups are label selectors; edit them on the Nodes page.</p>'}</div></article>
                </div>
                <section class="orch-section" aria-labelledby="orch-ex-h">
                    <header class="orch-section__head"><h2 class="orch-section__title" id="orch-ex-h">Recent executions</h2></header>
                    ${ui.table({ label: 'Recent executions', empty: 'Nothing has run here yet.', cols: [{ label: 'When' }, { label: 'Tool' }, { label: 'Step' }, { label: 'State' }, { label: 'Exit', num: true }, { label: 'Duration', num: true }, { label: 'Run' }],
                        rows: execs.map(x => [ui.time(x.created_at), `<span class="orch-mono">${esc(x.tool)}</span>${x.check_mode ? ' ' + ui.meta('check mode') : ''}${x.rollback_of ? ' ' + ui.meta('rollback') : ''}`, esc(x.step_name || x.step),
                            ui.chip(x.style, { title: x.reason || '' }), x.exit_code == null ? '—' : esc(x.exit_code), esc(ui.dur(x.duration_s)), `<a href="#/run/${encodeURIComponent(x.run_id)}" class="orch-mono">${esc(x.run_id)}</a>`]) })}
                </section>
                <details class="orch-details orch-card-like"><summary>Raw facts</summary>${ui.code(ui.json(n.facts || {}), { lang: 'json', title: 'facts.json' })}</details>
            </div>`;
        const done = () => Orch.refresh();
        ui.bindActions(el, n.actions, { args: { node_id: n.id }, subject: n.name, done, dialogs: { labels: () => labelsDialog(n, done) } });
        el.querySelector('[data-update-agent]')?.addEventListener('click', e => updateAgents([n.id], e.currentTarget));
        el.querySelector('[data-stop-test]')?.addEventListener('click', async e => {
            if (!await ui.confirm({ title: 'Stop this test node?', message: 'The container is removed and the node is revoked.', confirmLabel: 'Stop test node', tone: 'danger' })) return;
            try { await ui.command('stopTestNode', { node_id: n.id }, { ok: 'Test node stopped', button: e.currentTarget }); done(); } catch (_) { /* toasted */ }
        });
        return null;
    }

    Orch.nodes = { enrol, endpointCard, nodeName, labelsDialog, updateAgents };
    Orch.routes.nodes = { nav: 'nodes', title: 'Nodes', scopes: ['node'], render: renderList, crumbs: () => [{ label: 'Nodes' }] };
    Orch.routes.node = { nav: 'nodes', title: 'Node', scopes: ['node', 'run'], render: renderDetail, crumbs: r => [{ label: 'Nodes', to: '#/nodes' }, { label: r.parts[0] || 'Node' }] };
})();
