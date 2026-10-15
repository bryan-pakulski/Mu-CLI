// Session start (G§9.18 Welcome/launcher): the Dawn wash dissolving into
// paper, the display headline, and three large choice cards — Sessions,
// Create new, Container management.

import React from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import { useTheme } from '../theme/ThemeContext';
import { radii, spacing } from '../theme/tokens';
import { Card, Eyebrow, Icon, Text, WashBackground, type IconName } from '../ui';

export type SessionStartPromptProps = {
  onLoadSession: () => void;
  onCreateSession: () => void;
  onManageContainers: () => void;
};

export function SessionStartPrompt({ onLoadSession, onCreateSession, onManageContainers }: SessionStartPromptProps) {
  const { colors } = useTheme();
  const entries: Array<{ title: string; detail: string; icon: IconName; pigment: 'lagoon' | 'apricot' | 'meadow'; onPress: () => void }> = [
    { title: 'Sessions', detail: 'Open or manage saved work', icon: 'folder-open', pigment: 'lagoon', onPress: onLoadSession },
    { title: 'Create new', detail: 'Guided chat, workspace, or container setup', icon: 'plus', pigment: 'apricot', onPress: onCreateSession },
    { title: 'Container management', detail: 'Create, edit, clone, and snapshot environments', icon: 'box', pigment: 'meadow', onPress: onManageContainers },
  ];

  return (
    <WashBackground art="dawn" height={320} testID="session-start">
      <ScrollView contentContainerStyle={styles.root}>
        <View style={styles.content}>
          <View style={styles.hero}>
            <Eyebrow dot="ok">Connected</Eyebrow>
            <Text variant="title1" accessibilityRole="header" style={styles.title}>What are we building today?</Text>
            <Text variant="bodyLg" tone="ink2" style={styles.subtitle}>Pick up a session, start something new, or prepare an environment.</Text>
          </View>

          <View style={styles.entries}>
            {entries.map(entry => (
              <Card key={entry.title} onPress={entry.onPress} accessibilityLabel={`${entry.title}. ${entry.detail}`} bodyStyle={styles.entry}>
                <View style={[styles.entryIcon, { backgroundColor: colors.pigment[entry.pigment].wash }]}>
                  <Icon name={entry.icon} color={colors.pigment[entry.pigment].ink} />
                </View>
                <View style={styles.entryCopy}>
                  <Text variant="title3">{entry.title}</Text>
                  <Text variant="caption" tone="ink3">{entry.detail}</Text>
                </View>
                <Icon name="chevron-right" color={colors.ink3} />
              </Card>
            ))}
          </View>
          <Text variant="caption" tone="ink3" style={styles.footer}>Your models, workspace and environments stay under your control.</Text>
        </View>
      </ScrollView>
    </WashBackground>
  );
}

const styles = StyleSheet.create({
  root: { flexGrow: 1, justifyContent: 'flex-end', paddingHorizontal: spacing.base, paddingTop: 200, paddingBottom: spacing.xl },
  content: { width: '100%', maxWidth: 500, alignSelf: 'center' },
  hero: { gap: spacing.sm, marginBottom: spacing.lg },
  title: {},
  subtitle: {},
  entries: { gap: spacing.sm },
  entry: { minHeight: 80, flexDirection: 'row', alignItems: 'center', gap: spacing.md, paddingVertical: spacing.md },
  entryIcon: { width: 40, height: 40, borderRadius: radii.md, alignItems: 'center', justifyContent: 'center' },
  entryCopy: { flex: 1, gap: 2 },
  footer: { textAlign: 'center', marginTop: spacing.lg },
});
