import { jobsApi } from '../src/api/jobs';
import { useConnectionStore } from '../src/store/connection';

global.fetch = jest.fn();

function jsonResponse(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => 'application/json' },
    json: () => Promise.resolve(body),
  };
}

describe('jobsApi.delete', () => {
  beforeEach(() => {
    (fetch as jest.Mock).mockReset();
    useConnectionStore.getState().setBaseUrl('http://test:8000');
    useConnectionStore.getState().setActiveSession('main');
  });

  it('issues a forced DELETE by default (cancel -> archive -> purge)', async () => {
    (fetch as jest.Mock).mockResolvedValueOnce(
      jsonResponse({ job_id: 'abc', deleted: true, forced: true, worktree_removed: false, artifacts_removed: [], warnings: [], branch_preserved: 'mu/job-abc' }),
    );
    const result = await jobsApi.delete('abc');
    expect(result.deleted).toBe(true);
    expect(result.branch_preserved).toBe('mu/job-abc');
    const [url, init] = (fetch as jest.Mock).mock.calls[0];
    expect(String(url)).toContain('/api/jobs/abc?force=1');
    expect(init.method).toBe('DELETE');
  });

  it('can opt out of force', async () => {
    (fetch as jest.Mock).mockResolvedValueOnce(jsonResponse({ job_id: 'abc', deleted: true }));
    await jobsApi.delete('abc', false);
    const [url] = (fetch as jest.Mock).mock.calls[0];
    expect(String(url)).toContain('/api/jobs/abc?force=0');
  });
});
