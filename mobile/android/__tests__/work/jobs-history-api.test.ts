import { jobHistoryQuery } from '../../src/api/jobs';

describe('job history / management client', () => {
  it('builds history and report queries without empty filters', () => {
    expect(jobHistoryQuery({ q: '', archive: 'archived', limit: 50, offset: 0, sort: 'updated' })).toEqual({ archive: 'archived', limit: 50, offset: 0, sort: 'updated' });
  });
});
