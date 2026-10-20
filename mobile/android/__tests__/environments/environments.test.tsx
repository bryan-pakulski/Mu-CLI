// Environments (mobile_parity.md §6 Environments): the list, an environment's page with its actions
// and sessions, a template's job fields, and the create form — rendered against fixtures captured
// from the server's own types (scripts/fixtures/capture_environments.py).
import React from 'react';
import { AccessibilityInfo } from 'react-native';
import { act, fireEvent, render, waitFor } from '@testing-library/react-native';
import fixture from './fixtures/server.json';

const mockNavigate = jest.fn();
const mockReplace = jest.fn();
const mockGoBack = jest.fn();
let mockParams: Record<string, unknown> = {};
jest.mock('@react-navigation/native', () => ({
  useNavigation: () => ({ navigate: mockNavigate, replace: mockReplace, goBack: mockGoBack }),
  useRoute: () => ({ params: mockParams }),
  useIsFocused: () => true,
}));

jest.mock('../../src/api/containers', () => ({
  containersApi: {
    list: jest.fn(), stats: jest.fn(), configuration: jest.fn(), create: jest.fn(), update: jest.fn(), job: jest.fn(),
    action: jest.fn(), attach: jest.fn(), detach: jest.fn(), remove: jest.fn(), snapshot: jest.fn(), patchTemplate: jest.fn(), removeTemplate: jest.fn(),
  },
}));
jest.mock('../../src/api/sessions', () => ({
  sessionsApi: { getContainerDefaults: jest.fn(async () => ({ dockerfile: 'FROM python:3.12\n', egress_allow: ['pypi.org'], egress_deny: [] })) },
}));

import { containersApi } from '../../src/api/containers';
import { useConnectionStore } from '../../src/store/connection';
import { ThemeProvider } from '../../src/theme/ThemeContext';
import { EnvironmentsScreen } from '../../src/screens/environments/EnvironmentsScreen';
import { EnvironmentDetailScreen } from '../../src/screens/environments/EnvironmentDetailScreen';
import { EnvironmentFormScreen } from '../../src/screens/environments/EnvironmentFormScreen';
import { TemplateDetailScreen } from '../../src/screens/environments/TemplateDetailScreen';
import { appendSample, envRowSub, envStatus, listState } from '../../src/screens/environments/envModel';

const api = containersApi as unknown as Record<string, jest.Mock>;
const wrap = (node: React.ReactElement) => render(<ThemeProvider statusBar={false}>{node}</ThemeProvider>);
const listing = fixture.listing as any;
const stats = fixture.stats as any;

beforeEach(() => {
  // Running rows breathe unless reduced motion is on; jest's AccessibilityInfo returns no promise.
  jest.spyOn(AccessibilityInfo, 'isReduceMotionEnabled').mockResolvedValue(true);
  Object.values(api).forEach(fn => fn.mockReset());
  api.list.mockResolvedValue(JSON.parse(JSON.stringify(listing)));
  api.stats.mockResolvedValue(stats);
  mockNavigate.mockReset(); mockReplace.mockReset(); mockGoBack.mockReset();
  mockParams = {};
  useConnectionStore.setState({ isConnected: true });
});

describe('the words the page uses', () => {
  it('describes rows with live use, never a placeholder', () => {
    const [research, scratch, builder] = listing.containers;
    expect(envRowSub(research, stats.containers['mucli-research'])).toBe('Running · CPU 38% · 1.5 GB');
    expect(envRowSub(scratch, stats.containers['mucli-scratch'])).toBe('Stopped · 200 MB retained');
    expect(envStatus(builder)).toBe('waiting');
    expect(envStatus(research)).toBe('active');
    expect(listState(listing.containers, listing.templates)).toEqual(['2 running', '1 stopped', '1 template']);
  });

  it('keeps a bounded chart history from successive samples', () => {
    const live = stats.containers['mucli-research'];
    let samples = appendSample([], live);
    samples = appendSample(samples, { ...live, sampled_at: live.sampled_at + 2, block_write_bytes: 4096 }, live);
    expect(samples[1].w).toBe(2048);
    for (let i = 0; i < 100; i += 1) samples = appendSample(samples, live, live);
    expect(samples.length).toBe(60);
  });
});

describe('EnvironmentsScreen', () => {
  it('lists environments and templates and opens them', async () => {
    const ui = wrap(<EnvironmentsScreen />);
    expect(await ui.findByText('mucli-research')).toBeTruthy();
    expect(ui.getByText('Running · CPU 38% · 1.5 GB')).toBeTruthy();
    expect(ui.getByText('used by 3 jobs · from mucli-research')).toBeTruthy();
    fireEvent.press(ui.getByText('mucli-research'));
    expect(mockNavigate).toHaveBeenCalledWith('EnvironmentDetail', { name: 'mucli-research' });
    fireEvent.press(ui.getByText('python-base'));
    expect(mockNavigate).toHaveBeenCalledWith('TemplateDetail', { name: 'python-base' });
    fireEvent.press(ui.getByTestId('environments-new'));
    expect(mockNavigate).toHaveBeenCalledWith('EnvironmentForm', { mode: 'create' });
  });

  it('says so when there is nothing yet', async () => {
    api.list.mockResolvedValue({ containers: [], templates: [] });
    const ui = wrap(<EnvironmentsScreen />);
    expect(await ui.findByText('No environments yet')).toBeTruthy();
  });
});

describe('EnvironmentDetailScreen', () => {
  it('shows the state, live facts, sessions and configuration of a running environment', async () => {
    mockParams = { name: 'mucli-research' };
    const ui = wrap(<EnvironmentDetailScreen />);
    expect(await ui.findByText('used by 2 jobs', { exact: false })).toBeTruthy();
    expect(ui.getByText('up 2h 3m', { exact: false })).toBeTruthy();
    expect(ui.getByText('used by refactor-auth, notes +1', { exact: false })).toBeTruthy();
    expect(await ui.findByTestId('environment-charts')).toBeTruthy();
    expect(ui.getByText('42')).toBeTruthy();
    expect(ui.getByTestId('session-triage')).toBeTruthy();
    expect(ui.getByText('python-base')).toBeTruthy();
  });

  it('stops it, opens the terminal and detaches a session', async () => {
    mockParams = { name: 'mucli-research' };
    api.action.mockResolvedValue({ ok: true });
    api.detach.mockResolvedValue({ ok: true });
    const ui = wrap(<EnvironmentDetailScreen />);
    await ui.findByTestId('environment-actions-stop');
    await act(async () => { fireEvent.press(ui.getByTestId('environment-actions-stop')); });
    expect(api.action).toHaveBeenCalledWith('mucli-research', 'stop');
    fireEvent.press(ui.getByTestId('environment-actions-shell'));
    expect(mockNavigate).toHaveBeenCalledWith('Shell', { container: 'mucli-research' });
    await act(async () => { fireEvent.press(ui.getByTestId('detach-notes')); });
    expect(api.detach).toHaveBeenCalledWith('mucli-research', 'notes');
  });

  it('attaches a named session and removes the environment only after confirming', async () => {
    mockParams = { name: 'mucli-research' };
    api.attach.mockResolvedValue({ ok: true });
    api.remove.mockResolvedValue(undefined);
    const ui = wrap(<EnvironmentDetailScreen />);
    await ui.findByTestId('environment-actions-more');
    fireEvent.press(ui.getByTestId('environment-actions-more'));
    await act(async () => { fireEvent.press(ui.getByText('Attach session')); });
    fireEvent.changeText(ui.getByTestId('server-action-input'), 'review');
    await act(async () => { fireEvent.press(ui.getByTestId('server-action-ok')); });
    expect(api.attach).toHaveBeenCalledWith('mucli-research', 'review');

    fireEvent.press(ui.getByTestId('environment-actions-more'));
    await act(async () => { fireEvent.press(ui.getByText('Remove environment')); });
    expect(api.remove).not.toHaveBeenCalled();
    await act(async () => { fireEvent.press(ui.getByTestId('server-action-ok')); });
    expect(api.remove).toHaveBeenCalledWith('mucli-research');
    expect(mockGoBack).toHaveBeenCalled();
  });

  it('puts a secret-scan block first, with the way to the job', async () => {
    mockParams = { name: 'mucli-builder' };
    const ui = wrap(<EnvironmentDetailScreen />);
    expect(await ui.findByText('Secret scan blocked job #a1b2c3d4')).toBeTruthy();
    fireEvent.press(ui.getByText('Open job'));
    expect(mockNavigate).toHaveBeenCalledWith('JobDetail', { jobId: 'a1b2c3d4e5f6' });
  });

  it('a stopped environment offers Start and no charts', async () => {
    mockParams = { name: 'mucli-scratch' };
    const ui = wrap(<EnvironmentDetailScreen />);
    expect(await ui.findByTestId('environment-actions-start')).toBeTruthy();
    expect(ui.queryByTestId('environment-charts')).toBeNull();
    expect(ui.getByTestId('environment-stopped')).toBeTruthy();
  });
});

describe('TemplateDetailScreen', () => {
  it('saves the job fields the server lets you edit', async () => {
    mockParams = { name: 'python-base' };
    api.patchTemplate.mockResolvedValue({ ok: true, template: { ...listing.templates[0], job_setup: ['uv sync', 'make build'] } });
    const ui = wrap(<TemplateDetailScreen />);
    await ui.findByTestId('template-setup');
    expect(ui.getByTestId('template-save').props.accessibilityState).toMatchObject({ disabled: true });
    fireEvent.changeText(ui.getByTestId('template-setup'), 'uv sync\n\nmake build\n');
    await act(async () => { fireEvent.press(ui.getByTestId('template-save')); });
    expect(api.patchTemplate).toHaveBeenCalledWith('python-base', { description: 'Python 3.12 with uv', job_setup: ['uv sync', 'make build'], egress_allow: ['pypi.org', 'files.pythonhosted.org'] });
    fireEvent.press(ui.getByTestId('template-actions-use'));
    expect(mockNavigate).toHaveBeenCalledWith('EnvironmentForm', { mode: 'create', template: 'python-base' });
  });
});

describe('EnvironmentFormScreen', () => {
  it('creates an environment from a template and opens it once the build is ready', async () => {
    mockParams = { mode: 'create', template: 'python-base' };
    api.create.mockResolvedValue({ ok: true, job_id: 'j1' });
    api.job.mockResolvedValueOnce({ job_id: 'j1', state: 'running', stage: 'build', message: 'Building image', logs: [{ seq: 1, stream: 'stdout', text: 'step 1' }] })
      .mockResolvedValueOnce({ job_id: 'j1', state: 'ready', stage: 'ready', message: 'Ready', logs: [] });
    const ui = wrap(<EnvironmentFormScreen />);
    await waitFor(() => expect(ui.getByTestId('env-name').props.value).toBe('python-base-env'));
    await act(async () => { fireEvent.press(ui.getByTestId('env-submit')); });
    await waitFor(() => expect(mockReplace).toHaveBeenCalledWith('EnvironmentDetail', { name: 'python-base-env' }), { timeout: 3000 });
    expect(api.create).toHaveBeenCalledWith(expect.objectContaining({ name: 'python-base-env', template_name: 'python-base', dockerfile: null, egress_allow: null, start: true }));
  });

  it('shows the failure and the build log when the build fails', async () => {
    mockParams = { mode: 'create' };
    api.create.mockResolvedValue({ ok: true, job_id: 'j2' });
    api.job.mockResolvedValue({ job_id: 'j2', state: 'error', stage: 'build', message: 'Build failed', detail: 'pip: no such package', logs: [] });
    const ui = wrap(<EnvironmentFormScreen />);
    await waitFor(() => expect(ui.getByTestId('env-dockerfile').props.value).toContain('FROM python'));
    fireEvent.changeText(ui.getByTestId('env-name'), 'box');
    await act(async () => { fireEvent.press(ui.getByTestId('env-submit')); });
    expect(await ui.findByText('pip: no such package')).toBeTruthy();
    expect(api.create).toHaveBeenCalledWith(expect.objectContaining({ name: 'box', dockerfile: 'FROM python:3.12\n', egress_allow: ['pypi.org'] }));
    expect(mockReplace).not.toHaveBeenCalled();
  });
});
