import React from 'react';
import { Alert } from 'react-native';
import { act, fireEvent, render, waitFor } from '@testing-library/react-native';
import { inspectorApi } from '../src/api/inspector';
import { isPlanModeBlocked, planModeFromGroups, refreshPlanMode, setPlanModeRemote } from '../src/api/planMode';
import { PLAN_MODE_PLACEHOLDER, PlanModeBadge, PlanModeStrip } from '../src/components/PlanModeIndicator';
import { useConnectionStore } from '../src/store/connection';
import { duskColors, paperColors } from '../src/theme/tokens';

jest.mock('../src/api/inspector', () => ({
  ...jest.requireActual('../src/api/inspector'),
  inspectorApi: { getVariables: jest.fn(), setVariable: jest.fn(), unsetVariable: jest.fn() },
}));

const inspector = inspectorApi as unknown as Record<string, jest.Mock>;
const groups = (value: unknown) => [
  { name: 'behavior', variables: [
    { key: 'yolo', type: 'bool', default: false, value: false, is_default: true, help: '' },
    { key: 'plan_mode', type: 'bool', default: false, value, is_default: value === false, help: '' },
  ] },
];

beforeEach(() => {
  jest.clearAllMocks();
  useConnectionStore.setState({ isConnected: true, activeSessionName: 's1', planMode: false } as any);
  inspector.setVariable.mockImplementation(async (key: string, value: unknown) => ({ ok: true, key, value }));
});

describe('plan mode api helpers', () => {
  it('reads plan_mode out of the grouped variables', () => {
    expect(planModeFromGroups(groups(true) as any)).toBe(true);
    expect(planModeFromGroups(groups(false) as any)).toBe(false);
    expect(planModeFromGroups([])).toBeNull();
  });

  it('refreshes the store for the active session only', async () => {
    inspector.getVariables.mockResolvedValue({ groups: groups(true) });
    await expect(refreshPlanMode('s1')).resolves.toBe(true);
    expect(inspector.getVariables).toHaveBeenCalledWith('s1');
    expect(useConnectionStore.getState().planMode).toBe(true);

    inspector.getVariables.mockResolvedValue({ groups: groups(false) });
    await refreshPlanMode('other');
    expect(useConnectionStore.getState().planMode).toBe(true);
  });

  it('keeps the last value when the refresh fails', async () => {
    useConnectionStore.setState({ planMode: true } as any);
    inspector.getVariables.mockRejectedValue(new Error('offline'));
    await expect(refreshPlanMode('s1')).resolves.toBeNull();
    expect(useConnectionStore.getState().planMode).toBe(true);
  });

  it('toggles through POST /api/variables/plan_mode', async () => {
    await expect(setPlanModeRemote(true, 's1')).resolves.toBe(true);
    expect(inspector.setVariable).toHaveBeenCalledWith('plan_mode', true, 's1');
    expect(useConnectionStore.getState().planMode).toBe(true);
  });

  it('recognises the plan-mode refusal envelope', () => {
    expect(isPlanModeBlocked('{"ok": false, "error_code": "plan_mode_blocked"}')).toBe(true);
    expect(isPlanModeBlocked({ ok: false, error_code: 'plan_mode_blocked' })).toBe(true);
    expect(isPlanModeBlocked('{"ok": true}')).toBe(false);
    expect(isPlanModeBlocked(undefined)).toBe(false);
  });

  it('resets on session switch so another session never inherits the badge', () => {
    useConnectionStore.setState({ planMode: true } as any);
    act(() => { useConnectionStore.getState().setActiveSession('s2'); });
    expect(useConnectionStore.getState().planMode).toBe(false);
  });
});

describe('plan mode indicators', () => {
  it('renders nothing while plan mode is off', () => {
    const screen = render(<><PlanModeBadge /><PlanModeStrip /></>);
    expect(screen.queryByTestId('plan-mode-badge')).toBeNull();
    expect(screen.queryByTestId('plan-mode-strip')).toBeNull();
  });

  it('shows the header badge and confirms before turning off', async () => {
    useConnectionStore.setState({ planMode: true } as any);
    const alert = jest.spyOn(Alert, 'alert').mockImplementation(() => undefined);
    const screen = render(<PlanModeBadge />);
    expect(screen.getByText('🔒 Plan')).toBeTruthy();
    fireEvent.press(screen.getByTestId('plan-mode-badge'));
    expect(alert).toHaveBeenCalledWith('Turn off plan mode?', expect.any(String), expect.any(Array));
    const buttons = alert.mock.calls[0][2] as Array<{ text: string; onPress?: () => void }>;
    await act(async () => { buttons.find(button => button.text === 'Turn off')?.onPress?.(); });
    expect(inspector.setVariable).toHaveBeenCalledWith('plan_mode', false, 's1');
    await waitFor(() => expect(screen.queryByTestId('plan-mode-badge')).toBeNull());
    alert.mockRestore();
  });

  it('shows the composer strip with a Turn off button', async () => {
    useConnectionStore.setState({ planMode: true } as any);
    const screen = render(<PlanModeStrip />);
    expect(screen.getByText(/read-only\. Writing is blocked\./)).toBeTruthy();
    fireEvent.press(screen.getByLabelText('Turn off plan mode'));
    await waitFor(() => expect(inspector.setVariable).toHaveBeenCalledWith('plan_mode', false, 's1'));
    await waitFor(() => expect(screen.queryByTestId('plan-mode-strip')).toBeNull());
  });

  it('updates live when the store changes (SSE / poll path)', () => {
    const screen = render(<PlanModeStrip />);
    expect(screen.queryByTestId('plan-mode-strip')).toBeNull();
    act(() => { useConnectionStore.getState().setPlanMode(true); });
    expect(screen.getByTestId('plan-mode-strip')).toBeTruthy();
  });

  it('uses a dedicated colour distinct from attention and accent in both themes', () => {
    for (const colors of [paperColors, duskColors]) {
      expect(colors.plan.ink).toBeTruthy();
      expect(colors.plan.ink).not.toBe(colors.attention.ink);
      expect(colors.plan.ink).not.toBe(colors.accent.ink);
      expect(colors.plan.wash).not.toBe(colors.attention.wash);
    }
    expect(PLAN_MODE_PLACEHOLDER).toMatch(/Plan mode/);
  });
});
