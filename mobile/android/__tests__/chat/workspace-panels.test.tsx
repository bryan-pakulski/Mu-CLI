// Workspace panels as Mu pages (mobile_parity.md §6 Chat; modes_v2.md §5.4): the page header
// carries the state sentence and text tabs; entries are rows with status signs; destructive
// actions ask first; answers and file reads go to the server.
import React from 'react';
import { act, fireEvent, render, waitFor } from '@testing-library/react-native';

const mockGoBack = jest.fn();
jest.mock('@react-navigation/native', () => {
  const React = require('react');
  return {
    useNavigation: () => ({ canGoBack: () => true, goBack: mockGoBack, navigate: jest.fn() }),
    useFocusEffect: (fn: () => void) => React.useEffect(fn, [fn]),
  };
});
jest.mock('../../src/api/loop', () => ({ loopApi: { getState: jest.fn(), control: jest.fn(async () => ({})) } }));
jest.mock('../../src/api/security', () => ({ securityApi: { getState: jest.fn(), approveFinding: jest.fn(async () => ({})), refuteFinding: jest.fn(async () => ({})) } }));
jest.mock('../../src/api/prompts', () => ({ promptsApi: { listPending: jest.fn(), answer: jest.fn(async () => ({})), cancel: jest.fn(async () => ({})) } }));
jest.mock('../../src/api/files', () => ({ filesApi: { getTree: jest.fn(), readFile: jest.fn() } }));

import { loopApi } from '../../src/api/loop';
import { securityApi } from '../../src/api/security';
import { promptsApi } from '../../src/api/prompts';
import { filesApi } from '../../src/api/files';
import { LoopScreen } from '../../src/screens/LoopScreen';
import { SecurityScreen } from '../../src/screens/SecurityScreen';
import { PromptsScreen } from '../../src/screens/PromptsScreen';
import { FilesScreen } from '../../src/screens/FilesScreen';
import { ThemeProvider } from '../../src/theme/ThemeContext';
import { ToastHost } from '../../src/ui';

const wrap = (node: React.ReactElement) => render(<ThemeProvider statusBar={false}><ToastHost>{node}</ToastHost></ThemeProvider>);

const workspace = (mode: string, title: string) => ({
  schema_version: 1, mode, title, objective: 'Keep autonomous work inspectable.',
  status: { label: 'Running', tone: 'active' },
  views: [{ id: 'overview', label: 'Overview' }, { id: 'backlog', label: 'Backlog', count: 2 }],
  metrics: [{ id: 'm1', label: 'queued', value: 2, tone: 'warn', detail: '' }],
  quality: [{ id: 'q1', label: 'Evidence', state: 'not-assessed', detail: 'Completion is self-reported.' }],
  search_placeholder: '', provenance: 'loop_state.json',
});

beforeEach(() => jest.clearAllMocks());

const mission = (over: Record<string, unknown> = {}) => ({
  id: 'm1', goal: 'Ship it', status: 'running', status_reason: '', goal_history: [], budget: { max_cost_usd: 10 }, spent: { cost_usd: 3.4 },
  done_when: [{ id: 'c1', text: 'Tests pass', check: null, met: true, evidence: ['r1'], attested_by: '', note: '' }],
  increments: [{ n: 1, objective: 'Write the parser', result: 'passed', decision: '', next: 'wire the CLI', work_item: '', actions: [], evidence: ['r1'], delegations: [], remediation_item: '', at: 1 }],
  started_at: Date.now() / 1000, ended_at: null, ...over,
});
const loopState = (over: Record<string, unknown> = {}) => ({
  active: true, mission: mission(), budget_meter: { kind: 'cost_usd', used: 3.4, cap: 10, ratio: 0.34 }, current_item: 'w2', blockers: [], past: [],
  backlog: [{ id: 'w1', title: 'Write the parser', status: 'done', note: '' }, { id: 'w2', title: 'Wire the CLI', status: 'blocked', note: '' }],
  runs: [{ id: 'r1', purpose: 'verify', command: 'pytest -q', cwd: '/w', exit_code: 0, passed: true, predicate: { exit: 0 }, predicate_text: 'exit code 0', excerpt: '3 passed', duration_s: 2.1, runner: 'host' }],
  ...over,
});

it('Loop: the mission feeds the state sentence; Pause is real, Stop asks first', async () => {
  (loopApi.getState as jest.Mock).mockResolvedValue(loopState());
  const ui = wrap(<LoopScreen />);
  expect(await ui.findByText('Ship it')).toBeTruthy();
  expect(ui.getByText('Running', { exact: false })).toBeTruthy();
  expect(ui.getByText('increment 1', { exact: false })).toBeTruthy();
  expect(ui.getAllByText('$3.40 of $10.00', { exact: false }).length).toBe(2); // state sentence + budget meter
  expect(ui.getByText('cost $3.40 of $10.00')).toBeTruthy();
  expect(ui.queryByTestId('mode-now')).toBeNull();
  await act(async () => { fireEvent.press(ui.getByTestId('loop-pause')); });
  expect(loopApi.control).toHaveBeenCalledWith('pause');
  fireEvent.press(ui.getByTestId('loop-screen-header-tabs-backlog'));
  expect(ui.getByText('Wire the CLI')).toBeTruthy();
  expect(ui.getByText('blocked')).toBeTruthy();
  fireEvent.press(ui.getByText('Chat'));
  expect(mockGoBack).toHaveBeenCalled();
});

it('Loop: a paused mission waits in Now with Resume; Stop needs confirmation', async () => {
  (loopApi.getState as jest.Mock).mockResolvedValue(loopState({ mission: mission({ status: 'paused', status_reason: 'user paused' }) }));
  const ui = wrap(<LoopScreen />);
  expect(await ui.findByText('Mission paused')).toBeTruthy();
  await act(async () => { fireEvent.press(ui.getByTestId('now-resume')); });
  expect(loopApi.control).toHaveBeenCalledWith('resume');
  (loopApi.control as jest.Mock).mockClear();
  await act(async () => { fireEvent.press(ui.getByTestId('loop-screen-header-menu')); });
  await act(async () => { fireEvent.press(ui.getByText('Stop mission')); });
  expect(loopApi.control).not.toHaveBeenCalled();
  await act(async () => { fireEvent.press(ui.getByTestId('server-action-ok')); });
  expect(loopApi.control).toHaveBeenCalledWith('stop');
});

it('Loop: says so when there is no mission', async () => {
  (loopApi.getState as jest.Mock).mockResolvedValue({ active: false, mission: null, budget_meter: null, backlog: [], current_item: null, blockers: [], runs: [], past: [] });
  const ui = wrap(<LoopScreen />);
  expect(await ui.findByText('No mission')).toBeTruthy();
});

it('Security: refuting a finding asks first and does nothing when cancelled', async () => {
  (securityApi.getState as jest.Mock).mockResolvedValue({
    active: true, summary: null, report: { summary: 'Two issues.' }, workspace: workspace('security', 'Verification bench'),
    findings: [{ finding_id: 'f1', title: 'SQL injection', summary: 'Unescaped id', vulnerability_class: 'injection', severity: 'high', affected_paths: ['app.py'],
      exploit_path: '/q?id=', references: [], status: 'open', has_proof: true, proof_verified: false, proof_command: 'curl', proof_description: '',
      proof_verified_at: null, has_remediation: false, remediation_verified: false, remediation_description: '', remediation_verified_at: null, patch_diff: '' }],
  });
  const ui = wrap(<SecurityScreen />);
  expect(await ui.findByText('SQL injection')).toBeTruthy();
  expect(ui.getByText('PoC pending · fix none')).toBeTruthy();
  await act(async () => { fireEvent.press(ui.getByTestId('refute-f1')); });
  expect(ui.getByText('Abandon this finding as invalid?')).toBeTruthy();
  await act(async () => { fireEvent.press(ui.getByText('Cancel')); });
  expect(securityApi.refuteFinding).not.toHaveBeenCalled();
  expect(ui.queryByTestId('approve-f1')).toBeNull();
  expect(securityApi.approveFinding).not.toHaveBeenCalled();
});

it('Prompts: answering sends the text', async () => {
  (promptsApi.listPending as jest.Mock).mockResolvedValue({ pending: [{ id: 'p1', question: 'Which branch?' }] });
  const ui = wrap(<PromptsScreen />);
  expect(await ui.findByText('Which branch?')).toBeTruthy();
  expect(ui.getByText('1 need you', { exact: false })).toBeTruthy();
  fireEvent.press(ui.getByTestId('pending-p1-answer'));
  fireEvent.changeText(ui.getByTestId('answer-text'), ' main ');
  await act(async () => { fireEvent.press(ui.getByTestId('answer-send')); });
  expect(promptsApi.answer).toHaveBeenCalledWith('p1', { text: 'main' });
});

it('Files: folders open in place and files open read-only', async () => {
  (filesApi.getTree as jest.Mock).mockImplementation(async (path?: string) => path
    ? { path, entries: [{ name: 'app.py', path: `${path}/app.py`, is_dir: false, size: 2048 }] }
    : { path: undefined, roots: [{ name: 'src', path: '/w/src', is_dir: true, size: null }] });
  (filesApi.readFile as jest.Mock).mockResolvedValue({ content: 'print("hi")' });
  const ui = wrap(<FilesScreen />);
  await act(async () => { fireEvent.press(await ui.findByText('▸ src')); });
  expect(filesApi.getTree).toHaveBeenLastCalledWith('/w/src');
  expect(await ui.findByText('2KB')).toBeTruthy();
  await act(async () => { fireEvent.press(ui.getByText('app.py')); });
  await waitFor(() => expect(ui.getByTestId('file-content')).toBeTruthy());
  expect(ui.getByText('print("hi")')).toBeTruthy();
});
