(() => {
    'use strict';

    const detail = document.getElementById('work-detail');
    if (!detail) return;

    let lastJobId = '';
    let lastSignature = '';
    let inFlight = false;

    function traceApi() {
        return window.MuWorkTrace || null;
    }

    function escapeHtml(value) {
        const trace = traceApi();
        if (trace?.escapeHtml) return trace.escapeHtml(value);
        return String(value ?? '')
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#039;');
    }

    function selectedJobId() {
        const selected = document.querySelector('.work-job-card.is-selected[data-job-id]');
        return selected ? String(selected.dataset.jobId || '') : '';
    }

    function fmtWhen(epoch) {
        const trace = traceApi();
        if (trace?.fmtWhen) return trace.fmtWhen(epoch);
        return epoch ? new Date(Number(epoch) * 1000).toLocaleString() : '—';
    }

    function summary(event) {
        const trace = traceApi();
        return trace?.summary ? trace.summary(event) : String(event?.reason || '');
    }

    function label(event) {
        const trace = traceApi();
        return trace?.label ? trace.label(event) : String(event?.event_type || 'Diagnostic');
    }

    function render(snapshot) {
        const existing = detail.querySelector('.work-diagnostics-section');
        if (existing) existing.remove();
        const host = detail.querySelector('.work-detail');
        if (!host || !snapshot || !snapshot.job_id) return;

        const events = Array.isArray(snapshot.diagnostics) ? snapshot.diagnostics : [];
        const failure = snapshot.latest_failure || null;
        const hasLog = Boolean(snapshot.worker_log_exists && snapshot.worker_log_tail);
        if (!events.length && !hasLog) return;

        const section = document.createElement('section');
        section.className = 'work-section work-diagnostics-section';
        section.innerHTML = `
            <div class="work-section-title-row work-diagnostics-head">
                <div>
                    <h3>Job diagnostics</h3>
                    <p class="work-muted">Execution, worker and Git-preflight telemetry. Filter the trace, then expand only the event you need.</p>
                </div>
                <a class="work-secondary work-export-link" href="/api/jobs/${encodeURIComponent(snapshot.job_id)}/debug-export">Export debug bundle</a>
            </div>

            <div class="work-diagnostic-facts">
                <div><span>Status</span><strong>${escapeHtml(String(snapshot.status || '—').replaceAll('_', ' '))}</strong></div>
                <div><span>Worker</span><strong title="${escapeHtml(snapshot.worker_id || '')}">${escapeHtml(snapshot.worker_id ? String(snapshot.worker_id).slice(0, 18) : '—')}</strong></div>
                <div><span>Branch</span><strong title="${escapeHtml(snapshot.branch || '')}">${escapeHtml(snapshot.branch || '—')}</strong></div>
                <div><span>Heartbeat</span><strong>${escapeHtml(snapshot.heartbeat_at ? fmtWhen(snapshot.heartbeat_at) : '—')}</strong></div>
            </div>

            ${failure ? `
                <details class="work-diagnostic-failure" open>
                    <summary>
                        <span class="work-trace-dot" data-level="error"></span>
                        <span><strong>${escapeHtml(label(failure))}</strong><small>${escapeHtml(summary(failure) || 'Latest recorded failure')}</small></span>
                    </summary>
                    <pre>${escapeHtml(JSON.stringify(failure, null, 2))}</pre>
                </details>
            ` : ''}

            <div class="work-diagnostics-subhead">
                <div>
                    <strong>Execution trace</strong>
                    <span>${events.length} diagnostic events</span>
                </div>
                <span>Signal view hides low-value chatter by default</span>
            </div>
            <div data-diagnostic-trace></div>

            <details class="work-worker-log" ${failure && hasLog ? 'open' : ''}>
                <summary>
                    <span>Worker log</span>
                    <span>${snapshot.worker_log_exists ? `${Number(snapshot.worker_log_size || 0).toLocaleString()} bytes${snapshot.worker_log_truncated ? ' · tail shown' : ''}` : 'not created'}</span>
                </summary>
                ${hasLog
                    ? `<pre>${escapeHtml(String(snapshot.worker_log_tail || '').slice(-65536))}</pre>`
                    : '<p class="work-muted">No worker log has been created for this job.</p>'}
            </details>
        `;
        host.appendChild(section);

        const trace = traceApi();
        if (trace?.mount) {
            trace.mount(section.querySelector('[data-diagnostic-trace]'), events, { defaultView: 'signal' });
        } else {
            section.querySelector('[data-diagnostic-trace]').innerHTML = '<p class="work-muted">Trace browser unavailable.</p>';
        }
    }

    async function refresh() {
        const jobId = selectedJobId();
        if (!jobId || inFlight) {
            if (!jobId) lastJobId = '';
            return;
        }
        inFlight = true;
        try {
            const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/diagnostics?event_limit=700&log_tail_bytes=65536`);
            if (!response.ok) return;
            const data = await response.json();
            const snapshot = data?.diagnostics || null;
            if (!snapshot) return;
            const ids = Array.isArray(snapshot.diagnostics) ? snapshot.diagnostics.map(event => event.id).join(',') : '';
            const signature = `${jobId}:${ids}:${snapshot.worker_log_size || 0}:${snapshot.status || ''}`;
            if (jobId !== lastJobId || signature !== lastSignature || !detail.querySelector('.work-diagnostics-section')) {
                lastJobId = jobId;
                lastSignature = signature;
                render(snapshot);
            }
        } catch (_) {
            // Diagnostics are supplemental and must never interfere with review.
        } finally {
            inFlight = false;
        }
    }

    let scheduled = false;
    function schedule() {
        if (scheduled) return;
        scheduled = true;
        setTimeout(() => {
            scheduled = false;
            void refresh();
        }, 60);
    }

    document.addEventListener('click', event => {
        if (event.target.closest('[data-job-id]')) schedule();
    }, true);
    const observer = new MutationObserver(() => {
        if (selectedJobId()) schedule();
    });
    observer.observe(detail, { childList: true, subtree: true });
    setInterval(schedule, 3000);
    schedule();
})();
