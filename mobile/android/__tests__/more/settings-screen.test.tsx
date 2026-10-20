// Settings (mobile_parity.md §6): theme cards for Wash and every palette, light or dark, glass
// strength (Wash only), Reduce decoration, this phone's pairing, and the Areas links.
import React from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { act, fireEvent, render } from '@testing-library/react-native';

const mockNavigate = jest.fn();
jest.mock('@react-navigation/native', () => ({
  useNavigation: () => ({ navigate: mockNavigate }),
  useIsFocused: () => true,
}));
jest.mock('../../src/api/devices', () => ({ devicesApi: { whoami: jest.fn(async () => ({ id: 'd1', name: 'Pixel 8', created_at: 1, last_seen: 1, address: '' })) } }));

import { SettingsScreen } from '../../src/screens/SettingsScreen';
import { ThemeProvider, THEME_STORAGE_KEY, DECORATION_STORAGE_KEY, PALETTE_STORAGE_KEY, GLASS_STORAGE_KEY, useTheme } from '../../src/theme/ThemeContext';
import { PALETTES, washColors } from '../../src/theme/tokens';
import { useConnectionStore } from '../../src/store/connection';

const setItem = AsyncStorage.setItem as jest.Mock;
let theme: ReturnType<typeof useTheme>;
function Probe() { theme = useTheme(); return null; }
const renderScreen = () => render(<ThemeProvider statusBar={false}><SettingsScreen /><Probe /></ThemeProvider>);

beforeEach(() => {
  setItem.mockClear();
  mockNavigate.mockClear();
  useConnectionStore.setState({ isConnected: true, pairingRequired: false, baseUrl: 'http://192.168.1.20:30311' });
});

it('offers Wash and every palette, and choosing one recolours the app and makes it flat', async () => {
  const ui = renderScreen();
  await act(async () => undefined);
  expect(ui.getByTestId('palette-wash').props.accessibilityState).toMatchObject({ selected: true });
  for (const name of PALETTES) expect(ui.getByTestId(`palette-${name}`)).toBeTruthy();
  const pick = PALETTES[0];
  await act(async () => { fireEvent.press(ui.getByTestId(`palette-${pick}`)); });
  expect(setItem).toHaveBeenCalledWith(PALETTE_STORAGE_KEY, pick);
  expect(theme.palette).toBe(pick);
  expect(theme.flat).toBe(true);
  expect(theme.colors.canvas).toBe(washColors(theme.name, pick).canvas);
  // Glass applies to Wash only.
  expect(ui.queryByTestId('glass-setting')).toBeNull();
  await act(async () => { fireEvent.press(ui.getByTestId('palette-wash')); });
  expect(theme.palette).toBeNull();
  expect(ui.getByTestId('glass-setting')).toBeTruthy();
});

it('persists light or dark, glass strength and Reduce decoration', async () => {
  const ui = renderScreen();
  await act(async () => { fireEvent.press(ui.getByTestId('theme-dusk')); });
  expect(setItem).toHaveBeenCalledWith(THEME_STORAGE_KEY, 'dusk');
  expect(theme.name).toBe('dusk');
  await act(async () => { fireEvent.press(ui.getByLabelText('Off')); });
  expect(setItem).toHaveBeenCalledWith(GLASS_STORAGE_KEY, '0');
  expect(theme.flat).toBe(true);
  await act(async () => { fireEvent.press(ui.getByLabelText('80%')); });
  expect(theme.glass).toBe(80);
  expect(theme.flat).toBe(false);
  await act(async () => { fireEvent(ui.getByTestId('reduce-decoration'), 'valueChange', true); });
  expect(setItem).toHaveBeenCalledWith(DECORATION_STORAGE_KEY, '1');
  expect(theme.flat).toBe(true);
});

it('shows this phone\'s pairing and opens the pairing screen', async () => {
  const ui = renderScreen();
  expect(await ui.findByText('This phone: Pixel 8')).toBeTruthy();
  fireEvent.press(ui.getByTestId('settings-pair'));
  expect(mockNavigate).toHaveBeenCalledWith('Pair', undefined);
});

it('links each area to its own settings', async () => {
  const ui = renderScreen();
  await act(async () => undefined);
  fireEvent.press(ui.getByTestId('area-work'));
  expect(mockNavigate).toHaveBeenLastCalledWith('JobSettings', undefined);
  fireEvent.press(ui.getByTestId('area-pricing'));
  expect(mockNavigate).toHaveBeenLastCalledWith('Tabs', { screen: 'Insights', params: { tab: 'costs' } });
  fireEvent.press(ui.getByTestId('area-orchestration'));
  expect(mockNavigate).toHaveBeenLastCalledWith('Tabs', { screen: 'Orchestration', params: { tab: 'settings' } });
});
