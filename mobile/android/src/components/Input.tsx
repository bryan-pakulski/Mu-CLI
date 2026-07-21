import React from 'react';
import { TextInput, StyleSheet, TextStyle, ViewStyle } from 'react-native';
import { useTheme } from '../theme/ThemeContext';

export type InputProps = {
  value: string;
  onChangeText: (text: string) => void;
  placeholder?: string;
  multiline?: boolean;
  secureTextEntry?: boolean;
  style?: TextStyle;
  numberOfLines?: number;
  autoCapitalize?: 'none' | 'sentences' | 'words' | 'characters';
  autoCorrect?: boolean;
  keyboardType?: 'default' | 'url' | 'email-address' | 'numeric' | 'phone-pad';
};

export function Input({
  value,
  onChangeText,
  placeholder,
  multiline,
  secureTextEntry,
  style,
  numberOfLines,
  autoCapitalize,
  autoCorrect,
  keyboardType,
}: InputProps) {
  const { colors, spacing, radii, typography } = useTheme();
  const fontSpec = typography.base;
  return (
    <TextInput
      value={value}
      onChangeText={onChangeText}
      placeholder={placeholder}
      placeholderTextColor={colors.textDim}
      multiline={multiline}
      numberOfLines={numberOfLines}
      secureTextEntry={secureTextEntry}
      autoCapitalize={autoCapitalize}
      autoCorrect={autoCorrect}
      keyboardType={keyboardType}
      style={[
        {
          color: colors.text,
          fontSize: fontSpec.fontSize,
          lineHeight: fontSpec.lineHeight,
          backgroundColor: colors.bg,
          borderWidth: StyleSheet.hairlineWidth,
          borderColor: colors.border,
          borderRadius: radii.sm,
          paddingHorizontal: spacing.base,
          paddingVertical: spacing.sm,
          minHeight: 44,
        },
        style,
      ]}
    />
  );
}