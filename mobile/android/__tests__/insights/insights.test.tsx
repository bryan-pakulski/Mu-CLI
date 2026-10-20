// Insights (mobile_parity.md §6 Insights): Runs, Jobs and Costs as tabs of one page; the Costs tab
// edits the pricing registry (fixture captured from utils.model_pricing.pricing_catalog by
// scripts/fixtures/capture_environments.py); Jobs lists GET /api/jobs/history and opens analysis.
import React from 'react';
import { AccessibilityInfo } from 'react-native';
import { act, fireEvent, render, waitFor } from '@testing-library/react-native';
import pricing from './fixtures/pricing.json';

const mockNavigate = jest.fn();
let mockParams: Record<string, unknown> | undefined;
jest.mock('@react-navigation/native', () => ({
  useNavigation: () => ({ navigate: mockNavigate }),
  useRoute: () => ({ params: mockParams }),
  useIsFocused: () => true,
  useFocusEffect: (fn: () => void) => { const React = require('react'); React.useEffect(fn, []); },
}));
jest.mock('../../src/api/costs', () => ({
  BILLING_MODES: ['token', 'estimated_token', 'local', 'unknown'],
  costsApi: { get: jest.fn(), save: jest.fn(), reset: jest.fn() },
}));
jest.mock('../../src/api/client', () => ({ api: { get: jest.fn() } }));
jest.mock('../../src/api/traces', () => ({ tracesApi: { list: jest.fn(), getSession: jest.fn(), getRun: jest.fn() } }));

import { api } from '../../src/api/client';
import { costsApi } from '../../src/api/costs';
import { tracesApi } from '../../src/api/traces';
import { useConnectionStore } from '../../src/store/connection';
import { ThemeProvider } from '../../src/theme/ThemeContext';
import { InsightsScreen } from '../../src/screens/insights/InsightsScreen';
import { formatContext, formatRate, modelSub, parseNumber } from '../../src/screens/insights/CostsScreen';
import { jobFacts } from '../../src/screens/insights/JobsInsightsScreen';

const costs = costsApi as unknown as Record<string, jest.Mock>;
const traces = tracesApi as unknown as Record<string, jest.Mock>;
const get = (api as unknown as { get: jest.Mock }).get;
const wrap = () => render(<ThemeProvider statusBar={false}><InsightsScreen /></ThemeProvider>);
const catalog = () => JSON.parse(JSON.stringify(pricing));
const first = (pricing as any).models[0];

const HISTORY = {
  jobs: [
    { id: 'j-1', title: 'Fix flaky login test', status: 'merged', created_at: Date.now() / 1000 - 7200, elapsed_seconds: 754, cost_usd: 0.42 },
    { id: 'j-2', title: 'Upgrade dependencies', status: 'implementing', archived: false, created_at: Date.now() / 1000 - 120, elapsed_seconds: 0, cost_usd: 0 },
  ],
};

beforeEach(() => {
  jest.spyOn(AccessibilityInfo, 'isReduceMotionEnabled').mockResolvedValue(true);
  Object.values(costs).forEach(fn => fn.mockReset());
  Object.values(traces).forEach(fn => fn.mockReset());
  get.mockReset();
  mockNavigate.mockReset();
  mockParams = undefined;
  costs.get.mockResolvedValue(catalog());
  get.mockResolvedValue(HISTORY);
  traces.list.mockResolvedValue([]);
  useConnectionStore.setState({ activeSessionName: null });
});

describe('pricing words', () => {
  it('formats rates, context and the row line', () => {
    expect(formatRate(null)).toBe('—');
    expect(formatRate(3)).toBe('$3.00');
    expect(formatRate(0.075)).toBe('$0.075');
    expect(formatContext(200_000)).toBe('200K context');
    expect(formatContext(1_048_576)).toBe('1M context');
    expect(modelSub({ ...first, provider: 'ollama', billing: 'local', context_window: null })).toBe('ollama · Local · $0 API cost');
    expect(parseNumber('-1').error).toBeTruthy();
    expect(parseNumber('1.5', true).error).toBeTruthy();
    expect(parseNumber('').value).toBeNull();
  });
});

describe('InsightsScreen', () => {
  it('opens on Runs, and on the tab navigation asks for', async () => {
    const ui = wrap();
    expect(await ui.findByText('No session traces')).toBeTruthy();
    mockParams = { tab: 'costs' };
    const costsUi = wrap();
    expect(await costsUi.findByTestId('insights-costs')).toBeTruthy();
    expect(costsUi.getByTestId('insights-header-tabs-costs').props.accessibilityState).toMatchObject({ selected: true });
  });

  it('switches tabs from the header', async () => {
    const ui = wrap();
    await act(async () => { fireEvent.press(ui.getByTestId('insights-header-tabs-jobs')); });
    expect(await ui.findByText('Fix flaky login test')).toBeTruthy();
  });
});

describe('Runs', () => {
  // Shape of GET /api/traces/{run_id} (mu/gui/routers/traces.py): summary + series.
  const run = { run_id: 'r-abcdef12', session: 'refactor-auth', model: 'claude-fable-5-1', provider: 'anthropic', mode: 'agent', context_limit: 200000, max_iterations: 40, iters: 12, bytes: 1, file: 'x' };
  const summary = { ...run, total_in: 120000, total_out: 8000, total_cost: 0.3, compaction_count: 1, compaction_by_kind: {}, mechanical_fallback_count: 0, nudge_count: 2,
    nudge_by_kind: {}, nudges_broken: 0, subagent_iters: 0, peak_context: 50000, peak_estimated: 48000, peak_drift_abs: 0, mean_drift: 0, median_drift: 0,
    total_wall_ms: 64000, peak_wall_ms: 9000, mean_wall_ms: 5300, tool_calls: 31, request_count: 12, context_artifact_counts: {}, redundant_reads: 0, status: 'completed' };

  it('shows the newest run: who ran it, how far it got, and its charts', async () => {
    traces.list.mockResolvedValue([run, { ...run, run_id: 'r-2', mode: 'plan', iters: 3 }]);
    traces.getRun.mockResolvedValue({ run_id: run.run_id, summary, series: { context: [{ iter: 1, total_est: 1000, real: 1200 }], tool_histogram: [{ name: 'read_file', count: 9 }] } });
    const ui = wrap();
    expect(await ui.findByText('anthropic / claude-fable-5-1', { exact: false })).toBeTruthy();
    expect(ui.getByText('completed', { exact: false })).toBeTruthy();
    expect(ui.getByText('read_file')).toBeTruthy();
    traces.getRun.mockResolvedValue({ run_id: 'r-2', summary: { ...summary, run_id: 'r-2', mode: 'plan', iters: 3 }, series: {} });
    await act(async () => { fireEvent.press(ui.getByText('plan · 3 iters')); });
    expect(traces.getRun).toHaveBeenLastCalledWith('r-2', 96);
  });
});

describe('Jobs', () => {
  it('lists every job with its facts and opens its analysis', async () => {
    mockParams = { tab: 'jobs' };
    const ui = wrap();
    expect(await ui.findByText('Fix flaky login test')).toBeTruthy();
    expect(get).toHaveBeenCalledWith('/api/jobs/history', { query: expect.objectContaining({ scope: 'all', archive: 'all', sort: 'created' }) });
    expect(ui.getByText(jobFacts(HISTORY.jobs[0]))).toBeTruthy();
    expect(jobFacts(HISTORY.jobs[0])).toContain('12m 34s');
    expect(jobFacts(HISTORY.jobs[0])).toContain('$0.42');
    fireEvent.changeText(ui.getByTestId('insights-jobs-filter'), 'upgrade');
    expect(ui.queryByText('Fix flaky login test')).toBeNull();
    fireEvent.press(ui.getByText('Upgrade dependencies'));
    expect(mockNavigate).toHaveBeenCalledWith('JobAnalysis', { jobId: 'j-2' });
  });
});

describe('Costs', () => {
  beforeEach(() => { mockParams = { tab: 'costs' }; });

  it('filters by provider and edits a rate, saving only when asked', async () => {
    costs.save.mockImplementation(async (_c: unknown, models: unknown[]) => ({ ...catalog(), models, using_override: true }));
    const ui = wrap();
    expect(await ui.findByText(first.key)).toBeTruthy();
    const other = (pricing as any).models.find((m: any) => m.provider !== first.provider);
    fireEvent.press(ui.getByTestId(`costs-provider-${first.provider}`));
    expect(ui.queryByText(other.key)).toBeNull();
    expect(ui.queryByTestId('costs-save')).toBeNull();

    fireEvent.press(ui.getByText(first.key));
    fireEvent.changeText(ui.getByTestId('costs-field-input_per_million'), '-2');
    fireEvent.press(ui.getByTestId('costs-sheet-done'));
    expect(ui.getByTestId('costs-sheet-problem')).toBeTruthy();
    fireEvent.changeText(ui.getByTestId('costs-field-input_per_million'), '9.5');
    await act(async () => { fireEvent.press(ui.getByTestId('costs-sheet-done')); });
    expect(ui.getByText('Unsaved changes', { exact: false })).toBeTruthy();
    expect(costs.save).not.toHaveBeenCalled();

    await act(async () => { fireEvent.press(ui.getByTestId('costs-save')); });
    const saved = costs.save.mock.calls[0][1] as any[];
    expect(saved.find(m => m.key === first.key).input_per_million).toBe(9.5);
    expect(saved).toHaveLength((pricing as any).models.length);
    await waitFor(() => expect(ui.queryByTestId('costs-save')).toBeNull());
  });

  it('adds a model and refuses a duplicate', async () => {
    const ui = wrap();
    await ui.findByText(first.key);
    fireEvent.press(ui.getByTestId('costs-add'));
    fireEvent.changeText(ui.getByTestId('costs-field-provider'), first.provider);
    fireEvent.changeText(ui.getByTestId('costs-field-key'), first.key);
    fireEvent.press(ui.getByTestId('costs-sheet-done'));
    expect(ui.getByText(`${first.provider}/${first.key} is already in the registry.`)).toBeTruthy();
    fireEvent.changeText(ui.getByTestId('costs-field-key'), 'my-new-model');
    await act(async () => { fireEvent.press(ui.getByTestId('costs-sheet-done')); });
    expect(ui.getByText('my-new-model')).toBeTruthy();
  });

  it('resets to the packaged defaults only after confirming', async () => {
    costs.reset.mockResolvedValue(catalog());
    const ui = wrap();
    await ui.findByText(first.key);
    await act(async () => { fireEvent.press(ui.getByTestId('costs-reset')); });
    expect(costs.reset).not.toHaveBeenCalled();
    await act(async () => { fireEvent.press(ui.getByTestId('server-action-ok')); });
    expect(costs.reset).toHaveBeenCalled();
  });
});
