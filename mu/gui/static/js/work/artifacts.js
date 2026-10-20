/* Check run artifacts (mu/jobs/check_artifacts.py): browse everything a checks run produced —
 * suite output, JUnit reports, and the files the suites wrote (logs, XML, JSON, CSV, HTML
 * reports, screenshots, packet captures, archives) — with a viewer suited to each kind.
 *
 *   window.MuArtifacts.mount(placeholder, {jobId, run})
 *
 * The Checks tab re-renders often while a job is live; the browser for a run is built once and
 * its element moved into each new placeholder, so the open file, filters and scroll survive.
 * Artifacts come from code under test: everything is escaped text here, HTML reports load in a
 * sandboxed frame with no scripts (the server adds a sandbox CSP too), raw files download. */
(function () {
    'use strict';
    const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const KIND = {
        log: ['Log', 'file-text'], text: ['Text', 'file-text'], junit: ['JUnit', 'list-checks'], xml: ['XML', 'code'],
        json: ['JSON', 'braces'], csv: ['Table', 'table'], html: ['HTML report', 'globe'], image: ['Image', 'image'],
        pcap: ['Packet capture', 'network'], archive: ['Archive', 'archive'], binary: ['Binary', 'file'],
    };
    const LEVEL = /\b(ERROR|ERR|FATAL|FAIL(?:ED|URE)?|Traceback|Exception|panic)\b/;
    const WARN = /\b(WARN(?:ING)?|DEPRECAT\w*)\b/;
    const cache = new Map();          // `${jobId}/${run}` -> {el, state}

    const size = n => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;
    const base = (jobId, run) => `/api/jobs/${encodeURIComponent(jobId)}/checks/${encodeURIComponent(run)}`;
    const rawUrl = (jobId, run, path, view) => `${base(jobId, run)}/files/${path.split('/').map(encodeURIComponent).join('/')}${view ? '?view=1' : ''}`;
    async function getJSON(url) {
        const res = await fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
        if (!res.ok) throw new Error(res.status === 404 ? 'not found' : `HTTP ${res.status}`);
        return res.json();
    }

    function mount(placeholder, { jobId, run }) {
        if (!placeholder || !jobId || !run) return;
        const key = `${jobId}/${run}`;
        let entry = cache.get(key);
        if (!entry) {
            const el = document.createElement('section');
            el.className = 'work-art';
            el.setAttribute('aria-label', `Artifacts of run ${run}`);
            entry = { el, state: { jobId, run, listing: null, open: null, filter: '', previews: new Map(), view: {} } };
            cache.set(key, entry);
            load(entry);
        }
        placeholder.replaceWith(entry.el);
    }

    async function load(entry) {
        const { el, state } = entry;
        el.innerHTML = '<p class="work-meta">Loading artifacts…</p>';
        try {
            state.listing = await getJSON(`${base(state.jobId, state.run)}/artifacts`);
        } catch (err) {
            el.innerHTML = `<p class="work-meta">No artifacts for this run (${esc(err.message)}).</p>`;
            return;
        }
        const first = state.listing.files.find(f => !f.path.startsWith('suite-')) || state.listing.files[0];
        render(entry);
        if (first) open(entry, first.path);
    }

    function render(entry) {
        const { el, state } = entry;
        const files = state.listing.files;
        const groups = new Map();
        files.forEach(f => { const g = f.label === 'suite output' ? 'Suite output' : f.label === 'suites' ? 'Produced by the suites' : `Produced by ${(f.suites || [])[0] || f.label}`; if (!groups.has(g)) groups.set(g, []); groups.get(g).push(f); });
        const q = state.filter.toLowerCase();
        const skipped = state.listing.skipped || [];
        el.innerHTML = `<header class="work-art__head"><h3 class="work-subhead">Artifacts <span class="w-count">${files.length}</span></h3>
                <span class="work-meta">${state.listing.collected} produced · ${size(state.listing.bytes || 0)}</span><span class="work-spacer"></span>
                <input class="w-input work-art__filter" type="search" placeholder="Filter files…" aria-label="Filter artifacts" value="${esc(state.filter)}" data-art-filter></header>
            <div class="work-art__body">
                <nav class="work-art__list" aria-label="Artifact files">${[...groups].map(([g, list]) => {
                    const shown = list.filter(f => !q || `${f.name} ${f.kind}`.toLowerCase().includes(q));
                    return shown.length ? `<div class="work-art__group"><div class="work-art__grouphead">${esc(g)}</div>${shown.map(f => `<button type="button" class="work-art__file" data-art-path="${esc(f.path)}" aria-current="${f.path === state.open}" title="${esc(f.name)}">
                        <span class="work-art__name">${esc(f.name)}</span><span class="work-art__meta">${esc((KIND[f.kind] || KIND.binary)[0])} · ${size(f.bytes || 0)}</span></button>`).join('')}</div>` : '';
                }).join('') || '<p class="work-meta">No file matches.</p>'}
                ${skipped.length ? `<details class="work-art__skipped"><summary>${skipped.length} not kept</summary><ul>${skipped.slice(0, 50).map(s => `<li><span class="work-mono">${esc(s.name)}</span> — ${esc(s.reason)}</li>`).join('')}</ul></details>` : ''}</nav>
                <div class="work-art__viewer" data-art-viewer>${state.open ? '' : '<p class="work-meta">Choose a file.</p>'}</div>
            </div>`;
        el.querySelectorAll('[data-art-path]').forEach(b => { b.onclick = () => open(entry, b.dataset.artPath); });
        const filter = el.querySelector('[data-art-filter]');
        filter.oninput = () => { state.filter = filter.value; const pos = filter.selectionStart; render(entry); const f = el.querySelector('[data-art-filter]'); f.focus(); f.setSelectionRange(pos, pos); };
        if (state.open && state.previews.has(state.open)) paintViewer(entry);
    }

    async function open(entry, path) {
        const { el, state } = entry;
        state.open = path;
        state.view = {};
        el.querySelectorAll('[data-art-path]').forEach(b => b.setAttribute('aria-current', String(b.dataset.artPath === path)));
        const viewer = el.querySelector('[data-art-viewer]');
        if (!state.previews.has(path)) {
            viewer.innerHTML = '<p class="work-meta">Opening…</p>';
            try {
                state.previews.set(path, await getJSON(`${base(state.jobId, state.run)}/preview?path=${encodeURIComponent(path)}`));
            } catch (err) {
                viewer.innerHTML = `<p class="work-meta">Couldn’t open it: ${esc(err.message)}</p>`;
                return;
            }
            if (state.open !== path) return;
        }
        paintViewer(entry);
        window.WashMotion?.enter(el.querySelector('[data-art-viewer]'));
    }

    /* ------------------------------------------------------------ viewers */
    function paintViewer(entry) {
        const { el, state } = entry;
        const p = state.previews.get(state.open);
        const viewer = el.querySelector('[data-art-viewer]');
        if (!p || !viewer) return;
        const file = state.listing.files.find(f => f.path === p.path) || {};
        const modes = modesFor(p);
        const mode = state.view.mode && modes.some(m => m[0] === state.view.mode) ? state.view.mode : modes[0][0];
        viewer.innerHTML = `<header class="work-art__vhead"><div class="work-art__vtitle"><strong class="work-mono">${esc(file.name || p.path)}</strong>
                <span class="work-meta">${esc((KIND[p.kind] || KIND.binary)[0])} · ${size(p.bytes || 0)}${p.truncated ? ' · partly shown' : ''}${p.note ? ` · ${esc(p.note)}` : ''}</span></div>
                ${modes.length > 1 ? `<div class="w-tabs work-art__modes" role="tablist" aria-label="View as">${modes.map(([id, label]) => `<button type="button" class="w-tabs__tab" role="tab" aria-selected="${id === mode}" data-art-mode="${id}">${esc(label)}</button>`).join('')}</div>` : ''}
                <a class="w-textbtn" data-tone="quiet" href="${esc(rawUrl(state.jobId, state.run, p.path))}" download>Download</a></header>
            ${p.error ? `<p class="w-status" data-tone="attention">${esc(p.error)}</p>` : ''}
            <div class="work-art__vbody" role="tabpanel">${VIEW[mode](p, state, entry)}</div>`;
        viewer.querySelectorAll('[data-art-mode]').forEach(b => { b.onclick = () => { state.view = { mode: b.dataset.artMode }; paintViewer(entry); }; });
        BIND[mode]?.(viewer, p, state, entry);
    }

    function modesFor(p) {
        switch (p.kind) {
            case 'junit': return p.junit ? [['junit', 'Tests'], ['text', 'XML']] : [['text', 'XML']];
            case 'html': return [['html', 'Rendered'], ['text', 'Source']];
            case 'image': return [['image', 'Image']];
            case 'pcap': return p.packets ? [['packets', 'Packets'], ['talks', 'Conversations']] : [['hex', 'Hex']];
            case 'csv': return p.rows ? [['table', 'Table']] : [['text', 'Text']];
            case 'archive': return p.entries ? [['archive', 'Contents']] : p.text != null ? [['text', 'Text']] : [['hex', 'Hex']];
            case 'binary': return [['hex', 'Hex']];
            default: return p.text != null ? [['text', p.kind === 'log' ? 'Log' : 'Text']] : [['hex', 'Hex']];
        }
    }

    const VIEW = {
        text(p, state) {
            const v = state.view;
            return `<div class="work-art__tools" role="toolbar" aria-label="Text tools">
                <input class="w-input work-art__find" type="search" placeholder="Show lines containing…" aria-label="Show lines containing" value="${esc(v.find || '')}" data-art-find>
                <label class="work-art__opt"><input type="checkbox" data-art-problems ${v.problems ? 'checked' : ''}> errors and warnings only</label>
                <label class="work-art__opt"><input type="checkbox" data-art-wrap ${v.wrap === false ? '' : 'checked'}> wrap</label>
                <button type="button" class="w-textbtn" data-art-first>First error</button>
                <span class="work-meta" data-art-count></span></div>
                <div class="work-art__text${v.wrap === false ? '' : ' is-wrapped'}" data-art-text></div>`;
        },
        junit(p, state) {
            const t = p.junit.totals, f = state.view.status || (t.failures + t.errors ? 'problems' : 'all');
            const cases = p.junit.suites.flatMap(s => s.cases.map(c => ({ ...c, suite: s.name })));
            const shown = cases.filter(c => f === 'all' || (f === 'problems' ? ['failed', 'error'].includes(c.status) : c.status === f));
            const passed = t.tests - t.failures - t.errors - t.skipped;
            return `<div class="work-art__tools" role="toolbar" aria-label="Filter tests">
                    <span class="work-add">✓ ${passed} passed</span><span class="${t.failures + t.errors ? 'work-del' : ''}">✗ ${t.failures + t.errors} failed</span>${t.skipped ? `<span class="work-meta">– ${t.skipped} skipped</span>` : ''}
                    <span class="work-spacer"></span>
                    <select class="w-select work-art__status" aria-label="Show" data-art-status>${[['problems', 'Failures'], ['all', 'All tests'], ['passed', 'Passed'], ['skipped', 'Skipped']].map(([k, l]) => `<option value="${k}" ${k === f ? 'selected' : ''}>${l}</option>`).join('')}</select></div>
                <div class="w-table-wrap"><table class="w-table work-art__cases"><thead><tr><th scope="col">Test</th><th scope="col">Result</th><th scope="col" data-num>Time</th></tr></thead><tbody>
                ${shown.slice(0, 2000).map(c => `<tr><td data-mono><span class="work-meta">${esc(c.classname || c.suite)}${c.classname || c.suite ? '.' : ''}</span>${esc(c.name)}
                    ${c.message || c.detail ? `<details class="work-art__case"><summary>${esc(c.message || 'details')}</summary><pre class="work-log">${esc(c.detail)}</pre>${c.stdout ? `<pre class="work-log">${esc(c.stdout)}</pre>` : ''}</details>` : ''}</td>
                    <td>${c.status === 'passed' ? '<span class="work-add">✓ passed</span>' : c.status === 'skipped' ? '<span class="work-meta">– skipped</span>' : `<span class="work-del">✗ ${esc(c.status)}</span>`}</td>
                    <td data-num class="work-meta">${c.time != null ? `${c.time.toFixed(2)} s` : ''}</td></tr>`).join('') || '<tr><td colspan="3" class="work-empty-line">No test matches.</td></tr>'}
                </tbody></table></div>${shown.length > 2000 ? `<p class="work-meta">First 2,000 of ${shown.length} shown.</p>` : ''}`;
        },
        html(p, state) {
            return `<iframe class="work-art__frame" sandbox="" referrerpolicy="no-referrer" title="${esc(p.name || p.path)}" src="${esc(rawUrl(state.jobId, state.run, p.path, true))}"></iframe>
                <p class="work-meta">Shown without scripts. Download it to open it in full.</p>`;
        },
        image(p, state) {
            return `<div class="work-art__image"><img alt="${esc(p.name || p.path)}" src="${esc(rawUrl(state.jobId, state.run, p.path))}"></div>`;
        },
        table(p) {
            const [head, ...rows] = p.rows;
            return `<div class="w-table-wrap work-art__grid"><table class="w-table"><thead><tr>${(head || []).map(h => `<th scope="col">${esc(h)}</th>`).join('')}</tr></thead>
                <tbody>${rows.map(r => `<tr>${r.map(c => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>${p.truncated ? '<p class="work-meta">First 1,000 rows shown.</p>' : ''}`;
        },
        packets(p, state) {
            const v = state.view;
            const protos = Object.entries(p.protocols || {});
            return `<div class="work-art__tools" role="toolbar" aria-label="Filter packets">
                    <span class="work-meta">${p.total.toLocaleString()} packets · ${esc(p.format)}</span>
                    <span class="work-art__protos">${protos.slice(0, 10).map(([k, n]) => `<button type="button" class="w-textbtn" data-art-proto="${esc(k)}" aria-pressed="${v.proto === k}">${esc(k)} <span class="w-count">${n}</span></button>`).join('')}</span>
                    <input class="w-input work-art__find" type="search" placeholder="Filter: address, port, text…" aria-label="Filter packets" value="${esc(v.find || '')}" data-art-pfind></div>
                <div class="w-table-wrap work-art__grid"><table class="w-table work-art__packets"><thead><tr><th scope="col" data-num>No.</th><th scope="col" data-num>Time</th><th scope="col">Source</th><th scope="col">Destination</th><th scope="col">Protocol</th><th scope="col" data-num>Length</th><th scope="col">Info</th></tr></thead>
                <tbody data-art-ptable></tbody></table></div>${p.truncated ? `<p class="work-meta">First ${p.packets.length.toLocaleString()} packets shown. Download the capture for Wireshark.</p>` : ''}`;
        },
        talks(p) {
            return `<div class="w-table-wrap work-art__grid"><table class="w-table"><thead><tr><th scope="col">Endpoint A</th><th scope="col">Endpoint B</th><th scope="col">Protocol</th><th scope="col" data-num>Packets</th><th scope="col" data-num>Bytes</th></tr></thead>
                <tbody>${(p.conversations || []).map(t => `<tr><td data-mono>${esc(t.a)}</td><td data-mono>${esc(t.b)}</td><td>${esc(t.proto)}</td><td data-num>${t.packets}</td><td data-num>${size(t.bytes)}</td></tr>`).join('') || '<tr><td colspan="5" class="work-empty-line">No IP conversations.</td></tr>'}</tbody></table></div>`;
        },
        archive(p) {
            return `<div class="w-table-wrap work-art__grid"><table class="w-table"><thead><tr><th scope="col">${esc(p.archive || 'archive')} entry</th><th scope="col" data-num>Size</th></tr></thead>
                <tbody>${p.entries.map(e => `<tr><td data-mono>${esc(e.name)}</td><td data-num class="work-meta">${e.dir ? '' : size(e.bytes || 0)}</td></tr>`).join('')}</tbody></table></div>${p.truncated ? '<p class="work-meta">First 2,000 entries shown.</p>' : ''}`;
        },
        hex(p) {
            return `<pre class="work-art__hex">${esc(p.hex || '')}</pre><p class="work-meta">The first 4 KB. Download the file for all of it.</p>`;
        },
    };

    const BIND = {
        text(viewer, p, state) {
            const lines = String(p.text || '').split('\n');
            const box = viewer.querySelector('[data-art-text]');
            const count = viewer.querySelector('[data-art-count]');
            const paint = () => {
                const find = (state.view.find || '').toLowerCase();
                const rows = [];
                for (let i = 0; i < lines.length; i++) {
                    const line = lines[i];
                    const bad = LEVEL.test(line), warn = !bad && WARN.test(line);
                    if (state.view.problems && !bad && !warn) continue;
                    if (find && !line.toLowerCase().includes(find)) continue;
                    rows.push(`<div class="work-art__line"${bad ? ' data-level="error"' : warn ? ' data-level="warn"' : ''} data-line="${i + 1}"><span class="work-art__ln" aria-hidden="true">${i + 1}</span><span class="work-art__lt">${esc(line) || ' '}</span></div>`);
                    if (rows.length >= 20000) break;
                }
                box.innerHTML = rows.join('') || '<p class="work-meta">No line matches.</p>';
                count.textContent = find || state.view.problems ? `${rows.length.toLocaleString()} of ${lines.length.toLocaleString()} lines` : `${lines.length.toLocaleString()} lines`;
            };
            paint();
            const find = viewer.querySelector('[data-art-find]');
            find.oninput = () => { state.view.find = find.value; paint(); };
            viewer.querySelector('[data-art-problems]').onchange = e => { state.view.problems = e.target.checked; paint(); };
            viewer.querySelector('[data-art-wrap]').onchange = e => { state.view.wrap = e.target.checked; box.classList.toggle('is-wrapped', e.target.checked); };
            viewer.querySelector('[data-art-first]').onclick = () => {
                const hit = box.querySelector('[data-level="error"]');
                if (hit) { hit.scrollIntoView({ block: 'center' }); hit.classList.add('is-flash'); setTimeout(() => hit.classList.remove('is-flash'), 1200); }
                else window.WashToast?.show('No error lines found', 'info');
            };
        },
        junit(viewer, p, state, entry) {
            viewer.querySelector('[data-art-status]').onchange = e => { state.view.status = e.target.value; paintViewer(entry); };
        },
        packets(viewer, p, state, entry) {
            const body = viewer.querySelector('[data-art-ptable]');
            const paint = () => {
                const find = (state.view.find || '').toLowerCase();
                const rows = p.packets.filter(k => (!state.view.proto || k.proto === state.view.proto)
                    && (!find || `${k.src} ${k.dst} ${k.proto} ${k.info}`.toLowerCase().includes(find)));
                body.innerHTML = rows.slice(0, 5000).map(k => `<tr data-proto="${esc(k.proto)}"><td data-num class="work-meta">${k.no}</td><td data-num class="work-meta">${k.time.toFixed(6)}</td><td data-mono>${esc(k.src)}</td><td data-mono>${esc(k.dst)}</td><td>${esc(k.proto)}</td><td data-num>${k.length}</td><td data-mono>${esc(k.info)}</td></tr>`).join('')
                    || '<tr><td colspan="7" class="work-empty-line">No packet matches.</td></tr>';
            };
            paint();
            const find = viewer.querySelector('[data-art-pfind]');
            find.oninput = () => { state.view.find = find.value; paint(); };
            viewer.querySelectorAll('[data-art-proto]').forEach(b => { b.onclick = () => { state.view.proto = state.view.proto === b.dataset.artProto ? '' : b.dataset.artProto; paintViewer(entry); }; });
        },
    };

    window.MuArtifacts = { mount, _cache: cache };
})();
