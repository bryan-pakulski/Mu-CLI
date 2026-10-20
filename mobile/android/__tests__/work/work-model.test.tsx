// Work's words and the pieces around the board: the state sentence, decisive actions, the
// shell command, the Work badge, and History's delete through the job command.
import React from 'react';
import { fireEvent, render, waitFor } from '@testing-library/react-native';
import type { HomeVM, JobSummary } from '../../src/api/jobCore';
import { jobCoreApi } from '../../src/api/jobCore';
import { jobsApi } from '../../src/api/jobs';
import { decisiveAction, needsSentence, nowBoard, spendByDay, workStateParts } from '../../src/components/workModel';
import { shellCommand, shellQuote } from '../../src/components/jobViews';
import { refreshWorkBadge, useWorkBadge } from '../../src/store/workBadge';
import { HistoryPanel, reportSentence } from '../../src/screens/work/HistoryPanel';
import { ToastHost } from '../../src/ui';

jest.mock('../../src/api/jobCore', () => ({ ...jest.requireActual('../../src/api/jobCore'), jobCoreApi: { command: jest.fn() } }));
jest.mock('../../src/api/jobs', () => ({ ...jest.requireActual('../../src/api/jobs'), jobsApi: { history: jest.fn(), report: jest.fn(), archive: jest.fn(), restore: jest.fn() } }));

const NOW = 1_800_000_000;
const A = (id: string, extra: Record<string, unknown> = {}) => ({ id, label: id, enabled: true, reason: '', primary: false, danger: false, ...extra });
const job = (id: string, extra: Partial<JobSummary> = {}) => ({
  id, key: id.toUpperCase(), title: `Job ${id}`, state: 'implementing', verdict: '', mini: '', attention_detail: '', repository: '/src/acme/api',
  cost_usd: 0, actions: [], created_at: NOW - 3600, updated_at: NOW - 60, steps: [], ...extra,
}) as JobSummary;

describe('the state sentence', () => {
  it('says what needs you, what runs and what today cost, leaving zeros out', () => {
    const home = { needs_you: [job('a')], running: [job('b')], jobs: [job('a', { cost_usd: 1.25 }), job('b', { cost_usd: 0.5 })], counts: { needs_you: 1, total: 2 } } as HomeVM;
    expect(workStateParts({ agents: 2, checks: 1, needs_you: 1, engine: 'up' }, home, NOW).map(p => p.text)).toEqual(['1 needs you', '2 running', '1 check', '$1.75 today']);
    const quiet = { ...home, jobs: home.jobs.map(j => ({ ...j, updated_at: NOW - 5 * 86400 })) };
    expect(workStateParts({ agents: 0, checks: 0, needs_you: 0, engine: 'down' }, quiet, NOW).map(p => p.text)).toEqual(['container engine down']);
    expect(workStateParts(null, { needs_you: [], running: [], jobs: [], counts: { needs_you: 0, total: 0 } }, NOW).map(p => p.text)).toEqual(['No jobs yet']);
  });
});

describe('the Now board', () => {
  it('pins the action each waiting job asks for: the server’s primary, else by state', () => {
    expect(decisiveAction(job('a', { state: 'plan_review', actions: [A('discard'), A('approvePlan')] } as never))?.id).toBe('approvePlan');
    expect(decisiveAction(job('a', { state: 'needs_input', primary_action: 'raiseLimit', actions: [A('resume'), A('raiseLimit')] } as never))?.id).toBe('raiseLimit');
    expect(decisiveAction(job('a', { state: 'failed', actions: [A('retry', { enabled: false })] } as never))).toBeUndefined();
  });

  it('says why a job waits and groups needs-you oldest first, then running and recent', () => {
    expect(needsSentence(job('a', { state: 'failed', failed_from: 'checking', attention_detail: 'pytest failed' }))).toBe('Failed in checking · pytest failed · api');
    const home = {
      needs_you: [job('new', { updated_at: NOW }), job('old', { updated_at: NOW - 999 })], running: [job('run')],
      jobs: [job('new'), job('old'), job('run'), job('done', { state: 'done', updated_at: NOW - 5 })], counts: { needs_you: 2, total: 4 },
    } as HomeVM;
    const board = nowBoard(home);
    expect(board.needs.map(j => j.id)).toEqual(['old', 'new']);
    expect(board.finished.map(j => j.id)).toEqual(['done']);
  });

  it('buckets cost by the day each job was last active', () => {
    const spend = spendByDay([job('a', { cost_usd: 2, updated_at: NOW }), job('b', { cost_usd: 1, updated_at: NOW - 10 * 86400 })], NOW);
    expect(spend.today).toBe(2);
    expect(spend.week).toBe(2);
    expect(spend.buckets.reduce((a, b) => a + b, 0)).toBe(3);
  });
});

it('Open a shell is a quoted cd to paste on the computer', () => {
  expect(shellQuote("it's")).toBe(`'it'\\''s'`);
  expect(shellCommand({ worktree: '/w/my tree' })).toBe(`cd '/w/my tree'`);
});

describe('the Work badge', () => {
  it('counts the jobs that need you and keeps the last count when offline', async () => {
    await refreshWorkBadge(() => Promise.resolve({ count: 2, jobs: [{ title: 'Fix login' }, { title: 'Docs' }] }));
    expect(useWorkBadge.getState().count).toBe(2);
    expect(useWorkBadge.getState().summary).toBe('2 jobs need you: Fix login');
    await refreshWorkBadge(() => Promise.reject(new Error('offline')));
    expect(useWorkBadge.getState().count).toBe(2);
    await refreshWorkBadge(() => Promise.resolve({ count: 0, jobs: [] }));
    expect(useWorkBadge.getState().count).toBe(0);
  });
});

describe('History', () => {
  const row = { ...job('h1', { state: 'done' }), status: 'merged', archived: true, archived_at: 1, archived_reason: '', attempt_count: 1, elapsed_seconds: 60, manageable: true };

  it('reports in one sentence', () => {
    expect(reportSentence({ total_jobs: 4, archived_jobs: 1, total_cost_usd: 8, average_cost_usd: 2, average_elapsed_seconds: 120, attempts: 5, failure_count: 1, failure_rate: 0.25, verification_runs: 4, verification_passed: 3, verification_pass_rate: 0.75, status_counts: {}, repositories: [] }))
      .toBe('4 jobs · $8.00 total, $2.00 each · 25% failed (1) · checks passed 75% (3/4) · 2m average run');
  });

  it('deletes through the job command after a confirmation', async () => {
    (jobsApi.history as jest.Mock).mockResolvedValue({ jobs: [row], total: 1, limit: 50, offset: 0, filters: {} });
    (jobsApi.report as jest.Mock).mockResolvedValue({ report: null });
    (jobCoreApi.command as jest.Mock).mockResolvedValue({ ok: true, parts: ['state'], data: {} });
    const screen = render(<ToastHost><HistoryPanel onOpenJob={jest.fn()} /></ToastHost>);
    fireEvent.press(await screen.findByTestId('history-delete-h1'));
    expect(jobCoreApi.command).not.toHaveBeenCalled();
    fireEvent.press(screen.getByTestId('history-confirm'));
    await waitFor(() => expect(jobCoreApi.command).toHaveBeenCalledWith('h1', 'delete'));
  });
});
