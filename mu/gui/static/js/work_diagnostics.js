(() => {
    'use strict';

    const detail = document.getElementById('work-detail');
    if (!detail) return;

    const DIAGNOSTIC_EVENTS = new Set([
        'worker_lease_acquired',
        'worker_process_started',
        'worker_process_exited',
        'worker_spawn_failed',
        'worktree_preflight_started',
        'repository_inspected',
        'job_base_resolved',
        'worktree_inventory',
        'worktree_add_started',
        'worktree_prepare_failed',
        'checkpoint_failed',
        'runtime_error',
    ]);

    let lastJobId = '';
    let lastSignature = '';
    let inFlight = false;

    function escapeHtml(value) {
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
        const value = Number(epoch || 0);
        if (!value) return '';
        try {
            return new Date(value * 1000).toLocaleString([], {
                month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit',
            });
        } catch (_) {
            return '';
        }
    }

    function compactCommand(value) {
        if (!Array.isArray(value)) return '';
        return value.map(part => {
            const text = String(part ?? '');
            return /\s/.test(text) ? JSON.stringify(text) : text;
        }).join(' ');
    }

    function eventTitle(event) {
        const names = {
            worker_lease_acquired: 'Worker claimed job',
            worker_process_started: 'Worker process started',
            worker_process_exited: 'Worker process exited',
            worker_spawn_failed: 'Worker spawn failed',
            worktree_preflight_started: 'Git preflight started',
            repository_inspected: 'Repository inspected',
            job_base_resolved: 'Base revision resolved',
            worktree_inventory: 'Worktree inventory',
            worktree_add_started: 'Worktree creation started',
            worktree_prepare_failed: 'Worktree preparation failed',
            checkpoint_failed: 'Checkpoint failed',
            runtime_error: 'Runtime error',
            status_changed: 'Environment failure',
        };
        return names[event.event_type] || String(event.event_type || 'Diagnostic');
    }

    function diagnosticSummary(event) {
        const payload = event && typeof event.payload === 'object' ? event.payload : {};
        if (event.event_type === 'worktree_prepare_failed') {
            return payload.error || event.reason || 'Worktree preparation failed.';
        }
        if (event.event_type === 'status_changed') {
            return payload.error || event.reason || `${event.from_status || ''} → ${event.to_status || ''}`;
        }
        if (event.event_type === 'repository_inspected') {
            const branch = payload.detected_default_branch ? `default ${payload.detected_default_branch}` : '';
            return [payload.canonical_path, branch].filter(Boolean).join(' · ');
        }
        if (event.event_type === 'job_base_resolved') {
            return `${payload.resolved_base_ref || 'unknown ref'} · ${String(payload.base_sha || '').slice(0, 12)}${payload.fallback_used ? ' · fallback from requested base' : ''}`;
        }
        if (event.event_type === 'worktree_add_started') {
            return `${payload.branch || ''} → ${payload.worktree || ''}`;
        }
        if (event.event_type === 'worker_process_started') {
            return `${payload.phase || 'implementation'} · pid ${payload.pid || '—'}${payload.log_path ? ` · ${payload.log_path}` : ''}`;
        }
        if (event.event_type === 'worker_process_exited') {
            return `${payload.phase || 'implementation'} · exit ${payload.exit_code ?? '—'}${payload.log_path ? ` · ${payload.log_path}` : ''}`;
        }
        if (event.event_type === 'worktree_inventory') {
            return `${payload.registered_count ?? 0} registered · managed path ${payload.managed_path_registered ? 'registered' : 'not registered'} · ${payload.managed_path_exists ? 'exists' : 'absent'}`;
        }
        return payload.error || event.reason || payload.message || payload.summary || '';
    }

    function detailsBlock(event) {
        const payload = event && typeof event.payload === 'object' ? event.payload : {};
        const rows = [];
        const add = (label, value) => {
            if (value === undefined || value === null || value === '') return;
            rows.push(`<div class="work-receipt-row"><span class="work-receipt-label">${escapeHtml(label)}</span><span class="work-receipt-value" title="${escapeHtml(String(value))}">${escapeHtml(String(value))}</span></div>`);
        };
        add('Stage', payload.stage);
        add('Repository', payload.canonical_path || payload.repository || payload.repository_input);
        add('Detected branch', payload.detected_default_branch);
        add('Requested base', payload.requested_base_branch);
        add('Resolved base', payload.resolved_base_ref);
        add('Base SHA', payload.base_sha);
        add('Managed branch', payload.managed_branch || payload.branch);
        add('Worktree', payload.managed_worktree || payload.worktree);
        add('PID', payload.pid || payload.worker_process_id);
        add('Exit code', payload.return_code ?? payload.exit_code);
        add('Worker log', payload.log_path);
        const command = compactCommand(payload.command);
        if (command) add('Git command', command);

        let output = '';
        if (payload.stderr) output += `stderr\n${String(payload.stderr).trim()}\n`;
        if (payload.stdout) output += `${output ? '\n' : ''}stdout\n${String(payload.stdout).trim()}`;
        if (Array.isArray(payload.attempted_refs) && payload.attempted_refs.length) {
            output += `${output ? '\n\n' : ''}attempted refs\n${payload.attempted_refs.map(item => `${item.ref}: exit ${item.return_code}${item.stderr ? ` — ${item.stderr}` : ''}`).join('\n')}`;
        }

        return `
            ${rows.length ? `<div class="work-receipt-list">${rows.join('')}</div>` : ''}
            ${output ? `<pre class="work-diff">${escapeHtml(output.slice(-12000))}</pre>` : ''}
        `;
    }

    function render(jobId, events) {
        const existing = detail.querySelector('.work-diagnostics-section');
        if (existing) existing.remove();
        const host = detail.querySelector('.work-detail');
        if (!host || !jobId) return;

        const diagnostics = events.filter(event =>
            DIAGNOSTIC_EVENTS.has(event.event_type)
            || (event.event_type === 'status_changed' && event.to_status === 'environment_error')
        );
        if (!diagnostics.length) return;

        const failure = [...diagnostics].reverse().find(event =>
            event.event_type === 'worktree_prepare_failed'
            || event.event_type === 'worker_spawn_failed'
            || event.event_type === 'runtime_error'
            || (event.event_type === 'status_changed' && event.to_status === 'environment_error')
        );

        const section = document.createElement('section');
        section.className = 'work-section work-diagnostics-section';
        section.innerHTML = `
            <h3>Job diagnostics</h3>
            <p class="work-muted">Structured controller, worker and Git-preflight details for troubleshooting this job.</p>
            ${failure ? `
                <div class="work-error-banner">
                    <strong>${escapeHtml(eventTitle(failure))}</strong><br>
                    ${escapeHtml(diagnosticSummary(failure))}
                </div>
                ${detailsBlock(failure)}
            ` : ''}
            <details class="work-advanced" ${failure ? 'open' : ''}>
                <summary>Execution trace · ${diagnostics.length} events</summary>
                <div class="work-timeline">
                    ${diagnostics.slice(-30).reverse().map(event => `
                        <div class="work-timeline-item">
                            <div class="work-timeline-head">
                                <span class="work-timeline-kind">${escapeHtml(eventTitle(event))}</span>
                                <span class="work-timeline-time">${escapeHtml(fmtWhen(event.created_at))}</span>
                            </div>
                            <div class="work-timeline-body">${escapeHtml(diagnosticSummary(event))}</div>
                            ${detailsBlock(event)}
                        </div>
                    `).join('')}
                </div>
            </details>
        `;
        host.appendChild(section);
    }

    async function refresh() {
        const jobId = selectedJobId();
        if (!jobId || inFlight) {
            if (!jobId) lastJobId = '';
            return;
        }
        inFlight = true;
        try {
            const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/events?limit=500`);
            if (!response.ok) return;
            const data = await response.json();
            const events = Array.isArray(data.events) ? data.events : [];
            const relevant = events.filter(event =>
                DIAGNOSTIC_EVENTS.has(event.event_type)
                || (event.event_type === 'status_changed' && event.to_status === 'environment_error')
            );
            const signature = `${jobId}:${relevant.map(event => event.id).join(',')}`;
            if (jobId !== lastJobId || signature !== lastSignature || !detail.querySelector('.work-diagnostics-section')) {
                lastJobId = jobId;
                lastSignature = signature;
                render(jobId, events);
            }
        } catch (_) {
            // Diagnostics must never interfere with the main work surface.
        } finally {
            inFlight = false;
        }
    }

    document.addEventListener('click', event => {
        if (event.target.closest('[data-job-id]')) setTimeout(refresh, 40);
    }, true);

    const observer = new MutationObserver(() => {
        if (selectedJobId()) setTimeout(refresh, 25);
    });
    observer.observe(detail, { childList: true, subtree: true });

    setInterval(refresh, 2500);
    setTimeout(refresh, 250);
})();
