// Approvals and questions answered inline (mobile_parity.md §6 Chat): the queue comes from
// /api/prompts and the session stream; the inline card shows n of m and posts the web payload; an
// answer leaves a resolved breadcrumb; a failed answer keeps the card; auto-approve answers tool
// approvals itself; off the chat a "needs you" banner opens the chat.
import React from 'react';
import { act, fireEvent, render, waitFor } from '@testing-library/react-native';

const mockHandlers: Array<{ onMessage?: (e: Record<string, unknown>) => void; onOpen?: () => void }> = [];
jest.mock('../../src/api/sse', () => ({
  subscribeToEvents: (handlers: { onMessage?: (e: Record<string, unknown>) => void }) => { mockHandlers.push(handlers); return { close: jest.fn() }; },
}));
jest.mock('../../src/api/prompts', () => ({
  promptsApi: { listPending: jest.fn(), answer: jest.fn(), cancel: jest.fn() },
}));
const mockNavigate = jest.fn();
jest.mock('../../src/navigation/nav', () => ({ navigateTo: (...args: unknown[]) => mockNavigate(...args) }));

import { promptsApi } from '../../src/api/prompts';
import { PromptHost } from '../../src/components/PromptHost';
import { InlinePrompt, ResolvedLine } from '../../src/components/chat/InlinePrompt';
import { useConnectionStore } from '../../src/store/connection';
import { useActivePrompt, usePromptStore } from '../../src/store/prompts';
import { ThemeProvider } from '../../src/theme/ThemeContext';

const api = promptsApi as unknown as Record<string, jest.Mock>;
const write = { id: 'p1', shape: 'tool_approval', tool_name: 'write_file', tool_args: { path: 'src/app.py' }, session_name: 's1' };
const ask = { id: 'p2', shape: 'input', message: 'Which branch?', session_name: 's1' };

function Chat() {
  const { prompt, index, total } = useActivePrompt();
  const s = usePromptStore();
  return (
    <>
      {prompt ? <InlinePrompt prompt={prompt} index={index} total={total} submitting={s.submitting} error={s.error}
        onAnswer={(p, payload) => { void s.answer(p, payload); }} onCancel={p => { void s.cancel(p); }} onIndex={s.setIndex} /> : null}
      {(s.resolved.s1 || []).map(r => <ResolvedLine key={r.id} record={r} />)}
    </>
  );
}

const wrap = (node: React.ReactElement) => render(<ThemeProvider statusBar={false}>{node}</ThemeProvider>);

beforeEach(() => {
  jest.clearAllMocks();
  mockHandlers.length = 0;
  usePromptStore.setState({ session: null, queue: [], index: 0, submitting: false, error: null, resolved: {}, chatVisible: true, lastMessageId: null });
  useConnectionStore.setState({ isConnected: true, activeSessionName: 's1', yolo: false } as never);
  api.listPending.mockResolvedValue({ pending: [write, { ...ask, session_name: 'other' }] });
  api.answer.mockResolvedValue({ ok: true });
  api.cancel.mockResolvedValue({ ok: true });
});

it('recovers this session\'s pending prompts and follows the stream', async () => {
  const ui = wrap(<><PromptHost /><Chat /></>);
  await waitFor(() => expect(ui.getByText('Permission to edit app.py')).toBeTruthy());
  expect(usePromptStore.getState().queue.map(p => p.id)).toEqual(['p1']);
  act(() => mockHandlers[0].onMessage?.({ kind: 'prompt', id: 'p2', prompt: ask }));
  expect(ui.getByText('1 of 2')).toBeTruthy();
  fireEvent.press(ui.getByLabelText('Next request'));
  expect(ui.getByText('Which branch?')).toBeTruthy();
  act(() => mockHandlers[0].onMessage?.({ kind: 'prompt_resolved', id: 'p2' }));
  expect(ui.getByText('Permission to edit app.py')).toBeTruthy();
  expect(ui.queryByText('1 of 2')).toBeNull();
});

it('approves with the web payload and leaves an Approved breadcrumb', async () => {
  const ui = wrap(<><PromptHost /><Chat /></>);
  await waitFor(() => expect(ui.getByTestId('tool-approval-allow')).toBeTruthy());
  fireEvent.press(ui.getByTestId('tool-approval-remember'));
  await act(async () => { fireEvent.press(ui.getByTestId('tool-approval-allow')); });
  expect(api.answer).toHaveBeenCalledWith('p1', { approved: true, remember: true });
  expect(ui.queryByTestId('inline-prompt')).toBeNull();
  expect(ui.getByText('Approved')).toBeTruthy();
  expect(ui.getByText('write_file app.py')).toBeTruthy();
});

it('cancelling records Denied; a failed answer keeps the card with the reason', async () => {
  const ui = wrap(<><PromptHost /><Chat /></>);
  await waitFor(() => expect(ui.getByTestId('tool-approval-allow')).toBeTruthy());
  api.answer.mockRejectedValueOnce(new Error('offline'));
  await act(async () => { fireEvent.press(ui.getByTestId('tool-approval-allow')); });
  expect(ui.getByTestId('inline-prompt')).toBeTruthy();
  expect(ui.getByTestId('inline-prompt-error')).toBeTruthy();
  await act(async () => { fireEvent.press(ui.getByText('Cancel')); });
  expect(api.cancel).toHaveBeenCalledWith('p1');
  expect(ui.getByText('Denied')).toBeTruthy();
});

it('answers a question with its text', async () => {
  api.listPending.mockResolvedValue({ pending: [ask] });
  const ui = wrap(<><PromptHost /><Chat /></>);
  await waitFor(() => expect(ui.getByTestId('prompt-input')).toBeTruthy());
  expect(ui.getByTestId('prompt-submit').props.accessibilityState).toMatchObject({ disabled: true });
  fireEvent.changeText(ui.getByTestId('prompt-input'), 'main');
  await act(async () => { fireEvent.press(ui.getByTestId('prompt-submit')); });
  expect(api.answer).toHaveBeenCalledWith('p2', { value: 'main' });
  expect(ui.getByText('Answered')).toBeTruthy();
});

it('auto-approve answers tool approvals and marks them auto', async () => {
  useConnectionStore.setState({ yolo: true } as never);
  const ui = wrap(<><PromptHost /><Chat /></>);
  await waitFor(() => expect(api.answer).toHaveBeenCalledWith('p1', { approved: true, remember: false }));
  await waitFor(() => expect(ui.getByText('Auto-approved')).toBeTruthy());
  expect(ui.getByText('auto')).toBeTruthy();
});

it('records server-side auto-approvals from the stream', async () => {
  api.listPending.mockResolvedValue({ pending: [] });
  const ui = wrap(<><PromptHost /><Chat /></>);
  await waitFor(() => expect(mockHandlers.length).toBe(1));
  act(() => mockHandlers[0].onMessage?.({ kind: 'prompt_resolved', id: 'p9', auto: true, tool_name: 'bash', target: 'pytest' }));
  expect(ui.getByText('Auto-approved')).toBeTruthy();
  expect(ui.getByText('bash pytest')).toBeTruthy();
});

it('off the chat, a needs-you banner opens the chat', async () => {
  usePromptStore.setState({ chatVisible: false });
  const ui = wrap(<PromptHost />);
  await waitFor(() => expect(ui.getByTestId('needs-you-banner')).toBeTruthy());
  fireEvent.press(ui.getByTestId('needs-you-banner'));
  expect(mockNavigate).toHaveBeenCalledWith('Chat');
  act(() => usePromptStore.getState().setChatVisible(true));
  expect(ui.queryByTestId('needs-you-banner')).toBeNull();
});

it('PoC approvals remain on desktop even with auto-approve enabled', async () => {
  useConnectionStore.setState({ yolo: true });
  api.listPending.mockResolvedValue({ pending: [{ ...write, tool_name: 'verify_security_proof', approval_policy: 'always_human', requires_desktop: true }] });
  const ui = wrap(<><PromptHost /><Chat /></>);
  await waitFor(() => expect(ui.getByText('Review this PoC on desktop')).toBeTruthy());
  expect(ui.queryByTestId('tool-approval-allow')).toBeNull();
  expect(api.answer).not.toHaveBeenCalled();
  await act(async () => { fireEvent.press(ui.getByText('Cancel')); });
  expect(api.cancel).toHaveBeenCalledWith('p1');
});
