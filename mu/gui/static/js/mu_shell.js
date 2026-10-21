// The persistent shell (mu/gui/shell.py, templates/shell.html; Mu §3 revision 2).
//
// One document for the whole app. Every destination (/, /work, /orchestration,
// /containers, /trace, /jobs/trace, /models/costs, /settings) is a keep-alive view:
// an iframe of the same URL in embed mode, created on first visit and never torn
// down, so switching destinations is instant and keeps scroll, input, open sheets
// and live streams. Nothing here reloads a page.
//
// - Rail clicks, palette jumps (window.MuNav.go) and links inside views that point
//   at another destination switch views and push one history entry; back/forward
//   switch back. Unknown URLs navigate normally.
// - Views report their URL and title (wash_shell.js embed side: `mu:url`); the
//   active view's URL is the address bar (without `embed=1`), its title the tab's.
// - A view asked to show a different hash/query of its own path gets `mu:route`.
// - The mode (html[data-mode]) follows the active destination: the watercolour
//   cross-fades between two layers and the accent eases over (CSS, base/shell).
// - Theme and decoration changes reach every view (`mu:theme`).
(function () {
    'use strict';

    var DEST = window.MU_DESTINATIONS || {};
    var host = document.querySelector('[data-views]');
    if (!host) return;
    var views = new Map();          // path → {frame, href, title, loaded}
    var active = null;

    function own(obj, key) { return Object.prototype.hasOwnProperty.call(obj, key); }
    function keyOf(url) { return own(DEST, url.pathname) ? url.pathname : null; }
    function clean(href) {
        var url = new URL(href, location.href);
        url.searchParams.delete('embed');
        return url.pathname + url.search + url.hash;
    }
    function embedSrc(href) {
        var url = new URL(href, location.href);
        url.searchParams.set('embed', '1');
        return url.pathname + url.search + url.hash;
    }
    function post(view, message) {
        try { view.frame.contentWindow.postMessage(message, location.origin); } catch (e) { /* not ready */ }
    }
    function labelFor(key) {
        var nav = DEST[key][0];
        var item = document.querySelector('#w-sidebar [data-nav="' + nav + '"] .w-nav__label');
        return item ? item.textContent : nav;
    }

    // ------------------------------------------------------------ mode + rail
    var layers = Array.prototype.slice.call(document.querySelectorAll('[data-ambient-layer]'));
    function setMode(key) {
        var mode = DEST[key][1];
        var root = document.documentElement;
        if (root.getAttribute('data-mode') !== mode) {
            root.setAttribute('data-mode', mode);
            // Cross-fade: the hidden layer takes the new mode, then the two swap opacity.
            var on = layers.find(function (l) { return l.hasAttribute('data-on'); }) || layers[0];
            var off = layers.find(function (l) { return l !== on; });
            if (off) {
                off.setAttribute('data-mode', mode);
                requestAnimationFrame(function () {
                    off.setAttribute('data-on', '');
                    if (on) on.removeAttribute('data-on');
                });
            }
        }
        var nav = DEST[key][0];
        document.querySelectorAll('#w-sidebar [data-nav]').forEach(function (item) {
            if (item.getAttribute('data-nav') === nav) item.setAttribute('aria-current', 'page');
            else item.removeAttribute('aria-current');
        });
    }

    // ------------------------------------------------------------ views
    function create(key, href) {
        var frame = document.createElement('iframe');
        frame.className = 'mu-view';
        frame.title = labelFor(key);
        frame.setAttribute('allow', 'clipboard-read; clipboard-write; microphone; fullscreen');
        frame.src = embedSrc(href);
        var view = { frame: frame, href: clean(href), title: '', loaded: false };
        frame.addEventListener('load', function () {
            view.loaded = true;
            frame.removeAttribute('data-loading');
            post(view, { type: 'mu:view', active: active === key });   // loaded while hidden: say so
        });
        frame.setAttribute('data-loading', '');
        host.appendChild(frame);
        views.set(key, view);
        return view;
    }
    function show(key) {
        views.forEach(function (view, k) {
            var on = k === key;
            view.frame.toggleAttribute('data-active', on);
            view.frame.setAttribute('aria-hidden', on ? 'false' : 'true');
            view.frame.tabIndex = on ? 0 : -1;
            // Hidden views release their live streams (MuLive in mu_live.js): connections are scarce.
            post(view, { type: 'mu:view', active: on });
        });
    }
    function navigate(href, opts) {
        opts = opts || {};
        var url;
        try { url = new URL(href, location.href); } catch (e) { return; }
        if (url.origin !== location.origin) { location.assign(href); return; }
        var key = keyOf(url);
        if (!key) { location.assign(url.href); return; }
        var target = clean(url.href);
        var view = views.get(key);
        if (!view) view = create(key, target);
        else if (target !== view.href) {
            post(view, { type: 'mu:route', href: target });
            view.href = target;
        }
        var switching = active !== key;
        active = key;
        show(key);
        setMode(key);
        if (opts.push !== false && clean(location.href) !== target) history.pushState({ mu: key }, '', target);
        else history.replaceState({ mu: key }, '', target);
        if (view.title) document.title = view.title;
        if (switching && opts.focus !== false) {
            // Keys (palette, shortcuts) go to the view you just opened.
            setTimeout(function () { try { view.frame.focus(); view.frame.contentWindow.focus(); } catch (e) { /* loading */ } }, 0);
        }
    }

    // ------------------------------------------------------------ messages from views
    window.addEventListener('message', function (event) {
        if (event.origin !== location.origin) return;
        var data = event.data || {};
        var key = null, view = null;
        views.forEach(function (v, k) { if (v.frame.contentWindow === event.source) { key = k; view = v; } });
        if (!view) return;
        if (data.type === 'mu:url' && typeof data.href === 'string') {
            var reported = new URL(data.href, location.href);
            if (reported.pathname !== key && keyOf(reported)) {
                // The view followed a link to another destination: open it in its own
                // view and put this one back where it was.
                try { view.frame.contentWindow.history.back(); } catch (e) { /* ignore */ }
                navigate(reported.href);
                return;
            }
            view.href = clean(reported.href);
            if (data.title) view.title = data.title;
            if (key === active) {
                if (clean(location.href) !== view.href) history.replaceState({ mu: key }, '', view.href);
                if (view.title) document.title = view.title;
            }
        } else if (data.type === 'mu:go' && typeof data.href === 'string') {
            navigate(data.href);
        } else if (data.type === 'mu:sidebar' && window.WashShell) {
            window.WashShell.toggleSidebar();
        }
    });

    // ------------------------------------------------------------ rail, palette, history
    // Capture phase: before wash_shell.js's scripted page fade claims the click.
    document.addEventListener('click', function (event) {
        if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        var link = event.target.closest && event.target.closest('a[href]');
        if (!link || link.hasAttribute('download') || (link.target && link.target !== '_self')) return;
        var url;
        try { url = new URL(link.href, location.href); } catch (e) { return; }
        if (url.origin !== location.origin || !keyOf(url)) return;
        event.preventDefault();
        event.stopPropagation();
        navigate(url.href);
    }, true);
    window.addEventListener('popstate', function () { navigate(location.href, { push: false, focus: false }); });
    window.MuNav = { go: function (href) { navigate(href); } };
    window.MuShell = { go: navigate, views: views, active: function () { return active; } };

    // Theme and decoration: views follow the shell (storage events reach them too;
    // this is the immediate path).
    function broadcast() {
        var detail = {
            type: 'mu:theme',
            theme: window.MuTheme ? window.MuTheme.preference() : null,
            decoration: window.MuDecoration ? window.MuDecoration.preference() : null,
            palette: window.MuPalette ? window.MuPalette.preference() : null,
            glass: window.MuGlass ? window.MuGlass.preference() : null,
            blur: window.MuGlassBlur ? window.MuGlassBlur.preference() : null,
            textScale: window.MuTextScale ? window.MuTextScale.preference() : null,
        };
        views.forEach(function (view) { post(view, detail); });
    }
    window.addEventListener('mucli-theme-change', broadcast);
    window.addEventListener('mucli-decoration-change', broadcast);
    window.addEventListener('mucli-palette-change', broadcast);
    window.addEventListener('mucli-glass-change', broadcast);
    window.addEventListener('mucli-text-scale-change', broadcast);

    navigate(location.href, { push: false, focus: false });
})();
