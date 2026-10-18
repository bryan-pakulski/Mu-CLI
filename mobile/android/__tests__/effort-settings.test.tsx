import React from 'react';
import { act, fireEvent, render, waitFor } from '@testing-library/react-native';
import { effortApi, effortLabel, effortOptions } from '../src/api/effort';
import { inspectorApi } from '../src/api/inspector';
import { jobsApi } from '../src/api/jobs';
import { providersApi } from '../src/api/providers';
import { AdvancedSettingsSheet, optionLabel } from '../src/components/AdvancedSettingsSheet';
import { EffortControl } from '../src/components/EffortControl';
import { JobRulesForm } from '../src/components/JobRulesForm';
import { ToastHost } from '../src/ui';
import { useConnectionStore } from '../src/store/connection';

jest.mock('../src/api/providers', () => ({
  ...jest.requireActual('../src/api/providers'),
  providersApi: { getCurrent: jest.fn() },
}));
jest.mock('../src/api/inspector', () => ({
  ...jest.requireActual('../src/api/inspector'),
  inspectorApi: { getVariables: jest.fn(), setVariable: jest.fn(), unsetVariable: jest.fn() },
}));
jest.mock('../src/api/jobs', () => ({
  ...jest.requireActual('../src/api/jobs'),
  jobsApi: { getSettings: jest.fn(), saveRules: jest.fn(), toolchains: jest.fn(), buildToolchain: jest.fn() },
}));

const getCurrent = providersApi.getCurrent as jest.Mock;
const inspector = inspectorApi as unknown as Record<string, jest.Mock>;

beforeEach(() => {
  jest.clearAllMocks();
  useConnectionStore.setState({ isConnected: true, activeSessionName: 's1', activeProvider: 'ollama', activeModel: 'qwen3.8:27b' } as any);
  getCurrent.mockResolvedValue({ provider: 'ollama', model: 'qwen3.8:27b', ollama_api_key_set: false, effort: 'high',
    effort_levels: ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'] });
  inspector.setVariable.mockImplementation(async (key: string, value: unknown) => ({ ok: true, key, value }));
  inspector.unsetVariable.mockResolvedValue({ ok: true, key: 'reasoning_effort', value: '' });
});

describe('effort helpers', () => {
  it('lists Default first and labels levels', () => {
    expect(effortOptions(['low', 'high'])).toEqual(['', 'low', 'high']);
    expect(effortOptions()).toEqual(['', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']);
    expect(effortLabel('')).toBe('Default');
    expect(effortLabel('xhigh')).toBe('XHigh');
  });

  it('resets to the provider default with DELETE', async () => {
    await expect(effortApi.set('', 's1')).resolves.toBe('');
    expect(inspector.unsetVariable).toHaveBeenCalledWith('reasoning_effort', 's1');
    await expect(effortApi.set('low', 's1')).resolves.toBe('low');
    expect(inspector.setVariable).toHaveBeenCalledWith('reasoning_effort', 'low', 's1');
  });
});

describe('EffortControl', () => {
  it('shows the session effort and sets a new level from the picker', async () => {
    const screen = render(<EffortControl />);
    await waitFor(() => expect(screen.getByLabelText('Effort: High')).toBeTruthy());
    fireEvent.press(screen.getByLabelText('Effort: High'));
    fireEvent.press(screen.getByLabelText('Effort Low'));
    await waitFor(() => expect(inspector.setVariable).toHaveBeenCalledWith('reasoning_effort', 'low', 's1'));
    await waitFor(() => expect(screen.getByLabelText('Effort: Low')).toBeTruthy());
  });

  it('returns to the provider default', async () => {
    const screen = render(<EffortControl variant="row" />);
    await waitFor(() => expect(screen.getByText(/High · /)).toBeTruthy());
    fireEvent.press(screen.getByLabelText('Effort: High'));
    fireEvent.press(screen.getByLabelText('Effort Default'));
    await waitFor(() => expect(inspector.unsetVariable).toHaveBeenCalledWith('reasoning_effort', 's1'));
  });

  it('refreshes when the provider or model changes', async () => {
    render(<EffortControl />);
    await waitFor(() => expect(getCurrent).toHaveBeenCalledTimes(1));
    act(() => { useConnectionStore.setState({ activeModel: 'glm-5.3' } as any); });
    await waitFor(() => expect(getCurrent).toHaveBeenCalledTimes(2));
  });
});

describe('AdvancedSettingsSheet', () => {
  const groups = [
    { name: 'speed & effort', variables: [
      { key: 'reasoning_effort', type: 'str', default: '', value: '', is_default: true, help: 'Effort',
        options: ['', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'] },
      { key: 'turn_roll_deferred', type: 'bool', default: true, value: true, is_default: true, help: '' },
    ] },
    { name: 'ollama', variables: [
      { key: 'ollama_num_ctx_default', type: 'int', default: 65536, value: 65536, is_default: true, help: '' },
    ] },
  ];

  it('renders options as pickers and saves the chosen value', async () => {
    inspector.getVariables.mockResolvedValue({ groups });
    const screen = render(<AdvancedSettingsSheet visible onClose={jest.fn()} />);
    await waitFor(() => expect(screen.getByText('Speed & effort')).toBeTruthy());
    fireEvent.press(screen.getByText('Speed & effort'));
    expect(screen.getByLabelText('reasoning_effort Default')).toBeTruthy();
    fireEvent.press(screen.getByLabelText('reasoning_effort medium'));
    await waitFor(() => expect(inspector.setVariable).toHaveBeenCalledWith('reasoning_effort', 'medium'));
    expect(optionLabel('')).toBe('Default');
  });

  it('explains cloud vs local Ollama routes', async () => {
    inspector.getVariables.mockResolvedValue({ groups });
    const screen = render(<AdvancedSettingsSheet visible onClose={jest.fn()} />);
    await waitFor(() => expect(screen.getByText('Ollama')).toBeTruthy());
    fireEvent.press(screen.getByText('Ollama'));
    expect(screen.getByText(/ignores num_ctx and keep_alive/)).toBeTruthy();
    expect(screen.getByText(/at least 64k/)).toBeTruthy();
  });
});

describe('job stage effort rule', () => {
  it('renders as a select in the job rules form', async () => {
    (jobsApi.getSettings as jest.Mock).mockResolvedValue({ stage_models: {}, plan_approval: true, missing: [], rules: {
      schema: [{ id: 'iterations', title: 'Agent iterations', hint: '', fields: [
        { path: 'jobs.stage_effort.review', label: 'Review stage effort', kind: 'select', default: '', hint: '',
          options: [['', 'Provider default'], ['low', 'Low'], ['medium', 'Medium'], ['high', 'High'], ['max', 'Max']] },
      ] }],
      values: { 'jobs.stage_effort.review': '' }, overrides: {},
    } });
    (jobsApi.toolchains as jest.Mock).mockResolvedValue({ toolchains: [] });
    const screen = render(<ToastHost><JobRulesForm active /></ToastHost>);
    await waitFor(() => expect(screen.getByText('Review stage effort')).toBeTruthy());
    expect(screen.getByText('Medium')).toBeTruthy();
  });
});
