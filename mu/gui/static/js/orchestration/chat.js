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
 * It looks and behaves like the main chat because it is built from the same Wash components
 * (templates/fragments/chat.html): .w-transcript / .w-msg messages, the shared tool-card
 * renderer (wash/toolcard.js renderToolCards), .w-approval cards with .w-prompt__option rows,
 * .w-resolved breadcrumbs, the .w-composer with its send circle and stop square, and the
 * .w-tracenote "Working…" ink drop.
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
    /* Only these tools start or propose a run: their result becomes a run card with your decision. */
    const PROPOSES = new Set(['orch_run_propose', 'orch_tool_test', 'orch_runbook_test']);
    const toolLabel = name => String(name || 'tool').replace(/^orch_/, '').replace(/_/g, ' ');
    const STORE = 'mucli-orch-chat-open';
    const SUGGEST = [
        'Which nodes need attention?',
        'How full are the prod disks?',
        'Any pending security updates?',
        'Why did the last run fail?',
    ];

    const state = {
        open: false, session: '', model: '', busy: false, source: null, loaded: false,
        turnEl: null, turnText: '', runs: new Map(), withContext: true, lastSent: '',
        group: null,          // the stretch of tool calls in progress: {el, events, running}
        groups: [],           // every rendered stretch (re-rendered when a card is expanded)
    };
    let panel, log, input, sendBtn, ctxEl, composer, workingEl;
    let seq = 0;

    function build() {
        panel = document.getElementById('orch-chat');
        if (!panel) return false;
        panel.innerHTML = `
            <header class="orch-chat__head">
                <h2 class="w-group__title" id="orch-chat-title">Fleet chat</h2>
                <span class="w-composer__spacer"></span>
                <button type="button" class="w-btn" data-variant="ghost" data-size="sm" data-chat-new title="Start a new conversation">${icon('plus')}<span>New</span></button>
                <button type="button" class="w-btn" data-variant="icon" data-size="sm" data-chat-close aria-label="Close the fleet chat" title="Close (.)">${icon('x')}</button>
            </header>
            <div class="orch-chat__log w-transcript" data-mode-tint="default" data-chat-log role="log" aria-live="polite" aria-labelledby="orch-chat-title"></div>
            <div class="w-composer orch-chat__composer" data-mode-tint="default" data-chat-composer>
                <form class="w-composer__form" data-chat-form>
                    <div class="w-composer__input">
                        <textarea class="w-composer__textarea" rows="1" placeholder="Ask about your fleet…" aria-label="Message the fleet chat" data-chat-input></textarea>
                    </div>
                    <div class="w-composer__bar">
                        <span data-chat-ctx></span>
                        <span class="w-composer__spacer"></span>
                        <span class="orch-chat__model" data-chat-model></span>
                        <button type="submit" class="w-composer__send" aria-label="Send" data-chat-send>${icon('arrow-up')}</button>
                    </div>
                </form>
                <div class="w-composer__hint"><span>It can propose runs; only you can approve them.</span></div>
            </div>`;
        log = panel.querySelector('[data-chat-log]');
        input = panel.querySelector('[data-chat-input]');
        sendBtn = panel.querySelector('[data-chat-send]');
        ctxEl = panel.querySelector('[data-chat-ctx]');
        composer = panel.querySelector('[data-chat-composer]');
        panel.querySelector('[data-chat-close]').onclick = () => close();
        panel.querySelector('[data-chat-new]').onclick = () => fresh();
        panel.querySelector('[data-chat-form]').onsubmit = e => { e.preventDefault(); state.busy ? stop() : send(); };
        input.addEventListener('keydown', e => {
            if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (!state.busy) send(); }
            if (e.key === 'Escape') { e.preventDefault(); close(); }
        });
        input.addEventListener('input', () => { input.style.height = 'auto'; input.style.height = `${Math.min(input.scrollHeight, 200)}px`; });
        /* The tool-card renderer flips open/closed flags on its source events (toolcard.js);
         * this page has no Alpine to re-run the binding, so re-render the stretch it sits in. */
        log.addEventListener('click', e => {
            const hit = e.target.closest?.('[data-tc-toggle], [data-tc-more]');
            if (!hit) return;
            const group = state.groups.find(g => g.el.contains(hit));
            if (group) setTimeout(() => renderSteps(group), 0);
        });
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

    function reset() {
        log.innerHTML = '';
        workingEl = null;
        state.group = null;
        state.groups = [];
        state.turnEl = null;
    }
    function status(text) {
        reset();
        log.innerHTML = `<p class="w-tracenote" data-state="working"><span class="w-inkdrop" data-size="sm" data-tone="running" aria-hidden="true"></span><span>${esc(text)}</span></p>`;
    }

    async function connect(fresh = false) {
        status(fresh ? 'Starting a new conversation…' : 'Connecting…');
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
            reset();
            log.innerHTML = ui.banner('attention', '◆', `<strong>The fleet chat isn’t available.</strong> ${esc(err.message)}`);
            return;
        }
        await adopt(info);
    }
    function paintNeedsModel(fresh) {
        reset();
        log.innerHTML = `<div class="w-empty" data-tone="accent">
            <div class="w-empty__art" aria-hidden="true">${icon('sparkles')}</div>
            <h3 class="w-empty__title">Choose a model</h3>
            <p class="w-empty__body">No chat session is open to borrow one from.</p>
            <div class="w-empty__actions"><button type="button" class="w-btn" data-variant="secondary" data-chat-model-pick>Choose a model</button></div></div>`;
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
        reset();
        for (const turn of data.turns || []) {
            const role = turn.role === 'user' ? 'user' : 'assistant';
            for (const part of turn.parts || []) {
                if (part.type === 'text' && part.text?.trim()) {
                    endSteps();
                    if (role === 'user') addUser(stripContext(part.text));
                    else addAssistant(part.text);
                } else if (part.type === 'tool_call') addTool(part.tool_name, part.tool_args);
                else if (part.type === 'tool_result') toolResult(String(part.preview || ''), part.tool_name);
            }
        }
        endSteps();
        if (!log.children.length) paintEmpty();
        scroll(true);
    }

    function paintEmpty() {
        reset();
        log.innerHTML = `<div class="w-empty orch-chat__empty" data-tone="accent">
            <div class="w-empty__art" aria-hidden="true">${icon('sparkles')}</div>
            <h3 class="w-empty__title">Ask about your fleet</h3>
            <p class="w-empty__body">Or ask for something to be done. Runs it proposes wait for your approval.</p>
            <div class="orch-chat__suggest">${SUGGEST.map(s => `<button type="button" class="w-chip" data-variant="meta" data-chat-suggest>${esc(s)}</button>`).join('')}</div></div>`;
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
                if (!state.turnEl) { endSteps(); state.turnText = ''; state.turnEl = addAssistant('', { streaming: true }); }
                state.turnText += ev.text || '';
                paintMarkdown(state.turnEl.querySelector('.w-msg__body'), state.turnText);
                scroll();
                break;
            case 'user_message':
                /* Our own message is already shown; one sent elsewhere (another tab) is added. */
                if (stripContext(ev.text || '') !== state.lastSent) addUser(stripContext(ev.text || ''));
                state.lastSent = '';
                break;
            case 'assistant_end':
                finishAssistant();
                break;
            case 'tool_call':
                setBusy(true);
                finishAssistant();
                addTool(ev.tool_name, ev.tool_args);
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
                finishAssistant();
                endSteps();
                setBusy(false);
                refreshRuns();
                break;
            case 'error':
                addNote(ev.text || 'Something went wrong.', 'error');
                setBusy(false);
                break;
            case 'history_refresh':
                if (!state.busy) loadHistory();
                break;
            default:
        }
        working();
    }
    /* ------------------------------------------------------------ sending */
    function stripContext(text) { return String(text).replace(/^\[Looking at [^\]]*\]\n\n/, ''); }
    function contextLine() {
        const c = state.withContext && Orch.context?.();
        return c ? `[Looking at ${c.kind} ${c.label} (${c.id}) in Orchestration]\n\n` : '';
    }
    /* The page's item as context: a meta chip in the composer bar, like the main chat's mode pill. */
    function paintContext() {
        if (!ctxEl) return;
        const c = Orch.context?.();
        if (!c) { ctxEl.innerHTML = ''; return; }
        ctxEl.innerHTML = state.withContext
            ? `<span class="w-chip" data-variant="meta" title="Sent with your message">${icon('eye')}<span>${esc(c.kind)} ${esc(c.label)}</span><button type="button" class="w-chip__remove" data-chat-ctx-off aria-label="Don't include this ${esc(c.kind)}">×</button></span>`
            : `<button type="button" class="w-chip" data-variant="meta" data-chat-ctx-on>${icon('plus')}<span>About this ${esc(c.kind)}</span></button>`;
        ctxEl.querySelector('[data-chat-ctx-off]')?.addEventListener('click', () => { state.withContext = false; paintContext(); });
        ctxEl.querySelector('[data-chat-ctx-on]')?.addEventListener('click', () => { state.withContext = true; paintContext(); });
    }
    async function send(textArg) {
        const text = String(textArg ?? input.value).trim();
        if (!text || !state.session) return;
        if (log.querySelector('.orch-chat__empty')) reset();
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
            addNote(err.status === 409 ? 'It’s still answering. Wait for it, or stop it first.' : `Couldn’t send: ${err.message}`, 'error');
            setBusy(false);
        }
    }
    async function stop() {
        try { await api.post('/api/chat/interrupt', { session_name: state.session }); } catch (_) { /* best effort */ }
        setBusy(false);
    }
    /* Send circle ↔ stop square, exactly as the main composer. */
    function setBusy(on) {
        state.busy = on;
        if (!sendBtn) return;
        composer.toggleAttribute('data-busy', on);
        sendBtn.className = on ? 'w-composer__stop w-btn' : 'w-composer__send';
        if (on) sendBtn.dataset.variant = 'secondary'; else delete sendBtn.dataset.variant;
        sendBtn.setAttribute('aria-label', on ? 'Stop generating' : 'Send');
        sendBtn.innerHTML = on ? '<span class="w-composer__stop-glyph" aria-hidden="true"></span>' : icon('arrow-up');
        working();
    }
    /* "Working…" with the running ink drop (the main chat's empty-trace note), at the end of the
     * log while a turn is in flight and nothing else shows progress (a streaming message has its
     * own mark, a running tool card its own spinner). */
    function working() {
        if (!log) return;
        const runningTool = state.group?.running && state.group.events.some(e => e.kind === 'tool_call' && !e.__done);
        const show = state.busy && !state.turnEl && !runningTool && !log.querySelector('.w-approval');
        if (!show) { workingEl?.remove(); return; }
        if (!workingEl) {
            workingEl = document.createElement('p');
            workingEl.className = 'w-tracenote';
            workingEl.dataset.state = 'working';
            workingEl.setAttribute('role', 'status');
            workingEl.innerHTML = '<span class="w-inkdrop" data-size="sm" data-tone="running" aria-hidden="true"></span><span>Working…</span>';
        }
        if (log.lastElementChild !== workingEl) log.appendChild(workingEl);
        scroll();
    }

    /* ------------------------------------------------------------ messages (.w-msg) */
    function add(html) {
        log.querySelector('.orch-chat__empty')?.remove();
        log.querySelector(':scope > .w-tracenote:not([role])')?.remove();   // a connecting note
        const holder = document.createElement('div');
        holder.innerHTML = html.trim();
        const el = holder.firstElementChild;
        log.insertBefore(el, workingEl && workingEl.parentNode === log ? workingEl : null);
        return el;
    }
    function addUser(text) {
        return add(`<div class="w-msg" data-role="user"><div class="w-msg__body"><p>${esc(text).replace(/\n/g, '<br>')}</p></div></div>`);
    }
    function addAssistant(text, { streaming = false } = {}) {
        const el = add(`<div class="w-msg" data-role="assistant"${streaming ? ' data-streaming' : ''}>
            <div class="w-msg__head" aria-hidden="true"><span class="w-mark w-msg__mark"${streaming ? ' data-state="working"' : ''}></span><span class="w-msg__model">${esc(state.model || 'μcli')}</span></div>
            <div class="w-msg__body w-markdown"></div></div>`);
        paintMarkdown(el.querySelector('.w-msg__body'), text);
        return el;
    }
    function finishAssistant() {
        if (!state.turnEl) return;
        state.turnEl.removeAttribute('data-streaming');
        state.turnEl.querySelector('.w-msg__mark')?.removeAttribute('data-state');
        state.turnEl = null;
    }
    function addNote(text, role) {
        const el = add(`<div class="w-msg" data-role="${role === 'error' ? 'error' : 'info'}"${role === 'error' ? ' role="alert"' : ''}>${role === 'error' ? '<span class="w-msg__glyph" aria-hidden="true">✗</span>' : ''}<div class="w-msg__body">${esc(text)}</div></div>`);
        scroll();
        return el;
    }

    /* ------------------------------------------------------------ tool calls (toolcard.js) */
    function stepsGroup() {
        if (state.group?.el.isConnected) return state.group;
        const group = { el: add('<div class="w-trace orch-chat__trace"></div>'), events: [], running: true };
        state.group = group;
        state.groups.push(group);
        return group;
    }
    function addTool(name, args) {
        const group = stepsGroup();
        group.running = true;
        group.events.push({ kind: 'tool_call', name: String(name || 'tool'), args: args && typeof args === 'object' ? args : (args ? { detail: args } : null),
                            id: `fc-${++seq}`, at: Date.now() });
        renderSteps(group);
        scroll();
    }
    function toolResult(text, name) {
        const group = state.group || stepsGroup();
        const call = [...group.events].reverse().find(e => e.kind === 'tool_call' && !e.__done && (!name || e.name === name));
        if (call) call.__done = true;
        const tool = name || call?.name || '';
        group.events.push({ kind: 'tool_result', name: tool, text: String(text), id: `fr-${++seq}`, at: Date.now() });
        renderSteps(group);
        if (PROPOSES.has(tool)) noteRuns(text);
    }
    function renderSteps(group) {
        if (!window.renderToolCards) {
            group.el.textContent = group.events.filter(e => e.kind === 'tool_call').map(e => toolLabel(e.name)).join(' · ');
            return;
        }
        group.el.innerHTML = window.renderToolCards(group.events, { running: group.running, now: Date.now() });
    }
    /* A message (or the end of the turn) closes the stretch: it folds into one summary line. */
    function endSteps() {
        const group = state.group;
        state.group = null;
        if (!group || !group.el.isConnected) return;
        group.running = false;
        renderSteps(group);
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
            const el = add('<div class="orch-chat__run"><p class="w-tracenote"><span>Loading the run…</span></p></div>');
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
        catch (_) { el.innerHTML = `<p class="w-tracenote"><span>Run ${esc(id)} isn’t available.</span></p>`; return; }
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

    /* ------------------------------------------------------------ prompts (.w-approval) */
    const clock = () => new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    function addPrompt(id, p) {
        if (log.querySelector(`[data-prompt="${CSS.escape(String(id))}"]`)) return;
        const shape = p.shape || 'input';
        const approval = shape === 'tool_approval';
        const opts = (p.options || p.choices || []).map(o => (typeof o === 'string' ? { label: o } : o));
        const multi = !!p.multi_select;
        const title = approval ? `Allow ${toolLabel(p.tool_name || 'this tool')}?`
            : (p.question || p.message || (shape === 'input' ? 'The model is asking' : 'Choose'));
        const target = approval && p.tool_args && typeof p.tool_args === 'object'
            ? (p.tool_args.target || p.tool_args.ref || p.tool_args.node_id || p.tool_args.selector || '') : '';
        let body = '';
        if (approval) {
            body = `<div class="w-approval__tool">
                    <div class="w-approval__target"><span class="w-approval__toolname">${esc(p.tool_name || '(unknown tool)')}</span>${target ? `<span class="w-approval__path">${esc(typeof target === 'string' ? target : JSON.stringify(target))}</span>` : ''}</div>
                    ${p.tool_args ? `<details class="w-approval__args"><summary>Arguments</summary><pre class="diff-pane">${esc(JSON.stringify(p.tool_args, null, 2).slice(0, 6000))}</pre></details>` : ''}
                </div>`;
        } else if (shape === 'choice' || shape === 'choices') {
            body = `<div class="w-prompt__options" role="${multi ? 'group' : 'radiogroup'}" aria-labelledby="fc-pt-${esc(id)}">
                ${opts.map((o, i) => `<label class="w-prompt__option"><input class="w-prompt__input" type="${multi ? 'checkbox' : 'radio'}" name="fc-opt-${esc(id)}" value="${esc(o.label ?? o.value ?? '')}">
                    <span class="w-prompt__key" aria-hidden="true">${i < 9 ? i + 1 : ''}</span><span class="w-prompt__label">${esc(o.label ?? o.value ?? '')}${o.description ? ` <span class="w-approval__path">${esc(o.description)}</span>` : ''}</span><span class="w-prompt__check" aria-hidden="true">✓</span></label>`).join('')}
                ${p.allow_other || shape === 'choice' ? `<label class="w-prompt__option"><input class="w-prompt__input" type="${multi ? 'checkbox' : 'radio'}" name="fc-opt-${esc(id)}" value="__other__">
                    <span class="w-prompt__key" aria-hidden="true">·</span><input type="text" class="w-input w-prompt__other" placeholder="Something else…" aria-label="Other answer" data-other><span class="w-prompt__check" aria-hidden="true">✓</span></label>` : ''}
            </div>`;
        } else if (shape === 'confirm') {
            body = `<div class="w-approval__confirm"><strong>${esc(p.message || '')}</strong></div>`;
        } else {
            body = `<input type="text" class="w-input" aria-label="Your answer" value="${esc(p.default || '')}" data-text>`;
        }
        const cancelLabel = approval ? 'Deny' : (shape === 'confirm' ? 'No' : 'Cancel');
        const okLabel = approval ? 'Allow' : (shape === 'confirm' ? 'Yes' : 'Answer');
        const el = add(`<section class="w-approval" data-current data-tone="attention" data-shape="${esc(shape)}" role="group" aria-labelledby="fc-pt-${esc(id)}" tabindex="-1">
            <header class="w-approval__head">
                <span class="w-chip" data-tone="attention"><span class="w-chip__glyph" aria-hidden="true">${approval ? '◆' : '?'}</span><span>${approval ? 'Needs you' : 'Question'}</span></span>
                <h3 class="w-approval__title" id="fc-pt-${esc(id)}">${esc(title)}</h3>
            </header>
            <div class="w-approval__body">
                ${p.description && shape !== 'confirm' ? `<div class="w-approval__desc">${esc(p.description)}</div>` : ''}
                ${body}
                <footer class="w-approval__foot">
                    <div class="w-approval__meta"></div>
                    <div class="w-btn-row" data-align="end">
                        <button type="button" class="w-btn" data-variant="secondary" data-cancel>${cancelLabel}</button>
                        <button type="button" class="w-btn" data-variant="primary" data-approve>${approval ? '<span aria-hidden="true">✓</span>' : ''}<span>${okLabel}</span><kbd class="w-keycap" aria-hidden="true">⏎</kbd></button>
                    </div>
                </footer>
            </div></section>`);
        el.dataset.prompt = id;
        const selected = () => [...el.querySelectorAll('.w-prompt__input:checked')].map(i => i.value);
        el.querySelectorAll('.w-prompt__input').forEach(i => i.addEventListener('change', () => {
            el.querySelectorAll('.w-prompt__option').forEach(o => o.toggleAttribute('data-selected', !!o.querySelector('.w-prompt__input:checked')));
        }));
        el.querySelector('[data-other]')?.addEventListener('focus', () => {
            const radio = el.querySelector('.w-prompt__input[value="__other__"]');
            if (radio && !radio.checked) { radio.checked = true; radio.dispatchEvent(new Event('change')); }
        });
        const resolve = (verb, subject, denied) => {
            el.replaceWith(Object.assign(document.createElement('div'), {
                className: 'w-resolved',
                innerHTML: `<div class="w-resolved__line"><span class="w-resolved__glyph" aria-hidden="true">${denied ? '✗' : '✓'}</span><span class="w-resolved__verb">${esc(verb)}</span>${subject ? `<span class="w-resolved__subject">${esc(subject)}</span>` : ''}<time class="w-resolved__time">${clock()}</time></div>`,
            }));
            const done = log.lastElementChild;
            if (done) done.dataset.outcome = denied ? 'denied' : 'approved';
            working();
        };
        const answer = async (value, verb, subject, denied = false) => {
            el.querySelectorAll('button, input').forEach(b => { b.disabled = true; });
            try {
                await api.post(`/api/prompts/${encodeURIComponent(id)}/answer`, value);
                resolve(verb, subject, denied);
            } catch (err) {
                ui.toast(`Couldn’t answer: ${err.message}`, 'danger');
                el.querySelectorAll('button, input').forEach(b => { b.disabled = false; });
            }
        };
        const submit = () => {
            if (approval) return answer({ approved: true }, 'Approved', p.tool_name || '');
            if (shape === 'confirm') return answer({ value: true }, 'Answered', 'yes');
            if (shape === 'input') { const v = el.querySelector('[data-text]').value; return answer({ value: v }, 'Answered', v); }
            const picks = selected();
            const other = picks.includes('__other__') ? (el.querySelector('[data-other]')?.value || '').trim() : '';
            const chosen = picks.filter(v => v !== '__other__');
            if (!chosen.length && !other) { ui.toast('Choose an answer first', 'attention'); return null; }
            if (shape === 'choices') return answer({ value: chosen[0] || other }, 'Answered', chosen[0] || other);
            return answer({ selected: chosen, other_text: other }, 'Answered', [...chosen, other].filter(Boolean).join(', '));
        };
        const cancel = () => {
            if (approval) return answer({ approved: false }, 'Denied', p.tool_name || '', true);
            if (shape === 'confirm') return answer({ value: false }, 'Answered', 'no');
            return answer({ cancelled: true }, 'Cancelled', '', true);
        };
        el.querySelector('[data-approve]').onclick = submit;
        el.querySelector('[data-cancel]').onclick = cancel;
        el.addEventListener('keydown', e => {
            if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
            else if (e.key === 'Escape') { e.preventDefault(); cancel(); }
            else if (/^[1-9]$/.test(e.key) && !e.target.matches('input[type=text]')) {
                const pick = el.querySelectorAll('.w-prompt__input')[Number(e.key) - 1];
                if (pick) { pick.checked = multi ? !pick.checked : true; pick.dispatchEvent(new Event('change')); e.preventDefault(); }
            }
        });
        working();
        scroll(true);
        el.focus({ preventScroll: true });
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
