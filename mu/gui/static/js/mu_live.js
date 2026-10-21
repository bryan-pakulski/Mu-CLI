// Loaded early (not deferred) by every page: page scripts can open live streams at any point.
// Browsers allow ~6 HTTP/1.1 connections per host, shared by every tab, and each open
// EventSource holds one. With the persistent shell keeping several views alive, and more than
// one tab open, streams used to exhaust that pool: a reconnecting stream then queued forever
// ("Reconnecting to live updates…" until a reload). MuLive.stream keeps a stream open only while
// its page is actually on screen: the tab is visible and, inside the shell, the view is the
// active one (the shell posts {type: 'mu:view', active}). A hidden stream is released after a
// short grace and reopened (with a hello the page can resync from) when shown again. It also
// recovers on its own: any error closes it and reconnects with backoff (EventSource gives up for
// good on an HTTP error), and a watchdog reconnects when nothing — not even the server's
// 15-second `ping` event — arrives for 45 seconds.
(function () {
    'use strict';
    if (window.MuLive) return;
    var HIDE_GRACE_MS = 30000;
    var WATCHDOG_MS = 45000;
    var viewActive = true;
    var listeners = [];
    function visible() { return document.visibilityState !== 'hidden' && viewActive; }
    var last = visible();
    function emit() {
        var now = visible();
        if (now === last) return;
        last = now;
        listeners.slice().forEach(function (fn) { try { fn(now); } catch (e) { console.error(e); } });
    }
    document.addEventListener('visibilitychange', function () {
        emit();
        tabListeners.slice().forEach(function (fn) { try { fn(tabVisible()); } catch (e) { console.error(e); } });
    });
    var tabListeners = [];
    window.addEventListener('message', function (event) {
        if (event.origin !== location.origin || event.source !== window.parent) return;
        var data = event.data || {};
        if (data.type === 'mu:view') { viewActive = !!data.active; emit(); }
    });
    /* opts.tabOnly: follow only the tab's visibility (pages that must stay live while another
       view in the same tab is shown). */
    function onChange(fn, opts) {
        if (opts && opts.tabOnly) { tabListeners.push(fn); return function () { tabListeners = tabListeners.filter(function (f) { return f !== fn; }); }; } listeners.push(fn); return function () { listeners = listeners.filter(function (f) { return f !== fn; }); }; }

    /* stream(url | () => url, {onOpen, onMessage(ev), events: {name: fn(ev)}, onState(live)}) */
    var all = [];
    function stream(url, opts) {
        opts = opts || {};
        var src = null, attempt = 0, retry = 0, watchdog = 0, hideTimer = 0, closed = false;
        all.push({ url: typeof url === 'function' ? 'dynamic' : url, state: function () { return closed ? 'closed' : src ? ['connecting', 'open', 'closed'][src.readyState] : 'released'; } });
        function state(live) { if (opts.onState) opts.onState(live); }
        function bump() {
            clearTimeout(watchdog);
            watchdog = setTimeout(function () { if (src) fail(); }, WATCHDOG_MS);
        }
        function release() {
            clearTimeout(retry); clearTimeout(watchdog);
            if (src) { try { src.close(); } catch (e) { /* closed */ } src = null; }
        }
        function fail() {
            release();
            state(false);
            if (closed || !visible()) return;
            attempt = Math.min(attempt + 1, 6);
            var wait = Math.min(1000 * Math.pow(2, attempt - 1), 30000) * (0.5 + Math.random() * 0.5);
            retry = setTimeout(open, wait);
        }
        function open() {
            if (closed || src) return;
            var s = new EventSource(typeof url === 'function' ? url() : url);
            src = s;
            s.onopen = function () { attempt = 0; bump(); state(true); if (opts.onOpen) opts.onOpen(); };
            s.onmessage = function (ev) { bump(); if (opts.onMessage) opts.onMessage(ev); };
            Object.keys(opts.events || {}).forEach(function (name) {
                s.addEventListener(name, function (ev) { bump(); state(true); opts.events[name](ev); });
            });
            s.addEventListener('ping', bump);
            s.onerror = function () { if (src === s) fail(); };
        }
        onChange(function (now) {
            clearTimeout(hideTimer);
            if (now) { if (!src) { attempt = 0; clearTimeout(retry); open(); } }
            else hideTimer = setTimeout(release, HIDE_GRACE_MS);
        });
        window.addEventListener('pagehide', release);
        window.addEventListener('pageshow', function (ev) { if (ev.persisted && visible()) open(); });
        if (visible()) open();
        return {
            close: function () { closed = true; release(); },
            reconnect: function () { release(); attempt = 0; open(); },
            open: function () { return !!src && src.readyState === 1; },
        };
    }
    function tabVisible() { return document.visibilityState !== 'hidden'; }
    /* Diagnostics: this page's streams and their state (open | connecting | released | closed). */
    function streams() { return all.map(function (x) { return { url: x.url, state: x.state() }; }); }
    /* A page that manages its own EventSource (app.js) lists it here for streams(). */
    function track(url, state) { all.push({ url: url, state: state }); }
    window.MuLive = { visible: visible, tabVisible: tabVisible, onChange: onChange, stream: stream, streams: streams, track: track };
})();
