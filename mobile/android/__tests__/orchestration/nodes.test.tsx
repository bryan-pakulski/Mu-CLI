// Nodes on the phone, from the server's view models: presence (connecting / online / offline) and
// what a node is running; accepting a new node after checking its labels; the node page's
// maintenance (open runs decided inline, dry runs and confirmed reboots proposed as runs), labels,
// agent updates, and node-local policy — editable only when the node allows remote edits, through
// validate → send.
import React from 'react';
import { act, fireEvent, render, waitFor, within } from '@testing-library/react-native';
import { ThemeProvider } from '../../src/theme/ThemeContext';

jest.mock('../../src/api/orchestration', () => ({
  ...jest.requireActual('../../src/api/orchestration'),
  orchestrationApi: { nodes: jest.fn(), node: jest.fn(), maintenance: jest.fn(), command: jest.fn(), proposeAgentUpdate: jest.fn() },
  subscribeToOrchestrationChanges: jest.fn(() => ({ close: jest.fn() })),
}));

import { orchestrationApi } from '../../src/api/orchestration';
import { NodesView } from '../../src/screens/orchestration/NodesView';
import { OrchNodeScreen, parseLabels } from '../../src/screens/orchestration/NodeScreen';
import { busyWords, nodeSign, nodeSub } from '../../src/screens/orchestration/parts';

const api = orchestrationApi as unknown as Record<string, jest.Mock>;
const fx = (name: string) => JSON.parse(JSON.stringify(require(`../fixtures/server/orchestration/${name}.json`)));
const navigation = { navigate: jest.fn(), goBack: jest.fn() };
const wrap = (node: React.ReactElement) => render(<ThemeProvider statusBar={false}>{node}</ThemeProvider>);
const busy = { run_id: 'r_9', title: 'Restart web', tool: 'demo.restart@1', state: 'running', count: 2, queued: 0, since: 1 };

beforeEach(() => {
  jest.clearAllMocks();
  api.command.mockResolvedValue({ ok: true, command: 'x', data: {} });
  api.nodes.mockResolvedValue(fx('nodes'));
  api.node.mockResolvedValue(fx('node'));
  api.maintenance.mockResolvedValue(fx('maintenance'));
});

describe('node rows', () => {
  it('says connecting rather than offline just after the master starts, and what a busy node runs', () => {
    const n = fx('nodes').nodes.find((x: any) => x.status === 'active');
    expect(nodeSign({ ...n, presence: 'connecting', online: false })).toBe('pending');
    expect(nodeSub({ ...n, presence: 'connecting', online: false })).toMatch(/^Connecting/);
    expect(nodeSign({ ...n, presence: 'offline', online: false })).toBe('failed');
    expect(nodeSign({ ...n, busy })).toBe('active');
    expect(busyWords(busy as never)).toBe('Running Restart web +1');
    expect(busyWords({ ...busy, state: 'queued', count: 0, queued: 1 } as never)).toBe('Queued: Restart web');
  });

  it('lists nodes, filters by state and accepts a pending node after the label check', async () => {
    const data = fx('nodes');
    const pending = data.nodes.find((x: any) => x.status === 'pending');
    const ui = wrap(<NodesView openNode={jest.fn()} openRun={jest.fn()} />);
    expect(await ui.findByTestId(`orch-node-${pending.id}`)).toBeTruthy();
    await act(async () => { fireEvent.press(ui.getByTestId('orch-nodes-filter-pending')); });
    expect(ui.queryByText(/^web-1/)).toBeNull();
    await act(async () => { fireEvent.press(ui.getByTestId(`orch-node-${pending.id}-accept`)); });
    expect(ui.getByText(/env=prod, role=db/)).toBeTruthy();
    await act(async () => { fireEvent.press(ui.getByTestId('server-action-ok')); });
    expect(api.command).toHaveBeenCalledWith('acceptNode', { node_id: pending.id });
  });

  it('offers an update job for outdated agents, or opens the open one', async () => {
    const data = fx('nodes');
    data.agents = { current: '1.3.0', auto: false, outdated: 2, updatable: 1, open_run: '' };
    api.nodes.mockResolvedValue(data);
    api.proposeAgentUpdate.mockResolvedValue({ ok: true, command: 'proposeAgentUpdate', data: { run: { id: 'r_up', state: 'proposed' } } });
    const openRun = jest.fn();
    const ui = wrap(<NodesView openNode={jest.fn()} openRun={openRun} />);
    expect(await ui.findByText(/2 nodes run an older agent than the master \(1\.3\.0\)\. 1 need a manual update/)).toBeTruthy();
    await act(async () => { fireEvent.press(ui.getByTestId('orch-agents-update')); });
    expect(openRun).toHaveBeenCalledWith('r_up');
  });
});

describe('node page', () => {
  it('shows what the node is running and decides its open runs inline', async () => {
    api.node.mockResolvedValue({ ...fx('node'), busy });
    const m = fx('maintenance');
    const open = m.open_runs[0];
    const ui = wrap(<OrchNodeScreen route={{ params: { nodeId: fx('node').id } }} navigation={navigation} />);
    expect(within(await ui.findByTestId('orch-node-busy')).getByText('Restart web')).toBeTruthy();
    expect(await ui.findByTestId(`orch-open-run-${open.id}`)).toBeTruthy();
    await act(async () => { fireEvent.press(ui.getByTestId(`orch-open-run-${open.id}-actions-approveRun`)); });
    expect(api.command).toHaveBeenCalledWith('approveRun', { run_id: open.id });
  });

  it('proposes maintenance as runs: checks straight away, reboots after a confirmation', async () => {
    const node = fx('node');
    const m = fx('maintenance');
    const reboot = m.actions.find((a: any) => a.key === 'reboot');
    reboot.available = true; reboot.reason = '';
    api.maintenance.mockResolvedValue(m);
    api.command.mockResolvedValue({ ok: true, command: 'nodeMaintenance', data: { run: { id: 'r_m', state: 'proposed' } } });
    const ui = wrap(<OrchNodeScreen route={{ params: { nodeId: node.id } }} navigation={navigation} />);
    await act(async () => { fireEvent.press(await ui.findByTestId('orch-maint-check_updates-run')); });
    expect(api.command).toHaveBeenCalledWith('nodeMaintenance', { node_id: node.id, action: 'check_updates', params: {}, check_mode: false });
    api.command.mockClear();
    await act(async () => { fireEvent.press(ui.getByTestId('orch-maint-onlyif')); });
    await act(async () => { fireEvent.press(ui.getByTestId('orch-maint-reboot-run')); });
    expect(ui.getByText(/reboots 30 seconds after the run is approved.*It only reboots if it still needs one/)).toBeTruthy();
    expect(api.command).not.toHaveBeenCalled();
    await act(async () => { fireEvent.press(ui.getByTestId('server-action-ok')); });
    expect(api.command).toHaveBeenCalledWith('nodeMaintenance', { node_id: node.id, action: 'reboot', params: { only_if_required: true }, check_mode: false });
  });

  it('edits labels from the server action that asks for its dialog', async () => {
    const node = fx('node');
    const ui = wrap(<OrchNodeScreen route={{ params: { nodeId: node.id } }} navigation={navigation} />);
    await act(async () => { fireEvent.press(await ui.findByTestId('orch-node-actions-more')); });
    await act(async () => { fireEvent.press(ui.getByText('Edit labels')); });
    fireEvent.changeText(ui.getByTestId('orch-labels-input'), 'env=prod, role=web, tier=front');
    await act(async () => { fireEvent.press(ui.getByTestId('orch-labels-save')); });
    expect(api.command).toHaveBeenCalledWith('setNodeLabels', { node_id: node.id, labels: { env: 'prod', role: 'web', tier: 'front' } });
    expect(parseLabels('a=1, flag ,b = 2')).toEqual({ a: '1', flag: '', b: '2' });
  });

  it('keeps policy read-only when the node edits its own, and says why', async () => {
    const node = fx('node');
    const ui = wrap(<OrchNodeScreen route={{ params: { nodeId: node.id } }} navigation={navigation} />);
    expect(await ui.findByText('Edited on the node')).toBeTruthy();
    expect(ui.getByText(node.remote_policy.reason)).toBeTruthy();
    expect(ui.queryByTestId('orch-node-edit-policy')).toBeNull();
  });

  it('edits policy through validate → send when the node allows it', async () => {
    const node = fx('node');
    node.remote_policy = { allowed: true, supported: true, text: 'max_effect: read_only\n', pending: null };
    api.node.mockResolvedValue(node);
    api.command.mockImplementation(async (type: string) => (type === 'validateNodePolicy'
      ? { ok: true, command: type, data: { ok: true, errors: [], changes: [{ key: 'max_effect', from: 'read_only', to: 'mutating' }], warnings: ['Mutating tools can now run here.'] } }
      : { ok: true, command: type, data: {} }));
    const ui = wrap(<OrchNodeScreen route={{ params: { nodeId: node.id } }} navigation={navigation} />);
    await act(async () => { fireEvent.press(await ui.findByTestId('orch-node-edit-policy')); });
    expect(ui.getByTestId('orch-policy-send').props.accessibilityState).toMatchObject({ disabled: true });
    fireEvent.changeText(ui.getByTestId('orch-policy-text'), 'max_effect: mutating\n');
    await waitFor(() => expect(ui.getByText('read_only → mutating')).toBeTruthy(), { timeout: 2000 });
    expect(ui.getByText('◆ Mutating tools can now run here.')).toBeTruthy();
    await act(async () => { fireEvent.press(ui.getByTestId('orch-policy-send')); });
    expect(api.command).toHaveBeenCalledWith('setNodePolicy', { node_id: node.id, text: 'max_effect: mutating\n' });
  });
});
