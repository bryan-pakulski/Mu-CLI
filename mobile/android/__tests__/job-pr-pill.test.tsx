import React from 'react';
import { Linking } from 'react-native';
import { fireEvent, render, waitFor } from '@testing-library/react-native';
import { JobDetailScreen } from '../src/screens/JobDetailScreen';
import { JobRow } from '../src/components/JobsChrome';
import { prPill } from '../src/components/jobViews';
import { jobCoreApi, type JobSummary, type JobVM } from '../src/api/jobCore';

jest.mock('../src/api/jobCore', () => ({ jobCoreApi: { job: jest.fn(), command: jest.fn() }, subscribeToJobChanges: jest.fn(() => ({ close: jest.fn() })) }));
jest.mock('../src/components/JobStageModelsSheet', () => ({ JobStageModelsSheet: () => null }));

const PR = { url: 'https://github.com/acme/api/pull/12', number: 12, state: 'merged' };

describe('prPill', () => {
  it('labels number and state like the web header', () => {
    expect(prPill(null)).toBeNull();
    expect(prPill({})).toBeNull();
    expect(prPill(PR)).toMatchObject({ label: 'PR #12 · merged', tone: 'ok', url: PR.url });
    expect(prPill({ ...PR, state: 'open' })).toMatchObject({ label: 'PR #12', tone: 'accent' });
    expect(prPill({ ...PR, state: 'closed' })).toMatchObject({ label: 'PR #12 · closed', tone: 'danger' });
    expect(prPill({ number: 3, url: 'javascript:alert(1)' })).toMatchObject({ label: 'PR #3', url: '' });
  });
});

describe('PR pill surfaces', () => {
  beforeEach(() => { jest.spyOn(Linking, 'openURL').mockResolvedValue(true as never); });
  afterEach(() => jest.restoreAllMocks());

  it('shows on job list rows and opens the PR URL', async () => {
    const row = { id: 'j1', title: 'Fix', key: 'T-1', state: 'pushed', verdict: '', mini: '', attention_detail: '', repository: '/r',
      cost_usd: 0, actions: [], created_at: 1, updated_at: 1, steps: [], pr: PR } as unknown as JobSummary;
    const onPress = jest.fn();
    const screen = render(<JobRow job={row} onPress={onPress} now={2} />);
    expect(screen.getByText('PR #12 · merged')).toBeTruthy();
    fireEvent.press(screen.getByTestId('job-row-j1-pr'));
    await waitFor(() => expect(Linking.openURL).toHaveBeenCalledWith(PR.url));
    expect(onPress).not.toHaveBeenCalled();
  });

  it('shows in the job detail header', async () => {
    const vm = { id: 'j', title: 'Ticket', key: 'T-1', state: 'pushed', cost_usd: 0, branch: 'job/j', pipeline: [], actions: [],
      pr: { ...PR, state: 'open' } } as unknown as JobVM;
    (jobCoreApi.job as jest.Mock).mockResolvedValue(vm);
    const navigation = { setOptions: jest.fn(), addListener: jest.fn(() => jest.fn()), navigate: jest.fn() };
    const screen = render(<JobDetailScreen route={{ params: { jobId: 'j' } } as any} navigation={navigation as any} />);
    const pill = await screen.findByTestId('job-pr-pill');
    expect(screen.getByText('PR #12')).toBeTruthy();
    fireEvent.press(pill);
    await waitFor(() => expect(Linking.openURL).toHaveBeenCalledWith(PR.url));
    screen.unmount();
  });
});
