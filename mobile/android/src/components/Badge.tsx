import React from 'react';
import { View } from 'react-native';
import { Text } from './Text';
import { useTheme } from '../theme/ThemeContext';
import { fontWeights, tint } from '../theme/tokens';

export type BadgeProps = {
  label: string;
  variant?: 'neutral' | 'accent' | 'success' | 'warning' | 'error';
};

export function Badge({ label, variant = 'neutral' }: BadgeProps) {
  const { colors, spacing, radii, typography } = useTheme();
  // planos pill: the tone colour as text plus a tint of the same colour
  // ("currentColor" tint) as fill and border.
  const tone = {
    neutral: colors.muted,
    accent: colors.accent,
    success: colors.ok,
    warning: colors.warn,
    error: colors.danger,
  }[variant];
  const c = { bg: tint(tone, 0.14), border: tint(tone, 0.4), text: variant === 'neutral' ? colors.text : tone };
  const fontSpec = typography.xs;
  return (
    <View style={{ backgroundColor: c.bg, borderWidth: 1, borderColor: c.border, borderRadius: radii.pill, paddingHorizontal: spacing.sm, paddingVertical: 1, alignSelf: 'flex-start' }}>
      <Text style={{ color: c.text, fontSize: fontSpec.fontSize, lineHeight: fontSpec.lineHeight, fontWeight: fontWeights.medium }}>
        {label}
      </Text>
    </View>
  );
}
