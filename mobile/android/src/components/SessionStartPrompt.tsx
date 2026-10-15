import React from 'react';
import { StyleSheet, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';
import { fontSizes, fontWeights, radii, spacing, tracking, typography } from '../theme/tokens';
import { Pressable, Text } from '../ui';

export type SessionStartPromptProps = {
  onLoadSession: () => void;
  onCreateSession: () => void;
  onManageContainers: () => void;
};

export function SessionStartPrompt({ onLoadSession, onCreateSession, onManageContainers }: SessionStartPromptProps) {
  const { colors } = useTheme();
  const entries = [
    { title: 'Sessions', detail: 'Open or manage saved work', icon: 'folder-open-outline' as const, onPress: onLoadSession },
    { title: 'Create new', detail: 'Guided chat, workspace, or container setup', icon: 'add-circle-outline' as const, onPress: onCreateSession },
    { title: 'Container management', detail: 'Create, edit, clone, and snapshot environments', icon: 'cube-outline' as const, onPress: onManageContainers },
  ];

  return (
    <View style={[styles.root, { backgroundColor: colors.canvas }]}>
      <View style={styles.content}>
        <View style={styles.hero}>
          <View style={[styles.mark, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }]}><Text style={[styles.glyph, { color: colors.accent.ink, fontWeight: fontWeights.bold }]}>μ</Text></View>
          <View style={styles.heroCopy}>
            <Text style={[styles.kicker, { color: colors.ink3 }]}>mucli control centre</Text>
            <Text style={[styles.title, { color: colors.ink }]}>Choose where to begin.</Text>
            <Text variant="sm" dim style={styles.subtitle}>The MuCLI host is connected and ready.</Text>
          </View>
        </View>

        <View style={styles.entries}>
          {entries.map(entry => (
            <Pressable key={entry.title} onPress={entry.onPress} activeOpacity={0.72} style={[styles.entry, { backgroundColor: colors.surface, borderWidth: 1, borderColor: colors.hairline }]}>
              <View style={[styles.entryIcon, { backgroundColor: colors.sunken }]}><Ionicons name={entry.icon} size={20} color={colors.ink2} /></View>
              <View style={styles.entryCopy}><Text variant="base" style={styles.entryTitle}>{entry.title}</Text><Text variant="xs" dim style={styles.entryDetail}>{entry.detail}</Text></View>
              <Ionicons name="arrow-forward" size={19} color={colors.ink3} />
            </Pressable>
          ))}
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, justifyContent: 'center', paddingHorizontal: spacing.lg },
  content: { width: '100%', maxWidth: 500, alignSelf: 'center' },
  hero: { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.md, marginBottom: spacing.lg },
  heroCopy: { flex: 1 }, mark: { width: 48, height: 48, borderRadius: radii.sm, alignItems: 'center', justifyContent: 'center' }, glyph: { fontSize: fontSizes.xl, fontWeight: fontWeights.semibold },
  kicker: { fontSize: fontSizes.xs, fontWeight: fontWeights.semibold, marginBottom: spacing.sm }, title: { fontSize: fontSizes.xl, lineHeight: typography.xl.lineHeight, fontWeight: fontWeights.semibold, letterSpacing: tracking.tight }, subtitle: { lineHeight: typography.sm.lineHeight, marginTop: spacing.xs },
  entries: { gap: spacing.sm }, entry: { minHeight: 80, borderRadius: radii.panel, paddingHorizontal: spacing.base, flexDirection: 'row', alignItems: 'center', gap: spacing.md }, entryIcon: { width: 40, height: 40, borderRadius: radii.sm, alignItems: 'center', justifyContent: 'center' }, entryCopy: { flex: 1 }, entryTitle: { fontWeight: fontWeights.semibold }, entryDetail: { marginTop: spacing.xs, lineHeight: typography.xs.lineHeight },
});
