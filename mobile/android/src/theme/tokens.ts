// Design tokens — a restrained neutral palette with one indigo accent.

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
  bg: '#F7F7F8',
  bgLift: '#FFFFFF',
  bgHover: '#EFEFF1',
  text: '#171717',
  textDim: '#6F6F74',
  textSoft: '#3F3F46',
  accent: '#5B5BD6',
  accentSoft: '#ECECFF',
  accentText: '#FFFFFF',
  border: '#E5E5E8',
  borderStrong: '#D2D2D7',
  success: '#15803D',
  warning: '#B45309',
  error: '#DC2626',
  info: '#2563EB',
};

export const darkColors: ThemeColors = {
  bg: '#0F1012',
  bgLift: '#18191C',
  bgHover: '#23252A',
  text: '#F5F5F6',
  textDim: '#A1A1AA',
  textSoft: '#D4D4D8',
  accent: '#8B8BF5',
  accentSoft: '#29294B',
  accentText: '#0F1012',
  border: '#2B2D31',
  borderStrong: '#41434A',
  success: '#4ADE80',
  warning: '#FBBF24',
  error: '#F87171',
  info: '#60A5FA',
};

export type Theme = {
  colors: ThemeColors;
  spacing: typeof spacing;
  typography: typeof typography;
  radii: typeof radii;
};

export const lightTheme: Theme = { colors: lightColors, spacing, typography, radii };
export const darkTheme: Theme = { colors: darkColors, spacing, typography, radii };