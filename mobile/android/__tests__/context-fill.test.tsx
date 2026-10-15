// Header context ring (G§9.14): the SSE `context_snapshot` fill is the
// source; /api/memory/state is polled only until a snapshot arrives for the
// open session.
import React from 'react';
import { Text } from 'react-native';
import { act, render } from '@testing-library/react-native';
import { memoryApi } from '../src/api/memory';
import { CONTEXT_POLL_MS, useContextFill } from '../src/components/ModernHeader';
import { normaliseFill, useContextFillStore } from '../src/store/contextFill';

jest.mock('../src/api/memory', () => ({ memoryApi: { getState: jest.fn() } }));

const getState = memoryApi.getState as jest.Mock;

function Probe({ session, connected = true }: { session: string | null; connected?: boolean }) {
  const fill = useContextFill(session, connected);
  return <Text testID="fill">{fill == null ? 'none' : fill.toFixed(2)}</Text>;
}

const flush = () => act(async () => { await Promise.resolve(); });

describe('header context fill', () => {
  beforeEach(() => {
    jest.useFakeTimers();
    getState.mockReset();
    getState.mockResolvedValue({ active: true, fill_pct: 25 });
    useContextFillStore.setState({ sessionName: null, fill: null });
  });
  afterEach(() => jest.useRealTimers());

  it('normalises percent and fraction fills', () => {
    expect(normaliseFill(42)).toBeCloseTo(0.42);
    expect(normaliseFill('0.5')).toBe(0.5);
    expect(normaliseFill(undefined)).toBeNull();
    expect(normaliseFill('n/a')).toBeNull();
  });

  it('polls while no snapshot has arrived', async () => {
    const screen = render(<Probe session="s1" />);
    await flush();
    expect(getState).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('fill').props.children).toBe('0.25');
    await act(async () => { jest.advanceTimersByTime(CONTEXT_POLL_MS); });
    expect(getState).toHaveBeenCalledTimes(2);
  });

  it('prefers the SSE snapshot and stops polling once one arrives', async () => {
    const screen = render(<Probe session="s1" />);
    await flush();
    act(() => useContextFillStore.getState().report('s1', 62));
    expect(screen.getByTestId('fill').props.children).toBe('0.62');
    const calls = getState.mock.calls.length;
    await act(async () => { jest.advanceTimersByTime(CONTEXT_POLL_MS * 3); });
    expect(getState).toHaveBeenCalledTimes(calls);
  });

  it('ignores snapshots for another session and does not poll when disconnected', async () => {
    useContextFillStore.getState().report('other', 90);
    const screen = render(<Probe session="s1" />);
    await flush();
    expect(screen.getByTestId('fill').props.children).toBe('0.25');
    expect(getState).toHaveBeenCalledTimes(1);

    getState.mockClear();
    const offline = render(<Probe session="s2" connected={false} />);
    await flush();
    expect(offline.getByTestId('fill').props.children).toBe('none');
    expect(getState).not.toHaveBeenCalled();
  });
});
