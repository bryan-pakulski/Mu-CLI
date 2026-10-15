// Mobile connect state (G§9.18): the Dawn wash with the wordmark, the
// server URL and one primary Connect action. Text sits below the art band,
// where the wash has dissolved into canvas.

import React from 'react';
import { StyleSheet, View } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import { useConnectionStore } from '../store/connection';
import { radii, spacing } from '../theme/tokens';
import { Button, Chip, Text, WashBackground } from '../ui';

export type ConnectionPromptProps = {
  onConnect: () => void;
};

export function ConnectionPrompt({ onConnect }: ConnectionPromptProps) {
  const { colors } = useTheme();
  const baseUrl = useConnectionStore(state => state.baseUrl);

  return (
    <WashBackground art="dawn" height={300} testID="connection-prompt">
      <View style={styles.root}>
        <Text variant="display" accessibilityRole="header" style={styles.wordmark}>μcli</Text>
        <Text variant="title2" style={styles.title}>Connect to Mucli</Text>
        <Text variant="body" tone="ink2" style={styles.body}>
          Point this app at a running Mucli GUI to chat, run jobs and review approvals from your phone.
        </Text>
        <View style={[styles.urlBox, { backgroundColor: colors.surface, borderColor: colors.hairline }]}>
          <Chip tone="danger" glyph="✗">Offline</Chip>
          <Text variant="mono" style={styles.url} numberOfLines={1}>{baseUrl}</Text>
        </View>
        <Button variant="primary" label="Connect" navigates onPress={onConnect} style={styles.button} />
      </View>
    </WashBackground>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, alignItems: 'center', justifyContent: 'flex-end', paddingHorizontal: spacing.xl, paddingBottom: spacing['3xl'] },
  wordmark: { marginBottom: spacing.lg },
  title: { textAlign: 'center' },
  body: { maxWidth: 360, textAlign: 'center', marginTop: spacing.sm },
  urlBox: { width: '100%', maxWidth: 380, flexDirection: 'row', alignItems: 'center', gap: spacing.sm, borderWidth: 1, borderRadius: radii.pill, paddingHorizontal: spacing.md, minHeight: 44, marginTop: spacing.lg },
  url: { flex: 1 },
  button: { width: '100%', maxWidth: 380, marginTop: spacing.md },
});
