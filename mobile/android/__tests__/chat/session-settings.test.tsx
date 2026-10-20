// Session settings live with the conversation (mobile_parity.md §6 Settings): variables are
// grouped pickers, switches and fields; auto-approve queues offline with If-Match and rolls back
// when the server refuses; plan mode and the mode list act on the open session.
import React from 'react';
import { Text } from 'react-native';
import { act, fireEvent, render, waitFor } from '@testing-library/react-native';
import { inspectorApi } from '../../src/api/inspector';
import { SessionVariables, optionLabel } from '../../src/components/chat/SessionVariables';
import { SessionSettingsSheet } from '../../src/components/chat/SessionSettingsSheet';
import { useAutoApprove } from '../../src/components/chat/useAutoApprove';
import { useConnectionStore } from '../../src/store/connection';
import { ThemeProvider } from '../../src/theme/ThemeContext';

jest.mock('../../src/api/inspector', () => ({
  ...jest.requireActual('../../src/api/inspector'),
  inspectorApi: { getVariables: jest.fn(), setVariable: jest.fn(), unsetVariable: jest.fn() },
}));
jest.mock('../../src/components/EffortControl', () => ({ EffortControl: () => null }));

const inspector = inspectorApi as unknown as Record<string, jest.Mock>;
const groups = [
  { name: 'speed & effort', variables: [
    { key: 'reasoning_effort', type: 'str', default: '', value: '', is_default: true, help: 'Effort', options: ['', 'low', 'medium', 'high'] },
    { key: 'turn_roll_deferred', type: 'bool', default: true, value: true, is_default: true, help: '' },
  ] },
  { name: 'behavior', variables: [{ key: 'yolo', type: 'bool', default: false, value: false, is_default: true, help: '' }] },
  { name: 'ollama', variables: [{ key: 'ollama_num_ctx_default', type: 'int', default: 65536, value: 65536, is_default: true, help: '' }] },
];

const wrap = (node: React.ReactElement) => render(<ThemeProvider statusBar={false}>{node}</ThemeProvider>);

beforeEach(() => {
  jest.clearAllMocks();
  useConnectionStore.setState({ isConnected: true, activeSessionName: 's1', yolo: false, planMode: false, sessionRevision: 7 } as never);
  inspector.getVariables.mockResolvedValue({ groups });
  inspector.setVariable.mockImplementation(async (key: string, value: unknown) => ({ ok: true, key, value }));
  inspector.unsetVariable.mockResolvedValue({ ok: true, key: 'reasoning_effort', value: '' });
});

describe('SessionVariables', () => {
  it('renders options as pickers and saves the chosen value', async () => {
    const ui = wrap(<SessionVariables sessionName="s1" active />);
    await waitFor(() => expect(ui.getByText('Speed & effort')).toBeTruthy());
    fireEvent.press(ui.getByText('Speed & effort'));
    expect(ui.getByLabelText('reasoning_effort Default')).toBeTruthy();
    await act(async () => { fireEvent.press(ui.getByLabelText('reasoning_effort medium')); });
    expect(inspector.setVariable).toHaveBeenCalledWith('reasoning_effort', 'medium');
    expect(optionLabel('')).toBe('Default');
  });

  it('filters by key or help text and explains cloud vs local Ollama', async () => {
    const ui = wrap(<SessionVariables sessionName="s1" active />);
    await waitFor(() => expect(ui.getByText('Ollama')).toBeTruthy());
    fireEvent.press(ui.getByText('Ollama'));
    expect(ui.getByText(/ignores num_ctx and keep_alive/)).toBeTruthy();
    fireEvent.changeText(ui.getByTestId('variables-filter'), 'num_ctx');
    expect(ui.queryByText('Speed & effort')).toBeNull();
    expect(ui.getByText('Ollama Num Ctx Default')).toBeTruthy();
  });

  it('asks for a session first', () => {
    const ui = wrap(<SessionVariables sessionName={null} active />);
    expect(ui.getByText(/Load a session first/)).toBeTruthy();
  });
});

describe('auto-approve', () => {
  let api: ReturnType<typeof useAutoApprove>;
  function Probe() { api = useAutoApprove(); return <Text>{String(api.yolo)}</Text>; }

  it('rolls back when the server refuses', async () => {
    inspector.setVariable.mockRejectedValueOnce(new Error('409 conflict'));
    wrap(<Probe />);
    await act(async () => { await api.update(true); });
    expect(useConnectionStore.getState().yolo).toBe(false);
    expect(api.error).toMatch(/409 conflict/);
  });

  it('applies the server value when it succeeds', async () => {
    wrap(<Probe />);
    await act(async () => { await api.update(true); });
    expect(inspector.setVariable).toHaveBeenCalledWith('yolo', true, 's1');
    expect(useConnectionStore.getState().yolo).toBe(true);
  });

  it('queues offline and keeps the optimistic value', async () => {
    useConnectionStore.setState({ isConnected: false } as never);
    wrap(<Probe />);
    await act(async () => { await api.update(true); });
    expect(inspector.setVariable).not.toHaveBeenCalled();
    expect(useConnectionStore.getState().yolo).toBe(true);
    expect(useConnectionStore.getState().pendingMutations).toBeGreaterThanOrEqual(0);
  });
});

describe('SessionSettingsSheet', () => {
  const modes = [
    { name: 'default', display_name: 'Default', description: 'General work', disabled: false },
    { name: 'feature', display_name: 'Feature', description: 'Plans', disabled: false },
    { name: 'security', display_name: 'Security', description: '', disabled: true },
  ] as never[];

  it('switches auto-approve, plan mode and the mode for the open session', async () => {
    const onSelectMode = jest.fn();
    const onOpenProviders = jest.fn();
    const ui = wrap(<SessionSettingsSheet visible onClose={jest.fn()} modes={modes} activeMode="default" onSelectMode={onSelectMode} onOpenProviders={onOpenProviders} />);
    await waitFor(() => expect(inspector.getVariables).toHaveBeenCalled());
    await act(async () => { fireEvent(ui.getByTestId('settings-auto-approve'), 'valueChange', true); });
    expect(inspector.setVariable).toHaveBeenCalledWith('yolo', true, 's1');
    await act(async () => { fireEvent(ui.getByTestId('settings-plan-mode'), 'valueChange', true); });
    expect(inspector.setVariable).toHaveBeenCalledWith('plan_mode', true, 's1');
    fireEvent.press(ui.getByTestId('settings-mode-feature'));
    expect(onSelectMode).toHaveBeenCalledWith('feature');
    expect(ui.getByText('Requires a workspace or container')).toBeTruthy();
    fireEvent.press(ui.getByText('Provider and model'));
    expect(onOpenProviders).toHaveBeenCalled();
  });
});
