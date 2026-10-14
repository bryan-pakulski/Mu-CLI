import React from 'react';
import { View, ViewStyle } from 'react-native';
import { useTheme } from '../theme/ThemeContext';

export type CardProps = {
  children: React.ReactNode;
  style?: ViewStyle;
  /** Retained for API compatibility. planos panels are always surface +
   * 1px border + radius 10; an "elevated" card uses the raised surface. */
  elevated?: boolean;
};

export function Card({ children, style, elevated }: CardProps) {
  const { colors, spacing, radii } = useTheme();
  return (
    <View
      style={[
        {
          backgroundColor: colors.surface,
          borderWidth: 1,
          borderColor: colors.border,
          borderRadius: radii.panel,
          padding: spacing.base,
        },
        elevated && { backgroundColor: colors.raised },
        style,
      ]}
    >
      {children}
    </View>
  );
}
