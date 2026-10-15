// Wash text (G§2). Variants are the Wash type tokens; the pre-Wash size
// names (`xs`, `sm`, `base`, `md`, `lg`) are kept as aliases onto the same
// scale so older call sites read correctly. Display and title text is plum;
// everything else is ink, ink-2 or ink-3. The OS font scale may grow text up
// to 200% (never down below caption).

import React from 'react';
import { Text as RNText, type TextProps as RNTextProps, type TextStyle } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import { typeStyle, type TypeName, type WashColors } from '../theme/tokens';

export type WashTextVariant =
  | 'display' | 'title1' | 'title2' | 'title3' | 'bodyLg' | 'body' | 'label' | 'caption' | 'eyebrow' | 'mono' | 'monoSm';
export type LegacyTextVariant = 'xs' | 'sm' | 'base' | 'md' | 'lg' | 'xl' | '2xl';
export type TextVariant = WashTextVariant | LegacyTextVariant;

export type TextTone = 'ink' | 'ink2' | 'ink3' | 'plum' | 'onInk'
  | 'accent' | 'running' | 'ok' | 'attention' | 'caution' | 'danger' | 'plan' | 'info' | 'neutral';

const TOKEN: Record<TextVariant, TypeName> = {
  display: 'display', title1: 'title-1', title2: 'title-2', title3: 'title-3', bodyLg: 'body-lg', body: 'body',
  label: 'label', caption: 'caption', eyebrow: 'eyebrow', mono: 'mono', monoSm: 'mono-sm',
  xs: 'caption', sm: 'body', base: 'body', md: 'body-lg', lg: 'title-3', xl: 'title-2', '2xl': 'title-1',
};

const STYLES = Object.fromEntries(
  (Object.keys(TOKEN) as TextVariant[]).map(v => [v, typeStyle(TOKEN[v])]),
) as Record<TextVariant, TextStyle>;

const TITLES = new Set<TextVariant>(['display', 'title1', 'title2', 'title3']);

export function toneColor(colors: WashColors, tone: TextTone): string {
  switch (tone) {
    case 'ink': return colors.ink;
    case 'ink2': return colors.ink2;
    case 'ink3': return colors.ink3;
    case 'plum': return colors.plum;
    case 'onInk': return colors.onInk;
    default: return colors[tone].ink;
  }
}

export type TextProps = RNTextProps & {
  variant?: TextVariant;
  tone?: TextTone;
  /** Tertiary text (ink-3). */
  dim?: boolean;
  /** Accent ink (links). */
  accent?: boolean;
  /** Tabular numbers. */
  numeric?: boolean;
};

export function textStyle(variant: TextVariant): TextStyle {
  return STYLES[variant] ?? STYLES.body;
}

export const Text = React.forwardRef<RNText, TextProps>(function Text({ variant = 'body', tone, dim, accent, numeric, style, maxFontSizeMultiplier = 2, ...rest }, ref) {
  const { colors } = useTheme();
  const resolved: TextTone = tone ?? (accent ? 'accent' : dim ? 'ink3' : TITLES.has(variant) ? 'plum' : 'ink');
  return (
    <RNText
      ref={ref}
      maxFontSizeMultiplier={maxFontSizeMultiplier}
      style={[textStyle(variant), { color: toneColor(colors, resolved) }, numeric ? { fontVariant: ['tabular-nums'] } : null, style]}
      {...rest}
    />
  );
});
