// The agent's live status update: italic and a step quieter (ink-3), with a band of normal ink
// sweeping through it left to right, so it reads as "what it's doing now" rather than the final
// answer. `inline code` keeps its chip. Reduced motion keeps the quiet italic and drops the sweep.
import React, { useEffect, useRef, useState } from 'react';
import { Animated, Easing, StyleSheet, View } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import { useMotionPreference } from '../hooks/useMotionPreference';
import { fonts, radii, spacing, typeStyle } from '../theme/tokens';
import { Text } from './Text';

const BAND = 72;

/** Splits text into prose and `inline code` runs (backtick pairs on one line). */
export function statusRuns(text: string): Array<{ code: boolean; text: string }> {
  return text.split(/(`[^`\n]+`)/).filter(Boolean).map(part => (
    part.length > 2 && part.startsWith('`') && part.endsWith('`')
      ? { code: true, text: part.slice(1, -1) }
      : { code: false, text: part }
  ));
}

function StatusText({ text, ink }: { text: string; ink: string }) {
  const { colors } = useTheme();
  return (
    <Text variant="bodyLg" style={[styles.italic, { color: ink }]}>
      {statusRuns(text).map((run, i) => (run.code
        ? <Text key={i} style={[styles.code, { color: colors.ink2, backgroundColor: colors.sunken }]}>{run.text}</Text>
        : run.text))}
    </Text>
  );
}

export function StatusSheen({ text, testID }: { text: string; testID?: string }) {
  const { colors } = useTheme();
  const { animate } = useMotionPreference();
  const [width, setWidth] = useState(0);
  const sweep = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (!animate || !width) return undefined;
    const loop = Animated.loop(Animated.timing(sweep, { toValue: 1, duration: 2800, easing: Easing.linear, useNativeDriver: true, isInteraction: false }));
    loop.start();
    return () => { loop.stop(); sweep.setValue(0); };
  }, [animate, sweep, width]);

  // A narrow window slides across; inside it the same text in normal ink is held in place by the
  // opposite offset, so only the band's slice of the text brightens.
  const x = sweep.interpolate({ inputRange: [0, 1], outputRange: [-BAND, width] });
  const back = Animated.multiply(x, -1);
  return (
    <View testID={testID} accessibilityHint="Live status, the agent is still working" onLayout={e => setWidth(e.nativeEvent.layout.width)}>
      <StatusText text={text} ink={colors.ink3} />
      {animate && width ? (
        <Animated.View pointerEvents="none" importantForAccessibility="no-hide-descendants" accessibilityElementsHidden
          style={[styles.band, { transform: [{ translateX: x }] }]}>
          <Animated.View style={{ width, transform: [{ translateX: back }] }}>
            <StatusText text={text} ink={colors.ink} />
          </Animated.View>
        </Animated.View>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  italic: { fontStyle: 'italic' },
  code: { fontFamily: fonts.mono, fontSize: typeStyle('mono-sm').fontSize, fontStyle: 'normal', borderRadius: radii.xs, paddingHorizontal: spacing.xs },
  band: { position: 'absolute', top: 0, bottom: 0, left: 0, width: BAND, overflow: 'hidden' },
});
