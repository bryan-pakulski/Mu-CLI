/* Jobs mode shell (planos Shell): hash routes (#/ dashboard, #/jobs, #/job/<id>?tab=…,
 * #/activity, #/boards (Tickets), #/history, #/settings, #/setup with its Doctor tab at #/doctor), breadcrumbs, status chips (GET /api/jobs/vm/status),
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
    const refreshBtn = document.getElementById('work-refresh');
    const newBtn = document.getElementById('work-new');
    const backdrop = document.getElementById('work-create-backdrop');
    const createForm = document.getElementById('work-create-form');
    const createError = document.getElementById('work-create-error');
    const sessionEl = document.getElementById('work-session-context');
    const repoHint = document.getElementById('job-repository-hint');
    const executionHelp = document.getElementById('work-execution-help');
    const crumbsEl = document.getElementById('work-crumbs');
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

    /* ------------------------------------------------------------ status chips (planos StatusChips) */
    let knownNeeds = null;
    // Top-bar status chips are Wash status chips: tone via data-tone, glyph + label always.
    const CHIP_TONE = { muted: 'neutral', warn: 'attention' };
    function chip(name, tone, text, active) {
        const el = root.querySelector(`[data-chip="${name}"]`);
        if (!el) return;
        el.dataset.tone = CHIP_TONE[tone] || tone;
        el.querySelector('[data-chip-text]').textContent = text;
        el.toggleAttribute('data-active', !!active);
    }
    const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
    function renderStatus(st) {
        chip('agents', st.agents ? 'running' : 'muted', plural(st.agents, 'agent'), st.agents > 0);
        chip('checks', st.checks ? 'running' : 'muted', plural(st.checks, 'check'), st.checks > 0);
        chip('needs', st.needs_you ? 'warn' : 'muted', `${st.needs_you} need${st.needs_you === 1 ? 's' : ''} you`, false);
        const slot = root.querySelector('[data-chip-slot="engine"]');
        const engine = String(st.engine || '');
        slot.hidden = !engine || engine === 'off';
        if (!slot.hidden) {
            chip('engine', engine === 'up' ? 'ok' : engine === 'down' ? 'danger' : 'muted', `docker ${engine}`, false);
            root.querySelector('[data-chip="engine"]').title = `Container engine: docker ${engine}`;
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
        if (route.name === 'job') setCrumbs();
    }
    function onConnection(live) {
        const el = root.querySelector('[data-chip="live"]');
        el.dataset.tone = live ? 'ok' : 'caution';
        el.title = live ? 'Live updates connected' : 'Reconnecting to mucli…';
        el.querySelector('[data-chip-glyph]').textContent = live ? '●' : '○';
        el.querySelector('[data-chip-text]').textContent = live ? 'live' : 'offline';
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
    function setCrumbs() {
        const trail = route.name === 'job'
            ? [{ label: 'Jobs', to: '#/jobs' }, { label: (() => { const j = view.job(); return j?.id === route.id ? (j.key || `#${j.id.slice(0, 8)}`) : `#${route.id.slice(0, 8)}`; })() }]
            : route.name === 'jobs' ? [{ label: 'Jobs' }]
            : route.name === 'setup' && route.params?.get('tab') === 'doctor' ? [{ label: 'Setup', to: '#/setup' }, { label: 'Doctor' }]
            : PAGES[route.name] ? [{ label: PAGES[route.name] }] : [{ label: 'Dashboard' }];
        // The shared top-bar breadcrumb (μcli › Work › …): Work links back to the dashboard.
        crumbsEl.innerHTML = [{ label: 'Work', to: '#/' }, ...trail].map((c, i, all) => `<li class="w-breadcrumb__item">${c.to && i < all.length - 1 ? `<a href="${c.to}">${esc(c.label)}</a>` : `<span aria-current="${i === all.length - 1 ? 'page' : 'false'}">${esc(c.label)}</span>`}</li>`).join('');
        document.title = `${trail[trail.length - 1].label} · Work · μcli`;
    }
    async function applyRoute() {
        const next = parseHash();
        if (!next) return;
        route = next;
        root.querySelectorAll('[data-route]').forEach(s => { s.hidden = s.dataset.route !== route.name; });
        root.querySelectorAll('[data-nav]').forEach(a => {
            const on = a.dataset.nav === route.name || (route.name === 'job' && a.dataset.nav === 'jobs');
            if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
        });
        if (route.name !== 'job') view.leave();
        setCrumbs();
        (document.getElementById('w-main') || document.getElementById('work-main')).scrollTop = 0;
        window.dispatchEvent(new CustomEvent('mu:work-route', { detail: { name: route.name, params: route.params || new URLSearchParams() } }));
        if (route.name === 'job') {
            try { await view.select(route.id, { tab: route.tab }); } catch (e) {
                const missing = /not found|404/i.test(e.message);
                detailEl.innerHTML = `<div class="w-empty" data-variant="error" role="alert"><div class="w-empty__art" aria-hidden="true"><svg class="w-icon" focusable="false"><use href="/static/icons/wash.svg#i-alert-circle"></use></svg></div>
                    <h3 class="w-empty__title">${missing ? 'Job not found' : 'Couldn’t open this job'}</h3><p class="w-empty__body">${esc(missing ? `Job ${route.id} not found.` : e.message)}</p>
                    <div class="w-empty__actions"><a class="w-btn" data-variant="secondary" href="#/jobs">All jobs</a></div></div>`;
            }
            setCrumbs();
        }
    }
    window.addEventListener('hashchange', applyRoute);

    const view = window.MuJobWork.create({
        api, toast, boardEl, detailEl, totalEl, jobsEl, onHome, onConnection,
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
    const chosenWorkspace = () => workspaceInput.value.trim();
    // A git URL (https/ssh/scp-style) in the workspace field is sent as `repository` and cloned by the server.
    const GIT_URL = /^(?:(?:https?|ssh|git):\/\/[^\s/]+\/\S+|[\w.-]+@[\w.-]+:\S+)$/;
    const isRepoUrl = value => GIT_URL.test(String(value || '').trim());
    function renderWorkspaceChips() {
        const recent = recentWorkspaces(sessions || []);
        document.getElementById('job-workspace-recent').innerHTML = recent.map(w => `<option value="${esc(w)}"></option>`).join('');
        workspaceChips.innerHTML = recent.map(w => `<button type="button" class="w-chip work-mono" data-variant="filter" data-workspace="${esc(w)}" aria-pressed="${sameWorkspace(w, chosenWorkspace())}" title="${esc(w)}">${esc(w.split('/').filter(Boolean).pop() || w)}</button>`).join('');
    }
    function syncSessionHints() {
        const path = chosenWorkspace();
        const chosen = path ? sessionForWorkspace(path) : null;
        workspaceChips.querySelectorAll('[data-workspace]').forEach(b => b.setAttribute('aria-pressed', String(sameWorkspace(b.dataset.workspace, path))));
        if (path && isRepoUrl(path)) {
            repoHint.textContent = `Clones ${path} into a managed checkout; models come from Settings → Stage models.`;
            executionHelp.textContent = '';
            return;
        }
        if (!path) {
            repoHint.textContent = (sessions || []).length ? 'Pick a recent workspace, type the repository path or paste a git URL.' : 'Type the repository path the job works in, or paste a git URL to clone.';
            executionHelp.textContent = '';
            return;
        }
        repoHint.textContent = chosen
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
                await api('/api/jobs/environments/approve', { method: 'POST', body: { repository: offer.repository || path, fingerprint: offer.fingerprint, session: sessionForWorkspace(path)?.name || '' } });
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
                await runIn.refresh({ workspace: path, session: sessionForWorkspace(path)?.name || '' });
                data = runIn.info() || {};
            } else {
                data = await window.MuJobEnvironments.sources(api, { repository: path, session: sessionForWorkspace(path)?.name || '', images: false });
            }
            if (request === envRequest) { envPreview = data.resolved || { bindings: [], source: 'default' }; renderEnvField(); renderEnvApproval(); }
        } catch (error) { if (request === envRequest) { envPreview = { error: error.message, bindings: [] }; renderEnvField(); } }
    }
    async function changeEnvironments() {
        if (runIn) { await runIn.advanced(); syncRunIn(); return; }  // "Advanced…": the multi-environment editor
        const E = window.MuJobEnvironments;
        const path = chosenWorkspace();
        const result = await E.editor({ api, ui, title: 'Build environments for the new job',
            initial: envOverride || envPreview?.bindings || [], context: { repository: path, session: sessionForWorkspace(path)?.name || '' },
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
            // "Run unattended in sandbox" starts from the Settings default (off unless turned on there).
            const unattended = document.getElementById('job-auto-approve');
            if (!unattended.dataset.touched) { unattended.checked = values['defaults.run_unattended'] === true; unattended.dispatchEvent(new Event('change')); }
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
        syncSessionHints();
        syncTitleHint();
        void refreshEnvPreview();
        void prefillDefaults();
        if (!agentPicker && window.MuJobAgents) agentPicker = window.MuJobAgents.sheet(document.getElementById('job-agents'),
            { api, autoApprove: () => document.getElementById('job-auto-approve') });
        void agentPicker?.refresh();
        (chosenWorkspace() ? document.getElementById('job-title') : workspaceInput).focus();
    }

    function closeCreate() {
        window.MuDialog.close(backdrop);
        newBtn.focus();
    }

    function resetCreate(workspace) {
        createForm.reset();
        createForm.querySelectorAll('[data-touched]').forEach(el => { delete el.dataset.touched; });
        envOverride = null;
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
        if (!workspace) { createError.textContent = 'Choose the workspace this job works in.'; workspaceInput.focus(); return; }
        if (!title) { createError.textContent = 'Enter a title or a ticket key.'; document.getElementById('job-title').focus(); return; }
        submit.disabled = true;
        const mode = document.getElementById('job-mode').value.trim();
        const remote = isRepoUrl(workspace);
        const session = remote ? null : sessionForWorkspace(workspace);
        const common = { ...(remote ? { repository: workspace } : { workspace_root: workspace }), max_retries: Number(document.getElementById('job-retries').value || 2) };
        const cost = costPayload(document.getElementById('job-cost').value);
        const runtime = Number(document.getElementById('job-runtime').value || 0);
        // 0 is sent explicitly (unlimited); blank lets the server apply the Settings default.
        if (cost !== undefined) common.max_cost_usd = cost;
        if (runtime > 0) common.max_runtime_seconds = runtime;
        // Unattended sandbox writes are always sent explicitly: the job never inherits session YOLO.
        const execution = { auto_approve_writes: document.getElementById('job-auto-approve').checked };
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
            resetCreate(workspace);
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

    sessionEl.textContent = 'all sessions';
    sessionEl.title = 'Jobs from every session. New jobs ask which workspace to work in' + (entrySession ? ` (default: ${entrySession}’s).` : '.');
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
    document.getElementById('job-title').addEventListener('input', syncTitleHint);
    document.getElementById('job-auto-approve').addEventListener('input', e => { e.target.dataset.touched = '1'; });
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

    applyPrefs();
    ui.installKeys();
    view.start({ autoSelect: false }).then(applyRoute).catch(e => toast(e.message, 'danger'));
})();
