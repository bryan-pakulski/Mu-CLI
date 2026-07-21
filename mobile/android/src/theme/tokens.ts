// Design tokens — single source of truth for spacing, type, radii, colors.
// One accent color (indigo #6366F1), neutral gray ramp, semantic state colors.

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
  sm: 6,
  lg: 12,
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
  bg: '#FFFFFF',
  bgLift: '#F8FAFC',
  bgHover: '#F1F5F9',
  text: '#0F172A',
  textDim: '#64748B',
  textSoft: '#334155',
  accent: '#6366F1',
  accentSoft: '#E0E7FF',
  accentText: '#FFFFFF',
  border: '#E2E8F0',
  borderStrong: '#CBD5E1',
  success: '#16A34A',
  warning: '#D97706',
  error: '#DC2626',
  info: '#2563EB',
};

export const darkColors: ThemeColors = {
  bg: '#0F172A',
  bgLift: '#1E293B',
  bgHover: '#334155',
  text: '#F1F5F9',
  textDim: '#64748B',
  textSoft: '#CBD5E1',
  accent: '#6366F1',
  accentSoft: '#312E81',
  accentText: '#FFFFFF',
  border: '#334155',
  borderStrong: '#475569',
  success: '#22C55E',
  warning: '#FBBF24',
  error: '#EF4444',
  info: '#3B82F6',
};

export type Theme = {
  colors: ThemeColors;
  spacing: typeof spacing;
  typography: typeof typography;
  radii: typeof radii;
};

export const lightTheme: Theme = { colors: lightColors, spacing, typography, radii };
export const darkTheme: Theme = { colors: darkColors, spacing, typography, radii };