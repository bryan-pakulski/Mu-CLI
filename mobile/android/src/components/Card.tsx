import React from 'react';
import { StyleSheet, View, ViewStyle } from 'react-native';
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
          backgroundColor: colors.glass,
          borderColor: colors.hairline,
          borderWidth: StyleSheet.hairlineWidth,
          borderRadius: radii.lg,
          padding: spacing.base,
        },
        elevated && {
          shadowColor: '#000',
          shadowOpacity: 0.04,
          shadowRadius: 14,
          shadowOffset: { width: 0, height: 8 },
          elevation: 1,
        },
        style,
      ]}
    >
      {children}
    </View>
  );
}
