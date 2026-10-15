/* Wash tool call card (G§9.8) — the single representation of a tool
 * invocation in the web GUI.
 *
 *   renderToolCard(card, options)      one card → HTML string
 *   renderToolCards(events, options)   a trace's event list → HTML string
 *                                       (pairs tool_call/tool_result, groups
 *                                       3+ consecutive same-kind calls, adds
 *                                       thinking rows and notes)
 *   buildToolCards(events, options)    the view model renderToolCards uses
 *   subagentActionCard(action)         maps a sub-agent action onto a card
 *
 * One renderer is shared by live streaming and history hydration (both feed
 * trace turns whose `events` come from app.js addToolCall/addToolResult and
 * _historyTurnsToTimeline) and by the sub-agent panel's action timeline, so
 * the three can never drift apart.
 *
 * Output is an HTML string for Alpine `x-html`; every model-supplied value is
 * escaped. Expand/collapse state lives on the source objects (`open`,
 * `groupOpen`, `showAll`) so it survives re-renders: the delegated click
 * handler below looks the object up in a registry keyed by the rendered id
 * and flips the flag on the (reactive) object, which re-runs the binding.
 *
 * A finished card shows ✓ in ok ink for 1.2s, then settles to neutral ink-3
 * (CSS animation driven by data-fresh + --w-age) so a long transcript is not
 * a field of green ticks.
 */
(function () {
    'use strict';

    var SPRITE = '/static/icons/wash.svg#i-';
    var SETTLE_MS = 1200;
    var PREVIEW_LINES = 20;
    var registry = new Map();

    function esc(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }

    function icon(name) {
        return '<svg class="w-icon" data-size="sm" aria-hidden="true" focusable="false"><use href="' + SPRITE + name + '"></use></svg>';
    }

    // ------------------------------------------------------------ kinds
    var KINDS = {
        read:      { icon: 'file-text', label: 'read', noun: ['file', 'files'], verb: 'Read' },
        edit:      { icon: 'pencil', label: 'edit', noun: ['file', 'files'], verb: 'Edited' },
        shell:     { icon: 'terminal', label: 'shell', noun: ['command', 'commands'], verb: 'Ran' },
        search:    { icon: 'search', label: 'search', noun: ['search', 'searches'], verb: 'Ran' },
        web:       { icon: 'globe', label: 'web', noun: ['lookup', 'lookups'], verb: 'Made' },
        agent:     { icon: 'bot', label: 'sub-agent', noun: ['task', 'tasks'], verb: 'Delegated' },
        memory:    { icon: 'brain', label: 'memory', noun: ['memory call', 'memory calls'], verb: 'Made' },
        plan:      { icon: 'list-checks', label: 'plan', noun: ['plan update', 'plan updates'], verb: 'Made' },
        container: { icon: 'box', label: 'container', noun: ['container call', 'container calls'], verb: 'Made' },
        tool:      { icon: 'wrench', label: 'tool', noun: ['tool call', 'tool calls'], verb: 'Made' },
    };

    function kindOf(name) {
        var n = String(name || '').toLowerCase();
        if (/^(read_file|read_files|get_chunk|list_dir|list_files|view_file|open_file|cat|tree|read_cached|get_file)/.test(n)) return 'read';
        if (/(write_file|apply_patch|edit_file|replace_in_file|create_file|delete_file|move_file|str_replace|insert_text|patch)/.test(n)) return 'edit';
        if (/^(bash|shell|run_command|run_shell|execute|exec|terminal|run_tests|python)/.test(n)) return 'shell';
        if (/(search_for_string|grep|glob|find_files|search_code|codebase_search|ripgrep|search_files|find_symbol)/.test(n)) return 'search';
        if (/(web_search|fetch_url|web_fetch|browse|http_get|download_url|fetch_page)/.test(n)) return 'web';
        if (/(spawn_agent|delegate|subagent|sub_agent)/.test(n)) return 'agent';
        if (/(memory|remember|recall|forget)/.test(n)) return 'memory';
        if (/(plan|todo|goal|backlog|checklist)/.test(n)) return 'plan';
        if (/(container|docker)/.test(n)) return 'container';
        return 'tool';
    }

    // ------------------------------------------------------------ args
    function parseArgs(args) {
        if (args == null) return null;
        if (typeof args === 'object') return args;
        var text = String(args).trim();
        if (text.charAt(0) === '{' || text.charAt(0) === '[') {
            try { return JSON.parse(text); } catch (e) { return text; }
        }
        return text;
    }

    var PRIMARY_KEYS = ['path', 'file_path', 'filepath', 'filename', 'file', 'paths', 'files', 'command', 'cmd',
        'query', 'pattern', 'search', 'url', 'urls', 'task', 'goal', 'name', 'key', 'title', 'text'];

    var SEARCH_KEYS = ['query', 'pattern', 'search', 'regex', 'text'];

    function primaryArg(args, kind) {
        var parsed = parseArgs(args);
        if (kind === 'search' && parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
            for (var s = 0; s < SEARCH_KEYS.length; s += 1) {
                if (typeof parsed[SEARCH_KEYS[s]] === 'string' && parsed[SEARCH_KEYS[s]]) return '"' + parsed[SEARCH_KEYS[s]] + '"';
            }
        }
        if (parsed == null) return '';
        if (typeof parsed !== 'object') return String(parsed);
        if (Array.isArray(parsed)) return parsed.map(String).join(', ');
        for (var i = 0; i < PRIMARY_KEYS.length; i += 1) {
            var value = parsed[PRIMARY_KEYS[i]];
            if (value == null || value === '') continue;
            if (Array.isArray(value)) return value.map(String).join(', ');
            if (typeof value !== 'object') return String(value);
        }
        var keys = Object.keys(parsed);
        for (var j = 0; j < keys.length; j += 1) {
            var v = parsed[keys[j]];
            if (typeof v === 'string' && v) return v;
        }
        return '';
    }

    function secondaryMeta(kind, args) {
        var parsed = parseArgs(args);
        if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return '';
        var start = parsed.start_line != null ? parsed.start_line : (parsed.line_start != null ? parsed.line_start : parsed.offset);
        var end = parsed.end_line != null ? parsed.end_line : (parsed.line_end != null ? parsed.line_end : null);
        if (end == null && parsed.limit != null && start != null) end = Number(start) + Number(parsed.limit);
        if (kind === 'read' && start != null) return 'lines ' + start + (end != null ? '–' + end : '+');
        if (kind === 'search' && (parsed.path || parsed.directory || parsed.dir)) return 'in ' + (parsed.path || parsed.directory || parsed.dir);
        return '';
    }

    function middleTruncate(text, max) {
        var s = String(text || '').replace(/\s+/g, ' ').trim();
        if (s.length <= max) return s;
        var head = Math.ceil((max - 1) * 0.6);
        var tail = max - 1 - head;
        return s.slice(0, head) + '…' + s.slice(s.length - tail);
    }

    function formatDuration(ms) {
        if (!(ms >= 0)) return '';
        if (ms < 1000) return (Math.max(ms, 100) / 1000).toFixed(1) + 's';
        var seconds = ms / 1000;
        if (seconds < 60) return seconds.toFixed(1) + 's';
        var m = Math.floor(seconds / 60);
        return m + 'm ' + Math.round(seconds % 60) + 's';
    }

    function resultText(ev) {
        if (!ev) return '';
        if (typeof ev.rawText === 'string') return ev.rawText;
        if (typeof ev.text === 'string') return ev.text;
        return '';
    }

    function isErrorResult(text) {
        var head = String(text || '').slice(0, 400);
        return /^\s*(error|failed|traceback)\b/i.test(head) || /"ok"\s*:\s*false/.test(head) || /"error_code"\s*:/.test(head);
    }

    // ------------------------------------------------------------ view model
    /* Turn a trace's events into renderable items:
     *   {type:'card', ...}  a tool call (+ its result once it arrives)
     *   {type:'group', kind, cards:[…]}  3+ consecutive finished same-kind cards
     *   {type:'thinking', ev}  {type:'note', ev}
     */
    function buildToolCards(events, options) {
        var opts = options || {};
        var running = !!opts.running;
        var items = [];
        var pending = [];   // cards waiting for their result, FIFO
        var list = Array.isArray(events) ? events : [];
        for (var i = 0; i < list.length; i += 1) {
            var ev = list[i];
            if (!ev) continue;
            if (ev.kind === 'tool_call') {
                var kind = kindOf(ev.name);
                var card = {
                    type: 'card',
                    id: String(ev.id || ('tc-' + i)),
                    source: ev,
                    name: ev.name || 'tool',
                    kind: kind,
                    args: ev.args,
                    argsHtml: ev.jsonHtml || '',
                    target: primaryArg(ev.args, kind),
                    meta: secondaryMeta(kind, ev.args),
                    startedAt: Number(ev.at) || 0,
                    state: 'running',
                    result: null,
                };
                items.push(card);
                pending.push(card);
            } else if (ev.kind === 'tool_result') {
                var match = null;
                for (var p = 0; p < pending.length; p += 1) {
                    if (!ev.name || pending[p].name === ev.name) { match = pending[p]; pending.splice(p, 1); break; }
                }
                if (!match) {
                    // Orphan result (history page boundary): show it as its own card.
                    match = {
                        type: 'card', id: String(ev.id || ('tr-' + i)), source: ev, name: ev.name || 'tool',
                        kind: kindOf(ev.name), args: null, argsHtml: '', target: '', meta: '',
                        startedAt: 0, state: 'running', result: null,
                    };
                    items.push(match);
                }
                var text = resultText(ev);
                match.result = ev;
                match.resultText = text;
                match.resultHtml = ev.jsonHtml || '';
                match.cacheKey = ev.cacheKey || '';
                match.doneAt = Number(ev.at) || 0;
                match.state = ev.planBlocked ? 'blocked' : (isErrorResult(text) ? 'error' : 'done');
            } else if (ev.kind === 'thinking') {
                items.push({ type: 'thinking', id: String(ev.id || ('th-' + i)), source: ev, ev: ev });
            } else if (ev.kind === 'info') {
                items.push({ type: 'note', id: String(ev.id || ('in-' + i)), source: ev, ev: ev });
            }
        }
        // A trace that is no longer running has no live calls left.
        if (!running) {
            items.forEach(function (item) { if (item.type === 'card' && item.state === 'running') item.state = 'settled'; });
        }
        // Thinking rows: the live one (last item of a running trace) is open.
        for (var t = 0; t < items.length; t += 1) {
            if (items[t].type === 'thinking') items[t].live = running && t === items.length - 1;
        }
        return groupCards(items);
    }

    function groupCards(items) {
        var out = [];
        var run = [];
        function flush() {
            if (run.length >= 3) {
                out.push({ type: 'group', id: 'g:' + run[0].id, source: run[0].source, kind: run[0].kind, cards: run.slice() });
            } else {
                Array.prototype.push.apply(out, run);
            }
            run = [];
        }
        items.forEach(function (item) {
            var groupable = item.type === 'card' && item.state !== 'running';
            if (groupable && run.length && run[0].kind === item.kind) { run.push(item); return; }
            flush();
            if (groupable) run.push(item); else out.push(item);
        });
        flush();
        return out;
    }

    // ------------------------------------------------------------ render
    function register(id, source) {
        if (source && typeof source === 'object') registry.set(id, source);
    }

    function glyphFor(state) {
        switch (state) {
            case 'running': return '<span class="w-inkdrop" data-size="sm" data-tone="running"></span><span class="w-sr-only">Running</span>';
            case 'error': return '<span aria-hidden="true">✗</span><span class="w-sr-only">Failed</span>';
            case 'blocked': return icon('lock') + '<span class="w-sr-only">Blocked by plan mode</span>';
            default: return '<span aria-hidden="true">✓</span><span class="w-sr-only">Done</span>';
        }
    }

    function argsGrid(card) {
        var parsed = parseArgs(card.args);
        var rows = '<div class="w-toolcard__arg"><dt>tool</dt><dd>' + esc(card.name) + '</dd></div>';
        if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
            Object.keys(parsed).slice(0, 24).forEach(function (key) {
                var value = parsed[key];
                var text = typeof value === 'string' ? value : JSON.stringify(value);
                if (text && text.length > 600) text = text.slice(0, 600) + '…';
                rows += '<div class="w-toolcard__arg"><dt>' + esc(key) + '</dt><dd>' + esc(text) + '</dd></div>';
            });
            return '<dl class="w-toolcard__args">' + rows + '</dl>';
        }
        if (parsed != null && parsed !== '') {
            rows += '<div class="w-toolcard__arg"><dt>args</dt><dd>' + esc(typeof parsed === 'string' ? parsed : JSON.stringify(parsed)) + '</dd></div>';
        } else if (card.argsHtml) {
            return '<dl class="w-toolcard__args">' + rows + '</dl><pre class="w-toolcard__json">' + card.argsHtml + '</pre>';
        }
        return '<dl class="w-toolcard__args">' + rows + '</dl>';
    }

    function resultBlock(card, source) {
        if (!card.result) {
            return card.state === 'running' ? '<p class="w-toolcard__waiting">Waiting for the result…</p>' : '';
        }
        var text = String(card.resultText || '');
        var lines = text.split('\n');
        var showAll = !!(source && source.showAll);
        var shown = showAll ? lines : lines.slice(0, PREVIEW_LINES);
        var body = text
            ? '<pre class="w-toolcard__result">' + esc(shown.join('\n')) + '</pre>'
            : (card.resultHtml ? '<pre class="w-toolcard__result">' + card.resultHtml + '</pre>' : '<p class="w-toolcard__waiting">No output.</p>');
        var actions = '';
        if (text && lines.length > PREVIEW_LINES) {
            actions += '<button type="button" class="w-btn" data-variant="ghost" data-size="sm" data-tc-more="' + esc(card.id) + '">'
                + (showAll ? 'Show less' : 'Show all ' + lines.length + ' lines') + '</button>';
        }
        if (card.cacheKey) {
            actions += '<button type="button" class="w-btn" data-variant="ghost" data-size="sm" data-tc-cache="' + esc(card.cacheKey)
                + '" data-tc-name="' + esc(card.name) + '">' + icon('external-link') + '<span>Open cached result</span></button>';
        }
        return body + (actions ? '<div class="w-toolcard__actions">' + actions + '</div>' : '');
    }

    /* card fields: id, name, kind, target, meta, state (running|done|settled|error|blocked),
     * startedAt, doneAt, duration (ms, optional), args, argsHtml, result*, cacheKey, source. */
    function renderToolCard(card, options) {
        if (!card) return '';
        var opts = options || {};
        var now = Number(opts.now) || Date.now();
        var source = card.source || card;
        var id = String(card.id || '');
        register(id, source);
        var kind = KINDS[card.kind] ? card.kind : kindOf(card.name);
        var spec = KINDS[kind];
        var open = !!(source && source.open);
        var state = card.state || 'done';
        var duration = card.duration != null ? Number(card.duration)
            : (card.doneAt && card.startedAt ? card.doneAt - card.startedAt
                : (state === 'running' && card.startedAt ? now - card.startedAt : NaN));
        var age = card.doneAt ? now - card.doneAt : Infinity;
        var fresh = state === 'done' && age >= 0 && age < SETTLE_MS;
        var label = state === 'blocked' ? 'blocked' : spec.label;
        var target = card.target ? middleTruncate(card.target, 96) : '';
        var attrs = ' data-state="' + esc(state) + '" data-kind="' + esc(kind) + '"'
            + (fresh ? ' data-fresh style="--w-age: ' + Math.round(age) + 'ms"' : '')
            + (open ? ' data-open' : '');
        var title = card.name + (card.target ? ' · ' + card.target : '');
        var html = '<div class="w-toolcard"' + attrs + '>'
            + '<button type="button" class="w-toolcard__row" data-tc-toggle="' + esc(id) + '" aria-expanded="' + (open ? 'true' : 'false') + '" title="' + esc(title) + '">'
            + '<span class="w-toolcard__glyph">' + glyphFor(state) + '</span>'
            + '<span class="w-toolcard__icon">' + icon(spec.icon) + '</span>'
            + '<span class="w-toolcard__kind">' + esc(label) + '</span>'
            + (target ? '<span class="w-toolcard__target">' + esc(target) + '</span>' : '<span class="w-toolcard__target">' + esc(card.name) + '</span>')
            + (card.meta ? '<span class="w-toolcard__meta">' + esc(card.meta) + '</span>' : '')
            + '<span class="w-toolcard__time">' + esc(isFinite(duration) ? formatDuration(duration) : '') + '</span>'
            + '<span class="w-toolcard__chevron" aria-hidden="true">›</span>'
            + '</button>';
        if (open) {
            html += '<div class="w-toolcard__detail">' + argsGrid(card) + resultBlock(card, source) + '</div>';
        }
        return html + '</div>';
    }

    function groupLabel(group) {
        var spec = KINDS[group.kind] || KINDS.tool;
        var n = group.cards.length;
        return spec.verb + ' ' + n + ' ' + spec.noun[n === 1 ? 0 : 1];
    }

    function renderGroup(group, options) {
        var source = group.source;
        var id = group.id;
        registry.set(id, { __group: true, source: source });
        var open = !!(source && source.groupOpen);
        var spec = KINDS[group.kind] || KINDS.tool;
        var failed = group.cards.some(function (c) { return c.state === 'error'; });
        var targets = group.cards.map(function (c) { return c.target ? String(c.target).split('/').pop() : c.name; });
        var total = group.cards.reduce(function (sum, c) {
            return sum + (c.doneAt && c.startedAt ? c.doneAt - c.startedAt : 0);
        }, 0);
        var html = '<div class="w-toolgroup" data-kind="' + esc(group.kind) + '"' + (open ? ' data-open' : '') + (failed ? ' data-state="error"' : '') + '>'
            + '<button type="button" class="w-toolcard__row w-toolgroup__row" data-tc-toggle="' + esc(id) + '" aria-expanded="' + (open ? 'true' : 'false') + '">'
            + '<span class="w-toolcard__glyph">' + glyphFor(failed ? 'error' : 'done') + '</span>'
            + '<span class="w-toolcard__icon">' + icon(spec.icon) + '</span>'
            + '<span class="w-toolcard__kind">' + esc(groupLabel(group)) + '</span>'
            + '<span class="w-toolcard__target">' + esc(middleTruncate(targets.join(', '), 120)) + '</span>'
            + '<span class="w-toolcard__time">' + esc(total ? formatDuration(total) : '') + '</span>'
            + '<span class="w-toolcard__chevron" aria-hidden="true">›</span>'
            + '</button>';
        if (open) {
            html += '<div class="w-toolgroup__cards">' + group.cards.map(function (c) { return renderToolCard(c, options); }).join('') + '</div>';
        }
        return html + '</div>';
    }

    function renderThinking(item) {
        var ev = item.ev || {};
        register(item.id, ev);
        var open = ev.open === undefined ? !!item.live : !!ev.open;
        var text = String(ev.text || '');
        var words = text.trim() ? text.trim().split(/\s+/).length : 0;
        return '<div class="w-thinking"' + (open ? ' data-open' : '') + (item.live ? ' data-live' : '') + '>'
            + '<button type="button" class="w-thinking__head" data-tc-toggle="' + esc(item.id) + '" aria-expanded="' + (open ? 'true' : 'false') + '">'
            + (item.live ? '<span class="w-inkdrop" data-size="sm" data-tone="neutral"></span>' : '<span class="w-thinking__caret" aria-hidden="true">›</span>')
            + '<span class="w-thinking__label">' + (item.live ? 'Thinking' : 'Thought') + '</span>'
            + (words && !item.live ? '<span class="w-thinking__meta">' + words + ' words</span>' : '')
            + '</button>'
            + (open ? '<div class="w-thinking__body">' + esc(text) + '</div>' : '')
            + '</div>';
    }

    function renderNote(item) {
        return '<p class="w-tracenote">' + icon('info') + '<span>' + esc(item.ev && item.ev.text) + '</span></p>';
    }

    function renderToolCards(events, options) {
        var items = buildToolCards(events, options);
        if (!items.length) {
            return options && options.running
                ? '<div class="w-toollist"><p class="w-tracenote" data-state="working"><span class="w-inkdrop" data-size="sm" data-tone="running"></span><span>Working…</span></p></div>'
                : '';
        }
        return '<div class="w-toollist">' + items.map(function (item) {
            if (item.type === 'group') return renderGroup(item, options);
            if (item.type === 'thinking') return renderThinking(item);
            if (item.type === 'note') return renderNote(item);
            return renderToolCard(item, options);
        }).join('') + '</div>';
    }

    /* Sub-agent action → card. The action object is the reactive source, so
     * its `open` flag persists across the panel's re-renders. */
    function subagentActionCard(action, agent) {
        var a = action || {};
        var status = String(a.status || 'running');
        var state = status === 'error' ? 'error' : (status === 'done' ? 'done' : 'running');
        var elapsedMs = Number(a.elapsed) > 0 ? Number(a.elapsed) * 1000 : NaN;
        var at = Number(a.at) > 0 ? Number(a.at) * 1000 : 0;
        return {
            id: 'sa:' + String((agent && agent.task_id) || '') + ':' + String(a.seq),
            source: a,
            name: String(a.tool || 'tool'),
            kind: kindOf(a.tool),
            target: String(a.detail || ''),
            meta: '',
            state: state === 'done' && !(at && Date.now() - (at + (elapsedMs || 0)) < SETTLE_MS) ? 'settled' : state,
            startedAt: at,
            doneAt: state === 'done' && at && isFinite(elapsedMs) ? at + elapsedMs : 0,
            duration: isFinite(elapsedMs) ? elapsedMs : undefined,
            args: a.detail ? { detail: a.detail } : null,
            result: null,
        };
    }

    // ------------------------------------------------------------ behaviour
    document.addEventListener('click', function (event) {
        var target = event.target;
        if (!target || !target.closest) return;
        var more = target.closest('[data-tc-more]');
        if (more) {
            var src = registry.get(more.getAttribute('data-tc-more'));
            if (src) src.showAll = !src.showAll;
            return;
        }
        var cache = target.closest('[data-tc-cache]');
        if (cache) {
            try {
                window.Alpine.store('chat').openCachePopup(cache.getAttribute('data-tc-cache'), cache.getAttribute('data-tc-name') || '');
            } catch (e) { /* no chat store on this page */ }
            return;
        }
        var toggle = target.closest('[data-tc-toggle]');
        if (!toggle) return;
        var entry = registry.get(toggle.getAttribute('data-tc-toggle'));
        if (!entry) return;
        if (entry.__group) {
            if (entry.source) entry.source.groupOpen = !entry.source.groupOpen;
        } else {
            var isOpen = toggle.getAttribute('aria-expanded') === 'true';
            entry.open = !isOpen;
        }
    });

    var api = {
        kindOf: kindOf,
        primaryArg: primaryArg,
        buildToolCards: buildToolCards,
        renderToolCard: renderToolCard,
        renderToolCards: renderToolCards,
        subagentActionCard: subagentActionCard,
        formatDuration: formatDuration,
        SETTLE_MS: SETTLE_MS,
    };
    window.WashToolCard = api;
    window.renderToolCard = renderToolCard;
    window.renderToolCards = renderToolCards;
})();
