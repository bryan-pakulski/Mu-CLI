jest.mock('../../store/connection', () => ({
  useConnectionStore: {
    getState: () => ({
      baseUrl: 'http://mucli.test',
      activeSessionName: null,
    }),
  },
}));

import { api, ApiError } from '../client';

describe('mobile API timeout', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    global.fetch = jest.fn((_url, init) => new Promise((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => {
        const error = new Error('Aborted');
        error.name = 'AbortError';
        reject(error);
      });
    })) as jest.Mock;
  });

  afterEach(() => {
    jest.useRealTimers();
    jest.restoreAllMocks();
  });

  it('rejects a stalled request instead of remaining pending forever', async () => {
    const pending = api.get('/healthz', { timeoutMs: 25 });
    jest.advanceTimersByTime(25);
    await expect(pending).rejects.toEqual(
      expect.objectContaining({ name: 'ApiError', status: 0 }),
    );
    await expect(pending).rejects.toBeInstanceOf(ApiError);
  });
});
