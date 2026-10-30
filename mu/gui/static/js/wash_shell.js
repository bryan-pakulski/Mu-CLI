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
// - window.WashHead.set({title, back, state, tabs, actions}) — the page header
//   (Mu §4) for hash-routed pages. `back` {label, href} | null; `state` an array
//   of parts {text, tone?: 'attention'|'danger'|'quiet', href?} (falsy parts and
//   zero counts are the caller's to drop); `tabs` [{label, href, current}];
//   `actions` a Node, an HTML string the caller has escaped, or null.
//   WashHead.esc(text) escapes for HTML.
// - window.WashSheet.open({title, state, body, param, onClose, row|from, focus, float})
//   — the details (Mu §7.2). On a page with a visible [data-dock] (.w-split, from
//   1200px) they render into the dock beside the list; otherwise a float from the
//   right. Esc / the × (and the backdrop when floating) close it; `param`
//   ({key, value}) mirrors it in the URL query (?open=…) so a reload or a shared
//   link reopens it; `row` (or the element `from` sits in) is marked selected.
//   Docked panels take focus only with focus: true. WashSheet.docked() tells a
//   page whether to fill the dock on arrival; WashSheet.current() is the open
//   param value; WashSheet.close().
// - Keys (Mu §9.6): n presses the page header's primary action (the `g`
//   chords live in wash/palette.js). Never while typing.
// - Work badge (every page without its own): the rail's Work item counts the jobs that
//   need you (GET /api/jobs/attention), on the same schedule as the orchestration badge.
// - Embed mode (html[data-embed]: the page is a keep-alive view inside the persistent
//   shell, mu_shell.js): reports its URL and title to the shell (`mu:url`), sends links
//   to other destinations up (`mu:go`) instead of loading them here, follows `mu:route`
//   (another hash / query of this page; a query change first fires a cancelable
//   window `mu:route` event {href} so the page can apply it without reloading) and `mu:theme`; window.MuNav.go does the same
//   for scripts and the palette.
// - window.WashMotion — switching tab or item fades (Mu §4.3): leave(els) fades the old
//   content out and resolves when it's gone (at once under reduced motion); enter(el) fades
//   the new content in. [data-leaving] / [data-entering] styles live in components/mu.css.
//   Every [role=tab] whose aria-selected flips to "true" fades its panel in (aria-controls,
//   else the nearest [role=tabpanel]) with no per-page wiring; hash-routed pages call
//   leave() before and enter() after a route change.
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

    // ------------------------------------------------------------ page header (Mu §4)
    function esc(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function headEl(sel) { return document.querySelector('#w-pagehead ' + sel); }
    function setHead(spec) {
        spec = spec || {};
        var head = document.getElementById('w-pagehead');
        if (!head) return;
        if ('title' in spec) {
            var title = headEl('[data-head-title]');
            if (title) title.textContent = spec.title || '';
        }
        if ('back' in spec) {
            var back = headEl('[data-head-back]');
            if (back) {
                back.hidden = !spec.back;
                if (spec.back) {
                    back.setAttribute('href', spec.back.href || '#');
                    var label = back.querySelector('[data-head-back-label]');
                    if (label) label.textContent = spec.back.label || 'Back';
                }
            }
        }
        if ('state' in spec) {
            var state = headEl('[data-head-state]');
            if (state) state.innerHTML = (spec.state || []).filter(Boolean).map(function (part) {
                if (typeof part === 'string') part = { text: part };
                var tone = part.tone ? ' data-tone="' + esc(part.tone) + '"' : '';
                var tag = part.href ? 'a' : 'span';
                var href = part.href ? ' href="' + esc(part.href) + '"' : '';
                return '<' + tag + ' class="w-state__part"' + tone + href + '>' + esc(part.text) + '</' + tag + '>';
            }).join('');
        }
        if ('tabs' in spec) {
            var tabs = headEl('[data-head-tabs]');
            if (tabs) tabs.innerHTML = (spec.tabs || []).map(function (tab) {
                return '<a class="w-pagetabs__tab" href="' + esc(tab.href) + '"' + (tab.current ? ' aria-current="page"' : '') + '>' + esc(tab.label) + '</a>';
            }).join('');
        }
        if ('actions' in spec) {
            var actions = headEl('[data-head-actions]');
            if (actions) {
                actions.textContent = '';
                if (spec.actions && typeof spec.actions === 'object' && spec.actions.nodeType) actions.appendChild(spec.actions);
                else if (spec.actions) actions.innerHTML = String(spec.actions);
            }
        }
        if (spec.documentTitle) document.title = spec.documentTitle;
    }
    window.WashHead = { set: setHead, esc: esc };

    // ------------------------------------------------------------ sheet (Mu §7.2)
    // A page with a visible [data-dock] (.w-split__dock, from 1200px) gets the details
    // docked beside its list; otherwise they float over the page from the right.
    var sheet = null;
    function visibleDock() {
        var dock = document.querySelector('[data-dock]');
        if (!dock) return null;
        return dock.getClientRects().length && getComputedStyle(dock).display !== 'none' ? dock : null;
    }
    function markRow(row, on) {
        if (!row) return;
        if (on) row.setAttribute('data-selected', ''); else row.removeAttribute('data-selected');
    }
    function setParam(param, on) {
        if (!param) return;
        try {
            var url = new URL(location.href);
            if (on) url.searchParams.set(param.key, param.value); else url.searchParams.delete(param.key);
            history.replaceState(history.state, '', url.pathname + url.search + url.hash);
        } catch (e) { /* no URL support */ }
    }
    function closeSheet(opts) {
        if (!sheet) return;
        var current = sheet;
        sheet = null;
        document.removeEventListener('keydown', current.onKey, true);
        if (!(opts && opts.keepUrl)) setParam(current.param, false);
        markRow(current.row, false);
        if (current.docked) {
            current.el.remove();
        } else {
            var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
            current.el.setAttribute('data-closing', '');
            setTimeout(function () { current.el.remove(); }, reduce ? 0 : 160);
        }
        if (!(opts && opts.silent)) {
            if (current.returnFocus && current.returnFocus.focus && document.contains(current.returnFocus)) current.returnFocus.focus({ preventScroll: true });
            if (typeof current.onClose === 'function') { try { current.onClose(); } catch (e) { /* ignore */ } }
        }
    }
    function fill(root, spec, titleId) {
        root.querySelector('.w-sheet__title').textContent = spec.title || '';
        root.querySelector('.w-sheet__title').id = titleId;
        var stateEl = root.querySelector('.w-sheet__state');
        if (spec.state && spec.state.nodeType) stateEl.appendChild(spec.state);
        else if (spec.state) stateEl.innerHTML = String(spec.state);
        var body = root.querySelector('.w-sheet__body');
        if (spec.body && spec.body.nodeType) body.appendChild(spec.body);
        else if (spec.body) body.innerHTML = String(spec.body);
        return body;
    }
    var CLOSE = '<button type="button" class="w-btn w-sheet__close" data-variant="icon" aria-label="Close" title="Close (Esc)">' +
        '<svg class="w-icon" aria-hidden="true" focusable="false"><use href="/static/icons/wash.svg#i-x"></use></svg></button>' +
        '<h2 class="w-sheet__title"></h2><p class="w-state w-sheet__state"></p><div class="w-sheet__body"></div>';
    function openSheet(spec) {
        spec = spec || {};
        var returnFocus = sheet ? sheet.returnFocus : document.activeElement;
        closeSheet({ keepUrl: true, silent: true });
        var titleId = 'w-sheet-title-' + Date.now();
        var dock = spec.float ? null : visibleDock();
        var el, focusTarget;
        if (dock) {
            el = document.createElement('section');
            el.className = 'w-dock__panel';
            el.setAttribute('aria-labelledby', titleId);
            el.setAttribute('tabindex', '-1');
            el.innerHTML = CLOSE;
            dock.appendChild(el);
            focusTarget = el;
        } else {
            el = document.createElement('div');
            el.className = 'w-sheet';
            el.innerHTML = '<aside class="w-sheet__panel" role="dialog" aria-modal="true" tabindex="-1">' + CLOSE + '</aside>';
            el.querySelector('.w-sheet__panel').setAttribute('aria-labelledby', titleId);
            (document.getElementById('w-main') || document.body).appendChild(el);
            el.addEventListener('click', function (event) { if (event.target === el) closeSheet(); });
            focusTarget = el.querySelector('.w-sheet__panel');
        }
        var body = fill(el, spec, titleId);
        var row = spec.row || (spec.from && spec.from.closest ? spec.from.closest('.w-row') : null);
        var entry = { el: el, spec: spec, docked: !!dock, param: spec.param || null, onClose: spec.onClose, returnFocus: returnFocus, row: row };
        entry.onKey = function (event) {
            if (event.key !== 'Escape' || sheet !== entry) return;
            // A docked panel only takes Esc when focus is inside it (the list keeps its keys).
            if (entry.docked && !el.contains(document.activeElement)) return;
            event.stopPropagation();
            closeSheet();
        };
        document.addEventListener('keydown', entry.onKey, true);
        el.querySelector('.w-sheet__close').addEventListener('click', function () { closeSheet(); });
        setParam(entry.param, true);
        markRow(row, true);
        sheet = entry;
        // Docked panels opened without a user gesture (the first row on arrival) never steal focus.
        if (!dock || spec.focus === true) focusTarget.focus({ preventScroll: true });
        return { el: el, body: body, docked: !!dock, close: closeSheet };
    }
    // Crossing 1200px: a docked panel whose dock disappeared floats instead, and back.
    if (window.matchMedia) {
        var wide = window.matchMedia('(width >= 1200px)');
        var onWide = function () {
            if (!sheet) return;
            var dock = visibleDock();
            if ((sheet.docked && !dock) || (!sheet.docked && dock && !sheet.spec.float)) {
                var spec = sheet.spec;
                openSheet(Object.assign({}, spec, { focus: false }));
            }
        };
        if (wide.addEventListener) wide.addEventListener('change', onWide);
    }
    window.WashSheet = {
        open: openSheet,
        close: function () { closeSheet(); },
        isOpen: function () { return !!sheet; },
        docked: function () { return !!visibleDock(); },
        current: function () { return sheet && sheet.param ? sheet.param.value : null; },
        param: function (key) { try { return new URL(location.href).searchParams.get(key || 'open'); } catch (e) { return null; } },
    };

    // ------------------------------------------------------------ keys (Mu §9.6)
    function typing(target) {
        if (!target) return false;
        if (target.isContentEditable) return true;
        var tag = (target.tagName || '').toLowerCase();
        return tag === 'input' || tag === 'textarea' || tag === 'select';
    }
    // Pages ship hidden dialogs that carry aria-modal statically: only a visible one blocks keys.
    function openModal() {
        return Array.prototype.some.call(document.querySelectorAll('[aria-modal="true"]'), function (m) {
            return m.offsetParent !== null || m.getClientRects().length > 0;
        });
    }
    function initKeys() {
        var afterG = 0;
        document.addEventListener('keydown', function (event) {
            if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return;
            if (typing(event.target) || openModal()) return;
            var key = event.key;
            // `g n` is not a chord here; leave keys after `g` to the palette.
            if (Date.now() - afterG < 1200) { afterG = 0; return; }
            if (key === 'g') { afterG = Date.now(); return; }
            if (key === 'n') {
                var primary = document.querySelector('#w-pagehead [data-head-actions] .w-btn[data-variant="primary"]');
                if (primary && !primary.disabled) { event.preventDefault(); primary.click(); }
            }
        });
    }

    // ------------------------------------------------------------ work badge
    function initWorkBadge() {
        var badges = document.querySelectorAll('[data-work-badge]');
        if (!badges.length) return;
        var timer = 0;
        function paint(count) {
            badges.forEach(function (badge) {
                badge.hidden = !count;
                badge.textContent = count > 99 ? '99+' : String(count);
                badge.setAttribute('aria-label', count + ' job' + (count === 1 ? '' : 's') + ' need you');
            });
        }
        function refresh() {
            fetch('/api/jobs/attention', { credentials: 'same-origin', cache: 'no-store' })
                .then(function (res) { return res.ok ? res.json() : null; })
                .then(function (data) { if (data) paint(Number(data.count || 0)); })
                .catch(function () { /* jobs may not be hosted here */ });
        }
        function schedule() {
            clearInterval(timer);
            if (document.visibilityState === 'visible') timer = setInterval(refresh, 30000);
        }
        document.addEventListener('visibilitychange', function () {
            if (document.visibilityState === 'visible') refresh();
            schedule();
        });
        document.addEventListener('mucli:jobs-attention', refresh);
        refresh();
        schedule();
    }

    // ------------------------------------------------------------ embed mode (inside the shell)
    var EMBED = document.documentElement.hasAttribute('data-embed') && window.parent !== window;
    var SHELL_PATHS = ['/', '/work', '/orchestration', '/containers', '/trace', '/jobs/trace', '/models/costs', '/settings'];
    function toShell(message) { try { window.parent.postMessage(message, location.origin); } catch (e) { /* detached */ } }
    function cleanHref() {
        var url = new URL(location.href);
        url.searchParams.delete('embed');
        return url.pathname + url.search + url.hash;
    }
    function initEmbed() {
        if (!EMBED) return;
        var last = '';
        function report() {
            var href = cleanHref();
            var key = href + '\u0000' + document.title;
            if (key === last) return;
            last = key;
            toShell({ type: 'mu:url', href: href, title: document.title });
        }
        ['pushState', 'replaceState'].forEach(function (name) {
            var original = history[name];
            history[name] = function () {
                var result = original.apply(this, arguments);
                setTimeout(report, 0);
                return result;
            };
        });
        window.addEventListener('hashchange', report);
        window.addEventListener('popstate', report);
        var title = document.querySelector('title');
        if (title && window.MutationObserver) new MutationObserver(report).observe(title, { childList: true, characterData: true, subtree: true });
        report();

        function go(href) {
            var url;
            try { url = new URL(href, location.href); } catch (e) { return; }
            if (url.origin === location.origin && url.pathname === location.pathname) {
                if (url.search.replace(/[?&]embed=1/, '') === location.search.replace(/[?&]embed=1/, '')) {
                    if (url.hash !== location.hash) location.hash = url.hash;
                    return;
                }
                // A page may handle a query change in place (cancel the event); else reload the view.
                var routed = new CustomEvent('mu:route', { cancelable: true, detail: { href: url.pathname + url.search + url.hash } });
                if (!window.dispatchEvent(routed)) return;
                url.searchParams.set('embed', '1');
                location.assign(url.pathname + url.search + url.hash);
                return;
            }
            if (url.origin === location.origin && SHELL_PATHS.indexOf(url.pathname) >= 0) toShell({ type: 'mu:go', href: url.pathname + url.search + url.hash });
            else location.assign(url.href);
        }
        window.MuNav = { go: go };
        // Links to another destination switch views in the shell (capture: before any
        // page handler or the scripted page fade).
        document.addEventListener('click', function (event) {
            if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
            var link = event.target.closest && event.target.closest('a[href]');
            if (!link || link.hasAttribute('download') || (link.target && link.target !== '_self')) return;
            var url;
            try { url = new URL(link.href, location.href); } catch (e) { return; }
            if (url.origin !== location.origin || url.pathname === location.pathname || SHELL_PATHS.indexOf(url.pathname) < 0) return;
            event.preventDefault();
            event.stopPropagation();
            toShell({ type: 'mu:go', href: url.pathname + url.search + url.hash });
        }, true);
        window.addEventListener('message', function (event) {
            if (event.origin !== location.origin || event.source !== window.parent) return;
            var data = event.data || {};
            if (data.type === 'mu:route' && typeof data.href === 'string') go(data.href);
            else if (data.type === 'mu:theme') {
                if (data.theme && window.MuTheme) window.MuTheme.apply(data.theme, { silent: true });
                if (data.decoration && window.MuDecoration) window.MuDecoration.apply(data.decoration);
                if (data.palette && window.MuPalette && window.MuPalette.current() !== data.palette) window.MuPalette.apply(data.palette);
                if (data.scenery && window.MuScenery && window.MuScenery.current() !== data.scenery) window.MuScenery.apply(data.scenery, { silent: true });
                if (typeof data.glass === 'number' && window.MuGlass) window.MuGlass.apply(data.glass, { silent: true });
                if (typeof data.blur === 'number' && window.MuGlassBlur) window.MuGlassBlur.apply(data.blur, { silent: true });
                if (typeof data.textScale === 'number' && window.MuTextScale) window.MuTextScale.apply(data.textScale, { silent: true });
            }
        });
    }

    // ------------------------------------------------------------ panes (Mu rev. 2)
    // A [data-pane-resize] handle inside a .w-pane drags that pane's width (the list
    // pane sets --w-list-w, the aside --w-aside-w on .w-panes); the width is kept per
    // page in localStorage `mucli-panes`. Pages may render the handle at any time.
    var PANES_KEY = 'mucli-panes';
    function paneWidths() { try { return JSON.parse(localStorage.getItem(PANES_KEY) || '{}') || {}; } catch (e) { return {}; } }
    function applyPaneWidths(root) {
        var saved = paneWidths()[location.pathname] || {};
        (root || document).querySelectorAll('.w-panes').forEach(function (panes) {
            if (saved.list) panes.style.setProperty('--w-list-w', saved.list + 'px');
            if (saved.aside) panes.style.setProperty('--w-aside-w', saved.aside + 'px');
        });
    }
    function initPanes() {
        applyPaneWidths();
        if (window.MutationObserver) {
            new MutationObserver(function (records) {
                records.forEach(function (r) {
                    r.addedNodes.forEach(function (n) { if (n.nodeType === 1 && (n.matches('.w-panes') || n.querySelector('.w-panes'))) applyPaneWidths(n.parentNode || document); });
                });
            }).observe(document.body, { childList: true, subtree: true });
        }
        document.addEventListener('pointerdown', function (event) {
            var handle = event.target.closest && event.target.closest('[data-pane-resize]');
            if (!handle) return;
            var pane = handle.closest('.w-pane');
            var panes = pane && pane.closest('.w-panes');
            if (!panes) return;
            var which = pane.getAttribute('data-pane') === 'aside' ? 'aside' : 'list';
            var startX = event.clientX;
            var startW = pane.getBoundingClientRect().width;
            event.preventDefault();
            handle.setAttribute('data-dragging', '');
            handle.setPointerCapture(event.pointerId);
            function move(e) {
                var delta = which === 'aside' ? startX - e.clientX : e.clientX - startX;
                var w = Math.max(220, Math.min(640, Math.round(startW + delta)));
                panes.style.setProperty(which === 'aside' ? '--w-aside-w' : '--w-list-w', w + 'px');
            }
            function up(e) {
                handle.removeAttribute('data-dragging');
                handle.removeEventListener('pointermove', move);
                handle.removeEventListener('pointerup', up);
                var all = paneWidths();
                var mine = all[location.pathname] || {};
                mine[which] = Math.round(pane.getBoundingClientRect().width);
                all[location.pathname] = mine;
                try { localStorage.setItem(PANES_KEY, JSON.stringify(all)); } catch (err) { /* private mode */ }
            }
            handle.addEventListener('pointermove', move);
            handle.addEventListener('pointerup', up);
        });
    }
    window.WashPanes = { apply: applyPaneWidths };

    // ------------------------------------------------------------ tab transitions
    var LEAVE_FADE_MS = 120;
    function reducedMotion() {
        return !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
    }
    function leave(els) {
        els = (els || []).filter(function (el) { return el && el.setAttribute; });
        if (!els.length) return Promise.resolve();
        var already = els.every(function (el) { return el.hasAttribute('data-leaving'); });
        els.forEach(function (el) { el.removeAttribute('data-entering'); el.setAttribute('data-leaving', ''); });
        if (already || reducedMotion()) return Promise.resolve();
        return new Promise(function (resolve) { setTimeout(resolve, LEAVE_FADE_MS); });
    }
    function enter(el) {
        if (!el || !el.setAttribute) return;
        el.removeAttribute('data-leaving');
        el.removeAttribute('data-entering');
        void el.offsetWidth;                         // restart the animation on a quick second change
        el.setAttribute('data-entering', '');
        clearTimeout(el._muEnterTimer);
        el._muEnterTimer = setTimeout(function () { el.removeAttribute('data-entering'); }, 600);
    }
    function tabPanel(tab) {
        var ids = (tab.getAttribute('aria-controls') || '').split(/\s+/).filter(Boolean);
        for (var i = 0; i < ids.length; i++) {
            var byId = document.getElementById(ids[i]);
            if (byId && !byId.hidden) return byId;
        }
        // Alpine tabs (inspector, memory) without aria-controls: the visible panel nearest the tablist.
        var list = tab.closest('[role="tablist"]');
        var scope = list && list.parentElement;
        for (var depth = 0; scope && depth < 3; depth++, scope = scope.parentElement) {
            var panels = scope.querySelectorAll('[role="tabpanel"]');
            for (var j = 0; j < panels.length; j++) {
                if (!panels[j].hidden && panels[j].offsetParent !== null) return panels[j];
            }
        }
        return null;
    }
    function initTabMotion() {
        if (!window.MutationObserver) return;
        new MutationObserver(function (records) {
            var chosen = null;
            records.forEach(function (r) {
                var t = r.target;
                if (!t.getAttribute || t.getAttribute('role') !== 'tab') return;
                var now = t.getAttribute('aria-selected') === 'true';
                if (now && r.oldValue !== 'true') chosen = t;
            });
            if (!chosen) return;
            // After the page's own handler shows the panel (Alpine and x-show run in a microtask).
            requestAnimationFrame(function () { enter(tabPanel(chosen)); });
        }).observe(document.body, { subtree: true, attributes: true, attributeOldValue: true, attributeFilter: ['aria-selected'] });
    }
    window.WashMotion = { enter: enter, leave: leave };

    function init() { initSidebar(); initCollapse(); initOrchBadge(); initWorkBadge(); initEmbed(); initKeys(); initPanes(); initTabMotion(); }
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
        if (document.documentElement.hasAttribute('data-embed') || document.documentElement.hasAttribute('data-shell-root')) return null;
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
