/* Jobs mode — planos-style dashboard, jobs list and job page over the core VMs.
 *
 * Data: GET '/api/jobs/vm/home', GET /api/jobs/vm/job/{id}?parts=…, POST '/api/jobs/cmd',
 * SSE '/api/jobs/events' (part-scoped refetch). Every button comes from job.actions —
 * availability is decided once, on the server. UI primitives (dialogs, toasts, palette)
 * come from work/ui.js when present, with native fallbacks so this file stands alone.
 */
(() => {
    'use strict';
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const money = value => `$${Number(value || 0).toFixed(2)}`;
    const duration = ms => {
        const s = Math.max(0, Math.round(Number(ms || 0) / 1000));
        if (s < 60) return `${s}s`;
        const m = Math.floor(s / 60);
        if (m < 60) return `${m}m`;
        const h = Math.floor(m / 60);
        return h < 48 ? `${h}h ${m % 60}m` : `${Math.floor(h / 24)}d`;
    };
    const age = seconds => {
        const s = Math.max(0, Math.round(Number(seconds || 0)));
        if (s < 60) return `${s}s`; if (s < 3600) return `${Math.floor(s / 60)}m`;
        if (s < 172800) return `${Math.floor(s / 3600)}h`; return `${Math.floor(s / 86400)}d`;
    };
    const when = ts => { const d = new Date(Number(ts || 0) * 1000); return isNaN(d) ? '' : d.toISOString().slice(0, 19).replace('T', ' '); };
    const stripAnsi = text => String(text ?? '').replace(/\u001b\[[0-9;]*m/g, '');
    const ansi = text => {
        const map = {30:'black',31:'red',32:'green',33:'yellow',34:'blue',35:'magenta',36:'cyan',90:'black',91:'red',92:'green',93:'yellow',94:'blue',95:'magenta',96:'cyan'};
        let open = 0;
        const out = esc(text).replace(/\u001b\[([0-9;]*)m/g, (_, codes) => {
            let html = '</span>'.repeat(open); open = 0;
            for (const c of String(codes).split(';').map(Number)) {
                if (map[c]) { html += `<span class="ansi-${map[c]}">`; open++; }
                else if (c === 1) { html += '<span class="ansi-bold">'; open++; }
                else if (c === 2) { html += '<span class="ansi-dim">'; open++; }
            }
            return html;
        });
        return out + '</span>'.repeat(open);
    };

    /* status vocabulary (planos theme.ts STATUS) */
    const STATUS = {
        done: {icon:'✓', tone:'ok', label:'done'}, active: {icon:'●', tone:'running', label:'in progress'},
        waiting: {icon:'◆', tone:'warn', label:'needs you'}, failed: {icon:'✗', tone:'danger', label:'failed'},
        skipped: {icon:'–', tone:'muted', label:'skipped'}, pending: {icon:'○', tone:'muted', label:'not started'},
    };
    const STATE_STATUS = {selected:'pending', provisioning:'active', planning:'active', implementing:'active', checking:'active', reviewing:'active', pushing:'active',
        plan_review:'waiting', review_done:'waiting', needs_input:'waiting', pushed:'done', done:'done', failed:'failed', cancelled:'skipped'};
    const STATE_LABEL = {selected:'selected', provisioning:'preparing', planning:'planning', plan_review:'plan to approve', implementing:'implementing', checking:'checking',
        reviewing:'reviewing', review_done:'review done', pushing:'pushing', pushed:'pushed', done:'done', needs_input:'needs input', failed:'failed', cancelled:'cancelled'};
    const stateStatus = state => STATE_STATUS[state] || 'pending';
    const stateLabel = state => STATE_LABEL[state] || String(state || '').replaceAll('_', ' ');
    const SEVERITY = {blocker:'danger', major:'danger', minor:'warn', nit:'muted'};
    const VERDICT = {ready:['ok','ready to merge'], ready_with_nits:['ok','ready with nits'], needs_work:['warn','needs work'], do_not_merge:['danger','do not merge']};
    const COVERAGE = {met:'done', partial:'waiting', unmet:'failed', unverifiable:'skipped'};
    /* planos ActionVM fields (tone / confirm / input / dialog / hidden) come from the server;
     * these fallbacks keep older servers (actions without the new keys) on the same flows. */
    const TONE = {approvePlan:'ok', rejectPlan:'danger', approveTool:'warn', denyTool:'danger', retry:'warn', restartStage:'warn', resume:'warn', push:'ok', discard:'danger', delete:'danger'};
    const LABEL = {savePlanDraft:'Edit plan', reprompt:'Fix selected findings', message:'Message agent'};
    const FALLBACK_CONFIRM = {
        discard: 'Discard this job? Running jobs are cancelled first and their worktree is removed. Git branch is preserved.',
        delete: 'Delete this job? Running jobs are cancelled first and their worktree is removed. Git branch is preserved.',
        restartStage: 'Restart the current stage? Its in-flight work is discarded.',
    };
    const FALLBACK_INPUT = {
        rejectPlan: {label:'What should the re-plan change?', optional:false, multiline:true},
        denyTool: {label:'Reason for the agent (optional)', optional:true, multiline:false},
        resume: {label:'Guidance for the resumed stage', optional:true, multiline:true},
        reprompt: {label:'Extra instructions for the fix', optional:true, multiline:true},
        message: {label:'Message for the agent', optional:false, multiline:true},
    };
    /* which command arg receives the text typed into an action's input */
    const INPUT_ARG = {rejectPlan:'feedback', denyTool:'message', resume:'guidance', reprompt:'guidance', message:'text', rejectEnvironment:'reason'};
    // The approval banners own these buttons; they stay out of the action bar.
    const LIVE_DIFF_MS = 5000;
    const BANNER_ACTIONS = new Set(['approveTool', 'denyTool', 'approveEnvironment', 'rejectEnvironment']);
    /* A job paused on a cap shows its own banner (message · amount · Approve more · Cancel job). */
    const LIMIT_BANNER_ACTIONS = new Set(['raiseLimit', 'discard']);
    /* The patch the Diff tab shows: the live worktree diff while the job runs, else the checkpointed diff.patch. */
    const diffPatch = j => (j?.diff?.live ? j.diff.live.patch : j?.diff?.patch);
    const HIDDEN = new Set(['markFindings', 'markViewed']);
    const toneOf = a => (a.tone !== undefined && a.tone !== null ? a.tone : TONE[a.id]) || (a.danger ? 'danger' : '');
    const confirmOf = a => (a.confirm !== undefined ? a.confirm : FALLBACK_CONFIRM[a.id]) || '';
    const inputOf = a => (a.input !== undefined ? a.input : FALLBACK_INPUT[a.id]) || null;
    const dialogOf = a => a.dialog || (a.id === 'push' ? 'push' : null);
    const hiddenOf = a => !!a.hidden || HIDDEN.has(a.id);
    /* Build environments (work/environments.js): panel + editor; the commands live in the panel. */
    const ENV = window.MuJobEnvironments || null;
    const ENV_ACTIONS = new Set(['setEnvironments', 'refreshEnvironments', 'keepEnvironments']);

    const statusIcon = (status, label) => { const s = STATUS[status] || STATUS.pending;
        return `<span class="status-icon tone-${s.tone} ${status === 'active' ? 'active' : ''}" title="${esc(label || s.label)}"><span aria-hidden="true">${s.icon}</span><span class="sr-only">${esc(label || s.label)}</span></span>`; };
    const pill = (tone, html, title = '') => `<span class="pill tone-${tone}"${title ? ` title="${esc(title)}"` : ''}>${html}</span>`;
    const statePill = state => { const s = STATUS[stateStatus(state)];
        return pill(s.tone, `<span aria-hidden="true" class="${stateStatus(state) === 'active' ? 'status-icon active' : ''}">${s.icon}</span>${esc(stateLabel(state))}`, state); };
    const resultBar = (passed, failed, skipped = 0, large = false) => {
        const sum = Math.max(1, passed + failed + skipped), pct = n => `${(n / sum) * 100}%`;
        const text = `${passed} passed, ${failed} failed${skipped ? `, ${skipped} skipped` : ''}`;
        return `<div class="bar ${large ? 'lg' : ''}" role="img" aria-label="${text}" title="${text}">${passed ? `<span class="ok" style="width:${pct(passed)}"></span>` : ''}${failed ? `<span class="danger" style="width:${pct(failed)}"></span>` : ''}${skipped ? `<span class="muted" style="width:${pct(skipped)}"></span>` : ''}</div>`;
    };
    const jobKey = row => row.key || `#${String(row.id || '').slice(0, 8)}`;
    const stepsOf = row => row.pipeline || row.steps || [];
    const describeStep = s => [`${s.label}: ${(STATUS[s.status] || STATUS.pending).label}`, s.elapsed >= 1 ? duration(s.elapsed * 1000) : '', s.cost_usd ? money(s.cost_usd) : '', s.runs > 1 ? `${s.runs} runs` : ''].filter(Boolean).join(', ');
    const focusStep = steps => steps.find(s => s.status === 'failed') || steps.find(s => s.status === 'active' || s.status === 'waiting');

    function miniStepper(steps) {
        const G = 14, w = steps.length * G;
        return `<svg class="work-mini" width="${w}" height="14" viewBox="0 0 ${w} 14" role="img" aria-label="${esc('Pipeline: ' + steps.map(describeStep).join('; '))}">${steps.map((s, i) => {
            const cx = G / 2 + i * G, tok = (STATUS[s.status] || STATUS.pending).tone;
            return `<g><title>${esc(describeStep(s))}</title>${i < steps.length - 1 ? `<line x1="${cx + 4}" y1="7" x2="${cx + G - 4}" y2="7" class="link"/>` : ''}<circle cx="${cx}" cy="7" r="4" class="mininode m_${tok} ${s.status === 'pending' ? 'hollow' : ''} ${s.status === 'active' ? 'active' : ''}"/></g>`;
        }).join('')}</svg>`;
    }
    function pipelineStepper(steps) {
        const W = 160, width = steps.length * W;
        return `<div class="work-stepper"><svg viewBox="0 0 ${width} 82" preserveAspectRatio="xMidYMid meet" aria-hidden="true" focusable="false">${steps.map((s, i) => {
            const cx = W / 2 + i * W, st = STATUS[s.status] || STATUS.pending, next = steps[i + 1];
            const meta = [s.elapsed >= 1 ? duration(s.elapsed * 1000) : '', s.cost_usd ? money(s.cost_usd) : '', s.runs > 1 ? `×${s.runs}` : ''].filter(Boolean).join(' · ');
            return `<g data-step="${esc(s.id)}" data-status="${esc(s.status)}"><title>${esc(describeStep(s) + (s.detail ? ` — ${s.detail}` : ''))}</title>
                ${next ? `<line x1="${cx + 16}" y1="20" x2="${cx + W - 16}" y2="20" class="link ${s.status === 'done' || s.status === 'skipped' ? 'link-done' : ''}"/>` : ''}
                <circle cx="${cx}" cy="20" r="14" class="node n_${st.tone} ${s.status === 'active' ? 'active' : ''} ${s.status === 'pending' ? 'pending' : ''}"/>
                <text x="${cx}" y="25" text-anchor="middle" class="glyph t_${st.tone}">${st.icon}</text>
                <text x="${cx}" y="54" text-anchor="middle" class="label ${s.status === 'pending' ? 'dim' : ''}">${esc(s.label)}</text>
                <text x="${cx}" y="72" text-anchor="middle" class="meta">${esc(meta)}</text></g>`;
        }).join('')}</svg><ol class="sr-only" aria-label="Pipeline">${steps.map(s => `<li>${esc(describeStep(s))}</li>`).join('')}</ol></div>`;
    }

    /* Sanitised markdown (plan content and agent text may contain repository-controlled HTML). */
    function markdown(text) {
        if (window.MuJobPlan?.preview) return window.MuJobPlan.preview(String(text ?? ''));
        return `<pre>${esc(text)}</pre>`;
    }

    /* ---------------------------------------------------------------- unified diff parsing */
    function parsePatch(patch) {
        const files = [];
        for (const block of String(patch || '').split(/(?=^diff --git )/m).filter(b => b.trim())) {
            const path = block.match(/^\+\+\+ b\/(.*)$/m)?.[1] || block.match(/^--- a\/(.*)$/m)?.[1] || block.match(/^diff --git a\/(\S+)/)?.[1] || 'diff';
            const status = /^new file mode/m.test(block) ? 'A' : /^deleted file mode/m.test(block) ? 'D' : /^rename from/m.test(block) ? 'R' : 'M';
            const lines = []; let oldNo = 0, newNo = 0, added = 0, removed = 0, inHunk = false;
            for (const line of block.split('\n')) {
                const hunk = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/);
                if (hunk) { oldNo = Number(hunk[1]); newNo = Number(hunk[2]); inHunk = true; lines.push({kind:'hunk', text:line}); continue; }
                if (!inHunk) continue;
                if (line.startsWith('+')) { lines.push({kind:'add', text:line.slice(1), newNo:newNo++}); added++; }
                else if (line.startsWith('-')) { lines.push({kind:'del', text:line.slice(1), oldNo:oldNo++}); removed++; }
                else if (line.startsWith('\\')) lines.push({kind:'note', text:line});
                else if (line.startsWith(' ') || line === '') { if (line === '' && lines.length && lines[lines.length - 1].kind === 'note') continue; lines.push({kind:'ctx', text:line.slice(1), oldNo:oldNo++, newNo:newNo++}); }
            }
            while (lines.length && lines[lines.length - 1].kind === 'ctx' && lines[lines.length - 1].text === '') lines.pop();
            files.push({path, status, lines, added, removed, binary: /^Binary files/m.test(block)});
        }
        return files;
    }
    const LANG = {py:'python', js:'javascript', mjs:'javascript', ts:'typescript', tsx:'typescript', jsx:'javascript', rs:'rust', go:'go', java:'java', c:'c', h:'c', cc:'cpp', cpp:'cpp', hpp:'cpp',
        cs:'csharp', rb:'ruby', php:'php', sh:'bash', bash:'bash', css:'css', html:'xml', xml:'xml', json:'json', yml:'yaml', yaml:'yaml', md:'markdown', sql:'sql', lua:'lua', kt:'kotlin', swift:'swift', toml:'ini'};
    function highlight(text, path) {
        const lang = LANG[String(path).split('.').pop()?.toLowerCase()];
        if (!lang || !window.hljs?.getLanguage?.(lang)) return esc(text);
        try { return window.hljs.highlight(text, {language:lang, ignoreIllegals:true}).value; } catch (_) { return esc(text); }
    }

    function create({ api, toast, boardEl, detailEl, totalEl, jobsEl = null, onOpen = null, onRoute = null, onHome = null, onConnection = null }) {
        const ui = window.MuJobUI || {
            confirm: message => Promise.resolve(window.confirm(message)),
            prompt: ({label, optional}) => { const v = window.prompt(label, ''); return Promise.resolve(v === null || (!optional && !v.trim()) ? null : v); },
            register: () => {},
        };
        const notify = (text, tone = 'accent') => toast(text, tone);
        let home = null, job = null, selectedId = '', tab = 'plan', editor = null, editing = false, source = null, fetchNumber = 0;
        let sessionStage = '', splitDiff = localStorage.getItem('mucli-jobs-diff') === 'split', selectedRun = null, jobsQuery = '', jobsNeedsOnly = false, jobsSession = '', showUnavailable = false;
        let attempts = null, envOpen = false;
        const selectedFindings = new Set();
        const collapsed = new Set();
        const signatures = new Map();
        const tabs = ['session', 'plan', 'diff', 'review', 'checks', 'diagnostics', 'costs'];
        const labels = {plan:'Plan', session:'Session', diff:'Diff', review:'Review', checks:'Checks', diagnostics:'Diagnostics', costs:'Cost & audit'};
        const stepTabs = {plan:'plan', approve:'plan', implement:'session', checks:'checks', review:'review', push:'review'};
        const open = id => (onOpen ? onOpen(id) : select(id));
        /* "Viewed" marks are server-side (markViewed → diff.viewed); a file whose hunk changes drops out.
         * Servers without markViewed keep the marks for this page visit only. */
        const viewed = () => new Set(job?.diff?.viewed || []);
        async function setViewed(path, on) {
            const mark = job.actions.find(a => a.id === 'markViewed');
            const before = job.diff.viewed;
            const next = new Set(before || []); on ? next.add(path) : next.delete(path);
            job.diff = {...job.diff, viewed:[...next]};
            renderPart('diff', true);
            if (!mark) return;
            if (!mark.enabled) { job.diff = {...job.diff, viewed:before}; renderPart('diff', true); return notify(`Mark viewed: ${mark.reason || 'not available now'}`, 'warn'); }
            try { await send('markViewed', {path, viewed:on}); await refreshParts(['diff']); }
            catch (e) { job.diff = {...job.diff, viewed:before}; renderPart('diff', true); notify(e.message, 'danger'); }
        }

        function defaultTab(next) {
            const state = next.state;
            if (next.session?.approval) return 'session';
            if (state === 'plan_review') return 'plan';
            if (state === 'review_done' || state === 'pushed' || state === 'done') return 'review';
            if (state === 'checking') return 'checks';
            if (state === 'failed') return 'diagnostics';
            return 'session';
        }

        /* ------------------------------------------------------------ commands */
        async function send(type, args = {}) {
            return api('/api/jobs/cmd', { method:'POST', body:{ type, job_id:selectedId, args } });
        }
        /* planos ActionRunner: page flows first (edit / message / findings), then dialog → confirm →
         * input, then POST /api/jobs/cmd. The server re-checks availability (409 when disabled). */
        async function invoke(type) {
            const action = job?.actions?.find(a => a.id === type);
            if (!action?.enabled) return notify(`${LABEL[type] || action?.label || type}: ${action?.reason || 'not available now'}`, 'warn');
            const label = LABEL[type] || action.label;
            let args = {...(action.args || {})};
            if (type === 'savePlanDraft') { editing = true; signatures.delete('plan'); selectTab('plan'); return; }
            if (type === 'message') { selectTab('session'); setTimeout(() => detailEl.querySelector('[data-message]')?.focus(), 0); return; }
            if (type === 'markFindings' || type === 'markViewed') { selectTab(type === 'markViewed' ? 'diff' : 'review'); return; }
            if (type === 'approvePlan') {
                if (editor?.isDirty()) return notify('Save or cancel your plan edits before approving.', 'warn');
                args.version = job.plan?.latest_version ?? job.plan?.version ?? args.version;
            }
            if (type === 'reprompt' && !selectedFindings.size) { selectTab('review'); return notify('Pick the findings to fix on the Review tab.', 'warn'); }
            if (dialogOf(action) === 'push') return pushDialog(action);
            if (dialogOf(action) === 'environments') return environmentsDialog(action);
            const confirmText = confirmOf(action);
            if (confirmText && !await ui.confirm(['delete', 'discard'].includes(type) ? `${confirmText}\n\n${jobKey(job)} · ${job.title}` : confirmText,
                {title:`${label} · ${jobKey(job)}`, okLabel:label, tone:toneOf(action) === 'danger' ? 'danger' : undefined})) return;
            const input = inputOf(action);
            if (input) {
                let text;
                const banner = type === 'denyTool' ? detailEl.querySelector('[data-deny-reason]')
                    : type === 'raiseLimit' ? detailEl.querySelector('[data-limit-amount]') : null;
                if (banner) text = banner.value; // the approval / limit banner carries its own field (planos)
                else {
                    const title = type === 'reprompt' ? `Fix ${selectedFindings.size} finding${selectedFindings.size === 1 ? '' : 's'} · ${jobKey(job)}` : `${label} · ${jobKey(job)}`;
                    text = await ui.prompt({title, label:input.label, optional:!!input.optional, multiline:!!input.multiline, okLabel:type === 'reprompt' ? 'Fix findings' : label,
                        value:input.value || '', hint:input.hint || ''});
                    if (text === null) return;
                }
                if (!input.optional && !String(text || '').trim()) return notify(`${label}: ${input.label || 'a value'} is required`, 'warn');
                // input.arg (server) names the command arg; older servers fall back to the id map.
                const key = input.arg || INPUT_ARG[type] || 'text';
                if (String(text || '').trim()) args[key] = type === 'message' ? text.trim() : text;
                else if (!input.optional) return;
                else if (type === 'resume' || type === 'reprompt') args[key] = '';
            }
            if (type === 'reprompt') args.finding_ids = [...selectedFindings];
            try {
                const result = await api('/api/jobs/cmd', { method:'POST', body:{ type, job_id:selectedId, args } });
                notify(`${label}: done`, 'ok');
                if (type === 'delete') { editor?.destroy(); editor = null; job = null; selectedId = ''; delete detailEl.dataset.jobId; detailEl.innerHTML = ''; onOpen?.(null); }
                else { if (type === 'reprompt') selectedFindings.clear(); await refreshParts(result.parts); }
                await refreshHome();
            } catch (error) { notify(error.message, 'danger'); }
        }

        /* ------------------------------------------------------------ push dialog (planos PushDialog) */
        async function pushDialog(action) {
            const id = selectedId, key = jobKey(job);
            let preview;
            try { preview = await api(`/api/jobs/vm/job/${encodeURIComponent(id)}/push-preview`); }
            catch (e) {
                if (!/404|not found/i.test(e.message)) return notify(e.message, 'danger');
                // Older server without the preview VM: the default PR text is composed server-side.
                preview = {title:'', body:'', branch:job.branch || '', base:'', mode:'', remote:'', draft:true, requires_override:!!action.args?.requires_override, verdict:job.verdict || '', head_sha:'', reason:''};
            }
            if (id !== selectedId) return;
            const needsOverride = !!(preview.requires_override || action.args?.requires_override);
            const [vTone, vLabel] = VERDICT[preview.verdict] || ['muted', String(preview.verdict || '').replaceAll('_', ' ')];
            const form = document.createElement('form');
            form.className = 'col work-push-form';
            form.noValidate = true;
            form.innerHTML = `
                <div class="row wrap small work-push-target">
                    <span class="row mono"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" aria-hidden="true"><circle cx="6" cy="6" r="3"/><circle cx="18" cy="18" r="3"/><path d="M6 9v12M18 9a9 9 0 0 1-9 9"/></svg><span class="sr-only">branch</span>${esc(preview.branch || job.branch || '(job branch)')}</span>
                    <span aria-hidden="true" class="muted">→</span><span class="sr-only">into</span><span class="mono">${esc(preview.base || 'default branch')}</span>
                    ${preview.mode ? pill(preview.mode === 'none' ? 'warn' : 'accent', `via ${esc(preview.mode)}`, preview.mode === 'none' ? 'No PR provider: the branch is pushed without opening a PR' : '') : ''}
                    ${preview.verdict ? pill(vTone, esc(vLabel)) : ''}
                    ${preview.head_sha ? `<span class="muted mono" title="verified head">${esc(String(preview.head_sha).slice(0, 12))}</span>` : ''}
                </div>
                ${preview.remote ? `<div class="small muted ellipsis" title="${esc(preview.remote)}">remote <code>${esc(preview.remote)}</code></div>` : ''}
                ${preview.reason ? `<div class="note-box small" role="note"><span class="tone-warn" aria-hidden="true">◆</span> ${esc(preview.reason)}</div>` : ''}
                <div class="field"><label class="label" for="work-push-title">PR title</label>
                    <input id="work-push-title" type="text" maxlength="200" value="${esc(preview.title)}" placeholder="${preview.title ? '' : 'Default: composed from the job'}" data-autofocus></div>
                <div class="field"><div class="row"><label class="label" for="work-push-body">PR description</label><span class="spacer"></span>
                    <div class="work-segmented" role="group" aria-label="Description view"><button type="button" data-push-view="write" aria-pressed="true">Write</button><button type="button" data-push-view="preview" aria-pressed="false">Preview</button></div></div>
                    <textarea id="work-push-body" rows="8" maxlength="20000" placeholder="${preview.body ? '' : 'Default: composed from the plan, checks and review'}">${esc(preview.body)}</textarea>
                    <div class="work-push-preview md panel panel-body" data-push-preview hidden></div>
                    <span class="hint">Markdown. Leave blank to use the default text.</span></div>
                <label class="check"><input type="checkbox" id="work-push-draft" ${preview.draft === false ? '' : 'checked'}> Open as a draft PR</label>
                ${needsOverride ? `<div class="field"><div class="error-box small" role="alert"><span aria-hidden="true">✗ </span>The review verdict blocks merging. Pushing anyway needs a reason; it is recorded in the job audit.</div>
                    <label class="label" for="work-push-override">Reason for overriding the blocking review</label>
                    <textarea id="work-push-override" rows="3" required aria-required="true" style="font-family:var(--font-ui)"></textarea><span class="error" data-push-error></span></div>` : ''}`;
            const foot = document.createElement('div');
            foot.className = 'row';
            foot.innerHTML = `<button type="button" class="btn" data-cancel>Cancel</button><button type="button" class="btn solid tone-${needsOverride ? 'danger' : 'ok'}" data-push-submit>${needsOverride ? 'Override and push' : `Push ${esc(preview.branch || job.branch || '')}`.trim()}${action.shortcut ? '' : ''}</button>`;
            const d = ui.dialog ? ui.dialog({title:`Push ${key}`, body:form, footer:foot, wide:true}) : null;
            if (!d) return;
            const body = form.querySelector('#work-push-body'), previewEl = form.querySelector('[data-push-preview]');
            form.querySelectorAll('[data-push-view]').forEach(b => { b.onclick = () => {
                const show = b.dataset.pushView === 'preview';
                form.querySelectorAll('[data-push-view]').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
                if (show) previewEl.innerHTML = body.value.trim() ? markdown(body.value) : '<p class="muted">Nothing to preview — the default description is used.</p>';
                previewEl.hidden = !show; body.hidden = show;
            }; });
            foot.querySelector('[data-cancel]').onclick = () => d.close();
            const submit = foot.querySelector('[data-push-submit]');
            const go = async () => {
                const override = form.querySelector('#work-push-override');
                if (override && !override.value.trim()) {
                    override.setAttribute('aria-invalid', 'true'); form.querySelector('[data-push-error]').textContent = 'A reason is required to override the review.'; override.focus(); return;
                }
                const args = {...(action.args || {}), pr_title:form.querySelector('#work-push-title').value.trim(), pr_body:body.value, draft:form.querySelector('#work-push-draft').checked};
                delete args.requires_override;
                if (override) args.override_reason = override.value.trim();
                submit.disabled = true; submit.textContent = 'Pushing…';
                try {
                    const result = await api('/api/jobs/cmd', { method:'POST', body:{ type:'push', job_id:id, args } });
                    d.close(); notify(`${action.label}: done`, 'ok');
                    if (id === selectedId) await refreshParts(result.parts);
                    await refreshHome();
                } catch (error) { submit.disabled = false; submit.textContent = needsOverride ? 'Override and push' : 'Push'; notify(error.message, 'danger'); }
            };
            submit.onclick = go;
            form.onsubmit = e => { e.preventDefault(); go(); };
        }
        /* ------------------------------------------------------------ build environments dialog */
        async function environmentsDialog(action) {
            if (!ENV) return notify('Build environments editor is unavailable', 'warn');
            const id = selectedId, info = job.build_environments || {};
            const bindings = await ENV.editor({api, ui, title:`Build environments · ${jobKey(job)}`, initial:info.bindings || [],
                context:{repository:job.repository || '', session:job.origin_session || ''}, clearLabel:'Use repo / session / settings'});
            if (bindings === null || id !== selectedId) return;
            try {
                const result = await api('/api/jobs/cmd', { method:'POST', body:{ type:'setEnvironments', job_id:id, args:{environments:bindings} } });
                notify(bindings.length ? `Build environments: ${ENV.summary(result.data?.environments?.bindings || bindings)}` : 'Build environments: back to the default resolution', 'ok');
                envOpen = true;
                await refreshParts(result.parts);
            } catch (error) { notify(error.message, 'danger'); }
        }
        function renderEnvironments() {
            const host = detailEl.querySelector('[data-environments]');
            if (!host) return;
            if (!ENV || !envOpen) { host.innerHTML = ''; return; }
            const buttons = ['refreshEnvironments', 'keepEnvironments', 'setEnvironments']
                .map(type => job.actions.find(a => a.id === type)).filter(Boolean)
                .map(a => actionButton({...a, label:a.id === 'setEnvironments' ? 'Change' : a.label}, {size:'sm'})).join('');
            host.innerHTML = ENV.panel(job.build_environments, buttons);
            bindCommands(host);
        }
        async function runById(id, type) {
            if (await select(id) !== true) return;
            await invoke(type);
        }

        function actionButton(a, {size = '', extra = ''} = {}) {
            const tone = toneOf(a);
            const solid = a.primary && a.enabled && ['ok', 'danger', 'warn'].includes(tone) ? 'solid' : '';
            const dirtyBlock = a.id === 'approvePlan' && editor?.isDirty();
            const label = LABEL[a.id] || a.label;
            const title = dirtyBlock ? `${label}: save or cancel your plan edits first` : a.enabled ? (a.reason || (a.shortcut ? `${label} (${a.shortcut})` : label)) : `${label}: ${a.reason || 'not available now'}`;
            return `<button type="button" class="btn ${size} ${tone ? `tone-${tone}` : ''} ${solid}" data-command="${esc(a.id)}" data-action="${esc(a.id)}"${a.id === 'approvePlan' ? ' data-plan-approve' : ''}${a.id === 'rejectPlan' ? ' data-plan-reject' : ''}${a.enabled ? '' : ' aria-disabled="true"'}${dirtyBlock ? ' disabled' : ''}${a.shortcut ? ` aria-keyshortcuts="${esc(a.shortcut)}"` : ''} title="${esc(title)}" ${extra}>${esc(label)}${a.shortcut ? `<kbd aria-hidden="true">${esc(a.shortcut)}</kbd>` : ''}</button>`;
        }
        function bindCommands(root) {
            root.querySelectorAll('[data-command]').forEach(b => { b.onclick = () => invoke(b.dataset.command); });
        }

        /* ------------------------------------------------------------ dashboard */
        function renderHome() {
            if (!home) return;
            totalEl.textContent = home.jobs.length;
            onHome?.(home);
            const now = Date.now() / 1000;
            const card = row => {
                const failed = row.state === 'failed';
                const acts = (row.actions || []).filter(a => a.primary && a.enabled);
                return `<li class="work-job-card ${failed ? 'is-failed' : ''} ${row.id === selectedId ? 'is-selected' : ''}">
                    <div class="row">${statusIcon(stateStatus(row.state))}<a href="#/job/${esc(row.id)}" class="mono" data-job-id="${esc(row.id)}" style="font-weight:600">${esc(jobKey(row))}</a><span class="${failed ? 'tone-danger' : 'tone-warn'} small">${esc(stateLabel(row.state))}</span></div>
                    <div class="ellipsis" title="${esc(row.title)}">${esc(row.title)}</div>
                    ${row.origin_session ? `<div class="small muted ellipsis">session · ${esc(row.origin_session)}</div>` : ''}
                    ${row.limit?.summary ? `<div class="small tone-warn ellipsis" title="${esc(row.attention_detail || row.limit.message || '')}" data-limit-summary>${esc(row.limit.summary)}</div>`
                      : row.attention_detail ? `<div class="small muted ellipsis" title="${esc(row.attention_detail)}">${esc(row.attention_detail)}</div>` : ''}
                    ${acts.length ? `<div class="row wrap">${acts.map(a => `<button type="button" class="btn sm" data-open-action="${esc(row.id)}" data-action-id="${esc(a.id)}">${esc(LABEL[a.id] || a.label)}${a.shortcut ? `<kbd aria-hidden="true">${esc(a.shortcut)}</kbd>` : ''}</button>`).join('')}</div>` : ''}</li>`;
            };
            const RUN_ICON = {planning:'bot', implementing:'bot', reviewing:'bot', checking:'hammer', provisioning:'loader', pushing:'upload'};
            const ICONS = {
                bot:'<path d="M12 8V4H8"/><rect width="16" height="12" x="4" y="8" rx="2"/><path d="M2 14h2M20 14h2M15 13v2M9 13v2"/>',
                hammer:'<path d="m15 12-8.4 8.4a2.1 2.1 0 1 1-3-3L12 9"/><path d="M17.6 15 22 10.6M20.9 11.7l-1.2-1.2a2 2 0 0 1-.6-1.4V7.9l-2.3-2.3a5.5 5.5 0 0 0-3.9-1.6H9l.9.8a6.2 6.2 0 0 1 2.1 4.6V11l2 2h1.2a2 2 0 0 1 1.4.6l1.2 1.2"/>',
                loader:'<path d="M12 2v4M12 18v4M4.9 4.9l2.9 2.9M16.2 16.2l2.9 2.9M2 12h4M18 12h4M4.9 19.1l2.9-2.9M16.2 7.8l2.9-2.9"/>',
                upload:'<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/>',
            };
            const svg = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" class="tone-running" aria-label="${esc(name)}">${ICONS[name] || ICONS.bot}</svg>`;
            const running = row => { const step = focusStep(stepsOf(row));
                return `<li class="work-run-item"><div class="row">${svg(RUN_ICON[row.state] || 'bot')}<a href="#/job/${esc(row.id)}" class="mono" data-job-id="${esc(row.id)}" style="font-weight:600">${esc(jobKey(row))}</a><span>${esc(stateLabel(row.state))}</span><span class="spacer"></span><span class="muted num">${step?.elapsed >= 1 ? duration(step.elapsed * 1000) : ''}</span><span class="num">${money(row.cost_usd)}</span></div>
                    <div class="ellipsis small">${esc(row.title)}</div>${step?.detail ? `<div class="mono small muted ellipsis" title="${esc(step.detail)}">› ${esc(step.detail)}</div>` : ''}</li>`; };
            const counts = {
                needs: home.needs_you.length, running: home.running.length,
                checking: home.jobs.filter(j => j.state === 'checking').length,
                ready: home.jobs.filter(j => j.state === 'review_done').length,
                jobs: home.jobs.length,
            };
            const stat = (value, label, tone, glyph, attrs) => `<li><a class="work-stat tone-${tone}" ${attrs}><span class="work-stat-value"><span aria-hidden="true">${glyph}</span> ${value}</span><span class="work-stat-label">${esc(label)}</span></a></li>`;
            const recent = home.jobs.filter(j => ['done', 'pushed', 'failed', 'cancelled'].includes(j.state)).sort((a, b) => b.updated_at - a.updated_at).slice(0, 6);
            boardEl.innerHTML = `
                <ul class="plain work-stats" aria-label="Summary">
                    ${stat(counts.needs, 'needs you', counts.needs ? 'warn' : 'muted', '◆', 'href="#needs-you"')}
                    ${stat(counts.running, counts.running === 1 ? 'job running' : 'jobs running', counts.running ? 'running' : 'muted', '●', 'href="#running-now"')}
                    ${stat(counts.checking, counts.checking === 1 ? 'check running' : 'checks running', counts.checking ? 'running' : 'muted', '●', 'href="#running-now"')}
                    ${stat(counts.ready, 'ready to push', counts.ready ? 'ok' : 'muted', '✓', 'href="#needs-you"')}
                    ${stat(counts.jobs, 'jobs', 'accent', '○', 'href="#/jobs"')}
                </ul>
                <div class="grid-2">
                    <section class="panel" aria-labelledby="needs-you"><header><h2 id="needs-you"><span class="tone-warn" aria-hidden="true">◆</span> Needs you</h2><span class="badge tone-warn">${home.needs_you.length}</span></header>
                        <div class="panel-body">${home.needs_you.length ? `<ul class="plain work-cards">${home.needs_you.map(card).join('')}</ul>` : `<p class="empty">${statusIcon('done')} Nothing needs you right now.</p>`}</div></section>
                    <section class="panel" aria-labelledby="running-now"><header><h2 id="running-now"><span class="tone-running" aria-hidden="true">●</span> Running now</h2><span class="badge tone-running">${home.running.length}</span></header>
                        <div class="panel-body">${home.running.length ? `<ul class="plain work-running">${home.running.map(running).join('')}</ul>` : '<p class="empty">Nothing is running.</p>'}</div></section>
                </div>
                <section class="panel" aria-labelledby="jobs-h"><header><h2 id="jobs-h">Jobs</h2><span class="badge">${home.jobs.length}</span><span class="spacer"></span><a href="#/jobs" class="small">All jobs →</a></header>
                    <div class="panel-body flush">${jobTable(home.jobs.slice(0, 12), now)}</div></section>
                <section class="panel" aria-labelledby="recent-h"><header><h2 id="recent-h">Recent results</h2></header>
                    <div class="panel-body">${recent.length ? `<ul class="plain col">${recent.map(r => `<li class="row">${statusIcon(stateStatus(r.state), stateLabel(r.state))}<a href="#/job/${esc(r.id)}" class="mono" data-job-id="${esc(r.id)}">${esc(jobKey(r))}</a><span class="grow ellipsis">${esc(r.title)}</span>${r.verdict ? pill((VERDICT[r.verdict] || ['muted'])[0], esc((VERDICT[r.verdict] || [0, r.verdict.replaceAll('_', ' ')])[1])) : ''}<span class="muted small num">${age(now - r.updated_at)} ago</span></li>`).join('')}</ul>` : '<p class="empty">No jobs finished recently.</p>'}</div></section>`;
            bindHome(boardEl);
            renderJobsPage();
            ui.register('page:dashboard', home.jobs.map(j => ({id:`open:${j.id}`, label:`Open ${jobKey(j)} · ${j.title}`, group:'Jobs', run:() => open(j.id)})));
        }
        function jobTable(rows, now, extra) {
            if (!rows.length) return '<p class="empty">No jobs yet. Create one with “New job”, or import tickets from a Jira board.</p>';
            return `<div class="table-wrap"><table class="table"><thead><tr><th scope="col">Key</th><th scope="col">Pipeline</th><th scope="col">State</th><th scope="col" class="num">Cost</th><th scope="col" class="num">Age</th><th scope="col">Summary</th>${extra ? '<th scope="col"><span class="sr-only">Actions</span></th>' : ''}</tr></thead><tbody>${rows.map(row => {
                const step = focusStep(stepsOf(row));
                const needs = stateStatus(row.state) === 'waiting' && row.attention_detail;
                return `<tr class="clickable" data-row-job="${esc(row.id)}" aria-selected="${row.id === selectedId}">
                    <td class="key nowrap"><a href="#/job/${esc(row.id)}" data-job-id="${esc(row.id)}">${esc(jobKey(row))}</a>${stateStatus(row.state) === 'active' ? ' <span class="tone-running small" title="running"><span aria-hidden="true" class="status-icon active">●</span><span class="sr-only">running</span></span>' : ''}</td>
                    <td><div class="row nowrap">${miniStepper(stepsOf(row))}<span class="muted small">${esc(step?.label?.toLowerCase() || '')}</span></div></td>
                    <td>${statePill(row.state)}${row.verdict ? `<div class="small tone-${(VERDICT[row.verdict] || ['muted'])[0]}">${esc((VERDICT[row.verdict] || [0, row.verdict.replaceAll('_', ' ')])[1])}</div>` : ''}${needs ? `<div class="small tone-warn ellipsis" style="max-width:220px" title="${esc(row.attention_detail)}">${esc(row.attention_detail)}</div>` : ''}</td>
                    <td class="num">${money(row.cost_usd)}</td>
                    <td class="num muted">${age(now - row.created_at)}</td>
                    <td class="ellipsis" style="max-width:420px" title="${esc(row.title)}">${esc(row.title)}${row.origin_session || row.repository ? `<div class="muted small ellipsis">${row.origin_session ? `<span title="session">session · ${esc(row.origin_session)}</span>` : ''}${row.origin_session && row.repository ? ' · ' : ''}${row.repository ? `<span class="mono">${esc(row.repository)}</span>` : ''}</div>` : ''}</td>
                    ${extra ? `<td class="nowrap">${extra(row)}</td>` : ''}</tr>`;
            }).join('')}</tbody></table></div>`;
        }
        function bindHome(root) {
            root.querySelectorAll('[data-job-id]').forEach(b => { b.onclick = e => { e.preventDefault(); open(b.dataset.jobId); }; });
            root.querySelectorAll('tr[data-row-job]').forEach(tr => { tr.onclick = e => { if (!e.target.closest('a,button,input')) open(tr.dataset.rowJob); }; });
            root.querySelectorAll('[data-open-action]').forEach(b => { b.onclick = async () => {
                if (onOpen) onOpen(b.dataset.openAction);
                if (await select(b.dataset.openAction) !== true) return;
                const primary = job?.actions.find(a => a.id === b.dataset.actionId && a.enabled) || job?.actions.find(a => a.primary && a.enabled);
                if (primary) await invoke(primary.id);
            }; });
            root.querySelectorAll('a[href^="#needs-you"], a[href^="#running-now"]').forEach(a => { a.onclick = e => { e.preventDefault(); root.querySelector(a.getAttribute('href'))?.scrollIntoView({block:'start', behavior:'smooth'}); }; });
        }
        function renderJobsPage() {
            if (!jobsEl || !home) return;
            const now = Date.now() / 1000;
            const sessionNames = [...new Set(home.jobs.map(j => j.origin_session || ''))].sort();
            const rows = home.jobs.filter(j => (!jobsNeedsOnly || ['waiting', 'failed'].includes(stateStatus(j.state))) &&
                (!jobsSession || (j.origin_session || '') === (jobsSession === '-' ? '' : jobsSession)) &&
                (!jobsQuery || `${jobKey(j)} ${j.title} ${stateLabel(j.state)} ${j.repository || ''} ${j.origin_session || ''} ${j.id}`.toLowerCase().includes(jobsQuery.toLowerCase())));
            const icon = d => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
            const focused = document.activeElement?.id;
            jobsEl.innerHTML = `<div class="page-head"><h1>Jobs <span class="muted">(${home.jobs.length})</span></h1><span class="spacer"></span>
                <label class="row small"><span class="muted">Session</span><select id="work-jobs-session" aria-label="Filter by session"><option value="">All sessions</option>${sessionNames.map(n => `<option value="${esc(n || '-')}" ${(n || '-') === jobsSession ? 'selected' : ''}>${esc(n || 'no session')}</option>`).join('')}</select></label>
                <label class="check small"><input type="checkbox" id="work-jobs-needs" ${jobsNeedsOnly ? 'checked' : ''}> only jobs that need me</label>
                <input type="search" id="work-jobs-filter" aria-label="Filter jobs" placeholder="Filter…" value="${esc(jobsQuery)}"></div>
                <section class="panel"><div class="panel-body flush">${jobTable(rows, now, row => {
                    const del = (row.actions || []).find(a => a.id === 'delete');
                    return `<div class="row"><button type="button" class="btn ghost icon sm" data-row-archive="${esc(row.id)}" aria-label="Archive ${esc(jobKey(row))}" title="Archive">${icon('<rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8M10 12h4"/>')}</button>
                        <button type="button" class="btn ghost icon sm tone-danger" data-row-delete="${esc(row.id)}" aria-label="Delete ${esc(jobKey(row))}" title="Delete" ${del?.enabled ? '' : 'disabled'}>${icon('<path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>')}</button></div>`;
                })}</div></section>`;
            bindHome(jobsEl);
            const filter = jobsEl.querySelector('#work-jobs-filter');
            filter.oninput = () => { jobsQuery = filter.value; renderJobsPage(); };
            if (focused === 'work-jobs-filter') { filter.focus(); filter.setSelectionRange(filter.value.length, filter.value.length); }
            jobsEl.querySelector('#work-jobs-needs').onchange = e => { jobsNeedsOnly = e.target.checked; renderJobsPage(); };
            jobsEl.querySelector('#work-jobs-session').onchange = e => { jobsSession = e.target.value; renderJobsPage(); };
            jobsEl.querySelectorAll('[data-row-archive]').forEach(b => { b.onclick = async () => {
                const row = home.jobs.find(j => j.id === b.dataset.rowArchive);
                if (!await ui.confirm(`Archive ${jobKey(row)}? It leaves the board; History keeps its record and evidence.`, {title:`Archive ${jobKey(row)}`, okLabel:'Archive'})) return;
                try { await api(`/api/jobs/${encodeURIComponent(row.id)}/archive`, {method:'POST', body:{}}); notify(`Archived ${jobKey(row)}`, 'ok'); await refreshHome(); } catch (e) { notify(e.message, 'danger'); }
            }; });
            jobsEl.querySelectorAll('[data-row-delete]').forEach(b => { b.onclick = () => runById(b.dataset.rowDelete, 'delete'); });
        }
        async function refreshHome() { home = await api('/api/jobs/vm/home'); renderHome(); return home; }

        /* ------------------------------------------------------------ job page */
        function shell() {
            detailEl.innerHTML = `<header class="work-detail-head" data-job-header></header>
                <section class="panel panel-body" aria-label="Pipeline" data-pipeline></section>
                <div data-environments></div>
                <div data-approval></div>
                <div class="work-action-panel" data-actions></div>
                <div><div role="tablist" aria-label="Job views" class="work-tabs">${tabs.map(t => `<button type="button" role="tab" class="work-tab" id="work-tab-${t}" data-tab="${t}" aria-selected="${t === tab}" aria-controls="work-panel-${t}" tabindex="${t === tab ? 0 : -1}">${labels[t]}<span data-tab-badge="${t}"></span></button>`).join('')}</div>
                ${tabs.map(t => `<div role="tabpanel" class="work-tabpanel" id="work-panel-${t}" aria-labelledby="work-tab-${t}" data-panel="${t}" tabindex="0" ${t === tab ? '' : 'hidden'}></div>`).join('')}</div>`;
            const list = detailEl.querySelector('[role=tablist]');
            detailEl.querySelectorAll('[data-tab]').forEach(b => { b.onclick = () => selectTab(b.dataset.tab); });
            list.addEventListener('keydown', e => {
                const i = tabs.indexOf(tab); let n = -1;
                if (e.key === 'ArrowRight') n = (i + 1) % tabs.length; else if (e.key === 'ArrowLeft') n = (i - 1 + tabs.length) % tabs.length;
                else if (e.key === 'Home') n = 0; else if (e.key === 'End') n = tabs.length - 1;
                if (n < 0) return; e.preventDefault(); selectTab(tabs[n]); list.querySelector(`[data-tab="${tabs[n]}"]`)?.focus();
            });
        }
        function renderState() {
            const steps = stepsOf(job);
            const models = Object.entries(job.stage_models || {}).filter(([, m]) => m?.model);
            const failed = job.state === 'failed';
            detailEl.querySelector('[data-job-header]').innerHTML = `
                <div class="row wrap work-detail-header-top"><h1 class="mono">${esc(jobKey(job))}</h1>${statePill(job.state)}
                    ${stateStatus(job.state) === 'active' ? pill('running', `<span class="status-icon active" aria-hidden="true">●</span> live · ${esc(stateLabel(job.state))}`) : ''}
                    ${job.paused_from ? pill('warn', `paused in ${esc(stateLabel(job.paused_from))}`) : ''}
                    ${job.fix_attempts ? pill('muted', `fix attempts ${job.fix_attempts}`) : ''}
                    ${job.verdict ? pill((VERDICT[job.verdict] || ['muted'])[0], esc((VERDICT[job.verdict] || [0, job.verdict.replaceAll('_', ' ')])[1])) : ''}
                    ${ENV ? ENV.headerPill(job.environments || job.build_environments, envOpen) : ''}
                    ${/^https?:\/\//.test(job.pr?.url || '') ? `<a class="pill tone-${job.pr.state === 'merged' ? 'ok' : job.pr.state === 'closed' ? 'danger' : 'accent'}" href="${esc(job.pr.url)}" target="_blank" rel="noopener" data-pr-link>PR${job.pr.number ? ` #${esc(job.pr.number)}` : ''}${job.pr.state && job.pr.state !== 'open' ? ` · ${esc(job.pr.state)}` : ''}</a>` : ''}
                    <span class="spacer"></span>${budgetText(job)}</div>
                <div class="work-detail-summary">${esc(job.title)}</div>
                <div class="row wrap small muted">
                    ${job.branch ? `<span class="row mono"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" aria-hidden="true"><circle cx="6" cy="6" r="3"/><circle cx="18" cy="18" r="3"/><path d="M6 9v12M18 9a9 9 0 0 1-9 9"/></svg><span class="sr-only">branch</span>${esc(job.branch)}</span>` : ''}
                    ${job.origin_session ? `<span>session: <code>${esc(job.origin_session)}</code></span>` : ''}
                    ${job.repository ? `<span>repo: <code>${esc(job.repository)}</code></span>` : ''}
                    ${failed && job.failed_from ? `<span class="tone-danger">failed in ${esc(stateLabel(job.failed_from))}</span>` : ''}
                    <span>agents: ${window.MuJobAgents ? window.MuJobAgents.stageText(job) : (models.length ? models.map(([s, m]) => `${esc(s)} <code>${esc(m.model)}</code>`).join(' · ') : 'unset')}</span>
                    <button type="button" class="link-btn small" data-stage-models>Job settings</button>
                </div>
                ${job.attention_detail && !(job.session?.approval && job.state === 'needs_input') && !(job.limit && job.state === 'needs_input') ? (failed ? `<div class="error-box" role="alert">${statusIcon('failed')} ${esc(job.attention_detail)}</div>` : `<div class="note-box row">${statusIcon('waiting')} ${esc(job.attention_detail)}</div>`) : ''}`;
            detailEl.querySelector('[data-stage-models]').onclick = () => window.muOpenStageModels?.();
            const envPill = detailEl.querySelector('[data-env-pill]');
            if (envPill) envPill.onclick = () => { envOpen = !envOpen; renderState(); };
            detailEl.querySelector('[data-pipeline]').innerHTML = pipelineStepper(steps);
            detailEl.querySelectorAll('[data-step]').forEach(g => { g.onclick = () => selectTab(stepTabs[g.dataset.step] || 'session'); });
            renderEnvironments();
            renderApproval();
            renderActions();
            renderBadges();
            registerCommands();
        }
        function budgetText(j) {
            const b = j.budget || {cost_usd:j.cost_usd, max_cost_usd:j.max_cost_usd};
            const cap = Number(b.max_cost_usd) > 0 ? Number(b.max_cost_usd) : null;
            const frac = cap ? Number(b.cost_usd || 0) / cap : 0;
            const tone = cap && frac >= 0.9 ? 'tone-danger' : cap && frac >= 0.7 ? 'tone-warn' : '';
            const active = Number(b.active_seconds);
            return `<span class="work-detail-budget row small" data-job-budget>
                <span class="work-detail-cost ${tone}" title="agent cost so far${cap ? ` of the ${money(cap)} job cap` : ' (no cost cap)'}"><span class="num">${money(b.cost_usd)}</span><span class="muted"> / ${cap ? money(cap) : 'no cap'}</span></span>
                ${Number.isFinite(active) && j.budget ? `<span class="muted num" title="worker-active time${b.max_runtime_seconds ? ` (limit ${duration(b.max_runtime_seconds * 1000)})` : ''}">⏱ ${duration(active * 1000)}${b.max_runtime_seconds ? ` / ${duration(b.max_runtime_seconds * 1000)}` : ''}</span>` : ''}</span>`;
        }
        function renderEnvironmentApproval(host) {
            const ea = job.environment_approval;
            const approve = job.actions.find(a => a.id === 'approveEnvironment'), reject = job.actions.find(a => a.id === 'rejectEnvironment');
            if (!ea || !approve?.enabled) return '';
            const kind = ea.kind === 'build' ? 'Dockerfile build' : ea.kind === 'egress' ? 'network egress' : 'image';
            return `<section class="work-approval work-env-approval" role="alert" aria-labelledby="work-env-approval-h" data-env-approval>
                <div class="row wrap"><span class="tone-warn" aria-hidden="true">⬢</span><h2 id="work-env-approval-h">${ea.kind === 'egress' ? 'The repository asks for network egress (stages run without it until approved)' : `The repository asks to ${ea.kind === 'build' ? 'build and use' : 'use'} a build environment`}</h2></div>
                <dl class="work-env-approval-facts small">
                    <dt>Kind</dt><dd>${esc(kind)}${ea.origin ? ` <span class="muted">(${esc(ea.origin === 'devcontainer' ? 'devcontainer.json' : '.mucli/jobs.toml')})</span>` : ''}</dd>
                    ${ea.label ? `<dt>What</dt><dd class="mono">${esc(ea.label)}</dd>` : ''}
                    ${ea.ref ? `<dt>${ea.kind === 'build' ? 'Tag' : ea.kind === 'egress' ? 'Domains' : 'Image'}</dt><dd class="mono">${esc(ea.ref)}</dd>` : ''}
                    <dt>Fingerprint</dt><dd class="mono">${esc(ea.fingerprint)}</dd>
                    ${(ea.ignored_features || []).length ? `<dt>Ignored</dt><dd>devcontainer features: ${esc(ea.ignored_features.join(', '))}</dd>` : ''}
                </dl>
                ${ea.message ? `<p class="muted small">${esc(ea.message)}</p>` : ''}
                <div class="row wrap">${actionButton({...approve, primary:true})}${reject ? actionButton({...reject, primary:false}) : ''}</div></section>`;
        }
        function renderLimitBanner() {
            const lim = job.limit;
            const raise = job.actions.find(a => a.id === 'raiseLimit'), cancel = job.actions.find(a => a.id === 'discard');
            if (!lim || job.state !== 'needs_input' || !raise?.enabled) return '';
            const input = raise.input || {};
            return `<section class="work-approval work-limit-approval" role="alert" aria-labelledby="work-limit-h" data-limit-approval>
                <div class="row wrap"><span class="tone-warn" aria-hidden="true">◆</span><h2 id="work-limit-h">${esc(lim.summary || 'Limit reached')}</h2></div>
                <p>${esc(lim.message || '')}</p>
                <div class="row wrap"><label class="small" for="work-limit-amount">${esc(input.label || 'Additional amount')}</label>
                    <input id="work-limit-amount" type="text" inputmode="decimal" class="work-limit-amount" data-limit-amount value="${esc(input.value || '')}" aria-describedby="work-limit-hint">
                    ${actionButton({...raise, primary:true})}${cancel ? actionButton({...cancel, primary:false}) : ''}</div>
                ${input.hint ? `<p id="work-limit-hint" class="muted small">${esc(input.hint)}</p>` : ''}</section>`;
        }
        function renderApproval() {
            const host = detailEl.querySelector('[data-approval]');
            const limitHtml = renderLimitBanner();
            if (limitHtml) {
                // Keep what the user typed across live re-renders of the same pause.
                const prev = host.querySelector('[data-limit-amount]');
                const key = `limit:${job.limit.kind}:${job.limit.scope}:${job.limit.stage}:${job.limit.limit}`;
                const typed = prev && host.dataset.eventId === key ? prev.value : null;
                host.dataset.eventId = key; host.innerHTML = limitHtml; bindCommands(host);
                const box = host.querySelector('[data-limit-amount]');
                if (typed !== null) box.value = typed;
                box.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); invoke('raiseLimit'); } });
                return;
            }
            const envHtml = renderEnvironmentApproval(host);
            // A non-blocking egress banner never hides a pending tool approval.
            if (envHtml && !(job.environment_approval?.blocking === false && job.session?.approval)) { host.dataset.eventId = ''; host.innerHTML = envHtml; bindCommands(host); return; }
            const ap = job.session?.approval;
            const approve = job.actions.find(a => a.id === 'approveTool'), deny = job.actions.find(a => a.id === 'denyTool');
            if (!ap || (!approve?.enabled && !deny?.enabled)) { host.innerHTML = ''; return; }
            // planos ApprovalBanner: Approve · optional reason for the agent · Deny. Keep a typed reason across re-renders.
            const prev = host.querySelector('[data-deny-reason]');
            const reason = prev && host.dataset.eventId === String(ap.event_id ?? '') ? prev.value : '';
            const hadFocus = prev && document.activeElement === prev;
            const reasonLabel = inputOf(deny || {id:'denyTool'})?.label || 'Reason for the agent (optional)';
            host.dataset.eventId = String(ap.event_id ?? '');
            host.innerHTML = `<section class="work-approval" role="alert" aria-labelledby="work-approval-h"><div class="row wrap"><span class="tone-warn" aria-hidden="true">◆</span><h2 id="work-approval-h">The agent asks to use <code>${esc(ap.tool_name || 'a tool')}</code></h2></div>
                <pre>${esc(typeof ap.tool_args === 'string' ? ap.tool_args : JSON.stringify(ap.tool_args || {}, null, 2))}</pre>${ap.reason || ap.detail ? `<p class="muted small">${esc(ap.reason || ap.detail)}</p>` : ''}
                <div class="row wrap">${approve ? actionButton({...approve, primary:true}) : ''}
                    ${deny ? `<label class="sr-only" for="work-deny-reason">${esc(reasonLabel)}</label><input id="work-deny-reason" type="text" class="grow work-deny-reason" maxlength="2000" data-deny-reason placeholder="Reason for the agent if you deny (optional)" value="${esc(reason)}" ${deny.enabled ? '' : 'disabled'}>${actionButton({...deny, primary:false})}` : ''}
                    ${approve?.enabled === false && approve?.reason ? `<span class="muted small">${esc(approve.reason)}</span>` : ''}</div></section>`;
            bindCommands(host);
            const box = host.querySelector('[data-deny-reason]');
            if (box) {
                if (hadFocus) { box.focus(); box.setSelectionRange(box.value.length, box.value.length); }
                box.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); invoke('denyTool'); } });
            }
        }
        function renderActions() {
            const bar = detailEl.querySelector('[data-actions]');
            // Tool approval lives in the approval banner; hidden actions (markFindings, markViewed) never show.
            const inLimitBanner = !!(job.limit && job.state === 'needs_input');
            const list = job.actions.filter(a => !hiddenOf(a) && !BANNER_ACTIONS.has(a.id) && !(inLimitBanner && LIMIT_BANNER_ACTIONS.has(a.id)));
            const enabled = [...list.filter(a => a.enabled && a.primary), ...list.filter(a => a.enabled && !a.primary)];
            const disabled = list.filter(a => !a.enabled);
            bar.innerHTML = `<div class="row wrap" role="toolbar" aria-label="Job actions">${enabled.map(a => actionButton(a)).join('') || '<span class="muted small">Nothing to do right now.</span>'}
                ${disabled.length ? `<button type="button" class="btn ghost sm" aria-expanded="${showUnavailable}" aria-controls="work-unavailable" data-toggle-unavailable>${showUnavailable ? '▴ fewer' : `▾ ${disabled.length} unavailable`}</button>` : ''}</div>
                ${showUnavailable && disabled.length ? `<ul id="work-unavailable" class="plain col" aria-label="Unavailable actions" style="gap:var(--s-half)">${disabled.map(a => `<li class="row">${actionButton(a, {size:'sm'})}<span class="muted small">${esc(a.reason || '')}</span></li>`).join('')}</ul>` : ''}`;
            bindCommands(bar);
            bar.querySelector('[data-toggle-unavailable]')?.addEventListener('click', () => { showUnavailable = !showUnavailable; renderActions(); });
        }
        function renderBadges() {
            const set = (t, html) => { const el = detailEl.querySelector(`[data-tab-badge="${t}"]`); if (el) el.innerHTML = html ? ` ${html}` : ''; };
            const live = stateStatus(job.state) === 'active' && ['planning', 'implementing', 'reviewing'].includes(job.state);
            set('session', live ? '<span class="tone-running status-icon active" aria-label="live">●</span>' : job.session?.approval ? '<span class="tone-warn" aria-label="approval needed">◆</span>' : '');
            set('plan', job.plan?.draft ? '<span class="tone-warn" aria-label="unsaved draft">◆</span>' : job.state === 'plan_review' ? '<span class="tone-warn" aria-label="awaiting approval">◆</span>' : '');
            if (job.diff) { const files = parsePatch(diffPatch(job)); const a = files.reduce((n, f) => n + f.added, 0), r = files.reduce((n, f) => n + f.removed, 0);
                set('diff', files.length ? `<span class="small">${job.diff.live ? '<span class="tone-running status-icon active" aria-label="live">●</span> ' : ''}<span class="tone-ok">+${a}</span> <span class="tone-danger">−${r}</span></span>` : ''); }
            if (job.review) { const open = (job.review.findings || []).filter(f => (f.disposition || 'open') === 'open');
                const worst = open.some(f => SEVERITY[f.severity] === 'danger') ? 'danger' : open.some(f => f.severity === 'minor') ? 'warn' : 'muted';
                set('review', `<span class="badge tone-${worst}" aria-label="${open.length} open findings">${open.length}</span>`); }
            if (job.checks) { const last = (job.checks.runs || []).slice(-1)[0];
                set('checks', job.state === 'checking' ? '<span class="tone-running status-icon active" aria-label="running">●</span>' : last ? (last.passed ? '<span class="tone-ok" aria-label="passed">✓</span>' : '<span class="tone-danger" aria-label="failed">✗</span>') : ''); }
            set('diagnostics', job.state === 'failed' ? '<span class="badge tone-danger" aria-label="failed">!</span>' : '');
        }
        function registerCommands() {
            ui.register('page:job', [
                ...job.actions.filter(a => !hiddenOf(a) || ENV_ACTIONS.has(a.id)).map(a => ({id:`act:${a.id}`, label:LABEL[a.id] || a.label, group:'Job', key:a.shortcut || undefined, enabled:a.enabled, reason:a.reason, run:() => invoke(a.id)})),
                ...(ENV ? [{id:'env:panel', label:envOpen ? 'Hide build environments' : 'Show build environments', group:'Job', run:() => { envOpen = !envOpen; renderState(); }}] : []),
                ...tabs.map(t => ({id:`tab:${t}`, label:`Show ${labels[t].toLowerCase()}`, group:'Tab', run:() => selectTab(t)})),
            ], 10);
        }

        /* ------------------------------------------------------------ plan */
        function renderPlan(panel) {
            if (editor?.isDirty()) return;
            editor?.destroy(); editor = null;
            const plan = job.plan || {};
            const editAction = job.actions.find(a => a.id === 'savePlanDraft');
            const latest = plan.version === plan.latest_version;
            if (plan.error) { panel.innerHTML = `<div class="error-box" role="alert">${statusIcon('failed')} ${esc(plan.error)}</div>`; return; }
            if (!plan.markdown && !plan.draft && !editing) { panel.innerHTML = '<p class="empty">No plan yet.</p>'; return; }
            const versions = (plan.versions || []).length > 1 ? `<label class="row small"><span class="muted">Version</span><select data-plan-version aria-label="Plan version">${plan.versions.map(v => `<option value="${v}" ${v === plan.version ? 'selected' : ''}>v${v}${v === plan.approved_version ? ' · approved' : ''}${v === plan.latest_version ? ' · latest' : ''}</option>`).join('')}</select></label>` : `<span class="muted small">v${esc(plan.version || 1)}${plan.approved_version === plan.version ? ' · approved' : ''}</span>`;
            if (editing && editAction?.enabled && latest) {
                panel.innerHTML = '<div data-editor></div>';
                editor = window.MuJobPlan.mount(panel.querySelector('[data-editor]'), plan, job.actions, send, async () => { editing = false; signatures.delete('plan'); await refreshParts(['plan', 'state']); },
                    { onDirty: () => renderActions(), onCancel: () => { editing = false; editor?.destroy(); editor = null; signatures.delete('plan'); renderActions(); renderPart('plan', true); } });
                return;
            }
            editing = false;
            panel.innerHTML = `<div class="col">
                <div class="row wrap">${versions}
                    ${plan.draft ? '<span class="tone-warn small">◆ unsaved draft — edit the plan to continue or save it</span>' : job.state === 'plan_review' ? '<span class="tone-warn small">◆ waiting for your approval</span>' : ''}
                    <span class="spacer"></span>
                    ${editAction?.enabled && latest ? `<button type="button" class="btn" data-plan-edit><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg> Edit plan${editAction.shortcut ? `<kbd aria-hidden="true">${esc(editAction.shortcut)}</kbd>` : ''}</button>` : ''}
                </div>
                ${plan.diff_from_previous ? `<details><summary class="small muted">Changes from the previous version</summary><pre class="work-log">${esc(plan.diff_from_previous)}</pre></details>` : ''}
                ${(plan.errors || []).length ? `<div class="note-box small">${plan.errors.map(esc).join('<br>')}</div>` : ''}
                <article class="panel panel-body work-doc md">${markdown(plan.markdown || plan.draft || '')}</article></div>`;
            panel.querySelector('[data-plan-edit]')?.addEventListener('click', () => invoke('savePlanDraft'));
            panel.querySelector('[data-plan-version]')?.addEventListener('change', async e => {
                const response = await api(`/api/jobs/vm/job/${encodeURIComponent(selectedId)}?parts=plan&plan_version=${e.target.value}`);
                job.plan = response.plan; renderPart('plan', true);
            });
        }

        /* ------------------------------------------------------------ review */
        function renderReview(panel) {
            const report = job.review;
            if (!report) { panel.innerHTML = '<p class="empty">No review yet. The reviewer reports here after checks pass.</p>'; return; }
            const fix = job.actions.find(a => a.id === 'reprompt'), mark = job.actions.find(a => a.id === 'markFindings');
            const findings = report.findings || [];
            const [vTone, vLabel] = VERDICT[report.verdict] || ['muted', String(report.verdict || '').replaceAll('_', ' ')];
            const readiness = Number(report.merge_readiness || 0);
            const files = new Set(parsePatch(job.diff?.patch).map(f => f.path));
            panel.innerHTML = `<div class="stack">
                <div class="row wrap">${pill(vTone, esc(vLabel))}<span class="work-readiness small"><span class="muted">merge readiness</span>${resultBar(readiness, 100 - readiness)}<strong class="num">${readiness}/100</strong></span>
                    <span class="muted small">${Object.entries(report.counts || {}).map(([s, c]) => `${c} ${esc(s)}`).join(' · ')}</span></div>
                ${findings.length ? `<div class="row wrap" role="toolbar" aria-label="Fix review findings"><span class="muted small grow">Pick findings to fix in one targeted implement run (checks and review follow); the plan stays as it is.</span>
                    <button type="button" class="btn primary sm" data-fix-selected ${fix?.enabled && selectedFindings.size ? '' : 'disabled'} title="${esc(fix?.enabled ? 'Fix the selected findings' : fix?.reason || 'not available')}">Fix ${selectedFindings.size || ''} selected${fix?.shortcut ? `<kbd aria-hidden="true">${esc(fix.shortcut)}</kbd>` : ''}</button></div>` : ''}
                <div class="grid-2">
                    <section class="panel" aria-labelledby="rv-sum"><header><h2 id="rv-sum">Summary</h2></header><div class="panel-body md">${markdown(report.summary || '')}</div></section>
                    <section class="panel" aria-labelledby="rv-adh"><header><h2 id="rv-adh">Plan adherence</h2></header><div class="panel-body">${(report.plan_coverage || []).length ? `<ul class="plain col">${report.plan_coverage.map(c => `<li class="row" style="align-items:flex-start">${statusIcon(COVERAGE[c.status] || 'pending', c.status)}<span class="mono small nowrap">${esc(c.criterion_id)}</span><span class="grow small">${esc(c.evidence || c.status)}</span></li>`).join('')}</ul>` : '<p class="empty">No acceptance criteria were assessed.</p>'}</div></section>
                </div>
                ${(report.out_of_scope_changes || []).length ? `<div class="note-box"><strong class="small">Out-of-scope changes</strong><ul class="small">${report.out_of_scope_changes.map(x => `<li>${esc(typeof x === 'string' ? x : JSON.stringify(x))}</li>`).join('')}</ul></div>` : ''}
                ${findings.length ? '' : `<p class="empty">${statusIcon('done')} No findings.</p>`}
                ${['blocker', 'major', 'minor', 'nit'].map(sev => { const list = findings.filter(f => f.severity === sev); if (!list.length) return '';
                    return `<section aria-labelledby="sev-${sev}"><h2 id="sev-${sev}" class="tone-${SEVERITY[sev]}" style="margin-bottom:var(--s-1);text-transform:capitalize">${sev} <span class="badge">${list.length}</span></h2><ul class="plain col">${list.map(f => {
                        const closed = (f.disposition || 'open') !== 'open';
                        return `<li class="work-finding-card fc_${SEVERITY[f.severity]} ${closed ? 'is-closed' : ''}" id="finding-${esc(f.id)}" data-severity="${esc(f.severity)}">
                            <div class="row wrap"><input type="checkbox" data-finding="${esc(f.id)}" aria-label="select “${esc(f.title)}” for fixing" ${selectedFindings.has(f.id) ? 'checked' : ''} ${closed ? 'disabled' : ''}>${pill(SEVERITY[f.severity], esc(f.severity))}<strong class="grow">${esc(f.title)}</strong>
                                ${f.file && files.has(f.file) ? `<button type="button" class="btn sm" data-anchor="${esc(f.id)}">◎ Jump to diff</button>` : '<span class="muted small" title="this file is not part of the current diff">not in diff</span>'}
                                <button type="button" class="btn sm" data-fix-one="${esc(f.id)}" ${fix?.enabled && !closed ? '' : 'disabled'} title="${esc(fix?.enabled ? 'Fix only this finding' : fix?.reason || 'not available')}">Fix</button>
                                <select data-disposition="${esc(f.id)}" aria-label="Disposition of ${esc(f.title)}" ${mark?.enabled ? '' : 'disabled'}>${['open', 'accepted', 'dismissed', 'fixed'].map(d => `<option ${d === (f.disposition || 'open') ? 'selected' : ''}>${d}</option>`).join('')}</select></div>
                            <div class="mono small muted">${esc(f.file || 'no file')}${f.line_start ? `:${f.line_start}` : ''}${f.category ? ` · ${esc(f.category)}` : ''}${f.confidence != null ? ` · confidence ${esc(f.confidence)}` : ''}</div>
                            ${f.detail ? `<div class="md">${markdown(f.detail)}</div>` : ''}${f.suggestion ? `<div><span class="muted small">Suggestion</span><div class="md">${markdown(f.suggestion)}</div></div>` : ''}</li>`; }).join('')}</ul></section>`; }).join('')}</div>`;
            panel.querySelectorAll('[data-finding]').forEach(c => { c.onchange = () => { c.checked ? selectedFindings.add(c.dataset.finding) : selectedFindings.delete(c.dataset.finding); renderPart('review', true); }; });
            panel.querySelector('[data-fix-selected]')?.addEventListener('click', () => invoke('reprompt'));
            panel.querySelectorAll('[data-fix-one]').forEach(b => { b.onclick = () => { selectedFindings.clear(); selectedFindings.add(b.dataset.fixOne); invoke('reprompt'); }; });
            panel.querySelectorAll('[data-disposition]').forEach(c => { c.onchange = async () => { try { await send('markFindings', {ids:[c.dataset.disposition], disposition:c.value}); await refreshParts(['review', 'state']); } catch (e) { notify(e.message, 'danger'); } }; });
            panel.querySelectorAll('[data-anchor]').forEach(b => { b.onclick = () => jumpToDiff(findings.find(x => x.id === b.dataset.anchor)); });
        }
        function jumpToDiff(f) {
            if (!f) return;
            collapsed.delete(f.file);
            selectTab('diff');
            setTimeout(() => {
                const row = detailEl.querySelector(`[data-line-key="${CSS.escape((f.file || '') + ':' + f.line_start)}"]`) || detailEl.querySelector(`[data-file="${CSS.escape(f.file || '')}"]`);
                row?.scrollIntoView({block:'center'}); row?.classList.add('work-flash'); setTimeout(() => row?.classList.remove('work-flash'), 1600);
            }, 0);
        }

        /* ------------------------------------------------------------ diff */
        function renderDiff(panel) {
            const live = job.diff?.live || null;
            const files = parsePatch(diffPatch(job));
            const liveNote = live ? `<div class="note-box row wrap small work-live-diff" role="status" data-live-diff><span class="tone-running status-icon active" aria-hidden="true">●</span><strong>Live</strong>
                <span>working tree vs base <code>${esc(String(live.base_sha || '').slice(0, 12))}</code>, refreshed every few seconds while the job runs</span>
                ${live.untracked_total ? `<span class="muted">· ${live.untracked_total} new file${live.untracked_total === 1 ? '' : 's'}</span>` : ''}
                ${live.truncated ? '<span class="tone-warn">· truncated (large diff)</span>' : ''}<span class="spacer"></span><span class="muted">${esc(when(live.generated_at))}</span></div>` : '';
            if (!files.length) { panel.innerHTML = `${liveNote}<p class="empty">${live ? 'No changes in the working tree yet.' : 'No diff yet. The implementer’s changes appear here.'}</p>`; return; }
            const seen = live ? new Set() : viewed();
            const findings = (job.review?.findings || []).filter(f => (f.disposition || 'open') === 'open');
            const fix = job.actions.find(a => a.id === 'reprompt');
            const totalA = files.reduce((n, f) => n + f.added, 0), totalR = files.reduce((n, f) => n + f.removed, 0);
            const cbar = f => { const total = f.added + f.removed, blocks = 5, a = total ? Math.round((f.added / total) * blocks) : 0, r = total ? Math.min(blocks - a, Math.max(f.removed ? 1 : 0, blocks - a)) : 0;
                return `<span class="work-cbar" aria-hidden="true">${'<span class="c-add"></span>'.repeat(a)}${'<span class="c-del"></span>'.repeat(r)}${'<span class="c-none"></span>'.repeat(Math.max(0, blocks - a - r))}</span>`; };
            const comment = f => `<div class="work-diff-finding sev_${SEVERITY[f.severity]}" role="note" aria-label="Review finding: ${esc(f.severity)}: ${esc(f.title)}"><div class="row"><span aria-hidden="true" class="tone-${SEVERITY[f.severity]}">⚑</span>${pill(SEVERITY[f.severity], esc(f.severity))}<strong class="grow">${esc(f.title)}</strong>
                <button type="button" class="btn sm" data-fix-one="${esc(f.id)}" ${fix?.enabled ? '' : 'disabled'} title="${esc(fix?.enabled ? 'Run a targeted implement pass that fixes only this finding' : fix?.reason || 'not available')}">Fix this</button></div>${f.detail ? `<p>${esc(f.detail)}</p>` : ''}${f.suggestion ? `<p><span class="muted">Suggestion: </span>${esc(f.suggestion)}</p>` : ''}</div>`;
            const cols = splitDiff ? 6 : 4;
            const fileBody = f => {
                if (f.binary) return '<p class="empty">Binary file.</p>';
                const at = new Map();
                findings.filter(x => x.file === f.path).forEach(x => { const k = Number(x.line_start || 0); at.set(k, [...(at.get(k) || []), x]); });
                const big = f.lines.length > 4000;
                const code = l => big ? esc(l.text) : highlight(l.text, f.path);
                const findingRow = no => (at.get(no) || []).length ? `<tr class="diff-finding-row"><td colspan="${cols}">${at.get(no).map(comment).join('')}</td></tr>` : '';
                let rows = '';
                if (!splitDiff) {
                    for (const l of f.lines) {
                        if (l.kind === 'hunk') { rows += `<tr class="hunk"><td colspan="4">${esc(l.text)}</td></tr>`; continue; }
                        if (l.kind === 'note') { rows += `<tr class="note"><td colspan="4" class="muted">${esc(l.text)}</td></tr>`; continue; }
                        const cls = l.kind === 'add' ? 'add' : l.kind === 'del' ? 'del' : '';
                        rows += `<tr class="${cls}" data-line-key="${esc(f.path + ':' + (l.newNo ?? ''))}"><td class="no">${l.oldNo ?? ''}</td><td class="no">${l.newNo ?? ''}</td><td class="sign ${cls}">${l.kind === 'add' ? '+' : l.kind === 'del' ? '−' : ' '}<span class="sr-only">${l.kind === 'add' ? 'added' : l.kind === 'del' ? 'removed' : ''}</span></td><td class="code ${cls}">${code(l)}</td></tr>`;
                        if (l.kind !== 'del') rows += findingRow(l.newNo);
                    }
                } else {
                    let i = 0;
                    while (i < f.lines.length) {
                        const l = f.lines[i];
                        if (l.kind === 'hunk' || l.kind === 'note') { rows += `<tr class="${l.kind}"><td colspan="6">${esc(l.text)}</td></tr>`; i++; continue; }
                        if (l.kind === 'ctx') { rows += `<tr data-line-key="${esc(f.path + ':' + l.newNo)}"><td class="no">${l.oldNo}</td><td class="sign"></td><td class="code half">${code(l)}</td><td class="no">${l.newNo}</td><td class="sign"></td><td class="code half">${code(l)}</td></tr>${findingRow(l.newNo)}`; i++; continue; }
                        const dels = [], adds = [];
                        while (f.lines[i]?.kind === 'del') dels.push(f.lines[i++]);
                        while (f.lines[i]?.kind === 'add') adds.push(f.lines[i++]);
                        for (let k = 0; k < Math.max(dels.length, adds.length); k++) {
                            const d = dels[k], a = adds[k];
                            rows += `<tr data-line-key="${esc(f.path + ':' + (a?.newNo ?? ''))}">${d ? `<td class="no del">${d.oldNo}</td><td class="sign del">−</td><td class="code half del">${code(d)}</td>` : '<td class="no blank"></td><td class="blank"></td><td class="blank half"></td>'}${a ? `<td class="no add">${a.newNo}</td><td class="sign add">+</td><td class="code half add">${code(a)}</td>` : '<td class="no blank"></td><td class="blank"></td><td class="blank half"></td>'}</tr>${a ? findingRow(a.newNo) : ''}`;
                        }
                    }
                }
                return `<div class="work-diff-body"><table class="work-diff-table ${splitDiff ? 'is-split' : ''}"><tbody>${rows}</tbody></table></div>`;
            };
            panel.innerHTML = `${liveNote}<div class="work-diff-layout">
                <nav class="work-diff-tree" aria-label="Changed files"><div class="work-diff-tree-head col" style="gap:var(--s-half)">
                    <div class="row small"><strong>${files.length} file${files.length === 1 ? '' : 's'}</strong><span class="tone-ok">+${totalA}</span><span class="tone-danger">−${totalR}</span><span class="spacer"></span>${live ? '' : `<span class="muted">${files.filter(f => seen.has(f.path)).length}/${files.length} viewed</span>`}</div>
                    <div class="work-segmented" role="group" aria-label="Diff layout"><button type="button" data-diff-mode="unified" aria-pressed="${!splitDiff}">Unified</button><button type="button" data-diff-mode="split" aria-pressed="${splitDiff}">Split</button></div></div>
                    <ul class="plain">${files.map((f, i) => `<li class="work-tree-item ${seen.has(f.path) ? 'is-viewed' : ''}"><button type="button" class="work-tree-link" data-goto-file="${i}" title="${esc(f.path)}"><span class="work-st st_${f.status}" aria-label="${{A:'added', M:'modified', D:'deleted', R:'renamed'}[f.status]}">${f.status}</span><span class="ellipsis">${esc(f.path.split('/').pop())}</span></button>${cbar(f)}</li>`).join('')}</ul></nav>
                <div class="work-diff-files">${files.map((f, i) => { const isViewed = seen.has(f.path), isCollapsed = collapsed.has(f.path) || (isViewed && !collapsed.has(`!${f.path}`));
                    return `<section class="work-diff-file" id="diff-file-${i}" data-file="${esc(f.path)}"><header class="work-diff-file-head">
                        <button type="button" class="link-btn row" data-collapse="${esc(f.path)}" aria-expanded="${!isCollapsed}"><span aria-hidden="true">${isCollapsed ? '▸' : '▾'}</span><span class="work-st st_${f.status}">${f.status}</span><span class="mono small ellipsis">${esc(f.path)}</span></button>
                        <span class="small"><span class="tone-ok">+${f.added}</span> <span class="tone-danger">−${f.removed}</span></span><span class="spacer"></span>
                        ${findings.some(x => x.file === f.path) ? `<span class="tone-warn small" title="review findings in this file">⚑ ${findings.filter(x => x.file === f.path).length}</span>` : ''}
                        ${live ? '' : `<label class="check small"><input type="checkbox" data-viewed="${esc(f.path)}" ${isViewed ? 'checked' : ''}> Viewed</label>`}</header>
                        ${isCollapsed ? '' : fileBody(f)}</section>`; }).join('')}</div></div>`;
            panel.querySelectorAll('[data-diff-mode]').forEach(b => { b.onclick = () => { splitDiff = b.dataset.diffMode === 'split'; localStorage.setItem('mucli-jobs-diff', splitDiff ? 'split' : 'unified'); renderPart('diff', true); }; });
            panel.querySelectorAll('[data-goto-file]').forEach(b => { b.onclick = () => { const f = files[Number(b.dataset.gotoFile)]; collapsed.delete(f.path); collapsed.add(`!${f.path}`); renderPart('diff', true); panel.querySelector(`#diff-file-${b.dataset.gotoFile}`)?.scrollIntoView({block:'start'}); }; });
            panel.querySelectorAll('[data-collapse]').forEach(b => { b.onclick = () => { const p = b.dataset.collapse, open = b.getAttribute('aria-expanded') === 'true';
                collapsed.delete(p); collapsed.delete(`!${p}`); if (open) collapsed.add(p); else collapsed.add(`!${p}`); renderPart('diff', true); }; });
            panel.querySelectorAll('[data-viewed]').forEach(c => { c.onchange = () => { collapsed.delete(`!${c.dataset.viewed}`); collapsed.delete(c.dataset.viewed); setViewed(c.dataset.viewed, c.checked); }; });
            panel.querySelectorAll('[data-fix-one]').forEach(b => { b.onclick = () => { selectedFindings.clear(); selectedFindings.add(b.dataset.fixOne); invoke('reprompt'); }; });
        }

        /* ------------------------------------------------------------ checks (planos ChecksTab) */
        function suiteCounts(s) { const j = s.junit; return j ? {passed:j.passed || 0, failed:j.failed || 0, skipped:j.skipped || 0, total:j.total || 0} : {passed:s.passed ? 1 : 0, failed:s.passed ? 0 : 1, skipped:0, total:1}; }
        const runDuration = r => Number.isFinite(Number(r.duration_ms)) && r.duration_ms != null ? Number(r.duration_ms)
            : (r.suites || []).some(s => s.duration_ms != null) ? (r.suites || []).reduce((n, s) => n + Number(s.duration_ms || 0), 0) : null;
        /* failed test cases of a run: the VM's failed_cases for the latest run, else derived from JUnit cases */
        function failedCases(run, latest) {
            const fromVm = (job.checks?.failed_cases || []).filter(c => c.run == null || c.run === run.run);
            if (run.run === latest?.run && (job.checks?.failed_cases || []).length) return fromVm;
            const pre = new Set((run.suites || []).flatMap(s => (s.preexisting_failures || []).map(String)));
            return (run.suites || []).flatMap(s => (s.junit?.cases || []).filter(c => /fail|error/.test(String(c.status))).map(c => ({
                run:run.run, suite:s.suite, classname:c.suite || '', name:c.name || c.id, id:c.id, message:c.message || '', new:!pre.has(String(c.id)),
            })));
        }
        /* duration history per suite: the VM's checks.history, else rebuilt from the runs we have */
        function suiteHistory(suite) {
            const h = job.checks?.history?.[suite];
            if (Array.isArray(h)) return h.slice(-20);
            return (job.checks?.runs || []).map(r => ({r, s:(r.suites || []).find(x => x.suite === suite)})).filter(x => x.s && x.s.duration_ms != null)
                .map(({r, s}) => ({run:r.run, passed:!!s.passed, duration_ms:Number(s.duration_ms), finished_at:r.started_at})).slice(-20);
        }
        /* hand-written SVG bar chart (planos Chart with bars: passed = ok, failed = danger) */
        function durationChart(suite, points, selectedNo) {
            const W = 640, H = 150, L = 52, B = 22, T = 8, max = Math.max(1, ...points.map(p => Number(p.duration_ms || 0)));
            const slot = (W - L - 8) / points.length, bw = Math.min(40, Math.max(4, slot * 0.6));
            const y = v => T + (H - T - B) * (1 - v / max);
            const ticks = [0, 0.5, 1].map(f => f * max);
            const summary = points.map(p => `run ${p.run}: ${duration(p.duration_ms)} ${p.passed ? 'passed' : 'failed'}`).join(', ');
            return `<section aria-labelledby="dur-h-${esc(suite)}" class="col work-duration"><h3 id="dur-h-${esc(suite)}">Duration history · ${esc(suite)}</h3>
                <svg class="work-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMinYMid meet" role="img" aria-label="${esc(`Duration history for ${suite}: ${summary}`)}">
                ${ticks.map(t => `<line x1="${L}" x2="${W - 4}" y1="${y(t)}" y2="${y(t)}" class="grid"/><text x="${L - 6}" y="${y(t) + 4}" text-anchor="end" class="axis">${esc(t < 120000 ? `${Math.round(t / 1000)}s` : duration(t))}</text>`).join('')}
                ${points.map((p, i) => { const cx = L + slot * i + slot / 2, top = y(Number(p.duration_ms || 0));
                    return `<g><title>${esc(`Run ${p.run} · ${duration(p.duration_ms)} · ${p.passed ? 'passed' : 'failed'}`)}</title><rect x="${cx - bw / 2}" y="${top}" width="${bw}" height="${Math.max(1, H - B - top)}" rx="2" class="${p.passed ? 'bar-ok' : 'bar-danger'} ${p.run === selectedNo ? 'is-selected' : ''}"/>
                        <text x="${cx}" y="${H - 6}" text-anchor="middle" class="axis">#${esc(p.run)}</text></g>`; }).join('')}
                </svg><div class="row small muted"><span><span class="tone-ok" aria-hidden="true">■</span> passed</span><span><span class="tone-danger" aria-hidden="true">■</span> failed</span></div></section>`;
        }
        let failureFilter = '';
        function renderChecks(panel) {
            const runs = (job.checks?.runs || []).slice().reverse(); // newest first
            const recheck = job.actions.find(a => a.id === 'recheck');
            const live = job.checks?.live || [];
            const running = job.state === 'checking';
            const latest = runs[0];
            const selected = runs.find(r => r.run === selectedRun) || runs[0];
            const total = r => (r.suites || []).map(suiteCounts).reduce((a, c) => ({passed:a.passed + c.passed, failed:a.failed + c.failed, skipped:a.skipped + c.skipped, total:a.total + c.total}), {passed:0, failed:0, skipped:0, total:0});
            const runPill = r => r.passed ? pill('ok', '<span aria-hidden="true">✓</span>passed') : pill('danger', '<span aria-hidden="true">✗</span>failed');
            const started = r => r.started_at ? when(r.started_at).slice(5, 16) : '';
            const dur = r => { const ms = runDuration(r); return ms == null ? '' : duration(ms); };
            const liveLog = live.length && running ? `<section class="panel" aria-labelledby="live-h"><header><h2 id="live-h"><span class="tone-running status-icon active" aria-hidden="true">●</span> Live log</h2><span class="spacer"></span><span class="small muted">running now</span></header><div class="panel-body"><pre class="work-log" data-live-log role="log" aria-live="off">${ansi(live.map(e => `[${e.payload?.suite || 'check'}${e.payload?.baseline ? ' · baseline' : ''}] ${e.payload?.text || ''}`).join(''))}</pre></div></section>` : '';
            panel.innerHTML = `<div class="stack">
                <div class="row wrap"><div class="col" style="gap:2px"><span>${running ? '<span class="tone-running"><span class="status-icon active" aria-hidden="true">●</span> running · </span>' : ''}${latest ? `last: run ${latest.run} ${latest.passed ? 'passed' : 'failed'}` : 'No checks have run yet.'}</span>
                    <span class="small muted">fix attempts ${esc(job.fix_attempts || 0)} · checks come from the approved plan</span></div><span class="spacer"></span>
                    <div class="row wrap" role="toolbar" aria-label="Check actions">${recheck ? actionButton(recheck) : ''}</div></div>
                ${liveLog}
                ${runs.length ? `<div class="table-wrap panel"><table class="table" aria-label="Runs"><thead><tr><th scope="col">Run</th><th scope="col">Status</th><th scope="col">Result</th><th scope="col">Trigger</th><th scope="col" class="num">Duration</th><th scope="col">Started</th></tr></thead><tbody>${runs.map(r => { const k = total(r);
                    return `<tr class="clickable" aria-selected="${r === selected}" data-run="${esc(r.run)}"><td><button type="button" class="link-btn" aria-pressed="${r === selected}">Run ${esc(r.run)}</button></td><td>${runPill(r)}</td>
                        <td style="min-width:160px">${k.total ? `<div class="row">${resultBar(k.passed, k.failed, k.skipped)}<span class="small nowrap">${k.passed}✓ ${k.failed ? `<span class="tone-danger">${k.failed}✗</span>` : ''}</span></div>` : ''}</td>
                        <td class="muted small">${esc(r.trigger || '')}</td><td class="num">${esc(dur(r))}</td><td class="muted small nowrap">${esc(started(r))}</td></tr>`; }).join('')}</tbody></table></div>` : '<p class="empty">No builds or test runs yet.</p>'}
                ${selected ? (() => { const k = total(selected), cases = failedCases(selected, latest);
                    const q = failureFilter.toLowerCase(), rows = cases.filter(f => !q || `${f.suite} ${f.name} ${f.message}`.toLowerCase().includes(q));
                    const hasJunit = (selected.suites || []).some(s => s.junit);
                    const suitesWithHistory = (selected.suites || []).map(s => [s.suite, suiteHistory(s.suite)]).filter(([, h]) => h.length >= 2);
                    return `<section class="panel" aria-labelledby="run-h"><header><h2 id="run-h">Run ${esc(selected.run)}</h2>${runPill(selected)}<span class="spacer"></span><span class="small muted">${esc([dur(selected), selected.trigger, selected.head_sha ? String(selected.head_sha).slice(0, 12) : ''].filter(Boolean).join(' · '))}</span></header>
                    <div class="panel-body stack">
                        ${k.total ? `<div class="col">${resultBar(k.passed, k.failed, k.skipped, true)}<div class="row small"><span class="tone-ok">✓ ${k.passed} passed</span><span class="${k.failed ? 'tone-danger' : 'muted'}">✗ ${k.failed} failed</span>${k.skipped ? `<span class="muted">– ${k.skipped} skipped</span>` : ''}<span class="muted">of ${k.total}</span></div></div>` : ''}
                        ${(selected.suites || []).length ? `<div class="table-wrap"><table class="table work-suites" aria-label="Suites of run ${esc(selected.run)}"><thead><tr><th scope="col">Suite</th><th scope="col">Environment</th><th scope="col">Result</th><th scope="col" class="num">Duration</th></tr></thead><tbody>
                            ${selected.suites.map(s => `<tr><td class="mono small">${statusIcon(s.passed ? 'done' : 'failed')} ${esc(s.suite)}</td><td class="mono small" data-suite-env="${esc(s.environment || '')}">${s.environment ? `<span aria-hidden="true">⬢</span> ${esc(s.environment)}` : '<span class="muted">shared runtime</span>'}</td><td class="small">${s.missing_executable ? `<span class="tone-warn">${esc(s.missing_executable)} missing</span>` : s.passed ? '<span class="tone-ok">passed</span>' : '<span class="tone-danger">failed</span>'}</td><td class="num small muted">${s.duration_ms != null ? esc(duration(s.duration_ms)) : ''}</td></tr>`).join('')}</tbody></table></div>` : ''}
                        ${cases.length ? `<section aria-labelledby="jf-h" class="col"><div class="row wrap"><h3 id="jf-h">Failed test cases <span class="badge tone-danger">${cases.length}</span></h3><span class="spacer"></span>
                            <input type="search" data-failure-filter aria-label="Filter failed tests" placeholder="Filter failures…" value="${esc(failureFilter)}"></div>
                            <div class="table-wrap"><table class="table work-failures"><thead><tr><th scope="col">Suite</th><th scope="col">Test</th><th scope="col">Message</th></tr></thead><tbody>
                            ${rows.map(f => `<tr><td class="mono small">${esc(f.suite || '')}</td><td class="mono small"><span class="tone-danger" aria-hidden="true">✗ </span>${f.classname && f.classname !== f.suite ? `<span class="muted">${esc(f.classname)}.</span>` : ''}${esc(f.name || f.id || '')}${f.new === false ? ` ${pill('muted', 'pre-existing', 'also fails on the base commit')}` : f.new ? ` ${pill('danger', 'new')}` : ''}</td><td><pre class="small work-failure-msg">${esc(f.message || '')}</pre></td></tr>`).join('') || '<tr><td colspan="3" class="empty">No failure matches the filter.</td></tr>'}
                            </tbody></table></div></section>`
                            : hasJunit && !k.failed ? `<p class="tone-ok">${statusIcon('done')} No failing test cases.</p>` : ''}
                        ${!cases.length && Array.isArray(selected.new_failures) && selected.new_failures.length ? `<section><h3>New failures <span class="badge tone-danger">${selected.new_failures.length}</span></h3><ul class="mono small">${selected.new_failures.map(f => `<li class="tone-danger">✗ ${esc(typeof f === 'string' ? f : JSON.stringify(f))}</li>`).join('')}</ul></section>` : ''}
                        ${Array.isArray(selected.preexisting_failures) && selected.preexisting_failures.length ? `<section><h3>Pre-existing on the base commit <span class="badge">${selected.preexisting_failures.length}</span></h3><p class="small muted">These also fail before the change, so they are reported to the reviewer and not sent back to the implementer.</p><ul class="mono small muted">${selected.preexisting_failures.map(f => `<li>${esc(typeof f === 'string' ? f : JSON.stringify(f))}</li>`).join('')}</ul></section>` : ''}
                        ${suitesWithHistory.map(([suite, h]) => durationChart(suite, h, selected.run)).join('')}
                        <section aria-labelledby="log-h" class="col"><h3 id="log-h">Log</h3>
                        ${(selected.suites || []).map(s => { const c = suiteCounts(s);
                            return `<details class="col" ${s.passed ? '' : 'open'}><summary class="row">${statusIcon(s.passed ? 'done' : 'failed')}<strong>${esc(s.suite)}</strong><span class="work-suite-env small muted mono" title="build environment that ran it">${s.environment ? `<span aria-hidden="true">⬢</span> ${esc(s.environment)}` : 'shared runtime'}</span>${(s.preexisting_failures || []).length ? pill('muted', 'pre-existing failures') : ''}<span class="spacer"></span>${s.duration_ms != null ? `<span class="small muted num">${esc(duration(s.duration_ms))}</span>` : ''}<span style="width:160px">${resultBar(c.passed, c.failed, c.skipped)}</span><span class="small nowrap num">${c.passed}/${c.total}</span></summary>
                                <div class="col" style="margin-top:var(--s-1)"><code class="small">${esc(s.command || '')}</code><pre class="work-log">${ansi(String(s.stdout || '') + (s.stderr ? '\n' + s.stderr : '')) || '<span class="muted">No output.</span>'}</pre></div></details>`; }).join('') || '<p class="empty">No suite output.</p>'}</section>
                    </div></section>`; })() : ''}</div>`;
            bindCommands(panel);
            panel.querySelectorAll('tr[data-run]').forEach(tr => { tr.onclick = () => { selectedRun = Number(tr.dataset.run); renderPart('checks', true); }; });
            const filter = panel.querySelector('[data-failure-filter]');
            if (filter) filter.oninput = () => { failureFilter = filter.value; const pos = filter.selectionStart; renderPart('checks', true); const f = panel.querySelector('[data-failure-filter]'); f?.focus(); f?.setSelectionRange(pos, pos); };
            const log = panel.querySelector('[data-live-log]'); if (log) log.scrollTop = log.scrollHeight;
        }

        /* ------------------------------------------------------------ session */
        function sessionItems(data) {
            if (sessionStage) {
                const items = [];
                for (const m of data.stages?.[sessionStage] || []) for (const p of m.parts || []) {
                    if (p.type === 'text' && p.text) items.push({kind:m.role === 'user' ? 'user' : 'assistant', text:p.text});
                    else if (p.type === 'tool_call') { const env = ENV?.toolSummary(p.tool_name, p.tool_args);
                        const args = typeof p.tool_args === 'string' ? p.tool_args : JSON.stringify(p.tool_args ?? '');
                        items.push({kind:'tool', summary:env || `${p.tool_name}(${String(args || '').slice(0, 120)})`, text:`${p.tool_name}\n${args || ''}`}); }
                    else if (p.type === 'tool_result') { const result = typeof p.tool_result === 'string' ? p.tool_result : JSON.stringify(p.tool_result ?? '');
                        items.push({kind:'tool_result', summary:`${p.tool_name || 'result'}: ${String(result || '').split('\n')[0].slice(0, 160)}`, text:String(result || '')}); }
                }
                return items;
            }
            const items = [], pendingEnv = new Map();
            for (const e of data.events || []) {
                const inner = e.payload?.payload || {}, kind = e.payload?.event || e.event_type, stage = e.payload?.stage;
                if (e.event_type === 'sandbox_starting') items.push({kind:'workbench', text:`sandbox started${stage ? ` · ${stage}` : ''}${e.payload?.agent && e.payload.agent !== 'mucli' ? ` · ${e.payload.agent === 'claude' ? 'Claude Code' : 'Codex'}${e.payload.agent_model ? ` ${e.payload.agent_model}` : ''}` : ''}${e.payload?.environment ? ` · ${e.payload.environment}` : ''}${e.payload?.gpus ? ` · gpus ${e.payload.gpus}` : ''}`});
                else if (e.event_type === 'environment_command_started') { const p = e.payload || {};
                    // Live fold; the matching environment_command (end) fills in the exit code.
                    pendingEnv.set(`${p.env}\u0000${p.command}`, items.length);
                    items.push({kind:'tool', summary:`[${p.env || '?'}] ${p.command || ''} · running`, text:`run_in_environment · ${p.env || ''}${p.cwd && p.cwd !== '.' ? ` · in ${p.cwd}` : ''}\n$ ${p.command || ''}`}); }
                else if (e.event_type === 'environment_command') { const p = e.payload || {};
                    const key = `${p.env}\u0000${p.command}`, at = pendingEnv.has(key) ? pendingEnv.get(key) : -1;
                    pendingEnv.delete(key);
                    (at >= 0 ? (it => items.splice(at, 1, it)) : (it => items.push(it)))({kind:'tool', summary:`[${p.env || '?'}] ${p.command || ''}${p.timed_out ? ' · timed out' : p.return_code != null && p.return_code !== 0 ? ` · exit ${p.return_code}` : ''}`,
                        text:`run_in_environment · ${p.env || ''}${p.cwd && p.cwd !== '.' ? ` · in ${p.cwd}` : ''}\n$ ${p.command || ''}\nexit ${p.return_code ?? '?'}${p.duration_ms != null ? ` · ${duration(p.duration_ms)}` : ''}${p.image ? `\nimage ${p.image}` : ''}`}); }
                else if (['build_environments_preparing', 'build_environment_progress'].includes(e.event_type)) items.push({kind:'workbench', text:e.reason || 'Preparing build environments…'});
                else if (e.event_type === 'build_environment_ready') items.push({kind:'workbench', text:`build environment ${e.payload?.binding || ''} ready`});
                else if (e.event_type === 'build_environments_recreated') items.push({kind:'sys', text:e.reason || 'Build environment images are being re-created.'});
                else if (e.event_type === 'build_environment_failed') items.push({kind:'error', text:`build environment ${e.payload?.binding || ''} failed: ${e.reason || ''}`});
                else if (e.event_type === 'sandbox_stopped') items.push({kind:'workbench', text:`sandbox stopped${stage ? ` · ${stage}` : ''}`});
                else if (e.event_type === 'runtime_error' || kind === 'runtime_error') items.push({kind:'error', text:stripAnsi(inner.text || e.reason || 'runtime error')});
                else if (kind === 'agent_message') items.push({kind:inner.role === 'user' ? 'user' : 'assistant', text:inner.text || ''});
                else if (kind === 'tool_call_ui' && inner.tool_name === 'run_in_environment') continue; // shown by environment_command
                else if (kind === 'tool_call_ui') items.push({kind:'tool', summary:inner.tool_name || 'tool', text:JSON.stringify(inner, null, 2)});
                else if (kind === 'tool_result_ui') items.push({kind:'tool_result', summary:String(inner.preview || '').split('\n').find(Boolean)?.slice(0, 160) || 'result', text:String(inner.preview || '')});
                else if (kind === 'runtime_status') continue;
                else if (e.event_type === 'stage_message') items.push({kind:'assistant', text:inner.text || e.payload?.text || e.reason || ''});
                else items.push({kind:'sys', text:stripAnsi(inner.text || e.payload?.text || e.reason || kind)});
            }
            return items;
        }
        function renderSession(panel) {
            const data = job.session || {};
            const items = sessionItems(data);
            const msg = job.actions.find(a => a.id === 'message');
            const live = stateStatus(job.state) === 'active' && data.live;
            const status = [...(data.events || [])].reverse().find(e => e.payload?.event === 'runtime_status');
            const draft = panel.querySelector('[data-message]')?.value || '';
            const wasFocused = document.activeElement === panel.querySelector('[data-message]');
            const scroller = panel.querySelector('.work-transcript');
            const stick = !scroller || scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 40;
            const chevron = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>';
            const item = it => {
                switch (it.kind) {
                    case 'assistant': return `<div class="work-msg-assistant md">${markdown(it.text)}</div>`;
                    case 'user': return `<div class="work-msg-user"><span class="sr-only">You: </span><pre>${esc(it.text)}</pre></div>`;
                    case 'tool': case 'tool_result': { const multi = it.text && it.text !== it.summary;
                        return `<div><button type="button" class="work-fold ${it.kind === 'tool' ? 'is-tool' : ''}" ${multi ? 'aria-expanded="false" data-fold' : 'disabled'}>${multi ? chevron : '<span class="work-fold-pad"></span>'}<span class="mono ellipsis">${it.kind === 'tool' ? '› ' : '↳ '}${esc(it.summary)}</span></button>${multi ? `<pre class="work-fold-body" hidden>${esc(it.text)}</pre>` : ''}</div>`; }
                    case 'workbench': return `<div>${pill('accent', `<span aria-hidden="true">⬢</span> ${esc(it.text)}`)}</div>`;
                    case 'error': return `<div class="work-msg-sys tone-danger"><span aria-hidden="true">✗ </span>${esc(it.text)}</div>`;
                    default: return `<div class="work-msg-sys muted">${esc(it.text)}</div>`;
                }
            };
            const stages = [['', 'Live'], ['plan', 'Plan'], ['implement', 'Implement'], ['review', 'Review']];
            panel.innerHTML = `<div class="col" style="gap:var(--s-1)">
                <div class="row small wrap">${job.state === 'needs_input' ? '<span class="tone-warn"><span aria-hidden="true">◆</span> waiting for you</span>' : live ? '<span class="tone-running"><span class="status-icon active" aria-hidden="true">●</span> live session · the agent is working</span>' : `<span class="muted">${sessionStage ? `transcript of the ${sessionStage} stage` : 'recent stage activity'}</span>`}
                    <div class="work-segmented" role="group" aria-label="Session source">${stages.map(([v, l]) => `<button type="button" data-session-stage="${v}" aria-pressed="${v === sessionStage}">${l}</button>`).join('')}</div>
                    ${status && live ? `<span class="muted mono ellipsis" style="max-width:420px" title="${esc(stripAnsi(status.payload?.payload?.text))}">› ${esc(stripAnsi(status.payload?.payload?.text))}</span>` : ''}
                    <span class="spacer"></span><span class="muted">${items.length} items</span></div>
                <div class="work-transcript" role="log" aria-label="Agent session" tabindex="0">${items.length ? items.map(item).join('') : '<p class="empty">No session output yet.</p>'}</div>
                ${msg?.enabled ? `<form class="work-msg-box" data-message-form><label for="work-agent-msg" class="sr-only">Message the agent</label><textarea id="work-agent-msg" data-message rows="2" placeholder="Message the agent… (Enter to send, Shift+Enter for a new line)">${esc(draft)}</textarea>
                    <button type="submit" class="btn primary" data-send-message><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m22 2-7 20-4-9-9-4Z"/><path d="M22 2 11 13"/></svg> Send</button></form>`
                    : `<p class="muted small">Messaging unavailable${msg?.reason ? `: ${esc(msg.reason)}` : ''}.</p>`}</div>`;
            panel.querySelectorAll('[data-session-stage]').forEach(b => { b.onclick = () => { sessionStage = b.dataset.sessionStage; renderPart('session', true); }; });
            panel.querySelectorAll('[data-fold]').forEach(b => { b.onclick = () => { const body = b.nextElementSibling, open = b.getAttribute('aria-expanded') === 'true'; b.setAttribute('aria-expanded', String(!open)); body.hidden = open; b.querySelector('svg').style.transform = open ? '' : 'rotate(90deg)'; }; });
            const next = panel.querySelector('.work-transcript');
            if (stick || !scroller) next.scrollTop = next.scrollHeight; else next.scrollTop = scroller.scrollTop;
            const form = panel.querySelector('[data-message-form]');
            if (form) {
                const box = form.querySelector('[data-message]');
                if (wasFocused) { box.focus(); box.setSelectionRange(box.value.length, box.value.length); }
                box.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } });
                form.onsubmit = async e => { e.preventDefault(); if (!box.value.trim()) return;
                    try { await send('message', {text:box.value.trim()}); box.value = ''; notify('Message sent', 'ok'); } catch (error) { notify(error.message, 'danger'); } };
            }
        }

        /* ------------------------------------------------------------ diagnostics */
        function renderDiagnostics(panel) {
            if (!panel.querySelector('[data-diagnostics-host]')) panel.innerHTML = '<div class="work-detail" data-diagnostics-host data-job-id=""></div>';
            const host = panel.querySelector('[data-diagnostics-host]');
            host.dataset.jobId = selectedId;
            window.dispatchEvent(new CustomEvent('mu:job-diagnostics', {detail:{jobId:selectedId, host}}));
        }

        /* ------------------------------------------------------------ cost & audit */
        const EVENT_TONE = e => (/(fail|error|denied|reject|cancel|discard)/i.test(e) ? 'danger' : /(approv|done|pass|push|merged|complete)/i.test(e) ? 'ok' : /(edit|input|wait|needs|human|attention)/i.test(e) ? 'warn' : 'accent');
        function renderCosts(panel) {
            const costs = job.costs || {};
            const stages = Object.entries(costs.by_stage || {}).sort((a, b) => b[1] - a[1]);
            const max = Math.max(0.0001, ...stages.map(([, v]) => v));
            const events = (costs.events || []).filter(e => !['sandbox_event', 'checks_output'].includes(e.event_type));
            const times = events.map(e => Number(e.created_at)).filter(Number.isFinite);
            const timeline = times.length > 1 ? (() => { const t0 = Math.min(...times), t1 = Math.max(...times), W = 1000, x = t => 20 + ((t - t0) / Math.max(1, t1 - t0)) * (W - 40);
                return `<svg class="work-timeline" viewBox="0 0 ${W} 90" role="img" aria-label="Timeline: ${events.length} events from ${esc(when(t0))} to ${esc(when(t1))}"><line x1="20" x2="${W - 20}" y1="50" y2="50" class="tl-axis"/>${events.map(e => `<g><title>${esc(`${when(e.created_at)} ${e.event_type}${e.reason ? ` · ${e.reason}` : ''}`)}</title><circle cx="${x(Number(e.created_at))}" cy="50" r="4" class="tl_${EVENT_TONE(e.event_type + ' ' + (e.to_status || ''))}"/></g>`).join('')}<text x="20" y="86" class="tl-text">${esc(when(t0))}</text><text x="${W - 20}" y="86" text-anchor="end" class="tl-text">${esc(when(t1))}</text></svg>`; })() : '';
            panel.innerHTML = `<div class="stack"><div class="grid-2">
                <section class="panel" aria-labelledby="cost-h"><header><h2 id="cost-h">Cost per stage</h2><span class="spacer"></span><strong class="num">${money(costs.total_usd)}</strong></header><div class="panel-body">${stages.length ? `<ul class="plain col">${stages.map(([s, v]) => `<li class="work-cost-row"><span class="work-cost-label">${esc(s)}</span><span class="work-cost-track"><span class="work-cost-fill" style="width:${(v / max) * 100}%"></span></span><span class="num">${money(v)}</span></li>`).join('')}</ul>` : '<p class="empty">No cost recorded.</p>'}</div></section>
                <section class="panel" aria-labelledby="att-h"><header><h2 id="att-h">Attempts</h2></header><div class="panel-body flush table-wrap" data-attempts>${attemptsTable()}</div></section></div>
                <section class="panel" aria-labelledby="audit-h"><header><h2 id="audit-h">Audit timeline</h2><span class="badge">${events.length}</span></header><div class="panel-body stack">${timeline}
                    ${events.length ? `<ol class="plain work-audit">${events.slice().reverse().map(e => `<li><span class="work-audit-dot tone-${EVENT_TONE(e.event_type + ' ' + (e.to_status || ''))}" aria-hidden="true">●</span><time class="mono small muted nowrap" datetime="${esc(new Date(Number(e.created_at) * 1000).toISOString?.() || '')}">${esc(when(e.created_at))}</time><details><summary><strong>${esc(e.event_type)}</strong>${e.to_status ? ` <span class="muted small">→ ${esc(e.to_status)}</span>` : ''}${e.reason ? ` <span class="small muted">${esc(e.reason)}</span>` : ''}</summary><pre>${esc(JSON.stringify(e.payload || {}, null, 2))}</pre></details></li>`).join('')}</ol>` : '<p class="empty">No audit entries.</p>'}</div></section></div>`;
            if (!attempts || attempts.id !== selectedId) loadAttempts();
        }
        function attemptsTable() {
            if (!attempts || attempts.id !== selectedId) return '<div class="loading" role="status"><span class="status-icon active tone-running" aria-hidden="true">●</span>Loading attempts…</div>';
            if (!attempts.rows.length) return '<p class="empty">None.</p>';
            return `<table class="table"><thead><tr><th scope="col">#</th><th scope="col">Status</th><th scope="col" class="num">Cost</th><th scope="col">Started</th><th scope="col" class="num">Took</th></tr></thead><tbody>${attempts.rows.map(a => `<tr><td class="num">${esc(a.number)}</td><td>${pill(/fail|error/.test(a.status) ? 'danger' : /run/.test(a.status) ? 'running' : /succ|done|complete|pass/.test(a.status) ? 'ok' : 'muted', esc(a.status))}${a.error ? `<div class="small tone-danger ellipsis" style="max-width:260px" title="${esc(a.error)}">${esc(a.error)}</div>` : ''}</td><td class="num">${money(a.cost_usd)}</td><td class="muted small nowrap">${esc(when(a.started_at))}</td><td class="num muted">${a.finished_at && a.started_at ? duration((a.finished_at - a.started_at) * 1000) : ''}</td></tr>`).join('')}</tbody></table>`;
        }
        async function loadAttempts() {
            const id = selectedId;
            try { const r = await api(`/api/jobs/${encodeURIComponent(id)}/attempts`); attempts = {id, rows:(r.attempts || []).slice().reverse()}; }
            catch (_) { attempts = {id, rows:[]}; }
            if (id === selectedId) { const host = detailEl.querySelector('[data-attempts]'); if (host) host.innerHTML = attemptsTable(); }
        }

        const renderers = {plan:renderPlan, session:renderSession, diff:renderDiff, review:renderReview, checks:renderChecks, diagnostics:renderDiagnostics, costs:renderCosts};
        function renderPart(part, force = false) {
            if (!tabs.includes(part) || !job) return;
            const value = part === 'diff' && job.diff?.live ? {...job.diff, live:{...job.diff.live, generated_at:0}} : job[part];
            const signature = JSON.stringify([value, part === 'diff' ? job.review : null, job.actions, part === 'session' ? job.state : null]);
            if (!force && signatures.get(part) === signature) return;
            if (part === 'plan' && editor?.isDirty()) return;
            const panel = detailEl.querySelector(`[data-panel="${part}"]`);
            if (!panel) return;
            renderers[part](panel); signatures.set(part, signature);
        }
        function selectTab(next) {
            if (!tabs.includes(next)) next = 'session';
            tab = next;
            detailEl.querySelectorAll('[data-tab]').forEach(b => { const on = b.dataset.tab === tab; b.setAttribute('aria-selected', String(on)); b.tabIndex = on ? 0 : -1; });
            detailEl.querySelectorAll('[data-panel]').forEach(p => { p.hidden = p.dataset.panel !== tab; });
            renderPart(tab);
            onRoute?.({id:selectedId, tab});
        }
        async function refreshParts(parts = tabs) {
            if (!selectedId) return;
            const id = selectedId;
            const wanted = [...new Set(parts)].filter(p => p !== 'diagnostics');
            const response = await api(`/api/jobs/vm/job/${encodeURIComponent(id)}?parts=${encodeURIComponent(wanted.join(','))}`);
            if (id !== selectedId) return;
            job = {...job, ...response}; renderState();
            for (const part of parts) if (part === tab || part === 'plan' && editor) renderPart(part);
            renderPart(tab);
        }
        async function select(id, {tab: requested} = {}) {
            if (!id) return;
            if (id !== selectedId && editor && !editor.canLeave()) return;
            const request = ++fetchNumber;
            const next = await api(`/api/jobs/vm/job/${encodeURIComponent(id)}`);
            if (request !== fetchNumber) return;
            if (selectedId !== id) {
                editor?.destroy(); editor = null; editing = false; selectedFindings.clear(); signatures.clear(); collapsed.clear();
                sessionStage = ''; selectedRun = null; failureFilter = ''; showUnavailable = false; attempts = null;
                tab = tabs.includes(requested) ? requested : defaultTab(next); selectedId = id; shell();
            } else if (tabs.includes(requested) && requested !== tab) { tab = requested; }
            job = next; detailEl.dataset.jobId = id; renderState(); selectTab(tab); renderHome(); return true;
        }
        function leave() { ui.register('page:job', []); }
        /* Live diff: while the Diff tab of an active job is open, refetch only the diff part. */
        let liveBusy = false;
        async function refreshLiveDiff() {
            if (liveBusy || !selectedId || tab !== 'diff' || !job?.diff?.live || document.visibilityState !== 'visible') return;
            liveBusy = true;
            const id = selectedId;
            try {
                const response = await api(`/api/jobs/vm/job/${encodeURIComponent(id)}?parts=diff`);
                if (id !== selectedId || !response.diff) return;
                job = {...job, diff:response.diff};
                renderPart('diff'); renderBadges();
            } catch (_) { /* the next tick or SSE refresh retries */ }
            finally { liveBusy = false; }
        }

        async function start({ autoSelect = true } = {}) {
            await refreshHome();
            const initial = home.needs_you[0] || home.running[0] || home.jobs[0];
            if (autoSelect && initial) await select(initial.id);
            source = new EventSource('/api/jobs/events');
            source.onmessage = async event => { try { const change = JSON.parse(event.data); if (change.scope === 'home') await refreshHome(); if (change.scope === 'job' && change.id === selectedId) await refreshParts(change.parts); } catch (e) { notify(e.message, 'danger'); } };
            source.addEventListener('hello', () => { onConnection?.(true); refreshHome().catch(() => {}); if (selectedId) refreshParts().catch(() => {}); });
            source.onerror = () => onConnection?.(false);
            source.onopen = () => onConnection?.(true);
            window.addEventListener('beforeunload', () => source.close());
            setInterval(() => { if (home && document.visibilityState === 'visible') renderHome(); }, 30000);
            setInterval(() => { void refreshLiveDiff(); }, LIVE_DIFF_MS);
        }
        return {start, select, selectTab, leave, runById, refreshHome, refreshParts, selectedId:() => selectedId, home:() => home, job:() => job};
    }
    window.MuJobWork = {create, util:{esc, money, duration, age, when, ansi, stripAnsi, pill, statePill, statusIcon, stateLabel, stateStatus, STATUS, jobKey}};
})();
