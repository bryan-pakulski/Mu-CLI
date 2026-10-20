// A run on the phone, from the server's view models: the state sentence, the grid (one glyph per
// step with its vocabulary label; a node's executions with log and outputs; retry on failed nodes),
// the plan, and the results — one sortable table across nodes, shared as CSV.
import React from 'react';
import { Share } from 'react-native';
import { act, fireEvent, render, within } from '@testing-library/react-native';
import { ThemeProvider } from '../../src/theme/ThemeContext';

jest.mock('../../src/api/orchestration', () => ({
  ...jest.requireActual('../../src/api/orchestration'),
  orchestrationApi: { run: jest.fn(), runResults: jest.fn(), runResultsCsv: jest.fn(), command: jest.fn() },
  subscribeToOrchestrationChanges: jest.fn(() => ({ close: jest.fn() })),
}));

import { orchestrationApi } from '../../src/api/orchestration';
import { OrchRunScreen, runState } from '../../src/screens/orchestration/RunScreen';
import { ResultsTable, filterRows, formatCell, nextSort, sortRows, type ResultTable } from '../../src/ui/ResultsTable';

const api = orchestrationApi as unknown as Record<string, jest.Mock>;
const fx = (name: string) => JSON.parse(JSON.stringify(require(`../fixtures/server/orchestration/${name}.json`)));
const navigation = { navigate: jest.fn(), goBack: jest.fn() };
const wrap = (node: React.ReactElement) => render(<ThemeProvider statusBar={false}>{node}</ThemeProvider>);

beforeEach(() => {
  jest.clearAllMocks();
  api.command.mockResolvedValue({ ok: true, command: 'x', data: {} });
  api.runResults.mockResolvedValue(fx('run_results'));
});

describe('run detail', () => {
  it('says the state, effect and progress, and shows each node with its step glyphs and executions', async () => {
    const run = fx('run');
    api.run.mockResolvedValue(run);
    expect(runState(run).map(p => p.text)).toEqual(expect.arrayContaining(['Succeeded', run.effect_style.label, '2 of 2 done']));
    const ui = wrap(<OrchRunScreen route={{ params: { runId: run.id } }} navigation={navigation} />);
    const row = run.grid[0];
    expect(await ui.findByTestId(`orch-run-node-${row.node_id}`)).toBeTruthy();
    expect(ui.getByTestId(`orch-cell-${row.node_id}-0`).props.accessibilityLabel).toBe('succeeded');
    await act(async () => { fireEvent.press(ui.getByText(row.name)); });
    const execs = ui.getByTestId(`orch-run-node-${row.node_id}-execs`);
    expect(within(execs).getByText(/hello from web-1/)).toBeTruthy();
    expect(within(execs).getByTestId(/-log$/).props.children).toContain('message: hello');
  });

  it('offers only the actions the server allows; cancelling asks first', async () => {
    const run = fx('run_proposed');
    api.run.mockResolvedValue(run);
    const ui = wrap(<OrchRunScreen route={{ params: { runId: run.id } }} navigation={navigation} />);
    await act(async () => { fireEvent.press(await ui.findByTestId('orch-run-actions-approveRun')); });
    expect(api.command).toHaveBeenCalledWith('approveRun', { run_id: run.id });
    await act(async () => { fireEvent.press(ui.getByTestId('orch-run-actions-more')); });
    expect(ui.getByText('run is not paused')).toBeTruthy();
    await act(async () => { fireEvent.press(ui.getByText('Cancel')); });
    expect(ui.getByText(/Queued work is dropped/)).toBeTruthy();
    await act(async () => { fireEvent.press(ui.getByTestId('server-action-ok')); });
    expect(api.command).toHaveBeenCalledWith('cancelRun', { run_id: run.id });
  });

  it('retries a failed node on a running run', async () => {
    const run = fx('run');
    run.state = 'running';
    run.grid[1].failed = true;
    run.grid[1].progress = 'failed';
    api.run.mockResolvedValue(run);
    const ui = wrap(<OrchRunScreen route={{ params: { runId: run.id } }} navigation={navigation} />);
    await act(async () => { fireEvent.press(await ui.findByText(run.grid[1].name)); });
    await act(async () => { fireEvent.press(ui.getByTestId(`orch-retry-${run.grid[1].node_id}`)); });
    await act(async () => { fireEvent.press(ui.getByTestId('server-action-ok')); });
    expect(api.command).toHaveBeenCalledWith('retryNode', { run_id: run.id, node_id: run.grid[1].node_id });
  });

  it('shows the plan: decision, batches and excluded nodes', async () => {
    const run = fx('run_proposed');
    run.excluded = [{ node_id: 'n_x', name: 'db-new', reason: 'node is pending' }];
    api.run.mockResolvedValue(run);
    const ui = wrap(<OrchRunScreen route={{ params: { runId: run.id, tab: 'plan' } }} navigation={navigation} />);
    expect(await ui.findByText('Needs your approval')).toBeTruthy();
    expect(within(ui.getByTestId('orch-run-batches')).getByText('Canary')).toBeTruthy();
    expect(within(ui.getByTestId('orch-run-excluded')).getByText('node is pending')).toBeTruthy();
  });

  it('shows results across nodes and shares the view as CSV', async () => {
    const run = fx('run');
    api.run.mockResolvedValue(run);
    api.runResultsCsv.mockResolvedValue('node,message\nweb-1,hello from web-1\n');
    const share = jest.spyOn(Share, 'share').mockResolvedValue({ action: 'sharedAction' } as never);
    const ui = wrap(<OrchRunScreen route={{ params: { runId: run.id, tab: 'results' } }} navigation={navigation} />);
    expect(await ui.findByText('hello from web-2')).toBeTruthy();
    expect(ui.getByTestId('orch-run-results-meta').props.children.join('')).toContain('2 of 2 nodes reported');
    await act(async () => { fireEvent.press(ui.getByTestId('orch-run-results-csv')); });
    expect(api.runResultsCsv).toHaveBeenCalledWith(run.id, 0, 'summary');
    expect(share).toHaveBeenCalledWith(expect.objectContaining({ message: expect.stringContaining('web-1,hello from web-1') }));
  });
});

describe('results table', () => {
  const table = (): ResultTable => ({
    key: 'summary', label: 'Per node', per: 'node', metric: 'disk', order: 'desc',
    columns: [{ key: 'disk', label: 'Disk', kind: 'number', format: 'percent' }, { key: 'free', label: 'Free', kind: 'integer', format: 'bytes' }],
    rows: [
      { exec_id: 'a', node: 'web-1', state: 'succeeded', values: { disk: 40, free: 2048 } },
      { exec_id: 'b', node: 'web-2', state: 'succeeded', values: { disk: 95, free: null } },
      { exec_id: 'c', node: 'db-1', state: 'failed', values: { disk: null, free: 10 } },
    ],
  });

  it('sorts by the key metric first, keeps blanks last either way, and toggles a column', () => {
    const t = table();
    expect(sortRows(t, { sort: '', dir: 'asc' }).map(r => r.node)).toEqual(['web-2', 'web-1', 'db-1']);
    let st = nextSort(t, { sort: '', dir: 'asc' }, 'disk');
    expect(st).toEqual({ sort: 'disk', dir: 'asc' });
    expect(sortRows(t, st).map(r => r.node)).toEqual(['web-1', 'web-2', 'db-1']);
    st = nextSort(t, st, '__node');
    expect(sortRows(t, st).map(r => r.node)).toEqual(['db-1', 'web-1', 'web-2']);
    expect(filterRows(t.rows, 'web-2').map(r => r.exec_id)).toEqual(['b']);
  });

  it('formats values as the web does', () => {
    const [disk, free] = table().columns;
    expect(formatCell(disk, 95)).toBe('95%');
    expect(formatCell(free, 2048)).toBe('2.0 KB');
    expect(formatCell(free, null)).toBe('—');
    expect(formatCell({ key: 'd', label: 'D', kind: 'integer', format: 'days' }, 1)).toBe('1 day');
  });

  it('sorts on tap, marks failed rows and charts the metric', async () => {
    const ui = wrap(<ResultsTable data={{ tables: [table()] }} testID="rt" />);
    expect(within(ui.getByTestId('rt-row-c')).getByText('failed')).toBeTruthy();
    await act(async () => { fireEvent.press(ui.getByTestId('rt-sort-__node')); });
    expect(ui.getByTestId('rt-sort-__node').props.accessibilityState).toMatchObject({ selected: true });
    await act(async () => { fireEvent.press(ui.getByTestId('rt-mode-chart')); });
    expect(within(ui.getByTestId('rt-chart')).getByText('Disk by node')).toBeTruthy();
    expect(within(ui.getByTestId('rt-chart')).getByLabelText('web-2: 95%')).toBeTruthy();
  });

  it('says so when no node has reported', () => {
    const t = table();
    t.rows.forEach(r => { r.values = { disk: null, free: null }; });
    const ui = wrap(<ResultsTable data={{ tables: [t] }} empty="Nothing yet." />);
    expect(ui.getByText('Nothing yet.')).toBeTruthy();
  });
});
