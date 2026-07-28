import React from 'react';
import { StyleSheet, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';
import { useConnectionStore } from '../store/connection';
import { Button } from './Button';
import { Text } from './Text';

export type ConnectionPromptProps = {
  onConnect: () => void;
};

export function ConnectionPrompt({ onConnect }: ConnectionPromptProps) {
  const { colors } = useTheme();
  const baseUrl = useConnectionStore(state => state.baseUrl);

  return (
    <View style={[styles.root, { backgroundColor: colors.bg }]}>
      <View style={[styles.iconWrap, { backgroundColor: colors.accentSoft }]}>
        <Ionicons name="terminal-outline" size={30} color={colors.accent} />
      </View>
      <Text variant="xl" style={styles.title}>Connect to MuCLI</Text>
      <Text variant="sm" dim style={styles.body}>
        The mobile client needs a reachable MuCLI GUI instance before chat, sessions, modes, or traces can be used.
      </Text>
      <View style={[styles.urlBox, { backgroundColor: colors.bgLift, borderColor: colors.border }]}>
        <View style={[styles.dot, { backgroundColor: colors.error }]} />
        <Text variant="sm" style={styles.url} numberOfLines={1}>{baseUrl}</Text>
      </View>
      <Button title="Configure connection" onPress={onConnect} style={styles.button} />
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 28 },
  iconWrap: { width: 64, height: 64, borderRadius: 22, alignItems: 'center', justifyContent: 'center', marginBottom: 20 },
  title: { fontWeight: '700', letterSpacing: -0.5, textAlign: 'center' },
  body: { maxWidth: 360, textAlign: 'center', marginTop: 8, lineHeight: 21 },
  urlBox: { width: '100%', maxWidth: 380, flexDirection: 'row', alignItems: 'center', borderWidth: StyleSheet.hairlineWidth, borderRadius: 16, paddingHorizontal: 14, paddingVertical: 13, marginTop: 24 },
  dot: { width: 8, height: 8, borderRadius: 4, marginRight: 10 },
  url: { flex: 1, fontFamily: 'monospace' },
  button: { width: '100%', maxWidth: 380, marginTop: 12 },
});
