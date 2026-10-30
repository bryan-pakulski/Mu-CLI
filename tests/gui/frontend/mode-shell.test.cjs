// Run after npm ci in mobile/android:
// NODE_PATH=mobile/android/node_modules node --test tests/gui/frontend/mode-shell.test.cjs
// The shared mode shell (static/js/mode_shell.js) and the per-mode panel models
// (static/js/mode_models.js): state-sentence parts with zeros dropped, Now ordering, marks and
// words from the vocabulary, escaped sheets.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const { JSDOM } = require('jsdom');
const root = resolve(__dirname, '../../..');
const plain = value => JSON.parse(JSON.stringify(value));

function load() {
    const dom = new JSDOM('<!doctype html><body></body>', { runScripts: 'outside-only', url: 'http://127.0.0.1:30311/' });
    for (const file of ['wash_tokens.js', 'mode_shell.js', 'mode_models.js']) {
        dom.window.eval(readFileSync(resolve(root, 'mu/gui/static/js', file), 'utf8'));
    }
    return { S: dom.window.MuModes, M: dom.window.MuModeModels };
}

test('the state sentence drops zero and empty parts unless the zero is the news', () => {
    const { S } = load();
    const parts = S.sentence(['4 questions', '0 contested', '', null, '$0.00 of $10', { text: 'Nothing needs you', keep: true },
        { text: '0 left', keep: true }]);
    assert.deepEqual(plain(parts.map(p => p.text)), ['4 questions', 'Nothing needs you', '0 left']);
    assert.match(S.sentenceHtml([{ text: '<b>x</b>', tone: 'attention' }]), /data-tone="attention">&lt;b&gt;x&lt;\/b&gt;/);
});

test('Now is ordered safety, decisions, blockers, failures, budgets, reviews', () => {
    const { S } = load();
    const order = S.orderNow([{ id: 'r', kind: 'review' }, { id: 'b', kind: 'blocker' }, { id: 'd', kind: 'decision' },
        { id: 's', kind: 'safety' }, { id: 'u', kind: 'budget' }, { id: 'f', kind: 'failure' }]);
    assert.deepEqual(plain(order.map(x => x.id)), ['s', 'd', 'b', 'f', 'u', 'r']);
});

test('check runs render a vocabulary mark and an escaped sheet', () => {
    const { S } = load();
    const run = { id: 'r3', passed: false, timed_out: false, exit_code: 1, command: 'pytest <x>', label: 'Repro fails as expected',
        duration_s: 2.1, purpose: 'repro', predicate_text: "printing 'KeyError'", excerpt: '<script>', runner: 'host',
        hashes: { 'poc.sh': 'a'.repeat(64) } };
    const row = S.checkRunRow(run);
    assert.equal(row.status, 'failed');
    assert.equal(row.sub, 'pytest <x> · 2.1s');
    const sheet = S.checkRunSheet(run, 'full <out>');
    assert.ok(!sheet.includes('<script>') && sheet.includes('&lt;script&gt;'));
    assert.ok(sheet.includes('full &lt;out&gt;') && sheet.includes('poc.sh'));
    assert.equal(S.checkRunRow({ ...run, timed_out: true }).word, 'timed out');
});

test('debug: sentence, stepper and a close decision once verified', () => {
    const { M } = load();
    const c = { title: 'KeyError', status: 'verified', hypotheses: [{}, {}], suspects: [], regression_test: null };
    assert.deepEqual(plain(M.debug.sentence({ case: c })), ['KeyError', 'verified', '2 hypotheses', '0 suspects']);
    assert.deepEqual(plain(M.debug.stepper(c).map(s => s.status)), ['done', 'done', 'done', 'done', 'waiting']);
    const now = M.debug.now({ case: c, blockers: [{ id: 'b1', summary: 'need logs', needs: 'the log file' }] });
    assert.deepEqual(plain(now.map(r => r.id)), ['close', 'blocker:b1']);
    assert.equal(M.debug.primary({ case: c }).label, 'Close case');
    assert.equal(M.debug.footWord({ case: { ...c, status: 'reproduced' } }), 'reproduced');
    assert.ok(M.debug.hypothesisSheet({ id: 'h1', status: 'confirmed', evidence: [{ kind: 'location', ref: 'a<b>.py:2', stance: 'supports' }] })
        .includes('a&lt;b&gt;.py:2'));
});

test('feature: a draft asks for approval; execution reads t3 of 9; review counts what is left', () => {
    const { M } = load();
    const tasks = [1, 2, 3].map(id => ({ id, title: `T${id}`, status: id === 1 ? 'completed' : 'not_started', reviewed: false,
        criteria: [{ id: 'c1', text: 'x', met: id === 1, evidence: id === 1 ? [{ kind: 'run', ref: 'r1' }] : [] }] }));
    const draft = { status: 'draft', revision: 2, phases_meta: [{ id: 1, title: 'P' }], tasks, diff_proposals: [] };
    assert.deepEqual(plain(M.feature.sentence({ plan: draft }).map(p => p.text || p)), ['Draft', '1 phase', '3 tasks']);
    assert.equal(M.feature.now({ plan: draft })[0].id, 'approve');
    const running = { ...draft, status: 'executing', current_task: 2 };
    assert.deepEqual(plain(M.feature.sentence({ plan: running })), ['t2 of 3', '']);
    assert.equal(M.feature.footWord({ plan: running }), 't2 of 3');
    const review = { ...draft, status: 'review', tasks: tasks.map(t => ({ ...t, status: 'completed' })) };
    assert.deepEqual(plain(M.feature.sentence({ plan: review })), ['Review', '3 to review']);
    assert.equal(M.feature.criterionKey({ met: true, evidence: [{ kind: 'attested' }] }), 'attested');
    const sheet = M.feature.taskSheet({ ...tasks[0], objective: '<obj>', override: { actor: 'user:gui', reason: 'manual' } }, []);
    assert.ok(sheet.includes('&lt;obj&gt;') && sheet.includes('user:gui'));
});

test('loop: budget wording, pause/resume as the one action, done asks to extend', () => {
    const { M } = load();
    const mission = { status: 'running', status_reason: '', increments: [{}, {}], budget: { max_cost_usd: 10 },
        spent: { cost_usd: 3.4 } };
    assert.deepEqual(plain(M.loop.sentence({ mission }).map(p => p.text || p)), ['running', 'increment 2', '$3.40 of $10.00', '']);
    assert.equal(M.loop.primary({ mission }).label, 'Pause');
    assert.equal(M.loop.primary({ mission: { ...mission, status: 'paused' } }).label, 'Resume');
    assert.equal(M.loop.primary({ mission: null }).label, 'Start a mission');
    const paused = { mission: { ...mission, status: 'paused', status_reason: 'cost budget reached' } };
    assert.equal(M.loop.now(paused)[0].id, 'budget');
    assert.equal(M.loop.now({ mission: { ...mission, status: 'done' } })[0].id, 'done');
    assert.equal(M.loop.meter({ budget_meter: { kind: 'cost_usd', used: 3.4, cap: 10, ratio: 0.34 } }).label, '$3.40 of $10.00');
    assert.equal(M.loop.incrementRow({ n: 3, objective: 'Add retry', result: 'passed', evidence: ['r9'], next: 'metrics' }).sub,
        'passed · r9 · next: metrics');
});

test('teacher: curriculum consent first, gate state in line 2, reviews by date', () => {
    const { M } = load();
    const course = { status: 'curriculum_proposed', modules: [{ module_id: 'm1' }], lessons: [{}, {}], due_reviews: [], assignments: [] };
    assert.equal(M.teacher.now({ course })[0].id, 'approve');
    assert.equal(M.teacher.primary({ course }).label, 'Approve curriculum');
    const lesson = { lesson_id: 'l1', title: 'Returns', status: 'lecturing', order_blockers: [],
        gate: { assessed: 1, required: 2, concluded: false, transcript: false } };
    assert.equal(M.teacher.lessonRow(lesson).sub, 'comprehension 1 of 2');
    assert.equal(M.teacher.lessonGateLine({ ...lesson, gate: { ...lesson.gate, concluded: true } }), 'needs transcript');
    const now = 1_800_000_000;
    assert.equal(M.teacher.reviewRow({ review_id: 'rv1', objective: 'o', status: 'pending', due_at: now - 10 }, now).status, 'waiting');
    assert.equal(M.teacher.reviewRow({ review_id: 'rv1', objective: 'o', status: 'pending', due_at: now + 10 }, now).status, 'pending');
    const live = { status: 'in_progress', current_lesson_id: 'l1', modules: [{ module_id: 'm1' }, { module_id: 'm2' }],
        lessons: [{ lesson_id: 'l1', module_id: 'm1' }], due_reviews: [{ review_id: 'rv1', due_at: now }] };
    assert.deepEqual(plain(M.teacher.sentence({ course: live }).map(p => p.text || p)), ['Module 1 of 2', 'lesson l1', '1 review due']);
    assert.ok(M.teacher.gradeSheet({ grade: { status: 'awaiting_judgement', rubric_breakdown: [] } }).includes('awaiting judgement'));
});

test('default: the receipt line and the plan disclosure rows', () => {
    const { M } = load();
    assert.equal(M.receipt.line({ status: 'verified', files: ['a', 'b'], checks: [] }), '2 files changed · checks passed');
    assert.equal(M.receipt.line({ status: 'unverified', files: ['a'], checks: [] }), '1 file changed · unverified');
    assert.equal(M.receipt.line({ status: 'no_changes', files: [], checks: [] }), '');
    assert.deepEqual(plain(M.receipt.planRows([{ id: 'w1', title: 'a', status: 'in_progress' }, { id: 'w2', title: 'b', status: 'done' }])
        .map(r => [r.id, r.status])), [['w1', 'active']]);
});

test('work items: every open status has its verbs and Done reads runs as evidence, text as the reason', () => {
    const { M } = load();
    const rows = M.receipt.planRows([{ id: 'w5', title: 'fusion', status: 'blocked', note: 'needs a run id' },
        { id: 'w6', title: 'report', status: 'in_progress' }, { id: 'w7', title: 'next', status: 'pending' }]);
    assert.deepEqual(plain(rows.map(r => [r.id, r.note, r.actions.map(a => a.label), r.actions.find(a => a.primary)?.id || null])), [
        ['w5', 'needs a run id', ['Unblock', 'Done…', 'Drop'], 'pending'],
        ['w6', '', ['Done…', 'Not now', 'Drop'], 'done'],
        ['w7', '', ['Start', 'Done…', 'Drop'], null]]);
    assert.deepEqual(plain(M.doneBody('r12, r14')), { status: 'done', evidence: ['r12', 'r14'], override_reason: '' });
    assert.deepEqual(plain(M.doneBody('report reviewed, see r3')),
        { status: 'done', evidence: ['r3'], override_reason: 'report reviewed, see r3' });
});

test('security: coverage sentence, engine-ready decisions, vocabulary and escaped evidence', () => {
    const { M, S } = load();
    const f = { finding_id: 'f1', title: 'Injection', status: 'remediation_verified', proof_verified: true,
        remediation_verified: true, severity_confirmed: true, cvss_score: 9.8, severity: 'critical', cwe: 'CWE-78',
        exploit_path: '<script>alert(1)</script>', affected_paths: ['a.py:1'], runs: [{ id: 'r1', command: 'true', detail: '<output>', purpose: 'poc', runner: 'host' }] };
    const state = { report: { status: 'verifying', scope: { attacker: 'caller' }, coverage: { total: 4, reviewed: 2 } }, findings: [f] };
    assert.deepEqual(plain(S.sentence(M.security.sentence(state)).map(p => p.text)), ['verifying', '2 of 4 surface items reviewed', '1 finding verified']);
    assert.equal(M.security.now(state)[0].actions[0].id, 'approve');
    assert.equal(M.security.findingRow(f).status, 'waiting');
    assert.equal(M.security.findingRow({ ...f, status: 'refuted' }).status, 'skipped');
    assert.equal(M.security.now({ ...state, plan_mode: true }).length, 0);
    assert.equal(M.security.primary({ ...state, plan_mode: true }), null);
    assert.equal(M.security.now({ ...state, findings: [{ ...f, status: 'approved' }] }).length, 0);
    const html = M.security.findingSheet(f);
    assert.ok(html.includes('&lt;script&gt;') && !html.includes('<script>'));
    assert.ok(html.includes('&lt;output&gt;'));
});

test('a blocker is answered with its whole question in view', () => {
    const { M } = load();
    const long = 'Which quantisation should the matmul path use for the next increment, given fp16 inputs change results?';
    const b = { id: 'b1', summary: long, needs: 'a decision on fp16', details: 'Tried int8; accuracy dropped.',
        questions: ['Accept fp16?', 'Keep fp32 fallback?'] };
    const [row] = M.blockerRows([b]);
    assert.equal(row.title, long);
    const q = M.blockerQuestion(row.blocker);
    for (const part of [long, 'Needs: a decision on fp16', 'Tried int8; accuracy dropped.', '• Accept fp16?', '• Keep fp32 fallback?']) {
        assert.ok(q.includes(part), part);
    }
});
