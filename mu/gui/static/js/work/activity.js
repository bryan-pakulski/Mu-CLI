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

    /* ------------------------------------------------------------ render */
    function containerRow(c, job) {
        const key = c.name || c.id;
        const s = samples.get(key) || { cpu: [], mem: [] };
        const running = /^(running|up)/i.test(String(c.status || c.status_text || ''));
        const locked = job && ACTIVE.has(job.state);
        const status = pill(running ? 'running' : /exited|dead|created/i.test(String(c.status || '')) ? 'muted' : 'warn',
            `${glyph(running ? '●' : '○', running)}<span>${esc(c.status_text || c.status || 'unknown')}</span>`);
        return `<li class="work-ctr" data-container="${esc(key)}">
            <div class="work-ctr-name"><div class="work-toolbar"><span class="work-accent" aria-hidden="true">⬢</span><span class="work-mono work-ellipsis" title="${esc(key)}">${esc(key)}</span>${c.role === 'engine' ? pill('accent', 'docker engine', 'Per-job Docker engine (Sysbox)') : c.role === 'broker' ? pill('accent', 'run_in_environment', 'Sibling container for another build environment') : c.stage ? pill('accent', esc(c.stage)) : ''}${c.binding ? pill('muted', `<span aria-hidden="true">⬢</span> ${esc(c.binding)}`, 'build environment') : ''}${c.gpus ? pill('warn', `gpus ${esc(c.gpus)}`, 'GPU request') : ''}</div>
                <div class="work-meta work-ellipsis" title="${esc(c.image || '')}">${esc(c.image || '')}${c.started_at ? ` · ${running ? 'up' : 'started'} ${esc(since(c.started_at))}${running ? '' : ' ago'}` : ''}</div></div>
            <div class="work-ctr-state">${status}</div>
            <div class="work-ctr-metric"><span class="work-ctr-label work-meta">CPU</span>${sparkline(s.cpu, 'running', `CPU of ${key}`, pct, 100)}<span class="work-num">${esc(pct(c.cpu_percent))}</span></div>
            <div class="work-ctr-metric"><span class="work-ctr-label work-meta">Mem</span>${sparkline(s.mem, 'accent', `Memory of ${key}`, bytes, c.mem_limit || 0)}<span class="work-num" title="${c.mem_limit ? `limit ${esc(bytes(c.mem_limit))}` : ''}">${esc(bytes(c.mem_bytes))}</span></div>
            <div class="work-ctr-actions"><button type="button" class="w-btn" data-variant="icon" data-size="sm" data-tone="danger" data-remove-container="${esc(key)}" aria-label="Remove ${esc(key)}"
                ${locked ? `aria-disabled="true" data-locked="${esc(`${U.jobKey(job)} is ${U.stateLabel(job.state)}: stop or discard the job first`)}" title="${esc(`${U.jobKey(job)} is ${U.stateLabel(job.state)}: stop or discard the job first`)}"` : 'title="Remove container (docker rm -f)"'}>
                <svg class="w-icon" aria-hidden="true" focusable="false"><use href="/static/icons/wash.svg#i-trash-2"></use></svg></button></div></li>`;
    }
    const TONE = { warn: 'attention', muted: 'neutral' };
    const pill = (tone, html, title = '') => `<span class="w-chip" data-tone="${TONE[tone] || tone}"${title ? ` title="${esc(title)}"` : ''}>${html}</span>`;
    const glyph = (g, live = false) => (live ? '<span class="w-inkdrop" data-size="sm" aria-hidden="true"></span>' : `<span class="w-chip__glyph" aria-hidden="true">${g}</span>`);

    function jobNode(j) {
        const [glyph, tone, word] = KIND[j.kind] || KIND.agent;
        const live = tone === 'running';
        const stale = j.heartbeat_at && Date.now() / 1000 - Number(j.heartbeat_at) > 90;
        const containers = j.containers || [];
        return `<li class="work-tree-job" data-activity-job="${esc(j.id)}">
            <div class="work-toolbar work-tree-head">
                ${U.statusIcon(live ? 'active' : tone === 'warn' ? 'waiting' : 'pending', word)}
                <a href="#/job/${esc(j.id)}" class="work-job-row__key">${esc(U.jobKey(j))}</a>
                ${U.statePill(j.state)}
                ${pill(tone, `<span>${esc(word)}${j.stage && j.stage !== j.kind && j.stage !== word ? ` · ${esc(j.stage)}` : ''}</span>`)}
                <span class="work-grow work-ellipsis" title="${esc(j.title)}">${esc(j.title)}</span>
                ${j.since ? `<span class="work-meta work-num" title="in this state since ${esc(U.when(j.since))}">${esc(since(j.since))}</span>` : ''}
                <span class="work-num">${esc(U.money(j.cost_usd))}</span>
                <button type="button" class="w-btn" data-variant="secondary" data-size="sm" data-activity-log="${esc(j.id)}"><svg class="w-icon" aria-hidden="true" focusable="false"><use href="/static/icons/wash.svg#i-file-text"></use></svg><span>Log</span><span class="w-sr-only"> of ${esc(U.jobKey(j))}</span></button>
            </div>
            <div class="work-toolbar work-meta work-tree-meta">
                ${j.worker_id ? `<span>worker <code>${esc(String(j.worker_id).slice(0, 18))}</code></span>` : '<span>no worker</span>'}
                ${j.heartbeat_at ? `<span${stale ? ' class="work-warn"' : ''}>${stale ? '<span aria-hidden="true">◆ </span>' : ''}heartbeat ${esc(U.age(Date.now() / 1000 - Number(j.heartbeat_at)))} ago${stale ? ' (stale)' : ''}</span>` : ''}
                ${j.last ? `<span class="work-mono work-ellipsis work-grow" title="${esc(U.stripAnsi(j.last))}">› ${esc(U.stripAnsi(j.last))}</span>` : ''}
            </div>
            ${containers.length ? `<ul class="work-list work-tree-children" aria-label="Containers of ${esc(U.jobKey(j))}">${containers.map(c => containerRow(c, j)).join('')}</ul>`
                : `<p class="work-meta work-tree-children work-tree-none">${j.kind === 'waiting' ? 'Waiting for you; no container.' : 'No container (runs on the host or not started yet).'}</p>`}
        </li>`;
    }

    function render() {
        if (!active) return;
        if (!data) {
            bodyEl.innerHTML = lastError ? `<div class="w-banner" data-tone="danger" role="alert"><span class="w-banner__glyph" aria-hidden="true">✗</span><p class="w-banner__msg">${esc(lastError)}</p></div>`
                : '<div class="work-loading" role="status"><span class="w-inkdrop" aria-hidden="true"></span>Loading activity…</div>';
            return;
        }
        const engine = String(data.engine || '');
        const [eTone, eText] = ENGINE[engine] || ['muted', engine ? `docker ${engine}` : 'engine unknown'];
        engineEl.innerHTML = legacy ? '' : pill(eTone, `${glyph('⬢')}<span>${esc(eText)}</span>`, 'Container engine');
        updatedEl.textContent = data.generated_at ? `updated ${U.when(data.generated_at).slice(11)}` : '';
        const c = data.counts || {};
        const jobs = data.jobs || [], orphans = data.orphans || [];
        const focusKey = document.activeElement?.closest?.('[data-container], [data-activity-job]') && document.activeElement.matches('button')
            ? [document.activeElement.dataset.removeContainer, document.activeElement.dataset.activityLog] : null;
        bodyEl.innerHTML = `
            ${lastError ? `<div class="w-banner" data-tone="danger" role="alert"><span class="w-banner__glyph" aria-hidden="true">✗</span><p class="w-banner__msg">${esc(lastError)} — showing the last data.</p></div>` : ''}
            ${legacy ? '<div class="w-banner" data-tone="caution" role="note"><span class="w-banner__glyph" aria-hidden="true">◆</span><p class="w-banner__msg">This mucli server has no activity endpoint yet: showing running jobs only, without containers.</p></div>' : ''}
            ${engine === 'down' ? '<div class="w-banner" data-tone="danger" role="note"><span class="w-banner__glyph" aria-hidden="true">✗</span><p class="w-banner__msg">Docker is not reachable: container stats are unavailable.</p></div>' : ''}
            <ul class="w-chip-row work-activity-counts" aria-label="Activity summary">
                <li>${pill(c.agents ? 'running' : 'muted', `${glyph('●', !!c.agents)}<span>${Number(c.agents || 0)} agent${c.agents === 1 ? '' : 's'}</span>`)}</li>
                <li>${pill(c.checks ? 'running' : 'muted', `${glyph('●', !!c.checks)}<span>${Number(c.checks || 0)} check${c.checks === 1 ? '' : 's'}</span>`)}</li>
                <li>${pill(c.needs_you ? 'warn' : 'muted', `${glyph('◆')}<span>${Number(c.needs_you || 0)} need${c.needs_you === 1 ? 's' : ''} you</span>`)}</li>
                <li>${pill('accent', `${glyph('⬢')}<span>${Number(c.containers || 0)} container${c.containers === 1 ? '' : 's'}</span>`)}</li>
            </ul>
            <section class="w-card work-card" aria-labelledby="activity-jobs-h"><header class="w-card__head"><span class="w-eyebrow">Live</span><h2 class="w-card__title" id="activity-jobs-h">Agents, checks &amp; containers</h2><span class="w-count" data-tone="running">${jobs.length}</span></header>
                <div class="w-card__body">${jobs.length ? `<ul class="work-list work-tree">${jobs.map(jobNode).join('')}</ul>` : '<div class="w-empty" data-tone="running" data-size="sm"><div class="w-empty__art" aria-hidden="true"><svg class="w-icon" focusable="false"><use href="/static/icons/wash.svg#i-activity"></use></svg></div><h3 class="w-empty__title">All quiet</h3><p class="w-empty__body">No agent or check is running.</p></div>'}</div></section>
            <section class="w-card work-card" aria-labelledby="activity-orphans-h"><header class="w-card__head"><span class="w-eyebrow">Cleanup</span><h2 class="w-card__title" id="activity-orphans-h">Orphan containers</h2><span class="w-count"${orphans.length ? ' data-tone="attention"' : ''}>${orphans.length}</span><span class="w-card__meta">job containers whose job is no longer active</span></header>
                <div class="w-card__body">${orphans.length ? `<ul class="work-list work-tree-children is-flat">${orphans.map(o => containerRow(o, null)).join('')}</ul>` : '<p class="work-empty-line">No orphan containers.</p>'}</div></section>`;
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
        wrap.className = 'work-stack-tight work-log-drawer';
        wrap.innerHTML = `<div class="work-toolbar work-meta"><span data-log-source></span><span data-log-trunc></span><span class="work-spacer"></span>
            <label class="w-check"><input class="w-check__input" type="checkbox" data-log-follow checked><span>Follow</span></label>
            <button type="button" class="w-btn" data-variant="secondary" data-size="sm" data-log-refresh><svg class="w-icon" aria-hidden="true" focusable="false"><use href="/static/icons/wash.svg#i-refresh-cw"></use></svg><span>Refresh</span></button></div>
            <pre class="work-log work-log-tall" role="log" aria-label="Log of ${esc(U.jobKey(job))}" tabindex="0" data-log-text><span class="work-meta">Reading log…</span></pre>
            <a href="#/job/${esc(job.id)}?tab=session" data-log-open>Open ${esc(U.jobKey(job))}<span aria-hidden="true"> ›</span></a>`;
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
                pre.innerHTML = r.text ? U.ansi(r.text) : '<span class="work-meta">The log is empty.</span>';
                wrap.querySelector('[data-log-source]').textContent = r.source ? `source: ${r.source}` : '';
                wrap.querySelector('[data-log-trunc]').textContent = r.truncated ? '· showing the last 64 KB' : '';
                if (follow.checked && (atEnd || !pre.dataset.loaded)) pre.scrollTop = pre.scrollHeight;
                pre.dataset.loaded = '1';
            } catch (e) {
                if (closed) return;
                pre.innerHTML = `<span class="work-del">✗ ${esc(/404|not found/i.test(e.message) ? 'This mucli server has no activity log endpoint yet.' : e.message)}</span>`;
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
