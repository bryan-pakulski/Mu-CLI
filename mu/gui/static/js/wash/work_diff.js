/* Wash diff for the /work Diff tab (G§9.11).
 *
 * Interim, page-scoped module: the shared `static/js/wash/diff.js`
 * (renderDiff, spec §4.3) is being built for chat and approvals by another
 * track. Once it lands, /work should move onto it and this file goes away;
 * the markup below already follows G§9.11 so the swap is a call-site change.
 *
 *   MuWorkDiff.parse(patch)            → [{path, status A|M|D|R, lines, added, removed, binary}]
 *   MuWorkDiff.renderFile(file, opts)  → '<table class="work-diff-table">…'
 *        opts.split        side-by-side (wide screens), else unified
 *        opts.highlight    (text, path) → html (syntax colours; default: escape)
 *        opts.after(newNo) → html row(s) inserted after a new-side line (review findings)
 *        opts.cols         column count for injected rows
 *   MuWorkDiff.STATUS_LABEL            A/M/D/R → added/modified/deleted/renamed
 *
 * Lines: additions sit on the ok wash with a `+` gutter in ok ink, deletions
 * on the danger wash with a `−` gutter, context on canvas, hunk headers on
 * sunken in ink-3; the changed span of a paired −/+ line is one wash step
 * stronger (<mark class="work-diff-intra">). Styles: wash/patterns/work.css.
 */
(() => {
    'use strict';
    const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
    const STATUS_LABEL = {A:'added', M:'modified', D:'deleted', R:'renamed'};

    function parse(patch) {
        const files = [];
        for (const block of String(patch || '').split(/(?=^diff --git )/m).filter(b => b.trim())) {
            const path = block.match(/^\+\+\+ b\/(.*)$/m)?.[1] || block.match(/^--- a\/(.*)$/m)?.[1] || block.match(/^diff --git a\/(\S+)/)?.[1] || 'diff';
            const status = /^new file mode/m.test(block) ? 'A' : /^deleted file mode/m.test(block) ? 'D' : /^rename from/m.test(block) ? 'R' : 'M';
            const lines = []; let oldNo = 0, newNo = 0, added = 0, removed = 0, inHunk = false;
            for (const line of block.split('\n')) {
                const hunk = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/);
                if (hunk) { oldNo = Number(hunk[1]); newNo = Number(hunk[2]); inHunk = true; lines.push({kind:'hunk', text:line}); continue; }
                if (!inHunk) continue;
                if (line.startsWith('+')) { lines.push({kind:'add', text:line.slice(1), newNo:newNo++}); added++; }
                else if (line.startsWith('-')) { lines.push({kind:'del', text:line.slice(1), oldNo:oldNo++}); removed++; }
                else if (line.startsWith('\\')) lines.push({kind:'note', text:line});
                else if (line.startsWith(' ') || line === '') { if (line === '' && lines.length && lines[lines.length - 1].kind === 'note') continue; lines.push({kind:'ctx', text:line.slice(1), oldNo:oldNo++, newNo:newNo++}); }
            }
            while (lines.length && lines[lines.length - 1].kind === 'ctx' && lines[lines.length - 1].text === '') lines.pop();
            files.push({path, status, lines, added, removed, binary: /^Binary files/m.test(block)});
        }
        return files;
    }

    /* Changed span of a −/+ pair: common prefix and suffix stay plain. */
    function intra(before, after) {
        let start = 0;
        const max = Math.min(before.length, after.length);
        while (start < max && before[start] === after[start]) start++;
        let end = 0;
        while (end < max - start && before[before.length - 1 - end] === after[after.length - 1 - end]) end++;
        const span = text => [text.slice(0, start), text.slice(start, text.length - end), text.slice(text.length - end)];
        // A pair with almost nothing in common reads better without marks.
        if (start + end < 3) return null;
        return {before:span(before), after:span(after)};
    }

    function renderFile(file, {split = false, highlight = null, after = null, cols = null} = {}) {
        const big = file.lines.length > 4000;
        const hl = text => (big || !highlight ? esc(text) : highlight(text, file.path));
        const code = (line, parts) => parts && parts[1]
            ? `${hl(parts[0])}<mark class="work-diff-intra">${hl(parts[1])}</mark>${hl(parts[2])}`
            : hl(line.text);
        const width = cols || (split ? 6 : 4);
        const extra = no => (after ? after(no, width) : '');
        const sr = kind => (kind === 'add' ? '<span class="w-sr-only">added</span>' : kind === 'del' ? '<span class="w-sr-only">removed</span>' : '');
        // Pair consecutive −/+ runs so each pair can mark its changed span.
        const pairs = new Map();
        for (let i = 0; i < file.lines.length;) {
            if (file.lines[i].kind !== 'del') { i++; continue; }
            const dels = [], adds = [];
            while (file.lines[i]?.kind === 'del') dels.push(i++);
            while (file.lines[i]?.kind === 'add') adds.push(i++);
            for (let k = 0; k < Math.min(dels.length, adds.length); k++) {
                const span = intra(file.lines[dels[k]].text, file.lines[adds[k]].text);
                if (span) { pairs.set(dels[k], span.before); pairs.set(adds[k], span.after); }
            }
        }
        let rows = '';
        if (!split) {
            file.lines.forEach((l, i) => {
                if (l.kind === 'hunk') { rows += `<tr class="hunk" data-kind="hunk"><td colspan="4">${esc(l.text)}</td></tr>`; return; }
                if (l.kind === 'note') { rows += `<tr class="note" data-kind="note"><td colspan="4">${esc(l.text)}</td></tr>`; return; }
                const cls = l.kind === 'add' ? 'add' : l.kind === 'del' ? 'del' : '';
                rows += `<tr class="${cls}" data-kind="${l.kind}" data-line-key="${esc(file.path + ':' + (l.newNo ?? ''))}"><td class="no">${l.oldNo ?? ''}</td><td class="no">${l.newNo ?? ''}</td><td class="sign ${cls}">${l.kind === 'add' ? '+' : l.kind === 'del' ? '−' : ' '}${sr(l.kind)}</td><td class="code ${cls}">${code(l, pairs.get(i))}</td></tr>`;
                if (l.kind !== 'del') rows += extra(l.newNo);
            });
        } else {
            let i = 0;
            while (i < file.lines.length) {
                const l = file.lines[i];
                if (l.kind === 'hunk' || l.kind === 'note') { rows += `<tr class="${l.kind}" data-kind="${l.kind}"><td colspan="6">${esc(l.text)}</td></tr>`; i++; continue; }
                if (l.kind === 'ctx') { rows += `<tr data-kind="ctx" data-line-key="${esc(file.path + ':' + l.newNo)}"><td class="no">${l.oldNo}</td><td class="sign"></td><td class="code half">${code(l)}</td><td class="no">${l.newNo}</td><td class="sign"></td><td class="code half">${code(l)}</td></tr>${extra(l.newNo)}`; i++; continue; }
                const dels = [], adds = [];
                while (file.lines[i]?.kind === 'del') dels.push(i++);
                while (file.lines[i]?.kind === 'add') adds.push(i++);
                for (let k = 0; k < Math.max(dels.length, adds.length); k++) {
                    const d = dels[k] != null ? file.lines[dels[k]] : null, a = adds[k] != null ? file.lines[adds[k]] : null;
                    rows += `<tr data-kind="change" data-line-key="${esc(file.path + ':' + (a?.newNo ?? ''))}">${d ? `<td class="no del">${d.oldNo}</td><td class="sign del">−${sr('del')}</td><td class="code half del">${code(d, pairs.get(dels[k]))}</td>` : '<td class="no blank"></td><td class="blank"></td><td class="blank half"></td>'}${a ? `<td class="no add">${a.newNo}</td><td class="sign add">+${sr('add')}</td><td class="code half add">${code(a, pairs.get(adds[k]))}</td>` : '<td class="no blank"></td><td class="blank"></td><td class="blank half"></td>'}</tr>${a ? extra(a.newNo) : ''}`;
                }
            }
        }
        return `<div class="work-diff-body"><table class="work-diff-table ${split ? 'is-split' : ''}" data-mode="${split ? 'split' : 'unified'}"><tbody>${rows}</tbody></table></div>`;
    }

    window.MuWorkDiff = {parse, renderFile, intra, STATUS_LABEL};
})();
