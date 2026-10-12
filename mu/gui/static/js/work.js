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
    const lines = value => String(value || '').split(/\r?\n/).map(v => v.trim()).filter(Boolean);
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

    const view = window.MuJobWork.create({api, toast, boardEl, detailEl, totalEl});
    const selectJob = id => view.select(id);
    async function refreshBoard({refreshSelected = false} = {}) {
        await view.refreshHome();
        if (refreshSelected) await view.refreshParts();
    }
    function openCreate() {
        createError.textContent = '';
        backdrop.hidden = false;
        document.getElementById('job-title').focus();
    }

    function closeCreate() {
        backdrop.hidden = true;
    }

    const STAGE_NAMES = ['plan', 'implement', 'review'];

    // Only stages with at least one filled field are sent; blank stages
    // inherit the job model, then the session model, on the server.
    function collectStageModels() {
        const stages = {};
        STAGE_NAMES.forEach((stage) => {
            const entry = {};
            ['provider', 'model'].forEach((field) => {
                const input = document.getElementById(`job-stage-${stage}-${field}`);
                const value = input ? input.value.trim() : '';
                if (value) entry[field] = value;
            });
            if (Object.keys(entry).length) stages[stage] = entry;
        });
        return stages;
    }

    async function submitCreate(event) {
        event.preventDefault();
        createError.textContent = '';
        const submit = document.getElementById('work-create-submit');
        submit.disabled = true;
        const title = document.getElementById('job-title').value.trim();
        const provider = document.getElementById('job-provider').value.trim();
        const model = document.getElementById('job-model').value.trim();
        const mode = document.getElementById('job-mode').value.trim();
        const autoApprove = document.getElementById('job-auto-approve').checked;
        const payload = {
            title,
            description: document.getElementById('job-description').value.trim(),
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
        const stages = collectStageModels();
        if (Object.keys(stages).length) execution.stages = stages;
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
        repoHint.textContent = `Jobs run in the workspace attached to ${sessionName}.`;
        executionHelp.textContent = `Leave fields blank to inherit provider, model, mode and write-approval policy from ${sessionName}.`;
    } else {
        sessionEl.textContent = 'No session context';
        repoHint.textContent = 'Load a session with an attached workspace to create jobs.';
        executionHelp.textContent = 'Provider and model are required when this page was not opened from a loaded session.';
    }

    window.MuWork = Object.assign(window.MuWork || {}, {selectJob, refreshBoard});
    refreshBtn.addEventListener('click', () => refreshBoard({refreshSelected:true}).catch(e=>toast(e.message,true)));
    newBtn.addEventListener('click', openCreate);
    document.getElementById('work-create-close').addEventListener('click', closeCreate);
    document.getElementById('work-create-cancel').addEventListener('click', closeCreate);
    backdrop.addEventListener('click', e => { if (e.target === backdrop) closeCreate(); });
    createForm.addEventListener('submit', submitCreate);
    window.addEventListener('keydown', e => { if (e.key === 'Escape' && !backdrop.hidden) closeCreate(); });
    view.start().catch(e => toast(e.message, true));
})();
