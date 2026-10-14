/* Jobs mode UI primitives — vanilla ports of the planos web components:
 * Dialog (focus trap, Esc, focus restore), confirm/prompt, toasts, the command
 * registry, the ctrl+k palette, the "?" help overlay and the global keyboard
 * handler (":" / ctrl+k, "?", "g x" chords, single-key page actions).
 */
(() => {
    'use strict';
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
    const X_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" aria-hidden="true"><path d="M18 6 6 18M6 6l12 12"/></svg>';
    const host = () => document.getElementById('work-app') || document.body;
    let idSeq = 0;

    /* ------------------------------------------------------------ dialog */
    function dialog({ title, body = '', footer = '', wide = false, sheet = false, onClose } = {}) {
        const opener = document.activeElement;
        const titleId = `work-dlg-${++idSeq}`;
        const backdrop = document.createElement('div');
        backdrop.className = `work-dialog-backdrop${sheet ? ' is-sheet' : ''}`;
        backdrop.innerHTML = `<div class="work-dialog ${wide ? 'is-wide' : ''}" role="dialog" aria-modal="true" aria-labelledby="${titleId}" tabindex="-1">
            <header class="work-dialog-head"><h2 id="${titleId}">${esc(title)}</h2><span class="spacer"></span>
            <button type="button" class="btn ghost icon" aria-label="Close" data-dialog-close>${X_ICON}</button></header>
            <div class="work-dialog-body"></div>${footer ? '<footer class="work-dialog-foot"></footer>' : ''}</div>`;
        const panel = backdrop.firstElementChild;
        const bodyEl = panel.querySelector('.work-dialog-body');
        if (typeof body === 'string') bodyEl.innerHTML = body; else bodyEl.append(body);
        if (footer) { const f = panel.querySelector('.work-dialog-foot'); if (typeof footer === 'string') f.innerHTML = footer; else f.append(footer); }
        let closed = false;
        const close = () => {
            if (closed) return; closed = true;
            document.removeEventListener('keydown', onKey, true);
            backdrop.remove();
            if (opener && document.contains(opener)) opener.focus();
            onClose?.();
        };
        const onKey = e => {
            if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); close(); return; }
            if (e.key !== 'Tab') return;
            const items = [...panel.querySelectorAll(FOCUSABLE)].filter(x => !x.hasAttribute('disabled') && x.offsetParent !== null);
            if (!items.length) { e.preventDefault(); return; }
            const first = items[0], last = items[items.length - 1];
            if (e.shiftKey && (document.activeElement === first || document.activeElement === panel)) { e.preventDefault(); last.focus(); }
            else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        };
        backdrop.addEventListener('mousedown', e => { if (e.target === backdrop) close(); });
        panel.querySelector('[data-dialog-close]').onclick = close;
        document.addEventListener('keydown', onKey, true);
        host().append(backdrop);
        const first = panel.querySelector('[data-autofocus]') || panel.querySelector('input:not([type=hidden]):not([disabled]), textarea, select') || panel.querySelector(FOCUSABLE);
        (first || panel).focus();
        return { el: panel, body: bodyEl, close };
    }

    function confirm(message, { title = 'Confirm', okLabel = 'OK', tone } = {}) {
        return new Promise(resolve => {
            let result = false;
            const foot = document.createElement('div');
            foot.className = 'row';
            foot.innerHTML = `<button type="button" class="btn" data-cancel>Cancel</button><button type="button" class="btn ${tone === 'danger' ? 'solid tone-danger' : 'primary'}" data-ok data-autofocus>${esc(okLabel)}</button>`;
            const d = dialog({ title, body: `<p>${esc(message)}</p>`, footer: foot, onClose: () => resolve(result) });
            foot.querySelector('[data-cancel]').onclick = () => d.close();
            foot.querySelector('[data-ok]').onclick = () => { result = true; d.close(); };
            foot.querySelector('[data-ok]').focus();
        });
    }

    function prompt({ title, label, optional = false, multiline = false, okLabel = 'OK', value = '', hint = '' } = {}) {
        return new Promise(resolve => {
            let result = null;
            const id = `work-prompt-${++idSeq}`;
            const form = document.createElement('form');
            form.className = 'col';
            form.innerHTML = `<div class="field"><label class="label" for="${id}">${esc(label)}${optional ? ' <span class="muted small">(optional)</span>' : ''}</label>
                ${multiline ? `<textarea id="${id}" rows="6" data-autofocus>${esc(value)}</textarea>` : `<input id="${id}" type="text" value="${esc(value)}" data-autofocus>`}
                ${hint ? `<span class="hint">${esc(hint)}</span>` : ''}<span class="error" data-error></span></div>
                <div class="row" style="justify-content:flex-end"><button type="button" class="btn" data-cancel>Cancel</button><button type="submit" class="btn primary">${esc(okLabel)}</button></div>`;
            const d = dialog({ title, body: form, onClose: () => resolve(result) });
            const input = form.querySelector(`#${id}`);
            form.querySelector('[data-cancel]').onclick = () => d.close();
            if (multiline) input.addEventListener('keydown', e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); form.requestSubmit(); } });
            form.onsubmit = e => {
                e.preventDefault();
                if (!optional && !input.value.trim()) { input.setAttribute('aria-invalid', 'true'); form.querySelector('[data-error]').textContent = 'Required.'; input.focus(); return; }
                result = input.value; d.close();
            };
        });
    }

    /* ------------------------------------------------------------ toasts */
    const GLYPH = { ok: '✓', danger: '✗', warn: '◆', running: '●' };
    function toast(text, tone = 'accent', action) {
        const stack = document.querySelector('[data-toast-stack]');
        if (!stack) return;
        while (stack.children.length >= 5) stack.firstElementChild.remove();
        const item = document.createElement('div');
        item.className = `work-toast-item tone-${tone}`;
        item.setAttribute('role', tone === 'danger' ? 'alert' : 'status');
        item.innerHTML = `<span aria-hidden="true" class="work-toast-glyph">${GLYPH[tone] || 'ℹ'}</span><span class="work-toast-text">${esc(text)}</span>${action ? `<button type="button" class="btn sm" data-toast-action>${esc(action.label)}</button>` : ''}<button type="button" class="btn ghost icon sm" aria-label="Dismiss" data-toast-dismiss>${X_ICON}</button>`;
        const dismiss = () => item.remove();
        item.querySelector('[data-toast-dismiss]').onclick = dismiss;
        if (action) item.querySelector('[data-toast-action]').onclick = () => { action.run(); dismiss(); };
        stack.append(item);
        // Danger toasts stay longer; hovering pauses the dismissal.
        let ms = tone === 'danger' ? 9000 : tone === 'warn' ? 7000 : 4500, timer = setTimeout(dismiss, ms), started = Date.now();
        item.addEventListener('mouseenter', () => { clearTimeout(timer); ms -= Date.now() - started; });
        item.addEventListener('mouseleave', () => { started = Date.now(); timer = setTimeout(dismiss, Math.max(1500, ms)); });
    }

    /* ------------------------------------------------------------ command registry */
    const sources = new Map(); // id -> {items, priority}
    function register(id, items, priority = 0) { sources.set(id, { items: items || [], priority }); }
    function commands() {
        return [...sources.values()].sort((a, b) => a.priority - b.priority).flatMap(s => s.items);
    }
    function fuzzyScore(q, text) {
        if (!q) return 0;
        const t = text.toLowerCase(), needle = q.toLowerCase();
        const at = t.indexOf(needle);
        if (at >= 0) return 1000 - at;
        let score = 0, pos = 0;
        for (const ch of needle) { const i = t.indexOf(ch, pos); if (i < 0) return null; score += i === pos ? 5 : 1; pos = i + 1; }
        return score;
    }

    function palette() {
        if (document.querySelector('.work-dialog-backdrop')) return;
        const listId = `work-palette-${++idSeq}`;
        let sel = 0, results = [];
        const body = document.createElement('div');
        body.className = 'col';
        body.innerHTML = `<div class="work-palette-search"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" aria-hidden="true"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
            <input type="text" role="combobox" aria-expanded="true" aria-controls="${listId}" aria-label="Search commands" placeholder="Go to… or run an action" data-autofocus></div>
            <ul class="work-palette-list" id="${listId}" role="listbox" aria-label="Commands"></ul>`;
        const input = body.querySelector('input'), list = body.querySelector('ul');
        const d = dialog({ title: 'Command palette', body });
        const choose = item => { if (!item) return; d.close(); setTimeout(() => item.run(), 0); };
        const draw = () => {
            const seen = new Set();
            results = commands().filter(i => i.enabled !== false && !seen.has(i.id) && seen.add(i.id))
                .map(i => ({ i, s: fuzzyScore(input.value, `${i.label} ${i.group} ${i.keywords || ''}`) }))
                .filter(x => x.s != null).sort((a, b) => (input.value ? b.s - a.s : 0)).slice(0, 50).map(x => x.i);
            sel = Math.min(sel, Math.max(0, results.length - 1));
            list.innerHTML = results.map((i, n) => `<li id="${listId}-${n}" role="option" aria-selected="${n === sel}" data-index="${n}" class="work-palette-item"><span class="work-palette-group">${esc(i.group)}</span><span class="grow">${esc(i.label)}</span>${i.key ? `<kbd>${esc(i.key)}</kbd>` : ''}</li>`).join('') || '<li class="empty">No matching command</li>';
            input.setAttribute('aria-activedescendant', results[sel] ? `${listId}-${sel}` : '');
            list.querySelector(`[data-index="${sel}"]`)?.scrollIntoView({ block: 'nearest' });
        };
        list.addEventListener('mousemove', e => { const li = e.target.closest('[data-index]'); if (li && Number(li.dataset.index) !== sel) { sel = Number(li.dataset.index); draw(); } });
        list.addEventListener('click', e => { const li = e.target.closest('[data-index]'); if (li) choose(results[Number(li.dataset.index)]); });
        input.addEventListener('input', () => { sel = 0; draw(); });
        input.addEventListener('keydown', e => {
            if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(results.length - 1, sel + 1); draw(); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(0, sel - 1); draw(); }
            else if (e.key === 'Enter') { e.preventDefault(); choose(results[sel]); }
        });
        draw();
    }

    const GLOBAL_KEYS = [['ctrl+k  or  :', 'command palette'], ['?', 'this help'], ['g h', 'dashboard'], ['g t', 'jobs'],
        ['g a', 'activity'], ['g b', 'tickets'], ['g r', 'history'], ['g s', 'settings'], ['g u', 'setup'], ['g d', 'setup → doctor'], ['n', 'new job'], ['Esc', 'close dialog'], ['← →', 'switch tabs (when a tab has focus)']];
    function help() {
        if (document.querySelector('.work-dialog-backdrop')) return;
        const seen = new Set();
        const page = commands().filter(i => i.key && !i.key.startsWith('g ') && !seen.has(i.key) && seen.add(i.key));
        const row = (k, d, muted) => `<div class="row"><dt style="min-width:120px"><kbd>${esc(k)}</kbd></dt><dd style="margin:0" class="${muted ? 'muted' : ''}">${esc(d)}</dd></div>`;
        dialog({ title: 'Keyboard shortcuts', wide: true, body: `<p class="muted small">Shortcuts work when no text field has focus. Job keys are the same in every mucli front end.</p>
            <div class="grid-2"><section><h3>Everywhere</h3><dl class="col" style="gap:var(--s-half);margin:var(--s-1) 0 0">${GLOBAL_KEYS.map(([k, d]) => row(k, d)).join('')}</dl></section>
            <section><h3>This page</h3>${page.length ? `<dl class="col" style="gap:var(--s-half);margin:var(--s-1) 0 0">${page.map(i => row(i.key, i.label + (i.enabled === false && i.reason ? ` (${i.reason})` : ''), i.enabled === false)).join('')}</dl>` : '<p class="muted">No page shortcuts.</p>'}</section></div>` });
    }

    /* ------------------------------------------------------------ keyboard */
    const isTyping = el => !!el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable);
    let pendingG = 0;
    function installKeys() {
        window.addEventListener('keydown', e => {
            if (e.defaultPrevented) return;
            if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === 'k') { e.preventDefault(); palette(); return; }
            if (e.ctrlKey || e.metaKey || e.altKey || isTyping(e.target)) return;
            if (document.querySelector('[aria-modal="true"]:not([hidden])') && [...document.querySelectorAll('[aria-modal="true"]')].some(m => m.offsetParent !== null)) return;
            if (e.key === ':') { e.preventDefault(); palette(); return; }
            if (e.key === '?') { e.preventDefault(); help(); return; }
            const now = Date.now();
            if (pendingG && now - pendingG < 1200) {
                pendingG = 0;
                const hit = commands().find(i => i.key === `g ${e.key}`);
                if (hit) { e.preventDefault(); hit.run(); }
                return;
            }
            const items = commands();
            const hit = items.find(i => i.key === e.key && i.enabled !== false) || items.find(i => i.key === e.key);
            if (hit) {
                e.preventDefault();
                if (hit.enabled === false) toast(`${hit.label}: ${hit.reason || 'not available now'}`, 'warn');
                else hit.run();
                return;
            }
            if (e.key === 'g') pendingG = now;
        });
    }

    window.MuJobUI = { esc, dialog, confirm, prompt, toast, register, commands, palette, help, installKeys, isTyping };
})();
