import React from 'react';
import { View, Animated, StyleSheet, ViewStyle } from 'react-native';
import { useTheme } from '../theme/ThemeContext';

export type SkeletonProps = {
  width?: number;
  height?: number;
  radius?: number;
  style?: ViewStyle;
};

export function Skeleton({ width = 0, height = 20, radius = 6, style }: SkeletonProps) {
  const { colors } = useTheme();
  const opacity = React.useRef(new Animated.Value(0.3)).current;
  React.useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(opacity, { toValue: 0.6, duration: 800, useNativeDriver: true }),
        Animated.timing(opacity, { toValue: 0.3, duration: 800, useNativeDriver: true }),
      ])
    );
    loop.start();
    return () => loop.stop();
  }, [opacity]);
  return (
    <Animated.View
      style={[
        {
          width,
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