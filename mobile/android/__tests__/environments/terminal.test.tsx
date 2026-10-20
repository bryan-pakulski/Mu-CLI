// The environment terminal (mobile_parity.md §6, §7a): the socket is signed with the device token;
// a refusal for an unpaired or signed-out phone says so and offers pairing; output streams in.
import React from 'react';
import { act, fireEvent, render } from '@testing-library/react-native';

const mockNavigate = jest.fn();
let mockParams: Record<string, unknown> = {};
jest.mock('@react-navigation/native', () => ({
  useNavigation: () => ({ navigate: mockNavigate, goBack: jest.fn() }),
  useRoute: () => ({ params: mockParams }),
  useFocusEffect: () => undefined,
}));
jest.mock('../../src/api/sessions', () => ({ sessionsApi: { getContainer: jest.fn() } }));

import { resetAuthCache, saveToken } from '../../src/api/auth';
import { useConnectionStore } from '../../src/store/connection';
import { ThemeProvider } from '../../src/theme/ThemeContext';
import { TerminalScreen, describeClose, stripAnsi, trimOutput } from '../../src/screens/environments/TerminalScreen';

type Handler = ((event: any) => void) | null;
class MockSocket {
  static OPEN = 1;
  static last: MockSocket | null = null;
  readyState = 0;
  sent: string[] = [];
  onopen: Handler = null; onmessage: Handler = null; onclose: Handler = null; onerror: Handler = null;
  constructor(public url: string, public protocols: unknown, public options: { headers: Record<string, string> }) { MockSocket.last = this; }
  send(data: string) { this.sent.push(data); }
  close() { this.readyState = 3; }
  open() { this.readyState = 1; this.onopen?.({}); }
}

const BASE = 'http://192.168.1.20:30311';
const realWebSocket = global.WebSocket;
beforeEach(async () => {
  (global as any).WebSocket = MockSocket;
  MockSocket.last = null;
  mockNavigate.mockReset();
  mockParams = { container: 'mucli-research' };
  resetAuthCache();
  await saveToken(BASE, 'tok-1');
  useConnectionStore.setState({ baseUrl: BASE, pairingRequired: false, activeSessionName: null });
});
afterAll(() => { (global as any).WebSocket = realWebSocket; });

const wrap = () => render(<ThemeProvider statusBar={false}><TerminalScreen /></ThemeProvider>);

it('explains closes in words', () => {
  expect(describeClose(1008, 'Pair this device first').pairing).toBe(true);
  expect(describeClose(1008, 'This device was signed out').text).toMatch(/signed out/);
  expect(describeClose(1011).pairing).toBe(false);
  expect(stripAnsi('\u001b[32mok\u001b[0m')).toBe('ok');
  expect(trimOutput(Array.from({ length: 2500 }, (_, i) => String(i)).join('\n')).split('\n')).toHaveLength(2000);
});

it('opens a signed shell, streams output and sends commands', async () => {
  const ui = wrap();
  await act(async () => { fireEvent.press(ui.getByText('Connect')); });
  const ws = MockSocket.last!;
  expect(ws.url).toBe('ws://192.168.1.20:30311/api/containers/mucli-research/shell');
  expect(ws.options.headers.Authorization).toBe('Bearer tok-1');
  await act(async () => { ws.open(); ws.onmessage?.({ data: 'hello \u001b[1mworld\u001b[0m\n' }); });
  expect(ui.getByTestId('terminal-output').props.children).toContain('hello world');
  fireEvent.changeText(ui.getByTestId('terminal-input'), 'ls -la');
  await act(async () => { fireEvent(ui.getByTestId('terminal-input'), 'submitEditing'); });
  expect(ws.sent).toContain('ls -la\n');
});

it('a refused phone is told to pair, and the app knows it is unpaired', async () => {
  const ui = wrap();
  await act(async () => { fireEvent.press(ui.getByText('Connect')); });
  await act(async () => { MockSocket.last!.onclose?.({ code: 1008, reason: 'Pair this device first' }); });
  expect(ui.getByTestId('terminal-closed')).toBeTruthy();
  expect(useConnectionStore.getState().pairingRequired).toBe(true);
  fireEvent.press(ui.getByTestId('terminal-pair'));
  expect(mockNavigate).toHaveBeenCalledWith('Pair');
});
