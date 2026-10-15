import React from 'react';
import { View, Animated, StyleSheet, ViewStyle } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import { useMotionPreference } from '../hooks/useMotionPreference';
import { easings, radii } from '../theme/tokens';

// Functional loading indicator: a slow opacity breath. 800ms per half-cycle is
// deliberate (a 120-300ms loop would flicker); it stops under reduced motion.
const SKELETON_HALF_CYCLE_MS = 800;

export type SkeletonProps = {
  width?: number;
  height?: number;
  radius?: number;
  style?: ViewStyle;
};

export function Skeleton({ width, height = 20, radius = radii.sm, style }: SkeletonProps) {
  const { colors } = useTheme();
  const { animate } = useMotionPreference();
  const opacity = React.useRef(new Animated.Value(0.3)).current;
  React.useEffect(() => {
    if (!animate) {
      opacity.setValue(0.45);
      return;
    }
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(opacity, { toValue: 0.6, duration: SKELETON_HALF_CYCLE_MS, easing: easings.standard, useNativeDriver: true, isInteraction: false }),
        Animated.timing(opacity, { toValue: 0.3, duration: SKELETON_HALF_CYCLE_MS, easing: easings.standard, useNativeDriver: true, isInteraction: false }),
      ])
    );
    loop.start();
    return () => loop.stop();
  }, [animate, opacity]);
  return (
    <Animated.View
      style={[
        {
          width: width ?? '100%',
          height,
          borderRadius: radius,
          backgroundColor: colors.sunken,
          opacity,
        },
        style,
      ]}
    />
  );
}
