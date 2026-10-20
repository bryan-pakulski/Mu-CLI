import React from 'react';
import { fireEvent, render, waitFor } from '@testing-library/react-native';

import {
  bindingsFromDrafts, draftError, draftFromBinding, draftFromSpec, emptyDraft, environmentsApi, environmentsPillLabel,
  runInEnvironmentSummary, sourceLabel,
} from '../../src/api/environments';
import { jobCoreApi, type JobAction, type JobVM } from '../../src/api/jobCore';
import { sessionItems } from '../../src/components/jobViews';
import { BuildEnvironmentsField, BuildEnvironmentsSettings, EnvironmentsPill, JobEnvironmentsSheet } from '../../src/components/JobEnvironments';
import { ChecksTab } from '../../src/components/JobTabs';
import { ToastHost } from '../../src/ui';
import { JobActivityScreen } from '../../src/screens/JobActivityScreen';

jest.mock('../../src/api/environments', () => ({ ...jest.requireActual('../../src/api/environments'), environmentsApi: { get: jest.fn() } }));
jest.mock('../../src/api/jobCore', () => ({ ...jest.requireActual('../../src/api/jobCore'), jobCoreApi: { command: jest.fn(), activity: jest.fn(), status: jest.fn(() => new Promise(() => undefined)) } }));

const SOURCES = {
  environments: [{ name: 'rust-build', container: 'mucli-rust-build', spec: 'environment:mucli-rust-build', status: 'running' }],
  templates: [{ name: 'trt', spec: 'template:trt' }],
  images: [{ name: 'node:22', spec: 'image:node:22', size: '1GB' }],
  docker_in_docker: { available: false, engine: '', message: 'This job needs Docker-in-Docker, which requires Sysbox.', install: ['Install Sysbox CE', 'Restart Docker'] },
  gpu: { supported: true, reason: '1 NVIDIA GPU available' },
  resolved: { source: 'repo', summary: '', error: '', bindings: [
    { name: 'rust-build', source: 'environment', ref: 'rust-build', paths: ['resq-backend'], docker: false, gpus: null, label: 'rust-build → resq-backend' },
    { name: 'compose-it', source: 'template', ref: 'compose-it', paths: [], docker: true, gpus: '', label: 'compose-it ⬢ docker' }] },
  kept_jobs: [{ id: 'job-kept-1', title: 'Kept job', bytes: 2 * 1024 ** 3, bindings: 'rust-build → svc' }],
};

const action = (id: string, extra: Partial<JobAction> = {}): JobAction =>
  ({ id, label: id, enabled: true, reason: '', primary: false, danger: false, hidden: true, ...extra });

describe('build environment helpers', () => {
  it('formats the header pill', () => {
    expect(environmentsPillLabel({ count: 0 })).toBeNull();
    expect(environmentsPillLabel({ count: 1, status: 'ready' })).toBe('⬢ 1 environment');
    expect(environmentsPillLabel({ count: 3, status: 'preparing', kept: true })).toBe('⬢ 3 environments · preparing · kept');
    expect(sourceLabel('session')).toBe('session environment');
  });

  it('round-trips bindings through editor drafts', () => {
    const draft = draftFromBinding({ name: 'trt', source: 'template', ref: 'trt', paths: ['a', 'b'], docker: true, gpus: 'device=0,1' });
    expect(draft).toEqual({ source: 'template', ref: 'trt', paths: 'a, b', docker: true, gpus: '0,1' });
    expect(draftFromBinding({ name: 'x', source: 'image', ref: 'rust:1.89', paths: [], gpus: '' }).gpus).toBe('none');
    expect(draftFromSpec('image:node:22')).toMatchObject({ source: 'image', ref: 'node:22' });
    expect(bindingsFromDrafts([draft, { ...emptyDraft(), ref: '' }, { ...emptyDraft(), ref: 'rust-build' }])).toEqual([
      { env: 'template:trt', paths: ['a', 'b'], docker: true, gpus: '0,1' },
      { env: 'environment:rust-build', paths: [], docker: false },
    ]);
  });

  it('validates drafts locally', () => {
    expect(draftError({ ...emptyDraft(), ref: 'ok-name' })).toBeNull();
    expect(draftError({ ...emptyDraft(), ref: 'bad name' })).toMatch(/Invalid environment/);
    expect(draftError({ ...emptyDraft(), ref: 'x', paths: '../up' })).toMatch(/repository-relative/);
    expect(draftError({ ...emptyDraft(), source: 'image', ref: 'rust:1.89', gpus: 'all' })).toBeNull();
  });

  it('folds run_in_environment tool calls in the transcript', () => {
    expect(runInEnvironmentSummary({ env: 'node-build', command: 'npm test' })).toBe('[node-build] npm test');
    expect(runInEnvironmentSummary('{"env":"rust","command":"cargo build","cwd":"svc"}')).toBe('[rust] cargo build  (in svc)');
    const items = sessionItems({ live: false, events: [], stages: { implement: [
      { role: 'assistant', parts: [{ type: 'tool_call', tool_name: 'run_in_environment', tool_args: { env: 'node-build', command: 'npm test' } }] }] } } as any, 'implement');
    expect(items[0]).toMatchObject({ kind: 'tool', summary: '[node-build] npm test' });
    const ev = (id: number, event_type: string, payload: object, reason = '') => ({ id, event_type, reason, created_at: id, payload });
    const live = sessionItems({ live: true, stages: {}, events: [
      ev(1, 'build_environments_preparing', { total: 2 }, 'Preparing build environments (0/2)…'),
      ev(2, 'build_environment_progress', { done: 1, total: 2 }, 'Preparing build environments (1/2)…'),
      ev(3, 'build_environment_ready', { binding: 'rust-build' }),
      ev(4, 'build_environment_failed', { binding: 'node' }, 'no such image'),
      ev(5, 'environment_command_started', { stage: 'implement', env: 'node-build', command: 'npm test', cwd: 'web' }),
      ev(6, 'environment_command', { env: 'node-build', return_code: 0, duration_ms: 1500 }),
    ] } as any, '');
    expect(live.map(i => ('summary' in i ? i.summary : i.text))).toEqual([
      'Preparing build environments (0/2)…', 'Preparing build environments (1/2)…', 'build environment rust-build ready',
      'build environment node failed: no such image', '[node-build] npm test  (in web)', '[node-build] exit 0 · 1.5s']);
    expect(live[4].kind).toBe('tool');
  });
});

describe('job environments UI', () => {
  beforeEach(() => { (environmentsApi.get as jest.Mock).mockResolvedValue(SOURCES); (jobCoreApi.command as jest.Mock).mockReset(); });

  it('New job field shows the resolved set and edits an override', async () => {
    const onChange = jest.fn();
    const screen = render(<BuildEnvironmentsField active session="rsq" repository="/src/rsq" value={null} onChange={onChange} />);
    expect((await screen.findByTestId('environments-summary')).props.children).toBe('rust-build → resq-backend · compose-it ⬢ docker');
    expect(screen.getByText('from repo config (.mucli/jobs.toml)')).toBeTruthy();
    expect(environmentsApi.get).toHaveBeenCalledWith({ repository: '/src/rsq', session: 'rsq' });
    fireEvent.press(screen.getByTestId('environments-change'));
    expect(onChange.mock.calls[0][0]).toEqual([
      { source: 'environment', ref: 'rust-build', paths: 'resq-backend', docker: false, gpus: '' },
      { source: 'template', ref: 'compose-it', paths: '', docker: true, gpus: 'none' }]);
  });

  it('job sheet lists bindings and runs Refresh, Keep and Change', async () => {
    const invoke = jest.fn();
    const environments = { source: 'session', kept: true, refresh_pending: false, count: 1, bytes: 3 * 1024 ** 3, summary: 'rust-build', bindings: [{
      name: 'rust-build', source: 'environment', ref: 'mucli-rust-build', paths: [], primary: false, docker: true, gpus: 'all', status: 'failed',
      error: 'Build environment `rust-build` contains secret files', image_bytes: 3 * 1024 ** 3, home_bytes: 0, prepared_at: null,
      probed_env: ['PATH', 'RUSTUP_HOME'], skipped_paths: ['.ssh (secret)'], secret_paths: ['/etc/x.key (secret file pattern *.key)'],
      engine: 'sysbox', job_image: 'img', label: 'rust-build' }] };
    const actions = [action('refreshEnvironments', { label: 'Refresh environments', confirm: 'Re-snapshot?' }),
      action('keepEnvironments', { label: 'Unkeep environments' }), action('setEnvironments', { dialog: 'environments' })];
    const screen = render(<JobEnvironmentsSheet visible onClose={jest.fn()} environments={environments} actions={actions} invoke={invoke} busy={false} />);
    expect(screen.getByText('⬢ kept')).toBeTruthy();
    expect(screen.getByText('sysbox (Docker-in-Docker)')).toBeTruthy();
    expect(screen.getByText('PATH, RUSTUP_HOME')).toBeTruthy();
    expect(screen.getByText(/Secret scan hits \(1\)/)).toBeTruthy();
    fireEvent.press(screen.getByTestId('environments-refresh'));
    expect(invoke).toHaveBeenLastCalledWith(actions[0]);
    fireEvent.press(screen.getByText('Unkeep environments'));
    expect(invoke).toHaveBeenLastCalledWith(actions[1]);
    fireEvent.press(screen.getByTestId('environments-edit'));
    fireEvent.changeText(await screen.findByLabelText('Build environment 1 paths'), 'svc, web');
    fireEvent.press(screen.getByTestId('environments-save'));
    expect(invoke).toHaveBeenLastCalledWith('setEnvironments', { environments: [
      { env: 'environment:mucli-rust-build', paths: ['svc', 'web'], docker: true, gpus: 'all' }] });
  });

  it('header pill opens the sheet only when the job has environments', () => {
    const onPress = jest.fn();
    expect(render(<EnvironmentsPill summary={{ count: 0, kept: false, source: 'default', summary: '', status: '', bytes: 0 }} onPress={onPress} />).toJSON()).toBeNull();
    const screen = render(<EnvironmentsPill summary={{ count: 2, kept: false, source: 'repo', summary: '', status: 'ready', bytes: 0 }} onPress={onPress} />);
    fireEvent.press(screen.getByTestId('job-environments-pill'));
    expect(onPress).toHaveBeenCalled();
  });

  it('Settings card shows Sysbox guidance and unkeeps a kept job', async () => {
    (jobCoreApi.command as jest.Mock).mockResolvedValue({ ok: true, parts: ['state'], data: { kept: false } });
    const screen = render(<ToastHost><BuildEnvironmentsSettings active /></ToastHost>);
    expect(await screen.findByTestId('sysbox-guidance')).toBeTruthy();
    expect(screen.getByText('1. Install Sysbox CE')).toBeTruthy();
    expect(screen.getByText('✓ 1 NVIDIA GPU available')).toBeTruthy();
    fireEvent.press(screen.getByTestId('unkeep-job-kept-1'));
    await waitFor(() => expect(jobCoreApi.command).toHaveBeenCalledWith('job-kept-1', 'keepEnvironments', { keep: false }));
  });

  it('checks list the build environment that ran each suite', () => {
    const job = { state: 'review_done', actions: [], fix_attempts: 0, checks: { runs: [{ run: 1, passed: true, head_sha: 'abc', new_failures: 0, preexisting_failures: 0,
      suites: [{ suite: 'fe', command: 'npm test', passed: true, stdout: '', stderr: '', preexisting_failures: [], environment: 'node-build' }] }] } } as unknown as JobVM;
    const screen = render(<ChecksTab job={job} invoke={jest.fn()} busy={false} />);
    expect(screen.getByTestId('suite-env-fe')).toBeTruthy();
    expect(screen.getByText('node-build')).toBeTruthy();
  });

  it('Activity labels job engines and broker containers', async () => {
    const container = (name: string, extra: object) => ({ name, id: name, image: 'img', status: 'running', stage: '', started_at: null,
      cpu_percent: null, mem_bytes: null, mem_limit: null, ...extra });
    (jobCoreApi.activity as jest.Mock).mockResolvedValue({ generated_at: 0, engine: 'up', counts: { agents: 1, checks: 0, containers: 2, needs_you: 0 }, orphans: [],
      jobs: [{ id: 'job1', key: 'K-1', title: 'T', state: 'implementing', stage: 'implement', worker_id: null, heartbeat_at: null, lease_expires_at: null,
        since: 0, cost_usd: 0, kind: 'agent', last: '', containers: [
          container('mucli-job-abc-engine', { role: 'engine', gpus: 'all' }),
          container('mucli-job-abc-env-node-1', { role: 'broker', binding: 'node-build' })] }] });
    const navigation = { navigate: jest.fn(), addListener: jest.fn(() => jest.fn()) };
    const screen = render(<ToastHost><JobActivityScreen navigation={navigation as any} route={{} as any} /></ToastHost>);
    expect(await screen.findByText('Docker engine')).toBeTruthy();
    expect(screen.getByText('run in node-build')).toBeTruthy();
    expect(screen.getByText('GPUs all')).toBeTruthy();
    screen.unmount();
  });
});
