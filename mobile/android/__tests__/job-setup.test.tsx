import React from 'react';
import { Alert } from 'react-native';
import { act, fireEvent, render, waitFor } from '@testing-library/react-native';
import * as Clipboard from 'expo-clipboard';

import { api } from '../src/api/client';
import {
  doctorStatus, doctorSummary, firstAttention, promptInitials, setupApi, stepStatus,
  type DoctorReport, type SetupVM,
} from '../src/api/setup';
import { ToastHost } from '../src/ui';
import { JobDoctorScreen } from '../src/screens/JobDoctorScreen';
import { JobSetupScreen } from '../src/screens/JobSetupScreen';

jest.mock('../src/api/client', () => ({ api: { get: jest.fn(), post: jest.fn() } }));
jest.mock('expo-clipboard', () => ({ setStringAsync: jest.fn(() => Promise.resolve(true)) }));

const get = api.get as jest.Mock;
const post = api.post as jest.Mock;

const VM: SetupVM = {
  done: 2, total: 4, all_checked: true, blocking: ['Job runtime image'], can_finish: false, completed_at: null, job: null, notice: '',
  steps: [
    { id: 'docker', title: 'Docker', why: 'Stages run in containers.', optional: false, checking: false, state: 'ok', detail: 'Docker 29.7.1',
      actions: [{ id: 'recheck', label: 'Check again', kind: 'run' }] },
    { id: 'runtime', title: 'Job runtime image', why: 'The image stages run in.', optional: false, checking: false, state: 'todo',
      detail: 'mucli-job-runtime:latest is not built yet',
      actions: [{ id: 'build', label: 'Build the runtime image', kind: 'job', hint: 'a few minutes the first time' }] },
    { id: 'claude', title: 'Claude Code', why: 'Run stages on Claude Code.', optional: true, checking: false, state: 'warn',
      detail: 'using your login session',
      actions: [
        { id: 'setup-token', label: 'Create a long-lived token (`claude setup-token`)', kind: 'terminal', command: 'claude setup-token' },
        { id: 'paste-token', label: 'Paste a Claude OAuth token', kind: 'prompts', prompts: [{ label: 'CLAUDE_CODE_OAUTH_TOKEN (hidden)', initial: 'never-shown', secret: true }] },
      ] },
    { id: 'models', title: 'mucli stage models', why: 'Models per stage.', optional: false, checking: false, state: 'ok', detail: 'plan anthropic/x',
      actions: [{ id: 'settings', label: 'Open Job settings', kind: 'nav', nav: 'settings' }] },
  ],
};

const REPORT: DoctorReport = {
  checks: [
    { name: 'docker', status: 'ok', detail: 'Docker 29.7.1' },
    { name: 'runtime_image', status: 'fail', detail: 'built from older mucli code; rebuild it' },
    { name: 'codex_credentials', status: 'warn', detail: 'login from ~/.codex/auth.json' },
  ],
  failed: 1, warnings: 1, needs_setup: true,
};

const nav = () => ({ navigate: jest.fn(), goBack: jest.fn(), setOptions: jest.fn(), addListener: jest.fn(() => jest.fn()) });
const renderSetup = (navigation = nav()) => ({ navigation, ...render(<ToastHost><JobSetupScreen navigation={navigation as any} route={{} as any} /></ToastHost>) });

beforeEach(() => jest.clearAllMocks());

describe('setup api helpers', () => {
  it('maps step and doctor states onto the planos status vocabulary', () => {
    expect(stepStatus({ state: 'ok', checking: false })).toBe('done');
    expect(stepStatus({ state: 'todo', checking: false })).toBe('waiting');
    expect(stepStatus({ state: 'optional', checking: false })).toBe('pending');
    expect(stepStatus({ state: 'ok', checking: true })).toBe('active');
    expect(doctorStatus('fail')).toBe('failed');
    expect(doctorSummary(REPORT)).toBe('1/3 ok · 1 failing · 1 warnings');
  });

  it('starts on the first step needing attention and never prefills secrets', () => {
    expect(firstAttention(VM)?.id).toBe('runtime');
    expect(promptInitials(VM.steps[2].actions[1])).toEqual(['']);
  });

  it('calls the setup and doctor endpoints', async () => {
    get.mockResolvedValue(VM);
    post.mockResolvedValue({ ok: true, setup: VM });
    await setupApi.get(true);
    expect(get).toHaveBeenCalledWith('/api/jobs/setup', expect.objectContaining({ query: { recheck: 1 } }));
    await setupApi.action('claude', 'paste-token', ['tok']);
    expect(post).toHaveBeenCalledWith('/api/jobs/setup/action', { step: 'claude', action: 'paste-token', inputs: ['tok'] }, expect.anything());
    await setupApi.doctor(true);
    expect(get).toHaveBeenLastCalledWith('/api/jobs/doctor', expect.objectContaining({ query: { deep: 1 } }));
  });
});

describe('JobSetupScreen', () => {
  it('shows progress, what is left and the first step that needs attention', async () => {
    get.mockResolvedValue(VM);
    const screen = renderSetup();
    await waitFor(() => expect(screen.getByTestId('job-setup-progress').props.children.join('')).toBe('2/4 done'));
    expect(screen.getByText('To do: Job runtime image')).toBeTruthy();
    expect(screen.getByTestId('setup-step-runtime-body')).toBeTruthy();
    expect(screen.queryByTestId('setup-step-docker-body')).toBeNull();
  });

  it('runs a job action and follows its live log', async () => {
    jest.useFakeTimers();
    try {
      get.mockResolvedValueOnce(VM);
      const running = { ...VM, job: { step: 'runtime', action: 'build', title: 'Build the runtime image', running: true, ok: null, lines: ['#1 building'] } };
      post.mockResolvedValue({ ok: true, message: 'started', setup: running });
      get.mockResolvedValue({ ...running, job: { ...running.job, running: false, ok: true, lines: ['#1 building', '#11 DONE'] } });
      const screen = renderSetup();
      await waitFor(() => screen.getByTestId('setup-action-runtime-build'));
      fireEvent.press(screen.getByTestId('setup-action-runtime-build'));
      await waitFor(() => expect(screen.getByText('#1 building')).toBeTruthy());
      expect(post).toHaveBeenCalledWith('/api/jobs/setup/action', { step: 'runtime', action: 'build', inputs: [] }, expect.anything());
      await act(async () => { jest.advanceTimersByTime(1100); });
      await waitFor(() => expect(screen.getByText('#1 building\n#11 DONE')).toBeTruthy());
    } finally {
      jest.useRealTimers();
    }
  });

  it('asks for secrets in a masked field and sends them once', async () => {
    get.mockResolvedValue(VM);
    post.mockResolvedValue({ ok: true, message: 'Claude token saved', setup: VM });
    const screen = renderSetup();
    await waitFor(() => screen.getByTestId('setup-step-claude'));
    fireEvent.press(screen.getByTestId('setup-step-claude'));
    fireEvent.press(screen.getByTestId('setup-action-claude-paste-token'));
    const field = screen.getByTestId('setup-prompt-0');
    expect(field.props.secureTextEntry).toBe(true);
    expect(field.props.value).toBe('');
    fireEvent.changeText(field, 'sk-ant-oat-secret');
    fireEvent.press(screen.getByTestId('setup-prompt-save'));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/api/jobs/setup/action',
      { step: 'claude', action: 'paste-token', inputs: ['sk-ant-oat-secret'] }, expect.anything()));
  });

  it('shows terminal commands to copy, then re-checks', async () => {
    get.mockResolvedValue(VM);
    post.mockResolvedValue({ ok: true, message: 'checked again', setup: VM });
    const screen = renderSetup();
    await waitFor(() => screen.getByTestId('setup-step-claude'));
    fireEvent.press(screen.getByTestId('setup-step-claude'));
    fireEvent.press(screen.getByTestId('setup-action-claude-setup-token'));
    expect(screen.getByText('claude setup-token')).toBeTruthy();
    fireEvent.press(screen.getByTestId('setup-terminal-copy'));
    expect(Clipboard.setStringAsync).toHaveBeenCalledWith('claude setup-token');
    fireEvent.press(screen.getByTestId('setup-terminal-done'));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/api/jobs/setup/action',
      { step: 'claude', action: 'setup-token', inputs: [] }, expect.anything()));
  });

  it('opens Job settings for nav actions and confirms before risky ones', async () => {
    const vm = { ...VM, steps: VM.steps.map(s => (s.id === 'docker' ? { ...s, actions: [{ id: 'recheck', label: 'Check again', kind: 'run' as const, confirm: 'Sure?' }] } : s)) };
    get.mockResolvedValue(vm);
    const alert = jest.spyOn(Alert, 'alert').mockImplementation(() => undefined);
    const screen = renderSetup();
    await waitFor(() => screen.getByTestId('setup-step-models'));
    fireEvent.press(screen.getByTestId('setup-step-models'));
    fireEvent.press(screen.getByTestId('setup-action-models-settings'));
    expect(screen.navigation.navigate).toHaveBeenCalledWith('JobSettings');
    fireEvent.press(screen.getByTestId('setup-step-docker'));
    fireEvent.press(screen.getByTestId('setup-action-docker-recheck'));
    expect(alert).toHaveBeenCalledWith('Check again', 'Sure?', expect.any(Array));
    expect(post).not.toHaveBeenCalled();
    alert.mockRestore();
  });

  it('finishes when everything required is done', async () => {
    get.mockResolvedValue({ ...VM, can_finish: true, blocking: [] });
    post.mockResolvedValue({ ...VM, can_finish: true, completed_at: 1 });
    const screen = renderSetup();
    await waitFor(() => screen.getByTestId('job-setup-finish'));
    fireEvent.press(screen.getByTestId('job-setup-finish'));
    await waitFor(() => expect(post).toHaveBeenCalledWith('/api/jobs/setup/finish', {}));
    expect(screen.navigation.goBack).toHaveBeenCalled();
  });
});

describe('JobDoctorScreen', () => {
  it('lists checks, points at Setup when something fails and runs the deep check', async () => {
    get.mockResolvedValue(REPORT);
    const navigation = nav();
    const screen = render(<JobDoctorScreen navigation={navigation as any} route={{} as any} />);
    await waitFor(() => screen.getByTestId('doctor-check-runtime_image'));
    expect(screen.getByText('built from older mucli code; rebuild it')).toBeTruthy();
    fireEvent.press(screen.getByTestId('job-doctor-open-setup'));
    expect(navigation.navigate).toHaveBeenCalledWith('JobSetup');
    fireEvent.press(screen.getByTestId('job-doctor-deep'));
    await waitFor(() => expect(get).toHaveBeenLastCalledWith('/api/jobs/doctor', expect.objectContaining({ query: { deep: 1 } })));
  });
});

describe('Setup has a Doctor tab', () => {
  it('opens on the Doctor tab from the route and switches back to the checklist', async () => {
    get.mockImplementation((path: string) => Promise.resolve(path === '/api/jobs/doctor' ? REPORT : VM));
    const navigation = nav();
    const screen = render(<ToastHost><JobSetupScreen navigation={navigation as any} route={{ params: { tab: 'doctor' } } as any} /></ToastHost>);
    await waitFor(() => screen.getByTestId('doctor-check-runtime_image'));
    expect(screen.getByTestId('job-setup-tab-doctor').props.accessibilityState).toMatchObject({ selected: true });
    expect(screen.queryByTestId('job-setup-doctor')).toBeNull();
    fireEvent.press(screen.getByTestId('job-doctor-open-setup'));
    await waitFor(() => screen.getByTestId('job-setup-progress'));
    expect(navigation.navigate).not.toHaveBeenCalled();
    fireEvent.press(screen.getByTestId('job-setup-tab-doctor'));
    await waitFor(() => screen.getByTestId('job-doctor-checks'));
  });
});
