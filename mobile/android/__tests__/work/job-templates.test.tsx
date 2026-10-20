// Run a job in a container template (documentation/plans/job_templates.md): the "Run in"
// picker (groups, preselection, remember, fit warnings + Allow), the New job and Jira import
// payloads, and the unverified state for checks that could not run (§5).
import React from 'react';
import { StyleSheet } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { fireEvent, render, waitFor, within } from '@testing-library/react-native';

import { environmentsApi, type EnvironmentsVM } from '../../src/api/environments';
import { containersApi } from '../../src/api/containers';
import { jobsApi, ticketImportBody, type JobSessionOption } from '../../src/api/jobs';
import type { CheckRun, JobVM } from '../../src/api/jobCore';
import {
  LAST_RUN_IN_KEY, allowEgress, fitWarnings, preselectRunIn, runInExecution, runInGroups, saveLastRunIn, setupSummary, templateSummary,
} from '../../src/api/runIn';
import { RunInPicker } from '../../src/ui/RunInPicker';
import { NewJobSheet } from '../../src/components/NewJobSheet';
import { JobImportSheet } from '../../src/components/JobSessionPicker';
import { ChecksTab, TranscriptItem } from '../../src/components/JobTabs';
import { runOutcome, runVerification, sessionItems, suiteStatus } from '../../src/components/jobViews';
import { STATUS, runStatus } from '../../src/theme/status';
import { ToastHost } from '../../src/ui';

jest.mock('../../src/api/environments', () => ({
  ...jest.requireActual('../../src/api/environments'),
  environmentsApi: { get: jest.fn(), approve: jest.fn() },
}));
jest.mock('../../src/api/containers', () => ({
  ...jest.requireActual('../../src/api/containers'),
  containersApi: { patchTemplate: jest.fn() },
}));
jest.mock('../../src/api/jobs', () => ({
  ...jest.requireActual('../../src/api/jobs'),
  jobsApi: { sessions: jest.fn(), getSettings: jest.fn(), create: jest.fn(), importTicket: jest.fn() },
}));

const envs = environmentsApi as unknown as Record<string, jest.Mock>;
const containers = containersApi as unknown as Record<string, jest.Mock>;
const jobs = jobsApi as unknown as Record<string, jest.Mock>;
const storage = AsyncStorage as unknown as { getItem: jest.Mock; setItem: jest.Mock };

const VM = (extra: Partial<EnvironmentsVM> = {}): EnvironmentsVM => ({
  environments: [{ name: 'rust-build', spec: 'environment:rust-build', status: 'running', description: 'rustup 1.83' }],
  templates: [
    { name: 'rsq-node-build', spec: 'template:rsq-node-build', image: 'mucli-template:rsq', description: 'node 20, pnpm', size: '2.1 GB', size_bytes: 2.1 * 1024 ** 3,
      gpus: '', job_setup: ['npm --prefix a ci', 'npm --prefix b ci', 'npm --prefix c ci'], egress_allow: ['registry.npmjs.org'], fit: { missing_egress: [], toolchains: ['node'] } },
    { name: 'gpu-enabled-ubuntu24', spec: 'template:gpu-enabled-ubuntu24', image: 'mucli-template:gpu', description: 'CUDA', size: '15.8 GB', size_bytes: 0,
      gpus: 'all', job_setup: [], egress_allow: ['pypi.org'], fit: { missing_egress: ['registry.npmjs.org'], toolchains: [] } },
  ],
  images: [],
  docker_in_docker: { available: false, engine: '', message: '', install: [] },
  gpu: { supported: true, reason: '' },
  resolved: { source: 'default', bindings: [], summary: '', error: '' },
  remembered: null,
  ...extra,
});

const S = (name: string, extra: Partial<JobSessionOption> = {}): JobSessionOption => ({
  name, workspace: `/work/${name}`, workspaces: [`/work/${name}`], provider: 'openai', model: 'gpt-x', agent_mode: 'default',
  yolo: false, session_type: 'workspace', loaded: false, current: false, updated_at: 1, ...extra,
});

beforeEach(() => {
  jest.clearAllMocks();
  storage.getItem.mockResolvedValue(null);
  envs.get.mockResolvedValue(VM());
  containers.patchTemplate.mockResolvedValue({ name: 'gpu-enabled-ubuntu24' });
  jobs.sessions.mockResolvedValue({ current: 'beta', sessions: [S('beta', { current: true })] });
  jobs.getSettings.mockResolvedValue({ stage_models: {}, plan_approval: true });
  jobs.create.mockResolvedValue({ job: { id: 'new1' } });
});

describe('Run in helpers', () => {
  it('groups templates and environments, each sorted by name', () => {
    const groups = runInGroups(VM());
    expect(groups.templates.map(t => t.name)).toEqual(['gpu-enabled-ubuntu24', 'rsq-node-build']);
    expect(groups.environments.map(e => e.spec)).toEqual(['environment:rust-build']);
    expect(runInGroups(null)).toEqual({ templates: [], environments: [] });
  });

  it('preselects remembered, then session environment, then last used, then default', () => {
    const vm = VM({ remembered: { source: 'template:rsq-node-build' } });
    expect(preselectRunIn({ vm, sessionEnvironment: 'rust-build', lastUsed: 'template:gpu-enabled-ubuntu24' })).toBe('template:rsq-node-build');
    expect(preselectRunIn({ vm: VM(), sessionEnvironment: 'rust-build', lastUsed: 'template:gpu-enabled-ubuntu24' })).toBe('environment:rust-build');
    expect(preselectRunIn({ vm: VM(), sessionEnvironment: '', lastUsed: 'template:gpu-enabled-ubuntu24' })).toBe('template:gpu-enabled-ubuntu24');
    expect(preselectRunIn({ vm: VM(), sessionEnvironment: null, lastUsed: null })).toBe('');
    // A candidate that is no longer offered is skipped.
    expect(preselectRunIn({ vm: VM({ remembered: { source: 'template:gone' } }), sessionEnvironment: 'gone-too', lastUsed: 'template:rsq-node-build' })).toBe('template:rsq-node-build');
  });

  it('summarises a template row: description, GPU, setup and size', () => {
    expect(setupSummary(['npm ci', 'npm --prefix x ci', 'cargo fetch'])).toBe('npm ×2, cargo');
    const [gpu, node] = runInGroups(VM()).templates;
    expect(templateSummary(node)).toBe('node 20, pnpm · setup: npm ×3 · 2.1 GB');
    expect(templateSummary(gpu)).toBe('CUDA · GPU · no job setup · 15.8 GB');
    expect(fitWarnings(gpu)).toEqual(['registry.npmjs.org']);
    expect(allowEgress(gpu, ['registry.npmjs.org', 'pypi.org'])).toEqual(['pypi.org', 'registry.npmjs.org']);
  });

  it('builds the execution payload: template by name, remember only when asked', () => {
    expect(runInExecution('template:rsq-node-build')).toEqual({ template: 'rsq-node-build' });
    expect(runInExecution('template:rsq-node-build', true)).toEqual({ template: 'rsq-node-build', remember_template: true });
    expect(runInExecution('environment:rust-build')).toEqual({ environments: [{ env: 'environment:rust-build', paths: [], docker: false }] });
    expect(runInExecution('')).toEqual({});
    expect(runInExecution('', true)).toEqual({});
  });

  it('stores the last choice per workspace on the device', async () => {
    await saveLastRunIn('/work/beta/', 'template:rsq-node-build');
    expect(storage.setItem).toHaveBeenCalledWith(LAST_RUN_IN_KEY, JSON.stringify({ '/work/beta': 'template:rsq-node-build' }));
  });
});

describe('RunInPicker', () => {
  const Harness = (props: { sessionEnvironment?: string; onAdvanced?: () => void }) => {
    const [value, setValue] = React.useState('');
    const [remember, setRemember] = React.useState(false);
    return (
      <ToastHost>
        <RunInPicker active workspace="/work/beta" session="beta" debounceMs={0} value={value} onChange={setValue}
          remember={remember} onRememberChange={setRemember} {...props} />
      </ToastHost>
    );
  };

  it('preselects the last template used for the workspace and lists the three groups', async () => {
    storage.getItem.mockResolvedValue(JSON.stringify({ '/work/beta': 'template:rsq-node-build' }));
    const screen = render(<Harness />);
    await waitFor(() => expect(screen.getByTestId('run-in-value').props.children).toBe('rsq-node-build'));
    expect(envs.get).toHaveBeenCalledWith({ workspace: '/work/beta', repository: undefined, session: 'beta', images: false });
    expect(screen.getByTestId('run-in-trigger').props.accessibilityLabel).toBe('Run in: rsq-node-build');
    fireEvent.press(screen.getByTestId('run-in-trigger'));
    const sheet = within(screen.getByTestId('run-in-sheet'));
    expect(sheet.getAllByRole('header').map(h => h.props.children)).toEqual(
      expect.arrayContaining(['Default runtime', 'Templates', 'Environments (snapshotted at start)']));
    const option = screen.getByTestId('run-in-option-template:rsq-node-build');
    expect(option.props.accessibilityState).toMatchObject({ checked: true, selected: true });
    expect(sheet.getByText('node 20, pnpm · setup: npm ×3 · 2.1 GB')).toBeTruthy();
    expect(sheet.getByText('running · rustup 1.83')).toBeTruthy();
    // No editor on this surface: Advanced… is hidden.
    expect(screen.queryByTestId('run-in-advanced')).toBeNull();
  });

  it('prefers the session environment over the last used template', async () => {
    storage.getItem.mockResolvedValue(JSON.stringify({ '/work/beta': 'template:rsq-node-build' }));
    const screen = render(<Harness sessionEnvironment="rust-build" />);
    await waitFor(() => expect(screen.getByTestId('run-in-value').props.children).toBe('rust-build'));
    expect(screen.queryByTestId('run-in-remember')).toBeNull();
  });

  it('shows the fit warning and Allow PATCHes the template egress after confirming', async () => {
    const onAdvanced = jest.fn();
    const screen = render(<Harness onAdvanced={onAdvanced} />);
    await waitFor(() => expect(envs.get).toHaveBeenCalled());
    fireEvent.press(screen.getByTestId('run-in-trigger'));
    expect(screen.getByTestId('run-in-fit-row-gpu-enabled-ubuntu24-registry.npmjs.org')).toBeTruthy();
    expect(screen.getByText("can't reach registry.npmjs.org")).toBeTruthy();
    fireEvent.press(screen.getByTestId('run-in-option-template:gpu-enabled-ubuntu24'));
    // The chosen template's warning stays visible under the field.
    expect(screen.getByTestId('run-in-fit-field-gpu-enabled-ubuntu24-registry.npmjs.org')).toBeTruthy();
    fireEvent.press(screen.getByTestId('run-in-allow-gpu-enabled-ubuntu24-registry.npmjs.org'));
    expect(containers.patchTemplate).not.toHaveBeenCalled();
    expect(screen.getByText('Allow registry.npmjs.org?')).toBeTruthy();
    fireEvent.press(screen.getByTestId('run-in-allow-confirm'));
    await waitFor(() => expect(containers.patchTemplate).toHaveBeenCalledWith('gpu-enabled-ubuntu24', { egress_allow: ['pypi.org', 'registry.npmjs.org'] }));
    await waitFor(() => expect(envs.get).toHaveBeenCalledTimes(2));
    fireEvent.press(screen.getByTestId('run-in-trigger'));
    fireEvent.press(screen.getByTestId('run-in-advanced'));
    expect(onAdvanced).toHaveBeenCalled();
  });

  it('has 44dp rows and labelled options', async () => {
    const screen = render(<Harness />);
    await waitFor(() => expect(envs.get).toHaveBeenCalled());
    fireEvent.press(screen.getByTestId('run-in-trigger'));
    const row = screen.getByTestId('run-in-option-default');
    expect(row.props.accessibilityRole).toBe('radio');
    expect(row.props.accessibilityLabel).toBe('Default runtime, Shared runtime image · no project tools');
    for (const id of ['run-in-option-default', 'run-in-option-template:rsq-node-build', 'run-in-trigger', 'run-in-allow-gpu-enabled-ubuntu24-registry.npmjs.org']) {
      expect(StyleSheet.flatten(screen.getByTestId(id).props.style).minHeight).toBeGreaterThanOrEqual(44);
    }
  });
});

describe('payloads', () => {
  it('New job sends execution.template and remember_template', async () => {
    envs.get.mockResolvedValue(VM({ remembered: { source: 'template:rsq-node-build' } }));
    const onCreated = jest.fn();
    const screen = render(<NewJobSheet visible sessionName="beta" provider={null} model={null} onClose={jest.fn()} onCreated={onCreated} />);
    await waitFor(() => expect(screen.getByLabelText('Workspace').props.value).toBe('/work/beta'));
    await waitFor(() => expect(screen.getByTestId('run-in-value').props.children).toBe('rsq-node-build'), { timeout: 3000 });
    fireEvent(screen.getByTestId('run-in-remember'), 'valueChange', true);
    fireEvent.changeText(screen.getByLabelText('Title or ticket key'), 'Fix it');
    fireEvent.press(screen.getByText('Queue job'));
    await waitFor(() => expect(jobs.create).toHaveBeenCalled());
    const execution = jobs.create.mock.calls[0][0].execution;
    expect(execution).toMatchObject({ template: 'rsq-node-build', remember_template: true });
    expect(execution.environments).toBeUndefined();
    await waitFor(() => expect(storage.setItem).toHaveBeenCalledWith(LAST_RUN_IN_KEY, JSON.stringify({ '/work/beta': 'template:rsq-node-build' })));
  });

  it('New job ticket import carries execution.template in the defaults', async () => {
    jobs.importTicket.mockResolvedValue({ created: [{ key: 'SCRUM-1', job_id: 'j1' }] });
    const screen = render(<NewJobSheet visible sessionName="beta" provider={null} model={null} onClose={jest.fn()} onCreated={jest.fn()} />);
    await waitFor(() => expect(screen.getByLabelText('Workspace').props.value).toBe('/work/beta'));
    await waitFor(() => expect(envs.get).toHaveBeenCalled());
    fireEvent.press(screen.getByTestId('run-in-trigger'));
    fireEvent.press(screen.getByTestId('run-in-option-template:gpu-enabled-ubuntu24'));
    fireEvent.changeText(screen.getByLabelText('Title or ticket key'), 'SCRUM-1');
    fireEvent.press(screen.getByText('Import ticket'));
    await waitFor(() => expect(jobs.importTicket).toHaveBeenCalled());
    expect(jobs.importTicket.mock.calls[0][2].execution).toMatchObject({ template: 'gpu-enabled-ubuntu24' });
    expect(jobs.importTicket.mock.calls[0][2].execution.remember_template).toBeUndefined();
  });

  it('New job Advanced… replaces the choice with the environments editor', async () => {
    const screen = render(<NewJobSheet visible sessionName="beta" provider={null} model={null} onClose={jest.fn()} onCreated={jest.fn()} />);
    await waitFor(() => expect(envs.get).toHaveBeenCalled());
    fireEvent.press(screen.getByTestId('run-in-trigger'));
    fireEvent.press(screen.getByTestId('run-in-option-template:rsq-node-build'));
    fireEvent.press(screen.getByTestId('run-in-trigger'));
    fireEvent.press(screen.getByTestId('run-in-advanced'));
    expect(screen.getByTestId('environments-editor')).toBeTruthy();
    expect(screen.getByTestId('run-in-value').props.children).toBe('Advanced: 1 environment');
    fireEvent.changeText(screen.getByLabelText('Title or ticket key'), 'Fix it');
    fireEvent.press(screen.getByText('Queue job'));
    await waitFor(() => expect(jobs.create).toHaveBeenCalled());
    const execution = jobs.create.mock.calls[0][0].execution;
    expect(execution.environments).toEqual([{ env: 'template:rsq-node-build', paths: [], docker: false }]);
    expect(execution.template).toBeUndefined();
  });

  it('Jira import sends defaults.execution.template for every ticket', async () => {
    const onConfirm = jest.fn();
    const screen = render(<JobImportSheet visible defaultSession="beta" confirmLabel="Import 2" onClose={jest.fn()} onConfirm={onConfirm} />);
    await waitFor(() => expect(screen.getByLabelText('Workspace').props.value).toBe('/work/beta'));
    await waitFor(() => expect(envs.get).toHaveBeenCalled());
    fireEvent.press(screen.getByTestId('job-import-run-in-trigger'));
    fireEvent.press(screen.getByTestId('job-import-run-in-option-template:rsq-node-build'));
    fireEvent(screen.getByTestId('job-import-run-in-remember'), 'valueChange', true);
    fireEvent.press(screen.getByTestId('job-import-confirm'));
    const target = onConfirm.mock.calls[0][0];
    expect(target).toMatchObject({ workspace: '/work/beta', session: 'beta', execution: { template: 'rsq-node-build', remember_template: true } });
    expect(ticketImportBody(['A-1', 'A-2'], target)).toEqual({
      keys: ['A-1', 'A-2'], session_name: 'beta',
      defaults: { workspace_root: '/work/beta', execution: { template: 'rsq-node-build', remember_template: true } },
    });
    // Default runtime adds nothing to the defaults.
    expect(ticketImportBody(['A-1'], { workspace: '/work/beta' })).toEqual({ keys: ['A-1'], defaults: { workspace_root: '/work/beta' } });
  });
});

describe('unverified checks (§5)', () => {
  const unrunnable = {
    suite: 'lint', command: 'npx eslint .', passed: false, stdout: '', stderr: 'sh: 1: eslint: not found', preexisting_failures: [],
    status: 'unrunnable', unrunnable: { tool: 'eslint', cause: 'eslint comes from node_modules, which is not installed', hint: "add `npm ci` to this template's job setup" },
  };
  const passed = { suite: 'unit', command: 'pytest', passed: true, stdout: 'ok', stderr: '', preexisting_failures: [] };
  const run: CheckRun = { run: 1, passed: false, head_sha: 'abc', new_failures: 0, preexisting_failures: 0, suites: [passed, unrunnable], verified: false, unrunnable: 1 };
  const job = { id: 'j', state: 'needs_input', actions: [], fix_attempts: 0, checks: { runs: [run] } } as unknown as JobVM;

  it('the vocabulary has unverified (? caution) even before the token file is regenerated', () => {
    expect(STATUS.unverified).toEqual({ icon: '?', tone: 'caution', label: 'unverified' });
    expect(runStatus('unrunnable')).toBe('unverified');
  });

  it('classifies unrunnable suites and runs as unverified, never failed', () => {
    expect(suiteStatus(unrunnable)).toEqual({ status: 'unverified', label: 'unverified' });
    expect(suiteStatus(passed)).toEqual({ status: 'done', label: 'passed' });
    expect(runVerification(run)).toMatchObject({ verified: false, unrunnable: 1 });
    expect(runOutcome(run)).toEqual({ status: 'unverified', label: 'unverified' });
    expect(runVerification({ ...run, verified: undefined, unrunnable: undefined }).verified).toBe(false);
  });

  it('renders the unverified banner, chip, glyph, cause and hint in the Checks tab', () => {
    const screen = render(<ChecksTab job={job} invoke={jest.fn()} busy={false} />);
    const banner = within(screen.getByTestId('checks-unverified-banner'));
    expect(banner.getByText("Unverified: 1 check couldn't run")).toBeTruthy();
    expect(banner.getByText('?')).toBeTruthy();
    expect(banner.getByText(/eslint comes from node_modules/)).toBeTruthy();
    expect(banner.getByText("Fix: add `npm ci` to this template's job setup")).toBeTruthy();
    expect(screen.getByTestId('suite-unverified-lint')).toBeTruthy();
    expect(screen.getByTestId('suite-lint').props.accessibilityLabel).toBe('lint, unverified');
    expect(screen.getByTestId('suite-unrunnable-lint')).toBeTruthy();
    expect(screen.getByLabelText('Run 1, unverified')).toBeTruthy();
    expect(screen.getByText('last: run 1 unverified')).toBeTruthy();
    expect(screen.queryByText('pre-existing failures')).toBeNull();
  });

  it('shows no banner for a verified run', () => {
    const ok = { ...job, checks: { runs: [{ ...run, passed: true, suites: [passed], verified: true, unrunnable: 0 }] } } as unknown as JobVM;
    const screen = render(<ChecksTab job={ok} invoke={jest.fn()} busy={false} />);
    expect(screen.queryByTestId('checks-unverified-banner')).toBeNull();
  });
});

describe('job setup progress (§4)', () => {
  const ev = (id: number, payload: Record<string, unknown>) => ({ id, event_type: 'environment_setup', reason: '', created_at: id, payload });

  it('folds environment_setup events into one progress line per command', () => {
    const items = sessionItems({ live: true, events: [
      ev(1, { binding: 'node', command: 'npm --prefix a ci', index: 0, status: 'running' }),
      ev(2, { binding: 'node', command: 'npm --prefix a ci', index: 0, status: 'passed', exit_code: 0 }),
      ev(3, { binding: 'node', command: 'npm --prefix b ci', index: 1, status: 'failed', exit_code: 1, output_tail: 'npm ERR! 403' }),
    ], stages: {} }, '');
    expect(items).toEqual([
      { kind: 'setup', status: 'done', label: 'done', summary: 'setup 1 · [node] npm --prefix a ci', text: '' },
      { kind: 'setup', status: 'failed', label: 'failed · exit 1', summary: 'setup 2 · [node] npm --prefix b ci', text: 'npm ERR! 403' },
    ]);
  });

  it('renders a setup line with its status and expandable output', () => {
    const screen = render(<TranscriptItem item={{ kind: 'setup', status: 'failed', label: 'failed · exit 1', summary: 'setup 2 · [node] npm ci', text: 'npm ERR! 403' }} />);
    const line = screen.getByLabelText('Job setup: setup 2 · [node] npm ci, failed · exit 1');
    expect(screen.getByText('✗')).toBeTruthy();
    fireEvent.press(line);
    expect(screen.getByText('npm ERR! 403')).toBeTruthy();
  });
});

