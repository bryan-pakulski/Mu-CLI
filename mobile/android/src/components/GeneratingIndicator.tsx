import React, { useEffect, useMemo } from 'react';
import { Animated, StyleSheet, View } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import { Text } from './Text';
import { useMotionPreference } from '../hooks/useMotionPreference';
import { easings, motion, radii, spacing } from '../theme/tokens';

export type GeneratingIndicatorProps = {
  label?: string;
};

export function GeneratingIndicator({ label = 'Thinking' }: GeneratingIndicatorProps) {
  const { colors } = useTheme();
  const { animate } = useMotionPreference();
  const dots = useMemo(
    () => [new Animated.Value(0.25), new Animated.Value(0.25), new Animated.Value(0.25)],
    [],
  );

  useEffect(() => {
    if (!animate) {
      dots.forEach(dot => dot.setValue(0.6));
      return;
    }
    const animations = dots.map((dot, index) => Animated.loop(
      Animated.sequence([
        Animated.delay(index * motion.micro),
        Animated.timing(dot, {
          toValue: 1,
          duration: motion.large,
          easing: easings.out,
          useNativeDriver: true,
          isInteraction: false,
        }),
        Animated.timing(dot, {
          toValue: 0.25,
          duration: motion.large,
          easing: easings.in,
          useNativeDriver: true,
          isInteraction: false,
        }),
        Animated.delay((2 - index) * motion.micro),
      ]),
    ));
    animations.forEach(animation => animation.start());
    return () => animations.forEach(animation => animation.stop());
  }, [animate, dots]);

  return (
    <View style={styles.root} accessibilityLiveRegion="polite">
      <View style={styles.dots}>
        {dots.map((opacity, index) => (
          <Animated.View
            key={index}
            style={[styles.dot, { backgroundColor: colors.running.ink, opacity }]}
          />
        ))}
      </View>
      <Text variant="sm" style={{ color: colors.ink3, flexShrink: 1 }}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    minHeight: 40,
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: spacing.sm,
    paddingHorizontal: 2,
  },
  dots: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.xs,
    marginRight: spacing.sm,
  },
  dot: {
    width: 6,
    height: 6,
    borderRadius: radii.pill,
  },
});
