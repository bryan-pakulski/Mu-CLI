/* Node history charts (mu/orchestration/metrics.py): what CPU, memory, disk, load, network and
 * disk I/O did over time, for one node (the node page) or a scope (the Nodes dashboard: CPU,
 * memory and load averaged, the fullest disk, network and I/O summed).
 *
 *   OrchHistory (pure, tested in tests/gui/frontend/node-history.test.cjs):
 *     CHARTS, human(value, unit), niceMax(value, unit), segments(points, key, bucket_s),
 *     chartModel(def, data), summary(def, model, range), nearestIndex(points, keys, t)
 *     per-node sparklines for the dashboard's Health rows: SPARKS, SORTS, sparkTone(spark, v),
 *     sparkScales(perNode), sparkPath(points, key, max, since, until, bucket), nodeSparks(entry,
 *     scales, data), sparkSummary(name, sparks, range), sortNodes(ids, perNode, by)
 *   Orch.history.mount(el, {url, compact, notice(data) → html, bind(el, data), onData(data)}) → {dispose}
 *   Orch.history.sparksHtml(sparks, summary) → the inline sparklines of one Health row
 *
 * Samples arrive about every 20 s with no change event, so a mounted block polls every 30 s while
 * its page is on screen (MuLive, mu_live.js) and again when shown; the range is kept per tab.
 * One axis per chart (0 → its own scale, 0–100 for percentages); a gap in the samples is a gap in
 * the line; hover or the arrow keys read any bucket back (Mu §9, dataviz interaction).
 */
(function () {
    'use strict';
    const Orch = (window.Orch = window.Orch || { routes: {} });

    const CHARTS = [
        { id: 'cpu', title: 'CPU', unit: 'percent', series: [{ key: 'cpu_pct', label: 'CPU' }] },
        { id: 'mem', title: 'Memory', unit: 'percent', series: [{ key: 'mem_pct', label: 'Memory' }] },
        { id: 'disk', title: 'Disk (fullest)', unit: 'percent', series: [{ key: 'disk_pct', label: 'Disk' }] },
        { id: 'load', title: 'Load', unit: 'number', series: [{ key: 'load1', label: 'Load' }] },
        { id: 'net', title: 'Network', unit: 'bytes_per_s', series: [{ key: 'net_rx_bps', label: 'In' }, { key: 'net_tx_bps', label: 'Out' }] },
        { id: 'io', title: 'Disk I/O', unit: 'bytes_per_s', series: [{ key: 'disk_read_bps', label: 'Read' }, { key: 'disk_write_bps', label: 'Write' }] },
    ];
    const RANGES = ['1h', '6h', '24h', '7d', '30d'];
    const RANGE_WORDS = { '1h': 'hour', '6h': '6 hours', '24h': '24 hours', '7d': '7 days', '30d': '30 days' };
    const POLL_MS = 30000;

    /* ------------------------------------------------------------ model (pure) */
    function human(value, unit) {
        if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
        const v = Number(value);
        if (unit === 'percent') return `${v < 10 && v > 0 ? v.toFixed(1) : Math.round(v)}%`;
        if (unit === 'bytes_per_s') {
            for (const [size, suffix] of [[1 << 30, 'GB/s'], [1 << 20, 'MB/s'], [1 << 10, 'kB/s']]) {
                if (v >= size) return `${(v / size).toFixed(v / size < 10 ? 1 : 0)} ${suffix}`;
            }
            return `${Math.round(v)} B/s`;
        }
        if (Number.isInteger(v)) return String(v);
        return v >= 10 ? v.toFixed(1) : v.toFixed(2);
    }
    /* The axis top: 100 for percentages, else a round number (1, 2 or 5 × 10^k) at or above the peak. */
    function niceMax(peak, unit) {
        if (unit === 'percent') return 100;
        const v = Number(peak) || 0;
        if (v <= 0) return 1;
        const base = Math.pow(10, Math.floor(Math.log10(v)));
        for (const step of [1, 2, 5, 10]) if (step * base >= v) return step * base;
        return 10 * base;
    }
    /* Runs of [t, value] with no gap: a missing value, or a step longer than 2.5 buckets, breaks the line. */
    function segments(points, key, bucket) {
        const out = [];
        let run = [];
        let prevT = null;
        const limit = 2.5 * (Number(bucket) || 20);
        (points || []).forEach(p => {
            const v = p[key];
            const ok = v !== null && v !== undefined && Number.isFinite(Number(v));
            if (!ok || (prevT !== null && p.t - prevT > limit)) {
                if (run.length) out.push(run);
                run = [];
            }
            if (ok) { run.push([Number(p.t), Number(v)]); prevT = p.t; } else prevT = null;
        });
        if (run.length) out.push(run);
        return out;
    }
    function chartModel(def, data) {
        const points = data.points || [];
        const series = def.series.map(s => {
            const values = points.map(p => p[s.key]).filter(v => v !== null && v !== undefined && Number.isFinite(Number(v))).map(Number);
            return {
                ...s, segments: segments(points, s.key, data.bucket_s),
                now: values.length ? values[values.length - 1] : null,
                avg: values.length ? values.reduce((a, b) => a + b, 0) / values.length : null,
                peak: values.length ? Math.max(...values) : null,
            };
        });
        const peak = Math.max(0, ...series.map(s => s.peak ?? 0));
        return { def, series, max: niceMax(peak, def.unit), empty: series.every(s => !s.segments.length), since: data.since, until: data.until };
    }
    function summary(def, model, range) {
        const span = `over the last ${RANGE_WORDS[range] || range}`;
        if (model.empty) return `${def.title} ${span}: no samples.`;
        if (model.series.length === 1) {
            const s = model.series[0];
            return `${def.title} ${span}: now ${human(s.now, def.unit)}, average ${human(s.avg, def.unit)}, peak ${human(s.peak, def.unit)}.`;
        }
        return `${def.title} ${span}: ${model.series.map(s => `${s.label.toLowerCase()} now ${human(s.now, def.unit)}, peak ${human(s.peak, def.unit)}`).join('; ')}.`;
    }
    /* The bucket nearest to time t that has a value for any of the keys (−1 when none). */
    function nearestIndex(points, keys, t) {
        let best = -1;
        let dist = Infinity;
        (points || []).forEach((p, i) => {
            if (!keys.some(k => p[k] !== null && p[k] !== undefined)) return;
            const d = Math.abs(p.t - t);
            if (d < dist) { dist = d; best = i; }
        });
        return best;
    }

    /* ------------------------------------------------------------ per-node sparklines (pure)
     * One row per node in the dashboard's Health list: CPU and memory on 0–100 (so a hot node
     * reads hot whatever its neighbours do), network and disk I/O on the scope's shared scale
     * (the busiest node fills its row; the others compare against it). */
    const SPARKS = [
        { key: 'cpu_pct', label: 'CPU', unit: 'percent', warn: 85, crit: 95 },
        { key: 'mem_pct', label: 'Memory', unit: 'percent', warn: 90, crit: 95 },
        { key: 'net_bps', label: 'Network', unit: 'bytes_per_s' },
        { key: 'io_bps', label: 'Disk I/O', unit: 'bytes_per_s' },
    ];
    const SORTS = [['health', 'Health'], ['cpu_pct', 'CPU'], ['mem_pct', 'Memory'], ['net_bps', 'Network'], ['io_bps', 'Disk I/O']];
    const SPARK_W = 64;
    const SPARK_H = 16;
    function sparkTone(spark, value) {
        if (value === null || value === undefined || !spark.warn) return '';
        return value >= spark.crit ? 'danger' : value >= spark.warn ? 'attention' : '';
    }
    function sparkScales(perNode) {
        const out = {};
        SPARKS.forEach(sp => {
            if (sp.unit === 'percent') { out[sp.key] = 100; return; }
            const peak = Math.max(0, ...Object.values(perNode || {}).map(e => Number((e.peak || {})[sp.key]) || 0));
            out[sp.key] = niceMax(peak, sp.unit);
        });
        return out;
    }
    function sparkPath(points, key, max, since, until, bucket) {
        const span = Math.max(1, until - since);
        const x = t => Math.max(0, Math.min(SPARK_W, ((t - since) / span) * SPARK_W));
        const y = v => SPARK_H - 1 - (max > 0 ? Math.min(1, Math.max(0, v) / max) * (SPARK_H - 2) : 0);
        return segments(points, key, bucket).map(run => (run.length === 1
            ? `M${(x(run[0][0]) - 0.75).toFixed(1)},${y(run[0][1]).toFixed(1)}h1.5`
            : run.map(([t, v], j) => `${j ? 'L' : 'M'}${x(t).toFixed(1)},${y(v).toFixed(1)}`).join(''))).join('');
    }
    /* A node's sparklines: [{key, label, unit, now, tone, text, d}] (d '' when no samples). */
    function nodeSparks(entry, scales, data) {
        const points = (entry && entry.points) || [];
        const bucket = Math.max(1, (data.until - data.since) / 48);
        return SPARKS.map(sp => {
            const now = entry && entry.latest ? entry.latest[sp.key] : null;
            return { key: sp.key, label: sp.label, unit: sp.unit, now: now ?? null, peak: entry && entry.peak ? entry.peak[sp.key] ?? null : null,
                tone: sparkTone(sp, now), text: `${sp.label} ${human(now, sp.unit)}`,
                d: points.length ? sparkPath(points, sp.key, scales[sp.key], data.since, data.until, bucket) : '' };
        });
    }
    function sparkSummary(name, sparks, range) {
        const parts = sparks.filter(s => s.now !== null).map(s => `${s.label.toLowerCase()} now ${human(s.now, s.unit)}${s.peak !== null && s.peak !== s.now ? ` (peak ${human(s.peak, s.unit)})` : ''}`);
        return parts.length ? `${name}, last ${RANGE_WORDS[range] || range}: ${parts.join(', ')}.` : `${name}: no history.`;
    }
    /* Health order as given; otherwise busiest first by the latest value, nodes without one last. */
    function sortNodes(ids, perNode, by) {
        if (!by || by === 'health') return [...ids];
        const val = id => { const v = ((perNode || {})[id] || {}).latest?.[by]; return v === null || v === undefined ? -Infinity : Number(v); };
        return ids.map((id, i) => [id, i]).sort((a, b) => (val(b[0]) - val(a[0])) || (a[1] - b[1])).map(x => x[0]);
    }
    window.OrchHistory = { CHARTS, RANGES, human, niceMax, segments, chartModel, summary, nearestIndex,
                           SPARKS, SORTS, sparkTone, sparkScales, sparkPath, nodeSparks, sparkSummary, sortNodes };

    /* ------------------------------------------------------------ view */
    const W = 320;
    const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const clock = (t, range) => {
        const d = new Date(t * 1000);
        return range === '7d' || range === '30d'
            ? d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
            : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    };
    function svg(model, h) {
        const span = Math.max(1, model.until - model.since);
        const x = t => ((t - model.since) / span) * W;
        const y = v => h - 2 - (model.max > 0 ? Math.min(1, v / model.max) * (h - 6) : 0);
        const lines = model.series.map((s, i) => s.segments.map(run => {
            const d = run.map(([t, v], j) => `${j ? 'L' : 'M'}${x(t).toFixed(1)},${y(v).toFixed(1)}`).join('');
            const area = model.series.length === 1 && run.length > 1
                ? `<path class="orch-hist__area" d="${d}L${x(run[run.length - 1][0]).toFixed(1)},${h}L${x(run[0][0]).toFixed(1)},${h}Z"></path>` : '';
            const dot = run.length === 1 ? `<circle class="orch-hist__dot" data-series="${i}" cx="${x(run[0][0]).toFixed(1)}" cy="${y(run[0][1]).toFixed(1)}" r="2"></circle>` : '';
            return `${area}<path class="orch-hist__line" data-series="${i}" d="${d}"></path>${dot}`;
        }).join('')).join('');
        return `<svg viewBox="0 0 ${W} ${h}" preserveAspectRatio="none" aria-hidden="true" focusable="false">
            <line class="orch-hist__grid" x1="0" y1="4" x2="${W}" y2="4"></line>
            <line class="orch-hist__base" x1="0" y1="${h - 1}" x2="${W}" y2="${h - 1}"></line>
            ${lines}
            <line class="orch-hist__cursor" x1="0" x2="0" y1="0" y2="${h}" hidden></line>
        </svg>`;
    }
    function figure(def, model, range, compact) {
        const legend = model.series.length > 1
            ? `<p class="orch-hist__legend">${model.series.map((s, i) => `<span data-series="${i}"><i aria-hidden="true"></i>${esc(s.label)} ${esc(human(s.now, def.unit))}</span>`).join('')}</p>` : '';
        const value = model.series.length === 1 ? human(model.series[0].now, def.unit) : '';
        const text = summary(def, model, range);
        return `<figure class="orch-hist__chart" data-chart="${def.id}">
            <figcaption class="orch-hist__cap"><span class="orch-hist__label">${esc(def.title)}</span><span class="orch-hist__value" data-value>${esc(value)}</span></figcaption>
            ${legend}
            <div class="orch-hist__plot${compact ? ' orch-hist__plot--compact' : ''}" tabindex="0" role="img" aria-label="${esc(text)}" data-plot>
                ${model.empty ? '<p class="orch-hist__none">No samples in this range.</p>' : svg(model, compact ? 56 : 88)}
                <span class="orch-hist__scale" aria-hidden="true">${esc(human(model.max, def.unit))}</span>
            </div>
            <p class="orch-hist__axis" aria-hidden="true"><span>${esc(clock(model.since, range))}</span><span>now</span></p>
        </figure>`;
    }

    /* One Health row's sparklines: the CPU first (always shown), the rest wrap or hide when narrow. */
    function sparksHtml(sparks, label) {
        return `<span class="orch-sparks" role="img" aria-label="${esc(label)}">${sparks.map(s => `<span class="orch-spark" data-key="${esc(s.key)}"${s.tone ? ` data-tone="${s.tone}"` : ''}>`
            + (s.d ? `<svg class="orch-spark__svg" viewBox="0 0 ${SPARK_W} ${SPARK_H}" preserveAspectRatio="none" aria-hidden="true" focusable="false"><line x1="0" y1="${SPARK_H - 0.5}" x2="${SPARK_W}" y2="${SPARK_H - 0.5}"></line><path d="${s.d}"></path></svg>` : '')
            + `<span class="orch-spark__v" aria-hidden="true">${esc(s.text)}</span></span>`).join('')}</span>`;
    }

    function mount(el, { url, compact = false, notice = null, bind = null, onData = null, key = 'orch-hist-range' } = {}) {
        let range = sessionStorage.getItem(key) || '6h';
        if (!RANGES.includes(range)) range = '6h';
        let data = null;
        let seq = 0;
        let timer = 0;
        let disposed = false;
        const models = new Map();

        function paint() {
            if (disposed) return;
            const head = `<div class="orch-hist__head"><div class="orch-hist__ranges" role="tablist" aria-label="History range">${RANGES.map(r =>
                `<button type="button" role="tab" class="w-textbtn orch-hist__range" data-range="${r}" aria-selected="${r === range}"${r === range ? ' data-on' : ''}>${r}</button>`).join('')}</div></div>`;
            if (!data) { el.innerHTML = `${head}<p class="orch-muted">Loading history…</p>`; return; }
            const any = (data.points || []).length > 0;
            const note = notice ? notice(data) : '';
            models.clear();
            const charts = any ? CHARTS.map(def => { const m = chartModel(def, data); models.set(def.id, m); return figure(def, m, data.range, compact); }).join('') : '';
            el.innerHTML = `${head}${note}${any ? `<div class="orch-hist__grid-wrap${compact ? ' orch-hist__grid-wrap--compact' : ''}">${charts}</div>`
                : `<p class="orch-muted orch-hist__empty">No history yet. Nodes on agent ${esc('1.3.0')} or newer report a sample about every 20 seconds.</p>`}`;
            bind?.(el, data);
            onData?.(data);
        }
        async function load() {
            const mine = ++seq;
            try {
                const next = await window.OrchAPI.get(`${url}${url.includes('?') ? '&' : '?'}range=${encodeURIComponent(range)}`);
                if (mine !== seq || disposed) return;
                data = next;
                paint();
            } catch (err) {
                if (mine !== seq || disposed) return;
                if (!data) el.innerHTML = `<p class="orch-muted">Couldn’t load history: ${esc(err.message)}</p>`;
            }
        }
        const live = () => !window.MuLive || window.MuLive.visible();
        function schedule() {
            clearTimeout(timer);
            timer = setTimeout(() => { if (live()) load(); schedule(); }, POLL_MS);
        }
        const unlisten = window.MuLive ? window.MuLive.onChange(v => { if (v && !disposed) load(); }) : null;

        el.addEventListener('click', e => {
            const b = e.target.closest('[data-range]');
            if (!b || b.dataset.range === range) return;
            range = b.dataset.range;
            sessionStorage.setItem(key, range);
            paint();
            load();
        });
        /* Read a bucket back: hover (pointer) or the arrow keys on a focused chart. */
        function readout(fig, t) {
            const model = models.get(fig.dataset.chart);
            if (!model || !data) return;
            const keys = model.def.series.map(s => s.key);
            const i = nearestIndex(data.points, keys, t);
            const cursor = fig.querySelector('.orch-hist__cursor');
            const value = fig.querySelector('[data-value]');
            if (i < 0) return;
            const p = data.points[i];
            fig._at = p.t;
            const span = Math.max(1, model.until - model.since);
            if (cursor) { const xPos = ((p.t - model.since) / span) * W; cursor.setAttribute('x1', xPos); cursor.setAttribute('x2', xPos); cursor.removeAttribute('hidden'); }
            const parts = model.def.series.map(s => `${model.def.series.length > 1 ? `${s.label} ` : ''}${human(p[s.key], model.def.unit)}`);
            value.textContent = `${parts.join(' · ')} · ${clock(p.t, data.range)}`;
        }
        function clear(fig) {
            const model = models.get(fig.dataset.chart);
            fig._at = null;
            fig.querySelector('.orch-hist__cursor')?.setAttribute('hidden', '');
            const value = fig.querySelector('[data-value]');
            if (value && model) value.textContent = model.series.length === 1 ? human(model.series[0].now, model.def.unit) : '';
        }
        el.addEventListener('pointermove', e => {
            const plot = e.target.closest('[data-plot]');
            if (!plot) return;
            const fig = plot.closest('.orch-hist__chart');
            const model = models.get(fig.dataset.chart);
            if (!model) return;
            const box = plot.getBoundingClientRect();
            const frac = Math.max(0, Math.min(1, (e.clientX - box.left) / box.width));
            readout(fig, model.since + frac * (model.until - model.since));
        });
        el.addEventListener('pointerleave', () => el.querySelectorAll('.orch-hist__chart').forEach(clear), true);
        el.addEventListener('pointerout', e => { const fig = e.target.closest?.('.orch-hist__chart'); if (fig && !fig.contains(e.relatedTarget)) clear(fig); });
        el.addEventListener('keydown', e => {
            const plot = e.target.closest?.('[data-plot]');
            if (!plot || !data) return;
            const fig = plot.closest('.orch-hist__chart');
            if (e.key === 'Escape') { clear(fig); return; }
            if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
            e.preventDefault();
            const pts = data.points || [];
            if (!pts.length) return;
            const at = fig._at ?? pts[pts.length - 1].t;
            let i = pts.findIndex(p => p.t === at);
            if (i < 0) i = pts.length - 1;
            i = Math.max(0, Math.min(pts.length - 1, i + (e.key === 'ArrowRight' ? 1 : -1)));
            readout(fig, pts[i].t);
        });
        el.addEventListener('focusout', e => { const fig = e.target.closest?.('.orch-hist__chart'); if (fig && !fig.contains(e.relatedTarget)) clear(fig); });

        paint();
        load();
        schedule();
        return {
            dispose() { disposed = true; seq += 1; clearTimeout(timer); unlisten?.(); },
            reload: load,
        };
    }

    Orch.history = { mount, sparksHtml };
})();
