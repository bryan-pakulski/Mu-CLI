// One shared SSE connection per (path, session) (documentation/plans/mobile_parity.md §7): the
// stream is filtered with the server's `?session=` parameter, signed with the device token,
// resumes with Last-Event-ID, is shared by subscribers and closed by the last one, and a 401
// puts the phone into the pairing state.
type Listener = (event: Record<string, unknown>) => void;

const mockSources: MockSource[] = [];
class MockSource {
  url: string;
  options: { headers: Record<string, string> };
  listeners: Record<string, Listener[]> = {};
  closed = false;
  constructor(url: string, options: { headers: Record<string, string> }) {
    this.url = url;
    this.options = options;
    mockSources.push(this);
  }
  addEventListener(type: string, fn: Listener) { (this.listeners[type] ||= []).push(fn); }
  removeAllEventListeners() { this.listeners = {}; }
  close() { this.closed = true; }
  emit(type: string, event: Record<string, unknown> = {}) { (this.listeners[type] || []).forEach(fn => fn(event)); }
}
jest.mock('react-native-sse', () => ({ __esModule: true, default: function Source(url: string, options: { headers: Record<string, string> }) { return new MockSource(url, options); } }));

import { saveToken, resetAuthCache } from '../../src/api/auth';
import { liveStreamCount, reconnectAllStreams, streamUrl, subscribeToEvents, subscribeToKind } from '../../src/api/sse';
import { useConnectionStore } from '../../src/store/connection';

const BASE = 'http://192.168.1.20:30311';

beforeEach(async () => {
  jest.useFakeTimers();
  mockSources.length = 0;
  resetAuthCache();
  await saveToken(BASE, 'tok-1');
  useConnectionStore.setState({ baseUrl: BASE, activeSessionName: 'main', pairingRequired: false });
});
afterEach(() => { jest.useRealTimers(); });

it('filters by session with the server\'s ?session= parameter', () => {
  expect(streamUrl(BASE, '/api/events', 'my chat')).toBe(`${BASE}/api/events?session=my%20chat`);
  expect(streamUrl(BASE, '/api/jobs/events', null)).toBe(`${BASE}/api/jobs/events`);
  const sub = subscribeToEvents({});
  expect(mockSources[0].url).toBe(`${BASE}/api/events?session=main`);
  sub.close();
});

it('signs the stream with the device token', () => {
  const sub = subscribeToEvents({}, { path: '/api/jobs/events', sessionName: null });
  expect(mockSources[0].options.headers.Authorization).toBe('Bearer tok-1');
  sub.close();
});

it('shares one connection per stream and closes it with the last subscriber', () => {
  const a = jest.fn();
  const b = jest.fn();
  const subA = subscribeToKind('job_changed', a, { path: '/api/jobs/events', sessionName: null });
  const subB = subscribeToKind('job_changed', b, { path: '/api/jobs/events', sessionName: null });
  const other = subscribeToEvents({}, { path: '/api/orchestration/events', sessionName: null });
  expect(mockSources).toHaveLength(2);
  expect(liveStreamCount()).toBe(2);
  mockSources[0].emit('message', { data: JSON.stringify({ kind: 'job_changed', id: 'j1' }), lastEventId: '7' });
  expect(a).toHaveBeenCalledWith(expect.objectContaining({ id: 'j1' }));
  expect(b).toHaveBeenCalledTimes(1);
  subA.close();
  expect(mockSources[0].closed).toBe(false);
  subB.close();
  expect(mockSources[0].closed).toBe(true);
  other.close();
  expect(liveStreamCount()).toBe(0);
});

it('resumes from the last event id after an error, with backoff', () => {
  const onError = jest.fn();
  const sub = subscribeToEvents({ onError }, { sessionName: null });
  mockSources[0].emit('open');
  mockSources[0].emit('message', { data: JSON.stringify({ kind: 'turn_done' }), lastEventId: '42' });
  mockSources[0].emit('error', { message: 'dropped' });
  expect(onError).toHaveBeenCalled();
  expect(mockSources).toHaveLength(1);
  jest.advanceTimersByTime(3_000);
  expect(mockSources).toHaveLength(2);
  expect(mockSources[1].options.headers['Last-Event-ID']).toBe('42');
  sub.close();
});

it('a 401 puts the phone into the pairing state', () => {
  const sub = subscribeToEvents({}, { sessionName: null });
  mockSources[0].emit('error', { xhrStatus: 401, message: 'Unauthorized' });
  expect(useConnectionStore.getState().pairingRequired).toBe(true);
  sub.close();
});

it('reconnects every stream at once after pairing', () => {
  const sub = subscribeToEvents({}, { sessionName: null });
  reconnectAllStreams();
  expect(mockSources).toHaveLength(2);
  expect(mockSources[0].closed).toBe(true);
  sub.close();
});

it('reports malformed payloads instead of dropping them silently', () => {
  const onError = jest.fn();
  const onMessage = jest.fn();
  const sub = subscribeToEvents({ onError, onMessage }, { sessionName: null });
  mockSources[0].emit('message', { data: '{not json' });
  mockSources[0].emit('message', { data: JSON.stringify({ nokind: true }) });
  expect(onError).toHaveBeenCalledTimes(2);
  expect(onMessage).not.toHaveBeenCalled();
  sub.close();
});
