// Pair this browser (templates/pair.html). Sends the code to POST /api/devices/pair-browser; on
// success the server sets the HttpOnly device cookie and the page goes back to where it was asked
// for. Wrong codes lock out with growing waits (mu/gui/devices.py): the button stays disabled and
// counts down until the next try is allowed.
(function () {
    'use strict';
    // Loaded inside an app view (the shell's iframe): the whole window must become the pairing page.
    if (window.top !== window) {
        try { window.top.location.reload(); return; } catch (e) { /* not same-origin: pair here */ }
    }
    var form = document.querySelector('[data-pair-form]');
    if (!form) return;
    var code = form.querySelector('[data-pair-code]');
    var name = form.querySelector('[data-pair-name]');
    var status = form.querySelector('[data-pair-status]');
    var submit = form.querySelector('[data-pair-submit]');
    var timer = null;
    var LENGTH = parseInt(form.getAttribute('data-code-length') || '12', 10);

    function normalize(value) { return String(value || '').toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, LENGTH); }
    // Groups of four as typed: XXXX-XXXX-XXXX.
    function display(value) { return (normalize(value).match(/.{1,4}/g) || []).join('-'); }
    function say(text, tone) { status.textContent = text || ''; if (tone) status.setAttribute('data-tone', tone); else status.removeAttribute('data-tone'); }
    function wait(seconds) { return seconds < 90 ? seconds + 's' : Math.ceil(seconds / 60) + ' min'; }

    function defaultName() {
        var ua = navigator.userAgent || '';
        var browser = /Edg\//.test(ua) ? 'Edge' : /Firefox\//.test(ua) ? 'Firefox' : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : 'Browser';
        var os = /Android/.test(ua) ? 'Android' : /iPhone|iPad/.test(ua) ? 'iOS' : /Mac OS X/.test(ua) ? 'macOS' : /Windows/.test(ua) ? 'Windows' : /Linux/.test(ua) ? 'Linux' : '';
        return os ? browser + ' on ' + os : browser;
    }

    function lockFor(seconds) {
        if (timer) clearInterval(timer);
        var until = Date.now() + seconds * 1000;
        submit.disabled = true;
        function tick() {
            var left = Math.ceil((until - Date.now()) / 1000);
            if (left <= 0) {
                clearInterval(timer); timer = null;
                submit.disabled = false;
                say('You can try again now.', '');
                return;
            }
            say('Too many attempts. Try again in ' + wait(left) + '.', 'danger');
        }
        tick();
        timer = setInterval(tick, 1000);
    }

    function goNext() {
        var next = form.getAttribute('data-next') || '/';
        try { next = decodeURIComponent(next); } catch (e) { next = '/'; }
        if (next.charAt(0) !== '/' || next.charAt(1) === '/') next = '/';
        window.location.replace(next);
    }

    name.value = defaultName();
    code.addEventListener('input', function () {
        var caret = code.value.length === code.selectionStart;
        code.value = display(code.value);
        if (caret) code.setSelectionRange(code.value.length, code.value.length);
    });

    form.addEventListener('submit', function (event) {
        event.preventDefault();
        if (submit.disabled) return;
        var value = normalize(code.value);
        if (value.length !== LENGTH) { say('The code has ' + LENGTH + ' letters and digits, as shown on the computer.', 'danger'); code.focus(); return; }
        submit.disabled = true;
        say('Pairing…', '');
        fetch('/api/devices/pair-browser', {
            method: 'POST',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ code: value, device_name: name.value })
        }).then(function (r) {
            return r.json().catch(function () { return {}; }).then(function (d) { return { r: r, d: d }; });
        }).then(function (res) {
            if (res.r.ok) { say('Paired. Opening μcli…', 'ok'); goNext(); return; }
            var retry = parseInt(res.r.headers.get('Retry-After') || '0', 10);
            if (res.r.status === 429 && retry > 0) { lockFor(retry); return; }
            submit.disabled = false;
            say(res.d.detail || ('Pairing failed (' + res.r.status + ').'), 'danger');
            code.select();
        }).catch(function () {
            submit.disabled = false;
            say('Couldn’t reach μcli. Check the connection and try again.', 'danger');
        });
    });

    var locked = parseInt(form.getAttribute('data-locked-for') || '0', 10);
    if (locked > 0) lockFor(locked); else code.focus();
})();
