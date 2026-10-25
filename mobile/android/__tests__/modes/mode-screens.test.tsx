// Mode screens on Android (modes_v2.md §5.4): the header's state sentence, Now with its decisive
// actions (approve plan, approve curriculum, answer a blocker, close a verified case), records
// read-only, and the shared CheckRun sheet.
import React from 'react';
import { act, fireEvent, render } from '@testing-library/react-native';

jest.mock('@react-navigation/native', () => {
  const React = require('react');
  return {
    useNavigation: () => ({ canGoBack: () => false, goBack: jest.fn(), navigate: jest.fn() }),
    useFocusEffect: (fn: () => void) => React.useEffect(fn, [fn]),
  };
});
jest.mock('../../src/api/debug', () => ({ debugApi: { getState: jest.fn(), close: jest.fn(async () => ({})) } }));
jest.mock('../../src/api/feature', () => ({ featureApi: { getState: jest.fn(), approve: jest.fn(async () => ({})), decideDiff: jest.fn(async () => ({})), preview: jest.fn() } }));
jest.mock('../../src/api/teacher', () => ({ teacherApi: { getState: jest.fn(), approve: jest.fn(async () => ({})) } }));
jest.mock('../../src/api/modeState', () => ({
  modeStateApi: {
    listBlockers: jest.fn(async () => ({ open: [], resolved: [] })), resolveBlocker: jest.fn(async () => ({ ok: true })),
    getCheck: jest.fn(), getCheckOutput: jest.fn(async () => ({ id: 'r1', output: 'FULL OUTPUT' })),
  },
}));

import { debugApi } from '../../src/api/debug';
import { featureApi } from '../../src/api/feature';
import { teacherApi } from '../../src/api/teacher';
import { modeStateApi } from '../../src/api/modeState';
import { DebugScreen } from '../../src/screens/DebugScreen';
import { FeatureExplorerScreen } from '../../src/screens/FeatureExplorerScreen';
import { TeacherScreen } from '../../src/screens/TeacherScreen';
import { ThemeProvider } from '../../src/theme/ThemeContext';
import { ToastHost } from '../../src/ui';

// The joined text of a rendered node (state sentences are nested Text parts).
const textOf = (node: any): string => (typeof node === 'string' ? node : (node?.children || []).map(textOf).join(''));

const wrap = (node: React.ReactElement) => render(<ThemeProvider statusBar={false}><ToastHost>{node}</ToastHost></ThemeProvider>);

const run = { id: 'r1', purpose: 'repro', label: 'Repro fails as expected', command: 'pytest tests/x.py::test_y', cwd: '/w', exit_code: 1, passed: true,
  predicate: { exit_nonzero: true }, predicate_text: 'exit code is not 0', excerpt: 'KeyError: id', duration_s: 2.1, runner: 'host', hashes: {} };

const blocker = { id: 'b1', summary: 'Which database?', needs: 'decision', mode: 'debug', ref: '', details: '', questions: ['Postgres or SQLite?'],
  raised_at: 1, raised_by: 'agent', resolved_at: null, resolution: '', resolved_by: '', open: true };

const debugState = (status: string) => ({
  active: true, plan_mode: false, past_root_causes: [], runs: [run],
  cases: [{ id: 'd1', title: 'KeyError in sync', status, hypotheses: 1, suspects: 1, repro: 'pytest', updated_at: 1 }],
  case: { id: 'd1', title: 'KeyError in sync', symptom: 'sync crashes', status, repro: { command: 'pytest tests/x.py::test_y' },
    hypotheses: [{ id: 'h1', statement: 'Stale cache key', status: 'supported', evidence: [{ kind: 'run', ref: 'r1', stance: 'supports', note: 'fails without the key' }], created_at: 0, updated_at: 0 }],
    suspects: [{ path: 'sync.py', line: 42, reason: 'reads the key', symbol: '' }], fix: {}, verification: {}, regression_test: null, root_cause: null,
    bisect: null, unreproducible_reason: '', memory_id: '', created_at: 0, updated_at: 1 },
});

beforeEach(() => jest.clearAllMocks());

it('Debug: the case is the state sentence; a verified case asks to close with a root cause', async () => {
  (debugApi.getState as jest.Mock).mockResolvedValue(debugState('verified'));
  const ui = wrap(<DebugScreen />);
  expect(await ui.findByText('Close case?')).toBeTruthy();
  expect(textOf(ui.getByTestId('debug-screen-header-state'))).toBe('KeyError in sync  ·  verified  ·  1 hypothesis');
  expect(ui.getByTestId('pipeline-step-root_caused')).toBeTruthy();
  await act(async () => { fireEvent.press(ui.getByTestId('debug-close')); });
  fireEvent.changeText(ui.getByTestId('server-action-input'), 'cache key never invalidated');
  await act(async () => { fireEvent.press(ui.getByTestId('server-action-ok')); });
  expect(debugApi.close).toHaveBeenCalledWith({ cause: 'cache key never invalidated' });
});

it('Debug: a blocker is answered from Now', async () => {
  (debugApi.getState as jest.Mock).mockResolvedValue(debugState('reproduced'));
  (modeStateApi.listBlockers as jest.Mock).mockResolvedValue({ open: [blocker, { ...blocker, id: 'b2', mode: 'feature', summary: 'Elsewhere' }], resolved: [] });
  const ui = wrap(<DebugScreen />);
  expect(await ui.findByText('Which database?')).toBeTruthy();
  expect(ui.queryByText('Elsewhere')).toBeNull();
  expect(ui.queryByText('Close case?')).toBeNull();
  await act(async () => { fireEvent.press(ui.getByTestId('now-answer-b1')); });
  fireEvent.changeText(ui.getByTestId('server-action-input'), 'SQLite');
  await act(async () => { fireEvent.press(ui.getByTestId('server-action-ok')); });
  expect(modeStateApi.resolveBlocker).toHaveBeenCalledWith('b1', 'SQLite');
});

it('Debug: runs open the CheckRun sheet with command, predicate, excerpt and full output', async () => {
  (debugApi.getState as jest.Mock).mockResolvedValue(debugState('reproduced'));
  const ui = wrap(<DebugScreen />);
  fireEvent.press(await ui.findByTestId('debug-screen-header-tabs-runs'));
  await act(async () => { fireEvent.press(ui.getByTestId('check-run-r1')); });
  expect(textOf(ui.getByTestId('check-run-command'))).toBe('pytest tests/x.py::test_y');
  expect(ui.getByText('exit code is not 0')).toBeTruthy();
  expect(textOf(ui.getByTestId('check-run-excerpt'))).toBe('KeyError: id');
  await act(async () => { fireEvent.press(ui.getByTestId('check-run-output')); });
  expect(modeStateApi.getCheckOutput).toHaveBeenCalledWith('r1');
  expect(ui.getByText('FULL OUTPUT')).toBeTruthy();
});

it('Feature: a draft plan waits in Now; approving goes to the engine; tasks show criterion evidence', async () => {
  const crit = (id: string, met: boolean, evidence: unknown[]) => ({ id, text: `criterion ${id}`, check: null, met, evidence });
  const plan = {
    feature_id: 'sync', feature_name: 'Sync', feature_request: 'Add sync', directory: '', metadata_path: '', status: 'draft', revision: 1, approval: null,
    approval_history: [], phases_meta: [{ id: 1, title: 'Core', goal: '', order: 0 }], event_log: [], review_records: [], diff_proposals: [],
    current_task_id: null, completion_requested: false, completion: null, created_at: 0, updated_at: 0, hash: 'h', approved: false,
    approval_current: false, current_task: null, next_task: 1, counts: {}, unreviewed: [], completion_gaps: [],
    tasks: [{ id: 1, title: 'Parser', phase_id: 1, objective: 'Parse it', actions: [], status: 'not_started', notes: '', origin: 'plan', started_at: null,
      completed_at: null, diff_stat: '', reviewed: false, override: null, unmet: ['c2'],
      criteria: [crit('c1', true, [{ kind: 'run', ref: 'r1' }]), crit('c2', true, [{ kind: 'attested', actor: 'user:gui' }]), crit('c3', false, [])] }],
  };
  (featureApi.getState as jest.Mock).mockResolvedValue({ active: true, active_feature_id: 'sync', plan, features: [], blockers: [], runs: [run], read_only: false });
  const ui = wrap(<FeatureExplorerScreen />);
  expect(await ui.findByText('Approve plan · Sync')).toBeTruthy();
  expect(textOf(ui.getByTestId('feature-screen-header-state'))).toBe('Awaiting approval  ·  1 phase  ·  1 task');
  await act(async () => { fireEvent.press(ui.getByTestId('feature-approve')); });
  await act(async () => { fireEvent.press(ui.getByTestId('server-action-ok')); });
  expect(featureApi.approve).toHaveBeenCalled();
  fireEvent.press(ui.getByTestId('feature-screen-header-tabs-tasks'));
  expect(ui.getByText('2 of 3 criteria met')).toBeTruthy();
  await act(async () => { fireEvent.press(ui.getByTestId('task-1')); });
  expect(ui.getByText('met')).toBeTruthy();
  expect(ui.getByText('attested')).toBeTruthy();
  expect(ui.getByText('not met')).toBeTruthy();
});

it('Teacher: the curriculum waits for consent; reviews show their due dates', async () => {
  const lesson = (id: string, status: string) => ({ lesson_id: id, module_id: 'm1', title: `Lesson ${id}`, learning_objectives: [], concept_brief: '', status,
    assignment_ids: [], remediation_count: 0, lecture_comprehension_pct: null, lecture_concluded: false, min_lecture_checks: 2,
    lecture_comprehension_threshold: 60, lecture_transcript_path: null, exercise_file_paths: [], known_concept_skip: null, override: null,
    gate: { checks: 0, answered: 0, assessed: 0, comprehension_pct: null, required: 2, threshold: 60, concluded: false, transcript: false, skip: null },
    order_blockers: [], mastery_pct: null });
  const course = {
    course_id: 'rust', subject: 'Rust', status: 'curriculum_proposed', target_level: 'beginner', revision: 1, curriculum_approval: null, early_exit: null,
    current_lesson_id: null, current_assignment_id: null, learner_profile: {}, diagnostic_recorded: true,
    modules: [{ module_id: 'm1', title: 'Ownership', goal: '', order: 0, status: 'pending', lesson_ids: ['l1', 'l2'], mastery_threshold: 70, mastery_pct: null }],
    lessons: [lesson('l1', 'pending'), lesson('l2', 'pending')], assignments: [], checks: [], mastery: [], course_mastery: null,
    due_reviews: [], upcoming_reviews: [{ review_id: 'rv1', source_lesson_id: 'l1', status: 'pending', score_pct: null, completed_at: null, notes: '',
      objective: 'borrowing', due_at: Date.now() / 1000 + 3 * 86400 + 60, interval_days: 3, assignment_id: '' }],
    metrics: { total_modules: 1, total_lessons: 2, lessons_completed: 0, overall_pct: 0, average_score_pct: 0, current_module_index: 0, current_lesson_index_in_module: 0 },
    directory: '', events: [],
  };
  (teacherApi.getState as jest.Mock).mockResolvedValue({ active: true, active_course_id: 'rust', course, courses: [], course_path: null });
  const ui = wrap(<TeacherScreen />);
  expect(await ui.findByText('Approve curriculum · Rust')).toBeTruthy();
  expect(ui.getByText('1 module · 2 lessons')).toBeTruthy();
  expect(ui.getByText('Lesson l1')).toBeTruthy();
  await act(async () => { fireEvent.press(ui.getByTestId('teacher-approve')); });
  await act(async () => { fireEvent.press(ui.getByTestId('server-action-ok')); });
  expect(teacherApi.approve).toHaveBeenCalled();
  fireEvent.press(ui.getByTestId('teacher-screen-header-tabs-reviews'));
  expect(ui.getByText('borrowing')).toBeTruthy();
  expect(ui.getByText('due in 3d')).toBeTruthy();
});
