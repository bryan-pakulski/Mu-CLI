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
                if (map[c]) { html += `<span class="work-ansi-${map[c]}">`; open++; }
                else if (c === 1) { html += '<span class="work-ansi-bold">'; open++; }
                else if (c === 2) { html += '<span class="work-ansi-dim">'; open++; }
            }
            return html;
        });
        return out + '</span>'.repeat(open);
    };

    /* Status vocabulary: the shared Wash vocabulary (mu/design/vocabulary.json via
     * static/js/wash_tokens.js) — glyphs, semantic tones and canonical labels for
     * statuses, job states, review severities, verdicts and plan coverage. No local
     * copies: a state reads the same here, in the terminal and on Android. */
    const WASH = window.WASH;
    const VOCAB = WASH.vocabulary;
    const statusOf = status => { const v = VOCAB.status[status] || VOCAB.status.pending; return {glyph:v.glyph, tone:v.semantic, label:v.label}; };
    const stateStatus = state => WASH.stateStyle(state).status;
    const stateLabel = state => WASH.stateStyle(state).label;
    const severityTone = sev => VOCAB.severity[sev]?.semantic || 'neutral';
    const SEVERITIES = Object.keys(VOCAB.severity).sort((a, b) => VOCAB.severity[a].rank - VOCAB.severity[b].rank);
    const verdictOf = v => (VOCAB.verdict[v] ? [VOCAB.verdict[v].semantic, VOCAB.verdict[v].label] : ['neutral', String(v || '').replaceAll('_', ' ')]);
    const coverageStatus = c => VOCAB.coverage[c] || 'pending';
    const cap = text => { const s = String(text || ''); return s ? s[0].toUpperCase() + s.slice(1) : s; };
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
    const INPUT_ARG = {rejectPlan:'feedback', denyTool:'message', resume:'guidance', reprompt:'guidance', message:'text', rejectEnvironment:'reason', saveRepairedTemplate:'name'};
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
    /* Environment repair (job_templates.md §7): the repair card's actions. */
    const REPAIR_ACTIONS = ['addRepairToTemplate', 'saveRepairedTemplate'];

    /* data-tone for Wash components; server ActionVM tones still say warn/muted. */
    const washTone = tone => ({warn:'attention', muted:'neutral'}[tone] || tone || '');
    const toneAttr = tone => (tone ? ` data-tone="${esc(washTone(tone))}"` : '');
    /* Mu §5.4: status is a sign plus a word, never a pill. `pill` and friends keep their names
     * (every job module calls them) but render a tagged word: tone ink only for attention,
     * danger and caution, quiet ink otherwise (.work-tag in work.css). */
    const chipGlyph = glyph => `<span class="work-tag__glyph" aria-hidden="true">${glyph}</span>`;
    /* Inline status: glyph + label for assistive tech, the ink drop while active. */
    const statusIcon = (status, label) => { const s = statusOf(status), text = label || s.label;
        return `<span class="w-status work-status-icon" data-tone="${s.tone}" data-status="${esc(status)}" title="${esc(text)}">${status === 'active' ? '<span class="w-inkdrop" data-size="sm" aria-hidden="true"></span>' : `<span class="w-status__glyph" aria-hidden="true">${s.glyph}</span>`}<span class="w-sr-only">${esc(text)}</span></span>`; };
    const pill = (tone, html, title = '') => `<span class="work-tag"${toneAttr(tone)}${title ? ` title="${esc(title)}"` : ''}>${html}</span>`;
    const statePill = state => { const st = WASH.stateStyle(state);
        return `<span class="w-signword work-tag" data-status="${st.status}" title="${esc(state)}"><span class="w-sign" data-status="${esc(st.status)}" aria-hidden="true">${esc(st.glyph)}</span>${esc(cap(st.label))}</span>`; };
    const verdictPill = v => { const [tone, label] = verdictOf(v); return `<span class="work-tag work-verdict" data-tone="${tone}" data-verdict="${esc(v)}">${chipGlyph(tone === 'ok' ? '✓' : tone === 'danger' ? '✗' : '◆')}<span>${esc(cap(label))}</span></span>`; };
    const severityPill = sev => pill(severityTone(sev), esc(cap(VOCAB.severity[sev]?.label || sev)));
    /* Proportional bars are SVG (attribute geometry, no inline styles). */
    function segmentBar(segments, {label = '', large = false, cls = 'work-bar'} = {}) {
        const sum = Math.max(1e-9, segments.reduce((n, s) => n + Math.max(0, s.value), 0));
        let x = 0;
        const rects = segments.filter(s => s.value > 0).map(s => { const w = (s.value / sum) * 100; const r = `<rect class="seg-${s.tone}" x="${x}" y="0" width="${w}" height="1"/>`; x += w; return r; }).join('');
        return `<svg class="${cls}${large ? ' is-lg' : ''}" viewBox="0 0 100 1" preserveAspectRatio="none" role="img" aria-label="${esc(label)}" focusable="false"><title>${esc(label)}</title><rect class="seg-track" x="0" y="0" width="100" height="1"/>${rects}</svg>`;
    }
    const resultBar = (passed, failed, skipped = 0, large = false) => {
        const text = `${passed} passed, ${failed} failed${skipped ? `, ${skipped} skipped` : ''}`;
        return segmentBar([{value:passed, tone:'ok'}, {value:failed, tone:'danger'}, {value:skipped, tone:'neutral'}], {label:text, large});
    };
    /* A single fill of a track (cost per stage): fraction 0..1. */
    const meterBar = (fraction, label, tone = 'accent') => segmentBar([{value:Math.max(0, Math.min(1, fraction)), tone}, {value:Math.max(0, 1 - fraction), tone:'none'}], {label, cls:'work-meter'});
    const jobKey = row => row.key || `#${String(row.id || '').slice(0, 8)}`;
    const stepsOf = row => row.pipeline || row.steps || [];
    const describeStep = s => [`${s.label}: ${statusOf(s.status).label}`, s.elapsed >= 1 ? duration(s.elapsed * 1000) : '', s.cost_usd ? money(s.cost_usd) : '', s.runs > 1 ? `${s.runs} runs` : ''].filter(Boolean).join(', ');
    const focusStep = steps => steps.find(s => s.status === 'failed') || steps.find(s => s.status === 'active' || s.status === 'waiting');
    const stepGlyph = s => (s.status === 'active' ? '<span class="w-inkdrop" data-size="sm" aria-hidden="true"></span>' : `<span aria-hidden="true">${statusOf(s.status).glyph}</span>`);

    /* Mini stepper (job rows, G§9.15): the compact Wash stepper — dots plus the current label. */
    function miniStepper(steps) {
        const current = focusStep(steps);
        return `<ol class="w-stepper work-mini" data-compact aria-label="${esc('Pipeline: ' + steps.map(describeStep).join('; '))}">${steps.map(s =>
            `<li class="w-stepper__step" data-status="${esc(s.status)}"${s === current ? ' aria-current="step"' : ''} title="${esc(describeStep(s))}">${stepGlyph(s)}${s === current ? esc(s.label) : `<span class="w-sr-only">${esc(s.label)}</span>`}</li>`).join('')}</ol>`;
    }
    /* Pipeline stepper (job detail, G§9.13): glyph + label per step joined by hairlines; each
     * step opens the tab that shows it. Labels come from the job VM (shared vocabulary). */
    function pipelineStepper(steps) {
        const current = focusStep(steps);
        return `<ol class="w-stepper work-stepper" aria-label="Pipeline">${steps.map(s => {
            const meta = [s.elapsed >= 1 ? duration(s.elapsed * 1000) : '', s.cost_usd ? money(s.cost_usd) : '', s.runs > 1 ? `×${s.runs}` : ''].filter(Boolean).join(' · ');
            return `<li class="w-stepper__step" data-status="${esc(s.status)}"${s === current ? ' aria-current="step"' : ''}><button type="button" class="work-step" data-step="${esc(s.id)}" title="${esc(describeStep(s) + (s.detail ? ` — ${s.detail}` : ''))}">${stepGlyph(s)}<span>${esc(s.label)}</span>${meta ? `<span class="work-step__meta">${esc(meta)}</span>` : ''}<span class="w-sr-only">${esc(statusOf(s.status).label)}</span></button></li>`;
        }).join('')}</ol>`;
    }

    /* Sanitised markdown (plan content and agent text may contain repository-controlled HTML). */
    function markdown(text) {
        if (window.MuJobPlan?.preview) return window.MuJobPlan.preview(String(text ?? ''));
        return `<pre>${esc(text)}</pre>`;
    }

    /* ---------------------------------------------------------------- unified diff (wash/diff.js)
     * The one Wash diff renderer: parse, per-file body (unified/split, intra-
     * line marks, syntax colours, interleaved review findings), status
     * letters and the changed-file tree. This page only frames the files. */
    const DIFF = window.WashDiff;
    const parsePatch = patch => DIFF.parseUnified(patch);
    /* Inline sprite icon (static/icons/wash.svg). */
    const icon = (name, size = 'sm') => `<svg class="w-icon"${size ? ` data-size="${size}"` : ''} aria-hidden="true" focusable="false"><use href="/static/icons/wash.svg#i-${name}"></use></svg>`;

    function create({ api, toast, boardEl, detailEl, totalEl, jobsEl = null, listEl = null, listCountEl = null, asideEl = null, onOpen = null, onRoute = null, onHome = null, onConnection = null }) {
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
        const tabs = ['notes', 'session', 'plan', 'diff', 'review', 'checks', 'diagnostics', 'costs'];
        /* Heavy parts (agent transcripts, the audit trail) load only while their tab is open. */
        const LAZY = ['session', 'costs'];
        const stale = new Set();
        const approvalOf = j => j?.approval ?? j?.session?.approval ?? null;
        const labels = {notes:'Notes', plan:'Plan', session:'Session', diff:'Diff', review:'Review', checks:'Checks', diagnostics:'Diagnostics', costs:'Cost & audit'};
        const stepTabs = {plan:'plan', approve:'plan', implement:'session', checks:'checks', review:'review', push:'review'};
        const open = id => (onOpen ? onOpen(id) : select(id));
        /* The job renders in two panes (Mu §11b): its work in the main pane (detailEl) and its
         * context in the aside (asideEl). Lookups cover both. */
        const q = sel => detailEl.querySelector(sel) || asideEl?.querySelector(sel) || null;
        const qa = sel => [...detailEl.querySelectorAll(sel), ...(asideEl ? asideEl.querySelectorAll(sel) : [])];
        let inJob = false;          // the job route is open (the list marks the selected row)
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
            if (approvalOf(next)) return 'session';
            if (state === 'plan_review') return 'plan';
            return 'notes';
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
            if (type === 'message') { selectTab('session'); setTimeout(() => q('[data-message]')?.focus(), 0); return; }
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
                const banner = type === 'denyTool' ? q('[data-deny-reason]')
                    : type === 'raiseLimit' ? q('[data-limit-amount]') : null;
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
                if (type === 'delete') { editor?.destroy(); editor = null; job = null; selectedId = ''; delete detailEl.dataset.jobId; detailEl.innerHTML = ''; asideEl?.replaceChildren(); onOpen?.(null); }
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
            const form = document.createElement('form');
            form.className = 'work-stack work-push-form';
            form.noValidate = true;
            form.innerHTML = `
                <div class="w-chip-row work-push-target">
                    <span class="w-chip" data-variant="meta">${icon('git-branch')}<span class="w-sr-only">branch</span><span class="work-mono">${esc(preview.branch || job.branch || '(job branch)')}</span></span>
                    <span aria-hidden="true" class="work-meta">→</span><span class="w-sr-only">into</span><span class="w-chip" data-variant="meta"><span class="work-mono">${esc(preview.base || 'default branch')}</span></span>
                    ${preview.mode ? pill(preview.mode === 'none' ? 'caution' : 'accent', `via ${esc(preview.mode)}`, preview.mode === 'none' ? 'No PR provider: the branch is pushed without opening a PR' : '') : ''}
                    ${preview.verdict ? verdictPill(preview.verdict) : ''}
                    ${preview.head_sha ? `<span class="work-meta work-mono" title="verified head">${esc(String(preview.head_sha).slice(0, 12))}</span>` : ''}
                </div>
                ${preview.remote ? `<div class="work-meta work-ellipsis" title="${esc(preview.remote)}">remote <code>${esc(preview.remote)}</code></div>` : ''}
                ${preview.reason ? `<div class="w-banner" data-tone="attention" role="note"><span class="w-banner__glyph" aria-hidden="true">◆</span><p class="w-banner__msg">${esc(preview.reason)}</p></div>` : ''}
                ${preview.token && !preview.token.source ? `<div class="w-banner" data-tone="attention" role="note" data-push-token-missing><span class="w-banner__glyph" aria-hidden="true">◆</span><p class="w-banner__msg">No ${esc(preview.token.variable)} is set, so the branch will be pushed but no ${preview.token.provider === 'gitlab' ? 'merge request' : 'pull request'} can be opened. Add a ${esc(preview.token.provider)} token in Settings → Push.</p></div>` : ''}
                <div class="w-field"><label class="w-field__label" for="work-push-title">PR title</label>
                    <input class="w-input" id="work-push-title" type="text" maxlength="200" value="${esc(preview.title)}" placeholder="${preview.title ? '' : 'Default: composed from the job'}" data-autofocus></div>
                <div class="w-field"><div class="work-toolbar"><label class="w-field__label" for="work-push-body">PR description</label><span class="work-spacer"></span>
                    <div class="w-segmented" data-size="sm" role="group" aria-label="Description view"><button type="button" class="w-segmented__option" data-push-view="write" aria-pressed="true">Write</button><button type="button" class="w-segmented__option" data-push-view="preview" aria-pressed="false">Preview</button></div></div>
                    <textarea class="w-textarea" id="work-push-body" rows="8" maxlength="20000" placeholder="${preview.body ? '' : 'Default: composed from the plan, checks and review'}">${esc(preview.body)}</textarea>
                    <div class="work-push-preview w-markdown work-markdown" data-push-preview hidden></div>
                    <p class="w-field__hint">Markdown. Leave blank to use the default text.</p></div>
                <label class="w-check"><input class="w-check__input" type="checkbox" id="work-push-draft" ${preview.draft === false ? '' : 'checked'}><span>Open as a draft PR</span></label>
                ${(action.args?.unverified || preview.unverified) && !needsOverride ? `<div class="w-banner" data-tone="caution" role="status" data-push-unverified><span class="w-banner__glyph" aria-hidden="true">?</span><p class="w-banner__msg">Some checks could not run, so this change is unverified. The PR body says so.</p></div>` : ''}
                ${needsOverride ? `<div class="w-field"><div class="w-banner" data-tone="danger" role="alert"><span class="w-banner__glyph" aria-hidden="true">✗</span><p class="w-banner__msg">${action.args?.unverified || preview.unverified ? 'Some checks could not run, so this change is unverified.' : 'The review verdict blocks merging.'} Pushing anyway needs a reason; it is recorded in the job audit.</p></div>
                    <label class="w-field__label" for="work-push-override">Reason for overriding the blocking review</label>
                    <textarea class="w-textarea" id="work-push-override" rows="3" required aria-required="true"></textarea><p class="w-field__error" data-push-error></p></div>` : ''}`;
            const foot = document.createElement('div');
            foot.className = 'w-btn-row';
            foot.innerHTML = `<button type="button" class="w-btn" data-variant="secondary" data-cancel>Cancel</button><button type="button" class="w-btn" data-variant="${needsOverride ? 'danger' : 'primary'}" data-push-submit>${needsOverride ? 'Override and push' : `Push ${esc(preview.branch || job.branch || '')}`.trim()}</button>`;
            const d = ui.dialog ? ui.dialog({title:`Push ${key}`, body:form, footer:foot, wide:true}) : null;
            if (!d) return;
            const body = form.querySelector('#work-push-body'), previewEl = form.querySelector('[data-push-preview]');
            form.querySelectorAll('[data-push-view]').forEach(b => { b.onclick = () => {
                const show = b.dataset.pushView === 'preview';
                form.querySelectorAll('[data-push-view]').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
                if (show) previewEl.innerHTML = body.value.trim() ? markdown(body.value) : '<p class="work-muted">Nothing to preview — the default description is used.</p>';
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
            const host = q('[data-environments]');
            if (!host) return;
            if (!ENV || !envOpen) { host.innerHTML = ''; return; }
            const buttons = ['refreshEnvironments', 'keepEnvironments', 'setEnvironments']
                .map(type => job.actions.find(a => a.id === type)).filter(Boolean)
                .map(a => actionButton({...a, label:a.id === 'setEnvironments' ? 'Change' : a.label}, {size:'sm'})).join('');
            host.innerHTML = ENV.panel(job.build_environments, buttons);
            // The repair card (environment repair, §7) gets its actions in its own toolbar.
            host.querySelectorAll('[data-repair-actions]').forEach(slot => { slot.insertAdjacentHTML('beforeend', repairButtons()); });
            bindCommands(host);
            bindRepairTranscript(host);
        }
        /* Environment repair actions (non-blocking, never required): offered on the repair card. */
        function repairButtons() {
            return REPAIR_ACTIONS.map(type => job.actions.find(a => a.id === type))
                .filter(a => a && (a.enabled || a.reason !== 'no committed environment repair'))
                .map(a => actionButton({...a, primary:false}, {size:'sm', variant:'secondary'})).join('');
        }
        function bindRepairTranscript(root) {
            root.querySelectorAll('[data-repair-transcript]').forEach(b => { b.onclick = () => { sessionStage = 'repair'; selectTab('session'); renderPart('session', true); }; });
        }
        async function runById(id, type) {
            if (await select(id) !== true) return;
            await invoke(type);
        }

        /* One Wash button per action. variant: primary (the one the state asks for), tonal
         * (a toned alternative), secondary (everything else); danger only for an enabled
         * destructive primary. */
        function actionButton(a, {size = '', extra = '', variant = ''} = {}) {
            const tone = washTone(toneOf(a));
            const v = variant || (a.primary && a.enabled ? (tone === 'danger' ? 'danger' : 'primary') : tone && tone !== 'accent' && tone !== 'danger' && a.enabled ? 'tonal' : 'secondary');
            const dirtyBlock = a.id === 'approvePlan' && editor?.isDirty();
            const label = LABEL[a.id] || a.label;
            const title = dirtyBlock ? `${label}: save or cancel your plan edits first` : a.enabled ? (a.reason || (a.shortcut ? `${label} (${a.shortcut})` : label)) : `${label}: ${a.reason || 'not available now'}`;
            // Mu §7.3: one ink pill (the action the state asks for); every other action is a text button.
            const text = v !== 'primary' && v !== 'danger';
            const cls = text ? 'w-textbtn' : 'w-btn';
            const attrs = text ? (tone === 'danger' ? ' data-tone="danger"' : '') : ` data-variant="${v}"${size ? ` data-size="${size}"` : ''}${tone ? ` data-tone="${esc(tone)}"` : ''}`;
            return `<button type="button" class="${cls}"${attrs} data-command="${esc(a.id)}" data-action="${esc(a.id)}"${a.id === 'approvePlan' ? ' data-plan-approve' : ''}${a.id === 'rejectPlan' ? ' data-plan-reject' : ''}${a.enabled ? '' : ' aria-disabled="true"'}${dirtyBlock ? ' disabled' : ''}${a.shortcut ? ` aria-keyshortcuts="${esc(a.shortcut)}"` : ''} title="${esc(title)}" ${extra}><span>${esc(label)}</span></button>`;
        }
        /* Overflow menu item for an action (G§9.17 menu): disabled items stay listed with their reason. */
        function actionMenuItem(a) {
            const tone = washTone(toneOf(a));
            const label = LABEL[a.id] || a.label;
            return `<button type="button" class="w-menu__item" role="menuitem" data-command="${esc(a.id)}" data-action="${esc(a.id)}"${a.id === 'rejectPlan' ? ' data-plan-reject' : ''}${tone === 'danger' ? ' data-tone="danger"' : ''}${a.enabled ? '' : ' aria-disabled="true"'}${a.shortcut ? ` aria-keyshortcuts="${esc(a.shortcut)}"` : ''} title="${esc(a.enabled ? (a.reason || label) : `${label}: ${a.reason || 'not available now'}`)}"><span class="w-menu__label">${esc(label)}${!a.enabled && a.reason ? `<span class="work-menu-reason">${esc(a.reason)}</span>` : ''}</span>${a.shortcut ? `<kbd class="w-menu__shortcut" aria-hidden="true">${esc(a.shortcut)}</kbd>` : ''}</button>`;
        }
        function bindCommands(root) {
            root.querySelectorAll('[data-command]').forEach(b => { b.onclick = () => invoke(b.dataset.command); });
        }

        /* ------------------------------------------------------------ dashboard (Mu §5, §10.2) */
        /* Now (what needs you, its decisive action pinned on the row), Running, Recent: rows,
         * never cards. The state sentence in the page header carries the counts. */
        const PRIMARY_BY_STATE = {plan_review:'approvePlan', review_done:'push', needs_input:'resume', failed:'retry'};
        const repoName = row => (row.repository ? String(row.repository).split('/').filter(Boolean).pop() : '');
        const tone = (text, t) => (t ? `<span data-tone="${t}">${esc(text)}</span>` : esc(text));
        /* The state sign (Mu §5.4): glyph + colour from the shared vocabulary; the label is read out. */
        const signOf = row => { const st = WASH.stateStyle(row.state);
            return `<span class="w-sign" data-status="${esc(st.status)}" title="${esc(cap(st.label))}" aria-hidden="true">${esc(st.glyph)}</span>`; };
        /* Line 2: the state in words, then at most two facts that change what you'd do. */
        function rowSentence(row) {
            const st = WASH.stateStyle(row.state);
            const t = st.status === 'waiting' ? 'attention' : st.status === 'failed' ? 'danger' : '';
            const lead = row.limit?.summary ? 'Limit reached' : cap(st.label);
            const facts = [row.limit?.summary || (st.status === 'waiting' || st.status === 'failed' ? row.attention_detail : ''),
                row.verdict && st.status !== 'waiting' ? cap(verdictOf(row.verdict)[1]) : '', repoName(row)].filter(Boolean).slice(0, 2);
            return [tone(lead, t), ...facts.map(esc)].join(' · ');
        }
        /* The job row (Mu §5.1): sign, key + title, one sentence, time; actions on hover (pinned in Now). */
        function jobRow(row, now, {sub = null, actions = '', pinned = false, time = null} = {}) {
            const st = WASH.stateStyle(row.state);
            const stamp = row.updated_at || row.created_at;
            const sel = inJob && row.id === selectedId;
            return `<li class="w-row work-job-row${sel ? ' is-selected' : ''}" data-row-job="${esc(row.id)}" data-status="${st.status}"${sel ? ' aria-selected="true" data-selected' : ''}${pinned ? ' data-pinned' : ''}>
                ${signOf(row)}
                <a href="#/job/${esc(row.id)}" class="w-row__main" data-job-id="${esc(row.id)}" title="${esc(row.title)}">
                    <span class="w-row__title"><span class="w-row__key">${esc(jobKey(row))}</span>${esc(row.title)}</span>
                    <span class="w-row__sub"><span class="w-sr-only">${esc(cap(st.label))}: </span>${sub ?? rowSentence(row)}</span>
                </a>
                <span class="w-row__when" title="updated ${esc(when(stamp))}">${esc(time === null ? age(now - stamp) : time)}</span>
                ${actions ? `<span class="w-row__actions">${actions}</span>` : ''}
            </li>`;
        }
        /* A row in Now: why it waits, and the action the state asks for, always visible. */
        function needsRow(row, now) {
            const st = WASH.stateStyle(row.state);
            const failed = st.status === 'failed';
            const wanted = row.primary_action || PRIMARY_BY_STATE[row.state] || '';
            const act = (row.actions || []).find(a => a.id === wanted && a.enabled) || (row.actions || []).find(a => a.primary && a.enabled);
            const reason = row.limit?.summary || row.attention_summary || '';
            const lead = cap(reason ? 'limit reached' : failed && row.failed_from ? `failed in ${stateLabel(row.failed_from)}` : st.label);
            const detail = reason || row.attention_detail || '';
            const sub = `${tone(lead, failed ? 'danger' : 'attention')}${detail ? ` · <span${reason ? ' data-limit-summary' : ''} title="${esc(row.attention_detail || row.limit?.message || detail)}">${esc(detail)}</span>` : ''}${repoName(row) ? ` · ${esc(repoName(row))}` : ''}`;
            const actions = act ? `<button type="button" class="w-textbtn" data-open-action="${esc(row.id)}" data-action-id="${esc(act.id)}"${act.shortcut ? ` aria-keyshortcuts="${esc(act.shortcut)}"` : ''}>${esc(LABEL[act.id] || act.label)}</button>` : '';
            return jobRow(row, now, {sub, actions, pinned: true});
        }
        function group(id, title, body, more = '') {
            return `<section class="w-group work-group" aria-labelledby="${id}"><header class="w-group__head"><h2 class="w-group__title" id="${id}">${esc(title)}</h2>${more}</header>${body}</section>`;
        }
        /* A board cell (Mu §11b): a flat section beside the others, divided by hairlines. */
        function cell(id, title, body, {span = '', more = ''} = {}) {
            return `<section class="w-board__cell work-cell" aria-labelledby="${id}"${span ? ` data-span="${span}"` : ''}><header class="w-pane__head"><h2 class="w-pane__title" id="${id}">${esc(title)}</h2>${more}</header>${body}</section>`;
        }
        /* A meter as SVG geometry (no inline styles in the jobs app): a 4px track and the value. */
        function meter(pct, {tone = '', label = '', cls = 'work-meter'} = {}) {
            const v = Math.max(0, Math.min(100, Math.round(pct)));
            return `<svg class="${cls}" viewBox="0 0 100 4" preserveAspectRatio="none" role="img" aria-label="${esc(label)}"${tone ? ` data-tone="${tone}"` : ''}><rect class="work-meter__track" width="100" height="4" rx="2"></rect><rect class="work-meter__fill" width="${v}" height="4" rx="2"></rect></svg>`;
        }
        /* Running: what each job is doing, with its stages as a meter. */
        function runningRow(row, now) {
            const steps = stepsOf(row);
            const step = focusStep(steps);
            const done = steps.filter(s => s.status === 'done').length;
            const pct = steps.length ? Math.round(done / steps.length * 100) : 0;
            const what = [step?.label ? cap(step.label) : cap(stateLabel(row.state)), step?.detail || '', Number(row.cost_usd) ? money(row.cost_usd) : ''].filter(Boolean);
            const sub = `<span class="work-job-row__detail" title="${esc(step?.detail || '')}">${what.map(esc).join(' · ')}</span>${steps.length ? ` ${meter(pct, {tone: 'running', label: `${done} of ${steps.length} stages done`, cls: 'work-meter work-row-bar'})}` : ''}`;
            return jobRow(row, now, {sub});
        }
        /* Cost by day: each job's cost lands on the day it was last active (the board's trend,
         * not an invoice). One series, so no legend; each bar carries its day and amount. */
        function spendCell(now) {
            const DAY = 86400, days = 14;
            const start = Math.floor(now / DAY) * DAY - (days - 1) * DAY;
            const buckets = Array.from({length: days}, () => 0);
            for (const j of home.jobs) {
                const t = j.updated_at || j.created_at || 0, c = Number(j.cost_usd) || 0;
                if (!c || t < start) continue;
                buckets[Math.min(days - 1, Math.floor((t - start) / DAY))] += c;
            }
            const week = buckets.slice(-7).reduce((a, b) => a + b, 0);
            if (!buckets.some(Boolean)) return '';   // no cost yet: no $0.00 tile (Mu §5.5)
            const today = buckets[days - 1];
            const max = Math.max(...buckets, 0);
            const W = 10, G = 2, H = 36;
            const bars = buckets.map((v, i) => {
                const h = max ? Math.max(v ? 2 : 0, Math.round(v / max * H)) : 0;
                const day = new Date((start + i * DAY) * 1000).toLocaleDateString(undefined, {weekday: 'short', day: 'numeric'});
                return `<rect x="${i * (W + G)}" y="${H - h}" width="${W}" height="${h}" rx="2"><title>${esc(day)}: ${esc(money(v))}</title></rect>`;
            }).join('');
            const chart = max ? `<svg class="work-spark" viewBox="0 0 ${days * (W + G) - G} ${H}" width="${days * (W + G) - G}" height="${H}" role="img" aria-label="Job cost by day, last ${days} days; ${esc(money(week))} in the last 7">${bars}</svg>` : '';
            return cell('spend-h', 'Cost', `<span class="w-board__big work-num">${esc(money(week))}</span>
                <p class="work-meta">last 7 days${today ? ` · ${esc(money(today))} today` : ''}</p>${chart}`);
        }
        function renderHome() {
            if (!home) return;
            totalEl.textContent = home.jobs.length;
            onHome?.(home);
            const now = Date.now() / 1000;
            const needs = home.needs_you.slice().sort((a, b) => (a.updated_at || 0) - (b.updated_at || 0));
            const busy = new Set([...home.needs_you, ...home.running].map(j => j.id));
            const finished = home.jobs.filter(j => !busy.has(j.id)).sort((a, b) => b.updated_at - a.updated_at).slice(0, 6);
            const finishedRow = row => {
                const st = WASH.stateStyle(row.state);
                const took = row.updated_at && row.created_at ? duration((row.updated_at - row.created_at) * 1000) : '';
                const sub = [tone(cap(st.label), st.status === 'failed' ? 'danger' : ''), Number(row.cost_usd) ? esc(money(row.cost_usd)) : '', took ? esc(took) : '', esc(repoName(row))].filter(Boolean).join(' · ');
                return jobRow(row, now, {sub});
            };
            // The setup reminder (#work-setup-cta) is a persistent node: keep it across re-renders.
            const cta = document.getElementById('work-setup-cta');
            if (!home.jobs.length) {
                boardEl.innerHTML = `${cell('needs-you', 'Now', '<ul class="w-rows work-now-rows"></ul>')}
                    <div class="w-empty work-empty work-first-run" data-tone="accent"><div class="w-empty__art" aria-hidden="true">${icon('briefcase', '')}</div><h3 class="w-empty__title">No jobs yet</h3><p class="w-empty__body">Delegate engineering work to agents that plan, implement, check and review it in isolated sandboxes. You step in only when a job needs you.</p><div class="w-empty__actions"><button type="button" class="w-textbtn" data-new-job>Create a job</button><a class="w-textbtn" data-tone="quiet" href="#/boards">Browse tickets</a></div></div>`;
            } else {
                boardEl.innerHTML = `${cell('needs-you', 'Needs you', `<ul class="w-rows work-now-rows">${needs.map(r => needsRow(r, now)).join('')}</ul>${needs.length ? '' : '<p class="w-group__empty work-quiet-line">Nothing needs you.</p>'}`)}
                    <div class="w-board work-board-grid">
                    ${cell('running-now', 'Running', home.running.length ? `<ul class="w-rows work-running">${home.running.map(r => runningRow(r, now)).join('')}</ul>` : '<p class="w-group__empty work-quiet-line">Nothing running.</p>')}
                    ${spendCell(now)}
                    ${finished.length ? cell('recent-h', 'Recently finished', `<ul class="w-rows">${finished.map(finishedRow).join('')}</ul>`, {more: '<a href="#/jobs" class="w-group__more">All jobs</a>'}) : ''}
                </div>`;
            }
            if (cta) boardEl.querySelector('.work-now-rows')?.prepend(cta);
            boardEl.querySelector('.work-quiet-line')?.toggleAttribute('hidden', !!(cta && !cta.hidden));
            bindHome(boardEl);
            boardEl.querySelector('[data-new-job]')?.addEventListener('click', () => document.getElementById('work-new')?.click());
            renderList();
            renderJobsPage();
            ui.register('page:dashboard', home.jobs.map(j => ({id:`open:${j.id}`, label:`Open ${jobKey(j)} · ${j.title}`, group:'Jobs', run:() => open(j.id)})));
        }
        /* The list pane (Mu §11b): every job as a compact row, grouped Now / Running / Recent,
         * beside the board and beside the open job. The filter narrows all three groups. */
        let listQuery = '', listNeedsOnly = false;
        function renderList() {
            if (!listEl || !home) return;
            const now = Date.now() / 1000;
            const match = j => (!listNeedsOnly || ['waiting', 'failed'].includes(stateStatus(j.state))) &&
                (!listQuery || `${jobKey(j)} ${j.title} ${stateLabel(j.state)} ${j.repository || ''} ${j.origin_session || ''}`.toLowerCase().includes(listQuery.toLowerCase()));
            const needs = home.needs_you.filter(match).sort((a, b) => (a.updated_at || 0) - (b.updated_at || 0));
            const running = home.running.filter(match);
            const busy = new Set([...home.needs_you, ...home.running].map(j => j.id));
            const rest = home.jobs.filter(j => !busy.has(j.id) && match(j)).sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));
            const compact = row => {
                const st = WASH.stateStyle(row.state);
                const t = st.status === 'waiting' ? 'attention' : st.status === 'failed' ? 'danger' : '';
                const sub = [`<span class="work-mono">${esc(jobKey(row))}</span>`, tone(cap(row.limit?.summary ? 'limit reached' : st.label), t), Number(row.cost_usd) ? esc(money(row.cost_usd)) : ''].filter(Boolean).join(' · ');
                return jobRow(row, now, {sub});
            };
            const groups = [['Now', needs], ['Running', running], ['Recent', rest]].filter(([, rows]) => rows.length);
            const scroll = listEl.closest('.w-pane')?.scrollTop || 0;
            listEl.innerHTML = groups.length ? groups.map(([title, rows]) => `<section class="work-list-group" aria-label="${title}"><h3 class="work-list-group__title">${title} <span class="work-num">${rows.length}</span></h3><ul class="w-rows" data-spacing="loose">${rows.map(compact).join('')}</ul></section>`).join('')
                : `<p class="w-pane__empty">${home.jobs.length ? 'No jobs match.' : 'No jobs yet.'}</p>`;
            if (listCountEl) listCountEl.textContent = home.jobs.length ? String(home.jobs.length) : '';
            bindHome(listEl);
            const pane = listEl.closest('.w-pane');
            if (pane) pane.scrollTop = scroll;
        }
        if (listEl) {
            const filter = document.getElementById('work-list-filter');
            const needsOnly = document.getElementById('work-list-needs');
            if (filter) filter.oninput = () => { listQuery = filter.value; renderList(); };
            if (needsOnly) needsOnly.onchange = () => { listNeedsOnly = needsOnly.checked; renderList(); };
        }
        function jobList(rows, now, actions) {
            if (!rows.length) return `<p class="w-group__empty work-empty-line">No jobs match. Create one with New job, or import tickets from a Jira board.</p>`;
            return `<ul class="w-rows work-jobs-list">${rows.map(row => {
                const sub = `${rowSentence(row)}${Number(row.cost_usd) ? ` · ${esc(money(row.cost_usd))}` : ''}${row.origin_session ? ` · <span title="session">${esc(row.origin_session)}</span>` : ''}`;
                return jobRow(row, now, {sub, actions: actions ? actions(row) : ''});
            }).join('')}</ul>`;
        }
        /* A row opens the job in place (Mu §11b): the main pane shows it, the list stays. */
        function openRow(id) { return open(id); }
        function bindHome(root) {
            root.querySelectorAll('[data-job-id]').forEach(b => { b.onclick = e => {
                if (e.metaKey || e.ctrlKey || e.shiftKey || e.button === 1) return;   // new tab / window: the link's own href
                e.preventDefault(); openRow(b.dataset.jobId);
            }; });
            root.querySelectorAll('[data-row-job]').forEach(tr => { tr.onclick = e => { if (!e.target.closest('a,button,input')) openRow(tr.dataset.rowJob); }; });
            root.querySelectorAll('[data-open-action]').forEach(b => { b.onclick = async () => {
                if (onOpen) onOpen(b.dataset.openAction);
                if (await select(b.dataset.openAction) !== true) return;
                const primary = job?.actions.find(a => a.id === b.dataset.actionId && a.enabled) || job?.actions.find(a => a.primary && a.enabled);
                if (primary) await invoke(primary.id);
            }; });
        }
        function renderJobsPage() {
            if (!jobsEl || !home) return;
            const now = Date.now() / 1000;
            const sessionNames = [...new Set(home.jobs.map(j => j.origin_session || ''))].sort();
            const rows = home.jobs.filter(j => (!jobsNeedsOnly || ['waiting', 'failed'].includes(stateStatus(j.state))) &&
                (!jobsSession || (j.origin_session || '') === (jobsSession === '-' ? '' : jobsSession)) &&
                (!jobsQuery || `${jobKey(j)} ${j.title} ${stateLabel(j.state)} ${j.repository || ''} ${j.origin_session || ''} ${j.id}`.toLowerCase().includes(jobsQuery.toLowerCase())))
                .sort((a, b) => (b.updated_at || 0) - (a.updated_at || 0));
            const focused = document.activeElement?.id;
            jobsEl.innerHTML = `<div class="work-toolbar work-jobs-filters" role="search" aria-label="Filter jobs">
                    <input class="w-input" type="search" id="work-jobs-filter" aria-label="Filter jobs" placeholder="Filter jobs…" value="${esc(jobsQuery)}">
                    <label class="work-inline-field"><span class="w-sr-only">Session</span><span class="w-select" data-size="sm"><select class="w-select__control" id="work-jobs-session" aria-label="Filter by session"><option value="">All sessions</option>${sessionNames.map(n => `<option value="${esc(n || '-')}" ${(n || '-') === jobsSession ? 'selected' : ''}>${esc(n || 'no session')}</option>`).join('')}</select></span></label>
                    <label class="w-check"><input class="w-check__input" type="checkbox" id="work-jobs-needs" ${jobsNeedsOnly ? 'checked' : ''}><span>Only jobs that need me</span></label>
                    <span class="work-spacer"></span><span class="work-meta">${rows.length === home.jobs.length ? `${home.jobs.length} job${home.jobs.length === 1 ? '' : 's'}` : `${rows.length} of ${home.jobs.length}`}</span></div>
                ${jobList(rows, now, row => {
                    const del = (row.actions || []).find(a => a.id === 'delete');
                    return `<button type="button" class="w-textbtn" data-tone="quiet" data-row-archive="${esc(row.id)}" aria-label="Archive ${esc(jobKey(row))}" title="Archive">Archive</button>
                        <button type="button" class="w-textbtn" data-tone="danger" data-row-delete="${esc(row.id)}" aria-label="Delete ${esc(jobKey(row))}" title="Delete" ${del?.enabled ? '' : 'disabled'}>Delete</button>`;
                })}`;
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

        /* ------------------------------------------------------------ list keys */
        /* j / k move through the list pane (or the jobs list) and open the job in place. */
        let listRoute = '';
        function moveRow(step) {
            const scope = listRoute === 'jobs' ? document.querySelector('.work-route[data-route="jobs"]') : listEl;
            const rows = [...(scope || document).querySelectorAll('.work-job-row')].filter(r => r.offsetParent !== null);
            if (!rows.length) return;
            const at = rows.findIndex(r => r.matches('[data-selected], :focus-within'));
            const next = rows[Math.max(0, Math.min(rows.length - 1, at < 0 ? 0 : at + step))];
            next.querySelector('.w-row__main')?.focus();
            next.scrollIntoView({block: 'nearest'});
            if (listRoute !== 'jobs') open(next.dataset.rowJob);
        }
        /* work.js: the route changed. Now, Jobs and a job keep j / k. */
        function listMode(name) {
            listRoute = ['dashboard', 'jobs', 'job'].includes(name) ? name : '';
            const wasInJob = inJob;
            inJob = name === 'job';
            if (wasInJob !== inJob) renderList();
            if (!listRoute) { ui.register('page:list', []); return; }
            ui.register('page:list', [
                {id: 'list:next', label: 'Next job', group: 'Jobs', key: 'j', run: () => moveRow(1)},
                {id: 'list:prev', label: 'Previous job', group: 'Jobs', key: 'k', run: () => moveRow(-1)},
            ], 20);
        }

        /* ------------------------------------------------------------ job page */
        /* Job detail (Mu §10.2): the page header carries the title and the state sentence.
         * From 1200px the page is two columns: the work itself on the left (why it waits, a
         * pending decision, the tabs: notes, plan, diff, review, checks…) and a sticky summary
         * on the right (the action the state asks for, the facts, the stages, the receipts).
         * Narrower, the summary comes first. */
        function shell() {
            detailEl.innerHTML = `<div class="work-job-layout">
                <aside class="work-job-side" aria-label="Job summary">
                    <div class="work-action-panel" data-actions></div>
                    <header class="work-detail-head" data-job-header></header>
                    <section class="work-pipeline" aria-labelledby="work-pipeline-h" data-pipeline></section>
                    <nav class="work-job-receipts" aria-label="Receipts" data-receipts></nav>
                </aside>
                <div class="work-job-main">
                <div data-job-attention></div>
                <div data-environments></div>
                <div data-approval></div>
                <div class="work-detail-tabs"><div role="tablist" aria-label="Job views" class="w-tabs work-tabs">${tabs.map(t => `<button type="button" role="tab" class="w-tabs__tab work-tab" id="work-tab-${t}" data-tab="${t}" aria-selected="${t === tab}" aria-controls="work-panel-${t}" tabindex="${t === tab ? 0 : -1}">${labels[t]}<span class="work-tab-badge" data-tab-badge="${t}"></span></button>`).join('')}</div>
                ${tabs.map(t => `<div role="tabpanel" class="w-tabs__panel work-tabpanel" id="work-panel-${t}" aria-labelledby="work-tab-${t}" data-panel="${t}" tabindex="0" ${t === tab ? '' : 'hidden'}></div>`).join('')}</div>
                </div></div>`;
            // The job's context (actions, facts, stages, receipts) lives in the aside pane.
            const side = detailEl.querySelector('.work-job-side');
            if (asideEl && side) asideEl.replaceChildren(side);
            const list = q('[role=tablist]');
            qa('[data-tab]').forEach(b => { b.onclick = () => selectTab(b.dataset.tab); });
            list.addEventListener('keydown', e => {
                const i = tabs.indexOf(tab); let n = -1;
                if (e.key === 'ArrowRight') n = (i + 1) % tabs.length; else if (e.key === 'ArrowLeft') n = (i - 1 + tabs.length) % tabs.length;
                else if (e.key === 'Home') n = 0; else if (e.key === 'End') n = tabs.length - 1;
                if (n < 0) return; e.preventDefault(); selectTab(tabs[n]); list.querySelector(`[data-tab="${tabs[n]}"]`)?.focus();
            });
        }
        /* "Open a shell" (manual edits): the job's worktree is on this machine, so the command
         * is a cd you paste into your own terminal. Nothing runs from the browser. */
        let shellOpen = false;
        const shq = v => `'${String(v).replace(/'/g, `'\\''`)}'`;
        const shellCommand = j => `cd ${shq(j.worktree)}`;
        function shortPath(path) {
            const p = String(path || '');
            return p.length > 42 ? `…${p.slice(-41)}` : p;
        }
        function shellBox(j) {
            const running = stateStatus(j.state) === 'active';
            return `<div class="work-shellbox" role="group" aria-label="Open a shell in the job's working directory">
                <p class="work-shellbox__lede">Paste this into a terminal on this machine:</p>
                <div class="work-shellbox__cmd"><code class="work-mono">${esc(shellCommand(j))}</code>
                    <button type="button" class="w-btn" data-variant="secondary" data-size="sm" data-shell-copy>${icon('copy')}<span>Copy</span></button></div>
                <p class="work-shellbox__note">${running ? '<strong>The job is running.</strong> Pause it first so your edits and the agent’s don’t collide. ' : ''}`
                + `Commit your edits on <code class="work-mono">${esc(j.branch || 'the job branch')}</code> before you resume: the checks fail on uncommitted changes.</p></div>`;
        }
        async function copyShell(j, button) {
            const text = shellCommand(j);
            try { await navigator.clipboard.writeText(text); toast('Shell command copied', 'ok'); }
            catch (_) {
                const ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta); ta.select();
                const ok = document.execCommand('copy'); ta.remove();
                toast(ok ? 'Shell command copied' : 'Couldn’t copy: select the command and copy it', ok ? 'ok' : 'attention');
            }
            if (button) { const label = button.querySelector('span'); if (label) { label.textContent = 'Copied'; setTimeout(() => { label.textContent = 'Copy'; }, 1500); } }
        }
        function renderState() {
            const steps = stepsOf(job);
            const models = Object.entries(job.stage_models || {}).filter(([, m]) => m?.model);
            const failed = job.state === 'failed';
            const prTone = job.pr?.state === 'merged' ? 'ok' : job.pr?.state === 'closed' ? 'danger' : 'accent';
            // Mu §10.2: the page header carries the job's title (work.js) and its state sentence;
            // the body opens with a quiet facts line (key, repository, branch, session, cost).
            const st = WASH.stateStyle(job.state);
            const stTone = st.status === 'waiting' ? 'attention' : st.status === 'failed' ? 'danger' : st.status === 'done' ? 'quiet' : '';
            const prLabel = /^https?:\/\//.test(job.pr?.url || '') ? `PR${job.pr.number ? ` #${job.pr.number}` : ''}${job.pr.state && job.pr.state !== 'open' ? ` · ${job.pr.state}` : ''}` : '';
            window.WashHead?.set({ state: [
                { text: cap(st.label), tone: stTone },
                failed && job.failed_from ? { text: `failed in ${stateLabel(job.failed_from)}`, tone: 'danger' } : null,
                job.paused_from ? { text: `paused in ${stateLabel(job.paused_from)}` } : null,
                job.fix_attempts ? { text: `fix attempts ${job.fix_attempts}` } : null,
                job.verdict ? { text: cap(verdictOf(job.verdict)[1]) } : null,
                prLabel ? { text: prLabel, href: job.pr.url, tone: prTone === 'danger' ? 'danger' : '' } : null,
            ] });
            // The summary column: facts as a list (Mu §5.3), the agents per stage, and "why it
            // waits" as prose at the top of the work column (not a second state chip).
            const facts = [
                ['Key', `<span class="work-mono work-detail-key" title="job ${esc(job.id)}">${esc(jobKey(job))}</span>`],
                // A plain folder runs on a git snapshot of it; its result is a patch to apply there.
                job.folder_snapshot ? ['Folder', `<span class="work-mono" title="folder ${esc(job.folder_snapshot.source_path)}">${esc(job.folder_snapshot.source_path)}</span>`] : null,
                job.folder_snapshot ? ['Result', `<a href="/api/jobs/${encodeURIComponent(job.id)}/patch" download data-job-patch>${icon('download')} Download patch</a> <span class="work-meta">apply in the folder with <code>git apply</code></span>`] : null,
                job.repository && !job.folder_snapshot ? ['Repository', `<span class="work-mono" title="repository ${esc(job.repository)}">${esc(job.repository)}</span>`] : null,
                // Extra sources (mu/jobs/multi_source.py): each its own repository, PR or patch.
                (job.sources || []).length ? ['Sources', (job.sources || []).map(src => {
                    const result = src.pr?.url ? `<a href="${esc(src.pr.url)}" target="_blank" rel="noopener">${icon('git-pull-request')} PR${src.pr.number ? ` #${esc(src.pr.number)}` : ''}</a>`
                        : (src.folder || src.branch) ? `<a href="/api/jobs/${encodeURIComponent(job.id)}/patch?source=${encodeURIComponent(src.name)}" download>${icon('download')} patch</a>` : '';
                    return `<span class="work-mono" title="${esc(src.input)}">/sources/${esc(src.name)}</span> <span class="work-meta">${esc(src.input)}</span> ${result}`;
                }).join('<br>')] : null,
                job.branch ? ['Branch', `<span class="work-mono" title="branch">${esc(job.branch)}</span>`] : null,
                job.origin_session ? ['Session', `<span title="session">${esc(job.origin_session)}</span>`] : null,
                ['Budget', budgetText(job)],
                (job.attachments || []).length ? ['Attachments', (job.attachments || []).map(a =>
                    `<a class="work-mono" href="/api/jobs/${encodeURIComponent(job.id)}/attachments/${encodeURIComponent(a.name)}" download title="${esc(a.name)}">${esc(a.name)}</a>`).join(' · ')] : null,
                prLabel ? ['Pull request', `<a href="${esc(job.pr.url)}" target="_blank" rel="noopener" data-pr-link>${icon('git-pull-request')} ${esc(prLabel)}</a>`] : null,
                ENV ? ['Environment', ENV.headerPill(job.environments || job.build_environments, envOpen)] : null,
                job.worktree ? ['Working directory', `<span class="work-mono work-detail-path" title="${esc(job.worktree)}">${esc(shortPath(job.worktree))}</span> <button type="button" class="w-textbtn" data-open-shell aria-expanded="${shellOpen}">${icon('terminal')}<span>Open a shell</span></button>`] : null,
                ['Agents', `<span class="work-detail-agents">${window.MuJobAgents ? window.MuJobAgents.stageText(job) : (models.length ? models.map(([s, m]) => `${esc(s)} <code>${esc(m.model)}</code>`).join(' · ') : 'unset')}</span>`],
            ].filter(Boolean);
            q('[data-job-header]').innerHTML = `
                <dl class="w-facts work-detail-facts">${facts.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join('')}</dl>
                ${shellOpen && job.worktree ? shellBox(job) : ''}
                <button type="button" class="w-textbtn" data-tone="quiet" data-stage-models>${icon('sliders-horizontal')}<span>Job settings</span></button>`;
            // The Review tab opens with the review summary, which is where a finished review's
            // attention text comes from: don't say it twice there (work.css hides the banner).
            const head = String(job.attention_headline || job.attention_detail || '').replace(/…$/, '').slice(0, 120);
            detailEl.dataset.attentionFromReview = String(!!(head && String(job.review?.summary || '').startsWith(head)));
            q('[data-job-attention]').innerHTML = job.attention_detail && !(approvalOf(job) && job.state === 'needs_input') && !(job.limit && job.state === 'needs_input') ? attentionBanner(job, failed) : '';
            q('[data-receipts]').innerHTML = `<h2 class="w-group__title">Receipts</h2>
                <a class="w-textbtn" data-tone="quiet" href="#/job/${esc(job.id)}?tab=costs" data-receipt-tab="costs">Cost and audit</a>
                <a class="w-textbtn" data-tone="quiet" href="#/job/${esc(job.id)}?tab=checks" data-receipt-tab="checks">Checks and evidence</a>
                <a class="w-textbtn" data-tone="quiet" href="/jobs/trace?job=${encodeURIComponent(job.id)}">Job analysis</a>`;
            qa('[data-receipt-tab]').forEach(a => { a.onclick = e => { e.preventDefault(); selectTab(a.dataset.receiptTab); q('.work-detail-tabs')?.scrollIntoView({block: 'start', behavior: 'smooth'}); }; });
            q('[data-stage-models]').onclick = () => window.muOpenStageModels?.();
            const shellBtn = q('[data-open-shell]');
            if (shellBtn) shellBtn.onclick = () => { shellOpen = !shellOpen; renderState(); if (shellOpen) q('[data-shell-copy]')?.focus(); };
            const copyBtn = q('[data-shell-copy]');
            if (copyBtn) copyBtn.onclick = () => copyShell(job, copyBtn);
            const envPill = q('[data-env-pill]');
            if (envPill) envPill.onclick = () => { envOpen = !envOpen; renderState(); };
            q('[data-pipeline]').innerHTML = `<h2 class="w-group__title" id="work-pipeline-h">Stages</h2>${pipelineStepper(steps)}`;
            qa('[data-step]').forEach(g => { g.onclick = () => selectTab(stepTabs[g.dataset.step] || 'session'); });
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
            const tone = cap && frac >= 0.9 ? 'danger' : cap && frac >= 0.7 ? 'caution' : '';
            const active = Number(b.active_seconds);
            return `<span class="work-detail-budget" data-job-budget>
                <span class="work-detail-cost"${tone ? ` data-tone="${tone}"` : ''} title="agent cost so far${cap ? ` of the ${money(cap)} job cap` : ' (no cost cap)'}"><span class="work-num">${money(b.cost_usd)}</span><span class="work-meta"> / ${cap ? money(cap) : 'no cap'}</span></span>
                ${cap ? meter(frac * 100, {tone: tone === 'caution' ? 'attention' : tone, label: `${Math.round(frac * 100)}% of the cost cap`, cls: 'work-meter work-budget-bar'}) : ''}
                ${Number.isFinite(active) && j.budget ? `<span class="work-meta work-num" title="worker-active time${b.max_runtime_seconds ? ` (limit ${duration(b.max_runtime_seconds * 1000)})` : ''}">${icon('clock')}${duration(active * 1000)}${b.max_runtime_seconds ? ` / ${duration(b.max_runtime_seconds * 1000)}` : ''}</span>` : ''}</span>`;
        }
        /* Approval / limit / environment prompts are decision blocks (Mu §7.6): the one tinted
         * surface on the job page; the sign and the title say why it waits (no second "needs you"). */
        function stateCard({id, title, glyph = '◆', tone = 'attention', data = '', body = '', foot = ''}) {
            return `<section class="w-card work-approval" data-state="${tone}" role="alert" aria-labelledby="${id}" ${data}>
                <header class="w-card__head"><span class="w-sign" data-status="${tone === 'danger' ? 'failed' : 'waiting'}" aria-hidden="true">${glyph}</span><h2 class="w-card__title" id="${id}">${title}</h2></header>
                <div class="w-card__body">${body}</div>${foot ? `<footer class="w-card__foot">${foot}</footer>` : ''}</section>`;
        }
        function renderEnvironmentApproval(host) {
            const ea = job.environment_approval;
            const approve = job.actions.find(a => a.id === 'approveEnvironment'), reject = job.actions.find(a => a.id === 'rejectEnvironment');
            if (!ea || !approve?.enabled) return '';
            const kind = ea.kind === 'build' ? 'Dockerfile build' : ea.kind === 'egress' ? 'network egress' : 'image';
            return stateCard({id:'work-env-approval-h', glyph:'⬢', data:'data-env-approval',
                title:ea.kind === 'egress' ? 'The repository asks for network egress (stages run without it until approved)' : `The repository asks to ${ea.kind === 'build' ? 'build and use' : 'use'} a build environment${ea.blocking === false ? ' (this job runs without it; approving applies to future jobs)' : ''}`,
                body:`<dl class="work-env-approval-facts">
                    <dt>Kind</dt><dd>${esc(kind)}${ea.origin ? ` <span class="work-meta">(${esc(ea.origin === 'devcontainer' ? 'devcontainer.json' : '.mucli/jobs.toml')})</span>` : ''}</dd>
                    ${ea.label ? `<dt>What</dt><dd class="work-mono">${esc(ea.label)}</dd>` : ''}
                    ${ea.ref ? `<dt>${ea.kind === 'build' ? 'Tag' : ea.kind === 'egress' ? 'Domains' : 'Image'}</dt><dd class="work-mono">${esc(ea.ref)}</dd>` : ''}
                    <dt>Fingerprint</dt><dd class="work-mono">${esc(ea.fingerprint)}</dd>
                    ${(ea.ignored_features || []).length ? `<dt>Ignored</dt><dd>devcontainer features: ${esc(ea.ignored_features.join(', '))}</dd>` : ''}
                </dl>${ea.message ? `<p class="work-meta">${esc(ea.message)}</p>` : ''}`,
                foot:`${reject ? actionButton({...reject, primary:false}, {variant:'secondary'}) : ''}${actionButton({...approve, primary:true})}`});
        }
        /* Jobs run unattended: a failed template job setup is a warning, never a park. */
        function renderSetupWarning() {
            const w = job.setup_warning;
            if (!w) return '';
            return `<section class="w-card work-approval" data-state="caution" role="status" aria-labelledby="work-setup-warning-h" data-setup-warning>
                <header class="w-card__head"><span class="w-chip" data-tone="caution">${chipGlyph('?')}<span>Warning</span></span><h2 class="w-card__title" id="work-setup-warning-h">Job setup failed; the job continued without it</h2></header>
                <div class="w-card__body"><p class="work-meta">${esc(w.message || `\`${w.command || ''}\` failed in ${w.binding || 'the build environment'}.`)}</p></div></section>`;
        }
        function renderLimitBanner() {
            const lim = job.limit;
            const raise = job.actions.find(a => a.id === 'raiseLimit'), cancel = job.actions.find(a => a.id === 'discard');
            if (!lim || job.state !== 'needs_input' || !raise?.enabled) return '';
            const input = raise.input || {};
            return stateCard({id:'work-limit-h', data:'data-limit-approval', title:esc(lim.summary || 'Limit reached'),
                body:`<p>${esc(lim.message || '')}</p>
                    <div class="w-field work-limit-field"><label class="w-field__label" for="work-limit-amount">${esc(input.label || 'Additional amount')}</label>
                    <input id="work-limit-amount" type="text" inputmode="decimal" class="w-input work-limit-amount" data-limit-amount value="${esc(input.value || '')}" aria-describedby="work-limit-hint">
                    ${input.hint ? `<p id="work-limit-hint" class="w-field__hint">${esc(input.hint)}</p>` : ''}</div>`,
                foot:`${cancel ? actionButton({...cancel, primary:false}, {variant:'secondary'}) : ''}${actionButton({...raise, primary:true})}`});
        }
        function renderApproval() {
            const host = q('[data-approval]');
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
            if (envHtml && !(job.environment_approval?.blocking === false && approvalOf(job))) { host.dataset.eventId = ''; host.innerHTML = envHtml; bindCommands(host); return; }
            const ap = approvalOf(job);
            const approve = job.actions.find(a => a.id === 'approveTool'), deny = job.actions.find(a => a.id === 'denyTool');
            if (!ap || (!approve?.enabled && !deny?.enabled)) { host.dataset.eventId = ''; host.innerHTML = renderSetupWarning(); return; }
            // planos ApprovalBanner: Approve · optional reason for the agent · Deny. Keep a typed reason across re-renders.
            const prev = host.querySelector('[data-deny-reason]');
            const reason = prev && host.dataset.eventId === String(ap.event_id ?? '') ? prev.value : '';
            const hadFocus = prev && document.activeElement === prev;
            const reasonLabel = inputOf(deny || {id:'denyTool'})?.label || 'Reason for the agent (optional)';
            host.dataset.eventId = String(ap.event_id ?? '');
            host.innerHTML = stateCard({id:'work-approval-h', title:`The agent asks to use <code>${esc(ap.tool_name || 'a tool')}</code>`,
                body:`<pre class="work-code">${esc(typeof ap.tool_args === 'string' ? ap.tool_args : JSON.stringify(ap.tool_args || {}, null, 2))}</pre>${ap.reason || ap.detail ? `<p class="work-meta">${esc(ap.reason || ap.detail)}</p>` : ''}
                    ${deny ? `<div class="w-field"><label class="w-field__label" for="work-deny-reason">${esc(reasonLabel)}</label><input id="work-deny-reason" type="text" class="w-input work-deny-reason" maxlength="2000" data-deny-reason placeholder="Reason for the agent if you deny (optional)" value="${esc(reason)}" ${deny.enabled ? '' : 'disabled'}></div>` : ''}
                    ${approve?.enabled === false && approve?.reason ? `<p class="work-meta">${esc(approve.reason)}</p>` : ''}`,
                foot:`${deny ? actionButton({...deny, primary:false}, {variant:'secondary'}) : ''}${approve ? actionButton({...approve, primary:true}) : ''}`});
            bindCommands(host);
            const box = host.querySelector('[data-deny-reason]');
            if (box) {
                if (hadFocus) { box.focus(); box.setSelectionRange(box.value.length, box.value.length); }
                box.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); invoke('denyTool'); } });
            }
        }
        /* Action bar: the enabled primary action(s) as buttons — the first is the page's one
         * primary — and every other action in the overflow menu (disabled ones with their reason). */
        function renderActions() {
            const bar = q('[data-actions]');
            // Tool approval lives in the approval banner; hidden actions (markFindings, markViewed) never show.
            const inLimitBanner = !!(job.limit && job.state === 'needs_input');
            const list = job.actions.filter(a => !hiddenOf(a) && !BANNER_ACTIONS.has(a.id) && !(inLimitBanner && LIMIT_BANNER_ACTIONS.has(a.id)));
            const banner = !!q('[data-approval] .work-approval');
            const primaries = list.filter(a => a.enabled && a.primary);
            const others = list.filter(a => a.enabled && !a.primary);
            const disabled = list.filter(a => !a.enabled);
            const menuItems = [...others.map(actionMenuItem), ...(disabled.length ? [`${others.length ? '<hr class="w-menu__separator">' : ''}<div class="w-menu__heading">Not available now</div>`, ...disabled.map(actionMenuItem)] : [])].join('');
            const wasOpen = !!bar.querySelector('[data-overflow-menu]:not([hidden])');
            bar.innerHTML = `<div class="w-btn-row work-actions" role="toolbar" aria-label="Job actions">
                ${primaries.map((a, i) => actionButton(a, {variant:i === 0 && !banner ? '' : (washTone(toneOf(a)) === 'danger' ? 'secondary' : 'tonal')})).join('') || '<span class="work-meta">Nothing to do right now.</span>'}
                ${menuItems ? `<div class="work-overflow"><button type="button" class="w-textbtn" aria-haspopup="menu" aria-expanded="${wasOpen}" aria-controls="work-actions-menu" data-overflow-toggle><span>More actions</span><span aria-hidden="true">▾</span></button>
                    <div class="w-menu work-overflow__menu" role="menu" id="work-actions-menu" aria-label="More job actions" data-overflow-menu ${wasOpen ? '' : 'hidden'}>${menuItems}</div></div>` : ''}</div>`;
            bindCommands(bar);
            bindOverflow(bar);
        }
        /* Menu button behaviour: Esc / outside click close it, arrows move between items. */
        function bindOverflow(root) {
            const toggle = root.querySelector('[data-overflow-toggle]'), menu = root.querySelector('[data-overflow-menu]');
            if (!toggle || !menu) return;
            const items = () => [...menu.querySelectorAll('[role=menuitem]')];
            const close = (focus = true) => { menu.hidden = true; toggle.setAttribute('aria-expanded', 'false'); document.removeEventListener('mousedown', outside, true); if (focus) toggle.focus(); };
            const outside = e => { if (!root.querySelector('.work-overflow')?.contains(e.target)) close(false); };
            const openMenu = () => { menu.hidden = false; toggle.setAttribute('aria-expanded', 'true'); document.addEventListener('mousedown', outside, true); items()[0]?.focus(); };
            toggle.onclick = () => (menu.hidden ? openMenu() : close());
            toggle.addEventListener('keydown', e => { if (e.key === 'ArrowDown' && menu.hidden) { e.preventDefault(); openMenu(); } });
            menu.addEventListener('keydown', e => {
                const list = items(), i = list.indexOf(document.activeElement);
                if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
                else if (e.key === 'ArrowDown') { e.preventDefault(); list[(i + 1) % list.length]?.focus(); }
                else if (e.key === 'ArrowUp') { e.preventDefault(); list[(i - 1 + list.length) % list.length]?.focus(); }
                else if (e.key === 'Home') { e.preventDefault(); list[0]?.focus(); }
                else if (e.key === 'End') { e.preventDefault(); list[list.length - 1]?.focus(); }
                else if (e.key === 'Tab') close(false);
            });
            menu.querySelectorAll('[role=menuitem]').forEach(b => b.addEventListener('click', () => close(false)));
            if (!menu.hidden) document.addEventListener('mousedown', outside, true);
        }
        function renderBadges() {
            const set = (t, html) => { const el = q(`[data-tab-badge="${t}"]`); if (el) el.innerHTML = html ? ` ${html}` : ''; };
            const dot = (tone, label) => `<span class="w-status" data-tone="${tone}" aria-label="${label}">${tone === 'running' ? '<span class="w-inkdrop" data-size="sm" aria-hidden="true"></span>' : `<span class="w-status__glyph" aria-hidden="true">${tone === 'attention' ? '◆' : tone === 'ok' ? '✓' : '✗'}</span>`}</span>`;
            const live = stateStatus(job.state) === 'active' && ['planning', 'implementing', 'reviewing'].includes(job.state);
            set('session', live ? dot('running', 'live') : approvalOf(job) ? dot('attention', 'approval needed') : '');
            set('plan', job.plan?.draft ? dot('attention', 'unsaved draft') : job.state === 'plan_review' ? dot('attention', 'awaiting approval') : '');
            if (job.diff) { const files = parsePatch(diffPatch(job)); const a = files.reduce((n, f) => n + f.added, 0), r = files.reduce((n, f) => n + f.removed, 0);
                set('diff', files.length ? `<span class="work-diffstat">${job.diff.live ? dot('running', 'live') : ''}<span class="work-add">+${a}</span><span class="work-del">−${r}</span></span>` : ''); }
            if (job.review) { const open = (job.review.findings || []).filter(f => (f.disposition || 'open') === 'open');
                const worst = open.reduce((w, f) => (VOCAB.severity[f.severity]?.rank ?? 9) < (VOCAB.severity[w]?.rank ?? 9) ? f.severity : w, '');
                set('review', `<span class="w-count"${worst ? ` data-tone="${severityTone(worst)}"` : ''} aria-label="${open.length} open findings">${open.length}</span>`); }
            if (job.checks) { const last = (job.checks.runs || []).slice(-1)[0];
                set('checks', job.state === 'checking' ? dot('running', 'running') : last ? (last.passed ? dot('ok', 'passed') : dot('danger', 'failed')) : ''); }
            set('diagnostics', job.state === 'failed' ? '<span class="w-count" data-tone="danger" aria-label="failed">!</span>' : '');
        }
        function registerCommands() {
            ui.register('page:job', [
                ...job.actions.filter(a => !hiddenOf(a) || ENV_ACTIONS.has(a.id)).map(a => ({id:`act:${a.id}`, label:LABEL[a.id] || a.label, group:'Job', key:a.shortcut || undefined, enabled:a.enabled, reason:a.reason, run:() => invoke(a.id)})),
                ...(job.worktree ? [{id:'job:shell', label:'Copy a shell command for the working directory', group:'Job', run:() => copyShell(job)}] : []),
                ...(ENV ? [{id:'env:panel', label:envOpen ? 'Hide build environments' : 'Show build environments', group:'Job', run:() => { envOpen = !envOpen; renderState(); }}] : []),
                ...tabs.map(t => ({id:`tab:${t}`, label:`Show ${labels[t].toLowerCase()}`, group:'Tab', run:() => selectTab(t)})),
            ], 10);
        }

        /* ------------------------------------------------------------ plan */
        const emptyState = (title, body = '', tone = 'accent', glyph = 'sparkles') => `<div class="w-empty work-empty" data-tone="${tone}" data-size="sm"><div class="w-empty__art" aria-hidden="true">${icon(glyph, '')}</div><h3 class="w-empty__title">${esc(title)}</h3>${body ? `<p class="w-empty__body">${esc(body)}</p>` : ''}</div>`;
        const banner = (tone, glyph, html, role = '') => `<div class="w-banner" data-tone="${tone}"${role ? ` role="${role}"` : ''}><span class="w-banner__glyph" aria-hidden="true">${glyph}</span><div class="w-banner__msg">${html}</div></div>`;
        const card = ({id, eyebrow = '', title, meta = '', body, flush = false, attrs = ''}) => `<section class="w-card work-card" aria-labelledby="${id}" ${attrs}>
            <header class="w-card__head"><h2 class="w-card__title" id="${id}">${title}</h2>${meta ? `<span class="w-card__meta">${meta}</span>` : ''}</header>
            <div class="w-card__body${flush ? ' work-card-flush' : ''}">${body}</div></section>`;
        function renderPlan(panel) {
            if (editor?.isDirty()) return;
            editor?.destroy(); editor = null;
            const plan = job.plan || {};
            const editAction = job.actions.find(a => a.id === 'savePlanDraft');
            const latest = plan.version === plan.latest_version;
            if (plan.error) { panel.innerHTML = banner('danger', '✗', esc(plan.error), 'alert'); return; }
            if (!plan.markdown && !plan.draft && !editing) { panel.innerHTML = emptyState('No plan yet', 'The planner writes the plan and acceptance criteria here.', 'plan', 'list-checks'); return; }
            const versions = (plan.versions || []).length > 1 ? `<label class="work-inline-field"><span class="work-meta">Version</span><span class="w-select"><select class="w-select__control" data-plan-version aria-label="Plan version">${plan.versions.map(v => `<option value="${v}" ${v === plan.version ? 'selected' : ''}>v${v}${v === plan.approved_version ? ' · approved' : ''}${v === plan.latest_version ? ' · latest' : ''}</option>`).join('')}</select></span></label>` : `<span class="work-meta">Version ${esc(plan.version || 1)}${plan.approved_version === plan.version ? ' · approved' : ''}</span>`;
            if (editing && editAction?.enabled && latest) {
                panel.innerHTML = '<div data-editor></div>';
                editor = window.MuJobPlan.mount(panel.querySelector('[data-editor]'), plan, job.actions, send, async () => { editing = false; signatures.delete('plan'); await refreshParts(['plan', 'state']); },
                    { onDirty: () => renderActions(), onCancel: () => { editing = false; editor?.destroy(); editor = null; signatures.delete('plan'); renderActions(); renderPart('plan', true); } });
                return;
            }
            editing = false;
            panel.innerHTML = `<div class="work-stack">
                <div class="work-toolbar">${versions}
                    ${plan.draft ? pill('attention', `${chipGlyph('◆')}<span>Unsaved draft — edit the plan to continue or save it</span>`) : job.state === 'plan_review' ? pill('attention', `${chipGlyph('◆')}<span>Waiting for your approval</span>`) : ''}
                    <span class="work-spacer"></span>
                    ${editAction?.enabled && latest ? `<button type="button" class="w-textbtn" data-plan-edit${editAction.shortcut ? ` aria-keyshortcuts="${esc(editAction.shortcut)}" title="Edit plan (${esc(editAction.shortcut)})"` : ''}>${icon('pencil')}<span>Edit plan</span></button>` : ''}
                </div>
                ${plan.diff_from_previous ? `<details class="work-details"><summary>Changes from the previous version</summary><pre class="work-log">${esc(plan.diff_from_previous)}</pre></details>` : ''}
                ${(plan.errors || []).length ? banner('caution', '◆', plan.errors.map(esc).join('<br>')) : ''}
                <article class="w-card work-doc"><div class="w-card__body w-markdown work-markdown">${markdown(plan.markdown || plan.draft || '')}</div></article></div>`;
            panel.querySelector('[data-plan-edit]')?.addEventListener('click', () => invoke('savePlanDraft'));
            panel.querySelector('[data-plan-version]')?.addEventListener('change', async e => {
                const response = await api(`/api/jobs/vm/job/${encodeURIComponent(selectedId)}?parts=plan&plan_version=${e.target.value}`);
                job.plan = response.plan; renderPart('plan', true);
            });
        }

        /* ------------------------------------------------------------ review */
        /* Severity chips follow the vocabulary: blocker danger, major attention, minor caution, nit neutral. */
        function renderReview(panel) {
            const report = job.review;
            if (!report) { panel.innerHTML = emptyState('No review yet', 'The reviewer reports here after checks pass.', 'plan', 'eye'); return; }
            const fix = job.actions.find(a => a.id === 'reprompt'), mark = job.actions.find(a => a.id === 'markFindings');
            const findings = report.findings || [];
            const readiness = Number(report.merge_readiness || 0);
            const files = new Set(parsePatch(job.diff?.patch).map(f => f.path));
            panel.innerHTML = `<div class="work-stack">
                <div class="work-toolbar">${verdictPill(report.verdict)}<span class="work-readiness"><span class="work-meta">Merge readiness</span>${resultBar(readiness, 100 - readiness)}<strong class="work-num">${readiness}/100</strong></span>
                    <span class="work-meta">${Object.entries(report.counts || {}).map(([s, c]) => `${c} ${esc(s)}`).join(' · ')}</span></div>
                ${findings.length ? `<div class="work-toolbar" role="toolbar" aria-label="Fix review findings"><span class="work-meta work-grow">Pick findings to fix in one targeted implement run (checks and review follow); the plan stays as it is.</span>
                    <button type="button" class="w-textbtn" data-fix-selected ${fix?.enabled && selectedFindings.size ? '' : 'disabled'} title="${esc(fix?.enabled ? 'Fix the selected findings' : fix?.reason || 'not available')}">Fix ${selectedFindings.size || ''} selected</button></div>` : ''}
                <div class="work-grid-2">
                    ${card({id:'rv-sum', eyebrow:'Review', title:'Summary', body:`${report.headline ? `<p class="work-headline">${esc(report.headline)}</p>` : ''}${(report.notes || []).length ? `<ul class="work-note__bullets">${report.notes.map(n => `<li>${esc(n)}</li>`).join('')}</ul>` : ''}<div class="w-markdown work-markdown">${markdown(report.summary || '')}</div>`})}
                    ${card({id:'rv-adh', eyebrow:'Plan', title:'Plan adherence', body:(report.plan_coverage || []).length ? `<ul class="work-list">${report.plan_coverage.map(c => `<li class="work-coverage">${statusIcon(coverageStatus(c.status), c.status)}<span class="work-mono work-nowrap">${esc(c.criterion_id)}</span><span class="work-grow">${esc(c.evidence || c.status)}</span></li>`).join('')}</ul>` : '<p class="work-empty-line">No acceptance criteria were assessed.</p>'})}
                </div>
                ${(report.out_of_scope_changes || []).length ? banner('caution', '◆', `<strong>Out-of-scope changes</strong><ul>${report.out_of_scope_changes.map(x => `<li>${esc(typeof x === 'string' ? x : JSON.stringify(x))}</li>`).join('')}</ul>`) : ''}
                ${findings.length ? '' : emptyState('No findings', 'The reviewer raised nothing to fix.', 'ok', 'circle-check')}
                ${SEVERITIES.map(sev => { const list = findings.filter(f => f.severity === sev); if (!list.length) return '';
                    return `<section class="work-findings" aria-labelledby="sev-${sev}"><h3 id="sev-${sev}" class="work-findings__title">${severityPill(sev)}<span class="w-count">${list.length}</span></h3><ul class="work-list">${list.map(f => {
                        const closed = (f.disposition || 'open') !== 'open';
                        return `<li class="w-card work-finding-card ${closed ? 'is-closed' : ''}" id="finding-${esc(f.id)}" data-severity="${esc(f.severity)}" data-tone="${severityTone(f.severity)}">
                            <div class="w-card__body">
                            <div class="work-toolbar"><input type="checkbox" class="w-check__input" data-finding="${esc(f.id)}" aria-label="select “${esc(f.title)}” for fixing" ${selectedFindings.has(f.id) ? 'checked' : ''} ${closed ? 'disabled' : ''}>${severityPill(f.severity)}<strong class="work-grow work-finding-title">${esc(f.title)}</strong>
                                ${f.file && files.has(f.file) ? `<button type="button" class="w-btn" data-variant="ghost" data-size="sm" data-anchor="${esc(f.id)}">Jump to diff<span class="w-btn__chevron" aria-hidden="true">›</span></button>` : '<span class="work-meta" title="this file is not part of the current diff">not in diff</span>'}
                                <button type="button" class="w-btn" data-variant="secondary" data-size="sm" data-fix-one="${esc(f.id)}" ${fix?.enabled && !closed ? '' : 'disabled'} title="${esc(fix?.enabled ? 'Fix only this finding' : fix?.reason || 'not available')}">Fix</button>
                                <span class="w-select work-disposition"><select class="w-select__control" data-disposition="${esc(f.id)}" aria-label="Disposition of ${esc(f.title)}" ${mark?.enabled ? '' : 'disabled'}>${['open', 'accepted', 'dismissed', 'fixed'].map(d => `<option ${d === (f.disposition || 'open') ? 'selected' : ''}>${d}</option>`).join('')}</select></span></div>
                            <div class="work-mono work-meta">${esc(f.file || 'no file')}${f.line_start ? `:${f.line_start}` : ''}${f.category ? ` · ${esc(f.category)}` : ''}${f.confidence != null ? ` · confidence ${esc(f.confidence)}` : ''}</div>
                            ${f.detail ? `<div class="w-markdown work-markdown">${markdown(f.detail)}</div>` : ''}${f.suggestion ? `<div><span class="work-meta">Suggestion</span><div class="w-markdown work-markdown">${markdown(f.suggestion)}</div></div>` : ''}</div></li>`; }).join('')}</ul></section>`; }).join('')}</div>`;
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
                const row = q(`[data-line-key="${CSS.escape((f.file || '') + ':' + f.line_start)}"]`) || q(`[data-file="${CSS.escape(f.file || '')}"]`);
                row?.scrollIntoView({block:'center'}); row?.classList.add('work-flash'); setTimeout(() => row?.classList.remove('work-flash'), 1600);
            }, 0);
        }

        /* ------------------------------------------------------------ diff (G§9.11, wash/work_diff.js) */
        /* Review mode: the diff panel on its own over the whole view (work.css [data-zen]);
         * Esc, the ⤡ button, another tab or another job leave it. */
        function setDiffZen(panel, on) {
            if (!panel) return;
            panel.toggleAttribute('data-zen', on);
            document.documentElement.toggleAttribute('data-diff-zen', on);
            renderPart('diff', true);
            if (on) { panel.scrollTop = 0; window.WashMotion?.enter(panel); }
            panel.querySelector('[data-diff-zen]')?.focus();
        }
        document.addEventListener('keydown', e => {
            if (e.key !== 'Escape' || !document.documentElement.hasAttribute('data-diff-zen')) return;
            if (e.target?.closest?.('input, textarea, select, [contenteditable], .w-menu, .w-modal')) return;
            e.preventDefault();
            setDiffZen(q('[data-panel="diff"]'), false);
        });
        function renderDiff(panel) {
            const live = job.diff?.live || null;
            const files = parsePatch(diffPatch(job));
            const liveNote = live ? `<div class="w-banner work-live-diff" data-tone="running" role="status" data-live-diff><span class="w-inkdrop" data-size="sm" aria-hidden="true"></span>
                <p class="w-banner__msg"><strong>Live</strong> · working tree vs base <code>${esc(String(live.base_sha || '').slice(0, 12))}</code>, refreshed every few seconds while the job runs
                ${live.untracked_total ? `<span class="work-meta">· ${live.untracked_total} new file${live.untracked_total === 1 ? '' : 's'}</span>` : ''}
                ${live.truncated ? '<span>· truncated (large diff)</span>' : ''}</p><span class="work-meta">${esc(when(live.generated_at))}</span></div>` : '';
            if (!files.length) { panel.innerHTML = `${liveNote}${live ? emptyState('No changes yet', 'The working tree matches the base so far.', 'running', 'file-text') : emptyState('No diff yet', 'The implementer’s changes appear here.', 'accent', 'file-text')}`; return; }
            const seen = live ? new Set() : viewed();
            const findings = (job.review?.findings || []).filter(f => (f.disposition || 'open') === 'open');
            const fix = job.actions.find(a => a.id === 'reprompt');
            const totalA = files.reduce((n, f) => n + f.added, 0), totalR = files.reduce((n, f) => n + f.removed, 0);
            const comment = f => `<div class="w-card w-diff__finding" data-tone="${severityTone(f.severity)}" role="note" aria-label="Review finding: ${esc(f.severity)}: ${esc(f.title)}"><div class="w-card__body"><div class="work-toolbar">${severityPill(f.severity)}<strong class="work-grow">${esc(f.title)}</strong>
                <button type="button" class="w-btn" data-variant="secondary" data-size="sm" data-fix-one="${esc(f.id)}" ${fix?.enabled ? '' : 'disabled'} title="${esc(fix?.enabled ? 'Run a targeted implement pass that fixes only this finding' : fix?.reason || 'not available')}">Fix this</button></div>${f.detail ? `<p>${esc(f.detail)}</p>` : ''}${f.suggestion ? `<p><span class="work-meta">Suggestion: </span>${esc(f.suggestion)}</p>` : ''}</div></div>`;
            const fileBody = f => (f.binary ? '<p class="work-empty-line">Binary file.</p>'
                : DIFF.renderFile(f, {mode: splitDiff ? 'split' : 'unified', lineKeys: true,
                    findings: findings.map(x => ({file: x.file, line: Number(x.line_start || 0), html: comment(x)}))}));
            const statusLetter = f => DIFF.statusBadge(f);
            const zen = panel.hasAttribute('data-zen');
            const zenBar = `<div class="work-diff-bar">${zen ? `<div class="work-diff-bar__title"><span class="work-meta">Reviewing</span> <strong>${esc(job.title || jobKey(job))}</strong></div>` : ''}<span class="work-spacer"></span>
                ${zen ? '<span class="work-meta work-diff-bar__hint">Esc to leave</span>' : ''}<button type="button" class="w-btn" data-variant="icon" data-size="sm" data-diff-zen aria-pressed="${zen}" aria-label="${zen ? 'Leave review mode' : 'Review mode: the diff on its own, full screen'}" title="${zen ? 'Leave review mode (Esc)' : 'Review mode'}">${icon(zen ? 'minimize-2' : 'maximize-2')}</button></div>`;
            panel.innerHTML = `${zenBar}${liveNote}<div class="work-diff-layout">
                <nav class="work-diff-tree" aria-label="Changed files"><div class="work-diff-tree-head">
                    <div class="work-toolbar"><strong>${files.length} file${files.length === 1 ? '' : 's'}</strong><span class="work-diffstat"><span class="work-add">+${totalA}</span><span class="work-del">−${totalR}</span></span><span class="work-spacer"></span>${live ? '' : `<span class="work-meta">${files.filter(f => seen.has(f.path)).length}/${files.length} viewed</span>`}</div>
                    <div class="w-segmented work-diff-mode" data-size="sm" role="group" aria-label="Diff layout"><button type="button" class="w-segmented__option" data-diff-mode="unified" aria-pressed="${!splitDiff}">Unified</button><button type="button" class="w-segmented__option" data-diff-mode="split" aria-pressed="${splitDiff}">Split</button></div></div>
                    ${DIFF.renderTree(files, {viewed: f => seen.has(f.path), attrs: (f, i) => `data-goto-file="${i}"`})}</nav>
                <div class="work-diff-files">${files.map((f, i) => { const isViewed = seen.has(f.path), isCollapsed = collapsed.has(f.path) || (isViewed && !collapsed.has(`!${f.path}`));
                    return `<section class="work-diff-file" id="diff-file-${i}" data-file="${esc(f.path)}"><header class="work-diff-file-head">
                        <button type="button" class="work-diff-collapse" data-collapse="${esc(f.path)}" aria-expanded="${!isCollapsed}">${icon(isCollapsed ? 'chevron-right' : 'chevron-down')}${statusLetter(f)}<span class="work-mono work-ellipsis">${esc(f.path)}</span></button>
                        <span class="work-diffstat"><span class="work-add">+${f.added}</span><span class="work-del">−${f.removed}</span></span><span class="work-spacer"></span>
                        ${findings.some(x => x.file === f.path) ? `<span class="w-chip" data-tone="attention" title="review findings in this file">${chipGlyph('◆')}<span>${findings.filter(x => x.file === f.path).length} finding${findings.filter(x => x.file === f.path).length === 1 ? '' : 's'}</span></span>` : ''}
                        ${live ? '' : `<label class="w-check work-viewed"><input class="w-check__input" type="checkbox" data-viewed="${esc(f.path)}" ${isViewed ? 'checked' : ''}><span>Viewed</span></label>`}</header>
                        ${isCollapsed ? '' : fileBody(f)}</section>`; }).join('')}</div></div>`;
            panel.querySelector('[data-diff-zen]').onclick = () => setDiffZen(panel, !zen);
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
        /* hand-written SVG bar chart: passed bars in the ok pigment, failed in danger (viz guide) */
        function durationChart(suite, points, selectedNo) {
            const W = 640, H = 150, L = 52, B = 22, T = 8, max = Math.max(1, ...points.map(p => Number(p.duration_ms || 0)));
            const slot = (W - L - 8) / points.length, bw = Math.min(40, Math.max(4, slot * 0.6));
            const y = v => T + (H - T - B) * (1 - v / max);
            const ticks = [0, 0.5, 1].map(f => f * max);
            const summary = points.map(p => `run ${p.run}: ${duration(p.duration_ms)} ${p.passed ? 'passed' : 'failed'}`).join(', ');
            return `<section aria-labelledby="dur-h-${esc(suite)}" class="work-duration"><h3 id="dur-h-${esc(suite)}" class="work-subhead">Duration history · ${esc(suite)}</h3>
                <svg class="work-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMinYMid meet" role="img" aria-label="${esc(`Duration history for ${suite}: ${summary}`)}">
                ${ticks.map(t => `<line x1="${L}" x2="${W - 4}" y1="${y(t)}" y2="${y(t)}" class="grid"/><text x="${L - 6}" y="${y(t) + 4}" text-anchor="end" class="axis">${esc(t < 120000 ? `${Math.round(t / 1000)}s` : duration(t))}</text>`).join('')}
                ${points.map((p, i) => { const cx = L + slot * i + slot / 2, top = y(Number(p.duration_ms || 0));
                    return `<g><title>${esc(`Run ${p.run} · ${duration(p.duration_ms)} · ${p.passed ? 'passed' : 'failed'}`)}</title><rect x="${cx - bw / 2}" y="${top}" width="${bw}" height="${Math.max(1, H - B - top)}" rx="2" class="${p.passed ? 'bar-ok' : 'bar-danger'} ${p.run === selectedNo ? 'is-selected' : ''}"/>
                        <text x="${cx}" y="${H - 6}" text-anchor="middle" class="axis">#${esc(p.run)}</text></g>`; }).join('')}
                </svg><div class="work-legend"><span><i class="work-swatch" data-tone="ok" aria-hidden="true"></i>passed</span><span><i class="work-swatch" data-tone="danger" aria-hidden="true"></i>failed</span></div></section>`;
        }
        let failureFilter = '';
        /* Evidence (mu/jobs/evidence.py): what the agents captured while testing, newest first. */
        function evidenceCard(items) {
            if (!items.length) return '';
            const url = (e, view = '') => `/api/jobs/${encodeURIComponent(selectedId)}/evidence/${e.file.split('/').map(encodeURIComponent).join('/')}${view ? `?view=${view}` : ''}`;
            const label = e => e.caption || e.name;
            const meta = e => [e.stage, e.created_at ? when(e.created_at).slice(5, 16) : '', e.bytes ? bytesText(e.bytes) : ''].filter(Boolean).join(' · ');
            const tile = e => {
                const media = e.kind === 'image'
                    ? `<a class="work-evidence__media" href="${esc(url(e))}" target="_blank" rel="noopener" aria-label="Open ${esc(label(e))} full size"><img src="${esc(url(e))}" alt="${esc(label(e))}" loading="lazy"></a>`
                    : e.kind === 'video'
                        ? `<div class="work-evidence__media"><video src="${esc(url(e))}" controls preload="metadata" aria-label="${esc(label(e))}"></video></div>`
                        : `<div class="work-evidence__media work-evidence__file">${icon(e.kind === 'text' ? 'file-text' : 'file', 'lg')}<span class="work-mono work-ellipsis">${esc(e.name)}</span></div>`;
                const links = e.kind === 'text' ? `<a href="${esc(url(e, 'text'))}" target="_blank" rel="noopener">View</a> · <a href="${esc(url(e))}">Download</a>`
                    : e.kind === 'file' ? `<a href="${esc(url(e))}">Download</a>` : '';
                return `<figure class="work-evidence__item" data-evidence-kind="${esc(e.kind)}">${media}
                    <figcaption><span class="work-evidence__caption">${esc(label(e))}</span><span class="work-meta">${esc(meta(e))}${links ? ` · ${links}` : ''}</span></figcaption></figure>`;
            };
            return card({id:'evidence-h', eyebrow:'Evidence', title:'What the agent saw', meta:`<span class="w-count">${items.length}</span>`,
                body:`<p class="work-meta">Screenshots, recordings and logs captured while testing. They stay with the job and are never pushed.</p><div class="work-evidence">${items.map(tile).join('')}</div>`});
        }
        function bytesText(n) { return n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : n >= 1024 ? `${Math.round(n / 1024)} KB` : `${n} B`; }
        function renderChecks(panel) {
            const runs = (job.checks?.runs || []).slice().reverse(); // newest first
            const recheck = job.actions.find(a => a.id === 'recheck');
            const live = job.checks?.live || [];
            const running = job.state === 'checking';
            const latest = runs[0];
            const selected = runs.find(r => r.run === selectedRun) || runs[0];
            const total = r => (r.suites || []).map(suiteCounts).reduce((a, c) => ({passed:a.passed + c.passed, failed:a.failed + c.failed, skipped:a.skipped + c.skipped, total:a.total + c.total}), {passed:0, failed:0, skipped:0, total:0});
            const unverifiedStatus = statusOf('unverified');
            const isUnverified = r => r && (r.verified === false || Number(r.unrunnable || 0) > 0);
            const runPill = r => isUnverified(r) ? pill(unverifiedStatus.tone, `${chipGlyph(unverifiedStatus.glyph)}<span>Unverified</span>`, `${r.unrunnable || 0} check(s) could not run`)
                : r.passed ? pill('ok', `${chipGlyph('✓')}<span>Passed</span>`) : pill('danger', `${chipGlyph('✗')}<span>Failed</span>`);
            const suiteStatus = x => x.status === 'unrunnable' ? 'unverified' : x.passed ? 'done' : 'failed';
            const unverifiedCard = r => {
                if (!isUnverified(r)) return '';
                const rows = (r.unrunnable_suites || []).length ? r.unrunnable_suites
                    : (r.suites || []).filter(x => x.status === 'unrunnable').map(x => ({suite:x.suite, command:x.command, ...(x.unrunnable || {})}));
                const tpl = (job.build_environments?.bindings || []).find(b => b.source === 'template');
                const cont = job.actions.find(a => a.id === 'continueUnverified');
                const prepare = tpl ? `<a class="w-btn" data-variant="secondary" data-size="sm" data-prepare-template="${esc(tpl.ref)}" href="/containers#template-${encodeURIComponent(tpl.ref)}">Edit template ${esc(tpl.ref)}</a>`
                    : `<a class="w-btn" data-variant="secondary" data-size="sm" data-prepare-template="" href="/containers#templates-title">Prepare a template</a>`;
                return `<section class="w-card work-card work-unverified" data-unverified aria-labelledby="unverified-h"><header class="w-card__head">${statusIcon('unverified')}<h2 class="w-card__title" id="unverified-h">Unverified · ${esc(rows.length || r.unrunnable || 0)} check${(rows.length || r.unrunnable) === 1 ? '' : 's'} could not run</h2></header>
                    <div class="w-card__body work-stack-tight"><p class="work-meta">These checks never ran the project's own tools, so their results are not evidence. They are not counted as pre-existing failures; the review verdict is capped at needs work, and a published PR states that the checks could not run.</p>
                    <ul class="work-unverified__list">${rows.map(x => `<li data-unrunnable="${esc(x.suite || '')}"><strong class="work-mono">${esc(x.suite || '')}</strong>${x.tool ? ` · <code>${esc(x.tool)}</code>` : ''}${x.cause ? ` — ${esc(x.cause)}` : ''}${x.hint ? `<div class="work-meta">${esc(x.hint)}</div>` : ''}</li>`).join('')}</ul>
                    <div class="w-btn-row" role="toolbar" aria-label="Unverified checks">${prepare}${cont && !cont.hidden ? actionButton(cont) : ''}</div></div></section>`;
            };
            // A repair that failed or ended is history once a later run verified the checks: hide its
            // card (a successful repair stays, with its save-to-template actions).
            const repairs = job.checks?.repairs || [];
            const lastRepair = repairs[repairs.length - 1];
            const repairSuperseded = lastRepair && !['running', 'committed', 'repaired'].includes(lastRepair.outcome)
                && latest && !isUnverified(latest) && Number(latest.started_at || 0) > Number(lastRepair.finished_at || lastRepair.started_at || 0);
            const repair = ENV && !repairSuperseded ? ENV.repairCard(repairs, {buttons:repairButtons()}) : '';
            const started = r => r.started_at ? when(r.started_at).slice(5, 16) : '';
            const dur = r => { const ms = runDuration(r); return ms == null ? '' : duration(ms); };
            const liveLog = live.length && running ? card({id:'live-h', eyebrow:'Live', title:'Live log', meta:'running now', body:`<pre class="work-log" data-live-log role="log" aria-live="off">${ansi(live.map(e => `[${e.payload?.suite || 'check'}${e.payload?.baseline ? ' · baseline' : ''}] ${e.payload?.text || ''}`).join(''))}</pre>`}) : '';
            panel.innerHTML = `<div class="work-stack">
                <div class="work-toolbar"><div class="work-checks-summary"><span>${running ? '<span class="w-status" data-tone="running"><span class="w-inkdrop" data-size="sm" aria-hidden="true"></span>Running · </span>' : ''}${latest ? `Last: run ${latest.run} ${isUnverified(latest) ? 'unverified' : latest.passed ? 'passed' : 'failed'}` : 'No checks have run yet.'}</span>
                    <span class="work-meta">fix attempts ${esc(job.fix_attempts || 0)} · checks come from the approved plan</span></div><span class="work-spacer"></span>
                    <div class="w-btn-row" role="toolbar" aria-label="Check actions">${recheck ? actionButton(recheck) : ''}</div></div>
                ${liveLog}
                ${repair}
                ${unverifiedCard(latest)}
                ${evidenceCard(job.checks?.evidence || [])}
                ${runs.length ? `<div class="w-table-wrap"><table class="w-table work-runs" aria-label="Runs"><thead><tr><th scope="col">Run</th><th scope="col">Status</th><th scope="col">Result</th><th scope="col">Trigger</th><th scope="col" data-num>Duration</th><th scope="col">Started</th></tr></thead><tbody>${runs.map(r => { const k = total(r);
                    return `<tr class="clickable" aria-selected="${r === selected}" data-run="${esc(r.run)}"><td><button type="button" class="work-link-btn" aria-pressed="${r === selected}">Run ${esc(r.run)}</button></td><td>${runPill(r)}</td>
                        <td class="work-result-cell">${k.total ? `<div class="work-toolbar">${resultBar(k.passed, k.failed, k.skipped)}<span class="work-nowrap work-meta">${k.passed}✓ ${k.failed ? `<span class="work-del">${k.failed}✗</span>` : ''}</span></div>` : ''}</td>
                        <td class="work-meta">${esc(r.trigger || '')}</td><td data-num>${esc(dur(r))}</td><td class="work-meta work-nowrap">${esc(started(r))}</td></tr>`; }).join('')}</tbody></table></div>` : emptyState('No builds or test runs yet', 'Checks from the approved plan run here after implementing.', 'running', 'list-checks')}
                ${selected ? (() => { const k = total(selected), cases = failedCases(selected, latest);
                    const q = failureFilter.toLowerCase(), rows = cases.filter(f => !q || `${f.suite} ${f.name} ${f.message}`.toLowerCase().includes(q));
                    const hasJunit = (selected.suites || []).some(s => s.junit);
                    const suitesWithHistory = (selected.suites || []).map(s => [s.suite, suiteHistory(s.suite)]).filter(([, h]) => h.length >= 2);
                    return `<section class="w-card work-card" aria-labelledby="run-h"><header class="w-card__head"><h2 class="w-card__title" id="run-h">Run ${esc(selected.run)}</h2>${runPill(selected)}<span class="w-card__meta">${esc([dur(selected), selected.trigger, selected.head_sha ? String(selected.head_sha).slice(0, 12) : ''].filter(Boolean).join(' · '))}</span></header>
                    <div class="w-card__body work-stack">
                        ${k.total ? `<div class="work-stack-tight">${resultBar(k.passed, k.failed, k.skipped, true)}<div class="work-toolbar work-meta"><span class="work-add">✓ ${k.passed} passed</span><span class="${k.failed ? 'work-del' : ''}">✗ ${k.failed} failed</span>${k.skipped ? `<span>– ${k.skipped} skipped</span>` : ''}<span>of ${k.total}</span></div></div>` : ''}
                        ${(selected.suites || []).length ? `<div class="w-table-wrap"><table class="w-table work-suites" aria-label="Suites of run ${esc(selected.run)}"><thead><tr><th scope="col">Suite</th><th scope="col">Environment</th><th scope="col">Result</th><th scope="col" data-num>Duration</th></tr></thead><tbody>
                            ${selected.suites.map(s => `<tr data-suite-status="${esc(s.status || '')}"><td data-mono>${statusIcon(suiteStatus(s))} ${esc(s.suite)}</td><td data-mono data-suite-env="${esc(s.environment || '')}">${s.environment ? `<span aria-hidden="true">⬢</span> ${esc(s.environment)}` : '<span class="work-meta">shared runtime</span>'}</td><td>${s.status === 'unrunnable' ? pill(unverifiedStatus.tone, `${chipGlyph(unverifiedStatus.glyph)}<span>Unrunnable${s.unrunnable?.tool ? ` · ${esc(s.unrunnable.tool)}` : ''}</span>`, s.unrunnable?.cause || '') : s.missing_executable ? pill('caution', `<span>${esc(s.missing_executable)} missing</span>`) : s.passed ? pill('ok', `${chipGlyph('✓')}<span>Passed</span>`) : pill('danger', `${chipGlyph('✗')}<span>Failed</span>`)}</td><td class="work-meta" data-num>${s.duration_ms != null ? esc(duration(s.duration_ms)) : ''}</td></tr>`).join('')}</tbody></table></div>` : ''}
                        ${cases.length ? `<section aria-labelledby="jf-h" class="work-stack-tight"><div class="work-toolbar"><h3 id="jf-h" class="work-subhead">Failed test cases <span class="w-count" data-tone="danger">${cases.length}</span></h3><span class="work-spacer"></span>
                            <input class="w-input work-filter" type="search" data-failure-filter aria-label="Filter failed tests" placeholder="Filter failures…" value="${esc(failureFilter)}"></div>
                            <div class="w-table-wrap"><table class="w-table work-failures"><thead><tr><th scope="col">Suite</th><th scope="col">Test</th><th scope="col">Message</th></tr></thead><tbody>
                            ${rows.map(f => `<tr><td data-mono>${esc(f.suite || '')}</td><td data-mono><span class="work-del" aria-hidden="true">✗ </span>${f.classname && f.classname !== f.suite ? `<span class="work-meta">${esc(f.classname)}.</span>` : ''}${esc(f.name || f.id || '')}${f.new === false ? ` ${pill('neutral', '<span>pre-existing</span>', 'also fails on the base commit')}` : f.new ? ` ${pill('danger', '<span>new</span>')}` : ''}</td><td><pre class="work-failure-msg">${esc(f.message || '')}</pre></td></tr>`).join('') || '<tr><td colspan="3" class="work-empty-line">No failure matches the filter.</td></tr>'}
                            </tbody></table></div></section>`
                            : hasJunit && !k.failed ? `<p class="w-status" data-tone="ok"><span class="w-status__glyph" aria-hidden="true">✓</span>No failing test cases.</p>` : ''}
                        ${!cases.length && Array.isArray(selected.new_failures) && selected.new_failures.length ? `<section><h3 class="work-subhead">New failures <span class="w-count" data-tone="danger">${selected.new_failures.length}</span></h3><ul class="work-mono work-list">${selected.new_failures.map(f => `<li class="work-del">✗ ${esc(typeof f === 'string' ? f : JSON.stringify(f))}</li>`).join('')}</ul></section>` : ''}
                        ${Array.isArray(selected.preexisting_failures) && selected.preexisting_failures.length ? `<section><h3 class="work-subhead">Pre-existing on the base commit <span class="w-count">${selected.preexisting_failures.length}</span></h3><p class="work-meta">These also fail before the change, so they are reported to the reviewer and not sent back to the implementer.</p><ul class="work-mono work-meta work-list">${selected.preexisting_failures.map(f => `<li>${esc(typeof f === 'string' ? f : JSON.stringify(f))}</li>`).join('')}</ul></section>` : ''}
                        ${suitesWithHistory.map(([suite, h]) => durationChart(suite, h, selected.run)).join('')}
                        <div data-art-mount></div>
                        <section aria-labelledby="log-h" class="work-stack-tight"><h3 id="log-h" class="work-subhead">Log</h3>
                        ${(selected.suites || []).map(s => { const c = suiteCounts(s);
                            return `<details class="work-details work-suite-log" ${s.passed ? '' : 'open'}><summary>${statusIcon(suiteStatus(s))}<strong>${esc(s.suite)}</strong><span class="work-suite-env work-meta work-mono" title="build environment that ran it">${s.environment ? `<span aria-hidden="true">⬢</span> ${esc(s.environment)}` : 'shared runtime'}</span>${(s.preexisting_failures || []).length ? pill('neutral', '<span>pre-existing failures</span>') : ''}<span class="work-spacer"></span>${s.duration_ms != null ? `<span class="work-meta work-num">${esc(duration(s.duration_ms))}</span>` : ''}<span class="work-suite-bar">${resultBar(c.passed, c.failed, c.skipped)}</span><span class="work-nowrap work-num">${c.passed}/${c.total}</span></summary>
                                <div class="work-stack-tight work-suite-out"><code class="work-meta">${esc(s.command || '')}</code><pre class="work-log">${ansi(String(s.stdout || '') + (s.stderr ? '\n' + s.stderr : '')) || '<span class="work-meta">No output.</span>'}</pre></div></details>`; }).join('') || '<p class="work-empty-line">No suite output.</p>'}</section>
                    </div></section>`; })() : ''}</div>`;
            bindCommands(panel);
            bindRepairTranscript(panel);
            const artMount = panel.querySelector('[data-art-mount]');
            if (artMount && selected && window.MuArtifacts) window.MuArtifacts.mount(artMount, {jobId:job.id, run:selected.run});
            panel.querySelectorAll('tr[data-run]').forEach(tr => { tr.onclick = () => { selectedRun = Number(tr.dataset.run); renderPart('checks', true); }; });
            const filter = panel.querySelector('[data-failure-filter]');
            if (filter) filter.oninput = () => { failureFilter = filter.value; const pos = filter.selectionStart; renderPart('checks', true); const f = panel.querySelector('[data-failure-filter]'); f?.focus(); f?.setSelectionRange(pos, pos); };
            const log = panel.querySelector('[data-live-log]'); if (log) log.scrollTop = log.scrollHeight;
        }
        /* ------------------------------------------------------------ session */
        /* One line per tool call (mirrors core/notes.tool_summary for transcripts recorded before it). */
        function toolLine(name, args) {
            if (typeof args === 'string') return `${name} ${args.split('\n').find(l => l.trim()) || ''}`.slice(0, 160);
            const a = args || {}, cmd = a.command ?? a.cmd;
            if (cmd) { const text = Array.isArray(cmd) ? cmd.join(' ') : String(cmd), lines = text.split('\n').filter(l => l.trim());
                return `$ ${(lines[0] || '').trim().slice(0, 160)}${lines.length > 1 ? ' …' : ''}`; }
            const target = String(a.filename || a.file_path || a.path || '').replace('/workspace/', '') || (Array.isArray(a.files) ? a.files.slice(0, 3).join(', ') : '');
            const query = a.pattern || a.query || a.url || a.description;
            return [name, query ? `"${String(query).slice(0, 80)}"` : '', target].filter(Boolean).join(' ').slice(0, 160) || name;
        }
        /* A plan/review stage replies with its JSON report: show its headline, not the raw JSON. */
        function replyItem(text, stage) {
            const raw = String(text || '').trim(), body = raw.replace(/^```(?:json)?\s*|\s*```$/g, '');
            if (!body.startsWith('{')) return {kind:'assistant', text:raw};
            let data = null;
            try { data = JSON.parse(body); } catch (_) { return {kind:'assistant', text:raw}; }
            if (!data || typeof data !== 'object' || !('summary' in data || 'headline' in data)) return {kind:'assistant', text:raw};
            const review = Array.isArray(data.findings) || Array.isArray(data.plan_coverage);
            const first = String(data.summary || '').split(/(?<=[.!?])\s+/)[0];
            return {kind:'reply', headline:String(data.headline || first || ''), bullets:(Array.isArray(data.notes) ? data.notes : []).map(String).slice(0, 4),
                label:review ? `Review report${Array.isArray(data.findings) ? ` · ${data.findings.length} finding${data.findings.length === 1 ? '' : 's'}` : ''}` : stage === 'plan' || Array.isArray(data.steps) ? 'Plan' : 'Structured reply',
                tab:review ? 'review' : stage === 'plan' || Array.isArray(data.steps) ? 'plan' : '', text:JSON.stringify(data, null, 2)};
        }
        /* ------------------------------------------------------------ notes */
        function attentionBanner(j, failed) {
            const full = String(j.attention_detail || ''), head = String(j.attention_headline || '') || full;
            const more = head !== full && full.length > head.length;
            return `<div class="w-banner" data-tone="${failed ? 'danger' : 'attention'}"${failed ? ' role="alert"' : ''}><span class="w-banner__glyph" aria-hidden="true">${failed ? '✗' : '◆'}</span><div class="w-banner__msg"><p>${esc(head)}</p>${more ? `<details class="work-banner-more"><summary>Full text</summary><p>${esc(full)}</p></details>` : ''}</div></div>`;
        }
        const NOTE_STAGE = {plan:'Plan', implement:'Implement', checks:'Checks', review:'Review', push:'Push', you:'You', job:'Job'};
        function renderNotes(panel) {
            const notes = job.notes || [];
            const live = stateStatus(job.state) === 'active';
            const note = n => `<li class="work-note" data-stage="${esc(n.stage)}"><span class="work-note__dot" data-tone="${esc(n.tone || 'neutral')}" aria-hidden="true"></span>
                <div class="work-note__head"><span class="work-note__stage">${esc(NOTE_STAGE[n.stage] || n.stage)}</span><strong class="work-note__title">${esc(n.title)}</strong>${n.count > 1 ? `<span class="w-count" aria-label="${esc(n.count)} times">×${esc(n.count)}</span>` : ''}
                    <span class="work-meta work-nowrap">${esc(when(n.at))}${n.meta ? ` · ${esc(n.meta)}` : ''}</span>
                    ${n.tab && tabs.includes(n.tab) ? `<button type="button" class="w-btn work-note__link" data-variant="ghost" data-size="sm" data-note-tab="${esc(n.tab)}"><span>${esc(labels[n.tab])}</span>${icon('chevron-right')}</button>` : ''}</div>
                ${n.headline ? `<p class="work-note__headline">${esc(n.headline)}</p>` : ''}
                ${(n.bullets || []).length ? `<ul class="work-note__bullets">${n.bullets.map(b => `<li>${/^\$ /.test(b) ? `<code>${esc(b)}</code>` : esc(b)}</li>`).join('')}</ul>` : ''}</li>`;
            panel.innerHTML = `<div class="work-stack-tight">
                <div class="work-toolbar"><span class="work-meta">What happened, in plain English. The Session tab has the full agent transcript.</span><span class="work-spacer"></span><span class="work-meta">${notes.length} notes</span></div>
                ${notes.length ? `<ol class="work-notes" aria-label="Job notes">${notes.map(note).join('')}${live ? `<li class="work-note"><span class="work-note__dot" data-tone="running" aria-hidden="true"></span><div class="work-note__head"><span class="w-status" data-tone="running"><span class="w-inkdrop" data-size="sm" aria-hidden="true"></span>${esc(stateLabel(job.state))}…</span></div></li>` : ''}</ol>` : '<p class="work-empty-line">No notes yet.</p>'}</div>`;
            panel.querySelectorAll('[data-note-tab]').forEach(b => { b.onclick = () => selectTab(b.dataset.noteTab); });
        }
        function sessionItems(data) {
            if (sessionStage) {
                const items = [];
                for (const m of data.stages?.[sessionStage] || []) for (const p of m.parts || []) {
                    if (p.type === 'text' && p.text) items.push(m.role === 'user' ? {kind:'user', text:p.text} : replyItem(p.text, sessionStage));
                    else if (p.type === 'tool_call') { const env = ENV?.toolSummary(p.tool_name, p.tool_args);
                        const args = typeof p.tool_args === 'string' ? p.tool_args : JSON.stringify(p.tool_args ?? '');
                        items.push({kind:'tool', summary:env || toolLine(p.tool_name, p.tool_args), text:`${p.tool_name}\n${typeof p.tool_args?.command === 'string' ? p.tool_args.command : args || ''}`}); }
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
                else if (e.event_type === 'environment_setup') { const p = e.payload || {};
                    items.push({kind:p.status === 'failed' ? 'error' : 'workbench', text:`job setup [${p.binding || '?'}] ${Number(p.index ?? 0) + 1}. ${p.command || ''} · ${p.status || ''}${p.exit_code != null && p.status !== 'running' ? ` (exit ${p.exit_code})` : ''}${p.status === 'failed' && p.output_tail ? `\n${stripAnsi(String(p.output_tail)).slice(-1200)}` : ''}`}); }
                else if (e.event_type === 'environment_repair') { const p = e.payload || {};
                    if (p.phase === 'command') items.push({kind:'tool', summary:`repair [${p.binding || '?'}] ${p.command || ''}`, text:`environment repair · ${p.binding || ''}\n$ ${p.command || ''}`});
                    else items.push({kind:p.phase === 'finished' && !['repaired', 'unresolved'].includes(p.outcome) ? 'error' : 'workbench', text:e.reason || `environment repair ${p.phase || ''}`}); }
                else if (e.event_type === 'environment_setup_log') continue; // the panel and the failure tail carry the output
                else if (['environment_setup_started', 'environment_setup_finished'].includes(e.event_type)) items.push({kind:'workbench', text:e.reason || 'job setup'});
                else if (['environment_setup_dirty', 'environment_setup_skipped'].includes(e.event_type)) items.push({kind:'sys', text:e.reason || e.event_type});
                else if (e.event_type === 'build_environments_recreated') items.push({kind:'sys', text:e.reason || 'Build environment images are being re-created.'});
                else if (e.event_type === 'build_environment_failed') items.push({kind:'error', text:`build environment ${e.payload?.binding || ''} failed: ${e.reason || ''}`});
                else if (e.event_type === 'sandbox_stopped') items.push({kind:'workbench', text:`sandbox stopped${stage ? ` · ${stage}` : ''}`});
                else if (e.event_type === 'runtime_error' || kind === 'runtime_error') items.push({kind:'error', text:stripAnsi(inner.text || e.reason || 'runtime error')});
                else if (kind === 'agent_message') items.push(inner.role === 'user' ? {kind:'user', text:inner.text || ''} : replyItem(inner.text || '', stage));
                else if (kind === 'tool_call_ui' && inner.tool_name === 'run_in_environment') continue; // shown by environment_command
                else if (kind === 'tool_call_ui') items.push({kind:'tool', summary:inner.summary || inner.tool_name || 'tool', text:inner.summary ? '' : JSON.stringify(inner, null, 2)});
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
            const item = it => {
                switch (it.kind) {
                    case 'assistant': return `<div class="work-msg-assistant w-markdown work-markdown">${markdown(it.text)}</div>`;
                    case 'reply': return `<div class="work-msg-assistant">${it.headline ? `<p class="work-headline">${esc(it.headline)}</p>` : ''}${it.bullets.length ? `<ul class="work-note__bullets">${it.bullets.map(b => `<li>${esc(b)}</li>`).join('')}</ul>` : ''}<div class="work-fold-item"><button type="button" class="work-fold" aria-expanded="false" data-fold>${icon('chevron-right')}<span class="work-meta">${esc(it.label)}${it.tab ? ` · full report on the ${esc(labels[it.tab])} tab` : ''}</span></button><pre class="work-fold-body" hidden>${esc(it.text)}</pre></div></div>`;
                    case 'user': return `<div class="work-msg-user"><span class="w-sr-only">You: </span><pre>${esc(it.text)}</pre></div>`;
                    case 'tool': case 'tool_result': { const multi = it.text && it.text !== it.summary;
                        return `<div class="work-fold-item"><button type="button" class="work-fold ${it.kind === 'tool' ? 'is-tool' : ''}" ${multi ? 'aria-expanded="false" data-fold' : 'disabled'}>${multi ? icon('chevron-right') : '<span class="work-fold-pad"></span>'}<span class="work-mono work-ellipsis">${it.kind === 'tool' ? '› ' : '↳ '}${esc(it.summary)}</span></button>${multi ? `<pre class="work-fold-body" hidden>${esc(it.text)}</pre>` : ''}</div>`; }
                    case 'workbench': return `<div>${pill('accent', `${chipGlyph('⬢')}<span>${esc(it.text)}</span>`)}</div>`;
                    case 'error': return `<div class="work-msg-sys" data-tone="danger"><span aria-hidden="true">✗ </span>${esc(it.text)}</div>`;
                    default: return `<div class="work-msg-sys">${esc(it.text)}</div>`;
                }
            };
            const stages = [['', 'Live'], ['plan', 'Plan'], ['implement', 'Implement'], ['review', 'Review'],
                ...((data.stages?.repair || []).length || sessionStage === 'repair' ? [['repair', 'Repair']] : [])];
            panel.innerHTML = `<div class="work-stack-tight">
                <div class="work-toolbar">${job.state === 'needs_input' ? '<span class="w-status" data-tone="attention"><span class="w-status__glyph" aria-hidden="true">◆</span>Waiting for you</span>' : live ? '<span class="w-status" data-tone="running"><span class="w-inkdrop" data-size="sm" aria-hidden="true"></span>Live session · the agent is working</span>' : `<span class="work-meta">${sessionStage ? `Transcript of the ${sessionStage} stage` : 'Recent stage activity'}</span>`}
                    <div class="w-segmented" data-size="sm" role="group" aria-label="Session source">${stages.map(([v, l]) => `<button type="button" class="w-segmented__option" data-session-stage="${v}" aria-pressed="${v === sessionStage}">${l}</button>`).join('')}</div>
                    ${status && live ? `<span class="work-meta work-mono work-ellipsis work-runtime-status" title="${esc(stripAnsi(status.payload?.payload?.text))}">› ${esc(stripAnsi(status.payload?.payload?.text))}</span>` : ''}
                    <span class="work-spacer"></span><span class="work-meta">${items.length} items</span></div>
                <div class="work-transcript" role="log" aria-label="Agent session" tabindex="0">${items.length ? items.map(item).join('') : '<p class="work-empty-line">No session output yet.</p>'}</div>
                ${msg?.enabled ? `<form class="work-msg-box" data-message-form><label for="work-agent-msg" class="w-sr-only">Message the agent</label><textarea class="w-textarea" id="work-agent-msg" data-message rows="2" placeholder="Message the agent… (Enter to send, Shift+Enter for a new line)">${esc(draft)}</textarea>
                    <button type="submit" class="w-btn" data-variant="primary" data-send-message>${icon('send')}<span>Send</span></button></form>`
                    : `<p class="work-meta">Messaging unavailable${msg?.reason ? `: ${esc(msg.reason)}` : ''}.</p>`}</div>`;
            panel.querySelectorAll('[data-session-stage]').forEach(b => { b.onclick = () => { sessionStage = b.dataset.sessionStage; renderPart('session', true); }; });
            panel.querySelectorAll('[data-fold]').forEach(b => { b.onclick = () => { const body = b.nextElementSibling, open = b.getAttribute('aria-expanded') === 'true'; b.setAttribute('aria-expanded', String(!open)); body.hidden = open; }; });
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
        const EVENT_TONE = e => (/(fail|error|denied|reject|cancel|discard)/i.test(e) ? 'danger' : /(approv|done|pass|push|merged|complete)/i.test(e) ? 'ok' : /(edit|input|wait|needs|human|attention)/i.test(e) ? 'attention' : 'running');
        function renderCosts(panel) {
            const costs = job.costs || {};
            const stages = Object.entries(costs.by_stage || {}).sort((a, b) => b[1] - a[1]);
            const max = Math.max(0.0001, ...stages.map(([, v]) => v));
            const events = (costs.events || []).filter(e => !['sandbox_event', 'checks_output'].includes(e.event_type));
            const times = events.map(e => Number(e.created_at)).filter(Number.isFinite);
            const timeline = times.length > 1 ? (() => { const t0 = Math.min(...times), t1 = Math.max(...times), W = 1000, x = t => 20 + ((t - t0) / Math.max(1, t1 - t0)) * (W - 40);
                return `<svg class="work-timeline" viewBox="0 0 ${W} 90" role="img" aria-label="Timeline: ${events.length} events from ${esc(when(t0))} to ${esc(when(t1))}"><line x1="20" x2="${W - 20}" y1="50" y2="50" class="tl-axis"/>${events.map(e => `<g><title>${esc(`${when(e.created_at)} ${e.event_type}${e.reason ? ` · ${e.reason}` : ''}`)}</title><circle cx="${x(Number(e.created_at))}" cy="50" r="4" class="tl_${EVENT_TONE(e.event_type + ' ' + (e.to_status || ''))}"/></g>`).join('')}<text x="20" y="86" class="tl-text">${esc(when(t0))}</text><text x="${W - 20}" y="86" text-anchor="end" class="tl-text">${esc(when(t1))}</text></svg>`; })() : '';
            panel.innerHTML = `<div class="work-stack"><div class="work-grid-2">
                ${card({id:'cost-h', eyebrow:'Spend', title:'Cost per stage', meta:`<strong class="work-num">${money(costs.total_usd)}</strong>`, body:stages.length ? `<ul class="work-list">${stages.map(([s, v]) => `<li class="work-cost-row"><span class="work-cost-label">${esc(s)}</span>${meterBar(v / max, `${s}: ${money(v)}`)}<span class="work-num">${money(v)}</span></li>`).join('')}</ul>` : '<p class="work-empty-line">No cost recorded.</p>'})}
                <section class="w-card work-card" aria-labelledby="att-h"><header class="w-card__head"><h2 class="w-card__title" id="att-h">Attempts</h2></header><div class="w-card__body work-card-flush" data-attempts>${attemptsTable()}</div></section></div>
                ${card({id:'audit-h', eyebrow:'Audit', title:'Audit timeline', meta:`<span class="w-count">${events.length}</span>`, body:`${timeline}
                    ${events.length ? `<ol class="work-list work-audit">${events.slice().reverse().map(e => `<li><span class="work-audit-dot" data-tone="${EVENT_TONE(e.event_type + ' ' + (e.to_status || ''))}" aria-hidden="true"></span><time class="work-mono work-meta work-nowrap" datetime="${esc(new Date(Number(e.created_at) * 1000).toISOString?.() || '')}">${esc(when(e.created_at))}</time><details><summary><strong>${esc(e.event_type)}</strong>${e.to_status ? ` <span class="work-meta">→ ${esc(e.to_status)}</span>` : ''}${e.reason ? ` <span class="work-meta">${esc(e.reason)}</span>` : ''}</summary><pre class="work-code">${esc(JSON.stringify(e.payload || {}, null, 2))}</pre></details></li>`).join('')}</ol>` : '<p class="work-empty-line">No audit entries.</p>'}`})}</div>`;
            if (!attempts || attempts.id !== selectedId) loadAttempts();
        }
        function attemptsTable() {
            if (!attempts || attempts.id !== selectedId) return '<div class="work-loading" role="status"><span class="w-inkdrop" aria-hidden="true"></span>Loading attempts…</div>';
            if (!attempts.rows.length) return '<p class="work-empty-line">None.</p>';
            const tone = s => (/fail|error/.test(s) ? 'danger' : /run/.test(s) ? 'running' : /succ|done|complete|pass/.test(s) ? 'ok' : 'neutral');
            return `<div class="w-table-wrap work-table-flush"><table class="w-table"><thead><tr><th scope="col" data-num>#</th><th scope="col">Status</th><th scope="col" data-num>Cost</th><th scope="col">Started</th><th scope="col" data-num>Took</th></tr></thead><tbody>${attempts.rows.map(a => `<tr><td data-num>${esc(a.number)}</td><td><span class="work-tag"${toneAttr(tone(a.status) === 'ok' ? '' : tone(a.status))}>${esc(a.status)}</span>${a.error ? `<div class="work-cell-note work-del work-ellipsis" title="${esc(a.error)}">${esc(a.error)}</div>` : ''}</td><td data-num>${money(a.cost_usd)}</td><td class="work-meta work-nowrap">${esc(when(a.started_at))}</td><td data-num class="work-meta">${a.finished_at && a.started_at ? duration((a.finished_at - a.started_at) * 1000) : ''}</td></tr>`).join('')}</tbody></table></div>`;
        }
        async function loadAttempts() {
            const id = selectedId;
            try { const r = await api(`/api/jobs/${encodeURIComponent(id)}/attempts`); attempts = {id, rows:(r.attempts || []).slice().reverse()}; }
            catch (_) { attempts = {id, rows:[]}; }
            if (id === selectedId) { const host = q('[data-attempts]'); if (host) host.innerHTML = attemptsTable(); }
        }

        const renderers = {notes:renderNotes, plan:renderPlan, session:renderSession, diff:renderDiff, review:renderReview, checks:renderChecks, diagnostics:renderDiagnostics, costs:renderCosts};
        function renderPart(part, force = false) {
            if (!tabs.includes(part) || !job) return;
            if (LAZY.includes(part) && (job[part] === undefined || stale.has(part))) { if (part === tab) loadLazy(part); if (job[part] === undefined) return; }
            const value = part === 'diff' && job.diff?.live ? {...job.diff, live:{...job.diff.live, generated_at:0}} : job[part];
            const signature = JSON.stringify([value, part === 'diff' ? job.review : null, job.actions, part === 'session' ? job.state : null]);
            if (!force && signatures.get(part) === signature) return;
            if (part === 'plan' && editor?.isDirty()) return;
            const panel = q(`[data-panel="${part}"]`);
            if (!panel) return;
            renderers[part](panel); signatures.set(part, signature);
        }
        function selectTab(next) {
            if (!tabs.includes(next)) next = 'session';
            if (next !== 'diff' && q('[data-panel="diff"][data-zen]')) { q('[data-panel="diff"]').removeAttribute('data-zen'); document.documentElement.removeAttribute('data-diff-zen'); }
            tab = next;
            qa('[data-tab]').forEach(b => { const on = b.dataset.tab === tab; b.setAttribute('aria-selected', String(on)); b.tabIndex = on ? 0 : -1; });
            qa('[data-panel]').forEach(p => { p.hidden = p.dataset.panel !== tab; });
            renderPart(tab);
            onRoute?.({id:selectedId, tab});
        }
        /* Fetch a lazy part for the open tab; a change to it while hidden only marks it stale. */
        const lazyLoading = new Set();
        async function loadLazy(part) {
            if (lazyLoading.has(part) || !selectedId) return;
            lazyLoading.add(part);
            const id = selectedId;
            try {
                const response = await api(`/api/jobs/vm/job/${encodeURIComponent(id)}?parts=${part}`);
                if (id !== selectedId) return;
                job = {...job, [part]:response[part]}; stale.delete(part);
                if (part === tab) renderPart(part, true);
            } catch (e) { notify(e.message, 'danger'); }
            finally { lazyLoading.delete(part); }
        }
        async function refreshParts(parts = tabs) {
            if (!selectedId) return;
            const id = selectedId;
            const wanted = [...new Set(parts)].filter(p => p !== 'diagnostics' && (!LAZY.includes(p) || p === tab || (stale.add(p), false)));
            if (!wanted.length) return;
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
            const eager = tabs.filter(t => t !== 'diagnostics' && (!LAZY.includes(t) || t === requested));
            const next = await api(`/api/jobs/vm/job/${encodeURIComponent(id)}?parts=${encodeURIComponent(['state', 'pipeline', ...eager].join(','))}`);
            if (request !== fetchNumber) return;
            if (selectedId !== id) {
                editor?.destroy(); editor = null; editing = false; selectedFindings.clear(); signatures.clear(); collapsed.clear(); stale.clear();
                sessionStage = ''; selectedRun = null; failureFilter = ''; showUnavailable = false; attempts = null;
                document.documentElement.removeAttribute('data-diff-zen');
                tab = tabs.includes(requested) ? requested : defaultTab(next); selectedId = id; shell();
            } else if (tabs.includes(requested) && requested !== tab) { tab = requested; }
            const kept = selectedId === id && job ? Object.fromEntries(LAZY.filter(p => job[p] !== undefined && next[p] === undefined).map(p => { stale.add(p); return [p, job[p]]; })) : {};
            job = {...kept, ...next}; detailEl.dataset.jobId = id; renderState(); selectTab(tab); renderHome(); return true;
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
            const onMessage = async event => { try { const change = JSON.parse(event.data); if (change.scope === 'home') await refreshHome(); if (change.scope === 'job' && change.id === selectedId) await refreshParts(change.parts); } catch (e) { notify(e.message, 'danger'); } };
            // Every hello (first connect or a reconnect) refetches, so nothing missed while the
            // stream was down or released (hidden view or tab) stays stale.
            const onHello = () => { onConnection?.(true); refreshHome().catch(() => {}); if (selectedId) refreshParts().catch(() => {}); };
            if (window.MuLive) {
                // Open only while on screen; self-healing (mu_live.js MuLive).
                source = window.MuLive.stream('/api/jobs/events', { onMessage, events: { hello: onHello }, onState: live => onConnection?.(live) });
            } else {
                const connect = () => {
                    source = new EventSource('/api/jobs/events');
                    source.onmessage = onMessage;
                    source.addEventListener('hello', onHello);
                    source.onerror = () => onConnection?.(false);
                    source.onopen = () => onConnection?.(true);
                };
                connect();
                window.addEventListener('pagehide', () => source?.close());
                window.addEventListener('pageshow', event => { if (event.persisted) connect(); });
            }
            setInterval(() => { if (home && document.visibilityState === 'visible') renderHome(); }, 30000);
            setInterval(() => { void refreshLiveDiff(); }, LIVE_DIFF_MS);
        }
        return {start, select, selectTab, leave, runById, refreshHome, refreshParts, listMode, showPreview:id => open(id), previewId:() => '', selectedId:() => selectedId, home:() => home, job:() => job};
    }
    window.MuJobWork = {create, util:{esc, money, duration, age, when, ansi, stripAnsi, pill, statePill, statusIcon, stateLabel, stateStatus, statusOf, verdictPill, severityTone, chipGlyph, icon, jobKey}};
})();
