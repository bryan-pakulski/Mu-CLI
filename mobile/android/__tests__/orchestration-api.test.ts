// Orchestration API client: CSRF token fetch/cache, retry once on 403, command payloads,
// live-change decoding, attention notifications, and the vocabulary mapping.
jest.mock('../src/store/connection', () => ({
  useConnectionStore: { getState: () => ({ baseUrl: 'http://mucli.test', activeSessionName: null }) },
}));

import { ApiError } from '../src/api/client';
import { attentionNotification, orchestrationApi, resetCsrfCache } from '../src/api/orchestration';
import { orchEffect, orchState } from '../src/theme/status';
import { vocabulary } from '../src/theme/wash.generated';

type Call = { url: string; method: string; headers: Record<string, string>; body: unknown };

function respond(status: number, body: unknown) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => 'application/json' },
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  });
}

let calls: Call[];
let tokens: string[];
let cmdStatuses: number[];

beforeEach(() => {
  resetCsrfCache();
  calls = [];
  tokens = ['t1', 't2', 't3'];
  cmdStatuses = [];
  global.fetch = jest.fn((url: string, init: { method: string; headers: Record<string, string>; body?: string }) => {
    calls.push({ url, method: init.method, headers: init.headers, body: init.body ? JSON.parse(init.body) : undefined });
    if (url.includes('/api/orchestration/csrf')) return respond(200, { token: tokens.shift(), header: 'X-MuCLI-CSRF' });
    if (url.includes('/api/orchestration/cmd')) {
      const status = cmdStatuses.shift() ?? 200;
      return status === 200 ? respond(200, { ok: true, command: 'x', data: {} }) : respond(status, { detail: status === 403 ? 'missing or expired CSRF token; reload the page' : 'run is not awaiting approval' });
    }
    return respond(200, {});
  }) as unknown as typeof fetch;
});

const cmds = () => calls.filter(c => c.url.includes('/cmd'));

describe('orchestration commands', () => {
  it('fetches the CSRF token once, sends it as the header and caches it', async () => {
    await orchestrationApi.continueRun('r1');
    await orchestrationApi.pauseRun('r1');
    expect(calls.filter(c => c.url.includes('/csrf'))).toHaveLength(1);
    expect(cmds()).toHaveLength(2);
    for (const c of cmds()) {
      expect(c.method).toBe('POST');
      expect(c.headers['X-MuCLI-CSRF']).toBe('t1');
    }
  });

  it('refetches the token once and retries when a command answers 403', async () => {
    cmdStatuses = [403, 200];
    await orchestrationApi.cancelRun('r9');
    expect(calls.filter(c => c.url.includes('/csrf'))).toHaveLength(2);
    expect(cmds().map(c => c.headers['X-MuCLI-CSRF'])).toEqual(['t1', 't2']);
    expect(cmds()[1].body).toEqual({ type: 'cancelRun', args: { run_id: 'r9' } });
  });

  it('gives up after one retry and surfaces the server detail', async () => {
    cmdStatuses = [403, 403];
    await expect(orchestrationApi.acceptNode('n1')).rejects.toEqual(expect.objectContaining({ status: 403 }));
    expect(cmds()).toHaveLength(2);
  });

  it('does not retry other errors (409 detail is the message)', async () => {
    cmdStatuses = [409];
    const err = await orchestrationApi.approveRun('r1').catch(e => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toBe('run is not awaiting approval');
    expect(cmds()).toHaveLength(1);
  });

  it('sends the documented payloads', async () => {
    await orchestrationApi.approveRun('r1', 'ops.wipe-cache');
    await orchestrationApi.approveRun('r2');
    await orchestrationApi.rejectRun('r3', 'not now');
    await orchestrationApi.rollbackRun('r4');
    await orchestrationApi.retryNode('r5', 'n5');
    await orchestrationApi.quarantineNode('n6', 'flaky');
    await orchestrationApi.unquarantineNode('n7');
    await orchestrationApi.pauseDispatch('incident');
    await orchestrationApi.resumeDispatch();
    expect(cmds().map(c => c.body)).toEqual([
      { type: 'approveRun', args: { run_id: 'r1', typed: 'ops.wipe-cache' } },
      { type: 'approveRun', args: { run_id: 'r2' } },
      { type: 'rejectRun', args: { run_id: 'r3', reason: 'not now' } },
      { type: 'rollbackRun', args: { run_id: 'r4' } },
      { type: 'retryNode', args: { run_id: 'r5', node_id: 'n5' } },
      { type: 'quarantineNode', args: { node_id: 'n6', reason: 'flaky' } },
      { type: 'unquarantineNode', args: { node_id: 'n7' } },
      { type: 'pauseDispatch', args: { reason: 'incident' } },
      { type: 'resumeDispatch', args: {} },
    ]);
    expect(cmds().every(c => c.url.startsWith('http://mucli.test/api/orchestration/cmd'))).toBe(true);
  });

  it('reads runs filtered by state', async () => {
    await orchestrationApi.runs(['proposed', 'paused'], 20);
    expect(calls[0].url).toBe('http://mucli.test/api/orchestration/runs?state=proposed%2Cpaused&limit=20');
  });
});

describe('attention notifications', () => {
  it('maps bus events to notifications and ignores dispatch toggles', () => {
    expect(attentionNotification({ kind: 'orchestration_attention', detail: 'orchestration_approval', run_id: 'r1', title: 'Rotate logs' }))
      .toEqual(expect.objectContaining({ title: 'Orchestration: run awaits approval', body: 'Rotate logs', data: { orchRunId: 'r1' } }));
    expect(attentionNotification({ kind: 'orchestration_attention', detail: 'orchestration_node', node_id: 'n1' })?.data).toEqual({ orchNodeId: 'n1' });
    expect(attentionNotification({ kind: 'orchestration_attention', detail: 'orchestration', run_id: 'r2', state: 'paused', reason: 'failure threshold' })?.title)
      .toBe('Orchestration: Run paused');
    expect(attentionNotification({ kind: 'orchestration_attention', detail: 'dispatch', paused: true })).toBeNull();
  });
});

describe('badge and agent updates', () => {
  it('reads the badge without a CSRF token and proposes agent updates as a command', async () => {
    await orchestrationApi.badge();
    expect(calls.map(c => [c.method, c.url])).toEqual([['GET', 'http://mucli.test/api/orchestration/badge']]);
    await orchestrationApi.proposeAgentUpdate(['n1']);
    const cmd = calls[calls.length - 1];
    expect(cmd.url).toBe('http://mucli.test/api/orchestration/cmd');
    expect(cmd.body).toEqual({ type: 'proposeAgentUpdate', args: { nodes: ['n1'] } });
    expect(cmd.headers['X-MuCLI-CSRF']).toBe('t1');
    await orchestrationApi.proposeAgentUpdate();
    expect(calls[calls.length - 1].body).toEqual({ type: 'proposeAgentUpdate', args: {} });
  });
});

describe('orchestration vocabulary', () => {
  it('maps every table row onto the shared status vocabulary', () => {
    for (const table of ['orch_node_status', 'orch_run_state', 'orch_exec_state', 'orch_tool_status'] as const) {
      const rows = vocabulary[table] as Record<string, { status: string; label: string }>;
      for (const [key, row] of Object.entries(rows)) {
        expect(orchState(table, key)).toEqual({ status: row.status, label: row.label });
        expect(Object.keys(vocabulary.status)).toContain(row.status);
      }
    }
    expect(orchState('orch_run_state', 'proposed')).toEqual({ status: 'waiting', label: 'awaiting approval' });
    expect(orchState('orch_node_status', 'offline').status).toBe('failed');
  });

  it('prefers the server style and falls back to pending for unknown keys', () => {
    expect(orchState('orch_exec_state', 'queued', { status: 'pending', label: 'not started' }).label).toBe('not started');
    expect(orchState('orch_exec_state', 'mystery_state')).toEqual({ status: 'pending', label: 'mystery state' });
  });

  it('maps effects to semantic tones; unknown effects read as danger', () => {
    expect(orchEffect('read_only')).toEqual({ tone: 'ok', label: 'read-only' });
    expect(orchEffect('mutating')).toEqual({ tone: 'attention', label: 'mutating' });
    expect(orchEffect('destructive')).toEqual({ tone: 'danger', label: 'destructive' });
    expect(orchEffect('weird').tone).toBe('danger');
  });
});
