// Orchestration tab: approvals inbox rendering, typed confirmation, reject with reason,
// the kill switch, the run grid (vocabulary glyphs, executions) and node health.
import React from 'react';
import { act, fireEvent, render, waitFor } from '@testing-library/react-native';

import { orchestrationApi, subscribeToOrchestrationChanges, type OrchAction, type OrchNode, type OrchRunDetail } from '../src/api/orchestration';
import { OrchestrationScreen } from '../src/screens/OrchestrationScreen';
import { OrchRunScreen } from '../src/screens/OrchRunScreen';
import { AgentBanner, OrchNodesScreen, sortNodes } from '../src/screens/OrchNodesScreen';
import { OrchNodeScreen, policyRows } from '../src/screens/OrchNodeScreen';
import { logTail, outputSummary } from '../src/components/OrchViews';
import { badgeText, useOrchBadge } from '../src/store/orchBadge';

jest.mock('../src/api/orchestration', () => ({
  ...jest.requireActual('../src/api/orchestration'),
  orchestrationApi: { overview: jest.fn(), approvals: jest.fn(), run: jest.fn(), nodes: jest.fn(), node: jest.fn(), command: jest.fn(), proposeAgentUpdate: jest.fn() },
  subscribeToOrchestrationChanges: jest.fn(() => ({ close: jest.fn() })),
}));

const orch = orchestrationApi as unknown as Record<string, jest.Mock>;

const act_ = (id: string, label: string, extra: Partial<OrchAction> = {}): OrchAction =>
  ({ id, label, enabled: true, reason: '', primary: false, danger: false, tone: '', confirm: null, input: null, dialog: null, hidden: false, ...extra });

const exec = (over: Record<string, unknown> = {}) => ({
  id: 'e1', run_id: 'r1', node_id: 'n1', tool: 'ops.wipe-cache@2', step: 0, step_name: 'wipe', batch: 0, state: 'failed',
  style: { status: 'failed', label: 'failed' }, exit_code: 3, reason: '', duration_s: 1.25, attempts: 1, check_mode: false,
  rollback_of: '', output: { freed_bytes: 1024 }, output_errors: [], created_at: 1, dispatched_at: 1, started_at: 1, finished_at: 2,
  redactions: 0, log: 'line one\nboom: disk busy\n', log_truncated: false, ...over,
});

function run(over: Partial<OrchRunDetail> = {}): OrchRunDetail {
  return {
    id: 'r1', title: 'Wipe cache on web', kind: 'tool', target: 'ops.wipe-cache@2', state: 'proposed',
    style: { status: 'waiting', label: 'awaiting approval' }, proposed_by: 'ai:chat', approved_by: '', created_at: 1,
    started_at: null, finished_at: null, check_mode: false, nodes: 3, summary: {}, paused_reason: '', reason: '',
    effect: 'destructive', effect_style: { semantic: 'danger', label: 'destructive' },
    decision: { level: 'typed', summary: 'destructive tools need typed confirmation', rule: 'destructive' },
    blast_radius: '3 nodes in 2 batches', params: {}, selector: {}, strategy: {},
    excluded: [{ node_id: 'n9', name: 'db-1', reason: 'offline' }], steps: [{ name: 'wipe' }, { name: 'verify' }], batches: [['n1']],
    warnings: [], rollback_available: false, decision_full: null, grid: [], notes: [], secrets: [],
    actions: [
      act_('approveRun', 'Approve and start', { primary: true, tone: 'ok', input: { label: 'Type ops.wipe-cache to confirm', optional: false, multiline: false, arg: 'typed', expect: 'ops.wipe-cache' } }),
      act_('rejectRun', 'Reject', { tone: 'danger', danger: true, confirm: 'Reject this proposed run?', input: { label: 'Reason (optional)', optional: true, multiline: false, arg: 'reason' } }),
      act_('continueRun', 'Continue', { enabled: false, reason: 'run is not paused' }),
      act_('cancelRun', 'Cancel', { tone: 'danger', danger: true }),
    ],
    ...over,
  };
}

function node(over: Partial<OrchNode> = {}): OrchNode {
  return {
    id: 'n1', name: 'web-1', labels: { role: 'web', canary: 'true' }, status: 'active', style: { status: 'done', label: 'active' },
    online: true, kind: 'host', canary: false, last_seen: 1, last_seen_ago: '5s ago', agent_version: '1.0', address: '10.0.0.5',
    facts_at: 1, summary: { os: 'Debian 12', kernel: '6.1', arch: 'x86_64', cpus: 4, hostname: 'web-1', memory_gb: 8, memory_used_pct: 41.6,
      max_disk_used_pct: 87.2, uptime_days: 3, load: 0.2, systemd: true, services: 30, provides: [] },
    groups: [], policy: { sandbox: 'systemd', max_run_as: 'root', allow_tools: ['ops.*'] }, sandbox: 'systemd', policy_errors: [],
    actions: [act_('acceptNode', 'Accept', { enabled: false, reason: 'node is not awaiting acceptance', primary: true }), act_('quarantineNode', 'Quarantine', { tone: 'warn', confirm: 'Quarantine this node?', input: { label: 'Reason (optional)', optional: true, multiline: false, arg: 'reason' } }), act_('revokeNode', 'Revoke')],
    created_at: 1, ...over,
  };
}

const overview = (paused = false) => ({
  nodes: { total: 3, by_status: { active: 2, pending: 1 } }, catalog: { published: 4, drafts: 1, in_review: 1 },
  attention: { approvals: 1, paused_runs: 0, reviews: 1, pending_nodes: 1, offline_nodes: 0, dispatch_paused: paused, total: 3 },
  recent_runs: [], approvals: [], paused: [], unreachable: [], dispatch_paused: paused, time: 1,
});
const approvals = () => ({
  runs: [run()], paused: [],
  reviews: [{ ref: 'ops.rotate@1', title: 'Rotate logs', summary: 'logrotate now', version: 1, status: 'in_review', style: { status: 'waiting', label: 'in review' }, effect: 'mutating', effect_style: { semantic: 'attention', label: 'mutating' } }],
  runbook_reviews: [],
  nodes: [node({ id: 'n2', name: 'new-box', status: 'pending', style: { status: 'waiting', label: 'awaiting acceptance' },
    actions: [act_('acceptNode', 'Accept', { primary: true, tone: 'ok' }), act_('quarantineNode', 'Quarantine', { tone: 'warn' })] })],
});

const navigation = { navigate: jest.fn(), setOptions: jest.fn(), addListener: jest.fn(() => jest.fn()) };

beforeEach(() => {
  jest.clearAllMocks();
  orch.overview.mockResolvedValue(overview());
  orch.approvals.mockResolvedValue(approvals());
  orch.command.mockResolvedValue({ ok: true, command: 'x', data: {} });
});

describe('Orchestration tab: approvals inbox', () => {
  it('renders the proposed run with effect, blast radius, decision and excluded count', async () => {
    const screen = render(<OrchestrationScreen navigation={navigation as any} route={{ key: 'o', name: 'Orchestration' } as any} />);
    expect(await screen.findByText('Wipe cache on web')).toBeTruthy();
    expect(screen.getByTestId('orch-proposed-r1-effect')).toBeTruthy();
    expect(screen.getByText('destructive')).toBeTruthy();
    expect(screen.getByText(/Blast radius: 3 nodes in 2 batches/)).toBeTruthy();
    expect(screen.getByText(/Policy: destructive tools need typed confirmation/)).toBeTruthy();
    expect(screen.getByTestId('orch-proposed-r1-excluded').props.children).toBe(' · 1 excluded');
    // Inbox shows only approve/reject for proposed runs.
    expect(screen.getByTestId('orch-action-approveRun')).toBeTruthy();
    expect(screen.queryByTestId('orch-action-cancelRun')).toBeNull();
    // Pending node with accept; tool review read-only with a web hint.
    expect(screen.getByTestId('orch-pending-node-n2')).toBeTruthy();
    expect(screen.getByTestId('orch-review-ops.rotate@1')).toBeTruthy();
    expect(screen.getByText(/reviews happen on the web/)).toBeTruthy();
    expect(subscribeToOrchestrationChanges).toHaveBeenCalled();
    screen.unmount();
  });

  it('requires typing the target id exactly before approving', async () => {
    const screen = render(<OrchestrationScreen navigation={navigation as any} route={{ key: 'o', name: 'Orchestration' } as any} />);
    fireEvent.press(await screen.findByTestId('orch-action-approveRun'));
    expect(screen.getByTestId('orch-typed-hint')).toBeTruthy();
    fireEvent.changeText(screen.getByTestId('orch-action-prompt-input'), 'ops.wipe');
    fireEvent.press(screen.getByTestId('orch-action-prompt-submit'));
    expect(await screen.findByText('Type ops.wipe-cache exactly.')).toBeTruthy();
    expect(orch.command).not.toHaveBeenCalled();
    fireEvent.changeText(screen.getByTestId('orch-action-prompt-input'), 'ops.wipe-cache');
    await act(async () => { fireEvent.press(screen.getByTestId('orch-action-prompt-submit')); });
    expect(orch.command).toHaveBeenCalledWith('approveRun', { run_id: 'r1', typed: 'ops.wipe-cache' });
    await waitFor(() => expect(orch.overview).toHaveBeenCalledTimes(2));
    screen.unmount();
  });

  it('rejects with an optional reason, then a confirm', async () => {
    const screen = render(<OrchestrationScreen navigation={navigation as any} route={{ key: 'o', name: 'Orchestration' } as any} />);
    fireEvent.press(await screen.findByTestId('orch-action-rejectRun'));
    fireEvent.changeText(screen.getByTestId('orch-action-prompt-input'), 'wrong window');
    fireEvent.press(screen.getByTestId('orch-action-prompt-submit'));
    expect(await screen.findByText('Reject this proposed run?')).toBeTruthy();
    expect(orch.command).not.toHaveBeenCalled();
    await act(async () => { fireEvent.press(screen.getByTestId('orch-action-confirm-submit')); });
    expect(orch.command).toHaveBeenCalledWith('rejectRun', { run_id: 'r1', reason: 'wrong window' });
    screen.unmount();
  });

  it('accepts a pending node from the inbox', async () => {
    const screen = render(<OrchestrationScreen navigation={navigation as any} route={{ key: 'o', name: 'Orchestration' } as any} />);
    await screen.findByTestId('orch-pending-node-n2');
    const bar = screen.getByTestId('orch-pending-node-n2-actions');
    await act(async () => { fireEvent.press(bar.findAll((n: { props: { testID?: string } }) => n.props.testID === 'orch-action-acceptNode')[0]); });
    expect(orch.command).toHaveBeenCalledWith('acceptNode', { node_id: 'n2' });
    screen.unmount();
  });

  it('confirms before pausing all dispatch and shows the paused banner with resume', async () => {
    const screen = render(<OrchestrationScreen navigation={navigation as any} route={{ key: 'o', name: 'Orchestration' } as any} />);
    fireEvent.press(await screen.findByTestId('orch-kill-switch'));
    fireEvent.press(screen.getByTestId('orch-action-prompt-submit')); // optional reason left empty
    expect(await screen.findByTestId('orch-action-confirm')).toBeTruthy();
    expect(orch.command).not.toHaveBeenCalled();
    orch.overview.mockResolvedValue(overview(true));
    await act(async () => { fireEvent.press(screen.getByTestId('orch-action-confirm-submit')); });
    expect(orch.command).toHaveBeenCalledWith('pauseDispatch', {});
    expect(await screen.findByTestId('orch-dispatch-paused')).toBeTruthy();
    expect(screen.getByText('Dispatch is paused: no new work is sent to any node.')).toBeTruthy();
    fireEvent.press(screen.getByTestId('orch-kill-switch'));
    await act(async () => { fireEvent.press(await screen.findByTestId('orch-action-confirm-submit')); });
    expect(orch.command).toHaveBeenLastCalledWith('resumeDispatch', {});
    screen.unmount();
  });

  it('refetches on a live change (debounced)', async () => {
    jest.useFakeTimers();
    try {
      const screen = render(<OrchestrationScreen navigation={navigation as any} route={{ key: 'o', name: 'Orchestration' } as any} />);
      await act(async () => { await Promise.resolve(); });
      const onChange = (subscribeToOrchestrationChanges as jest.Mock).mock.calls.slice(-1)[0][0];
      const before = orch.overview.mock.calls.length;
      act(() => { onChange({ scope: 'run', id: 'r1', parts: ['state'], seq: 2 }); onChange({ scope: 'run', id: 'r1', parts: ['state'], seq: 3 }); });
      await act(async () => { jest.advanceTimersByTime(400); });
      expect(orch.overview.mock.calls.length).toBe(before + 1);
      screen.unmount();
    } finally { jest.useRealTimers(); }
  });

  it('explains when the server has no orchestration', async () => {
    const { ApiError } = jest.requireActual('../src/api/client');
    orch.overview.mockRejectedValue(new ApiError(404, 'Not Found'));
    const screen = render(<OrchestrationScreen navigation={navigation as any} route={{ key: 'o', name: 'Orchestration' } as any} />);
    expect(await screen.findByTestId('orch-missing')).toBeTruthy();
    screen.unmount();
  });
});

describe('run detail', () => {
  it('shows step glyphs with vocabulary labels and a node’s executions on tap; retries failed nodes', async () => {
    orch.run.mockResolvedValue(run({
      state: 'paused', style: { status: 'waiting', label: 'paused' }, paused_reason: 'failure threshold reached',
      actions: [act_('continueRun', 'Continue', { primary: true, tone: 'ok' }), act_('rollbackRun', 'Roll back', { tone: 'warn', confirm: 'Roll back this run?' }), act_('approveRun', 'Approve and start', { enabled: false, reason: 'run is not awaiting approval' })],
      grid: [{
        node_id: 'n1', name: 'web-1', labels: {}, online: true, batch: 0, progress: 'failed', failed: true, rollbacks: [],
        cells: [
          { step: 0, name: 'wipe', exec: exec() as any, style: { status: 'failed', label: 'failed' }, attempts: 1 },
          { step: 1, name: 'verify', exec: null, style: { status: 'pending', label: 'not started' }, attempts: 0 },
        ],
      }],
    }));
    const screen = render(<OrchRunScreen navigation={navigation as any} route={{ key: 'r', name: 'OrchRun', params: { runId: 'r1' } } as any} />);
    expect(await screen.findByText('Paused: failure threshold reached')).toBeTruthy();
    expect(screen.getByTestId('orch-cell-n1-0').props.accessibilityLabel).toBe('failed');
    expect(screen.getByTestId('orch-cell-n1-1').props.accessibilityLabel).toBe('not started');
    expect(screen.getByText('✗')).toBeTruthy();
    expect(screen.queryByTestId('orch-exec-e1')).toBeNull();
    fireEvent.press(screen.getByLabelText(/web-1, failed\. Show executions/));
    expect(screen.getByTestId('orch-exec-e1-log').props.children).toBe('line one\nboom: disk busy');
    expect(screen.getByTestId('orch-exec-e1-output').props.children).toBe('freed_bytes: 1024');
    fireEvent.press(screen.getByTestId('orch-retry-n1'));
    await act(async () => { fireEvent.press(await screen.findByTestId('orch-action-confirm-submit')); });
    expect(orch.command).toHaveBeenCalledWith('retryNode', { run_id: 'r1', node_id: 'n1' });
    // Disabled actions explain why instead of sending.
    fireEvent.press(screen.getByTestId('orch-action-bar-unavailable'));
    expect(screen.getByText('run is not awaiting approval')).toBeTruthy();
    screen.unmount();
  });
});

describe('node health', () => {
  it('lists nodes with status, disk and memory, needs-you first', async () => {
    orch.nodes.mockResolvedValue({ nodes: [node(), node({ id: 'n3', name: 'db-1', online: false, last_seen_ago: '2h ago', style: { status: 'failed', label: 'offline' } })], groups: [] });
    const screen = render(<OrchNodesScreen navigation={navigation as any} route={{ key: 'n', name: 'OrchNodes' } as any} />);
    expect(await screen.findByTestId('orch-node-n1')).toBeTruthy();
    expect(screen.getByTestId('orch-node-n1-health').props.children.join('')).toBe('disk 87% · mem 42%');
    expect(screen.getByText('offline')).toBeTruthy();
    expect(screen.getAllByText('role=web')).toHaveLength(2);
    fireEvent.press(screen.getByTestId('orch-node-n3'));
    expect(navigation.navigate).toHaveBeenCalledWith('OrchNode', { nodeId: 'n3' });
    expect(sortNodes([node(), node({ id: 'n3', name: 'db-1', online: false })]).map(n => n.id)).toEqual(['n3', 'n1']);
    screen.unmount();
  });

  it('node detail shows facts, policy and recent executions; quarantine asks for a reason', async () => {
    orch.node.mockResolvedValue({ ...node(), facts: {}, recent_executions: [exec({ state: 'succeeded', style: { status: 'done', label: 'succeeded' } })] });
    const screen = render(<OrchNodeScreen navigation={navigation as any} route={{ key: 'n', name: 'OrchNode', params: { nodeId: 'n1' } } as any} />);
    expect(await screen.findByText('Debian 12')).toBeTruthy();
    expect(screen.getByText('max_run_as')).toBeTruthy();
    expect(screen.getByText('ops.*')).toBeTruthy();
    expect(screen.getByTestId('orch-node-exec-e1')).toBeTruthy();
    expect(screen.queryByTestId('orch-action-revokeNode')).toBeNull();
    fireEvent.press(screen.getByTestId('orch-action-quarantineNode'));
    fireEvent.changeText(screen.getByTestId('orch-action-prompt-input'), 'disk alarms');
    fireEvent.press(screen.getByTestId('orch-action-prompt-submit'));
    await act(async () => { fireEvent.press(await screen.findByTestId('orch-action-confirm-submit')); });
    expect(orch.command).toHaveBeenCalledWith('quarantineNode', { node_id: 'n1', reason: 'disk alarms' });
    screen.unmount();
  });
});

describe('agent updates', () => {
  const agents = (over = {}) => ({ current: '1.1.0', auto: false, outdated: 2, updatable: 2, open_run: '', ...over });

  it('nodes list offers an update job for outdated agents, or opens the job already running', () => {
    const onUpdate = jest.fn(); const onOpenRun = jest.fn();
    let screen = render(<AgentBanner agents={agents()} onUpdate={onUpdate} onOpenRun={onOpenRun} />);
    expect(screen.getByText(/2 nodes run an older agent than the master \(1\.1\.0\)/)).toBeTruthy();
    fireEvent.press(screen.getByTestId('orch-agents-update'));
    expect(onUpdate).toHaveBeenCalled();
    screen.unmount();
    screen = render(<AgentBanner agents={agents({ open_run: 'r7' })} onUpdate={onUpdate} onOpenRun={onOpenRun} />);
    fireEvent.press(screen.getByTestId('orch-agents-open-run'));
    expect(onOpenRun).toHaveBeenCalledWith('r7');
    screen.unmount();
    expect(render(<AgentBanner agents={agents({ outdated: 0, updatable: 0 })} onUpdate={onUpdate} onOpenRun={onOpenRun} />).toJSON()).toBeNull();
    expect(render(<AgentBanner agents={null} onUpdate={onUpdate} onOpenRun={onOpenRun} />).toJSON()).toBeNull();
  });

  it('node detail proposes an update job for just that node and opens it', async () => {
    orch.node.mockResolvedValue({ ...node({ agent: { version: '1.0', current: '1.1.0', outdated: true, updatable: true, reason: '' } }), facts: {}, recent_executions: [] });
    orch.proposeAgentUpdate.mockResolvedValue({ ok: true, command: 'proposeAgentUpdate', data: { run: { id: 'r9', state: 'proposed' } } });
    const screen = render(<OrchNodeScreen navigation={navigation as any} route={{ key: 'n', name: 'OrchNode', params: { nodeId: 'n1' } } as any} />);
    expect(await screen.findByText('1.0 (master has 1.1.0)')).toBeTruthy();
    await act(async () => { fireEvent.press(screen.getByTestId('orch-node-update-agent')); });
    expect(orch.proposeAgentUpdate).toHaveBeenCalledWith(['n1']);
    expect(navigation.navigate).toHaveBeenCalledWith('OrchRun', { runId: 'r9' });
    screen.unmount();
  });

  it('an agent that cannot self-update says why and offers no button', async () => {
    orch.node.mockResolvedValue({ ...node({ agent: { version: '1.0', current: '1.1.0', outdated: true, updatable: false, reason: 'reinstall the node agent' } }), facts: {}, recent_executions: [] });
    const screen = render(<OrchNodeScreen navigation={navigation as any} route={{ key: 'n', name: 'OrchNode', params: { nodeId: 'n1' } } as any} />);
    expect(await screen.findByText(/reinstall the node agent/)).toBeTruthy();
    expect(screen.queryByTestId('orch-node-update-agent')).toBeNull();
    screen.unmount();
  });
});

describe('tab badge', () => {
  it('formats the count and clears when disconnected', () => {
    expect(badgeText(0)).toBeUndefined();
    expect(badgeText(3)).toBe('3');
    expect(badgeText(120)).toBe('99+');
    useOrchBadge.getState().set({ approvals: 1, paused_runs: 0, reviews: 0, pending_nodes: 1, offline_nodes: 0, dispatch_paused: false, total: 2, used: true, summary: '1 run awaiting approval' } as any);
    expect(useOrchBadge.getState().count).toBe(2);
    useOrchBadge.getState().set(null);
    expect(useOrchBadge.getState().count).toBe(0);
  });
});

describe('helpers', () => {
  it('tails logs, summarises outputs and policies', () => {
    expect(logTail(Array.from({ length: 50 }, (_, i) => `l${i}`).join('\n'), 3)).toBe('l47\nl48\nl49');
    expect(outputSummary({ a: 1, b: 'x' })).toBe('a: 1\nb: x');
    expect(outputSummary(null)).toBe('');
    expect(policyRows({ sandbox: 'systemd', errors: ['x'], allow: [], caps: { a: 1 } })).toEqual([['sandbox', 'systemd'], ['allow', 'none'], ['caps', '1 entries']]);
  });
});
