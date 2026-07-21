import React from 'react';
import { TouchableOpacity, StyleSheet, ViewStyle } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';

export type IconButtonProps = {
  icon: keyof typeof Ionicons.glyphMap;
  onPress: () => void;
  size?: number;
  color?: string;
  disabled?: boolean;
  style?: ViewStyle;
};

export function IconButton({ icon, onPress, size = 22, color, disabled, style }: IconButtonProps) {
  const { colors } = useTheme();
  return (
    <TouchableOpacity
      onPress={onPress}
      disabled={disabled}
      style={[
        {
          minWidth: 44,
          minHeight: 44,
          alignItems: 'center',
          justifyContent: 'center',
          borderRadius: 999,
          opacity: disabled ? 0.4 : 1,
        },
        style,
      ]}
      activeOpacity={0.6}
    >
      <Ionicons name={icon} size={size} color={color ?? colors.text} />
    </TouchableOpacity>
  );
}