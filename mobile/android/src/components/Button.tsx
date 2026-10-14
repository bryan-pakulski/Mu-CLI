import React from 'react';
import {
  Pressable,
  Text as RNText,
  TextStyle,
  ViewStyle,
  ActivityIndicator,
} from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import { fontWeights, touchTarget } from '../theme/tokens';

export type ButtonProps = {
  title: string;
  onPress: () => void;
  /** planos buttons: primary = accent fill (one per region), secondary =
   * raised + 1px border, ghost = text only, danger = destructive (solid danger). */
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  /** planos tone variant: tinted text/border on secondary, solid fill on primary. */
  tone?: 'ok' | 'warn' | 'danger';
  disabled?: boolean;
  loading?: boolean;
  style?: ViewStyle;
  textStyle?: TextStyle;
  /** Round-32b F11: expose a11y state (e.g. {disabled: true}) to screen
   * readers while a conflict action is in progress. */
  accessibilityState?: { disabled?: boolean; busy?: boolean; checked?: boolean };
};

export function Button({
  title,
  onPress,
  variant = 'primary',
  disabled,
  loading,
  style,
  textStyle,
  accessibilityState,
  tone,
}: ButtonProps) {
  const { colors, spacing, radii, typography } = useTheme();

  const baseStyle: ViewStyle = {
    minHeight: touchTarget,
    borderRadius: radii.sm,
    paddingHorizontal: spacing.base,
    paddingVertical: spacing.sm,
    alignItems: 'center',
    justifyContent: 'center',
    flexDirection: 'row',
    borderWidth: 1,
    opacity: disabled ? 0.5 : 1,
  };

  const toneColor = tone ? colors[tone] : null;
  const variantStyles: Record<string, ViewStyle> = {
    primary: { backgroundColor: toneColor || colors.accent, borderColor: toneColor || colors.accent },
    secondary: { backgroundColor: colors.raised, borderColor: toneColor || colors.border },
    ghost: { backgroundColor: 'transparent', borderColor: 'transparent' },
    danger: { backgroundColor: colors.danger, borderColor: colors.danger },
  };
  const pressedStyles: Record<string, ViewStyle> = {
    primary: toneColor ? { opacity: 0.86 } : { backgroundColor: colors.accentHover, borderColor: colors.accentHover },
    secondary: { backgroundColor: colors.hover },
    ghost: { backgroundColor: colors.hover },
    danger: { opacity: 0.86 },
  };

  const textColor = variant === 'primary' || variant === 'danger'
    ? colors.accentOn
    : toneColor
      ? toneColor
      : variant === 'ghost'
        ? colors.muted
        : colors.text;
  const fontSpec = typography.sm;

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ ...accessibilityState, disabled: !!(disabled || loading), busy: !!loading }}
      onPress={onPress}
      disabled={disabled || loading}
      style={({ pressed }) => [baseStyle, variantStyles[variant], pressed && pressedStyles[variant], style]}
    >
      {loading && <ActivityIndicator size="small" color={textColor} style={{ marginRight: spacing.sm }} />}
      <RNText style={[{ color: textColor, fontSize: fontSpec.fontSize, lineHeight: fontSpec.lineHeight, fontWeight: fontWeights.medium, flexShrink: 1, textAlign: 'center' }, textStyle]}>
        {title}
      </RNText>
    </Pressable>
  );
}
