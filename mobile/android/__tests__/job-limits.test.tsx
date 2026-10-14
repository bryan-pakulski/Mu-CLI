// Job caps pause instead of failing: generic action input (arg/value/hint),
// the limit banner on the job page, the Needs-you summary and notifications.
import React from 'react';
import { fireEvent, render, waitFor, within } from '@testing-library/react-native';

import { ApiError } from '../src/api/client';
import { jobCoreApi, type HomeVM, type JobSummary, type JobVM } from '../src/api/jobCore';
import { JobDetailScreen } from '../src/screens/JobDetailScreen';
import { WorkScreen } from '../src/screens/WorkScreen';
import { inputArgs, inputSpec } from '../src/components/JobActions';
import {
  enteredNeedsYou, notificationText, notifyNeedsYou, resetNotificationsModule, resetNotifyCache, setNotifyEnabled,
} from '../src/api/jobNotifications';

const mockNotifications = {
  getPermissionsAsync: jest.fn(() => Promise.resolve({ granted: true })),
  requestPermissionsAsync: jest.fn(() => Promise.resolve({ granted: true })),
  scheduleNotificationAsync: jest.fn(() => Promise.resolve('n1')),
  setNotificationHandler: jest.fn(),
  setNotificationChannelAsync: jest.fn(() => Promise.resolve()),
  AndroidImportance: { HIGH: 4 },
};
jest.mock('expo-notifications', () => mockNotifications, { virtual: true });
jest.mock('../src/api/jobCore', () => ({
  ...jest.requireActual('../src/api/jobCore'),
  jobCoreApi: {
    home: jest.fn(), job: jest.fn(), command: jest.fn(), markViewed: jest.fn(() => Promise.resolve({})),
    status: jest.fn(() => Promise.resolve({ agents: 0, checks: 0, needs_you: 1, engine: 'up' })),
  },
  subscribeToJobChanges: jest.fn(() => ({ close: jest.fn() })),
}));
jest.mock('../src/components/JobStageModelsSheet', () => ({ JobStageModelsSheet: () => null }));
jest.mock('../src/api/jobs', () => ({
  ...jest.requireActual('../src/api/jobs'),
  jobsApi: { getSettings: jest.fn(() => Promise.resolve({ stage_models: {}, plan_approval: true, missing: [] })), diagnostics: jest.fn(() => new Promise(() => {})), attempts: jest.fn(() => Promise.resolve({ attempts: [] })) },
}));

const api = jobCoreApi as unknown as Record<string, jest.Mock>;
const nav = () => ({ setOptions: jest.fn(), addListener: jest.fn(() => jest.fn()), navigate: jest.fn(), goBack: jest.fn(), dispatch: jest.fn() });
const A = (id: string, label: string, extra: Record<string, unknown> = {}) => ({ id, label, enabled: true, reason: '', primary: false, danger: false, ...extra });

const MESSAGE = 'Job reached its $100.00 cost limit during implement ($100.03 used). Approve more to continue, or cancel.';
const SUMMARY = 'Limit reached: cost $100.03 / $100.00';
const limit = {
  kind: 'cost', scope: 'job', stage: 'implement', used: 100.03, limit: 100, unit: 'usd',
  message: MESSAGE, summary: SUMMARY, suggested: 100, suggested_text: '100',
};
const raiseLimit = A('raiseLimit', 'Approve more & continue', {
  primary: true, tone: 'ok', shortcut: 'L',
  args: { kind: 'cost', scope: 'job', stage: 'implement', amount: '100' },
  input: { label: 'Additional cost (USD)', optional: false, multiline: false, arg: 'amount', value: '100', type: 'amount', hint: 'Limit $100.00, used $100.03.', unit: 'usd' },
});
const pausedJob = {
  id: 'j1', key: 'T-9', title: 'Big refactor', state: 'needs_input', cost_usd: 100.03, branch: 'job/j1', repository: 'repo', verdict: '',
  created_at: 1, updated_at: 2, attention_detail: MESSAGE, mini: '', pipeline: [], steps: [], limit,
  actions: [
    raiseLimit,
    A('resume', 'Resume', { enabled: false, reason: 'paused on a limit' }),
    A('discard', 'Cancel job', { tone: 'danger', confirm: 'Discard this job? Running work is cancelled.' }),
  ],
} as unknown as JobVM;

beforeEach(() => {
  jest.clearAllMocks();
  api.command.mockResolvedValue({ ok: true, parts: ['state'], data: { limit: { kind: 'cost', new: 200 }, resumed: true } });
  resetNotifyCache();
  resetNotificationsModule();
});

async function openJob(job: JobVM = pausedJob, params: Record<string, unknown> = {}) {
  api.job.mockResolvedValue(job);
  const screen = render(<JobDetailScreen route={{ params: { jobId: 'j1', ...params } } as any} navigation={nav() as any} />);
  await screen.findByTestId('job-header');
  return screen;
}

describe('generic action input contract', () => {
  it('honours input.arg / value / hint and keeps fallbacks for older servers', () => {
    const spec = inputSpec(raiseLimit as any)!;
    expect(spec).toMatchObject({ label: 'Additional cost (USD)', arg: 'amount', value: '100', hint: 'Limit $100.00, used $100.03.', type: 'amount', multiline: false });
    expect(inputArgs('raiseLimit', '2h', spec)).toEqual({ amount: '2h' });
    // input.arg wins over the hardcoded id → arg mapping.
    expect(inputArgs('message', 'hi', { label: 'x', optional: false, multiline: true, arg: 'body' })).toEqual({ body: 'hi' });
    // Older servers (no arg): unchanged fallbacks.
    expect(inputArgs('message', 'hi', inputSpec(A('message', 'Message') as any))).toEqual({ text: 'hi' });
    expect(inputArgs('rejectPlan', 'redo', { label: 'x', optional: false, multiline: true })).toEqual({ feedback: 'redo' });
  });

  it('the prompt sheet prefills input.value, shows the hint and sends {kind, scope, stage, amount}', async () => {
    const screen = await openJob(pausedJob, { action: 'raiseLimit' });
    const field = await screen.findByTestId('job-action-prompt-input');
    expect(field.props.value).toBe('100');
    expect(within(screen.getByTestId('job-action-prompt')).getByText('Limit $100.00, used $100.03.')).toBeTruthy();
    fireEvent.changeText(field, '50');
    fireEvent.press(screen.getByTestId('job-action-prompt-submit'));
    await waitFor(() => expect(api.command).toHaveBeenCalledWith('j1', 'raiseLimit', { kind: 'cost', scope: 'job', stage: 'implement', amount: '50' }));
    screen.unmount();
  });
});

describe('limit banner', () => {
  it('renders the message, a prefilled amount and both buttons; approve sends raiseLimit', async () => {
    const screen = await openJob();
    const banner = screen.getByTestId('job-limit-banner');
    expect(within(banner).getByText(MESSAGE)).toBeTruthy();
    expect(within(banner).getByTestId('job-limit-amount').props.value).toBe('100');
    expect(within(banner).getByText('Approve more & continue')).toBeTruthy();
    expect(within(banner).getByText('Cancel job')).toBeTruthy();
    // The banner owns raiseLimit / Cancel job; the action bar does not repeat them.
    const bar = screen.getByTestId('job-action-bar');
    expect(within(bar).queryByText('Approve more & continue')).toBeNull();
    expect(within(bar).queryByText('Cancel job')).toBeNull();

    fireEvent.changeText(within(banner).getByTestId('job-limit-amount'), '  ');
    fireEvent.press(within(banner).getByTestId('job-limit-raise'));
    expect(within(screen.getByTestId('job-limit-banner')).getByText(/Enter an amount/)).toBeTruthy();
    expect(api.command).not.toHaveBeenCalled();

    fireEvent.changeText(screen.getByTestId('job-limit-amount'), '25usd');
    fireEvent.press(screen.getByTestId('job-limit-raise'));
    await waitFor(() => expect(api.command).toHaveBeenCalledWith('j1', 'raiseLimit', { kind: 'cost', scope: 'job', stage: 'implement', amount: '25usd' }));
    screen.unmount();
  });

  it('shows the server 400 detail inline', async () => {
    api.command.mockRejectedValueOnce(new ApiError(400, 'Could not parse amount "lots"', { detail: 'Could not parse amount "lots"' }));
    const screen = await openJob();
    fireEvent.changeText(screen.getByTestId('job-limit-amount'), 'lots');
    fireEvent.press(screen.getByTestId('job-limit-raise'));
    await waitFor(() => expect(within(screen.getByTestId('job-limit-banner')).getByText('Could not parse amount "lots"')).toBeTruthy());
    screen.unmount();
  });

  it('Cancel job runs discard through its confirm', async () => {
    const screen = await openJob();
    fireEvent.press(screen.getByTestId('job-limit-cancel'));
    expect(await screen.findByText('Discard this job? Running work is cancelled.')).toBeTruthy();
    fireEvent.press(screen.getByTestId('job-action-confirm-submit'));
    await waitFor(() => expect(api.command).toHaveBeenCalledWith('j1', 'discard', {}));
    screen.unmount();
  });

  it('is absent when the job has no limit', async () => {
    const screen = await openJob({ ...pausedJob, limit: null, attention_detail: '', actions: [A('raiseLimit', 'Raise limit', { hidden: true, input: { label: 'x', arg: 'amount' } })] } as unknown as JobVM);
    expect(screen.queryByTestId('job-limit-banner')).toBeNull();
    expect(within(screen.getByTestId('job-action-bar')).queryByText('Raise limit')).toBeNull();
    screen.unmount();
  });
});

it('Needs-you rows show the limit summary and the primary action opens the job', async () => {
  const row = { ...pausedJob, primary_action: 'raiseLimit', attention_summary: SUMMARY };
  api.home.mockResolvedValue({ needs_you: [row], running: [], jobs: [row], counts: { needs_you: 1, total: 1 } });
  const navigation = nav();
  const screen = render(<WorkScreen route={{} as any} navigation={navigation as any} />);
  const card = await screen.findByTestId('needs-you-j1');
  expect(within(card).getByText(SUMMARY)).toBeTruthy();
  fireEvent.press(within(card).getByTestId('needs-you-j1-raiseLimit'));
  expect(navigation.navigate).toHaveBeenCalledWith('JobDetail', { jobId: 'j1', action: 'raiseLimit' });
  screen.unmount();
});

describe('limit notifications', () => {
  const row = (id: string, extra: Partial<JobSummary> = {}): JobSummary => ({
    id, title: `Job ${id}`, key: `K-${id}`, state: 'needs_input', verdict: '', mini: '', attention_detail: '', repository: '/r',
    cost_usd: 0, actions: [], created_at: 1, updated_at: 1, steps: [], ...extra,
  });
  const home = (needs: JobSummary[]): HomeVM => ({ needs_you: needs, running: [], jobs: needs, counts: { needs_you: needs.length, total: needs.length } });

  it('fires for a limit_reached job with the limit message as body', async () => {
    const paused = row('a', { limit: limit as any, attention_detail: MESSAGE });
    const first = enteredNeedsYou(null, home([]));
    const next = enteredNeedsYou(first.keys, home([paused]));
    expect(next.entered.map(r => r.id)).toEqual(['a']);
    // Already waiting for input (e.g. a question) and then hitting a cap still alerts.
    const waiting = enteredNeedsYou(null, home([row('b')]));
    expect(enteredNeedsYou(waiting.keys, home([row('b', { limit: limit as any })])).entered.map(r => r.id)).toEqual(['b']);

    const expected = { title: 'K-a: Limit reached — approve more?', body: `Job a · ${MESSAGE}`.slice(0, 240) };
    expect(notificationText(paused)).toEqual(expected);
    await setNotifyEnabled(true);
    expect(await notifyNeedsYou(next.entered)).toBe(1);
    expect(mockNotifications.scheduleNotificationAsync).toHaveBeenCalledWith({ content: { ...expected, data: { jobId: 'a' } }, trigger: null });
  });
});
