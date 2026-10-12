import { compactStageModels, JOB_STAGE_NAMES, jobsApi } from '../src/api/jobs';
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

describe('compactStageModels', () => {
  it('lists stages in pipeline order', () => {
    expect(JOB_STAGE_NAMES).toEqual(['plan', 'implement', 'review']);
  });

  it('drops blank stages and trims values', () => {
    expect(
      compactStageModels({
        plan: { provider: ' anthropic ', model: ' opus ' },
        implement: { provider: '', model: '   ' },
        review: { model: 'sonnet' },
      }),
    ).toEqual({
      plan: { provider: 'anthropic', model: 'opus' },
      review: { model: 'sonnet' },
    });
  });

  it('returns an empty object when nothing is filled', () => {
    expect(compactStageModels({})).toEqual({});
  });
});

describe('jobsApi.create with stages', () => {
  beforeEach(() => {
    (fetch as jest.Mock).mockReset();
    useConnectionStore.getState().setBaseUrl('http://test:8000');
    useConnectionStore.getState().setActiveSession('main');
  });

  it('sends execution.stages in the request body', async () => {
    (fetch as jest.Mock).mockResolvedValueOnce(jsonResponse({ job: { id: 'j1', title: 't' } }));
    await jobsApi.create({
      title: 't',
      execution: { stages: { plan: { provider: 'anthropic', model: 'opus' } } },
    });
    const [url, init] = (fetch as jest.Mock).mock.calls[0];
    expect(String(url)).toContain('/api/jobs');
    const body = JSON.parse(init.body);
    expect(body.execution.stages).toEqual({ plan: { provider: 'anthropic', model: 'opus' } });
  });
});
