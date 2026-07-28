import React, { useEffect, useMemo } from 'react';
import { Animated, Easing, StyleSheet, View } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import { Text } from './Text';

export type GeneratingIndicatorProps = {
  label?: string;
};

export function GeneratingIndicator({ label = 'Thinking' }: GeneratingIndicatorProps) {
  const { colors } = useTheme();
  const dots = useMemo(
    () => [new Animated.Value(0.25), new Animated.Value(0.25), new Animated.Value(0.25)],
    [],
  );

  useEffect(() => {
    const animations = dots.map((dot, index) => Animated.loop(
      Animated.sequence([
        Animated.delay(index * 140),
        Animated.timing(dot, {
          toValue: 1,
          duration: 300,
          easing: Easing.out(Easing.quad),
          useNativeDriver: true,
        }),
        Animated.timing(dot, {
          toValue: 0.25,
          duration: 420,
          easing: Easing.inOut(Easing.quad),
          useNativeDriver: true,
        }),
        Animated.delay((2 - index) * 140),
      ]),
    ));
    animations.forEach(animation => animation.start());
    return () => animations.forEach(animation => animation.stop());
  }, [dots]);

  return (
    <View style={styles.root} accessibilityLiveRegion="polite">
      <View style={styles.dots}>
        {dots.map((opacity, index) => (
          <Animated.View
            key={index}
            style={[styles.dot, { backgroundColor: colors.textDim, opacity }]}
          />
        ))}
      </View>
      <Text variant="sm" style={{ color: colors.textDim }}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    minHeight: 38,
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 7,
    paddingHorizontal: 2,
  },
  dots: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 4,
    marginRight: 9,
  },
  dot: {
    width: 5,
    height: 5,
    borderRadius: 3,
  },
});
