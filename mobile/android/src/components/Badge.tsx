import React from 'react';
import { View } from 'react-native';
import { Text } from './Text';
import { useTheme } from '../theme/ThemeContext';
import { fontWeights } from '../theme/tokens';

export type BadgeProps = {
  label: string;
  variant?: 'neutral' | 'accent' | 'success' | 'warning' | 'error';
};

export function Badge({ label, variant = 'neutral' }: BadgeProps) {
  const { colors, spacing, radii, typography } = useTheme();
  // planos pill: the tone colour as text plus a tint of the same colour
  // ("currentColor" tint) as fill and border.
  const tone = {
    neutral: colors.neutral,
    accent: colors.accent,
    success: colors.ok,
    warning: colors.attention,
    error: colors.danger,
  }[variant];
  const c = { bg: tone.wash, border: tone.wash, text: variant === 'neutral' ? colors.ink2 : tone.ink };
  const fontSpec = typography.xs;
  return (
    <View style={{ backgroundColor: c.bg, borderWidth: 1, borderColor: c.border, borderRadius: radii.pill, paddingHorizontal: spacing.sm, paddingVertical: 1, alignSelf: 'flex-start' }}>
      <Text style={{ color: c.text, fontSize: fontSpec.fontSize, lineHeight: fontSpec.lineHeight, fontWeight: fontWeights.medium }}>
        {label}
      </Text>
    </View>
  );
}
