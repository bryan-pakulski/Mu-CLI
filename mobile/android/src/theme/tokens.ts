// Mobile design tokens aligned with the MuCLI alpine-glass web UI.
// Colour belongs to the environment; controls stay quiet and neutral.

export const spacing = { xs: 4, sm: 8, md: 12, base: 16, lg: 24, xl: 32, '2xl': 48, '3xl': 64 } as const;

export const typography = {
  xs: { fontSize: 12, lineHeight: 16 },
  sm: { fontSize: 14, lineHeight: 20 },
  base: { fontSize: 16, lineHeight: 24 },
  lg: { fontSize: 20, lineHeight: 28 },
  xl: { fontSize: 24, lineHeight: 32 },
  '2xl': { fontSize: 32, lineHeight: 40 },
} as const;

export const radii = { sm: 7, lg: 16, pill: 999 } as const;

export type SyntaxColors = {
  keyword: string; string: string; comment: string; number: string; func: string;
  operator: string; punctuation: string; plain: string; added: string; removed: string; diffHeader: string;
};

export type ThemeColors = {
  canvas: string;
  bg: string;
  bgLift: string;
  bgHover: string;
  glass: string;
  glassStrong: string;
  text: string;
  textDim: string;
  textSoft: string;
  accent: string;
  accentStrong: string;
  accentSoft: string;
  accentText: string;
  border: string;
  borderStrong: string;
  hairline: string;
  success: string;
  warning: string;
  error: string;
  info: string;
  skyField: string;
  glacierField: string;
  sunriseField: string;
  peachField: string;
  alpineField: string;
  snowField: string;
  syntax: SyntaxColors;
};

export const lightColors: ThemeColors = {
  canvas: '#EDF3F8',
  bg: 'rgba(237,243,248,0.58)',
  bgLift: 'rgba(249,252,254,0.52)',
  bgHover: 'rgba(58,84,109,0.050)',
  glass: 'rgba(249,252,254,0.64)',
  glassStrong: 'rgba(252,254,255,0.91)',
  text: '#1C2732',
  textDim: '#7B8894',
  textSoft: '#53616E',
  accent: '#6286A8',
  accentStrong: '#527795',
  accentSoft: 'rgba(98,134,168,0.075)',
  accentText: '#F8FBFD',
  border: 'rgba(39,59,77,0.060)',
  borderStrong: 'rgba(39,59,77,0.110)',
  hairline: 'rgba(39,59,77,0.085)',
  success: '#66877A',
  warning: '#8F7D60',
  error: '#B95F6B',
  info: '#748B9F',
  skyField: '#4891D6',
  glacierField: '#A6D4F0',
  sunriseField: '#F097BC',
  peachField: '#F9BFAC',
  alpineField: '#5B977A',
  snowField: '#FFFFFF',
  syntax: {
    keyword: '#657F9A', string: '#66877A', comment: '#8C98A3', number: '#9B7D72',
    func: '#527795', operator: '#6D7985', punctuation: '#89949E', plain: '#1C2732',
    added: '#66877A', removed: '#B95F6B', diffHeader: '#748B9F',
  },
};

export const darkColors: ThemeColors = {
  canvas: '#0D1219',
  bg: 'rgba(13,18,25,0.58)',
  bgLift: 'rgba(18,24,32,0.52)',
  bgHover: 'rgba(201,216,231,0.055)',
  glass: 'rgba(19,25,34,0.62)',
  glassStrong: 'rgba(18,24,32,0.90)',
  text: '#EEF2F6',
  textDim: '#85909C',
  textSoft: '#BBC4CD',
  accent: '#8BA9C6',
  accentStrong: '#7295B5',
  accentSoft: 'rgba(139,169,198,0.085)',
  accentText: '#0D1219',
  border: 'rgba(222,231,240,0.065)',
  borderStrong: 'rgba(222,231,240,0.115)',
  hairline: 'rgba(222,231,240,0.085)',
  success: '#86A897',
  warning: '#AA987B',
  error: '#CD7C86',
  info: '#92A6B8',
  skyField: '#5B9DDC',
  glacierField: '#AAD7F2',
  sunriseField: '#EA8FB4',
  peachField: '#F7B9A9',
  alpineField: '#4E8970',
  snowField: '#F1F7FC',
  syntax: {
    keyword: '#9DB4CB', string: '#94B3A2', comment: '#65717D', number: '#B9A08F',
    func: '#8BA9C6', operator: '#A3ADB7', punctuation: '#7C8792', plain: '#EEF2F6',
    added: '#86A897', removed: '#CD7C86', diffHeader: '#92A6B8',
  },
};

export type Theme = {
  colors: ThemeColors;
  spacing: typeof spacing;
  typography: typeof typography;
  radii: typeof radii;
};

export const lightTheme: Theme = { colors: lightColors, spacing, typography, radii };
export const darkTheme: Theme = { colors: darkColors, spacing, typography, radii };
