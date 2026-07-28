// Design tokens — aligned with the warm, low-contrast MuCLI web GUI palette.

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  base: 16,
  lg: 24,
  xl: 32,
  '2xl': 48,
  '3xl': 64,
} as const;

export const typography = {
  xs: { fontSize: 12, lineHeight: 16 },
  sm: { fontSize: 14, lineHeight: 20 },
  base: { fontSize: 16, lineHeight: 24 },
  lg: { fontSize: 20, lineHeight: 28 },
  xl: { fontSize: 24, lineHeight: 32 },
  '2xl': { fontSize: 32, lineHeight: 40 },
} as const;

export const radii = {
  sm: 10,
  lg: 18,
  pill: 999,
} as const;

export type ThemeColors = {
  bg: string;
  bgLift: string;
  bgHover: string;
  text: string;
  textDim: string;
  textSoft: string;
  accent: string;
  accentSoft: string;
  accentText: string;
  border: string;
  borderStrong: string;
  success: string;
  warning: string;
  error: string;
  info: string;
};

export const lightColors: ThemeColors = {
  bg: '#FAF8F5',
  bgLift: '#F3F0EB',
  bgHover: '#EBE7E0',
  text: '#2B2B2B',
  textDim: '#999999',
  textSoft: '#6B6B6B',
  accent: '#555555',
  accentSoft: '#E8E3DB',
  accentText: '#FAF8F5',
  border: '#E7E2DA',
  borderStrong: '#D9D2C8',
  success: '#587A5C',
  warning: '#9A6A28',
  error: '#C8455D',
  info: '#667A8E',
};

export const darkColors: ThemeColors = {
  bg: '#1A1814',
  bgLift: '#221F1A',
  bgHover: '#2A2722',
  text: '#D4D0C8',
  textDim: '#6B6860',
  textSoft: '#9C998F',
  accent: '#B0ACA0',
  accentSoft: '#302D27',
  accentText: '#1A1814',
  border: '#2C2924',
  borderStrong: '#3A3630',
  success: '#8CAA83',
  warning: '#C6A15B',
  error: '#D98787',
  info: '#8EA4B8',
};

export type Theme = {
  colors: ThemeColors;
  spacing: typeof spacing;
  typography: typeof typography;
  radii: typeof radii;
};

export const lightTheme: Theme = { colors: lightColors, spacing, typography, radii };
export const darkTheme: Theme = { colors: darkColors, spacing, typography, radii };