/* Trace Analyzer — Alpine component + custom <canvas> renderers.
 *
 * No charting library: the server returns structured series/snapshot
 * (`/api/traces` + `/api/traces/<id>`), and this file draws every chart with
 * vanilla 2D canvas. Mirrors the codebase's bindCanvas idiom from the memory
 * panel. Clicking an iter in any chart/table scrolls the conversation view to
 * that iteration and highlights it.
 *
 * Each panel has a ⓘ info button (see trace.html) whose popout explains what the
 * panel shows, how to read it, and which harness decision it informs.
 */

// Shared palette — within-panel colors are distinct; cross-panel reuse is
// intentional only for semantically-identical signals (e.g. red = error/bad
// everywhere). Kept in one place so the charts read consistently.
const PALETTE = {
    actual: "#e0a040",        // amber — real prompt tokens
    est: "#b0aca0",           // accent-ish — harness estimate (dashed)
    drift: "#e07aa0",         // pink — tokenizer drift (the headline line)
    tokIn: "#e0a040",
    tokOut: "#7ec96b",        // green
    tokCached: "#5b9bd0",     // blue
    tokReasoning: "#c08ae0",  // purple
    taskMem: "#7ec96b",
    scratchpad: "#c98a4b",    // amber-brown (distinct from taskMem)
    subBar: "#5b9bd0",
    read: "#5b9bd0",
    redundant: "#d9534f",
    ok: "#5b9bd0",
    err: "#d9534f",
    cache: "#7ec96b",
    stuck: "#d9534f",
    stall: "#e0a040",
    limit: "rgba(220,100,100,0.55)",
    compaction: "rgba(120,180,220,0.7)",
    warnBand: "rgba(220,160,60,0.12)",
    heat: [224, 160, 64],      // amber ramp for the layer heat strip
    driftPos: [91, 155, 208],  // teal — positive drift (est > actual)
    driftNeg: [217, 83, 79],   // red — negative drift (est < actual)
    status: {                  // memory by_status colors
        active: "#7ec96b", stale: "#e0a040", done: "#5b9bd0",
        pending: "#c08ae0", pruned: "#8b8b8b",
    },
    statusDefault: "#8b8b8b",
};

const READ_TOOLS = new Set([
    "read_file", "get_chunk", "list_dir", "search_for_string",
    "search_references", "retrieve_relevant_context", "get_workspace_details",
]);

function traceApp() {
    return {
        runs: [],
        runId: "",
        summary: null,
        series: { context: [], layers_stacked: {}, drift: [], tokens: [],
                  latency: [],
                  tool_histogram: [], compaction_timeline: [], nudge_timeline: [],
                  nudge_efficacy: [], redundant_reads: [], subagent_timeline: [],
                  memory_series: [] },
        iters: [],
        tools: [],
        snapshot: { grid: [], drift_strip: [], xs: [], compaction_cols: [],
                    context_actual: [], context_est: [],
                    meta: { layers: [], labels: {}, layer_vmax: {}, n: 0 } },
        loading: false,
        selectedIter: null,
        selectedTool: null,
        driftLog: false,        // signed-log y scale for the drift chart
        infoOpen: null,         // open info popout key
        hoverRef: null, hoverIdx: null,
        session: "",            // session scope (?session=) — traces are per-session
        runBounds: [],           // per-run global [start,end] for the combined view
        theme: "dark",          // current data-theme (for the toggle button glyph)
        expandedTools: {},      // toolKey -> true: that tool's result preview is expanded
        expandedCards: {},      // cardKey -> true: all tool previews in that turn expanded

        async init() {
            // Session scope: the analyzer is opened from the chat with
            // ?session=<name> and shows a COMBINED view of every run in that
            // session (all of it, chronologically, run boundaries marked),
            // ignoring every other session. No param → fall back to a global
            // per-run picker.
            const qs = new URLSearchParams(window.location.search);
            this.session = (qs.get("session") || "").trim();
            this.theme = document.documentElement.getAttribute("data-theme") || "dark";
            // Re-render canvases on resize (debounced) — _setupCanvas reads
            // clientWidth at draw time, so a re-render picks up the new CSS size.
            this._resizeHandler = () => {
                clearTimeout(this._resizeTimer);
                this._resizeTimer = setTimeout(() => this.renderAll(), 150);
            };
            window.addEventListener("resize", this._resizeHandler);
            if (this.session) {
                await this.loadSession();
            } else {
                await this.loadRuns();
            }
        },

        async loadSession() {
            // Combined multi-run view: every run in the session merged into
            // one series/summary/snapshot, with run_bounds for boundary markers.
            this.loading = true;
            this.selectedIter = null;
            this.selectedTool = null;
            try {
                const r = await fetch(
                    "/api/traces/session/" + encodeURIComponent(this.session) + "?cols=256"
                );
                if (!r.ok) { this.summary = null; return; }
                const d = await r.json();
                this.summary = d.summary;
                this.series = d.series;
                this.iters = d.iters || [];
                this.tools = d.tools || [];
                this.snapshot = d.snapshot || this.snapshot;
                this.runBounds = d.run_bounds || [];
                this.runId = d.run_id || "";
                this.$nextTick(() => {
                    this.attachHovers();
                    this.renderAll();
                });
            } finally {
                this.loading = false;
            }
        },

        async loadRuns() {
            // No-session fallback: global per-run picker. Auto-loads newest.
            try {
                const url = "/api/traces" + (this.session
                    ? "?session=" + encodeURIComponent(this.session) : "");
                const r = await fetch(url);
                this.runs = await r.json();
            } catch (e) {
                this.runs = [];
            }
            // Auto-load the newest run so the analyzer opens already
            // visualizing data instead of waiting on a pick.
            if (this.runs.length && !this.runId) {
                this.runId = this.runs[0].run_id;
                this.loadRun();
            }
        },

        async loadRun() {
            if (!this.runId) { this.summary = null; return; }
            this.loading = true;
            this.selectedIter = null;
            this.selectedTool = null;
            this.runBounds = [];   // single-run view has no run boundaries
            try {
                const r = await fetch("/api/traces/" + encodeURIComponent(this.runId) + "?cols=256");
                const d = await r.json();
                this.summary = d.summary;
                this.series = d.series;
                this.iters = d.iters || [];
                this.tools = d.tools || [];
                this.snapshot = d.snapshot || this.snapshot;
                this.$nextTick(() => {
                    this.attachHovers();
                    this.renderAll();
                });
            } finally {
                this.loading = false;
            }
        },

        toggleInfo(key) {
            this.infoOpen = (this.infoOpen === key) ? null : key;
        },

        // Mirror the chat's $store.theme.toggle() (app.js): flip data-theme and
        // persist to localStorage. app.css themes via html[data-theme=...], so
        // setting the attribute is enough — no stylesheet swapping needed here
        // (that's only for the chat's hljs code blocks).
        toggleTheme() {
            this.theme = (this.theme === "dark") ? "light" : "dark";
            document.documentElement.setAttribute("data-theme", this.theme);
            try { localStorage.setItem("mucli-theme", this.theme); } catch (e) {}
            this.renderAll();   // re-render canvases against the new theme vars
        },

        // ---- helpers ---------------------------------------------------
        fmtNum(v) { v = Number(v || 0); return v >= 1000 ? v.toLocaleString() : String(v); },
        fmtMs(v) { v = Number(v || 0); if (v >= 60000) return (v / 60000).toFixed(1) + "m"; if (v >= 1000) return (v / 1000).toFixed(1) + "s"; return Math.round(v) + "ms"; },
        fmtCost(v) { v = Number(v || 0); return v ? "$" + (v < 0.01 ? v.toFixed(6) : v.toFixed(4)) : "—"; },
        fmtStatus(r) { return r.iters > 0 ? "done" : "—"; },
        toolHistSorted() { return [...(this.series.tool_histogram || [])].sort((a, b) => b.count - a.count); },
        maxToolCount() { return Math.max(1, ...this.toolHistSorted().map(h => h.count)); },
        errorCodes(h) { return Object.entries(h.error_codes || {}); },
        selectedToolSeries() {
            const h = (this.series.tool_histogram || []).find(h => h.name === this.selectedTool);
            return (h && h.latency_series) || [];
        },
        toolsForIter(it) { return (this.tools || []).filter(t => t.iter === it); },
        // Active subagent children at the selected iter, else all seen this run.
        subagentChildren() {
            const tl = this.series.subagent_timeline || [];
            if (this.selectedIter !== null) {
                const e = tl.find(s => s.iter === this.selectedIter);
                if (e && e.children && e.children.length) return e.children;
            }
            const seen = {}; const out = [];
            for (const s of tl) for (const c of (s.children || [])) {
                const k = c.task_id || (c.task_id === 0 ? 0 : (c.status + (out.length)));
                if (!seen[k]) { seen[k] = 1; out.push(c); }
            }
            return out;
        },

        selectIter(it) {
            this.selectedIter = it;
            this.$nextTick(() => {
                // The conversation view groups iters into turn cards, so the
                // exact `iter-<it>` id may not exist (the card is keyed by its
                // first iter). Fall back to the card whose [min,max] range
                // contains the iter so chart clicks still scroll to the right turn.
                let el = document.getElementById("iter-" + it);
                if (!el) {
                    const cards = document.querySelectorAll("[data-iter-min]");
                    for (const c of cards) {
                        const lo = +c.getAttribute("data-iter-min");
                        const hi = +c.getAttribute("data-iter-max");
                        if (it >= lo && it <= hi) { el = c; break; }
                    }
                }
                if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
                this.renderAll();
            });
        },

        // Group iters into assistant "turn cards" with run-separator banners
        // spliced in at each run boundary (combined session view). A card
        // starts at each iter where the model produced text (the turn's
        // "voice") and absorbs the following tool-only iters until the next
        // text iter — so prose sits at the top of the card and the tool calls
        // it then made hang below it. Leading tool-only iters (the model
        // acting before first speaking) form an "acting only" card with no
        // voice. Returns a list of either {sep:true, run_id, idx, model,
        // status, iters} or {card:true, ...turnCardFields}.
        turnCards() {
            const out = [];
            let prevRun = null;
            const bounds = this.runBounds || [];
            const idxOf = (rid) => {
                const i = bounds.findIndex(b => b.run_id === rid);
                return i >= 0 ? i + 1 : null;
            };
            let cur = null;
            const flush = () => { if (cur) { out.push({ card: true, ...cur }); cur = null; } };
            for (const it of (this.iters || [])) {
                const rid = it.run_id || "";
                if (rid !== prevRun) {
                    flush();
                    const b = bounds.find(x => x.run_id === rid);
                    out.push({
                        sep: true, run_id: rid, idx: idxOf(rid),
                        model: b ? b.model : "", status: b ? b.status : "",
                        iters: b ? b.iters : null,
                    });
                    prevRun = rid;
                    cur = null;
                }
                const tools = this.toolsForIter(it.iter).map(t => ({ ...t, _iter: it.iter }));
                if (it.has_text) {
                    // A speaking iter closes the previous card and opens a new
                    // one with itself as the voice (not yet pushed, so later
                    // tool-only iters absorb into it).
                    flush();
                    cur = { iters: [it], voice: it, toolList: tools.slice() };
                } else {
                    if (!cur) cur = { iters: [it], voice: null, toolList: [] };
                    else cur.iters.push(it);
                    cur.toolList.push(...tools);
                }
            }
            flush();
            // Badge the last turn card as the final output.
            for (let i = out.length - 1; i >= 0; i--) {
                if (out[i].card) { out[i].final = true; break; }
            }
            return out;
        },

        // ---- turn-card helpers (called from the template) ----------------
        cardKey(c) { return "c" + c.iters[0].iter; },
        cardLabel(c) {
            const a = c.iters[0].iter, b = c.iters[c.iters.length - 1].iter;
            return a === b ? "iter " + a : "iters " + a + "–" + b;
        },
        cardWall(c) { return c.iters.reduce((s, it) => s + (it.wall_ms || 0), 0); },
        cardTokens(c) {
            return c.iters.reduce((s, it) => s + ((it.tokens || {}).in || 0), 0);
        },
        cardMaxDrift(c) {
            let m = 0;
            for (const it of c.iters) { const d = Math.abs(it.context ? it.context.drift_pct : 0); if (d > m) m = d; }
            return Math.round(m);
        },
        cardHasCompaction(c) { return c.iters.some(it => it.compaction); },
        toolKey(c, ti) { return c.iters[0].iter + ":" + ti; },
        toolExpanded(c, ti) {
            return !!this.expandedTools[this.toolKey(c, ti)] || !!this.expandedCards[this.cardKey(c)];
        },
        toggleTool(c, ti) {
            const k = this.toolKey(c, ti);
            this.expandedTools[k] = !this.expandedTools[k];
            // reactive: reassign so Alpine notices the object-key change
            this.expandedTools = { ...this.expandedTools };
        },
        toggleCardTools(c) {
            const k = this.cardKey(c);
            this.expandedCards[k] = !this.expandedCards[k];
            this.expandedCards = { ...this.expandedCards };
        },

        // Draw run-boundary dividers on a per-iter chart. `data` is the series
        // (each point carries `iter`); we map each run's start_iter to its x.
        _runMarks(ctx, w, h, pad, data) {
            if (!this.runBounds || this.runBounds.length < 2 || !data.length) return;
            const t = this._theme();
            const n = data.length;
            ctx.font = "9px " + (getComputedStyle(document.body).fontFamily);
            for (let bi = 1; bi < this.runBounds.length; bi++) {
                const startIter = this.runBounds[bi].start_iter;
                const idx = data.findIndex(d => d.iter === startIter);
                if (idx < 0) continue;
                const x = this._iterX(idx, n, pad, w);
                ctx.strokeStyle = "rgba(176,172,160,0.22)";
                ctx.setLineDash([2, 4]); ctx.lineWidth = 1;
                ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, h - pad.b); ctx.stroke();
                ctx.setLineDash([]);
                // tiny run number tick at the top so boundaries are countable
                ctx.fillStyle = t.dim; ctx.textAlign = "center"; ctx.textBaseline = "top";
                const label = "run " + (bi + 1);
                const lx = Math.min(Math.max(pad.l + 14, x), w - pad.r - 14);
                ctx.fillText(label, lx, pad.t + 1);
            }
        },
        selectTool(name) {
            this.selectedTool = (this.selectedTool === name) ? null : name;
            this.$nextTick(() => this.renderTool());
        },

        // ---- canvas theme ---------------------------------------------
        _theme() {
            const css = getComputedStyle(document.documentElement);
            const pick = (v, fb) => (css.getPropertyValue(v).trim() || fb);
            return {
                text: pick("--text", "#d4d0c8"),
                dim: pick("--text-dim", "#6b6860"),
                border: pick("--border-strong", "rgba(255,255,255,0.10)"),
                accent: pick("--accent", "#b0aca0"),
                bg: pick("--bg", "#1a1814"),
            };
        },

        _setupCanvas(ref) {
            const c = this.$refs[ref];
            if (!c) return null;
            const dpr = window.devicePixelRatio || 1;
            // Logical (CSS-pixel) height is FIXED per canvas from its original
            // HTML height attribute — cache it on the first call. We set
            // c.height = h*dpr below, so re-reading c.height on a later render
            // would read back the already-scaled value and compound by dpr
            // *every* render. Since mousemove triggers a full renderAll, the
            // canvas would balloon exponentially on hover (the "zooms in a
            // huge amount" bug). Width comes from clientWidth (stable CSS
            // pixels), so it never compounds.
            if (!c._logicalH) {
                c._logicalH = parseInt(c.getAttribute("height")) || c.height || 200;
            }
            const w = c.clientWidth || parseInt(c.getAttribute("width")) || 1200;
            const h = c._logicalH;
            c.width = w * dpr;
            c.height = h * dpr;
            const ctx = c.getContext("2d");
            ctx.scale(dpr, dpr);
            return { ctx, w, h };
        },

        _drawAxes(ctx, w, h, pad, ys, opts = {}) {
            const t = this._theme();
            ctx.strokeStyle = t.border; ctx.fillStyle = t.dim;
            ctx.font = "10px " + (getComputedStyle(document.body).fontFamily);
            ctx.lineWidth = 1;
            const unit = opts.unit || "";
            // y gridlines + labels
            ctx.textAlign = "right"; ctx.textBaseline = "middle";
            for (let i = 0; i <= 4; i++) {
                const yv = ys.max - (ys.max - ys.min) * (i / 4);
                const yy = pad.t + (h - pad.t - pad.b) * (i / 4);
                ctx.beginPath(); ctx.moveTo(pad.l, yy); ctx.lineTo(w - pad.r, yy); ctx.stroke();
                ctx.fillText(this.fmtNum(Math.round(yv)), pad.l - 4, yy);
            }
            // y-axis unit caption (top-left of plot)
            if (unit) {
                ctx.fillStyle = t.dim; ctx.textAlign = "left"; ctx.textBaseline = "top";
                ctx.fillText(unit, pad.l + 2, 2);
                ctx.fillStyle = t.dim;
            }
            // x labels — denser when there are many iters
            const xs = opts.xs || [];
            ctx.textAlign = "center"; ctx.textBaseline = "top";
            if (xs.length) {
                const plotW = w - pad.l - pad.r;
                const want = xs.length > 60 ? 7 : (xs.length > 20 ? 5 : 3);
                const labels = [];
                for (let k = 0; k < want; k++) {
                    const idx = Math.round((k / Math.max(1, want - 1)) * (xs.length - 1));
                    if (!labels.includes(idx)) labels.push(idx);
                }
                for (const idx of labels) {
                    const xx = pad.l + (xs.length === 1 ? plotW / 2 : (idx / (xs.length - 1)) * plotW);
                    ctx.fillText("iter " + xs[idx], xx, h - pad.b + 4);
                }
            }
        },

        _iterX(i, n, pad, w) {
            if (n <= 1) return pad.l + (w - pad.l - pad.r) / 2;
            return pad.l + (i / (n - 1)) * (w - pad.l - pad.r);
        },

        _empty(ctx, w, h, msg) {
            const t = this._theme();
            ctx.fillStyle = t.dim; ctx.font = "11px " + (getComputedStyle(document.body).fontFamily);
            ctx.textAlign = "left"; ctx.textBaseline = "top";
            ctx.fillText(msg, 8, 12);
        },

        // ---- hover (line charts) --------------------------------------
        attachHovers() {
            if (this._hoverAttached) return;
            this._hoverAttached = true;
            const cfgs = [
                { ref: "ctxCanvas", pad: { l: 56, r: 12 }, key: "context",
                  label: it => `${this.fmtNum(it.real || it.actual)} real · ${this.fmtNum(it.total_est)} est tok${it.drift_ratio ? " · drift ×" + it.drift_ratio : ""}` },
                { ref: "driftCanvas", pad: { l: 56, r: 12 }, key: "drift",
                  label: it => `${it.drift_pct}% drift` },
                { ref: "tokCanvas", pad: { l: 56, r: 12 }, key: "tokens",
                  label: it => `in ${this.fmtNum(it.in)} · out ${this.fmtNum(it.out)} tok` },
                { ref: "latCanvas", pad: { l: 56, r: 12 }, key: "latency",
                  label: it => `${this.fmtMs(it.wall_ms)} wall` },
            ];
            for (const c of cfgs) {
                const cv = this.$refs[c.ref]; if (!cv) continue;
                cv._cfg = c;
                cv.addEventListener("mousemove", (e) => this._onHoverMove(e, cv));
                cv.addEventListener("mouseleave", () => {
                    if (this.hoverRef === c.ref) { this.hoverRef = null; this.hoverIdx = null; this.renderAll(); }
                });
                cv.addEventListener("click", (e) => {
                    const idx = this._hoverIdxAt(e, cv); if (idx == null) return;
                    const s = this.series[c.key] || []; if (s[idx]) this.selectIter(s[idx].iter);
                });
            }
        },
        _hoverIdxAt(e, cv) {
            const c = cv._cfg; const rect = cv.getBoundingClientRect();
            const x = e.clientX - rect.left;
            const s = this.series[c.key] || []; const n = s.length; if (!n) return null;
            const w = cv.clientWidth || cv.width;
            const plotL = c.pad.l, plotR = w - c.pad.r;
            if (x < plotL || x > plotR) return null;
            const frac = (x - plotL) / Math.max(1, plotR - plotL);
            return Math.max(0, Math.min(n - 1, Math.round(frac * (n - 1))));
        },
        _onHoverMove(e, cv) {
            const idx = this._hoverIdxAt(e, cv); if (idx == null) return;
            this.hoverRef = cv._cfg.ref; this.hoverIdx = idx; this.renderAll();
        },
        _drawHover(ctx, w, h, pad, n, idx, label) {
            if (this.hoverRef !== this._curRef) return;
            const t = this._theme();
            const x = this._iterX(idx, n, pad, w);
            ctx.strokeStyle = t.accent; ctx.setLineDash([3, 3]); ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, h - pad.b); ctx.stroke();
            ctx.setLineDash([]);
            // chip
            ctx.font = "10px " + (getComputedStyle(document.body).fontFamily);
            const tw = ctx.measureText(label).width + 10;
            const cx = Math.min(Math.max(pad.l + 2, x + 6), w - pad.r - tw - 2);
            ctx.fillStyle = t.bg; ctx.strokeStyle = t.border;
            ctx.fillRect(cx, pad.t + 2, tw, 16);
            ctx.strokeRect(cx, pad.t + 2, tw, 16);
            ctx.fillStyle = t.text; ctx.textAlign = "left"; ctx.textBaseline = "middle";
            ctx.fillText(label, cx + 5, pad.t + 10);
        },

        // ---- renderers -------------------------------------------------
        renderAll() {
            this.renderContext();
            this.renderHeatStrip();
            this.renderDrift();
            this.renderReads();
            this.renderSub();
            this.renderMem();
            this.renderTokens();
            this.renderLatency();
            this.renderTool();
        },

        renderContext() {
            const s = this._setupCanvas("ctxCanvas"); if (!s) return;
            const { ctx, w, h } = s; const t = this._theme();
            const pad = { l: 56, r: 12, t: 14, b: 20 };
            ctx.clearRect(0, 0, w, h);
            this._curRef = "ctxCanvas";
            const ctxData = this.series.context || [];
            if (!ctxData.length) { this._empty(ctx, w, h, "no context data"); return; }
            const limit = this.summary ? this.summary.context_limit : 0;
            // `real` is the representative real-prompt fill (drift-corrected
            // estimate, floored at the provider's reported count). `total_est`
            // is the raw cl100k estimate — the gap between them is the
            // tokenizer drift that, uncorrected, causes the "prompt too long"
            // 400 the old `actual` (Ollama cached delta) hid.
            const all = ctxData.flatMap(d => [d.real || d.actual, d.total_est]).concat(limit || 0);
            const max = Math.max(1, ...all) * 1.08;
            this._drawAxes(ctx, w, h, pad, { min: 0, max }, { xs: ctxData.map(d => d.iter), unit: "tokens" });

            const n = ctxData.length;
            const plotH = h - pad.t - pad.b;

            // real (solid) + total_est (dashed)
            const line = (key, color, dash) => {
                ctx.strokeStyle = color; ctx.lineWidth = 1.6; ctx.setLineDash(dash || []);
                ctx.beginPath();
                for (let i = 0; i < n; i++) {
                    const x = this._iterX(i, n, pad, w);
                    const y = pad.t + plotH - (ctxData[i][key] / max) * plotH;
                    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
                }
                ctx.stroke(); ctx.setLineDash([]);
            };
            line("real", PALETTE.actual, []);
            line("total_est", PALETTE.est, [4, 3]);

            // context_limit reference — draw on-chart, or an off-chart label
            if (limit > 0) {
                if (limit <= max) {
                    const y = pad.t + plotH - (limit / max) * plotH;
                    ctx.strokeStyle = PALETTE.limit; ctx.setLineDash([2, 3]);
                    ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke();
                    ctx.setLineDash([]);
                } else {
                    ctx.fillStyle = "rgba(220,100,100,0.7)";
                    ctx.font = "10px " + (getComputedStyle(document.body).fontFamily);
                    ctx.textAlign = "right"; ctx.textBaseline = "top";
                    ctx.fillText("limit " + this.fmtNum(limit) + " tok (off-chart ↑)", w - pad.r - 2, pad.t + 2);
                }
            }

            this._compactionMarks(ctx, w, h, pad, n);
            this._runMarks(ctx, w, h, pad, ctxData);

            // legend
            ctx.font = "10px " + (getComputedStyle(document.body).fontFamily);
            ctx.textAlign = "left"; ctx.textBaseline = "top";
            ctx.fillStyle = PALETTE.actual; ctx.fillText("● real est", w - pad.r - 150, 4);
            ctx.fillStyle = PALETTE.est; ctx.fillText("┄ cl100k est", w - pad.r - 80, 4);

            // hover + selected
            if (this.hoverRef === "ctxCanvas" && this.hoverIdx != null) {
                const it = ctxData[this.hoverIdx];
                const dr = it.drift_ratio ? (" · drift ×" + it.drift_ratio) : "";
                this._drawHover(ctx, w, h, pad, n, this.hoverIdx,
                    this.fmtNum(it.real || it.actual) + " real · " + this.fmtNum(it.total_est) + " est" + dr);
            }
            this._selectedMark(ctx, w, h, pad, n, ctxData);
        },

        renderHeatStrip() {
            const s = this._setupCanvas("heatCanvas"); if (!s) return;
            const { ctx, w, h } = s; const t = this._theme();
            ctx.clearRect(0, 0, w, h);
            const snap = this.snapshot || {};
            const grid = snap.grid || [];
            const meta = snap.meta || {};
            const layers = meta.layers || [];
            const labels = meta.labels || {};
            const xs = snap.xs || [];
            const driftStrip = snap.drift_strip || [];
            const compCols = snap.compaction_cols || [];
            if (!grid.length || !layers.length) { this._empty(ctx, w, h, "no layer data"); return; }

            const driftH = 14, gap = 3;
            const pad = { l: 96, r: 12, t: 6, b: 16 };
            const cols = xs.length;
            const cellW = (w - pad.l - pad.r) / Math.max(1, cols);
            const rowsH = h - pad.t - pad.b - driftH - gap;
            const rowH = rowsH / layers.length;

            // layer heat rows
            ctx.font = "10px " + (getComputedStyle(document.body).fontFamily);
            ctx.textBaseline = "middle";
            layers.forEach((key, ri) => {
                const y = pad.t + ri * rowH;
                ctx.fillStyle = t.dim; ctx.textAlign = "right";
                ctx.fillText(labels[key] || key, pad.l - 6, y + rowH / 2);
                const row = grid[ri] || [];
                for (let ci = 0; ci < cols; ci++) {
                    const v = row[ci] || 0;
                    const x = pad.l + ci * cellW;
                    if (v > 0) {
                        const a = 0.06 + (v / 255) * 0.9;
                        ctx.fillStyle = `rgba(${PALETTE.heat[0]},${PALETTE.heat[1]},${PALETTE.heat[2]},${a})`;
                        ctx.fillRect(x, y + 0.5, Math.max(0.6, cellW - 0.4), rowH - 1);
                    }
                }
            });
            // drift strip
            const dy = pad.t + rowsH + gap;
            ctx.fillStyle = t.dim; ctx.textAlign = "right"; ctx.textBaseline = "middle";
            ctx.fillText("drift", pad.l - 6, dy + driftH / 2);
            for (let ci = 0; ci < cols; ci++) {
                const v = driftStrip[ci] || 128;
                const x = pad.l + ci * cellW;
                let fill;
                if (v >= 128) {
                    const a = (v - 128) / 128;
                    fill = `rgba(${PALETTE.driftPos[0]},${PALETTE.driftPos[1]},${PALETTE.driftPos[2]},${a * 0.9})`;
                } else {
                    const a = (128 - v) / 128;
                    fill = `rgba(${PALETTE.driftNeg[0]},${PALETTE.driftNeg[1]},${PALETTE.driftNeg[2]},${a * 0.9})`;
                }
                ctx.fillStyle = fill;
                ctx.fillRect(x, dy, Math.max(0.6, cellW - 0.4), driftH);
            }
            // compaction column markers
            ctx.fillStyle = PALETTE.compaction;
            for (const ci of compCols) {
                ctx.fillRect(pad.l + ci * cellW + cellW / 2 - 1, pad.t, 2, rowsH + gap + driftH);
            }
            // selected-iter column highlight
            if (this.selectedIter !== null) {
                const ci = xs.indexOf(this.selectedIter);
                if (ci >= 0) {
                    ctx.strokeStyle = t.accent; ctx.lineWidth = 1;
                    ctx.strokeRect(pad.l + ci * cellW, pad.t - 1, cellW, rowsH + gap + driftH);
                }
            }
            // x labels
            ctx.fillStyle = t.dim; ctx.textAlign = "center"; ctx.textBaseline = "top";
            const want = cols > 60 ? 7 : (cols > 20 ? 5 : 3);
            for (let k = 0; k < want; k++) {
                const ci = Math.round((k / Math.max(1, want - 1)) * (cols - 1));
                ctx.fillText("iter " + xs[ci], pad.l + ci * cellW + cellW / 2, h - pad.b + 2);
            }
        },

        _selectedMark(ctx, w, h, pad, n, data) {
            if (this.selectedIter === null) return;
            const idx = data.findIndex(d => d.iter === this.selectedIter);
            if (idx < 0) return;
            const t = this._theme();
            const x = this._iterX(idx, n, pad, w);
            ctx.strokeStyle = t.accent; ctx.setLineDash([2, 2]); ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, h - pad.b); ctx.stroke();
            ctx.setLineDash([]);
        },

        renderDrift() {
            const s = this._setupCanvas("driftCanvas"); if (!s) return;
            const { ctx, w, h } = s; const t = this._theme();
            const pad = { l: 56, r: 12, t: 14, b: 20 };
            ctx.clearRect(0, 0, w, h);
            this._curRef = "driftCanvas";
            const drift = this.series.drift || [];
            if (!drift.length) { this._empty(ctx, w, h, "no drift data"); return; }
            const n = drift.length;
            const plotH = h - pad.t - pad.b;

            // raw values
            const raw = drift.map(d => d.drift_pct);
            const absSorted = raw.map(Math.abs).sort((a, b) => a - b);
            const p99 = absSorted[Math.min(absSorted.length - 1, Math.floor(absSorted.length * 0.99))];
            // Clamp the linear axis so the ±15% warn band stays visible; values
            // beyond the clamp get an off-chart cap triangle. (drift_pct blows up
            // when prompt_tokens_actual is small, so a few iters can reach ±2000%.)
            const CLAMP = Math.min(200, Math.max(15, p99 || 15));
            const useLog = this.driftLog;
            // y transform: linear within ±CLAMP (capped), or signed-log
            const toY = (v) => {
                let nv;
                if (useLog) {
                    const s = Math.sign(v); const m = Math.log10(1 + Math.abs(v));
                    nv = s * m;
                } else {
                    nv = Math.max(-CLAMP, Math.min(CLAMP, v));
                }
                return pad.t + plotH / 2 - (nv / yScale) * (plotH / 2);
            };
            const yScale = useLog ? Math.log10(1 + CLAMP) : CLAMP;

            // ±15% warn band (only the in-range portion)
            const y15 = toY(15), yN15 = toY(-15);
            ctx.fillStyle = PALETTE.warnBand;
            ctx.fillRect(pad.l, Math.min(y15, yN15), w - pad.l - pad.r, Math.abs(y15 - yN15));
            // zero line
            ctx.strokeStyle = t.border; ctx.setLineDash([3, 3]);
            const y0 = toY(0);
            ctx.beginPath(); ctx.moveTo(pad.l, y0); ctx.lineTo(w - pad.r, y0); ctx.stroke();
            ctx.setLineDash([]);

            // y labels
            ctx.fillStyle = t.dim; ctx.font = "10px " + (getComputedStyle(document.body).fontFamily);
            ctx.textAlign = "right"; ctx.textBaseline = "middle";
            const ticks = useLog ? [-CLAMP, -15, 0, 15, CLAMP] : [-CLAMP, -15, 0, 15, CLAMP];
            for (const v of ticks) ctx.fillText((v > 0 ? "+" : "") + Math.round(v) + "%", pad.l - 4, toY(v));
            // unit caption
            ctx.textAlign = "left"; ctx.textBaseline = "top";
            ctx.fillText(useLog ? "drift % (signed log)" : "drift %", pad.l + 2, 2);
            // x labels
            ctx.textAlign = "center"; ctx.textBaseline = "top";
            const want = n > 60 ? 7 : (n > 20 ? 5 : 3);
            for (let k = 0; k < want; k++) {
                const idx = Math.round((k / Math.max(1, want - 1)) * (n - 1));
                ctx.fillText("iter " + drift[idx].iter, this._iterX(idx, n, pad, w), h - pad.b + 4);
            }

            // drift line (with off-chart caps when linear & clamped)
            ctx.strokeStyle = PALETTE.drift; ctx.lineWidth = 1.8;
            ctx.beginPath();
            for (let i = 0; i < n; i++) {
                const x = this._iterX(i, n, pad, w);
                const y = toY(raw[i]);
                if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
            }
            ctx.stroke();
            // cap triangles for clamped values
            if (!useLog) {
                ctx.fillStyle = PALETTE.drift;
                for (let i = 0; i < n; i++) {
                    if (Math.abs(raw[i]) > CLAMP) {
                        const x = this._iterX(i, n, pad, w);
                        const top = raw[i] > 0;
                        const yy = top ? pad.t + 3 : h - pad.b - 3;
                        ctx.beginPath();
                        ctx.moveTo(x, yy);
                        ctx.lineTo(x - 3, yy + (top ? 6 : -6));
                        ctx.lineTo(x + 3, yy + (top ? 6 : -6));
                        ctx.closePath(); ctx.fill();
                    }
                }
            }

            this._runMarks(ctx, w, h, pad, drift);

            // legend
            ctx.font = "10px " + (getComputedStyle(document.body).fontFamily);
            ctx.textAlign = "right"; ctx.textBaseline = "top";
            ctx.fillStyle = PALETTE.drift; ctx.fillText("● drift", w - pad.r - 2, 2);
            ctx.textAlign = "left";
            ctx.fillStyle = "rgba(220,160,60,0.9)"; ctx.fillRect(pad.l + 2, h - pad.b - 14, 8, 8);
            ctx.fillStyle = t.dim; ctx.fillText("±15% warn", pad.l + 12, h - pad.b - 16);

            // selected + hover
            if (this.hoverRef === "driftCanvas" && this.hoverIdx != null) {
                const it = drift[this.hoverIdx];
                this._drawHover(ctx, w, h, pad, n, this.hoverIdx, it.drift_pct + "% drift");
            }
            this._selectedMark(ctx, w, h, pad, n, drift);
        },

        _compactionMarks(ctx, w, h, pad, n) {
            const comps = this.series.compaction_timeline || [];
            if (!comps.length || !n) return;
            ctx.fillStyle = PALETTE.compaction;
            for (const c of comps) {
                const idx = (this.series.context || []).findIndex(d => d.iter === c.iter);
                if (idx < 0) continue;
                const x = this._iterX(idx, n, pad, w);
                ctx.fillRect(x - 1, pad.t, 2, h - pad.t - pad.b);
            }
        },

        renderReads() {
            const s = this._setupCanvas("readCanvas"); if (!s) return;
            const { ctx, w, h } = s; const t = this._theme();
            ctx.clearRect(0, 0, w, h);
            const iters = this.iters || [];
            const n = iters.length;
            // Only genuine read tools, keyed by tool + path (a path can be read
            // by more than one tool). Writes carry paths too — exclude them.
            const tools = (this.tools || []).filter(t => READ_TOOLS.has(t.name) && t.path);
            if (!n || !tools.length) { this._empty(ctx, w, h, "no read-tool calls with a path"); return; }
            // unique (tool,path) rows in first-seen order
            const rows = []; const seen = {};
            for (const tool of tools) {
                const k = tool.name + "|" + tool.path;
                if (!seen[k]) { seen[k] = 1; rows.push({ name: tool.name, path: tool.path }); }
            }
            const pad = { l: 170, r: 12, t: 10, b: 18 };
            const plotH = h - pad.t - pad.b;
            const cellW = (w - pad.l - pad.r) / Math.max(1, n);
            // cap rows to what fits; floor rowH at 6px; note overflow
            let rowH = Math.min(16, plotH / Math.max(1, rows.length));
            rowH = Math.max(6, rowH);
            const maxRows = Math.floor(plotH / rowH);
            const shownRows = rows.slice(0, maxRows);
            const overflow = rows.length - shownRows.length;

            const redundant = new Set((this.series.redundant_reads || []).map(r => r.iter + "|" + r.path));
            const byRow = {};
            for (const tool of tools) {
                const k = tool.name + "|" + tool.path;
                (byRow[k] = byRow[k] || {})[tool.iter] = tool;
            }
            ctx.font = "10px " + (getComputedStyle(document.body).fontFamily);
            ctx.textBaseline = "middle";
            shownRows.forEach((r, ri) => {
                const y = pad.t + ri * rowH;
                const k = r.name + "|" + r.path;
                const lbl = r.path.length > 26 ? "…" + r.path.slice(-25) : r.path;
                ctx.fillStyle = t.dim; ctx.textAlign = "right";
                ctx.fillText((r.name + " " + lbl), pad.l - 6, y + rowH / 2);
                const cells = byRow[k] || {};
                for (let i = 0; i < n; i++) {
                    const it = iters[i].iter;
                    if (cells[it] !== undefined) {
                        const x = pad.l + i * cellW;
                        const isRed = redundant.has(it + "|" + r.path);
                        ctx.fillStyle = isRed ? PALETTE.redundant : PALETTE.read;
                        ctx.globalAlpha = isRed ? 0.85 : 0.6;
                        ctx.fillRect(x + 0.5, y + 1, Math.max(1.2, cellW - 1), rowH - 2);
                        ctx.globalAlpha = 1;
                    }
                }
            });
            if (overflow > 0) {
                ctx.fillStyle = t.dim; ctx.textAlign = "right"; ctx.textBaseline = "bottom";
                ctx.fillText("+" + overflow + " more paths", pad.l - 6, h - pad.b - 2);
            }
            // x labels
            ctx.fillStyle = t.dim; ctx.textAlign = "center"; ctx.textBaseline = "top";
            const want = n > 60 ? 7 : (n > 20 ? 5 : 3);
            for (let k = 0; k < want; k++) {
                const idx = Math.round((k / Math.max(1, want - 1)) * (n - 1));
                ctx.fillText("iter " + iters[idx].iter, pad.l + idx * cellW + cellW / 2, h - pad.b + 2);
            }
            this._runMarks(ctx, w, h, pad, iters);
            // legend
            ctx.textAlign = "left"; ctx.textBaseline = "middle";
            ctx.fillStyle = PALETTE.read; ctx.globalAlpha = 0.6; ctx.fillRect(8, 8, 9, 9); ctx.globalAlpha = 1;
            ctx.fillStyle = t.dim; ctx.fillText("read", 20, 13);
            ctx.fillStyle = PALETTE.redundant; ctx.fillRect(54, 8, 9, 9);
            ctx.fillStyle = t.dim; ctx.fillText("redundant re-read", 66, 13);
        },

        renderSub() {
            const s = this._setupCanvas("subCanvas"); if (!s) return;
            const { ctx, w, h } = s; const t = this._theme();
            ctx.clearRect(0, 0, w, h);
            const sa = this.series.subagent_timeline || [];
            const n = sa.length;
            const anyActive = sa.some(d => d.active > 0 || d.stuck || d.stall);
            if (!n || !anyActive) { this._empty(ctx, w, h, "no subagents spawned this run"); return; }
            const pad = { l: 56, r: 12, t: 14, b: 18 };
            const maxActive = Math.max(1, ...sa.map(d => d.active));
            const plotH = h - pad.t - pad.b;
            this._drawAxes(ctx, w, h, pad, { min: 0, max: maxActive }, { xs: sa.map(d => d.iter), unit: "active" });
            ctx.fillStyle = PALETTE.subBar;
            const barW = Math.max(2, (w - pad.l - pad.r) / n - 2);
            for (let i = 0; i < n; i++) {
                const d = sa[i];
                if (d.active <= 0) continue;
                const x = this._iterX(i, n, pad, w) - barW / 2;
                const bh = (d.active / maxActive) * plotH;
                ctx.fillStyle = PALETTE.subBar;
                ctx.fillRect(x, pad.t + plotH - bh, barW, bh);
                // stuck + stall as two stacked caps so both are visible
                if (d.stuck) {
                    ctx.fillStyle = PALETTE.stuck;
                    ctx.fillRect(x, pad.t + plotH - bh - 3, barW, 3);
                }
                if (d.stall) {
                    ctx.fillStyle = PALETTE.stall;
                    ctx.fillRect(x, pad.t + plotH - bh - (d.stuck ? 6 : 3), barW, 3);
                }
            }
            this._runMarks(ctx, w, h, pad, sa);
            // legend
            ctx.font = "10px " + (getComputedStyle(document.body).fontFamily);
            ctx.textAlign = "left"; ctx.textBaseline = "top";
            ctx.fillStyle = PALETTE.subBar; ctx.fillText("● active", w - pad.r - 130, 2);
            ctx.fillStyle = PALETTE.stuck; ctx.fillText("■ stuck", w - pad.r - 70, 2);
            ctx.fillStyle = PALETTE.stall; ctx.fillText("■ stall", w - pad.r - 24, 2);
        },

        renderMem() {
            const s = this._setupCanvas("memCanvas"); if (!s) return;
            const { ctx, w, h } = s; const t = this._theme();
            ctx.clearRect(0, 0, w, h);
            const mem = this.series.memory_series || [];
            const n = mem.length;
            if (!n) { this._empty(ctx, w, h, "no memory data"); return; }
            const pad = { l: 56, r: 12, t: 14, b: 18 };
            const counts = mem.map(d => Math.max(d.task_memory_count, d.scratchpad_count));
            // include by_status stack peaks in the y max so the stack doesn't overflow
            const statusKeys = this._statusKeys(mem);
            const stackPeaks = mem.map(d => statusKeys.reduce((a, k) => a + ((d.by_status || {})[k] || 0), 0));
            const max = Math.max(1, ...counts, ...stackPeaks) * 1.1;
            const plotH = h - pad.t - pad.b;
            this._drawAxes(ctx, w, h, pad, { min: 0, max }, { xs: mem.map(d => d.iter), unit: "count" });

            // stacked by_status area (faint, behind the count lines)
            const stackTop = new Array(n).fill(0);
            for (const k of statusKeys) {
                const vals = mem.map(d => (d.by_status || {})[k] || 0);
                const col = PALETTE.status[k] || PALETTE.statusDefault;
                ctx.fillStyle = col; ctx.globalAlpha = 0.22;
                ctx.beginPath();
                for (let i = 0; i < n; i++) {
                    const x = this._iterX(i, n, pad, w);
                    const y = pad.t + plotH - ((stackTop[i] + vals[i]) / max) * plotH;
                    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
                }
                for (let i = n - 1; i >= 0; i--) {
                    const x = this._iterX(i, n, pad, w);
                    const y = pad.t + plotH - (stackTop[i] / max) * plotH;
                    ctx.lineTo(x, y);
                }
                ctx.closePath(); ctx.fill();
                for (let i = 0; i < n; i++) stackTop[i] += vals[i];
                ctx.globalAlpha = 1;
            }

            // count lines (scratchpad dashed to match its legend glyph)
            const line = (key, color, dash) => {
                ctx.strokeStyle = color; ctx.lineWidth = 1.6; ctx.setLineDash(dash || []);
                ctx.beginPath();
                for (let i = 0; i < n; i++) {
                    const x = this._iterX(i, n, pad, w);
                    const y = pad.t + plotH - (mem[i][key] / max) * plotH;
                    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
                }
                ctx.stroke(); ctx.setLineDash([]);
            };
            line("task_memory_count", PALETTE.taskMem, []);
            line("scratchpad_count", PALETTE.scratchpad, [4, 3]);

            this._runMarks(ctx, w, h, pad, mem);

            // legend (counts + status)
            ctx.font = "10px " + (getComputedStyle(document.body).fontFamily);
            ctx.textAlign = "left"; ctx.textBaseline = "top";
            let lx = w - pad.r - 150;
            ctx.fillStyle = PALETTE.taskMem; ctx.fillText("● task_memory", lx, 2); lx += 78;
            ctx.fillStyle = PALETTE.scratchpad; ctx.fillText("┄ scratchpad", lx, 2);
            // status legend along the bottom-left
            let sx = pad.l + 2; const sy = h - pad.b - 12;
            for (const k of statusKeys) {
                const col = PALETTE.status[k] || PALETTE.statusDefault;
                ctx.globalAlpha = 0.5; ctx.fillStyle = col; ctx.fillRect(sx, sy, 8, 8); ctx.globalAlpha = 1;
                ctx.fillStyle = t.dim; ctx.fillText(k, sx + 11, sy - 1);
                sx += 11 + ctx.measureText(k).width + 12;
            }
        },

        _statusKeys(mem) {
            const s = {}; for (const d of mem) for (const k of Object.keys(d.by_status || {})) s[k] = 1;
            // stable, conventional order
            const order = ["active", "pending", "stale", "done", "pruned"];
            return order.filter(k => s[k]).concat(Object.keys(s).filter(k => !order.includes(k)).sort());
        },

        renderTokens() {
            const s = this._setupCanvas("tokCanvas"); if (!s) return;
            const { ctx, w, h } = s; const t = this._theme();
            ctx.clearRect(0, 0, w, h);
            this._curRef = "tokCanvas";
            const tk = this.series.tokens || [];
            const n = tk.length;
            if (!n) { this._empty(ctx, w, h, "no token data"); return; }
            const pad = { l: 56, r: 12, t: 14, b: 20 };
            const max = Math.max(1, ...tk.map(d => Math.max(d.in, d.out, d.cached, d.reasoning))) * 1.1;
            const plotH = h - pad.t - pad.b;
            this._drawAxes(ctx, w, h, pad, { min: 0, max }, { xs: tk.map(d => d.iter), unit: "tokens" });
            const line = (key, color) => {
                ctx.strokeStyle = color; ctx.lineWidth = 1.5;
                ctx.beginPath();
                for (let i = 0; i < n; i++) {
                    const x = this._iterX(i, n, pad, w);
                    const y = pad.t + plotH - ((tk[i][key] || 0) / max) * plotH;
                    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
                }
                ctx.stroke();
            };
            line("in", PALETTE.tokIn);
            line("out", PALETTE.tokOut);
            line("cached", PALETTE.tokCached);
            line("reasoning", PALETTE.tokReasoning);
            this._runMarks(ctx, w, h, pad, tk);
            // stacked vertical legend (4 entries × 32px collided)
            ctx.font = "10px " + (getComputedStyle(document.body).fontFamily);
            ctx.textAlign = "left"; ctx.textBaseline = "top";
            const lg = [["in", PALETTE.tokIn], ["out", PALETTE.tokOut], ["cached", PALETTE.tokCached], ["reasoning", PALETTE.tokReasoning]];
            lg.forEach(([k, c], i) => {
                const y = 2 + i * 12;
                ctx.fillStyle = c; ctx.fillText("● " + k, w - pad.r - 70, y);
            });
            if (this.hoverRef === "tokCanvas" && this.hoverIdx != null) {
                const it = tk[this.hoverIdx];
                this._drawHover(ctx, w, h, pad, n, this.hoverIdx, "in " + this.fmtNum(it.in) + " / out " + this.fmtNum(it.out));
            }
        },

        renderLatency() {
            const s = this._setupCanvas("latCanvas"); if (!s) return;
            const { ctx, w, h } = s; const t = this._theme();
            ctx.clearRect(0, 0, w, h);
            this._curRef = "latCanvas";
            const lat = this.series.latency || [];
            const n = lat.length;
            if (!n) { this._empty(ctx, w, h, "no latency data"); return; }
            const pad = { l: 56, r: 12, t: 14, b: 18 };
            const max = Math.max(1, ...lat.map(d => d.wall_ms)) * 1.1;
            const plotH = h - pad.t - pad.b;
            this._drawAxes(ctx, w, h, pad, { min: 0, max }, { xs: lat.map(d => d.iter), unit: "wall ms" });
            const barW = Math.max(2, (w - pad.l - pad.r) / n - 1);
            for (let i = 0; i < n; i++) {
                const x = this._iterX(i, n, pad, w) - barW / 2;
                const bh = (lat[i].wall_ms / max) * plotH;
                ctx.fillStyle = (this.selectedIter === lat[i].iter) ? PALETTE.actual : "rgba(176,172,160,0.6)";
                ctx.fillRect(x, pad.t + plotH - bh, barW, bh);
            }
            this._runMarks(ctx, w, h, pad, lat);
            if (this.hoverRef === "latCanvas" && this.hoverIdx != null) {
                const it = lat[this.hoverIdx];
                this._drawHover(ctx, w, h, pad, n, this.hoverIdx, this.fmtMs(it.wall_ms) + " wall");
            }
        },

        renderTool() {
            const s = this._setupCanvas("toolCanvas"); if (!s) return;
            const { ctx, w, h } = s; const t = this._theme();
            ctx.clearRect(0, 0, w, h);
            const ts = this.selectedToolSeries();
            if (!ts.length) { this._empty(ctx, w, h, this.selectedTool ? "no calls" : "select a tool"); return; }
            const pad = { l: 56, r: 12, t: 14, b: 18 };
            const max = Math.max(1, ...ts.map(d => d.latency_ms)) * 1.1;
            const n = ts.length;
            const plotH = h - pad.t - pad.b;
            this._drawAxes(ctx, w, h, pad, { min: 0, max }, { xs: ts.map(d => d.iter), unit: "ms" });
            const barW = Math.max(2, (w - pad.l - pad.r) / n - 2);
            for (let i = 0; i < n; i++) {
                const x = this._iterX(i, n, pad, w) - barW / 2;
                const bh = (ts[i].latency_ms / max) * plotH;
                ctx.fillStyle = ts[i].ok ? PALETTE.ok : PALETTE.err;
                ctx.fillRect(x, pad.t + plotH - bh, barW, Math.max(1, bh));
                if (ts[i].cache_hit) {
                    ctx.fillStyle = PALETTE.cache;
                    ctx.fillRect(x, pad.t + plotH - bh - 2, barW, 2);
                }
            }
            // legend
            ctx.font = "10px " + (getComputedStyle(document.body).fontFamily);
            ctx.textAlign = "left"; ctx.textBaseline = "top";
            ctx.fillStyle = PALETTE.ok; ctx.fillText("● ok", w - pad.r - 96, 2);
            ctx.fillStyle = PALETTE.err; ctx.fillText("● error", w - pad.r - 60, 2);
            ctx.fillStyle = PALETTE.cache; ctx.fillText("▬ cache hit", w - pad.r - 24, 2);
        },
    };
}