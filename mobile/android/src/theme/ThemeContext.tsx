// Wash theme provider (spec §5.1, G§10.11; Mu revision 2, mobile_parity.md §4).
//
// Preferences, persisted per device under the web's names (`theme`, `palette`, `glass`,
// `decoration`):
// - theme: `system | paper | dusk` (light / dark); legacy `light`/`dark` migrate on read.
// - palette: null (Wash) or one of tokens.json palettes (Darcula, One, Material, Tokyo Night).
//   Palettes are flat: no glass, no watercolour (as on the web).
// - glass: 0–100 (default 60), the Wash theme's glass strength.
// - decoration: "Reduce decoration" swaps the watercolour for a flat canvas (G§6.2.5).
// The provider also tracks the current destination (the tab), whose accent tints the chrome, and
// renders the status bar so its icons match the active theme.

import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { useColorScheme } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { PALETTES, themeFor, type DestinationName, type PaletteName, type Theme, type Tone3 } from './tokens';

export type ThemePreference = 'system' | 'paper' | 'dusk';
export type ThemeName = 'paper' | 'dusk';

export const THEME_STORAGE_KEY = '@mucli/theme';
export const DECORATION_STORAGE_KEY = '@mucli/reduce-decoration';
export const PALETTE_STORAGE_KEY = '@mucli/palette';
export const GLASS_STORAGE_KEY = '@mucli/glass';
export const GLASS_DEFAULT = 60;

export function parsePalette(raw: string | null | undefined): PaletteName | null {
  return raw && (PALETTES as readonly string[]).includes(raw) ? (raw as PaletteName) : null;
}

export function parseGlass(raw: string | null | undefined): number {
  const n = raw == null || raw === '' ? GLASS_DEFAULT : Number(raw);
  return Number.isFinite(n) ? Math.max(0, Math.min(100, Math.round(n))) : GLASS_DEFAULT;
}

/** Normalise a stored value; `light`/`dark` are the pre-Wash names. */
export function parseThemePreference(raw: string | null | undefined): ThemePreference {
  switch (raw) {
    case 'paper': case 'light': return 'paper';
    case 'dusk': case 'dark': return 'dusk';
    default: return 'system';
  }
}

export function resolveThemeName(preference: ThemePreference, scheme: string | null | undefined): ThemeName {
  if (preference === 'paper' || preference === 'dusk') return preference;
  return scheme === 'dark' ? 'dusk' : 'paper';
}

export type ThemeContextValue = Theme & {
  /** The resolved theme. */
  name: ThemeName;
  isDark: boolean;
  preference: ThemePreference;
  setPreference: (preference: ThemePreference) => void;
  /** Flip between Paper and Dusk (pins the preference). */
  toggleTheme: () => void;
  reduceDecoration: boolean;
  setReduceDecoration: (on: boolean) => void;
  /** null = the Wash theme. */
  palette: PaletteName | null;
  setPalette: (palette: PaletteName | null) => void;
  /** Glass strength 0–100 (Wash only; palettes and reduced decoration are flat). */
  glass: number;
  setGlass: (glass: number) => void;
  /** Surfaces are solid: a palette, reduced decoration or glass at 0. */
  flat: boolean;
  /** The destination on screen (the tab) and its accent. */
  destination: DestinationName;
  setDestination: (destination: DestinationName) => void;
  accent: Tone3;
};

const noop = () => undefined;

const DEFAULT_THEME = themeFor('paper');

const ThemeContext = createContext<ThemeContextValue>({
  ...DEFAULT_THEME,
  name: 'paper',
  isDark: false,
  preference: 'system',
  setPreference: noop,
  toggleTheme: noop,
  reduceDecoration: false,
  setReduceDecoration: noop,
  palette: null,
  setPalette: noop,
  glass: GLASS_DEFAULT,
  setGlass: noop,
  flat: false,
  destination: 'chat',
  setDestination: noop,
  accent: DEFAULT_THEME.colors.destination.chat,
});

export function ThemeProvider({ children, statusBar = true }: { children: React.ReactNode; statusBar?: boolean }) {
  const scheme = useColorScheme();
  const [preference, setPreferenceState] = useState<ThemePreference>('system');
  const [reduceDecoration, setReduceDecorationState] = useState(false);
  const [palette, setPaletteState] = useState<PaletteName | null>(null);
  const [glass, setGlassState] = useState(GLASS_DEFAULT);
  const [destination, setDestinationState] = useState<DestinationName>('chat');

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [themeRaw, decorationRaw, paletteRaw, glassRaw] = await Promise.all([
          AsyncStorage.getItem(THEME_STORAGE_KEY),
          AsyncStorage.getItem(DECORATION_STORAGE_KEY),
          AsyncStorage.getItem(PALETTE_STORAGE_KEY),
          AsyncStorage.getItem(GLASS_STORAGE_KEY),
        ]);
        if (cancelled) return;
        // Only touch state when storage differs from the defaults, so a
        // first launch does not re-render the whole tree.
        const stored = parseThemePreference(themeRaw);
        if (stored !== 'system') setPreferenceState(stored);
        if (decorationRaw === '1') setReduceDecorationState(true);
        const storedPalette = parsePalette(paletteRaw);
        if (storedPalette) setPaletteState(storedPalette);
        const storedGlass = parseGlass(glassRaw);
        if (storedGlass !== GLASS_DEFAULT) setGlassState(storedGlass);
      } catch {
        // Storage unavailable: keep following the OS.
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const setPreference = useCallback((next: ThemePreference) => {
    setPreferenceState(next);
    AsyncStorage.setItem(THEME_STORAGE_KEY, next).catch(noop);
  }, []);

  const setReduceDecoration = useCallback((on: boolean) => {
    setReduceDecorationState(on);
    AsyncStorage.setItem(DECORATION_STORAGE_KEY, on ? '1' : '0').catch(noop);
  }, []);

  const setPalette = useCallback((next: PaletteName | null) => {
    setPaletteState(next);
    AsyncStorage.setItem(PALETTE_STORAGE_KEY, next ?? '').catch(noop);
  }, []);

  const setGlass = useCallback((next: number) => {
    const value = parseGlass(String(next));
    setGlassState(value);
    AsyncStorage.setItem(GLASS_STORAGE_KEY, String(value)).catch(noop);
  }, []);

  const setDestination = useCallback((next: DestinationName) => {
    setDestinationState(prev => (prev === next ? prev : next));
  }, []);

  const name = resolveThemeName(preference, scheme);
  const toggleTheme = useCallback(() => setPreference(name === 'dusk' ? 'paper' : 'dusk'), [name, setPreference]);
  const theme = useMemo(() => themeFor(name, palette), [name, palette]);

  const value = useMemo<ThemeContextValue>(() => ({
    ...theme,
    name,
    isDark: name === 'dusk',
    preference,
    setPreference,
    toggleTheme,
    reduceDecoration,
    setReduceDecoration,
    palette,
    setPalette,
    glass,
    setGlass,
    flat: reduceDecoration || palette != null || glass === 0,
    destination,
    setDestination,
    accent: theme.colors.destination[destination],
  }), [theme, name, preference, reduceDecoration, palette, glass, destination,
       setPreference, setReduceDecoration, setPalette, setGlass, setDestination, toggleTheme]);

  return (
    <ThemeContext.Provider value={value}>
      {statusBar ? <StatusBar style={name === 'dusk' ? 'light' : 'dark'} /> : null}
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  return useContext(ThemeContext);
}
