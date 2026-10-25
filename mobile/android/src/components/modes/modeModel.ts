// The mode shell's pure model (modes_v2.md §5.2, §5.4 Android): state sentences with zero parts
// dropped, the Now list (what waits for the user, ordered by urgency), and the record words —
// check-run lines, criterion evidence marks, the debug ladder, lesson gate lines, review due
// dates and the default-mode turn receipt line. Screens render these; nothing here touches React.

import type { StatePart } from '../../ui';
import type { Blocker, CheckRun, TurnReceipt } from '../../api/modeState';
import type { DebugState } from '../../api/debug';
import type { FeaturePlan, FeatureState, FeatureCriterion } from '../../api/feature';
import type { LoopCriterion, LoopMission, LoopState } from '../../api/loop';
import type { TeacherCourse, TeacherLesson, TeacherReview } from '../../api/teacher';
import { duration, modeState, money, type Status } from '../../theme/status';
import type { StepLike } from '../../ui/Stepper';

/* ------------------------------------------------------------ state sentence */

type Part = string | StatePart | null | undefined | false;

/** "3 hypotheses" / "1 hypothesis"; null for zero (the sentence drops it). */
export function count(n: number | null | undefined, one: string, many = `${one}s`): string | null {
  const value = Number(n || 0);
  return value ? `${value} ${value === 1 ? one : many}` : null;
}

const capitalize = (text: string) => (text ? text[0].toUpperCase() + text.slice(1) : text);

/** Parts → state-sentence parts: empty parts dropped, the first capitalised. */
export function sentence(...parts: Part[]): StatePart[] {
  const out = parts
    .filter((p): p is string | StatePart => (typeof p === 'string' ? p.length > 0 : Boolean(p && p.text)))
    .map(p => (typeof p === 'string' ? { text: p } : p));
  if (out.length) out[0] = { ...out[0], text: capitalize(out[0].text) };
  return out;
}

/** "KeyError in sync · reproduced · 3 hypotheses". */
export function debugSentence(state: DebugState | null | undefined): StatePart[] {
  const c = state?.case;
  if (!c) return sentence(state?.cases.length ? count(state.cases.length, 'case') : 'No open case');
  return sentence(c.title, modeState('debug_case', c.status).label, count(c.hypotheses.length, 'hypothesis', 'hypotheses'));
}

/** Draft · 3 phases · 9 tasks → t3 of 9 · 1 blocked → Review · 2 to review → Done · 9 tasks. */
export function featureSentence(plan: FeaturePlan | null | undefined): StatePart[] {
  if (!plan) return sentence('No feature loaded');
  const live = plan.tasks.filter(t => t.status !== 'archived');
  const blocked = live.filter(t => t.status === 'blocked').length;
  const label = modeState('feature_plan', plan.status).label;
  if (plan.status === 'draft') {
    return sentence({ text: label, tone: 'attention' }, count(plan.phases_meta.length, 'phase'), count(live.length, 'task'));
  }
  if (plan.status === 'review') {
    return sentence(label, count(plan.unreviewed.length, 'task to review', 'tasks to review'), blocked ? { text: `${blocked} blocked`, tone: 'attention' } : null);
  }
  if (plan.status === 'executing' || plan.status === 'approved') {
    const current = plan.current_task ?? null;
    const done = live.filter(t => t.status === 'completed').length;
    const position = current != null ? `t${current} of ${live.length}` : `${done} of ${live.length} done`;
    return sentence(plan.status === 'approved' && current == null ? label : position, blocked ? { text: `${blocked} blocked`, tone: 'attention' } : null);
  }
  return sentence(label, count(live.length, 'task'));
}

function runtimeLeft(mission: LoopMission, nowS: number): string | null {
  const cap = Number(mission.budget.max_runtime_s || 0);
  if (!cap || mission.status !== 'running') return null;
  const used = Math.max(Number(mission.spent.runtime_s || 0), nowS - Number(mission.started_at || nowS));
  const left = cap - used;
  return left > 0 ? `${duration(left * 1000)} left` : null;
}

/** "Running · increment 12 · $3.40 of $10 · 2h 10m left" (or "Paused — budget reached"). */
export function loopSentence(mission: LoopMission | null | undefined, nowS = Date.now() / 1000): StatePart[] {
  if (!mission) return sentence('No mission');
  const label = modeState('loop_mission', mission.status).label;
  const head = mission.status !== 'running' && mission.status_reason ? `${label} — ${mission.status_reason}` : label;
  const tone: StatePart['tone'] = mission.status === 'paused' || mission.status === 'blocked' ? 'attention' : '';
  const n = mission.increments.length;
  const spent = Number(mission.spent.cost_usd || 0);
  const cap = Number(mission.budget.max_cost_usd || 0);
  const cost = cap ? `${money(spent)} of ${money(cap)}` : spent ? money(spent) : null;
  return sentence({ text: head, tone }, n ? `increment ${n}` : null, cost, runtimeLeft(mission, nowS));
}

/** "Module 2 of 4 · lesson 2.3 · 2 reviews due". */
export function teacherSentence(course: TeacherCourse | null | undefined): StatePart[] {
  if (!course) return sentence('No active course');
  const m = course.metrics || ({} as TeacherCourse['metrics']);
  const due = count(course.due_reviews.length, 'review due', 'reviews due');
  if (course.status !== 'in_progress' || !m.current_module_index) {
    const tone: StatePart['tone'] = course.status === 'curriculum_proposed' ? 'attention' : '';
    return sentence({ text: modeState('teacher_course', course.status).label, tone }, count(course.modules.length, 'module'), due);
  }
  return sentence(
    `module ${m.current_module_index} of ${m.total_modules}`,
    m.current_lesson_index_in_module ? `lesson ${m.current_module_index}.${m.current_lesson_index_in_module}` : null,
    due ? { text: due, tone: 'attention' } : null,
  );
}

/* ------------------------------------------------------------ Now */

/**
 * What waits for the user. `rank` orders by urgency: user-only approvals first (nothing moves
 * until they're given), then blockers the agent raised, then decisions on records, then budget
 * and mission state, then suggestions, then learner reminders. Within a rank, oldest first.
 */
export type NowKind =
  | 'approve_plan' | 'approve_curriculum' | 'blocker' | 'diff_decision' | 'grade_judgement'
  | 'budget_exhausted' | 'mission_paused' | 'mission_done' | 'close_case' | 'complete_feature'
  | 'exercise_waiting' | 'review_due';

export const NOW_RANK: Record<NowKind, number> = {
  approve_plan: 0, approve_curriculum: 0,
  blocker: 1,
  diff_decision: 2, grade_judgement: 2,
  budget_exhausted: 3, mission_paused: 3, mission_done: 3,
  close_case: 4, complete_feature: 4,
  exercise_waiting: 5, review_due: 5,
};

export interface NowItem {
  key: string;
  kind: NowKind;
  /** The record it acts on (blocker id, proposal id, review id, …). */
  ref: string;
  title: string;
  sub?: string;
  at?: number;
  blocker?: Blocker;
}

export function orderNow(items: NowItem[]): NowItem[] {
  return [...items].sort((a, b) => (NOW_RANK[a.kind] - NOW_RANK[b.kind]) || ((a.at || 0) - (b.at || 0)) || a.key.localeCompare(b.key));
}

export function blockerNow(blockers: Blocker[] | null | undefined): NowItem[] {
  return (blockers || []).filter(b => b.open !== false && !b.resolved_at).map(b => ({
    key: `blocker:${b.id}`, kind: 'blocker' as const, ref: b.id, title: b.summary,
    sub: [b.needs ? `Needs ${b.needs}` : '', ...(b.questions || [])].filter(Boolean).join(' · ') || undefined,
    at: b.raised_at, blocker: b,
  }));
}

/** Session blockers that belong on a mode's panel: its own, and the session-wide ones. */
export function blockersFor(mode: string, blockers: Blocker[] | null | undefined): Blocker[] {
  return (blockers || []).filter(b => b.open !== false && !b.resolved_at && (!b.mode || b.mode === mode));
}

export function debugNow(state: DebugState | null | undefined, blockers: Blocker[] = []): NowItem[] {
  const items = blockerNow(blockers);
  const c = state?.case;
  if (c && c.status === 'verified') {
    items.push({ key: `close:${c.id}`, kind: 'close_case', ref: c.id, title: 'Close case?', sub: 'The fix is verified. State the root cause to close it.', at: c.updated_at });
  }
  return orderNow(items);
}

export function featureNow(state: FeatureState | null | undefined): NowItem[] {
  const plan = state?.plan;
  if (!plan || state?.read_only) return [];
  const items = blockerNow(state?.blockers);
  const live = plan.tasks.filter(t => t.status !== 'archived');
  if (plan.status === 'draft') {
    items.push({
      key: `approve:${plan.feature_id}`, kind: 'approve_plan', ref: plan.feature_id, title: `Approve plan · ${plan.feature_name}`,
      sub: [count(plan.phases_meta.length, 'phase'), count(live.length, 'task'), count(live.reduce((n, t) => n + t.criteria.length, 0), 'criterion', 'criteria'), plan.revision > 1 ? `revision ${plan.revision}` : null].filter(Boolean).join(' · '),
      at: plan.updated_at,
    });
  }
  for (const p of plan.diff_proposals.filter(d => d.status === 'pending')) {
    const task = plan.tasks.find(t => t.id === p.task_id);
    items.push({ key: `diff:${p.id}`, kind: 'diff_decision', ref: p.id, title: `Diff for t${p.task_id}${task ? ` ${task.title}` : ''}`, sub: p.rationale || undefined, at: p.created_at });
  }
  if (plan.status === 'review' && !(plan.completion_gaps || []).length && !plan.unreviewed.length) {
    items.push({ key: `complete:${plan.feature_id}`, kind: 'complete_feature', ref: plan.feature_id, title: 'Mark feature complete?', sub: 'Every task is complete and reviewed.', at: plan.updated_at });
  }
  return orderNow(items);
}

export function loopNow(state: LoopState | null | undefined): NowItem[] {
  const mission = state?.mission;
  const items = blockerNow(state?.blockers);
  if (mission) {
    const reason = mission.status_reason || '';
    if (mission.status === 'paused' && /budget/i.test(reason)) {
      items.push({ key: `budget:${mission.id}`, kind: 'budget_exhausted', ref: mission.id, title: 'Budget reached', sub: `${capitalize(reason)}. Raise the budget to continue, or stop the mission.` });
    } else if (mission.status === 'paused') {
      items.push({ key: `paused:${mission.id}`, kind: 'mission_paused', ref: mission.id, title: 'Mission paused', sub: reason ? capitalize(reason) : undefined });
    } else if (mission.status === 'done') {
      items.push({ key: `done:${mission.id}`, kind: 'mission_done', ref: mission.id, title: 'Mission done — extend or finish?', sub: reason ? capitalize(reason) : undefined, at: mission.ended_at || undefined });
    }
  }
  return orderNow(items);
}

export function teacherNow(course: TeacherCourse | null | undefined, blockers: Blocker[] = []): NowItem[] {
  const items = blockerNow(blockers);
  if (!course) return orderNow(items);
  if (course.status === 'curriculum_proposed') {
    const lessons = course.lessons.length;
    items.push({
      key: `curriculum:${course.course_id}`, kind: 'approve_curriculum', ref: course.course_id, title: `Approve curriculum · ${course.subject}`,
      sub: [count(course.modules.length, 'module'), count(lessons, 'lesson'), course.revision > 1 ? `revision ${course.revision}` : null].filter(Boolean).join(' · '),
    });
  }
  for (const r of course.due_reviews) {
    items.push({ key: `review:${r.review_id}`, kind: 'review_due', ref: r.review_id, title: `Review due · ${r.objective || lessonTitle(course, r.source_lesson_id)}`, sub: 'Ask for it in the chat to start.', at: r.due_at });
  }
  for (const a of course.assignments) {
    if (a.grade?.status === 'awaiting_judgement') {
      items.push({ key: `grade:${a.assignment_id}`, kind: 'grade_judgement', ref: a.assignment_id, title: `Grade awaiting judgement · ${lessonTitle(course, a.lesson_id)}`, sub: a.grade.feedback || undefined, at: a.grade.graded_at });
    } else if (a.assignment_id === course.current_assignment_id && a.status === 'presented') {
      items.push({ key: `exercise:${a.assignment_id}`, kind: 'exercise_waiting', ref: a.assignment_id, title: `Exercise waiting · ${lessonTitle(course, a.lesson_id)}`, sub: a.prompt ? a.prompt.split('\n')[0] : undefined, at: a.created_at });
    }
  }
  return orderNow(items);
}

function lessonTitle(course: TeacherCourse, lessonId: string): string {
  return course.lessons.find(l => l.lesson_id === lessonId)?.title || lessonId;
}

/* ------------------------------------------------------------ records */

export function checkRunMark(run: Pick<CheckRun, 'passed' | 'timed_out'>): { status: Status; label: string } {
  return modeState('check_run', run.timed_out ? 'timed_out' : run.passed ? 'passed' : 'failed');
}

/** "Repro fails as expected · pytest tests/x.py::test_y · 2.1s". */
export function checkRunLine(run: CheckRun): { title: string; sub: string } {
  const secs = Number(run.duration_s || 0);
  return {
    title: run.label || capitalize(String(run.purpose || 'check').replace(/_/g, ' ')),
    sub: [run.command, secs ? `${secs.toFixed(1)}s` : ''].filter(Boolean).join(' · '),
  };
}

/** Feature criterion: met with a run (✓), attested by the user, or not met. */
export function featureCriterionKey(c: FeatureCriterion): 'met' | 'attested' | 'unmet' {
  if (!c.met) return 'unmet';
  return c.evidence.some(e => e.kind === 'run' || e.kind === 'file') ? 'met' : c.evidence.some(e => e.kind === 'attested') ? 'attested' : 'met';
}

export function loopCriterionKey(c: LoopCriterion): 'met' | 'attested' | 'unmet' {
  if (!c.met) return 'unmet';
  return c.evidence.length ? 'met' : c.attested_by ? 'attested' : 'met';
}

const LADDER = ['open', 'reproduced', 'root_caused', 'fixed', 'verified'] as const;

/** The case's ladder as stepper steps: passed rungs done, the current one active. */
export function debugSteps(status: string): StepLike[] {
  const closed = status === 'closed';
  const at = LADDER.indexOf(status as typeof LADDER[number]);
  return LADDER.map((rung, i) => ({
    id: rung,
    label: modeState('debug_case', rung).label.replace(/,.*$/, ''),
    status: closed || i < at ? 'done' : i === at ? (rung === 'verified' ? 'done' : 'active') : 'pending',
  }));
}

/** Line 2 of a lesson row: "needs transcript", "comprehension 2 of 3", "passed 85%". */
export function lessonGateLine(lesson: TeacherLesson): string {
  const g = lesson.gate;
  if (lesson.status === 'completed') return lesson.mastery_pct != null ? `passed ${lesson.mastery_pct}%` : 'passed';
  if (g?.skip) return 'known concept, lecture skipped';
  if (lesson.status === 'pending') return lesson.order_blockers?.length ? `after ${lesson.order_blockers.join(', ')}` : '';
  if (lesson.status === 'presenting' || lesson.status === 'lecturing' || lesson.status === 'remediating') {
    if (g && !g.transcript) return 'needs transcript';
    if (g && g.assessed < g.required) return `comprehension ${g.assessed} of ${g.required}`;
    if (g && g.comprehension_pct != null && g.comprehension_pct < g.threshold) return `comprehension ${g.comprehension_pct}%, needs ${g.threshold}%`;
    return 'ready for the exercise';
  }
  return modeState('teacher_lesson', lesson.status).label;
}

/** A review's due date: "due now", "due in 3d", "due 2026-11-02". */
export function dueText(review: Pick<TeacherReview, 'due_at'>, nowS = Date.now() / 1000): string {
  const at = Number(review.due_at || 0);
  if (!at) return '';
  const delta = at - nowS;
  if (delta <= 0) return 'due now';
  if (delta < 14 * 86400) {
    const d = Math.floor(delta / 86400);
    const h = Math.floor(delta / 3600);
    return d >= 1 ? `due in ${d}d` : h >= 1 ? `due in ${h}h` : 'due within the hour';
  }
  return `due ${new Date(at * 1000).toISOString().slice(0, 10)}`;
}

/* ------------------------------------------------------------ turn receipt */

/** "2 files changed · checks passed" / "2 files changed · unverified"; '' when nothing to say. */
export function receiptLine(receipt: TurnReceipt | null | undefined): string {
  if (!receipt) return '';
  const files = receipt.files || [];
  const checks = receipt.checks || [];
  if (receipt.status === 'no_changes') {
    if (!checks.length) return '';
    const passed = checks.filter(c => c.passed).length;
    return `No files changed · ${passed} of ${checks.length} checks passed`;
  }
  const head = `${files.length} file${files.length === 1 ? '' : 's'} changed`;
  return `${head} · ${modeState('turn_receipt', receipt.status).label}`;
}

type MessageLike = { id: string; role: string; historyIndex?: number };

/**
 * Which assistant message each receipt sits under: the last assistant message of the turn whose
 * user message has the receipt's `turn` (history index). A receipt for a turn not in the window
 * by index (a live turn, before history reloads) goes under the last turn when it's the newest.
 */
export function placeReceipts(messages: MessageLike[], receipts: TurnReceipt[]): Record<string, TurnReceipt> {
  const segments: Array<{ turn?: number; anchor?: string }> = [];
  for (const m of messages) {
    if (m.role === 'user') {
      const prev = segments[segments.length - 1];
      // Consecutive user parts of one checkpoint share a segment.
      if (!prev || prev.anchor || prev.turn !== m.historyIndex || m.historyIndex == null) segments.push({ turn: m.historyIndex });
    } else if (m.role === 'assistant') {
      if (!segments.length) segments.push({});
      segments[segments.length - 1].anchor = m.id;
    }
  }
  const out: Record<string, TurnReceipt> = {};
  const newest = receipts.reduce<TurnReceipt | null>((best, r) => (!best || (r.at || 0) >= (best.at || 0) ? r : best), null);
  for (const r of receipts) {
    if (!receiptLine(r)) continue;
    const seg = r.turn != null ? segments.find(s => s.turn === r.turn) : undefined;
    if (seg?.anchor) out[seg.anchor] = r;
    else if (r === newest) {
      const last = segments[segments.length - 1];
      if (last?.anchor && (last.turn == null || r.turn == null || last.turn <= r.turn)) out[last.anchor] = r;
    }
  }
  return out;
}
