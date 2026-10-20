(() => {
    'use strict';

    const els = {
        main: document.getElementById('jt-main'), loading: document.getElementById('jt-loading'),
        empty: document.getElementById('jt-empty'), error: document.getElementById('jt-error'),
        jobSelect: document.getElementById('jt-job-select'), compareSelect: document.getElementById('jt-compare-select'),
        title: document.getElementById('jt-job-title'), status: document.getElementById('jt-job-status'),
        overview: document.getElementById('jt-overview'), signals: document.getElementById('jt-signals'),
        runtimeTrace: document.getElementById('jt-runtime-trace'),
        compareSection: document.getElementById('jt-compare-section'), compareCaption: document.getElementById('jt-compare-caption'), comparison: document.getElementById('jt-comparison'),
        phaseCanvas: document.getElementById('jt-phase-canvas'), phaseLegend: document.getElementById('jt-phase-legend'), phaseTable: document.getElementById('jt-phase-table'), intervals: document.getElementById('jt-intervals'),
        attemptCanvas: document.getElementById('jt-attempt-canvas'), attemptTable: document.getElementById('jt-attempt-table'),
        tools: document.getElementById('jt-tools'), verifications: document.getElementById('jt-verifications'), incidents: document.getElementById('jt-incidents'),
        eventCount: document.getElementById('jt-event-count'), eventQuery: document.getElementById('jt-event-query'), eventCategory: document.getElementById('jt-event-category'), eventSeverity: document.getElementById('jt-event-severity'), events: document.getElementById('jt-events'),
        refresh: document.getElementById('jt-refresh'), debugExport: document.getElementById('jt-debug-export'), exportAnalysis: document.getElementById('jt-export-analysis'),
        panes: document.getElementById('jt-panes'), jobList: document.getElementById('jt-job-list'), jobFilter: document.getElementById('jt-job-filter'), jobCount: document.getElementById('jt-job-count'),
        glance: document.getElementById('jt-glance'), glanceEmpty: document.getElementById('jt-glance-empty'), openWork: document.getElementById('jt-open-work'),
        back: document.getElementById('jt-back'), asideToggle: document.getElementById('jt-aside-toggle'),
    };

    const state = { jobs: [], analysis: null, comparison: null, resizeTimer: null };
    // Phase colours are Wash tokens (documentation/visualization_style_guide.md):
    // semantic pigments where the state carries a meaning (needs you, failed,
    // ready), categorical pigments otherwise. Canvas resolves them at draw time
    // via phaseColor(); HTML swatches reference the custom property directly.
    const PHASE_TOKENS = {
        queued: '--wash-neutral-pigment', preparing: '--wash-running-pigment', running: '--wash-accent-pigment', needs_human: '--wash-attention-pigment',
        verifying: '--wash-plan-pigment', ready_for_review: '--wash-ok-pigment', recovering: '--wash-caution-pigment', conflicted: '--wash-layer-L0',
        failed: '--wash-danger-pigment', timed_out: '--wash-caution-pigment', budget_exceeded: '--wash-caution-pigment', environment_error: '--wash-danger-pigment',
        cancelled: '--wash-neutral-pigment', merged: '--wash-ok-pigment',
    };
    const PHASE_FALLBACK = '--wash-neutral-pigment';
    const STOPPED = new Set(['failed', 'environment_error', 'timed_out', 'budget_exceeded', 'conflicted']);
    const WAITING = new Set(['queued', 'needs_human']);

    function esc(value) { return String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;').replaceAll("'", '&#039;'); }
    // Shared status vocabulary: done ✓ ok · active ● running · waiting ◆ warn ·
    // failed ✗ danger · skipped – muted · pending ○ muted. Glyph + label, never colour alone.
    const STATUS_KIND = { merged: 'done', ready_for_review: 'done', queued: 'pending', needs_human: 'waiting', conflicted: 'waiting', cancelled: 'skipped', failed: 'failed', timed_out: 'failed', budget_exceeded: 'failed', environment_error: 'failed' };
    // Shared status vocabulary → glyph + Wash semantic. Mu §5.4: the status is a sign + word in the
    // page header's state sentence (.w-signword / .w-sign keyed by the vocabulary status), never a pill.
    const KIND_GLYPH = { done: ['✓', 'ok'], active: ['●', 'running'], waiting: ['◆', 'attention'], failed: ['✗', 'danger'], skipped: ['–', 'neutral'], pending: ['○', 'neutral'] };
    function applyStatusChip(node, status, archived) {
        if (!node) return;
        const kind = STATUS_KIND[String(status || '')] || (status ? 'active' : 'pending');
        const [glyph, tone] = KIND_GLYPH[kind];
        node.className = 'w-state__part w-signword jt-status-chip';
        node.dataset.status = kind;
        node.dataset.tone = tone;
        node.hidden = false;
        node.replaceChildren();
        const g = document.createElement('span'); g.className = 'w-sign'; g.dataset.status = kind; g.setAttribute('aria-hidden', 'true'); g.textContent = glyph;
        const label = document.createElement('span'); label.textContent = `${statusLabel(status)}${archived ? ' · archived' : ''}`;
        node.append(g, label);
    }
    // Terminal colour codes (ESC[…m) in captured check output are noise in a summary line.
    function plain(value) { return String(value ?? '').replace(/\u001b\[[0-9;?]*[A-Za-z]/g, '').replace(/\[[0-9;]{1,8}m(?!\])/g, ''); }
    function statusLabel(value) { return String(value || '').replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase()); }
    function fmtNum(value, digits = 0) { const n = Number(value || 0); return Number.isFinite(n) ? n.toLocaleString(undefined, { maximumFractionDigits: digits }) : '—'; }
    function fmtMoney(value) { return `$${Number(value || 0).toFixed(2)}`; }
    function fmtDuration(value) {
        let seconds = Math.max(0, Number(value || 0));
        if (seconds < 1) return `${Math.round(seconds * 1000)}ms`;
        if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
        const h = Math.floor(seconds / 3600); seconds -= h * 3600; const m = Math.floor(seconds / 60); const s = Math.round(seconds - m * 60);
        return h ? `${h}h ${m}m ${s}s` : `${m}m ${s}s`;
    }
    function fmtSignedDuration(value) { const n = Number(value || 0); return `${n > 0 ? '+' : n < 0 ? '−' : ''}${fmtDuration(Math.abs(n))}`; }
    function fmtWhen(epoch) { if (!epoch) return '—'; try { return new Date(Number(epoch) * 1000).toLocaleString(); } catch (_) { return '—'; } }
    function tokenCompact(tokens) { const entries = Object.entries(tokens || {}).filter(([, v]) => Number.isFinite(Number(v)) && Number(v) !== 0); return entries.length ? entries.slice(0, 4).map(([k, v]) => `${k}:${fmtNum(v)}`).join(' · ') : '—'; }
    function css(name, fallback = '') { return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback; }
    function phaseToken(status) { return PHASE_TOKENS[status] || PHASE_FALLBACK; }
    function phaseColor(status) { return css(phaseToken(status)); }
    function canvasFont(size, family) { return `${size}px ${css(family === 'mono' ? '--wash-font-mono' : '--wash-font-sans')}`; }
    // Labels drawn on a pigment fill use the Paper ink in both themes: pigments are mid-tones.
    // Label colour for text drawn on a pigment fill composited at `alpha` over the
    // card surface: Paper ink on light results, Dusk ink on dark ones (WASH.color).
    function hexRgb(value) { const m = String(value || '').trim().match(/^#([0-9a-f]{6})$/i); return m ? [0, 2, 4].map(i => parseInt(m[1].slice(i, i + 2), 16)) : null; }
    function onPigment(fill, alpha) {
        const W = window.WASH;
        const dark = (W && W.color('ink', 'paper')) || css('--wash-ink'), light = (W && W.color('ink', 'dusk')) || css('--wash-on-ink');
        const f = hexRgb(fill), b = hexRgb(css('--wash-surface'));
        if (!f || !b) return css('--wash-ink');
        const c = f.map((v, i) => (v * alpha + b[i] * (1 - alpha)) / 255).map(v => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2] > 0.18 ? dark : light;
    }
    async function api(url) { const response = await fetch(url); const data = await response.json().catch(() => ({})); if (!response.ok) throw new Error(data.detail || `${response.status} ${response.statusText}`); return data; }
    function phaseClass(status) {
        if (['preparing', 'running', 'recovering'].includes(status)) return 'active';
        if (status === 'verifying') return 'verification';
        if (WAITING.has(status)) return 'waiting';
        if (STOPPED.has(status)) return 'stopped';
        if (status === 'ready_for_review') return 'review';
        if (['cancelled', 'merged'].includes(status)) return 'terminal';
        return 'other';
    }

    async function loadJobs() {
        const data = await api('/api/jobs/history?scope=all&archive=all&limit=1000&sort=created&order=desc');
        state.jobs = data.jobs || [];
        const options = state.jobs.map(job => {
            const execution = job.execution || {};
            const runtime = [execution.provider, execution.model].filter(Boolean).join(' · ');
            // The selected job's status is in the state sentence; the picker names the job.
            const meta = [runtime, job.archived ? 'archived' : ''].filter(Boolean).join(' · ');
            return `<option value="${esc(job.id)}">${esc(job.title)} · ${esc(job.id.slice(0, 8))}${meta ? ` · ${esc(meta)}` : ''}</option>`;
        }).join('');
        els.jobSelect.innerHTML = '<option value="">Select a job…</option>' + options;
        if (els.compareSelect) els.compareSelect.innerHTML = '<option value="">Compare with…</option>' + options;
        renderJobList();
    }

    // The list pane (Mu §11b): one row per job — status sign, title, the status in words
    // with wall time and cost, and how long ago it started. The select above stays the
    // keyboard picker; a row and the select make the same choice.
    function ago(epoch) {
        const d = Math.max(0, Date.now() / 1000 - Number(epoch || 0));
        if (!epoch) return '';
        if (d < 3600) return `${Math.max(1, Math.floor(d / 60))}m`;
        if (d < 86400) return `${Math.floor(d / 3600)}h`;
        return `${Math.floor(d / 86400)}d`;
    }
    function renderJobList() {
        if (!els.jobList) return;
        const q = (els.jobFilter && els.jobFilter.value || '').trim().toLowerCase();
        const jobs = state.jobs.filter(job => !q || `${job.title} ${job.id} ${job.status}`.toLowerCase().includes(q));
        const current = state.analysis ? state.analysis.job.id : els.jobSelect.value;
        if (els.jobCount) els.jobCount.textContent = state.jobs.length ? String(state.jobs.length) : '';
        els.jobList.innerHTML = jobs.length ? jobs.map(job => {
            const kind = STATUS_KIND[String(job.status || '')] || (job.status ? 'active' : 'pending');
            const glyph = KIND_GLYPH[kind][0];
            const facts = [statusLabel(job.status), job.archived ? 'archived' : '', Number(job.elapsed_seconds) ? fmtDuration(job.elapsed_seconds) : '', Number(job.cost_usd) ? fmtMoney(job.cost_usd) : ''].filter(Boolean).join(' · ');
            return `<li class="w-row jt-jobrow" role="option" data-job="${esc(job.id)}" aria-selected="${job.id === current ? 'true' : 'false'}">
                <span class="w-sign" data-status="${kind}" aria-hidden="true">${glyph}</span>
                <button type="button" class="w-row__main" data-job-open="${esc(job.id)}"><span class="w-row__title">${esc(job.title)}</span><span class="w-row__sub">${esc(facts)}</span></button>
                <span class="w-row__when">${esc(ago(job.created_at))}</span></li>`;
        }).join('') : `<li class="w-pane__empty">${state.jobs.length ? 'No job matches.' : 'No jobs yet.'}</li>`;
    }
    function showPane(which) { if (els.panes) els.panes.dataset.show = which; }

    // The state sentence (Mu §4.2): status · wall time · cost · changed files. The job
    // picker below names the job, so the sentence never repeats its title.
    function paintState(a, costLabel) {
        const s = a.summary || {};
        const parts = [
            s.elapsed_seconds ? [fmtDuration(s.elapsed_seconds), 'Wall time'] : null,
            [fmtMoney(s.model_api_cost_usd != null ? s.model_api_cost_usd : s.cost_usd), costLabel],
            s.changed_files ? [`${fmtNum(s.changed_files)} file${Number(s.changed_files) === 1 ? '' : 's'} changed`, 'Changed files'] : null,
        ].filter(Boolean);
        const sentence = els.status && els.status.parentElement;
        if (!sentence) return;
        sentence.querySelectorAll('[data-jt-fact]').forEach(node => node.remove());
        sentence.insertAdjacentHTML('beforeend', parts.map(([text, title]) => `<span class="w-state__part" data-jt-fact title="${esc(title)}">${esc(text)}</span>`).join(''));
    }
    function card(label, value, cls = '') { return `<div class="trace-card ${cls}"><div class="tc-label">${esc(label)}</div><div class="tc-val">${esc(value)}</div></div>`; }

    function renderOverview(a) {
        const s = a.summary || {};
        const tokenTotal = Object.values(s.tokens || {}).reduce((sum, v) => sum + (Number(v) || 0), 0);
        const costLabel = (s.billing_modes || []).includes('estimated_token') ? 'model cost est.' : 'model cost';
        // Mu §6.1 / §5.5: status, wall time and cost are in the state sentence; a card that
        // would read 0 / — is left out (the signals below still say "no failures").
        const nz = v => Number(v || 0) !== 0;
        els.overview.innerHTML = [
            nz(s.active_seconds) && card('active execution', fmtDuration(s.active_seconds)),
            nz(s.passive_seconds) && card('passive residence', fmtDuration(s.passive_seconds), 'trace-card--warn'),
            nz(s.stopped_seconds) && card('stopped after errors', fmtDuration(s.stopped_seconds), 'trace-card--warn'),
            nz(s.verification_seconds) && card('verification', fmtDuration(s.verification_seconds)),
            card('attempts', String(s.attempts || 0)),
            nz(s.retries) && card('additional attempts', String(s.retries), 'trace-card--warn'),
            tokenTotal && card('tokens recorded', fmtNum(tokenTotal)),
            nz(s.tool_calls) && card('tool calls', fmtNum(s.tool_calls)),
            nz(s.unique_tools) && card('unique tools', fmtNum(s.unique_tools)),
            nz(s.human_gates) && card('human gates', fmtNum(s.human_gates), 'trace-card--warn'),
            nz(s.verification_runs) && card('verification runs', fmtNum(s.verification_runs)),
            nz(s.verification_failures) && card('verification failures', fmtNum(s.verification_failures), 'trace-card--warn'),
            nz(s.failures) && card('runtime failures', fmtNum(s.failures), 'trace-card--warn'),
            nz(s.changed_files) && card('changed files', fmtNum(s.changed_files)),
            (nz(s.additions) || nz(s.deletions)) && card('diff', `+${fmtNum(s.additions)} / -${fmtNum(s.deletions)}`),
        ].filter(Boolean).join('');
        paintState(a, costLabel);

        const firstPass = s.first_pass_verification;
        const firstPassValue = firstPass == null ? 'not measured' : firstPass ? 'passed' : 'needed another verification pass';
        const firstTone = firstPass == null ? 'neutral' : firstPass ? 'ok' : 'attention';
        const autonomyTone = (s.human_gates || 0) === 0 ? 'ok' : (s.human_gates || 0) <= 2 ? 'attention' : 'danger';
        const failureTone = (s.failures || 0) === 0 ? 'ok' : 'danger';
        const passivePct = s.elapsed_seconds ? (Number(s.passive_seconds || 0) / Number(s.elapsed_seconds) * 100) : 0;
        els.signals.innerHTML = `
            <div class="jt-signal" data-tone="${firstTone}"><div class="jt-signal-label">First verification</div><div class="jt-signal-value">${esc(firstPassValue)}</div><div class="jt-signal-detail">${fmtNum(s.verification_passes)} passed · ${fmtNum(s.verification_failures)} failed verification runs</div></div>
            <div class="jt-signal" data-tone="${autonomyTone}"><div class="jt-signal-label">Autonomy</div><div class="jt-signal-value">${fmtNum(s.human_gates)} human gate${Number(s.human_gates) === 1 ? '' : 's'}</div>${nz(s.human_responses) ? `<div class="jt-signal-detail">${fmtNum(s.human_responses)} recorded human response${Number(s.human_responses) === 1 ? '' : 's'}</div>` : ''}</div>
            <div class="jt-signal" data-tone="${failureTone}"><div class="jt-signal-label">Execution stability</div><div class="jt-signal-value">${nz(s.failures) ? `${fmtNum(s.failures)} failure signal${Number(s.failures) === 1 ? '' : 's'}` : 'no failures'}</div>${nz(s.recoveries) ? `<div class="jt-signal-detail">${fmtNum(s.recoveries)} recovery transition${Number(s.recoveries) === 1 ? '' : 's'}</div>` : ''}</div>
            <div class="jt-signal" data-tone="${passivePct > 35 ? 'attention' : 'ok'}"><div class="jt-signal-label">Passive share</div><div class="jt-signal-value">${passivePct.toFixed(1)}%</div><div class="jt-signal-detail">queue + human wait + stopped/error + review residence; not active agent work</div></div>`;
    }

    function renderRuntimeTrace(a) {
        const rt = a.runtime_trace || {};
        if (!rt.available) {
            els.runtimeTrace.innerHTML = `<div class="jt-trace-unavailable"><strong>No iteration-level harness trace</strong><p>${esc(rt.reason || 'No detailed trace was recorded.')}</p>${rt.session_name ? `<code>${esc(rt.session_name)}</code>` : ''}</div>`;
            return;
        }
        const s = rt.summary || {};
        els.runtimeTrace.innerHTML = `
            <div class="jt-runtime-grid">
                <div><span>Runs</span><strong>${fmtNum(rt.run_count)}</strong></div><div><span>Iterations</span><strong>${fmtNum(s.iters)}</strong></div>
                <div><span>Provider input</span><strong>${fmtNum(s.total_in)}</strong></div><div><span>Provider output</span><strong>${fmtNum(s.total_out)}</strong></div>
                <div><span>Agent wall time</span><strong>${fmtDuration(Number(s.total_wall_ms || 0) / 1000)}</strong></div><div><span>Peak context</span><strong>${fmtNum(s.peak_context)}</strong></div>
                <div><span>Context jumps</span><strong>${fmtNum(s.peak_request_delta)}</strong></div><div><span>Compactions</span><strong>${fmtNum(s.compaction_count)}</strong></div>
                <div><span>Redundant reads</span><strong>${fmtNum(s.redundant_reads)}</strong></div><div><span>Subagent iterations</span><strong>${fmtNum(s.subagent_iters)}</strong></div>
            </div>
            <div class="jt-runtime-actions"><a class="w-btn jt-open-trace" data-variant="secondary" data-size="sm" href="${esc(rt.trace_url || '#')}" target="_blank" rel="noopener"><span>Open full session Trace Analyzer</span><span class="w-btn__chevron" aria-hidden="true">›</span></a><span>${esc(rt.reason || '')}</span></div>
            ${(rt.top_context_spikes || []).length ? `<details class="jt-runtime-spikes"><summary>Largest request-context jumps · ${(rt.top_context_spikes || []).length}</summary><pre>${esc(JSON.stringify(rt.top_context_spikes, null, 2))}</pre></details>` : ''}`;
    }

    function setupCanvas(canvas, height) {
        const dpr = Math.max(1, window.devicePixelRatio || 1); const width = Math.max(320, canvas.parentElement?.clientWidth ? canvas.parentElement.clientWidth - 24 : 1000);
        canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr); canvas.style.width = `${width}px`; canvas.style.height = `${height}px`;
        const ctx = canvas.getContext('2d'); ctx.setTransform(dpr, 0, 0, dpr, 0, 0); return { ctx, width, height };
    }

    function renderPhaseChart(a) {
        const { ctx, width, height } = setupCanvas(els.phaseCanvas, 210); ctx.clearRect(0, 0, width, height);
        const intervals = (a.phase_intervals || []).filter(x => Number(x.duration_seconds || 0) >= 0); const total = Math.max(0.001, Number(a.summary?.elapsed_seconds || intervals.reduce((s, x) => s + Number(x.duration_seconds || 0), 0)));
        const left = 32, right = 22, top = 56, barH = 44, plotW = width - left - right; ctx.font = canvasFont(11); ctx.fillStyle = css('--wash-ink-3'); ctx.fillText('job lifetime', left, 28);
        let cursor = left;
        for (const interval of intervals) {
            const w = Math.max(interval.duration_seconds > 0 ? 1 : 0, plotW * Number(interval.duration_seconds || 0) / total); ctx.fillStyle = phaseColor(interval.status);
            ctx.globalAlpha = interval.passive_residence ? .42 : .78; ctx.fillRect(cursor, top, w, barH); ctx.globalAlpha = 1;
            if (w > 64) { ctx.save(); ctx.beginPath(); ctx.rect(cursor + 3, top, w - 6, barH); ctx.clip(); ctx.fillStyle = onPigment(phaseColor(interval.status), interval.passive_residence ? .42 : .78); ctx.font = canvasFont(10, 'mono'); ctx.fillText(statusLabel(interval.status), cursor + 7, top + 18); ctx.fillText(`${fmtDuration(interval.duration_seconds)} · ${interval.classification || phaseClass(interval.status)}`, cursor + 7, top + 33); ctx.restore(); }
            cursor += w;
        }
        ctx.strokeStyle = css('--wash-hairline'); ctx.beginPath(); ctx.moveTo(left, top + barH + 24); ctx.lineTo(width - right, top + barH + 24); ctx.stroke();
        for (let i = 0; i <= 4; i++) { const x = left + plotW * i / 4; ctx.fillStyle = css('--wash-ink-3'); ctx.font = canvasFont(9, 'mono'); ctx.fillText(fmtDuration(total * i / 4), x - (i ? 10 : 0), top + barH + 42); }
        const unique = [...new Set(intervals.map(x => x.status))]; els.phaseLegend.innerHTML = unique.map(status => `<span class="jt-legend-item"><span class="jt-legend-swatch" aria-hidden="true" style="--jt-swatch: var(${phaseToken(status)})"></span>${esc(statusLabel(status))}</span>`).join('');
        els.phaseTable.innerHTML = (a.phase_breakdown || []).map(row => `<tr><td>${esc(statusLabel(row.status))}</td><td>${esc(phaseClass(row.status))}</td><td data-num>${esc(fmtDuration(row.seconds))}</td><td data-num>${Number(row.percent || 0).toFixed(1)}%</td><td data-num>${fmtNum(row.occurrences)}</td></tr>`).join('');
        renderIntervals(a);
    }

    function eventLine(event, label) {
        if (!event) return `<div class="jt-interval-event"><span>${label}</span><em>not recorded</em></div>`;
        return `<div class="jt-interval-event"><span>${label}</span><div><strong>${esc(statusLabel(event.event_type))}</strong><small>${esc(fmtWhen(event.created_at))}${event.summary ? ` · ${esc(event.summary)}` : ''}</small></div></div>`;
    }
    function renderIntervals(a) {
        const intervals = a.phase_intervals || [];
        if (!intervals.length) { els.intervals.innerHTML = '<p class="trace-muted">No lifecycle intervals recorded.</p>'; return; }
        els.intervals.innerHTML = intervals.map((interval, index) => {
            const classification = interval.classification || phaseClass(interval.status); const eventRows = interval.events || [];
            const activity = Number(interval.activity_event_count || 0); const agent = Number(interval.agent_event_count || 0);
            return `<details class="jt-interval" data-class="${esc(classification)}" ${STOPPED.has(interval.status) ? 'open' : ''}>
                <summary><span class="jt-interval-index">${index + 1}</span><span class="jt-interval-state">${esc(statusLabel(interval.status))}</span><span class="jt-interval-class">${esc(classification)}</span><span class="jt-interval-duration">${esc(fmtDuration(interval.duration_seconds))}</span><span class="jt-interval-activity">${agent ? `${agent} agent events` : activity ? `${activity} diagnostic events` : 'no activity'}</span></summary>
                <div class="jt-interval-detail"><p class="jt-interval-interpretation">${esc(interval.interpretation || interval.explanation || '')}</p><p class="jt-interval-explanation">${esc(interval.explanation || '')}</p>
                ${eventLine(interval.entry_event, 'Entered')} ${eventLine(interval.exit_event, 'Exited')}
                <div class="jt-interval-counters"><span>${fmtNum(interval.event_count)} total events</span><span>${fmtNum(interval.worker_event_count)} worker</span><span>${fmtNum(interval.agent_event_count)} agent/tool</span></div>
                ${eventRows.length ? `<details class="jt-interval-events"><summary>Events inside this residence · ${eventRows.length}</summary><div>${eventRows.map(event => `<div class="jt-interval-event-row"><time>${esc(fmtWhen(event.created_at))}</time><strong>${esc(statusLabel(event.event_type))}</strong><span>${esc(event.summary || event.reason || '')}</span></div>`).join('')}</div></details>` : '<div class="jt-no-events">No events were recorded while the job remained in this state.</div>'}
                </div></details>`;
        }).join('');
    }

    function renderAttemptChart(a) {
        const { ctx, width, height } = setupCanvas(els.attemptCanvas, 240); ctx.clearRect(0, 0, width, height); const attempts = a.attempts || [];
        if (!attempts.length) { ctx.fillStyle = css('--wash-ink-3'); ctx.font = canvasFont(12); ctx.fillText('No implementation attempts recorded.', 28, 42); els.attemptTable.innerHTML = ''; return; }
        const left = 48, right = 42, top = 28, bottom = 42, plotW = width - left - right, plotH = height - top - bottom; const maxDur = Math.max(1, ...attempts.map(x => Number(x.duration_seconds || 0))); const maxCost = Math.max(.01, ...attempts.map(x => Number(x.cost_usd || 0))); const slot = plotW / attempts.length; const barW = Math.min(50, slot * .52);
        ctx.strokeStyle = css('--wash-hairline'); ctx.beginPath(); ctx.moveTo(left, top + plotH); ctx.lineTo(width - right, top + plotH); ctx.stroke(); const points = [];
        attempts.forEach((attempt, index) => { const cx = left + slot * (index + .5); const bh = plotH * Number(attempt.duration_seconds || 0) / maxDur; ctx.fillStyle = css(attempt.status === 'completed' ? '--wash-ok-pigment' : attempt.status === 'needs_human' ? '--wash-attention-pigment' : '--wash-danger-pigment'); ctx.globalAlpha = .66; ctx.fillRect(cx - barW / 2, top + plotH - bh, barW, bh); ctx.globalAlpha = 1; const py = top + plotH - plotH * Number(attempt.cost_usd || 0) / maxCost; points.push([cx, py]); ctx.fillStyle = css('--wash-ink-3'); ctx.font = canvasFont(9, 'mono'); ctx.textAlign = 'center'; ctx.fillText(`#${attempt.number}`, cx, height - 16); });
        ctx.strokeStyle = css('--wash-ink-2'); ctx.lineWidth = 1.7; ctx.beginPath(); points.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)); ctx.stroke(); points.forEach(([x, y]) => { ctx.beginPath(); ctx.fillStyle = css('--wash-ink-2'); ctx.arc(x, y, 3.4, 0, Math.PI * 2); ctx.fill(); }); ctx.textAlign = 'left';
        const table = els.attemptTable.closest('table');
        if (table) {
            table.classList.toggle('jt-no-tools', attempts.every(x => !Number(x.tool_calls || 0)));
            table.classList.toggle('jt-no-messages', attempts.every(x => !Number(x.agent_messages || 0)));
            table.classList.toggle('jt-no-errors', attempts.every(x => !Number(x.runtime_errors || 0)));
            table.classList.toggle('jt-no-tokens', attempts.every(x => tokenCompact(x.tokens) === '—'));
        }
        els.attemptTable.innerHTML = attempts.map(row => `<tr><td>#${row.number}</td><td>${esc(statusLabel(row.status))}</td><td data-num>${esc(fmtDuration(row.duration_seconds))}</td><td data-num>${esc(fmtMoney(row.cost_usd))}</td><td data-num>${fmtNum(row.tool_calls)}</td><td data-num>${fmtNum(row.agent_messages)}</td><td data-num>${fmtNum(row.runtime_errors)}</td><td title="${esc(JSON.stringify(row.tokens || {}))}">${esc(tokenCompact(row.tokens))}</td></tr>`).join('');
    }

    // With no tool telemetry the section steps aside (Verification takes the row) and the
    // fact is said once, in Verification's caption line.
    function renderTools(a) { const tools = a.tools || []; const section = els.tools.closest('section'); if (section) section.hidden = !tools.length; if (!tools.length) { els.tools.innerHTML = '<p class="trace-muted">No tool-call telemetry recorded.</p>'; return; } const max = Math.max(1, ...tools.map(x => Number(x.count || 0))); els.tools.innerHTML = tools.slice(0, 24).map(tool => `<div class="jt-tool-row"><div class="jt-tool-name" title="${esc(tool.name)}">${esc(tool.name)}</div><div class="jt-tool-track"><div class="jt-tool-fill" style="width:${(Number(tool.count || 0) / max * 100).toFixed(1)}%"></div></div><div class="jt-tool-value">${fmtNum(tool.count)}</div></div>`).join(''); }
    function renderVerifications(a) { const rows = a.verifications || []; if (!rows.length) { els.verifications.innerHTML = '<p class="trace-muted">No deterministic verification runs recorded.</p>'; return; } els.verifications.innerHTML = rows.map((row, index) => `<div class="jt-verification" data-pass="${row.passed}"><div class="jt-verification-mark">${row.passed ? '✓' : '✗'}</div><div class="jt-verification-main"><div class="jt-verification-title">Run ${index + 1} · ${esc(statusLabel(row.status))}</div><div class="jt-verification-meta">${fmtNum(row.checks_passed)}/${fmtNum(row.checks)} checks · ${fmtNum(row.changed_files)} files · +${fmtNum(row.additions)} / -${fmtNum(row.deletions)}${row.dirty ? ' · dirty' : ''}</div></div><div class="jt-verification-time">${esc(fmtDuration(row.duration_seconds))}</div></div>`).join(''); }
    // A human gate's payload as a sentence ("Needs work · merge readiness 75% · review done"),
    // never raw JSON; the full payload stays one click away.
    function gateSummary(payload) {
        const p = payload || {};
        const parts = [];
        if (p.verdict) parts.push(statusLabel(p.verdict));
        if (p.merge_readiness != null) parts.push(`merge readiness ${fmtNum(p.merge_readiness)}%`);
        if (p.attention_reason) parts.push(String(p.attention_reason).replaceAll('_', ' '));
        const core = p.core_state || {};
        if (core.state && core.state !== p.attention_reason) parts.push(`state ${String(core.state).replaceAll('_', ' ')}`);
        if (core.fix_attempts) parts.push(`${fmtNum(core.fix_attempts)} fix attempt${Number(core.fix_attempts) === 1 ? '' : 's'}`);
        if (!parts.length) {
            for (const [key, value] of Object.entries(p)) {
                if (value == null || typeof value === 'object') continue;
                parts.push(`${key.replaceAll('_', ' ')} ${value}`);
                if (parts.length >= 4) break;
            }
        }
        return parts.join(' · ');
    }
    function incidentRows(rows, kind) { return `<div class="jt-incident-list">${rows.slice().reverse().slice(0, 30).map(row => {
        const copy = kind === 'gate' ? gateSummary(row.payload) : (row.summary || '');
        const raw = kind === 'gate' && row.payload ? `<details class="w-disclose jt-incident-raw"><summary>Payload</summary><pre class="jt-event-json">${esc(JSON.stringify(row.payload, null, 2))}</pre></details>` : '';
        return `<div class="jt-incident"><div class="jt-incident-head"><span class="jt-incident-kind">${esc(kind === 'gate' ? (row.reason || 'Human gate') : statusLabel(row.event_type || 'Failure'))}</span><span class="jt-incident-time">${esc(fmtWhen(row.created_at))}</span></div>${copy ? `<div class="jt-incident-copy">${esc(copy)}</div>` : ''}${raw}</div>`;
    }).join('')}</div>`; }
    function renderIncidents(a) {
        const gates = a.human_gates || [], failures = a.failures || [];
        const groups = [];
        if (gates.length) groups.push(`<div class="jt-incident-group"><div class="jt-incident-title">Human gates</div>${incidentRows(gates, 'gate')}</div>`);
        if (failures.length) groups.push(`<div class="jt-incident-group"><div class="jt-incident-title">Failures</div>${incidentRows(failures, 'failure')}</div>`);
        const quiet = [!gates.length && 'no human gates', !failures.length && 'no failures'].filter(Boolean);
        els.incidents.innerHTML = groups.join('') + (quiet.length ? `<p class="trace-muted jt-incidents-quiet">${esc(quiet.join(' · ').replace(/^./, c => c.toUpperCase()))} recorded.</p>` : '');
    }

    function renderEvents() {
        const a = state.analysis; if (!a) return; const q = String(els.eventQuery.value || '').trim().toLowerCase(); const category = els.eventCategory.value; const severity = els.eventSeverity.value;
        const rows = (a.timeline || []).filter(event => { if (category !== 'all' && event.category !== category) return false; if (severity !== 'all' && event.severity !== severity) return false; if (!q) return true; return `${event.title} ${event.summary} ${event.reason} ${event.category} ${JSON.stringify(event.payload || {})}`.toLowerCase().includes(q); }).slice().reverse();
        const total = a.timeline_total_events || (a.timeline || []).length;
        els.eventCount.textContent = rows.length === total ? `${fmtNum(total)} events` : `${fmtNum(rows.length)} of ${fmtNum(total)} events`;
        // Mu §5.1: a burst of the same event (same title, category and severity in a row —
        // sandbox events, check output lines) is one row with a count and a time span;
        // expand it for each event. Pairs stay separate unless they are identical.
        const runs = [];
        for (const event of rows) {
            const key = `${event.category}|${event.severity}|${event.title}`;
            const last = runs[runs.length - 1];
            if (last && last.key === key) last.items.push(event); else runs.push({ key, items: [event] });
        }
        const groups = [];
        for (const run of runs) {
            const same = run.items.every(e => (e.summary || e.reason || '') === (run.items[0].summary || run.items[0].reason || ''));
            if (run.items.length >= 3 || same) groups.push(run); else run.items.forEach(item => groups.push({ key: run.key, items: [item] }));
        }
        const clock = epoch => { const text = fmtWhen(epoch); return text.includes(', ') ? text.split(', ').pop() : text; };
        const one = event => `<details class="jt-event" data-severity="${esc(event.severity)}"><summary><span class="jt-event-elapsed">+${esc(fmtDuration(event.elapsed_seconds))}</span><span class="jt-event-category">${esc(event.category)}</span><span class="jt-event-title">${esc(event.title)}</span><span class="jt-event-summary">${esc(plain(event.summary || event.reason || ''))}</span><span class="jt-event-time">${esc(fmtWhen(event.created_at))}</span></summary><div class="jt-event-detail"><pre class="jt-event-json">${esc(JSON.stringify(event, null, 2))}</pre></div></details>`;
        const many = group => {
            const first = group.items[0], last = group.items[group.items.length - 1];
            const summaries = new Set(group.items.map(e => e.summary || e.reason || ''));
            const summary = summaries.size === 1 ? plain(first.summary || first.reason || '') : `${fmtNum(summaries.size)} different lines`;
            return `<details class="jt-event jt-event--group" data-severity="${esc(first.severity)}"><summary><span class="jt-event-elapsed">+${esc(fmtDuration(first.elapsed_seconds))}</span><span class="jt-event-category">${esc(first.category)}</span><span class="jt-event-title">${esc(first.title)} <span class="jt-event-count">×${group.items.length}</span></span><span class="jt-event-summary">${esc(summary)}</span><span class="jt-event-time" title="${esc(fmtWhen(last.created_at))} – ${esc(fmtWhen(first.created_at))}">${esc(clock(last.created_at))}–${esc(clock(first.created_at))}</span></summary><div class="jt-event-detail jt-event-burst">${group.items.slice(0, 200).map(one).join('')}${group.items.length > 200 ? `<p class="trace-muted">${fmtNum(group.items.length - 200)} more not shown.</p>` : ''}</div></details>`;
        };
        els.events.innerHTML = groups.length ? groups.map(g => g.items.length > 1 ? many(g) : one(g.items[0])).join('') : '<p class="trace-muted">No events match the current filter.</p>';
    }

    function formatCompare(value, unit) { if (value == null) return '—'; if (unit === 'seconds') return fmtDuration(value); if (unit === 'usd') return fmtMoney(value); return fmtNum(value, 2); }
    function renderComparison(payload) {
        const comp = payload?.comparison; if (!comp) { els.compareSection.hidden = true; return; } const reference = payload.reference?.job || {}; els.compareCaption.textContent = `current vs ${reference.title || String(comp.comparison_job_id || '').slice(0, 8)}`;
        els.comparison.innerHTML = (comp.metrics || []).map(metric => { let tone = ''; if (metric.delta != null && metric.delta !== 0 && metric.lower_is_better != null) tone = (metric.lower_is_better ? metric.delta < 0 : metric.delta > 0) ? 'ok' : 'danger'; const delta = metric.delta == null ? '—' : metric.unit === 'seconds' ? fmtSignedDuration(metric.delta) : `${metric.delta > 0 ? '+' : ''}${formatCompare(metric.delta, metric.unit)}`; return `<div class="jt-compare-metric"><div class="jt-compare-label">${esc(metric.label)}</div><div class="jt-compare-values"><span class="jt-compare-primary">${esc(formatCompare(metric.primary, metric.unit))}</span><span class="jt-compare-ref">vs ${esc(formatCompare(metric.comparison, metric.unit))}</span></div><div class="jt-compare-delta" data-tone="${tone}">${esc(delta)}</div></div>`; }).join(''); els.compareSection.hidden = false;
    }
    async function loadComparison() { const compareId = els.compareSelect.value; if (!compareId || !state.analysis || compareId === state.analysis.job.id) { state.comparison = null; els.compareSection.hidden = true; return; } try { const data = await api(`/api/jobs/analysis/compare?job_id=${encodeURIComponent(state.analysis.job.id)}&compare_id=${encodeURIComponent(compareId)}`); state.comparison = data; renderComparison(data); } catch (error) { els.compareSection.hidden = true; showError(error.message); } }

    function renderAll(a) {
        state.analysis = a; if (els.title) els.title.textContent = a.job.title; applyStatusChip(els.status, a.job.status, a.job.archived); els.debugExport.href = `/api/jobs/${encodeURIComponent(a.job.id)}/debug-export`;
        renderOverview(a); renderRuntimeTrace(a); renderPhaseChart(a); renderAttemptChart(a); renderTools(a); renderVerifications(a); renderIncidents(a); renderEvents(); els.main.hidden = false; els.empty.hidden = true; els.loading.hidden = true; els.error.hidden = true;
        if (els.glance) els.glance.hidden = false; if (els.glanceEmpty) els.glanceEmpty.hidden = true;
        if (els.openWork) els.openWork.href = `/work#/job/${encodeURIComponent(a.job.id)}`;
        els.jobList && els.jobList.querySelectorAll('.jt-jobrow').forEach(row => row.setAttribute('aria-selected', row.dataset.job === a.job.id ? 'true' : 'false'));
    }
    function showError(message) { els.error.textContent = String(message || 'Unknown error'); els.error.hidden = false; els.loading.hidden = true; }
    async function loadJob(jobId, { updateUrl = true } = {}) {
        if (!jobId) { state.analysis = null; els.main.hidden = true; els.empty.hidden = false; els.loading.hidden = true; return; }
        els.loading.textContent = 'Analysing durable job evidence…'; els.loading.hidden = false; els.main.hidden = true; els.error.hidden = true;
        try { const data = await api(`/api/jobs/${encodeURIComponent(jobId)}/analysis?timeline_limit=10000`); els.jobSelect.value = jobId; if (updateUrl) { const url = new URL(window.location.href); url.searchParams.set('job', jobId); if (els.compareSelect.value) url.searchParams.set('compare', els.compareSelect.value); else url.searchParams.delete('compare'); history.replaceState(null, '', url); } renderAll(data.analysis); if (els.compareSelect.value) await loadComparison(); } catch (error) { showError(error.message); }
    }
    function exportAnalysis() { if (!state.analysis) return; const blob = new Blob([JSON.stringify(state.analysis, null, 2)], { type: 'application/json' }); const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = `mucli-job-${state.analysis.job.id.slice(0, 12)}-analysis.json`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
    // Theme: the shared top-bar toggle (route_theme.js, window.MuTheme) owns the
    // paper/dusk preference; canvases redraw on every mucli-theme-change.
    function syncTheme() { if (state.analysis) { renderPhaseChart(state.analysis); renderAttemptChart(state.analysis); } }
    async function init() { try { await loadJobs(); const qs = new URLSearchParams(window.location.search); const jobId = (qs.get('job') || '').trim(); const compareId = (qs.get('compare') || '').trim(); if (compareId) els.compareSelect.value = compareId; if (jobId) await loadJob(jobId, { updateUrl: false }); else { els.loading.hidden = true; els.empty.hidden = false; } } catch (error) { showError(error.message); } }

    els.jobSelect.addEventListener('change', () => loadJob(els.jobSelect.value));
    if (els.jobList) els.jobList.addEventListener('click', event => {
        const button = event.target.closest('[data-job-open]');
        if (!button) return;
        showPane('main');
        if (!state.analysis || state.analysis.job.id !== button.dataset.jobOpen) loadJob(button.dataset.jobOpen);
    });
    if (els.jobFilter) els.jobFilter.addEventListener('input', renderJobList);
    if (els.back) els.back.addEventListener('click', () => showPane('list'));
    if (els.asideToggle) els.asideToggle.addEventListener('click', () => {
        const open = els.panes.dataset.aside !== 'open';
        if (open) els.panes.dataset.aside = 'open'; else delete els.panes.dataset.aside;
        els.asideToggle.setAttribute('aria-pressed', String(open));
    });
    if (els.compareSelect) els.compareSelect.addEventListener('change', async () => { const url = new URL(window.location.href); if (els.compareSelect.value) url.searchParams.set('compare', els.compareSelect.value); else url.searchParams.delete('compare'); history.replaceState(null, '', url); await loadComparison(); });
    els.refresh.addEventListener('click', async () => { await loadJobs(); if (state.analysis) await loadJob(state.analysis.job.id, { updateUrl: false }); });
    window.addEventListener('mucli-theme-change', syncTheme); els.exportAnalysis.addEventListener('click', exportAnalysis);
    // Command palette (static/js/wash/palette.js): this analyzer's actions.
    if (window.WashPalette) {
        window.WashPalette.register('job-trace', () => [
            { id: 'jt:job', group: 'Actions', label: 'Choose a job to analyze', icon: 'list-checks', run: () => document.getElementById('jt-job-select')?.focus() },
            { id: 'jt:refresh', group: 'Actions', label: 'Refresh job analysis', icon: 'refresh-cw', run: () => els.refresh.click() },
            { id: 'jt:export', group: 'Actions', label: 'Export analysis as JSON', icon: 'download', enabled: !!state.analysis, run: exportAnalysis },
            { id: 'jt:work', group: 'Navigate', label: 'Open this job in Work', icon: 'briefcase', enabled: !!state.analysis,
              run: () => { const href = `/work#/job/${encodeURIComponent(state.analysis.job.id)}`; if (window.MuNav) window.MuNav.go(href); else location.href = href; } },
        ], { priority: 10 });
    }
    [els.eventQuery, els.eventCategory, els.eventSeverity].forEach(el => el.addEventListener(el.tagName === 'INPUT' ? 'input' : 'change', renderEvents));
    window.addEventListener('resize', () => { clearTimeout(state.resizeTimer); state.resizeTimer = setTimeout(() => { if (state.analysis) { renderPhaseChart(state.analysis); renderAttemptChart(state.analysis); } }, 150); });
    init();
})();
