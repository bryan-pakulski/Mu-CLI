(() => {
    'use strict';

    const detail = document.getElementById('work-detail');
    if (!detail) return;

    const LABELS = {
        job_created: 'Job created',
        job_archived: 'Job archived',
        job_restored: 'Job restored',
        status_changed: 'Status changed',
        attempt_started: 'Attempt started',
        attempt_finished: 'Attempt finished',
        worker_process_started: 'Worker started',
        worker_process_exited: 'Worker exited',
        worker_process_terminated: 'Worker terminated',
        worker_spawn_failed: 'Worker spawn failed',
        worker_lease_acquired: 'Worker lease acquired',
        worker_lease_released: 'Worker lease released',
        worktree_preflight_started: 'Git preflight started',
        repository_inspected: 'Repository inspected',
        job_base_resolved: 'Base revision resolved',
        worktree_inventory: 'Worktree inventory',
        worktree_add_started: 'Worktree creation started',
        worktree_prepare_failed: 'Worktree preparation failed',
        worktree_ready: 'Worktree ready',
        worktree_removed: 'Worktree removed',
        checkpoint_created: 'Checkpoint created',
        checkpoint_failed: 'Checkpoint failed',
        agent_message: 'Agent response',
        tool_call_ui: 'Tool call',
        runtime_error: 'Runtime error',
        verification_pending: 'Verification pending',
        verification_evidence_created: 'Verification evidence',
        verification_failed: 'Verification failed',
        verification_worker_error: 'Verification worker error',
        verification_contract_updated: 'Validation updated',
        work_receipt_updated: 'Receipt updated',
        human_response: 'Human response',
        interaction_response: 'Human decision',
        interaction_response_consumed: 'Decision consumed',
        review_feedback: 'Changes requested',
    };

    function escapeHtml(value) {
        return String(value ?? '')
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#039;');
    }

    function titleCase(value) {
        return String(value || '')
            .replaceAll('_', ' ')
            .replace(/\b\w/g, letter => letter.toUpperCase());
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

    function category(event) {
        const type = String(event?.event_type || '');
        if (type.includes('error') || type.includes('failed') || ['timed_out', 'budget_exceeded'].includes(event?.to_status)) return 'errors';
        if (type.startsWith('worktree_') || type === 'repository_inspected' || type === 'job_base_resolved' || type === 'checkpoint_created') return 'git';
        if (type.startsWith('worker_') || type === 'attempt_started' || type === 'attempt_finished') return 'worker';
        if (type.startsWith('verification_') || type === 'work_receipt_updated') return 'verification';
        if (type === 'status_changed' || type === 'job_created' || type === 'job_archived' || type === 'job_restored') return 'lifecycle';
        if (type === 'human_response' || type === 'interaction_response' || type === 'interaction_response_consumed' || type === 'review_feedback') return 'human';
        if (type === 'agent_message') return 'agent';
        if (type === 'tool_call_ui') return 'tools';
        return 'system';
    }

    function severity(event) {
        const type = String(event?.event_type || '');
        const target = String(event?.to_status || '');
        if (type.includes('error') || type.includes('failed') || ['failed', 'environment_error', 'timed_out', 'budget_exceeded'].includes(target)) return 'error';
        if (target === 'needs_human' || type === 'review_feedback') return 'attention';
        return 'info';
    }

    function isSignal(event) {
        return !['agent', 'tools', 'system'].includes(category(event)) || severity(event) !== 'info';
    }

    function label(event) {
        return LABELS[event?.event_type] || titleCase(event?.event_type || 'Event');
    }

    function summary(event) {
        const payload = event && typeof event.payload === 'object' ? event.payload : {};
        if (event?.event_type === 'status_changed') {
            return `${event.from_status || 'new'} → ${event.to_status || ''}${event.reason ? ` · ${event.reason}` : ''}`;
        }
        if (event?.event_type === 'agent_message') return String(payload.text || '').slice(0, 320);
        if (event?.event_type === 'tool_call_ui') return String(payload.tool_name || payload.name || 'tool call');
        if (event?.event_type === 'checkpoint_created') return `${payload.label || 'checkpoint'} · ${String(payload.sha || '').slice(0, 12)}`;
        if (event?.event_type === 'repository_inspected') {
            return [payload.submitted_path || payload.canonical_path, payload.current_branch ? `branch ${payload.current_branch}` : '', payload.source_worktree_clean === true ? 'clean' : payload.source_worktree_clean === false ? 'dirty' : ''].filter(Boolean).join(' · ');
        }
        if (event?.event_type === 'job_base_resolved') return `${payload.resolved_base_ref || 'base'} · ${String(payload.base_sha || '').slice(0, 12)}`;
        if (event?.event_type === 'worker_process_started') return `${payload.phase || 'implementation'} · pid ${payload.pid || '—'}`;
        if (event?.event_type === 'worker_process_exited') return `${payload.phase || 'implementation'} · exit ${payload.exit_code ?? '—'}`;
        if (event?.event_type === 'human_response' || event?.event_type === 'review_feedback') return String(payload.detail || event.reason || '').slice(0, 320);
        if (event?.reason) return String(event.reason).slice(0, 320);
        for (const key of ['error', 'summary', 'message', 'text', 'command']) {
            if (payload[key]) return String(payload[key]).slice(0, 320);
        }
        return '';
    }

    function compactRows(event) {
        const payload = event && typeof event.payload === 'object' ? event.payload : {};
        const values = [
            ['Event ID', event.id],
            ['From', event.from_status],
            ['To', event.to_status],
            ['Reason', event.reason],
            ['Stage', payload.stage],
            ['Repository', payload.canonical_path || payload.repository || payload.repository_input],
            ['Branch', payload.managed_branch || payload.branch || payload.current_branch],
            ['Base', payload.resolved_base_ref || payload.base_sha],
            ['Worktree', payload.managed_worktree || payload.worktree],
            ['PID', payload.pid || payload.worker_process_id],
            ['Exit', payload.return_code ?? payload.exit_code],
            ['Tool', payload.tool_name],
            ['Log', payload.log_path],
        ].filter(([, value]) => value !== undefined && value !== null && value !== '');
        if (!values.length) return '';
        return `<div class="work-trace-kv">${values.map(([key, value]) => `
            <div><span>${escapeHtml(key)}</span><code>${escapeHtml(String(value))}</code></div>
        `).join('')}</div>`;
    }

    function renderEvent(event) {
        const cat = category(event);
        const level = severity(event);
        const preview = summary(event);
        const raw = JSON.stringify(event, null, 2);
        const haystack = `${label(event)} ${cat} ${level} ${preview} ${raw}`.toLowerCase();
        return `
            <details class="work-trace-event" data-category="${cat}" data-level="${level}" data-signal="${isSignal(event) ? '1' : '0'}" data-search="${escapeHtml(haystack)}">
                <summary>
                    <span class="work-trace-dot" data-level="${level}"></span>
                    <span class="work-trace-summary-main">
                        <span class="work-trace-title">${escapeHtml(label(event))}</span>
                        ${preview ? `<span class="work-trace-preview">${escapeHtml(preview)}</span>` : ''}
                    </span>
                    <span class="work-trace-category">${escapeHtml(cat)}</span>
                    <time>${escapeHtml(fmtWhen(event.created_at))}</time>
                </summary>
                <div class="work-trace-detail">
                    ${compactRows(event)}
                    <div class="work-trace-json-head">
                        <span>Raw event</span>
                        <button type="button" class="work-trace-copy" data-copy-event>Copy JSON</button>
                    </div>
                    <pre>${escapeHtml(raw)}</pre>
                </div>
            </details>
        `;
    }

    function mount(host, events, options = {}) {
        if (!host) return;
        const values = Array.isArray(events) ? events.slice() : [];
        const defaultView = options.defaultView || 'all';
        const defaultCategory = options.defaultCategory || 'all';
        host.classList.add('work-trace-browser');
        host.innerHTML = `
            <div class="work-trace-toolbar">
                <input type="search" data-trace-search placeholder="Filter trace…" aria-label="Filter trace">
                <select data-trace-category aria-label="Trace category">
                    <option value="all">All categories</option>
                    <option value="errors">Errors</option>
                    <option value="lifecycle">Lifecycle</option>
                    <option value="git">Git / worktree</option>
                    <option value="worker">Worker</option>
                    <option value="verification">Verification</option>
                    <option value="human">Human</option>
                    <option value="agent">Agent</option>
                    <option value="tools">Tools</option>
                    <option value="system">System</option>
                </select>
                <select data-trace-view aria-label="Trace verbosity">
                    <option value="all">All events</option>
                    <option value="signal">Signal only</option>
                    <option value="errors">Errors only</option>
                </select>
                <button type="button" class="work-trace-order" data-trace-order data-order="desc">Newest first</button>
                <span class="work-trace-count" data-trace-count></span>
            </div>
            <div class="work-trace-list" data-trace-list></div>
        `;
        const list = host.querySelector('[data-trace-list]');
        const search = host.querySelector('[data-trace-search]');
        const categoryEl = host.querySelector('[data-trace-category]');
        const view = host.querySelector('[data-trace-view]');
        const order = host.querySelector('[data-trace-order]');
        const count = host.querySelector('[data-trace-count]');
        categoryEl.value = defaultCategory;
        view.value = defaultView;

        const render = () => {
            const newest = order.dataset.order !== 'asc';
            const ordered = newest ? values.slice().reverse() : values.slice();
            list.innerHTML = ordered.map(renderEvent).join('') || '<p class="work-muted">No trace events recorded.</p>';
            apply();
            list.querySelectorAll('[data-copy-event]').forEach(button => {
                button.addEventListener('click', event => {
                    event.preventDefault();
                    const raw = button.closest('.work-trace-detail')?.querySelector('pre')?.textContent || '';
                    navigator.clipboard?.writeText(raw).catch(() => {});
                    button.textContent = 'Copied';
                    setTimeout(() => { button.textContent = 'Copy JSON'; }, 1000);
                });
            });
        };

        const apply = () => {
            const needle = String(search.value || '').trim().toLowerCase();
            const cat = categoryEl.value;
            const visibility = view.value;
            let visible = 0;
            host.querySelectorAll('.work-trace-event').forEach(row => {
                const matchesText = !needle || String(row.dataset.search || '').includes(needle);
                const matchesCategory = cat === 'all' || row.dataset.category === cat;
                const matchesView = visibility === 'all'
                    || (visibility === 'signal' && row.dataset.signal === '1')
                    || (visibility === 'errors' && row.dataset.level === 'error');
                const show = matchesText && matchesCategory && matchesView;
                row.hidden = !show;
                if (show) visible += 1;
            });
            count.textContent = `${visible} / ${values.length}`;
        };

        search.addEventListener('input', apply);
        categoryEl.addEventListener('change', apply);
        view.addEventListener('change', apply);
        order.addEventListener('click', () => {
            const next = order.dataset.order === 'desc' ? 'asc' : 'desc';
            order.dataset.order = next;
            order.textContent = next === 'desc' ? 'Newest first' : 'Oldest first';
            render();
        });
        render();
    }

    window.MuWorkTrace = { mount, category, severity, label, summary, renderEvent, escapeHtml, fmtWhen };

    let scheduled = false;
    let inFlight = false;

    function selectedJobId() {
        const selected = document.querySelector('.work-job-card.is-selected[data-job-id]');
        return selected ? String(selected.dataset.jobId || '') : '';
    }

    async function enhanceActivity() {
        if (inFlight) return;
        const jobId = selectedJobId();
        if (!jobId) return;
        const section = [...detail.querySelectorAll('.work-section')].find(item => {
            const heading = item.querySelector(':scope > h3');
            return heading && String(heading.textContent || '').trim().startsWith('Activity');
        });
        if (!section || section.dataset.traceJob === jobId) return;
        inFlight = true;
        try {
            const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/events?limit=5000`);
            if (!response.ok) return;
            const data = await response.json();
            const events = Array.isArray(data.events) ? data.events : [];
            section.dataset.traceJob = jobId;
            section.innerHTML = `
                <div class="work-section-title-row">
                    <div>
                        <h3>Activity</h3>
                        <p class="work-muted">Filter the durable event stream, then expand an event to inspect its complete payload.</p>
                    </div>
                    <span class="work-trace-total">${events.length} events</span>
                </div>
                <div data-activity-trace></div>
            `;
            mount(section.querySelector('[data-activity-trace]'), events, { defaultView: 'all' });
        } catch (_) {
            // The original activity list remains usable if enhancement fails.
        } finally {
            inFlight = false;
        }
    }

    function scheduleEnhance() {
        if (scheduled) return;
        scheduled = true;
        setTimeout(() => {
            scheduled = false;
            void enhanceActivity();
        }, 40);
    }

    const observer = new MutationObserver(scheduleEnhance);
    observer.observe(detail, { childList: true, subtree: true });
    document.addEventListener('click', event => {
        if (event.target.closest('[data-job-id]')) scheduleEnhance();
    }, true);
    setInterval(scheduleEnhance, 5000);
    scheduleEnhance();
})();
