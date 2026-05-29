// μcli GUI — chat-first frontend wired to FastAPI + SSE.
//
// Alpine stores:
//   chat       — turn buffer, history fetch, send
//   sessions   — list/switch/create/delete sessions
//   mode       — agent mode tabs (default/debug/feature/...)
//   prompts    — pending blocking-prompt queue (modal)
//   tokens     — running token meter
//
// Bootstrap order is in DOMContentLoaded at the bottom.

document.addEventListener("alpine:init", () => {
    Alpine.store("chat", {
        // Multi-session: each session has its own slot in `chats`. The
        // `currentName` selector flips when the user clicks a different
        // session in the sidebar. Top-level getters (turns, busy, …)
        // proxy to the focused slot, so existing template bindings like
        // `$store.chat.turns` keep working without per-template changes.
        chats: {},
        currentName: null,
        // Connection status (SSE) is a global concern, not per-session.
        connected: null,
        lastOpenAt: 0,

        // ---------- per-session slot management ----------------------

        _newSlot() {
            return {
                turns: [],
                busy: false,
                externalActive: false,
                clock: 0,
            };
        },
        _slot(name) {
            const key = name || this.currentName || "__default__";
            if (!this.chats[key]) this.chats[key] = this._newSlot();
            return this.chats[key];
        },
        current() { return this._slot(); },
        focus(name) {
            this.currentName = name || null;
            this._slot(name);   // ensure created
            this.scroll();
        },

        // Back-compat top-level getters: legacy templates read e.g.
        // `$store.chat.turns` / `$store.chat.busy`. Route those to the
        // focused slot so we don't have to rewrite every binding.
        get turns()          { return this._slot().turns; },
        set turns(v)         { this._slot().turns = v; },
        get busy()           { return this._slot().busy; },
        set busy(v)          { this._slot().busy = v; },
        get externalActive() { return this._slot().externalActive; },
        set externalActive(v){ this._slot().externalActive = v; },
        get clock()          { return this._slot().clock; },
        set clock(v)         { this._slot().clock = v; },

        // ---------- helpers ------------------------------------------

        _id(prefix) {
            return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
        },
        _lastTurn(slot) {
            const turns = slot.turns;
            return turns.length ? turns[turns.length - 1] : null;
        },
        _currentTrace(slot) {
            const t = this._lastTurn(slot);
            return (t && t.role === "trace") ? t : null;
        },
        _ensureTrace(slot) {
            let t = this._currentTrace(slot);
            if (!t) {
                t = {
                    id: this._id("tr"),
                    role: "trace",
                    events: [],
                    open: false,
                    running: true,
                    startedAt: Date.now(),
                    elapsed: null,
                };
                slot.turns.push(t);
            } else {
                t.running = true;
            }
            return t;
        },
        _closeTrace(slot) {
            const t = this._currentTrace(slot);
            if (t && t.running) {
                t.running = false;
                t.elapsed = ((Date.now() - t.startedAt) / 1000).toFixed(1);
            }
        },
        _findById(slot, id) { return slot.turns.find((t) => t.id === id); },
        _lastByRole(slot, role) {
            for (let i = slot.turns.length - 1; i >= 0; i--) {
                if (slot.turns[i].role === role) return slot.turns[i];
            }
            return null;
        },

        // ---------- user + assistant turns ---------------------------

        addUser(text, name) {
            const slot = this._slot(name);
            this._closeTrace(slot);
            slot.turns.push({
                id: this._id("u"),
                role: "user",
                text,
                html: renderMarkdown(text),
                streaming: false,
            });
            if (!name || name === this.currentName) this.scroll();
        },
        startAssistant(turn_id, name) {
            const slot = this._slot(name);
            this._closeTrace(slot);
            slot.turns.push({
                id: turn_id || this._id("a"),
                role: "assistant",
                text: "",
                html: "",
                streaming: true,
            });
            if (!name || name === this.currentName) this.scroll();
        },
        appendDelta(turn_id, text, name) {
            const slot = this._slot(name);
            let t = this._findById(slot, turn_id);
            if (!t || t.role !== "assistant") {
                t = this._lastByRole(slot, "assistant");
                if (!t || !t.streaming) {
                    this.startAssistant(turn_id, name);
                    t = this._lastByRole(slot, "assistant");
                }
            }
            if (!t) return;
            t.text += text;
            t.html = renderMarkdown(t.text);
            if (!name || name === this.currentName) this.scroll();
        },
        endAssistant(turn_id, name) {
            const slot = this._slot(name);
            const t = this._findById(slot, turn_id) || this._lastByRole(slot, "assistant");
            if (!t) return;
            t.streaming = false;
            t.html = renderMarkdown(t.text);
            if (!name || name === this.currentName) queueMicrotask(highlightAll);
        },

        // ---------- trace events -------------------------------------

        addToolCall(toolName, args, name) {
            const slot = this._slot(name);
            const t = this._ensureTrace(slot);
            t.events.push({
                id: this._id("ev"),
                kind: "tool_call",
                name: toolName || "(unknown)",
                jsonHtml: renderJSON(args),
                at: Date.now(),
            });
            if (!name || name === this.currentName) this.scroll();
        },
        addToolResult(toolName, text, name) {
            const slot = this._slot(name);
            const t = this._ensureTrace(slot);
            t.events.push({
                id: this._id("ev"),
                kind: "tool_result",
                name: toolName || "",
                jsonHtml: renderJSON(text),
                rawText: typeof text === "string" ? text : null,
                at: Date.now(),
            });
            if (!name || name === this.currentName) this.scroll();
        },
        addThinking(text, name) {
            if (!text) return;
            const slot = this._slot(name);
            const t = this._ensureTrace(slot);
            const last = t.events[t.events.length - 1];
            if (last && last.kind === "thinking") {
                last.text += text;
                return;
            }
            t.events.push({
                id: this._id("ev"),
                kind: "thinking",
                text: String(text),
                at: Date.now(),
            });
            if (!name || name === this.currentName) this.scroll();
        },
        addInfo(text, opts, name) {
            const slot = this._slot(name);
            if (opts && opts.standalone) {
                slot.turns.push({
                    id: this._id("i"),
                    role: "info",
                    text,
                    html: escapeHtml(text),
                });
                if (!name || name === this.currentName) this.scroll();
                return;
            }
            const t = this._ensureTrace(slot);
            t.events.push({
                id: this._id("ev"),
                kind: "info",
                text: String(text),
                at: Date.now(),
            });
            if (!name || name === this.currentName) this.scroll();
        },
        addError(text, name) {
            const slot = this._slot(name);
            this._closeTrace(slot);
            slot.turns.push({
                id: this._id("e"),
                role: "error",
                text,
                html: escapeHtml(text),
            });
            if (!name || name === this.currentName) this.scroll();
        },

        addPromptResolved(record, name) {
            const slot = this._slot(name);
            const cancelled = !!(record && record.cancelled);
            const answer = record && record.answer ? String(record.answer) : "";
            const title = record && record.title ? String(record.title) : "";
            slot.turns.push({
                id: this._id("pr"),
                role: "prompt_resolved",
                cancelled,
                shape: (record && record.shape) || "",
                toolName: (record && record.toolName) || "",
                title,
                titleHtml: renderMarkdownInline(title),
                answer,
                answerHtml: renderMarkdownInline(answer),
                at: Date.now(),
            });
            if (!name || name === this.currentName) this.scroll();
        },

        finishTurn(name) {
            const slot = this._slot(name);
            this._closeTrace(slot);
        },

        isBusy(name) {
            return !!(this.chats[name] && this.chats[name].busy);
        },

        // ---------- send + history -----------------------------------

        scroll() {
            queueMicrotask(() => {
                const el = document.querySelector(".chat-history");
                if (el) el.scrollTop = el.scrollHeight;
            });
        },
        async send(text) {
            text = (text || "").trim();
            const name = this.currentName;
            if (!text) return;
            const slot = this._slot(name);
            if (slot.busy) return;
            this.addUser(text, name);
            slot.busy = true;
            try {
                const resp = await fetch("/api/chat/send", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ text, session_name: name }),
                });
                if (resp.status === 409) {
                    this.addError("A turn is already in flight.", name);
                    slot.busy = false;
                } else if (!resp.ok) {
                    const data = await resp.json().catch(() => ({}));
                    this.addError(data.detail || `send failed (${resp.status})`, name);
                    slot.busy = false;
                }
            } catch (err) {
                this.addError(`Network error: ${err}`, name);
                slot.busy = false;
            }
        },
        async loadHistory(name) {
            const target = name || this.currentName;
            try {
                const url = target
                    ? `/api/sessions/current/history?session_name=${encodeURIComponent(target)}`
                    : "/api/sessions/current/history";
                const r = await fetch(url);
                const data = await r.json();
                // At boot, target may be null (currentName is unset until
                // sessions.load() runs). Key the slot by the server's
                // returned name so the history lands where the proxy
                // getters will look once sessions.load syncs currentName.
                const key = target || data.name || null;
                const slot = this._slot(key);
                if (!this.currentName && data.name) this.currentName = data.name;
                slot.turns = [];
                let traceForTurn = null;
                for (const turn of data.turns || []) {
                    for (const part of turn.parts || []) {
                        if (part.type === "text") {
                            traceForTurn = null;
                            slot.turns.push({
                                id: `h-${turn.index}-${slot.turns.length}`,
                                role: turn.role,
                                text: part.text,
                                html: renderMarkdown(part.text),
                                streaming: false,
                            });
                        } else if (part.type === "tool_call") {
                            if (!traceForTurn) {
                                traceForTurn = {
                                    id: this._id("tr"),
                                    role: "trace",
                                    events: [],
                                    open: false,
                                    running: false,
                                    startedAt: Date.now(),
                                    elapsed: null,
                                };
                                slot.turns.push(traceForTurn);
                            }
                            traceForTurn.events.push({
                                id: this._id("ev"),
                                kind: "tool_call",
                                name: part.tool_name || "(unknown)",
                                jsonHtml: renderJSON(part.tool_args),
                                at: 0,
                            });
                        } else if (part.type === "tool_result") {
                            if (!traceForTurn) {
                                traceForTurn = {
                                    id: this._id("tr"),
                                    role: "trace",
                                    events: [],
                                    open: false,
                                    running: false,
                                    startedAt: Date.now(),
                                    elapsed: null,
                                };
                                slot.turns.push(traceForTurn);
                            }
                            traceForTurn.events.push({
                                id: this._id("ev"),
                                kind: "tool_result",
                                name: part.tool_name || "",
                                jsonHtml: renderJSON(part.preview),
                                rawText: typeof part.preview === "string" ? part.preview : null,
                                at: 0,
                            });
                        }
                    }
                }
                if (!name || name === this.currentName) {
                    this.scroll();
                    queueMicrotask(highlightAll);
                }
            } catch (err) {
                console.error("history", err);
            }
        },
    });

    Alpine.store("sessions", {
        list: [],
        current: null,
        loaded: [],     // names currently resident in daemon memory
        busy: [],       // names with a turn in flight (server-side)
        async load() {
            const r = await fetch("/api/sessions");
            const data = await r.json();
            this.list = data.sessions || [];
            this.current = data.current;
            this.loaded = data.loaded || [];
            this.busy = data.busy || [];
            // First load — sync the chat store's focus pointer.
            const chat = Alpine.store("chat");
            if (!chat.currentName && this.current) chat.focus(this.current);
        },
        isLoaded(name) { return (this.loaded || []).includes(name); },
        isBusy(name) {
            // Server-reported busy OR locally-tracked busy. The local
            // slot flag flips on /send before the server has echoed an
            // event back, so the sidebar dot lights up immediately.
            return (this.busy || []).includes(name) ||
                   Alpine.store("chat").isBusy(name);
        },
        async switchTo(name) {
            // In-memory? Just refocus locally and ask the server to flip
            // its focus pointer. No page reload, so the previously-active
            // session keeps streaming its turn into its own slot.
            if (this.isLoaded(name)) {
                try {
                    await fetch(`/api/sessions/${encodeURIComponent(name)}/focus`, {
                        method: "POST",
                    });
                } catch (e) { /* non-fatal; UI still flips */ }
                this.current = name;
                Alpine.store("chat").focus(name);
                // Ensure history is populated (it may be a session the
                // browser has never rendered before, only resident in
                // the daemon's cache).
                Alpine.store("chat").loadHistory(name);
                // mode.load() refreshes the active mode's panel store
                // via panelModes — no explicit teacher/feature call needed.
                Alpine.store("mode").load();
                return;
            }
            // Not loaded yet — POST /load (which is idempotent).
            const r = await fetch(`/api/sessions/${encodeURIComponent(name)}/load`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({}),
            });
            if (!r.ok) {
                const d = await r.json().catch(() => ({}));
                alert(d.detail || `Load failed (${r.status})`);
                return;
            }
            await this.load();
            this.current = name;
            Alpine.store("chat").focus(name);
            await Alpine.store("chat").loadHistory(name);
            await Alpine.store("mode").load();
        },
        async remove(name) {
            await fetch(`/api/sessions/${encodeURIComponent(name)}`, { method: "DELETE" });
            await this.load();
        },
        async unload(name) {
            await fetch(`/api/sessions/${encodeURIComponent(name)}/unload`, {
                method: "POST",
            });
            await this.load();
        },
        async deactivate() {
            await fetch("/api/sessions/active", { method: "DELETE" });
            location.reload();
        },
    });

    Alpine.store("mode", {
        active: "default",
        modes: [],
        // Modes that own a dedicated side-panel template. The layout
        // toggles its `.has-panel` shell class against this list so the
        // chat column reserves room only when something will fill it.
        panelModes: ["teacher", "feature", "research", "security", "loop", "debug"],
        async load() {
            const r = await fetch("/api/modes");
            const data = await r.json();
            this.modes = data.modes || [];
            this.active = data.current || "default";
            // Mode-specific side-effect: refresh the panel store for
            // the active mode so the layout has data the instant the
            // user lands here (no extra click).
            const store = this.panelModes.includes(this.active)
                ? Alpine.store(this.active)
                : null;
            if (store && typeof store.load === "function") store.load();
        },
        async set(name) {
            const r = await fetch(`/api/modes/${name}`, { method: "POST" });
            if (r.ok) {
                this.active = name;
            } else {
                const d = await r.json().catch(() => ({}));
                Alpine.store("chat").addInfo(d.detail || `mode switch failed (${r.status})`);
            }
            await this.load();
        },
    });

    Alpine.store("prompts", {
        queue: [],
        // `active` returns the head prompt FOR THE FOCUSED SESSION.
        // Background-session prompts queue up but don't render inline
        // until the user switches to that session. A getter (not a
        // method) so Alpine's proxy tracks dependencies.
        get active() {
            const cur = Alpine.store("chat").currentName;
            for (const item of this.queue) {
                const name = item && item.session_name;
                if (!name || !cur || name === cur) return item;
            }
            return null;
        },
        // List of prompts for the currently-focused session (cards render
        // inline; queue beyond the head shows as "N more pending" badge).
        get visibleQueue() {
            const cur = Alpine.store("chat").currentName;
            return this.queue.filter(item => {
                const name = item && item.session_name;
                return !name || !cur || name === cur;
            });
        },
        push(payload) { this.queue.push(payload); },
        // Remove a specific id from the queue (used after answer/cancel
        // since the head isn't always the right one with cross-session
        // prompts in flight).
        _remove(id) {
            const i = this.queue.findIndex(x => x.id === id);
            if (i >= 0) this.queue.splice(i, 1);
        },
        async answer(id, value) {
            if (!id) return;
            try {
                const r = await fetch(`/api/prompts/${id}/answer`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(value),
                });
                if (!r.ok) {
                    Alpine.store("chat").addError(
                        `prompt answer failed (${r.status})`
                    );
                }
            } finally {
                this._remove(id);
            }
        },
        async cancel(id) {
            if (id) {
                try { await fetch(`/api/prompts/${id}/cancel`, { method: "POST" }); }
                catch (e) {}
                this._remove(id);
            } else {
                // Edge case (no id): drop the head item to unstick the UI.
                this.queue.shift();
            }
        },
    });

    Alpine.store("tokens", { input: 0, output: 0, total: 0, total_cost: 0 });

    Alpine.store("yolo", {
        active: false,
        async load() {
            try {
                const r = await fetch("/api/variables");
                const d = await r.json();
                for (const g of (d.groups || [])) {
                    for (const v of (g.variables || [])) {
                        if (v.key === "yolo") { this.active = !!v.value; return; }
                    }
                }
            } catch (e) { console.error("yolo.load", e); }
        },
        async toggle() {
            const next = !this.active;
            try {
                const r = await fetch("/api/variables/yolo", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ value: next }),
                });
                if (r.ok) this.active = next;
            } catch (e) { console.error("yolo.toggle", e); }
        },
    });

    Alpine.store("theme", {
        // Initialised from the data-theme attribute the pre-paint
        // script in base.html set. Toggling it updates the DOM,
        // localStorage, and (via subscribers) the highlight.js link.
        current: document.documentElement.getAttribute("data-theme") || "dark",
        toggle() {
            this.current = this.current === "dark" ? "light" : "dark";
            try { localStorage.setItem("mucli-theme", this.current); } catch (e) {}
            applyTheme(this.current);
            // Re-highlight existing code blocks after the stylesheet swap.
            queueMicrotask(rehighlightAll);
        },
    });

    Alpine.store("teacher", {
        // Active course payload from /api/teacher/state. Loaded on demand
        // when the user is in teacher mode AND a session is active.
        course: null,
        courses: [],
        active: false,
        loaded: false,
        coursePath: null,    // debugging breadcrumb: where the data was read
        openSections: {
            profile: true,
            curriculum: true,
            current: true,
            assignments: true,
            reviews: false,
        },

        async load() {
            try {
                const r = await fetch("/api/teacher/state");
                const d = await r.json();
                this.active = !!d.active;
                this.course = d.course || null;
                this.courses = d.courses || [];
                this.coursePath = d.course_path || null;
                this.loaded = true;
                if (window.__mucliTeacherDebug) {
                    console.log("teacher.load", {
                        active_course_id: d.active_course_id,
                        raw_teacher_state_present: d.raw_teacher_state_present,
                        registry_size: d.registry_size,
                        course_path: d.course_path,
                        course: d.course,
                        courses: d.courses,
                    });
                }
            } catch (e) {
                console.error("teacher.load", e);
            }
        },
        toggleSection(name) {
            this.openSections[name] = !this.openSections[name];
        },

        // ---- view helpers ----
        statusGlyph(status) {
            switch ((status || "").toLowerCase()) {
                case "completed":     return "✓";
                case "graded":        return "✓";
                case "lecturing":
                case "presenting":
                case "assigned":      return "◐";
                case "remediating":   return "⚠";
                case "pending":       return "○";
                default:              return "·";
            }
        },
        // Diagnostic: flip this in the browser console to log every
        // teacher state refresh: `window.__mucliTeacherDebug = true`.
        currentLesson() {
            if (!this.course || !this.course.current_lesson_id) return null;
            return (this.course.lessons || []).find(
                l => l.lesson_id === this.course.current_lesson_id
            ) || null;
        },
        currentModule() {
            if (!this.course || !this.course.current_module_id) return null;
            return (this.course.modules || []).find(
                m => m.module_id === this.course.current_module_id
            ) || null;
        },
        gradedAssignments() {
            if (!this.course) return [];
            return (this.course.assignments || []).filter(a => a.grade);
        },
        allAssignments() {
            if (!this.course) return [];
            return (this.course.assignments || []);
        },
        scheduledReviews() {
            if (!this.course) return [];
            return (this.course.scheduled_reviews || []);
        },
        // Map learner_profile keys → which fields are array-of-tags
        // versus solo-text. Surfacing both shapes in one helper keeps
        // the template lean.
        profileTagFields() {
            return ["strengths", "gaps", "goals", "modality",
                    "background", "anchors", "stumbling_blocks"];
        },
        profileTextFields() {
            return ["pace", "jargon_tolerance", "motivation",
                    "personality", "notes"];
        },
        // Per-module completion: how many lessons inside this module
        // have status==='completed'? Used for the curriculum progress bar.
        moduleProgress(m) {
            if (!m || !m.lessons || !m.lessons.length) {
                return { done: 0, total: 0, pct: 0 };
            }
            const done = m.lessons.filter(l => (l.status || "") === "completed").length;
            const total = m.lessons.length;
            return { done, total, pct: total ? Math.round((done * 100) / total) : 0 };
        },
        // ISO-ish timestamp from a unix float. Used for learner-profile
        // recorded_at + course updated_at.
        formatTimestamp(unix) {
            if (!unix || typeof unix !== "number") return "";
            try {
                return new Date(unix * 1000).toLocaleString();
            } catch (e) {
                return "";
            }
        },
        async switchCourse(courseId) {
            // Sends a /teach load <id> via chat so the agent's command
            // dispatch handles it (keeping registry/state in sync).
            if (!courseId) return;
            await Alpine.store("chat").send(`/teach load ${courseId}`);
        },
    });

    Alpine.store("feature", {
        // Active feature plan summary from /api/feature/state. Same load
        // triggers as the teacher store: page boot, mode flip, turn_complete
        // SSE, session_updated SSE.
        plan: null,
        features: [],
        active: false,
        loaded: false,
        metadataPath: null,
        openSections: {
            events: false,
            reviews: false,
        },
        // Per-phase collapse state. Defaults open for the in-progress
        // phase, closed otherwise; the load() hook sets initial values.
        openPhases: {},
        // The task whose drill-down is currently expanded (one at a time).
        expandedTaskId: null,
        // Drag-and-drop transfer state.
        dragTaskId: null,

        async load() {
            try {
                const r = await fetch("/api/feature/state");
                const d = await r.json();
                this.active = !!d.active;
                this.plan = d.plan || null;
                this.features = d.features || [];
                this.metadataPath = d.metadata_path || null;
                // Seed open/closed defaults for new phases without
                // disturbing whatever the user has already toggled.
                const phases = (this.plan && this.plan.phase_columns) || [];
                for (const phase of phases) {
                    const key = String(phase.id);
                    if (this.openPhases[key] === undefined) {
                        const status = (phase.status || "").toLowerCase();
                        // Open whichever phase is currently in flight;
                        // leave done/pending phases closed.
                        this.openPhases[key] = status === "in_progress"
                            || status === "blocked";
                    }
                }
                this.loaded = true;
            } catch (e) {
                console.error("feature.load", e);
            }
        },
        toggleSection(name) {
            this.openSections[name] = !this.openSections[name];
        },
        togglePhase(id) {
            const key = String(id);
            this.openPhases[key] = !this.openPhases[key];
        },
        isPhaseOpen(id) {
            return !!this.openPhases[String(id)];
        },
        toggleTask(id) {
            this.expandedTaskId = this.expandedTaskId === id ? null : id;
        },
        isTaskExpanded(id) {
            return this.expandedTaskId === id;
        },

        // ---- view helpers ----
        statusGlyph(status) {
            switch ((status || "").toLowerCase()) {
                case "completed":   return "✓";
                case "in_progress": return "◐";
                case "blocked":     return "⚠";
                case "archived":    return "✕";
                case "pending":
                case "not_started": return "○";
                default:            return "·";
            }
        },
        formatTimestamp(unix) {
            if (!unix || typeof unix !== "number") return "";
            try { return new Date(unix * 1000).toLocaleString(); }
            catch (e) { return ""; }
        },
        // The agent's "current" task is the in_progress one (if any), else
        // the next actionable. summarize_feature_plan ships this as
        // `execution.next_task`; surface both via one accessor.
        currentTask() {
            if (!this.plan) return null;
            const exec = this.plan.execution || {};
            if (exec.next_task) return exec.next_task;
            const active = this.plan.active_tasks || [];
            return active.length ? active[0] : null;
        },
        phaseColumns() {
            return (this.plan && this.plan.phase_columns) || [];
        },
        progressPct() {
            if (!this.plan || !this.plan.task_count) return 0;
            return Math.round(
                ((this.plan.tasks_completed_count || 0) /
                    (this.plan.task_count || 1)) * 100
            );
        },
        // The backend's summary uses `tasks_completed` as a bool; derive
        // the count from the task list so the progress bar makes sense.
        tasksCompletedCount() {
            if (!this.plan) return 0;
            return (this.plan.phases || []).filter(
                t => (t.status || "").toLowerCase() === "completed"
            ).length;
        },
        recentEvents(limit = 5) {
            if (!this.plan || !this.plan.event_log) return [];
            return this.plan.event_log.slice(-limit).reverse();
        },
        reviews() {
            if (!this.plan) return [];
            return this.plan.review_records || [];
        },
        // ---- mutating actions ----
        async transitionTask(taskId, toStatus) {
            if (!taskId || !toStatus) return;
            try {
                const r = await fetch(
                    `/api/feature/tasks/${taskId}/transition`,
                    {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ to_status: toStatus }),
                    }
                );
                if (!r.ok) {
                    const data = await r.json().catch(() => ({}));
                    Alpine.store("chat").addInfo(
                        `feature: cannot move task #${taskId} to ${toStatus} — ${data.detail || r.status}`
                    );
                }
            } catch (e) {
                console.error("feature.transitionTask", e);
            } finally {
                await this.load();
            }
        },
        async toggleExitCriterion(taskId, idx) {
            if (!taskId && taskId !== 0) return;
            try {
                const r = await fetch(
                    `/api/feature/tasks/${taskId}/exit-criteria/${idx}/toggle`,
                    { method: "POST" }
                );
                if (!r.ok) {
                    const data = await r.json().catch(() => ({}));
                    console.warn("toggleExitCriterion", r.status, data.detail);
                }
            } catch (e) {
                console.error("feature.toggleExitCriterion", e);
            } finally {
                await this.load();
            }
        },
        isVerified(task, criterion) {
            return (task.verified_exit_criteria || []).includes(criterion);
        },
        async switchFeature(featureId) {
            if (!featureId) return;
            await Alpine.store("chat").send(`/feature load ${featureId}`);
        },
    });

    Alpine.store("research", {
        sources: [],
        sourceCount: 0,
        bibliography: "",
        findings: [],
        findingCount: 0,
        active: false,
        loaded: false,
        openSections: {
            sources: true,
            bibliography: false,
            findings: true,
        },
        // Client-side filter: selected source types (empty = show all).
        typeFilter: [],
        // Client-side credibility threshold slider (0 = no filter).
        credibilityMin: 0,
        // Which source row is expanded for detail.
        expandedSourceId: null,

        async load() {
            try {
                const r = await fetch("/api/research/state");
                const d = await r.json();
                this.active = !!d.active;
                this.sources = d.sources || [];
                this.sourceCount = d.source_count || 0;
                this.bibliography = d.bibliography || "";
                this.findings = d.findings || [];
                this.findingCount = d.finding_count || 0;
                this.loaded = true;
            } catch (e) {
                console.error("research.load", e);
            }
        },
        toggleSection(name) {
            this.openSections[name] = !this.openSections[name];
        },
        toggleType(type) {
            const i = this.typeFilter.indexOf(type);
            if (i >= 0) this.typeFilter.splice(i, 1);
            else this.typeFilter.push(type);
        },
        isTypeActive(type) {
            return this.typeFilter.length === 0 || this.typeFilter.includes(type);
        },
        toggleSource(id) {
            this.expandedSourceId = this.expandedSourceId === id ? null : id;
        },
        isSourceExpanded(id) {
            return this.expandedSourceId === id;
        },
        filteredSources() {
            return this.sources.filter(s => {
                if (this.typeFilter.length && !this.typeFilter.includes(s.source_type)) return false;
                if (this.credibilityMin > 0 && (s.credibility_score || 0) < this.credibilityMin) return false;
                return true;
            });
        },
        sourceTypes() {
            const types = new Set(this.sources.map(s => s.source_type));
            return [...types].sort();
        },
        credibilityPct(score) {
            return Math.round((score || 0) * 100);
        },
        formatTimestamp(unix) {
            if (!unix || typeof unix !== "number") return "";
            try { return new Date(unix * 1000).toLocaleString(); }
            catch (e) { return ""; }
        },
        async copyBibliography() {
            try {
                await navigator.clipboard.writeText(this.bibliography);
            } catch (e) {
                console.warn("clipboard write failed", e);
            }
        },
    });

    Alpine.store("security", {
        report: null,
        findings: [],
        summary: null,
        active: false,
        loaded: false,
        openSections: {
            findings: true,
            stats: false,
        },
        severityFilter: [],
        expandedFindingId: null,

        async load() {
            try {
                const r = await fetch("/api/security/state");
                const d = await r.json();
                this.active = !!d.active;
                this.report = d.report || null;
                this.findings = d.findings || [];
                this.summary = d.summary || null;
                this.loaded = true;
            } catch (e) {
                console.error("security.load", e);
            }
        },
        toggleSection(name) {
            this.openSections[name] = !this.openSections[name];
        },
        toggleSeverity(sev) {
            const i = this.severityFilter.indexOf(sev);
            if (i >= 0) this.severityFilter.splice(i, 1);
            else this.severityFilter.push(sev);
        },
        isSeverityActive(sev) {
            return this.severityFilter.length === 0 || this.severityFilter.includes(sev);
        },
        toggleFinding(id) {
            this.expandedFindingId = this.expandedFindingId === id ? null : id;
        },
        isFindingExpanded(id) {
            return this.expandedFindingId === id;
        },
        filteredFindings() {
            if (!this.severityFilter.length) return this.findings;
            return this.findings.filter(f => this.severityFilter.includes(f.severity));
        },
        severities() {
            const s = new Set(this.findings.map(f => f.severity));
            const order = ["critical", "high", "medium", "low", "info"];
            return order.filter(sev => s.has(sev));
        },
        statusGlyph(status) {
            switch ((status || "").toLowerCase()) {
                case "approved":              return "✓";
                case "exploit_verified":
                case "remediation_verified":  return "◐";
                case "refuted":               return "✕";
                case "proof_attached":
                case "remediation_attached":  return "◔";
                case "new":                   return "○";
                default:                      return "·";
            }
        },
        severityColor(sev) {
            switch ((sev || "").toLowerCase()) {
                case "critical": return "var(--err)";
                case "high":     return "#e0af68";
                case "medium":   return "#ff9e64";
                case "low":      return "#7aa2f7";
                case "info":     return "var(--text-dimmer)";
                default:         return "var(--text-dim)";
            }
        },
        formatTimestamp(unix) {
            if (!unix || typeof unix !== "number") return "";
            try { return new Date(unix * 1000).toLocaleString(); }
            catch (e) { return ""; }
        },
        async approveFinding(findingId) {
            try {
                const r = await fetch(
                    `/api/security/findings/${findingId}/approve`,
                    { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }
                );
                if (!r.ok) {
                    const d = await r.json().catch(() => ({}));
                    Alpine.store("chat").addInfo(`security: cannot approve ${findingId} — ${d.detail || r.status}`);
                }
            } catch (e) {
                console.error("security.approve", e);
            } finally {
                await this.load();
            }
        },
        async refuteFinding(findingId, reason) {
            try {
                const r = await fetch(
                    `/api/security/findings/${findingId}/refute`,
                    {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ reason: reason || "" }),
                    }
                );
                if (!r.ok) {
                    const d = await r.json().catch(() => ({}));
                    Alpine.store("chat").addInfo(`security: cannot refute ${findingId} — ${d.detail || r.status}`);
                }
            } catch (e) {
                console.error("security.refute", e);
            } finally {
                await this.load();
            }
        },
    });

    Alpine.store("loop", {
        loopGoal: "",
        loopActive: false,
        loopFeatures: [],
        backlog: [],
        memory: [],
        active: false,
        loaded: false,
        openSections: { backlog: true, features: false, memory: false },

        async load() {
            try {
                const r = await fetch("/api/loop/state");
                const d = await r.json();
                this.active = !!d.active;
                this.loopGoal = d.loop_goal || "";
                this.loopActive = !!d.loop_active;
                this.loopFeatures = d.loop_features || [];
                this.backlog = d.backlog || [];
                this.memory = d.memory || [];
                this.loaded = true;
            } catch (e) { console.error("loop.load", e); }
        },
        toggleSection(name) { this.openSections[name] = !this.openSections[name]; },
        statusGlyph(status) {
            switch ((status || "").toLowerCase()) {
                case "completed": return "✓";
                case "in_progress": return "◐";
                case "blocked": return "⚠";
                default: return "○";
            }
        },
        formatTimestamp(unix) {
            if (!unix || typeof unix !== "number") return "";
            try { return new Date(unix * 1000).toLocaleString(); }
            catch (e) { return ""; }
        },
    });

    Alpine.store("debug", {
        debugTarget: "",
        hypotheses: [],
        suspects: [],
        findings: [],
        scratchpadCount: 0,
        active: false,
        loaded: false,
        openSections: { hypotheses: true, suspects: true, findings: false },
        expandedHypothesisId: null,

        async load() {
            try {
                const r = await fetch("/api/debug/state");
                const d = await r.json();
                this.active = !!d.active;
                this.debugTarget = d.debug_target || "";
                this.hypotheses = d.hypotheses || [];
                this.suspects = d.suspects || [];
                this.findings = d.findings || [];
                this.scratchpadCount = d.scratchpad_count || 0;
                this.loaded = true;
            } catch (e) { console.error("debug.load", e); }
        },
        toggleSection(name) { this.openSections[name] = !this.openSections[name]; },
        toggleHypothesis(id) {
            this.expandedHypothesisId = this.expandedHypothesisId === id ? null : id;
        },
        isHypothesisExpanded(id) {
            return this.expandedHypothesisId === id;
        },
        statusGlyph(status) {
            switch ((status || "").toLowerCase()) {
                case "confirmed": return "✓";
                case "supported": return "◐";
                case "disproved": return "✕";
                case "untested":  return "○";
                default:          return "·";
            }
        },
        formatTimestamp(unix) {
            if (!unix || typeof unix !== "number") return "";
            try { return new Date(unix * 1000).toLocaleString(); }
            catch (e) { return ""; }
        },
    });

    Alpine.store("confirm", {
        // Small inline confirm-popover anchored near the click coords.
        // Replaces window.confirm() so we don't pop a native modal that
        // blocks the page; just a quiet "are you sure" bubble next to
        // the action button.
        open: false,
        x: 0,
        y: 0,
        message: "",
        danger: false,
        _onConfirm: null,

        ask(message, event, onConfirm, opts) {
            this.message = String(message || "");
            this.danger = !!(opts && opts.danger);
            this._onConfirm = typeof onConfirm === "function" ? onConfirm : null;
            // Anchor to the click coords; clamp inside the viewport so
            // edge-of-screen clicks don't push the popover off-screen.
            const w = window.innerWidth, h = window.innerHeight;
            const px = Math.min((event && event.clientX) || 0, w - 240);
            const py = Math.min((event && event.clientY) || 0, h - 100);
            this.x = Math.max(8, px);
            this.y = Math.max(8, py);
            this.open = true;
        },
        confirm() {
            const cb = this._onConfirm;
            this._onConfirm = null;
            this.open = false;
            if (cb) {
                try { cb(); } catch (e) { console.error("confirm cb", e); }
            }
        },
        cancel() {
            this._onConfirm = null;
            this.open = false;
        },
    });

    Alpine.store("fileBrowser", {
        open: false,
        mode: "folder",       // "folder" or "file"
        path: "",
        parent: "",
        entries: [],
        loading: false,
        _onSelect: null,

        async show(mode, onSelect, startPath) {
            this.mode = mode || "folder";
            this._onSelect = typeof onSelect === "function" ? onSelect : null;
            this.open = true;
            await this.navigate(startPath || "~");
        },
        async navigate(path) {
            this.loading = true;
            try {
                const r = await fetch(`/api/browse?path=${encodeURIComponent(path)}`);
                if (!r.ok) {
                    const d = await r.json().catch(() => ({}));
                    console.warn("browse failed:", d.detail || r.status);
                    return;
                }
                const d = await r.json();
                this.path = d.path;
                this.parent = d.parent;
                this.entries = d.entries || [];
            } catch (e) {
                console.error("fileBrowser.navigate", e);
            } finally {
                this.loading = false;
            }
        },
        up() {
            if (this.parent && this.parent !== this.path) this.navigate(this.parent);
        },
        select(entry) {
            if (entry.is_dir && this.mode === "file") {
                this.navigate(entry.path);
                return;
            }
            if (entry.is_dir && this.mode === "folder") {
                this.navigate(entry.path);
                return;
            }
            // File selected in file mode
            if (!entry.is_dir && this.mode === "file") {
                const cb = this._onSelect;
                this._onSelect = null;
                this.open = false;
                if (cb) cb(entry.path);
            }
        },
        selectCurrent() {
            if (this.mode !== "folder") return;
            const cb = this._onSelect;
            this._onSelect = null;
            this.open = false;
            if (cb) cb(this.path);
        },
        cancel() {
            this._onSelect = null;
            this.open = false;
        },
        formatSize(bytes) {
            if (bytes === null || bytes === undefined) return "";
            if (bytes < 1024) return bytes + " B";
            if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " K";
            return (bytes / 1048576).toFixed(1) + " M";
        },

        // --- create folder ---
        creatingFolder: false,
        newFolderName: "",

        startCreateFolder() {
            this.creatingFolder = true;
            this.newFolderName = "";
        },
        async confirmCreateFolder() {
            const name = (this.newFolderName || "").trim();
            if (!name) { this.cancelCreateFolder(); return; }
            try {
                const r = await fetch("/api/browse/mkdir", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ path: this.path, name }),
                });
                if (!r.ok) {
                    const d = await r.json().catch(() => ({}));
                    alert(d.detail || `create folder failed (${r.status})`);
                    return;
                }
                this.creatingFolder = false;
                this.newFolderName = "";
                await this.navigate(this.path);
            } catch (e) {
                console.error("fileBrowser.confirmCreateFolder", e);
            }
        },
        cancelCreateFolder() {
            this.creatingFolder = false;
            this.newFolderName = "";
        },
    });

    Alpine.store("inspector", {
        open: false,
        tab: "workspace",   // workspace | memory | stats | settings
        // ----- workspace
        workspace: { folders: [], cwd: "", newPath: "" },
        async loadWorkspace() {
            const r = await fetch("/api/workspace");
            const d = await r.json();
            this.workspace.folders = d.folders || [];
            this.workspace.cwd = d.cwd || "";
        },
        async addWorkspace() {
            const path = (this.workspace.newPath || "").trim();
            if (!path) return;
            const r = await fetch("/api/workspace", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ path }),
            });
            if (!r.ok) {
                const d = await r.json().catch(() => ({}));
                alert(d.detail || `add failed (${r.status})`);
                return;
            }
            this.workspace.newPath = "";
            await this.loadWorkspace();
        },
        async removeWorkspace(p) {
            await fetch(`/api/workspace?path=${encodeURIComponent(p)}`, { method: "DELETE" });
            await this.loadWorkspace();
        },

        // ----- memory
        memory: { taskMemory: [], scratchpad: [], query: "", results: null },
        async loadMemory() {
            const r = await fetch("/api/memory");
            const d = await r.json();
            this.memory.taskMemory = d.task_memory || [];
            this.memory.scratchpad = d.scratchpad || [];
            this.memory.results = null;
        },
        async searchMemory() {
            const q = (this.memory.query || "").trim();
            if (!q) { this.memory.results = null; return; }
            const r = await fetch(`/api/memory/search?q=${encodeURIComponent(q)}`);
            const d = await r.json();
            this.memory.results = d.results || [];
        },
        async deleteMemory(id) {
            await fetch(`/api/memory/${id}`, { method: "DELETE" });
            await this.loadMemory();
            if (this.memory.query) await this.searchMemory();
        },
        async clearMemory() {
            await fetch("/api/memory/clear", { method: "POST" });
            await this.loadMemory();
        },

        // ----- stats
        stats: null,
        async loadStats() {
            const r = await fetch("/api/stats");
            this.stats = await r.json();
        },

        // ----- variables (settings)
        // The server returns {groups:[{name, variables:[...]}]} so the
        // UI can render each category as a collapsible submenu.
        variables: { groups: [] },
        varFilter: "",
        // group name → bool. Collapsed by default; user toggles per group.
        openGroups: {},

        async loadVariables() {
            const r = await fetch("/api/variables");
            const d = await r.json();
            this.variables.groups = d.groups || [];
            // Seed any new groups as collapsed; preserve the user's
            // existing per-group toggle state across refreshes.
            for (const g of this.variables.groups) {
                if (!(g.name in this.openGroups)) this.openGroups[g.name] = false;
            }
        },
        toggleGroup(name) {
            this.openGroups[name] = !this.openGroups[name];
        },
        isGroupOpen(name) {
            // While the filter is active, auto-expand any group that has
            // matching entries so the user can actually see the results.
            const q = (this.varFilter || "").trim().toLowerCase();
            if (q) {
                const group = this.variables.groups.find(g => g.name === name);
                if (!group) return false;
                return group.variables.some(v => v.key.toLowerCase().includes(q));
            }
            return !!this.openGroups[name];
        },
        async setVariable(key, value) {
            const r = await fetch(`/api/variables/${encodeURIComponent(key)}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ value }),
            });
            if (!r.ok) {
                const d = await r.json().catch(() => ({}));
                alert(d.detail || `set failed (${r.status})`);
            }
            await this.loadVariables();
        },
        async resetVariable(key) {
            await fetch(`/api/variables/${encodeURIComponent(key)}`, { method: "DELETE" });
            await this.loadVariables();
        },

        // ----- shell
        async openDrawer() {
            this.open = true;
            await this.refreshActiveTab();
        },
        close() { this.open = false; },
        async setTab(name) {
            this.tab = name;
            await this.refreshActiveTab();
        },
        async refreshActiveTab() {
            switch (this.tab) {
                case "workspace": await this.loadWorkspace(); break;
                case "memory":    await this.loadMemory(); break;
                case "stats":     await this.loadStats(); break;
                case "settings":  await this.loadVariables(); break;
            }
        },
        filteredVariables() {
            // Returns the same {groups: [...]} shape the template
            // expects. When a filter is active, each group's variables
            // are narrowed to matching keys, and empty groups drop out.
            const q = (this.varFilter || "").trim().toLowerCase();
            if (!q) return this.variables.groups;
            return this.variables.groups
                .map(g => ({
                    name: g.name,
                    variables: g.variables.filter(v => v.key.toLowerCase().includes(q)),
                }))
                .filter(g => g.variables.length > 0);
        },
    });
});

function escapeHtml(s) {
    return String(s || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

function renderMarkdown(text) {
    if (typeof marked === "undefined") return escapeHtml(text);
    try {
        return marked.parse(text, { breaks: true, gfm: true });
    } catch {
        return escapeHtml(text);
    }
}

// Alias for clarity inside the modal — same rendering, just named so
// the call site reads intent ("block markdown for the title/desc").
function renderMarkdownBlock(text) { return renderMarkdown(text); }

// Inline-only render: no <p> wrapper, no block elements. Used for
// option labels where backtick `code` should render but we don't want
// paragraph layout.
function renderMarkdownInline(text) {
    if (typeof marked === "undefined") return escapeHtml(text);
    try {
        return marked.parseInline(String(text || ""), { gfm: true });
    } catch {
        return escapeHtml(text);
    }
}

function highlightAll() {
    if (typeof hljs === "undefined") return;
    document.querySelectorAll("pre code").forEach((el) => {
        if (!el.dataset.highlighted) {
            try {
                hljs.highlightElement(el);
                el.dataset.highlighted = "1";
            } catch {}
        }
    });
}

function highlightInScope(selector) {
    if (typeof hljs === "undefined") return;
    document.querySelectorAll(`${selector} pre code`).forEach((el) => {
        if (!el.dataset.highlighted) {
            try {
                hljs.highlightElement(el);
                el.dataset.highlighted = "1";
            } catch {}
        }
    });
}

function bootSSE() {
    let hasConnectedBefore = false;
    const source = new EventSource("/api/events");
    source.onopen = () => {
        const chat = Alpine.store("chat");
        chat.connected = true;
        chat.lastOpenAt = Date.now();
        // On reconnect (not initial boot), re-sync state in case we
        // missed events while the connection was down. This handles the
        // common case of session switch causing a brief SSE drop.
        if (hasConnectedBefore) {
            chat.loadHistory();
            Alpine.store("sessions").load();
            refreshActivePanel();
        }
        hasConnectedBefore = true;
    };
    source.onmessage = (ev) => {
        let data;
        try { data = JSON.parse(ev.data); } catch { return; }
        if (data && data.kind === "hello") return;
        routeEvent(data);
    };
    source.addEventListener("ping", () => {});
    source.onerror = () => {
        const chat = Alpine.store("chat");
        const sinceOpen = chat.lastOpenAt ? Date.now() - chat.lastOpenAt : Infinity;
        if (sinceOpen > 3000) {
            chat.connected = false;
        }
    };
}

// Refresh the panel store for whichever mode is currently active.
// Called after turn_complete / session_updated so the side-panel
// reflects state the agent just changed. Quiet no-op when the active
// mode has no panel store.
function refreshActivePanel() {
    const mode = Alpine.store("mode");
    if (!mode || !mode.panelModes || !mode.panelModes.includes(mode.active)) return;
    const store = Alpine.store(mode.active);
    if (store && typeof store.load === "function") store.load();
}

function routeEvent(ev) {
    const chat = Alpine.store("chat");
    const prompts = Alpine.store("prompts");
    const tokens = Alpine.store("tokens");
    // Every event carries `session_name` (stamped by WebUI._publish on
    // the server). Route into the matching per-session slot so events
    // from a background session don't clobber the focused one.
    const name = ev.session_name || null;
    const slot = chat._slot(name);
    const isFocused = !name || name === chat.currentName;
    switch (ev.kind) {
        case "user_message": break;  // echoed locally on send
        case "assistant_start": chat.startAssistant(ev.turn_id, name); break;
        case "assistant_delta": chat.appendDelta(ev.turn_id, ev.text || "", name); break;
        case "assistant_end":
            chat.endAssistant(ev.turn_id, name);
            // Keep busy=true until turn_complete (more tool calls may follow).
            break;
        case "tool_call": chat.addToolCall(ev.tool_name, ev.tool_args, name); break;
        case "thinking_delta": chat.addThinking(ev.text || "", name); break;
        case "tool_result":
            chat.addToolResult(ev.tool_name || "", ev.text || "", name);
            break;
        case "info": chat.addInfo(ev.text || "", null, name); break;
        case "error":
            chat.addError(ev.text || "", name);
            slot.busy = false;
            chat.finishTurn(name);
            break;
        case "status_start": break;  // spinner status; trace dots cover it
        case "status_end": break;
        case "prompt":
            // Prompts are session-scoped; the queue carries name so the
            // inline card only renders in its own session's chat.
            prompts.push({ id: ev.id, prompt: ev.prompt, session_name: name });
            break;
        case "prompt_resolved":
        case "prompt_cancelled": break;
        case "turn_complete":
            // Tokens are global (current session's totals) — only update
            // the meter when the focused session is the one that completed.
            if (isFocused && ev.result && ev.result.tokens) {
                Object.assign(tokens, ev.result.tokens);
            }
            slot.busy = false;
            chat.finishTurn(name);
            // Refresh the active panel store so kanban/curriculum/etc.
            // reflect whatever the just-finished turn changed.
            if (isFocused) refreshActivePanel();
            break;
        case "command_result":
            chat.addInfo(
                `/${(ev.result && ev.result.command) || "command"}: ${(ev.result && ev.result.message) || ""}`,
                null, name
            );
            slot.busy = false;
            chat.finishTurn(name);
            // Slash commands like /feature new mutate the session's
            // panel state without triggering a model turn, so the
            // turn_complete hook never fires. Refresh here too.
            if (isFocused) refreshActivePanel();
            break;
        case "diff":
            chat.addInfo(`diff proposed: ${ev.filename}`, null, name);
            break;
        case "session_updated":
            slot.externalActive = true;
            chat.addInfo(
                "↻ another mucli process updated this session — reloading history",
                { standalone: true }, name
            );
            chat.loadHistory(name);
            if (isFocused) refreshActivePanel();
            break;
    }
}

// ---------- json rendering helper -----------------------------------------
//
// Tool args and tool results often arrive as nested objects or JSON-encoded
// strings. Always emit a multi-line pretty-print + highlight.js classes
// so users can read the structure instead of squinting at a stringified blob.

function renderJSON(val) {
    if (val === undefined || val === null) return null;

    let pretty;
    if (typeof val === "string") {
        const trimmed = val.trim();
        // If the string itself parses as JSON, reformat it pretty.
        if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
            try {
                pretty = JSON.stringify(JSON.parse(trimmed), null, 2);
            } catch {
                pretty = val;
            }
        } else {
            pretty = val;
        }
    } else {
        try { pretty = JSON.stringify(val, null, 2); }
        catch { pretty = String(val); }
    }

    // Try highlight.js for json; if anything fails fall back to plain text.
    if (typeof hljs !== "undefined") {
        try {
            return hljs.highlight(pretty, {
                language: "json",
                ignoreIllegals: true,
            }).value;
        } catch {}
    }
    return escapeHtml(pretty);
}

function summarizeTrace(t) {
    if (!t || !t.events) return "trace";
    const counts = {};
    for (const ev of t.events) counts[ev.kind] = (counts[ev.kind] || 0) + 1;
    const parts = [];
    if (counts.tool_call) parts.push(`${counts.tool_call} tool${counts.tool_call > 1 ? "s" : ""}`);
    if (counts.tool_result) parts.push(`${counts.tool_result} result${counts.tool_result > 1 ? "s" : ""}`);
    if (counts.thinking) parts.push(`${counts.thinking} think`);
    if (counts.info) parts.push(`${counts.info} note${counts.info > 1 ? "s" : ""}`);
    const label = parts.join(" · ") || (t.running ? "thinking" : "trace");
    const dur = t.elapsed
        ? `${t.elapsed}s`
        : (t.running
            ? `${((Date.now() - t.startedAt) / 1000).toFixed(1)}s`
            : null);
    return dur ? `${label} · ${dur}` : label;
}

function eventLabel(ev) {
    switch (ev.kind) {
        case "tool_call":   return "→ tool";
        case "tool_result": return "← result";
        case "thinking":    return "thinking";
        case "info":        return "info";
        default:            return ev.kind;
    }
}

// ---------- prompt modal component ---------------------------------------
//
// Defined at module scope (not inside the modal template) because the
// modal HTML lives inside a `<template x-if>`. The browser treats
// `<template>` content as inert — scripts inside it never execute.
// Putting promptModal() here guarantees it's defined when Alpine
// evaluates `x-data="promptModal()"` on the modal mount.

function promptModal() {
    return {
        // Local mirror of the active prompt — sync() keeps it fresh.
        prompt: {},
        id: null,
        shape: "",
        description: "",
        multi: false,

        // Per-shape local form state.
        value: null,
        text: "",
        otherText: "",
        answers: {},
        remember: false,

        sync(active) {
            // x-effect calls this whenever the bound item changes.
            // The inline prompt cards iterate $store.prompts.queue, so
            // each card's `active` is its own queue entry (not the
            // head). Reset local form state only when the id changes
            // so re-renders don't clobber what the user has typed.
            if (!active) {
                this.id = null;
                this.prompt = {};
                this.shape = "";
                this.description = "";
                this.multi = false;
                return;
            }
            if (active.id === this.id) return;
            const p = active.prompt || {};
            this.id = active.id;
            this.prompt = p;
            this.shape = p.shape || "";
            this.description = String(p.description || p.message || "");
            this.multi = !!p.multi_select;
            this.value = this.multi ? [] : null;
            this.text = String(p.default || "");
            this.otherText = "";
            this.answers = {};
            this.remember = false;
            // Apply syntax highlighting to any code fences in the
            // freshly-rendered title/description/options.
            queueMicrotask(() => highlightInScope(".modal"));
        },

        title() {
            switch (this.shape) {
                case "choice":
                case "choices":       return this.prompt.question || "choose";
                case "input":         return "input";
                case "confirm":       return "confirm";
                case "quiz":          return "quiz";
                case "tool_approval": return "approve tool call";
                case "diff":          return "review change";
                default:              return "prompt";
            }
        },
        // Title and description are model-authored. They commonly carry
        // markdown (code fences, inline `code`, lists, etc.) — render
        // them via marked so the user sees a parseable layout instead
        // of a single line of plaintext.
        titleHtml() { return renderMarkdownBlock(this.title()); },
        descriptionHtml() { return renderMarkdownBlock(this.description); },

        okLabel() {
            switch (this.shape) {
                case "tool_approval": return "approve";
                case "confirm":       return "yes";
                case "diff":          return "approve";
                default:              return "ok";
            }
        },
        optLabel(o) { return typeof o === "string" ? o : (o.label || o.name || JSON.stringify(o)); },
        // Inline-only markdown for option labels (so `code` renders but
        // a paragraph wrapper doesn't get inserted).
        optLabelHtml(o) { return renderMarkdownInline(this.optLabel(o)); },
        optValue(o) { return typeof o === "string" ? o : (o.value !== undefined ? o.value : (o.id || o.label)); },

        // Quiz options: the TUI submits the option STRING (q.options[idx]),
        // so the GUI does the same — graders expect the literal option
        // text, not an index or an id. Falls back gracefully for object
        // options.
        quizOptionValue(o) {
            if (typeof o === "string") return o;
            if (o == null) return "";
            if (o.value !== undefined) return o.value;
            if (o.label !== undefined) return o.label;
            return String(o);
        },
        quizOptionLabel(o) { return this.optLabel(o); },

        // Expose the inline-markdown helper inside the modal scope so
        // x-html template expressions can call it (e.g. for question
        // prompts that contain inline `code`).
        renderMarkdownInline(text) { return renderMarkdownInline(text); },
        formatArgs(args) {
            if (args === undefined || args === null) return "(no arguments)";
            try { return JSON.stringify(args, null, 2); }
            catch { return String(args); }
        },

        async submit() {
            if (!this.id) return;
            let payload;
            switch (this.shape) {
                case "choice":
                case "choices": {
                    const selected = Array.isArray(this.value)
                        ? this.value
                        : (this.value !== null && this.value !== undefined ? [this.value] : []);
                    const hasOther = selected.includes("__other__");
                    const real = selected.filter(v => v !== "__other__");
                    payload = {
                        selected: real,
                        other_text: hasOther ? this.otherText : "",
                    };
                    break;
                }
                case "input":         payload = { value: this.text }; break;
                case "confirm":       payload = { value: true }; break;
                case "quiz":          payload = { answers: this.answers }; break;
                case "tool_approval": payload = { approved: true, remember: this.remember }; break;
                case "diff":          payload = { approved: true }; break;
                default:              payload = { value: this.value };
            }
            // Stash the decision in the chat scrollback BEFORE clearing
            // the live card, so the user has a breadcrumb of what they
            // picked when scrolling back.
            Alpine.store("chat").addPromptResolved({
                shape: this.shape,
                title: this.title(),
                answer: this._summarizeAnswer(payload),
                toolName: this.prompt.tool_name || "",
                cancelled: false,
            });
            await Alpine.store("prompts").answer(this.id, payload);
        },
        async cancel() {
            if (this.id) {
                Alpine.store("chat").addPromptResolved({
                    shape: this.shape,
                    title: this.title(),
                    answer: "",
                    toolName: this.prompt.tool_name || "",
                    cancelled: true,
                });
                await Alpine.store("prompts").cancel(this.id);
            } else {
                Alpine.store("prompts").queue.shift();
            }
        },

        _summarizeAnswer(payload) {
            switch (this.shape) {
                case "choice":
                case "choices": {
                    const sel = (payload && payload.selected) || [];
                    const other = (payload && payload.other_text) || "";
                    const parts = sel.slice();
                    if (other) parts.push(other);
                    return parts.length ? parts.join(", ") : "(none)";
                }
                case "input":         return String((payload && payload.value) || "");
                case "confirm":       return "yes";
                case "quiz": {
                    const a = (payload && payload.answers) || {};
                    const entries = Object.entries(a);
                    if (!entries.length) return "(no answers)";
                    // Show the answers themselves, not just the count.
                    return entries.map(([k, v]) => `${k}: ${v}`).join("; ");
                }
                case "tool_approval": {
                    const tool = this.prompt.tool_name ? ` \`${this.prompt.tool_name}\`` : "";
                    const remember = payload && payload.remember ? " (remembered)" : "";
                    return `approved${tool}${remember}`;
                }
                case "diff":          return "approved";
                default:              return String((payload && payload.value) || "");
            }
        },
    };
}

function autoSize(el) {
    el.style.height = "auto";
    el.style.height = Math.min(el.scrollHeight, 200) + "px";
}

// ---------- theme ----------------------------------------------------------
//
// The early-load script in base.html sets data-theme before first paint.
// These helpers run after DOM ready to (a) sync the highlight.js stylesheet
// to the current theme and (b) flip the theme on user toggle.

function applyTheme(theme) {
    document.documentElement.setAttribute("data-theme", theme);
    const dark = document.getElementById("hljs-dark");
    const light = document.getElementById("hljs-light");
    if (dark)  dark.disabled  = (theme === "light");
    if (light) light.disabled = (theme === "dark");
}

function toggleTheme() {
    const cur = document.documentElement.getAttribute("data-theme") || "dark";
    const next = cur === "dark" ? "light" : "dark";
    try { localStorage.setItem("mucli-theme", next); } catch (e) {}
    applyTheme(next);
}

// Re-highlight code blocks after theme flips so colors apply
// consistently to anything already rendered.
function rehighlightAll() {
    document.querySelectorAll("pre code").forEach((el) => {
        delete el.dataset.highlighted;
    });
    highlightAll();
}

document.addEventListener("DOMContentLoaded", () => {
    applyTheme(document.documentElement.getAttribute("data-theme") || "dark");
    bootSSE();
    Alpine.store("chat").loadHistory();
    Alpine.store("sessions").load();
    // mode.load() preloads the active mode's panel store via panelModes —
    // the panel populates the instant the user lands in a panel mode,
    // no extra mode-flip round-trip needed.
    Alpine.store("mode").load();
    Alpine.store("yolo").load();
    setInterval(() => Alpine.store("sessions").load(), 5000);
    // Live clock: bump while ANY session's turn is in flight so the
    // running trace header re-renders its elapsed time. (One global tick
    // is enough — we re-render every slot's clock; backgrounded ones
    // aren't visible but the cost is negligible.)
    setInterval(() => {
        const chat = Alpine.store("chat");
        let anyBusy = false;
        for (const key of Object.keys(chat.chats)) {
            if (chat.chats[key].busy) {
                chat.chats[key].clock = Date.now();
                anyBusy = true;
            }
        }
        // Touching `clock` (the focused-slot getter) is harmless if not busy.
        if (anyBusy) { /* re-render already triggered above */ }
    }, 500);
});
