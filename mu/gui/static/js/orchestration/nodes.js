/* Orchestration — Nodes: inventory, node detail, enrolment and
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

    const STATUSES = ['active', 'connecting', 'offline', 'pending', 'quarantined', 'revoked'];
    /* Presence (vm.node_presence): just after the master starts, a node it hasn't heard from yet is
     * "connecting" (pending), not offline. Older payloads without presence fall back to online. */
    const presence = n => n.presence || (n.online ? 'online' : 'offline');
    const statusKey = n => (n.status === 'active' && presence(n) !== 'online' ? presence(n) : n.status);

    /* ------------------------------------------------------------ shared bits */
    function endpointCard(ep) {
        ep = ep || {};
        const running = !!ep.running;
        const st = running ? { semantic: 'ok', glyph: '✓', label: 'Listening', status: 'done' }
            : (ep.error ? { semantic: 'danger', glyph: '✗', label: 'Not running', status: 'failed' } : { semantic: 'neutral', glyph: '○', label: 'Not configured', status: 'pending' });
        return `<section class="w-group orch-endpoint" aria-labelledby="orch-ep-h">
            <div class="w-group__head"><h2 class="w-group__title" id="orch-ep-h">Node endpoint</h2>${ui.chip(st)}<a class="w-group__more" href="#/settings?section=network">Network settings</a></div>
                ${ep.error ? `<p class="orch-error" role="alert"><span aria-hidden="true">✗</span> ${esc(ep.error)}</p>` : ''}
                ${ui.kv([
                    ['Address', ep.bind ? `${ep.bind}:${ep.port}` : 'Not set'],
                    ['Kind', ep.kind], ['URL', ep.url],
                    ['TLS fingerprint', ep.fingerprint ? `<code class="orch-mono orch-break">${esc(ep.fingerprint)}</code> <button type="button" class="w-btn" data-variant="icon" data-size="sm" data-orch-copy data-copy-value="${esc(ep.fingerprint)}" aria-label="Copy fingerprint" title="Copy fingerprint">${icon('copy', 'sm')}</button>` : '', true],
                ])}
                ${!ep.bind ? '<p class="orch-muted">Nodes reach the master on a private address you choose. Public and any-address binds are refused.</p>' : ''}
            </section>`;
    }

    /* The endpoint in one line above the inventory (Mu §4.2): where nodes connect and whether
     * it is listening; URL and fingerprint fold into a disclosure. */
    function endpointLine(ep, { link = true } = {}) {
        ep = ep || {};
        const running = !!ep.running;
        const st = running ? 'done' : (ep.error ? 'failed' : 'waiting');
        const head = running ? `Listening on ${ep.bind}:${ep.port}` : (ep.error ? 'Node endpoint not running' : 'Node endpoint not configured');
        return `<div class="orch-endpoint-line" aria-label="Node endpoint">
            <p class="w-state"><span class="w-state__part"${running ? '' : ` data-tone="${ep.error ? 'danger' : 'attention'}"`}><span class="w-sign" data-status="${st}" aria-hidden="true">${running ? '✓' : (ep.error ? '✗' : '◆')}</span> ${esc(head)}</span>${ep.kind ? `<span class="w-state__part">${esc(ep.kind)}</span>` : ''}${link ? '<a class="w-state__part" href="#/settings?section=network">Network settings</a>' : ''}</p>
            ${ep.error ? `<p class="orch-error" role="alert"><span aria-hidden="true">✗</span> ${esc(ep.error)}</p>` : ''}
            ${!ep.bind ? '<p class="orch-muted">Nodes reach the master on a private address you choose. Public and any-address binds are refused.</p>' : ''}
            ${ep.url || ep.fingerprint ? `<details class="w-disclose"><summary>Endpoint details</summary>${ui.kv([
                ['URL', ep.url],
                ['TLS fingerprint', ep.fingerprint ? `<code class="orch-mono orch-break">${esc(ep.fingerprint)}</code> <button type="button" class="w-btn" data-variant="icon" data-size="sm" data-orch-copy data-copy-value="${esc(ep.fingerprint)}" aria-label="Copy fingerprint" title="Copy fingerprint">${icon('copy', 'sm')}</button>` : '', true],
            ])}</details>` : ''}
        </div>`;
    }

    function nodeName(n, { link = true } = {}) {
        const name = esc(n.name || n.id);
        const extras = `${n.canary ? ' <span class="orch-tag" title="Canary node: tests and first batches run here">canary</span>' : ''}${n.kind === 'container' ? ' <span class="orch-tag" title="Disposable test node">test node</span>' : ''}`;
        return `<span class="orch-node-name">${link ? `<a href="#/node/${encodeURIComponent(n.id)}">${name}</a>` : name}${extras}</span><span class="orch-sub orch-mono">${esc(n.id)}</span>`;
    }
    function sandboxCell(n) {
        if (n.sandbox === 'none') return '<span data-tone="attention" title="Tools run without a sandbox on this node">no sandbox</span>';
        return n.sandbox ? esc(n.sandbox) : '<span class="orch-muted">—</span>';
    }
    /* The node's agent version, flagged when it's older than the master's. */
    function agentCell(n) {
        const a = n.agent || {};
        if (!a.outdated) return esc(n.agent_version || '—');
        return `${esc(n.agent_version)} <span data-tone="attention" title="${esc(a.updatable ? `Can be updated to ${a.current}` : a.reason)}">(${esc(a.updatable ? 'update available' : 'outdated')})</span>`;
    }
    /* Propose an update job (an ordinary run: plan, approval per the access policy, canary first). */
    /* `stay`: from a node page the job shows (and is approved) right there, under Maintenance. */
    async function updateAgents(nodeIds, button, { stay = false } = {}) {
        try {
            const res = await ui.command('proposeAgentUpdate', nodeIds ? { nodes: nodeIds } : {}, { quiet: true, button });
            const run = res.data.run;
            if (stay) {
                ui.toast(run.state === 'proposed' ? 'Update job proposed: approve it under Maintenance' : 'Update job started', 'ok');
                Orch.refresh();
                return;
            }
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
        return `<span data-tone="danger" title="${esc(errs.join('\n'))}">${esc(ui.plural(errs.length, 'policy error'))}</span>`;
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

    /* ------------------------------------------------------------ node policy (edited on the master)
     * mu/orchestration/node_policy.py: the operator edits policy.yaml here; the master signs it, the
     * node's next poll carries it and the node (root helper) checks it again before installing.
     * Operator only (setNodePolicy is never available to the AI); no approval step — this is the
     * operator's own edit, like changing the file over SSH. */
    function policyDialog(node, done) {
        const rp = node.remote_policy || {};
        const form = document.createElement('form');
        form.className = 'orch-stack orch-policyedit';
        const id = ui.uid('policy');
        form.innerHTML = `<div class="w-field"><label class="w-field__label" for="${id}">policy.yaml on ${esc(node.name)}</label>
            <textarea class="w-textarea" data-mono id="${id}" rows="16" data-autofocus spellcheck="false">${esc(rp.text || '')}</textarea>
            <p class="w-field__hint">The node checks it again before installing and keeps the previous file as policy.yaml.prev. Runs are still decided by the access policy.</p></div>
            <div class="orch-policyedit__check" data-check aria-live="polite"></div>
            <p class="w-field__error" data-error role="alert"></p>
            <div class="w-btn-row orch-dialog__actions"><button type="button" class="w-btn" data-variant="secondary" data-cancel>Cancel</button><button type="submit" class="w-btn" data-variant="primary" disabled>Send to ${esc(node.name)}</button></div>`;
        const d = ui.dialog({ title: `Node policy · ${node.name}`, body: form, size: 'md' });
        const area = form.querySelector(`#${id}`);
        const submit = form.querySelector('[type=submit]');
        const box = form.querySelector('[data-check]');
        const fmt = v => (Array.isArray(v) ? (v.length ? v.join(', ') : 'none') : (v === null || v === undefined ? 'unset' : String(v)));
        const check = ui.debounce(async () => {
            let res;
            try { res = (await api.cmd('validateNodePolicy', { node_id: node.id, text: area.value })).data; }
            catch (err) { box.innerHTML = `<p class="w-field__error">${esc(err.message)}</p>`; submit.disabled = true; return; }
            const same = area.value.trim() === String(rp.text || '').trim();
            submit.disabled = !res.ok || same;
            box.innerHTML = !res.ok
                ? `<p class="w-field__error"><span aria-hidden="true">✗</span> ${res.errors.map(esc).join('<br>')}</p>`
                : same ? '<p class="orch-sub">No changes yet.</p>'
                : `${res.changes.length ? `<dl class="w-facts orch-policyedit__changes">${res.changes.map(c => `<dt>${esc(c.key)}</dt><dd>${esc(fmt(c.from))} → <strong>${esc(fmt(c.to))}</strong></dd>`).join('')}</dl>` : '<p class="orch-sub">Formatting or comments only.</p>'}
                   ${res.warnings.map(w => `<p class="orch-note" data-tone="attention">◆ ${esc(w)}</p>`).join('')}`;
        }, 250);
        area.addEventListener('input', check);
        check();
        form.querySelector('[data-cancel]').onclick = () => d.close();
        form.onsubmit = async e => {
            e.preventDefault();
            try {
                await ui.busy(submit, () => api.cmd('setNodePolicy', { node_id: node.id, text: area.value }));
                ui.toast(`Policy sent; ${node.name} installs it on its next check-in`, 'ok');
                d.close();
                done?.();
            } catch (err) { form.querySelector('[data-error]').innerHTML = `<span aria-hidden="true">✗</span> ${esc(err.message)}`; }
        };
    }
    function policyState(node) {
        const rp = node.remote_policy || {};
        const p = rp.pending;
        const head = rp.allowed
            ? '<button type="button" class="w-textbtn" data-edit-policy>Edit policy</button>'
            : `<span class="orch-sub" title="${esc(rp.reason || '')}">Edited on the node</span>`;
        let line = '';
        if (p && p.state === 'pending') line = `<p class="orch-note" data-tone="attention">◆ An edit by ${esc(p.by || 'the operator')} is waiting for ${esc(node.name)} to check in (sent ${esc(ui.ago(p.at))}). <button type="button" class="w-textbtn" data-tone="quiet" data-cancel-policy>Cancel it</button></p>`;
        else if (p && p.state === 'refused') line = `<p class="orch-note" data-tone="danger">✗ ${esc(node.name)} refused the last edit: ${esc(p.reason || 'no reason given')}</p>`;
        else if (p && p.state === 'applied' && p.applied_at && Date.now() / 1000 - p.applied_at < 3600) line = `<p class="orch-note">✓ The edit by ${esc(p.by || 'the operator')} is in force (${esc(ui.ago(p.applied_at))}).</p>`;
        else if (p && p.state === 'expired') line = '<p class="orch-note" data-tone="attention">◆ The last edit expired before the node checked in.</p>';
        if (!rp.allowed && rp.reason) line += `<p class="orch-sub">${esc(rp.reason)}</p>`;
        return { head, line };
    }

    async function startTestNode(button, done) {
        const image = await ui.prompt({ title: 'Start a disposable test node', label: 'Container image', optional: true, value: '', okLabel: 'Start test node', hint: 'Leave blank for the default image. The node runs in a local container without a sandbox, is marked canary and is revoked when you stop it.' });
        if (image === null) return;
        try {
            await ui.command('startTestNode', { image: image.trim() }, { ok: 'Test node starting', button });
            done?.();
        } catch (_) { /* toasted */ }
    }

    /* ------------------------------------------------------------ meters (Mu §11b) */
    /* A node's fullest disk and memory as inline meters: the number beside a 4px bar, in
     * attention from 80%, danger from 92%. */
    const meterTone = v => (v >= 92 ? 'danger' : (v >= 80 ? 'attention' : ''));
    function meter(label, pct) {
        if (pct == null || pct === '') return '';
        const v = Math.max(0, Math.min(100, Math.round(Number(pct))));
        const tone = meterTone(v);
        return `<span class="orch-meter"><span class="w-bar" style="--v: ${v}%"${tone ? ` data-tone="${tone}"` : ''} role="img" aria-label="${esc(label)} ${v}% used"></span><span class="orch-meter__num"${tone ? ` data-tone="${tone}"` : ''}>${esc(label)} ${v}%</span></span>`;
    }
    const meters = s => [meter('disk', s?.max_disk_used_pct), meter('mem', s?.memory_used_pct)].filter(Boolean).join('');
    /* One node as a row: sign (online / status), name, the state in words when it isn't
     * simply active, its meters, agent drift; last seen on the right. */
    /* Work in flight on a node (vm.node_busy): a pulsing ● and what's running, so a run in
     * progress is visible from the fleet list and the node page. */
    const busyRunning = n => n.busy?.state === 'running' && n.status === 'active';
    function busyWord(n) {
        const b = n.busy;
        if (!b) return '';
        const more = b.count + b.queued > 1 ? ` +${b.count + b.queued - 1}` : '';
        return b.state === 'running'
            ? `<span class="orch-busy" data-tone="running">Running ${esc(b.title)}${more}</span>`
            : `<span class="orch-busy">Queued: ${esc(b.title)}${more}</span>`;
    }
    function busyStrip(n) {
        const b = n.busy;
        if (!b) return '';
        const href = `#/run/${encodeURIComponent(b.run_id)}`;
        return `<div class="orch-busy-strip" role="status" aria-live="polite">${ui.rows([ui.row({
            status: b.state === 'running' ? 'active' : 'pending', title: b.title, href,
            sub: ui.subline([b.state === 'running' ? `running here${b.step_name && b.step_name !== 'main' ? `: ${esc(b.step_name)}` : ''}` : 'queued for the node’s next poll',
                `<span class="w-mono">${esc(b.tool)}</span>`, b.check_mode ? 'dry run' : '',
                b.count + b.queued > 1 ? `${b.count + b.queued - 1} more here` : '']),
            when: b.since ? ui.time(b.since) : '', pinned: true,
            actions: `<a class="w-textbtn" data-tone="quiet" href="${href}">Open run</a>`,
        })])}</div>`;
    }
    /* `draggable`: the row can be dragged onto a group in the list (groups.js). */
    function nodeRow(n, { pinnedAccept = true, draggable = false } = {}) {
        const accept = pinnedAccept && (n.actions || []).find(a => a.id === 'acceptNode' && a.enabled);
        const offline = statusKey(n) === 'offline';
        const stWord = statusKey(n) === 'active' ? '' : `<span${offline || n.status === 'revoked' ? ' data-tone="danger"' : (n.status === 'pending' ? ' data-tone="attention"' : '')}>${esc(ui.cap(offline ? 'offline' : (n.style?.label || n.status)))}</span>`;
        return ui.row({
            status: offline ? 'failed' : busyRunning(n) ? 'active' : ui.statusKey(n.style), title: n.name || n.id, href: `#/node/${encodeURIComponent(n.id)}`,
            titleHtml: `${esc(n.name || n.id)}${n.canary ? ' <span class="orch-tag">canary</span>' : ''}${n.kind === 'container' ? ' <span class="orch-tag">test node</span>' : ''}`,
            sub: ui.subline([stWord, busyWord(n), meters(n.summary), n.summary?.reboot_required ? `<span data-tone="attention" title="${esc((n.summary.reboot_reasons || []).join('; '))}">needs reboot</span>` : '',
                n.agent?.outdated ? `<span data-tone="attention">agent ${esc(n.agent_version)} → ${esc(n.agent.current)}</span>` : '',
                n.sandbox === 'none' ? sandboxCell(n) : '', (n.policy_errors || []).length ? policyCell(n) : '']),
            when: n.last_seen ? ui.time(n.last_seen) : '', pinned: !!accept,
            actions: accept ? `<button type="button" class="w-textbtn" data-accept="${esc(n.id)}">Accept</button>` : '',
            attrs: draggable && n.status !== 'revoked' ? ` draggable="true" data-node-id="${esc(n.id)}" data-node-name="${esc(n.name || n.id)}"` : '',
        });
    }
    function bindAccept(root, nodes) {
        root.querySelectorAll('[data-accept]').forEach(b => b.addEventListener('click', async () => {
            const n = nodes.find(x => x.id === b.dataset.accept);
            const ok = await ui.confirm({ title: `Accept ${n.name}?`, message: `It will receive work that targets its labels (${ui.formatLabels(n.labels) || 'no labels'}). Check the name and labels match the machine you enrolled.`, confirmLabel: 'Accept node' });
            if (!ok) return;
            try { await ui.command('acceptNode', { node_id: n.id }, { ok: `${n.name} accepted`, button: b }); Orch.refresh(); } catch (_) { /* toasted */ }
        }));
    }

    /* ------------------------------------------------------------ inventory (the LIST pane) */
    /* `scopeGroup` (groups.js): only that group's nodes ("-" = in no group, "*" = all);
     * `draggable`: rows drag onto groups. */
    async function renderInventory(el, route, { isCurrent, setQuery, soft, tools, scopeGroup = '*', draggable = false }) {
        const data = await api.get('nodes');
        if (!isCurrent()) return null;
        if (scopeGroup === '-') data.nodes = data.nodes.filter(n => !(n.groups || []).length);
        else if (scopeGroup && scopeGroup !== '*') data.nodes = data.nodes.filter(n => (n.groups || []).includes(scopeGroup));
        const filter = route.query.get('status') || '';
        const prev = soft ? el.querySelector('#orch-node-search')?.value : null;
        const search = prev ?? (route.query.get('q') || '');
        const counts = {};
        data.nodes.forEach(n => { const k = statusKey(n); counts[k] = (counts[k] || 0) + 1; });
        if (tools) {
            tools.innerHTML = '<button type="button" class="w-textbtn" data-tone="quiet" data-enrol title="Create a join token for one machine">Quick enrol</button>';
            tools.querySelector('[data-enrol]').onclick = enrol;
        }
        el.innerHTML = `
            <div class="orch-listtools">
                <label class="w-visually-hidden" for="orch-node-search">Search nodes</label>
                <input class="w-input orch-search" id="orch-node-search" type="search" placeholder="Search name, id or label" value="${esc(search)}" autocomplete="off">
                ${Object.keys(counts).length > 1 ? `<div class="orch-textfilters" role="group" aria-label="Filter by status">
                    <button type="button" class="orch-textfilter" aria-pressed="${!filter}" data-filter="">All <span class="orch-count">${data.nodes.length}</span></button>
                    ${STATUSES.filter(s => counts[s]).map(s => `<button type="button" class="orch-textfilter" aria-pressed="${filter === s}" data-filter="${esc(s)}">${esc(ui.cap(ui.vstyle('orch_node_status', s).label))} <span class="orch-count">${counts[s]}</span></button>`).join('')}
                </div>` : ''}
            </div>
            <div data-node-table></div>`;
        const paintTable = () => {
            const q = (el.querySelector('#orch-node-search').value || '').toLowerCase();
            const f = route.query.get('status') || '';
            const rows = data.nodes.filter(n => (!f || statusKey(n) === f) && (!q || [n.name, n.id, n.address, ui.formatLabels(n.labels)].join(' ').toLowerCase().includes(q)));
            el.querySelector('[data-node-table]').innerHTML = ui.rows(rows.map(n => nodeRow(n, { draggable })), { label: 'Nodes', empty: data.nodes.length ? 'No nodes match this filter.' : (scopeGroup && scopeGroup !== '*' ? 'No nodes here yet.' : 'No nodes enrolled yet. Set one up, or create a join token.') });
            bindAccept(el, data.nodes);
            Orch.markSelected?.();
        };
        paintTable();
        el.querySelector('#orch-node-search').addEventListener('input', ui.debounce(() => { setQuery({ q: el.querySelector('#orch-node-search').value }); paintTable(); }, 150));
        el.querySelectorAll('[data-filter]').forEach(b => b.addEventListener('click', () => {
            setQuery({ status: b.dataset.filter });
            el.querySelectorAll('[data-filter]').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
            paintTable();
        }));
        return null;
    }

    /* ------------------------------------------------------------ fleet (MAIN with no node chosen) */
    /* The fleet's plumbing beside the inventory, as a board of flat sections: the endpoint
     * nodes dial, agent drift, test nodes, groups and join tokens. */
    async function renderFleet(el, route, { isCurrent }) {
        const data = await api.get('nodes');
        if (!isCurrent()) return null;
        const testStatuses = Object.values(data.test_nodes || {});
        const n = data.nodes;
        const online = n.filter(x => x.online).length;
        const avg = key => { const v = n.map(x => x.summary?.[key]).filter(x => x != null); return v.length ? v.reduce((a, b) => a + Number(b), 0) / v.length : null; };
        const hot = n.filter(x => (x.summary?.max_disk_used_pct ?? 0) >= 80 || (x.summary?.memory_used_pct ?? 0) >= 80);
        el.innerHTML = `
            <header class="orch-head"><h1 class="w-page__title">Fleet</h1></header>
            <div class="w-board orch-board">
                <section class="w-board__cell" aria-labelledby="orch-fl-h">
                    <header class="w-pane__head"><h2 class="w-pane__title" id="orch-fl-h">Fleet</h2></header>
                    <span class="w-board__big">${esc(online)}<span class="orch-big__of"> / ${esc(n.length)} online</span></span>
                    <p class="orch-board__line">${meter('average disk', avg('max_disk_used_pct'))}</p>
                    <p class="orch-board__line">${meter('average mem', avg('memory_used_pct'))}</p>
                    ${hot.length ? `<p class="orch-board__line" data-tone="attention">${esc(ui.plural(hot.length, 'node is', 'nodes are'))} above 80% disk or memory: ${hot.slice(0, 4).map(x => `<a href="#/node/${encodeURIComponent(x.id)}">${esc(x.name || x.id)}</a>`).join(', ')}</p>` : ''}
                </section>
                <section class="w-board__cell" aria-labelledby="orch-ep2-h">
                    <header class="w-pane__head"><h2 class="w-pane__title" id="orch-ep2-h">Endpoint</h2></header>
                    ${endpointLine(data.endpoint)}
                    ${agentBanner(data.agents)}
                </section>
                <section class="w-board__cell" aria-labelledby="orch-tn-h">
                    <header class="w-pane__head"><h2 class="w-pane__title" id="orch-tn-h">Test nodes</h2><button type="button" class="w-textbtn" data-tone="quiet" data-test-node>Start one</button></header>
                    ${testStatuses.length ? ui.rows(testStatuses.map(t => ui.row({
                        status: t.state === 'ready' ? 'done' : (t.state === 'failed' ? 'failed' : 'active'),
                        title: t.container, titleHtml: `<span class="w-mono">${esc(t.container)}</span>`, href: t.node_id ? `#/node/${encodeURIComponent(t.node_id)}` : '',
                        sub: ui.subline([esc(ui.cap(t.state === 'ready' ? 'ready' : (t.state || 'starting'))), `<span class="w-mono">${esc(t.image)}</span>`, t.error ? `<span data-tone="danger">${esc(t.error)}</span>` : '']),
                        pinned: true,
                        actions: t.node_id && (data.test_node_ids || []).includes(t.node_id) ? `<button type="button" class="w-textbtn" data-tone="danger" data-stop-test="${esc(t.node_id)}">Stop</button>` : '',
                    })), { label: 'Test nodes' }) : '<p class="w-group__empty">A disposable node in a local container: canary, no sandbox, revoked when you stop it.</p>'}
                </section>
                <section class="w-board__cell" aria-labelledby="orch-tokens-h">
                    <header class="w-pane__head"><h2 class="w-pane__title" id="orch-tokens-h">Join tokens</h2><button type="button" class="w-textbtn" data-tone="quiet" data-enrol>New token</button></header>
                    ${ui.rows((data.tokens || []).slice(0, 10).map(t => {
                        const expired = !t.used_at && t.expires_at && t.expires_at * 1000 < Date.now();
                        const st = t.used_at ? 'done' : (expired ? 'skipped' : 'active');
                        return ui.row({
                            status: st, title: t.note || (Object.keys(t.labels || {}).length ? ui.formatLabels(t.labels) : 'join token'),
                            href: t.node_id ? `#/node/${encodeURIComponent(t.node_id)}` : '',
                            sub: ui.subline([t.used_at ? 'used' : (expired ? 'expired' : `open until ${esc(ui.when(t.expires_at))}`), t.canary ? 'canary' : '', t.note && Object.keys(t.labels || {}).length ? esc(ui.formatLabels(t.labels)) : '']),
                            when: ui.time(t.created_at),
                        });
                    }), { label: 'Join tokens', empty: 'No join tokens yet.' })}
                </section>
            </div>`;
        el.querySelector('[data-update-agents]')?.addEventListener('click', e => updateAgents(null, e.currentTarget));
        el.querySelectorAll('[data-enrol]').forEach(b => b.addEventListener('click', enrol));
        el.querySelector('[data-test-node]').addEventListener('click', e => startTestNode(e.currentTarget, () => Orch.refresh()));
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
        const execRows = ui.rows(execs.map(x => ui.row({
            status: ui.statusKey(x.style), title: x.tool, titleHtml: `<span class="w-mono">${esc(x.tool)}</span>`, href: `#/run/${encodeURIComponent(x.run_id)}`,
            sub: ui.subline([esc(ui.cap(x.style?.label || x.state || '')), esc(x.step_name || x.step), x.exit_code == null ? '' : `exit ${esc(x.exit_code)}`, x.duration_s != null ? esc(ui.dur(x.duration_s)) : '', x.check_mode ? 'check mode' : '', x.rollback_of ? 'rollback' : '', x.reason ? esc(x.reason) : '']),
            when: ui.time(x.created_at),
        })), { label: 'Recent executions', empty: 'Nothing has run here yet.' });
        const labels = Object.entries(n.labels || {});
        el.innerHTML = `
            <header class="orch-head"><h1 class="w-page__title">${esc(n.name || n.id)}</h1></header>
            <div class="orch-detail">
                <div class="orch-detail__main">
                ${n.sandbox === 'none' ? ui.banner('caution', '◆', '<strong>No sandbox.</strong> This node runs tools directly on the host (its local policy sets <code>sandbox: none</code>). Keep it to test or low-risk machines.') : ''}
                ${(n.policy_errors || []).length ? ui.banner('danger', '✗', `<strong>The node’s local policy has errors</strong>, so it refuses work until they’re fixed: ${n.policy_errors.map(esc).join('; ')}`) : ''}
                ${busyStrip(n)}
                <section class="w-group orch-maint" aria-labelledby="orch-maint-h" data-maint>
                    <div class="w-group__head"><h2 class="w-group__title" id="orch-maint-h">Maintenance</h2><span class="orch-sub">updates, reboots and services, as reviewed runs</span></div>
                    ${ui.loading('Checking what this node allows…')}</section>
                <section class="w-group" aria-labelledby="orch-hw-h"><div class="w-group__head"><h2 class="w-group__title" id="orch-hw-h">Machine</h2><span class="orch-sub">${n.facts_at ? `facts collected ${esc(ui.ago(n.facts_at))}` : 'facts not collected yet'}</span></div>
                    <div class="orch-gauges">
                        ${s.max_disk_used_pct != null ? `<div class="orch-gauge"><span class="orch-gauge__label">Fullest disk</span><span class="orch-gauge__num">${esc(Math.round(s.max_disk_used_pct))}%</span><span class="w-bar" style="--v: ${esc(Math.round(s.max_disk_used_pct))}%; --w-bar-w: 100%"${meterTone(s.max_disk_used_pct) ? ` data-tone="${meterTone(s.max_disk_used_pct)}"` : ''} aria-hidden="true"></span></div>` : ''}
                        ${s.memory_used_pct != null ? `<div class="orch-gauge"><span class="orch-gauge__label">Memory${s.memory_gb != null ? ` of ${esc(s.memory_gb)} GB` : ''}</span><span class="orch-gauge__num">${esc(Math.round(s.memory_used_pct))}%</span><span class="w-bar" style="--v: ${esc(Math.round(s.memory_used_pct))}%; --w-bar-w: 100%"${meterTone(s.memory_used_pct) ? ` data-tone="${meterTone(s.memory_used_pct)}"` : ''} aria-hidden="true"></span></div>` : ''}
                        ${s.load != null ? `<div class="orch-gauge"><span class="orch-gauge__label">Load (1 min)${s.cpus ? ` on ${esc(ui.plural(s.cpus, 'CPU'))}` : ''}</span><span class="orch-gauge__num">${esc(s.load)}</span>${s.cpus ? `<span class="w-bar" style="--v: ${esc(Math.min(100, Math.round(100 * Number(s.load) / Number(s.cpus))))}%; --w-bar-w: 100%" aria-hidden="true"></span>` : ''}</div>` : ''}
                        ${s.uptime_days != null ? `<div class="orch-gauge"><span class="orch-gauge__label">Uptime</span><span class="orch-gauge__num">${esc(s.uptime_days)} d</span></div>` : ''}
                    </div>
                    ${ui.kv([
                        ['Hostname', s.hostname], ['OS', s.os], ['Kernel', s.kernel], ['Architecture', s.arch], ['CPUs', s.cpus],
                        ['systemd', s.systemd == null ? '' : (s.systemd ? `yes${s.services != null ? ` · ${s.services} services` : ''}` : 'no')],
                        ['Address', n.address],
                    ])}
                    ${(s.provides || []).length ? `<details class="w-disclose"><summary>Provides ${esc(s.provides.length)} commands and services</summary><p class="orch-labels">${s.provides.map(p => `<span class="orch-label">${esc(p)}</span>`).join('')}</p></details>` : ''}
                </section>
                <section class="w-group orch-hist" aria-labelledby="orch-hist-h"><div class="w-group__head"><h2 class="w-group__title" id="orch-hist-h">History</h2><span class="orch-sub">CPU, memory, disk, load, network and disk I/O over time</span></div>
                    <div data-history></div></section>
                <section class="w-group" aria-labelledby="orch-lab-h"><div class="w-group__head"><h2 class="w-group__title" id="orch-lab-h">Labels and groups</h2></div>
                    ${labels.length ? `<p class="orch-labels">${labels.map(([k, v]) => `<span class="orch-label">${esc(k)}=${esc(v)}</span>`).join('')}</p>` : '<p class="orch-muted">No labels. Runs target nodes by label; set some from the actions.</p>'}
                    <p class="orch-sub">${(n.groups || []).length ? `In ${n.groups.map(g => `<a href="#/group/${encodeURIComponent(g)}">${esc(g)}</a>`).join(', ')}` : 'In no group. Drag it onto a group in the Nodes list, or manage groups on the Groups tab.'}</p>
                </section>
                <section class="w-group" aria-labelledby="orch-pol-h" data-policy-section><div class="w-group__head"><h2 class="w-group__title" id="orch-pol-h">Node-local policy</h2><span class="orch-sub">what this node will refuse</span>${policyState(n).head}</div>
                    ${policyState(n).line}
                    ${Object.keys(policy).length ? ui.kv(POLICY_ROWS.filter(([k]) => k in policy).map(([k, label]) => [label, listText(policy[k])])) : '<p class="orch-muted">The node hasn’t reported a policy yet.</p>'}</section>
                </div>
                <aside class="orch-detail__aside" aria-label="About this node">
                    <div class="orch-toolbar">${ui.actionBar(n.actions, { label: 'Node actions' })}${n.kind === 'container' && n.status !== 'revoked' ? '<button type="button" class="w-textbtn" data-tone="danger" data-stop-test>Stop test node</button>' : ''}</div>
                    <section class="w-group" aria-labelledby="orch-agent-h"><h2 class="w-group__title" id="orch-agent-h">Agent</h2>
                        <p class="orch-note"${n.agent?.outdated ? ' data-tone="attention"' : ''}>${n.agent_version ? `mucli-node ${esc(n.agent_version)}` : 'Version not reported yet'}${n.agent?.outdated ? ` · master has ${esc(n.agent.current)}` : (n.agent_version ? ' · up to date' : '')}</p>
                        ${n.agent?.outdated ? (n.agent.updatable ? '<button type="button" class="w-textbtn" data-update-agent>Update this node</button>' : `<p class="orch-sub">${esc(n.agent.reason || '')}</p>`) : ''}
                    </section>
                    <section class="w-group" aria-labelledby="orch-ex-h"><h2 class="w-group__title" id="orch-ex-h">Recent executions</h2>${execRows}</section>
                    <details class="w-disclose"><summary>Details</summary>
                        ${ui.kv([['Node id', `<code class="orch-mono">${esc(n.id)}</code>`, true], ['Address', n.address], ['Agent version', n.agent_version],
                            ['Last seen', `${ui.time(n.last_seen)}${n.online ? ' · online' : ''}`, true], ['Enrolled', ui.when(n.created_at)]])}
                        <details class="w-disclose"><summary>Raw facts</summary>${ui.code(ui.json(n.facts || {}), { lang: 'json', title: 'facts.json' })}</details>
                    </details>
                </aside>
            </div>`;
        Orch.itemState([
            { text: ui.cap(statusKey(n) === 'active' ? (n.style?.label || n.status) : (n.style?.label || statusKey(n))), tone: n.status === 'pending' ? 'attention' : statusKey(n) === 'offline' || n.status === 'revoked' ? 'danger' : '' },
            busyRunning(n) ? { text: 'working', href: `#/run/${encodeURIComponent(n.busy.run_id)}` } : null,
            n.canary ? { text: 'canary' } : null, n.kind ? { text: n.kind } : null,
            { text: Object.keys(n.labels || {}).length ? ui.formatLabels(n.labels) : 'no labels' },
            n.last_seen ? { text: `seen ${ui.ago(n.last_seen)}` } : null,
        ].filter(Boolean));
        const done = () => Orch.refresh();
        ui.bindActions(el, n.actions, { args: { node_id: n.id }, subject: n.name, done, dialogs: { labels: () => labelsDialog(n, done) } });
        el.querySelector('[data-update-agent]')?.addEventListener('click', e => updateAgents([n.id], e.currentTarget, { stay: true }));
        renderMaintenance(el.querySelector('[data-maint]'), n);
        el.querySelector('[data-edit-policy]')?.addEventListener('click', () => policyDialog(n, done));
        el.querySelector('[data-cancel-policy]')?.addEventListener('click', async e => {
            try { await ui.command('cancelNodePolicy', { node_id: n.id }, { ok: 'Policy edit cancelled', button: e.currentTarget }); done(); } catch (_) { /* toasted */ }
        });
        el.querySelector('[data-stop-test]')?.addEventListener('click', async e => {
            if (!await ui.confirm({ title: 'Stop this test node?', message: 'The container is removed and the node is revoked.', confirmLabel: 'Stop test node', tone: 'danger' })) return;
            try { await ui.command('stopTestNode', { node_id: n.id }, { ok: 'Test node stopped', button: e.currentTarget }); done(); } catch (_) { /* toasted */ }
        });
        /* History charts (history.js): poll while shown; an agent before 1.3.0 records none. */
        const history = Orch.history?.mount(el.querySelector('[data-history]'), {
            url: `nodes/${encodeURIComponent(n.id)}/metrics`,
            notice: data => ((data.needs_update || []).length
                ? ui.banner('attention', '◆', 'This node’s agent predates 1.3.0 — update it to record history.',
                    n.agent?.updatable ? '<button type="button" class="w-btn" data-variant="tonal" data-tone="attention" data-size="sm" data-history-update>Update this node</button>' : '')
                : ''),
            bind: box => box.querySelector('[data-history-update]')?.addEventListener('click', e => updateAgents([n.id], e.currentTarget, { stay: true })),
        });
        return history ? { dispose: () => history.dispose() } : null;
    }

    /* ------------------------------------------------------------ maintenance (the control panel)
     * Built-in tools (mu/orchestration/maintenance.py) one click away: each button proposes an
     * ordinary run for this node (nodeMaintenance) and opens it; the run page asks for approval
     * as the access policy says, and the node's own policy still decides. Dry run = check mode. */
    const MAINT_DONE = { check_updates: 'Update check', security_updates: 'Security updates', full_upgrade: 'Upgrade',
        reboot: 'Reboot', service: 'Service action' };
    async function startMaintenance(n, action, { check = false, params = {}, button = null } = {}) {
        try {
            const res = await ui.command('nodeMaintenance', { node_id: n.id, action, params, check_mode: check }, { quiet: true, button });
            const waiting = res.data.run.state === 'proposed';
            ui.toast(waiting ? `${check ? 'Dry run' : (MAINT_DONE[action] || 'Run')} proposed: approve it below` : (check ? 'Dry run started' : 'Started'), 'ok');
            /* Stay on the node: the run appears under "Open runs here" with its decision. */
            Orch.refresh();
        } catch (_) { /* toasted */ }
    }
    async function renderMaintenance(box, n) {
        if (!box) return;
        let m;
        try { m = await api.get(`nodes/${encodeURIComponent(n.id)}/maintenance`); }
        catch (err) { box.innerHTML = ui.errorState(err, { retry: false }); return; }
        const r = m.reboot || {};
        const pend = m.pending_updates;
        const status = [];
        if (r.required === null) status.push('<span class="orch-muted">Reboot status needs agent 1.2.0 or newer</span>');
        else if (r.required) status.push(`<span data-tone="attention"><strong>Needs a reboot</strong>${r.reasons?.length ? `: ${esc(r.reasons.join('; '))}` : ''}</span>`);
        else status.push('No reboot needed');
        if (m.uptime_days != null) status.push(`up ${esc(m.uptime_days)} days`);
        if (pend) status.push(pend.count ? `<span data-tone="attention">${esc(ui.plural(pend.count, 'pending update'))}</span> <span class="orch-muted">checked ${esc(ui.ago(pend.at))}</span>` : `no pending updates <span class="orch-muted">checked ${esc(ui.ago(pend.at))}</span>`);
        else status.push('<span class="orch-muted">updates not checked yet</span>');
        const row = a => {
            const blocked = !a.available;
            const primary = blocked ? '' : `<button type="button" class="w-textbtn" data-maint-run="${esc(a.key)}"${a.danger ? ' data-tone="danger"' : ''} title="${esc(a.help)}">${esc(a.key === 'check_updates' ? 'Check now' : a.label)}</button>`;
            const dry = a.check_available ? `<button type="button" class="w-textbtn" data-tone="quiet" data-maint-check="${esc(a.key)}" title="Check mode: report what would change, change nothing">Dry run</button>` : '';
            const svc = a.key === 'service' ? `<span class="orch-maint__svc">
                    <input class="w-input" list="orch-maint-units-${esc(n.id)}" data-maint-unit placeholder="unit, e.g. nginx" aria-label="Service" maxlength="128">
                    <datalist id="orch-maint-units-${esc(n.id)}">${(m.services || []).map(u => `<option value="${esc(u)}"></option>`).join('')}</datalist>
                    <span class="w-select" data-size="sm"><select class="w-select__control" data-maint-svcaction aria-label="Service action">
                        ${['restart', 'reload', 'start', 'stop'].map(x => `<option value="${x}">${x}</option>`).join('')}</select></span></span>` : '';
            const opt = a.key === 'full_upgrade' && !blocked ? '<label class="w-check orch-maint__opt"><input type="checkbox" class="w-check__input" data-maint-removals><span>Allow removals</span></label>'
                : a.key === 'reboot' && !blocked ? '<label class="w-check orch-maint__opt"><input type="checkbox" class="w-check__input" data-maint-onlyif><span>Only if needed</span></label>' : '';
            return `<li class="orch-maint__row" data-key="${esc(a.key)}"${blocked ? ' data-blocked' : ''}>
                <div class="orch-maint__what"><span class="orch-maint__label">${esc(a.label)}</span><span class="orch-sub">${esc(blocked ? a.reason : a.help)}${blocked && (n.remote_policy || {}).allowed && /max_effect|allowed_tools|denied_tools|accept_categories/.test(a.reason) ? ' <button type="button" class="w-textbtn" data-edit-policy-inline>Edit policy</button>' : ''}</span></div>
                <div class="orch-maint__do">${blocked && a.key === 'service' ? '' : svc}${opt}${primary}${dry}</div></li>`;
        };
        const installed = (m.actions || []).every(a => a.installed);
        box.innerHTML = `
            <div class="w-group__head"><h2 class="w-group__title" id="orch-maint-h">Maintenance</h2><span class="orch-sub">updates, reboots and services, as reviewed runs</span></div>
            <p class="w-state orch-maint__status">${status.map(x => `<span class="w-state__part">${x}</span>`).join('')}</p>
            ${(m.open_runs || []).length ? `<div class="orch-maint__open" aria-label="Open runs on this node">
                <h3 class="orch-maint__subhead">Open runs here</h3>
                ${ui.rows(m.open_runs.map((r, i) => ui.row({
                    status: ui.statusKey(r.style), title: r.title, href: `#/run/${encodeURIComponent(r.id)}`,
                    sub: ui.subline([esc(ui.cap(r.style?.label || r.state)), r.state === 'proposed' ? esc(r.decision?.summary || '') : `here: ${esc(String(r.here).replace(/_/g, ' '))}`,
                        r.progress && r.nodes > 1 ? esc(r.progress) : '', r.check_mode ? 'dry run' : '', r.paused_reason ? esc(r.paused_reason) : '']),
                    when: ui.time(r.created_at), pinned: true, attrs: ` data-open-run="${i}"`,
                    actions: ui.rowActions(r.actions, { only: ['approveRun', 'rejectRun', 'continueRun', 'cancelRun'] }) + `<a class="w-textbtn" data-tone="quiet" href="#/run/${encodeURIComponent(r.id)}">Open run</a>`,
                })))}</div>` : ''}
            ${installed ? '' : `<div class="w-decision orch-maint__install" data-tone="attention"><p class="w-decision__q">The built-in maintenance tools aren’t installed yet.</p>
                <p class="w-decision__r">They’re signed and published like any tool: reboot, security updates, full upgrade and service control, each with a dry run.</p>
                <div class="w-decision__acts"><button type="button" class="w-btn" data-variant="primary" data-maint-install>Install built-in tools</button></div></div>`}
            <ul class="orch-maint__list">${(m.actions || []).map(row).join('')}</ul>
            ${(m.recent || []).length ? `<details class="w-disclose"><summary>Recent maintenance (${esc(m.recent.length)})</summary>${ui.rows(m.recent.map(x => ui.row({
                status: ui.statusKey(x.style), title: x.tool, titleHtml: `<span class="w-mono">${esc(x.tool)}</span>`, href: `#/run/${encodeURIComponent(x.run_id)}`,
                sub: ui.subline([esc(ui.cap(x.style?.label || x.state)), x.check_mode ? 'dry run' : '']), when: ui.time(x.finished_at || x.created_at),
            })))}</details>` : ''}`;
        (m.open_runs || []).forEach((r, i) => {
            const rowEl = box.querySelector(`[data-open-run="${i}"]`);
            if (rowEl) ui.bindActions(rowEl, r.actions, { args: { run_id: r.id }, subject: r.title, done: () => Orch.refresh() });
        });
        box.querySelector('[data-maint-install]')?.addEventListener('click', async e => {
            try { await ui.command('installStarterPack', {}, { ok: 'Built-in tools installed', button: e.currentTarget }); renderMaintenance(box, n); } catch (_) { /* toasted */ }
        });
        const paramsFor = key => {
            const li = box.querySelector(`.orch-maint__row[data-key="${key}"]`);
            if (key === 'service') return { unit: (li.querySelector('[data-maint-unit]')?.value || '').trim(), action: li.querySelector('[data-maint-svcaction]')?.value || 'restart' };
            if (key === 'full_upgrade') return { allow_removals: !!li.querySelector('[data-maint-removals]')?.checked };
            if (key === 'reboot') return { only_if_required: !!li.querySelector('[data-maint-onlyif]')?.checked };
            return {};
        };
        const go = async (key, check, button) => {
            const params = paramsFor(key);
            if (key === 'service' && !params.unit) { ui.toast('Choose a service first.', 'danger'); box.querySelector('[data-maint-unit]')?.focus(); return; }
            if (key === 'reboot' && !check) {
                const ok = await ui.confirm({ title: `Reboot ${n.name}?`, message: `${n.name} reboots 30 seconds after the run is approved and is unreachable for a minute or two.${params.only_if_required ? ' It only reboots if it still needs one.' : ''}`, confirmLabel: 'Propose reboot', tone: 'danger' });
                if (!ok) return;
            }
            startMaintenance(n, key, { check, params, button });
        };
        box.querySelectorAll('[data-edit-policy-inline]').forEach(b => b.addEventListener('click', () => policyDialog(n, () => Orch.refresh())));
        box.querySelectorAll('[data-maint-run]').forEach(b => b.addEventListener('click', e => go(b.dataset.maintRun, false, e.currentTarget)));
        box.querySelectorAll('[data-maint-check]').forEach(b => b.addEventListener('click', e => go(b.dataset.maintCheck, true, e.currentTarget)));
    }

    Orch.nodes = { enrol, startMaintenance, policyDialog, endpointCard, endpointLine, nodeName, labelsDialog, updateAgents, renderInventory, renderFleet, nodeRow, meter, meters };
    /* #/nodes (the dashboard) is groups.js. */
    Orch.routes.node = { nav: 'nodes', title: 'Node', scopes: ['node', 'run'], render: renderDetail, crumbs: r => [{ label: 'Nodes', to: '#/nodes' }, { label: r.parts[0] || 'Node' }] };
})();
