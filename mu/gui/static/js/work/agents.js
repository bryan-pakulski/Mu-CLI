/* Agent harnesses (mucli / Claude Code / Codex) for jobs mode: the per-stage "Agent" picker on
 * the New job sheet, the Agents status card in Settings (GET /api/jobs/agents, secrets via
 * PUT /api/jobs/agents/secret — values are never shown) and the job page's per-stage agent text. */
(() => {
    'use strict';
    const STAGES = ['plan', 'implement', 'review'];
    const STAGE_LABEL = { plan: 'Plan', implement: 'Implement', review: 'Review' };
    const AGENTS = [['mucli', 'mucli'], ['claude', 'Claude Code'], ['codex', 'Codex']];
    const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const label = agent => (AGENTS.find(([id]) => id === agent) || [agent, agent])[1];
    let status = null;

    async function load(api, force = false) {
        if (!status || force) status = await api('/api/jobs/agents');
        return status;
    }

    function modelHint(data, stage, agent, stageDefaults) {
        if (agent === 'mucli') {
            const d = stageDefaults?.[stage];
            return d?.model ? `default: ${d.provider ? `${d.provider} · ` : ''}${d.model}` : 'session model';
        }
        const info = data?.[agent] || {};
        return info.models?.[stage] || info.default_models?.[stage] || `${label(agent)} default model`;
    }

    /* ------------------------------------------------------------ New job sheet */
    // The one per-stage override picker: agent (mucli / Claude Code / Codex) and its model. For
    // mucli the model is a provider + model (execution.stages); for an external agent it is that
    // agent's model (execution.stage_agents). Blank = Settings → Stage models, then the session.
    // host: element to render into. Returns { collect(), collectModels(), refresh(), reset() }.
    function sheet(host, { api, autoApprove }) {
        if (!host) return null;
        let data = null, touched = false, providers = [], stageDefaults = {};
        const modelLists = {};
        host.innerHTML = `<div class="work-agent-grid" role="group" aria-labelledby="job-agents-label">${STAGES.map(stage => `
            <label class="work-stage-name" for="job-agent-${stage}">${STAGE_LABEL[stage]}</label>
            <select id="job-agent-${stage}" data-agent-stage="${stage}" aria-describedby="job-agent-${stage}-hint">
                ${AGENTS.map(([id, text]) => `<option value="${id}">${esc(text)}</option>`).join('')}</select>
            <select id="job-stage-${stage}-provider" data-stage-provider="${stage}" aria-label="${STAGE_LABEL[stage]} stage provider"><option value="">provider</option></select>
            <input id="job-agent-${stage}-model" data-agent-model="${stage}" list="job-agent-${stage}-models" placeholder="model" aria-label="${STAGE_LABEL[stage]} stage model">
            <datalist id="job-agent-${stage}-models"></datalist>
            <small class="work-agent-hint work-tone" id="job-agent-${stage}-hint"></small>`).join('')}</div>
            <p class="work-caption" data-tone="attention" data-codex-warning hidden></p>`;
        const select = stage => host.querySelector(`[data-agent-stage="${stage}"]`);
        const provider = stage => host.querySelector(`[data-stage-provider="${stage}"]`);
        const model = stage => host.querySelector(`[data-agent-model="${stage}"]`);
        const warning = host.querySelector('[data-codex-warning]');

        async function fillModels(stage) {
            const list = host.querySelector(`#job-agent-${stage}-models`);
            const name = select(stage).value === 'mucli' ? provider(stage).value : '';
            if (!name) { list.innerHTML = ''; return; }
            modelLists[name] = modelLists[name] || api(`/api/providers/${encodeURIComponent(name)}/models`)
                .then(d => (d.models || []).map(m => typeof m === 'string' ? m : String(m?.name || m?.id || m?.model || '')).filter(Boolean))
                .catch(() => []);
            const models = await modelLists[name];
            list.innerHTML = models.map(m => `<option value="${esc(m)}"></option>`).join('');
        }

        function sync() {
            STAGES.forEach(stage => {
                const agent = select(stage).value, info = data?.[agent] || {};
                const mucli = agent === 'mucli';
                provider(stage).hidden = !mucli;
                provider(stage).disabled = !mucli;
                model(stage).placeholder = modelHint(data, stage, agent, stageDefaults);
                const hint = host.querySelector(`#job-agent-${stage}-hint`);
                hint.textContent = !mucli && info.ready === false ? `✗ ${info.problems?.[0] || `${label(agent)} is not ready`}` : '';
                if (hint.textContent) hint.dataset.tone = 'danger'; else delete hint.dataset.tone;
                [...select(stage).options].forEach(o => {
                    const ready = o.value === 'mucli' || data?.[o.value]?.ready !== false;
                    o.textContent = label(o.value) + (ready ? '' : ' (not set up)');
                });
            });
            const codexGated = select('implement').value === 'codex' && !autoApprove()?.checked;
            warning.hidden = !codexGated;
            warning.textContent = codexGated ? `⚠ ${data?.codex?.warnings?.find(w => w.includes('approval review')) ||
                'Codex implement without unattended mode sends every command through Codex’s automatic approval review, which may refuse to run tests.'}` : '';
        }
        host.addEventListener('change', e => {
            const stage = e.target.dataset.agentStage || e.target.dataset.stageProvider;
            if (e.target.matches('[data-agent-stage]')) { touched = true; model(stage).value = ''; }
            if (stage) void fillModels(stage);
            sync();
        });
        host.addEventListener('input', e => { if (e.target.matches('[data-agent-stage]') && e.target.value) touched = true; });
        autoApprove()?.addEventListener('change', sync);

        async function refresh() {
            const [agentsData, providerData, settings] = await Promise.all([
                load(api, true).catch(() => null),
                providers.length ? Promise.resolve({ providers }) : api('/api/providers').catch(() => ({ providers: [] })),
                api('/api/jobs/settings').catch(() => ({})),
            ]);
            data = agentsData;
            providers = providerData.providers || [];
            stageDefaults = settings.stage_models || {};
            STAGES.forEach(stage => {
                const p = provider(stage), keep = p.value;
                p.innerHTML = '<option value="">provider</option>' + providers.map(x => {
                    const name = x.name || x;
                    return `<option value="${esc(name)}">${esc(name + (x.configured === false ? ' (not configured)' : ''))}</option>`;
                }).join('');
                p.value = keep;
                if (!touched) { select(stage).value = data?.defaults?.[stage] || 'mucli'; model(stage).value = ''; p.value = ''; }
            });
            sync();
        }
        // stage_agents for execution: only when an external agent is chosen or the user changed it.
        function collect() {
            const out = {};
            STAGES.forEach(stage => {
                const agent = select(stage).value, m = model(stage).value.trim();
                out[stage] = { agent, ...(agent !== 'mucli' && m ? { model: m } : {}) };
            });
            const external = STAGES.some(s => out[s].agent !== 'mucli');
            return touched || external ? out : null;
        }
        // execution.stages for mucli stages with a model override; blank stages are not sent.
        function collectModels() {
            const stages = {};
            STAGES.forEach(stage => {
                if (select(stage).value !== 'mucli') return;
                const m = model(stage).value.trim(), p = provider(stage).value.trim();
                if (m) stages[stage] = { ...(p ? { provider: p } : {}), model: m };
            });
            return stages;
        }
        function reset() { touched = false; STAGES.forEach(stage => { provider(stage).value = ''; }); void refresh(); }
        return { refresh, collect, collectModels, reset };
    }

    /* ------------------------------------------------------------ Settings card */
    async function card(target, { api, toast }) {
        if (!target) return;
        let data;
        try { data = await load(api, true); } catch (error) { target.innerHTML = `<div class="w-banner work-banner-sm" data-tone="danger" role="alert"><span class="w-banner__glyph" aria-hidden="true">✗</span><p class="w-banner__msg">${esc(error.message)}</p></div>`; return; }
        const secretSource = data.secrets?.CLAUDE_CODE_OAUTH_TOKEN || '';
        target.innerHTML = `<div class="work-stack-tight work-agent-statuses">${['claude', 'codex'].map(agent => {
            const h = data[agent] || {};
            return `<div class="work-stack-tight work-agent-status" data-agent-status="${agent}">
                <div class="work-toolbar work-caption"><span class="w-chip"${h.ready ? ' data-tone="ok"' : ''}><span class="w-chip__glyph" aria-hidden="true">${h.ready ? '✓' : '○'}</span><span>${esc(h.label || label(agent))}: ${h.ready ? 'ready' : 'not ready'}</span></span>
                    ${h.binary ? `<span class="work-mono work-ellipsis" title="${esc(h.binary)}">${esc(h.version || h.binary)}</span>` : ''}
                    ${h.credential ? `<span class="work-muted">credential: ${esc(h.credential.replaceAll('_', ' '))}</span>` : ''}</div>
                ${(h.problems || []).map(p => `<div class="work-caption" data-tone="danger">✗ ${esc(p)}</div>`).join('')}
                ${(h.warnings || []).map(w => `<div class="work-caption" data-tone="attention">⚠ ${esc(w)}</div>`).join('')}
                ${h.ready ? '' : `<details class="work-caption"><summary>Set up ${esc(h.label || label(agent))}</summary><ol>${(data.setup?.[agent] || []).map(s => `<li>${esc(s)}</li>`).join('')}</ol></details>`}</div>`;
        }).join('')}
            <div class="work-toolbar work-caption" data-agent-secret role="group" aria-label="Claude setup token">
                <label for="agent-oauth-token">Claude setup token <span class="work-muted">(${secretSource ? `set in ${esc(secretSource)}` : 'not set'})</span></label>
                <input id="agent-oauth-token" type="password" autocomplete="off" placeholder="paste the output of claude setup-token" class="w-input work-grow">
                <button type="button" class="w-btn" data-variant="secondary" data-size="sm" data-secret-save>Save token</button>
                ${secretSource === 'secrets.env' ? '<button type="button" class="w-btn" data-variant="ghost" data-size="sm" data-tone="danger" data-secret-clear>Clear</button>' : ''}
            </div></div>`;
        // Rendered inside the settings section's <form>: a nested form would be dropped, so this
        // is a plain group and Enter must not submit the section.
        const form = target.querySelector('[data-agent-secret]');
        const put = async value => {
            try {
                status = await api('/api/jobs/agents/secret', { method: 'PUT', body: { key: 'CLAUDE_CODE_OAUTH_TOKEN', value } });
                toast?.(value ? 'Claude setup token saved' : 'Claude setup token cleared', 'ok');
            } catch (error) { toast?.(error.message, 'danger'); }
            void card(target, { api, toast });
        };
        const save = () => {
            const input = form.querySelector('input');
            const value = input.value.trim();
            input.value = '';
            if (value) void put(value);
        };
        form.querySelector('[data-secret-save]').addEventListener('click', save);
        form.querySelector('input').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); save(); } });
        form.querySelector('[data-secret-clear]')?.addEventListener('click', () => void put(''));
    }

    /* ------------------------------------------------------------ job page */
    // "plan Claude Code claude-opus-5-5 · implement mucli gpt-5.5 …" (HTML), from the job VM.
    function stageText(job) {
        const agents = job.stage_agents || {}, models = job.stage_models || {};
        const parts = STAGES.map(stage => {
            const a = agents[stage] || { agent: 'mucli' };
            const m = a.agent === 'mucli' ? models[stage]?.model : (a.model || 'default model');
            if (a.agent === 'mucli' && !m) return '';
            return `${esc(stage)} ${a.agent === 'mucli' ? '' : `<span class="w-chip" data-tone="accent" data-stage-agent="${esc(a.agent)}">${esc(a.label || label(a.agent))}</span> `}<code>${esc(m)}</code>`;
        }).filter(Boolean);
        return parts.length ? parts.join(' · ') : 'unset';
    }

    window.MuJobAgents = { load, sheet, card, stageText, label, STAGES };
})();
