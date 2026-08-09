(() => {
    'use strict';

    const els = {
        main: document.getElementById('jt-main'),
        loading: document.getElementById('jt-loading'),
        empty: document.getElementById('jt-empty'),
        error: document.getElementById('jt-error'),
        jobSelect: document.getElementById('jt-job-select'),
        compareSelect: document.getElementById('jt-compare-select'),
        title: document.getElementById('jt-job-title'),
        status: document.getElementById('jt-job-status'),
        overview: document.getElementById('jt-overview'),
        signals: document.getElementById('jt-signals'),
        compareSection: document.getElementById('jt-compare-section'),
        compareCaption: document.getElementById('jt-compare-caption'),
        comparison: document.getElementById('jt-comparison'),
        phaseCanvas: document.getElementById('jt-phase-canvas'),
        phaseLegend: document.getElementById('jt-phase-legend'),
        phaseTable: document.getElementById('jt-phase-table'),
        attemptCanvas: document.getElementById('jt-attempt-canvas'),
        attemptTable: document.getElementById('jt-attempt-table'),
        tools: document.getElementById('jt-tools'),
        verifications: document.getElementById('jt-verifications'),
        incidents: document.getElementById('jt-incidents'),
        eventCount: document.getElementById('jt-event-count'),
        eventQuery: document.getElementById('jt-event-query'),
        eventCategory: document.getElementById('jt-event-category'),
        eventSeverity: document.getElementById('jt-event-severity'),
        events: document.getElementById('jt-events'),
        refresh: document.getElementById('jt-refresh'),
        theme: document.getElementById('jt-theme'),
        debugExport: document.getElementById('jt-debug-export'),
        exportAnalysis: document.getElementById('jt-export-analysis'),
    };

    const state = { jobs: [], analysis: null, comparison: null, resizeTimer: null };

    const PHASE_COLORS = {
        queued: '#8299ad', preparing: '#8ba9c6', running: '#6f9eb7',
        needs_human: '#b59a7f', verifying: '#789f8d', ready_for_review: '#88a996',
        recovering: '#a8a0b7', conflicted: '#b88c8f', failed: '#bf747a',
        timed_out: '#ae8b74', budget_exceeded: '#b6907a', environment_error: '#bd7f88',
        cancelled: '#7f8994', merged: '#7fa48e',
    };

    function esc(value) {
        return String(value ?? '')
            .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;').replaceAll("'", '&#039;');
    }
    function statusLabel(value) {
        return String(value || '').replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase());
    }
    function fmtNum(value, digits = 0) {
        const n = Number(value || 0);
        return Number.isFinite(n) ? n.toLocaleString(undefined, { maximumFractionDigits: digits }) : '—';
    }
    function fmtMoney(value) { return `$${Number(value || 0).toFixed(2)}`; }
    function fmtDuration(value) {
        let seconds = Math.max(0, Number(value || 0));
        if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
        if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
        const h = Math.floor(seconds / 3600); seconds -= h * 3600;
        const m = Math.floor(seconds / 60); const s = Math.round(seconds - m * 60);
        return h ? `${h}h ${m}m ${s}s` : `${m}m ${s}s`;
    }
    function fmtWhen(epoch) {
        if (!epoch) return '—';
        try { return new Date(Number(epoch) * 1000).toLocaleString(); } catch (_) { return '—'; }
    }
    function tokenCompact(tokens) {
        const entries = Object.entries(tokens || {}).filter(([, v]) => Number.isFinite(Number(v)) && Number(v) !== 0);
        if (!entries.length) return '—';
        return entries.slice(0, 4).map(([k, v]) => `${k}:${fmtNum(v)}`).join(' · ');
    }
    function css(name, fallback) {
        const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
        return value || fallback;
    }
    async function api(url) {
        const response = await fetch(url);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.detail || `${response.status} ${response.statusText}`);
        return data;
    }

    async function loadJobs() {
        const data = await api('/api/jobs/history?scope=all&archive=all&limit=1000&sort=created&order=desc');
        state.jobs = data.jobs || [];
        const options = state.jobs.map(job => {
            const runtime = [job.provider, job.model].filter(Boolean).join(' · ');
            const meta = [statusLabel(job.status), runtime, job.archived ? 'archived' : ''].filter(Boolean).join(' · ');
            return `<option value="${esc(job.id)}">${esc(job.title)} · ${esc(job.id.slice(0, 8))} · ${esc(meta)}</option>`;
        }).join('');
        els.jobSelect.innerHTML = '<option value="">— select a job —</option>' + options;
        els.compareSelect.innerHTML = '<option value="">compare with…</option>' + options;
    }

    function card(label, value, cls = '') {
        return `<div class="trace-card ${cls}"><div class="tc-label">${esc(label)}</div><div class="tc-val">${esc(value)}</div></div>`;
    }

    function renderOverview(a) {
        const s = a.summary || {};
        const tokenTotal = Object.values(s.tokens || {}).reduce((sum, v) => sum + (Number(v) || 0), 0);
        els.overview.innerHTML = [
            card('status', statusLabel(a.job.status)),
            card('wall time', fmtDuration(s.elapsed_seconds)),
            card('active time', fmtDuration(s.active_seconds)),
            card('waiting', fmtDuration(s.waiting_seconds), (s.waiting_ratio || 0) > .35 ? 'trace-card--warn' : ''),
            card('attempts', String(s.attempts || 0)),
            card('additional attempts', String(s.retries || 0), (s.retries || 0) > 0 ? 'trace-card--warn' : ''),
            card('cost', fmtMoney(s.cost_usd)),
            card('tokens recorded', tokenTotal ? fmtNum(tokenTotal) : '—'),
            card('tool calls', fmtNum(s.tool_calls)),
            card('unique tools', fmtNum(s.unique_tools)),
            card('human gates', fmtNum(s.human_gates), (s.human_gates || 0) > 0 ? 'trace-card--warn' : ''),
            card('verification runs', fmtNum(s.verification_runs)),
            card('verification failures', fmtNum(s.verification_failures), (s.verification_failures || 0) > 0 ? 'trace-card--warn' : ''),
            card('runtime failures', fmtNum(s.failures), (s.failures || 0) > 0 ? 'trace-card--warn' : ''),
            card('changed files', fmtNum(s.changed_files)),
            card('diff', `+${fmtNum(s.additions)} / -${fmtNum(s.deletions)}`),
            card('cost / attempt', s.cost_per_attempt == null ? '—' : fmtMoney(s.cost_per_attempt)),
            card('tools / active min', s.tool_calls_per_active_minute == null ? '—' : Number(s.tool_calls_per_active_minute).toFixed(1)),
        ].join('');

        const firstPass = s.first_pass_verification;
        const firstPassValue = firstPass == null ? 'not measured' : firstPass ? 'passed' : 'needed another verification pass';
        const firstTone = firstPass == null ? '' : firstPass ? 'good' : 'warn';
        const autonomyTone = (s.human_gates || 0) === 0 ? 'good' : (s.human_gates || 0) <= 2 ? 'warn' : 'bad';
        const failureTone = (s.failures || 0) === 0 ? 'good' : 'bad';
        const waitingPct = s.elapsed_seconds ? (Number(s.waiting_seconds || 0) / Number(s.elapsed_seconds) * 100) : 0;
        els.signals.innerHTML = `
            <div class="jt-signal" data-tone="${firstTone}"><div class="jt-signal-label">First verification</div><div class="jt-signal-value">${esc(firstPassValue)}</div><div class="jt-signal-detail">${fmtNum(s.verification_passes)} passed · ${fmtNum(s.verification_failures)} failed verification runs</div></div>
            <div class="jt-signal" data-tone="${autonomyTone}"><div class="jt-signal-label">Autonomy</div><div class="jt-signal-value">${fmtNum(s.human_gates)} human gate${Number(s.human_gates) === 1 ? '' : 's'}</div><div class="jt-signal-detail">${fmtNum(s.human_responses)} recorded human responses</div></div>
            <div class="jt-signal" data-tone="${failureTone}"><div class="jt-signal-label">Execution stability</div><div class="jt-signal-value">${fmtNum(s.failures)} failure signal${Number(s.failures) === 1 ? '' : 's'}</div><div class="jt-signal-detail">${fmtNum(s.recoveries)} recovery transition${Number(s.recoveries) === 1 ? '' : 's'}</div></div>
            <div class="jt-signal" data-tone="${waitingPct > 35 ? 'warn' : 'good'}"><div class="jt-signal-label">Waiting share</div><div class="jt-signal-value">${waitingPct.toFixed(1)}%</div><div class="jt-signal-detail">queue + human-blocked time relative to full job lifetime</div></div>
        `;
    }

    function setupCanvas(canvas, height) {
        const dpr = Math.max(1, window.devicePixelRatio || 1);
        const width = Math.max(320, canvas.parentElement?.clientWidth ? canvas.parentElement.clientWidth - 24 : 1000);
        canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
        canvas.style.width = `${width}px`; canvas.style.height = `${height}px`;
        const ctx = canvas.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        return { ctx, width, height };
    }

    function renderPhaseChart(a) {
        const { ctx, width, height } = setupCanvas(els.phaseCanvas, 210);
        ctx.clearRect(0, 0, width, height);
        const intervals = (a.phase_intervals || []).filter(x => Number(x.duration_seconds || 0) >= 0);
        const total = Math.max(0.001, Number(a.summary?.elapsed_seconds || intervals.reduce((s, x) => s + Number(x.duration_seconds || 0), 0)));
        const left = 32, right = 22, top = 56, barH = 44, plotW = width - left - right;
        ctx.font = '11px system-ui'; ctx.fillStyle = css('--text-dim', '#86919c');
        ctx.fillText('job lifetime', left, 28);
        let cursor = left;
        for (const interval of intervals) {
            const w = Math.max(interval.duration_seconds > 0 ? 1 : 0, plotW * Number(interval.duration_seconds || 0) / total);
            ctx.fillStyle = PHASE_COLORS[interval.status] || '#87919a';
            ctx.globalAlpha = .78; ctx.fillRect(cursor, top, w, barH); ctx.globalAlpha = 1;
            if (w > 64) {
                ctx.save(); ctx.beginPath(); ctx.rect(cursor + 3, top, w - 6, barH); ctx.clip();
                ctx.fillStyle = '#eef2f6'; ctx.font = '10px ui-monospace, monospace';
                ctx.fillText(statusLabel(interval.status), cursor + 7, top + 18);
                ctx.fillText(fmtDuration(interval.duration_seconds), cursor + 7, top + 33); ctx.restore();
            }
            cursor += w;
        }
        ctx.strokeStyle = css('--hairline', 'rgba(150,160,170,.22)'); ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(left, top + barH + 24); ctx.lineTo(width - right, top + barH + 24); ctx.stroke();
        for (let i = 0; i <= 4; i++) {
            const x = left + plotW * i / 4; const t = total * i / 4;
            ctx.fillStyle = css('--text-dimmer', '#6f7880'); ctx.font = '9px ui-monospace, monospace';
            ctx.fillText(fmtDuration(t), x - (i ? 10 : 0), top + barH + 42);
        }
        const unique = [...new Set(intervals.map(x => x.status))];
        els.phaseLegend.innerHTML = unique.map(status => `<span class="jt-legend-item"><span class="jt-legend-swatch" style="background:${PHASE_COLORS[status] || '#87919a'}"></span>${esc(statusLabel(status))}</span>`).join('');
        els.phaseTable.innerHTML = (a.phase_breakdown || []).map(row => `<tr><td>${esc(statusLabel(row.status))}</td><td>${esc(fmtDuration(row.seconds))}</td><td>${Number(row.percent || 0).toFixed(1)}%</td><td>${fmtNum(row.occurrences)}</td></tr>`).join('');
    }

    function renderAttemptChart(a) {
        const { ctx, width, height } = setupCanvas(els.attemptCanvas, 240);
        ctx.clearRect(0, 0, width, height);
        const attempts = a.attempts || [];
        if (!attempts.length) {
            ctx.fillStyle = css('--text-dim', '#87919a'); ctx.font = '12px system-ui'; ctx.fillText('No implementation attempts recorded.', 28, 42); return;
        }
        const left = 48, right = 42, top = 28, bottom = 42, plotW = width - left - right, plotH = height - top - bottom;
        const maxDur = Math.max(1, ...attempts.map(x => Number(x.duration_seconds || 0)));
        const maxCost = Math.max(.01, ...attempts.map(x => Number(x.cost_usd || 0)));
        const slot = plotW / attempts.length; const barW = Math.min(50, slot * .52);
        ctx.strokeStyle = css('--hairline', 'rgba(150,160,170,.22)'); ctx.beginPath(); ctx.moveTo(left, top + plotH); ctx.lineTo(width - right, top + plotH); ctx.stroke();
        const points = [];
        attempts.forEach((attempt, index) => {
            const cx = left + slot * (index + .5);
            const bh = plotH * Number(attempt.duration_seconds || 0) / maxDur;
            ctx.fillStyle = attempt.status === 'completed' ? '#789f8d' : attempt.status === 'needs_human' ? '#b59a7f' : '#bd7f88';
            ctx.globalAlpha = .66; ctx.fillRect(cx - barW / 2, top + plotH - bh, barW, bh); ctx.globalAlpha = 1;
            const py = top + plotH - plotH * Number(attempt.cost_usd || 0) / maxCost;
            points.push([cx, py]);
            ctx.fillStyle = css('--text-dimmer', '#717b84'); ctx.font = '9px ui-monospace, monospace'; ctx.textAlign = 'center'; ctx.fillText(`#${attempt.number}`, cx, height - 16);
        });
        ctx.strokeStyle = '#c991a7'; ctx.lineWidth = 1.7; ctx.beginPath();
        points.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)); ctx.stroke();
        points.forEach(([x, y]) => { ctx.beginPath(); ctx.fillStyle = '#c991a7'; ctx.arc(x, y, 3.4, 0, Math.PI * 2); ctx.fill(); });
        ctx.textAlign = 'left'; ctx.fillStyle = css('--text-dim', '#87919a'); ctx.font = '9px system-ui'; ctx.fillText(`duration max ${fmtDuration(maxDur)}`, left, 12);
        ctx.textAlign = 'right'; ctx.fillText(`cost max ${fmtMoney(maxCost)}`, width - right, 12); ctx.textAlign = 'left';

        els.attemptTable.innerHTML = attempts.map(row => `<tr>
            <td>#${row.number}</td><td>${esc(statusLabel(row.status))}</td><td>${esc(fmtDuration(row.duration_seconds))}</td>
            <td>${esc(fmtMoney(row.cost_usd))}</td><td>${fmtNum(row.tool_calls)}</td><td>${fmtNum(row.agent_messages)}</td>
            <td>${fmtNum(row.runtime_errors)}</td><td title="${esc(JSON.stringify(row.tokens || {}))}">${esc(tokenCompact(row.tokens))}</td>
        </tr>`).join('');
    }

    function renderTools(a) {
        const tools = a.tools || [];
        if (!tools.length) { els.tools.innerHTML = '<p class="trace-muted">No tool-call telemetry recorded.</p>'; return; }
        const max = Math.max(1, ...tools.map(x => Number(x.count || 0)));
        els.tools.innerHTML = tools.slice(0, 24).map(tool => `<div class="jt-tool-row">
            <div class="jt-tool-name" title="${esc(tool.name)}">${esc(tool.name)}</div>
            <div class="jt-tool-track"><div class="jt-tool-fill" style="width:${(Number(tool.count || 0) / max * 100).toFixed(1)}%"></div></div>
            <div class="jt-tool-value">${fmtNum(tool.count)}</div>
        </div>`).join('');
    }

    function renderVerifications(a) {
        const rows = a.verifications || [];
        if (!rows.length) { els.verifications.innerHTML = '<p class="trace-muted">No deterministic verification runs recorded.</p>'; return; }
        els.verifications.innerHTML = rows.map((row, index) => `<div class="jt-verification" data-pass="${row.passed}">
            <div class="jt-verification-mark">${row.passed ? '✓' : '×'}</div>
            <div class="jt-verification-main"><div class="jt-verification-title">Run ${index + 1} · ${esc(statusLabel(row.status))}</div>
            <div class="jt-verification-meta">${fmtNum(row.checks_passed)}/${fmtNum(row.checks)} checks · ${fmtNum(row.changed_files)} files · +${fmtNum(row.additions)} / -${fmtNum(row.deletions)}${row.dirty ? ' · dirty' : ''}</div></div>
            <div class="jt-verification-time">${esc(fmtDuration(row.duration_seconds))}</div>
        </div>`).join('');
    }

    function incidentRows(rows, kind) {
        if (!rows.length) return '<p class="trace-muted">None recorded.</p>';
        return `<div class="jt-incident-list">${rows.slice().reverse().slice(0, 30).map(row => `<div class="jt-incident">
            <div class="jt-incident-head"><span class="jt-incident-kind">${esc(kind === 'gate' ? (row.reason || 'Human gate') : statusLabel(row.event_type || 'Failure'))}</span><span class="jt-incident-time">${esc(fmtWhen(row.created_at))}</span></div>
            <div class="jt-incident-copy">${esc(kind === 'gate' ? JSON.stringify(row.payload || {}).slice(0, 600) : (row.summary || ''))}</div>
        </div>`).join('')}</div>`;
    }
    function renderIncidents(a) {
        els.incidents.innerHTML = `<div class="jt-incident-group"><div class="jt-incident-title">Human gates · ${(a.human_gates || []).length}</div>${incidentRows(a.human_gates || [], 'gate')}</div>
            <div class="jt-incident-group"><div class="jt-incident-title">Failures · ${(a.failures || []).length}</div>${incidentRows(a.failures || [], 'failure')}</div>`;
    }

    function renderEvents() {
        const a = state.analysis; if (!a) return;
        const q = String(els.eventQuery.value || '').trim().toLowerCase();
        const category = els.eventCategory.value; const severity = els.eventSeverity.value;
        const rows = (a.timeline || []).filter(event => {
            if (category !== 'all' && event.category !== category) return false;
            if (severity !== 'all' && event.severity !== severity) return false;
            if (!q) return true;
            return `${event.title} ${event.summary} ${event.reason} ${event.category} ${JSON.stringify(event.payload || {})}`.toLowerCase().includes(q);
        }).slice().reverse();
        els.eventCount.textContent = `${rows.length} shown · ${a.timeline_total_events || (a.timeline || []).length} durable events`;
        els.events.innerHTML = rows.length ? rows.map(event => `<details class="jt-event" data-severity="${esc(event.severity)}">
            <summary><span class="jt-event-elapsed">+${esc(fmtDuration(event.elapsed_seconds))}</span><span class="jt-event-category">${esc(event.category)}</span><span class="jt-event-title">${esc(event.title)}</span><span class="jt-event-summary">${esc(event.summary || event.reason || '')}</span><span class="jt-event-time">${esc(fmtWhen(event.created_at))}</span></summary>
            <div class="jt-event-detail"><pre class="work-diff">${esc(JSON.stringify(event, null, 2))}</pre></div>
        </details>`).join('') : '<p class="trace-muted">No events match the current filter.</p>';
    }

    function formatCompare(value, unit) {
        if (value == null) return '—';
        if (unit === 'seconds') return fmtDuration(value);
        if (unit === 'usd') return fmtMoney(value);
        return fmtNum(value, 2);
    }
    function renderComparison(payload) {
        const comp = payload?.comparison;
        if (!comp) { els.compareSection.hidden = true; return; }
        const reference = payload.reference?.job || {};
        els.compareCaption.textContent = `current vs ${reference.title || String(comp.comparison_job_id || '').slice(0, 8)}`;
        els.comparison.innerHTML = (comp.metrics || []).map(metric => {
            let tone = '';
            if (metric.delta != null && metric.delta !== 0 && metric.lower_is_better != null) {
                tone = (metric.lower_is_better ? metric.delta < 0 : metric.delta > 0) ? 'good' : 'bad';
            }
            const delta = metric.delta == null ? '—' : `${metric.delta > 0 ? '+' : ''}${formatCompare(metric.delta, metric.unit)}`;
            return `<div class="jt-compare-metric"><div class="jt-compare-label">${esc(metric.label)}</div>
                <div class="jt-compare-values"><span class="jt-compare-primary">${esc(formatCompare(metric.primary, metric.unit))}</span><span class="jt-compare-ref">vs ${esc(formatCompare(metric.comparison, metric.unit))}</span></div>
                <div class="jt-compare-delta" data-tone="${tone}">${esc(delta)}</div></div>`;
        }).join('');
        els.compareSection.hidden = false;
    }

    async function loadComparison() {
        const compareId = els.compareSelect.value;
        if (!compareId || !state.analysis || compareId === state.analysis.job.id) {
            state.comparison = null; els.compareSection.hidden = true; return;
        }
        try {
            const data = await api(`/api/jobs/analysis/compare?job_id=${encodeURIComponent(state.analysis.job.id)}&compare_id=${encodeURIComponent(compareId)}`);
            state.comparison = data; renderComparison(data);
        } catch (error) { els.compareSection.hidden = true; showError(error.message); }
    }

    function renderAll(a) {
        state.analysis = a;
        els.title.textContent = `· ${a.job.title}`;
        els.status.textContent = `· ${statusLabel(a.job.status)}${a.job.archived ? ' · archived' : ''}`;
        els.debugExport.href = `/api/jobs/${encodeURIComponent(a.job.id)}/debug-export`;
        renderOverview(a); renderPhaseChart(a); renderAttemptChart(a); renderTools(a); renderVerifications(a); renderIncidents(a); renderEvents();
        els.main.hidden = false; els.empty.hidden = true; els.loading.hidden = true; els.error.hidden = true;
    }

    function showError(message) {
        els.error.textContent = String(message || 'Unknown error'); els.error.hidden = false; els.loading.hidden = true;
    }

    async function loadJob(jobId, { updateUrl = true } = {}) {
        if (!jobId) { state.analysis = null; els.main.hidden = true; els.empty.hidden = false; els.loading.hidden = true; return; }
        els.loading.textContent = 'Analysing durable job evidence…'; els.loading.hidden = false; els.main.hidden = true; els.error.hidden = true;
        try {
            const data = await api(`/api/jobs/${encodeURIComponent(jobId)}/analysis?timeline_limit=10000`);
            els.jobSelect.value = jobId;
            if (updateUrl) {
                const url = new URL(window.location.href); url.searchParams.set('job', jobId); if (els.compareSelect.value) url.searchParams.set('compare', els.compareSelect.value); else url.searchParams.delete('compare'); history.replaceState(null, '', url);
            }
            renderAll(data.analysis);
            if (els.compareSelect.value) await loadComparison();
        } catch (error) { showError(error.message); }
    }

    function exportAnalysis() {
        if (!state.analysis) return;
        const blob = new Blob([JSON.stringify(state.analysis, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob); const a = document.createElement('a');
        a.href = url; a.download = `mucli-job-${state.analysis.job.id.slice(0, 12)}-analysis.json`; a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    function toggleTheme() {
        const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', next); els.theme.textContent = next === 'dark' ? '☾' : '☼';
        try { localStorage.setItem('mucli-theme', next); } catch (_) {}
        if (state.analysis) { renderPhaseChart(state.analysis); renderAttemptChart(state.analysis); }
    }

    async function init() {
        try {
            await loadJobs();
            const qs = new URLSearchParams(window.location.search); const jobId = (qs.get('job') || '').trim(); const compareId = (qs.get('compare') || '').trim();
            if (compareId) els.compareSelect.value = compareId;
            if (jobId) await loadJob(jobId, { updateUrl: false }); else { els.loading.hidden = true; els.empty.hidden = false; }
        } catch (error) { showError(error.message); }
    }

    els.jobSelect.addEventListener('change', () => loadJob(els.jobSelect.value));
    els.compareSelect.addEventListener('change', async () => {
        const url = new URL(window.location.href); if (els.compareSelect.value) url.searchParams.set('compare', els.compareSelect.value); else url.searchParams.delete('compare'); history.replaceState(null, '', url);
        await loadComparison();
    });
    els.refresh.addEventListener('click', async () => { await loadJobs(); if (state.analysis) await loadJob(state.analysis.job.id, { updateUrl: false }); });
    els.theme.addEventListener('click', toggleTheme);
    els.exportAnalysis.addEventListener('click', exportAnalysis);
    [els.eventQuery, els.eventCategory, els.eventSeverity].forEach(el => el.addEventListener(el.tagName === 'INPUT' ? 'input' : 'change', renderEvents));
    window.addEventListener('resize', () => { clearTimeout(state.resizeTimer); state.resizeTimer = setTimeout(() => { if (state.analysis) { renderPhaseChart(state.analysis); renderAttemptChart(state.analysis); } }, 150); });
    els.theme.textContent = document.documentElement.getAttribute('data-theme') === 'dark' ? '☾' : '☼';
    init();
})();
