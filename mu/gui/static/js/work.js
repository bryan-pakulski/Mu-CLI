(() => {
    'use strict';

    const root = document.getElementById('work-app');
    if (!root) return;

    const boardEl = document.getElementById('work-board');
    const detailEl = document.getElementById('work-detail');
    const totalEl = document.getElementById('work-total');
    const refreshBtn = document.getElementById('work-refresh');
    const newBtn = document.getElementById('work-new');
    const backdrop = document.getElementById('work-create-backdrop');
    const createForm = document.getElementById('work-create-form');
    const createError = document.getElementById('work-create-error');
    const sessionEl = document.getElementById('work-session-context');
    const repoHint = document.getElementById('job-repository-hint');
    const executionHelp = document.getElementById('work-execution-help');
    const toastEl = document.getElementById('work-toast');

    const sessionName = String(root.dataset.session || '').trim();
    const state = {
        board: null,
        selectedId: null,
        selectedJob: null,
        loadingDetail: false,
        poll: null,
    };

    const SECTIONS = [
        ['needs_you', 'Needs you'],
        ['running', 'Running'],
        ['queued', 'Queued'],
        ['ready', 'Ready for review'],
        ['failed', 'Failed'],
        ['done', 'Done'],
    ];

    const EVENT_LABELS = {
        job_created: 'Job created',
        status_changed: 'Status changed',
        worker_process_started: 'Worker started',
        worker_process_exited: 'Worker exited',
        worker_lease_acquired: 'Worker claimed job',
        worktree_ready: 'Worktree ready',
        checkpoint_created: 'Checkpoint',
        agent_message: 'Agent response',
        tool_call_ui: 'Tool call',
        runtime_error: 'Runtime error',
        verification_evidence_created: 'Verification evidence',
        verification_failed: 'Verification failed',
        work_receipt_updated: 'Receipt updated',
        human_response: 'Human response',
        interaction_response: 'Human decision',
        interaction_response_consumed: 'Decision consumed',
        review_feedback: 'Changes requested',
        verification_contract_updated: 'Validation updated',
    };

    function escapeHtml(value) {
        return String(value ?? '')
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#039;');
    }

    function lines(value) {
        return String(value || '')
            .split(/\r?\n/)
            .map(item => item.trim())
            .filter(Boolean);
    }

    function fmtMoney(value) {
        const number = Number(value || 0);
        return `$${number.toFixed(2)}`;
    }

    function fmtDuration(value) {
        let seconds = Math.max(0, Math.round(Number(value || 0)));
        const hours = Math.floor(seconds / 3600);
        seconds -= hours * 3600;
        const minutes = Math.floor(seconds / 60);
        seconds -= minutes * 60;
        if (hours) return `${hours}h ${minutes}m ${seconds}s`;
        if (minutes) return `${minutes}m ${seconds}s`;
        return `${seconds}s`;
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

    function statusLabel(status) {
        return String(status || '')
            .replaceAll('_', ' ')
            .replace(/\b\w/g, letter => letter.toUpperCase());
    }

    function eventBody(event) {
        const payload = event && typeof event.payload === 'object' ? event.payload : {};
        if (event.event_type === 'agent_message') return String(payload.text || '').slice(0, 1200);
        if (event.event_type === 'tool_call_ui') return String(payload.tool_name || '');
        if (event.event_type === 'human_response' || event.event_type === 'review_feedback') return String(payload.detail || '');
        if (event.event_type === 'checkpoint_created') return `${payload.label || 'checkpoint'} · ${String(payload.sha || '').slice(0, 12)}`;
        if (event.event_type === 'status_changed') {
            return `${event.from_status || 'new'} → ${event.to_status || ''}${event.reason ? ` · ${event.reason}` : ''}`;
        }
        if (event.reason) return String(event.reason);
        for (const key of ['summary', 'text', 'message', 'command', 'error']) {
            if (payload[key]) return String(payload[key]).slice(0, 1200);
        }
        return '';
    }

    async function api(path, options = {}) {
        const init = { ...options };
        init.headers = { ...(init.headers || {}) };
        if (init.body && typeof init.body !== 'string') {
            init.headers['Content-Type'] = 'application/json';
            init.body = JSON.stringify(init.body);
        }
        const response = await fetch(path, init);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            const detail = data && data.detail ? data.detail : `${response.status} ${response.statusText}`;
            throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
        }
        return data;
    }

    function toast(message, isError = false) {
        toastEl.textContent = String(message || '');
        toastEl.style.color = isError ? 'var(--err)' : 'var(--text-soft)';
        toastEl.hidden = false;
        clearTimeout(toast._timer);
        toast._timer = setTimeout(() => { toastEl.hidden = true; }, 3800);
    }

    function currentJobs() {
        if (!state.board || !state.board.sections) return [];
        return SECTIONS.flatMap(([key]) => state.board.sections[key] || []);
    }

    function renderBoard() {
        const board = state.board;
        if (!board || !board.sections) {
            boardEl.innerHTML = '<div class="work-board-empty">No job data.</div>';
            return;
        }
        const total = Object.values(board.counts || {}).reduce((sum, value) => sum + Number(value || 0), 0);
        totalEl.textContent = String(total);
        if (!total) {
            boardEl.innerHTML = '<div class="work-board-empty">No engineering jobs yet. Queue one to begin.</div>';
            return;
        }
        boardEl.innerHTML = SECTIONS.map(([key, label]) => {
            const jobs = board.sections[key] || [];
            if (!jobs.length) return '';
            return `
                <section class="work-board-section" data-section="${key}">
                    <header class="work-board-section-head"><span>${label}</span><span>${jobs.length}</span></header>
                    ${jobs.map(job => `
                        <button class="work-job-card ${job.id === state.selectedId ? 'is-selected' : ''}" data-job-id="${escapeHtml(job.id)}">
                            <div class="work-card-top">
                                <span class="work-card-title">${escapeHtml(job.title)}</span>
                                <span class="work-status" data-status="${escapeHtml(job.status)}">${escapeHtml(statusLabel(job.status))}</span>
                            </div>
                            <div class="work-card-bottom">
                                <span class="work-card-meta">${escapeHtml(job.needs_attention ? (job.attention_detail || job.attention_reason) : (job.branch || job.repository || 'Waiting for workspace'))}</span>
                                <span class="work-card-cost">${fmtMoney(job.cost_usd)}</span>
                            </div>
                        </button>
                    `).join('')}
                </section>
            `;
        }).join('');
        boardEl.querySelectorAll('[data-job-id]').forEach(button => {
            button.addEventListener('click', () => selectJob(button.dataset.jobId));
        });
    }

    async function refreshBoard({ quiet = false, refreshSelected = false } = {}) {
        if (!quiet) boardEl.classList.add('is-refreshing');
        try {
            const board = await api('/api/jobs/board');
            state.board = board;
            const stillExists = state.selectedId && currentJobs().some(job => job.id === state.selectedId);
            if (state.selectedId && !stillExists) state.selectedId = null;
            renderBoard();
            if (refreshSelected && state.selectedId && !state.loadingDetail) {
                await selectJob(state.selectedId, { quiet: true });
            }
        } catch (error) {
            if (!quiet) toast(error.message, true);
        } finally {
            boardEl.classList.remove('is-refreshing');
        }
    }

    function latestAttentionContext(events) {
        for (let index = events.length - 1; index >= 0; index -= 1) {
            const event = events[index];
            if (event.to_status === 'needs_human') return event.payload || {};
        }
        return {};
    }

    function verificationRows(verification) {
        const checks = verification && Array.isArray(verification.checks) ? verification.checks : [];
        if (!checks.length) return '<p class="work-muted">No deterministic checks have run yet.</p>';
        return `<div class="work-verification-list">${checks.map(check => `
            <div class="work-verification-row ${check.passed ? 'is-pass' : 'is-fail'}">
                <span class="work-check-mark">${check.passed ? '✓' : '×'}</span>
                <span class="work-check-command" title="${escapeHtml(check.command)}">${escapeHtml(check.command)}</span>
                <span class="work-check-duration">${Number(check.duration_seconds || 0).toFixed(2)}s</span>
            </div>
            ${!check.passed && (check.stderr || check.stdout || check.error) ? `
                <pre class="work-diff">${escapeHtml(String(check.error || check.stderr || check.stdout).slice(-8000))}</pre>
            ` : ''}
        `).join('')}</div>`;
    }

    function timelineRows(events) {
        const relevant = events
            .filter(event => EVENT_LABELS[event.event_type] || ['runtime_status', 'runtime_error'].includes(event.event_type))
            .slice(-35)
            .reverse();
        if (!relevant.length) return '<p class="work-muted">No activity recorded yet.</p>';
        return `<div class="work-timeline">${relevant.map(event => `
            <div class="work-timeline-item">
                <div class="work-timeline-head">
                    <span class="work-timeline-kind">${escapeHtml(EVENT_LABELS[event.event_type] || statusLabel(event.event_type))}</span>
                    <span class="work-timeline-time">${escapeHtml(fmtWhen(event.created_at))}</span>
                </div>
                ${eventBody(event) ? `<div class="work-timeline-body">${escapeHtml(eventBody(event))}</div>` : ''}
            </div>
        `).join('')}</div>`;
    }

    function receiptRows(job, receipt) {
        const git = receipt.git || {};
        const execution = job.execution || {};
        return `
            <div class="work-receipt-list">
                <div class="work-receipt-row"><span class="work-receipt-label">Repository</span><span class="work-receipt-value" title="${escapeHtml(job.repository || '')}">${escapeHtml(job.repository || '—')}</span></div>
                <div class="work-receipt-row"><span class="work-receipt-label">Branch</span><span class="work-receipt-value">${escapeHtml(job.branch || '—')}</span></div>
                <div class="work-receipt-row"><span class="work-receipt-label">Base</span><span class="work-receipt-value">${escapeHtml(String(job.base_sha || '—').slice(0, 12))}</span></div>
                <div class="work-receipt-row"><span class="work-receipt-label">Head</span><span class="work-receipt-value">${escapeHtml(String(git.head_sha || '—').slice(0, 12))}</span></div>
                <div class="work-receipt-row"><span class="work-receipt-label">Runtime</span><span class="work-receipt-value">${escapeHtml(`${execution.provider || '—'} · ${execution.model || '—'}`)}</span></div>
                <div class="work-receipt-row"><span class="work-receipt-label">Mode</span><span class="work-receipt-value">${escapeHtml(execution.agent_mode || 'default')}</span></div>
                <div class="work-receipt-row"><span class="work-receipt-label">Workspace clean</span><span class="work-receipt-value">${git.dirty === false ? 'yes' : git.dirty === true ? 'no' : 'unknown'}</span></div>
            </div>
        `;
    }

    function actionPanel(job, events) {
        const context = latestAttentionContext(events);
        if (job.status === 'needs_human') {
            if (job.attention_reason === 'approval_required') {
                const canApprove = context.can_approve !== false;
                return `
                    <aside class="work-action-panel">
                        <h3>Approval required</h3>
                        <p>${escapeHtml(job.attention_detail || `Approve ${context.tool_name || 'tool request'}?`)}</p>
                        ${context.tool_name ? `<div class="work-receipt-row"><span class="work-receipt-label">Tool</span><span class="work-receipt-value">${escapeHtml(context.tool_name)}</span></div>` : ''}
                        ${context.tool_args ? `<pre class="work-diff">${escapeHtml(JSON.stringify(context.tool_args, null, 2))}</pre>` : ''}
                        <textarea id="work-response-detail" rows="3" placeholder="Optional explanation"></textarea>
                        <div class="work-action-row">
                            ${canApprove ? '<button class="work-primary" data-action="approve">Approve</button>' : ''}
                            <button class="work-danger" data-action="deny">Deny</button>
                        </div>
                        ${!canApprove ? '<div class="work-action-note">This request cannot be approved because the modification preview failed.</div>' : ''}
                    </aside>
                `;
            }
            if (job.attention_reason === 'verification_required') {
                return `
                    <aside class="work-action-panel">
                        <h3>Validation required</h3>
                        <p>${escapeHtml(job.attention_detail || 'Add one or more deterministic validation commands.')}</p>
                        <textarea id="work-response-detail" rows="5" placeholder="One command per line\npytest tests/auth\nnpm run typecheck"></textarea>
                        <div class="work-action-row"><button class="work-primary" data-action="respond">Save & continue</button></div>
                    </aside>
                `;
            }
            const options = Array.isArray(context.choices) ? context.choices : Array.isArray(context.options) ? context.options : [];
            return `
                <aside class="work-action-panel">
                    <h3>Input required</h3>
                    <p>${escapeHtml(job.attention_detail || 'The agent needs your input to continue.')}</p>
                    ${options.length ? `<div class="work-action-note">Options: ${options.map(option => escapeHtml(typeof option === 'object' ? (option.label || option.value || JSON.stringify(option)) : option)).join(' · ')}</div>` : ''}
                    <textarea id="work-response-detail" rows="4" placeholder="Your response"></textarea>
                    <div class="work-action-row"><button class="work-primary" data-action="respond">Respond & continue</button></div>
                </aside>
            `;
        }
        if (job.status === 'ready_for_review') {
            return `
                <aside class="work-action-panel">
                    <h3>Review decision</h3>
                    <p>Verification passed. Review the evidence and diff before deciding what happens next.</p>
                    <textarea id="work-review-feedback" rows="4" placeholder="Feedback for another implementation pass"></textarea>
                    <div class="work-action-row">
                        <button class="work-primary" data-action="changes">Request changes</button>
                        <button class="work-action" data-action="continue">Continue work</button>
                        <button class="work-danger" data-action="discard">Discard</button>
                    </div>
                    <div class="work-action-note">Merge/PR actions arrive in Milestone 5. M4 deliberately stops at reviewed, verified code.</div>
                </aside>
            `;
        }
        if (['failed', 'timed_out', 'budget_exceeded', 'environment_error'].includes(job.status)) {
            return `
                <aside class="work-action-panel">
                    <h3>Recovery</h3>
                    <p>This job stopped before reaching review. Add guidance and retry the same durable branch/session.</p>
                    <textarea id="work-review-feedback" rows="4" placeholder="Optional guidance for the retry"></textarea>
                    <div class="work-action-row">
                        <button class="work-primary" data-action="continue">Retry / continue</button>
                        <button class="work-danger" data-action="discard">Discard</button>
                    </div>
                </aside>
            `;
        }
        if (!['merged', 'cancelled'].includes(job.status)) {
            return `
                <aside class="work-action-panel">
                    <h3>${escapeHtml(statusLabel(job.status))}</h3>
                    <p>MuCLI owns execution in the background. You can close this page without affecting the job.</p>
                    <div class="work-action-row"><button class="work-danger" data-action="discard">Cancel job</button></div>
                </aside>
            `;
        }
        return `
            <aside class="work-action-panel">
                <h3>${escapeHtml(statusLabel(job.status))}</h3>
                <p>This job is no longer active.</p>
            </aside>
        `;
    }

    function wireActions(job) {
        detailEl.querySelectorAll('[data-action]').forEach(button => {
            button.addEventListener('click', async () => {
                const action = button.dataset.action;
                const response = document.getElementById('work-response-detail');
                const feedback = document.getElementById('work-review-feedback');
                const detail = String((response && response.value) || (feedback && feedback.value) || '').trim();
                button.disabled = true;
                try {
                    if (action === 'approve' || action === 'deny') {
                        await api(`/api/jobs/${encodeURIComponent(job.id)}/respond`, {
                            method: 'POST', body: { decision: action, detail },
                        });
                    } else if (action === 'respond') {
                        const payload = job.attention_reason === 'verification_required'
                            ? { detail, value: lines(detail) }
                            : { detail, value: detail, selected: detail ? [detail] : [] };
                        await api(`/api/jobs/${encodeURIComponent(job.id)}/respond`, { method: 'POST', body: payload });
                    } else if (action === 'changes') {
                        if (!detail) throw new Error('Add review feedback before requesting changes.');
                        await api(`/api/jobs/${encodeURIComponent(job.id)}/request-changes`, { method: 'POST', body: { feedback: detail } });
                    } else if (action === 'continue') {
                        await api(`/api/jobs/${encodeURIComponent(job.id)}/continue`, { method: 'POST', body: { detail } });
                    } else if (action === 'discard') {
                        if (!window.confirm('Discard/cancel this job? Its worktree and evidence will remain available for inspection.')) return;
                        await api(`/api/jobs/${encodeURIComponent(job.id)}/discard`, { method: 'POST', body: { reason: detail || 'discarded from Engineering Work' } });
                    }
                    toast('Job updated.');
                    await refreshBoard({ quiet: true });
                    await selectJob(job.id, { quiet: true });
                } catch (error) {
                    toast(error.message, true);
                } finally {
                    button.disabled = false;
                }
            });
        });
    }

    function renderDetail(job, receipt, events, diff) {
        const outcome = receipt.outcome || {};
        const git = receipt.git || {};
        const verification = receipt.verification || null;
        const activity = receipt.activity || {};
        const ready = job.status === 'ready_for_review';
        const needs = job.status === 'needs_human' || job.status === 'conflicted';
        const failed = ['failed', 'timed_out', 'budget_exceeded', 'environment_error'].includes(job.status);

        detailEl.innerHTML = `
            <article class="work-detail">
                <header class="work-detail-header">
                    <div class="work-detail-header-top">
                        <div>
                            <span class="work-status" data-status="${escapeHtml(job.status)}">${escapeHtml(statusLabel(job.status))}</span>
                            <h2>${escapeHtml(job.title)}</h2>
                        </div>
                        <span class="work-card-cost">${escapeHtml(job.id.slice(0, 12))}</span>
                    </div>
                    <div class="work-detail-subtitle">${escapeHtml(job.description || 'No description supplied.')}</div>
                    ${ready ? '<div class="work-ready-banner"><strong>Ready for review.</strong> All configured deterministic verification checks passed on a clean isolated worktree.</div>' : ''}
                    ${needs ? `<div class="work-attention-banner"><strong>Needs you · ${escapeHtml(statusLabel(job.attention_reason))}</strong><br>${escapeHtml(job.attention_detail || '')}</div>` : ''}
                    ${failed ? `<div class="work-error-banner"><strong>${escapeHtml(statusLabel(job.status))}</strong><br>Review the timeline and latest verification/runtime evidence before retrying.</div>` : ''}
                </header>

                <div class="work-metrics">
                    <div class="work-metric"><div class="work-metric-label">Worked</div><div class="work-metric-value">${fmtDuration(outcome.elapsed_seconds)}</div></div>
                    <div class="work-metric"><div class="work-metric-label">Cost</div><div class="work-metric-value">${fmtMoney(outcome.cost_usd)}</div></div>
                    <div class="work-metric"><div class="work-metric-label">Attempts</div><div class="work-metric-value">${Number(outcome.attempts || 0)}</div></div>
                    <div class="work-metric"><div class="work-metric-label">Changes</div><div class="work-metric-value">+${Number(git.additions || 0)} / -${Number(git.deletions || 0)}</div></div>
                </div>

                <div class="work-detail-grid">
                    <div class="work-detail-main">
                        <section class="work-section">
                            <h3>Work receipt</h3>
                            ${receiptRows(job, receipt)}
                        </section>

                        <section class="work-section">
                            <h3>Acceptance criteria</h3>
                            ${job.acceptance_criteria && job.acceptance_criteria.length
                                ? `<ul>${job.acceptance_criteria.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>`
                                : '<p class="work-muted">No explicit acceptance criteria.</p>'}
                        </section>

                        <section class="work-section">
                            <h3>Verification · ${escapeHtml(verification ? statusLabel(verification.status) : 'Not run')}</h3>
                            ${verificationRows(verification)}
                        </section>

                        <section class="work-section">
                            <h3>Git diff</h3>
                            <div class="work-diff-summary">${escapeHtml((diff && diff.stat) || git.diff_stat || 'No diff available yet.')}</div>
                            ${diff ? `<pre class="work-diff">${escapeHtml(diff.patch || 'No changed lines.')}</pre>` : '<p class="work-muted">A committed job diff becomes available after the worktree is prepared.</p>'}
                        </section>

                        <section class="work-section">
                            <h3>Activity · ${Number(activity.events || events.length || 0)} events</h3>
                            ${timelineRows(events)}
                        </section>
                    </div>
                    <div class="work-detail-side">
                        ${actionPanel(job, events)}
                    </div>
                </div>
            </article>
        `;
        wireActions(job);
    }

    async function selectJob(jobId, { quiet = false } = {}) {
        if (!jobId) return;
        state.selectedId = jobId;
        renderBoard();
        state.loadingDetail = true;
        if (!quiet) detailEl.innerHTML = '<div class="work-empty-detail"><p>Loading evidence…</p></div>';
        try {
            const [jobResult, receiptResult, eventsResult, diffResult] = await Promise.allSettled([
                api(`/api/jobs/${encodeURIComponent(jobId)}`),
                api(`/api/jobs/${encodeURIComponent(jobId)}/receipt`),
                api(`/api/jobs/${encodeURIComponent(jobId)}/events?limit=5000`),
                api(`/api/jobs/${encodeURIComponent(jobId)}/diff?max_chars=700000`),
            ]);
            if (jobResult.status !== 'fulfilled') throw jobResult.reason;
            const job = jobResult.value.job;
            const receipt = receiptResult.status === 'fulfilled' ? receiptResult.value.receipt : {
                outcome: { cost_usd: job.cost_usd, attempts: 0, elapsed_seconds: 0 },
                git: {}, activity: {}, verification: null,
            };
            const events = eventsResult.status === 'fulfilled' ? eventsResult.value.events || [] : [];
            const diff = diffResult.status === 'fulfilled' ? diffResult.value.diff : null;
            state.selectedJob = job;
            renderDetail(job, receipt, events, diff);
        } catch (error) {
            detailEl.innerHTML = `<div class="work-empty-detail"><h2>Could not load job</h2><p>${escapeHtml(error.message)}</p></div>`;
        } finally {
            state.loadingDetail = false;
        }
    }

    function openCreate() {
        createError.textContent = '';
        backdrop.hidden = false;
        document.getElementById('job-title').focus();
    }

    function closeCreate() {
        backdrop.hidden = true;
    }

    async function submitCreate(event) {
        event.preventDefault();
        createError.textContent = '';
        const submit = document.getElementById('work-create-submit');
        submit.disabled = true;
        const title = document.getElementById('job-title').value.trim();
        const repository = document.getElementById('job-repository').value.trim();
        const provider = document.getElementById('job-provider').value.trim();
        const model = document.getElementById('job-model').value.trim();
        const mode = document.getElementById('job-mode').value.trim();
        const autoApprove = document.getElementById('job-auto-approve').checked;
        const payload = {
            title,
            description: document.getElementById('job-description').value.trim(),
            repository,
            acceptance_criteria: lines(document.getElementById('job-acceptance').value),
            validation_commands: lines(document.getElementById('job-validation').value),
            max_retries: Number(document.getElementById('job-retries').value || 2),
        };
        if (sessionName) payload.session_name = sessionName;
        const cost = Number(document.getElementById('job-cost').value || 0);
        const runtime = Number(document.getElementById('job-runtime').value || 0);
        if (cost > 0) payload.max_cost_usd = cost;
        if (runtime > 0) payload.max_runtime_seconds = runtime;
        const execution = {};
        if (provider) execution.provider = provider;
        if (model) execution.model = model;
        if (mode) execution.agent_mode = mode;
        if (autoApprove) execution.auto_approve_writes = true;
        if (Object.keys(execution).length) payload.execution = execution;
        if (!sessionName && (!provider || !model)) {
            createError.textContent = 'Provider and model are required when no current session is available.';
            submit.disabled = false;
            return;
        }
        try {
            const response = await api('/api/jobs', { method: 'POST', body: payload });
            createForm.reset();
            document.getElementById('job-retries').value = '2';
            closeCreate();
            toast(`Queued: ${response.job.title}`);
            await refreshBoard({ quiet: true });
            await selectJob(response.job.id);
        } catch (error) {
            createError.textContent = error.message;
        } finally {
            submit.disabled = false;
        }
    }

    if (sessionName) {
        sessionEl.textContent = `Inherit from session · ${sessionName}`;
        repoHint.textContent = `Leave blank to use the workspace attached to ${sessionName}.`;
        executionHelp.textContent = `Leave fields blank to inherit provider, model, mode and write-approval policy from ${sessionName}.`;
    } else {
        sessionEl.textContent = 'No session context';
        repoHint.textContent = 'Enter the Git repository path available to the MuCLI host.';
        executionHelp.textContent = 'Provider and model are required when this page was not opened from a loaded session.';
    }

    refreshBtn.addEventListener('click', () => refreshBoard({ refreshSelected: true }));
    newBtn.addEventListener('click', openCreate);
    document.getElementById('work-create-close').addEventListener('click', closeCreate);
    document.getElementById('work-create-cancel').addEventListener('click', closeCreate);
    backdrop.addEventListener('click', event => { if (event.target === backdrop) closeCreate(); });
    createForm.addEventListener('submit', submitCreate);
    window.addEventListener('keydown', event => { if (event.key === 'Escape' && !backdrop.hidden) closeCreate(); });

    refreshBoard().then(() => {
        const jobs = currentJobs();
        const preferred = jobs.find(job => job.status === 'needs_human') || jobs.find(job => job.status === 'ready_for_review') || jobs[0];
        if (preferred) selectJob(preferred.id, { quiet: true });
    });

    state.poll = window.setInterval(() => {
        refreshBoard({ quiet: true, refreshSelected: true });
    }, 5000);
})();
