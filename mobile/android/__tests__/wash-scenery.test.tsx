// Scenery packs (tokens.json scenery): Classic keeps each destination's scene and accent; a pack
// paints every destination with its watercolour and recolours the accent; flat palettes ignore it;
// the choice is stored and offered in Settings (Wash only).
import React from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { AccessibilityInfo } from 'react-native';
import { act, fireEvent, render } from '@testing-library/react-native';

const mockNavigate = jest.fn();
jest.mock('@react-navigation/native', () => ({
  useNavigation: () => ({ navigate: mockNavigate }),
  useIsFocused: () => true,
}));
// Jest stubs every image require as the same number; give each page painting its own identity.
jest.mock('../src/theme/washArt.generated', () => {
  const actual = jest.requireActual('../src/theme/washArt.generated');
  const art: Record<string, Record<string, Record<string, { hero: unknown; ambient: unknown }>>> = {};
  for (const [pack, pages] of Object.entries(actual.pageArt as Record<string, Record<string, Record<string, unknown>>>)) {
    art[pack] = {};
    for (const [page, themes] of Object.entries(pages)) {
      art[pack][page] = {};
      for (const name of Object.keys(themes)) art[pack][page][name] = { hero: { uri: `${pack}/${page}-${name}` }, ambient: { uri: `${pack}/ambient-${page}-${name}` } };
    }
  }
  return { pageArt: art, realPageArt: actual.pageArt };
});
jest.mock('../src/api/devices', () => ({ devicesApi: { whoami: jest.fn(async () => null) } }));

import { SettingsScreen } from '../src/screens/SettingsScreen';
import { PALETTE_STORAGE_KEY, SCENERY_STORAGE_KEY, ThemeProvider, parseScenery, useTheme } from '../src/theme/ThemeContext';
import { DESTINATIONS, PALETTES, SCENERY, destinationAmbient, sceneryAccent, washColors } from '../src/theme/tokens';
import { scenery as sceneryTokens } from '../src/theme/wash.generated';
import { pageArt } from '../src/theme/washArt.generated';
const realPageArt = (jest.requireMock('../src/theme/washArt.generated') as { realPageArt: typeof pageArt }).realPageArt;
import { AmbientBackground, ambientArt } from '../src/ui/Glass';
import { WashBackground, washArt } from '../src/ui/WashBackground';
import { useConnectionStore } from '../src/store/connection';

const setItem = AsyncStorage.setItem as jest.Mock;
const getItem = AsyncStorage.getItem as jest.Mock;
let theme: ReturnType<typeof useTheme>;
function Probe() { theme = useTheme(); return null; }

beforeEach(() => {
  setItem.mockClear();
  getItem.mockReset();
  getItem.mockImplementation(async () => null);
  useConnectionStore.setState({ isConnected: false, pairingRequired: false, baseUrl: 'http://127.0.0.1:30311' });
});

it('lists the packs from the design tokens, and reads anything else as Classic', () => {
  expect(SCENERY).toEqual(Object.keys(sceneryTokens).filter(k => !k.startsWith('_') && k !== 'classic'));
  expect(SCENERY).toEqual(expect.arrayContaining(['desert', 'forest', 'ocean', 'space']));
  expect(parseScenery('ocean')).toBe('ocean');
  expect(parseScenery('classic')).toBeNull();
  expect(parseScenery('volcano')).toBeNull();
});

it('a pack recolours every destination to its pigment; a flat palette keeps its own', () => {
  for (const pack of SCENERY) {
    const colors = washColors('paper', null, pack);
    for (const d of DESTINATIONS) expect(colors.destination[d]).toEqual(colors.pigment[sceneryAccent(pack)]);
  }
  const classic = washColors('paper');
  expect(classic.destination.chat).not.toEqual(classic.destination.work);
  const flat = washColors('paper', PALETTES[0], 'desert');
  expect(flat.destination.work).toEqual(washColors('paper', PALETTES[0]).destination.work);
});

it('a stored pack paints each destination with its own painting, and becomes the accent', async () => {
  jest.spyOn(AccessibilityInfo, 'isReduceMotionEnabled').mockResolvedValue(true);   // switch without the fade
  getItem.mockImplementation(async (key: string) => (key === SCENERY_STORAGE_KEY ? 'forest' : null));
  const ui = render(
    <ThemeProvider statusBar={false}>
      <AmbientBackground><WashBackground art="lagoon" testID="hero" /></AmbientBackground>
      <Probe />
    </ThemeProvider>,
  );
  await act(async () => undefined);
  expect(theme.scenery).toBe('forest');
  expect(theme.accent).toEqual(theme.colors.pigment.meadow);
  const ambient = () => ui.getByTestId('ambient-wash', { includeHiddenElements: true }).props.source;
  const heroShows = (src: unknown) => ui.UNSAFE_root.findAll((n: { props?: { href?: unknown } }) => n.props?.href === src).length > 0;
  expect(ambient()).toBe(pageArt.forest.chat[theme.name].ambient);
  expect(heroShows(pageArt.forest.chat[theme.name].hero)).toBe(true);
  // Every destination has its own painting: switching tabs changes the backdrop and the hero.
  const seen = new Set([ambient()]);
  for (const d of DESTINATIONS.filter(name => name !== 'chat')) {
    await act(async () => { theme.setDestination(d); });
    expect(ambient()).toBe(ambientArt('forest', theme.name, d));
    expect(ambient()).toBe(pageArt.forest[d][theme.name].ambient);
    expect(heroShows(washArt('forest', theme.name, d))).toBe(true);
    seen.add(ambient());
  }
  expect(seen.size).toBe(DESTINATIONS.length);
});

it('every pack has a painting of every destination in both themes', () => {
  for (const pack of SCENERY) {
    for (const d of DESTINATIONS) {
      for (const name of ['paper', 'dusk'] as const) {
        expect(realPageArt[pack]?.[d]?.[name]?.hero).toBeTruthy();
        expect(realPageArt[pack]?.[d]?.[name]?.ambient).toBeTruthy();
      }
    }
  }
});

it('Classic follows the destination; a flat palette switches the pack off but keeps the choice', async () => {
  getItem.mockImplementation(async (key: string) => (key === SCENERY_STORAGE_KEY ? 'space' : key === PALETTE_STORAGE_KEY ? PALETTES[0] : null));
  render(<ThemeProvider statusBar={false}><Probe /></ThemeProvider>);
  await act(async () => undefined);
  expect(theme.sceneryChoice).toBe('space');
  expect(theme.scenery).toBeNull();
  expect(theme.accent).toEqual(theme.colors.destination.chat);
  expect(destinationAmbient('chat')).toBe('dawn');
});

it('Settings offers Classic and every pack (Wash only) and stores the choice', async () => {
  const ui = render(<ThemeProvider statusBar={false}><SettingsScreen /><Probe /></ThemeProvider>);
  await act(async () => undefined);
  expect(ui.getByTestId('scenery-classic').props.accessibilityState).toMatchObject({ selected: true });
  for (const pack of SCENERY) expect(ui.getByTestId(`scenery-${pack}`)).toBeTruthy();
  await act(async () => { fireEvent.press(ui.getByTestId('scenery-desert')); });
  expect(setItem).toHaveBeenCalledWith(SCENERY_STORAGE_KEY, 'desert');
  expect(theme.scenery).toBe('desert');
  expect(ui.getByTestId('scenery-desert').props.accessibilityState).toMatchObject({ selected: true });
  await act(async () => { fireEvent.press(ui.getByTestId('scenery-classic')); });
  expect(setItem).toHaveBeenCalledWith(SCENERY_STORAGE_KEY, 'classic');
  expect(theme.scenery).toBeNull();
  await act(async () => { fireEvent.press(ui.getByTestId(`palette-${PALETTES[0]}`)); });
  expect(ui.queryByTestId('scenery-setting')).toBeNull();
});
