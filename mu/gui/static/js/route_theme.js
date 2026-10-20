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

// "Reduce decoration" (G§6.2 rule 5) — one controller for every page.
// Preference (localStorage `mucli-decoration`): "reduced" | "full"; unset
// follows prefers-reduced-transparency. The resolved state is written to
// html[data-decoration="reduced"] (the pre-paint bootstrap does the same),
// and any `[data-decoration-toggle]` checkbox (the settings drawer switch)
// stays in sync.
(function () {
    'use strict';
    var KEY = 'mucli-decoration';
    var media = window.matchMedia ? window.matchMedia('(prefers-reduced-transparency: reduce)') : null;

    function preference() {
        try {
            var raw = localStorage.getItem(KEY);
            return raw === 'reduced' || raw === 'full' ? raw : 'system';
        } catch (_) { return 'system'; }
    }
    function reduced(pref) {
        if (pref === 'reduced') return true;
        if (pref === 'full') return false;
        return !!(media && media.matches);
    }
    function apply(pref) {
        var on = reduced(pref || preference());
        var root = document.documentElement;
        if (on) root.setAttribute('data-decoration', 'reduced');
        else root.removeAttribute('data-decoration');
        document.querySelectorAll('[data-decoration-toggle]').forEach(function (input) {
            input.checked = on;
            input.setAttribute('aria-checked', on ? 'true' : 'false');
        });
        window.dispatchEvent(new CustomEvent('mucli-decoration-change', { detail: { reduced: on, preference: pref || preference() } }));
        return on;
    }
    function set(value) {
        var pref = value === true || value === 'reduced' ? 'reduced' : 'full';
        try { localStorage.setItem(KEY, pref); } catch (_) { /* private mode */ }
        return apply(pref);
    }

    document.addEventListener('change', function (event) {
        var input = event.target.closest && event.target.closest('[data-decoration-toggle]');
        if (input) set(!!input.checked);
    });
    if (media) {
        var onSystem = function () { if (preference() === 'system') apply('system'); };
        if (media.addEventListener) media.addEventListener('change', onSystem);
        else if (media.addListener) media.addListener(onSystem);
    }
    window.addEventListener('storage', function (event) { if (event.key === KEY) apply(); });

    window.MuDecoration = { KEY: KEY, preference: preference, reduced: function () { return reduced(preference()); }, set: set, apply: apply };

    // Alpine renders the drawer switch after this script; sync once it exists.
    function sync() { apply(); }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', sync);
    else sync();
    document.addEventListener('alpine:initialized', sync);
})();

// Palette (Mu revision 2) — one controller for every page and the shell.
// Preference (localStorage `mucli-palette`): "wash" (the default: watercolour Paper /
// Dusk) | "darcula" | "one" | "material" | "tokyo". Any other value reads as wash.
// Applied as html[data-palette] (removed for wash; the pre-paint bootstrap does the
// same) and combined with html[data-theme] for light or dark: every palette has both.
// Controls: any [data-palette-choice="<name>"] button (aria-pressed is kept in sync).
// A change dispatches `mucli-palette-change` and `mucli-theme-change` (charts redraw
// from the tokens on the latter).
(function () {
    'use strict';
    var KEY = 'mucli-palette';
    var NAMES = ['wash', 'darcula', 'one', 'material', 'tokyo'];
    function normalize(value) { return NAMES.indexOf(String(value || '')) >= 0 ? String(value) : 'wash'; }
    function preference() {
        try { return normalize(localStorage.getItem(KEY)); } catch (_) { return 'wash'; }
    }
    function apply(pref, opts) {
        var name = normalize(pref || preference());
        var root = document.documentElement;
        if (name === 'wash') root.removeAttribute('data-palette');
        else root.setAttribute('data-palette', name);
        document.querySelectorAll('[data-palette-choice]').forEach(function (button) {
            button.setAttribute('aria-pressed', button.getAttribute('data-palette-choice') === name ? 'true' : 'false');
        });
        if (!(opts && opts.silent)) {
            window.dispatchEvent(new CustomEvent('mucli-palette-change', { detail: { palette: name } }));
            if (window.MuTheme) {
                var wash = window.MuTheme.current();
                window.dispatchEvent(new CustomEvent('mucli-theme-change', {
                    detail: { theme: window.MuTheme.legacyName(wash), wash: wash, preference: window.MuTheme.preference(), palette: name },
                }));
            }
        }
        return name;
    }
    function set(value) {
        var name = normalize(value);
        try { localStorage.setItem(KEY, name); } catch (_) { /* private mode */ }
        return apply(name);
    }
    document.addEventListener('click', function (event) {
        var button = event.target.closest && event.target.closest('[data-palette-choice]');
        if (button && !button.disabled) set(button.getAttribute('data-palette-choice'));
    });
    window.addEventListener('storage', function (event) { if (event.key === KEY) apply(event.newValue); });
    window.MuPalette = { KEY: KEY, NAMES: NAMES, preference: preference, current: function () { return normalize(document.documentElement.getAttribute('data-palette')); }, set: set, apply: apply };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { apply(null, { silent: true }); });
    else apply(null, { silent: true });
})();

// Glass (Mu revision 2; Wash theme only) — Settings → Appearance → Glass.
// Preference (localStorage `mucli-glass`): 0–100, default 60. Applied as the CSS
// variable --mu-glass (0–1) and html[data-glass] when above 0 (the pre-paint
// bootstrap does the same); patterns/shell.css turns the leaf to frosted glass
// only when no other palette is chosen and decoration isn't reduced.
// Controls: any input[data-glass-range]; output[data-glass-value] shows the value.
(function () {
    'use strict';
    var KEY = 'mucli-glass';
    var DEFAULT = 60;
    function clamp(value) {
        var n = parseInt(value, 10);
        if (isNaN(n)) return DEFAULT;
        return Math.max(0, Math.min(100, Math.round(n / 5) * 5));
    }
    function preference() {
        try { var raw = localStorage.getItem(KEY); return raw == null ? DEFAULT : clamp(raw); } catch (_) { return DEFAULT; }
    }
    function apply(value, opts) {
        var v = clamp(value == null ? preference() : value);
        var root = document.documentElement;
        root.style.setProperty('--mu-glass', String(v / 100));
        if (v > 0) root.setAttribute('data-glass', ''); else root.removeAttribute('data-glass');
        document.querySelectorAll('[data-glass-range]').forEach(function (input) { if (String(input.value) !== String(v)) input.value = v; input.style.setProperty('--w-fill', v + '%'); });
        document.querySelectorAll('[data-glass-value]').forEach(function (out) { out.textContent = v ? v + '%' : 'Off'; });
        if (!(opts && opts.silent)) window.dispatchEvent(new CustomEvent('mucli-glass-change', { detail: { glass: v } }));
        return v;
    }
    function set(value) {
        var v = clamp(value);
        try { localStorage.setItem(KEY, String(v)); } catch (_) { /* private mode */ }
        return apply(v);
    }
    document.addEventListener('input', function (event) {
        var input = event.target.closest && event.target.closest('[data-glass-range]');
        if (input) set(input.value);
    });
    window.addEventListener('storage', function (event) { if (event.key === KEY) apply(event.newValue == null ? DEFAULT : event.newValue); });
    window.MuGlass = { KEY: KEY, DEFAULT: DEFAULT, preference: preference, set: set, apply: apply };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { apply(null, { silent: true }); });
    else apply(null, { silent: true });
})();

// Glass blur (Wash theme only) — Settings → Appearance → Blur, beside Glass.
// Preference (localStorage `mucli-glass-blur`): 0–100, default 60 → --mu-blur (0–1);
// patterns/shell.css scales the leaf's backdrop blur (up to 40px) and the frosted bars by it.
// Controls: input[data-blur-range]; output[data-blur-value].
(function () {
    'use strict';
    var KEY = 'mucli-glass-blur';
    var DEFAULT = 60;
    function clamp(value) {
        var n = parseInt(value, 10);
        if (isNaN(n)) return DEFAULT;
        return Math.max(0, Math.min(100, Math.round(n / 5) * 5));
    }
    function preference() {
        try { var raw = localStorage.getItem(KEY); return raw == null ? DEFAULT : clamp(raw); } catch (_) { return DEFAULT; }
    }
    function apply(value, opts) {
        var v = clamp(value == null ? preference() : value);
        document.documentElement.style.setProperty('--mu-blur', String(v / 100));
        document.querySelectorAll('[data-blur-range]').forEach(function (input) { if (String(input.value) !== String(v)) input.value = v; input.style.setProperty('--w-fill', v + '%'); });
        document.querySelectorAll('[data-blur-value]').forEach(function (out) { out.textContent = v ? v + '%' : 'Off'; });
        if (!(opts && opts.silent)) window.dispatchEvent(new CustomEvent('mucli-glass-change', { detail: { blur: v } }));
        return v;
    }
    function set(value) {
        var v = clamp(value);
        try { localStorage.setItem(KEY, String(v)); } catch (_) { /* private mode */ }
        return apply(v);
    }
    document.addEventListener('input', function (event) {
        var input = event.target.closest && event.target.closest('[data-blur-range]');
        if (input) set(input.value);
    });
    window.addEventListener('storage', function (event) { if (event.key === KEY) apply(event.newValue == null ? DEFAULT : event.newValue); });
    window.MuGlassBlur = { KEY: KEY, DEFAULT: DEFAULT, preference: preference, set: set, apply: apply };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { apply(null, { silent: true }); });
    else apply(null, { silent: true });
})();

// Text size — Settings → Appearance → Text size (every theme). Preference (localStorage
// `mucli-text-scale`): percent of the design, 85–120 in steps of 5 (tokens.json
// density.text_scale), default 100. Applied as --mu-scale on <html> (the pre-paint bootstrap
// does the same); tokens.css multiplies type, line heights, spacing and control sizes by it, so
// a smaller size packs more on screen and keeps its proportions. Controls:
// input[data-text-scale-range]; output[data-text-scale-value].
(function () {
    'use strict';
    var KEY = 'mucli-text-scale';
    var MIN = 85, MAX = 120, STEP = 5, DEFAULT = 100;
    function clamp(value) {
        var n = parseInt(value, 10);
        if (isNaN(n)) return DEFAULT;
        return Math.max(MIN, Math.min(MAX, Math.round(n / STEP) * STEP));
    }
    function preference() {
        try { var raw = localStorage.getItem(KEY); return raw == null ? DEFAULT : clamp(raw); } catch (_) { return DEFAULT; }
    }
    function apply(value, opts) {
        var v = clamp(value == null ? preference() : value);
        var root = document.documentElement;
        if (v === DEFAULT) root.style.removeProperty('--mu-scale');
        else root.style.setProperty('--mu-scale', String(v / 100));
        document.querySelectorAll('[data-text-scale-range]').forEach(function (input) {
            if (String(input.value) !== String(v)) input.value = v;
            input.style.setProperty('--w-fill', ((v - MIN) / (MAX - MIN) * 100) + '%');
        });
        document.querySelectorAll('[data-text-scale-value]').forEach(function (out) { out.textContent = v + '%'; });
        if (!(opts && opts.silent)) window.dispatchEvent(new CustomEvent('mucli-text-scale-change', { detail: { textScale: v } }));
        return v;
    }
    function set(value) {
        var v = clamp(value);
        try { localStorage.setItem(KEY, String(v)); } catch (_) { /* private mode */ }
        return apply(v);
    }
    document.addEventListener('input', function (event) {
        var input = event.target.closest && event.target.closest('[data-text-scale-range]');
        if (input) set(input.value);
    });
    document.addEventListener('click', function (event) {
        var reset = event.target.closest && event.target.closest('[data-text-scale-reset]');
        if (reset) set(DEFAULT);
    });
    window.addEventListener('storage', function (event) { if (event.key === KEY) apply(event.newValue == null ? DEFAULT : event.newValue); });
    window.MuTextScale = { KEY: KEY, MIN: MIN, MAX: MAX, STEP: STEP, DEFAULT: DEFAULT, preference: preference, set: set, apply: apply };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', function () { apply(null, { silent: true }); });
    else apply(null, { silent: true });
})();
