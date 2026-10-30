// The mode shell's pure model (src/components/modes/modeModel.ts; modes_v2.md §5.2, §5.4):
// state sentences drop zero parts, Now is ordered by urgency, records get vocabulary words, and
// the turn receipt reads as one line under the right assistant message.
import {
  count, debugNow, debugSentence, debugSteps, dueText, featureCriterionKey, featureNow, featureSentence, lessonGateLine, loopNow,
  loopSentence, orderNow, placeReceipts, receiptLine, sentence, teacherNow, teacherSentence, workDoneBody, workItemActions, type NowItem,
} from '../../src/components/modes/modeModel';
import { modeState } from '../../src/theme/status';

const blocker = (id: string, raised_at: number, mode = '') => ({
  id, summary: `Blocker ${id}`, needs: 'decision', mode, ref: '', details: '', questions: ['Which DB?'], raised_at, raised_by: 'agent',
  resolved_at: null, resolution: '', resolved_by: '', open: true,
});

const task = (id: number, status: string, over: Record<string, unknown> = {}) => ({
  id, title: `Task ${id}`, phase_id: 1, objective: '', actions: [], status, notes: '', origin: 'plan', started_at: null, completed_at: null,
  diff_stat: '', reviewed: false, override: null, unmet: [], criteria: [{ id: 'c1', text: 'works', check: null, met: false, evidence: [] }], ...over,
});

const plan = (over: Record<string, unknown> = {}) => ({
  feature_id: 'f', feature_name: 'Sync', feature_request: '', directory: '', metadata_path: '', status: 'draft', revision: 1, approval: null,
  approval_history: [], tasks: [task(1, 'not_started'), task(2, 'not_started'), task(3, 'archived')], phases_meta: [{ id: 1, title: 'P1', goal: '', order: 0 }],
  event_log: [], review_records: [], diff_proposals: [], current_task_id: null, completion_requested: false, completion: null, created_at: 0,
  updated_at: 5, hash: 'h', approved: false, approval_current: false, current_task: null, next_task: 1, counts: {}, unreviewed: [], completion_gaps: [],
  ...over,
}) as any;

describe('state sentence', () => {
  it('drops zero and empty parts and capitalises the first', () => {
    expect(count(0, 'hypothesis', 'hypotheses')).toBeNull();
    expect(count(1, 'hypothesis', 'hypotheses')).toBe('1 hypothesis');
    expect(sentence('', null, 'reproduced', count(0, 'run'), { text: '' }, false).map(p => p.text)).toEqual(['Reproduced']);
  });

  it('debug: the case title, its status word and the hypotheses', () => {
    const state: any = { active: true, cases: [], runs: [], past_root_causes: [], plan_mode: false,
      case: { id: 'd1', title: 'KeyError in sync', status: 'reproduced', hypotheses: [{}, {}, {}], suspects: [] } };
    expect(debugSentence(state).map(p => p.text)).toEqual(['KeyError in sync', 'reproduced', '3 hypotheses']);
    state.case.hypotheses = [];
    expect(debugSentence(state).map(p => p.text)).toEqual(['KeyError in sync', 'reproduced']);
  });

  it('feature: draft counts phases and live tasks; executing names the current task and blocked ones', () => {
    expect(featureSentence(plan()).map(p => p.text)).toEqual(['Awaiting approval', '1 phase', '2 tasks']);
    const exec = plan({ status: 'executing', current_task: 2, tasks: [task(1, 'completed'), task(2, 'in_progress'), task(3, 'blocked')] });
    expect(featureSentence(exec).map(p => p.text)).toEqual(['T2 of 3', '1 blocked']);
    expect(featureSentence(plan({ status: 'review', unreviewed: [1, 2] })).map(p => p.text)).toEqual(['In review', '2 tasks to review']);
  });

  it('loop: status, increment, cost of cap and runtime left; zero spend is left out', () => {
    const now = 1000;
    const m: any = { id: 'm', goal: 'g', status: 'running', status_reason: '', done_when: [], budget: { max_cost_usd: 10, max_runtime_s: 7200 },
      spent: { cost_usd: 3.4, runtime_s: 0 }, increments: Array.from({ length: 12 }, (_, i) => ({ n: i + 1 })), started_at: now - 600 };
    expect(loopSentence(m, now).map(p => p.text)).toEqual(['Running', 'increment 12', '$3.40 of $10.00', '1h 50m left']);
    const paused = { ...m, status: 'paused', status_reason: 'cost_usd budget reached', budget: {}, spent: { cost_usd: 0 }, increments: [] };
    expect(loopSentence(paused, now).map(p => p.text)).toEqual(['Paused — cost_usd budget reached']);
  });

  it('teacher: module and lesson position plus due reviews', () => {
    const course: any = { status: 'in_progress', modules: [{}, {}, {}, {}], lessons: [], due_reviews: [{}, {}],
      metrics: { current_module_index: 2, total_modules: 4, current_lesson_index_in_module: 3 } };
    expect(teacherSentence(course).map(p => p.text)).toEqual(['Module 2 of 4', 'lesson 2.3', '2 reviews due']);
    expect(teacherSentence({ ...course, status: 'curriculum_proposed', due_reviews: [], metrics: {} }).map(p => p.text)).toEqual(['Curriculum to approve', '4 modules']);
  });
});

describe('Now', () => {
  it('orders approvals, then blockers, then decisions, then suggestions; oldest first within a rank', () => {
    const items: NowItem[] = [
      { key: 'r', kind: 'review_due', ref: 'r', title: 'r', at: 1 },
      { key: 'b2', kind: 'blocker', ref: 'b2', title: 'b2', at: 20 },
      { key: 'c', kind: 'complete_feature', ref: 'c', title: 'c' },
      { key: 'b1', kind: 'blocker', ref: 'b1', title: 'b1', at: 10 },
      { key: 'd', kind: 'diff_decision', ref: 'd', title: 'd', at: 0 },
      { key: 'a', kind: 'approve_plan', ref: 'a', title: 'a', at: 99 },
    ];
    expect(orderNow(items).map(i => i.key)).toEqual(['a', 'b1', 'b2', 'd', 'c', 'r']);
  });

  it('feature: a draft asks for approval before the blockers and pending diffs', () => {
    const state: any = { plan: plan({ diff_proposals: [{ id: 'p1', task_id: 1, status: 'pending', rationale: 'fix', created_at: 3 }, { id: 'p2', task_id: 1, status: 'approved' }] }),
      blockers: [blocker('b1', 1)], read_only: false, runs: [], features: [] };
    expect(featureNow(state).map(i => i.kind)).toEqual(['approve_plan', 'blocker', 'diff_decision']);
    expect(featureNow({ ...state, read_only: true })).toEqual([]);
    const review: any = { ...state, blockers: [], plan: plan({ status: 'review', diff_proposals: [] }) };
    expect(featureNow(review).map(i => i.kind)).toEqual(['complete_feature']);
    expect(featureNow({ ...review, plan: plan({ status: 'review', completion_gaps: ['t2 not reviewed'] }) })).toEqual([]);
  });

  it('loop: a reached budget, a paused or a finished mission wait for the user; a running one does not', () => {
    const base: any = { mission: { id: 'm', status: 'running', status_reason: '' }, blockers: [] };
    expect(loopNow(base)).toEqual([]);
    expect(loopNow({ ...base, mission: { id: 'm', status: 'paused', status_reason: 'cost_usd budget reached' } }).map(i => i.kind)).toEqual(['budget_exhausted']);
    expect(loopNow({ ...base, mission: { id: 'm', status: 'paused', status_reason: '' }, blockers: [blocker('b', 1)] }).map(i => i.kind)).toEqual(['blocker', 'mission_paused']);
    expect(loopNow({ ...base, mission: { id: 'm', status: 'done', status_reason: '' } }).map(i => i.kind)).toEqual(['mission_done']);
  });

  it('debug: "Close case?" only once the fix is verified', () => {
    const state: any = { case: { id: 'd1', status: 'fixed', updated_at: 1 } };
    expect(debugNow(state)).toEqual([]);
    expect(debugNow({ case: { id: 'd1', status: 'verified', updated_at: 1 } } as any, [blocker('b', 0)]).map(i => i.kind)).toEqual(['blocker', 'close_case']);
  });

  it('teacher: curriculum consent first, then a grade awaiting judgement, then reminders', () => {
    const course: any = { course_id: 'c', subject: 'Rust', status: 'curriculum_proposed', revision: 1, modules: [{}], current_assignment_id: 'a2',
      lessons: [{ lesson_id: 'l1', title: 'Ownership' }], due_reviews: [{ review_id: 'rv1', source_lesson_id: 'l1', objective: 'borrowing', due_at: 5 }],
      assignments: [{ assignment_id: 'a1', lesson_id: 'l1', status: 'submitted', grade: { status: 'awaiting_judgement', feedback: '', graded_at: 9 } },
        { assignment_id: 'a2', lesson_id: 'l1', status: 'presented', prompt: 'Write it\nmore', grade: null, created_at: 2 }] };
    expect(teacherNow(course).map(i => i.kind)).toEqual(['approve_curriculum', 'grade_judgement', 'exercise_waiting', 'review_due']);
  });
});

describe('records', () => {
  it('vocabulary words come from the shared tables; unknown keys keep their raw word', () => {
    expect(modeState('hypothesis', 'refuted')).toEqual({ status: 'failed', label: 'refuted' });
    expect(modeState('blocker', 'open').status).toBe('waiting');
    expect(modeState('feature_task', 'some_new_state')).toEqual({ status: 'pending', label: 'some new state' });
  });

  it('criterion evidence: a run is met, user attestation is attested, otherwise not met', () => {
    expect(featureCriterionKey({ id: 'c', text: '', check: null, met: true, evidence: [{ kind: 'run', ref: 'r1' }] })).toBe('met');
    expect(featureCriterionKey({ id: 'c', text: '', check: null, met: true, evidence: [{ kind: 'attested', actor: 'user:mobile' }] })).toBe('attested');
    expect(featureCriterionKey({ id: 'c', text: '', check: null, met: false, evidence: [] })).toBe('unmet');
  });

  it('debug ladder: passed rungs done, the current one active, closed all done', () => {
    expect(debugSteps('root_caused').map(s => s.status)).toEqual(['done', 'done', 'active', 'pending', 'pending']);
    expect(debugSteps('closed').every(s => s.status === 'done')).toBe(true);
    expect(debugSteps('fixed')[3].label).toBe('fixed');
  });

  it('lesson gate lines', () => {
    const gate = { checks: 1, answered: 1, assessed: 1, comprehension_pct: 50, required: 3, threshold: 60, concluded: false, transcript: true, skip: null };
    const lesson: any = { status: 'lecturing', gate, order_blockers: [], mastery_pct: null };
    expect(lessonGateLine(lesson)).toBe('comprehension 1 of 3');
    expect(lessonGateLine({ ...lesson, gate: { ...gate, transcript: false } })).toBe('needs transcript');
    expect(lessonGateLine({ ...lesson, status: 'completed', mastery_pct: 85 })).toBe('passed 85%');
  });

  it('review due dates', () => {
    expect(dueText({ due_at: 50 }, 100)).toBe('due now');
    expect(dueText({ due_at: 100 + 3 * 86400 + 5 }, 100)).toBe('due in 3d');
    expect(dueText({ due_at: 0 }, 100)).toBe('');
  });
});

describe('turn receipt', () => {
  const r = (status: string, files: string[], turn: number | null = 4, checks: any[] = []) => ({ status, files, checks, work_items: [], blockers: [], turn, at: turn ?? 99 });

  it('reads as one quiet line', () => {
    expect(receiptLine(r('verified', ['a.py', 'b.py']))).toBe('2 files changed · checks passed');
    expect(receiptLine(r('unverified', ['a.py']))).toBe('1 file changed · unverified');
    expect(receiptLine(r('no_changes', []))).toBe('');
    expect(receiptLine(r('no_changes', [], 4, [{ passed: true }, { passed: false }]))).toBe('No files changed · 1 of 2 checks passed');
  });

  it('sits under the last assistant message of its turn; a live receipt goes under the newest turn', () => {
    const messages = [
      { id: 'u1', role: 'user', historyIndex: 0 }, { id: 'a1', role: 'assistant', historyIndex: 1 }, { id: 'a2', role: 'assistant', historyIndex: 3 },
      { id: 'u2', role: 'user', historyIndex: 4 }, { id: 'a3', role: 'assistant', historyIndex: 5 },
      { id: 'u3', role: 'user' }, { id: 'a4', role: 'assistant' },
    ];
    const placed = placeReceipts(messages, [r('verified', ['x'], 0), r('unverified', ['y'], 9), r('no_changes', [], 4)]);
    expect(placed.a2?.status).toBe('verified');
    expect(placed.a4?.status).toBe('unverified');
    expect(placed.a3).toBeUndefined(); // nothing to say
    expect(placed.a1).toBeUndefined();
  });
});

describe('work items', () => {
  it('blocked, in-progress and to-do items each have verbs, the decisive one first', () => {
    expect(workItemActions({ status: 'blocked' }).map(a => [a.id, a.label])).toEqual([['pending', 'Unblock'], ['done', 'Done…'], ['dropped', 'Drop']]);
    expect(workItemActions({ status: 'in_progress' })[0]).toEqual({ id: 'done', label: 'Done…', primary: true });
    expect(workItemActions({ status: 'pending' })[0].label).toBe('Start');
    expect(workItemActions({ status: 'done' })).toEqual([]);
  });

  it('Done reads check runs as evidence and keeps any other text as the recorded reason', () => {
    expect(workDoneBody('r12 r14')).toEqual({ status: 'done', evidence: ['r12', 'r14'], override_reason: '' });
    expect(workDoneBody('report reviewed, see r3')).toEqual({ status: 'done', evidence: ['r3'], override_reason: 'report reviewed, see r3' });
  });
});
