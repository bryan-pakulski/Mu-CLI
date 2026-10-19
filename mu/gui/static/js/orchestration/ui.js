/* Orchestration UI primitives on the Wash components.
 *
 * Everything that reaches innerHTML goes through esc(); server styles
 * ({status, glyph, semantic, label} from mu/orchestration/vm.py) become
 * status chips (glyph + label, never colour alone). Dialogs are .w-modal
 * surfaces on the shared dialog stack (static/js/dialogs.js: focus trap,
 * Esc, focus restore); destructive confirms use WashConfirm (Cancel gets the
 * default focus); toasts go through WashToast's live regions.
 *
 * Actions (mu/orchestration/actions.py ActionVM) render as buttons: hidden
 * ones are skipped, disabled ones stay focusable with aria-disabled and the
 * reason as their title, `confirm` asks first, `input` prompts (with
 * `expect` the operator must type that exact value), `dialog` hands over to
 * a page dialog (labels | test | run).
 */
(() => {
    'use strict';
    const api = window.OrchAPI;
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
    const icon = (name, size) => `<svg class="w-icon"${size ? ` data-size="${esc(size)}"` : ''} aria-hidden="true" focusable="false"><use href="/static/icons/wash.svg#i-${esc(name)}"></use></svg>`;
    let seq = 0;
    const uid = prefix => `${prefix}-${++seq}`;

    /* ------------------------------------------------------------ formatting */
    function ago(ts) {
        if (!ts) return 'never';
        const d = Math.max(0, Date.now() / 1000 - Number(ts));
        if (d < 60) return `${Math.floor(d)}s ago`;
        if (d < 3600) return `${Math.floor(d / 60)}m ago`;
        if (d < 86400) return `${Math.floor(d / 3600)}h ago`;
        return `${Math.floor(d / 86400)}d ago`;
    }
    function when(ts) {
        if (!ts) return '—';
        const d = new Date(Number(ts) * 1000);
        return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    }
    const time = ts => `<time datetime="${esc(ts ? new Date(Number(ts) * 1000).toISOString() : '')}" title="${esc(when(ts))}">${esc(ago(ts))}</time>`;
    function dur(s) {
        if (s == null || s === '') return '—';
        const n = Number(s);
        if (n < 1) return `${Math.round(n * 1000)} ms`;
        if (n < 60) return `${n.toFixed(n < 10 ? 1 : 0)} s`;
        return `${Math.floor(n / 60)}m ${Math.round(n % 60)}s`;
    }
    const plural = (n, word, many) => `${n} ${n === 1 ? word : (many || `${word}s`)}`;
    const cap = s => { s = String(s || ''); return s ? s[0].toUpperCase() + s.slice(1) : s; };
    const json = value => JSON.stringify(value, null, 2);

    /* ------------------------------------------------------------ marks (Mu §5.4) */
    /* Status is a sign plus a word, never a pill: the sign is the vocabulary status key
     * (waiting ◆, active ●, failed ✗, done ✓, pending ○ …); colour only where it asks for you. */
    const SEMANTIC_STATUS = { ok: 'done', running: 'active', attention: 'waiting', danger: 'failed', caution: 'unverified', neutral: 'pending', plan: 'pending', info: 'pending' };
    const statusKey = style => (style && (style.status || SEMANTIC_STATUS[style.semantic])) || 'pending';
    function sign(style) {
        const st = statusKey(style);
        const glyph = style?.glyph || ((window.WASH?.vocabulary?.status || {})[st] || {}).glyph || '○';
        return `<span class="w-sign" data-status="${esc(st)}" aria-hidden="true">${esc(glyph)}</span>`;
    }
    function chip(style, { label, title } = {}) {
        if (!style) return '';
        const text = label || cap(style.label || style.status || '');
        return `<span class="w-signword orch-mark" data-status="${esc(statusKey(style))}"${title ? ` title="${esc(title)}"` : ''}>${sign(style)}${esc(text)}</span>`;
    }
    /* Effects are words (Mu §5.4): destructive in danger ink, the rest quiet. */
    function effectChip(es, effect) {
        if (!es) return '';
        return `<span class="orch-effect" data-tone="${esc(es.semantic)}" data-effect="${esc(effect || '')}" title="Effect: ${esc(es.label)}">${esc(cap(es.label))}</span>`;
    }
    const effectWord = (es, effect) => (es ? `<span class="orch-effect" data-tone="${esc(es.semantic)}" data-effect="${esc(effect || '')}">${esc(es.label)}</span>` : '');
    const meta = (text, title) => `<span class="orch-meta"${title ? ` title="${esc(title)}"` : ''}>${esc(text)}</span>`;
    function labelChips(labels, { empty = '—' } = {}) {
        const entries = Object.entries(labels || {});
        if (!entries.length) return `<span class="orch-muted">${esc(empty)}</span>`;
        return `<span class="orch-labels">${entries.map(([k, v]) => `<span class="orch-label">${esc(k)}=${esc(v)}</span>`).join('')}</span>`;
    }
    /* A style from a WASH vocabulary table ({key: {status, label}}) when the server sent none. */
    function vstyle(tableName, key) {
        const v = window.WASH?.vocabulary || {};
        const row = (v[tableName] || {})[key];
        const st = row ? row.status : 'pending';
        const spec = (v.status || {})[st] || { glyph: '○', semantic: 'neutral' };
        return { status: st, glyph: spec.glyph, semantic: spec.semantic, label: row ? row.label : String(key || '').replace(/_/g, ' ') };
    }
    const status = (style, text) => chip(style, { label: text });

    /* ------------------------------------------------------------ blocks */
    const loading = (text = 'Loading…') => `<div class="orch-loading" role="status"><span class="w-inkdrop" data-tone="running" aria-hidden="true"></span>${esc(text)}</div>`;
    function empty(title, body = '', { icon: ic = 'sparkles', tone = 'accent', action = '', size = 'sm' } = {}) {
        return `<div class="w-empty" data-tone="${esc(tone)}"${size ? ` data-size="${esc(size)}"` : ''}><div class="w-empty__art" aria-hidden="true">${icon(ic)}</div><h3 class="w-empty__title">${esc(title)}</h3>${body ? `<p class="w-empty__body">${esc(body)}</p>` : ''}${action ? `<div class="w-empty__actions">${action}</div>` : ''}</div>`;
    }
    function errorState(err, { retry = true } = {}) {
        return `<div class="w-empty" data-tone="danger" data-variant="error" data-size="sm" role="alert"><div class="w-empty__art" aria-hidden="true">${icon('alert-circle')}</div><h3 class="w-empty__title">Couldn’t load this</h3><p class="w-empty__body">${esc(err?.message || err)}</p>${retry ? `<div class="w-empty__actions"><button type="button" class="w-btn" data-variant="secondary" data-orch-retry>Retry</button></div>` : ''}</div>`;
    }
    function banner(tone, glyph, message, actionHtml = '') {
        return `<div class="w-banner orch-banner" data-tone="${esc(tone)}" role="status"><span class="w-banner__glyph" aria-hidden="true">${esc(glyph)}</span><p class="w-banner__msg">${message}</p>${actionHtml}</div>`;
    }
    function kv(rows) {
        const items = rows.filter(r => r && r[1] !== undefined && r[1] !== null && r[1] !== '');
        return `<dl class="w-facts orch-kv">${items.map(([k, v, raw]) => `<dt>${esc(k)}</dt><dd>${raw ? v : esc(v)}</dd>`).join('')}</dl>`;
    }
    function table({ cols, rows, empty: emptyText = 'Nothing here yet.', stack = true, label = '', cls = '' }) {
        if (!rows.length) return `<p class="orch-muted orch-empty-line">${esc(emptyText)}</p>`;
        const head = cols.map(c => `<th scope="col"${c.num ? ' data-num' : ''}${c.cls ? ` class="${esc(c.cls)}"` : ''}>${c.sr ? `<span class="w-sr-only">${esc(c.label)}</span>` : esc(c.label)}</th>`).join('');
        const body = rows.map(r => {
            const cells = Array.isArray(r) ? r : r.cells;
            const attrs = Array.isArray(r) ? '' : (r.attrs || '');
            return `<tr${attrs}>${cells.map((cell, i) => {
                const c = cols[i] || {};
                return `<td${c.num ? ' data-num' : ''}${c.mono ? ' data-mono' : ''}${c.cls ? ` class="${esc(c.cls)}"` : ''} data-label="${esc(c.label || '')}">${cell ?? ''}</td>`;
            }).join('')}</tr>`;
        }).join('');
        return `<div class="w-table-wrap orch-table-wrap${cls ? ` ${esc(cls)}` : ''}"><table class="w-table orch-table"${stack ? ' data-stack' : ''}${label ? ` aria-label="${esc(label)}"` : ''}><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
    }
    function highlight(text, lang) {
        const hl = window.hljs;
        if (!hl || !lang || !text || text.length > 200000) return esc(text);
        try {
            if (hl.getLanguage && !hl.getLanguage(lang)) return esc(text);
            return hl.highlight(text, { language: lang, ignoreIllegals: true }).value;
        } catch (_) { return esc(text); }
    }
    const LANG_BY_EXT = { sh: 'bash', bash: 'bash', py: 'python', yaml: 'yaml', yml: 'yaml', json: 'json', ps1: 'powershell', conf: 'ini', ini: 'ini', toml: 'ini', md: 'markdown', txt: '' };
    const langFor = name => LANG_BY_EXT[String(name || '').split('.').pop().toLowerCase()] ?? '';
    function code(text, { lang = '', title = '', copy = true, cls = '' } = {}) {
        const label = title || lang || 'text';
        return `<div class="w-code orch-code${cls ? ` ${esc(cls)}` : ''}" data-lang="${esc(lang)}"><div class="w-code__head"><span class="w-code__lang">${esc(label)}</span>${copy ? `<button type="button" class="w-code__copy" data-orch-copy aria-label="Copy ${esc(label)}">${icon('copy', 'sm')}<span class="w-code__copy-label">Copy</span></button>` : ''}</div><pre class="w-code__pre" tabindex="0"><code class="hljs${lang ? ` language-${esc(lang)}` : ''}">${highlight(String(text ?? ''), lang)}</code></pre></div>`;
    }
    async function copyText(text, button) {
        try {
            await navigator.clipboard.writeText(text);
        } catch (_) {
            const ta = document.createElement('textarea');
            ta.value = text; ta.setAttribute('readonly', ''); ta.className = 'w-visually-hidden';
            document.body.append(ta); ta.select();
            try { document.execCommand('copy'); } catch (__) { /* ignore */ }
            ta.remove();
        }
        if (button) {
            button.setAttribute('data-copied', '');
            const lbl = button.querySelector('.w-code__copy-label, span:last-child');
            const old = lbl?.textContent;
            if (lbl) lbl.textContent = 'Copied';
            setTimeout(() => { button.removeAttribute('data-copied'); if (lbl) lbl.textContent = old; }, 1500);
        }
    }
    document.addEventListener('click', e => {
        const btn = e.target.closest('[data-orch-copy]');
        if (!btn) return;
        const value = btn.dataset.copyValue;
        const text = value != null ? value : (btn.closest('.w-code')?.querySelector('code')?.textContent || '');
        copyText(text, btn);
    });

    /* Structured output: objects become key/value rows, arrays of objects tables. */
    function scalar(v) {
        if (v === null || v === undefined) return '<span class="orch-muted">—</span>';
        if (typeof v === 'boolean') return v ? 'yes' : 'no';
        if (typeof v === 'object') return `<code class="orch-inline-json">${esc(JSON.stringify(v))}</code>`;
        return esc(v);
    }
    function outputView(value, depth = 0) {
        if (value === null || value === undefined) return '<p class="orch-muted">No structured output.</p>';
        if (Array.isArray(value)) {
            if (!value.length) return '<p class="orch-muted">Empty list.</p>';
            if (value.every(x => x && typeof x === 'object' && !Array.isArray(x))) {
                const keys = [...new Set(value.flatMap(x => Object.keys(x)))].slice(0, 12);
                return table({ cols: keys.map(k => ({ label: k, num: value.every(x => x[k] == null || typeof x[k] === 'number') })), rows: value.map(x => keys.map(k => scalar(x[k]))), stack: false });
            }
            return `<ul class="orch-list">${value.map(x => `<li>${scalar(x)}</li>`).join('')}</ul>`;
        }
        if (typeof value === 'object') {
            const entries = Object.entries(value);
            if (!entries.length) return '<p class="orch-muted">Empty object.</p>';
            return `<dl class="orch-kv">${entries.map(([k, v]) => {
                const nested = v && typeof v === 'object' && depth < 2 && (Array.isArray(v) ? v.length : Object.keys(v).length);
                return `<div class="orch-kv__row${nested ? ' orch-kv__row--block' : ''}"><dt>${esc(k)}</dt><dd>${nested ? outputView(v, depth + 1) : scalar(v)}</dd></div>`;
            }).join('')}</dl>`;
        }
        return `<p>${scalar(value)}</p>`;
    }

    /* ------------------------------------------------------------ tabs */
    function tabs(id, items, active) {
        return `<div class="w-tabs orch-tabs" role="tablist" aria-label="${esc(id)}">${items.map(t => `<button type="button" class="w-tabs__tab" role="tab" id="orch-tab-${esc(t.id)}" aria-selected="${t.id === active}" aria-controls="orch-panel" tabindex="${t.id === active ? 0 : -1}" data-tab="${esc(t.id)}">${esc(t.label)}${t.count != null && t.count !== '' ? ` <span class="w-count"${t.tone ? ` data-tone="${esc(t.tone)}"` : ''}>${esc(t.count)}</span>` : ''}</button>`).join('')}</div>`;
    }
    function bindTabs(root, onSelect) {
        const list = root.querySelector('[role="tablist"]');
        if (!list) return;
        const all = () => [...list.querySelectorAll('[role="tab"]')];
        list.addEventListener('click', e => { const t = e.target.closest('[role="tab"]'); if (t) onSelect(t.dataset.tab); });
        list.addEventListener('keydown', e => {
            const items = all();
            const i = items.indexOf(document.activeElement);
            if (i < 0) return;
            let next = -1;
            if (e.key === 'ArrowRight') next = (i + 1) % items.length;
            else if (e.key === 'ArrowLeft') next = (i - 1 + items.length) % items.length;
            else if (e.key === 'Home') next = 0;
            else if (e.key === 'End') next = items.length - 1;
            if (next < 0) return;
            e.preventDefault();
            items[next].focus();
            onSelect(items[next].dataset.tab, true);
        });
    }

    /* ------------------------------------------------------------ dialogs */
    function dialog({ title, body = '', footer = '', size = 'sm', onClose, cls = '' } = {}) {
        const opener = document.activeElement;
        const titleId = uid('orch-dlg');
        const backdrop = document.createElement('div');
        backdrop.className = 'w-modal orch-dialog';
        backdrop.innerHTML = `<section class="w-modal__dialog orch-dialog__panel${cls ? ` ${esc(cls)}` : ''}" data-size="${esc(size)}" role="dialog" aria-modal="true" aria-labelledby="${titleId}" tabindex="-1">
            <header class="w-modal__head"><h2 class="w-modal__title" id="${titleId}">${esc(title)}</h2>
            <button type="button" class="w-btn" data-variant="icon" aria-label="Close" title="Close" data-dialog-close>${icon('x')}</button></header>
            <div class="w-modal__body orch-dialog__body"></div>${footer ? '<footer class="w-modal__foot"></footer>' : ''}</section>`;
        const panel = backdrop.firstElementChild;
        const bodyEl = panel.querySelector('.orch-dialog__body');
        if (typeof body === 'string') bodyEl.innerHTML = body; else bodyEl.append(body);
        const foot = panel.querySelector('.w-modal__foot');
        if (foot) { if (typeof footer === 'string') foot.innerHTML = footer; else foot.append(footer); }
        let closed = false;
        const close = () => {
            if (closed) return;
            closed = true;
            window.MuDialog?.close(backdrop);
            removeAfterClose(backdrop);
            if (opener && document.contains(opener)) opener.focus();
            onClose?.();
        };
        backdrop.addEventListener('mousedown', e => { if (e.target === backdrop) close(); });
        backdrop.addEventListener('dialog-close', close);
        backdrop.addEventListener('keydown', e => { if (e.key === 'Escape' && !window.MuDialog) close(); });
        panel.querySelector('[data-dialog-close]').onclick = close;
        backdrop.hidden = true;
        document.body.append(backdrop);
        if (window.MuDialog) window.MuDialog.open(backdrop, { focus: false }); else backdrop.hidden = false;
        const first = panel.querySelector('[data-autofocus]') || panel.querySelector('input:not([type=hidden]):not([disabled]), textarea, select') || panel.querySelector(FOCUSABLE);
        (first || panel).focus();
        return { el: panel, body: bodyEl, foot, close };
    }

    /* Remove a closed (hidden) modal or drawer once its closing animation has played. */
    function removeAfterClose(el) {
        const ms = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--wash-motion-small')) || 200;
        const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
        setTimeout(() => el.remove(), reduced ? 0 : ms + 60);
    }

    function confirm({ title, message, confirmLabel = 'OK', tone = 'primary' }) {
        if (window.WashConfirm) return window.WashConfirm({ title, message, confirmLabel, tone });
        return Promise.resolve(window.confirm(message));
    }

    /* prompt: a labelled input; `expect` requires that exact text (typed confirmation). */
    function prompt({ title, label, message = '', optional = false, multiline = false, okLabel = 'OK', value = '', hint = '', expect = '', danger = false, type = 'text' } = {}) {
        return new Promise(resolve => {
            let result = null;
            const id = uid('orch-prompt');
            const form = document.createElement('form');
            form.className = 'orch-stack';
            form.noValidate = true;
            form.innerHTML = `${message ? `<p class="orch-dialog__msg">${esc(message)}</p>` : ''}<div class="w-field"><label class="w-field__label" for="${id}">${esc(label)}${optional ? ' <span class="orch-muted">(optional)</span>' : ''}</label>
                ${multiline ? `<textarea class="w-textarea" id="${id}" rows="5" data-autofocus>${esc(value)}</textarea>` : `<input class="w-input" id="${id}" type="${esc(type)}" value="${esc(value)}" autocomplete="off" spellcheck="false" data-autofocus${expect ? ' data-mono' : ''}>`}
                ${hint ? `<p class="w-field__hint">${esc(hint)}</p>` : ''}<p class="w-field__error" data-error role="alert"></p></div>
                <div class="w-btn-row orch-dialog__actions"><button type="button" class="w-btn" data-variant="secondary" data-cancel>Cancel</button><button type="submit" class="w-btn" data-variant="${danger ? 'danger' : 'primary'}" data-ok>${esc(okLabel)}</button></div>`;
            const d = dialog({ title, body: form, onClose: () => resolve(result) });
            const input = form.querySelector(`#${id}`);
            const ok = form.querySelector('[data-ok]');
            if (expect) {
                ok.disabled = true;
                input.addEventListener('input', () => { ok.disabled = input.value.trim() !== expect; });
            }
            form.querySelector('[data-cancel]').onclick = () => d.close();
            if (multiline) input.addEventListener('keydown', e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); form.requestSubmit(); } });
            form.onsubmit = e => {
                e.preventDefault();
                const v = input.value;
                const err = form.querySelector('[data-error]');
                if (expect && v.trim() !== expect) { input.setAttribute('aria-invalid', 'true'); err.innerHTML = `<span aria-hidden="true">✗</span> Type ${esc(expect)} exactly.`; input.focus(); return; }
                if (!optional && !v.trim()) { input.setAttribute('aria-invalid', 'true'); err.innerHTML = '<span aria-hidden="true">✗</span> Required.'; input.focus(); return; }
                result = expect ? v.trim() : v;
                d.close();
            };
        });
    }

    const TOAST = { ok: 'ok', danger: 'danger', error: 'danger', attention: 'attention', info: 'info' };
    function toast(text, tone = 'info') {
        const kind = TOAST[tone] || 'info';
        if (window.WashToast) return window.WashToast.show(String(text || ''), kind, { timeout: kind === 'danger' ? 0 : 5000 });
        console.log(text);
        return null;
    }

    /* Busy state for a button while a request runs (G§9.1 loading). */
    async function busy(button, fn) {
        if (button) { button.setAttribute('aria-busy', 'true'); button.disabled = true; }
        try { return await fn(); }
        finally { if (button && document.contains(button)) { button.removeAttribute('aria-busy'); button.disabled = false; } }
    }

    /* ------------------------------------------------------------ rows (Mu §5.1) */
    /* row({status, key, title, href, sub, when, actions, pinned, attrs}) — sub/actions are HTML. */
    function row({ status: st, glyph, key = '', title, titleHtml = '', href = '', sub = '', when: whenHtml = '', actions = '', pinned = false, attrs = '' }) {
        const tag = href ? 'a' : 'div';
        const signHtml = st ? `<span class="w-sign" data-status="${esc(st)}" aria-hidden="true">${esc(glyph || ((window.WASH?.vocabulary?.status || {})[st] || {}).glyph || '○')}</span>` : '';
        return `<li class="w-row"${pinned ? ' data-pinned' : ''}${attrs}>${signHtml}<${tag} class="w-row__main"${href ? ` href="${esc(href)}"` : ''}>`
            + `<span class="w-row__title">${key ? `<span class="w-row__key">${esc(key)}</span>` : ''}${titleHtml || esc(title)}</span>`
            + `${sub ? `<span class="w-row__sub">${sub}</span>` : ''}</${tag}>`
            + `${whenHtml ? `<span class="w-row__when">${whenHtml}</span>` : ''}${actions ? `<span class="w-row__actions">${actions}</span>` : ''}</li>`;
    }
    const rows = (items, { empty: emptyText = '', label = '' } = {}) => (items.length
        ? `<ul class="w-rows"${label ? ` aria-label="${esc(label)}"` : ''}>${items.join('')}</ul>`
        : (emptyText ? `<p class="w-group__empty">${esc(emptyText)}</p>` : ''));
    /* A state sentence as HTML (Mu §4.2): parts {text, tone?} → .w-state__part spans (the
     * separators are drawn by mu.css); falsy parts dropped. */
    const stateHtml = parts => (parts || []).filter(Boolean).map(p => (typeof p === 'string' ? { text: p } : p))
        .map(p => `<span class="w-state__part"${p.tone ? ` data-tone="${esc(p.tone)}"` : ''}>${esc(p.text)}</span>`).join('');
    /* A sub line: parts joined by " · ", falsy parts dropped (parts are HTML). */
    const subline = parts => parts.filter(Boolean).join(' · ');

    /* ------------------------------------------------------------ actions */
    const ACTION_ICONS = {
        acceptNode: 'check', quarantineNode: 'shield', unquarantineNode: 'shield-check', revokeNode: 'circle-x',
        setNodeLabels: 'pencil', setNodeCanary: 'flask-conical', rotateCredential: 'refresh-cw', refreshFacts: 'refresh-cw',
        lintDraft: 'list-checks', testDraft: 'flask-conical', submitReview: 'send', requestChanges: 'corner-down-left',
        publishVersion: 'shield-check', deprecateVersion: 'archive', undeprecateVersion: 'rotate-ccw', retireVersion: 'archive',
        newVersion: 'plus', discardDraft: 'trash-2', runTool: 'play', runRunbook: 'play',
        approveRun: 'check', rejectRun: 'x', continueRun: 'play', pauseRun: 'pause', rollbackRun: 'rotate-ccw', cancelRun: 'circle-x',
    };
    function actionButtons(actions, { size = '', only = null, exclude = [], showDisabled = true, primaryLast = false } = {}) {
        const order = (actions || []).map((a, i) => [a, i]);
        if (primaryLast) order.sort((x, y) => (x[0].primary && x[0].enabled ? 1 : 0) - (y[0].primary && y[0].enabled ? 1 : 0));
        return order.map(([a, i]) => {
            if (a.hidden || exclude.includes(a.id) || (only && !only.includes(a.id))) return '';
            if (!a.enabled && !showDisabled) return '';
            const variant = a.primary && a.enabled ? 'primary' : (a.danger && a.enabled ? 'secondary' : (a.tone && a.enabled ? 'tonal' : 'secondary'));
            const tone = a.tone && a.enabled && variant === 'tonal' ? ` data-tone="${esc(a.tone === 'warn' ? 'attention' : a.tone)}"` : '';
            const disabled = a.enabled ? '' : ` aria-disabled="true" title="${esc(a.reason || 'Not available now')}"`;
            const ic = ACTION_ICONS[a.id];
            return `<button type="button" class="w-btn orch-action" data-variant="${variant}"${tone}${size ? ` data-size="${esc(size)}"` : ''} data-action-index="${i}" data-action="${esc(a.id)}"${a.danger && a.enabled ? ' data-danger' : ''}${disabled}>${ic ? icon(ic) : ''}<span>${esc(a.label)}</span></button>`;
        }).join('');
    }
    /* Row actions (Mu §5.1): text buttons in `only` order; the primary one in ink, danger in
     * danger ink, the rest quiet. Same data-action-index contract, so bindActions/runAction (typed
     * confirmation, prompts) apply unchanged. */
    function rowActions(actions, { only = null, exclude = [], primaryLast = true } = {}) {
        const order = (actions || []).map((a, i) => [a, i]);
        if (only) order.sort((x, y) => only.indexOf(x[0].id) - only.indexOf(y[0].id));
        else if (primaryLast) order.sort((x, y) => (x[0].primary && x[0].enabled ? 1 : 0) - (y[0].primary && y[0].enabled ? 1 : 0));
        return order.map(([a, i]) => {
            if (a.hidden || !a.enabled || exclude.includes(a.id) || (only && !only.includes(a.id))) return '';
            const tone = a.primary ? '' : (a.danger ? ' data-tone="danger"' : ' data-tone="quiet"');
            return `<button type="button" class="w-textbtn orch-action" data-action-index="${i}" data-action="${esc(a.id)}"${a.danger ? ' data-danger' : ''}${tone}>${esc(a.label)}</button>`;
        }).join('');
    }
    /* Enabled actions as buttons; unavailable ones fold into "N unavailable" with their reasons visible. */
    function actionBar(actions, opts = {}) {
        const html = actionButtons(actions, { ...opts, showDisabled: false });
        const off = (actions || []).filter(a => !a.hidden && !a.enabled && !(opts.exclude || []).includes(a.id) && (!opts.only || opts.only.includes(a.id)));
        const more = off.length ? `<details class="orch-more"><summary class="w-btn" data-variant="ghost" data-size="sm">${esc(off.length)} unavailable</summary><ul class="orch-more__list">${off.map(a => `<li><span class="orch-more__label">${esc(a.label)}</span> <span class="orch-muted">${esc(a.reason || 'not available now')}</span></li>`).join('')}</ul></details>` : '';
        return html || more ? `<div class="w-btn-row orch-actions" role="group" aria-label="${esc(opts.label || 'Actions')}">${html}${more}</div>` : '';
    }

    /* ctx: {args, dialogs: {name: (action, ctx) => Promise}, overrides: {commandId: fn},
     *       done(result, action), subject} */
    function bindActions(root, actions, ctx) {
        root.querySelectorAll('[data-action-index]').forEach(btn => {
            if (btn.dataset.bound) return;
            btn.dataset.bound = '1';
            btn.addEventListener('click', () => {
                const action = (actions || [])[Number(btn.dataset.actionIndex)];
                if (action && action.id === btn.dataset.action) runAction(action, ctx, btn);
            });
        });
    }

    async function runAction(action, ctx = {}, button = null) {
        if (!action.enabled) { toast(`${action.label}: ${action.reason || 'not available now'}`, 'attention'); return null; }
        if (ctx.overrides?.[action.id]) return ctx.overrides[action.id](action, ctx);
        if (action.dialog) {
            const fn = ctx.dialogs?.[action.dialog];
            if (fn) return fn(action, ctx);
            toast(`${action.label} isn’t available on this screen.`, 'attention');
            return null;
        }
        const args = { ...(ctx.args || {}) };
        const subject = ctx.subject ? ` ${ctx.subject}` : '';
        if (action.input) {
            const inp = action.input;
            const value = await prompt({
                title: `${action.label}${subject}`, label: inp.label, message: action.confirm || '', optional: !!inp.optional,
                multiline: !!inp.multiline, okLabel: action.label, expect: inp.expect || '', danger: !!action.danger,
                hint: inp.expect ? 'This change needs typed confirmation.' : '',
            });
            if (value === null) return null;
            if (value !== '' || !inp.optional) args[inp.arg] = value;
        } else if (action.confirm) {
            const ok = await confirm({ title: `${action.label}${subject}?`, message: action.confirm, confirmLabel: action.label, tone: action.danger ? 'danger' : 'primary' });
            if (!ok) return null;
        }
        try {
            const result = await busy(button, () => api.cmd(action.id, args));
            toast(`${action.label}: done`, 'ok');
            ctx.done?.(result, action);
            return result;
        } catch (err) {
            toast(`${action.label} failed: ${err.message}`, 'danger');
            return null;
        }
    }

    /* Run a command with toast feedback; returns the result or null. */
    async function command(type, args, { ok = '', button = null, quiet = false } = {}) {
        try {
            const result = await busy(button, () => api.cmd(type, args));
            if (!quiet) toast(ok || 'Done', 'ok');
            return result;
        } catch (err) {
            toast(err.message, 'danger');
            throw err;
        }
    }

    function debounce(fn, ms) {
        let t = 0;
        return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
    }

    /* "k=v, k2=v2" ⇄ {k: v} */
    function parseLabels(text) {
        const out = {};
        String(text || '').split(/[,\n]/).map(s => s.trim()).filter(Boolean).forEach(part => {
            const i = part.indexOf('=');
            if (i > 0) out[part.slice(0, i).trim()] = part.slice(i + 1).trim();
        });
        return out;
    }
    const formatLabels = labels => Object.entries(labels || {}).map(([k, v]) => `${k}=${v}`).join(', ');

    window.OrchUI = {
        esc, icon, uid, ago, when, time, dur, plural, cap, json, chip, sign, statusKey, effectChip, effectWord, meta, labelChips, status, vstyle,
        row, rows, subline, rowActions, stateHtml,
        loading, empty, errorState, banner, kv, table, code, highlight, langFor, copyText, outputView,
        tabs, bindTabs, dialog, removeAfterClose, confirm, prompt, toast, busy, actionButtons, actionBar, bindActions, runAction,
        command, debounce, parseLabels, formatLabels,
    };
})();
