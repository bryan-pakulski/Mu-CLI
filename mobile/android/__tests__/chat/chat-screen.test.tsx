// The conversation (src/screens/ChatScreen.tsx) on fixture messages: prose and user bubbles, a
// finished turn's "Worked" disclosure with the web's tool summary inside, the inline approval with
// its breadcrumb, sending through the composer, and copying a reply.
import React from 'react';
import { act, fireEvent, render } from '@testing-library/react-native';
import * as Clipboard from 'expo-clipboard';

const mockSend = jest.fn(async () => true);
const mockStop = jest.fn();
let mockMessages: unknown[] = [];
let mockStreaming = false;
let mockReceipts: unknown[] = [];
jest.mock('../../src/hooks/useChatSession', () => ({
  useChatSession: () => ({
    messages: mockMessages, receipts: mockReceipts, setMessages: (fn: (m: unknown[]) => unknown[]) => { mockMessages = fn(mockMessages); }, streaming: mockStreaming,
    waitingForFirstToken: false, historyLoading: false, activityLabel: 'Thinking', sseConnected: true, error: null, artifactRevision: 0,
    hasMore: false, loadingOlder: false, sendMessage: mockSend, stop: mockStop, retry: jest.fn(), loadOlderHistory: jest.fn(async () => 0),
  }),
}));
jest.mock('@react-navigation/native', () => ({ useIsFocused: () => true }));
jest.mock('expo-clipboard', () => ({ setStringAsync: jest.fn(async () => true) }));
jest.mock('../../src/components/ArtifactStrip', () => ({ ArtifactStrip: () => null }));
jest.mock('../../src/components/EffortControl', () => ({ EffortControl: () => null }));
jest.mock('../../src/components/chat/useContextFill', () => ({ useContextFill: () => 0.3 }));
jest.mock('../../src/api/inspector', () => ({ inspectorApi: { getVariables: jest.fn(async () => ({ groups: [] })), setVariable: jest.fn() } }));
jest.mock('../../src/api/prompts', () => ({ promptsApi: { answer: jest.fn(async () => ({})), cancel: jest.fn(), listPending: jest.fn() } }));
jest.mock('../../src/hooks/useCommandCompletion', () => ({
  useCommandCompletion: () => ({ visible: false, items: [], selectedIdx: 0, update: jest.fn(), close: jest.fn() }),
}));

import { promptsApi } from '../../src/api/prompts';
import { ChatScreen } from '../../src/screens/ChatScreen';
import { useConnectionStore } from '../../src/store/connection';
import { usePromptStore } from '../../src/store/prompts';
import { ThemeProvider } from '../../src/theme/ThemeContext';

const tool = (id: string, name: string, args: unknown, status = 'done', result = 'ok') => ({ id, role: 'tool', text: '', tool: { id, name, args, status, result, durationMs: 200 } });

beforeEach(() => {
  jest.clearAllMocks();
  mockStreaming = false;
  mockReceipts = [];
  mockMessages = [
    { id: 'u1', role: 'user', text: 'Fix the failing test' },
    { id: 'c1', role: 'collapse', text: '', collapseCount: 3, collapseElapsed: '12s', collapseOpen: false, childTurns: [
      { id: 'a0', role: 'assistant', text: 'Looking at the tests first.' },
      tool('t1', 'read_file', { path: 'tests/test_a.py' }), tool('t2', 'bash', { command: 'pytest -q' }, 'failed', 'Error: 1 failed'),
    ] },
    { id: 'a1', role: 'assistant', text: 'Fixed: the fixture was stale.' },
  ];
  useConnectionStore.setState({ activeSessionName: 's1', isConnected: true, activeModel: 'qwen3.8:27b', yolo: false, planMode: false } as never);
  usePromptStore.setState({ session: 's1', queue: [], index: 0, submitting: false, error: null, resolved: {}, chatVisible: false, lastMessageId: null });
});

const renderChat = (props: Partial<React.ComponentProps<typeof ChatScreen>> = {}) => render(
  <ThemeProvider statusBar={false}>
    <ChatScreen mode={{ name: 'default', label: 'Default' }} onOpenModes={jest.fn()} onOpenSettings={jest.fn()} onOpenContext={jest.fn()} artifactsVisible {...props} />
  </ThemeProvider>,
);

it('shows the conversation and folds a finished turn into Worked, with the web tool summary inside', () => {
  const ui = renderChat();
  expect(ui.getByText('Fix the failing test')).toBeTruthy();
  expect(ui.getByText('Worked for 12s · 3 steps')).toBeTruthy();
  expect(ui.queryByText('Looking at the tests first.')).toBeNull();
  fireEvent.press(ui.getByText('Worked for 12s · 3 steps'));
  ui.rerender(<ThemeProvider statusBar={false}><ChatScreen mode={{ name: 'default', label: 'Default' }} onOpenModes={jest.fn()} onOpenSettings={jest.fn()} onOpenContext={jest.fn()} artifactsVisible /></ThemeProvider>);
  expect(ui.getByText('Looking at the tests first.')).toBeTruthy();
  expect(ui.getByText('Read 1 file · ran 1 command')).toBeTruthy();
  expect(ui.getByText('1 failed')).toBeTruthy();
});

it('marks the transcript visible so prompts are answered inline', () => {
  renderChat();
  expect(usePromptStore.getState().chatVisible).toBe(true);
  expect(usePromptStore.getState().lastMessageId).toBe('a1');
});

it('answers an approval inline and leaves the breadcrumb after the last message', async () => {
  usePromptStore.setState({ queue: [{ id: 'p1', shape: 'tool_approval', tool_name: 'write_file', tool_args: { path: 'tests/conftest.py' } }] as never });
  const ui = renderChat();
  expect(ui.getByText('Permission to edit conftest.py')).toBeTruthy();
  expect(ui.getByText('◆ Waiting for your answer above')).toBeTruthy();
  await act(async () => { fireEvent.press(ui.getByTestId('tool-approval-allow')); });
  expect(promptsApi.answer).toHaveBeenCalledWith('p1', { approved: true, remember: false });
  expect(ui.getByText('Approved')).toBeTruthy();
  expect(ui.getByText('write_file conftest.py')).toBeTruthy();
});

it('sends from the composer and shows the foot', async () => {
  const onOpenSettings = jest.fn();
  const ui = renderChat({ onOpenSettings });
  fireEvent.changeText(ui.getByTestId('composer-input'), 'thanks');
  await act(async () => { fireEvent.press(ui.getByTestId('composer-send')); });
  expect(mockSend).toHaveBeenCalledWith('thanks', []);
  fireEvent.press(ui.getByText('qwen3.8:27b'));
  expect(onOpenSettings).toHaveBeenCalled();
  expect(ui.getByLabelText('Context 30% full')).toBeTruthy();
});

it('stops a running turn and copies a reply', async () => {
  mockStreaming = true;
  const ui = renderChat();
  fireEvent.press(ui.getByTestId('composer-stop'));
  expect(mockStop).toHaveBeenCalled();
  fireEvent.press(ui.getAllByLabelText('Copy message')[0]);
  expect(Clipboard.setStringAsync).toHaveBeenCalledWith('Fixed: the fixture was stale.');
});

it('shows the default-mode turn receipt as one quiet line under the turn\'s last assistant message', () => {
  mockReceipts = [{ status: 'unverified', files: ['a.py', 'b.py'], checks: [], work_items: [], blockers: [], turn: null, at: 1 }];
  const ui = renderChat();
  expect(ui.getByTestId('turn-receipt-a1')).toBeTruthy();
  expect(ui.getByText('2 files changed · unverified')).toBeTruthy();
  expect(ui.queryByTestId('turn-receipt-a0')).toBeNull();
  fireEvent.press(ui.getByTestId('turn-receipt-a1'));
  expect(ui.getByText('a.py')).toBeTruthy();
});
