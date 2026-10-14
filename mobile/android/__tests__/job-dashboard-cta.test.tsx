import React from 'react';
import { fireEvent, render, waitFor } from '@testing-library/react-native';
import { jobCoreApi } from '../src/api/jobCore';
import { jobsApi } from '../src/api/jobs';
import { setupApi } from '../src/api/setup';
import { WorkScreen } from '../src/screens/WorkScreen';

jest.mock('../src/api/jobCore', () => ({
  ...jest.requireActual('../src/api/jobCore'),
  jobCoreApi: { home: jest.fn(), status: jest.fn(() => Promise.resolve({ agents: 0, checks: 0, needs_you: 0, engine: 'up' })) },
  subscribeToJobChanges: jest.fn(() => ({ close: jest.fn() })),
}));
jest.mock('../src/components/JobStageModelsSheet', () => ({ JobStageModelsSheet: () => null }));
jest.mock('../src/components/JiraSheet', () => ({ JiraSheet: () => null }));
jest.mock('../src/api/jobs', () => ({ ...jest.requireActual('../src/api/jobs'), jobsApi: { getSettings: jest.fn(), sessions: jest.fn(() => new Promise(() => {})) } }));
jest.mock('../src/api/setup', () => ({ ...jest.requireActual('../src/api/setup'), setupApi: { get: jest.fn() } }));

const nav = () => ({ setOptions: jest.fn(), addListener: jest.fn(() => jest.fn()), navigate: jest.fn(), goBack: jest.fn(), dispatch: jest.fn() });

beforeEach(() => {
  jest.clearAllMocks();
  (jobCoreApi.home as jest.Mock).mockResolvedValue({ needs_you: [], running: [], jobs: [], counts: { needs_you: 0, total: 0 } });
});

it('shows ONE setup call-to-action covering unfinished setup and missing stage models', async () => {
  (setupApi.get as jest.Mock).mockResolvedValue({ completed_at: null });
  (jobsApi.getSettings as jest.Mock).mockResolvedValue({ stage_models: {}, plan_approval: true, missing: ['plan', 'review'] });
  const navigation = nav();
  const screen = render(<WorkScreen route={{} as any} navigation={navigation as any} />);
  await waitFor(() => expect(screen.getByText(/Finish jobs setup.*No default model for plan, review/)).toBeTruthy());
  expect(screen.getAllByTestId('work-setup-cta')).toHaveLength(1);
  fireEvent.press(screen.getByTestId('work-setup-cta'));
  expect(navigation.navigate).toHaveBeenCalledWith('JobSetup');
  screen.unmount();
});

it('falls back to the stage-models message once setup is finished', async () => {
  (setupApi.get as jest.Mock).mockResolvedValue({ completed_at: 1 });
  (jobsApi.getSettings as jest.Mock).mockResolvedValue({ stage_models: {}, plan_approval: true, missing: ['implement'] });
  const screen = render(<WorkScreen route={{} as any} navigation={nav() as any} />);
  await waitFor(() => expect(screen.getByText(/No default model for implement: jobs use their session’s model/)).toBeTruthy());
  expect(screen.getAllByTestId('work-setup-cta')).toHaveLength(1);
  screen.unmount();
});
