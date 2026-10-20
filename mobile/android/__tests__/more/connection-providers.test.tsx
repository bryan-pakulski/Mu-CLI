// More → Connection and Provider and model: the saved address only changes once the new one
// answers; a model switch goes to the server, or joins the offline queue.
import React from 'react';
import { act, fireEvent, render, waitFor } from '@testing-library/react-native';

const mockGoBack = jest.fn();
const mockNavigate = jest.fn();
jest.mock('@react-navigation/native', () => {
  const React = require('react');
  return {
    useNavigation: () => ({ navigate: mockNavigate, goBack: mockGoBack, canGoBack: () => true }),
    useFocusEffect: (fn: () => void) => React.useEffect(fn, [fn]),
  };
});
jest.mock('../../src/api/client', () => ({ ...jest.requireActual('../../src/api/client'), checkHealth: jest.fn() }));
jest.mock('../../src/api/providers', () => ({
  providersApi: {
    list: jest.fn(async () => ({ providers: [{ name: 'anthropic', configured: true }, { name: 'openai', configured: false, requires: 'OPENAI_API_KEY' }] })),
    getCurrent: jest.fn(async () => ({ provider: 'anthropic', model: 'claude-sonnet-5-5' })),
    listModels: jest.fn(async () => ({ models: ['claude-sonnet-5-5', 'claude-opus-5-5'] })),
    switch: jest.fn(async () => ({})),
  },
}));
jest.mock('../../src/store/connection', () => {
  const actual = jest.requireActual('../../src/store/connection');
  return { ...actual, queueIfOffline: jest.fn(async () => false) };
});

import { checkHealth } from '../../src/api/client';
import { providersApi } from '../../src/api/providers';
import { ConnectionScreen } from '../../src/screens/ConnectionScreen';
import { ProvidersScreen } from '../../src/screens/ProvidersScreen';
import { queueIfOffline, useConnectionStore } from '../../src/store/connection';
import { ThemeProvider } from '../../src/theme/ThemeContext';
import { ToastHost } from '../../src/ui';

const wrap = (node: React.ReactElement) => render(<ThemeProvider statusBar={false}><ToastHost>{node}</ToastHost></ThemeProvider>);

beforeEach(() => {
  jest.clearAllMocks();
  useConnectionStore.setState({ baseUrl: 'http://192.168.1.20:30311', isConnected: false, activeSessionName: null });
});

describe('Connection', () => {
  it('keeps the saved address when the new one never answers', async () => {
    jest.useFakeTimers();
    (checkHealth as jest.Mock).mockResolvedValue(false);
    const ui = wrap(<ConnectionScreen />);
    await act(async () => undefined);
    fireEvent.changeText(ui.getByTestId('connection-url'), 'http://192.168.1.99:30311');
    await act(async () => { fireEvent.press(ui.getByTestId('connection-test')); });
    await act(async () => { jest.advanceTimersByTime(10_000); });
    await act(async () => { jest.advanceTimersByTime(10_000); });
    jest.useRealTimers();
    await waitFor(() => expect(ui.getByTestId('connection-error')).toBeTruthy());
    expect(useConnectionStore.getState().baseUrl).toBe('http://192.168.1.20:30311');
    expect(checkHealth).toHaveBeenCalledTimes(3);
  });

  it('switches to an address that answers and goes back', async () => {
    (checkHealth as jest.Mock).mockResolvedValue(true);
    const ui = wrap(<ConnectionScreen />);
    await act(async () => undefined);
    fireEvent.changeText(ui.getByTestId('connection-url'), 'http://192.168.1.99:30311/');
    await act(async () => { fireEvent.press(ui.getByTestId('connection-test')); });
    expect(useConnectionStore.getState().baseUrl).toBe('http://192.168.1.99:30311');
    expect(useConnectionStore.getState().isConnected).toBe(true);
    expect(mockGoBack).toHaveBeenCalled();
  });

  it('rejects an invalid address without calling the server', async () => {
    const ui = wrap(<ConnectionScreen />);
    await act(async () => undefined);
    fireEvent.changeText(ui.getByTestId('connection-url'), 'ftp://nope');
    await act(async () => { fireEvent.press(ui.getByTestId('connection-test')); });
    expect(ui.getByTestId('connection-error')).toBeTruthy();
    expect(checkHealth).not.toHaveBeenCalled();
  });
});

describe('Provider and model', () => {
  it('lists providers with what an unconfigured one needs, and switches the model', async () => {
    const ui = wrap(<ProvidersScreen />);
    expect(await ui.findByTestId('model-claude-opus-5-5')).toBeTruthy();
    expect(ui.getByText('Needs OPENAI_API_KEY')).toBeTruthy();
    await act(async () => { fireEvent.press(ui.getByText('claude-opus-5-5')); });
    expect(providersApi.switch).toHaveBeenCalledWith('anthropic', 'claude-opus-5-5');
    expect(useConnectionStore.getState().activeModel).toBe('claude-opus-5-5');
  });

  it('queues the switch while offline instead of calling the server', async () => {
    (queueIfOffline as jest.Mock).mockResolvedValueOnce(true);
    const ui = wrap(<ProvidersScreen />);
    await act(async () => { fireEvent.press(await ui.findByText('claude-opus-5-5')); });
    expect(queueIfOffline).toHaveBeenCalledWith('provider_switch', { provider: 'anthropic', model: 'claude-opus-5-5' });
    expect(providersApi.switch).not.toHaveBeenCalled();
  });
});
