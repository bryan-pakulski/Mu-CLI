import React from 'react';
import { Text as RNText, TextProps as RNTextProps, StyleSheet } from 'react-native';
import { useTheme } from '../theme/ThemeContext';

export type TextProps = RNTextProps & {
  variant?: keyof typeof import('../theme/tokens').typography;
  dim?: boolean;
  accent?: boolean;
};

export function Text({ variant = 'base', dim, accent, style, ...rest }: TextProps) {
  const { colors, typography } = useTheme();
  const fontSpec = typography[variant];
  const color = accent ? colors.accent : dim ? colors.textDim : colors.text;
  return (
    <RNText
      style={[{ color, fontSize: fontSpec.fontSize, lineHeight: fontSpec.lineHeight }, style]}
      {...rest}
    />
  );
}