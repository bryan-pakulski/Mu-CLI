import React from 'react';
import { StyleSheet, TouchableOpacity, View } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { useTheme } from '../theme/ThemeContext';
import { Text } from './Text';

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
    <View style={[styles.root, { backgroundColor: colors.bg }]}>
      <View style={styles.content}>
        <View style={styles.hero}>
          <View style={[styles.mark, { backgroundColor: colors.bgHover }]}><Text style={[styles.glyph, { color: colors.text }]}>μ</Text></View>
          <View style={styles.heroCopy}>
            <Text style={[styles.kicker, { color: colors.textDim }]}>MUCLI CONTROL CENTRE</Text>
            <Text style={[styles.title, { color: colors.text }]}>Choose where to begin.</Text>
            <Text variant="sm" dim style={styles.subtitle}>The MuCLI host is connected and ready.</Text>
          </View>
        </View>

        <View style={styles.entries}>
          {entries.map(entry => (
            <TouchableOpacity key={entry.title} onPress={entry.onPress} activeOpacity={0.72} style={[styles.entry, { backgroundColor: colors.bgLift, borderColor: colors.border }]}>
              <View style={[styles.entryIcon, { backgroundColor: colors.bgHover }]}><Ionicons name={entry.icon} size={21} color={colors.accent} /></View>
              <View style={styles.entryCopy}><Text variant="base" style={styles.entryTitle}>{entry.title}</Text><Text variant="xs" dim style={styles.entryDetail}>{entry.detail}</Text></View>
              <Ionicons name="arrow-forward" size={19} color={colors.textDim} />
            </TouchableOpacity>
          ))}
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, justifyContent: 'center', paddingHorizontal: 20 },
  content: { width: '100%', maxWidth: 500, alignSelf: 'center' },
  hero: { flexDirection: 'row', alignItems: 'flex-start', gap: 14, marginBottom: 28 },
  heroCopy: { flex: 1 }, mark: { width: 50, height: 50, borderRadius: 17, alignItems: 'center', justifyContent: 'center' }, glyph: { fontSize: 25, fontWeight: '700' },
  kicker: { fontSize: 10, fontWeight: '700', letterSpacing: 1.2, marginBottom: 7 }, title: { fontSize: 28, lineHeight: 34, fontWeight: '700', letterSpacing: -0.8 }, subtitle: { lineHeight: 20, marginTop: 6 },
  entries: { gap: 10 }, entry: { minHeight: 88, borderWidth: StyleSheet.hairlineWidth, borderRadius: 18, paddingHorizontal: 15, flexDirection: 'row', alignItems: 'center', gap: 12 }, entryIcon: { width: 42, height: 42, borderRadius: 14, alignItems: 'center', justifyContent: 'center' }, entryCopy: { flex: 1 }, entryTitle: { fontWeight: '700' }, entryDetail: { marginTop: 4, lineHeight: 17 },
});
