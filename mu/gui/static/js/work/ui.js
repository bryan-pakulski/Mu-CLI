/* Jobs mode UI primitives on the Wash components: Dialog (.w-modal markup on
 * the shared dialog stack, static/js/dialogs.js: focus trap, Esc and focus
 * restore), confirm/prompt, toasts (window.WashToast, the shared
 * polite/assertive regions), the command registry (fed to the shared Wash
 * command palette, static/js/wash/palette.js, which owns ⌘/Ctrl+K), the "?"
 * help overlay and the global keyboard handler (":", "?", "g x" chords,
 * single-key page actions).
 */
(() => {
    'use strict';
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
    const X_ICON = '<svg class="w-icon" aria-hidden="true" focusable="false"><use href="/static/icons/wash.svg#i-x"></use></svg>';
    const host = () => document.getElementById('work-app') || document.body;
    let idSeq = 0;

    /* ------------------------------------------------------------ dialog */
    function dialog({ title, body = '', footer = '', wide = false, sheet = false, onClose } = {}) {
        const opener = document.activeElement;
        const titleId = `work-dlg-${++idSeq}`;
        const backdrop = document.createElement('div');
        backdrop.className = `w-modal work-dialog-backdrop${sheet ? ' is-sheet' : ''}`;
        backdrop.innerHTML = `<section class="w-modal__dialog work-dialog${wide ? ' is-wide' : ''}" data-size="${wide ? 'lg' : 'sm'}" role="dialog" aria-modal="true" aria-labelledby="${titleId}" tabindex="-1">
            <header class="w-modal__head work-dialog-head"><h2 class="w-modal__title" id="${titleId}">${esc(title)}</h2>
            <button type="button" class="w-btn" data-variant="icon" aria-label="Close" title="Close" data-dialog-close>${X_ICON}</button></header>
            <div class="w-modal__body work-dialog-body"></div>${footer ? '<footer class="w-modal__foot work-dialog-foot"></footer>' : ''}</section>`;
        const panel = backdrop.firstElementChild;
        const bodyEl = panel.querySelector('.work-dialog-body');
        if (typeof body === 'string') bodyEl.innerHTML = body; else bodyEl.append(body);
        if (footer) { const f = panel.querySelector('.work-dialog-foot'); if (typeof footer === 'string') f.innerHTML = footer; else f.append(footer); }
        let closed = false;
        const close = () => {
            if (closed) return; closed = true;
            window.MuDialog.close(backdrop);
            backdrop.remove();
            if (opener && document.contains(opener)) opener.focus();
            onClose?.();
        };
        backdrop.addEventListener('mousedown', e => { if (e.target === backdrop) close(); });
        backdrop.addEventListener('dialog-close', close);
        panel.querySelector('[data-dialog-close]').onclick = close;
        backdrop.hidden = true;
        host().append(backdrop);
        // The shared stack traps Tab and routes Esc to `dialog-close`; this
        // function places the initial focus itself (below).
        window.MuDialog.open(backdrop, { focus: false });
        const first = panel.querySelector('[data-autofocus]') || panel.querySelector('input:not([type=hidden]):not([disabled]), textarea, select') || panel.querySelector(FOCUSABLE);
        (first || panel).focus();
        return { el: panel, body: bodyEl, close };
    }

    function confirm(message, { title = 'Confirm', okLabel = 'OK', tone } = {}) {
        return new Promise(resolve => {
            let result = false;
            const foot = document.createElement('div');
            foot.className = 'w-btn-row';
            // Destructive confirms (G§9.17): the danger button repeats the verb and Cancel gets default focus.
            const danger = tone === 'danger';
            foot.innerHTML = `<button type="button" class="w-btn" data-variant="secondary" data-cancel>Cancel</button><button type="button" class="w-btn" data-variant="${danger ? 'danger' : 'primary'}" data-ok>${esc(okLabel)}</button>`;
            const d = dialog({ title, body: `<p class="work-dialog-message">${esc(message)}</p>`, footer: foot, onClose: () => resolve(result) });
            foot.querySelector('[data-cancel]').onclick = () => d.close();
            foot.querySelector('[data-ok]').onclick = () => { result = true; d.close(); };
            foot.querySelector(danger ? '[data-cancel]' : '[data-ok]').focus();
        });
    }

    function prompt({ title, label, optional = false, multiline = false, okLabel = 'OK', value = '', hint = '' } = {}) {
        return new Promise(resolve => {
            let result = null;
            const id = `work-prompt-${++idSeq}`;
            const form = document.createElement('form');
            form.className = 'work-stack';
            form.innerHTML = `<div class="w-field"><label class="w-field__label" for="${id}">${esc(label)}${optional ? ' <span class="work-meta">(optional)</span>' : ''}</label>
                ${multiline ? `<textarea class="w-textarea" id="${id}" rows="6" data-autofocus>${esc(value)}</textarea>` : `<input class="w-input" id="${id}" type="text" value="${esc(value)}" data-autofocus>`}
                ${hint ? `<p class="w-field__hint">${esc(hint)}</p>` : ''}<p class="w-field__error" data-error></p></div>
                <div class="w-btn-row" data-align="end"><button type="button" class="w-btn" data-variant="secondary" data-cancel>Cancel</button><button type="submit" class="w-btn" data-variant="primary">${esc(okLabel)}</button></div>`;
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
    /* The shared Wash toast (G§9.16, wash_shell.js): info and success auto-dismiss in the
     * polite region, errors persist in the assertive one. Jobs tones map onto Wash tones. */
    const TOAST_TONE = { ok: 'ok', danger: 'danger', warn: 'attention', running: 'info', accent: 'info', muted: 'info' };
    function toast(text, tone = 'accent', action) {
        const kind = TOAST_TONE[tone] || 'info';
        const item = window.WashToast?.show(String(text || ''), kind, { timeout: kind === 'danger' ? 0 : kind === 'attention' ? 7000 : 5000 });
        if (!item || !action) return;
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'w-btn';
        button.dataset.variant = 'ghost';
        button.dataset.size = 'sm';
        button.dataset.toastAction = '';
        button.textContent = action.label;
        button.onclick = () => { action.run(); item.remove(); };
        item.insertBefore(button, item.querySelector('.w-toast__close'));
    }

    /* ------------------------------------------------------------ command registry */
    const sources = new Map(); // id -> {items, priority}
    function register(id, items, priority = 0) { sources.set(id, { items: items || [], priority }); }
    function commands() {
        return [...sources.values()].sort((a, b) => a.priority - b.priority).flatMap(s => s.items);
    }

    /* The page's commands live in the shared Wash command palette
     * (static/js/wash/palette.js, loaded by _layout.html on every page). */
    if (window.WashPalette) {
        window.WashPalette.configure({ goKeys: false });   // /work owns its `g x` chords
        window.WashPalette.register('work', () => {
            const seen = new Set();
            return commands().filter(i => !seen.has(i.id) && seen.add(i.id)).map(i => ({
                id: `work:${i.id}`, group: i.group, label: i.label, key: i.key, keywords: i.keywords,
                hint: i.enabled === false ? (i.reason || 'not available now') : '', enabled: i.enabled !== false, run: i.run,
            }));
        }, { priority: 5 });
    }
    function palette() { window.WashPalette?.open(); }

    const GLOBAL_KEYS = [['⌘K / ctrl+k  or  :', 'command palette'], ['?', 'this help'], ['g h', 'dashboard'], ['g t', 'jobs'],
        ['g a', 'activity'], ['g b', 'tickets'], ['g r', 'history'], ['g s', 'settings'], ['g u', 'setup'], ['g d', 'setup → doctor'], ['n', 'new job'], ['Esc', 'close dialog'], ['← →', 'switch tabs (when a tab has focus)']];
    function help() {
        if (document.querySelector('.work-dialog-backdrop')) return;
        const seen = new Set();
        const page = commands().filter(i => i.key && !i.key.startsWith('g ') && !seen.has(i.key) && seen.add(i.key));
        const row = (k, d, muted) => `<div class="work-keys__row"><dt><kbd class="w-keycap">${esc(k)}</kbd></dt><dd${muted ? ' class="work-meta"' : ''}>${esc(d)}</dd></div>`;
        dialog({ title: 'Keyboard shortcuts', wide: true, body: `<p class="work-meta">Shortcuts work when no text field has focus. Job keys are the same in every mucli front end.</p>
            <div class="work-grid-2"><section><h3 class="work-subhead">Everywhere</h3><dl class="work-keys">${GLOBAL_KEYS.map(([k, d]) => row(k, d)).join('')}</dl></section>
            <section><h3 class="work-subhead">This page</h3>${page.length ? `<dl class="work-keys">${page.map(i => row(i.key, i.label + (i.enabled === false && i.reason ? ` (${i.reason})` : ''), i.enabled === false)).join('')}</dl>` : '<p class="work-meta">No page shortcuts.</p>'}</section></div>` });
    }

    /* ------------------------------------------------------------ keyboard */
    const isTyping = el => !!el && (/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) || el.isContentEditable);
    let pendingG = 0;
    // Capture phase on window: Work's own keys (g s → Work settings, n → New job) run before the
    // shared frame's (wash_shell.js, on document) and mark the event handled; keys Work doesn't
    // know (g c, g o, g e…) fall through to the frame.
    function installKeys() {
        window.addEventListener('keydown', e => {
            if (e.defaultPrevented) return;
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
        }, true);
    }

    window.MuJobUI = { esc, dialog, confirm, prompt, toast, register, commands, palette, help, installKeys, isTyping };
})();
