/* Jobs mode — build environments (documentation/plans/job_build_environments.md §10).
 *
 * Shared by the New job sheet (work.js), the job page (work/view.js) and Settings (work/rules.js):
 * the resolved-set summary, the editor dialog (environment / template / image per row, plus
 * paths, Docker and GPUs) and the job page panel. Data: GET /api/jobs/environments
 * (sources + resolved preview + Docker-in-Docker/GPU status + kept jobs); job VM
 * `build_environments`; commands setEnvironments / refreshEnvironments / keepEnvironments.
 */
(() => {
    'use strict';
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    // Wash chip tones (G§9.2): the job vocabulary's warn is attention; muted is the plain chip.
    const TONE = {warn:'attention', muted:''};
    const toneAttr = tone => { const t = TONE[tone] ?? tone; return t ? ` data-tone="${t}"` : ''; };
    const pill = (tone, html, title = '') => `<span class="w-chip"${toneAttr(tone)}${title ? ` title="${esc(title)}"` : ''}>${html}</span>`;
    const SOURCE_LABEL = {job:'this job', repo:'repo config', session:'session', settings:'settings', default:'default runtime', error:'error'};
    const STATUS = {ready:['ok', '✓', 'ready'], preparing:['running', '●', 'preparing'], failed:['danger', '✗', 'failed'], cleaned:['muted', '○', 'cleaned']};
    const ACTIONS = new Set(['setEnvironments', 'refreshEnvironments', 'keepEnvironments']);
    // Template job setup (job_templates.md §4): the binding's setup status and per-command progress.
    const SETUP = {running:['running', '●', 'job setup running'], ok:['ok', '✓', 'job setup done'], failed:['danger', '✗', 'job setup failed'],
        skipped:['muted', '–', 'job setup skipped'], pending:['muted', '○', 'job setup pending']};
    function setupBlock(x) {
        const setup = x.setup || {};
        const commands = (setup.commands || []).length ? setup.commands : (x.job_setup || []).map((command, index) => ({index, command, status:'pending'}));
        if (!commands.length) return '';
        const [tone, glyph, word] = SETUP[setup.status || 'pending'] || SETUP.pending;
        const rows = commands.map(c => { const [t, g] = SETUP[c.status] || SETUP.pending;
            return `<li class="work-setup-cmd" data-setup-index="${esc(c.index)}" data-setup-status="${esc(c.status || 'pending')}"><span class="w-status" data-tone="${t === 'muted' ? 'neutral' : t}">${c.status === 'running' ? '<span class="w-inkdrop" data-size="sm" aria-hidden="true"></span>' : `<span class="w-status__glyph" aria-hidden="true">${g}</span>`}<span class="w-sr-only">${esc(c.status || 'pending')}</span></span><code>${esc(c.command)}</code>${c.exit_code != null && c.status !== 'ok' ? `<span class="work-meta">exit ${esc(c.exit_code)}</span>` : ''}</li>`; }).join('');
        return `<div class="work-stack-tight" data-env-setup="${esc(setup.status || 'pending')}"><div class="work-toolbar">${pill(tone, `<span aria-hidden="true" class="w-chip__glyph"${setup.status === 'running' ? ' data-state="active"' : ''}>${glyph}</span><span>${esc(word)}</span>`)}
            ${x.source === 'template' ? `<a class="work-meta" href="/containers#template-${encodeURIComponent(x.ref)}">Edit template</a>` : ''}</div>
            <ol class="work-setup-cmds" aria-label="Job setup commands of ${esc(x.name)}">${rows}</ol>
            ${(setup.files || []).length ? `<p class="work-meta" data-tone="attention">Setup left ${setup.files.length} file${setup.files.length === 1 ? '' : 's'} git does not ignore (checks ignore them): ${esc(setup.files.slice(0, 6).join(', '))}${setup.files.length > 6 ? '…' : ''}</p>` : ''}
            ${setup.status === 'failed' && setup.error ? `<pre class="work-log">${esc(setup.error)}</pre>` : ''}</div>`;
    }

    function bytes(n) {
        const v = Number(n || 0);
        if (!v) return '';
        const units = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.min(units.length - 1, Math.floor(Math.log(v) / Math.log(1024)));
        return `${(v / 1024 ** i).toFixed(i >= 3 ? 1 : 0)} ${units[i]}`;
    }

    /* ``rust-build → resq-backend, stream-processor-rs`` · ``compose-it ⬢ docker`` */
    function label(b) {
        const paths = Array.isArray(b.paths) ? b.paths : String(b.paths || '').split(',').map(p => p.trim()).filter(Boolean);
        return `${b.name || b.ref || b.env || ''}${paths.length ? ` → ${paths.join(', ')}` : ''}${b.docker ? ' ⬢ docker' : ''}${b.gpus ? ` · gpus ${b.gpus}` : ''}`;
    }
    function summary(bindings) { return (bindings || []).map(b => b.label || label(b)).join(' · '); }

    /* A binding (VM / preview shape) → the editor row / setEnvironments payload shape. */
    function toRow(b) {
        const spec = b.env || (b.source && b.ref ? `${b.source}:${b.ref}` : b.ref || '');
        return {env:spec, as:b.name || '', paths:(b.paths || []).join(', '), docker:!!b.docker, primary:!!b.primary,
            gpus:b.gpus ? String(b.gpus) : ''}; // blank = inherit from the source environment
    }
    function toBinding(row) {
        const out = {env:row.env.trim(), paths:row.paths, docker:!!row.docker, primary:!!row.primary};
        if (row.as) out.as = row.as;
        if (String(row.gpus || '').trim()) out.gpus = row.gpus.trim();
        return out;
    }
    function rowName(row) {
        if (row.as) return row.as;
        const [head, ...rest] = row.env.split(':');
        const ref = ['template', 'image', 'environment', 'env'].includes(head) ? rest.join(':') : row.env;
        return head === 'image' ? ref.split('@')[0].split('/').pop().split(':')[0] : ref.replace(/^mucli-/, '');
    }

    async function sources(api, {repository = '', session = '', images = true} = {}) {
        const q = new URLSearchParams({repository, session, images:images ? 'true' : 'false'});
        return api(`/api/jobs/environments?${q}`);
    }

    /* Editor dialog. Resolves to a list of bindings (``[]`` = no override), or null on cancel. */
    function editor({api, ui, title = 'Build environments', initial = [], context = {}, allowClear = true, clearLabel = 'Use default'}) {
        return new Promise(resolve => {
            let result = null;
            let rows = initial.map(toRow);
            if (!rows.length) rows = [{env:'', as:'', paths:'', docker:false, primary:false, gpus:''}];
            const listId = `work-env-sources-${Math.random().toString(36).slice(2, 8)}`;
            const form = document.createElement('form');
            form.className = 'work-stack-tight work-env-editor';
            form.noValidate = true;
            const foot = document.createElement('div');
            foot.className = 'w-btn-row';
            foot.innerHTML = `${allowClear ? `<button type="button" class="w-btn" data-variant="ghost" data-env-clear>${esc(clearLabel)}</button>` : ''}<span class="work-spacer"></span><button type="button" class="w-btn" data-variant="secondary" data-cancel>Cancel</button><button type="button" class="w-btn" data-variant="primary" data-env-save>Save</button>`;
            let info = null;
            const draw = () => {
                const dind = info?.docker_in_docker, gpu = info?.gpu;
                form.innerHTML = `<p class="work-meta">Each job snapshots these at start and removes the images when it ends. Source: a container-mode environment, <code>template:&lt;name&gt;</code> or <code>image:&lt;ref&gt;</code>. Paths are repository-relative components (blank = whole repository).</p>
                    <datalist id="${listId}">${(info?.environments || []).map(e => `<option value="${esc(e.spec)}">environment · ${esc(e.status || '')}${e.gpus ? ` · gpus ${esc(e.gpus)}` : ''}</option>`).join('')}
                        ${(info?.templates || []).map(t => `<option value="${esc(t.spec)}">template${t.description ? ` · ${esc(t.description)}` : ''}</option>`).join('')}
                        ${(info?.images || []).map(i => `<option value="${esc(i.spec)}">image · ${esc(i.size || '')}</option>`).join('')}</datalist>
                    <div class="w-table-wrap"><table class="w-table work-env-table"><thead><tr><th scope="col">Source</th><th scope="col">Paths</th><th scope="col">Docker</th><th scope="col">Primary</th><th scope="col">GPUs</th><th scope="col"><span class="w-sr-only">Remove</span></th></tr></thead><tbody>
                    ${rows.map((r, i) => `<tr data-env-row="${i}">
                        <td><input type="text" list="${listId}" data-env-key="env" aria-label="Source of row ${i + 1}" placeholder="rust-build · template:x · image:rust:1.89" value="${esc(r.env)}" class="w-input work-env-source"></td>
                        <td><input type="text" data-env-key="paths" aria-label="Paths of row ${i + 1}" placeholder="whole repository" value="${esc(r.paths)}" class="w-input"></td>
                        <td><input type="checkbox" class="w-check__input" data-env-key="docker" aria-label="Docker engine for row ${i + 1}" ${r.docker ? 'checked' : ''}></td>
                        <td><input type="checkbox" class="w-check__input" data-env-key="primary" aria-label="Primary environment (row ${i + 1})" ${r.primary ? 'checked' : ''}></td>
                        <td><input type="text" data-env-key="gpus" aria-label="GPUs of row ${i + 1}" placeholder="inherit" value="${esc(r.gpus)}" class="w-input work-env-gpus"></td>
                        <td><button type="button" class="w-btn" data-variant="icon" data-size="sm" data-tone="danger" data-env-remove="${i}" aria-label="Remove row ${i + 1}" title="Remove row ${i + 1}">×</button></td></tr>`).join('')}
                    </tbody></table></div>
                    <div class="work-toolbar"><button type="button" class="w-btn" data-variant="secondary" data-size="sm" data-env-add>+ Add environment</button><span class="work-spacer"></span>
                        ${info ? `<span class="work-meta"${dind?.available ? ' data-tone="ok"' : ''} title="${esc(dind?.message || '')}">Docker-in-Docker: ${dind?.available ? 'Sysbox available' : 'needs Sysbox'}</span>
                        <span class="work-meta"${gpu?.supported ? ' data-tone="ok"' : ''} title="${esc(gpu?.reason || '')}">GPUs: ${gpu?.supported ? 'available' : 'unavailable'}</span>` : '<span class="work-meta">Loading sources…</span>'}</div>
                    <span class="work-meta" data-tone="danger" data-env-error></span>`;
                form.querySelectorAll('[data-env-remove]').forEach(b => { b.onclick = () => { read(); rows.splice(Number(b.dataset.envRemove), 1); draw(); }; });
                form.querySelector('[data-env-add]').onclick = () => { read(); rows.push({env:'', as:'', paths:'', docker:false, primary:false, gpus:''}); draw(); form.querySelector(`[data-env-row="${rows.length - 1}"] [data-env-key="env"]`)?.focus(); };
            };
            const read = () => {
                rows = [...form.querySelectorAll('[data-env-row]')].map((tr, i) => {
                    const v = k => tr.querySelector(`[data-env-key="${k}"]`);
                    return {env:v('env').value.trim(), as:rows[i]?.env === v('env').value.trim() ? rows[i].as : '', paths:v('paths').value.trim(),
                        docker:v('docker').checked, primary:v('primary').checked, gpus:v('gpus').value.trim()};
                });
            };
            draw();
            const d = ui.dialog({title, body:form, footer:foot, wide:true, onClose:() => resolve(result)});
            sources(api, context).then(data => { info = data; read(); draw(); }).catch(() => { info = {}; read(); draw(); });
            foot.querySelector('[data-cancel]').onclick = () => d.close();
            foot.querySelector('[data-env-clear]')?.addEventListener('click', () => { result = []; d.close(); });
            foot.querySelector('[data-env-save]').onclick = () => {
                read();
                const chosen = rows.filter(r => r.env);
                if (!chosen.length) { result = []; d.close(); return; }
                const names = chosen.map(rowName);
                const dup = names.find((n, i) => names.indexOf(n) !== i);
                if (dup) { form.querySelector('[data-env-error]').textContent = `Two rows are both named ${dup}; give one of them a different source.`; return; }
                result = chosen.map(toBinding).map(b => ({...b, label:label({...b, name:b.as || rowName(b)})}));
                d.close();
            };
        });
    }

    /* Job page header pill (``⬢ 3 environments`` / ``⬢ kept`` / status tint). */
    function headerPill(summaryInfo, open) {
        const s = summaryInfo || {};
        const count = Number(s.count || 0);
        const tone = s.status === 'failed' ? 'danger' : s.status === 'preparing' ? 'running' : count ? 'accent' : 'muted';
        const text = count ? `${count} environment${count === 1 ? '' : 's'}` : 'shared runtime';
        const title = count ? `${s.summary || ''}${s.source ? ` (from ${SOURCE_LABEL[s.source] || s.source})` : ''}` : 'Stages run in the shared mucli-job-runtime image';
        return `<button type="button" class="w-chip work-env-pill"${toneAttr(tone)} data-env-pill aria-expanded="${!!open}" aria-controls="work-env-panel" title="${esc(title)}"><span aria-hidden="true" class="w-chip__glyph"${s.status === 'preparing' ? ' data-state="active"' : ''}>⬢</span><span>${esc(text)}${s.kept ? ' · kept' : ''}</span></button>`;
    }

    /* Job page panel: one row per binding plus Refresh (E) / Keep (K) / Change. */
    function panel(info, buttons) {
        const b = info || {bindings:[]};
        const rows = (b.bindings || []).map(x => {
            const [tone, glyph, word] = STATUS[x.status] || ['muted', '○', x.status || 'not prepared'];
            const probe = (x.probed_env || []).length ? `probed ${x.probed_env.join(', ')}` : '';
            return `<li class="work-env-binding work-stack-tight" data-env-binding="${esc(x.name)}">
                <div class="work-toolbar">${pill(tone, `<span aria-hidden="true" class="w-chip__glyph"${x.status === 'preparing' ? ' data-state="active"' : ''}>${glyph}</span><span>${esc(word)}</span>`)}
                    <strong class="work-mono">${esc(x.name)}</strong><span class="work-meta">${esc(x.source)} <code>${esc(x.ref)}</code></span>
                    ${x.primary ? pill('accent', 'primary') : ''}${x.docker ? pill('accent', `<span aria-hidden="true">⬢</span> docker${x.engine ? ` · ${esc(x.engine)}` : ''}`) : ''}${x.gpus ? pill('accent', `gpus ${esc(x.gpus)}`) : ''}
                    <span class="work-spacer"></span>${x.image_bytes ? `<span class="work-caption work-num" title="job image size">${esc(bytes(x.image_bytes))}</span>` : ''}</div>
                <div class="work-meta">${esc((x.paths || []).length ? x.paths.join(', ') : 'whole repository')}${probe ? ` · ${esc(probe)}` : ''}${x.home_bytes ? ` · home ${esc(bytes(x.home_bytes))}` : ''}</div>
                ${setupBlock(x)}
                ${x.error ? `<div class="w-banner work-banner-sm" data-tone="danger" role="alert"><span class="w-banner__glyph" aria-hidden="true">✗</span><p class="w-banner__msg">${esc(x.error)}</p></div>` : ''}
                ${(x.secret_paths || []).length ? `<details open><summary class="work-meta" data-tone="danger">Secret scan blocked ${x.secret_paths.length} path${x.secret_paths.length === 1 ? '' : 's'}</summary><ul class="work-caption work-mono">${x.secret_paths.map(p => `<li>${esc(p)}</li>`).join('')}</ul></details>` : ''}
                ${(x.skipped_paths || []).length ? `<details><summary class="work-meta">${x.skipped_paths.length} home path${x.skipped_paths.length === 1 ? '' : 's'} left out of the snapshot</summary><ul class="work-meta work-mono">${x.skipped_paths.map(p => `<li>${esc(p)}</li>`).join('')}</ul></details>` : ''}</li>`;
        }).join('');
        const src = b.source ? `<span class="work-meta">from ${esc(SOURCE_LABEL[b.source] || b.source)}</span>` : '';
        return `<section class="w-card work-env-panel" id="work-env-panel" aria-labelledby="work-env-h"><header class="w-card__head"><h2 class="w-card__title" id="work-env-h"><span aria-hidden="true">⬢</span> Build environments</h2>${src}
                ${b.kept ? pill('accent', '<span aria-hidden="true">⬢</span> kept', 'Exempt from idle retention') : ''}${b.refresh_pending ? pill('warn', 'refresh pending') : ''}
                ${b.bytes ? `<span class="work-meta work-num">${esc(bytes(b.bytes))}</span>` : ''}<span class="work-spacer"></span>
                <div class="work-toolbar" role="toolbar" aria-label="Build environment actions">${buttons || ''}</div></header>
            <div class="w-card__body">${rows ? `<ul class="work-list">${rows}</ul>` : '<div class="w-empty work-empty-inline"><p class="w-empty__body">No build environments: stages run in the shared <code>mucli-job-runtime</code> image. Use Change to pick environments.</p></div>'}</div></section>`;
    }

    /* Session tab: ``› [node-build] npm test`` for run_in_environment calls. */
    function toolSummary(name, args) {
        if (name !== 'run_in_environment') return null;
        let a = args;
        if (typeof a === 'string') { try { a = JSON.parse(a); } catch (_) { a = {}; } }
        a = a || {};
        return `[${a.env || '?'}] ${a.command || ''}${a.cwd && a.cwd !== '.' ? `  (in ${a.cwd})` : ''}`;
    }

    window.MuJobEnvironments = {ACTIONS, SOURCE_LABEL, bytes, label, summary, sources, editor, headerPill, panel, toolSummary, toRow, setupBlock};
})();
