import { api } from './client';

export interface TeacherCourseSummary {
  course_id: string;
  subject: string;
  status: string;
  is_active: boolean;
  lesson_total: number;
  lessons_completed_count: number;
  directory: string;
}

export interface TeacherModule {
  module_id: string;
  title: string;
  goal: string;
  order: number;
  status: string | null;
  lesson_ids: string[];
  mastery_threshold: number | null;
  lessons: TeacherLesson[];
}

export interface TeacherLesson {
  lesson_id: string;
  module_id: string;
  title: string;
  status: string;
  concept_brief: string;
  learning_objectives: string[];
  lecture_turn_count: number;
  lecture_comprehension_pct: number | null;
  remediation_count: number;
  lecture_transcript_path: string | null;
  exercise_file_paths: string[];
}

export interface TeacherCourse {
  course_id: string;
  subject: string;
  target_level: string;
  status: string;
  directory: string;
  learner_profile: Record<string, unknown>;
  current_module_id: string | null;
  current_lesson_id: string | null;
  current_assignment_id: string | null;
  lessons_completed_count: number;
  lesson_total: number;
  modules: TeacherModule[];
  lessons: TeacherLesson[];
  assignments: Array<Record<string, unknown>>;
  scheduled_reviews: Array<Record<string, unknown>>;
}

export interface TeacherState {
  active: boolean;
  active_course_id: string | null;
  course: TeacherCourse | null;
  courses: TeacherCourseSummary[];
  raw_teacher_state_present: boolean;
  registry_size: number;
}

export const teacherApi = {
  getState: () => api.get<TeacherState>('/api/teacher/state'),
  getLecture: (lessonId: string) =>
    api.get<{ lesson_id: string; content: string }>(`/api/teacher/lessons/${encodeURIComponent(lessonId)}/lecture`),
  getExercises: (lessonId: string) =>
    api.get<{ lesson_id: string; files: Array<{ path: string; content: string }> }>(`/api/teacher/lessons/${encodeURIComponent(lessonId)}/exercises`),
  getExerciseFile: (lessonId: string, path: string) =>
    api.get<{ lesson_id: string; path: string; content: string }>(`/api/teacher/lessons/${encodeURIComponent(lessonId)}/exercises/${path}`),
};