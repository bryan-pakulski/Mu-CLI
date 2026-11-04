// A spinning arc around a percentage: the one thing shown while a conversation loads, so the
// transcript appears once, already at its end, instead of popping and shifting into place.
import React, { useEffect, useRef } from 'react';
import { Animated, Easing, View } from 'react-native';
import Svg, { Circle } from 'react-native-svg';
import { useTheme } from '../theme/ThemeContext';
import { useMotionPreference } from '../hooks/useMotionPreference';
import { spacing } from '../theme/tokens';
import { Text } from './Text';

export function LoadingRing({ progress, label, size = 72, testID }: { progress: number; label: string; size?: number; testID?: string }) {
  const { colors } = useTheme();
  const { animate } = useMotionPreference();
  const spin = useRef(new Animated.Value(0)).current;
  const pct = Math.max(0, Math.min(1, Number.isFinite(progress) ? progress : 0));
  const r = (size - 6) / 2;
  const circ = 2 * Math.PI * r;

  useEffect(() => {
    if (!animate) return undefined;
    const loop = Animated.loop(Animated.timing(spin, { toValue: 1, duration: 1100, easing: Easing.linear, useNativeDriver: true, isInteraction: false }));
    loop.start();
    return () => { loop.stop(); spin.setValue(0); };
  }, [animate, spin]);

  const rotate = spin.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '360deg'] });
  return (
    <View testID={testID} accessible accessibilityRole="progressbar" accessibilityLabel={`${label} ${Math.round(pct * 100)}%`}
      accessibilityValue={{ min: 0, max: 100, now: Math.round(pct * 100) }} style={{ alignItems: 'center', gap: spacing.md }}>
      <View style={{ width: size, height: size, alignItems: 'center', justifyContent: 'center' }}>
        <Svg width={size} height={size} style={{ position: 'absolute' }}>
          <Circle cx={size / 2} cy={size / 2} r={r} stroke={colors.sunken} strokeWidth={4} fill="none" />
          <Circle cx={size / 2} cy={size / 2} r={r} stroke={colors.ink3} strokeWidth={4} fill="none" strokeLinecap="round"
            strokeDasharray={`${circ * pct} ${circ}`} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
        </Svg>
        <Animated.View style={{ position: 'absolute', width: size, height: size, transform: [{ rotate }] }}>
          <Svg width={size} height={size}>
            <Circle cx={size / 2} cy={size / 2} r={r} stroke={colors.ink} strokeWidth={4} fill="none" strokeLinecap="round"
              strokeDasharray={`${circ * 0.12} ${circ}`} />
          </Svg>
        </Animated.View>
        <Text variant="label" numeric tone="ink2">{`${Math.round(pct * 100)}%`}</Text>
      </View>
      <Text variant="caption" tone="ink3">{label}</Text>
    </View>
  );
}
