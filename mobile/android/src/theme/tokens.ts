// Mobile design tokens — the planos design system (planos `src/core/theme.ts`,
// mirrored by mu/gui/static/css/work.css for the web jobs mode).
//
// Components consume only these values: no raw colours, font sizes, radii,
// shadows or durations outside this file. One Tokyo Night-style palette in
// dark and a GitHub-like palette in light: accent for brand/focus/links,
// ok/warn/danger/running for state, muted for secondary text, and
// bg < surface (panels) < raised (controls, dialogs) < hover for elevation.
// Legacy names (bgSubtle, glass, hairline, success, ...) are re-pointed at the
// planos tokens so every existing screen picks the palette up unchanged.
// Contrast ratios (WCAG 2.2) for text tokens are listed in the report table
// and in `__tests__/planos-theme.test.ts`.

import { Easing, Platform, type ViewStyle } from 'react-native';

/* ------------------------------------------------------------ constants */

/** Spacing — the 8 px scale with a 4 px half step (4/8/12/16/24/32/48/64/96). */
export const spacing = { xs: 4, sm: 8, md: 12, base: 16, lg: 24, xl: 32, '2xl': 48, '3xl': 64, '4xl': 96 } as const;

/** Font sizes — planos type scale 12/14/16/20 (legacy keys re-pointed onto it). */
export const fontSizes = { xs: 12, sm: 14, base: 14, md: 16, lg: 20, xl: 20, '2xl': 20 } as const;

/** Weights 400/500/600; 700 only for stat values and the logo. */
export const fontWeights = { regular: '400', medium: '500', semibold: '600', bold: '700' } as const;

/** Line-height ratios: body 1.5, snug 1.4, headings 1.25. */
export const lineHeights = { tight: 1.25, snug: 1.4, body: 1.5 } as const;

/** planos headings use normal tracking. */
export const tracking = { tight: 0 } as const;

export const typography = {
  xs: { fontSize: 12, lineHeight: 18 },
  sm: { fontSize: 14, lineHeight: 20 },
  base: { fontSize: 14, lineHeight: 21 },
  md: { fontSize: 16, lineHeight: 24 },
  lg: { fontSize: 20, lineHeight: 26 },
  xl: { fontSize: 20, lineHeight: 26 },
  '2xl': { fontSize: 20, lineHeight: 26 },
} as const;

/**
 * Inter-like system UI font (Roboto on Android) for everything; mono only for
 * code, logs, IDs, paths, hashes and numeric data (JetBrains Mono when the
 * device has it, otherwise the platform monospace).
 */
export const fonts = {
  ui: Platform.select({ android: 'sans-serif', default: undefined }) as string | undefined,
  mono: 'monospace',
} as const;

/** Radius: 6 for controls/rows/chips, 10 for panels/dialogs/sheets, pill for dots/badges. */
export const radii = { sm: 6, control: 6, md: 10, lg: 10, panel: 10, pill: 999 } as const;

/** Motion durations (ms). */
export const motion = { micro: 120, small: 200, large: 300 } as const;

/** Easing curves matching --ease-out / --ease-in / --ease-standard. */
export const easings = {
  out: Easing.bezier(0.16, 1, 0.3, 1),
  in: Easing.bezier(0.4, 0, 1, 1),
  standard: Easing.bezier(0.2, 0, 0, 1),
} as const;

/** Minimum touch target (dp). */
export const touchTarget = 44;

/** Hex colour + alpha → rgba(). The planos "currentColor tint" for pills, banners and fills. */
export function tint(hex: string, alpha: number): string {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha})`;
}

export type SyntaxColors = {
  keyword: string; string: string; comment: string; number: string; func: string;
  operator: string; punctuation: string; plain: string; added: string; removed: string; diffHeader: string;
};

/** planos semantic tokens (theme.ts TOKENS + WEB_EXTRA). */
export type PlanosTone = 'accent' | 'ok' | 'warn' | 'danger' | 'running' | 'muted';

export type ThemeColors = {
  // planos tokens
  surface: string;
  border: string;
  raised: string;
  hover: string;
  ok: string;
  warn: string;
  danger: string;
  running: string;
  muted: string;
  // Canonical surfaces
  canvas: string;
  bg: string;
  bgSubtle: string;
  bgRaised: string;
  bgHover: string;
  bgActive: string;
  // Canonical text (text = primary, textSoft = secondary, textDim = tertiary)
  text: string;
  textSoft: string;
  textDim: string;
  textPrimary: string;
  textSecondary: string;
  textTertiary: string;
  // Accent
  accent: string;
  accentHover: string;
  accentSubtle: string;
  accentFg: string;
  accentOn: string;
  // Lines
  hairline: string;
  controlBorder: string;
  // Semantic (solid for text/icons, subtle for fills)
  success: string;
  successSubtle: string;
  warning: string;
  warningSubtle: string;
  error: string;
  errorSubtle: string;
  info: string;
  infoSubtle: string;
  /** Plan mode (read-only) indicator — violet so it never reads as warn/YOLO. */
  plan: string;
  planSubtle: string;
  scrim: string;
  codeBg: string;
  shadow: string;
  /** Data-visualisation series only; never UI chrome. */
  viz: readonly [string, string, string, string, string, string, string, string];
  // Legacy aliases — kept so existing code resolves to the new system.
  bgLift: string;
  glass: string;
  glassStrong: string;
  accentStrong: string;
  accentSoft: string;
  accentText: string;
  accentLine: string;
  accentMuted: string;
  highlight: string;
  borderStrong: string;
  // Retired atmosphere — names kept, values are flat surfaces.
  skyField: string;
  glacierField: string;
  sunriseField: string;
  peachField: string;
  alpineField: string;
  snowField: string;
  card: string;
  errorBg: string;
  syntax: SyntaxColors;
};

/** Text colour that sits on a solid tone fill (primary/solid buttons). */
export function onTone(colors: ThemeColors, tone: PlanosTone): string {
  return tone === 'muted' ? colors.text : colors.accentOn;
}

/** Resolve a planos tone to its colour. */
export function toneColor(colors: ThemeColors, tone: PlanosTone | '' | undefined | null): string {
  switch (tone) {
    case 'ok': return colors.ok;
    case 'warn': return colors.warn;
    case 'danger': return colors.danger;
    case 'running': return colors.running;
    case 'muted': return colors.muted;
    case 'accent': return colors.accent;
    default: return colors.text;
  }
}

function build(P: {
  accent: string; accentHover: string; ok: string; warn: string; danger: string; running: string; muted: string;
  text: string; surface: string; border: string; bg: string; raised: string; hover: string;
  borderStrong: string; purple: string; teal: string; onAccent: string; scrim: string; shadow: string; tintAlpha: number;
}): Omit<ThemeColors, 'syntax'> {
  const a = P.tintAlpha;
  return {
    surface: P.surface, border: P.border, raised: P.raised, hover: P.hover,
    ok: P.ok, warn: P.warn, danger: P.danger, running: P.running, muted: P.muted,
    canvas: P.bg, bg: P.bg, bgSubtle: P.surface, bgRaised: P.raised, bgHover: P.hover, bgActive: P.hover,
    text: P.text, textSoft: P.muted, textDim: P.muted, textPrimary: P.text, textSecondary: P.muted, textTertiary: P.muted,
    accent: P.accent, accentHover: P.accentHover, accentSubtle: tint(P.accent, a), accentFg: P.accent, accentOn: P.onAccent,
    hairline: P.border, controlBorder: P.border,
    success: P.ok, successSubtle: tint(P.ok, a), warning: P.warn, warningSubtle: tint(P.warn, a),
    error: P.danger, errorSubtle: tint(P.danger, a), info: P.running, infoSubtle: tint(P.running, a),
    plan: P.purple, planSubtle: tint(P.purple, a),
    scrim: P.scrim, codeBg: P.bg, shadow: P.shadow,
    viz: [P.accent, P.ok, P.warn, P.danger, P.running, P.purple, P.teal, P.muted],
    bgLift: P.surface, glass: P.surface, glassStrong: P.raised,
    accentStrong: P.accentHover, accentSoft: tint(P.accent, a), accentText: P.onAccent, accentLine: P.accent,
    accentMuted: P.muted, highlight: P.warn, borderStrong: P.borderStrong,
    skyField: P.bg, glacierField: P.bg, sunriseField: P.bg, peachField: P.bg, alpineField: P.bg, snowField: P.bg,
    card: P.surface, errorBg: tint(P.danger, a),
  };
}

/* ------------------------------------------------------------ light */
// accent #2f5bd3 · ok #1a7f37 · warn #9a6700 · danger #cf222e · running #0969da
// muted #57606a · text #1f2328 · surface #ffffff · border #d0d7de · bg #f6f8fa
// raised #ffffff · hover #eaeef2
export const lightColors: ThemeColors = {
  ...build({
    accent: '#2f5bd3', accentHover: '#2449b0', ok: '#1a7f37', warn: '#9a6700', danger: '#cf222e', running: '#0969da', muted: '#57606a',
    text: '#1f2328', surface: '#ffffff', border: '#d0d7de', bg: '#f6f8fa', raised: '#ffffff', hover: '#eaeef2',
    borderStrong: '#afb8c1', purple: '#8250df', teal: '#1b7c83', onAccent: '#ffffff', scrim: 'rgba(31,35,40,0.4)', shadow: '#1f2328', tintAlpha: 0.1,
  }),
  // Every syntax token is >= 4.5:1 on codeBg (#f6f8fa).
  syntax: {
    keyword: '#2f5bd3', string: '#1a7f37', comment: '#57606a', number: '#9a6700',
    func: '#0969da', operator: '#57606a', punctuation: '#57606a', plain: '#1f2328',
    added: '#1a7f37', removed: '#cf222e', diffHeader: '#0969da',
  },
};

/* ------------------------------------------------------------ dark (default on web) */
// accent #7aa2f7 · ok #73c991 · warn #e0af68 · danger #f7768e · running #7dcfff
// muted #8b93b8 · text #c0caf5 · surface #1a1b26 · border #2f3549 · bg #16161e
// raised #1f2335 · hover #292e42
export const darkColors: ThemeColors = {
  ...build({
    accent: '#7aa2f7', accentHover: '#8fb1f9', ok: '#73c991', warn: '#e0af68', danger: '#f7768e', running: '#7dcfff', muted: '#8b93b8',
    text: '#c0caf5', surface: '#1a1b26', border: '#2f3549', bg: '#16161e', raised: '#1f2335', hover: '#292e42',
    borderStrong: '#3b4261', purple: '#bb9af7', teal: '#2ac3de', onAccent: '#16161e', scrim: 'rgba(0,0,0,0.55)', shadow: '#000000', tintAlpha: 0.15,
  }),
  // Every syntax token is >= 4.5:1 on codeBg (#16161e).
  syntax: {
    keyword: '#7aa2f7', string: '#73c991', comment: '#8b93b8', number: '#e0af68',
    func: '#7dcfff', operator: '#8b93b8', punctuation: '#8b93b8', plain: '#c0caf5',
    added: '#73c991', removed: '#f7768e', diffHeader: '#7dcfff',
  },
};

/* ------------------------------------------------------------ elevation */

/**
 * Elevation for floating layers only (sheets, modals, menus, toasts): planos
 * dialogs are `raised` + 1px border. Level 1 = menus/popovers/toasts,
 * level 2 = modals/sheets/drawers. Light adds a soft shadow; dark relies on
 * the lighter raised surface + border.
 */
export function elevation(level: 1 | 2, colors: ThemeColors, isDark: boolean): ViewStyle {
  const ring: ViewStyle = { borderWidth: 1, borderColor: colors.border };
  if (isDark) return { ...ring, backgroundColor: colors.bgRaised };
  return level === 1
    ? { ...ring, backgroundColor: colors.bgRaised, shadowColor: colors.shadow, shadowOpacity: 0.06, shadowRadius: 2, shadowOffset: { width: 0, height: 1 }, elevation: 2 }
    : { ...ring, backgroundColor: colors.bgRaised, shadowColor: colors.shadow, shadowOpacity: 0.12, shadowRadius: 16, shadowOffset: { width: 0, height: 12 }, elevation: 8 };
}

export type Theme = {
  colors: ThemeColors;
  spacing: typeof spacing;
  typography: typeof typography;
  fontSizes: typeof fontSizes;
  fontWeights: typeof fontWeights;
  radii: typeof radii;
  motion: typeof motion;
};

export const lightTheme: Theme = { colors: lightColors, spacing, typography, fontSizes, fontWeights, radii, motion };
export const darkTheme: Theme = { colors: darkColors, spacing, typography, fontSizes, fontWeights, radii, motion };
