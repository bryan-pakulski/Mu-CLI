// The Orchestration tab on the server's own view models (fixtures/server/orchestration, captured by
// scripts/fixtures/capture_orchestration.py): the fleet's state sentence, Now — runs to approve with
// their decisive action (typed confirmation when policy asks, reject with an optional reason),
// nodes to accept, reboots, the kill switch — and the other views.
import React from 'react';
import { act, fireEvent, render, waitFor, within } from '@testing-library/react-native';
import { ThemeProvider } from '../../src/theme/ThemeContext';

jest.mock('../../src/api/orchestration', () => ({
  ...jest.requireActual('../../src/api/orchestration'),
  orchestrationApi: {
    overview: jest.fn(), approvals: jest.fn(), nodes: jest.fn(), audit: jest.fn(), runs: jest.fn(), tools: jest.fn(),
    settings: jest.fn(), command: jest.fn(), proposeAgentUpdate: jest.fn(),
  },
  subscribeToOrchestrationChanges: jest.fn(() => ({ close: jest.fn() })),
}));

import { orchestrationApi } from '../../src/api/orchestration';
import { OrchestrationScreen, fleetState } from '../../src/screens/orchestration/OrchestrationScreen';

const api = orchestrationApi as unknown as Record<string, jest.Mock>;
const fx = (name: string) => JSON.parse(JSON.stringify(require(`../fixtures/server/orchestration/${name}.json`)));
const navigation = { navigate: jest.fn(), goBack: jest.fn() };

function setup(over: { overview?: any; approvals?: any } = {}) {
  api.overview.mockResolvedValue(over.overview || fx('overview'));
  api.approvals.mockResolvedValue(over.approvals || fx('approvals'));
  api.nodes.mockResolvedValue(fx('nodes'));
  api.audit.mockResolvedValue(fx('audit'));
  api.runs.mockResolvedValue(fx('runs'));
  api.tools.mockResolvedValue(fx('tools'));
  api.settings.mockResolvedValue(fx('settings'));
  api.command.mockResolvedValue({ ok: true, command: 'x', data: {} });
}

const renderTab = (params?: { tab?: string }) => render(
  <ThemeProvider statusBar={false}><OrchestrationScreen navigation={navigation} route={{ params }} /></ThemeProvider>,
);

beforeEach(() => { jest.clearAllMocks(); setup(); });

it('says what the fleet is doing in one sentence', () => {
  const overview = fx('overview');
  const parts = fleetState(overview).map(p => p.text);
  expect(parts).toEqual(['3 nodes', '2 need you', `${overview.catalog.published} tools`, '1 draft', 'dispatching']);
  const o = fx('overview');
  o.nodes = { total: 3, by_status: { active: 1, offline: 1, connecting: 1 } };
  o.attention.dispatch_paused = true;
  expect(fleetState(o)).toEqual(expect.arrayContaining([{ text: '1 offline', tone: 'danger' }, { text: '1 connecting', tone: 'quiet' }, { text: 'dispatch paused', tone: 'danger' }]));
});

it('puts the run to approve and the node to accept first, each with its action', async () => {
  const ui = renderTab();
  const run = fx('approvals').runs[0];
  const node = fx('approvals').nodes[0];
  const row = await ui.findByTestId(`orch-now-run-${run.id}`);
  expect(within(row).getByText(`Approve · ${run.title}`)).toBeTruthy();
  await act(async () => { fireEvent.press(ui.getByTestId(`orch-now-run-${run.id}-actions-approveRun`)); });
  expect(api.command).toHaveBeenCalledWith('approveRun', { run_id: run.id });
  await act(async () => { fireEvent.press(ui.getByTestId(`orch-now-node-${node.id}-actions-acceptNode`)); });
  expect(api.command).toHaveBeenCalledWith('acceptNode', { node_id: node.id });
});

it('asks for the typed confirmation policy requires before approving', async () => {
  const approvals = fx('approvals');
  const run = approvals.runs[0];
  run.actions.find((a: any) => a.id === 'approveRun').input = { label: 'Type demo.restart to confirm', optional: false, multiline: false, arg: 'typed', expect: 'demo.restart' };
  setup({ approvals });
  const ui = renderTab();
  await ui.findByTestId(`orch-now-run-${run.id}`);
  await act(async () => { fireEvent.press(ui.getByTestId(`orch-now-run-${run.id}-actions-approveRun`)); });
  expect(ui.getByText('Type demo.restart to confirm.')).toBeTruthy();
  fireEvent.changeText(ui.getByTestId('server-action-input'), 'demo.restar');
  expect(ui.getByTestId('server-action-ok').props.accessibilityState).toMatchObject({ disabled: true });
  fireEvent.changeText(ui.getByTestId('server-action-input'), 'demo.restart');
  await act(async () => { fireEvent.press(ui.getByTestId('server-action-ok')); });
  expect(api.command).toHaveBeenCalledWith('approveRun', { run_id: run.id, typed: 'demo.restart' });
});

it('rejects with the confirm text and an optional reason in one step', async () => {
  const run = fx('approvals').runs[0];
  const ui = renderTab();
  await ui.findByTestId(`orch-now-run-${run.id}`);
  await act(async () => { fireEvent.press(ui.getByTestId(`orch-now-run-${run.id}-actions-more`)); });
  await act(async () => { fireEvent.press(ui.getByText('Reject')); });
  expect(ui.getByText('Reject this proposed run?')).toBeTruthy();
  fireEvent.changeText(ui.getByTestId('server-action-input'), 'wrong window');
  await act(async () => { fireEvent.press(ui.getByTestId('server-action-ok')); });
  expect(api.command).toHaveBeenCalledWith('rejectRun', { run_id: run.id, reason: 'wrong window' });
});

it('confirms before pausing all dispatch, and offers resume while paused', async () => {
  const ui = renderTab();
  await act(async () => { fireEvent.press(await ui.findByTestId('orch-kill-switch')); });
  expect(api.command).not.toHaveBeenCalled();
  await act(async () => { fireEvent.press(ui.getByTestId('server-action-ok')); });
  expect(api.command).toHaveBeenCalledWith('pauseDispatch', {});

  const o = fx('overview');
  o.attention.dispatch_paused = true;
  setup({ overview: o });
  const paused = renderTab();
  expect(await paused.findByTestId('orch-dispatch-paused')).toBeTruthy();
  await act(async () => { fireEvent.press(paused.getAllByTestId('orch-kill-switch')[0]); });
  expect(api.command).toHaveBeenCalledWith('resumeDispatch', {});
});

it('offers rolling reboots for nodes that need one, then opens the run', async () => {
  const o = fx('overview');
  o.reboot = { count: 1, nodes: [{ node_id: 'n_1', name: 'web-1', reasons: ['kernel updated'] }] };
  setup({ overview: o });
  api.command.mockResolvedValue({ ok: true, command: 'nodeMaintenance', data: { run: { id: 'r_reboot', state: 'proposed' } } });
  const ui = renderTab();
  await act(async () => { fireEvent.press(await ui.findByTestId('orch-now-reboot-go')); });
  await act(async () => { fireEvent.press(ui.getByTestId('server-action-ok')); });
  expect(api.command).toHaveBeenCalledWith('nodeMaintenance', { node_ids: ['n_1'], action: 'reboot', params: { only_if_required: true } });
  expect(navigation.navigate).toHaveBeenCalledWith('OrchRun', { runId: 'r_reboot' });
});

it('switches views with the header tabs and opens a run', async () => {
  const ui = renderTab();
  await ui.findByTestId('orch-now');
  await act(async () => { fireEvent.press(ui.getByTestId('orch-head-tabs-runs')); });
  const first = fx('runs').runs[0];
  await act(async () => { fireEvent.press(await ui.findByText(first.title)); });
  expect(navigation.navigate).toHaveBeenCalledWith('OrchRun', { runId: first.id });
  await act(async () => { fireEvent.press(ui.getByTestId('orch-head-tabs-catalog')); });
  expect(await ui.findByTestId('orch-tool-demo.echo')).toBeTruthy();
});

it('opens Settings from the route param, read-only, with secret names never values', async () => {
  const ui = renderTab({ tab: 'settings' });
  expect(await ui.findByTestId('orch-set-access')).toBeTruthy();
  expect(ui.getByText('5. Anything that changes a system is confirmed')).toBeTruthy();
  expect(ui.getByText('To change these, open Orchestration › Settings on the computer running μcli.')).toBeTruthy();
  await waitFor(() => expect(api.command).not.toHaveBeenCalled());
});

it('explains when the server has no orchestration', async () => {
  const { ApiError } = jest.requireActual('../../src/api/client');
  api.overview.mockRejectedValue(new ApiError(404, 'Not Found'));
  const ui = renderTab();
  expect(await ui.findByTestId('orch-missing')).toBeTruthy();
});
