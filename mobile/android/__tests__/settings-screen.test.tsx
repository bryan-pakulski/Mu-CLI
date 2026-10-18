// Settings is a real screen under More (G§8.3): theme preference persists,
// Reduce decoration toggles, and the auto-approve warning banner shows.
import React from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { act, fireEvent, render } from '@testing-library/react-native';

const mockNavigate = jest.fn();
jest.mock('@react-navigation/native', () => ({
  useNavigation: () => ({ navigate: mockNavigate }),
  useIsFocused: () => false,
}));
jest.mock('../src/api/inspector', () => ({ inspectorApi: { getVariables: jest.fn(), setVariable: jest.fn() } }));
jest.mock('../src/components/AdvancedSettingsSheet', () => ({ AdvancedSettingsSheet: () => null }));
jest.mock('../src/components/ContainerManagerSheet', () => ({ ContainerManagerSheet: () => null }));
jest.mock('../src/components/EffortControl', () => ({ EffortControl: () => null }));

import { SettingsScreen } from '../src/screens/SettingsScreen';
import { ThemeProvider, THEME_STORAGE_KEY, DECORATION_STORAGE_KEY } from '../src/theme/ThemeContext';
import { useConnectionStore } from '../src/store/connection';

const setItem = AsyncStorage.setItem as jest.Mock;

it('persists the theme preference and Reduce decoration', async () => {
  const ui = render(<ThemeProvider statusBar={false}><SettingsScreen /></ThemeProvider>);
  await act(async () => { fireEvent.press(ui.getByTestId('theme-dusk')); });
  expect(setItem).toHaveBeenCalledWith(THEME_STORAGE_KEY, 'dusk');
  expect(ui.getByTestId('theme-dusk').props.accessibilityState).toMatchObject({ selected: true });
  await act(async () => { fireEvent(ui.getByTestId('reduce-decoration'), 'valueChange', true); });
  expect(setItem).toHaveBeenCalledWith(DECORATION_STORAGE_KEY, '1');
});

it('keeps auto-approve visible with a non-dismissable banner', () => {
  useConnectionStore.setState({ yolo: true, isConnected: true, activeSessionName: 's1' });
  const ui = render(<ThemeProvider statusBar={false}><SettingsScreen /></ThemeProvider>);
  expect(ui.getByText('Auto-approve is on — Mucli will run tools without asking')).toBeTruthy();
  expect(ui.getByText('Turn off')).toBeTruthy();
  useConnectionStore.setState({ yolo: false });
});
