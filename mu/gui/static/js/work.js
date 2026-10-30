/* Jobs mode shell: hash routes (#/ Now, #/jobs, #/job/<id>?tab=…, #/activity, #/boards
 * (Tickets), #/history, #/settings, #/setup with its Doctor tab at #/doctor), the page header
 * (Mu §4: WashHead title / back link, the state sentence from GET /api/jobs/vm/status, the
 * current tab), the leaf alert when the connection or Docker is broken, the header menus,
 * global commands and preferences, plus the New job sheet. Page modules (work/activity.js,
 * work_jira.js, work_management.js, work_stage_models.js) listen for the `mu:work-route` event. */
(() => {
    'use strict';
    const root = document.getElementById('work-app');
    if (!root) return;
    const ui = window.MuJobUI;
    const boardEl = document.getElementById('work-board');
    const detailEl = document.getElementById('work-detail');
    const jobsEl = document.getElementById('work-jobs');
    const totalEl = document.getElementById('work-total');
    // Mu §11b panes: the list (compact rows), the main pane (the board, a job, a page) and
    // the aside (the open job's context; Activity's running stage).
    const panesEl = document.getElementById('work-panes');
    const listEl = document.getElementById('work-list');
    const listCountEl = document.getElementById('work-list-count');
    const asideEl = document.getElementById('work-aside');
    const mainEl = document.getElementById('work-main');
    const refreshBtn = document.getElementById('work-refresh');
    const newBtn = document.getElementById('work-new');
    const backdrop = document.getElementById('work-create-backdrop');
    const createForm = document.getElementById('work-create-form');
    const createError = document.getElementById('work-create-error');
    const repoHint = document.getElementById('job-repository-hint');
    const executionHelp = document.getElementById('work-execution-help');
    const stateEl = document.getElementById('work-state');
    // The session the page was opened from only preselects its workspace: jobs mode lists every
    // session's jobs and a new job asks for the workspace first (recent ones come from saved sessions).
    const entrySession = String(root.dataset.session || '').trim();
    const workspaceInput = document.getElementById('job-workspace');
    const workspaceChips = document.getElementById('job-workspace-chips');
    const titleHint = document.getElementById('job-title-hint');
    // "Title or ticket key": a Jira key imports that ticket instead of creating a titled job.
    const JIRA_KEY = /^[A-Z][A-Z0-9_]+-\d+$/;
    let sessions = null;
    // Build environments on the New job sheet: the resolved preview, or this job's override.
    const envSummaryEl = document.getElementById('job-environments-summary');
    const envHintEl = document.getElementById('job-environments-hint');
    const envResetBtn = document.getElementById('job-environments-reset');
    let envOverride = null, envPreview = null, envRequest = 0;
    // "Run in" (job_templates.md §3.1): default runtime, a template, an environment or Advanced….
    const runInHost = document.getElementById('job-run-in');
    const runIn = runInHost && window.MuRunInPicker ? window.MuRunInPicker.create(runInHost, { api, ui, toast: (t, k) => toast(t, k),
        idPrefix: 'job-run-in-trigger', onChange: () => { syncRunIn(); } }) : null;
    // The picker owns Advanced…; the legacy row button stays in the markup only as the
    // no-picker fallback, so there is one way to open the environments editor.
    if (runIn) {
        const legacyChange = document.getElementById('job-environments-change');
        if (legacyChange) legacyChange.hidden = true;
    }
    function syncRunIn() {
        const v = runIn?.value();
        envOverride = v?.kind === 'advanced' && v.bindings?.length ? v.bindings : null;
        renderEnvField();
    }
    // Agent per stage (mucli / Claude Code / Codex) on the New job sheet.
    let agentPicker = null;
    const lines = value => String(value || '').split(/\r?\n/).map(v => v.trim()).filter(Boolean);
    const esc = ui.esc;

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
    // Back-compat: older callers pass `true` for errors.
    const toast = (message, tone = 'accent') => ui.toast(String(message || ''), tone === true ? 'danger' : tone === false ? 'accent' : tone);

    /* ------------------------------------------------------------ preferences */
    const prefs = {
        get density() { return localStorage.getItem('mucli-jobs-density') === 'compact' ? 'compact' : 'comfortable'; },
        get notify() { return localStorage.getItem('mucli-jobs-notify') === '1'; },
    };
    function applyPrefs() {
        root.dataset.density = prefs.density;
        const d = document.getElementById('work-density');
        d.setAttribute('aria-label', `Density: ${prefs.density}`); d.title = `Density: ${prefs.density}`;
        d.querySelectorAll('[data-density-icon]').forEach(i => i.toggleAttribute('hidden', i.dataset.densityIcon !== prefs.density));
        const n = document.getElementById('work-notify');
        n.setAttribute('aria-pressed', String(prefs.notify));
        n.setAttribute('aria-label', prefs.notify ? 'Turn desktop notifications off' : 'Turn desktop notifications on');
        const nLabel = n.querySelector('[data-notify-label]');
        if (nLabel) nLabel.textContent = prefs.notify ? 'Turn notifications off' : 'Turn notifications on';
        n.querySelectorAll('[data-notify-icon]').forEach(i => i.toggleAttribute('hidden', (i.dataset.notifyIcon === 'on') !== prefs.notify));
        registerGlobal();
    }
    function toggleDensity() { localStorage.setItem('mucli-jobs-density', prefs.density === 'compact' ? 'comfortable' : 'compact'); applyPrefs(); }
    async function toggleNotify() {
        if (prefs.notify) { localStorage.setItem('mucli-jobs-notify', '0'); toast('Desktop notifications off', 'muted'); applyPrefs(); return; }
        if (typeof Notification === 'undefined') { toast('This browser has no desktop notifications', 'warn'); return; }
        const p = Notification.permission === 'granted' ? 'granted' : await Notification.requestPermission();
        if (p !== 'granted') { toast('Notifications are blocked for this page in the browser settings', 'warn'); return; }
        localStorage.setItem('mucli-jobs-notify', '1');
        toast('Desktop notifications on: you will be told when a job needs you', 'ok');
        applyPrefs();
    }

    /* ------------------------------------------------------------ state sentence (Mu §4.2) */
    // One sentence under the title replaces the old status chips: needs you · running · checks ·
    // spend today. Zeros are left out; "Nothing needs you" when nothing waits and nothing runs.
    let knownNeeds = null, lastStatus = null;
    const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
    const money = v => `$${(Number(v) || 0).toFixed(2)}`;
    function spendToday() {
        if (!lastHome) return 0;
        const start = new Date(); start.setHours(0, 0, 0, 0);
        const since = start.getTime() / 1000;
        return lastHome.jobs.filter(j => Number(j.updated_at || j.created_at) >= since).reduce((sum, j) => sum + (Number(j.cost_usd) || 0), 0);
    }
    function paintState() {
        if (route.name === 'job') return;  // a job's own state sentence (work/view.js) owns the header there
        const st = lastStatus || { agents: 0, checks: 0, needs_you: 0 };
        const parts = [];
        if (st.needs_you) parts.push({ text: `${st.needs_you} need${st.needs_you === 1 ? 's' : ''} you`, tone: 'attention', href: '#/' });
        if (st.agents) parts.push({ text: `${st.agents} running`, href: '#/activity' });
        if (st.checks) parts.push({ text: plural(st.checks, 'check'), href: '#/activity' });
        const spend = spendToday();
        if (spend >= 0.005) parts.push({ text: `${money(spend)} today`, tone: 'quiet' });
        if (!parts.length && lastHome) parts.push({ text: lastHome.jobs.length ? 'Nothing needs you' : 'No jobs yet', tone: 'quiet' });
        if (window.WashHead) window.WashHead.set({ state: parts });
        else if (stateEl) stateEl.textContent = parts.map(p => p.text).join(' · ');
    }
    // Docker health is silent while it's fine: the leaf alert shows only when it's down.
    function renderStatus(st) {
        lastStatus = st;
        paintState();
        const engine = String(st.engine || '');
        const alert = document.querySelector('[data-alert="engine"]');
        if (alert) {
            alert.hidden = engine !== 'down';
            alert.title = `Container engine: docker ${engine}`;
        }
    }
    // Older servers have no /vm/status: derive the counts from the home VM instead.
    let statusEndpoint = true, statusTimer = 0, lastHome = null;
    const fromHome = home => ({
        agents: home.running.filter(j => j.state !== 'checking').length,
        checks: home.jobs.filter(j => j.state === 'checking').length,
        needs_you: home.needs_you.length, engine: '',
    });
    async function refreshStatus() {
        if (statusEndpoint) {
            try { renderStatus(await api('/api/jobs/vm/status')); return; }
            catch (e) { if (/404|not found/i.test(e.message)) statusEndpoint = false; }
        }
        if (lastHome) renderStatus(fromHome(lastHome));
    }
    function scheduleStatus() { clearTimeout(statusTimer); statusTimer = setTimeout(refreshStatus, 250); }
    setInterval(() => { if (document.visibilityState === 'visible') refreshStatus(); }, 10000);
    function onHome(home) {
        lastHome = home;
        scheduleStatus();
        const ids = new Set(home.needs_you.map(j => j.id));
        if (knownNeeds && prefs.notify && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
            for (const j of home.needs_you) if (!knownNeeds.has(j.id)) {
                const n = new Notification(`${j.key || 'Job'} needs you`, { body: `${j.title} — ${j.attention_detail || j.state.replaceAll('_', ' ')}`, tag: `mucli-job-${j.id}` });
                n.onclick = () => { window.focus(); go(`#/job/${j.id}`); };
            }
        }
        knownNeeds = ids;
        paintState();
        if (route.name === 'job') setHead();
    }
    // The connection is silent while live; "Reconnecting…" shows only after a few seconds down,
    // so a page load or a brief blip never flashes it.
    let offlineTimer = 0;
    function onConnection(live) {
        const el = document.querySelector('[data-alert="live"]');
        if (!el) return;
        clearTimeout(offlineTimer);
        if (live) { el.hidden = true; return; }
        offlineTimer = setTimeout(() => { el.hidden = false; }, 3000);
    }

    /* ------------------------------------------------------------ header menus */
    // The More tab and the ⋯ tools menu: click or ArrowDown opens; Esc, outside click, Tab or
    // choosing an item closes; arrows move between items.
    function bindMenus() {
        root.querySelectorAll('[data-menu-wrap]').forEach(wrap => {
            const toggle = wrap.querySelector('[data-menu-toggle]'), menu = wrap.querySelector('[data-menu]');
            if (!toggle || !menu) return;
            const items = () => [...menu.querySelectorAll('[role=menuitem]')];
            const outside = e => { if (!wrap.contains(e.target)) close(false); };
            function close(focus = true) {
                menu.hidden = true; toggle.setAttribute('aria-expanded', 'false');
                document.removeEventListener('mousedown', outside, true);
                if (focus) toggle.focus();
            }
            function open() {
                menu.hidden = false; toggle.setAttribute('aria-expanded', 'true');
                document.addEventListener('mousedown', outside, true);
                items()[0]?.focus();
            }
            toggle.addEventListener('click', () => (menu.hidden ? open() : close()));
            toggle.addEventListener('keydown', e => { if (e.key === 'ArrowDown' && menu.hidden) { e.preventDefault(); open(); } });
            menu.addEventListener('keydown', e => {
                const list = items(), i = list.indexOf(document.activeElement);
                if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
                else if (e.key === 'ArrowDown') { e.preventDefault(); list[(i + 1) % list.length]?.focus(); }
                else if (e.key === 'ArrowUp') { e.preventDefault(); list[(i - 1 + list.length) % list.length]?.focus(); }
                else if (e.key === 'Tab') close(false);
            });
            menu.addEventListener('click', e => { if (e.target.closest('[role=menuitem]')) close(false); });
        });
    }

    /* ------------------------------------------------------------ router */
    let route = { name: 'dashboard' };
    // Route ids stay stable (#/boards, #/doctor); labels follow the UI (Tickets; Doctor is a tab of Setup).
    const PAGES = { activity: 'Activity', boards: 'Tickets', history: 'History', settings: 'Settings', setup: 'Setup' };
    const go = hash => { if (location.hash !== hash) location.hash = hash; else applyRoute(); };
    function parseHash() {
        const h = location.hash || '#/';
        if (!h.startsWith('#/')) return null; // in-page anchors (#needs-you, skip link) are not routes
        const [path, query = ''] = h.slice(1).split('?');
        const params = new URLSearchParams(query);
        const job = path.match(/^\/job\/([^/]+)$/);
        if (job) return { name: 'job', id: decodeURIComponent(job[1]), tab: params.get('tab') || '' };
        if (path === '/jobs') return { name: 'jobs' };
        // Doctor is a tab inside Setup: #/doctor (and #/setup?tab=doctor) open it there.
        if (path === '/doctor') { params.set('tab', 'doctor'); return { name: 'setup', params }; }
        const page = path.slice(1);
        if (PAGES[page]) return { name: page, params };
        return { name: 'dashboard' };
    }
    // The page header (Mu §4.1): "Work" on every tab; a job shows its own title under "← Jobs".
    function setHead() {
        const trail = route.name === 'job'
            ? [{ label: 'Jobs', to: '#/jobs' }, { label: (() => { const j = view.job(); return j?.id === route.id ? (j.key || `#${j.id.slice(0, 8)}`) : `#${route.id.slice(0, 8)}`; })() }]
            : route.name === 'jobs' ? [{ label: 'Jobs' }]
            : route.name === 'setup' && route.params?.get('tab') === 'doctor' ? [{ label: 'Setup', to: '#/setup' }, { label: 'Doctor' }]
            : PAGES[route.name] ? [{ label: PAGES[route.name] }] : [{ label: 'Now' }];
        if (route.name === 'job') {
            const j = view.job();
            const title = j?.id === route.id ? (j.title || trail[1].label) : trail[1].label;
            // With the list pane beside the job (wide leaves) the way back is the list itself.
            const listShown = window.matchMedia ? window.matchMedia('(width >= 840px)').matches : true;
            window.WashHead?.set({ title, back: listShown ? null : { label: 'Jobs', href: '#/jobs' } });
        } else {
            window.WashHead?.set({ title: 'Work', back: null });
            paintState();
        }
        if (newBtn) newBtn.hidden = route.name === 'job';
        // A job is a full page under "← Jobs": the destination tabs step aside for the job's own.
        root.dataset.routeName = route.name;
        document.title = `${trail[trail.length - 1].label} · Work · μcli`;
    }
    async function applyRoute() {
        const next = parseHash();
        if (!next) return;
        const changed = !route || route.name !== next.name;
        // Another job from the list: the open one fades out and the next fades in (Mu §4.3).
        const jobSwitch = !changed && next.name === 'job' && route.id !== next.id;
        route = next;
        if (!jobSwitch) [detailEl, asideEl].forEach(el => el?.removeAttribute('data-leaving'));
        const swap = () => { root.querySelectorAll('[data-route]').forEach(s => { s.hidden = s.dataset.route !== route.name; }); };
        // Fade between sections like a page navigation (base.css names the stage w-stage);
        // tab and job changes within a section swap in place.
        if (changed && document.startViewTransition && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
            document.startViewTransition(swap);
        } else {
            swap();
        }
        root.querySelectorAll('[data-nav]').forEach(a => {
            const on = a.dataset.nav === route.name || (route.name === 'job' && a.dataset.nav === 'jobs');
            if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
        });
        // "More" is the current tab while one of its pages is open.
        root.querySelectorAll('[data-nav-group]').forEach(b => {
            if (b.dataset.navGroup.split(' ').includes(route.name)) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
        });
        if (route.name !== 'job') view.leave();
        // Panes per route (Mu §11b): Now = list + board; a job = list + job + context;
        // Activity = the page + its running stage docked in the aside; the rest = one pane.
        if (panesEl) {
            panesEl.dataset.layout = route.name === 'job' ? 'list-main-aside' : route.name === 'dashboard' ? 'list-main' : route.name === 'activity' ? 'main-aside' : 'main';
            panesEl.removeAttribute('data-aside');
            panesEl.dataset.show = 'main';
        }
        if (asideEl) asideEl.toggleAttribute('data-dock', route.name === 'activity');
        setHead();
        if (changed && mainEl) mainEl.scrollTop = 0;
        window.dispatchEvent(new CustomEvent('mu:work-route', { detail: { name: route.name, params: route.params || new URLSearchParams() } }));
        // Now / Jobs share the dock's job preview (Mu §7.2); other routes close it.
        view.listMode(route.name);
        if (route.name === 'job') {
            if (jobSwitch && window.WashMotion) {
                await window.WashMotion.leave([detailEl, asideEl]);
                if (route !== next) return;                 // a newer route took over
            }
            try { await view.select(route.id, { tab: route.tab }); } catch (e) {
                const missing = /not found|404/i.test(e.message);
                asideEl?.querySelector('.work-job-side')?.remove();
                detailEl.innerHTML = `<div class="w-empty" data-variant="error" role="alert"><div class="w-empty__art" aria-hidden="true"><svg class="w-icon" focusable="false"><use href="/static/icons/wash.svg#i-alert-circle"></use></svg></div>
                    <h3 class="w-empty__title">${missing ? 'Job not found' : 'Couldn’t open this job'}</h3><p class="w-empty__body">${esc(missing ? `Job ${route.id} not found.` : e.message)}</p>
                    <div class="w-empty__actions"><a class="w-btn" data-variant="secondary" href="#/jobs">All jobs</a></div></div>`;
            }
            if (jobSwitch) [detailEl, asideEl].forEach(el => (window.WashMotion ? window.WashMotion.enter(el) : el?.removeAttribute('data-leaving')));
            setHead();
        }
    }
    window.addEventListener('hashchange', applyRoute);

    const view = window.MuJobWork.create({
        api, toast, boardEl, detailEl, totalEl, jobsEl, listEl, listCountEl, asideEl, onHome, onConnection,
        onOpen: id => go(id ? `#/job/${encodeURIComponent(id)}` : '#/'),
        onRoute: ({ id, tab }) => {
            if (route.name !== 'job' || route.id !== id) return;
            const hash = `#/job/${encodeURIComponent(id)}${tab ? `?tab=${tab}` : ''}`;
            if (location.hash !== hash) history.replaceState(null, '', hash);
        },
    });
    const selectJob = id => go(`#/job/${encodeURIComponent(id)}`);
    async function refreshBoard({ refreshSelected = false } = {}) {
        await view.refreshHome();
        if (refreshSelected) await view.refreshParts();
    }

    /* ------------------------------------------------------------ global commands */
    function registerGlobal() {
        const dusk = window.MuTheme ? window.MuTheme.current() === 'dusk' : document.documentElement.getAttribute('data-theme') === 'dusk';
        ui.register('global', [
            { id: 'nav:dashboard', label: 'Go to Dashboard', group: 'Navigate', key: 'g h', run: () => go('#/') },
            { id: 'nav:jobs', label: 'Go to Jobs', group: 'Navigate', key: 'g t', run: () => go('#/jobs') },
            { id: 'nav:activity', label: 'Go to Activity', group: 'Navigate', key: 'g a', run: () => go('#/activity') },
            { id: 'nav:boards', label: 'Go to Tickets', group: 'Navigate', key: 'g b', run: () => go('#/boards') },
            { id: 'nav:history', label: 'Go to History', group: 'Navigate', key: 'g r', run: () => go('#/history') },
            { id: 'nav:settings', label: 'Go to Settings', group: 'Navigate', key: 'g s', run: () => go('#/settings') },
            { id: 'nav:setup', label: 'Go to Setup (checklist)', group: 'Navigate', key: 'g u', run: () => go('#/setup') },
            { id: 'nav:doctor', label: 'Go to Setup → Doctor (check everything)', group: 'Navigate', key: 'g d', run: () => go('#/doctor') },
            { id: 'nav:analysis', label: 'Open job analysis', group: 'Navigate', run: () => { location.href = '/jobs/trace'; } },
            { id: 'nav:pricing', label: 'Open model pricing', group: 'Navigate', run: () => { location.href = '/models/costs'; } },
            { id: 'job:new', label: 'New job', group: 'Jobs', key: 'n', run: openCreate },
            { id: 'job:refresh', label: 'Refresh', group: 'Jobs', run: () => refreshBoard({ refreshSelected: true }).catch(e => toast(e.message, 'danger')) },
            { id: 'pref:theme', label: `Switch to ${dusk ? 'Paper' : 'Dusk'} theme`, group: 'Preferences', run: () => { root.querySelector('[data-route-theme]')?.click(); registerGlobal(); } },
            { id: 'pref:density', label: `Switch to ${prefs.density === 'compact' ? 'comfortable' : 'compact'} density`, group: 'Preferences', run: toggleDensity },
            { id: 'pref:notify', label: `${prefs.notify ? 'Disable' : 'Enable'} desktop notifications`, group: 'Preferences', run: () => void toggleNotify() },
            { id: 'help', label: 'Keyboard shortcuts', group: 'Help', key: '?', run: ui.help },
        ], 100);
    }

    /* ------------------------------------------------------------ new job sheet */
    async function loadSessions(force = false) {
        if (!sessions || force) {
            try { sessions = (await api('/api/jobs/sessions')).sessions || []; }
            catch (error) { sessions = []; toast(`Could not list sessions: ${error.message}`, 'danger'); }
        }
        return sessions;
    }
    const describeSession = s => [s.workspace || 'no workspace', s.provider && s.model ? `${s.provider} · ${s.model}` : '', s.loaded ? 'loaded' : ''].filter(Boolean).join(' · ');
    function sessionOptions(list, selected) {
        if (!list.length) return '<option value="">No saved sessions — create one in chat first</option>';
        return '<option value="">Choose a session…</option>' + list.map(s =>
            `<option value="${esc(s.name)}" ${s.name === selected ? 'selected' : ''}>${esc(s.name)} — ${esc(describeSession(s))}</option>`).join('');
    }
    // Recent workspaces, in session order (current session first, then most recently saved).
    function recentWorkspaces(list) {
        const seen = new Set(), out = [];
        for (const s of list) for (const w of (s.workspaces?.length ? s.workspaces : [s.workspace])) {
            if (w && !seen.has(w)) { seen.add(w); out.push(w); }
        }
        return out.slice(0, 8);
    }
    const sameWorkspace = (a, b) => String(a || '').replace(/\/+$/, '') === String(b || '').replace(/\/+$/, '');
    // The saved session whose workspace this is: it supplies model defaults, mode and environment.
    const sessionForWorkspace = path => (sessions || []).find(s => (s.workspaces?.length ? s.workspaces : [s.workspace]).some(w => sameWorkspace(w, path)));
    // Source (mu/jobs/sources.py): a folder (path, or a git URL pasted there), a repository from
    // Settings → Repositories (or another URL), or a saved session (its workspace and defaults).
    const sourceKindEl = document.getElementById('job-source-kind');
    const repoSelect = document.getElementById('job-repository');
    const repoUrlInput = document.getElementById('job-repository-url');
    const sessionSelect = document.getElementById('job-session');
    const conversationBox = document.getElementById('job-session-conversation');
    const OTHER_REPO = '\u0000other';
    let sourceKind = 'folder', repositories = [];
    const chosenRepository = () => repositories.find(r => r.name === repoSelect.value) || null;
    function chosenWorkspace() {
        if (sourceKind === 'repository') return repoSelect.value === OTHER_REPO ? repoUrlInput.value.trim() : (chosenRepository()?.url || '');
        if (sourceKind === 'session') return chosenSession()?.workspace || '';
        return workspaceInput.value.trim();
    }
    // The saved session that supplies model defaults, mode and environment.
    function chosenSession() {
        if (sourceKind === 'session') return (sessions || []).find(s => s.name === sessionSelect.value) || null;
        const path = chosenWorkspace();
        return path && !isRepoUrl(path) ? sessionForWorkspace(path) || null : null;
    }
    // A git URL (https/ssh/scp-style) in the workspace field is sent as `repository` and cloned by the server.
    const GIT_URL = /^(?:(?:https?|ssh|git):\/\/[^\s/]+\/\S+|[\w.-]+@[\w.-]+:\S+)$/;
    const isRepoUrl = value => GIT_URL.test(String(value || '').trim());
    function setSourceKind(kind) {
        sourceKind = kind;
        sourceKindEl.querySelectorAll('[data-source-kind]').forEach(b => b.setAttribute('aria-checked', String(b.dataset.sourceKind === kind)));
        document.querySelectorAll('[data-source-panel]').forEach(p => { p.hidden = p.dataset.sourcePanel !== kind; });
        renderAttachments();
        syncSessionHints();
        void refreshEnvPreview();
    }
    function renderSourcePickers() {
        const keepRepo = repoSelect.value, keepSession = sessionSelect.value;
        repoSelect.innerHTML = (repositories.length ? '' : '<option value="">No repositories in Settings yet</option>')
            + repositories.map(r => `<option value="${esc(r.name)}">${esc(r.name)} — ${esc(r.url)}${r.branch ? ` (${esc(r.branch)})` : ''}</option>`).join('')
            + `<option value="${OTHER_REPO}">Another URL…</option>`;
        document.getElementById('job-repository-names').innerHTML = repositories.map(r => `<option value="${esc(r.name)}">${esc(r.url)}</option>`).join('');
        if ([...repoSelect.options].some(o => o.value === keepRepo)) repoSelect.value = keepRepo;
        repoUrlInput.hidden = repoSelect.value !== OTHER_REPO;
        const usable = (sessions || []).filter(x => x.workspace);
        sessionSelect.innerHTML = usable.length ? usable.map(x => `<option value="${esc(x.name)}">${esc(x.name)} — ${esc(describeSession(x))}</option>`).join('')
            : '<option value="">No saved session has a workspace</option>';
        const entry = usable.find(x => x.name === entrySession) || usable.find(x => x.current);
        sessionSelect.value = [...sessionSelect.options].some(o => o.value === keepSession) && keepSession ? keepSession : (entry?.name || usable[0]?.name || '');
    }
    // Other sources (mu/jobs/multi_source.py): more folders, repositories or sessions the job may
    // change, each at /sources/<name> with its own branch and PR (a folder: a patch).
    const sourcesList = document.getElementById('job-sources');
    const SOURCE_KINDS = [['folder', 'Folder'], ['repository', 'Repository'], ['session', 'Session']];
    let extraSources = [];
    function renderExtraSources() {
        const usable = (sessions || []).filter(x => x.workspace);
        sourcesList.innerHTML = extraSources.map((src, i) => {
            const kind = `<span class="w-select"><select class="w-select__control" data-src-kind="${i}" aria-label="Kind of source ${i + 1}">${SOURCE_KINDS.map(([v, l]) =>
                `<option value="${v}" ${v === src.kind ? 'selected' : ''}>${l}</option>`).join('')}</select></span>`;
            const value = src.kind === 'session'
                ? `<span class="w-select"><select class="w-select__control" data-src-value="${i}" aria-label="Session for source ${i + 1}">${usable.map(x =>
                    `<option value="${esc(x.name)}" ${x.name === src.value ? 'selected' : ''}>${esc(x.name)} — ${esc(x.workspace)}</option>`).join('') || '<option value="">No saved session has a workspace</option>'}</select></span>`
                : `<input class="w-input" data-mono data-src-value="${i}" value="${esc(src.value)}" autocomplete="off" spellcheck="false"
                    aria-label="${src.kind === 'repository' ? 'Repository' : 'Folder'} for source ${i + 1}"
                    list="${src.kind === 'repository' ? 'job-repository-names' : 'job-workspace-recent'}"
                    placeholder="${src.kind === 'repository' ? 'a Settings repository — or a git URL' : '/home/me/src/other-project'}">`;
            return `<li>${kind}${value}<button type="button" class="w-btn" data-variant="icon" data-size="sm" data-src-remove="${i}" aria-label="Remove source ${i + 1}" title="Remove">✕</button></li>`;
        }).join('');
    }
    // A repository row names a Settings → Repositories entry, or else is a URL / path.
    const extraSourcePayload = () => extraSources.filter(src => String(src.value || '').trim()).map(src => {
        const value = src.value.trim();
        if (src.kind === 'session') return { kind: 'session', session: value };
        if (src.kind === 'repository') return repositories.some(r => r.name === value) ? { kind: 'repository', name: value } : { kind: 'repository', url: value };
        return { kind: 'folder', path: value };
    });
    // Attachments (mu/jobs/attachments.py): uploads travel base64-encoded in the create request;
    // session files are referenced by id and copied by the server.
    const ATTACH_MAX_FILES = 20, ATTACH_MAX_BYTES = 25 * 1024 * 1024;
    const attachList = document.getElementById('job-attachments');
    const attachInput = document.getElementById('job-attach-input');
    const attachSessionBtn = document.getElementById('job-attach-session');
    let attachments = [];
    const fmtSize = n => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;
    function renderAttachments() {
        const session = chosenSession();
        attachSessionBtn.hidden = !session;
        attachSessionBtn.textContent = session ? `From ${session.name}…` : 'From the session…';
        attachList.innerHTML = attachments.map((a, i) => `<li><span class="work-mono" title="${esc(a.name)}">${esc(a.name)}</span>
            <span class="work-meta">${esc([a.size != null ? fmtSize(a.size) : '', a.session ? `from ${a.session}` : ''].filter(Boolean).join(' · '))}</span>
            <button type="button" class="w-btn" data-variant="icon" data-size="sm" data-attach-remove="${i}" aria-label="Remove ${esc(a.name)}" title="Remove">✕</button></li>`).join('');
    }
    const readBase64 = file => new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).replace(/^data:[^,]*,/, ''));
        reader.onerror = () => reject(reader.error || new Error(`Could not read ${file.name}`));
        reader.readAsDataURL(file);
    });
    async function addFiles(files) {
        for (const file of files) {
            if (attachments.length >= ATTACH_MAX_FILES) { toast(`A job takes at most ${ATTACH_MAX_FILES} attachments`, 'danger'); break; }
            if (file.size > ATTACH_MAX_BYTES) { toast(`${file.name} is larger than 25 MB`, 'danger'); continue; }
            try { attachments.push({ name: file.name, size: file.size, mime_type: file.type || '', content_base64: await readBase64(file) }); }
            catch (error) { toast(error.message, 'danger'); }
        }
        renderAttachments();
    }
    async function attachFromSession() {
        const session = chosenSession();
        if (!session) return;
        const enc = encodeURIComponent(session.name);
        let files = [];
        try {
            const [att, art] = await Promise.all([api(`/api/sessions/${enc}/attachments?limit=200`), api(`/api/sessions/${enc}/artifacts?limit=200`)]);
            files = [...(att.attachments || []).map(a => ({ id: a.attachment_id, key: 'attachment_id', name: a.name, size: a.size, kind: 'attachment' })),
                     ...(art.artifacts || []).map(a => ({ id: a.artifact_id, key: 'artifact_id', name: a.name, size: a.size, kind: a.kind === 'visualization' ? 'chart' : 'artifact' }))];
        } catch (error) { toast(`Could not list ${session.name}'s files: ${error.message}`, 'danger'); return; }
        const taken = new Set(attachments.filter(a => a.session === session.name).map(a => `${a.key}:${a.id}`));
        const form = document.createElement('form');
        form.className = 'work-stack';
        form.innerHTML = (files.length ? `<ul class="work-attachments">${files.map((f, i) => `<li><label class="w-check work-grow"><input class="w-check__input" type="checkbox" data-pick="${i}" ${taken.has(`${f.key}:${f.id}`) ? 'checked disabled' : ''}><span class="work-mono">${esc(f.name)}</span></label>
                <span class="work-meta">${esc([f.kind, f.size != null ? fmtSize(f.size) : ''].filter(Boolean).join(' · '))}</span></li>`).join('')}</ul>`
            : `<p class="work-meta">${esc(session.name)} has no attachments or artifacts.</p>`)
            + `<div class="w-btn-row" data-align="end"><button type="button" class="w-btn" data-variant="secondary" data-cancel>Cancel</button>${files.length ? '<button type="submit" class="w-btn" data-variant="primary">Attach</button>' : ''}</div>`;
        const d = ui.dialog({ title: `Attach from ${session.name}`, body: form });
        form.querySelector('[data-cancel]').onclick = () => d.close();
        form.onsubmit = e => {
            e.preventDefault();
            form.querySelectorAll('[data-pick]:checked:not(:disabled)').forEach(box => {
                const f = files[Number(box.dataset.pick)];
                if (attachments.length < ATTACH_MAX_FILES) attachments.push({ session: session.name, key: f.key, id: f.id, name: f.name, size: f.size });
            });
            renderAttachments();
            d.close();
        };
    }
    const attachmentPayload = () => attachments.map(a => a.content_base64 != null
        ? { name: a.name, mime_type: a.mime_type, content_base64: a.content_base64 }
        : { session: a.session, [a.key]: a.id });
    function renderWorkspaceChips() {
        const recent = recentWorkspaces(sessions || []);
        document.getElementById('job-workspace-recent').innerHTML = recent.map(w => `<option value="${esc(w)}"></option>`).join('');
        workspaceChips.innerHTML = recent.map(w => `<button type="button" class="w-chip work-mono" data-variant="filter" data-workspace="${esc(w)}" aria-pressed="${sameWorkspace(w, chosenWorkspace())}" title="${esc(w)}">${esc(w.split('/').filter(Boolean).pop() || w)}</button>`).join('');
    }
    function syncSessionHints() {
        const path = chosenWorkspace();
        const chosen = chosenSession();
        renderAttachments();
        workspaceChips.querySelectorAll('[data-workspace]').forEach(b => b.setAttribute('aria-pressed', String(sameWorkspace(b.dataset.workspace, path))));
        if (path && isRepoUrl(path)) {
            repoHint.textContent = `Clones ${path} into a managed checkout; models come from Settings → Stage models.`;
            executionHelp.textContent = '';
            return;
        }
        if (!path) {
            repoHint.textContent = sourceKind === 'repository' ? (repoSelect.value === OTHER_REPO ? 'Paste a git URL (https or ssh); add #branch for a base branch.' : 'Add repositories in Settings → Repositories.')
                : sourceKind === 'session' ? 'Pick a saved session: the job runs in its workspace with its model, mode and environment.'
                : (sessions || []).length ? 'Pick a recent workspace, type the repository path or paste a git URL.' : 'Type the repository path the job works in, or paste a git URL to clone.';
            executionHelp.textContent = '';
            return;
        }
        const branch = sourceKind === 'repository' ? chosenRepository()?.branch : '';
        repoHint.textContent = branch ? `Starts from ${branch} in ${path}.` : chosen
            ? `Runs in ${path} with the model, mode and environment of session ${chosen.name}.`
            : `Runs in ${path}. No saved session uses this workspace: models come from Settings → Stage models (or the current session).`;
        executionHelp.textContent = chosen ? `Blank fields inherit from ${chosen.name}` + (chosen.provider ? ` (${chosen.provider} · ${chosen.model}, ${chosen.agent_mode} mode).` : '.') : '';
    }
    function syncTitleHint() {
        const value = document.getElementById('job-title').value.trim();
        titleHint.textContent = JIRA_KEY.test(value)
            ? `Imports Jira ticket ${value}: its summary, description and acceptance criteria become the job.`
            : 'A Jira key (PROJ-123) imports that ticket; anything else becomes the job title.';
        document.getElementById('work-create-submit').textContent = JIRA_KEY.test(value) ? 'Import ticket' : 'Queue job';
    }
    function renderEnvField() {
        const E = window.MuJobEnvironments;
        if (!envSummaryEl || !E) return;
        const choice = runIn?.value() || { kind: 'default' };
        envResetBtn.hidden = !envOverride && choice.kind === 'default';
        if (choice.kind === 'template') {
            const t = (runIn.templates() || []).find(x => x.name === choice.template);
            envSummaryEl.textContent = `template:${choice.template}`;
            envHintEl.textContent = `Every stage runs in a snapshot of ${choice.template}, taken at start.${(t?.job_setup || []).length ? ` Its job setup (${t.job_setup.length} command${t.job_setup.length === 1 ? '' : 's'}) runs before planning.` : ''}`;
            return;
        }
        if (choice.kind === 'environment') {
            envSummaryEl.textContent = choice.spec;
            envHintEl.textContent = 'Every stage runs in a snapshot of this environment, taken at start.';
            return;
        }
        if (envOverride) {
            envSummaryEl.textContent = envOverride.length ? E.summary(envOverride) : 'shared runtime image';
            envHintEl.textContent = 'Set for this job (overrides repo config, session and settings).';
            return;
        }
        if (!envPreview) { envSummaryEl.textContent = chosenWorkspace() ? 'Resolving…' : 'Choose a workspace to see the build environments.'; return; }
        if (envPreview.error) { envSummaryEl.textContent = 'misconfigured'; envHintEl.textContent = envPreview.error; return; }
        const source = E.SOURCE_LABEL[envPreview.source] || envPreview.source;
        envSummaryEl.textContent = envPreview.bindings?.length ? (envPreview.summary || E.summary(envPreview.bindings)) : 'shared runtime image (mucli-job-runtime)';
        envHintEl.textContent = envPreview.bindings?.length ? `From ${source}. Each job snapshots these at start and removes them when it ends.`
            : 'No environment configured in the repo (.mucli/jobs.toml), the session or Settings → Toolchains.';
    }
    function renderEnvApproval() {
        const host = document.getElementById('job-environments-approval');
        if (!host) return;
        const path = chosenWorkspace();
        const suggestion = envPreview?.suggestion?.approval;
        const pending = (envPreview?.approval_required || []).map(r => ({ repository: path, fingerprint: r.fingerprint, kind: '', ref: r.ref, label: `${r.binding}: ${r.ref}` }));
        const offers = [...(suggestion ? [suggestion] : []), ...pending].filter(o => o && o.fingerprint);
        if (!offers.length || envOverride) { host.hidden = true; host.innerHTML = ''; return; }
        host.hidden = false;
        host.innerHTML = offers.map((o, i) => `<div class="w-card work-env-offer" data-state="attention">
            <header class="w-card__head"><span class="w-chip" data-tone="attention"><span class="w-chip__glyph" aria-hidden="true">◆</span><span>Needs you</span></span><strong class="w-card__title">This repository declares a build environment</strong></header>
            <div class="w-card__body">
            ${envPreview?.suggestion?.message && o === suggestion ? `<p>${esc(envPreview.suggestion.message)}</p>` : ''}
            <dl class="work-env-approval-facts">
                <dt>Kind</dt><dd>${esc(o.kind === 'build' ? 'Dockerfile build' : o.kind === 'image' ? 'image' : (o.kind || 'image'))}</dd>
                ${o.label ? `<dt>What</dt><dd class="work-mono">${esc(o.label)}</dd>` : ''}
                ${o.ref ? `<dt>Image</dt><dd class="work-mono">${esc(o.ref)}</dd>` : ''}
                <dt>Fingerprint</dt><dd class="work-mono">${esc(o.fingerprint)}</dd>
            </dl>
            <div class="work-toolbar"><button type="button" class="w-btn" data-variant="tonal" data-tone="attention" data-size="sm" data-env-approve="${i}">Approve for this repository</button>
                <span class="work-meta">Not approved: jobs on this repository park until you approve it or pick an environment under Advanced.</span></div></div></div>`).join('');
        host.querySelectorAll('[data-env-approve]').forEach(b => { b.onclick = async () => {
            const offer = offers[Number(b.dataset.envApprove)];
            if (!await ui.confirm(`Approve ${offer.label || offer.ref || offer.fingerprint} as the build environment for ${path}?${offer.kind === 'build' ? '\n\nBuilding runs the Dockerfile\'s RUN steps on your container engine with network access.' : ''}`,
                { title: 'Approve build environment', okLabel: 'Approve' })) return;
            b.disabled = true;
            try {
                await api('/api/jobs/environments/approve', { method: 'POST', body: { repository: offer.repository || path, fingerprint: offer.fingerprint, session: chosenSession()?.name || '' } });
                toast('Build environment approved', 'ok');
                await refreshEnvPreview();
            } catch (error) { b.disabled = false; toast(error.message, 'danger'); }
        }; });
    }
    async function refreshEnvPreview() {
        const path = chosenWorkspace();
        envPreview = null; renderEnvField(); renderEnvApproval();
        if (path && isRepoUrl(path)) { void runIn?.refresh({ workspace: path, session: '' }); if (envSummaryEl) envSummaryEl.textContent = 'Resolved from the repository after it is cloned.'; return; }
        if (!path) void runIn?.refresh({ workspace: '', session: '' });
        if (!path || !window.MuJobEnvironments) return;
        const request = ++envRequest;
        try {
            let data;
            if (runIn) {
                await runIn.refresh({ workspace: path, session: chosenSession()?.name || '' });
                data = runIn.info() || {};
            } else {
                data = await window.MuJobEnvironments.sources(api, { repository: path, session: chosenSession()?.name || '', images: false });
            }
            if (request === envRequest) { envPreview = data.resolved || { bindings: [], source: 'default' }; renderEnvField(); renderEnvApproval(); }
        } catch (error) { if (request === envRequest) { envPreview = { error: error.message, bindings: [] }; renderEnvField(); } }
    }
    async function changeEnvironments() {
        if (runIn) { await runIn.advanced(); syncRunIn(); return; }  // "Advanced…": the multi-environment editor
        const E = window.MuJobEnvironments;
        const path = chosenWorkspace();
        const result = await E.editor({ api, ui, title: 'Build environments for the new job',
            initial: envOverride || envPreview?.bindings || [], context: { repository: path, session: chosenSession()?.name || '' },
            clearLabel: 'Use default' });
        if (result === null) return;
        envOverride = result.length ? result : null;
        renderEnvField();
    }

    // Where imported tickets run (Tickets page): workspace or git URL first, like the New job
    // sheet; the session is optional (a saved session on that workspace is matched by the
    // server). "Run again" re-imports tickets whose latest job is done (force).
    // Resolves to { workspace, session, force, defaults } or null when cancelled.
    async function chooseImportTarget({ title = 'Import tickets', okLabel = 'Create jobs', force = false } = {}) {
        const list = await loadSessions(true);
        const entry = list.find(s => s.name === entrySession) || list.find(s => s.current);
        const initial = workspaceInput?.dataset.chosen || entry?.workspace || recentWorkspaces(list)[0] || '';
        return new Promise(resolve => {
            let result = null;
            const form = document.createElement('form');
            form.className = 'work-stack';
            const recent = recentWorkspaces(list);
            form.innerHTML = `<div class="w-field"><label class="w-field__label" for="work-import-workspace">Workspace</label>
                <input class="w-input" data-mono id="work-import-workspace" data-autofocus required list="work-import-workspace-recent" autocomplete="off" spellcheck="false"
                    placeholder="/home/me/src/project — or https://github.com/org/repo.git" value="${esc(initial)}">
                <datalist id="work-import-workspace-recent">${recent.map(w => `<option value="${esc(w)}"></option>`).join('')}</datalist>
                <div class="w-chip-row work-workspace-recent" role="group" aria-label="Recent workspaces">${recent.map(w =>
                    `<button type="button" class="w-chip work-mono" data-variant="filter" data-import-workspace="${esc(w)}" title="${esc(w)}">${esc(w.split('/').filter(Boolean).pop() || w)}</button>`).join('')}</div>
                <p class="w-field__hint" data-import-hint></p></div>
                <div class="w-field" id="work-import-run-in-field"><span class="w-field__label" id="work-import-run-in-label">Run in</span>
                <div class="work-runin-host" data-import-run-in role="group" aria-labelledby="work-import-run-in-label"></div>
                <p class="w-field__hint">Applies to every ticket in this import.</p></div>
                <div class="w-field"><label class="w-field__label" for="work-import-session">Session (optional)</label>
                <span class="w-select"><select class="w-select__control" id="work-import-session"><option value="">Match by workspace</option>${list.map(s =>
                    `<option value="${esc(s.name)}">${esc(s.name)} — ${esc(describeSession(s))}</option>`).join('')}</select></span></div>
                <label class="w-check"><input class="w-check__input" type="checkbox" id="work-import-force" ${force ? 'checked' : ''}>
                    <span>Run again <span class="work-meta">— also start a new job for tickets whose latest job is done</span></span></label>
                <p class="w-field__error work-form-error" data-error></p>
                <div class="w-btn-row" data-align="end"><button type="button" class="w-btn" data-variant="secondary" data-cancel>Cancel</button><button type="submit" class="w-btn" data-variant="primary">${esc(okLabel)}</button></div>`;
            const d = ui.dialog({ title, body: form, onClose: () => resolve(result) });
            const input = form.querySelector('#work-import-workspace'), hint = form.querySelector('[data-import-hint]');
            const sessionSelect = form.querySelector('#work-import-session');
            const importRunIn = window.MuRunInPicker ? window.MuRunInPicker.create(form.querySelector('[data-import-run-in]'),
                { api, ui, toast: (t, k) => toast(t, k), idPrefix: 'work-import-run-in' }) : null;
            let runInTimer = 0;
            const refreshRunIn = () => {
                const path = input.value.trim();
                const s = sessionSelect.value || (path && !isRepoUrl(path) ? (sessionForWorkspace(path)?.name || '') : '');
                void importRunIn?.refresh({ workspace: path, session: s });
            };
            const sync = () => {
                const path = input.value.trim();
                form.querySelectorAll('[data-import-workspace]').forEach(b => b.setAttribute('aria-pressed', String(sameWorkspace(b.dataset.importWorkspace, path))));
                const chosen = sessionSelect.value ? list.find(s => s.name === sessionSelect.value) : (path && !isRepoUrl(path) ? sessionForWorkspace(path) : null);
                hint.textContent = !path ? 'Pick a recent workspace, type the repository path or paste a git URL.'
                    : isRepoUrl(path) ? `Clones ${path} into a managed checkout for the imported jobs.`
                    : chosen ? `Runs in ${path} with the model, mode and environment of session ${chosen.name}.`
                    : `Runs in ${path}. Models come from Settings → Stage models (or the current session).`;
            };
            form.querySelectorAll('[data-import-workspace]').forEach(b => { b.onclick = () => { input.value = b.dataset.importWorkspace; sync(); refreshRunIn(); }; });
            input.addEventListener('input', () => { sync(); clearTimeout(runInTimer); runInTimer = setTimeout(refreshRunIn, 400); });
            sessionSelect.addEventListener('change', () => { sync(); refreshRunIn(); }); sync(); refreshRunIn();
            form.querySelector('[data-cancel]').onclick = () => d.close();
            form.onsubmit = e => {
                e.preventDefault();
                const path = input.value.trim();
                if (!path) { form.querySelector('[data-error]').textContent = 'Choose a workspace or repository for the imported jobs.'; input.focus(); return; }
                const session = sessionSelect.value || (isRepoUrl(path) ? '' : (sessionForWorkspace(path)?.name || ''));
                result = { workspace: path, session, force: form.querySelector('#work-import-force').checked,
                           defaults: isRepoUrl(path) ? { repository: path } : { workspace_root: path } };
                // "Run in" for every imported ticket: defaults.execution.template (+ remember_template).
                const runInPayload = importRunIn ? importRunIn.payload() : {};
                if (Object.keys(runInPayload).length) result.defaults.execution = runInPayload;
                importRunIn?.commit();
                d.close();
            };
        });
    }

    // Ask which session to run in (Jira import and other flows outside the sheet).
    async function chooseSession({ title = 'Choose a session', okLabel = 'Continue' } = {}) {
        const list = await loadSessions(true);
        return new Promise(resolve => {
            let result = null;
            const form = document.createElement('form');
            form.className = 'work-stack';
            form.innerHTML = `<div class="w-field"><label class="w-field__label" for="work-choose-session">Session</label>
                <span class="w-select"><select class="w-select__control" id="work-choose-session" data-autofocus required>${sessionOptions(list, entrySession)}</select></span>
                <p class="w-field__hint" data-session-hint></p><p class="w-field__error work-form-error" data-error></p></div>
                <div class="w-btn-row" data-align="end"><button type="button" class="w-btn" data-variant="secondary" data-cancel>Cancel</button><button type="submit" class="w-btn" data-variant="primary">${esc(okLabel)}</button></div>`;
            const d = ui.dialog({ title, body: form, onClose: () => resolve(result) });
            const select = form.querySelector('select'), hint = form.querySelector('[data-session-hint]');
            const sync = () => { const s = list.find(x => x.name === select.value); hint.textContent = s ? describeSession(s) : 'Jobs run in the chosen session’s workspace.'; };
            select.addEventListener('change', sync); sync();
            form.querySelector('[data-cancel]').onclick = () => d.close();
            form.onsubmit = e => { e.preventDefault(); if (!select.value) { form.querySelector('[data-error]').textContent = 'Choose a session.'; return; } result = select.value; d.close(); };
        });
    }

    // Job cost cap: blank = Settings → Job cost limit, 0 = explicitly unlimited ("no cap").
    function costPlaceholder(value) {
        const cap = Number(value);
        if (value == null || value === '' || !Number.isFinite(cap)) return 'Settings default ($100)';
        return cap > 0 ? `Settings default ($${cap})` : 'Settings default (no cap)';
    }
    function costPayload(raw) {
        const text = String(raw ?? '').trim();
        if (!text) return undefined;
        const cap = Number(text);
        return Number.isFinite(cap) && cap >= 0 ? cap : undefined;
    }
    function syncCostHint() {
        const input = document.getElementById('job-cost'), hint = document.getElementById('job-cost-hint');
        if (!input || !hint) return;
        const cap = costPayload(input.value);
        hint.textContent = cap === 0 ? 'no cap — only the per-stage budgets apply'
            : cap === undefined ? `Blank = ${input.placeholder || 'Settings default'} · 0 = no cap` : `Job stops at $${cap} · 0 = no cap`;
    }

    async function prefillDefaults() {
        // Settings → New job defaults (only fills fields the user has not typed in).
        try {
            const values = (await api('/api/jobs/settings')).rules?.values || {};
            const fill = (id, value) => { const el = document.getElementById(id); if (el && !el.dataset.touched && value != null) el.value = value; };
            fill('job-retries', values['defaults.max_retries']);
            fill('job-cost', values['defaults.max_cost_usd']);
            const costInput = document.getElementById('job-cost');
            if (costInput) costInput.placeholder = costPlaceholder(values['defaults.max_cost_usd']);
            syncCostHint();
            fill('job-runtime', values['defaults.max_runtime_seconds']);
            repositories = (values['jobs.repositories'] || []).filter(r => r && r.name && r.url);
            renderSourcePickers();
            if (sourceKind === 'repository') { syncSessionHints(); void refreshEnvPreview(); }
        } catch (_) { /* older server: keep the form's own defaults */ }
    }

    async function openCreate() {
        createError.textContent = '';
        // The shared dialog stack (dialogs.js): Tab stays inside, Esc sends
        // `dialog-close`, focus returns to the opener. Focus is set below.
        window.MuDialog.open(backdrop, { focus: false });
        const list = await loadSessions(true);
        if (!workspaceInput.dataset.touched) {
            const entry = list.find(s => s.name === entrySession) || list.find(s => s.current);
            workspaceInput.value = workspaceInput.dataset.chosen || entry?.workspace || recentWorkspaces(list)[0] || '';
        }
        renderWorkspaceChips();
        renderSourcePickers();
        syncSessionHints();
        syncTitleHint();
        void refreshEnvPreview();
        void prefillDefaults();
        if (!agentPicker && window.MuJobAgents) agentPicker = window.MuJobAgents.sheet(document.getElementById('job-agents'),
            { api });
        void agentPicker?.refresh();
        const sourceControl = { folder: workspaceInput, repository: repoSelect, session: sessionSelect }[sourceKind];
        (chosenWorkspace() ? document.getElementById('job-title') : sourceControl).focus();
    }

    function closeCreate() {
        window.MuDialog.close(backdrop);
        newBtn.focus();
    }

    function resetCreate(workspace) {
        createForm.reset();
        createForm.querySelectorAll('[data-touched]').forEach(el => { delete el.dataset.touched; });
        envOverride = null;
        attachments = [];
        renderAttachments();
        extraSources = [];
        renderExtraSources();
        runIn?.setDefault();
        agentPicker?.reset();
        workspaceInput.dataset.chosen = workspace;
        syncTitleHint();
    }

    async function submitCreate(event) {
        event.preventDefault();
        createError.textContent = '';
        const submit = document.getElementById('work-create-submit');
        const title = document.getElementById('job-title').value.trim();
        const workspace = chosenWorkspace();
        if (!workspace) {
            createError.textContent = { repository: 'Choose a repository (add them in Settings → Repositories) or paste a URL.', session: 'Choose a session with a workspace.' }[sourceKind] || 'Choose the workspace this job works in.';
            ({ folder: workspaceInput, repository: repoSelect.value === OTHER_REPO ? repoUrlInput : repoSelect, session: sessionSelect })[sourceKind].focus();
            return;
        }
        if (!title) { createError.textContent = 'Enter a title or a ticket key.'; document.getElementById('job-title').focus(); return; }
        submit.disabled = true;
        const mode = document.getElementById('job-mode').value.trim();
        const remote = isRepoUrl(workspace);
        // Folder: the path (or pasted URL) and the session matched by workspace, as before.
        // Repository / session: a `source` the server resolves (mu/jobs/sources.py).
        const session = sourceKind === 'folder' ? chosenSession() : null;
        const source = sourceKind === 'repository'
            ? (repoSelect.value === OTHER_REPO ? { kind: 'repository', url: workspace } : { kind: 'repository', name: repoSelect.value })
            : sourceKind === 'session' ? { kind: 'session', session: sessionSelect.value, include_conversation: conversationBox.checked } : null;
        const common = { ...(source ? { source } : remote ? { repository: workspace } : { workspace_root: workspace }),
                         max_retries: Number(document.getElementById('job-retries').value || 2) };
        if (attachments.length) common.attachments = attachmentPayload();
        const otherSources = extraSourcePayload();
        if (otherSources.length) common.sources = otherSources;
        const cost = costPayload(document.getElementById('job-cost').value);
        const runtime = Number(document.getElementById('job-runtime').value || 0);
        // 0 is sent explicitly (unlimited); blank lets the server apply the Settings default.
        if (cost !== undefined) common.max_cost_usd = cost;
        if (runtime > 0) common.max_runtime_seconds = runtime;
        // Jobs always run unattended inside their sandbox: there is no per-job auto-approve choice.
        const execution = {};
        if (mode) execution.agent_mode = mode;
        const stages = agentPicker?.collectModels() || {};
        if (Object.keys(stages).length) execution.stages = stages;
        if (envOverride && envOverride.length) execution.environments = envOverride.map(({ label, ...binding }) => binding);
        // Run in: execution.template (+ remember_template) or an environment spec (server normalises).
        if (runIn) Object.assign(execution, runIn.payload());
        const stageAgents = agentPicker?.collect();
        if (stageAgents) execution.stage_agents = stageAgents;
        common.execution = execution;
        try {
            let jobId, label;
            if (JIRA_KEY.test(title)) {
                const body = { keys: [title], defaults: { ...common } };
                if (session) body.session_name = session.name;
                const res = await api('/api/jobs/jira/import', { method: 'POST', body });
                const created = (res.created || [])[0], skipped = (res.skipped || [])[0], failed = (res.errors || [])[0];
                if (failed && !created) throw new Error(`${failed.key}: ${failed.error}`);
                jobId = created?.job_id || skipped?.job_id;
                label = created ? `Imported ${title}: ${created.title || ''}` : skipped ? `${title} already has a job` : `Nothing imported for ${title}`;
            } else {
                const payload = {
                    ...common, title,
                    description: document.getElementById('job-description').value.trim(),
                    acceptance_criteria: lines(document.getElementById('job-acceptance').value),
                    validation_commands: lines(document.getElementById('job-validation').value),
                };
                if (session) payload.session_name = session.name;
                const response = await api('/api/jobs', { method: 'POST', body: payload });
                jobId = response.job.id; label = `Queued: ${response.job.title}`;
            }
            runIn?.commit();
            resetCreate(sourceKind === 'folder' ? workspace : workspaceInput.dataset.chosen || '');
            closeCreate();
            toast(label, 'ok');
            await refreshBoard({ quiet: true });
            if (jobId) selectJob(jobId);
        } catch (error) {
            createError.textContent = error.message;
        } finally {
            submit.disabled = false;
        }
    }

    let workspaceTimer = 0;
    workspaceInput.addEventListener('input', () => {
        workspaceInput.dataset.touched = '1';
        syncSessionHints();
        clearTimeout(workspaceTimer); workspaceTimer = setTimeout(() => void refreshEnvPreview(), 400);
    });
    workspaceChips.addEventListener('click', e => {
        const b = e.target.closest('[data-workspace]');
        if (!b) return;
        workspaceInput.value = b.dataset.workspace; workspaceInput.dataset.touched = '1';
        syncSessionHints(); void refreshEnvPreview();
    });
    sourceKindEl.addEventListener('click', e => { const b = e.target.closest('[data-source-kind]'); if (b) setSourceKind(b.dataset.sourceKind); });
    sourceKindEl.addEventListener('keydown', e => {
        // Radio group: arrow keys move the choice.
        const kinds = ['folder', 'repository', 'session'], step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
        if (!step) return;
        e.preventDefault();
        const next = kinds[(kinds.indexOf(sourceKind) + step + kinds.length) % kinds.length];
        setSourceKind(next);
        sourceKindEl.querySelector(`[data-source-kind="${next}"]`).focus();
    });
    repoSelect.addEventListener('change', () => { repoUrlInput.hidden = repoSelect.value !== OTHER_REPO; if (!repoUrlInput.hidden) repoUrlInput.focus(); syncSessionHints(); void refreshEnvPreview(); });
    repoUrlInput.addEventListener('input', () => { syncSessionHints(); clearTimeout(workspaceTimer); workspaceTimer = setTimeout(() => void refreshEnvPreview(), 400); });
    sessionSelect.addEventListener('change', () => { syncSessionHints(); void refreshEnvPreview(); });
    document.getElementById('job-source-add').addEventListener('click', () => {
        if (extraSources.length >= 8) { toast('A job takes at most 8 other sources', 'danger'); return; }
        extraSources.push({ kind: 'folder', value: '' });
        renderExtraSources();
        sourcesList.querySelector(`[data-src-value="${extraSources.length - 1}"]`)?.focus();
    });
    sourcesList.addEventListener('change', e => {
        const k = e.target.closest('[data-src-kind]');
        if (k) { extraSources[Number(k.dataset.srcKind)] = { kind: k.value, value: k.value === 'session' ? ((sessions || []).find(x => x.workspace)?.name || '') : '' }; renderExtraSources(); }
        const v = e.target.closest('select[data-src-value]');
        if (v) extraSources[Number(v.dataset.srcValue)].value = v.value;
    });
    sourcesList.addEventListener('input', e => {
        const v = e.target.closest('input[data-src-value]');
        if (v) extraSources[Number(v.dataset.srcValue)].value = v.value;
    });
    sourcesList.addEventListener('click', e => {
        const b = e.target.closest('[data-src-remove]');
        if (b) { extraSources.splice(Number(b.dataset.srcRemove), 1); renderExtraSources(); }
    });
    document.getElementById('job-attach-files').addEventListener('click', () => attachInput.click());
    attachInput.addEventListener('change', () => { void addFiles([...attachInput.files]); attachInput.value = ''; });
    attachSessionBtn.addEventListener('click', () => void attachFromSession());
    attachList.addEventListener('click', e => { const b = e.target.closest('[data-attach-remove]'); if (b) { attachments.splice(Number(b.dataset.attachRemove), 1); renderAttachments(); } });
    document.getElementById('job-title').addEventListener('input', syncTitleHint);
    document.getElementById('job-environments-change')?.addEventListener('click', () => void changeEnvironments());
    envResetBtn?.addEventListener('click', () => { envOverride = null; if (runIn) runIn.setDefault(); else renderEnvField(); });
    ['job-retries', 'job-cost', 'job-runtime'].forEach(id => document.getElementById(id)?.addEventListener('input', e => { e.target.dataset.touched = '1'; }));
    document.getElementById('job-cost')?.addEventListener('input', syncCostHint);
    syncSessionHints();

    window.MuWork = Object.assign(window.MuWork || {}, { selectJob, refreshBoard, go, route: () => route, api, toast, chooseSession, chooseImportTarget, loadSessions });
    refreshBtn.addEventListener('click', () => refreshBoard({ refreshSelected: true }).then(() => toast('Refreshed', 'ok')).catch(e => toast(e.message, 'danger')));
    newBtn.addEventListener('click', openCreate);
    document.getElementById('work-create-close').addEventListener('click', closeCreate);
    document.getElementById('work-create-cancel').addEventListener('click', closeCreate);
    backdrop.addEventListener('click', e => { if (e.target === backdrop) closeCreate(); });
    createForm.addEventListener('submit', submitCreate);
    // The New job modal (.w-modal) is on the shared dialog stack: Esc closes it.
    backdrop.addEventListener('dialog-close', closeCreate);
    document.getElementById('work-palette-open').addEventListener('click', ui.palette);
    document.getElementById('work-help-open').addEventListener('click', ui.help);
    document.getElementById('work-density').addEventListener('click', toggleDensity);
    document.getElementById('work-notify').addEventListener('click', () => void toggleNotify());
    root.querySelector('[data-route-theme]')?.addEventListener('click', () => setTimeout(registerGlobal, 0));
    bindMenus();

    applyPrefs();
    ui.installKeys();
    view.start({ autoSelect: false }).then(applyRoute).catch(e => toast(e.message, 'danger'));
})();
