/* Trace Analyzer — Alpine component + custom <canvas> renderers.
 *
 * No charting library: the server returns structured series/snapshot
 * (`/api/traces` + `/api/traces/<id>`), and this file draws every chart with
 * vanilla 2D canvas. Mirrors the codebase's bindCanvas idiom from the memory
 * panel. Clicking an iter in any chart/table scrolls the conversation view to
 * that iteration and highlights it.
 *
 * X axis contract: every chart shares ONE iter→pixel scale (`_xOf`), derived
 * from the shared pan/zoom window [xStart, xStart+xCount) over `iters`. Each
 * visible iter owns an equal-width slot; points/bars/cells sit at the slot
 * centre. Sparse series (efficiency, per-tool latency, snapshot columns) are
 * positioned by their iter number — never stretched to fill the width — so
 * panning and zooming keep all charts in lockstep. Hover is a shared
 * crosshair (`hoverIter`) drawn on every chart; the value chip appears on the
 * hovered one.
 *
 * Navigation: drag to pan · ⇧+drag to zoom a region · ctrl/⌘+scroll or
 * double-click to zoom at the cursor · +/−/all/latest buttons · scrollbar.
 *
 * Each panel has a ⓘ info button (see trace.html) whose popout explains what the
 * panel shows, how to read it, and which harness decision it informs.
 */

// Shared palette — Wash pigments (documentation/visualization_style_guide.md
// "Colour"): categorical series cycle --wash-series-1..7, context layers use
// --wash-layer-*, and semantic marks use the semantic pigment step (ok,
// danger, caution, running, accent). Text uses the ink steps. Canvas cannot
// read var(), so refreshTracePalette() resolves every colour from the current
// theme at draw time; the trace app redraws on `mucli-theme-change`.
const PALETTE = { attribution: {}, status: {}, layers: {} };

// Read a CSS custom property from the document root (current theme).
function traceToken(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// Parse "#rgb", "#rrggbb" or "rgb(a)(r,g,b[,a])" into an [r,g,b] triplet.
function traceRgb(color) {
    const c = String(color || "").trim();
    let m = c.match(/^#([0-9a-f]{3}|[0-9a-f]{6})$/i);
    if (m) {
        let h = m[1];
        if (h.length === 3) h = h.split("").map((ch) => ch + ch).join("");
        return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
    }
    m = c.match(/rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)/i);
    if (m) return [Number(m[1]), Number(m[2]), Number(m[3])];
    return [128, 128, 128];
}

// A token at a given alpha, as a canvas-safe colour string.
function traceAlpha(name, alpha) {
    const [r, g, b] = traceRgb(traceToken(name));
    return `rgba(${r},${g},${b},${alpha})`;
}

// Chart labels use the Wash UI face (Inter), falling back to the body font.
function traceFont() {
    return traceToken("--wash-font-sans") || getComputedStyle(document.body).fontFamily;
}

// Legend entry: the glyph carries the series pigment, the label stays in the
// ink-3 text step (pigments are not text-safe; style guide "Colour").
function traceLegendText(ctx, color, text, x, y) {
    const glyph = String(text).slice(0, 1), rest = String(text).slice(1);
    const align = ctx.textAlign;
    const gw = ctx.measureText(glyph).width, rw = ctx.measureText(rest).width;
    const x0 = align === "right" || align === "end" ? x - gw - rw : align === "center" ? x - (gw + rw) / 2 : x;
    ctx.textAlign = "left";
    ctx.fillStyle = color; ctx.fillText(glyph, x0, y);
    ctx.fillStyle = traceToken("--wash-ink-3"); ctx.fillText(rest, x0 + gw, y);
    ctx.textAlign = align;
}

function refreshTracePalette() {
    const s = (n) => traceToken("--wash-series-" + n);   // lagoon apricot lilac meadow rose sky sun
    const p = (name) => traceToken("--wash-" + name + "-pigment");
    Object.assign(PALETTE, {
        actual: s(1),             // real prompt tokens
        est: traceToken("--wash-ink-3"),  // harness estimate (dashed, neutral)
        drift: s(3),              // tokenizer drift (the headline line)
        tokIn: s(1),
        tokOut: s(2),
        tokCached: s(6),
        tokReasoning: s(3),
        taskMem: s(4),
        scratchpad: s(2),         // distinct from taskMem
        subBar: s(6),
        read: s(1),
        redundant: p("danger"),
        ok: p("ok"),
        err: p("danger"),
        cache: s(4),
        stuck: p("danger"),
        stall: p("caution"),
        limit: traceAlpha("--wash-danger-pigment", 0.55),
        compaction: traceAlpha("--wash-running-pigment", 0.7),
        warnBand: traceAlpha("--wash-caution-pigment", 0.16),
        warn: traceAlpha("--wash-caution-pigment", 0.9),
        heat: traceRgb(p("accent")),                 // fallback ramp for unknown layers
        driftPos: traceRgb(s(1)),                    // positive drift (est > actual): lagoon
        driftNeg: traceRgb(p("danger")),             // negative drift (est < actual): rose
        savedBand: traceAlpha("--wash-series-2", 0.18),
        injectedFill: traceAlpha("--wash-series-4", 0.32),
        latency: traceAlpha("--wash-series-6", 0.6),
        statusDefault: traceToken("--wash-neutral-pigment"),
    });
    PALETTE.attribution = {
        system: traceToken("--wash-layer-L0"), user: s(1), assistant: s(2),
        tool_calls: s(3), tool_results: s(4),
        files_images: s(5), other: traceToken("--wash-neutral-pigment"), tool_schemas: s(6),
    };
    PALETTE.status = {           // memory by_status colors
        active: p("running"), stale: p("caution"), done: p("ok"),
        pending: traceToken("--wash-plan-pigment"), pruned: traceToken("--wash-neutral-pigment"),
    };
    PALETTE.layers = {};         // heat-strip rows: each layer in its own pigment
    for (const key of ["L0", "L1A", "L1B", "L2", "L3", "L4", "L4B", "L5"]) {
        PALETTE.layers[key.toLowerCase()] = traceRgb(traceToken("--wash-layer-" + key));
    }
    return PALETTE;
}

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
                  memory_series: [], context_attribution: [], top_context_spikes: [],
                  efficiency: [] },
        iters: [],
        tools: [],
        snapshot: { grid: [], drift_strip: [], xs: [], compaction_cols: [],
                    context_actual: [], context_est: [],
                    meta: { layers: [], labels: {}, layer_vmax: {}, n: 0 } },
        loading: false,
        // Round-48 F16: opt-in "all runs" — bounded by default (newest 5).
        allRuns: false,
        selectedIter: null,
        selectedTool: null,
        driftLog: false,        // signed-log y scale for the drift chart
        infoOpen: null,         // open info popout key
        hoverRef: null,         // canvas ref under the cursor (chip owner)
        session: "",            // session scope (?session=) — traces are per-session
        runBounds: [],           // per-run global [start,end] for the combined view
        theme: "paper",         // current Wash theme (paper | dusk)
        expandedTools: {},      // toolKey -> true: that tool's result preview is expanded
        expandedCards: {},      // cardKey -> true: all tool previews in that turn expanded
        VIEW_CAP: 100,          // default iters in view (the "latest" window)
        MIN_VIEW: 3,            // zoom-in floor (iters)
        xStart: 0,              // first iter index visible in the X window
        xCount: 100,            // iters currently visible (MIN_VIEW..totalIters)
        hoverIter: null,        // shared crosshair iter (all charts)
        brush: null,            // {a, b} iter positions of the ⇧+drag zoom selection
        readHover: null,        // {x, y, label} read-map hover tooltip state

        async init() {
            // Session scope: the analyzer is opened from the chat with
            // ?session=<name> and shows a COMBINED view of every run in that
            // session (all of it, chronologically, run boundaries marked),
            // ignoring every other session. No param → fall back to a global
            // per-run picker.
            const qs = new URLSearchParams(window.location.search);
            this.session = (qs.get("session") || "").trim();
            this.theme = window.MuTheme ? window.MuTheme.current() : "paper";
            // The shared top-bar toggle (route_theme.js) switches the theme;
            // canvases cannot inherit CSS variables, so redraw on every change.
            window.addEventListener("mucli-theme-change", (event) => {
                this.theme = (event.detail && event.detail.wash) || "paper";
                this.renderAll();
            });
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
            // Round-47 F15 + Round-48 F16: default fetch is bounded to the
            // NEWEST 5 runs; this.allRuns (user opt-in) widens to every run
            // in the session — the analyzer must not lose access to older
            // runs just because the default payload is bounded.
            this.loading = true;
            this.selectedIter = null;
            this.selectedTool = null;
            try {
                const limit = this.allRuns ? 0 : 5;
                const r = await fetch(
                    "/api/traces/session/" + encodeURIComponent(this.session) + "?cols=2048&limit=" + limit
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
                this._resetWindow();
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

        async clearRun() {
            if (!this.runId || this.session) return;
            if (!window.confirm("Clear this trace run? This deletes the JSONL file.")) return;
            try {
                const r = await fetch("/api/traces/" + encodeURIComponent(this.runId), {
                    method: "DELETE",
                });
                if (!r.ok) { window.alert("Clear failed: " + r.status); return; }
                this.summary = null;
                this.series = {};
                this.iters = [];
                this.tools = [];
                this.snapshot = {};
                this.runId = "";
                await this.loadRuns();
            } catch (e) {
                window.alert("Clear failed: " + e);
            }
        },

        async loadRun() {
            if (!this.runId) { this.summary = null; return; }
            this.loading = true;
            this.selectedIter = null;
            this.selectedTool = null;
            this.runBounds = [];   // single-run view has no run boundaries
            try {
                const r = await fetch("/api/traces/" + encodeURIComponent(this.runId) + "?cols=2048");
                const d = await r.json();
                this.summary = d.summary;
                this.series = d.series;
                this.iters = d.iters || [];
                this.tools = d.tools || [];
                this.snapshot = d.snapshot || this.snapshot;
                this._resetWindow();
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

        // Kept for keyboard/legacy callers: delegate to the shared controller.
        toggleTheme() {
            if (window.MuTheme) window.MuTheme.toggle();
        },

        // ---- helpers ---------------------------------------------------
        fmtNum(v) { v = Number(v || 0); return v >= 1000 ? v.toLocaleString() : String(v); },
        fmtMs(v) { v = Number(v || 0); if (v >= 60000) return (v / 60000).toFixed(1) + "m"; if (v >= 1000) return (v / 1000).toFixed(1) + "s"; return Math.round(v) + "ms"; },
        fmtCost(v) { v = Number(v || 0); return v ? "$" + (v < 0.01 ? v.toFixed(6) : v.toFixed(4)) : "—"; },
        fmtStatus(r) { return r.iters > 0 ? "done" : "—"; },
        // Shared status vocabulary (✓ ok · ● running · ◆ waiting · ✗ failed · ○ pending);
        // the glyph is always rendered next to the status text, never alone.
        statusKind(status) {
            const s = String(status || "").toLowerCase();
            if (!s) return "pending";
            if (/err|fail|crash|abort/.test(s)) return "failed";
            if (/interrupt|stop|cancel|wait/.test(s)) return "waiting";
            if (/run|active|progress/.test(s)) return "active";
            if (/complete|done|ok|success/.test(s)) return "done";
            return "pending";
        },
        statusGlyph(status) {
            return ({done: "✓", active: "●", waiting: "◆", failed: "✗", pending: "○"})[this.statusKind(status)];
        },
        statusTone(status) {
            return ({done: "tone-ok", active: "tone-running", waiting: "tone-warn", failed: "tone-danger", pending: "tone-muted"})[this.statusKind(status)];
        },
        // Wash semantic for the shared vocabulary (data-tone on .w-chip).
        washTone(status) {
            return ({done: "ok", active: "running", waiting: "attention", failed: "danger", pending: "neutral"})[this.statusKind(status)] || "neutral";
        },
        attributionLabel(key) {
            return ({system: "system", user: "user", assistant: "assistant",
                tool_calls: "tool calls", tool_results: "tool results",
                files_images: "files/images", other: "other",
                tool_schemas: "tool schemas"})[key] || key || "other";
        },
        toolHistSorted() { return [...(this.series.tool_histogram || [])].sort((a, b) => b.count - a.count); },
        maxToolCount() { return Math.max(1, ...this.toolHistSorted().map(h => h.count)); },

        // ---- shared X window (pan + zoom) -----------------------------------
        // All per-iter series + the heat grid + the read map are positioned by
        // iter through ONE scale: the window [xStart, xStart+xCount) over
        // `iters` maps to the plot width, one equal slot per visible iter.
        totalIters() { return (this.iters || []).length; },
        _winStart(total) {
            const cnt = Math.min(this.xCount, total);
            return Math.max(0, Math.min(this.xStart, Math.max(0, total - cnt)));
        },
        _winCount(total) { return Math.max(1, Math.min(this.xCount, total)); },
        _win() {
            const total = this.totalIters();
            return { s: this._winStart(total), c: this._winCount(total), total };
        },
        // iter number → position in `iters` (built once per load).
        _posOf(iter) {
            const m = this._iterIndex;
            if (!m) return -1;
            const p = m.get(iter);
            return p == null ? -1 : p;
        },
        _slotW(pad, w) { return (w - pad.l - pad.r) / this._win().c; },
        // pixel x of the slot centre for position `p` (may be off-plot).
        _xAtPos(p, pad, w) { return pad.l + (p - this._win().s + 0.5) * this._slotW(pad, w); },
        // pixel x of an iter; null when unknown, or when outside the window
        // unless `outside` is set (lines use it for the clipped edge segments).
        _xOf(iter, pad, w, outside) {
            const p = this._posOf(iter);
            if (p < 0) return null;
            const { s, c } = this._win();
            if (!outside && (p < s || p >= s + c)) return null;
            return this._xAtPos(p, pad, w);
        },
        // iter under pixel x (slot hit-test); null outside the plot.
        _iterAtX(x, pad, w) {
            const { s, c } = this._win();
            if (x < pad.l || x > w - pad.r) return null;
            const local = Math.max(0, Math.min(c - 1, Math.floor((x - pad.l) / this._slotW(pad, w))));
            const it = (this.iters || [])[s + local];
            return it ? it.iter : null;
        },
        // Visible slice of a series. Full per-iter series slice positionally;
        // sparse series (efficiency, tool latency) filter by iter number.
        // `padOne` includes one neighbour on each side so lines run to the
        // plot edge while panning (drawn under a clip rect).
        _view(arr, padOne) {
            if (!arr || !arr.length) return [];
            const { s, c, total } = this._win();
            const ext = padOne ? 1 : 0;
            const lo = Math.max(0, s - ext), hi = Math.min(total - 1, s + c - 1 + ext);
            if (arr.length === total) return arr.slice(lo, hi + 1);
            const all = this.iters || [];
            const loIt = all[lo] ? all[lo].iter : -Infinity;
            const hiIt = all[hi] ? all[hi].iter : Infinity;
            return arr.filter(d => d.iter != null && d.iter >= loIt && d.iter <= hiIt);
        },
        _clipPlot(ctx, w, h, pad) {
            ctx.save(); ctx.beginPath();
            ctx.rect(pad.l, pad.t, w - pad.l - pad.r, h - pad.t - pad.b); ctx.clip();
        },
        // Trace a path through `data` (any series with `iter`), y from yFn.
        _pathOf(ctx, data, pad, w, yFn) {
            let started = false;
            for (const d of data) {
                const x = this._xOf(d.iter, pad, w, true);
                if (x == null) continue;
                const y = yFn(d);
                if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
            }
            return started;
        },
        _hoverPoint(data) {
            if (this.hoverIter == null) return null;
            return data.find(d => d.iter === this.hoverIter) || null;
        },
        _notePad(ref, pad) { const c = this.$refs[ref]; if (c) c._pad = pad; },
        _padOf(cv) { return cv._pad || { l: 56, r: 12, t: 14, b: 20 }; },
        _resetWindow() {
            const all = this.iters || [];
            this._iterIndex = new Map(all.map((it, i) => [it.iter, i]));
            const total = all.length;
            this.xCount = Math.max(1, Math.min(this.VIEW_CAP, total));
            // Start at the latest iterations (RHS): the most recent work is the
            // default point of interest. When the whole run fits in the window
            // (total ≤ xCount) xStart is 0 and covers everything.
            this.xStart = Math.max(0, total - this.xCount);
            this.brush = null;
            this._updatePanCursors();
        },
        _afterWindowChange() { this._updatePanCursors(); this.renderAll(); },
        xScrollMax() { return Math.max(0, this.totalIters() - this.xCount); },
        // Pannable when the run exceeds the visible window. Dragging any chart
        // shifts the shared window (see _attachPan); all charts redraw together.
        _pannable() { return this.totalIters() > this.xCount; },
        _updatePanCursors() {
            const cur = this._pannable() ? "grab" : "crosshair";
            document.querySelectorAll(".trace-canvas").forEach(cv => { cv.style.cursor = cur; });
        },

        // ---- zoom ------------------------------------------------------------
        canZoomIn() { return this._win().c > this.MIN_VIEW; },
        canZoomOut() { return this.xCount < this.totalIters(); },
        zoomLabel() {
            const { c, total } = this._win();
            if (!total) return "";
            return c >= total ? "all" : Math.round((total / c) * 10) / 10 + "×";
        },
        // Anchor for button zoom: the selected iter when visible, else centre.
        _anchorPos() {
            const { s, c } = this._win();
            if (this.selectedIter !== null) {
                const p = this._posOf(this.selectedIter);
                if (p >= s && p < s + c) return p + 0.5;
            }
            return s + c / 2;
        },
        // Scale the window by `factor` keeping `anchorPos` (a fractional
        // position in `iters`) at the same on-screen fraction.
        zoomBy(factor, anchorPos) {
            const { s, c, total } = this._win();
            if (!total) return;
            let cnt = Math.round(c * factor);
            if (factor < 1 && cnt === c) cnt = c - 1;
            if (factor > 1 && cnt === c) cnt = c + 1;
            cnt = Math.max(Math.min(this.MIN_VIEW, total), Math.min(total, cnt));
            if (cnt === c) return;
            const anchor = anchorPos == null ? s + c / 2 : anchorPos;
            const frac = (anchor - s) / c;
            const ns = Math.round(anchor - frac * cnt);
            this.xCount = cnt;
            this.xStart = Math.max(0, Math.min(ns, total - cnt));
            this._afterWindowChange();
        },
        // Zoom the window to the inclusive position range [a, b].
        zoomToRange(a, b) {
            const total = this.totalIters();
            if (!total) return;
            let lo = Math.max(0, Math.min(a, b)), hi = Math.min(total - 1, Math.max(a, b));
            let cnt = hi - lo + 1;
            const floor = Math.min(this.MIN_VIEW, total);
            if (cnt < floor) { lo = Math.max(0, lo - Math.floor((floor - cnt) / 2)); cnt = floor; }
            this.xCount = Math.min(total, cnt);
            this.xStart = Math.max(0, Math.min(lo, total - this.xCount));
            this._afterWindowChange();
        },
        zoomIn() { this.zoomBy(0.5, this._anchorPos()); },
        zoomOut() { this.zoomBy(2, this._anchorPos()); },
        zoomAll() {
            const total = this.totalIters();
            this.xCount = Math.max(1, total); this.xStart = 0;
            this._afterWindowChange();
        },
        zoomReset() { this._resetWindow(); this.renderAll(); },

        // ---- pan (drag) + brush zoom (⇧+drag) ---------------------------------
        _attachPan(cv) {
            if (cv._panAttached) return;
            cv._panAttached = true;
            cv.addEventListener("mousedown", (e) => this._onPanDown(e, cv));
        },
        _onPanDown(e, cv) {
            if (e.button !== 0) return;
            const rect = cv.getBoundingClientRect();
            const pad = this._padOf(cv), w = cv.clientWidth || cv.width;
            if (e.shiftKey) {
                // Brush: select an iter range to zoom into on release.
                const it = this._iterAtX(e.clientX - rect.left, pad, w);
                if (it == null) return;
                e.preventDefault();
                const p = this._posOf(it);
                this.brush = { a: p, b: p };
                this._brushCV = cv;
                this.hoverRef = null; this.hoverIter = null; this.readHover = null;
                const move = (ev) => this._onBrushMove(ev);
                const up = (ev) => {
                    window.removeEventListener("mousemove", move);
                    window.removeEventListener("mouseup", up);
                    this._onBrushUp(ev);
                };
                window.addEventListener("mousemove", move);
                window.addEventListener("mouseup", up);
                this.renderAll();
                return;
            }
            if (!this._pannable()) return;        // nothing to pan — let click through
            e.preventDefault();
            this._dragCV = cv;
            this._dragStartClientX = e.clientX;
            this._dragStartXStart = this.xStart;
            this._dragMoved = false;
            this.hoverRef = null; this.hoverIter = null; this.readHover = null;  // drop hover chip while panning
            cv.style.cursor = "grabbing";
            // Track on the window so the drag continues even when the cursor
            // leaves the canvas; remove on mouseup to avoid leaks.
            const move = (ev) => this._onPanMove(ev);
            const up = (ev) => {
                window.removeEventListener("mousemove", move);
                window.removeEventListener("mouseup", up);
                this._onPanUp(ev);
            };
            window.addEventListener("mousemove", move);
            window.addEventListener("mouseup", up);
        },
        _onPanMove(ev) {
            if (!this._dragCV) return;
            const cv = this._dragCV;
            const dx = ev.clientX - this._dragStartClientX;
            if (Math.abs(dx) > 4) this._dragMoved = true;   // distinguish drag from click
            // pixels per visible iter — the shared slot width for this canvas.
            const ppi = Math.max(0.5, this._slotW(this._padOf(cv), cv.clientWidth || cv.width));
            const iters = Math.round(dx / ppi);             // drag right → show earlier iters
            const maxStart = this.xScrollMax();
            const ns = Math.max(0, Math.min(this._dragStartXStart - iters, maxStart));
            if (ns !== this.xStart) { this.xStart = ns; this.renderAll(); }
        },
        _onPanUp() {
            this._dragCV = null;
            this._updatePanCursors();
        },
        _onBrushMove(ev) {
            const cv = this._brushCV; if (!cv || !this.brush) return;
            const rect = cv.getBoundingClientRect();
            const pad = this._padOf(cv), w = cv.clientWidth || cv.width;
            const x = Math.max(pad.l, Math.min(w - pad.r, ev.clientX - rect.left));
            const it = this._iterAtX(x, pad, w);
            if (it == null) return;
            const p = this._posOf(it);
            if (p !== this.brush.b) { this.brush = { a: this.brush.a, b: p }; this.renderAll(); }
        },
        _onBrushUp() {
            const b = this.brush; this.brush = null; this._brushCV = null;
            this._dragMoved = true;   // swallow the trailing click
            if (b && b.a !== b.b) this.zoomToRange(b.a, b.b); else this.renderAll();
        },
        _drawBrush(ctx, w, h, pad) {
            const b = this.brush; if (!b) return;
            const { s, c } = this._win();
            const lo = Math.max(s, Math.min(b.a, b.b)), hi = Math.min(s + c - 1, Math.max(b.a, b.b));
            if (hi < lo) return;
            const sw = this._slotW(pad, w);
            const x0 = this._xAtPos(lo, pad, w) - sw / 2, x1 = this._xAtPos(hi, pad, w) + sw / 2;
            const t = this._theme();
            ctx.fillStyle = traceAlpha("--wash-ink-3", 0.16);
            ctx.fillRect(x0, pad.t, x1 - x0, h - pad.t - pad.b);
            ctx.strokeStyle = t.accent; ctx.lineWidth = 1; ctx.setLineDash([]);
            ctx.beginPath(); ctx.moveTo(x0, pad.t); ctx.lineTo(x0, h - pad.b);
            ctx.moveTo(x1, pad.t); ctx.lineTo(x1, h - pad.b); ctx.stroke();
        },
        // Label for the scrollbar: "iters A–B of N" in actual iter numbers.
        xScrollLabel() {
            const all = this.iters || [];
            const n = all.length;
            if (!n) return "";
            const s = this._winStart(n), c = this._winCount(n);
            const a = all[s] ? all[s].iter : "?";
            const b = all[Math.min(n - 1, s + c - 1)] ? all[Math.min(n - 1, s + c - 1)].iter : "?";
            return "iters " + a + "–" + b + " / " + n;
        },
        // Scrollbar thumb: width = window's share of the run, left = its
        // position within the scrollable range. Bound via :style so Alpine
        // re-renders it reactively on every xStart/xCount change.
        thumbStyle() {
            const total = this.totalIters();
            if (!total) return { display: "none" };
            const wPct = (Math.min(this.xCount, total) / total) * 100;
            const max = this.xScrollMax();
            const lPct = max > 0 ? (this.xStart / max) * (100 - wPct) : 0;
            return { width: wPct + "%", left: lPct + "%" };
        },
        // Drag the thumb to pan (same shared xStart as chart drag).
        onScrollThumbDown(e) {
            if (!this._pannable()) return;
            e.preventDefault(); e.stopPropagation();
            const track = this.$refs.xscrollTrack;
            const rect = track.getBoundingClientRect();
            const total = this.totalIters(), max = this.xScrollMax();
            const thumbW = (this.xCount / total) * rect.width;
            const usable = Math.max(1, rect.width - thumbW);   // px the thumb can travel
            const start = this.xStart, startClientX = e.clientX;
            const move = (ev) => {
                const dx = ev.clientX - startClientX;
                const ns = Math.max(0, Math.min(start + Math.round((dx / usable) * max), max));
                if (ns !== this.xStart) { this.xStart = ns; this.renderAll(); }
            };
            const up = () => {
                window.removeEventListener("mousemove", move);
                window.removeEventListener("mouseup", up);
            };
            window.addEventListener("mousemove", move);
            window.addEventListener("mouseup", up);
        },
        // Click the track (not the thumb) to jump — centers the window on
        // the click, like a native scrollbar.
        onScrollTrackClick(e) {
            if (!this._pannable()) return;
            if (e.target.classList && e.target.classList.contains("trace-xscroll-thumb")) return;
            const track = this.$refs.xscrollTrack;
            const rect = track.getBoundingClientRect();
            const frac = (e.clientX - rect.left) / Math.max(1, rect.width);  // 0..1
            const center = Math.round(frac * this.totalIters());
            const max = this.xScrollMax();
            const ns = Math.max(0, Math.min(center - Math.floor(this.xCount / 2), max));
            if (ns !== this.xStart) { this.xStart = ns; this.renderAll(); }
        },
        // Wheel over any chart: horizontal (trackpad deltaX or ⇧+wheel) pans
        // the shared window; ctrl/⌘+wheel zooms at the cursor's iter.
        _attachWheel(cv) {
            if (cv._wheelAttached) return;
            cv._wheelAttached = true;
            cv.addEventListener("wheel", (e) => {
                if (e.ctrlKey || e.metaKey) {
                    if (this.totalIters() <= 1) return;
                    e.preventDefault();
                    const rect = cv.getBoundingClientRect();
                    const pad = this._padOf(cv), w = cv.clientWidth || cv.width;
                    const it = this._iterAtX(e.clientX - rect.left, pad, w);
                    const anchor = it != null ? this._posOf(it) + 0.5 : null;
                    this.zoomBy(e.deltaY < 0 ? 0.8 : 1.25, anchor);
                    return;
                }
                if (!this._pannable()) return;
                const dx = e.deltaX || (e.shiftKey ? e.deltaY : 0);
                if (!dx) return;
                e.preventDefault();
                const step = Math.max(1, Math.round(this.xCount * 0.1));
                const max = this.xScrollMax();
                const ns = Math.max(0, Math.min(this.xStart + (dx > 0 ? step : -step), max));
                if (ns !== this.xStart) { this.xStart = ns; this.renderAll(); }
            }, { passive: false });
        },
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
            // Pan the X window to include the selected iter so chart clicks
            // on off-screen iters bring them into view.
            const idx = this._posOf(it);
            const { s, c } = this._win();
            if (idx >= 0 && (idx < s || idx >= s + c)) {
                const maxStart = this.xScrollMax();
                this.xStart = Math.max(0, Math.min(idx - Math.floor(c / 2), maxStart));
            }
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

        // Draw run-boundary dividers on a per-iter chart: each run's
        // start_iter (a global iter in the combined view) through the shared scale.
        _runMarks(ctx, w, h, pad) {
            if (!this.runBounds || this.runBounds.length < 2) return;
            const t = this._theme();
            ctx.font = "9px " + traceFont();
            for (let bi = 1; bi < this.runBounds.length; bi++) {
                const x = this._xOf(this.runBounds[bi].start_iter, pad, w);
                if (x == null) continue;
                ctx.strokeStyle = traceAlpha("--wash-ink-3", 0.22);
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
            refreshTracePalette();
            return {
                text: traceToken("--wash-ink"),
                dim: traceToken("--wash-ink-3"),
                border: traceToken("--wash-hairline"),
                accent: traceToken("--wash-accent-pigment"),
                bg: traceToken("--wash-surface"),
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
            c._ref = ref;
            return { ctx, w, h };
        },

        _drawAxes(ctx, w, h, pad, ys, opts = {}) {
            const t = this._theme();
            ctx.strokeStyle = t.border; ctx.fillStyle = t.dim;
            ctx.font = "10px " + traceFont();
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
            this._drawXLabels(ctx, w, h, pad, 4);
        },

        // X labels from the shared window: evenly spaced visible iters at
        // their slot centres, density adapting to the plot width + zoom.
        _drawXLabels(ctx, w, h, pad, yOff) {
            const t = this._theme();
            const { s, c } = this._win();
            const all = this.iters || [];
            if (!all.length) return;
            const sw = this._slotW(pad, w);
            ctx.fillStyle = t.dim; ctx.font = "10px " + traceFont();
            ctx.textAlign = "center"; ctx.textBaseline = "top";
            const want = Math.max(1, Math.min(c, Math.floor((w - pad.l - pad.r) / 64)));
            const used = new Set();
            for (let k = 0; k < want; k++) {
                const local = want === 1 ? Math.floor((c - 1) / 2) : Math.round((k / (want - 1)) * (c - 1));
                if (used.has(local)) continue;
                used.add(local);
                const it = all[s + local];
                if (!it) continue;
                // keep the (centred) label inside the canvas at both edges
                const half = ctx.measureText("iter " + it.iter).width / 2 + 2;
                const lx = Math.min(Math.max(pad.l + (local + 0.5) * sw, half), w - half);
                ctx.fillText("iter " + it.iter, lx, h - pad.b + (yOff == null ? 4 : yOff));
            }
        },

        _empty(ctx, w, h, msg) {
            const t = this._theme();
            ctx.fillStyle = t.dim; ctx.font = "11px " + traceFont();
            ctx.textAlign = "left"; ctx.textBaseline = "top";
            ctx.fillText(msg, 8, 12);
        },

        // ---- hover (shared crosshair) ----------------------------------
        // Every canvas reports the iter under the cursor into `hoverIter`;
        // renderAll draws the crosshair on all charts and the value chip on
        // the hovered one. Click jumps the conversation to that iter;
        // double-click zooms in around it.
        attachHovers() {
            if (this._hoverAttached) return;
            this._hoverAttached = true;
            document.querySelectorAll(".trace-canvas").forEach(cv => {
                if (cv === this.$refs.readCanvas) return;   // 2D hover below
                cv.addEventListener("mousemove", (e) => this._onHoverMove(e, cv));
                cv.addEventListener("mouseleave", () => {
                    if (this.hoverRef === cv._ref) { this.hoverRef = null; this.hoverIter = null; this.renderAll(); }
                });
                cv.addEventListener("click", (e) => {
                    // A drag pans / a ⇧+drag brushes; swallow the trailing click
                    // so it doesn't also jump the conversation view.
                    if (this._dragMoved) { this._dragMoved = false; return; }
                    const it = this._hoverIterAt(e, cv);
                    if (it != null) this.selectIter(it);
                });
                cv.addEventListener("dblclick", (e) => {
                    e.preventDefault();
                    const it = this._hoverIterAt(e, cv);
                    this.zoomBy(0.5, it != null ? this._posOf(it) + 0.5 : null);
                });
            });
            // Read-state map uses a 2D (row + col) hover instead of the x-only
            // crosshair: the popup shows the path under the cursor and, when
            // the cursor is on a cell, the iter + read/redundant state.
            const rc = this.$refs.readCanvas;
            if (rc && !rc._readHoverAttached) {
                rc._readHoverAttached = true;
                rc.addEventListener("mousemove", (e) => this._onReadHover(e, rc));
                rc.addEventListener("mouseleave", () => {
                    if (this.hoverRef === "readCanvas") { this.hoverRef = null; this.hoverIter = null; this.readHover = null; this.renderAll(); }
                });
                rc.addEventListener("click", (e) => {
                    if (this._dragMoved) { this._dragMoved = false; return; }
                    const it = this._hoverIterAt(e, rc);
                    if (it != null) this.selectIter(it);
                });
                rc.addEventListener("dblclick", (e) => {
                    e.preventDefault();
                    const it = this._hoverIterAt(e, rc);
                    this.zoomBy(0.5, it != null ? this._posOf(it) + 0.5 : null);
                });
            }
            // Drag-to-pan / ⇧+drag-to-zoom / wheel on every canvas.
            document.querySelectorAll(".trace-canvas").forEach(cv => {
                this._attachPan(cv);
                this._attachWheel(cv);
            });
        },
        _hoverIterAt(e, cv) {
            const rect = cv.getBoundingClientRect();
            return this._iterAtX(e.clientX - rect.left, this._padOf(cv), cv.clientWidth || cv.width);
        },
        _onHoverMove(e, cv) {
            if (this._dragCV || this.brush) return;    // dragging pans/brushes; don't chase hover
            const it = this._hoverIterAt(e, cv);
            if (it == null) {
                if (this.hoverRef === cv._ref) { this.hoverRef = null; this.hoverIter = null; this.renderAll(); }
                return;
            }
            if (it === this.hoverIter && this.hoverRef === cv._ref) return;
            this.hoverRef = cv._ref; this.hoverIter = it; this.renderAll();
        },
        // Crosshair at hoverIter on every chart; the chip only on the hovered
        // one (`_curRef` is set by each renderer). `label` null → line only.
        _drawHover(ctx, w, h, pad, label) {
            if (this.hoverIter == null) return;
            const x = this._xOf(this.hoverIter, pad, w);
            if (x == null) return;
            const mine = this.hoverRef === this._curRef;
            const t = this._theme();
            ctx.strokeStyle = t.accent; ctx.setLineDash([3, 3]); ctx.lineWidth = 1;
            ctx.globalAlpha = mine ? 1 : 0.4;
            ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, h - pad.b); ctx.stroke();
            ctx.globalAlpha = 1; ctx.setLineDash([]);
            if (!mine || !label) return;
            // chip
            ctx.font = "10px " + traceFont();
            const tw = ctx.measureText(label).width + 10;
            const cx = Math.min(Math.max(pad.l + 2, x + 6), w - pad.r - tw - 2);
            ctx.fillStyle = t.bg; ctx.strokeStyle = t.border;
            ctx.fillRect(cx, pad.t + 2, tw, 16);
            ctx.strokeRect(cx, pad.t + 2, tw, 16);
            ctx.fillStyle = t.text; ctx.textAlign = "left"; ctx.textBaseline = "middle";
            ctx.fillText(label, cx + 5, pad.t + 10);
        },
        // Overlays every chart draws last: brush band + crosshair + selection.
        _overlays(ctx, w, h, pad, label) {
            this._drawBrush(ctx, w, h, pad);
            this._drawHover(ctx, w, h, pad, label);
            this._selectedMark(ctx, w, h, pad);
        },

        // ---- renderers -------------------------------------------------
        renderAll() {
            this.renderContext();
            this.renderAttribution();
            this.renderHeatStrip();
            this.renderDrift();
            this.renderReads();
            this.renderSub();
            this.renderMem();
            this.renderTokens();
            this.renderLatency();
            this.renderTool();
            this.renderEfficiency();
        },

        renderAttribution() {
            const s = this._setupCanvas("attrCanvas"); if (!s) return;
            const { ctx, w, h } = s; const t = this._theme();
            ctx.clearRect(0, 0, w, h);
            this._curRef = "attrCanvas";
            const dataAll = this.series.context_attribution || [];
            const data = this._view(dataAll);
            const pad = { l: 56, r: 12, t: 38, b: 20 };
            this._notePad("attrCanvas", pad);
            if (!data.length) { this._empty(ctx, w, h, "no request attribution data"); return; }
            const keys = ["system", "user", "assistant", "tool_calls", "tool_results", "files_images", "other", "tool_schemas"];
            const max = Math.max(1, ...dataAll.map(d => d.total || keys.reduce((sum, key) => sum + (d[key] || 0), 0))) * 1.08;
            const plotH = h - pad.t - pad.b;
            this._drawAxes(ctx, w, h, pad, { min: 0, max }, { unit: "tokens" });
            const slotW = this._slotW(pad, w);
            const barW = Math.max(1, Math.min(28, slotW * 0.8));
            data.forEach((point) => {
                const cx = this._xOf(point.iter, pad, w);
                if (cx == null) return;
                const x = cx - barW / 2;
                let yBottom = h - pad.b;
                for (const key of keys) {
                    const value = point[key] || 0;
                    const height = value / max * plotH;
                    if (height > 0) {
                        ctx.fillStyle = PALETTE.attribution[key];
                        ctx.fillRect(x, yBottom - height, barW, height);
                        yBottom -= height;
                    }
                }
                if ((point.delta || 0) > 0) {
                    const top = h - pad.b - ((point.total || 0) / max * plotH);
                    ctx.fillStyle = point.delta > max * 0.1 ? PALETTE.err : PALETTE.warn;
                    ctx.beginPath(); ctx.moveTo(x + barW / 2, Math.max(pad.t + 2, top - 7));
                    ctx.lineTo(x + barW / 2 - 3, Math.max(pad.t + 8, top - 1));
                    ctx.lineTo(x + barW / 2 + 3, Math.max(pad.t + 8, top - 1));
                    ctx.closePath(); ctx.fill();
                }
            });
            this._runMarks(ctx, w, h, pad);
            ctx.font = "9px " + traceFont();
            ctx.textAlign = "left"; ctx.textBaseline = "top";
            let lx = pad.l + 2, ly = 3;
            for (const key of keys) {
                const label = this.attributionLabel(key);
                const width = ctx.measureText(label).width + 17;
                if (lx + width > w - pad.r) { lx = pad.l + 2; ly += 13; }
                ctx.fillStyle = PALETTE.attribution[key]; ctx.fillRect(lx, ly + 2, 7, 7);
                ctx.fillStyle = t.dim; ctx.fillText(label, lx + 10, ly);
                lx += width;
            }
            const hp = this._hoverPoint(data);
            this._overlays(ctx, w, h, pad, hp
                ? this.fmtNum(hp.total) + " request · " + (hp.delta >= 0 ? "+" : "") + this.fmtNum(hp.delta) + " · " + this.attributionLabel(hp.growth_source)
                : "iter " + this.hoverIter + " · no request");
        },

        renderContext() {
            const s = this._setupCanvas("ctxCanvas"); if (!s) return;
            const { ctx, w, h } = s; const t = this._theme();
            const pad = { l: 56, r: 12, t: 14, b: 20 };
            this._notePad("ctxCanvas", pad);
            ctx.clearRect(0, 0, w, h);
            this._curRef = "ctxCanvas";
            const ctxAll = this.series.context || [];
            const ctxData = this._view(ctxAll, true);
            if (!ctxData.length) { this._empty(ctx, w, h, "no context data"); return; }
            // The solid line is the provider's *reported* prompt size
            // (`actual`) — the ground truth, which can never exceed the model's
            // context window. The old code plotted the drift-corrected
            // `real_est` (cl100k × effective_drift_ratio, ×2.5 for Ollama) as
            // "real", which is a *conservative compaction guardrail*, not a
            // measurement: whenever the cl100k estimate overshot the
            // drift-corrected ceiling (emergency compaction territory) it
            // extrapolated to ~2× the raw window — an impossible "real prompt
            // size" that read as "reporting double the max context size."
            // `actual` for frontier providers is the true full prompt; for
            // Ollama it is the non-cached prompt delta (near-zero in a warm
            // loop = cache hit), shown honestly here. The drift-corrected
            // estimate is still surfaced in the hover tooltip + the dedicated
            // drift chart. `total_est` is the raw cl100k estimate.
            // y-scale is from the FULL series so it stays stable as you pan.
            const maxAll = ctxAll.flatMap(d => [d.actual, d.total_est]);
            const max = Math.max(1, ...maxAll) * 1.08;
            this._drawAxes(ctx, w, h, pad, { min: 0, max }, { unit: "tokens" });

            const plotH = h - pad.t - pad.b;

            // actual (solid, provider-reported) + total_est (dashed, cl100k)
            this._clipPlot(ctx, w, h, pad);
            const line = (key, color, dash) => {
                ctx.strokeStyle = color; ctx.lineWidth = 1.6; ctx.setLineDash(dash || []);
                ctx.beginPath();
                this._pathOf(ctx, ctxData, pad, w, d => pad.t + plotH - ((d[key] || 0) / max) * plotH);
                ctx.stroke(); ctx.setLineDash([]);
            };
            line("actual", PALETTE.actual, []);
            line("total_est", PALETTE.est, [4, 3]);
            ctx.restore();

            this._compactionMarks(ctx, w, h, pad);
            this._runMarks(ctx, w, h, pad);

            // legend
            ctx.font = "10px " + traceFont();
            ctx.textAlign = "left"; ctx.textBaseline = "top";
            traceLegendText(ctx, PALETTE.actual, "● actual (provider)", w - pad.r - 168, 4);
            traceLegendText(ctx, PALETTE.est, "┄ cl100k est", w - pad.r - 80, 4);

            // hover + selected
            const hp = this._hoverPoint(ctxData);
            this._overlays(ctx, w, h, pad, hp
                ? this.fmtNum(hp.actual) + " actual · " + this.fmtNum(hp.total_est) + " est · "
                    + this.fmtNum(hp.real_est) + " real_est" + (hp.drift_ratio ? (" · drift ×" + hp.drift_ratio) : "")
                : "iter " + this.hoverIter);
        },

        renderHeatStrip() {
            const s = this._setupCanvas("heatCanvas"); if (!s) return;
            const { ctx, w, h } = s; const t = this._theme();
            ctx.clearRect(0, 0, w, h);
            this._curRef = "heatCanvas";
            const snap = this.snapshot || {};
            const meta = snap.meta || {};
            const layers = meta.layers || [];
            const labels = meta.labels || {};
            const xsAll = snap.xs || [];
            const gridAll = snap.grid || [];
            const pad = { l: 96, r: 12, t: 6, b: 16 };
            this._notePad("heatCanvas", pad);
            if (!gridAll.length || !layers.length || !xsAll.length) { this._empty(ctx, w, h, "no layer data"); return; }
            // The snapshot is downsampled to `cols` columns server-side (one
            // per iter up to the 2048 requested, a stride of several iters
            // beyond that). Each
            // column spans from its own iter up to the next column's iter
            // through the SHARED scale, so the strip lines up with the line
            // charts at every zoom level instead of stretching its own
            // columns across the width.
            const { s: ws, c: wc } = this._win();
            const sw = this._slotW(pad, w);
            const spans = [];   // {ci, x0, x1} for columns overlapping the window
            for (let ci = 0; ci < xsAll.length; ci++) {
                const p0 = this._posOf(xsAll[ci]);
                if (p0 < 0) continue;
                const p1 = ci + 1 < xsAll.length ? this._posOf(xsAll[ci + 1]) : p0 + 1;
                const pEnd = p1 > p0 ? p1 : p0 + 1;
                if (pEnd <= ws || p0 >= ws + wc) continue;
                const x0 = this._xAtPos(Math.max(p0, ws), pad, w) - sw / 2;
                const x1 = this._xAtPos(Math.min(pEnd, ws + wc), pad, w) - sw / 2;
                spans.push({ ci, x0, x1: Math.max(x1, x0 + 0.6) });
            }
            if (!spans.length) { this._empty(ctx, w, h, "no layer data in view"); return; }

            const driftH = 14, gap = 3;
            const rowsH = h - pad.t - pad.b - driftH - gap;
            const rowH = rowsH / layers.length;

            // layer heat rows
            ctx.font = "10px " + traceFont();
            ctx.textBaseline = "middle";
            this._clipPlot(ctx, w, h, pad);
            layers.forEach((key, ri) => {
                const y = pad.t + ri * rowH;
                const row = gridAll[ri] || [];
                for (const sp of spans) {
                    const v = row[sp.ci] || 0;
                    if (v > 0) {
                        const a = 0.06 + (v / 255) * 0.9;
                        const rgb = PALETTE.layers[String(key).toLowerCase()] || PALETTE.heat;
                        ctx.fillStyle = `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${a})`;
                        ctx.fillRect(sp.x0, y + 0.5, Math.max(0.6, sp.x1 - sp.x0 - 0.4), rowH - 1);
                    }
                }
            });
            // drift strip
            const dy = pad.t + rowsH + gap;
            for (const sp of spans) {
                const v = (snap.drift_strip || [])[sp.ci] || 128;
                let fill;
                if (v >= 128) {
                    const a = (v - 128) / 128;
                    fill = `rgba(${PALETTE.driftPos[0]},${PALETTE.driftPos[1]},${PALETTE.driftPos[2]},${a * 0.9})`;
                } else {
                    const a = (128 - v) / 128;
                    fill = `rgba(${PALETTE.driftNeg[0]},${PALETTE.driftNeg[1]},${PALETTE.driftNeg[2]},${a * 0.9})`;
                }
                ctx.fillStyle = fill;
                ctx.fillRect(sp.x0, dy, Math.max(0.6, sp.x1 - sp.x0 - 0.4), driftH);
            }
            // compaction column markers
            ctx.fillStyle = PALETTE.compaction;
            for (const ci of (snap.compaction_cols || [])) {
                const sp = spans.find(q => q.ci === ci);
                if (!sp) continue;
                ctx.fillRect((sp.x0 + sp.x1) / 2 - 1, pad.t, 2, rowsH + gap + driftH);
            }
            ctx.restore();
            // row labels
            layers.forEach((key, ri) => {
                ctx.fillStyle = t.dim; ctx.textAlign = "right"; ctx.textBaseline = "middle";
                ctx.fillText(labels[key] || key, pad.l - 6, pad.t + ri * rowH + rowH / 2);
            });
            ctx.fillStyle = t.dim; ctx.textAlign = "right"; ctx.textBaseline = "middle";
            ctx.fillText("drift", pad.l - 6, dy + driftH / 2);
            // selected-iter column highlight
            if (this.selectedIter !== null) {
                const x = this._xOf(this.selectedIter, pad, w);
                if (x != null) {
                    ctx.strokeStyle = t.accent; ctx.lineWidth = 1;
                    ctx.strokeRect(x - sw / 2, pad.t - 1, sw, rowsH + gap + driftH);
                }
            }
            this._drawXLabels(ctx, w, h, pad, 2);
            this._drawBrush(ctx, w, h, pad);
            this._drawHover(ctx, w, h, pad, this.hoverIter != null ? "iter " + this.hoverIter : null);
        },

        _selectedMark(ctx, w, h, pad) {
            if (this.selectedIter === null) return;
            const x = this._xOf(this.selectedIter, pad, w);
            if (x == null) return;
            const t = this._theme();
            ctx.strokeStyle = t.accent; ctx.setLineDash([2, 2]); ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, h - pad.b); ctx.stroke();
            ctx.setLineDash([]);
        },

        renderDrift() {
            const s = this._setupCanvas("driftCanvas"); if (!s) return;
            const { ctx, w, h } = s; const t = this._theme();
            const pad = { l: 56, r: 12, t: 14, b: 20 };
            this._notePad("driftCanvas", pad);
            ctx.clearRect(0, 0, w, h);
            this._curRef = "driftCanvas";
            const driftAll = this.series.drift || [];
            const drift = this._view(driftAll, true);
            if (!drift.length) { this._empty(ctx, w, h, "no drift data"); return; }
            const plotH = h - pad.t - pad.b;

            // raw values (window); clamp p99 from the FULL series so the
            // ±warn band + y-scale stay stable as you pan.
            const raw = drift.map(d => d.drift_pct);
            const absAll = driftAll.map(d => Math.abs(d.drift_pct)).sort((a, b) => a - b);
            const p99 = absAll[Math.min(absAll.length - 1, Math.floor(absAll.length * 0.99))];
            // Clamp the linear axis so the ±15% warn band stays visible; values
            // beyond the clamp get an off-chart cap triangle. (drift_pct is now
            // gated to reliable full-prompt readings — Ollama's near-zero cached
            // delta is zeroed instead of producing ±2000% spikes — but a cold
            // cache can still show a large real divergence, so keep the clamp.)
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
            ctx.fillStyle = t.dim; ctx.font = "10px " + traceFont();
            ctx.textAlign = "right"; ctx.textBaseline = "middle";
            const ticks = useLog ? [-CLAMP, -15, 0, 15, CLAMP] : [-CLAMP, -15, 0, 15, CLAMP];
            for (const v of ticks) ctx.fillText((v > 0 ? "+" : "") + Math.round(v) + "%", pad.l - 4, toY(v));
            // unit caption
            ctx.textAlign = "left"; ctx.textBaseline = "top";
            ctx.fillText(useLog ? "drift % (signed log)" : "drift %", pad.l + 2, 2);
            // x labels
            this._drawXLabels(ctx, w, h, pad, 4);

            // drift line (with off-chart caps when linear & clamped)
            this._clipPlot(ctx, w, h, pad);
            ctx.strokeStyle = PALETTE.drift; ctx.lineWidth = 1.8;
            ctx.beginPath();
            this._pathOf(ctx, drift, pad, w, d => toY(d.drift_pct));
            ctx.stroke();
            ctx.restore();
            // cap triangles for clamped values
            if (!useLog) {
                ctx.fillStyle = PALETTE.drift;
                for (let i = 0; i < drift.length; i++) {
                    if (Math.abs(raw[i]) > CLAMP) {
                        const x = this._xOf(drift[i].iter, pad, w);
                        if (x == null) continue;
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

            this._runMarks(ctx, w, h, pad);

            // legend
            ctx.font = "10px " + traceFont();
            ctx.textAlign = "right"; ctx.textBaseline = "top";
            traceLegendText(ctx, PALETTE.drift, "● drift", w - pad.r - 2, 2);
            ctx.textAlign = "left";
            ctx.fillStyle = PALETTE.warn; ctx.fillRect(pad.l + 2, h - pad.b - 14, 8, 8);
            ctx.fillStyle = t.dim; ctx.fillText("±15% warn", pad.l + 12, h - pad.b - 16);

            // selected + hover
            const hp = this._hoverPoint(drift);
            this._overlays(ctx, w, h, pad, hp ? hp.drift_pct + "% drift" : "iter " + this.hoverIter);
        },

        _compactionMarks(ctx, w, h, pad) {
            const comps = this.series.compaction_timeline || [];
            if (!comps.length) return;
            ctx.fillStyle = PALETTE.compaction;
            for (const c of comps) {
                const x = this._xOf(c.iter, pad, w);
                if (x == null) continue;
                ctx.fillRect(x - 1, pad.t, 2, h - pad.t - pad.b);
            }
        },

        renderReads() {
            const s = this._setupCanvas("readCanvas"); if (!s) return;
            const { ctx, w, h } = s; const t = this._theme();
            ctx.clearRect(0, 0, w, h);
            this._curRef = "readCanvas";
            const itersAll = this.iters || [];
            const iters = this._view(itersAll);
            const n = iters.length;
            const pad = { l: 12, r: 12, t: 10, b: 18 };
            this._notePad("readCanvas", pad);
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
            // No Y-axis filename labels: the concern is *duplicate* reads (red
            // cells), which the grid makes visible without per-row text. The
            // full path + read/redundant state pops up on hover (see _onReadHover).
            const plotH = h - pad.t - pad.b;
            const cellW = this._slotW(pad, w);
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
            // publish geometry for the hover handler (row/col hit testing)
            const cv = this.$refs.readCanvas;
            if (cv) cv._geom = { pad, rowH, rows: shownRows, byRow, redundant, w, h };

            ctx.font = "10px " + traceFont();
            ctx.textBaseline = "middle";
            shownRows.forEach((r, ri) => {
                const y = pad.t + ri * rowH;
                const k = r.name + "|" + r.path;
                const cells = byRow[k] || {};
                for (let i = 0; i < n; i++) {
                    const it = iters[i].iter;
                    if (cells[it] !== undefined) {
                        const cx = this._xOf(it, pad, w);
                        if (cx == null) continue;
                        const x = cx - cellW / 2;
                        const isRed = redundant.has(it + "|" + r.path);
                        ctx.fillStyle = isRed ? PALETTE.redundant : PALETTE.read;
                        ctx.globalAlpha = isRed ? 0.85 : 0.6;
                        ctx.fillRect(x + 0.5, y + 1, Math.max(1.2, cellW - 1), rowH - 2);
                        ctx.globalAlpha = 1;
                    }
                }
            });
            if (overflow > 0) {
                ctx.fillStyle = t.dim; ctx.textAlign = "right"; ctx.textBaseline = "top";
                ctx.fillText("+" + overflow + " more paths (hover the grid to identify a row)", w - pad.r - 2, 2);
            }
            // x labels
            this._drawXLabels(ctx, w, h, pad, 2);
            this._runMarks(ctx, w, h, pad);
            this._drawBrush(ctx, w, h, pad);
            // crosshair from the other charts (the read map's own hover is 2D)
            if (this.hoverRef !== "readCanvas") this._drawHover(ctx, w, h, pad, null);
            this._selectedMark(ctx, w, h, pad);
            // legend
            ctx.textAlign = "left"; ctx.textBaseline = "middle";
            ctx.fillStyle = PALETTE.read; ctx.globalAlpha = 0.6; ctx.fillRect(8, 8, 9, 9); ctx.globalAlpha = 1;
            ctx.fillStyle = t.dim; ctx.fillText("read", 20, 13);
            ctx.fillStyle = PALETTE.redundant; ctx.fillRect(54, 8, 9, 9);
            ctx.fillStyle = t.dim; ctx.fillText("redundant re-read", 66, 13);

            // hover tooltip (filename popup on mouse-over)
            if (this.hoverRef === "readCanvas" && this.readHover && this.readHover.label) {
                const { x, y, label } = this.readHover;
                ctx.font = "10px " + traceFont();
                const tw = ctx.measureText(label).width + 10;
                let cx = x + 10, cy = y - 18;
                if (cx + tw > w - pad.r) cx = x - tw - 10;
                if (cx < pad.l) cx = pad.l;
                if (cy < pad.t) cy = y + 14;
                ctx.fillStyle = t.bg; ctx.strokeStyle = t.border;
                ctx.fillRect(cx, cy, tw, 16); ctx.strokeRect(cx, cy, tw, 16);
                ctx.fillStyle = t.text; ctx.textAlign = "left"; ctx.textBaseline = "middle";
                ctx.fillText(label, cx + 5, cy + 8);
            }
        },

        _onReadHover(e, cv) {
            if (this._dragCV || this.brush) return;    // dragging pans/brushes; don't chase hover
            const g = cv._geom; if (!g) return;
            const rect = cv.getBoundingClientRect();
            const x = e.clientX - rect.left, y = e.clientY - rect.top;
            const plotR = g.w - g.pad.r, plotB = g.h - g.pad.b;
            if (y < g.pad.t || y > plotB || x < g.pad.l || x > plotR) {
                if (this.hoverRef === "readCanvas") { this.readHover = null; this.hoverRef = null; this.hoverIter = null; this.renderAll(); }
                return;
            }
            const it = this._iterAtX(x, g.pad, g.w);
            const row = Math.floor((y - g.pad.t) / g.rowH);
            if (row < 0 || row >= g.rows.length) {
                // Below the rows: keep the shared crosshair, drop the path chip.
                this.hoverRef = "readCanvas"; this.hoverIter = it; this.readHover = null; this.renderAll();
                return;
            }
            const r = g.rows[row];
            const isRed = it != null && g.redundant.has(it + "|" + r.path);
            const cell = it != null ? (g.byRow[r.name + "|" + r.path] || {})[it] : undefined;
            let label = r.name + " " + r.path;
            if (it != null) label += " · iter " + it + (isRed ? " · redundant re-read" : (cell ? " · read" : ""));
            this.hoverRef = "readCanvas";
            this.hoverIter = it;
            this.readHover = { x, y, label };
            this.renderAll();
        },

        renderSub() {
            const s = this._setupCanvas("subCanvas"); if (!s) return;
            const { ctx, w, h } = s; const t = this._theme();
            ctx.clearRect(0, 0, w, h);
            this._curRef = "subCanvas";
            const saAll = this.series.subagent_timeline || [];
            const sa = this._view(saAll);
            const n = sa.length;
            const pad = { l: 56, r: 12, t: 14, b: 18 };
            this._notePad("subCanvas", pad);
            const anyActive = saAll.some(d => d.active > 0 || d.stuck || d.stall);
            if (!n || !anyActive) { this._empty(ctx, w, h, "no subagents spawned this run"); return; }
            const maxActive = Math.max(1, ...saAll.map(d => d.active));
            const plotH = h - pad.t - pad.b;
            this._drawAxes(ctx, w, h, pad, { min: 0, max: maxActive }, { unit: "active" });
            ctx.fillStyle = PALETTE.subBar;
            const barW = Math.max(1.5, Math.min(28, this._slotW(pad, w) - 2));
            for (let i = 0; i < n; i++) {
                const d = sa[i];
                if (d.active <= 0) continue;
                const cx = this._xOf(d.iter, pad, w);
                if (cx == null) continue;
                const x = cx - barW / 2;
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
            this._runMarks(ctx, w, h, pad);
            // legend
            ctx.font = "10px " + traceFont();
            ctx.textAlign = "left"; ctx.textBaseline = "top";
            traceLegendText(ctx, PALETTE.subBar, "● active", w - pad.r - 130, 2);
            traceLegendText(ctx, PALETTE.stuck, "■ stuck", w - pad.r - 70, 2);
            traceLegendText(ctx, PALETTE.stall, "■ stall", w - pad.r - 24, 2);
            const hp = this._hoverPoint(sa);
            this._overlays(ctx, w, h, pad, hp
                ? hp.active + " active" + (hp.stuck ? " · " + hp.stuck + " stuck" : "") + (hp.stall ? " · " + hp.stall + " stall" : "")
                : "iter " + this.hoverIter);
        },

        renderMem() {
            const s = this._setupCanvas("memCanvas"); if (!s) return;
            const { ctx, w, h } = s; const t = this._theme();
            ctx.clearRect(0, 0, w, h);
            this._curRef = "memCanvas";
            const memAll = this.series.memory_series || [];
            const mem = this._view(memAll, true);
            const n = mem.length;
            const pad = { l: 56, r: 12, t: 14, b: 18 };
            this._notePad("memCanvas", pad);
            if (!n) { this._empty(ctx, w, h, "no memory data"); return; }
            const counts = mem.map(d => Math.max(d.task_memory_count, d.scratchpad_count));
            // include by_status stack peaks in the y max so the stack doesn't overflow;
            // status keys from the FULL run so the legend lists every status seen.
            const statusKeys = this._statusKeys(memAll);
            const stackPeaks = memAll.map(d => statusKeys.reduce((a, k) => a + ((d.by_status || {})[k] || 0), 0));
            const max = Math.max(1, ...counts, ...stackPeaks) * 1.1;
            const plotH = h - pad.t - pad.b;
            this._drawAxes(ctx, w, h, pad, { min: 0, max }, { unit: "count" });

            // stacked by_status area (faint, behind the count lines)
            this._clipPlot(ctx, w, h, pad);
            const xs = mem.map(d => this._xOf(d.iter, pad, w, true));
            const stackTop = new Array(n).fill(0);
            for (const k of statusKeys) {
                const vals = mem.map(d => (d.by_status || {})[k] || 0);
                const col = PALETTE.status[k] || PALETTE.statusDefault;
                ctx.fillStyle = col; ctx.globalAlpha = 0.22;
                ctx.beginPath();
                for (let i = 0; i < n; i++) {
                    const y = pad.t + plotH - ((stackTop[i] + vals[i]) / max) * plotH;
                    if (i === 0) ctx.moveTo(xs[i], y); else ctx.lineTo(xs[i], y);
                }
                for (let i = n - 1; i >= 0; i--) {
                    const y = pad.t + plotH - (stackTop[i] / max) * plotH;
                    ctx.lineTo(xs[i], y);
                }
                ctx.closePath(); ctx.fill();
                for (let i = 0; i < n; i++) stackTop[i] += vals[i];
                ctx.globalAlpha = 1;
            }

            // count lines (scratchpad dashed to match its legend glyph)
            const line = (key, color, dash) => {
                ctx.strokeStyle = color; ctx.lineWidth = 1.6; ctx.setLineDash(dash || []);
                ctx.beginPath();
                this._pathOf(ctx, mem, pad, w, d => pad.t + plotH - ((d[key] || 0) / max) * plotH);
                ctx.stroke(); ctx.setLineDash([]);
            };
            line("task_memory_count", PALETTE.taskMem, []);
            line("scratchpad_count", PALETTE.scratchpad, [4, 3]);
            ctx.restore();

            this._runMarks(ctx, w, h, pad);

            // legend (counts + status)
            ctx.font = "10px " + traceFont();
            ctx.textAlign = "left"; ctx.textBaseline = "top";
            let lx = w - pad.r - 150;
            traceLegendText(ctx, PALETTE.taskMem, "● task_memory", lx, 2); lx += 78;
            traceLegendText(ctx, PALETTE.scratchpad, "┄ scratchpad", lx, 2);
            // status legend along the bottom-left
            let sx = pad.l + 2; const sy = h - pad.b - 12;
            for (const k of statusKeys) {
                const col = PALETTE.status[k] || PALETTE.statusDefault;
                ctx.globalAlpha = 0.5; ctx.fillStyle = col; ctx.fillRect(sx, sy, 8, 8); ctx.globalAlpha = 1;
                ctx.fillStyle = t.dim; ctx.fillText(k, sx + 11, sy - 1);
                sx += 11 + ctx.measureText(k).width + 12;
            }
            const hp = this._hoverPoint(mem);
            this._overlays(ctx, w, h, pad, hp
                ? hp.task_memory_count + " task_memory · " + hp.scratchpad_count + " scratchpad"
                : "iter " + this.hoverIter);
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
            const tkAll = this.series.tokens || [];
            const tk = this._view(tkAll, true);
            const n = tk.length;
            const pad = { l: 56, r: 12, t: 14, b: 20 };
            this._notePad("tokCanvas", pad);
            if (!n) { this._empty(ctx, w, h, "no token data"); return; }
            const max = Math.max(1, ...tkAll.map(d => Math.max(d.in, d.out, d.cached, d.reasoning))) * 1.1;
            const plotH = h - pad.t - pad.b;
            this._drawAxes(ctx, w, h, pad, { min: 0, max }, { unit: "tokens" });
            this._clipPlot(ctx, w, h, pad);
            const line = (key, color) => {
                ctx.strokeStyle = color; ctx.lineWidth = 1.5;
                ctx.beginPath();
                this._pathOf(ctx, tk, pad, w, d => pad.t + plotH - ((d[key] || 0) / max) * plotH);
                ctx.stroke();
            };
            line("in", PALETTE.tokIn);
            line("out", PALETTE.tokOut);
            line("cached", PALETTE.tokCached);
            line("reasoning", PALETTE.tokReasoning);
            ctx.restore();
            this._runMarks(ctx, w, h, pad);
            // stacked vertical legend (4 entries × 32px collided)
            ctx.font = "10px " + traceFont();
            ctx.textAlign = "left"; ctx.textBaseline = "top";
            const lg = [["in", PALETTE.tokIn], ["out", PALETTE.tokOut], ["cached", PALETTE.tokCached], ["reasoning", PALETTE.tokReasoning]];
            lg.forEach(([k, c], i) => {
                const y = 2 + i * 12;
                traceLegendText(ctx, c, "● " + k, w - pad.r - 70, y);
            });
            const hp = this._hoverPoint(tk);
            this._overlays(ctx, w, h, pad, hp
                ? "in " + this.fmtNum(hp.in) + " · out " + this.fmtNum(hp.out) + " · cached " + this.fmtNum(hp.cached) + " · reasoning " + this.fmtNum(hp.reasoning)
                : "iter " + this.hoverIter);
        },

        // Tool-output efficiency: raw tool tokens (amber) vs injected tokens
        // (green). The amber band between the raw line and the green area is
        // tokens kept out of context by storing raw externally + observing.
        effSummary() {
            return (this.summary && this.summary.efficiency) || {};
        },
        // Ranked harness-suspects digest from build_summary (same block the
        // trace tools return). Empty for clean runs -> section stays hidden.
        suspectsList() {
            return (this.summary && this.summary.suspects) || [];
        },
        // Jump from a suspect card to the evidence: map suspect -> panel.
        // Compaction rows live in the compaction timeline; drift in the
        // drift chart; everything else jumps to the first affected
        // iteration recorded on the suspect itself.
        suspectJump(s) {
            const m = /iters? \[([0-9,\s]+)\]/.exec(s.next || "");
            if (m) {
                const first = m[1].split(",")[0].trim();
                if (first) { this.selectIter(parseInt(first, 10)); return; }
            }
            window.scrollTo({ top: 0, behavior: "smooth" });
        },
        renderEfficiency() {
            const s = this._setupCanvas("effCanvas"); if (!s) return;
            const { ctx, w, h } = s; const t = this._theme();
            ctx.clearRect(0, 0, w, h);
            this._curRef = "effCanvas";
            const effAll = this.series.efficiency || [];
            // Sparse: only iters that made tool calls. Positioned by iter
            // through the shared scale (never stretched across the width).
            const eff = this._view(effAll, true);
            const n = eff.length;
            const pad = { l: 56, r: 12, t: 16, b: 20 };
            this._notePad("effCanvas", pad);
            if (!n) { this._empty(ctx, w, h, "no tool-output efficiency data in view"); return; }
            const max = Math.max(1, ...effAll.map(d => Math.max(d.raw_tokens || 0, d.injected_tokens || 0))) * 1.1;
            const plotH = h - pad.t - pad.b;
            this._drawAxes(ctx, w, h, pad, { min: 0, max }, { unit: "tokens" });
            const y = v => pad.t + plotH - ((v || 0) / max) * plotH;
            const xs = eff.map(d => this._xOf(d.iter, pad, w, true));
            this._clipPlot(ctx, w, h, pad);
            // saved band: between raw (top) and injected (bottom) — tokens kept
            // out of context by storing raw externally + observing.
            ctx.fillStyle = PALETTE.savedBand;
            ctx.beginPath();
            for (let i = 0; i < n; i++) ctx.lineTo(xs[i], y(eff[i].raw_tokens));
            for (let i = n - 1; i >= 0; i--) ctx.lineTo(xs[i], y(eff[i].injected_tokens));
            ctx.closePath(); ctx.fill();
            // injected tokens — filled area (what actually entered context)
            ctx.fillStyle = PALETTE.injectedFill;
            ctx.beginPath();
            for (let i = 0; i < n; i++) ctx.lineTo(xs[i], y(eff[i].injected_tokens));
            ctx.lineTo(xs[n - 1], pad.t + plotH); ctx.lineTo(xs[0], pad.t + plotH);
            ctx.closePath(); ctx.fill();
            ctx.strokeStyle = PALETTE.tokOut; ctx.lineWidth = 1.5;
            ctx.beginPath();
            this._pathOf(ctx, eff, pad, w, d => y(d.injected_tokens));
            ctx.stroke();
            // raw tokens — line above (what would have entered without observing)
            ctx.strokeStyle = PALETTE.actual; ctx.lineWidth = 1.6; ctx.setLineDash([5, 3]);
            ctx.beginPath();
            this._pathOf(ctx, eff, pad, w, d => y(d.raw_tokens));
            ctx.stroke(); ctx.setLineDash([]);
            // point markers so isolated tool-call iters are visible when zoomed
            const sw = this._slotW(pad, w);
            if (sw >= 6) {
                for (let i = 0; i < n; i++) {
                    ctx.fillStyle = PALETTE.actual; ctx.beginPath(); ctx.arc(xs[i], y(eff[i].raw_tokens), 2.2, 0, Math.PI * 2); ctx.fill();
                    ctx.fillStyle = PALETTE.tokOut; ctx.beginPath(); ctx.arc(xs[i], y(eff[i].injected_tokens), 2.2, 0, Math.PI * 2); ctx.fill();
                }
            }
            ctx.restore();
            this._runMarks(ctx, w, h, pad);
            // legend
            ctx.font = "10px " + traceFont();
            ctx.textAlign = "left"; ctx.textBaseline = "top";
            // right-aligned legend, measured so the two entries never overlap
            ctx.textAlign = "right";
            traceLegendText(ctx, PALETTE.tokOut, "● injected (kept)", w - pad.r - 2, 2);
            const injW = ctx.measureText("● injected (kept)").width;
            traceLegendText(ctx, PALETTE.actual, "┄ raw (would-be)", w - pad.r - 2 - injW - 12, 2);
            ctx.textAlign = "left";
            const hp = this._hoverPoint(eff);
            this._overlays(ctx, w, h, pad, hp
                ? this.fmtNum(hp.raw_tokens) + " raw · " + this.fmtNum(hp.injected_tokens) + " inj · "
                    + this.fmtNum((hp.raw_tokens || 0) - (hp.injected_tokens || 0)) + " saved · "
                    + hp.tool_calls + " call" + (hp.tool_calls === 1 ? "" : "s") + (hp.omitted ? " · " + hp.omitted + " observed" : "")
                : "iter " + this.hoverIter + " · no tool output");
        },

        renderLatency() {
            const s = this._setupCanvas("latCanvas"); if (!s) return;
            const { ctx, w, h } = s; const t = this._theme();
            ctx.clearRect(0, 0, w, h);
            this._curRef = "latCanvas";
            const latAll = this.series.latency || [];
            const lat = this._view(latAll);
            const n = lat.length;
            const pad = { l: 56, r: 12, t: 14, b: 18 };
            this._notePad("latCanvas", pad);
            if (!n) { this._empty(ctx, w, h, "no latency data"); return; }
            const max = Math.max(1, ...latAll.map(d => d.wall_ms)) * 1.1;
            const plotH = h - pad.t - pad.b;
            this._drawAxes(ctx, w, h, pad, { min: 0, max }, { unit: "wall ms" });
            const barW = Math.max(1.5, Math.min(28, this._slotW(pad, w) - 1));
            for (let i = 0; i < n; i++) {
                const cx = this._xOf(lat[i].iter, pad, w);
                if (cx == null) continue;
                const x = cx - barW / 2;
                const bh = (lat[i].wall_ms / max) * plotH;
                ctx.fillStyle = (this.selectedIter === lat[i].iter) ? PALETTE.actual : PALETTE.latency;
                ctx.fillRect(x, pad.t + plotH - bh, barW, bh);
            }
            this._runMarks(ctx, w, h, pad);
            const hp = this._hoverPoint(lat);
            this._overlays(ctx, w, h, pad, hp ? this.fmtMs(hp.wall_ms) + " wall" : "iter " + this.hoverIter);
        },

        renderTool() {
            const s = this._setupCanvas("toolCanvas"); if (!s) return;
            const { ctx, w, h } = s; const t = this._theme();
            ctx.clearRect(0, 0, w, h);
            this._curRef = "toolCanvas";
            const tsAll = this.selectedToolSeries();
            // Sparse (only iters where this tool ran) and possibly several
            // calls per iter: bars sit in their iter's slot, sub-divided when
            // an iter has more than one call.
            const ts = this._view(tsAll);
            const pad = { l: 56, r: 12, t: 14, b: 18 };
            this._notePad("toolCanvas", pad);
            if (!ts.length) { this._empty(ctx, w, h, this.selectedTool ? (tsAll.length ? "no calls in view" : "no calls") : "select a tool"); return; }
            const max = Math.max(1, ...tsAll.map(d => d.latency_ms)) * 1.1;
            const plotH = h - pad.t - pad.b;
            this._drawAxes(ctx, w, h, pad, { min: 0, max }, { unit: "ms" });
            const slotW = Math.max(1.5, Math.min(28, this._slotW(pad, w) - 2));
            const byIter = new Map();
            for (const d of ts) { if (!byIter.has(d.iter)) byIter.set(d.iter, []); byIter.get(d.iter).push(d); }
            for (const [iter, calls] of byIter) {
                const cx = this._xOf(iter, pad, w);
                if (cx == null) continue;
                const m = calls.length;
                const bw = Math.max(1, (slotW - (m - 1)) / m);
                calls.forEach((d, k) => {
                    const x = cx - slotW / 2 + k * (bw + 1);
                    const bh = (d.latency_ms / max) * plotH;
                    ctx.fillStyle = d.ok ? PALETTE.ok : PALETTE.err;
                    ctx.fillRect(x, pad.t + plotH - bh, bw, Math.max(1, bh));
                    if (d.cache_hit) {
                        ctx.fillStyle = PALETTE.cache;
                        ctx.fillRect(x, pad.t + plotH - bh - 2, bw, 2);
                    }
                });
            }
            this._runMarks(ctx, w, h, pad);
            // legend
            ctx.font = "10px " + traceFont();
            ctx.textAlign = "left"; ctx.textBaseline = "top";
            traceLegendText(ctx, PALETTE.ok, "● ok", w - pad.r - 96, 2);
            traceLegendText(ctx, PALETTE.err, "● error", w - pad.r - 60, 2);
            traceLegendText(ctx, PALETTE.cache, "▬ cache hit", w - pad.r - 24, 2);
            const calls = this.hoverIter != null ? ts.filter(d => d.iter === this.hoverIter) : [];
            this._overlays(ctx, w, h, pad, calls.length
                ? calls.map(d => this.fmtMs(d.latency_ms) + (d.ok ? "" : " ✗") + (d.cache_hit ? " (cache)" : "")).join(" · ")
                : "iter " + this.hoverIter + " · no calls");
        },
    };
}
