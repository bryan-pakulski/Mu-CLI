/**
 * A model/provider switch reaches the server as POST /api/providers/switch —
 * the only method the route accepts (a PUT answered 405 Method Not Allowed).
 * Covers the live switch and a switch queued offline and replayed later.
 */
jest.mock('@react-native-async-storage/async-storage', () => {
  let store: Record<string, string> = {};
  return {
    getItem: jest.fn((key: string) => Promise.resolve(store[key] ?? null)),
    setItem: jest.fn((key: string, value: string) => { store[key] = value; return Promise.resolve(); }),
    removeItem: jest.fn((key: string) => { delete store[key]; return Promise.resolve(); }),
  };
});

import { providersApi } from '../../src/api/providers';
import { enqueueOffline, replayQueue, clearQueue } from '../../src/api/offlineQueue';
import { useConnectionStore } from '../../src/store/connection';

const fetchMock = jest.fn();
global.fetch = fetchMock as unknown as typeof fetch;

function lastSwitchRequest(): { url: string; method: string; body: Record<string, unknown> } {
  const call = fetchMock.mock.calls.find(([url]) => String(url).includes('/api/providers/switch'));
  expect(call).toBeDefined();
  const [url, init] = call as [string, RequestInit];
  return { url, method: String(init.method), body: JSON.parse(String(init.body)) };
}

beforeEach(async () => {
  fetchMock.mockReset();
  fetchMock.mockResolvedValue({
    ok: true,
    status: 200,
    headers: { get: () => 'application/json' },
    json: () => Promise.resolve({ ok: true }),
  });
  useConnectionStore.getState().setBaseUrl('http://test:8000');
  await clearQueue();
});

it('sends a live switch as POST with the provider and model', async () => {
  await providersApi.switch('anthropic', 'claude-opus-5-5');
  const request = lastSwitchRequest();
  expect(request.method).toBe('POST');
  expect(request.body).toMatchObject({ provider: 'anthropic', model: 'claude-opus-5-5' });
});

it('replays a queued offline switch as POST', async () => {
  await enqueueOffline('provider_switch', { provider: 'openai', model: 'gpt-6' });
  const outcome = await replayQueue();
  expect(outcome.applied).toBe(1);
  const request = lastSwitchRequest();
  expect(request.method).toBe('POST');
  expect(request.body).toMatchObject({ provider: 'openai', model: 'gpt-6' });
});
