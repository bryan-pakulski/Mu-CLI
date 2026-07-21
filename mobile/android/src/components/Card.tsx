import React from 'react';
import { View, StyleSheet, ViewStyle } from 'react-native';
import { useTheme } from '../theme/ThemeContext';

export type CardProps = {
  children: React.ReactNode;
  style?: ViewStyle;
  elevated?: boolean;
};

export function Card({ children, style, elevated }: CardProps) {
  const { colors, spacing, radii } = useTheme();
  return (
    <View
      style={[
        {
          backgroundColor: colors.bgLift,
          borderRadius: radii.lg,
          padding: spacing.base,
          borderWidth: StyleSheet.hairlineWidth,
          borderColor: colors.border,
        },
        elevated && {
          shadowColor: '#000',
          shadowOpacity: 0.08,
          shadowRadius: 8,
          shadowOffset: { width: 0, height: 2 },
          elevation: 3,
        },
        style,
      ]}
    >
      {children}
    </View>
  );
}