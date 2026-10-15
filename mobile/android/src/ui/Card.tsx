// Cards (G§9.3): `default` (surface, radius-lg, hairline), `sunken`
// (feature/settings group: sunken fill, no border) and `state` (a 4px left
// rule in the semantic pigment with a wash header strip; attention, danger,
// plan and ok only). Plus the stat strip, key/value rows, pre blocks and the
// small inline empty/loading rows.

import React from 'react';
import { View, type StyleProp, type TextStyle, type ViewStyle } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import { fonts, radii, spacing, toneOf, type Tone } from '../theme/tokens';
import type { Status } from '../theme/status';
import { Pressable } from './Pressable';
import { Text } from './Text';
import { CountBadge, Eyebrow, StatusIcon } from './Chip';
import { InkDrop } from './Progress';

export type CardProps = {
  variant?: 'default' | 'sunken' | 'state';
  /** Semantic tone for `state` cards. */
  tone?: Tone;
  title?: React.ReactNode;
  eyebrow?: string;
  /** Count/label shown as a badge next to the title. */
  badge?: React.ReactNode;
  badgeTone?: Tone;
  /** Glyph before the title (e.g. ◆ in attention). */
  glyph?: string;
  glyphTone?: Tone;
  right?: React.ReactNode;
  footer?: React.ReactNode;
  children?: React.ReactNode;
  /** No body padding (lists that draw their own rows). */
  flush?: boolean;
  /** Raised surface (legacy `elevated`). */
  elevated?: boolean;
  onPress?: () => void;
  style?: StyleProp<ViewStyle>;
  bodyStyle?: StyleProp<ViewStyle>;
  testID?: string;
  accessibilityLabel?: string;
};

export function Card({
  variant = 'default', tone = 'attention', title, eyebrow, badge, badgeTone = '', glyph, glyphTone = '', right, footer,
  children, flush, elevated, onPress, style, bodyStyle, testID, accessibilityLabel,
}: CardProps) {
  const { colors } = useTheme();
  const t = toneOf(colors, tone);
  const isState = variant === 'state';
  const frame: ViewStyle = variant === 'sunken'
    ? { backgroundColor: colors.sunken, borderRadius: radii.lg }
    : {
      backgroundColor: elevated ? colors.raised : colors.surface,
      borderWidth: 1,
      borderColor: colors.hairline,
      borderRadius: radii.lg,
      ...(isState ? { borderLeftWidth: 4, borderLeftColor: t.pigment } : null),
    };
  const head = title != null || right != null || eyebrow ? (
    <View style={[
      { minHeight: 44, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingHorizontal: spacing.base, paddingTop: spacing.md, paddingBottom: children != null ? spacing.sm : spacing.md },
      isState ? { backgroundColor: t.wash, paddingBottom: spacing.md } : null,
    ]}>
      {glyph ? <Text variant="body" importantForAccessibility="no" style={{ color: toneOf(colors, glyphTone || (isState ? tone : 'neutral')).ink, fontWeight: '600' }}>{glyph}</Text> : null}
      <View style={{ flexShrink: 1, gap: spacing.xs }}>
        {eyebrow ? <Eyebrow>{eyebrow}</Eyebrow> : null}
        {typeof title === 'string' ? <Text variant="title3" accessibilityRole="header" numberOfLines={2}>{title}</Text> : title}
      </View>
      {badge != null && badge !== '' ? <CountBadge tone={badgeTone}>{badge}</CountBadge> : null}
      <View style={{ flex: 1 }} />
      {right}
    </View>
  ) : null;
  const inner = (
    <>
      {head}
      {children != null ? <View style={[flush ? null : { padding: spacing.base, paddingTop: head && !isState ? 0 : spacing.base, gap: spacing.sm }, bodyStyle]}>{children}</View> : null}
      {footer ? <View style={{ flexDirection: 'row', justifyContent: 'flex-end', flexWrap: 'wrap', gap: spacing.sm, paddingHorizontal: spacing.base, paddingBottom: spacing.base }}>{footer}</View> : null}
    </>
  );
  if (onPress) {
    return (
      <Pressable testID={testID} accessibilityLabel={accessibilityLabel} onPress={onPress} style={[frame, { overflow: 'hidden' }, style]}>
        {inner}
      </Pressable>
    );
  }
  return <View testID={testID} accessibilityLabel={accessibilityLabel} style={[frame, { overflow: 'hidden' }, style]}>{inner}</View>;
}

/** Pre-Wash name for a titled card. */
export const Panel = Card;

export type StatItem = { value: number | string; label: string; tone?: Tone; glyph?: string; onPress?: () => void; testID?: string };

/** A row of big tabular numbers with captions and hairline dividers (G§9.3). */
export function StatStrip({ items, testID }: { items: StatItem[]; testID?: string }) {
  const { colors } = useTheme();
  return (
    <View testID={testID} style={{ flexDirection: 'row', flexWrap: 'wrap', rowGap: spacing.md }}>
      {items.map((item, i) => {
        const tone = item.tone && item.tone !== 'neutral' && item.tone !== 'muted' && Number(item.value) > 0 ? toneOf(colors, item.tone).ink : colors.plum;
        const body = (
          <>
            <Text variant="title1" numeric style={{ color: tone }}>
              {item.glyph ? <Text variant="title2" importantForAccessibility="no" style={{ color: tone }}>{item.glyph} </Text> : null}
              {item.value}
            </Text>
            <Text variant="caption" tone="ink3" numberOfLines={1}>{item.label}</Text>
          </>
        );
        const cell: ViewStyle = { flexGrow: 1, flexBasis: '30%', minWidth: 88, minHeight: 44, paddingHorizontal: spacing.md, borderLeftWidth: i === 0 ? 0 : 1, borderLeftColor: colors.hairline };
        return item.onPress ? (
          <Pressable key={item.label} testID={item.testID} accessibilityLabel={`${item.value} ${item.label}`} onPress={item.onPress} style={cell}>{body}</Pressable>
        ) : (
          <View key={item.label} testID={item.testID} accessible accessibilityLabel={`${item.value} ${item.label}`} style={cell}>{body}</View>
        );
      })}
    </View>
  );
}

/** Pre-Wash stat tile, now a single-cell stat strip entry. */
export function StatTile({ value, label, tone, glyph, onPress, testID }: { value: number | string; label: string; tone: Tone; glyph: string; onPress?: () => void; testID?: string }) {
  return <StatStrip items={[{ value, label, tone, glyph, onPress, testID }]} />;
}

/** Key/value row used in detail panels. */
export function KV({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <View style={{ flexDirection: 'row', gap: spacing.sm, paddingVertical: 2 }}>
      <Text variant="caption" tone="ink3" style={{ minWidth: 96 }}>{label}</Text>
      <View style={{ flex: 1 }}>{typeof children === 'string' || typeof children === 'number' ? <Text variant="caption">{children}</Text> : children}</View>
    </View>
  );
}

/** Mono pre block (logs, args). */
export function Pre({ children, testID, style, numberOfLines }: { children: React.ReactNode; testID?: string; style?: StyleProp<TextStyle>; numberOfLines?: number }) {
  const { colors } = useTheme();
  return (
    <Text testID={testID} selectable variant="monoSm" numberOfLines={numberOfLines}
      style={[{ fontFamily: fonts.mono, backgroundColor: colors.code, borderRadius: radii.sm, padding: spacing.md, color: colors.ink, overflow: 'hidden' }, style]}>
      {children}
    </Text>
  );
}

/** Inline empty row (inside a card). */
export function Empty({ children, status }: { children: React.ReactNode; status?: Status }) {
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingVertical: spacing.md }}>
      {status ? <StatusIcon status={status} /> : null}
      <Text variant="body" tone="ink3" style={{ flex: 1 }}>{children}</Text>
    </View>
  );
}

/** Inline loading row. */
export function Loading({ label = 'Loading…' }: { label?: string }) {
  return (
    <View accessibilityRole="progressbar" accessibilityLabel={label} style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.sm, paddingVertical: spacing.md }}>
      <InkDrop />
      <Text variant="body" tone="ink3">{label}</Text>
    </View>
  );
}
