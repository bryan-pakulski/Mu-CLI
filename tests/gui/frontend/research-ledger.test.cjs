// Run after npm ci in mobile/android:
// NODE_PATH=mobile/android/node_modules node --test tests/gui/frontend/*.test.cjs
// Research panel model (static/js/research_ledger.js): the question tree, claims per question,
// the claims × origins matrix, vocabulary words, and the claim sheet's escaped markup.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { JSDOM } = require('jsdom');
const root = resolve(__dirname, '../../..');
// Objects built inside the JSDOM window carry its prototypes; compare their plain shape.
const plain = value => JSON.parse(JSON.stringify(value));

function model() {
    const dom = new JSDOM('<!doctype html><body></body>', { runScripts: 'outside-only', url: 'http://127.0.0.1:30311/' });
    dom.window.eval(readFileSync(resolve(root, 'mu/gui/static/js/wash_tokens.js'), 'utf8'));
    dom.window.eval(readFileSync(resolve(root, 'mu/gui/static/js/research_ledger.js'), 'utf8'));
    return dom.window.MuResearch;
}

const QUESTIONS = [
    { id: 'q1', text: 'Main', parent_id: null },
    { id: 'q2', text: 'Second', parent_id: null },
    { id: 'q1.1', text: 'Sub of main', parent_id: 'q1' },
    { id: 'q1.1.1', text: 'Deeper', parent_id: 'q1.1' },
    { id: 'q9.1', text: 'Orphan', parent_id: 'q9' },
];

test('questions come back in tree order with their depth; orphans become roots', () => {
    const rows = model().tree(QUESTIONS);
    assert.deepEqual(plain(rows.map(r => [r.id, r.depth])),
        [['q1', 0], ['q1.1', 1], ['q1.1.1', 2], ['q2', 0], ['q9.1', 0]]);
});

test('a question lists its own claims and its sub-questions\', not a sibling prefix', () => {
    const claims = [{ id: 'c1', question_id: 'q1' }, { id: 'c2', question_id: 'q1.1' },
        { id: 'c3', question_id: 'q10' }, { id: 'c4', question_id: 'q2' }];
    const R = model();
    assert.deepEqual(plain(R.claimsFor(claims, 'q1').map(c => c.id)), ['c1', 'c2']);
    assert.deepEqual(plain(R.claimsFor(claims, null).map(c => c.id)), ['c1', 'c2', 'c3', 'c4']);
});

test('the matrix groups evidence by origin and keeps every stance an origin takes', () => {
    const sources = [{ id: 1, origin: 'a.org' }, { id: 2, origin: 'b.com' }, { id: 3, origin: 'a.org' }];
    const claims = [
        { id: 'c1', question_id: 'q1', text: 'One', confidence: 'medium', evidence: [
            { citation_id: 1, stance: 'supports' }, { citation_id: 3, stance: 'supports' },
            { citation_id: 2, stance: 'contradicts' }] },
        { id: 'c2', question_id: 'q1', text: 'Two', confidence: 'low', evidence: [
            { citation_id: 2, stance: 'supports' }, { citation_id: 2, stance: 'qualifies' },
            { citation_id: 99, stance: 'supports' }] },
    ];
    const m = plain(model().matrix(claims, sources));
    assert.deepEqual(m.origins, ['a.org', 'b.com']);
    assert.deepEqual(m.rows[0].cells, { 'a.org': ['supports'], 'b.com': ['contradicts'] });
    assert.deepEqual(m.rows[1].cells, { 'b.com': ['supports', 'qualifies'] });
});

test('words and signs come from the Wash vocabulary', () => {
    const R = model();
    assert.deepEqual(plain(R.vstyle('research_question', 'contested')), { status: 'unverified', glyph: '?', label: 'contested' });
    assert.equal(R.vstyle('research_stance', 'contradicts').status, 'failed');
    assert.equal(R.vstyle('research_confidence', 'high').label, 'high confidence');
});

test('the claim sheet shows each excerpt with its source and escapes page text', () => {
    const R = model();
    const html = R.claimSheet({
        id: 'c1', question_id: 'q1', text: 'Claim', kind: 'inferred', confidence: 'medium',
        origins: { supports: ['a.org'] },
        evidence: [{ citation_id: 1, stance: 'supports', excerpt: '<img src=x onerror=alert(1)>', locator: 'Page 3' }],
    }, { 1: { id: 1, title: 'Paper "A"', url: 'https://a.org/p', origin: 'a.org' } });
    const dom = new JSDOM(`<body>${html}</body>`);
    const doc = dom.window.document;
    assert.equal(doc.querySelector('img'), null);
    assert.equal(doc.querySelector('.rs-evidence__quote').textContent, '<img src=x onerror=alert(1)>');
    const link = doc.querySelector('.rs-evidence a');
    assert.equal(link.getAttribute('href'), 'https://a.org/p');
    assert.equal(link.textContent, 'Paper "A"');
    assert.match(doc.querySelector('.rs-evidence .w-row__sub').textContent, /supports · \[\^1\] Paper "A" · Page 3 · a\.org/);
    assert.match(doc.querySelector('.w-facts').textContent, /medium confidence · inferred/);
});
