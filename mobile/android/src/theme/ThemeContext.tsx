// Wash theme provider (spec §5.1, G§10.11).
//
// The preference is `system | paper | dusk` and is persisted per device in
// AsyncStorage, so the choice survives a restart. Legacy `light`/`dark`
// values are migrated on read. A separate "Reduce decoration" preference
// replaces watercolour art with a flat wash (G§6.2.5). The provider also
// renders the status bar so its icons match the active theme.

import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { useColorScheme } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { duskTheme, paperTheme, type Theme } from './tokens';

export type ThemePreference = 'system' | 'paper' | 'dusk';
export type ThemeName = 'paper' | 'dusk';

export const THEME_STORAGE_KEY = '@mucli/theme';
export const DECORATION_STORAGE_KEY = '@mucli/reduce-decoration';

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
};

const noop = () => undefined;

const ThemeContext = createContext<ThemeContextValue>({
  ...paperTheme,
  name: 'paper',
  isDark: false,
  preference: 'system',
  setPreference: noop,
  toggleTheme: noop,
  reduceDecoration: false,
  setReduceDecoration: noop,
});

export function ThemeProvider({ children, statusBar = true }: { children: React.ReactNode; statusBar?: boolean }) {
  const scheme = useColorScheme();
  const [preference, setPreferenceState] = useState<ThemePreference>('system');
  const [reduceDecoration, setReduceDecorationState] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [themeRaw, decorationRaw] = await Promise.all([
          AsyncStorage.getItem(THEME_STORAGE_KEY),
          AsyncStorage.getItem(DECORATION_STORAGE_KEY),
        ]);
        if (cancelled) return;
        // Only touch state when storage differs from the defaults, so a
        // first launch does not re-render the whole tree.
        const stored = parseThemePreference(themeRaw);
        if (stored !== 'system') setPreferenceState(stored);
        if (decorationRaw === '1') setReduceDecorationState(true);
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

  const name = resolveThemeName(preference, scheme);
  const toggleTheme = useCallback(() => setPreference(name === 'dusk' ? 'paper' : 'dusk'), [name, setPreference]);

  const value = useMemo<ThemeContextValue>(() => ({
    ...(name === 'dusk' ? duskTheme : paperTheme),
    name,
    isDark: name === 'dusk',
    preference,
    setPreference,
    toggleTheme,
    reduceDecoration,
    setReduceDecoration,
  }), [name, preference, reduceDecoration, setPreference, setReduceDecoration, toggleTheme]);

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
