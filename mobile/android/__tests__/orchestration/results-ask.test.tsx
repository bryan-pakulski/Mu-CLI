// "Ask about this" on fleet results: the fleet chat opens with a question naming the run (or tool),
// the table view and the filter, left in the composer for the user to send.
import React from 'react';
import { act, fireEvent, render } from '@testing-library/react-native';
import { ThemeProvider } from '../../src/theme/ThemeContext';

jest.mock('../../src/api/orchestration', () => ({
  ...jest.requireActual('../../src/api/orchestration'),
  orchestrationApi: { run: jest.fn(), runResults: jest.fn(), runResultsCsv: jest.fn(), command: jest.fn() },
  subscribeToOrchestrationChanges: jest.fn(() => ({ close: jest.fn() })),
}));
jest.mock('../../src/screens/orchestration/AskSheets', () => {
  const actual = jest.requireActual('../../src/screens/orchestration/AskSheets');
  const R = require('react');
  const { Text: T } = require('react-native');
  return { ...actual, FleetChatSheet: ({ visible, prefill }: { visible: boolean; prefill?: string }) => (visible ? R.createElement(T, { testID: 'fleet-chat-prefill' }, prefill) : null) };
});

import { orchestrationApi } from '../../src/api/orchestration';
import { resultsQuestion } from '../../src/screens/orchestration/AskSheets';
import { OrchRunScreen } from '../../src/screens/orchestration/RunScreen';
import { ResultsTable } from '../../src/ui/ResultsTable';

const api = orchestrationApi as unknown as Record<string, jest.Mock>;
const fx = (name: string) => JSON.parse(JSON.stringify(require(`../fixtures/server/orchestration/${name}.json`)));
const wrap = (node: React.ReactElement) => render(<ThemeProvider statusBar={false}>{node}</ThemeProvider>);

it('words the question like the web, for a run or a tool', () => {
  expect(resultsQuestion({ run: { id: 'r1', target: 'sys.disk@1' } }, { label: 'Summary', filter: '' }))
    .toBe('Look at the fleet results of run r1 (sys.disk@1, Summary view) with fleet_results and tell me what needs attention.');
  expect(resultsQuestion({ toolId: 'sys.disk' }, { label: 'Mounts', filter: 'web' }))
    .toBe('Look at the latest sys.disk results across the fleet (Mounts view, filtered by "web") with fleet_results and tell me what needs attention.');
});

it('hands the current view to onAsk', async () => {
  const onAsk = jest.fn();
  const data = fx('run_results');
  const ui = wrap(<ResultsTable data={data} onAsk={onAsk} testID="rt" />);
  fireEvent.press(ui.getByTestId('rt-ask'));
  const step = data.steps[data.steps.length - 1];
  expect(onAsk).toHaveBeenCalledWith({ view: step.tables[0].key, label: step.tables[0].label, step: step.step, filter: '' });
  await act(async () => undefined);
});

it('opens the fleet chat with the question from a run\'s results', async () => {
  const run = fx('run');
  api.run.mockResolvedValue(run);
  api.runResults.mockResolvedValue(fx('run_results'));
  api.command.mockResolvedValue({ ok: true, command: 'x', data: {} });
  const ui = wrap(<OrchRunScreen route={{ params: { runId: run.id, tab: 'results' } }} navigation={{ navigate: jest.fn(), goBack: jest.fn() }} />);
  await act(async () => { fireEvent.press(await ui.findByTestId('orch-run-results-ask')); });
  expect(ui.getByTestId('fleet-chat-prefill').props.children).toContain(`fleet results of run ${run.id} (${run.target},`);
});
