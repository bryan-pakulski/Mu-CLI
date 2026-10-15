import React from 'react';
import { StyleSheet, View } from 'react-native';
import { useTheme } from '../theme/ThemeContext';

// Flat theme background. The former atmospheric raster is retired: surfaces
// are flat (no gradient, glow or blur), matching the web design tokens. The
// component name and props are kept so callers do not change.
export function AtmosphericBackground({ children }: { children: React.ReactNode }) {
  const { colors } = useTheme();
  return (
    <View style={[styles.root, { backgroundColor: colors.canvas }]}>
      <View style={styles.content}>{children}</View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, overflow: 'hidden' },
  content: { flex: 1, backgroundColor: 'transparent' },
});
