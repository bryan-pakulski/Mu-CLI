// Tapping a "needs you" notification opens the exact item, including the tap that launched the app.
import React from 'react';
import { act, render } from '@testing-library/react-native';

const mockNavigate = jest.fn();
jest.mock('../../src/navigation/nav', () => ({
  navRef: { current: { isReady: () => true } },
  navigateTo: (...args: unknown[]) => mockNavigate(...args),
}));

import { routeForNotification, useNotificationTaps } from '../../src/navigation/notifications';

it('maps job and orchestration notification data to their screens', () => {
  expect(routeForNotification({ jobId: 'j-1' })).toEqual({ name: 'JobDetail', params: { jobId: 'j-1' } });
  expect(routeForNotification({ orchRunId: 'r-1' })).toEqual({ name: 'OrchRun', params: { runId: 'r-1' } });
  expect(routeForNotification({ orchNodeId: 'n-1' })).toEqual({ name: 'OrchNode', params: { nodeId: 'n-1' } });
  expect(routeForNotification({ orchRef: 'disk-usage@3' })).toEqual({ name: 'OrchTool', params: { toolId: 'disk-usage', version: 3 } });
  expect(routeForNotification({ orchRef: 'not a ref' })).toBeNull();
  expect(routeForNotification({})).toBeNull();
  expect(routeForNotification(null)).toBeNull();
});

it('opens the launching notification once and later taps as they come', async () => {
  let listener: ((r: unknown) => void) | null = null;
  const remove = jest.fn();
  const response = (data: Record<string, unknown>) => ({ notification: { request: { content: { data } } } });
  const mod = {
    getLastNotificationResponseAsync: jest.fn(async () => response({ jobId: 'j-9' })),
    addNotificationResponseReceivedListener: jest.fn((fn: (r: unknown) => void) => { listener = fn; return { remove }; }),
  };
  function Host() { useNotificationTaps(mod as never); return null; }
  const ui = render(<Host />);
  await act(async () => undefined);
  expect(mockNavigate).toHaveBeenCalledWith('JobDetail', { jobId: 'j-9' });
  await act(async () => { listener!(response({ orchRunId: 'r-2' })); });
  expect(mockNavigate).toHaveBeenLastCalledWith('OrchRun', { runId: 'r-2' });
  ui.unmount();
  expect(remove).toHaveBeenCalled();
});
