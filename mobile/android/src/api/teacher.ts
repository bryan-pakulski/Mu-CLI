// Teacher mode (modes_v2.md §4.6; mu/gui/routers/teacher.py): the active course — curriculum
// with each lesson's gate state, assignments with grades, comprehension checks, objective
// mastery, due and upcoming reviews (SM-2, by date) and the learner profile. The learner's
// decisions (curriculum consent, starting a lesson out of order, finishing early) go through
// the engine's gates.

import { api } from './client';

export type TeacherCourseStatus = 'diagnosing' | 'curriculum_proposed' | 'in_progress' | 'completed' | 'archived';
export type TeacherLessonStatus = 'pending' | 'presenting' | 'lecturing' | 'assigned' | 'graded' | 'completed' | 'remediating';

export interface TeacherCourseSummary {
  course_id: string;
  subject: string | null;
  status: string | null;
  is_active: boolean;
  lessons: number;
  lessons_completed: number;
  directory: string | null;
}

/** A lesson's lecture gate (mu/teacher/gates.py lecture_state). */
export interface TeacherLessonGate {
  checks: number;
  answered: number;
  assessed: number;
  comprehension_pct: number | null;
  required: number;
  threshold: number;
  concluded: boolean;
  transcript: boolean;
  skip: Record<string, unknown> | null;
}

export interface TeacherModule {
  module_id: string;
  title: string;
  goal: string;
  order: number;
  status: string;
  lesson_ids: string[];
  mastery_threshold: number;
  mastery_pct: number | null;
}

export interface TeacherLesson {
  lesson_id: string;
  module_id: string;
  title: string;
  learning_objectives: string[];
  concept_brief: string;
  status: TeacherLessonStatus | string;
  assignment_ids: string[];
  remediation_count: number;
  lecture_comprehension_pct: number | null;
  lecture_concluded: boolean;
  min_lecture_checks: number;
  lecture_comprehension_threshold: number;
  lecture_transcript_path: string | null;
  exercise_file_paths: string[];
  known_concept_skip: Record<string, unknown> | null;
  override: Record<string, unknown> | null;
  gate: TeacherLessonGate;
  order_blockers: string[];
  mastery_pct: number | null;
}

export interface TeacherGrade {
  score_pct: number;
  passed: boolean;
  rubric_breakdown: Array<Record<string, unknown>>;
  verification_result: Record<string, unknown>;
  feedback: string;
  graded_at: number;
  status: 'graded' | 'awaiting_judgement' | string;
  check_run: string;
}

export interface TeacherAssignment {
  assignment_id: string;
  lesson_id: string;
  kind: string;
  prompt: string;
  artifact_paths: string[];
  status: string;
  grade: TeacherGrade | null;
  pass_threshold: number;
  created_at: number;
  objectives: string[];
  review_id: string;
  precheck_run: string;
}

export interface TeacherCheck {
  check_id: string;
  lesson_id: string;
  question: string;
  kind: 'open' | 'choice' | string;
  options: string[];
  objective: string;
  answer: string | null;
  answered_at: number | null;
  signal: 'on_track' | 'partial' | 'confused' | string | null;
  note: string;
  asked_at: number;
}

export interface TeacherMasteryRow {
  lesson_id: string;
  module_id: string;
  objective: string;
  mastery_pct: number | null;
  attempts: number;
}

export interface TeacherReview {
  review_id: string;
  source_lesson_id: string;
  status: 'pending' | 'done' | 'skipped' | string;
  score_pct: number | null;
  completed_at: number | null;
  notes: string;
  objective: string;
  due_at: number;
  interval_days: number;
  assignment_id: string;
}

export interface TeacherMetrics {
  total_modules: number;
  total_lessons: number;
  lessons_completed: number;
  overall_pct: number;
  average_score_pct: number;
  current_module_index: number;
  current_lesson_index_in_module: number;
}

export interface TeacherCourse {
  course_id: string;
  subject: string;
  status: TeacherCourseStatus | string;
  target_level: string;
  revision: number;
  curriculum_approval: { actor?: string; at?: number; revision?: number } | null;
  early_exit: { actor?: string; reason?: string; at?: number } | null;
  current_lesson_id: string | null;
  current_assignment_id: string | null;
  learner_profile: Record<string, unknown>;
  diagnostic_recorded: boolean;
  modules: TeacherModule[];
  lessons: TeacherLesson[];
  assignments: TeacherAssignment[];
  checks: TeacherCheck[];
  mastery: TeacherMasteryRow[];
  course_mastery: number | null;
  due_reviews: TeacherReview[];
  upcoming_reviews: TeacherReview[];
  metrics: TeacherMetrics;
  directory: string;
  events: Array<Record<string, unknown>>;
}

export interface TeacherState {
  active: boolean;
  active_course_id: string | null;
  course: TeacherCourse | null;
  courses: TeacherCourseSummary[];
  course_path: string | null;
}

const lid = (lessonId: string) => encodeURIComponent(lessonId);

export const teacherApi = {
  getState: () => api.get<TeacherState>('/api/teacher/state'),
  /** The learner approves the proposed curriculum. */
  approve: () => api.post<TeacherState>('/api/teacher/approve'),
  startLesson: (lessonId: string, overrideReason = '') =>
    api.post<TeacherState>(`/api/teacher/lessons/${lid(lessonId)}/start`, { override_reason: overrideReason }),
  finishEarly: (reason: string) => api.post<TeacherState>('/api/teacher/finish-early', { reason }),
  getLecture: (lessonId: string) =>
    api.get<{ lesson_id: string; content: string }>(`/api/teacher/lessons/${lid(lessonId)}/lecture`),
  getExercises: (lessonId: string) =>
    api.get<{ lesson_id: string; files: Array<{ path: string; content: string }> }>(`/api/teacher/lessons/${lid(lessonId)}/exercises`),
  getExerciseFile: (lessonId: string, path: string) => {
    // Server-provided path goes into a URL route segment: percent-encode each
    // segment (keep '/' separators — backend route is {path:path}) so ?#, ../
    // fragments or encoded separators can't alter routing semantics.
    const encodedPath = path.split('/').map(encodeURIComponent).join('/');
    return api.get<{ lesson_id: string; path: string; content: string }>(`/api/teacher/lessons/${lid(lessonId)}/exercises/${encodedPath}`);
  },
};
