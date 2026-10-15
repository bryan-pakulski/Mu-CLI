// Wash button (G§9.1). Pill-shaped; one `primary` (ink fill) per view,
// `secondary` (surface + hairline-strong), `ghost`, `tonal` (semantic wash +
// semantic ink), `danger` (danger ink fill, destructive confirmations only)
// and `icon` (circular, always labelled). Mobile default size is `lg` (44);
// `md` (36) reaches 44 through hit slop.
//
// The shorthand props older call sites used (`primary`, `ghost`, `small`,
// `iconOnly`, `tone`, `title`) resolve onto these variants so screens migrate
// without changing behaviour.

import React from 'react';
import { View, type StyleProp, type TextStyle, type ViewStyle } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import { controlSize, radii, spacing, toSemantic, toneOf, type Tone } from '../theme/tokens';
import { Pressable, type Haptic } from './Pressable';
import { Text } from './Text';
import { Icon, type IconName } from './Icon';
import { InkDrop } from './Progress';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'tonal' | 'danger' | 'icon';

export type ButtonProps = {
  /** Visible label (the accessibility label for icon buttons). */
  label?: string;
  /** Alias of `label` (pre-Wash call sites). */
  title?: string;
  onPress: () => void;
  variant?: ButtonVariant;
  /** Semantic tone for `tonal` (and the legacy tone shorthand). */
  tone?: Tone;
  size?: 'md' | 'lg';
  /** Leading icon (16) — a Wash icon name or a node. */
  icon?: IconName | React.ReactNode;
  /** Trailing chevron: the action navigates. */
  navigates?: boolean;
  disabled?: boolean;
  /** Visually disabled but still pressable (to explain why). */
  ariaDisabled?: boolean;
  loading?: boolean;
  selected?: boolean;
  haptic?: Haptic;
  accessibilityLabel?: string;
  accessibilityHint?: string;
  accessibilityState?: { disabled?: boolean; busy?: boolean; checked?: boolean; selected?: boolean; expanded?: boolean };
  testID?: string;
  style?: StyleProp<ViewStyle>;
  textStyle?: StyleProp<TextStyle>;
  // Legacy shorthands
  primary?: boolean;
  ghost?: boolean;
  small?: boolean;
  iconOnly?: boolean;
};

export function resolveVariant(p: Pick<ButtonProps, 'variant' | 'primary' | 'ghost' | 'tone' | 'iconOnly'>): ButtonVariant {
  if (p.variant) return p.variant;
  if (p.iconOnly) return 'icon';
  const tone = p.tone ? toSemantic(p.tone) : null;
  if (p.primary) return tone === 'danger' ? 'danger' : 'primary';
  if (p.ghost) return 'ghost';
  if (tone && tone !== 'neutral') return 'tonal';
  return 'secondary';
}

export function Button(props: ButtonProps) {
  const { colors } = useTheme();
  const {
    onPress, tone, icon, navigates, disabled, ariaDisabled, loading, selected, accessibilityHint,
    accessibilityState, testID, style, textStyle,
  } = props;
  const label = props.label ?? props.title ?? '';
  const variant = resolveVariant(props);
  const size = props.size ?? (props.small ? 'md' : 'lg');
  const height = size === 'lg' ? controlSize.lg : controlSize.md;
  const t = toneOf(colors, tone || (variant === 'danger' ? 'danger' : 'accent'));

  const look: { bg: string; border: string; fg: string } = (() => {
    switch (variant) {
      case 'primary': return { bg: colors.ink, border: colors.ink, fg: colors.onInk };
      case 'danger': return { bg: colors.danger.ink, border: colors.danger.ink, fg: colors.onInk };
      case 'tonal': return { bg: t.wash, border: t.wash, fg: t.ink };
      case 'ghost': return { bg: 'transparent', border: 'transparent', fg: selected ? colors.accent.ink : colors.ink2 };
      case 'icon': return { bg: selected ? colors.accent.wash : 'transparent', border: 'transparent', fg: selected ? colors.accent.ink : colors.ink2 };
      default: return { bg: selected ? colors.accent.wash : colors.surface, border: selected ? colors.accent.pigment : colors.hairlineStrong, fg: selected ? colors.accent.ink : colors.ink };
    }
  })();

  const leading = typeof icon === 'string'
    ? <Icon name={icon as IconName} size={variant === 'icon' ? 'md' : 'sm'} color={look.fg} />
    : icon ?? null;
  const isIcon = variant === 'icon';
  const busy = !!loading;

  return (
    <Pressable
      testID={testID}
      accessibilityRole="button"
      accessibilityLabel={props.accessibilityLabel || label}
      accessibilityHint={accessibilityHint}
      accessibilityState={{ ...accessibilityState, busy: busy || undefined, selected: selected || accessibilityState?.selected || undefined, disabled: ariaDisabled || accessibilityState?.disabled || undefined }}
      disabled={disabled || busy}
      haptic={props.haptic ?? (variant === 'primary' || variant === 'danger' ? 'light' : false)}
      onPress={onPress}
      style={({ pressed }) => [
        {
          minHeight: height,
          minWidth: isIcon ? height : undefined,
          flexDirection: 'row',
          alignItems: 'center',
          justifyContent: 'center',
          gap: spacing.sm,
          paddingHorizontal: isIcon ? 0 : size === 'lg' ? spacing['20'] : spacing.base,
          borderRadius: radii.pill,
          borderWidth: 1,
          backgroundColor: pressed && (variant === 'ghost' || variant === 'icon' || variant === 'secondary') ? colors.sunken : look.bg,
          borderColor: look.border,
          opacity: ariaDisabled ? 0.4 : 1,
        },
        style,
      ]}
    >
      {busy ? (
        <View style={{ position: 'absolute' }}><InkDrop tone={variant === 'primary' || variant === 'danger' ? 'neutral' : tone || 'running'} size={8} /></View>
      ) : null}
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.sm, opacity: busy ? 0 : 1, flexShrink: 1 }}>
        {leading}
        {isIcon ? null : (
          <Text variant="label" numberOfLines={1} style={[{ color: look.fg, flexShrink: 1, textAlign: 'center' }, textStyle]}>{label}</Text>
        )}
        {navigates && !isIcon ? <Text variant="label" importantForAccessibility="no" style={{ color: look.fg }}>›</Text> : null}
      </View>
    </Pressable>
  );
}

/** Icon-only button (G§9.1 `icon`): 44 on touch, always labelled. */
export function IconButton({ icon, label, onPress, selected, disabled, testID, tone, style, accessibilityHint }: {
  icon: IconName; label: string; onPress: () => void; selected?: boolean; disabled?: boolean; testID?: string; tone?: Tone; style?: StyleProp<ViewStyle>; accessibilityHint?: string;
}) {
  const { colors } = useTheme();
  return (
    <Pressable
      testID={testID}
      accessibilityLabel={label}
      accessibilityHint={accessibilityHint}
      accessibilityState={selected ? { selected: true } : undefined}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [{
        width: controlSize.lg, height: controlSize.lg, borderRadius: radii.pill, alignItems: 'center', justifyContent: 'center',
        backgroundColor: pressed ? colors.sunken : selected ? colors.accent.wash : 'transparent',
      }, style]}
    >
      <Icon name={icon} color={tone ? toneOf(colors, tone).ink : selected ? colors.accent.ink : colors.ink2} />
    </Pressable>
  );
}
