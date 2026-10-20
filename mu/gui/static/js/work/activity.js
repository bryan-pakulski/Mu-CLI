/* Jobs mode — Activity page (#/activity), a vanilla port of planos web/src/pages/Activity.tsx.
 *
 * Data: GET /api/jobs/vm/activity (polled every 5 s while the page is visible), the job worker
 * log via GET /api/jobs/vm/activity/logs/{job_id}, and POST /api/jobs/activity/containers/{name}/remove.
 * CPU / memory sparklines keep the last 60 samples per container on the client. Agents and
 * checks are Mu rows; opening one (or its Log) shows its containers and live worker log in the
 * Work dock (WashSheet; a floating sheet below 1200px).
 */
(() => {
    'use strict';
    const page = document.getElementById('work-activity');
    if (!page || !window.MuJobWork?.util) return;
    const U = window.MuJobWork.util;
    const ui = window.MuJobUI;
    const esc = U.esc;
    const bodyEl = page.querySelector('[data-activity-body]');
    const engineEl = page.querySelector('[data-activity-engine]');
    const updatedEl = page.querySelector('[data-activity-updated]');
    const refreshBtn = document.getElementById('work-activity-refresh');
    const POLL_MS = 5000, SAMPLES = 60;
    const ACTIVE = new Set(['provisioning', 'planning', 'implementing', 'checking', 'reviewing', 'pushing']);
    const KIND = {
        agent: ['●', 'running', 'agent'], checks: ['●', 'running', 'checks'], provision: ['●', 'running', 'preparing'],
        push: ['●', 'running', 'pushing'], waiting: ['◆', 'warn', 'waiting'],
    };
    const ENGINE = { up: ['ok', 'docker up'], down: ['danger', 'docker down'], off: ['muted', 'docker off'] };

    let active = false, timer = 0, data = null, legacy = false, loading = false, lastError = '';
    const samples = new Map(); // container name -> {cpu: [], mem: []}

    async function api(path, init = {}) {
        if (window.MuWork?.api) return window.MuWork.api(path, init);
        const response = await fetch(path, init);
        const body = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(body.detail || `${response.status} ${response.statusText}`);
        return body;
    }
    const toast = (text, tone) => ui?.toast(text, tone);

    const bytes = n => {
        if (n == null || !Number.isFinite(Number(n))) return '–';
        const v = Number(n), u = ['B', 'KB', 'MB', 'GB', 'TB'];
        let i = 0, x = v;
        while (x >= 1024 && i < u.length - 1) { x /= 1024; i++; }
        return `${x >= 100 || i === 0 ? Math.round(x) : x.toFixed(1)} ${u[i]}`;
    };
    const pct = v => (v == null || !Number.isFinite(Number(v)) ? '–' : `${Number(v).toFixed(1)}%`);
    const since = ts => (ts ? U.duration((Date.now() / 1000 - Number(ts)) * 1000) : '');

    /* ------------------------------------------------------------ samples + sparklines */
    function record(snapshot) {
        const seen = new Set();
        const all = [...(snapshot.jobs || []).flatMap(j => j.containers || []), ...(snapshot.orphans || [])];
        for (const c of all) {
            const key = c.name || c.id;
            seen.add(key);
            const s = samples.get(key) || { cpu: [], mem: [] };
            s.cpu.push(c.cpu_percent == null ? null : Number(c.cpu_percent));
            s.mem.push(c.mem_bytes == null ? null : Number(c.mem_bytes));
            if (s.cpu.length > SAMPLES) s.cpu.splice(0, s.cpu.length - SAMPLES);
            if (s.mem.length > SAMPLES) s.mem.splice(0, s.mem.length - SAMPLES);
            samples.set(key, s);
        }
        for (const key of [...samples.keys()]) if (!seen.has(key)) samples.delete(key);
    }
    /* Hand-written SVG sparkline (planos Sparkline: bare line + 15% fill, no axes). */
    function sparkline(values, token, label, fmt, ceiling) {
        const nums = values.filter(v => v != null && Number.isFinite(v));
        if (!nums.length) return `<span class="work-spark-empty work-meta" role="img" aria-label="${esc(label)}: no samples yet">–</span>`;
        const W = 120, H = 28, n = Math.max(2, values.length);
        const max = Math.max(ceiling || 0, ...nums, 1e-9);
        const x = i => (values.length === 1 ? W : (i / (n - 1)) * W), y = v => H - 2 - (v / max) * (H - 4);
        const segments = [];
        let cur = [];
        values.forEach((v, i) => { if (v == null || !Number.isFinite(v)) { if (cur.length) segments.push(cur); cur = []; } else cur.push([x(i), y(v)]); });
        if (cur.length) segments.push(cur);
        const paths = segments.map(seg => {
            const pts = seg.length === 1 ? [[Math.max(0, seg[0][0] - 2), seg[0][1]], seg[0]] : seg;
            const line = pts.map(([a, b]) => `${a.toFixed(1)},${b.toFixed(1)}`).join(' ');
            return `<polygon class="fill" points="${pts[0][0].toFixed(1)},${H} ${line} ${pts[pts.length - 1][0].toFixed(1)},${H}"/><polyline class="line" points="${line}"/>`;
        }).join('');
        const last = nums[nums.length - 1], peak = Math.max(...nums);
        return `<svg class="work-spark" data-tone="${token}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(`${label}: now ${fmt(last)}, peak ${fmt(peak)} over ${nums.length} samples`)}">${paths}</svg>`;
    }

    /* ------------------------------------------------------------ render (Mu §5.1, §7.2) */
    /* Agents and checks are rows; opening one shows its containers (live CPU / memory) and
     * its worker log in the dock beside the list from 1200px, or a floating sheet narrower.
     * Orphan containers are a second group only when there are any. The counts and Docker's
     * health are not repeated here: the page header's state sentence and the leaf alert
     * carry them. */
    function containerRow(c, job) {
        const key = c.name || c.id;
        const s = samples.get(key) || { cpu: [], mem: [] };
        const running = /^(running|up)/i.test(String(c.status || c.status_text || ''));
        const exited = /exited|dead|created/i.test(String(c.status || ''));
        const locked = job && ACTIVE.has(job.state);
        const role = c.role === 'engine' ? '<span title="Per-job Docker engine (Sysbox)">docker engine</span>'
            : c.role === 'broker' ? '<span title="Sibling container for another build environment">run_in_environment</span>' : c.stage ? esc(c.stage) : '';
        const facts = [esc(c.status_text || c.status || 'unknown'), role, c.binding ? `<span title="build environment">⬢ ${esc(c.binding)}</span>` : '',
            c.gpus ? `<span data-tone="attention" title="GPU request">gpus ${esc(c.gpus)}</span>` : '',
            c.started_at ? `${running ? 'up' : 'started'} ${esc(since(c.started_at))}${running ? '' : ' ago'}` : ''].filter(Boolean);
        const lockText = locked ? `${U.jobKey(job)} is ${U.stateLabel(job.state)}: stop or discard the job first` : '';
        return `<li class="w-row work-ctr" data-container="${esc(key)}">
            <span class="w-sign" data-status="${running ? 'active' : exited ? 'pending' : 'waiting'}" aria-hidden="true">${running ? '●' : exited ? '○' : '◆'}</span>
            <div class="w-row__main">
                <span class="w-row__title work-mono" title="${esc(key)}">${esc(key)}</span>
                <span class="w-row__sub">${facts.join(' · ')}</span>
                <span class="w-row__sub work-meta work-ellipsis" title="${esc(c.image || '')}">${esc(c.image || '')}</span>
                <span class="work-ctr-metrics">
                    <span class="work-ctr-metric"><span class="work-ctr-label">CPU</span>${sparkline(s.cpu, 'running', `CPU of ${key}`, pct, 100)}<span class="work-num">${esc(pct(c.cpu_percent))}</span></span>
                    <span class="work-ctr-metric"><span class="work-ctr-label">Memory</span>${sparkline(s.mem, 'accent', `Memory of ${key}`, bytes, c.mem_limit || 0)}<span class="work-num" title="${c.mem_limit ? `limit ${esc(bytes(c.mem_limit))}` : ''}">${esc(bytes(c.mem_bytes))}</span></span>
                </span>
            </div>
            <span class="w-row__actions"><button type="button" class="w-textbtn" data-tone="danger" data-remove-container="${esc(key)}" aria-label="Remove ${esc(key)}"
                ${locked ? `aria-disabled="true" data-locked="${esc(lockText)}" title="${esc(lockText)}"` : 'title="Remove container (docker rm -f)"'}>Remove</button></span></li>`;
    }

    function stageWords(j) {
        const [, , word] = KIND[j.kind] || KIND.agent;
        return `${word}${j.stage && j.stage !== j.kind && j.stage !== word ? ` · ${j.stage}` : ''}`;
    }
    function jobRow(j) {
        const [glyphChar, tone] = KIND[j.kind] || KIND.agent;
        const live = tone === 'running';
        const stale = j.heartbeat_at && Date.now() / 1000 - Number(j.heartbeat_at) > 90;
        const n = (j.containers || []).length;
        const sub = [esc(stageWords(j)),
            j.worker_id ? `worker <code>${esc(String(j.worker_id).slice(0, 18))}</code>` : 'no worker',
            j.heartbeat_at ? `<span${stale ? ' data-tone="attention"' : ''}>heartbeat ${esc(U.age(Date.now() / 1000 - Number(j.heartbeat_at)))} ago${stale ? ' (stale)' : ''}</span>` : '',
            n ? `${n} container${n === 1 ? '' : 's'}` : '',
            Number(j.cost_usd) ? esc(U.money(j.cost_usd)) : ''].filter(Boolean).join(' · ');
        return `<li class="w-row work-activity-row" data-activity-job="${esc(j.id)}"${j.id === runId ? ' data-selected' : ''}>
            <span class="w-sign" data-status="${live ? 'active' : tone === 'warn' ? 'waiting' : 'pending'}" aria-hidden="true">${glyphChar}</span>
            <button type="button" class="w-row__main" data-activity-open="${esc(j.id)}">
                <span class="w-row__title"><span class="w-row__key">${esc(U.jobKey(j))}</span>${esc(j.title)}</span>
                <span class="w-row__sub"><span class="w-sr-only">${esc(U.stateLabel(j.state))}: </span>${sub}</span>
                ${j.last ? `<span class="w-row__sub work-mono" title="${esc(U.stripAnsi(j.last))}">› ${esc(U.stripAnsi(j.last))}</span>` : ''}
            </button>
            ${j.since ? `<span class="w-row__when" title="in this state since ${esc(U.when(j.since))}">${esc(since(j.since))}</span>` : ''}
            <span class="w-row__actions"><button type="button" class="w-textbtn" data-activity-log="${esc(j.id)}">Log<span class="w-sr-only"> of ${esc(U.jobKey(j))}</span></button>
                <a class="w-textbtn" data-tone="quiet" href="#/job/${esc(j.id)}">Open job</a></span>
        </li>`;
    }

    function render() {
        if (!active) return;
        // Docker's health is silent while it's up; the leaf alert (work.js) also says when it's down.
        const engineNow = String(data?.engine || '');
        const [eTone, eText] = ENGINE[engineNow] || ['muted', engineNow ? `docker ${engineNow}` : ''];
        engineEl.innerHTML = !legacy && engineNow && engineNow !== 'up' && eText ? `<span class="work-meta"${eTone === 'danger' ? ' data-tone="danger"' : ''} title="Container engine">${esc(eText)}</span>` : '';
        if (!data) {
            bodyEl.innerHTML = lastError ? `<div class="w-banner" data-tone="danger" role="alert"><span class="w-banner__glyph" aria-hidden="true">✗</span><p class="w-banner__msg">${esc(lastError)}</p></div>`
                : '<div class="work-loading" role="status"><span class="w-inkdrop" aria-hidden="true"></span>Loading activity…</div>';
            return;
        }
        const engine = String(data.engine || '');
        updatedEl.textContent = data.generated_at ? `updated ${U.when(data.generated_at).slice(11)}` : '';
        const jobs = data.jobs || [], orphans = data.orphans || [];
        const focusKey = document.activeElement?.closest?.('[data-container], [data-activity-job]') && document.activeElement.matches('button')
            ? [document.activeElement.dataset.removeContainer, document.activeElement.dataset.activityLog, document.activeElement.dataset.activityOpen] : null;
        bodyEl.innerHTML = `
            ${lastError ? `<div class="w-banner" data-tone="danger" role="alert"><span class="w-banner__glyph" aria-hidden="true">✗</span><p class="w-banner__msg">${esc(lastError)} — showing the last data.</p></div>` : ''}
            ${legacy ? '<div class="w-banner" data-tone="caution" role="note"><span class="w-banner__glyph" aria-hidden="true">◆</span><p class="w-banner__msg">This mucli server has no activity endpoint yet: showing running jobs only, without containers.</p></div>' : ''}
            <section class="w-group" aria-labelledby="activity-jobs-h"><h2 class="w-group__title" id="activity-jobs-h">Agents, checks &amp; containers</h2>
                ${jobs.length ? `<ul class="w-rows work-activity-rows">${jobs.map(jobRow).join('')}</ul>`
                    : `<p class="w-group__empty">All quiet: no agent or check is running${engine === 'down' ? ', and Docker isn’t reachable for container stats' : ''}.</p>`}</section>
            ${orphans.length ? `<section class="w-group" aria-labelledby="activity-orphans-h"><header class="w-group__head"><h2 class="w-group__title" id="activity-orphans-h">Orphan containers</h2><span class="work-meta">job containers whose job is no longer active</span></header>
                <ul class="w-rows">${orphans.map(o => containerRow(o, null)).join('')}</ul></section>` : ''}`;
        bindRemove(bodyEl, jobs);
        bodyEl.querySelectorAll('[data-activity-open]').forEach(b => { b.onclick = () => openRun(jobs.find(j => j.id === b.dataset.activityOpen), { from: b }); });
        bodyEl.querySelectorAll('[data-activity-log]').forEach(b => { b.onclick = () => openRun(jobs.find(j => j.id === b.dataset.activityLog), { from: b, log: true }); });
        if (focusKey) {
            const [rm, log, openId] = focusKey;
            (rm ? bodyEl.querySelector(`[data-remove-container="${CSS.escape(rm)}"]`) : log ? bodyEl.querySelector(`[data-activity-log="${CSS.escape(log)}"]`)
                : openId ? bodyEl.querySelector(`[data-activity-open="${CSS.escape(openId)}"]`) : null)?.focus();
        }
        paintRun();
        autoOpen();
    }
    function bindRemove(root, jobs) {
        root.querySelectorAll('[data-remove-container]').forEach(b => { b.onclick = () => b.dataset.locked ? toast(`Can't remove ${b.dataset.removeContainer}: ${b.dataset.locked}`, 'warn') : remove(b.dataset.removeContainer, jobs.find(j => (j.containers || []).some(x => (x.name || x.id) === b.dataset.removeContainer))); });
    }

    /* ------------------------------------------------------------ the run in the dock */
    let runId = '', runHandle = null, runDismissed = false, logTimer = 0;
    function runState(j) {
        const [, tone] = KIND[j.kind] || KIND.agent;
        return [`<span class="w-state__part w-signword" data-status="${tone === 'running' ? 'active' : 'waiting'}">${esc(U.stateLabel(j.state))}</span>`,
            `<span class="w-state__part">${esc(stageWords(j))}</span>`,
            j.since ? `<span class="w-state__part">for ${esc(since(j.since))}</span>` : '',
            Number(j.cost_usd) ? `<span class="w-state__part">${esc(U.money(j.cost_usd))}</span>` : ''].join('');
    }
    function paintRun() {
        if (!runId || !runHandle?.el?.isConnected || !data) return;
        const j = (data.jobs || []).find(x => x.id === runId);
        if (!j) return;
        runHandle.el.querySelector('.w-sheet__state').innerHTML = runState(j);
        const last = runHandle.body.querySelector('[data-run-last]');
        last.textContent = j.last ? `› ${U.stripAnsi(j.last)}` : '';
        last.hidden = !j.last;
        const ctrs = j.containers || [];
        const host = runHandle.body.querySelector('[data-run-ctrs]');
        host.innerHTML = ctrs.length ? `<h3 class="w-group__title">Containers</h3><ul class="w-rows">${ctrs.map(c => containerRow(c, j)).join('')}</ul>`
            : `<p class="w-group__empty">${j.kind === 'waiting' ? 'Waiting for you; no container.' : 'No container (runs on the host or not started yet).'}</p>`;
        bindRemove(host, data.jobs || []);
    }
    function stopLog() { clearTimeout(logTimer); logTimer = 0; }
    function openRun(job, { from = null, focus = false, log = false } = {}) {
        if (!job || !window.WashSheet) return;
        stopLog();
        const wrap = document.createElement('div');
        wrap.className = 'work-jobpreview work-run';
        wrap.innerHTML = `<div class="work-jobpreview__acts"><a class="w-textbtn work-jobpreview__open" href="#/job/${esc(job.id)}?tab=session" data-log-open>Open ${esc(U.jobKey(job))} <span aria-hidden="true">→</span></a></div>
            <p class="work-mono work-run__last" data-run-last hidden></p>
            <section data-run-ctrs aria-label="Containers"></section>
            <section aria-label="Log of ${esc(U.jobKey(job))}">
                <header class="w-group__head"><h3 class="w-group__title">Log</h3><span class="work-meta" data-log-source></span><span class="work-meta" data-log-trunc></span>
                    <label class="w-check"><input class="w-check__input" type="checkbox" data-log-follow checked><span>Follow</span></label>
                    <button type="button" class="w-textbtn" data-tone="quiet" data-log-refresh>Refresh</button></header>
                <pre class="work-log work-log-tall" role="log" aria-label="Log of ${esc(U.jobKey(job))}" tabindex="0" data-log-text><span class="work-meta">Reading log…</span></pre>
            </section>`;
        runId = job.id;
        document.querySelectorAll('.work-activity-row[data-selected]').forEach(el => { if (el.dataset.activityJob !== job.id) el.removeAttribute('data-selected'); });
        runHandle = window.WashSheet.open({ title: `${U.jobKey(job)} · ${job.title}`, state: runState(job), body: wrap, param: { key: 'open', value: `run:${job.id}` },
            row: from?.closest?.('.w-row') || bodyEl.querySelector(`[data-activity-job="${CSS.escape(job.id)}"]`), focus: focus || log,
            onClose: () => { if (runId === job.id) { runId = ''; runHandle = null; runDismissed = true; stopLog(); document.querySelectorAll('.work-activity-row[data-selected]').forEach(el => el.removeAttribute('data-selected')); } } });
        const pre = wrap.querySelector('[data-log-text]');
        const follow = wrap.querySelector('[data-log-follow]');
        const load = async () => {
            stopLog();
            if (runId !== job.id) return;
            try {
                const r = await api(`/api/jobs/vm/activity/logs/${encodeURIComponent(job.id)}?tail_bytes=65536`);
                if (runId !== job.id) return;
                const atEnd = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
                pre.innerHTML = r.text ? U.ansi(r.text) : '<span class="work-meta">The log is empty.</span>';
                wrap.querySelector('[data-log-source]').textContent = r.source ? `source: ${r.source}` : '';
                wrap.querySelector('[data-log-trunc]').textContent = r.truncated ? '· the last 64 KB' : '';
                if (follow.checked && (atEnd || !pre.dataset.loaded)) pre.scrollTop = pre.scrollHeight;
                pre.dataset.loaded = '1';
            } catch (e) {
                if (runId !== job.id) return;
                pre.innerHTML = `<span class="work-del">✗ ${esc(/404|not found/i.test(e.message) ? 'This mucli server has no activity log endpoint yet.' : e.message)}</span>`;
                return;
            }
            if (ACTIVE.has(job.state) && document.visibilityState === 'visible') logTimer = setTimeout(load, 3000);
        };
        wrap.querySelector('[data-log-refresh]').onclick = load;
        paintRun();
        load();
        if (log) pre.focus({ preventScroll: true });
    }
    function closeRun() {
        if (!runId) return;
        runId = ''; runHandle = null; stopLog();
        if (window.WashSheet?.isOpen()) window.WashSheet.close();
    }
    /* Arrival: ?open=run:<id> opens that run; otherwise, with the dock showing, the first one. */
    function autoOpen() {
        if (!active || runId || runDismissed || !data || !window.WashSheet) return;
        const jobs = data.jobs || [];
        const asked = String(window.WashSheet.param('open') || '');
        const wanted = asked.startsWith('run:') ? jobs.find(j => j.id === asked.slice(4)) : null;
        if (wanted) return openRun(wanted);
        if (window.WashSheet.docked() && jobs.length) openRun(jobs[0]);
    }

    /* ------------------------------------------------------------ data */
    async function fetchLegacy() {
        const home = await api('/api/jobs/vm/home');
        const kind = s => (s === 'checking' ? 'checks' : s === 'provisioning' ? 'provision' : s === 'pushing' ? 'push' : 'agent');
        return {
            generated_at: Date.now() / 1000, engine: '',
            counts: { agents: home.running.filter(j => j.state !== 'checking').length, checks: home.running.filter(j => j.state === 'checking').length, containers: 0, needs_you: home.needs_you.length },
            jobs: home.running.map(j => ({ ...j, kind: kind(j.state), stage: '', since: j.updated_at, containers: [], last: (j.pipeline || j.steps || []).find(s => s.status === 'active')?.detail || '' })),
            orphans: [],
        };
    }
    async function tick() {
        clearTimeout(timer);
        if (!active || loading) return;
        loading = true;
        try {
            let next;
            if (!legacy) {
                try { next = await api('/api/jobs/vm/activity'); }
                catch (e) { if (/404|not found/i.test(e.message)) legacy = true; else throw e; }
            }
            if (legacy) next = await fetchLegacy();
            data = next; lastError = '';
            record(data);
        } catch (e) { lastError = e.message; }
        finally { loading = false; }
        render();
        schedule();
    }
    function schedule() {
        clearTimeout(timer);
        if (active && document.visibilityState === 'visible') timer = setTimeout(tick, POLL_MS);
    }

    async function remove(name, job) {
        const ok = await ui.confirm(`Remove container ${name}? docker rm -f stops whatever is still running in it.`, { title: 'Remove container', okLabel: 'Remove', tone: 'danger' });
        if (!ok) return;
        try {
            await api(`/api/jobs/activity/containers/${encodeURIComponent(name)}/remove`, { method: 'POST' });
            toast(`Removed ${name}`, 'ok');
            samples.delete(name);
        } catch (e) {
            toast(/409/.test(e.message) || /active/i.test(e.message) ? `${name}: ${e.message}` : e.message, 'danger');
        }
        await tick();
        if (job) document.querySelector(`[data-activity-log="${CSS.escape(job.id)}"]`)?.focus();
    }

    /* ------------------------------------------------------------ wiring */
    refreshBtn?.addEventListener('click', () => { tick(); });
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && active) tick(); else clearTimeout(timer); });
    window.addEventListener('mu:work-route', e => {
        const next = e.detail?.name === 'activity';
        if (next === active) { if (next) tick(); return; }
        active = next;
        if (active) {
            runDismissed = false;
            ui?.register('page:activity', [{ id: 'activity:refresh', label: 'Refresh activity', group: 'Activity', run: tick }], 10);
            render(); tick();
        } else { clearTimeout(timer); closeRun(); ui?.register('page:activity', []); }
    });
})();
