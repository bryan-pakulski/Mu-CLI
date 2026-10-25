// Teacher mode (modes_v2.md §4.6, §5.3 Teacher, §5.4 Android): the path (modules → lessons with
// their gate state in line 2), objective mastery, assignments with grades, and spaced reviews with
// their due dates. Now holds the curriculum approval (the learner's consent), due reviews, an
// exercise waiting for the learner, a grade awaiting judgement and any blocker.

import React, { useCallback, useMemo, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { PanelPage, PanelSection, Fact } from '../components/chat/PanelPage';
import {
  NowBoard, blockersFor, dueText, lessonGateLine, resolveBlockerAction, teacherNow, teacherSentence, useCheckRunSheet, useModeActions,
  type NowItem,
} from '../components/modes';
import { modeStateApi, type Blocker } from '../api/modeState';
import { teacherApi, type TeacherAssignment, type TeacherState } from '../api/teacher';
import { modeState } from '../theme/status';
import { spacing } from '../theme/tokens';
import { Banner, Button, Row, Sheet, Text } from '../ui';

const TABS = [
  { key: 'path', label: 'Path' },
  { key: 'mastery', label: 'Mastery' },
  { key: 'reviews', label: 'Reviews' },
];

/** An assignment's grade as a mark: awaiting judgement waits; graded passes or fails. */
export function gradeMark(a: TeacherAssignment): { status: string; label: string } {
  if (!a.grade) return { status: 'pending', label: a.status.replace(/_/g, ' ') };
  if (a.grade.status === 'awaiting_judgement') return { status: 'waiting', label: 'awaiting judgement' };
  const mark = modeState('check_run', a.grade.passed ? 'passed' : 'failed');
  return { status: mark.status, label: `${mark.label} ${a.grade.score_pct}%` };
}

export function TeacherScreen() {
  const [state, setState] = useState<TeacherState | null>(null);
  const [blockers, setBlockers] = useState<Blocker[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [tab, setTab] = useState('path');
  const [openAssignment, setOpenAssignment] = useState<TeacherAssignment | null>(null);

  const load = useCallback(async () => {
    try {
      setError(null);
      const [next, open] = await Promise.all([
        teacherApi.getState(),
        modeStateApi.listBlockers().then(r => r.open).catch(() => [] as Blocker[]),
      ]);
      setState(next);
      setBlockers(blockersFor('teacher', open));
    } catch (e) { setError(String(e)); }
    finally { setLoading(false); setRefreshing(false); }
  }, []);
  useFocusEffect(useCallback(() => { setLoading(true); void load(); }, [load]));

  const actions = useModeActions(load);
  const sheet = useCheckRunSheet();
  const course = state?.course || null;
  const now = useMemo(() => teacherNow(course, blockers), [course, blockers]);

  const actionsFor = (item: NowItem) => {
    if (item.kind === 'blocker') {
      return <Button label="Answer" variant="primary" testID={`now-answer-${item.ref}`}
        onPress={() => void actions.run(resolveBlockerAction(item), args => modeStateApi.resolveBlocker(item.ref, String(args.resolution || '')))} />;
    }
    if (item.kind === 'approve_curriculum') {
      return <Button label="Approve curriculum" variant="primary" icon="circle-check" testID="teacher-approve"
        onPress={() => void actions.run({ id: 'approve', label: 'Approve curriculum', confirm: 'Start this course with the proposed modules and lessons?' }, () => teacherApi.approve())} />;
    }
    return null;
  };

  const lessonsOf = (moduleId: string) => (course?.lessons || []).filter(l => l.module_id === moduleId);
  const empty = !loading && !error && !course && !(state?.courses.length) && !now.length;
  return (
    <PanelPage testID="teacher-screen" title={course?.subject || 'Teacher'} state={teacherSentence(course)}
      tabs={TABS} tab={tab} onTab={setTab}
      loading={loading} error={error} onRetry={() => void load()}
      empty={empty ? { title: 'No course', message: 'Tell the teacher what you want to learn in teacher mode; it proposes a curriculum for you to approve.' } : null}
      refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }}
      overlay={<>{actions.element}{sheet.element}</>}>
      {actions.error ? <Banner tone="danger" title="That didn't go through" testID="teacher-error">{actions.error}</Banner> : null}
      <NowBoard items={now} actionsFor={actionsFor} />

      {!course && state?.courses.length ? (
        <PanelSection title="Courses">
          {state.courses.map(c => {
            const mark = modeState('teacher_course', c.status);
            return <Row key={c.course_id} status={mark.status} title={c.subject || c.course_id} when={mark.label}
              sub={c.lessons ? `${c.lessons_completed} of ${c.lessons} lessons` : undefined} />;
          })}
        </PanelSection>
      ) : null}

      {course && tab === 'path' ? [...course.modules].sort((a, b) => a.order - b.order).map(m => (
        <PanelSection key={m.module_id} title={m.title} right={m.mastery_pct != null ? <Text variant="caption" tone="ink3" numeric>{`${m.mastery_pct}%`}</Text> : undefined} testID={`module-${m.module_id}`}>
          {lessonsOf(m.module_id).map(l => {
            const mark = modeState('teacher_lesson', l.status);
            return <Row key={l.lesson_id} status={mark.status} title={l.title} when={mark.label} sub={lessonGateLine(l) || undefined}
              selected={l.lesson_id === course.current_lesson_id} testID={`lesson-${l.lesson_id}`} />;
          })}
        </PanelSection>
      )) : null}

      {course && tab === 'mastery' ? (
        <>
          <PanelSection title="Objectives" right={course.course_mastery != null ? <Text variant="caption" tone="ink3" numeric>{`course ${course.course_mastery}%`}</Text> : undefined}
            empty="No objectives yet.">
            {course.mastery.map((row, i) => (
              <Row key={`${row.lesson_id}:${i}`} title={row.objective} when={row.mastery_pct != null ? `${row.mastery_pct}%` : 'not assessed'}
                sub={row.attempts ? `${row.attempts} attempt${row.attempts === 1 ? '' : 's'}` : undefined} />
            ))}
          </PanelSection>
          <PanelSection title="Assignments" empty="No assignments yet.">
            {course.assignments.map(a => {
              const mark = gradeMark(a);
              return <Row key={a.assignment_id} status={mark.status} title={a.prompt.split('\n')[0] || a.assignment_id} when={mark.label}
                onPress={() => setOpenAssignment(a)} testID={`assignment-${a.assignment_id}`} />;
            })}
          </PanelSection>
        </>
      ) : null}

      {course && tab === 'reviews' ? (
        <>
          <PanelSection title="Due" empty="Nothing due.">
            {course.due_reviews.map(r => {
              const mark = modeState('teacher_review', 'due');
              return <Row key={r.review_id} status={mark.status} title={r.objective || r.source_lesson_id} when={dueText(r)} testID={`review-${r.review_id}`} />;
            })}
          </PanelSection>
          <PanelSection title="Upcoming" empty="No reviews scheduled.">
            {course.upcoming_reviews.filter(r => !course.due_reviews.some(d => d.review_id === r.review_id)).map(r => {
              const mark = modeState('teacher_review', 'scheduled');
              return <Row key={r.review_id} status={mark.status} title={r.objective || r.source_lesson_id} when={dueText(r)} testID={`review-${r.review_id}`} />;
            })}
          </PanelSection>
        </>
      ) : null}

      <Sheet visible={openAssignment !== null} onClose={() => setOpenAssignment(null)} title="Assignment" testID="assignment-sheet">
        {openAssignment ? (
          <View style={styles.sheet}>
            <Row status={gradeMark(openAssignment).status} title={gradeMark(openAssignment).label} />
            <Text variant="body">{openAssignment.prompt}</Text>
            {openAssignment.grade?.feedback ? <Fact label="Feedback" value={openAssignment.grade.feedback} /> : null}
            {(openAssignment.grade?.rubric_breakdown || []).map((r, i) => (
              <Fact key={i} label={String(r.criterion || `criterion ${i + 1}`)} value={[r.score != null ? `score ${r.score}` : '', r.weight != null ? `weight ${r.weight}` : '', r.description ? String(r.description) : ''].filter(Boolean).join(' · ')} />
            ))}
            {openAssignment.grade?.check_run ? <Button variant="ghost" label="Grader run ▸" onPress={() => { const id = openAssignment.grade!.check_run; setOpenAssignment(null); sheet.open(id); }} /> : null}
          </View>
        ) : null}
      </Sheet>
    </PanelPage>
  );
}

const styles = StyleSheet.create({
  sheet: { gap: spacing.sm },
});
