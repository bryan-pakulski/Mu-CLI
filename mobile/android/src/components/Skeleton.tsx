import React from 'react';
import { View, Animated, StyleSheet, ViewStyle } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import { useMotionPreference } from '../hooks/useMotionPreference';

export type SkeletonProps = {
  width?: number;
  height?: number;
  radius?: number;
  style?: ViewStyle;
};

export function Skeleton({ width, height = 20, radius = 6, style }: SkeletonProps) {
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
        Animated.timing(opacity, { toValue: 0.6, duration: 800, useNativeDriver: true, isInteraction: false }),
        Animated.timing(opacity, { toValue: 0.3, duration: 800, useNativeDriver: true, isInteraction: false }),
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
          backgroundColor: colors.bgHover,
          opacity,
        },
        style,
      ]}
    />
  );
}
