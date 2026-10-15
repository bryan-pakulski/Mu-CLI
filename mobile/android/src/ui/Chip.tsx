// Chips, pills and badges (G§9.2). Status chips pair the vocabulary glyph
// with its label on the semantic wash; meta chips are `glass-subtle` pills
// with a light edge (solid surface with Reduce decoration); eyebrow
// chips label sections (sentence case, never all-caps); filter chips invert
// to ink when selected; count badges use tabular numbers.

import React from 'react';
import { View, type StyleProp, type ViewStyle } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import { radii, spacing, toneOf, type Tone } from '../theme/tokens';
import { STATUS, asStatus, type Status } from '../theme/status';
import { Pressable } from './Pressable';
import { Text } from './Text';
import { Icon } from './Icon';
import { useGlass } from './Glass';

export type ChipVariant = 'status' | 'meta' | 'eyebrow' | 'filter';

export type ChipProps = {
  children?: React.ReactNode;
  label?: string;
  variant?: ChipVariant;
  tone?: Tone;
  glyph?: string;
  /** Eyebrow: a 6px pigment square in this tone. */
  dot?: Tone;
  selected?: boolean;
  /** Meta chip that opens a menu: trailing ▾. */
  menu?: boolean;
  onPress?: () => void;
  /** Removable chip (attachments): trailing ✕ with a 44dp target. */
  onRemove?: () => void;
  removeLabel?: string;
  /** Dashed outline (plan mode, G§1.4). */
  dashed?: boolean;
  accessibilityLabel?: string;
  testID?: string;
  style?: StyleProp<ViewStyle>;
};

export function Chip({
  children, label, variant = 'status', tone = 'neutral', glyph, dot, selected, menu, onPress, onRemove, removeLabel,
  dashed, accessibilityLabel, testID, style,
}: ChipProps) {
  const { colors } = useTheme();
  const glass = useGlass();
  const t = toneOf(colors, tone);
  const content = label ?? children;
  const onGlass = glass.solid ? colors.surface : glass.style('subtle').backgroundColor as string;
  const look = (() => {
    switch (variant) {
      case 'meta': return { bg: onGlass, border: colors.hairline, fg: colors.ink2 };
      case 'eyebrow': return glass.solid ? { bg: colors.sunken, border: colors.sunken, fg: colors.ink2 } : { bg: onGlass, border: 'transparent', fg: colors.ink2 };
      case 'filter': return selected
        ? { bg: colors.ink, border: colors.ink, fg: colors.onInk }
        : { bg: onGlass, border: colors.hairline, fg: colors.ink2 };
      default: return { bg: t.wash, border: dashed ? t.pigment : t.wash, fg: t.ink };
    }
  })();
  const body = (
    <>
      {dot ? <View style={{ width: 6, height: 6, borderRadius: 1, backgroundColor: toneOf(colors, dot).pigment }} /> : null}
      {variant === 'filter' && selected ? <Text variant="caption" importantForAccessibility="no" style={{ color: look.fg }}>✓</Text> : null}
      {glyph ? <Text variant="caption" importantForAccessibility="no" style={{ color: look.fg, fontWeight: '600' }}>{glyph}</Text> : null}
      {content == null || content === '' ? null : React.isValidElement(content)
        ? content
        : <Text variant={variant === 'eyebrow' ? 'eyebrow' : 'caption'} numberOfLines={1} style={{ color: look.fg, fontWeight: '500' }}>{content}</Text>}
      {menu ? <Text variant="caption" importantForAccessibility="no" style={{ color: look.fg }}>▾</Text> : null}
      {onRemove ? (
        <Pressable accessibilityLabel={removeLabel || `Remove ${typeof content === 'string' ? content : ''}`.trim()} onPress={onRemove}
          hitSlop={{ top: 12, bottom: 12, left: 8, right: 12 }} style={{ marginLeft: 2 }}>
          <Icon name="x" size="sm" color={look.fg} />
        </Pressable>
      ) : null}
    </>
  );
  const chipStyle: StyleProp<ViewStyle> = [{
    flexDirection: 'row', alignItems: 'center', gap: spacing.xs, alignSelf: 'flex-start',
    minHeight: 24, paddingHorizontal: variant === 'eyebrow' ? spacing.sm : 10, paddingVertical: 2,
    borderRadius: radii.pill, borderWidth: 1, borderStyle: dashed ? 'dashed' : 'solid',
    backgroundColor: look.bg, borderColor: look.border,
  }, style];
  if (onPress) {
    return (
      <Pressable testID={testID} accessibilityLabel={accessibilityLabel} accessibilityState={variant === 'filter' ? { selected: !!selected } : undefined}
        onPress={onPress} style={chipStyle}>
        {body}
      </Pressable>
    );
  }
  return <View testID={testID} accessibilityLabel={accessibilityLabel} style={chipStyle}>{body}</View>;
}

/** Status chip from the shared vocabulary: glyph + label on the semantic wash. */
export function StatusChip({ status, label, testID }: { status: Status | string; label?: string; testID?: string }) {
  const s = STATUS[asStatus(status)];
  const text = label || s.label;
  return <Chip testID={testID} tone={s.tone} glyph={s.icon} accessibilityLabel={text}>{text}</Chip>;
}

/** Glyph + (visible or accessible) label from the shared status vocabulary. */
export function StatusIcon({ status, label, showLabel = false, size = 'sm' }: { status: Status | string; label?: string; showLabel?: boolean; size?: 'xs' | 'sm' | 'base' }) {
  const { colors } = useTheme();
  const s = STATUS[asStatus(status)];
  const text = label || s.label;
  const c = toneOf(colors, s.tone).ink;
  const variant = size === 'xs' ? 'caption' : 'body';
  return (
    <View accessible accessibilityLabel={text} style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.xs }}>
      <Text variant={variant} style={{ color: c, fontWeight: '600' }}>{s.icon}</Text>
      {showLabel ? <Text variant={variant} style={{ color: c }}>{text}</Text> : null}
    </View>
  );
}

/** Eyebrow chip above a section title ("Plan", "Review", "Context"). */
export function Eyebrow({ children, dot = 'accent' }: { children: string; dot?: Tone }) {
  return <Chip variant="eyebrow" dot={dot}>{children}</Chip>;
}

/** 18px-min count pill in tabular mono; attention tone when it needs you. */
export function CountBadge({ tone = '', children, testID }: { tone?: Tone; children: React.ReactNode; testID?: string }) {
  const { colors } = useTheme();
  const on = !!tone && tone !== 'neutral' && tone !== 'muted';
  const t = toneOf(colors, tone);
  return (
    <View testID={testID} style={{ minWidth: 18, minHeight: 18, alignItems: 'center', justifyContent: 'center', borderRadius: radii.pill, paddingHorizontal: 6, backgroundColor: on ? t.wash : colors.sunken }}>
      <Text variant="monoSm" numeric style={{ color: on ? t.ink : colors.ink2 }}>{children}</Text>
    </View>
  );
}

/** Pre-Wash `Pill`: a status chip in a tone. */
export function Pill({ tone = 'neutral', children, glyph, style, accessibilityLabel, testID }: { tone?: Tone; children: React.ReactNode; glyph?: string; style?: StyleProp<ViewStyle>; accessibilityLabel?: string; testID?: string }) {
  return <Chip tone={tone} glyph={glyph} style={style} accessibilityLabel={accessibilityLabel} testID={testID}>{children}</Chip>;
}

/** Pre-Wash `StatusPill`. */
export const StatusPill = StatusChip;
