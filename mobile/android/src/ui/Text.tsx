// Wash text (G§2). Variants are the Wash type tokens; the pre-Wash size
// names (`xs`, `sm`, `base`, `md`, `lg`) are kept as aliases onto the same
// scale so older call sites read correctly. Display and title text is plum;
// everything else is ink, ink-2 or ink-3. The OS font scale may grow text up
// to 200% (never down below caption).

import React from 'react';
import { Platform, StyleSheet, Text as RNText, type StyleProp, type TextProps as RNTextProps, type TextStyle } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import { typeStyle, withStaticWeight, type TypeName, type WashColors } from '../theme/tokens';
import { textScale as textScaleTokens } from '../theme/wash.generated';

// Android selects font weight by family name (tokens.fontFamilies); a
// caller's `fontWeight` override is resolved to the matching static family.

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

/** Smallest text the Text size setting can produce (tokens.json density.text_scale.min_px). */
export const MIN_SCALED_TEXT = textScaleTokens.min_px;

/**
 * ``style`` at the user's text size (percent; ThemeContext textScale): font size and line height
 * multiplied, never below MIN_SCALED_TEXT (unless the design is already smaller). The same style
 * object back at 100%.
 */
export function scaleText<T extends TextStyle>(style: T, percent: number): T {
  if (!percent || percent === 100) return style;
  const k = percent / 100;
  const out: T = { ...style };
  if (typeof style.fontSize === 'number') {
    out.fontSize = Math.max(Math.min(MIN_SCALED_TEXT, style.fontSize), Math.round(style.fontSize * k * 2) / 2);
    if (typeof style.lineHeight === 'number') {
      out.lineHeight = Math.max(Math.round(style.lineHeight * k), Math.ceil((out.fontSize as number) * 1.2));
    }
  } else if (typeof style.lineHeight === 'number') {
    out.lineHeight = Math.round(style.lineHeight * k);
  }
  return out;
}

export const Text = React.forwardRef<RNText, TextProps>(function Text({ variant = 'body', tone, dim, accent, numeric, style, maxFontSizeMultiplier = 2, ...rest }, ref) {
  const { colors, textScale } = useTheme();
  const resolved: TextTone = tone ?? (accent ? 'accent' : dim ? 'ink3' : TITLES.has(variant) ? 'plum' : 'ink');
  const merged: StyleProp<TextStyle> = [textStyle(variant), { color: toneColor(colors, resolved) }, numeric ? { fontVariant: ['tabular-nums'] } : null, style];
  let final: StyleProp<TextStyle> = merged;
  if (textScale !== 100) final = scaleText(StyleSheet.flatten(merged), textScale);
  if (Platform.OS === 'android' && (style || textScale !== 100)) final = withStaticWeight(StyleSheet.flatten(final));
  return (
    <RNText
      ref={ref}
      maxFontSizeMultiplier={maxFontSizeMultiplier}
      style={final}
      {...rest}
    />
  );
});
