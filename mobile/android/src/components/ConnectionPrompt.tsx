import React from 'react';
import { StyleSheet, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';
import { useConnectionStore } from '../store/connection';
import { Button } from './Button';
import { Text } from './Text';
import { fontWeights, fonts, radii, spacing, tracking, typography } from '../theme/tokens';

export type ConnectionPromptProps = {
  onConnect: () => void;
};

export function ConnectionPrompt({ onConnect }: ConnectionPromptProps) {
  const { colors } = useTheme();
  const baseUrl = useConnectionStore(state => state.baseUrl);

  return (
    <View style={[styles.root, { backgroundColor: colors.canvas }]}>
      <View style={[styles.iconWrap, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }]}>
        <Ionicons name="terminal-outline" size={32} color={colors.ink3} />
      </View>
      <Text variant="xl" style={styles.title}>Connect to MuCLI</Text>
      <Text variant="sm" dim style={styles.body}>
        The mobile client needs a reachable MuCLI GUI instance before chat, sessions, modes, or traces can be used.
      </Text>
      <View style={[styles.urlBox, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }]}>
        <View style={[styles.dot, { backgroundColor: colors.danger.ink }]} />
        <Text variant="sm" style={styles.url} numberOfLines={1}>{baseUrl}</Text>
      </View>
      <Button title="Configure connection" onPress={onConnect} style={styles.button} />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: spacing.xl },
  iconWrap: { width: 64, height: 64, borderRadius: radii.sm, alignItems: 'center', justifyContent: 'center', marginBottom: spacing.base },
  title: { fontWeight: fontWeights.semibold, letterSpacing: tracking.tight, textAlign: 'center' },
  body: { maxWidth: 360, textAlign: 'center', marginTop: spacing.sm, lineHeight: typography.sm.lineHeight },
  urlBox: { width: '100%', maxWidth: 380, flexDirection: 'row', alignItems: 'center', borderRadius: radii.sm, paddingHorizontal: spacing.base, paddingVertical: spacing.md, marginTop: spacing.lg },
  dot: { width: 8, height: 8, borderRadius: radii.pill, marginRight: spacing.sm },
  url: { flex: 1, fontFamily: fonts.mono },
  button: { width: '100%', maxWidth: 380, marginTop: spacing.md },
});
