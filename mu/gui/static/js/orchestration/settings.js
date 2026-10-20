/* Orchestration — Settings (network, WireGuard overlay, access policy,
 * signing and trusted keys, secrets, rollout defaults, schedules,
 * import/export, starter pack) and the Audit log
 * (documentation/plans/orchestration.md §3.5, §3.6, §6, §7, §8).
 */
(() => {
    'use strict';
    const api = window.OrchAPI;
    const ui = window.OrchUI;
    const { esc, icon } = ui;
    const Orch = (window.Orch = window.Orch || { routes: {} });
    Orch.routes = Orch.routes || {};

    const SECTIONS = [['network', 'Network'], ['overlay', 'Overlay'], ['access', 'Access policy'], ['keys', 'Keys'], ['secrets', 'Secrets'],
        ['defaults', 'Rollout defaults'], ['agents', 'Node agents'], ['schedules', 'Schedules'], ['bundles', 'Import and export'], ['starter', 'Starter pack']];
    const err = (form, e) => { const p = form.querySelector('[data-error]'); if (p) p.innerHTML = `<span aria-hidden="true">✗</span> ${esc(e.message || e)}`; };
    const clearErr = form => { const p = form.querySelector('[data-error]'); if (p) p.textContent = ''; };
    const copyBtn = (value, label) => `<button type="button" class="w-btn" data-variant="icon" data-size="sm" data-orch-copy data-copy-value="${esc(value)}" aria-label="Copy ${esc(label)}" title="Copy ${esc(label)}">${icon('copy', 'sm')}</button>`;
    /* A settings section is a form section (Mu §10.6): title, lede and section tools on the
     * left (sticky from 1200px), the fields on the right. */
    const section = (id, title, lede, body, tools = '') => `<section class="w-formsection orch-settings__section" id="orch-set-${id}" aria-labelledby="orch-set-${id}-h">
        <header class="w-formsection__head"><h2 class="w-formsection__title" id="orch-set-${id}-h">${esc(title)}</h2>${lede ? `<p class="w-formsection__lede">${lede}</p>` : ''}${tools ? `<div class="orch-formsection__tools">${tools}</div>` : ''}</header>
        <div class="w-formsection__body">${body}</div></section>`;

    /* The access policy editor shows {rules: [...]}; saveAccessPolicy wants the list. */
    function rulesListYaml(text) {
        const m = /^\s*rules:\s*(?:\n|$)/.exec(text);
        if (!m) return text;
        const rest = text.slice(m[0].length).split('\n');
        const indents = rest.filter(l => l.trim()).map(l => l.match(/^ */)[0].length);
        const min = indents.length ? Math.min(...indents) : 0;
        return rest.map(l => l.slice(Math.min(min, l.match(/^ */)[0].length))).join('\n');
    }

    function scheduleSpecText(s) {
        if (s.kind === 'cron') return `cron ${s.spec?.cron || ''}`;
        if (s.kind === 'interval') return `every ${ui.dur(s.spec?.every_s)}`;
        if (s.kind === 'trigger') return `when ${s.spec?.metric} ${s.spec?.op} ${s.spec?.value ?? ''}${s.spec?.cooldown_s ? ` (cooldown ${ui.dur(s.spec.cooldown_s)})` : ''}`;
        return JSON.stringify(s.spec || {});
    }

    async function scheduleDialog(existing, done) {
        const P = window.OrchPickers;
        const s = existing || { name: '', kind: 'cron', spec: { cron: '0 3 * * *' }, target: { kind: 'tool', target: '', params: {}, selector: { labels: '' }, strategy: {}, check_mode: false }, enabled: true };
        const id = ui.uid('sched');
        let tkind = s.target?.kind === 'runbook' ? 'runbook' : 'tool';
        const form = document.createElement('form');
        form.className = 'orch-stack';
        form.noValidate = true;
        form.innerHTML = `
            <div class="orch-form-grid">
                <div class="w-field"><label class="w-field__label" for="${id}-name">Name</label><input class="w-input" id="${id}-name" value="${esc(s.name)}" autocomplete="off" data-autofocus></div>
                <div class="w-field"><label class="w-field__label" for="${id}-kind">Kind</label><span class="w-select"><select class="w-select__control" id="${id}-kind">${['cron', 'interval', 'trigger'].map(k => `<option value="${k}"${s.kind === k ? ' selected' : ''}>${k}</option>`).join('')}</select></span></div>
            </div>
            <div data-spec="cron" class="w-field"><label class="w-field__label" for="${id}-cron">Cron expression</label><input class="w-input" data-mono id="${id}-cron" value="${esc(s.spec?.cron || '')}" placeholder="0 3 * * *"><p class="w-field__hint">minute hour day month weekday, in the master’s local time.</p></div>
            <div data-spec="interval" class="w-field"><label class="w-field__label" for="${id}-every">Every (seconds)</label><input class="w-input" type="number" min="60" id="${id}-every" value="${esc(s.spec?.every_s || 3600)}"></div>
            <div data-spec="trigger" class="orch-form-grid">
                <div class="w-field"><label class="w-field__label" for="${id}-metric">Metric</label><span class="w-select"><select class="w-select__control" id="${id}-metric">${['max_disk_used_pct', 'memory_used_pct', 'load', 'uptime_days', 'failed_units'].map(m => `<option value="${m}"${s.spec?.metric === m ? ' selected' : ''}>${m}</option>`).join('')}</select></span></div>
                <div class="w-field"><label class="w-field__label" for="${id}-op">Comparison</label><span class="w-select"><select class="w-select__control" id="${id}-op">${['>', '>=', '<', '<=', '==', '!=', 'changed'].map(o => `<option value="${esc(o)}"${s.spec?.op === o ? ' selected' : ''}>${esc(o)}</option>`).join('')}</select></span></div>
                <div class="w-field"><label class="w-field__label" for="${id}-value">Value</label><input class="w-input" id="${id}-value" value="${esc(s.spec?.value ?? '')}"></div>
                <div class="w-field"><label class="w-field__label" for="${id}-cool">Cooldown (seconds)</label><input class="w-input" type="number" min="0" id="${id}-cool" value="${esc(s.spec?.cooldown_s ?? 3600)}"></div>
            </div>
            <div class="w-segmented" role="group" aria-label="Run a">
                <button type="button" class="w-segmented__option" aria-pressed="${tkind === 'tool'}" data-tkind="tool">Tool</button>
                <button type="button" class="w-segmented__option" aria-pressed="${tkind === 'runbook'}" data-tkind="runbook">Runbook</button>
            </div>
            <div data-target></div>
            <fieldset class="orch-fieldset"><legend>Parameters</legend><div data-params></div></fieldset>
            <div data-where></div>
            <p class="w-field__hint" data-trigger-hint>Triggers may leave the target empty: they run on the node whose metric fired.</p>
            <label class="w-check"><input class="w-check__input" type="checkbox" id="${id}-check"${s.target?.check_mode ? ' checked' : ''}><span>Check mode</span></label>
            <label class="w-switch"><span class="w-switch__label">Enabled</span><input class="w-switch__input" type="checkbox" role="switch" id="${id}-en"${s.enabled ? ' checked' : ''}></label>
            <p class="orch-muted">Scheduled runs go through the same access policy as any other run; ones that need confirmation wait in Approvals.</p>
            <p class="w-field__error" data-error role="alert"></p>
            <div class="w-btn-row orch-dialog__actions"><button type="button" class="w-btn" data-variant="secondary" data-cancel>Cancel</button><button type="submit" class="w-btn" data-variant="primary">Save schedule</button></div>`;
        const d = ui.dialog({ title: existing ? `Edit schedule ${s.name}` : 'New schedule', body: form, size: 'lg' });
        const kindSel = form.querySelector(`#${id}-kind`);
        const showSpec = () => {
            form.querySelectorAll('[data-spec]').forEach(p => { p.hidden = p.dataset.spec !== kindSel.value; });
            form.querySelector('[data-trigger-hint]').hidden = kindSel.value !== 'trigger';
        };
        kindSel.addEventListener('change', showSpec);
        showSpec();
        let params = null;
        let picker = null;
        let shownRef = '';
        let firstValues = s.target?.params || {};
        const targetChanged = (ref, version) => {
            const schema = version ? version.inputs || {} : {};
            const box = form.querySelector('[data-params]');
            if (!params) params = P.paramsForm(box, schema, { values: firstValues, emptyText: ref ? 'This version takes no parameters.' : 'Choose a tool or runbook first.' });
            else if (ref !== shownRef) params.rebuild(schema);
            firstValues = {};
            shownRef = ref;
        };
        const mountTarget = value => {
            params = null;
            shownRef = '';
            picker = P.combobox(form.querySelector('[data-target]'), { purpose: 'schedule', kinds: [tkind], value, label: tkind === 'tool' ? 'Tool' : 'Runbook', onChange: targetChanged });
            picker.ready.then(() => targetChanged(picker.value(), picker.version()));
        };
        mountTarget(s.target?.target || '');
        const where = P.selectorBuilder(form.querySelector('[data-where]'), { value: s.target?.selector || {}, label: 'Target nodes', allowAll: true });
        form.querySelectorAll('[data-tkind]').forEach(b => b.addEventListener('click', () => {
            if (tkind === b.dataset.tkind) return;
            tkind = b.dataset.tkind;
            form.querySelectorAll('[data-tkind]').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
            mountTarget('');
        }));
        form.querySelector('[data-cancel]').onclick = () => d.close();
        form.onsubmit = async e => {
            e.preventDefault();
            clearErr(form);
            const kind = kindSel.value;
            const read = params ? params.read() : { values: {}, errors: [] };
            if (read.errors.length) { err(form, read.errors.join('; ')); return; }
            if (!picker?.value()) { err(form, `Choose a ${tkind}.`); return; }
            const v = form.querySelector(`#${id}-value`).value.trim();
            const spec = kind === 'cron' ? { cron: form.querySelector(`#${id}-cron`).value.trim() }
                : kind === 'interval' ? { every_s: Number(form.querySelector(`#${id}-every`).value) }
                : { metric: form.querySelector(`#${id}-metric`).value, op: form.querySelector(`#${id}-op`).value, value: v === '' ? null : (Number.isNaN(Number(v)) ? v : Number(v)), cooldown_s: Number(form.querySelector(`#${id}-cool`).value || 0) };
            const schedule = {
                ...(existing?.id ? { id: existing.id } : {}), name: form.querySelector(`#${id}-name`).value.trim(), kind, spec, enabled: form.querySelector(`#${id}-en`).checked,
                target: { kind: tkind, target: picker.value(), params: read.values, selector: where.value(), strategy: existing?.target?.strategy || {}, check_mode: form.querySelector(`#${id}-check`).checked },
            };
            try { await ui.busy(form.querySelector('[type=submit]'), () => api.cmd('putSchedule', { schedule })); ui.toast('Schedule saved', 'ok'); d.close(); done?.(); }
            catch (e2) { err(form, e2); }
        };
    }

    /* ------------------------------------------------------------ settings page */
    async function renderSettings(el, route, { isCurrent }) {
        const s = await api.get('settings');
        if (!isCurrent()) return null;
        const net = s.network || {};
        const ov = s.overlay || {};
        const pol = s.access_policy || {};
        const sec = s.secrets || {};
        const strat = (s.defaults || {}).strategy || {};
        const keyRow = (label, k) => k ? `<div class="orch-key"><p class="orch-label-text">${esc(label)}</p>${ui.kv([['Key id', `<code class="orch-mono">${esc(k.key_id)}</code>`, true], ['Public key', `<code class="orch-mono orch-break">${esc(k.public_key)}</code> ${copyBtn(k.public_key, `${label} public key`)}`, true]])}</div>` : '';
        el.innerHTML = `
            <header class="w-page__head orch-head">
                <div><h1 class="w-page__title">Orchestration settings</h1></div>
            </header>
            <nav class="orch-textfilters orch-settings__nav" aria-label="Settings sections">${SECTIONS.map(([id, label]) => `<a class="orch-textfilter" href="#/settings?section=${id}" data-section-link="${id}">${esc(label)}</a>`).join('')}</nav>
            <div class="orch-settings">
                ${section('network', 'Network', 'Nodes connect to a TLS endpoint on a private address you choose. The browser GUI stays on loopback.', `
                    <div class="orch-grid-2">
                        <form class="w-card orch-card" data-form="network" novalidate><div class="w-card__body orch-stack">
                            <div class="w-field"><label class="w-field__label" for="set-bind">Bind address</label><input class="w-input" data-mono id="set-bind" value="${esc(net.bind || '')}" placeholder="10.77.0.1" autocomplete="off">
                                <p class="w-field__hint">A private, loopback or overlay address. Public and any-address binds (0.0.0.0, ::) are refused, so nothing is exposed to the internet. Leave empty to stop the endpoint.</p></div>
                            <div class="orch-form-grid">
                                <div class="w-field"><label class="w-field__label" for="set-port">Port</label><input class="w-input" type="number" min="1024" max="65535" id="set-port" value="${esc(net.port || 30312)}"></div>
                                <div class="w-field"><label class="w-field__label" for="set-adv">Advertised address <span class="orch-muted">(optional)</span></label><input class="w-input" data-mono id="set-adv" value="${esc(net.advertise || '')}" placeholder="master.internal" autocomplete="off"></div>
                            </div>
                            <p class="w-field__error" data-error role="alert"></p>
                            <div class="w-btn-row orch-form-actions"><button type="submit" class="w-btn" data-variant="primary">Save and restart endpoint</button></div>
                        </div></form>
                        <div>${Orch.nodes.endpointLine(s.endpoint, { link: false })}</div>
                    </div>`)}
                ${section('overlay', 'WireGuard overlay', 'An optional private network between the master and nodes, for machines that share no private LAN.', ov.subnet ? `
                    <div class="orch-grid-2">
                        <article class="w-card orch-card"><header class="w-card__head"><h3 class="w-card__title">Overlay</h3>${ui.chip({ semantic: 'ok', glyph: '✓', label: 'Initialised' })}</header><div class="w-card__body">${ui.kv([
                            ['Subnet', ov.subnet], ['Master endpoint', ov.endpoint || 'not set'], ['Listen port', ov.listen_port],
                            ['Master public key', `<code class="orch-mono orch-break">${esc(ov.public_key || '')}</code>`, true],
                            ['Hub endpoint', ov.hub_endpoint || ''], ['Hub public key', ov.hub_public_key ? `<code class="orch-mono orch-break">${esc(ov.hub_public_key)}</code>` : '', true],
                        ])}<div class="w-btn-row"><a class="w-btn" data-variant="secondary" data-size="sm" href="/api/orchestration/overlay/master" target="_blank" rel="noopener">${icon('download', 'sm')}<span>Master config</span></a>${ov.hub_endpoint ? `<a class="w-btn" data-variant="secondary" data-size="sm" href="/api/orchestration/overlay/hub" target="_blank" rel="noopener">${icon('download', 'sm')}<span>Hub config</span></a>` : ''}</div></div></article>
                        <form class="w-card orch-card" data-form="peer" novalidate><header class="w-card__head"><h3 class="w-card__title">Add a peer</h3></header><div class="w-card__body orch-stack">
                            <div class="w-field"><label class="w-field__label" for="set-peer">Peer name</label><input class="w-input" data-mono id="set-peer" placeholder="web-01" autocomplete="off"></div>
                            <p class="w-field__error" data-error role="alert"></p>
                            <div class="w-btn-row orch-form-actions"><button type="submit" class="w-btn" data-variant="primary">Create peer config</button></div>
                            <div data-peer-out></div></div></form>
                    </div>
                    ${ui.table({ label: 'Overlay peers', empty: 'No peers yet.', cols: [{ label: 'Name' }, { label: 'Address' }, { label: 'Role' }, { label: 'Public key' }, { label: 'Node' }, { label: 'Added' }],
                        rows: (s.overlay_peers || []).map(p => [esc(p.name), `<span class="orch-mono">${esc(p.address)}</span>`, esc(p.role), `<code class="orch-mono orch-break">${esc(p.public_key)}</code>`, p.node_id ? `<a class="orch-mono" href="#/node/${encodeURIComponent(p.node_id)}">${esc(p.node_id)}</a>` : '—', ui.time(p.created_at)]) })}` : `
                    <form class="w-card orch-card orch-form-narrow" data-form="overlay" novalidate><div class="w-card__body orch-stack">
                        <div class="w-field"><label class="w-field__label" for="set-subnet">Subnet</label><input class="w-input" data-mono id="set-subnet" value="10.77.0.0/24"><p class="w-field__hint">Must be a private range.</p></div>
                        <div class="w-field"><label class="w-field__label" for="set-wgend">Master endpoint <span class="orch-muted">(host:port nodes dial)</span></label><input class="w-input" data-mono id="set-wgend" placeholder="198.51.100.7:51820" autocomplete="off"></div>
                        <div class="w-field"><label class="w-field__label" for="set-hub">Hub endpoint <span class="orch-muted">(optional relay)</span></label><input class="w-input" data-mono id="set-hub" placeholder="hub.example.net:51820" autocomplete="off"></div>
                        <p class="w-field__error" data-error role="alert"></p>
                        <div class="w-btn-row orch-form-actions"><button type="submit" class="w-btn" data-variant="primary">Initialise overlay</button></div></div></form>`)}
                ${section('access', 'Access policy', 'Ordered rules, first match wins: who (operator or AI) may run which effect, category or tool, on which nodes. Each node is judged separately and the strictest result wins.', `
                    <form class="orch-stack" data-form="access" novalidate>
                        <div class="orch-head__meta">${pol.default ? ui.meta('Using the default rules') : ui.meta(`Version ${pol.version}`)}</div>
                        <div class="w-field"><label class="w-field__label" for="set-policy">Rules (YAML)</label><textarea class="w-textarea orch-editor" data-mono id="set-policy" rows="18" spellcheck="false">${esc(pol.yaml || '')}</textarea>
                            <p class="w-field__hint">Each rule: <code>name</code>, <code>match</code> (actor: ai|operator, run: run|test, effect, category, tool glob, nodes selector) and <code>decision</code>: allow, confirm, deny or <code>{confirm: typed}</code>. A rule without <code>run</code> matches ordinary runs only; <code>run: test</code> rules decide canary tests of drafts (tests nothing matches are confirmed). Whatever the rules say, tests only reach canary nodes and destructive effects keep typed confirmation.</p></div>
                        <details class="orch-details"><summary>Example: let the AI test read-only drafts without asking</summary>${ui.code('- name: AI may test read-only drafts on canaries\n  match: {actor: ai, run: test, effect: read_only}\n  decision: allow', { lang: 'yaml', title: 'rule (put it before the default test rule)' })}</details>
                        <details class="orch-details" data-tool-ids><summary>Tool ids in the catalog (for <code>tool:</code> globs)</summary><p class="orch-muted">Loading…</p></details>
                        <p class="w-field__error" data-error role="alert"></p>
                        <div class="w-btn-row orch-form-actions"><button type="button" class="w-btn" data-variant="secondary" data-reset-policy>Reset to defaults</button><button type="submit" class="w-btn" data-variant="primary">Save policy</button></div>
                    </form>
                    ${(pol.history || []).length ? `<details class="orch-details"><summary>History (${pol.history.length})</summary>${ui.table({ label: 'Policy history', cols: [{ label: 'Version', num: true }, { label: 'Saved by' }, { label: 'When' }, { label: 'Rules', num: true }, { label: 'Actions', sr: true, cls: 'orch-actions-col' }],
                        rows: pol.history.map((h, i) => [String(h.version), esc(h.created_by), ui.time(h.created_at), String((h.rules || []).length), `<button type="button" class="w-btn" data-variant="ghost" data-size="sm" data-load-policy="${i}">Load into editor</button>`]) })}</details>` : ''}`)}
                ${section('keys', 'Keys', 'The master signs every published version; nodes only run what this key signed. Test runs of drafts use the separate test key.', `
                    <div class="orch-grid-2">
                        <article class="w-card orch-card"><header class="w-card__head"><h3 class="w-card__title">Signing keys</h3></header><div class="w-card__body orch-stack">${keyRow('Publishing key', (s.keys || {}).signing)}${keyRow('Test key', (s.keys || {}).test)}</div></article>
                        <article class="w-card orch-card"><header class="w-card__head"><h3 class="w-card__title">Trusted bundle keys</h3></header><div class="w-card__body orch-stack">
                            <p class="orch-muted">Bundles signed by these keys import as published; others import as drafts for review.</p>
                            ${ui.table({ label: 'Trusted keys', empty: 'No trusted keys.', stack: false, cols: [{ label: 'Label' }, { label: 'Key id' }, { label: 'Added' }, { label: 'Actions', sr: true, cls: 'orch-actions-col' }],
                                rows: (s.trusted_keys || []).map(k => [esc(k.label || '—'), `<code class="orch-mono" title="${esc(k.public_key)}">${esc(k.key_id)}</code>`, ui.time(k.added_at), `<button type="button" class="w-btn" data-variant="ghost" data-size="sm" data-untrust="${esc(k.key_id)}">Remove</button>`]) })}
                            <form class="orch-stack" data-form="trust" novalidate>
                                <div class="w-field"><label class="w-field__label" for="set-tk">Public key (base64 Ed25519)</label><input class="w-input" data-mono id="set-tk" autocomplete="off" spellcheck="false"></div>
                                <div class="w-field"><label class="w-field__label" for="set-tkl">Label</label><input class="w-input" id="set-tkl" autocomplete="off" placeholder="Staging master"></div>
                                <p class="w-field__error" data-error role="alert"></p>
                                <div class="w-btn-row orch-form-actions"><button type="submit" class="w-btn" data-variant="secondary">Trust key</button></div>
                            </form></div></article>
                    </div>`)}
                ${section('secrets', 'Secrets', 'Values are encrypted on the master and only sent to nodes for the runs that declare them. Only names are shown here.', `
                    ${sec.locked ? ui.banner('attention', '◆', '<strong>Secrets are locked.</strong> Runs that need secrets can’t dispatch until you unlock them.') : ''}
                    <div class="orch-grid-2">
                        <article class="w-card orch-card"><header class="w-card__head"><h3 class="w-card__title">Stored secrets</h3>${sec.locked ? ui.chip({ semantic: 'attention', glyph: '◆', label: 'Locked' }) : ui.chip({ semantic: 'ok', glyph: '✓', label: 'Unlocked' })}${sec.passphrase ? ui.meta('passphrase protected') : ''}</header><div class="w-card__body orch-stack">
                            ${ui.table({ label: 'Secrets', empty: 'No secrets yet.', stack: false, cols: [{ label: 'Name' }, { label: 'Description' }, { label: 'Updated' }, { label: 'Actions', sr: true, cls: 'orch-actions-col' }],
                                rows: (sec.names || []).map(x => { const n = typeof x === 'string' ? { name: x } : x; return [`<code class="orch-mono">${esc(n.name)}</code>`, esc(n.description || ''), ui.time(n.updated_at), `<button type="button" class="w-btn" data-variant="ghost" data-size="sm" data-del-secret="${esc(n.name)}">Delete</button>`]; }) })}
                            <div class="w-btn-row">${sec.passphrase ? (sec.locked ? `<button type="button" class="w-btn" data-variant="primary" data-unlock>${icon('unlock')}<span>Unlock</span></button>` : `<button type="button" class="w-btn" data-variant="secondary" data-lock>${icon('lock')}<span>Lock now</span></button>`) : ''}
                                <button type="button" class="w-btn" data-variant="ghost" data-passphrase>${icon('shield')}<span>${sec.passphrase ? 'Change passphrase' : 'Protect with a passphrase'}</span></button></div>
                        </div></article>
                        <form class="w-card orch-card" data-form="secret" novalidate><header class="w-card__head"><h3 class="w-card__title">Add or replace a secret</h3></header><div class="w-card__body orch-stack">
                            <div class="w-field"><label class="w-field__label" for="set-sn">Name</label><input class="w-input" data-mono id="set-sn" autocomplete="off" placeholder="DB_PASSWORD"></div>
                            <div class="w-field"><label class="w-field__label" for="set-sv">Value</label><input class="w-input" type="password" id="set-sv" autocomplete="new-password"></div>
                            <div class="w-field"><label class="w-field__label" for="set-sd">Description <span class="orch-muted">(optional)</span></label><input class="w-input" id="set-sd" autocomplete="off"></div>
                            <p class="w-field__error" data-error role="alert"></p>
                            <div class="w-btn-row orch-form-actions"><button type="submit" class="w-btn" data-variant="primary"${sec.locked ? ' disabled title="Unlock secrets first"' : ''}>Save secret</button></div>
                        </div></form>
                    </div>`)}
                ${section('agents', 'Node agents', `This master serves agent ${esc((s.agent_updates || {}).current || '')}. Nodes that allow it (<code>allow_agent_updates: true</code> in their policy.yaml) are updated by an update job: an ordinary run, canary first and batched, that the access policy decides like any other.`, `
                    <div class="w-card orch-card orch-form-narrow"><div class="w-card__body orch-stack">
                        <label class="w-switch"><span class="w-switch__label">Keep node agents up to date (propose an update job when nodes fall behind)</span><input class="w-switch__input" type="checkbox" role="switch" id="set-agent-auto"${(s.agent_updates || {}).auto ? ' checked' : ''}></label>
                        <p class="w-field__hint">The job waits in Approvals unless an access rule allows it, for example <code>{name: Agent updates, match: {tool: mucli-node}, decision: allow}</code>. Each node verifies the signed order and the agent’s digest, test-runs the new agent and keeps the previous one as <code>mucli-node.pyz.prev</code>.</p>
                        <div class="w-btn-row"><a class="w-btn" data-variant="secondary" data-size="sm" href="#/nodes">Nodes and their agents <span class="w-btn__chevron" aria-hidden="true">›</span></a></div>
                    </div></div>`)}
                ${section('defaults', 'Rollout defaults', 'Used when a run doesn’t set its own strategy.', `
                    <form class="w-card orch-card orch-form-narrow" data-form="defaults" novalidate><div class="w-card__body orch-stack">
                        <div class="orch-form-grid">
                            <div class="w-field"><label class="w-field__label" for="set-dc">Canary nodes</label><input class="w-input" type="number" min="0" id="set-dc" value="${esc(strat.canary ?? 1)}"></div>
                            <div class="w-field"><label class="w-field__label" for="set-db">Batch size</label><input class="w-input" type="number" min="1" id="set-db" value="${esc(strat.batch ?? 10)}"></div>
                            <div class="w-field"><label class="w-field__label" for="set-dp">Max parallel</label><input class="w-input" type="number" min="1" id="set-dp" value="${esc(strat.max_parallel ?? '')}" placeholder="batch size"></div>
                            <div class="w-field"><label class="w-field__label" for="set-dt">Failure threshold (%)</label><input class="w-input" type="number" min="0" max="100" id="set-dt" value="${esc(strat.failure_threshold ?? 10)}"></div>
                            <div class="w-field"><label class="w-field__label" for="set-dw">Pause between batches (s)</label><input class="w-input" type="number" min="0" id="set-dw" value="${esc(strat.pause_between_s ?? 0)}"></div>
                        </div>
                        <p class="w-field__error" data-error role="alert"></p>
                        <div class="w-btn-row orch-form-actions"><button type="submit" class="w-btn" data-variant="primary">Save defaults</button></div></div></form>`)}
                ${section('schedules', 'Schedules', 'Runs on a cron, an interval or when a node fact crosses a threshold.', `
                    ${ui.table({ label: 'Schedules', empty: 'No schedules yet.', cols: [{ label: 'Name' }, { label: 'When' }, { label: 'Target' }, { label: 'Next' }, { label: 'Last fired' }, { label: 'Enabled' }, { label: 'Actions', sr: true, cls: 'orch-actions-col' }],
                        rows: (s.schedules || []).map((x, i) => [esc(x.name), esc(scheduleSpecText(x)), `<span class="orch-mono">${esc(x.target?.target || '')}</span>${x.target?.selector?.labels ? `<span class="orch-sub orch-mono">${esc(x.target.selector.labels)}</span>` : ''}`,
                            x.next_at ? esc(ui.when(x.next_at)) : '—', ui.time(x.last_fired_at),
                            `<label class="w-switch orch-switch-inline"><span class="w-visually-hidden">Enable ${esc(x.name)}</span><input class="w-switch__input" type="checkbox" role="switch" data-toggle-sched="${i}"${x.enabled ? ' checked' : ''}></label>`,
                            `<button type="button" class="w-btn" data-variant="ghost" data-size="sm" data-edit-sched="${i}">Edit</button><button type="button" class="w-btn" data-variant="ghost" data-size="sm" data-del-sched="${i}">Delete</button>`]) })}`,
                    `<button type="button" class="w-btn" data-variant="secondary" data-size="sm" data-add-sched>${icon('plus', 'sm')}<span>New schedule</span></button>`)}
                ${section('bundles', 'Import and export', 'Move tools, runbooks, groups, schedules and policy between masters as a signed bundle.', `
                    <div class="orch-grid-2">
                        <article class="w-card orch-card"><header class="w-card__head"><h3 class="w-card__title">Export</h3></header><div class="w-card__body orch-stack">
                            <p class="orch-muted">Published tools and runbooks, signed with this master’s key.</p>
                            <label class="w-check"><input class="w-check__input" type="checkbox" id="set-exd"><span>Include drafts</span></label>
                            <label class="w-check"><input class="w-check__input" type="checkbox" id="set-exi"><span>Include node inventory</span></label>
                            <div class="w-btn-row"><a class="w-btn" data-variant="secondary" id="set-export" href="/api/orchestration/export?drafts=0" download data-no-prefetch>${icon('download')}<span>Download bundle</span></a></div></div></article>
                        <form class="w-card orch-card" data-form="import" novalidate><header class="w-card__head"><h3 class="w-card__title">Import</h3></header><div class="w-card__body orch-stack">
                            <div class="w-field"><label class="w-field__label" for="set-imf">Bundle file</label><input class="w-input" type="file" id="set-imf" accept=".zst,.tar,.gz,.mucli-orch,application/octet-stream"></div>
                            <p class="w-field__error" data-error role="alert"></p>
                            <div class="w-btn-row orch-form-actions"><button type="submit" class="w-btn" data-variant="secondary">${icon('eye')}<span>Preview import</span></button></div>
                        </div></form>
                    </div>
                    <div data-import-preview></div>`)}
                ${section('starter', 'Starter pack', 'Read-only diagnostics (disk, memory, load, services, open ports, certificates, updates) to get going.', `
                    <div class="w-btn-row"><button type="button" class="w-btn" data-variant="secondary" data-starter>${icon('download')}<span>Install starter pack</span></button><button type="button" class="w-btn" data-variant="ghost" data-starter-force>Reinstall (replace drafts)</button></div>`)}
            </div>`;

        const done = () => Orch.refresh();
        const on = (sel, ev, fn) => el.querySelector(sel)?.addEventListener(ev, fn);
        const submit = (name, fn) => { const f = el.querySelector(`[data-form="${name}"]`); if (f) f.addEventListener('submit', async e => { e.preventDefault(); clearErr(f); try { await fn(f); } catch (e2) { err(f, e2); } }); };
        const val = id => el.querySelector(id)?.value.trim() ?? '';

        submit('network', async f => { await ui.busy(f.querySelector('[type=submit]'), () => api.cmd('setNetwork', { bind: val('#set-bind'), port: Number(val('#set-port') || 30312), advertise: val('#set-adv') })); ui.toast('Network saved', 'ok'); done(); });
        submit('overlay', async f => { await ui.busy(f.querySelector('[type=submit]'), () => api.cmd('overlayInit', { subnet: val('#set-subnet'), endpoint: val('#set-wgend'), hub_endpoint: val('#set-hub') })); ui.toast('Overlay initialised', 'ok'); done(); });
        submit('peer', async f => {
            const res = await ui.busy(f.querySelector('[type=submit]'), () => api.cmd('overlayAddPeer', { name: val('#set-peer') }));
            f.querySelector('[data-peer-out]').innerHTML = `${ui.banner('attention', '◆', '<strong>Copy this now.</strong> It contains the peer’s private key, which isn’t stored on the master and won’t be shown again.')}${ui.code(res.data.node_config, { lang: 'ini', title: `${res.data.peer?.name || 'peer'}.conf` })}`;
            ui.toast(`Peer ${res.data.peer?.name} added at ${res.data.peer?.address}`, 'ok');
        });
        el.querySelector('[data-tool-ids]')?.addEventListener('toggle', async e => {
            const box = e.currentTarget;
            if (!box.open || box.dataset.loaded) return;
            box.dataset.loaded = '1';
            try {
                const o = await window.OrchPickers.options('run');
                const ids = o.tools.map(t => t.id);
                box.querySelector('p').outerHTML = ids.length ? `<p class="orch-labels">${ids.map(i => `<button type="button" class="w-textbtn orch-mono" data-tone="quiet" data-orch-copy data-copy-value="${esc(i)}" title="Copy ${esc(i)}">${esc(i)}</button>`).join('')}</p>` : '<p class="orch-muted">No published tools yet.</p>';
            } catch (e2) { box.querySelector('p').textContent = e2.message; }
        });
        submit('access', async f => {
            const text = val('#set-policy');
            await ui.busy(f.querySelector('[type=submit]'), () => api.cmd('saveAccessPolicy', { rules: rulesListYaml(text) }));
            ui.toast('Access policy saved', 'ok'); done();
        });
        on('[data-reset-policy]', 'click', async e => {
            if (!await ui.confirm({ title: 'Reset the access policy?', message: 'The default rules replace your current rules (saved as a new version).', confirmLabel: 'Reset policy', tone: 'danger' })) return;
            try { await ui.command('resetAccessPolicy', {}, { ok: 'Policy reset', button: e.currentTarget }); done(); } catch (_) { /* toasted */ }
        });
        el.querySelectorAll('[data-load-policy]').forEach(b => b.addEventListener('click', () => {
            const h = pol.history[Number(b.dataset.loadPolicy)];
            el.querySelector('#set-policy').value = 'rules:\n' + toYamlRules(h.rules || []);
            el.querySelector('#set-policy').focus();
            ui.toast(`Loaded version ${h.version}. Save to make it current.`, 'info');
        }));
        submit('trust', async f => { await ui.busy(f.querySelector('[type=submit]'), () => api.cmd('addTrustedKey', { public_key: val('#set-tk'), label: val('#set-tkl') })); ui.toast('Key trusted', 'ok'); done(); });
        el.querySelectorAll('[data-untrust]').forEach(b => b.addEventListener('click', async () => {
            if (!await ui.confirm({ title: 'Stop trusting this key?', message: 'Bundles it signed will import as drafts for review.', confirmLabel: 'Remove key', tone: 'danger' })) return;
            try { await ui.command('removeTrustedKey', { key_id: b.dataset.untrust }, { ok: 'Key removed' }); done(); } catch (_) { /* toasted */ }
        }));
        submit('secret', async f => {
            await ui.busy(f.querySelector('[type=submit]'), () => api.cmd('putSecret', { name: val('#set-sn'), value: el.querySelector('#set-sv').value, description: val('#set-sd') }));
            el.querySelector('#set-sv').value = '';
            ui.toast('Secret saved', 'ok'); done();
        });
        el.querySelectorAll('[data-del-secret]').forEach(b => b.addEventListener('click', async () => {
            if (!await ui.confirm({ title: `Delete secret ${b.dataset.delSecret}?`, message: 'Tools that declare it will fail until it is added again.', confirmLabel: 'Delete secret', tone: 'danger' })) return;
            try { await ui.command('deleteSecret', { name: b.dataset.delSecret }, { ok: 'Secret deleted' }); done(); } catch (_) { /* toasted */ }
        }));
        on('[data-unlock]', 'click', async e => {
            const p = await ui.prompt({ title: 'Unlock secrets', label: 'Passphrase', type: 'password', okLabel: 'Unlock' });
            if (p === null) return;
            try { await ui.command('unlockSecrets', { passphrase: p }, { ok: 'Secrets unlocked', button: e.currentTarget }); done(); } catch (_) { /* toasted */ }
        });
        on('[data-lock]', 'click', async e => { try { await ui.command('lockSecrets', {}, { ok: 'Secrets locked', button: e.currentTarget }); done(); } catch (_) { /* toasted */ } });
        on('[data-passphrase]', 'click', async e => {
            const p = await ui.prompt({ title: sec.passphrase ? 'Change the secrets passphrase' : 'Protect secrets with a passphrase', label: 'New passphrase', type: 'password', okLabel: 'Set passphrase', hint: 'After a restart, secrets stay locked until you enter it. There is no recovery if you forget it.' });
            if (p === null) return;
            try { await ui.command('setSecretPassphrase', { passphrase: p }, { ok: 'Passphrase set', button: e.currentTarget }); done(); } catch (_) { /* toasted */ }
        });
        submit('defaults', async f => {
            const n = id => { const v = val(id); return v === '' ? undefined : Number(v); };
            const strategy = { canary: n('#set-dc'), batch: n('#set-db'), max_parallel: n('#set-dp'), failure_threshold: n('#set-dt'), pause_between_s: n('#set-dw') };
            Object.keys(strategy).forEach(k => strategy[k] === undefined && delete strategy[k]);
            await ui.busy(f.querySelector('[type=submit]'), () => api.cmd('setDefaults', { strategy }));
            ui.toast('Defaults saved', 'ok'); done();
        });
        on('#set-agent-auto', 'change', async e => {
            const box = e.currentTarget;
            try { await ui.command('setAgentUpdates', { auto: box.checked }, { ok: box.checked ? 'Agents will be kept up to date' : 'Automatic agent updates are off' }); }
            catch (_) { box.checked = !box.checked; }
        });
        on('[data-add-sched]', 'click', () => scheduleDialog(null, done));
        el.querySelectorAll('[data-edit-sched]').forEach(b => b.addEventListener('click', () => scheduleDialog(s.schedules[Number(b.dataset.editSched)], done)));
        el.querySelectorAll('[data-del-sched]').forEach(b => b.addEventListener('click', async () => {
            const x = s.schedules[Number(b.dataset.delSched)];
            if (!await ui.confirm({ title: `Delete schedule ${x.name}?`, message: 'It stops firing. Runs it already created are kept.', confirmLabel: 'Delete schedule', tone: 'danger' })) return;
            try { await ui.command('deleteSchedule', { id: x.id }, { ok: 'Schedule deleted' }); done(); } catch (_) { /* toasted */ }
        }));
        el.querySelectorAll('[data-toggle-sched]').forEach(c => c.addEventListener('change', async () => {
            const x = s.schedules[Number(c.dataset.toggleSched)];
            try { await ui.command('setScheduleEnabled', { id: x.id, enabled: c.checked }, { ok: c.checked ? 'Schedule enabled' : 'Schedule paused' }); }
            catch (_) { c.checked = !c.checked; }
        }));
        const exportHref = () => { el.querySelector('#set-export').href = `/api/orchestration/export?drafts=${el.querySelector('#set-exd').checked ? 1 : 0}&inventory=${el.querySelector('#set-exi').checked ? 1 : 0}`; };
        on('#set-exd', 'change', exportHref);
        on('#set-exi', 'change', exportHref);
        submit('import', async f => {
            const file = el.querySelector('#set-imf').files[0];
            if (!file) throw new Error('Choose a bundle file first.');
            const res = await ui.busy(f.querySelector('[type=submit]'), () => api.upload('import?apply=0', file));
            paintImport(el.querySelector('[data-import-preview]'), res, file);
        });
        on('[data-starter]', 'click', async e => { try { const r = await ui.command('installStarterPack', {}, { quiet: true, button: e.currentTarget }); ui.toast((r.data.installed || []).length ? `Installed ${r.data.installed.length} tools` : 'The starter pack is already installed', 'ok'); } catch (_) { /* toasted */ } });
        on('[data-starter-force]', 'click', async e => {
            if (!await ui.confirm({ title: 'Reinstall the starter pack?', message: 'Open drafts of starter tools are discarded and the tools are published again as new versions.', confirmLabel: 'Reinstall' })) return;
            try { const r = await ui.command('installStarterPack', { force: true }, { quiet: true, button: e.currentTarget }); ui.toast(`Installed ${(r.data.installed || []).length} tools`, 'ok'); } catch (_) { /* toasted */ }
        });

        const want = route.query.get('section');
        if (want) {
            const target = el.querySelector(`#orch-set-${CSS.escape(want)}`);
            if (target) { target.scrollIntoView({ block: 'start' }); target.querySelector('h2')?.setAttribute('tabindex', '-1'); target.querySelector('h2')?.focus({ preventScroll: true }); }
        }
        el.querySelectorAll('[data-section-link]').forEach(a => a.addEventListener('click', e => {
            e.preventDefault();
            Orch.setQuery({ section: a.dataset.sectionLink });
            const t = el.querySelector(`#orch-set-${a.dataset.sectionLink}`);
            t?.scrollIntoView({ behavior: 'smooth', block: 'start' });
            const h = t?.querySelector('h2'); if (h) { h.setAttribute('tabindex', '-1'); h.focus({ preventScroll: true }); }
        }));
        // Forms in progress must not be replaced by live updates; refresh only on request.
        return { onChange() {} };
    }

    /* Minimal YAML for loading an old policy version back into the editor (JSON flow style is valid YAML). */
    function toYamlRules(rules) {
        return rules.map(r => `- name: ${JSON.stringify(r.name)}\n  match: ${JSON.stringify(r.match || {})}\n  decision: ${JSON.stringify(r.decision)}`).join('\n') + '\n';
    }

    function paintImport(box, res, file) {
        const trust = res.trust || {};
        const diff = res.diff || {};
        const ST = { new: { semantic: 'ok', glyph: '✓', label: 'New' }, identical: { semantic: 'neutral', glyph: '–', label: 'Identical' }, conflict: { semantic: 'danger', glyph: '✗', label: 'Conflict' }, changed: { semantic: 'attention', glyph: '◆', label: 'Changed' } };
        const trustChip = trust.intact === false ? ui.chip({ semantic: 'danger', glyph: '✗', label: 'Tampered or incomplete' })
            : trust.trusted ? ui.chip({ semantic: 'ok', glyph: '✓', label: 'Signed by a trusted key' })
            : trust.signed ? ui.chip({ semantic: 'attention', glyph: '◆', label: 'Signed, key not trusted' }) : ui.chip({ semantic: 'attention', glyph: '◆', label: 'Unsigned' });
        const access = (diff.policies || {}).access;
        box.innerHTML = `<article class="w-card orch-card" data-state="${trust.intact === false ? 'danger' : (trust.trusted ? 'ok' : 'attention')}" aria-labelledby="orch-imp-h">
            <header class="w-card__head">${trustChip}<h3 class="w-card__title" id="orch-imp-h" tabindex="-1">Import preview · ${esc(file.name)}</h3></header>
            <div class="w-card__body orch-stack">
                ${ui.kv([['Signed', trust.signed ? 'yes' : 'no'], ['Trusted', trust.trusted ? 'yes' : 'no'], ['Intact', trust.intact ? 'yes' : 'no'], ['Key id', trust.key_id ? `<code class="orch-mono">${esc(trust.key_id)}</code>` : '', true]])}
                ${(trust.problems || []).length ? ui.banner('danger', '✗', trust.problems.map(esc).join('; ')) : ''}
                ${!trust.trusted && trust.intact ? '<p class="orch-muted">Tools from an untrusted bundle import as drafts: review and publish them yourself.</p>' : ''}
                ${ui.table({ label: 'Tools in bundle', empty: 'No tools.', stack: false, cols: [{ label: 'Tool' }, { label: 'Effect' }, { label: 'Status' }, { label: 'Local version' }, { label: 'Problems' }],
                    rows: (diff.tools || []).map(t => [`${esc(t.title || t.tool_id)}<span class="orch-sub orch-mono">${esc(t.ref)}</span>`, t.effect ? ui.effectChip(Orch.runs.effectStyle(t.effect), t.effect) : '—', ui.chip(ST[t.status] || ST.changed), t.local_version != null ? esc(t.local_version) : '—', (t.errors || []).map(esc).join('; ') || '<span class="orch-muted">—</span>']) })}
                ${(diff.runbooks || []).length ? ui.table({ label: 'Runbooks in bundle', stack: false, cols: [{ label: 'Runbook' }, { label: 'Status' }], rows: diff.runbooks.map(r => [`<span class="orch-mono">${esc(r.ref)}</span>`, ui.chip(ST[r.status] || ST.changed)]) }) : ''}
                ${(diff.groups || []).length ? ui.table({ label: 'Groups in bundle', stack: false, cols: [{ label: 'Group' }, { label: 'Selector' }, { label: 'Status' }], rows: diff.groups.map(g => [esc(g.name), `<code class="orch-mono">${esc(g.selector)}</code>`, ui.chip(ST[g.status] || ST.changed)]) }) : ''}
                ${(diff.schedules || []).length ? `<p class="orch-sub">Schedules: ${diff.schedules.map(x => esc(`${x.name} (${x.kind})`)).join(', ')}</p>` : ''}
                ${access ? `<p>${access.changed ? ui.chip({ semantic: 'attention', glyph: '◆', label: 'Access policy differs' }) : ui.chip({ semantic: 'neutral', glyph: '–', label: 'Access policy identical' })}${(access.errors || []).length ? ` <span class="orch-error">${access.errors.map(esc).join('; ')}</span>` : ''}</p>` : ''}
                ${access?.changed ? '<label class="w-check"><input class="w-check__input" type="checkbox" data-imp-policies><span>Also replace my access policy with the bundle’s</span></label>' : ''}
                ${res.applied ? ui.banner('ok', '✓', 'Imported.') : `<div class="w-btn-row"><button type="button" class="w-btn" data-variant="primary" data-imp-apply${trust.intact === false ? ' disabled title="The bundle failed its integrity check"' : ''}>${icon('upload')}<span>Apply import</span></button></div>`}
            </div></article>`;
        box.querySelector('#orch-imp-h').focus({ preventScroll: false });
        box.querySelector('[data-imp-apply]')?.addEventListener('click', async e => {
            const policies = box.querySelector('[data-imp-policies]')?.checked ? 1 : 0;
            try {
                const r = await ui.busy(e.currentTarget, () => api.upload(`import?apply=1&policies=${policies}`, file));
                ui.toast('Bundle imported', 'ok');
                paintImport(box, { ...r, applied: true }, file);
            } catch (err2) { ui.toast(`Import failed: ${err2.message}`, 'danger'); }
        });
    }

    /* ------------------------------------------------------------ audit */
    async function renderAudit(el, route, { isCurrent, setQuery }) {
        const q = route.query;
        const filters = { actor: q.get('actor') || '', action: q.get('action') || '', subject: q.get('subject') || '' };
        const qs = p => new URLSearchParams(Object.fromEntries(Object.entries(p).filter(([, v]) => v !== '' && v != null))).toString();
        const data = await api.get(`audit?${qs({ ...filters, limit: 200 })}`);
        if (!isCurrent()) return null;
        let entries = data.entries || [];
        el.innerHTML = `
            <header class="w-page__head orch-head">
                <div><h1 class="w-page__title">Audit log</h1></div>
                <span class="orch-spacer"></span>
                <button type="button" class="w-btn" data-variant="secondary" data-verify>${icon('shield-check')}<span>Verify chain</span></button>
            </header>
            <div class="orch-stack">
                <div data-verify-out aria-live="polite"></div>
                <form class="orch-filters orch-audit-filters" data-filters novalidate role="search" aria-label="Filter the audit log">
                    <div class="w-field"><label class="w-field__label" for="au-actor">Actor</label><input class="w-input" data-mono id="au-actor" value="${esc(filters.actor)}" placeholder="operator, ai:*, node:…" autocomplete="off"></div>
                    <div class="w-field"><label class="w-field__label" for="au-action">Action</label><input class="w-input" data-mono id="au-action" value="${esc(filters.action)}" placeholder="run.approve, tool.*" autocomplete="off"></div>
                    <div class="w-field"><label class="w-field__label" for="au-subject">Subject</label><input class="w-input" data-mono id="au-subject" value="${esc(filters.subject)}" placeholder="r_…, n_…, tool@3" autocomplete="off"></div>
                    <div class="w-btn-row"><button type="submit" class="w-btn" data-variant="secondary">${icon('filter', 'sm')}<span>Filter</span></button>${Object.values(filters).some(Boolean) ? '<button type="button" class="w-btn" data-variant="ghost" data-clear>Clear</button>' : ''}</div>
                </form>
                <div data-audit-table></div>
                <div class="w-btn-row" data-more-row><button type="button" class="w-btn" data-variant="ghost" data-more>Load older</button></div>
            </div>`;
        const paint = () => {
            el.querySelector('[data-audit-table]').innerHTML = ui.table({
                label: 'Audit entries', empty: 'No entries match.',
                cols: [{ label: 'Seq', num: true }, { label: 'When', cls: 'orch-nowrap' }, { label: 'Actor' }, { label: 'Action' }, { label: 'Subject' }, { label: 'Details' }],
                rows: entries.map(e => {
                    const payload = e.payload && Object.keys(e.payload).length ? JSON.stringify(e.payload) : '';
                    return [String(e.seq), `<span title="${esc(new Date(e.at * 1000).toISOString())}">${esc(ui.when(e.at))}</span>`, `<span class="orch-mono">${esc(e.actor)}</span>`, `<span class="orch-mono">${esc(e.action)}</span>`, `<span class="orch-mono orch-break">${esc(e.subject)}</span>`,
                        payload ? (payload.length > 90 ? `<details class="orch-details orch-details--inline"><summary><code class="orch-mono">${esc(payload.slice(0, 80))}…</code></summary><pre class="orch-pre">${esc(JSON.stringify(e.payload, null, 2))}</pre></details>` : `<code class="orch-mono orch-break">${esc(payload)}</code>`) : '<span class="orch-muted">—</span>'];
                }),
            });
            el.querySelector('[data-more-row]').hidden = entries.length < 200 && !el.querySelector('[data-more-row]').dataset.more;
        };
        paint();
        el.querySelector('[data-filters]').addEventListener('submit', e => {
            e.preventDefault();
            setQuery({ actor: el.querySelector('#au-actor').value.trim(), action: el.querySelector('#au-action').value.trim(), subject: el.querySelector('#au-subject').value.trim() });
            Orch.refresh();
        });
        el.querySelector('[data-clear]')?.addEventListener('click', () => { setQuery({ actor: '', action: '', subject: '' }); Orch.refresh(); });
        el.querySelector('[data-more]').addEventListener('click', async e => {
            const before = entries.length ? Math.min(...entries.map(x => x.seq)) : 0;
            try {
                const more = await ui.busy(e.currentTarget, () => api.get(`audit?${qs({ ...filters, before, limit: 200 })}`));
                entries = entries.concat(more.entries || []);
                el.querySelector('[data-more-row]').dataset.more = (more.entries || []).length === 200 ? '1' : '';
                paint();
                if (!(more.entries || []).length) { el.querySelector('[data-more-row]').hidden = true; ui.toast('That’s the beginning of the log.', 'info'); }
            } catch (err2) { ui.toast(err2.message, 'danger'); }
        });
        el.querySelector('[data-verify]').addEventListener('click', async e => {
            const out = el.querySelector('[data-verify-out]');
            try {
                const v = await ui.busy(e.currentTarget, () => api.get('audit/verify'));
                out.innerHTML = v.ok ? ui.banner('ok', '✓', `<strong>The chain is intact.</strong> ${esc(ui.plural(v.entries || 0, 'entry', 'entries'))} verified.`)
                    : ui.banner('danger', '✗', `<strong>The chain is broken${v.seq ? ` at entry ${esc(v.seq)}` : ''}.</strong> ${esc(v.problem || '')} Export the log and investigate before trusting anything after it.`);
            } catch (err2) { out.innerHTML = ui.banner('danger', '✗', esc(err2.message)); }
        });
        return null;
    }

    Orch.routes.settings = { nav: 'settings', title: 'Settings', scopes: [], render: renderSettings, crumbs: () => [{ label: 'Settings' }] };
    Orch.routes.audit = { nav: 'audit', title: 'Audit log', scopes: [], render: renderAudit, crumbs: () => [{ label: 'Audit log' }] };
})();
