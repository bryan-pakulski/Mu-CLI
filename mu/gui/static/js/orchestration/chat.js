/* Fleet chat: ask about your nodes and runs, and run things, without leaving Orchestration.
 *
 * A panel docked on the right of every orchestration route, backed by one `orchestration`
 * session (POST /api/orchestration/chat/session; loaded without focusing it, so the main chat
 * keeps its own session). The model has the orch_* tools: it looks things up, drafts tools and
 * *proposes* runs. It can never approve, dispatch or touch policy (AI_COMMANDS); a run it
 * proposes appears here as a card whose Approve / Reject buttons are the operator's own
 * commands, exactly as on the run page (typed confirmation included). Tool approvals and
 * questions the model asks are answered inline.
 *
 * Transport is the main chat's: POST /api/chat/send {text, session_name}, the session's SSE
 * stream (/api/events?session=), POST /api/prompts/{id}/answer, POST /api/chat/interrupt, and
 * GET /api/sessions/current/history?session_name= on open.
 *
 * Orch.chat = {open, close, toggle, isOpen, ask(text)} ; "." toggles it.
 */
(function () {
    'use strict';
    const Orch = (window.Orch = window.Orch || { routes: {} });
    const api = window.OrchAPI;
    const ui = window.OrchUI;
    const { esc, icon } = ui;
    const RUN_ID = /\br_[0-9a-f]{12}\b/g;
    /* Only these tools start or propose a run: their result becomes a run card with your
     * decision. Run ids in other results (catalog search lists recent runs) stay small links. */
    const PROPOSES = new Set(['orch_run_propose', 'orch_tool_test', 'orch_runbook_test']);
    const toolLabel = name => String(name || 'tool').replace(/^orch_/, '').replace(/_/g, ' ');
    const STORE = 'mucli-orch-chat-open';
    const SUGGEST = [
        'Which nodes need attention right now?',
        'How full are the disks across prod?',
        'Check for pending security updates on every node.',
        'What failed in the last run, and why?',
    ];

    const state = {
        open: false, session: '', model: '', busy: false, source: null, loaded: false,
        turnEl: null, turnText: '', runs: new Map(), withContext: true, lastSent: '', group: null, step: null,
    };
    let panel, log, input, sendBtn, ctxEl;

    function build() {
        panel = document.getElementById('orch-chat');
        if (!panel) return false;
        panel.innerHTML = `
            <header class="orch-chat__head">
                <div class="orch-chat__title"><h2 class="w-group__title" id="orch-chat-title">Fleet chat</h2><span class="orch-sub" data-chat-model></span></div>
                <button type="button" class="w-textbtn" data-tone="quiet" data-chat-new title="Start a new conversation">${icon('plus')}<span>New</span></button>
                <button type="button" class="w-btn" data-variant="icon" data-chat-close aria-label="Close the fleet chat" title="Close (.)">${icon('x')}</button>
            </header>
            <div class="orch-chat__log" data-chat-log role="log" aria-live="polite" aria-labelledby="orch-chat-title"></div>
            <div class="orch-chat__ctx" data-chat-ctx hidden></div>
            <form class="orch-chat__composer" data-chat-form>
                <textarea class="w-input orch-chat__input" rows="2" placeholder="Ask about your nodes, or ask it to run something…" aria-label="Message the fleet chat" data-chat-input></textarea>
                <button type="submit" class="w-btn" data-variant="primary" data-size="sm" data-chat-send>${icon('arrow-up')}<span>Send</span></button>
            </form>
            <p class="orch-chat__foot orch-sub">It can propose runs; only you can approve them.</p>`;
        log = panel.querySelector('[data-chat-log]');
        input = panel.querySelector('[data-chat-input]');
        sendBtn = panel.querySelector('[data-chat-send]');
        ctxEl = panel.querySelector('[data-chat-ctx]');
        panel.querySelector('[data-chat-close]').onclick = () => close();
        panel.querySelector('[data-chat-new]').onclick = () => fresh();
        panel.querySelector('[data-chat-form]').onsubmit = e => { e.preventDefault(); state.busy ? stop() : send(); };
        input.addEventListener('keydown', e => {
            if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (!state.busy) send(); }
            if (e.key === 'Escape') { e.preventDefault(); close(); }
        });
        input.addEventListener('input', () => { input.style.height = 'auto'; input.style.height = `${Math.min(input.scrollHeight, 200)}px`; });
        window.addEventListener('hashchange', paintContext);
        return true;
    }

    /* ------------------------------------------------------------ open / close */
    async function open({ focus = true } = {}) {
        if (!panel && !build()) return;
        state.open = true;
        panel.hidden = false;
        document.getElementById('orch-app')?.setAttribute('data-chat', 'open');
        try { localStorage.setItem(STORE, '1'); } catch (_) { /* private mode */ }
        window.WashMotion?.enter(panel);
        Orch.repaintHead?.();
        paintContext();
        if (!state.loaded) await connect();
        if (focus) input.focus();
    }
    function close() {
        if (!panel) return;
        state.open = false;
        panel.hidden = true;
        document.getElementById('orch-app')?.removeAttribute('data-chat');
        try { localStorage.removeItem(STORE); } catch (_) { /* private mode */ }
        Orch.repaintHead?.();
    }
    const toggle = () => (state.open ? close() : open());

    async function connect(fresh = false) {
        log.innerHTML = `<p class="orch-chat__hint orch-sub">${fresh ? 'Starting a new conversation…' : 'Connecting…'}</p>`;
        let info;
        try {
            info = await api.post('chat/session', fresh ? { fresh: true } : {});
        } catch (err) {
            /* No model to borrow (no chat session open, none used before): ask for one, once;
             * the server remembers it for the fleet chat. */
            if (err.status === 400 && Orch.studio?.chooseModel) {
                paintNeedsModel(fresh);
                askModel(fresh);
                return;
            }
            log.innerHTML = `<div class="orch-chat__hint">${ui.banner('attention', '◆', `<strong>The fleet chat isn’t available.</strong> ${esc(err.message)}`)}</div>`;
            return;
        }
        await adopt(info);
    }
    function paintNeedsModel(fresh) {
        log.innerHTML = `<div class="orch-chat__hint"><p class="orch-sub">Choose the model the fleet chat uses.</p>
            <button type="button" class="w-btn" data-variant="secondary" data-size="sm" data-chat-model-pick>Choose a model</button></div>`;
        log.querySelector('[data-chat-model-pick]').onclick = () => askModel(fresh);
    }
    function askModel(fresh) {
        Orch.studio.chooseModel(async extra => adopt(await api.post('chat/session', { ...extra, ...(fresh ? { fresh: true } : {}) })), {
            title: 'Fleet chat model', ok: 'Start chatting',
            lede: 'No chat session is open to borrow a model from. Choose the one the fleet chat uses; it’s remembered.',
        });
    }
    async function adopt(info) {
        state.session = info.session_name;
        state.model = info.model || '';
        state.loaded = true;
        state.runs.clear();
        panel.querySelector('[data-chat-model]').textContent = state.model;
        await loadHistory();
        listen();
        setBusy(!!info.busy);
    }
    async function fresh() {
        if (state.busy) await stop();
        state.source?.close();
        state.loaded = false;
        await connect(true);
        input.focus();
    }

    async function loadHistory() {
        let data;
        try { data = await api.get(`/api/sessions/current/history?session_name=${encodeURIComponent(state.session)}&limit_turns=40`); }
        catch (_) { data = { turns: [] }; }
        log.innerHTML = '';
        for (const turn of data.turns || []) {
            const role = turn.role === 'user' ? 'user' : 'assistant';
            for (const part of turn.parts || []) {
                if (part.type === 'text' && part.text?.trim()) {
                    endSteps();
                    if (role === 'user') addUser(stripContext(part.text));
                    else addAssistant(part.text);
                } else if (part.type === 'tool_call') addTool(part.tool_name);
                else if (part.type === 'tool_result') toolResult(String(part.preview || ''), part.tool_name);
            }
        }
        endSteps();
        if (!log.children.length) paintEmpty();
        scroll(true);
    }

    function paintEmpty() {
        log.innerHTML = `<div class="orch-chat__empty"><p>Ask about your fleet, or ask for something to be done. Runs it proposes wait for your approval.</p>
            <div class="orch-chat__suggest">${SUGGEST.map(s => `<button type="button" class="w-textbtn" data-chat-suggest>${esc(s)}</button>`).join('')}</div></div>`;
        log.querySelectorAll('[data-chat-suggest]').forEach(b => { b.onclick = () => { input.value = b.textContent; send(); }; });
    }

    /* ------------------------------------------------------------ the stream */
    function listen() {
        state.source?.close();
        const src = new EventSource(`/api/events?session=${encodeURIComponent(state.session)}`);
        state.source = src;
        /* Every event is an SSE "message"; its sequence rides as the event id (replay on reconnect). */
        src.onmessage = e => {
            if (e.lastEventId) { if (seen.has(e.lastEventId)) return; seen.add(e.lastEventId); }
            try { handle(JSON.parse(e.data)); } catch (err) { console.error(err); }
        };
    }
    const seen = new Set();
    function handle(ev) {
        if (!ev) return;
        if (ev.kind === 'hello') { setBusy((ev.busy || []).includes(state.session)); return; }
        if (ev.session_name && ev.session_name !== state.session) return;
        switch (ev.kind) {
            case 'assistant_delta':
                setBusy(true);
                if (!state.turnEl) { endSteps(); state.turnText = ''; state.turnEl = addAssistant(''); }
                state.turnText += ev.text || '';
                paintMarkdown(state.turnEl, state.turnText);
                scroll();
                break;
            case 'user_message':
                /* Our own message is already shown; one sent elsewhere (another tab) is added. */
                if (stripContext(ev.text || '') !== state.lastSent) addUser(stripContext(ev.text || ''));
                state.lastSent = '';
                break;
            case 'assistant_end':
                state.turnEl = null;
                break;
            case 'tool_call':
                setBusy(true);
                state.turnEl = null;
                addTool(ev.tool_name);
                break;
            case 'tool_result':
                toolResult(String(ev.text || ''), ev.tool_name);
                break;
            case 'prompt':
                if ((ev.prompt?.session_name || ev.session_name) === state.session) addPrompt(ev.id, ev.prompt || {});
                break;
            case 'prompt_resolved':
            case 'prompt_cancelled':
                log.querySelector(`[data-prompt="${CSS.escape(String(ev.id))}"]`)?.remove();
                break;
            case 'turn_complete':
                state.turnEl = null;
                endSteps();
                setBusy(false);
                refreshRuns();
                break;
            case 'error':
                addNote(ev.text || 'Something went wrong.', 'danger');
                setBusy(false);
                break;
            case 'history_refresh':
                if (!state.busy) loadHistory();
                break;
            default:
        }
    }
    /* ------------------------------------------------------------ sending */
    function stripContext(text) { return String(text).replace(/^\[Looking at [^\]]*\]\n\n/, ''); }
    function contextLine() {
        const c = state.withContext && Orch.context?.();
        return c ? `[Looking at ${c.kind} ${c.label} (${c.id}) in Orchestration]\n\n` : '';
    }
    function paintContext() {
        if (!ctxEl) return;
        const c = Orch.context?.();
        if (!c) { ctxEl.hidden = true; return; }
        ctxEl.hidden = false;
        ctxEl.innerHTML = state.withContext
            ? `<span class="orch-sub">About</span> <span class="orch-chat__chip">${esc(c.kind)} ${esc(c.label)}</span><button type="button" class="w-textbtn" data-tone="quiet" data-chat-ctx-off aria-label="Don't include this ${esc(c.kind)}">${icon('x')}</button>`
            : `<button type="button" class="w-textbtn" data-tone="quiet" data-chat-ctx-on>Ask about this ${esc(c.kind)}</button>`;
        ctxEl.querySelector('[data-chat-ctx-off]')?.addEventListener('click', () => { state.withContext = false; paintContext(); });
        ctxEl.querySelector('[data-chat-ctx-on]')?.addEventListener('click', () => { state.withContext = true; paintContext(); });
    }
    async function send(textArg) {
        const text = String(textArg ?? input.value).trim();
        if (!text || !state.session) return;
        if (log.querySelector('.orch-chat__empty')) log.innerHTML = '';
        endSteps();
        addUser(text);
        state.lastSent = text;
        input.value = '';
        input.style.height = 'auto';
        setBusy(true);
        scroll(true);
        try {
            await api.post('/api/chat/send', { text: contextLine() + text, session_name: state.session });
        } catch (err) {
            addNote(err.status === 409 ? 'It’s still answering. Wait for it, or stop it first.' : `Couldn’t send: ${err.message}`, 'danger');
            setBusy(false);
        }
    }
    async function stop() {
        try { await api.post('/api/chat/interrupt', { session_name: state.session }); } catch (_) { /* toasted below */ }
        setBusy(false);
    }
    function setBusy(on) {
        state.busy = on;
        if (!sendBtn) return;
        sendBtn.innerHTML = on ? '<span>Stop</span>' : `${icon('arrow-up')}<span>Send</span>`;
        sendBtn.dataset.variant = on ? 'secondary' : 'primary';
        panel.toggleAttribute('data-busy', on);
        typing(on);
    }
    /* The animated "…" at the end of the log while it works (a turn in flight). */
    let typingEl = null;
    function typing(on) {
        if (!log) return;
        if (!on) { typingEl?.remove(); return; }
        if (!typingEl) {
            typingEl = document.createElement('div');
            typingEl.className = 'orch-chat__typing';
            typingEl.setAttribute('role', 'status');
            typingEl.innerHTML = '<span class="w-sr-only">Working…</span><span aria-hidden="true"></span><span aria-hidden="true"></span><span aria-hidden="true"></span>';
        }
        if (!typingEl.isConnected || log.lastElementChild !== typingEl) log.appendChild(typingEl);
        scroll();
    }

    /* ------------------------------------------------------------ messages */
    function add(cls, html) {
        log.querySelector('.orch-chat__empty')?.remove();
        log.querySelector('.orch-chat__hint')?.remove();
        const el = document.createElement('div');
        el.className = `orch-chat__msg ${cls}`;
        el.innerHTML = html;
        log.insertBefore(el, typingEl && typingEl.parentNode === log ? typingEl : null);
        return el;
    }
    function addUser(text) { return add('is-user', `<p>${esc(text).replace(/\n/g, '<br>')}</p>`); }
    function addAssistant(text) { const el = add('is-assistant', ''); paintMarkdown(el, text); return el; }
    function addNote(text, tone) { const el = add('is-note', `<p class="orch-sub"${tone ? ` data-tone="${tone}"` : ''}>${esc(text)}</p>`); scroll(); return el; }
    /* Tool calls: one small "Used N tools" disclosure per stretch of calls between messages,
     * each step with a short preview of its result inside. */
    function stepsGroup() {
        if (state.group?.isConnected) return state.group;
        const el = add('is-steps', `<details class="orch-chat__steps"><summary><span class="w-sign" data-status="active" aria-hidden="true">●</span><span data-steps-label>Working…</span></summary><ol class="orch-chat__steplist"></ol></details>`);
        state.group = el;
        return el;
    }
    function addTool(name) {
        const group = stepsGroup();
        settleStep();
        const li = document.createElement('li');
        li.className = 'orch-chat__step';
        li.dataset.tool = String(name || '');
        li.innerHTML = `<span class="w-sign" data-status="active" aria-hidden="true">●</span><span class="orch-chat__stepname">${esc(toolLabel(name))}</span>`;
        group.querySelector('ol').appendChild(li);
        state.step = li;
        labelSteps(group);
        scroll();
        return li;
    }
    function toolResult(text, name) {
        const li = state.step && (!name || state.step.dataset.tool === name) ? state.step
            : [...(state.group?.querySelectorAll('.orch-chat__step') || [])].reverse().find(x => !name || x.dataset.tool === name);
        const tool = name || li?.dataset.tool || '';
        if (li && !li.querySelector('.orch-chat__stepout')) {
            const ids = [...new Set(String(text).match(RUN_ID) || [])];
            const preview = String(text).trim().slice(0, 600);
            li.insertAdjacentHTML('beforeend', `<div class="orch-chat__stepout">${preview ? `<pre>${esc(preview)}${text.length > 600 ? '…' : ''}</pre>` : ''}${
                ids.length && !PROPOSES.has(tool) ? `<p class="orch-chat__steplinks">${ids.slice(0, 12).map(id => `<a href="#/run/${encodeURIComponent(id)}">${esc(id)}</a>`).join(' ')}${ids.length > 12 ? ` +${ids.length - 12}` : ''}</p>` : ''}</div>`);
        }
        settleStep();
        if (state.group) labelSteps(state.group);
        if (PROPOSES.has(tool)) noteRuns(text);
    }
    function settleStep() {
        const sign = state.step?.querySelector('.w-sign[data-status="active"]');
        if (sign) { sign.dataset.status = 'done'; sign.textContent = '✓'; }
        state.step = null;
    }
    function labelSteps(group, done = false) {
        const steps = [...group.querySelectorAll('.orch-chat__step')];
        const names = [...new Set(steps.map(x => toolLabel(x.dataset.tool)))];
        const running = !done && steps.some(x => x.querySelector('.w-sign[data-status="active"]'));
        const head = group.querySelector('summary .w-sign');
        head.dataset.status = running ? 'active' : 'done';
        head.textContent = running ? '●' : '✓';
        const shown = names.slice(0, 3).join(', ') + (names.length > 3 ? ` +${names.length - 3}` : '');
        group.querySelector('[data-steps-label]').textContent = running
            ? `Working: ${toolLabel(steps[steps.length - 1]?.dataset.tool)}`
            : `Used ${steps.length} tool${steps.length === 1 ? '' : 's'} · ${shown}`;
    }
    /* A message (or the end of the turn) closes the current stretch of tool calls. */
    function endSteps() {
        settleStep();
        if (state.group?.isConnected) {
            state.group.querySelectorAll('.w-sign[data-status="active"]').forEach(sign => { sign.dataset.status = 'done'; sign.textContent = '✓'; });
            labelSteps(state.group, true);
        }
        state.group = null;
    }
    function paintMarkdown(el, text) {
        const md = window.marked?.parse ? window.marked.parse(String(text || ''), { breaks: true }) : `<p>${esc(text)}</p>`;
        el.innerHTML = window.DOMPurify ? window.DOMPurify.sanitize(md, { USE_PROFILES: { html: true }, FORBID_TAGS: ['style', 'form', 'input'] }) : `<p>${esc(text)}</p>`;
        el.querySelectorAll('a[href]').forEach(a => { if (!/^#\//.test(a.getAttribute('href'))) { a.target = '_blank'; a.rel = 'noopener noreferrer'; } });
    }
    function scroll(force) {
        const near = log.scrollHeight - log.scrollTop - log.clientHeight < 120;
        if (force || near) log.scrollTop = log.scrollHeight;
    }

    /* ------------------------------------------------------------ runs it proposed */
    function noteRuns(text) {
        const ids = [...new Set(String(text).match(RUN_ID) || [])];
        ids.forEach(id => {
            if (state.runs.has(id)) return;
            const el = add('is-run', '<p class="orch-sub">Loading the run…</p>');
            el.dataset.chatRun = id;
            state.runs.set(id, el);
            paintRun(id);
        });
        scroll();
    }
    async function paintRun(id) {
        const el = state.runs.get(id);
        if (!el) return;
        let run;
        try { run = await api.get(`runs/${encodeURIComponent(id)}`); }
        catch (_) { el.innerHTML = `<p class="orch-sub">Run ${esc(id)} isn’t available.</p>`; return; }
        const href = `#/run/${encodeURIComponent(id)}`;
        el.innerHTML = ui.rows([ui.row({
            status: ui.statusKey(run.style), title: run.title, href,
            sub: ui.subline([esc(ui.cap(run.style?.label || run.state)), run.decision?.summary ? esc(run.decision.summary) : '', run.check_mode ? 'dry run' : '',
                run.progress ? esc(run.progress) : '']),
            pinned: run.state === 'proposed',
            actions: ui.rowActions(run.actions, { only: ['approveRun', 'rejectRun', 'continueRun', 'cancelRun'] }) + `<a class="w-textbtn" data-tone="quiet" href="${href}">Open</a>`,
        })]);
        ui.bindActions(el, run.actions, { args: { run_id: id }, subject: run.title, done: () => paintRun(id) });
    }
    function refreshRuns() { state.runs.forEach((_, id) => paintRun(id)); }

    /* ------------------------------------------------------------ prompts */
    function addPrompt(id, p) {
        if (log.querySelector(`[data-prompt="${CSS.escape(String(id))}"]`)) return;
        const shape = p.shape || 'input';
        let body = '';
        if (shape === 'tool_approval') {
            const args = p.tool_args ? JSON.stringify(p.tool_args, null, 2) : '';
            body = `<p><strong>Allow ${esc(String(p.tool_name || 'this tool').replace(/^orch_/, '').replace(/_/g, ' '))}?</strong> ${esc(p.description || '')}</p>
                ${args ? `<details class="w-disclose"><summary>Arguments</summary><pre class="orch-chat__args">${esc(args.slice(0, 4000))}</pre></details>` : ''}
                <div class="w-btn-row"><button type="button" class="w-btn" data-variant="primary" data-size="sm" data-answer='{"approved":true}'>Allow</button>
                <button type="button" class="w-btn" data-variant="secondary" data-size="sm" data-answer='{"approved":false}'>Deny</button></div>`;
        } else if (shape === 'confirm') {
            body = `<p>${esc(p.message)}</p><div class="w-btn-row"><button type="button" class="w-btn" data-variant="primary" data-size="sm" data-answer='{"value":true}'>Yes</button>
                <button type="button" class="w-btn" data-variant="secondary" data-size="sm" data-answer='{"value":false}'>No</button></div>`;
        } else if (shape === 'choice' || shape === 'choices') {
            const opts = (p.options || p.choices || []).map(o => (typeof o === 'string' ? { label: o } : o));
            body = `<p><strong>${esc(p.question || p.message || 'Choose')}</strong>${p.description ? ` ${esc(p.description)}` : ''}</p>
                <div class="orch-chat__choices">${opts.map(o => `<button type="button" class="w-btn" data-variant="secondary" data-size="sm" data-choice="${esc(o.label ?? o.value ?? '')}" title="${esc(o.description || '')}">${esc(o.label ?? o.value ?? '')}</button>`).join('')}</div>
                ${p.allow_other || shape === 'choice' ? '<div class="orch-chat__other"><input class="w-input" data-other placeholder="Or type an answer…"><button type="button" class="w-textbtn" data-other-send>Send</button></div>' : ''}`;
        } else {
            body = `<p>${esc(p.message || p.question || 'The model is asking:')}</p><div class="orch-chat__other"><input class="w-input" data-other value="${esc(p.default || '')}"><button type="button" class="w-textbtn" data-other-send>Send</button></div>`;
        }
        const el = add('is-prompt', `<div class="orch-chat__prompt" role="group" aria-label="The model is waiting for you">${body}</div>`);
        el.dataset.prompt = id;
        const answer = async value => {
            el.querySelectorAll('button, input').forEach(b => { b.disabled = true; });
            try { await api.post(`/api/prompts/${encodeURIComponent(id)}/answer`, value); el.remove(); }
            catch (err) { ui.toast(`Couldn’t answer: ${err.message}`, 'danger'); el.querySelectorAll('button, input').forEach(b => { b.disabled = false; }); }
        };
        el.querySelectorAll('[data-answer]').forEach(b => { b.onclick = () => answer(JSON.parse(b.dataset.answer)); });
        el.querySelectorAll('[data-choice]').forEach(b => {
            b.onclick = () => answer(shape === 'choices' ? { value: b.dataset.choice } : { selected: [b.dataset.choice], other_text: '' });
        });
        const other = el.querySelector('[data-other]');
        const sendOther = () => {
            const v = other.value.trim();
            if (shape === 'choice') answer({ selected: [], other_text: v });
            else answer({ value: v });
        };
        el.querySelector('[data-other-send]')?.addEventListener('click', sendOther);
        other?.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); sendOther(); } });
        scroll(true);
    }

    /* ------------------------------------------------------------ wiring */
    Orch.subscribe?.(change => { if (state.open && change.scope === 'run' && state.runs.has(change.id)) paintRun(change.id); });
    document.addEventListener('keydown', e => {
        if (e.key !== '.' || e.metaKey || e.ctrlKey || e.altKey) return;
        const t = e.target;
        if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
        e.preventDefault();
        toggle();
    });
    Orch.chat = {
        open, close, toggle, isOpen: () => state.open,
        ask: async text => { await open({ focus: false }); if (state.session) send(text); },
    };
    /* Reopen after a reload if it was open. */
    try { if (localStorage.getItem(STORE) === '1') setTimeout(() => open({ focus: false }), 0); } catch (_) { /* private mode */ }
})();
