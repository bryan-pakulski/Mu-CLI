import React from 'react';
import {
  TouchableOpacity,
  Text as RNText,
  StyleSheet,
  TextStyle,
  ViewStyle,
  ActivityIndicator,
} from 'react-native';
import { useTheme } from '../theme/ThemeContext';

export type ButtonProps = {
  title: string;
  onPress: () => void;
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  disabled?: boolean;
  loading?: boolean;
  style?: ViewStyle;
  textStyle?: TextStyle;
};

export function Button({
  title,
  onPress,
  variant = 'primary',
  disabled,
  loading,
  style,
  textStyle,
}: ButtonProps) {
  const { colors, spacing, radii, typography } = useTheme();

  const baseStyle: ViewStyle = {
    minHeight: 46,
    borderRadius: radii.sm,
    paddingHorizontal: spacing.base,
    paddingVertical: spacing.sm,
    alignItems: 'center',
    justifyContent: 'center',
    flexDirection: 'row',
    opacity: disabled ? 0.45 : 1,
  };

  const variantStyles: Record<string, ViewStyle> = {
    primary: { backgroundColor: colors.accent },
    secondary: { backgroundColor: colors.bgHover },
    ghost: { backgroundColor: 'transparent' },
    danger: { backgroundColor: colors.error },
  };

  const textColor = variant === 'primary' || variant === 'danger' ? colors.accentText : colors.text;
  const fontSpec = typography.sm;

  return (
    <TouchableOpacity
      accessibilityRole="button"
      onPress={onPress}
      disabled={disabled || loading}
      style={[baseStyle, variantStyles[variant], style]}
      activeOpacity={0.72}
    >
      {loading && <ActivityIndicator size="small" color={textColor} style={{ marginRight: 8 }} />}
      <RNText style={[{ color: textColor, fontSize: fontSpec.fontSize, fontWeight: '600' }, textStyle]}>
        {title}
      </RNText>
    </TouchableOpacity>
  );
}