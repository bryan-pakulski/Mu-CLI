import React from 'react';
import { StyleSheet, View } from 'react-native';
import { useTheme } from '../theme/ThemeContext';

/**
 * Full-app environmental colour wash shared with the web UI.
 *
 * These are intentionally enormous, mostly off-screen fields of colour — not
 * illustrative scenery. There are no mountains, polygons, horizon drawings or
 * other geometric landscape recreations here.
 */
export function AtmosphericBackground({ children }: { children: React.ReactNode }) {
  const { colors, isDark } = useTheme();
  const strength = isDark ? 1 : 1.12;

  return (
    <View style={[styles.root, { backgroundColor: colors.canvas }]}>
      <View pointerEvents="none" style={StyleSheet.absoluteFill}>
        <View style={[styles.sky, { backgroundColor: colors.skyField, opacity: 0.14 * strength }]} />
        <View style={[styles.rose, { backgroundColor: colors.sunriseField, opacity: 0.105 * strength }]} />
        <View style={[styles.peach, { backgroundColor: colors.peachField, opacity: 0.055 * strength }]} />
        <View style={[styles.glacier, { backgroundColor: colors.glacierField, opacity: 0.075 * strength }]} />
        <View style={[styles.alpine, { backgroundColor: colors.alpineField, opacity: 0.065 * strength }]} />
        <View style={[styles.snow, { backgroundColor: colors.snowField, opacity: 0.065 * strength }]} />
      </View>
      <View style={styles.content}>{children}</View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, overflow: 'hidden' },
  content: { flex: 1, backgroundColor: 'transparent' },
  // Atmospheric colour only — deliberately too large / soft-edged to read as shapes.
  sky: {
    position: 'absolute',
    top: -310,
    left: -360,
    width: 980,
    height: 780,
    borderRadius: 490,
  },
  rose: {
    position: 'absolute',
    top: -290,
    right: -330,
    width: 860,
    height: 680,
    borderRadius: 430,
  },
  peach: {
    position: 'absolute',
    top: -60,
    right: -390,
    width: 760,
    height: 620,
    borderRadius: 380,
  },
  glacier: {
    position: 'absolute',
    top: 80,
    left: -420,
    width: 1080,
    height: 980,
    borderRadius: 540,
  },
  alpine: {
    position: 'absolute',
    bottom: -360,
    right: -400,
    width: 980,
    height: 760,
    borderRadius: 490,
  },
  snow: {
    position: 'absolute',
    top: -390,
    left: 10,
    width: 700,
    height: 610,
    borderRadius: 350,
  },
});
