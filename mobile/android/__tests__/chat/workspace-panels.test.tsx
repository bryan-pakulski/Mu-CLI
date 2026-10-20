// Workspace panels as Mu pages (mobile_parity.md §6 Chat): the mode workspace feeds the page
// header (title, status + metrics as the state sentence, views as tabs); entries are rows with
// status signs; destructive actions ask first; answers and file reads go to the server.
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
jest.mock('../../src/api/loop', () => ({ loopApi: { getState: jest.fn(), setActive: jest.fn(async () => ({})) } }));
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

it('Loop: the workspace becomes the header, and the backlog shows each item\'s status', async () => {
  (loopApi.getState as jest.Mock).mockResolvedValue({
    active: true, loop_goal: 'Ship it', loop_active: true, loop_features: [], memory: [], workspace: workspace('loop', 'Mission control'),
    backlog: [{ id: 1, content: 'Write the parser', status: 'completed', source: 'plan.md', tags: [] }, { id: 2, content: 'Wire the CLI', status: 'blocked', source: '', tags: [] }],
  });
  const ui = wrap(<LoopScreen />);
  expect(await ui.findByText('Mission control')).toBeTruthy();
  expect(ui.getByText('Running', { exact: false })).toBeTruthy();
  expect(ui.getByText('2 queued', { exact: false })).toBeTruthy();
  expect(ui.getByTestId('loop-screen-header-tabs-backlog')).toBeTruthy();
  expect(ui.getByLabelText('done')).toBeTruthy();
  expect(ui.getByLabelText('failed')).toBeTruthy();
  expect(ui.getByText('1/2')).toBeTruthy();
  await act(async () => { fireEvent.press(ui.getByTestId('loop-toggle')); });
  expect(loopApi.setActive).toHaveBeenCalledWith(false, 'Ship it');
  fireEvent.press(ui.getByText('Chat'));
  expect(mockGoBack).toHaveBeenCalled();
});

it('Loop: says so when no loop is active', async () => {
  (loopApi.getState as jest.Mock).mockResolvedValue({ active: false, loop_goal: '', loop_active: false, loop_features: [], backlog: [], memory: [], workspace: workspace('loop', 'Mission control') });
  const ui = wrap(<LoopScreen />);
  expect(await ui.findByText('No loop active')).toBeTruthy();
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
  await act(async () => { fireEvent.press(ui.getByTestId('approve-f1')); });
  await act(async () => { fireEvent.press(ui.getByTestId('server-action-ok')); });
  expect(securityApi.approveFinding).toHaveBeenCalledWith('f1');
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
