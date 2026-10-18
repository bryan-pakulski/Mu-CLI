/* Wash command palette (G§9.17) — page-agnostic, ⌘K / Ctrl+K.
 *
 * Pages register their own commands; the palette groups and filters them.
 *
 *   WashPalette.register(sourceId, items | () => items, { priority })
 *       items: [{ id, group, label, hint?, keywords?, key?, icon?, href?,
 *                 run?(), enabled? }]
 *       group: "Navigate" | "Sessions" | "Actions" | "Commands" | any string
 *       A function source is re-evaluated each time the palette draws, so
 *       live lists (sessions, slash commands) stay current.
 *   WashPalette.unregister(sourceId)
 *   WashPalette.open(query?) / close() / toggle() / isOpen()
 *   WashPalette.commands()            → the flattened, enabled item list
 *   WashPalette.configure({ goKeys: false })   pages with their own `g x` chords
 *
 * The palette mounts into #w-palette-root (templates/partials/palette.html)
 * or creates it. It is a level-3, 640px centred dialog with a combobox input
 * and a grouped listbox; focus is trapped, Esc closes, focus is restored.
 * It is loaded by templates/_layout.html on every page (/, /work,
 * /containers, /trace, /jobs/trace, /models/costs, /design); each page adds
 * its own sources. Navigation between pages (Chat, Work, Environments,
 * Trace, Job analysis, Model pricing) is registered here. `g c` / `g w` /
 * `g e` / `g t` jump between pages when focus is not in a text field
 * (G§10.5) unless the page owns `g` chords (/work).
 */
(function () {
    'use strict';

    var GROUP_ORDER = ['Navigate', 'Sessions', 'Actions', 'Commands'];
    var sources = new Map();
    var settings = { hotkey: true, goKeys: true };
    var state = { open: false, sel: 0, results: [], restore: null, root: null, input: null, list: null };
    var uid = 0;

    function esc(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    function register(id, items, options) {
        var priority = options && options.priority != null ? options.priority : 0;
        sources.set(String(id), { items: items, priority: priority });
        if (state.open) draw();
        return function () { unregister(id); };
    }

    function unregister(id) {
        sources.delete(String(id));
        if (state.open) draw();
    }

    function commands() {
        var seen = new Set();
        var out = [];
        Array.from(sources.values())
            .sort(function (a, b) { return a.priority - b.priority; })
            .forEach(function (source) {
                var items = source.items;
                if (typeof items === 'function') {
                    try { items = items(); } catch (e) { items = []; }
                }
                (items || []).forEach(function (item) {
                    if (!item || item.enabled === false) return;
                    var key = String(item.id || (item.group + ':' + item.label));
                    if (seen.has(key)) return;
                    seen.add(key);
                    out.push(item);
                });
            });
        return out;
    }

    function score(query, text) {
        if (!query) return 0;
        var t = String(text).toLowerCase();
        var q = String(query).toLowerCase().trim();
        if (!q) return 0;
        var at = t.indexOf(q);
        if (at >= 0) return 1000 - at;
        var s = 0, pos = 0;
        for (var i = 0; i < q.length; i += 1) {
            if (q[i] === ' ') continue;
            var found = t.indexOf(q[i], pos);
            if (found < 0) return null;
            s += found === pos ? 5 : 1;
            pos = found + 1;
        }
        return s;
    }

    function groupRank(group) {
        var index = GROUP_ORDER.indexOf(group);
        return index < 0 ? GROUP_ORDER.length : index;
    }

    function filtered(query) {
        return commands()
            .map(function (item, index) {
                return { item: item, index: index, s: score(query, [item.label, item.group, item.hint, item.keywords].join(' ')) };
            })
            .filter(function (x) { return x.s != null; })
            .sort(function (a, b) {
                if (query) return b.s - a.s || a.index - b.index;
                return groupRank(a.item.group) - groupRank(b.item.group) || a.index - b.index;
            })
            .slice(0, 60)
            .map(function (x) { return x.item; });
    }

    function mount() {
        var root = document.getElementById('w-palette-root');
        if (!root) {
            root = document.createElement('div');
            root.id = 'w-palette-root';
            root.className = 'w-palette-root';
            root.setAttribute('data-palette-mount', '');
            root.hidden = true;
            document.body.appendChild(root);
        }
        return root;
    }

    function build() {
        var root = mount();
        uid += 1;
        var listId = 'w-palette-list-' + uid;
        var titleId = 'w-palette-title-' + uid;
        root.innerHTML = ''
            + '<div class="w-palette" data-palette-backdrop>'
            + '<div class="w-palette__dialog" role="dialog" aria-modal="true" aria-labelledby="' + titleId + '">'
            + '<h2 class="w-sr-only" id="' + titleId + '">Command palette</h2>'
            + '<div class="w-palette__search">'
            + '<svg class="w-icon" aria-hidden="true" focusable="false"><use href="/static/icons/wash.svg#i-search"></use></svg>'
            + '<input class="w-palette__input" type="text" role="combobox" aria-expanded="true" aria-autocomplete="list"'
            + ' aria-controls="' + listId + '" aria-label="Search commands" placeholder="Go to, switch session or run a command…" autocomplete="off" spellcheck="false">'
            + '<kbd class="w-keycap">Esc</kbd>'
            + '</div>'
            + '<ul class="w-palette__list" id="' + listId + '" role="listbox" aria-label="Commands"></ul>'
            + '<p class="w-palette__foot"><span><kbd class="w-keycap">↑</kbd><kbd class="w-keycap">↓</kbd> move</span><span><kbd class="w-keycap">⏎</kbd> run</span><span><kbd class="w-keycap">⌘K</kbd> toggle</span></p>'
            + '</div></div>';
        state.root = root;
        state.input = root.querySelector('.w-palette__input');
        state.list = root.querySelector('.w-palette__list');
        state.listId = listId;
        root.querySelector('[data-palette-backdrop]').addEventListener('mousedown', function (event) {
            if (event.target === event.currentTarget) close();
        });
        state.input.addEventListener('input', function () { state.sel = 0; draw(); });
        state.input.addEventListener('keydown', onKey);
        state.list.addEventListener('mousemove', function (event) {
            var li = event.target.closest('[data-index]');
            if (li && Number(li.dataset.index) !== state.sel) { state.sel = Number(li.dataset.index); paintSelection(); }
        });
        state.list.addEventListener('click', function (event) {
            var li = event.target.closest('[data-index]');
            if (li) choose(state.results[Number(li.dataset.index)]);
        });
        root.addEventListener('keydown', function (event) {
            if (event.key === 'Tab') { event.preventDefault(); state.input.focus(); }
        });
    }

    function draw() {
        if (!state.list) return;
        var query = state.input.value;
        state.results = filtered(query);
        state.sel = Math.min(state.sel, Math.max(0, state.results.length - 1));
        var html = '';
        var lastGroup = null;
        state.results.forEach(function (item, n) {
            var group = item.group || 'Commands';
            if (!query && group !== lastGroup) {
                html += '<li class="w-palette__group" role="presentation">' + esc(group) + '</li>';
                lastGroup = group;
            }
            html += '<li class="w-palette__item" role="option" id="' + state.listId + '-' + n + '" data-index="' + n + '" aria-selected="' + (n === state.sel) + '">'
                + (item.icon ? '<svg class="w-icon" data-size="sm" aria-hidden="true" focusable="false"><use href="/static/icons/wash.svg#i-' + esc(item.icon) + '"></use></svg>' : '<span class="w-palette__bullet" aria-hidden="true"></span>')
                + '<span class="w-palette__label">' + esc(item.label) + '</span>'
                + (item.hint ? '<span class="w-palette__hint">' + esc(item.hint) + '</span>' : '')
                + (query ? '<span class="w-palette__hint">' + esc(group) + '</span>' : '')
                + (item.key ? '<kbd class="w-keycap">' + esc(item.key) + '</kbd>' : '')
                + '</li>';
        });
        state.list.innerHTML = html || '<li class="w-palette__empty" role="presentation">No matching command</li>';
        paintSelection();
    }

    function paintSelection() {
        if (!state.list) return;
        state.list.querySelectorAll('[data-index]').forEach(function (li) {
            li.setAttribute('aria-selected', String(Number(li.dataset.index) === state.sel));
        });
        var active = state.results[state.sel] ? state.listId + '-' + state.sel : '';
        state.input.setAttribute('aria-activedescendant', active);
        var node = active && document.getElementById(active);
        if (node && node.scrollIntoView) node.scrollIntoView({ block: 'nearest' });
    }

    function onKey(event) {
        if (event.key === 'ArrowDown') { event.preventDefault(); state.sel = Math.min(state.results.length - 1, state.sel + 1); paintSelection(); }
        else if (event.key === 'ArrowUp') { event.preventDefault(); state.sel = Math.max(0, state.sel - 1); paintSelection(); }
        else if (event.key === 'Enter') { event.preventDefault(); choose(state.results[state.sel]); }
        else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); close(); }
    }

    function choose(item) {
        if (!item) return;
        close();
        setTimeout(function () {
            if (typeof item.run === 'function') item.run();
            else if (item.href) window.location.href = item.href;
        }, 0);
    }

    function open(query) {
        if (state.open) { state.input.focus(); return; }
        state.restore = document.activeElement;
        build();
        state.open = true;
        state.sel = 0;
        state.root.hidden = false;
        document.documentElement.setAttribute('data-palette-open', '');
        if (query) state.input.value = String(query);
        draw();
        state.input.focus();
    }

    function close() {
        if (!state.open) return;
        state.open = false;
        if (state.root) { state.root.hidden = true; state.root.innerHTML = ''; }
        state.list = null;
        state.input = null;
        document.documentElement.removeAttribute('data-palette-open');
        var restore = state.restore;
        state.restore = null;
        if (restore && restore.focus && document.contains(restore)) restore.focus({ preventScroll: true });
    }

    function toggle() { if (state.open) close(); else open(); }

    function configure(options) {
        Object.keys(options || {}).forEach(function (key) { settings[key] = options[key]; });
    }

    function isTyping(target) {
        var el = target || document.activeElement;
        if (!el) return false;
        return el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName);
    }

    var goPending = 0;
    var GO_KEYS = { c: '/', w: '/work', e: '/containers', t: '/trace' };
    document.addEventListener('keydown', function (event) {
        if (!settings.hotkey || document.body.getAttribute('data-palette-hotkey') === 'off') return;
        if ((event.metaKey || event.ctrlKey) && !event.altKey && !event.shiftKey && String(event.key).toLowerCase() === 'k') {
            event.preventDefault();
            toggle();
            return;
        }
        if (!settings.goKeys || state.open || event.metaKey || event.ctrlKey || event.altKey || isTyping(event.target)) return;
        if (document.querySelector('[role="dialog"][aria-modal="true"]:not([hidden])') && document.querySelector('.w-modal:not([hidden]), .w-drawer:not([hidden])')) return;
        var key = String(event.key).toLowerCase();
        if (goPending && Date.now() - goPending < 1200 && GO_KEYS[key]) {
            goPending = 0;
            event.preventDefault();
            window.location.href = GO_KEYS[key];
            return;
        }
        goPending = key === 'g' ? Date.now() : 0;
    });

    register('wash:navigate', [
        { id: 'nav:chat', group: 'Navigate', label: 'Chat', icon: 'message-circle', href: '/', key: 'g c' },
        { id: 'nav:work', group: 'Navigate', label: 'Work', icon: 'briefcase', href: '/work', key: 'g w' },
        { id: 'nav:environments', group: 'Navigate', label: 'Environments', icon: 'box', href: '/containers', key: 'g e' },
        { id: 'nav:trace', group: 'Navigate', label: 'Trace', icon: 'activity', href: '/trace', key: 'g t' },
        { id: 'nav:job-analysis', group: 'Navigate', label: 'Job analysis', icon: 'bar-chart-3', href: '/jobs/trace' },
        { id: 'nav:model-pricing', group: 'Navigate', label: 'Model pricing', icon: 'sliders-horizontal', href: '/models/costs' },
        { id: 'nav:theme', group: 'Actions', label: 'Toggle Paper / Dusk theme', icon: 'moon', run: function () { if (window.MuTheme) window.MuTheme.toggle(); } },
    ], { priority: 100 });

    window.WashPalette = {
        register: register,
        unregister: unregister,
        commands: commands,
        open: open,
        close: close,
        toggle: toggle,
        isOpen: function () { return state.open; },
        configure: configure,
    };
})();
