// The node dashboard and node groups on the phone (vm.fleet_dashboard, the group commands): the
// scope picker with each group's health, the dashboard's state sentence, meters and worst-first
// rows with reasons; moving nodes between groups from a long-press (a move when viewing a group);
// "Run on this group" aiming the run at the group; and the Groups tab's editor — members, a
// selector checked live (matched nodes shown and locked), rename and delete.
import React from 'react';
import { act, fireEvent, render, waitFor } from '@testing-library/react-native';
import { ThemeProvider } from '../../src/theme/ThemeContext';
import { ToastHost } from '../../src/ui';

jest.mock('../../src/api/orchestration', () => ({
  ...jest.requireActual('../../src/api/orchestration'),
  orchestrationApi: {
    nodes: jest.fn(), dashboard: jest.fn(), groups: jest.fn(), nodeMetrics: jest.fn(), dashboardMetrics: jest.fn(), command: jest.fn(), proposeAgentUpdate: jest.fn(),
    putGroup: jest.fn(), addToGroup: jest.fn(), removeFromGroup: jest.fn(), renameGroup: jest.fn(), deleteGroup: jest.fn(),
    previewSelector: jest.fn(), tools: jest.fn(), tool: jest.fn(), runbook: jest.fn(),
  },
  subscribeToOrchestrationChanges: jest.fn(() => ({ close: jest.fn() })),
}));

import { orchestrationApi } from '../../src/api/orchestration';
import { NodesView } from '../../src/screens/orchestration/NodesView';
import { GroupsView, healthWords } from '../../src/screens/orchestration/groups';

const api = orchestrationApi as unknown as Record<string, jest.Mock>;
const fx = (name: string) => JSON.parse(JSON.stringify(require(`../fixtures/server/orchestration/${name}.json`)));
const wrap = (node: React.ReactElement) => render(<ThemeProvider statusBar={false}><ToastHost>{node}</ToastHost></ThemeProvider>);
const ok = (data: Record<string, unknown> = {}) => ({ ok: true, command: 'x', data });
const groups = fx('groups');
const node = (name: string) => groups.nodes.find((n: any) => n.name === name);

beforeEach(() => {
  jest.clearAllMocks();
  (orchestrationApi as unknown as Record<string, jest.Mock>).nodeMetrics?.mockResolvedValue(JSON.parse(JSON.stringify(require('../fixtures/server/orchestration/node_metrics.json'))));
  (orchestrationApi as unknown as Record<string, jest.Mock>).dashboardMetrics?.mockResolvedValue(JSON.parse(JSON.stringify(require('../fixtures/server/orchestration/dashboard_metrics.json'))));
  api.nodes.mockResolvedValue(fx('nodes'));
  api.dashboard.mockImplementation(async (g: string | null) => fx(g == null ? 'dashboard' : g === '-' ? 'dashboard_ungrouped' : 'dashboard_group'));
  api.groups.mockResolvedValue(fx('groups'));
  for (const k of ['putGroup', 'addToGroup', 'renameGroup', 'deleteGroup', 'command']) api[k].mockResolvedValue(ok());
  api.removeFromGroup.mockResolvedValue(ok({ still_matched_by_selector: [] }));
  api.previewSelector.mockResolvedValue(ok({ ok: true, nodes: [] }));
  api.tools.mockResolvedValue(fx('tools'));
  api.tool.mockResolvedValue(fx('tool'));
});

describe('the node dashboard', () => {
  it('shows each group with its health, the scope in words, meters and the worst node first with why', async () => {
    const ui = wrap(<NodesView openNode={jest.fn()} openRun={jest.fn()} />);
    expect(await ui.findByTestId('orch-dashboard')).toBeTruthy();
    expect(ui.getByTestId('orch-scope-g-web').props.accessibilityHint).toBe('1 critical');
    expect(ui.getByTestId('orch-scope-g-canaries').props.accessibilityHint).toBe('all healthy');
    expect(ui.getByText(/3 nodes/)).toBeTruthy();
    expect(ui.getByText(/1 critical/)).toBeTruthy();
    expect(ui.getByTestId('orch-dashboard-meters')).toBeTruthy();
    expect(ui.getByText('disk 96% full')).toBeTruthy();                     // the reason, in words
    expect(ui.queryByTestId('orch-group-run-open')).toBeNull();            // the fleet isn't a group
    expect(healthWords({ ok: 2, warning: 0, critical: 0, pending: 0 })).toBe('all healthy');
  });

  it('switches scope to a group and runs a published tool on it', async () => {
    const openRun = jest.fn();
    api.command.mockImplementation(async (type: string) => (type === 'planRun'
      ? ok({ plan: { ok: true, nodes: ['a', 'b'], blast_radius: '2 nodes' } }) : ok({ run: { id: 'r_g', state: 'proposed' } })));
    const ui = wrap(<NodesView openNode={jest.fn()} openRun={openRun} />);
    await act(async () => { fireEvent.press(await ui.findByTestId('orch-scope-g-web')); });
    expect(api.dashboard).toHaveBeenLastCalledWith('web');
    await act(async () => { fireEvent.press(await ui.findByTestId('orch-group-run-open')); });
    await act(async () => { fireEvent.press(await ui.findByTestId('orch-group-run-demo.echo@1')); });
    expect(await ui.findByTestId('orch-run-sheet-groups')).toBeTruthy();
    await act(async () => { fireEvent.press(ui.getByTestId('orch-run-sheet-plan')); });
    expect(api.command).toHaveBeenCalledWith('planRun', expect.objectContaining({ target: 'demo.echo@1', selector: { groups: ['web'] } }));
    await act(async () => { fireEvent.press(await ui.findByTestId('orch-run-sheet-propose')); });
    expect(openRun).toHaveBeenCalledWith('r_g');
  });
});

describe('moving nodes between groups', () => {
  it('adds a node from the fleet view, and moves it when viewing a group it is a member of', async () => {
    const ui = wrap(<NodesView openNode={jest.fn()} openRun={jest.fn()} />);
    const db = node('db-new');
    await ui.findByTestId(`orch-node-${db.id}`);
    await act(async () => { fireEvent(ui.getByText('db-new'), 'longPress'); });   // long-press the row
    await act(async () => { fireEvent.press(await ui.findByTestId('action-sheet-move')); });
    await act(async () => { fireEvent.press(await ui.findByTestId('action-sheet-to-canaries')); });
    expect(api.addToGroup).toHaveBeenCalledWith('canaries', [db.id], undefined);

    // Viewing "canaries" (web-1 is a member): moving to another group takes it out of this one.
    api.dashboard.mockImplementation(async () => ({ ...fx('dashboard_group'), scope: { group: 'canaries', label: 'canaries' },
      nodes: fx('dashboard_group').nodes.filter((n: any) => n.name === 'web-1') }));
    await act(async () => { fireEvent.press(ui.getByTestId('orch-scope-g-canaries')); });
    const web1 = node('web-1');
    await act(async () => { fireEvent.press(await ui.findByTestId(`orch-node-${web1.id}-group`)); });
    await act(async () => { fireEvent.press(await ui.findByTestId('action-sheet-remove')); });
    expect(api.removeFromGroup).toHaveBeenCalledWith('canaries', [web1.id]);
  });

  it('says so when a removed node stays because the selector matches it', async () => {
    api.removeFromGroup.mockResolvedValue(ok({ still_matched_by_selector: [node('web-2').id] }));
    api.dashboard.mockImplementation(async () => fx('dashboard_group'));
    const ui = wrap(<NodesView openNode={jest.fn()} openRun={jest.fn()} />);
    await act(async () => { fireEvent.press(await ui.findByTestId('orch-scope-g-web')); });
    await act(async () => { fireEvent.press(await ui.findByTestId(`orch-node-${node('web-2').id}-group`)); });
    await act(async () => { fireEvent.press(await ui.findByTestId('action-sheet-remove')); });
    expect(await ui.findByText(/stays in web: its labels match the group’s selector/)).toBeTruthy();
  });
});

describe('the Groups tab', () => {
  it('lists groups with their health and how they choose nodes', async () => {
    const ui = wrap(<GroupsView />);
    expect(await ui.findByTestId('orch-group-web')).toBeTruthy();
    expect(ui.getByText(/2 nodes · 1 critical · selector role=web · Front ends/)).toBeTruthy();
    expect(ui.getByText(/1 node · all healthy · members only/)).toBeTruthy();
    expect(ui.getByTestId('orch-groups-ungrouped')).toBeTruthy();
  });

  it('creates a group from picked members', async () => {
    const ui = wrap(<GroupsView />);
    await act(async () => { fireEvent.press(await ui.findByTestId('orch-groups-new')); });
    fireEvent.changeText(ui.getByTestId('orch-group-name'), 'batch-1');
    await act(async () => { fireEvent.press(await ui.findByTestId(`orch-group-member-${node('db-new').id}`)); });
    await act(async () => { fireEvent.press(ui.getByTestId('orch-group-save')); });
    expect(api.putGroup).toHaveBeenCalledWith({ name: 'batch-1', selector: '', description: '', members: [node('db-new').id] });
  });

  it('checks the selector as you type, locks matched nodes, renames and saves', async () => {
    api.previewSelector.mockImplementation(async (labels: string) => (labels === 'role=web'
      ? ok({ ok: true, nodes: [{ id: node('web-1').id }, { id: node('web-2').id }] }) : ok({ ok: false, error: 'bad selector term' })));
    const ui = wrap(<GroupsView />);
    await act(async () => { fireEvent.press(await ui.findByTestId('orch-group-web')); });
    await waitFor(() => expect(ui.getByTestId(`orch-group-member-${node('web-2').id}`).props.accessibilityState).toMatchObject({ checked: true, disabled: true }));
    expect(ui.getByText(/web-2 · matched by the selector/)).toBeTruthy();

    fireEvent.changeText(ui.getByTestId('orch-group-selector'), 'role=');
    expect(await ui.findByText('bad selector term')).toBeTruthy();
    await act(async () => { fireEvent.press(ui.getByTestId('orch-group-save')); });
    expect(api.putGroup).not.toHaveBeenCalled();                              // an invalid selector can't be saved

    fireEvent.changeText(ui.getByTestId('orch-group-selector'), 'role=web');
    await waitFor(() => expect(ui.queryByText('bad selector term')).toBeNull());
    fireEvent.changeText(ui.getByTestId('orch-group-name'), 'frontend');
    await act(async () => { fireEvent.press(ui.getByTestId('orch-group-save')); });
    expect(api.renameGroup).toHaveBeenCalledWith('web', 'frontend');
    expect(api.putGroup).toHaveBeenCalledWith(expect.objectContaining({ name: 'frontend', selector: 'role=web', description: 'Front ends' }));
  });

  it('deletes a group only after confirming', async () => {
    const ui = wrap(<GroupsView />);
    await act(async () => { fireEvent.press(await ui.findByTestId('orch-group-canaries')); });
    await act(async () => { fireEvent.press(ui.getByTestId('orch-group-delete')); });
    expect(api.deleteGroup).not.toHaveBeenCalled();
    await act(async () => { fireEvent.press(ui.getByTestId('server-action-ok')); });
    expect(api.deleteGroup).toHaveBeenCalledWith('canaries');
  });
});
