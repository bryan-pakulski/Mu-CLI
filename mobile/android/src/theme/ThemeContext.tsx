import React, { createContext, useContext, useState, useCallback } from 'react';
import { useColorScheme } from 'react-native';
import { lightTheme, darkTheme, type Theme } from './tokens';

type ThemeContextValue = Theme & {
  isDark: boolean;
  toggleTheme: () => void;
};

const ThemeContext = createContext<ThemeContextValue>(lightTheme as ThemeContextValue);

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const scheme = useColorScheme();
  const [manualOverride, setManualOverride] = useState<boolean | null>(null);
  const isDark = manualOverride !== null ? manualOverride : scheme === 'dark';
  const theme = isDark ? darkTheme : lightTheme;
  const toggleTheme = useCallback(() => setManualOverride(prev => !(prev !== null ? prev : scheme === 'dark')), [scheme]);
  const value: ThemeContextValue = { ...theme, isDark, toggleTheme };
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  return useContext(ThemeContext);
}