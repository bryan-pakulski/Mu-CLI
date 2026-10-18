import React from 'react';
import { fireEvent, render, waitFor, within } from '@testing-library/react-native';
import { ApiError } from '../src/api/client';
import {
  jiraApi, jobsApi, costCapPayload, costPlaceholder, defaultJobSession, ticketImportBody, doneSkippedKeys, defaultWorkspace, isTicketKey, newJobDefaults, recentWorkspaces, sessionForWorkspace, sessionModelLabel,
  type JobRules, type JobSessionOption,
} from '../src/api/jobs';
import { jobCoreApi, type JobSummary } from '../src/api/jobCore';
import { NewJobSheet } from '../src/screens/WorkScreen';
import { JobsListScreen, filterJobs, jobSessions, ALL_SESSIONS } from '../src/screens/JobsListScreen';
import { JiraBrowser } from '../src/components/JiraSheet';
import { JobRulesForm } from '../src/components/JobRulesForm';
import { JobRow, JobsNavBar } from '../src/components/JobsChrome';
import { ToastHost } from '../src/ui';
import realRules from './fixtures/job-rules-schema.json';

jest.mock('../src/api/jobs', () => ({
  ...jest.requireActual('../src/api/jobs'),
  jobsApi: { sessions: jest.fn(), getSettings: jest.fn(), create: jest.fn(), importTicket: jest.fn(), saveRules: jest.fn(), archive: jest.fn() },
  jiraApi: { settings: jest.fn(), boards: jest.fn(), sprints: jest.fn(), tickets: jest.fn(), importKeys: jest.fn(), save: jest.fn(), test: jest.fn(), unlink: jest.fn() },
}));
jest.mock('../src/api/jobCore', () => {
  const actual = jest.requireActual('../src/api/jobCore');
  return {
    ...actual,
    jobCoreApi: { home: jest.fn(), status: jest.fn(() => Promise.resolve({ agents: 0, checks: 0, needs_you: 0, engine: 'up' })), command: jest.fn() },
    subscribeToJobChanges: jest.fn(() => ({ close: jest.fn() })),
  };
});

const jobs = jobsApi as unknown as Record<string, jest.Mock>;
const jira = jiraApi as unknown as Record<string, jest.Mock>;
const core = jobCoreApi as unknown as Record<string, jest.Mock>;

const S = (name: string, extra: Partial<JobSessionOption> = {}): JobSessionOption => ({
  name, workspace: `/work/${name}`, workspaces: [`/work/${name}`], provider: 'openai', model: 'gpt-x', agent_mode: 'default',
  yolo: false, session_type: 'workspace', loaded: false, current: false, updated_at: 1, ...extra,
});
const SESSIONS = [S('beta', { current: true }), S('alpha', { provider: 'anthropic', model: 'claude-y' })];
const RULES = { schema: [], overrides: {}, values: { 'defaults.max_retries': 4, 'defaults.max_cost_usd': 3, 'defaults.max_runtime_seconds': 900 } } as unknown as JobRules;

const row = (id: string, origin: string, extra: Partial<JobSummary> = {}): JobSummary => ({
  id, title: `Job ${id}`, key: '', state: 'running', verdict: '', mini: '', attention_detail: '', repository: '/repo',
  cost_usd: 0, actions: [], created_at: 1, updated_at: 1, steps: [], origin_session: origin, ...extra,
});

beforeEach(() => {
  jest.clearAllMocks();
  jobs.sessions.mockResolvedValue({ current: 'beta', sessions: SESSIONS });
  jobs.getSettings.mockResolvedValue({ stage_models: {}, plan_approval: true, rules: RULES });
  jobs.create.mockResolvedValue({ job: { id: 'new1' } });
});

describe('session helpers', () => {
  it('defaults to the app session only when it is a saved session', () => {
    expect(defaultJobSession(SESSIONS, 'alpha')).toBe('alpha');
    expect(defaultJobSession(SESSIONS, 'gone')).toBeNull();
    expect(defaultJobSession(SESSIONS, null)).toBeNull();
    expect(sessionModelLabel(S('x', { provider: 'ollama', model: '' }))).toBe('ollama');
  });

  it('reads New job defaults from rules values, blank when unset', () => {
    expect(newJobDefaults(RULES)).toEqual({ retries: '4', cost: '3', runtime: '900' });
    expect(newJobDefaults(realRules as unknown as JobRules)).toEqual({ retries: '2', cost: '', runtime: '' });
    expect(newJobDefaults(undefined)).toEqual({ retries: '', cost: '', runtime: '' });
  });
});

describe('New job sheet (workspace first)', () => {
  const open = (sessionName: string | null) => {
    const onCreated = jest.fn();
    const screen = render(<NewJobSheet visible sessionName={sessionName} provider={null} model={null} onClose={jest.fn()} onCreated={onCreated} />);
    return { screen, onCreated };
  };

  it('derives recent workspaces from sessions and maps a workspace to its session', () => {
    expect(recentWorkspaces(SESSIONS)).toEqual(['/work/beta', '/work/alpha']);
    expect(sessionForWorkspace(SESSIONS, '/work/alpha/')?.name).toBe('alpha');
    expect(sessionForWorkspace(SESSIONS, '/elsewhere')).toBeUndefined();
    expect(defaultWorkspace(SESSIONS, 'alpha')).toBe('/work/alpha');
    expect(defaultWorkspace(SESSIONS, null)).toBe('/work/beta');
    expect(defaultWorkspace([], null)).toBe('');
    expect(isTicketKey('SCRUM-50')).toBe(true);
    expect(isTicketKey('Fix SCRUM-50')).toBe(false);
  });

  it('preselects the app session workspace, prefills rule defaults and sends the picked workspace', async () => {
    const { screen, onCreated } = open('beta');
    await screen.findByTestId('job-workspace-/work/alpha');
    await waitFor(() => expect(screen.getByLabelText('Workspace').props.value).toBe('/work/beta'));
    expect(screen.getByTestId('job-workspace-/work/beta').props.accessibilityState).toMatchObject({ checked: true });

    fireEvent.press(screen.getByTestId('job-workspace-/work/alpha'));
    expect(screen.getByTestId('job-workspace-/work/alpha').props.accessibilityState).toMatchObject({ checked: true });
    expect(screen.getByText(/Model, mode and environment from session alpha \(anthropic · claude-y\)/)).toBeTruthy();
    fireEvent.press(screen.getByTestId('job-advanced-toggle'));
    await waitFor(() => expect(screen.getByLabelText('Retries').props.value).toBe('4'));
    expect(screen.getByLabelText('Cost USD').props.value).toBe('3');
    expect(screen.getByLabelText('Runtime sec').props.value).toBe('900');
    fireEvent.changeText(screen.getByLabelText('Title or ticket key'), 'Fix it');
    fireEvent.press(screen.getByText('Queue job'));
    await waitFor(() => expect(jobs.create).toHaveBeenCalled());
    expect(jobs.create.mock.calls[0][0]).toMatchObject({
      title: 'Fix it', session_name: 'alpha', workspace_root: '/work/alpha', max_retries: 4, max_cost_usd: 3, max_runtime_seconds: 900,
    });
    // Jobs always run unattended: no auto-approve flag and no model override is sent.
    expect(jobs.create.mock.calls[0][0].execution).toEqual({});
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith({ id: 'new1' }));
  });

  it('is not a dead end without saved sessions: a typed workspace creates the job', async () => {
    jobs.sessions.mockResolvedValue({ current: null, sessions: [] });
    const { screen } = open(null);
    fireEvent.changeText(screen.getByLabelText('Title or ticket key'), 'Fix it');
    fireEvent.press(screen.getByText('Queue job'));
    expect(await screen.findByText('Choose the workspace this job works in.')).toBeTruthy();
    expect(jobs.create).not.toHaveBeenCalled();
    fireEvent.changeText(screen.getByLabelText('Workspace'), '/src/new-repo');
    fireEvent.press(screen.getByText('Queue job'));
    await waitFor(() => expect(jobs.create).toHaveBeenCalled());
    expect(jobs.create.mock.calls[0][0]).toMatchObject({ title: 'Fix it', workspace_root: '/src/new-repo' });
    expect(jobs.create.mock.calls[0][0].session_name).toBeUndefined();
  });

  it('offers no unattended / auto-approve control: jobs always run unattended', async () => {
    const { screen } = open('beta');
    await waitFor(() => expect(screen.getByLabelText('Workspace').props.value).toBe('/work/beta'));
    fireEvent.press(screen.getByTestId('job-advanced-toggle'));
    expect(screen.queryByTestId('job-unattended')).toBeNull();
    expect(screen.queryByText(/unattended/i)).toBeNull();
    expect(screen.queryByText(/auto-approve/i)).toBeNull();
    fireEvent.changeText(screen.getByLabelText('Title or ticket key'), 'Fix it');
    fireEvent.press(screen.getByText('Queue job'));
    await waitFor(() => expect(jobs.create).toHaveBeenCalled());
    expect(jobs.create.mock.calls[0][0].execution).not.toHaveProperty('auto_approve_writes');
  });

  it('imports a Jira ticket when the title is a ticket key', async () => {
    jobs.importTicket.mockResolvedValue({ created: [{ key: 'SCRUM-50', job_id: 'j50' }] });
    const { screen, onCreated } = open('beta');
    await waitFor(() => expect(screen.getByLabelText('Workspace').props.value).toBe('/work/beta'));
    fireEvent.changeText(screen.getByLabelText('Title or ticket key'), 'SCRUM-50');
    expect(screen.queryByLabelText('Description')).toBeNull();
    fireEvent.press(screen.getByText('Import ticket'));
    await waitFor(() => expect(jobs.importTicket).toHaveBeenCalled());
    expect(jobs.importTicket.mock.calls[0][0]).toBe('SCRUM-50');
    expect(jobs.importTicket.mock.calls[0][1]).toBe('beta');
    expect(jobs.importTicket.mock.calls[0][2]).toMatchObject({ workspace_root: '/work/beta' });
    expect(jobs.importTicket.mock.calls[0][2].execution).not.toHaveProperty('auto_approve_writes');
    expect(jobs.create).not.toHaveBeenCalled();
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith({ id: 'j50' }));
  });

  it('cost field: blank uses the Settings default, 0 is sent explicitly as no cap', async () => {
    expect(costCapPayload('')).toBeUndefined();
    expect(costCapPayload(' 0 ')).toBe(0);
    expect(costCapPayload('12.5')).toBe(12.5);
    expect(costCapPayload('-1')).toBeUndefined();
    expect(costPlaceholder(RULES)).toBe('Settings default ($3)');
    expect(costPlaceholder({ ...RULES, values: { 'defaults.max_cost_usd': 0 } } as unknown as JobRules)).toBe('Settings default (no cap)');
    expect(costPlaceholder(null)).toBe('Settings default ($100)');
    const { screen } = open('beta');
    fireEvent.press(screen.getByTestId('job-advanced-toggle'));
    await waitFor(() => expect(screen.getByLabelText('Cost USD').props.placeholder).toBe('Settings default ($3)'));
    fireEvent.changeText(screen.getByLabelText('Cost USD'), '0');
    expect(screen.getByTestId('job-cost-hint').props.children).toMatch(/no cap/);
    fireEvent.changeText(screen.getByLabelText('Title or ticket key'), 'Fix it');
    fireEvent.press(screen.getByText('Queue job'));
    await waitFor(() => expect(jobs.create).toHaveBeenCalled());
    expect(jobs.create.mock.calls[0][0].max_cost_usd).toBe(0);
  });

  it('cost field left blank omits max_cost_usd so the server applies the Settings default', async () => {
    const { screen } = open('beta');
    fireEvent.press(screen.getByTestId('job-advanced-toggle'));
    await waitFor(() => expect(screen.getByLabelText('Cost USD').props.value).toBe('3'));
    fireEvent.changeText(screen.getByLabelText('Cost USD'), '');
    fireEvent.changeText(screen.getByLabelText('Title or ticket key'), 'Fix it');
    fireEvent.press(screen.getByText('Queue job'));
    await waitFor(() => expect(jobs.create).toHaveBeenCalled());
    expect('max_cost_usd' in jobs.create.mock.calls[0][0]).toBe(false);
  });

  it('builds ticket import bodies workspace-first', () => {
    expect(ticketImportBody(['A-1'], { workspace: '/w', session: 's', force: true }))
      .toEqual({ keys: ['A-1'], defaults: { workspace_root: '/w' }, session_name: 's', force: true });
    expect(ticketImportBody(['A-1'], { workspace: 'git@github.com:acme/api.git' }))
      .toEqual({ keys: ['A-1'], defaults: { repository: 'git@github.com:acme/api.git' } });
    expect(ticketImportBody(['A-1'], 'legacy')).toEqual({ keys: ['A-1'], session_name: 'legacy' });
    expect(doneSkippedKeys([{ key: 'A-1', reason: 'latest job is done (…)' }, { key: 'A-2', reason: 'active job exists' }])).toEqual(['A-1']);
  });

  it('keeps limits the user typed before settings arrive', async () => {
    let resolve: (v: unknown) => void = () => undefined;
    jobs.getSettings.mockReturnValue(new Promise(r => { resolve = r; }));
    const { screen } = open('beta');
    fireEvent.press(screen.getByTestId('job-advanced-toggle'));
    fireEvent.changeText(screen.getByLabelText('Cost USD'), '7');
    resolve({ stage_models: {}, plan_approval: true, rules: RULES });
    await waitFor(() => expect(screen.getByLabelText('Retries').props.value).toBe('4'));
    expect(screen.getByLabelText('Cost USD').props.value).toBe('7');
  });

  it('falls back to the app session on servers without /api/jobs/sessions', async () => {
    jobs.sessions.mockRejectedValue(new ApiError(404, 'nope'));
    const { screen } = open('beta');
    await waitFor(() => expect(screen.getByText('Blank uses beta’s workspace.')).toBeTruthy());
    fireEvent.changeText(screen.getByLabelText('Title or ticket key'), 'Fix it');
    fireEvent.press(screen.getByText('Queue job'));
    await waitFor(() => expect(jobs.create).toHaveBeenCalled());
    expect(jobs.create.mock.calls[0][0].session_name).toBe('beta');
  });
});

describe('Jira import asks for the workspace (session optional)', () => {
  const linked = () => {
    jira.settings.mockResolvedValue({ configured: true, base_url: 'https://j', email: 'e', mode: 'cloud', api_token_set: true, pat_set: false, writeback: false, sources: {}, preferences: { boards: [{ id: 1, name: 'B', project: 'P', type: 'scrum' }], default_jql: '', assigned_to_me: false } });
    jira.boards.mockResolvedValue({ boards: [], my_projects: [] });
    jira.sprints.mockResolvedValue({ sprints: [] });
    jira.tickets.mockResolvedValue({ tickets: [{ key: 'A-1', summary: 'Do it', url: '', status: 'To Do', status_category: '', issue_type: '', priority: '', assignee: '', labels: [] }], statuses: [], jql: '' });
  };

  it('opens the workspace sheet and imports into the chosen workspace with its matching session', async () => {
    linked();
    jira.importKeys.mockResolvedValue({ created: [{ key: 'A-1', job_id: 'j9' }] });
    const onImported = jest.fn();
    const screen = render(<JiraBrowser active sessionName="beta" onImported={onImported} />);
    fireEvent.press(await screen.findByText('Do it'));
    fireEvent.press(screen.getByText('Create 1 job'));
    expect(jira.importKeys).not.toHaveBeenCalled();
    await screen.findByTestId('job-import-workspace-/work/alpha');
    expect(screen.getByText('Import tickets')).toBeTruthy();
    await waitFor(() => expect(screen.getByTestId('job-import-workspace').props.value).toBe('/work/beta'));
    fireEvent.press(screen.getByTestId('job-import-workspace-/work/alpha'));
    expect(screen.getByText(/from session alpha/)).toBeTruthy();
    fireEvent.press(screen.getByTestId('job-import-confirm'));
    await waitFor(() => expect(jira.importKeys).toHaveBeenCalledWith(['A-1'], { workspace: '/work/alpha', session: 'alpha', force: false }));
    await waitFor(() => expect(onImported).toHaveBeenCalledWith(['j9']));
    expect(await screen.findByText(/Created 1 job in alpha/)).toBeTruthy();
  });

  it('imports into a git URL without a session and offers Run again for done tickets', async () => {
    linked();
    jira.importKeys
      .mockResolvedValueOnce({ created: [], skipped: [{ key: 'A-1', job_id: 'old', reason: 'latest job is done (choose Run again, or --force, to start a new job)' }] })
      .mockResolvedValueOnce({ created: [{ key: 'A-1', job_id: 'j10' }] });
    const screen = render(<JiraBrowser active sessionName="beta" />);
    fireEvent.press(await screen.findByText('Do it'));
    fireEvent.press(screen.getByText('Create 1 job'));
    const field = await screen.findByTestId('job-import-workspace');
    fireEvent.changeText(field, 'https://github.com/acme/api.git');
    expect(screen.getByText(/server clones this repository/)).toBeTruthy();
    fireEvent.press(screen.getByTestId('job-import-confirm'));
    await waitFor(() => expect(jira.importKeys).toHaveBeenCalledWith(['A-1'], { workspace: 'https://github.com/acme/api.git', session: null, force: false }));
    fireEvent.press(await screen.findByTestId('jira-run-again'));
    fireEvent.press(screen.getByTestId('job-import-confirm'));
    await waitFor(() => expect(jira.importKeys).toHaveBeenLastCalledWith(['A-1'], expect.objectContaining({ force: true })));
  });

  it('requires a workspace or repository', async () => {
    linked();
    jobs.sessions.mockResolvedValue({ current: null, sessions: [] });
    const screen = render(<JiraBrowser active sessionName={null} />);
    fireEvent.press(await screen.findByText('Do it'));
    fireEvent.press(screen.getByText('Create 1 job'));
    fireEvent.press(await screen.findByTestId('job-import-confirm'));
    expect(await screen.findByText('Choose a workspace or repository for the imported jobs.')).toBeTruthy();
    expect(jira.importKeys).not.toHaveBeenCalled();
  });
});

describe('jobs list session filter', () => {
  const rows = [row('a1', 'alpha'), row('b1', 'beta'), row('b2', 'beta', { title: 'cache fix' }), row('n1', '')];

  it('filters by origin session and keeps all sessions by default', () => {
    expect(filterJobs(rows, '', false).map(r => r.id)).toEqual(['a1', 'b1', 'b2', 'n1']);
    expect(filterJobs(rows, '', false, ALL_SESSIONS)).toHaveLength(4);
    expect(filterJobs(rows, '', false, 'beta').map(r => r.id)).toEqual(['b1', 'b2']);
    expect(filterJobs(rows, '', false, '').map(r => r.id)).toEqual(['n1']);
    expect(filterJobs(rows, 'cache', false, 'beta').map(r => r.id)).toEqual(['b2']);
    expect(filterJobs(rows, 'alpha', false).map(r => r.id)).toEqual(['a1']);
    expect(jobSessions(rows)).toEqual([{ name: 'beta', count: 2 }, { name: 'alpha', count: 1 }, { name: '', count: 1 }]);
  });

  it('renders session chips, filters rows and shows the session on each row', async () => {
    core.home.mockResolvedValue({ needs_you: [], running: [], jobs: rows, counts: { needs_you: 0, total: 4 } });
    const navigation = { navigate: jest.fn(), setOptions: jest.fn(), addListener: jest.fn(() => jest.fn()) };
    const screen = render(<ToastHost><JobsListScreen route={{} as any} navigation={navigation as any} /></ToastHost>);
    await screen.findByTestId('job-row-a1');
    expect(screen.getByTestId('jobs-scope-chip')).toBeTruthy();
    expect(within(screen.getByTestId('job-row-a1')).getByText(/session · alpha/)).toBeTruthy();
    expect(screen.getByTestId('jobs-session-all').props.accessibilityState).toMatchObject({ checked: true });
    fireEvent.press(screen.getByTestId('jobs-session-beta'));
    expect(screen.queryByTestId('job-row-a1')).toBeNull();
    expect(screen.getByTestId('job-row-b1')).toBeTruthy();
    expect(screen.getByText('session · beta')).toBeTruthy();
    fireEvent.press(screen.getByTestId('jobs-session-none'));
    expect(screen.getByTestId('job-row-n1')).toBeTruthy();
    expect(screen.queryByTestId('job-row-b1')).toBeNull();
  });
});

describe('jobs chrome', () => {
  it('labels the scope as all sessions and rows with their origin session', async () => {
    const nav = render(<JobsNavBar current="Work" onNavigate={jest.fn()} />);
    expect(nav.getByLabelText('Showing jobs from all sessions')).toBeTruthy();
    const r = render(<JobRow job={row('x1', 'gamma')} onPress={jest.fn()} />);
    expect(r.getByTestId('job-row-x1-meta').props.children).toBe('session · gamma  /repo');
  });
});

describe('settings renders the full rules schema', () => {
  it('shows every server section including New job defaults', async () => {
    jobs.getSettings.mockResolvedValue({ stage_models: {}, plan_approval: true, rules: realRules });
    const screen = render(<ToastHost><JobRulesForm active /></ToastHost>);
    const rules = realRules as unknown as JobRules;
    for (const section of rules.schema) expect(await screen.findByTestId(`rules-${section.id}`)).toBeTruthy();
    expect(rules.schema.reduce((n, s) => n + s.fields.length, 0)).toBe(33);
    const defaults = within(screen.getByTestId('rules-defaults'));
    expect(defaults.getByText('New job defaults')).toBeTruthy();
    for (const field of rules.schema.find(s => s.id === 'defaults')!.fields) expect(defaults.getByTestId(`rule-${field.path}`)).toBeTruthy();
  });
});
