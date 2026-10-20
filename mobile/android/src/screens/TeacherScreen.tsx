// Teacher mode (web teacher_panel.html): the course, its modules and lessons, mastery checks and
// the retention schedule.

import React, { useCallback, useState } from 'react';
import { StyleSheet, View } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { ModeWorkspaceIntro, useModeWorkspaceView, workspaceState, workspaceTabs } from '../components';
import { PanelPage, PanelSection } from '../components/chat/PanelPage';
import { teacherApi, type TeacherLesson, type TeacherModule, type TeacherState } from '../api/teacher';
import { spacing } from '../theme/tokens';
import { Row, Rows, Text } from '../ui';

/** Course / module / lesson status → the shared status vocabulary. */
export function lessonStatus(status: string | null | undefined): string {
  if (status === 'completed' || status === 'done') return 'done';
  if (status === 'presenting' || status === 'in_progress') return 'active';
  return 'pending';
}

export function TeacherScreen() {
  const [state, setState] = useState<TeacherState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [expandedModule, setExpandedModule] = useState<string | null>(null);
  const workspaceView = useModeWorkspaceView(state?.workspace);

  const load = useCallback(async () => {
    try { setError(null); setState(await teacherApi.getState()); }
    catch (e) { setError(String(e)); }
    finally { setLoading(false); setRefreshing(false); }
  }, []);
  useFocusEffect(useCallback(() => { setLoading(true); void load(); }, [load]));

  const workspace = state?.workspace;
  const course = state?.course;
  const showModules = workspaceView.selectedView !== 'mastery' && workspaceView.selectedView !== 'reviews';
  const assignments = (course?.assignments || []) as Array<Record<string, any>>;
  const reviews = (course?.scheduled_reviews || []) as Array<Record<string, any>>;
  const empty = !state || (!state.active && state.courses.length === 0);

  return (
    <PanelPage testID="teacher-screen" title={workspace?.title || 'Teacher'} state={workspaceState(workspace)}
      tabs={workspaceTabs(workspace)} tab={workspaceView.selectedView} onTab={workspaceView.selectView}
      loading={loading} error={error} onRetry={() => void load()}
      empty={!loading && !error && empty ? { title: 'No courses', message: 'No teacher courses available.' } : null}
      refreshing={refreshing} onRefresh={() => { setRefreshing(true); void load(); }}>
      {state && workspace ? (
        <>
          <ModeWorkspaceIntro workspace={workspace} />
          {course ? (
            <PanelSection title="Course">
              <Row status={lessonStatus(course.status)} title={course.subject} when={course.status.replace(/_/g, ' ')}
                sub={`${course.target_level} · ${course.lessons_completed_count}/${course.lesson_total} lessons`} testID="teacher-course" />
            </PanelSection>
          ) : (
            <PanelSection title="No active course" count={state.courses.length} empty="No courses yet.">
              {state.courses.map(c => <Row key={c.course_id} status={lessonStatus(c.status)} title={c.subject} when={c.status.replace(/_/g, ' ')} />)}
            </PanelSection>
          )}
          {course && showModules ? (
            <PanelSection title="Modules" count={course.modules.length} empty="No modules yet.">
              {course.modules.map(module => (
                <ModuleRow key={String(module.module_id)} module={module} expanded={expandedModule === String(module.module_id)}
                  onToggle={() => setExpandedModule(prev => (prev === String(module.module_id) ? null : String(module.module_id)))} />
              ))}
            </PanelSection>
          ) : null}
          {course && workspaceView.shows('mastery') ? (
            <PanelSection title="Demonstrated mastery" count={assignments.length} empty="No mastery checks recorded.">
              {assignments.map((assignment, index) => {
                const grade = assignment.grade as Record<string, any> | undefined;
                return (
                  <Row key={String(assignment.assignment_id || index)} status={grade ? (grade.passed ? 'done' : 'waiting') : 'pending'}
                    title={String(assignment.assignment_id || 'Assignment')} when={grade ? `${grade.score_pct ?? '—'}%` : undefined}
                    sub={(
                      <View style={styles.sub}>
                        <Text variant="caption" tone="ink3">{String(assignment.kind || assignment.status || 'assignment')}</Text>
                        {assignment.prompt ? <Text variant="caption" tone="ink2">{String(assignment.prompt)}</Text> : null}
                        {grade?.feedback ? <Text variant="caption" tone="ink3">Evidence: {String(grade.feedback)}</Text> : null}
                        {!grade ? <Text variant="caption" tone="attention">Mastery not assessed yet.</Text> : null}
                      </View>
                    )} />
                );
              })}
            </PanelSection>
          ) : null}
          {course && workspaceView.shows('reviews') ? (
            <PanelSection title="Retention schedule" count={reviews.length} empty="No spaced reviews scheduled.">
              {reviews.map((review, index) => (
                <Row key={String(review.review_id || index)} status={review.status === 'done' ? 'done' : 'pending'}
                  title={String(review.source_lesson_title || review.source_lesson_id || 'Review')}
                  when={review.due_at_lesson_count != null ? `lesson ${review.due_at_lesson_count}` : undefined}
                  sub={[String(review.status || 'scheduled'), review.score_pct != null ? `recall score ${review.score_pct}%` : '', review.notes ? String(review.notes) : ''].filter(Boolean).join(' · ')} />
              ))}
            </PanelSection>
          ) : null}
        </>
      ) : null}
    </PanelPage>
  );
}

function ModuleRow({ module, expanded, onToggle }: { module: TeacherModule; expanded: boolean; onToggle: () => void }) {
  const done = module.lessons.filter(l => l.status === 'completed').length;
  return (
    <Row status={lessonStatus(module.status)} title={module.title} when={`${done}/${module.lessons.length}`} onPress={onToggle}
      accessibilityLabel={`${module.title}, ${done} of ${module.lessons.length} lessons${expanded ? ', expanded' : ''}`}
      testID={`module-${module.module_id}`}
      actions={expanded ? (
        <View style={styles.lessons}>
          <Rows>{module.lessons.map(lesson => <LessonRow key={lesson.lesson_id} lesson={lesson} />)}</Rows>
        </View>
      ) : undefined} />
  );
}

function LessonRow({ lesson }: { lesson: TeacherLesson }) {
  return (
    <Row status={lessonStatus(lesson.status)} title={lesson.title} when={lesson.status.replace(/_/g, ' ')}
      sub={lesson.lecture_comprehension_pct !== null ? `Comprehension ${lesson.lecture_comprehension_pct}%` : undefined} />
  );
}

const styles = StyleSheet.create({
  sub: { gap: spacing.xs },
  lessons: { flex: 1 },
});
