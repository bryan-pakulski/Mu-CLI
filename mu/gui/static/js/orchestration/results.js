/* Fleet results (mu/orchestration/results.py): a tool's outputs as one sortable table across
 * nodes, with a chart of the key metric and CSV export.
 *
 *   Orch.results.mount(el, { url, csv(step, view), onRow(row), ask(ctx) → text, empty })
 *
 * `ask` adds "Ask about this": Chat opens with that question in the composer (unsent); the
 * AI answers from the same tables with the read-only fleet_results tool.
 *
 * `url` is relative to /api/orchestration (runs/<id>/results or tools/<id>/results); the
 * response is either {steps: [{step, step_name, tool, tables}]} (a run) or {tables} (a tool's
 * latest results). Each table: {key, label, per: node|item, columns, metric, order, rows}.
 * View, sort, chart/table and filter are kept per url, so live refreshes of the run page
 * don't reset them. Values from nodes are untrusted: everything goes through esc().
 */
(() => {
    'use strict';
    const api = window.OrchAPI;
    const ui = window.OrchUI;
    const { esc } = ui;
    const memory = new Map();          // url → {step, view, sort, dir, mode, q}
    const CHART_MAX = 24;

    function bytes(n) {
        if (typeof n !== 'number' || !isFinite(n)) return '—';
        const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
        let v = Math.abs(n), i = 0;
        while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
        return `${n < 0 ? '−' : ''}${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
    }
    function num(n) {
        if (typeof n !== 'number' || !isFinite(n)) return '—';
        return Number.isInteger(n) ? n.toLocaleString() : n.toLocaleString(undefined, { maximumFractionDigits: 2 });
    }
    /* Tone for a value: percentages fill up, days run out. */
    function tone(col, v) {
        if (typeof v !== 'number') return '';
        if (col.format === 'percent') return v >= 90 ? 'danger' : v >= 75 ? 'attention' : '';
        if (col.format === 'days') return v <= 14 ? 'danger' : v <= 30 ? 'attention' : '';
        return '';
    }
    function cell(col, v) {
        if (v === null || v === undefined || v === '') return '<span class="orch-muted">—</span>';
        const t = tone(col, v);
        switch (col.format) {
            case 'bytes': return esc(bytes(v));
            case 'percent': {
                const pct = Math.max(0, Math.min(100, Number(v)));
                return `<span class="orch-results__pct"><span class="w-bar" style="--v: ${esc(pct)}%"${t ? ` data-tone="${t}"` : ''} aria-hidden="true"></span><span${t ? ` data-tone="${t}"` : ''}>${esc(num(v))}%</span></span>`;
            }
            case 'days': return `<span${t ? ` data-tone="${t}"` : ''}>${esc(num(v))} ${Math.abs(v) === 1 ? 'day' : 'days'}</span>`;
            case 'seconds': return esc(ui.dur ? ui.dur(v) : `${num(v)} s`);
            case 'bool': return v ? 'yes' : 'no';
            case 'number': return esc(num(v));
            default: return esc(String(v));
        }
    }
    const numeric = col => col.kind === 'integer' || col.kind === 'number';

    function sorted(table, st) {
        const key = st.sort || table.metric;
        const dir = st.sort ? st.dir : (table.order || 'asc');
        const rows = table.rows.slice();
        if (!key) return rows;
        const val = r => (key === '__node' ? r.node : r.values[key]);
        rows.sort((a, b) => {
            const x = val(a), y = val(b);
            const xm = x === null || x === undefined || x === '', ym = y === null || y === undefined || y === '';
            if (xm || ym) return xm === ym ? 0 : xm ? 1 : -1;      // blanks last either way
            const c = typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y), undefined, { numeric: true });
            return dir === 'desc' ? -c : c;
        });
        return rows;
    }
    function filtered(rows, q) {
        const needle = String(q || '').trim().toLowerCase();
        if (!needle) return rows;
        return rows.filter(r => [r.node, r.state, ...Object.entries(r.labels || {}).map(([k, v]) => `${k}=${v}`), ...Object.values(r.values)]
            .some(v => v !== null && v !== undefined && String(v).toLowerCase().includes(needle)));
    }

    function tableHtml(table, rows, st) {
        const showState = table.rows.some(r => r.state !== 'succeeded');
        const sortKey = st.sort || table.metric;
        const dir = st.sort ? st.dir : (table.order || 'asc');
        const th = (key, label, isNum) => {
            const on = sortKey === key;
            const aria = on ? (dir === 'desc' ? 'descending' : 'ascending') : 'none';
            return `<th scope="col" aria-sort="${aria}"${isNum ? ' data-num' : ''}><button type="button" class="orch-results__sort" data-sort="${esc(key)}">${esc(label)}<span class="orch-results__arrow" aria-hidden="true">${on ? (dir === 'desc' ? '↓' : '↑') : ''}</span></button></th>`;
        };
        const head = `<tr>${th('__node', 'Node', false)}${showState ? '<th scope="col">State</th>' : ''}${table.columns.map(c => th(c.key, c.label, numeric(c))).join('')}</tr>`;
        const body = rows.map(r => `<tr data-exec="${esc(r.exec_id)}">
            <th scope="row"><button type="button" class="orch-results__node" data-row-exec="${esc(r.exec_id)}" title="${esc(Object.entries(r.labels || {}).map(([k, v]) => `${k}=${v}`).join(', '))}">${esc(r.node)}</button></th>
            ${showState ? `<td>${r.state === 'succeeded' ? '<span class="orch-muted">ok</span>' : `<span class="w-signword" data-status="${r.state === 'failed' ? 'failed' : 'pending'}">${esc(String(r.state).replace(/_/g, ' '))}</span>`}</td>` : ''}
            ${table.columns.map(c => `<td${numeric(c) ? ' data-num' : ''}>${cell(c, r.values[c.key])}</td>`).join('')}</tr>`).join('');
        return `<div class="orch-results__scroll"><table class="w-table orch-results__table"><caption class="w-visually-hidden">${esc(table.label)}, sorted by ${esc(sortKey === '__node' ? 'node' : (table.columns.find(c => c.key === sortKey)?.label || sortKey || 'node'))}</caption><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
    }

    /* A horizontal bar per row for the chart column (the sort column when numeric, else the
     * key metric). SVG geometry, no inline styles; a text table stays for assistive tech. */
    function chartHtml(table, rows, st) {
        const key = (st.sort && st.sort !== '__node' && numeric(table.columns.find(c => c.key === st.sort) || {})) ? st.sort : table.metric;
        const col = table.columns.find(c => c.key === key);
        if (!col) return '<p class="orch-muted">Nothing numeric to chart in this view.</p>';
        const data = rows.filter(r => typeof r.values[key] === 'number').slice(0, CHART_MAX);
        if (!data.length) return '<p class="orch-muted">No values to chart yet.</p>';
        const label = r => (table.per === 'item'
            ? `${r.node} · ${String(table.columns.filter(c => !numeric(c)).map(c => r.values[c.key]).filter(Boolean)[0] ?? '')}`
            : r.node);
        const max = col.format === 'percent' ? 100 : Math.max(...data.map(r => Math.abs(r.values[key]))) || 1;
        const rowH = 26, labelW = 200, valueW = 90, width = 760, barW = width - labelW - valueW;
        const bars = data.map((r, i) => {
            const v = r.values[key], w = Math.max(1, Math.round(barW * Math.min(1, Math.abs(v) / max)));
            const t = tone(col, v);
            const y = i * rowH;
            return `<g class="orch-results__bar" data-tone="${t || 'accent'}"><title>${esc(label(r))}: ${esc(cell(col, v).replace(/<[^>]+>/g, ''))}</title>
                <text x="${labelW - 10}" y="${y + 17}" text-anchor="end" class="orch-results__barlabel">${esc(label(r).length > 30 ? `${label(r).slice(0, 29)}…` : label(r))}</text>
                <rect x="${labelW}" y="${y + 6}" width="${barW}" height="14" rx="3" class="orch-results__track"></rect>
                <rect x="${labelW}" y="${y + 6}" width="${w}" height="14" rx="3" class="orch-results__fill"></rect>
                <text x="${labelW + barW + 10}" y="${y + 17}" class="orch-results__barvalue">${esc(cell(col, v).replace(/<[^>]+>/g, ''))}</text></g>`;
        }).join('');
        const more = rows.length > data.length ? `<p class="orch-sub">Showing the first ${data.length} of ${rows.length}; the table has every row.</p>` : '';
        return `<figure class="orch-results__chart"><figcaption class="orch-sub">${esc(col.label)} by ${table.per === 'item' ? 'item' : 'node'}</figcaption>
            <svg viewBox="0 0 ${width} ${data.length * rowH + 6}" role="img" aria-label="${esc(`${col.label}: ${data.map(r => `${label(r)} ${cell(col, r.values[key]).replace(/<[^>]+>/g, '')}`).join(', ')}`)}">${bars}</svg>${more}</figure>`;
    }

    function mount(el, opts) {
        if (!el) return;
        const key = opts.url;
        const st = memory.get(key) || { step: null, view: 'summary', sort: '', dir: 'asc', mode: 'table', q: '' };
        memory.set(key, st);
        let data = null;

        function tables() {
            if (!data) return [];
            if (data.steps) {
                const step = data.steps.find(s => s.step === st.step) || data.steps[data.steps.length - 1];
                if (step) st.step = step.step;
                return step ? step.tables : [];
            }
            return data.tables || [];
        }
        function paint() {
            const list = tables();
            const steps = data && data.steps ? data.steps : null;
            const anyRows = list.some(t => t.rows.length && t.rows.some(r => Object.values(r.values).some(v => v !== null && v !== undefined)));
            if (!anyRows) { el.innerHTML = `<p class="orch-muted">${esc(opts.empty || 'No results yet.')}</p>`; return; }
            const table = list.find(t => t.key === st.view) || list[0];
            st.view = table.key;
            const rows = filtered(sorted(table, st), st.q);
            const stepPicker = steps && steps.length > 1
                ? `<label class="orch-results__step"><span class="w-visually-hidden">Step</span><span class="w-select" data-size="sm"><select class="w-select__control" data-step>${steps.map(s => `<option value="${s.step}"${s.step === st.step ? ' selected' : ''}>${esc(s.step_name || `Step ${s.step + 1}`)} · ${esc(s.tool)}</option>`).join('')}</select></span></label>` : '';
            const viewTabs = list.length > 1
                ? `<div class="orch-results__views" role="group" aria-label="Results view">${list.map(t => `<button type="button" class="orch-textfilter" data-view="${esc(t.key)}" aria-pressed="${t.key === table.key}">${esc(t.label)}<span class="orch-count">${t.rows.length}</span></button>`).join('')}</div>` : '';
            const csvHref = opts.csv ? opts.csv(st.step ?? 0, table.key) : '';
            const withOut = table.per === 'node' ? table.rows.filter(r => Object.values(r.values).some(v => v !== null && v !== undefined)).length : table.rows.length;
            el.innerHTML = `
                <div class="orch-results__bar">
                    ${stepPicker}${viewTabs}
                    <span class="orch-results__spacer"></span>
                    ${table.rows.length > 8 ? `<input type="search" class="w-input orch-results__filter" data-filter placeholder="Filter" aria-label="Filter results" value="${esc(st.q)}">` : ''}
                    <div class="orch-results__mode" role="group" aria-label="Show as">
                        <button type="button" class="orch-textfilter" data-mode="table" aria-pressed="${st.mode === 'table'}">Table</button>
                        <button type="button" class="orch-textfilter" data-mode="chart" aria-pressed="${st.mode === 'chart'}">Chart</button>
                    </div>
                    ${csvHref ? `<a class="w-textbtn" href="${esc(csvHref)}" download data-no-prefetch>${ui.icon('download', 'sm')}<span>CSV</span></a>` : ''}
                    ${opts.ask ? `<button type="button" class="w-textbtn" data-ask title="Open Chat with a question about these results (not sent)">${ui.icon('message-circle', 'sm')}<span>Ask about this</span></button>` : ''}
                </div>
                <p class="orch-sub orch-results__meta" aria-live="polite">${table.per === 'item'
                    ? `${esc(rows.length)} ${esc(table.label.toLowerCase())} across ${esc(new Set(table.rows.map(r => r.node_id)).size)} nodes`
                    : `${esc(withOut)} of ${esc(table.rows.length)} nodes reported`}${st.q ? ` · ${esc(rows.length)} match` : ''}${data.oldest ? ` · oldest result ${esc(ui.ago(data.oldest))}` : ''}</p>
                ${st.mode === 'chart' ? chartHtml(table, rows, st) : tableHtml(table, rows, st)}`;
            bind();
        }
        function bind() {
            el.querySelector('[data-step]')?.addEventListener('change', e => { st.step = Number(e.target.value); st.view = 'summary'; st.sort = ''; paint(); });
            el.querySelectorAll('[data-view]').forEach(b => b.addEventListener('click', () => { st.view = b.dataset.view; st.sort = ''; paint(); }));
            el.querySelectorAll('[data-mode]').forEach(b => b.addEventListener('click', () => { st.mode = b.dataset.mode; paint(); }));
            el.querySelectorAll('[data-sort]').forEach(b => b.addEventListener('click', () => {
                const k = b.dataset.sort;
                const table = tables().find(t => t.key === st.view);
                const current = st.sort || table?.metric;
                const currentDir = st.sort ? st.dir : (table?.order || 'asc');
                st.dir = current === k ? (currentDir === 'asc' ? 'desc' : 'asc') : (k === '__node' ? 'asc' : 'desc');
                st.sort = k;
                paint();
                el.querySelector(`[data-sort="${CSS.escape(k)}"]`)?.focus();
            }));
            const filter = el.querySelector('[data-filter]');
            if (filter) filter.addEventListener('input', ui.debounce(() => {
                st.q = filter.value;
                const pos = filter.selectionStart;
                paint();
                const again = el.querySelector('[data-filter]');
                if (again) { again.focus(); again.setSelectionRange(pos, pos); }
            }, 150));
            /* Hand the table to Chat: the AI reads it with fleet_results (read-only). */
            el.querySelector('[data-ask]')?.addEventListener('click', () => {
                const table = tables().find(t => t.key === st.view);
                const text = opts.ask({ view: st.view, label: table?.label || st.view, step: st.step, filter: st.q });
                const href = `/?ask=${encodeURIComponent(text)}`;
                if (window.MuNav) window.MuNav.go(href); else location.assign(href);
            });
            if (opts.onRow) el.querySelectorAll('[data-row-exec]').forEach(b => b.addEventListener('click', () => {
                const row = tables().flatMap(t => t.rows).find(r => r.exec_id === b.dataset.rowExec);
                if (row) opts.onRow(row);
            }));
        }
        el.innerHTML = ui.loading ? ui.loading('Loading results…') : '<p class="orch-muted">Loading results…</p>';
        api.get(opts.url).then(d => { data = d; paint(); }).catch(err => { el.innerHTML = ui.errorState(err, { retry: false }); });
    }

    const Orch = (window.Orch = window.Orch || { routes: {} });
    Orch.results = { mount, bytes, format: cell };
})();
