// The shared mode shell (documentation/plans/modes_v2.md §5.2), as pure helpers loadable
// without Alpine (node tests: tests/gui/frontend/mode-shell.test.cjs).
//
// Every mode panel has the same skeleton — one header bar (title, state sentence, text tabs, at
// most one primary action), "Now" when something waits for the user, rows with marks, sheets for
// one record — and fills it with its own records (window.MuModeModels, mode_models.js).
//
// - MuModes.esc(text)                      → HTML-escaped text.
// - MuModes.vstyle(table, key)             → {status, glyph, label} from the Wash vocabulary.
// - MuModes.sign(table, key)               → escaped mark markup.
// - MuModes.sentence(parts)                → the state sentence parts with zero / empty parts dropped
//                                            (Mu §4.2): parts are strings or {text, tone}.
// - MuModes.sentenceHtml(parts)            → escaped <span class="w-state__part"> markup.
// - MuModes.orderNow(items)                → Now rows in the order to act: safety decisions, user
//                                            decisions, blockers, failures, budgets, reviews (Mu §5.2).
// - MuModes.relTime(ts, now)               → "23h" / "4m" / "2d" (seconds since the epoch).
// - MuModes.checkRunRow(run)               → {status, glyph, title, sub, when} for the shared row.
// - MuModes.checkRunSheet(run, output)     → escaped HTML for the CheckRun sheet.
(function (root) {
    'use strict';

    const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, ch => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));

    function vstyle(table, key) {
        const vocab = (root.WASH && root.WASH.vocabulary) || {};
        const row = (vocab[table] || {})[key];
        const status = row ? row.status : 'pending';
        const spec = (vocab.status || {})[status] || { glyph: '○' };
        return { status, glyph: spec.glyph, label: row ? row.label : String(key || '').replace(/_/g, ' ') };
    }

    function sign(table, key) {
        const s = vstyle(table, key);
        return `<span class="w-sign" data-status="${esc(s.status)}" aria-hidden="true">${esc(s.glyph)}</span>`;
    }

    // "0 claims", "0 of 3", "$0.00", "" and null are left out; "Nothing needs you" style
    // sentences that ARE the news are passed as {text, keep: true}.
    const ZERO = /^\s*(\$?0+(\.0+)?%?)(\s|$)/;

    function sentence(parts) {
        return (parts || []).map(p => (typeof p === 'string' ? { text: p } : p))
            .filter(p => p && p.text != null && String(p.text).trim() !== '')
            .filter(p => p.keep || !ZERO.test(String(p.text)));
    }

    function sentenceHtml(parts) {
        return sentence(parts).map(p =>
            `<span class="w-state__part"${p.tone ? ` data-tone="${esc(p.tone)}"` : ''}>${esc(p.text)}</span>`).join('');
    }

    const NOW_RANK = { safety: 0, decision: 1, blocker: 2, failure: 3, budget: 4, review: 5 };

    function orderNow(items) {
        return (items || []).map((item, i) => ({ item, i }))
            .sort((a, b) => ((NOW_RANK[a.item.kind] ?? 9) - (NOW_RANK[b.item.kind] ?? 9)) || (a.i - b.i))
            .map(x => x.item);
    }

    function relTime(ts, now) {
        if (!ts) return '';
        const secs = Math.max(0, Math.round((now || Date.now() / 1000) - Number(ts)));
        if (secs < 60) return 'now';
        if (secs < 3600) return `${Math.floor(secs / 60)}m`;
        if (secs < 86400) return `${Math.floor(secs / 3600)}h`;
        return `${Math.floor(secs / 86400)}d`;
    }

    function runKey(run) {
        if (run.timed_out) return 'timed_out';
        return run.passed ? 'passed' : 'failed';
    }

    function checkRunRow(run) {
        const s = vstyle('check_run', runKey(run));
        const label = run.label || run.purpose || 'check';
        const dur = run.duration_s ? `${Number(run.duration_s).toFixed(1)}s` : '';
        return {
            id: run.id, status: s.status, glyph: s.glyph,
            title: label,
            sub: [run.command, dur].filter(Boolean).join(' · '),
            when: relTime(run.started_at),
            word: s.label,
        };
    }

    function checkRunSheet(run, output) {
        const s = vstyle('check_run', runKey(run));
        const exit = run.timed_out ? 'timed out' : (run.exit_code == null ? '—' : String(run.exit_code));
        const tests = run.tests && run.tests.total
            ? `<dt>Tests</dt><dd>${esc(run.tests.passed)} of ${esc(run.tests.total)} passed${run.tests.failed ? `, ${esc(run.tests.failed)} failed` : ''}</dd>`
            : '';
        const hashes = Object.entries(run.hashes || {})
            .map(([path, h]) => `<dt class="mono">${esc(path)}</dt><dd class="mono">${esc(String(h).slice(0, 16))}</dd>`).join('');
        const full = output == null ? '' :
            `<details class="w-disclose"><summary>Full output</summary><pre class="w-code__pre mono">${esc(output)}</pre></details>`;
        return `<dl class="w-facts">
            <dt>Result</dt><dd>${sign('check_run', runKey(run))} ${esc(s.label)}${run.detail ? ` · ${esc(run.detail)}` : ''}</dd>
            <dt>Command</dt><dd><code class="mono">${esc(run.command)}</code></dd>
            <dt>Expected</dt><dd>${esc(run.predicate_text || '')}</dd>
            <dt>Exit</dt><dd>${esc(exit)}</dd>
            <dt>Ran on</dt><dd>${esc(run.runner || 'host')}</dd>
            ${tests}
        </dl>
        <pre class="w-code__pre mono">${esc(run.excerpt || '')}</pre>
        ${full}
        <details class="w-disclose"><summary>Details</summary><dl class="w-facts">
            <dt>Run</dt><dd class="mono">${esc(run.id)}</dd>
            <dt>Directory</dt><dd class="mono">${esc(run.cwd || '')}</dd>
            <dt>Purpose</dt><dd>${esc(run.purpose || '')}</dd>
            ${hashes}
        </dl></details>`;
    }

    root.MuModes = { esc, vstyle, sign, sentence, sentenceHtml, orderNow, relTime, checkRunRow, checkRunSheet };
})(typeof window !== 'undefined' ? window : globalThis);
