// Wash theme controller (route_theme.js) — one implementation for every page,
// loaded by templates/_layout.html.
//
// Preference (localStorage `mucli-theme`): "system" | "paper" | "dusk".
// Legacy stored values "light"/"dark" are migrated on read (light → paper,
// dark → dusk). The resolved theme is written to html[data-theme] as
// "paper" or "dusk"; the pre-paint bootstrap in partials/theme_bootstrap.html
// does the same before first paint so there is no flash.
//
// Every change dispatches `mucli-theme-change` on window. For the
// visualization theme contract (documentation/visualization_style_guide.md)
// `detail.theme` keeps the legacy "light"/"dark" value; `detail.wash` carries
// "paper"/"dusk" and `detail.preference` the stored preference.
(function () {
    'use strict';
    var KEY = 'mucli-theme';
    var PREFS = ['system', 'paper', 'dusk'];
    var LEGACY = { light: 'paper', dark: 'dusk' };
    var media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

    function normalize(value) {
        var v = String(value || '');
        if (LEGACY[v]) return LEGACY[v];
        return PREFS.indexOf(v) >= 0 ? v : 'system';
    }
    function readPreference() {
        try {
            var raw = localStorage.getItem(KEY);
            var pref = normalize(raw);
            if (raw && raw !== pref) localStorage.setItem(KEY, pref);
            return pref;
        } catch (_) { return 'system'; }
    }
    function resolve(pref) {
        if (pref === 'paper' || pref === 'dusk') return pref;
        return media && media.matches ? 'dusk' : 'paper';
    }
    function legacyName(wash) { return wash === 'dusk' ? 'dark' : 'light'; }
    function current() {
        var t = document.documentElement.getAttribute('data-theme');
        return (t === 'dusk' || t === 'dark') ? 'dusk' : 'paper';
    }

    var listeners = [];
    function apply(pref, opts) {
        var preference = normalize(pref);
        var wash = resolve(preference);
        var root = document.documentElement;
        root.setAttribute('data-theme', wash);
        root.setAttribute('data-theme-pref', preference);
        syncToggles(wash, preference);
        var detail = { theme: legacyName(wash), wash: wash, preference: preference };
        if (!(opts && opts.silent)) {
            listeners.slice().forEach(function (fn) { try { fn(detail); } catch (_) { /* keep notifying */ } });
            window.dispatchEvent(new CustomEvent('mucli-theme-change', { detail: detail }));
        }
        return detail;
    }
    function setPreference(pref) {
        var preference = normalize(pref);
        try { localStorage.setItem('mucli-theme', preference); } catch (_) { /* private mode */ }
        return apply(preference);
    }
    function toggle() {
        return setPreference(current() === 'dusk' ? 'paper' : 'dusk');
    }
    function syncToggles(wash, preference) {
        var next = wash === 'dusk' ? 'Paper' : 'Dusk';
        document.querySelectorAll('[data-route-theme]').forEach(function (button) {
            var label = 'Switch to ' + next + ' theme';
            button.setAttribute('aria-label', label);
            button.title = label;
            button.setAttribute('data-theme-current', wash);
        });
        document.querySelectorAll('[data-theme-select]').forEach(function (select) {
            if (select.value !== preference) select.value = preference;
        });
    }

    // Clicks are delegated so toggles rendered later (Alpine templates,
    // injected markup) work without re-binding.
    document.addEventListener('click', function (event) {
        var button = event.target.closest && event.target.closest('[data-route-theme]');
        if (button && !button.disabled) toggle();
    });
    document.addEventListener('change', function (event) {
        var select = event.target.closest && event.target.closest('[data-theme-select]');
        if (select) setPreference(select.value);
    });
    function bind() {
        syncToggles(current(), readPreference());
    }

    if (media) {
        var onSystem = function () { if (readPreference() === 'system') apply('system'); };
        if (media.addEventListener) media.addEventListener('change', onSystem);
        else if (media.addListener) media.addListener(onSystem);
    }
    window.addEventListener('storage', function (event) {
        if (event.key === KEY) apply(normalize(event.newValue));
    });

    window.MuTheme = {
        KEY: KEY,
        normalize: normalize,
        preference: readPreference,
        resolve: resolve,
        current: current,
        legacy: function () { return legacyName(current()); },
        legacyName: legacyName,
        set: setPreference,
        toggle: toggle,
        apply: apply,
        bind: bind,
        onChange: function (fn) { listeners.push(fn); },
    };

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
    else bind();
})();
