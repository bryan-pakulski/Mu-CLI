/* Engineering Work — Jira link (Settings page, #/settings) + Jira ticket browser (Tickets page, #/boards).
 * Both are routes: the router's `mu:work-route` event loads them; nav links navigate.
 *
 * Settings mirror the Ollama key pattern: the token field is a password input
 * whose placeholder says "token set — type to replace"; a blank field keeps the
 * stored token. The server never returns secrets, only *_set flags.
 */
(function () {
    'use strict';

    const settingsBtn = document.getElementById('work-jira-settings-open');
    const browseBtn = document.getElementById('work-jira-open');
    const settingsPage = document.getElementById('work-jira-settings-backdrop');
    const browsePage = document.getElementById('work-jira-backdrop');
    if (!settingsBtn || !settingsPage || !browseBtn || !browsePage) return;
    const go = hash => (window.MuWork?.go ? window.MuWork.go(hash) : (location.hash = hash));

    const appEl = document.getElementById('work-app');
    const $ = id => document.getElementById(id);
    const els = {
        form: $('work-jira-settings-form'),
        status: $('work-jira-link-status'),
        baseUrl: $('jira-base-url'),
        email: $('jira-email'),
        token: $('jira-api-token'),
        tokenClear: $('jira-api-token-clear'),
        pat: $('jira-pat'),
        patClear: $('jira-pat-clear'),
        jql: $('jira-default-jql'),
        mine: $('jira-assigned-to-me'),
        boardQuery: $('jira-board-query'),
        boardFind: $('jira-board-find'),
        boardResults: $('jira-board-results'),
        preferred: $('jira-preferred-boards'),
        test: $('jira-test'),
        unlink: $('jira-unlink'),
        settingsMsg: $('jira-settings-message'),
        browseSettings: $('work-jira-browse-settings'),
        browseEmpty: $('work-jira-unlinked'),
        browseBody: $('work-jira-browser'),
        board: $('jira-browse-board'),
        sprint: $('jira-browse-sprint'),
        search: $('jira-browse-jql'),
        browseMine: $('jira-browse-mine'),
        browseDone: $('jira-browse-done'),
        statusChips: $('jira-browse-statuses'),
        load: $('jira-browse-load'),
        rows: $('jira-ticket-rows'),
        selectAll: $('jira-ticket-select-all'),
        importBtn: $('jira-import-selected'),
        browseMsg: $('jira-browse-message'),
    };

    let settings = null;
    let preferredBoards = [];
    let tickets = [];
    const selected = new Set();
    // Set by the "Run again" offer: the next import dialog starts with Run again (force) checked.
    let rerunNext = false;
    let allBoards = null;          // every board the link can see (auto-discovered, cached)
    let boardsPromise = null;
    const statusFilter = new Set(); // status names picked in the chip row (empty = all)

    function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, ch => ({
            '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        }[ch]));
    }

    function statusLabel(value) {
        return String(value || '').replace(/_/g, ' ').replace(/^./, c => c.toUpperCase());
    }

    async function api(path, options = {}) {
        const init = { ...options, headers: { ...(options.headers || {}) } };
        if (init.body && typeof init.body !== 'string') {
            init.headers['Content-Type'] = 'application/json';
            init.body = JSON.stringify(init.body);
        }
        const response = await fetch(path, init);
        let data = {};
        try { data = await response.json(); } catch (_) { data = {}; }
        if (!response.ok) {
            const detail = data && data.detail !== undefined ? data.detail : response.statusText;
            throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
        }
        return data;
    }

    function say(el, text, kind = '') {
        if (!el) return;
        el.textContent = text || '';
        el.dataset.kind = kind;
        el.hidden = !text;
    }

    // ------------------------------------------------------------ settings

    function renderLinkStatus() {
        const s = settings || {};
        const sources = s.sources || {};
        const envFields = Object.keys(sources).filter(k => sources[k] === 'env');
        let text;
        if (s.error) text = `Settings file unreadable: ${s.error}`;
        else if (s.configured) text = `Linked · ${s.base_url} · ${s.mode === 'bearer' ? 'Data Center PAT' : 'Cloud API token'}`;
        else text = 'Not linked. Add your Jira URL and an API token (Cloud) or PAT (Data Center).';
        if (envFields.length) text += ` · env overrides: ${envFields.join(', ')}`;
        els.status.textContent = text;
        els.status.dataset.linked = s.configured ? 'true' : 'false';
        browseBtn.dataset.linked = s.configured ? 'true' : 'false';
    }

    function renderPreferred() {
        if (!preferredBoards.length) {
            els.preferred.innerHTML = '<li class="work-jira-muted">No preferred boards yet. Star boards below.</li>';
            return;
        }
        els.preferred.innerHTML = preferredBoards.map(b => `
            <li class="work-jira-chip">
                <span>${escapeHtml(b.name || `Board ${b.id}`)}</span>
                <button type="button" class="work-jira-chip-remove" data-remove-board="${escapeHtml(b.id)}"
                        aria-label="Remove ${escapeHtml(b.name || b.id)} from preferred boards">×</button>
            </li>`).join('');
        els.preferred.querySelectorAll('[data-remove-board]').forEach(btn => {
            btn.addEventListener('click', () => {
                const id = Number(btn.dataset.removeBoard);
                preferredBoards = preferredBoards.filter(b => b.id !== id);
                renderPreferred();
            });
        });
    }

    function fillSettingsForm() {
        const s = settings || {};
        const prefs = s.preferences || {};
        els.baseUrl.value = s.base_url || '';
        els.email.value = s.email || '';
        els.token.value = '';
        els.pat.value = '';
        els.token.placeholder = s.api_token_set ? 'token set — type to replace' : 'Atlassian API token';
        els.pat.placeholder = s.pat_set ? 'PAT set — type to replace' : 'Personal access token';
        els.tokenClear.checked = false;
        els.patClear.checked = false;
        els.tokenClear.closest('label').hidden = !s.api_token_set;
        els.patClear.closest('label').hidden = !s.pat_set;
        els.jql.value = prefs.default_jql || '';
        els.mine.checked = !!prefs.assigned_to_me;
        preferredBoards = (prefs.boards || []).map(b => ({ ...b }));
        renderPreferred();
        renderLinkStatus();
        fillTrackerForm();
    }

    // ------------------------------------------------------------ trackers (writeback, watcher)
    // Built here (not in work.html) so the tracker controls ship with this script.

    const EVENT_LABELS = {
        created: 'Job created', plan_ready: 'Plan ready', pushed: 'Change pushed (PR)',
        done: 'Job done', failed: 'Job failed', needs_human: 'Needs a human',
    };
    const tracker = (function buildTrackerSection() {
        const box = document.createElement('div');
        box.className = 'work-jira-trackers';
        box.innerHTML = `
            <h3 class="work-jira-section">Jira deployment</h3>
            <label class="work-field work-field-wide"><span>Deployment</span>
                <select data-tr="deployment">
                    <option value="auto">Detect automatically (/serverInfo)</option>
                    <option value="cloud">Jira Cloud (REST v3)</option>
                    <option value="dc">Data Center / Server (REST v2)</option>
                </select></label>
            <label class="work-field work-field-wide"><span>Acceptance criteria field (optional)</span>
                <input data-tr="acceptance" type="text" placeholder="customfield_10035"></label>
            <h3 class="work-jira-section">Ticket writeback</h3>
            <label class="work-jira-inline"><input data-tr="wb-enabled" type="checkbox"> Comment on tickets and update their status as jobs progress</label>
            <small class="work-jira-muted" data-tr="wb-source"></small>
            <label class="work-jira-inline"><input data-tr="wb-linkpr" type="checkbox"> Link the pull request on the Jira ticket</label>
            <table class="work-jira-events"><thead><tr><th>Event</th><th>Comment</th><th>Jira status</th><th>GitHub action</th></tr></thead>
                <tbody data-tr="events"></tbody></table>
            <small class="work-jira-muted">Jira statuses are matched by name against the ticket's workflow transitions. GitHub actions: close, reopen, label:NAME, unlabel:NAME (comma-separated).</small>
            <h3 class="work-jira-section">Ticket watcher</h3>
            <label class="work-jira-inline"><input data-tr="watch-enabled" type="checkbox"> Import new tickets from saved queries automatically</label>
            <label class="work-field work-field-wide"><span>Check every (minutes)</span>
                <input data-tr="watch-interval" type="number" min="1" step="1"></label>
            <label class="work-field work-field-wide"><span>Queries — one per line: tracker | query | workspace path</span>
                <textarea data-tr="watch-queries" rows="3" placeholder="jira | project = ABC AND labels = mucli | /home/me/src/abc&#10;github | repo:acme/api is:open label:mucli | /home/me/src/api"></textarea></label>
            <small class="work-jira-muted" data-tr="github-status"></small>`;
        els.form.insertBefore(box, els.settingsMsg);
        const q = name => box.querySelector(`[data-tr="${name}"]`);
        return {
            box, deployment: q('deployment'), acceptance: q('acceptance'), wbEnabled: q('wb-enabled'),
            wbSource: q('wb-source'), wbLinkPr: q('wb-linkpr'), events: q('events'),
            watchEnabled: q('watch-enabled'), watchInterval: q('watch-interval'), watchQueries: q('watch-queries'),
            githubStatus: q('github-status'),
        };
    })();

    function fillTrackerForm() {
        const s = settings || {};
        const wb = s.writeback_settings || {};
        const events = wb.events || {};
        tracker.deployment.value = s.deployment || 'auto';
        tracker.acceptance.value = s.acceptance_field || '';
        tracker.wbEnabled.checked = !!wb.enabled;
        tracker.wbEnabled.disabled = wb.source === 'env';
        tracker.wbSource.textContent = wb.source === 'env' ? 'Set by JIRA_WRITEBACK in the server environment.' : '';
        tracker.wbLinkPr.checked = wb.link_pr !== false;
        tracker.events.innerHTML = (s.events || Object.keys(EVENT_LABELS)).map(name => {
            const ev = events[name] || {};
            return `<tr data-event="${escapeHtml(name)}">
                <td>${escapeHtml(EVENT_LABELS[name] || name)}</td>
                <td><input type="checkbox" data-field="comment" aria-label="Comment on ${escapeHtml(EVENT_LABELS[name] || name)}" ${ev.comment !== false ? 'checked' : ''}></td>
                <td><input type="text" data-field="jira_status" aria-label="Jira status for ${escapeHtml(EVENT_LABELS[name] || name)}" placeholder="leave" value="${escapeHtml(ev.jira_status || '')}"></td>
                <td><input type="text" data-field="github" aria-label="GitHub action for ${escapeHtml(EVENT_LABELS[name] || name)}" placeholder="none" value="${escapeHtml(ev.github || '')}"></td>
            </tr>`;
        }).join('');
        const watch = (s.trackers || {}).watch || {};
        tracker.watchEnabled.checked = !!watch.enabled;
        tracker.watchInterval.value = Math.max(1, Math.round((watch.interval_seconds || 900) / 60));
        tracker.watchQueries.value = (watch.queries || [])
            .map(w => [w.tracker, w.query, w.workspace_root].join(' | ')).join('\n');
        const gh = (s.trackers || {}).github || {};
        tracker.githubStatus.textContent = gh.mode === 'token' ? 'GitHub Issues: using GITHUB_TOKEN/GH_TOKEN from the server environment.'
            : gh.mode === 'gh' ? 'GitHub Issues: using the gh CLI login on the server.'
                : 'GitHub Issues: set GITHUB_TOKEN or GH_TOKEN on the server, or log in with the gh CLI.';
    }

    function trackerPayload() {
        const events = {};
        tracker.events.querySelectorAll('tr[data-event]').forEach(row => {
            events[row.dataset.event] = {
                comment: row.querySelector('[data-field="comment"]').checked,
                jira_status: row.querySelector('[data-field="jira_status"]').value.trim(),
                github: row.querySelector('[data-field="github"]').value.trim(),
            };
        });
        const queries = tracker.watchQueries.value.split('\n').map(line => line.trim()).filter(Boolean).map(line => {
            const parts = line.split('|').map(p => p.trim());
            if (parts.length < 3) return null;
            return { tracker: parts[0].toLowerCase(), query: parts.slice(1, -1).join(' | '), workspace_root: parts[parts.length - 1] };
        }).filter(Boolean);
        const body = {
            deployment: tracker.deployment.value,
            acceptance_field: tracker.acceptance.value.trim(),
            writeback: { link_pr: tracker.wbLinkPr.checked, events },
            trackers: { watch: {
                enabled: tracker.watchEnabled.checked,
                interval_seconds: Math.max(1, Number(tracker.watchInterval.value) || 15) * 60,
                queries,
            } },
        };
        if (!tracker.wbEnabled.disabled) body.writeback.enabled = tracker.wbEnabled.checked;
        return body;
    }

    async function loadSettings() {
        settings = await api('/api/jobs/jira/settings');
        return settings;
    }

    async function openSettings() {
        say(els.settingsMsg, '');
        els.boardResults.innerHTML = '';
        try {
            await loadSettings();
            fillSettingsForm();
            if (settings && settings.configured) findBoards();
        } catch (err) {
            say(els.settingsMsg, err.message, 'error');
        }
    }

    // Leaving the Settings page drops any typed-but-unsaved secrets.
    function closeSettings() {
        els.token.value = '';
        els.pat.value = '';
    }

    function settingsPayload() {
        const body = {
            ...trackerPayload(),
            base_url: els.baseUrl.value.trim(),
            email: els.email.value.trim(),
            preferences: {
                boards: preferredBoards,
                default_jql: els.jql.value.trim(),
                assigned_to_me: els.mine.checked,
            },
        };
        // Blank secret = keep stored value (the browser never sees it).
        if (els.token.value.trim()) body.api_token = els.token.value.trim();
        if (els.pat.value.trim()) body.pat = els.pat.value.trim();
        if (els.tokenClear.checked) body.clear_api_token = true;
        if (els.patClear.checked) body.clear_pat = true;
        return body;
    }

    async function saveSettings({ quiet = false } = {}) {
        settings = await api('/api/jobs/jira/settings', { method: 'PUT', body: settingsPayload() });
        allBoards = null;  // credentials may have changed
        fillSettingsForm();
        if (!quiet) say(els.settingsMsg, 'Saved.', 'ok');
        return settings;
    }

    async function testLink() {
        say(els.settingsMsg, 'Testing connection…');
        try {
            await saveSettings({ quiet: true });
            const res = await api('/api/jobs/jira/test', { method: 'POST' });
            const who = (res.user && (res.user.display_name || res.user.email || res.user.account_id)) || 'unknown user';
            say(els.settingsMsg, `Connected as ${who}.`, 'ok');
        } catch (err) {
            say(els.settingsMsg, err.message, 'error');
        }
    }

    async function unlink() {
        const ok = window.MuJobUI
            ? await window.MuJobUI.confirm('Remove the saved Jira URL, email and token? Preferred boards are kept.', { title: 'Unlink Jira', okLabel: 'Unlink', tone: 'danger' })
            : window.confirm('Remove the saved Jira URL, email and token? Preferred boards are kept.');
        if (!ok) return;
        try {
            settings = await api('/api/jobs/jira/settings', { method: 'DELETE' });
            fillSettingsForm();
            say(els.settingsMsg, 'Jira unlinked.', 'ok');
        } catch (err) {
            say(els.settingsMsg, err.message, 'error');
        }
    }

    // Boards are discovered automatically (every board the link can see,
    // suggested ones first) and filtered locally as you type.
    function fetchBoards(force = false) {
        if (allBoards && !force) return Promise.resolve(allBoards);
        if (boardsPromise && !force) return boardsPromise;
        boardsPromise = api('/api/jobs/jira/boards')
            .then(data => { allBoards = data.boards || []; return allBoards; })
            .finally(() => { boardsPromise = null; });
        return boardsPromise;
    }

    function boardMeta(b) {
        const project = b.project && b.project_name ? `${b.project} · ${b.project_name}` : (b.project || b.project_name || '');
        return [project, b.type].filter(Boolean).join(' · ');
    }

    function renderBoardResults() {
        const boards = allBoards || [];
        const q = els.boardQuery.value.trim().toLowerCase();
        const shown = q
            ? boards.filter(b => `${b.name} ${b.project || ''} ${b.project_name || ''}`.toLowerCase().includes(q))
            : boards;
        if (!shown.length) {
            const text = boards.length ? 'No boards match.' : 'No boards are visible to this Jira account.';
            els.boardResults.innerHTML = `<li class="work-jira-muted">${text}</li>`;
            return;
        }
        const preferredIds = new Set(preferredBoards.map(b => b.id));
        els.boardResults.innerHTML = shown.map(b => {
            const on = preferredIds.has(b.id);
            return `<li>
                <button type="button" class="work-jira-board-toggle" data-board-id="${escapeHtml(b.id)}"
                        aria-pressed="${on ? 'true' : 'false'}"
                        aria-label="${on ? 'Remove' : 'Add'} ${escapeHtml(b.name)} ${on ? 'from' : 'to'} preferred boards">
                    <span aria-hidden="true">${on ? '★' : '☆'}</span>
                    <span>${escapeHtml(b.name)}</span>
                    ${b.suggested ? '<span class="work-jira-suggested">Suggested</span>' : ''}
                    <small>${escapeHtml(boardMeta(b))}</small>
                </button>
            </li>`;
        }).join('');
        els.boardResults.querySelectorAll('[data-board-id]').forEach(btn => {
            btn.addEventListener('click', () => {
                const id = Number(btn.dataset.boardId);
                const board = boards.find(b => b.id === id);
                if (preferredBoards.some(b => b.id === id)) {
                    preferredBoards = preferredBoards.filter(b => b.id !== id);
                } else if (board) {
                    preferredBoards.push({ id: board.id, name: board.name, type: board.type || '' });
                }
                renderPreferred();
                renderBoardResults();
                const again = els.boardResults.querySelector(`[data-board-id="${id}"]`);
                if (again) again.focus();
            });
        });
    }

    async function findBoards({ force = false } = {}) {
        if (!allBoards || force) {
            els.boardResults.innerHTML = '<li class="work-jira-muted">Finding your boards…</li>';
        }
        try {
            await fetchBoards(force);
            renderBoardResults();
        } catch (err) {
            els.boardResults.innerHTML = `<li class="work-jira-error">${escapeHtml(err.message)}</li>`;
        }
    }

    // ------------------------------------------------------------ browser

    function boardOptions() {
        const prefs = (settings && settings.preferences && settings.preferences.boards) || [];
        const opts = ['<option value="">— JQL search (no board) —</option>'];
        const opt = b => `<option value="${escapeHtml(b.id)}">${escapeHtml(b.name || `Board ${b.id}`)}</option>`;
        const prefIds = new Set(prefs.map(b => b.id));
        if (prefs.length) opts.push(`<optgroup label="Preferred">${prefs.map(opt).join('')}</optgroup>`);
        const others = (allBoards || []).filter(b => !prefIds.has(b.id));
        const suggested = others.filter(b => b.suggested);
        const rest = others.filter(b => !b.suggested);
        if (suggested.length) opts.push(`<optgroup label="Suggested">${suggested.map(opt).join('')}</optgroup>`);
        if (rest.length) opts.push(`<optgroup label="All boards">${rest.map(opt).join('')}</optgroup>`);
        return opts.join('');
    }

    async function loadSprints() {
        const boardId = els.board.value;
        const base = '<option value="">All board issues</option><option value="backlog">Backlog</option>';
        els.sprint.innerHTML = base;
        els.sprint.disabled = !boardId;
        if (!boardId) return;
        try {
            const data = await api(`/api/jobs/jira/boards/${encodeURIComponent(boardId)}/sprints`);
            const sprints = data.sprints || [];
            els.sprint.innerHTML = base + sprints.map(s =>
                `<option value="${escapeHtml(s.id)}">${escapeHtml(s.name)}${s.state ? ` (${escapeHtml(s.state)})` : ''}</option>`
            ).join('');
            const active = sprints.find(s => s.state === 'active');
            if (active) els.sprint.value = String(active.id);
        } catch (err) {
            // Kanban boards have no sprints; the Agile API answers 400. Not fatal.
            els.sprint.disabled = true;
        }
    }

    function linkedJobCell(job) {
        if (!job) return '<span class="work-jira-muted">—</span>';
        const label = statusLabel(job.status);
        return `<button type="button" class="work-jira-job-link" data-open-job="${escapeHtml(job.job_id)}"
                    title="${escapeHtml(job.title || job.job_id)}">
                    <span class="work-status" data-status="${escapeHtml(job.status)}">${escapeHtml(label)}</span>
                    <span class="work-jira-job-id">${escapeHtml(String(job.job_id).slice(0, 10))}</span>
                </button>`;
    }

    function updateImportState() {
        els.importBtn.disabled = selected.size === 0;
        els.importBtn.textContent = selected.size ? `Create ${selected.size} job${selected.size > 1 ? 's' : ''}` : 'Create jobs';
        const importable = visibleTickets().filter(t => !(t.job && t.job.live));
        els.selectAll.checked = importable.length > 0 && importable.every(t => selected.has(t.key));
        els.selectAll.disabled = importable.length === 0;
    }

    function renderTickets() {
        const rows = visibleTickets();
        if (!rows.length) {
            els.rows.innerHTML = '<tr><td colspan="6" class="work-jira-muted">No tickets match.</td></tr>';
            updateImportState();
            return;
        }
        els.rows.innerHTML = rows.map(t => {
            const live = !!(t.job && t.job.live);
            return `<tr data-key="${escapeHtml(t.key)}" class="${live ? 'has-live-job' : ''}">
                <td><input type="checkbox" data-select-ticket="${escapeHtml(t.key)}"
                           aria-label="Select ${escapeHtml(t.key)}" ${live ? 'disabled title="Already has an active job"' : ''}
                           ${selected.has(t.key) ? 'checked' : ''}></td>
                <td><a href="${escapeHtml(t.url)}" target="_blank" rel="noopener noreferrer" class="work-mono">${escapeHtml(t.key)}</a></td>
                <td class="work-jira-summary">${escapeHtml(t.summary)}
                    ${t.issue_type ? `<small>${escapeHtml(t.issue_type)}${t.priority ? ` · ${escapeHtml(t.priority)}` : ''}</small>` : ''}</td>
                <td><span class="work-jira-ticket-status" data-category="${escapeHtml(t.status_category)}">${escapeHtml(t.status || '—')}</span></td>
                <td>${escapeHtml(t.assignee || 'Unassigned')}</td>
                <td>${linkedJobCell(t.job)}</td>
            </tr>`;
        }).join('');
        els.rows.querySelectorAll('[data-select-ticket]').forEach(box => {
            box.addEventListener('change', () => {
                if (box.checked) selected.add(box.dataset.selectTicket);
                else selected.delete(box.dataset.selectTicket);
                updateImportState();
            });
        });
        els.rows.querySelectorAll('[data-open-job]').forEach(btn => {
            btn.addEventListener('click', () => openJob(btn.dataset.openJob));
        });
        updateImportState();
    }

    // Status chips filter the loaded tickets locally (empty selection = all).
    function visibleTickets() {
        return statusFilter.size ? tickets.filter(t => statusFilter.has(t.status)) : tickets;
    }

    function renderStatusChips(statuses) {
        if (!statuses.length) {
            els.statusChips.innerHTML = '';
            return;
        }
        els.statusChips.innerHTML = statuses.map(s => {
            const on = statusFilter.has(s.name);
            return `<button type="button" class="work-jira-chip work-jira-status-chip" data-status-chip="${escapeHtml(s.name)}"
                        data-category="${escapeHtml(s.category)}" aria-pressed="${on ? 'true' : 'false'}">
                        ${escapeHtml(s.name)} <span class="work-jira-muted">${escapeHtml(s.count)}</span></button>`;
        }).join('');
        els.statusChips.querySelectorAll('[data-status-chip]').forEach(btn => {
            btn.addEventListener('click', () => {
                const name = btn.dataset.statusChip;
                if (statusFilter.has(name)) statusFilter.delete(name); else statusFilter.add(name);
                btn.setAttribute('aria-pressed', statusFilter.has(name) ? 'true' : 'false');
                renderTickets();
                reportCount();
            });
        });
    }

    function reportCount() {
        const shown = visibleTickets().length;
        const total = tickets.length;
        const scope = els.browseDone.checked ? '' : ' (done hidden)';
        const text = shown === total
            ? `${total} ticket${total === 1 ? '' : 's'}${scope}`
            : `${shown} of ${total} tickets${scope}`;
        say(els.browseMsg, text);
    }

    async function loadTickets() {
        say(els.browseMsg, 'Loading tickets…');
        els.load.disabled = true;
        try {
            const params = new URLSearchParams();
            if (els.board.value) params.set('board_id', els.board.value);
            if (els.sprint.value === 'backlog') params.set('backlog', 'true');
            else if (els.sprint.value) params.set('sprint_id', els.sprint.value);
            if (els.search.value.trim()) params.set('jql', els.search.value.trim());
            if (els.browseMine.checked) params.set('mine', 'true');
            if (els.browseDone.checked) params.set('include_done', 'true');
            const data = await api(`/api/jobs/jira/tickets?${params.toString()}`);
            tickets = data.tickets || [];
            const present = new Set(tickets.map(t => t.status));
            [...statusFilter].forEach(s => { if (!present.has(s)) statusFilter.delete(s); });
            renderStatusChips(data.statuses || []);
            const keys = new Set(tickets.map(t => t.key));
            [...selected].forEach(k => { if (!keys.has(k)) selected.delete(k); });
            renderTickets();
            reportCount();
        } catch (err) {
            say(els.browseMsg, err.message, 'error');
        } finally {
            els.load.disabled = false;
        }
    }

    async function importSelected() {
        if (!selected.size) return;
        // Workspace / repository first (like the New job sheet); the session is optional.
        let target = null;
        if (window.MuWork && typeof window.MuWork.chooseImportTarget === 'function') {
            target = await window.MuWork.chooseImportTarget({
                title: `Import ${selected.size} ticket${selected.size === 1 ? '' : 's'} into…`, okLabel: 'Create jobs', force: rerunNext });
            if (!target) return;
        }
        rerunNext = false;
        els.importBtn.disabled = true;
        say(els.browseMsg, 'Creating jobs…');
        try {
            const body = { keys: [...selected] };
            if (target) {
                body.defaults = { ...target.defaults };
                if (target.session) body.session_name = target.session;
                if (target.force) body.force = true;
            } else {
                const sessionName = String((appEl && appEl.dataset.session) || '').trim();
                if (sessionName) body.session_name = sessionName;
            }
            const res = await api('/api/jobs/jira/import', { method: 'POST', body });
            const parts = [];
            if ((res.created || []).length) parts.push(`${res.created.length} created`);
            if ((res.skipped || []).length) parts.push(`${res.skipped.length} skipped: ${res.skipped.map(k => `${k.key} (${k.reason || 'already linked'})`).join('; ')}`);
            if ((res.errors || []).length) parts.push(`${res.errors.length} failed: ${res.errors.map(e => `${e.key} ${e.error}`).join('; ')}`);
            selected.clear();
            await loadTickets();
            say(els.browseMsg, parts.join(' · ') || 'Nothing to import.', (res.errors || []).length ? 'error' : 'ok');
            offerRunAgain((res.skipped || []).filter(k => /latest job is done/.test(String(k.reason || ''))).map(k => k.key));
            if (window.MuWork && typeof window.MuWork.refreshBoard === 'function') {
                window.MuWork.refreshBoard({ quiet: true });
            }
        } catch (err) {
            say(els.browseMsg, err.message, 'error');
            updateImportState();
        }
    }

    // Tickets skipped because their latest job is done: one click re-selects them and opens
    // the import dialog with "Run again" (force) checked.
    function offerRunAgain(keys) {
        if (!keys.length || !els.browseMsg) return;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'w-btn';
        btn.dataset.variant = 'secondary';
        btn.dataset.size = 'sm';
        btn.dataset.jiraRunAgain = '';
        btn.textContent = `Run again (${keys.length})`;
        btn.title = `Start a new job for ${keys.join(', ')}`;
        btn.onclick = () => {
            keys.forEach(k => selected.add(k));
            rerunNext = true;
            void importSelected();
        };
        els.browseMsg.append(' ', btn);
    }

    function openJob(jobId) {
        if (window.MuWork && typeof window.MuWork.selectJob === 'function') {
            window.MuWork.selectJob(jobId);
        }
    }

    async function openBrowser() {
        say(els.browseMsg, '');
        try {
            await loadSettings();
        } catch (err) {
            say(els.browseMsg, err.message, 'error');
            return;
        }
        renderLinkStatus();
        const linked = !!(settings && settings.configured);
        els.browseEmpty.hidden = linked;
        els.browseBody.hidden = !linked;
        if (!linked) return;
        const prefs = settings.preferences || {};
        const previousBoard = els.board.value;
        try { await fetchBoards(); } catch (_) { /* board list is optional; JQL still works */ }
        els.board.innerHTML = boardOptions();
        if (previousBoard && [...els.board.options].some(o => o.value === previousBoard)) {
            els.board.value = previousBoard;
        } else if ((prefs.boards || []).length) {
            els.board.value = String(prefs.boards[0].id);
        } else {
            const pick = (allBoards || []).find(b => b.suggested) || (allBoards || [])[0];
            if (pick) els.board.value = String(pick.id);
        }
        if (!els.search.value) els.search.value = prefs.default_jql || '';
        els.browseMine.checked = !!prefs.assigned_to_me;
        await loadSprints();
        if (els.board.value || els.search.value.trim() || els.browseMine.checked) {
            await loadTickets();
        } else {
            tickets = [];
            els.rows.innerHTML = '<tr><td colspan="6" class="work-jira-muted">Pick a preferred board or enter a JQL query.</td></tr>';
            updateImportState();
        }
    }

    // ------------------------------------------------------------ wiring

    let current = '';
    window.addEventListener('mu:work-route', event => {
        const name = event.detail?.name;
        if (current === 'settings' && name !== 'settings') closeSettings();
        current = name;
        if (name === 'settings') openSettings();
        else if (name === 'boards') openBrowser();
    });
    els.browseSettings.addEventListener('click', () => go('#/settings'));
    els.form.addEventListener('submit', async event => {
        event.preventDefault();
        try { await saveSettings(); } catch (err) { say(els.settingsMsg, err.message, 'error'); }
    });
    els.test.addEventListener('click', testLink);
    els.unlink.addEventListener('click', unlink);
    els.boardFind.addEventListener('click', () => findBoards({ force: true }));
    els.boardQuery.addEventListener('input', () => { if (allBoards) renderBoardResults(); });
    els.boardQuery.addEventListener('keydown', event => {
        if (event.key === 'Enter') { event.preventDefault(); findBoards(); }
    });
    els.board.addEventListener('change', async () => { await loadSprints(); await loadTickets(); });
    els.sprint.addEventListener('change', loadTickets);
    els.browseDone.addEventListener('change', loadTickets);
    els.browseMine.addEventListener('change', loadTickets);
    els.load.addEventListener('click', loadTickets);
    els.search.addEventListener('keydown', event => {
        if (event.key === 'Enter') { event.preventDefault(); loadTickets(); }
    });
    els.selectAll.addEventListener('change', () => {
        visibleTickets().filter(t => !(t.job && t.job.live)).forEach(t => {
            if (els.selectAll.checked) selected.add(t.key); else selected.delete(t.key);
        });
        renderTickets();
    });
    els.importBtn.addEventListener('click', importSelected);

    // Reflect link state on the header chip without opening anything.
    loadSettings().then(renderLinkStatus).catch(() => {});
})();
