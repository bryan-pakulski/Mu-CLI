// Wash shell behaviour shared by every page on templates/_layout.html.
//
// - Sidebar drawer (below 840px): the top-bar menu button toggles
//   #w-shell[data-sidebar-open]; Esc, the scrim, or following a link closes it.
// - Sidebar collapse (840px and wider, every page): the sidebar foot button
//   ([data-sidebar-toggle]) or window.WashShell.toggleSidebar() sets
//   html[data-sidebar="collapsed"], saves it in localStorage `mucli-layout`
//   (sidebarOpen, shared with the chat page's layout store; the pre-paint
//   bootstrap restores it) and fires `mucli:sidebar` {open}.
// - Orchestration badge (every page): the sidebar's Orchestration item shows
//   how many things wait for you (runs to approve, paused runs, reviews, nodes
//   to accept) from GET /api/orchestration/badge — every 30s while the page is
//   visible, on return to the tab and on `mucli:orch-attention`. That endpoint
//   never starts orchestration on a machine that hasn't used it.
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

    var LAYOUT_KEY = 'mucli-layout';
    function sidebarOpen() { return document.documentElement.getAttribute('data-sidebar') !== 'collapsed'; }
    function paintToggle() {
        var open = sidebarOpen();
        document.querySelectorAll('[data-sidebar-toggle]').forEach(function (button) {
            var label = open ? 'Collapse sidebar' : 'Expand sidebar';
            button.setAttribute('aria-expanded', String(open));
            button.setAttribute('aria-label', label);
            button.title = label;
            var text = button.querySelector('[data-sidebar-toggle-label]');
            if (text) text.textContent = open ? 'Collapse' : 'Expand';
        });
    }
    function setSidebar(open, persist) {
        if (open) document.documentElement.removeAttribute('data-sidebar');
        else document.documentElement.setAttribute('data-sidebar', 'collapsed');
        if (persist !== false) {
            try {
                var saved = JSON.parse(localStorage.getItem(LAYOUT_KEY) || '{}') || {};
                saved.sidebarOpen = !!open;
                localStorage.setItem(LAYOUT_KEY, JSON.stringify(saved));
            } catch (e) { /* storage unavailable: this page only */ }
        }
        paintToggle();
        document.dispatchEvent(new CustomEvent('mucli:sidebar', { detail: { open: !!open } }));
    }
    function initCollapse() {
        paintToggle();
        document.addEventListener('click', function (event) {
            if (event.target.closest && event.target.closest('[data-sidebar-toggle]')) setSidebar(!sidebarOpen());
        });
        // Another tab changed it: follow without writing back.
        window.addEventListener('storage', function (event) {
            if (event.key !== LAYOUT_KEY) return;
            try { var v = JSON.parse(event.newValue || '{}'); setSidebar(v.sidebarOpen !== false, false); } catch (e) {}
        });
    }
    function initOrchBadge() {
        var badges = document.querySelectorAll('[data-orch-badge]');
        if (!badges.length) return;
        var timer = 0;
        function paint(att) {
            var total = Number((att && att.total) || 0);
            var text = total ? (att.summary || (total + ' waiting')) : '';
            badges.forEach(function (badge) {
                badge.hidden = !total;
                badge.textContent = total > 99 ? '99+' : String(total);
                badge.setAttribute('aria-label', total + ' orchestration item' + (total === 1 ? '' : 's') + ' need you');
                var item = badge.closest('a');
                if (item) item.title = total ? 'Orchestration: ' + text : 'Orchestration';
            });
        }
        function refresh() {
            fetch('/api/orchestration/badge', { credentials: 'same-origin', cache: 'no-store' })
                .then(function (res) { return res.ok ? res.json() : null; })
                .then(function (att) { if (att) paint(att); })
                .catch(function () { /* next tick retries */ });
        }
        function schedule() {
            clearInterval(timer);
            if (document.visibilityState === 'visible') timer = setInterval(refresh, 30000);
        }
        document.addEventListener('visibilitychange', function () {
            if (document.visibilityState === 'visible') refresh();
            schedule();
        });
        // The orchestration page knows the counts already (detail); others ask again.
        document.addEventListener('mucli:orch-attention', function (event) {
            if (event.detail && typeof event.detail.total === 'number') paint(event.detail); else refresh();
        });
        refresh();
        schedule();
    }

    window.WashShell = {
        sidebarOpen: sidebarOpen,
        setSidebar: setSidebar,
        toggleSidebar: function () { setSidebar(!sidebarOpen()); },
    };

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

    function init() { initSidebar(); initCollapse(); initOrchBadge(); }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();

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
