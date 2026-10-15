// Status, progress and loading (G§5, G§9.13): the ink drop, linear progress,
// skeletons, result/meter bars and the sparkline. Every animation stops under
// the OS reduce-motion setting; the ink drop then becomes a static ● with a
// "working" label.

import React, { useEffect, useRef } from 'react';
import { Animated, View, type DimensionValue, type StyleProp, type ViewStyle } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import { useMotionPreference } from '../hooks/useMotionPreference';
import { easings, radii, spacing, toneOf, type Tone } from '../theme/tokens';
import { Text } from './Text';

/** The universal "working" indicator: one pigment dot that swells and settles (1.6s). */
export function InkDrop({ tone = 'running', size = 10, label, testID }: { tone?: Tone; size?: number; label?: string; testID?: string }) {
  const { colors } = useTheme();
  const { animate } = useMotionPreference();
  const scale = useRef(new Animated.Value(1)).current;
  const t = toneOf(colors, tone);
  useEffect(() => {
    if (!animate) { scale.setValue(1); return undefined; }
    const loop = Animated.loop(Animated.sequence([
      Animated.timing(scale, { toValue: 1.35, duration: 800, easing: easings.bleed, useNativeDriver: true, isInteraction: false }),
      Animated.timing(scale, { toValue: 1, duration: 800, easing: easings.bleed, useNativeDriver: true, isInteraction: false }),
    ]));
    loop.start();
    return () => loop.stop();
  }, [animate, scale]);
  return (
    <View testID={testID} accessible={!!label} accessibilityRole={label ? 'progressbar' : undefined} accessibilityLabel={label}
      accessibilityState={label ? { busy: true } : undefined}
      style={{ flexDirection: 'row', alignItems: 'center', gap: spacing.sm }}>
      {animate ? (
        <Animated.View style={{ width: size, height: size, borderRadius: size / 2, backgroundColor: t.pigment, transform: [{ scale }] }} />
      ) : (
        <Text variant="caption" importantForAccessibility="no" style={{ color: t.ink, lineHeight: size + 4 }}>●</Text>
      )}
      {label ? <Text variant="caption" tone="ink3">{label}</Text> : null}
    </View>
  );
}

/** 4px sunken track with a running fill; `value` null = indeterminate (a soft wash). */
export function ProgressBar({ value, tone = 'running', accessibilityLabel, style }: { value: number | null; tone?: Tone; accessibilityLabel?: string; style?: StyleProp<ViewStyle> }) {
  const { colors } = useTheme();
  const pct = value == null ? null : Math.max(0, Math.min(1, value));
  return (
    <View accessible accessibilityRole="progressbar" accessibilityLabel={accessibilityLabel}
      accessibilityValue={pct == null ? undefined : { min: 0, max: 100, now: Math.round(pct * 100) }}
      style={[{ height: 4, borderRadius: radii.pill, backgroundColor: colors.sunken, overflow: 'hidden' }, style]}>
      <View style={{ height: 4, width: pct == null ? '40%' : `${pct * 100}%`, backgroundColor: pct == null ? toneOf(colors, tone).wash : toneOf(colors, tone).pigment }} />
    </View>
  );
}

export type SkeletonProps = { width?: DimensionValue; height?: number; radius?: number; style?: StyleProp<ViewStyle> };

/** Sunken block with a slow lighter breath (1.8s); static under reduced motion. */
export function Skeleton({ width, height = 20, radius = radii.sm, style }: SkeletonProps) {
  const { colors } = useTheme();
  const { animate } = useMotionPreference();
  const opacity = useRef(new Animated.Value(1)).current;
  useEffect(() => {
    if (!animate) { opacity.setValue(1); return undefined; }
    const loop = Animated.loop(Animated.sequence([
      Animated.timing(opacity, { toValue: 0.55, duration: 900, easing: easings.standard, useNativeDriver: true, isInteraction: false }),
      Animated.timing(opacity, { toValue: 1, duration: 900, easing: easings.standard, useNativeDriver: true, isInteraction: false }),
    ]));
    loop.start();
    return () => loop.stop();
  }, [animate, opacity]);
  return <Animated.View style={[{ width: width ?? '100%', height, borderRadius: radius, backgroundColor: colors.sunken, opacity }, style]} />;
}

/** Stacked pass/fail/skip bar. */
export function ResultBar({ passed, failed, skipped = 0, large = false, testID }: { passed: number; failed: number; skipped?: number; large?: boolean; testID?: string }) {
  const { colors } = useTheme();
  const sum = Math.max(1, passed + failed + skipped);
  const text = `${passed} passed, ${failed} failed${skipped ? `, ${skipped} skipped` : ''}`;
  const h = large ? 10 : 6;
  return (
    <View testID={testID} accessible accessibilityRole="image" accessibilityLabel={text}
      style={{ height: h, borderRadius: radii.pill, overflow: 'hidden', flexDirection: 'row', minWidth: 40, backgroundColor: colors.sunken }}>
      {passed ? <View style={{ flex: passed / sum, backgroundColor: colors.ok.pigment }} /> : null}
      {failed ? <View style={{ flex: failed / sum, backgroundColor: colors.danger.pigment }} /> : null}
      {skipped ? <View style={{ flex: skipped / sum, backgroundColor: colors.neutral.pigment }} /> : null}
    </View>
  );
}

/** Single-value horizontal meter (cost per stage, readiness). */
export function Meter({ value, max = 1, tone = 'accent', accessibilityLabel }: { value: number; max?: number; tone?: Tone; accessibilityLabel?: string }) {
  const { colors } = useTheme();
  const pct = Math.max(0, Math.min(1, max > 0 ? value / max : 0));
  return (
    <View accessible={!!accessibilityLabel} accessibilityLabel={accessibilityLabel}
      style={{ height: 6, borderRadius: radii.pill, overflow: 'hidden', flexDirection: 'row', minWidth: 40, flex: 1, backgroundColor: colors.sunken }}>
      <View style={{ width: `${pct * 100}%`, backgroundColor: toneOf(colors, tone).pigment }} />
    </View>
  );
}

/** Vertical bar sparkline from Views (CPU/mem, duration history). */
export function Sparkline({ values, max, tone = 'running', height = 24, colorsFor, accessibilityLabel, testID }: {
  values: Array<number | null>; max?: number; tone?: Tone; height?: number; colorsFor?: (i: number) => string | undefined; accessibilityLabel: string; testID?: string;
}) {
  const { colors } = useTheme();
  const top = max ?? Math.max(1e-9, ...values.map(v => Number(v || 0)));
  return (
    <View testID={testID} accessible accessibilityRole="image" accessibilityLabel={accessibilityLabel}
      style={{ height, flexDirection: 'row', alignItems: 'flex-end', borderBottomWidth: 1, borderBottomColor: colors.hairline, alignSelf: 'stretch', minWidth: 60 }}>
      {values.map((v, i) => (
        <View key={i} style={{
          flex: 1,
          height: `${Math.max(v == null ? 0 : 4, (Number(v || 0) / top) * 100)}%`,
          backgroundColor: v == null ? 'transparent' : colorsFor?.(i) || toneOf(colors, tone).pigment,
          marginHorizontal: 0.5,
          borderTopLeftRadius: 1,
          borderTopRightRadius: 1,
        }} />
      ))}
    </View>
  );
}
