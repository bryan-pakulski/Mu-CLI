(() => {
    'use strict';

    const openBtn = document.getElementById('work-manage');
    const backdrop = document.getElementById('work-management-backdrop');
    if (!openBtn || !backdrop) return;

    const closeBtn = document.getElementById('work-management-close');
    const applyBtn = document.getElementById('work-management-apply');
    const resetBtn = document.getElementById('work-management-reset');
    const queryEl = document.getElementById('work-management-query-text');
    const statusEl = document.getElementById('work-management-status');
    const archiveEl = document.getElementById('work-management-archive');
    const scopeEl = document.getElementById('work-management-scope');
    const ageEl = document.getElementById('work-management-age');
    const repoEl = document.getElementById('work-management-repository');
    const reportEl = document.getElementById('work-management-report-data');
    const resultsEl = document.getElementById('work-management-results');
    const selectionEl = document.getElementById('work-management-selection');
    const rangeEl = document.getElementById('work-management-range');
    const previousBtn = document.getElementById('work-management-previous');
    const nextBtn = document.getElementById('work-management-next');
    const selectAllEl = document.getElementById('work-management-select-all');
    const archiveSelectedBtn = document.getElementById('work-management-archive-selected');
    const restoreSelectedBtn = document.getElementById('work-management-restore-selected');
    const deleteSelectedBtn = document.getElementById('work-management-delete-selected');
    const exportJson = document.getElementById('work-management-export-json');
    const exportCsv = document.getElementById('work-management-export-csv');
    const toastEl = document.getElementById('work-toast');

    const state = {
        rows: [],
        total: 0,
        offset: 0,
        limit: 50,
        selected: new Set(),
        loading: false,
    };

    function escapeHtml(value) {
        return String(value ?? '')
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#039;');
    }

    function toast(message, isError = false) {
        if (!toastEl) return;
        toastEl.textContent = String(message || '');
        toastEl.style.color = isError ? 'var(--err)' : 'var(--text-soft)';
        toastEl.hidden = false;
        clearTimeout(toast._timer);
        toast._timer = setTimeout(() => { toastEl.hidden = true; }, 4200);
    }

    function statusLabel(value) {
        return String(value || '').replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase());
    }

    function fmtMoney(value) {
        return `$${Number(value || 0).toFixed(2)}`;
    }

    function fmtDuration(value) {
        let seconds = Math.max(0, Math.round(Number(value || 0)));
        const hours = Math.floor(seconds / 3600);
        seconds -= hours * 3600;
        const minutes = Math.floor(seconds / 60);
        seconds -= minutes * 60;
        if (hours) return `${hours}h ${minutes}m`;
        if (minutes) return `${minutes}m ${seconds}s`;
        return `${seconds}s`;
    }

    function fmtDate(value) {
        const epoch = Number(value || 0);
        if (!epoch) return '—';
        return new Date(epoch * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    }

    async function api(path, options = {}) {
        const init = { ...options, headers: { ...(options.headers || {}) } };
        if (init.body && typeof init.body !== 'string') {
            init.headers['Content-Type'] = 'application/json';
            init.body = JSON.stringify(init.body);
        }
        const response = await fetch(path, init);
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            const detail = data?.detail || `${response.status} ${response.statusText}`;
            throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
        }
        return data;
    }

    function filterParams({ includePaging = true } = {}) {
        const params = new URLSearchParams();
        const q = String(queryEl.value || '').trim();
        const status = String(statusEl.value || '').trim();
        const repository = String(repoEl.value || '').trim();
        if (q) params.set('q', q);
        if (status && status !== 'all') params.append('status', status);
        if (repository) params.set('repository', repository);
        params.set('archive', archiveEl.value || 'all');
        params.set('scope', scopeEl.value || 'history');
        const days = Number(ageEl.value || 0);
        if (days > 0) params.set('created_after', String(Date.now() / 1000 - days * 86400));
        if (includePaging) {
            params.set('limit', String(state.limit));
            params.set('offset', String(state.offset));
        }
        return params;
    }

    function reportExportHref(format) {
        const params = filterParams({ includePaging: false });
        params.set('format', format);
        return `/api/jobs/report/export?${params.toString()}`;
    }

    function renderReport(report) {
        const statusEntries = Object.entries(report?.status_counts || {}).slice(0, 6);
        const repositories = Array.isArray(report?.repositories) ? report.repositories.slice(0, 5) : [];
        reportEl.innerHTML = `
            <div class="work-report-metrics">
                <div class="work-report-metric"><span>Jobs</span><strong>${Number(report?.total_jobs || 0)}</strong></div>
                <div class="work-report-metric"><span>Archived</span><strong>${Number(report?.archived_jobs || 0)}</strong></div>
                <div class="work-report-metric"><span>Attempts</span><strong>${Number(report?.attempts || 0)}</strong></div>
                <div class="work-report-metric"><span>Cost</span><strong>${fmtMoney(report?.total_cost_usd)}</strong></div>
                <div class="work-report-metric"><span>Avg runtime</span><strong>${fmtDuration(report?.average_elapsed_seconds)}</strong></div>
                <div class="work-report-metric"><span>Failure rate</span><strong>${(Number(report?.failure_rate || 0) * 100).toFixed(1)}%</strong></div>
            </div>
            <div class="work-report-breakdown">
                <strong>Breakdown</strong>
                <div class="work-report-breakdown-list">
                    ${statusEntries.map(([status, count]) => `<div><span>${escapeHtml(statusLabel(status))}</span><span>${Number(count)}</span><span></span></div>`).join('')}
                    ${repositories.map(item => `<div><span title="${escapeHtml(item.repository)}">${escapeHtml(item.repository)}</span><span>${Number(item.jobs)}</span><span>${fmtMoney(item.cost_usd)}</span></div>`).join('')}
                    ${!statusEntries.length && !repositories.length ? '<div><span>No matching history</span><span></span><span></span></div>' : ''}
                </div>
            </div>
        `;
    }

    function rowActions(job) {
        return `
            <div class="work-management-row-actions">
                <a href="/api/jobs/${encodeURIComponent(job.id)}/debug-export" title="Export job telemetry">Debug export</a>
                ${job.archived
                    ? `<button type="button" data-manage-action="restore" data-job-id="${escapeHtml(job.id)}">Restore</button>`
                    : job.manageable ? `<button type="button" data-manage-action="archive" data-job-id="${escapeHtml(job.id)}">Archive</button>` : ''}
                ${job.archived ? `<button type="button" class="delete" data-manage-action="delete" data-job-id="${escapeHtml(job.id)}">Delete</button>` : ''}
            </div>
        `;
    }

    function renderRows() {
        if (!state.rows.length) {
            resultsEl.innerHTML = '<tr><td colspan="8"><div class="work-loading">No jobs match this query.</div></td></tr>';
        } else {
            resultsEl.innerHTML = state.rows.map(job => `
                <tr data-history-job="${escapeHtml(job.id)}">
                    <td><input type="checkbox" data-history-select="${escapeHtml(job.id)}" ${state.selected.has(job.id) ? 'checked' : ''}></td>
                    <td>
                        <div class="work-management-title">
                            <strong title="${escapeHtml(job.title)}">${escapeHtml(job.title)}</strong>
                            <span>${escapeHtml(job.id.slice(0, 12))}${job.archived ? ' · archived' : ''}</span>
                        </div>
                    </td>
                    <td><span class="work-management-status">${escapeHtml(statusLabel(job.status))}</span>${job.archived ? '<span class="work-management-archive-mark">A</span>' : ''}</td>
                    <td class="work-management-repo" title="${escapeHtml(job.repository || '')}">${escapeHtml(job.repository || '—')}</td>
                    <td>${fmtMoney(job.cost_usd)}</td>
                    <td>${Number(job.attempt_count || 0)}</td>
                    <td title="Worked ${escapeHtml(fmtDuration(job.elapsed_seconds))}">${escapeHtml(fmtDate(job.updated_at))}</td>
                    <td>${rowActions(job)}</td>
                </tr>
            `).join('');
        }

        resultsEl.querySelectorAll('[data-history-select]').forEach(input => {
            input.addEventListener('change', () => {
                const id = input.dataset.historySelect;
                if (input.checked) state.selected.add(id); else state.selected.delete(id);
                updateSelection();
            });
        });
        resultsEl.querySelectorAll('[data-manage-action]').forEach(button => {
            button.addEventListener('click', () => void rowAction(button.dataset.manageAction, button.dataset.jobId));
        });
        updateSelection();
    }

    function updateSelection() {
        // Drop selections no longer represented by the current query page only
        // when the user explicitly hits Select all. Persistent cross-page
        // selection is otherwise useful for bulk archival.
        selectionEl.textContent = `${state.selected.size} selected`;
        const disabled = state.selected.size === 0;
        archiveSelectedBtn.disabled = disabled;
        restoreSelectedBtn.disabled = disabled;
        deleteSelectedBtn.disabled = disabled;
        const visibleIds = state.rows.map(job => job.id);
        selectAllEl.checked = Boolean(visibleIds.length && visibleIds.every(id => state.selected.has(id)));
        selectAllEl.indeterminate = Boolean(!selectAllEl.checked && visibleIds.some(id => state.selected.has(id)));
    }

    async function load({ resetOffset = false } = {}) {
        if (state.loading) return;
        if (resetOffset) state.offset = 0;
        state.loading = true;
        resultsEl.innerHTML = '<tr><td colspan="8"><div class="work-loading">Loading job history…</div></td></tr>';
        try {
            const params = filterParams();
            const reportParams = filterParams({ includePaging: false });
            const [history, reportData] = await Promise.all([
                api(`/api/jobs/history?${params.toString()}`),
                api(`/api/jobs/report?${reportParams.toString()}`),
            ]);
            state.rows = history.jobs || [];
            state.total = Number(history.total || 0);
            renderReport(reportData.report || {});
            renderRows();
            const start = state.total ? state.offset + 1 : 0;
            const end = Math.min(state.total, state.offset + state.rows.length);
            rangeEl.textContent = `${start}–${end} of ${state.total}`;
            previousBtn.disabled = state.offset <= 0;
            nextBtn.disabled = state.offset + state.limit >= state.total;
            exportJson.href = reportExportHref('json');
            exportCsv.href = reportExportHref('csv');
        } catch (error) {
            resultsEl.innerHTML = `<tr><td colspan="8"><div class="work-loading">${escapeHtml(error.message)}</div></td></tr>`;
            toast(error.message, true);
        } finally {
            state.loading = false;
        }
    }

    async function rowAction(action, jobId) {
        try {
            if (action === 'archive') {
                await api(`/api/jobs/${encodeURIComponent(jobId)}/archive`, { method: 'POST', body: {} });
                toast('Job archived.');
            } else if (action === 'restore') {
                await api(`/api/jobs/${encodeURIComponent(jobId)}/restore`, { method: 'POST', body: {} });
                toast('Job restored.');
            } else if (action === 'delete') {
                if (!window.confirm('Permanently delete this archived job record, telemetry, evidence and clean managed worktree? The Git branch is preserved.')) return;
                await api(`/api/jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE' });
                state.selected.delete(jobId);
                toast('Archived job deleted.');
            }
            document.getElementById('work-refresh')?.click();
            await load();
        } catch (error) {
            toast(error.message, true);
        }
    }

    async function bulk(action) {
        const ids = [...state.selected];
        if (!ids.length) return;
        if (action === 'delete' && !window.confirm(`Permanently delete ${ids.length} selected archived jobs? Git branches are preserved.`)) return;
        try {
            const result = await api('/api/jobs/manage/bulk', {
                method: 'POST',
                body: { action, job_ids: ids },
            });
            const message = `${result.succeeded} ${action} succeeded${result.failed ? ` · ${result.failed} failed` : ''}`;
            toast(message, Boolean(result.failed));
            if (action === 'delete') {
                result.results?.filter(item => item.ok).forEach(item => state.selected.delete(item.job_id));
            }
            document.getElementById('work-refresh')?.click();
            await load();
        } catch (error) {
            toast(error.message, true);
        }
    }

    function open() {
        backdrop.hidden = false;
        document.body.classList.add('work-management-open');
        void load({ resetOffset: true });
        setTimeout(() => queryEl.focus(), 20);
    }

    function close() {
        backdrop.hidden = true;
        document.body.classList.remove('work-management-open');
    }

    function reset() {
        queryEl.value = '';
        statusEl.value = 'all';
        archiveEl.value = 'all';
        scopeEl.value = 'history';
        ageEl.value = '0';
        repoEl.value = '';
        state.selected.clear();
        void load({ resetOffset: true });
    }

    openBtn.addEventListener('click', open);
    closeBtn.addEventListener('click', close);
    backdrop.addEventListener('click', event => { if (event.target === backdrop) close(); });
    applyBtn.addEventListener('click', () => void load({ resetOffset: true }));
    resetBtn.addEventListener('click', reset);
    previousBtn.addEventListener('click', () => { state.offset = Math.max(0, state.offset - state.limit); void load(); });
    nextBtn.addEventListener('click', () => { state.offset += state.limit; void load(); });
    selectAllEl.addEventListener('change', () => {
        state.rows.forEach(job => {
            if (selectAllEl.checked) state.selected.add(job.id); else state.selected.delete(job.id);
        });
        renderRows();
    });
    archiveSelectedBtn.addEventListener('click', () => void bulk('archive'));
    restoreSelectedBtn.addEventListener('click', () => void bulk('restore'));
    deleteSelectedBtn.addEventListener('click', () => void bulk('delete'));
    queryEl.addEventListener('keydown', event => {
        if (event.key === 'Enter') { event.preventDefault(); void load({ resetOffset: true }); }
    });
    [statusEl, archiveEl, scopeEl, ageEl].forEach(input => input.addEventListener('change', () => void load({ resetOffset: true })));
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && !backdrop.hidden) close();
    });
})();
