// Sessions and threads in one drawer (mobile_parity.md §6 Chat): signs for running / needs you /
// failed, unread counts, opening loads or focuses, and a thread is never deletable while it is the
// open one or still running.
import React from 'react';
import { Alert } from 'react-native';
import { act, fireEvent, render, waitFor } from '@testing-library/react-native';

jest.mock('../../src/api/sessions', () => ({
  ...jest.requireActual('../../src/api/sessions'),
  sessionsApi: { list: jest.fn(), load: jest.fn(), focus: jest.fn(), unload: jest.fn(), delete: jest.fn() },
}));
jest.mock('../../src/api/threads', () => ({ threadsApi: { list: jest.fn(), remove: jest.fn() } }));
jest.mock('../../src/components/NewSessionSheet', () => ({ NewSessionSheet: () => null }));
jest.mock('../../src/components/NewThreadSheet', () => ({ NewThreadSheet: () => null }));

import { sessionsApi } from '../../src/api/sessions';
import { threadsApi } from '../../src/api/threads';
import { SessionsDrawer, canDeleteThread, sessionSign, threadSign } from '../../src/components/chat/SessionsDrawer';
import { useConnectionStore } from '../../src/store/connection';
import { usePromptStore } from '../../src/store/prompts';
import { ThemeProvider } from '../../src/theme/ThemeContext';

const sessions = sessionsApi as unknown as Record<string, jest.Mock>;
const threads = threadsApi as unknown as Record<string, jest.Mock>;
const thread = (id: string, session_name: string, status: string, unread = 0) => ({
  thread_id: id, session_name, title: session_name, status, current_goal: '', run_origin: '', runtime_id: '', last_seen: 0, created_at: 0, updated_at: 0, unread_count: unread, claimed_paths: [],
});

beforeEach(() => {
  jest.clearAllMocks();
  useConnectionStore.setState({ activeSessionName: 'main', isConnected: true } as never);
  usePromptStore.setState({ queue: [{ id: 'p', shape: 'tool_approval' }] as never });
  sessions.list.mockResolvedValue({ current: 'main', active: true, loaded: ['main'], busy: ['build'], sessions: [
    { name: 'main', is_current: true, is_loaded: true, is_busy: false, modified_at: '', modified_unix: 0, session_type: 'workspace' },
    { name: 'build', is_current: false, is_loaded: true, is_busy: true, modified_at: '', modified_unix: 0, session_type: 'container' },
    { name: 'old', is_current: false, is_loaded: false, is_busy: false, modified_at: '', modified_unix: 0 },
  ] });
  threads.list.mockResolvedValue({ session_name: 'main', thread_group_id: 'g', current_thread_id: 't1', threads: [
    thread('t1', 'main', 'idle'), thread('t2', 'main-review', 'awaiting_approval', 3), thread('t3', 'main-docs', 'idle'),
  ] });
  sessions.load.mockResolvedValue({});
});

it('maps states to signs and guards thread deletion', () => {
  expect(threadSign('awaiting_approval' as never)).toBe('waiting');
  expect(threadSign('running' as never)).toBe('active');
  expect(threadSign('error' as never)).toBe('failed');
  expect(sessionSign({ is_busy: false, is_loaded: true } as never, true, true)).toBe('waiting');
  expect(sessionSign({ is_busy: true, is_loaded: true } as never, false, false)).toBe('active');
  expect(canDeleteThread(thread('t1', 'main', 'idle') as never, 'main')).toBe(false);
  expect(canDeleteThread(thread('t2', 'x', 'running') as never, 'main')).toBe(false);
  expect(canDeleteThread(thread('t3', 'x', 'idle') as never, 'main')).toBe(true);
});

it('lists threads and sessions, and opens an unloaded session by loading it', async () => {
  const onClose = jest.fn();
  const ui = render(<ThemeProvider statusBar={false}><SessionsDrawer visible onClose={onClose} /></ThemeProvider>);
  await waitFor(() => expect(ui.getByTestId('drawer-threads')).toBeTruthy());
  expect(ui.getByText('3 new')).toBeTruthy();
  expect(ui.getByText('workspace · needs you')).toBeTruthy();
  expect(ui.getByText('container · working')).toBeTruthy();
  await act(async () => { fireEvent.press(ui.getByText('old')); });
  expect(sessions.load).toHaveBeenCalledWith('old', undefined, undefined, { timeoutMs: 30_000 });
  expect(useConnectionStore.getState().activeSessionName).toBe('old');
  expect(onClose).toHaveBeenCalled();
});

it('offers delete for an idle thread only, behind a confirmation', async () => {
  const alert = jest.spyOn(Alert, 'alert').mockImplementation(() => undefined);
  const ui = render(<ThemeProvider statusBar={false}><SessionsDrawer visible onClose={jest.fn()} /></ThemeProvider>);
  await waitFor(() => expect(ui.getByTestId('thread-t1')).toBeTruthy());
  fireEvent(ui.getByText('main-review'), 'longPress');
  expect(ui.getByText('The thread is still running')).toBeTruthy();
  fireEvent.press(ui.getByText('Delete thread'));
  expect(alert).not.toHaveBeenCalled();
  alert.mockRestore();
});

it('deletes an idle thread after confirming', async () => {
  const alert = jest.spyOn(Alert, 'alert').mockImplementation(() => undefined);
  const ui = render(<ThemeProvider statusBar={false}><SessionsDrawer visible onClose={jest.fn()} /></ThemeProvider>);
  await waitFor(() => expect(ui.getByTestId('thread-t3')).toBeTruthy());
  fireEvent(ui.getByText('main-docs'), 'longPress');
  fireEvent.press(ui.getByText('Delete thread'));
  expect(alert).toHaveBeenCalledWith('Delete thread?', expect.stringContaining('main-docs'), expect.any(Array));
  const confirm = (alert.mock.calls[0][2] as Array<{ text: string; onPress?: () => Promise<void> }>).find(b => b.text === 'Delete');
  threads.remove.mockResolvedValue({});
  sessions.unload.mockResolvedValue({});
  await act(async () => { await confirm?.onPress?.(); });
  expect(threads.remove).toHaveBeenCalledWith('t3', 'main', { timeoutMs: 12_000 });
  alert.mockRestore();
});

it('marks every session the server says is awaiting input, not only the open one', async () => {
  usePromptStore.setState({ queue: [] as never });
  sessions.list.mockResolvedValueOnce({ current: 'main', active: true, loaded: ['main', 'build'], busy: [], awaiting_input: ['build'], sessions: [
    { name: 'main', is_current: true, is_loaded: true, is_busy: false, awaiting_input: false, modified_at: '', modified_unix: 0, session_type: 'workspace' },
    { name: 'build', is_current: false, is_loaded: true, is_busy: true, awaiting_input: true, modified_at: '', modified_unix: 0, session_type: 'container' },
  ] });
  const ui = render(<ThemeProvider statusBar={false}><SessionsDrawer visible onClose={jest.fn()} /></ThemeProvider>);
  await waitFor(() => expect(ui.getByText('container · needs you')).toBeTruthy());
  expect(ui.getByText('workspace · loaded')).toBeTruthy();
  expect(sessionSign({ is_busy: true, is_loaded: true, awaiting_input: true } as never, false, false)).toBe('waiting');
});
