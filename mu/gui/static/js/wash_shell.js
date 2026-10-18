// Wash shell behaviour shared by every page on templates/_layout.html.
//
// - Sidebar drawer (below 840px): the top-bar menu button toggles
//   #w-shell[data-sidebar-open]; Esc, the scrim, or following a link closes it.
// - window.WashToast.show(message, tone, {timeout, action: {label, run}}) —
//   every page's toasts, including app.js's $store.toast (which delegates here). Errors go to the assertive region and persist;
//   info/success go to the polite region and auto-dismiss after 5s (G§9.16).
// - Page transitions where the browser lacks cross-document View Transitions (Firefox):
//   fade the stage out before following a same-origin link.
(function () {
    'use strict';

    function initSidebar() {
        var shell = document.getElementById('w-shell');
        var button = document.querySelector('[data-shell-menu]');
        if (!shell || !button) return;
        var scrim = null;
        function close() {
            shell.removeAttribute('data-sidebar-open');
            button.setAttribute('aria-expanded', 'false');
            button.setAttribute('aria-label', 'Open navigation');
            if (scrim) { scrim.remove(); scrim = null; }
        }
        function open() {
            shell.setAttribute('data-sidebar-open', '');
            button.setAttribute('aria-expanded', 'true');
            button.setAttribute('aria-label', 'Close navigation');
            scrim = document.createElement('div');
            scrim.className = 'w-scrim';
            scrim.addEventListener('click', close);
            shell.appendChild(scrim);
            var first = document.querySelector('#w-sidebar a, #w-sidebar button');
            if (first) first.focus({ preventScroll: true });
        }
        button.addEventListener('click', function () {
            if (shell.hasAttribute('data-sidebar-open')) close(); else open();
        });
        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape' && shell.hasAttribute('data-sidebar-open')) {
                close();
                button.focus({ preventScroll: true });
            }
        });
        var sidebar = document.getElementById('w-sidebar');
        if (sidebar) sidebar.addEventListener('click', function (event) {
            if (event.target.closest('a')) close();
        });
    }

    var GLYPHS = { ok: '✓', success: '✓', danger: '✗', error: '✗', attention: '◆', info: '●' };
    function show(message, tone, options) {
        var kind = tone === 'error' ? 'danger' : (tone === 'success' ? 'ok' : (tone || 'info'));
        var assertive = kind === 'danger';
        var region = document.getElementById(assertive ? 'w-toasts-assertive' : 'w-toasts');
        if (!region) return null;
        var toast = document.createElement('div');
        toast.className = 'w-toast';
        toast.setAttribute('data-tone', kind);
        var glyph = document.createElement('span');
        glyph.className = 'w-toast__glyph';
        glyph.setAttribute('aria-hidden', 'true');
        glyph.textContent = GLYPHS[kind] || '●';
        var msg = document.createElement('span');
        msg.className = 'w-toast__msg';
        msg.textContent = String(message || '');
        var dismiss = document.createElement('button');
        dismiss.type = 'button';
        dismiss.className = 'w-toast__close';
        dismiss.setAttribute('aria-label', 'Dismiss notification');
        dismiss.textContent = '×';
        dismiss.addEventListener('click', function () { toast.remove(); });
        toast.append(glyph, msg);
        if (options && options.action && options.action.label) {
            var action = document.createElement('button');
            action.type = 'button';
            action.className = 'w-btn';
            action.setAttribute('data-variant', 'ghost');
            action.setAttribute('data-size', 'sm');
            action.textContent = String(options.action.label);
            action.addEventListener('click', function () {
                try { if (typeof options.action.run === 'function') options.action.run(); }
                finally { toast.remove(); }
            });
            toast.append(action);
        }
        toast.append(dismiss);
        region.appendChild(toast);
        var timeout = options && options.timeout != null ? options.timeout : (assertive ? 0 : 5000);
        if (timeout > 0) setTimeout(function () { toast.remove(); }, timeout);
        return toast;
    }
    window.WashToast = { show: show };

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initSidebar);
    else initSidebar();

    // Scripted page transition for browsers without cross-document View Transitions
    // (html[data-nav="fade"], set pre-paint by partials/theme_bootstrap.html — e.g. Firefox).
    // A same-origin page link fades the stage out (base.css .w-leaving) before navigating;
    // the next page fades its stage in. Hash-only links (/work routes), new-tab clicks,
    // downloads, API/static URLs and handlers that already called preventDefault are left alone.
    var LEAVE_MS = 160;
    function navigable(event) {
        if (document.documentElement.getAttribute('data-nav') !== 'fade') return null;
        if (event.defaultPrevented || event.button !== 0) return null;
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return null;
        var link = event.target && event.target.closest ? event.target.closest('a[href]') : null;
        if (!link || link.hasAttribute('download') || link.hasAttribute('data-no-transition')) return null;
        var target = (link.getAttribute('target') || '').toLowerCase();
        if (target && target !== '_self') return null;
        var url;
        try { url = new URL(link.href, location.href); } catch (e) { return null; }
        if (url.origin !== location.origin) return null;
        if (/^\/(api|static)\//.test(url.pathname)) return null;
        if (url.pathname === location.pathname && url.search === location.search) return null;  // in-page / hash route
        if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return null;
        return url;
    }
    document.addEventListener('click', function (event) {
        var url = navigable(event);
        if (!url) return;
        event.preventDefault();
        var root = document.documentElement;
        root.classList.add('w-leaving');
        setTimeout(function () { location.assign(url.href); }, LEAVE_MS);
        // A beforeunload prompt can cancel the navigation; never leave the page faded out.
        setTimeout(function () { root.classList.remove('w-leaving'); }, LEAVE_MS + 1500);
    });
    // Back/forward-cache restores show the page exactly as it was left: undo the fade-out.
    window.addEventListener('pageshow', function () { document.documentElement.classList.remove('w-leaving'); });
})();
