/* Wash diff view (G§9.11) — one diff component for chat `diff` events,
 * approval previews and the /work Diff tab.
 *
 *   renderDiff(unifiedText, options)      → HTMLElement (div.w-diff)
 *   renderDiffHTML(unifiedText, options)  → the same markup as a string (x-html)
 *   diffContents(original, modified, {filename, context}) → unified text
 *   parseUnified(unifiedText)             → [{path, oldPath, added, removed, hunks}]
 *
 * options:
 *   mode        "unified" (default) | "split"; split is offered as a toggle on
 *               wide screens (≥1200px, CSS) and is otherwise shown unified
 *   filename    fallback path when the text has no ---/+++ headers
 *   maxLines    truncate the body after N lines with an "N more lines" row
 *   collapsed   start with file bodies collapsed
 *   openHref    adds an "Open file" link to each file header
 *   highlight   false to skip per-line syntax highlighting (hljs)
 *
 * Additions use the ok wash with a + gutter in ok ink, deletions the danger
 * wash with a − gutter in danger ink, hunk headers are sunken ink-3, and
 * intra-line changes are one step stronger. Exported as window.WashDiff and
 * window.renderDiff for /work to adopt.
 */
(function () {
    'use strict';

    function esc(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    // ------------------------------------------------------------ parse
    function parseUnified(text, fallbackName) {
        var lines = String(text || '').replace(/\r\n?/g, '\n').split('\n');
        if (lines.length && lines[lines.length - 1] === '') lines.pop();
        var files = [];
        var file = null;
        var hunk = null;
        var oldNo = 0;
        var newNo = 0;
        function startFile(path, oldPath) {
            file = { path: path || fallbackName || '', oldPath: oldPath || '', added: 0, removed: 0, hunks: [] };
            files.push(file);
            hunk = null;
        }
        function cleanPath(p) {
            return String(p || '').replace(/^[ab]\//, '').replace(/\t.*$/, '').trim();
        }
        for (var i = 0; i < lines.length; i += 1) {
            var line = lines[i];
            if (/^diff --git /.test(line)) {
                var m = line.match(/^diff --git a\/(.*?) b\/(.*)$/);
                startFile(m ? m[2] : '', m ? m[1] : '');
                continue;
            }
            if (/^--- /.test(line) && i + 1 < lines.length && /^\+\+\+ /.test(lines[i + 1])) {
                var oldPath = cleanPath(line.slice(4));
                var newPath = cleanPath(lines[i + 1].slice(4));
                var path = newPath === '/dev/null' ? oldPath : newPath;
                if (!file || file.hunks.length) startFile(path, oldPath);
                else { file.path = path || file.path; file.oldPath = oldPath; }
                i += 1;
                continue;
            }
            var h = line.match(/^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$/);
            if (h) {
                if (!file) startFile(fallbackName || '', '');
                oldNo = Number(h[1]);
                newNo = Number(h[3]);
                hunk = { header: line, oldStart: oldNo, newStart: newNo, lines: [] };
                file.hunks.push(hunk);
                continue;
            }
            if (/^(index |new file mode|deleted file mode|similarity index|rename (from|to) |old mode|new mode|Binary files)/.test(line) && !hunk) continue;
            if (!file) startFile(fallbackName || '', '');
            if (!hunk) {
                hunk = { header: '', oldStart: 1, newStart: 1, lines: [] };
                oldNo = 1; newNo = 1;
                file.hunks.push(hunk);
            }
            var sign = line.charAt(0);
            if (sign === '+') {
                hunk.lines.push({ type: 'add', text: line.slice(1), oldNo: null, newNo: newNo++ });
                file.added += 1;
            } else if (sign === '-') {
                hunk.lines.push({ type: 'del', text: line.slice(1), oldNo: oldNo++, newNo: null });
                file.removed += 1;
            } else if (sign === '\\') {
                hunk.lines.push({ type: 'meta', text: line, oldNo: null, newNo: null });
            } else {
                hunk.lines.push({ type: 'ctx', text: sign === ' ' ? line.slice(1) : line, oldNo: oldNo++, newNo: newNo++ });
            }
        }
        return files;
    }

    // ------------------------------------------------------------ line diff
    /* Myers-free LCS on lines; bounded so huge files degrade to a full
     * replace instead of an O(n·m) table that freezes the tab. */
    function diffLines(a, b) {
        var n = a.length, m = b.length;
        var start = 0;
        while (start < n && start < m && a[start] === b[start]) start += 1;
        var endA = n, endB = m;
        while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) { endA -= 1; endB -= 1; }
        var midA = a.slice(start, endA), midB = b.slice(start, endB);
        var ops = [];
        for (var s = 0; s < start; s += 1) ops.push(['=', a[s]]);
        if (midA.length * midB.length > 4000000) {
            midA.forEach(function (l) { ops.push(['-', l]); });
            midB.forEach(function (l) { ops.push(['+', l]); });
        } else {
            var rows = midA.length + 1, cols = midB.length + 1;
            var table = new Uint32Array(rows * cols);
            for (var i = midA.length - 1; i >= 0; i -= 1) {
                for (var j = midB.length - 1; j >= 0; j -= 1) {
                    table[i * cols + j] = midA[i] === midB[j]
                        ? table[(i + 1) * cols + j + 1] + 1
                        : Math.max(table[(i + 1) * cols + j], table[i * cols + j + 1]);
                }
            }
            var x = 0, y = 0;
            while (x < midA.length && y < midB.length) {
                if (midA[x] === midB[y]) { ops.push(['=', midA[x]]); x += 1; y += 1; }
                else if (table[(x + 1) * cols + y] >= table[x * cols + y + 1]) { ops.push(['-', midA[x]]); x += 1; }
                else { ops.push(['+', midB[y]]); y += 1; }
            }
            while (x < midA.length) { ops.push(['-', midA[x]]); x += 1; }
            while (y < midB.length) { ops.push(['+', midB[y]]); y += 1; }
        }
        for (var e = endA; e < n; e += 1) ops.push(['=', a[e]]);
        return ops;
    }

    function diffContents(original, modified, options) {
        var opts = options || {};
        var context = opts.context != null ? opts.context : 3;
        var name = opts.filename || 'file';
        var a = String(original || '').replace(/\r\n?/g, '\n').split('\n');
        var b = String(modified || '').replace(/\r\n?/g, '\n').split('\n');
        if (a.length && a[a.length - 1] === '') a.pop();
        if (b.length && b[b.length - 1] === '') b.pop();
        var ops = diffLines(a, b);
        var out = ['--- a/' + name, '+++ b/' + name];
        var oldNo = 1, newNo = 1;
        var i = 0;
        while (i < ops.length) {
            while (i < ops.length && ops[i][0] === '=') { i += 1; oldNo += 1; newNo += 1; }
            if (i >= ops.length) break;
            var hs = Math.max(0, i - context);
            var hOld = oldNo - (i - hs), hNew = newNo - (i - hs);
            var body = [];
            for (var c = hs; c < i; c += 1) body.push(' ' + ops[c][1]);
            var oldCount = i - hs, newCount = i - hs;
            var lastChange = i;
            var k = i;
            while (k < ops.length) {
                var op = ops[k];
                if (op[0] === '=') {
                    if (k - lastChange > context * 2) break;
                    body.push(' ' + op[1]); oldCount += 1; newCount += 1;
                } else {
                    lastChange = k;
                    if (op[0] === '-') { body.push('-' + op[1]); oldCount += 1; }
                    else { body.push('+' + op[1]); newCount += 1; }
                }
                k += 1;
            }
            // Trim trailing context beyond `context` lines.
            var trailing = 0;
            for (var t = body.length - 1; t >= 0 && body[t].charAt(0) === ' '; t -= 1) trailing += 1;
            var drop = Math.max(0, trailing - context);
            if (drop) { body.splice(body.length - drop, drop); oldCount -= drop; newCount -= drop; k -= drop; }
            out.push('@@ -' + hOld + ',' + oldCount + ' +' + hNew + ',' + newCount + ' @@');
            Array.prototype.push.apply(out, body);
            for (var q = i; q < k; q += 1) {
                if (ops[q][0] !== '+') oldNo += 1;
                if (ops[q][0] !== '-') newNo += 1;
            }
            i = k;
        }
        return out.join('\n');
    }

    // ------------------------------------------------------------ render
    var EXT_LANG = {
        py: 'python', js: 'javascript', mjs: 'javascript', ts: 'typescript', tsx: 'typescript', jsx: 'javascript',
        json: 'json', css: 'css', html: 'xml', xml: 'xml', md: 'markdown', sh: 'bash', bash: 'bash', yml: 'yaml',
        yaml: 'yaml', toml: 'ini', rs: 'rust', go: 'go', java: 'java', kt: 'kotlin', rb: 'ruby', c: 'c', h: 'c',
        cpp: 'cpp', hpp: 'cpp', sql: 'sql', swift: 'swift', php: 'php',
    };
    function languageFor(path) {
        var m = String(path || '').toLowerCase().match(/\.([a-z0-9]+)$/);
        return m ? EXT_LANG[m[1]] || '' : '';
    }

    function highlightLine(text, lang, enabled) {
        if (!enabled || !lang || typeof window.hljs === 'undefined' || !text) return esc(text);
        try { return window.hljs.highlight(text, { language: lang, ignoreIllegals: true }).value; }
        catch (e) { return esc(text); }
    }

    // Common prefix/suffix of a replaced pair → mark the changed middle.
    function intraPair(oldText, newText) {
        var a = String(oldText), b = String(newText);
        var p = 0;
        while (p < a.length && p < b.length && a[p] === b[p]) p += 1;
        var s = 0;
        while (s < a.length - p && s < b.length - p && a[a.length - 1 - s] === b[b.length - 1 - s]) s += 1;
        if (p === 0 && s === 0) return null;
        function wrap(t) {
            var mid = t.slice(p, t.length - s);
            return esc(t.slice(0, p)) + (mid ? '<mark class="w-diff__intra">' + esc(mid) + '</mark>' : '') + esc(t.slice(t.length - s));
        }
        return [wrap(a), wrap(b)];
    }

    // Pair consecutive del/add runs for intra-line marks and split view.
    function annotate(hunk, lang, hl) {
        var rows = hunk.lines.map(function (l) { return { line: l, html: null }; });
        var i = 0;
        while (i < rows.length) {
            if (rows[i].line.type !== 'del') { i += 1; continue; }
            var dels = [];
            while (i < rows.length && rows[i].line.type === 'del') { dels.push(rows[i]); i += 1; }
            var adds = [];
            while (i < rows.length && rows[i].line.type === 'add') { adds.push(rows[i]); i += 1; }
            for (var k = 0; k < Math.min(dels.length, adds.length); k += 1) {
                var pair = intraPair(dels[k].line.text, adds[k].line.text);
                if (pair) { dels[k].html = pair[0]; adds[k].html = pair[1]; }
                dels[k].partner = adds[k];
                adds[k].partner = dels[k];
            }
        }
        rows.forEach(function (r) { if (r.html == null) r.html = highlightLine(r.line.text, lang, hl); });
        return rows;
    }

    var SIGN = { add: '+', del: '−', ctx: ' ', meta: ' ' };

    function unifiedRows(rows) {
        return rows.map(function (r) {
            var l = r.line;
            return '<tr class="w-diff__row" data-type="' + l.type + '">'
                + '<td class="w-diff__num">' + (l.oldNo != null ? l.oldNo : '') + '</td>'
                + '<td class="w-diff__num">' + (l.newNo != null ? l.newNo : '') + '</td>'
                + '<td class="w-diff__sign" aria-hidden="true">' + SIGN[l.type] + '</td>'
                + '<td class="w-diff__code">' + (r.html || '&#8203;') + '</td></tr>';
        }).join('');
    }

    function splitRows(rows) {
        var out = '';
        var i = 0;
        function cell(r, side) {
            if (!r) return '<td class="w-diff__num"></td><td class="w-diff__code" data-type="empty"></td>';
            var l = r.line;
            var no = side === 'old' ? l.oldNo : l.newNo;
            return '<td class="w-diff__num">' + (no != null ? no : '') + '</td><td class="w-diff__code" data-type="' + l.type + '">' + (r.html || '&#8203;') + '</td>';
        }
        while (i < rows.length) {
            var r = rows[i];
            if (r.line.type === 'del') {
                var dels = [];
                while (i < rows.length && rows[i].line.type === 'del') { dels.push(rows[i]); i += 1; }
                var adds = [];
                while (i < rows.length && rows[i].line.type === 'add') { adds.push(rows[i]); i += 1; }
                for (var k = 0; k < Math.max(dels.length, adds.length); k += 1) {
                    out += '<tr class="w-diff__row">' + cell(dels[k], 'old') + cell(adds[k], 'new') + '</tr>';
                }
            } else if (r.line.type === 'add') {
                out += '<tr class="w-diff__row">' + cell(null, 'old') + cell(r, 'new') + '</tr>';
                i += 1;
            } else {
                out += '<tr class="w-diff__row">' + cell(r, 'old') + cell(r, 'new') + '</tr>';
                i += 1;
            }
        }
        return out;
    }

    var uid = 0;

    function renderDiffHTML(text, options) {
        var opts = options || {};
        var mode = opts.mode === 'split' ? 'split' : 'unified';
        var files = parseUnified(text, opts.filename);
        if (!files.length) {
            return '<div class="w-diff" data-mode="' + mode + '"><p class="w-diff__empty">No changes.</p></div>';
        }
        var budget = opts.maxLines > 0 ? opts.maxLines : Infinity;
        var html = '<div class="w-diff" data-mode="' + mode + '">';
        files.forEach(function (file) {
            uid += 1;
            var bodyId = 'w-diff-body-' + uid;
            var lang = languageFor(file.path);
            var hl = opts.highlight !== false;
            var collapsed = !!opts.collapsed;
            html += '<section class="w-diff__file">'
                + '<header class="w-diff__head">'
                + '<button type="button" class="w-diff__toggle" aria-expanded="' + (collapsed ? 'false' : 'true') + '" aria-controls="' + bodyId + '">'
                + '<span class="w-diff__caret" aria-hidden="true">›</span>'
                + '<span class="w-diff__path">' + esc(file.path || 'changes') + '</span></button>'
                + '<span class="w-diff__stat"><span class="w-diff__added">+' + file.added + '</span> <span class="w-diff__removed">−' + file.removed + '</span>'
                + '<span class="w-sr-only"> (' + file.added + ' added, ' + file.removed + ' removed)</span></span>'
                + '<span class="w-diff__tools">'
                + '<span class="w-segmented w-diff__modes" data-size="sm" role="group" aria-label="Diff layout">'
                + '<button type="button" class="w-segmented__option" data-diff-mode="unified" aria-pressed="' + (mode === 'unified') + '">Unified</button>'
                + '<button type="button" class="w-segmented__option" data-diff-mode="split" aria-pressed="' + (mode === 'split') + '">Split</button>'
                + '</span>'
                + (opts.openHref ? '<a class="w-btn" data-variant="ghost" data-size="sm" href="' + esc(typeof opts.openHref === 'function' ? opts.openHref(file.path) : opts.openHref) + '">Open file</a>' : '')
                + '</span></header>'
                + '<div class="w-diff__body" id="' + bodyId + '"' + (collapsed ? ' hidden' : '') + '>';
            var unified = '';
            var split = '';
            var hidden = 0;
            file.hunks.forEach(function (hunk) {
                if (budget <= 0) { hidden += hunk.lines.length; return; }
                var lines = hunk.lines;
                if (lines.length > budget) {
                    hidden += lines.length - budget;
                    hunk = { header: hunk.header, lines: lines.slice(0, budget) };
                }
                budget -= hunk.lines.length;
                var rows = annotate(hunk, lang, hl);
                if (hunk.header) {
                    unified += '<tr class="w-diff__row" data-type="hunk"><td class="w-diff__hunk" colspan="4">' + esc(hunk.header) + '</td></tr>';
                    split += '<tr class="w-diff__row" data-type="hunk"><td class="w-diff__hunk" colspan="4">' + esc(hunk.header) + '</td></tr>';
                }
                unified += unifiedRows(rows);
                split += splitRows(rows);
            });
            html += '<table class="w-diff__table" data-view="unified"><tbody>' + unified + '</tbody></table>'
                + '<table class="w-diff__table" data-view="split"><tbody>' + split + '</tbody></table>';
            if (hidden) html += '<p class="w-diff__more">' + hidden + ' more line' + (hidden === 1 ? '' : 's') + ' not shown' + (opts.moreLabel ? ' · ' + esc(opts.moreLabel) : '') + '</p>';
            html += '</div></section>';
        });
        return html + '</div>';
    }

    function renderDiff(text, options) {
        var holder = document.createElement('div');
        holder.innerHTML = renderDiffHTML(text, options);
        return holder.firstElementChild;
    }

    function stats(text) {
        return parseUnified(text).reduce(function (acc, f) {
            acc.added += f.added; acc.removed += f.removed; acc.files += 1; return acc;
        }, { added: 0, removed: 0, files: 0 });
    }

    // ------------------------------------------------------------ behaviour
    document.addEventListener('click', function (event) {
        var t = event.target;
        if (!t || !t.closest) return;
        var modeBtn = t.closest('.w-diff [data-diff-mode]');
        if (modeBtn) {
            var root = modeBtn.closest('.w-diff');
            var mode = modeBtn.getAttribute('data-diff-mode');
            root.setAttribute('data-mode', mode);
            root.querySelectorAll('[data-diff-mode]').forEach(function (b) {
                b.setAttribute('aria-pressed', String(b.getAttribute('data-diff-mode') === mode));
            });
            return;
        }
        var toggle = t.closest('.w-diff__toggle');
        if (toggle) {
            var body = document.getElementById(toggle.getAttribute('aria-controls'));
            var open = toggle.getAttribute('aria-expanded') !== 'true';
            toggle.setAttribute('aria-expanded', String(open));
            if (body) body.hidden = !open;
        }
    });

    var api = {
        parseUnified: parseUnified,
        diffContents: diffContents,
        renderDiff: renderDiff,
        renderDiffHTML: renderDiffHTML,
        stats: stats,
        languageFor: languageFor,
    };
    window.WashDiff = api;
    window.renderDiff = renderDiff;
})();
