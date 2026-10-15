// More tab (G§8.3): Settings and the secondary destinations that used to
// live only in the chat header menu. Rows are 56dp with a Wash icon.

import React from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { useTheme } from '../theme/ThemeContext';
import { useConnectionStore } from '../store/connection';
import { spacing } from '../theme/tokens';
import { Card, Eyebrow, Icon, Pressable, Text, type IconName } from '../ui';

type Entry = { screen: string; label: string; detail: string; icon: IconName; needsSession?: boolean };

const ENTRIES: Entry[] = [
  { screen: 'Settings', label: 'Settings', detail: 'Theme, safety, provider and session options', icon: 'settings' },
  { screen: 'Connection', label: 'Connection', detail: 'The Mucli server this app talks to', icon: 'network' },
  { screen: 'Providers', label: 'Provider and model', detail: 'Choose the model for this session', icon: 'cpu' },
  { screen: 'Modes', label: 'Mode', detail: 'Choose the active agent strategy', icon: 'sliders-horizontal', needsSession: true },
  { screen: 'Threads', label: 'Agent threads', detail: 'Switch conversations and review coordination', icon: 'git-branch', needsSession: true },
  { screen: 'Artifacts', label: 'Session files', detail: 'Visualizations, model artifacts and uploads', icon: 'paperclip', needsSession: true },
  { screen: 'Traces', label: 'Session trace', detail: 'Turns, tools and context over time', icon: 'activity', needsSession: true },
  { screen: 'Prompts', label: 'Pending prompts', detail: 'Questions and approvals waiting on you', icon: 'help-circle', needsSession: true },
];

export function MoreScreen() {
  const navigation = useNavigation<any>();
  const { colors } = useTheme();
  const activeSessionName = useConnectionStore(s => s.activeSessionName);
  return (
    <ScrollView style={{ flex: 1, backgroundColor: 'transparent' }} contentContainerStyle={styles.content} testID="more-screen">
      <Eyebrow>More</Eyebrow>
      <Text variant="title1" accessibilityRole="header">Everything else</Text>
      <Card variant="sunken" bodyStyle={styles.body}>
        {ENTRIES.map(entry => {
          const disabled = entry.needsSession && !activeSessionName;
          return (
            <Pressable key={entry.screen} testID={`more-${entry.screen}`} accessibilityLabel={entry.label}
              accessibilityHint={disabled ? 'Load a session first' : entry.detail} disabled={disabled} scale={false}
              onPress={() => navigation.navigate(entry.screen)}
              style={({ pressed }) => [styles.row, pressed ? { backgroundColor: colors.hairline } : null]}>
              <Icon name={entry.icon} color={colors.ink3} />
              <View style={{ flex: 1 }}>
                <Text variant="body" style={{ fontWeight: '500' }}>{entry.label}</Text>
                <Text variant="caption" tone="ink3">{disabled ? 'Load a session first' : entry.detail}</Text>
              </View>
              <Icon name="chevron-right" size="sm" color={colors.ink3} />
            </Pressable>
          );
        })}
      </Card>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  content: { padding: spacing.base, paddingBottom: spacing['2xl'], gap: spacing.md },
  body: { paddingVertical: spacing.xs, paddingHorizontal: spacing.md, gap: 0 },
  row: { minHeight: 56, flexDirection: 'row', alignItems: 'center', gap: spacing.md, paddingVertical: spacing.sm, borderRadius: 10 },
});
