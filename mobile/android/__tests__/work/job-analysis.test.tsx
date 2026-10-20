// Job performance: the headline numbers in the state sentence, then sections of rows.
import React from 'react';
import { act, render } from '@testing-library/react-native';

jest.mock('../../src/api/client', () => ({ ...jest.requireActual('../../src/api/client'), api: { get: jest.fn() } }));

import { api } from '../../src/api/client';
import { JobAnalysisScreen } from '../../src/screens/JobAnalysisScreen';
import { ThemeProvider } from '../../src/theme/ThemeContext';

const summary = {
  elapsed_seconds: 3725, active_seconds: 600, waiting_seconds: 0, verification_seconds: 0, passive_seconds: 3000, stopped_seconds: 125,
  attempts: 2, retries: 1, cost_usd: 1.5, model_api_cost_usd: 1.25, billing_modes: ['estimated_token'], tokens: { input: 1200 },
  tool_calls: 40, unique_tools: 5, human_gates: 1, human_responses: 1, verification_runs: 2, verification_passes: 1, verification_failures: 1,
  first_pass_verification: false, failures: 2, recoveries: 1, changed_files: 3, additions: 10, deletions: 4, waiting_ratio: null,
};
const analysis = {
  job: { id: 'j1', title: 'Fix the flaky test', status: 'merged' }, summary,
  phase_breakdown: [{ status: 'implementing', seconds: 600, percent: 16.1, occurrences: 1 }],
  attempts: [{ number: 1, status: 'failed', duration_seconds: 300, cost_usd: 0.5, tool_calls: 10, agent_messages: 4, runtime_errors: 1, tokens: {} }],
  tools: [{ name: 'read_file', count: 12, share: 0.3 }],
  verifications: [{ status: 'failed', passed: false, duration_seconds: 30, checks: 3, checks_passed: 2, changed_files: 3, additions: 1, deletions: 0, dirty: false }],
  human_gates: [{ event_id: 7, created_at: 1, reason: 'Approve the plan' }],
  failures: [{ event_id: 9, created_at: 2, event_type: 'checks_failed', summary: '1 new failure' }],
};

it('loads the analysis and shows it as a header and rows', async () => {
  (api.get as jest.Mock).mockResolvedValue({ analysis });
  const navigation = { canGoBack: () => true, goBack: jest.fn() };
  const ui = render(<ThemeProvider statusBar={false}><JobAnalysisScreen route={{ params: { jobId: 'j1' } } as never} navigation={navigation as never} /></ThemeProvider>);
  await act(async () => undefined);
  expect((api.get as jest.Mock).mock.calls[0][0]).toBe('/api/jobs/j1/analysis');
  expect(ui.getByText('Fix the flaky test')).toBeTruthy();
  expect(ui.getByText('1h 2m 5s wall · 10m 0s active', { exact: false })).toBeTruthy();
  expect(ui.getByText('2 failures', { exact: false })).toBeTruthy();
  expect(ui.getByText('Cost (estimate)')).toBeTruthy();
  expect(ui.getByText('Needed repair')).toBeTruthy();
  expect(ui.getByText('Approve the plan')).toBeTruthy();
  expect(ui.getByText('1 new failure')).toBeTruthy();
});

it('says so when the analysis cannot be loaded', async () => {
  (api.get as jest.Mock).mockRejectedValue(new Error('job not found'));
  const ui = render(<ThemeProvider statusBar={false}><JobAnalysisScreen route={{ params: { jobId: 'nope' } } as never} navigation={{ canGoBack: () => false } as never} /></ThemeProvider>);
  await act(async () => undefined);
  expect(ui.getByText('job not found')).toBeTruthy();
});
