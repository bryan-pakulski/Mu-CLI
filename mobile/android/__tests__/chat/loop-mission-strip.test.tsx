/**
 * Loop mode shows its mission beside the chat: the goal, its state, the budget meter and what
 * waits for you; tapping opens the Loop panel.
 */
import React from 'react';
import { fireEvent, render, waitFor } from '@testing-library/react-native';

jest.mock('../../src/api/loop', () => ({ loopApi: { getState: jest.fn() } }));
jest.mock('../../src/navigation/nav', () => ({ navigateTo: jest.fn() }));

import { loopApi } from '../../src/api/loop';
import { navigateTo } from '../../src/navigation/nav';
import { LoopMissionStrip } from '../../src/components/chat/LoopMissionStrip';
import { ThemeProvider } from '../../src/theme/ThemeContext';

const mission = (status: string) => ({
  id: 'm1', goal: 'Get RT-DETR to 18/18 on the new kernels', status, status_reason: status === 'paused' ? 'by user' : '',
  done_when: [], increments: [{ n: 1, objective: 'x', result: 'partial', decision: '', next: '', work_item: '', actions: [], evidence: [], delegations: [], remediation_item: '', at: 1 }],
  budget: { max_cost_usd: 10 }, spent: { cost_usd: 3.4 }, started_at: 1, ended_at: null,
});

const state = (status: string) => ({
  active: true, mission: mission(status), budget_meter: { kind: 'cost_usd', used: 3.4, cap: 10, ratio: 0.34 },
  backlog: [], current_item: null, blockers: [], runs: [], past: [],
});

const wrap = (ui: React.ReactElement) => render(<ThemeProvider>{ui}</ThemeProvider>);

beforeEach(() => jest.clearAllMocks());

it('shows the goal, the budget and what waits, and opens the Loop panel', async () => {
  (loopApi.getState as jest.Mock).mockResolvedValue(state('paused'));
  const open = jest.fn(() => navigateTo('Loop'));
  const screen = wrap(<LoopMissionStrip refreshKey="a" onOpen={open} />);

  expect(await screen.findByText('Get RT-DETR to 18/18 on the new kernels')).toBeTruthy();
  expect(screen.getByText('cost $3.40 of $10.00')).toBeTruthy();
  expect(screen.getByTestId('loop-strip-now')).toBeTruthy();

  fireEvent.press(screen.getByTestId('loop-strip'));
  expect(navigateTo).toHaveBeenCalledWith('Loop');
});

it('says how to start when there is no mission, and reloads when a turn ends', async () => {
  (loopApi.getState as jest.Mock).mockResolvedValue({ ...state('running'), mission: null, budget_meter: null });
  const screen = wrap(<LoopMissionStrip refreshKey="idle" onOpen={jest.fn()} />);
  expect(await screen.findByTestId('loop-strip-empty')).toBeTruthy();

  (loopApi.getState as jest.Mock).mockResolvedValue(state('running'));
  screen.rerender(<ThemeProvider><LoopMissionStrip refreshKey="turn-ended" onOpen={jest.fn()} /></ThemeProvider>);
  await waitFor(() => expect(screen.getByTestId('loop-strip-goal')).toBeTruthy());
  expect(loopApi.getState).toHaveBeenCalledTimes(2);
});
