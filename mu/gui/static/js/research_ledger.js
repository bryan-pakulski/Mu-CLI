// Research panel model (fragments/research_panel.html, the `research` Alpine store).
//
// Pure helpers over GET /api/research/state, loadable without Alpine (node tests):
// - MuResearch.tree(questions)            → rows in tree order with depth; orphans become roots.
// - MuResearch.claimsFor(claims, qid)     → the question's claims and its sub-questions'.
// - MuResearch.matrix(claims, sources)    → claims × origins; each cell lists the stances that
//                                            origin takes (supports / contradicts / qualifies).
// - MuResearch.vstyle(table, key)         → {status, glyph, label} from the Wash vocabulary
//                                            (research_question / _confidence / _stance /
//                                            _source_state).
// - MuResearch.claimSheet(claim, byId)    → escaped HTML for the claim's WashSheet: its
//                                            evidence excerpts and the sources behind them.
(function (root) {
    'use strict';

    const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, ch => (
        { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));

    function tree(questions) {
        const list = Array.isArray(questions) ? questions : [];
        const ids = new Set(list.map(q => q.id));
        const children = new Map();
        const roots = [];
        for (const q of list) {
            const parent = q.parent_id && ids.has(q.parent_id) && q.parent_id !== q.id ? q.parent_id : null;
            if (parent) {
                if (!children.has(parent)) children.set(parent, []);
                children.get(parent).push(q);
            } else {
                roots.push(q);
            }
        }
        const out = [];
        const seen = new Set();
        const walk = (q, depth) => {
            if (seen.has(q.id)) return;
            seen.add(q.id);
            out.push({ ...q, depth });
            for (const child of children.get(q.id) || []) walk(child, depth + 1);
        };
        roots.forEach(q => walk(q, 0));
        return out;
    }

    function inQuestion(questionId, qid) {
        return !qid || questionId === qid || String(questionId || '').startsWith(qid + '.');
    }

    function claimsFor(claims, qid) {
        return (Array.isArray(claims) ? claims : []).filter(c => inQuestion(c.question_id, qid));
    }

    function matrix(claims, sources) {
        const originOf = new Map((sources || []).map(s => [s.id, s.origin]));
        const origins = [];
        const rows = (claims || []).map(c => {
            const cells = {};
            for (const ev of c.evidence || []) {
                const origin = originOf.get(ev.citation_id);
                if (!origin) continue;
                if (!origins.includes(origin)) origins.push(origin);
                const stances = cells[origin] || (cells[origin] = []);
                if (!stances.includes(ev.stance)) stances.push(ev.stance);
            }
            return { claim_id: c.id, question_id: c.question_id, text: c.text, confidence: c.confidence, cells };
        });
        return { origins, rows };
    }

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

    function claimSheet(claim, sourcesById) {
        const byId = sourcesById || {};
        const conf = vstyle('research_confidence', claim.confidence);
        const kind = claim.kind && claim.kind !== 'observed' ? ` · ${esc(claim.kind)}` : '';
        const origins = (claim.origins && claim.origins.supports) || [];
        const facts = `<dl class="w-facts">
            <dt>Confidence</dt><dd>${sign('research_confidence', claim.confidence)} ${esc(conf.label)}${kind}</dd>
            <dt>Question</dt><dd>${esc(claim.question_id)}</dd>
            <dt>Supported by</dt><dd>${origins.length ? esc(origins.join(', ')) : '—'}</dd>
        </dl>`;
        const evidence = (claim.evidence || []).map(ev => {
            const src = byId[ev.citation_id] || {};
            const stance = vstyle('research_stance', ev.stance);
            const where = ev.locator ? ` · ${esc(ev.locator)}` : '';
            const link = src.url
                ? `<a href="${esc(src.url)}" target="_blank" rel="noopener noreferrer">${esc(src.title || src.url)}</a>`
                : esc(src.title || '');
            return `<li class="w-row rs-evidence" data-stance="${esc(ev.stance)}">
                ${sign('research_stance', ev.stance)}
                <div class="w-row__main">
                    <blockquote class="rs-evidence__quote">${esc(ev.excerpt)}</blockquote>
                    <span class="w-row__sub">${esc(stance.label)} · [^${esc(ev.citation_id)}] ${link}${where}${src.origin ? ` · ${esc(src.origin)}` : ''}</span>
                </div>
            </li>`;
        }).join('');
        return `${facts}<h3 class="rs-sheet__title">Evidence</h3><ul class="w-rows rs-evidence-list">${evidence}</ul>`;
    }

    root.MuResearch = { tree, claimsFor, matrix, vstyle, claimSheet, esc };
})(typeof window !== 'undefined' ? window : globalThis);
