/* Jobs mode — Activity page (#/activity), a vanilla port of planos web/src/pages/Activity.tsx.
 *
 * Data: GET /api/jobs/vm/activity (polled every 5 s while the page is visible), the job worker
 * log via GET /api/jobs/vm/activity/logs/{job_id}, and POST /api/jobs/activity/containers/{name}/remove.
 * CPU / memory sparklines keep the last 60 samples per container on the client.
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
        if (!nums.length) return `<span class="work-spark-empty muted small" role="img" aria-label="${esc(label)}: no samples yet">–</span>`;
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
        return `<svg class="work-spark tone-${token}" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${esc(`${label}: now ${fmt(last)}, peak ${fmt(peak)} over ${nums.length} samples`)}">${paths}</svg>`;
    }

    /* ------------------------------------------------------------ render */
    function containerRow(c, job) {
        const key = c.name || c.id;
        const s = samples.get(key) || { cpu: [], mem: [] };
        const running = /^(running|up)/i.test(String(c.status || c.status_text || ''));
        const locked = job && ACTIVE.has(job.state);
        const status = pill(running ? 'running' : /exited|dead|created/i.test(String(c.status || '')) ? 'muted' : 'warn',
            `<span aria-hidden="true">${running ? '●' : '○'}</span>${esc(c.status_text || c.status || 'unknown')}`);
        return `<li class="work-ctr" data-container="${esc(key)}">
            <div class="work-ctr-name"><div class="row"><span class="tone-accent" aria-hidden="true">⬢</span><span class="mono small ellipsis" title="${esc(key)}">${esc(key)}</span>${c.role === 'engine' ? pill('accent', 'docker engine', 'Per-job Docker engine (Sysbox)') : c.role === 'broker' ? pill('accent', 'run_in_environment', 'Sibling container for another build environment') : c.stage ? pill('accent', esc(c.stage)) : ''}${c.binding ? pill('muted', `<span aria-hidden="true">⬢</span> ${esc(c.binding)}`, 'build environment') : ''}${c.gpus ? pill('warn', `gpus ${esc(c.gpus)}`, 'GPU request') : ''}</div>
                <div class="muted small ellipsis" title="${esc(c.image || '')}">${esc(c.image || '')}${c.started_at ? ` · ${running ? 'up' : 'started'} ${esc(since(c.started_at))}${running ? '' : ' ago'}` : ''}</div></div>
            <div class="work-ctr-state">${status}</div>
            <div class="work-ctr-metric"><span class="work-ctr-label small muted">CPU</span>${sparkline(s.cpu, 'running', `CPU of ${key}`, pct, 100)}<span class="small num">${esc(pct(c.cpu_percent))}</span></div>
            <div class="work-ctr-metric"><span class="work-ctr-label small muted">Mem</span>${sparkline(s.mem, 'accent', `Memory of ${key}`, bytes, c.mem_limit || 0)}<span class="small num" title="${c.mem_limit ? `limit ${esc(bytes(c.mem_limit))}` : ''}">${esc(bytes(c.mem_bytes))}</span></div>
            <div class="work-ctr-actions"><button type="button" class="btn sm tone-danger" data-remove-container="${esc(key)}" aria-label="Remove ${esc(key)}"
                ${locked ? `aria-disabled="true" data-locked="${esc(`${U.jobKey(job)} is ${U.stateLabel(job.state)}: stop or discard the job first`)}" title="${esc(`${U.jobKey(job)} is ${U.stateLabel(job.state)}: stop or discard the job first`)}"` : 'title="Remove container (docker rm -f)"'}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button></div></li>`;
    }
    const pill = (tone, html, title = '') => `<span class="pill tone-${tone}"${title ? ` title="${esc(title)}"` : ''}>${html}</span>`;

    function jobNode(j) {
        const [glyph, tone, word] = KIND[j.kind] || KIND.agent;
        const live = tone === 'running';
        const stale = j.heartbeat_at && Date.now() / 1000 - Number(j.heartbeat_at) > 90;
        const containers = j.containers || [];
        return `<li class="work-tree-job" data-activity-job="${esc(j.id)}">
            <div class="row wrap work-tree-head">
                <span class="tone-${tone} ${live ? 'status-icon active' : 'status-icon'}" aria-hidden="true">${glyph}</span>
                <a href="#/job/${esc(j.id)}" class="mono" style="font-weight:600">${esc(U.jobKey(j))}</a>
                ${U.statePill(j.state)}
                ${pill(tone, `${esc(word)}${j.stage && j.stage !== j.kind && j.stage !== word ? ` · ${esc(j.stage)}` : ''}`)}
                <span class="grow ellipsis small" title="${esc(j.title)}">${esc(j.title)}</span>
                ${j.since ? `<span class="muted small num" title="in this state since ${esc(U.when(j.since))}">${esc(since(j.since))}</span>` : ''}
                <span class="small num">${esc(U.money(j.cost_usd))}</span>
                <button type="button" class="btn sm" data-activity-log="${esc(j.id)}"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4M10 9H8M16 13H8M16 17H8"/></svg>Log<span class="sr-only"> of ${esc(U.jobKey(j))}</span></button>
            </div>
            <div class="row wrap small work-tree-meta">
                ${j.worker_id ? `<span class="muted">worker <code>${esc(String(j.worker_id).slice(0, 18))}</code></span>` : '<span class="muted">no worker</span>'}
                ${j.heartbeat_at ? `<span class="${stale ? 'tone-warn' : 'muted'}">${stale ? '<span aria-hidden="true">◆ </span>' : ''}heartbeat ${esc(U.age(Date.now() / 1000 - Number(j.heartbeat_at)))} ago${stale ? ' (stale)' : ''}</span>` : ''}
                ${j.last ? `<span class="mono muted ellipsis grow" title="${esc(U.stripAnsi(j.last))}">› ${esc(U.stripAnsi(j.last))}</span>` : ''}
            </div>
            ${containers.length ? `<ul class="plain work-tree-children" aria-label="Containers of ${esc(U.jobKey(j))}">${containers.map(c => containerRow(c, j)).join('')}</ul>`
                : `<p class="small muted work-tree-children work-tree-none">${j.kind === 'waiting' ? 'Waiting for you; no container.' : 'No container (runs on the host or not started yet).'}</p>`}
        </li>`;
    }

    function render() {
        if (!active) return;
        if (!data) {
            bodyEl.innerHTML = lastError ? `<div class="error-box" role="alert">✗ ${esc(lastError)}</div>`
                : '<div class="loading" role="status"><span class="status-icon active tone-running" aria-hidden="true">●</span>Loading activity…</div>';
            return;
        }
        const engine = String(data.engine || '');
        const [eTone, eText] = ENGINE[engine] || ['muted', engine ? `docker ${engine}` : 'engine unknown'];
        engineEl.innerHTML = legacy ? '' : pill(eTone, `<span aria-hidden="true">⬢</span> ${esc(eText)}`, 'Container engine');
        updatedEl.textContent = data.generated_at ? `updated ${U.when(data.generated_at).slice(11)}` : '';
        const c = data.counts || {};
        const jobs = data.jobs || [], orphans = data.orphans || [];
        const focusKey = document.activeElement?.closest?.('[data-container], [data-activity-job]') && document.activeElement.matches('button')
            ? [document.activeElement.dataset.removeContainer, document.activeElement.dataset.activityLog] : null;
        bodyEl.innerHTML = `
            ${lastError ? `<div class="error-box small" role="alert">✗ ${esc(lastError)} — showing the last data.</div>` : ''}
            ${legacy ? '<div class="note-box small" role="note"><span class="tone-warn" aria-hidden="true">◆</span> This mucli server has no activity endpoint yet: showing running jobs only, without containers.</div>' : ''}
            ${engine === 'down' ? '<div class="note-box small" role="note"><span class="tone-danger" aria-hidden="true">✗</span> Docker is not reachable: container stats are unavailable.</div>' : ''}
            <ul class="plain row wrap work-activity-counts" aria-label="Activity summary">
                <li>${pill(c.agents ? 'running' : 'muted', `<span aria-hidden="true" class="${c.agents ? 'status-icon active' : ''}">●</span>${Number(c.agents || 0)} agent${c.agents === 1 ? '' : 's'}`)}</li>
                <li>${pill(c.checks ? 'running' : 'muted', `<span aria-hidden="true" class="${c.checks ? 'status-icon active' : ''}">●</span>${Number(c.checks || 0)} check${c.checks === 1 ? '' : 's'}`)}</li>
                <li>${pill(c.needs_you ? 'warn' : 'muted', `<span aria-hidden="true">◆</span>${Number(c.needs_you || 0)} need${c.needs_you === 1 ? 's' : ''} you`)}</li>
                <li>${pill('accent', `<span aria-hidden="true">⬢</span>${Number(c.containers || 0)} container${c.containers === 1 ? '' : 's'}`)}</li>
            </ul>
            <section class="panel" aria-labelledby="activity-jobs-h"><header><h2 id="activity-jobs-h">Agents, checks &amp; containers</h2><span class="badge tone-running">${jobs.length}</span></header>
                <div class="panel-body">${jobs.length ? `<ul class="plain work-tree">${jobs.map(jobNode).join('')}</ul>` : '<p class="empty">No agent or check is running.</p>'}</div></section>
            <section class="panel" aria-labelledby="activity-orphans-h"><header><h2 id="activity-orphans-h">Orphan containers</h2><span class="badge ${orphans.length ? 'tone-warn' : ''}">${orphans.length}</span><span class="spacer"></span><span class="small muted">job containers whose job is no longer active</span></header>
                <div class="panel-body">${orphans.length ? `<ul class="plain work-tree-children is-flat">${orphans.map(o => containerRow(o, null)).join('')}</ul>` : '<p class="empty">No orphan containers.</p>'}</div></section>`;
        bodyEl.querySelectorAll('[data-remove-container]').forEach(b => { b.onclick = () => b.dataset.locked ? toast(`Can't remove ${b.dataset.removeContainer}: ${b.dataset.locked}`, 'warn') : remove(b.dataset.removeContainer, jobs.find(j => (j.containers || []).some(x => (x.name || x.id) === b.dataset.removeContainer))); });
        bodyEl.querySelectorAll('[data-activity-log]').forEach(b => { b.onclick = () => openLog(jobs.find(j => j.id === b.dataset.activityLog)); });
        if (focusKey) {
            const [rm, log] = focusKey;
            (rm ? bodyEl.querySelector(`[data-remove-container="${CSS.escape(rm)}"]`) : log ? bodyEl.querySelector(`[data-activity-log="${CSS.escape(log)}"]`) : null)?.focus();
        }
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

    /* ------------------------------------------------------------ live log drawer */
    function openLog(job) {
        if (!job || !ui?.dialog) return;
        const wrap = document.createElement('div');
        wrap.className = 'col work-log-drawer';
        wrap.innerHTML = `<div class="row wrap small"><span data-log-source class="muted"></span><span data-log-trunc class="muted"></span><span class="spacer"></span>
            <label class="check small"><input type="checkbox" data-log-follow checked> Follow</label>
            <button type="button" class="btn sm" data-log-refresh><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>Refresh</button></div>
            <pre class="work-log work-log-tall" role="log" aria-label="Log of ${esc(U.jobKey(job))}" tabindex="0" data-log-text><span class="muted">Reading log…</span></pre>
            <a class="small" href="#/job/${esc(job.id)}?tab=session" data-log-open>Open ${esc(U.jobKey(job))} →</a>`;
        let closed = false, logTimer = 0;
        const d = ui.dialog({ title: `Log · ${U.jobKey(job)}`, body: wrap, wide: true, sheet: true, onClose: () => { closed = true; clearTimeout(logTimer); } });
        const pre = wrap.querySelector('[data-log-text]');
        const follow = wrap.querySelector('[data-log-follow]');
        const load = async () => {
            clearTimeout(logTimer);
            if (closed) return;
            try {
                const r = await api(`/api/jobs/vm/activity/logs/${encodeURIComponent(job.id)}?tail_bytes=65536`);
                if (closed) return;
                const atEnd = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
                pre.innerHTML = r.text ? U.ansi(r.text) : '<span class="muted">The log is empty.</span>';
                wrap.querySelector('[data-log-source]').textContent = r.source ? `source: ${r.source}` : '';
                wrap.querySelector('[data-log-trunc]').textContent = r.truncated ? '· showing the last 64 KB' : '';
                if (follow.checked && (atEnd || !pre.dataset.loaded)) pre.scrollTop = pre.scrollHeight;
                pre.dataset.loaded = '1';
            } catch (e) {
                if (closed) return;
                pre.innerHTML = `<span class="tone-danger">✗ ${esc(/404|not found/i.test(e.message) ? 'This mucli server has no activity log endpoint yet.' : e.message)}</span>`;
                return;
            }
            if (ACTIVE.has(job.state) && document.visibilityState === 'visible') logTimer = setTimeout(load, 3000);
        };
        wrap.querySelector('[data-log-refresh]').onclick = load;
        wrap.querySelector('[data-log-open]').onclick = () => d.close();
        load();
    }

    /* ------------------------------------------------------------ wiring */
    refreshBtn?.addEventListener('click', () => { tick(); });
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && active) tick(); else clearTimeout(timer); });
    window.addEventListener('mu:work-route', e => {
        const next = e.detail?.name === 'activity';
        if (next === active) { if (next) tick(); return; }
        active = next;
        if (active) {
            ui?.register('page:activity', [{ id: 'activity:refresh', label: 'Refresh activity', group: 'Activity', run: tick }], 10);
            render(); tick();
        } else { clearTimeout(timer); ui?.register('page:activity', []); }
    });
})();
