import React from 'react';
import { Image, StyleSheet, View } from 'react-native';
import { useTheme } from '../theme/ThemeContext';

// Tiny local raster generated from the approved alpine palette. It contains
// only smooth atmospheric colour/light falloff — no scenery or geometric
// landscape recreation — and avoids adding a gradient/blur runtime dependency.
const ATMOSPHERE_URI = 'data:image/png;base64,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';

export function AtmosphericBackground({ children }: { children: React.ReactNode }) {
  const { colors, isDark } = useTheme();
  return (
    <View style={[styles.root, { backgroundColor: colors.canvas }]}>
      <View pointerEvents="none" style={styles.atmosphere}>
        <Image
          source={{ uri: ATMOSPHERE_URI }}
          resizeMode="stretch"
          style={[styles.atmosphere, { opacity: isDark ? 0.82 : 0.74 }]}
        />
      </View>
      <View style={styles.content}>{children}</View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, overflow: 'hidden' },
  atmosphere: { ...StyleSheet.absoluteFillObject },
  content: { flex: 1, backgroundColor: 'transparent' },
});
