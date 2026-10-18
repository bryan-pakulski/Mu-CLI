// W10 visibility on Android: live diff, environment approvals, budget header,
// git URL in the New job sheet and "needs you" local notifications.
import React from 'react';
import { act, fireEvent, render, waitFor } from '@testing-library/react-native';

import { JobDetailScreen, LIVE_DIFF_REFRESH_MS } from '../src/screens/JobDetailScreen';
import { jobCoreApi, subscribeToJobChanges, type HomeVM, type JobSummary, type JobVM } from '../src/api/jobCore';
import { isRepositoryUrl, jobsApi } from '../src/api/jobs';
import { environmentsApi, pendingEnvironmentApprovals } from '../src/api/environments';
import { budgetSummary } from '../src/components/jobViews';
import { RepositoryEnvironmentApproval } from '../src/components/JobEnvironments';
import { NewJobSheet } from '../src/screens/WorkScreen';
import { NeedsYouNotifier } from '../src/components/NeedsYouNotifier';
import { useConnectionStore } from '../src/store/connection';
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
  jobCoreApi: { job: jest.fn(), command: jest.fn(), home: jest.fn(), status: jest.fn(() => Promise.resolve({ agents: 0, checks: 0, needs_you: 0, engine: 'up' })) },
  subscribeToJobChanges: jest.fn(() => ({ close: jest.fn() })),
}));
jest.mock('../src/api/jobs', () => ({
  ...jest.requireActual('../src/api/jobs'),
  jobsApi: { sessions: jest.fn(), getSettings: jest.fn(), create: jest.fn(), importTicket: jest.fn() },
}));
jest.mock('../src/api/environments', () => ({
  ...jest.requireActual('../src/api/environments'),
  environmentsApi: { get: jest.fn(), approve: jest.fn() },
}));
jest.mock('../src/components/JobStageModelsSheet', () => ({ JobStageModelsSheet: () => null }));

const core = jobCoreApi as unknown as Record<string, jest.Mock>;
const jobs = jobsApi as unknown as Record<string, jest.Mock>;
const envs = environmentsApi as unknown as Record<string, jest.Mock>;
const navigation = { setOptions: jest.fn(), addListener: jest.fn(() => jest.fn()), navigate: jest.fn(), goBack: jest.fn() };

const PATCH = 'diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n@@ -1 +1,2 @@\n a\n+live line\n';
const baseVm = {
  id: 'j', title: 'Ticket', key: 'T-1', state: 'implementing', cost_usd: 5, branch: 'job/j', pipeline: [], actions: [],
  diff: { patch: null, viewed: [], live: { patch: PATCH, files: ['a.txt'], untracked: ['new.txt'], untracked_total: 1, truncated: true, base_sha: 'abcdef1234567890', generated_at: 1, bytes: 10 } },
  budget: { cost_usd: 5, max_cost_usd: 20, cost_fraction: 0.25, active_seconds: 125, max_runtime_seconds: null },
} as unknown as JobVM;

beforeEach(() => {
  jest.clearAllMocks();
  resetNotifyCache();
  resetNotificationsModule();
});

describe('git URL in the New job sheet', () => {
  it('recognises https, ssh and scp-style URLs but not paths', () => {
    expect(isRepositoryUrl('https://github.com/org/repo')).toBe(true);
    expect(isRepositoryUrl('https://github.com/org/repo.git')).toBe(true);
    expect(isRepositoryUrl('ssh://git@gitlab.com/g/app.git')).toBe(true);
    expect(isRepositoryUrl('git@github.com:org/repo.git')).toBe(true);
    expect(isRepositoryUrl('/home/me/src/project')).toBe(false);
    expect(isRepositoryUrl('relative/dir')).toBe(false);
    expect(isRepositoryUrl('C:\\repo')).toBe(false);
    expect(isRepositoryUrl('https://github.com')).toBe(false);
    expect(isRepositoryUrl('')).toBe(false);
  });

  it('sends a URL as `repository` instead of `workspace_root`', async () => {
    jobs.sessions.mockResolvedValue({ current: null, sessions: [] });
    jobs.getSettings.mockResolvedValue({ stage_models: {}, plan_approval: true, rules: { schema: [], overrides: {}, values: {} } });
    jobs.create.mockResolvedValue({ job: { id: 'new1' } });
    const onCreated = jest.fn();
    const screen = render(<NewJobSheet visible sessionName={null} provider={null} model={null} onClose={jest.fn()} onCreated={onCreated} />);
    fireEvent.changeText(screen.getByTestId('job-workspace'), 'git@github.com:org/repo.git');
    fireEvent.changeText(screen.getByTestId('job-title'), 'Fix it');
    expect(screen.getByText('Git URL: the server clones this repository for the job.')).toBeTruthy();
    fireEvent.press(screen.getByTestId('job-create-submit'));
    await waitFor(() => expect(jobs.create).toHaveBeenCalled());
    const input = jobs.create.mock.calls[0][0];
    expect(input.repository).toBe('git@github.com:org/repo.git');
    expect(input).not.toHaveProperty('workspace_root');
    expect(envs.get).not.toHaveBeenCalled();
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith({ id: 'new1' }));
  });
});

describe('job page', () => {
  it('shows the live diff with badge, new-file count and truncation note, and refreshes it on an interval', async () => {
    jest.useFakeTimers();
    try {
      core.job.mockResolvedValue(baseVm);
      const screen = render(<JobDetailScreen route={{ params: { jobId: 'j' } } as any} navigation={navigation as any} />);
      await act(async () => { await Promise.resolve(); });
      fireEvent.press(await screen.findByTestId('job-tab-diff'));
      expect(screen.getByTestId('diff-live')).toBeTruthy();
      expect(screen.getByText('Live')).toBeTruthy();
      expect(screen.getByTestId('diff-live-untracked').props.children).toEqual([1, ' new file', '']);
      expect(screen.getByTestId('diff-live-truncated')).toBeTruthy();
      expect(screen.getByText('live line')).toBeTruthy();
      expect(screen.queryByTestId('diff-viewed-count')).toBeNull();
      core.job.mockClear();
      await act(async () => { jest.advanceTimersByTime(LIVE_DIFF_REFRESH_MS + 10); });
      expect(core.job).toHaveBeenCalledWith('j', ['diff']);
      screen.unmount();
    } finally { jest.useRealTimers(); }
  });

  it('falls back to the checkpointed diff when no live diff is sent', async () => {
    core.job.mockResolvedValue({ ...baseVm, state: 'pushed', diff: { patch: PATCH, viewed: [], live: null } });
    const screen = render(<JobDetailScreen route={{ params: { jobId: 'j' } } as any} navigation={navigation as any} />);
    fireEvent.press(await screen.findByTestId('job-tab-diff'));
    expect(screen.queryByTestId('diff-live')).toBeNull();
    expect(screen.getByTestId('diff-viewed-count')).toBeTruthy();
    screen.unmount();
  });

  it('header shows cost vs cap and active time', async () => {
    core.job.mockResolvedValue(baseVm);
    const screen = render(<JobDetailScreen route={{ params: { jobId: 'j' } } as any} navigation={navigation as any} />);
    expect(await screen.findByText('$5.00 / $20.00')).toBeTruthy();
    expect(screen.getByText('active 2m')).toBeTruthy();
    screen.unmount();
  });

  it('approves and rejects a repository build environment through the command path', async () => {
    const approval = { repository: '/repo', fingerprint: 'build:abc', kind: 'build', ref: 'mucli/devcontainer-repo:abc',
      label: 'Dockerfile .devcontainer/Dockerfile (context .) from .devcontainer/devcontainer.json', origin: 'devcontainer',
      ignored_features: ['ghcr.io/x/y'], message: 'This repository asks mucli to build and use…', event_id: 9 };
    const actions = [
      { id: 'approveEnvironment', label: 'Approve environment', enabled: true, reason: '', primary: true, danger: false, tone: 'warn', confirm: 'Approve this environment?', args: { fingerprint: 'build:abc' } },
      { id: 'rejectEnvironment', label: 'Reject environment', enabled: true, reason: '', primary: false, danger: false, tone: 'danger', input: { label: 'Reason (optional)', optional: true, multiline: false }, args: { fingerprint: 'build:abc' } },
    ];
    core.job.mockResolvedValue({ ...baseVm, state: 'needs_input', environment_approval: approval, actions });
    core.command.mockResolvedValue({ ok: true, parts: ['state'], data: {} });
    const screen = render(<JobDetailScreen route={{ params: { jobId: 'j' } } as any} navigation={navigation as any} />);
    expect(await screen.findByTestId('job-environment-approval')).toBeTruthy();
    expect(screen.getByText('build:abc')).toBeTruthy();
    expect(screen.getByText('mucli/devcontainer-repo:abc')).toBeTruthy();
    expect(screen.getByText('ghcr.io/x/y')).toBeTruthy();
    expect(screen.getByText(approval.label)).toBeTruthy();
    // Kept out of the generic action bar (the banner owns them): one button each.
    expect(screen.getAllByText('Approve environment')).toHaveLength(1);
    fireEvent.press(screen.getByTestId('job-approve-environment'));
    fireEvent.press(await screen.findByTestId('job-action-confirm-submit'));
    await waitFor(() => expect(core.command).toHaveBeenCalledWith('j', 'approveEnvironment', { fingerprint: 'build:abc' }));
    fireEvent.press(screen.getByTestId('job-reject-environment'));
    fireEvent.changeText(await screen.findByLabelText('Reason (optional)'), 'use mine');
    fireEvent.press(screen.getByTestId('job-action-prompt-submit'));
    await waitFor(() => expect(core.command).toHaveBeenLastCalledWith('j', 'rejectEnvironment', { fingerprint: 'build:abc', reason: 'use mine' }));
    screen.unmount();
  });

  it('shows a failed job setup as a warning while the job keeps running (jobs run unattended)', async () => {
    const message = 'Job setup for build environment `rsq` failed: `npm ci` exited 1. The job continues without it.';
    core.job.mockResolvedValue({ ...baseVm, state: 'planning', setup_warning: { binding: 'rsq', command: 'npm ci', message } });
    const screen = render(<JobDetailScreen route={{ params: { jobId: 'j' } } as any} navigation={navigation as any} />);
    expect(await screen.findByTestId('job-setup-warning')).toBeTruthy();
    expect(screen.getByText('Job setup failed; the job continued without it')).toBeTruthy();
    expect(screen.getByText(message)).toBeTruthy();
    screen.unmount();
  });
});

describe('budget summary', () => {
  it('formats cost vs cap, no cap and the warn/danger tones', () => {
    expect(budgetSummary({ cost_usd: 1, budget: { cost_usd: 1, max_cost_usd: null, cost_fraction: null, active_seconds: 30, max_runtime_seconds: 3600 } } as any))
      .toMatchObject({ cost: '$1.00 · no cap', time: 'active 30s / 1h 0m', tone: '' });
    expect(budgetSummary({ cost_usd: 9, budget: { cost_usd: 9, max_cost_usd: 10, cost_fraction: 0.9, active_seconds: 0, max_runtime_seconds: null } } as any).tone).toBe('warn');
    expect(budgetSummary({ cost_usd: 12, max_cost_usd: 10 } as any)).toMatchObject({ cost: '$12.00 / $10.00', tone: 'danger', time: '' });
  });
});

describe('New job: repository environment approval', () => {
  it('lists the suggestion and declared bindings', () => {
    const out = pendingEnvironmentApprovals({ source: 'repo', bindings: [], summary: '', error: '',
      suggestion: { message: 'm', approval: { repository: '/r', fingerprint: 'build:1', kind: 'build', ref: 'tag' } },
      approval_required: [{ binding: 'repo', ref: 'node:20', fingerprint: 'image:node:20' }, { binding: 'x', ref: 'tag', fingerprint: 'build:1' }] }, '/r');
    expect(out.map(o => o.fingerprint)).toEqual(['build:1', 'image:node:20']);
    expect(out[1]).toMatchObject({ kind: 'image', ref: 'node:20' });
  });

  it('approves the requested fingerprint via the endpoint', async () => {
    envs.get.mockResolvedValue({ resolved: { source: 'repo', bindings: [], summary: '', error: '',
      approval_required: [{ binding: 'repo', ref: 'node:20', fingerprint: 'image:node:20' }] } });
    envs.approve.mockResolvedValue({ approved: 'image:node:20' });
    const screen = render(<RepositoryEnvironmentApproval repository="/r" session="s" debounceMs={0} />);
    const button = await screen.findByTestId('approve-environment-image:node:20');
    expect(envs.get).toHaveBeenCalledWith({ repository: '/r', session: 's', images: false });
    fireEvent.press(button);
    await waitFor(() => expect(envs.approve).toHaveBeenCalledWith({ repository: '/r', fingerprint: 'image:node:20', session: 's' }));
    await waitFor(() => expect(screen.queryByTestId('new-job-environment-approval')).toBeNull());
  });
});

describe('"needs you" notifications', () => {
  const row = (id: string, state: string, extra: Partial<JobSummary> = {}): JobSummary => ({
    id, title: `Job ${id}`, key: `K-${id}`, state, verdict: '', mini: '', attention_detail: '', repository: '/r',
    cost_usd: 0, actions: [], created_at: 1, updated_at: 1, steps: [], ...extra,
  });
  const home = (needs: JobSummary[], all: JobSummary[] = needs): HomeVM => ({ needs_you: needs, running: [], jobs: all, counts: { needs_you: needs.length, total: all.length } });

  it('baselines on first load and reports only newly entered jobs', () => {
    const first = enteredNeedsYou(null, home([row('a', 'plan_review')]));
    expect(first.entered).toEqual([]);
    const next = enteredNeedsYou(first.keys, home([row('a', 'plan_review'), row('b', 'review_done')]));
    expect(next.entered.map(r => r.id)).toEqual(['b']);
    // Re-entering a different gate notifies again; a closed PR counts too.
    const again = enteredNeedsYou(next.keys, home([row('a', 'needs_input'), row('b', 'review_done'), row('c', 'pushed', { pr: { state: 'closed' } })]));
    expect(again.entered.map(r => r.id)).toEqual(['a', 'c']);
    expect(notificationText(row('c', 'pushed', { pr: { state: 'closed' } })).title).toBe('K-c: PR closed without merging');
    expect(notificationText(row('a', 'plan_review')).title).toBe('K-a: Plan ready for your review');
  });

  it('posts local notifications unless the toggle is off', async () => {
    expect(await notifyNeedsYou([row('a', 'plan_review')])).toBe(1);
    expect(mockNotifications.scheduleNotificationAsync).toHaveBeenCalledWith({
      content: { title: 'K-a: Plan ready for your review', body: 'Job a', data: { jobId: 'a' } }, trigger: null });
    await setNotifyEnabled(false);
    mockNotifications.scheduleNotificationAsync.mockClear();
    expect(await notifyNeedsYou([row('b', 'review_done')])).toBe(0);
    expect(mockNotifications.scheduleNotificationAsync).not.toHaveBeenCalled();
    await setNotifyEnabled(true);
  });

  it('does not post when permission is denied', async () => {
    mockNotifications.getPermissionsAsync.mockResolvedValueOnce({ granted: false, canAskAgain: true } as any);
    mockNotifications.requestPermissionsAsync.mockResolvedValueOnce({ granted: false } as any);
    expect(await notifyNeedsYou([row('a', 'plan_review')])).toBe(0);
  });

  it('the app-wide notifier alerts when the home VM gains a needs-you job', async () => {
    await setNotifyEnabled(true);
    core.home.mockResolvedValueOnce(home([row('a', 'plan_review')])).mockResolvedValue(home([row('a', 'plan_review'), row('b', 'needs_input')]));
    useConnectionStore.setState({ isConnected: true } as any);
    const screen = render(<NeedsYouNotifier />);
    await waitFor(() => expect(core.home).toHaveBeenCalledTimes(1));
    expect(mockNotifications.scheduleNotificationAsync).not.toHaveBeenCalled();
    const onChange = (subscribeToJobChanges as jest.Mock).mock.calls.slice(-1)[0][0];
    await act(async () => { onChange({ scope: 'home', id: '', parts: ['state'], seq: 2 }); });
    await waitFor(() => expect(mockNotifications.scheduleNotificationAsync).toHaveBeenCalledTimes(1));
    expect((mockNotifications.scheduleNotificationAsync.mock.calls[0] as any)[0].content.data).toEqual({ jobId: 'b' });
    screen.unmount();
    useConnectionStore.setState({ isConnected: false } as any);
  });
});
