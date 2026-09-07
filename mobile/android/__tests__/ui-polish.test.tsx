import React from 'react';
import { AccessibilityInfo, AppState, Text } from 'react-native';
import { act, fireEvent, render, renderHook, waitFor } from '@testing-library/react-native';
import { BottomSheet } from '../src/components/BottomSheet';
import { ThemeProvider } from '../src/theme/ThemeContext';
import { useMotionPreference } from '../src/hooks/useMotionPreference';

afterEach(() => jest.restoreAllMocks());

it('does not mount a hidden sheet and exposes a close action without a title', () => {
  const mounted = jest.fn();
  const close = jest.fn();
  function Content() {
    React.useEffect(() => { mounted(); }, []);
    return <Text>Settings content</Text>;
  }
  const sheet = (visible: boolean) => <ThemeProvider><BottomSheet visible={visible} onClose={close}><Content /></BottomSheet></ThemeProvider>;
  const view = render(sheet(false));
  expect(mounted).not.toHaveBeenCalled();
  view.rerender(sheet(true));
  expect(mounted).toHaveBeenCalledTimes(1);
  fireEvent.press(view.getByRole('button', { name: 'Close' }));
  expect(close).toHaveBeenCalledTimes(1);
});

it('pauses decorative motion in the background and honors preference changes', async () => {
  const previousState = AppState.currentState;
  AppState.currentState = 'active';
  let changeMotion: (value: boolean) => void = () => {};
  let changeState: (value: any) => void = () => {};
  const removeMotion = jest.fn();
  const removeState = jest.fn();
  jest.spyOn(AccessibilityInfo, 'isReduceMotionEnabled').mockResolvedValue(false);
  jest.spyOn(AccessibilityInfo, 'addEventListener').mockImplementation((_, listener) => {
    // Jest infers RN's last overload (announcementFinished); this hook
    // subscribes only to the boolean reduceMotionChanged event.
    changeMotion = listener as unknown as (value: boolean) => void;
    return { remove: removeMotion } as unknown as ReturnType<typeof AccessibilityInfo.addEventListener>;
  });
  jest.spyOn(AppState, 'addEventListener').mockImplementation((_, listener) => {
    changeState = listener;
    return { remove: removeState };
  });
  try {
    const hook = renderHook(() => useMotionPreference());
    await waitFor(() => expect(hook.result.current.animate).toBe(true));
    act(() => changeState('background'));
    expect(hook.result.current.animate).toBe(false);
    act(() => { changeMotion(true); changeState('active'); });
    expect(hook.result.current.reducedMotion).toBe(true);
    expect(hook.result.current.animate).toBe(false);
    hook.unmount();
    expect(removeMotion).toHaveBeenCalledTimes(1);
    expect(removeState).toHaveBeenCalledTimes(1);
  } finally { AppState.currentState = previousState; }
});
