// The node dashboard's health rows carry each node's own mini charts (CPU, memory, network from
// the same per-node metrics poll as the History block), so the nodes working hardest are visible
// without opening each one; the rows sort by health or by CPU, memory or network now.
import React from 'react';
import { act, fireEvent, render, waitFor } from '@testing-library/react-native';
import { StyleSheet } from 'react-native';
import { ThemeProvider } from '../../src/theme/ThemeContext';
import { paperColors } from '../../src/theme/tokens';

jest.mock('../../src/api/orchestration', () => ({
  ...jest.requireActual('../../src/api/orchestration'),
  orchestrationApi: { nodes: jest.fn(), dashboard: jest.fn(), groups: jest.fn(), dashboardMetrics: jest.fn(), command: jest.fn(), proposeAgentUpdate: jest.fn() },
  subscribeToOrchestrationChanges: jest.fn(() => ({ close: jest.fn() })),
}));

import { orchestrationApi } from '../../src/api/orchestration';
import { NodesView, sortRows, sparkWords } from '../../src/screens/orchestration/NodesView';

const api = orchestrationApi as unknown as Record<string, jest.Mock>;
const fx = (name: string) => JSON.parse(JSON.stringify(require(`../fixtures/server/orchestration/${name}.json`)));
const wrap = (node: React.ReactElement) => render(<ThemeProvider statusBar={false}>{node}</ThemeProvider>);
const byName = (name: string) => fx('dashboard').nodes.find((n: any) => n.name === name);

beforeEach(() => {
  jest.clearAllMocks();
  api.nodes.mockResolvedValue(fx('nodes'));
  api.dashboard.mockResolvedValue(fx('dashboard'));
  api.dashboardMetrics.mockResolvedValue(fx('dashboard_metrics_per_node'));
});

it('asks for the per-node series once, with the History block', async () => {
  const ui = wrap(<NodesView openNode={jest.fn()} openRun={jest.fn()} />);
  await ui.findByTestId(`orch-node-${byName('web-2').id}-sparks`);
  expect(api.dashboardMetrics).toHaveBeenCalledWith(null, '6h', true);
  expect(api.dashboardMetrics).toHaveBeenCalledTimes(1);
});

it('shows each node its own CPU, memory and network with the current values, high use marked', async () => {
  const web2 = byName('web-2');
  const ui = wrap(<NodesView openNode={jest.fn()} openRun={jest.fn()} />);
  await ui.findByTestId(`orch-node-${web2.id}-sparks`);
  expect(ui.getByTestId(`orch-node-${web2.id}-cpu-value`).props.children.join('')).toBe('CPU 70%');
  expect(ui.getByTestId(`orch-node-${web2.id}-mem-value`).props.children.join('')).toBe('Mem 60%');
  expect(ui.getByTestId(`orch-node-${web2.id}-net-value`).props.children.join('')).toBe('Net 78.1 kB/s');
  expect(ui.queryByTestId(`orch-node-${byName('db-new').id}-sparks`)).toBeNull();       // pending: no charts
  // Busy rows are marked in words for screen readers.
  const row = ui.getByTestId(`orch-node-${web2.id}`);
  expect(JSON.stringify(row.findAll((n: any) => typeof n.props.accessibilityLabel === 'string').map((n: any) => n.props.accessibilityLabel)))
    .toContain('CPU 70%, memory 60%, network 78.1 kB/s');
});

it('marks CPU at or above 85% and says "no history" for a node that sends none', async () => {
  const data = fx('dashboard_metrics_per_node');
  const web1 = byName('web-1');
  const web2 = byName('web-2');
  data.per_node[web2.id].latest.cpu_pct = 97;
  data.per_node[web2.id].points.push({ t: data.until, cpu_pct: 97 });
  data.per_node[web1.id] = { points: [], latest: { cpu_pct: null, mem_pct: null, net_bps: null, io_bps: null }, peak: { cpu_pct: null, mem_pct: null, net_bps: null, io_bps: null } };
  api.dashboardMetrics.mockResolvedValue(data);
  const ui = wrap(<NodesView openNode={jest.fn()} openRun={jest.fn()} />);
  expect(await ui.findByTestId(`orch-node-${web1.id}-nohistory`)).toBeTruthy();
  const color = (id: string) => StyleSheet.flatten(ui.getByTestId(id).props.style).color;
  expect(color(`orch-node-${web2.id}-cpu-value`)).toBe(paperColors.danger.ink);
  expect(color(`orch-node-${web2.id}-mem-value`)).toBe(paperColors.ink2);
});

it('sorts by CPU so the busiest node comes first, and back to health order', async () => {
  const data = fx('dashboard_metrics_per_node');
  const web1 = byName('web-1');
  data.per_node[web1.id].latest.cpu_pct = 99;
  api.dashboardMetrics.mockResolvedValue(data);
  const ui = wrap(<NodesView openNode={jest.fn()} openRun={jest.fn()} />);
  await ui.findByTestId(`orch-node-${web1.id}-sparks`);
  const order = () => ui.getAllByTestId(/^orch-node-[^-]+$/).map(e => e.props.testID);
  expect(order()[0]).toBe(`orch-node-${byName('web-2').id}`);                            // health: the critical one first
  await act(async () => { fireEvent.press(ui.getByTestId('orch-nodes-sort-cpu')); });
  await waitFor(() => expect(order()[0]).toBe(`orch-node-${web1.id}`));
  expect(order()[order().length - 1]).toBe(`orch-node-${byName('db-new').id}`);         // no history: last
  await act(async () => { fireEvent.press(ui.getByTestId('orch-nodes-sort-health')); });
  expect(order()[0]).toBe(`orch-node-${byName('web-2').id}`);
});

it('sortRows and sparkWords', () => {
  const spark = { a: { points: [{ t: 1 }], latest: { cpu_pct: 10, mem_pct: 95, net_bps: 5, io_bps: null }, peak: {} },
                  b: { points: [{ t: 1 }], latest: { cpu_pct: 50, mem_pct: 20, net_bps: 900, io_bps: null }, peak: {} } } as never;
  const rows = [{ id: 'c' }, { id: 'a' }, { id: 'b' }];
  expect(sortRows(rows, 'cpu', spark).map(r => r.id)).toEqual(['b', 'a', 'c']);
  expect(sortRows(rows, 'memory', spark).map(r => r.id)).toEqual(['a', 'b', 'c']);
  expect(sortRows(rows, 'network', spark).map(r => r.id)).toEqual(['b', 'a', 'c']);
  expect(sortRows(rows, 'health', spark)).toBe(rows);
  expect(sparkWords(undefined)).toBe('no history');
});
