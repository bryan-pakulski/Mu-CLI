import React from 'react';
import { View, StyleSheet } from 'react-native';
import { Text } from './Text';
import { useTheme } from '../theme/ThemeContext';

export type BadgeProps = {
  label: string;
  variant?: 'neutral' | 'accent' | 'success' | 'warning' | 'error';
};

export function Badge({ label, variant = 'neutral' }: BadgeProps) {
  const { colors, spacing, radii, typography } = useTheme();
  const colorMap = {
    neutral: { bg: colors.bgHover, text: colors.textSoft },
    accent: { bg: colors.accentSoft, text: colors.accent },
    success: { bg: colors.success + '22', text: colors.success },
    warning: { bg: colors.warning + '22', text: colors.warning },
    error: { bg: colors.error + '22', text: colors.error },
  };
  const c = colorMap[variant];
  const fontSpec = typography.xs;
  return (
    <View style={{ backgroundColor: c.bg, borderRadius: radii.pill, paddingHorizontal: spacing.sm, paddingVertical: 2 }}>
      <Text style={{ color: c.text, fontSize: fontSpec.fontSize, fontWeight: '600' }}>
        {label}
      </Text>
    </View>
  );
}