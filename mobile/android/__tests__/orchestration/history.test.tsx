// Node history on the phone: the kit chart scales percentages to 0–100 and other units to their
// peak, leaves a gap where a node sent nothing, and reads out a summary sentence; the node screen
// fetches the chosen range and shows six charts (a banner when the agent predates history); the
// dashboard block charts the scope; polling runs only while the screen is focused and the app
// is in the foreground.
import React from 'react';
import { AppState } from 'react-native';
import { act, fireEvent, render, waitFor } from '@testing-library/react-native';
import { NavigationContext } from '@react-navigation/native';
import { ThemeProvider } from '../../src/theme/ThemeContext';
import { ToastHost, chartMax, chartPaths, chartSummary, formatMetric } from '../../src/ui';

jest.mock('../../src/api/orchestration', () => ({
  ...jest.requireActual('../../src/api/orchestration'),
  orchestrationApi: { nodeMetrics: jest.fn(), dashboardMetrics: jest.fn() },
  subscribeToOrchestrationChanges: jest.fn(() => ({ close: jest.fn() })),
}));

import { orchestrationApi } from '../../src/api/orchestration';
import { HISTORY_POLL_MS, NodeHistory, ScopeHistory } from '../../src/screens/orchestration/history';

const api = orchestrationApi as unknown as Record<string, jest.Mock>;
const fx = (name: string) => JSON.parse(JSON.stringify(require(`../fixtures/server/orchestration/${name}.json`)));
const wrap = (node: React.ReactElement) => render(<ThemeProvider statusBar={false}><ToastHost>{node}</ToastHost></ThemeProvider>);

beforeEach(() => {
  jest.clearAllMocks();
  api.nodeMetrics.mockImplementation(async (_id: string, range: string) => ({ ...fx('node_metrics'), range }));
  api.dashboardMetrics.mockResolvedValue(fx('dashboard_metrics'));
});

describe('the chart', () => {
  const m = fx('node_metrics');
  const opts = { since: m.since, until: m.until, bucketS: m.bucket_s, width: 300, height: 60 };

  it('formats values for their unit', () => {
    expect(formatMetric(42.4, 'percent')).toBe('42%');
    expect(formatMetric(1.5 * 2 ** 20, 'bytes_per_s')).toBe('1.5 MB/s');
    expect(formatMetric(512, 'bytes_per_s')).toBe('512 B/s');
    expect(formatMetric(0.734, 'number')).toBe('0.73');
    expect(formatMetric(undefined, 'percent')).toBe('–');
  });

  it('scales percentages to 0–100 and other units to their peak', () => {
    expect(chartMax([3, 7], 'percent')).toBe(100);
    expect(chartMax([1000, 2000], 'bytes_per_s')).toBeCloseTo(2200);
    expect(chartMax([], 'number')).toBe(1);
  });

  it('leaves a gap where the node sent nothing, instead of drawing across it', () => {
    const cpu = chartPaths(m.points, 'cpu_pct', { ...opts, max: 100 });
    expect(cpu).toHaveLength(2);                                   // the canary's half-hour off
    const disk = chartPaths(m.points, 'disk_pct', { ...opts, max: 100 });
    expect(disk[0].startsWith('M')).toBe(true);
    // 40% of a 60 px chart sits 36 px down.
    expect(disk[0].split(' ')[0].split(',')[1]).toBe('36.0');
    expect(chartPaths(m.points, 'nothing', { ...opts, max: 1 })).toEqual([]);
  });

  it('summarises the series in one sentence for screen readers', () => {
    const pts = [{ t: 1, cpu_pct: 10 }, { t: 2, cpu_pct: 40 }, { t: 3, cpu_pct: 12 }];
    expect(chartSummary('CPU', '6 hours', pts, [{ key: 'cpu_pct', label: 'CPU' }], 'percent'))
      .toBe('CPU over the last 6 hours: now 12%, average 21%, peak 40%');
    expect(chartSummary('Network', 'hour', [{ t: 1, in: 2048 }], [{ key: 'in', label: 'In' }, { key: 'out', label: 'Out' }], 'bytes_per_s'))
      .toBe('Network over the last hour: In now 2.0 kB/s, average 2.0 kB/s, peak 2.0 kB/s; Out: no data');
  });
});

describe('the node screen History', () => {
  it('fetches the default range, charts six series, and refetches when the range changes', async () => {
    const ui = wrap(<NodeHistory nodeId="n1" nodeName="web-1" />);
    await waitFor(() => expect(ui.getByTestId('orch-node-history-charts')).toBeTruthy());
    expect(api.nodeMetrics).toHaveBeenCalledWith('n1', '6h');
    for (const id of ['cpu', 'memory', 'disk', 'load', 'network', 'io']) expect(ui.getByTestId(`orch-node-history-charts-${id}`)).toBeTruthy();
    expect(ui.getByTestId('orch-node-history-charts-cpu').props.accessibilityLabel).toMatch(/^CPU over the last 6 hours: now \d+%/);
    await act(async () => { fireEvent.press(ui.getByTestId('orch-node-history-range-24h')); });
    expect(api.nodeMetrics).toHaveBeenLastCalledWith('n1', '24h');
  });

  it('says when the agent predates history and offers the update', async () => {
    api.nodeMetrics.mockResolvedValue(fx('node_metrics_outdated'));
    const update = jest.fn();
    const ui = wrap(<NodeHistory nodeId="n2" nodeName="web-2" onUpdateAgent={update} />);
    await waitFor(() => expect(ui.getByTestId('orch-node-history-outdated')).toBeTruthy());
    fireEvent.press(ui.getByTestId('orch-node-history-update'));
    expect(update).toHaveBeenCalled();
  });

  it('explains an empty history', async () => {
    api.nodeMetrics.mockResolvedValue({ ...fx('node_metrics'), points: [] });
    const ui = wrap(<NodeHistory nodeId="n1" nodeName="web-1" />);
    expect(await ui.findByTestId('orch-node-history-empty')).toBeTruthy();
  });
});

describe('the dashboard History', () => {
  it('charts the scope compactly and says how nodes combine', async () => {
    const ui = wrap(<ScopeHistory scope="web" />);
    await waitFor(() => expect(ui.getByTestId('orch-dashboard-history-charts')).toBeTruthy());
    expect(api.dashboardMetrics).toHaveBeenCalledWith('web', '6h');
    expect(ui.getByText(/averaged across 2 nodes; network and disk I\/O are summed; disk is the fullest/)).toBeTruthy();
    expect(ui.queryByTestId('orch-dashboard-history-charts-load')).toBeNull();           // compact: four charts
  });

  it('names the nodes that have no history yet', async () => {
    api.dashboardMetrics.mockResolvedValue({ ...fx('dashboard_metrics'), needs_update: ['old-1'] });
    const ui = wrap(<ScopeHistory scope={null} />);
    expect(await ui.findByText(/1 node without history \(agent before 1.3.0\): old-1/)).toBeTruthy();
  });
});

describe('polling', () => {
  afterEach(() => { jest.useRealTimers(); });

  it('polls while focused and in the foreground, and stops when the screen loses focus or the app goes background', async () => {
    jest.useFakeTimers();
    const listeners: Record<string, Array<() => void>> = {};
    let focused = true;
    const navigation = {
      isFocused: () => focused,
      addListener: (name: string, fn: () => void) => { (listeners[name] ||= []).push(fn); return () => { listeners[name] = listeners[name].filter(f => f !== fn); }; },
    };
    let appListener: ((s: string) => void) | null = null;
    jest.spyOn(AppState, 'addEventListener').mockImplementation(((_t: string, fn: (s: string) => void) => { appListener = fn; return { remove: jest.fn() }; }) as never);
    wrap(<NavigationContext.Provider value={navigation as never}><NodeHistory nodeId="n1" nodeName="web-1" /></NavigationContext.Provider>);
    await act(async () => undefined);
    expect(api.nodeMetrics).toHaveBeenCalledTimes(1);
    await act(async () => { jest.advanceTimersByTime(HISTORY_POLL_MS); });
    expect(api.nodeMetrics).toHaveBeenCalledTimes(2);

    focused = false;
    await act(async () => { listeners.blur.forEach(fn => fn()); });
    await act(async () => { jest.advanceTimersByTime(HISTORY_POLL_MS * 3); });
    expect(api.nodeMetrics).toHaveBeenCalledTimes(2);                                   // not on screen: no polls

    focused = true;
    await act(async () => { listeners.focus.forEach(fn => fn()); });
    await act(async () => { jest.advanceTimersByTime(HISTORY_POLL_MS); });
    expect(api.nodeMetrics).toHaveBeenCalledTimes(3);
    await act(async () => { appListener?.('background'); });
    await act(async () => { jest.advanceTimersByTime(HISTORY_POLL_MS * 3); });
    expect(api.nodeMetrics).toHaveBeenCalledTimes(3);                                   // backgrounded: no polls
  });
});
