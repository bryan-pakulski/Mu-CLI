// Alpine stores for the mode panels on the shared shell (documentation/plans/modes_v2.md §5).
//
// Each store loads its mode's GET /api/<mode>/state, exposes the shell bindings the shared
// header (fragments/mode_shell.html) reads — title, sentence(), tabs, tab, primary(), now() —
// and runs the user's actions through the mode's engine routes (the server records the surface
// as the actor). Pure models live in mode_models.js (window.MuModeModels), shared helpers in
// mode_shell.js (window.MuModes).
//
// Also here: `workPlan` (the session's work items: the Plan disclosure above the composer, and
// the default mode's turn receipts in the conversation) and `modeExpand` (the expanded view: a
// mode's heavy surface over the conversation, state in the URL ?mode-view=<mode>&open=<id>).
(function () {
    'use strict';

    function sessionQuery() {
        const chat = window.Alpine && Alpine.store('chat');
        const name = chat && chat.currentName;
        return name ? `?session_name=${encodeURIComponent(name)}` : '';
    }

    async function post(url, body) {
        const r = await fetch(url + sessionQuery(), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body || {}),
        });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) {
            const detail = data && data.detail;
            const message = (detail && (detail.message || detail)) || `Request failed (${r.status})`;
            if (window.WashToast) window.WashToast.show(String(message), 'error');
            return null;
        }
        return data;
    }

    // A one-field (or confirm-only) dialog: resolves the text, true for a confirm, or null.
    function ask(opts) { return Alpine.store('modeAsk').ask(opts); }

    // Put text in the composer for the user to review and send.
    function setDraft(text) {
        const input = document.querySelector('.w-composer__textarea');
        if (!input) return;
        input.value = text;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.focus();
    }

    function openRun(run) {
        if (!run || !window.WashSheet) return;
        const S = window.MuModes;
        const row = S.checkRunRow(run);
        const render = output => window.WashSheet.open({
            title: row.title,
            state: `<span class="w-state__part">${S.esc(row.word)}</span><span class="w-state__part mono">${S.esc(run.id)}</span>`,
            body: S.checkRunSheet(run, output),
            param: { key: 'run', value: run.id },
        });
        render(null);
        fetch(`/api/mode-state/checks/${encodeURIComponent(run.id)}/output${sessionQuery()}`)
            .then(r => (r.ok ? r.json() : null))
            .then(d => { if (d && window.WashSheet.current() === run.id) render(d.output); })
            .catch(() => {});
    }

    function sheet(title, stateParts, body, param) {
        if (!window.WashSheet) return;
        window.WashSheet.open({ title, state: window.MuModes.sentenceHtml(stateParts), body, param });
    }

    // A mode store: state + shell bindings; `extra` adds the mode's own actions.
    function modeStore(mode, endpoint, extra) {
        const model = () => window.MuModeModels[mode];
        return Object.assign({
            mode,
            state: {},
            loaded: false,
            tab: model().tabs ? model().tabs[0] : '',
            title: ({ security: 'Security', debug: 'Debug', feature: 'Feature', loop: 'Loop', teacher: 'Teacher' })[mode] || mode,
            tabs() { return model().tabs || []; },
            setTab(tab) { this.tab = tab; },
            async load() {
                try {
                    const r = await fetch(`${endpoint}${sessionQuery()}`);
                    this.state = await r.json();
                    this.loaded = true;
                    this.afterLoad && this.afterLoad();
                } catch (e) { console.error(`${mode}.load`, e); }
            },
            sentence() { return window.MuModes.sentence(model().sentence(this.state)); },
            sentenceHtml() { return window.MuModes.sentenceHtml(model().sentence(this.state)); },
            now() { return model().now(this.state); },
            primary() { return model().primary(this.state); },
            footWord() { return model().footWord(this.state); },
            runsById() { return Object.fromEntries((this.state.runs || []).map(r => [r.id, r])); },
            openRun(id) { openRun(typeof id === 'string' ? this.runsById()[id] : id); },
            runRow(run) { return window.MuModes.checkRunRow(run); },
            async resolveBlocker(blockerId) {
                const answer = await ask({ title: 'Answer the blocker', label: 'Your answer — the agent sees it on its next turn', confirm: 'Answer' });
                if (answer === null) return;
                if (await post(`/api/mode-state/blockers/${encodeURIComponent(blockerId)}/resolve`, { resolution: answer })) {
                    await this.load();
                }
            },
            async act(row, action) {
                if (action.id === 'resolve') return this.resolveBlocker(row.ref);
                if (this.handle) return this.handle(action.id, row);
            },
            async runPrimary() {
                const p = this.primary();
                if (p && this.handle) await this.handle(p.id, null);
            },
        }, extra || {});
    }

    document.addEventListener('alpine:init', () => {
        const S = () => window.MuModes;
        const MM = () => window.MuModeModels;

        Alpine.store('security', modeStore('security', '/api/security/state', {
            now() {
                const pending = (Alpine.store('prompts').visibleQueue || []).filter(p => p.prompt && p.prompt.requires_desktop);
                const rows = pending.map(p => ({ id: `poc:${p.id}`, ref: p.id, kind: 'safety', status: 'waiting', glyph: '◆',
                    title: 'Review PoC execution', sub: `${p.prompt.tool_args.runner || ''} · ${p.prompt.tool_args.command || ''}`,
                    actions: [{ id: 'review-poc', label: 'Review command', primary: true }] }));
                return S().orderNow([...rows, ...MM().security.now(this.state)]);
            },
            openRun(run) {
                const render = output => sheet(S().checkRunRow(run).title, [run.runner], S().checkRunSheet(run, output), { key: 'run', value: run.id });
                render(null);
                const path = (run.security_output_path || '').split('/').map(encodeURIComponent).join('/');
                fetch(`/api/security/artifacts/${path}${sessionQuery()}`).then(r => r.ok ? r.json() : null).then(data => {
                    if (data && window.WashSheet.current() === run.id) render(data.text);
                }).catch(() => {});
            },
            findings() { return (this.state.findings || []).map(f => ({ ...MM().security.findingRow(f), raw: f })); },
            openFinding(f) { sheet(f.title, [MM().security.findingRow(f).sub], MM().security.findingSheet(f), { key: 'open', value: f.finding_id }); },
            openDiff(f) { Alpine.store('modeExpand').open('security', f.finding_id, { title: f.title, html: `<pre><code>${S().esc(f.patch_diff || '')}</code></pre>` }); },
            openReport() { Alpine.store('modeExpand').open('security', 'report', { title: (this.state.report || {}).title, markdown: this.state.report_markdown || '', artifact: 'REPORT.md' }); },
            async chooseRunner(runner) {
                if (runner === 'host' && !(await ask({ title: 'Enable host execution for this scan?',
                    body: 'PoCs can modify the real workspace and use the host network. Each run still requires command and script review.',
                    confirm: 'Enable host', danger: true }))) return;
                const data = await post('/api/security/runner', { runner });
                if (data) this.state = data;
            },
            async handle(id, row) {
                if (id === 'review-poc') {
                    const prompts = Alpine.store('prompts');
                    prompts.cursor = prompts.visibleQueue.findIndex(p => p.id === row.ref);
                    prompts.focusCard();
                    return;
                }
                let data;
                if (id === 'report') {
                    data = await post('/api/security/report');
                    if (data) { this.state = data; this.openReport(); }
                    return;
                }
                if (id === 'refute') {
                    const reason = await ask({ title: 'Refute hypothesis', label: 'Why is this hypothesis unsupported?', confirm: 'Refute' });
                    if (!reason) return;
                    data = await post(`/api/security/findings/${encodeURIComponent(row.ref)}/refute`, { reason });
                } else if (id === 'approve') {
                    if (!(await ask({ title: 'Approve this verified finding?', confirm: 'Approve' }))) return;
                    data = await post(`/api/security/findings/${encodeURIComponent(row.ref)}/approve`);
                }
                if (data) this.state = data;
            },
        }));

        // ------------------------------------------------------------ debug
        Alpine.store('debug', modeStore('debug', '/api/debug/state', {
            stepper() { return MM().debug.stepper(this.state.case); },
            hypotheses() { return ((this.state.case && this.state.case.hypotheses) || []).map(h => ({ ...MM().debug.hypothesisRow(h), raw: h })); },
            suspects() { return (this.state.case && this.state.case.suspects) || []; },
            runs() { return (this.state.runs || []).map(r => ({ ...S().checkRunRow(r), raw: r })); },
            openHypothesis(h) {
                sheet(h.statement, [S().vstyle('hypothesis', h.status).label], MM().debug.hypothesisSheet(h), { key: 'open', value: h.id });
            },
            openFile(s) {
                // Suspects open in the Files panel.
                Alpine.store('mode').setView('files');
                const files = Alpine.store('files');
                if (files && typeof files.openFile === 'function') files.openFile(s.path);
            },
            async handle(id) {
                if (id === 'close') {
                    const cause = await ask({ title: 'Close case', label: 'Root cause — the defect, not the symptom', confirm: 'Close case' });
                    if (!cause) return;
                    const data = await post('/api/debug/close', { cause });
                    if (data) this.state = data;
                }
            },
            async selectCase(caseId) {
                const data = await post(`/api/debug/cases/${encodeURIComponent(caseId)}/select`);
                if (data) this.state = data;
            },
        }));

        // ------------------------------------------------------------ feature
        Alpine.store('feature', modeStore('feature', '/api/feature/state', {
            plan() { return this.state.plan || null; },
            phases() { return MM().feature.phases(this.plan()); },
            taskRow(t) { return MM().feature.taskRow(t); },
            criteria() {
                const plan = this.plan();
                const out = [];
                for (const t of (plan && plan.tasks) || []) {
                    for (const c of t.criteria || []) {
                        const s = S().vstyle('criterion', MM().feature.criterionKey(c));
                        out.push({ id: `t${t.id}.${c.id}`, task: t, c, status: s.status, glyph: s.glyph, label: s.label });
                    }
                }
                return out;
            },
            reviews() { return (this.plan() && this.plan().review_records) || []; },
            openTask(t) {
                const s = S().vstyle('feature_task', t.status);
                sheet(`t${t.id} ${t.title}`, [s.label], MM().feature.taskSheet(t, this.reviews()), { key: 'open', value: `t${t.id}` });
            },
            async handle(id, row) {
                let data = null;
                if (id === 'approve') data = await post('/api/feature/approve');
                else if (id === 'complete') data = await post('/api/feature/complete');
                else if (id === 'changes') {
                    setDraft('Please change the plan: ');
                    return;
                } else if (id === 'diff-approve' || id === 'diff-reject') {
                    const reason = id === 'diff-reject' ? ((await ask({ title: 'Reject diff', label: 'Why reject it?', confirm: 'Reject', optional: true })) || '') : '';
                    data = await post(`/api/feature/diffs/${encodeURIComponent(row.ref)}/decide`,
                        { decision: id === 'diff-approve' ? 'approved' : 'rejected', reason });
                }
                if (data) this.state = data;
            },
            async startTask(t) {
                const data = await post(`/api/feature/tasks/${t.id}/start`);
                if (data) this.state = data;
            },
            async attest(t, c) {
                const note = await ask({ title: 'Mark criterion met', label: `Why is "${c.text}" met? (recorded as your attestation)`, confirm: 'Mark met' });
                if (!note) return;
                const data = await post(`/api/feature/tasks/${t.id}/criteria/${encodeURIComponent(c.id)}/attest`, { note });
                if (data) this.state = data;
            },
            async override(t) {
                const reason = await ask({ title: 'Override', label: 'Complete without the missing evidence — the reason is recorded', confirm: 'Complete anyway', danger: true });
                if (!reason) return;
                const data = await post(`/api/feature/tasks/${t.id}/complete`, { override_reason: reason });
                if (data) this.state = data;
            },
            async loadFeature(featureId) {
                if (await post(`/api/feature/${encodeURIComponent(featureId)}/load`)) await this.load();
            },
        }));

        // ------------------------------------------------------------ loop
        Alpine.store('loop', modeStore('loop', '/api/loop/state', {
            mission() { return this.state.mission || null; },
            meter() { return MM().loop.meter(this.state); },
            criteria() { return ((this.mission() && this.mission().done_when) || []).map(c => MM().loop.criterionRow(c)); },
            timeline() { return [...((this.mission() && this.mission().increments) || [])].reverse().map(i => ({ ...MM().loop.incrementRow(i), raw: i })); },
            backlog() { return MM().receipt.planRows(this.state.backlog || []).concat(
                (this.state.backlog || []).filter(i => i.status === 'done').map(i => ({ id: i.id, status: 'done', glyph: '✓', title: i.title,
                    sub: (i.evidence || []).length ? `done · ${i.evidence.join(', ')}` : 'done' }))); },
            openIncrement(inc) {
                sheet(`${inc.n} ${inc.objective}`, [S().vstyle('loop_increment', inc.result).label], MM().loop.incrementSheet(inc),
                    { key: 'open', value: String(inc.n) });
            },
            async handle(id) {
                let data = null;
                if (id === 'pause' || id === 'resume' || id === 'stop') {
                    if (id === 'stop' && !(await ask({ title: 'Stop the mission?', body: 'A stopped mission cannot be resumed.', confirm: 'Stop mission', danger: true }))) return;
                    data = await post('/api/loop/control', { action: id });
                } else if (id === 'start') {
                    const goal = await ask({ title: 'Start a mission', label: 'Goal — what the loop works toward', confirm: 'Start' });
                    if (!goal) return;
                    data = await post('/api/loop/start', { goal });
                } else if (id === 'add-item') {
                    const content = await ask({ title: 'Add backlog item', label: 'What should the loop do?', confirm: 'Add' });
                    if (!content) return;
                    data = await post('/api/loop/backlog', { content });
                } else if (id === 'budget') {
                    const cost = await ask({ title: 'Raise budget', label: 'Cost budget in USD', confirm: 'Set budget' });
                    if (!cost) return;
                    data = await post('/api/loop/budget', { max_cost_usd: Number(cost) });
                }
                if (data) this.state = data;
            },
        }));

        // ------------------------------------------------------------ teacher
        Alpine.store('teacher', modeStore('teacher', '/api/teacher/state', {
            course() { return this.state.course || null; },
            path() { return MM().teacher.path(this.course()); },
            lessonRow(l) { return MM().teacher.lessonRow(l); },
            mastery() { return ((this.course() && this.course().mastery) || []).map(r => MM().teacher.masteryRow(r)); },
            reviews() {
                const c = this.course();
                return [...((c && c.due_reviews) || []), ...((c && c.upcoming_reviews) || [])]
                    .filter((r, i, all) => all.findIndex(x => x.review_id === r.review_id) === i)
                    .map(r => MM().teacher.reviewRow(r));
            },
            currentLesson() {
                const c = this.course();
                return c ? (c.lessons || []).find(l => l.lesson_id === c.current_lesson_id) || null : null;
            },
            lessonChecks() {
                const c = this.course(); const l = this.currentLesson();
                return c && l ? (c.checks || []).filter(k => k.lesson_id === l.lesson_id).map(k => MM().teacher.checkRow(k)) : [];
            },
            lessonAssignments() {
                const c = this.course(); const l = this.currentLesson();
                return c && l ? (c.assignments || []).filter(a => a.lesson_id === l.lesson_id) : [];
            },
            openLesson(l) {
                const c = this.course();
                const checks = (c.checks || []).filter(k => k.lesson_id === l.lesson_id).map(k => {
                    const row = MM().teacher.checkRow(k);
                    return `<li class="w-row"><span class="w-sign" data-status="${S().esc(row.status)}" aria-hidden="true">${S().esc(row.glyph)}</span>
                        <div class="w-row__main"><span class="w-row__title">${S().esc(row.title)}</span><span class="w-row__sub">${S().esc(row.sub)}</span></div></li>`;
                }).join('');
                const grades = (c.assignments || []).filter(a => a.lesson_id === l.lesson_id)
                    .map(a => `<h3 class="w-group__title">${S().esc(a.assignment_id)}</h3>${MM().teacher.gradeSheet(a)}`).join('');
                const objectives = (l.learning_objectives || []).map(o => `<li>${S().esc(o)}</li>`).join('');
                sheet(l.title, [MM().teacher.lessonGateLine(l)],
                    `<h3 class="w-group__title">Objectives</h3><ul>${objectives}</ul>
                     <h3 class="w-group__title">Checks</h3><ul class="w-rows">${checks || '<li class="w-group__empty">No checks yet.</li>'}</ul>${grades}
                     <details class="w-disclose"><summary>Details</summary><dl class="w-facts"><dt>Lesson</dt><dd class="mono">${S().esc(l.lesson_id)}</dd>
                     <dt>Learner profile</dt><dd>${S().esc(Object.entries(c.learner_profile || {}).filter(([k, v]) => v && !['recorded_at', 'prior'].includes(k)).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : v}`).join(' · '))}</dd></dl></details>`,
                    { key: 'open', value: l.lesson_id });
            },
            async openTranscript(l) {
                const r = await fetch(`/api/teacher/lessons/${encodeURIComponent(l.lesson_id)}/lecture${sessionQuery()}`);
                if (!r.ok) { if (window.WashToast) window.WashToast.show('No transcript for this lesson yet.', 'info'); return; }
                const d = await r.json();
                Alpine.store('modeExpand').open('teacher', l.lesson_id, { title: l.title, markdown: d.content });
            },
            async handle(id, row) {
                let data = null;
                if (id === 'approve') data = await post('/api/teacher/approve');
                else if (id === 'start-review' || id === 'open-exercise') {
                    setDraft(id === 'start-review' ? `Let's do the review ${row.ref} now.` : `I'm ready for exercise ${row.ref}.`);
                    return;
                }
                if (data) this.state = data;
            },
            async startOutOfOrder(l) {
                const reason = await ask({ title: 'Start out of order', label: 'Why start this lesson now? (recorded)', confirm: 'Start lesson' });
                if (!reason) return;
                const data = await post(`/api/teacher/lessons/${encodeURIComponent(l.lesson_id)}/start`, { override_reason: reason });
                if (data) this.state = data;
            },
            async finishEarly() {
                const reason = await ask({ title: 'Finish early', label: 'Why finish before every lesson passed? (recorded)', confirm: 'Finish early', danger: true });
                if (!reason) return;
                const data = await post('/api/teacher/finish-early', { reason });
                if (data) this.state = data;
            },
        }));

        // ------------------------------------------------------------ the plan + turn receipts
        Alpine.store('workPlan', {
            items: [],
            open: false,
            async load() {
                try {
                    const r = await fetch(`/api/mode-state/work${sessionQuery()}`);
                    if (!r.ok) return;
                    const d = await r.json();
                    this.items = d.items || [];
                } catch (e) { /* no session yet */ }
            },
            rows() { return MM().receipt.planRows(this.items); },
            currentTitle() {
                const cur = this.items.find(i => i.status === 'in_progress');
                return cur ? cur.title : '';
            },
            openReceipt(receipt) {
                const mark = MM().receipt.mark(receipt);
                sheet('Turn receipt', [mark.label, `${(receipt.files || []).length} files`], MM().receipt.sheet(receipt));
            },
        });

        // ------------------------------------------------------------ the shell's one-field dialog
        Alpine.store('modeAsk', {
            visible: false, title: '', label: '', body: '', confirmLabel: 'OK', danger: false, optional: false,
            value: '', _resolve: null,
            ask(opts) {
                if (this._resolve) this._resolve(null);
                Object.assign(this, { visible: true, title: opts.title || '', label: opts.label || '', body: opts.body || '',
                    confirmLabel: opts.confirm || 'OK', danger: !!opts.danger, optional: !!opts.optional, value: '' });
                return new Promise(resolve => { this._resolve = resolve; });
            },
            submit() {
                if (this.label && !this.optional && !this.value.trim()) return;
                const r = this._resolve; this._resolve = null; this.visible = false;
                if (r) r(this.label ? this.value.trim() : true);
            },
            cancel() {
                const r = this._resolve; this._resolve = null; this.visible = false;
                if (r) r(null);
            },
        });

        // ------------------------------------------------------------ expanded view (§5.2)
        Alpine.store('modeExpand', {
            mode: null,
            item: null,
            content: null,
            open(mode, item, content) {
                this.mode = mode;
                this.item = item || null;
                this.content = content || null;
                try {
                    const url = new URL(location.href);
                    url.searchParams.set('mode-view', mode);
                    if (item) url.searchParams.set('open', item); else url.searchParams.delete('open');
                    history.replaceState(history.state, '', url.pathname + url.search + url.hash);
                } catch (e) { /* non-browser */ }
            },
            close() {
                this.mode = null; this.item = null; this.content = null;
                try {
                    const url = new URL(location.href);
                    url.searchParams.delete('mode-view'); url.searchParams.delete('open');
                    history.replaceState(history.state, '', url.pathname + url.search + url.hash);
                } catch (e) { /* non-browser */ }
            },
            async openSecurityLink(event) {
                if (this.mode !== 'security') return;
                const link = event.target.closest('a');
                const href = link && link.getAttribute('href');
                if (!href || /^(?:[a-z]+:|#|\/)/i.test(href)) return;
                event.preventDefault();
                const base = new URL(`/security-artifacts/${(this.content || {}).artifact || 'REPORT.md'}`, location.origin);
                const target = new URL(href, base);
                if (!target.pathname.startsWith('/security-artifacts/')) return;
                const path = target.pathname.slice('/security-artifacts/'.length);
                const response = await fetch(`/api/security/artifacts/${path}${sessionQuery()}`);
                if (!response.ok) return;
                const data = await response.json();
                this.content = { title: data.path, artifact: data.path,
                    ...(data.format === 'markdown' ? { markdown: data.text } : { html: `<pre><code>${S().esc(data.text)}</code></pre>` }) };
            },
            html() {
                const c = this.content;
                if (!c) return '';
                if (c.markdown != null && window.marked) {
                    const raw = window.marked.parse(String(c.markdown));
                    return window.DOMPurify ? window.DOMPurify.sanitize(raw) : S().esc(c.markdown);
                }
                return c.html || '';
            },
            async restore() {
                // A reload (or a shared link) with ?mode-view=feature&open=t4 reopens the view.
                try {
                    const params = new URL(location.href).searchParams;
                    const mode = params.get('mode-view');
                    if (mode === 'feature') this.open('feature', params.get('open'));
                    else if (mode === 'security') {
                        const store = Alpine.store('security');
                        await store.load();
                        if (params.get('open') === 'report') store.openReport();
                        else { const f = (store.state.findings || []).find(f => f.finding_id === params.get('open')); if (f) store.openDiff(f); }
                    }
                    else if (mode === 'teacher' && params.get('open')) {
                        const store = Alpine.store('teacher');
                        await store.load();
                        const lesson = ((store.course() || {}).lessons || []).find(l => l.lesson_id === params.get('open'));
                        if (lesson) store.openTranscript(lesson);
                    }
                } catch (e) { /* ignore */ }
            },
        });

        document.addEventListener('keydown', event => {
            if (event.key === 'Escape' && Alpine.store('modeExpand').mode) Alpine.store('modeExpand').close();
        });
    });

    // Turn receipts arrive on the event stream (kind "turn_receipt"); app.js's routeEvent hands
    // them here so the row sits under the turn's last assistant message.
    window.MuModeReceipts = {
        attach(chat, slot, receipt, line) {
            const turns = (slot && slot.turns) || [];
            for (let i = turns.length - 1; i >= 0; i--) {
                if (turns[i].role === 'assistant') {
                    turns[i].receipt = { ...receipt, line: line || window.MuModeModels.receipt.line(receipt),
                        mark: window.MuModeModels.receipt.mark(receipt) };
                    return true;
                }
            }
            return false;
        },
    };
})();
