import React from 'react';
import { act, fireEvent, render, waitFor, within } from '@testing-library/react-native';
import { ApiError } from '../../src/api/client';
import { jobCoreApi, type JobVM } from '../../src/api/jobCore';
import { JobDetailScreen } from '../../src/screens/JobDetailScreen';
import { JobActivityScreen } from '../../src/screens/JobActivityScreen';
import { WorkScreen } from '../../src/screens/WorkScreen';
import { JobActionBar } from '../../src/components/JobActions';
import * as Clipboard from 'expo-clipboard';

jest.mock('../../src/api/jobCore', () => {
  const actual = jest.requireActual('../../src/api/jobCore');
  return {
    ...actual,
    jobCoreApi: {
      home: jest.fn(), job: jest.fn(), command: jest.fn(), pushPreview: jest.fn(), markViewed: jest.fn(() => Promise.resolve({})),
      activity: jest.fn(), activityLogs: jest.fn(), removeContainer: jest.fn(), status: jest.fn(() => Promise.resolve({ agents: 1, checks: 0, needs_you: 2, engine: 'up' })),
    },
    subscribeToJobChanges: jest.fn(() => ({ close: jest.fn() })),
  };
});
jest.mock('../../src/components/JobStageModelsSheet', () => ({ JobStageModelsSheet: () => null }));
jest.mock('expo-clipboard', () => ({ setStringAsync: jest.fn(() => Promise.resolve(true)) }));
jest.mock('../../src/api/jobs', () => ({
  ...jest.requireActual('../../src/api/jobs'),
  jobsApi: { getSettings: jest.fn(() => Promise.resolve({ stage_models: {}, plan_approval: true, missing: [] })), diagnostics: jest.fn(() => new Promise(() => {})), attempts: jest.fn(() => Promise.resolve({ attempts: [] })) },
}));

const api = jobCoreApi as unknown as Record<string, jest.Mock>;
const nav = () => ({ setOptions: jest.fn(), addListener: jest.fn(() => jest.fn()), navigate: jest.fn(), goBack: jest.fn(), dispatch: jest.fn() });
const A = (id: string, label: string, extra: Record<string, unknown> = {}) => ({ id, label, enabled: true, reason: '', primary: false, danger: false, ...extra });

const reviewJob = {
  id: 'j1', key: 'T-7', title: 'Fix login', state: 'review_done', cost_usd: 1.5, branch: 'job/j1', repository: 'repo', verdict: 'needs_work',
  created_at: 1, updated_at: 2, attention_detail: '', mini: '', fix_attempts: 0,
  pipeline: [
    { id: 'plan', label: 'Plan', status: 'done', glyph: '✓', elapsed: 120, cost_usd: 0.5, runs: 1, detail: '' },
    { id: 'review', label: 'Review', status: 'waiting', glyph: '◆', elapsed: 0, cost_usd: 0, runs: 1, detail: '' },
  ],
  actions: [
    A('push', 'Push', { primary: true, tone: 'ok', dialog: 'push' }),
    A('retry', 'Retry', { tone: 'warn' }),
    A('delete', 'Delete', { tone: 'danger', confirm: 'Delete this job? The branch is kept.' }),
    A('approveTool', 'Approve tool', { tone: 'warn', args: { event_id: 7 } }),
    A('denyTool', 'Deny', { tone: 'danger', args: { event_id: 7 }, input: { label: 'Reason for the agent (optional)', optional: true } }),
    A('markViewed', 'Mark viewed', { hidden: true }),
    A('markFindings', 'Mark findings', { hidden: true }),
    A('reprompt', 'Fix findings'),
    A('restartStage', 'Restart stage', { enabled: false, reason: 'nothing is running' }),
  ],
  session: { live: false, events: [], stages: {}, approval: { tool_name: 'run_shell', tool_args: { cmd: 'rm -rf build' } } },
  diff: { patch: 'diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,1 @@\n-old\n+new\n', viewed: [] },
  review: { verdict: 'needs_work', merge_readiness: 60, summary: 'Mostly fine', counts: { major: 1 }, plan_coverage: [],
    findings: [{ id: 'F-1', severity: 'major', title: 'Race', detail: 'd', suggestion: '', file: 'a.py', line_start: 1, disposition: 'open' }] },
} as unknown as JobVM;

beforeEach(() => {
  jest.clearAllMocks();
  api.command.mockResolvedValue({ ok: true, parts: ['state'], data: {} });
});

async function openJob(job = reviewJob, params: Record<string, unknown> = {}) {
  api.job.mockResolvedValue(job);
  const navigation = nav();
  const screen = render(<JobDetailScreen route={{ params: { jobId: 'j1', ...params } } as any} navigation={navigation as any} />);
  await screen.findByTestId('job-header');
  return { screen, navigation };
}

const tabOf = (screen: ReturnType<typeof render>, tab: string) => screen.getByTestId(`job-page-header-tabs-${tab}`);
/** Non-primary actions live under More. */
const fromMore = (screen: ReturnType<typeof render>, label: string) => {
  fireEvent.press(screen.getByTestId('job-action-more'));
  fireEvent.press(screen.getByText(label));
};

it('renders the job page: header, pipeline, approval banner, action bar and tabs', async () => {
  const { screen } = await openJob();
  expect(screen.getByRole('header').props.children).toBe('Fix login');
  expect(screen.getByTestId('job-state-pill')).toBeTruthy();
  expect(screen.getByLabelText(/Plan: done, 2m, \$0.50/)).toBeTruthy();
  expect(screen.getByTestId('job-approval-banner')).toBeTruthy();
  const bar = screen.getByTestId('job-action-bar');
  // Hidden and banner-owned actions stay out of the bar; the enabled primary is the one button.
  expect(within(bar).getByText('Push')).toBeTruthy();
  fireEvent.press(screen.getByTestId('job-action-more'));
  expect(screen.queryByText('Mark viewed')).toBeNull();
  // Approve tool lives only on the approval banner.
  expect(screen.getAllByText('Approve tool')).toHaveLength(1);
  expect(screen.getByText('nothing is running')).toBeTruthy();
  // A pending tool approval opens the Session tab; the Review tab shows the verdict.
  expect(screen.getByTestId('job-transcript')).toBeTruthy();
  expect(tabOf(screen, 'session').props.accessibilityState).toMatchObject({ selected: true });
  fireEvent.press(tabOf(screen, 'review'));
  expect(within(screen.getByTestId('review-verdict')).getByText('needs work')).toBeTruthy();
});

it('opens a requested tab from a deep link', async () => {
  const { screen } = await openJob(reviewJob, { tab: 'diff' });
  expect(tabOf(screen, 'diff').props.accessibilityState).toMatchObject({ selected: true });
  expect(await screen.findByTestId('diff-viewed-count')).toBeTruthy();
});

it('dispatches a plain action straight to /api/jobs/cmd', async () => {
  const { screen } = await openJob();
  fromMore(screen, 'Retry');
  await waitFor(() => expect(api.command).toHaveBeenCalledWith('j1', 'retry', {}));
});

it('asks to confirm destructive actions before sending them', async () => {
  const { screen, navigation } = await openJob();
  fromMore(screen, 'Delete');
  expect(await screen.findByText('Delete this job? The branch is kept.')).toBeTruthy();
  expect(api.command).not.toHaveBeenCalled();
  fireEvent.press(screen.getByTestId('server-action-ok'));
  await waitFor(() => expect(api.command).toHaveBeenCalledWith('j1', 'delete', {}));
  await waitFor(() => expect(navigation.goBack).toHaveBeenCalled());
});

it('denies a tool with a reason from the input dialog', async () => {
  const { screen } = await openJob();
  fireEvent.press(screen.getByTestId('job-deny-tool'));
  fireEvent.changeText(await screen.findByTestId('server-action-input'), 'Never delete build output');
  fireEvent.press(screen.getByTestId('server-action-ok'));
  await waitFor(() => expect(api.command).toHaveBeenCalledWith('j1', 'denyTool', { event_id: 7, message: 'Never delete build output' }));
});

it('opens the push dialog prefilled from push-preview and sends the edited PR fields', async () => {
  api.pushPreview.mockResolvedValue({ title: 'T-7: Fix login', body: 'Body', branch: 'job/j1', base: 'main', mode: 'gh', remote: 'github.com/o/r', draft: true, requires_override: false, verdict: 'needs_work', head_sha: 'abc123', reason: '' });
  const { screen } = await openJob();
  fireEvent.press(screen.getByTestId('job-action-push'));
  const title = await screen.findByLabelText('Pull request title');
  expect(title.props.value).toBe('T-7: Fix login');
  fireEvent.changeText(title, 'Fix the login race');
  fireEvent(screen.getByTestId('push-draft-switch'), 'valueChange', false);
  fireEvent.press(screen.getByTestId('push-submit'));
  await waitFor(() => expect(api.command).toHaveBeenCalledWith('j1', 'push', { pr_title: 'Fix the login race', pr_body: 'Body', draft: false }));
});

it('requires an override reason when the review blocks the push, and degrades without push-preview', async () => {
  api.pushPreview.mockRejectedValue(new ApiError(404, 'Not Found'));
  const job = { ...reviewJob, actions: reviewJob.actions.map(a => (a.id === 'push' ? { ...a, args: { requires_override: true } } : a)) } as JobVM;
  const { screen } = await openJob(job);
  fireEvent.press(screen.getByTestId('job-action-push'));
  expect(await screen.findByText(/no push preview/)).toBeTruthy();
  fireEvent.press(screen.getByTestId('push-submit'));
  expect(api.command).not.toHaveBeenCalled();
  fireEvent.changeText(screen.getByLabelText('Override reason'), 'Reviewer finding is a false positive');
  fireEvent.press(screen.getByTestId('push-submit'));
  await waitFor(() => expect(api.command).toHaveBeenCalledWith('j1', 'push', expect.objectContaining({ draft: true, override_reason: 'Reviewer finding is a false positive' })));
});

it('marks diff files viewed on the server and fixes a finding inline', async () => {
  const { screen } = await openJob();
  fireEvent.press(tabOf(screen, 'diff'));
  expect((await screen.findAllByLabelText('modified')).length).toBe(2);
  fireEvent.press(screen.getByTestId('fix-this-F-1'));
  // reprompt asks for optional extra instructions first.
  fireEvent.press(await screen.findByTestId('server-action-ok'));
  await waitFor(() => expect(api.command).toHaveBeenCalledWith('j1', 'reprompt', expect.objectContaining({ finding_ids: ['F-1'] })));
});

it('opens the diff full screen in review mode and leaves it with back', async () => {
  const { screen } = await openJob();
  fireEvent.press(tabOf(screen, 'diff'));
  fireEvent.press(await screen.findByTestId('diff-review'));
  const review = screen.getByTestId('diff-review-mode');
  expect(within(review).getAllByTestId('diff-file-a.py')).toHaveLength(1);
  // Review mode has no second review button.
  expect(within(review).queryByTestId('diff-review')).toBeNull();
  fireEvent.press(within(review).getByLabelText('Leave review mode'));
  expect(screen.queryByTestId('diff-review-mode')).toBeNull();
});

it('starts a Needs-you action passed from the Now board', async () => {
  await openJob(reviewJob, { action: 'retry' });
  await waitFor(() => expect(api.command).toHaveBeenCalledWith('j1', 'retry', {}));
});

it('marks a diff file viewed through markViewed', async () => {
  const { screen } = await openJob();
  fireEvent.press(tabOf(screen, 'diff'));
  fireEvent.press(await screen.findByTestId('viewed-a.py'));
  await waitFor(() => expect(api.markViewed).toHaveBeenCalledWith('j1', 'a.py', true));
  expect(screen.getByText('1/1 viewed')).toBeTruthy();
});

it('offers the worktree as a shell command to copy, never running anything', async () => {
  const job = { ...reviewJob, worktree: "/home/me/work tree/it's" } as JobVM;
  const { screen } = await openJob(job);
  fireEvent.press(screen.getByTestId('job-open-shell'));
  expect(within(screen.getByTestId('job-shell-command')).getByText(`cd '/home/me/work tree/it'\\''s'`)).toBeTruthy();
  fireEvent.press(screen.getByTestId('job-shell-copy'));
  await waitFor(() => expect(Clipboard.setStringAsync).toHaveBeenCalledWith(`cd '/home/me/work tree/it'\\''s'`));
  expect(api.command).not.toHaveBeenCalled();
});

it('shows plain-English notes, linking each to its tab', async () => {
  const job = { ...reviewJob, session: { ...reviewJob.session, approval: null }, notes: [
    { id: 'e1', at: 100, stage: 'plan', tone: 'ok', title: 'Plan ready', headline: 'Fix the race in login', bullets: ['$ pytest -q'], meta: '3 steps', count: 1, tab: 'plan' },
    { id: 'e2', at: 200, stage: 'checks', tone: 'danger', title: 'Checks failed', headline: '', bullets: [], meta: '', count: 2, tab: 'checks' },
  ] } as unknown as JobVM;
  const { screen } = await openJob(job);
  expect(tabOf(screen, 'notes').props.accessibilityState).toMatchObject({ selected: true });
  expect(screen.getByText('Fix the race in login')).toBeTruthy();
  expect(screen.getByText('$ pytest -q')).toBeTruthy();
  fireEvent.press(screen.getByTestId('note-e1-tab'));
  expect(tabOf(screen, 'plan').props.accessibilityState).toMatchObject({ selected: true });
});

it('keeps the primary as the bar button and the rest, with reasons, under More', async () => {
  const onInvoke = jest.fn();
  const screen = render(<JobActionBar actions={[A('recheck', 'Re-run checks'), A('approvePlan', 'Approve plan', { primary: true, tone: 'ok' }), A('discard', 'Discard', { enabled: false, reason: 'job is done' })] as any} onInvoke={onInvoke} />);
  fireEvent.press(screen.getByTestId('job-action-approvePlan'));
  expect(onInvoke).toHaveBeenCalledWith(expect.objectContaining({ id: 'approvePlan' }));
  expect(screen.queryByText('job is done')).toBeNull();
  fireEvent.press(screen.getByTestId('job-action-more'));
  expect(screen.getByText('job is done')).toBeTruthy();
  fireEvent.press(screen.getByText('Re-run checks'));
  expect(onInvoke).toHaveBeenCalledWith(expect.objectContaining({ id: 'recheck' }));
});

it('blocks approving the plan while it has unsaved edits, and says why', () => {
  const screen = render(<JobActionBar planDirty actions={[A('approvePlan', 'Approve plan', { primary: true })] as any} onInvoke={jest.fn()} />);
  fireEvent.press(screen.getByTestId('job-action-more'));
  expect(screen.getByText('Save or cancel your plan edits first')).toBeTruthy();
});

const activity = {
  generated_at: 100, engine: 'up', counts: { agents: 1, checks: 0, containers: 2, needs_you: 0 },
  jobs: [{ id: 'j1', key: 'T-7', title: 'Fix login', state: 'implementing', stage: 'implement', worker_id: 'w1', heartbeat_at: 99, lease_expires_at: 200, since: 50, cost_usd: 0.4, kind: 'agent', last: 'editing a.py',
    containers: [{ name: 'mucli-job-aaaaaaaaaaaa-impl', id: 'c1', image: 'mucli/sandbox', status: 'running', stage: 'implement', started_at: 60, cpu_percent: 42, mem_bytes: 200 * 1024 * 1024, mem_limit: 1024 * 1024 * 1024 }] }],
  orphans: [{ name: 'mucli-job-bbbbbbbbbbbb-old', id: 'c2', image: 'mucli/sandbox', status: 'exited', stage: '', started_at: null, cpu_percent: null, mem_bytes: null, mem_limit: null }],
};

it('renders activity with CPU/memory sparklines, opens logs and removes an orphaned container', async () => {
  api.activity.mockResolvedValue(activity);
  api.activityLogs.mockResolvedValue({ text: 'worker started\nediting a.py', truncated: true, source: 'worker' });
  api.removeContainer.mockResolvedValue({ ok: true });
  const navigation = nav();
  const screen = render(<JobActivityScreen route={{} as any} navigation={navigation as any} />);
  expect(await screen.findByTestId('activity-job-j1')).toBeTruthy();
  expect(screen.getByLabelText('CPU 42 percent')).toBeTruthy();
  expect(screen.getByLabelText(/Memory 200 MB, 20 percent of limit/)).toBeTruthy();
  expect(screen.getByLabelText('CPU no sample yet')).toBeTruthy();
  // Active job's container cannot be removed from the UI; the orphan can.
  expect(screen.getByTestId('remove-mucli-job-aaaaaaaaaaaa-impl').props.accessibilityState.disabled).toBe(true);
  fireEvent.press(screen.getByTestId('activity-log-j1'));
  expect(within(await screen.findByTestId('activity-log-text')).getByText(/editing a.py/)).toBeTruthy();
  expect(api.activityLogs).toHaveBeenCalledWith('j1');
  fireEvent.press(screen.getByTestId('remove-mucli-job-bbbbbbbbbbbb-old'));
  fireEvent.press(screen.getByTestId('remove-confirm'));
  await waitFor(() => expect(api.removeContainer).toHaveBeenCalledWith('mucli-job-bbbbbbbbbbbb-old'));
  screen.unmount();
});

it('polls activity every 5 s and explains when the server has no activity endpoint', async () => {
  jest.useFakeTimers();
  try {
    api.activity.mockRejectedValue(new ApiError(404, 'Not Found'));
    const screen = render(<JobActivityScreen route={{} as any} navigation={nav() as any} />);
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByText(/Activity needs a newer mucli server/)).toBeTruthy();
    const calls = api.activity.mock.calls.length;
    await act(async () => { jest.advanceTimersByTime(5000); });
    expect(api.activity.mock.calls.length).toBe(calls + 1);
    screen.unmount();
  } finally { jest.useRealTimers(); }
});

it('renders the Now board: needs-you rows with their decisive action, the state sentence and tabs', async () => {
  const summary = { ...reviewJob, steps: reviewJob.pipeline, attention_detail: 'Review found 1 major issue' };
  api.home.mockResolvedValue({ needs_you: [summary], running: [], jobs: [summary], counts: { needs_you: 1, total: 1 } });
  const navigation = nav();
  const screen = render(<WorkScreen route={{} as any} navigation={navigation as any} />);
  expect(await screen.findByTestId('now-needs-you')).toBeTruthy();
  expect(screen.getByText('2 need you', { exact: false })).toBeTruthy();
  expect(screen.getAllByLabelText(/^Pipeline: Plan: done/).length).toBeGreaterThan(0);
  fireEvent.press(screen.getByTestId('needs-you-j1-push'));
  expect(navigation.navigate).toHaveBeenCalledWith('JobDetail', { jobId: 'j1', action: 'push' });
  fireEvent.press(screen.getByTestId('work-header-menu'));
  fireEvent.press(screen.getByText('Activity'));
  expect(navigation.navigate).toHaveBeenCalledWith('JobActivity');
  fireEvent.press(screen.getByTestId('work-header-tabs-jobs'));
  expect(await screen.findByTestId('jobs-list')).toBeTruthy();
  screen.unmount();
});
