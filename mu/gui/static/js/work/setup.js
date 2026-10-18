/* Jobs Setup and Doctor (planos-style) for jobs mode.
 * Setup (#/setup): a live checklist (GET /api/jobs/setup); each step re-checks the machine and offers
 * the actions that fix it — run, prompts (inline form; secrets never prefilled or shown), job (live
 * log polled from the setup VM), terminal (a command to copy, then "check again") and nav.
 * Doctor (a tab of Setup; #/doctor still opens it): pass/warn/fail report (GET /api/jobs/doctor,
 * deep = sandbox self-test). Also drives the Setup nav badge and the ONE dismissible dashboard
 * call-to-action (setup steps to do and/or stage models without a default). */
(() => {
    'use strict';
    const work = () => window.MuWork || {};
    const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const STEP_ICON = { ok: '✓', todo: '○', warn: '!', skipped: '–', optional: '·', '': '…' };
    // Wash tones (G§9.13); '' = the neutral ink-3 status.
    const STEP_TONE = { ok: 'ok', todo: 'attention', warn: 'attention', skipped: '', optional: '', '': '' };
    const DOC_ICON = { ok: '✓', warn: '!', fail: '✗' };
    const DOC_TONE = { ok: 'ok', warn: 'attention', fail: 'danger' };
    const toneAttr = tone => (tone ? ` data-tone="${tone}"` : '');
    // Status glyph (✓ ○ ! ✗ ●) in its tone's ink; data-state="active" pulses while checking.
    const glyph = (tone, icon, active = false, attrs = 'aria-hidden="true"') =>
        `<span class="w-status work-status-glyph"${toneAttr(tone)}${active ? ' data-state="active"' : ''} ${attrs}>${icon}</span>`;
    const DISMISS_KEY = 'mucli-jobs-setup-cta';

    let vm = null, selected = '', openForm = '', poll = null, busy = false, doctor = null, doctorBusy = false;
    let missingModels = [];  // stages without a default model (from work_stage_models.js)
    const $ = id => document.getElementById(id);

    async function api(path, options) { return work().api(path, options); }
    const toast = (m, tone) => work().toast?.(m, tone);

    /* ------------------------------------------------------------ setup data */
    async function load({ recheck = false } = {}) {
        vm = await api(`/api/jobs/setup${recheck ? '?recheck=1' : ''}`);
        reminders();
        return vm;
    }

    function current() {
        if (!vm) return null;
        return vm.steps.find(s => s.id === selected)
            || vm.steps.find(s => !s.checking && (s.state === 'todo' || s.state === 'warn'))
            || vm.steps[0];
    }

    /* ------------------------------------------------------------ setup render */
    function renderSetup() {
        const host = $('work-setup');
        if (!host || !vm) return;
        const step = current();
        selected = step.id;
        $('work-setup-summary').textContent = `${vm.done}/${vm.total} done · each step checks your machine live; re-run any time`
            + (vm.completed_at ? ` · finished ${new Date(vm.completed_at * 1000).toLocaleDateString()}` : '');
        $('work-setup-bar').style.width = `${Math.round((vm.done / Math.max(1, vm.total)) * 100)}%`;
        const finish = $('work-setup-finish');
        finish.disabled = !vm.can_finish;
        finish.title = vm.can_finish ? 'Mark setup as complete' : (vm.blocking.length ? `To do: ${vm.blocking.join(', ')}` : 'checking…');
        const list = vm.steps.map(s => {
            const state = s.checking ? '' : s.state;
            return `<li><button type="button" class="work-setup-step${s.id === step.id ? ' is-selected' : ''}" data-step="${esc(s.id)}" aria-current="${s.id === step.id}">
                ${glyph(STEP_TONE[state], STEP_ICON[state] ?? '?', s.checking)}
                <span class="work-setup-step-title">${esc(s.title)}${s.optional ? ' <span class="work-meta">(optional)</span>' : ''}</span>
                <span class="w-sr-only">${esc(s.checking ? 'checking' : s.state)}</span></button></li>`;
        }).join('');
        const footer = vm.can_finish
            ? '<p class="work-caption" data-tone="ok"><strong>All set:</strong> press Finish setup.</p>'
            : `<p class="work-meta">${vm.blocking.length ? `To do: ${esc(vm.blocking.join(', '))}` : 'checking…'}</p>`;
        host.innerHTML = `
            <section class="w-card work-setup-steps" aria-label="Setup steps"><ul>${list}</ul>${footer}</section>
            <section class="w-card work-setup-detail" aria-labelledby="work-setup-step-title">
                <header class="w-card__head"><h2 class="w-card__title" id="work-setup-step-title">${esc(step.title)}</h2>${step.optional ? '<span class="w-count">optional</span>' : ''}</header>
                <div class="w-card__body work-stack">
                    <p class="work-muted">${esc(step.why)}</p>
                    <p class="work-setup-state">${glyph(STEP_TONE[step.checking ? '' : step.state], STEP_ICON[step.checking ? '' : step.state] ?? '?', step.checking)} ${esc(step.detail)}</p>
                    <div class="work-setup-actions work-stack">${step.actions.map(a => actionHtml(step, a)).join('') || '<p class="work-meta">Nothing to do here.</p>'}</div>
                    <div data-setup-log></div>
                </div>
            </section>`;
        renderLog();
    }

    function actionHtml(step, a) {
        const hint = a.hint ? `<small class="work-meta">${esc(a.hint)}</small>` : '';
        const key = `${step.id}:${a.id}`;
        if (a.kind === 'terminal') {
            return `<div class="work-setup-action work-setup-terminal">
                <div class="work-setup-action-label">${esc(a.label)} <span class="work-meta">(in a terminal)</span></div>
                <div class="work-setup-command"><code>${esc(a.command || '')}</code>
                    <button type="button" class="w-btn" data-variant="ghost" data-copy="${esc(a.command || '')}">Copy</button></div>
                ${hint}
                <div><button type="button" class="w-btn" data-variant="secondary" data-run="${esc(a.id)}">I ran it — check again</button></div></div>`;
        }
        if (a.kind === 'prompts' && openForm === key) {
            return `<form class="work-setup-action work-setup-form" data-form="${esc(a.id)}" autocomplete="off">
                <div class="work-setup-action-label">${esc(a.label)}</div>${hint}
                ${(a.prompts || []).map((p, i) => `<label class="w-field"><span class="w-field__label">${esc(p.label)}</span>
                    <input class="w-input" name="p${i}" ${p.secret ? 'type="password" autocomplete="new-password"' : `type="text" value="${esc(p.initial || '')}"`} ${i === 0 ? 'autofocus' : ''}></label>`).join('')}
                <div class="w-btn-row"><button type="submit" class="w-btn" data-variant="primary">Save</button><button type="button" class="w-btn" data-variant="ghost" data-cancel>Cancel</button></div></form>`;
        }
        const running = vm.job?.running;
        const disabled = a.kind === 'job' && running ? 'disabled title="another setup job is running"' : '';
        const variant = a.kind === 'job' ? 'primary' : 'secondary';
        return `<div class="work-setup-action"><button type="button" class="w-btn" data-variant="${variant}" data-run="${esc(a.id)}" ${disabled}>${esc(a.label)}${a.kind === 'nav' ? ' →' : ''}</button>${hint}</div>`;
    }

    function renderLog() {
        const host = document.querySelector('[data-setup-log]');
        if (!host || !vm) return;
        const job = vm.job;
        if (!job || (job.step !== selected && !job.running) || !job.lines) { host.innerHTML = ''; return; }
        const tone = job.running ? 'running' : job.ok ? 'ok' : 'danger';
        const icon = job.running ? '●' : job.ok ? '✓' : '✗';
        host.innerHTML = `<div class="work-setup-job"><div>${glyph(tone, icon, job.running)}
            <strong>${esc(job.title)}</strong> <span class="work-meta">${job.running ? 'running…' : job.ok ? 'passed' : 'failed'}</span></div>
            <pre class="work-setup-log" aria-live="polite">${esc(job.lines.join('\n'))}</pre></div>`;
        const pre = host.querySelector('pre');
        pre.scrollTop = pre.scrollHeight;
    }

    /* ------------------------------------------------------------ setup actions */
    async function runAction(stepId, actionId, inputs) {
        const step = vm.steps.find(s => s.id === stepId);
        const a = step?.actions.find(x => x.id === actionId);
        if (!a || busy) return;
        if (a.kind === 'nav') { work().go?.(`#/${a.nav || 'settings'}`); return; }
        if (a.kind === 'prompts' && !inputs) { openForm = `${stepId}:${actionId}`; renderSetup(); return; }
        if (a.confirm && !window.confirm(a.confirm)) return;
        busy = true;
        try {
            const res = await api('/api/jobs/setup/action', { method: 'POST', body: { step: stepId, action: actionId, inputs: inputs || [] } });
            openForm = '';
            vm = res.setup || vm;
            if (res.nav) work().go?.(`#/${res.nav}`);
            if (res.message) toast(res.message, a.kind === 'job' ? 'accent' : 'ok');
            if (vm.job?.running) startPolling();
        } catch (e) {
            toast(`${a.label}: ${e.message}`, 'danger');
        } finally {
            busy = false;
            renderSetup();
            reminders();
        }
    }

    function startPolling() {
        if (poll) return;
        poll = setInterval(async () => {
            try {
                const wasRunning = vm?.job?.running;
                vm = await api('/api/jobs/setup');
                if (vm.job?.running) {
                    // Keep any open form intact: only the log changes while a job runs.
                    if (document.querySelector('.work-setup-form')) renderLog(); else renderSetup();
                    return;
                }
                clearInterval(poll); poll = null;
                if (wasRunning && vm.job) toast(`${vm.job.title}: ${vm.job.ok ? 'done' : 'failed'}`, vm.job.ok ? 'ok' : 'danger');
                renderSetup();
                reminders();
            } catch (e) { clearInterval(poll); poll = null; toast(e.message, 'danger'); }
        }, 1000);
    }

    function bindSetup() {
        const host = $('work-setup');
        host.addEventListener('click', async e => {
            const stepBtn = e.target.closest('[data-step]');
            if (stepBtn) { selected = stepBtn.dataset.step; openForm = ''; renderSetup(); host.querySelector(`[data-step="${CSS.escape(selected)}"]`)?.focus(); return; }
            const copy = e.target.closest('[data-copy]');
            if (copy) {
                try { await navigator.clipboard.writeText(copy.dataset.copy); toast('Copied', 'ok'); }
                catch { toast('Copy failed: select the command and copy it', 'warn'); }
                return;
            }
            if (e.target.closest('[data-cancel]')) { openForm = ''; renderSetup(); return; }
            const run = e.target.closest('[data-run]');
            if (run) runAction(selected, run.dataset.run);
        });
        host.addEventListener('submit', e => {
            const form = e.target.closest('[data-form]');
            if (!form) return;
            e.preventDefault();
            const inputs = [...form.querySelectorAll('input')].map(i => i.value);
            runAction(selected, form.dataset.form, inputs);
        });
        $('work-setup-recheck').addEventListener('click', async () => {
            const btn = $('work-setup-recheck');
            btn.disabled = true;
            try { vm = await api('/api/jobs/setup/recheck', { method: 'POST', body: {} }); toast('Checked again', 'ok'); }
            catch (e) { toast(e.message, 'danger'); }
            finally { btn.disabled = false; renderSetup(); reminders(); }
        });
        $('work-setup-finish').addEventListener('click', async () => {
            try { vm = await api('/api/jobs/setup/finish', { method: 'POST' }); toast('Setup complete', 'ok'); }
            catch (e) { toast(e.message, 'danger'); }
            renderSetup(); reminders();
        });
    }

    /* ------------------------------------------------------------ doctor */
    function renderDoctor() {
        const host = $('work-doctor');
        if (!host) return;
        const badge = $('work-doctor-badge');
        if (doctorBusy && !doctor) { host.innerHTML = `<div class="work-loading" role="status"><span class="w-inkdrop" aria-hidden="true"></span>${esc(doctorBusy)}</div>`; return; }
        if (!doctor) return;
        const checks = doctor.checks || [];
        const ok = checks.filter(c => c.status === 'ok').length;
        badge.textContent = `${ok}/${checks.length} ok${doctor.failed ? ` · ${doctor.failed} failing` : ''}${doctor.warnings ? ` · ${doctor.warnings} warnings` : ''}`;
        badge.className = 'w-count';
        badge.dataset.tone = doctor.failed ? 'danger' : doctor.warnings ? 'attention' : 'ok';
        host.innerHTML = `
            ${doctorBusy ? `<p class="work-loading" role="status"><span class="w-inkdrop" data-size="sm" aria-hidden="true"></span>${esc(doctorBusy)}</p>` : ''}
            ${doctor.failed ? `<div class="w-banner work-doctor-cta" data-tone="danger" role="alert"><span class="w-banner__glyph" aria-hidden="true">✗</span><p class="w-banner__msg">${doctor.failed} check${doctor.failed === 1 ? '' : 's'} failing. The Checklist tab has the actions that fix them.</p><a class="w-btn" data-variant="tonal" data-tone="danger" data-size="sm" href="#/setup">Open checklist</a></div>` : ''}
            <div class="w-table-wrap"><table class="w-table work-doctor-table"><thead><tr><th scope="col"><span class="w-sr-only">Status</span></th><th scope="col">Check</th><th scope="col">Detail</th></tr></thead>
            <tbody>${checks.map(c => `<tr class="is-${esc(c.status)}"><td>${glyph(DOC_TONE[c.status], DOC_ICON[c.status] || '?', false, `aria-label="${esc(c.status)}"`)}</td>
                <td><code>${esc(c.name)}</code></td><td class="${c.status === 'ok' ? 'work-muted' : 'work-tone'}"${c.status === 'ok' ? '' : toneAttr(DOC_TONE[c.status])}>${esc(c.detail)}</td></tr>`).join('')}</tbody></table></div>`;
    }

    async function runDoctor(deep = false) {
        if (doctorBusy) return;
        doctorBusy = deep ? 'Running the sandbox self-test (starts real containers, ~10-20s)…' : 'Running checks…';
        $('work-doctor-run').disabled = $('work-doctor-deep').disabled = true;
        renderDoctor();
        try {
            doctor = await api(`/api/jobs/doctor${deep ? '?deep=1' : ''}`);
            if (deep) toast(doctor.failed ? `Doctor: ${doctor.failed} failing` : 'Doctor: all checks pass', doctor.failed ? 'danger' : 'ok');
        } catch (e) { toast(`Doctor: ${e.message}`, 'danger'); }
        finally {
            doctorBusy = false;
            $('work-doctor-run').disabled = $('work-doctor-deep').disabled = false;
            renderDoctor();
            if (doctor && doctor.needs_setup !== undefined) reminders(doctor.needs_setup);
        }
    }

    /* ------------------------------------------------------------ reminders */
    function reminders() {
        const pending = !!vm && (!vm.completed_at || (vm.all_checked && vm.blocking.length > 0));
        const badge = $('work-setup-badge');
        if (badge) { badge.hidden = !pending; badge.title = pending ? (vm.blocking.length ? `To do: ${vm.blocking.join(', ')}` : 'Setup not finished') : ''; }
        const tabBadge = $('work-setup-tab-badge');
        if (tabBadge) tabBadge.hidden = !pending;
        const cta = $('work-setup-cta');
        if (!cta) return;
        // One call-to-action for everything jobs still need: setup steps first, then stage models.
        const models = missingModels.length ? missingModels.join(', ') : '';
        const signature = `${vm ? `${vm.completed_at || ''}|${vm.blocking.join(',')}` : ''}|${models}`;
        cta.hidden = !(pending || models) || localStorage.getItem(DISMISS_KEY) === signature;
        const text = cta.querySelector('[data-cta-text]'), link = cta.querySelector('a');
        if (pending) {
            text.textContent = (vm.blocking.length ? `Jobs setup has steps to do: ${vm.blocking.join(', ')}.` : 'Jobs aren’t fully set up yet: the checklist checks Docker, the runtime image, agents and the sandbox.')
                + (models ? ` No default model for ${models} (jobs use their session’s model).` : '');
            link.href = '#/setup'; link.textContent = 'Open setup';
        } else if (models) {
            text.textContent = `No default model for ${models}: jobs use their session’s model for those stages.`;
            link.href = '#/settings'; link.textContent = 'Choose models';
        }
        cta.dataset.signature = signature;
    }
    window.addEventListener('mu:stage-models', e => { missingModels = (e.detail?.missing || []).slice(); reminders(); });

    /* ------------------------------------------------------------ wiring */
    // Setup has two tabs: Checklist (#/setup) and Doctor (#/doctor or #/setup?tab=doctor).
    function showTab(tab) {
        document.querySelectorAll('[data-setup-tab]').forEach(a => a.setAttribute('aria-selected', String(a.dataset.setupTab === tab)));
        document.querySelectorAll('[data-setup-panel]').forEach(p => { p.hidden = p.dataset.setupPanel !== tab; });
    }
    function onRoute(name, params) {
        if (name !== 'setup') return;
        const tab = params?.get?.('tab') === 'doctor' ? 'doctor' : 'checklist';
        showTab(tab);
        if (tab === 'doctor') {
            if (!doctor) runDoctor(false); else renderDoctor();
            return;
        }
        if (vm) renderSetup();
        load().then(() => { renderSetup(); if (vm.job?.running) startPolling(); }).catch(e => {
            $('work-setup').innerHTML = `<div class="w-banner" data-tone="danger" role="alert"><span class="w-banner__glyph" aria-hidden="true">✗</span><p class="w-banner__msg">${esc(e.message)}</p></div>`;
        });
    }

    function init() {
        if (!$('work-setup')) return;
        bindSetup();
        $('work-doctor-run').addEventListener('click', () => runDoctor(false));
        $('work-doctor-deep').addEventListener('click', () => runDoctor(true));
        $('work-setup-cta')?.querySelector('[data-cta-dismiss]')?.addEventListener('click', () => {
            const cta = $('work-setup-cta');
            localStorage.setItem(DISMISS_KEY, cta.dataset.signature || '');
            cta.hidden = true;
        });
        window.addEventListener('mu:work-route', e => onRoute(e.detail?.name, e.detail?.params));
        const current = work().route?.();
        if (current?.name === 'setup') onRoute('setup', current.params);
        // Badge + dashboard reminder: one setup check shortly after load (the server caches it).
        setTimeout(() => { if (!vm) load().catch(() => {}); }, 1500);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
    window.MuJobSetup = { load, renderSetup, runDoctor, state: () => ({ vm, doctor }) };
})();
