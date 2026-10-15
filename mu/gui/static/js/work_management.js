/* Jobs mode — History page (#/history): query historic jobs, aggregate report, archive /
 * restore / delete and exports. Loads whenever the router enters the history route. */
(() => {
    'use strict';

    const page = document.getElementById('work-management-backdrop');
    if (!page) return;

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
        window.MuJobUI?.toast(String(message || ''), isError ? 'danger' : 'ok');
    }
    const confirmDanger = (message, title) => (window.MuJobUI
        ? window.MuJobUI.confirm(message, { title, okLabel: 'Delete', tone: 'danger' })
        : Promise.resolve(window.confirm(message)));

    function statusLabel(value) {
        return String(value || '').replaceAll('_', ' ').replace(/\b\w/g, c => c.toUpperCase());
    }
    // Shared Wash vocabulary (window.WASH, job_status): glyph + label, never colour alone.
    function statusPill(value) {
        const st = window.WASH ? window.WASH.stateStyle(value) : { glyph: '○', semantic: 'neutral', label: statusLabel(value).toLowerCase() };
        return `<span class="w-chip work-management-status" data-tone="${st.semantic}"><span class="w-chip__glyph" aria-hidden="true">${st.glyph}</span><span>${escapeHtml(st.label)}</span></span>`;
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
        const failure = Number(report?.failure_rate || 0);
        // Stat strip (G§9.3): one row of big numbers, not a card per metric.
        const metric = (value, label, tone = '') => `<div class="w-stat work-report-metric"${tone ? ` data-tone="${tone}"` : ''}><dt class="w-stat__label">${escapeHtml(label)}</dt><dd class="w-stat__value">${value}</dd></div>`;
        reportEl.innerHTML = `
            <dl class="w-stats work-report-metrics">
                ${metric(Number(report?.total_jobs || 0), 'Jobs')}
                ${metric(Number(report?.archived_jobs || 0), 'Archived')}
                ${metric(Number(report?.attempts || 0), 'Attempts')}
                ${metric(fmtMoney(report?.total_cost_usd), 'Cost')}
                ${metric(fmtDuration(report?.average_elapsed_seconds), 'Avg runtime')}
                ${metric(`${(failure * 100).toFixed(1)}%`, 'Failure rate', failure > 0 ? 'danger' : '')}
            </dl>
            <div class="work-grid-2 work-report-breakdown">
                <section class="w-card" aria-labelledby="work-report-status-h"><header class="w-card__head"><span class="w-eyebrow">Report</span><h2 class="w-card__title" id="work-report-status-h">By status</h2></header>
                    <div class="w-card__body">${statusEntries.length ? `<ul class="work-list work-report-breakdown-list">${statusEntries.map(([status, count]) => `<li class="work-toolbar">${statusPill(status)}<span class="work-spacer"></span><span class="work-num">${Number(count)}</span></li>`).join('')}</ul>` : '<p class="work-empty-line">No matching history.</p>'}</div></section>
                <section class="w-card" aria-labelledby="work-report-repo-h"><header class="w-card__head"><span class="w-eyebrow">Report</span><h2 class="w-card__title" id="work-report-repo-h">By repository</h2></header>
                    <div class="w-card__body">${repositories.length ? `<ul class="work-list work-report-breakdown-list">${repositories.map(item => `<li class="work-toolbar"><span class="work-mono work-ellipsis work-grow" title="${escapeHtml(item.repository)}">${escapeHtml(item.repository)}</span><span class="work-num work-meta">${Number(item.jobs)} jobs</span><span class="work-num">${fmtMoney(item.cost_usd)}</span></li>`).join('')}</ul>` : '<p class="work-empty-line">No matching history.</p>'}</div></section>
            </div>
        `;
    }

    function rowActions(job) {
        return `
            <div class="w-btn-row work-management-row-actions">
                <a class="w-btn" data-variant="ghost" data-size="sm" href="/api/jobs/${encodeURIComponent(job.id)}/debug-export" title="Export job telemetry">Debug export</a>
                ${job.archived
                    ? `<button type="button" class="w-btn" data-variant="secondary" data-size="sm" data-manage-action="restore" data-job-id="${escapeHtml(job.id)}">Restore</button>`
                    : job.manageable ? `<button type="button" class="w-btn" data-variant="secondary" data-size="sm" data-manage-action="archive" data-job-id="${escapeHtml(job.id)}">Archive</button>` : ''}
                <button type="button" class="w-btn delete" data-variant="ghost" data-size="sm" data-tone="danger" data-manage-action="delete" data-job-id="${escapeHtml(job.id)}" aria-label="Delete ${escapeHtml(job.title)}" title="Cancel if running, then permanently delete (Git branch preserved)">Delete</button>
            </div>
        `;
    }

    function renderRows() {
        if (!state.rows.length) {
            resultsEl.innerHTML = '<tr><td colspan="8"><div class="work-loading">No jobs match this query.</div></td></tr>';
        } else {
            resultsEl.innerHTML = state.rows.map(job => `
                <tr data-history-job="${escapeHtml(job.id)}">
                    <td><input type="checkbox" class="w-check__input" data-history-select="${escapeHtml(job.id)}" aria-label="Select ${escapeHtml(job.title)}" ${state.selected.has(job.id) ? 'checked' : ''}></td>
                    <td>
                        <div class="work-management-title">
                            <a href="#/job/${encodeURIComponent(job.id)}" title="${escapeHtml(job.title)}">${escapeHtml(job.title)}</a>
                            <span>${escapeHtml(job.id.slice(0, 12))}${job.archived ? ' · archived' : ''}</span>
                        </div>
                    </td>
                    <td>${statusPill(job.status)}${job.archived ? '<span class="w-chip work-management-archive-mark" data-tone="neutral" title="archived"><span>archived</span></span>' : ''}</td>
                    <td class="work-management-repo" title="${escapeHtml(job.repository || '')}">${escapeHtml(job.repository || '—')}</td>
                    <td class="num" data-num>${fmtMoney(job.cost_usd)}</td>
                    <td class="num" data-num>${Number(job.attempt_count || 0)}</td>
                    <td class="num work-meta" data-num title="Worked ${escapeHtml(fmtDuration(job.elapsed_seconds))}">${escapeHtml(fmtDate(job.updated_at))}</td>
                    <td class="w-table__actions">${rowActions(job)}</td>
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
                if (!await confirmDanger('Delete this job? Running work is cancelled first; the record, telemetry, evidence and managed worktree are removed. The Git branch is preserved.', 'Delete job')) return;
                await api(`/api/jobs/${encodeURIComponent(jobId)}?force=1`, { method: 'DELETE' });
                state.selected.delete(jobId);
                toast('Job deleted. Git branch preserved.');
            }
            window.MuWork?.refreshBoard?.().catch(() => {});
            await load();
        } catch (error) {
            toast(error.message, true);
        }
    }

    async function bulk(action) {
        const ids = [...state.selected];
        if (!ids.length) return;
        if (action === 'delete' && !await confirmDanger(`Delete ${ids.length} selected jobs? Running jobs are cancelled first; records, telemetry, evidence and managed worktrees are removed. Git branches are preserved.`, `Delete ${ids.length} jobs`)) return;
        try {
            const result = await api('/api/jobs/manage/bulk', {
                method: 'POST',
                body: { action, job_ids: ids, force: action === 'delete' },
            });
            const message = `${result.succeeded} ${action} succeeded${result.failed ? ` · ${result.failed} failed` : ''}`;
            toast(message, Boolean(result.failed));
            if (action === 'delete') {
                result.results?.filter(item => item.ok).forEach(item => state.selected.delete(item.job_id));
            }
            window.MuWork?.refreshBoard?.().catch(() => {});
            await load();
        } catch (error) {
            toast(error.message, true);
        }
    }

    // The History page is a route (#/history): entering it (re)queries; nothing to close.
    function open() {
        void load({ resetOffset: true });
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

    window.addEventListener('mu:work-route', event => { if (event.detail?.name === 'history') open(); });
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
})();
