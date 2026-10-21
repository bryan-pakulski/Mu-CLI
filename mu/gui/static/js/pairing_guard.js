// Revocation guard for browsers paired from another machine (mu/gui/devices.py).
//
// When this computer revokes the browser, the GUI must drop to the pairing page at once, with no
// rail, no Settings and no stale views left on screen. Every app page (and the persistent shell)
// loads this before its own scripts:
//   * any same-origin response "401 pairing_required" reloads the whole window (the server then
//     serves the pairing page instead of the app);
//   * a live stream (EventSource) erroring, which is what revocation does to open streams, checks at once;
//   * a paired remote browser also re-checks every 5 s and when the tab comes back into view.
// This computer's own browser (loopback) is never asked to pair, so for it this does nothing.
(function () {
    'use strict';
    if (window.__muPairingGuard) return;
    window.__muPairingGuard = true;
    var nativeFetch = window.fetch ? window.fetch.bind(window) : null;
    var rebooting = false;
    var timer = 0;
    var pending = 0;

    function reboot() {
        if (rebooting) return;
        rebooting = true;
        try { (window.top || window).location.reload(); } catch (e) { window.location.reload(); }
    }
    function sameOrigin(url) {
        try { return new URL(url, window.location.href).origin === window.location.origin; } catch (e) { return false; }
    }
    function probe() {
        if (!nativeFetch || rebooting) return;
        nativeFetch('/api/devices/self', { credentials: 'same-origin', cache: 'no-store' }).then(function (r) {
            if (r.status === 401) { reboot(); return null; }
            return r.ok ? r.json() : null;
        }).then(function (me) {
            // Only a paired remote browser keeps checking.
            if (me && me.device && !timer) timer = setInterval(probe, 5000);
        }).catch(function () { /* offline: the next check retries */ });
    }
    function probeSoon() {
        if (pending) return;
        pending = setTimeout(function () { pending = 0; probe(); }, 500);
    }

    if (nativeFetch) {
        window.fetch = function (input, init) {
            return nativeFetch(input, init).then(function (response) {
                if (response.status === 401 && sameOrigin(response.url)) {
                    response.clone().json().then(function (body) {
                        if (body && body.pairing_required) reboot();
                    }).catch(function () { /* not JSON: not the gate */ });
                }
                return response;
            });
        };
    }

    var NativeEventSource = window.EventSource;
    if (NativeEventSource) {
        var Guarded = function (url, config) {
            var source = new NativeEventSource(url, config);
            source.addEventListener('error', probeSoon);
            return source;
        };
        Guarded.prototype = NativeEventSource.prototype;
        Guarded.CONNECTING = NativeEventSource.CONNECTING;
        Guarded.OPEN = NativeEventSource.OPEN;
        Guarded.CLOSED = NativeEventSource.CLOSED;
        window.EventSource = Guarded;
    }

    document.addEventListener('visibilitychange', function () { if (document.visibilityState === 'visible' && timer) probe(); });
    probe();
})();
