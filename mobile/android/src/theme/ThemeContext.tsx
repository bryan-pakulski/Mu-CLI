import React, { createContext, useContext } from 'react';
import { useColorScheme } from 'react-native';
import { lightTheme, darkTheme, type Theme } from './tokens';

type ThemeContextValue = Theme & {
  isDark: boolean;
};

const ThemeContext = createContext<ThemeContextValue>(lightTheme as ThemeContextValue);

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const scheme = useColorScheme();
  const isDark = scheme === 'dark';
  const theme = isDark ? darkTheme : lightTheme;
  const value: ThemeContextValue = { ...theme, isDark };
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  return useContext(ThemeContext);
}