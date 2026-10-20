// The fleet chat on the phone (Orchestration → Ask), on the main chat's transport: the session opens
// through POST /api/orchestration/chat/session (asking for a model once when there's none to borrow),
// history loads, messages go through /api/chat/send, the session's stream fills the transcript with
// streamed text and tool cards, "Working…" shows while it runs, prompts are answered inline and a new
// conversation starts over. The discovery chat opens its draft's session the same way.
import React from 'react';
import { act, fireEvent, render, waitFor } from '@testing-library/react-native';
import { ThemeProvider } from '../../src/theme/ThemeContext';

let streamHandler: ((ev: Record<string, unknown>) => void) | null = null;
let streamSession: unknown = null;
jest.mock('../../src/api/sse', () => ({
  ...jest.requireActual('../../src/api/sse'),
  subscribeToEvents: jest.fn((handlers: { onMessage: (ev: Record<string, unknown>) => void }, opts: { sessionName?: unknown }) => {
    streamHandler = handlers.onMessage; streamSession = opts?.sessionName; return { close: jest.fn() };
  }),
}));
jest.mock('../../src/api/orchestration', () => ({
  ...jest.requireActual('../../src/api/orchestration'),
  orchestrationApi: { chatSession: jest.fn(), studioSession: jest.fn() },
}));
jest.mock('../../src/api/sessions', () => ({ sessionsApi: { getHistory: jest.fn() } }));
jest.mock('../../src/api/prompts', () => ({ promptsApi: { listPending: jest.fn(), answer: jest.fn(), cancel: jest.fn() } }));
jest.mock('../../src/api/chat', () => ({ chatApi: { send: jest.fn(), interrupt: jest.fn() } }));
jest.mock('../../src/api/providers', () => ({ providersApi: { list: jest.fn(), listModels: jest.fn() } }));

import { ApiError } from '../../src/api/client';
import { chatApi } from '../../src/api/chat';
import { orchestrationApi } from '../../src/api/orchestration';
import { promptsApi } from '../../src/api/prompts';
import { providersApi } from '../../src/api/providers';
import { sessionsApi } from '../../src/api/sessions';
import { DiscoveryChat, FleetChat, applyEvent, historyEntries } from '../../src/components/orchestration/OrchChatSheet';

const orch = orchestrationApi as unknown as Record<string, jest.Mock>;
const wrap = (node: React.ReactElement) => render(<ThemeProvider statusBar={false}>{node}</ThemeProvider>);
const emit = async (ev: Record<string, unknown>) => { await act(async () => { streamHandler?.(ev); }); };

beforeEach(() => {
  jest.clearAllMocks();
  streamHandler = null;
  orch.chatSession.mockResolvedValue({ session_name: 'fleet-chat-20261020-120000', model: 'claude-sonnet-5-5', busy: false });
  (sessionsApi.getHistory as jest.Mock).mockResolvedValue({ name: 'x', turns: [] });
  (promptsApi.listPending as jest.Mock).mockResolvedValue({ pending: [] });
  (chatApi.send as jest.Mock).mockResolvedValue({});
  (promptsApi.answer as jest.Mock).mockResolvedValue({});
});

it('folds history and stream events into one transcript', () => {
  const h = historyEntries([
    { role: 'user', parts: [{ type: 'text', text: 'disk?' }] },
    { role: 'assistant', parts: [{ type: 'tool_call', tool_name: 'fleet_overview', tool_args: {} }, { type: 'tool_result', tool_name: 'fleet_overview', preview: 'ok' }, { type: 'text', text: 'All fine.' }] },
  ]);
  expect(h.map(e => e.type)).toEqual(['user', 'tools', 'assistant']);
  let list = applyEvent([], { kind: 'assistant_delta', text: 'Hel' }, '');
  list = applyEvent(list, { kind: 'assistant_delta', text: 'lo' }, '');
  expect(list).toEqual([expect.objectContaining({ type: 'assistant', text: 'Hello', streaming: true })]);
  list = applyEvent(list, { kind: 'tool_call', tool_name: 'fleet_results', tool_args: { tool: 'sys.disk' } }, '');
  expect(list[0]).toMatchObject({ streaming: false });
  expect(applyEvent(list, { kind: 'user_message', text: 'mine' }, 'mine')).toHaveLength(2);
});

it('opens the fleet session, sends on the chat transport and streams the answer with tool cards', async () => {
  const ui = wrap(<FleetChat visible onClose={jest.fn()} />);
  expect(await ui.findByText('Ask about your fleet')).toBeTruthy();
  expect(orch.chatSession).toHaveBeenCalledWith({});
  expect(streamSession).toBe('fleet-chat-20261020-120000');
  fireEvent.changeText(ui.getByTestId('orch-fleet-chat-composer-input'), 'Which nodes need attention?');
  await act(async () => { fireEvent.press(ui.getByTestId('orch-fleet-chat-composer-send')); });
  expect(chatApi.send).toHaveBeenCalledWith('Which nodes need attention?', 'fleet-chat-20261020-120000');
  expect(ui.getByTestId('orch-fleet-chat-working')).toBeTruthy();
  await emit({ kind: 'user_message', text: 'Which nodes need attention?' });
  await emit({ kind: 'tool_call', tool_name: 'fleet_overview', tool_args: {} });
  expect(ui.getByText('Working…')).toBeTruthy();
  await emit({ kind: 'tool_result', tool_name: 'fleet_overview', text: '3 nodes' });
  await emit({ kind: 'assistant_delta', text: 'web-2 is ' });
  await emit({ kind: 'assistant_delta', text: 'nearly full.' });
  await emit({ kind: 'turn_complete' });
  expect(ui.getAllByTestId('orch-fleet-chat-user')).toHaveLength(1);
  expect(ui.getByTestId('orch-fleet-chat-tools')).toBeTruthy();
  expect(ui.getByText(/web-2 is nearly full\./)).toBeTruthy();
  expect(ui.queryByTestId('orch-fleet-chat-working')).toBeNull();
});

it('asks for a model once when there is none to borrow, then opens with it', async () => {
  orch.chatSession.mockRejectedValueOnce(new ApiError(400, 'choose a provider and model for the fleet chat'));
  (providersApi.list as jest.Mock).mockResolvedValue({ providers: [{ name: 'anthropic', configured: true, requires: '' }, { name: 'openai', configured: false, requires: 'OPENAI_API_KEY' }] });
  (providersApi.listModels as jest.Mock).mockResolvedValue({ models: ['claude-sonnet-5-5', 'claude-opus-5-5'] });
  const ui = wrap(<FleetChat visible onClose={jest.fn()} />);
  await waitFor(() => expect(ui.getByTestId('orch-chat-model-ok').props.accessibilityState).toMatchObject({ disabled: false }));
  await act(async () => { fireEvent.press(ui.getByTestId('orch-chat-model-ok')); });
  expect(orch.chatSession).toHaveBeenLastCalledWith({ provider: 'anthropic', model: 'claude-sonnet-5-5' });
  expect(await ui.findByText('Ask about your fleet')).toBeTruthy();
});

it('answers a prompt inline and leaves a resolved breadcrumb', async () => {
  const ui = wrap(<FleetChat visible onClose={jest.fn()} />);
  await ui.findByText('Ask about your fleet');
  await emit({ kind: 'prompt', id: 'p1', prompt: { shape: 'confirm', question: 'Propose a restart of web-1?', session_name: 'fleet-chat-20261020-120000' } });
  expect(ui.getByText('Propose a restart of web-1?')).toBeTruthy();
  await act(async () => { fireEvent.press(ui.getByText('Yes')); });
  expect(promptsApi.answer).toHaveBeenCalledWith('p1', expect.objectContaining({ value: true }));
  expect(ui.getByTestId(/^resolved-p1/)).toBeTruthy();
});

it('starts a new conversation (the old one is deleted on the server)', async () => {
  const ui = wrap(<FleetChat visible onClose={jest.fn()} />);
  await ui.findByText('Ask about your fleet');
  await act(async () => { fireEvent.press(ui.getByTestId('orch-fleet-chat-new')); });
  expect(orch.chatSession).toHaveBeenLastCalledWith({ fresh: true });
});

it('opens a draft’s discovery chat with its ref', async () => {
  orch.studioSession.mockResolvedValue({ session_name: 'studio-demo-rotate', model: 'm' });
  const ui = wrap(<DiscoveryChat visible onClose={jest.fn()} draftRef="demo.rotate@1" />);
  expect(await ui.findByText('Design a tool together')).toBeTruthy();
  expect(orch.studioSession).toHaveBeenCalledWith({ ref: 'demo.rotate@1', provider: undefined, model: undefined });
  expect(streamSession).toBe('studio-demo-rotate');
});

it('opens with a results question in the composer, not sent', async () => {
  const ui = wrap(<FleetChat visible onClose={jest.fn()} prefill="Look at the fleet results of run r1 (sys.disk@1, Summary view) with fleet_results and tell me what needs attention." />);
  await waitFor(() => expect(orch.chatSession).toHaveBeenCalled());
  expect(ui.getByTestId('orch-fleet-chat-composer-input').props.value).toContain('fleet results of run r1');
  expect(chatApi.send).not.toHaveBeenCalled();
});
