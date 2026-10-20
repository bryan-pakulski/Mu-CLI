/* Orchestration — Set up a node (documentation/plans/runbook_editor.md §5).
 *
 * A guided, resumable wizard: master readiness → describe the node → get the
 * agent onto it (one-liner, downloads or manual commands) → wait for it to
 * enrol (live) → accept → confirm it is alive (heartbeat + a real sys.facts
 * round trip through verifyNode). The wizard's state is the join token it
 * created: #/setup/<token id> resumes it (also remembered in localStorage
 * under orch.setup.last), and everything else is read back from the server.
 */
(() => {
    'use strict';
    const api = window.OrchAPI;
    const ui = window.OrchUI;
    const { esc, icon } = ui;
    const Orch = (window.Orch = window.Orch || { routes: {} });
    Orch.routes = Orch.routes || {};

    const STORE_KEY = 'orch.setup.last';
    const STEPS = ['Master', 'Describe', 'Install', 'Enrol', 'Accept', 'Confirm'];
    const LIFETIMES = [[3600, '1 hour'], [4 * 3600, '4 hours'], [86400, '24 hours'], [7 * 86400, '7 days']];
    const QUIET_AFTER_S = 300;
    const HEARTBEAT_S = 30;
    const TERMINAL = ['succeeded', 'failed', 'cancelled', 'rejected', 'rolled_back', 'expired'];

    /* ------------------------------------------------------------ pure helpers (tested in node) */
    /* 1..6 is the step to work on, 7 means finished. */
    function stepFor({ endpointReady = false, setup = null, waiting = false, heartbeatOk = false, roundTripOk = false } = {}) {
        if (!setup) return endpointReady ? 2 : 1;
        if (!setup.used_at) return waiting ? 4 : 3;
        const node = setup.node;
        if (!node || node.status !== 'active') return 5;
        return heartbeatOk && roundTripOk ? 7 : 6;
    }
    function policyWarnings(policy, errors) {
        const out = [];
        const p = policy || {};
        if ((errors || []).length) out.push({ tone: 'danger', text: `The node’s policy.yaml is invalid, so it refuses all work: ${errors.join('; ')}` });
        if (!Object.keys(p).length) out.push({ tone: 'caution', text: 'The node hasn’t reported its local policy yet.' });
        if (p.deny_all) out.push({ tone: 'danger', text: 'The local policy denies everything (deny_all).' });
        if (p.max_effect === 'read_only') out.push({ tone: 'caution', text: 'Read-only: the node refuses any tool that changes the system (max_effect: read_only). Raise it in /etc/mucli-node/policy.yaml when you want changes.' });
        if (p.sandbox === 'none') out.push({ tone: 'caution', text: 'No sandbox: tools run directly on the host (sandbox: none).' });
        return out;
    }
    /* What to change on the node for a refused or failed setup check. */
    function failureHint(reason) {
        const r = String(reason || '').toLowerCase();
        // Wording from mu/orchestration/node/policy.py's refusals.
        if (/denied on this node|denied_tools/.test(r)) return 'Remove the matching pattern (for example "sys.*") from denied_tools in /etc/mucli-node/policy.yaml on the node.';
        if (/allowed_tools|tool .*not allowed|not in allowed/.test(r)) return 'Allow the tool in /etc/mucli-node/policy.yaml on the node: add "sys.*" (or "sys.facts") to allowed_tools.';
        if (/categor/.test(r)) return 'Add inventory to accept_categories in /etc/mucli-node/policy.yaml.';
        if (/runtime/.test(r)) return 'sys.facts is a builtin: allow the builtin runtime in allowed_runtimes in /etc/mucli-node/policy.yaml.';
        if (/max_effect|effect/.test(r)) return 'sys.facts is read-only; check max_effect in /etc/mucli-node/policy.yaml.';
        if (/run_as|privilege|user/.test(r)) return 'sys.facts runs as nobody: check allowed_run_as and max_privilege in /etc/mucli-node/policy.yaml.';
        if (/policy does not parse|cannot read policy/.test(r)) return 'The node’s /etc/mucli-node/policy.yaml is broken: fix it on the node (the agent refuses everything until it parses).';
        if (/quiet/.test(r)) return 'The node is in its quiet hours (quiet_hours in policy.yaml); try again later or adjust them.';
        if (/signature|key/.test(r)) return 'The node doesn’t trust this master’s signing key: re-enrol it with a new join token.';
        if (/unreachable|timed out|timeout/.test(r)) return 'The node stopped answering: check `systemctl status mucli-node` and `journalctl -u mucli-node` on it.';
        return 'Check /etc/mucli-node/policy.yaml and `journalctl -u mucli-node` on the node.';
    }
    function reachCommand(url) {
        return url ? `curl -sk -o /dev/null -w '%{http_code}\\n' ${url}/api/orchestration/node/install.sh   # expect 200` : '';
    }
    /* A poll received after acceptance, and recent. */
    function heartbeatOk(node, now = Date.now() / 1000) {
        if (!(node && node.status === 'active' && node.last_seen)) return false;
        if (node.accepted_at && Number(node.last_seen) < Number(node.accepted_at)) return false;
        return now - Number(node.last_seen) <= HEARTBEAT_S;
    }
    /* The main (last) step's execution of a run detail. */
    function mainExec(run) {
        const row = (run?.grid || [])[0];
        const cells = row?.cells || [];
        return cells.length ? cells[cells.length - 1].exec : null;
    }

    const remember = value => { try { localStorage.setItem(STORE_KEY, JSON.stringify(value)); } catch (_) { /* private mode */ } };
    const recall = () => { try { return JSON.parse(localStorage.getItem(STORE_KEY) || 'null'); } catch (_) { return null; } };

    /* ------------------------------------------------------------ rendering bits */
    function stepper(step) {
        return `<ol class="w-stepper orch-stepper orch-setup__stepper" aria-label="Setup progress">${STEPS.map((label, i) => {
            const n = i + 1;
            const st = n < step ? 'done' : (n === step ? 'active' : 'pending');
            const glyph = { done: '✓', active: '●', pending: '○' }[st];
            return `<li class="w-stepper__step" data-status="${st}"${n === step ? ' aria-current="step"' : ''}><span aria-hidden="true">${glyph}</span>${esc(label)}</li>`;
        }).join('')}</ol>`;
    }
    const card = (n, title, body, { state = '', foot = '' } = {}) => `<section class="w-card orch-card orch-setup__card"${state ? ` data-state="${esc(state)}"` : ''} aria-labelledby="orch-setup-h${n}">
        <header class="w-card__head"><span class="orch-step-num" aria-hidden="true">${n}</span><h2 class="w-card__title" id="orch-setup-h${n}" tabindex="-1">${esc(title)}</h2></header>
        <div class="w-card__body orch-stack">${body}</div>${foot ? `<footer class="w-card__foot">${foot}</footer>` : ''}</section>`;
    const doneRow = (n, title, text, action = '') => `<li class="orch-setup__done"><span class="w-status" data-tone="ok"><span class="w-status__glyph" aria-hidden="true">✓</span>${esc(`${n}. ${title}`)}</span><span class="orch-sub">${text}</span>${action}</li>`;
    const check = (ok, label, detail = '', pending = false) => `<li class="orch-setup__check">${ui.chip(ok ? { semantic: 'ok', glyph: '✓', label: 'Passed' } : pending ? { semantic: 'running', glyph: '●', label: 'Waiting' } : { semantic: 'neutral', glyph: '○', label: 'Not yet' })}<span class="orch-setup__check-label">${esc(label)}</span>${detail ? `<span class="orch-sub">${detail}</span>` : ''}</li>`;
    const err = text => `<p class="w-field__error" data-error role="alert">${text ? `<span aria-hidden="true">✗</span> ${esc(text)}` : ''}</p>`;

    function factsKv(node) {
        const s = node.summary || {};
        const f = node.facts || {};
        return ui.kv([['Hostname', s.hostname], ['OS', s.os], ['Kernel', s.kernel], ['Python', f.python], ['systemd', s.systemd == null ? '' : (s.systemd ? 'yes' : 'no')],
            ['Connected from', node.address], ['Agent', node.agent_version]]);
    }
    function policyBlock(node) {
        const p = node.policy || {};
        const warns = policyWarnings(p, node.policy_errors);
        const list = v => Array.isArray(v) ? (v.length ? v.join(', ') : 'none') : String(v ?? '');
        return `${Object.keys(p).length ? ui.kv([['Highest effect', p.max_effect], ['Allowed tools', list(p.allowed_tools)], ['Denied tools', list(p.denied_tools)],
            ['Categories', list(p.accept_categories)], ['Sandbox', p.sandbox], ['Canary', p.canary == null ? '' : (p.canary ? 'yes' : 'no')]]) : ''}
            ${warns.map(w => ui.banner(w.tone, w.tone === 'danger' ? '✗' : '◆', esc(w.text))).join('')}`;
    }

    /* ------------------------------------------------------------ the wizard */
    async function render(el, route, { isCurrent, setCrumbs, go }) {
        const tokenId = route.parts[0] || '';
        const q = route.query;
        const prefill = { labels: ui.parseLabels(q.get('labels') || ''), canary: q.get('canary') === '1', name: '' };
        const st = { net: null, setup: null, waiting: false, run: null, runId: '', verifyError: '', missingTool: false, labelsDirty: false, mode: 'oneliner', settings: null, index: null, peer: null };
        const last = recall();
        if (tokenId && last && last.token_id === tokenId) { st.runId = last.run_id || ''; st.waiting = !!last.waiting; }

        async function load() {
            const tasks = [api.get('network/interfaces')];
            if (tokenId) tasks.push(api.getSecure(`setup/${encodeURIComponent(tokenId)}`));
            const [net, setup] = await Promise.all(tasks);
            st.net = net;
            st.setup = setup || null;
            // Another browser (or cleared storage) resumes from the node's latest setup check.
            if (!st.runId && st.setup?.verify_run_id) st.runId = st.setup.verify_run_id;
            if (st.runId) { try { st.run = await api.get(`runs/${encodeURIComponent(st.runId)}`); } catch (_) { st.run = null; } }
        }
        await load();
        if (!isCurrent()) return null;
        setCrumbs([{ label: 'Nodes', to: '#/nodes' }, { label: 'Set up a node' }]);
        if (tokenId) remember({ token_id: tokenId, labels: st.setup?.labels || {}, canary: !!st.setup?.canary, run_id: st.runId, waiting: st.waiting, done: false });

        const endpointReady = () => !!(st.net?.endpoint?.running && st.net?.endpoint?.url);
        const roundTripOk = () => st.run?.state === 'succeeded';
        const step = () => stepFor({ endpointReady: endpointReady(), setup: st.setup, waiting: st.waiting, heartbeatOk: heartbeatOk(st.setup?.node), roundTripOk: roundTripOk() });
        let lastStep = 0;

        function paint() {
            const s = step();
            const moved = s !== lastStep && lastStep !== 0;
            lastStep = s;
            const resume = !tokenId && last && last.token_id && !last.done
                ? ui.banner('info', '●', 'You have a node setup in progress.', `<a class="w-btn" data-variant="tonal" data-tone="info" data-size="sm" href="#/setup/${encodeURIComponent(last.token_id)}">Resume it</a>`) : '';
            el.innerHTML = `
                <header class="w-page__head orch-head">
                    <div class="orch-head__title"><h1 class="w-page__title">Set up a node</h1>
                    </div>
                    <span class="orch-spacer"></span>
                    <div class="w-btn-row"><button type="button" class="w-btn" data-variant="ghost" data-quick>${icon('plus')}<span>Quick enrol</span></button></div>
                </header>
                <div class="orch-stack orch-setup">
                    ${resume}
                    ${stepper(s)}
                    <ul class="orch-setup__done-list" role="list">${doneRows(s)}</ul>
                    <div data-step aria-live="polite">${s === 7 ? finished() : current(s)}</div>
                </div>`;
            el.querySelector('[data-quick]').addEventListener('click', () => Orch.nodes?.enrol());
            el.querySelector('[data-change-network]')?.addEventListener('click', () => { st.forceNetwork = true; paint(); });
            bind();
            if (moved) el.querySelector('[data-step] h2')?.focus({ preventScroll: true });
        }

        function doneRows(s) {
            const ep = st.net?.endpoint || {};
            const rows = [];
            if (s > 1 && !st.forceNetwork) rows.push(doneRow(1, 'Master ready', `${esc(ep.bind)}:${esc(ep.port)} (${esc(ep.kind || 'private')})`, ' <button type="button" class="w-btn" data-variant="ghost" data-size="sm" data-change-network>Change</button>'));
            if (s > 2 && st.setup) rows.push(doneRow(2, 'Node described', `${esc(st.setup.name || 'unnamed')} · ${ui.labelChips(st.setup.labels, { empty: 'no labels' })}${st.setup.canary ? ' · canary' : ''}`));
            if (s > 4 && st.setup?.used_at) rows.push(doneRow(4, 'Enrolled', `${esc(st.setup.node?.name || st.setup.node_id)} ${ui.time(st.setup.used_at)}`));
            if (s > 5) rows.push(doneRow(5, 'Accepted', esc(st.setup.node?.name || '')));
            return rows.join('');
        }

        function current(s) {
            if (st.forceNetwork || s === 1) return stepMaster();
            if (s === 2) return stepDescribe();
            if (s === 3) return stepInstall();
            if (s === 4) return stepWait();
            if (s === 5) return stepAccept();
            return stepConfirm();
        }

        /* ---- 1. master readiness */
        function stepMaster() {
            const ep = st.net?.endpoint || {};
            const net = st.net?.network || {};
            const rows = st.net?.interfaces || [];
            const status = ep.running ? ui.chip({ semantic: 'ok', glyph: '✓', label: `Listening on ${ep.bind}:${ep.port}` }) : ep.error ? ui.chip({ semantic: 'danger', glyph: '✗', label: 'Not running' }) : ui.chip({ semantic: 'neutral', glyph: '○', label: 'Not configured' });
            return card(1, 'Master readiness', `
                <div class="orch-head__meta">${status}${ep.kind ? ui.meta(ep.kind) : ''}</div>
                ${ep.error ? `<p class="orch-error" role="alert">${esc(ep.error)}</p>` : ''}
                ${ep.fingerprint ? ui.kv([['TLS fingerprint', `<code class="orch-mono orch-break">${esc(ep.fingerprint)}</code>`, true]]) : ''}
                <p class="orch-muted">Nodes dial out to the master’s node endpoint. It binds to one private address of this machine; public and any-address binds are refused.</p>
                <form class="orch-stack" data-net-form novalidate>
                    <fieldset class="orch-fieldset"><legend>Address nodes connect to</legend>
                        <ul class="orch-setup__ifaces" role="list">${rows.map((r, i) => `<li><label class="w-check orch-setup__iface${r.ok ? '' : ' orch-setup__iface--off'}"><input class="w-check__input" type="radio" name="orch-setup-bind" value="${esc(r.address)}"${r.ok ? '' : ' disabled'}${(net.bind || '') === r.address ? ' checked' : ''} data-i="${i}"><span><span class="orch-mono">${esc(r.address)}</span> <span class="orch-sub">${esc(r.interface || '')} · ${esc(r.kind)} — ${esc(r.reason)}</span></span></label></li>`).join('') || '<li class="orch-muted">No addresses found.</li>'}</ul></fieldset>
                    <div class="orch-form-grid">
                        <div class="w-field"><label class="w-field__label" for="orch-setup-port">Port</label><input class="w-input" type="number" min="1024" max="65535" id="orch-setup-port" value="${esc(net.port || 30312)}"></div>
                        <div class="w-field"><label class="w-field__label" for="orch-setup-adv">Advertised address <span class="orch-muted">(optional)</span></label><input class="w-input" data-mono id="orch-setup-adv" value="${esc(net.advertise || '')}" placeholder="master.internal" autocomplete="off"></div>
                    </div>
                    <p class="orch-muted">Node on another network? Set up the <a href="#/settings?section=overlay">WireGuard overlay</a> first, then bind to its address.</p>
                    ${err('')}
                    <div class="w-btn-row orch-form-actions">${st.forceNetwork && endpointReady() ? '<button type="button" class="w-btn" data-variant="ghost" data-keep-network>Keep the current address</button>' : ''}<button type="submit" class="w-btn" data-variant="primary">Save and start the endpoint</button></div>
                </form>`, { state: ep.error ? 'danger' : '' });
        }

        /* ---- 2. describe */
        function labelRows(labels) {
            const entries = Object.entries(labels);
            if (!entries.length) entries.push(['', '']);
            return entries.map(([k, v], i) => `<li class="orch-setup__label-row" data-label-row>
                <input class="w-input" data-mono data-lk aria-label="Label key ${i + 1}" value="${esc(k)}" list="orch-setup-keys" placeholder="env" autocomplete="off" spellcheck="false">
                <span aria-hidden="true">=</span>
                <input class="w-input" data-mono data-lv aria-label="Label value ${i + 1}" value="${esc(v)}" list="orch-setup-vals-${i}" placeholder="prod" autocomplete="off" spellcheck="false"><datalist id="orch-setup-vals-${i}" data-vals></datalist>
                <button type="button" class="w-btn" data-variant="icon" data-size="sm" data-label-del aria-label="Remove label ${i + 1}" title="Remove">${icon('x', 'sm')}</button></li>`).join('');
        }
        function stepDescribe() {
            const keys = Object.entries((st.index || {}).labels || {});
            const overlay = st.settings?.overlay?.subnet;
            const draft = st.describe || { name: prefill.name, labels: prefill.labels, canary: prefill.canary, ttl: 3600 };
            return card(2, 'Describe the node', `
                <form class="orch-stack" data-describe novalidate>
                    <div class="w-field"><label class="w-field__label" for="orch-setup-name">Node name</label><input class="w-input" id="orch-setup-name" value="${esc(draft.name)}" placeholder="web-01" autocomplete="off" data-autofocus><p class="w-field__hint">Leave empty to use the machine’s host name.</p></div>
                    <fieldset class="orch-fieldset"><legend>Labels</legend>
                        <ul class="orch-setup__labels" role="list" data-labels>${labelRows(draft.labels)}</ul>
                        <datalist id="orch-setup-keys">${keys.map(([k, vals]) => `<option value="${esc(k)}" label="${esc(`${k} (${Object.values(vals).reduce((a, b) => a + b, 0)} nodes)`)}"></option>`).join('')}</datalist>
                        <div class="w-btn-row"><button type="button" class="w-btn" data-variant="ghost" data-size="sm" data-label-add>${icon('plus', 'sm')}<span>Add label</span></button></div>
                        <p class="w-field__hint">Runs and access rules target nodes by label. Suggestions are the labels already on your nodes.</p></fieldset>
                    <label class="w-check"><input class="w-check__input" type="checkbox" id="orch-setup-canary"${draft.canary ? ' checked' : ''}><span>Canary node (tests and first batches run here; also set <code>canary: true</code> in its policy.yaml)</span></label>
                    <div class="w-field"><label class="w-field__label" for="orch-setup-ttl">Join token lifetime</label><span class="w-select"><select class="w-select__control" id="orch-setup-ttl">${LIFETIMES.map(([s, l]) => `<option value="${s}"${Number(draft.ttl) === s ? ' selected' : ''}>${esc(l)}</option>`).join('')}</select></span></div>
                    ${overlay ? `<details class="orch-details"><summary>WireGuard peer for this node (optional)</summary><div class="orch-stack-sm">
                        <p class="orch-muted">For a node outside the master’s network: install this <code>wg-quick</code> config on the node before the agent.</p>
                        <div class="w-btn-row"><button type="button" class="w-btn" data-variant="secondary" data-size="sm" data-peer>${icon('network', 'sm')}<span>Create peer config</span></button></div>
                        <div data-peer-out>${st.peer ? peerHtml(st.peer) : ''}</div></div></details>` : ''}
                    ${err('')}
                    <div class="w-btn-row orch-form-actions"><a class="w-btn" data-variant="ghost" href="#/nodes">Cancel</a><button type="submit" class="w-btn" data-variant="primary">${icon('plus')}<span>Create join token</span></button></div>
                </form>`);
        }
        const peerHtml = p => `${ui.banner('attention', '◆', '<strong>Copy this now.</strong> It holds the peer’s private key, which the master doesn’t keep and won’t show again.')}${ui.code(p.node_config, { lang: 'ini', title: `${p.peer?.name || 'peer'}.conf` })}`;

        /* ---- 3. install */
        function stepInstall() {
            const s = st.setup;
            const url = s.master_url;
            const host = (() => { try { return new URL(url).hostname.replace(/^\[|\]$/g, ''); } catch (_) { return ''; } })();
            const prereq = `<details class="orch-details" open><summary>Before you start: what the node needs</summary>
                <ul class="orch-list"><li>Linux with systemd</li><li>Python 3.10 or newer (<code>python3 --version</code>)</li><li>root or <code>sudo</code></li><li>It can reach <code class="orch-mono">${esc(host || 'the master')}:${esc(st.net?.endpoint?.port || 30312)}</code>. Test from the node:</li></ul>
                ${url ? ui.code(reachCommand(url), { lang: 'bash', title: 'Reachability check', cls: 'orch-code--wrap' }) : ''}</details>`;
            if (!s.available) {
                const why = s.expired ? 'This join token expired.' : 'This page can’t hand out this token any more (the GUI restarted, or it was created elsewhere).';
                return card(3, 'Get the agent onto the node', `${ui.banner('caution', '◆', esc(why) + ' Create a new token with the same labels to continue.', '<button type="button" class="w-btn" data-variant="tonal" data-tone="caution" data-size="sm" data-new-token>Create a new token</button>')}${prereq}`);
            }
            const modes = [['oneliner', 'One-liner'], ['download', 'Download'], ['manual', 'Manual']];
            let body = '';
            if (st.mode === 'oneliner') {
                body = `<p>Run this on the node as a user with sudo. It checks the installer’s SHA-256; the installer checks the agent’s and pins this master’s TLS fingerprint.</p>${ui.code(s.command, { lang: 'bash', title: 'Install command', cls: 'orch-code--wrap' })}`;
            } else if (st.mode === 'download') {
                body = `<p>For a node that can’t reach the master over HTTPS before enrolment, or to review the files first: download both, copy them to the node and run the installer next to the agent.</p>
                    <div class="orch-setup__downloads">
                        <div class="orch-setup__download"><button type="button" class="w-btn" data-variant="secondary" data-dl="agent.pyz">${icon('download')}<span>mucli-node.pyz</span></button><code class="orch-mono orch-break orch-sub">sha256 ${esc(s.pyz_sha256)}</code><span data-dl-result="agent.pyz" aria-live="polite"></span></div>
                        <div class="orch-setup__download"><button type="button" class="w-btn" data-variant="secondary" data-dl="install.sh">${icon('download')}<span>install.sh</span></button><code class="orch-mono orch-break orch-sub">sha256 ${esc(s.install_sha256 || '')}</code><span data-dl-result="install.sh" aria-live="polite"></span></div>
                    </div>
                    ${ui.banner('caution', '◆', 'install.sh contains the live join token. Keep it private and delete it after enrolment.')}
                    ${ui.code(`scp mucli-node.pyz install.sh ${s.name || 'NODE'}:\nssh ${s.name || 'NODE'} 'sha256sum mucli-node.pyz install.sh && sudo bash install.sh'`, { lang: 'bash', title: 'Copy and run', cls: 'orch-code--wrap' })}`;
            } else {
                body = `<p>The installer’s steps one by one. Copy <code>mucli-node.pyz</code> to the node first (Download tab).</p>${(s.manual || []).map((m, i) => ui.code(m.command, { lang: 'bash', title: `${i + 1}. ${m.title}`, cls: 'orch-code--wrap' })).join('')}`;
            }
            return card(3, 'Get the agent onto the node', `
                <div class="w-segmented" role="group" aria-label="How to install">${modes.map(([k, l]) => `<button type="button" class="w-segmented__option" aria-pressed="${st.mode === k}" data-mode="${k}">${esc(l)}</button>`).join('')}</div>
                <div class="orch-stack-sm">${body}</div>
                ${ui.kv([['Token expires', ui.when(s.expires_at)], ['Labels', ui.labelChips(s.labels, { empty: 'none' }), true], ['Master fingerprint', `<code class="orch-mono orch-break">${esc(s.fingerprint || '')}</code>`, true]])}
                ${prereq}`, { foot: '<button type="button" class="w-btn" data-variant="primary" data-wait>I ran it: wait for the node <span class="w-btn__chevron" aria-hidden="true">›</span></button>' });
        }

        /* ---- 4. wait */
        function stepWait() {
            const s = st.setup;
            const age = Date.now() / 1000 - Number(s.created_at || 0);
            const hints = [];
            if (s.expired) hints.push(ui.banner('danger', '✗', 'The join token expired before the node used it.', '<button type="button" class="w-btn" data-variant="tonal" data-tone="danger" data-size="sm" data-new-token>Create a new token</button>'));
            else if (age > QUIET_AFTER_S) hints.push(ui.banner('caution', '◆', `Nothing yet after ${Math.round(age / 60)} minutes. Check that the node reaches the master (below), that the installer finished without errors, and <code>journalctl -u mucli-node</code> on the node.`));
            return card(4, 'Waiting for the node', `
                <p class="orch-setup__waiting"><span class="w-inkdrop" data-tone="running" aria-hidden="true"></span><span>Waiting for the node to enrol with this token. This page updates by itself.</span></p>
                ${hints.join('')}
                <details class="orch-details"><summary>Troubleshooting</summary><ul class="orch-list">
                    <li>“fingerprint mismatch”: the node reached something other than this master (a proxy, or an old address). Check the address and that the endpoint’s certificate hasn’t changed; then run the install command again.</li>
                    <li>“join token expired” or “already used”: create a new token here.</li>
                    <li>Connection refused or timed out: the node can’t reach <code class="orch-mono">${esc(s.master_url)}</code>. Test with:</li></ul>
                    ${ui.code(reachCommand(s.master_url), { lang: 'bash', title: 'Reachability check', cls: 'orch-code--wrap' })}</details>`,
            { foot: '<button type="button" class="w-btn" data-variant="ghost" data-back-install>‹ Back to the install instructions</button>' });
        }

        /* ---- 5. accept */
        function stepAccept() {
            const node = st.setup.node;
            if (!node) return card(5, 'Accept the node', ui.loading('Loading the node…'));
            const pending = node.status === 'pending';
            return card(5, 'Accept the node', `
                <p>${esc(node.name)} enrolled ${ui.time(st.setup.used_at)}. Check that this is the machine you meant before accepting it.</p>
                ${!pending ? ui.banner(node.status === 'revoked' ? 'danger' : 'caution', '◆', `This node is ${esc(node.status)}.`) : ''}
                <div class="orch-grid-2">
                    <div><h3 class="orch-subhead">Facts</h3>${factsKv(node)}</div>
                    <div><h3 class="orch-subhead">Local policy</h3>${policyBlock(node)}</div>
                </div>
                <form class="orch-stack-sm" data-labels-form novalidate>
                    <div class="w-field"><label class="w-field__label" for="orch-setup-node-labels">Labels</label><input class="w-input" data-mono id="orch-setup-node-labels" value="${esc(ui.formatLabels(node.labels))}" autocomplete="off" spellcheck="false"><p class="w-field__hint">Comma-separated key=value pairs.</p></div>
                    <label class="w-check"><input class="w-check__input" type="checkbox" id="orch-setup-node-canary"${node.canary ? ' checked' : ''}><span>Canary node</span></label>
                    ${err('')}
                </form>`, {
                state: 'attention',
                foot: pending ? '<button type="button" class="w-btn" data-variant="secondary" data-reject>Revoke</button><button type="button" class="w-btn" data-variant="primary" data-accept>' + icon('check') + '<span>Accept node</span></button>' : `<a class="w-btn" data-variant="secondary" href="#/node/${encodeURIComponent(node.id)}">Open the node</a>`,
            });
        }

        /* ---- 6. confirm */
        function stepConfirm() {
            const node = st.setup.node;
            const hb = heartbeatOk(node);
            const run = st.run;
            const ex = mainExec(run);
            let trip = '';
            if (st.verifyError) {
                trip = ui.banner('danger', '✗', esc(st.verifyError), st.missingTool ? '<button type="button" class="w-btn" data-variant="tonal" data-tone="danger" data-size="sm" data-starter>Install the starter pack</button>' : '');
            } else if (!run) {
                trip = '<p class="orch-muted">Runs the built-in read-only <code>sys.facts</code> on just this node, planned, policy-checked, signed and audited like any other run.</p>';
            } else if (run.state === 'proposed') {
                trip = ui.banner('attention', '◆', 'The setup check is waiting for approval under your access policy.', `<a class="w-btn" data-variant="tonal" data-tone="attention" data-size="sm" href="#/run/${encodeURIComponent(run.id)}">Review the run</a>`);
            } else if (run.state === 'succeeded') {
                trip = `<p>${ui.chip({ semantic: 'ok', glyph: '✓', label: `Ran in ${ui.dur(ex?.duration_s)}` })} <a href="#/run/${encodeURIComponent(run.id)}">Run ${esc(run.id)}</a></p><details class="orch-details"><summary>Facts it returned</summary>${ui.outputView(ex?.output)}</details>`;
            } else if (TERMINAL.includes(run.state)) {
                const reason = ex?.reason || run.reason || run.state;
                trip = `${ui.banner('danger', '✗', `<strong>The setup check ${esc(run.state)}:</strong> ${esc(reason)}`)}<p>${esc(failureHint(reason))}</p><p><a href="#/run/${encodeURIComponent(run.id)}">Open the run</a></p>`;
            } else {
                trip = `<p class="orch-setup__waiting"><span class="w-inkdrop" data-tone="running" aria-hidden="true"></span><span>Running on the node (${esc(run.style?.label || run.state)})…</span></p>`;
            }
            const busyRun = run && !TERMINAL.includes(run.state) && run.state !== 'proposed';
            return card(6, 'Confirm it is alive', `
                <ul class="orch-setup__checks" role="list">
                    ${check(hb, 'Heartbeat', node ? `last seen ${ui.time(node.last_seen)}${node.agent_version ? ` · agent ${esc(node.agent_version)}` : ''}` : '', !hb)}
                    ${check(roundTripOk(), 'Round trip: sys.facts', '', busyRun)}
                </ul>
                ${!hb ? '<p class="orch-muted">Waiting for a poll from the node (every few seconds). If this stays empty, check <code>systemctl status mucli-node</code> on the node.</p>' : ''}
                ${trip}`, {
                foot: `${!hb ? '<button type="button" class="w-btn" data-variant="ghost" data-refresh>Check again</button>' : ''}${busyRun ? '' : `<button type="button" class="w-btn" data-variant="${run ? 'secondary' : 'primary'}" data-verify>${icon('play')}<span>${run ? 'Run the check again' : 'Run setup check'}</span></button>`}`,
            });
        }

        function finished() {
            const node = st.setup.node;
            remember({ token_id: tokenId, labels: st.setup.labels, canary: st.setup.canary, done: true });
            const again = `#/setup?labels=${encodeURIComponent(ui.formatLabels(st.setup.labels).replace(/, /g, ','))}${st.setup.canary ? '&canary=1' : ''}`;
            return `<article class="w-card orch-card" data-state="ok" aria-labelledby="orch-setup-fin">
                <header class="w-card__head">${ui.chip({ semantic: 'ok', glyph: '✓', label: 'Done' })}<h2 class="w-card__title" id="orch-setup-fin" tabindex="-1">${esc(node.name)} is set up</h2></header>
                <div class="w-card__body"><ul class="orch-setup__checks" role="list">${check(true, 'Heartbeat', `last seen ${ui.time(node.last_seen)}`)}${check(true, 'Round trip: sys.facts', `ran in ${esc(ui.dur(mainExec(st.run)?.duration_s))}`)}</ul></div>
                <footer class="w-card__foot"><a class="w-btn" data-variant="secondary" href="${esc(again)}">${icon('plus')}<span>Set up another</span></a><a class="w-btn" data-variant="primary" href="#/node/${encodeURIComponent(node.id)}">Open ${esc(node.name)} <span class="w-btn__chevron" aria-hidden="true">›</span></a></footer></article>`;
        }

        /* ------------------------------------------------------------ actions */
        const showErr = (form, e) => { const p = form?.querySelector('[data-error]'); if (p) p.innerHTML = `<span aria-hidden="true">✗</span> ${esc(e.message || e)}`; };
        async function refresh() {
            try { await load(); } catch (e) { ui.toast(`Couldn’t refresh: ${e.message}`, 'danger'); }
            if (isCurrent()) paint();
        }
        async function createToken(button, args) {
            const res = await ui.busy(button, () => api.cmd('createJoinToken', args));
            const tid = res.data.token_id;
            remember({ token_id: tid, labels: args.labels, canary: args.canary, done: false });
            go(`#/setup/${encodeURIComponent(tid)}`);
        }
        function readLabels(root) {
            const out = {};
            root.querySelectorAll('[data-label-row]').forEach(r => { const k = r.querySelector('[data-lk]').value.trim(); const v = r.querySelector('[data-lv]').value.trim(); if (k) out[k] = v; });
            return out;
        }
        function fillValues(row) {
            const k = row.querySelector('[data-lk]').value.trim();
            const vals = Object.entries(((st.index || {}).labels || {})[k] || {});
            row.querySelector('[data-vals]').innerHTML = vals.map(([v, n]) => `<option value="${esc(v)}" label="${esc(`${v} (${n})`)}"></option>`).join('');
        }

        function bind() {
            const netForm = el.querySelector('[data-net-form]');
            netForm?.addEventListener('submit', async e => {
                e.preventDefault();
                const bindAddr = netForm.querySelector('input[name="orch-setup-bind"]:checked')?.value || '';
                if (!bindAddr) { showErr(netForm, 'Choose an address.'); return; }
                try {
                    await ui.busy(netForm.querySelector('[type=submit]'), () => api.cmd('setNetwork', { bind: bindAddr, port: Number(netForm.querySelector('#orch-setup-port').value || 30312), advertise: netForm.querySelector('#orch-setup-adv').value.trim() }));
                    ui.toast('Node endpoint saved', 'ok');
                    st.forceNetwork = false;
                    await refresh();
                } catch (e2) { showErr(netForm, e2); }
            });
            el.querySelector('[data-keep-network]')?.addEventListener('click', () => { st.forceNetwork = false; paint(); });

            const desc = el.querySelector('[data-describe]');
            if (desc) {
                const list = desc.querySelector('[data-labels]');
                const save = () => { st.describe = { name: desc.querySelector('#orch-setup-name').value, labels: readLabels(desc), canary: desc.querySelector('#orch-setup-canary').checked, ttl: desc.querySelector('#orch-setup-ttl').value }; };
                list.querySelectorAll('[data-label-row]').forEach(fillValues);
                list.addEventListener('input', e => { if (e.target.matches('[data-lk]')) fillValues(e.target.closest('[data-label-row]')); save(); });
                desc.addEventListener('change', save);
                list.addEventListener('click', e => {
                    const del = e.target.closest('[data-label-del]');
                    if (!del) return;
                    save();
                    const k = del.closest('[data-label-row]').querySelector('[data-lk]').value.trim();
                    delete st.describe.labels[k];
                    del.closest('[data-label-row]').remove();
                    if (!list.children.length) { list.innerHTML = labelRows({}); list.querySelectorAll('[data-label-row]').forEach(fillValues); }
                    list.querySelector('[data-lk]')?.focus();
                });
                desc.querySelector('[data-label-add]').addEventListener('click', () => {
                    save();
                    const tmp = document.createElement('ul');
                    tmp.innerHTML = labelRows({ '': '' }).replace(/orch-setup-vals-0/g, `orch-setup-vals-${list.children.length}`).replace(/Label (key|value) 1/g, `Label $1 ${list.children.length + 1}`);
                    const row = tmp.firstElementChild;
                    list.append(row);
                    row.querySelector('[data-lk]').focus();
                });
                desc.querySelector('[data-peer]')?.addEventListener('click', async e => {
                    const name = (desc.querySelector('#orch-setup-name').value.trim() || 'node').replace(/[^A-Za-z0-9_.-]/g, '-');
                    try {
                        const res = await ui.command('overlayAddPeer', { name }, { ok: 'Peer config created', button: e.currentTarget });
                        st.peer = res.data;
                        desc.querySelector('[data-peer-out]').innerHTML = peerHtml(st.peer);
                    } catch (_) { /* toasted */ }
                });
                desc.addEventListener('submit', async e => {
                    e.preventDefault();
                    save();
                    const d = st.describe;
                    const bad = Object.keys(d.labels).find(k => !/^[A-Za-z0-9][A-Za-z0-9_./-]{0,62}$/.test(k));
                    if (bad) { showErr(desc, `“${bad}” isn’t a valid label key (letters, digits, . _ / -).`); return; }
                    try { await createToken(desc.querySelector('[type=submit]'), { labels: d.labels, canary: d.canary, ttl_s: Number(d.ttl), name: d.name.trim(), note: 'setup wizard' }); }
                    catch (e2) { showErr(desc, e2); }
                });
            }

            el.querySelectorAll('[data-mode]').forEach(b => b.addEventListener('click', () => { st.mode = b.dataset.mode; paint(); el.querySelector(`[data-mode="${st.mode}"]`)?.focus(); }));
            el.querySelectorAll('[data-dl]').forEach(b => b.addEventListener('click', async () => {
                const name = b.dataset.dl;
                const out = el.querySelector(`[data-dl-result="${name}"]`);
                const expect = name === 'agent.pyz' ? st.setup.pyz_sha256 : st.setup.install_sha256;
                try {
                    const r = await ui.busy(b, () => api.download(`setup/${encodeURIComponent(tokenId)}/${name}`, name === 'agent.pyz' ? 'mucli-node.pyz' : 'install.sh'));
                    const ok = r.sha256 && r.sha256 === expect;
                    out.innerHTML = ok ? ui.chip({ semantic: 'ok', glyph: '✓', label: 'Downloaded, digest matches' }) : ui.chip({ semantic: 'danger', glyph: '✗', label: 'Digest mismatch: don’t use this file' });
                } catch (e) {
                    out.innerHTML = `<span class="orch-error">${esc(e.message)}</span>`;
                    if (e.status === 410) refresh();
                }
            }));
            el.querySelectorAll('[data-new-token]').forEach(b => b.addEventListener('click', async () => {
                try { await createToken(b, { labels: st.setup.labels || {}, canary: !!st.setup.canary, name: st.setup.name || '', note: 'setup wizard' }); }
                catch (e) { ui.toast(e.message, 'danger'); }
            }));
            el.querySelector('[data-wait]')?.addEventListener('click', () => { st.waiting = true; remember({ ...(recall() || {}), token_id: tokenId, waiting: true }); paint(); });
            el.querySelector('[data-back-install]')?.addEventListener('click', () => { st.waiting = false; remember({ ...(recall() || {}), token_id: tokenId, waiting: false }); paint(); });

            const lf = el.querySelector('[data-labels-form]');
            lf?.addEventListener('input', () => { st.labelsDirty = true; });
            el.querySelector('[data-accept]')?.addEventListener('click', async e => {
                const node = st.setup.node;
                const labels = ui.parseLabels(lf.querySelector('#orch-setup-node-labels').value);
                const canary = lf.querySelector('#orch-setup-node-canary').checked;
                try {
                    await ui.busy(e.currentTarget, async () => {
                        if (JSON.stringify(labels) !== JSON.stringify(node.labels || {})) await api.cmd('setNodeLabels', { node_id: node.id, labels });
                        if (canary !== !!node.canary) await api.cmd('setNodeCanary', { node_id: node.id, canary });
                        await api.cmd('acceptNode', { node_id: node.id });
                    });
                    st.labelsDirty = false;
                    ui.toast(`${node.name} accepted`, 'ok');
                    await refresh();
                } catch (e2) { showErr(lf, e2); }
            });
            el.querySelector('[data-reject]')?.addEventListener('click', async e => {
                const node = st.setup.node;
                if (!await ui.confirm({ title: `Revoke ${node.name}?`, message: 'Its credential is deleted. It must enrol again with a new join token.', confirmLabel: 'Revoke', tone: 'danger' })) return;
                try { await ui.command('revokeNode', { node_id: node.id }, { ok: 'Node revoked', button: e.currentTarget }); await refresh(); } catch (_) { /* toasted */ }
            });

            el.querySelector('[data-refresh]')?.addEventListener('click', refresh);
            el.querySelector('[data-verify]')?.addEventListener('click', async e => {
                st.verifyError = ''; st.missingTool = false;
                try {
                    const res = await ui.busy(e.currentTarget, () => api.cmd('verifyNode', { node_id: st.setup.node.id }));
                    st.runId = res.data.run.id;
                    remember({ ...(recall() || {}), token_id: tokenId, run_id: st.runId });
                } catch (e2) {
                    st.verifyError = e2.message;
                    st.missingTool = !!(e2.data && e2.data.missing_tool) || /starter pack/i.test(e2.message);
                }
                await refresh();
            });
            el.querySelector('[data-starter]')?.addEventListener('click', async e => {
                try {
                    await ui.command('installStarterPack', {}, { ok: 'Starter pack installed', button: e.currentTarget });
                    st.verifyError = ''; st.missingTool = false;
                    paint();
                    el.querySelector('[data-verify]')?.click();
                } catch (_) { /* toasted */ }
            });
        }

        if (step() === 2 || st.forceNetwork) {
            try { [st.index, st.settings] = await Promise.all([window.OrchPickers ? window.OrchPickers.labels() : api.get('labels'), api.get('settings')]); } catch (_) { /* suggestions are optional */ }
            if (!isCurrent()) return null;
        }
        paint();

        // Live: the change stream drives the wizard; a slow timer covers missed heartbeats.
        const refreshSoon = ui.debounce(refresh, 300);
        const timer = setInterval(() => { const s = step(); if (s >= 3 && s <= 6 && !st.labelsDirty) refresh(); }, 30000);
        return {
            onChange(change) {
                const s = step();
                if (!tokenId || s < 3 || s === 7) return;
                if (s === 5 && st.labelsDirty) return;
                if (change.scope === 'node' || (change.scope === 'run' && st.runId && (!change.id || change.id === st.runId))) refreshSoon();
            },
            dispose() { clearInterval(timer); },
        };
    }

    Orch.setup = { start: () => (Orch.go ? Orch.go('#/setup') : (location.hash = '#/setup')), stepFor, policyWarnings, failureHint, reachCommand, heartbeatOk, mainExec };
    Orch.routes.setup = { nav: 'nodes', title: 'Set up a node', scopes: [], render, crumbs: () => [{ label: 'Nodes', to: '#/nodes' }, { label: 'Set up a node' }] };
})();
