import React from 'react';
import { Pressable, ViewStyle } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';
import { radii, touchTarget } from '../theme/tokens';

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
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: !!disabled }}
      onPress={onPress}
      disabled={disabled}
      style={({ pressed }) => [
        {
          minWidth: touchTarget,
          minHeight: touchTarget,
          alignItems: 'center',
          justifyContent: 'center',
          borderRadius: radii.control,
          opacity: disabled ? 0.4 : 1,
          backgroundColor: pressed ? colors.hover : 'transparent',
        },
        style,
      ]}
    >
      <Ionicons name={icon} size={size} color={color ?? colors.text} />
    </Pressable>
  );
}